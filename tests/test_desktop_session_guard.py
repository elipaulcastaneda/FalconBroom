from fastapi.testclient import TestClient
from pathlib import Path
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from fbroom import main as fb


def test_desktop_session_guard_requires_secret(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    client = TestClient(fb.app)

    assert client.get("/health").status_code == 401
    assert client.get("/uploads", headers={"X-FalconBroom-Desktop-Token": "b" * 64}).status_code == 401
    assert client.post("/inspect", json={"path": "private.csv"}).status_code == 401
    assert client.get("/health", headers={"X-FalconBroom-Desktop-Token": "a" * 64}).status_code == 200
    assert client.post("/apply", json={}).status_code == 401


def test_desktop_session_secret_not_required_for_non_sidecar_tests(monkeypatch):
    monkeypatch.delenv("FALCONBROOM_DESKTOP_SECRET", raising=False)
    assert TestClient(fb.app).get("/health").status_code == 200


def test_confirmed_workspace_cleanup_only_deletes_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    for folder in ("uploads", "outputs", "recipes", "history/snapshots", "inspections", "consents"):
        directory = Path("data") / folder
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "record.txt").write_text("private", encoding="utf-8")
    external = tmp_path / "outside.txt"
    external.write_text("external", encoding="utf-8")
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}

    assert client.post("/desktop/clear-workspace", json={"confirm_text": "no"}, headers=headers).status_code == 400
    assert Path("data/uploads/record.txt").exists()
    result = client.post("/desktop/clear-workspace", json={"confirm_text": "DELETE LOCAL WORKSPACE"}, headers=headers)
    assert result.status_code == 200
    assert result.json()["deleted_files"] == 5
    assert not Path("data/history/snapshots/record.txt").exists()
    assert Path("data/consents/record.txt").exists()
    assert external.exists()


def test_workspace_cleanup_rejects_symlink_before_deletion(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data/uploads")
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    response = TestClient(fb.app).post(
        "/desktop/clear-workspace",
        headers={"X-FalconBroom-Desktop-Token": "a" * 64},
        json={"confirm_text": "DELETE LOCAL WORKSPACE"},
    )
    assert response.status_code == 409
    assert (root / "keep.txt").exists()
    assert outside.exists()


def test_desktop_supabase_deletion_fails_before_deleting_local_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    user = {"id": "6a0d1258-0950-4bbb-a829-0d5718c47e9e", "auth_provider": "supabase"}
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: user)
    result = TestClient(fb.app).post(
        "/account/delete",
        headers={"X-FalconBroom-Desktop-Token": "a" * 64, "Authorization": "Bearer test"},
        json={"password": "test", "confirm_text": "DELETE MY ACCOUNT"},
    )
    assert result.status_code == 503
    assert not Path("data/account_deletions").exists()


def test_real_sidecar_requires_session_secret(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["FALCONBROOM_DESKTOP_SECRET"] = "b" * 64
    process = subprocess.Popen(
        [sys.executable, "-m", "fbroom.sidecar", "--port", str(port), "--data-dir", str(tmp_path)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Desktop sidecar exited before becoming ready")
                try:
                    response = client.get(url, headers={"X-FalconBroom-Desktop-Token": "b" * 64})
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    time.sleep(0.2)
            else:
                pytest.fail("Desktop sidecar did not become ready")
            assert response.status_code == 200
            assert client.get(url).status_code == 401
    finally:
        process.terminate()
        process.wait(timeout=10)
