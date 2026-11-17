from fastapi.testclient import TestClient
from pathlib import Path
import io
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from fbroom import main as fb
from starlette.websockets import WebSocketDisconnect


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


def test_missing_vault_key_reports_health_and_blocks_other_routes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setattr(fb.app.state, "desktop_vault_missing_key", True)
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}

    assert client.get("/health").status_code == 401
    response = client.get("/health", headers=headers)
    assert response.status_code == 503
    assert response.json() == {"status": "error", "code": "vault_key_missing"}
    assert client.get("/uploads", headers=headers).status_code == 503
    response = client.get("/health", headers={**headers, "Origin": "http://tauri.localhost"})
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    response = client.options(
        "/health",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-FalconBroom-Desktop-Token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    monkeypatch.setattr(fb, "TEAM_FEATURES_ENABLED", True)
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: {"id": "test"})
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/shared?token=test"):
            pytest.fail("WebSocket should be blocked while the vault key is missing")


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


@pytest.mark.parametrize("control_only", [False, True])
def test_real_sidecar_requires_session_secret(tmp_path, control_only):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["FALCONBROOM_DESKTOP_SECRET"] = "b" * 64
    env["FALCONBROOM_CONTROL_ONLY"] = "1" if control_only else "0"
    if control_only:
        original = tmp_path / "data" / "uploads" / "existing.csv"
        original.parent.mkdir(parents=True)
        original.write_text("column\nvalue\n", encoding="utf-8")
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
            if control_only:
                assert original.read_text(encoding="utf-8") == "column\nvalue\n"
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
@pytest.mark.parametrize("unreadable_key", [False, True])
def test_real_sidecar_reports_missing_key_without_modifying_encrypted_data(tmp_path, unreadable_key):
    source = tmp_path / "data" / "uploads" / "preserved.fbenc"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic encrypted data")
    key_path = tmp_path / "data" / ".vault" / "master.dpapi"
    if unreadable_key:
        key_path.parent.mkdir(parents=True)
        key_path.write_bytes(b"unreadable key")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "FALCONBROOM_DESKTOP_SECRET": "b" * 64,
        "FALCONBROOM_CONTROL_ONLY": "0",
        "FALCONBROOM_DESKTOP_ACCOUNT_ISOLATION": "1",
        "FALCONBROOM_REMOTE_POLICY_FILE": str(tmp_path / "data" / "remote-services.json"),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "fbroom.sidecar", "--port", str(port), "--data-dir", str(tmp_path)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Desktop sidecar exited before reporting its missing key")
                try:
                    response = client.get(
                        f"http://127.0.0.1:{port}/health",
                        headers={"X-FalconBroom-Desktop-Token": "b" * 64},
                    )
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    time.sleep(0.2)
            else:
                pytest.fail("Desktop sidecar did not report its missing key")
            assert response.status_code == 503
            assert response.json()["code"] == ("vault_key_unavailable" if unreadable_key else "vault_key_missing")
            assert client.get(f"http://127.0.0.1:{port}/health").status_code == 401
            assert client.get(
                f"http://127.0.0.1:{port}/uploads",
                headers={"X-FalconBroom-Desktop-Token": "b" * 64},
            ).status_code == 503
            assert client.post(
                f"http://127.0.0.1:{port}/desktop/vault/fresh",
                headers={"X-FalconBroom-Desktop-Token": "b" * 64},
                json={"confirmation": "no"},
            ).status_code == 400
            assert not (tmp_path / "desktop-profiles").exists()
            response = client.post(
                f"http://127.0.0.1:{port}/desktop/vault/fresh",
                headers={"X-FalconBroom-Desktop-Token": "b" * 64},
                json={"confirmation": "START FRESH"},
            )
            assert response.status_code == 200
            assert response.json() == {"status": "restart_required"}
            assert client.get(
                f"http://127.0.0.1:{port}/health",
                headers={"X-FalconBroom-Desktop-Token": "b" * 64},
            ).status_code == 409
        assert source.read_bytes() == b"synthetic encrypted data"
        if unreadable_key:
            assert key_path.read_bytes() == b"unreadable key"
        else:
            assert not key_path.exists()
    finally:
        process.terminate()
        process.wait(timeout=10)
    profile = (tmp_path / "data" / ".desktop-active-profile").read_text(encoding="ascii")
    fresh_root = tmp_path / "desktop-profiles" / profile
    process = subprocess.Popen(
        [sys.executable, "-m", "fbroom.sidecar", "--port", str(port), "--data-dir", str(tmp_path)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Fresh desktop sidecar exited before becoming ready")
                try:
                    response = client.get(
                        f"http://127.0.0.1:{port}/health",
                        headers={"X-FalconBroom-Desktop-Token": "b" * 64},
                    )
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    time.sleep(0.2)
            else:
                pytest.fail("Fresh desktop sidecar did not become ready")
            assert response.status_code == 200
        assert (fresh_root / "data" / ".vault" / "master.dpapi").is_file()
        assert source.read_bytes() == b"synthetic encrypted data"
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
def test_real_sidecar_export_restore_and_uninstall_verification(tmp_path):
    import base64
    from fbroom import desktop_vault

    root = tmp_path / "profile"
    local = tmp_path / "local"
    root.mkdir()
    local.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local),
        "FALCONBROOM_DESKTOP_SECRET": "c" * 64,
        "FALCONBROOM_CONTROL_ONLY": "0",
        "FALCONBROOM_DESKTOP_ACCOUNT_ISOLATION": "1",
        "FALCONBROOM_REMOTE_POLICY_FILE": str(root / "data" / "remote-services.json"),
    })
    headers = {"X-FalconBroom-Desktop-Token": "c" * 64}
    base = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-m", "fbroom.sidecar", "--port", str(port), "--data-dir", str(root)]

    def start(expected):
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 40
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail("Desktop sidecar exited before becoming ready")
                    try:
                        response = client.get(base + "/health", headers=headers)
                        assert response.status_code == expected
                        return process
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        time.sleep(0.2)
            pytest.fail("Desktop sidecar did not become ready")
        except BaseException:
            process.terminate()
            process.wait(timeout=10)
            raise

    password = "a long recovery passphrase 123"
    recovery = tmp_path / "recovery.fbrecovery"
    process = start(200)
    try:
        with httpx.Client(trust_env=False) as client:
            result = client.post(base + "/desktop/vault/export", headers=headers, json={
                "destination": str(recovery), "passphrase": password,
            })
            assert result.status_code == 200
            owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            try:
                verifier = [sys.executable, "-m", "fbroom.sidecar", "--verify-uninstall-recovery",
                            "--data-dir", str(root), "--uninstaller-pid", str(owner.pid)]
                assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 0
                assert client.get(base + "/health", headers=headers).status_code == 423
            finally:
                owner.terminate()
                owner.wait(timeout=10)
            assert client.get(base + "/health", headers=headers).status_code == 200
    finally:
        process.terminate()
        process.wait(timeout=10)
    verifier = [sys.executable, "-m", "fbroom.sidecar", "--verify-uninstall-recovery", "--data-dir", str(root)]
    assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 0
    uninstaller = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        guarded = verifier + ["--uninstaller-pid", str(uninstaller.pid)]
        assert subprocess.run(guarded, env=env, capture_output=True, timeout=40).returncode == 0
        assert subprocess.run(command, env=env, capture_output=True, timeout=40).returncode != 0
        assert (root / "data" / ".desktop-uninstall-pending").is_file()
    finally:
        uninstaller.terminate()
        uninstaller.wait(timeout=10)
    assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 0
    assert not (root / "data" / ".desktop-uninstall-pending").exists()
    bundle = recovery.read_bytes()
    recovery.write_bytes(bundle[:-1] + bytes([bundle[-1] ^ 1]))
    assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 1
    recovery.write_bytes(bundle)

    data = root / "data"
    key_path = data / ".vault" / "master.dpapi"
    master_key = desktop_vault._load_master_key(key_path)
    sealed = data / "test.fbenc"
    desktop_vault._write_encrypted(io.BytesIO(b"private synthetic data"), sealed, master_key)
    key_path.unlink()
    assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 1
    process = start(503)
    try:
        with httpx.Client(trust_env=False, timeout=30) as client:
            payload = {"recovery_file": base64.b64encode(bundle).decode("ascii"), "passphrase": "wrong recovery passphrase"}
            assert client.post(base + "/desktop/vault/restore", headers=headers, json=payload).status_code == 400
            assert not key_path.exists()
            payload["passphrase"] = password
            result = client.post(base + "/desktop/vault/restore", headers=headers, json=payload)
            assert result.status_code == 200
            assert result.json() == {"status": "restart_required"}
    finally:
        process.terminate()
        process.wait(timeout=10)
    process = start(200)
    try:
        assert desktop_vault._load_master_key(key_path) == master_key
        assert subprocess.run(verifier, env=env, capture_output=True, timeout=40).returncode == 0
    finally:
        process.terminate()
        process.wait(timeout=10)
