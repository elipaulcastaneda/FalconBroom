import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbroom import main as fb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "0")
    importlib.reload(fb)
    monkeypatch.setattr(fb, "_get_user_from_token", lambda token: {"id": token})
    monkeypatch.setattr(fb.celery_process_export_job, "delay", lambda job_id: None)
    monkeypatch.setattr(fb.celery_process_delete_job, "delay", lambda job_id: None)
    return TestClient(fb.app, base_url="https://testserver")


def auth(user_id):
    return {"Authorization": f"Bearer {user_id}"}


def test_consent_job_routes_require_auth_even_outside_production(client):
    assert client.post("/consent/export-job").status_code == 401
    assert client.post("/consent/delete-job").status_code == 401
    assert client.get("/jobs/job_12345678").status_code == 401


def test_jobs_are_scoped_to_authenticated_owner(client):
    forbidden = client.post(
        "/consent/export-job?user_id=user-b",
        headers=auth("user-a"),
    )
    assert forbidden.status_code == 403

    created = client.post("/consent/export-job", headers=auth("user-a"))
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    assert client.get(f"/jobs/{job_id}", headers=auth("user-b")).status_code == 404
    status = client.get(f"/jobs/{job_id}", headers=auth("user-a"))
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert "result" not in status.json()

    # Legacy ownerless job files are not visible to authenticated remote users.
    legacy_id = "job_87654321"
    (Path("data/jobs") / f"{legacy_id}.json").write_text(
        json.dumps({"id": legacy_id, "type": "export_consents", "status": "completed"}),
        encoding="utf-8",
    )
    assert client.get(f"/jobs/{legacy_id}", headers=auth("user-a")).status_code == 404


def test_export_download_requires_completed_owned_export_with_safe_path(client):
    created = client.post("/consent/export-job", headers=auth("user-a"))
    job_id = created.json()["job_id"]
    exports_dir = Path("data/consents/exports")
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_file = exports_dir / f"export_{job_id}.json"
    export_file.write_text('{"consents":[]}', encoding="utf-8")

    job_file = Path("data/jobs") / f"{job_id}.json"
    job = json.loads(job_file.read_text(encoding="utf-8"))
    job.update(
        status="completed",
        result={"export_path": str(export_file.resolve())},
    )
    job_file.write_text(json.dumps(job), encoding="utf-8")

    assert client.get(
        f"/jobs/{job_id}?download=true", headers=auth("user-b")
    ).status_code == 404
    download = client.get(
        f"/jobs/{job_id}?download=true", headers=auth("user-a")
    )
    assert download.status_code == 200
    assert download.json() == {"consents": []}

    job["result"]["export_path"] = str(Path("outside.json").resolve())
    job_file.write_text(json.dumps(job), encoding="utf-8")
    assert client.get(
        f"/jobs/{job_id}?download=true", headers=auth("user-a")
    ).status_code == 404


def test_explicit_local_desktop_mode_keeps_unauthenticated_job_access(client, monkeypatch):
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    job_id = fb._write_job(
        {
            "type": "delete_consents",
            "user_id": "desktop-user",
            "requester_user_id": "desktop-user",
            "status": "pending",
        }
    )
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
