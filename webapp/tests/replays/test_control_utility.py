"""Knowledge operations on one enemy's unknown at exact times (W09; app/control/utility.py): locate, restrict,
exclude, hypothesis, broaden, pause/resume; the three clocks (stored frames, analytical instants, exact
transitions); and the gap detector's judging of a locate that has its own record."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from app.control.encode import encode_data
from app.gaps.detect import judged_against_before
from app.replays import control_format as cf
from tests.replays.control_toys import HALL, blob, door_hall, open_hall, toy_geometry
from tests.replays.control_toys import toy_ability, uv

# A west of everything, facing the west wall: it sees almost nothing of the hall. B5 and B6 stand east, out of view.
A_HIDDEN = ("A", [(0.0, 104, 200, 180)])


def snapshots(b, geo, infos=(), ticks=None, **kw):
    """(RoundControl, {t: {side: {enemy: finite reached mask}}}, {t: (events, reasons, protect, cleared)})"""
    regions, extra = {}, {}

    class Watch:
        def on_tick(self, tick, unknown):
            regions[round(tick.t, 6)] = {side: {s: np.isfinite(r) for s, r in unknown.reached[side].items()}
                                         for side in ("A", "B")}
            extra[round(tick.t, 6)] = ({k: list(v) for k, v in unknown.events.items()},
                                       {k: list(v) for k, v in unknown.reasons.items()},
                                       copy.deepcopy(unknown.protect), {k: set(v) for k, v in unknown.cleared.items()})

    rc = ce.compute_round(b, geo, ticks=ticks, knowledge=False, observer=Watch(), infos=list(infos), **kw)
    return rc, regions, extra


def info(t, kind, x=None, y=None, r=None, slot=5, side="A", reason="test", source="", **kw):
    return ut.Info(t, side, slot, kind, reason, x=x, y=y, radius_m=r, source=source, **kw)


def hall_round(t_end=8.0, more=None):
    players = {0: A_HIDDEN, 5: ("B", [(0.0, 400, 200, 0)])}
    players.update(more or {})
    return blob(players, t_end=t_end)


COUNT_READERS = ("nightfall_counts", "dizzy_counts", "thrash_detains", "storm_surges")


def count_rows(reader, targets=(5, 6), *, source=41, t=1.0):
    code, name, kind = {
        "nightfall_counts": ("BountyHunter", "X_WaveForm", "GameObject"),
        "dizzy_counts": ("Aggrobot", "E_DiscTurret_PowerWave", "GameObject"),
        "thrash_detains": ("Aggrobot", "RollyPolly", "Pawn"),
        "storm_surges": ("Mage", "4_SplashGrenade", "GameObject"),
    }[reader]
    cast = toy_ability(code, name, 128 if reader == "nightfall_counts" else 400, 200, 0,
                       t=t, t1=t + .5, kind=kind, id=source, hits_read=True, fired=True, yaw=0)
    if reader == "thrash_detains":
        cast.update(u=uv(104, 200)[0], path=[[t, *uv(104, 200)], [t + .5, *uv(400, 200)]])
    if reader == "storm_surges":
        # The parser's actual condensed nearsight shape: no src, code, name or target field.
        hits = [{"k": "nearsight", "t": t, "by": 0, "ability": "harbor_storm_surge",
                 "targets": list(targets), "hits": [[s, t + .5, 2.0] for s in targets],
                 "u": cast["u"], "v": cast["v"]}] if targets else []
    else:
        status = {"nightfall_counts": "decayed", "dizzy_counts": "concussed",
                  "thrash_detains": "detained"}[reader]
        hits = [{"k": "status", "t": t + .5, "t1": t + 2, "by": 0, "target": s,
                 "code": code, "name": name, "status": status, "from": "force", "src": source}
                for s in targets]
    return [cast, *hits]


def count_round(reader, targets=(5, 6), **kwargs):
    b = hall_round(more={6: ("B", [(0.0, 400, 200, 0)])})
    b["util"] = count_rows(reader, targets, **kwargs)
    return ce.RoundInputs(b, open_hall(), infos=[])


@pytest.mark.parametrize("reader", COUNT_READERS)
def test_count_reader_groups_distinct_enemy_hits_without_naming_them(reader, monkeypatch):
    rnd = count_round(reader, (5, 6, 5))
    original_pos = rnd.pos

    def no_enemy_position(slot, t):
        assert rnd.team[slot] == "A", "a count reader looked up a hit enemy's position"
        return original_pos(slot, t)

    monkeypatch.setattr(rnd, "pos", no_enemy_position)
    monkeypatch.setattr(rnd, "height", lambda *args: pytest.fail("a count reader read target height"))
    [hit] = getattr(ut, "read_" + reader)(rnd)
    assert (hit.kind, hit.slot, hit.count, hit.side) == ("one_of", ut.ONE_OF_SLOT, 2, "A")
    assert hit.detail == {"needs": "status"}
    assert hit.cast_id == 41 and hit.row_key == ut._identity(rnd.blob["util"][0])["row_key"]
    assert hit.t == 1.5
    assert (hit.mask is not None) == (reader in ("nightfall_counts", "dizzy_counts"))
    if reader == "thrash_detains":
        assert (hit.x, hit.y, hit.radius_m) == pytest.approx((400, 200, 5), abs=.1)
    if reader == "storm_surges":
        assert hit.radius_m == 6
    admitted = ce.RoundInputs(rnd.blob, rnd.geo).infos
    assert any(i.cast_id == 41 and i.kind == "one_of" and i.count == 2 for i in admitted)


@pytest.mark.parametrize("reader", COUNT_READERS)
@pytest.mark.parametrize("proof", ["complete", "old", "unfired", "false_stamp"])
def test_count_reader_zero_requires_complete_fired_source(reader, proof):
    rnd = count_round(reader, ())
    cast = rnd.blob["util"][0]
    if proof == "old":
        cast.pop("hits_read")
    elif proof == "unfired":
        cast.pop("fired")
    elif proof == "false_stamp":
        cast["hits_read"] = False
    hits = getattr(ut, "read_" + reader)(rnd)
    if proof != "complete":
        assert hits == []
        return
    assert [i.slot for i in hits] == [5, 6]
    assert all(i.kind == "exclude" and i.detail == {"needs": "status"} for i in hits)
    assert all(i.cast_id == 41 and i.row_key == ut._identity(cast)["row_key"] for i in hits)


@pytest.mark.parametrize("reader", COUNT_READERS)
def test_count_reader_retains_positive_hits_without_zero_proof(reader):
    rnd = count_round(reader)
    rnd.blob["util"][0].pop("hits_read")
    rnd.blob["util"][0].pop("fired")
    [hit] = getattr(ut, "read_" + reader)(rnd)
    assert hit.kind == "one_of" and hit.count == 2


@pytest.mark.parametrize("reader", COUNT_READERS)
def test_count_reader_keeps_same_owner_casts_separate(reader):
    rnd = count_round(reader, (5,))
    rnd.blob["util"].extend(count_rows(reader, (5, 6), source=42,
                                      t=2.0 if reader == "storm_surges" else 1.0))
    hits = getattr(ut, "read_" + reader)(rnd)
    assert [(i.cast_id, i.count) for i in hits] == [(41, 1), (42, 2)]
    assert hits[0].source != hits[1].source


@pytest.mark.parametrize("reader", COUNT_READERS)
@pytest.mark.parametrize("targets", [(), (5, 6)])
def test_count_reader_preserves_evolution_region(reader, targets):
    rnd = count_round(reader, targets)
    next(p for p in rnd.blob["players"] if p["slot"] == 6)["agent"] = "Veto"
    rnd.blob["util"].append({"k": "buff", "t": 0.0, "t1": 8.0, "by": 6, "target": 6,
                             "code": "Pine", "name": "X_Evolution"})
    operations = ce.RoundInputs(rnd.blob, rnd.geo).infos
    assert not any(i.kind == "exclude" and i.slot == 6 for i in operations)
    _, actual = suspension_unknown(operations, (400, 400), immune=(6,), t=operations[0].t)
    _, baseline = suspension_unknown([], (400, 400), immune=(6,), t=operations[0].t)
    np.testing.assert_array_equal(actual.reached["A"][6], baseline.reached["A"][6])
    assert actual.events["A"] == []


@pytest.mark.parametrize("targets", [(), (5, 6)])
@pytest.mark.parametrize("unheard", ["far", "dead", "deaf"])
def test_storm_surge_requires_a_live_listening_teammate(targets, unheard, monkeypatch):
    rnd = count_round("storm_surges", targets)
    if unheard == "far":
        monkeypatch.setattr(rnd, "pos", lambda slot, t: (-10000, -10000, 0))
    elif unheard == "dead":
        monkeypatch.setattr(rnd, "alive", lambda slot, t: slot != 0)
    else:
        rnd.nearsight[0] = [(0, 8)]
    assert ut.read_storm_surges(rnd) == []


def test_storm_surge_reads_whirlpool_only_and_matches_owner_time_place():
    rnd = count_round("storm_surges")
    cast, hit = rnd.blob["util"]
    rnd.blob["util"].insert(0, dict(cast, kind="Projectile", id=40, fired=False))
    rnd.blob["util"].extend([dict(hit, by=5), dict(hit, t=6, hits=[[5, 6, 2]]),
                             dict(hit, t=6, u=uv(100, 200)[0])])
    [out] = ut.read_storm_surges(rnd)
    assert (out.cast_id, out.count) == (41, 2)


@pytest.mark.parametrize("offset", ["time", "place"])
@pytest.mark.parametrize("matched_targets", [(), (5,)])
def test_storm_surge_unmatched_hit_blocks_nearby_count_and_zero(offset, matched_targets):
    rnd = count_round("storm_surges", matched_targets)
    _, hit = count_rows("storm_surges", (6,))
    if offset == "time":
        hit["t"] += .5
    else:
        hit["u"] += 3
    rnd.blob["util"].append(hit)
    assert ut.read_storm_surges(rnd) == []


def test_storm_surge_unmatched_hit_blocks_only_nearby_casts_of_the_same_owner():
    rnd = count_round("storm_surges", ())
    _, orphan = count_rows("storm_surges", (5,))
    orphan["t"] += .5
    other_owner = dict(rnd.blob["util"][0], by=6, id=43)
    rnd.blob["util"].extend([orphan, other_owner, *count_rows("storm_surges", (5,), source=44, t=2),
                             *count_rows("storm_surges", (5, 6), source=42, t=6)])
    out = ut.read_storm_surges(rnd)
    assert [(i.cast_id, i.kind, i.count) for i in out] == [(43, "exclude", 1), (42, "one_of", 2)]


@pytest.mark.parametrize("offset", ["time", "place", "ambiguous"])
def test_storm_surge_unmatched_row_without_hits_does_not_block_zero(offset):
    rnd = count_round("storm_surges", ())
    _, row = count_rows("storm_surges", (5,))
    row.update(targets=[], hits=[])
    if offset == "time":
        row["t"] += .5
    elif offset == "place":
        row["u"] += 3
    else:
        rnd.blob["util"].append(dict(rnd.blob["util"][0], id=42))
    rnd.blob["util"].append(row)
    out = ut.read_storm_surges(rnd)
    assert [i.cast_id for i in out] == ([41, 41, 42, 42] if offset == "ambiguous" else [41, 41])
    assert all(i.kind == "exclude" for i in out)


def test_storm_surge_combines_hit_rows_and_refuses_ambiguous_whirlpools():
    rnd = count_round("storm_surges", (5,))
    cast, hit = rnd.blob["util"]
    rnd.blob["util"].append(dict(hit, targets=[6], hits=[[6, 1.5, 2]]))
    [out] = ut.read_storm_surges(rnd)
    assert out.count == 2
    rnd.blob["util"].append(dict(cast, id=42))
    assert ut.read_storm_surges(rnd) == []


@pytest.mark.parametrize("yaw,inside,outside", [(0, (184, 200), (112, 200)),
                                               (90, (128, 256), (128, 144))])
def test_nightfall_uses_its_oriented_wave_rectangle(yaw, inside, outside, monkeypatch):
    rnd = count_round("nightfall_counts")
    rnd.blob["util"][0]["yaw"] = yaw
    monkeypatch.setitem(ut.FIGURES, "nightfall", {"length_m": 10, "width_m": 10})
    [out] = ut.read_nightfall_counts(rnd)
    assert out.mask[rnd.geo.node_at_px(*inside)]
    assert not out.mask[rnd.geo.node_at_px(*outside)]
    assert not out.mask[rnd.geo.node_at_px(240, 248)]


def test_dizzy_count_is_limited_by_sight_and_range(monkeypatch):
    rnd = count_round("dizzy_counts")
    rnd.geo = door_hall()
    [out] = ut.read_dizzy_counts(rnd)
    assert not out.mask[rnd.geo.node_at_px(112, 200)]
    assert out.mask[rnd.geo.node_at_px(344, 200)]
    monkeypatch.setitem(ut.FIGURES, "dizzy_range_m", 10)
    [out] = ut.read_dizzy_counts(rnd)
    assert out.mask[rnd.geo.node_at_px(344, 200)]
    assert not out.mask[rnd.geo.node_at_px(280, 200)]


def test_count_reader_does_not_invent_missing_direction_or_detain_place():
    rnd = count_round("nightfall_counts")
    rnd.blob["util"][0].pop("yaw")
    assert ut.read_nightfall_counts(rnd) == []
    rnd = count_round("thrash_detains")
    rnd.blob["util"][0].pop("path")
    assert ut.read_thrash_detains(rnd) == []


@pytest.mark.parametrize("reader", COUNT_READERS[:3])
def test_status_count_reader_ignores_unrelated_and_friendly_rows(reader):
    rnd = count_round(reader, ())
    _, hit = count_rows(reader, (5,))
    rnd.blob["util"].extend([dict(hit, friendly=True),
                             dict(hit, by=6), dict(hit, status="unrelated")])
    out = getattr(ut, "read_" + reader)(rnd)
    assert [i.kind for i in out] == ["exclude", "exclude"]


@pytest.mark.parametrize("reader", COUNT_READERS[:3])
@pytest.mark.parametrize("src", [999, None])
@pytest.mark.parametrize("matched_targets", [(), (5,)])
def test_orphaned_status_hit_blocks_nearby_cast_of_its_source(reader, src, matched_targets):
    rnd = count_round(reader, matched_targets)
    _, orphan = count_rows(reader, (6,))
    orphan["src"] = src
    # An ability with this id but a different source does not resolve the hit.
    rnd.blob["util"].extend([orphan, toy_ability("Hunter", "Q_SonarPing", 400, 200, 0, t=1, id=999)])
    assert getattr(ut, "read_" + reader)(rnd) == []


@pytest.mark.parametrize("reader", COUNT_READERS[:3])
def test_orphaned_status_hit_leaves_other_owner_and_distant_cast_usable(reader):
    rnd = count_round(reader, ())
    _, orphan = count_rows(reader, (5,))
    orphan["src"] = 999
    other_owner = dict(rnd.blob["util"][0], by=6, id=43)
    rnd.blob["util"].extend([orphan, other_owner, *count_rows(reader, (5, 6), source=42, t=6)])
    out = getattr(ut, "read_" + reader)(rnd)
    assert [(i.cast_id, i.kind, i.count) for i in out] == [(43, "exclude", 1), (42, "one_of", 2)]


@pytest.mark.parametrize("reader", COUNT_READERS[:3])
@pytest.mark.parametrize("end", [6, None])
def test_orphaned_status_hit_blocks_cast_throughout_its_lifetime(reader, end):
    rnd = count_round(reader, ())
    rnd.blob["util"][0]["t1"] = end
    _, orphan = count_rows(reader, (5,))
    orphan.update(src=999, t=5.5)
    rnd.blob["util"].append(orphan)
    assert getattr(ut, "read_" + reader)(rnd) == []


def suspension_rows(by=0, source="zone-1", t=1.2):
    return [toy_ability("Paprika", "X_LevitateZone", 400, 200, by, t=1.0, id=source),
            {"k": "status", "t": t, "t1": t + .8, "by": by, "target": 5 if by == 0 else 0,
             "code": "Paprika", "name": "X_LevitateZone", "status": "levitated", "from": "force", "src": source}]


@pytest.mark.parametrize("case", ["enemy", "friendly", "none", "pulled", "two_zones", "two_hushes"])
def test_control_suspension_reader_binds_enemy_hits_to_their_zone(case):
    rows = suspension_rows()
    if case == "friendly":
        rows[1]["friendly"] = True
    elif case == "none":
        rows.pop()
    elif case == "pulled":
        rows[1]["status"] = "pulled"
    elif case in ("two_zones", "two_hushes"):
        rows.insert(0, suspension_rows(by=5 if case == "two_hushes" else 0, source="zone-2")[0])
    if case == "enemy":
        rows.append(dict(rows[1], t=1.1))
    b = hall_round()
    b["util"] = rows
    rnd = ce.RoundInputs(b, open_hall())
    out = ut.read_hush_suspensions(rnd)
    if case in ("friendly", "none", "pulled"):
        assert out == []
    else:
        [hit] = out
        assert (hit.t, hit.side, hit.slot, hit.kind, hit.reason) == (
            1.1 if case == "enemy" else 1.2, "A", -1, "one_of", "hush_suspension")
        assert (hit.x, hit.y) == pytest.approx((400, 200), abs=.1)
        assert hit.radius_m == 7.0 and hit.source == f"Paprika_X_LevitateZone@{hit.t}"


@pytest.mark.parametrize("kind,slot", [("one_of", 3), ("restrict", -1)])
def test_control_side_wide_info_requires_its_reserved_slot(kind, slot):
    with pytest.raises(ValueError):
        info(1.0, kind, slot=slot)


def one_of(t=1.2, **kw):
    return info(t, "one_of", 400, 200, r=7.0, slot=-1, reason="hush_suspension", source="zone", **kw)


def suspension_unknown(infos, places=(400, 200), *, dead=(), immune=(), seen=(), seed_t=0.0, t=1.2, holders=None):
    """No hidden position pushes: seed regions, then exercise the real Unknown.apply ordering and spread."""
    geo = open_hall()
    unknown = ce.Unknown(geo)
    slots = range(5, 5 + len(places))
    for slot, x in zip(slots, places):
        reached = np.full(geo.n, np.inf)
        reached[ut.footprint(info(0, "restrict", x, 200, r=5), geo)] = seed_t
        unknown.reached["A"][slot] = reached
    unknown._prev_t = 1.0
    for slot, x, spotted_at in seen:
        unknown.seen["A"][slot] = (geo.node_at_px(x, 200), spotted_at)
    for side in ("A", "B"):
        unknown.free_since[side] = np.zeros(geo.n)
        unknown._room[side] = geo.walk_n.copy()
    rnd = SimpleNamespace(team={0: "A", **dict.fromkeys(slots, "B")}, alive=lambda s, t: s not in dead,
                          watchers=[], infos=ut.ordered(infos), evolution={s: [(0, 9)] for s in immune})
    unknown.apply(SimpleNamespace(t=t, holders=holders or {}, rnd=rnd))
    return geo, unknown


@pytest.mark.parametrize("places", [(400, 200), (400, 400)])
def test_anonymous_count_one_matches_default_one_of_array_for_array(places):
    _, old = suspension_unknown([one_of()], places)
    _, actual = suspension_unknown([one_of(count=1)], places)
    for slot in (5, 6):
        np.testing.assert_array_equal(actual.reached["A"][slot], old.reached["A"][slot])
    assert actual.reasons == old.reasons
    assert actual.protect == old.protect
    assert actual.applied == old.applied
    assert actual.ignored == old.ignored


@pytest.mark.parametrize("places", [(400, 200), (400, 400)])
@pytest.mark.parametrize("masked", [False, True])
def test_anonymous_count_two_restricts_each_candidate_and_reports_cast(places, masked):
    key = (0, "Paprika", "X_LevitateZone", 1.0)
    hit = one_of(count=2, cast_id=41, row_key=key)
    if masked:
        hit.mask = ut.footprint(hit, open_hall())
    geo, actual = suspension_unknown([hit], places)
    _, baseline = suspension_unknown([], places)
    fp = ut.footprint(hit, geo)
    candidates = [5] if places[1] == 200 else [5, 6]
    assert [r[1:3] for r in actual.reasons["A"]] == [(s, "restrict") for s in candidates]
    for slot in candidates:
        assert not np.isfinite(actual.reached["A"][slot][~fp]).any()
    assert actual.applied["A"] == [
        (41, key, "restrict", s, tuple(np.flatnonzero(np.isfinite(baseline.reached["A"][s]) & ~fp)))
        for s in candidates]
    assert not actual.protect["A"]
    assert actual.events["A"] == []


@pytest.mark.parametrize("count,inside", [(1, False), (2, False), (2, True)])
def test_anonymous_count_keeps_known_outside_enemies_whose_regions_reach_footprint(count, inside):
    # A locate followed by a broaden keeps a known point while adding possible ground in F.
    # Start the late pass before these operations so the reference uses identical ordering.
    prefix = [info(1.2, "one_of", 100, 100, r=1, slot=-1, source="miss"),
              info(1.2, "locate", 400 if inside else 200, 200, slot=5),
              info(1.2, "broaden", 400, 200, r=5, slot=5)]
    hit = one_of(count=count)
    geo, baseline = suspension_unknown(prefix, (400, 400))
    fp = ut.footprint(hit, geo)
    known_node = geo.node_at_px(400 if inside else 200, 200)
    assert bool(fp[known_node]) == inside
    assert np.isfinite(baseline.reached["A"][5][known_node])
    assert (np.isfinite(baseline.reached["A"][5]) & fp).any()

    candidates = [6] if inside else [5, 6]
    kind = "hypothesis" if count == 1 else "restrict"
    # The old count-one rule selects every region reaching F, even if its known point is outside.
    reference_ops = [info(hit.t, kind, hit.x, hit.y, r=hit.radius_m, slot=s,
                          reason=hit.reason, source=hit.source) for s in candidates]
    _, expected = suspension_unknown([*prefix, *reference_ops], (400, 400))
    _, actual = suspension_unknown([*prefix, hit], (400, 400))
    assert [r[1:3] for r in actual.reasons["A"] if r[3] == hit.reason] == [
        (s, kind) for s in candidates]
    for slot in (5, 6):
        np.testing.assert_array_equal(actual.reached["A"][slot], expected.reached["A"][slot])
    assert actual.protect == expected.protect
    assert actual.applied == expected.applied


def test_anonymous_count_subtracts_seen_enemies_before_selecting_candidates():
    locate = info(1.2, "locate", 400, 200, slot=5)
    hit = one_of(count=2, cast_id=41)
    _, actual = suspension_unknown([locate, hit], (400, 400, 400))
    _, baseline = suspension_unknown([locate], (400, 400, 400))
    assert [r[1:3] for r in actual.reasons["A"] if r[3] == hit.reason] == [
        (6, "hypothesis"), (7, "hypothesis")]
    np.testing.assert_array_equal(actual.reached["A"][5], baseline.reached["A"][5])
    for slot in (6, 7):
        assert (np.isfinite(baseline.reached["A"][slot]) <= np.isfinite(actual.reached["A"][slot])).all()
    assert not any(row[0] == 41 for row in actual.applied["A"])


def test_anonymous_count_fully_explained_by_seen_enemies_changes_nothing():
    locates = [info(1.2, "locate", 400, 200, slot=s) for s in (5, 6)]
    _, actual = suspension_unknown([*locates, one_of(count=2)], (400, 400, 400))
    _, baseline = suspension_unknown(locates, (400, 400, 400))
    assert actual.ignored["one_of explained by a seen enemy"] == 1
    assert actual.reasons == baseline.reasons
    for slot in (5, 6, 7):
        np.testing.assert_array_equal(actual.reached["A"][slot], baseline.reached["A"][slot])


@pytest.mark.parametrize("count", [1, 2])
def test_ambiguous_anonymous_mask_keeps_all_regions_unchanged(count):
    hit = one_of(count=count)
    hit.mask = ut.footprint(hit, open_hall())
    _, actual = suspension_unknown([hit], (400, 400, 400))
    _, baseline = suspension_unknown([], (400, 400, 400))
    for slot in (5, 6, 7):
        np.testing.assert_array_equal(actual.reached["A"][slot], baseline.reached["A"][slot])
    assert sum(actual.ignored.values()) == 1
    assert not actual.reasons["A"] and not actual.protect["A"] and not actual.applied["A"]


@pytest.mark.parametrize("needs", ["status", "reveal"])
@pytest.mark.parametrize("masked", [False, True])
def test_zero_hits_excludes_footprint_for_every_live_enemy(needs, masked):
    rnd = SimpleNamespace(team={0: "A", 1: "A", 5: "B", 6: "B", 7: "B"},
                          alive=lambda s, t: s != 7)
    footprint = {"x": 400, "y": 200, "radius_m": 1}
    if masked:
        footprint = {"mask": ut.footprint(info(0, "exclude", 400, 200, r=1), open_hall())}
    key = (0, "Paprika", "X_LevitateZone", 1.0)
    operations = ut.zero_hits(rnd, 1.2, "A", "anonymous_zero", needs=needs,
                              source="zone", cast_id=41, row_key=key, **footprint)
    assert [(i.slot, i.kind, i.detail["needs"]) for i in operations] == [
        (5, "exclude", needs), (6, "exclude", needs)]
    assert all((i.cast_id, i.row_key) == (41, key) for i in operations)
    geo, actual = suspension_unknown(operations, (400, 400, 400), dead=(7,))
    fp = ut.footprint(operations[0], geo)
    for slot in (5, 6):
        assert not np.isfinite(actual.reached["A"][slot][fp]).any()
        assert np.isfinite(actual.reached["A"][slot][~fp]).any()
    assert 7 not in actual.reached["A"]


@pytest.mark.parametrize("needs", ["status", "reveal"])
def test_zero_hits_keeps_immune_enemy_out_of_admitted_exclusions(needs):
    b = hall_round(more={6: ("B", [(0.0, 400, 200, 0)])})
    next(p for p in b["players"] if p["slot"] == 6)["agent"] = "Veto"
    b["util"] = [{"k": "buff", "t": 0.0, "t1": 3.0, "by": 6, "target": 6,
                  "code": "Pine", "name": "X_Evolution"}]
    geo = open_hall()
    rnd = ce.RoundInputs(b, geo)
    operations = ut.zero_hits(rnd, 1.2, "A", "anonymous_zero", needs=needs,
                              x=400, y=200, radius_m=1)
    assert [i.slot for i in operations] == [5, 6]
    assert [i.slot for i in ce.RoundInputs(b, geo, infos=operations).infos] == [5]


@pytest.mark.parametrize("places,count", [((400, 200), 1), ((400, 400), 2), ((200, 200), 0)])
def test_control_suspension_candidates_restrict_hypothesise_or_count(places, count):
    hit = one_of()
    geo, actual = suspension_unknown([hit], places)
    _, baseline = suspension_unknown([], places)
    fp = ut.footprint(hit, geo)
    if count == 1:
        assert not np.isfinite(actual.reached["A"][5][~fp]).any()
        assert np.array_equal(actual.reached["A"][6], baseline.reached["A"][6])
        assert actual.reasons["A"] == [(1.2, 5, "restrict", "hush_suspension", "zone")]
    elif count == 2:
        for slot in (5, 6):
            assert actual.protect["A"][slot] == {"zone": geo.node_at_px(400, 200)}
            assert (np.isfinite(baseline.reached["A"][slot]) <= np.isfinite(actual.reached["A"][slot])).all()
        assert [r[2] for r in actual.reasons["A"]] == ["hypothesis", "hypothesis"]
    else:
        assert actual.ignored["one_of with no candidate"] == 1
        for slot in (5, 6):
            assert np.array_equal(actual.reached["A"][slot], baseline.reached["A"][slot])
    assert actual.events["A"] == []


@pytest.mark.parametrize("excluded", ["dead", "immune"])
def test_control_suspension_never_selects_dead_or_immune_enemies(excluded):
    _, actual = suspension_unknown([one_of()], (400, 400), **{excluded: (6,)})
    assert actual.reasons["A"] == [(1.2, 5, "restrict", "hush_suspension", "zone")]
    assert not actual.protect["A"]


@pytest.mark.parametrize("before", [True, False])
def test_control_suspension_respects_equal_time_exclusion_order(before):
    hit = one_of()
    exclude = info(1.2, "exclude", 400, 200, r=7, slot=6)
    # Keep ground outside the excluded disk so the exclusion can succeed.
    geo, actual = suspension_unknown([exclude, hit] if before else [hit, exclude], (400, 340))
    assert [r[2] for r in actual.reasons["A"] if r[3] == "hush_suspension"] == (
        ["restrict"] if before else ["hypothesis", "hypothesis"])


def test_control_suspension_tests_regions_advanced_to_its_exact_instant():
    geo, baseline = suspension_unknown([], (314, 200), seed_t=1.0, t=1.5)
    hit = one_of(1.5)
    fp = ut.footprint(hit, geo)
    _, previous = suspension_unknown([], (314, 200), seed_t=1.0, t=1.0)
    assert not np.isfinite(previous.reached["A"][5][fp]).any()
    assert np.isfinite(baseline.reached["A"][5][fp]).any()
    _, actual = suspension_unknown([hit], (314, 200), seed_t=1.0, t=1.5)
    assert actual.reasons["A"] == [(1.5, 5, "restrict", "hush_suspension", "zone")]


@pytest.mark.parametrize("from_blob", [True, False])
def test_control_suspension_is_admitted_and_changes_compute_round(from_blob):
    b, geo = hall_round(), open_hall()
    b["util"] = suspension_rows(t=6.2) if from_blob else []
    infos = [] if from_blob else [one_of(6.2)]
    rnd = ce.RoundInputs(b, geo, infos=infos)
    assert [(i.kind, i.slot) for i in rnd.infos] == [("one_of", -1)]
    _, regions, extra = snapshots(b, geo, infos)
    fp = ut.footprint(rnd.infos[0], geo)
    assert not regions[6.2]["A"][5][~fp].any()
    assert any(r[2:4] == ("restrict", "hush_suspension") for r in extra[6.2][1]["A"])
    baseline = hall_round()
    _, old, _ = snapshots(baseline, geo, [info(6.2, "resume")])
    assert old[6.2]["A"][5].sum() > regions[6.2]["A"][5].sum()


@pytest.mark.parametrize("inside", [True, False])
def test_control_suspension_seen_enemy_explains_only_a_zone_it_is_inside(inside):
    geo = door_hall()
    b = blob({0: ("A", [(0.0, 300, 200, 180 if inside else 0)]),
              5: ("B", [(0.0, 250 if inside else 400, 200, 0)]),
              6: ("B", [(0.0, 180, 200, 180)])}, t_end=2.0)
    # Exercise the real ability/status row shape and reader, including the visible victim's identity.
    b["util"] = [toy_ability("Paprika", "X_LevitateZone", 240, 200, 0, t=1.0, t1=2.0, id="zone-1"),
                 {"k": "status", "t": 1.2, "t1": 2.0, "by": 0, "target": 5,
                  "code": "Paprika", "name": "X_LevitateZone", "status": "levitated",
                  "from": "force", "src": "zone-1"}]
    baseline = copy.deepcopy(b)
    baseline["util"] = []
    ticks = [0.0, 1.0, 1.2, 1.5, 2.0]
    _, old, _ = snapshots(baseline, geo, [info(1.2, "resume", slot=6)], ticks=ticks)
    rc, regions, extra = snapshots(b, geo, ticks=ticks)
    [hit] = ce.RoundInputs(b, geo).infos
    fp = ut.footprint(hit, geo)
    assert (old[1.2]["A"][6] & fp).any(), "the hidden nonvictim must be a possible sound source"
    assert (1.2, 5, "locate", "seen", "") in extra[1.2][1]["A"]
    ignored = rc.missing_inputs.get("utility knowledge ignored: one_of explained by a seen enemy", 0)
    if inside:
        assert ignored == 1
        for t in ticks:
            assert np.array_equal(regions[t]["A"][6], old[t]["A"][6])
        assert regions[1.2]["A"][6][geo.node_at_px(180, 200)]
        assert not any(r[3] == "hush_suspension" for r in extra[1.2][1]["A"])
    else:
        assert ignored == 0
        assert any(r[1:4] == (6, "restrict", "hush_suspension") for r in extra[1.2][1]["A"])
        assert not regions[1.2]["A"][6][~fp].any()
        assert not np.array_equal(regions[1.2]["A"][6], old[1.2]["A"][6])


@pytest.mark.parametrize("before", [True, False])
@pytest.mark.parametrize("inside", [True, False])
@pytest.mark.parametrize("reason", ["hush_suspension", "side_wide_evidence"])
def test_control_one_of_respects_equal_time_locate_order(before, inside, reason):
    hit = info(1.2, "one_of", 400, 200, r=7.0, slot=-1, reason=reason, source="zone")
    locate = info(1.2, "locate", 400 if inside else 200, 200, slot=6)
    _, actual = suspension_unknown([locate, hit] if before else [hit, locate], (400, 400))
    _, baseline = suspension_unknown([locate], (400, 400))
    assert actual.ignored["one_of explained by a seen enemy"] == int(before and inside)
    assert actual.ignored["one_of with no candidate"] == 0
    if before and inside:
        for slot in (5, 6):
            assert np.array_equal(actual.reached["A"][slot], baseline.reached["A"][slot])
        assert not actual.protect["A"]
        assert not any(r[3] == reason for r in actual.reasons["A"])
    elif not before:
        assert [r[2] for r in actual.reasons["A"] if r[3] == reason] == ["hypothesis", "hypothesis"]
    else:
        assert [r[1:3] for r in actual.reasons["A"] if r[3] == reason] == [(5, "restrict")]


def test_control_one_of_does_not_treat_an_old_sighting_as_seen_now():
    _, actual = suspension_unknown([one_of()], seen=[(5, 400, 1.0)])
    assert actual.ignored["one_of explained by a seen enemy"] == 0
    assert actual.reasons["A"] == [(1.2, 5, "restrict", "hush_suspension", "zone")]


def test_control_one_of_uses_a_locate_between_two_equal_time_sounds():
    miss = info(1.2, "one_of", 100, 200, r=1.0, slot=-1, source="miss")
    locate = info(1.2, "locate", 400, 200, slot=6)
    _, actual = suspension_unknown([miss, locate, one_of()], (400, 400))
    assert actual.ignored["one_of with no candidate"] == 1
    assert actual.ignored["one_of explained by a seen enemy"] == 1
    assert not actual.protect["A"]
    assert not any(r[3] == "hush_suspension" for r in actual.reasons["A"])


def test_a_round_with_no_info_has_only_its_frames():
    rc, _, _ = snapshots(hall_round(), open_hall())
    assert np.array_equal(rc.analytic, rc.ticks)
    assert "analytic_only_ticks" not in rc.timings


def test_a_locate_restarts_the_region_at_its_exact_time_and_shows_from_the_next_frame():
    b, geo = hall_round(), open_hall()
    rc, regions, extra = snapshots(b, geo, [info(6.2, "locate", 300, 250, reason="neural_theft")])
    assert 6.2 in [round(t, 6) for t in rc.analytic] and 6.2 not in [round(t, 6) for t in rc.ticks]
    assert 6.25 in [round(t, 6) for t in rc.ticks]
    node = geo.node_at(geo.cell_of_px(300, 250), None)
    at = regions[6.2]["A"][5]
    assert at[node] and at.sum() <= 2          # B5's own push and the located spot, nothing else
    events, reasons, _, _ = extra[6.2]
    assert (5, 6.2, "neural_theft") in events["A"]
    assert (6.2, 5, "locate", "neural_theft", "") in reasons["A"]
    # the stored frames: the one before the locate still shows the old region, the one after the new one
    frame = {round(t, 6): i for i, t in enumerate(rc.ticks)}
    assert max(t for t in rc.ticks if t < 6.2) == 6.0
    big = rc.unknown["A"][frame[6.0]].sum()
    small = rc.unknown["A"][frame[6.25]].sum()
    assert big > 100 and small < 20
    # and that is what the encoded blob carries (frames on the 1/16 s grid, the locate at 6.25 s, not before)
    header, streams = cf.unpack_data(encode_data(rc, b))
    times = [tk / header["hz"] for tk in header["ticks"]]
    assert 6.2 not in times and 6.25 in times
    masks = cf.decode_masks(streams["unknown_a"], len(times), header["cells"],
                            [c[0] for c in header["unknown_checkpoints"]], slots=1)
    assert sum(masks[times.index(6.0)][0]) == big and sum(masks[times.index(6.25)][0]) == small


def test_two_operations_between_frames_both_happen_in_order():
    b, geo = hall_round(), open_hall()
    rc, regions, extra = snapshots(b, geo, [info(6.2, "locate", 300, 250), info(6.21, "exclude", 300, 250, r=2.0)])
    assert {6.2, 6.21} <= {round(t, 6) for t in rc.analytic}
    node = geo.node_at(geo.cell_of_px(300, 250), None)
    assert regions[6.2]["A"][5][node] and not regions[6.21]["A"][5][node]
    frame = {round(t, 6): i for i, t in enumerate(rc.ticks)}
    assert len([t for t in rc.ticks if 6.19 < t < 6.26]) == 1           # one frame shows both


def test_restriction_keeps_disconnected_parts_and_recentres_nothing():
    geo = door_hall()                       # a wall x 200-216 from the north edge to the door row
    b = blob({0: ("A", [(0.0, 104, 250, 180)]), 5: ("B", [(0.0, 190, 120, 90)])}, t_end=40.0)
    rc, regions, _ = snapshots(b, geo, [info(35.0, "restrict", 208, 150, r=4.0)])
    before, after = regions[34.5]["A"][5], regions[35.0]["A"][5]
    x = geo.centres[:, 0]
    west, east = after & (x < 200), after & (x > 216)
    assert west.any() and east.any(), "both sides of the wall survive: possible origins either side"
    disk = ut.footprint(info(35.0, "restrict", 208, 150, r=4.0), geo)
    assert not (after & ~disk & ~(np.arange(geo.n) == geo.node_at(geo.cell_of_px(190, 120), None))).any()
    assert before.sum() > after.sum()


def test_exclusion_touches_one_enemy_only():
    b = hall_round(more={6: ("B", [(0.0, 395, 260, 0)])})
    geo = open_hall()
    rc, regions, _ = snapshots(b, geo, [info(5.0, "exclude", 350, 230, r=3.0, slot=5)])
    disk = ut.footprint(info(5.0, "exclude", 350, 230, r=3.0), geo)
    prev = round(max(t for t in rc.analytic if t < 5.0), 6)
    assert regions[prev]["A"][5][disk].any() and not regions[5.0]["A"][5][disk & ~(np.arange(geo.n) == geo.node_at(
        geo.cell_of_px(400, 200), None))].any()
    assert regions[5.0]["A"][6][disk].any(), "B6's region is not touched by what was learnt about B5"


def test_an_operation_that_would_leave_the_enemy_nowhere_is_ignored_and_counted():
    b, geo = hall_round(), open_hall()
    rc, regions, _ = snapshots(b, geo, [info(3.0, "restrict", 110, 110, r=1.0)])
    assert regions[3.0]["A"][5].sum() > 5
    assert rc.missing_inputs["utility knowledge ignored: restrict that would leave the enemy nowhere"] == 1


def pocket():
    """The hall and a one-cell room (8 x 8 px) nobody can walk to or see into."""
    return toy_geometry("Pocket", [HALL, (480, 96, 488, 104)])


@pytest.mark.slow
def test_a_hypothesis_in_a_one_cell_room_survives_the_sliver_cleanup_while_unseen():
    geo = pocket()
    rc, regions, extra = snapshots(hall_round(), geo, [info(2.0, "hypothesis", 484, 100, source="beacon-1")])
    node = geo.node_at(geo.cell_of_px(484, 100), None)
    later = [t for t in sorted(regions) if t >= 2.0]
    assert all(regions[t]["A"][5][node] for t in later), "at its creation and on every tick after"
    assert extra[later[-1]][2]["A"][5] == {"beacon-1": node}
    assert rc.unknown["A"][list(np.round(rc.ticks, 6)).index(later[-1])][
        list(rc.walk_cells).index(geo.cell_of_px(484, 100))]


def test_an_unprotected_broadening_there_is_cleaned_up_as_before():
    geo = pocket()
    _, regions, _ = snapshots(hall_round(), geo, [info(2.0, "broaden", 484, 100, r=0.5)])
    node = geo.node_at(geo.cell_of_px(484, 100), None)
    assert not regions[2.0]["A"][5][node], "a one-cell piece no enemy stands in is dropped (DROP_PIECE_CELLS)"


def test_vision_clears_a_hypothesis_for_good():
    geo = open_hall()
    watcher = ("A", [(0.0, 300, 200, 0)])          # looks east along the hall, over (350, 200)
    b = blob({0: watcher, 5: ("B", [(0.0, 400, 280, 0)])}, t_end=8.0)
    _, regions, extra = snapshots(b, geo, [info(2.0, "hypothesis", 350, 200, source="beacon-2")])
    node = geo.node_at(geo.cell_of_px(350, 200), None)
    assert not regions[2.0]["A"][5][node]
    events, reasons, protect, cleared = extra[2.0]
    assert protect["A"].get(5, {}) == {} and "beacon-2" in cleared["A"]
    assert (2.0, 5, "cleanup", "hypothesis_cleared", "beacon-2") in reasons["A"]
    assert all(not extra[t][2]["A"].get(5) for t in extra if t > 2.0), "never protected again"
    assert all((5, 2.0, "test") not in extra[t][0]["A"] for t in extra), "a hypothesis is not locating evidence"


def test_a_pause_stops_the_spread_and_nothing_is_caught_up_after():
    geo = open_hall()
    ticks = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    _, free, _ = snapshots(hall_round(), geo, ticks=ticks)
    _, held, _ = snapshots(hall_round(), geo, [info(1.0, "pause"), info(3.0, "resume")], ticks=ticks)
    size = lambda regions, t: int(regions[t]["A"][5].sum())  # noqa: E731
    assert size(held, 2.0) == size(held, 1.0) == size(free, 1.0)
    assert size(held, 3.0) == size(free, 1.0)
    assert size(held, 4.0) == size(free, 2.0) and size(held, 5.0) == size(free, 3.0)


def test_a_pause_that_never_ends_holds_to_the_round_end_and_a_death_clears_it():
    geo = open_hall()
    ticks = [0.0, 1.0, 2.0, 3.0]
    b = blob({0: A_HIDDEN, 5: ("B", [(0.0, 400, 200, 0)])}, t_end=4.0, deaths={5: 2.5})
    _, held, extra = snapshots(b, geo, [info(1.0, "pause"), info(1.5, "hypothesis", 300, 200, source="s")], ticks=ticks)
    assert held[2.0]["A"][5].sum() == held[1.5]["A"][5].sum()
    assert 5 not in held[3.0]["A"] and extra[3.0][2]["A"] == {}


def test_between_frame_instants_integrate_like_frames_would():
    """An Info's own instant, evaluated between frames, adds to every total exactly what the same instant would
    as a stored frame: the stored frames differ, the totals don't."""
    b, geo = hall_round(t_end=10.0), open_hall()
    infos = [info(6.2, "locate", 300, 250), info(6.21, "broaden", 200, 200, r=6.0)]
    rc, _, _ = snapshots(b, geo, infos)
    as_frames = sorted({*np.round(rc.ticks, 6).tolist(), 6.2, 6.21})
    ref, _, _ = snapshots(b, geo, infos, ticks=as_frames)
    assert np.allclose(rc.analytic, ref.ticks)
    for s in rc.players:
        a, r = rc.players[s].as_dict(), ref.players[s].as_dict()
        assert a == r, s
    assert [s.seconds for s in rc.sections] == pytest.approx([s.seconds for s in ref.sections])
    for x, y in zip(rc.sections, ref.sections):
        assert np.allclose(x.totals, y.totals)
    assert rc.redundant_m2s == pytest.approx(ref.redundant_m2s)
    # and the 0.01 s between the two Infos is its own step, with its own state
    assert rc.timings["analytic_only_ticks"] == 2


