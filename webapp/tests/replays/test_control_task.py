"""One round's control as a task (app/control/task.py; docs/map-control-worker-plan.md, step 1): the shape
the local command and the replay worker's control child both use, with failures sorted into the round's
own (`engine`, stored) and the machine's (`infra`, retried)."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from control_toys import blob, open_hall  # noqa: E402

from app.control import engine, task  # noqa: E402
from app.control.encode import encode_data, encode_summary  # noqa: E402
from app.replays import format as fmt  # noqa: E402


@pytest.fixture
def toy(monkeypatch):
    geo = open_hall()
    monkeypatch.setitem(task._GEOMETRY, geo.name, geo)
    return geo


def make_task(geo, sides=None, players=None):
    players = players or {0: ("A", [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}
    data = blob(players, t_end=4.0)
    link = {"sides": sides if sides is not None else {"0": "attack", "5": "defense"}, "db_deaths": []}
    return {"key": 7, "map": geo.name, "blob": fmt.encode_blob(data), "link": link}, data


def test_a_round_gives_the_same_bytes_as_the_engine(toy):
    t, data = make_task(toy)
    result = task.compute_task(t)
    assert result["status"] == "ok" and result["key"] == 7 and result["seconds"] >= 0
    rc = engine.compute_round(data, toy, engine.ControlLink(sides={0: "attack", 5: "defense"}))
    assert result["data"] == encode_data(rc, data) and result["summary"] == encode_summary(rc, data)
    geo = result["geometry"]
    assert set(geo) == {"sight", "walk", "barrier", "specials", "scale"} and len(geo["sight"]) == 12
    assert geo["specials"] == [] and geo["scale"] is None       # a toy map isn't in maps.json


def test_the_geometry_hashes_are_the_ones_the_build_records():
    import hashlib

    from app.control import geometry

    geo = geometry.load_geometry("Ascent")
    index = __import__("json").loads((geometry.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]["Ascent"]
    used = task.geometry_used(geo)
    assert used["sight"] == index["sight_sha"] and used["walk"] == index["walk_sha"]
    assert used["scale"] == __import__("json").loads(geometry.MAPS_JSON.read_text(encoding="utf-8"))["Ascent"]["xMultiplier"]
    assert hashlib.sha256(np.packbits(geo.sight).tobytes()).hexdigest()[:12] == index["sight_sha"]


def test_a_round_the_engine_refuses_is_the_rounds_own_failure(toy):
    players = {0: (None, [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}
    t, _ = make_task(toy, sides={}, players=players)     # slot 0: no side group and no side in the link
    result = task.compute_task(t)
    assert result["status"] == "failed" and result["error_kind"] == "engine"
    assert result["error"].startswith("ControlError")


def test_a_map_the_machine_has_no_geometry_for_is_an_infra_failure(toy):
    t, _ = make_task(toy)
    t["map"] = "NoSuchMap"
    result = task.compute_task(t)
    assert result["status"] == "failed" and result["error_kind"] == "infra"
    assert "GeometryError" in result["error"]
