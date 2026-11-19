"""Merge-quality report: pure builders over rows, the thin reader, redaction and the CLI."""
import json

from recon.ingest_support import WED
from recon.report_support import SYNTH_CLASSES, SYNTH_TITLES, cli, db_with, ps, sgy

OK_META = {"recon_ingest": {"last_run": WED.isoformat(), "last_ok": WED.isoformat(), "last_result": "ok"}}


def _rep(db_path, meta=None):
    from school_dashboard.assignments import report
    return report.run(db_path, now=WED, meta=OK_META if meta is None else meta)


def _pairs(rep):
    return rep["unmatched_pairs"]


def test_merged_assignment_counts_as_multi_source_and_is_not_a_pair(tmp_path):
    both = sgy("Essay Draft", "Language Arts", "s1")
    both["links"].append({"source": "powerschool", "external_key": "p1", "norm_status": "missing"})
    rep = _rep(db_with(tmp_path, [both]))
    kid = rep["kids"]["Alex"]
    assert kid["assignments"] == 1 and kid["multi_source"] == 1 and kid["merge_rate"] == 1.0
    assert kid["sources"]["schoology"]["links"] == 1 and kid["sources"]["powerschool"]["links"] == 1
    assert _pairs(rep) == [] and rep["alias_suggestions"] == []


