"""mailto builder (S8): pure, everything percent-encoded, no header can be added. SYNTHETIC data."""
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

TO = "t.rivera@school.test"


def build(to=TO, subject="Alex - Essay (Math 6)", body="Hello,\nThanks"):
    from school_dashboard.assignments import notes
    return notes.mailto(to, subject, body)


def parts(url):
    s = urlsplit(url)
    assert s.scheme == "mailto" and not s.fragment
    return unquote(s.path), parse_qs(s.query, keep_blank_values=True, strict_parsing=True)


def test_plain_shape():
    to, q = parts(build())
    assert to == TO and q == {"subject": ["Alex - Essay (Math 6)"], "body": ["Hello,\r\nThanks"]}


def test_newlines_are_crlf_encoded():
    url = build(body="a\nb\r\nc")
    assert "%0D%0A" in url and "%0A%0D" not in url and "\n" not in url and "\r" not in url
    assert parts(url)[1]["body"] == ["a\r\nb\r\nc"]


def test_special_characters_are_encoded():
    subject, body = "A & B? #1 100% = ok", "x&y=z? #frag 50% café 中"
    url = build(subject=subject, body=body)
    assert url.isascii() and "#" not in url and " " not in url and url.count("?") == 1 and url.count("&") == 1
    to, q = parts(url)
    assert q["subject"] == [subject] and q["body"] == [body] and set(q) == {"subject", "body"}


@pytest.mark.parametrize("evil", ["x\r\ncc: x@y.org", "x%0Acc: x@y.org", "x?bcc=x@y.org", "x&cc=x@y.org",
                                  "x\nBcc: x@y.org", "x,y@z.org", "x;cc=y@z.org", "\r\n\r\nbody"])
def test_header_injection_cannot_add_headers(evil):
    for url in (build(subject=f"Alex - {evil}", body="ok"), build(subject="ok", body=f"Hello\n{evil}")):
        to, q = parts(url)
        assert set(q) == {"subject", "body"} and to == TO
        assert "\r" not in url and "\n" not in url and url.count("?") == 1 and url.count("&") == 1


@pytest.mark.parametrize("bad", [None, "", "a", "a@b", "@b.org", "a b@c.org", "a@b.org\r\n", "a@b.org\n",
                                 "a@b.org?cc=x@y.org", "a@b.org,c@d.org", "a@b.org;c@d.org", "a%40@b.org",
                                 "a&b@c.org", "a@b.org&cc=c@d.org", "a#b@c.org", "é@b.org", "a@b.org\t"])
def test_bad_addresses_are_refused(bad):
    assert build(to=bad) is None


def test_length_cap_is_1800_total():
    base = len(build(body=""))
    ok = build(body="x" * (1800 - base))
    assert ok is not None and len(ok) == 1800
    assert build(body="x" * (1801 - base)) is None
    assert build(body="\n" * 300) is None  # each newline is six characters once encoded
