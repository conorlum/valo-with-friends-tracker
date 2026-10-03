"""Timing gaps' storage (timing-gaps spec, section 7): a round's run row and gap rows are replaced together.
SQLite, no engine."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from test_replay_store import TABLES, add_match, condensed  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.gaps.rows import to_rows  # noqa: E402
from app.models.replay import ReplayGap, ReplayRoundGapRun  # noqa: E402
from app.replays import store  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES + [ReplayRoundGapRun.__table__, ReplayGap.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def stored_round(session_factory, condensed):
    db = session_factory()
    add_match(db)
    replay_id = store.store_replay(db, condensed, source="local").replay_id
    db.close()
    return replay_id, 1


RUN = {"status": "ok", "fingerprint": "f" * 16, "gaps_revision": 1, "chokes_hash": None, "notes": {}, "error": None}
ROW = {"seq": 0, "kind": "predicted", "map": "Toy", "victim_slot": 0, "victim_side": None, "t_open": 1.0,
       "t_last_exposed": 2.0, "t_close": 7.0, "spot_cell": 5, "victim_cell": 6, "distance_m": 3.0,
       "angle_deg": 170.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing", "cause_detail": {},
       "choke_seq": [], "route": [[[1.0, 1.0, 1.0]]], "candidate_slots": [5], "candidate_distances": {"5": 3.0},
       "checked_at": None, "stood_at": None, "stood_by": None, "shot_at": None, "shot_by": None,
       "killed_at": None, "killed_by": None, "victim_won_at": None, "context": {}, "linked_seq": None}


def test_store_replaces_a_rounds_gaps_in_one_go(session_factory, stored_round):
    replay_id, n = stored_round
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW, {**ROW, "seq": 1}]) == "stored"
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW]) == "stored"
    s = session_factory()
    assert s.query(ReplayGap).filter_by(replay_id=replay_id, round_number=n).count() == 1
    assert s.get(ReplayRoundGapRun, (replay_id, n)).gap_count == 1


def test_a_failed_run_stores_no_rows(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, RUN, [ROW])
    assert store_gaps(session_factory, replay_id, n, {**RUN, "status": "failed", "error": "boom"}, []) == "stored"
    s = session_factory()
    assert s.get(ReplayRoundGapRun, (replay_id, n)).status == "failed"
    assert s.query(ReplayGap).count() == 0


def _gap(**kw):
    base = dict(kind="predicted", victim=3, team="A", choke_seq=(2, 4), t_open=1.0, spot=7, victim_node=8,
                distance_m=4.0, angle_deg=150.0, route=[[[1.0, 2.0, 3.0]]], cause="open_timing", cause_detail={},
                candidates={5: 4.0, 2: 6.5}, t_last_exposed=2.0, t_close=7.0, qualified_s=2.00049, flicker=False,
                checked_at=[], stood_at=None, stood_by=None, shot_at=None, shot_by=None, killed_at=None,
                killed_by=None, victim_won_at=None, context={}, linked=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_to_rows_assigns_seq_links_and_json_ready_fields(session_factory, stored_round):
    replay_id, n = stored_round
    back = _gap(kind="backshot", qualified_s=0.0, choke_seq=None)
    pred = _gap(linked=back)
    back.linked = pred
    rnd = SimpleNamespace(group_side={"A": "attack"})
    geo = SimpleNamespace(name="Toy", heights=None, node_cell=np.arange(20))
    rows = to_rows([pred, back], rnd, geo)
    assert [r["seq"] for r in rows] == [0, 1]
    assert rows[0]["linked_seq"] == 1 and rows[1]["linked_seq"] == 0
    assert rows[0]["victim_side"] == "attack" and rows[0]["map"] == "Toy"
    assert rows[0]["qualified_s"] == 2.0 and rows[1]["qualified_s"] is None
    assert rows[0]["choke_seq"] == [2, 4] and rows[1]["choke_seq"] is None
    assert rows[0]["candidate_slots"] == [2, 5] and rows[0]["candidate_distances"] == {"5": 4.0, "2": 6.5}
    assert rows[0]["checked_at"] is None and rows[0]["spot_cell"] == 7
    assert store_gaps(session_factory, replay_id, n, RUN, rows) == "stored"
    s = session_factory()
    assert s.query(ReplayGap).count() == 2
