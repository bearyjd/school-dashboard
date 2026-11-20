"""Pure v2 digest builder (S7): golden texts, ladder, ordering, purity."""
import copy
import re
import subprocess
from collections import Counter
import sys
from pathlib import Path

import pytest

from recon import digest_support as ds

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", sorted(ds.SCENARIOS))
def test_golden_text(name):
    assert ds.SCENARIOS[name]().text + "\n" == ds.golden(name)


def test_mixed_counts_and_cards():
    out = ds.mixed()
    assert (out.kids, out.open_items) == (3, 11)
    assert len(out.cards) == 11
    first = out.cards[0]
    assert first == {"source": "recon", "child": "Alex", "title": "Essay draft",
                     "detail": "Do tonight: Math 6, due Nov 19", "due_date": "2026-11-19", "url": "", "done": False}
    assert {c["detail"].split(":")[0] for c in out.cards} >= {"Missing", "Disputed", "Done, not received"}


def test_nothing_open_gives_no_cards():
    out = ds.all_clear()
    assert out.cards == () and out.open_items == 0 and out.kids == 3


def test_morning_and_night_differ_only_in_the_heading_word():
    night, morning = ds.mixed("night"), ds.mixed("morning")
    assert night.text.replace("Do tonight", "Due next") == morning.text
    assert night.cards[0]["detail"].startswith("Do tonight") and morning.cards[0]["detail"].startswith("Due next")


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        ds.build(ds.summary(("Alex",)), [], "afternoon")


def test_text_is_plain_ascii_without_markdown():
    text = ds.mixed().text
    assert all(ord(ch) < 128 for ch in text) and "**" not in text and "#" not in text


@pytest.mark.parametrize("name,max_cap_lines", [("ladder_cap3", 16), ("ladder_cap1", 10)])
def test_ladder_steps_stay_within_budget(name, max_cap_lines):
    text = ds.SCENARIOS[name]().text
    assert len(text.encode("utf-8")) <= 3500
    assert max(len(b.splitlines()) for b in text.split("\n\n")) <= max_cap_lines


def test_ladder_first_step_cap_three():
    text = ds.SCENARIOS["ladder_cap3"]().text
    block = text.split("\n\n")[1]
    assert block.count("\n- Assignment") == 3 and "- +3 more" in block


def test_ladder_second_step_cap_one():
    block = ds.SCENARIOS["ladder_cap1"]().text.split("\n\n")[1]
    assert block.count("\n- Assignment") == 1 and "- +4 more" in block


def test_ladder_last_step_is_counts_only_and_keeps_every_kid_and_header():
    out = ds.SCENARIOS["ladder_counts"]()
    lines = out.text.splitlines()
    assert lines[0].startswith("Data as of")
    assert [ln for ln in lines if ln.startswith("Student")] == [
        f"Student{n:02d}: Do tonight 5, Waiting 5, Needs action 5" for n in range(24)]
    assert out.cards == () and out.kids == 24 and out.open_items == 360


def test_cards_are_capped_at_thirty():
    assert len(ds.many(2, 7).cards) == 30


def test_unverified_banner_names_sources_and_all_clear_is_downgraded():
    assert "NOT VERIFIED: assignment merge not up to date." in ds.all_clear_unverified().text
    banner = ds.banner_sources().text
    assert "NOT VERIFIED: PowerSchool, Schoology not up to date." in banner and "Alex: all clear" in banner


def test_header_always_carries_the_date():
    assert ds.mixed().text.splitlines()[0] == "Data as of Wed Nov 18, 4:05 PM"
    assert ds.build(ds.summary(("Alex",), as_of="2026-11-18T09:00:00-05:00"), []).text.startswith(
        "Data as of Wed Nov 18, 9:00 AM")
    assert ds.build(ds.summary(("Alex",), as_of="2026-11-20T00:07:00-05:00"), []).text.startswith(
        "Data as of Fri Nov 20, 12:07 AM")


def test_ixl_has_its_own_line_and_heading_counts_assignments_only():
    text = ds.mixed().text
    assert "Do tonight (2)\n- Essay draft" in text and "- IXL" not in text
    assert "\nIXL: ELA 2 remaining, Math 4 remaining\n" in text and "Spanish" not in text


def test_ixl_only_kid_shows_name_and_line_without_a_heading():
    block = ds.ixl_remaining().text.split("\n\n")[1]
    assert block == "Alex\nIXL: Math 3 remaining" and "Do tonight" not in ds.ixl_remaining().text


def test_ixl_without_a_number_is_listed_as_status_unknown():
    assert "IXL: ELA status unknown, Math 4 remaining" in ds.ixl_unknown().text
    out = ds.ixl_unknown()
    assert out.open_items == 2 and [c["title"] for c in out.cards] == ["IXL ELA", "IXL Math"]


def test_kid_with_only_zero_remaining_ixl_is_all_clear():
    out = ds.build(ds.summary(("Alex",)), [ds.ixl("Alex", "Math", 0)])
    assert "Alex: all clear" in out.text and out.open_items == 0


def test_plus_more_counts_the_same_rows_as_the_heading():
    items = [ds.item("Alex", f"Task {n}", "do_tonight", due=f"2026-11-{10 + n}") for n in range(1, 9)]
    text = ds.build(ds.summary(("Alex",)), [*items, ds.ixl("Alex", "Math", 5)]).text
    assert "Do tonight (8)" in text and "- +2 more" in text and "IXL: Math 5 remaining" in text


