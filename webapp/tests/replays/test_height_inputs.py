"""What a height build is made from (app/replays/height_inputs.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): the input manifest both services compute,
and the check set, which is never "nothing to check" because its file is missing."""

import hashlib
import json

import pytest

from app.control import heights as hc
from app.replays import control_format as cf
from app.replays import height_inputs as hi

GEOMETRY = {"sight": "s" * 12, "walk": "w" * 12, "scale": 7e-5}
REPLAYS = [["m2", "p.c11.f1.a1", "b" * 64, 20], ["m1", "p.c11.f1.a1", "a" * 64, 24]]

# HEIGHT_RULES_REVISION -> the hash of app/control/heights.py's constants it was released with. Changed a
# constant, or the build's code? Bump HEIGHT_RULES_REVISION in app/replays/control_format.py and pin it here.
PINNED_RULES = {1: "d8abb3511f70", 2: "d8abb3511f70", 3: "72ed40ed76a5", 4: "72ed40ed76a5", 5: "72ed40ed76a5",
                # re-pinned in place for EYE_M 1.05 (was "0eb4ba581603")
                6: "93c941558c40"}    # 6 unreleased: re-pinned in place for reach and crouch stance (was
                                      # "bb8b0358a9c8", then "52136ab282f4"), for the head and feet sight points
                                      # ("29ebe9bc316b"), for the 1 m step ("6d8f90ffeaa2"), then for VIEW_REACH_M
                                      # (was "ef8175ac1844"), then for the crouch key


@pytest.mark.parametrize("name, value", [("BOX_STEP_M", 0.6), ("BOX_RING_PX", 24), ("BOX_STEADY_DZ_M", 0.2),
                                       ("TOP_REACH_M", 0.8), ("VIEW_REACH_M", 1.2), ("TOP_TOL_M", 0.25), ("CROUCH_DROP_M", 0.3),
                                       ("EYE_M", 1.1), ("HEAD_M", 1.0), ("FEET_M", -0.6),
                                       ("CROUCH_LAG_S", 0.3), ("CROUCH_NEAR_S", 3.0)])
def test_a_box_top_constant_moves_the_rules_hash(monkeypatch, name, value):
    before = hc.rules()["constants"]
    monkeypatch.setattr(hc, name, value)
    assert hc.rules()["constants"] != before


def test_without_the_new_stance_constants_the_previous_rules_hash_is_unchanged(monkeypatch):
    for name in ("TOP_REACH_M", "VIEW_REACH_M", "TOP_TOL_M", "CROUCH_DROP_M", "HEAD_M", "FEET_M",
                 "CROUCH_LAG_S", "CROUCH_NEAR_S"):
        monkeypatch.delattr(hc, name, raising=False)
    monkeypatch.setattr(hc, "EYE_M", 0.7)
    monkeypatch.setattr(hc, "STEP_UP_M", 0.7)
    monkeypatch.setattr(hc, "BODY_M", 0.3, raising=False)
    assert hc.rules()["constants"] == "bb8b0358a9c8"


def test_without_view_reach_the_previous_revision_six_hash_is_unchanged(monkeypatch):
    monkeypatch.delattr(hc, "CROUCH_LAG_S", raising=False)
    monkeypatch.delattr(hc, "CROUCH_NEAR_S", raising=False)
    monkeypatch.delattr(hc, "VIEW_REACH_M")
    monkeypatch.setattr(hc, "EYE_M", 1.1)
    assert hc.rules()["constants"] == "6d8f90ffeaa2"


def test_with_the_previous_eye_the_revision_six_rules_hash_is_unchanged(monkeypatch):
    monkeypatch.delattr(hc, "CROUCH_LAG_S", raising=False)
    monkeypatch.delattr(hc, "CROUCH_NEAR_S", raising=False)
    monkeypatch.setattr(hc, "EYE_M", 1.1)
    assert hc.rules()["constants"] == "0eb4ba581603"


def test_without_the_crouch_key_constants_the_previous_rules_hash_is_unchanged(monkeypatch):
    monkeypatch.delattr(hc, "CROUCH_LAG_S", raising=False)
    monkeypatch.delattr(hc, "CROUCH_NEAR_S", raising=False)
    assert hc.rules()["constants"] == "ef8175ac1844"


def test_the_height_rules_are_pinned_to_their_revision():
    digest = hc.rules()["constants"]
    assert PINNED_RULES.get(cf.HEIGHT_RULES_REVISION) == digest, (
        f"the height constants changed (hash {digest}): bump HEIGHT_RULES_REVISION in "
        f"app/replays/control_format.py and pin {{{cf.HEIGHT_RULES_REVISION + 1}: '{digest}'}} here")
    assert hc.rules() == {"version": cf.HEIGHT_VERSION, "revision": cf.HEIGHT_RULES_REVISION, "constants": digest}


def test_the_manifest_is_the_same_whatever_order_the_replays_come_in():
    a = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    b = hi.manifest("Sunset", REPLAYS[::-1], dict(GEOMETRY), "k" * 12)
    assert hi.digest(a) == hi.digest(b) and len(hi.digest(a)) == 16
    assert hi.key(a) == f"heights:Sunset:{hi.digest(a)}"
    assert hi.rounds_expected(a) == 44 and hi.matches(a) == ["m1", "m2"]
    assert a["height_version"] == cf.HEIGHT_VERSION and a["height_rules"] == cf.HEIGHT_RULES_REVISION
    json.dumps(a)


