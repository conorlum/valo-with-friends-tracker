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
    MARGIN,
    N_LATTICE,
    TOTAL_ALIVE,
    effective_surfaces,
    estimate_swing_table,
    family_a_leverage,
    family_b_columns,
    fit_family_a,
    fit_family_b,
)
from app.services.kill_order_leverage import COMPONENTS, PARAMS, shipped_graph
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
    team_rows: tuple = ()     # one TeamLeverageRow per output row -- T1's is synthetic


def _aggregate_rows(rows):
    """One synthetic row standing for a group. Family B's columns are linear
    in the blocks, so summing the blocks and summing the columns agree --
    a test pins that rather than trusting it."""
    from app.services.kill_order_leverage import TeamLeverageRow

    first = rows[0]
    return TeamLeverageRow(
        match_id=first.match_id, round_id=-1, round_number=-1,
        damage_diff=float(sum(r.damage_diff for r in rows)),
        kill=sum(r.kill for r in rows), death=sum(r.death for r in rows),
        death_untraded=sum(r.death_untraded for r in rows),
        terminal_alive_diff=float(rows[-1].terminal_alive_diff),
        total_kills=int(sum(r.total_kills for r in rows)),
    )


def _wpa_context(observations) -> dict:
    """A value model context for wpa_target. See run_nested_cv's WPA branch
    for why an all-data fit is used here rather than a per-fold one."""
    from app.services.win_probability import fit_value_model

    return {"value_beta": fit_value_model(observations)}


def align_target(leverage_rows, observations, config, context=None) -> AlignedTarget:
    """`context` is only meaningful for a WPA config -- see wpa_target and
    _wpa_context below. T1/T2 ignore it."""
    reference = build_target(observations, config, ["damage"], context=context)
    by_round = {row.round_id: row for row in leverage_rows}
    per_round = family_a_leverage(list(by_round.values()))
    index_of = {round_id: i for i, round_id in enumerate(by_round)}

    if config.name == "T1":
        # One row per eligible match: the first half summed.
        rows, damage, round_ids, team_rows = [], [], [], []
        for match_id in reference.match_ids:
            members = [
                r for r in leverage_rows
                if r.match_id == match_id and r.round_number <= FIRST_HALF_ROUNDS
            ]
            rows.append(sum(per_round[index_of[r.round_id]] for r in members))
            damage.append(sum(r.damage_diff for r in members))
            round_ids.append(-1)
            team_rows.append(_aggregate_rows(members))
        leverage = np.array(rows)
        damage = np.array(damage)
    else:
        # One row per source round, in the parent's own row order.
        order = _source_round_order(observations, config, reference)
        leverage = np.array([per_round[index_of[rid]] for rid in order])
        damage = np.array([by_round[rid].damage_diff for rid in order])
        round_ids = order
        team_rows = [by_round[rid] for rid in order]

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
        team_rows=tuple(team_rows),
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
    # Family B only: the fitted weighting over the FIXED graph, and the
    # effective per-(component, side) price surfaces derived from it.
    weights: np.ndarray | None = None
    surfaces: dict | None = None


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
                  n_folds=5, seed=0, state_visits=None, family="A"):
    """Outer folds by match; L2 selected inside each training fold.

    `state_visits` supplies the swing table's raw material. When omitted (in
    tests) a table is derived from the training rows' own outcomes, which is
    enough to exercise the wiring.

    `family` selects the fitter: "A" (fit_family_a, over leverage matrices)
    or "B" (fit_family_b, over team_rows against a FIXED graph). One
    orchestrator handles both so the fold split, the calibration path and
    the identity checks are not duplicated between two near-identical
    functions -- duplicating the leakage rules is the thing most worth not
    doing here.
    """
    if family not in ("A", "B"):
        raise ValueError(f"unknown family {family!r}; expected 'A' or 'B'")

    # WPA needs a fitted value model to weight rows by leverage (see
    # wpa_target). Round INCLUSION doesn't depend on the model's values --
    # only round_won_by_team_a is not None -- so an all-data context is safe
    # for determining row structure. The WEIGHT VALUES it produces are a
    # deliberately simpler stand-in for the parent's fold-specific/inner-OOF
    # value model: WPA feeds only the descriptive target_agreement check
    # here (Task 18), never a primary P1-P4 comparison, so an all-data
    # weighting is a stated, contained simplification rather than a silent
    # one -- a genuinely held-out WPA claim would need the same per-fold
    # refit discipline everything else in this module already has.
    context = _wpa_context(observations) if config.name == "WPA" else None
    aligned = align_target(leverage_rows, observations, config, context=context)
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
            if family == "A":
                l2 = _select_l2(name, aligned, train_mask, l2_grid, state_visits or [],
                                leverage_rows)
                candidate = fit_family_a(name, train, test, table, l2, exposure,
                                         shipped_graph(), controls=outer_controls)
                in_fold = fit_family_a(name, train, train_on_train, table, l2, exposure,
                                       shipped_graph(), controls=self_controls)
                weights = None
                surfaces = None
            else:
                l2 = float(l2_grid[0]) if len(l2_grid) == 1 else _select_l2_b(
                    name, aligned, train_mask, l2_grid
                )
                train_rows = [aligned.team_rows[i] for i in np.flatnonzero(train_mask)]
                test_rows = [aligned.team_rows[i] for i in np.flatnonzero(test_mask)]
                candidate = fit_family_b(
                    name, (train_rows, aligned.y[train_mask], aligned.weights[train_mask]),
                    (test_rows, aligned.weights[test_mask]), shipped_graph(), l2,
                    controls=outer_controls, exposure=exposure,
                )
                in_fold = fit_family_b(
                    name, (train_rows, aligned.y[train_mask], aligned.weights[train_mask]),
                    (train_rows, aligned.weights[train_mask]), shipped_graph(), l2,
                    controls=self_controls, exposure=exposure,
                )
                _, column_names = family_b_columns(train_rows[:1], shipped_graph(), name)
                weights = candidate.weights
                surfaces = effective_surfaces(candidate.weights, column_names, shipped_graph())

            calibration = platt_calibrate(in_fold.scores, aligned.y[train_mask],
                                          weights=aligned.weights[train_mask])
            probabilities = apply_calibration(calibration, candidate.scores)

            results[name].per_fold[fold] = FoldFit(
                fold=fold, l2=l2, train_match_ids=train_match_ids,
                test_match_ids=test_match_ids, swing_table=table, exposure=exposure,
                calibration=calibration, graph=candidate.graph, d=candidate.d,
                deployable=candidate.deployable, reasons=candidate.reasons,
                weights=weights, surfaces=surfaces,
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


def _select_l2_b(name, aligned, train_mask, l2_grid, inner_folds=3, seed=1):
    """Family B's analogue of _select_l2: inner CV inside the training
    fold, over team_rows against the FIXED shipped graph rather than a
    leverage matrix. Family B fits a weighting, not a curve, so there is no
    swing table or exposure-dependent basis to rebuild per inner split --
    only the fold split itself needs to come from training matches only.
    """
    train_matches = aligned.match_ids[train_mask]
    inner = stable_folds(train_matches, n_folds=inner_folds, seed=seed)
    inner_of = np.array([inner[int(m)] for m in train_matches])
    train_rows = [aligned.team_rows[i] for i in np.flatnonzero(train_mask)]

    best, best_loss = float(l2_grid[0]), np.inf
    for l2 in l2_grid:
        losses, weight_of = [], []
        for inner_fold in range(inner_folds):
            inner_test = inner_of == inner_fold
            inner_train = ~inner_test
            if not inner_test.any() or not inner_train.any():
                continue

            sub_train_rows = [r for r, keep in zip(train_rows, inner_train) if keep]
            sub_test_rows = [r for r, keep in zip(train_rows, inner_test) if keep]
            sub_y_train = aligned.y[train_mask][inner_train]
            sub_w_train = aligned.weights[train_mask][inner_train]
            sub_controls_train = aligned.controls[train_mask][inner_train]
            sub_controls_test = aligned.controls[train_mask][inner_test]

            try:
                candidate = fit_family_b(
                    name, (sub_train_rows, sub_y_train, sub_w_train),
                    (sub_test_rows, aligned.weights[train_mask][inner_test]),
                    shipped_graph(), l2, controls=(sub_controls_train, sub_controls_test),
                )
                fitted = fit_family_b(
                    name, (sub_train_rows, sub_y_train, sub_w_train),
                    (sub_train_rows, sub_w_train), shipped_graph(), l2,
                    controls=(sub_controls_train, sub_controls_train),
                )
            except ValueError:
                continue  # an inner split with an undetermined state: skip, never impute
            calibration = platt_calibrate(fitted.scores, sub_y_train, weights=sub_w_train)
            probabilities = apply_calibration(calibration, candidate.scores)
            losses.append(_weighted_loss(probabilities, aligned.y[train_mask][inner_test],
                                         aligned.weights[train_mask][inner_test]))
            weight_of.append(float(aligned.weights[train_mask][inner_test].sum()))

        if losses:
            combined = float(np.average(losses, weights=weight_of))
            if combined < best_loss:
                best, best_loss = float(l2), combined
    return best


from app.services.impact_eval import CONTROLS_CONTEXT, CONTROLS_RESULT
from app.services.stats_math import paired_bootstrap_delta

LADDER_RUNGS = (
    "round_result", "plus_context", "plus_damage",
    "plus_terminal_state", "plus_leverage",
)

_RUNG_COLUMNS = {
    "round_result": list(CONTROLS_RESULT),
    "plus_context": list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT),
    "plus_damage": list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT) + ["damage_diff"],
    "plus_terminal_state": (
        list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT)
        + ["damage_diff", "terminal_alive_diff", "total_kills"]
    ),
    "plus_leverage": (
        list(CONTROLS_RESULT) + list(CONTROLS_CONTEXT)
        + ["damage_diff", "terminal_alive_diff", "total_kills"]
        + [f"leverage:{name}" for name in PARAMS]
    ),
}


