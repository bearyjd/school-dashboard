"""Pure request validation for /api/v2 (no Flask)."""
import dataclasses

import pytest

from school_dashboard.assignments.service import Actor
from web import api_v2_validate as v

KIDS = frozenset({"Alex", "Sam"})
A = {"role": "parent", "name": "Dana"}


def code(fn, *args, status=400):
    with pytest.raises(v.RequestError) as ei:
        fn(*args)
    assert ei.value.status == status
    return ei.value.code


def test_module_has_no_flask_import():
    import inspect
    assert "flask" not in inspect.getsource(v).lower()


def test_media_type_and_size():
    v.check_media_type("application/json")
    assert code(v.check_media_type, "text/plain", status=415) == "unsupported_media_type"
    assert code(v.check_media_type, None, status=415) == "unsupported_media_type"
    v.check_size(v.MAX_BODY)
    v.check_size(None)
    assert code(v.check_size, v.MAX_BODY + 1, status=413) == "payload_too_large"


@pytest.mark.parametrize("raw", [b"[]", b"1", b'"x"', b"null", b"nope", b"\xff\xfe", b"[" * 5000, b""])
def test_parse_body_rejects_non_objects(raw):
    assert code(v.parse_body, raw) in ("invalid_json", "invalid_body")


def test_parse_body_ok_and_oversized_read():
    assert v.parse_body(b'{"a": 1}') == {"a": 1}
    assert code(v.parse_body, b" " * (v.MAX_BODY + 1), status=413) == "payload_too_large"


def test_parsed_requests_are_frozen_and_input_untouched():
    body = {"action": "done", "actor": dict(A)}
    snapshot = {"action": "done", "actor": dict(A)}
    req = v.parse_stage(body)
    assert body == snapshot
    assert req.action == "done" and req.actor == Actor("parent", "Dana")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(req, "action", "undo")


@pytest.mark.parametrize("body,c", [
    ({"action": "done"}, "invalid_actor"),
    ({"action": "done", "actor": "Dana"}, "invalid_actor"),
    ({"action": "done", "actor": {"role": "admin", "name": "D"}}, "invalid_actor"),
    ({"action": "done", "actor": {"role": "parent", "name": ""}}, "invalid_actor"),
    ({"action": "done", "actor": {"role": "parent", "name": "x" * 41}}, "invalid_actor"),
    ({"action": "done", "actor": {"role": "parent", "name": 5}}, "invalid_actor"),
    ({"action": "done", "actor": {"role": "parent", "name": "D", "x": 1}}, "unknown_field"),
    ({"action": "done", "actor": {"role": "parent"}}, "invalid_actor"),
    ({"action": "bogus", "actor": A}, "invalid_field"),
    ({"action": True, "actor": A}, "invalid_field"),
    ({"actor": A}, "invalid_field"),
    ({"action": "done", "actor": A, "extra": 1}, "unknown_field"),
])
def test_stage_rejections(body, c):
    assert code(v.parse_stage, body) == c


def test_actor_name_is_cleaned():
    assert v.parse_stage({"action": "undo", "actor": {"role": "kid", "name": " A\x00lex  B "}}).actor.name == "Alex B"


def test_confirm_note_rules():
    assert v.parse_teacher_confirmed({"note": " Got  it ", "actor": A}).note == "Got it"
    for bad in ("ab", "x" * 501, "  \x00 ", 5, None, True):
        assert code(v.parse_teacher_confirmed, {"note": bad, "actor": A}) == "invalid_field"


@pytest.mark.parametrize("bad", [True, False, "2", 2.0, None, 0, -3, [1], 2 ** 63])
def test_ids_must_be_real_positive_ints(bad):
    assert code(v.parse_merge, {"into": bad, "actor": A}) == "invalid_field"
    assert code(v.parse_split, {"link_id": bad, "actor": A}) == "invalid_field"


def test_merge_split_remerge_ok():
    assert v.parse_merge({"into": 4, "actor": A}).into == 4
    assert v.parse_split({"link_id": 9, "actor": A}).link_id == 9
    assert v.parse_remerge({"actor": A}).actor.role == "parent"
    assert code(v.parse_remerge, {"actor": A, "x": 1}) == "unknown_field"


