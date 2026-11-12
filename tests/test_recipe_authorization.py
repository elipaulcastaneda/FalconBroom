import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbroom import main as fb


@pytest.fixture
def production_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENV", "production")
    Path("data/uploads").mkdir(parents=True)
    Path("data/recipes").mkdir(parents=True)
    monkeypatch.setattr(
        fb,
        "_get_user_from_token",
        lambda token: {"id": token, "email": f"{token}@example.com"},
    )
    return TestClient(fb.app, base_url="https://testserver")


def create_upload(user_id, name="source.csv"):
    path = Path("data/uploads") / name
    path.write_text("name\nalice\n", encoding="utf-8")
    path.with_name(f"{path.name}.meta.json").write_text(
        json.dumps({"owner": {"user_id": user_id}}),
        encoding="utf-8",
    )
    return path


def create_recipe(recipe_id, owner_id, source):
    record = {
        "id": recipe_id,
        "name": recipe_id,
        "status": "draft",
        "expected_columns": ["name"],
        "recipe": {
            "sources": [{"path": str(source.resolve())}],
            "cleaning_steps": [],
            "joins": [],
            "outputs": [],
        },
        "owner": {"user_id": owner_id},
    }
    (Path("data/recipes") / f"{recipe_id}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    return record


def auth(user_id):
    return {"Authorization": f"Bearer {user_id}"}


def test_recipe_routes_require_auth_in_production(production_client):
    source = create_upload("user-a")
    create_recipe("recipe-a", "user-a", source)
    recipe = {
        "sources": [{"path": str(source.resolve())}],
        "cleaning_steps": [],
        "joins": [],
        "outputs": [],
    }
    routes = [
        ("GET", "/recipes", {}),
        ("POST", "/recipes", {"json": {"name": "new", "recipe": recipe}}),
        ("GET", "/recipes/recipe-a", {}),
        ("GET", "/recipes/recipe-a/explain", {}),
        ("POST", "/recipes/recipe-a/approve", {}),
        ("POST", "/recipes/recipe-a/run", {}),
        ("POST", "/recipes/recipe-a/validate", {}),
        ("POST", "/recipes/recipe-a/export_sheets", {}),
        ("POST", "/preview?recipe_id=recipe-a", {"json": recipe}),
    ]

    for method, path, kwargs in routes:
        response = production_client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)


def test_recipe_list_and_reads_are_scoped_to_owner(production_client, monkeypatch):
    source_a = create_upload("user-a", "a.csv")
    source_b = create_upload("user-b", "b.csv")
    create_recipe("recipe-a", "user-a", source_a)
    create_recipe("recipe-b", "user-b", source_b)
    create_recipe("legacy-recipe", None, source_a)
    client = production_client
    monkeypatch.setattr(fb, "explain_recipe", lambda recipe, profile: [])

    listed = client.get("/recipes", headers=auth("user-a"))
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["recipes"]] == ["recipe-a"]

    own = client.get("/recipes/recipe-a", headers=auth("user-a"))
    assert own.status_code == 200
    assert own.json()["owner"]["user_id"] == "user-a"
    assert client.get("/recipes/recipe-b", headers=auth("user-a")).status_code == 404
    assert client.get("/recipes/legacy-recipe", headers=auth("user-a")).status_code == 404
    assert client.get("/recipes/recipe-b/explain", headers=auth("user-a")).status_code == 404

    explanation = client.get("/recipes/recipe-a/explain", headers=auth("user-a"))
    assert explanation.status_code == 200
    assert explanation.json()["explanations"] == []


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("POST", "/approve"),
        ("POST", "/run"),
        ("POST", "/validate"),
        ("POST", "/export_sheets"),
    ],
)
def test_recipe_mutations_and_execution_are_scoped_to_owner(production_client, method, suffix):
    source = create_upload("user-b")
    create_recipe("recipe-b", "user-b", source)

    response = production_client.request(
        method,
        f"/recipes/recipe-b{suffix}",
        headers=auth("user-a"),
    )

    assert response.status_code == 404


def test_saving_recipe_records_owner_and_rejects_another_users_source(production_client):
    owned_source = create_upload("user-a", "owned.csv")
    other_source = create_upload("user-b", "other.csv")

    def body(source):
        return {
            "name": "customer recipe",
            "recipe": {
                "sources": [{"path": str(source.resolve())}],
                "cleaning_steps": [],
                "joins": [],
                "outputs": [],
            },
        }

    saved = production_client.post(
        "/recipes",
        headers=auth("user-a"),
        json=body(owned_source),
    )
    assert saved.status_code == 200, saved.text
    record = json.loads(Path(saved.json()["path"]).read_text(encoding="utf-8"))
    assert record["owner"]["user_id"] == "user-a"

    denied = production_client.post(
        "/recipes",
        headers=auth("user-a"),
        json=body(other_source),
    )
    assert denied.status_code == 404


def test_recipe_source_cannot_reference_file_outside_owned_uploads(production_client):
    outside = Path("private.csv").resolve()
    outside.write_text("secret\nvalue\n", encoding="utf-8")
    response = production_client.post(
        "/recipes",
        headers=auth("user-a"),
        json={
            "name": "outside",
            "recipe": {
                "sources": [{"path": str(outside)}],
                "cleaning_steps": [],
                "joins": [],
                "outputs": [],
            },
        },
    )
    assert response.status_code == 404


def test_preview_cannot_read_another_users_upload(production_client):
    source = create_upload("user-b")
    response = production_client.post(
        "/preview",
        headers=auth("user-a"),
        json={
            "sources": [{"path": str(source.resolve())}],
            "cleaning_steps": [],
            "joins": [],
            "outputs": [],
        },
    )
    assert response.status_code == 404


def test_recipe_id_cannot_escape_recipe_directory(production_client):
    with pytest.raises(fb.HTTPException) as exc:
        fb._recipe_path("../../users/private")
    assert exc.value.status_code == 404


def test_local_recipe_workflow_remains_compatible(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("FALCONBROOM_LOCAL_MODE", "1")
    Path("data/recipes").mkdir(parents=True)
    source = tmp_path / "source.csv"
    source.write_text("name\nalice\n", encoding="utf-8")
    client = TestClient(fb.app)
    recipe = {
        "sources": [{"path": str(source)}],
        "cleaning_steps": [],
        "joins": [],
        "outputs": [],
    }

    saved = client.post("/recipes", json={"name": "local", "recipe": recipe})
    assert saved.status_code == 200, saved.text
    rid = saved.json()["id"]
    assert client.get("/recipes").status_code == 200
    assert client.get(f"/recipes/{rid}").status_code == 200
