"""The spike's step rule, its grids, the zone builder's pure parts and the archive repair."""
import gzip
import json
import math

import numpy as np
import pytest
from sqlalchemy import select

from app.models import RoundPlant
from app.services import plant_position as position
from app.services import round_plants as plants
from scripts import build_plant_zones as builder
from tests.test_round_plants import db, planted_match


def grids(zone, walk):
    height, width = zone.shape
    return (0, 0, width, height, bytearray(zone.astype(np.uint8).ravel().tobytes()),
            bytearray(walk.astype(np.uint8).ravel().tobytes()))


@pytest.fixture
def world(monkeypatch):
    """A 200x200-cell open zone; one uv unit per metre-hundredth along +u from (5000, 5000)."""
    size = position.GRID_SIZE
    zone, walk = np.zeros((size, size), bool), np.zeros((size, size), bool)
    zone[412:612, 412:612] = True
    walk[312:712, 312:712] = True
    monkeypatch.setattr(position, "_grids", lambda key: grids(zone, walk) if key == "Test" else None)
    return zone, walk


def along(metres):
    return 5000 + metres * 100, 5000


@pytest.mark.parametrize("values", [[], [0], [1], [0, 0, 1, 1, 1, 0], [1, 0, 1, 1]])
def test_runs_round_trip(values):
    assert list(position.decode_runs(position.encode_runs(values), len(values))) == values


def test_runs_that_do_not_fill_the_grid_are_refused():
    with pytest.raises(ValueError):
        position.decode_runs([1, 1], 3)


def test_open_ground_takes_the_full_step(world):
    assert position.pick_step("Test", along) == 0.7


@pytest.mark.parametrize("wall_m,step", [(1.5, 0.7), (1.2, 0.4), (0.9, 0.2), (0.5, 0.0)])
def test_a_wall_ahead_shortens_the_step(world, wall_m, step):
    _, walk = world
    cell = int((5000 + wall_m * 100) / position.UV_SCALE * position.GRID_SIZE)
    walk[:, cell:] = False
    assert position.pick_step("Test", along) == step


def test_the_site_edge_shortens_the_step(world):
    zone, _ = world
    cell = int((5000 + 0.55 * 100) / position.UV_SCALE * position.GRID_SIZE)
    zone[:, cell:] = False
    assert position.pick_step("Test", along) == 0.4


def test_a_planter_on_a_box_is_held_only_by_the_zone(world):
    _, walk = world
    walk[:] = False
    assert position.pick_step("Test", along) == 0.7


def test_only_a_box_that_was_planted_on_joins_the_zone():
    zone = np.zeros((100, 100), bool)
    zone[20:80, 20:80] = True
    zone[30:40, 30:40] = False
    zone[60:70, 60:70] = False
    filled = builder.fill_planted_holes(zone, np.array([[35, 35], [36, 36], [65, 65]]))
    assert filled[35, 35] and not filled[65, 65] and filled[25, 25]


def test_an_uncovered_map_raises_and_is_not_covered(world):
    assert not position.covers("Other") and not position.covers(None) and position.covers("Test")
    with pytest.raises(KeyError):
        position.pick_step("Other", along)


def facing(view=0.0):
    return {"type": "round-summary", "attributes": {"round": 1}, "metadata": {"plant": {
        "site": "B", "location": {"x": 1000, "y": 2000}, "platformUserIdentifier": "planter",
        "playerLocations": [{"platformUserIdentifier": "planter", "location": {"x": 1000, "y": 2000},
                             "viewRadians": view}]}}}


def test_plant_uv_uses_the_picked_step_along_the_world_aim(monkeypatch):
    plant = plants.plant_from_summary(facing())
    monkeypatch.setattr(plants.plant_position, "covers", lambda key: True)
    monkeypatch.setattr(plants.plant_position, "pick_step", lambda key, point_at: 0.2)
    assert plants.plant_uv("Bind", plant) == plants.to_uv("Bind", 1020, 2000)


