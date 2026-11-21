"""S4 review round: score evidence (C1) and safe_url (C3)."""
import pytest

from recon.adapter_support import resolve
from recon.s4_support import NOW
from recon.support import mk_link, mk_state
from school_dashboard.assignments import stages


# ---- C1 ----
def d(score, status="graded", **kw_):
    return stages.portal_decision(mk_state(), [mk_link(norm_status=status, score=score, **kw_)], NOW.date())


@pytest.mark.parametrize("score", ["95", "7/10", "0.5", ".5", "A", "b+", "C-", "D", "p", "Pass", "PASS",
                                   "Score: 8/10", "85%", "10 / 10"])
@pytest.mark.parametrize("status", ["graded", "submitted"])
def test_real_scores_count_on_submitted_or_graded(score, status):
    assert d(score, status) == (4, "portal")


@pytest.mark.parametrize("score", ["0", "0/10", "0%", "0.0", "-/10", "-", "--", "—", "–", "n/a", "N/A",
                                   "na", "missing", "m", "exempt", "excused", "incomplete", "inc", "late", "F",
                                   "f", "", "  ", None, "-5", "E", "x/10"])
def test_non_evidence_scores_do_not_count(score):
    assert d(score)[0] == 3 and d(score, "submitted")[0] == 3


@pytest.mark.parametrize("status", ["late", "missing", "open", "none", "cleared"])
def test_a_score_on_other_statuses_is_never_receipt_evidence(status):
    assert d("9/10", status)[0] != 4


def test_real_score_text_function():
    assert stages._real_score("9/10") and not stages._real_score("0/10") and not stages._real_score(None)


def test_powerschool_late_row_with_zero_score_stays_late():
    from school_dashboard.assignments.adapters.powerschool import PowerSchoolAdapter
    item = {"course": "Math", "title": "T", "due": "2026-11-06", "category": None, "score": "0/10"}
    entry = {"name": "Alpha", "missing": [], "late": [item], "upcoming": [], "warnings": []}
    got = PowerSchoolAdapter().parse({"children": [entry], "warnings": []}, resolve_kid=resolve)
    assert [r.portal_status for r in got.records] == ["late"]


# ---- C3 ----
@pytest.mark.parametrize("url", [
    "https://x.org/a?assignment_id=5", "https://x.org/?monkey=1", "https://x.org/?keyword=1",
    "https://x.org/p?sidebar=1&id=3", "https://x.org/p;version=2", "https://x.org/?authority=1"])
def test_safe_url_keeps_harmless_urls(url):
    from school_dashboard.assignments.readmodel import safe_url
    assert safe_url(url) == url


@pytest.mark.parametrize("url", [
    "https://x.org/?token=a", "https://x.org/?TOKEN=a", "https://x.org/?api_key=a", "https://x.org/?api-key=a",
    "https://x.org/?key=a", "https://x.org/?auth_token=a", "https://x.org/?access-token=a",
    "https://x.org/?session=a", "https://x.org/?sig=a", "https://x.org/?X-Amz-Signature=a",
    "https://x.org/?password=a", "https://x.org/?secret_value=a", "https://x.org/#access_token=abc",
    "https://x.org/a;jsessionid=abc", "https://x.org/a;sessionid=abc/b", "https://x.org/a;token=1",
    "https://u:p@x.org/", "ftp://x.org/", "https://x.org/?sid=1", "javascript:alert(1)"])
def test_safe_url_drops_credential_urls(url):
    from school_dashboard.assignments.readmodel import safe_url
    assert safe_url(url) is None
