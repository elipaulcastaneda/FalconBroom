import json
from fastapi.testclient import TestClient
from fbroom.main import app
from pathlib import Path


client = TestClient(app)


def test_health_and_metrics_and_preview(tmp_path):
    # health
    r = client.get('/health')
    assert r.status_code == 200

    # create a small CSV source file
    src = tmp_path / 'sample.csv'
    src.write_text('name,age\nAlice,30\nBob,25\n', encoding='utf-8')

    payload = {
        'instruction': 'lowercase name',
        'source_path': str(src),
        'output_path': 'out.csv',
    }
    r = client.post('/debug/recipe_from_text', json=payload)
    assert r.status_code == 200
    body = r.json()
    assert 'preview' in body

    # metrics endpoint may not be enabled in some test environments
    r = client.get('/metrics')
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        # ensure preview requests metric present
        assert b'fbroom_preview_requests_total' in r.content
