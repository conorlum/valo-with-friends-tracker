"""Turns match data into ONE differential observation per round, then fits
and scores candidate Impact weightings against forward-looking targets
under nested cross-validation.

Internal tooling only -- nothing here is imported by app/main.py, any
router, or any template. See
docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md.

Observation unit is one row per round with team-A-minus-team-B features.
The two (round, team) rows of a round have perfectly complementary
outcomes, so treating them as two observations would double every
apparent sample size.
"""

import hashlib
from dataclasses import dataclass

import numpy as np

from app.models.match import Team
from app.scoring.impact import FACTOR_WEIGHTS, FULL_BUY_THRESHOLD
from app.services.map_side_stats import attacking_team_for_round

SURRENDER_SUFFIX = "Surrendered Win"


class MissingImpactRows(Exception):
    """Raised when a playable round has no impact rows. Never swallowed into
    a zero-valued observation -- absent data is not zero impact."""


@dataclass
class RoundObservation:
    match_id: int
    round_id: int
    round_number: int

    # Component differentials (team A minus team B).
    damage: float
    econ_impact: float
    time_impact: float
    swing_impact: float

    # The single kill baseline: kills_A - kills_B. Deaths are ~redundant
    # (deaths_A == kills_B in 99.1% of rounds in this DB), so carrying them
    # separately would be the same column twice.
    kill_diff: float

    # Raw Average Combat Score differential (RoundPlayerStat.score, team A
    # minus team B, summed over the round) -- the simplest possible baseline.
    # If the hand-tuned Impact formula can't beat plain ACS, that is a much
    # sharper finding than "it's about the same as kill differential."
    acs_diff: float

    # The EXACT stored/calculated impact differential, carried alongside the
    # components rather than reconstructed from them. impact.py round()s
    # kill_impact, death_impact and each component independently, so
    # rebuilding "current Impact" from the four component columns accumulates
    # a couple of points of error per player-round -- across 10 players and
    # ~21 rounds that is enough to move a close comparison. The
    # current_impact candidate reads this field directly.
    impact_diff: float

    # Controls. Score is BEFORE this round, economy is at the START of this
    # round, side is DURING this round, and the round's own result is kept
    # as its own separate control -- never folded into the others.
    score_diff_before: int
    attacking_is_team_a: bool
    loadout_diff: float
    full_buy_count_diff: int

    # Outcomes.
    round_won_by_team_a: bool | None
    match_won_by_team_a: bool | None
    is_terminal: bool


def _winner_is_team_a(outcome: str | None) -> bool | None:
    if not outcome or outcome.endswith(SURRENDER_SUFFIX):
        return None
    if outcome.startswith("Team A"):
        return True
    if outcome.startswith("Team B"):
        return False
    return None


def _match_won_by_team_a(match) -> bool | None:
    """None for a tie -- excluded from every denominator, matching
    match_win()'s contract in app.services.player_profile_types."""
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    return match.team1_rounds_won > match.team2_rounds_won


def build_observations_for_match(match, calculated_rows) -> list[RoundObservation]:
    """`calculated_rows` are CalculatedImpact objects from
    build_impact_rows_for_match for this match only. Surrender placeholder
    rounds are dropped -- nobody played them."""
    team_by_mp = {
        mp.id: (mp.team.value if hasattr(mp.team, "value") else mp.team)
        for mp in match.match_players
    }
    team_a = Team.TEAM_1.value

    def team_of(match_player_id: int) -> str:
        # An unknown id silently defaulting to "not team A" would quietly
        # assign a stranger's kills and impact to team B.
        if match_player_id not in team_by_mp:
            raise MissingImpactRows(
                f"match {match.id}: match_player {match_player_id} is not in this match"
            )
        return team_by_mp[match_player_id]

    impact_by_round: dict[int, dict[str, float]] = {}
    impact_rows_by_round: dict[int, set[int]] = {}
    for row in calculated_rows:
        impact_rows_by_round.setdefault(row.round_id, set()).add(row.match_player_id)
        sign = 1.0 if team_of(row.match_player_id) == team_a else -1.0
        bucket = impact_by_round.setdefault(
            row.round_id,
            {"damage": 0.0, "econ_impact": 0.0, "time_impact": 0.0,
             "swing_impact": 0.0, "impact_diff": 0.0},
        )
        bucket["damage"] += sign * row.damage
        bucket["econ_impact"] += sign * row.econ_impact
        bucket["time_impact"] += sign * row.time_impact
        bucket["swing_impact"] += sign * row.swing_impact
        bucket["impact_diff"] += sign * row.impact

    playable = [
        r for r in sorted(match.rounds, key=lambda r: r.round_number)
        if not (r.outcome or "").endswith(SURRENDER_SUFFIX)
    ]
    match_result = _match_won_by_team_a(match)

    observations: list[RoundObservation] = []
    score_a = score_b = 0
    for index, round_row in enumerate(playable):
        kills_a = kills_b = 0
        loadout_a = loadout_b = 0
        players_a = players_b = 0
        full_buy_a = full_buy_b = 0
        acs_a = acs_b = 0
        for stat in round_row.player_stats:
            if team_of(stat.match_player_id) == team_a:
                kills_a += stat.kills
                loadout_a += stat.loadout
                players_a += 1
                full_buy_a += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0
                acs_a += stat.score or 0
            else:
                kills_b += stat.kills
                loadout_b += stat.loadout
                players_b += 1
                full_buy_b += 1 if stat.loadout >= FULL_BUY_THRESHOLD else 0
                acs_b += stat.score or 0

        # A round with no impact rows would otherwise silently become a
        # "zero impact" observation, which is a data point that says
        # something false. Fail loudly; the loader counts and reports
        # excluded matches.
        if round_row.id not in impact_by_round:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number} has no impact rows"
            )
        # PARTIAL coverage is as corrupting as none: a round scored for 7 of 10
        # players has component totals and full-buy counts that are simply
        # wrong, and would enter the regression looking like a legitimate
        # observation. Every participant with a stat row must also have an
        # impact row, and vice versa.
        stat_ids = {s.match_player_id for s in round_row.player_stats}
        impact_ids = impact_rows_by_round.get(round_row.id, set())
        if stat_ids != impact_ids:
            raise MissingImpactRows(
                f"match {match.id} round {round_row.round_number}: "
                f"{len(stat_ids)} stat rows vs {len(impact_ids)} impact rows"
            )
        impact = impact_by_round[round_row.id]
        won_by_a = _winner_is_team_a(round_row.outcome)

        observations.append(
            RoundObservation(
                match_id=match.id,
                round_id=round_row.id,
                round_number=round_row.round_number,
                damage=impact["damage"],
                econ_impact=impact["econ_impact"],
                time_impact=impact["time_impact"],
                swing_impact=impact["swing_impact"],
                impact_diff=impact["impact_diff"],
                kill_diff=kills_a - kills_b,
                acs_diff=acs_a - acs_b,
                score_diff_before=score_a - score_b,
                attacking_is_team_a=attacking_team_for_round(round_row.round_number) == Team.TEAM_1,
                # TEAM-AVERAGE, not sum: a sum silently encodes how many
                # player-stat rows a round happens to have, so a round
                # missing a player would read as a poorer economy.
                loadout_diff=(loadout_a / players_a if players_a else 0.0)
                - (loadout_b / players_b if players_b else 0.0),
                full_buy_count_diff=full_buy_a - full_buy_b,
                round_won_by_team_a=won_by_a,
                match_won_by_team_a=match_result,
                is_terminal=index == len(playable) - 1,
            )
        )

        if won_by_a is True:
            score_a += 1
        elif won_by_a is False:
            score_b += 1

    return observations


FIRST_HALF_ROUNDS = 12
SECOND_HALF_END = 24

