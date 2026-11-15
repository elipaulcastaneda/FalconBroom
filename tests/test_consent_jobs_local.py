import importlib, os, time, json
import asyncio
import io
import sqlite3
import subprocess
import sys
import pyzipper
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def desktop_consents(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Desktop vault uses Windows DPAPI")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    import fbroom.main as main
    importlib.reload(main)
    monkeypatch.setattr(main.desktop_vault, "_root", None)
    monkeypatch.setattr(main.desktop_vault, "_master_key", None)
    main.desktop_vault.initialize(Path("data"))
    return main, TestClient(main.app), {"X-FalconBroom-Desktop-Token": "a" * 64}


def test_desktop_consent_receipts_and_job_export_are_encrypted(desktop_consents):
    main, client, headers = desktop_consents
    receipt = client.post("/consent", headers=headers, json={
        "user_id": "alice", "consents": {"analytics": False}, "user_agent": "private-agent",
    })
    assert receipt.status_code == 200, receipt.text
    receipt_path = Path("data") / "consents" / (receipt.json()["id"] + ".json")
    encrypted = main.desktop_vault.encrypted_path(receipt_path)
    assert encrypted.is_file() and not receipt_path.exists()
    assert b"private-agent" not in encrypted.read_bytes()
    listed = client.get("/consent", headers=headers, params={"user_id": "alice"})
    assert listed.status_code == 200 and listed.json()["consents"][0]["consents"] == {"analytics": False}
    job = client.post("/consent/export-job", headers=headers, params={"user_id": "alice"})
    assert job.status_code == 200, job.text
    jid = job.json()["job_id"]
    stored = Path("data") / "consents" / "exports" / f"export_{jid}.json"
    assert not stored.exists() and main.desktop_vault.encrypted_path(stored).is_file()
    downloaded = client.get(f"/jobs/{jid}", headers=headers, params={"download": True})
    assert downloaded.status_code == 200 and downloaded.json()["consents"][0]["user_agent"] == "private-agent"
    assert downloaded.headers["cache-control"] == "no-store"
    password = "a-long-random-passphrase-for-testing"
    protected = client.post(f"/jobs/{jid}/encrypted-download", headers=headers, json={"password": password})
    assert protected.status_code == 200 and protected.headers["cache-control"] == "no-store"
    assert client.post(f"/jobs/{jid}/encrypted-download", json={"password": password}).status_code == 401
    assert b"private-agent" not in protected.content
    with pyzipper.AESZipFile(io.BytesIO(protected.content)) as archive:
        archive.setpassword(password.encode())
        assert json.loads(archive.read("consents.json"))["consents"][0]["user_agent"] == "private-agent"
    with pyzipper.AESZipFile(io.BytesIO(protected.content)) as archive:
        archive.setpassword(b"wrong-password")
        with pytest.raises(RuntimeError):
            archive.read("consents.json")
    assert client.post(f"/jobs/{jid}/encrypted-download", headers=headers, json={"password": "short"}).status_code == 400
    deleted = client.delete(f"/consent/{receipt.json()['id']}", headers=headers)
    assert deleted.status_code == 200 and not encrypted.exists()


def test_desktop_consent_migration_conflicts_and_resume(desktop_consents):
    main, client, headers = desktop_consents
    root = Path("data") / "consents"
    root.mkdir(exist_ok=True)
    path = root / "consent_old.json"
    record = {"id": "consent_old", "user_id": "alice", "consents": {"analytics": False}}
    path.write_text(json.dumps(record), encoding="utf-8")
    main.desktop_vault.write_encrypted_bytes(path, b'{"different":true}')
    path.write_text(json.dumps(record), encoding="utf-8")
    assert client.post("/desktop/protect-consents", headers=headers, json={"confirm_text": "wrong"}).status_code == 400
    response = client.post("/desktop/protect-consents", headers=headers, json={"confirm_text": "PROTECT CONSENTS"})
    assert response.status_code == 500 and path.exists()
    assert client.get("/consent", headers=headers, params={"user_id": "alice"}).status_code == 500
    main.desktop_vault.encrypted_path(path).unlink()
    response = client.post("/desktop/protect-consents", headers=headers, json={"confirm_text": "PROTECT CONSENTS"})
    assert response.status_code == 200 and response.json()["migrated"] == 1
    assert not path.exists()
    assert client.get("/consent", headers=headers, params={"user_id": "alice"}).json()["consents"] == [record]
    assert client.post("/desktop/protect-consents", headers=headers, json={"confirm_text": "PROTECT CONSENTS"}).json()["migrated"] == 0


def test_desktop_consent_operations_fail_without_vault(desktop_consents, monkeypatch):
    main, client, headers = desktop_consents
    monkeypatch.setattr(main.desktop_vault, "_master_key", None)
    assert client.get("/consent", headers=headers, params={"user_id": "alice"}).status_code == 500
    assert client.post("/consent", headers=headers, json={"user_id": "alice", "consents": {}}).status_code == 500
    assert client.post("/consent/export-job", headers=headers, params={"user_id": "alice"}).status_code == 500


def test_desktop_audit_is_encrypted_and_detects_tampering(desktop_consents):
    main, client, headers = desktop_consents
    response = client.post("/consent", headers=headers, json={
        "user_id": "secret-user", "consents": {"analytics": False}, "user_agent": "secret-agent",
    })
    assert response.status_code == 200
    db = Path("data") / ".vault" / "audit.sqlite3"
    assert db.is_file()
    assert b"secret-user" not in db.read_bytes()
    assert b"secret-agent" not in db.read_bytes()
    assert not (Path("data") / "consents" / "audit.log").exists()
    entries = main.desktop_audit.read_all()
    assert entries[0]["event"] == "record_consent"
    assert "secret-user" not in json.dumps(entries)
    with sqlite3.connect(str(db)) as connection:
        connection.execute("UPDATE entries SET ciphertext = x'00' WHERE sequence = 1")
    with pytest.raises(main.desktop_vault.DesktopVaultError):
        main.desktop_audit.read_all()
    with pytest.raises(main.desktop_vault.DesktopVaultError):
        main._append_audit_event(Path("data") / "consents", "next", {})
    assert not (Path("data") / "consents" / "audit.log").exists()
    failed = client.post("/consent", headers=headers, json={"user_id": "alice", "consents": {}})
    assert failed.status_code == 500


def test_desktop_audit_concurrent_writes_and_legacy_label(desktop_consents):
    main, _, _ = desktop_consents
    root = Path("data") / "consents"
    root.mkdir(exist_ok=True)
    (root / "audit.log").write_text('{"event":"old"}\n', encoding="utf-8")
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(main._append_audit_event, root, "test", {"user_id": "private"}) for _ in range(12)]
        for future in futures:
            assert future.result()
    records = main._collect_audit_logs()
    assert sum(row["integrity"] == "verified-desktop-chain" for row in records) == 12
    assert sum(row["integrity"] == "legacy-unverified" for row in records) == 1


