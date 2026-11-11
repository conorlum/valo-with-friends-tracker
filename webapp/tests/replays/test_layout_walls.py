"""Idle wall-share counting from each round's own walk mask; synthetic rows on SQLite."""

import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_control_store import db, factory  # noqa: E402,F401

from app.config import settings
from app.models.replay import Replay, ReplayRound, ReplayRoundControl, ReplayRoundWallShare, ReplayUpload
from app.models.site_events import SiteEvent
from app.replays import control_format as cf
from app.replays import format as fmt
from app.services import layout_walls as walls
from app.services import replay_control_remote as remote
from app.services import replay_upload, site_health

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp()


def control_data(walk=(0,)):
    bits = bytearray(cf.GRID * cf.GRID // 8)
    for cell in walk:
        bits[cell >> 3] |= 1 << (7 - (cell & 7))
    return cf.pack_data({"walk": base64.b64encode(bits).decode(), "ticks": []},
                        {"states": b"", "coverage": b"", "control": b""})


def blob(us=(10, 100, 10, 100), *, hz=4, t0=0.0, alive=None):
    """The documented gzip JSON shape, with tracks encoded by the production format helper."""
    return {"v": 1, "round": 1, "map": "Ascent", "hz": hz, "t_start": 0.0,
            "t_decided": 1.0, "t_end": 1.0,
            "players": [{"slot": 0, "agent": "Jett", "side": "A"}],
            "tracks": {"0": [fmt.encode_segment(t0, list(us), [10] * len(us), [0] * len(us))]},
            "alive": {"0": [[0.0, None, None]] if alive is None else alive}, "crouch": {},
            "kills": [], "plant": None, "defuse": None, "util": []}


@pytest.fixture
def replay(db):
    row = Replay(match_uuid="00000000-0000-4000-8000-000000000001", map_name="Ascent", round_count=25,
                 format_version=1, recipe="toy", game_branch="++Ares-Core+release-11.00",
                 source_sha256="0" * 64, source="local", link_status="unlinked", link_inputs={})
    db.add(row)
    db.commit()
    return row


def put_round(db, replay, number=1, *, stored=True, status="ok", fingerprint="first", walk=(0,)):
    if stored:
        db.add(ReplayRound(replay_id=replay.id, round_number=number,
                           data=fmt.encode_blob({**blob(), "round": number})))
    db.add(ReplayRoundControl(replay_id=replay.id, round_number=number, status=status,
                              fingerprint=fingerprint, data_version=cf.DATA_VERSION if status == "ok" else None,
                              data=control_data(walk) if status == "ok" else None,
                              summary=cf.pack_summary({}) if status == "ok" else None))
    db.commit()


def test_crossing_a_wall_counts_positions_from_the_round_mask():
    parsed = fmt.decode_blob(fmt.encode_blob(blob()))
    assert walls.round_share(control_data(), parsed) == (2, 4)
    assert walls.round_share(control_data((0, 1)), parsed) == (0, 4)


def test_every_walk_cell_centre_maps_back_to_its_cell():
    header, _ = cf.unpack_data(control_data(range(cf.GRID * cf.GRID)))
    for cell in cf.walk_bitmap(header):
        u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        assert walls._cell(u, v) == cell
    assert walls._cell(fmt.UV_SCALE, fmt.UV_SCALE) == cf.GRID * cf.GRID - 1


def test_sixteen_hz_keeps_only_four_hz_samples():
    us = [10 if i % 4 == 0 else 100 for i in range(16)]
    assert walls.round_share(control_data(), blob(us, hz=16)) == (0, 4)
    # Sampling is on the round clock, rather than every fourth point of a shifted segment.
    assert walls.round_share(control_data(), blob([100, 100, 10, 100], hz=16, t0=0.125)) == (0, 1)


def test_only_alive_samples_count_and_none_end_stays_alive():
    lives = [[0.25, 0.5, "kill"], [0.75, None, None]]
    assert walls.round_share(control_data(), blob(alive=lives)) == (2, 2)
    assert walls.round_share(control_data(), blob(alive=[])) == (0, 0)


def test_counts_every_slot_and_segment_but_not_slots_without_alive_intervals():
    row = blob()
    row["tracks"]["1"] = [fmt.encode_segment(0, [100], [10], [0]),
                            fmt.encode_segment(0.5, [100], [10], [0])]
    row["alive"]["1"] = [[0, None, None]]
    row["tracks"]["2"] = row["tracks"]["1"]
    assert walls.round_share(control_data(), row) == (4, 6)


def test_changed_fingerprint_recomputes_and_upserts(db, factory, replay):
    put_round(db, replay)
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    share = db.get(ReplayRoundWallShare, (replay.id, 1))
    assert (share.inside, share.total, share.asset_key, share.control_fingerprint) == (2, 4, "Ascent", "first")
    assert share.computed_at.replace(tzinfo=timezone.utc).timestamp() == NOW
    assert walls.step(factory, NOW + 1, control_in_flight=0) == {"computed": 0}
    control = db.get(ReplayRoundControl, (replay.id, 1))
    control.fingerprint, control.data = "second", control_data((0, 1))
    db.commit()
    assert walls.step(factory, NOW + 2, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    assert db.query(ReplayRoundWallShare).count() == 1
    assert (share.inside, share.total, share.control_fingerprint) == (0, 4, "second")
    assert share.computed_at.replace(tzinfo=timezone.utc).timestamp() == NOW + 2


def test_missing_blob_is_stored_as_zero_and_not_picked_again(db, factory, replay):
    put_round(db, replay, stored=False)
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    row = db.get(ReplayRoundWallShare, (replay.id, 1))
    assert (row.inside, row.total) == (0, 0)
    assert walls.step(factory, NOW + 1, control_in_flight=0) == {"computed": 0}


@pytest.mark.parametrize("demo,in_flight,reason", [(True, 0, "demo mode"),
                                                    (False, 1, "map control tasks are in flight")])
def test_skips_before_opening_a_session(monkeypatch, demo, in_flight, reason):
    monkeypatch.setattr(settings, "demo_mode", demo)

    def forbidden():
        pytest.fail("an idle gate must not open a session")

    assert walls.step(forbidden, NOW, control_in_flight=in_flight) == {"skipped": reason}


@pytest.mark.parametrize("status", replay_upload.UNFINISHED)
@pytest.mark.parametrize("session_key", [None, "ordinary", "auto-reparseXrecipe"])
def test_waits_for_ordinary_unfinished_upload(db, factory, replay, status, session_key):
    put_round(db, replay)
    db.add(ReplayUpload(id="00000000-0000-4000-8000-000000000002", status=status, session_key=session_key))
    db.commit()
    assert walls.step(factory, NOW, control_in_flight=0) == {"skipped": "an upload is unfinished"}
    assert db.query(ReplayRoundWallShare).count() == 0


@pytest.mark.parametrize("status,session_key", [("stored", "ordinary"), ("failed", None),
                                                  ("queued", replay_upload.AUTO_PREFIX + "recipe")])
def test_finished_and_automatic_uploads_do_not_block(db, factory, replay, status, session_key):
    put_round(db, replay)
    db.add(ReplayUpload(id="00000000-0000-4000-8000-000000000002", status=status, session_key=session_key))
    db.commit()
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}


def test_step_is_bounded_ordered_and_ignores_failed_control(db, factory, replay, monkeypatch):
    monkeypatch.setattr(walls, "STEP_ROWS", 2)
    for number in (3, 2, 1):
        put_round(db, replay, number)
    put_round(db, replay, 4, status="failed")
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 2}
    assert [r.round_number for r in db.query(ReplayRoundWallShare).order_by(ReplayRoundWallShare.round_number)] == [1, 2]
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 0}


