"""The stored info contract, using anonymous slots and synthetic probe payloads."""

import copy
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.control.mollies import MOLLY_KEYS
from app.critutil import abilities, extract
from tests.moments.helpers import round_of, toy_ability
from tests.replays.control_toys import open_hall


MEASURE = {"cleared_m2": 12.5, "located": 2, "hold_s": 3.0}
ZERO = {"cleared_m2": 0.0, "located": 0, "hold_s": 0.0}
LEGACY = {"up_s": 8.0, "front_s": 4.0, "held_m2s": 16.0, "crossed_s": 1.0}


def recon(t=5.0, actor=101):
    return toy_ability("Hunter", "Q_RevealBolt", 200, 200, 0, t=t, t1=t + 5,
                       kind="Projectile", id=actor)


def run(util, holds, t_decided=25.0):
    players = {0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}
    rnd, blob = round_of(players, util=util, geo=open_hall(), t_decided=t_decided)
    return extract.round_util(rnd, blob, holds=holds)


def entry(*, cast_id=None, row_key=None, item=None, actor=None, kind="info", t0=5.0, measure=None):
    # The complete transport shape, including nullable identity and legacy-item fields.
    return {"cast_id": cast_id, "row_key": row_key, "item": item, "id": actor, "by": 0,
            "kind": kind, "t0": t0, "uv": None, **(MEASURE if measure is None else measure)}


def payload(*entries, items=(), error=None):
    return {"error": error, "items": list(items), "info": list(entries)}


def test_figure_reads_stored_info_and_old_casts_are_unknown():
    from app.critutil import info

    assert info.figure({"info": MEASURE}) == MEASURE
    assert info.figure({"info": ZERO}) == ZERO
    assert info.figure({"info": None}) is None
    assert info.figure({}) is None
    assert info.empty() == ZERO
    first = info.empty()
    first["located"] = 9
    assert info.empty() == ZERO


def test_ability_ids_exist_and_molly_hold_abilities_match_the_allow_list():
    from app.critutil import info

    ids = {f"{e['agent']}/{e['ability']}" for e in abilities.load()}
    assert info.CLEAR_ABILITIES <= ids
    assert info.HOLD_ABILITIES <= ids
    assert len(info.CLEAR_ABILITIES) == 21
    non_mollies = {"Cypher/Trapwire", "Cypher/Spycam", "Killjoy/Alarmbot", "Killjoy/Turret",
                   "Chamber/Trademark", "Sova/Owl Drone", "Skye/Trailblazer", "Gekko/Thrash", "Tejo/Stealth Drone"}
    molly_ids = set()
    for key in MOLLY_KEYS:
        matches = [e for e in abilities.load()
                   if abilities.lookup(key, entries=[e]) or any(re.search(p, key) for p in e.get("pieces", []))]
        assert len(matches) == 1, key
        molly_ids.add(f"{matches[0]['agent']}/{matches[0]['ability']}")
    assert info.HOLD_ABILITIES == non_mollies | molly_ids
    assert "Cypher/Spycam dart" not in info.CLEAR_ABILITIES | info.HOLD_ABILITIES


def test_info_cast_id_reaches_a_recon_cast():
    (cast,) = run([recon()], payload(entry(cast_id=101)))["casts"]
    assert cast["info"] == MEASURE


@pytest.mark.parametrize("holds, expected", [
    (payload(), ZERO),
    (payload(entry(cast_id=101), error="probe failed"), None),
    ({"error": None, "items": []}, None),
    (None, None),
])
def test_zero_requires_a_successful_current_probe(holds, expected):
    (cast,) = run([recon()], holds)["casts"]
    assert cast["info"] == expected


def test_unlisted_and_after_decided_casts_have_no_info():
    util = [recon(t=26), toy_ability("Wraith", "4_Smoke", 200, 200, 0, t=5, t1=15)]
    out = run(util, payload(entry(cast_id=101)))
    assert all(c["info"] is None for c in out["casts"])


@pytest.mark.parametrize("code, name", [
    ("Gumshoe", "4_TripWire"), ("Killjoy", "Q_StealthAlarmbot"), ("Sarge", "Q_Molotov_Production"),
])
def test_hold_abilities_without_a_measure_still_count_as_zero(code, name):
    row = toy_ability(code, name, 200, 200, 0, t=5, t1=15, kind="GameObject")
    (cast,) = run([row], payload())["casts"]
    assert cast["info"] == ZERO


def test_multiple_probe_entries_for_one_cast_keep_all_three_measures():
    (cast,) = run([recon()], payload(entry(cast_id=101), entry(cast_id=101)))["casts"]
    assert cast["info"] == {k: 2 * v for k, v in MEASURE.items()}
    assert MEASURE == {"cleared_m2": 12.5, "located": 2, "hold_s": 3.0}


