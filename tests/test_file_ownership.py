import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from fbroom import main as fb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("FALCONBROOM_LOCAL_MODE", raising=False)
    for folder in ("uploads", "outputs", "inspections", "history", "recipes"):
        Path("data", folder).mkdir(parents=True, exist_ok=True)

    def authenticate(request):
        user = request.headers.get("x-test-user")
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"id": user}

    monkeypatch.setattr(fb, "_require_auth", authenticate)
    return TestClient(fb.app)


def upload(name, owner=None):
    path = Path("data/uploads") / name
    path.write_text("id,name\n1,Alice\n", encoding="utf-8")
    if owner:
        path.with_name(path.name + ".meta.json").write_text(
            json.dumps({"owner": {"user_id": owner}}), encoding="utf-8"
        )
    return str(path.resolve())


def test_source_access_requires_auth_and_ownership(client):
    own = upload("own.csv", "alice")
    other = upload("other.csv", "bob")
    legacy = upload("legacy.csv")
    assert client.post("/profile", json={"path": own}).status_code == 401
    headers = {"x-test-user": "alice"}
    assert client.post("/profile", json={"path": own}, headers=headers).status_code == 200
    for path in (other, legacy):
        assert client.post("/profile", json={"path": path}, headers=headers).status_code == 404
        assert client.get("/download", params={"path": path}, headers=headers).status_code == 404
        assert client.post("/uploads/delete", json={"path": path}, headers=headers).status_code == 404
    assert {item["path"] for item in client.get("/uploads", headers=headers).json()["uploads"]} == {own}


def test_source_rejects_traversal_outside_uploads(client):
    outside = Path("data/outputs/outside.csv")
    outside.write_text("id\n1\n", encoding="utf-8")
    outside.with_name(outside.name + ".meta.json").write_text(
        json.dumps({"owner": {"user_id": "alice"}}), encoding="utf-8"
    )
    response = client.post(
        "/profile", json={"path": "data/uploads/../outputs/outside.csv"}, headers={"x-test-user": "alice"}
    )
    assert response.status_code == 404


def test_source_rejects_symlink_outside_uploads(client):
    outside = Path("data/outputs/outside.csv").resolve()
    outside.write_text("id\n1\n", encoding="utf-8")
    link = Path("data/uploads/link.csv")
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    link.with_name(link.name + ".meta.json").write_text(
        json.dumps({"owner": {"user_id": "alice"}}), encoding="utf-8"
    )
    response = client.post("/profile", json={"path": str(link)}, headers={"x-test-user": "alice"})
    assert response.status_code == 404


@pytest.mark.parametrize("route", ["/join-suggestions", "/join-preview", "/join-export"])
def test_join_checks_both_sources(client, route):
    own = upload("own.csv", "alice")
    other = upload("other.csv", "bob")
    headers = {"x-test-user": "alice"}
    for left, right in ((own, other), (other, own)):
        response = client.post(route, json={"left_path": left, "right_path": right}, headers=headers)
        assert response.status_code == 404, response.text


def test_inspections_are_owner_scoped(client):
    own = upload("own.csv", "alice")
    alice = {"x-test-user": "alice"}
    bob = {"x-test-user": "bob"}
    response = client.post("/inspect", json={"path": own}, headers=alice)
    assert response.status_code == 200, response.text
    record_id = response.json()["dump_id"]
    assert client.get(f"/inspections/{record_id}", headers=bob).status_code == 404
    assert client.get("/inspections", headers=bob).json()["inspections"] == []
    assert client.get(f"/inspections/{record_id}", headers=alice).status_code == 200


