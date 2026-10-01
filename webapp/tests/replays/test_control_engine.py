"""Stage 2 control rules (app/control/engine.py) on toy maps, one or more tests per rule in
docs/replay-map-control-plan.md's Stage 2 list. Positions are minimap pixels; cells are 8 px."""

import copy

import numpy as np
import pytest

from app.control import engine as ce
from app.control.geometry import GRID, Wall, cast
from app.services.replay_view import alive_steps
from tests.replays.control_toys import blob, door_hall, midwall_hall, open_hall, two_rooms, uv

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)
B_OWN = (ce.B_PASSIVE, ce.B_SAFE, ce.B_ACTIVE)
CONTESTED = (ce.CONTESTED, ce.CONTESTED_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def tick(geo, players, t=1.0, link=None, **kw):
    return ce.Tick(ce.RoundInputs(blob(players, **kw), geo, link), t)


def at(geo, state, x, y):
    return int(state[geo.cell_of_px(x, y)])


def ability(code, name, x, y, by, t=0.0, t1=10.0, kind="Zone", **extra):
    u, v = uv(x, y)
    return {"k": "ability", "t": t, "t1": t1, "by": by, "kind": kind, "code": code, "name": name, "u": u, "v": v,
            **extra}


# ---------------------------------------------------------------- vision


def test_movement_classes_and_their_cones():
    geo = open_hall()
    m_per_s = 1 / geo.m_per_px          # px per second at 1 m/s
    players = {0: ("A", [(0.0, 150, 200, 0)]),
               1: ("A", [(0.0, 150, 150, 0), (10.0, 150 + 3 * m_per_s * 10, 150, 0)]),     # 3 m/s: walk
               2: ("A", [(0.0, 120, 260, 0), (10.0, 120 + 6 * m_per_s * 10, 260, 0)]),     # 6 m/s: run
               5: still("B", 400, 110, 90)}
    tk = tick(geo, players, t=1.0)
    assert [tk.holders[s].mode for s in (0, 1, 2)] == ["hold", "walk", "run"]
    turning = {0: ("A", [(0.0, 150, 200, 0), (0.9, 150, 200, 0), (1.0, 150, 200, 60)]), 5: still("B", 400, 110, 90)}
    assert tick(geo, turning, t=1.0).holders[0].mode == "run"      # 240 deg/s: a fast turn
    cone = {mode: int(ce.sector(geo, 150.0, 200.0, 0.0, half).sum()) for mode, half in ce.CONE_HALF.items()}
    assert cone["hold"] > cone["walk"] > cone["run"] > 0


def test_hollow_and_solid_smokes():
    geo = open_hall()
    smoke = (250.0, 200.0, 30.0, False)
    outside = cast(geo, 150.0, 200.0, np.arange(-10, 10.01, 0.5), [smoke])
    c = lambda x, y: geo.cell_of_px(x, y)  # noqa: E731
    assert not outside[c(250, 200)] and not outside[c(350, 200)], "nobody sees into or through a smoke"
    inside = cast(geo, 250.0, 200.0, np.arange(-10, 10.01, 0.5), [smoke])
    assert inside[c(266, 200)] and not inside[c(350, 200)], "from inside a hollow smoke: out to its edge"
    solid = cast(geo, 250.0, 200.0, np.arange(-10, 10.01, 0.5), [(250.0, 200.0, 30.0, True)])
    assert not solid[c(266, 200)], "Brimstone's smokes are solid"
    # through the engine: a Wraith smoke between A and the far wall hides the far side
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    tk = tick(geo, players, util=[ability("Wraith", "4_Smoke", 250, 200, 5)])
    assert not tk.holders[0].raw[c(350, 200)]
    assert tick(geo, players).holders[0].raw[c(350, 200)]


def test_a_utility_wall_blocks_sight_where_it_crosses():
    geo = open_hall()
    c = lambda x, y: geo.cell_of_px(x, y)  # noqa: E731
    wall = Wall.from_points([(250.0, 120.0), (250.0, 240.0)])
    seen = cast(geo, 150.0, 200.0, np.arange(-10, 10.01, 0.5), [wall])
    assert seen[c(240, 200)] and not seen[c(262, 200)] and not seen[c(350, 200)]
    # beyond the wall's end, sight goes round it
    past_end = cast(geo, 150.0, 270.0, np.arange(-2, 2.01, 0.5), [wall])
    assert past_end[c(350, 270)]
    # the same wall in the pairwise check the fills use
    p, q = np.array([[150.0, 200.0]]), np.array([[350.0, 200.0], [350.0, 300.0], [200.0, 200.0]])   # 2nd: y 250 at the wall
    assert ce.smoke_blocks(p, q, wall).tolist() == [[True, False, False]]


def test_vipers_wall_blocks_only_while_it_is_up():
    geo = open_hall()
    c = lambda x, y: geo.cell_of_px(x, y)  # noqa: E731
    players = {0: still("A", 150, 200, 0), 4: still("A", 150, 120, 90), 5: still("B", 400, 110, 90)}
    points = [list(uv(250, 100)), list(uv(250, 292))]
    wall = ability("Pandemic", "E_SmokeScreenManager", 250, 120, 4, t=0.0, t1=10.0, kind="GameObject",
                   points=points, on=[[2.0, 5.0], [7.0, None]])
    seen = {t: tick(geo, players, t=t, util=[wall]).holders[0].raw[c(350, 200)] for t in (1.0, 3.0, 6.0, 8.0)}
    assert seen == {1.0: True, 3.0: False, 6.0: True, 8.0: False}
    assert {2.0, 5.0, 7.0} <= set(ce.RoundInputs(blob(players, util=[wall]), geo).tick_times().tolist())


# ---------------------------------------------------------------- safe space


def test_safe_space_behind_a_holder_at_the_door():
    geo = door_hall()
    players = {0: still("A", 204, 292, 0), 5: still("B", 400, 150, 180)}
    state = tick(geo, players).compose()["state"]
    assert at(geo, state, 120, 120) == ce.A_SAFE, "the west room is behind A's line"
    assert at(geo, state, 400, 150) in B_OWN + CONTESTED


def test_all_enemies_dead_makes_the_whole_map_the_teams():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 200, 180)}
    state = tick(geo, players, t=1.0, deaths={5: 0.5}).compose()["state"]
    walk = geo.walk.ravel()
    assert np.isin(state[walk], A_OWN).all()