FEATURE_COMPONENTS = ["damage", "econ_impact", "time_impact", "swing_impact"]
BASELINE_DAMAGE = ["damage"]
BASELINE_KILL_DIFF = ["kill_diff"]
BASELINE_ACS = ["acs_diff"]
# The round's own result is a control in its own right. It is deliberately
# NOT merged into CONTROLS_CONTEXT: the control ladder's whole point is to
# measure what the components add ON TOP of knowing who won the round.
CONTROLS_RESULT = ["round_result"]
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]

# Which nuisance controls belong with which target. DERIVED from the config
# rather than passed in, because the right answer differs per target and a
# caller passing the wrong set produces a plausible-looking but meaningless
# weighting.
#
#   T2  -> result + context. The whole claim is "the components add something
#          beyond knowing who won the round and what the teams could afford
#          next", which is exactly the control ladder's step 3 -> 4. Fitting
#          the weights without round_result would report weights from a
#          different model than the ladder validates.
#   WPA -> context only. round_result IS the WPA label; controlling for the
#          label would be circular.
#   T1  -> none. Its rows are whole-match aggregates, where a summed
#          per-round result control is just the halftime score, and
#          "does first-half Impact predict the match" is the question as
#          asked. Stated explicitly rather than defaulted.
TARGET_CONTROLS = {
    "T1": [],
    "T2": CONTROLS_RESULT + CONTROLS_CONTEXT,
    # T3 predicts future rounds AND the match from round N, so it needs the
    # same control block T2 does. score_diff_before matters more here than it
    # does for T2: it is what stops a late-round row from recovering the match
    # outcome for free off the scoreline.
    "T3": CONTROLS_RESULT + CONTROLS_CONTEXT,
    "WPA": CONTROLS_CONTEXT,
}


def controls_for(config) -> list[str]:
    if config.name not in TARGET_CONTROLS:
        raise ValueError(f"no control set declared for target {config.name!r}")
    return list(TARGET_CONTROLS[config.name])


def _feature_value(obs: RoundObservation, name: str) -> float:
    if name == "round_result":
        return 0.0 if obs.round_won_by_team_a is None else (1.0 if obs.round_won_by_team_a else -1.0)
    if name == "attacking_is_team_a":
        return 1.0 if obs.attacking_is_team_a else 0.0
    return float(getattr(obs, name))


def _row(obs: RoundObservation, feature_names: list[str]) -> list[float]:
    return [_feature_value(obs, name) for name in feature_names]


def _half_of(round_number: int) -> int:
    """1 = first half, 2 = second half, 3 = overtime. Mirrors the boundary
    impact.py:309 already uses -- the economy resets at halftime, so a
    forward window must never cross it."""
    if round_number <= FIRST_HALF_ROUNDS:
        return 1
    if round_number <= SECOND_HALF_END:
        return 2
    return 3


def assign_folds(match_ids, n_folds: int = 5, seed: int = 0) -> dict[int, int]:
    """match_id -> fold index. Folds are assigned by MATCH, never by row:
    two rounds of the same match are not independent, so splitting them
    across folds would leak."""
    unique = sorted(set(int(m) for m in match_ids))
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(unique))
    return {unique[int(pos)]: int(i % n_folds) for i, pos in enumerate(order)}


_FIB64 = 0x9E3779B97F4A7C15  # nearest odd 64-bit int to 2**64 / golden ratio
_MASK64 = (1 << 64) - 1


def stable_folds(match_ids, n_folds: int = 5, seed: int = 0) -> dict[int, int]:
    """match_id -> fold index, independent of what else is in the set.

    assign_folds permutes over the COLLECTION, so adding or excluding a
    single match can move every other match to a different fold even with
    the same seed. That makes a shared Stage A / Stage C yardstick matrix
    silently incomparable. Here the fold comes from match_id alone (plus a
    seed-derived offset), so a match lands in the same fold regardless of
    its neighbours.

    Multiplicative (Fibonacci) hashing, not a cryptographic hash mod
    n_folds. Measured: SHA-256(seed:match_id) mod 5 over 1,151 sequential
    ids -- the realistic shape of this table's primary keys -- splits
    199/207/246/249/250, a range of 51 against this stage's own <10%
    balance tolerance, because a hash mod small n carries O(sqrt(N))
    sampling noise per bucket regardless of hash quality. Multiplying by an
    odd constant near 2**64/phi is a textbook equidistributing hash for
    exactly this shape of input (contiguous or near-contiguous integer
    keys) and lands within 1 of a perfect split on the same 1,151 ids.

    The seed enters as an additive offset derived from SHA-256 of the seed
    alone (not Python's per-process-randomized hash()), so the offset --
    and therefore the whole partition -- is reproducible across restarts
    and unrelated between seeds.

    assign_folds is deliberately left untouched -- the parent project's
    committed results were produced with it, and changing it would move
    published numbers.
    """
    seed_bytes = hashlib.sha256(f"stable_folds_seed:{int(seed)}".encode()).digest()
    offset = int.from_bytes(seed_bytes[:8], "big")
    out: dict[int, int] = {}
    for match_id in {int(m) for m in match_ids}:
        mixed = (((match_id + offset) & _MASK64) * _FIB64) & _MASK64
        out[match_id] = (mixed * int(n_folds)) >> 64
    return out


def dataset_fingerprint(match_ids) -> str:
    """Stable identity for an eligible match SET."""
    unique = sorted({int(m) for m in match_ids})
    payload = ",".join(str(m) for m in unique).encode()
    return f"{len(unique)}:{hashlib.sha256(payload).hexdigest()[:16]}"


def fold_mapping_hash(folds: dict[int, int]) -> str:
    """Stable identity for an ACTUAL match -> fold assignment.

    Not redundant with dataset_fingerprint, and assuming it was is the hole
    this closes: the parent project's results used the permutation-based
    assign_folds, so the same match set can carry a completely different
    assignment. Same fingerprint, different folds, a matrix that looks
    comparable and is not.
    """
    payload = ";".join(f"{int(m)}:{int(f)}" for m, f in sorted(folds.items())).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def group_by_match(observations) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for obs in observations:
        grouped.setdefault(obs.match_id, []).append(obs)
    return grouped


@dataclass
class FitDataset:
    X: np.ndarray
    y: np.ndarray
    w: np.ndarray
    match_ids: np.ndarray
    feature_names: list[str]


@dataclass(frozen=True)
class TargetConfig:
    """A fully-specified target. Passed INTO the CV orchestrator rather than
    used to pre-build a dataset, because each configuration produces
    different rows -- selecting among prebuilt datasets on the reporting
    folds is exactly the optimism this design avoids."""

    name: str
    k: int = 3
    gamma: float = 0.7
    match_weight: float = 1.0

    # T3 only. `match_share` is the fraction of each row's target weight that
    # the MATCH outcome carries, held constant across rows -- see
    # match_primary_target for why that is not the same knob as
    # `match_weight`. `match_rounds_limit`, when set, attaches the match term
    # only for rounds <= that number (T2's N <= 12 rule).
    match_share: float | None = None
    match_rounds_limit: int | None = None

    def target_identity(self) -> tuple:
        """What makes two configs the SAME prediction problem. Two configs
        differing here define different y, so their losses are not
        comparable -- see PRIMARY_T2."""
        return (self.name, self.k, self.gamma, self.match_weight,
                self.match_share, self.match_rounds_limit)


