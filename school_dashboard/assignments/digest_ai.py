"""Pure guards for the optional AI paragraph of the v2 digest: the paragraph is kept as is or dropped, never edited.

`violated_rule` names the first rule an answer breaks; `neutralise` removes the list wrapper tags from data before
it is wrapped for the model. No I/O, clock, env or LLM import.
"""
from __future__ import annotations

import re

_URLISH = re.compile(r"(?i)https?:|www\.|\b[\w-]+\.[a-z]{2,}/|@")
_DIGITS = re.compile(r"\d+")
_WORDS = re.compile(r"[A-Za-z]+")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_REASSURANCE = re.compile(r"(?i)all clear|all done|nothing due|nothing to do|nothing is missing|no missing|"
                          r"everything is done|caught up")
_TAGS = re.compile(r"(?i)</?assignment_list>")
MAX_SENTENCES = 2
MIN_NAME_LETTERS = 3


def neutralise(text: str) -> str:
    """`text` without any literal <assignment_list> / </assignment_list> tag (also ones that reform on removal)."""
    while _TAGS.search(text):
        text = _TAGS.sub("", text)
    return text


def _unknown_name(sentences: list[str], known: frozenset[str]) -> bool:
    for sentence in sentences:
        for word in _WORDS.findall(sentence)[1:]:  # the first word of a sentence may be capitalised anyway
            if len(word) >= MIN_NAME_LETTERS and word[0].isupper() and word.casefold() not in known:
                return True
    return False


def violated_rule(answer: str, digest_text: str, *, has_open: bool) -> str | None:
    """Name of the first rule `answer` breaks against the deterministic `digest_text`, or None when it is usable."""
    if _URLISH.search(answer):
        return "url"
    if set(_DIGITS.findall(answer)) - set(_DIGITS.findall(digest_text)):
        return "digits"
    sentences = [s for s in _SENTENCES.split(answer) if s.strip()]
    if _unknown_name(sentences, frozenset(w.casefold() for w in _WORDS.findall(digest_text))):
        return "names"
    if has_open and _REASSURANCE.search(answer):
        return "reassurance"
    if len(sentences) > MAX_SENTENCES:
        return "sentences"
    return None
