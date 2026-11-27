"""Offline contracts for clutch unknown space, information, fakes and utility."""

import base64
import gzip
from pathlib import Path
import socket
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clutch_measures as measures

from app.coach.roundview import RoundView
from app.control.encode import encode_masks
from app.control.geometry import GRID
from app.replays import control_format as cf
from tests.moments.helpers import damage, kill, round_of, toy_ability


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def payload(*, unknown=True, events=(), masks=None, cells=None):
    cells = cells if cells is not None else [25 * GRID + x for x in range(20, 28)]
    walk = np.zeros(GRID * GRID, dtype=bool)
    walk[cells] = True
    header = {"hz": 16, "ticks": [160, 176], "cells": 8,
              "walk": base64.b64encode(np.packbits(walk).tobytes()).decode("ascii"),
              "checkpoints": [[0, 0, 0, 0]], "unknown_checkpoints": [[0, 0, 0]],
              "knew": {"A": {"7": [[4.0, 5.0, 1234, 5678]]}},
              "knowledge_events": {"A": list(events)}, "group_side": {"A": "attack", "B": "defense"}}
    streams = {"states": b"", "coverage": b"", "control": b""}
    if unknown:
        masks = masks if masks is not None else [[1] * 8, [1] * 4 + [0] * 4]
        for group, frames in (("a", masks), ("b", [[0] * 8] * 2)):
            streams["unknown_" + group] = encode_masks(np.asarray(frames, bool)[:, None, :], [0])[0]
    return cf.pack_data(header, streams)


def pair(*, util=(), kills=(), end=40, points=None):
    return round_of({0: ("A", points or [(0, 180, 204, 0)]),
                     7: ("B", [(0, 300, 204, 180)]),
                     1: ("A", [(0, 180, 270, 0)])}, util=util, kills=kills, t_end=end)


def window(start=10, end=30):
    return {"slot": 0, "t_s": start, "t_end_window": end, "side": "attack"}


@pytest.mark.parametrize("raw", [False, True])
def test_control_seconds_gzip_and_lazy_masks(raw, monkeypatch):
    packed = payload()
    calls = []
    decode = cf.decode_unknown
    def tracked(*args, **kwargs):
        calls.append(True)
        return decode(*args, **kwargs)
    monkeypatch.setattr(cf, "decode_unknown", tracked)
    ctl = measures.ControlRound(gzip.decompress(packed) if raw else packed)
    assert ctl.has_unknown
    assert ctl.header["hz"] == 16
    assert list(ctl.ticks) == [10, 11]
    assert calls == []
    assert ctl.unknown_share("A", 9) is None
    assert ctl.unknown_share("A", 10) == 1
    assert ctl.unknown_share("A", 10.9) == 1
    assert ctl.unknown_share("A", 11) == 0.5
    assert ctl.unknown_share("A", 50) == 0.5
    assert len(calls) == 1
    assert ctl.unknown_share("B", 10) == 0


def test_missing_unknown_is_null():
    rnd, _ = pair()
    ctl = measures.ControlRound(payload(unknown=False))
    assert not ctl.has_unknown
    assert measures.unknown(rnd, ctl, window()) == {
        "unknown_share_start": None, "unknown_share_end": None, "into_unknown_share": None}


def test_located_uses_overlapping_sight_runs_and_found_class_sound():
    ctl = measures.ControlRound(payload())
    assert ctl.located("A", 7, 5.5, 10) is None
    assert ctl.located("A", 7, 4.5, 9.5) == "seen"
    assert ctl.located("A", 7, 5, 5) == "seen"
    ctl = measures.ControlRound(payload(events=[[8, 7, "footsteps", "locate"],
                                                 [9, 6, "seen", "locate"],
                                                 [9, 5, "knife_zero", "locate"]]))
    assert ctl.located("A", 7, 5.5, 10) == "heard"
    assert ctl.located("A", 7, 4.5, 9.5) == "seen"
    assert ctl.located("A", 7, 8.1, 10) is None
    assert ctl.located("A", 6, 5, 10) is None
    assert ctl.located("A", 5, 5, 10) is None
    assert ctl.located("B", 7, 0, 10) is None


def test_information_counts_engagements_once_with_five_second_lookback():
    ctl = measures.ControlRound(payload(events=[[8, 6, "footsteps", "locate"]]))
    events = [{"t": 9, "enemies": [7, 6], "kind": "fight"},
              {"t": 11, "enemies": [7], "kind": "sight"}]
    assert measures.information(ctl, "A", events) == {"located_share": 0.5}
    assert measures.information(ctl, "A", []) == {"located_share": None}
    assert measures.information(ctl, "A", [{"t": 9, "enemies": [7, 5], "kind": "fight"}]) == {
        "located_share": 0}


