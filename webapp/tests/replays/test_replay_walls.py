"""Moving utility and walls in the stored round (the 2026-10-05 review, items 20, 22-23, 27-28): a
projectile's flight, Blaze's laid line, a Barrier Orb's segments and a Shear's line and raised span. Real rows
from tests/fixtures/replays/utility; a raised Shear is built from the parser's row shape, since every local
Shear went untriggered."""

import json
from pathlib import Path

import pytest

from app.replays import extras as ex
from app.replays.condense import MapInfo, PlayerTable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
GAME_MAP = MapInfo("Testmap", "Test", 1e-5, 1e-5, 0.5, 0.5)
AGENTS = {"Phoenix": "Phoenix", "Thorne": "Sage", "Nox": "Vyse", "Wushu": "Jett"}


def players() -> PlayerTable:
    agents = ["Phoenix", "Sage", "Vyse", "Jett", "Omen", "Cypher", "Reyna", "Killjoy", "Breach", "Viper"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot={1230: 0, 588: 1, 696: 2, 1220: 3},
                       other_slot={}, pawn_changes={}, gone_ms={})


def rows_of(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8").splitlines() if line]


def build(tmp_path, rows, start_ms, length_ms=120_000):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return ex.build_extras(path, players(), [(start_ms, start_ms + length_ms, start_ms + length_ms)], GAME_MAP, AGENTS,
                           lambda t_ms: {})


def uv(x, y):
    return list(GAME_MAP.to_uv(x, y))


def test_a_projectile_keeps_its_flight_in_world_units(tmp_path):
    extras = build(tmp_path, rows_of("phoenix_blaze"), 500_000)
    shot = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "Q_FlameWall_ThroughWall")
    assert shot["kind"] == "Projectile" and len(shot["flight"]) >= 5
    # the first replicated sample (76.72, 93.64 m) lands on the spawn point (7672.3, 9364.2 units), not near 0
    assert shot["flight"][0][1:3] == pytest.approx([shot["u"], shot["v"]], abs=1)
    assert shot["flight"][0][3] == 27                                    # 2.72 m -> 272 units -> dm
    times = [p[0] for p in shot["flight"]]
    assert times == sorted(times) and times[0] >= shot["t0"] and times[-1] <= shot["t1"]
    assert all(b - a >= 0.099 for a, b in zip(times, times[1:-1]))        # thinned; the last sample is always kept
    assert shot["flight"][-1][1:3] != shot["flight"][0][1:3]
    assert extras.report["projectile_flights"] == 1


def test_blaze_is_a_wall_line_along_its_projectiles_flight(tmp_path):
    extras = build(tmp_path, rows_of("phoenix_blaze"), 500_000)
    by = {r["name"]: r for r in extras.rounds[1]["abilities"]}
    wall, shot = by["Q_FlameWallManager_Production"], by["Q_FlameWall_ThroughWall"]
    assert wall["slot"] == 0 and wall["on"] == [[wall["t0"], wall["t1"]]]
    assert wall["t1"] - wall["t0"] == pytest.approx(10.687, abs=0.001)
    assert wall["points"][0] == [shot["u"], shot["v"]]                  # it starts at the cast
    assert len(wall["points"]) >= 8                                     # dense enough to carry a curve
    assert wall["points"][-1] == shot["flight"][-1][1:3]                # and ends where the flight ended
    ahead = [(q[0] - p[0], q[1] - p[1]) for p, q in zip(wall["points"], wall["points"][1:])]
    assert all(abs(du) + abs(dv) > 0 for du, dv in ahead[1:])           # it keeps moving: a line, not a point
    assert extras.report["blaze_lines"] == 1


def test_blaze_without_a_flight_is_a_point_and_is_counted(tmp_path):
    rows = [r for r in rows_of("phoenix_blaze")
            if not (r["type"] == "export_group_received" and "ReplicatedMovement" in (r.get("payload") or {}))]
    extras = build(tmp_path, rows, 500_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "Q_FlameWallManager_Production")
    assert "points" not in wall and "on" not in wall
    assert extras.report["blaze_without_a_line"] == 1 and "projectile_flights" not in extras.report


