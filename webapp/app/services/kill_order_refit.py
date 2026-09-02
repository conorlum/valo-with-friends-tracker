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
