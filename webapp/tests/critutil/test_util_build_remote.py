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
from sqlalchemy.orm import Session

from app.critutil import build_digest, corpus
from app.db import Base
from app.models.friendship import Friendship
from app.models.kill_event import KillEvent
from app.models.match import Match, MatchPlayer, MatchSource
from app.models.player import Player
from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundUtil, UtilModel
from app.models.round import Round, RoundPlayerStat
from app.services import replay_util, util_model, util_values
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

    def util_have(self, rows, obs=(), *, prune=False, ref=None):
        self.calls.append(("have", dict(rows=list(rows), obs=list(obs), prune=prune, ref=ref)))
        if self.failure == "have":
            raise Unreachable("offline")
        return {"missing_rows": list(rows) if self.missing_rows is None else list(self.missing_rows),
                "missing_obs": list(obs) if self.missing_obs is None else list(self.missing_obs)}

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
    all_missing = [remote.obs_key(mid, 1, mid) for mid in range(2, remote.OBS_MATCHES_PER_CYCLE + 4)]
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
    assert remote.obs_key(7, 11, 23) == f"7-11-23-{remote.OBS_VERSION}"


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