def test_the_info_frame_never_shows_before_the_info():
    assert ce.snap_after(6.2) == 6.25 and ce.snap_after(6.25) == 6.25 and ce.snap(6.2) == 6.1875


def test_infos_about_a_teammate_or_out_of_the_round_are_dropped():
    b = hall_round()
    rnd = ce.RoundInputs(b, open_hall(), infos=[info(1.0, "locate", 300, 200, slot=0), info(99.0, "locate", 300, 200),
                                                 info(2.0, "locate", 300, 200)])
    assert [(i.t, i.slot) for i in rnd.infos] == [(2.0, 5)]


def test_equal_times_run_resume_pause_then_information_in_reader_order():
    out = ut.ordered([info(1.0, "exclude", 0, 0, r=1), info(1.0, "resume"), info(1.0, "locate", 0, 0),
                      info(1.0, "pause"), info(0.5, "hypothesis", 0, 0)])
    assert [i.kind for i in out] == ["hypothesis", "resume", "pause", "exclude", "locate"]


def test_an_unknown_operation_is_refused():
    with pytest.raises(ValueError):
        ut.Info(1.0, "A", 5, "teleport", "x")


# ---------------------------------------------------------------- the gap detector's judging (R7 with exact times)


def record(t, events):
    return SimpleNamespace(t=t, events={"A": events, "B": []})


