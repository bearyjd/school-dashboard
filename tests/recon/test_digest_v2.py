"""Pure v2 digest builder (S7): golden texts, ladder, ordering, purity."""
import copy
import subprocess
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
    code = (f"import sys; sys.path.insert(0, {str(REPO)!r}); import school_dashboard.assignments.digest_v2 as m; "
            "bad = [n for n in ('requests', 'school_dashboard.llm', 'sqlite3') if n in sys.modules]; "
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