def _control_matrix(observations, aligned):
    """The parent's control block, in aligned's row order. controls_for(T2)
    is exactly CONTROLS_RESULT + CONTROLS_CONTEXT, in that order, which is
    exactly the column order _RUNG_COLUMNS declares -- so this is aligned's
    own controls block, unchanged."""
    return aligned.controls


def _rung_design(rung, controls, aligned, extras):
    """The numeric design matrix for one rung, stacked in the same column
    order _RUNG_COLUMNS names them in."""
    if rung == "round_result":
        return controls[:, :1]
    blocks = [controls]
    if rung in ("plus_damage", "plus_terminal_state", "plus_leverage"):
        blocks.append(aligned.damage[:, None])
    if rung in ("plus_terminal_state", "plus_leverage"):
        blocks.append(extras)
    if rung == "plus_leverage":
        blocks.append(aligned.leverage)
    return np.column_stack(blocks)


def control_ladder(leverage_rows, observations, config, n_folds=5, seed=0,
                   l2=1.0, draws=200):
    """Nested models on the frozen target, each knowing more than the last.

    L2 is DELIBERATELY FROZEN at 1.0 across every rung rather than selected.
    The ladder measures what each block of columns ADDS, so every rung must
    face the same penalty: selecting L2 per rung would let a rung win by
    getting a better hyperparameter rather than better information. The
    frozen value is stated here so a reader does not mistake it for an
    oversight, and the report prints it.

    The headline is rung 4 -> 5: what the leverage columns add beyond
    knowing who won the round, what the teams could afford, how much damage
    was done, how the round ended and how many kills it took.
    """
    aligned = align_target(leverage_rows, observations, config)
    by_round = {row.round_id: row for row in leverage_rows}
    extras = np.array([
        [by_round[int(rid)].terminal_alive_diff, by_round[int(rid)].total_kills]
        if int(rid) in by_round else [0.0, 0.0]
        for rid in aligned.round_ids
    ], dtype=float)
    controls = _control_matrix(observations, aligned)

    folds = stable_folds(aligned.match_ids, n_folds=n_folds, seed=seed)
    fold_of = np.array([folds[int(m)] for m in aligned.match_ids])

    predictions: dict[str, np.ndarray] = {}
    for rung in LADDER_RUNGS:
        design = _rung_design(rung, controls, aligned, extras)
        probabilities = np.zeros(len(aligned.y))
        for fold in range(n_folds):
            test = fold_of == fold
            train = ~test
            if not test.any() or not train.any():
                continue
            scaled_train, scaled_test, _c, _s = standardize(design[train], design[test])
            beta = fit_logistic(scaled_train, aligned.y[train],
                                weights=aligned.weights[train], l2=l2)
            probabilities[test] = predict_proba(beta, scaled_test)
        predictions[rung] = probabilities

    report: dict = {"config": config.name}
    for position, rung in enumerate(LADDER_RUNGS):
        entry = {
            "columns": _RUNG_COLUMNS[rung],
            "n_features": len(_RUNG_COLUMNS[rung]),
            "weighted_log_loss": float(
                weighted_log_loss(predictions[rung], aligned.y, aligned.weights)
            ),
        }
        if position:
            previous = LADDER_RUNGS[position - 1]
            entry["delta_from"] = previous
            entry["added_columns"] = [
                c for c in _RUNG_COLUMNS[rung] if c not in _RUNG_COLUMNS[previous]
            ]
            entry["delta"] = entry["weighted_log_loss"] - float(
                weighted_log_loss(predictions[previous], aligned.y, aligned.weights)
            )
        report[rung] = entry

    report["headline"] = _paired_step(
        aligned, predictions, "plus_terminal_state", "plus_leverage", draws, seed
    )
    report["plus_terminal_state"].update(
        {k: v for k, v in _paired_step(
            aligned, predictions, "plus_damage", "plus_terminal_state", draws, seed
        ).items() if k in ("delta", "delta_ci")}
    )
    return report


def _paired_step(aligned, predictions, lower, upper, draws, seed):
    groups: dict[int, list] = {}
    for index, match_id in enumerate(aligned.match_ids):
        groups.setdefault(int(match_id), []).append(index)

    def loss_of(name):
        def inner(sample):
            rows = [i for block in sample for i in block]
            return weighted_log_loss(
                predictions[name][rows], aligned.y[rows], aligned.weights[rows]
            )
        return inner

    low, high = paired_bootstrap_delta(
        loss_of(upper), loss_of(lower), groups, draws=draws, seed=seed
    )
    delta = float(
        weighted_log_loss(predictions[upper], aligned.y, aligned.weights)
        - weighted_log_loss(predictions[lower], aligned.y, aligned.weights)
    )
    return {
        "from": lower, "to": upper, "delta": delta, "delta_ci": [low, high],
        "metric": "weighted log loss, upper minus lower",
        "reading": (
            "negative delta = the added columns IMPROVED held-out prediction. "
            "An interval spanning zero means they added nothing measurable, "
            "which for rung 4 -> 5 is an informative result rather than a failure."
        ),
    }


from app.services.kill_order_curves import normalize_for_display
from app.services.kill_order_leverage import PARAM_INDEX
from app.services.stats_math import cluster_bootstrap_ci

_LATTICE_STATES = [(own, opp) for own in range(1, 6) for opp in range(1, 6)]


def monotonicity_violations(graph) -> list:
    """Within a fixed number of players remaining, weight must be
    non-decreasing as the state gets closer to even.

    REPORTED, never imposed: the shipped table, the measured swing table
    and every prior all satisfy this, so a constraint would bind only where
    the data disagrees with the prior -- exactly where we want to hear from
    the data.
    """
    graph = np.asarray(graph, dtype=float)
    out: list[str] = []
    for own_a, opp_a in _LATTICE_STATES:
        for own_b, opp_b in _LATTICE_STATES:
            if own_a + opp_a != own_b + opp_b:
                continue
            if abs(own_a - opp_a) >= abs(own_b - opp_b):
                continue
            closer = graph[PARAM_INDEX[f"{own_a}v{opp_a}"]]
            further = graph[PARAM_INDEX[f"{own_b}v{opp_b}"]]
            if closer < further:
                out.append(
                    f"{own_a}v{opp_a}={closer:.1f} < {own_b}v{opp_b}={further:.1f} "
                    f"(same total alive, closer to even should not score less)"
                )
    return out


def conditioning_report(leverage) -> dict:
    leverage = np.asarray(leverage, dtype=float)
    usable = leverage.std(axis=0) > 1e-12
    scaled = (leverage[:, usable] - leverage[:, usable].mean(axis=0)) / leverage[:, usable].std(axis=0)
    correlation = np.corrcoef(scaled, rowvar=False)
    # Clip before the log: a genuinely collinear design can produce tiny
    # negative eigenvalues from floating point, and log(share) then returns
    # NaN for the whole report.
    eigenvalues = np.clip(np.linalg.eigvalsh(correlation)[::-1], 1e-12, None)
    share = eigenvalues / eigenvalues.sum()
    off_diagonal = correlation[~np.eye(correlation.shape[0], dtype=bool)]
    return {
        "n_columns": int(usable.sum()),
        "max_abs_correlation": float(np.abs(off_diagonal).max()),
        "condition_number": float(eigenvalues[0] / eigenvalues[-1]),
        "effective_rank": float(np.exp(-(share * np.log(share)).sum())),
        "eigenvalues": [float(v) for v in eigenvalues],
        "vif": [float(v) for v in np.diag(np.linalg.pinv(correlation))],
    }


