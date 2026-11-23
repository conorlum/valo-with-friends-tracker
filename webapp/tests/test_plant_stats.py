"""Pure plant statistics, with synthetic rows in the round_plants shape."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services import plant_stats as stats
from scripts import test_catalog


SPOTS = [
    dict(id="default", site="A", u=100, v=100, radius=50),
    dict(id="corner", site="A", u=140, v=100, radius=50),
    dict(id="default", site="B", u=100, v=100, radius=50),
]


def row(match_id=1, **changes):
    return {"match_id": match_id, "site": "A", "u": 100, "v": 100,
            "plant_time": 50.0, "attackers_alive": 3, "defenders_alive": 2,
            "attackers_won": True, "defused": False, **changes}


@pytest.mark.parametrize("time, band", [
    (0, "<45"), (44.999, "<45"), (45, "45-70"), (69.999, "45-70"),
    (70, ">=70"), (100, ">=70"), (None, None),
])
def test_plant_band(time, band):
    assert stats.plant_band(time) == band


def test_two_wins_shrink_toward_site_including_outside_plants():
    rows = [row(1), row(2)] + [row(i, u=500, attackers_won=False) for i in range(3, 11)]
    site = stats.spot_rows(rows, SPOTS, min_n=1)["A"]
    spot = site["spots"][0]
    assert spot["n"] == spot["usable_n"] == 2
    assert spot["raw_pct"] == 100
    assert spot["shrunk_pct"] == pytest.approx(100 * (2 + 8 * 0.2) / 10)
    assert site["outside"] == 8
    assert site["raw_pct"] == 20


def test_adjusted_zero_and_explicit_sign_when_actual_equals_expected():
    rows = [row(i, attackers_won=won) for i in range(20) for won in (True, False)]
    result = stats.spot_rows(rows, SPOTS, min_n=1)["A"]["spots"][0]
    assert result["adjusted_pct"] == pytest.approx(0)
    assert result["adjusted_display"] == "+0.0%"
    assert result["adjusted_ci"] == pytest.approx((0, 0))
    assert result["adjusted_n"] == len(rows)


def test_supplied_expectations_are_used_without_refitting(monkeypatch):
    def forbidden_fit(rows):
        raise AssertionError("supplied expectations must not be refitted")

    monkeypatch.setattr(stats, "expected_oof", forbidden_fit)
    rows = [row(1), row(2, attackers_won=False), row(3)]
    expectations = (0.2, 0.6, None)
    site = stats.spot_rows(rows, SPOTS, min_n=1, expectations=expectations)["A"]
    for result in (site, site["spots"][0]):
        assert result["n"] == result["usable_n"] == 3
        assert result["adjusted_n"] == 2
        assert result["adjusted_pct"] == pytest.approx(10)
        assert result["adjusted_display"] == "+10.0%"


@pytest.mark.parametrize("expectations", [[], [0.5], [0.5] * 3])
def test_expectations_length_must_match_rows(expectations):
    with pytest.raises(ValueError, match="length"):
        stats.spot_rows([row(1), row(2)], SPOTS, expectations=expectations)


def test_subset_uses_whole_population_expectations_instead_of_refitting():
    subset = [row(i, asset_key="Ascent") for i in range(20)]
    population = subset + [row(i, asset_key="Bind", attackers_won=False)
                           for i in range(20, 60)]
    expectations = stats.expected_oof(population)[:len(subset)]
    supplied = stats.spot_rows(subset, SPOTS, min_n=1, expectations=expectations)["A"]["spots"][0]
    refitted = stats.spot_rows(subset, SPOTS, min_n=1)["A"]["spots"][0]
    assert all(value is not None for value in expectations)
    assert supplied["adjusted_pct"] == pytest.approx(
        100 * sum(1 - value for value in expectations) / len(subset))
    assert supplied["adjusted_pct"] > 0
    assert refitted["adjusted_pct"] == pytest.approx(0)


def test_own_match_never_affects_its_expected_value_and_order_is_stable():
    rows = [row(i, attackers_won=i % 3 == 0, plant_time=40 + i % 4 * 15)
            for i in range(30) for _ in range(3)]
    original = deepcopy(rows)
    before = stats.expected_oof(rows)
    for match_id in range(30):
        changed = [{**r, "attackers_won": not r["attackers_won"]}
                   if r["match_id"] == match_id else r for r in rows]
        after = stats.expected_oof(changed)
        assert [p for r, p in zip(rows, after) if r["match_id"] == match_id] == [
            p for r, p in zip(rows, before) if r["match_id"] == match_id]
    assert stats.expected_oof(reversed(rows)) == list(reversed(before))
    assert rows == original


def test_hierarchical_strength_eight_and_whole_fold_exclusion():
    def fold(match_id):
        return int.from_bytes(hashlib.sha256(str(match_id).encode()).digest(), "big") % 5

    target = 0
    training_ids = [i for i in range(1, 100) if fold(i) != fold(target)][:3]
    same_fold = next(i for i in range(1, 100) if fold(i) == fold(target))
    rows = [row(target), row(training_ids[0]),
            row(training_ids[1], plant_time=80, attackers_won=False),
            row(training_ids[2], attackers_alive=1, attackers_won=False)]
    overall = 1 / 3
    pair = (1 + 8 * overall) / (2 + 8)
    cell = (1 + 8 * pair) / (1 + 8)
    assert stats.expected_oof(rows)[0] == pytest.approx(cell)
    rows += [row(same_fold)] * 20
    assert stats.expected_oof(rows)[0] == pytest.approx(cell)


def test_missing_time_uses_alive_pair_and_unusable_rows_are_excluded():
    rows = [row(i, plant_time=None, attackers_won=i % 2 == 0) for i in range(20)]
    before = stats.expected_oof(rows)
    assert all(value is not None for value in before)
    invalid = row(99, attackers_alive=None, defenders_alive=None, attackers_won=None)
    assert stats.expected_oof(rows + [invalid]) == before + [None]
    assert stats.expected_oof([row()]) == [None]
    with pytest.raises(ValueError):
        stats.expected_oof(rows, folds=1)


def test_site_prior_is_local_and_single_match_has_no_adjusted_rate():
    a_rows = [row(1)] * 2 + [row(1, u=500, attackers_won=False)] * 8
    b_rows = [row(2, site="B")] * 100
    a = stats.spot_rows(a_rows, SPOTS, min_n=1)["A"]["spots"][0]
    combined = stats.spot_rows(a_rows + b_rows, SPOTS, min_n=1)["A"]["spots"][0]
    assert combined["shrunk_pct"] == a["shrunk_pct"]
    assert a["raw_pct"] == 100
    assert a["adjusted_pct"] is a["adjusted_ci"] is None
    assert a["adjusted_n"] == 0


def test_threshold_and_null_outcomes_count_positions_only():
    rows = [row(i, attackers_won=i % 2 == 0, defused=i % 2 != 0)
            for i in range(99)]
    small = stats.spot_rows(rows, SPOTS)["A"]["spots"][0]
    for key in ("raw_pct", "shrunk_pct", "adjusted_pct", "adjusted_ci", "defused_pct",
                "adjusted_display", "adjusted_ci_display"):
        assert small[key] is None
    excluded = row(100, attackers_alive=None, defenders_alive=None,
                   attackers_won=None, defused=True)
    result = stats.spot_rows(rows + [excluded], SPOTS)["A"]["spots"][0]
    assert result["n"] == 100
    assert result["usable_n"] == 99
    assert result["raw_pct"] == pytest.approx(100 * 50 / 99)
    assert result["defused_pct"] == pytest.approx(100 * 49 / 99)
    assert result["median_plant_time"] == 50
    assert result["plant_time_n"] == 100
    assert result["adjusted_pct"] is not None


def test_nearest_containing_circle_site_boundary_tie_and_outside():
    rows = [row(u=130), row(u=500), row(u=100, v=150), row(u=120),
            row(site="B", u=100), row(site="C")]
    assert stats.assign_spots(rows, SPOTS) == ["corner", "outside", "default", "default",
                                                      "default", "outside"]
    result = stats.spot_rows(rows, SPOTS)
    assert result["A"]["outside"] == result["C"]["outside"] == 1
    assert result["B"]["outside"] == 0
    assert [spot["n"] for spot in result["A"]["spots"]] == [2, 1]
    assert result["B"]["spots"][0]["n"] == 1


def test_match_clustering_widens_interval_and_contains_estimate(monkeypatch):
    # Isolate clustering from fitted expectations: 20 correlated wins, 20 losses.
    monkeypatch.setattr(stats, "expected_oof", lambda rows: [0.5] * len(rows))
    clustered = [row(i, attackers_won=i % 2 == 0) for i in range(4) for _ in range(20)]
    independent = [{**r, "match_id": i} for i, r in enumerate(clustered)]
    a = stats.spot_rows(clustered, SPOTS, min_n=1)["A"]["spots"][0]
    b = stats.spot_rows(independent, SPOTS, min_n=1)["A"]["spots"][0]
    lo, hi = a["adjusted_ci"]
    assert lo <= a["adjusted_pct"] <= hi
    assert hi - lo > b["adjusted_ci"][1] - b["adjusted_ci"][0]
    assert a["adjusted_ci_display"][0].startswith("-")
    assert a["adjusted_ci_display"][1].startswith("+")
    assert stats.spot_rows(clustered, SPOTS, min_n=1)["A"]["spots"][0] == a


def test_bootstrap_uses_500_draws_seed_zero_and_match_groups(monkeypatch):
    calls = []
    original = stats.stats_math.cluster_bootstrap_ci

    def capture(fn, groups, **kwargs):
        calls.append((groups, kwargs))
        return original(fn, groups, **kwargs)

    monkeypatch.setattr(stats.stats_math, "cluster_bootstrap_ci", capture)
    rows = [row(i, attackers_won=won) for i in range(10) for won in (True, False)]
    stats.spot_rows(rows, SPOTS, min_n=1)
    assert calls
    for groups, kwargs in calls:
        assert kwargs == dict(draws=500, seed=0)
        assert len(groups) == 10
        assert all(len(group) == 2 for group in groups.values())


def test_empty_and_all_excluded_rows_are_json_safe_and_plain_objects_work():
    result = stats.spot_rows([], SPOTS)
    assert set(result) == {"A", "B"}
    assert result["A"]["n"] == result["A"]["outside"] == 0
    rows = [row(i, attackers_alive=None, defenders_alive=None, attackers_won=None,
                plant_time=None) for i in range(100)]
    result = stats.spot_rows([SimpleNamespace(**r) for r in rows], SPOTS)
    spot = result["A"]["spots"][0]
    assert spot["n"] == 100 and spot["usable_n"] == 0
    assert spot["raw_pct"] is spot["adjusted_ci"] is spot["median_plant_time"] is None
    json.dumps(result, allow_nan=False)


def test_plant_suite_owns_stats_exactly_and_preserves_timing_owner():
    catalog = test_catalog.load_catalog()
    assert "tests/test_plant_stats.py" in catalog["plants"]["tests"]
    assert "app/services/plant_stats.py" in catalog["plants"]["guards"]
    owners = test_catalog.suite_of(catalog, ["tests/test_plant_window.py"])
    assert owners["tests/test_plant_window.py"] == ["impact-timing"]