def test_a_utility_locate_is_judged_only_at_its_own_instant():
    assert judged_against_before(record(6.2, [(5, 6.2, "neural_theft")])) == {"A": {5}, "B": set()}
    # a later record never re-judges against the uncertainty from before an earlier locate
    assert judged_against_before(record(6.25, [(5, 6.2, "neural_theft")])) == {"A": set(), "B": set()}


def test_the_engines_own_events_keep_the_r7_rule():
    for kind in ("kill", "gunfire", "damage", "footsteps", "seen", "plant", "revived",
                 "trip_destroyed", "camera_destroyed"):
        assert judged_against_before(record(6.25, [(5, 6.2, kind)]))["A"] == {5}, kind


# ---------------------------------------------------------------- cast provenance and applied operations (R1/D9)

@pytest.mark.parametrize("reader", ut.READERS)
def test_each_reader_preserves_the_util_row_identity(reader, monkeypatch):
    b = hall_round()
    row = toy_ability("Hunter", "Q_SonarPing", 400, 200, 0, t=1.0, id=41)
    if reader == "read_reveals":
        row = {"k": "reveal", "t": 1.0, "t1": 2.0, "by": 0, "target": 5,
               "code": "Hunter", "name": "Q_SonarPing"}
    elif reader == "read_knives":
        row.update(code="Grenadier", name="E_SuppressionPulse",
                   activation={"state": "completed", "targets_complete": True},
                   pulse={"t": 1.2, "r": 1000, "hits": []})
    elif reader == "read_skye_flashes":
        row.update(k="flash", ability="skye_guiding_light", hits=[],
                   activation={"state": "completed", "targets_complete": True},
                   pop={"t": 1.2, "u": row["u"], "v": row["v"]})
    elif reader == "read_leers":
        row.update(code="Vampire", name="4_NearsightAOE_Source", by=5)
    elif reader == "read_omen_ults":
        row.update(code="Wraith", name="X_GlobalTeleport_Intention", by=5, t1=2.0)
    elif reader == "read_waylay_recalls":
        row.update(code="Terra", name="E_RewindTime_RewindTarget", by=5,
                   recall={"t": 1.2, "t1": 1.5, "u": row["u"], "v": row["v"]})
    elif reader == "read_hush_suspensions":
        row, hit = suspension_rows(source=41)
        b["util"] = [hit]
    elif reader.removeprefix("read_") in COUNT_READERS:
        row, *hits = count_rows(reader.removeprefix("read_"), (5,))
        b["util"] = hits
    b["util"].append(row)
    rnd = ce.RoundInputs(b, open_hall())
    monkeypatch.setattr(ut, "heard", lambda *a: reader in ("read_waylay_recalls", "read_storm_surges"))
    monkeypatch.setattr(ut, "_sees_point", lambda rnd, s, t, *a: reader != "read_omen_ults" or t >= 1.5)
    out = getattr(ut, reader)(rnd)
    assert out, reader
    for operation in out:
        assert operation.cast_id == row.get("id")
        assert operation.row_key == (row["by"], row["code"], row["name"], row["t"])


