"""Candidate kill-order curves: the empirical swing table, the
parameterizations built on it, and the recovery of a deployable graph from
fitted coefficients.

dP is an OBSERVATIONAL contrast between two state values, not the causal
value of crossing the state. It is a prior shape and a descriptive
benchmark; the report must not describe it as a causal effect.
"""

from dataclasses import dataclass, field

import numpy as np

from app.services.kill_order_leverage import PARAM_INDEX, PARAMS

_LATTICE = [(own, opp) for own in range(1, 6) for opp in range(1, 6)]


def _axis(values) -> np.ndarray:
    """Standardize over the 25 lattice states, UNWEIGHTED, and park the
    fallback at the origin.

    Unweighted on purpose: exposure weighting would make the transform
    depend on data and therefore need per-fold recomputation and a leakage
    argument. These are fixed constants instead.
    """
    raw = np.asarray(values, dtype=float)
    standardized = (raw - raw.mean()) / raw.std()
    out = np.zeros(len(PARAMS))
    out[: len(_LATTICE)] = standardized
    return out


MARGIN = _axis([own - opp for own, opp in _LATTICE])
TOTAL_ALIVE = _axis([own + opp for own, opp in _LATTICE])
EVEN_STATE = np.zeros(len(PARAMS))
for _own, _opp in _LATTICE:
    if _own == _opp:
        EVEN_STATE[PARAM_INDEX[f"{_own}v{_opp}"]] = 1.0


@dataclass(frozen=True)
class SwingTable:
    dp: np.ndarray                      # (26,), NaN where undetermined
    visits: np.ndarray                  # (26,) state-entry counts
    win_rate: dict                      # (own, opp) -> P(win), for the report
    incomplete: list = field(default_factory=list)


def estimate_swing_table(visit_rows) -> SwingTable:
    """dP per parameter from state-entry win rates.

    MUST be called with TRAINING rows only wherever the result feeds a
    candidate, a basis or a prior -- it is estimated from round outcomes,
    so an all-data table leaks held-out outcomes into every candidate.
    """
    wins: dict[tuple[int, int], int] = {}
    counts: dict[tuple[int, int], int] = {}
    for row in visit_rows:
        key = (row.own, row.opp)
        counts[key] = counts.get(key, 0) + 1
        wins[key] = wins.get(key, 0) + (1 if row.won else 0)

    win_rate = {key: wins[key] / counts[key] for key in counts}

    dp = np.full(len(PARAMS), np.nan)
    visit_counts = np.zeros(len(PARAMS))
    incomplete: list[str] = []
    for own, opp in _LATTICE:
        name = f"{own}v{opp}"
        index = PARAM_INDEX[name]
        visit_counts[index] = counts.get((own, opp), 0)
        here = win_rate.get((own, opp))
        after = win_rate.get((own, opp - 1))
        if here is None or after is None:
            incomplete.append(name)
            continue
        dp[index] = after - here
    # The fallback has no state, so it has no swing value. Candidates that
    # need a number there pin it; they never interpolate one.
    return SwingTable(dp=dp, visits=visit_counts, win_rate=win_rate, incomplete=incomplete)


from app.services.kill_order_leverage import COMPONENTS, FALLBACK_WEIGHT, PARAM_INDEX

