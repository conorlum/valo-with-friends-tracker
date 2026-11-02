"""Cover (<Map>.cover.png, index.json `cover_sha`) joins the control input identity like the buy-phase barrier,
and only on a map that has cover: a map without cover keeps every key, fingerprint and envelope it had.

The no-cover literals below were computed from the code before this change (W5, stage 0c)."""

import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
from map_feature_artifact_toys import base_case, geometry_case, snapshot_case

from app.control import features as cf_features, geometry as cg, task
from app.replays import control_format as cf, map_feature_inputs as fi, map_feature_artifacts as fa
from app.replays import map_feature_sources as sources
from app.replays.control_inputs import InvalidControlInputs, PinnedInputs, verify_result_inputs
from app.replays.height_inputs import HeightSelection
from app.services import replay_control as rcn, replay_gaps

NO_COVER = "Abyss"
COVER = "Ascent"
LINK = {"linked": False, "sides": {}, "db_deaths": []}

# Literals from the unchanged code (Abyss has no cover).
ABYSS_INPUTS = {"barrier": "06d022399053", "scale": 8.1e-05, "sight": "8342f6714685", "specials": [],
                "walk": "42d1d3f29a3c"}
ABYSS_FINGERPRINT = "6a2ce4ec3dad1043"          # over the envelope without control/data/summary/figures
ABYSS_PIN = {**ABYSS_INPUTS, "features": "f" * 64}
ABYSS_TAGS_DIGEST = "909f901f1ac2f29a577ffa135029ebb05578784aba9dcdb59fa5ab0e6d87d092"
ABYSS_SNAPSHOT_KEYS = ["barrier", "barrier_sha", "legacy", "scale", "sight", "specials", "walk"]
ABYSS_SNAPSHOT_SHA = "de011d7c38b5c61d30c9c0d3ad1c494131107808757a4108f670b46dfb4e55ce"


@pytest.fixture
def resolver(monkeypatch):
    """resolve_current_geometry with no database: flat heights, and an artifact that exists."""
    monkeypatch.setattr(rcn.control_heights, "select_height", lambda db, m, d: HeightSelection("flat"))
    monkeypatch.setattr(rcn.control_feature_artifacts, "find_artifact_header",
                        lambda db, key: SimpleNamespace(digest="f" * 64))
    return lambda name: rcn.resolve_current_geometry(None, name)


def envelope_fingerprint(inputs):
    env = cf.input_envelope("recipe", "0" * 64, LINK, inputs)
    for k in ("control", "data", "summary", "figures"):
        env.pop(k)
    return cf.fingerprint_from_inputs(env)


def with_cover_sha(monkeypatch, name, sha):
    index, tags, maps = rcn._assets()
    index = copy.deepcopy(index)
    index[name]["cover_sha"] = sha
    monkeypatch.setattr(rcn, "_assets", lambda: (index, tags, maps))
    _, _, _, raw = rcn._current_snapshot()
    monkeypatch.setattr(rcn, "_current_snapshot", lambda: (index, tags, maps, raw))


# --- no cover: nothing moves ---------------------------------------------------------------------------------

def test_a_map_without_cover_keeps_its_geometry_inputs_and_fingerprint():
    inputs = rcn.geometry_inputs(NO_COVER, {})
    assert inputs == ABYSS_INPUTS and "cover" not in inputs
    assert envelope_fingerprint(inputs) == ABYSS_FINGERPRINT


def test_a_map_without_cover_keeps_its_current_geometry_identity(resolver):
    context = resolver(NO_COVER)
    assert context.state == "ready"
    assert context.pinned.geometry == ABYSS_PIN
    assert context.feature_input.key.tags_digest == ABYSS_TAGS_DIGEST
    assert hashlib.sha256(context.feature_input.canonical_inputs).hexdigest() == ABYSS_TAGS_DIGEST


def test_a_map_without_cover_keeps_geometry_used():
    assert task.geometry_used(cg.load_geometry(NO_COVER, height_mode="flat")) == ABYSS_INPUTS