def per_parameter_report(leverage, exposure) -> dict:
    """Diagnostics only. Deliberately carries NO per-parameter verdict: the
    rejected stability rule lived here, and a 'stable' key reappearing is
    how it would come back."""
    leverage = np.asarray(leverage, dtype=float)
    conditioning = conditioning_report(leverage)
    usable = leverage.std(axis=0) > 1e-12
    vif_by_column = dict(zip(np.flatnonzero(usable).tolist(), conditioning["vif"]))
    out = {}
    for index, name in enumerate(PARAMS):
        out[name] = {
            "exposure": float(np.asarray(exposure, dtype=float)[index]),
            "rounds_touched": int(np.count_nonzero(leverage[:, index])),
            "vif": float(vif_by_column.get(index, float("nan"))),
        }
    return out


def _weighted_rms(values, exposure) -> float:
    exposure = np.asarray(exposure, dtype=float)
    return float(np.sqrt(np.sum(exposure * np.asarray(values, dtype=float) ** 2) / exposure.sum()))


def _fold_bootstrap_ratio(prepared, reference, exposure, draws, seed):
    """DESCRIPTIVE fallback when no refit callback is available: bootstrap
    over WHICH of the (few) fold graphs are resampled, not over matches.

    This is explicitly weaker than the match-clustered refitting bootstrap
    below -- 5 folds sharing 3/5 of their matches pairwise are neither
    independent nor numerous -- so it never sets gate_eligible, but it still
    gives a point estimate and an interval for reporting.
    """
    rng = np.random.default_rng(seed)
    n = len(prepared)
    ratios = []
    for _ in range(draws):
        draw = [prepared[i] for i in rng.integers(0, n, size=n)]
        block = np.array(draw)
        mean = block.mean(axis=0)
        distance = _weighted_rms(mean - reference, exposure)
        if distance <= 0:
            continue
        spread = float(np.mean([_weighted_rms(g - mean, exposure) for g in block]))
        ratios.append(spread / distance)
    if not ratios:
        return {"ratio": float("nan"), "ratio_ci": [float("nan"), float("nan")], "stable": False}
    low, high = np.percentile(ratios, [2.5, 97.5])
    return {
        "ratio": float(np.mean(ratios)),
        "ratio_ci": [float(low), float(high)],
        "stable": bool(np.isfinite(high) and high < 1.0),
    }


def stability_report(result, shipped, exposure, refit=None, match_ids=None,
                     draws=200, seed=0) -> dict:
    """Graph-level stability, as a MATCH-CLUSTERED REFITTING bootstrap.

        ratio = RMS(resampled graph - mean graph) / RMS(mean - shipped)

    stable <=> the UPPER bound of that ratio is below 1: the candidate
    differs from the shipped graph by more than it differs from itself.

    `refit(match_ids) -> graph` refits the candidate on a resampled match
    set. It is REQUIRED when stability gates a success claim. An earlier
    draft bootstrapped the five outer-fold graphs instead -- but five
    overlapping folds (any two share 3/5 of their matches) are neither
    independent nor numerous enough to support a percentile interval, and
    that quantity was gating P1/P2. Without `refit` this returns a
    DESCRIPTIVE fold-resampling figure and sets `gate_eligible=False`, and
    the verdict must not consume it.

    Reported twice, because they answer different questions:
      - `shape`: graphs display-normalized, so only the curve's shape counts
      - `raw`:   graphs as recovered, so a change in overall level relative
                 to damage counts too -- and that level IS part of the
                 deployable metric, so erasing it would hide a real change

    Top-level `ratio`/`ratio_ci`/`stable` mirror the `raw` block, since the
    overall level IS part of what ships; `shape` stays available for a
    reader who wants the curve's proportions alone.
    """
    fold_graphs = [f.graph for f in result.per_fold.values() if f.graph is not None]
    if len(fold_graphs) < 2:
        return {"stable": False, "gate_eligible": False,
                "reason": "fewer than two folds produced a graph"}

    def summarize(sample, reference):
        block = np.array(sample)
        mean = block.mean(axis=0)
        spread = float(np.mean([_weighted_rms(g - mean, exposure) for g in block]))
        distance = _weighted_rms(mean - reference, exposure)
        return spread / distance if distance > 0 else np.inf

    out: dict = {"n_folds": len(fold_graphs)}
    for label, prepare in (("shape", lambda g: normalize_for_display(g, exposure)),
                           ("raw", lambda g: np.asarray(g, dtype=float))):
        prepared = [prepare(g) for g in fold_graphs]
        reference = prepare(shipped)
        out[label] = {"fold_dispersion_ratio": float(summarize(prepared, reference))}
        out[label].update(_fold_bootstrap_ratio(prepared, reference, exposure, draws, seed))

    out["ratio"] = out["raw"]["ratio"]
    out["ratio_ci"] = out["raw"]["ratio_ci"]
    out["stable"] = out["raw"]["stable"]

    if refit is None or match_ids is None:
        out.update({
            "gate_eligible": False,
            "rule": "fold-resampling only -- DESCRIPTIVE, must not gate a success claim",
        })
        return out

    rng = np.random.default_rng(seed)
    unique = np.asarray(sorted(set(int(m) for m in match_ids)))
    resampled: list[np.ndarray] = []
    for _ in range(draws):
        drawn = unique[rng.integers(0, len(unique), size=len(unique))]
        graph = refit(drawn.tolist())
        if graph is not None and np.all(np.isfinite(graph)):
            resampled.append(np.asarray(graph, dtype=float))
    if len(resampled) < 20:
        out.update({"gate_eligible": False,
                    "rule": f"only {len(resampled)} usable refits; interval not trustworthy"})
        return out

    for label, prepare in (("shape", lambda g: normalize_for_display(g, exposure)),
                           ("raw", lambda g: np.asarray(g, dtype=float))):
        prepared = [prepare(g) for g in resampled]
        reference = prepare(shipped)
        mean = np.array(prepared).mean(axis=0)
        distance = _weighted_rms(mean - reference, exposure)
        ratios = [
            _weighted_rms(g - mean, exposure) / distance if distance > 0 else np.inf
            for g in prepared
        ]
        low, high = np.percentile(ratios, [2.5, 97.5])
        out[label].update({
            "ratio": float(np.mean(ratios)),
            "ratio_ci": [float(low), float(high)],
            "stable": bool(np.isfinite(high) and high < 1.0),
        })

    out["ratio"] = out["raw"]["ratio"]
    out["ratio_ci"] = out["raw"]["ratio_ci"]
    out["gate_eligible"] = True
    out["stable"] = bool(out["shape"]["stable"] and out["raw"]["stable"])
    out["rule"] = (
        "match-clustered refitting bootstrap; resampled-to-mean RMS over "
        "mean-to-shipped RMS, exposure-weighted; stable when the upper bound "
        "is below 1 for BOTH the display-normalized shape and the raw level"
    )
    out["draws_used"] = len(resampled)
    return out


from app.services.kill_order_curves import (
    _finite_dp, construction_normalize, score_rounds,
)
from app.services.stats_math import _average_ranks


def _regress_on_swing(graph, dp, exposure) -> dict:
    """Exposure-weighted least squares of the graph on the swing curve.

    The spec's motivating measurement: hand = 50.0 + 478.0 * dP at
    R^2 = 0.9704, every residual within +-17 on a 40-250 scale. If the
    shipped numbers are already an affine function of the data's own swing
    curve, the headroom for refitting is small and the report must say so
    before it says anything else.
    """
    graph = np.asarray(graph, dtype=float)
    dp = np.asarray(dp, dtype=float)
    exposure = np.asarray(exposure, dtype=float)
    usable = np.isfinite(dp) & np.isfinite(graph) & (exposure > 0)
    weights = exposure[usable] / exposure[usable].sum()
    design = np.column_stack([np.ones(usable.sum()), dp[usable]])
    root = np.sqrt(weights)[:, None]
    coefficients, *_ = np.linalg.lstsq(design * root, graph[usable] * root.ravel(), rcond=None)
    predicted = design @ coefficients
    residual = graph[usable] - predicted
    mean = float(np.sum(weights * graph[usable]))
    r_squared = 1 - float(np.sum(weights * residual ** 2)) / float(
        np.sum(weights * (graph[usable] - mean) ** 2)
    )
    names = [PARAMS[i] for i in np.flatnonzero(usable)]
    return {
        "intercept": float(coefficients[0]),
        "slope": float(coefficients[1]),
        "r_squared": float(r_squared),
        "residuals": {n: float(v) for n, v in zip(names, residual)},
        "implied": {n: float(v) for n, v in zip(names, predicted)},
    }


def _compare_graphs(leverage, damage, reference, other) -> dict:
    a = score_rounds(leverage, damage, reference)
    b = score_rounds(leverage, damage, other)
    # _average_ranks, not a double argsort: tied scores are common here
    # (rounds with identical leverage) and double argsort breaks ties
    # arbitrarily, which inflates the correlation.
    ranks = _average_ranks
    return {
        "pearson": float(np.corrcoef(a, b)[0, 1]),
        "spearman": float(np.corrcoef(ranks(a), ranks(b))[0, 1]),
        "sd_reference": float(a.std()),
        "sd_difference": float((a - b).std()),
        "sign_flip_rate": float(np.mean((a > 0) != (b > 0))),
        "n": int(len(a)),
    }