def test_upload_conversion_and_owner_scoped_download(client):
    alice = {"x-test-user": "alice"}
    bob = {"x-test-user": "bob"}
    response = client.post(
        "/upload", files={"file": ("sample.csv", b"id,name\n1,Alice\n", "text/csv")}, headers=alice
    )
    assert response.status_code == 200, response.text
    path = response.json()["path"]
    assert client.post("/metadata", json={"path": path}, headers=alice).status_code == 200
    assert client.get("/download", params={"path": path}, headers=alice).status_code == 200
    assert client.get("/download", params={"path": path}, headers=bob).status_code == 404


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pandas", "sql"])
def test_join_export_output_is_owned(client, fmt):
    left = upload("left.csv", "alice")
    right = upload("right.csv", "alice")
    alice = {"x-test-user": "alice"}
    response = client.post(
        "/join-export",
        headers=alice,
        json={"left_path": left, "right_path": right, "left_on": ["id"], "right_on": ["id"], "export_format": fmt},
    )
    assert response.status_code == 200, response.text
    path = response.json()["export_path"]
    assert client.get("/download", params={"path": path}, headers=alice).status_code == 200
    assert client.get("/download", params={"path": path}, headers={"x-test-user": "bob"}).status_code == 404


def test_join_export_rejects_unsafe_filename(client):
    own = upload("own.csv", "alice")
    response = client.post(
        "/join-export",
        headers={"x-test-user": "alice"},
        json={"left_path": own, "right_path": own, "left_on": ["id"], "right_on": ["id"], "filename": "../overwrite"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("route", ["/preview", "/apply"])
@pytest.mark.parametrize("params", [{"right": "other"}, {"right_path": "other"}, {"rules": [{"map_path": "other"}]}])
def test_recipe_step_cannot_read_another_users_file(client, route, params):
    own = upload("own.csv", "alice")
    other = upload("other.csv", "bob")
    if "rules" in params:
        params["rules"][0]["map_path"] = other
    else:
        params[next(iter(params))] = other
    recipe = {
        "sources": [{"path": own}],
        "cleaning_steps": [{"action": "entity_resolution", "column": None, "params": params}],
        "outputs": [],
    }
    response = client.post(route, json=recipe, headers={"x-test-user": "alice"})
    assert response.status_code == 404, response.text


def test_remote_apply_creates_owned_output_and_snapshot(client):
    own = upload("own.csv", "alice")
    recipe = {"sources": [{"path": own}], "cleaning_steps": [], "outputs": [{"path": "../unsafe.csv"}]}
    response = client.post("/apply", json=recipe, headers={"x-test-user": "alice"})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    output = Path(result["written"])
    snapshot = Path(result["snapshot"]["path"])
    assert output.resolve().is_relative_to(Path("data/outputs").resolve())
    assert json.loads(output.with_name(output.name + ".meta.json").read_text())["owner"]["user_id"] == "alice"
    assert json.loads(snapshot.with_name(snapshot.name + ".meta.json").read_text())["owner"]["user_id"] == "alice"
    assert not Path("../unsafe.csv").exists()


def test_account_cleanup_removes_owned_outputs_and_snapshots(client):
    for folder in (Path("data/outputs"), Path("data/history/snapshots")):
        folder.mkdir(parents=True, exist_ok=True)
        for owner in ("alice", "bob"):
            artifact = folder / f"{owner}.csv"
            artifact.write_text("value\n1\n", encoding="utf-8")
            artifact.with_name(artifact.name + ".meta.json").write_text(
                json.dumps({"owner": {"user_id": owner}}), encoding="utf-8"
            )
    fb._delete_local_user_data("alice")
    for folder in (Path("data/outputs"), Path("data/history/snapshots")):
        assert not (folder / "alice.csv").exists()
        assert not (folder / "alice.csv.meta.json").exists()
        assert (folder / "bob.csv").exists()


def test_account_cleanup_does_not_delete_other_users_output(client):
    output = Path("data/outputs/bob.csv")
    output.write_text("value\n2\n", encoding="utf-8")
    output.with_name(output.name + ".meta.json").write_text(
        json.dumps({"owner": {"user_id": "bob"}}), encoding="utf-8"
    )
    history = Path("data/history/run.json")
    history.write_text(
        json.dumps({"owner": {"user_id": "alice"}, "output_path": str(output.resolve())}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Could not verify or remove"):
        fb._delete_local_user_data("alice")
    assert output.exists()
    assert history.exists()


@pytest.mark.parametrize("kind", ["upload", "output"])
def test_account_cleanup_preserves_ownership_metadata_when_artifact_removal_fails(
    client, monkeypatch, kind
):
    root = Path("data/uploads" if kind == "upload" else "data/outputs")
    artifact = root / "alice.csv"
    artifact.write_text("value\n1\n", encoding="utf-8")
    metadata = artifact.with_name(artifact.name + ".meta.json")
    metadata.write_text(json.dumps({"owner": {"user_id": "alice"}}), encoding="utf-8")
    delete = fb._delete_owned_file

    def fail_artifact(path, root):
        if path == artifact:
            raise OSError("simulated failure")
        delete(path, root)

    monkeypatch.setattr(fb, "_delete_owned_file", fail_artifact)
    with pytest.raises(RuntimeError, match="Could not verify or remove"):
        fb._delete_local_user_data("alice")
    assert artifact.exists()
    assert metadata.exists()

    monkeypatch.setattr(fb, "_delete_owned_file", delete)
    fb._delete_local_user_data("alice")
    assert not artifact.exists()
    assert not metadata.exists()


@pytest.mark.parametrize("kind", ["upload", "output"])
def test_account_cleanup_retries_metadata_after_artifact_removed(client, monkeypatch, kind):
    root = Path("data/uploads" if kind == "upload" else "data/outputs")
    artifact = root / "alice.csv"
    artifact.write_text("value\n1\n", encoding="utf-8")
    metadata = artifact.with_name(artifact.name + ".meta.json")
    metadata.write_text(json.dumps({"owner": {"user_id": "alice"}}), encoding="utf-8")
    delete = fb._delete_owned_file

    def fail_metadata(path, root):
        if path == metadata:
            raise OSError("simulated failure")
        delete(path, root)

    monkeypatch.setattr(fb, "_delete_owned_file", fail_metadata)
    with pytest.raises(RuntimeError, match="Could not verify or remove"):
        fb._delete_local_user_data("alice")
    assert not artifact.exists()
    assert metadata.exists()

    monkeypatch.setattr(fb, "_delete_owned_file", delete)
    fb._delete_local_user_data("alice")
    assert not metadata.exists()


@pytest.mark.skipif(os.name != "nt", reason="Desktop vault uses Windows DPAPI")
@pytest.mark.parametrize("kind", ["upload", "output"])
def test_account_cleanup_retries_encrypted_artifacts(client, monkeypatch, kind):
    root = Path("data/uploads" if kind == "upload" else "data/outputs")
    artifact = root / "alice.csv"
    metadata = artifact.with_name(artifact.name + ".meta.json")
    monkeypatch.setattr(fb.desktop_vault, "_root", None)
    monkeypatch.setattr(fb.desktop_vault, "_master_key", None)
    fb.desktop_vault.initialize(Path("data"))
    fb.desktop_vault.write_encrypted_bytes(artifact, b"value\n1\n")
    fb.desktop_vault.write_encrypted_bytes(
        metadata, json.dumps({"owner": {"user_id": "alice"}}).encode("utf-8")
    )
    delete = fb._delete_owned_file

    def fail_artifact(path, root):
        if path == artifact:
            raise OSError("simulated failure")
        delete(path, root)

    monkeypatch.setattr(fb, "_delete_owned_file", fail_artifact)
    with pytest.raises(RuntimeError, match="Could not verify or remove"):
        fb._delete_local_user_data("alice")
    assert fb.desktop_vault.exists(artifact)
    assert fb.desktop_vault.exists(metadata)

    monkeypatch.setattr(fb, "_delete_owned_file", delete)
    fb._delete_local_user_data("alice")
    assert not fb.desktop_vault.exists(artifact)
    assert not fb.desktop_vault.exists(metadata)
