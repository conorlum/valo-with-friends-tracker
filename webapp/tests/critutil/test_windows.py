"""Each cast's up-time from its table rule (app/critutil/windows.py), on plain dicts."""

from app.critutil import abilities
from app.critutil.windows import window

TD = 100.0


def cast(t=10.0, typ="util", effects=(), **signals):
    s = {"landed": None, "seen_end": None, "pop": None, "hits": [], "effect_ends": [], "on": [], "off": [],
         "armed": None, "ult_drop": None}
    s.update(signals)
    return {"t": t, "type": typ, "effects": list(effects), "signals": s}


def ent(rule, D=None, g=1, **kw):
    return {"rule": rule, "grace_s": g, "duration_s": D, **kw}


def w(item, entry, td=TD, **kw):
    return window(item, entry, td, **kw)


def segs(item, entry, td=TD, **kw):
    return w(item, entry, td, **kw)["segments"]


def eff(t0, t1, known, how="blind", target=5):
    return {"target": target, "how": how, "t0": t0, "t1": t1, "known": known}


def test_duration_runs_for_d_plus_grace_from_landing():
    assert w(cast(10.0, landed=11.0), ent("duration", 8)) == {"segments": [[10.0, 20.0]], "rule": "duration",
                                                              "fallback": False}


def test_duration_is_cut_at_the_last_seen_piece_if_earlier():
    assert segs(cast(10.0, seen_end=14.0), ent("duration", 8)) == [[10.0, 15.0]]
    assert segs(cast(10.0, seen_end=40.0), ent("duration", 8)) == [[10.0, 19.0]]


def test_duration_with_no_d_needs_seen_end():
    assert segs(cast(10.0), ent("duration", None)) == []
    assert segs(cast(10.0, seen_end=14.0), ent("duration", None)) == [[10.0, 15.0]]


def test_no_entry_is_a_duration_rule_with_no_d():
    assert w(cast(10.0, seen_end=12.0), None) == {"segments": [[10.0, 13.0]], "rule": "duration", "fallback": False}
    assert segs(cast(10.0), None) == []


def test_fixed_n():
    assert w(cast(10.0), ent("fixed:3")) == {"segments": [[10.0, 13.0]], "rule": "fixed:3", "fallback": False}


def test_flash_with_pop_and_hits_caps_the_blind_at_four():
    c = cast(10.0, pop=10.5, hits=[[10.6, 12.0, True], [10.6, 16.6, True]])
    assert w(c, ent("flash", 1.5)) == {"segments": [[10.0, 14.5]], "rule": "flash", "fallback": False}
    assert segs(cast(10.0, pop=10.5, hits=[[10.6, 11.6, True]]), ent("flash")) == [[10.0, 11.5]]


def test_flash_with_pop_and_no_hits_is_pop_plus_grace():
    assert segs(cast(10.0, pop=10.5), ent("flash", 1.5)) == [[10.0, 11.5]]


def test_flash_with_no_pop_but_effects_is_each_capped_at_four_without_grace():
    c = cast(10.0, effects=[eff(11.0, 12.0, True, "concuss"), eff(11.0, 20.0, True, "concuss")])
    r = w(c, ent("flash", 1.5))
    assert r["segments"] == [[10.0, 15.0]] and r["fallback"] is True


def test_flash_with_no_pop_and_no_effects_uses_duration_capped_at_seen_end():
    r = w(cast(10.0), ent("flash", 2.0))
    assert r["segments"] == [[10.0, 13.0]] and r["fallback"] is True
    assert segs(cast(10.0, seen_end=10.5), ent("flash", 2.0)) == [[10.0, 11.5]]
    assert segs(cast(10.0), ent("flash", None)) == []


def test_status_known_end_plus_two():
    c = cast(10.0, effects=[eff(11.0, 14.0, True, "slow"), eff(11.0, 13.0, True, "slow")])
    assert segs(c, ent("status", 5)) == [[10.0, 16.0]]


def test_status_unknown_end_is_t0_plus_d_plus_grace_and_damage_is_ignored():
    c = cast(10.0, effects=[eff(11.0, 11.5, False, "slow"), eff(12.0, 30.0, True, "damage")])
    assert segs(c, ent("status", 4)) == [[10.0, 16.0]]
    assert segs(cast(10.0, effects=[eff(11.0, 12.0, False, "slow")]), ent("status", None)) == []


def test_status_with_only_unknown_ends_is_cut_at_seen_end_but_a_known_end_is_not():
    leer = [eff(11.0, 11.5, False, "nearsight")]
    assert segs(cast(10.0, effects=leer, seen_end=12.0), ent("status", 4)) == [[10.0, 13.0]]
    assert segs(cast(10.0, effects=leer, seen_end=40.0), ent("status", 4)) == [[10.0, 16.0]]
    known = [eff(11.0, 15.0, True, "slow")]
    assert segs(cast(10.0, effects=known, seen_end=12.0), ent("status", 4)) == [[10.0, 17.0]]


