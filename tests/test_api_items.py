import os
import pytest
from web.app import app as flask_app


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "test.db")
    flask_app.config["TESTING"] = True
    os.environ["SCHOOL_DB_PATH"] = db
    with flask_app.test_client() as c:
        yield c
    os.environ.pop("SCHOOL_DB_PATH", None)


def test_get_items_empty(client):
    r = client.get("/api/items")
    assert r.status_code == 200
    assert r.get_json() == {"items": []}


def test_post_item_creates(client):
    r = client.post("/api/items", json={
        "child": "Alice", "title": "Soccer practice", "type": "extracurricular"
    })
    assert r.status_code == 201
    data = r.get_json()
    assert data["title"] == "Soccer practice"
    assert data["source"] == "manual"
    assert data["completed"] == 0


def test_patch_item_completes(client):
    r = client.post("/api/items", json={"child": "Alice", "title": "Test"})
    item_id = r.get_json()["id"]
    r2 = client.patch(f"/api/items/{item_id}", json={"completed": True})
    assert r2.status_code == 200
    r3 = client.get("/api/items?include_completed=1")
    items = r3.get_json()["items"]
    assert items[0]["completed"] == 1
    assert items[0]["completed_at"] is not None


def test_patch_item_edits(client):
    r = client.post("/api/items", json={"child": "Alice", "title": "Original"})
    item_id = r.get_json()["id"]
    r2 = client.patch(f"/api/items/{item_id}", json={"title": "Updated", "notes": "New notes"})
    assert r2.status_code == 200
    items = client.get("/api/items").get_json()["items"]
    assert items[0]["title"] == "Updated"
    assert items[0]["notes"] == "New notes"
