"""Remote Util preparation on synthetic SQLite data; no server or real accounts."""

import base64
import hashlib
import io
import json
import logging
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.critutil import build_digest, corpus
from app.db import Base
from app.models.friendship import Friendship
from app.models.kill_event import KillEvent
from app.models.match import Match, MatchPlayer, MatchSource
from app.models.player import Player
from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundUtil, UtilModel
from app.models.round import Round, RoundPlayerStat
from app.services import replay_control_remote, replay_util, util_model, util_store, util_values
from app.services import util_build_remote as remote
from app.services.replay_control_remote import ControlClient, Conflict, Unreachable, WorkerGone


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()


class Client:
    def __init__(self, missing_rows=None, missing_obs=None):
        self.missing_rows = missing_rows
        self.missing_obs = missing_obs
        self.calls = []
        self.failure = None
        self.conflict = None

    def util_have(self, rows, obs=None, *, prune=False, ref=None):
        self.calls.append(("have", dict(rows=list(rows), obs=None if obs is None else list(obs), prune=prune, ref=ref)))
        if self.failure == "have":
            raise Unreachable("offline")
        return {"missing_rows": list(rows) if self.missing_rows is None else list(self.missing_rows),
                "missing_obs": list(obs or []) if self.missing_obs is None else list(self.missing_obs)}

    def util_rounds(self, rows, *, ref=None):
        self.calls.append(("rounds", dict(rows=list(rows), ref=ref)))
        if self.failure == "rounds":
            raise Unreachable("refused")
        return {}

    def util_obs(self, obs, *, ref=None):
        self.calls.append(("obs", dict(obs=list(obs), ref=ref)))
        if self.failure == "obs":
            raise Unreachable("refused")
        return {}

    def util_open(self, **body):
        self.calls.append(("open", body))
        return self.conflict or {"id": "synthetic-job", "key": body["key"], "status": "queued"}

    def sent(self, kind):
        return [body for name, body in self.calls if name == kind]


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://")
    tables = (Player, Match, MatchPlayer, Round, RoundPlayerStat, KillEvent, Friendship,
              Replay, ReplayPlayer, ReplayRound, ReplayRoundUtil, UtilModel)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in tables])
    monkeypatch.setattr(remote, "settings", SimpleNamespace(util_build_remote=True, demo_mode=False))
    monkeypatch.setattr(replay_control_remote.settings, "replay_control_remote", True)
    monkeypatch.setattr(replay_control_remote.settings, "replay_worker_url", "https://synthetic.invalid")
    monkeypatch.setattr(replay_control_remote.settings, "demo_mode", False)
    monkeypatch.setattr(replay_util.Freshness, "preload", lambda *a: None)
    monkeypatch.setattr(replay_util.Freshness, "current", lambda *a: True)
    failures = []
    monkeypatch.setattr(remote.util_build_health, "record", lambda *a: failures.append(a))
    monkeypatch.setattr(remote.util_build_health, "set_in_flight", lambda value: None)
    with Session(engine) as db:
        add_replay(db, 1)
        db.add(UtilModel(name="win", key="synthetic-win", version=util_model.VERSION,
                         data={"key": "synthetic-win", "excluded": [1], "cells": {}}))
        db.commit()
        yield SimpleNamespace(db=db, state=remote.UtilState(), client=Client(), failures=failures,
                              health={"util": {"digest": build_digest.digest(remote.WEBAPP)}})
    engine.dispose()


def add_replay(db, number):
    db.add(Match(id=number, external_id=f"synthetic-{number}", source=MatchSource.SCRAPED))
    db.add(Replay(id=number, match_id=number, match_uuid=f"00000000-0000-0000-0000-{number:012d}",
                  map_name="Haven", round_count=40, format_version=1, recipe="p1.c22.f1.a1",
                  game_branch="synthetic", source_sha256="0" * 64, source="local",
                  link_status="linked", link_inputs={}, clock_offset=0.0))


def put_rows(env, count=1, age=130, status="ok", start=1, size=1):
    for n in range(start, start + count):
        env.db.merge(ReplayRoundUtil(replay_id=1, round_number=n, status=status, fingerprint="f" * 16,
                                    gaps_revision=5, data={"casts": [], "padding": "x" * size},
                                    computed_at=datetime.fromtimestamp(NOW - age, timezone.utc)))
    env.db.commit()


def cycle(env, at=NOW):
    return remote.push(env.db, env.client, env.state, env.health, at)


def store_current(env):
    rows = replay_util.current_rows(env.db, with_data=False)
    key = replay_util.corpus_key(env.db, rows, {"key": "synthetic-win"})
    env.db.merge(UtilModel(name="values", key=key, version=util_values.VERSION, data={"unused": True}))
    env.db.commit()


@pytest.mark.parametrize("age,asked,expected", [(60, False, False), (130, False, True), (60, True, True)])
def test_quiet_or_explicit_request(env, age, asked, expected):
    put_rows(env, age=age)
    if asked:
        remote.request_now(env.state)
    cycle(env)
    assert bool(env.client.sent("open")) is expected
    if expected:
        assert env.state.phase == "open" and env.state.build_id == "synthetic-job"
        cycle(env)
        assert len(env.client.sent("open")) == 1


