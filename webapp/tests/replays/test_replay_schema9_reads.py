"""Schema 9 reads: each payload field a schema 8 reader found data in still finds it on schema 9's shape.

The rows under tests/fixtures/replay/schema9_rows/ are real schema 9 rows trimmed from a real export; the
tests read them through `read_raw`/`build_extras`/`read_ndjson` (so `contract.loads_row` normalises them), never
through hand-built dicts of the schema 9 shape. The schema 8 cases are the shapes the committed fixtures use."""

import base64
import json
from pathlib import Path

import pytest

from app.replays import player_state_extract as pse
from app.replays.condense import MapInfo, PlayerTable
from app.replays.contract import loads_row, read_ndjson
from app.replays.extras import (MESH_NODE, actor_list_guids, build_extras, damage_origin_point, fragment_key,
                                packed_vector, read_raw)

ROWS = Path(__file__).resolve().parents[1] / "fixtures" / "replay" / "schema9_rows"
GAME_MAP = MapInfo("Testmap", "Test", 1e-4, 1e-4, 0.5, 0.5)
AGENTS = {"Gumshoe": "Cypher"}
WINDOWS = [(10_000, 50_000, 60_000), (380_000, 420_000, 430_000)]


def rows_of(name):
    return [loads_row(line) for line in (ROWS / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def two_cyphers() -> PlayerTable:
    agents = ["Omen", "Cypher", "Sova", "Jett", "Sage", "Omen", "Cypher", "Killjoy", "Breach", "Viper"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot={998: 1, 692: 6},
                       other_slot={50: 1, 40: 6}, pawn_changes={}, gone_ms={})


# ------------------------------------------------------------------------------------------------ helpers

def test_actor_list_guids_reads_both_shapes():
    assert actor_list_guids([{"NetGuid": 3332}]) == frozenset({3332})
    assert actor_list_guids([3332, {"NetGuid": 5}]) == frozenset({3332, 5})
    assert actor_list_guids([]) == frozenset()
    assert actor_list_guids([{"NetGuid": 0}, {"Other": 1}]) == frozenset()
    # schema 8: a base64 packed list, leading count dropped, zeros dropped
    schema8 = {"BitCount": 64, "Data": "AgIGIHE2AAA=", "TypeName": "Actors"}
    assert actor_list_guids(schema8) == frozenset({3512})
    assert actor_list_guids({"Data": "!!"}) == frozenset()
    assert actor_list_guids(None) == frozenset()


def test_damage_origin_point_reads_both_shapes():
    assert damage_origin_point({"x": 1034.54, "y": 2648.61, "z": 276.3}) == (1034.54, 2648.61, 276.3)
    assert damage_origin_point({"x": 1, "y": 2}) is None
    assert damage_origin_point(None) is None
    # schema 8: the packed vector, in the units packed_vector gives
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility" / "deadlock_wall_full.ndjson"
    origin = next(r["payload"]["DamageOrigin"] for r in map(json.loads, fixture.read_text(encoding="utf-8").splitlines())
                  if r.get("type") == "rpc_received" and isinstance((r.get("payload") or {}).get("DamageOrigin"), dict))
    want = packed_vector(origin["BitCount"], base64.b64decode(origin["Data"]))
    assert want is not None and damage_origin_point(origin) == want
    assert damage_origin_point({"BitCount": 8, "Data": base64.b64encode(b"\x00").decode()}) is None


def test_fragment_key_prefers_replication_key_then_id_then_length():
    assert fragment_key({"ReplicationKey": 13, "ReplicationID": 2}, 7) == 13
    assert fragment_key({"ReplicationID": 2}, 7) == 2
    assert fragment_key({}, 7) == 7


# ------------------------------------------------------------------------------------------------ real rows

def test_the_real_damage_origin_row_is_a_world_point(tmp_path):
    rows = rows_of("damage_origin.ndjson")
    # The export has no Deadlock: the first spawn row gets a mesh-node archetype, the DamageOrigin shape is real.
    rows[0]["archetype_path"] = f"Default__Pawn_Cable_{MESH_NODE}_C"
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    raw = read_raw(path)
    [actor] = raw.actors.values()
    assert actor.name == MESH_NODE
    assert raw.origins == {3564: (1034.54, 2648.61, 276.3)}


def test_the_real_fragment_deltas_make_every_hull(tmp_path):
    path = ROWS / "fragment_delta.ndjson"
    raw = read_raw(path)
    keys = set()
    for row in rows_of("fragment_delta.ndjson"):
        delta = (row.get("payload") or {}).get("FragmentDelta")
        if delta:
            keys |= {u["ReplicationKey"] for u in delta["Updates"] if u.get("ConvexHullPoints")}
    # rpc deltas of 5 and 4 updates, then the export-group row's 21
    assert len(keys) == 30
    assert len(raw.footprints[4850]) == 30


def test_a_delete_only_delta_still_drops_a_hull(tmp_path):
    rows = rows_of("fragment_delta.ndjson")
    gone = {"type": "rpc_received", "time_ms": 61_700, "actor_net_guid": 4850, "function_name": "FragmentInfo",
            "payload": {"FragmentDelta": {"ArrayKey": 31, "BaseKey": 30, "DeletedIds": [3], "Updates": []}}}
    rows.insert(3, gone)
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert len(read_raw(path).footprints[4850]) == 29


def test_a_schema_8_delta_still_keys_on_replication_id(tmp_path):
    hull = [{"Point": {"x": 0, "y": 0}}, {"Point": {"x": 1, "y": 0}}, {"Point": {"x": 0, "y": 1}}]
    rows = [{"type": "actor_spawned", "time_ms": 100, "actor_net_guid": 9,
             "archetype_path": "Default__Patch_Phoenix_MolotovFire_C", "location": {"x": 0, "y": 0, "z": 0}},
            {"type": "export_group_received", "time_ms": 200, "actor_net_guid": 9, "is_actor": False,
             "payload": {"FragmentDelta": {"DeletedIds": [], "Updates": [
                 {"ReplicationID": 5, "ConvexHullPoints": hull}, {"ReplicationID": 6, "ConvexHullPoints": hull}]}}}]
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert sorted(read_raw(path).footprints[9]) == [5, 6]


def test_the_real_actors_context_row_lists_the_second_anchor():
    path = ROWS / "actors_context.ndjson"
    raw = read_raw(path, pawns=frozenset({998, 692}))
    assert len(raw.contexts) == 5
    first = raw.contexts[0]
    assert first.t_ms == 20_467 and first.equip.guid == 1014 and first.listed == frozenset({3332})
    assert [(c.t_ms, c.equip.guid, sorted(c.listed)) for c in raw.contexts[1:]] == [
        (387_293, 1014, [14456]), (390_864, 708, [14566]), (394_016, 708, [14622]), (400_160, 1014, [14898])]


def test_real_placements_give_the_trips_their_equippables_owner_and_an_end(tmp_path):
    path = tmp_path / "events.ndjson"
    path.write_text((ROWS / "actors_context.ndjson").read_text(encoding="utf-8"), encoding="utf-8")
    extras = build_extras(path, two_cyphers(), WINDOWS, GAME_MAP, AGENTS, lambda t_ms: {})
    wires = [a for r in extras.rounds.values() for a in r["abilities"] if a["name"] == "4_TripWire"]
    # The trimmed export has no close rows, so round 1's trip (placed at 20.5 s) is still alive in round 2 and is
    # in both rounds (W3): five placements, six rows, one id twice.
    assert len(wires) == 6 and len({a["id"] for a in wires}) == 5
    wires = list({a["id"]: a for a in wires}.values())
    assert {a["owner_by"] for a in wires} == {"equippable"}
    assert all(a["end"] is not None for a in wires)
    assert extras.report["wires_paired"] == 5
    # character 998 -> slot 1 (placements at 20.5 s, 387.3 s, 400.2 s), 692 -> slot 6 (390.9 s, 394.0 s)
    assert sorted(a["slot"] for a in wires) == [1, 1, 1, 6, 6]
    [first] = [a for a in extras.rounds[1]["abilities"] if a["name"] == "4_TripWire"]
    # second anchor 3332 at world (-1801.9, 2990.1): u = y * 1e-4 + 0.5 as 10_000 units, v = x * ... + 0.5
    assert first["slot"] == 1 and first["end"] == [round((2990.1 * 1e-4 + 0.5) * 10_000), round((-1801.9 * 1e-4 + 0.5) * 10_000)]


# ------------------------------------------------------------------------------------------------ LifeChangeEvents

def _real_life_changes():
    return [row["payload"] for row in (r.data for r in read_ndjson(ROWS / "life_change_events.ndjson"))]


def test_the_real_life_change_lists_decode():
    lethal_a, lethal_b, hit = _real_life_changes()
    assert pse.decode_life_changes(lethal_a["LifeChangeEvents"]) == [
        {"g": 3634, "res": 0.0, "d": 0.0, "alive": 1}, {"g": 3632, "res": 0.0, "d": -1.0, "alive": 0}]
    assert pse.decode_life_changes(lethal_b["LifeChangeEvents"]) == [
        {"g": 4208, "res": 0.0, "d": 0.0, "alive": 1}, {"g": 4206, "res": 0.0, "d": -1.0, "alive": 0}]
    assert pse.decode_life_changes(hit["LifeChangeEvents"]) == [
        {"g": 868, "res": 0.0, "d": 0.0, "alive": 1}, {"g": 866, "res": 74.0, "d": -26.0, "alive": 1}]


def test_a_malformed_life_change_list_is_undecodable():
    assert pse.decode_life_changes([{"ChangedComponent": 1}]) is None
    assert pse.decode_life_changes([{"ChangedComponent": 1, "LifeResult": "x", "DeltaLife": 0, "AliveAfterChange": True}]) is None
    assert pse.decode_life_changes("nope") is None


def test_real_life_changes_sum_to_the_damage_and_resolve_the_hp_section():
    class Export:
        events = read_ndjson(ROWS / "life_change_events.ndjson")

    table = PlayerTable(subjects=[None] * 10, agents=["Cypher"] * 10, pawn_slot={3564: 0, 4140: 1, 792: 2},
                        other_slot={}, pawn_changes={}, gone_ms={})
    state = pse.read_player_state(Export, table)
    assert len(state.hits) == 3 and all(h.sections is not None for h in state.hits)
    assert "vitals: undecodable LifeChangeEvents" not in state.diagnostics
    assert "vitals: sections don't sum to DamageTaken" not in state.diagnostics
    assert state.hp_section == {3564: 3632, 4140: 4206}


# the schema 8 blob (replay 7a278f4b's export, as test_player_state_extract.py pins it) still decodes as before
def test_a_schema_8_blob_still_decodes():
    from tests.replays.test_player_state_extract import REAL_LETHAL
    got = pse.decode_life_changes(REAL_LETHAL)
    assert got and all(set(s) == {"g", "res", "d", "alive"} for s in got)
