import pytest
import json
import logging
import sqlite3
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from school_dashboard import kid_colors, state
from school_dashboard.digest import send_ntfy
from school_dashboard.overview import build_overview
from school_dashboard.kid_colors import PALETTE, resolve_colors, with_child_colors


def test_named_defaults_are_stable_regardless_of_order():
    a = resolve_colors(["Jack", "Penn", "Ford"])
    b = resolve_colors(["Ford", "Penn", "Jack"])
    assert a == b == {"Jack": "green", "Penn": "lavender", "Ford": "amber"}


def test_defaults_are_case_insensitive():
    assert resolve_colors(["jack"]) == {"jack": "green"}


def test_explicit_color_beats_default():
    assert resolve_colors(["Jack"], {"Jack": "rose"}) == {"Jack": "rose"}


def test_unknown_kids_use_positional_fallback_deterministically():
    out = resolve_colors(["Alpha", "Jack", "Beta"])
    assert out["Jack"] == "green"
    assert out["Alpha"] == kid_colors.FALLBACK[0]
    assert out["Beta"] == kid_colors.FALLBACK[1]
    assert out == resolve_colors(["Alpha", "Jack", "Beta"])


def test_fallback_wraps_only_after_palette_exhausted():
    names = [f"K{i}" for i in range(len(PALETTE) + 1)]
    out = resolve_colors(names)
    assert len(set(list(out.values())[:len(PALETTE)])) == len(PALETTE)
    assert out[names[-1]] == out[names[0]]


def test_fallback_skips_colors_taken_by_explicit_or_default():
    assert resolve_colors(["Ford", "Alpha", "Beta"], {"Ford": "blue"}) == {
        "Ford": "blue", "Alpha": "rose", "Beta": "teal"}
    assert len(set(resolve_colors(["Jack", "Penn", "Ford", "A", "B", "C"]).values())) == 6


def test_defaults_are_in_palette_and_distinct():
    vals = list(kid_colors.DEFAULTS.values())
    assert len(set(vals)) == len(vals)
    assert set(vals) <= set(PALETTE)


def test_with_child_colors_adds_color_without_mutating():
    cards = [{"child": "Penn", "title": "x"}, {"child": "", "title": "y"}]
    out = with_child_colors(cards, {})
    assert out[0]["color"] == "lavender"
    assert out[1]["color"] == ""
    assert "color" not in cards[0]


def test_roster_color_validated(tmp_path, monkeypatch, caplog):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"children": {
        "Ford": {"grade": "3", "school": "S", "color": "teal"},
        "Penn": {"grade": "6", "school": "S", "color": "chartreuse"},
        "Jack": {"grade": "8", "school": "S"},
    }}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))
    state._config_cache = None
    with caplog.at_level(logging.WARNING):
        roster = {r["name"]: r for r in state.get_roster()}
    state._config_cache = None
    assert roster["Ford"]["color"] == "teal"
    assert "color" not in roster["Penn"]
    assert "color" not in roster["Jack"]
    assert "chartreuse" in caplog.text