def test_current_build_and_cached_signature_never_load_values_data(env, monkeypatch):
    put_rows(env)
    store_current(env)
    queries = []
    def guard(conn, cursor, statement, parameters, context, many):
        queries.append(statement)
        if "util_model.data" in statement and "values" in parameters:
            pytest.fail("loaded the values blob")
    event.listen(env.db.get_bind(), "before_cursor_execute", guard)
    original = replay_util.current_rows
    calls = []
    monkeypatch.setattr(replay_util, "current_rows", lambda *a, **kw: calls.append(kw) or original(*a, **kw))
    cycle(env)
    cycle(env, NOW + 20)
    assert calls == [{"with_data": False}]
    assert env.client.calls == []
    cycle(env, NOW + 601)
    assert len(calls) == 2


def test_request_now_forces_full_check_after_unchanged_signature(env, monkeypatch):
    put_rows(env)
    store_current(env)
    cycle(env)
    original = replay_util.corpus_key
    calls = []
    monkeypatch.setattr(replay_util, "corpus_key", lambda *a: calls.append(1) or original(*a))
    remote.request_now(env.state)
    cycle(env, NOW + 1)
    assert calls == [1]
    assert env.client.calls == []


def test_friendship_changes_signature_and_full_key_without_request(env, monkeypatch):
    put_rows(env)
    store_current(env)
    cycle(env)
    env.db.add(Friendship(owner_player_id=101, friend_player_id=102))
    env.db.commit()
    cycle(env, NOW + 1)
    assert len(env.client.sent("open")) == 1


def test_failed_recent_row_keeps_old_ok_rows_from_dispatch(env):
    put_rows(env)
    put_rows(env, age=60, status="failed", start=2)
    cycle(env)
    assert not env.client.calls


def test_digest_mismatch_waits_and_records_once_after_thirty_minutes(env):
    put_rows(env)
    env.health["util"]["digest"] = "different"
    for at in (NOW, NOW + 1860, NOW + 1880):
        cycle(env, at)
    assert not env.client.calls
    assert [f[0] for f in env.failures] == ["digest"]
    assert env.state.tries == {}
    env.health["util"]["digest"] = build_digest.digest(remote.WEBAPP)
    cycle(env, NOW + 1900)
    assert len(env.client.sent("open")) == 1
    assert env.state.digest_mismatch_since is None


def test_only_missing_three_of_ten_blobs_are_selected(env):
    put_rows(env, count=10)
    rows = replay_util.current_rows(env.db, with_data=False)
    missing = [corpus.row_key(*row) for row in rows[2:5]]
    env.client.missing_rows = missing
    selected = []
    def capture(conn, cursor, statement, parameters, context, many):
        if "replay_round_util.data" in statement:
            selected.append((statement, parameters))
    event.listen(env.db.get_bind(), "before_cursor_execute", capture)
    cycle(env)
    sent = [row for body in env.client.sent("rounds") for row in body["rows"]]
    assert [row["key"] for row in sent] == missing
    assert len(selected) == 1 and "WHERE" in selected[0][0]
    assert set(selected[0][1]) <= {1, 3, 4, 5, "ok"}
    assert all(json.loads(base64.b64decode(row["data"]))["casts"] == [] for row in sent)


def test_thirty_batches_take_four_cycles(env, monkeypatch):
    monkeypatch.setattr(remote, "BATCH_BYTES", 1500)
    put_rows(env, count=30, size=700)
    for index, expected in enumerate((8, 16, 24, 30)):
        cycle(env, NOW + index)
        assert len(env.client.sent("rounds")) == expected
        assert bool(env.client.sent("open")) is (index == 3)
    assert len(env.client.sent("have")) == 1
    assert env.client.sent("have")[0]["prune"] is True
    assert all(len(json.dumps(body).encode()) <= 1500 for body in env.client.sent("rounds"))


def test_blob_queries_are_chunked_to_two_hundred_keys(env):
    put_rows(env, count=205)
    queries = []
    def capture(conn, cursor, statement, parameters, context, many):
        if "replay_round_util.data" in statement:
            queries.append(parameters)
    event.listen(env.db.get_bind(), "before_cursor_execute", capture)
    cycle(env)
    assert len(queries) == 2
    assert all(len(parameters) <= 401 for parameters in queries)  # 200 pairs and status
    assert len(env.client.sent("open")) == 1


def test_full_freshness_check_after_ten_minutes(env, monkeypatch):
    put_rows(env)
    store_current(env)
    cycle(env)
    monkeypatch.setattr(replay_util.Freshness, "current", lambda *a: False)
    cycle(env, NOW + 20)
    assert not env.client.calls
    cycle(env, NOW + 601)
    assert env.client.sent("open")[0]["rows"] == []


def non_replay_matches(env, count):
    for mid in range(2, count + 2):
        env.db.add(Match(id=mid, external_id=f"synthetic-{mid}", source=MatchSource.SCRAPED))
        env.db.add(Round(id=mid, match_id=mid, round_number=1))
        env.db.add(KillEvent(id=mid, round_id=mid, weapon="Vandal", event_time_seconds=8.0))
    env.db.delete(env.db.get(UtilModel, "win"))
    env.db.commit()