def test_a_holders_death_loses_the_space_on_the_next_tick():
    geo = door_hall()
    players = {0: still("A", 204, 292, 0), 5: still("B", 400, 150, 180)}
    rc = ce.compute_round(blob(players, t_end=2.0, deaths={0: 1.2}), geo)
    times = rc.ticks.tolist()
    assert 1.1875 in times and 1.25 in times
    col = int(np.searchsorted(rc.walk_cells, geo.cell_of_px(120, 120)))
    assert rc.states[times.index(1.1875), col] == ce.A_SAFE
    assert rc.states[times.index(1.25), col] in B_OWN


def test_specials_link_a_fill_across_the_void():
    players = {0: still("A", 120, 120, 180), 5: still("B", 340, 140, 180)}
    plain = two_rooms()
    state = tick(plain, players).compose()["state"]
    assert at(plain, state, 160, 160) in A_OWN
    linked = two_rooms([{"kind": "teleporter", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": False}])
    state = tick(linked, players).compose()["state"]
    assert at(linked, state, 160, 160) not in A_OWN, "B can come through the teleporter"


# ---------------------------------------------------------------- contests


def test_the_entry_contests_what_they_see_and_their_way_back():
    geo = midwall_hall()
    players = {0: still("A", 150, 170, 0), 5: still("B", 220, 170, 180), 6: still("B", 380, 130, 90)}
    tk = tick(geo, players)
    base = tk.compose()
    state = base["state"]
    assert at(geo, state, 185, 170) in CONTESTED, "both claim the ground between them"
    entry = tk.holders[5]
    watched = base["cl"]["A"][2]
    assert watched[entry.cell], "the entry stands in A's vision"
    target = np.zeros((GRID, GRID), bool)
    for mask, _, slots in base["fills"]["B"].comps:
        if any(s != 5 for s in slots):
            target |= mask
    lane = tk.way_back(entry, target, watched) & geo.walk.ravel()
    assert lane.any()
    assert np.isin(state[lane], CONTESTED).all(), "the lane back to their team is held open"


def test_mutual_sight_contests_the_holders_lines():
    geo = open_hall()
    unseen = tick(geo, {0: still("A", 150, 200, 0), 5: still("B", 380, 200, 0)}).compose()["state"]
    seen = tick(geo, {0: still("A", 150, 200, 0), 5: still("B", 380, 200, 180)}).compose()["state"]
    held = lambda s: int(np.isin(s, (ce.A_ACTIVE, ce.A_PASSIVE)).sum())  # noqa: E731
    assert held(unseen) > 0
    assert held(seen) == 0, "a holder the enemy sees keeps the line, but it is fought over"


def test_both_teams_watching_a_lane_without_seeing_each_other():
    geo = midwall_hall()
    tk = tick(geo, {0: still("A", 200, 170, 60), 5: still("B", 310, 170, 120)})
    assert 0 not in tk.sees[5] and 5 not in tk.sees[0]
    state = tk.compose()["state"]
    assert at(geo, state, 256, 272) == ce.CONTESTED_ACTIVE, "both claim it, and a cone is on it"


def test_passive_against_passive_is_contested():
    geo = midwall_hall()
    # both look past the lane, so it's in both teams' passive vision only
    tk = tick(geo, {0: still("A", 200, 170, 20), 5: still("B", 310, 170, 160)})
    state = tk.compose()["state"]
    assert at(geo, state, 256, 272) == ce.CONTESTED


@pytest.mark.parametrize("by,flagged", [(5, True), (1, False)])
def test_an_enemy_molly_on_the_holder_contests_them(by, flagged):
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 1: still("A", 120, 120, 180), 5: still("B", 400, 110, 90)}
    tk = tick(geo, players, util=[ability("Phoenix", "MolotovFire", 150, 200, by)])
    assert tk.holders[0].flagged is flagged


def test_wallbang_hits_contest_for_two_seconds_and_team_damage_never():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 1: still("A", 120, 120, 180), 5: still("B", 400, 110, 90)}
    hits = [{"k": "damage", "t": 1.0, "t1": 1.0, "by": 5, "target": 0, "src": "gun", "wall": True, "n": 1},
            {"k": "damage", "t": 1.0, "t1": 1.0, "by": 5, "target": 1, "src": "gun", "wall": False, "n": 1},
            {"k": "damage", "t": 1.0, "t1": 1.0, "by": 1, "target": 0, "src": "gun", "wall": True, "n": 1}]
    rnd = ce.RoundInputs(blob(players, util=hits), geo)
    assert ce.Tick(rnd, 2.9).holders[0].flagged and not ce.Tick(rnd, 3.1).holders[0].flagged
    assert not ce.Tick(rnd, 1.5).holders[1].flagged, "a plain hit: the enemy's sight already counts"
    team = ce.RoundInputs(blob(players, util=hits[2:]), geo)
    assert not ce.Tick(team, 1.5).holders[0].flagged


# ---------------------------------------------------------------- statuses


@pytest.mark.parametrize("row", [
    {"k": "status", "t": 0.5, "t1": 2.0, "by": 5, "target": 0, "status": "concussed", "code": "X", "name": "Y"},
    {"k": "reveal", "t": 0.5, "t1": 2.0, "by": 5, "target": 0, "code": "X", "name": "Y"},
])
def test_concuss_and_reveal_downgrade_to_passive(row):
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    rnd = ce.RoundInputs(blob(players, util=[row]), geo)
    during, after = ce.Tick(rnd, 1.0).holders[0], ce.Tick(rnd, 2.5).holders[0]
    assert not during.active.any() and during.passive[during.body].all() and during.body.any()
    # the presence bubble stays while concussed or revealed (the user's call, 2026-10-01)
    assert during.passive[geo.cell_of_px(150 - round(1.5 / geo.m_per_px), 200)]
    assert after.active.any()


def test_nearsight_leaves_a_bubble():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    row = {"k": "nearsight", "t": 0.9, "by": 5, "ability": "omen_paranoia", "targets": [0], "hits": [[0, 1.0, 2.0]]}
    rnd = ce.RoundInputs(blob(players, util=[row]), geo)
    body = ce.Tick(rnd, 1.5).holders[0].body
    d = np.hypot(geo.centres[body, 0] - 150, geo.centres[body, 1] - 200) * geo.m_per_px
    assert body.any() and d.max() <= ce.NEARSIGHT_RADIUS_M + geo.cell_m
    assert ce.Tick(rnd, 3.5).holders[0].body.sum() > body.sum()


def test_flash_lasts_the_victims_recorded_duration():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    row = {"k": "flash", "t": 0.5, "by": 5, "ability": "phoenix_curveball_left", "targets": [0], "hits": [[0, 1.0, 0.8]]}
    rnd = ce.RoundInputs(blob(players, util=[row]), geo)
    assert not ce.Tick(rnd, 1.25).holders[0].body.any()
    assert ce.Tick(rnd, 1.9).holders[0].body.any()
    assert not rnd.missing.get("flash hit time and blind duration (placeholder used)")
    # an older blob: the cast time plus the fuse, and the agent's full blind
    old = {k: v for k, v in row.items() if k != "hits"}
    rnd = ce.RoundInputs(blob(players, util=[old]), geo)
    assert ce.Tick(rnd, 0.9).holders[0].body.any() and not ce.Tick(rnd, 2.4).holders[0].body.any()
    assert ce.Tick(rnd, 2.6).holders[0].body.any()
    assert rnd.missing["flash hit time and blind duration (placeholder used)"] == 1


def test_a_flashed_holders_cells_stay_covered_go_to_the_enemy_or_are_contested():
    geo = open_hall()
    # A0 looks east down the hall, A1 north across its middle; B (in the far corner, facing the wall) sees little
    players = {0: still("A", 150, 200, 0), 1: still("A", 150, 280, 270), 5: still("B", 400, 110, 0)}
    row = {"k": "flash", "t": 0.9, "by": 5, "ability": "phoenix_curveball_left", "targets": [0], "hits": [[0, 1.0, 1.0]]}
    rnd = ce.RoundInputs(blob(players, util=[row]), geo)
    pre_t, post_t = ce.Tick(rnd, 0.5), ce.Tick(rnd, 1.5)
    pre, post = pre_t.compose()["state"], post_t.compose()["state"]
    walk = geo.walk.ravel()
    a0, a1, b = pre_t.holders[0], pre_t.holders[1], post_t.holders[5]
    shared = walk & a0.raw & a1.raw & np.isin(pre, A_OWN)
    assert shared.any() and np.isin(post[shared], A_OWN).all(), "a teammate still covers them: no change"
    only_a0 = walk & a0.raw & ~a1.raw & ~b.raw & np.isin(pre, A_OWN)
    # still behind A's lines (no enemy free space sees it), fought over, or the enemy's
    assert only_a0.any() and np.isin(post[only_a0], CONTESTED + B_OWN + (ce.A_SAFE,)).all()
    assert not np.isin(post[only_a0], (ce.A_ACTIVE, ce.A_PASSIVE)).any(), "nobody on A watches them now"
    assert np.isin(post[only_a0], CONTESTED).any(), "the enemy used utility to take that space"
    to_enemy = walk & a0.raw & ~a1.raw & b.raw & np.isin(pre, CONTESTED)
    assert to_enemy.any() and np.isin(post[to_enemy], B_OWN).all(), "lost cells the enemy watches are theirs"


# ---------------------------------------------------------------- watchers


def test_a_trip_watches_its_line_until_its_owner_dies():
    geo = open_hall()
    trip = ability("Gumshoe", "4_TripWire", 300, 120, 0, kind="GameObject", end=list(uv(300, 280)))
    players = {0: still("A", 150, 200, 180), 1: still("A", 110, 110, 180), 5: still("B", 400, 200, 0)}
    rnd = ce.RoundInputs(blob(players, util=[trip], deaths={0: 1.5}), geo)
    before = ce.Tick(rnd, 1.0)
    cell = geo.cell_of_px(300, 200)
    assert before.holders[0].watch[cell] and at(geo, before.compose()["state"], 300, 200) in A_OWN
    after = ce.Tick(rnd, 2.0)
    assert not any(h.watch[cell] for h in after.holders.values())
    assert at(geo, after.compose()["state"], 300, 200) not in A_OWN, "placed utility dies with its owner"


def test_the_turret_watches_a_cone_along_its_yaw():
    geo = open_hall()
    turret = ability("Killjoy", "E_Turret", 250, 200, 0, kind="Pawn", yaw=0, yaws=[[0.0, 0], [2.0, 180]])
    players = {0: still("A", 120, 120, 180), 5: still("B", 400, 110, 90)}
    rnd = ce.RoundInputs(blob(players, util=[turret]), geo)
    ahead, behind = geo.cell_of_px(330, 200), geo.cell_of_px(170, 200)
    first, turned = ce.Tick(rnd, 1.0).holders[0].watch, ce.Tick(rnd, 3.0).holders[0].watch
    assert first[ahead] and not first[behind]
    assert turned[behind] and not turned[ahead]
    side = geo.cell_of_px(250, 120)   # 90 degrees off: outside the 100-degree cone
    assert not first[side]


def test_the_camera_replaces_its_owners_view_while_he_is_in_it():
    geo = open_hall()
    camera = ability("Gumshoe", "E_PossessableCamera", 350, 150, 0, kind="Pawn", yaw=90,
                     possessed=[[1.0, 2.0]], yaws=[[0.0, 90]])
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 280, 180)}
    rnd = ce.RoundInputs(blob(players, util=[camera]), geo)
    below = geo.cell_of_px(350, 250)
    out, inside, back = (ce.Tick(rnd, t).holders[0] for t in (0.5, 1.5, 2.5))
    assert out.raw.any() and not out.watch[below]
    assert not inside.raw.any() and inside.watch[below]
    assert back.raw.any() and not back.watch[below]
    old = {k: v for k, v in camera.items() if k not in ("possessed", "yaws")}
    rnd = ce.RoundInputs(blob(players, util=[old]), geo)
    assert rnd.missing["Cypher camera: no in-use intervals (not modelled)"] == 1


