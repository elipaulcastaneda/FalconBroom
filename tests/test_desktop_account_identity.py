import asyncio
import os
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fbroom.desktop_account_identity import verify_account


def _verify(handler, *, project="https://project.supabase.co", authorization="Bearer synthetic-token"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await verify_account(client, project, "synthetic-public-key", authorization)

    return asyncio.run(run())


def test_verifies_account_only_against_configured_project():
    def handler(request):
        assert str(request.url) == "https://project.supabase.co/auth/v1/user"
        assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json={"id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"})

    assert _verify(handler) == {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}


@pytest.mark.parametrize("project", [
    "http://project.supabase.co",
    "https://project.supabase.co.attacker.example",
    "https://project.supabase.co@attacker.example",
    "https://project.supabase.co/other",
])
def test_rejects_untrusted_project_before_sending_token(project):
    def handler(request):
        pytest.fail("A token was sent to an invalid project")

    with pytest.raises(HTTPException) as error:
        _verify(handler, project=project)
    assert error.value.status_code == 503


@pytest.mark.parametrize(("response", "status"), [
    (httpx.Response(401), 401),
    (httpx.Response(302, headers={"Location": "https://attacker.example"}), 503),
    (httpx.Response(200, json={"id": "not-a-uuid"}), 503),
    (httpx.Response(200, content=b"not-json"), 503),
])
def test_failed_verification_never_returns_an_identity(response, status):
    with pytest.raises(HTTPException) as error:
        _verify(lambda request: response)
    assert error.value.status_code == status


def test_network_failure_does_not_allow_offline_access():
    def handler(request):
        raise httpx.ConnectError("unavailable", request=request)

    with pytest.raises(HTTPException) as error:
        _verify(handler)
    assert error.value.status_code == 503


@pytest.mark.parametrize("authorization", ["", "Basic synthetic", "Bearer token with spaces"])
def test_missing_or_malformed_authentication_is_rejected(authorization):
    def handler(request):
        pytest.fail("A malformed authorization header was sent")

    with pytest.raises(HTTPException) as error:
        _verify(handler, authorization=authorization)
    assert error.value.status_code == 401


@pytest.mark.parametrize(("verified_id", "local_id", "status"), [
    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", 200),
    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 401),
])
def test_desktop_me_checks_verified_identity(tmp_path, monkeypatch, verified_id, local_id, status):
    from fbroom import desktop_vault, main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_REQUIRE_VERIFIED_ACCOUNT", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_URL", "https://project.supabase.co")

    async def get(client, url, *, headers, follow_redirects):
        assert url == "https://project.supabase.co/auth/v1/user"
        assert headers["apikey"] == "synthetic-public-key"
        assert headers["Authorization"] == "Bearer synthetic-token"
        assert follow_redirects is False
        return httpx.Response(200, json={"id": verified_id})

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(main, "_require_auth", lambda request: {"id": local_id})
    response = TestClient(main.app).get("/me", headers={
        "X-FalconBroom-Desktop-Token": "a" * 64,
        "X-FalconBroom-Account-Publishable-Key": "synthetic-public-key",
        "Authorization": "Bearer synthetic-token",
    })
    assert response.status_code == status, response.text


