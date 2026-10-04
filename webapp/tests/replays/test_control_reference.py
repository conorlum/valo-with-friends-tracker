"""A map without a height asset gives the reference engine's bytes
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "No height asset"): the toy rounds
of control_toys.reference_rounds() against the digests recorded before the per-floor work, with only the
revision in the header normalised. Real rounds are compared locally by scripts/control_reference.py.
Re-recorded 2026-10-02 for CONTROL_REVISION 5 (the timing-gaps locating events)."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))

import control_reference as ref  # noqa: E402
from control_toys import reference_rounds  # noqa: E402

from app.control import engine as ce  # noqa: E402

REFERENCE = json.loads(ref.FIXTURE.read_text(encoding="utf-8"))["rounds"]


def test_the_fixture_names_every_reference_round():
    assert set(REFERENCE) == set(reference_rounds())


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_a_flat_round_is_byte_identical_to_the_reference(name):
    geo, blob, link = reference_rounds()[name]
    rc = ce.compute_round(blob, geo, link)
    assert ref.digest_round(rc, blob) == REFERENCE[name], (
        f"the control output of the flat toy round {name!r} changed; a map without heights must not")


def test_the_reference_rounds_exercise_what_they_claim():
    """A reference that nothing reaches proves nothing: each round's own feature shows in its inputs."""
    rounds = reference_rounds()
    geo, blob, link = rounds["barrier"]
    assert geo.barrier is not None and link.db_deaths
    rnd = ce.RoundInputs(rounds["setups"][1], rounds["setups"][0])
    assert {w.kind for w in rnd.watchers} == {"trip", "area", "turret"} and rnd.walls
    rnd = ce.RoundInputs(rounds["pawns"][1], rounds["pawns"][0])
    assert {w.kind for w in rnd.watchers} == {"camera", "drone"}
    rnd = ce.RoundInputs(rounds["midwall"][1], rounds["midwall"][0])
    assert rnd.smokes and rnd.damage_zones and rnd.flashed and rnd.nearsight and rnd.reveals and rnd.plant
    assert rounds["rooms"][0].specials
