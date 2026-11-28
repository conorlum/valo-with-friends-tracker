"""Branch-review fixes R2.1: names-only ability=, health rows that always clear, KAY/O with no agent, dead code."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text

from app.models.replay import Replay
from app.search import index, match
from app.search.filters import FilterError, parse
from app.search.match import matches
from app.services import replay_search as lib
from app.services import replay_search_index as store
from app.services import replay_search_sweep as sweep
from tests.search.test_search_match import ctx, row
from tests.search.test_search_store import factory, indexed, pending_ids, replay_id, state_of, stored  # noqa: F401

# ------------------------------------------------------------------------------------- F2: ability= is a name only


@pytest.mark.parametrize("value", ["c", "C", "q", "E", "x", "X", "4", "1", "é", "_", "%"])
def test_a_one_character_ability_is_a_filter_error(value):
    with pytest.raises(FilterError, match=r"ability: give the ability's name, e\.g\. ability=owl"):
        parse([f"ability={value}"])


@pytest.mark.parametrize("value", ["ow", "owl", "Owl Drone", "smoke", "NULL/cmd", "4x"])
def test_a_name_of_two_or_more_characters_parses(value):
    assert parse([f"ability={value}"]).ability == value


def test_the_matcher_never_reads_the_key_column():
    cast = row(kind="cast", ability="Sova/Owl Drone", ability_key="C")
    assert matches(parse(["ability=owl"]), ctx(), cast)
    assert not matches(parse(["ability=ow"]), ctx(), row(kind="cast", ability="Sova/Recon", ability_key="OW"))
    assert not hasattr(match, "_ability_key_ok")


def test_the_prefilter_has_no_letter_branch():
    import inspect

    source = inspect.getsource(lib._conditions)
    assert "ability_key" not in source and "len(wanted_ability)" not in source


def test_the_stored_ability_key_column_stays():
    assert "ability_key" in match.ROW_KEYS
    assert index._key_of("Q_Molotov") == "Q"


def test_the_docs_do_not_promise_a_letter():
    import inspect

    for module in (match, lib, index):
        assert "archetype letter" not in inspect.getsource(module).replace("The archetype letter of an ability name", "")
    assert "letter" not in (match._ability_ok.__doc__ or "")


# ------------------------------------------------------------------------------------------- F3: health rows clear


def test_the_race_guard_returns_a_non_failure_status(factory, monkeypatch):
    rid = indexed(factory)
    before = state_of(factory, rid)

    def boom(_inputs):
        raise ValueError("kaboom")

    monkeypatch.setattr(index, "build_rows", boom)
    result = store.rebuild(factory, rid)
    assert result == "skipped: built elsewhere"
    after = state_of(factory, rid)
    assert after.error is None and after.key == before.key and after.built_at == before.built_at
    assert pending_ids(factory) == []


def test_a_skipped_rebuild_resolves_the_row_and_does_not_cool_the_replay(monkeypatch):
    record, resolve = Mock(), Mock()
    monkeypatch.setattr(sweep.site_health, "record", record)
    monkeypatch.setattr(sweep.site_health, "resolve", resolve)
    monkeypatch.setattr(sweep.deploy_drain, "paused_now", lambda: False)
    db = Mock()
    failed_until = {}
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda s: False)
    counts = sweep.run_pass(Mock(return_value=db), stop, failed_until, 100, select=Mock(return_value=[5]),
                            rebuild=Mock(return_value="skipped: built elsewhere"), drop=Mock(return_value=[]),
                            uuids=lambda d, ids: {5: "uuid-5"})
    assert counts == {"skipped": 1} and failed_until == {}
    record.assert_not_called()
    resolve.assert_called_once_with("search_index_failed", "uuid-5", replay_id=5)


def test_drop_unlinked_returns_the_dropped_replay_ids_and_their_count(factory):
    rid = indexed(factory)
    with factory() as db:
        db.execute(text("UPDATE replays SET link_status='unlinked'"))
        db.commit()
    with factory() as db:
        dropped = store.drop_unlinked(db)
        db.commit()
    assert list(dropped) == [rid] and len(dropped) == 1 and dropped
    with factory() as db:
        assert not store.drop_unlinked(db) and len(store.drop_unlinked(db)) == 0


def _pass(monkeypatch, dropped, uuid_of):
    resolve, record = Mock(), Mock()
    monkeypatch.setattr(sweep.site_health, "resolve", resolve)
    monkeypatch.setattr(sweep.site_health, "record", record)
    monkeypatch.setattr(sweep.deploy_drain, "paused_now", lambda: False)
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda s: False)
    counts = sweep.run_pass(Mock(return_value=Mock()), stop, {}, 100, select=Mock(return_value=[]),
                            rebuild=Mock(), drop=Mock(return_value=dropped), uuids=uuid_of)
    return counts, resolve, record


def test_the_sweep_resolves_the_rows_of_replays_drop_unlinked_removed(monkeypatch):
    seen = []

    def uuids(db, ids):
        seen.append(sorted(ids))
        return {i: f"uuid-{i}" for i in ids}

    _, resolve, record = _pass(monkeypatch, [3, 4], uuids)
    assert {c.args for c in resolve.call_args_list} == {("search_index_failed", "uuid-3"),
                                                        ("search_index_failed", "uuid-4")}
    assert {c.kwargs["replay_id"] for c in resolve.call_args_list} == {3, 4}
    record.assert_not_called()
    assert [3, 4] in seen


def test_a_dropped_replay_with_no_uuid_or_a_failed_lookup_never_fails_the_pass(monkeypatch):
    _, resolve, _ = _pass(monkeypatch, [3], lambda db, ids: {})
    resolve.assert_not_called()

    def broken(db, ids):
        raise RuntimeError("no db")

    counts, resolve, _ = _pass(monkeypatch, [3], broken)
    resolve.assert_not_called() and counts == {}


def test_nothing_dropped_resolves_nothing(monkeypatch):
    _, resolve, _ = _pass(monkeypatch, [], lambda db, ids: {})
    resolve.assert_not_called()


def test_a_legacy_drop_that_returns_a_number_still_works(monkeypatch):
    _, resolve, _ = _pass(monkeypatch, 2, lambda db, ids: {})
    resolve.assert_not_called()


def test_end_to_end_the_dropped_replay_uuid_is_looked_up(factory):
    rid = indexed(factory)
    with factory() as db:
        uuid = str(db.execute(select(Replay.match_uuid).where(Replay.id == rid)).scalar_one())
        db.execute(text("UPDATE replays SET link_status='unlinked'"))
        db.commit()
    resolved = []
    original = sweep.site_health.resolve
    sweep.site_health.resolve = lambda kind, key, **kw: resolved.append((kind, key, kw))
    try:
        sweep.deploy_drain.paused_now, saved = (lambda: False), sweep.deploy_drain.paused_now
        try:
            stop = SimpleNamespace(is_set=lambda: False, wait=lambda s: False)
            sweep.run_pass(factory, stop, {}, 0)
        finally:
            sweep.deploy_drain.paused_now = saved
    finally:
        sweep.site_health.resolve = original
    assert ("search_index_failed", uuid, {"replay_id": rid}) in resolved


# ------------------------------------------------------------------------------------------ F4: KAY/O with no agent


@pytest.mark.parametrize("agent", [None, "Jett", "KAY/O", "kay/o"])
def test_kayo_ability_tail_never_depends_on_the_rows_agent(agent):
    cast = row(kind="cast", slot=1, agent=agent, ability="KAY/O/NULL/cmd", ult=True, alive1=None, alive2=None,
               post_plant=False, plant_t=None)
    assert match._ability_tail(cast) == "NULL/cmd"
    assert matches(parse(["ability=null/cmd"]), ctx(), cast)
    assert not matches(parse(["ability=o/"]), ctx(), cast)
    assert not matches(parse(["ability=kay"]), ctx(), cast)
    assert match.summary(cast, lambda s: None, None).startswith(
        f"{agent or 'someone'} used their ultimate, NULL/cmd")


def test_kayo_prefix_is_case_insensitive_and_other_agents_split_at_the_first_slash():
    assert match._ability_tail({"ability": "kay/o/flash/drive", "agent": None}) == "flash/drive"
    assert match._ability_tail({"ability": "Sova/Owl Drone", "agent": None}) == "Owl Drone"
    assert match._ability_tail({"ability": "Sova/Owl Drone", "agent": "Jett"}) == "Owl Drone"
    assert match._ability_tail({"ability": "Jett/Smoke", "agent": "Jett"}) == "Smoke"
    assert match._ability_tail({"ability": "NoSlash", "agent": None}) == "NoSlash"
    assert match._ability_tail({"ability": None, "agent": None}) is None
    assert match._ability_tail({"ability": "", "agent": "KAY/O"}) is None


# ------------------------------------------------------------------ F1: a round with no known attacker has no side


@pytest.mark.parametrize("side", ["attack", "defense"])
def test_no_known_attacker_means_no_team_for_side(side):
    r = row(attack_team=None, kind="kill", team="team-1")
    assert match.our_team(parse([f"side={side}"]), ctx(), r) is None
    assert not matches(parse([f"side={side}"]), ctx(), r)
    assert not matches(parse([f"side={side}", "won=1"]), ctx(), r)


# ---------------------------------------------------------------------------------------------- F5: dead code


def test_key_column_length_is_gone():
    assert not hasattr(index, "KEY_COLUMN_LENGTH")


def test_the_missing_space_is_fixed():
    import inspect

    source = inspect.getsource(store)
    assert "parts =key_parts" not in source and "parts = key_parts" in source
