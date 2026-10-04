"""The observer hook and the tick cache (timing-gaps spec, section 3)."""

import numpy as np

from app.control import engine as ce
from app.control import heights as hc
from app.gaps import cache
from tests.replays.control_toys import blob, door_hall, height_blob, standing, toy_heights


def _round():
    return blob({0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180), (6.0, 300, 270, 180)])}, t_end=6.0)


def test_control_is_byte_identical_with_an_observer():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import control_reference as ref
    geo, data = door_hall(), _round()
    plain = ref.digest_round(ce.compute_round(data, geo), data)
    watched = ref.digest_round(ce.compute_round(data, geo, observer=lambda rec, unk: None), data)
    assert plain == watched


def test_the_observer_sees_every_tick_with_players_and_unknown():
    geo, data = door_hall(), _round()
    seen = []
    rc = ce.compute_round(data, geo, observer=lambda rec, unk: seen.append(
        (rec.t, sorted(rec.players), {s: {e: int((a >= 0).sum()) for e, a in d.items()} for s, d in rec.unknown.items()})))
    assert [t for t, _, _ in seen] == rc.ticks.tolist()
    assert seen[0][1] == [0, 5]
    assert seen[-1][2]["A"][5] > 0


def test_the_view_is_the_players_own_sight_before_memory():
    """R12: `view` is active | passive (presence included) as the tick built it, before Memory adds
    remembered ground to `passive`; `live` is view plus utility and the player's own node."""
    geo, data = door_hall(), _round()
    checked = []

    def look(rec, unk):
        for p in rec.players.values():
            assert p.view is not None and p.view.dtype == bool and len(p.view) == geo.n
            assert not (p.view & ~p.live).any()
            assert np.array_equal(p.live, p.view | p.utility | (np.arange(geo.n) == p.node))
            assert p.eye_z is None                    # a flat map
        checked.append(rec.t)

    ce.compute_round(data, geo, observer=look)
    assert checked


def test_eye_z_is_the_engines_eye_height_on_a_map_with_heights():
    """R13: `eye_z` is Tick._eye: the player's own z plus EYE_M."""
    geo = toy_heights("ObserveEye", [(96, 96, 416, 296)], upper=[((200, 150, 260, 200), 3.0)])
    data = height_blob({0: standing(230, 175, 3.0), 5: standing(380, 250, 0.0, "B")}, t_end=3.0)
    eyes = []
    ce.compute_round(data, geo, observer=lambda rec, unk: eyes.append({s: p.eye_z for s, p in rec.players.items()}))
    assert eyes
    for e in eyes:
        assert abs(e[0] - (3.0 + hc.EYE_M)) < 0.05
        assert abs(e[5] - hc.EYE_M) < 0.05


def test_cache_keys_differ_by_round(tmp_path):
    a = cache.cache_path(7, 1, "abcd", tmp_path)
    b = cache.cache_path(7, 2, "abcd", tmp_path)
    c = cache.cache_path(8, 1, "abcd", tmp_path)
    assert len({a, b, c}) == 3


def test_cache_keys_differ_by_engine_key(tmp_path):
    """R5: a different engine key (a choke or hearing edit) never names the old file."""
    assert cache.cache_path(7, 1, "abcd", tmp_path) != cache.cache_path(7, 1, "abce", tmp_path)


def test_the_cache_replays_what_the_observer_saw(tmp_path):
    geo, data = door_hall(), _round()
    live = []
    path = cache.cache_path(1, 1, "f", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.append((rec.t, {s: {e: a.copy() for e, a in d.items()} for s, d in rec.unknown.items()}))
        writer(rec, unk)

    ce.compute_round(data, geo, observer=both)
    writer.close()
    replayed = list(cache.replay(path))
    assert [rec.t for rec, _ in replayed] == [t for t, _ in live]
    for (rec, logs), (_, want) in zip(replayed, live):
        for side, enemies in want.items():
            for e, arr in enemies.items():
                assert np.array_equal(rec.unknown[side][e], arr)
    assert len(replayed[-1][1]["A"]) > 0


def test_the_cache_keeps_players_events_and_smokes(tmp_path):
    geo = toy_heights("ObserveCache", [(96, 96, 416, 296)], upper=[((200, 150, 260, 200), 3.0)])
    data = height_blob({0: standing(230, 175, 3.0), 5: ("B", [(0.0, 380, 250, 180, 0.0), (3.0, 300, 270, 180, 0.0)])},
                       t_end=3.0)
    live = []
    path = cache.cache_path(1, 2, "k", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.append((rec.t, {s: (p.team, p.node, p.x, p.y, p.yaw, p.live.copy(), p.utility.copy(), p.view.copy(),
                                 p.eye_z) for s, p in rec.players.items()},
                     {k: list(v) for k, v in rec.events.items()}, list(rec.smokes)))
        writer(rec, unk)

    ce.compute_round(data, geo, observer=both)
    writer.close()
    replayed = [rec for rec, _ in cache.replay(path)]
    assert len(replayed) == len(live)
    for rec, (t, players, events, smokes) in zip(replayed, live):
        assert rec.t == t and rec.events == events and rec.smokes == smokes
        assert sorted(rec.players) == sorted(players)
        for s, (team, node, x, y, yaw, lv, ut, view, eye) in players.items():
            p = rec.players[s]
            assert (p.team, p.node, p.x, p.y, p.yaw, p.eye_z) == (team, node, x, y, yaw, eye)
            assert np.array_equal(p.live, lv) and np.array_equal(p.utility, ut) and np.array_equal(p.view, view)
    assert any(p.eye_z is not None for rec in replayed for p in rec.players.values())


def test_the_cache_stores_the_missing_inputs(tmp_path):
    """R20: the compute-time missing-input counts travel with the file."""
    geo, data = door_hall(), _round()
    path = cache.cache_path(1, 3, "m", tmp_path)
    writer = cache.Writer(path)
    rc = ce.compute_round(data, geo, observer=writer)
    writer.close(rc.missing_inputs | {"locating event without a position": 2})
    assert cache.read_missing(path) == rc.missing_inputs | {"locating event without a position": 2}
    empty = cache.cache_path(1, 4, "m", tmp_path)
    w2 = cache.Writer(empty)
    ce.compute_round(data, geo, observer=w2)
    w2.close()
    assert cache.read_missing(empty) == {}
    assert not list(tmp_path.glob("*.tmp"))


def test_a_cache_of_another_format_is_refused(tmp_path):
    import gzip
    import pickle

    import pytest

    path = tmp_path / "old.ticks.pkl.gz"
    with gzip.open(path, "wb") as f:
        pickle.dump({"format": 0}, f)
    with pytest.raises(ValueError):
        list(cache.replay(path))
    with pytest.raises(ValueError):
        cache.read_missing(path)