@pytest.mark.parametrize("name, change", [
    ("a re-condensed blob", lambda r, g, k: ([[r[0][0], "p.c12.f1.a1", r[0][2], r[0][3]], r[1]], g, k)),
    ("a replaced source file", lambda r, g, k: ([[r[0][0], r[0][1], "c" * 64, r[0][3]], r[1]], g, k)),
    ("a round fewer", lambda r, g, k: ([[r[0][0], r[0][1], r[0][2], 19], r[1]], g, k)),
    ("a new match", lambda r, g, k: ([*r, ["m3", "p.c11.f1.a1", "d" * 64, 22]], g, k)),
    ("a deleted match", lambda r, g, k: (r[:1], g, k)),
    ("a re-drawn walk mask", lambda r, g, k: (r, {**g, "walk": "x" * 12}, k)),
    ("a re-drawn sight mask", lambda r, g, k: (r, {**g, "sight": "x" * 12}, k)),
    ("a cover mask added", lambda r, g, k: (r, {**g, "cover": "c" * 12}, k)),
    ("another check set", lambda r, g, k: (r, g, "z" * 12)),
    ("no check set", lambda r, g, k: (r, g, None)),
])
def test_every_input_moves_the_digest(name, change):
    base = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    assert hi.digest(hi.manifest("Sunset", *change(REPLAYS, GEOMETRY, "k" * 12))) != hi.digest(base), name


def test_a_re_drawn_cover_mask_moves_the_digest():
    covered = {**GEOMETRY, "cover": "c" * 12}
    base = hi.digest(hi.manifest("Sunset", REPLAYS, covered, "k" * 12))
    assert hi.digest(hi.manifest("Sunset", REPLAYS, {**covered, "cover": "d" * 12}, "k" * 12)) != base


def test_the_rule_revisions_are_inputs_too(monkeypatch):
    base = hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12))
    monkeypatch.setattr(cf, "HEIGHT_RULES_REVISION", cf.HEIGHT_RULES_REVISION + 1)
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) != base
    monkeypatch.undo()
    monkeypatch.setattr(cf, "HEIGHT_VERSION", cf.HEIGHT_VERSION + 1)
    assert hi.digest(hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)) != base


def test_changes_tell_added_matches_from_evidence_that_is_gone():
    old = hi.manifest("Sunset", REPLAYS, GEOMETRY, "k" * 12)
    more = hi.manifest("Sunset", [*REPLAYS, ["m3", "p", "d" * 64, 22]], GEOMETRY, "k" * 12)
    assert hi.changes(old, more) == {"added": ["m3"], "gone": [], "other": False}
    fewer = hi.manifest("Sunset", REPLAYS[:1], GEOMETRY, "k" * 12)
    assert hi.changes(old, fewer) == {"added": [], "gone": ["m1"], "other": False}
    recondensed = hi.manifest("Sunset", [["m1", "p.c12", "a" * 64, 24], REPLAYS[0]], GEOMETRY, "k" * 12)
    assert hi.changes(old, recondensed) == {"added": [], "gone": ["m1"], "other": False}, "replaced counts as gone"
    remasked = hi.manifest("Sunset", REPLAYS, {**GEOMETRY, "walk": "x" * 12}, "k" * 12)
    assert hi.changes(old, remasked) == {"added": [], "gone": [], "other": True}
    assert hi.changes(None, old) == {"added": ["m1", "m2"], "gone": [], "other": True}, "a row from before manifests"


def test_a_missing_or_broken_check_set_is_an_error_and_an_empty_one_is_not(tmp_path):
    with pytest.raises(hi.CheckSetError, match="missing"):
        hi.must_block_set(tmp_path / "none.json")
    assert hi.must_block_sha(tmp_path / "none.json") is None
    for text in ("not json", "[]", '{"lines": 3}', '{"other": []}'):
        (tmp_path / "bad.json").write_text(text, encoding="utf-8")
        with pytest.raises(hi.CheckSetError):
            hi.must_block_set(tmp_path / "bad.json")
    (tmp_path / "empty.json").write_text('{"lines": []}', encoding="utf-8")
    lines, sha = hi.must_block_set(tmp_path / "empty.json")
    assert lines == [] and sha == hashlib.sha256(b'{"lines": []}').hexdigest()[:12]
    assert hi.must_block_sha(tmp_path / "empty.json") == sha


def test_the_committed_check_set_is_readable_and_the_geometry_identity_matches_the_index():
    lines, sha = hi.must_block_set()
    assert isinstance(lines, list) and len(sha) == 12
    index = json.loads((hi.CONTROL_DIR / "index.json").read_text(encoding="utf-8"))["maps"]
    name = sorted(index)[0]
    got = hi.geometry_identity(name)
    assert got["sight"] == index[name]["sight_sha"] and got["walk"] == index[name]["walk_sha"] and got["scale"]
    assert hi.geometry_identity("NoSuchMap") is None
