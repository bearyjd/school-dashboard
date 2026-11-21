"""normalize.py: title_key and class_key."""
import pytest

from school_dashboard.assignments.normalize import class_key, title_key


@pytest.mark.parametrize("raw,expected", [
    ("Chapter 5 Quiz", "chapter 5 quiz"),
    ("CHAPTER 5 QUIZ", "chapter 5 quiz"),
    ("Ch. 5: Quiz!", "ch 5 quiz"),
    ("  Ch   5 \t Quiz  ", "ch 5 quiz"),
    ("Ｑｕｉｚ １", "quiz 1"),      # NFKC: fullwidth letters and digit
    ("Straße", "strasse"),                           # casefold, not lower
    ("Don't stop", "dont stop"),                          # punctuation removed, not replaced by a space
    ("", ""),
])
def test_title_key_normalises(raw, expected):
    assert title_key(raw) == expected


def test_title_key_is_idempotent():
    once = title_key("Ch. 5: Quiz!")
    assert title_key(once) == once


@pytest.mark.parametrize("name", [None, "", "   "])
def test_class_key_blank_or_none_is_empty(name):
    assert class_key(name) == ""


def test_class_key_normalises_like_titles():
    assert class_key("  Math, 6 ") == "math 6"


def test_class_key_applies_alias_after_normalising_the_name():
    assert class_key("MATHEMATICS Grade 6", {"mathematics grade 6": "math 6"}) == "math 6"


def test_class_key_alias_keys_are_compared_normalised():
    assert class_key("math6", {"Math-6!": "algebra"}) == "algebra"


def test_class_key_alias_values_are_normalised():
    assert class_key("pre-algebra", {"pre-algebra": "Math 6."}) == "math 6"


def test_class_key_without_matching_alias_returns_normalised_name():
    assert class_key("Science 6", {"math": "x"}) == "science 6"


def test_class_key_default_aliases_are_not_shared_state():
    class_key("Math", {"math": "x"})
    assert class_key("Math") == "math"
