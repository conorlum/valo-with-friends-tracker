"""Back-shots (timing-gaps spec, section 6) on toy rounds through the real engine, and the linking and `shot`
level rules on hand-built gaps."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.gaps import backshots as bs
from app.gaps import detect as gd
from app.gaps.rows import to_rows
from tests.replays.control_toys import blob, open_hall, track
from tests.replays.test_gaps_detect import run


def _dmg(t, by=5, target=0, wall=False):
    return {"k": "damage", "t": t, "t1": t + 0.1, "by": by, "target": target, "src": "gun", "wall": wall, "n": 1}


def _behind_round(util, deaths=None, kills=None, t_end=12.0, b_points=None, t_decided=None):
    data = blob({0: ("A", [(0.0, 150, 200, 180)]), 5: ("B", b_points or [(0.0, 400, 200, 180)])}, t_end=t_end,
                util=util, deaths=deaths, t_decided=t_decided)
    data["kills"] = kills or []
    return data


def backshots(gaps):
    return [g for g in gaps if g.kind == "backshot"]


def test_damage_from_behind_by_an_unlocated_enemy_is_a_backshot():
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0)])))
    assert b.victim == 0 and b.candidates.keys() == {5} and b.t_open == pytest.approx(8.0)
    assert b.angle_deg > gd.BEHIND_DEG and b.route and b.choke_seq == ()
    assert b.t_last_exposed is None and b.t_close is None


def test_the_shooters_own_gunfire_does_not_cancel_the_backshot():
    shot = {"k": "shot", "t": 7.8, "by": 5, "u": 3900, "v": 1950, "gun": None}
    assert len(backshots(run(open_hall(), _behind_round([shot, _dmg(8.0)])))) == 1


def test_earlier_gunfire_does_cancel_it():
    """Gunfire heard 1 s before the run is outside SHOT_LOOKBACK_S: it located the shooter."""
    shot = {"k": "shot", "t": 7.0, "by": 5, "u": 3900, "v": 1950, "gun": None}
    assert backshots(run(open_hall(), _behind_round([shot, _dmg(8.0)]))) == []


def test_follow_up_damage_within_5s_is_the_same_backshot():
    assert len(backshots(run(open_hall(), _behind_round([_dmg(8.0), _dmg(9.0)])))) == 1


def test_damage_lost_for_5s_again_is_a_new_backshot():
    assert len(backshots(run(open_hall(), _behind_round([_dmg(2.0), _dmg(7.5)])))) == 2


def test_damage_from_in_front_is_not_a_backshot():
    data = blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, util=[_dmg(8.0)])
    assert backshots(run(open_hall(), data)) == []


def test_ability_damage_is_not_a_backshot():
    hit = dict(_dmg(8.0), src="ability")
    assert backshots(run(open_hall(), _behind_round([hit]))) == []


def test_a_sighting_less_than_5s_before_cancels_it_and_one_5s_before_does_not():
    # A faces east (sees B) from 3 s to 4 s, then west again.
    pts = [(0.0, 150, 200, 180), (3.0, 150, 200, 0), (4.0, 150, 200, 180)]
    data = blob({0: ("A", pts), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, util=[_dmg(8.0)])
    assert backshots(run(open_hall(), data)) == []
    data = blob({0: ("A", pts), 5: ("B", [(0.0, 400, 200, 180)])}, t_end=12.0, util=[_dmg(10.0)])
    [b] = backshots(run(open_hall(), data))
    # the path starts where the team last located B (the sighting), not at the round start
    assert 3.0 <= b.route[0][0][0] < 4.5


def test_a_wallbang_counts_and_is_tagged():
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0, wall=True)])))
    assert b.context["wall"] is True


def test_nothing_after_the_round_is_decided():
    assert backshots(run(open_hall(), _behind_round([_dmg(9.0)], t_decided=8.0))) == []


def test_missing_positions_record_nothing_and_are_counted():
    data = _behind_round([_dmg(8.0)])
    data["tracks"]["5"] = track([(0.0, 400, 200, 180)], 0.0, 7.0)      # B has no sample at 8 s
    dets = []
    assert backshots(run(open_hall(), data, detector=dets)) == []
    assert dets[0].notes[bs.NO_POSITION] == 1


def test_a_backshot_links_to_the_open_gap_and_marks_it_shot_and_killed():
    kills = [{"i": 0, "t": 8.5, "killer": 5, "victim": 0, "u": 0, "v": 0}]
    gaps = run(open_hall(), _behind_round([_dmg(8.0)], deaths={0: 8.5}, kills=kills))
    [b] = backshots(gaps)
    [g] = [g for g in gaps if g.kind == "predicted" and g.victim == 0]
    assert b.linked is g
    assert g.shot_by == 5 and g.shot_at == pytest.approx(8.0)
    assert g.killed_by == 5 and g.killed_at == pytest.approx(8.5)
    assert b.killed_by == 5 and b.killed_at == pytest.approx(8.5)
    assert b.shot_at is None and b.stood_at is None, "a back-shot fills only killed_*"


def test_a_kill_after_the_result_window_is_not_the_backshots():
    kills = [{"i": 0, "t": 11.5, "killer": 5, "victim": 0, "u": 0, "v": 0}]
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0)], deaths={0: 11.5}, kills=kills)))
    assert b.killed_at is None and b.killed_by is None


# ---------------------------------------------------------------- R16: peak speed and the two distances


def test_an_early_sprint_is_the_peak():
    """B runs at about 4 m/s (under AUDIBLE_MPS, so unheard) from 0 to 2 s, then stands; the back-shot at 8 s
    keeps the path from the round start, so the peak is the early run, not the still last 5 s."""
    b_points = [(0.0, 400, 200, 180), (2.0, 343, 200, 180)]
    [b] = backshots(run(open_hall(), _behind_round([_dmg(8.0)], b_points=b_points)))
    assert b.route[0][0][0] == pytest.approx(0.0)
    assert b.context["peak_speed_mps"] == pytest.approx(57 * 0.14 / 2, abs=0.25)


def test_the_candidate_distance_and_distance_m_differ():
    geo = open_hall()
    dets = []
    [b] = backshots(run(geo, _behind_round([_dmg(8.0)], b_points=[(0.0, 401, 203, 180)]), detector=dets))
    rnd = dets[0].rnd
    sx, sy = geo.centres[b.spot]
    bx, by_, _ = rnd.pos(5, 8.0)
    vx, vy, _ = rnd.pos(0, 8.0)
    assert b.candidates[5] == pytest.approx(dets[0]._metres(bx, by_, sx, sy), abs=0.01)
    assert b.distance_m == pytest.approx(dets[0]._metres(vx, vy, sx, sy), abs=0.01)
    assert b.candidates[5] < 2.0 < b.distance_m


# ---------------------------------------------------------------- breaks in the path


def test_a_break_in_the_path_keeps_two_pieces_and_no_choke_sequence():
    data = _behind_round([_dmg(8.0)])
    data["tracks"]["5"] = track([(0.0, 400, 200, 180)], 0.0, 3.0) + track([(0.0, 400, 200, 180)], 4.0, 12.0)
    dets = []
    gaps = run(open_hall(), data, detector=dets)
    [b] = backshots(gaps)
    assert len(b.route) == 2 and b.route[0][-1][0] < 3.5 <= b.route[1][0][0]
    assert b.choke_seq is None
    assert dets[0].notes[bs.BROKEN_PATH] == 1
    # a null sequence matches no gap's, so it links by the nearest spot
    [g] = [g for g in gaps if g.kind == "predicted" and g.victim == 0]
    assert b.linked is g


def test_choke_path_collapses_repeats_and_skips_non_choke_nodes():
    node_choke = np.array([-1, 3, 3, -1, 7, 3, -1], np.int32)
    assert bs.choke_path([0, 1, 2, 3, 3, 4, 4, 5, 6], node_choke) == (3, 7, 3)
    assert bs.choke_path([0, 3, 6], node_choke) == ()


# ---------------------------------------------------------------- linking and `shot`, on hand-built gaps


GEO = SimpleNamespace(centres=np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]]), m_per_px=0.1)


def _gap(spot, seq, t_open=0.0, t_close=10.0, joined=None, stood=None, victim=0, life=0):
    g = gd.Gap("predicted", victim, "A", 0, seq, t_open, spot, 0, 1.0, 150.0, [[]], life=life, t_close=t_close)
    g.joined = dict(joined if joined is not None else {5: t_open})
    g.candidates = {e: 1.0 for e in g.joined}
    for e, ts in (stood or {}).items():
        g.stood_times[e] = list(ts)
    return g


def test_linking_prefers_the_same_choke_sequence_then_the_nearest_spot():
    near, same = _gap(0, (1,)), _gap(2, (2,))
    assert bs.link(GEO, [near, same], 0, 0, 5, 3.0, (2,), 10.0, 0.0) is same
    assert bs.link(GEO, [near, same], 0, 0, 5, 3.0, (9,), 10.0, 0.0) is near
    assert bs.link(GEO, [near, same], 0, 0, 5, 3.0, None, 190.0, 0.0) is same


def test_linking_needs_the_shooter_on_the_list_by_then_and_the_gap_open():
    late = _gap(0, (), joined={5: 4.0})
    assert bs.link(GEO, [late], 0, 0, 5, 3.0, (), 0.0, 0.0) is None, "B joined after the shot"
    assert bs.link(GEO, [_gap(0, (), joined={6: 0.0})], 0, 0, 5, 3.0, (), 0.0, 0.0) is None, "not a candidate"
    assert bs.link(GEO, [_gap(0, (), t_close=2.0)], 0, 0, 5, 3.0, (), 0.0, 0.0) is None, "closed before"
    assert bs.link(GEO, [_gap(0, (), victim=1)], 0, 0, 5, 3.0, (), 0.0, 0.0) is None, "another victim"


def _shot(t, shooter=5, victim=0, life=0):
    return gd.Gap("backshot", victim, "A", -1, (), t, 0, 0, 1.0, 150.0, [[]], life=life, candidates={shooter: 0.5})


def test_shot_while_open_or_within_the_window_of_a_stand():
    open_gap = _gap(0, (), t_close=10.0)
    after_stand = _gap(0, (), t_close=5.0, stood={5: [4.0]})
    too_late = _gap(0, (), t_close=5.0, stood={5: [1.0]})
    joined_later = _gap(0, (), t_open=0.0, t_close=10.0, joined={5: 7.0})
    other_life = _gap(0, (), t_close=10.0, life=1)
    bs.mark_shots([open_gap, after_stand, too_late, joined_later, other_life], [_shot(6.5), _shot(9.0)])
    assert (open_gap.shot_at, open_gap.shot_by) == (6.5, 5), "the first occurrence only"
    assert after_stand.shot_at == 6.5
    assert too_late.shot_at is None
    assert joined_later.shot_at == 9.0, "the 6.5 s shot came before B joined"
    assert other_life.shot_at is None


def _levels(gaps, kills):
    det = SimpleNamespace(gaps=gaps, rnd=SimpleNamespace(kills=kills))
    gd.GapDetector._levels(det)


def test_a_candidates_kill_while_the_gap_is_open_is_killed_without_a_stand_or_shot():
    """Ruling D6: `killed` uses `shot`'s window, so an open gap is enough; the anchored reading (3 s after a
    stand or shot) would miss this kill."""
    g = _gap(0, (), t_open=0.0, t_close=8.0)
    _levels([g], [(8.0, 5, 0)])
    assert (g.killed_at, g.killed_by) == (8.0, 5)


def test_killed_after_the_close_needs_a_stand_and_a_shot_is_no_anchor():
    after_stand = _gap(0, (), t_close=5.0, stood={5: [4.5]})
    shot_only = _gap(0, (), t_close=5.0)
    shot_only.shot_at, shot_only.shot_by = 6.0, 5
    _levels([after_stand, shot_only], [(7.0, 5, 0)])
    assert after_stand.killed_at == 7.0
    assert shot_only.killed_at is None


def test_levels_need_the_enemy_on_the_list_by_the_kill():
    g = _gap(0, (), t_open=0.0, t_close=10.0, joined={5: 6.0})
    _levels([g], [(4.0, 5, 0), (4.5, 0, 5)])
    assert g.killed_at is None and g.victim_won_at is None


def test_the_victim_winning_while_the_gap_is_open():
    g = _gap(0, (), t_open=0.0, t_close=10.0, joined={5: 0.0, 6: 0.0})
    _levels([g], [(3.0, 0, 7), (4.0, 0, 6), (5.0, 0, 5)])
    assert g.victim_won_at == 4.0, "the first kill of a candidate (7 is not one)"


def test_two_runs_starting_together_are_one_backshot():
    assert len(backshots(run(open_hall(), _behind_round([_dmg(8.0), _dmg(8.0)])))) == 1


def test_backshot_context_has_the_predicted_rows_keys():
    gaps = run(open_hall(), _behind_round([_dmg(8.0)]))
    [b] = backshots(gaps)
    [g] = [g for g in gaps if g.kind == "predicted" and g.victim == 0]
    assert b.context["victim_sees_enemy"] is None
    assert set(b.context) == set(g.context) | {"wall", "peak_speed_mps"}


def test_backshot_rows_are_json_safe():
    kills = [{"i": 0, "t": 8.5, "killer": 5, "victim": 0, "u": 0, "v": 0}]
    dets = []
    gaps = run(open_hall(), _behind_round([_dmg(8.0, wall=True)], deaths={0: 8.5}, kills=kills), detector=dets)
    rows = to_rows(gaps, dets[0].rnd, dets[0].geo)
    json.dumps(rows)
    [row] = [r for r in rows if r["kind"] == "backshot"]
    assert row["linked_seq"] is not None and rows[row["linked_seq"]]["kind"] == "predicted"
    assert row["choke_seq"] == [] and row["candidate_slots"] == [5] and row["qualified_s"] is None
    assert type(row["context"]["peak_speed_mps"]) in (float, type(None)) and type(row["distance_m"]) is float
    assert all(type(x) in (float, int) for piece in row["route"] for pt in piece for x in pt)