def test_refit_only_loads_missing_observations_across_cycles(env, monkeypatch):
    put_rows(env)
    non_replay_matches(env, remote.OBS_MATCHES_PER_CYCLE + 2)
    all_missing = list(remote._observation_keys(env.db, {1}))
    env.client.missing_obs = all_missing[:-1]
    loads = []
    def observations(db, excluded, *, by_match, match_ids):
        assert by_match and excluded == {1}
        loads.append(set(match_ids))
        return {mid: [(5, 4, False, 8.0, None, True)] for mid in match_ids}
    monkeypatch.setattr(util_model, "load_observations", observations)
    cycle(env)
    assert sum(map(len, loads)) == remote.OBS_MATCHES_PER_CYCLE
    assert not env.client.sent("open")
    cycle(env, NOW + 1)
    assert sum(map(len, loads)) == remote.OBS_MATCHES_PER_CYCLE + 1
    assert all((remote.OBS_MATCHES_PER_CYCLE + 3) not in ids for ids in loads)
    sent = [item for b in env.client.sent("obs") for item in b["obs"]]
    assert [r["key"] for r in sent] == all_missing[:-1]
    assert len(env.client.sent("open")) == 1


@pytest.mark.parametrize("failure", ["have", "rounds", "obs"])
def test_push_error_records_one_failure(env, monkeypatch, failure):
    put_rows(env)
    if failure == "obs":
        non_replay_matches(env, 1)
        monkeypatch.setattr(util_model, "load_observations", lambda *a, **kw: {2: []})
    env.client.failure = failure
    cycle(env)
    assert [f[0] for f in env.failures] == ["push"]
    assert env.state.phase == "idle"
    assert list(env.state.tries.values()) == [1]


def test_retry_backoff_and_three_try_cap(env):
    put_rows(env)
    env.client.failure = "have"
    for offset in (0, 1, 301, 602, 903):
        cycle(env, NOW + offset)
    assert len(env.client.sent("have")) == 3
    assert list(env.state.tries.values()) == [3]


def test_three_changed_preparations_do_not_spend_build_tries(env, monkeypatch):
    put_rows(env)
    original = remote._round_entries

    def changed(*args):
        raise remote._Changed("synthetic input moved")

    monkeypatch.setattr(remote, "_round_entries", changed)
    keys = []
    for offset in range(3):
        cycle(env, NOW + offset)
        keys.append(env.state.key)
        assert env.state.phase == "idle"
    assert len(set(keys)) == 1
    monkeypatch.setattr(remote, "_round_entries", original)
    cycle(env, NOW + 3)
    assert len(env.client.sent("open")) == 1
    assert env.state.tries == {keys[0]: 1}
    assert not env.state.gave_up and not env.failures


def test_values_only_have_leaves_observation_cache_alone(env):
    put_rows(env)
    cycle(env)
    assert env.client.sent("have")[0]["obs"] is None
    assert env.client.sent("open")[0]["obs"] == []


@pytest.mark.parametrize("obs", [None, "omitted"])
def test_client_have_preserves_unspecified_observations(monkeypatch, obs):
    client = ControlClient("synthetic.invalid")
    bodies = []
    monkeypatch.setattr(client, "_post", lambda route, body: bodies.append(body) or {})
    if obs == "omitted":
        client.util_have([], prune=True)
    else:
        client.util_have([], None, prune=True)
    assert bodies[0].get("obs") is None


def test_worker_store_have_none_keeps_observation_files(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(remote.WEBAPP.parent))
    from replay_worker.server import Settings, UtilStore

    store = UtilStore(Settings(temp_root=tmp_path, workflow_slug="synthetic", render_api_key="synthetic"))
    store.put_round("old", b"{}")
    store.put_obs("cached", b"[]")
    assert store.have([], None, prune=True) == {"missing_rows": [], "missing_obs": []}
    assert (store.root / "obs" / "cached.json").read_bytes() == b"[]"
    assert not (store.root / "rounds" / "old.json").exists()


@pytest.mark.parametrize("model,identity,field,value", [
    (Round, 101, "outcome", "Team B Elimination Win"),
    (Round, 102, "plant_time", 21.0),
    (Round, 101, "round_number", 3),
    (Round, 101, "planted", True),
    (Round, 102, "exploded", True),
    (Round, 102, "defused", False),
    (Round, 102, "defuse_time", 37.0),
    (MatchPlayer, 11, "team", "TEAM_2"),
    (KillEvent, 101, "event_time_seconds", 19.0),
    (KillEvent, 101, "killer_match_player_id", 12),
    (KillEvent, 101, "death_match_player_id", 11),
    (KillEvent, 101, "round_id", 102),
])
def test_observation_key_covers_estimator_fields(model, identity, field, value):
    from tests.critutil.test_util_model_refit import synthetic_db

    db = synthetic_db()
    engine = db.get_bind()
    try:
        before = {mid: key for key, mid in remote._observation_keys(db, {6}).items()}
        setattr(db.get(model, identity), field, value)
        db.commit()
        after = {mid: key for key, mid in remote._observation_keys(db, {6}).items()}
        assert before.keys() == after.keys()
        assert {mid for mid in before if before[mid] != after[mid]} == {1}
    finally:
        db.close()
        engine.dispose()


