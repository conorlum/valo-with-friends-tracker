"""A player's stance relative to a cover piece: standing on it, hugging it, or crossing it in a jump. Inert until
the see-over rule lands: `stance_at` returns None and nothing in the engine calls it yet."""

from __future__ import annotations

from dataclasses import dataclass

BOX_SETTLE_S = 0.5
ANCHOR_MAX_M = 2.0
BOX_RISE_M = None          # fitted by the GATE probe (W9)
BOX_STEADY_DZ_M = None     # fitted by the GATE probe (W9)

CALLS = 0                  # how many times stance_at has been called (the identity witness reads it)


@dataclass(frozen=True)
class Stance:
    kind: str
    px: tuple[float, float]
    eye_px: tuple[float, float]
    see_over: int
    anchor: int | None


def stance_at(rnd, slot, t) -> Stance | None:
    global CALLS
    CALLS += 1
    return None
