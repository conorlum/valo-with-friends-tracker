r"""Measurement-local variants of the post-plant time factor, for the arms
declared in docs/superpowers/2026-09-07-predeclared-values.md, entry
"2026-09-19 -- DECLARATION: the post-plant time factor".

NOTHING UNDER app/ IS EDITED. This module installs a wrapper around
`app.scoring.impact._time_factor` at measurement time only. The wrapper is
deliberately NOT a copy of the shipped function: it CALLS the shipped function
and then, for the active variant, overrides its result on the events that
variant is declared to change. A copy would be a second implementation free to
drift from the one it claims to be a reference for, and arm P0 would then be
measuring the copy rather than what ships.

Consequence worth stating plainly: with no variant active the wrapper returns
the shipped value unchanged, by construction. The identity gate in the runner
therefore tests the PLUMBING -- that patching, kwarg pass-through and the
replay path leave every observation untouched -- which is exactly the part that
could silently break.

Each variant receives the shipped value plus the event, and returns a
replacement. The predicates below (`_plant_time`, `_resolved`, `_post_plant`)
mirror the shipped function's own branch conditions; they are short enough to
check by eye against impact.py, which is why the deltas are expressed this way
rather than by rewriting the branches.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scoring import impact as impact_module
from app.scoring.plant_window import is_phantom_plant

SPIKE_SECONDS = 45.0
OVERRIDE_START = 38.0

# The shipped ramp evaluated at the override window's lower edge, which is the
# value a death is charged at t = 37.999... The cliff is this dropping to 0.50
# at t = 38.0.
RAMP_AT_38 = 1 + OVERRIDE_START / 53          # 1.7169811320754718
POST_RESOLUTION_FACTOR = 0.5

_ORIGINAL_TIME_FACTOR = impact_module._time_factor

# Set by `activate`. None means "shipped behaviour", and is the identity gate's
# configuration.
_ACTIVE = None


# --- predicates, mirroring impact.py's own branch conditions ---------------

def _plant_time(round_row):
    return round_row.plant_time if round_row.planted else None


def _resolved(round_row, kill_time):
    """The shipped function's exploded/defused early return -- the condition
    under which it returns 0.5 today."""
    plant_time = _plant_time(round_row)
    exploded = (
        round_row.exploded and plant_time is not None
        and kill_time >= plant_time + SPIKE_SECONDS
    )
    defused = (
        round_row.defused and round_row.defuse_time is not None
        and kill_time >= round_row.defuse_time
    )
    return exploded or defused


def _post_plant(round_row, kill_time):
    """At or after the nominal plant, and not yet resolved -- the region the
    ramp and the plant+38..45 override cover. Phantom plants are INCLUDED,
    because the shipped function includes them; that is candidate C3."""
    plant_time = _plant_time(round_row)
    return plant_time is not None and kill_time >= plant_time and not _resolved(round_row, kill_time)


def _seconds_since_plant(round_row, kill_time):
    return kill_time - round_row.plant_time


def _in_override_window(round_row, kill_time):
    plant_time = _plant_time(round_row)
    if plant_time is None:
        return False
    return plant_time + OVERRIDE_START <= kill_time <= plant_time + SPIKE_SECONDS


def p2b_death_decay(seconds: float) -> float:
    """C2's replacement for the flat 0.50 death charge in [38, 45]: linear from
    the ramp's own value at 38 down to the post-resolution value at 45, so the
    factor is continuous at both ends instead of stepping 70.8% in 0.1s."""
    span = SPIKE_SECONDS - OVERRIDE_START
    progress = (seconds - OVERRIDE_START) / span
    return RAMP_AT_38 + (POST_RESOLUTION_FACTOR - RAMP_AT_38) * progress


# --- the variants ----------------------------------------------------------
#
# Signature: (round_row, kill_time, for_death, shipped_value) -> float

def _v_p1(round_row, kill_time, for_death, shipped):
    """C1: a decided round pays 0, not 0.5."""
    if _resolved(round_row, kill_time):
        return 0.0
    return shipped


def _v_p2b(round_row, kill_time, for_death, shipped):
    """C2: the death cliff at plant+38 becomes a linear decay. The KILL side is
    left exactly as shipped (flat 1.75), so this arm moves deaths only."""
    if for_death and _post_plant(round_row, kill_time) and _in_override_window(round_row, kill_time):
        return p2b_death_decay(_seconds_since_plant(round_row, kill_time))
    return shipped


def _v_p3a(round_row, kill_time, for_death, shipped):
    """C3, minimal: the resolution value applies at plant+45 whatever the
    exploded/defused flags say, so the ramp is capped. Changes nothing in a
    round that already resolves -- the shipped function returns 0.5 there
    already -- and catches the phantom plants climbing past the ceiling."""
    plant_time = _plant_time(round_row)
    if plant_time is not None and kill_time >= plant_time + SPIKE_SECONDS:
        return POST_RESOLUTION_FACTOR
    return shipped


def _v_p3b(round_row, kill_time, for_death, shipped):
    """C3, structural: a phantom plant gets no post-plant regime at all -- what
    routing the legacy branches through effective_plant_time would do."""
    if round_row.planted and is_phantom_plant(round_row):
        return 1.0
    return shipped


def _v_p4(scale: float):
    """C4: the post-plant regime scaled by a constant.

    The scale covers the ramp AND the plant+38..45 override, because both are
    the post-plant regime whose LEVEL is the thing under test -- it is the
    quantity Part 4's centring constant `c` preserved, and 1/c = 0.7826 is
    where this candidate's prior evidence comes from. It does not touch the
    post-resolution value, which is candidate C1's territory. The override
    window is 2,924 of 154,031 post-plant kills, so the reading barely moves
    either way; it is written down because it is a choice.
    """
    def variant(round_row, kill_time, for_death, shipped):
        if _post_plant(round_row, kill_time):
            return shipped * scale
        return shipped
    return variant


def _v_p2l(lam: float):
    """P2L: P2b's extra death-side leverage spread uniformly over every
    post-plant death instead of concentrated in the window. `lam` is fitted per
    fold on training matches only (see the runner)."""
    def variant(round_row, kill_time, for_death, shipped):
        if for_death and _post_plant(round_row, kill_time):
            return shipped * lam
        return shipped
    return variant


def _compose(*variants):
    """Apply in order, each seeing the previous one's output as `shipped`."""
    def variant(round_row, kill_time, for_death, shipped):
        value = shipped
        for inner in variants:
            value = inner(round_row, kill_time, for_death, value)
        return value
    return variant


