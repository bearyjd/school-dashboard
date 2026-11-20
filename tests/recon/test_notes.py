"""Teacher-note templates (S8): pure, deterministic, plain text. All data is SYNTHETIC."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from recon.support import FIXTURES

GOLD = FIXTURES / "notes"
FLAGS = ("disputed", "done_not_received", "missing")
TODAY = date(2026, 11, 18)
MARKED = date(2026, 11, 16)


def make(flag="disputed", *, title="Essay Draft", cls="Math 6", teacher=None, parent="Dana", kid="Alex",
         marked=MARKED):
    from school_dashboard.assignments import notes
    item = notes.NoteItem(title=title, class_name=cls, marked_on=marked)
    return notes.draft(item, flag=flag, parent_name=parent, kid=kid, teacher_name=teacher, today=TODAY)


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("who,teacher", [("teacher", "Ms. Rivera"), ("noteacher", None)])
def test_golden_body(flag, who, teacher):
    want = (GOLD / f"{flag}_{who}.txt").read_text().rstrip("\n")
    assert make(flag, teacher=teacher).body == want


@pytest.mark.parametrize("flag", FLAGS)
def test_subject_and_greeting(flag):
    d = make(flag, teacher="Ms. Rivera")
    assert d.subject == "Alex - Essay Draft (Math 6)" and d.body.startswith("Hello Ms. Rivera,\n")
    assert make(flag, cls=None).subject == "Alex - Essay Draft"
    assert make(flag, teacher="  ").body.startswith("Hello,\n")


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("title", ["Essay Draft", "URGENT!!! see http://evil.test/x?a=1 and www.evil.test",
                                   "mail me: a@b.org https://x.test", "x" * 500])
def test_body_has_no_url_address_bang_or_urgent(flag, title):
    d = make(flag, title=title, teacher="Ms. Rivera")
    for text in (d.body, d.subject):
        low = text.lower()
        assert "http" not in low and "www." not in low and "@" not in text and "!" not in text
        assert "urgent" not in low and "\r" not in text


@pytest.mark.parametrize("flag", FLAGS)
def test_dirty_values_are_cleaned_and_capped(flag):
    d = make(flag, title="Es\x00say\r\n  Draft‮" + "z" * 400, cls="Ma\tth\n6", parent="Da\x07na\r\n",
             kid="Al\x00ex\n", teacher="Ms.\r\nRivera")
    assert "\r" not in d.body and "\x00" not in d.body and "‮" not in d.body
    assert d.body.startswith("Hello Ms. Rivera,\n") and d.body.endswith("Thank you,\nDana")
    assert d.subject.startswith("Alex - Essay Draft") and "\n" not in d.subject
    assert len(d.subject) <= len("Alex - ") + 120 + len(" (Math 6)")


@pytest.mark.parametrize("flag", FLAGS)
def test_missing_date_and_empty_title_degrade_gracefully(flag):
    d = make(flag, marked=None, title="http://x.test")
    assert "None" not in d.body and d.body.count("\n") >= 5 and "assignment" in d.subject


@pytest.mark.parametrize("flag", FLAGS)
def test_deterministic_short_and_unchanged_inputs(flag):
    a, b = make(flag, teacher="Ms. Rivera"), make(flag, teacher="Ms. Rivera")
    assert a == b
    lines = [ln for ln in a.body.splitlines() if ln]
    assert 4 <= len(lines) <= 7
    with pytest.raises(FrozenInstanceError):
        setattr(a, "body", "x")


def test_unknown_flag_is_rejected():
    for flag in ("upcoming", "do_tonight", "waiting_on_teacher", "", "nope"):
        with pytest.raises(ValueError):
            make(flag)
