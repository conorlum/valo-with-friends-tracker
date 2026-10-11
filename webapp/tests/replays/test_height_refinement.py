import pytest

from app.control import height_build as hb, height_motion as hm, height_refinement as hr
from tests.replays.control_toys import fast_blob, open_hall, stair_run


PARENT = 25 * 128 + 25
CHILDREN = [(50 + dy) * 256 + 50 + dx for dy in (0, 1) for dx in (0, 1)]
GEO = open_hall()
MATCHES = [0, 0, 1, 1, 1, 0]


def runs(z, child, *, first_slot=0):
    return [hb.Stand(i, first_slot, 0, 1, z, (PARENT,), 204, 204, (child,)) for i in range(6)]


def analyze(stands, floors=None, reasons=None, previous=None, walks=()):
    return hr.analyze(GEO, stands, walks, MATCHES, floors or {}, reasons or {},
                      hr.history(GEO.name) if previous is None else previous)


def test_close_ledge_heights_split_without_inventing_unsampled_children():
    found = runs(90, CHILDREN[0]) + runs(105, CHILDREN[1], first_slot=1)
    result = analyze(found, reasons={PARENT: hb.TOO_CLOSE})
    assert result['history']['parents'] == [PARENT]
    assert result['children'][0]['floors_world_dm'] == [[90, 0]]
    assert result['children'][1]['floors_world_dm'] == [[105, 0]]
    assert result['children'][2]['floors_world_dm'] == []
    assert result['area'] == {'selected_parent_equivalents': 1, 'selected_px2': 64,
                              'supported_child_px2': 32, 'unknown_child_px2': 32}
    assert not result['runtime_enabled']


def test_genuine_stacked_floors_at_the_same_xy_keep_the_parent_coarse():
    found = [s for child in CHILDREN for s in runs(90, child) + runs(120, child, first_slot=1)]
    result = analyze(found, floors={PARENT: [(90, 0), (120, 0)]})
    assert result['history']['parents'] == []
    assert not result['checked'][0]['spatial_distinction']


def test_refined_child_can_still_have_genuine_stacked_floors():
    found = runs(90, CHILDREN[0]) + runs(120, CHILDREN[0], first_slot=1) + runs(90, CHILDREN[1])
    result = analyze(found, floors={PARENT: [(90, 0), (120, 0)]})
    assert result['children'][0]['floors_world_dm'] == [[90, 0], [120, 0]]
    assert result['children'][1]['floors_world_dm'] == [[90, 0]]


def test_prior_selection_is_explicit_and_retained_when_evidence_disappears():
    result = analyze([], previous=hr.history(GEO.name, [PARENT]))
    assert result['history']['parents'] == [PARENT]
    assert result['area']['unknown_child_px2'] == 64
    for prior in (None, hr.history('another-map'), {'v': 2, 'map': GEO.name, 'child_px': 4, 'parents': []}):
        with pytest.raises(ValueError, match='history'):
            hr.analyze(GEO, [], [], [], {}, {}, prior)


def test_missing_samples_and_neighbour_disagreement_do_not_create_empty_splits():
    assert analyze([], reasons={PARENT: hb.NEIGHBOURS_DISAGREE})['history']['parents'] == []
    assert analyze(runs(90, CHILDREN[0]), reasons={PARENT: hb.NEIGHBOURS_DISAGREE})['history']['parents'] == []


def test_selection_and_hash_are_independent_of_run_order_and_duplicates_do_not_vote():
    found = runs(90, CHILDREN[0]) + runs(105, CHILDREN[1], first_slot=1)
    a = analyze(found, reasons={PARENT: hb.TOO_CLOSE})
    b = analyze(found[::-1] + found, reasons={PARENT: hb.TOO_CLOSE})
    assert a == b
    assert hr.identity(hr.history(GEO.name, [PARENT])) != hr.identity(hr.history(GEO.name))


def test_a_single_match_cannot_resolve_children_and_refinement_does_not_revote_frames():
    stand = hb.Stand(0, 0, 0, 10, 90, (PARENT,), 204, 204, (CHILDREN[0],) * 100)
    obs = hr.observations([stand] * 10, [], [0])
    assert len(obs[CHILDREN[0]]) == 1
    result = hr.analyze(GEO, [stand], [], [0], {}, {}, hr.history(GEO.name, [PARENT]))
    assert result['area']['supported_child_px2'] == 0


def test_accepted_walks_keep_actual_fine_footprints_and_lows():
    blob = fast_blob({0: stair_run()})
    walks = hm.walks(blob, GEO, found=hb.stands(blob, GEO))
    assert walks and any(len(set(w.fine_cells)) > len(set(w.cells)) for w in walks)
    for walk in walks:
        assert len(walk.fine_cells) == len(walk.fine_low)
        assert {hm.coarse_of_fine(c) for c in walk.fine_cells} <= set(walk.cells)


def test_platform_exclusion_cannot_leak_child_evidence_back_into_a_run():
    # One long accepted stand crosses from a platform exclusion radius into ordinary ground.
    cells = (PARENT, PARENT + 40)
    far_child = CHILDREN[0] + 80
    stand = hb.Stand(0, 0, 0, 1, 90, cells, 360, 204, (CHILDREN[0], far_child))
    kept = hb.off_platforms([stand], [(0, 2, 204, 204)], GEO)
    assert len(kept) == 1 and kept[0].cells == (PARENT + 40,)
    assert kept[0].fine_cells == (far_child,)
    assert set(hr.observations(kept, [], [0])) == {far_child}


def test_inconsistent_or_unaccepted_fine_evidence_is_rejected():
    stand = hb.Stand(0, 0, 0, 1, 90, (PARENT,), 204, 204, (0,))
    with pytest.raises(ValueError, match='outside accepted'):
        hr.observations([stand], [], [0])
