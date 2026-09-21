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

def _v_p1(round_row, kill_time, for_death, shipped, ctx=None):
    """C1: a decided round pays 0, not 0.5."""
    if _resolved(round_row, kill_time):
        return 0.0
    return shipped


def _v_p2b(round_row, kill_time, for_death, shipped, ctx=None):
    """C2: the death cliff at plant+38 becomes a linear decay. The KILL side is
    left exactly as shipped (flat 1.75), so this arm moves deaths only."""
    if for_death and _post_plant(round_row, kill_time) and _in_override_window(round_row, kill_time):
        return p2b_death_decay(_seconds_since_plant(round_row, kill_time))
    return shipped


def _v_p3a(round_row, kill_time, for_death, shipped, ctx=None):
    """C3, minimal: the resolution value applies at plant+45 whatever the
    exploded/defused flags say, so the ramp is capped. Changes nothing in a
    round that already resolves -- the shipped function returns 0.5 there
    already -- and catches the phantom plants climbing past the ceiling."""
    plant_time = _plant_time(round_row)
    if plant_time is not None and kill_time >= plant_time + SPIKE_SECONDS:
        return POST_RESOLUTION_FACTOR
    return shipped


def _v_p3b(round_row, kill_time, for_death, shipped, ctx=None):
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
    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if _post_plant(round_row, kill_time):
            return shipped * scale
        return shipped
    return variant


def _v_flat(k: float):
    """DECLARATION 2: the post-plant regime replaced by a flat constant -- no
    ramp, no plant+38..45 override. Pre-plant stays 1.0 and the post-resolution
    value stays 0.5, both untouched, so this isolates the SHAPE of the
    post-plant factor and nothing else.

    k = 1.00 is the null model: no post-plant timing whatsoever, the factor is
    1.0 everywhere a round is live. It is in the grid precisely so "no model"
    is an option the measurement can choose.
    """
    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if _post_plant(round_row, kill_time):
            return k
        return shipped
    return variant


def _v_swing(scale: float, value_table, floor: float, ceil: float, fallback: float):
    """DECLARATION 6 (D1): the post-plant payout IS the measured win-probability
    swing, not the kill-order bonus times a clock factor.

    Today a post-plant kill pays `K(s) x T(t)`. D1 pays `S x clamp(D, floor,
    ceil)` where D is what the event actually changed. It is delivered through
    this wrapper by returning `T = S*D/K`, which makes the kill leg exactly
    `K * T = S*D` and the death leg `K * traded * T = S*D*traded`, so the trade
    discount survives untouched.

    `K` is recovered from the alive counts alone. That is sound because the
    kill-order graph is symmetric under team relabeling -- verified, zero
    asymmetric edges -- so it does not matter which side is stored as team 1.

    A cell with no supported D, a self-kill, or a phantom plant falls back to
    the flat `fallback`, matching the comparator rather than the shipped ramp.
    """
    from app.scoring.impact import _kill_order_bonus
    from app.models.match import Team
    from app.scoring.postplant_factor import difference
    from app.scoring.plant_window import effective_plant_time

    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if not _post_plant(round_row, kill_time):
            return shipped
        ctx = ctx or {}
        alive = ctx.get("alive_counts")
        is_attacker = ctx.get("is_attacker")
        if alive is None or is_attacker is None or ctx.get("self_kill"):
            return fallback
        effective = effective_plant_time(round_row)
        if effective is None:          # phantom plant: never in D's population
            return fallback
        a, d = alive
        t = int(kill_time - effective)
        victim_is_attacker = not is_attacker
        raw = difference(value_table, a, d, t, victim_is_attacker)
        if raw is None:
            return fallback
        swung = min(max(raw, floor), ceil)
        # K for this transition, with attackers labelled team 1 (safe by the
        # verified mirror symmetry). team1_kill_index/team2_kill_index are the
        # counts BEFORE the kill, and the loser is the victim's side.
        # Argument order validated against the scorer's own stored
        # kill_order_bonus on all 153,481 post-plant kills: TEAM_1 when the
        # victim is an attacker matches exactly, the mirror matches 24%.
        k = _kill_order_bonus(a, d, Team.TEAM_1 if victim_is_attacker else Team.TEAM_2,
                              False)
        if not k:
            return fallback
        return scale * swung / k
    return variant


