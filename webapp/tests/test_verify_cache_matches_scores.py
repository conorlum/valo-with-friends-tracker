"""The agreement check must fail on a VALUE, not merely on a missing row.

A checker that only notices absent or undecodable cache rows would pass on exactly
the failure it exists for: a blob that decodes cleanly and disagrees with the scores.
So the central test here alters one cached average in memory and requires a
complaint naming that field.

Nothing here touches a database: `_compare_player_scope` is a pure function of a
decoded blob, the ordered match list, and the score aggregates.
"""

from types import SimpleNamespace

from scripts.verify_cache_matches_scores import (
    DEFAULT_TOLERANCE,
    _compare_player_scope,
    _scope_match_player_ids,
)
from app.services.player_data import RECENT_MATCH_LIMIT


def _cached(matches, overall_impact, overall_death):
    """A stand-in for a decoded CachedPlayerViews, duck-typed to what the checker reads."""
    return SimpleNamespace(profile=SimpleNamespace(
        matches=[SimpleNamespace(match=SimpleNamespace(external_id=ext),
                                 average_impact=ai, average_kill_impact=ak,
                                 average_death_impact=ad)
                 for ext, ai, ak, ad in matches],
        overall_average_impact=overall_impact,
        overall_average_death_impact=overall_death,
    ))


PLAYER = SimpleNamespace(display_name="TestPlayer#NA1")
# one match, two score rows: impact 100/200, kill 10/20, death 4/6
ORDERED = [(1, 11, "ext-a")]
AGGREGATES = {1: (2, 300.0, 30.0, 10.0)}
TRUE_AVG_IMPACT, TRUE_AVG_KILL, TRUE_AVG_DEATH = 150.0, 15.0, 5.0


def _run(cached):
    return _compare_player_scope(PLAYER, "career", cached, ORDERED, AGGREGATES, DEFAULT_TOLERANCE)


def test_agreeing_cache_reports_nothing():
    cached = _cached([("ext-a", TRUE_AVG_IMPACT, TRUE_AVG_KILL, TRUE_AVG_DEATH)],
                     TRUE_AVG_IMPACT, TRUE_AVG_DEATH)
    assert _run(cached) == []


def test_a_single_altered_average_is_caught():
    """The mutation that matters: structurally valid, decodes fine, one wrong number."""
    cached = _cached([("ext-a", TRUE_AVG_IMPACT + 0.5, TRUE_AVG_KILL, TRUE_AVG_DEATH)],
                     TRUE_AVG_IMPACT, TRUE_AVG_DEATH)
    problems = _run(cached)
    assert len(problems) == 1
    assert "average_impact" in problems[0]
    assert "ext-a" in problems[0]
    assert "150.5" in problems[0]


def test_each_per_match_field_is_checked_independently():
    for field, altered in (
            ("average_impact", (TRUE_AVG_IMPACT + 1, TRUE_AVG_KILL, TRUE_AVG_DEATH)),
            ("average_kill_impact", (TRUE_AVG_IMPACT, TRUE_AVG_KILL + 1, TRUE_AVG_DEATH)),
            ("average_death_impact", (TRUE_AVG_IMPACT, TRUE_AVG_KILL, TRUE_AVG_DEATH + 1))):
        problems = _run(_cached([("ext-a", *altered)], TRUE_AVG_IMPACT, TRUE_AVG_DEATH))
        assert any(field in p for p in problems), f"{field} was not checked"


def test_a_wrong_overall_average_is_caught_even_when_every_match_agrees():
    """The rollback shape: per-match numbers restored, the pooled figure stale."""
    cached = _cached([("ext-a", TRUE_AVG_IMPACT, TRUE_AVG_KILL, TRUE_AVG_DEATH)],
                     TRUE_AVG_IMPACT + 7, TRUE_AVG_DEATH)
    problems = _run(cached)
    assert len(problems) == 1
    assert "overall_average_impact" in problems[0]


def test_a_missing_match_is_caught_and_stops_the_value_comparison():
    problems = _run(_cached([], TRUE_AVG_IMPACT, TRUE_AVG_DEATH))
    assert len(problems) == 1
    assert "match list differs" in problems[0]
    assert "absent from cache" in problems[0]


