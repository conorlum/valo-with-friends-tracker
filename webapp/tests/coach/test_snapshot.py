import json

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


def test_a_snapshot_is_saved_one_file_per_round_and_reads_only_the_round_asked_for(tmp_path):
    snapshot.save(built(), tmp_path)
    head = json.loads((tmp_path / snapshot.FILE).read_text(encoding="utf-8"))
    rounds_dir = tmp_path / head["rounds_dir"]
    assert sorted(p.name for p in rounds_dir.iterdir()) == ["1.json", "2.json"]
    assert "rounds" not in head
    (rounds_dir / "2.json").unlink()      # round 1's questions never open round 2's file
    snap = snapshot.load(tmp_path)
    assert snap.round_numbers == [1, 2] and snap.gaps(1) == [] and snap.view(1).number == 1


def test_util_percentiles_are_saved_once_outside_round_files(tmp_path):
    table = {"Sova/Owl Drone": {"cleared_m2": {"n": 11, "points": [float(p) for p in range(101)]}}}
    data = built()
    data["context"] = {**data["context"], "util_info": table}
    snapshot.save(data, tmp_path)
    head = json.loads((tmp_path / snapshot.FILE).read_text(encoding="utf-8"))
    assert head["context"]["util_info"] == table
    rounds_dir = tmp_path / head["rounds_dir"]
    for n in (1, 2):
        assert "points" not in (rounds_dir / f"{n}.json").read_text(encoding="utf-8")
    (rounds_dir / "2.json").unlink()
    snap = snapshot.load(tmp_path)
    assert snap.context["util_info"] == table
    assert snap.view(1).number == 1


@pytest.mark.parametrize("split", [False, True])
def test_an_older_snapshot_still_loads(tmp_path, split):
    data = built()
    if split:
        rounds = data.pop("rounds")
        data["round_files"] = list(rounds)
        rounds_dir = tmp_path / snapshot.ROUNDS_DIR
        rounds_dir.mkdir()
        for n, entry in rounds.items():
            (rounds_dir / f"{n}.json").write_text(json.dumps(entry), encoding="utf-8")
    (tmp_path / snapshot.FILE).write_text(json.dumps(data), encoding="utf-8")
    snap = snapshot.load(tmp_path)
    assert snap.round_numbers == [1, 2] and snap.control(2)["cells"] == {"A Site": 1}


def test_replacing_a_snapshot_keeps_each_reader_on_one_generation(tmp_path):
    snapshot.save(built(), tmp_path)
    old = snapshot.load(tmp_path)
    updated = built()
    updated["rounds"]["1"]["gaps"] = ["updated"]
    updated["rounds"]["2"]["gaps"] = [object()]
    original_files = set(tmp_path.iterdir())
    with pytest.raises(TypeError, match="not JSON serializable"):
        snapshot.save(updated, tmp_path)
    assert set(tmp_path.iterdir()) == original_files
    assert snapshot.load(tmp_path).gaps(1) == []

    updated["rounds"]["2"]["gaps"] = ["updated"]
    snapshot.save(updated, tmp_path)
    latest = snapshot.load(tmp_path)
    assert [latest.gaps(n) for n in latest.round_numbers] == [["updated"], ["updated"]]
    assert [old.gaps(n) for n in old.round_numbers] == [[], []]


def test_repeated_saves_keep_only_the_current_and_the_previous_generation(tmp_path):
    names = []
    for _ in range(4):
        snapshot.save(built(), tmp_path)
        names.append(json.loads((tmp_path / snapshot.FILE).read_text(encoding="utf-8"))["rounds_dir"])
    left = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert left == sorted(names[-2:])               # a reader of the previous manifest can still open its rounds
    assert snapshot.load(tmp_path).gaps(2) == []


def test_a_locked_manifest_is_retried_then_named(tmp_path, monkeypatch):
    from pathlib import Path

    from app.coach import snapfile
    snapshot.save(built(), tmp_path)
    real, refusals = Path.replace, []

    def locked_twice(self, target):
        if len(refusals) < 2:
            refusals.append(target)
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    monkeypatch.setattr(snapfile, "REPLACE_WAIT_S", 0.001)
    monkeypatch.setattr(Path, "replace", locked_twice)
    snapshot.save(built(), tmp_path)                        # two refusals, then the swap goes through
    assert len(refusals) == 2 and snapshot.load(tmp_path).round_numbers == [1, 2]

    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(5, "denied")))
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(PermissionError, match="stayed locked"):
        snapshot.save(built(), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before      # the old snapshot is still the live one


@pytest.mark.parametrize("damage", ["missing", "not json"])
def test_a_damaged_round_file_is_a_snapshot_error_naming_the_round(tmp_path, damage):
    from app.coach import query
    snapshot.save(built(), tmp_path)
    head = json.loads((tmp_path / snapshot.FILE).read_text(encoding="utf-8"))
    path = tmp_path / head["rounds_dir"] / "2.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{not json", encoding="utf-8")
    snap = snapshot.load(tmp_path)
    with pytest.raises(snapshot.SnapshotError, match="round 2's file"):
        snap.gaps(2)
    answer = query.run(snap, tmp_path, "timeline", ["2"])            # an answer the model reads, and logged
    assert "round 2's file" in answer["error"] and answer["id"] in query.read_log(tmp_path)


def test_the_query_tool_loads_no_database_code(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    webapp = Path(__file__).resolve().parents[2]
    snapshot.save(built(), tmp_path)
    # The real entry point, run as ./q runs it; then what it imported.
    code = ("import runpy, sys; sys.argv = ['coach_query.py', '--run', sys.argv[1], '--', 'timeline', '1']\n"
            "try:\n    runpy.run_path('scripts/coach_query.py', run_name='__main__')\n"
            "except SystemExit:\n    pass\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'sqlalchemy' or m in ('app.db', "
            "'app.services.replay_control', 'app.coach.snapshot')))")
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, check=True,
                         cwd=webapp).stdout.strip().splitlines()
    assert json.loads(out[0])["round"] == 1 and out[-1] == "[]"


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