# ---------------------------------------------------------------- the round


def test_the_tick_schedule():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}
    util = [ability("Wraith", "4_Smoke", 250, 200, 5, t=1.03, t1=3.3),
            *({"k": "shot", "t": t, "by": 0, "u": 1, "v": 1} for t in (2.01, 2.1, 2.3))]
    rnd = ce.RoundInputs(blob(players, t_end=5.0, util=util, deaths={5: 4.2}), geo)
    ticks = rnd.tick_times().tolist()
    assert {0.0, 0.5, 1.0, 4.5}.issubset(ticks)
    assert 3.3125 in ticks, "the smoke's end, snapped to the 16 Hz grid"
    assert 2.3125 in ticks and 2.0625 not in ticks and 2.125 not in ticks, "one shot tick per 0.25 s window"
    assert 4.1875 in ticks and 4.25 in ticks, "a death: the grid point before it and the one after"
    assert all(t * 16 == int(t * 16) for t in ticks) and ticks == sorted(ticks) and max(ticks) < 5.0
    rc = ce.compute_round(blob(players, t_end=5.0, util=util, deaths={5: 4.2}), geo)
    assert rc.weights.sum() == pytest.approx(5.0)
    assert rc.players[5].alive_s == pytest.approx(4.2)


@pytest.mark.parametrize("alive,db_t", [([[0.0, None, "round_end"]], 2.5), ([[0.0, 3.0, "kill"]], 2.5),
                                        ([[0.0, 3.0, "kill"]], 1.5)])
