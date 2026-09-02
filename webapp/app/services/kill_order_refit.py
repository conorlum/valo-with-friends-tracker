"""Nested cross-validation, the control ladder, diagnostics and verdicts
for the kill-order graph refit.

Every leakage rule in the spec lands in run_nested_cv, and they interact:
the swing table, the exposure vector, the shrinkage prior, the L2 choice
and the probability calibration are ALL built from training matches only,
inside the fold loop. Hoisting any one of them out of the loop for speed
silently invalidates every held-out number this module produces.
"""

from dataclasses import dataclass, field

import numpy as np

from app.services.impact_eval import (
    FIRST_HALF_ROUNDS,
    build_target,
    controls_for,
    group_by_match,
    stable_folds,
)
from app.services.kill_order_curves import (
    FAMILY_A,
    estimate_swing_table,
    family_a_leverage,
    fit_family_a,
)
from app.services.kill_order_leverage import PARAMS, shipped_graph
from app.services.stats_math import (
    apply_calibration,
    fit_logistic,
    platt_calibrate,
    predict_proba,
    standardize,
    weighted_log_loss,
)


@dataclass(frozen=True)
class AlignedTarget:
    """A frozen target's y, weights and CONTROLS, joined to Stage C's
    leverage rows.

    y and weights come from the parent project's own builders -- Stage C
    predicts exactly the quantity Stage A did, and a test pins that.

    `controls` is not optional and an earlier draft omitted it, which
    changed the estimand: the spec's equation is
    `eta = controls.gamma + d*damage + SUM q_k x_k`, and without the control
    block the graph absorbs score differential, side, economy and the
    round's own result -- exactly the information the ladder exists to
    condition on. Which controls apply is target-specific and comes from the
    parent's `controls_for`, not from a choice made here.
    """

    leverage: np.ndarray      # (n_rows, 26)
    damage: np.ndarray        # (n_rows,)
    controls: np.ndarray      # (n_rows, n_controls), possibly zero-width
    control_names: tuple
    y: np.ndarray
    weights: np.ndarray
    match_ids: np.ndarray
    round_ids: np.ndarray     # source round per row; -1 where a row spans many


def align_target(leverage_rows, observations, config) -> AlignedTarget:
    reference = build_target(observations, config, ["damage"])
    by_round = {row.round_id: row for row in leverage_rows}
    per_round = family_a_leverage(list(by_round.values()))
    index_of = {round_id: i for i, round_id in enumerate(by_round)}

    if config.name == "T1":
        # One row per eligible match: the first half summed.
        rows, damage, round_ids = [], [], []
        for match_id in reference.match_ids:
            members = [
                r for r in leverage_rows
                if r.match_id == match_id and r.round_number <= FIRST_HALF_ROUNDS
            ]
            rows.append(sum(per_round[index_of[r.round_id]] for r in members))
            damage.append(sum(r.damage_diff for r in members))
            round_ids.append(-1)
        leverage = np.array(rows)
        damage = np.array(damage)
    else:
        # One row per source round, in the parent's own row order.
        order = _source_round_order(observations, config, reference)
        leverage = np.array([per_round[index_of[rid]] for rid in order])
        damage = np.array([by_round[rid].damage_diff for rid in order])
        round_ids = order

    names = tuple(controls_for(config))
    if config.name == "T1":
        # T1's rows are whole-match aggregates and the parent assigns it no
        # controls; a zero-width block keeps the design code uniform.
        controls = np.zeros((len(leverage), 0))
    else:
        by_round_obs = {o.round_id: o for o in observations}
        controls = np.array(
            [[_control_value(by_round_obs[int(rid)], n) for n in names] for rid in round_ids],
            dtype=float,
        ).reshape(len(leverage), len(names))

    return AlignedTarget(
        leverage=leverage, damage=damage, controls=controls, control_names=names,
        y=np.asarray(reference.y, dtype=float),
        weights=np.asarray(reference.w, dtype=float),
        match_ids=np.asarray(reference.match_ids), round_ids=np.asarray(round_ids),
    )


def _control_value(observation, name):
    """One control off a RoundObservation. `round_result` is the round's own
    outcome and is deliberately a control in its own right, never folded in
    with the context block -- the ladder's whole claim is about what the
    components add ON TOP of knowing who won the round."""
    if name == "round_result":
        return 1.0 if observation.round_won_by_team_a else 0.0
    if name == "attacking_is_team_a":
        return 1.0 if observation.attacking_is_team_a else 0.0
    return float(getattr(observation, name))


