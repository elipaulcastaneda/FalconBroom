import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from fbroom.desktop_request_gate import DesktopRequestGate, _WindowsFileGate


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Desktop request gate uses Windows file locks")


def test_cleanup_waits_for_stream_and_new_requests_wait_for_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    stream_started = threading.Event()
    stream_release = threading.Event()
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    later_started = threading.Event()

    async def app(scope, receive, send):
        if scope["path"] == "/stream":
            stream_started.set()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            await asyncio.to_thread(stream_release.wait)
            await send({"type": "http.response.body", "body": b"last"})
        else:
            if scope["path"] == "/account/delete":
                cleanup_started.set()
                await asyncio.to_thread(cleanup_release.wait)
            else:
                later_started.set()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"done"})

    client = TestClient(DesktopRequestGate(app, timeout=5))
    with ThreadPoolExecutor(max_workers=3) as executor:
        try:
            streaming = executor.submit(client.get, "/stream")
            assert stream_started.wait(3)
            cleanup = executor.submit(client.post, "/account/delete")
            assert not cleanup_started.wait(0.3)
            stream_release.set()
            assert streaming.result(timeout=3).content == b"firstlast"
            assert cleanup_started.wait(3)
            later = executor.submit(client.get, "/later")
            assert not later_started.wait(0.3)
            cleanup_release.set()
            assert cleanup.result(timeout=3).status_code == 200
            assert later.result(timeout=3).status_code == 200
            assert later_started.is_set()
        finally:
            stream_release.set()
            cleanup_release.set()


def test_cleanup_lock_waits_for_another_process(tmp_path):
    gate_path = tmp_path / "data" / ".desktop-request-gate"
    script = (
        "import asyncio,sys,time; from pathlib import Path; "
        "from fbroom.desktop_request_gate import _WindowsFileGate; "
        "g=_WindowsFileGate(Path(sys.argv[1])); "
        "asyncio.run(g.lock(0,False,time.monotonic()+5)); "
        "asyncio.run(g.lock(1,False,time.monotonic()+5)); "
        "g.unlock(0); print('ready',flush=True); time.sleep(2); g.close()"
    )
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(gate_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        gate = _WindowsFileGate(gate_path)
        try:
            asyncio.run(gate.lock(0, True, time.monotonic() + 1))
            with pytest.raises(TimeoutError):
                asyncio.run(gate.lock(1, True, time.monotonic() + 0.3))
        finally:
            gate.close()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stdout + stderr
        gate = _WindowsFileGate(gate_path)
        try:
            asyncio.run(gate.lock(0, True, time.monotonic() + 1))
            asyncio.run(gate.lock(1, True, time.monotonic() + 1))
        finally:
            gate.close()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_timed_out_cleanup_does_not_run_and_gate_is_released(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    cleanup_called = False

    async def app(scope, receive, send):
        nonlocal cleanup_called
        cleanup_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})

    active = _WindowsFileGate(Path("data") / ".desktop-request-gate")
    try:
        asyncio.run(active.lock(0, False, time.monotonic() + 1))
        asyncio.run(active.lock(1, False, time.monotonic() + 1))
        active.unlock(0)
        client = TestClient(DesktopRequestGate(app, timeout=0.15))
        response = client.post("/account/delete")
        assert response.status_code == 423
        assert not cleanup_called
    finally:
        active.close()

    assert client.post("/account/delete").status_code == 200
    assert cleanup_called
