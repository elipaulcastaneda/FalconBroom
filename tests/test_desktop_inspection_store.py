import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbroom import desktop_inspection_store as store
from fbroom import main as fb


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI required")


def test_desktop_inspection_roundtrip_and_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    record = {"id": "inspection_1", "inspection": {"value": "private-data"}}
    path = tmp_path / "inspection_1.json"
    store.write_inspection(path, record)
    assert b"private-data" not in path.read_bytes()
    assert store.read_inspection(path) == record
    process = subprocess.run(
        [sys.executable, "-c",
         "import sys; from pathlib import Path; from fbroom.desktop_inspection_store import read_inspection; "
         "assert read_inspection(Path(sys.argv[1]))['inspection']['value'] == 'private-data'",
         str(path)], check=False, capture_output=True,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")


def test_missing_protection_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    path = tmp_path / "inspection.json"
    def fail(*args, **kwargs):
        raise store.InspectionProtectionError("unavailable")
    monkeypatch.setattr(store, "_dpapi", fail)
    with pytest.raises(store.InspectionProtectionError):
        store.write_inspection(path, {"value": "secret"})
    assert not path.exists()


def test_corrupt_ciphertext_is_not_treated_as_plaintext(monkeypatch, tmp_path):
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    path = tmp_path / "inspection.json"
    store.write_inspection(path, {"value": "secret"})
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(store.InspectionProtectionError):
        store.read_inspection(path)


def test_opt_in_migration_preserves_legacy_data_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data") / "inspections"
    root.mkdir(parents=True)
    path = root / "inspection_1.json"
    raw = json.dumps({"value": "private"}).encode()
    path.write_bytes(raw)
    original = store._dpapi
    def fail(*args, **kwargs):
        raise store.InspectionProtectionError("unavailable")
    monkeypatch.setattr(store, "_dpapi", fail)
    with pytest.raises(store.InspectionProtectionError):
        store.migrate_inspections(root)
    assert path.read_bytes() == raw
    monkeypatch.setattr(store, "_dpapi", original)
    assert store.migrate_inspections(root) == 1
    assert store.migrate_inspections(root) == 0
    assert b"private" not in path.read_bytes()
    assert store.read_inspection(path) == {"value": "private"}


def test_inspection_endpoints_and_confirmation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data") / "inspections"
    root.mkdir(parents=True)
    monkeypatch.setattr(fb, "INSPECTIONS_DIR", root)
    store.write_inspection(root / "inspection_1.json", {"id": "inspection_1", "inspection": {"value": "private"}})
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    assert client.get("/inspections", headers=headers).json()["inspections"][0]["id"] == "inspection_1"
    assert client.get("/inspections/inspection_1", headers=headers).json()["inspection"]["value"] == "private"
    assert client.post("/desktop/protect-inspections", headers=headers, json={"confirm_text": "wrong"}).status_code == 400
    assert client.post("/desktop/protect-inspections", headers=headers, json={"confirm_text": "PROTECT INSPECTIONS"}).json()["migrated"] == 0


def test_inspection_creation_and_plaintext_history_export(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data") / "inspections"
    root.mkdir(parents=True)
    monkeypatch.setattr(fb, "INSPECTIONS_DIR", root)
    monkeypatch.setattr(fb, "_require_owned_file", lambda *args: None)
    monkeypatch.setattr(fb, "_require_auth", lambda request: {"id": "test-user"})
    monkeypatch.setattr(fb.cleaner, "inspect_source", lambda *args, **kwargs: {"sample": "private-data"})
    client = TestClient(fb.app)
    headers = {"X-FalconBroom-Desktop-Token": "a" * 64}
    response = client.post("/inspect", headers=headers, json={"path": "example.csv"})
    assert response.status_code == 200
    result = response.json()
    path = root / f"{result['dump_id']}.json"
    assert b"private-data" not in path.read_bytes()
    assert client.get(f"/inspections/{result['dump_id']}", headers=headers).json()["inspection"]["sample"] == "private-data"

    record = store.read_inspection(path)
    record["owner"] = {"user_id": "test-user"}
    store.write_inspection(path, record)
    export = client.get("/history/download_all", headers=headers)
    assert export.status_code == 200
    import io
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert json.loads(archive.read(f"inspections/{path.name}"))["inspection"]["sample"] == "private-data"
    assert not list((Path("data") / "tmp").glob("history_lineage_*.zip"))


def test_migration_replace_failure_preserves_plaintext(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data") / "inspections"
    root.mkdir(parents=True)
    path = root / "inspection_1.json"
    raw = b'{"value":"original"}'
    path.write_bytes(raw)
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(store.os, "replace", fail)
    with pytest.raises(OSError):
        store.migrate_inspections(root)
    assert path.read_bytes() == raw
    assert list(root.iterdir()) == [path]


def test_migration_verification_failure_preserves_plaintext(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    root = Path("data") / "inspections"
    root.mkdir(parents=True)
    path = root / "inspection_1.json"
    raw = b'{"value":"original"}'
    path.write_bytes(raw)
    original = store._dpapi
    def corrupt_read(data, decrypt):
        return b"incorrect" if decrypt else original(data, decrypt)
    monkeypatch.setattr(store, "_dpapi", corrupt_read)
    with pytest.raises(store.InspectionProtectionError):
        store.migrate_inspections(root)
    assert path.read_bytes() == raw
    assert list(root.iterdir()) == [path]
