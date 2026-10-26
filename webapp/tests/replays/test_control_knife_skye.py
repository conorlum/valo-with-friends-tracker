"""KAY/O's knife and Skye's flash as information (W12; the 2026-10-05 review items 10, 14, 15): zero, all or some
of the living enemies hit; proven complete versus missing or unresolved; Veto; KAY/O's own sight. A partial knife
(2026-10-16, docs/map-control-knife-partial-plan.md) restricts the enemies it hit and excludes the ones it missed,
each with the rim margin."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import utility as ut
from tests.replays.control_toys import blob, open_hall, toy_ability, uv

R_UV = 130          # ~ 1.86 m on the toy map (TOY_SCALE: 0.14 m per px): under the 2 m rim


def players():
    return {0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 400, 280, 0)]), 6: ("B", [(0.0, 390, 120, 0)])}


def knife(hits, t=3.0, r=R_UV, state="completed", complete=True, x=300, y=200):
    row = toy_ability("Grenadier", "E_SuppressionPulse", x, y, 0, t=t - 1.0, t1=t + 14.0, kind="GameObject")
    row["activation"] = {"state": state, "evidence": "pulse_oneshot" if state == "completed" else None,
                         "targets_complete": complete, "diagnostics": [] if complete else ["no_pulse_row"]}
    if state == "completed":
        row["pulse"] = {"t": t, "hits": hits, "r": r}
    return row


def infos(util, deaths=None, veto=None):
    b = blob(players(), t_end=10.0, util=util, deaths=deaths)
    if veto is not None:
        next(p for p in b["players"] if p["slot"] == veto)["agent"] = "Veto"
        b["util"].append({"k": "buff", "t": 1.0, "t1": 8.0, "by": veto, "target": veto, "code": "Pine",
                          "name": "X_Evolution"})      # Evolution's buff row on himself (CONDENSE 23)
    return [(i.slot, i.kind, i.reason) for i in ce.RoundInputs(b, open_hall()).infos]


def test_a_knife_that_hit_nobody_rules_its_radius_out_for_every_living_enemy():
    assert infos([knife([])]) == [(5, "exclude", "knife_zero"), (6, "exclude", "knife_zero")]


def test_a_knife_that_hit_everyone_living_holds_each_inside_it():
    assert infos([knife([5, 6])]) == [(5, "restrict", "knife_all"), (6, "restrict", "knife_all")]


PARTIAL = [(5, "restrict", "knife_hit"), (6, "exclude", "knife_missed")]


def test_a_partial_knife_restricts_who_it_hit_and_excludes_who_it_missed():
    assert infos([knife([5])]) == PARTIAL


def test_the_knife_reasons_are_pinned_and_every_emitted_reason_is_in_them():
    assert ut.KNIFE_REASONS == ("knife_zero", "knife_all", "knife_hit", "knife_missed")
    for hits in ([], [5], [5, 6]):
        assert {r for _, _, r in infos([knife(hits)])} <= set(ut.KNIFE_REASONS)


def test_the_dead_dont_count_at_the_pulse():
    assert infos([knife([5])], deaths={6: 2.5}) == [(5, "restrict", "knife_all")], "B6 died before it pulsed"
    assert infos([knife([5])], deaths={6: 3.5}) == PARTIAL, "B6 was alive at the pulse: some, not all"


def test_partial_ops_need_suppress_and_carry_the_plain_radius_and_the_lists():
    b = blob(players(), t_end=10.0, util=[knife([5], r=600)])
    got = {i.slot: i for i in ce.RoundInputs(b, open_hall()).infos}
    rnd = ce.RoundInputs(b, open_hall())
    plain = 600 * ce.PX / 10000 * rnd.geo.m_per_px
    rim = ut.FIGURES["knife_rim_m"]
    assert rim == 2.0
    assert got[5].radius_m == pytest.approx(plain + rim)
    assert got[6].radius_m == pytest.approx(plain - rim)
    for i in got.values():
        assert i.detail["needs"] == "suppress" and i.detail["hits"] == [5] and i.detail["living"] == [5, 6]
        assert i.detail["r_m"] == pytest.approx(plain)


@pytest.mark.parametrize("hits, reasons", [
    ([], {5: ("exclude", -1), 6: ("exclude", -1)}),             # knife_zero
    ([5, 6], {5: ("restrict", 1), 6: ("restrict", 1)}),          # knife_all
    ([5], {5: ("restrict", 1), 6: ("exclude", -1)}),             # knife_hit / knife_missed
])
@pytest.mark.parametrize("rim", [2.0, 0.0])
def test_every_knife_case_carries_the_rim_and_a_zero_rim_is_the_plain_radius(monkeypatch, hits, reasons, rim):
    monkeypatch.setitem(ut.FIGURES, "knife_rim_m", rim)
    b = blob(players(), t_end=10.0, util=[knife(hits, r=600)])
    rnd = ce.RoundInputs(b, open_hall())
    plain = 600 * ce.PX / 10000 * rnd.geo.m_per_px
    got = {i.slot: i for i in rnd.infos}
    for slot, (kind, sign) in reasons.items():
        assert got[slot].kind == kind
        assert got[slot].radius_m == pytest.approx(plain + sign * rim)


def test_a_small_knife_exclude_radius_never_goes_below_zero():
    b = blob(players(), t_end=10.0, util=[knife([5], r=R_UV)])
    got = {i.slot: i for i in ce.RoundInputs(b, open_hall()).infos}
    assert got[6].radius_m == 0.0
    assert got[5].radius_m > 0.0


def test_allies_hit_are_not_hits():
    assert infos([knife([0])]) == [(5, "exclude", "knife_zero"), (6, "exclude", "knife_zero")]


@pytest.mark.parametrize("state, complete", [("unknown", False), ("completed", False)])
def test_without_proof_of_a_pulse_and_a_full_hit_list_nothing_is_inferred(state, complete):
    assert infos([knife([], state=state, complete=complete)]) == []


def test_a_legacy_knife_row_without_activation_tells_nothing():
    row = toy_ability("Grenadier", "E_SuppressionPulse", 300, 200, 0, t=2.0, t1=16.0, kind="GameObject", fx=[3.0])
    assert infos([row]) == []


def test_an_ulting_veto_keeps_his_region_and_makes_all_impossible():
    assert infos([knife([])], veto=5) == [(6, "exclude", "knife_zero")]
    assert infos([knife([6])], veto=5) == [(6, "restrict", "knife_hit")], "Veto's exclude is dropped by the engine"
    assert infos([knife([5, 6])], veto=5) == [(6, "restrict", "knife_hit")]


def test_the_knife_radius_is_the_replays_and_goes_through_walls():
    b = blob(players(), t_end=10.0, util=[knife([], r=600)])
    rnd = ce.RoundInputs(b, open_hall())
    radius = rnd.infos[0].radius_m
    assert radius == pytest.approx(600 * ce.PX / 10000 * rnd.geo.m_per_px - ut.FIGURES["knife_rim_m"])
    assert rnd.infos[0].mask is None, "a disk, not a line of sight"


def test_a_zero_knife_clears_its_radius_from_the_unknown():
    geo = open_hall()
    b = blob(players(), t_end=10.0, util=[knife([], t=6.0, r=600)])
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = {s: np.isfinite(r) for s, r in unknown.reached["A"].items()}

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    disk = ut.footprint(ce.RoundInputs(b, geo).infos[0], geo)
    for s in (5, 6):
        p = ce.RoundInputs(b, geo).pos(s, 6.0)
        own = geo.node_at(geo.cell_of_px(p[0], p[1]), None)
        assert (out[5.5][s] & disk).sum() > 10
        assert not (out[6.0][s] & disk & (np.arange(geo.n) != own)).any()


def test_a_partial_knife_restricts_the_hit_and_clears_the_missed_in_the_engine():
    geo = open_hall()
    tracks = {0: ("A", [(0.0, 104, 200, 180)]), 5: ("B", [(0.0, 330, 200, 0)]), 6: ("B", [(0.0, 390, 120, 0)])}
    b = blob(tracks, t_end=10.0, util=[knife([5], t=6.0, r=600)])
    out = {}

    class Watch:
        def on_tick(self, tick, unknown):
            out[round(tick.t, 6)] = {s: np.isfinite(r) for s, r in unknown.reached["A"].items()}

    ce.compute_round(b, geo, knowledge=False, observer=Watch())
    infos_ = {i.slot: i for i in ce.RoundInputs(b, geo).infos}
    assert (infos_[5].kind, infos_[6].kind) == ("restrict", "exclude")
    keep, gone = ut.footprint(infos_[5], geo), ut.footprint(infos_[6], geo)
    assert (out[5.5][5] & ~keep).sum() > 10, "before the pulse the hit one could be outside the disk"
    assert not (out[6.0][5] & ~keep).any(), "at the pulse the hit one is inside it"
    own = geo.node_at(geo.cell_of_px(390, 120), None)
    assert gone.sum() > 10 and (out[5.5][6] & gone).sum() > 10
    assert not (out[6.0][6] & gone & (np.arange(geo.n) != own)).any()


def skye(hits, pop=(300, 200), state="completed", complete=True, t=4.0):
    u, v = uv(*pop)
    return {"k": "flash", "t": t - 1.0, "by": 0, "ability": "skye_guiding_light", "targets": sorted({h[0] for h in hits}),
            "hits": hits, "id": 77, "pop": {"t": t, "u": u, "v": v, "src": "flash_source"},
            "activation": {"state": state, "evidence": "skye_flash_source", "targets_complete": complete,
                           "diagnostics": [] if complete else ["unresolved_target"]}}


def test_a_skye_flash_with_no_enemy_hit_clears_its_sight_and_range():
    out = infos([skye([])])
    assert out == [(5, "exclude", "skye_no_cue"), (6, "exclude", "skye_no_cue")]


def test_any_enemy_hit_even_zero_long_means_the_cue_played_and_nothing_changes():
    assert infos([skye([[5, 4.2, 0.0]])]) == []
    assert infos([skye([[6, 4.2, 1.8]])]) == []


def test_a_skye_flash_that_hit_only_allies_still_clears():
    assert infos([skye([[0, 4.2, 1.0]])]) == [(5, "exclude", "skye_no_cue"), (6, "exclude", "skye_no_cue")]


@pytest.mark.parametrize("change", [{"state": "unknown"}, {"complete": False}])
def test_missing_or_unresolved_skye_records_tell_nothing(change):
    assert infos([skye([], **change)]) == []


def test_an_unplaced_pop_tells_nothing():
    row = skye([])
    row["pop"] = {"t": 4.0, "place": "stale"}
    assert infos([row]) == []


def test_a_skye_flash_footprint_is_its_sight_within_its_range():
    b = blob(players(), t_end=10.0, util=[skye([], pop=(300, 200))])
    rnd = ce.RoundInputs(b, open_hall())
    mask = rnd.infos[0].mask
    geo = rnd.geo
    d = np.hypot(geo.centres[:, 0] - 300, geo.centres[:, 1] - 200) * geo.m_per_px
    assert mask.any() and d[mask].max() <= ut.FIGURES["skye_flash_range_m"] + 1e-6


def test_an_ulting_veto_keeps_his_region_on_an_empty_skye_flash():
    assert infos([skye([])], veto=6) == [(5, "exclude", "skye_no_cue")]


def test_a_living_suppressed_kayo_keeps_his_own_sight_and_an_ult_pulse_reveals_nobody():
    geo = open_hall()
    pulse = toy_ability("Grenadier", "X_UltPulse", 120, 200, 0, t=3.0, t1=4.0, kind="GameObject")
    status = {"k": "status", "t": 2.0, "t1": 6.0, "by": 5, "target": 0, "status": "suppressed",
              "code": "Grenadier", "name": "E_SuppressionPulse", "from": "object"}
    b = blob({0: ("A", [(0.0, 120, 200, 0)]), 5: ("B", [(0.0, 400, 280, 0)])}, t_end=10.0, util=[pulse, status])
    rnd = ce.RoundInputs(b, geo)
    assert rnd.infos == []
    tick = ce.Tick(rnd, 3.0)
    assert tick.holders[0].active.sum() > 20, "suppressed, he still sees"
