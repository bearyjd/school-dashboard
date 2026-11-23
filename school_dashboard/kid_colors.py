"""Named, stable per-kid colours.

Each kid uses the colour configured on their roster entry or the next available colour in roster order.
The same names are CSS tokens in the SPA.
"""
from typing import Mapping, Optional, Sequence

PALETTE: tuple[str, ...] = ("green", "lavender", "amber", "blue", "rose", "teal")
FALLBACK: tuple[str, ...] = ("amber", "lavender", "green", "blue", "rose", "teal")


def resolve_colors(names: Sequence[str], explicit: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Map each name to a palette colour; deterministic for a given roster order.

    Kids with no explicit colour take the next fallback colour not already used by another kid
    (colours repeat only once the whole palette is taken).
    """
    explicit = {name.strip().lower(): color for name, color in (explicit or {}).items()}
    fixed = {name: explicit.get(name.strip().lower()) for name in names}
    used = set(fixed.values())
    candidates = [color for color in FALLBACK if color not in used] or FALLBACK
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
    """New cards with a `color` ('' when the card has no usable child); inputs are not mutated.

    Card children match roster names case-insensitively. `roster_names` fixes the fallback order so
    colours match the overview; other names follow, sorted.
    """
    def child_of(card: dict) -> str:
        child = card.get("child")
        return child if isinstance(child, str) else ""

    known = {n.lower() for n in roster_names}
    extra = sorted({child_of(c) for c in cards if child_of(c) and child_of(c).lower() not in known})
    colors = resolve_colors([*roster_names, *extra], explicit)
    by_lower = {n.lower(): col for n, col in colors.items()}
    return [{**c, "color": by_lower.get(child_of(c).lower(), "")} for c in cards]
