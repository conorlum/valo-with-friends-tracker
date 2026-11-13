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
from app.replays import layouts
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


@pytest.mark.parametrize("start", range(6))
def test_a_segment_starting_on_any_sixteen_hz_index_keeps_its_quarter_seconds(start):
    """F3 (2026-11-12): t0 is stored to 3 decimals (1/16 s is 0.062), so a test on the time itself dropped
    every sample of a segment starting on an odd index. Sampling reads the grid index instead."""
    us = [100] * 160
    parsed = fmt.decode_blob(fmt.encode_blob(blob(us, hz=16, t0=start / 16)))
    inside, total = walls.round_share(control_data(), parsed)
    assert total == 40 and inside == 40


@pytest.mark.parametrize("hz", [4, 16, 30])
def test_on_quarter_keeps_four_samples_a_second(hz):
    assert sum(walls.on_quarter(round(k / hz, 3), hz) for k in range(1, 10 * hz + 1)) == 40


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


def current_fingerprint(db, replay, number=1):
    """The fingerprint control has on the replay's current layout, so its share is filed under that key."""
    from app.services import control_heights, replay_control as rc

    key = rc.layout_of(replay).layout.asset_key
    return rc.round_fingerprint(replay, rc.side_groups(db, replay), number, control_heights.active_digests(db),
                                context=rc.resolve_current_geometry(db, key))