# THE PRIMARY TARGETS ARE FROZEN, NOT SELECTED.
#
# k, gamma and match_weight change the DEFINITION of y, not just how well a
# model predicts a fixed outcome. A smoother target (larger k, higher gamma)
# or one diluted with the more-predictable match result has lower achievable
# entropy, so it wins a log-loss comparison for reasons that have nothing to
# do with whether Impact predicts winning. Selecting among them by their own
# losses would systematically prefer whichever outcome is easiest, and would
# let different outer folds pool predictions of different quantities.
#
# So: one primary target per family, declared up front. The rest are
# SENSITIVITY ANALYSES, compared only on the fixed binary yardsticks -- whose
# labels are identical across configurations -- never on their own losses.
PRIMARY_T1 = TargetConfig(name="T1")
PRIMARY_T2 = TargetConfig(name="T2", k=3, gamma=0.7, match_weight=1.0)
T2_SENSITIVITY_GRID = [
    TargetConfig(name="T2", k=k, gamma=g, match_weight=m)
    for k in (2, 3, 4)
    for g in (0.5, 0.7, 0.9)
    for m in (0.0, 0.5, 1.0)
]


def _empty_dataset(feature_names: list[str]) -> FitDataset:
    return FitDataset(
        X=np.zeros((0, len(feature_names))), y=np.zeros(0), w=np.zeros(0),
        match_ids=np.zeros(0, dtype=int), feature_names=list(feature_names),
    )


def _dataset(rows, ys, ws, mids, feature_names) -> FitDataset:
    if not rows:
        return _empty_dataset(feature_names)
    return FitDataset(
        np.array(rows, dtype=float), np.array(ys, dtype=float), np.array(ws, dtype=float),
        np.array(mids, dtype=int), list(feature_names),
    )