def test_legacy_audit_migration_keeps_unverified_status(desktop_consents):
    main, client, headers = desktop_consents
    root = Path("data") / "consents"
    root.mkdir(exist_ok=True)
    audit = root / "audit.log"
    audit.write_text('{"event":"old","user_id":"private"}\n', encoding="utf-8")
    privacy = Path("data") / "privacy" / "privacy_records.json"
    privacy.write_text('[{"event":"old"}]', encoding="utf-8")
    assert client.post("/desktop/protect-audit", headers=headers, json={"confirm_text": "wrong"}).status_code == 400
    response = client.post("/desktop/protect-audit", headers=headers, json={"confirm_text": "PROTECT AUDIT"})
    assert response.status_code == 200 and response.json()["migrated"] == 2
    assert not audit.exists() and not privacy.exists()
    assert b"private" not in main.desktop_vault.encrypted_path(audit).read_bytes()
    records = main._collect_audit_logs()
    assert records[0]["integrity"] == "legacy-unverified"
    assert records[0]["record"]["user_id"] == "private"
    assert client.post("/desktop/protect-audit", headers=headers, json={"confirm_text": "PROTECT AUDIT"}).json()["migrated"] == 0


def test_legacy_audit_migration_rejects_conflicting_copies(desktop_consents):
    main, client, headers = desktop_consents
    root = Path("data") / "consents"
    root.mkdir(exist_ok=True)
    audit = root / "audit.log"
    main.desktop_vault.write_encrypted_bytes(audit, b"old")
    audit.write_text("different", encoding="utf-8")
    response = client.post("/desktop/protect-audit", headers=headers, json={"confirm_text": "PROTECT AUDIT"})
    assert response.status_code == 500 and audit.read_text() == "different"
    with pytest.raises(main.desktop_vault.DesktopVaultError):
        main._collect_audit_logs()


