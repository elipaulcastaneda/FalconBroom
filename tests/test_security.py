import os
from pathlib import Path
import importlib

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

fb = importlib.import_module('fbroom.main')

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None


def test_save_and_load_sensitive_json_encrypted(tmp_path, monkeypatch):
    if Fernet is None:
        pytest.skip('cryptography not installed')
    key = Fernet.generate_key()
    monkeypatch.setenv('DATA_ENC_KEY', key.decode())
    p = tmp_path / 'secret.json'
    obj = {'hello': 'world', 'n': 1}
    saved = fb.save_sensitive_json(p, obj)
    assert saved.exists()
    # ensure file has .enc when encryption enabled
    assert str(saved).endswith('.enc')
    loaded = fb.load_sensitive_json(saved)
    assert loaded == obj
def test_save_and_load_sensitive_json_plain(tmp_path, monkeypatch):
    monkeypatch.delenv('DATA_ENC_KEY', raising=False)
    p = tmp_path / 'plain.json'
    obj = {'a': 2}
    saved = fb.save_sensitive_json(p, obj)
    assert saved.exists()
    # when not encrypted, saved path should be the original
    assert str(saved).endswith('plain.json')
    loaded = fb.load_sensitive_json(saved)
    assert loaded == obj


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/uploads", {}),
        ("POST", "/uploads/delete", {"json": {"path": "data/uploads/example.csv"}}),
        ("GET", "/history", {}),
        ("POST", "/history/snapshots/cleanup", {"json": {"days": 30}}),
        ("GET", "/inspections", {}),
        ("GET", "/download", {"params": {"path": "data/outputs/example.csv"}}),
        ("POST", "/join-preview", {"json": {"left_path": "left.csv", "right_path": "right.csv", "left_on": ["key"], "right_on": ["key"]}}),
        ("POST", "/join-export", {"json": {"left_path": "left.csv", "right_path": "right.csv", "left_on": ["key"], "right_on": ["key"]}}),
        ("GET", "/consent", {}),
        ("GET", "/consent/export", {}),
        ("POST", "/consent", {"json": {"consents": {}}}),
    ],
)
def test_sensitive_routes_require_auth_in_production(monkeypatch, method, path, kwargs):
    monkeypatch.setenv("ENV", "production")
    client = TestClient(fb.app, base_url="https://testserver")

    response = client.request(method, path, **kwargs)

    assert response.status_code == 401


def test_sensitive_routes_remain_available_in_local_mode(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    client = TestClient(fb.app)

    response = client.get("/uploads")

    assert response.status_code == 200


def test_sensitive_routes_accept_valid_auth_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: {"id": "test-user"})
    client = TestClient(fb.app, base_url="https://testserver")

    response = client.get("/uploads", headers={"Authorization": "Bearer valid-test-token"})

    assert response.status_code == 200


def test_authenticated_join_preview_works_in_production(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: {"id": "test-user"})
    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True)
    left = uploads / "left.csv"
    right = uploads / "right.csv"
    left.write_text("key,left_value\n1,a\n", encoding="utf-8")
    right.write_text("key,right_value\n1,b\n", encoding="utf-8")
    for path in (left, right):
        path.with_name(path.name + ".meta.json").write_text(
            '{"owner":{"user_id":"test-user"}}', encoding="utf-8"
        )
    client = TestClient(fb.app, base_url="https://testserver")

    response = client.post(
        "/join-preview",
        headers={"Authorization": "Bearer valid-test-token"},
        json={"left_path": str(left), "right_path": str(right), "left_on": ["key"], "right_on": ["key"]},
    )

    assert response.status_code == 200
    assert response.json()["stats"]["joined_count"] == 1


def test_consent_reads_are_scoped_to_authenticated_user(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: {"id": "test-user"})
    consent_dir = Path("data") / "consents"
    consent_dir.mkdir(parents=True)
    (consent_dir / "own.json").write_text('{"id":"own","user_id":"test-user"}', encoding="utf-8")
    (consent_dir / "other.json").write_text('{"id":"other","user_id":"another-user"}', encoding="utf-8")
    client = TestClient(fb.app, base_url="https://testserver")

    response = client.get(
        "/consent?user_id=another-user",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 200
    assert [record["id"] for record in response.json()["consents"]] == ["own"]


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("POST", "/team/invite", {"json": {}}),
        ("POST", "/team/accept", {"json": {}}),
        ("POST", "/team/invite/decline", {"json": {}}),
        ("GET", "/team/invites", {}),
        ("GET", "/team/members", {}),
        ("DELETE", "/team/invites/invite_example.json", {}),
        ("PATCH", "/team/members", {"json": {}}),
        ("POST", "/team/owners", {"json": {}}),
        ("GET", "/uploads/shared", {}),
        ("POST", "/uploads/example.csv/share", {"json": {"shared": True}}),
    ],
)
def test_team_and_sharing_routes_are_disabled(method, path, kwargs):
    client = TestClient(fb.app)

    response = client.request(method, path, **kwargs)

    assert response.status_code == 404


def test_shared_upload_websocket_is_disabled():
    client = TestClient(fb.app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/shared"):
            pass


def test_me_hides_legacy_team_fields(monkeypatch):
    monkeypatch.setattr(
        fb,
        "_require_auth",
        lambda request: {
            "id": "individual-user",
            "email": "person@example.com",
            "team_name": "legacy-team",
            "team_members": ["other@example.com"],
            "team_owner": True,
        },
    )
    client = TestClient(fb.app)

    response = client.get("/me")

    assert response.status_code == 200
    assert response.json() == {"id": "individual-user", "email": "person@example.com"}
