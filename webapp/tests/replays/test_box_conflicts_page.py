"""Corpus box-height metadata stays available after the Control tab's Box heights list is removed."""

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


def stub(monkeypatch, data, digest="d1", row_id=1):
    reads = []
    row = SimpleNamespace(map_name="Summit", digest=digest, id=row_id, rules={"version": 3}, built_at=None)
    monkeypatch.setattr(ch, "active_rows", lambda db: {"Summit": row})

    def read(db, wanted):
        reads.append(wanted)
        return data
    monkeypatch.setattr(ch, "asset_bytes_of_row", read, raising=False)
    return reads


def test_service_filters_records_and_keeps_all_matches(monkeypatch):
    other = dict(RECORD, match="other")
    early = dict(RECORD, round=2, t0=50.0)
    later = dict(RECORD, t0=40.0)
    stub(monkeypatch, asset({"version": 3, "box_conflicts": [later, None, other, RECORD, "bad", early]}))
    assert ch.all_box_conflicts(None) == [("Summit", {"version": 3}, None, record)
                                         for record in [later, other, RECORD, early]]


def test_service_empty_cases(monkeypatch):
    stub(monkeypatch, asset({"version": 3}))
    assert ch.all_box_conflicts(None) == []
    stub(monkeypatch, b"not an npz", digest="d2", row_id=2)
    assert ch.all_box_conflicts(None) == []
    monkeypatch.setattr(ch, "active_rows", lambda db: {})
    assert ch.all_box_conflicts(None) == []

    def boom(db):
        raise RuntimeError("db down")
    monkeypatch.setattr(ch, "active_rows", boom)
    assert ch.all_box_conflicts(None) == []


def test_service_reads_an_asset_as_save_asset_writes_it(monkeypatch, tmp_path):
    """The real writer (heights.save_asset: savez_compressed, sorted JSON) read through the numpy-free meta reader."""
    from app.control import heights as hc

    floors = np.zeros((2, 2), "<i2")
    built = SimpleNamespace(meta={"box_conflicts": [RECORD]}, refinement=None, box_tops=None, floors=floors,
                            spread=floors, supported=floors.astype(bool), unresolved=floors.astype(bool),
                            edges=np.zeros((0, 5), "<i4"), kind=np.zeros((2, 2), "i1"))
    path = tmp_path / "asset.npz"
    hc.save_asset(path, built)
    stub(monkeypatch, path.read_bytes())
    assert ch.all_box_conflicts(None) == [("Summit", {"version": 3}, None, RECORD)]


def test_service_refuses_a_meta_member_that_is_not_uint8(monkeypatch):
    payload = json.dumps({"box_conflicts": [RECORD]}).encode()
    payload += b" " * (len(payload) % 2)
    out = io.BytesIO()
    np.savez(out, meta=np.frombuffer(payload, "<i2"))
    stub(monkeypatch, out.getvalue())
    assert ch.all_box_conflicts(None) == []


def test_service_caches_per_build_not_per_digest(monkeypatch):
    reads = stub(monkeypatch, asset({"version": 3, "box_conflicts": [RECORD]}))
    first = ch.all_box_conflicts(None)
    first.append("junk")
    first[0][3]["z"] = 99
    first[0][1]["version"] = 99
    assert ch.all_box_conflicts(None) == [("Summit", {"version": 3}, None, RECORD)]
    assert reads == [1]
    # The digest covers what the engine reads, not the conflict list: a second build can share it.
    next_record = dict(RECORD, round=3)
    next_reads = stub(monkeypatch, asset({"box_conflicts": [next_record]}), digest="d1", row_id=2)
    assert ch.all_box_conflicts(None) == [("Summit", {"version": 3}, None, next_record)]
    assert next_reads == [2]


def render_player(**context):
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    return env.get_template("replays/_player.html").render(
        replay={"map_name": "Summit", "round_numbers": [1, 18]}, **context)


@pytest.mark.parametrize("linked", [True, False])
@pytest.mark.parametrize("records", [[], [RECORD]])
def test_partial_never_has_box_heights(linked, records):
    html = render_player(linked=linked, control={"cover_reviewed": True}, box_conflicts=records,
                         replay_data={"players": {"1": {"agent": "Omen"}}})
    assert "data-replay-box-heights" not in html
    assert "Box heights" not in html


def context(monkeypatch, linked, control, demo=False):
    page = {"match": {"rounds": [1], "linked": linked, "external_id": MATCH, "control": control, "gaps": False},
            "players": {}}
    monkeypatch.setattr(router.replay_service, "page_context", lambda db, replay: page)
    monkeypatch.setattr(router.settings, "demo_mode", demo, raising=False)
    request = SimpleNamespace(query_params={}, scope={})
    return router.viewer_context(request, None, SimpleNamespace(map_name="Summit", match_uuid=MATCH))


@pytest.mark.parametrize("linked,control,demo", [
    (True, {"cover_reviewed": True}, False), (True, None, False),
    (False, None, False), (True, {"cover_reviewed": True}, True),
])
def test_route_context_has_no_box_conflicts(monkeypatch, linked, control, demo):
    assert "box_conflicts" not in context(monkeypatch, linked, control, demo)