def test_changed_fingerprint_recomputes_and_upserts(db, factory, replay):
    first = current_fingerprint(db, replay)
    assert first is not None
    put_round(db, replay, fingerprint=first)
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    share = db.get(ReplayRoundWallShare, (replay.id, 1))
    assert (share.inside, share.total, share.asset_key, share.control_fingerprint) == (2, 4, "Ascent", first)
    assert share.computed_at.replace(tzinfo=timezone.utc).timestamp() == NOW
    assert walls.step(factory, NOW + 1, control_in_flight=0) == {"computed": 0}
    control = db.get(ReplayRoundControl, (replay.id, 1))
    control.fingerprint, control.data = "second", control_data((0, 1))
    db.commit()
    assert walls.step(factory, NOW + 2, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    assert db.query(ReplayRoundWallShare).count() == 1
    # S9: control that isn't current for the replay's layout (computed on an older mask) is never pooled.
    assert (share.inside, share.total, share.control_fingerprint, share.asset_key) == (0, 4, "second",
                                                                                      walls.NOT_CURRENT)
    assert share.computed_at.replace(tzinfo=timezone.utc).timestamp() == NOW + 2


def test_a_not_current_share_is_refiled_when_its_layout_becomes_current_with_the_same_control(
        db, factory, replay, monkeypatch):
    """R2-4 (2026-11-12): filed NOT_CURRENT while pending; the redraw lands and control's fingerprint is the same
    (its inputs didn't change). The share is refiled under the key, no sooner than RECHECK_S, without re-measuring."""
    fingerprint = current_fingerprint(db, replay)
    put_round(db, replay, fingerprint=fingerprint)
    real = walls.replay_control.layout_of
    monkeypatch.setattr(walls.replay_control, "layout_of",
                        lambda r: walls.replay_control.layouts.Selection("layout_pending", None))
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    monkeypatch.setattr(walls.replay_control, "layout_of", real)
    monkeypatch.setattr(walls, "round_share", lambda *a: pytest.fail("a refile never re-measures"))
    assert walls.step(factory, NOW + walls.RECHECK_S - 1, control_in_flight=0) == {"computed": 0}
    db.expire_all()
    assert db.get(ReplayRoundWallShare, (replay.id, 1)).asset_key == walls.NOT_CURRENT
    assert walls.step(factory, NOW + walls.RECHECK_S, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    share = db.get(ReplayRoundWallShare, (replay.id, 1))
    assert (share.asset_key, share.inside, share.total) == ("Ascent", 2, 4)


def test_a_still_ineligible_share_moves_to_the_back_of_the_recheck(db, factory, replay, monkeypatch):
    put_round(db, replay, fingerprint=current_fingerprint(db, replay))
    monkeypatch.setattr(walls.replay_control, "layout_of",
                        lambda r: walls.replay_control.layouts.Selection("layout_unknown", None))
    walls.step(factory, NOW, control_in_flight=0)
    assert walls.step(factory, NOW + walls.RECHECK_S, control_in_flight=0) == {"computed": 0}
    db.expire_all()
    share = db.get(ReplayRoundWallShare, (replay.id, 1))
    assert share.asset_key == walls.NOT_CURRENT
    assert share.computed_at.replace(tzinfo=timezone.utc) == datetime.fromtimestamp(NOW + walls.RECHECK_S,
                                                                                    timezone.utc)


def test_a_batch_reads_side_groups_in_one_query(db, factory, replay, monkeypatch):
    """R2-10 (2026-11-12): no per-replay side-group or height-digest read inside the dispatcher's pass."""
    put_round(db, replay, fingerprint=current_fingerprint(db, replay))
    monkeypatch.setattr(walls.replay_control, "side_groups", lambda *a: pytest.fail("read per replay"))
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    assert db.get(ReplayRoundWallShare, (replay.id, 1)).asset_key == "Ascent"


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


def test_a_replay_with_no_layout_is_filed_as_not_current(db, factory, replay, monkeypatch):
    put_round(db, replay, fingerprint=current_fingerprint(db, replay))
    monkeypatch.setattr(walls.replay_control, "layout_of",
                        lambda r: walls.replay_control.layouts.Selection("layout_unknown", None))
    assert walls.step(factory, NOW, control_in_flight=0) == {"computed": 1}
    db.expire_all()
    assert db.get(ReplayRoundWallShare, (replay.id, 1)).asset_key == walls.NOT_CURRENT
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


@pytest.fixture
def alarm(monkeypatch):
    """A checked base layout, independent of the shipped map registry."""
    layout = layouts.Layout("Ascent@base", "Ascent", None, (13, 9), None)
    monkeypatch.setattr(walls.replay_control, "layout_of", lambda r: layouts.Selection("ok", layout))
    monkeypatch.setattr(walls, "MIN_BASELINE_REPLAYS", 1)     # its own test below holds the real figure
    monkeypatch.setattr(walls, "_last_raised", set())
    monkeypatch.setattr(walls, "_callout_cache", {})
    recorded, resolved = [], []
    monkeypatch.setattr(site_health, "record", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(site_health, "resolve", lambda *a: resolved.append(a))
    return recorded, resolved


def alarm_replay(db, patch="13.10", *, inside=110, total=1000, rounds=1, current=True, status="ok",
                 map_name="Ascent"):
    serial = db.query(Replay).count() + 1
    row = Replay(match_uuid=f"00000000-0000-4000-8000-{serial:012d}", map_name=map_name,
                 round_count=rounds, format_version=1, recipe="toy",
                 game_branch=f"++Ares-Core+release-{patch}", source_sha256="0" * 64,
                 source="local", link_status="unlinked", link_inputs={})
    db.add(row)
    db.commit()
    for number in range(1, rounds + 1):
        put_round(db, row, number, status=status)
        db.add(ReplayRoundWallShare(replay_id=row.id, round_number=number, asset_key=map_name,
                                   control_fingerprint="first" if current else "old",
                                   inside=inside, total=total, computed_at=datetime.fromtimestamp(NOW, timezone.utc)))
    db.commit()
    return row


def open_alarm(db, key="Ascent:13.10", kind="map_layout_walls"):
    db.add(SiteEvent(kind=kind, key=key, error="synthetic", count=1,
                     first_seen=datetime.fromtimestamp(NOW, timezone.utc),
                     last_seen=datetime.fromtimestamp(NOW, timezone.utc)))
    db.commit()


def later_replays(db, **kwargs):
    for _ in range(4):
        alarm_replay(db, **kwargs)


def test_evaluate_records_patch_rise_and_context(db, alarm):
    # Pooled baseline is 10%, rather than the unweighted mean of the round shares (30%).
    alarm_replay(db, "13.08", inside=50, total=100)
    alarm_replay(db, "13.09", inside=50, total=900)
    later_replays(db)
    open_alarm(db)
    open_alarm(db, "Ascent:13.11")
    walls.evaluate(db)
    recorded, resolved = alarm
    [(kind, key, error, context)] = recorded
    assert (kind, key) == ("map_layout_walls", "Ascent:13.10")
    assert error and "\n" not in error
    assert {k: context[k] for k in ("map", "patch", "layout", "replays", "share", "baseline")} == {
        "map": "Ascent", "patch": "13.10", "layout": "Ascent", "replays": 4,
        "share": 11.0, "baseline": 10.0}
    assert "callouts" in context
    assert resolved == [("map_layout_walls", "Ascent:13.11")]


@pytest.mark.parametrize("inside", [104, 105])
def test_evaluate_below_or_at_threshold_resolves_without_decoding(db, alarm, monkeypatch, inside):
    alarm_replay(db, "13.09", inside=100)
    later_replays(db, inside=inside)
    open_alarm(db)
    open_alarm(db, "unrelated", kind="page_error")
    monkeypatch.setattr(fmt, "decode_blob", lambda *a: pytest.fail("a quiet patch must not decode blobs"))
    walls.evaluate(db)
    assert alarm == ([], [("map_layout_walls", "Ascent:13.10")])


def test_evaluate_requires_four_distinct_replays(db, alarm):
    alarm_replay(db, "13.09", inside=100)
    for _ in range(3):
        alarm_replay(db, rounds=2)
    walls.evaluate(db)
    assert alarm == ([], [])


def test_evaluate_baseline_patches_never_raise(db, alarm):
    later_replays(db, patch="13.08", inside=0)
    later_replays(db, patch="13.09", inside=900)
    walls.evaluate(db)
    assert alarm == ([], [])


def test_evaluate_redraw_covers_patch_and_resolves(db, alarm, monkeypatch):
    alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    open_alarm(db)
    redraw = layouts.Layout("Ascent@13.10", "Ascent_13_10", (13, 10), (13, 10), None)
    base = layouts.Layout("Ascent@base", "Ascent", None, (13, 9), None)
    monkeypatch.setattr(walls.replay_control, "layout_of", lambda r: layouts.Selection(
        "ok", redraw if layouts.parse_patch(r.game_branch) >= (13, 10) else base))
    walls.evaluate(db)
    assert alarm == ([], [("map_layout_walls", "Ascent:13.10")])


def test_evaluate_demo_does_not_even_read_session(db, alarm, monkeypatch):
    open_alarm(db)
    monkeypatch.setattr(settings, "demo_mode", True)
    walls.evaluate(None)
    assert alarm == ([], [])


@pytest.mark.parametrize("invalid", [{"current": False}, {"status": "failed"}, {"patch": "unknown"}])
def test_evaluate_excludes_noncurrent_failed_and_unpatched_rows(db, alarm, invalid):
    alarm_replay(db, "13.09", inside=100)
    for _ in range(3):
        alarm_replay(db)
    alarm_replay(db, **invalid)
    walls.evaluate(db)
    assert alarm == ([], [])


@pytest.mark.parametrize("state", ["layout_unknown", "layout_pending"])
def test_evaluate_skips_unavailable_selection(db, alarm, monkeypatch, state):
    alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    monkeypatch.setattr(walls.replay_control, "layout_of", lambda r: layouts.Selection(state, None))
    walls.evaluate(db)
    assert alarm == ([], [])


@pytest.mark.parametrize("missing", ["baseline", "drawn_from", "samples", "other_map"])
def test_evaluate_requires_a_sampled_baseline_for_same_map_and_layout(db, alarm, monkeypatch, missing):
    if missing != "baseline":
        alarm_replay(db, "13.09", inside=0, total=0 if missing == "samples" else 1000,
                     map_name="Haven" if missing == "other_map" else "Ascent")
    if missing == "drawn_from":
        layout = layouts.Layout("Ascent@base", "Ascent", None, None, None)
        monkeypatch.setattr(walls.replay_control, "layout_of", lambda r: layouts.Selection("ok", layout))
    later_replays(db)
    walls.evaluate(db)
    assert alarm == ([], [])


def test_evaluate_names_inside_callouts_and_limits_decoding(db, alarm, monkeypatch):
    from app.coach import areas

    alarm_replay(db, "13.09", inside=100)
    later_replays(db, rounds=51)
    decoded, located = [], []
    original = fmt.decode_blob

    def decode(data):
        decoded.append(True)
        return original(data)

    def locate(key, u, v):
        located.append((key, u, v))
        return {"name": "Mid", "super": "Center"}

    monkeypatch.setattr(fmt, "decode_blob", decode)
    monkeypatch.setattr(areas, "area_at", locate)
    walls.evaluate(db)
    context = alarm[0][0][3]
    assert context["callouts"] == [{"name": "Mid", "count": 400}]
    assert len(decoded) == 200
    assert len(located) == 400 and set(located) == {("Ascent", 100, 10)}


def test_evaluate_top_three_callouts_include_unnamed(db, alarm, monkeypatch):
    from app.coach import areas

    alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    names = iter(["Mid", "Mid", None, "Mid", None, "A", "B", "Mid"])
    monkeypatch.setattr(areas, "area_at", lambda *a: None if (name := next(names)) is None else {"name": name})
    walls.evaluate(db)
    assert alarm[0][0][3]["callouts"] == [
        {"name": "Mid", "count": 4}, {"name": "unnamed", "count": 2}, {"name": "A", "count": 1}]


def test_a_replay_with_no_measured_position_does_not_count_toward_the_minimum(db, alarm):
    """S13: three sampled replays and one 0/0 replay are three, not four."""
    alarm_replay(db, "13.09", inside=100)
    for _ in range(3):
        alarm_replay(db)
    alarm_replay(db, inside=0, total=0)
    walls.evaluate(db)
    assert alarm == ([], [])


def test_a_baseline_needs_four_replays(db, alarm, monkeypatch):
    """F8: a baseline from fewer than MIN_BASELINE_REPLAYS replays judges nothing, and says so."""
    monkeypatch.setattr(walls, "MIN_BASELINE_REPLAYS", 4)
    for _ in range(3):
        alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    assert walls.evaluate(db) == {"Ascent": 3}
    assert alarm == ([], [])
    alarm_replay(db, "13.09", inside=100)
    assert walls.evaluate(db) == {}
    assert [r[1] for r in alarm[0]] == ["Ascent:13.10"]


def test_a_standing_alarm_is_recorded_once_per_evidence(db, alarm):
    """F4: the record carries a dedupe on what was measured, so a PR-cleared false alarm stays cleared until
    the evidence changes, and the count doesn't climb every pass."""
    alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    walls.evaluate(db)
    first = alarm[0][0][3]["evidence"]
    assert walls._seen_evidence(None, {"evidence": first}) == {"evidence": first}
    assert walls._seen_evidence({"evidence": first}, {"evidence": first}) is None
    walls.evaluate(db)
    assert alarm[0][1][3]["evidence"] == first, "the same measurement is the same evidence"
    alarm_replay(db)
    walls.evaluate(db)
    assert alarm[0][2][3]["evidence"] != first, "a new replay is new evidence"


def test_a_dedupe_skips_the_flush_of_a_cleared_alarm_with_the_same_evidence(db, factory):
    site_health.record("map_layout_walls", "Ascent:13.10", "rose", {"evidence": "e1"},
                       dedupe=walls._seen_evidence)
    site_health.flush(session_factory=factory)
    db.query(SiteEvent).filter(SiteEvent.kind == "map_layout_walls").delete()   # a PR's resolved entry
    db.commit()
    site_health.record("map_layout_walls", "Ascent:13.10", "rose", {"evidence": "e1"},
                       dedupe=walls._seen_evidence, refresh=True)
    site_health.flush(session_factory=factory)
    db.expire_all()
    assert db.get(SiteEvent, ("map_layout_walls", "Ascent:13.10")) is None, "a refresh never brings a row back"
    site_health.record("map_layout_walls", "Ascent:13.10", "rose", {"evidence": "e2"},
                       dedupe=walls._seen_evidence)
    site_health.flush(session_factory=factory)
    db.expire_all()
    assert db.get(SiteEvent, ("map_layout_walls", "Ascent:13.10")) is not None


def test_a_standing_alarm_and_its_mark_outlive_retention_while_the_evidence_stands(db, factory, monkeypatch):
    """R2-8 (2026-11-12): the same evidence, reported again, moves last_seen and the mark forward without
    counting, so retention (KEEP_DAYS) drops neither the alarm nor the mark that keeps a cleared one cleared."""
    from datetime import timedelta

    from app.models.site_events import SiteEventMark

    key = ("map_layout_walls", "Ascent:13.10")
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    clock = {"now": start}
    monkeypatch.setattr(site_health, "_now", lambda: clock["now"])
    for day in range(0, 2 * site_health.KEEP_DAYS, 7):
        clock["now"] = start + timedelta(days=day)
        site_health.record(*key, "rose", {"evidence": "e1"}, dedupe=walls._seen_evidence, refresh=True)
        site_health.flush(session_factory=factory)
    db.expire_all()
    row = db.get(SiteEvent, key)
    assert row is not None and row.count == 1
    assert site_health._aware(row.last_seen) == clock["now"]
    mark = db.query(SiteEventMark).filter(SiteEventMark.kind == key[0], SiteEventMark.key == key[1]).one()
    assert site_health._aware(mark.updated_at) == clock["now"]


def test_a_key_raised_last_pass_and_still_queued_is_resolved(db, alarm, monkeypatch):
    """F11/S10: the previous pass's raise may not be flushed yet; dropping below resolves it all the same."""
    alarm_replay(db, "13.09", inside=100)
    later_replays(db)
    walls.evaluate(db)
    assert [r[1] for r in alarm[0]] == ["Ascent:13.10"]
    monkeypatch.setattr(walls, "THRESHOLD_POINTS", 50)
    walls.evaluate(db)
    assert alarm[1] == [("map_layout_walls", "Ascent:13.10")]


def test_the_cycle_evaluates_a_quiet_site_every_ten_minutes(db, factory, monkeypatch):
    evaluated = []
    monkeypatch.setattr(remote, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_reparse", lambda *a, **k: frozenset())
    monkeypatch.setattr(remote, "_submit", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(remote, "LIVE", None)
    monkeypatch.setattr(remote.replay_control, "prepare_discovered", lambda *a, **k: None)
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    monkeypatch.setattr(walls, "evaluate", lambda session: evaluated.append(True), raising=False)
    monkeypatch.setattr(walls, "step", lambda *a, **k: {"computed": 0})
    state = remote.State()
    for at in (0, 20, walls.EVALUATE_EVERY_S - 1, walls.EVALUATE_EVERY_S, walls.EVALUATE_EVERY_S + 20):
        remote.cycle(factory, None, state, now=NOW + at)
    assert len(evaluated) == 2


def test_cycle_evaluate_failure_records_loop_key_and_continues(db, factory, monkeypatch):
    submitted, closed = [], []
    monkeypatch.setattr(remote, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_reparse", lambda *a, **k: frozenset())
    monkeypatch.setattr(remote, "_submit", lambda *a, **k: submitted.append(True))
    monkeypatch.setattr(remote, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(remote, "LIVE", None)
    monkeypatch.setattr(remote.replay_control, "prepare_discovered", lambda *a, **k: None)
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    monkeypatch.setattr(walls, "step", lambda *a, **k: {"computed": 0})

    def boom(session):
        original = session.close

        def close():
            closed.append(True)
            original()

        monkeypatch.setattr(session, "close", close)
        raise ValueError("synthetic evaluation failure")

    monkeypatch.setattr(walls, "evaluate", boom, raising=False)
    state = remote.State()
    counts = remote.cycle(factory, None, state, now=NOW)
    assert counts["walls_error"] == 1
    assert closed == [True] and submitted == [True]
    site_health.flush(session_factory=factory)
    db.expire_all()
    event = db.get(SiteEvent, ("loop_failed", "layout_walls_step"))
    assert "synthetic evaluation failure" in event.error


def test_cycle_evaluates_first_pass_and_after_computation(db, factory, monkeypatch):
    evaluated = []
    monkeypatch.setattr(remote, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_reparse", lambda *a, **k: frozenset())
    monkeypatch.setattr(remote, "_submit", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(remote, "LIVE", None)
    monkeypatch.setattr(remote.replay_control, "prepare_discovered", lambda *a, **k: None)
    monkeypatch.setattr(remote.replay_heights_remote, "enabled", lambda: False)
    monkeypatch.setattr(walls, "evaluate", lambda session: evaluated.append(True), raising=False)
    results = iter([{"skipped": "busy"}, {"computed": 0}, {"computed": 1}, {"computed": 0}])
    monkeypatch.setattr(walls, "step", lambda *a, **k: next(results))
    state = remote.State()
    for i in range(4):
        remote.cycle(factory, None, state, now=NOW + i)
        # R2-11: a pass the backfill skipped (an upload, control in flight) never evaluates, not even the first
        assert len(evaluated) == [0, 1, 2, 2][i]
