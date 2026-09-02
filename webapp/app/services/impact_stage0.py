"""Stage 0: what does Impact, exactly as it ships today, say about winning?

Runs BEFORE any fitting, on the CURRENT stored scores -- which means the
`realized` swing variant, since that is what the live scorer wrote. Stage 0
describes the shipped metric rather than feeding a forward-looking fit, so
the leakage constraint does not apply here.

Cohorts are not optional. Measured 2026-09-01: 7,814 of 8,251 players
(94.7%) have exactly one match, so an uncontrolled within-person
calculation is almost entirely rows whose centred Impact is 0 by
construction.
"""

from dataclasses import dataclass

import numpy as np

from app.services.stats_math import cluster_bootstrap_ci, point_biserial, tercile_buckets

COHORT_RULES = {
    "recurrent": 2,                # >= 2 decided matches      (437 players)
    "per_player_tercile": 9,       # >= 3 matches per bucket   (81 players)
    "per_player_correlation": 10,  # per-player correlation    (71 players)
}


@dataclass
class PlayerMatch:
    player_id: int
    match_id: int
    avg_impact: float
    won: bool


def _by_player(rows) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.player_id, []).append(row)
    return grouped


def filter_cohort(rows, min_matches: int) -> list[PlayerMatch]:
    grouped = _by_player(rows)
    return [row for row in rows if len(grouped[row.player_id]) >= min_matches]


def pooled_relationship(rows) -> dict:
    """Raw pooled Impact vs win/loss. Confounded by between-player skill --
    that is what within_player_centered exists to remove."""
    impacts = np.array([r.avg_impact for r in rows], dtype=float)
    wins = np.array([1 if r.won else 0 for r in rows], dtype=int)
    if len(impacts) == 0:
        return {"n": 0, "point_biserial": float("nan"), "win_rate": float("nan")}
    return {
        "n": len(impacts),
        "players": len(_by_player(rows)),
        "point_biserial": point_biserial(impacts, wins),
        "win_rate": float(wins.mean()),
        "mean_impact_in_wins": float(impacts[wins == 1].mean()) if (wins == 1).any() else float("nan"),
        "mean_impact_in_losses": float(impacts[wins == 0].mean()) if (wins == 0).any() else float("nan"),
    }


def within_player_centered(rows, min_matches: int = 2) -> dict:
    """Each player's Impact minus their OWN mean, then pooled. Removes
    between-player skill level, so what is left is "when this player plays
    above their own baseline, do they win more?"."""
    grouped = _by_player(rows)
    values, labels, eligible = [], [], 0
    for player_rows in grouped.values():
        if len(player_rows) < min_matches:
            continue
        eligible += 1
        mean = float(np.mean([r.avg_impact for r in player_rows]))
        for r in player_rows:
            values.append(r.avg_impact - mean)
            labels.append(1 if r.won else 0)
    if not values:
        return {"n": 0, "players": 0, "point_biserial": float("nan")}
    return {
        "n": len(values),
        "players": eligible,
        "point_biserial": point_biserial(values, labels),
    }


def per_player_correlations(rows, min_matches: int | None = None) -> dict:
    """ONE correlation per eligible player, summarised as a distribution.

    Distinct from a pooled statistic: filtering rows by cohort and re-running
    a pooled correlation does not compute per-player correlations.
    """
    threshold = COHORT_RULES["per_player_correlation"] if min_matches is None else min_matches
    values = []
    for player_rows in _by_player(rows).values():
        if len(player_rows) < threshold:
            continue
        r = point_biserial(
            [row.avg_impact for row in player_rows],
            [1 if row.won else 0 for row in player_rows],
        )
        if np.isfinite(r):
            values.append(float(r))
    if not values:
        return {"players": 0, "values": [], "median": float("nan"),
                "iqr": [float("nan"), float("nan")], "fraction_positive": float("nan")}
    arr = np.array(values)
    q1, q3 = np.percentile(arr, [25, 75])
    return {
        "players": len(values),
        "values": values,
        "median": float(np.median(arr)),
        "iqr": [float(q1), float(q3)],
        "fraction_positive": float((arr > 0).mean()),
    }


