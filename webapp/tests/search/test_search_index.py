"""The pure index builder: toy two-round input, no database."""

from __future__ import annotations

import pytest

from app.search import index
from app.search.index import build_rows, buy_of, source_key
from app.search.match import ROW_KEYS


def _players():
    out = {}
    for s in range(10):
        out[s] = {"player_id": 100 + s, "agent": f"Agent{s}", "team": "team-1" if s < 5 else "team-2"}
    return out


def _inputs(**r1):
    round1 = {
        "outcome": "elimination", "attack_team": "team-1", "won_team": "team-1", "site": "A",
        "plant_t": 30.0, "defuse_t": None,
        "loadouts": {"team-1": [800] * 5, "team-2": [800] * 5},
        "states": [{"t": 0.0, "alive": [5, 5]}, {"t": 20.0, "alive": [4, 5]}, {"t": 40.0, "alive": [4, 4]}],
        "kills": [{"t": 20.0, "killer_slot": 5, "victim_slot": 0, "weapon": "Vandal"},
                  {"t": 40.0, "killer_slot": 1, "victim_slot": 6, "weapon": "Sheriff"}],
        "fights": [{"slot": 1, "t0": 38.0, "t1": 41.0, "tags": ["won"], "first": "player"}],
        "casts": [{"by": 2, "agent": "Agent2", "t": 10.0, "t_end": 12.0, "names": ["Q_Molotov"],
                   "kinds": ["damage"], "type": "ability", "ability_id": "Agent2/Molotov"},
                  {"by": 7, "agent": "Agent7", "t": 15.0, "t_end": None, "names": ["Ultimate_X"],
                   "kinds": ["ult"], "type": "ult", "ability_id": "Agent7/Ult"}],
        "backshots": [{"t": 20.0, "victim_slot": 0, "cause": "flank"}],
    }
    round1.update(r1)
    round2 = {
        "outcome": "elimination", "attack_team": "team-2", "won_team": "team-2", "site": None,
        "plant_t": None, "defuse_t": None,
        "loadouts": {"team-1": [4000] * 5, "team-2": [900] * 5},
        "states": None, "kills": [{"t": 5.0, "killer_slot": 6, "victim_slot": 1, "weapon": "Phantom"}],
        "fights": None, "casts": None, "backshots": [],
    }
    return {"replay": {"id": 7, "uuid": "u-7", "map": "Haven", "played_at": "2026-01-02T03:04:05", "match_id": 9},
            "players": _players(), "rounds": {1: round1, 2: round2}}


def _rows(**r1):
    return build_rows(_inputs(**r1))


def _of(rows, rnd, kind):
    return [r for r in rows if r["round"] == rnd and r["kind"] == kind]


def test_state_rows_split_at_plant():
    st = _of(_rows(), 1, "state")
    assert [r["t"] for r in st] == [0.0, 20.0, 30.0, 40.0]
    assert [r["t_end"] for r in st] == [20.0, 30.0, 40.0, None]
    by_t = {r["t"]: r for r in st}
    assert by_t[30.0]["post_plant"] is True
    assert by_t[20.0]["post_plant"] is False
    assert by_t[30.0]["alive1"] == 4 and by_t[30.0]["alive2"] == 5


def test_kill_and_death_rows():
    rows = _rows()
    kills, deaths = _of(rows, 1, "kill"), _of(rows, 1, "death")
    assert len(kills) == len(deaths) == 2
    k = kills[0]
    assert (k["alive1"], k["alive2"]) == (5, 5)
    assert k["first"] is True and kills[1]["first"] is False
    assert deaths[0]["first"] is True and deaths[1]["first"] is False
    assert k["slot"] == 5 and k["other_slot"] == 0 and k["other_team"] == "team-1" and k["weapon"] == "Vandal"
    d = deaths[0]
    assert d["slot"] == 0 and d["other_slot"] == 5 and d["team"] == "team-1" and d["other_team"] == "team-2"
    assert d["player_id"] == 100 and d["other_agent"] == "Agent5"
    assert (kills[1]["alive1"], kills[1]["alive2"]) == (4, 5)


def test_alive_slots_exclude_victims():
    st = {r["t"]: r for r in _of(_rows(), 1, "state")}
    assert 0 not in st[40.0]["alive_slots"] and 6 not in st[40.0]["alive_slots"]
    assert len(st[40.0]["alive_slots"]) == 8
    assert len(st[0.0]["alive_slots"]) == 10
    assert 0 not in st[20.0]["alive_slots"]