def test_shard_changed_between_preparation_and_load_abandons(env, monkeypatch):
    put_rows(env)
    non_replay_matches(env, 1)
    original = env.client.util_have
    loads = []

    def changed(*args, **kwargs):
        env.db.get(Round, 2).plant_time = 25.0
        env.db.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(env.client, "util_have", changed)
    monkeypatch.setattr(util_model, "load_observations", lambda *a, **kw: loads.append(1) or {})
    cycle(env)
    assert not env.client.sent("open") and not env.client.sent("obs")
    assert not loads and not env.failures
    assert env.state.phase == "idle"
    assert all(n == 0 for n in env.state.tries.values())


def test_shard_key_and_observations_share_a_separate_snapshot(env, monkeypatch):
    put_rows(env)
    non_replay_matches(env, 1)
    original_keys = remote._observation_keys
    reads = []

    def keys(db, excluded, match_ids=None):
        if match_ids is not None:
            reads.append(("key", db))
        return original_keys(db, excluded, match_ids)

    def observations(db, *args, **kwargs):
        assert db is not env.db
        assert db.in_transaction()
        assert reads[-1] == ("key", db)
        reads.append(("obs", db))
        return {}

    def send(entries, **kwargs):
        assert not reads[-1][1].in_transaction()
        assert not env.db.in_transaction()
        return {}

    monkeypatch.setattr(remote, "_observation_keys", keys)
    monkeypatch.setattr(util_model, "load_observations", observations)
    monkeypatch.setattr(env.client, "util_obs", send)
    cycle(env)
    assert len(env.client.sent("open")) == 1 and not env.failures


def test_postgres_observation_keys_use_three_ordered_sql_aggregates():
    from sqlalchemy.dialects.postgresql import dialect

    queries = []
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=dialect()),
                         execute=lambda query: queries.append(query) or [(1, "players", "rounds", "kills")])
    assert remote._observation_keys(db, {6}) == {remote.obs_key(1, "players", "rounds", "kills"): 1}
    assert len(queries) == 1
    sql = str(queries[0].compile(dialect=dialect(), compile_kwargs={"literal_binds": True})).lower()
    assert sql.count("md5(string_agg(") == 3
    assert sql.count("group by") == 3
    assert sql.count("order by") == 4  # each aggregate, then the matches
    for field in ("match_players.team", "rounds.outcome", "rounds.plant_time", "rounds.defuse_time",
                  "kill_events.event_time_seconds", "kill_events.killer_match_player_id",
                  "kill_events.death_match_player_id"):
        assert field in sql


def test_concurrent_edit_after_key_check_abandons_shard(tmp_path, monkeypatch):
    from tests.critutil.test_util_model_refit import synthetic_db

    db = synthetic_db("sqlite:///" + (tmp_path / "snapshot.sqlite").as_posix())
    engine = db.get_bind()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        state = remote.UtilState(excluded=frozenset({6}))
        state.obs_keys = remote._observation_keys(db, state.excluded, {1})
        expected = util_model.load_observations(db, state.excluded, by_match=True, match_ids={1})
        db.rollback()
        original = util_model.load_observations

        def concurrent_edit(snapshot, *args, **kwargs):
            # A separate connection commits after the key read, before the shard read.
            with Session(engine) as writer:
                writer.get(KillEvent, 101).event_time_seconds = 19.0
                writer.commit()
            actual = original(snapshot, *args, **kwargs)
            assert actual == expected
            return actual

        monkeypatch.setattr(util_model, "load_observations", concurrent_edit)
        with pytest.raises(remote._Changed, match="observation match changed"):
            remote._observation_entries(db, state, list(state.obs_keys))
        assert remote._observation_keys(db, state.excluded, {1}) != state.obs_keys
    finally:
        db.close()
        engine.dispose()


def test_new_replay_without_util_changes_dispatch_identity(env):
    put_rows(env)
    cycle(env)
    first = env.state.key
    store_current(env)
    env.state.phase = "idle"  # collection belongs to W10
    add_replay(env.db, 2)
    env.db.commit()
    cycle(env, NOW + 1)
    assert env.state.key != first
    assert env.client.sent("open")[-1]["refit"] is True
    assert env.client.sent("open")[-1]["ref"] != env.client.sent("open")[0]["ref"]


@pytest.mark.parametrize("change", ["row", "friends", "offset"])
def test_changed_preparation_abandoned_before_next_push(env, monkeypatch, change):
    put_rows(env, count=10, size=700)
    monkeypatch.setattr(remote, "BATCH_BYTES", 1500)
    cycle(env)
    old_key, old_ref = env.state.key, env.state.ref
    if change == "row":
        row = env.db.get(ReplayRoundUtil, (1, 10))
        row.data = {"casts": [], "new": True}
        row.computed_at += timedelta(microseconds=1)
    elif change == "friends":
        env.db.add(Friendship(owner_player_id=101, friend_player_id=102))
    else:
        env.db.get(Replay, 1).clock_offset = 1.0
    env.db.commit()
    cycle(env, NOW + 1)
    assert env.state.phase == "idle"
    assert len(env.client.sent("rounds")) == 8
    cycle(env, NOW + 2)
    assert env.state.key != old_key and env.state.ref != old_ref
    for body in env.client.sent("rounds"):
        if body["ref"] == old_ref:
            assert not any(json.loads(base64.b64decode(r["data"])).get("new") for r in body["rows"])


