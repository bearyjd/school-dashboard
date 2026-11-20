"""Named, stable per-kid colours.

A kid's colour is resolved by (1) a valid `color` in the roster config, (2) the built-in name default,
(3) a positional fallback among the kids that matched neither. The same names are CSS tokens in the SPA.
"""
from typing import Mapping, Optional, Sequence

PALETTE: tuple[str, ...] = ("green", "lavender", "amber", "blue", "rose", "teal")
DEFAULTS: Mapping[str, str] = {"jack": "green", "penn": "lavender", "ford": "amber"}
FALLBACK: tuple[str, ...] = ("blue", "rose", "teal")


def resolve_colors(names: Sequence[str], explicit: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Map each name to a palette colour; deterministic for a given roster order.

    Kids with no explicit or default colour take the next fallback colour not already used by another kid
    (colours repeat only once the whole palette is taken).
    """
    explicit = explicit or {}
    fixed = {n: explicit.get(n) or DEFAULTS.get(n.strip().lower()) for n in names}
    used = {c for c in fixed.values() if c}
    candidates = [c for c in (*FALLBACK, *PALETTE) if c not in used]
    candidates = list(dict.fromkeys(candidates)) or list(FALLBACK)
    out: dict[str, str] = {}
    unresolved = 0
    for name in names:
        color = fixed[name]
        if color is None:
            color = candidates[unresolved % len(candidates)]
            unresolved += 1
        out[name] = color
    return out


def with_child_colors(cards: Sequence[dict], explicit: Optional[Mapping[str, str]] = None,
                      roster_names: Sequence[str] = ()) -> list[dict]:
    """New cards with a `color` ('' when the card has no child); inputs are not mutated.

    Card children match roster names case-insensitively. `roster_names` fixes the fallback order so
    colours match the overview; other names follow, sorted.
    """
    known = {n.lower() for n in roster_names}
    extra = sorted({c["child"] for c in cards if c.get("child") and c["child"].lower() not in known})
    colors = resolve_colors([*roster_names, *extra], explicit)
    by_lower = {n.lower(): col for n, col in colors.items()}
    return [{**c, "color": by_lower.get(c.get("child", "").lower(), "")} for c in cards]