def test_class_put_ok_and_defaults():
    req = v.parse_class_put({"kid": "Alex", "class_name": " Math  6 ", "actor": A}, KIDS)
    assert (req.kid, req.class_name) == ("Alex", "Math 6")
    assert req.settings.grace_school_days is None and req.settings.paper_only is False
    full = v.parse_class_put({"kid": "Sam", "class_name": "ELA", "grace_school_days": 3, "paper_only": True,
                              "aliases": ["Language Arts"], "teacher_name": "Ms R", "teacher_email": "r@x.invalid",
                              "actor": A}, KIDS)
    assert full.settings.aliases == ("Language Arts",) and full.settings.paper_only is True


@pytest.mark.parametrize("field,bad", [
    ("kid", 1), ("class_name", ""), ("class_name", "x" * 121), ("class_name", 5),
    ("grace_school_days", True), ("grace_school_days", "3"), ("grace_school_days", 11), ("grace_school_days", -1),
    ("grace_school_days", 2.5), ("paper_only", 1), ("paper_only", "yes"), ("paper_only", None),
    ("aliases", "ELA"), ("aliases", [1]), ("aliases", [""]), ("aliases", ["x"] * 21), ("aliases", ["x" * 101]),
    ("teacher_name", 5), ("teacher_name", "x" * 121), ("teacher_email", 5), ("teacher_email", "x" * 201)])
def test_class_put_rejections(field, bad):
    body = {"kid": "Alex", "class_name": "Math 6", "actor": A, field: bad}
    assert code(v.parse_class_put, body, KIDS) == "invalid_field"


def test_class_put_kid_must_be_known_and_unknown_keys_rejected():
    assert code(v.parse_class_put, {"kid": "Zed", "class_name": "M", "actor": A}, KIDS) == "invalid_kid"
    assert code(v.parse_class_put, {"kid": "Alex", "class_name": "M", "actor": A, "z": 1}, KIDS) == "unknown_field"


def test_parse_kid():
    assert v.parse_kid("Alex", KIDS) == "Alex"
    for bad in ("Zed", "", None, 5, "alex"):
        assert code(v.parse_kid, bad, KIDS) == "invalid_kid"


def test_require_parent():
    v.require_parent(Actor("parent", "D"))
    assert code(v.require_parent, Actor("kid", "A"), status=403) == "forbidden_role"


def test_list_query_defaults_and_values():
    f = v.parse_list_query({}, KIDS)
    assert (f.limit, f.offset, f.include_closed, f.kid) == (200, 0, False, None)
    f = v.parse_list_query({"kid": "Alex", "bucket": "do_tonight", "flag": "missing", "stage": "2", "kind": "task",
                            "include_closed": "1", "limit": "500", "offset": "3", "ignored": "x"}, KIDS)
    assert (f.kid, f.bucket, f.flag, f.stage, f.kind, f.include_closed, f.limit, f.offset) == (
        "Alex", "do_tonight", "missing", 2, "task", True, 500, 3)


@pytest.mark.parametrize("q", [
    {"limit": "0"}, {"limit": "501"}, {"limit": "x"}, {"limit": "1.5"}, {"limit": ""}, {"offset": "-1"},
    {"offset": "x"}, {"bucket": "bogus"}, {"flag": "bogus"}, {"stage": "0"}, {"stage": "5"}, {"stage": "x"},
    {"kind": "bogus"}, {"include_closed": "2"}, {"include_closed": "true"}, {"limit": "٣"}])
def test_list_query_rejections(q):
    assert code(v.parse_list_query, q, KIDS) in ("invalid_query",)


def test_list_query_bad_kid():
    assert code(v.parse_list_query, {"kid": "Zed"}, KIDS) == "invalid_kid"


def test_class_query():
    assert v.parse_class_query({"kid": "Alex"}, KIDS) == "Alex"
    assert code(v.parse_class_query, {}, KIDS) == "invalid_kid"