@pytest.mark.parametrize("change", ["fingerprint", "computed_at"])
def test_missing_row_must_match_frozen_tuple_even_when_max_timestamp_is_unchanged(env, monkeypatch, change):
    put_rows(env, count=9, size=700)
    put_rows(env, start=10, age=160, size=700)
    monkeypatch.setattr(remote, "BATCH_BYTES", 1500)
    cycle(env)
    old_ref = env.state.ref
    row = env.db.get(ReplayRoundUtil, (1, 10))
    if change == "fingerprint":
        row.fingerprint = "e" * 16
    else:
        row.computed_at += timedelta(seconds=1)
    row.data = {"new": True}
    env.db.commit()
    cycle(env, NOW + 1)
    assert env.state.phase == "idle"
    assert len(env.client.sent("rounds")) == 8
    assert all(body["ref"] == old_ref for body in env.client.sent("rounds"))


def test_observation_keys_are_rechecked_before_loading_new_bytes(env, monkeypatch):
    put_rows(env)
    non_replay_matches(env, 2)
    monkeypatch.setattr(remote, "OBS_MATCHES_PER_CYCLE", 1)
    loads = []
    def observations(db, excluded, *, by_match, match_ids):
        loads.append(set(match_ids))
        return {mid: [] for mid in match_ids}
    monkeypatch.setattr(util_model, "load_observations", observations)
    cycle(env)
    old_key = env.state.key
    env.db.add(KillEvent(id=10, round_id=3, weapon="Vandal", event_time_seconds=12.0))
    env.db.commit()
    cycle(env, NOW + 1)
    assert env.state.phase == "idle"
    assert loads == [{2}]
    cycle(env, NOW + 2)
    assert env.state.key != old_key


def test_decision_and_every_push_log_and_request_carries_ref(env, caplog):
    put_rows(env)
    with caplog.at_level(logging.INFO, logger=remote.__name__):
        cycle(env)
    assert len(env.state.ref) == 32
    assert caplog.records
    for record in caplog.records:
        assert env.state.ref in record.getMessage() and env.state.key in record.getMessage()
    assert all(body["ref"] == env.state.ref for _, body in env.client.calls)
    frozen = corpus.decode_context(base64.b64decode(env.client.sent("open")[0]["context"]))
    assert [corpus.row_key(*r) for r in frozen["rows"]] == list(env.state.row_keys)


def test_open_missing_lists_return_to_push_without_second_try(env):
    put_rows(env)
    env.client.missing_rows = []
    key = corpus.row_key(*replay_util.current_rows(env.db, with_data=False)[0])
    env.client.conflict = {"missing_rows": [key], "missing_obs": []}
    cycle(env)
    assert env.state.phase == "pushing"
    env.client.conflict = None
    cycle(env, NOW + 1)
    assert env.state.phase == "open" and list(env.state.tries.values()) == [1]


def test_demo_and_disabled_do_nothing(env, monkeypatch):
    put_rows(env)
    for flags in ((True, True), (False, False)):
        monkeypatch.setattr(remote, "settings", SimpleNamespace(util_build_remote=flags[0], demo_mode=flags[1]))
        assert not remote.enabled()
        remote.request_now(env.state)
        cycle(env)
    assert not env.client.calls


def test_obs_version_is_lf_normalized_site_source_hash():
    digest = hashlib.sha256()
    for path in ("app/services/util_model.py", "app/services/state_replay.py", "app/scoring/plant_window.py"):
        digest.update((remote.WEBAPP / path).read_bytes().replace(b"\r\n", b"\n"))
    assert remote.OBS_VERSION == digest.hexdigest()[:8]
    key = remote.obs_key(7, "players", "rounds", "kills")
    assert key == f"7-{hashlib.sha256(b'players:rounds:kills').hexdigest()}-{remote.OBS_VERSION}"
    assert len(key) <= 120


def test_client_routes_and_open_conflict(monkeypatch):
    client = ControlClient("synthetic.invalid")
    calls = []
    def call(request):
        body = json.loads(request.data) if request.data else None
        calls.append((request.full_url, body))
        return {"ok": True}
    monkeypatch.setattr(client, "_call", call)
    client.util_have(["r"], ["o"], prune=True, ref="ref")
    client.util_rounds([{"key": "r", "data": "e30="}], ref="ref")
    client.util_obs([{"key": "o", "data": "W10="}], ref="ref")
    client.util_open(key="key", digest="digest", rows=["r"], obs=["o"], refit=True,
                     context="e30=", ref="ref")
    client.util_build("job")
    assert [url.removeprefix(client.base) for url, _ in calls] == [
        "/util/have", "/util/rounds", "/util/obs", "/util/build", "/util/build/job"]
    assert calls[0][1] == {"rows": ["r"], "obs": ["o"], "prune": True, "ref": "ref"}
    missing = {"missing_rows": ["r"], "missing_obs": ["o"]}
    def conflict(*a):
        raise Conflict(missing)
    monkeypatch.setattr(client, "_post", conflict)
    assert client.util_open(key="key", digest="digest", rows=[], obs=[], refit=False,
                            context="e30=", ref="ref") == missing


def test_result_streams_to_file_with_five_minute_timeout(tmp_path, monkeypatch):
    payload = b"synthetic result" * 100000
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    def urlopen(request, *, timeout):
        assert request.full_url.endswith("/util/build/job/result")
        assert timeout == 300
        return Stream(payload)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    dest = tmp_path / "result"
    ControlClient("synthetic.invalid").util_result("job", dest)
    assert dest.read_bytes() == payload