def test_distance_uses_walk_rank_and_moving_ticks():
    # Eight nonzero flat cell indices; unknown only at the far end of the path.
    ctl = measures.ControlRound(payload(masks=[[0] * 7 + [1]] * 2))
    rnd, _ = pair(points=[(0, 164, 204, 0), (10, 164, 204, 0), (10.5, 200, 204, 0)])
    distances = [ctl.nearest_unknown_m("A", x, 204, 10, rnd.geo) for x in (164, 180, 200)]
    assert distances == sorted(distances, reverse=True)
    assert distances[0] == pytest.approx(56 * rnd.geo.m_per_px)
    assert measures.unknown(rnd, ctl, window(10.1, 10.5)) == {
        "unknown_share_start": 0.125, "unknown_share_end": 0.125, "into_unknown_share": 1.0}
    assert ctl.nearest_unknown_m("B", 164, 204, 10, rnd.geo) is None


@pytest.mark.parametrize("yaw, expected", [(0, 0), (180, 1), (309, 0), (308, 1)])
def test_backshot_is_bearing_outside_view_cone(yaw, expected):
    rnd, blob = pair(points=[(0, 180, 204, yaw)], util=[damage(12, 7, 0)])
    assert measures.backshots(rnd, blob, 0, 10, 20) == expected


def test_backshots_exclude_outgoing_friendly_and_outside_window():
    rnd, blob = pair(points=[(0, 180, 204, 180)], util=[damage(9, 7, 0), damage(21, 7, 0),
                      damage(12, 0, 7), damage(12, 1, 0)])
    assert measures.backshots(rnd, blob, 0, 10, 20) == 0


def test_backshots_count_runs_without_inventing_individual_hit_positions():
    rnd, blob = pair(points=[(0, 180, 204, 180)], util=[damage(12, 7, 0, t1=13, n=3)])
    assert measures.backshots(rnd, blob, 0, 10, 20) == 1


@pytest.mark.parametrize("start, end, expected", [
    (9, 11, 1), (9, 10, 1), (10, 11, 1), (12, 13, 1),
    (8, 9, 0), (21, 22, 0), (20, 22, 1), (9, 22, 1),
])
def test_backshots_intersect_closed_damage_run_window(start, end, expected):
    rnd, blob = pair(points=[(0, 180, 204, 180)],
                     util=[damage(start, 7, 0, t1=end, n=3)])
    assert measures.backshots(rnd, blob, 0, 10, 20) == expected


@pytest.mark.parametrize("before, at_entry, expected", [(0, 180, 1), (180, 0, 0)])
def test_crossing_backshot_uses_bearing_at_entry(before, at_entry, expected):
    rnd, blob = pair(points=[(0, 180, 204, before), (10, 180, 204, at_entry)],
                     util=[damage(9, 7, 0, t1=11)])
    assert measures.backshots(rnd, blob, 0, 10, 20) == expected


def view_of(blob):
    return RoundView(blob, {0: "0", 1: "1", 7: "7"}, 1, None)


def test_defuse_tap_followed_by_enemy_damage():
    bomb = toy_ability("", "Spike", 180, 204, 7, t=5, t1=40, kind="Bomb",
                       defuses=[[12, 13, 0, False]])
    rnd, blob = pair(util=[bomb, damage(16, 7, 0)])
    events = measures.engagements(rnd, blob, 0, 10, 30)
    assert measures.fakes(view_of(blob), rnd, blob, window(), events) == {"fakes": 1, "fake_followed": 1}


def test_unfinished_plant_and_completed_plant():
    rnd, blob = pair()
    blob["player_state"] = {"plants": [[12, 15, 0], [20, 24, 0]],
                            "spike": [{"t": 24, "s": "planted", "slot": 0}]}
    assert measures.fakes(view_of(blob), rnd, blob, window(), []) == {"fakes": 1, "fake_followed": 0}


@pytest.mark.parametrize("delay, followed", [(0, 1), (5, 1), (5.01, 0)])
def test_fake_followup_window_begins_at_release(delay, followed):
    rnd, blob = pair()
    blob["player_state"] = {"plants": [[12, 15, 0]]}
    events = [{"t": 15 + delay, "enemies": [7], "kind": "fight"}]
    assert measures.fakes(view_of(blob), rnd, blob, window(), events) == {
        "fakes": 1, "fake_followed": followed}


def test_half_and_completed_defuses_are_not_taps():
    bomb = toy_ability("", "Spike", 180, 204, 7, t=5, t1=40, kind="Bomb",
                       defuses=[[12, 15.5, 0, False], [20, 23.5, 0, True]])
    rnd, blob = pair(util=[bomb])
    assert measures.fakes(view_of(blob), rnd, blob, window(), []) == {"fakes": 0, "fake_followed": None}


