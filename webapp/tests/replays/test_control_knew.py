"""What each team knew (app/control/engine.py `Knowledge`; docs/map-control-team-knew-plan.md) on toy maps:
seen enemies are exact, unseen ones are a region of where they could be, a just-lost enemy's view lingers as
passive, and only real deaths give a team the whole map."""

import numpy as np

from app.control import engine as ce
from app.control.geometry import GRID
from tests.replays.control_toys import blob, open_hall

A_OWN = (ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE)


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


def test_the_possible_region_grows_with_time_and_stops_at_watched_ground():
    geo = open_hall()
    start = geo.cell_of_px(300, 200)
    watched = np.zeros(GRID * GRID, bool)
    small = ce.possible_region(geo, start, watched, 2)
    big = ce.possible_region(geo, start, watched, 6)
    assert small[start] and small.sum() == 25 and big.sum() > small.sum() and (big | small).sum() == big.sum()
    wall = np.zeros((GRID, GRID), bool)
    wall[:, start % GRID + 1] = True                      # the team watches the column east of the start
    blocked = ce.possible_region(geo, start, wall.ravel(), 10)
    assert not (blocked.reshape(GRID, GRID)[:, start % GRID + 1:]).any()


def _region_step_by_step(geo, start, watched, steps):
    """possible_region as it was first written: one frontier step at a time (the reference)."""
    from scipy import ndimage

    open_ = geo.walk & ~watched.reshape(GRID, GRID)
    region = np.zeros((GRID, GRID), bool)
    region.flat[start] = True
    front = region.copy()
    for _ in range(max(0, int(steps))):
        nxt = ndimage.binary_dilation(front, ce.EIGHT) & open_ & ~region
        if not nxt.any():
            break
        region |= nxt
        front = nxt
    return region.ravel()


def test_the_possible_region_matches_walking_it_step_by_step():
    """One masked dilation (13x faster, the optimisation review of 2026-10-01) gives exactly the region of
    the step-by-step walk, including 0 steps, fractional steps and a start the team watches."""
    geo = open_hall()
    rng = np.random.default_rng(7)
    walk = np.flatnonzero(geo.walk.ravel())
    for k in range(60):
        start = int(rng.choice(walk))
        watched = rng.random(GRID * GRID) < (0.05 + 0.3 * rng.random())
        if k % 3 == 0:
            watched[start] = True
        steps = [0, 0.4, 1, 2.7, 5, 40][k % 6]
        assert np.array_equal(ce.possible_region(geo, start, watched, steps),
                              _region_step_by_step(geo, start, watched, steps)), (k, start, steps)


def test_when_every_enemy_is_seen_the_picture_is_the_truth():
    geo = open_hall()
    players = {0: still("A", 150, 200, 0), 5: still("B", 380, 200, 180)}     # face to face
    rc = ce.compute_round(blob(players, t_end=2.0), geo)
    assert np.array_equal(rc.knew_states["A"], rc.states) and np.array_equal(rc.knew_states["B"], rc.states)
    assert rc.knew_sightings["A"][5][0][:2] == [float(rc.ticks[0]), float(rc.ticks[-1])]


def test_an_unseen_enemy_is_not_absent():
    geo = open_hall()
    # back to back: neither team sees the other; without the fog A's picture would be the whole hall
    players = {0: still("A", 150, 200, 180), 5: still("B", 380, 200, 0)}
    rc = ce.compute_round(blob(players, t_end=2.0), geo)
    walkable = len(rc.walk_cells)
    for n in range(len(rc.ticks)):
        assert np.isin(rc.knew_states["A"][n], A_OWN).sum() < walkable
    assert rc.knew_sightings["A"] == {}


def test_only_a_real_death_gives_the_whole_map():
    geo = open_hall()
    players = {0: still("A", 150, 200, 180), 5: still("B", 380, 200, 0)}
    rc = ce.compute_round(blob(players, t_end=3.0, deaths={5: 1.5}), geo)
    after = [n for n, t in enumerate(rc.ticks) if t > 1.6]
    assert after and all(np.isin(rc.knew_states["A"][n], A_OWN).all() for n in after)


def test_a_lost_enemys_view_lingers_as_passive_then_fades():
    geo = open_hall()
    # A0 faces B5 until 1 s, then turns away; B5 faces A0 all along (B's view of A doesn't matter here)
    players = {0: ("A", [(0.0, 150, 200, 0), (1.0, 150, 200, 180)]), 5: still("B", 380, 200, 180)}
    rnd = ce.RoundInputs(blob(players, t_end=8.0), geo)
    kn = ce.Knowledge(rnd, "A")
    first = kn.tick_for(ce.Tick(rnd, 0.5), 0.5)
    assert 5 in first.holders and not first.extra_passive
    lingering = kn.tick_for(ce.Tick(rnd, 2.0), 2.0)
    assert 5 not in lingering.holders and lingering.extra_passive["B"].any()
    assert lingering.seeds["B"].any(), "and they could have moved"
    gone = kn.tick_for(ce.Tick(rnd, 5.0), 5.0)
    assert not gone.extra_passive and gone.seeds["B"].sum() >= lingering.seeds["B"].sum()
    assert kn.sightings[5] == [[0.5, 0.5, round(380 * 10000 / 1024), round(200 * 10000 / 1024)]]


def test_the_true_view_is_unchanged_by_the_knowledge_pass():
    geo = open_hall()
    players = {0: still("A", 150, 200, 180), 5: still("B", 380, 200, 0)}
    with_k = ce.compute_round(blob(players, t_end=2.0), geo)
    without = ce.compute_round(blob(players, t_end=2.0), geo, knowledge=False)
    assert np.array_equal(with_k.states, without.states) and np.array_equal(with_k.control_masks, without.control_masks)
    assert without.knew_states is None
