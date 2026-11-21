"""Builders and golden scenarios for the S7 digest tests. All data is SYNTHETIC (Alex, Sam, Jo)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from recon.support import FIXTURES, local

GOLDEN = FIXTURES / "digest"
NOW = local(2026, 11, 18, 17, 0)
AS_OF = "2026-11-18T16:05:00-05:00"
_BUCKET = {"do_tonight": "do_tonight", "waiting_on_teacher": "waiting_on_teacher", "done_not_received": "needs_action",
           "disputed": "needs_action", "missing": "needs_action"}


def item(kid: str, title: str, flag: str, *, cls: str | None = "Math 6", due: str | None = None,
         kind: str = "assignment", stage: int = 1, info: Sequence[str] = (), unverified: bool = False,
         since: str | None = None, remaining: int | None = None, raw: str | None = None) -> dict[str, Any]:
    status = f"{remaining} remaining" if remaining is not None else raw
    links = [{"id": 1, "source": "ixl", "raw_status": status}] if status is not None else []
    return {"id": 1, "kid": kid, "title": title, "class": cls, "due": due, "kind": kind, "stage": stage,
            "flag": flag, "bucket": _BUCKET.get(flag), "info": list(info), "unverified": unverified,
            "links": links, "stage_since": since or ""}


def ixl(kid: str, subject: str, remaining: int) -> dict[str, Any]:
    return item(kid, f"IXL {subject}", "do_tonight", cls=None, kind="practice", remaining=remaining)


def summary(kids: Sequence[str], *, ok: bool = True, as_of: str | None = AS_OF,
            unverified: Sequence[str] = (), records: int | None = None) -> dict[str, Any]:
    extra = {} if records is None else {"record_counts": {k: records for k in kids}}
    return {**extra, "kids": [{"name": k} for k in kids], "data_as_of": as_of, "ingest_ok": ok,
            "unverified_sources": list(unverified), "warnings": [], "generated_at": NOW.isoformat()}


def build(summ: Mapping[str, Any], items: Sequence[Mapping[str, Any]], mode: str = "night"):
    from school_dashboard.assignments import digest_v2
    return digest_v2.build(summ, items, mode=mode, now=NOW)


def mixed_items() -> list[dict[str, Any]]:
    return [
        item("Alex", "Science lab report", "do_tonight", cls="Science 6", due="2026-11-19", unverified=True),
        item("Alex", "Essay draft", "do_tonight", due="2026-11-19", info=["late"]),
        ixl("Alex", "Math", 4), ixl("Alex", "ELA", 2), ixl("Alex", "Spanish", 0),
        item("Alex", "Poster", "waiting_on_teacher", cls="Art 6", stage=3, since="2026-11-17T15:00:00-05:00",
             info=["cleared_unconfirmed"]),
        item("Alex", "Reading log", "waiting_on_teacher", cls="ELA 6", stage=2, since="2026-11-18T08:00:00-05:00"),
        item("Alex", "Quiz corrections", "disputed", cls="Math 6", stage=3, due="2026-11-16"),
        item("Alex", "Vocabulary sheet", "missing", cls="ELA 6", due="2026-11-13", info=["conflict"]),
        item("Alex", "Map project", "missing", cls="History 6", due="2026-11-12"),
        item("Alex", "Lab safety form", "done_not_received", cls="Science 6", stage=2,
             since="2026-11-10T09:00:00-05:00", due="2026-11-09"),
        item("Jo", "Fractions worksheet", "missing", cls="Math 5", due="2026-11-17"),
    ]


def mixed(mode: str = "night"):
    return build(summary(("Alex", "Sam", "Jo")), mixed_items(), mode)


def all_clear():
    return build(summary(("Alex", "Sam", "Jo")), [])


def all_clear_unverified():
    return build(summary(("Alex", "Sam", "Jo"), ok=False), [])


def banner_sources():
    items = [item("Sam", "Lab sheet", "do_tonight", cls="Science 6", due="2026-11-19", unverified=True),
             item("Sam", "Atlas map", "missing", cls="History 6", due="2026-11-12")]
    return build(summary(("Alex", "Sam"), unverified=("powerschool", "schoology")), items)


def ixl_remaining():
    return build(summary(("Alex", "Sam")), [ixl("Alex", "Math", 3), ixl("Alex", "ELA", 0), ixl("Sam", "ELA", 7)])


def no_records():
    return build(summary(("Alex", "Sam"), records=0), [])


def ixl_unknown():
    items = [ixl("Alex", "Math", 4), ixl("Alex", "Spanish", 0), item("Alex", "IXL ELA", "do_tonight", cls=None,
             kind="practice", raw="not started")]
    return build(summary(("Alex",)), items)


def plus_more():
    items = [item("Alex", f"Task {n}", "do_tonight", due=f"2026-11-{10 + n}") for n in range(1, 10)]
    return build(summary(("Alex",)), items)


def dirty_titles():
    items = [item("Alex", "Bell\x07 ringer\x00 ​work", "do_tonight", due="2026-11-19"),
             item("Alex", "  spaced \n\t out   title  ", "do_tonight", due="2026-11-19"),
             item("Alex", "x" * 10_000, "do_tonight", due="2026-11-19"),
             item("Alex", "\x00\x01", "do_tonight", due="2026-11-19")]
    return build(summary(("Alex",)), items)


def no_as_of():
    return build(summary(("Alex",), as_of=None), [])


def ordering():
    items = [item("Alex", "Zeta", "do_tonight", due="2026-11-20"), item("Alex", "Beta", "do_tonight", due=None),
             item("Alex", "Alpha", "do_tonight", due=None), item("Alex", "Gamma", "do_tonight", due="2026-11-19"),
             item("Jo", "Late one", "missing", due="2026-11-12"), item("Alex", "Early", "missing", due="2026-11-10")]
    return build(summary(("Jo", "Alex")), items)


def many(kids: int, per_bucket: int):
    names = [f"Student{n:02d}" for n in range(kids)]
    items = []
    for kid in names:
        for n in range(per_bucket):
            items += [item(kid, f"Assignment number {n} for the unit", "do_tonight", due=f"2026-11-{10 + n:02d}"),
                      item(kid, f"Waiting item number {n} of the unit", "waiting_on_teacher", stage=3,
                           since="2026-11-17T10:00:00-05:00"),
                      item(kid, f"Missing item number {n} of the unit", "missing", due=f"2026-11-{10 + n:02d}")]
    return build(summary(names), items)


SCENARIOS: dict[str, Callable[[], Any]] = {
    "mixed_night": mixed, "mixed_morning": lambda: mixed("morning"), "all_clear": all_clear,
    "all_clear_unverified": all_clear_unverified, "banner_sources": banner_sources, "ixl_remaining": ixl_remaining,
    "no_records": no_records, "ixl_unknown": ixl_unknown, "plus_more": plus_more, "dirty_titles": dirty_titles, "no_as_of": no_as_of, "ordering": ordering,
    "ladder_cap3": lambda: many(4, 6), "ladder_cap1": lambda: many(9, 5), "ladder_counts": lambda: many(24, 5),
}


def golden(name: str) -> str:
    return (GOLDEN / f"{name}.txt").read_text()


def write_goldens(names: Sequence[str] | None = None) -> None:  # used once by hand to (re)generate fixtures
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in names or SCENARIOS:
        Path(GOLDEN / f"{name}.txt").write_text(SCENARIOS[name]().text + "\n")


FRI = local(2026, 11, 20, 17, 0)
SAT = local(2026, 11, 21, 10, 0)
ROSTER = ("Alex", "Sam", "Jo")


def friday_env(tmp_path, monkeypatch, *, seed=None, **kw):
    """Synthetic recon db with one Alex item due Friday, sync meta fresh as of Friday afternoon."""
    from recon.api_support import make_api, sgy, write_meta
    api = make_api(tmp_path, monkeypatch, seed if seed is not None else [sgy("Report", "Math 6", "s1", due="2026-11-20")],
                   roster=ROSTER, **kw)
    write_meta(api.state, now=FRI)
    return api


def real_digest(db: str, now, mode: str = "night"):
    """The digest from the real read model over the temp db (no recompute)."""
    from school_dashboard.assignments import digest_run, digest_v2, readctx
    with readctx.connection(readonly=True) as conn:
        summ, items = digest_run.assemble(conn, readctx.load(conn, now))
    return digest_v2.build(summ, items, mode=mode, now=now)