def test_applied_excludes_report_changes_but_omit_noops_and_ignored_operations():
    key = (0, "Hunter", "Q_SonarPing", 1.0)
    operations = [info(1.2, "exclude", 400, 200, r=1, cast_id=41, row_key=key),
                  info(1.2, "exclude", 400, 200, r=1, cast_id=42, row_key=key),
                  info(1.2, "exclude", 400, 200, r=1000, cast_id=43, row_key=key)]
    geo, unknown = suspension_unknown(operations)
    assert unknown.applied["B"] == []
    assert unknown.applied["A"] == [(41, key, "exclude", 5,
                                      tuple(np.flatnonzero(ut.footprint(operations[0], geo))))]
    assert unknown.ignored["exclude that would leave the enemy nowhere"] == 1


@pytest.mark.parametrize("late", [False, True])
def test_applied_locate_uses_the_region_on_the_previous_record(late):
    key = (0, "Hunter", "Q_SonarPing", 1.0)
    operations = [info(1.2, "exclude", 400, 200, r=1, cast_id=40, row_key=key)]
    if late:
        operations.append(info(1.2, "one_of", 100, 100, r=1, slot=-1))
    operations.append(info(1.2, "locate", 400, 200, cast_id=41, row_key=key))
    geo, unknown = suspension_unknown(operations)
    previous = ut.footprint(info(0, "restrict", 400, 200, r=5), geo)
    assert unknown.applied["A"][-1] == (41, key, "locate", 5, tuple(np.flatnonzero(previous)))


