"""Part 3 of docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md:
the pre-plant proximity-to-plant time factor. Pure extraction/fitting leaf --
no coupling to the live impact.py kill loop (see app/scoring/preplant_scalar.py
for the dormant runtime hook).

`dt` throughout this module is `seconds_to_plant` (app.scoring.plant_window):
POSITIVE before the plant, decreasing to 0 at the plant instant. This matches
the spec's own knot values, written as positive numbers (30, 20, 10, 5, 0)."""

from dataclasses import dataclass

from sqlalchemy import text

from app.models.match import Team
from app.scoring.plant_window import attacking_team, effective_plant_time, is_phantom_plant, seconds_to_plant


@dataclass(frozen=True)
class PreplantKillObservation:
    match_id: int
    round_id: int
    dt: float                          # seconds_to_plant; always > 0 (strictly pre-plant)
    adv: int                           # killer_alive - victim_alive at kill time, pre-decrement
    is_attacker: bool                  # was the killer on the attacking side this round
    exact_state: str                   # "{killer_alive}v{victim_alive}"
    round_won_by_killer_team: bool | None  # None if the round's winner is not determinable


class _RoundRow:
    """Minimal shim so plant_window's helpers (which only read .planted,
    .plant_time, .outcome) can run against a plain dict row without an ORM
    round-trip."""

    def __init__(self, planted, plant_time, outcome):
        self.planted = planted
        self.plant_time = plant_time
        self.outcome = outcome


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def extract_preplant_observations(db) -> list[PreplantKillObservation]:
    """One row per non-self pre-plant kill in a non-phantom, non-surrendered
    planted round. Deaths are NOT extracted separately -- they reuse the same
    fitted scalar at scoring time (spec, "Deaths": ship symmetric)."""
    rounds = {
        r["id"]: dict(r)
        for r in db.execute(text(
            "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds"
        )).mappings()
    }
    match_players = {
        mp["id"]: dict(mp)
        for mp in db.execute(text("SELECT id, match_id, team FROM match_players")).mappings()
    }

    kills_by_round: dict[int, list[dict]] = {}
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round.setdefault(k["round_id"], []).append(dict(k))

    observations: list[PreplantKillObservation] = []
    for round_id, kills in kills_by_round.items():
        r = rounds.get(round_id)
        if r is None:
            continue
        if r["outcome"] and "Surrendered" in r["outcome"]:
            continue

        round_row = _RoundRow(r["planted"], r["plant_time"], r["outcome"])
        if is_phantom_plant(round_row):
            continue
        plant_time = effective_plant_time(round_row)
        if plant_time is None:
            continue  # never-planted (or phantom, already excluded above)

        winner = _winner_team(r["outcome"])
        atk = attacking_team(r["round_number"])
        alive = {Team.TEAM_1: 5, Team.TEAM_2: 5}

        for kill in kills:
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if not killer_id or not victim_id or killer_id not in match_players or victim_id not in match_players:
                continue
            # SQLAlchemy's Enum column stores the member NAME ("TEAM_1"), not
            # its value ("team-1"), and raw SQL reads that name back as a
            # plain string on both sqlite and Postgres -- Team[...] (by name)
            # is the correct reconstruction, not Team(...) (by value).
            killer_team = Team[match_players[killer_id]["team"]]
            victim_team = Team[match_players[victim_id]["team"]]
            self_kill = killer_team == victim_team
            kill_time = kill["event_time_seconds"]
            dt = seconds_to_plant(round_row, kill_time)

            if not self_kill and dt is not None and dt > 0:
                killer_alive = alive[killer_team]
                victim_alive = alive[victim_team]
                observations.append(PreplantKillObservation(
                    match_id=r["match_id"],
                    round_id=round_id,
                    dt=dt,
                    adv=killer_alive - victim_alive,
                    is_attacker=(atk == killer_team),
                    exact_state=f"{killer_alive}v{victim_alive}",
                    round_won_by_killer_team=(winner == killer_team) if winner is not None else None,
                ))

            if not self_kill and alive[victim_team] > 0:
                alive[victim_team] -= 1

    return observations


# -- The fixed-knot proximity shape (spec, "Shape knots") -------------------
#
# Knots at dt = 30, 20, 10, 5, 0 (dt is seconds_to_plant: POSITIVE before the
# plant). shape(30) is pinned to 0 (the far-end anchor, no free parameter).
# shape(20) = theta1 and shape(10) = theta2 are free, fitted parameters
# (Task 3). The plateau below 10s is IMPOSED, not fitted: shape(5) and
# shape(0) both equal theta2 exactly, rather than carrying their own
# parameters -- M1 shows the near-plant buckets dip below the -10..-5 bucket
# in every even state, and letting the shape fit that dip would misrepresent
# sampling noise as a real late-arriving decline.
SHAPE_KNOTS = (30.0, 20.0, 10.0, 5.0, 0.0)


