"""Map area JSON is shared by every viewer and follows reviewed paint edits."""

import json

import pytest
from fastapi import FastAPI

from app.coach import areas
from app.config import settings
from app.control.geometry import load_base_geometry
from app.db import get_db
from app.replays import control_format as cf
from app.replays import format as fmt
from app.routers import replays
from app.services import replay_areas, site_health
from tests.coach.test_areas import painted_object, write_paint
from tests.test_site_health_slow import TestClient


def expand(runs):
    return [value for value, count in runs for _ in range(count)]


@pytest.fixture
def get_areas(monkeypatch, tmp_path):
    replay_areas._payload.cache_clear()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    app = FastAPI()
    app.include_router(replays.router)

    def no_database():
        pytest.fail("map areas must not depend on a viewer or database")

    app.dependency_overrides[get_db] = no_database

    def get(map_name="Sunset", viewer=None):
        async def with_session(scope, receive, send):
            scope["session"] = {} if viewer is None else {"player_id": viewer}
            await app(scope, receive, send)

        messages = TestClient(with_session).request(f"/replays/areas/{map_name}.json")
        status = messages[0]["status"]
        body = b"".join(message.get("body", b"") for message in messages[1:])
        return status, json.loads(body)

    return get


@pytest.mark.parametrize("map_name", ["Unknown", "Sunset.extra"])
def test_unknown_map_is_404(get_areas, map_name):
    assert get_areas(map_name)[0] == 404


def test_demo_is_404_even_after_a_cached_request(get_areas, monkeypatch):
    assert get_areas()[0] == 200
    monkeypatch.setattr(settings, "demo_mode", True)
    assert get_areas()[0] == 404


def test_missing_paint_guesses_a_name_for_every_sunset_walk_cell(get_areas):
    status, body = get_areas()
    assert status == 200
    assert set(body) == {"map", "approximate", "names", "cells", "bands"}
    assert body["map"] == "Sunset" and body["approximate"] is True
    assert body["bands"] == {}
    names = {row["id"]: row for row in body["names"]}
    assert all(set(row) == {"id", "name", "parent", "super"} for row in names.values())
    cells = expand(body["cells"])
    assert len(cells) == cf.GRID ** 2
    walk = load_base_geometry("Sunset").walk.ravel()
    for cell, walkable in enumerate(walk):
        if not walkable:
            assert cells[cell] == 0
            continue
        u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        guess = areas.area_at("Sunset", u, v)
        assert names[cells[cell]] == {
            "id": cells[cell], "name": guess["name"], "parent": None, "super": guess["super"],
        }


def test_reviewed_paint_passes_through_ground_and_height_bands(get_areas, tmp_path):
    obj = painted_object({0: 1, 1: 2}, bands={"1": [[-20, 0, 2], [10, 30, 3]]})
    write_paint(tmp_path, obj)
    status, body = get_areas()
    assert status == 200 and body["approximate"] is False
    assert body["map"] == "Sunset"
    assert body["names"] == [{key: row[key] for key in ("id", "name", "parent", "super")}
                             for row in obj["names"]]
    assert body["cells"] == obj["cells"]
    assert expand(body["cells"])[2:] == [0] * (cf.GRID ** 2 - 2)
    assert body["bands"] == obj["bands"]


def test_unreviewed_paint_stays_approximate(get_areas, tmp_path):
    write_paint(tmp_path, painted_object({0: 1}) | {"reviewed": False})
    assert get_areas()[1]["approximate"] is True


def test_cache_follows_creation_byte_changes_and_removal(get_areas, tmp_path, monkeypatch):
    calls = []
    original = areas.callouts

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(areas, "callouts", counted)
    approximate = get_areas()[1]
    assert approximate["approximate"] is True
    assert get_areas()[1] == approximate
    assert len(calls) == 1  # the fallback is built only once for this table hash
    path = write_paint(tmp_path, painted_object({0: 1}))
    first = get_areas()[1]
    assert first["approximate"] is False
    changed = painted_object({0: 3, 1: 2})
    path.write_text(json.dumps(changed), encoding="utf-8")
    second = get_areas()[1]
    assert second["cells"] == changed["cells"] and second != first
    path.unlink()
    assert get_areas()[1] == approximate


@pytest.mark.parametrize("painted", [False, True])
def test_viewers_and_logged_out_get_identical_bodies(get_areas, tmp_path, painted):
    if painted:
        write_paint(tmp_path, painted_object({0: 1}))
    responses = [get_areas(viewer=viewer) for viewer in (None, 101, 202)]
    assert all(status == 200 for status, _ in responses)
    assert responses[0] == responses[1] == responses[2]


def test_map_areas_are_classified_as_data():
    assert site_health.PAGE_ROUTES["/replays/areas/{map_name}.json"] == "not_page"