def test_casts_ult_and_none():
    rows = _rows()
    casts = _of(rows, 1, "cast")
    assert len(casts) == 2
    ult = next(c for c in casts if c["slot"] == 7)
    assert ult["ult"] is True and ult["tags"] == ["ult"]
    mol = next(c for c in casts if c["slot"] == 2)
    assert mol["ult"] is False and mol["ability"] == "Agent2/Molotov" and mol["ability_key"] == "Q"
    assert mol["t_end"] == 12.0 and mol["team"] == "team-1" and mol["player_id"] == 102
    assert _of(rows, 2, "cast") == []


def test_plant_fight_backshot_rows():
    rows = _rows(defuse_t=60.0)
    p = _of(rows, 1, "plant")[0]
    assert p["t"] == 30.0 and p["slot"] is None and p["player_id"] is None and p["agent"] is None
    assert p["team"] is None and p["post_plant"] is True
    assert _of(rows, 1, "defuse")[0]["t"] == 60.0
    assert _of(rows, 2, "plant") == []
    f = _of(rows, 1, "fight")[0]
    assert (f["t"], f["t_end"], f["slot"], f["first"], f["tags"]) == (38.0, 41.0, 1, True, ["won"])
    b = _of(rows, 1, "backshot")[0]
    assert b["slot"] == 0 and b["tags"] == ["flank"]


def test_no_states_gives_no_state_rows_and_no_alive():
    rows = build_rows(_inputs(states=None))
    assert _of(rows, 1, "state") == []
    for r in _of(rows, 1, "kill") + _of(rows, 1, "cast"):
        assert r["alive1"] is None and r["alive2"] is None


def test_round_fields_scores_and_buys():
    rows = _rows()
    r2 = _of(rows, 2, "kill")[0]
    assert (r2["score1"], r2["score2"]) == (1, 0)
    assert (r2["attack_team"], r2["won_team"], r2["planted"], r2["site"]) == ("team-2", "team-2", False, None)
    assert (r2["buy1"], r2["buy2"]) == ("force", "save")
    r1 = _of(rows, 1, "kill")[0]
    assert (r1["buy1"], r1["buy2"]) == ("pistol", "pistol")
    assert (r1["score1"], r1["score2"], r1["planted"], r1["plant_t"]) == (0, 0, True, 30.0)
    assert r1["replay_id"] == 7 and r1["uuid"] == "u-7" and r1["map"] == "Haven" and r1["match_id"] == 9
    assert r1["played_at"] == "2026-01-02T03:04:05"


def test_buy_of():
    assert buy_of(1, [4000] * 5) == "pistol"
    assert buy_of(13, []) == "pistol"
    assert buy_of(5, [4000] * 5) == "force"
    assert buy_of(5, [900] * 5) == "save"
    assert buy_of(5, [2000] * 5) == "eco"
    assert buy_of(5, [5000] * 5) == "full"
    assert buy_of(5, []) is None
    assert buy_of(5, None) is None


def test_row_keys_and_seq():
    rows = _rows()
    assert rows
    for r in rows:
        assert tuple(sorted(r)) == tuple(sorted(ROW_KEYS))
    for n in (1, 2):
        assert [r["seq"] for r in rows if r["round"] == n] == list(range(len([r for r in rows if r["round"] == n])))


def test_fractional_times():
    rows = build_rows(_inputs(
        states=[{"t": 0.0, "alive": [5, 5]}, {"t": 20.123, "alive": [4, 5]}],
        kills=[{"t": 20.123, "killer_slot": 5, "victim_slot": 0, "weapon": "Vandal"}],
        plant_t=None, fights=None, casts=None, backshots=[]))
    k = _of(rows, 1, "kill")[0]
    d = _of(rows, 1, "death")[0]
    assert (k["alive1"], k["alive2"]) == (5, 5) and (d["alive1"], d["alive2"]) == (5, 5)
    st = next(r for r in _of(rows, 1, "state") if r["t"] == 20.123)
    assert 0 not in st["alive_slots"]


def test_source_key(monkeypatch):
    parts = {"a": 1, "b": ["x"]}
    k = source_key(parts)
    assert k == source_key({"b": ["x"], "a": 1}) and len(k) == 64
    assert k != source_key({"a": 2, "b": ["x"]})
    monkeypatch.setattr(index, "INDEX_REVISION", index.INDEX_REVISION + 1)
    assert source_key(parts) != k
