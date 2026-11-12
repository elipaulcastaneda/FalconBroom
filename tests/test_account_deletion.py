import importlib
import json
from pathlib import Path

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from fbroom import main as fb


@pytest.fixture
def account_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "production")
    importlib.reload(fb)
    return TestClient(fb.app, base_url="https://testserver")


def test_local_signup_login_and_full_account_deletion(account_client):
    signup = account_client.post(
        "/signup",
        json={"username": "beta-user", "email": "beta@example.com", "password": "test-password"},
    )
    assert signup.status_code == 200
    user_id = signup.json()["user_id"]

    login = account_client.post(
        "/login",
        json={"email": "beta@example.com", "password": "test-password"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    uploads = Path("data/uploads")
    upload = uploads / "private.csv"
    upload.write_text("value\n1\n", encoding="utf-8")
    (uploads / "private.csv.meta.json").write_text(
        json.dumps({"owner": {"user_id": user_id}}),
        encoding="utf-8",
    )
    other_upload = uploads / "other.csv"
    other_upload.write_text("value\n2\n", encoding="utf-8")
    (uploads / "other.csv.meta.json").write_text(
        json.dumps({"owner": {"user_id": "another-user"}}),
        encoding="utf-8",
    )
    Path("data/recipes/owned.json").write_text(
        json.dumps({"owner": {"user_id": user_id}}),
        encoding="utf-8",
    )
    other_recipe = Path("data/recipes/other.json")
    other_recipe.write_text(
        json.dumps({"owner": {"user_id": "another-user"}}),
        encoding="utf-8",
    )
    output = Path("data/outputs/result.csv")
    output.write_text("value\n1\n", encoding="utf-8")
    output.with_name(output.name + ".meta.json").write_text(
        json.dumps({"owner": {"user_id": user_id}}), encoding="utf-8"
    )
    Path("data/history/run.json").write_text(
        json.dumps({"owner": {"user_id": user_id}, "output_path": str(output.resolve())}),
        encoding="utf-8",
    )
    Path("data/consents/consent_owned.json").write_text(
        json.dumps({"user_id": user_id, "consents": {"analytics": True}}),
        encoding="utf-8",
    )

    response = account_client.post(
        "/account/delete",
        headers=headers,
        json={"password": "test-password", "confirm_text": "DELETE MY ACCOUNT"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert not (Path("data/users") / f"{user_id}.json").exists()
    assert not list(Path("data/sessions").glob("session_*.json"))
    assert not upload.exists()
    assert not (uploads / "private.csv.meta.json").exists()
    assert other_upload.exists()
    assert (uploads / "other.csv.meta.json").exists()
    assert not Path("data/recipes/owned.json").exists()
    assert other_recipe.exists()
    assert not Path("data/history/run.json").exists()
    assert not output.exists()
    assert not Path("data/consents/consent_owned.json").exists()
    assert not list(Path("data/emails").glob("email_*.json"))
    deletion_job = json.loads(
        (Path("data/account_deletions") / f"{result['job_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert deletion_job["status"] == "completed"
    assert "user_id" not in deletion_job


def test_account_deletion_rejects_bad_password_and_confirmation(account_client):
    account_client.post(
        "/signup",
        json={"username": "beta-user", "email": "beta@example.com", "password": "test-password"},
    )
    login = account_client.post(
        "/login",
        json={"email": "beta@example.com", "password": "test-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    wrong_password = account_client.post(
        "/account/delete",
        headers=headers,
        json={"password": "wrong-password", "confirm_text": "DELETE MY ACCOUNT"},
    )
    assert wrong_password.status_code == 401
    wrong_confirmation = account_client.post(
        "/account/delete",
        headers=headers,
        json={"password": "test-password", "confirm_text": "delete"},
    )
    assert wrong_confirmation.status_code == 400
    assert len(list(Path("data/users").glob("*.json"))) == 1


@pytest.mark.parametrize("record_type", ["consent_job", "dsar"])
def test_local_cleanup_keeps_export_record_when_export_removal_fails(
    account_client, monkeypatch, record_type
):
    user_id = "test-user"
    if record_type == "consent_job":
        export = Path("data/consents/exports/export_job_12345678.json")
        record = Path("data/jobs/job_12345678.json")
        payload = {
            "type": "export_consents",
            "user_id": user_id,
            "result": {"export_path": str(export)},
        }
    else:
        export = Path("data/outputs/dsar_export.json")
        record = Path("data/dsar/dsar_test.json")
        payload = {"user_id": user_id, "export_path": str(export)}
    export.parent.mkdir(parents=True, exist_ok=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    export.write_text("private", encoding="utf-8")
    record.write_text(json.dumps(payload), encoding="utf-8")
    delete = fb._delete_owned_file

    def fail_export(path, root):
        if path == export:
            raise OSError("simulated failure")
        delete(path, root)

    monkeypatch.setattr(fb, "_delete_owned_file", fail_export)
    with pytest.raises(RuntimeError, match="Could not verify or remove"):
        fb._delete_local_user_data(user_id)
    assert export.exists()
    assert record.exists()

    monkeypatch.setattr(fb, "_delete_owned_file", delete)
    fb._delete_local_user_data(user_id)
    assert not export.exists()
    assert not record.exists()


class FakeSupabaseResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def test_supabase_account_deletion_clears_database_storage_and_auth(
    account_client, monkeypatch
):
    user_id = "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    user = {
        "id": user_id,
        "email": "supabase@example.com",
        "auth_provider": "supabase",
        "username": "supabase@example.com",
    }
    Path("data/users").mkdir(parents=True, exist_ok=True)
    (Path("data/users") / f"{user_id}.json").write_text(json.dumps(user), encoding="utf-8")
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: user)

    async def verify_password(email, password, expected_user_id):
        assert email == user["email"]
        assert password == "correct-password"
        assert expected_user_id == user_id

    calls = []

    async def supabase_call(path, method="GET", json_body=None):
        calls.append((path, method, json_body))
        if path == "/rest/v1/rpc/falconbroom_delete_user_data":
            return FakeSupabaseResponse(
                200,
                {"storage_objects": [{"bucket_id": "uploads", "name": "users/private.csv"}]},
            )
        return FakeSupabaseResponse(204)

    monkeypatch.setattr(fb, "_verify_supabase_password", verify_password)
    monkeypatch.setattr(fb, "_call_supabase_service_role", supabase_call)

    response = account_client.post(
        "/account/delete",
        headers={"Authorization": "Bearer supabase-token"},
        json={"password": "correct-password", "confirm_text": "DELETE MY ACCOUNT"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert calls == [
        (
            "/rest/v1/rpc/falconbroom_delete_user_data",
            "POST",
            {"p_user_id": user_id},
        ),
        (
            "/storage/v1/object/uploads",
            "DELETE",
            {"prefixes": ["users/private.csv"]},
        ),
        (f"/auth/v1/admin/users/{user_id}", "DELETE", None),
    ]
    assert not (Path("data/users") / f"{user_id}.json").exists()


def test_supabase_deletion_failure_is_tracked_and_admin_retry_completes(
    account_client, monkeypatch
):
    user_id = "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    user = {
        "id": user_id,
        "email": "supabase@example.com",
        "auth_provider": "supabase",
    }
    Path("data/users").mkdir(parents=True, exist_ok=True)
    (Path("data/users") / f"{user_id}.json").write_text(json.dumps(user), encoding="utf-8")
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: user)
    monkeypatch.setattr(fb, "_verify_supabase_password", lambda *args: _resolved())
    calls = {"count": 0}

    async def supabase_call(path, method="GET", json_body=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeSupabaseResponse(500)
        if path == "/rest/v1/rpc/falconbroom_delete_user_data":
            return FakeSupabaseResponse(200, {"storage_objects": []})
        return FakeSupabaseResponse(204)

    monkeypatch.setattr(fb, "_call_supabase_service_role", supabase_call)
    failed = account_client.post(
        "/account/delete",
        headers={"Authorization": "Bearer supabase-token"},
        json={"password": "correct-password", "confirm_text": "DELETE MY ACCOUNT"},
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "needs_attention"
    assert (Path("data/users") / f"{user_id}.json").is_file()

    monkeypatch.setattr(fb, "_require_admin", lambda request: {"id": "admin"})
    retried = account_client.post(
        f"/admin/account-deletion/{failed.json()['job_id']}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "completed"
    assert not (Path("data/users") / f"{user_id}.json").exists()


async def _resolved():
    return None


def test_supabase_es256_token_uses_documented_jwks_endpoint(account_client, monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-key"
    requested_urls = []
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setattr(
        fb,
        "_fetch_jwks_cached",
        lambda url: requested_urls.append(url) or {"keys": [public_jwk]},
    )
    token = jwt.encode(
        {"sub": "6a0d1258-0950-4bbb-a829-0d5718c47e9e", "aud": "authenticated"},
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    user = fb._get_user_from_token(token)

    assert user is not None
    assert user["id"] == "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    assert requested_urls == ["https://example.supabase.co/auth/v1/.well-known/jwks.json"]


def test_service_role_delete_supports_json_body(account_client, monkeypatch):
    import asyncio

    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, headers, json):
            calls.append((method, url, json))
            return FakeSupabaseResponse(200)

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setattr(fb.httpx, "AsyncClient", FakeAsyncClient)

    response = asyncio.run(
        fb._call_supabase_service_role(
            "/storage/v1/object/test-bucket",
            method="DELETE",
            json_body={"prefixes": ["synthetic.txt"]},
        )
    )

    assert response.status_code == 200
    assert calls == [
        ("DELETE", "https://example.supabase.co/storage/v1/object/test-bucket", {"prefixes": ["synthetic.txt"]})
    ]
