"""One team's nodes are that team's; contested needs both sides (2026-10-14 run, bug 3: a room marked
contested with no orange claim). Toy patterns as test_control_floors.py and test_adaptive_ground.py."""
import numpy as np

from app.control import engine as ce
from tests.replays.control_toys import HALL, toy_heights
from tests.replays.test_adaptive_ground import PARENT, mixed
from tests.replays.test_control_floors import bridge, node


def _adaptive(a, b):
    """The adaptive toy with the two walkable sub-nodes of the split cell set to `a` and `b`."""
    geo = mixed()
    state = np.full(geo.n, ce.NONE, np.uint8)
    state[geo.node_at_px(202, 202, 0)] = a
    state[geo.node_at_px(206, 202, 0)] = b
    return geo, state


def _floors(lower, upper):
    geo = bridge()
    state = np.zeros(geo.n, np.uint8)
    state[node(geo, 260, floor=0)], state[node(geo, 260, floor=1)] = lower, upper
    return geo, state, node(geo, 260, floor=0)


def test_adaptive_one_team_takes_its_strongest():
    geo, state = _adaptive(ce.B_ACTIVE, ce.B_SAFE)
    assert ce.collapse_states(geo, state)[PARENT] == ce.B_ACTIVE


def test_adaptive_active_beside_nothing_stays_active():
    geo, state = _adaptive(ce.B_ACTIVE, ce.NONE)
    assert ce.collapse_states(geo, state)[PARENT] == ce.B_ACTIVE


def test_adaptive_both_teams_is_contested_active():
    geo, state = _adaptive(ce.A_ACTIVE, ce.B_SAFE)
    assert ce.collapse_states(geo, state)[PARENT] == ce.CONTESTED_ACTIVE


def test_floors_one_team_takes_its_strongest():
    geo, state, under = _floors(ce.B_SAFE, ce.B_PASSIVE)
    assert ce.collapse_states(geo, state)[under] == ce.B_SAFE


def test_floors_nothing_beside_a_claim_is_the_claim():
    geo, state, under = _floors(ce.NONE, ce.B_SAFE)
    assert ce.collapse_states(geo, state)[under] == ce.B_SAFE


def test_floors_both_teams_still_contested():
    geo, state, under = _floors(ce.A_SAFE, ce.B_PASSIVE)
    assert ce.collapse_states(geo, state)[under] == ce.CONTESTED


def test_floors_a_contested_floor_keeps_the_cell_contested():
    geo, state, under = _floors(ce.CONTESTED, ce.B_SAFE)
    assert ce.collapse_states(geo, state)[under] == ce.CONTESTED
    geo, state, under = _floors(ce.CONTESTED_ACTIVE, ce.B_SAFE)
    assert ce.collapse_states(geo, state)[under] == ce.CONTESTED_ACTIVE


def test_three_floors_one_team():
    geo = toy_heights("Three", [HALL], upper=[((240, 96, 288, 296), 4.0), ((240, 96, 288, 296), 8.0)])
    low, mid, top = (node(geo, 260, floor=k) for k in range(3))
    state = np.zeros(geo.n, np.uint8)
    state[low], state[mid], state[top] = ce.B_SAFE, ce.B_PASSIVE, ce.B_SAFE
    assert ce.collapse_states(geo, state)[low] == ce.B_SAFE


def test_section_totals_code_of_one_team_leaf_is_that_teams():
    geo, state = _adaptive(ce.B_ACTIVE, ce.B_SAFE)
    code = ce.space_codes(geo, state)
    assert code[geo.node_space[geo.node_at_px(202, 202, 0)]] == ce.B_ACTIVE
    assert code[geo.node_space[geo.node_at_px(206, 202, 0)]] == ce.B_SAFE
    assert code[geo.node_space[geo.node_at_px(206, 202, 0)]] != ce.CONTESTED_ACTIVE