def test_status_with_no_hit_runs_its_duration_and_is_capped_at_seen_end():
    assert segs(cast(10.0), ent("status", 6)) == [[10.0, 17.0]]
    assert segs(cast(10.0, seen_end=12.0), ent("status", 6)) == [[10.0, 13.0]]
    assert segs(cast(10.0, seen_end=12.0), ent("status", None)) == [[10.0, 13.0]]
    assert segs(cast(10.0), ent("status", None)) == []


def test_placeable_minus_off_spans():
    c = cast(10.0, typ="placeable", armed=11.0, seen_end=40.0, off=[[20.0, 25.0], [30.0, 31.0]])
    assert segs(c, ent("placeable", g=0)) == [[11.0, 20.0], [25.0, 30.0], [31.0, 40.0]]


def test_placeable_without_seen_end_runs_to_decided_and_off_all_life_is_empty():
    assert segs(cast(10.0, armed=11.0), ent("placeable", g=0)) == [[11.0, 100.0]]
    assert segs(cast(10.0, armed=11.0, off=[[0.0, 200.0]]), ent("placeable", g=0)) == []


def test_placeable_armed_in_buy_phase_starts_at_zero():
    assert segs(cast(-20.0, armed=-15.0, seen_end=30.0), ent("placeable", g=0)) == [[0.0, 30.0]]


def trap(t=10.0, trigger=None, **signals):
    c = cast(t, typ="placeable", **signals)
    c["trigger"] = trigger
    return c


def test_placeable_tail_is_the_grace_after_its_end():
    # Interceptor: active time plus 2 s
    assert segs(trap(armed=11.0, seen_end=20.0), ent("placeable", 9, g=2)) == [[11.0, 22.0]]


def test_a_tripped_trap_ends_its_after_trigger_time_past_the_trigger():
    shear = ent("placeable", 6, g=2, after_trigger_s=6)
    assert segs(trap(armed=11.0, seen_end=30.0, trigger=30.0), shear) == [[11.0, 38.0]]    # holds, trips, 6 s wall + 2
    assert segs(trap(armed=11.0, seen_end=50.0), shear) == [[11.0, 52.0]]                 # never tripped: its life + 2
    fakeout = ent("placeable", 10, g=0, after_trigger_s=2)
    assert segs(trap(armed=11.0, seen_end=15.0, trigger=15.0), fakeout) == [[11.0, 17.0]]  # shot: plus its flash
    assert segs(trap(armed=11.0, seen_end=21.0), fakeout) == [[11.0, 21.0]]


def test_from_trigger_counts_only_after_it_went_off():
    nano = ent("placeable", 4, g=2, from_trigger=True, after_trigger_s=4)
    assert segs(trap(armed=1.0, seen_end=40.0, go_off=30.0), nano) == [[30.0, 36.0]]
    assert segs(trap(armed=1.0, seen_end=40.0, trigger=31.0), nano) == [[31.0, 37.0]]     # no swarm row: the hit
    assert segs(trap(armed=1.0, seen_end=40.0, go_off=30.0, trigger=31.0), nano) == [[30.0, 36.0]]
    assert segs(trap(armed=1.0, seen_end=40.0), nano) == []                               # never set off


def test_possessed_is_each_look_through_plus_the_grace():
    cam = ent("possessed", None, g=5)
    looks = [[6.3, 8.6], [15.7, 16.2], [28.8, 29.4]]
    assert segs(cast(5.0, possessed=looks), cam) == [[6.3, 13.6], [15.7, 21.2], [28.8, 34.4]]
    assert segs(cast(5.0, possessed=[[6.3, 8.6], [10.0, 11.0]]), cam) == [[6.3, 16.0]]       # tails that overlap merge
    assert segs(cast(5.0, possessed=[[90.0, None]]), cam) == [[90.0, 100.0]]                 # in it at the end
    assert segs(cast(5.0), cam) == []                                                        # never looked through


def test_travel_is_the_flight_then_the_status():
    para = ent("travel", 2, g=0)
    assert w(cast(10.0, seen_end=11.5), para) == {"segments": [[10.0, 13.5]], "rule": "travel", "fallback": False}
    r = w(cast(10.0), para)
    assert r["segments"] == [[10.0, 12.0]] and r["fallback"] is True