DISPLAY_MEAN = 136.6  # the shipped graph's exposure-weighted mean, measured


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate reduced to what every yardstick needs: per-round scores
    on the RECOVERED parameterization, plus enough provenance to report it.

    `graph` is populated for Family A; `weights` for Family B, whose
    candidates are weightings over a fixed graph rather than graphs.
    """

    name: str
    scores: np.ndarray
    d: float
    deployable: bool
    reasons: tuple[str, ...] = ()
    graph: np.ndarray | None = None
    weights: np.ndarray | None = None


def family_a_leverage(team_rows) -> np.ndarray:
    """(n_rounds, 26). The shipped FACTOR_WEIGHTS are all 1.0 over a total
    of 3, so Family A's per-parameter column is the mean over components of
    the kill and death halves."""
    out = np.zeros((len(team_rows), len(PARAMS)))
    for index, row in enumerate(team_rows):
        out[index] = (row.kill + row.death).sum(axis=1) / len(COMPONENTS)
    return out


N_LATTICE = 25
LATTICE = slice(0, N_LATTICE)


def lattice_dp(table) -> np.ndarray:
    """The 25 lattice dP values, or a refusal.

    The fallback parameter is NOT included and is never imputed: it has no
    state, so it has no swing value, and the spec pins it at the shipped 100
    for every structured family. An earlier draft mean-imputed both the
    fallback and any undetermined lattice state, which quietly invented a
    number for a state the data could not speak to.
    """
    dp = np.asarray(table.dp, dtype=float)[LATTICE]
    if not np.all(np.isfinite(dp)):
        missing = [PARAMS[i] for i in np.flatnonzero(~np.isfinite(dp))]
        raise ValueError(
            f"swing table has no dP for {missing}; this fold cannot build a "
            f"structured candidate. Fail the fold rather than imputing."
        )
    return dp


def basis_for(name: str, table) -> np.ndarray:
    """(25, p) over the LATTICE ONLY. A structured candidate's lattice graph
    is `basis @ theta`; its fallback is pinned at FALLBACK_WEIGHT and enters
    the fit through the composite damage column instead."""
    dp = lattice_dp(table)
    ones = np.ones(N_LATTICE)
    if name == "G1b":
        return np.column_stack([ones, dp])
    if name == "G2":
        return np.column_stack(
            [ones, dp, dp ** 2, np.sign(MARGIN[LATTICE]), EVEN_STATE[LATTICE]]
        )
    if name in ("G3", "G4"):
        return np.eye(len(PARAMS))     # unstructured: the fallback is a free column
    raise ValueError(f"unknown basis {name!r}")


def recover_graph(beta, damage_index: int, basis: np.ndarray):
    """beta is [intercept, controls..., damage, theta...] in whatever column
    order the caller built. `damage_index` is the damage column's position
    among the NON-intercept columns.

    Returns (graph, d). The caller checks d > 0 via check_deployable; this
    does not silently repair it.
    """
    coefficients = np.asarray(beta, dtype=float)[1:]
    d = float(coefficients[damage_index])
    theta = coefficients[damage_index + 1 :]
    q = basis @ theta
    if d == 0.0:
        return np.full(len(PARAMS), np.nan), d
    return q / d, d


def check_deployable(graph, d: float, exposure, min_exposure: float = 1.0):
    """A candidate that cannot ship has not improved the metric.

    Non-negativity is a DEPLOYABILITY GATE, not a constraint on the fit:
    nothing is clipped during fitting, and a candidate needing negative
    prices is reported in full with the offending states listed.
    """
    reasons: list[str] = []
    if not np.isfinite(d) or d <= 0:
        reasons.append(f"damage coefficient d={d:.4g} is not positive; no graph is recoverable")
    graph = np.asarray(graph, dtype=float)
    exposure = np.asarray(exposure, dtype=float)
    if not np.all(np.isfinite(graph)):
        reasons.append("graph contains non-finite values")
    else:
        offending = [
            f"{PARAMS[i]}={graph[i]:.1f}"
            for i in range(len(PARAMS))
            if graph[i] < 0 and exposure[i] >= min_exposure
        ]
        if offending:
            reasons.append("negative price at " + ", ".join(offending))
    return (not reasons), reasons


def score_rounds(leverage, damage_diff, graph) -> np.ndarray:
    """S_r = damage_diff + SUM_k b_k * x_r[k]. This -- not eta -- is what
    every yardstick consumes."""
    return np.asarray(damage_diff, dtype=float) + np.asarray(leverage, dtype=float) @ np.asarray(
        graph, dtype=float
    )


def _exposure_weighted_mean(graph, exposure) -> float:
    exposure = np.asarray(exposure, dtype=float)
    total = exposure.sum()
    if total <= 0:
        raise ValueError("exposure is empty; cannot normalize")
    return float(np.sum(exposure * np.asarray(graph, dtype=float)) / total)


def construction_normalize(graph, exposure, reference):
    """DEFINES a candidate and is therefore scored. Used by G1a, whose raw
    dP values are probabilities and would otherwise sit ~1000x below the
    shipped scale, changing its balance against damage rather than its
    shape. `exposure` and `reference` must both come from TRAINING rows."""
    target = _exposure_weighted_mean(reference, exposure)
    current = _exposure_weighted_mean(graph, exposure)
    if current == 0:
        raise ValueError("cannot normalize a graph with zero exposure-weighted mean")
    return np.asarray(graph, dtype=float) * (target / current)


def normalize_for_display(graph, exposure):
    """A transform on a COPY, for reading and comparing shapes. Never
    applied before scoring: rescaling b without d changes its strength
    relative to damage and evaluates a candidate nobody proposed."""
    current = _exposure_weighted_mean(graph, exposure)
    if current == 0:
        return np.asarray(graph, dtype=float).copy()
    return np.asarray(graph, dtype=float) * (DISPLAY_MEAN / current)


from app.services.stats_math import back_transform, fit_logistic, standardize

FAMILY_A = ("current_graph", "swing_plugin", "swing_affine", "swing_basis", "pooled", "free")
_FITTED_BASIS = {"swing_affine": "G1b", "swing_basis": "G2", "free": "G4"}


def _fit_and_recover(design_train, y_train, weights, l2, design_test, damage_index,
                     basis, penalty=None):
    """Standardize on TRAIN, fit, back-transform, recover (graph, d).

    Standardization is on training statistics and the coefficients are
    back-transformed before recovery, because b = q/d is a ratio of RAW-unit
    coefficients -- recovering from standardized ones would divide by the
    wrong scale.
    """
    scaled_train, _scaled_test, centre, scale = standardize(design_train, design_test)
    beta = fit_logistic(scaled_train, y_train, weights=weights, l2=l2, penalty=penalty)
    raw = back_transform(beta, centre, scale)
    graph, d = recover_graph(raw, damage_index=damage_index, basis=basis)
    return graph, d


def fit_family_a(name, train, test, table, l2, exposure, shipped, prior=None, controls=None):
    """One Family A candidate, fitted on `train` and scored on `test`.

    train = (leverage, damage_diff, y, weights); test = (leverage,
    damage_diff, weights). `exposure` and `table` must come from TRAINING
    rows -- both feed candidate construction.
    """
    train_leverage, train_damage, y_train, w_train = train
    test_leverage, test_damage, _w_test = test
    controls_train, controls_test = (controls or (None, None))

    def stack(*blocks):
        usable = [np.asarray(b, dtype=float) for b in blocks if b is not None and np.size(b)]
        return np.column_stack(usable)

    n_controls = 0 if controls_train is None else np.asarray(controls_train).shape[1]

    fallback = PARAM_INDEX["fallback"]

    if name == "current_graph":
        graph, d = np.asarray(shipped, dtype=float), 1.0
    elif name == "swing_plugin":
        # Normalize the 25 LATTICE values against the shipped lattice, then
        # pin the fallback. Including a parameter with no state in the
        # normalization would let it move the whole curve's scale.
        scaled = construction_normalize(
            lattice_dp(table), exposure[LATTICE], np.asarray(shipped)[LATTICE]
        )
        graph = np.empty(len(PARAMS))
        graph[LATTICE] = scaled
        graph[fallback] = FALLBACK_WEIGHT
        d = 1.0
    elif name == "pooled":
        if prior is None:
            prior = fit_family_a(
                "swing_plugin", train, test, table, l2, exposure, shipped
            ).graph
        b_prior = np.asarray(prior, dtype=float)
        composite = train_damage + train_leverage @ b_prior
        composite_test = test_damage + test_leverage @ b_prior
        design_train = stack(controls_train, composite, train_leverage)
        design_test = stack(controls_test, composite_test, test_leverage)
        # The composite damage column carries d and MUST stay unpenalised:
        # penalising it drives d and delta to zero together, and delta/d then
        # never converges on the prior. Measured -- see Task 1.
        mask = np.ones(design_train.shape[1])
        mask[n_controls] = 0.0
        delta_graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=np.eye(len(PARAMS)), penalty=mask,
        )
        # recover_graph already divided by d, so delta_graph IS delta/d.
        graph = b_prior + delta_graph
    elif name == "free":
        design_train = stack(controls_train, train_damage, train_leverage)
        design_test = stack(controls_test, test_damage, test_leverage)
        graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=np.eye(len(PARAMS)),
        )
    else:
        # Structured families: the fallback is PINNED at the shipped 100 and
        # rides in the composite damage column, so the basis fits the 25
        # lattice states only.
        basis = basis_for(_FITTED_BASIS[name], table)
        composite = train_damage + FALLBACK_WEIGHT * train_leverage[:, fallback]
        composite_test = test_damage + FALLBACK_WEIGHT * test_leverage[:, fallback]
        design_train = stack(controls_train, composite, train_leverage[:, LATTICE] @ basis)
        design_test = stack(controls_test, composite_test, test_leverage[:, LATTICE] @ basis)
        lattice_graph, d = _fit_and_recover(
            design_train, y_train, w_train, l2, design_test,
            damage_index=n_controls, basis=basis,
        )
        graph = np.empty(len(PARAMS))
        graph[LATTICE] = lattice_graph
        graph[fallback] = FALLBACK_WEIGHT

    deployable, reasons = check_deployable(graph, d, exposure)
    return ScoredCandidate(
        name=name,
        scores=score_rounds(test_leverage, test_damage, graph),
        d=float(d),
        deployable=deployable,
        reasons=tuple(reasons),
        graph=np.asarray(graph, dtype=float),
    )


FAMILY_B = ("stage_a_exact", "kd_split_base", "component_tilt", "component_tilt_symmetric")

_RUNG_SPEC = {
    # (split kill/death?, which axes)
    "stage_a_exact": (False, ("base",)),
    "kd_split_base": (True, ("base",)),
    "component_tilt": (False, ("base", "margin", "total")),
    "component_tilt_symmetric": (True, ("base", "margin", "total")),
}
_AXES = {"base": None, "margin": MARGIN, "total": TOTAL_ALIVE}


def family_b_columns(team_rows, graph, rung):
    """(n_rounds, p) plus column names.

    Each column is SUM_k graph[k] * axis[k] * block[k][component], where
    `block` is the kill half, the death half, or their sum when the rung
    does not split them. `graph` is FIXED -- Family B fits weightings over a
    curve, not the curve itself.
    """
    split, axes = _RUNG_SPEC[rung]
    graph = np.asarray(graph, dtype=float)
    sides = ("kill", "death") if split else ("combined",)

    names: list[str] = []
    for component in COMPONENTS:
        for side in sides:
            for axis in axes:
                label = component if side == "combined" else f"{component}_{side}"
                names.append(f"{label}_{axis}")

    columns = np.zeros((len(team_rows), len(names)))
    for index, row in enumerate(team_rows):
        blocks = {"kill": row.kill, "death": row.death, "combined": row.kill + row.death}
        position = 0
        for component_index in range(len(COMPONENTS)):
            for side in sides:
                block = np.asarray(blocks[side], dtype=float)[:, component_index]
                for axis in axes:
                    weight = graph if _AXES[axis] is None else graph * _AXES[axis]
                    columns[index, position] = float(np.sum(weight * block))
                    position += 1
    return columns, names


def fit_family_b(rung, train, test, graph, l2, controls=None, exposure=None):
    # `exposure` IS used: it gates which negative effective prices count.
    """One rung, fitted on train and scored on test.

    train = (team_rows, y, weights); test = (team_rows, weights).

    The fitted numbers are regression coefficients q, NOT deployable
    weights: damage carries its own coefficient d here too, so every one
    divides by d. An earlier draft claimed they were directly (w, a, t) and
    was wrong.
    """
    train_rows, y_train, w_train = train
    test_rows, _w_test = test
    controls_train, controls_test = (controls or (None, None))
    n_controls = 0 if controls_train is None else np.asarray(controls_train).shape[1]

    train_columns, names = family_b_columns(train_rows, graph, rung)
    test_columns, _ = family_b_columns(test_rows, graph, rung)
    train_damage = np.array([r.damage_diff for r in train_rows], dtype=float)
    test_damage = np.array([r.damage_diff for r in test_rows], dtype=float)

    def stack(controls_block, damage, columns):
        usable = [b for b in (controls_block, damage[:, None], columns) if b is not None]
        return np.column_stack(usable)

    design_train = stack(controls_train, train_damage, train_columns)
    design_test = stack(controls_test, test_damage, test_columns)

    scaled_train, _scaled_test, centre, scale = standardize(design_train, design_test)
    beta = fit_logistic(scaled_train, y_train, weights=w_train, l2=l2)
    raw = back_transform(beta, centre, scale)[1:]
    d = float(raw[n_controls])
    weights = raw[n_controls + 1 :] / d if d != 0 else np.full(len(names), np.nan)

    reasons: list[str] = []
    if not np.isfinite(d) or d <= 0:
        reasons.append(f"damage coefficient d={d:.4g} is not positive; no weighting is recoverable")
    else:
        # An earlier draft checked ONLY d here, so a rung could carry a
        # negative effective price at a well-exposed state and still be
        # marked deployable. Build every surface and check it.
        for label, surface in effective_surfaces(weights, names, graph).items():
            offending = [
                f"{PARAMS[i]}={surface[i]:.1f}"
                for i in range(len(PARAMS))
                if surface[i] < 0 and (exposure is None or exposure[i] >= 1.0)
            ]
            if offending:
                reasons.append(f"negative effective price in {label} at " + ", ".join(offending))

    return ScoredCandidate(
        name=rung,
        scores=test_damage + test_columns @ weights,
        d=d,
        deployable=not reasons,
        reasons=tuple(reasons),
        weights=weights,
    )


def effective_surfaces(weights, names, graph) -> dict:
    """The 26-value price curve each (component, side) ends up with once its
    base weight and tilts are applied. These -- not the raw coefficients --
    are what the report prints and what the deployability gate checks.

    A tilt coefficient is meaningless on its own: its size is relative to its
    base weight, and Stage A's headline finding is that the econ base
    collapses to ~0.
    """
    graph = np.asarray(graph, dtype=float)
    weights = np.asarray(weights, dtype=float)
    multiplier = {"base": np.ones(len(PARAMS)), "margin": MARGIN, "total": TOTAL_ALIVE}
    by_label: dict = {}
    for value, name in zip(weights, names):
        label, axis = name.rsplit("_", 1)
        by_label[label] = by_label.get(label, np.zeros(len(PARAMS))) + value * multiplier[axis]
    return {label: graph * surface for label, surface in by_label.items()}
