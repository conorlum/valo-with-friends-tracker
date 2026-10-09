"""The damaging-molly contract W10 consumes (app/control/mollies.py, utility.json `molly`; the replay
player-state plan amendment F8, decision D6): which zones count lives in code, their figures in the hashed
table, and a zone without a known owner is skipped and counted."""

import json

import pytest

from app.control import engine as ce
from app.control import mollies as mo
from app.control import utility as ut
from app.replays import control_format as cf

FIVE = {"Phoenix_MolotovFire", "Sarge_Q_Molotov_Production", "Pandemic_AcidMolotov_NewMolotov",
        "Killjoy_4_BeeSwarm_Damage", "Aggrobot_C_ExplodeyPatch"}


def _row(code="Phoenix", name="MolotovFire", by=5, t=12.028, t1=19.228, **extra):
    return {"k": "ability", "kind": "Patch", "code": code, "name": name, "by": by, "owner_by": "agent",
            "t": t, "t1": t1, "u": 5480, "v": 1136, "z": 80, **extra}


def test_the_classification_is_the_five_sustained_zones_in_code():
    assert mo.MOLLY_KEYS == frozenset(FIVE)
    assert set(ut.FIGURES["molly"]) == FIVE, "every classified zone has figures, and nothing else does"
    raw = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))["molly"]
    assert all("classif" not in json.dumps(v).lower() for v in raw.values()), "no classification in the JSON"


def test_the_radii_are_the_engines_damage_zone_radii():
    for key in FIVE:
        [r] = [r for pat, r in ce.DAMAGE_ZONES if pat.match(key)]
        assert ut.FIGURES["molly"][key]["radius_m"] == r / 100.0
    assert ut.FIGURES["molly"]["Phoenix_MolotovFire"]["seconds"] == 4.0


def test_a_zone_burns_for_its_seconds_capped_at_the_rows_end():
    figures = ut.FIGURES["molly"]
    zones, diagnostics = mo.molly_zones([_row(), _row(t=30.0625, t1=32.0)], figures)
    assert zones == [(5, 5480, 1136, 4.5, 12.028, 12.028 + 4.0), (5, 5480, 1136, 4.5, 30.0625, 32.0)]
    assert diagnostics == {}


def test_a_row_with_no_end_burns_for_its_seconds():
    zones, _ = mo.molly_zones([_row(t1=None)], ut.FIGURES["molly"])
    assert zones[0][5] == 12.028 + 4.0


def test_other_rows_are_not_mollies():
    rows = [_row(code="Hunter", name="4_ExplosiveBolt_Explosion"), _row(code="Wraith", name="4_Smoke"),
            {"k": "status", "t": 1.0, "t1": 2.0, "target": 1, "status": "slowed"},
            _row(kind="Projectile")]
    assert mo.molly_zones(rows, ut.FIGURES["molly"]) == ([], {})


def test_an_unknown_owner_is_skipped_and_counted():
    zones, diagnostics = mo.molly_zones([_row(by=None), _row(), _row(code="Sarge", name="Q_Molotov_Production",
                                                                   by=None)], ut.FIGURES["molly"])
    assert [z[0] for z in zones] == [5]
    assert diagnostics == {"molly without a known owner": 2}


def test_a_molly_without_figures_or_a_place_is_skipped_and_counted():
    figures = {k: v for k, v in ut.FIGURES["molly"].items() if k != "Phoenix_MolotovFire"}
    zones, diagnostics = mo.molly_zones([_row(), _row(code="Sarge", name="Q_Molotov_Production", u=None)], figures)
    assert zones == []
    assert diagnostics == {"molly without figures": 1, "molly without a place": 1}


def _hash_with(tmp_path, edit):
    utility = json.loads(cf.UTILITY_FILE.read_text(encoding="utf-8"))
    edit(utility)
    path = tmp_path / "utility.json"
    path.write_text(json.dumps(utility), encoding="utf-8")
    return cf.figures_hash(utility=path)


@pytest.mark.parametrize("edit", [
    lambda u: u["molly"]["Phoenix_MolotovFire"].update(seconds=4.5),
    lambda u: u["molly"]["Killjoy_4_BeeSwarm_Damage"].update(radius_m=5.0),
])
def test_a_changed_molly_number_changes_figures_hash(tmp_path, edit):
    assert _hash_with(tmp_path, lambda u: None) != _hash_with(tmp_path, edit)


def test_a_changed_molly_note_does_not(tmp_path):
    def edit(u):
        for v in u["molly"].values():
            v["note"] = "measured in game"
        u["sources"]["molly"] = "tested in game, 2026-10-12"
    assert _hash_with(tmp_path, lambda u: None) == _hash_with(tmp_path, edit)


def test_a_row_that_ends_before_it_burns_or_has_no_time_is_skipped_and_counted():
    zones, diagnostics = mo.molly_zones([_row(t1=12.028), _row(t=float("nan")), _row(t=True)], ut.FIGURES["molly"])
    assert zones == []
    assert diagnostics == {"molly ended before it burned": 1, "molly without a time": 2}


# ---------------------------------------------------------------- per-side hazards (what P07a consumes)

SIDES = {0: "A", 2: "A", 5: "B", 9: "B"}


def _hazards(rows, sides=SIDES, diagnostics=None):
    zones, _ = mo.molly_zones(rows, ut.FIGURES["molly"])
    return mo.Hazards(zones, sides, diagnostics)


def test_a_molly_restricts_only_its_owners_sides_unknown():
    h = _hazards([_row(by=5, t=12.626, t1=19.8)])
    assert [z.by for z in h.hazards_at("B", 13.0)] == [5], "unknown[B] (where B thinks A is) is blocked by B's fire"
    assert h.hazards_at("A", 13.0) == [], "a player isn't blocked by their own team's molly"


def test_a_molly_burns_over_its_half_open_interval():
    h = _hazards([_row(by=0, t=12.626, t1=19.8)])
    assert h.hazards_at("A", 12.625) == [], "not before it lands"
    assert len(h.hazards_at("A", 12.626)) == 1
    assert len(h.hazards_at("A", 16.625)) == 1
    assert h.hazards_at("A", 12.626 + 4.0) == [], "end-exclusive"
    assert h.reopen_times("A") == [12.626 + 4.0] and h.reopen_times("B") == []
    assert h.transitions() == [12.626, 12.626 + 4.0]


def test_hazards_are_kept_per_side_in_time_order():
    h = _hazards([_row(by=9, t=40.3125, t1=50.0), _row(by=2, code="Sarge", name="Q_Molotov_Production", t=30.0625,
                                                        t1=45.0), _row(by=5, t=20.0625, t1=21.0)])
    assert [(z.by, z.t0) for z in h.by_side["B"]] == [(5, 20.0625), (9, 40.3125)]
    assert [(z.by, z.t1) for z in h.by_side["A"]] == [(2, 37.0625)], "Incendiary's 7 s"
    assert h.reopen_times("B") == [21.0, 44.3125]


def test_an_owner_without_a_side_is_counted_not_guessed_hostile():
    diagnostics = {}
    h = _hazards([_row(by=7)], diagnostics=diagnostics)
    assert h.by_side == {} and diagnostics == {"molly owner without a side": 1}
