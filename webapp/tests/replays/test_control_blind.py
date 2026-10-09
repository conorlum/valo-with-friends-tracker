"""Zero personal control while blinded (docs/superpowers/plans/2026-10-06-replay-player-state-impl.md, P07 and its
amendments: "Exact flash and nearsight intervals", "Blinded control"; the design's section 2).

A confirmed blind is `[t, t + dur)` on the replay clock, unsnapped; a zero-length hit is nothing. While blinded a
player keeps their body (alive, positioned, seen by the enemy, their own cell evidence against the unknown) and
nothing personal: no sight, presence, memory, backfill, coverage, control, Q63 credit or space taken. Their trip,
alarmbot and turret keep watching for the team, credited to nobody; a camera or drone they operate shows nothing.
The expected areas and durations here are worked out by hand, not read back from the engine."""

import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import engine as ce
from app.gaps import cache
from app.gaps import detect as gd
from app.gaps.rows import to_rows
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability, uv

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import control_reference as ref  # noqa: E402

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def flash(target, t, dur, by=5):
    return {"k": "flash", "t": t - 0.3, "by": by, "ability": "phoenix_curveball_left", "targets": [target],
            "hits": [[target, t, dur]]}


def ticks(rnd):
    """Every analytical instant of the round, stepped as compute_round steps them (unknown, then memory)."""
    runner = ce.TickRunner(rnd.geo)
    for t in rnd.analytic_times(rnd.tick_times(), own=True):
        yield runner.step(ce.Tick(rnd, float(t)))


# ---------------------------------------------------------------- the interval


def test_a_blind_is_its_exact_interval_and_its_ends_are_analytic_instants():
    geo = open_hall()
    data = blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}, t_end=8.0,
                util=[flash(0, 1.626, 0.5), {"k": "nearsight", "t": 4.0, "by": 5, "ability": "omen_paranoia",
                                             "targets": [0], "hits": [[0, 4.2, None]]}])
    rnd = ce.RoundInputs(data, geo)
    assert rnd.flashed[0] == [(1.626, 1.626 + 0.5)]
    assert rnd.nearsight[0] == [(4.2, 4.2 + ce.NEARSIGHT_DEFAULT_S)]       # the null duration's placeholder, exact
    for t in (1.626, 1.626 + 0.5, 4.2, 4.2 + ce.NEARSIGHT_DEFAULT_S):
        assert t in rnd.transitions and round(t, 6) in {round(float(x), 6) for x in rnd.analytic_times(
            rnd.tick_times(), own=True)}
    assert not ce.Tick(rnd, 1.625).holders[0].blind, "a hit at x.626 is not blind at x.625"
    assert ce.Tick(rnd, 1.626).holders[0].blind
    assert ce.Tick(rnd, 2.1259).holders[0].blind and not ce.Tick(rnd, 2.126).holders[0].blind
    near = ce.Tick(rnd, 4.5).holders[0]
    assert not near.blind and near.body.any(), "nearsight keeps its bubble and is not a blind"


def test_zero_and_negative_hits_blind_nobody():
    geo = open_hall()
    data = blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}, t_end=4.0,
                util=[flash(0, 1.0, 0.0), flash(0, 2.0, -1.0)])
    rnd = ce.RoundInputs(data, geo)
    assert rnd.flashed[0] == [] and rnd.transitions == []
    assert not ce.Tick(rnd, 1.0).holders[0].blind


def test_a_zero_duration_hit_changes_nothing():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 1: still("A", 150, 280, 270), 5: still("B", 400, 200, 180)}
    plain = blob(players, t_end=4.0)
    hit = blob(players, t_end=4.0, util=[flash(0, 1.0, 0.0)])
    a, b = ce.compute_round(plain, geo), ce.compute_round(hit, geo)
    assert np.array_equal(a.ticks, b.ticks) and np.array_equal(a.states, b.states)
    assert np.array_equal(a.control, b.control, equal_nan=True)
    assert {s: p.as_dict() for s, p in a.players.items()} == {s: p.as_dict() for s, p in b.players.items()}