def test_reveal_row_key_uses_the_same_join_as_effects_after_json_transport():
    reveal = {"k": "reveal", "t": 7.0, "t1": 8.0, "by": 0, "target": 5,
              "code": "Hunter", "name": "Q_SonarPing"}
    key = [reveal[k] for k in ("by", "code", "name", "t")]
    (cast,) = run([recon(), reveal], payload(entry(row_key=key)))["casts"]
    assert cast["info"] == MEASURE
    assert cast["effects"] == [{"target": 5, "how": "reveal", "t0": 7.0, "t1": 8.0, "known": True}]


def test_fury_blast_without_an_actor_joins_the_ult_cast():
    blast = {"k": "blast", "t": 7.0, "by": 0, "code": "Hunter", "name": "X_LaserMulti",
             "u": 2000, "v": 2000, "yaw": 0, "hits": []}
    key = [blast[k] for k in ("by", "code", "name", "t")]
    out = run([blast], payload(entry(row_key=key)))
    fury = next(c for c in out["casts"] if c["ability_id"] == "Sova/Hunter's Fury")
    assert fury["info"] == MEASURE


@pytest.mark.parametrize("uses", [
    [(0, 7.0), (0, 9.0), (0, 12.0)],
    [(0, 7.0), (5, 7.0)],
    [(0, 7.0), (0, 12.0), (0, 13.01)],
])
def test_fury_blasts_group_by_owner_and_the_original_use_window(uses):
    blasts = [{"k": "blast", "t": t, "by": by, "code": "Hunter", "name": "X_LaserMulti",
               "u": 2000, "v": 2000, "yaw": 0, "hits": []} for by, t in uses]
    measures = [entry(row_key=[r[k] for k in ("by", "code", "name", "t")]) for r in blasts]
    casts = run(blasts, payload(*measures))["casts"]
    expected = {
        ((0, 7.0), (0, 9.0), (0, 12.0)): [(0, 7.0, 13.0, 3)],
        ((0, 7.0), (5, 7.0)): [(0, 7.0, 13.0, 1), (5, 7.0, 13.0, 1)],
        ((0, 7.0), (0, 12.0), (0, 13.01)): [(0, 7.0, 13.0, 2), (0, 13.01, 19.01, 1)],
    }[tuple(uses)]
    assert [(c["by"], c["t"], c["t_end"]) for c in casts] == [e[:3] for e in expected]
    assert all(c["ability_id"] == "Sova/Hunter's Fury" for c in casts)
    assert [c["info"] for c in casts] == [{k: n * v for k, v in MEASURE.items()} for _, _, _, n in expected]


def test_no_blast_rows_keep_the_complete_pre_w8_casts():
    import hashlib
    import json

    util = [recon(), toy_ability("Hunter", "Q_SonarBolt", 200, 200, 0, t=5.5, t1=10,
                                kind="GameObject", id=102),
            toy_ability("Gumshoe", "4_TripWire", 250, 200, 5, t=0, t1=20, kind="GameObject", id=201),
            toy_ability("Wraith", "4_Smoke", 300, 200, 0, t=15, t1=20, id=301)]
    players = {0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 200, 180)])}
    rnd, blob = round_of(players, util=util, geo=open_hall(), t_decided=25.0)
    casts = extract.build_casts(rnd, blob)
    packed = [{field: getattr(c, field) for field in c.__slots__} for c in casts]
    text = json.dumps(packed, sort_keys=True, default=sorted, separators=(",", ":"))
    # Recorded from HEAD's build_casts before W8's blast loop; every slot, including the original rows.
    assert len(casts) == 3
    assert hashlib.sha256(text.encode()).hexdigest() == "62349334494a0d190c37736a3acd4508e9ce5efa7eed1c97263883da77667da8"


def test_info_cast_id_takes_precedence_over_another_rows_key():
    other = recon(t=16, actor=102)
    key = [other[k] for k in ("by", "code", "name", "t")]
    casts = run([recon(), other], payload(entry(cast_id=101, row_key=key)))["casts"]
    assert [c["info"] for c in casts] == [MEASURE, ZERO]


def test_unmatched_info_is_not_guessed_from_owner_or_source():
    measure = entry(cast_id=999, row_key=[0, "Hunter", "Q_SonarPing", 99.0])
    measure["source"] = "Hunter_Q_RevealBolt@5.0"
    (cast,) = run([recon()], payload(measure))["casts"]
    assert cast["info"] == ZERO


