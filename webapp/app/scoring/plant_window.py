"""Leaf helpers for reasoning about a round's plant: phantom-plant detection,
the attacking side (including overtime), and proximity-to-plant arithmetic.

This module is the single consolidated implementation the plant-window spec
(docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 1) requires. It depends only on the models and app.models.match.Team --
no DB session, no service imports -- so every layer above it (scoring,
services) can depend on it without risking an import cycle.

Four call sites duplicated an attacking-side function before this module
existed, and only one of them (app/services/map_side_stats.py) handled
overtime correctly. That OT rule -- round 25 resets to round 1's side, then
alternates every round -- is empirically verified (2026-08-23, 5,251 rounds,
0 disagreements against the outcome-derived attacker; see the spec for the
full derivation) and is absorbed here as the canonical `attacking_team`.
"""

from app.models.match import Team

PRE_WINDOW = 30.0
POST_WINDOW = 15.0


def is_phantom_plant(round_row) -> bool:
    """A planted round decided by the round timer (a 'Time Win') never armed
    for real -- a genuine plant forces an explode or a defuse. The rule is
    the outcome string, not the plant_time value: 14 further rounds have
    plant_time > 100s and end in Elimination Wins, and those are legitimate
    rounds with a noisy timestamp, not phantom plants. (76 phantom rounds and
    14 such Elimination Wins on the full dataset, re-measured 2026-09-09; the
    24/6 this used to cite came from a pre-sync subset.)"""
    return bool(round_row.planted and round_row.outcome and "Time Win" in round_row.outcome)


def effective_plant_time(round_row) -> float | None:
    """The single choke point for reading a round's plant time: None for an
    unplanted round OR a phantom plant, so callers never have to remember to
    exclude phantoms themselves."""
    if not round_row.planted or is_phantom_plant(round_row):
        return None
    return round_row.plant_time


def attacking_team(round_number: int) -> Team | None:
    """None only for round_number < 1. Regulation is the documented 1-12 /
    13-24 split; past round 24 (overtime) the side resets to round 1's side
    at round 25 and then alternates every single round (unlike regulation's
    swap-every-12)."""
    if round_number < 1:
        return None
    if round_number <= 12:
        return Team.TEAM_1
    if round_number <= 24:
        return Team.TEAM_2
    offset = round_number - 25
    return Team.TEAM_1 if offset % 2 == 0 else Team.TEAM_2


def seconds_to_plant(round_row, kill_time: float) -> float | None:
    """Signed time relative to the (effective) plant: positive before the
    plant, counting down to 0 at the plant instant, negative after it. None
    if the round has no effective plant (unplanted or phantom)."""
    plant_time = effective_plant_time(round_row)
    if plant_time is None:
        return None
    return plant_time - kill_time


def in_plant_window(round_row, kill_time: float) -> bool:
    """True within PRE_WINDOW seconds before the plant through POST_WINDOW
    seconds after it (inclusive of both ends). False for a round with no
    effective plant."""
    dt = seconds_to_plant(round_row, kill_time)
    if dt is None:
        return False
    return -POST_WINDOW <= dt <= PRE_WINDOW


# Half-open buckets in kill_time - plant_time ("time since plant") space,
# matching the bucketing already used by
# docs/superpowers/diagnostics/measure_proximity_curve_and_overtime.py so the
# M1/M2 measurement figures this module supersedes remain reproducible
# against the same bucket boundaries.
_WINDOW_BUCKETS: list[tuple[str, float, float]] = [
    ("-30..-20", -30.0, -20.0),
    ("-20..-10", -20.0, -10.0),
    ("-10..-5", -10.0, -5.0),
    ("-5..0", -5.0, 0.0),
    ("0..+5", 0.0, 5.0),
    ("+5..+15", 5.0, 15.0),
]


def window_bucket(round_row, kill_time: float) -> str | None:
    """The labelled bucket a kill falls into, in time-since-plant space
    (negative before the plant, non-negative after). None if the round has
    no effective plant or the kill falls outside every bucket."""
    dt = seconds_to_plant(round_row, kill_time)
    if dt is None:
        return None
    since_plant = -dt
    for label, lo, hi in _WINDOW_BUCKETS:
        if lo <= since_plant < hi:
            return label
    return None
