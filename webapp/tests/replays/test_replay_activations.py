"""KAY/O's ZERO/point in the stored round (the 2026-10-05 review, item 10): whether the landed knife pulsed,
its radius and who it suppressed, from cuts of a real export (tests/fixtures/replays/utility). A knife that hit
nobody must be told apart from one nothing is known about."""

import json
from pathlib import Path

import pytest

from app.replays import format as fmt
from app.replays.condense import MapInfo, PlayerTable
from app.replays.extras import build_extras

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "replays" / "utility"
GAME_MAP = MapInfo("Testmap", "Test", 1e-5, 1e-5, 0.5, 0.5)
AGENTS = {"Grenadier": "KAY/O", "Sequoia": "Iso", "Nox": "Vyse", "Thorne": "Sage", "Smonk": "Clove", "Wushu": "Jett"}
# The pawns of the export the fixtures were cut from; slot 0 is the KAY/O.
PAWNS = {1452: 5, 696: 6, 796: 7, 1552: 8, 1220: 9}


def players() -> PlayerTable:
    agents = ["KAY/O", "Killjoy", "Miks", "Iso", "Reyna", "Iso", "Vyse", "Sage", "Clove", "Jett"]
    return PlayerTable(subjects=[None] * 10, agents=agents, pawn_slot=dict(PAWNS), other_slot={}, pawn_changes={},
                       gone_ms={})


def knife(name: str, start_ms: int, rows=None, tmp_path=None) -> dict:
    path = FIXTURES / f"{name}.ndjson"
    if rows is not None:
        path = tmp_path / "events.ndjson"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    extras = build_extras(path, players(), [(start_ms, start_ms + 60_000, start_ms + 60_000)], GAME_MAP, AGENTS,
                          lambda t_ms: {})
    return next(r for r in extras.rounds[1]["abilities"] if r["name"] == "E_SuppressionPulse")


def test_a_knife_that_suppressed_five_names_them_and_its_radius():
    row = knife("kayo_knife_hits", 40_000)
    assert row["slot"] == 0 and row["agent"] == "KAY/O"
    assert row["pulse"]["t"] - row["t0"] == pytest.approx(1.0, abs=0.001)          # it pulses a second after it lands
    assert row["pulse"]["hits"] == [5, 6, 7, 8, 9]
    assert row["pulse"]["r"] == round(1500 * 1e-5 * fmt.UV_SCALE)                  # 15 m, as the replay says
    assert row["activation"] == {"state": "completed", "evidence": "pulse_oneshot", "targets_complete": True,
                                 "diagnostics": []}
    assert row["fx"] == [row["pulse"]["t"]]                                        # the viewer's pop, as before


def test_a_knife_that_hit_nobody_is_a_proven_empty():
    row = knife("kayo_knife_empty", 230_000)
    assert row["pulse"]["hits"] == [] and row["activation"]["state"] == "completed"
    assert row["activation"]["targets_complete"] is True


def test_a_knife_with_no_pulse_row_is_unknown_not_empty(tmp_path):
    rows = [json.loads(line) for line in (FIXTURES / "kayo_knife_empty.ndjson").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if not (r["type"] == "rpc_received" and r["function_name"] == "MulticastPlayOneShotEffect")]
    row = knife("x", 230_000, rows, tmp_path)
    assert "pulse" not in row
    assert row["activation"] == {"state": "unknown", "evidence": None, "targets_complete": False,
                                 "diagnostics": ["no_pulse_row"]}


def test_a_hit_long_after_the_pulse_or_on_its_owner_is_not_its_hit(tmp_path):
    rows = [json.loads(line) for line in (FIXTURES / "kayo_knife_hits.ndjson").read_text(encoding="utf-8").splitlines()]
    hits = [r for r in rows if r["type"] == "rpc_received" and r["actor_net_guid"] in PAWNS]
    assert len(hits) == 5
    hits[0]["time_ms"] += 5_000                      # far too late: something else named the knife
    hits[1]["actor_net_guid"] = 4242                 # nobody's pawn
    rows.sort(key=lambda r: r["time_ms"])
    row = knife("x", 40_000, rows, tmp_path)
    assert len(row["pulse"]["hits"]) == 3


def test_the_ult_pulses_are_rows_of_their_own_name_and_never_a_knife():
    extras = build_extras(FIXTURES / "kayo_ult.ndjson", players(), [(320_000, 380_000, 380_000)], GAME_MAP, AGENTS,
                          lambda t_ms: {})
    rows = extras.rounds[1]["abilities"]
    assert sorted(r["name"] for r in rows) == ["X_UltPulse"] * 5 + ["X_UltPulseManager"]
    assert not any("pulse" in r or "activation" in r for r in rows)     # NULL/cmd is not ZERO/point
    pulses = sorted(r["t0"] for r in rows if r["name"] == "X_UltPulse")
    assert [round(b - a, 1) for a, b in zip(pulses, pulses[1:])] == [3.0, 3.0, 3.0, 3.0]
