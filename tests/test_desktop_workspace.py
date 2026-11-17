import asyncio
import io
import json
import os
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fbroom.desktop_workspace import account_workspace, current_account, workspace_directory


def test_workspaces_separate_accounts_and_projects_without_assigning_legacy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path("data") / "history"
    root.mkdir(parents=True)
    legacy = root / "legacy.json"
    legacy.write_text("legacy", encoding="utf-8")
    paths = []
    for project, user in [
        ("https://project.supabase.co", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("https://project.supabase.co", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("https://other.supabase.co", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ]:
        with account_workspace(project, user):
            paths.append(workspace_directory(root))
            assert list(paths[-1].iterdir()) == []
    assert len(set(paths)) == 3
    assert legacy.read_text(encoding="utf-8") == "legacy"
    assert workspace_directory(root) == root
    assert current_account() is None


def test_workspace_context_isolated_between_async_tasks_and_threads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path("data") / "uploads"
    root.mkdir(parents=True)

    async def work(user):
        with account_workspace("https://project.supabase.co", user):
            await asyncio.sleep(0)
            return await asyncio.to_thread(workspace_directory, root)

    async def run():
        return await asyncio.gather(
            work("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            work("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        )

    first, second = asyncio.run(run())
    assert first != second
    assert current_account() is None


def test_rejects_redirected_workspace_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path("data") / "outputs"
    root.mkdir(parents=True)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == ".accounts" or original(path))
    with account_workspace("https://project.supabase.co", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"):
        with pytest.raises(ValueError, match="redirected"):
            workspace_directory(root)


def test_remote_lookup_cache_is_separate_for_each_account(monkeypatch):
    import httpx
    import pandas as pd
    from fbroom import desktop_remote_services, engine

    calls = []
    monkeypatch.setattr(engine, "_EXTERNAL_LOOKUP_CACHE", {})
    monkeypatch.setattr(desktop_remote_services, "require", lambda service: None)
    monkeypatch.setattr(desktop_remote_services, "network_access", lambda service: nullcontext())

    def get(url, timeout, headers):
        calls.append(current_account())
        return httpx.Response(200, json={"value": current_account()[1]})

    monkeypatch.setattr(httpx, "get", get)
    params = {"template": "https://example.test/{id}", "map": {"value": "resolved"}}
    values = []
    for user in ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"):
        with account_workspace("https://project.supabase.co", user):
            values.append(engine._external_lookup(pd.DataFrame({"id": [1]}), params)["resolved"][0])
    assert values == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
    assert len(calls) == 2


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault requires Windows DPAPI")
def test_isolated_sidecar_leaves_legacy_files_untouched(tmp_path, monkeypatch):
    import uvicorn
    from fbroom import desktop_request_gate, desktop_vault, sidecar

    monkeypatch.chdir(tmp_path)
    legacy = Path("data") / "uploads" / "legacy.csv"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy contents")
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setattr(desktop_request_gate, "desktop_startup_gate", lambda: nullcontext())
    monkeypatch.setattr(desktop_vault, "migrate_plaintext_files", lambda roots: pytest.fail("Legacy migration ran"))
    monkeypatch.setattr(desktop_vault, "migrate_plaintext_file", lambda *args: pytest.fail("Legacy migration ran"))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_ISOLATION", "1")
    sidecar.main(["--port", "3009"])
    assert legacy.read_bytes() == b"legacy contents"
    assert not desktop_vault.encrypted_path(legacy).exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault requires Windows DPAPI")
def test_scoped_desktop_routes_isolate_uploads_runs_and_legacy(tmp_path, monkeypatch):
    from fbroom import desktop_vault, main

    monkeypatch.chdir(tmp_path)
    for name in ("uploads", "outputs", "recipes", "history", "inspections"):
        (Path("data") / name).mkdir(parents=True)
    legacy = Path("data") / "history" / "run_legacy.json"
    legacy.write_text('{"id":"run_legacy"}', encoding="utf-8")
    monkeypatch.setattr(desktop_vault, "_root", None)
    monkeypatch.setattr(desktop_vault, "_master_key", None)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_SECRET", "a" * 64)
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_ISOLATION", "1")
    monkeypatch.setenv("FALCONBROOM_DESKTOP_ACCOUNT_URL", "https://project.supabase.co")
    monkeypatch.setenv("FALCONBROOM_REMOTE_POLICY_FILE", str(tmp_path / "data" / "remote-services.json"))
    desktop_vault.initialize(Path("data"))
    ids = {
        "Bearer account-a": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "Bearer account-b": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    }

    async def verify(client, project, public_key, authorization):
        if authorization not in ids:
            raise HTTPException(status_code=401, detail="Invalid account")
        return {"id": ids[authorization]}

    monkeypatch.setattr(main, "verify_desktop_account", verify)
    client = TestClient(main.app)

    def headers(account):
        authorization = f"Bearer {account}"
        return {
            "X-FalconBroom-Desktop-Token": "a" * 64,
            "X-FalconBroom-Account-Publishable-Key": "public",
            "X-FalconBroom-Expected-Account": ids[authorization],
            "Authorization": authorization,
        }

    a, b = headers("account-a"), headers("account-b")
    legacy_source = Path("data") / "uploads" / "legacy.csv"
    desktop_vault.write_encrypted_bytes(legacy_source, b"id,value\n1,legacy\n")
    desktop_vault.write_encrypted_bytes(
        legacy_source.with_name("legacy.csv.meta.json"),
        json.dumps({"owner": {"user_id": ids["Bearer account-a"]}}).encode(),
    )
    assert client.post("/profile", headers=a, json={"path": str(legacy_source)}).status_code == 404
    def upload_for(account_headers, name):
        response = client.post(
            "/desktop/upload-stream",
            headers={**account_headers, "X-FalconBroom-Filename": name},
            content=b"id,value\n1,concurrent\n",
        )
        assert response.status_code == 200, response.text
        return response.json()["path"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(upload_for, a, "concurrent-a.csv")
        future_b = pool.submit(upload_for, b, "concurrent-b.csv")
        path_a, path_b = future_a.result(), future_b.result()
    assert client.post("/profile", headers=a, json={"path": path_a}).status_code == 200
    assert client.post("/profile", headers=b, json={"path": path_b}).status_code == 200
    assert client.post("/profile", headers=a, json={"path": path_b}).status_code == 404
    assert client.post("/profile", headers=b, json={"path": path_a}).status_code == 404
    with account_workspace("https://project.supabase.co", ids["Bearer account-a"]):
        sqlite_path = workspace_directory(Path("data") / "uploads") / "lookup.sqlite"
        original_sqlite = tmp_path / "synthetic-lookup.sqlite"
        with sqlite3.connect(original_sqlite) as connection:
            connection.execute("CREATE TABLE lookup (code TEXT, value TEXT)")
            connection.execute("INSERT INTO lookup VALUES ('1', 'account-a')")
        desktop_vault.seal_file(original_sqlite, sqlite_path, remove_source=False)
        main._write_file_owner(sqlite_path, {"id": ids["Bearer account-a"]})
    lookup = {
        "sources": [{"path": path_a}],
        "cleaning_steps": [{"action": "external_lookup", "column": None, "params": {
            "mode": "sqlite", "db": str(sqlite_path),
            "query": "SELECT value FROM lookup WHERE code='{id}'", "map": {"value": "resolved"},
        }}],
        "outputs": [],
    }
    own_lookup = client.post("/preview", headers=a, json=lookup)
    assert own_lookup.status_code == 200, own_lookup.text
    assert "account-a" in own_lookup.text
    other_lookup = {**lookup, "sources": [{"path": path_b}]}
    assert client.post("/preview", headers=b, json=other_lookup).status_code == 404
    upload = client.post(
        "/desktop/upload-stream",
        headers={**a, "X-FalconBroom-Filename": "source.csv"},
        content=b"id,value\n1,private\n",
    )
    assert upload.status_code == 200, upload.text
    source = upload.json()["path"]
    assert client.post("/profile", headers=a, json={"path": source}).status_code == 200
    assert client.post("/profile", headers=b, json={"path": source}).status_code == 404
    own_metadata = client.post("/metadata", headers=a, json={"path": source})
    assert own_metadata.status_code == 200, own_metadata.text
    assert own_metadata.json()["metadata"]["plain_language_suggestions"] == upload.json()["plain_language_suggestions"]
    assert client.post("/metadata", headers=b, json={"path": source}).status_code == 404
    generated = client.post("/recipe-from-text", headers=a, json={"source_path": source, "instruction": "uppercase value"})
    assert generated.status_code == 200, generated.text
    assert client.post("/recipe-from-text", headers=b, json={"source_path": source, "instruction": "uppercase value"}).status_code == 404
    assert client.post("/apply-patch", headers=b, json={"path": source, "patches": []}).status_code == 404
    assert client.post("/write-parquet", headers=b, json={"path": source}).status_code == 404
    inspection = client.post("/inspect", headers=a, json={"path": source})
    assert inspection.status_code == 200, inspection.text
    inspection_id = inspection.json()["dump_id"]
    assert client.get(f"/inspections/{inspection_id}", headers=a).status_code == 200
    assert client.get(f"/inspections/{inspection_id}", headers=b).status_code == 404
    assert client.get("/download", headers=b, params={"path": source}).status_code == 404
    uploads_b = client.get("/uploads", headers=b).json()["uploads"]
    assert all(item["path"] != source for item in uploads_b)
    assert client.get("/history", headers=b).json() == {"history": []}
    assert client.post("/uploads/delete", headers=b, json={"path": source}).status_code == 404
    join = {
        "left_path": source, "right_path": source,
        "left_on": ["id"], "right_on": ["id"], "filename": "joined",
    }
    assert client.post("/join-preview", headers=b, json=join).status_code == 404
    preview = client.post("/join-preview", headers=a, json=join)
    assert preview.status_code == 200, preview.text
    export = client.post("/join-export", headers=a, json=join)
    assert export.status_code == 200, export.text
    output = export.json()["export_path"]
    assert client.get("/download", headers=a, params={"path": output}).status_code == 200
    assert client.get("/download", headers=b, params={"path": output}).status_code == 404
    history = client.get("/history", headers=a).json()["history"]
    assert len(history) == 1
    run_id = history[0]["id"]
    assert client.post(f"/history/{run_id}/rollback", headers=b).status_code == 404
    assert client.delete(f"/history/{run_id}", headers=b).status_code == 404
    assert client.post(f"/history/{run_id}/rollback", headers=a).status_code == 200
    assert client.get("/history", headers=b).json() == {"history": []}
    recipe = {"sources": [{"path": source}], "cleaning_steps": [], "outputs": []}
    applied = client.post("/apply", headers=a, json=recipe)
    assert applied.status_code == 200, applied.text
    snapshot = applied.json()["result"]["snapshot"]["path"]
    assert client.post("/history/snapshots/rollback", headers=b, json={"snapshot_path": snapshot}).status_code == 404
    rolled_back = client.post("/history/snapshots/rollback", headers=a, json={"snapshot_path": snapshot})
    assert rolled_back.status_code == 200, rolled_back.text
    assert client.post("/history/snapshots/cleanup", headers=b, json={"days": 0}).status_code == 200
    assert client.post("/history/snapshots/rollback", headers=a, json={"snapshot_path": snapshot}).status_code == 200
    saved = client.post("/recipes", headers=a, json={"name": "synthetic", "recipe": recipe})
    assert saved.status_code == 200, saved.text
    recipe_id = saved.json()["id"]
    assert client.get(f"/recipes/{recipe_id}", headers=b).status_code == 404
    assert client.post(f"/recipes/{recipe_id}/approve", headers=a).status_code == 200
    run = client.post(f"/recipes/{recipe_id}/run", headers=a)
    assert run.status_code == 200, run.text
    run_id = run.json()["run"]["id"]
    assert client.post(f"/history/{run_id}/rollback", headers=b).status_code == 404
    own_archive = client.get("/history/download_all", headers=a)
    assert own_archive.status_code == 200, own_archive.text
    with zipfile.ZipFile(io.BytesIO(own_archive.content)) as bundle:
        assert any(run_id in name for name in bundle.namelist())
        assert any(name.startswith("outputs/") for name in bundle.namelist())
    archive = client.get("/history/download_all", headers=b)
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert not bundle.namelist()
    assert legacy.read_text(encoding="utf-8") == '{"id":"run_legacy"}'
    switched = {**b, "X-FalconBroom-Expected-Account": ids["Bearer account-a"]}
    assert client.post("/profile", headers=switched, json={"path": source}).status_code == 409
    assert client.get("/history", headers={"X-FalconBroom-Desktop-Token": "a" * 64}).status_code == 401
    async def unavailable(*args):
        raise HTTPException(status_code=503, detail="Account verification is unavailable")

    monkeypatch.setattr(main, "verify_desktop_account", unavailable)
    assert client.get("/history", headers=a).status_code == 503