def test_db_only_deaths_agree_with_the_viewer(alive, db_t):
    geo = open_hall()
    b = blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)}, t_end=5.0)
    b["alive"]["0"] = alive
    rnd = ce.RoundInputs(b, geo, ce.ControlLink(db_deaths=((0, db_t),)))
    steps = alive_steps(b, {0: "team-1", 5: "team-2"}, [{"slot": 0, "t_db": db_t}], 0.0)
    for t in np.arange(0.0, 5.0, 0.0625):
        viewer = next(row for row in reversed(steps) if row[0] <= t)[1]
        assert rnd.alive(0, float(t)) == bool(viewer), t


def test_a_blob_with_no_sides_takes_the_links_or_refuses():
    geo = open_hall()
    b = blob({0: still("A", 150, 200, 0), 5: still("B", 400, 110, 90)})
    for p in b["players"]:
        p["side"] = None
    rnd = ce.RoundInputs(b, geo, ce.ControlLink(sides={0: "attack", 5: "defense"}))
    assert rnd.team == {0: "A", 5: "B"} and rnd.group_side == {"A": "attack", "B": "defense"}
    with pytest.raises(ce.ControlError):
        ce.RoundInputs(b, geo)


# ---------------------------------------------------------------- memory (D6)


class _Tk:
    def __init__(self, t, *holders):
        self.t, self.holders = t, {h.slot: h for h in holders}