@pytest.mark.parametrize("code, name, ability", [
    ("Hunter", "E_Drone", "Sova/Owl Drone"),
    ("Gumshoe", "E_PossessableCamera", "Cypher/Spycam"),
    ("Killjoy", "E_Turret", "Killjoy/Turret"),
])
def test_watchers_match_by_actor_and_carry_both_measures(code, name, ability):
    row = toy_ability(code, name, 200, 200, 0, t=5, t1=15, kind="GameObject", id=301)
    (cast,) = run([row], payload(entry(actor=301, kind="camera")))["casts"]
    assert cast["ability_id"] == ability
    assert cast["info"] == MEASURE


def test_burning_mollies_reuse_legacy_item_matches_and_original_item_indices():
    util = [toy_ability("Sarge", "Q_Molotov_Production", 200, 200, 0, t=5, t1=10),
            toy_ability("Sarge", "Q_Molotov_Production", 300, 200, 0, t=15, t1=20)]
    items = [{"what": "molly", "kind": "molly", "by": 0, "id": None, "uv": [r["u"], r["v"]],
              "t0": r["t"], "t1": r["t1"], **LEGACY} for r in reversed(util)]
    holds = payload(entry(item=0, kind="molly", t0=15),
                    entry(item=1, kind="molly", measure={**ZERO, "hold_s": 7.0}), items=items)
    before = copy.deepcopy(holds)
    out = run(util, holds)
    assert [c["info"]["hold_s"] for c in out["casts"]] == [7.0, 3.0]
    assert all(c["hold"] == LEGACY for c in out["casts"])
    old = run(util, {"error": None, "items": items})
    for result in (out, old):
        for cast in result["casts"]:
            cast.pop("info", None)
    assert out == old
    assert holds == before


def test_drone_darts_share_the_drone_cast_and_camera_darts_credit_the_camera():
    drone = toy_ability("Hunter", "E_Drone", 200, 200, 0, t=5, t1=15, kind="Pawn", id=301)
    dart = toy_ability("Hunter", "E_Drone_RevealDart", 300, 200, 0, t=7, t1=8, kind="Projectile", id=302)
    (cast,) = run([drone, dart], payload(entry(cast_id=302)))["casts"]
    assert cast["info"] == MEASURE
    camera = toy_ability("Gumshoe", "E_PossessableCamera", 200, 200, 0, t=5, t1=15, kind="GameObject", id=401)
    dart = toy_ability("Gumshoe", "E_CameraTrackingDart", 300, 200, 0, t=7, t1=8, kind="Projectile", id=402)
    casts = run([camera, dart], payload(entry(cast_id=402)))["casts"]
    by_ability = {c["ability_id"]: c for c in casts}
    assert by_ability["Cypher/Spycam"]["info"] == MEASURE
    assert by_ability["Cypher/Spycam dart"]["info"] is None


@pytest.mark.parametrize("stored", [MEASURE, ZERO, None, "missing"])
def test_match_payload_preserves_stored_info(monkeypatch, stored):
    from app.services import replay_moments, replay_util, replays

    cast = {"i": 0}
    if stored != "missing":
        cast["info"] = stored
    row = SimpleNamespace(round_number=1, gaps_revision=1, fingerprint="toy", status="ok", data={"casts": [cast]})
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [row]
    replay = SimpleNamespace(id="toy-replay", round_count=1)
    monkeypatch.setattr(replays, "page_context", lambda *a: {"players": {}})
    monkeypatch.setattr(replay_moments, "tab_players", lambda *a: {})
    monkeypatch.setattr(replay_util, "_replay_context", lambda *a: {replay.id: {"match_id": "toy-match"}})
    monkeypatch.setattr(replay_util.util_model, "load", lambda *a: None)
    monkeypatch.setattr(replay_util.util_model, "covers", lambda *a: True)
    monkeypatch.setattr(replay_util, "Freshness", lambda *a: SimpleNamespace(current=lambda *a: True))
    monkeypatch.setattr(replay_util, "_values_row", lambda *a: ("toy-key", {"fingerprints": {replay.id: {"1": "toy"}}}))
    monkeypatch.setattr(replay_util, "corpus_key", lambda *a: "toy-key")
    monkeypatch.setattr(replay_util, "current_rows", lambda *a, **kw: [])
    monkeypatch.setattr(replay_util.areas, "load_table", lambda **kw: {})
    monkeypatch.setattr(replay_util, "_cast_rows", lambda *a, **kw: [
        {"replay": replay.id, "round": 1, "i": 0, "slot": 0, "t": 5.0, "scored": True,
         "value": 0.0, "ledger": 0.0, "jobs": 0.0, "features": {}}])
    out = replay_util.match_payload(db, replay, read_only=True)
    assert out["rounds"]["1"]["casts"][0]["info"] == (None if stored == "missing" else stored)
