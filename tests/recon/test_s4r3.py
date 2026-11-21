"""Host allowlist review round: safe_url tuning and the debounced marker."""
from collections import Counter
from datetime import timedelta

import pytest

from recon.report_support import sgy
from recon.s4_support import NOW, open_conn
from school_dashboard.assignments import readmodel

BASE = "https://portal.example.com/x"


@pytest.mark.parametrize("suffix", [
    "?course_code=A1", "?school_code=9", "?country_code=US", "?promo-code=X", "?filter[status]=open",
    "?filter[kind][]=a", "?pwn=1", "?tickets=3", "?code_of_conduct=1", "?class_code=7", "?section_code=2",
    "?zip_code=1", "?postal_code=1", "?area_code=1", "?language_code=en", "?currency_code=USD", "?promo_code=X",
    "?barcode=1", "?zipcode=1", "?unicode=1", "?postcode=1", "?encode=1"])
def test_over_blocking_cases_pass(suffix):
    assert readmodel.safe_url(BASE + suffix) == BASE + suffix


@pytest.mark.parametrize("suffix", [
    "?reset_code=1", "?verification_code=1", "?invite_code=1", "?login_code=1", "?magic_code=1", "?oauth_code=1",
    "?2fa_code=1", "?my-code=1", "?x[reset_code]=1", "?Reset_Code=1", "?code=1", "?auth_code=1", "?otp_code=1", "?access_code=1", "?pass_code=1", "?passcode=1",
    "?user[token]=a", "?x[password]=a", "?x[api_key]=a", "?x%5Bpasswd%5D=a", "?passwd=1", "?pw=1", "?ticket=1",
    "?user_pw=1", "?a[b][secret]=1", "#pw=1", "/f;ticket=1"])
def test_under_blocking_cases_are_dropped(suffix):
    assert readmodel.safe_url(BASE + suffix) is None


def _ctx(c, now):
    from school_dashboard.assignments import ingest_kid
    return ingest_kid.build_context(c, "Alex", now=now, no_school_rows=[], baseline=False, file_hashes={})


def test_debounced_counter_only_counts_inserted_rows(tmp_path):
    from school_dashboard.assignments import ingest_status
    c = open_conn(tmp_path, [sgy("Essay", "Math 6", "s1")])
    counts: Counter[str] = Counter()
    ingest_status.enqueue_debounced(c, _ctx(c, NOW), 1, "disputed", counts)
    later = _ctx(c, NOW + timedelta(hours=1))
    ingest_status.enqueue_debounced(c, later, 1, "disputed", counts)
    ingest_status.enqueue_debounced(c, later, 1, "disputed", counts)  # same timestamp: row ignored
    assert counts["pushes_enqueued"] == 1 and counts["pushes_debounced"] == 1


def test_debounced_marker_is_one_named_constant():
    from school_dashboard.assignments import repo
    assert repo.DEBOUNCED == "debounced"
    import pathlib
    root = pathlib.Path(repo.__file__).parent
    offenders = [p.name for p in root.glob("*.py") if p.name != "repo.py" and "'debounced'" in p.read_text()]
    assert offenders == []
    assert "'debounced'" not in pathlib.Path(repo.__file__).read_text()