def _sees(slot, team, cells):
    z = np.zeros(GRID * GRID, bool)
    view = z.copy()
    view[np.asarray(cells, int)] = True
    return ce.Holder(slot, team, 0, 0.0, 0.0, view.copy(), z.copy(), z.copy(), view.copy(), view.copy(), False, "hold")


def _halves(geo):
    walk = np.flatnonzero(geo.walk.ravel())
    x = walk % GRID
    mid = geo.cell_of_px(256, 200) % GRID
    return walk[x < mid], walk[x >= mid]


def test_seen_again_is_live_and_memory_dies_with_its_player():
    geo = open_hall()
    west, _ = _halves(geo)
    mem = ce.Memory(geo)
    mem.apply(_Tk(0.0, _sees(0, "A", west)))
    again = _sees(0, "A", west)
    mem.apply(_Tk(0.5, again))
    assert again.active[west].all() and not again.passive.any()
    mem.apply(_Tk(1.0, _sees(5, "B", [])))
    assert 0 not in mem.cells
    back = _sees(0, "A", [])
    mem.apply(_Tk(1.5, back))
    assert not back.passive.any(), "a new life starts with no memory"


def _at(slot, team, geo, x, y, cells=()):
    h = _sees(slot, team, cells)
    h.cell = geo.cell_of_px(x, y)
    return h


