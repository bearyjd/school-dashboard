"""Text rendering and redaction for the merge-quality report (pure; inputs are never mutated)."""
from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping

from .adapters.base import clean_text

_NAME_FIELDS = ("title_a", "title_b", "class_a", "class_b")


_PRINT_CAP = 400


def _hash(prefix: str, text: str | None, salt: bytes) -> str | None:
    if text is None:
        return None
    return f"{prefix}:{hashlib.sha256(salt + text.strip().casefold().encode('utf-8')).hexdigest()[:8]}"


def _hash_pair(pair: Mapping[str, Any], salt: bytes) -> dict[str, Any]:
    out = dict(pair)
    for name in _NAME_FIELDS:
        out[name] = _hash("t" if name.startswith("title") else "c", pair[name], salt)
    alias = pair["suggested_alias"]
    out["suggested_alias"] = None if alias is None else {k: _hash("c", v, salt) for k, v in alias.items()}
    return out


def redact(report: Mapping[str, Any], salt: bytes | None = None) -> dict[str, Any]:
    """Copy of the report with titles and class names replaced by short hashes.

    The salt is random per call: hashes agree within one report but not across reports, and cannot be
    reversed with a dictionary of likely titles."""
    key = os.urandom(16) if salt is None else salt
    out = dict(report)
    out["unmatched_pairs"] = [_hash_pair(p, key) for p in report["unmatched_pairs"]]
    out["alias_suggestions"] = [{**s, "class_a": _hash("c", s["class_a"], key), "class_b": _hash("c", s["class_b"], key)}
                                for s in report["alias_suggestions"]]
    return out


def _c(value: Any) -> str:
    """Printable form of any untrusted value: control and format characters gone, bounded."""
    return clean_text(str(value), _PRINT_CAP)


def _kid_lines(kid: str, stat: Mapping[str, Any]) -> list[str]:
    lines = [f"{_c(kid)}: {stat['assignments']} assignments, {stat['multi_source']} in 2+ sources "
             f"(merge rate {stat['merge_rate']:.0%}), {stat['possible_duplicates']} possible duplicates"]
    lines += [f"  {_c(src)}: {s['links']} links ({s['live']} live)" for src, s in sorted(stat["sources"].items())]
    lines.append(f"  flags: {stat['flags']}  stages: {stat['stages']}")
    cover = stat["evidence_coverage"]
    lines.append("  received-evidence coverage: " + ("n/a" if cover is None else f"{cover:.0%}"))
    return lines


def _schema_line(info: Mapping[str, Any] | None) -> str:
    if not info:
        return "schema: unknown"
    return f"schema: v{int(info['version'])}, {int(info['legacy_rows_outside_limits'])} legacy rows outside the new limits"


def render_text(report: Mapping[str, Any]) -> str:
    health = report["health"]
    baseline = report.get("baseline_at")
    lines = ["Assignment merge-quality report",
             f"ingest: {'ok' if health['ok'] else 'NOT OK (' + _c(health['issue']) + ')'}, "
             f"data as of {_c(health['data_as_of'] or 'unknown')}", f"outbox: {report['outbox'] or 'empty'}",
             _schema_line(report.get("schema")),
             f"baseline: set {_c(baseline)}" if baseline else "baseline: NOT set (immediate pushes are off)", ""]
    for kid, stat in report["kids"].items():
        lines += _kid_lines(kid, stat)
    more = " (list truncated)" if report.get("unmatched_truncated") else ""
    lines += ["", f"unmatched pairs: {len(report['unmatched_pairs'])}{more}"]
    for p in report["unmatched_pairs"]:
        lines.append(f"  {_c(p['kid'])} due {p['due']}: \"{_c(p['title_a'])}\" ({_c(p['class_a'])}) ~ "
                     f"\"{_c(p['title_b'])}\" ({_c(p['class_b'])}) ratio {p['ratio']}")
    lines += ["", f"alias suggestions: {len(report['alias_suggestions'])}"]
    lines += [f"  {_c(s['kid'])}: {_c(s['class_a'])} = {_c(s['class_b'])} ({s['count']} pairs)"
              for s in report["alias_suggestions"]]
    return "\n".join(lines) + "\n"
