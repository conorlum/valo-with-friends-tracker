"""The Critical Util build across site, worker and task, offline: the real ControlClient and the site's loop
(`util_build_remote.step`) against `replay_worker.server.make_server`, with a fake Render running the real `util` task
in-process. The toy corpus lives on SQLite; nothing touches a real database or account."""

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "webapp" / "tests" / "replays"))

from fake_render import FakeRender  # noqa: E402
from replay_tasks import tasks as task_bodies  # noqa: E402
from replay_worker import server  # noqa: E402

from app.critutil import build_digest, corpus  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.friendship import Friendship  # noqa: E402
from app.models.kill_event import KillEvent  # noqa: E402
from app.models.match import Match, MatchPlayer, MatchSource  # noqa: E402
from app.models.player import Player  # noqa: E402
from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundUtil, UtilModel  # noqa: E402
from app.models.round import Round, RoundPlayerStat  # noqa: E402
from app.services import replay_control_remote, replay_util, util_model, util_values  # noqa: E402
from app.services import util_build_remote as remote  # noqa: E402
from app.services.replay_control_remote import ControlClient  # noqa: E402
from tests.critutil.test_build import FRIEND, fake_gate, toy_corpus  # noqa: E402
from tests.critutil.test_values import MODEL  # noqa: E402

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp()
FINGERPRINT = "f" * 16
COMPUTED = datetime.fromtimestamp(NOW - 600, timezone.utc).replace(tzinfo=None)
FIRST_PLAIN_MODEL = {**MODEL, "key": "toy-win"}
OBSERVATIONS = [(5, 5, False, 10.0, None, True), (4, 5, False, 12.0, None, False), (5, 4, True, 30.0, 20.0, True),
                (3, 5, False, 20.0, None, False), (5, 3, False, 22.0, None, True), (2, 2, True, 40.0, 25.0, False)]
NON_REPLAY = range(101, 111)


def observations_of(match_id):
    return [OBSERVATIONS[(match_id + i) % len(OBSERVATIONS)] for i in range(4)]


@pytest.fixture
def render():
    fake = FakeRender()
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def world(tmp_path, render, monkeypatch):
    """The worker (store, run table, util builds), its Render, the toy corpus and the site's switch on."""
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))
    stored, ctx = toy_corpus()
    settings = server.Settings(temp_root=tmp_path, workflow_slug="replay-tasks", render_api_key="k",
                               render_api_base=render.url)
    table = server.make_runs(settings)
    store = server.UtilStore(settings)
    httpd = server.make_server(server.Worker(settings), runs_table=table, util=store)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    render.runner = lambda kind, args: task_bodies.TASKS[kind](
        args, task_bodies.WorkerLink(base, args["job_id"], args["token"]))

    engine = create_engine("sqlite://")
    models = (Player, Match, MatchPlayer, Round, RoundPlayerStat, KillEvent, Friendship,
              Replay, ReplayPlayer, ReplayRound, ReplayRoundUtil, UtilModel)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in models])
    monkeypatch.setattr(remote, "settings", SimpleNamespace(util_build_remote=True, demo_mode=False))
    monkeypatch.setattr(replay_control_remote.settings, "replay_control_remote", True)
    monkeypatch.setattr(replay_control_remote.settings, "replay_worker_url", "https://synthetic.invalid")
    monkeypatch.setattr(replay_control_remote.settings, "demo_mode", False)
    monkeypatch.setattr(replay_util.Freshness, "preload", lambda *a: None)
    monkeypatch.setattr(replay_util.Freshness, "current", lambda *a: True)
    monkeypatch.setattr(replay_util, "_replay_context", lambda db, ids: {m: c for m, c in ctx.items() if m in ids})
    monkeypatch.setattr(replay_util, "friend_players", lambda db: {FRIEND})
    monkeypatch.setattr(util_model, "load_observations", lambda db, excluded, *, by_match=False, match_ids=None: {
        m: observations_of(m) for m in NON_REPLAY if m not in excluded and (match_ids is None or m in match_ids)})
    failures = []
    monkeypatch.setattr(remote.util_build_health, "record", lambda *a: failures.append(a))
    monkeypatch.setattr(remote.util_build_health, "set_in_flight", lambda value: None)
    monkeypatch.setattr(remote.util_build_health, "clear_all", lambda: None)

    with Session(engine) as db:
        for m in ctx:
            db.add(Match(id=m, external_id=f"toy-{m}", source=MatchSource.SCRAPED))
            db.add(Replay(id=m, match_id=m, match_uuid=f"00000000-0000-0000-0000-{m:012d}", map_name="Haven",
                          round_count=1, format_version=1, recipe="p1.c22.f1.a1", game_branch="toy",
                          source_sha256="0" * 64, source="local", link_status="linked", link_inputs={},
                          clock_offset=0.0))
            db.add(ReplayRoundUtil(replay_id=m, round_number=1, status="ok", fingerprint=FINGERPRINT,
                                   gaps_revision=5, data=stored[m][1], computed_at=COMPUTED))
        for m in NON_REPLAY:
            db.add(Match(id=m, external_id=f"toy-{m}", source=MatchSource.SCRAPED))
            db.add(Round(id=m, match_id=m, round_number=1))
            db.add(KillEvent(id=m, round_id=m, weapon="Vandal", event_time_seconds=8.0))
        # An estimator that already left every replay match out: the first build needs no refit.
        db.add(UtilModel(name="win", key="toy-win", version=util_model.VERSION,
                         data={**FIRST_PLAIN_MODEL, "excluded": sorted(ctx)}))
        db.commit()
    maker = sessionmaker(bind=engine)
    box = SimpleNamespace(maker=maker, client=ControlClient(base), state=remote.UtilState(), store=store,
                          render=render, stored=stored, ctx=ctx, failures=failures, httpd=httpd, engine=engine,
                          tick=0)
    try:
        yield box
    finally:
        httpd.shutdown()
        httpd.server_close()
        engine.dispose()


