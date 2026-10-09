"""A map without a height asset gives the reference engine's bytes
(docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4, "No height asset"): the toy rounds
of control_toys.reference_rounds() against the digests recorded before the per-floor work, with only the
revision in the header normalised. Real rounds are compared locally by scripts/control_reference.py.
Re-recorded 2026-10-02 for CONTROL_REVISION 5 (the timing-gaps locating events). Re-recorded 2026-10-04 for
CONTROL_REVISION 6 (D5: contested needs a live claim; an enemy's live view ends remembered ground).
Re-recorded 2026-10-05 for CONTROL_REVISION 7 (the utility-review run, D8): every round's data digest moved with
the header's new `knowledge_events` list and nothing else, except `midwall`, whose reveal now locates the
revealed enemy (its summary moved too). Old and new values: the run's W22-digests.json and its revision card.
Re-recorded 2026-10-09 for CONTROL_REVISION 9 (P07, the blind rule and exact flash and nearsight intervals): only
`midwall` moved, data 21e83b2894f012c6 -> 33a55d33f36e2564, summary 9874c72c92ee818d -> 2ad11a1afe23dae6; the two
midwall tests below prove the move is that rule's and nothing else's. Re-recorded 2026-10-09 again for P07a (the
same revision, unreleased: an enemy's damaging molly holds the unknown): only `midwall`, whose Phoenix molly burns
over 3.0-7.0, moved, data 33a55d33f36e2564 -> 12c79de24d1d2ba6, summary 2ad11a1afe23dae6 -> a5741819229a66d0."""

import json
import sys
from pathlib import Path

import numpy as np
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


# ---------------------------------------------------------------- the midwall move for the blind rule (P07)

# `midwall`'s digests before P07 (CONTROL_REVISION 7's re-record, unchanged through 8 and W4's 9).
MIDWALL_BEFORE_P07 = {"data": "21e83b2894f012c6", "summary": "9874c72c92ee818d"}


def _pre_p07_blinds(self, util):
    """The engine's flash and nearsight reading before P07, verbatim: intervals snapped to the grid and at least
    one grid step long, their ends tick events."""
    for e in util:
        k = e.get("k")
        if k not in ("flash", "nearsight"):
            continue
        if self.evolution:
            e = self._unhit(e)
        target = self.flashed if k == "flash" else self.nearsight
        if "hits" in e:
            for slot, t, dur in e["hits"]:
                if dur is None:
                    dur = (ce.FLASH_FULL_S.get((e.get("ability") or "").split("_")[0], ce.FLASH_DEFAULT_S)
                           if k == "flash" else ce.NEARSIGHT_DEFAULT_S)
                    self.missing[f"{k} hit without a duration (placeholder used)"] += 1
                target[slot].append(ce._span(t, t + dur))
                self.events += [t, t + dur]
            continue
        if k == "flash":
            start = next((a["t"] for a in util if a.get("k") == "ability" and a.get("by") == e.get("by")
                          and (a.get("thrown") or {}).get("t0") == e["t"]), e["t"] + ce.FLASH_FUSE_S)
            dur = ce.FLASH_FULL_S.get((e.get("ability") or "").split("_")[0], ce.FLASH_DEFAULT_S)
        else:
            start, dur = e["t"], ce.NEARSIGHT_S.get(e.get("ability"), ce.NEARSIGHT_DEFAULT_S)
        for s in e.get("targets", []):
            target[s].append(ce._span(start, start + dur))
            self.events += [start, start + dur]
            self.missing["flash hit time and blind duration (placeholder used)" if k == "flash"
                         else "nearsight hit time and duration (placeholder used)"] += 1


def test_midwall_without_the_blind_rule_and_exact_intervals_is_its_old_reference(monkeypatch):
    """The move is P07's and nothing else's: with the old interval reading and the blind rule switched off, the
    engine gives the pre-P07 bytes again."""
    monkeypatch.setattr(ce.RoundInputs, "_blinds", _pre_p07_blinds)
    for rule in ("credited", "contributes", "remembers"):
        monkeypatch.setattr(ce, rule, lambda h: True)
    monkeypatch.setattr(ce, "device_sees", lambda kind, blind: True)
    monkeypatch.setattr(ce.RoundInputs, "_mollies", lambda self, util: None)
    geo, blob, link = reference_rounds()["midwall"]
    assert ref.digest_round(ce.compute_round(blob, geo, link), blob) == MIDWALL_BEFORE_P07
    assert REFERENCE["midwall"] != MIDWALL_BEFORE_P07