def test_a_barrier_orb_keeps_each_segments_place_and_span(tmp_path):
    extras = build(tmp_path, rows_of("sage_wall"), 60_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "E_Wall_Fortifying")
    assert wall["slot"] == 1 and len(wall["segments"]) == 4
    assert wall["segments"][0][:2] == uv(5137.3, 746.3) and wall["segments"][3][:2] == uv(4618.0, 720.6)
    assert all(seg[2] == pytest.approx(4.1, abs=0.001) for seg in wall["segments"])       # all four rise at once
    assert all(seg[3] == pytest.approx(44.101, abs=0.001) for seg in wall["segments"])    # 40.0 s later
    assert wall["t1"] == pytest.approx(45.101, abs=0.001)                                 # the root goes a second after


def test_a_segment_shot_out_early_ends_alone(tmp_path):
    rows = rows_of("sage_wall")
    first = next(r for r in rows if r["type"] == "valorant_wall_segment_destroyed" and r["segment_index"] == 1)
    first["time_ms"] = 80_000
    rows.sort(key=lambda r: r["time_ms"])
    wall = next(r for r in build(tmp_path, rows, 60_000).rounds[1]["abilities"] if r["name"] == "E_Wall_Fortifying")
    assert [seg[3] for seg in wall["segments"]] == [pytest.approx(44.101, abs=0.001), 20.0,
                                                    pytest.approx(44.101, abs=0.001), pytest.approx(44.101, abs=0.001)]


def test_an_untriggered_shear_has_its_line_and_was_never_raised(tmp_path):
    extras = build(tmp_path, rows_of("vyse_wall_untriggered"), 20_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "WallTrap")
    assert wall["slot"] == 2 and wall["line"] == [uv(7500.0, -3404.44), uv(6800.27, -3404.44)]
    assert "raised" not in wall and "trigger" not in wall
    assert extras.report["vyse_walls"] == 1 and "vyse_walls_raised" not in extras.report


def test_a_triggered_shear_has_its_raised_span_and_who_crossed(tmp_path):
    """The parser's activation row (ReplayEventJsonWriter.WriteValorantWallActivated) and the destruction of the
    active wall actor: the wall stood between them. The crossing itself has no time of its own in the export."""
    rows = [r for r in rows_of("vyse_wall_untriggered") if r["type"] != "valorant_wall_destroyed"]
    placed = next(r for r in rows if r["type"] == "valorant_wall_placed")
    activated = {"type": "valorant_wall_activated", "time_ms": 61_700, "wall_actor_net_guid": 3756,
                 "wall_kind": "vyse_shear", "active_wall_actor_net_guid": 4999, "location": placed["location"],
                 "wall_start": placed["wall_start"], "wall_end": placed["wall_end"],
                 "trigger_character_net_guid": 1220, "evidence": "vyse_dynamic_collision_enabled"}
    destroyed = {"type": "valorant_wall_destroyed", "time_ms": 67_700, "wall_actor_net_guid": 3756,
                 "wall_kind": "vyse_shear", "active_wall_actor_net_guid": 4999, "location": placed["location"],
                 "evidence": "vyse_active_wall_actor_destroyed"}
    rows = sorted(rows + [activated, destroyed], key=lambda r: r["time_ms"])
    extras = build(tmp_path, rows, 20_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "WallTrap")
    assert wall["raised"] == [41.7, 47.7] and wall["trigger"] == 3
    assert extras.report["vyse_walls_raised"] == 1


def test_a_shear_whose_wall_never_reports_its_end_is_raised_with_no_known_end(tmp_path):
    rows = rows_of("vyse_wall_untriggered")
    placed = next(r for r in rows if r["type"] == "valorant_wall_placed")
    activated = {"type": "valorant_wall_activated", "time_ms": 61_700, "wall_actor_net_guid": 3756,
                 "wall_kind": "vyse_shear", "active_wall_actor_net_guid": 4999, "wall_start": placed["wall_start"],
                 "wall_end": placed["wall_end"], "trigger_character_net_guid": None,
                 "evidence": "vyse_dynamic_collision_enabled"}
    rows = sorted(rows + [activated], key=lambda r: r["time_ms"])     # its `destroyed` row is the untriggered kind
    wall = next(r for r in build(tmp_path, rows, 20_000).rounds[1]["abilities"] if r["name"] == "WallTrap")
    assert wall["raised"] == [41.7, None] and wall["trigger"] is None


def test_a_shear_without_both_ends_has_no_line_and_is_counted(tmp_path):
    rows = rows_of("vyse_wall_untriggered")
    next(r for r in rows if r["type"] == "valorant_wall_placed")["wall_end"] = None
    extras = build(tmp_path, rows, 20_000)
    wall = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "WallTrap")
    assert "line" not in wall and extras.report["vyse_walls_without_a_line"] == 1
