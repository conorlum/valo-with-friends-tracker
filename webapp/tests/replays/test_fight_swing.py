"""Fight swing (app/control/fight_swing.py): team fights and the ground each side lost, on hand-made frames."""

import numpy as np

from app.control import fight_swing as fs

A_HELD, B_HELD, CONTESTED = 2, 5, 8


def _round(states, *, deaths, agents=None, masks=None, alive=None, positions=None, frames=None,
           t_decided=100.0, cell_xy=None):
    states = np.asarray(states, np.uint8)
    n_frames, n_cells = states.shape
    frames = np.arange(n_frames, dtype=float) if frames is None else np.asarray(frames, float)
    team = {0: "A", 1: "A", 5: "B", 6: "B"}
    masks = np.zeros((n_frames, 10, n_cells), bool) if masks is None else masks
    cell_xy = np.stack([np.arange(n_cells) * 10.0, np.zeros(n_cells)], 1) if cell_xy is None else cell_xy
    positions = positions or {}
    return fs.round_fights(
        deaths=deaths, team=team, agents=agents or {}, alive=alive or (lambda s, t: True),
        pos_m=lambda s, t: positions.get(s, (0.0, 0.0)), frames=frames, states=states, control_masks=masks,
        cell_xy_m=cell_xy, cell_m2=1.0, t_start=0.0, t_decided=t_decided)


def test_deaths_chain_into_one_fight_while_each_is_within_the_window():
    fights = fs.chain([(0, 10.0), (5, 15.5), (1, 21.0), (6, 30.0)])
    assert [[t for _, t in f] for f in fights] == [[10.0, 15.5, 21.0], [30.0]]


def test_lost_ground_is_before_minus_after_within_20_m_of_the_deaths():
    # cells at 0, 10, 20, 30 m; the death at 0 m: cells within 20 m are the first three
    before, after = [A_HELD, A_HELD, A_HELD, A_HELD], [B_HELD, A_HELD, B_HELD, B_HELD]
    states = [before] * 12 + [after] * 18
    [f] = _round(states, deaths=[(0, 12.0)])
    assert f["lost_m2"] == {"A": 2.0, "B": -2.0}          # the 30 m cell flipped too but is not the fight's
    assert f["area_m2"] == 3.0 and f["measured"] == [11.0, 17.0]
    assert f["deaths"] == [[0, 12.0, "A"]] and not f["wipe"]


def test_the_after_end_is_capped_at_the_decision():
    states = [[A_HELD]] * 14 + [[B_HELD]] * 16
    [f] = _round(states, deaths=[(0, 12.0)], t_decided=13.0)
    assert f["measured"][1] == 13.0 and f["lost_m2"]["A"] == 0.0     # frames 11-13 are still A's


def test_only_a_setup_sentinels_removal_mask_widens_the_ground():
    n = 4
    states = np.array([[A_HELD] * n] * 12 + [[B_HELD] * n] * 18, np.uint8)
    masks = np.zeros((30, 10, n), bool)
    masks[:, 0, 3] = True                      # slot 0's removal would lose the far cell (a trip at 30 m)
    for agent, far in (("Cypher", 1.0), ("Omen", 0.0)):
        [f] = _round(states, deaths=[(0, 12.0)], agents={0: agent}, masks=masks)
        assert f["lost_m2"]["A"] == 3.0 + far, agent


def test_a_sentinels_mask_counts_only_where_their_team_held_or_contested():
    n = 4
    states = np.array([[A_HELD, A_HELD, A_HELD, 0]] * 12 + [[B_HELD] * n] * 18, np.uint8)
    masks = np.zeros((30, 10, n), bool)
    masks[:, 0, 3] = True                      # nobody holds that cell: the mask alone is not ground
    [f] = _round(states, deaths=[(0, 12.0)], agents={0: "Killjoy"}, masks=masks)
    assert f["area_m2"] == 3.0
    states[:, 3] = CONTESTED
    [f] = _round(states, deaths=[(0, 12.0)], agents={0: "Killjoy"}, masks=masks)
    assert f["area_m2"] == 4.0


def test_an_end_between_frames_takes_no_frame_past_it():
    # half-second frames; the death at 10.1 puts "before" at 9.1: frames 7.5-9.0, never 9.5 (where A's cell flips)
    frames = np.arange(0, 15, 0.5)
    states = np.array([[A_HELD] if t < 9.5 else [B_HELD] for t in frames], np.uint8)
    [f] = _round(states, deaths=[(0, 10.1)], frames=frames)
    assert f["measured"][0] == 9.1 and f["lost_m2"]["A"] == 1.0


def test_a_sentinels_mask_is_read_at_its_exact_frame_when_there_is_one():
    # the death at 12.0 reads the mask at 11.5, a stored frame: that frame's mask, not 11.0's
    frames = np.arange(0, 15, 0.5)
    states = np.array([[A_HELD] * 4] * 24 + [[B_HELD] * 4] * 6, np.uint8)
    masks = np.zeros((len(frames), 10, 4), bool)
    masks[list(frames).index(11.5), 0, 3] = True
    [f] = _round(states, deaths=[(0, 12.0)], agents={0: "Cypher"}, masks=masks, frames=frames)
    assert f["area_m2"] == 4.0


def test_a_disconnect_is_not_a_death_and_joins_no_fights():
    alive = {"0": [[0.0, 14.0, "left"]], "1": [[0.0, 10.0, "killed"]], "5": [[0.0, None, None]]}
    left = fs.left_ends(alive)
    assert left == {(0, 14.0)}
    assert fs.left_ends({"0": [[0.0, 14.0]]}) == set() and fs.left_ends(None) == set()
    # deaths at 10 and 20 s, more than CHAIN_S apart; a disconnect at 14 s would chain them into one fight
    deaths = [(1, 10.0), (0, 14.0), (5, 20.0)]
    assert len(_round([[A_HELD]] * 30, deaths=deaths)) == 1
    fights = fs.round_fights(
        deaths=deaths, left=left, team={0: "A", 1: "A", 5: "B", 6: "B"}, agents={}, alive=lambda s, t: True,
        pos_m=lambda s, t: (0.0, 0.0), frames=np.arange(30.0), states=np.full((30, 1), A_HELD, np.uint8),
        control_masks=np.zeros((30, 10, 1), bool), cell_xy_m=np.zeros((1, 2)), cell_m2=1.0, t_start=0.0,
        t_decided=100.0)
    assert [f["deaths"] for f in fights] == [[[1, 10.0, "A"]], [[5, 20.0, "B"]]]


def test_a_fight_that_leaves_a_side_with_nobody_is_a_wipe():
    dead = {0: 12.0, 1: 13.0}
    alive = lambda s, t: not (s in dead and t >= dead[s])  # noqa: E731
    [f] = _round([[A_HELD]] * 30, deaths=[(0, 12.0), (1, 13.0)], alive=alive)
    assert f["wipe"]
    [f] = _round([[A_HELD]] * 30, deaths=[(0, 12.0)], alive=alive)
    assert not f["wipe"]


def test_deaths_outside_the_live_round_and_rounds_without_frames():
    assert _round([[A_HELD]] * 30, deaths=[(0, 150.0)]) == []
    assert _round(np.zeros((0, 1)), deaths=[(0, 12.0)], frames=[]) == []
