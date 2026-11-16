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
from tests.replays.control_toys import toy_ability

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


def suspension_unknown(infos, places=(400, 200), *, dead=(), immune=(), seed_t=0.0, t=1.2):
    """No hidden position pushes: seed regions, then exercise the real Unknown.apply ordering and spread."""
    geo = open_hall()
    unknown = ce.Unknown(geo)
    for slot, x in zip((5, 6), places):
        reached = np.full(geo.n, np.inf)
        reached[ut.footprint(info(0, "restrict", x, 200, r=5), geo)] = seed_t
        unknown.reached["A"][slot] = reached
    unknown._prev_t = 1.0
    for side in ("A", "B"):
        unknown.free_since[side] = np.zeros(geo.n)
        unknown._room[side] = geo.walk_n.copy()
    rnd = SimpleNamespace(team={0: "A", 5: "B", 6: "B"}, alive=lambda s, t: s not in dead,
                          watchers=[], infos=ut.ordered(infos), evolution={s: [(0, 9)] for s in immune})
    unknown.apply(SimpleNamespace(t=t, holders={}, rnd=rnd))
    return geo, unknown


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
