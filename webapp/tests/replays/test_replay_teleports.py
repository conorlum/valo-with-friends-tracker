"""Teleports and temporary bodies in the stored round (the 2026-10-05 review, items 12, 18, 21): Omen's ult
marker and its outcome, Phoenix's return point, Waylay's return point and her recall. Real rows from
tests/fixtures/replays/utility; the body positions an outcome needs are given by each test."""

import json
from pathlib import Path

import pytest

from app.replays import extras as ex
from app.replays.condense import MapInfo, PlayerTable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
GAME_MAP = MapInfo("Testmap", "Test", 1e-5, 1e-5, 0.5, 0.5)
AGENTS = {"Wraith": "Omen", "Phoenix": "Phoenix", "Terra": "Waylay"}
DESTINATION = (2740.0, 3674.9)          # the Sunset cast's marker
ORIGIN = (-3000.0, -500.0)


def players(agents=None) -> PlayerTable:
    agents = agents or ["Omen", "Phoenix", "Waylay", "Jett", "Sage", "Cypher", "Reyna", "Killjoy", "Breach", "Viper"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot={1100: 0, 17588: 1, 692: 2}, other_slot={},
                       pawn_changes={}, gone_ms={})


def rows_of(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.ndjson").read_text(encoding="utf-8").splitlines() if line]


def build(tmp_path, rows, start_ms, positions_at=lambda t_ms: {}, table=None):
    path = tmp_path / "events.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["time_ms"])), encoding="utf-8")
    return ex.build_extras(path, table or players(), [(start_ms, start_ms + 90_000, start_ms + 90_000)], GAME_MAP, AGENTS,
                           positions_at)


def omen(tmp_path, after, before=ORIGIN, table=None):
    rows = rows_of("omen_ult")
    spawn = next(r for r in rows if r["type"] == "actor_spawned")
    closed = next(r for r in rows if r["type"] == "actor_closed")["time_ms"]

    def positions_at(t_ms):
        if t_ms <= spawn["time_ms"]:
            return {} if before is None else {0: before}
        return {} if after is None else ({0: after} if t_ms >= closed else {0: before})

    extras = build(tmp_path, rows, 1_000_000, positions_at, table)
    return next(r for r in extras.rounds[1]["abilities"] if r["name"] == "X_GlobalTeleport_Intention"), extras


def test_omens_marker_is_a_row_at_the_destination_for_the_channel(tmp_path):
    row, extras = omen(tmp_path, DESTINATION)
    assert (row["code"], row["agent"], row["slot"], row["owner_by"]) == ("Wraith", "Omen", 0, "agent")
    assert row["t1"] - row["t0"] == pytest.approx(3.503, abs=0.001)
    assert [row["u"], row["v"]] == list(GAME_MAP.to_uv(*DESTINATION)) and row["z"] == 21
    assert (row["outcome"], row["evidence"]) == ("completed", "marker_closed_body_at_destination")
    assert row["from"] == list(GAME_MAP.to_uv(*ORIGIN))
    assert extras.report["omen_ult_completed"] == 1


@pytest.mark.parametrize("after, before, outcome, evidence", [
    ((ORIGIN[0] + 100, ORIGIN[1]), ORIGIN, "cancelled", "marker_closed_body_at_origin"),
    ((9000.0, 9000.0), ORIGIN, "unknown", "body_elsewhere"),
    (None, ORIGIN, "unknown", "no_body_sample"),
    ((DESTINATION[0] + 50, DESTINATION[1]), (DESTINATION[0] - 100, DESTINATION[1]), "unknown", "destination_beside_origin"),
    ((DESTINATION[0] + 50, DESTINATION[1]), None, "completed", "marker_closed_body_at_destination"),
])
def test_an_outcome_needs_the_closed_marker_and_the_body_to_agree(tmp_path, after, before, outcome, evidence):
    row, _ = omen(tmp_path, after, before)
    assert (row["outcome"], row["evidence"]) == (outcome, evidence)


def test_two_omens_leave_the_marker_unowned_and_its_outcome_unknown(tmp_path):
    two = ["Omen", "Phoenix", "Waylay", "Omen", "Sage", "Cypher", "Reyna", "Killjoy", "Breach", "Viper"]
    row, _ = omen(tmp_path, DESTINATION, table=players(two))
    assert row["slot"] is None and (row["outcome"], row["evidence"]) == ("unknown", "no_owner")


def test_a_marker_that_never_closes_has_no_outcome(tmp_path):
    rows = [r for r in rows_of("omen_ult") if r["type"] != "actor_closed"]
    extras = build(tmp_path, rows, 1_000_000, lambda t_ms: {0: DESTINATION})
    [row] = extras.rounds[1]["abilities"]
    assert (row["outcome"], row["evidence"]) == ("unknown", "marker_never_closed")


def test_phoenixs_return_point_spans_the_ult(tmp_path):
    extras = build(tmp_path, rows_of("phoenix_ult"), 700_000)
    [row] = extras.rounds[1]["abilities"]
    assert f"{row['code']}_{row['name']}" == ex.PHOENIX_RETURN and row["slot"] == 1
    assert row["t1"] - row["t0"] == pytest.approx(10.664, abs=0.001)
    assert "z" in row and "outcome" not in row


def test_a_recall_is_tied_to_its_return_point(tmp_path):
    extras = build(tmp_path, rows_of("waylay_recall"), 60_000)
    by = {r["name"]: r for r in extras.rounds[1]["abilities"]}
    anchor, flight = by["E_RewindTime_RewindTarget"], by["E_RewindTime_ForObservers"]
    assert anchor["slot"] == 2 and flight["kind"] == "Projectile"
    assert anchor["recall"]["t"] == flight["t0"] == pytest.approx(18.815, abs=0.001)
    assert anchor["recall"]["t1"] == anchor["t1"] == flight["t1"]                # she arrives as both close
    assert [anchor["recall"]["u"], anchor["recall"]["v"]] == [flight["u"], flight["v"]]   # where she recalled from
    assert [anchor["u"], anchor["v"]] != [flight["u"], flight["v"]]
    assert extras.report["waylay_recalls"] == 1 and extras.report["waylay_anchors"] == 1


def test_a_return_point_that_runs_out_has_no_recall(tmp_path):
    rows = [r for r in rows_of("waylay_recall") if "ForObservers" not in json.dumps(r)]
    flight = next(r["actor_net_guid"] for r in rows_of("waylay_recall")
                  if r["type"] == "actor_spawned" and "ForObservers" in r["archetype_path"])
    rows = [r for r in rows if r.get("actor_net_guid") != flight]
    extras = build(tmp_path, rows, 60_000)
    [anchor] = extras.rounds[1]["abilities"]
    assert "recall" not in anchor and extras.report.get("waylay_recalls", 0) == 0


def test_a_recall_that_does_not_end_with_the_anchor_is_not_its_recall(tmp_path):
    rows = rows_of("waylay_recall")
    flight = next(r["actor_net_guid"] for r in rows if r["type"] == "actor_spawned" and "ForObservers" in r["archetype_path"])
    for r in rows:
        if r["type"] == "actor_closed" and r["actor_net_guid"] == flight:
            r["time_ms"] -= 1_000
    extras = build(tmp_path, rows, 60_000)
    anchor = next(r for r in extras.rounds[1]["abilities"] if r["name"] == "E_RewindTime_RewindTarget")
    assert "recall" not in anchor


def test_nothing_is_made_up_for_yoru_or_veto(tmp_path):
    """Their signals aren't in any local export: no row, and no alias that would let a lookalike in."""
    names = ["Default__Ability_Stealth_E_Teleport_C", "Default__Ability_Stealth_X_Cloak_Equip_C",
             "Default__Intention_Stealth_E_Teleport_C", "Default__Intention_Wraith_X_GlobalTeleportDecor_C"]
    rows = [{"type": "actor_spawned", "time_ms": 1_000_100 + i, "actor_net_guid": 60 + i, "archetype_path": name,
             "location": {"x": 0, "y": 0, "z": 0}, "rotation": None} for i, name in enumerate(names)]
    assert build(tmp_path, rows, 1_000_000).rounds == {}
