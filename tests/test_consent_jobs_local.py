import importlib, os, time, json
from pathlib import Path

import pytest


@pytest.mark.parametrize("job_type", ["export_consents", "delete_consents"])
def test_packaged_desktop_runs_consent_job_before_request_returns(tmp_path, monkeypatch, job_type):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    import fbroom.main as main
    importlib.reload(main)

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
    job = json.loads((Path("data") / "jobs" / f"{result['job_id']}.json").read_text())
    assert not dispatches
    assert job["status"] == "completed"
    if job_type == "export_consents":
        assert Path(job["result"]["export_path"]).is_file()
    else:
        assert not (consent_dir / "consent_a.json").exists()


def test_packaged_desktop_reports_consent_job_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    import fbroom.main as main
    importlib.reload(main)

    def fail_job(job_id):
        raise RuntimeError("job failed")

    monkeypatch.setattr(main, "celery_process_export_job", fail_job)
    with pytest.raises(main.HTTPException) as exc_info:
        main._enqueue_consent_job("export_consents", "user_a", "user_a")
    assert exc_info.value.status_code == 503
    jobs = list((Path("data") / "jobs").glob("job_*.json"))
    assert len(jobs) == 1
    job = json.loads(jobs[0].read_text())
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
