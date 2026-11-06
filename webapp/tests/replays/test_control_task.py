"""One round's control as a task (app/control/task.py; docs/map-control-worker-plan.md, step 1): the shape
the local command and the replay worker's control child both use, with failures sorted into the round's
own (`engine`, stored) and the machine's (`infra`, retried)."""

import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

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
    assert geo["specials"] == [] and geo["scale"] == 7e-5
    # Provenance reports the scale actually loaded, including a synthetic map's fallback scale.


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


def test_a_round_says_where_its_visibility_cache_came_from(toy, monkeypatch):
    monkeypatch.setattr(toy, "visibility_source", "cache")
    monkeypatch.setattr(toy, "visibility_s", 0.25)
    t, _ = make_task(toy)
    result = task.compute_task(t)
    assert result["status"] == "ok"
    assert result["visibility_source"] == "cache" and result["visibility_s"] == 0.25


def test_a_round_the_engine_refuses_still_says_where_its_cache_came_from(toy, monkeypatch):
    monkeypatch.setattr(toy, "visibility_source", "built")
    players = {0: (None, [(0.0, 150, 200, 0)]), 5: ("B", [(0.0, 380, 250, 180)])}
    t, _ = make_task(toy, sides={}, players=players)     # as test_a_round_the_engine_refuses_is_the_rounds_own_failure
    result = task.compute_task(t)
    assert result["status"] == "failed" and result["error_kind"] == "engine"
    assert result["visibility_source"] == "built"


def test_the_visibility_cache_name_is_one_the_worker_takes_for_that_map(toy):
    import re

    from app.control import geometry
    from replay_worker import control_cache

    name = geometry.visibility_name(toy)
    assert re.fullmatch(rf"{toy.name}\.[0-9a-f]{{16}}\.npz", name)
    assert control_cache.valid(name, toy.name) and control_cache.visibility_name(name)


def test_the_feature_row_line_reports_reuse_only_when_a_row_cache_exists(toy, monkeypatch):
    from types import SimpleNamespace

    assert task._rows_line(toy) is None                       # no feature rows on this map: no line
    monkeypatch.setattr(toy, "_feature_sight_cache", SimpleNamespace(row_hits=30, row_builds=10), raising=False)
    assert task._rows_line(toy) == "Open: feature rows 30 reused, 10 built"


def test_a_warm_build_names_the_cache_file_its_rounds_will_read(toy, monkeypatch):
    from app.control import geometry

    monkeypatch.setattr(toy, "visibility_source", "built")
    monkeypatch.setattr(toy, "visibility_s", 1.5)
    result = task.warm_task({"key": "warm:Open", "map": toy.name})
    assert result["status"] == "ok" and result["key"] == "warm:Open"
    assert result["visibility"] == geometry.visibility_name(toy)
    assert result["visibility_source"] == "built" and result["visibility_s"] == 1.5


def test_a_warm_build_for_a_map_with_no_geometry_is_an_infra_failure():
    result = task.warm_task({"key": "w", "map": "NoSuchMap"})
    assert result["status"] == "failed" and result["error_kind"] == "infra"
    assert "GeometryError" in result["error"]


def test_a_warm_build_loads_with_the_rounds_own_height_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(task, "_load", lambda *a, **k: seen.append(k) or (_ for _ in ()).throw(RuntimeError("x")))
    task.warm_task({"key": "w", "map": "Ascent", "height_mode": "flat", "features": "f" * 64})
    assert seen == [{"height_mode": "flat", "exact": "f" * 64}]


def test_phase_lines_go_to_stderr_only_when_asked(monkeypatch, capsys):
    task._phase("quiet")
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("CONTROL_PHASE_LOG", "1")
    task._phase("Ascent: visibility start")
    assert capsys.readouterr().err == "control phase: Ascent: visibility start\n"


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


def test_a_cache_that_wont_load_is_removed_and_named_in_the_answer(tmp_path, monkeypatch):
    # R5-B3: the replay worker drops its own copy of a named file, so later runs stop downloading it broken.
    from app.control import geometry

    broken = tmp_path / "Ascent.0123456789abcdef.npz"
    broken.write_bytes(b"not a zip")
    monkeypatch.setattr(geometry, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(geometry, "load_geometry", lambda name, **kw: type("G", (), {"name": name})())

    def unreadable(geo):
        raise ValueError("bad zip")
    monkeypatch.setattr(geometry, "visibility", unreadable)
    monkeypatch.setattr(task, "_GEOMETRY", {})
    result = task.warm_task({"key": "w", "map": "Ascent"})
    assert result["status"] == "failed" and result["unreadable"] == [broken.name] and not broken.exists()
    task.UNREADABLE.clear()
