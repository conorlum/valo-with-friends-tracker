"""The weight-solving tool's arithmetic.

It answers: what A/D/B/C make each term average a declared PERCENTAGE of the
Impact score? Nothing here is fitted to outcomes -- it is a scale convention,
the same kind of choice as ECON_SCALE, and the target percentages are the
owner's input.
"""
import pytest

from scripts.solve_impact_weights import (
    achieved_shares,
    derive_weights,
    match_shares,
    solve_weights,
    targets_from_parts,
)

TERMS = ("damage", "assists", "leverage", "econ")


def test_parts_and_split_expand_to_percentages():
    targets = targets_from_parts("3:2:2", "4:1")
    assert targets["damage"] == pytest.approx(3 / 7 * 4 / 5)
    assert targets["assists"] == pytest.approx(3 / 7 * 1 / 5)
    assert targets["leverage"] == pytest.approx(2 / 7)
    assert targets["econ"] == pytest.approx(2 / 7)
    assert sum(targets.values()) == pytest.approx(1.0)


def test_match_shares_are_absolute_shares_averaged_over_players():
    # Two players: the first is all damage, the second splits damage/leverage.
    players = [
        {"damage": 100, "assists": 0, "leverage": 0, "econ": 0},
        {"damage": 50, "assists": 0, "leverage": -50, "econ": 0},
    ]
    shares = match_shares(players, {t: 1.0 for t in TERMS})
    assert shares["damage"] == pytest.approx((1.0 + 0.5) / 2)
    assert shares["leverage"] == pytest.approx((0.0 + 0.5) / 2)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_a_negative_term_counts_by_magnitude_not_sign():
    positive = match_shares([{"damage": 50, "assists": 0, "leverage": 50, "econ": 0}], {t: 1.0 for t in TERMS})
    negative = match_shares([{"damage": 50, "assists": 0, "leverage": -50, "econ": 0}], {t: 1.0 for t in TERMS})
    assert positive == negative


def test_derive_weights_scales_each_term_by_target_over_its_share():
    shares = {"damage": 0.80, "assists": 0.04, "leverage": 0.12, "econ": 0.04}
    targets = {"damage": 0.40, "assists": 0.08, "leverage": 0.24, "econ": 0.28}
    weights = derive_weights(shares, targets)
    assert weights["damage"] == pytest.approx(0.5)
    assert weights["assists"] == pytest.approx(2.0)
    assert weights["leverage"] == pytest.approx(2.0)
    assert weights["econ"] == pytest.approx(7.0)


def test_a_term_with_no_contribution_is_reported_rather_than_dividing_by_zero():
    weights = derive_weights({"damage": 1.0, "assists": 0.0, "leverage": 0.0, "econ": 0.0},
                             {t: 0.25 for t in TERMS})
    assert weights["damage"] == pytest.approx(0.25)
    assert weights["assists"] is None
    assert weights["econ"] is None


def test_solving_reaches_the_targets_where_one_ratio_pass_does_not():
    """Shares are not linear in the weights: scaling one term changes every
    other term's denominator, so the single ratio pass overshoots."""
    corpus = [
        [{"damage": 900, "assists": 30, "leverage": 200, "econ": 40},
         {"damage": 400, "assists": 60, "leverage": -300, "econ": -80}],
        [{"damage": 1200, "assists": 10, "leverage": 50, "econ": 5},
         {"damage": 300, "assists": 90, "leverage": 400, "econ": 150}],
    ]
    targets = targets_from_parts("3:2:2", "4:1")
    one_pass = derive_weights(_mean_shares(corpus, {t: 1.0 for t in TERMS}), targets)
    solved = solve_weights(corpus, targets, iterations=200)

    after_one_pass = achieved_shares(corpus, one_pass)
    after_solving = achieved_shares(corpus, solved)
    for term in TERMS:
        assert abs(after_solving[term] - targets[term]) < 0.005
    assert max(abs(after_one_pass[t] - targets[t]) for t in TERMS) > \
        max(abs(after_solving[t] - targets[t]) for t in TERMS)


def test_solution_is_scale_free_relative_to_damage():
    corpus = [[{"damage": 500, "assists": 50, "leverage": 250, "econ": 100}]]
    targets = targets_from_parts("3:2:2", "4:1")
    solved = solve_weights(corpus, targets, iterations=200)
    doubled = solve_weights([[{k: v * 2 for k, v in p.items()} for p in m] for m in corpus],
                            targets, iterations=200)
    ratios = {t: solved[t] / doubled[t] for t in TERMS}
    assert max(ratios.values()) == pytest.approx(min(ratios.values()), rel=1e-6)


def _mean_shares(corpus, weights):
    per_match = [match_shares(m, weights) for m in corpus]
    return {t: sum(s[t] for s in per_match) / len(per_match) for t in TERMS}