def test_late_derived_restriction_preserves_cast_and_reports_ruled_out_ground():
    key = (0, "Paprika", "X_LevitateZone", 1.0)
    hit = one_of(cast_id=41, row_key=key)
    geo, unknown = suspension_unknown([hit])
    _, baseline = suspension_unknown([])
    assert unknown.applied["A"] == [(41, key, "restrict", 5,
                                      tuple(np.flatnonzero(np.isfinite(baseline.reached["A"][5])
                                                           & ~ut.footprint(hit, geo))))]


@pytest.mark.parametrize("late", [False, True])
def test_a_locate_on_an_enemy_in_view_is_not_applied(late):
    b = blob({0: ("A", [(0.0, 104, 200, 0)]), 5: ("B", [(0.0, 200, 200, 180)])}, t_end=2)
    records = []
    operations = [info(1.2, "one_of", 100, 100, r=1, slot=-1)] if late else []
    operations.append(info(1.2, "locate", 400, 200, cast_id=41))
    ce.compute_round(b, open_hall(), knowledge=False, observer=lambda rec, unk: records.append(rec),
                     infos=operations)
    assert all(not rec.applied["A"] for rec in records)


def test_a_reveal_reports_its_original_row_key_and_resets_on_the_next_tick():
    b = hall_round(t_end=2)
    row = {"k": "reveal", "t": 1.2, "t1": 1.2, "by": 0, "target": 5,
           "code": "Hunter", "name": "Q_SonarPing"}
    b["util"] = [row]
    records = []
    ce.compute_round(b, open_hall(), knowledge=False, observer=lambda rec, unk: records.append(rec))
    [(at, entry)] = [(rec.t, entry) for rec in records for entry in rec.applied["A"]]
    assert at == 1.2
    assert entry[:4] == (None, (0, "Hunter", "Q_SonarPing", 1.2), "locate", 5)
    assert entry[4]