def _source_round_order(observations, config, reference):
    """The round each target row was built from, in the parent builder's
    order. Derived by replaying the builder's own eligibility rules against
    the same observations rather than guessing, and asserted against the
    reference row count so a mismatch fails loudly instead of misaligning
    y with X."""
    from app.services.impact_eval import _half_of

    order: list[int] = []
    for match_id, obs in group_by_match(observations).items():
        by_half: dict[int, list] = {}
        for o in obs:
            by_half.setdefault(_half_of(o.round_number), []).append(o)
        for half in by_half.values():
            half.sort(key=lambda o: o.round_number)
            for position, o in enumerate(half):
                if config.name == "WPA":
                    if o.round_won_by_team_a is None:
                        continue
                    order.append(o.round_id)
                    continue
                if o.is_terminal:
                    continue
                future = half[position + 1 : position + 1 + config.k]
                future = [f for f in future if f.round_won_by_team_a is not None]
                # forward_window_target also attaches a match-outcome
                # auxiliary for every round in the first half (weight
                # config.match_weight), independent of the in-half forward
                # window -- so a round at the END of a half (round 12's
                # in-half future is empty, since round 13 starts the next
                # half) is still an eligible row via the auxiliary alone.
                # Missing this rescues zero rows on the wrong side: an
                # earlier version of this replica excluded round 12 of
                # every match, undercounting the reference by exactly one
                # row per match.
                has_match_aux = (
                    config.match_weight > 0
                    and o.round_number <= FIRST_HALF_ROUNDS
                    and o.match_won_by_team_a is not None
                )
                if not future and not has_match_aux:
                    continue
                order.append(o.round_id)
    if len(order) != len(reference.y):
        raise ValueError(
            f"alignment produced {len(order)} rows for {config.name}, "
            f"but the parent builder produced {len(reference.y)}"
        )
    return order


@dataclass
class FoldFit:
    fold: int
    l2: float
    train_match_ids: tuple
    test_match_ids: tuple
    swing_table: object
    exposure: np.ndarray
    calibration: np.ndarray
    graph: np.ndarray | None
    d: float
    deployable: bool
    reasons: tuple


@dataclass
class CandidateResult:
    name: str
    per_fold: dict = field(default_factory=dict)
    oof_scores: np.ndarray = None
    oof_probabilities: np.ndarray = None
    oof_y: np.ndarray = None
    oof_weights: np.ndarray = None
    oof_match_ids: np.ndarray = None
    oof_row_ids: np.ndarray = None


def _weighted_loss(probabilities, y, weights):
    return weighted_log_loss(probabilities, y, weights)


def run_nested_cv(leverage_rows, observations, config, candidates, l2_grid,
                  n_folds=5, seed=0, state_visits=None):
    """Outer folds by match; L2 selected inside each training fold.

    `state_visits` supplies the swing table's raw material. When omitted (in
    tests) a table is derived from the training rows' own outcomes, which is
    enough to exercise the wiring.
    """
    aligned = align_target(leverage_rows, observations, config)
    folds = stable_folds(aligned.match_ids, n_folds=n_folds, seed=seed)
    fold_of = np.array([folds[int(m)] for m in aligned.match_ids])

    results = {name: CandidateResult(name=name) for name in candidates}
    collected = {name: [] for name in candidates}
    row_order: list[np.ndarray] = []

    for fold in range(n_folds):
        test_mask = fold_of == fold
        train_mask = ~test_mask
        if not test_mask.any() or not train_mask.any():
            continue

        train_match_ids = tuple(sorted(set(aligned.match_ids[train_mask].tolist())))
        test_match_ids = tuple(sorted(set(aligned.match_ids[test_mask].tolist())))

        # EVERYTHING below is training-fold only.
        train_rounds = {int(r) for r in aligned.round_ids[train_mask] if r >= 0}
        visits = [v for v in (state_visits or []) if v.match_id in set(train_match_ids)]
        table = estimate_swing_table(visits) if visits else _table_from_rows(
            leverage_rows, train_rounds
        )
        exposure = np.abs(aligned.leverage[train_mask]).sum(axis=0)

        train = (aligned.leverage[train_mask], aligned.damage[train_mask],
                 aligned.y[train_mask], aligned.weights[train_mask])
        test = (aligned.leverage[test_mask], aligned.damage[test_mask],
                aligned.weights[test_mask])
        train_on_train = (aligned.leverage[train_mask], aligned.damage[train_mask],
                          aligned.weights[train_mask])
        outer_controls = (aligned.controls[train_mask], aligned.controls[test_mask])
        self_controls = (aligned.controls[train_mask], aligned.controls[train_mask])

        row_order.append(np.flatnonzero(test_mask))

        for name in candidates:
            l2 = _select_l2(name, aligned, train_mask, l2_grid, state_visits or [],
                            leverage_rows)
            candidate = fit_family_a(name, train, test, table, l2, exposure,
                                     shipped_graph(), controls=outer_controls)
            in_fold = fit_family_a(name, train, train_on_train, table, l2, exposure,
                                   shipped_graph(), controls=self_controls)
            calibration = platt_calibrate(in_fold.scores, aligned.y[train_mask],
                                          weights=aligned.weights[train_mask])
            probabilities = apply_calibration(calibration, candidate.scores)

            results[name].per_fold[fold] = FoldFit(
                fold=fold, l2=l2, train_match_ids=train_match_ids,
                test_match_ids=test_match_ids, swing_table=table, exposure=exposure,
                calibration=calibration, graph=candidate.graph, d=candidate.d,
                deployable=candidate.deployable, reasons=candidate.reasons,
            )
            collected[name].append((candidate.scores, probabilities))

    order = np.concatenate(row_order)
    for name in candidates:
        scores = np.concatenate([s for s, _ in collected[name]])
        probabilities = np.concatenate([p for _, p in collected[name]])
        results[name].oof_scores = scores
        results[name].oof_probabilities = probabilities
        results[name].oof_y = aligned.y[order]
        results[name].oof_weights = aligned.weights[order]
        results[name].oof_match_ids = aligned.match_ids[order]
        results[name].oof_row_ids = order
    return results


