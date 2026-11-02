"""Identity witness for the stance work: control output on rounds the stance change must not move, recorded from
the code before it (tests/fixtures/control/stance_identity.json).

Covers a flat toy round ("open" of control_toys.reference_rounds()), a heights toy round (a ledge, a walker
climbing it and an enemy below), and the control input identity: the committed geometry inputs of a map with no
height asset, and the fingerprint over an input envelope WITHOUT the revision, data/summary versions and figures
hash (they are bumped on purpose; this witness must survive that). The revision is normalised by digest_round.

Regenerate (only for a change meant to move these):  python tests/replays/test_stance_identity.py
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))
sys.path.insert(0, str(WEBAPP))

import control_reference as ref  # noqa: E402
from control_toys import blob, box_hall, height_blob, reference_rounds, standing, toy_heights  # noqa: E402

FIXTURE = WEBAPP / "tests" / "fixtures" / "control" / "stance_identity.json"
DROPPED = ("control", "data", "summary", "figures")


def heights_round():
    geo = toy_heights("StanceLedge", [(96, 96, 416, 296)], ground=[((96, 96, 256, 296), 4.0)])
    players = {0: ("A", [(0.0, 150, 204, 0, 4.0), (6.0, 250, 204, 0, 4.0)]),
               1: standing(180, 150, 4.0),
               5: standing(300, 204, 0.0, "B"),
               6: ("B", [(0.0, 400, 120, 180, 0.0), (8.0, 320, 250, 180, 0.0)])}
    return geo, height_blob(players, t_end=10.0)


def box_round():
    """The cover toy (a box mid-hall) with players on both sides of it; nobody's track enters the box."""
    walk = lambda side, x0, y0, x1, y1, yaw: (side, [(0.0, x0, y0, yaw), (10.0, x1, y1, yaw)])  # noqa: E731
    players = {0: walk("A", 120, 150, 300, 150, 0), 1: walk("A", 230, 260, 230, 120, 270),
               5: ("B", [(0.0, 400, 150, 180)]), 6: walk("B", 400, 270, 300, 230, 200)}
    return box_hall(), blob(players, t_end=10.0)


def computed(flat: bool = True, heights: bool = True, box: bool = True) -> dict:
    from app.control import engine as ce
    from app.replays import control_format as cf
    from app.services import replay_control as rcn

    out = {}
    if box:
        bgeo, bblob = box_round()
        out["box_hall_empty"] = ref.digest_round(ce.compute_round(bblob, bgeo, None), bblob)
    if heights:
        hgeo, hblob = heights_round()
        out["heights_ledge"] = ref.digest_round(ce.compute_round(hblob, hgeo, None), hblob)
    if not flat:
        return out
    geo, blob, link = reference_rounds()["open"]
    out["open"] = ref.digest_round(ce.compute_round(blob, geo, link), blob)
    index = rcn._assets()[0]
    flat = sorted(m for m, e in index.items() if not e.get("height_sha") and e.get("sight_sha"))[0]
    inputs = rcn.geometry_inputs(flat, {})
    envelope = cf.input_envelope("recipe", "0" * 64, {"linked": False, "sides": {}, "db_deaths": []}, inputs)
    for k in DROPPED:
        envelope.pop(k)
    out["geometry_inputs"] = {"map": flat, "inputs": inputs, "fingerprint": cf.fingerprint_from_inputs(envelope)}
    return out


@pytest.mark.slow       # ~23 s in a fresh process (the open_hall geometry build)
def test_the_flat_round_and_the_input_identity_match_the_base_code():
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    now = computed(heights=False, box=False)
    assert now["open"] == recorded["open"], "a flat round's control output moved"
    assert now["geometry_inputs"] == recorded["geometry_inputs"], "the control input identity moved"


@pytest.mark.slow       # the toy height asset's visibility build alone is ~40 s
def test_the_heights_round_matches_the_base_code():
    # heights_ledge.data: re-pinned in place for EYE_M 1.05 (was "0e088c75b2f2a880")
    # heights_ledge.summary: re-pinned in place for EYE_M 1.05 (was "7c06afb6c393e12d")
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert computed(flat=False, box=False)["heights_ledge"] == recorded["heights_ledge"], \
        "a heights round's output moved"


@pytest.mark.slow
def test_with_the_previous_eye_the_heights_round_matches_the_old_pins(monkeypatch):
    from app.control import heights as hc

    monkeypatch.setattr(hc, "EYE_M", 1.1)
    assert computed(flat=False, box=False)["heights_ledge"] == {
        "data": "0e088c75b2f2a880", "summary": "7c06afb6c393e12d"}


@pytest.mark.slow       # the toy height asset's visibility build alone is ~40 s
def test_a_cover_round_nobody_enters_matches_the_base_code_and_stance_is_never_called():
    from app.control import stance

    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    before = stance.CALLS
    now = computed()
    assert stance.CALLS == before, "the engine called stance_at while the rule is not in"
    assert now["box_hall_empty"] == recorded["box_hall_empty"], "a cover round nobody enters moved"
    assert now["open"] == recorded["open"] and now["heights_ledge"] == recorded["heights_ledge"]


def test_the_fixture_holds_no_revision():
    assert "CONTROL_REVISION" not in FIXTURE.read_text(encoding="utf-8")


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(computed(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", FIXTURE)
