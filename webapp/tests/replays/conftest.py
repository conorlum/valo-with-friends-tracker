"""Marks `geometry` every replay test module that uses the map-control engine (app.control, app.gaps, the height
build, map features) or its toy maps, so `-m geometry` / `-m "not geometry"` split the replay tests in two: the
engine on one side, the parser, worker, upload and viewer on the other."""

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GEOMETRY = re.compile(r"app\.control|app\.gaps|app import control|control_toys|map_feature|app\.replays\.height|"
                      r"height_build")
_seen: dict = {}


@pytest.hookimpl(tryfirst=True)  # before -m deselects by marker
def pytest_collection_modifyitems(config, items):
    for item in items:
        path = item.path.resolve()
        if path.parent != HERE:
            continue
        if path not in _seen:
            _seen[path] = bool(GEOMETRY.search(path.read_text(encoding="utf-8")))
        if _seen[path]:
            item.add_marker(pytest.mark.geometry)
