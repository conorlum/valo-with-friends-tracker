"""kill_order_leverage stays on the LEGACY time factor under Impact v4
(plan 2026-09-21-impact-v4, section 2.3, review finding R8).

It reconstructs legacy ex-ante components for the evaluation and refit tooling
(kill_order_curves, kill_order_refit); no route reaches it. Following the
runtime's decided-only flag there would mix v4 timing into a legacy
decomposition, so it asks for the legacy factor explicitly, and this pins it.
"""
from app.services import kill_order_leverage
from app.services.kill_order_leverage import COMPONENTS, kill_terms_for_match
from tests.test_kill_order_leverage import kill, make_match

TIME = COMPONENTS.index("time")


def _defused_match(t):
    rounds, outcomes, stats, players, kills = make_match([kill(1, 6, t)], outcome="Team B Defuse Win")
    rnd = rounds[5]
    rnd.planted, rnd.plant_time = True, 30.0
    rnd.defused, rnd.defuse_time = True, 60.0
    return rounds, outcomes, stats, players, kills


def test_a_kill_after_the_defuse_keeps_the_legacy_half():
    (term,) = kill_terms_for_match(*_defused_match(61.0))[5]
    assert term.kill[TIME] == 0.5          # v4 would pay 0.0
    assert term.death_untraded[TIME] == 0.5


def test_a_post_plant_kill_keeps_the_legacy_ramp():
    (term,) = kill_terms_for_match(*_defused_match(40.0))[5]
    assert term.kill[TIME] == 1 + 10 / 53   # v4 would pay 1.0


def test_both_calls_ask_for_the_legacy_factor_explicitly(monkeypatch):
    calls = []
    original = kill_order_leverage._time_factor

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(kill_order_leverage, "_time_factor", spy)
    kill_terms_for_match(*_defused_match(40.0))
    assert len(calls) == 2
    assert all(call.get("decided_only") is False for call in calls)