def test_desktop_audit_truncation_and_interrupted_transaction(desktop_consents):
    main, _, _ = desktop_consents
    root = Path("data") / "consents"
    main._append_audit_event(root, "first", {})
    main._append_audit_event(root, "second", {})
    db = Path("data") / ".vault" / "audit.sqlite3"
    connection = sqlite3.connect(str(db))
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("DELETE FROM entries WHERE sequence = 2")
    connection.close()
    assert len(main.desktop_audit.read_all()) == 2
    with sqlite3.connect(str(db)) as connection:
        connection.execute("DELETE FROM entries WHERE sequence = 2")
    with pytest.raises(main.desktop_vault.DesktopVaultError):
        main.desktop_audit.read_all()


def test_desktop_audit_cross_process_writers(desktop_consents):
    main, _, _ = desktop_consents
    script = (
        "import sys; from pathlib import Path; from fbroom import desktop_vault, desktop_audit; "
        "desktop_vault.initialize(Path('data')); "
        "[(desktop_audit.append('consents', 'worker', {'user_id':'private'}, 'now')) for _ in range(4)]"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(main.__file__).resolve().parents[1])
    processes = [
        subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        for _ in range(3)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    assert len(main.desktop_audit.read_all()) == 12


def test_audit_database_prevents_replacing_missing_vault_key(desktop_consents, monkeypatch):
    main, _, _ = desktop_consents
    main._append_audit_event(Path("data") / "consents", "test", {})
    (Path("data") / ".vault" / "master.dpapi").unlink()
    monkeypatch.setattr(main.desktop_vault, "_root", None)
    monkeypatch.setattr(main.desktop_vault, "_master_key", None)
    with pytest.raises(main.desktop_vault.DesktopVaultError, match="key is missing"):
        main.desktop_vault.initialize(Path("data"))


def test_legacy_desktop_consent_job_migration_and_conflict(desktop_consents):
    main, client, headers = desktop_consents
    path = main._job_file_path("job_12345678")
    record = {"id": path.stem, "type": "export_consents", "user_id": "alice", "requester_user_id": "alice", "status": "pending"}
    path.write_text(json.dumps(record), encoding="utf-8")
    assert client.get("/jobs/job_12345678", headers=headers).status_code == 200
    assert client.post("/desktop/protect-consent-jobs", headers=headers, json={"confirm_text": "wrong"}).status_code == 400
    main.desktop_vault.seal_file(path, remove_source=False)
    path.write_text(json.dumps({**record, "user_id": "another"}), encoding="utf-8")
    response = client.post("/desktop/protect-consent-jobs", headers=headers, json={"confirm_text": "PROTECT CONSENT JOBS"})
    assert response.status_code == 500 and path.exists()
    main.desktop_vault.encrypted_path(path).unlink()
    response = client.post("/desktop/protect-consent-jobs", headers=headers, json={"confirm_text": "PROTECT CONSENT JOBS"})
    assert response.status_code == 200 and response.json()["migrated"] == 1
    assert not path.exists()
    assert client.get("/jobs/job_12345678", headers=headers).status_code == 200
    assert client.post("/desktop/protect-consent-jobs", headers=headers, json={"confirm_text": "PROTECT CONSENT JOBS"}).json()["migrated"] == 0


def test_consent_migration_keeps_original_if_verification_fails(desktop_consents, monkeypatch):
    main, client, headers = desktop_consents
    root = Path("data") / "consents"
    root.mkdir(exist_ok=True)
    path = root / "consent_old.json"
    original = json.dumps({"id": "consent_old", "user_id": "alice", "consents": {}})
    path.write_text(original, encoding="utf-8")
    seal = main.desktop_vault.seal_file

    def corrupt_ciphertext(source, **kwargs):
        destination = seal(source, **kwargs)
        destination.write_bytes(b"damaged")
        return destination

    monkeypatch.setattr(main.desktop_vault, "seal_file", corrupt_ciphertext)
    response = client.post("/desktop/protect-consents", headers=headers, json={"confirm_text": "PROTECT CONSENTS"})
    assert response.status_code == 500
    assert path.read_text(encoding="utf-8") == original


def test_consent_job_download_rejects_conflicting_copies(desktop_consents):
    main, client, headers = desktop_consents
    job = client.post("/consent/export-job", headers=headers, params={"user_id": "alice"})
    assert job.status_code == 200
    jid = job.json()["job_id"]
    stored = Path("data") / "consents" / "exports" / f"export_{jid}.json"
    stored.write_text('{"consents": []}', encoding="utf-8")
    response = client.get(f"/jobs/{jid}", headers=headers, params={"download": True})
    assert response.status_code == 500
    response = client.post(f"/jobs/{jid}/encrypted-download", headers=headers, json={"password": "long-passphrase-for-this-test"})
    assert response.status_code == 500


def test_consent_encrypted_download_respects_opt_out(desktop_consents, monkeypatch):
    main, client, headers = desktop_consents
    job = client.post("/consent/export-job", headers=headers, params={"user_id": "alice"})
    assert job.status_code == 200
    monkeypatch.setattr(main, "_is_opted_out", lambda **kwargs: True)
    response = client.post(
        f"/jobs/{job.json()['job_id']}/encrypted-download", headers=headers,
        json={"password": "long-passphrase-for-this-test"},
    )
    assert response.status_code == 403


def test_local_account_cleanup_finds_encrypted_consent_jobs(desktop_consents):
    main, client, headers = desktop_consents
    job = client.post("/consent/export-job", headers=headers, params={"user_id": "alice"})
    assert job.status_code == 200
    jid = job.json()["job_id"]
    record = main._job_file_path(jid)
    export = Path(main._read_job(record)["result"]["export_path"])
    main._check_desktop_deletion_auxiliary_ownership("alice")
    main._delete_local_user_data("alice")
    assert not main.desktop_vault.exists(record)
    assert not main.desktop_vault.exists(export)


def test_independent_desktop_consent_worker_waits_for_exclusive_gate(desktop_consents):
    main, _, _ = desktop_consents
    from fbroom.desktop_request_gate import _WindowsFileGate
    jid = main._write_job({"type": "export_consents", "user_id": "alice", "status": "pending"})
    gate = _WindowsFileGate(Path("data") / ".desktop-request-gate")
    try:
        asyncio.run(gate.lock(0, True, time.monotonic() + 2))
        asyncio.run(gate.lock(1, True, time.monotonic() + 2))
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(main.celery_process_export_job, jid)
            time.sleep(0.2)
            assert not worker.done()
            assert main._read_job(main._job_file_path(jid))["status"] == "pending"
            gate.close()
            worker.result(timeout=5)
        assert main._read_job(main._job_file_path(jid))["status"] == "completed"
    finally:
        if not gate._file.closed:
            gate.close()


@pytest.mark.parametrize("job_type", ["export_consents", "delete_consents"])
@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
def test_packaged_desktop_runs_consent_job_before_request_returns(tmp_path, monkeypatch, job_type):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    import fbroom.main as main
    importlib.reload(main)
    monkeypatch.setattr(main.desktop_vault, "_root", None)
    monkeypatch.setattr(main.desktop_vault, "_master_key", None)
    main.desktop_vault.initialize(Path("data"))

    consent_dir = Path("data") / "consents"
    consent_dir.mkdir(parents=True, exist_ok=True)
    (consent_dir / "consent_a.json").write_text(
        json.dumps({"id": "consent_a", "user_id": "user_a"}), encoding="utf-8"
    )

    dispatches = []

    def unexpected_dispatch(*args, **kwargs):
        dispatches.append((args, kwargs))

    task = main.celery_process_export_job if job_type == "export_consents" else main.celery_process_delete_job
    monkeypatch.setattr(task, "delay", unexpected_dispatch)
    result = main._enqueue_consent_job(job_type, "user_a", "user_a")
    job = main._read_job(main._job_file_path(result["job_id"]))
    assert not main._job_file_path(result["job_id"]).exists()
    assert b"user_a" not in main.desktop_vault.encrypted_path(main._job_file_path(result["job_id"])).read_bytes()
    assert not dispatches
    assert job["status"] == "completed"
    if job_type == "export_consents":
        assert main.desktop_vault.encrypted_path(Path(job["result"]["export_path"])).is_file()
    else:
        assert not (consent_dir / "consent_a.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
def test_packaged_desktop_reports_consent_job_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    import fbroom.main as main
    importlib.reload(main)
    monkeypatch.setattr(main.desktop_vault, "_root", None)
    monkeypatch.setattr(main.desktop_vault, "_master_key", None)
    main.desktop_vault.initialize(Path("data"))

    def fail_job(job_id, coordinated=False):
        raise RuntimeError("job failed")

    monkeypatch.setattr(main, "celery_process_export_job", fail_job)
    with pytest.raises(main.HTTPException) as exc_info:
        main._enqueue_consent_job("export_consents", "user_a", "user_a")
    assert exc_info.value.status_code == 503
    jobs = main._job_files()
    assert len(jobs) == 1
    job = main._read_job(jobs[0])
    assert job["status"] == "failed"
    assert job["result"]["error"] == "job_dispatch_failed"


def test_export_and_delete_jobs_local(tmp_path, monkeypatch):
    # run module with CWD set to tmp_path so data/ dirs are created there
    monkeypatch.chdir(tmp_path)
    # import fbroom.main after chdir so it creates data dirs under tmp_path
    import fbroom.main as main
    importlib.reload(main)

    # create sample consent receipts
    CONSENT_DIR = Path('data') / 'consents'
    CONSENT_DIR.mkdir(parents=True, exist_ok=True)
    sample1 = {'id': 'consent_a', 'user_id': 'user_a', 'consents': {'analytics': True}, 'user_agent': 'ua', 'ip': '1.2.3.4', 'timestamp': '2026-01-01T00:00:00Z'}
    sample2 = {'id': 'consent_b', 'user_id': 'user_a', 'consents': {'analytics': False}, 'user_agent': 'ua', 'ip': '1.2.3.4', 'timestamp': '2026-01-01T00:01:00Z'}
    (CONSENT_DIR / 'consent_a.json').write_text(json.dumps(sample1))
    (CONSENT_DIR / 'consent_b.json').write_text(json.dumps(sample2))

    # enqueue export job
    res = main._enqueue_consent_job('export_consents', 'user_a', 'user_a')
    jid = res.get('job_id')
    assert jid

    # small sleep to allow eager task to run
    time.sleep(0.1)
    jobf = Path('data') / 'jobs' / f"{jid}.json"
    assert jobf.exists()
    job = json.loads(jobf.read_text())
    assert job.get('status') in ('completed','processing')
    if job.get('status') == 'completed':
        result = job.get('result') or {}
        export_path = result.get('export_path')
        assert export_path and Path(export_path).exists()

        # check audit log
        audit = CONSENT_DIR / 'audit.log'
        assert audit.exists()

    # enqueue delete job
    res2 = main._enqueue_consent_job('delete_consents', 'user_a', 'user_a')
    jid2 = res2.get('job_id')
    assert jid2
    time.sleep(0.1)
    jobf2 = Path('data') / 'jobs' / f"{jid2}.json"
    assert jobf2.exists()
    job2 = json.loads(jobf2.read_text())
    assert job2.get('status') in ('completed','processing')

    # ensure consents removed
    remaining = list(CONSENT_DIR.glob('consent_*.json'))
    assert len(remaining) == 0