def test_same_title_different_class_is_a_pair_with_suggested_alias(tmp_path):
    rep = _rep(db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")]))
    assert len(_pairs(rep)) == 1
    pair = _pairs(rep)[0]
    assert pair["kid"] == "Alex" and pair["due"] == "2026-11-20" and pair["ratio"] == 1.0
    assert {pair["title_a"], pair["title_b"]} == {"Essay Draft"}
    assert {pair["class_a"], pair["class_b"]} == {"Language Arts", "ELA"}
    assert {pair["suggested_alias"]["from"], pair["suggested_alias"]["to"]} == {"Language Arts", "ELA"}
    assert rep["alias_suggestions"][0]["count"] == 1


def test_similar_titles_same_class_are_a_pair_without_alias(tmp_path):
    rep = _rep(db_with(tmp_path, [sgy("Chapter 4 Quiz", "Math 6", "s1"),
                                  ps("Chapter 4 Quiz Review", "Math 6", "p1")]))
    assert len(_pairs(rep)) == 1 and _pairs(rep)[0]["ratio"] >= 0.6
    assert _pairs(rep)[0]["suggested_alias"] is None and rep["alias_suggestions"] == []


def test_non_pairs_are_not_listed(tmp_path):
    seed = [sgy("Essay Draft", "Language Arts", "s1"),
            ps("Essay Draft", "ELA", "p1", due="2026-11-21"),            # different due date
            sgy("Essay Draft", "ELA", "s2"),                             # same source as the first
            ps("Volcano Poster", "Math 6", "p2")]                         # titles too different
    rep = _rep(db_with(tmp_path, seed))
    # only (s1 Language Arts, p2?) no: ratio too low; (s2, p2) too low; s1/s2 vs p1 differ in due
    assert _pairs(rep) == []


def test_alias_suggestions_aggregate_and_sort(tmp_path):
    seed = [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1"),
            sgy("Volcano Poster", "Language Arts", "s2", due="2026-11-22"),
            ps("Volcano Poster", "ELA", "p2", due="2026-11-22"),
            sgy("Chapter 4 Quiz", "Math 6", "s3", due="2026-11-23"),
            ps("Chapter 4 Quiz", "Math Six", "p3", due="2026-11-23")]
    sugg = _rep(db_with(tmp_path, seed))["alias_suggestions"]
    assert [s["count"] for s in sugg] == [2, 1]
    assert sugg[0]["kid"] == "Alex" and {sugg[0]["class_a"], sugg[0]["class_b"]} == {"Language Arts", "ELA"}


def test_per_kid_flags_stages_evidence_duplicates_and_outbox(tmp_path):
    from school_dashboard.assignments import repo, schema
    seed = [sgy("Essay Draft", "Language Arts", "s1"),
            ps("Volcano Poster", "Math 6", "p1", status="missing", stage=2),
            ps("Chapter 4 Quiz", "Math 6", "p2", status="open"),
            sgy("Chapter 4 Quiz Review", "Math 6", "s2", stage=4)]
    path = db_with(tmp_path, seed)
    conn = schema.connect(path)
    with repo.transaction(conn):
        conn.execute("UPDATE assignments SET flag = 'missing' WHERE title = 'Volcano Poster'")
        conn.execute("UPDATE assignments SET flag = 'upcoming' WHERE title = 'Essay Draft'")
        aid = conn.execute("SELECT id FROM assignments WHERE title = 'Volcano Poster'").fetchone()[0]
        repo.append_event(conn, aid, at=WED, setter="portal", kind="merge", detail="possible_duplicate:1,2")
        repo.enqueue_push(conn, aid, "missing", WED, "transition")
        repo.enqueue_push(conn, aid, "disputed", WED, "baseline")
    conn.close()
    kid = _rep(path)["kids"]["Alex"]
    assert kid["flags"] == {"missing": 1, "upcoming": 1}
    assert kid["stages"] == {"1": 2, "2": 1, "4": 1}
    assert kid["possible_duplicates"] == 1
    # open = 3; covered: schoology link (Essay), powerschool missing link (Poster); PS open Quiz is not
    assert kid["evidence_coverage"] == round(2 / 3, 3)
    assert _rep(path)["outbox"] == {"pending": 1, "suppressed": 1}


def test_health_is_reported_and_missing_ingest_is_not_ok(tmp_path):
    path = db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1")])
    assert _rep(path)["health"]["ok"] is True
    assert _rep(path)["health"]["data_as_of"] == WED.isoformat()
    bad = _rep(path, meta={"recon_ingest": {"last_run": WED.isoformat(), "last_result": "error"}})["health"]
    assert bad["ok"] is False and bad["issue"] == "error"


def test_redaction_hides_titles_and_classes_and_is_consistent_within_one_report(tmp_path):
    from school_dashboard.assignments import report
    path = db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    redacted = report.redact(_rep(path))
    text = json.dumps(redacted) + report.render_text(redacted)
    for secret in (*SYNTH_TITLES, *SYNTH_CLASSES, "essay draft", "language arts"):
        assert secret not in text, secret
    again = report.redact(_rep(path))
    assert again != redacted  # random per-run salt: hashes are not comparable across reports
    pair = redacted["unmatched_pairs"][0]
    assert pair["title_a"] == pair["title_b"] and pair["class_a"] != pair["class_b"]
    assert pair["suggested_alias"] is not None


def test_text_render_shows_titles_for_the_operator(tmp_path):
    from school_dashboard.assignments import report
    path = db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    text = report.render_text(_rep(path))
    assert "Essay Draft" in text and "Alex" in text


def test_input_is_not_mutated_by_redact(tmp_path):
    from school_dashboard.assignments import report
    path = db_with(tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    rep = _rep(path)
    snapshot = json.dumps(rep, sort_keys=True)
    report.redact(rep)
    assert json.dumps(rep, sort_keys=True) == snapshot


# ---- CLI ------------------------------------------------------------------

def _state_db(state_env, tmp_path, seed):
    return db_with(state_env, seed)


def test_cli_json_report_is_stdout_only_and_stderr_has_counts_only(state_env, tmp_path):
    _state_db(state_env, tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    r = cli(state_env, "report", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["unmatched_pairs"][0]["title_a"] == "Essay Draft"
    assert "Essay" not in r.stderr and "ELA" not in r.stderr


def test_cli_text_report_and_redact(state_env, tmp_path):
    _state_db(state_env, tmp_path, [sgy("Essay Draft", "Language Arts", "s1"), ps("Essay Draft", "ELA", "p1")])
    plain = cli(state_env, "report")
    assert plain.returncode == 0 and "Essay Draft" in plain.stdout
    red = cli(state_env, "report", "--redact")
    assert red.returncode == 0 and "Essay" not in red.stdout + red.stderr and "ELA" not in red.stdout
    red_json = cli(state_env, "report", "--json", "--redact")
    assert "Essay" not in red_json.stdout and "Language" not in red_json.stdout


def test_cli_report_not_migrated_exits_3(state_env):
    assert cli(state_env, "report").returncode == 3


def test_cli_report_is_read_only(state_env, tmp_path):
    from recon.conftest import table_sha
    path = _state_db(state_env, tmp_path, [sgy("Essay Draft", "Language Arts", "s1")])
    before = table_sha(path, "assignments"), table_sha(path, "source_links")
    assert cli(state_env, "report").returncode == 0
    assert (table_sha(path, "assignments"), table_sha(path, "source_links")) == before