def test_the_same_matches_in_a_different_order_is_a_difference():
    ordered = [(1, 11, "ext-a"), (2, 12, "ext-b")]   # newest first
    aggregates = {1: (1, 100.0, 10.0, 1.0), 2: (1, 200.0, 20.0, 2.0)}
    # correct would be oldest first: ext-b then ext-a. This is the wrong way round.
    cached = _cached([("ext-a", 100.0, 10.0, 1.0), ("ext-b", 200.0, 20.0, 2.0)], 150.0, 1.5)
    problems = _compare_player_scope(PLAYER, "career", cached, ordered, aggregates, DEFAULT_TOLERANCE)
    assert len(problems) == 1
    assert "different order" in problems[0]


def test_overall_is_row_weighted_not_a_mean_of_match_means():
    """A 1-round match and a 100-round match must not count equally: the profile
    pools score rows and divides once, and so must this."""
    ordered = [(1, 11, "ext-a"), (2, 12, "ext-b")]
    aggregates = {1: (1, 300.0, 0.0, 0.0), 2: (99, 99.0, 0.0, 0.0)}
    row_weighted = (300.0 + 99.0) / 100
    mean_of_means = (300.0 + 1.0) / 2
    cached = _cached([("ext-b", 1.0, 0.0, 0.0), ("ext-a", 300.0, 0.0, 0.0)], row_weighted, 0.0)
    assert _compare_player_scope(PLAYER, "career", cached, ordered, aggregates,
                                 DEFAULT_TOLERANCE) == []
    wrong = _cached([("ext-b", 1.0, 0.0, 0.0), ("ext-a", 300.0, 0.0, 0.0)], mean_of_means, 0.0)
    assert any("overall_average_impact" in p for p in
               _compare_player_scope(PLAYER, "career", wrong, ordered, aggregates, DEFAULT_TOLERANCE))


def test_float_noise_within_tolerance_is_not_reported():
    cached = _cached([("ext-a", TRUE_AVG_IMPACT + 1e-12, TRUE_AVG_KILL, TRUE_AVG_DEATH)],
                     TRUE_AVG_IMPACT, TRUE_AVG_DEATH)
    assert _run(cached) == []


def test_recent_scope_window_is_newest_n_but_returned_oldest_first():
    """Both conventions at once, because reversing either one produces a
    difference that looks real. `ordered` is newest first; the recent window is
    its first RECENT_MATCH_LIMIT entries; the result is oldest first, because
    PlayerProfile.matches is oldest first and the router reverses it to display."""
    ordered = [(i, 100 + i, f"ext-{i}") for i in range(RECENT_MATCH_LIMIT + 5)]
    window = _scope_match_player_ids(ordered, "recent")
    assert len(window) == RECENT_MATCH_LIMIT
    # the newest RECENT_MATCH_LIMIT entries -- ext-30..ext-34 are excluded
    assert {e for _mp, _m, e in window} == {f"ext-{i}" for i in range(RECENT_MATCH_LIMIT)}
    # ...oldest first, so the newest match of the window is LAST
    assert window[0][2] == f"ext-{RECENT_MATCH_LIMIT - 1}"
    assert window[-1][2] == "ext-0"
    assert _scope_match_player_ids(ordered, "career") == list(reversed(ordered))


def test_an_unscored_match_inside_the_window_does_not_shift_it():
    """The subtlety this checker exists around: the window is taken before the
    profile drops unscored matches, so an unscored one must not pull an older
    match into scope."""
    ordered = [(i, 100 + i, f"ext-{i}") for i in range(RECENT_MATCH_LIMIT + 5)]
    # match_player 0 (the newest) has no scores; every other one in the window does
    aggregates = {i: (1, 10.0, 1.0, 1.0) for i in range(1, RECENT_MATCH_LIMIT)}
    cached = _cached(
        [(f"ext-{i}", 10.0, 1.0, 1.0) for i in reversed(range(1, RECENT_MATCH_LIMIT))],
        10.0, 1.0)
    assert _compare_player_scope(PLAYER, "recent", cached, ordered, aggregates,
                                 DEFAULT_TOLERANCE) == []
    # and ext-30 must NOT have been pulled in to backfill the unscored slot
    assert all(m.match.external_id != f"ext-{RECENT_MATCH_LIMIT}"
               for m in cached.profile.matches)
