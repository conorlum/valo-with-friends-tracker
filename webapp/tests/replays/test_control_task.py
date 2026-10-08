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


from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control import task as control_task  # noqa: E402
from tests.replays.control_toys import height_rounds, open_hall, standing  # noqa: E402,F811


def _toy_asset():
    geo = open_hall()
    return hb.build(height_rounds(lambda m, n: {0: standing(204, 204, 0.0), 1: standing(212, 204, 0.0)}), geo).asset


def test_a_task_naming_a_height_digest_reads_it_from_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_CACHE_DIR", str(tmp_path))
    asset = _toy_asset()
    path = cg.height_cache_path("Toy", asset.digest)
    assert path == tmp_path / "heights" / f"Toy.{asset.digest}.height.npz"
    with pytest.raises(cg.GeometryError, match="not on this machine"):
        control_task.height_file("Toy", asset.digest)
    path.parent.mkdir(parents=True)
    hc.save_asset(path, asset)
    assert control_task.height_file("Toy", asset.digest) == path
    other = cg.height_cache_path("Toy", "0" * 12)
    hc.save_asset(other, asset)                           # a file whose bytes are another asset's
    with pytest.raises(cg.GeometryError, match="is not the asset"):
        control_task.height_file("Toy", "0" * 12)
    assert not other.exists(), "a wrong file is removed, so the next push replaces it"


def test_a_failure_to_find_the_heights_is_the_machines_not_the_rounds(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_CACHE_DIR", str(tmp_path))
    out = control_task.compute_task({"key": "k", "map": "Ascent", "height": "f" * 12, "blob": b"", "link":
                                     {"sides": {}, "db_deaths": []}})
    assert out["status"] == "failed" and out["error_kind"] == "infra" and "not on this machine" in out["error"]
