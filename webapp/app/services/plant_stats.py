"""Pure plant statistics; percentages use 0..100 and adjusted percentage points.

Rows are mappings or attribute objects with the round_plants fields. Spots are
an ordered sequence of mappings with id, site, u, v and radius, already selected
for one layout. Spot ids are unique within each site; 'outside' is reserved.
No source filtering or deduplication happens here: the caller supplies the
population. No database, scored model, or Impact table is read.
"""
from collections import defaultdict
from collections.abc import Mapping
import hashlib
import math
from statistics import median

from app.services import stats_math


PRIOR_STRENGTH = 8


def _get(row, key):
    return row[key] if isinstance(row, Mapping) else getattr(row, key)


def _usable(row):
    return all(_get(row, key) is not None for key in (
        "attackers_alive", "defenders_alive", "attackers_won"))


def plant_band(t):
    """DB-clock round seconds; missing times have no band (alive-only backoff)."""
    if t is None or not math.isfinite(t):
        return None
    if t < 45:
        return "<45"
    return "45-70" if t < 70 else ">=70"


def _fold(match_id, folds):
    # Python's hash() is process-randomized; this split must survive restarts.
    digest = hashlib.sha256(str(match_id).encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % folds


def expected_oof(rows, folds=5):
    """Aligned out-of-fold probabilities, or None for unusable/untrained rows.

    Entire matches share a stable SHA-256 fold. Both levels of shrinkage use
    strength 8: alive-pair -> training overall, then time-cell -> alive-pair.
    Missing times use the pair directly. An empty training population yields
    None, rather than borrowing an outcome from the held-out match.
    """
    if not isinstance(folds, int) or folds < 2:
        raise ValueError("folds must be an integer >= 2")
    rows = list(rows)
    fold_ids = [_fold(_get(row, "match_id"), folds) for row in rows]
    usable = [_usable(row) for row in rows]
    result = [None] * len(rows)
    for held_out in sorted(set(fold_ids)):
        pairs = defaultdict(lambda: [0, 0])
        cells = defaultdict(lambda: [0, 0])
        n = wins = 0
        for row, fold_id, valid in zip(rows, fold_ids, usable):
            if fold_id == held_out or not valid:
                continue
            pair = (_get(row, "attackers_alive"), _get(row, "defenders_alive"))
            won = int(_get(row, "attackers_won"))
            n += 1
            wins += won
            pairs[pair][0] += won
            pairs[pair][1] += 1
            band = plant_band(_get(row, "plant_time"))
            if band is not None:
                cells[(*pair, band)][0] += won
                cells[(*pair, band)][1] += 1
        if not n:
            continue
        overall = wins / n
        for i, (row, fold_id, valid) in enumerate(zip(rows, fold_ids, usable)):
            if fold_id != held_out or not valid:
                continue
            pair = (_get(row, "attackers_alive"), _get(row, "defenders_alive"))
            pair_wins, pair_n = pairs[pair]
            pair_rate = (pair_wins + PRIOR_STRENGTH * overall) / (pair_n + PRIOR_STRENGTH)
            band = plant_band(_get(row, "plant_time"))
            if band is None:
                result[i] = pair_rate
            else:
                cell_wins, cell_n = cells[(*pair, band)]
                result[i] = (cell_wins + PRIOR_STRENGTH * pair_rate) / (cell_n + PRIOR_STRENGTH)
    return result


def assign_spots(rows, spots):
    """Aligned spot ids, or 'outside'; nearest containing centre on the same site.

    Circle boundaries are included. Equal distances retain committed spot order.
    """
    spots = list(spots)
    assigned = []
    for row in rows:
        closest = "outside"
        best_distance = math.inf
        for spot in spots:
            if spot["site"] != _get(row, "site"):
                continue
            distance = (spot["u"] - _get(row, "u")) ** 2 + (spot["v"] - _get(row, "v")) ** 2
            if distance <= spot["radius"] ** 2 and distance < best_distance:
                closest, best_distance = spot["id"], distance
        assigned.append(closest)
    return assigned


def _residual_mean(groups):
    return 100 * sum(sum(group) for group in groups) / sum(len(group) for group in groups)


def _summary(entries, site_rate, min_n):
    # Each entry pairs the original row with its expectation fitted once on the
    # full population. Never refit expectations on a spatial subset or resample.
    usable = [(row, expected) for row, expected in entries if _usable(row)]
    adjusted = [(row, expected) for row, expected in usable if expected is not None]
    times = [_get(row, "plant_time") for row, _ in entries
             if plant_band(_get(row, "plant_time")) is not None]
    result = dict(n=len(entries), usable_n=len(usable), adjusted_n=len(adjusted),
                  plant_time_n=len(times), raw_pct=None, shrunk_pct=None,
                  adjusted_pct=None, adjusted_ci=None, adjusted_display=None,
                  adjusted_ci_display=None, defused_pct=None,
                  median_plant_time=median(times) if times else None)
    if len(entries) < min_n or not usable:
        return result
    wins = sum(_get(row, "attackers_won") for row, _ in usable)
    result["raw_pct"] = 100 * wins / len(usable)
    result["shrunk_pct"] = 100 * (wins + PRIOR_STRENGTH * site_rate) / (len(usable) + PRIOR_STRENGTH)
    result["defused_pct"] = 100 * sum(_get(row, "defused") for row, _ in usable) / len(usable)
    if adjusted:
        groups = defaultdict(list)
        for row, expected in adjusted:
            groups[_get(row, "match_id")].append(int(_get(row, "attackers_won")) - expected)
        estimate = _residual_mean(list(groups.values()))
        interval = stats_math.cluster_bootstrap_ci(_residual_mean, groups, draws=500, seed=0)
        result.update(adjusted_pct=estimate, adjusted_ci=interval,
                      adjusted_display=f"{estimate:+.1f}%",
                      adjusted_ci_display=tuple(f"{value:+.1f}%" for value in interval))
    return result


def spot_rows(rows, spots, min_n=100, expectations=None):
    """Return {site: {counts, statistics, outside, spots: [...]}} for one layout.

    Every configured spot is present, even when empty. Observed sites without
    spots are present too. n includes excluded rounds and both sources;
    usable_n is the win/defuse denominator, adjusted_n counts paired residuals,
    and plant_time_n counts known DB times. Rates and intervals are None below
    min_n total positions or without usable outcomes. Times include all plants.
    The site prior includes outside-circle plants; it uses usable outcomes only.

    expectations is an optional sequence aligned with rows, used without
    refitting. Callers showing a subset should fit expected_oof on the whole
    population (all layouts), then pass the values aligned with their subset.
    None fits on the supplied rows; a length mismatch raises ValueError.
    """
    if min_n < 1:
        raise ValueError("min_n must be >= 1")
    rows, spots = list(rows), list(spots)
    expectations = expected_oof(rows) if expectations is None else list(expectations)
    if len(expectations) != len(rows):
        raise ValueError("expectations must have the same length as rows")
    assignments = assign_spots(rows, spots)
    sites = dict.fromkeys([spot["site"] for spot in spots] + [_get(row, "site") for row in rows])
    site_entries = defaultdict(list)
    spot_entries = defaultdict(list)
    outside = defaultdict(int)
    for row, expected, assigned in zip(rows, expectations, assignments):
        site = _get(row, "site")
        site_entries[site].append((row, expected))
        if assigned == "outside":
            outside[site] += 1
        else:
            spot_entries[site, assigned].append((row, expected))
    result = {}
    for site in sites:
        usable = [row for row, _ in site_entries[site] if _usable(row)]
        site_rate = sum(_get(row, "attackers_won") for row in usable) / len(usable) if usable else None
        summary = _summary(site_entries[site], site_rate, min_n)
        summary.update(site=site, outside=outside[site], spots=[
            {**spot, **_summary(spot_entries[site, spot["id"]], site_rate, min_n)}
            for spot in spots if spot["site"] == site
        ])
        result[site] = summary
    return result
