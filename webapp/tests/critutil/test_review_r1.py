"""Branch review regressions, with synthetic exports and anonymous slots only."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.control import engine
from app.critutil import extract, info, windows
from app.critutil.unknown import InfoMeasure, UnknownProbe
from app.services import util_values
from tests.moments.helpers import kill, round_of, toy_ability
from tests.replays.control_toys import open_hall, uv


PLAYERS = {0: ("A", [(0.0, 200, 200, 180)]), 5: ("B", [(0.0, 250, 200, 0)])}


def blast(t=7.0, **extra):
    # The export's actorless row: u/v are the shooter's position, not a placed area.
    u, v = uv(200, 200)
    return {"k": "blast", "t": t, "by": 0, "code": "Hunter", "name": "X_LaserMulti",
            "u": u, "v": v, "yaw": 180, "hits": [], "hits_read": True, "fired": True, **extra}


def test_f2_fury_origin_never_holds_an_area_or_takes_shared_credit():
    molly = toy_ability("Sarge", "Q_Molotov_Production", 200, 200, 0, t=7, t1=13)
    spike = {"k": "ability", "kind": "Bomb", "t": 6, "by": 0, "u": molly["u"], "v": molly["v"],
             "defuses": [[8, 9, 5, False]]}
    rnd, blob = round_of(PLAYERS, util=[blast(), molly, spike], geo=open_hall())
    item = {"what": "molly", "kind": "molly", "by": 0, "id": None, "uv": [molly["u"], molly["v"]],
            "t0": 7, "t1": 13, "up_s": 6, "front_s": 4, "held_m2s": 8, "crossed_s": 0}
    out = extract.round_util(rnd, blob, holds={"items": [item], "error": None, "info": []})
    fury = next(c for c in out["casts"] if c["ability_id"] == "Sova/Hunter's Fury")
    placed = next(c for c in out["casts"] if c["ability_id"] == "Brimstone/Incendiary")
    assert placed["presence"]["waiting_s"] > 0
    assert fury["presence"] is None
    assert fury["spike"] is None
    assert fury["hold"] is None
    assert placed["hold"] == {k: item[k] for k in ("up_s", "front_s", "held_m2s", "crossed_s")}
    assert all(util_values.features(fury)[k] == 0 for k in ("waiting_s", "held_back", "defuse_stopped"))
    rnd, blob = round_of(PLAYERS, util=[molly, spike], geo=open_hall())
    (reference,) = extract.round_util(rnd, blob)["casts"]
    assert placed["presence"] == reference["presence"]
    assert placed["spike"] == reference["spike"]


@pytest.mark.parametrize("ending", ["final", "t1", "gone", "death", "hold", "unknown", "competing"])
def test_f6_fury_ending_cuts_the_cast_active_span_and_value_window(ending):
    rows = [blast(), blast(9, **({ending: True if ending == "final" else 9} if ending in {"final", "t1", "gone"} else {}))]
    if ending == "competing":
        rows = [blast(t1=12), blast(9, final=True)]
    if ending == "hold":
        rows.append({"k": "hold", "t": 9, "by": 0, "gun": "Vandal", "cat": "rifle", "id": 301, "gen": 0})
    rnd, blob = round_of(PLAYERS, util=rows, geo=open_hall(),
                         kills=[kill(9, 5, 0)] if ending == "death" else [])
    (raw,) = extract.build_casts(rnd, blob)
    (cast,) = extract.round_util(rnd, blob)["casts"]
    end = 13 if ending == "unknown" else 9
    assert cast["t_end"] == end
    assert cast["signals"]["seen_end"] == (None if ending == "unknown" else 9)
    assert extract._active_spans(rnd, raw) == [[7, end]]
    assert windows.window(cast, raw.entry, rnd.t_decided)["segments"] == [[7, end + raw.entry["grace_s"]]]


def test_f6_final_blast_does_not_absorb_a_later_use():
    rnd, blob = round_of(PLAYERS, util=[blast(), blast(9, final=True), blast(10)], geo=open_hall())
    casts = extract.build_casts(rnd, blob)
    assert [(c.t, c.t_end, len(c.rows)) for c in casts] == [(7, 9, 2), (10, 16, 1)]


@pytest.mark.parametrize("name", ["X_Segment", "E_AirStrikeMortar"])
@pytest.mark.parametrize("case", ["second", "both", "crossed", "expired"])
def test_f4_multi_piece_molly_unions_live_footprints_once_and_preserves_hold(name, case):
    rows = [toy_ability("Cashew", name, x, 200, 0, t=5, t1=12, kind="GameObject", yaw=0)
            for x in (150, 350)]
    if name == "E_AirStrikeMortar":
        rows.insert(0, toy_ability("Cashew", "E_MapMissileMarker", 150, 200, 0, t=4.5, t1=12))
    rnd, blob = round_of(PLAYERS, util=rows, geo=open_hall())
    casts = extract.build_casts(rnd, blob)
    assert len(casts) == 1
    probe = UnknownProbe(rnd)
    assert len(probe.items) == 2
    if case == "expired":
        probe.items[0]["t1"] = 5.1
    measure = InfoMeasure(probe)
    first, second = [item["mask"] for item in probe.items]
    ring = probe.topo.dilate(first | second) & ~(first | second)
    unknown = np.zeros(probe.n, bool)
    edge = probe.items[1]["ring"] & ring
    unknown[np.flatnonzero(edge)[0]] = True
    if case == "both":
        unknown[np.flatnonzero(probe.items[0]["ring"] & ring)[0]] = True
    elif case == "crossed":
        unknown[np.flatnonzero(first)[0]] = True
    for t in (5, 5.5):
        measure.step(SimpleNamespace(t=t, players={}, applied={},
                                    unknown={"A": {5: np.where(unknown, 0, -1)}}))
    holds = probe.finish()
    holds["info"] = measure.finish()
    (cast,) = extract.round_util(rnd, blob, holds=holds)["casts"]
    assert cast["info"]["hold_s"] == (0 if case == "crossed" else 0.5)
    legacy = extract.round_util(rnd, blob, holds={"items": holds["items"], "error": None})["casts"][0]
    assert cast["hold"] == legacy["hold"]
    assert len(holds["info"]) == 1


HIT_SOURCES = [("BountyHunter", "4_WolfHound"), ("Aggrobot", "SeekerNade"), ("Guide", "X_Pack"),
               ("Aggrobot", "RollyPolly"), ("BountyHunter", "X_WaveForm"),
               ("BountyHunter", "Q_Tether_SphereExpansion"), ("Aggrobot", "E_DiscTurret_PowerWave"),
               ("Mage", "4_SplashGrenade"), ("Stealth", "4_Decoy_V2")]
LEGACY_SOURCES = [("Hunter", "Q_RevealBolt"), ("Sarge", "Q_Molotov_Production"),
                  ("Gumshoe", "4_TripWire"), ("Gumshoe", "E_PossessableCamera"),
                  ("Hunter", "E_Drone"), ("Killjoy", "E_Turret"), ("Killjoy", "Q_StealthAlarmbot")]


def hit_source_rows(source, t=5, stamp=None):
    if source == ("Hunter", "X_LaserMulti"):
        row = blast(t)
        row.pop("hits_read")
    else:
        row = toy_ability(*source, 200, 200, 0, t=t, t1=t + 2, kind="GameObject")
    if stamp is not None:
        row["hits_read"] = stamp
    # These stamped sources are pieces of the table's parent ability, as in condensed exports.
    parent = {("BountyHunter", "Q_Tether_SphereExpansion"): "Q_TetherGrenade_SphereExpansion",
              ("Mage", "4_SplashGrenade"): "harbor_storm_surge"}.get(source)
    return ([toy_ability(source[0], parent, 200, 200, 0, t=t - 0.1, t1=t + 2)] if parent else []) + [row]


@pytest.mark.parametrize("source", HIT_SOURCES + LEGACY_SOURCES + [("Hunter", "X_LaserMulti")])
@pytest.mark.parametrize("stamp", [None, False, True])
def test_f7_each_cast_requires_only_its_own_source_coverage(source, stamp):
    rnd, blob = round_of(PLAYERS, util=hit_source_rows(source, stamp=stamp), geo=open_hall())
    probe = UnknownProbe(rnd)
    # No tick operation: a successful current probe, including measured zeros.
    out = extract.round_util(rnd, blob, holds=probe.finish())
    (cast,) = out["casts"]
    covered = source in LEGACY_SOURCES or stamp is True
    assert cast["info"] == (info.empty() if covered else None)
    result = info.summarise([{"match": "synthetic", "player": "slot-0", "ability_id": cast["ability_id"],
                              "info": cast["info"], "scored": True}], ci=lambda *args: (0, 0))
    assert bool(result["overall"]["clears"] or result["overall"]["holds"]) is covered


@pytest.mark.parametrize("current", [False, True])
def test_f7_mixed_cast_coverage_through_engine_probe_and_summary(current):
    prowler = toy_ability("BountyHunter", "4_WolfHound", 200, 200, 0, t=5, t1=7, kind="Pawn", id=201)
    if current:
        prowler["hits_read"] = True
    recon = toy_ability("Hunter", "Q_RevealBolt", 200, 200, 0, t=5, t1=7, kind="Projectile", id=202)
    trip = toy_ability("Gumshoe", "4_TripWire", 200, 200, 0, t=5, t1=7, kind="GameObject", id=203)
    drone = toy_ability("Hunter", "E_Drone", 200, 200, 0, t=5, t1=7, kind="Pawn", id=204)
    rnd, blob = round_of(PLAYERS, util=[prowler, recon, trip, drone], geo=open_hall(), t_end=10)
    probe = UnknownProbe(rnd)
    engine.compute_round(blob, rnd.geo, observer=lambda rec, _: probe.step(rec), knowledge=False)
    holds = probe.finish()
    assert holds["error"] is None and holds["info"] is not None
    out = extract.round_util(rnd, blob, holds=holds)
    assert len(out["casts"]) == 4
    assert all((cast["info"] is not None) is (current or cast["ability_id"] != "Fade/Prowler")
               for cast in out["casts"])
    casts = [{**cast, "match": "synthetic", "player_id": 0} for cast in out["casts"]]
    summary = info.summarise(casts, ci=lambda *args: (0, 0))
    assert sum(row["n"] for row in summary["overall"]["clears"]) == (3 if current else 2)
    assert sum(row["n"] for row in summary["overall"]["holds"]) == 2


@pytest.mark.parametrize("source", HIT_SOURCES + [("Hunter", "X_LaserMulti")])
@pytest.mark.parametrize("other_cast", ["same_ability", "unrelated"])
def test_f7_another_casts_stamp_never_supplies_missing_coverage(source, other_cast):
    first = hit_source_rows(source)
    second = hit_source_rows(("Hunter", "X_LaserMulti") if other_cast == "unrelated" else source,
                             t=20, stamp=True)
    rnd, blob = round_of(PLAYERS, util=first + second, geo=open_hall())
    casts = extract.round_util(rnd, blob, holds={"error": None, "items": [], "info": []})["casts"]
    assert len(casts) == 2
    assert casts[0]["info"] is None
    assert casts[1]["info"] == info.empty()


def test_f7_partial_fury_sources_do_not_claim_complete_measurement():
    rnd, blob = round_of(PLAYERS, util=[blast(), blast(9, hits_read=False, final=True)], geo=open_hall())
    (cast,) = extract.round_util(rnd, blob, holds={"error": None, "items": [], "info": []})["casts"]
    assert cast["info"] is None


def test_f7_coverage_sources_match_the_condenser_and_ability_table():
    from app.replays.extras import HITS_READ

    sources = extract.INFO_HIT_SOURCES
    assert set(sources.values()) == HITS_READ | {"Hunter_X_LaserMulti"}
    for ability, source in sources.items():
        code, name = source.split("_", 1)
        rnd, blob = round_of(PLAYERS, util=hit_source_rows((code, name)), geo=open_hall())
        (cast,) = extract.build_casts(rnd, blob)
        assert f"{cast.entry['agent']}/{cast.entry['ability']}" == ability


@pytest.mark.parametrize("source", [("BountyHunter", "Q_Tether_SphereExpansion"), ("Mage", "4_SplashGrenade")])
def test_f7_stamp_on_parent_does_not_cover_missing_piece(source):
    rows = hit_source_rows(source)
    rows[0]["hits_read"] = True
    rnd, blob = round_of(PLAYERS, util=rows, geo=open_hall())
    (cast,) = extract.round_util(rnd, blob, holds={"error": None, "items": [], "info": []})["casts"]
    assert cast["info"] is None


def test_f7_mixed_old_sources_preserve_nonzero_trip_and_drone_measurements():
    rows = [toy_ability("BountyHunter", "4_WolfHound", 200, 200, 0, t=5, t1=7, kind="Pawn", id=201),
            toy_ability("Gumshoe", "4_TripWire", 200, 200, 0, t=5, t1=7, kind="GameObject", id=202),
            toy_ability("Hunter", "E_Drone", 200, 200, 0, t=5, t1=7, kind="Pawn", id=203)]
    measured = {"cleared_m2": 12.5, "located": 1, "hold_s": 2.0}
    entries = [{"cast_id": actor, "row_key": None, "item": None, "id": None, "by": 0,
                "kind": "info", "t0": 5, "uv": None, **measured} for actor in (201, 202, 203)]
    rnd, blob = round_of(PLAYERS, util=rows, geo=open_hall())
    casts = extract.round_util(rnd, blob, holds={"error": None, "items": [], "info": entries})["casts"]
    assert {cast["ability_id"]: cast["info"] for cast in casts} == {
        "Fade/Prowler": None, "Cypher/Trapwire": measured, "Sova/Owl Drone": measured}