# PC: the Tier A fixes combined. P3b runs FIRST so a phantom plant is out of
# the post-plant regime before the other two are consulted; P1 then zeroes a
# resolved round, and P2b reshapes what is left. A phantom plant sets neither
# resolution flag, so P1 never fires on one either way -- the ordering is for
# legibility, not to paper over an interaction.
def _v_pc(round_row, kill_time, for_death, shipped):
    return _compose(_v_p3b, _v_p1, _v_p2b)(round_row, kill_time, for_death, shipped)


# PC+ (declaration section 5): PC plus every Tier B arm that reached
# IMPROVEMENT. P4f selected scale 0.70 on all five folds, so the level member is
# the fixed 0.70 rather than a per-fold choice, and PC+ needs no refitting.
#
# ORDER MATTERS, and the declaration did not fix it, so it is fixed here and
# stated in the RESULT. P2b first, so the death-window decay exists; then the
# level, which scales the whole post-plant regime INCLUDING that decay, because
# the decay is part of the regime whose level is under test; then P3b and P1,
# whose outputs are NOT scaled -- a phantom plant's 1.0 is the pre-plant value
# and a decided round's 0 is an absence of stake, and scaling either would be
# applying a post-plant level to something the fix just removed from the
# post-plant regime.
def _v_pc_plus(round_row, kill_time, for_death, shipped):
    return _compose(_v_p2b, _v_p4(0.70), _v_p3b, _v_p1)(
        round_row, kill_time, for_death, shipped)


VARIANTS = {
    "P0": None,
    "PC+": _v_pc_plus,
    "P1": _v_p1,
    "P2b": _v_p2b,
    "P3a": _v_p3a,
    "P3b": _v_p3b,
    "PC": _v_pc,
}


def variant_for(name: str, **kwargs):
    """Resolve an arm name to its variant function. Parameterised arms are
    built here so the runner never constructs one by hand."""
    if name.startswith("P4-"):
        return _v_p4(float(name.split("-", 1)[1]))
    if name == "P2L":
        return _v_p2l(kwargs["lam"])
    if name not in VARIANTS:
        raise KeyError(f"unknown arm {name!r}")
    return VARIANTS[name]


# --- installation ----------------------------------------------------------

def _patched(round_row, kill_time, for_death=False, **kwargs):
    shipped = _ORIGINAL_TIME_FACTOR(round_row, kill_time, for_death=for_death, **kwargs)
    if _ACTIVE is None:
        return shipped
    return _ACTIVE(round_row, kill_time, for_death, shipped)


def install():
    """Patch impact.py's module-global. Both call sites resolve `_time_factor`
    through the module namespace at call time, so this reaches them."""
    impact_module._time_factor = _patched


def uninstall():
    impact_module._time_factor = _ORIGINAL_TIME_FACTOR


def activate(variant):
    """Select the active variant. None restores shipped behaviour."""
    global _ACTIVE
    _ACTIVE = variant