def test_a_map_without_cover_keeps_its_archived_base():
    index, tags, maps, _ = rcn._current_snapshot()
    snap = sources.base_snapshot(NO_COVER, rcn.CONTROL_DIR, tags.get(NO_COVER, {}), maps[NO_COVER]["xMultiplier"])
    assert sorted(snap) == ABYSS_SNAPSHOT_KEYS
    assert hashlib.sha256(fi.canonical_json(snap)).hexdigest() == ABYSS_SNAPSHOT_SHA


# --- cover: it is in the identity ----------------------------------------------------------------------------

def test_a_map_with_cover_carries_its_cover_sha_in_every_identity(resolver):
    index = rcn._assets()[0]
    sha = index[COVER]["cover_sha"]
    assert rcn.geometry_inputs(COVER, {})["cover"] == sha
    assert resolver(COVER).pinned.geometry["cover"] == sha
    assert task.geometry_used(cg.load_geometry(COVER, height_mode="flat"))["cover"] == sha


def test_a_cover_only_change_moves_the_control_gap_and_util_fingerprints(monkeypatch, resolver):
    from app.services import replay_util
    replay = SimpleNamespace(id=1, map_name=COVER, recipe="r.c1.f1.a0", source_sha256="0" * 64,
                             link_status="none", match_id=None, link_report=None, clock_offset=0.0,
                             db_deaths=None)
    monkeypatch.setattr(rcn, "side_groups", lambda db, r: {})
    monkeypatch.setattr(replay_gaps.choke_assets, "asset_hash", lambda m: "c" * 8)
    before_inputs = rcn.geometry_inputs(COVER, {})
    before_control = rcn.round_fingerprint(replay, {}, 1, {})
    fresh = replay_util.Freshness(None)
    before_util = fresh.expected(replay, 1)
    before_gap = replay_gaps.gap_fingerprint(before_control, COVER, chokes="c" * 8, hearing="h")

    with_cover_sha(monkeypatch, COVER, "0" * 12)
    after_inputs = rcn.geometry_inputs(COVER, {})
    assert {k: v for k, v in after_inputs.items() if k != "cover"} == \
        {k: v for k, v in before_inputs.items() if k != "cover"}           # same sight and walk
    after_control = rcn.round_fingerprint(replay, {}, 1, {})
    assert after_control != before_control
    assert replay_gaps.gap_fingerprint(after_control, COVER, chokes="c" * 8, hearing="h") != before_gap
    assert replay_util.Freshness(None).expected(replay, 1) != before_util


def test_matches_geometry_refuses_a_result_on_other_cover(monkeypatch):
    from app.services import replay_control_remote as remote
    mine = {"sight": "a", "walk": "b", "barrier": None, "specials": [], "scale": 1.0, "cover": "c1"}
    monkeypatch.setattr(rcn, "geometry_inputs", lambda m, h: dict(mine))
    assert remote._matches_geometry({"geometry": dict(mine)}, "X", {})
    assert not remote._matches_geometry({"geometry": {**mine, "cover": "c2"}}, "X", {})
    assert not remote._matches_geometry({"geometry": {k: v for k, v in mine.items() if k != "cover"}}, "X", {})
    plain = {k: v for k, v in mine.items() if k != "cover"}
    monkeypatch.setattr(rcn, "geometry_inputs", lambda m, h: dict(plain))
    assert remote._matches_geometry({"geometry": dict(plain)}, "X", {})
    assert not remote._matches_geometry({"geometry": {**plain, "cover": "c1"}}, "X", {})


def test_verify_result_inputs_refuses_a_result_computed_on_other_cover():
    planned = PinnedInputs({"sight": "a", "cover": "c1"})
    ok = {"status": "ok", "geometry": {"sight": "a", "cover": "c1"}}
    assert verify_result_inputs(planned, ok) is None
    with pytest.raises(InvalidControlInputs):
        verify_result_inputs(planned, {"status": "ok", "geometry": {"sight": "a", "cover": "c2"}})


