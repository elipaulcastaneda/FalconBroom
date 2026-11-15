import csv
import io
import json
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import pyzipper

from fastapi.testclient import TestClient

from fbroom import desktop_vault, main as fb


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_managed_csv_memory_limit_and_failure_cleanup(tmp_path, monkeypatch):
    import polars as pl
    from fbroom import engine

    root = tmp_path / "data"
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    destination = outputs / "data.csv"
    staged_paths = []
    write_targets = []
    stage = desktop_vault.stage_managed_output
    write_csv = engine._write_csv

    def track_stage(path):
        result = stage(path)
        staged_paths.append(result)
        return result

    def track_write(df, target):
        write_targets.append(target)
        return write_csv(df, target)

    monkeypatch.setattr(desktop_vault, "stage_managed_output", track_stage)
    monkeypatch.setattr(engine, "_write_csv", track_write)
    with desktop_vault.request_workspace():
        assert engine._write_managed_csv(
            pl.DataFrame({"value": ["private-small"], "price": ["12.50"], "date": ["2026-01-02"]}),
            destination,
        )
        assert isinstance(write_targets[-1], io.BytesIO)
        assert not staged_paths[-1].exists()
        assert b"private-small" in desktop_vault.read_bytes(destination)
        assert not destination.exists()
        with monkeypatch.context() as patch:
            patch.setattr(desktop_vault, "materialize", lambda path: pytest.fail("small table was materialized"))
            read_back = engine._read_table(str(destination))
        assert read_back["price"].to_list() == ["12.50"]
        assert read_back["date"].to_list() == ["2026-01-02"]

        assert engine._write_managed_csv(pl.DataFrame({"value": ["x" * (9 * 1024 * 1024)]}), destination)
        assert Path(write_targets[-1]) == staged_paths[-1]
        assert not staged_paths[-1].exists()
        assert len(desktop_vault.read_bytes(destination)) > 8 * 1024 * 1024
        assert not destination.exists()

    previous = desktop_vault.encrypted_path(destination).read_bytes()
    def failing_write(df, target):
        if isinstance(target, engine._BoundedCsvBuffer):
            target.exceeded = True
            raise OSError("buffer limit")
        Path(target).write_bytes(b"unfinished private data")
        raise OSError("write failed")

    monkeypatch.setattr(engine, "_write_csv", failing_write)
    with pytest.raises(OSError, match="write failed"):
        with desktop_vault.request_workspace() as workspace:
            engine._write_managed_csv(pl.DataFrame({"value": ["private"]}), destination)
    assert not workspace.exists() and not staged_paths[-1].exists()
    assert desktop_vault.encrypted_path(destination).read_bytes() == previous
    assert not destination.exists()

    from openpyxl import Workbook
    from openpyxl.worksheet._writer import WorksheetWriter
    write_sheet = WorksheetWriter.write

    def failing_sheet(writer):
        write_sheet(writer)
        raise OSError("worksheet write failed")

    monkeypatch.setattr(WorksheetWriter, "write", failing_sheet)
    with desktop_vault.request_workspace() as workspace:
        workbook = Workbook()
        workbook.active.append(["private"])
        staged = desktop_vault.stage_managed_output(outputs / "failed.xlsx")
        with pytest.raises(OSError, match="worksheet write failed"):
            fb._save_export_workbook(workbook, staged)
        assert not list(workspace.rglob("*.xml"))
    assert not workspace.exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_streaming_upload_consumes_raw_body_and_rejects_nonlocal(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    received = bytearray()

    async def consume(request, file):
        while chunk := await file.read(32768):
            assert len(chunk) <= 32768
            received.extend(chunk)
        return {"name": file.filename, "size": len(received)}

    monkeypatch.setattr(fb, "_process_upload", consume)
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64, "X-FalconBroom-Filename": "caf%C3%A9.csv"}
    content = b"private" * 300000
    response = client.post("/desktop/upload-stream", headers=headers, content=content)
    assert response.status_code == 200 and response.json() == {"name": "caf\u00e9.csv", "size": len(content)}
    assert received == content
    assert client.post("/desktop/upload-stream", content=b"x").status_code == 401
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "0")
    assert client.post("/desktop/upload-stream", headers=headers, content=b"x").status_code == 404


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_desktop_streaming_download_validates_and_cleans_up(tmp_path, monkeypatch):
    root = tmp_path / "data"
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    monkeypatch.setattr(fb, "OUTPUTS_DIR", outputs)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    path = outputs / "sample.csv"
    contents = b"secret-value\n" * 100000
    desktop_vault.write_encrypted_bytes(path, contents)
    client = TestClient(fb.app)

    def reject_materialize(*args):
        raise AssertionError("ordinary download materialized plaintext")

    with monkeypatch.context() as patch:
        patch.setattr(desktop_vault, "materialize_to", reject_materialize)
        response = client.get("/download", headers=headers, params={"path": str(path)})
    assert response.status_code == 200 and response.content == contents
    assert int(response.headers["content-length"]) == len(contents)
    assert response.headers["cache-control"] == "no-store"
    assert not list((root.parent / "temporary").glob("fb-v2-*"))

    import asyncio
    from starlette.requests import Request

    async def cancel_download():
        request = Request({
            "type": "http", "method": "GET", "path": "/download",
            "headers": [], "scheme": "http", "server": ("127.0.0.1", 80),
        })
        streamed = fb.download(str(path), request)
        first = await streamed.body_iterator.__anext__()
        assert first and first == contents[:len(first)]
        await streamed.body_iterator.aclose()

    asyncio.run(cancel_download())
    assert not list((root.parent / "temporary").glob("fb-v2-*"))

    async def disconnect_download():
        from starlette.requests import ClientDisconnect

        scope = {
            "type": "http", "method": "GET", "path": "/download",
            "headers": [], "scheme": "http", "server": ("127.0.0.1", 80),
            "asgi": {"spec_version": "2.4"},
        }
        streamed = fb.download(str(path), Request(scope))

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        with pytest.raises(ClientDisconnect):
            await streamed(scope, receive, send)

    asyncio.run(disconnect_download())
    assert not list((root.parent / "temporary").glob("fb-v2-*"))

    response = client.get("/download", headers={**headers, "Range": "bytes=0-5"}, params={"path": str(path)})
    assert response.status_code == 206 and response.content == contents[:6]
    assert not list((root.parent / "temporary").glob("fb-v2-*"))

    sealed = desktop_vault.encrypted_path(path)
    sealed.write_bytes(sealed.read_bytes()[:-1])
    response = client.get("/download", headers=headers, params={"path": str(path)})
    assert response.status_code == 500
    assert b"secret-value" not in response.content
    assert not list((root.parent / "temporary").glob("fb-v2-*"))


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_scratch_workspace_has_protected_windows_acl(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    folder, lock = desktop_vault.create_scratch()
    try:
        child = folder / "test.txt"
        child.write_text("test", encoding="utf-8")
        command = (
            "$a=Get-Acl -LiteralPath $env:FB_TEST_FOLDER; "
            "$b=Get-Acl -LiteralPath $env:FB_TEST_FILE; "
            "Write-Output $a.AreAccessRulesProtected; Write-Output $a.Sddl; Write-Output $b.Sddl"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            env={**os.environ, "FB_TEST_FOLDER": str(folder), "FB_TEST_FILE": str(child)},
            capture_output=True, text=True, check=True,
        )
        lines = result.stdout.splitlines()
        assert lines[0].strip() == "True"
        assert ";;;OW)" in lines[1] and ";;;SY)" in lines[1]
        assert "S-1-3-4" in lines[2] or ";;;OW)" in lines[2]
    finally:
        desktop_vault.release_scratch(folder, lock)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_scratch_cleanup_failure_is_reported_and_retryable(tmp_path, monkeypatch, caplog):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    folder, lock = desktop_vault.create_scratch()
    (folder / "test.txt").write_text("private", encoding="utf-8")
    with monkeypatch.context() as patch:
        def fail_cleanup(*args):
            raise OSError("cleanup failed")
        patch.setattr(desktop_vault, "_remove_scratch", fail_cleanup)
        with pytest.raises(OSError, match="cleanup failed"):
            desktop_vault.release_scratch(folder, lock)
    assert lock.closed and folder.exists()
    assert "Could not remove a desktop temporary workspace" in caplog.text
    assert desktop_vault.cleanup_abandoned_scratch() == 1
    assert not folder.exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_encrypted_download_streams_managed_output_and_preview(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data"
    outputs = root / "outputs"
    uploads = root / "uploads"
    outputs.mkdir(parents=True)
    uploads.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    monkeypatch.setattr(fb, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(fb, "UPLOAD_DIR", uploads)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    password = "a-random-long-test-password"
    contents = b"private-data\n" * 100000
    source = outputs / "private-name.csv"
    source.write_bytes(contents)
    desktop_vault.seal_file(source)

    response = client.post(
        "/desktop/encrypted-download", headers=headers, json={"path": str(source), "password": password}
    )
    assert response.status_code == 200, response.text
    assert b"private-data" not in response.content
    assert b"private-name" not in response.content
    with pyzipper.AESZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["export.csv"]
        with pytest.raises(RuntimeError):
            archive.read("export.csv", pwd=b"wrong-password")
        assert archive.read("export.csv", pwd=password.encode()) == contents
        info = archive.getinfo("export.csv")
    damaged = bytearray(response.content)
    name_length, extra_length = struct.unpack_from("<HH", damaged, info.header_offset + 26)
    encrypted_data = info.header_offset + 30 + name_length + extra_length + 18
    damaged[encrypted_data] ^= 1
    with pyzipper.AESZipFile(io.BytesIO(damaged)) as archive:
        with pytest.raises(pyzipper.BadZipFile, match="HMAC"):
            archive.read("export.csv", pwd=password.encode())
    assert not source.exists()
    assert client.get("/download", headers=headers, params={"path": str(source)}).content == contents

    preview = client.post(
        "/desktop/encrypted-download", headers=headers,
        json={"preview_csv": "name\nprivate\n", "password": password},
    )
    assert preview.status_code == 200
    with pyzipper.AESZipFile(io.BytesIO(preview.content)) as archive:
        assert archive.read("export.csv", pwd=password.encode()) == b"name\nprivate\n"
    assert client.post("/desktop/encrypted-download", headers=headers, json={"path": str(source), "password": "short"}).status_code == 400
    assert client.post("/desktop/encrypted-download", headers=headers, json={"path": str(tmp_path / "outside.csv"), "password": password}).status_code == 403
    assert client.post("/desktop/encrypted-download", json={"path": str(source), "password": password}).status_code == 401
    corrupt = outputs / "corrupt.csv"
    corrupt.write_bytes(b"private-data")
    sealed = desktop_vault.seal_file(corrupt)
    sealed.write_bytes(sealed.read_bytes()[:-1])
    scratch_folders = []
    create_scratch = desktop_vault.create_scratch

    def track_scratch():
        folder, lock = create_scratch()
        scratch_folders.append(folder)
        return folder, lock

    monkeypatch.setattr(desktop_vault, "create_scratch", track_scratch)
    failing_client = TestClient(fb.app, raise_server_exceptions=False)
    assert failing_client.post("/desktop/encrypted-download", headers=headers, json={"path": str(corrupt), "password": password}).status_code == 500
    assert scratch_folders and all(not folder.exists() for folder in scratch_folders)
    with desktop_vault.request_workspace():
        fb._write_file_owner(source, {"id": "owner"})
    monkeypatch.setenv("ENV", "production")
    client = TestClient(fb.app, base_url="https://testserver")
    monkeypatch.setattr(fb, "_require_auth", lambda request: {"id": "other-user"})
    assert client.post("/desktop/encrypted-download", headers=headers, json={"path": str(source), "password": password}).status_code == 404
    monkeypatch.setattr(fb, "_require_auth", lambda request: {"id": "owner"})
    assert client.post("/desktop/encrypted-download", headers=headers, json={"path": str(source), "password": password}).status_code == 200
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "0")
    assert client.post("/desktop/encrypted-download", headers=headers, json={"path": str(source), "password": password}).status_code == 404


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_vault_rejects_complete_chunk_truncation_and_trailing_data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    source = root / "large.csv"
    source.write_bytes(b"x" * (desktop_vault._CHUNK_SIZE + 17))
    encrypted = desktop_vault.seal_file(source)
    encoded = encrypted.read_bytes()
    first_length = struct.unpack(">I", encoded[desktop_vault._HEADER_SIZE:desktop_vault._HEADER_SIZE + 4])[0]
    after_first_chunk = desktop_vault._HEADER_SIZE + 4 + 12 + first_length
    with desktop_vault.request_workspace():
        for damaged in (encoded[:desktop_vault._HEADER_SIZE], encoded[:after_first_chunk], encoded + b"x"):
            encrypted.write_bytes(damaged)
            with pytest.raises(desktop_vault.DesktopVaultError):
                desktop_vault.materialize(source)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_desktop_vault_migrates_and_reads_authenticated_files(tmp_path, monkeypatch):
    root = tmp_path / "data"
    uploads = root / "uploads"
    outputs = root / "outputs"
    uploads.mkdir(parents=True)
    outputs.mkdir(parents=True)
    original = uploads / "sample.csv"
    original.write_bytes(b"id,value\n1,private-row\n")
    previous_output = outputs / "joined.csv"
    previous_output.write_bytes(b"id,result\n1,private-output\n")

    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)

    assert desktop_vault.migrate_plaintext_files((uploads, outputs)) == 2
    encrypted = desktop_vault.encrypted_path(original)
    encrypted_output = desktop_vault.encrypted_path(previous_output)
    assert encrypted.is_file()
    assert encrypted_output.is_file()
    assert not original.exists()
    assert not previous_output.exists()
    assert b"private-row" not in encrypted.read_bytes()
    assert b"private-output" not in encrypted_output.read_bytes()

    with desktop_vault.request_workspace():
        materialized = desktop_vault.materialize(original)
        assert materialized.read_bytes() == b"id,value\n1,private-row\n"

        encrypted.write_bytes(encrypted.read_bytes()[:-1] + bytes([encrypted.read_bytes()[-1] ^ 1]))
        with pytest.raises(desktop_vault.DesktopVaultError):
            desktop_vault.materialize(original)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_missing_key_does_not_replace_encrypted_data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    (root / "existing.fbenc").write_bytes(b"encrypted")
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setattr(
        desktop_vault,
        "_dpapi",
        lambda data, decrypt: (_ for _ in ()).throw(AssertionError("must not replace key")),
    )

    with pytest.raises(desktop_vault.DesktopVaultError, match="key is missing"):
        desktop_vault.initialize(root)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_corrupt_key_is_not_replaced(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    key_path = root / ".vault" / "master.dpapi"
    key_path.write_bytes(b"corrupt")
    with pytest.raises(desktop_vault.DesktopVaultError, match="Could not unlock"):
        desktop_vault.initialize(root)
    assert key_path.read_bytes() == b"corrupt"


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
@pytest.mark.parametrize("data", [b"", b"x" * (1024 * 1024 + 17)], ids=["empty", "multichunk"])
def test_vault_round_trips_empty_and_multichunk_files(tmp_path, monkeypatch, data):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    source = root / "data.csv"
    source.write_bytes(data)
    desktop_vault.seal_file(source)
    with desktop_vault.request_workspace():
        assert desktop_vault.materialize(source).read_bytes() == data


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_migration_resumes_only_when_plaintext_and_ciphertext_match(tmp_path, monkeypatch):
    root = tmp_path / "data"
    uploads = root / "uploads"
    uploads.mkdir(parents=True)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    source = uploads / "sample.csv"
    source.write_bytes(b"first")
    encrypted = desktop_vault.seal_file(source, remove_source=False)

    source.write_bytes(b"different")
    with pytest.raises(desktop_vault.DesktopVaultError, match="Conflicting"):
        desktop_vault.migrate_plaintext_files((uploads,))
    assert source.read_bytes() == b"different"
    assert encrypted.exists()

    source.write_bytes(b"first")
    assert desktop_vault.migrate_plaintext_files((uploads,)) == 1
    assert not source.exists()
    with desktop_vault.request_workspace():
        assert desktop_vault.materialize(source).read_bytes() == b"first"


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_plaintext_workspace_is_removed_on_error(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    with pytest.raises(RuntimeError, match="processing failed"):
        with desktop_vault.request_workspace() as workspace:
            (workspace / "sensitive.csv").write_bytes(b"private")
            raise RuntimeError("processing failed")
    assert not workspace.exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_scratch_cleanup_preserves_active_workspaces_and_removes_abandoned(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    active, active_lock = desktop_vault.create_scratch()
    abandoned, abandoned_lock = desktop_vault.create_scratch()
    (active / "active.csv").write_bytes(b"active")
    (abandoned / "private.csv").write_bytes(b"private")
    abandoned_lock.close()
    try:
        assert desktop_vault.cleanup_abandoned_scratch() == 1
        assert not abandoned.exists()
        assert (active / "active.csv").read_bytes() == b"active"
    finally:
        desktop_vault.release_scratch(active, active_lock)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_scratch_cleanup_after_process_exit_does_not_remove_other_process_workspace(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    active, active_lock = desktop_vault.create_scratch()
    try:
        script = (
            "import os,sys; from pathlib import Path; from fbroom import desktop_vault as v; "
            "v.initialize(sys.argv[1]); p,lock=v.create_scratch(); "
            "(p/'secret.txt').write_text('private'); print(p,flush=True); os._exit(0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(root)], capture_output=True, text=True, check=True
        )
        abandoned = Path(result.stdout.strip())
        assert abandoned.is_dir()
        assert desktop_vault.cleanup_abandoned_scratch() == 1
        assert not abandoned.exists()
        assert active.is_dir()
    finally:
        desktop_vault.release_scratch(active, active_lock)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_legacy_output_migration_preserves_conflicting_files(tmp_path, monkeypatch):
    root = tmp_path / "data"
    output = root / "outputs"
    output.mkdir(parents=True)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    legacy = tmp_path / "output_from_text.csv"
    target = output / "output_from_text_legacy.csv"
    legacy.write_bytes(b"secret")
    desktop_vault.migrate_plaintext_file(legacy, target)
    assert not legacy.exists()
    assert desktop_vault.encrypted_path(target).is_file()
    legacy.write_bytes(b"different")
    with pytest.raises(desktop_vault.DesktopVaultError, match="Conflicting"):
        desktop_vault.migrate_plaintext_file(legacy, target)
    assert legacy.read_bytes() == b"different"


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
def test_desktop_upload_provenance_uses_relative_logical_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    upload_dir = Path("data") / "uploads"
    upload_dir.mkdir(parents=True)
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(Path("data"))
    monkeypatch.setattr(fb, "UPLOAD_DIR", upload_dir)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    client = TestClient(fb.app)
    response = client.post(
        "/upload", headers=headers, files={"file": ("sample.csv", b"id,name\n1,Alice\n", "text/csv")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    download = client.get("/download", headers=headers, params={"path": body["path"]})
    assert download.status_code == 200, download.text
    rows = list(csv.DictReader(io.StringIO(download.text)))
    assert rows and all(row["source_path"] == body["source_path"] for row in rows)


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault key protection uses Windows DPAPI")
@pytest.mark.parametrize("block_remote", [False, True])
def test_desktop_upload_source_inspection_and_join_remain_usable(tmp_path, monkeypatch, block_remote):
    import openpyxl.worksheet._writer as worksheet_writer

    def reject_untracked_excel_temp(*args, **kwargs):
        raise AssertionError("Excel output tried to use the system temporary directory")

    monkeypatch.setattr(worksheet_writer, "create_temporary_file", reject_untracked_excel_temp)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data"
    upload_dir = root / "uploads"
    output_dir = root / "outputs"
    inspection_dir = root / "inspections"
    history_dir = root / "history"
    recipe_dir = root / "recipes"
    for directory in (upload_dir, output_dir, inspection_dir, history_dir, recipe_dir):
        directory.mkdir(parents=True)

    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    desktop_vault.initialize(root)
    monkeypatch.setattr(fb, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(fb, "OUTPUTS_DIR", output_dir)
    monkeypatch.setattr(fb, "INSPECTIONS_DIR", inspection_dir)
    monkeypatch.setattr(fb, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(fb, "RECIPES_DIR", recipe_dir)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    if block_remote:
        from fbroom import desktop_remote_services
        monkeypatch.setenv("FALCONBROOM_REMOTE_POLICY_FILE", str(root / "remote-services.json"))
        desktop_remote_services.write_policy({name: False for name in desktop_remote_services.SERVICES})
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    client = TestClient(fb.app)

    left = client.post(
        "/upload",
        headers=headers,
        files={"file": ("left.csv", b"id,name\n1,private-left\n", "text/csv")},
    )
    right = client.post(
        "/desktop/upload-stream",
        headers={**headers, "X-FalconBroom-Filename": "right.csv", "Content-Type": "application/octet-stream"},
        content=b"id,amount\n1,42\n",
    )
    assert left.status_code == 200, left.text
    assert right.status_code == 200, right.text
    assert right.json()["name"] == "right.csv"
    assert client.post("/desktop/upload-stream", headers=headers, content=b"data").status_code == 400
    assert client.post("/desktop/upload-stream", headers={**headers, "X-FalconBroom-Filename": "..%2fother.csv"}, content=b"data").status_code == 400
    left_path = Path(left.json()["path"])
    right_path = Path(right.json()["path"])
    assert not left_path.exists()
    assert desktop_vault.encrypted_path(left_path).is_file()
    assert b"private-left" not in desktop_vault.encrypted_path(left_path).read_bytes()
    normalized_download = client.get("/download", headers=headers, params={"path": str(left_path)})
    assert normalized_download.status_code == 200, normalized_download.text
    normalized_rows = list(csv.DictReader(io.StringIO(normalized_download.text)))
    assert normalized_rows
    assert all(row["source_path"] == str(upload_dir / Path(left.json()["source_path"]).name) for row in normalized_rows)

    profile = client.post("/profile", headers=headers, json={"path": str(left_path)})
    inspection = client.post("/inspect", headers=headers, json={"path": str(left_path)})
    assert profile.status_code == 200, profile.text
    assert inspection.status_code == 200, inspection.text
    metadata = client.post("/metadata", headers=headers, json={"path": str(left_path)})
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["metadata"]["row_count"] == left.json()["row_count"]

    legacy_recipe = recipe_dir / "legacy.json"
    legacy_history = history_dir / "run_legacy.json"
    legacy_recipe.write_text(json.dumps({"id": "legacy", "name": "sensitive-name", "recipe": {}}), encoding="utf-8")
    legacy_history.write_text(json.dumps({"id": "run_legacy", "status": "sensitive-status"}), encoding="utf-8")
    assert desktop_vault.migrate_plaintext_files((recipe_dir, history_dir)) == 2
    assert not legacy_recipe.exists() and not legacy_history.exists()
    assert b"sensitive-name" not in desktop_vault.encrypted_path(legacy_recipe).read_bytes()
    assert b"sensitive-status" not in desktop_vault.encrypted_path(legacy_history).read_bytes()
    assert client.get("/recipes/legacy", headers=headers).status_code == 200
    assert any(item["id"] == "run_legacy" for item in client.get("/history", headers=headers).json()["history"])

    generated = client.post(
        "/recipe-from-text",
        headers=headers,
        json={"instruction": "clean name", "source_path": str(left_path), "output_path": "output_from_text.csv"},
    )
    assert generated.status_code == 200, generated.text
    generated_recipe = generated.json()["recipe"]
    assert Path(generated_recipe["outputs"][0]["path"]) == output_dir / "output_from_text.csv"
    generated_apply = client.post("/apply", headers=headers, json=generated_recipe)
    assert generated_apply.status_code == 200, generated_apply.text
    assert desktop_vault.encrypted_path(output_dir / "output_from_text.csv").is_file()
    assert not (tmp_path / "output_from_text.csv").exists()

    output_path = output_dir / "cleaned.csv"
    recipe = {
        "sources": [{"path": str(left_path)}],
        "cleaning_steps": [],
        "outputs": [{"path": str(output_path)}],
    }
    preview = client.post("/preview", headers=headers, json=recipe)
    applied = client.post("/apply", headers=headers, json=recipe)
    assert preview.status_code == 200, preview.text
    assert applied.status_code == 200, applied.text
    assert desktop_vault.encrypted_path(output_path).is_file()
    assert not output_path.exists()
    saved = client.post("/recipes", headers=headers, json={"name": "private-recipe", "recipe": recipe})
    assert saved.status_code == 200, saved.text
    recipe_id = saved.json()["id"]
    recipe_file = recipe_dir / f"{recipe_id}.json"
    assert not recipe_file.exists()
    assert desktop_vault.encrypted_path(recipe_file).is_file()
    assert b"private-recipe" not in desktop_vault.encrypted_path(recipe_file).read_bytes()
    assert client.get(f"/recipes/{recipe_id}", headers=headers).status_code == 200
    assert client.post(f"/recipes/{recipe_id}/approve", headers=headers).status_code == 200
    run = client.post(f"/recipes/{recipe_id}/run", headers=headers)
    assert run.status_code == 200, run.text
    encrypted_source = client.post(
        "/desktop/encrypted-download", headers=headers,
        json={"path": run.json()["run"]["output_path"], "password": "a-random-long-test-password"},
    )
    assert encrypted_source.status_code == 200, encrypted_source.text
    with pyzipper.AESZipFile(io.BytesIO(encrypted_source.content)) as archive:
        assert b"private-left" in archive.read("export.csv", pwd=b"a-random-long-test-password")
    for export_format, extension, signature in (
        ("xlsx", ".xlsx", b"PK"),
        ("pkl", ".pkl", b"\x80"),
        ("sqlite", ".sqlite", b"SQLite format 3"),
    ):
        converted = client.post(
            f"/recipes/{recipe_id}/run", headers=headers, params={"export_format": export_format}
        )
        assert converted.status_code == 200, converted.text
        path = Path(converted.json()["run"]["output_path"])
        assert path.suffix == extension
        assert not path.exists() and desktop_vault.encrypted_path(path).is_file()
        download = client.get("/download", headers=headers, params={"path": str(path)})
        assert download.status_code == 200 and download.content.startswith(signature)
        if export_format == "xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(download.content), read_only=True)
            assert any("private-left" in str(row) for row in workbook.active.values)
            workbook.close()
    for patches, patch_recipe in (([{"row": 0, "column": "name", "value": "changed"}], None), ([], recipe)):
        payload = {"path": str(left_path), "patches": patches}
        if patch_recipe:
            payload["recipe"] = patch_recipe
        patched = client.post("/apply-patch", headers=headers, json=payload)
        assert patched.status_code == 200, patched.text
        for field in ("patched_path", "upload_path"):
            path = Path(patched.json()[field])
            assert not path.exists() and desktop_vault.encrypted_path(path).is_file()
            assert client.get("/download", headers=headers, params={"path": str(path)}).status_code == 200
    parquet = client.post("/write-parquet", headers=headers, json={"path": str(left_path)})
    assert parquet.status_code == 200, parquet.text
    path = Path(parquet.json()["path"])
    assert not path.exists() and desktop_vault.encrypted_path(path).is_file()
    assert client.get("/download", headers=headers, params={"path": str(path)}).content.startswith(b"PAR1")
    run_id = run.json()["run"]["id"]
    assert desktop_vault.encrypted_path(history_dir / f"{run_id}.json").is_file()
    history = client.get("/history", headers=headers)
    assert history.status_code == 200 and any(item["id"] == run_id for item in history.json()["history"])
    rollback_run = client.post(f"/history/{run_id}/rollback", headers=headers)
    assert rollback_run.status_code == 200, rollback_run.text
    assert client.delete(f"/history/{run_id}", headers=headers).status_code == 200
    snapshot_path = Path(applied.json()["result"]["snapshot"]["path"])
    assert desktop_vault.encrypted_path(snapshot_path).is_file()
    assert not snapshot_path.exists()
    rollback = client.post(
        "/history/snapshots/rollback",
        headers=headers,
        json={"snapshot_path": str(snapshot_path)},
    )
    assert rollback.status_code == 200, rollback.text
    rollback_path = Path(rollback.json()["rollback"]["rollback_path"])
    assert desktop_vault.encrypted_path(rollback_path).is_file()
    assert not rollback_path.exists()

    external_path = tmp_path / "external.csv"
    external_recipe = {**recipe, "outputs": [{"path": str(external_path)}]}
    external = client.post("/apply", headers=headers, json=external_recipe)
    assert external.status_code == 200, external.text
    assert external_path.is_file()
    assert b"private-left" in external_path.read_bytes()

    preview = client.post(
        "/join-preview",
        headers=headers,
        json={
            "left_path": str(left_path),
            "right_path": str(right_path),
            "left_on": ["id"],
            "right_on": ["id"],
        },
    )
    assert preview.status_code == 200, preview.text

    exported = client.post(
        "/join-export",
        headers=headers,
        json={
            "left_path": str(left_path),
            "right_path": str(right_path),
            "left_on": ["id"],
            "right_on": ["id"],
            "export_format": "csv",
        },
    )
    assert exported.status_code == 200, exported.text
    export_path = Path(exported.json()["export_path"])
    encrypted_export = desktop_vault.encrypted_path(export_path)
    assert encrypted_export.is_file()
    assert not export_path.exists()
    downloaded = client.get(
        "/download",
        headers=headers,
        params={"path": str(export_path)},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert b"private-left" in downloaded.content
    encrypted_join = client.post(
        "/desktop/encrypted-download", headers=headers,
        json={"path": str(export_path), "password": "a-random-long-test-password"},
    )
    assert encrypted_join.status_code == 200, encrypted_join.text
    with pyzipper.AESZipFile(io.BytesIO(encrypted_join.content)) as archive:
        assert b"private-left" in archive.read("export.csv", pwd=b"a-random-long-test-password")

    for export_format in ("xlsx", "pandas", "sql"):
        alternate = client.post(
            "/join-export",
            headers=headers,
            json={
                "left_path": str(left_path),
                "right_path": str(right_path),
                "left_on": ["id"],
                "right_on": ["id"],
                "export_format": export_format,
            },
        )
        assert alternate.status_code == 200, alternate.text
        path = Path(alternate.json()["export_path"])
        assert desktop_vault.encrypted_path(path).is_file()
        assert not path.exists()
        downloaded_format = client.get("/download", headers=headers, params={"path": str(path)})
        assert downloaded_format.status_code == 200
        if export_format == "xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(downloaded_format.content), read_only=True)
            assert any("private-left" in str(row) for row in workbook.active.values)
            workbook.close()

    monkeypatch.setattr(fb, "_require_auth", lambda request: {"id": "test-owner"})
    with desktop_vault.request_workspace():
        fb._write_file_owner(export_path, {"id": "test-owner"})
        history_record = fb._read_record(legacy_history)
        history_record["owner"] = {"user_id": "test-owner"}
        fb._write_record(legacy_history, history_record)
    archive = client.get("/history/download_all", headers=headers)
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read(f"outputs/{export_path.name}").find(b"private-left") >= 0
        assert json.loads(zipped.read("history/run_legacy.json"))["status"] == "sensitive-status"

    history_file = desktop_vault.encrypted_path(history_dir / "run_legacy.json")
    original_history = history_file.read_bytes()
    history_file.write_bytes(original_history[:-1] + bytes([original_history[-1] ^ 1]))
    assert client.get("/history", headers=headers).status_code == 500
    history_file.write_bytes(original_history)

    deleted = client.post(
        "/uploads/delete",
        headers=headers,
        json={"path": str(left_path)},
    )
    assert deleted.status_code == 200, deleted.text
    assert not desktop_vault.encrypted_path(left_path).exists()
    assert not desktop_vault.encrypted_path(Path(left.json()["source_path"])).exists()
    assert not list((tmp_path / "temporary").glob("fb-*"))