def first_half_target(observations, feature_names: list[str]) -> FitDataset:
    """T1: one row per ELIGIBLE match, components summed over rounds 1-12.

    A match missing any genuine first-half round is excluded rather than
    normalised -- a truncated total is not comparable to a full one. 22 of
    1,151 matches in this DB fall short once surrender placeholders are
    removed, so T1's n is 1,129.
    """
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        first_half = [o for o in obs if o.round_number <= FIRST_HALF_ROUNDS]
        # The exact round SET, not just the count: a duplicated round number
        # alongside a missing one would pass a length check while silently
        # double-counting one round and dropping another.
        if {o.round_number for o in first_half} != set(range(1, FIRST_HALF_ROUNDS + 1)):
            continue
        result = first_half[0].match_won_by_team_a
        if result is None:
            continue
        rows.append([sum(_feature_value(o, name) for o in first_half) for name in feature_names])
        ys.append(1.0 if result else 0.0)
        ws.append(1.0)
        mids.append(match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


def forward_window_target(
    observations, feature_names: list[str], k: int = 3, gamma: float = 0.7, match_weight: float = 1.0
) -> FitDataset:
    """T2: ONE collapsed row per non-terminal source round.

    y = weighted mean of the next k in-half round outcomes (weights
    gamma**j), w = the total of those weights. For a weighted
    quasi-binomial fit this is identical to expanding into k rows, but it
    keeps n at the true number of source rounds instead of inflating it,
    and makes the match-clustered bootstrap straightforward.

    Windows never cross the halftime reset or the OT boundary -- the same
    rule impact.py:309 encodes. Terminal rounds contribute nothing: they
    have no eligible future. The match-outcome auxiliary is attached only
    for N <= 12, because for later rounds the match result is
    substantially determined by round N.
    """
    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_number = {o.round_number: o for o in obs}
        for o in obs:
            if o.is_terminal:
                continue
            numerator = 0.0
            denominator = 0.0
            for step in range(1, k + 1):
                future = by_number.get(o.round_number + step)
                if future is None or _half_of(future.round_number) != _half_of(o.round_number):
                    break
                if future.round_won_by_team_a is None:
                    continue
                weight = gamma ** (step - 1)
                numerator += weight * (1.0 if future.round_won_by_team_a else 0.0)
                denominator += weight

            if (
                match_weight > 0
                and o.round_number <= FIRST_HALF_ROUNDS
                and o.match_won_by_team_a is not None
            ):
                numerator += match_weight * (1.0 if o.match_won_by_team_a else 0.0)
                denominator += match_weight

            if denominator == 0:
                continue
            rows.append(_row(o, feature_names))
            ys.append(numerator / denominator)
            ws.append(denominator)
            mids.append(match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


def match_primary_target(
    observations, feature_names: list[str], k: int = 3, gamma: float = 0.7,
    match_share: float = 0.67, match_rounds_limit: int | None = None,
) -> FitDataset:
    """T3: winning the MATCH first, winning subsequent ROUNDS second.

    T2 already blends the two, but through `match_weight`, which is an
    absolute weight rather than a share -- and that makes the balance
    invisible. At the frozen k=3, gamma=0.7 the future-round weights sum to
    1 + 0.7 + 0.49 = 2.19, so `match_weight=1.0` gives the match outcome
    1.0/3.19 = **31%** of the target. The whole T2 sensitivity grid swept
    `match_weight` in {0, 0.5, 1.0}, i.e. a match share of 0%, 19% and 31%:
    every target this project has fitted has been round-dominant.

    This builder states the balance directly instead. `match_share` is the
    fraction of each row's target weight carried by the match outcome, and
    it is held CONSTANT across rows by deriving the match weight per row:

        W_r = SUM over available future rounds j of gamma**(j-1)
        m_r = W_r * s / (1 - s)
        y   = (1 - s) * (weighted mean future round win)  +  s * match win
        w   = W_r + m_r  =  W_r / (1 - s)

    Deriving `m_r` per row matters near a half boundary, where fewer future
    rounds are available: a constant `match_weight` there silently raises the
    match share, so rows would not share a target definition. This form keeps
    the share exactly `s` for every row, and reduces to T2 with
    `match_weight=0` at s=0.

    **"Winning the round" here means SUBSEQUENT rounds, never the round the
    impact was scored in.** A round's own kills are, near-deterministically,
    that round's outcome, so scoring impact against it measures nothing --
    the tautology the parent spec is built to avoid. Attribution of the
    current round is Stage B's WPA, a different question with a different
    contract.

    `match_rounds_limit` reproduces T2's "match term only for N <= 12" rule.
    It defaults to OFF here: applying the match term to only some rows makes
    the share inconsistent across the dataset, which defeats the purpose of
    naming a share. The tautology risk that rule guards against is also much
    weaker in this design, because `score_diff_before` is a control -- the
    model already knows the scoreline, so recovering the match outcome from a
    late round's components is not free. It is exposed as a parameter so the
    restriction can be run as a sensitivity rather than assumed either way.
    """
    if not 0.0 <= match_share < 1.0:
        raise ValueError(f"match_share must be in [0, 1), got {match_share}")

    rows, ys, ws, mids = [], [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_number = {o.round_number: o for o in obs}
        for o in obs:
            if o.is_terminal:
                continue
            round_numerator = 0.0
            round_weight = 0.0
            for step in range(1, k + 1):
                future = by_number.get(o.round_number + step)
                if future is None or _half_of(future.round_number) != _half_of(o.round_number):
                    break
                if future.round_won_by_team_a is None:
                    continue
                weight = gamma ** (step - 1)
                round_numerator += weight * (1.0 if future.round_won_by_team_a else 0.0)
                round_weight += weight

            # No future evidence means no row, exactly as in T2. Carrying
            # such a row on the match term alone would give it a different
            # blend from every other row.
            if round_weight == 0:
                continue

            wants_match = (
                match_share > 0
                and (match_rounds_limit is None or o.round_number <= match_rounds_limit)
            )
            if wants_match and o.match_won_by_team_a is None:
                # A tie carries no match signal. Silently dropping just the
                # match term would leave this row round-only, i.e. a
                # different target; drop the row instead.
                continue

            match_component = match_share / (1.0 - match_share) if wants_match else 0.0
            m_r = round_weight * match_component
            numerator = round_numerator + m_r * (1.0 if o.match_won_by_team_a else 0.0)
            denominator = round_weight + m_r

            rows.append(_row(o, feature_names))
            ys.append(numerator / denominator)
            ws.append(denominator)
            mids.append(match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


def build_target(observations, config: TargetConfig, feature_names: list[str], context=None) -> FitDataset:
    """`context` carries a fold-fitted value model for a WPA config (Stage
    B, not yet implemented); T1/T2 ignore it. Accepting it here is what lets
    fit_constrained_weights call this uniformly for every target family."""
    if config.name == "T1":
        return first_half_target(observations, feature_names)
    if config.name == "T2":
        return forward_window_target(
            observations, feature_names, k=config.k, gamma=config.gamma,
            match_weight=config.match_weight,
        )
    if config.name == "T3":
        if config.match_share is None:
            raise ValueError("a T3 config must set match_share")
        return match_primary_target(
            observations, feature_names, k=config.k, gamma=config.gamma,
            match_share=config.match_share,
            match_rounds_limit=config.match_rounds_limit,
        )
    if config.name == "WPA":
        return wpa_target(observations, feature_names, context)
    raise ValueError(f"unknown target: {config.name!r}")


from app.services.win_probability import state_after, state_before, value_of


def wpa_target(observations, feature_names: list[str], context: dict) -> FitDataset:
    """Stage B: label = did team A win this round, weight = leverage.

    Signed dV is in [-1, 1] and is not a probability, so it cannot be the
    `y` of a logistic fit. Using abs(dV) as a SAMPLE WEIGHT instead makes
    the fit care more about high-leverage rounds without pretending a
    signed swing is a likelihood.

    `context["value_beta"]` MUST come from a model fitted on training
    observations only -- see cross_validate's context_builder.
    """
    if not context or "value_beta" not in context:
        raise ValueError("wpa_target requires a context carrying 'value_beta'")
    fallback_model = context["value_beta"]
    # Training rows get an INNER-OOF value model (one that did not see their
    # own match), so the leverage weights used to fit the component weights
    # are not this model's in-sample predictions of the very rows it was fit
    # on. Outer-test rows are absent from this map and fall back to the
    # full-training model, which never saw them either.
    per_match = context.get("value_beta_by_match", {})

    rows, ys, ws, mids = [], [], [], []
    for o in observations:
        if o.round_won_by_team_a is None:
            continue
        model = per_match.get(o.match_id, fallback_model)
        leverage = abs(value_of(model, state_after(o)) - value_of(model, state_before(o)))
        rows.append(_row(o, feature_names))
        ys.append(1.0 if o.round_won_by_team_a else 0.0)
        ws.append(leverage)
        mids.append(o.match_id)
    return _dataset(rows, ys, ws, mids, feature_names)


from app.services.stats_math import (
    apply_calibration,
    auc,
    back_transform,
    cluster_bootstrap_ci,
    fit_logistic,
    paired_bootstrap_delta,
    platt_calibrate,
    predict_proba,
    standardize,
    weighted_log_loss,
)


def paired_oof_log_loss_delta(oof_a: dict, oof_b: dict, draws: int = 200, seed: int = 0):
    """(point, lo, hi) for weighted-log-loss(a) - weighted-log-loss(b), with
    both sides evaluated on the SAME resampled matches each draw.

    Only valid when a and b predict the SAME target -- differing feature
    sets, yes; differing k/gamma/match_weight, no. See PRIMARY_T2.
    """
    combined: dict[int, tuple[list, list]] = {}
    for index, oof in ((0, oof_a), (1, oof_b)):
        for s, y, w, m in zip(oof["scores"], oof["y"], oof["w"], oof["match_ids"]):
            combined.setdefault(int(m), ([], []))[index].append((s, y, w))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss(
                [r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat]
            )

        return fn

    point = weighted_log_loss(oof_a["scores"], oof_a["y"], oof_a["w"]) - weighted_log_loss(
        oof_b["scores"], oof_b["y"], oof_b["w"]
    )
    lo, hi = paired_bootstrap_delta(side(0), side(1), combined, draws=draws, seed=seed)
    return point, lo, hi


@dataclass
class FoldResult:
    fold: int
    train_match_ids: list[int]
    test_match_ids: list[int]
    config: TargetConfig
    l2: float
    beta_raw: np.ndarray
    feature_names: list[str]


def split_observations(observations, folds: dict[int, int], fold: int):
    train = [o for o in observations if folds[o.match_id] != fold]
    test = [o for o in observations if folds[o.match_id] == fold]
    return train, test


def _fit_and_score(train_ds: FitDataset, test_ds: FitDataset, l2: float):
    """Standardize on TRAIN, fit on TRAIN, predict TEST. Returns
    (predictions, raw-unit beta) or None when either side is unusable."""
    if len(train_ds.y) == 0 or len(test_ds.y) == 0:
        return None
    scaled_train, scaled_test, centre, scale = standardize(train_ds.X, test_ds.X)
    beta = fit_logistic(scaled_train, train_ds.y, weights=train_ds.w, l2=l2)
    return predict_proba(beta, scaled_test), back_transform(beta, centre, scale)


def _select_config(train_obs, configs, feature_names, l2_grid, inner_folds: int, seed: int,
                   context_builder=None, fold_fn=assign_folds):
    """Inner CV over TRAINING observations only, selecting L2.

    REFUSES to compare configurations that define different targets. Log
    loss against different y is not a comparison -- see PRIMARY_T2. Pass a
    single frozen target; run alternatives as separate sensitivity runs and
    compare them on the fixed yardsticks instead.

    The target is still rebuilt inside every inner split, because that is
    what lets a target depend on a fold-fitted context (Stage B).

    `fold_fn` defaults to assign_folds (this function's original, published
    behaviour) and is otherwise identical in signature to stable_folds --
    passing the latter is how a re-run shares fold membership with Stage C.
    """
    identities = {c.target_identity() for c in configs}
    if len(identities) > 1:
        raise ValueError(
            "_select_config cannot choose between different target definitions "
            f"({sorted(identities)}): their losses measure different outcomes. "
            "Freeze one target and compare alternatives on a fixed yardstick."
        )
    inner = fold_fn([o.match_id for o in train_obs], n_folds=inner_folds, seed=seed + 1)
    best = (configs[0], l2_grid[0])
    best_loss = float("inf")

    for config in configs:
        for l2 in l2_grid:
            losses, weights = [], []
            for fold in range(inner_folds):
                inner_train, inner_test = split_observations(train_obs, inner, fold)
                if not inner_train or not inner_test:
                    continue
                inner_context = context_builder(inner_train) if context_builder is not None else None
                train_ds = build_target(inner_train, config, feature_names, inner_context)
                test_ds = build_target(inner_test, config, feature_names, inner_context)
                fitted = _fit_and_score(train_ds, test_ds, l2)
                if fitted is None:
                    continue
                preds, _ = fitted
                loss = weighted_log_loss(preds, test_ds.y, test_ds.w)
                if np.isfinite(loss):
                    losses.append(loss)
                    weights.append(float(test_ds.w.sum()))
            if not losses:
                continue
            mean_loss = float(np.average(losses, weights=weights))
            if mean_loss < best_loss:
                best, best_loss = (config, l2), mean_loss
    return best


def cross_validate(
    observations, configs, feature_names, l2_grid,
    n_folds: int = 5, inner_folds: int = 3, seed: int = 0, context_builder=None,
    fold_fn=assign_folds,
) -> dict:
    """Outer CV that receives RAW OBSERVATIONS and a config grid.

    Within each outer fold: select (config, l2) on inner splits of the
    training matches, rebuild the target on the full training set, fit,
    and predict the untouched test matches. Nothing about the test fold
    influences selection, standardization, or fitting.

    `context_builder(train_obs) -> dict`, when given, is called on each
    outer fold's TRAINING observations only, and the result is threaded to
    both the training and test target builds -- this is how a WPA config's
    value model is cross-fit without ever seeing a test match's outcome.

    `fold_fn` defaults to assign_folds, this function's original published
    behaviour -- every existing committed result was produced with it.
    Passing stable_folds instead is how a re-run shares fold membership
    with Stage C's yardstick matrix (see RunIdentity / matrix_is_comparable
    in kill_order_refit.py): assign_folds permutes over the collection, so
    an identical match set can still carry a different assignment under it.
    """
    folds = fold_fn([o.match_id for o in observations], n_folds=n_folds, seed=seed)

    fold_results: list[FoldResult] = []
    scores, ys, ws, mids, baselines = [], [], [], [], []

    for fold in range(n_folds):
        train_obs, test_obs = split_observations(observations, folds, fold)
        if not train_obs or not test_obs:
            continue

        config, l2 = _select_config(
            train_obs, configs, feature_names, l2_grid, inner_folds, seed, context_builder,
            fold_fn=fold_fn,
        )
        context = context_builder(train_obs) if context_builder is not None else None
        train_ds = build_target(train_obs, config, feature_names, context)
        test_ds = build_target(test_obs, config, feature_names, context)
        fitted = _fit_and_score(train_ds, test_ds, l2)
        if fitted is None:
            continue
        preds, beta_raw = fitted

        fold_results.append(
            FoldResult(
                fold=fold,
                train_match_ids=sorted({o.match_id for o in train_obs}),
                test_match_ids=sorted({o.match_id for o in test_obs}),
                config=config,
                l2=l2,
                beta_raw=beta_raw,
                feature_names=list(feature_names),
            )
        )
        scores.extend(preds.tolist())
        ys.extend(test_ds.y.tolist())
        ws.extend(test_ds.w.tolist())
        mids.extend(test_ds.match_ids.tolist())

        # The "knows nothing" comparator, built from the TRAINING half's base
        # rate. Computing one base rate over all pooled OOF labels would let
        # each test fold's own outcomes into its own comparator.
        train_rate = float(np.average(train_ds.y, weights=train_ds.w))
        baselines.extend([train_rate] * len(test_ds.y))

    return {
        "folds": fold_results,
        "oof": {
            "scores": np.array(scores),
            "y": np.array(ys),
            "w": np.array(ws),
            "match_ids": np.array(mids, dtype=int),
            "baseline": np.array(baselines),
        },
    }


def oof_metrics(oof: dict, draws: int = 200, seed: int = 0) -> dict:
    """Weighted log loss ONLY, plus the intercept-only baseline it must beat.

    No AUC here, deliberately. T2's target is a weighted fraction of future
    round wins; rounding it at 0.5 to manufacture a binary label changes the
    estimand and discards the observation weights that gamma and
    match_weight exist to set. AUC belongs to the yardsticks, whose labels
    are genuinely binary.
    """
    if len(oof["y"]) == 0:
        return {"n": 0}
    groups: dict[int, list] = {}
    for s, y, w, m in zip(oof["scores"], oof["y"], oof["w"], oof["match_ids"]):
        groups.setdefault(int(m), []).append((s, y, w))

    def loss_of(sample):
        flat = [r for rows in sample for r in rows]
        return weighted_log_loss([r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat])

    # The "knows nothing" comparator, already computed per fold from TRAINING
    # base rates by cross_validate -- so the improvement below is genuinely
    # out-of-fold and can carry a paired interval.
    fitted_loss = weighted_log_loss(oof["scores"], oof["y"], oof["w"])
    baseline_probs = oof["baseline"]
    baseline_loss = weighted_log_loss(baseline_probs, oof["y"], oof["w"])

    paired: dict[int, tuple[list, list]] = {}
    for s, b, y, w, m in zip(oof["scores"], baseline_probs, oof["y"], oof["w"], oof["match_ids"]):
        entry = paired.setdefault(int(m), ([], []))
        entry[0].append((s, y, w))
        entry[1].append((b, y, w))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss(
                [r[0] for r in flat], [r[1] for r in flat], [r[2] for r in flat]
            )

        return fn

    lo, hi = paired_bootstrap_delta(side(1), side(0), paired, draws=draws, seed=seed)

    return {
        "weighted_log_loss": fitted_loss,
        "weighted_log_loss_ci": list(cluster_bootstrap_ci(loss_of, groups, draws=draws, seed=seed)),
        "intercept_only_log_loss": baseline_loss,
        "improvement_over_intercept": baseline_loss - fitted_loss,
        "improvement_ci": [lo, hi],
        "n": int(len(oof["y"])),
        "matches": len(groups),
        "total_weight": float(np.sum(oof["w"])),
    }


# Normalised so the three factor weights sum to 3, matching the shipped
# FACTOR_WEIGHTS = {"econ": 1.0, "time": 1.0, "swing": 1.0} convention.
FACTOR_WEIGHT_TOTAL = 3.0
DEFAULT_DAMAGE_GRID = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]


@dataclass
class ConstrainedWeights:
    damage_multiplier: float
    econ: float
    time: float
    swing: float
    train_log_loss: float
    # The fitted logistic slope on the composite. MUST be positive for the
    # weighting to mean "higher Impact is better"; see fit_constrained_weights.
    composite_slope: float = float("nan")
    usable: bool = True


def _simplex_grid(step: float):
    """All non-negative (a, b, c) with a + b + c == 1 on a `step` lattice."""
    steps = int(round(1.0 / step))
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            yield (i / steps, j / steps, (steps - i - j) / steps)


def fit_constrained_weights(
    observations, config: TargetConfig, control_names: list[str],
    simplex_step: float = 0.1, damage_grid=None, l2: float | None = None, context=None,
) -> ConstrainedWeights:
    """Search (damage_multiplier, w_econ, w_time, w_swing) under the shipped
    parameterization, WITH the nuisance controls in the design.

    Fitting the composite alone would let the component weights absorb
    variance the controls already explain, so the reported FACTOR_WEIGHTS
    would come from a different model than the control ladder validates.

    MUST be called on training-fold observations only.
    """
    neutral = ConstrainedWeights(1.0, 1.0, 1.0, 1.0, float("nan"), float("nan"), usable=False)
    if not observations:
        return neutral

    feature_names = FEATURE_COMPONENTS + list(control_names)
    # `context` carries a fold-fitted value model for a WPA config; T1/T2
    # ignore it. Passing it here is what lets Stage B produce a constrained
    # candidate on the same footing as T1/T2.
    dataset = build_target(observations, config, feature_names, context)
    if len(dataset.y) == 0:
        return neutral

    component_index = {name: feature_names.index(name) for name in FEATURE_COMPONENTS}
    damage = dataset.X[:, component_index["damage"]]
    factors = np.column_stack(
        [
            dataset.X[:, component_index["econ_impact"]],
            dataset.X[:, component_index["time_impact"]],
            dataset.X[:, component_index["swing_impact"]],
        ]
    )
    controls = (
        dataset.X[:, [feature_names.index(n) for n in control_names]]
        if control_names
        else np.zeros((len(dataset.y), 0))
    )

    # L2 here regularises the CONTROLLED composite design, which is a
    # different model from the feature-only fit whose L2 the outer fold
    # selected. Rather than inherit that value or sweep L2 inside the simplex
    # search (which would multiply the search by the grid size), pick it once
    # from a stand-in composite -- the shipped FACTOR_WEIGHTS -- on the same
    # controlled design, then hold it fixed across the search.
    if l2 is None:
        stand_in = (
            1.0 * damage
            + factors @ (np.array([FACTOR_WEIGHTS["econ"], FACTOR_WEIGHTS["time"],
                                   FACTOR_WEIGHTS["swing"]]) / sum(FACTOR_WEIGHTS.values()))
        )
        design = np.column_stack([controls, stand_in])
        scaled, _, _, _ = standardize(design, design)
        best_l2, best_l2_loss = 1.0, float("inf")
        for candidate_l2 in (0.01, 0.1, 1.0, 10.0):
            beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=candidate_l2)
            loss = weighted_log_loss(predict_proba(beta, scaled), dataset.y, dataset.w)
            if np.isfinite(loss) and loss < best_l2_loss:
                best_l2, best_l2_loss = candidate_l2, loss
        l2 = best_l2

    grid = DEFAULT_DAMAGE_GRID if damage_grid is None else damage_grid
    best = None
    for weights in _simplex_grid(simplex_step):
        factor_score = factors @ np.array(weights)
        for d in grid:
            composite = d * damage + factor_score
            if composite.std() == 0:
                continue
            design = np.column_stack([controls, composite])
            scaled, _, _, _ = standardize(design, design)
            beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=l2)
            loss = weighted_log_loss(predict_proba(beta, scaled), dataset.y, dataset.w)
            if not np.isfinite(loss):
                continue

            # The composite is the LAST design column, so beta[-1] is its
            # slope. A NEGATIVE slope means this weighting predicts well by
            # saying "more Impact, more likely to LOSE". The search would
            # otherwise happily pick it -- the loss is good -- and the
            # deployment proposal would publish non-negative component weights
            # as though higher meant better. Reject it outright.
            if beta[-1] <= 0:
                continue

            key = (loss, d, weights, float(beta[-1]))
            if best is None or key[:3] < best[:3]:
                best = key

    if best is None:
        # Every candidate was anti-predictive (or degenerate). That is a
        # finding, not a weighting: returning neutral weights marked unusable
        # keeps it out of the deployment proposal.
        return ConstrainedWeights(1.0, 1.0, 1.0, 1.0, float("nan"), float("nan"), usable=False)

    loss, d, weights, slope = best
    scaled_weights = [v * FACTOR_WEIGHT_TOTAL for v in weights]
    return ConstrainedWeights(
        damage_multiplier=float(d),
        econ=float(scaled_weights[0]),
        time=float(scaled_weights[1]),
        swing=float(scaled_weights[2]),
        train_log_loss=float(loss),
        composite_slope=float(slope),
        usable=True,
    )


def coefficient_diagnostics(
    observations, config: TargetConfig, feature_names: list[str],
    draws: int = 200, seed: int = 0, l2: float = 1.0,
) -> dict:
    """Collinearity reporting for a fit whose components share a
    multiplicand by construction (impact.py:496-502).

    sign_stability is a REFITTING bootstrap over resampled MATCHES: the
    model is re-fit on each draw. Resampling fixed predictions could not
    say anything about coefficient signs.
    """
    grouped = group_by_match(observations)
    keys = list(grouped)
    if not keys:
        return {"sign_stability": {}, "sign_direction": {}, "correlation_matrix": {},
                "drop_one": {}, "full_log_loss": float("nan"),
                "bootstrap_draws_completed": 0}

    rng = np.random.default_rng(seed)
    positives = np.zeros(len(feature_names))
    completed = 0
    for _ in range(draws):
        picked = rng.integers(0, len(keys), size=len(keys))
        sample = [o for i in picked for o in grouped[keys[int(i)]]]
        dataset = build_target(sample, config, feature_names)
        if len(dataset.y) == 0 or len(np.unique(np.round(dataset.y))) < 2:
            continue
        scaled, _, centre, scale = standardize(dataset.X, dataset.X)
        beta = fit_logistic(scaled, dataset.y, weights=dataset.w, l2=l2)
        positives += (back_transform(beta, centre, scale)[1:] > 0).astype(float)
        completed += 1

    # Direction as well as magnitude: max(pos, neg) alone cannot distinguish
    # "consistently helpful" from "consistently anti-predictive", and those
    # mean opposite things for a component that is supposed to measure impact.
    sign_stability = {
        name: (float(max(p, completed - p) / completed) if completed else float("nan"))
        for name, p in zip(feature_names, positives)
    }
    sign_direction = {
        name: (float(p / completed) if completed else float("nan"))
        for name, p in zip(feature_names, positives)
    }

    full_dataset = build_target(observations, config, feature_names)
    corr = np.corrcoef(full_dataset.X, rowvar=False)
    correlation_matrix = {
        a: {b: float(corr[i][j]) for j, b in enumerate(feature_names)}
        for i, a in enumerate(feature_names)
    }

    # Drop-one is measured in WEIGHTED LOG LOSS on the fixed target, not in
    # AUC over a rounded fractional label. The target is identical across
    # every variant here (only the feature set changes), so the losses ARE
    # comparable -- unlike a comparison across target definitions.
    full = cross_validate(observations, [config], feature_names, [l2], seed=seed)
    full_loss = (
        weighted_log_loss(full["oof"]["scores"], full["oof"]["y"], full["oof"]["w"])
        if len(full["oof"]["y"])
        else float("nan")
    )

    drop_one = {}
    for name in feature_names:
        reduced_names = [n for n in feature_names if n != name]
        if not reduced_names:
            continue
        out = cross_validate(observations, [config], reduced_names, [l2], seed=seed)
        without = (
            weighted_log_loss(out["oof"]["scores"], out["oof"]["y"], out["oof"]["w"])
            if len(out["oof"]["y"])
            else float("nan")
        )
        _, lo, hi = paired_oof_log_loss_delta(out["oof"], full["oof"], draws=draws, seed=seed)
        drop_one[name] = {
            "log_loss_without": without,
            "log_loss_cost_of_dropping": without - full_loss,
            "cost_ci": [lo, hi],
        }

    return {
        "sign_stability": sign_stability,
        "sign_direction": sign_direction,
        "correlation_matrix": correlation_matrix,
        "full_log_loss": full_loss,
        "drop_one": drop_one,
        "bootstrap_draws_completed": completed,
    }


@dataclass
class Candidate:
    """A weighting to be scored. Baselines share the shape of fitted
    weightings so every candidate goes through identical code."""

    name: str
    feature_names: list[str]
    weights: list[float]


# Reads the EXACT impact differential rather than rebuilding it from the four
# components. impact.py round()s kill_impact, death_impact and each component
# independently, so a reconstruction carries a couple of points of error per
# player-round -- across 10 players and ~21 rounds that is enough to move a
# close comparison against a fitted candidate.
CURRENT_IMPACT_CANDIDATE = Candidate(
    name="current_impact",
    feature_names=["impact_diff"],
    weights=[1.0],
)

# ONE kill baseline. kills and deaths as separate columns was the same
# column twice: kills_and_deaths.[1,-1] == kills - deaths algebraically, and
# deaths_A == kills_B in 99.1% of this DB's rounds.
BASELINE_CANDIDATES = [
    Candidate("kill_diff", BASELINE_KILL_DIFF, [1.0]),
    Candidate("damage_only", BASELINE_DAMAGE, [1.0]),
    # Straight ACS -- the sharpest possible baseline. If the hand-tuned
    # Impact formula can't beat this, "about the same as kill diff" was
    # the mild version of the finding.
    Candidate("acs", BASELINE_ACS, [1.0]),
]


def candidate_from_constrained(name: str, weights: ConstrainedWeights) -> Candidate:
    return Candidate(
        name=name,
        feature_names=FEATURE_COMPONENTS,
        weights=[
            weights.damage_multiplier,
            weights.econ / FACTOR_WEIGHT_TOTAL,
            weights.time / FACTOR_WEIGHT_TOTAL,
            weights.swing / FACTOR_WEIGHT_TOTAL,
        ],
    )


def _score_of(observation, candidate: Candidate) -> float:
    return sum(
        weight * _feature_value(observation, name)
        for name, weight in zip(candidate.feature_names, candidate.weights)
    )


def yardstick_first_half(observations, candidate: Candidate):
    """Y1. One row per eligible match: candidate score summed over rounds
    1-12 versus the match result. Not split by side -- every first-half row
    is attack-first for team A, so the other subset is empty."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        first_half = [o for o in obs if o.round_number <= FIRST_HALF_ROUNDS]
        if len(first_half) != FIRST_HALF_ROUNDS or first_half[0].match_won_by_team_a is None:
            continue
        scores.append(sum(_score_of(o, candidate) for o in first_half))
        labels.append(1 if first_half[0].match_won_by_team_a else 0)
        mids.append(match_id)
    return scores, labels, mids


def yardstick_full_match(observations, candidate: Candidate):
    """Y2. Every round. Absolute discrimination is inflated because the
    features contain the outcome's own kills -- read only as the paired gap
    over kill_diff."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        if not obs or obs[0].match_won_by_team_a is None:
            continue
        scores.append(sum(_score_of(o, candidate) for o in obs))
        labels.append(1 if obs[0].match_won_by_team_a else 0)
        mids.append(match_id)
    return scores, labels, mids


def yardstick_forward_rounds(observations, candidate: Candidate):
    """Y3. Round N's score versus who won the majority of rounds N+2 onward
    within the same half. Skipping N+1 keeps the strongest post-round
    mediator out of the label."""
    scores, labels, mids = [], [], []
    for match_id, obs in group_by_match(observations).items():
        by_half: dict[int, list] = {}
        for o in obs:
            by_half.setdefault(_half_of(o.round_number), []).append(o)
        for half_obs in by_half.values():
            half_obs.sort(key=lambda o: o.round_number)
            for index, o in enumerate(half_obs):
                future = [f for f in half_obs[index + 2 :] if f.round_won_by_team_a is not None]
                if not future:
                    continue
                won = sum(1 for f in future if f.round_won_by_team_a)
                if won * 2 == len(future):
                    continue  # an exact split has no majority to predict
                scores.append(_score_of(o, candidate))
                labels.append(1 if won * 2 > len(future) else 0)
                mids.append(match_id)
    return scores, labels, mids


YARDSTICKS = {
    "first_half_to_match": yardstick_first_half,
    "full_match_to_match": yardstick_full_match,
    "forward_rounds": yardstick_forward_rounds,
}


def fold_candidates(
    observations, fold_results, name: str, context_builder=None
) -> dict[int, Candidate]:
    """One constrained weighting per outer fold, fitted on that fold's
    TRAINING matches only. The matrix then applies each to its own test
    matches, so a fitted candidate is never scored on data it saw.

    `context_builder` is required for a WPA config, whose target depends on
    a value model; it is built from the same training observations, so the
    leverage weights never see a test match either.

    Returns (candidates_by_fold, weights_by_fold). The weights are returned,
    not discarded, because "do T1 and T2 agree on the weighting?" is one of
    the questions this whole project exists to answer.
    """
    by_match = group_by_match(observations)
    out: dict[int, Candidate] = {}
    fold_weights: dict[int, ConstrainedWeights] = {}
    for fold in fold_results:
        train_obs = [o for mid in fold.train_match_ids for o in by_match.get(mid, [])]
        context = context_builder(train_obs) if context_builder is not None else None
        # Controls are DERIVED from the target, and L2 is chosen for the
        # controlled design inside fit_constrained_weights -- the outer fold's
        # L2 belongs to a different (feature-only, uncontrolled) model.
        weights = fit_constrained_weights(
            train_obs, fold.config, controls_for(fold.config), context=context
        )
        out[fold.fold] = candidate_from_constrained(name, weights)
        fold_weights[fold.fold] = weights
    return out, fold_weights


def _cell(scores, labels, mids, draws, seed, baseline_fn=None, probs=None):
    """One matrix cell.

    `probs` may be supplied pre-calibrated. That matters for FITTED
    candidates: their pooled scores come from several different per-fold
    models, so calibrating with folds drawn over those pooled scores can put
    a score in the calibration-test set whose own model was trained on the
    match being used to calibrate. The caller therefore calibrates inside
    each outer fold instead (train-match scores -> test-match probabilities)
    and passes the result in.

    When `probs` is None the pooled calibration below is used. That is safe
    only for FIXED candidates -- current_impact and the baselines were never
    fitted to this data at all, so no model saw any of it.
    """
    if not scores:
        return None
    groups: dict[int, list] = {}
    for s, l, m in zip(scores, labels, mids):
        groups.setdefault(int(m), []).append((s, l))

    def auc_of(sample):
        flat = [pair for rows in sample for pair in rows]
        return auc([p[0] for p in flat], [p[1] for p in flat])

    lo, hi = cluster_bootstrap_ci(auc_of, groups, draws=draws, seed=seed)

    scores_arr = np.array(scores, dtype=float)
    labels_arr = np.array(labels, dtype=int)
    if probs is None:
        folds = assign_folds(mids, n_folds=5, seed=seed)
        fold_of = np.array([folds[int(m)] for m in mids])
        probs = np.zeros(len(scores_arr))
        for fold in range(5):
            test = fold_of == fold
            if not test.any():
                continue
            train = ~test
            if not train.any() or len(np.unique(labels_arr[train])) < 2:
                probs[test] = labels_arr[train].mean() if train.any() else 0.5
                continue
            probs[test] = apply_calibration(
                platt_calibrate(scores_arr[train], labels_arr[train]), scores_arr[test]
            )
    else:
        probs = np.asarray(probs, dtype=float)

    prob_groups: dict[int, list] = {}
    for pr, l, m in zip(probs, labels_arr, mids):
        prob_groups.setdefault(int(m), []).append((pr, l))

    def loss_of(sample):
        flat = [pair for rows in sample for pair in rows]
        return weighted_log_loss([p[0] for p in flat], [p[1] for p in flat])

    loss_lo, loss_hi = cluster_bootstrap_ci(loss_of, prob_groups, draws=draws, seed=seed)

    cell = {
        "auc": auc(scores, labels),
        "auc_ci": [lo, hi],
        "log_loss": weighted_log_loss(probs, labels_arr),
        "log_loss_ci": [loss_lo, loss_hi],
        "n": len(labels),
        "matches": len(groups),
    }

    if baseline_fn is not None:
        baseline_scores, baseline_labels, baseline_mids = baseline_fn()
        paired: dict[int, list] = {}
        by_match_candidate: dict[int, list] = {}
        for s, l, m in zip(scores, labels, mids):
            by_match_candidate.setdefault(int(m), []).append((s, l))
        for s, l, m in zip(baseline_scores, baseline_labels, baseline_mids):
            paired.setdefault(int(m), []).append((s, l))
        shared = sorted(set(by_match_candidate) & set(paired))
        combined = {m: (by_match_candidate[m], paired[m]) for m in shared}

        def cand_auc(sample):
            flat = [p for pair in sample for p in pair[0]]
            return auc([p[0] for p in flat], [p[1] for p in flat])

        def base_auc(sample):
            flat = [p for pair in sample for p in pair[1]]
            return auc([p[0] for p in flat], [p[1] for p in flat])

        gap_lo, gap_hi = paired_bootstrap_delta(cand_auc, base_auc, combined, draws=draws, seed=seed)

        # BOTH point estimates on exactly the shared rows the bootstrap used.
        # Taking the candidate's AUC over all its rows and the baseline's over
        # all of ITS rows would compare two different populations, and the
        # resulting point would not sit inside its own interval.
        shared_rows = [combined[m] for m in shared]
        cell["gap_over_kill_diff"] = cand_auc(shared_rows) - base_auc(shared_rows)
        cell["gap_ci"] = [gap_lo, gap_hi]
        cell["auc_on_shared_rows"] = cand_auc(shared_rows)
        cell["baseline_auc_on_shared_rows"] = base_auc(shared_rows)

    return cell


def yardstick_matrix(
    observations, fixed_candidates, per_fold_candidates: dict, folds: dict,
    draws: int = 200, seed: int = 0,
) -> dict:
    """Every candidate x every yardstick.

    `fixed_candidates` are weightings that were never fitted to this data
    (current_impact, baselines) and are scored on all matches.
    `per_fold_candidates` maps a name -> {fold index: Candidate}; each is
    scored ONLY on that fold's test matches, then pooled, so a fitted
    weighting is never evaluated on matches it was fitted on.
    """
    by_match = group_by_match(observations)
    matrix: dict[str, dict] = {}

    for yardstick_name, fn in YARDSTICKS.items():
        matrix[yardstick_name] = {}
        kill_baseline = next(c for c in BASELINE_CANDIDATES if c.name == "kill_diff")

        def baseline_fn(fn=fn):
            return fn(observations, kill_baseline)

        for candidate in fixed_candidates:
            scores, labels, mids = fn(observations, candidate)
            matrix[yardstick_name][candidate.name] = _cell(
                scores, labels, mids, draws, seed,
                baseline_fn=None if candidate.name == "kill_diff" else baseline_fn,
            )

        for name, per_fold in per_fold_candidates.items():
            scores, labels, mids, probs = [], [], [], []
            for fold_index, candidate in per_fold.items():
                fold = folds.get(fold_index)
                if fold is None:
                    continue
                test_obs = [o for mid in fold.test_match_ids for o in by_match.get(mid, [])]
                s, l, m = fn(test_obs, candidate)
                if not s:
                    continue

                # Calibration is fitted INSIDE the fold, on this fold's
                # training matches under this fold's own candidate, then
                # applied once to its test matches. Calibrating over the
                # pooled scores instead would mix models across folds.
                train_obs = [o for mid in fold.train_match_ids for o in by_match.get(mid, [])]
                ts, tl, _ = fn(train_obs, candidate)
                if ts and len(set(tl)) >= 2:
                    fold_probs = apply_calibration(platt_calibrate(ts, tl), s)
                else:
                    fold_probs = np.full(len(s), float(np.mean(tl)) if tl else 0.5)

                scores.extend(s)
                labels.extend(l)
                mids.extend(m)
                probs.extend(np.asarray(fold_probs, dtype=float).tolist())

            matrix[yardstick_name][name] = _cell(
                scores, labels, mids, draws, seed,
                baseline_fn=baseline_fn, probs=probs or None,
            )

    return matrix


from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.models import ImpactScore, Match, MatchPlayer, Round
from app.models.round import RoundPlayerStat
from app.scoring.impact import build_impact_rows_for_match
from app.services.impact_stage0 import PlayerMatch
from app.services.surrender_rounds import NOT_A_SURRENDER_ROUND


def _match_ids(db) -> list[int]:
    return [
        mid
        for (mid,) in db.query(Match.id)
        .join(Round, Round.match_id == Match.id)
        .filter(NOT_A_SURRENDER_ROUND)
        .distinct()
        .all()
    ]


def _hydrated_match(db, match_id):
    return (
        db.query(Match)
        .options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.player_stats),
        )
        .filter(Match.id == match_id)
        .one()
    )