@pytest.mark.parametrize("kind", ["exclude", "restrict", "hypothesis"])
def test_early_operations_overwritten_by_sight_are_not_applied(kind, monkeypatch):
    geo = open_hall()
    empty = np.zeros(geo.n, bool)
    sight = empty.copy()
    sight[geo.node_at_px(400, 200)] = True
    holders = {0: SimpleNamespace(slot=0, team="A", cell=geo.node_at_px(104, 200),
                                  active=sight, passive=empty, watch=empty),
               5: SimpleNamespace(slot=5, team="B", cell=geo.node_at_px(400, 200),
                                  active=empty, passive=empty, watch=empty)}
    operation = info(1.2, kind, 300 if kind == "hypothesis" else 400, 200, r=1, cast_id=41)
    _, actual = suspension_unknown([operation], holders=holders)
    operate = ce.Unknown._operate

    def without_report(self, *args, **kwargs):
        result = operate(self, *args, **kwargs)
        self.applied = {"A": [], "B": []}
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ce.Unknown, "_operate", without_report)
        _, baseline = suspension_unknown([operation], holders=holders)
    for side in ("A", "B"):
        assert actual.cells[side].tobytes() == baseline.cells[side].tobytes()
        for slot, region in actual.reached[side].items():
            assert region.tobytes() == baseline.reached[side][slot].tobytes()
    assert not [entry for entry in actual.applied["A"] if entry[3] == 5]