def stage_c0_report(team_rows, player_rows, state_visits, draws=200, seed=0) -> dict:
    """Descriptive, all-data, and LABELLED as such. Nothing here is a
    held-out estimate; the swing table is built over every match precisely
    because this block describes the data rather than predicting from it.
    """
    leverage = family_a_leverage(team_rows)
    damage = np.array([row.damage_diff for row in team_rows], dtype=float)
    exposure = np.abs(leverage).sum(axis=0)
    shipped = shipped_graph()
    table = estimate_swing_table(state_visits)
    # _finite_dp lives in kill_order_curves and is the ONLY place a NaN dP is
    # pinned. Do not add a second copy here -- two pinning rules that drift
    # apart would silently give G1a a different graph in different modules.
    plugin = construction_normalize(_finite_dp(table), exposure, shipped)

    groups: dict[int, list] = {}
    for index, row in enumerate(team_rows):
        groups.setdefault(int(row.match_id), []).append(index)

    def pearson_of(sample):
        rows = [i for block in sample for i in block]
        a = score_rounds(leverage[rows], damage[rows], shipped)
        b = score_rounds(leverage[rows], damage[rows], plugin)
        return float(np.corrcoef(a, b)[0, 1])

    low, high = cluster_bootstrap_ci(pearson_of, groups, draws=draws, seed=seed)

    round_level = _compare_graphs(leverage, damage, shipped, plugin)
    round_level["pearson_ci"] = [float(low), float(high)]

    return {
        "note": (
            "DESCRIPTIVE, all-data. The swing table here is estimated over every "
            "match, so nothing in this block is a held-out estimate. dP is an "
            "observational contrast between state values, not the causal value "
            "of crossing a state."
        ),
        "swing_table": {
            "dp": {PARAMS[i]: (None if not np.isfinite(table.dp[i]) else float(table.dp[i]))
                   for i in range(len(PARAMS))},
            "visits": {PARAMS[i]: float(table.visits[i]) for i in range(len(PARAMS))},
            "incomplete": table.incomplete,
        },
        "shipped_vs_swing": _regress_on_swing(shipped, table.dp, exposure),
        "current_vs_swing_plugin": {
            "round_level": round_level,
            "player_match_level": _player_match_comparison(player_rows, shipped, plugin),
        },
        "reading": (
            "A correlation above 0.99 with no sign flips means no downstream "
            "yardstick difference was ever possible -- but correlation alone is "
            "NOT the practical-equivalence test; see the verdict checklist."
        ),
    }


def _player_match_comparison(player_rows, reference, other) -> dict:
    """Average Impact per (player, match) under each graph. The team
    differential can hide a change that per-player attribution shows, so
    both are reported."""
    # A PLAYER's Impact is damage + kill_impact - death_impact. The team row
    # adds both halves because the victim's death is subtracted from the
    # other team; per player it is a subtraction. An earlier draft used
    # kill + death here, which scores a player as if their own deaths helped
    # them.
    totals: dict[tuple, list] = {}
    for row in player_rows:
        key = (row.player_id, row.match_id)
        block = (row.kill - row.death).sum(axis=1) / 3.0
        totals.setdefault(key, []).append((row.damage, block))
    a, b = [], []
    for entries in totals.values():
        a.append(np.mean([d + float(np.sum(reference * v)) for d, v in entries]))
        b.append(np.mean([d + float(np.sum(other * v)) for d, v in entries]))
    a, b = np.array(a), np.array(b)
    ranks = lambda v: np.argsort(np.argsort(v))
    return {
        "pearson": float(np.corrcoef(a, b)[0, 1]),
        "spearman": float(np.corrcoef(ranks(a), ranks(b))[0, 1]),
        "n_player_matches": int(len(a)),
    }


stage_c0_report.regress_on_swing = _regress_on_swing
stage_c0_report.compare_graphs = _compare_graphs


from app.services.stats_math import point_biserial, tercile_buckets


MIN_MATCHES_FOR_WITHIN_PLAYER = 9   # >= 3 per tercile, matching the parent spec


def player_level_report(player_rows, match_outcomes, graph, surfaces=None,
                        draws=200, seed=0, min_matches=MIN_MATCHES_FOR_WITHIN_PLAYER) -> dict:
    """The only place in this project where the kill and death halves
    separate. At team level they correlate 0.937-0.957 by construction, so
    the team yardsticks cannot see a kill/death asymmetry; here they can.

    `surfaces` optionally supplies a Family B rung's recovered EFFECTIVE
    PRICE SURFACES -- a dict of (component, side) -> 26-vector, from
    `effective_surfaces`. An earlier draft took a three-element component
    weighting, which B2 and B3 (9 and 18 coefficients) cannot supply: it
    would have raised a shape error or, worse, silently scored the wrong
    thing. None uses the shipped equal weights over the given graph.
    """
    graph = np.asarray(graph, dtype=float)

    def collapse(block, side):
        block = np.asarray(block, dtype=float)
        if surfaces is None:
            return float(np.sum(graph * block.sum(axis=1) / len(COMPONENTS)))
        total = 0.0
        for index, component in enumerate(COMPONENTS):
            key = f"{component}_{side}"
            surface = surfaces.get(key, surfaces.get(component))
            if surface is None:
                raise KeyError(f"no effective surface for {key!r}")
            total += float(np.sum(np.asarray(surface) * block[:, index]))
        return total

    per_match: dict[tuple, dict] = {}
    for row in player_rows:
        if row.match_id not in match_outcomes:
            # A tie has no winner -- match_win()'s own contract excludes it
            # from every denominator, and "won" is undefined for it here
            # too. Not every match a player appears in has a determinable
            # outcome, so this is a real, expected skip on live data, not
            # a data-quality problem.
            continue
        key = (row.player_id, row.match_id)     # canonical player, then match
        entry = per_match.setdefault(key, {
            "kill": 0.0, "death": 0.0, "death_untraded": 0.0, "damage": 0.0,
            "rounds": 0, "won": bool(match_outcomes[row.match_id]) == bool(row.team_is_a),
        })
        entry["kill"] += collapse(row.kill, "kill")
        entry["death"] += collapse(row.death, "death")
        entry["death_untraded"] += collapse(row.death_untraded, "death")
        entry["damage"] += row.damage
        entry["rounds"] += 1

    def series(name):
        if name == "impact":
            return np.array([
                (e["damage"] + e["kill"] - e["death"]) / e["rounds"] for e in per_match.values()
            ])
        if name == "kill_impact":
            return np.array([(e["damage"] + e["kill"]) / e["rounds"] for e in per_match.values()])
        return np.array([e["death"] / e["rounds"] for e in per_match.values()])

    won = np.array([e["won"] for e in per_match.values()], dtype=int)
    keys = list(per_match)
    players = np.array([k[0] for k in keys])
    groups: dict[int, list] = {}
    for index, (_player, match_id) in enumerate(keys):
        groups.setdefault(int(match_id), []).append(index)

    def within_player_lift(values, rows=None):
        """Terciles computed WITHIN each sufficiently-observed player, then
        pooled. Global terciles would mostly compare strong players against
        weak ones -- 94.7% of players here have a single match -- which is
        not the quantity the spec asks for and not what the player page
        would show."""
        rows = np.arange(len(values)) if rows is None else np.asarray(rows)
        top_hits = top_n = bottom_hits = bottom_n = 0
        eligible = 0
        for player in np.unique(players[rows]):
            mine = rows[players[rows] == player]
            if len(mine) < min_matches:
                continue
            eligible += 1
            buckets = tercile_buckets(values[mine])
            if not (buckets == 2).any() or not (buckets == 0).any():
                continue
            top_hits += int(won[mine][buckets == 2].sum())
            top_n += int((buckets == 2).sum())
            bottom_hits += int(won[mine][buckets == 0].sum())
            bottom_n += int((buckets == 0).sum())
        if not top_n or not bottom_n:
            return None, 0
        return (top_hits / top_n) - (bottom_hits / bottom_n), eligible

    per_player: dict = {"summary": {}, "min_matches": min_matches}
    for name in ("impact", "kill_impact", "death_impact"):
        values = series(name)
        lift, eligible = within_player_lift(values)

        def lift_of(sample, values=values):
            rows = [i for block in sample for i in block]
            result, _ = within_player_lift(values, rows)
            return result

        low, high = cluster_bootstrap_ci(lift_of, groups, draws=draws, seed=seed)
        per_player[name] = {
            "point_biserial": float(point_biserial(values, won)),
            "within_player_tercile_lift": None if lift is None else float(lift),
            "ci": [float(low), float(high)],
            "eligible_players": eligible,
        }
        per_player["summary"][name] = {"mean": float(values.mean()), "sd": float(values.std())}

    scored = float(np.mean([e["death"] / e["rounds"] for e in per_match.values()]))
    untraded = float(np.mean([e["death_untraded"] / e["rounds"] for e in per_match.values()]))
    return {
        "note": (
            "Player-level. The team differential fuses kill and death (they "
            "correlate 0.937-0.957 there by construction); this block is where "
            "they separate, and where the trade discount is attributable."
        ),
        "n_player_matches": len(per_match),
        "per_player": per_player,
        "trades": {
            "death_impact_as_scored": scored,
            "death_impact_without_trade_credit": untraded,
            "discount": untraded - scored,
            "reading": (
                "The discount is what _traded_factor forgave. It depends on "
                "whether the player's TEAM traded for them, so it is a team "
                "quality currently charged to an individual."
            ),
        },
    }


