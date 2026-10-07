"""scripts/control_cases.py: the user's judged real-round cases (tests/fixtures/control/unknown_cases.json),
checked against a computed round's stored bytes. The rounds themselves are recomputed locally by the script;
here the checking is run on a toy round, and the committed case file is checked for shape."""

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE =Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import control_cases as cc  # noqa: E402

from app.control import engine as ce  # noqa: E402
from app.control.encode import encode_data  # noqa: E402
from app.control.geometry import GRID, PX  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from tests.replays.control_toys import blob, open_hall  # noqa: E402


def _round():
    """The open hall with a barrier down x 256; each team faces its own back wall, so at 2 s A's unknown
    has walked to x 240 but not to x 160 (test_the_spawn_is_held_until_an_enemy_could_have_walked_there)."""
    geo = copy.copy(open_hall())
    geo.barrier = np.zeros((GRID, GRID), bool)
    geo.barrier[:, geo.cell_of_px(256, 200) % GRID] = True
    b = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, t_end=6.0)
    return encode_data(ce.compute_round(b, geo, ticks=[0.0, 1.0, 2.0]), b)


def _case(expect, cells, t=2.0, side="A"):
    return {"id": "toy", "round": 1, "t": t, "side": side, "expect": expect, "cells": cells}


def test_a_case_passes_when_every_listed_cell_is_as_expected():
    data = _round()
    assert cc.check_case(_case("unknown", [[240, 200]]), data)["passes"]
    assert cc.check_case(_case("clear", [[160, 200]]), data)["passes"]


def test_a_case_fails_naming_the_cells_that_are_not():
    data = _round()
    result = cc.check_case(_case("unknown", [[240, 200], [160, 200]]), data)
    assert not result["passes"] and result["wrong"] == [[160, 200]]
    assert not cc.check_case(_case("clear", [[240, 200]]), data)["passes"]


def test_the_tick_checked_is_the_last_at_or_before_t():
    data = _round()
    assert cc.check_case(_case("clear", [[240, 200]], t=0.5), data)["passes"], "0.5 s reads the 0 s tick"
    result = cc.check_case(_case("unknown", [[240, 200]], t=2.5), data)
    assert result["passes"] and result["t_checked"] == 2.0


def test_a_cell_off_the_walkable_ground_fails_rather_than_passing():
    result = cc.check_case(_case("clear", [[2, 2]]), _round())
    assert not result["passes"] and result["wrong"] == [[2, 2]]


def test_the_committed_cases_are_well_formed():
    data = json.loads(cc.CASES.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    assert ids and len(ids) == len(set(ids))
    for c in data["cases"]:
        assert c["expect"] in ("unknown", "clear", "state") and c["side"] in ("A", "B"), c["id"]
        if c["expect"] == "state":
            assert c["states"] and set(c["states"]) <= set(cf.STATE_NAMES), c["id"]
        assert c["round"] >= 1 and c["t"] >= 0 and c["source"] and c["map"] and len(c["match"]) == 36, c["id"]
        assert c["cells"] and all(len(p) == 2 and 0 <= p[0] < PX and 0 <= p[1] < PX for p in c["cells"]), c["id"]


def test_a_state_case_checks_each_cells_state():
    data = _round()
    own = dict(_case("state", [[160, 200]]), states=["a_passive", "a_safe", "a_active"])
    assert cc.check_case(own, data)["passes"], "at 2 s x 160 is deep in A's start ground"
    nobody = dict(_case("state", [[160, 200]]), states=["none"])
    result = cc.check_case(nobody, data)
    assert not result["passes"] and result["wrong"] == [[160, 200]]


def test_a_state_case_off_the_walkable_ground_fails():
    result = cc.check_case(dict(_case("state", [[2, 2]]), states=["none"]), _round())
    assert not result["passes"] and result["wrong"] == [[2, 2]]


def test_a_state_case_checks_every_listed_cell_not_just_the_first():
    """x 248 is nobody's at 2 s (A's unknown has walked to x 240); x 160 is still A's, so it isn't."""
    case = dict(_case("state", [[248, 200], [160, 200]]), states=["none"])
    result = cc.check_case(case, _round())
    assert not result["passes"] and result["wrong"] == [[160, 200]]


# ---------------------------------------------------------------- the local-inputs mode (--inputs)

MATCH = "00000000-0000-4000-8000-000000000001"
WALL = {"k": "ability", "t": 1.0, "t1": 5.0, "by": 5, "kind": "GameObject", "code": "Cable", "name": "E_CableJamRoot",
        "u": 2500, "v": 1953}


def _manifest(tmp_path, *, util=(), requires=None, **changes):
    """A one-round manifest beside its blob (the toy hall round, with `util`), and the blob's dict."""
    import hashlib

    from app.replays import format as fmt

    b = blob({0: ("A", [(0.0, 120, 200, 180)]), 5: ("B", [(0.0, 400, 200, 0)])}, t_end=6.0, util=util)
    data = fmt.encode_blob(b)
    (tmp_path / "1.json.gz").write_bytes(data)
    row = {"match": MATCH, "round": 1, "map": "Toy", "blob": "1.json.gz",
           "blob_sha256": hashlib.sha256(data).hexdigest(), "source_sha256": "ab" * 32,
           "condense_revision": fmt.CONDENSE_REVISION,
           "link": {"sides": {"0": "attack", "5": "defense"}, "db_deaths": [[5, 4.5]]}}
    if requires is not None:
        row["requires"] = requires
    row.update(changes)
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps({"rounds": [row]}), encoding="utf-8")
    return path, b, data