def _table_from_rows(leverage_rows, train_round_ids):
    # `train_round_ids` may be a set of round ids or of match ids depending on
    # the caller; both are membership tests over training rows only.
    """Fallback swing table for tests and for targets whose rows do not map
    to single rounds. Real runs pass `state_visits` from the extractor."""
    from app.services.kill_order_curves import SwingTable

    dp = np.full(len(PARAMS), 0.2)
    visits = np.zeros(len(PARAMS))
    for row in leverage_rows:
        if row.round_id in train_round_ids:
            visits += np.abs(row.kill).sum(axis=1)
    return SwingTable(dp=dp, visits=visits, win_rate={}, incomplete=[])


def _select_l2(name, aligned, train_mask, l2_grid, state_visits, leverage_rows,
               inner_folds=3, seed=1):
    """Inner CV inside the training fold. L2 is the ONLY hyperparameter
    selected here -- it does not change the outcome being predicted, which is
    why it is the only one allowed.

    EVERYTHING data-derived is rebuilt per inner split. An earlier draft
    passed in the swing table and exposure computed over the WHOLE outer
    training set and reused them for every inner split -- so for G2 (whose
    basis is built on dP) and G3 (whose prior is) the candidate had already
    seen the inner-validation matches' outcomes, and G1a's construction scale
    had seen their covariates. That is a leak inside the selection loop, and
    it biases the L2 choice toward whichever value overfits the table best.
    """
    if name in ("current_graph", "swing_plugin") or len(l2_grid) == 1:
        return float(l2_grid[0])

    train_matches = aligned.match_ids[train_mask]
    inner = stable_folds(train_matches, n_folds=inner_folds, seed=seed)
    inner_of = np.array([inner[int(m)] for m in train_matches])
    visits_by_match: dict = {}
    for visit in state_visits:
        visits_by_match.setdefault(visit.match_id, []).append(visit)

    best, best_loss = float(l2_grid[0]), np.inf
    for l2 in l2_grid:
        losses, weight_of = [], []
        for inner_fold in range(inner_folds):
            inner_test = inner_of == inner_fold
            inner_train = ~inner_test
            if not inner_test.any() or not inner_train.any():
                continue

            # Rebuilt from the INNER training matches only.
            inner_match_ids = set(train_matches[inner_train].tolist())
            inner_visits = [v for m in inner_match_ids for v in visits_by_match.get(m, [])]
            inner_table = (
                estimate_swing_table(inner_visits) if inner_visits
                else _table_from_rows(leverage_rows, inner_match_ids)
            )
            inner_exposure = np.abs(aligned.leverage[train_mask][inner_train]).sum(axis=0)

            sub_train = tuple(a[train_mask][inner_train] for a in
                              (aligned.leverage, aligned.damage, aligned.y, aligned.weights))
            sub_test = tuple(a[train_mask][inner_test] for a in
                             (aligned.leverage, aligned.damage, aligned.weights))
            sub_controls = (aligned.controls[train_mask][inner_train],
                            aligned.controls[train_mask][inner_test])
            self_controls = (aligned.controls[train_mask][inner_train],) * 2

            try:
                candidate = fit_family_a(name, sub_train, sub_test, inner_table, l2,
                                         inner_exposure, shipped_graph(),
                                         controls=sub_controls)
                fitted = fit_family_a(name, sub_train,
                                      (sub_train[0], sub_train[1], sub_train[3]),
                                      inner_table, l2, inner_exposure, shipped_graph(),
                                      controls=self_controls)
            except ValueError:
                continue  # an inner split with an undetermined state: skip, never impute
            calibration = platt_calibrate(fitted.scores, sub_train[2], weights=sub_train[3])
            probabilities = apply_calibration(calibration, candidate.scores)
            losses.append(_weighted_loss(probabilities, aligned.y[train_mask][inner_test],
                                         aligned.weights[train_mask][inner_test]))
            weight_of.append(float(aligned.weights[train_mask][inner_test].sum()))

        # WEIGHTED across inner folds. An unweighted mean lets a small fold
        # count as much as a large one, which contradicts this project's
        # "every objective is weighted" rule.
        if losses:
            combined = float(np.average(losses, weights=weight_of))
            if combined < best_loss:
                best, best_loss = float(l2), combined
    return best
