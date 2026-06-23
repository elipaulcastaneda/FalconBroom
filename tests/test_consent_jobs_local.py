import importlib, os, time, json
from pathlib import Path

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
    res = main.enqueue_export_job(user_id='user_a')
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
    res2 = main.enqueue_delete_job(user_id='user_a')
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
