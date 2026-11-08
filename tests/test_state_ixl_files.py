"""update_from_ixl_files must not refresh a child's `updated` from empty/error files."""
import json

from school_dashboard.state import update_from_ixl_files

OLD = {"updated": "2026-10-31T06:00:00", "totals": {"math": 1}, "remaining": []}


def _state():
    return {"ixl": {"Ford": dict(OLD)}, "schoology": {}}


def test_error_shaped_file_is_skipped(tmp_path, caplog):
    (tmp_path / "ford-assigned.json").write_text(json.dumps(
        {"status": "error", "errors": [{"code": "auth.login_failed", "stage": "login"}], "remaining": []}))
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 0
    assert state["ixl"]["Ford"] == OLD
    assert "ford-assigned.json" in caplog.text


MATH = {"math": {"assigned": 3, "done": 1, "remaining": 2}}


def test_empty_and_non_dict_files_are_skipped(tmp_path):
    (tmp_path / "ford-assigned.json").write_text("")
    (tmp_path / "jack-assigned.json").write_text("[]")
    (tmp_path / "penn-assigned.json").write_text("{}")
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 0
    assert state["ixl"]["Ford"] == OLD
    assert "Jack" not in state["ixl"] and "Penn" not in state["ixl"]


def test_valid_file_is_merged(tmp_path):
    (tmp_path / "ford-assigned.json").write_text(json.dumps(
        {"status": "ok", "errors": [], "totals": MATH, "remaining": []}))
    state = _state()
    assert update_from_ixl_files(state, str(tmp_path)) == 1
    assert state["ixl"]["Ford"]["totals"] == MATH
    assert state["ixl"]["Ford"]["updated"] != OLD["updated"]
