"""Alias audit: a roster keyed by a LONG name with `name_aliases` -> the SHORT canonical name used in every section."""
import json

import pytest

from recon.api_support import data, make_api, sgy
from recon.ingest_support import sgy_payload
from school_dashboard import state

CFG = {"children": {"Alex": {"grade": "6", "school": "S"}, "Samantha": {"grade": "4", "school": "S", "color": "teal"},
                    "Caseyson": {"grade": "2", "school": "S"}},
       "name_aliases": {"samantha": "Sam", "caseyson": "Casey"}}


def _seed(kid: str, key: str) -> dict:
    return {**sgy("Essay " + key, "Math 6", key), "kid": kid}


@pytest.fixture
def api(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, [_seed("Sam", "s1"), _seed("Casey", "s2"), _seed("Alex", "s3")], roster=None)
    cfg = tmp_path / "aliased.json"
    cfg.write_text(json.dumps(CFG))
    monkeypatch.setenv("SCHOOL_DASHBOARD_CONFIG", str(cfg))
    monkeypatch.setattr(state, "_config_cache", None)
    return a


def test_roster_names_are_canonical_and_deduplicated(api):
    from school_dashboard.assignments import readctx
    assert readctx.roster_names() == ("Alex", "Sam", "Casey")


def test_config_lists_each_child_once_under_the_canonical_name_with_stable_colours(api):
    kids = data(api.get("/api/v2/config"))["data"]["kids"]
    assert [k["name"] for k in kids] == ["Alex", "Sam", "Casey"]
    colours = {k["name"]: k["color"] for k in kids}
    assert colours["Sam"] == "teal" and colours["Casey"] == "lavender"


def test_summary_has_one_entry_per_child_and_the_rows_are_under_it(api):
    kids = data(api.get("/api/v2/summary"))["data"]["kids"]
    assert [k["name"] for k in kids] == ["Alex", "Sam", "Casey"]
    sam = next(k for k in kids if k["name"] == "Sam")
    assert "counts" in sam and sam["name"] == "Sam"


def test_overview_and_config_agree_on_names_and_colours(api):
    overview = api.get("/api/overview").get_json()["kids"]
    config = data(api.get("/api/v2/config"))["data"]["kids"]
    assert overview == config


def test_kid_param_accepts_the_canonical_name(api):
    resp = api.get("/api/v2/assignments?kid=Sam")
    assert resp.status_code == 200, resp.data[:200]
    assert [i["kid"] for i in resp.get_json()["data"]] == ["Sam"]


def test_ingest_roster_filter_keeps_scraped_short_names(api):
    from school_dashboard.assignments import ingest
    assert ingest._roster() == frozenset({"Alex", "Sam", "Casey"})


def test_digest_cards_use_the_same_colour_as_the_overview(api):
    from school_dashboard.kid_colors import with_child_colors
    roster = state.canonical_roster()
    cards = with_child_colors([{"child": "Sam"}, {"child": "Casey"}], {r["name"]: r["color"] for r in roster if r.get("color")},
                              [r["name"] for r in roster])
    overview = {k["name"]: k["color"] for k in api.get("/api/overview").get_json()["kids"]}
    assert [c["color"] for c in cards] == [overview["Sam"], overview["Casey"]] == ["teal", "lavender"]


def test_gc_team_label_with_the_long_name_matches_the_canonical_kid(api):
    from school_dashboard.overview_events import split_kids
    assert split_kids("Samantha+Alex", ["Alex", "Sam"]) == ["Sam", "Alex"]