def _v_side(level: float, weights: dict):
    """DECLARATION 4: a post-plant factor that is a constant PER VICTIM SIDE.

    `weights` maps a key to a multiplier whose kill-weighted mean over the
    training population is 1, so `level` alone sets the overall level and the
    weights carry only the split. Keys are either `victim_is_attacker` (A1) or
    `(victim_is_attacker, late)` with `late` meaning t >= 30 (A2).

    Two events decline the split and take the flat `level` instead, because
    "the victim's side" is undefined or unestimated for them:
      * a self-kill, where killer and victim are the same player -- the same
        exclusion Part 4 makes for the same reason;
      * a call with no `is_attacker`, which is how the scorer signals it could
        not resolve the attacking side for the round.
    """
    banded = any(isinstance(k, tuple) for k in weights)

    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if not _post_plant(round_row, kill_time):
            return shipped
        ctx = ctx or {}
        is_attacker = ctx.get("is_attacker")
        if is_attacker is None or ctx.get("self_kill"):
            return level
        # is_attacker names the KILLER's side, so the victim is the other one.
        victim_is_attacker = not is_attacker
        if banded:
            late = (kill_time - round_row.plant_time) >= 30
            w = weights.get((victim_is_attacker, late), 1.0)
        else:
            w = weights.get(victim_is_attacker, 1.0)
        return level * w
    return variant


def _v_band(level: float, profile, norm: float):
    """DECLARATION 11: a STEP profile over time bands, not a smooth curve.

    `profile` is [(t_lo, multiplier), ...] ascending; the last band whose
    `t_lo <= t` wins. `norm` is the kill-weighted mean of the multiplier over
    the post-plant kill population, so `level` alone sets the LEVEL and the
    profile carries only the step shape:

        T = level * multiplier(t) / norm      ->  mean T over post-plant = level

    Every shape arm before this was smooth and RISING (the shipped ramp, Part
    4's curve, `t/45`). The measured swing is flat to 30s and then COLLAPSES --
    20.03pp at 20-30s against 2.54pp at 38-45s, an 8x drop -- so this tests a
    cliff DOWNWARD at a mechanically meaningful boundary, which is the shape
    the data actually shows and the opposite of what ships.
    """
    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if not _post_plant(round_row, kill_time):
            return shipped
        t = kill_time - round_row.plant_time
        mult = profile[0][1]
        for t_lo, m in profile:
            if t >= t_lo:
                mult = m
        return level * mult / norm
    return variant


def _v_additive(alpha: float, kbar: float, use_time: bool, side_weights=None):
    """DECLARATION 10: the post-plant payout is `K(s)` PLUS a term, not `K(s)`
    TIMES one.

        payout = K(s) + f
        f      = alpha * kbar * shape
        shape  = (t / SPIKE_SECONDS) when use_time else 1.0,
                 multiplied by side_weights[victim_is_attacker] when given

    Every arm in this investigation so far has been multiplicative or a
    replacement. This is the first additive one, and it encodes a different
    belief: a multiplier says late kills AMPLIFY whatever the kill was worth,
    an additive term says being late is worth something IN ITSELF, the same
    amount whether the kill was decisive or marginal.

    Delivered through the wrapper as `T = 1 + f/K`, so `K*T = K + f` exactly.
    The base is 1.0 -- the F-1.00 flat arm -- NOT the shipped ramp: this
    REPLACES the multiplicative time factor rather than stacking on top of it,
    which is what makes `ADD vs F-1.00` isolate `f` and nothing else.

    `K` is recovered exactly as D1 recovers it, with the same validated
    argument order (TEAM_1 when the victim is an attacker; the mirror matches
    only 24%). An event with no usable `K`, a self-kill or a phantom plant
    falls back to the flat 1.0 base, matching the comparator.
    """
    from app.scoring.impact import _kill_order_bonus
    from app.models.match import Team
    from app.scoring.plant_window import effective_plant_time

    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if not _post_plant(round_row, kill_time):
            return shipped
        ctx = ctx or {}
        alive = ctx.get("alive_counts")
        is_attacker = ctx.get("is_attacker")
        if alive is None or is_attacker is None or ctx.get("self_kill"):
            return 1.0
        effective = effective_plant_time(round_row)
        if effective is None:          # phantom plant
            return 1.0
        a, d = alive
        victim_is_attacker = not is_attacker
        k = _kill_order_bonus(a, d, Team.TEAM_1 if victim_is_attacker else Team.TEAM_2,
                              False)
        if not k:
            return 1.0
        shape = 1.0
        if use_time:
            shape = min(max((kill_time - effective) / SPIKE_SECONDS, 0.0), 1.0)
        if side_weights is not None:
            shape *= side_weights.get(victim_is_attacker, 1.0)
        return 1.0 + (alpha * kbar * shape) / k
    return variant