def test_fakes_require_owner_window_enemy_and_known_stop():
    rnd, blob = pair(kills=[kill(11, 0, 7)])
    blob["player_state"] = {"plants": [[5, 8, 0], [12, 15, 0], [12, 15, 1], [30, 33, 0], [10, None, 0]]}
    assert measures.fakes(view_of(blob), rnd, blob, window(), []) == {"fakes": 0, "fake_followed": None}


@pytest.mark.parametrize("charges, expected", [
    ({"E": [{"t": 0, "n": 2}, {"t": 20, "n": 1}]}, 2),
    ({}, None), ({"E": [{"t": 15, "n": 2}]}, None),
    ({"E": [{"t": 0, "n": 0}]}, 0),
    ({"E": [{"t": 0, "n": 2}], "X": [{"t": 0, "n": 1}]}, 2),
    ({"E": [{"t": 0, "n": 2}], "Q": [{"t": 15, "n": 1}]}, None),
])
def test_held_start_missing_zero_and_ult_exclusion(charges, expected):
    rnd, blob = pair()
    blob["player_state"] = {"charges": {"0": charges}}
    assert measures.util(rnd, blob, 0, 10, None, None)["held_start"] == expected


def test_held_at_death_and_survival():
    rnd, blob = pair()
    blob["player_state"] = {"charges": {"0": {"E": [{"t": 0, "n": 2}, {"t": 20, "n": 1}]}}}
    assert measures.util(rnd, blob, 0, 10, 30, None) == {
        "held_start": 2, "held_at_death": 1, "used": 0, "ult_ready": None}
    assert measures.util(rnd, blob, 0, 10, None, None)["held_at_death"] is None


@pytest.mark.parametrize("ps", [None, {}, {"charges": None}, {"charges": {"1": {"E": [{"t": 0, "n": 2}]}}}])
def test_absent_charge_evidence_is_not_zero(ps):
    rnd, blob = pair()
    blob["player_state"] = ps
    result = measures.util(rnd, blob, 0, 10, 30, None)
    assert result["held_start"] is None
    assert result["held_at_death"] is None


def smoke(t, by=0):
    return [toy_ability("Wraith", "4_Smoke", 250, 204, by, t=t, t1=t + 1, kind="Projectile"),
            toy_ability("Wraith", "4_Smoke", 250, 204, by, t=t + 1, t1=t + 10,
                        thrown={"t0": t})]


@pytest.mark.parametrize("rows, expected", [(smoke(12), 1), (smoke(12) + smoke(25), 2),
    (smoke(12, 1), 0), (smoke(8), 0), (smoke(31), 0)])
def test_used_counts_casts_in_window_with_merged_pieces(rows, expected):
    rnd, blob = pair(util=rows)
    assert measures.util(rnd, blob, 0, 10, 30, None)["used"] == expected


def test_survivor_use_window_stops_at_round_decision():
    rnd, blob = pair(util=smoke(45))
    assert measures.util(rnd, blob, 0, 10, None, None)["used"] == 0


def test_movement_uses_come_from_build_casts_too():
    rnd, blob = pair(util=[{"k": "cast", "t": 12, "by": 0, "code": "Dash", "name": "Dash"}])
    assert measures.util(rnd, blob, 0, 10, 30, None)["used"] == 1


def test_ult_cap_uses_other_round_and_requires_proof():
    rnd, blob = pair()
    blob["player_state"] = {"ult": {"0": [{"t": 0, "n": 7}]}}
    proof = {"player_state": {"ult": {"0": [{"t": 0, "n": 7}, {"t": 12, "n": 0}]}}}
    caps = measures.ult_caps({1: blob, 2: proof}, {0: "A"})
    assert caps == {0: 7}
    assert measures.util(rnd, blob, 0, 10, None, caps[0])["ult_ready"] is True
    assert measures.ult_caps({1: blob}, {0: "A"}).get(0) is None
    assert measures.util(rnd, blob, 0, 10, None, None)["ult_ready"] is None
    assert measures.util(rnd, blob, 0, 10, None, 8)["ult_ready"] is False


TIMED_ULT_USES = [
    ("Reyna", {"k": "equip", "code": "Vampire", "name": "X_Frenzy"}),
    ("Reyna", {"k": "heal", "code": "Vampire", "name": "X_Frenzy", "amount": 10}),
    ("Chamber", {"k": "shot", "gun": "Tour de Force"}),
    ("Jett", {"k": "damage", "target": 7, "name": "X_Dagger_Production", "from": "equippable"}),
    ("Neon", {"k": "hold", "name": "Overdrive"}),
    ("Veto", {"k": "buff", "code": "Pine", "name": "X_Evolution", "t1": 40}),
    ("Veto", {"k": "heal", "code": "Pine", "name": "X_SelfBuffVers_Engineering", "amount": 10}),
]


