"""The game figures the engine consumes are one semantic view (W22): hearing.json's and utility.json's numbers. A
changed number makes control and the gap runs stale; an edited citation or note makes neither. And the checks W22
asks of the integrated engine: the incremental counterfactual against the full one across an ability wall."""

import json

import pytest

from app.control import engine as ce
from app.control import utility as ut
from app.replays import control_format as cf
from app.services import replay_gaps
from tests.replays.control_toys import blob, open_hall, toy_ability, uv

LINK = {"sides": {"0": "attack", "5": "defense"}, "db_deaths": []}
GEOMETRY = {"sight": "s", "walk": "w", "barrier": None, "specials": [], "scale": 7e-5}


def copies(tmp_path, monkeypatch, edit_utility=None, edit_hearing=None):
    """The two figure files copied (and edited) into tmp, with the readers pointed at them."""
    utility = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    hearing = json.loads(cf.HEARING_FILE.read_text(encoding="utf-8"))
    if edit_utility:
        edit_utility(utility)
    if edit_hearing:
        edit_hearing(hearing)
    (tmp_path / "utility.json").write_text(json.dumps(utility), encoding="utf-8")
    (tmp_path / "hearing.json").write_text(json.dumps(hearing), encoding="utf-8")
    monkeypatch.setattr(cf, "UTILITY_FILE", tmp_path / "utility.json")
    monkeypatch.setattr(cf, "HEARING_FILE", tmp_path / "hearing.json")
    monkeypatch.setattr(replay_gaps, "HEARING_FILE", tmp_path / "hearing.json")


def keys():
    control = cf.fingerprint("p1.c13.f1.a0", "0" * 64, LINK, GEOMETRY)
    return {"figures": cf.figures_hash(), "control": control,
            "gaps": replay_gaps.gap_fingerprint(control, "Nomap", chokes=None),
            "engine_key": replay_gaps.engine_key("fixed-control-fingerprint", "Nomap")}


def test_the_view_is_the_numbers_the_engine_reads():
    raw = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    assert cf._numbers(raw) == ut.FIGURES
    assert "sources" in raw and "PROVISIONAL" in raw and len(cf.figures_hash()) == 16


@pytest.mark.parametrize("edit", [
    lambda u: u["reveal_range_m"].update(haunt=31.0),
    lambda u: u.update(skye_flash_range_m=19.5),
    lambda u: u.update(leer_cast_range_m=9.0),
    lambda u: u["hearing_m"].update(waylay_recall=40.0),
])
def test_a_changed_ability_figure_makes_control_and_gaps_stale(tmp_path, monkeypatch, edit):
    copies(tmp_path, monkeypatch)
    before = keys()
    copies(tmp_path, monkeypatch, edit_utility=edit)
    after = keys()
    assert all(before[k] != after[k] for k in before), {k: before[k] == after[k] for k in before}


def test_a_changed_hearing_figure_makes_control_and_gaps_stale(tmp_path, monkeypatch):
    copies(tmp_path, monkeypatch)
    before = keys()
    copies(tmp_path, monkeypatch, edit_hearing=lambda h: h.update(footstep_range_m=45.0))
    after = keys()
    assert all(before[k] != after[k] for k in before)


@pytest.mark.parametrize("edit_utility, edit_hearing", [
    (lambda u: u["sources"].update(reveal_range_m="measured in game, 2026-10-12"), None),
    (lambda u: u.update(PROVISIONAL="tested"), None),
    (None, lambda h: h["sources"].update(guns="a new citation")),
    (lambda u: u.update(note_to_self="a new text key"), None),
])
def test_an_edited_citation_or_note_changes_nothing(tmp_path, monkeypatch, edit_utility, edit_hearing):
    copies(tmp_path, monkeypatch)
    before = keys()
    copies(tmp_path, monkeypatch, edit_utility=edit_utility, edit_hearing=edit_hearing)
    assert keys() == before


def test_a_missing_figures_file_reads_as_empty_not_as_an_error(tmp_path):
    assert len(cf.figures_hash(tmp_path / "none.json", tmp_path / "none2.json")) == 16
    assert cf.figures_hash(tmp_path / "none.json", tmp_path / "none2.json") != cf.figures_hash()


def test_the_revisions_of_this_release():
    from app.gaps import detect
    from app.replays import format as fmt

    assert (fmt.CONDENSE_REVISION, cf.CONTROL_REVISION) == (15, 9)
    assert detect.GAPS_REVISION == replay_gaps.GAPS_REVISION == 2, "the detector's output rules are unchanged"


def test_the_incremental_counterfactual_matches_the_full_one_across_a_wall_and_a_reveal():
    geo = open_hall()
    wall = toy_ability("Thorne", "E_Wall_Fortifying", 250, 196, 5, t=1.0, t1=40.0, kind="GameObject", yaw=0,
                       segments=[[*uv(250, y), 2.0, 6.0 if y == 221 else None] for y in (121, 171, 221, 271)])
    reveal = {"k": "reveal", "t": 4.0, "t1": 6.0, "by": 0, "target": 5, "code": "Gumshoe", "name": "X_InterrogateHat"}
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 1: ("A", [(0.0, 130, 120, 0)]), 5: ("B", [(0.0, 400, 200, 180)]),
              6: ("B", [(0.0, 390, 270, 180)])}, t_end=9.0, util=[wall, reveal])
    rc = ce.compute_round(b, geo, full_every=1, knowledge=False)
    assert rc.cf_check["player_ticks"] > 50
    assert rc.cf_check["identical"] == rc.cf_check["player_ticks"], rc.cf_check
    # nothing is credited twice by publishing: the analytical instants are the frames plus the exact ones
    assert len(rc.analytic) == len(rc.ticks) + rc.timings.get("analytic_only_ticks", 0)
    assert sum(p.alive_s for p in rc.players.values()) == pytest.approx(4 * (b["t_decided"] - 0.0), abs=1e-6)
