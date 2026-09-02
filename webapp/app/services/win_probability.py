"""V(state) = P(team A wins the match | state), used by Stage B to weight
rounds by leverage.

FRAMING (see the spec): Stage B DEFINES an impact measure; it is not
independent predictive validation. V(after) - V(before) is dominated by
the round's own outcome, which the round's own kills nearly determine, so
it does not escape the tautology. Its value is that a swing in a close,
late, economically pivotal round is worth more than the same swing in a
decided one.

CROSS-FITTING IS MANDATORY. fit_value_model must be called on TRAINING
observations only, inside each outer fold. A model fitted once on every
match encodes evaluation-match outcomes into the leverage weights, and
calling the downstream fit out-of-fold does not undo that.
"""

from dataclasses import dataclass

import numpy as np

from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.services.stats_math import (
    cluster_bootstrap_ci,
    fit_logistic,
    paired_bootstrap_delta,
    predict_proba,
    standardize,
    weighted_log_loss,
)

ECON_FEATURES = ("loadout_diff", "full_buy_count_diff")


@dataclass
class StateFeatures:
    score_diff: int
    rounds_played: int
    attacking_is_team_a: bool
    is_terminal: bool = False
    terminal_result: float | None = None
    loadout_diff: float = 0.0
    full_buy_count_diff: float = 0.0
    # False for an after-state: round N+1's pre-buy economy is not something
    # this project extracts, and reusing round N's would be a silent lie.
    econ_known: bool = True


def state_before(observation) -> StateFeatures:
    return StateFeatures(
        score_diff=observation.score_diff_before,
        rounds_played=observation.round_number - 1,
        attacking_is_team_a=observation.attacking_is_team_a,
        loadout_diff=observation.loadout_diff,
        full_buy_count_diff=observation.full_buy_count_diff,
    )


def state_after(observation) -> StateFeatures:
    """The state entering the NEXT round.

    Side comes from round N+1, not N: sides swap at the 12->13 boundary and
    alternate every round in overtime, so reusing the current round's side
    is wrong exactly where leverage matters most.

    A terminal round's after-state is the finished match, pinned to the
    actual result rather than extrapolated.
    """
    delta = 0
    if observation.round_won_by_team_a is True:
        delta = 1
    elif observation.round_won_by_team_a is False:
        delta = -1

    if observation.is_terminal:
        result = None
        if observation.match_won_by_team_a is not None:
            result = 1.0 if observation.match_won_by_team_a else 0.0
        return StateFeatures(
            score_diff=observation.score_diff_before + delta,
            rounds_played=observation.round_number,
            attacking_is_team_a=observation.attacking_is_team_a,
            is_terminal=True,
            terminal_result=result,
        )

    next_round = observation.round_number + 1
    # Economy is deliberately NOT carried into the after-state. The
    # observation's loadout is round N's PRE-BUY state; round N+1's economy
    # is a different quantity that this project never extracts. Copying N's
    # economy forward would make an econ-aware V(after) quietly wrong -- and
    # wrong in the direction that flatters econ, since it would look like the
    # economy had not changed. `econ_known=False` makes value_of refuse
    # rather than guess.
    return StateFeatures(
        score_diff=observation.score_diff_before + delta,
        rounds_played=observation.round_number,
        attacking_is_team_a=attacking_team_for_round(next_round) == Team.TEAM_1,
        econ_known=False,
    )


def _design_row(state: StateFeatures, include_econ: bool) -> list[float]:
    row = [
        float(state.score_diff),
        float(state.rounds_played),
        # The interaction is the point: a two-round lead at round 3 and at
        # round 22 are not the same state, and an additive model cannot say so.
        float(state.score_diff) * float(state.rounds_played),
        1.0 if state.attacking_is_team_a else 0.0,
    ]
    if include_econ:
        row.extend([float(state.loadout_diff), float(state.full_buy_count_diff)])
    return row


@dataclass
class ValueModel:
    """Coefficients PLUS the training centre/scale they were fitted under.

    Standardization is not cosmetic here: score_diff spans about +/-13,
    rounds_played 0-24, their interaction a few hundred, and loadout_diff tens
    of thousands. A single ridge penalty applied to raw columns would shrink
    those wildly unevenly -- and would make the econ-increment comparison
    unfair in exactly the direction that buries econ, since its columns are
    the largest and so the most penalised.
    """

    beta: np.ndarray
    centre: np.ndarray
    scale: np.ndarray
    include_econ: bool