def load_all_observations(db, use_realized_swing: bool = False, report: dict | None = None):
    """Replays every match through the scorer, so components are the
    EX-ANTE variant by default -- the only variant eligible for
    forward-looking fitting. Costs a full replay (minutes).

    A match whose rounds lack impact rows is EXCLUDED and counted, never
    silently turned into zero-impact observations. Pass `report` to receive
    the exclusion count; the CLI prints it.
    """
    observations: list[RoundObservation] = []
    excluded: list[int] = []
    # Surrender placeholder rounds are excluded via _match_ids' NOT_A_SURRENDER_ROUND
    # filter above -- this loader must not hand-roll a second, driftable copy.
    for match_id in _match_ids(db):
        match = _hydrated_match(db, match_id)
        rows = build_impact_rows_for_match(db, match_id, use_realized_swing=use_realized_swing)
        try:
            observations.extend(build_observations_for_match(match, rows))
        except MissingImpactRows:
            excluded.append(match_id)
    if report is not None:
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return observations


def load_stored_observations(db, report: dict | None = None) -> list[RoundObservation]:
    """Reads stored impact_scores directly -- the REALIZED components, as
    the live scorer wrote them. No replay, so Stage 0 and the realized
    yardstick pass are fast. Never use these for a forward-looking fit.

    Exclusions are counted separately from the ex-ante loader's: a match can
    be scored but incompletely, and "how much data did we actually have"
    differs between the two passes."""
    observations: list[RoundObservation] = []
    excluded: list[int] = []
    for match_id in _match_ids(db):
        match = _hydrated_match(db, match_id)
        stored = (
            db.query(ImpactScore)
            .join(Round, Round.id == ImpactScore.round_id)
            .filter(Round.match_id == match_id, NOT_A_SURRENDER_ROUND)
            .all()
        )
        try:
            observations.extend(build_observations_for_match(match, stored))
        except MissingImpactRows:
            excluded.append(match_id)
    if report is not None:
        report["excluded_matches"] = len(excluded)
        report["excluded_match_ids"] = excluded[:20]
    return observations