from dataclasses import asdict

PRIMARY_COMPARISONS = (
    {"name": "P1", "candidate": "swing_basis", "against": "current_graph",
     "target": "T2", "alpha": 0.025, "declares": "A1"},
    {"name": "P2", "candidate": "pooled", "against": "current_graph",
     "target": "T2", "alpha": 0.025, "declares": "A1"},
    {"name": "P3", "candidate": "component_tilt", "against": "stage_a_exact",
     "target": "T2", "alpha": 0.05, "declares": "C"},
    {"name": "P4", "candidate": "swing_basis", "against": "pooled",
     "target": "T2", "alpha": 0.05, "declares": None},
)

VERDICTS = {
    "A1": ("prediction, next rounds", (1, 2)),
    "A2": ("prediction, match outcome", (6,)),
    "B": ("collinearity", (3, 4, 5)),
    "C": ("structure", (7,)),
}

PRACTICAL_EQUIVALENCE_LOSS = 0.0008   # the parent report's own T2 CI half-width
PRACTICAL_EQUIVALENCE_RMS = 0.01      # 1% of the score sd
COLLINEARITY_THRESHOLD = 0.70         # just below today's observed minimum of 0.733
AGREEMENT_SPEARMAN = 0.90
AGREEMENT_RMS_SHARE = 0.15


@dataclass(frozen=True)
class RunIdentity:
    dataset_fingerprint: str
    fold_mapping_hash: str
    calculation_version: str


def matrix_is_comparable(left: RunIdentity, right: RunIdentity):
    """Stage A and Stage C rows may share a matrix ONLY if all three match.

    The fold-mapping hash is not redundant with the fingerprint: the parent
    project's committed results used the permutation-based assign_folds, so
    an identical match set can carry a completely different assignment --
    same fingerprint, different folds, a matrix that looks comparable and is
    not.
    """
    reasons = [
        f"{field} differs: {getattr(left, field)!r} != {getattr(right, field)!r}"
        for field in ("dataset_fingerprint", "fold_mapping_hash", "calculation_version")
        if getattr(left, field) != getattr(right, field)
    ]
    return (not reasons), reasons


def paired_delta(result_a, result_b, alpha=0.05, draws=500, seed=0) -> dict:
    """Paired held-out weighted-log-loss difference, clustered by match.

    Negative = A predicts better than B. `alpha` is the two-sided level:
    0.025 for a co-primary, 0.05 otherwise.
    """
    groups: dict[int, list] = {}
    for index, match_id in enumerate(result_a.oof_match_ids):
        groups.setdefault(int(match_id), []).append(index)

    def loss_of(result):
        def inner(sample):
            rows = [i for block in sample for i in block]
            return weighted_log_loss(
                result.oof_probabilities[rows], result.oof_y[rows], result.oof_weights[rows]
            )
        return inner

    low, high = paired_bootstrap_delta(
        loss_of(result_a), loss_of(result_b), groups, draws=draws, seed=seed, alpha=alpha
    )
    delta = float(
        weighted_log_loss(result_a.oof_probabilities, result_a.oof_y, result_a.oof_weights)
        - weighted_log_loss(result_b.oof_probabilities, result_b.oof_y, result_b.oof_weights)
    )
    return {
        "delta": delta, "ci": [float(low), float(high)], "alpha": float(alpha),
        "excludes_zero": bool(high < 0 or low > 0),
        "favours": result_a.name if delta < 0 else result_b.name,
    }


def verdict_report(primaries, deployable, practically_equivalent, targets_agree,
                   max_component_correlation, econ_negative_every_fold,
                   beats_kill_diff_t1, stability) -> dict:
    """Four verdicts, printed side by side and never summarized into one.

    A Verdict A1 null alongside a Verdict C signal is a coherent and
    expected outcome -- 'the shared curve's shape was right, and the mistake
    was sharing it' -- and collapsing it would destroy the finding.
    """
    notes: dict[str, list[str]] = {key: [] for key in VERDICTS}

    cleared = []
    for name in ("P1", "P2"):
        entry = primaries[name]
        candidate = next(p["candidate"] for p in PRIMARY_COMPARISONS if p["name"] == name)
        if not deployable.get(candidate, True):
            notes["A1"].append(f"{name}: {candidate} is not deployable and cannot clear the bar")
            continue
        if not stability.get(candidate, {}).get("stable", False):
            notes["A1"].append(f"{name}: {candidate} did not pass the stability criterion")
            continue
        if entry["ci"][1] < 0:
            cleared.append(name)

    items = {
        1: bool(cleared),
        2: not practically_equivalent,
        3: targets_agree,
        4: max_component_correlation < COLLINEARITY_THRESHOLD,
        5: not econ_negative_every_fold,
        6: beats_kill_diff_t1,
        7: primaries["P3"]["ci"][1] < 0,
    }

    return {
        "note": (
            "A predeclared ANALYSIS PLAN, not a pre-registration: this dataset "
            "was used to design the candidates and set the thresholds, so it "
            "does not carry the independence a pre-registration claims. What it "
            "does buy is that the thresholds cannot move after the results."
        ),
        "thresholds": {
            "practical_equivalence_log_loss": PRACTICAL_EQUIVALENCE_LOSS,
            "practical_equivalence_rms_share": PRACTICAL_EQUIVALENCE_RMS,
            "collinearity_below": COLLINEARITY_THRESHOLD,
            "agreement_spearman_above": AGREEMENT_SPEARMAN,
            "agreement_rms_share_below": AGREEMENT_RMS_SHARE,
        },
        "primaries": primaries,
        "cleared_primaries": cleared,
        "verdicts": {
            key: {
                "question": question,
                "items": {str(i): bool(items[i]) for i in indices},
                "helped": all(items[i] for i in indices),
                "notes": notes[key],
            }
            for key, (question, indices) in VERDICTS.items()
        },
        # Every value consumed above, plus WHERE it came from. A verdict
        # computed from a hardcoded input is worse than no verdict -- the
        # CLI once passed max_component_correlation=1.0 and
        # beats_kill_diff_t1=False as constants, and this is the record
        # that would have caught it.
        "inputs": {
            "practically_equivalent": practically_equivalent,
            "targets_agree": targets_agree,
            "max_component_correlation": max_component_correlation,
            "econ_negative_every_fold": econ_negative_every_fold,
            "beats_kill_diff_t1": beats_kill_diff_t1,
            "source": {
                "practically_equivalent": "stage_c0_report (current_vs_swing_plugin round-level sd ratio)",
                "targets_agree": "target_agreement",
                "max_component_correlation": "component_correlations",
                "econ_negative_every_fold": "Stage A's own coefficient diagnostics (a prior finding this stage does not re-derive: Stage C fits the kill-order graph, not the outer FACTOR_WEIGHTS econ collapsed under)",
                "beats_kill_diff_t1": "yardstick_matrix",
            },
        },
    }


# Bumped whenever a change to this stage's own calculation would alter
# reported numbers for a fixed dataset (mirrors IMPACT_CALCULATION_VERSION's
# role for the shipped scorer). Folded into RunIdentity.calculation_version.
STAGE_C_SCHEMA_VERSION = 1

REPORT_SECTIONS = (
    "identity",            # dataset fingerprint, fold mapping hash, calculation version
    "stage_c0",            # FIRST: how much can the graph move Impact at all
    "loading",             # eligible / excluded match counts
    "conditioning",        # condition number, effective rank, VIF
    "per_parameter",       # exposure, rounds touched, VIF -- diagnostics only
    "family_a",            # G0-G4 per fold: recovered graphs, d, deployability
    "family_b",            # the B0-B3 ladder
    "control_ladder",      # five rungs, headline on 4 -> 5
    "yardstick_matrix",    # targets x yardsticks, refusing mixed Stage A rows
    "player_level",        # death impact and trades
    "stability",           # graph-level bootstrap ratio
    "deferral_check",      # match count against the ~4,000 re-open threshold
    "verdicts",            # LAST, four of them, never merged
)