def _v_p2l(lam: float):
    """P2L: P2b's extra death-side leverage spread uniformly over every
    post-plant death instead of concentrated in the window. `lam` is fitted per
    fold on training matches only (see the runner)."""
    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        if for_death and _post_plant(round_row, kill_time):
            return shipped * lam
        return shipped
    return variant


def _compose(*variants):
    """Apply in order, each seeing the previous one's output as `shipped`."""
    def variant(round_row, kill_time, for_death, shipped, ctx=None):
        value = shipped
        for inner in variants:
            value = inner(round_row, kill_time, for_death, value, ctx)
        return value
    return variant


# PC: the Tier A fixes combined. P3b runs FIRST so a phantom plant is out of
# the post-plant regime before the other two are consulted; P1 then zeroes a
# resolved round, and P2b reshapes what is left. A phantom plant sets neither
# resolution flag, so P1 never fires on one either way -- the ordering is for
# legibility, not to paper over an interaction.
def _v_pc(round_row, kill_time, for_death, shipped, ctx=None):
    return _compose(_v_p3b, _v_p1, _v_p2b)(round_row, kill_time, for_death, shipped, ctx)


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
def _v_pc_plus(round_row, kill_time, for_death, shipped, ctx=None):
    return _compose(_v_p2b, _v_p4(0.70), _v_p3b, _v_p1)(
        round_row, kill_time, for_death, shipped, ctx)


ROUND_SECONDS = 100.0


def round_decided(round_row, kill_time):
    """DECLARATION 12: the round's outcome no longer depends on anything a
    player can do. Three ways, and only these:

      * the spike was defused, and the kill is at or after the defuse;
      * the spike really armed (not a phantom plant) and the kill is at or
        after plant+45 -- it has exploded, whatever the flags say (C3);
      * no real plant, the round was a Time Win, and the kill is after 100s.

    The Time Win condition is not decoration: 14 rounds carry plant_time >
    100s and end in Elimination Wins (plant_window.is_phantom_plant), so the
    clock alone is noisy enough to catch a real round-ending kill. On the
    corpus every post-100s kill in an unplanted or phantom round is in a Time
    Win round (338 kills), so the condition removes nothing today; it is there
    so a noisy timestamp can never zero a live kill."""
    from app.scoring.plant_window import effective_plant_time
    if (round_row.defused and round_row.defuse_time is not None
            and kill_time >= round_row.defuse_time):
        return True
    plant_time = effective_plant_time(round_row)
    if plant_time is not None:
        return kill_time >= plant_time + SPIKE_SECONDS
    return (kill_time > ROUND_SECONDS
            and bool(round_row.outcome and "Time Win" in round_row.outcome))


def _v_decided(round_row, kill_time, for_death, shipped, ctx=None):
    """DECLARATION 12, arm N: NO time factor. Every kill and death is worth
    1.0 before and after the plant -- no ramp, no plant+38..45 override, no
    post-plant premium -- and 0 once the round is decided, where the shipped
    function pays 0.5. Timing survives only as the answer to "could this kill
    still change the round?"."""
    return 0.0 if round_decided(round_row, kill_time) else 1.0


VARIANTS = {
    "P0": None,
    "N": _v_decided,
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
    if name.startswith("L-"):
        return _v_p4(float(name.split("-", 1)[1]))
    if name.startswith("F-"):
        return _v_flat(float(name.split("-", 1)[1]))
    if name.startswith("STEP"):
        # DECLARATION 11. Banded step profile; the caller supplies the profile
        # and the kill-weighted norm that holds the LEVEL fixed.
        return _v_band(kwargs["level"], kwargs["profile"], kwargs["norm"])
    if name.startswith("ADD-"):
        # DECLARATION 10. ADD-T / ADD-S / ADD-TS; alpha, kbar and the side
        # weights are supplied by the caller, which calibrates alpha so the
        # arm's MEAN post-plant payout matches a measured flat arm.
        return _v_additive(kwargs["alpha"], kwargs["kbar"],
                           kwargs["use_time"], kwargs.get("side_weights"))
    if name == "D1":
        return _v_swing(kwargs["scale"], kwargs["value_table"],
                        kwargs["floor"], kwargs["ceil"], kwargs["fallback"])
    if name in ("A1", "A2"):
        return _v_side(kwargs["level"], kwargs["weights"])
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
    # kwargs is the scorer's OWN call context, passed through untouched. A
    # side-dependent arm needs `is_attacker` (which names the KILLER's side, so
    # the victim is the other one) and `self_kill` (where killer and victim are
    # the same player and "the victim's side" has no meaning).
    return _ACTIVE(round_row, kill_time, for_death, shipped, kwargs)


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