def load_player_matches(db) -> list[PlayerMatch]:
    """One row per (player, match): their average STORED Impact across the
    match, and whether their team won. Stage 0 describes the shipped
    metric, so stored scores are correct here."""
    rows = (
        db.query(
            MatchPlayer.player_id,
            MatchPlayer.match_id,
            MatchPlayer.team,
            func.avg(ImpactScore.impact).label("avg_impact"),
            Match.team1_rounds_won,
            Match.team2_rounds_won,
        )
        .join(ImpactScore, ImpactScore.match_player_id == MatchPlayer.id)
        .join(Round, Round.id == ImpactScore.round_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(NOT_A_SURRENDER_ROUND)
        .group_by(
            MatchPlayer.player_id, MatchPlayer.match_id, MatchPlayer.team,
            Match.team1_rounds_won, Match.team2_rounds_won,
        )
        .all()
    )

    out: list[PlayerMatch] = []
    for player_id, match_id, team, avg_impact, won1, won2 in rows:
        if won1 == won2:
            continue  # ties excluded from every denominator
        team_value = team.value if hasattr(team, "value") else team
        team_a_won = won1 > won2
        out.append(
            PlayerMatch(
                player_id=player_id,
                match_id=match_id,
                avg_impact=float(avg_impact),
                won=team_a_won if team_value == Team.TEAM_1.value else not team_a_won,
            )
        )
    return out


def load_player_matches_acs(db) -> list[PlayerMatch]:
    """One row per (player, match): their average STORED ACS (RoundPlayerStat.score)
    across the match, and whether their team won.

    User-requested comparison baseline: if the hand-tuned Impact formula
    can't beat plain ACS on the SAME Stage 0 methodology (same cohorts,
    same within-player centering, same CIs), that is a sharper finding
    than "about the same as kill differential." Mirrors load_player_matches
    exactly except for the averaged column, so the two are directly
    comparable through the same stage0_report call.
    """
    rows = (
        db.query(
            MatchPlayer.player_id,
            MatchPlayer.match_id,
            MatchPlayer.team,
            func.avg(RoundPlayerStat.score).label("avg_acs"),
            Match.team1_rounds_won,
            Match.team2_rounds_won,
        )
        .join(RoundPlayerStat, RoundPlayerStat.match_player_id == MatchPlayer.id)
        .join(Round, Round.id == RoundPlayerStat.round_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(NOT_A_SURRENDER_ROUND)
        .group_by(
            MatchPlayer.player_id, MatchPlayer.match_id, MatchPlayer.team,
            Match.team1_rounds_won, Match.team2_rounds_won,
        )
        .all()
    )

    out: list[PlayerMatch] = []
    for player_id, match_id, team, avg_acs, won1, won2 in rows:
        if won1 == won2:
            continue  # ties excluded from every denominator
        team_value = team.value if hasattr(team, "value") else team
        team_a_won = won1 > won2
        out.append(
            PlayerMatch(
                player_id=player_id,
                match_id=match_id,
                avg_impact=float(avg_acs),
                won=team_a_won if team_value == Team.TEAM_1.value else not team_a_won,
            )
        )
    return out
