"""The damaging-molly contract W10 consumes (app/control/mollies.py, utility.json `molly`; the replay
player-state plan amendment F8, decision D6): which zones count lives in code, their figures in the hashed
table, and a zone without a known owner is skipped and counted.

Then the traversal rule itself (P07a and the plan's amendment "Molly traversal"): an active enemy damaging molly
stops the unknown walking through it; a friendly one, a non-damaging area and an instant blast don't. Every expected
arrival below is worked out by hand (octile steps of S seconds from the enemy's cell, or a test-local Dijkstra),
never read back from the engine."""

import heapq
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import engine as ce
from app.control import mollies as mo
from app.control import utility as ut
from app.control.geometry import GRID
from app.gaps import cache
from app.gaps import detect as gd
from app.gaps.rows import to_rows
from app.replays import control_format as cf
from tests.replays.control_toys import (blob, height_blob, standing, toy_ability, toy_geometry, toy_heights, uv,
                                        z_dm)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import control_reference as ref  # noqa: E402

FIVE = {"Phoenix_MolotovFire", "Sarge_Q_Molotov_Production", "Pandemic_AcidMolotov_NewMolotov",
        "Killjoy_4_BeeSwarm_Damage", "Aggrobot_C_ExplodeyPatch"}
# The owner's list (2026-10-09): the five, then Aftershock, Orbital Strike, Guided Salvo, Armageddon and FRAG/ment.
# Vyse's Razorvine is on it too, with no key until a replay shows its object.
TEN = FIVE | {"Breach_4_FusionBlast", "Sarge_X_OrbitalStrike_Production", "Cashew_E_AirStrikeMortar",
              "Cashew_X_Segment", "Grenadier_Q_SemtexBasic"}
# From each ability's wiki page (D6, 2026-10-09): (radius, length, width, seconds)
WIKI = {"Phoenix_MolotovFire": (4.5, None, None, 4.0), "Sarge_Q_Molotov_Production": (4.5, None, None, 8.0),
        "Pandemic_AcidMolotov_NewMolotov": (4.5, None, None, 6.5), "Killjoy_4_BeeSwarm_Damage": (4.5, None, None, 4.0),
        "Aggrobot_C_ExplodeyPatch": (5.5, None, None, 3.3), "Breach_4_FusionBlast": (3.0, 10.0, None, 3.0),
        "Sarge_X_OrbitalStrike_Production": (9.0, None, None, 5.75), "Cashew_E_AirStrikeMortar": (4.5, None, None, 2.9),
        "Cashew_X_Segment": (6.0, 2.0, 12.0, 1.0), "Grenadier_Q_SemtexBasic": (4.0, None, None, 4.0)}


def _row(code="Phoenix", name="MolotovFire", by=5, t=12.028, t1=19.228, **extra):
    return {"k": "ability", "kind": "Patch", "code": code, "name": name, "by": by, "owner_by": "agent",
            "t": t, "t1": t1, "u": 5480, "v": 1136, "z": 80, **extra}


def test_the_classification_is_the_owners_list_in_code():
    assert mo.MOLLY_KEYS == frozenset(TEN)
    assert set(ut.FIGURES["molly"]) == TEN, "every classified zone has figures, and nothing else does"
    raw = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))["molly"]
    assert all("classif" not in json.dumps(v).lower() for v in raw.values()), "no classification in the JSON"
    assert mo.SHAPES == {"Breach_4_FusionBlast": "capsule", "Cashew_X_Segment": "strip"}
    assert mo.THROUGH_WALLS == {"Killjoy_4_BeeSwarm_Damage", "Sarge_X_OrbitalStrike_Production",
                                "Breach_4_FusionBlast", "Cashew_X_Segment"}


def test_the_figures_are_the_wikis():
    for key, (radius, length, width, seconds) in WIKI.items():
        fig = ut.FIGURES["molly"][key]
        assert (fig["radius_m"], fig.get("length_m"), fig.get("width_m"), fig["seconds"]) == (radius, length, width,
                                                                                            seconds), key


def test_the_radii_are_the_engines_damage_zone_radii():
    for key in FIVE | {"Sarge_X_OrbitalStrike_Production"}:
        [r] = [r for pat, r in ce.DAMAGE_ZONES if pat.match(key)]
        assert ut.FIGURES["molly"][key]["radius_m"] == r / 100.0
    assert ut.FIGURES["molly"]["Phoenix_MolotovFire"]["seconds"] == 4.0


def test_a_zone_burns_for_its_seconds_capped_at_the_rows_end():
    figures = ut.FIGURES["molly"]
    zones, diagnostics = mo.molly_zones([_row(), _row(t=30.0625, t1=32.0)], figures)
    assert [z[:6] for z in zones] == [(5, 5480, 1136, 4.5, 12.028, 12.028 + 4.0), (5, 5480, 1136, 4.5, 30.0625, 32.0)]
    assert all(z.shape == "circle" and not z.through and z.z == 80 for z in zones)
    assert diagnostics == {}


