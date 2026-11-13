"""Coach reads stored utility products, with synthetic rows and a deterministic polling clock."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.coach import snapshot
from app.config import settings
from app.critutil import build_digest
from app.models.replay import UtilModel
from app.services import replay_util, util_model, util_rebuild, util_values
from scripts import _admin_client, coach_report, coach_snapshot, coach_store_report


@pytest.fixture
def reader(monkeypatch):
    engine = create_engine("sqlite://", poolclass=QueuePool)
    UtilModel.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as seed:
        seed.add_all([
            UtilModel(name="win", version=util_model.VERSION, key="estimator", data={"excluded": [7]}),
            UtilModel(name="values", version=util_values.VERSION, key="current", data={"rows": []}),
        ])
        seed.commit()
    caller = factory()
    state = SimpleNamespace(now=0.0, polls=[], sleeps=[], payloads=0, events=[], on_sleep=lambda: None,
                            caller=caller, factory=factory, engine=engine)
    replay = SimpleNamespace(id=1, recipe="recipe-1", source_sha256="synthetic-source", match_id=7, map_name="Toy", round_count=1)
    context = {"match": {"linked": True, "rounds": [1]}, "players": {}}

    def find(db, uuid):
        assert db is caller
        db.query(UtilModel.key).first()  # replay/context reads open the caller's transaction
        state.events.append("replay")
        return replay

    monkeypatch.setattr(snapshot.replays, "find_replay", find)
    monkeypatch.setattr(snapshot.replays, "page_context", lambda *a: context)
    monkeypatch.setattr(snapshot.replays, "round_blob", lambda *a: state.events.append("round") or {})
    monkeypatch.setattr(snapshot.fmt, "decode_blob", lambda blob: {"players": []})
    monkeypatch.setattr(snapshot.replay_control, "round_link", lambda *a: {})
    monkeypatch.setattr(snapshot.replay_control, "round_control",
                        lambda *a: SimpleNamespace(status="not_ready", stale=False))

    def current_rows(db, *, with_data=True):
        assert db is not caller and not with_data
        state.polls.append(db)
        return []

    monkeypatch.setattr(replay_util, "current_rows", current_rows)
    monkeypatch.setattr(replay_util, "corpus_key", lambda *a: "current")

    def payload(db, loaded, *, read_only=False):
        assert read_only and db is caller and loaded is replay
        assert state.events[-1] == "replay"
        state.payloads += 1
        state.events.append("util")
        return {"rounds": {"1": {"casts": []}}, "updating": False}

    monkeypatch.setattr(replay_util, "match_payload", payload)

    def sleep(seconds):
        assert not caller.in_transaction()
        assert engine.pool.checkedout() == 0
        state.sleeps.append(seconds)
        state.now += seconds
        state.on_sleep()

    monkeypatch.setattr(snapshot, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep), raising=False)

    def set_key(key):
        with factory() as writer:
            writer.query(UtilModel).filter_by(name="values").update({"key": key})
            writer.commit()

    state.set_key = set_key
    state.read = lambda **kw: snapshot.read(caller, "synthetic", session_factory=factory,
                                           util_wait_s=0.05, poll_s=0.02, **kw)
    yield state
    caller.close()
    engine.dispose()


def test_current_returns_before_rounds_without_asking(reader):
    ask = Mock()
    assert reader.read(ask=ask)["rounds"]["1"]["util"] == {"casts": []}
    ask.assert_not_called()
    assert not reader.sleeps and len(reader.polls) == 1
    assert reader.events == ["replay", "replay", "util", "replay", "round", "replay"]


def test_stale_then_current_on_third_poll_asks_once(reader):
    reader.set_key("old")
    reader.on_sleep = lambda: reader.set_key("current") if len(reader.sleeps) == 2 else None
    ask = Mock(return_value={})
    assert reader.read(ask=ask)["rounds"]
    assert len(reader.polls) == 3 and len({id(s) for s in reader.polls}) == 3
    ask.assert_called_once_with()


def test_deadline_names_both_keys_and_logs_missing_token(reader, caplog):
    reader.set_key("old")
    with pytest.raises(snapshot.SnapshotError, match=r"stored key old, current key current"):
        reader.read()
    assert reader.now == pytest.approx(0.05)
    assert "no REPLAY_ADMIN_TOKEN: waiting for the site's own build" in caplog.text
    assert "round" not in reader.events


def test_poll_selects_metadata_without_model_blobs_or_writes(reader):
    reader.set_key("old")
    statements = []
    writes = []
    event.listen(reader.engine, "before_cursor_execute", lambda c, cur, sql, *a: statements.append(sql))
    event.listen(reader.factory, "before_flush", lambda *a: writes.append("flush"))
    event.listen(reader.factory, "before_commit", lambda *a: writes.append("commit"))
    with pytest.raises(snapshot.SnapshotError):
        reader.read()
    assert statements
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert all("util_model.data AS util_model_data" not in sql for sql in statements)
    assert all(not s.in_transaction() for s in reader.polls)
    assert writes == []


@pytest.mark.parametrize("excluded", [None, [], [7]])
@pytest.mark.parametrize("remote", [False, True])
def test_read_only_never_builds_with_stale_or_missing_products(monkeypatch, excluded, remote):
    monkeypatch.setattr(settings, "util_build_remote", remote)
    db = Mock()
    db.query.return_value.filter.return_value.all.return_value = []
    replay = SimpleNamespace(id=1, round_count=0)
    monkeypatch.setattr(snapshot.replays, "page_context", lambda *a: {"players": {}})
    monkeypatch.setattr(replay_util, "_replay_context", lambda *a: {1: {"match_id": 7}})
    monkeypatch.setattr(util_model, "load", lambda db: {"excluded": excluded} if excluded is not None else None)
    monkeypatch.setattr(replay_util, "_values_row", lambda db: None)
    monkeypatch.setattr(replay_util, "current_rows", lambda *a, **kw: [])
    monkeypatch.setattr(replay_util, "corpus_key", lambda *a: "current")
    monkeypatch.setattr(replay_util, "_cast_rows", lambda *a, **kw: [])
    forbidden = Mock(side_effect=AssertionError("coach attempted a build"))
    monkeypatch.setattr(util_model, "ensure", forbidden)
    monkeypatch.setattr(replay_util, "ensure_values", forbidden)
    monkeypatch.setattr(util_rebuild, "request", forbidden)
    assert replay_util.match_payload(db, replay, wait=True, read_only=True)["updating"]
    forbidden.assert_not_called()


def test_payload_error_is_reported_after_deadline(reader, monkeypatch, caplog):
    monkeypatch.setattr(replay_util, "match_payload", Mock(side_effect=RuntimeError("unreadable utility")))
    with pytest.raises(snapshot.SnapshotError, match="RuntimeError: unreadable utility"):
        reader.read()
    assert reader.now == pytest.approx(0.05) and "round" not in reader.events
    assert "unreadable utility" in caplog.text


def test_payload_recovers_from_one_read_error(reader, monkeypatch):
    original = replay_util.match_payload
    calls = []

    def payload(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("try again")
        return original(*a, **kw)

    monkeypatch.setattr(replay_util, "match_payload", payload)
    assert reader.read()["rounds"] and len(calls) == 2


def test_poll_recovers_from_database_error(reader, monkeypatch):
    original = replay_util.current_rows
    calls = []

    def rows(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        return original(*a, **kw)

    monkeypatch.setattr(replay_util, "current_rows", rows)
    assert reader.read()["rounds"] and len(calls) == 2


def test_ask_error_is_logged_and_wait_continues(reader, caplog):
    reader.set_key("old")
    reader.on_sleep = lambda: reader.set_key("current")
    def request():
        assert not reader.caller.in_transaction() and reader.engine.pool.checkedout() == 0
        raise _admin_client.AdminError("site unavailable")

    ask = Mock(side_effect=request)
    assert reader.read(ask=ask)["rounds"]
    ask.assert_called_once_with()
    assert "site unavailable" in caplog.text


def test_different_site_digest_refuses_immediately(reader, monkeypatch):
    reader.set_key("old")
    monkeypatch.setattr(build_digest, "digest", lambda *a: "local-code")
    ask = Mock(return_value={"stale": True, "in_flight": False, "key": "different-evidence",
                             "stored_key": "old", "digest": "site-code"})
    with pytest.raises(snapshot.SnapshotError, match=r"code differs.*local digest local-code, site digest site-code"):
        reader.read(ask=ask)
    assert reader.sleeps == []


def test_different_corpus_key_with_same_digest_keeps_waiting(reader):
    reader.set_key("old")
    reader.on_sleep = lambda: reader.set_key("current")
    ask = Mock(return_value={"stale": True, "in_flight": True, "key": "different-evidence",
                             "stored_key": "old", "digest": build_digest.digest(Path(snapshot.__file__).resolve().parents[2])})
    assert reader.read(ask=ask)["rounds"] and len(reader.sleeps) == 1


def test_updating_payload_reenters_wait(reader, monkeypatch):
    original = replay_util.match_payload
    calls = []

    def payload(*a, **kw):
        calls.append(1)
        return {"updating": True} if len(calls) == 1 else original(*a, **kw)

    monkeypatch.setattr(replay_util, "match_payload", payload)
    assert reader.read()["rounds"] and len(calls) == 2 and len(reader.sleeps) == 1


@pytest.mark.parametrize("version", [0, util_values.VERSION])
def test_wrong_version_or_uncovered_estimator_is_stale(reader, version):
    with reader.factory() as writer:
        if version == 0:
            writer.query(UtilModel).filter_by(name="values").update({"version": 0})
        else:
            writer.query(UtilModel).filter_by(name="win").update({"data": {"excluded": []}})
        writer.commit()
    with pytest.raises(snapshot.SnapshotError, match="stored build is not current"):
        reader.read()
    assert reader.payloads == 0


@pytest.mark.parametrize("script", [coach_report, coach_snapshot])
@pytest.mark.parametrize("token", [None, "synthetic-token"])
def test_commands_exit_two_on_util_refusal(monkeypatch, tmp_path, capsys, script, token):
    import app.db
    from app.coach import playbook

    if token:
        monkeypatch.setenv("REPLAY_ADMIN_TOKEN", token)
    else:
        monkeypatch.delenv("REPLAY_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(app.db, "SessionLocal", MagicMock())
    monkeypatch.setattr(playbook, "load", lambda: {})
    monkeypatch.setattr(coach_store_report, "resolve", lambda *a: (7, 100))
    ask = Mock(return_value={})
    monkeypatch.setattr(_admin_client, "request_util_build", ask)

    def refused(db, uuid, *, ask=None):
        assert (ask is not None) == bool(token)
        if ask:
            ask()
        raise snapshot.SnapshotError("Critical Util: stored build is not current")

    monkeypatch.setattr(snapshot, "read", refused)
    argv = ["abcdef12-0000-4000-8000-000000000000"]
    argv += ["0"] if script is coach_report else ["--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", ["coach", *argv])
    assert script.main() == 2
    assert "Critical Util" in capsys.readouterr().err
    assert ask.call_args_list == ([((_admin_client.DEFAULT_SITE,), {})] if token else [])


@pytest.fixture
def replacing_reader(reader, monkeypatch):
    state = SimpleNamespace(active=None, payloads=0, on_payload=lambda: None, on_round=lambda: None)

    def replacement(generation, *, row_id=1):
        return SimpleNamespace(id=row_id, recipe=f"recipe-{generation}", source_sha256="synthetic-source",
                               match_id=7, map_name="Toy", round_count=1, generation=generation)

    state.replacement = replacement
    state.active = replacement(1)

    def find(db, uuid):
        db.query(UtilModel.key).first()
        return state.active

    def context(db, replay):
        n = replay.generation
        return {"match": {"linked": True, "rounds": [n], "recipe": replay.recipe},
                "players": {str(n): {"side": "A"}}, "rounds": {str(n): {}}}

    def blob(db, replay, n):
        assert n == replay.generation
        state.on_round()
        return {"players": [{"slot": n, "side": "A"}], "recipe": replay.recipe}

    def payload(db, replay, *, read_only=False):
        assert read_only
        state.payloads += 1
        result = {"updating": False, "rounds": {str(replay.generation): {
            "casts": [], "unused": [], "avail": [], "recipe": replay.recipe}}}
        state.on_payload()
        return result

    monkeypatch.setattr(snapshot.replays, "find_replay", find)
    monkeypatch.setattr(snapshot.replays, "page_context", context)
    monkeypatch.setattr(snapshot.replays, "round_blob", blob)
    monkeypatch.setattr(snapshot.fmt, "decode_blob", lambda value: value)
    monkeypatch.setattr(snapshot.replay_control, "round_link",
                        lambda replay, groups, n: {"sides": {str(n): groups[n]}})
    monkeypatch.setattr(replay_util, "match_payload", payload)
    state.payload = payload
    return state


def assert_replacement_snapshot(data):
    assert data["context"]["match"]["recipe"] == "recipe-2"
    assert set(data["context"]["players"]) == {"2"}
    assert set(data["rounds"]) == {"2"}
    entry = data["rounds"]["2"]
    assert entry["blob"]["recipe"] == entry["util"]["recipe"] == "recipe-2"
    assert entry["link"]["sides"] == {"2": "A"}


@pytest.mark.parametrize("row_id", [1, 2])
def test_replacement_during_util_wait_uses_new_context_rounds_and_util(reader, replacing_reader, monkeypatch, row_id):
    state = replacing_reader
    def payload(*a, **kw):
        if len(reader.polls) == 1:
            return {"updating": True}
        state.active = state.replacement(2, row_id=row_id)
        # The replay was replaced before this poll's lookup, as it would be on the site.
        return state.payload(a[0], state.active, **kw)
    original_find = snapshot.replays.find_replay
    def find(*a):
        if len(reader.polls) == 2:
            state.active = state.replacement(2, row_id=row_id)
        return original_find(*a)
    monkeypatch.setattr(snapshot.replays, "find_replay", find)
    monkeypatch.setattr(replay_util, "match_payload", payload)
    assert_replacement_snapshot(reader.read())
    assert len(reader.polls) == 2


@pytest.mark.parametrize("when", ["confirmation", "rounds"])
def test_replacement_after_util_confirmation_restarts_once(reader, replacing_reader, when):
    state = replacing_reader
    def replace_once():
        state.active = state.replacement(2)
        state.on_payload = state.on_round = lambda: None
    if when == "confirmation":
        state.on_payload = replace_once
    else:
        state.on_round = replace_once
    assert_replacement_snapshot(reader.read())
    assert state.payloads == 2


def test_replacements_that_never_settle_share_one_deadline(reader, replacing_reader):
    state = replacing_reader
    state.on_payload = lambda: setattr(state, "active", state.replacement(state.active.generation + 1))
    with pytest.raises(snapshot.SnapshotError, match="Critical Util: replay identity changed"):
        reader.read()
    assert reader.now == pytest.approx(0.05)
    assert state.payloads >= 2
