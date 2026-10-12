"""P08 (docs/superpowers/plans/2026-10-06-replay-player-state-impl.md): the player-state work makes exactly the
stored data stale that it changes. Semantic input mutations, not constants restated: the molly allow-list (code in
app/control/mollies.py) is pinned to CONTROL_REVISION like the engine's other constants, a prose edit to it is
not; the Abyss barrier fix changes Abyss's control fingerprint and no other map's inputs; CONTROL 9 and
CONDENSE 15 each change the control fingerprint, and through it the gaps fingerprint."""

from __future__ import annotations

from app.control import mollies as mo
from app.replays import control_format as cf
from app.replays import format as fmt
from app.services import replay_control as rc
from app.services import replay_gaps
from tests.replays.test_control_format import _constants_digest

LINK = {"sides": {"0": "attack", "5": "defense"}, "db_deaths": []}
SOURCE = "0" * 64
BASE_ABYSS_BARRIER = "279e647ec456"        # index.json barrier_sha on the base, cf75c29


def _fingerprint(recipe: str, geometry: dict) -> str:
    return cf.fingerprint(recipe, SOURCE, LINK, geometry)


def test_dropping_a_zone_from_the_molly_allow_list_changes_the_pinned_digest(monkeypatch):
    before = _constants_digest()
    monkeypatch.setattr(mo, "MOLLY_KEYS", mo.MOLLY_KEYS - {"Killjoy_4_BeeSwarm_Damage"})
    assert _constants_digest() != before


def test_adding_a_zone_to_the_molly_allow_list_changes_the_pinned_digest(monkeypatch):
    before = _constants_digest()
    monkeypatch.setattr(mo, "MOLLY_KEYS", mo.MOLLY_KEYS | {"Viper_Snake_Bite"})
    assert _constants_digest() != before


def test_a_prose_edit_to_the_molly_module_changes_nothing(monkeypatch):
    before = _constants_digest()
    monkeypatch.setattr(mo, "__doc__", "reworded")
    assert _constants_digest() == before


def test_the_abyss_barrier_fix_makes_abyss_control_and_gaps_stale():
    recipe = fmt.recipe("p" * 12, "a" * 8)
    now = rc.geometry_inputs("Abyss")
    assert now["barrier"] not in (None, BASE_ABYSS_BARRIER)
    then = dict(now, barrier=BASE_ABYSS_BARRIER)
    assert _fingerprint(recipe, now) != _fingerprint(recipe, then)
    assert (replay_gaps.gap_fingerprint(_fingerprint(recipe, now), "Abyss", chokes=None, hearing="h")
            != replay_gaps.gap_fingerprint(_fingerprint(recipe, then), "Abyss", chokes=None, hearing="h"))


def test_control_9_makes_every_stored_control_and_gap_run_stale(monkeypatch):
    recipe = fmt.recipe("p" * 12, "a" * 8)
    geometry = rc.geometry_inputs("Ascent")
    now = _fingerprint(recipe, geometry)
    monkeypatch.setattr(cf, "CONTROL_REVISION", 8)
    before = _fingerprint(recipe, geometry)
    assert cf.CONTROL_REVISION == 8 and now != before
    assert (replay_gaps.gap_fingerprint(now, "Ascent", chokes=None, hearing="h")
            != replay_gaps.gap_fingerprint(before, "Ascent", chokes=None, hearing="h"))


def test_condense_15_changes_the_recipe_and_so_the_control_fingerprint(monkeypatch):
    geometry = rc.geometry_inputs("Abyss")
    now = fmt.recipe("p" * 12, "a" * 8)
    monkeypatch.setattr(fmt, "CONDENSE_REVISION", 14)
    before = fmt.recipe("p" * 12, "a" * 8)
    assert rc.condense_revision(now) == 15 and rc.condense_revision(before) == 14
    assert _fingerprint(now, geometry) != _fingerprint(before, geometry)
