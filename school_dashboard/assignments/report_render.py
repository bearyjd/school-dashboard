"""Text rendering and redaction for the merge-quality report (pure; inputs are never mutated)."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

_NAME_FIELDS = ("title_a", "title_b", "class_a", "class_b")


def _hash(prefix: str, text: str | None) -> str | None:
    if text is None:
        return None
    return f"{prefix}:{hashlib.sha256(text.strip().casefold().encode('utf-8')).hexdigest()[:8]}"


def _hash_pair(pair: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(pair)
    for name in _NAME_FIELDS:
        out[name] = _hash("t" if name.startswith("title") else "c", pair[name])
    alias = pair["suggested_alias"]
    out["suggested_alias"] = None if alias is None else {k: _hash("c", v) for k, v in alias.items()}
    return out


def redact(report: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of the report with titles and class names replaced by stable short hashes."""
    out = dict(report)
    out["unmatched_pairs"] = [_hash_pair(p) for p in report["unmatched_pairs"]]
    out["alias_suggestions"] = [{**s, "class_a": _hash("c", s["class_a"]), "class_b": _hash("c", s["class_b"])}
                                for s in report["alias_suggestions"]]
    return out


def _kid_lines(kid: str, stat: Mapping[str, Any]) -> list[str]:
    lines = [f"{kid}: {stat['assignments']} assignments, {stat['multi_source']} in 2+ sources "
             f"(merge rate {stat['merge_rate']:.0%}), {stat['possible_duplicates']} possible duplicates"]
    lines += [f"  {src}: {s['links']} links ({s['live']} live)" for src, s in sorted(stat["sources"].items())]
    lines.append(f"  flags: {stat['flags']}  stages: {stat['stages']}")
    cover = stat["evidence_coverage"]
    lines.append("  received-evidence coverage: " + ("n/a" if cover is None else f"{cover:.0%}"))
    return lines


def render_text(report: Mapping[str, Any]) -> str:
    health = report["health"]
    lines = ["Assignment merge-quality report",
             f"ingest: {'ok' if health['ok'] else 'NOT OK (' + str(health['issue']) + ')'}, "
             f"data as of {health['data_as_of'] or 'unknown'}", f"outbox: {report['outbox'] or 'empty'}", ""]
    for kid, stat in report["kids"].items():
        lines += _kid_lines(kid, stat)
    lines += ["", f"unmatched pairs: {len(report['unmatched_pairs'])}"]
    for p in report["unmatched_pairs"]:
        lines.append(f"  {p['kid']} due {p['due']}: \"{p['title_a']}\" ({p['class_a']}) ~ "
                     f"\"{p['title_b']}\" ({p['class_b']}) ratio {p['ratio']}")
    lines += ["", f"alias suggestions: {len(report['alias_suggestions'])}"]
    lines += [f"  {s['kid']}: {s['class_a']} = {s['class_b']} ({s['count']} pairs)" for s in report["alias_suggestions"]]
    return "\n".join(lines) + "\n"