def test_a_flash_ends_with_the_life_it_started_in():
    """A revived player doesn't keep an earlier life's flash (the shared normaliser's rule)."""
    geo = open_hall()
    data = blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}, t_end=8.0, util=[flash(0, 1.0, 4.0)])
    data["alive"]["0"] = [[0.0, 2.0, "kill"], [3.0, None, None]]
    rnd = ce.RoundInputs(data, geo)
    assert rnd.flashed[0] == [(1.0, 2.0)]
    assert not ce.Tick(rnd, 3.5).holders[0].blind


# ---------------------------------------------------------------- what a blinded player holds


def _team_round(blind: bool):
    """A0 looks east with a trip across the hall and a camera they sit in from 1.0 to 2.0; A1 looks north; B5 faces
    the wall in the far corner. A0 is blinded from 1.03 to 2.47 (between published frames)."""
    geo = open_hall()
    util = [toy_ability("Gumshoe", "4_TripWire", 300, 120, 0, kind="GameObject", end=list(uv(300, 280))),
            toy_ability("Gumshoe", "E_PossessableCamera", 350, 150, 0, kind="Pawn", yaw=180, possessed=[[1.0, 2.0]])]
    if blind:
        util.append(flash(0, 1.03, 1.44))
    data = blob({0: still("A", 150, 200, 0), 1: still("A", 150, 280, 270), 5: still("B", 400, 110, 0)},
                t_end=4.0, util=util)
    return geo, ce.RoundInputs(data, geo)


def _at(rnd, t):
    for tk in ticks(rnd):
        if abs(tk.t - t) < 1e-9:
            return tk
    raise AssertionError(f"no instant at {t}")


def test_a_blinded_player_holds_nothing_personal_but_keeps_their_body():
    geo, rnd = _team_round(True)
    tk = _at(rnd, 1.5)
    h = tk.holders[0]
    assert h.blind and h.cell == ce.Tick(rnd, 0.5).holders[0].cell, "in place"
    assert not h.active.any() and not h.passive.any(), "no sight, presence or memory"
    assert h.memory is None or not h.memory.any()
    assert not tk.view[0].any()
    cov = tk.coverage()
    assert cov[0][0] == 0.0 and cov[0][1] == 0.0 and not cov[0][2].any() and not cov[0][3].any()
    assert tk.live[0][h.cell], "their own cell is still evidence"
    _, plain = _team_round(False)
    assert np.array_equal(_at(plain, 1.5).holders[1].active, tk.holders[1].active), "the teammate is untouched"
    assert cov[1][0] > 0


def test_their_trip_keeps_watching_for_the_team_and_their_camera_shows_nothing():
    geo, rnd = _team_round(True)
    _, plain = _team_round(False)
    trip = ce.Tick(rnd, 0.5).holders[0].watch                    # before the camera is in use: the trip alone
    cell = geo.cell_of_px(300, 200)
    assert trip[cell]
    tk = _at(rnd, 1.5)
    assert np.array_equal(tk.holders[0].watch, trip), "the trip watches; the operated camera doesn't"
    assert (_at(plain, 1.5).holders[0].watch & ~trip).any(), "unblinded, the camera shows ground"
    assert int(tk.compose()["state"][cell]) in A_OWN, "the team still holds the trip's line"
    assert not tk.coverage()[0][3][cell], "credited to nobody"


def test_removing_a_blinded_player_changes_nothing_in_either_counterfactual():
    _, rnd = _team_round(True)
    tk = _at(rnd, 1.5)
    base = tk.compose()
    assert np.array_equal(tk.compose(removed=0)["state"], base["state"])
    assert np.array_equal(tk.compose(removed=0, base=base, full=False)["state"], base["state"])
    assert not np.array_equal(tk.compose(removed=1)["state"], base["state"]), "a seeing teammate still counts"


def test_recovery_at_the_exact_end_sees_again():
    _, rnd = _team_round(True)
    end = 1.03 + 1.44
    assert ce.Tick(rnd, end - 1e-6).holders[0].blind
    back = ce.Tick(rnd, end).holders[0]
    assert not back.blind and back.active.any()