def fit_value_model(observations, l2: float = 1.0, include_econ: bool = False) -> ValueModel:
    """MUST be called inside each outer training fold. Fitting once over all
    matches and then running outer CV leaks evaluation outcomes into the
    leverage weights."""
    rows, labels = [], []
    for o in observations:
        if o.match_won_by_team_a is None:
            continue
        rows.append(_design_row(state_before(o), include_econ))
        labels.append(1.0 if o.match_won_by_team_a else 0.0)
    width = 4 + (2 if include_econ else 0)
    if not rows or len(set(labels)) < 2:
        return ValueModel(np.zeros(width + 1), np.zeros(width), np.ones(width), include_econ)

    X = np.array(rows, dtype=float)
    scaled, _, centre, scale = standardize(X, X)
    beta = fit_logistic(scaled, np.array(labels), l2=l2)
    return ValueModel(beta, centre, scale, include_econ)


def value_of(model: ValueModel, state: StateFeatures) -> float:
    """Terminal states short-circuit: the match is decided, so its value is
    exactly 1 or 0, not a model extrapolation.

    Applies the model's OWN training centre/scale, so a test state is
    transformed exactly as the training states were.
    """
    if state.is_terminal and state.terminal_result is not None:
        return float(state.terminal_result)
    if model.include_econ and not state.econ_known:
        raise ValueError(
            "econ-aware V(state) cannot be evaluated on an after-state: round "
            "N+1's pre-buy economy is not extracted. Either use the base model "
            "for leverage, or extract genuine next-round economy first."
        )
    row = np.array([_design_row(state, model.include_econ)], dtype=float)
    return float(predict_proba(model.beta, (row - model.centre) / model.scale)[0])


def econ_increment_report(observations, n_folds: int = 5, seed: int = 0) -> dict:
    """The spec's measured econ step: held-out log loss WITHOUT econ state
    versus WITH it. The delta is the quantitative answer to 'how much does
    econ carryover actually matter'."""
    from app.services.impact_eval import assign_folds, split_observations

    folds = assign_folds([o.match_id for o in observations], n_folds=n_folds, seed=seed)

    def held_out(include_econ: bool):
        rows = []
        for fold in range(n_folds):
            train, test = split_observations(observations, folds, fold)
            if not train or not test:
                continue
            model = fit_value_model(train, include_econ=include_econ)
            for o in test:
                if o.match_won_by_team_a is None:
                    continue
                rows.append(
                    (o.match_id, value_of(model, state_before(o)),
                     1.0 if o.match_won_by_team_a else 0.0)
                )
        return rows

    base_rows = held_out(False)
    econ_rows = held_out(True)
    if not base_rows or not econ_rows:
        return {"base_log_loss": float("nan"), "with_econ_log_loss": float("nan"),
                "delta": float("nan"), "delta_ci": [float("nan"), float("nan")]}

    def loss(rows):
        return weighted_log_loss([r[1] for r in rows], [r[2] for r in rows])

    # Paired by match: both models are scored on the SAME resampled matches
    # each draw, so the interval is for the DIFFERENCE rather than being two
    # independent intervals the reader has to eyeball.
    combined: dict[int, tuple[list, list]] = {}
    for index, rows in ((0, base_rows), (1, econ_rows)):
        for match_id, prob, label in rows:
            combined.setdefault(int(match_id), ([], []))[index].append((prob, label))

    def side(index):
        def fn(sample):
            flat = [r for pair in sample for r in pair[index]]
            if not flat:
                return float("nan")
            return weighted_log_loss([r[0] for r in flat], [r[1] for r in flat])

        return fn

    lo, hi = paired_bootstrap_delta(side(0), side(1), combined, seed=seed)
    base, with_econ = loss(base_rows), loss(econ_rows)
    return {
        "base_log_loss": base,
        "with_econ_log_loss": with_econ,
        "delta": base - with_econ,
        "delta_ci": [lo, hi],
        "note": (
            "positive delta = adding econ state improved held-out prediction. "
            "An interval spanning zero means econ state added nothing measurable "
            "to the win-probability model."
        ),
    }