def test_local_inputs_give_the_task_the_live_mode_would_build(tmp_path, monkeypatch):
    import urllib.request

    def no_network(*args, **kwargs):
        raise AssertionError("the local mode fetched something")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    path, _, data = _manifest(tmp_path, util=[WALL], requires=[{"k": "ability", "code": "Cable", "name": "^E_"}])
    tasks = cc.load_inputs(path)
    assert tasks == {(MATCH, 1): {"key": (MATCH, 1), "map": "Toy", "blob": data,
                                  "link": {"sides": {"0": "attack", "5": "defense"}, "db_deaths": [[5, 4.5]]}}}


def test_the_same_blob_gives_the_same_result_whichever_way_it_arrives(tmp_path):
    """The served and the local path hand compute_task the same bytes and link: the bytes of the result match."""
    from app.replays import format as fmt

    path, b, data = _manifest(tmp_path)
    task = cc.load_inputs(path)[(MATCH, 1)]
    link = ce.ControlLink(sides={int(s): side for s, side in task["link"]["sides"].items()},
                          db_deaths=tuple((int(s), float(t)) for s, t in task["link"]["db_deaths"]))
    geo = open_hall()
    local = encode_data(ce.compute_round(fmt.decode_blob(task["blob"]), geo, link, ticks=[0.0, 1.0, 2.0]), b)
    served = encode_data(ce.compute_round(fmt.decode_blob(data), geo, link, ticks=[0.0, 1.0, 2.0]), b)
    assert local == served
    assert cc.check_case(_case("clear", [[160, 200]]), local)["passes"]


def test_local_inputs_refuse_a_missing_or_different_blob(tmp_path):
    import pytest

    path, _, _ = _manifest(tmp_path)
    (tmp_path / "1.json.gz").unlink()
    with pytest.raises(cc.InputsError, match="no blob at"):
        cc.load_inputs(path)
    path, _, data = _manifest(tmp_path)
    (tmp_path / "1.json.gz").write_bytes(data + b"\x00")
    with pytest.raises(cc.InputsError, match="sha256 differs"):
        cc.load_inputs(path)
    with pytest.raises(cc.InputsError, match="unreadable manifest"):
        cc.load_inputs(tmp_path / "absent.json")


@pytest.mark.parametrize("changes, message", [
    ({"round": 2}, "the blob is round 1"),
    ({"map": "Summit"}, "the blob is round 1 on 'Toy'"),
    ({"condense_revision": 1}, "condensed at revision 1"),
    ({"source_sha256": "abc"}, "not a sha256"),
    ({"link": {"sides": {"0": "attack", "5": "sideways"}}}, "name another side"),
    ({"blob_sha256": None, "blob": None}, "missing or bad"),
])
def test_local_inputs_refuse_a_mismatched_identity_or_context(tmp_path, changes, message):
    path, _, _ = _manifest(tmp_path, **changes)
    with pytest.raises(cc.InputsError, match=message):
        cc.load_inputs(path)


def test_local_inputs_refuse_a_blob_without_the_required_event(tmp_path):
    need = [{"k": "ability", "code": "Cable", "name": "^E_"}]
    path, _, _ = _manifest(tmp_path, util=[], requires=need)
    with pytest.raises(cc.InputsError, match="lacks required util rows"):
        cc.load_inputs(path)
    path, _, _ = _manifest(tmp_path, util=[WALL], requires=[dict(need[0], min=2)])
    with pytest.raises(cc.InputsError, match="lacks required util rows"):
        cc.load_inputs(path)


def test_required_rows_match_on_kind_and_patterns():
    b = {"util": [WALL, {"k": "status", "status": "suppressed", "code": "Grenadier", "name": "E_SuppressionPulse"}]}
    assert cc.required_missing(b, [{"k": "ability", "code": "^Cable$"}, {"k": "status", "status": "suppressed"}]) == []
    assert cc.required_missing(b, [{"k": "reveal"}]) == [{"k": "reveal"}]
    assert cc.required_missing(b, [{"k": "ability", "name": "Sensor"}]) == [{"k": "ability", "name": "Sensor"}]
    assert cc.required_missing(b, []) == []


def test_the_local_mode_stops_before_computing_when_no_case_names_its_rounds(tmp_path):
    path, _, _ = _manifest(tmp_path)
    with pytest.raises(SystemExit, match="no case names a round"):
        cc.main(["--inputs", str(path)])
    (tmp_path / "1.json.gz").unlink()
    with pytest.raises(SystemExit, match="INPUTS"):
        cc.main(["--inputs", str(path)])


def test_a_local_case_is_well_formed_and_marked():
    """Cases that need newly extracted utility are marked `local`, so the live run leaves them out."""
    data = json.loads(cc.CASES.read_text(encoding="utf-8"))
    assert all(isinstance(c.get("local", False), bool) for c in data["cases"])
