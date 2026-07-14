import os
from fastapi.testclient import TestClient
from fbroom.main import app

client = TestClient(app)


def test_admin_metrics_tab_requires_admin(monkeypatch):
    # ensure no header -> 403
    r = client.get('/admin/metrics')
    assert r.status_code == 403

    # set ADMIN_TOKEN and call with header
    monkeypatch.setenv('ADMIN_TOKEN', 'admintoken123')
    r = client.get('/admin/metrics', headers={'X-Admin-Token': 'admintoken123'})
    assert r.status_code == 200
    assert 'Admin Metrics' in r.text