def test_the_current_snapshot_refuses_an_archived_base_whose_cover_is_not_the_index(monkeypatch, resolver):
    index = rcn._assets()[0]
    assert resolver(COVER).state == "ready"
    with_cover_sha(monkeypatch, COVER, "0" * 12)
    assert resolver(COVER).state == "source_error"


def test_base_snapshot_reads_the_cover_png_when_there_is_one():
    index, tags, maps, _ = rcn._current_snapshot()
    snap = sources.base_snapshot(COVER, rcn.CONTROL_DIR, tags.get(COVER, {}), maps[COVER]["xMultiplier"])
    assert snap["cover_sha"] == index[COVER]["cover_sha"]
    assert snap["cover"]["shape"] == [1024, 1024]


# --- the archive ---------------------------------------------------------------------------------------------

def cover_mask():
    mask = np.zeros((1024, 1024), bool)
    mask[100:110, 100:110] = True
    mask[300:304, 500:520] = True
    return mask


def archived(geo, cover=None):
    base = base_case()
    if cover is not None:
        base["cover"] = cf_features._packed_mask(cover)
        base["cover_sha"] = "abc123abc123"
    return base


def inputs_for(geo, base, monkeypatch):
    monkeypatch.setattr(fi, "CONSUMER_VERSIONS", {"test": 1})
    return fi.identify_features(geo.name, geo.height_sha, snapshot_case(), base)


def test_archive_reconstruction_round_trips_cover(monkeypatch):
    geo = geometry_case(flat=True)
    mask = cover_mask()
    cg.attach_cover(geo, mask, "abc123abc123")
    inp = inputs_for(geo, archived(geo, mask), monkeypatch)
    restored = cf_features.geometry_from_feature_inputs(inp)
    assert np.array_equal(restored.cover_px, geo.cover_px) and restored.cover_px.max() == 2
    assert restored.cover_sha == "abc123abc123"
    cf_features.verify_permanent_context(geo, inp)


def test_archive_without_cover_reconstructs_none(monkeypatch):
    geo = geometry_case(flat=True)
    inp = inputs_for(geo, archived(geo), monkeypatch)
    assert "cover" not in json.loads(inp.canonical_inputs)["base"]
    restored = cf_features.geometry_from_feature_inputs(inp)
    assert restored.cover_px is None and restored.cover_sha is None
    cf_features.verify_permanent_context(geo, inp)


def test_cover_is_attached_before_heights(monkeypatch):
    geo = geometry_case()
    mask = cover_mask()
    cg.attach_cover(geo, mask, "abc123abc123")
    inp = inputs_for(geo, archived(geo, mask), monkeypatch)
    seen = []
    real = cg.attach_heights
    monkeypatch.setattr(cg, "attach_heights", lambda g, a: (seen.append(g.cover_px is not None), real(g, a))[1])
    restored = cf_features.geometry_from_feature_inputs(inp, geo.heights)
    assert seen == [True] and restored.cover_px is not None


@pytest.mark.parametrize("how", ["other", "missing", "extra"])
def test_a_geometry_on_other_cover_than_the_archive_is_refused(monkeypatch, how):
    geo = geometry_case(flat=True)
    mask = cover_mask()
    archive_cover = None if how == "extra" else mask
    inp = inputs_for(geo, archived(geo, archive_cover), monkeypatch)
    if how == "other":
        cg.attach_cover(geo, mask & ~(np.arange(1024)[:, None] < 105), "abc123abc123")
    elif how == "extra":
        cg.attach_cover(geo, mask, "abc123abc123")
    with pytest.raises(fa.FeatureArtifactCorrupt):
        cf_features.verify_permanent_context(geo, inp)


def test_a_corrupt_archived_cover_is_refused(monkeypatch):
    geo = geometry_case(flat=True)
    mask = cover_mask()
    base = archived(geo, mask)
    base["cover"]["shape"] = [128, 128]
    inp = inputs_for(geo, base, monkeypatch)
    with pytest.raises(fa.FeatureArtifactCorrupt):
        cf_features.geometry_from_feature_inputs(inp)