def within_player_tercile_lift(rows, min_matches: int | None = None) -> dict:
    """Terciles computed WITHIN each player, then pooled -- the form the
    player page will display ("a top-third game for me"), not global
    terciles after centring."""
    threshold = COHORT_RULES["per_player_tercile"] if min_matches is None else min_matches

    top_wins = top_total = bottom_wins = bottom_total = 0
    eligible = 0
    for player_rows in _by_player(rows).values():
        if len(player_rows) < threshold:
            continue
        eligible += 1
        buckets = tercile_buckets([r.avg_impact for r in player_rows])
        for row, bucket in zip(player_rows, buckets):
            if bucket == 2:
                top_total += 1
                top_wins += 1 if row.won else 0
            elif bucket == 0:
                bottom_total += 1
                bottom_wins += 1 if row.won else 0

    top_rate = top_wins / top_total if top_total else float("nan")
    bottom_rate = bottom_wins / bottom_total if bottom_total else float("nan")
    return {
        "players": eligible,
        "top_win_rate": top_rate,
        "bottom_win_rate": bottom_rate,
        "lift": top_rate - bottom_rate,
        "top_n": top_total,
        "bottom_n": bottom_total,
    }


def _grouped_by_match(rows) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.match_id, []).append(row)
    return grouped


def _ci(metric_fn, rows, draws: int, seed: int):
    """Bootstrap clustered by MATCH. metric_fn receives a flat row list, so
    player means, eligibility and tercile boundaries are all recomputed
    inside each resample rather than held fixed."""
    groups = _grouped_by_match(rows)

    def wrapped(sample):
        flat = [r for rows_ in sample for r in rows_]
        return metric_fn(flat)

    return list(cluster_bootstrap_ci(wrapped, groups, draws=draws, seed=seed))


def stage0_report(rows, roster_player_ids=None, draws: int = 200, seed: int = 0) -> dict:
    """Everything Stage 0 owes the spec, each headline number with a
    match-clustered CI."""
    roster_player_ids = set(roster_player_ids or ())
    roster_rows = [r for r in rows if r.player_id in roster_player_ids]
    recurrent_rows = filter_cohort(rows, COHORT_RULES["recurrent"])

    report = {
        "variant": "realized",
        "note": "stored scores as the live scorer wrote them; not an input to any forward-looking fit",
        "pooled": {
            **pooled_relationship(rows),
            "ci": _ci(lambda r: pooled_relationship(r)["point_biserial"], rows, draws, seed),
        },
        "within_player_centered": {
            **within_player_centered(rows),
            "ci": _ci(lambda r: within_player_centered(r)["point_biserial"], rows, draws, seed),
        },
        "per_player_correlations": per_player_correlations(rows),
        "within_player_terciles": {
            **within_player_tercile_lift(rows),
            "ci": _ci(lambda r: within_player_tercile_lift(r)["lift"], rows, draws, seed),
        },
        "cohorts": {},
    }

    # Every headline number gets an interval, in the cohorts too -- a cohort
    # of 71 players is exactly where an uncertainty-free point estimate
    # misleads most.
    for name, cohort_rows in (("roster", roster_rows), ("recurrent", recurrent_rows)):
        report["cohorts"][name] = {
            "players": len(_by_player(cohort_rows)),
            "pooled": {
                **pooled_relationship(cohort_rows),
                "ci": _ci(lambda r: pooled_relationship(r)["point_biserial"],
                          cohort_rows, draws, seed),
            },
            "within_player_centered": {
                **within_player_centered(cohort_rows),
                "ci": _ci(lambda r: within_player_centered(r)["point_biserial"],
                          cohort_rows, draws, seed),
            },
            "per_player_correlations": {
                **per_player_correlations(cohort_rows),
                "median_ci": _ci(lambda r: per_player_correlations(r)["median"],
                                 cohort_rows, draws, seed),
            },
            "within_player_terciles": {
                **within_player_tercile_lift(cohort_rows),
                "ci": _ci(lambda r: within_player_tercile_lift(r)["lift"],
                          cohort_rows, draws, seed),
            },
        }

    report["per_player_correlations"]["median_ci"] = _ci(
        lambda r: per_player_correlations(r)["median"], rows, draws, seed
    )
    return report