@pytest.mark.parametrize("kind", ["restrict", "exclude"])
@pytest.mark.parametrize("spotted", [False, True])
def test_applied_footprint_contains_only_nodes_removed_from_a_small_region(kind, spotted):
    geo = open_hall()
    unknown = ce.Unknown(geo)
    region = ut.footprint(info(0, "restrict", 400, 200, r=5), geo)
    reached = np.where(region, 0.0, np.inf)
    operation = info(1.2, kind, 400 if kind == "restrict" else 380, 200,
                     r=1 if kind == "restrict" else 5, cast_id=41)
    fp = ut.footprint(operation, geo)
    ruled_out = ~fp if kind == "restrict" else fp
    removed = region & ruled_out
    assert removed.any() and (ruled_out & geo.walk_n & ~region).any()
    unknown._operate("A", 5, [operation], reached.copy(), reached.copy(), {}, None, None,
                     1.0, 1.2, SimpleNamespace(t_start=0.0), spotted={5} if spotted else set())
    assert unknown.applied["A"] == [(41, None, kind, 5, tuple(np.flatnonzero(removed)))]


# ---------------------------------------------------------------- Thrash and Tejo's drone as flown drones (W3)

FLOWN = {"Aggrobot": "RollyPolly", "Cashew": "4_Spider_LockOn"}