def shape_basis(dt: float) -> tuple[float, float]:
    """Weights (w1, w2) on the two free shape parameters theta1 = shape(20),
    theta2 = shape(10) == shape(5) == shape(0) (the imposed plateau), such
    that shape(dt) = w1*theta1 + w2*theta2 for any fitted (theta1, theta2)."""
    if dt >= 30.0:
        return (0.0, 0.0)
    if dt >= 20.0:
        frac = (30.0 - dt) / 10.0
        return (frac, 0.0)
    if dt >= 10.0:
        frac = (20.0 - dt) / 10.0
        return (1.0 - frac, frac)
    return (0.0, 1.0)  # plateau: 0 <= dt < 10 (and anything nearer the plant)


# -- The exact-state logistic fit (spec, "The fitting contract") ------------
#
# Advantage is clamped to [-3, 2] for the INTERACTION term only -- exact
# states keep their real alive counts. logit_lift(adv, side) is defined at
# the near-plant plateau (w1=0, w2=1, i.e. dt <= 10, where shape(dt) == 1 by
# construction), so shape(dt) * logit_lift(adv, side) reproduces the fitted
# linear predictor exactly at every dt.
#
# FIT AS A PRODUCT, not reverse-engineered into one. An earlier version fit
# w1 (the dt=20 knot) as its own pooled, un-interacted coefficient theta1,
# then set shape_mid_ratio = theta1/theta2 after the fact, hoping
# shape(dt)*logit_lift(adv, side) would reproduce it. It doesn't, except at
# the one cell where logit_lift(adv, side) happens to equal theta2 -- see
# docs/superpowers/2026-09-08-preplant-dip-independent-verification.md
# section 7, Bug B, up to 3.2 logits off everywhere else. (Interacting w1
# with adv/side directly -- giving it its own independent intercept/slope
# family -- was tried before that and produced an unstable shape_mid_ratio;
# it also wouldn't have actually restored the product form, since nothing
# forces two independently-fit knot families to be proportional.)
#
# Instead, shape_mid_ratio is treated as what it is: the one nuisance
# parameter of a genuinely multiplicative model, profiled by grid search
# (SHAPE_MID_RATIO_COARSE_GRID below -- coarse then a fine pass around the
# coarse winner, both fixed and deterministic, no adaptive optimizer). For each
# candidate ratio r, dt's ENTIRE contribution collapses to a single scalar
# s(dt; r) = w1*r + w2, and the amplitude family (state FE aside) is fit as
# s * [1, adv, atk, adv*atk] -- one shared logit_lift(adv, side), scaled by
# s, at every dt. The r minimising training deviance is shape_mid_ratio; the
# amplitude coefficients at that r are intercept_atk/slope_atk/etc. below.
# By construction shape(dt) * logit_lift(adv, side) now IS the fitted linear
# predictor at every dt, not an approximation of it.

import numpy as np

from app.services.stats_math import back_transform, fit_logistic, predict_proba, standardize, weighted_log_loss

_DEGENERATE_THETA2_FALLBACK = 0.5  # see shape_mid_ratio's docstring

# Profiled over shape_mid_ratio (see the block comment above). Coarse pass
# fixed at step 0.05 over a wide range; the fine pass re-centres on the
# coarse winner at step 0.005 across +/-0.05. Both grids are fixed ahead of
# any run, per this repo's predeclared-values discipline -- neither widens
# or shifts based on a result.
SHAPE_MID_RATIO_COARSE_GRID: tuple[float, ...] = tuple(
    round(v, 2) for v in np.arange(-3.0, 3.0 + 1e-9, 0.05)
)
_SHAPE_MID_RATIO_FINE_STEP = 0.005
_SHAPE_MID_RATIO_FINE_RADIUS = 0.05


def _clamp_adv(adv: int) -> int:
    return max(-3, min(2, adv))


@dataclass
class PreplantFit:
    intercept_atk: float
    slope_atk: float
    intercept_def: float
    slope_def: float
    shape_mid_ratio: float
    state_effects: dict[str, float]
    include_side_interaction: bool
    n_observations: int

    def logit_lift(self, adv: int, is_attacker: bool) -> float:
        adv = _clamp_adv(adv)
        if is_attacker:
            return self.intercept_atk + self.slope_atk * adv
        return self.intercept_def + self.slope_def * adv

    def shape(self, dt: float) -> float:
        """The fitted proximity curve, normalised to peak at exactly 1.0 at
        the plateau (dt <= 10), 0 at dt >= 30. shape_mid_ratio (the fitted
        value at dt=20, relative to the plateau) is a real fitted parameter,
        not assumed linear."""
        w1, w2 = shape_basis(dt)
        return w1 * self.shape_mid_ratio + w2


def _amplitude_design_row(o: PreplantKillObservation, r: float, include_side_interaction: bool) -> list[float]:
    """s(dt; r) = w1*r + w2 -- dt's entire contribution collapses to one
    scalar at candidate ratio r, shared by every column below. This is what
    makes the fit a literal product shape(dt) * logit_lift(adv, side)."""
    w1, w2 = shape_basis(o.dt)
    s = w1 * r + w2
    adv_c = _clamp_adv(o.adv)
    atk = 1.0 if o.is_attacker else 0.0
    row = [s, s * adv_c]
    if include_side_interaction:
        row += [s * atk, s * adv_c * atk]
    return row