def test_json_methods_use_normal_timeout(monkeypatch):
    calls = []
    def urlopen(request, *, timeout):
        calls.append(timeout)
        return io.BytesIO(b"{}")
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = ControlClient("synthetic.invalid")
    client.util_have([], ref="ref")
    client.util_rounds([], ref="ref")
    client.util_obs([], ref="ref")
    client.util_open(key="k", digest="d", rows=[], obs=[], refit=False, context="e30=", ref="ref")
    client.util_build("job")
    assert calls == [30] * 5


@pytest.mark.parametrize("code,error", [(404, WorkerGone), (500, Unreachable)])
def test_result_failure_is_client_error(tmp_path, monkeypatch, code, error):
    def fail(*a, **kw):
        raise urllib.error.HTTPError("synthetic", code, "failure", {}, io.BytesIO())
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(error):
        ControlClient("synthetic.invalid").util_result("job", tmp_path / "result")


@pytest.fixture
def stepping(env, monkeypatch):
    """Worker-shaped answers and fresh, tracked sessions; all calls are in-process."""
    sessions, flags, clears = [], [], []
    maker = sessionmaker(bind=env.db.get_bind())

    def factory():
        db = maker()
        sessions.append(db)
        return db

    env.factory, env.sessions = factory, sessions
    env.flags, env.clears = flags, clears
    env.status = "queued"
    env.error_kind = "infra"
    env.output = None
    env.overrides = {}
    env.paths = []
    env.polls = 0
    env.worker_jobs = {}

    def no_transaction():
        assert not any(db.in_transaction() for db in sessions)

    def health():
        no_transaction()
        return env.health

    def poll(build_id):
        no_transaction()
        env.polls += 1
        if isinstance(env.status, Exception):
            raise env.status
        ctx = corpus.decode_context(env.state.context)
        win = {"key": "synthetic-refitted", "excluded": sorted(env.state.excluded), "cells": {}}
        values = {"casts": [], "weights": {"cover": 0.125}, "nested": [True, None, "synthetic"]}
        output = corpus.encode_output({"win": win if env.state.refit else None, "values": values})
        env.output = output if env.output is None else env.output
        return dict({"id": build_id, "key": env.state.key, "status": env.status,
                     "values_key": corpus.values_key(ctx["key_parts"], win["key"] if env.state.refit else env.state.win_key),
                     "digest": env.state.digest, "version": util_values.VERSION, "refit": env.state.refit,
                     "bytes": len(env.output), "sha256": hashlib.sha256(env.output).hexdigest(),
                     "error": "synthetic failure", "error_kind": env.error_kind}, **env.overrides)

    def result(build_id, path):
        no_transaction()
        env.paths.append(path)
        path.write_bytes(env.output)
        return path

    monkeypatch.setattr(env.client, "health", health, raising=False)
    monkeypatch.setattr(env.client, "util_build", poll, raising=False)
    monkeypatch.setattr(env.client, "util_result", result, raising=False)
    for name in ("util_have", "util_rounds", "util_obs", "util_open"):
        original = getattr(env.client, name)
        def checked(*a, _method=original, _name=name, **kw):
            no_transaction()
            answer = _method(*a, **kw)
            if _name == "util_open" and answer.get("id"):
                # Model worker dedupe: equal dispatch keys return the same build.
                answer["id"] = env.worker_jobs.setdefault(kw["key"], f"synthetic-job-{len(env.worker_jobs) + 1}")
            return answer
        monkeypatch.setattr(env.client, name, checked)
    monkeypatch.setattr(remote.util_build_health, "set_in_flight", flags.append)
    monkeypatch.setattr(remote.util_build_health, "clear_all", lambda: clears.append(True))
    return env


def advance(env, at=NOW):
    remote.step(env.factory, env.client, env.state, at)


@pytest.mark.parametrize("refit", [False, True])
def test_step_queued_running_done_stores_exact_output_and_estimator_key(stepping, refit, caplog):
    env = stepping
    put_rows(env)
    if refit:
        env.db.query(UtilModel).delete()
        env.db.commit()
    sql = []
    event.listen(env.db.get_bind(), "before_cursor_execute", lambda c, u, s, p, x, m: sql.append(s))
    with caplog.at_level(logging.INFO, logger=remote.__name__):
        advance(env)
        dispatch_key, ref = env.state.key, env.state.ref
        assert remote.in_flight(env.state)
        advance(env, NOW + 1)
        env.status = "running"
        advance(env, NOW + 2)
        env.status = "done"
        commits = []

        def committed(db):
            commits.append(db)

        event.listen(Session, "after_commit", committed)
        try:
            advance(env, NOW + 3)
        finally:
            event.remove(Session, "after_commit", committed)
        assert commits == [env.sessions[-1]]
    assert not remote.in_flight(env.state)
    assert env.flags[0] is True and env.flags[-1] is False and env.clears == [True]
    assert all(not path.exists() for path in env.paths)
    with env.factory() as db:
        stored = db.get(UtilModel, "values")
        assert stored.data is None
        assert util_store.decode_values(stored.blob) == json.loads(env.output)["values"]
        assert stored.version == util_values.VERSION and stored.computed_at is not None
        assert stored.key != dispatch_key
        assert stored.key == replay_util.corpus_key(db, replay_util.current_rows(db, with_data=False), util_model.load(db))
        if refit:
            win = db.get(UtilModel, "win")
            assert win.data == json.loads(env.output)["win"] and win.version == util_model.VERSION
    writes = [statement.upper() for statement in sql
              if statement.lstrip().upper().startswith(("INSERT INTO UTIL_MODEL", "UPDATE UTIL_MODEL"))]
    assert writes
    assert all("CAST(" not in statement and "JSONB" not in statement for statement in writes)
    advance(env, NOW + 4)
    assert len(env.client.sent("open")) == 1
    records = [r.getMessage() for r in caplog.records if r.name == remote.__name__]
    for action in ("open", "poll", "collect", "store"):
        assert any(f"util {action}" in line for line in records)
    assert all(ref in line and dispatch_key in line for line in records)