def cycle(box, count=1):
    for _ in range(count):
        box.tick += 1
        remote.step(box.maker, box.client, box.state, NOW + box.tick)


def run_until_stored(box, limit=60):
    """Cycle (as the site's loop does) until the build is stored and nothing is in flight."""
    import time
    opened = False
    for _ in range(limit):
        cycle(box)
        opened = opened or box.state.phase != "idle" or bool(box.render.started)
        if opened and box.state.phase == "idle":
            return
        time.sleep(0.05)
    pytest.fail(f"the build never finished; phase {box.state.phase}, failures {box.failures}")


def rows_of(box):
    with box.maker() as db:
        return replay_util.current_rows(db, with_data=False)


def expected_values(box, model):
    rows = [(m, 1, FINGERPRINT, COMPUTED, box.stored[m][1]) for m in sorted(box.ctx)]
    values = corpus.corpus_from_inputs((box.stored, box.ctx, {FRIEND}), rows, model)
    return json.loads(corpus.encode_output(values))


def test_a_build_runs_across_site_worker_and_task_and_is_stored(world):
    box = world
    run_until_stored(box)
    assert box.failures == []
    assert [s["task"] for s in box.render.started] == ["replay-tasks/util"]
    with box.maker() as db:
        stored = db.get(UtilModel, "values")
        assert stored.version == util_values.VERSION
        assert stored.data == expected_values(box, {**FIRST_PLAIN_MODEL, "excluded": sorted(box.ctx)})
        # Keyed by what it was built from, the stored estimator included (R5).
        assert stored.key == replay_util.corpus_key(db, replay_util.current_rows(db, with_data=False),
                                                    util_model.load(db))
        assert db.get(UtilModel, "win").key == "toy-win"       # no refit: the estimator is untouched
    assert box.state.phase == "idle"


def test_a_refit_due_stores_the_refitted_estimator_and_nothing_is_built_twice(world):
    box = world
    with box.maker() as db:                               # a replay arrives for a match the estimator trained on
        db.get(UtilModel, "win").data = {**FIRST_PLAIN_MODEL, "excluded": [1, 2, 3]}
        db.commit()
    run_until_stored(box)
    assert box.failures == []
    excluded = set(box.ctx)
    refit = util_model.refit_from({m: observations_of(m) for m in NON_REPLAY}, excluded)
    with box.maker() as db:
        win = db.get(UtilModel, "win")
        assert win.data == json.loads(corpus.encode_output(refit))
        assert win.key == refit["key"] and win.version == util_model.VERSION
        values = db.get(UtilModel, "values")
        assert values.data == expected_values(box, refit)
        # The stored key is the refitted estimator's, not the dispatch key (R5), and it is current now.
        assert values.key == replay_util.corpus_key(db, replay_util.current_rows(db, with_data=False), refit)
        assert values.key != box.state.key
    started = len(box.render.started)
    assert started == 1
    cycle(box, 6)
    assert len(box.render.started) == started and box.state.phase == "idle"
    assert box.failures == []


def test_a_worker_with_another_digest_is_sent_nothing(world):
    box = world
    box.store.digest = "0" * 16
    assert box.store.digest != build_digest.digest(remote.WEBAPP)
    cycle(box, 4)
    assert box.render.started == []
    assert box.state.phase == "idle" and box.state.key is None
    assert box.store.usage() == 0                        # not one input was pushed either
    with box.maker() as db:
        assert db.get(UtilModel, "values") is None
    box.store.digest = build_digest.digest(remote.WEBAPP)       # the worker is redeployed: the build goes ahead
    run_until_stored(box)
    assert len(box.render.started) == 1