def test_failure_closes_session_and_rolls_back_the_whole_step(db, factory, replay, monkeypatch):
    put_round(db, replay, 1)
    put_round(db, replay, 2)
    real_layout_of = walls.replay_control.layout_of
    calls = []

    def raising(replay):
        calls.append(replay)
        if len(calls) == 2:
            raise ValueError("synthetic wall failure")
        return real_layout_of(replay)

    monkeypatch.setattr(walls.replay_control, "layout_of", raising)
    session = factory()
    closed = []
    real_close = session.close

    def close():
        closed.append(True)
        real_close()

    monkeypatch.setattr(session, "close", close)
    with pytest.raises(ValueError, match="synthetic wall failure"):
        walls.step(lambda: session, NOW, control_in_flight=0)
    assert closed == [True]
    assert db.query(ReplayRoundWallShare).count() == 0


def test_an_unreadable_round_is_stored_as_zero_and_does_not_hold_back_later_rounds(db, factory, replay,
                                                                                    monkeypatch):
    put_round(db, replay, 1)
    put_round(db, replay, 2)
    real_share = walls.round_share

    def raising(data, row):
        if row["round"] == 1:
            raise ValueError("synthetic unreadable round")
        return real_share(data, row)

    monkeypatch.setattr(walls, "round_share", raising)
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 2}
    db.expire_all()
    shares = {r.round_number: (r.inside, r.total) for r in db.query(ReplayRoundWallShare)}
    assert shares == {1: (0, 0), 2: (2, 4)}
    assert walls.step(factory, NOW + 1, control_in_flight=0) == {"computed": 0}


def test_a_replay_with_no_layout_is_keyed_by_its_map_name(db, factory, replay, monkeypatch):
    put_round(db, replay)
    monkeypatch.setattr(walls.replay_control, "layout_of",
                        lambda r: walls.replay_control.layouts.Selection("layout_unknown", None))
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    assert db.get(ReplayRoundWallShare, (replay.id, 1)).asset_key == "Ascent"
    assert walls.step(factory, NOW + 1, control_in_flight=0) == {"computed": 0}


def test_cycle_wall_failure_records_loop_key_and_continues(db, factory, replay, monkeypatch):
    put_round(db, replay)
    submitted = []
    monkeypatch.setattr(remote, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_reparse", lambda *a, **k: frozenset())
    monkeypatch.setattr(remote, "_submit", lambda *a, **k: submitted.append(True))
    monkeypatch.setattr(remote, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(remote, "LIVE", None)
    monkeypatch.setattr(remote.replay_control, "prepare_discovered", lambda *a, **k: None)
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    original = walls.replay_reparse_auto.ordinary_upload_unfinished

    def boom(*args):
        raise ValueError("synthetic wall failure")

    monkeypatch.setattr(walls.replay_reparse_auto, "ordinary_upload_unfinished", boom)
    state = remote.State()
    for i in range(2):
        counts = remote.cycle(factory, None, state, now=NOW + i)
        assert counts["walls_error"] == 1
    site_health.flush(session_factory=factory)
    db.expire_all()
    event = db.get(SiteEvent, ("loop_failed", "layout_walls_step"))
    assert event.count == 2
    assert "synthetic wall failure" in event.error
    assert len(submitted) == 2
    monkeypatch.setattr(walls.replay_reparse_auto, "ordinary_upload_unfinished", original)
    counts = remote.cycle(factory, None, state, now=NOW + 2)
    assert counts["walls_computed"] == 1 and counts["walls_error"] == 0
    site_health.flush(session_factory=factory)
    db.expire_all()
    assert db.get(SiteEvent, ("loop_failed", "layout_walls_step")) is None