# ---------------------------------------------------------------- memory and the unknown


def _close_round(blind: bool):
    """A0 alone on A, facing east at B5 5.6 m away (who faces A0). A0 is blinded 1.03 to 4.03."""
    geo = open_hall()
    data = blob({0: still("A", 150, 200, 0), 5: still("B", 190, 200, 180)}, t_end=6.0,
                util=[flash(0, 1.03, 3.0)] if blind else [])
    return geo, ce.RoundInputs(data, geo)


def test_memory_eaten_while_blind_is_not_reinstalled_and_the_own_cell_stays_evidence():
    geo, rnd = _close_round(True)
    before = None
    eaten = np.zeros(geo.n, bool)
    after = None
    for tk in ticks(rnd):
        h = tk.holders[0]
        if tk.t < 1.03:
            before = h.memory.copy() if h.memory is not None else None
            seen_before = (h.active | h.passive) & geo.walk_n
        elif tk.t < 1.03 + 3.0:
            assert h.blind and not h.passive.any() and (h.memory is None or not h.memory.any())
            assert not tk.unknown["A"][h.cell], "the blinded player's own cell is no enemy's"
            eaten |= tk.unknown["A"]
        elif after is None:
            after = h
            eaten |= tk.unknown["A"]
    remembered = seen_before if before is None else (before | seen_before)
    assert (remembered & eaten).any(), "the unknown reached remembered ground during the blind"
    neighbours = ce.topology.of(geo).dilate(np.eye(1, geo.n, after.cell, dtype=bool).ravel(), eight=True)
    assert (eaten & neighbours).any(), "the unknown came right up to the blinded player"
    assert after.memory is not None and not (after.memory & eaten).any(), "never the pre-blind snapshot"
    assert not after.blind and after.active.any()


def test_a_blinded_body_is_still_seen_by_the_enemy():
    _, rnd = _close_round(True)
    tk = _at(rnd, 2.0)
    assert tk.holders[0].blind and 0 in tk.sees[5]
    assert 5 not in tk.sees[0]


# ---------------------------------------------------------------- the round's numbers


def _sole_round(blind_from: float | None):
    """A0 alone on A in the door hall's west room, facing south; B5 east of the wall."""
    geo = door_hall()
    util = [flash(0, blind_from, 10.0)] if blind_from is not None else []
    data = blob({0: still("A", 150, 200, 90), 5: still("B", 400, 200, 180)}, t_end=8.0, util=util)
    return geo, data


def test_a_blinded_sole_survivor_keeps_alive_time_and_gets_zero_from_the_exact_start():
    start = 6.03                                   # between the published frames 6.0 and 6.0625
    geo, data = _sole_round(start)
    _, plain = _sole_round(None)
    x, y = ce.compute_round(data, geo), ce.compute_round(plain, geo)
    assert x.players[0].alive_s == pytest.approx(8.0), "alive from 0 to the round's end, blind or not"
    assert y.players[0].alive_s == pytest.approx(8.0)
    late = x.ticks >= start
    assert late.any() and np.all(x.control[late, 0] == 0.0)
    assert not x.control_masks[late, 0].any() and not x.coverage_masks[late, 0].any()
    assert np.all(y.control[y.ticks >= 6.0625, 0] > 0), "unblinded, the last player holds what the team holds (Q63)"
    # by hand: the unblinded control on every step before 6.0, then the 6.0 frame's for 0.03 s, then nothing
    early = y.ticks < 6.0
    expected = float((y.control[early, 0].astype(float) * y.weights[early]).sum())
    expected += float(y.control[list(y.ticks).index(6.0), 0]) * (start - 6.0)
    assert x.players[0].control_m2s == pytest.approx(expected, rel=1e-5)
    assert y.players[0].control_m2s > expected
    assert x.players[0].taken_m2 <= y.players[0].taken_m2
    # the team still holds ground with nobody credited for it
    assert np.isin(x.states[late], A_OWN).any() and x.redundant_m2s["A"] > y.redundant_m2s["A"]