def test_no_records_on_record_header_and_per_kid_wording():
    out = ds.no_records()
    assert out.text.splitlines()[1] == "No assignments on record yet."
    assert "Alex: nothing on record" in out.text and "all clear" not in out.text


@pytest.mark.parametrize("summ", [
    ds.summary(("Alex",), ok=False, records=0), ds.summary(("Alex",))])
def test_no_records_wording_needs_healthy_ingest_and_known_zero_rows(summ):
    text = ds.build(summ, []).text
    assert "on record" not in text


def test_no_records_wording_is_off_when_any_kid_has_rows():
    summ = {**ds.summary(("Alex", "Sam"), records=0), "record_counts": {"Alex": 0, "Sam": 3}}
    text = ds.build(summ, []).text
    assert "No assignments on record yet." not in text and "Alex: all clear" in text


def test_kid_and_source_names_are_cleaned():
    summ = {**ds.summary(("Al\x07ex",), unverified=("bad\x00src",)), "kids": [{"name": "Al\x07ex"}]}
    out = ds.build(summ, [ds.item("Al\x07ex", "Essay", "do_tonight")])
    assert "\nAlex\n" in out.text and "badsrc" in out.text and "\x07" not in out.text and "\x00" not in out.text
    assert out.cards[0]["child"] == "Alex"


def test_cards_are_distributed_round_robin_across_kids_in_roster_order():
    out = ds.many(2, 10)
    assert len(out.cards) == 30
    assert [c["child"] for c in out.cards[:2]] == ["Student00", "Student01"]
    assert Counter(c["child"] for c in out.cards) == {"Student00": 15, "Student01": 15}
    mine = [c["detail"].split(":")[0] for c in out.cards if c["child"] == "Student00"]
    assert mine[:6] == ["Do tonight"] * 6 and mine == sorted(mine, key=["Do tonight", "Waiting on teacher", "Missing"].index)


def test_counts_only_step_truncates_kids_with_a_final_marker_and_keeps_the_header():
    out = ds.many(300, 1)
    text = out.text
    lines = text.splitlines()
    assert len(text.encode()) <= 3500 and lines[0].startswith("Data as of")
    shown = [ln for ln in lines if ln.startswith("Student")]
    assert re.fullmatch(r"\+(\d+) more kids", lines[-1]) and shown
    assert len(shown) + int(lines[-1].split()[0]) == 300


def test_final_text_joins_all_parts_when_they_fit():
    from school_dashboard.assignments import digest_v2
    assert digest_v2.final_text("det", "ai", "notes") == "det\n\nai\n\nnotes"
    assert digest_v2.final_text("det", "", "") == "det"


def test_final_text_drops_the_ai_paragraph_first_by_utf8_bytes():
    from school_dashboard.assignments import digest_v2
    det = "d" * 3480
    ai = "AI summary:\n" + "\u20ac" * 150  # 162 characters but 462 bytes
    notes = "Notes: 1 kid(s) could not be refreshed; showing stored data."
    out = digest_v2.final_text(det, ai, notes)
    assert out == f"{det}\n\n{notes}" and len(out.encode()) <= digest_v2.FINAL_MAX_BYTES == 3900
    assert digest_v2.final_text(det, "x" * 300, notes) == f"{det}\n\n{'x' * 300}\n\n{notes}"


def test_final_text_never_exceeds_the_cap_with_a_full_deterministic_part():
    from school_dashboard.assignments import digest_v2
    det = ds.many(4, 6).text
    out = digest_v2.final_text(det, "\u00e9" * 400, "Notes: 3 kid(s) could not be refreshed; showing stored data.")
    assert len(out.encode()) <= 3900 and out.endswith("stored data.") and "\u00e9" not in out


def test_missing_data_as_of_reads_unknown():
    assert ds.no_as_of().text.startswith("Data as of: unknown")


def test_input_is_not_mutated():
    summ, items = ds.summary(("Alex", "Sam", "Jo")), ds.mixed_items()
    before = copy.deepcopy((summ, items))
    ds.build(summ, items)
    assert (summ, items) == before


def test_result_is_frozen():
    out = ds.mixed()
    with pytest.raises(AttributeError):
        out.text = "x"  # type: ignore[misc]


def test_saturday_drops_do_tonight_because_flags_come_from_the_read_model(tmp_path, monkeypatch):
    api = ds.friday_env(tmp_path, monkeypatch)
    fri, sat = ds.real_digest(api.db, ds.FRI), ds.real_digest(api.db, ds.SAT)
    assert "Do tonight (1)\n- Report (Math 6, due Nov 20)" in fri.text
    assert "Do tonight" not in sat.text
    assert sat.text + "\n" == ds.golden("saturday_missing")
    assert sat.cards[0]["detail"] == "Missing: Math 6, due Nov 20"


def test_pure_module_has_no_forbidden_imports():
    code = (f"import sys; sys.path.insert(0, {str(REPO)!r}); before = set(sys.modules); "
            "import school_dashboard.assignments.digest_v2 as m; "
            "bad = [n for n in ('requests', 'school_dashboard.llm', 'sqlite3') if n in sys.modules and n not in before]; "
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_pure_module_source_imports_only_stdlib_and_clean_text():
    import ast
    tree = ast.parse((REPO / "school_dashboard/assignments/digest_v2.py").read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if "llm" in m or m.split(".")[0] in ("requests", "sqlite3", "os")}
