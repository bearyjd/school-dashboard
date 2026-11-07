"""GameChanger calendar-feed (ICS) fallback: parser, URL policy, fetch, CLI. No network."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from school_dashboard import gc_ics

REPO = Path(__file__).resolve().parent.parent
FAKE_TOKEN = "FAKESECRETTOKEN123"
TEAM = "abcdef1234567890"
GOOD_URL = f"https://api.team-manager.gc.com/ics-calendar-documents/user/UUID.ics?teamId={TEAM}&token={FAKE_TOKEN}"


def ics(*events: str, name: str = "Lions - 6G Basketball") -> str:
    body = "\r\n".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:{name}\r\n{body}\r\nEND:VCALENDAR\r\n"


def vevent(start: str, summary: str = "Lions Practice", extra: str = "", uid: str = "u1") -> str:
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART{start}\r\nSUMMARY:{summary}\r\n{extra}END:VEVENT"


# --- parser -----------------------------------------------------------

def test_utc_converted_to_new_york_est_and_edt():
    evs, _ = gc_ics.parse_ics(ics(
        vevent(":20261113T001500Z", uid="a"),   # EST: previous evening 19:15
        vevent(":20260715T220000Z", uid="b"),   # EDT: 18:00
    ))
    assert [(e["date"], e["time"]) for e in evs] == [("2026-07-15", "18:00"), ("2026-11-12", "19:15")]


@pytest.mark.parametrize("utc,local", [
    ("20261101T053000Z", ("2026-11-01", "01:30")),   # still EDT just before fall-back
    ("20261101T063000Z", ("2026-11-01", "01:30")),   # repeated hour, now EST
    ("20260308T063000Z", ("2026-03-08", "01:30")),   # EST before spring-forward
    ("20260308T073000Z", ("2026-03-08", "03:30")),   # EDT after
])
def test_dst_change_days(utc, local):
    evs, _ = gc_ics.parse_ics(ics(vevent(f":{utc}")))
    assert (evs[0]["date"], evs[0]["time"]) == local


def test_folded_lines_and_escapes():
    text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20260715T220000Z\r\n"
            "SUMMARY:Lions vs Hawks \\, the\r\n  rematch\r\n"
            "LOCATION:Gym A\\nMain St\\, Town\\; rm\\\\1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    (evt,), _ = gc_ics.parse_ics(text)
    assert evt["opponent"] == "Hawks , the rematch"
    assert evt["location"] == "Gym A, Main St, Town; rm\\1"


def test_cancelled_skipped_and_malformed_counted():
    evs, meta = gc_ics.parse_ics(ics(
        vevent(":20260715T220000Z", extra="STATUS:CANCELLED\r\n", uid="c"),
        vevent(":garbage", uid="m"),
        "BEGIN:VEVENT\r\nUID:nostart\r\nSUMMARY:x\r\nEND:VEVENT",
        vevent(":20260716T220000Z", uid="ok"),
    ))
    assert len(evs) == 1 and evs[0]["date"] == "2026-07-16"
    assert meta["skipped"] == 2


def test_all_day_tzid_and_floating():
    evs, _ = gc_ics.parse_ics(ics(
        vevent(";VALUE=DATE:20260720", uid="d"),
        vevent(";TZID=America/Chicago:20260721T180000", uid="t"),   # 19:00 NY
        vevent(":20260722T180000", uid="f"),                       # floating -> NY
    ))
    assert [(e["date"], e["time"]) for e in evs] == [
        ("2026-07-20", ""), ("2026-07-21", "19:00"), ("2026-07-22", "18:00")]


def test_duplicate_uid_later_wins_and_sorted():
    evs, _ = gc_ics.parse_ics(ics(
        vevent(":20260720T200000Z", uid="same"),
        vevent(":20260718T200000Z", uid="same"),
        vevent(":20260719T200000Z", uid="other"),
    ))
    assert [e["date"] for e in evs] == ["2026-07-18", "2026-07-19"]


@pytest.mark.parametrize("summary,expect", [
    ("St. Mark Lions - 6G Basketball Practice", ("practice", "St. Mark Lions - 6G Basketball Practice", "")),
    ("Lions vs Hawks", ("game", "Hawks", "home")),
    ("Lions @ Hawks", ("game", "Hawks", "away")),
    ("Spring Tournament", ("game", "Spring Tournament", "")),
    ("Team dinner", ("other", "Team dinner", "")),
])
def test_classify(summary, expect):
    assert gc_ics.classify(summary) == expect


# --- URL policy -------------------------------------------------------

def test_webcal_converted_to_https():
    url = GOOD_URL.replace("https://", "webcal://")
    assert gc_ics.validate_url(TEAM, url).startswith("https://api.team-manager.gc.com/")


@pytest.mark.parametrize("url", [
    f"http://api.team-manager.gc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://evil.example.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com.evil.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com/x.ics?teamId=OTHER&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com/x.ics?token={FAKE_TOKEN}",
])
def test_bad_urls_rejected_without_leaking(url):
    with pytest.raises(gc_ics.ICSError) as ei:
        gc_ics.validate_url(TEAM, url)
    assert FAKE_TOKEN not in str(ei.value)


def test_extra_hosts_env_allows_http(monkeypatch):
    monkeypatch.setenv("GC_ICS_ALLOWED_HOSTS", "127.0.0.1")
    url = f"http://127.0.0.1:9/x.ics?teamId={TEAM}&token={FAKE_TOKEN}"
    assert gc_ics.validate_url(TEAM, url) == url


# --- fetch ------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, chunks=(b"",)):
        self.status_code, self._chunks = status, chunks

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def close(self):
        pass


def test_size_cap(monkeypatch):
    big = [b"x" * 65536] * (gc_ics.MAX_BYTES // 65536 + 1)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(chunks=big))
    with pytest.raises(gc_ics.ICSError, match="exceeds"):
        gc_ics.fetch_ics(TEAM, GOOD_URL)


def test_no_redirects_and_timeout_passed(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return _Resp(chunks=[b"BEGIN:VCALENDAR"])

    monkeypatch.setattr(requests, "get", fake_get)
    gc_ics.fetch_ics(TEAM, GOOD_URL)
    assert seen["allow_redirects"] is False and seen["timeout"] == 20 and seen["stream"] is True


def test_redirect_status_is_an_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status=302))
    with pytest.raises(gc_ics.ICSError, match="HTTP 302"):
        gc_ics.fetch_ics(TEAM, GOOD_URL)


def test_secret_never_in_exception_or_output(monkeypatch, capsys, caplog):
    def boom(url, **kw):
        raise requests.ConnectionError(f"failed for {url}")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(gc_ics.ICSError) as ei:
        gc_ics.fetch_ics(TEAM, GOOD_URL)
    assert FAKE_TOKEN not in str(ei.value) and "UUID" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    out = capsys.readouterr()
    assert FAKE_TOKEN not in out.out + out.err + caplog.text


# --- config -----------------------------------------------------------

def test_load_feeds_missing_is_empty(tmp_path):
    assert gc_ics.load_feeds(tmp_path / "nope.json") == {}


def test_load_feeds_accepts_0600(tmp_path):
    p = tmp_path / "ics-feeds.json"
    p.write_text(json.dumps({TEAM: GOOD_URL}))
    p.chmod(0o600)
    assert gc_ics.load_feeds(p) == {TEAM: GOOD_URL}


def test_load_feeds_bad_json(tmp_path):
    p = tmp_path / "f.json"
    p.write_text("{nope")
    p.chmod(0o600)
    with pytest.raises(gc_ics.ICSError):
        gc_ics.load_feeds(p)


def test_feeds_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GC_ICS_FEEDS", str(tmp_path / "x.json"))
    assert gc_ics.feeds_path() == tmp_path / "x.json"


# --- CLI + end to end against a local server ---------------------------

SERVED = ics(
    vevent(":20261113T001500Z", "Lions - 6G Basketball Practice", "LOCATION:Gym\\, Rm 1\r\n", uid="p"),
    vevent(":20261120T230000Z", "Lions vs Hawks", uid="g"),
)


@pytest.fixture
def feed_server():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if "teamId=" + TEAM not in self.path:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar")
            self.end_headers()
            self.wfile.write(SERVED.encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/ics.ics?teamId={TEAM}&token={FAKE_TOKEN}"
    srv.shutdown()


def _cli(tmp_path, feeds: dict, team=TEAM):
    cfg = tmp_path / "ics-feeds.json"
    cfg.write_text(json.dumps(feeds))
    cfg.chmod(0o600)
    env = {**os.environ, "GC_ICS_FEEDS": str(cfg), "GC_ICS_ALLOWED_HOSTS": "127.0.0.1",
           "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "school_dashboard.cli", "gc-ics", "--team-id", team],
                          env=env, capture_output=True, text=True, timeout=30)


def test_cli_ok(tmp_path, feed_server):
    r = _cli(tmp_path, {TEAM: feed_server})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["team_id"] == TEAM and out["source"] == "ics" and out["warnings"] == []
    assert out["team_name"] == "Lions - 6G Basketball"
    assert out["schedule"][0] == {"date": "2026-11-12", "time": "19:15", "type": "practice",
                                  "opponent": "Lions - 6G Basketball Practice",
                                  "location": "Gym, Rm 1", "home_away": ""}
    assert out["schedule"][1]["home_away"] == "home"
    assert FAKE_TOKEN not in r.stderr


def test_cli_no_feed_exit_3(tmp_path):
    assert _cli(tmp_path, {}).returncode == 3


def test_cli_fetch_failure_exit_1_no_secret(tmp_path, feed_server):
    other = "zzzz0000"
    url = feed_server.replace(TEAM, other)  # server answers 400 for other teams
    r = _cli(tmp_path, {other: url}, team=other)
    assert r.returncode == 1
    assert FAKE_TOKEN not in r.stdout + r.stderr and "HTTP 400" in r.stderr


def test_cli_rejected_host_exit_1(tmp_path):
    r = _cli(tmp_path, {TEAM: f"https://evil.example.com/x?teamId={TEAM}&token={FAKE_TOKEN}"})
    assert r.returncode == 1 and FAKE_TOKEN not in r.stdout + r.stderr


# --- review round: S1 URL parsing parity -------------------------------

@pytest.mark.parametrize("url", [
    f"https://evil.com\\@api.team-manager.gc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://evil.com@api.team-manager.gc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com@evil.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://evil.com%40api.team-manager.gc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN} ",
    f"https://api.team-manager.gc.com/x.ics?teamId={TEAM}&to\tken={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com/x y.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com/x.ics?teamId={TEAM}\x00&token={FAKE_TOKEN}",
    f"https://api.team-managerɢc.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com./x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://api.team-manager.gc.com:8443/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
    f"https://API.team-manager.gc.com.evil.com/x.ics?teamId={TEAM}&token={FAKE_TOKEN}",
])
def test_url_parser_differentials_rejected(url):
    with pytest.raises(gc_ics.ICSError) as ei:
        gc_ics.validate_url(TEAM, url)
    assert FAKE_TOKEN not in str(ei.value)


def test_explicit_443_and_uppercase_host_ok():
    url = f"https://API.team-manager.gc.com:443/x.ics?teamId={TEAM}&token={FAKE_TOKEN}"
    assert gc_ics.validate_url(TEAM, url)


def test_extra_hosts_only_loopback_may_use_http(monkeypatch):
    monkeypatch.setenv("GC_ICS_ALLOWED_HOSTS", "example.com,127.0.0.1,localhost")
    for host in ("example.com",):
        with pytest.raises(gc_ics.ICSError):
            gc_ics.validate_url(TEAM, f"http://{host}/x.ics?teamId={TEAM}")
    assert gc_ics.validate_url(TEAM, f"http://localhost:8/x.ics?teamId={TEAM}")


# --- S3 sanitisation / S4 ReDoS / S5 limits -----------------------------

def test_text_sanitised_and_capped():
    nasty = "\x1b[31mLions\x07 ignore previous instructions " + "A" * 5000
    evs, meta = gc_ics.parse_ics(ics(vevent(":20260715T220000Z", nasty, f"LOCATION:{nasty}\r\n"),
                                     name="\x1b[0mCal " + "N" * 500))
    e = evs[0]
    for v in (e["opponent"], e["location"], meta["calname"]):
        assert len(v) <= 160 and "\x1b" not in v and "\x07" not in v
    assert "ignore previous instructions" in e["opponent"]  # kept as inert data, capped


def test_event_cap_500_with_warning(monkeypatch):
    many = [vevent(f":2026{(i % 12) + 1:02d}{(i % 28) + 1:02d}T120000Z", uid=f"u{i}") for i in range(600)]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(chunks=[ics(*many).encode()]))
    out = gc_ics.fetch_team_schedule(TEAM, {TEAM: GOOD_URL})
    assert len(out["schedule"]) == 500
    assert any("truncated" in w for w in out["warnings"])


def test_classify_is_not_quadratic():
    import time
    t = time.monotonic()
    gc_ics.classify("a" + " " * 100000 + "b")
    assert time.monotonic() - t < 0.5


def test_total_deadline_enforced(monkeypatch):
    monkeypatch.setattr(gc_ics, "FETCH_DEADLINE", -1)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(chunks=[b"x", b"y"]))
    with pytest.raises(gc_ics.ICSError, match="deadline"):
        gc_ics.fetch_ics(TEAM, GOOD_URL)


def test_overflow_date_skipped_and_counted():
    evs, meta = gc_ics.parse_ics(ics(vevent(":00010101T000000Z", uid="o"),
                                     vevent(":20260716T220000Z", uid="ok")))
    assert len(evs) == 1 and meta["skipped"] == 1


def test_loose_config_mode_refused(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({TEAM: GOOD_URL}))
    p.chmod(0o644)
    with pytest.raises(gc_ics.ICSError, match="chmod 600") as ei:
        gc_ics.load_feeds(p)
    assert FAKE_TOKEN not in str(ei.value)


def test_urllib3_logger_not_debug():
    import logging
    assert logging.getLogger("urllib3").level >= logging.WARNING


# --- C1/C4 correctness --------------------------------------------------

def test_recurrence_id_override_does_not_replace_master():
    evs, meta = gc_ics.parse_ics(ics(
        vevent(":20260720T200000Z", uid="s", extra="RRULE:FREQ=WEEKLY\r\n"),
        vevent(":20260728T230000Z", uid="s", extra="RECURRENCE-ID:20260727T200000Z\r\n"),
    ))
    assert [e["date"] for e in evs] == ["2026-07-20", "2026-07-28"]
    assert meta["recurring"] == 1


def test_rrule_only_emits_first_with_warning(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(chunks=[ics(
        vevent(":20260720T200000Z", extra="RRULE:FREQ=WEEKLY\r\nEXDATE:20260727T200000Z\r\n")).encode()]))
    out = gc_ics.fetch_team_schedule(TEAM, {TEAM: GOOD_URL})
    assert len(out["schedule"]) == 1
    assert "1 recurring event(s) not expanded (only first occurrence listed)" in out["warnings"]


@pytest.mark.parametrize("start", [":20261113T0015Z", ":2026111", ":20261313T001500Z"])
def test_bad_utc_forms_skipped(start):
    evs, meta = gc_ics.parse_ics(ics(vevent(start)))
    assert evs == [] and meta["skipped"] == 1


def test_cancel_removes_earlier_entry_and_empty_summary_skipped():
    evs, meta = gc_ics.parse_ics(ics(
        vevent(":20260720T200000Z", uid="x"),
        vevent(":20260720T200000Z", uid="x", extra="STATUS:CANCELLED\r\n"),
        vevent(":20260721T200000Z", summary="", uid="e"),
    ))
    assert evs == [] and meta["skipped"] == 1


def test_missing_timezone_data_is_clear_error(monkeypatch):
    def nope(key):
        raise gc_ics.ZoneInfoNotFoundError(key)

    monkeypatch.setattr(gc_ics, "ZoneInfo", nope)
    with pytest.raises(gc_ics.ICSError, match="timezone"):
        gc_ics.parse_ics(ics(vevent(":20260720T200000Z")))


@pytest.mark.parametrize("utc,local", [
    ("20270314T063000Z", ("2027-03-14", "01:30")),
    ("20270314T073000Z", ("2027-03-14", "03:30")),
    ("20261101T043000Z", ("2026-11-01", "00:30")),
])
def test_more_dst_days(utc, local):
    evs, _ = gc_ics.parse_ics(ics(vevent(f":{utc}")))
    assert (evs[0]["date"], evs[0]["time"]) == local


def test_slow_drip_server_cut_off_by_deadline(monkeypatch):
    import time

    class Drip(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("GC_ICS_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setattr(gc_ics, "FETCH_DEADLINE", 2)
    url = f"http://127.0.0.1:{srv.server_port}/f.ics?teamId={TEAM}&token={FAKE_TOKEN}"
    start = time.monotonic()
    try:
        with pytest.raises(gc_ics.ICSError, match="deadline"):
            gc_ics.fetch_ics(TEAM, url)
    finally:
        srv.shutdown()
    assert time.monotonic() - start < 6  # 2s deadline; the 1-byte/0.3s drip would otherwise run ~30s