def test_no_locate_or_death_event_comes_from_a_blind():
    geo, data = _sole_round(2.03)
    _, plain = _sole_round(None)
    x, y = ce.compute_round(data, geo), ce.compute_round(plain, geo)
    for side in ("A", "B"):
        kinds = lambda rc: {(r[1], r[2], r[3]) for r in rc.reasons[side]}   # noqa: E731
        assert kinds(x) == kinds(y)
    assert x.players[0].deaths == [] and y.players[0].deaths == []


def test_dying_while_blinded_charges_no_loss():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 1: still("A", 150, 280, 270), 5: still("B", 400, 200, 180)}
    hurt = {"k": "damage", "t": 2.0, "t1": 2.1, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 2}
    x = ce.compute_round(blob(players, t_end=5.0, util=[flash(0, 1.03, 3.0), hurt], deaths={0: 2.2}), geo)
    y = ce.compute_round(blob(players, t_end=5.0, util=[hurt], deaths={0: 2.2}), geo)
    assert x.players[0].alive_s == pytest.approx(2.2) and y.players[0].alive_s == pytest.approx(2.2)
    [dx], [dy] = x.players[0].deaths, y.players[0].deaths
    assert dx["control_m2"] == 0.0 and dy["control_m2"] > 0.0
    assert x.players[1].control_m2s > 0


def test_a_whole_team_blinded_is_credited_nothing_and_still_holds_ground():
    geo = door_hall()
    players = {0: still("A", 150, 200, 90), 1: still("A", 120, 150, 0), 5: still("B", 400, 200, 180)}
    trip = toy_ability("Gumshoe", "4_TripWire", 120, 250, 1, kind="GameObject", end=list(uv(190, 250)))
    rc = ce.compute_round(blob(players, t_end=5.0, util=[trip, flash(0, 2.03, 1.5), flash(1, 2.03, 1.5)]), geo)
    during = (rc.ticks >= 2.03) & (rc.ticks < 3.53)
    assert during.any()
    assert np.all(rc.control[during][:, [0, 1]] == 0.0) and not rc.coverage_masks[during][:, [0, 1]].any()
    line = np.flatnonzero(np.isin(rc.walk_cells, [geo.cell_of_px(x, 250) for x in range(130, 190, 8)]))
    assert len(line) and np.isin(rc.states[np.ix_(during, line)], A_OWN).all(), "the trip still holds its line"
    assert rc.players[0].alive_s == pytest.approx(5.0) and rc.players[1].alive_s == pytest.approx(5.0)


# ---------------------------------------------------------------- the gap detector


def test_cached_and_live_gaps_agree_on_a_blinded_round(tmp_path):
    assert cache.FORMAT == 1, "no gaps cache shape change for the blind rule"
    geo, rnd = _close_round(True)
    data = rnd.blob
    live = gd.GapDetector(geo, ce.RoundInputs(data, geo))
    path = cache.cache_path(1, 1, "blind", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.step(rec, unk.log)
        writer(rec, unk)

    rc = ce.compute_round(data, geo, observer=both, knowledge=False)
    writer.close(rc.missing_inputs)
    replayed = gd.GapDetector(geo, ce.RoundInputs(data, geo))
    for rec, logs in cache.replay(path):
        replayed.step(rec, logs)
    a, b = to_rows(live.finish(), live.rnd, geo), to_rows(replayed.finish(), replayed.rnd, geo)
    assert a == b


def test_a_round_without_a_blind_matches_its_reference_digest():
    """The unchanged no-blind fixture: every flat reference round but `midwall` has no flash or nearsight."""
    import json

    from tests.replays.control_toys import reference_rounds

    pinned = json.loads(ref.FIXTURE.read_text(encoding="utf-8"))["rounds"]
    geo, data, link = reference_rounds()["setups"]
    assert not any(e.get("k") in ("flash", "nearsight") for e in data["util"])
    assert ref.digest_round(ce.compute_round(data, geo, link), data) == pinned["setups"]