def flown_drone(code, possessed=None):
    extra = {} if possessed is None else {"possessed": [list(p) for p in possessed]}
    return toy_ability(code, FLOWN[code], 110, 196, 0, t=0.0, t1=3.0, kind="Pawn",
                       path=[[0.0, *uv(110, 196)], [3.0, *uv(112, 196)]], yaws=[[0.0, 0]], **extra)


def flown_watch(code, t, possessed=((0.0, 2.0),)):
    geo = open_hall()
    b = blob({0: A_HIDDEN, 5: ("B", [(0.0, 400, 280, 0)])}, t_end=4.0, util=[flown_drone(code, possessed)])
    rnd = ce.RoundInputs(b, geo)
    assert [w.kind for w in rnd.watchers] == ["drone"]
    return geo, ce.Tick(rnd, t).holders[0].watch


def seen_at(geo, watch, x, y):
    return bool(watch[geo.node_at(geo.cell_of_px(x, y), None)])


@pytest.mark.parametrize("code", ["Aggrobot", "Cashew"])
def test_a_flown_thrash_or_tejo_drone_sees_within_its_cone_and_range_only(code):
    geo, watch = flown_watch(code, 1.0)
    assert seen_at(geo, watch, 180, 196)                 # ahead, inside the range
    assert not seen_at(geo, watch, 330, 196)             # ahead, past 18-20 m (about 130-145 px)
    assert not seen_at(geo, watch, 140, 286)             # 71 degrees off the heading: outside either cone


def test_the_two_drones_have_their_own_cones():
    geo, thrash = flown_watch("Aggrobot", 1.0)
    _, tejo = flown_watch("Cashew", 1.0)
    assert not seen_at(geo, thrash, 130, 226) and seen_at(geo, tejo, 130, 226)     # 56 degrees: past 45, within 60
    assert seen_at(geo, thrash, 240, 196) and not seen_at(geo, tejo, 250, 196)    # 20 m against 18 m


@pytest.mark.parametrize("code", ["Aggrobot", "Cashew"])
def test_outside_its_possessed_span_a_drone_sees_nothing(code):
    geo, watch = flown_watch(code, 2.5)
    assert not watch.any()


@pytest.mark.parametrize("code", ["Aggrobot", "Cashew"])
def test_a_drone_with_no_possessed_span_counts_its_whole_life_and_is_marked(code):
    rnd = ce.RoundInputs(blob({0: A_HIDDEN, 5: ("B", [(0.0, 400, 280, 0)])}, t_end=4.0,
                              util=[flown_drone(code)]), open_hall())
    assert rnd.missing["drone flown intervals (whole life used)"] == 1


def test_the_drone_table_is_built_from_the_figures():
    fig = ut.FIGURES["drone"]
    assert ce.DRONES["Aggrobot_RollyPolly"] == (fig["thrash"]["half_deg"], fig["thrash"]["range_m"])
    assert ce.DRONES["Cashew_4_Spider_LockOn"] == (fig["tejo"]["half_deg"], fig["tejo"]["range_m"])
    assert ce.DRONES["Guide_Q_PossessableScout"] == (45.0, 22.5)