def test_tag_counts_only_a_dart_that_tagged_someone():
    dart = ent("tag", None, g=5)
    tagged = cast(10.0, seen_end=13.0)
    tagged["enemies"] = [6]
    assert segs(tagged, dart) == [[10.0, 18.0]]                                            # out at 13 s, plus 5
    assert segs(cast(10.0, seen_end=10.6), dart) == []                                     # a miss
    no_end = cast(10.0, effects=[{"t0": 12.0, "t1": 13.0, "how": "reveal"}])
    no_end["enemies"] = [6]
    assert segs(no_end, dart) == [[10.0, 18.0]]


def test_on_spans_are_the_stored_spans_plus_the_grace():
    c = cast(10.0, on=[[10.0, 14.0], [18.0, 22.0]])
    assert w(c, ent("on_spans", 10)) == {"segments": [[10.0, 15.0], [18.0, 23.0]], "rule": "on_spans",
                                         "fallback": False}
    assert segs(c, ent("on_spans", 10, g=0)) == [[10.0, 14.0], [18.0, 22.0]]
    assert segs(c, ent("on_spans", 10, g=5)) == [[10.0, 27.0]]       # Viper: 5 s after each span, overlaps merge


def test_viper_smokes_have_a_five_second_tail():
    by = {e["ability"]: e for e in abilities.load() if e["agent"] == "Viper"}
    assert by["Poison Cloud"]["rule"] == by["Toxic Screen"]["rule"] == "on_spans"
    assert by["Poison Cloud"]["grace_s"] == by["Toxic Screen"]["grace_s"] == 5


def test_open_off_and_on_spans_run_to_the_end():
    c = cast(10.0, armed=11.0, seen_end=40.0, off=[[20.0, None]])
    assert segs(c, ent("placeable", g=0)) == [[11.0, 20.0]]
    assert segs(cast(10.0, on=[[12.0, None]]), ent("on_spans", 10)) == [[12.0, 100.0]]


def test_on_spans_empty_depends_on_on_known():
    assert w(cast(10.0), ent("on_spans", 10)) == {"segments": [], "rule": "on_spans", "fallback": False}
    r = w(cast(10.0, landed=11.0), ent("on_spans", 10), on_known=False)
    assert r["segments"] == [[10.0, 22.0]] and r["fallback"] is True


def test_on_spans_with_no_d_and_on_unknown_is_empty_with_fallback():
    r = w(cast(10.0), ent("on_spans", None), on_known=False)
    assert r == {"segments": [], "rule": "on_spans", "fallback": True}


def test_life_runs_to_seen_end_or_falls_back():
    c = cast(10.0, typ="ult", landed=11.0, seen_end=30.0)
    assert w(c, ent("life", 20)) == {"segments": [[11.0, 30.0]], "rule": "life", "fallback": False}
    r = w(cast(10.0, typ="ult", landed=11.0), ent("life", 20))
    assert r["segments"] == [[11.0, 32.0]] and r["fallback"] is True
    assert segs(cast(10.0, typ="ult"), ent("life", None)) == []


def test_an_arc_rose_popped_twice_has_a_window_for_each_pop():
    # Branch review B6, 370 round 1's rose: pops at 17.04 and 38.76, an enemy blinded after each (1.629 s, 1.353 s).
    c = cast(15.46, pop=17.04, hits=[[17.04, 18.67, True], [38.76, 40.11, True]])
    c["rose"] = {"pops": [17.04, 38.76], "ended": "popped", "gone": None, "seen": True}
    assert segs(c, ent("flash", 2.0)) == [[15.46, 18.67], [38.76, 40.11]]
    one = cast(15.46, pop=17.04, hits=[[17.04, 18.67, True]])
    one["rose"] = {"pops": [17.04], "ended": "popped", "gone": None, "seen": True}
    assert segs(one, ent("flash", 2.0)) == [[15.46, 18.67]], "one pop: as before"


def test_life_that_ran_to_the_rounds_end_is_up_to_decided_not_the_fallback():
    # Branch review B3: a Pit up until the round ended has no seen_end (that is kept for an early end), but it did
    # not go missing: it is up to t_decided, not start + D + g.
    r = w(cast(2.0, typ="ult", landed=2.0, to_end=True), ent("life", 8))
    assert r == {"segments": [[2.0, TD]], "rule": "life", "fallback": False}


def test_heal_row_runs_to_death_or_decided():
    assert w({"t": 10.0, "t1": 12.0, "target": 1, "death": 20.0}, None) == {
        "segments": [[10.0, 20.0]], "rule": "heal", "fallback": False}
    assert segs({"t": 10.0, "t1": 12.0, "target": 1, "death": None}, None) == [[10.0, 100.0]]


def test_heal_cast_runs_to_decided():
    assert segs(cast(10.0, typ="heal"), ent("heal")) == [[10.0, 100.0]]