def _barrier_hall():
    """The open hall with a barrier line down x 256: A starts west of it, B east."""
    geo = copy.copy(open_hall())    # open_hall() is shared: don't leave a barrier on it
    geo.barrier = np.zeros((GRID, GRID), bool)
    geo.barrier[:, geo.cell_of_px(256, 200) % GRID] = True
    return geo


def test_a_player_pressed_on_the_barrier_line_still_gets_a_share():
    geo = _barrier_hall()
    west, _ = _halves(geo)
    mem = ce.Memory(geo)
    on_line = _at(1, "A", geo, 258, 120)
    assert geo.barrier.ravel()[on_line.cell]
    mem.begin(ce.barrier_start(geo, _Tk(0.0, _at(0, "A", geo, 150, 250), on_line, _at(5, "B", geo, 400, 200))))
    assert mem.cells[1].any() and set(np.flatnonzero(mem.cells[1])) <= set(west.tolist())
    assert mem.cells[1][geo.cell_of_px(248, 120)], "the ground next to them on their side is theirs"


def test_a_leaking_barrier_gives_no_start_ground():
    geo = _barrier_hall()
    geo.barrier[: geo.cell_of_px(0, 150) // GRID, :] = False     # the line stops at y 150: a gap to the north wall
    rnd = ce.RoundInputs(blob({0: still("A", 150, 200, 0), 5: still("B", 400, 200, 180)}), geo)
    tk = ce.Tick(rnd, 0.0)
    mem = ce.Memory(geo)
    mem.begin(ce.barrier_start(geo, tk))
    assert mem.cells == {} and rnd.missing["barrier paint leaks (no start ground)"] == 2


def test_no_barrier_paint_means_no_start_memory():
    geo = open_hall()
    mem = ce.Memory(geo)
    mem.begin(ce.barrier_start(geo, _Tk(0.0, _at(0, "A", geo, 150, 200))))
    a = _at(0, "A", geo, 150, 200)
    mem.apply(_Tk(1.0, a))
    assert not a.passive.any()


def test_the_start_is_shared_by_walking_distance():
    geo = _barrier_hall()
    area = geo.walk & ~geo.barrier
    area[:, geo.cell_of_px(256, 200) % GRID:] = False
    shares = ce._share_by_walk(area, {0: geo.cell_of_px(120, 120), 1: geo.cell_of_px(120, 280)})
    assert shares[0].ravel()[geo.cell_of_px(130, 110)] and shares[1].ravel()[geo.cell_of_px(130, 290)]
    assert not (shares[0] & shares[1]).any() and ((shares[0] | shares[1]) == area).all()


def _band(geo, x0, x1, y0=96, y1=296):
    """Flat walkable cells whose centres lie in the px box."""
    cx, cy = geo.centres[:, 0], geo.centres[:, 1]
    return geo.walk.ravel() & (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)


def _lines(geo, views: dict):
    """A tick on the open hall whose holders see exactly `views`: {slot: (team, x, y, active flat)}."""
    tk = tick(geo, {s: still(team, x, y, 0) for s, (team, x, y, _) in views.items()})
    z = np.zeros(GRID * GRID, bool)
    tk.holders = {s: ce.Holder(s, team, geo.cell_of_px(x, y), x, y, act.copy(), z.copy(), z.copy(), act.copy(),
                               act.copy(), False, "hold") for s, (team, x, y, act) in views.items()}
    tk._back = {}
    return tk


def test_backfill_fills_behind_an_unbroken_line_as_passive():
    geo = open_hall()
    tk = _lines(geo, {0: ("A", 150, 200, _band(geo, 240, 256)), 5: ("B", 400, 200, _band(geo, 300, 316))})
    back = tk.backfill("A")
    behind = geo.cell_of_px(120, 120)
    assert set(back) == {0} and back[0][behind], "west of A's line is A's"
    assert not back[0][geo.cell_of_px(280, 200)], "between the lines: B can walk there"
    state = tk.compose()["state"]
    assert at(geo, state, 120, 120) in A_OWN
    act, psv, _, pas = tk.coverage()[0]
    assert pas[behind] and psv > 0, "the player's own coverage shows it, as passive"


def test_no_backfill_through_a_gap_or_over_enemy_control():
    geo = open_hall()
    gap = _band(geo, 240, 256, 96, 250)       # the line stops short of the south wall
    assert _lines(geo, {0: ("A", 150, 200, gap), 5: ("B", 400, 200, _band(geo, 300, 316))}).backfill("A") == {}
    seen_by_b = _band(geo, 96, 140, 96, 150)
    tk = _lines(geo, {0: ("A", 150, 200, _band(geo, 240, 256)), 5: ("B", 400, 200, _band(geo, 300, 316) | seen_by_b)})
    back = tk.backfill("A")[0]
    assert not back[seen_by_b].any() and back[geo.cell_of_px(200, 250)]


def test_backfill_goes_to_the_nearer_players_line_and_opens_without_them():
    geo = open_hall()
    north, south = _band(geo, 240, 256, 96, 200), _band(geo, 240, 256, 200, 296)
    tk = _lines(geo, {0: ("A", 200, 120, north), 1: ("A", 200, 280, south), 5: ("B", 400, 200, _band(geo, 300, 316))})
    back = tk.backfill("A")
    assert back[0][geo.cell_of_px(230, 110)] and back[1][geo.cell_of_px(230, 290)]
    assert not (back[0] & back[1]).any()
    assert tk.backfill("A", removed=1) == {}, "without the south line, B can walk round: no pocket"


def test_a_turn_leaves_the_ground_behind_covered():
    geo = open_hall()
    players = {0: ("A", [(0.0, 150, 200, 180), (1.0, 150, 200, 0)]), 5: still("B", 400, 110, 90)}
    rc = ce.compute_round(blob(players, t_end=2.0), geo, ticks=[0.5, 1.0, 1.5])
    col = int(np.searchsorted(rc.walk_cells, geo.cell_of_px(110, 200)))
    assert rc.coverage_masks[0, 0, col], "seen while facing west"
    assert rc.coverage_masks[2, 0, col], "still covered, as memory, after turning east"
    assert rc.states[2, col] in A_OWN