T1_ELIGIBLE = ("current_graph", "swing_plugin", "swing_affine", "swing_basis")


def run_all_targets(leverage_rows, observations, l2_grid, n_folds=5, seed=0,
                    state_visits=None, value_model=None):
    """T1, T2 and WPA, each on its own frozen definition.

    G3 and G4 are excluded from T1 by design, not by accident: 26 free
    parameters against 1,114 matches is the ratio this project already calls
    indefensible, and running it anyway then declining to believe it would be
    theatre.
    """
    from app.services.impact_eval import PRIMARY_T1, PRIMARY_T2, TargetConfig

    plans = {
        "T1": (PRIMARY_T1, list(T1_ELIGIBLE)),
        "T2": (PRIMARY_T2, list(FAMILY_A)),
        "WPA": (TargetConfig(name="WPA"), list(FAMILY_A)),
    }
    out: dict = {}
    for label, (config, candidates) in plans.items():
        out[label] = run_nested_cv(
            leverage_rows, observations, config, candidates=candidates,
            l2_grid=l2_grid, n_folds=n_folds, seed=seed, state_visits=state_visits,
        )
    return out


def target_agreement(graphs_by_target, exposure) -> dict:
    """Do the graphs fitted against different targets agree?

    Stage A's answer for the OUTER weights was an emphatic no -- T1 put zero
    weight on econ, T2 on everything but swing, WPA on swing. This asks the
    same question of the curve, against thresholds declared before the run.
    """
    labels = sorted(graphs_by_target)
    normalized = {k: normalize_for_display(v, exposure) for k, v in graphs_by_target.items()}
    mean_price = float(np.mean([_weighted_rms(g, exposure) for g in normalized.values()]))

    spearman, rms_share = {}, {}
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            key = f"{a}~{b}"
            ranks_a, ranks_b = _average_ranks(normalized[a]), _average_ranks(normalized[b])
            spearman[key] = float(np.corrcoef(ranks_a, ranks_b)[0, 1])
            rms_share[key] = float(
                _weighted_rms(normalized[a] - normalized[b], exposure) / mean_price
            )
    return {
        "spearman": spearman,
        "rms_share": rms_share,
        "thresholds": {"spearman_above": AGREEMENT_SPEARMAN,
                       "rms_share_below": AGREEMENT_RMS_SHARE},
        "agree": bool(min(spearman.values()) > AGREEMENT_SPEARMAN
                      and max(rms_share.values()) < AGREEMENT_RMS_SHARE),
    }


import copy

from app.services.impact_eval import (
    BASELINE_CANDIDATES, CURRENT_IMPACT_CANDIDATE, Candidate,
)
from app.services.impact_eval import yardstick_matrix as _parent_yardstick_matrix


def _rounding_gap(team_rows, observations) -> dict:
    """current_impact reads the ROUNDED stored impact_diff; current_graph is
    the same shipped values through the UNROUNDED leverage pipeline.
    Printed on its own line so the rounding cost is visible rather than
    absorbed into a fitted-candidate comparison -- computed directly here,
    independent of any fold structure, since neither side is fitted."""
    by_round = {row.round_id: row for row in team_rows}
    per_round = family_a_leverage(list(by_round.values()))
    index_of = {rid: i for i, rid in enumerate(by_round)}
    shipped = shipped_graph()

    stored, unrounded = [], []
    for obs in observations:
        row = by_round.get(obs.round_id)
        if row is None:
            continue
        lev = per_round[index_of[obs.round_id]]
        unrounded.append(float(row.damage_diff + lev @ shipped))
        stored.append(float(obs.impact_diff))
    stored_arr = np.array(stored, dtype=float)
    unrounded_arr = np.array(unrounded, dtype=float)
    pearson = (
        float(np.corrcoef(stored_arr, unrounded_arr)[0, 1]) if len(stored_arr) > 1 else float("nan")
    )
    return {
        "compared": ["current_impact", "current_graph"],
        "mean_abs_gap": float(np.mean(np.abs(stored_arr - unrounded_arr))) if len(stored_arr) else 0.0,
        "pearson": pearson,
        "n": int(len(stored_arr)),
        "reading": (
            "current_impact reads the rounded stored impact_diff; current_graph is "
            "the same shipped values through the unrounded leverage pipeline."
        ),
    }


def yardstick_matrix(team_rows, observations, results, draws=200, seed=0,
                     stage_a_rows=None, stage_a_identity=None, identity=None) -> dict:
    """Every Stage C candidate x every parent yardstick, scored through the
    PARENT PROJECT'S OWN yardstick functions so Stage A and Stage C cells
    come from identical code.

    A fitted candidate's per-round score (S_r = damage_diff + graph . x_r)
    is not one of RoundObservation's existing fields, so it cannot be
    expressed as a Candidate over native feature names the way the fixed
    baselines are. Instead each candidate's OOF score is attached as a
    synthetic per-candidate field on a CLONE of each observation (never the
    originals -- these are shared, mutable dataclasses), and a Candidate
    reading that one field takes it through yardstick_first_half /
    yardstick_full_match / yardstick_forward_rounds unchanged.

    Refuses to join Stage A rows unless matrix_is_comparable passes on all
    three identity values -- see RunIdentity.
    """
    by_round = {row.round_id: row for row in team_rows}
    per_round = family_a_leverage(list(by_round.values()))
    index_of = {rid: i for i, rid in enumerate(by_round)}

    clones = [copy.copy(obs) for obs in observations]
    clones_by_round: dict = {}
    for clone in clones:
        clones_by_round.setdefault(clone.round_id, []).append(clone)

    per_fold_candidates: dict[str, dict[int, Candidate]] = {}
    folds_source: dict = {}
    for name, result in results.items():
        field = f"_stage_c_score_{name}"
        for clone in clones:
            setattr(clone, field, 0.0)  # default for rounds no fold ever scores

        per_fold_candidates[name] = {}
        for fold_index, fitted in result.per_fold.items():
            if fitted.graph is None:
                continue
            if not folds_source:
                folds_source = result.per_fold
            test_ids = set(fitted.test_match_ids)
            for rid, row in by_round.items():
                if row.match_id not in test_ids or rid not in index_of:
                    continue
                lev = per_round[index_of[rid]]
                score = float(row.damage_diff + lev @ fitted.graph)
                for clone in clones_by_round.get(rid, ()):
                    setattr(clone, field, score)
            per_fold_candidates[name][fold_index] = Candidate(
                name=name, feature_names=[field], weights=[1.0],
            )

    fixed = [CURRENT_IMPACT_CANDIDATE, *BASELINE_CANDIDATES]
    cells = _parent_yardstick_matrix(
        clones, fixed, per_fold_candidates, folds_source, draws=draws, seed=seed,
    )

    out: dict = {
        "cells": cells,
        "rounding_gap": _rounding_gap(team_rows, observations),
    }

    if stage_a_rows is not None and stage_a_identity is not None and identity is not None:
        ok, reasons = matrix_is_comparable(identity, stage_a_identity)
        out["stage_a_joined"] = ok
        out["stage_a_refusal"] = reasons
        out["stage_a_rows"] = stage_a_rows if ok else None
    else:
        out["stage_a_joined"] = False
        out["stage_a_refusal"] = ["no Stage A identity supplied"]

    return out


def component_correlations(team_rows, graph) -> dict:
    """econ_impact / time_impact / swing_impact recomputed PER ROUND under a
    given graph -- verdict item 4 reads max_abs. The CLI once passed a
    hardcoded 1.0 here, which would have made the verdict meaningless."""
    graph = np.asarray(graph, dtype=float)
    columns: dict[str, np.ndarray] = {}
    for index, component in enumerate(COMPONENTS):
        values = [
            float(np.sum(graph * (row.kill[:, index] + row.death[:, index])))
            for row in team_rows
        ]
        columns[component] = np.array(values, dtype=float)

    names = list(columns)
    stacked = np.column_stack([columns[n] for n in names])
    correlation = np.corrcoef(stacked, rowvar=False)
    off_diagonal = correlation[~np.eye(len(names), dtype=bool)]
    return {
        "matrix": {
            a: {b: float(correlation[i, j]) for j, b in enumerate(names)}
            for i, a in enumerate(names)
        },
        "max_abs": float(np.abs(off_diagonal).max()) if len(names) > 1 else 0.0,
    }


