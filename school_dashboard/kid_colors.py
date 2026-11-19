"""Named, stable per-kid colours.

A kid's colour is resolved by (1) a valid `color` in the roster config, (2) the built-in name default,
(3) a positional fallback among the kids that matched neither. The same names are CSS tokens in the SPA.
"""
from typing import Mapping, Optional, Sequence

PALETTE: tuple[str, ...] = ("green", "lavender", "amber", "blue", "rose", "teal")
DEFAULTS: Mapping[str, str] = {"jack": "green", "penn": "lavender", "ford": "amber"}
FALLBACK: tuple[str, ...] = ("blue", "rose", "teal")


def resolve_colors(names: Sequence[str], explicit: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Map each name to a palette colour; deterministic for a given roster order."""
    explicit = explicit or {}
    out: dict[str, str] = {}
    unresolved = 0
    for name in names:
        color = explicit.get(name) or DEFAULTS.get(name.strip().lower())
        if color is None:
            color = FALLBACK[unresolved % len(FALLBACK)]
            unresolved += 1
        out[name] = color
    return out


def with_child_colors(cards: Sequence[dict], explicit: Optional[Mapping[str, str]] = None,
                      roster_names: Sequence[str] = ()) -> list[dict]:
    """New cards with a `color` ('' when the card has no child); inputs are not mutated.

    `roster_names` fixes the fallback order so colours match the overview; other names follow, sorted.
    """
    extra = sorted({c["child"] for c in cards if c.get("child")} - set(roster_names))
    colors = resolve_colors([*roster_names, *extra], explicit)
    return [{**c, "color": colors.get(c.get("child", ""), "")} for c in cards]
