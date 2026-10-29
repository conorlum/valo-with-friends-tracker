"""Box-height conflicts from the active height asset's meta, shown on the replay page's Control tab (W11)."""

import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from jinja2 import Environment, FileSystemLoader

from app.routers import replays as router
from app.services import control_heights as ch

WEBAPP = Path(__file__).resolve().parents[2]
MATCH = "9210d960-ee88-4cac-975c-8dabc3b7d44a"
RECORD = {"match": MATCH, "round": 18, "slot": 1, "t0": 35.24, "t1": 36.344, "z": 3.0,
          "px": [74, 316], "candidates": [[5, [4.0]], [6, []]]}


def asset(meta):
    out = io.BytesIO()
    np.savez(out, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8), floors=np.zeros(3, "<i2"))
    return out.getvalue()


@pytest.fixture(autouse=True)
def fresh_cache():
    ch._BOX_CONFLICTS.clear()
    yield
    ch._BOX_CONFLICTS.clear()


def stub(monkeypatch, data, digest="d1"):
    reads = []
    row = SimpleNamespace(map_name="Summit", digest=digest, id=1)
    monkeypatch.setattr(ch, "active_rows", lambda db: {"Summit": row})

    def read(db, map_name, dig):
        reads.append(dig)
        return data
    monkeypatch.setattr(ch, "asset_bytes", read)
    return reads


def test_service_filters_and_sorts(monkeypatch):
    other = dict(RECORD, match="other")
    early = dict(RECORD, round=2, t0=50.0)
    later = dict(RECORD, t0=40.0)
    stub(monkeypatch, asset({"version": 3, "box_conflicts": [later, other, RECORD, early]}))
    got = ch.box_conflicts(None, "Summit", MATCH)
    assert got == [early, RECORD, later]
    assert ch.box_conflicts(None, "Summit", "nobody") == []


def test_service_empty_cases(monkeypatch):
    stub(monkeypatch, asset({"version": 3}))
    assert ch.box_conflicts(None, "Summit", MATCH) == []
    stub(monkeypatch, b"not an npz", digest="d2")
    assert ch.box_conflicts(None, "Summit", MATCH) == []
    monkeypatch.setattr(ch, "active_rows", lambda db: {})
    assert ch.box_conflicts(None, "Summit", MATCH) == []

    def boom(db):
        raise RuntimeError("db down")
    monkeypatch.setattr(ch, "active_rows", boom)
    assert ch.box_conflicts(None, "Summit", MATCH) == []


def test_service_caches_per_digest(monkeypatch):
    reads = stub(monkeypatch, asset({"version": 3, "box_conflicts": [RECORD]}))
    first = ch.box_conflicts(None, "Summit", MATCH)
    first.append("junk")
    first[0]["z"] = 99
    assert ch.box_conflicts(None, "Summit", MATCH) == [RECORD]
    assert reads == ["d1"]


def render_player(**context):
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    return env.get_template("replays/_player.html").render(
        replay={"map_name": "Summit", "round_numbers": [1, 18]}, **context)


def test_partial_shows_the_section():
    html = render_player(linked=True, control={"cover_reviewed": True}, box_conflicts=[RECORD],
                         replay_data={"players": {"1": {"agent": "Omen"}}})
    assert "data-replay-box-heights" in html
    for text in ("R18", "35.2", "Omen", "at 3.0 m", "box 5 (top 4.0 m)", "box 6 (no top)", "?round=18&amp;t=35.2"):
        assert text in html, text


def test_partial_falls_back_to_the_slot():
    html = render_player(linked=True, control={"cover_reviewed": True}, box_conflicts=[RECORD],
                         replay_data={"players": {}})
    assert "slot 1" in html


def test_partial_hides_the_section():
    full = dict(control={"cover_reviewed": True}, replay_data={"players": {}})
    assert "data-replay-box-heights" not in render_player(linked=True, box_conflicts=[], **full)
    assert "data-replay-box-heights" not in render_player(linked=True, **full)
    assert "data-replay-box-heights" not in render_player(linked=False, box_conflicts=[RECORD], **full)


def context(monkeypatch, linked, control, demo=False):
    page = {"match": {"rounds": [1], "linked": linked, "external_id": MATCH, "control": control, "gaps": False},
            "players": {}}
    monkeypatch.setattr(router.replay_service, "page_context", lambda db, replay: page)
    monkeypatch.setattr(router.settings, "demo_mode", demo, raising=False)
    monkeypatch.setattr(router.control_heights, "box_conflicts", lambda db, m, u: [RECORD])
    request = SimpleNamespace(query_params={}, scope={})
    return router.viewer_context(request, None, SimpleNamespace(map_name="Summit", match_uuid=MATCH))


def test_route_context(monkeypatch):
    assert context(monkeypatch, True, {"cover_reviewed": True})["box_conflicts"] == [RECORD]
    assert context(monkeypatch, True, None)["box_conflicts"] == []
    assert context(monkeypatch, False, None)["box_conflicts"] == []
    assert context(monkeypatch, True, {"cover_reviewed": True}, demo=True)["box_conflicts"] == []
