import json
import logging
import sqlite3
from unittest.mock import patch

from school_dashboard import kid_colors, state
from school_dashboard.digest import send_ntfy
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


def test_fallback_wraps():
    names = [f"K{i}" for i in range(len(kid_colors.FALLBACK) + 1)]
    out = resolve_colors(names)
    assert out[names[-1]] == kid_colors.FALLBACK[0]


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