def test_plant_uv_keeps_the_full_step_on_an_uncovered_map(monkeypatch):
    plant = plants.plant_from_summary(facing())
    monkeypatch.setattr(plants.plant_position, "covers", lambda key: False)
    assert plants.plant_uv("Bind", plant) == plants.to_uv("Bind", 1070, 2000)


def test_plant_uv_without_facing_keeps_tracker_position():
    summary = facing()
    summary["metadata"]["plant"]["playerLocations"] = []
    plant = plants.plant_from_summary(summary)
    assert plants.plant_uv("Bind", plant) == plants.to_uv("Bind", 1000, 2000)


def test_zone_mask_keeps_the_tint_across_a_drawn_line_and_drops_specks():
    rgba = np.zeros((200, 200, 4), np.uint8)
    rgba[..., :3], rgba[..., 3] = 118, 255
    rgba[50:120, 50:130] = (152, 152, 118, 255)
    rgba[80:82, 50:130] = (255, 255, 255, 255)
    rgba[150:154, 150:154] = (152, 152, 118, 255)
    mask = builder.zone_mask(rgba)
    assert mask[81, 90] and mask[60, 60] and not mask[152, 152] and not mask[10, 10]


def test_best_shift_finds_the_alignment_and_prefers_no_move():
    zone = np.zeros((100, 100), bool)
    zone[40:60, 40:60] = True
    assert builder.best_shift(zone, np.array([[50, 50], [45, 55]])) == (0, 0)
    edge = np.array([[61, 50], [61, 45], [50, 50]])
    dy, dx = builder.best_shift(zone, edge)
    assert builder.inside_count(builder.shifted(zone, dy, dx), edge) == 3 and dy == 2 and dx == 0


def test_reposition_updates_tracker_rows_from_the_archive(db, tmp_path, monkeypatch):
    match = planted_match(db, external_id="synthetic-archive")
    plants.upsert_plants(db, match, [dict(round_number=1, u=1, v=2, site="B")], source="tracker")
    db.commit()
    (tmp_path / "synthetic-archive.json.gz").write_bytes(gzip.compress(json.dumps({"segments": [facing()]}).encode()))
    (tmp_path / "not-in-db.json.gz").write_bytes(gzip.compress(b'{"segments": []}'))
    (tmp_path / "broken.json.gz").write_bytes(b"not gzip")
    dry = plants.reposition(db, tmp_path, dry_run=True, log=lambda _: None)
    assert dry["updated"] == 1 and dry["bad"] == 1 and dry["unknown"] == 1
    assert (db.scalar(select(RoundPlant)).u, db.scalar(select(RoundPlant)).v) == (1, 2)
    result = plants.reposition(db, tmp_path, log=lambda _: None)
    assert result["updated"] == 1
    row = db.scalar(select(RoundPlant))
    assert (row.u, row.v) != (1, 2) and row.source == "tracker"
    assert plants.reposition(db, tmp_path, log=lambda _: None)["updated"] == 0


def test_reposition_never_replaces_a_replay_position(db, tmp_path):
    match = planted_match(db, external_id="synthetic-replay")
    plants.upsert_plants(db, match, [dict(round_number=1, u=1234, v=5678, site="A")], source="replay")
    db.commit()
    (tmp_path / "synthetic-replay.json.gz").write_bytes(gzip.compress(json.dumps({"segments": [facing()]}).encode()))
    assert plants.reposition(db, tmp_path, log=lambda _: None)["skipped"] == 1
    assert (db.scalar(select(RoundPlant)).u, db.scalar(select(RoundPlant)).v) == (1234, 5678)


def test_reposition_stops_when_asked(db, tmp_path):
    (tmp_path / "a.json.gz").write_bytes(gzip.compress(b'{"segments": []}'))
    assert plants.reposition(db, tmp_path, should_stop=lambda: "paused", log=lambda _: None)["stopped"] == "paused"
