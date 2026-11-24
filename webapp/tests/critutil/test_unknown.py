"""Critical Util's unknown probe (app/critutil/unknown.py), through the real engine on toy rounds."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine as ce
from app.critutil import extract
from app.critutil.unknown import UnknownProbe
from tests.replays.control_toys import blob, door_hall, toy_ability, uv


def run(util, players, t_end=20.0):
    geo = door_hall()
    data = blob(players, util=util, t_end=t_end)
    rnd = ce.RoundInputs(data, geo)
    probe = UnknownProbe(rnd)
    ce.compute_round(data, geo, observer=lambda record, unknown: probe.step(record), knowledge=False)
    return probe.finish(), rnd, data


def still(side, x, y, yaw):
    return (side, [(0.0, x, y, yaw), (20.0, x, y, yaw)])


def test_a_trip_across_the_door_holds_the_unknown_at_its_line():
    # door_hall: a wall at x 200-216 with a door at its south end (y 288-296). Team A is west, out of sight of the
    # enemy east of the wall; the wire runs across the west side of the door, so the enemy's unknown comes through
    # the door and stops at it.
    trip = toy_ability("Gumshoe", "4_TripWire", 185, 240, 0, t=0.5, t1=20.0, kind="GameObject",
                       end=list(uv(185, 296)))
    # facing north, away from the door: only the wire watches it
    players = {0: still("A", 120, 110, 270), 1: still("A", 105, 105, 270), 5: still("B", 400, 200, 0)}
    holds, rnd, data = run([trip], players)
    assert holds["error"] is None
    (item,) = [i for i in holds["items"] if i["what"] == "device"]
    assert item["by"] == 0 and item["kind"] == "trip" and item["up_s"] > 5
    assert item["front_s"] > 5, "the unknown comes through the door (about 10 s) and stops at the wire"
    assert item["crossed_s"] == 0
    assert item["held_m2s"] > 0, "the wire's cells are ground no teammate sees"
    out = extract.round_util(ce.RoundInputs(data, rnd.geo), data, holds=holds)
    (cast,) = [c for c in out["casts"] if "device" in c["kinds"]]
    assert cast["hold"]["front_s"] == item["front_s"]
    assert out["avail"]["unknown"] is True


def _door_trip(t=0.5, t1=20.0, **extra):
    return toy_ability("Gumshoe", "4_TripWire", 185, 240, 0, t=t, t1=t1, kind="GameObject",
                       end=list(uv(185, 296)), **extra)


DOOR_PLAYERS = {0: still("A", 120, 110, 270), 1: still("A", 105, 105, 270), 5: still("B", 400, 200, 0)}


def _probe(util, t_end=20.0, t_decided=None, deaths=None):
    geo = door_hall()
    data = blob(DOOR_PLAYERS, util=util, t_end=t_end, t_decided=t_decided, deaths=deaths)
    rnd = ce.RoundInputs(data, geo)
    probe = UnknownProbe(rnd)
    ce.compute_round(data, geo, observer=lambda record, unknown: probe.step(record), knowledge=False)
    return probe.finish()


def test_a_trip_earns_nothing_while_switched_off_after_its_owner_dies_or_once_the_round_is_decided():
    (live,) = _probe([_door_trip(id=11)])["items"]
    assert live["id"] == 11 and live["up_s"] > 15
    suppressed = {"k": "status", "t": 4.0, "t1": 14.0, "by": 5, "target": 0, "status": "suppressed"}
    (off,) = _probe([_door_trip(), suppressed])["items"]
    assert off["up_s"] <= live["up_s"] - 9.5, "a suppressed Cypher's wire watches nothing (the engine's `off`)"
    (dead,) = _probe([_door_trip()], deaths={0: 6.0})["items"]
    assert dead["up_s"] <= 6.0 and dead["held_m2s"] < live["held_m2s"], "a dead owner's wire holds nothing (Q71)"
    (decided,) = _probe([_door_trip()], t_decided=8.0)["items"]
    assert decided["up_s"] <= 8.0, "nothing after the round was decided"


def test_two_buy_phase_trips_at_zero_each_get_their_own_probe_item():
    util = [_door_trip(t=0.0, id=21),
            toy_ability("Gumshoe", "4_TripWire_SecondWire", 185, 296, 0, t=0.0, t1=20.0, kind="GameObject", id=22),
            toy_ability("Gumshoe", "4_TripWire", 120, 240, 0, t=0.0, t1=20.0, kind="GameObject",
                        end=list(uv(150, 240)), id=23),
            toy_ability("Gumshoe", "4_TripWire_SecondWire", 150, 240, 0, t=0.0, t1=20.0, kind="GameObject", id=24)]
    geo = door_hall()
    data = blob(DOOR_PLAYERS, util=util, t_end=20.0)
    rnd = ce.RoundInputs(data, geo)
    probe = UnknownProbe(rnd)
    ce.compute_round(data, geo, observer=lambda record, unknown: probe.step(record), knowledge=False)
    holds = probe.finish()
    by_id = {i["id"]: i for i in holds["items"]}
    assert set(by_id) == {21, 23}
    out = extract.round_util(ce.RoundInputs(data, geo), data, holds=holds)
    trips = [c for c in out["casts"] if "4_TripWire" in c["names"]]
    assert len(trips) == 2 and all(c["hold"] is not None for c in trips)
    door = next(c for c in trips if (c["u"], c["v"]) == uv(185, 240))
    assert door["hold"]["front_s"] == by_id[21]["front_s"]


def test_the_probe_never_raises():
    probe = UnknownProbe.__new__(UnknownProbe)
    probe.items, probe.prev_t, probe.error = [], None, None
    probe.step(object())                     # a record it can't read
    assert probe.error is not None and probe.finish() == {"error": probe.error, "items": []}


def test_each_probe_item_goes_to_one_cast_of_its_kind():
    from tests.moments.helpers import round_of

    util = [toy_ability("Gumshoe", "4_TripWire", 185, 240, 0, t=-8.0, t1=20.0, kind="GameObject"),
            toy_ability("Gumshoe", "4_TripWire", 120, 240, 0, t=-6.0, t1=20.0, kind="GameObject"),
            toy_ability("Gumshoe", "E_PossessableCamera", 150, 150, 0, t=3.0, t1=20.0, kind="GameObject")]
    players = {0: still("A", 120, 110, 270), 5: still("B", 400, 200, 0)}
    rnd, data = round_of(players, util=util, geo=door_hall(), t_end=20.0)
    item = lambda kind, t0, front: {"what": "device", "kind": kind, "by": 0, "t0": t0, "t1": 20.0,  # noqa: E731
                                    "up_s": 10.0, "front_s": front, "held_m2s": 1.0, "crossed_s": 0.0}
    holds = {"error": None, "items": [item("trip", 0.0, 4.0), item("trip", 0.0, 6.0), item("camera", 3.0, 9.0)]}
    out = extract.round_util(rnd, data, holds=holds)
    fronts = sorted(c["hold"]["front_s"] for c in out["casts"] if c["hold"])
    assert fronts == [4.0, 6.0, 9.0], "one item each, never a sum"
    (cam,) = [c for c in out["casts"] if "E_PossessableCamera" in c["names"]]
    assert cam["hold"]["front_s"] == 9.0


def _pulse_run(*, enemies=5, seen=False, kind="exclude", shared=False, ignored=False, decided=12.0,
               cache_path=None):
    from app.control.utility import Info
    from app.gaps.cache import Writer

    geo = door_hall()
    players = {0: still("A", 120, 110, 270),
               **{s: still("B", 400, 200, 0) for s in range(5, 5 + enemies)}}
    data = blob(players, t_end=12.0, t_decided=decided)
    rnd = ce.RoundInputs(data, geo)
    mask = (geo.centres[:, 0] > 240) & (geo.centres[:, 0] < 320) & geo.walk_n
    infos = [Info(8.0, "A", s, kind, "toy", x=280, y=200, mask=mask, source="unrelated text", cast_id=actor,
                  row_key=(0, "Hunter", "Q_RevealBolt", 8.0))
             for actor in ([101, 102] if shared else [101]) for s in range(5, 5 + enemies)
             if not shared or actor == (101 if s % 2 else 102)]
    if ignored:
        # The engine refuses an exclude that would leave an enemy nowhere.
        for operation in infos:
            operation.mask = geo.walk_n.copy()
    probe = UnknownProbe(rnd)
    writer = Writer(cache_path) if cache_path else None
    previous, expected = {}, []

    def observe(rec, unknown):
        if writer:
            writer(rec, unknown)
        before = np.zeros(geo.n, bool)
        after = np.zeros(geo.n, bool)
        footprint = np.zeros(geo.n, bool)
        for arr in previous.values():
            before |= arr >= 0
        for arr in rec.unknown["A"].values():
            after |= arr >= 0
        for _, _, operation, _, nodes in rec.applied["A"]:
            if operation in ("exclude", "restrict", "locate"):
                footprint[list(nodes)] = True
        if seen and rec.applied["A"]:
            # A teammate sees the very ground on this record; utility gets no credit for it.
            rec.players[0].view = footprint.copy()
        visible = np.zeros(geo.n, bool)
        for p in rec.players.values():
            if p.team == "A" and p.view is not None:
                visible |= p.view
        if rec.t < decided:
            expected.append(geo.area(before & ~after & footprint & ~visible) * geo.cell_m ** 2)
        probe.step(rec)
        previous.clear()
        previous.update({s: arr.copy() for s, arr in rec.unknown["A"].items()})

    ce.compute_round(data, geo, infos=infos, observer=observe, knowledge=False)
    if writer:
        writer.close()
    return probe.finish(), sum(expected), rnd


def test_empty_pulse_counts_the_union_once_for_five_enemies_and_splits_shared_casts():
    single, area, _ = _pulse_run()
    assert area > 0
    assert sum(m["cleared_m2"] for m in single["info"]) == round(area, 1)
    shared, shared_area, _ = _pulse_run(shared=True)
    by_cast = {m["cast_id"]: m["cleared_m2"] for m in shared["info"]}
    assert by_cast == {101: round(shared_area / 2, 1), 102: round(shared_area / 2, 1)}


@pytest.mark.parametrize("options", [{"seen": True}, {"ignored": True}, {"kind": "broaden"},
                                    {"kind": "hypothesis"}, {"decided": 7.0}])
def test_seen_ignored_additive_and_after_decided_operations_never_clear(options):
    holds, _, _ = _pulse_run(**options)
    assert holds["info"] is not None
    assert sum(m["cleared_m2"] for m in holds["info"]) == 0


def _controlled(kind="camera", *, decided=20.0, static=False):
    from app.critutil.unknown import InfoMeasure

    geo = door_hall()
    row = toy_ability("Gumshoe", "E_PossessableCamera", 150, 150, 0, t=0, t1=20,
                      id=301, kind="GameObject", yaw=0, possessed=[[0, 20]])
    data = blob(DOOR_PLAYERS, util=[row], t_end=20, t_decided=decided)
    rnd = ce.RoundInputs(data, geo)
    rnd.watchers[0].kind = kind
    if kind == "turret":
        rnd.watchers[0].t0 = 0.5
    mask = np.zeros(geo.n, bool)
    mask[geo.cell_of_px(160, 200)] = True
    if kind in ("trip", "area"):
        rnd.watchers[0].cells = np.flatnonzero(mask)
    if static:
        rnd.watchers.append(ce.Watcher("trip", 0, 0, 20, cells=np.flatnonzero(mask), src=302))
    probe = UnknownProbe(rnd)
    measure = InfoMeasure(probe)
    ring = probe.topo.dilate(mask) & ~mask
    return probe, measure, mask, ring


def _record(t, mask, unknown, *, applied=(), seen=None, enemies=1):
    return SimpleNamespace(t=t, players={0: SimpleNamespace(team="A", utility=mask,
                           view=np.zeros(len(mask), bool) if seen is None else seen)},
                           unknown={"A": {5 + s: np.where(unknown, 0, -1) for s in range(enemies)}, "B": {}},
                           applied={"A": list(applied), "B": []})


@pytest.mark.parametrize("kind", ["camera", "drone", "turret"])
def test_dynamic_watcher_clear_then_hold_and_turret_only_clears_once(kind):
    probe, measure, mask, ring = _controlled(kind)
    edge = np.zeros(len(mask), bool)
    edge[np.flatnonzero(ring)[0]] = True
    # Reuse and mutate the region array, as the engine does.
    first = _record(0, mask, mask | edge)
    measure.step(first)
    first.unknown["A"][5][mask] = -1
    first.t = 1
    measure.step(first)
    (m,) = measure.finish()
    assert m["cleared_m2"] > 0 and m["hold_s"] == 0
    area = probe.rnd.geo.area(mask) * probe.cell_m2
    assert m["cleared_m2"] == round(area, 1)
    measure.step(_record(2, mask, edge))
    assert measure.finish()[0]["hold_s"] == 1
    measure.step(_record(3, mask, mask | edge))
    measure.step(_record(4, mask, edge))
    (m,) = measure.finish()
    assert m["cleared_m2"] == round(area * (1 if kind == "turret" else 2), 1)
    assert m["hold_s"] == (2 if kind == "turret" else 1)


@pytest.mark.parametrize("kind", ["trip", "area", "camera", "drone", "turret"])
def test_hold_needs_known_ground_past_the_edge_live_enemies_and_live_time(kind):
    _, measure, mask, ring = _controlled(kind, decided=5)
    measure.step(_record(0, mask, ring))
    measure.step(_record(1, mask, ring))
    assert measure.finish()[0]["hold_s"] == 0, "unknown on both sides cannot be held"
    partial = ring.copy()
    partial[np.flatnonzero(ring)[0]] = False
    measure.step(_record(2, mask, partial))
    assert measure.finish()[0]["hold_s"] == 1
    measure.step(_record(3, mask, partial, enemies=0))
    measure.step(_record(5, mask, partial))
    assert measure.finish()[0]["hold_s"] == 1


def test_camera_footprint_subtracts_its_owners_static_watchers():
    _, measure, mask, ring = _controlled(static=True)
    measure.step(_record(0, mask, mask | ring))
    measure.step(_record(1, mask, ring))
    camera = [m for m in measure.finish() if m["id"] == 301]
    assert sum(m["cleared_m2"] + m["hold_s"] for m in camera) == 0


@pytest.mark.parametrize("kind", ["exclude", "restrict", "locate"])
def test_applied_identity_credits_each_clear_kind_and_only_locates_unseen_enemies(kind):
    probe, measure, mask, ring = _controlled("trip")
    measure.step(_record(0, mask, mask | ring, enemies=2))
    op = [(401, (0, "Hunter", "Q_RevealBolt", 1.0), kind, 5, tuple(np.flatnonzero(mask)))]
    measure.step(_record(1, mask, ring, applied=op, enemies=2))
    credited = next(m for m in measure.finish() if m["cast_id"] == 401)
    assert credited["cleared_m2"] == round(probe.rnd.geo.area(mask) * probe.cell_m2, 1)
    assert credited["located"] == (1 if kind == "locate" else 0)
    # A seen locate is excluded even when the applied transport contains one.
    _, measure, mask, ring = _controlled("trip")
    measure.step(_record(0, mask, mask | ring))
    measure.step(_record(1, mask, mask, applied=op, seen=mask))
    assert all(m["located"] == 0 for m in measure.finish())


def test_info_failure_preserves_legacy_items_and_marks_info_unknown(monkeypatch):
    from app.critutil.unknown import InfoMeasure

    baseline = _probe([_door_trip()])
    def broken(self, rec):
        raise ValueError("info failed")
    monkeypatch.setattr(InfoMeasure, "step", broken)
    failed = _probe([_door_trip()])
    assert failed["items"] == baseline["items"] and failed["error"] is None
    assert failed["info"] is None


def test_a_drone_never_joins_legacy_items_or_changes_another_devices_hold():
    drone = toy_ability("Hunter", "E_Drone", 150, 150, 0, t=1, t1=10, kind="Pawn", id=502,
                        path=[[1, *uv(150, 150)], [10, *uv(150, 150)]], possessed=[[1, 10]], yaw=0)
    with_drone, rnd, data = run([_door_trip(id=501), drone], DOOR_PLAYERS)
    without, _, _ = run([_door_trip(id=501)], DOOR_PLAYERS)
    # Compare the same engine records with the drone removed only from the reporting probe.
    rnd.watchers = [w for w in rnd.watchers if w.kind != "drone"]
    reference = UnknownProbe(rnd)
    ce.compute_round(data, rnd.geo, observer=lambda rec, unknown: reference.step(rec), knowledge=False)
    assert with_drone["items"] == reference.finish()["items"]
    assert len(with_drone["items"]) == len(without["items"]) == 1
    out = extract.round_util(rnd, data, holds=with_drone)
    cast = next(c for c in out["casts"] if c["ability_id"] == "Sova/Owl Drone")
    assert cast["hold"] is None and cast["info"] is not None


def test_cached_replay_and_live_probe_give_equal_figures(tmp_path):
    from app.gaps import cache

    path = tmp_path / "info.ticks.pkl.gz"
    live, area, rnd = _pulse_run(cache_path=path)
    assert area > 0 and cache.FORMAT == 2
    cached = UnknownProbe(rnd)
    for rec, _ in cache.replay(path):
        cached.step(rec)
    assert cached.finish() == live


def test_two_actor_rows_of_one_cast_share_cells_as_one_cast():
    from app.critutil.unknown import InfoMeasure

    probe, _, mask, ring = _controlled("trip")
    probe.rnd.blob["util"] = [
        toy_ability("Hunter", "Q_RevealBolt", 280, 200, 0, t=0, t1=5, id=401, kind="Projectile"),
        toy_ability("Hunter", "Q_SonarBolt", 280, 200, 0, t=0.5, t1=5, id=402, kind="GameObject")]
    measure = InfoMeasure(probe)
    measure.step(_record(0, mask, mask | ring, enemies=3))
    ops = [(actor, None, "exclude", 5 + i, tuple(np.flatnonzero(mask)))
           for i, actor in enumerate((401, 402, 403))]
    measure.step(_record(1, mask, ring, applied=ops, enemies=3))
    clears = [m["cleared_m2"] for m in measure.finish() if m["cleared_m2"]]
    area = probe.rnd.geo.area(mask) * probe.cell_m2
    assert clears == [round(area / 2, 1), round(area / 2, 1)]


@pytest.mark.parametrize("kind", ["locate", "restrict"])
def test_real_engine_locates_and_restrictions_reach_the_applied_cast(kind):
    holds, area, _ = _pulse_run(kind=kind)
    (m,) = holds["info"]
    assert m["cast_id"] == 101 and m["cleared_m2"] == round(area, 1)
    assert m["located"] == (5 if kind == "locate" else 0)


def test_a_count_operation_never_counts_as_a_locate():
    _, measure, mask, ring = _controlled("trip")
    measure.step(_record(0, mask, mask | ring))
    measure.step(_record(1, mask, ring, applied=[(401, None, "one_of", 5, (0,))]))
    assert all(m["located"] == 0 for m in measure.finish())


def test_camera_dart_and_camera_footprints_share_as_one_information_cast():
    from app.critutil.unknown import InfoMeasure

    probe, _, mask, ring = _controlled()
    probe.rnd.blob["util"].append(toy_ability("Gumshoe", "E_CameraTrackingDart", 280, 200, 0,
                                            t=0.5, t1=5, id=402, kind="Projectile"))
    measure = InfoMeasure(probe)
    measure.step(_record(0, mask, mask | ring, enemies=2))
    nodes = tuple(np.flatnonzero(mask))
    measure.step(_record(1, mask, ring, enemies=2,
                         applied=[(402, None, "exclude", 5, nodes), (403, None, "exclude", 6, nodes)]))
    clears = sorted(m["cleared_m2"] for m in measure.finish() if m["cleared_m2"])
    area = probe.rnd.geo.area(mask) * probe.cell_m2
    assert clears == [round(area / 2, 1), round(area / 2, 1)]


def test_clear_uses_the_whole_sides_previous_union():
    probe, measure, mask, ring = _controlled("trip")
    previous = _record(0, mask, ring)
    previous.unknown["A"][6] = np.where(mask, 0, -1)
    measure.step(previous)
    # Enemy 6 is gone, while a live enemy's applied exclude also removed this ground this tick.
    measure.step(_record(1, mask, ring,
                         applied=[(401, None, "exclude", 5, tuple(np.flatnonzero(mask)))]))
    clear = next(m for m in measure.finish() if m["cast_id"] == 401)
    assert clear["cleared_m2"] == round(probe.rnd.geo.area(mask) * probe.cell_m2, 1)


@pytest.mark.parametrize("kind", ["trip", "camera"])
def test_two_holding_watchers_of_one_cast_count_each_tick_once(kind):
    from dataclasses import replace

    probe, _, mask, ring = _controlled(kind)
    probe.rnd.watchers.append(replace(probe.rnd.watchers[0]))
    measure = UnknownProbe(probe.rnd).info
    edge = ring.copy()
    edge[np.flatnonzero(ring)[0]] = False
    for t in range(5):
        measure.step(_record(t, mask, edge))
    (m,) = measure.finish()
    assert m["hold_s"] == 4.0


def test_a_dynamic_clear_suppresses_all_holds_joined_to_its_cast():
    from dataclasses import replace

    probe, _, mask, ring = _controlled()
    other = np.zeros(probe.n, bool)
    other[probe.rnd.geo.cell_of_px(184, 200)] = True
    probe.rnd.watchers.append(replace(probe.rnd.watchers[0], kind="area", cells=np.flatnonzero(other)))
    measure = UnknownProbe(probe.rnd).info
    edge = np.zeros(probe.n, bool)
    edge[np.flatnonzero(ring)[0]] = True
    other_ring = probe.topo.dilate(other) & ~other
    edge[np.flatnonzero(other_ring)[0]] = True
    measure.step(_record(0, mask, mask | edge))
    measure.step(_record(1, mask, edge))
    (m,) = measure.finish()
    assert m["cleared_m2"] > 0 and m["hold_s"] == 0


def test_finish_rounds_plain_json_numbers_without_rounding_running_totals():
    import json

    _, measure, _, _ = _controlled()
    key = measure._entry(cast_id=401, t=1.234567)
    raw = {"cleared_m2": np.float64(12.345678), "hold_s": np.float64(0.0049), "located": np.int64(2)}
    measure.measures[key].update(raw)
    (first,) = measure.finish()
    assert first["cleared_m2"] == 12.3 and first["hold_s"] == 0.0 and first["t0"] == 1.23
    assert type(first["cleared_m2"]) is type(first["hold_s"]) is type(first["t0"]) is float
    assert type(first["located"]) is int
    assert json.loads(json.dumps([first])) == [first]
    assert {k: measure.measures[key][k] for k in raw} == raw
    measure.measures[key]["hold_s"] += 0.0049
    assert measure.finish()[0]["hold_s"] == 0.01
    assert measure.measures[key]["hold_s"] == pytest.approx(0.0098)