@pytest.mark.parametrize("overrides", [{"version": -1}, {"digest": "other"}, {"sha256": "0" * 64}])
def test_step_rejects_foreign_or_corrupt_result(stepping, overrides):
    env = stepping
    put_rows(env)
    advance(env)
    env.status, env.overrides = "done", overrides
    advance(env, NOW + 1)
    assert env.db.get(UtilModel, "values") is None
    assert [f[0] for f in env.failures] == ["collect"]
    assert list(env.state.tries.values()) == [1]
    assert env.clears == [] and not remote.in_flight(env.state)
    assert all(not path.exists() for path in env.paths)


def test_step_deploy_refusal_refunds_try_and_retains_mismatch_clock(stepping):
    env = stepping
    put_rows(env)
    env.status, env.error_kind = "failed", "deploy"
    for offset in (0, 310, 620, 930, 1240, 1550, 1860):
        advance(env, NOW + offset)
        advance(env, NOW + offset + 1)
        assert list(env.state.tries.values()) == [0]
        advance(env, NOW + offset + 2)
    assert [f[0] for f in env.failures] == ["digest"]
    assert len(env.client.sent("open")) == 7


def test_step_three_infra_failures_and_gave_up_once(stepping):
    env = stepping
    put_rows(env)
    env.status = "failed"
    for offset in (0, 310, 620, 930, 1240):
        advance(env, NOW + offset)
        advance(env, NOW + offset + 1)
    assert [f[0] for f in env.failures] == ["run", "run", "run", "gave_up"]
    assert list(env.state.tries.values()) == [3]
    assert len(env.client.sent("open")) == 3


@pytest.mark.parametrize("status", [WorkerGone("404"), "dead"])
def test_step_dead_or_forgotten_run_spends_try(stepping, status):
    env = stepping
    put_rows(env)
    advance(env)
    env.status = status
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["run"]
    assert list(env.state.tries.values()) == [1]


def test_step_running_timeout_starts_when_first_seen_running(stepping):
    env = stepping
    put_rows(env)
    advance(env)
    advance(env, NOW + 9000)  # queued time never consumes the run limit
    env.status = "running"
    advance(env, NOW + 9001)
    advance(env, NOW + 12601)
    assert not env.failures
    advance(env, NOW + 12602)
    assert [f[0] for f in env.failures] == ["run"]


