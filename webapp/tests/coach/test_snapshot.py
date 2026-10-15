import pytest

from app.coach import snapshot
from tests.coach import toys

CONTEXT = {"match": {"uuid": "u-1", "map": "Toy", "rounds": [1, 2], "linked": True, "uv_per_unit": toys.UV_PER_UNIT,
                     "score": [1, 1], "external_id": "u-1"},
           "players": {str(s): {"slot": s, "name": f"P{s}#T{s}", "agent": "Jett", "team": "team1" if s < 5 else "team2",
                                "side": "A" if s < 5 else "B", "player_id": 100 + s} for s in range(10)},
           "rounds": {"1": {"db": {"winner": "team1"}, "stats": {}, "kills": {}},
                      "2": {"db": {"winner": "team2"}, "stats": {}, "kills": {}}}}
SHARES = {"t": [0.0], "cells": {"A Site": 1}, "areas": {"A Site": [[1, 0, 0]]},
          "knew": {"A": {}, "B": {}}, "group_side": None}


def built(controls=None, blobs=None):
    blobs = blobs or {1: toys.round_blob(toys.ten(), n=1), 2: toys.round_blob(toys.ten(), n=2)}
    controls = controls or {1: ("ok", False, SHARES), 2: ("ok", False, SHARES)}
    return snapshot.assemble(CONTEXT, blobs, controls)


def test_a_complete_match_has_no_gaps(tmp_path):
    snapshot.save(built(), tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.round_numbers == [1, 2] and snap.map == "Toy"
    assert snap.gaps(1) == [] and snap.control(1)["cells"] == {"A Site": 1}
    assert snap.view(2).number == 2 and snap.view(2).position(0, 1.0)["alive"]


def test_each_missing_source_is_named(tmp_path):
    no_knew = {**SHARES, "knew": None}
    blob = toys.round_blob(toys.ten(), n=2)
    blob.pop("player_state", None)
    data = built(controls={1: ("not_ready", False, None), 2: ("ok", True, no_knew)},
                 blobs={1: toys.round_blob(toys.ten(), n=1), 2: blob})
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.gaps(1) == ["control_not_ready", "team_knowledge"]
    assert snap.gaps(2) == ["control_stale", "team_knowledge", "player_state"]
    assert snap.control(1) is None


def test_the_link_gives_roles_site_only_deaths_and_sides_the_blob_lacks(tmp_path):
    blob = toys.round_blob(toys.ten(), n=1)
    for p in blob["players"]:
        p["side"] = None                                   # the condenser could not resolve sides
    links = {1: {"linked": True, "sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)},
                 "db_deaths": [[3, 25.0]]}}
    data = snapshot.assemble(CONTEXT, {1: blob, 2: toys.round_blob(toys.ten(), n=2)},
                             {1: ("ok", False, SHARES), 2: ("ok", False, SHARES)}, links)
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.role(1, 0) == "attack" and snap.role(1, 7) == "defense" and snap.role(2, 0) is None
    assert snap.view(1).enemies(0) == [5, 6, 7, 8, 9]      # groups came from the page context
    assert not snap.view(1).alive(3, 30.0) and snap.gaps(1) == ["db_only_deaths"] and snap.gaps(2) == []


def test_the_round_link_decides_a_side_the_blob_lacks_before_the_page_group(tmp_path):
    blob = toys.round_blob(toys.ten(), n=1)
    for p in blob["players"]:
        p["side"] = None
    # slot 0's page group is "A", but this round the link says it defends
    links = {1: {"linked": True, "sides": {str(s): "defense" if s < 5 else "attack" for s in range(10)},
                 "db_deaths": []}}
    data = snapshot.assemble(CONTEXT, {1: blob}, {1: ("ok", False, SHARES)}, links)
    snapshot.save(data, tmp_path)
    view = snapshot.load(tmp_path).view(1)
    assert CONTEXT["players"]["0"]["side"] == "A"
    assert view.side(0) == "B" and view.side(7) == "A"


def test_a_player_is_found_by_slot_name_or_name_without_tag(tmp_path):
    snapshot.save(built(), tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.slot_of("3") == 3 and snap.slot_of("p3#t3") == 3 and snap.slot_of("P3") == 3


def test_an_unknown_player_fails_with_the_valid_names(tmp_path):
    snapshot.save(built(), tmp_path)
    with pytest.raises(snapshot.SnapshotError, match="P0#T0"):
        snapshot.load(tmp_path).slot_of("nobody")


def test_read_refuses_an_unlinked_replay(monkeypatch):
    class Replay:
        map_name = "Toy"

    monkeypatch.setattr(snapshot.replays, "find_replay", lambda db, uuid: Replay())
    monkeypatch.setattr(snapshot.replays, "page_context",
                        lambda db, r: {"match": {"linked": False, "reason": "not linked"}, "players": {}, "rounds": {}})
    with pytest.raises(snapshot.SnapshotError, match="not linked"):
        snapshot.read(None, "u-1")
    monkeypatch.setattr(snapshot.replays, "find_replay", lambda db, uuid: None)
    with pytest.raises(snapshot.SnapshotError, match="no replay"):
        snapshot.read(None, "u-1")


def test_enemies_are_named_by_agent_and_teammates_keep_their_names(tmp_path):
    data = built()
    data["context"] = {**CONTEXT, "players": {s: dict(p) for s, p in CONTEXT["players"].items()}}
    data["context"]["players"]["5"]["agent"] = "Reyna"
    snapshot.hide_enemy_names(data, "P1#T1")
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.names[0] == "P0#T0" and snap.names[5] == "enemy Reyna"
    assert snap.names[6] == "enemy Jett" and snap.names[7] == "enemy Jett 2"
    assert snap.slot_of("enemy Reyna") == 5 and CONTEXT["players"]["5"]["name"] == "P5#T5"


def test_hiding_names_refuses_a_player_not_in_the_match():
    with pytest.raises(snapshot.SnapshotError):
        snapshot.hide_enemy_names(built(), "Nobody#NA1")