def test_gone_cuts_a_zone_short_but_not_before_it_starts():
    zones, _ = mo.molly_zones([_row(gone=14.0), _row(gone=12.0)], ut.FIGURES["molly"])
    assert [z.t1 for z in zones] == [14.0, 12.028 + 4.0]


# ---------------------------------------------------------------- the owner's added zones (2026-10-09)

def _frag(flight, **extra):
    return _row(code="Grenadier", name="Q_SemtexBasic", kind="Projectile", t=13.57, t1=19.0, u=3576, v=5896,
                **({"flight": flight} if flight is not None else {}), **extra)


def test_fragment_holds_from_where_and_when_it_lands():
    # thrown from (3576, 5896) at 13.57; still flying at 14.2; on the ground from 14.6 (moving 5 uv, then still)
    flight = [[13.57, 3576, 5896, 98], [14.2, 3900, 6100, 60], [14.6, 4100, 6200, 20], [15.0, 4105, 6200, 20],
              [16.0, 4105, 6202, 20]]
    zones, diagnostics = mo.molly_zones([_frag(flight)], ut.FIGURES["molly"])
    [z] = zones
    assert (z.u, z.v, z.radius_m, z.t0, z.t1) == (4105, 6202, 4.0, 14.6, 14.6 + 4.0) and diagnostics == {}


def test_fragment_without_a_flight_has_no_place_and_other_projectiles_are_flights():
    zones, diagnostics = mo.molly_zones([_frag(None), _frag([[13.57, 1, 1]]),
                                         _row(code="Breach", name="4_FusionBlast", kind="Projectile"),
                                         _row(code="Grenadier", name="Q_SemtexBasic", kind="GameObject")],
                                        ut.FIGURES["molly"])
    assert zones == [] and diagnostics == {"molly without a place": 2}


def test_aftershock_is_a_capsule_along_breachs_yaw_through_the_wall():
    [z], _ = mo.molly_zones([_row(code="Breach", name="4_FusionBlast", kind="GameObject", yaw=182, t=32.024,
                                  t1=36.329)], ut.FIGURES["molly"])
    assert (z.shape, z.radius_m, z.length_m, z.angle, z.through) == ("capsule", 3.0, 10.0, 182.0, True)
    assert (z.t0, z.t1) == (32.024, 32.024 + 3.0)


def _segment(t, u, v, **extra):
    return _row(code="Cashew", name="X_Segment", kind="GameObject", by=4, t=t, t1=t + 4.0, u=u, v=v, **extra)


def _cast(t, u, v, yaw=0):
    return _row(code="Cashew", name="X_SegmentManager", kind="GameObject", by=4, t=t, t1=t + 8.0, u=u, v=v, yaw=yaw)


def test_armageddon_segments_hold_from_the_cast_along_their_path():
    # replay 39 round 12's shape: the cast at 1.946, segments 0.25 s apart walking +u (and a little +v)
    rows = [_cast(1.946, 2371, 3265, yaw=6)] + [_segment(4.195 + 0.25 * i, 2449 + 155 * i, 3274 + 17 * i)
                                               for i in range(6)]
    zones, diagnostics = mo.molly_zones(rows, ut.FIGURES["molly"])
    assert len(zones) == 6 and diagnostics == {}
    want = math.degrees(math.atan2(17 * 5, 155 * 5))
    for i, z in enumerate(zones):
        assert (z.shape, z.length_m, z.width_m, z.through) == ("strip", 2.0, 12.0, True)
        assert z.t0 == 1.946 and z.t1 == pytest.approx(4.195 + 0.25 * i + 1.0)
        assert z.angle == pytest.approx(want)


def test_a_lone_armageddon_segment_holds_from_its_own_time_and_is_counted():
    rows = [_cast(1.0, 0, 0), _segment(20.0, 5000, 5000, yaw=90), _segment(4.0, 100, 100) | {"by": 3}]
    zones, diagnostics = mo.molly_zones(rows, ut.FIGURES["molly"])
    assert [(z.t0, z.angle) for z in zones] == [(20.0, 90.0), (4.0, 0.0)]
    assert diagnostics == {"armageddon segment without its cast (held from its own time)": 2}


def test_one_segment_takes_its_direction_from_the_cast():
    zones, _ = mo.molly_zones([_cast(1.0, 1000, 1000, yaw=45), _segment(4.0, 1000, 2000)], ut.FIGURES["molly"])
    assert zones[0].angle == pytest.approx(90.0)


# Footprints by shape and walls. Two corridors 3 cells tall (rows 24-26 and 29-31), cols 12-51, with a two-cell
# wall between them (rows 27-28); cell centres at 8 * col + 4, 8 * row + 4. Expected cells are worked out here from
# each shape's definition on the cell centres, independently of the engine's code.
TWIN = [(96, 192, 416, 216), (96, 232, 416, 256)]


