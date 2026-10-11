import pytest

from app.control.rope_positions import coordinate_cluster, vertical_runs


def points(speed, seconds=2, x=100, y=200):
    return [{'t': i * 50, 'x': x, 'y': y, 'z': speed * i * .05} for i in range(round(seconds / .05) + 1)]


@pytest.mark.parametrize('speed', [200, 400, 800, 1600, -200, -800])
def test_ride_speed_does_not_change_geometry(speed):
    [ride] = vertical_runs(points(speed))
    assert ride['world_xy_cm'] == [100, 200]
    assert ride['speed_cm_s'] == pytest.approx(speed)


def test_pauses_split_geometry_runs_and_horizontal_departure_is_excluded():
    samples = points(200, seconds=1)
    samples += [{'t': 1000 + i * 50, 'x': 100, 'y': 200, 'z': 200} for i in range(1, 11)]
    samples += [{'t': 1500 + p['t'], 'x': p['x'], 'y': p['y'], 'z': 200 + p['z']}
                for p in points(200, seconds=1)[1:]]
    assert len(vertical_runs(samples)) == 2
    moving = [{**p, 'x': p['t']} for p in points(200)]
    assert not vertical_runs(moving)


def test_jump_arc_and_teleport_do_not_become_a_rope_location():
    arc = [{**p, 'z': 800 * p['t'] / 1000 - 1000 * (p['t'] / 1000) ** 2} for p in points(200, seconds=.8)]
    assert not vertical_runs(arc)
    assert not vertical_runs([{'t': 0, 'x': 100, 'y': 200, 'z': 0},
                              {'t': 50, 'x': 100, 'y': 200, 'z': 1000}])


def rides(positions):
    return [{'round': i + 1, 'slot': 0, 't0_ms': 0, 't1_ms': 2000, 'uv': uv}
            for i, uv in enumerate(positions)]


def test_coordinates_ignore_outliers_without_averaging_two_ropes():
    result = coordinate_cluster(rides([[100, 200], [100, 200], [101, 200], [400, 500]]))
    assert result['uv'] == [100, 200] and result['excluded_rides'] == 1
    assert coordinate_cluster(rides([[100, 200], [100, 200], [400, 500], [400, 500]]))['uv'] is None


def test_duplicate_samples_or_one_round_do_not_certify_a_coordinate():
    one = rides([[100, 200]])
    assert coordinate_cluster(one * 100)['uv'] is None
    same_round = rides([[100, 200], [100, 200]])
    same_round[1]['round'] = 1
    same_round[1]['slot'] = 1
    assert coordinate_cluster(same_round)['uv'] is None
