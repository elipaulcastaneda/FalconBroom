import importlib
import json
import os
from pathlib import Path

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from fbroom import main as fb
from fbroom import desktop_vault


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


def test_desktop_deletion_stops_when_workspace_ownership_is_unknown(account_client):
    upload = Path("data/uploads/unattributed.csv")
    upload.write_text("value\n1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ownership"):
        fb._check_desktop_deletion_ownership()
    metadata = upload.with_name(upload.name + ".meta.json")
    metadata.write_text(json.dumps({"owner": {"user_id": "another-user"}}), encoding="utf-8")
    fb._check_desktop_deletion_ownership()
    assert upload.exists()
    metadata.write_text("invalid json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ownership"):
        fb._check_desktop_deletion_ownership()


def test_desktop_deletion_pauses_for_cross_account_join_history(account_client):
    source = Path("data/uploads/source.csv")
    source.write_text("id\n1\n", encoding="utf-8")
    source.with_name(source.name + ".meta.json").write_text(
        json.dumps({"owner": {"user_id": "alice"}}), encoding="utf-8",
    )
    history = Path("data/history/shared-join.json")
    history.write_text(json.dumps({
        "type": "join", "owner": {"user_id": "bob"},
        "left_path": str(source), "right_path": str(source),
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Join source ownership"):
        fb._check_desktop_deletion_ownership()
    assert source.exists() and history.exists()


@pytest.mark.parametrize("directory", ["uploads", "recipes", "inspections", "history"])
def test_desktop_deletion_rejects_unhandled_nested_files(account_client, directory):
    nested = Path("data") / directory / "nested" / "record.json"
    nested.parent.mkdir(parents=True)
    nested.write_text(json.dumps({"owner": {"user_id": "test-user"}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="ownership"):
        fb._check_desktop_deletion_ownership()


def test_desktop_deletion_requires_review_for_unreadable_sessions(account_client):
    sessions = Path("data/sessions")
    sessions.mkdir(exist_ok=True)
    (sessions / "session_broken.json").write_text("invalid", encoding="utf-8")
    with pytest.raises(RuntimeError, match="review"):
        fb._check_desktop_deletion_auxiliary_ownership("alice")


@pytest.mark.parametrize(("target", "requester"), [("alice", "bob"), ("bob", "alice")])
def test_desktop_deletion_preserves_cross_account_consent_jobs(account_client, target, requester):
    job = Path("data/jobs/job_shared.json")
    job.write_text(json.dumps({
        "type": "export_consents", "user_id": target, "requester_user_id": requester,
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Shared"):
        fb._check_desktop_deletion_auxiliary_ownership("alice")
    assert job.exists()
    fb._check_desktop_deletion_auxiliary_ownership("other")


def test_desktop_deletion_rejects_corrupt_optouts(account_client):
    path = Path("data/privacy/opt_outs.json")
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(RuntimeError, match="review"):
        fb._check_desktop_deletion_auxiliary_ownership("alice")
    assert path.read_text(encoding="utf-8") == "invalid"


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pandas", "sql"])
def test_desktop_join_output_ownership_requires_matching_sources(tmp_path, monkeypatch, shared, fmt):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_TEST_ACCOUNT_DELETION", "1")
    importlib.reload(fb)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(Path("data"))
    user_id = "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    other_id = "cc856b01-88d7-497d-b36b-04446b70c3c6"
    monkeypatch.setattr(fb, "_require_auth", lambda request: {"id": request.headers["x-test-user"]})

    async def verify_user(path, public_key, token=None, body=None):
        assert path == "/auth/v1/user"
        return {"id": token}

    monkeypatch.setattr(fb, "_test_deletion_http", verify_user)
    client = TestClient(fb.app)

    def upload(user, name):
        response = client.post(
            "/upload",
            headers={"X-FalconBroom-Desktop-Token": "a" * 64, "X-Test-User": user,
                     "Authorization": f"Bearer {user}", "X-FalconBroom-Test-Publishable-Key": "sb_publishable_fake"},
            files={"file": (name, b"id,value\n1,example\n", "text/csv")},
        )
        assert response.status_code == 200, response.text
        return response.json()["path"]

    left = upload(user_id, "left.csv")
    right = upload(other_id if shared else user_id, "right.csv")
    result = client.post(
        "/join-export",
        headers={"X-FalconBroom-Desktop-Token": "a" * 64, "X-Test-User": user_id},
        json={"left_path": left, "right_path": right, "left_on": ["id"], "right_on": ["id"],
              "export_format": fmt},
    )
    assert result.status_code == 200, result.text
    output = Path(result.json()["export_path"])
    metadata = output.with_name(output.name + ".meta.json")
    if shared:
        assert not desktop_vault.exists(metadata)
        with pytest.raises(RuntimeError, match="ownership"):
            fb._check_desktop_deletion_ownership()
        conflict = client.post(
            "/join-export",
            headers={"X-FalconBroom-Desktop-Token": "a" * 64, "X-Test-User": user_id},
            json={"left_path": left, "right_path": right, "left_on": ["id"],
                  "right_on": ["id"], "filename": output.stem, "export_format": fmt},
        )
        assert conflict.status_code == 409
        assert desktop_vault.exists(output)
    else:
        assert json.loads(desktop_vault.read_text(metadata))["owner"]["user_id"] == user_id
        other_output = Path("data/outputs") / f"other{output.suffix}"
        desktop_vault.write_encrypted_bytes(other_output, b"id,value\n1,other\n")
        desktop_vault.write_encrypted_bytes(
            other_output.with_name(other_output.name + ".meta.json"),
            json.dumps({"owner": {"user_id": other_id}}).encode(),
        )
        conflict = client.post(
            "/join-export",
            headers={"X-FalconBroom-Desktop-Token": "a" * 64, "X-Test-User": user_id},
            json={"left_path": left, "right_path": right, "left_on": ["id"],
                  "right_on": ["id"], "filename": "other", "export_format": fmt},
        )
        assert conflict.status_code == 409
        assert desktop_vault.read_bytes(other_output) == b"id,value\n1,other\n"
        fb._check_desktop_deletion_ownership()
        fb._delete_local_user_data(user_id)
        assert not desktop_vault.exists(output)
        assert desktop_vault.exists(other_output)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
def test_desktop_inspection_ownership_requires_verified_account_and_owned_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_TEST_ACCOUNT_DELETION", "1")
    importlib.reload(fb)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(Path("data"))
    user_id = "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    other_id = "cc856b01-88d7-497d-b36b-04446b70c3c6"
    source = Path("data/uploads/source.csv")
    desktop_vault.write_encrypted_bytes(source, b"id,value\n1,example\n")
    desktop_vault.write_encrypted_bytes(
        source.with_name(source.name + ".meta.json"),
        json.dumps({"owner": {"user_id": user_id}}).encode(),
    )
    existing = Path("data/inspections/legacy.json")
    fb.write_inspection(existing, {"inspection": {"rows": []}})

    async def verify_user(path, public_key, token=None, body=None):
        assert path == "/auth/v1/user"
        if token == "invalid":
            raise fb.HTTPException(status_code=401, detail="Invalid token")
        return {"id": token}

    monkeypatch.setattr(fb, "_test_deletion_http", verify_user)
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64,
               "X-FalconBroom-Test-Publishable-Key": "sb_publishable_fake"}

    def run(token):
        return client.post("/inspect", headers={**headers, "Authorization": f"Bearer {token}"},
                           json={"path": str(source), "offset": 0, "limit": 1})

    owned = run(user_id)
    assert owned.status_code == 200, owned.text
    owned_path = Path(owned.json()["dump_path"])
    assert fb.read_inspection(owned_path)["owner"]["user_id"] == user_id
    foreign = run(other_id)
    assert foreign.status_code == 200, foreign.text
    assert "owner" not in fb.read_inspection(Path(foreign.json()["dump_path"]))
    invalid = run("invalid")
    assert invalid.status_code == 401
    missing_key = client.post(
        "/inspect",
        headers={"X-FalconBroom-Desktop-Token": "a" * 64, "Authorization": f"Bearer {user_id}"},
        json={"path": str(source), "offset": 0, "limit": 1},
    )
    assert missing_key.status_code == 400
    anonymous = client.post("/inspect", headers=headers, json={"path": str(source), "offset": 0, "limit": 1})
    assert anonymous.status_code == 200
    assert "owner" not in fb.read_inspection(Path(anonymous.json()["dump_path"]))
    assert "owner" not in fb.read_inspection(existing)
    with pytest.raises(RuntimeError, match="ownership"):
        fb._check_desktop_deletion_ownership()
    fb._delete_local_user_data(user_id)
    assert not owned_path.exists()
    assert existing.exists()
    assert Path(foreign.json()["dump_path"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
def test_desktop_test_deletion_pauses_shared_data_and_recovers_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_TEST_ACCOUNT_DELETION", "1")
    importlib.reload(fb)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(Path("data"))
    user_id = "6a0d1258-0950-4bbb-a829-0d5718c47e9e"
    job_id = "3b87c304-bef2-4c79-8ebd-bf26426467aa"
    target = Path("data/uploads/target.csv")
    other = Path("data/uploads/other.csv")
    for path, owner in ((target, user_id), (other, "other-user")):
        desktop_vault.write_encrypted_bytes(path, b"value\n1\n")
        desktop_vault.write_encrypted_bytes(
            path.with_name(path.name + ".meta.json"),
            json.dumps({"owner": {"user_id": owner}}).encode(),
        )

    state = {"shared": True, "cloud": "awaiting_local"}
    calls = []

    async def cloud(path, public_key, token=None, body=None):
        calls.append((path, body))
        if path == "/auth/v1/user":
            if token == "other-session":
                return {"id": "cc856b01-88d7-497d-b36b-04446b70c3c6"}
            return {"id": user_id, "email": "test@example.invalid"}
        if body["action"] == "start":
            if state["shared"]:
                return {"status": "shared_data_review_required"}
            return {"status": "awaiting_local", "job_id": job_id, "user_id": user_id}
        if body["action"] == "local_complete":
            state["cloud"] = "ready"
            return {"status": "accepted", "job_id": job_id}
        return {"status": state["cloud"], "job_id": job_id}

    monkeypatch.setattr(fb, "_test_deletion_http", cloud)
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64, "Authorization": "Bearer session"}
    body = {"password": "synthetic", "confirm_text": "DELETE MY ACCOUNT", "test_anon_key": "sb_publishable_test", "test_user_id": user_id}
    disabled = client.post("/account/delete", headers=headers, json=body)
    assert disabled.status_code == 503
    assert not calls
    monkeypatch.setattr(fb, "_TEST_DELETION_READY", True)
    mismatch = client.post("/account/delete", headers=headers, json={**body, "test_user_id": "cc856b01-88d7-497d-b36b-04446b70c3c6"})
    assert mismatch.status_code == 409
    assert not any(payload and payload.get("action") == "start" for _, payload in calls)
    result = client.post("/account/delete", headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "shared_data_review_required"
    assert desktop_vault.exists(target) and desktop_vault.exists(other)
    assert not Path("data/.test-deletion-pending").exists()

    state["shared"] = False
    cleanup = fb._delete_local_user_data

    def fail_after_cleanup(*args):
        cleanup(*args)
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(fb, "_delete_local_user_data", fail_after_cleanup)
    result = client.post("/account/delete", headers=headers, json=body)
    assert result.status_code == 503
    assert Path("data/.test-deletion-pending").exists()
    assert desktop_vault.exists(other)
    assert not any(payload and payload.get("action") == "local_complete" for _, payload in calls)
    recovery_headers = {**headers, "X-FalconBroom-Test-Publishable-Key": "sb_publishable_test"}
    assert client.get("/me", headers=recovery_headers).json()["id"] == user_id
    assert client.get("/me", headers=headers).status_code == 400
    assert client.get("/me", headers={**recovery_headers, "Authorization": "Bearer other-session"}).status_code == 409
    preflight = client.options("/me", headers={
        "Origin": "http://tauri.localhost",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-falconbroom-desktop-token,x-falconbroom-test-publishable-key",
    })
    assert preflight.status_code == 200
    assert client.post("/me", headers=recovery_headers).status_code == 423

    monkeypatch.setattr(fb, "_delete_local_user_data", cleanup)
    marker = Path("data/.test-deletion-pending")
    marker.write_text("another-account", encoding="utf-8")
    previous_calls = len(calls)
    result = client.post("/account/delete", headers=headers, json=body)
    assert result.status_code == 409
    assert not any(payload and payload.get("action") == "start" for _, payload in calls[previous_calls:])
    assert client.post("/account/delete", headers=headers, json={
        "password": "synthetic", "confirm_text": "DELETE MY ACCOUNT",
    }).status_code == 423
    marker.write_text(user_id, encoding="utf-8")
    result = client.post("/account/delete", headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["local_status"] == "completed" and result.json()["status"] == "ready"
    assert not desktop_vault.exists(target)
    assert desktop_vault.exists(other)
    assert Path("data/.test-deletion-pending").exists()
    assert client.post("/upload", headers=headers, files={"file": ("x.csv", b"a\n1\n")}).status_code == 423
    assert client.get("/me", headers=recovery_headers).status_code == 200
    pending = client.post("/desktop/test-deletions", headers=headers, json={"test_anon_key": "sb_publishable_test"})
    assert pending.status_code == 200 and pending.json()["local_pending"] is True

    marker.unlink()
    previous_calls = len(calls)
    result = client.post("/account/delete", headers=headers, json=body)
    assert result.status_code == 409
    assert not any(payload and payload.get("action") == "local_complete" for _, payload in calls[previous_calls:])
    marker.write_text(user_id, encoding="utf-8")
    state["cloud"] = "completed"
    result = client.post("/desktop/test-deletions", headers=headers, json={"test_anon_key": "sb_publishable_test"})
    assert result.status_code == 200, result.text
    assert result.json()["deletions"] == [{"job_id": job_id, "user_id": user_id, "status": "completed", "local_status": "completed"}]
    assert result.json()["local_pending"] is False
    assert not Path("data/.test-deletion-pending").exists()
    saved = desktop_vault.encrypted_path(fb._test_deletion_record_path(user_id))
    assert saved.is_file()
    assert b"recovery_token" not in saved.read_bytes()
    start_tokens = [payload["recovery_token"] for _, payload in calls if payload and payload.get("action") == "start"]
    assert len(set(start_tokens)) == 1


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