def test_step_open_refusal_and_unreachable_health_record_dispatch(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    def refuse(*a, **kw):
        raise Unreachable("synthetic refusal")
    monkeypatch.setattr(env.client, "util_open", refuse)
    advance(env)
    assert [f[0] for f in env.failures] == ["dispatch"]
    monkeypatch.setattr(env.client, "health", refuse)
    advance(env, NOW + 310)
    assert [f[0] for f in env.failures] == ["dispatch", "dispatch"]


def test_step_store_failure_rolls_back_both_products(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    env.db.query(UtilModel).delete()
    env.db.commit()
    advance(env)
    env.status = "done"
    def reject(*a):
        raise RuntimeError("synthetic database refusal")
    monkeypatch.setattr(util_model, "rebuild_lock", reject)
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["store"]
    assert env.db.query(UtilModel).count() == 0
    assert all(not db.in_transaction() for db in env.sessions)
    assert not env.clears


@pytest.mark.parametrize("error", [
    OperationalError("INSERT", {}, Exception("SSL SYSCALL error: EOF detected")),
    DBAPIError("INSERT", {}, Exception("gone"), connection_invalidated=True),
])
def test_step_store_lost_connection_gives_up_on_the_key_at_once(stepping, monkeypatch, error):
    env = stepping
    put_rows(env)
    advance(env)
    key = env.state.key
    env.status = "done"

    def reject(*a):
        raise error

    monkeypatch.setattr(util_store, "store_values", reject)
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["store", "gave_up"]
    assert env.state.tries[key] == remote.MAX_TRIES
    assert key in env.state.gave_up
    advance(env, NOW + remote.BACKOFF_S + 2)
    assert len(env.client.sent("open")) == 1
    assert [f[0] for f in env.failures] == ["store", "gave_up"]
    add_replay(env.db, 2)
    env.db.commit()
    advance(env, NOW + remote.BACKOFF_S + 3)
    assert len(env.client.sent("open")) == 2
    assert env.state.key != key


def test_step_store_other_error_keeps_three_tries(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    env.status = "done"

    def reject(*a):
        raise RuntimeError("synthetic store failure")

    monkeypatch.setattr(util_store, "store_values", reject)
    for attempt in range(4):
        at = NOW + attempt * (remote.BACKOFF_S + 2)
        advance(env, at)
        advance(env, at + 1)
    assert [f[0] for f in env.failures] == ["store", "store", "store", "gave_up"]
    assert list(env.state.tries.values()) == [remote.MAX_TRIES]
    assert len(env.client.sent("open")) == 3


def test_step_lost_connection_outside_store_keeps_three_tries(stepping):
    env = stepping
    put_rows(env)
    advance(env)
    key = env.state.key
    env.status = OperationalError("SELECT", {}, Exception("gone"))
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["run"]
    assert env.state.tries[key] == 1
    assert not env.state.gave_up


@pytest.mark.parametrize("refit,output", [
    (False, {"win": None, "values": None}),
    (True, {"win": {}, "values": {"rows": []}}),
    (True, {"win": None, "values": {"rows": []}}),
    (True, {"win": {"key": ""}, "values": {"rows": []}}),
    (True, {"win": {"key": 1}, "values": {"rows": []}}),
])
def test_store_rejects_missing_values_or_win_before_writing(stepping, refit, output):
    env = stepping
    put_rows(env)
    if refit:
        env.db.query(UtilModel).delete()
        env.db.commit()
    advance(env)
    assert env.state.refit is refit
    env.output = corpus.encode_output(output)
    env.status = "done"
    writes = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE")):
            writes.append(statement)

    event.listen(env.db.get_bind(), "before_cursor_execute", capture)
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["store"]
    assert env.db.get(UtilModel, "values") is None
    assert not writes


def test_step_store_failure_after_values_write_keeps_both_old_products(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    old_values = {"rows": [], "estimator": {"key": "synthetic-old"}}
    old_win = {"key": "synthetic-old", "excluded": [], "cells": {}}
    util_store.store_values(env.db, "synthetic-old-values", old_values)
    env.db.merge(UtilModel(name="win", version=util_model.VERSION, key="synthetic-old", data=old_win))
    env.db.commit()
    advance(env)
    assert env.state.refit
    env.status = "done"
    merge = Session.merge
    flushed = []

    def reject_win(db, instance, *a, **kw):
        if isinstance(instance, UtilModel) and instance.name == "win":
            db.flush()
            assert db.get(UtilModel, "values").key != "synthetic-old-values"
            flushed.append(True)
            raise RuntimeError("synthetic win write failure")
        return merge(db, instance, *a, **kw)

    monkeypatch.setattr(Session, "merge", reject_win)
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["store"]
    assert flushed == [True]
    with env.factory() as db:
        values = db.get(UtilModel, "values")
        win = db.get(UtilModel, "win")
        assert values.key == "synthetic-old-values" and values.data is None
        assert util_store.decode_values(values.blob) == old_values
        assert win.key == "synthetic-old" and win.data == old_win


def test_step_new_replay_after_success_opens_new_worker_identity(stepping):
    env = stepping
    put_rows(env)
    advance(env)
    first = env.state.key
    first_build = env.state.build_id
    env.status = "done"
    advance(env, NOW + 1)
    add_replay(env.db, 2)
    env.db.commit()
    advance(env, NOW + 2)
    assert len(env.client.sent("open")) == 2
    assert env.client.sent("open")[-1]["key"] != first
    assert env.client.sent("open")[-1]["refit"] is True
    assert env.state.build_id != first_build


def test_step_disabled_does_not_contact_worker_or_open_session(stepping, monkeypatch):
    env = stepping
    monkeypatch.setattr(remote, "enabled", lambda: False)
    advance(env)
    assert env.sessions == [] and env.client.calls == [] and env.polls == 0


def test_step_result_fetch_failure_is_collected_as_failure(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    advance(env)
    env.status = "done"
    def missing(*a):
        raise WorkerGone("synthetic missing result")
    monkeypatch.setattr(env.client, "util_result", missing)
    advance(env, NOW + 1)
    assert [f[0] for f in env.failures] == ["collect"]
    assert list(env.state.tries.values()) == [1]
    assert env.db.get(UtilModel, "values") is None


def test_util_loop_survives_error_and_holds_no_transaction_while_polling(stepping, monkeypatch):
    env = stepping
    put_rows(env)
    advance(env)
    stop = SimpleNamespace(wait=lambda *a: len(passes) >= 2, is_set=lambda: True)
    passes, failures, successes = [], [], []
    monkeypatch.setattr(remote, "STATE", env.state)
    original_step = remote.step

    def sometimes(*a, **kw):
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("synthetic util failure")
        # The stepping fixture checks every session is out of transaction at worker I/O.
        original_step(*a, **kw)

    monkeypatch.setattr(remote, "step", sometimes)
    monkeypatch.setattr(remote.site_health, "loop_failed", lambda name, exc: failures.append(name))
    monkeypatch.setattr(remote.site_health, "loop_ok", successes.append)
    remote.run_forever(env.factory, env.client, stop, every_s=0)
    assert len(passes) == 2 and env.polls == 1
    assert failures == [remote.LOOP_NAME] and successes == [remote.LOOP_NAME]
