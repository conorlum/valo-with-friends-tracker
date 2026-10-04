"""scripts/control_cases.py: the user's judged real-round cases (tests/fixtures/control/unknown_cases.json),
checked against a computed round's stored bytes. The rounds themselves are recomputed locally by the script;
here the checking is run on a toy round, and the committed case file is checked for shape."""

import copy
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(WEBAPP / "scripts"))

import control_cases as cc  # noqa: E402

from app.control import engine as ce  # noqa: E402
from app.control.encode import encode_data  # noqa: E402
from app.control.geometry import GRID, PX  # noqa: E402
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
        assert c["expect"] in ("unknown", "clear") and c["side"] in ("A", "B"), c["id"]
        assert c["round"] >= 1 and c["t"] >= 0 and c["source"] and c["map"] and len(c["match"]) == 36, c["id"]
        assert c["cells"] and all(len(p) == 2 and 0 <= p[0] < PX and 0 <= p[1] < PX for p in c["cells"]), c["id"]