def _twin_nodes(row, geo):
    rnd = ce.RoundInputs(corridor_blob({0: ("A", (118, 204, 0)), 5: ("B", (404, 244, 180))}, [row]), geo)
    [(_, _, nodes)] = rnd.mollies
    return {(int(n) // GRID, int(n) % GRID) for n in nodes}


def _px(x, y):
    """Where a row placed at (x, y) px is stored: its uv rounded to whole units, back in px."""
    u, v = uv(x, y)
    return u * 1024 / 10000, v * 1024 / 10000


def _walkable(r, c):
    return 12 <= c <= 51 and (24 <= r <= 26 or 29 <= r <= 31)


@pytest.mark.parametrize("code,name,through", [("Phoenix", "MolotovFire", False), ("Killjoy", "4_BeeSwarm_Damage", True),
                                               ("Sarge", "X_OrbitalStrike_Production", True)])
def test_a_circle_passes_the_wall_only_when_it_pierces(code, name, through):
    geo = toy_geometry("MollyTwin", TWIN)
    radius = ut.FIGURES["molly"][f"{code}_{name}"]["radius_m"] / geo.m_per_px
    got = _twin_nodes(toy_ability(code, name, 300, 212, 0, t=1.0, t1=20.0, kind="GameObject"), geo)
    want = {(r, c) for r in range(GRID) for c in range(GRID) if _walkable(r, c)
            and (8 * c + 4 - _px(300, 212)[0]) ** 2 + (8 * r + 4 - _px(300, 212)[1]) ** 2 <= radius ** 2}
    lower = {(r, c) for r, c in want if r >= 29}
    assert lower, "the circle reaches the lower corridor"
    assert got == (want if through else want - lower)


def test_aftershock_covers_a_capsule_forward_of_where_it_stuck():
    geo = toy_geometry("MollyTwin", TWIN)
    row = toy_ability("Breach", "4_FusionBlast", 200, 204, 0, t=1.0, t1=6.0, kind="GameObject", yaw=0)
    got = _twin_nodes(row, geo)
    r, length = 3.0 / geo.m_per_px, 10.0 / geo.m_per_px
    x0, y0 = _px(200, 204)
    want = set()
    for rr in range(GRID):
        for c in range(GRID):
            x, y = 8 * c + 4, 8 * rr + 4
            along = min(max(x - x0, 0.0), length)
            if _walkable(rr, c) and (x - x0 - along) ** 2 + (y - y0) ** 2 <= r * r:
                want.add((rr, c))
    assert got == want and max(c for _, c in want) > min(c for _, c in want) + 6, "it reaches well forward"


def test_an_armageddon_segment_covers_a_strip_across_both_corridors():
    geo = toy_geometry("MollyTwin", TWIN)
    rows = [toy_ability("Cashew", "X_SegmentManager", 300, 100, 0, t=0.0, t1=8.0, kind="GameObject", yaw=90),
            toy_ability("Cashew", "X_Segment", 300, 224, 0, t=3.0, t1=7.0, kind="GameObject")]
    rnd = ce.RoundInputs(corridor_blob({0: ("A", (118, 204, 0)), 5: ("B", (404, 244, 180))}, rows), geo)
    [(_, z, nodes)] = rnd.mollies
    assert z.angle == pytest.approx(90.0) and (z.t0, z.t1) == (0.0, 4.0)
    got = {(int(n) // GRID, int(n) % GRID) for n in nodes}
    half_len, half_wid = 1.0 / geo.m_per_px, 6.0 / geo.m_per_px
    # the path runs +v (down the map): 2 m along y, 12 m across x
    want = {(r, c) for r in range(GRID) for c in range(GRID) if _walkable(r, c)
            and abs(8 * r + 4 - _px(300, 224)[1]) <= half_len and abs(8 * c + 4 - _px(300, 224)[0]) <= half_wid}
    assert got == want


def test_a_row_with_no_end_burns_for_its_seconds():
    zones, _ = mo.molly_zones([_row(t1=None)], ut.FIGURES["molly"])
    assert zones[0][5] == 12.028 + 4.0


def test_other_rows_are_not_mollies():
    rows = [_row(code="Hunter", name="4_ExplosiveBolt_Explosion"), _row(code="Wraith", name="4_Smoke"),
            {"k": "status", "t": 1.0, "t1": 2.0, "target": 1, "status": "slowed"},
            _row(kind="Projectile")]
    assert mo.molly_zones(rows, ut.FIGURES["molly"]) == ([], {})


def test_an_unknown_owner_is_skipped_and_counted():
    zones, diagnostics = mo.molly_zones([_row(by=None), _row(), _row(code="Sarge", name="Q_Molotov_Production",
                                                                   by=None)], ut.FIGURES["molly"])
    assert [z[0] for z in zones] == [5]
    assert diagnostics == {"molly without a known owner": 2}


def test_a_molly_without_figures_or_a_place_is_skipped_and_counted():
    figures = {k: v for k, v in ut.FIGURES["molly"].items() if k != "Phoenix_MolotovFire"}
    zones, diagnostics = mo.molly_zones([_row(), _row(code="Sarge", name="Q_Molotov_Production", u=None)], figures)
    assert zones == []
    assert diagnostics == {"molly without figures": 1, "molly without a place": 1}


def _hash_with(tmp_path, edit):
    utility = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    edit(utility)
    path = tmp_path / "utility.json"
    path.write_text(json.dumps(utility), encoding="utf-8")
    return cf.figures_hash(utility=path)


@pytest.mark.parametrize("edit", [
    lambda u: u["molly"]["Phoenix_MolotovFire"].update(seconds=4.5),
    lambda u: u["molly"]["Killjoy_4_BeeSwarm_Damage"].update(radius_m=5.0),
])
def test_a_changed_molly_number_changes_figures_hash(tmp_path, edit):
    assert _hash_with(tmp_path, lambda u: None) != _hash_with(tmp_path, edit)


def test_a_changed_molly_note_does_not(tmp_path):
    def edit(u):
        for v in u["molly"].values():
            v["note"] = "measured in game"
        u["sources"]["molly"] = "tested in game, 2026-10-12"
    assert _hash_with(tmp_path, lambda u: None) == _hash_with(tmp_path, edit)


def test_a_row_that_ends_before_it_burns_or_has_no_time_is_skipped_and_counted():
    zones, diagnostics = mo.molly_zones([_row(t1=12.028), _row(t=float("nan")), _row(t=True)], ut.FIGURES["molly"])
    assert zones == []
    assert diagnostics == {"molly ended before it burned": 1, "molly without a time": 2}


# ---------------------------------------------------------------- per-side hazards (what P07a consumes)

SIDES = {0: "A", 2: "A", 5: "B", 9: "B"}


def _hazards(rows, sides=SIDES, diagnostics=None):
    zones, _ = mo.molly_zones(rows, ut.FIGURES["molly"])
    return mo.Hazards(zones, sides, diagnostics)


def test_a_molly_restricts_only_its_owners_sides_unknown():
    h = _hazards([_row(by=5, t=12.626, t1=19.8)])
    assert [z.by for z in h.hazards_at("B", 13.0)] == [5], "unknown[B] (where B thinks A is) is blocked by B's fire"
    assert h.hazards_at("A", 13.0) == [], "a player isn't blocked by their own team's molly"


def test_a_molly_burns_over_its_half_open_interval():
    h = _hazards([_row(by=0, t=12.626, t1=19.8)])
    assert h.hazards_at("A", 12.625) == [], "not before it lands"
    assert len(h.hazards_at("A", 12.626)) == 1
    assert len(h.hazards_at("A", 16.625)) == 1
    assert h.hazards_at("A", 12.626 + 4.0) == [], "end-exclusive"
    assert h.reopen_times("A") == [12.626 + 4.0] and h.reopen_times("B") == []
    assert h.transitions() == [12.626, 12.626 + 4.0]


def test_hazards_are_kept_per_side_in_time_order():
    h = _hazards([_row(by=9, t=40.3125, t1=50.0), _row(by=2, code="Sarge", name="Q_Molotov_Production", t=30.0625,
                                                        t1=45.0), _row(by=5, t=20.0625, t1=21.0)])
    assert [(z.by, z.t0) for z in h.by_side["B"]] == [(5, 20.0625), (9, 40.3125)]
    assert [(z.by, z.t1) for z in h.by_side["A"]] == [(2, 38.0625)], "Incendiary's 8 s"
    assert h.reopen_times("B") == [21.0, 44.3125]


def test_an_owner_without_a_side_is_counted_not_guessed_hostile():
    diagnostics = {}
    h = _hazards([_row(by=7)], diagnostics=diagnostics)
    assert h.by_side == {} and diagnostics == {"molly owner without a side": 1}


# ---------------------------------------------------------------- traversal (P07a)
#
# The corridor: 3 cells tall (rows 24-26, y 192-216), cols 12-51 (x 96-416); a side room nobody can walk to from it
# (x 96-140, y 250-290) holds the molly's owner, so nothing they see touches the corridor's unknown. The mover stands
# at col 50, row 25 and is the only source of the unknown measured. A 4.5 m molly at (300, 204) covers, by hand (cell
# centres within 4.5 m / 0.1395 m per px = 32.26 px): row 25 cols 33-41, rows 24 and 26 cols 34-40, so the corridor
# is shut from col 34 to col 40. S is one straight step of the unknown (a 1.116 m cell at UNKNOWN_MPS: 0.3445 s).

R2 = math.sqrt(2)
CORRIDOR = (96, 192, 416, 216)
ROOM = (96, 250, 140, 290)
MOVER = (404, 204, 180)
OWNER = (118, 270, 0)
FOOTPRINT = {25: range(33, 42), 24: range(34, 41), 26: range(34, 41)}


def corridor(name="MollyCorridor", specials=None):
    return toy_geometry(name, [CORRIDOR, ROOM], specials=specials)


def at(row, col):
    return row * GRID + col


def footprint():
    return {at(r, c) for r, cols in FOOTPRINT.items() for c in cols}


def step_s(geo):
    return geo.cell_m / ce.UNKNOWN_MPS


def octile(row, col, frm=(25, 50)):
    dy, dx = abs(row - frm[0]), abs(col - frm[1])
    return (max(dy, dx) - min(dy, dx)) + R2 * min(dy, dx)


# (id, players {slot: (side, (x, y, yaw))}, link sides or None, the side whose unknown is measured, mover, owner)
ROLES = [
    ("A's fire holds B", {5: ("B", MOVER), 0: ("A", OWNER)}, None, "A", 5, 0),
    ("B's fire holds A", {0: ("A", MOVER), 5: ("B", OWNER)}, None, "B", 0, 5),
    ("sides from the link, swapped", {5: (None, MOVER), 0: (None, OWNER)}, {5: "attack", 0: "defense"}, "B", 5, 0),
]


def molly(by, t, t1=20.0, x=300, y=204, code="Phoenix", name="MolotovFire", **extra):
    return toy_ability(code, name, x, y, by, t=t, t1=t1, kind="Patch", **extra)


def corridor_blob(players, util=(), t_end=10.0, deaths=None):
    return blob({s: (side, [(0.0, *p)]) for s, (side, p) in players.items()}, t_end=t_end, util=util, deaths=deaths)


def link_of(sides):
    return None if sides is None else ce.ControlLink(sides=sides)


def arrivals(data, geo, link=None, view="A", mover=5, **kw):
    """compute_round, and the measured enemy's arrival times at every analytical instant."""
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            r = unknown.reached[view].get(mover)
            out[round(float(tick.t), 6)] = None if r is None else r.copy()

    rc = ce.compute_round(data, geo, link, knowledge=False, observer=Watch(), **kw)
    return rc, out


def west_of_zone():
    return [at(r, c) for r in (24, 25, 26) for c in range(12, 34) if at(r, c) not in footprint()]


def check_row(r, t, want_of, cols):
    """Row 25's arrivals at instant t: each col's hand value where it is due by t, unreached otherwise."""
    for c in cols:
        want = want_of(c)
        if want <= t:
            assert r[at(25, c)] == pytest.approx(want, abs=1e-9), c
        else:
            assert not np.isfinite(r[at(25, c)]), c


def test_the_footprint_side_and_instants_are_the_rows():
    geo = corridor()
    rnd = ce.RoundInputs(corridor_blob(ROLES[0][1], [molly(0, 1.03)]), geo)
    [(side, zone, nodes)] = rnd.mollies
    assert side == "A" and (zone.t0, zone.t1) == (1.03, 1.03 + 4.0)
    assert set(nodes.tolist()) == footprint()
    assert {1.03, 5.03} <= set(rnd.transitions)
    assert not rnd.hazard_at("A", 1.0299).any() and rnd.hazard_at("A", 1.03).any()
    assert rnd.hazard_at("A", 5.0299).any() and not rnd.hazard_at("A", 5.03).any(), "end-exclusive"
    assert not rnd.hazard_at("B", 3.0).any(), "it never holds its own team's unknown"
    assert rnd.hazard_reopened("A", 5.0)[at(25, 37)] == -np.inf and rnd.hazard_reopened("A", 5.03)[at(25, 37)] == 5.03


@pytest.mark.parametrize("role", ROLES, ids=[r[0] for r in ROLES])
def test_a_hostile_molly_blocks_until_it_ends_and_arrivals_start_from_the_end(role):
    """Lands at 1.03 and ends at 5.03, both between the published frames. Nobody is hit (no damage row): it blocks
    all the same. By hand: the unknown reaches the zone's east rim at 3.10 s (col 41, 9 steps), so nothing at or
    west of the zone is reached before 5.03; then the cheapest way west starts at 5.03 from row 24/26 col 41
    (reached at 8 + sqrt 2 steps): row 25 col c at 5.03 + (40 - c + sqrt 2) S."""
    _, players, sides, view, mover, owner = role
    geo = corridor()
    data = corridor_blob(players, [molly(owner, 1.03)], t_end=10.0)
    assert not any(e.get("k") == "damage" for e in data["util"])
    rc, out = arrivals(data, geo, link_of(sides), view, mover)
    s = step_s(geo)
    assert {1.03, 5.03} <= {round(float(t), 6) for t in rc.analytic}
    assert not {1.03, 5.03} & {round(float(t), 6) for t in rc.ticks}
    shut = sorted(footprint()) + west_of_zone()
    for t, r in out.items():
        if t <= 5.03:
            assert not np.isfinite(r[shut]).any(), f"nothing crossed by {t}"
    assert np.isfinite(out[5.0][at(24, 41)]) and np.isfinite(out[5.0][at(25, 42)]), "the front waits at the rim"
    check_row(out[9.5], 9.5, lambda c: 5.03 + (40 - c + R2) * s, range(20, 34))


def test_the_rows_end_caps_the_burn():
    """The row closes at 4.03, before Hot Hands' 4 s are up: passage reopens at 4.03."""
    geo = corridor()
    _, players, _, view, mover, owner = ROLES[0]
    data = corridor_blob(players, [molly(owner, 1.03, t1=4.03)])
    assert ce.RoundInputs(data, geo).mollies[0][1].t1 == 4.03
    _, out = arrivals(data, geo, None, view, mover)
    s = step_s(geo)
    assert not np.isfinite(out[4.0][at(25, 40)])
    check_row(out[8.0], 8.0, lambda c: 4.03 + (40 - c + R2) * s, range(20, 34))


def test_uncertainty_inside_is_kept_not_grown_and_moves_again_only_from_the_end():
    """Lands at 5.95: by then the unknown is in every zone cell (the farthest, row 25 col 33, at 17 S = 5.86 s) but
    not past it (row 24 col 33 needs 16 + sqrt 2 steps = 6.00 s). While it burns (to 9.95) those arrivals are kept
    exactly and nothing new is entered; from 9.95 the enemy may walk out west, from 9.95 and not from 5.86: row 25
    col c (c <= 32) at 9.95 + (33 - c) S. No locate event comes of it."""
    geo = corridor()
    _, players, _, view, mover, owner = ROLES[0]
    rc, out = arrivals(corridor_blob(players, [molly(owner, 5.95)], t_end=12.0), geo, None, view, mover)
    plain, _ = arrivals(corridor_blob(players, t_end=12.0), geo, None, view, mover)
    s = step_s(geo)
    zone = sorted(footprint())
    kept = np.array([octile(*divmod(n, GRID)) * s for n in zone])
    for t, r in out.items():
        if 5.95 <= t <= 9.95:
            assert np.allclose(r[zone], kept, rtol=0, atol=1e-9), f"kept, not grown or dropped, at {t}"
            assert not np.isfinite(r[west_of_zone()]).any(), f"nobody walked out by {t}"
    check_row(out[11.5], 11.5, lambda c: 9.95 + (33 - c) * s, range(20, 33))
    for side in ("A", "B"):
        assert rc.reasons[side] == plain.reasons[side]


def test_a_molly_landing_behind_the_unknown_takes_nothing_away():
    """Lands at 8.0, after the unknown has passed through: the far side keeps spreading as if it weren't there."""
    geo = corridor()
    _, players, _, view, mover, owner = ROLES[0]
    _, out = arrivals(corridor_blob(players, [molly(owner, 8.0)]), geo, None, view, mover)
    _, plain = arrivals(corridor_blob(players), geo, None, view, mover)
    for t in plain:
        assert np.array_equal(out[t], plain[t]), t


@pytest.mark.parametrize("rows", [
    pytest.param(lambda mover, owner: [molly(mover, 1.03)], id="its own team's molly"),
    pytest.param(lambda mover, owner: [molly(owner, 1.03, code="Clay", name="Q_Explosion"),
                                       molly(owner, 1.03, code="Hunter", name="4_ExplosiveBolt_Explosion"),
                                       {**molly(owner, 1.03), "kind": "Projectile"}],
                 id="instant blasts and a molly still in flight"),
    pytest.param(lambda mover, owner: [molly(owner, 1.03, code="Sage", name="Q_SlowOrb")], id="a non-damaging area"),
])
def test_what_is_not_an_enemy_damaging_molly_holds_nothing(rows):
    geo = corridor()
    _, players, _, view, mover, owner = ROLES[0]
    _, out = arrivals(corridor_blob(players, rows(mover, owner)), geo, None, view, mover)
    _, plain = arrivals(corridor_blob(players), geo, None, view, mover)
    for t in plain:
        assert np.array_equal(out[t], plain[t]), t


def test_an_unknown_owner_adds_no_block_and_is_counted():
    geo = corridor()
    _, players, _, view, mover, _ = ROLES[0]
    rc, out = arrivals(corridor_blob(players, [molly(None, 1.03), molly(7, 1.03)]), geo, None, view, mover)
    _, plain = arrivals(corridor_blob(players), geo, None, view, mover)
    for t in plain:
        assert np.array_equal(out[t], plain[t]), t
    assert rc.missing_inputs.get("molly without a known owner (blocks nothing)") == 1
    assert rc.missing_inputs.get("molly owner without a side (blocks nothing)") == 1


def test_overlapping_mollies_hold_until_the_last_ends_through_the_owners_blind_and_death():
    """One at x 300 (1.03-5.03) and one at x 316 (3.0-7.0), both the owner's; the owner is blinded 1.5-1.9 and dies
    at 2.0. The second covers row 25 cols 35-43, rows 24/26 cols 36-42: what the first alone covered reopens at 5.03
    but is closed in by the second until 7.0. Where the second landed on ground the unknown already held (row 25
    col 42, 8 S) it is kept; from 7.0 it moves again: row 25 col c (c <= 32) at 7.0 + (42 - c) S."""
    geo = corridor()
    _, players, _, view, mover, owner = ROLES[0]
    flash = {"k": "flash", "t": 1.2, "by": mover, "ability": "phoenix_curveball_left", "targets": [owner],
             "hits": [[owner, 1.5, 0.4]]}
    util = [molly(owner, 1.03), molly(owner, 3.0, x=316), flash]
    _, out = arrivals(corridor_blob(players, util, t_end=12.0, deaths={owner: 2.0}), geo, None, view, mover)
    s = step_s(geo)
    first_only = [at(25, 33), at(25, 34), at(24, 34), at(24, 35), at(26, 34), at(26, 35)]
    for t, r in out.items():
        if t <= 7.0:
            assert not np.isfinite(r[first_only + west_of_zone()]).any(), t
    assert out[6.5][at(25, 42)] == pytest.approx(8 * s, abs=1e-9), "kept from before the second landed"
    check_row(out[11.5], 11.5, lambda c: 7.0 + (42 - c) * s, range(20, 33))


def _dijkstra(geo, source, blocked, s):
    """Earliest arrivals from `source` at time 0 over the walkable cells, octile steps of `s`, never into a
    `blocked` cell and never diagonally past one."""
    walk = geo.walk.ravel()
    best = {source: 0.0}
    heap = [(0.0, source)]
    while heap:
        d, cell = heapq.heappop(heap)
        if d > best[cell]:
            continue
        y, x = divmod(cell, GRID)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                n = (y + dy) * GRID + x + dx
                if not (dy or dx) or not walk[n] or n in blocked:
                    continue
                if dy and dx and ((y + dy) * GRID + x in blocked or y * GRID + x + dx in blocked):
                    continue
                nd = d + s * (R2 if dy and dx else 1.0)
                if nd < best.get(n, math.inf):
                    best[n] = nd
                    heapq.heappush(heap, (nd, n))
    return best


def test_a_route_around_a_partly_covered_hall_stays_open():
    """A hall 11 rows tall (y 160-248): the molly covers rows 21-29 at its centre, so rows 20 and 30 go round it. Two
    Incendiaries back to back (0.5-7.5, 7.0-14.0) keep it burning. Every arrival matches a test-local Dijkstra."""
    geo = toy_geometry("MollyHall", [(96, 160, 416, 248), (96, 290, 140, 330)])   # the room well clear of sight
    players = {5: ("B", MOVER), 0: ("A", (118, 310, 0))}
    util = [molly(0, 0.5, code="Sarge", name="Q_Molotov_Production"),
            molly(0, 7.0, code="Sarge", name="Q_Molotov_Production")]
    _, out = arrivals(corridor_blob(players, util, t_end=10.0), geo, None, "A", 5)
    s = step_s(geo)
    r_px = 4.5 / geo.m_per_px
    blocked = {at(r, c) for r in range(20, 31) for c in range(12, 52)
               if ((c * 8 + 4) - 300) ** 2 + ((r * 8 + 4) - 204) ** 2 <= r_px ** 2}
    assert len(blocked) == 49 and at(21, 37) in blocked and at(20, 37) not in blocked
    best = _dijkstra(geo, at(25, 50), blocked, s)
    got = out[9.5]
    for n in range(geo.n):
        want = best.get(n, math.inf)
        if want <= 9.5:
            assert got[n] == pytest.approx(want, abs=1e-9), divmod(n, GRID)
        else:
            assert not np.isfinite(got[n]), divmod(n, GRID)
    assert best[at(25, 30)] > octile(25, 30) * s + 0.5, "the way round is longer"


def test_a_special_link_over_the_zone_still_carries_the_unknown_but_not_into_it():
    """One-way links from col 45 to col 28 (over the zone, both ends outside) and from col 46 to col 37 (into it).
    The first is a verified route past the fire: row 25 col 28 at 6 S, and on from there. The second lands nobody
    in the fire: col 37 waits for the end (5.03) and is entered then from the link, at 5.03 + S (shown from 6.0, once
    the walk from the west joins it: a lone cell is the existing sliver rule's)."""
    specials = [{"kind": "teleport", "a": list(uv(364, 204)), "b": list(uv(228, 204)), "one_way": True},
                {"kind": "teleport", "a": list(uv(372, 204)), "b": list(uv(300, 204)), "one_way": True}]
    geo = corridor("MollyCorridorLinks", specials)
    _, players, _, view, mover, owner = ROLES[0]
    _, out = arrivals(corridor_blob(players, [molly(owner, 1.03)]), geo, None, view, mover)
    s = step_s(geo)
    check_row(out[4.5], 4.5, lambda c: (6 + abs(c - 28)) * s, range(20, 33))
    for t, r in out.items():
        if t <= 5.03:
            assert not np.isfinite(r[sorted(footprint())]).any(), t
    assert out[6.0][at(25, 37)] == pytest.approx(5.03 + s, abs=1e-9)


def test_a_molly_blocks_only_the_floor_it_burns_on():
    """A bridge (4 m) over the corridor at x 256-344, not joined to the ground. A molly on the bridge leaves the
    ground route as it was; the same molly on the ground holds it."""
    geo = toy_heights("MollyBridge", [CORRIDOR, ROOM], upper=[((256, 192, 344, 216), 4.0)])
    players = {5: standing(*MOVER[:2], 0.0, "B", 180), 0: standing(*OWNER[:2], 0.0, "A", 0)}
    up = molly(0, 1.03, z=z_dm(4.0), code="Sarge", name="Q_Molotov_Production")
    ground = molly(0, 1.03, z=z_dm(0.0), code="Sarge", name="Q_Molotov_Production")

    def run(util):
        return arrivals(height_blob(players, t_end=8.0, util=util), geo, None, "A", 5)[1]

    rnd = ce.RoundInputs(height_blob(players, t_end=8.0, util=[up, ground]), geo)
    (_, _, top), (_, _, low) = rnd.mollies
    assert len(top) == len(low) == len(footprint())
    assert np.allclose(geo.node_z[top], 4.0) and np.allclose(geo.node_z[low], 0.0)
    plain, over, held = run([]), run([up]), run([ground])
    for t in plain:
        assert np.array_equal(over[t], plain[t]), t
    target = geo.node_at(at(25, 30), 0.0)
    assert np.isfinite(plain[7.5][target]) and not np.isfinite(held[7.5][target])


# ---------------------------------------------------------------- counterfactual, knowledge, gaps


def _watch_round():
    """A1 stands at col 27 facing west, holding the corridor west of col 31; B5 at col 50; A0's molly 1.03-5.03."""
    geo = corridor()
    players = {5: ("B", MOVER), 0: ("A", OWNER), 1: ("A", (220, 204, 180))}
    return geo, corridor_blob(players, [molly(0, 1.03)], t_end=8.0)


def _ticks_to(rnd, until):
    runner = ce.TickRunner(rnd.geo)
    tick = None
    for t in rnd.analytic_times(rnd.tick_times(), own=True):
        if t > until:
            break
        tick = runner.step(ce.Tick(rnd, float(t)))
    return tick


def test_the_counterfactual_without_a_holder_does_not_flood_through_the_fire():
    """By hand: removing A1 opens the corridor west of the zone, but the unknown's only sources are east of it, so
    while it burns nothing is gained; once it has ended the west opens to them."""
    geo, data = _watch_round()
    rnd = ce.RoundInputs(data, geo)
    during = _ticks_to(rnd, 3.0)
    assert np.array_equal(during.unknown_without("A", 1), during.unknown["A"])
    after = _ticks_to(rnd, 7.0)
    gained = after.unknown_without("A", 1) & ~after.unknown["A"]
    assert gained[[at(25, c) for c in range(12, 30)]].any(), "the rule, not the geometry, held it"


def test_full_and_incremental_counterfactuals_agree_on_a_molly_round():
    geo, data = _watch_round()
    rc = ce.compute_round(data, geo, full_every=1, knowledge=False)
    assert rc.cf_check["player_ticks"] > 0 and rc.cf_check["identical"] == rc.cf_check["player_ticks"]


def test_the_knowledge_picture_does_not_walk_through_the_fire():
    """A's picture of B5 (never seen) grows from B5's start at KNEW_RUN_MPS: by 4.5 s about 27 cells, but not past
    the burning zone, so its west-most cells are row 24/26 col 41 (row 25 col 41 burns)."""
    geo, data = _watch_round()
    rnd = ce.RoundInputs(data, geo)
    seed = ce.Knowledge(rnd, "A").tick_for(_ticks_to(rnd, 4.5), 4.5).seeds["B"]
    assert (np.flatnonzero(seed) % GRID).min() == 41 and seed[at(24, 41)] and not seed[at(25, 41)]
    later = ce.Knowledge(rnd, "A").tick_for(_ticks_to(rnd, 6.0), 6.0).seeds["B"]
    assert (np.flatnonzero(later) % GRID).min() < 33, "past it once it has ended"


def test_cached_and_live_gaps_agree_on_a_molly_round(tmp_path):
    assert cache.FORMAT == 1, "no gaps cache shape change for the molly rule"
    geo = corridor()
    _, players, _, _, _, owner = ROLES[0]
    data = corridor_blob(players, [molly(owner, 1.03)], t_end=8.0)
    live = gd.GapDetector(geo, ce.RoundInputs(data, geo))
    path = cache.cache_path(1, 1, "molly", tmp_path)
    writer = cache.Writer(path)

    def both(rec, unk):
        live.step(rec, unk.log)
        writer(rec, unk)

    rc = ce.compute_round(data, geo, observer=both, knowledge=False)
    writer.close(rc.missing_inputs)
    replayed = gd.GapDetector(geo, ce.RoundInputs(data, geo))
    for rec, logs in cache.replay(path):
        replayed.step(rec, logs)
    assert to_rows(live.finish(), live.rnd, geo) == to_rows(replayed.finish(), replayed.rnd, geo)


def test_a_round_without_a_molly_matches_its_reference_digest():
    from tests.replays.control_toys import reference_rounds

    pinned = json.loads(ref.FIXTURE.read_text(encoding="utf-8"))["rounds"]
    geo, data, link = reference_rounds()["open"]
    assert not any(mo.molly_key(e) for e in data["util"])
    assert ce.RoundInputs(data, geo).mollies == []
    assert ref.digest_round(ce.compute_round(data, geo, link), data) == pinned["open"]