def test_digest_store_cards_carry_color(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    state._config_cache = None
    db = str(tmp_path / "s.db")
    card = {"source": "ixl", "child": "Ford", "title": "t", "detail": "", "due_date": None,
            "url": "", "done": False}
    with patch("school_dashboard.digest.requests.post"):
        send_ntfy("t", "m", "Homework Check", cards=[card], db_path=db)
    state._config_cache = None
    with sqlite3.connect(db) as c:
        raw = c.execute("select cards from digests").fetchone()[0]
    assert json.loads(raw)[0]["color"] == "amber"


def test_roster_color_is_trimmed_and_lowercased(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"children": {"Ford": {"grade": "3", "school": "S", "color": " Teal "}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))
    state._config_cache = None
    roster = state.get_roster()
    state._config_cache = None
    assert roster[0]["color"] == "teal"


def test_card_child_matches_roster_case_insensitively():
    cards = [{"child": "ford"}, {"child": "alpha"}]
    out = with_child_colors(cards, {"Ford": "rose"}, ["Ford", "Alpha"])
    assert [c["color"] for c in out] == ["rose", "blue"]


def test_digest_fallback_follows_roster_order_not_alphabetical(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"children": {"Beta": {"grade": "2", "school": "S"},
                                          "Alpha": {"grade": "1", "school": "S"}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))
    state._config_cache = None
    db = str(tmp_path / "d.db")
    cards = [{"child": c, "title": "t"} for c in ("Alpha", "Beta")]
    with patch("school_dashboard.digest.requests.post"):
        send_ntfy("t", "m", "Homework Check", cards=cards, db_path=db)
    state._config_cache = None
    with sqlite3.connect(db) as c:
        stored = json.loads(c.execute("select cards from digests").fetchone()[0])
    assert {c["child"]: c["color"] for c in stored} == {"Beta": "blue", "Alpha": "rose"}


def test_digest_fallback_colors_match_overview(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"children": {"Alpha": {"grade": "1", "school": "S"},
                                          "Beta": {"grade": "2", "school": "S"}}}))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(p))
    state._config_cache = None
    db = str(tmp_path / "d.db")
    # Zed sorts before Alpha, so a sorted-only order would give Zed blue.
    cards = [{"child": c, "title": "t"} for c in ("Beta", "Zed", "Alpha")]
    with patch("school_dashboard.digest.requests.post"):
        send_ntfy("t", "m", "Homework Check", cards=cards, db_path=db)
    roster = state.get_roster()
    state._config_cache = None
    with sqlite3.connect(db) as c:
        stored = json.loads(c.execute("select cards from digests").fetchone()[0])
    got = {c["child"]: c["color"] for c in stored}
    assert got == {"Alpha": "blue", "Beta": "rose", "Zed": "teal"}
    tz = ZoneInfo("America/New_York")
    ov = build_overview({}, [], {}, [], {}, roster, datetime(2026, 11, 6, 12, tzinfo=tz), tz)
    assert {k["name"]: k["color"] for k in ov["kids"]} == {"Alpha": "blue", "Beta": "rose"}


@pytest.mark.parametrize("child", [None, 5, "missing"])
def test_with_child_colors_tolerates_bad_child(child):
    card = {"title": "t"} if child == "missing" else {"child": child, "title": "t"}
    out = with_child_colors([card, {"child": "Ford"}], None, ["Ford"])
    assert out[0]["color"] == "" and out[1]["color"] == "amber"


def _digest_rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("select cards from digests").fetchall()


@pytest.mark.parametrize("child", [None, 5])
def test_send_ntfy_stores_row_with_bad_child_card(tmp_path, monkeypatch, child):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    state._config_cache = None
    db = str(tmp_path / "s.db")
    cards = [{"child": child, "title": "t"}, {"child": "Ford", "title": "u"}]
    with patch("school_dashboard.digest.requests.post") as post:
        send_ntfy("t", "m", "Homework Check", cards=cards, db_path=db)
    state._config_cache = None
    stored = json.loads(_digest_rows(db)[0][0])
    assert [c["color"] for c in stored] == ["", "amber"]
    assert "?digest=" in post.call_args.kwargs["headers"]["Click"]


def test_send_ntfy_roster_failure_stores_uncoloured_and_warns(tmp_path, caplog):
    db = str(tmp_path / "s.db")
    cards = [{"child": "Ford", "title": "u"}]
    with patch("school_dashboard.digest.canonical_roster", side_effect=RuntimeError("boom")), \
            patch("school_dashboard.digest.requests.post") as post, caplog.at_level(logging.WARNING):
        send_ntfy("t", "m", "Homework Check", cards=cards, db_path=db)
    rows = _digest_rows(db)
    assert len(rows) == 1 and json.loads(rows[0][0]) == cards
    assert "?digest=" in post.call_args.kwargs["headers"]["Click"]
    warns = [r for r in caplog.records if "colour" in r.getMessage().lower()]
    assert len(warns) == 1 and "RuntimeError" in warns[0].getMessage() and "boom" not in warns[0].getMessage()


def test_send_ntfy_empty_cards_records_row_and_keeps_static_link(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(tmp_path / "none.json"))
    state._config_cache = None
    db = str(tmp_path / "s.db")
    with patch("school_dashboard.digest.requests.post") as post:
        send_ntfy("t", "m", "Homework Check", cards=[], db_path=db)
    state._config_cache = None
    assert json.loads(_digest_rows(db)[0][0]) == []
    assert "?digest=" not in post.call_args.kwargs["headers"]["Click"]
