import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fbroom import desktop_remote_services as services
from fbroom.engine import Cleaner, _external_lookup, _fetch_exchange_rate, _geocode_address
from fbroom.ingest import _extract_google_drive_export
from fbroom.main import app, _send_email_message, _sync_urlopen_post

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Desktop preferences use Windows file locks")


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_REMOTE_POLICY_FILE", str(tmp_path / "data" / "remote-services.json"))
    return {"X-FalconBroom-Desktop-Token": "a" * 64}


def test_preferences_are_independent_persisted_and_validated(desktop):
    client = TestClient(app)
    assert client.get("/desktop/remote-services").status_code == 401
    initial = client.get("/desktop/remote-services", headers=desktop)
    assert initial.status_code == 200
    policy = initial.json()
    original = dict(policy)
    assert all(policy.values())
    policy["http_lookup"] = False
    response = client.post("/desktop/remote-services", headers=desktop, json={"expected": original, "policy": policy})
    assert response.status_code == 200, response.text
    assert client.get("/desktop/remote-services", headers=desktop).json() == policy
    invalid = dict(policy, smtp="false")
    assert client.post("/desktop/remote-services", headers=desktop, json={"expected": policy, "policy": invalid}).status_code == 400
    assert client.post("/desktop/remote-services", headers=desktop, json={"expected": original, "policy": original}).status_code == 409
    assert services.read_policy() == policy
    assert client.get("/desktop/remote-lease", headers=desktop).status_code == 403


def test_disabled_http_lookup_is_reported_without_network_or_affecting_local_lookup(desktop, tmp_path, monkeypatch):
    policy = services.read_policy()
    policy["http_lookup"] = False
    services.write_policy(policy)
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    frame = pd.DataFrame({"code": ["A"]})
    with pytest.raises(services.RemoteServiceDisabled, match="http_lookup"):
        _external_lookup(frame, {"mode": "http", "template": "https://example.test/{code}"})
    database = tmp_path / "lookup.sqlite"
    import sqlite3
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE lookup (code TEXT, value TEXT)")
        connection.execute("INSERT INTO lookup VALUES ('A', 'local')")
    result = _external_lookup(frame, {"mode": "sqlite", "db": str(database), "query": "SELECT value FROM lookup WHERE code='{code}'", "map": {"value": "resolved"}})
    assert result["resolved"].to_list() == ["local"]
    source = tmp_path / "source.csv"
    source.write_text("code\nA\n", encoding="utf-8")
    with pytest.raises(services.RemoteServiceDisabled, match="http_lookup"):
        Cleaner().preview_recipe({"sources": [{"path": str(source)}], "cleaning_steps": [{"action": "external_lookup", "params": {"template": "https://example.test/{code}"}}]})
    recipe = {"sources": [{"path": str(source)}], "cleaning_steps": [{"action": "external_lookup", "column": None, "params": {"template": "https://example.test/{code}"}}], "outputs": [{"path": str(tmp_path / "output.csv")}]}
    client = TestClient(app)
    for route in ("/preview", "/apply"):
        response = client.post(route, headers=desktop, json=recipe)
        assert response.status_code >= 400, response.text
        assert "http_lookup" in response.json()["detail"]
    assert not (tmp_path / "output.csv").exists()


def test_saving_waits_for_an_inflight_remote_service(desktop):
    started = threading.Event()
    finish = threading.Event()
    policy = services.read_policy()
    policy["google_drive"] = False

    def operation():
        with services.network_access("google_drive"):
            started.set()
            assert finish.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(operation)
        try:
            assert started.wait(3)
            saving = pool.submit(services.write_policy, policy)
            assert not saving.done()
            finish.set()
            running.result(timeout=3)
            saving.result(timeout=3)
        finally:
            finish.set()
    with pytest.raises(services.RemoteServiceDisabled, match="google_drive"):
        with services.network_access("google_drive"):
            pass


def test_invalid_preferences_do_not_enable_services(desktop):
    path = services.policy_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"http_lookup": false}', encoding="utf-8")
    with pytest.raises(services.RemoteServiceError):
        services.require("http_lookup")


def test_google_drive_restriction_stops_shortcut_export(desktop, tmp_path, monkeypatch):
    policy = services.read_policy()
    policy["google_drive"] = False
    services.write_policy(policy)
    shortcut = tmp_path / "test.gdoc"
    shortcut.write_text('{"resource_id": "abcdefghi"}', encoding="utf-8")
    monkeypatch.setattr("fbroom.ingest.urlopen", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    with pytest.raises(services.RemoteServiceDisabled, match="google_drive"):
        _extract_google_drive_export(shortcut, tmp_path, [], [])


def test_individual_network_services_are_blocked_independently(desktop, monkeypatch):
    policy = services.read_policy()
    policy.update(geocoding=False, exchangerate_host=False, smtp=False, audit_export=False)
    services.write_policy(policy)
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    monkeypatch.setattr("smtplib.SMTP", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    monkeypatch.setenv("SMTP_HOST", "mail.example.test")
    with pytest.raises(services.RemoteServiceDisabled, match="geocoding"):
        _geocode_address("example address")
    with pytest.raises(services.RemoteServiceDisabled, match="exchangerate_host"):
        _fetch_exchange_rate("exchangerate.host", "USD", "EUR")
    with pytest.raises(services.RemoteServiceDisabled, match="smtp"):
        _send_email_message("test@example.test", "subject", "body")
    with pytest.raises(services.RemoteServiceDisabled, match="audit_export"):
        _sync_urlopen_post("https://example.test", b"test")
    assert services.read_policy()["openexchangerates"]


def test_remote_lease_drains_before_preferences_change(desktop, tmp_path):
    import httpx

    sockets = [socket.socket(), socket.socket()]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "fbroom.main:app", "--host", "127.0.0.1", "--port", str(port), "--lifespan", "off"],
            cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for port in ports
    ]
    base, second = [f"http://127.0.0.1:{port}" for port in ports]
    try:
        with httpx.Client(timeout=5) as client:
            for server in (base, second):
                for _ in range(100):
                    try:
                        if client.get(server + "/health", headers=desktop).status_code == 200:
                            break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                else:
                    pytest.fail("Test backend did not start")
            policy = client.get(base + "/desktop/remote-services", headers=desktop).json()
            original = dict(policy)
            policy["remote_backend"] = False
            with client.stream("GET", base + "/desktop/remote-lease", headers=desktop) as lease:
                assert lease.status_code == 200
                next(lease.iter_bytes())
                with ThreadPoolExecutor(max_workers=1) as pool:
                    saving = pool.submit(httpx.post, second + "/desktop/remote-services", headers=desktop, json={"expected": original, "policy": policy}, timeout=8)
                    time.sleep(0.3)
                    assert not saving.done()
                    lease.close()
                    assert saving.result(timeout=5).status_code == 200
            assert client.get(base + "/desktop/remote-lease", headers=desktop).status_code == 403
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
