import pytest
from school_dashboard.db import (
    init_db, create_item, update_item, complete_item,
    list_items, delete_item, item_exists_for_email,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_create_and_list_items(db):
    item_id = create_item(db, child="Alice", title="Math test", due_date="2026-05-01")
    items = list_items(db)
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["child"] == "Alice"
    assert items[0]["title"] == "Math test"
    assert items[0]["completed"] == 0
    assert items[0]["source"] == "manual"


def test_complete_item(db):
    item_id = create_item(db, child="Alice", title="Soccer game")
    result = complete_item(db, item_id)
    assert result is True
    items = list_items(db, include_completed=True)
    assert items[0]["completed"] == 1
    assert items[0]["completed_at"] is not None


def test_update_item(db):
    item_id = create_item(db, child="Alice", title="Old title", notes=None)
    update_item(db, item_id, title="New title", notes="Bring cleats")
    items = list_items(db)
    assert items[0]["title"] == "New title"
    assert items[0]["notes"] == "Bring cleats"


def test_list_filters_by_child(db):
    create_item(db, child="Alice", title="Alice task")
    create_item(db, child="Bob", title="Bob task")
    alice_items = list_items(db, child="Alice")
    assert len(alice_items) == 1
    assert alice_items[0]["child"] == "Alice"


def test_list_excludes_completed_by_default(db):
    item_id = create_item(db, child="Alice", title="Done task")
    complete_item(db, item_id)
    assert list_items(db) == []
    assert len(list_items(db, include_completed=True)) == 1


def test_dedup_email_items(db):
    create_item(db, child="Alice", title="Game", due_date="2026-05-10", source="email")
    assert item_exists_for_email(db, "Alice", "Game", "2026-05-10") is True
    assert item_exists_for_email(db, "Bob", "Game", "2026-05-10") is False


def test_delete_item(db):
    item_id = create_item(db, child="Alice", title="Delete me")
    assert delete_item(db, item_id) is True
    assert list_items(db, include_completed=True) == []


def test_create_item_rejects_empty_child(db):
    with pytest.raises(ValueError, match="child"):
        create_item(db, child="", title="Task")


def test_create_item_rejects_empty_title(db):
    with pytest.raises(ValueError, match="title"):
        create_item(db, child="Alice", title="")