def test_raze_from_ult_drop_or_fallback():
    assert segs(cast(10.0, typ="ult", ult_drop=9.5), ent("ult:raze", 3)) == [[9.5, 11.0]]
    r = w(cast(10.0, typ="ult"), ent("ult:raze", 3))
    assert r["segments"] == [[10.0, 14.0]] and r["fallback"] is True


def test_omen_arrival_and_spike_pickup():
    e = ent("ult:omen", 5)
    assert segs(cast(10.0, typ="ult", arrival=15.0, got_spike=False, spike_pickup=None), e) == [[10.0, 25.0]]
    assert segs(cast(10.0, typ="ult", arrival=15.0, got_spike=True, spike_pickup=17.0), e) == [[10.0, 27.0]]
    r = w(cast(10.0, typ="ult"), e)
    assert r["segments"] == [[10.0, 16.0]] and r["fallback"] is True


def test_thrash_from_move_to_seen_end():
    e = ent("ult:thrash", 6)
    assert segs(cast(10.0, typ="ult", moved=12.0, seen_end=20.0), e) == [[12.0, 21.0]]
    r = w(cast(10.0, typ="ult", moved=12.0), e)
    assert r["segments"] == [[10.0, 17.0]] and r["fallback"] is True


def test_duel_and_capture():
    assert segs(cast(10.0, typ="ult", duel_end=15.0), ent("ult:duel", 8)) == [[10.0, 16.0]]
    assert w(cast(10.0, typ="ult"), ent("ult:duel", 8))["fallback"] is True
    assert segs(cast(10.0, typ="ult", capture_end=18.0), ent("ult:capture", 8)) == [[10.0, 19.0]]
    assert segs(cast(10.0, typ="ult"), ent("ult:capture", None)) == []


def test_seekers_hit_seen_end_or_fallback():
    e = ent("ult:seekers", 5)
    c = cast(10.0, typ="ult", effects=[eff(12.0, 14.0, True, "nearsight"), eff(12.0, 16.0, True, "nearsight")],
             seen_end=30.0)
    assert segs(c, e) == [[10.0, 18.0]]
    assert segs(cast(10.0, typ="ult", seen_end=20.0), e) == [[10.0, 20.0]]
    r = w(cast(10.0, typ="ult"), e)
    assert r["segments"] == [[10.0, 16.0]] and r["fallback"] is True


def test_until_end_is_the_earlier_of_ult_end_and_death():
    e = ent("ult:until_end", None, 0)
    use = {"t": 10.0, "type": "ult", "signals": {"ult_end": 30.0, "death": 25.0}}
    assert segs(use, e) == [[10.0, 25.0]]
    use["signals"] = {"ult_end": 22.0, "death": 25.0}
    assert segs(use, e) == [[10.0, 22.0]]
    use["signals"] = {"ult_end": None, "death": None}
    assert w(use, e) == {"segments": [[10.0, 100.0]], "rule": "ult:until_end", "fallback": False}


def test_until_death_with_no_use_is_empty():
    use = {"t": None, "type": "ult", "signals": {"ult_end": None, "death": 25.0}}
    assert segs(use, ent("ult:until_death", None, 0)) == []


def test_every_window_is_cut_at_t_decided():
    assert segs(cast(10.0, landed=10.0), ent("duration", 8), td=14.0) == [[10.0, 14.0]]
    assert segs(cast(10.0, on=[[10.0, 14.0], [18.0, 22.0]]), ent("on_spans", 10), td=12.0) == [[10.0, 12.0]]
    assert segs(cast(10.0, landed=10.0), ent("duration", 8), td=10.0) == []


def test_buff_runs_to_the_last_buff_end_refreshes_included():
    h = ent("buff", 8, g=0)
    assert segs(cast(10.0, seen_end=18.0), h) == [[10.0, 18.0]]
    assert segs(cast(10.0, seen_end=21.0), h) == [[10.0, 21.0]]                  # a kill refreshed it
    assert segs(cast(10.0), h) == [[10.0, 100.0]]                                # still on at the end


def test_a_sentinels_placed_util_ends_when_its_owner_dies():
    tw = ent("placeable", None, g=1, off_at_death=True)
    assert segs(trap(0.0, armed=0.0, owner_death=32.0), tw) == [[0.0, 32.0]]
    assert segs(trap(0.0, armed=0.0), tw) == [[0.0, 100.0]]                      # lived
    assert segs(trap(0.0, armed=0.0, owner_death=32.0), ent("placeable", None, g=1)) == [[0.0, 100.0]]   # no flag
    assert segs(trap(0.0, armed=40.0, owner_death=32.0), tw) == []               # dead before it armed
    cam = ent("possessed", None, g=5, off_at_death=True)
    assert segs(cast(5.0, possessed=[[6.3, 8.6], [15.7, 16.2]], owner_death=16.0), cam) == [[6.3, 13.6], [15.7, 16.0]]