def _fit_at_ratio(usable, other_states, labels, r, include_side_interaction):
    rows = [
        [1.0 if o.exact_state == st else 0.0 for st in other_states]
        + _amplitude_design_row(o, r, include_side_interaction)
        for o in usable
    ]
    X = np.array(rows, dtype=float)
    # Standardize before ridge-penalized fitting, then back-transform to raw
    # units. Without this, fit_logistic's uniform l2 penalty shrinks columns
    # unevenly by their natural scale (0/1 state dummies vs. shape*adv
    # products spanning several units) -- exactly the trap
    # win_probability.py's ValueModel docstring warns about, and the cause
    # of an early, wildly wrong shape_mid_ratio caught while running this
    # against the real DB (Task 7).
    scaled, _, centre, scale = standardize(X, X)
    beta_scaled = fit_logistic(scaled, labels, l2=1.0)
    beta = back_transform(beta_scaled, centre, scale)
    deviance = weighted_log_loss(predict_proba(beta, X), labels)
    return beta, deviance


def fit_preplant_time_model(
    observations: list[PreplantKillObservation], include_side_interaction: bool = True,
) -> PreplantFit:
    """Logistic regression of round-win on shape(dt) x adv x side, plus
    exact pre-kill state fixed effects (spec, 'Weights and errors'). One row
    per observation; hand-rolled IRLS (app.services.stats_math.fit_logistic)
    since this repo has no statsmodels/scipy dependency.

    shape_mid_ratio is profiled by grid search (see the module-level comment
    above `fit_preplant_time_model`'s definition) rather than fit as an
    independent coefficient and divided out after the fact -- that produced
    Bug B (docs/superpowers/2026-09-08-preplant-dip-independent-verification.md
    section 7): the reconstructed shape(dt) * logit_lift(adv, side) disagreed
    with the actual fitted linear predictor by up to 3.2 logits away from the
    plateau. Profiling fits the product directly, so no reconstruction step
    exists to disagree."""
    usable = [o for o in observations if o.round_won_by_killer_team is not None]
    states = sorted({o.exact_state for o in usable})
    reference_state = "5v5" if "5v5" in states else (states[0] if states else None)
    other_states = [s for s in states if s != reference_state]
    n_state = len(other_states)

    labels_list = [1.0 if o.round_won_by_killer_team else 0.0 for o in usable]
    if not usable or len(set(labels_list)) < 2:
        width = n_state + (4 if include_side_interaction else 2)
        beta = np.zeros(width + 1)
        shape_mid_ratio = _DEGENERATE_THETA2_FALLBACK
    else:
        labels = np.array(labels_list)

        best_r, best_beta, best_deviance = None, None, float("inf")
        for r in SHAPE_MID_RATIO_COARSE_GRID:
            beta_r, deviance = _fit_at_ratio(usable, other_states, labels, r, include_side_interaction)
            if deviance < best_deviance:
                best_r, best_beta, best_deviance = r, beta_r, deviance

        n_fine = round(_SHAPE_MID_RATIO_FINE_RADIUS / _SHAPE_MID_RATIO_FINE_STEP)
        fine_grid = [round(best_r + i * _SHAPE_MID_RATIO_FINE_STEP, 4) for i in range(-n_fine, n_fine + 1)]
        for r in fine_grid:
            beta_r, deviance = _fit_at_ratio(usable, other_states, labels, r, include_side_interaction)
            if deviance < best_deviance:
                best_r, best_beta, best_deviance = r, beta_r, deviance

        shape_mid_ratio = float(best_r)
        beta = best_beta

    idx = 1 + n_state  # skip intercept + state dummies
    # Column order after idx: s, s*adv, [s*atk, s*adv*atk] -- the single
    # amplitude family shared by every dt, per _amplitude_design_row.
    amp_intercept = beta[idx + 0]
    amp_slope_adv = beta[idx + 1]
    if include_side_interaction:
        amp_atk = beta[idx + 2]
        amp_adv_atk = beta[idx + 3]
    else:
        amp_atk = 0.0
        amp_adv_atk = 0.0

    intercept_def = amp_intercept
    intercept_atk = amp_intercept + amp_atk
    slope_def = amp_slope_adv
    slope_atk = amp_slope_adv + amp_adv_atk

    state_effects = {reference_state: 0.0} if reference_state else {}
    for i, s in enumerate(other_states):
        state_effects[s] = float(beta[1 + i])

    return PreplantFit(
        intercept_atk=float(intercept_atk), slope_atk=float(slope_atk),
        intercept_def=float(intercept_def), slope_def=float(slope_def),
        shape_mid_ratio=shape_mid_ratio,
        state_effects=state_effects, include_side_interaction=include_side_interaction,
        n_observations=len(usable),
    )