@pytest.mark.parametrize("agent, evidence", TIMED_ULT_USES)
@pytest.mark.parametrize("used_at, expected", [(26.884, False), (35.9, False), (36, True), (None, True)])
def test_timed_ult_readiness_uses_activation_evidence(agent, evidence, used_at, expected):
    rows = [] if used_at is None else [dict(evidence, t=used_at, by=0)]
    rnd, blob = pair(util=rows)
    blob["player_state"] = {"ult": {"0": [{"t": 0, "n": 7}, {"t": 39, "n": 7}]}}
    assert measures.util(rnd, blob, 0, 35.9, None, 7, agent=agent)["ult_ready"] is expected


@pytest.mark.parametrize("agent", ["Sage", None])
def test_non_timed_or_unspecified_agent_keeps_points_readiness(agent):
    rnd, blob = pair(util=[{"k": "equip", "t": 5, "by": 0, "name": "X_Frenzy"}])
    blob["player_state"] = {"ult": {"0": [{"t": 0, "n": 7}]}}
    assert measures.util(rnd, blob, 0, 10, None, 7, agent=agent)["ult_ready"] is True


@pytest.mark.parametrize("cap, points, expected", [(None, 7, None), (7, None, None), (8, 7, False)])
def test_unused_timed_ult_keeps_missing_or_insufficient_points(cap, points, expected):
    rnd, blob = pair()
    if points is not None:
        blob["player_state"] = {"ult": {"0": [{"t": 0, "n": points}]}}
    assert measures.util(rnd, blob, 0, 10, None, cap, agent="Reyna")["ult_ready"] is expected


@pytest.mark.parametrize("cap, points", [(None, 7), (7, None), (7, 7)])
def test_proven_timed_ult_use_overrides_missing_cap_or_points(cap, points):
    rnd, blob = pair(util=[{"k": "equip", "t": 0, "by": 0, "name": "X_Frenzy"}])
    if points is not None:
        blob["player_state"] = {"ult": {"0": [{"t": 0, "n": points}]}}
    assert measures.util(rnd, blob, 0, 10, None, cap, agent="Reyna")["ult_ready"] is False


def test_other_slots_timed_ult_use_does_not_consume_ours():
    rnd, blob = pair(util=[{"k": "equip", "t": 5, "by": 7, "name": "X_Frenzy"}])
    blob["player_state"] = {"ult": {"0": [{"t": 0, "n": 7}]}}
    assert measures.util(rnd, blob, 0, 10, None, 7, agent="Reyna")["ult_ready"] is True


def test_review_passes_agent_for_already_active_ult(monkeypatch, tmp_path):
    import clutch_review as review

    rnd, blob = pair(util=[{"k": "equip", "t": 26.884, "by": 0, "name": "X_Frenzy"}])
    blob["player_state"] = {"ult": {"0": [{"t": 0, "n": 7}]}}
    ctx = {"players": {str(s): {"side": group, "agent": "Reyna" if s == 0 else "Sage"}
                       for s, group in rnd.team.items()},
           "match": {"side_to_team": {"A": "team-1", "B": "team-2"},
                     "asset": "Toy", "match_id": 0},
           "rounds": {"1": {"db": {"plant": None}}}}
    task = {"uuid": "00000000-0000-0000-0000-000000000000", "cache": str(tmp_path),
            "geometry_cache": str(tmp_path), "missing_control": [1], "labels": {0: "0"}}
    monkeypatch.setitem(review._GEOMETRIES, (task["geometry_cache"], blob["map"]), rnd.geo)
    monkeypatch.setattr(measures, "situations", lambda *a: ([window(35.9, 40)], []))
    monkeypatch.setattr(measures, "clock", lambda *a: {})
    monkeypatch.setattr(measures, "state", lambda *a: {})
    monkeypatch.setattr(measures, "fakes", lambda *a: {})
    rows, _, status, _ = review._measure_round(task, ctx, 1, blob, {0: 7})
    assert status == "measured"
    assert rows[0]["util"]["ult_ready"] is False


def test_import_is_db_free_in_fresh_process():
    script = "import sys; sys.path.insert(0, 'scripts'); import clutch_measures; " \
             "assert not {'app.db', 'app.models', 'app.scoring.plant_window', 'preview_control_live'} & set(sys.modules)"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