def test_midwall_now_has_the_blind_rule_and_exact_intervals():
    """What the new reference holds, worked out from the round's rows by hand: A0's flash hit at 1.0 for 0.8 s blinds
    them over [1.0, 1.8) exactly; B5's nearsight hit at 4.2 with no duration gets the 1.0 s placeholder, [4.2, 5.2).
    Their ends are analytic instants and show at the first frame at or after them (1.8125, 4.25, 5.25), never at the
    nearest one before (4.1875, 5.1875). Over the blind, A0 is alive and credited nothing."""
    geo, blob, link = reference_rounds()["midwall"]
    rc = ce.compute_round(blob, geo, link)
    for t in (1.0, 1.8, 4.2, 5.2):
        assert any(abs(a - t) < 1e-9 for a in rc.analytic)
    frames = set(rc.ticks.round(6).tolist())
    assert {1.0, 1.8125, 4.25, 5.25} <= frames and not {4.1875, 5.1875} & frames
    blind = (rc.ticks >= 1.0) & (rc.ticks < 1.8)
    assert blind.sum() >= 2
    assert np.all(rc.control[blind, 0] == 0.0) and not rc.coverage_masks[blind, 0].any()
    assert not rc.control_masks[blind, 0].any()
    seeing = (rc.ticks < 1.0) | ((rc.ticks >= 1.8125) & (rc.ticks < 3.0))
    assert rc.coverage_masks[seeing, 0].any(axis=1).all(), "before and after the blind, A0 covers ground"
    assert rc.players[0].alive_s == pytest.approx(blob["t_decided"]), "alive from 0 to the decision, blind or not"
    assert REFERENCE["midwall"] == ref.digest_round(rc, blob)


# ---------------------------------------------------------------- the midwall move for the molly rule (P07a)

# `midwall`'s digests after P07 and before P07a (W9's re-record).
MIDWALL_BEFORE_P07A = {"data": "33a55d33f36e2564", "summary": "2ad11a1afe23dae6"}


def test_midwall_without_the_molly_rule_is_its_p07_reference(monkeypatch):
    """The second move is P07a's and nothing else's: with the round's molly read as nothing, the engine gives W9's
    bytes again."""
    monkeypatch.setattr(ce.RoundInputs, "_mollies", lambda self, util: None)
    geo, blob, link = reference_rounds()["midwall"]
    assert ref.digest_round(ce.compute_round(blob, geo, link), blob) == MIDWALL_BEFORE_P07A
    assert REFERENCE["midwall"] != MIDWALL_BEFORE_P07A


def test_midwall_molly_changes_nothing_before_it_burns(monkeypatch):
    """Phoenix's (slot 5, team B) Hot Hands burns over [3.0, 7.0) round (150, 200). It holds B's unknown only, from
    its start: every frame before 3.0 is the same with and without it, and some frame after differs."""
    geo, blob, link = reference_rounds()["midwall"]
    rnd = ce.RoundInputs(blob, geo)
    assert [(s, z.t0, z.t1) for s, z, _ in rnd.mollies] == [("B", 3.0, 7.0)]
    assert rnd.hazard_at("B", 5.0).any() and not rnd.hazard_at("A", 5.0).any()
    rc = ce.compute_round(blob, geo, link)
    monkeypatch.setattr(ce.RoundInputs, "_mollies", lambda self, util: None)
    off = ce.compute_round(blob, geo, link)
    on_early, off_early = rc.ticks < 3.0, off.ticks < 3.0
    assert on_early.sum() >= 2 and np.array_equal(rc.ticks[on_early], off.ticks[off_early])
    assert np.array_equal(rc.control[on_early], off.control[off_early], equal_nan=True)
    assert np.array_equal(rc.control_masks[on_early], off.control_masks[off_early])
    assert ref.digest_round(rc, blob) != ref.digest_round(off, blob), "the molly moves something once it burns"