def test_desktop_me_fails_closed_when_verification_is_unavailable(tmp_path, monkeypatch):
    from fbroom import desktop_vault, main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_REQUIRE_VERIFIED_ACCOUNT", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_URL", "https://project.supabase.co")

    async def get(client, url, *, headers, follow_redirects):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(main, "_require_auth", lambda request: pytest.fail("Used unverified identity"))
    response = TestClient(main.app).get("/me", headers={
        "X-FalconBroom-Desktop-Token": "a" * 64,
        "X-FalconBroom-Account-Publishable-Key": "synthetic-public-key",
        "Authorization": "Bearer synthetic-token",
    })
    assert response.status_code == 503, response.text


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault requires Windows DPAPI")
def test_account_workflow_storage_separates_accounts_and_preserves_legacy(tmp_path, monkeypatch):
    from fbroom import desktop_vault, main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_REQUIRE_VERIFIED_ACCOUNT", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_URL", "https://project.supabase.co")
    monkeypatch.setenv("FALCONBROOM_REMOTE_POLICY_FILE", str(tmp_path / "data" / "remote-services.json"))
    desktop_vault.initialize(Path("data"))
    legacy = Path("data") / "history" / "legacy.json"
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text('{"legacy":true}', encoding="utf-8")

    ids = {
        "Bearer account-a": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "Bearer account-b": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    }

    async def get(client, url, *, headers, follow_redirects):
        identifier = ids.get(headers["Authorization"])
        if identifier is None:
            return httpx.Response(401)
        return httpx.Response(200, json={"id": identifier})

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    client = TestClient(main.app)
    def headers(account):
        return {
            "X-FalconBroom-Desktop-Token": "a" * 64,
            "X-FalconBroom-Account-Publishable-Key": "synthetic-public-key",
            "X-FalconBroom-Expected-Account": ids.get(f"Bearer {account}", ""),
            "Authorization": f"Bearer {account}",
        }

    payload = {"data": [{"state": {"instruction": "private checkpoint"}}], "revision": None}
    response = client.put("/desktop/account-workflow/checkpoints", headers=headers("account-a"), json=payload)
    assert response.status_code == 200, response.text
    own = client.get("/desktop/account-workflow/checkpoints", headers=headers("account-a"))
    other = client.get("/desktop/account-workflow/checkpoints", headers=headers("account-b"))
    assert own.json()["data"] == payload["data"]
    assert own.json()["revision"] == response.json()["revision"]
    assert other.json() == {"data": [], "revision": None}
    conflict = client.put("/desktop/account-workflow/checkpoints", headers=headers("account-a"), json=payload)
    assert conflict.status_code == 409
    assert client.get("/desktop/account-workflow/checkpoints", headers=headers("account-a")).json() == own.json()
    updated = {"data": [{"state": {"instruction": "updated"}}], "revision": own.json()["revision"]}
    assert client.put("/desktop/account-workflow/checkpoints", headers=headers("account-a"), json=updated).status_code == 200
    assert client.get("/desktop/account-workflow/checkpoints", headers=headers("account-a")).json()["data"] == updated["data"]
    encrypted = list((Path("data") / "account_workflows").rglob("checkpoints.json.fbenc"))
    assert len(encrypted) == 1
    assert b"private checkpoint" not in encrypted[0].read_bytes()
    assert not encrypted[0].with_name("checkpoints.json").exists()
    assert legacy.read_text(encoding="utf-8") == '{"legacy":true}'
    assert client.get("/desktop/account-workflow/checkpoints", headers=headers("unknown")).status_code == 401
    switched = headers("account-b")
    switched["X-FalconBroom-Expected-Account"] = ids["Bearer account-a"]
    assert client.put("/desktop/account-workflow/checkpoints", headers=switched, json=payload).status_code == 409
    missing = headers("account-a")
    del missing["X-FalconBroom-Expected-Account"]
    assert client.get("/desktop/account-workflow/checkpoints", headers=missing).status_code == 409
    assert client.get("/desktop/account-workflow/checkpoints", headers=headers("account-b")).json() == {"data": [], "revision": None}
    assert client.put("/desktop/account-workflow/checkpoints", headers=headers("account-b"), json={"data": {}, "revision": None}).status_code == 400
    logical = encrypted[0].with_name("checkpoints.json")
    desktop_vault.write_encrypted_bytes(logical, b"not-json")
    assert client.get("/desktop/account-workflow/checkpoints", headers=headers("account-a")).status_code == 500
    assert client.put("/desktop/account-workflow/checkpoints", headers=headers("account-a"), json=payload).status_code == 500
    assert desktop_vault.read_bytes(logical) == b"not-json"