def factor_profiles(state_terms) -> dict:
    """Per-state mean kill-half factor values, and their correlation with
    MARGIN and TOTAL_ALIVE -- the evidence Family B rests on (spec: econ
    tracks margin at -0.981, swing at +0.946, time tracks total alive at
    -0.956). `state_terms` is any iterable of objects carrying
    `param_index` and a 3-tuple/array `kill` (e.g. KillTerm); only tracked
    lattice states contribute; the fallback has no state and is excluded.

    Diagnostic only -- like monotonicity, this reports a shape, it does not
    select or gate anything.
    """
    sums = np.zeros((len(PARAMS), len(COMPONENTS)))
    counts = np.zeros(len(PARAMS))
    for term in state_terms:
        index = term.param_index
        if index >= N_LATTICE:  # fallback has no state
            continue
        sums[index] += np.asarray(term.kill, dtype=float)
        counts[index] += 1

    lattice = np.flatnonzero(counts[:N_LATTICE] > 0)
    if len(lattice) == 0:
        return {"profiles": {}, "corr_with_margin": {}, "corr_with_total_alive": {}}

    profiles: dict[str, np.ndarray] = {}
    corr_margin: dict[str, float] = {}
    corr_total: dict[str, float] = {}
    for index, component in enumerate(COMPONENTS):
        mean_values = sums[lattice, index] / counts[lattice]
        profiles[component] = {PARAMS[i]: float(v) for i, v in zip(lattice, mean_values)}
        margin_here = MARGIN[lattice]
        total_here = TOTAL_ALIVE[lattice]
        corr_margin[component] = (
            float(np.corrcoef(mean_values, margin_here)[0, 1]) if mean_values.std() > 0 else float("nan")
        )
        corr_total[component] = (
            float(np.corrcoef(mean_values, total_here)[0, 1]) if mean_values.std() > 0 else float("nan")
        )

    return {
        "profiles": profiles,
        "corr_with_margin": corr_margin,
        "corr_with_total_alive": corr_total,
        "n_states": int(len(lattice)),
    }


def to_jsonable(value):
    """Walk a report converting numpy scalars/arrays to plain Python types,
    NaN/Inf to None (json.dumps would otherwise emit non-standard `NaN`
    tokens that json.loads on the read side may not accept), and
    dataclasses to dicts. Everything this module returns eventually goes
    through this before json.dumps -- the acceptance test is what pins that
    the round trip survives."""
    import dataclasses
    import math

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if not math.isfinite(f) else f
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _weighted_leverage(team_rows, component_weights) -> np.ndarray:
    """Like family_a_leverage, but with EXPLICIT per-component outer
    weights instead of the shipped FACTOR_WEIGHTS (all 1.0, total 3).
    Used only by outer_weight_sensitivity / alternation_sensitivity to ask
    how much a candidate's recovered graph depends on w rather than b."""
    weights = np.asarray(component_weights, dtype=float)
    total = float(weights.sum())
    out = np.zeros((len(team_rows), len(PARAMS)))
    for index, row in enumerate(team_rows):
        out[index] = ((row.kill + row.death) * weights).sum(axis=1) / total
    return out


def fallback_sensitivity(team_rows, observations, config, l2_grid,
                         candidates=("swing_basis", "pooled"), n_folds=5, seed=0) -> dict:
    """Drops every round where a kill touched the FALLBACK parameter and
    re-runs the same candidates. A shift here is a data-quality finding
    about the resurrection heuristic (497 rounds, measured, in the full
    DB), not a graph finding -- reported, never adopted, and the eligible
    round set for the primary run is never restricted this way.

    Not a paired significance test: the full and dropped runs replay
    DIFFERENT observation sets, so their OOF rows are not index-aligned the
    way paired_delta requires. Reported instead as each run's own held-out
    loss plus how far the two runs' recovered graphs sit from each other.
    """
    fallback_index = PARAM_INDEX["fallback"]
    affected = {
        row.round_id for row in team_rows
        if np.any(row.kill[fallback_index] != 0) or np.any(row.death[fallback_index] != 0)
    }
    filtered_rows = [row for row in team_rows if row.round_id not in affected]
    filtered_obs = [o for o in observations if o.round_id not in affected]

    exposure = np.abs(family_a_leverage(team_rows)).sum(axis=0)
    full = run_nested_cv(team_rows, observations, config, candidates=list(candidates),
                         l2_grid=l2_grid, n_folds=n_folds, seed=seed)
    dropped = (
        run_nested_cv(filtered_rows, filtered_obs, config, candidates=list(candidates),
                     l2_grid=l2_grid, n_folds=n_folds, seed=seed)
        if filtered_rows else {}
    )

    moved: dict = {}
    for name in candidates:
        if name not in full or name not in dropped:
            continue
        full_graphs = [f.graph for f in full[name].per_fold.values() if f.graph is not None]
        dropped_graphs = [f.graph for f in dropped[name].per_fold.values() if f.graph is not None]
        if not full_graphs or not dropped_graphs:
            continue
        full_mean = np.mean(full_graphs, axis=0)
        dropped_mean = np.mean(dropped_graphs, axis=0)
        moved[name] = {
            "graph_rms_shift": float(_weighted_rms(full_mean - dropped_mean, exposure)),
            "full_oof_log_loss": float(
                weighted_log_loss(full[name].oof_probabilities, full[name].oof_y, full[name].oof_weights)
            ),
            "dropped_oof_log_loss": float(
                weighted_log_loss(dropped[name].oof_probabilities, dropped[name].oof_y,
                                  dropped[name].oof_weights)
            ),
        }

    return {
        "affected_rounds": len(affected),
        "moved": moved,
        "reading": (
            "A shift here is a data-quality finding about the resurrection "
            "heuristic, not a graph finding."
        ),
    }


def outer_weight_sensitivity(team_rows, observations, config, weights_by_target,
                             name="swing_basis", l2=1.0, state_visits=None) -> dict:
    """ALL-DATA (descriptive, not held-out): the same candidate fit under
    the shipped equal outer weighting versus under each TARGET's own Stage
    A weighting, measuring how much the recovered graph depends on w
    rather than on b. `weights_by_target` is {label: (w_econ, w_time,
    w_swing)}, supplied by the caller from Stage A's own fitted weights --
    this stage does not re-derive them.
    """
    aligned = align_target(team_rows, observations, config)
    table = (
        estimate_swing_table(state_visits) if state_visits
        else _table_from_rows(team_rows, {r.round_id for r in team_rows})
    )
    exposure = np.abs(aligned.leverage).sum(axis=0)

    def fit(leverage):
        return fit_family_a(
            name, (leverage, aligned.damage, aligned.y, aligned.weights),
            (leverage, aligned.damage, aligned.weights), table, l2, exposure, shipped_graph(),
        )

    shipped = fit(aligned.leverage)
    out: dict = {"shipped_weighting": {"graph": shipped.graph, "d": shipped.d}}
    for label, w in weights_by_target.items():
        candidate = fit(_weighted_leverage(aligned.team_rows, w))
        out[label] = {
            "graph": candidate.graph, "d": candidate.d,
            "rms_shift_from_shipped_weighting": float(
                _weighted_rms(candidate.graph - shipped.graph, exposure)
            ),
        }
    return out


def alternation_sensitivity(team_rows, observations, config, name="swing_basis", l2=1.0,
                            state_visits=None) -> dict:
    """b -> w -> b, EXACTLY two b steps, declared up front: fit the graph
    under the shipped w, refit w under that graph (the parent's own
    fit_constrained_weights), then refit the graph once more under the new
    w. Reports whether the second b step moved anything. Iterating to
    convergence is refused: the objective is non-convex, and "we stopped
    when it stopped moving" is a selection surface dressed as a numerical
    detail.
    """
    from app.services.impact_eval import controls_for, fit_constrained_weights

    aligned = align_target(team_rows, observations, config)
    table = (
        estimate_swing_table(state_visits) if state_visits
        else _table_from_rows(team_rows, {r.round_id for r in team_rows})
    )
    exposure = np.abs(aligned.leverage).sum(axis=0)

    def fit_b(leverage):
        return fit_family_a(
            name, (leverage, aligned.damage, aligned.y, aligned.weights),
            (leverage, aligned.damage, aligned.weights), table, l2, exposure, shipped_graph(),
        )

    def refit_w(graph):
        """econ/time/swing recomputed as observation-shaped columns UNDER
        `graph`, on CLONES (never the shared originals), then Stage A's own
        constrained-weight search over them."""
        by_round = {row.round_id: row for row in team_rows}
        clones = []
        for obs in observations:
            row = by_round.get(obs.round_id)
            clone = copy.copy(obs)
            if row is not None:
                for attr, index in (("econ_impact", 0), ("time_impact", 1), ("swing_impact", 2)):
                    setattr(clone, attr, float(
                        np.sum(graph * (row.kill[:, index] + row.death[:, index])) / len(COMPONENTS)
                    ))
            clones.append(clone)
        return fit_constrained_weights(clones, config, controls_for(config))

    step1 = fit_b(aligned.leverage)
    w2 = refit_w(step1.graph)
    step2 = fit_b(_weighted_leverage(aligned.team_rows, [w2.econ, w2.time, w2.swing]))

    return {
        "b1": step1.graph,
        "w2": {"econ": w2.econ, "time": w2.time, "swing": w2.swing,
              "damage_multiplier": w2.damage_multiplier},
        "b2": step2.graph,
        "graph_rms_shift": float(_weighted_rms(step2.graph - step1.graph, exposure)),
        "reading": (
            "Exactly two b steps, declared up front. Iterating to convergence is "
            "refused: the objective is non-convex, and stopping when it stops "
            "moving is a selection surface dressed as a numerical detail."
        ),
    }


def _practically_equivalent_stage_c0(stage_c0: dict) -> bool:
    """PRACTICAL_EQUIVALENCE_RMS is 1% of the score sd: reuses Stage C0's
    own current-vs-swing-plugin comparison, since sd(difference) vs
    sd(reference) is exactly that measurement, already in the report."""
    round_level = stage_c0["current_vs_swing_plugin"]["round_level"]
    if round_level["sd_reference"] == 0:
        return False
    return (round_level["sd_difference"] / round_level["sd_reference"]) < PRACTICAL_EQUIVALENCE_RMS


def build_full_report(leverage_rows, observations, player_rows=None, state_visits=None,
                      draws=200, l2_grid=None, n_folds=5, seed=0,
                      outer_weights_by_target=None, econ_negative_every_fold=True) -> dict:
    """Assembles the complete Stage C report: every REPORT_SECTIONS entry
    populated, all four primaries, all four verdicts computed from real
    inputs -- never a hardcoded placeholder. The CLI becomes argument
    parsing plus printing; this is the function it calls.

    `econ_negative_every_fold` is the one verdict input this stage cannot
    derive on its own: it is a STAGE A finding (the outer FACTOR_WEIGHTS
    econ coefficient was negative in every fold), not something a
    kill-order-graph refit independently measures, since Stage C never
    fits per-component outer weights at all. Defaults to that already-
    established prior finding rather than an invented constant; a caller
    with a fresh Stage A run should pass the real value.

    `outer_weights_by_target`, if given, feeds outer_weight_sensitivity
    with each target's Stage A weighting ({label: (w_econ, w_time,
    w_swing)}); omitted, that one sensitivity is skipped rather than
    guessed.
    """
    from app.scoring.impact import IMPACT_CALCULATION_VERSION
    from app.services.impact_eval import (
        PRIMARY_T2, dataset_fingerprint, fold_mapping_hash,
    )
    from app.services.kill_order_curves import FAMILY_B

    player_rows = list(player_rows or [])
    state_visits = list(state_visits or [])
    l2_grid = list(l2_grid or [0.01, 0.1, 1.0, 10.0, 100.0])

    report: dict = {section: None for section in REPORT_SECTIONS}

    match_ids = sorted({row.match_id for row in leverage_rows})
    folds = stable_folds(match_ids)
    identity = RunIdentity(
        dataset_fingerprint=dataset_fingerprint(match_ids),
        fold_mapping_hash=fold_mapping_hash(folds),
        calculation_version=f"{IMPACT_CALCULATION_VERSION}/{STAGE_C_SCHEMA_VERSION}",
    )
    report["identity"] = identity.__dict__
    report["loading"] = {
        "eligible_matches": len(match_ids), "excluded_matches": 0, "excluded_match_ids": [],
    }

    # FIRST, always: if the metric does not move, everything below is read
    # in that light rather than as a headline of its own.
    report["stage_c0"] = stage_c0_report(leverage_rows, player_rows, state_visits, draws=draws)

    leverage_matrix = family_a_leverage(leverage_rows)
    exposure = np.abs(leverage_matrix).sum(axis=0)
    report["conditioning"] = conditioning_report(leverage_matrix)
    report["per_parameter"] = per_parameter_report(leverage_matrix, exposure)

    def _fold_summary(result):
        oof_loss = (
            float(weighted_log_loss(result.oof_probabilities, result.oof_y, result.oof_weights))
            if result.oof_probabilities is not None else None
        )
        return {
            "oof_weighted_log_loss": oof_loss,
            "per_fold": {
                str(f.fold): {
                    "l2": f.l2, "d": f.d, "deployable": f.deployable, "reasons": list(f.reasons),
                    "graph": f.graph, "weights": f.weights,
                }
                for f in result.per_fold.values()
            },
        }

    family_a_results = run_nested_cv(
        leverage_rows, observations, PRIMARY_T2, candidates=list(FAMILY_A), l2_grid=l2_grid,
        n_folds=n_folds, seed=seed, state_visits=state_visits, family="A",
    )
    report["family_a"] = {name: _fold_summary(r) for name, r in family_a_results.items()}

    family_b_results = run_nested_cv(
        leverage_rows, observations, PRIMARY_T2, candidates=list(FAMILY_B), l2_grid=l2_grid,
        n_folds=n_folds, seed=seed, family="B",
    )
    report["family_b"] = {name: _fold_summary(r) for name, r in family_b_results.items()}

    all_results = {**family_a_results, **family_b_results}

    report["control_ladder"] = control_ladder(
        leverage_rows, observations, PRIMARY_T2, n_folds=n_folds, draws=draws,
    )

    match_outcomes = {
        o.match_id: o.match_won_by_team_a for o in observations if o.match_won_by_team_a is not None
    }
    report["player_level"] = player_level_report(
        player_rows, match_outcomes, shipped_graph(), draws=draws,
    )

    report["stability"] = {
        name: stability_report(result, shipped_graph(), exposure, draws=draws)
        for name, result in all_results.items() if name != "current_graph"
    }

    report["yardstick_matrix"] = yardstick_matrix(
        leverage_rows, observations, family_a_results, draws=draws, identity=identity,
    )

    report["deferral_check"] = {
        "matches": len(match_ids), "reopen_threshold": 4000,
        "reachable": len(match_ids) >= 4000,
        "note": "4,000 re-opens the deferred per-component fits for a LOOK, not a verdict.",
    }

    all_targets = run_all_targets(
        leverage_rows, observations, l2_grid=l2_grid, n_folds=n_folds, seed=seed,
        state_visits=state_visits,
    )
    graphs_by_target: dict = {}
    for label, results_by_name in all_targets.items():
        candidate = results_by_name.get("swing_basis")
        if candidate is None:
            continue
        graphs = [f.graph for f in candidate.per_fold.values() if f.graph is not None]
        if graphs:
            graphs_by_target[label] = np.mean(graphs, axis=0)
    agreement = (
        target_agreement(graphs_by_target, exposure) if len(graphs_by_target) >= 2
        else {"agree": False, "spearman": {}, "rms_share": {},
             "thresholds": {"spearman_above": AGREEMENT_SPEARMAN,
                            "rms_share_below": AGREEMENT_RMS_SHARE}}
    )

    correlations = component_correlations(leverage_rows, shipped_graph())

    primaries = {
        spec["name"]: paired_delta(all_results[spec["candidate"]], all_results[spec["against"]],
                                   alpha=spec["alpha"], draws=draws, seed=seed)
        for spec in PRIMARY_COMPARISONS
        if spec["candidate"] in all_results and spec["against"] in all_results
    }

    first_half_cell = (report["yardstick_matrix"]["cells"] or {}).get("first_half_to_match", {})
    current_graph_cell = first_half_cell.get("current_graph") or {}
    beats_kill_diff_t1 = bool((current_graph_cell.get("gap_over_kill_diff") or 0.0) > 0)

    report["verdicts"] = verdict_report(
        primaries=primaries,
        deployable={n: all(f.deployable for f in r.per_fold.values()) for n, r in all_results.items()},
        practically_equivalent=_practically_equivalent_stage_c0(report["stage_c0"]),
        targets_agree=agreement["agree"],
        max_component_correlation=correlations["max_abs"],
        econ_negative_every_fold=econ_negative_every_fold,
        beats_kill_diff_t1=beats_kill_diff_t1,
        stability=report["stability"],
    )

    # Sensitivities: reported and none adopted. Best-effort -- a failure
    # here must never take down the sections above, which is why each is
    # wrapped rather than left to propagate.
    sensitivities: dict = {}
    try:
        sensitivities["fallback"] = fallback_sensitivity(
            leverage_rows, observations, PRIMARY_T2, l2_grid, n_folds=n_folds, seed=seed,
        )
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        sensitivities["fallback"] = {"error": f"{type(exc).__name__}: {exc}"}
    if outer_weights_by_target:
        try:
            sensitivities["outer_weight"] = outer_weight_sensitivity(
                leverage_rows, observations, PRIMARY_T2, outer_weights_by_target,
                state_visits=state_visits,
            )
        except Exception as exc:  # noqa: BLE001
            sensitivities["outer_weight"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        sensitivities["alternation"] = alternation_sensitivity(
            leverage_rows, observations, PRIMARY_T2, state_visits=state_visits,
        )
    except Exception as exc:  # noqa: BLE001
        sensitivities["alternation"] = {"error": f"{type(exc).__name__}: {exc}"}
    report["sensitivities"] = sensitivities

    return report
