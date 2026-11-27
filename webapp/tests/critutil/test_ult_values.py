"""Each ult's own lift window: its active time, plus a per-ult tail from abilities.json `ult_window`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.critutil import abilities, ult_values, windows

PINNED_EXTRACTOR_HASH = "a397dd02ece492660b985e58d1469c6c1295c7a8e1b513e82262c5aaaa5206f5"
WEBAPP_ROOT = Path(__file__).resolve().parents[2]


def _entry(agent, ability):
    return {(e["agent"], e["ability"]): e for e in abilities.load()}[(agent, ability)]


def _fixed(n, window):
    return {"agent": "X", "ability": "Y", "type": "ult", "rule": f"fixed:{n}", "ult_window": window}


def _cast(t, **kw):
    item = {"type": "ult", "t": t, "signals": {}, "effects": []}
    item.update(kw)
    return item


def _active(monkeypatch, segments):
    monkeypatch.setattr(ult_values.util_values, "item_segments", lambda rd, item, typ=None: [list(s) for s in segments])


def test_a_tail_extends_the_last_segment(monkeypatch):
    _active(monkeypatch, [[10.0, 20.0]])
    segs, mode = ult_values.ult_segments({"t_decided": 100.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 25.0]] and mode == "window"


def test_the_tail_is_cut_at_t_decided(monkeypatch):
    _active(monkeypatch, [[10.0, 20.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 22.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 22.0]]


def test_only_the_last_of_two_segments_grows(monkeypatch):
    _active(monkeypatch, [[10.0, 12.0], [20.0, 25.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 12.0], [20.0, 30.0]]


def test_brimstone_is_ten_seconds_from_the_start():
    entry = _entry("Brimstone", "Orbital Strike")
    item = _cast(10.0, ability_id="Brimstone/Orbital Strike")
    segs, mode = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[10.0, 20.0]] and mode == "window"
    segs, _ = ult_values.ult_segments({"t_decided": 15.0}, item, entry)
    assert segs == [[10.0, 15.0]]


def test_thrash_ends_at_its_latest_effect_plus_two_then_the_tail():
    entry = _entry("Gekko", "Thrash")
    item = _cast(5.0, ability_id="Gekko/Thrash", signals={"moved": 6.0, "seen_end": 12.0},
                 effects=[{"target": 1, "how": "cocooned", "t0": 8.0, "t1": 15.0, "known": True},
                          {"target": 2, "how": "cocooned", "t0": 9.0, "t1": 18.0, "known": True}])
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[6.0, 25.0]]
    segs, _ = ult_values.ult_segments({"t_decided": 22.0}, item, entry)
    assert segs == [[6.0, 22.0]]


def test_thrash_with_no_effect_runs_to_seen_end_plus_eight():
    entry = _entry("Gekko", "Thrash")
    item = _cast(5.0, ability_id="Gekko/Thrash", signals={"moved": 6.0, "seen_end": 12.0})
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[6.0, 25.0]]
    segs, _ = ult_values.ult_segments({"t_decided": 19.0}, item, entry)
    assert segs == [[6.0, 19.0]]


def test_a_round_ult_runs_to_the_round_end(monkeypatch):
    _active(monkeypatch, [[30.0, 40.0]])
    segs, mode = ult_values.ult_segments({"t_decided": 70.0}, _cast(30.0), _fixed(5, "round"))
    assert segs == [[30.0, 70.0]] and mode == "round"


def test_an_ult_window_is_never_capped_at_45_seconds(monkeypatch):
    _active(monkeypatch, [[10.0, 80.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 200.0}, _cast(10.0), _fixed(5, 5))
    assert segs == [[10.0, 85.0]]


def test_the_window_table():
    by = {(e["agent"], e["ability"]): e for e in abilities.load() if e["type"] == "ult"}
    for key, e in by.items():
        w = e.get("ult_window")
        assert w == "round" or (isinstance(w, str) and re.fullmatch(r"total:\d+(\.\d+)?", w)) or (
            isinstance(w, (int, float)) and not isinstance(w, bool) and w >= 0), (key, w)
    assert not [e["ability"] for e in abilities.load() if e["type"] != "ult" and "ult_window" in e]
    expect = {("Brimstone", "Orbital Strike"): "total:10", ("Killjoy", "Lockdown"): 10, ("Cypher", "Neural Theft"): 10,
              ("Breach", "Rolling Thunder"): 6, ("Omen", "From the Shadows"): 0, ("Deadlock", "Annihilation"): 5,
              ("Sova", "Hunter's Fury"): 5, ("Yoru", "Dimensional Drift"): 5}
    for key in ("Jett", "Blade Storm"), ("Reyna", "Empress"), ("Veto", "Evolution"), ("Neon", "Overdrive"), \
            ("Chamber", "Tour De Force"), ("Sage", "Resurrection"), ("Clove", "Not Dead Yet"):
        expect[key] = "round"
    for key, w in expect.items():
        assert by[key]["ult_window"] == w, key
    others = [k for k in by if k not in expect]
    assert all(by[k]["ult_window"] == 5 for k in others)


def test_the_seed_carries_the_same_windows():
    d = WEBAPP_ROOT / "app" / "critutil"
    table = {(e["agent"], e["ability"]): e.get("ult_window") for e in json.loads((d / "abilities.json").read_text(encoding="utf-8"))["abilities"]}
    seed = {(e["agent"], e["ability"]): e.get("ult_window") for e in json.loads((d / "abilities_seed.json").read_text(encoding="utf-8"))["abilities"]}
    assert table == seed


def test_the_extractor_hash_ignores_ult_window():
    assert abilities.extractor_hash() == PINNED_EXTRACTOR_HASH


# --- W4: the ult table, the window's swing and the lift

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.services import util_model, util_values  # noqa: E402

MODEL = util_model.fit([(a, d, False, 10.0, None, i < 20 * a // (a + d)) for a in range(1, 6) for d in range(1, 6)
                        for i in range(20)])


def _kill(t, killer, victim, before, k_side="defense", v_side="attack"):
    return {"t": t, "killer": killer, "victim": victim, "k_side": k_side, "v_side": v_side, "before": before}


def _round(casts=(), kills=(), t_decided=60.0, ults=None):
    return {"sides": {"0": "attack", "1": "attack", "5": "defense", "6": "defense"}, "plant": None, "contact": 5.0,
            "t_decided": t_decided, "kills": list(kills), "casts": list(casts), "heals": [], "unused": [],
            "ults": ults or {}}


def _ult_cast(t, by=1, **kw):
    return {"type": "ult", "by": by, "side": "attack", "t": t, "signals": {}, "effects": [], "kinds": [], **kw}


def _window(monkeypatch, segs, mode="window"):
    monkeypatch.setattr(ult_values, "ult_segments", lambda rd, item, entry: ([list(s) for s in segs], mode))


def test_a_toy_rounds_moment_row_has_152_values_and_end_is_result_minus_p():
    rd = _round(t_decided=4.0)
    for side, won, result in (("attack", "attack", 1.0), ("defense", "attack", 0.0)):
        cells = ult_values.ult_moments(rd, MODEL, won)
        pick = {k: a for k, a in cells.items() if k.startswith(side + "|")}
        assert pick and all(a.shape == (152,) for a in cells.values())
        assert sum(a[0] for a in pick.values()) == 2
        want = sum(result - util_values._p_at(rd, MODEL, side, t) for t in (0.0, 2.0))
        assert sum(a[151] for a in pick.values()) == pytest.approx(want)
    assert ult_values.ult_moments(rd, MODEL, None) == {}


def test_the_moment_row_swings_run_to_150_seconds():
    rd = _round(t_decided=200.0)
    cells = ult_values.ult_moments(rd, MODEL, "attack")
    assert all(a.shape == (152,) for a in cells.values())
    assert sum(a[0] for a in cells.values()) == 200


def test_a_60_second_window_counts_a_kill_at_second_50(monkeypatch):
    rd = _round(kills=[_kill(50.0, 5, 0, [5, 5])])
    times, P = util_values.swing_grid(rd, MODEL, "attack")
    assert abs(P[60] - P[49]) > 0.01, "the kill at 50 s moves P"
    swing, L = ult_values.ult_swing(rd, MODEL, [[0.0, 60.0]], "attack")
    assert L == 60
    assert swing == pytest.approx(float(P[60] - P[0]), abs=1e-6)
    assert ult_values.MAX_ULT_L == 150
    assert util_values.cast_swing_v2(rd, MODEL, {}, [[0.0, 60.0]], "attack")[1] == 45


def test_a_moment_inside_a_tail_is_left_out_for_that_side_only(monkeypatch):
    _window(monkeypatch, [[10.0, 20.0]])
    rd = _round(casts=[_ult_cast(10.0)])
    cells = ult_values.ult_moments(rd, MODEL, "attack")
    n = lambda side: sum(a[0] for k, a in cells.items() if k.startswith(side + "|"))  # noqa: E731
    assert n("attack") == 25 and n("defense") == 30


def _cells(rd, n, end_sum, side="attack", t=10.0):
    keys = util_values.levels(rd, side, t)
    arr = np.zeros(152)
    arr[0], arr[151] = n, end_sum
    return keys, arr


def test_a_round_ult_that_won_from_04_against_a_cell_mean_of_01_lifts_half(monkeypatch):
    _window(monkeypatch, [[10.0, 60.0]], "round")
    monkeypatch.setattr(util_values, "_p_at", lambda rd, m, side, t: 0.4)
    rd = _round(casts=[_ult_cast(10.0)])
    keys, arr = _cells(rd, 40, 4.0)
    table = util_values.expand({keys[0]: arr})
    out = ult_values.ult_lift(rd, MODEL, rd["casts"][0], table, {}, "attack")
    assert out["mode"] == "round" and out["lift"] == pytest.approx(0.5) and out["swing"] == pytest.approx(0.6)
    assert out["base_n"] == 40 and out["level"] == util_values.BASE_LEVELS[0]
    assert ult_values.ult_lift(rd, MODEL, rd["casts"][0], table, {}, "defense")["lift"] == pytest.approx(-0.5)
    assert ult_values.ult_lift(rd, MODEL, rd["casts"][0], table, {}, None)["lift"] is None


def test_the_ults_own_match_is_never_in_its_baseline(monkeypatch):
    _window(monkeypatch, [[10.0, 60.0]], "round")
    monkeypatch.setattr(util_values, "_p_at", lambda rd, m, side, t: 0.4)
    rd = _round(casts=[_ult_cast(10.0)])
    keys, other = _cells(rd, 40, 4.0)
    _, own = _cells(rd, 40, -200.0)
    table = util_values.expand({keys[0]: other + own})
    out = ult_values.ult_lift(rd, MODEL, rd["casts"][0], table, util_values.expand({keys[0]: own}), "attack")
    assert out["lift"] == pytest.approx(0.5) and out["base_n"] == 40


def test_a_thin_fine_cell_falls_back_a_level_and_none_gives_no_lift(monkeypatch):
    _window(monkeypatch, [[10.0, 60.0]], "round")
    monkeypatch.setattr(util_values, "_p_at", lambda rd, m, side, t: 0.4)
    rd = _round(casts=[_ult_cast(10.0)])
    keys, _ = _cells(rd, 0, 0.0)
    thin, wide = np.zeros(152), np.zeros(152)
    thin[0], thin[151] = util_values.BASE_MIN_N - 1, 0.0
    wide[0], wide[151] = 40, 8.0
    table = {keys[0]: thin, keys[1]: wide, keys[2]: wide}
    out = ult_values.ult_lift(rd, MODEL, rd["casts"][0], table, {}, "attack")
    assert out["level"] == util_values.BASE_LEVELS[1] and out["lift"] == pytest.approx(0.4)
    out = ult_values.ult_lift(rd, MODEL, rd["casts"][0], {keys[0]: thin}, {}, "attack")
    assert out["lift"] is None and out["level"] is None


def test_a_window_ult_is_compared_at_its_own_length(monkeypatch):
    _window(monkeypatch, [[10.0, 40.0]])
    rd = _round(casts=[_ult_cast(10.0)])
    keys, _ = _cells(rd, 0, 0.0)
    arr = np.array([40.0] + [float(L) for L in range(1, 151)] + [0.0])
    out = ult_values.ult_lift(rd, MODEL, rd["casts"][0], util_values.expand({keys[0]: arr}), {}, "attack")
    assert out["mode"] == "window" and out["L"] == 30
    assert out["lift"] == pytest.approx(out["swing"] - 30 / 40)


def test_kayo_with_two_ult_casts_is_one_item():
    rd = _round(casts=[_ult_cast(30.0), _ult_cast(10.0), _ult_cast(50.0, by=5, side="defense", after_decided=True),
                       {"type": "flash", "by": 6, "t": 5.0}],
                ults={"1": {"use": {"t": 10.0, "ability_id": "X/Y", "type": "ult", "signals": {}}},
                      "5": {"use": None}, "6": {"use": {"t": 20.0, "ability_id": "A/B", "type": "ult", "signals": {}}},
                      "0": {"use": {"t": None, "type": "ult"}}})
    items = ult_values.use_items(rd)
    assert [(i["by"], i["t"]) for i in items] == [(1, 10.0), (6, 20.0)]
    assert items[1]["side"] == "defense"


def test_a_non_ult_window_is_unchanged():
    entry = {"rule": "fixed:4", "type": "wall"}
    got = windows.window({"type": "wall", "t": 10.0, "signals": {}}, entry, 100.0)
    assert got == {"segments": [[10.0, 14.0]], "rule": "fixed:4", "fallback": False}


# --- W5: charges, the ranking and friend rows


def _u(end=None, cap=None, used=False, t=30.0):
    # the real shape: an until_end agent's unused round still carries a `use` dict, with t None (extract._use)
    return {"cap_seen": cap, "end": end, "t": t, "used": used,
            "use": {"t": t if used else None, "ability_id": "X/Ult", "type": "ult", "signals": {}}}


def _ctx(match="m1", agent="Jett", pid=7, last_round=20):
    return {"match": match, "agents": {"1": agent}, "players": {"1": pid}, "last_round": last_round}


def test_used_reads_the_flag_or_a_use_with_a_time_and_never_an_empty_use_dict():
    f = ult_values._used
    assert f({"used": True, "use": None}) and f({"use": {"t": 3.0}}) and f({"use": {"t": 0.0}})
    assert not f({"use": {"t": None, "ability_id": "X/Ult"}}) and not f({"use": None}) and not f({}) and not f({"used": False})


def test_a_full_jett_with_the_real_use_shape_held_to_the_end_is_wasted_and_has_a_pmf():
    rounds = {1: _round(ults={"1": _u(cap=8, used=True)})}
    for n in range(2, 8):
        rounds[n] = _round(ults={"1": {**_u(end=8), "start": 2, "gained": 1}})
    out = ult_values.charges(rounds, {}, _ctx(last_round=7))
    assert [c[5] for c in out] == [1, 0]
    rounds[7] = _round(ults={"1": {**_u(end=3), "start": 2, "gained": 1}})
    assert ult_values.gain_pmf({"m": {"rounds": rounds, "agents": {"1": "Jett"}}}) == {"Jett": {1: 1.0}}


def test_a_final_round_missing_from_the_util_rows_leaves_the_last_state_uncounted():
    rounds = {1: _round(ults={"1": _u(cap=7, used=True)})}
    for n in range(10, 20):
        rounds[n] = _round(ults={"1": _u(end=7)})
    assert [c[5] for c in ult_values.charges(rounds, {}, _ctx(last_round=20))] == [1]
    assert [c[5] for c in ult_values.charges(rounds, {}, _ctx(last_round=19))] == [1, 0]
    assert [c[5] for c in ult_values.charges(rounds, {}, _ctx(last_round=None))] == [1]


def _ch(agent, pid, match, lift, used=1, cost=7):
    return [agent, pid, match, lift, cost, used]


def test_proven_costs_is_the_most_cap_seen_per_slot():
    rounds = {1: _round(ults={"1": _u(cap=6, used=True)}), 2: _round(ults={"1": _u(cap=8, used=True), "5": _u(end=3)})}
    assert ult_values.proven_costs(rounds) == {"1": 8}


def test_a_use_is_a_charge_with_its_lift_and_a_zero_lift_stays_used():
    rounds = {3: _round(ults={"1": _u(cap=7, used=True)}), 4: _round(ults={"1": _u(cap=7, used=True)})}
    out = ult_values.charges(rounds, {(3, "1"): 0.25, (4, "1"): 0.0}, _ctx())
    assert out == [["Jett", 7, "m1", 0.25, 7, 1], ["Jett", 7, "m1", 0.0, 7, 1]]


def test_an_uncompared_use_has_a_none_lift():
    rounds = {3: _round(ults={"1": _u(cap=7, used=True)})}
    assert ult_values.charges(rounds, {}, _ctx()) == [["Jett", 7, "m1", None, 7, 1]]


def test_a_full_ult_held_to_the_last_round_is_one_wasted_charge():
    rounds = {1: _round(ults={"1": _u(cap=7, used=True)})}
    for n in range(10, 21):
        rounds[n] = _round(ults={"1": _u(end=7)})
    out = ult_values.charges(rounds, {(1, "1"): 0.1}, _ctx())
    assert out == [["Jett", 7, "m1", 0.1, 7, 1], ["Jett", 7, "m1", 0, 7, 0]]


def test_a_full_ult_used_in_round_15_is_not_wasted():
    rounds = {1: _round(ults={"1": _u(cap=7, used=True)})}
    for n in range(10, 21):
        rounds[n] = _round(ults={"1": _u(end=7, used=(n == 15))})
    out = ult_values.charges(rounds, {}, _ctx())
    assert [c[5] for c in out] == [1, 1]


def test_a_slot_that_never_proved_a_cost_has_no_charges():
    rounds = {n: _round(ults={"1": _u(end=7)}) for n in range(1, 5)}
    assert ult_values.charges(rounds, {}, _ctx()) == []


def test_cluster_ci_is_repeatable_and_resamples_whole_matches():
    groups = [[1.0, 1.0, 1.0], [-1.0, -1.0, -1.0]]
    fn = lambda rows: sum(rows) / len(rows)  # noqa: E731
    a = ult_values.cluster_ci(groups, fn, seed=0)
    assert a == ult_values.cluster_ci(groups, fn, seed=0)
    assert a[0] == -1.0 and a[1] == 1.0   # whole matches move together: only -1, 0 or 1 are possible


def test_cluster_ci_skips_draws_with_no_value():
    assert ult_values.cluster_ci([[None]], lambda rows: None) == (None, None)


def test_aggregate_counts_uses_uncompared_and_wasted():
    ch = [_ch("A", 1, "m", 0.3), _ch("A", 1, "m", 0.1), _ch("A", 1, "m", 0.0), _ch("A", 1, "m", None),
          _ch("A", 1, "m", 0, used=0)]
    agg = ult_values.aggregate(ch)["A"]
    assert (agg["n_uses"], agg["n_uncompared"], agg["wasted"]) == (4, 1, 1)
    assert agg["n_charges"] == 4
    assert agg["value"] == pytest.approx(0.4 / 4)


def _many(agent, n, lift, match="m"):
    return [_ch(agent, 1, f"{match}{i}", lift) for i in range(n)]


def test_ranking_is_best_first_and_small_agents_do_not_move_the_cast_mean():
    ch = _many("A", 6, 0.2) + _many("B", 6, 0.4) + _many("C", 4, 0.9)
    out = ult_values.rank(ch, agents=["A", "B", "C", "D"])
    rows = {r["agent"]: r for r in out["rows"]}
    assert [r["agent"] for r in out["rows"]][:2] == ["B", "A"]
    assert out["cast_mean"] == pytest.approx(0.3)
    assert rows["B"]["diff"] == pytest.approx(0.1)
    assert rows["C"]["n_uses"] == 4 and rows["C"]["value"] is None and rows["C"]["lo"] is None
    assert rows["D"]["n_uses"] == 0 and rows["D"]["value"] is None
    assert {r["agent"] for r in out["rows"][2:]} == {"C", "D"}


def test_six_uncompared_uses_keep_counts_and_have_no_figures():
    ch = _many("A", 6, None) + _many("B", 6, 0.2)
    out = ult_values.rank(ch, agents=["A", "B"])
    a = next(r for r in out["rows"] if r["agent"] == "A")
    assert a["n_uses"] == 6 and a["n_uncompared"] == 6
    assert a["value"] is None and a["lo"] is None and a["hi"] is None and a["diff"] is None
    assert out["cast_mean"] == pytest.approx(0.2)
    fr = ult_values.friend_rows(ch[:6], [1])
    assert fr[0]["n_uses"] == 6 and fr[0]["value"] is None


def test_no_qualifying_agent_leaves_cast_mean_none():
    out = ult_values.rank(_many("A", 3, 0.2), agents=["A"])
    assert out["cast_mean"] is None


def test_rank_defaults_to_every_ult_agent_in_the_abilities_table():
    out = ult_values.rank([])
    ults = {e["agent"] for e in abilities.load() if e.get("type") == "ult"}
    assert {r["agent"] for r in out["rows"]} == ults


def test_friend_rows_only_for_the_circle_and_shrunk_toward_the_agent():
    ch = ([_ch("A", 1, f"m{i}", 0.5) for i in range(5)] + [_ch("A", 2, f"n{i}", 0.1) for i in range(5)]
          + [_ch("A", 3, f"o{i}", 0.9) for i in range(4)] + [_ch("A", 9, "z", 0.2) for _ in range(6)])
    rows = ult_values.friend_rows(ch, [1, 3])
    assert {r["player_id"] for r in rows} == {1, 3}
    agent_v = ult_values.aggregate(ch)["A"]["value"]
    r1 = next(r for r in rows if r["player_id"] == 1)
    assert r1["value"] == pytest.approx((5 * 0.5 + 5 * agent_v) / 10)
    assert r1["n_uses"] == 5 and r1["lo"] is not None
    r3 = next(r for r in rows if r["player_id"] == 3)
    assert r3["n_uses"] == 4 and r3["value"] is None and r3["lo"] is None


# --- W6: the orb chart


def test_extra_ults_with_a_constant_gain_of_one_and_cost_two():
    pmf = {1: 1.0}
    # E(l, p): p>=2 -> 1 + E(l-1, 1); else E(l-1, min(p+1, 2)). Extra = E(l, p+1) - E(l, p).
    assert [ult_values.extra_ults(pmf, 2, 0, left) for left in (1, 2, 3, 4)] == [0.0, 1.0, 0.0, 1.0]
    assert [ult_values.extra_ults(pmf, 2, 1, left) for left in (1, 2, 3, 4)] == [1.0, 0.0, 1.0, 0.0]


def test_an_ult_that_fills_after_the_last_round_counts_nothing():
    assert ult_values.extra_ults({1: 1.0}, 2, 0, 1) == 0.0


def _match_ctx(n):
    return {"rounds": {i: {"winner": "attack", "id": i} for i in range(1, n + 1)}}


def test_rounds_left_on_a_two_round_match():
    assert ult_values.rounds_left({"a": _match_ctx(2)}) == {1: 0.5, 0: 0.5}


def test_rounds_left_counts_the_context_rounds_not_the_util_rows():
    out = ult_values.rounds_left({"a": _match_ctx(20)})
    assert sorted(out) == list(range(20))
    assert out[0] == pytest.approx(1 / 20)


def test_a_cells_mean_over_rounds_left_by_hand():
    dist = ult_values.rounds_left({"a": _match_ctx(2)})
    mean = sum(s * ult_values.extra_ults({1: 1.0}, 2, 1, left) for left, s in dist.items())
    assert mean == pytest.approx(0.5)


def test_the_recursion_matches_a_seeded_simulation():
    pmf = {0: 0.3, 1: 0.4, 3: 0.2, 5: 0.1}
    cost, left, p0 = 6, 9, 2
    rng = np.random.default_rng(3)
    gs, ws = list(pmf), list(pmf.values())

    def run(p, draws):
        points, n = p, 0
        for g in draws:
            if points >= cost:
                n += 1
                points = g
            else:
                points = min(points + g, cost)
        return n

    N = 40000
    total = 0
    for _ in range(N):
        draws = [int(x) for x in rng.choice(gs, size=left, p=ws)]
        total += run(p0 + 1, draws) - run(p0, draws)
    assert ult_values.extra_ults(pmf, cost, p0, left) == pytest.approx(total / N, abs=0.01)


def test_gain_pmf_uses_rounds_that_started_below_cost_without_a_use():
    def u(start, gained, used=False):
        return {"cap_seen": 7, "start": start, "gained": gained, "used": used, "use": {"t": None, "ability_id": "X/Ult"}}

    rounds = {1: _round(ults={"1": u(2, 1)}), 2: _round(ults={"1": u(3, 3)}), 3: _round(ults={"1": u(7, 2, True)}),
              4: _round(ults={"1": u(None, 2)}), 5: _round(ults={"1": u(4, 1)})}
    out = ult_values.gain_pmf({"m": {"rounds": rounds, "agents": {"1": "Jett"}}})
    assert out == {"Jett": {1: pytest.approx(2 / 3), 3: pytest.approx(1 / 3)}}


def _rk(agent, value, lo, hi, n=10):
    return {"agent": agent, "n_uses": n, "value": value, "lo": lo, "hi": hi}


def test_phoenix_two_points_beats_omen_six_when_eight_rounds_are_left():
    pmf = {0: 0.3, 1: 0.5, 2: 0.2}
    ranked = {"rows": [_rk("Phoenix", 0.07, 0.02, 0.1), _rk("Omen", 0.01, 0.0, 0.02)]}
    out = ult_values.orb_chart(ranked, {"Phoenix": pmf, "Omen": pmf}, {"Phoenix": 7, "Omen": 7}, {8: 1.0})
    rows = {r["agent"]: r for r in out["rows"]}
    assert out["max_cost"] == 7
    assert [r["agent"] for r in out["rows"]] == ["Phoenix", "Omen"]
    assert len(rows["Phoenix"]["cells"]) == 7
    assert rows["Phoenix"]["cells"][2] > rows["Omen"]["cells"][6]


def test_omen_wins_where_its_extra_ults_are_far_higher():
    ranked = [_rk("Phoenix", 0.07, 0.02, 0.1), _rk("Omen", 0.01, 0.0, 0.02)]
    pmfs = {"Phoenix": {0: 1.0}, "Omen": {1: 1.0}}
    out = ult_values.orb_chart(ranked, pmfs, {"Phoenix": 7, "Omen": 2}, {5: 1.0})
    rows = {r["agent"]: r for r in out["rows"]}
    assert rows["Phoenix"]["cells"][2] == 0.0
    assert rows["Omen"]["cells"][1] > rows["Phoenix"]["cells"][2]


def test_negative_value_negative_cells_muted_when_ci_spans_zero_and_thin_agents_dropped():
    ranked = {"rows": [_rk("A", -0.05, -0.1, -0.01), _rk("B", 0.03, -0.01, 0.07), _rk("C", 0.2, 0.1, 0.3, n=4),
                       _rk("D", None, None, None)]}
    pmf = {1: 1.0}
    out = ult_values.orb_chart(ranked, {a: pmf for a in "ABCD"}, {a: 2 for a in "ABCD"}, {5: 1.0})
    assert [r["agent"] for r in out["rows"]] == ["A", "B"]
    a, b = out["rows"]
    assert min(a["cells"]) < 0 and not a["muted"]
    assert b["muted"]


# --- W7: held ults and orb risk

_RED = ["0", "1", "2", "3", "4"]
_BLUE = ["5", "6", "7", "8", "9"]
_NON_PISTOL = [n for n in range(2, 26) if n not in (12, 13)][:20]


def _held_round(red_ready, blue_ready, red_won, n, red_used=0, blue_used=0, drop_start=None):
    sides = {s: ("attack" if n % 2 else "defense") for s in _RED} | {s: ("defense" if n % 2 else "attack") for s in _BLUE}
    ults = {}
    for team, ready, used in ((_RED, red_ready, red_used), (_BLUE, blue_ready, blue_used)):
        for i, s in enumerate(team):
            r = i < ready
            ults[s] = {"cap_seen": 8, "start": 8 if r else 0, "used": r and i < used,
                       "use": {"t": None, "ability_id": "X/Ult", "type": "ult", "signals": {}}}
    if drop_start:
        ults[drop_start].pop("start")
    row = _round(ults=ults)
    row["sides"] = sides
    red_side = sides["0"]
    return row, ("attack" if (red_side == "attack") == red_won else "defense")


def _held_ctx(match, rows_won, loadouts=None):
    players = {s: {"player_id": int(s), "agent": "A", "team": "Red"} for s in _RED}
    players |= {s: {"player_id": int(s), "agent": "A", "team": "Blue"} for s in _BLUE}
    rounds = {n: {"winner": w, "id": n} for n, w in rows_won.items()}
    loadout = {}
    for n in rows_won:
        lo = (loadouts or {}).get(n, (3000, 3000))
        loadout[(n, "Red")], loadout[(n, "Blue")] = lo
    return {"match_id": match, "map": "Ascent", "players": players, "rounds": rounds, "loadout": loadout}


def _sim(seed, n_matches, make):
    """make(rng, match_index, n) -> (red_ready, blue_ready, p_red_wins, loadouts)."""
    rng = np.random.default_rng(seed)
    by, ctx = {}, {}
    for m in range(n_matches):
        rounds, won, loads = {}, {}, {}
        for n in _NON_PISTOL:
            rr, br, p, lo = make(rng, m, n)
            row, winner = _held_round(rr, br, bool(rng.random() < p), n)
            rounds[n], won[n], loads[n] = row, winner, lo
        by[f"m{m}"] = {"rounds": rounds}
        ctx[f"m{m}"] = _held_ctx(f"m{m}", won, loads)
    return by, ctx


def test_each_ready_ult_adding_ten_points_reads_about_ten():
    def make(rng, m, n):
        a, b = int(rng.integers(0, 3)), int(rng.integers(0, 3))
        return a, b, 0.5 + 0.1 * (a - b), (3000, 3000)
    by, ctx = _sim(1, 150, make)
    out = ult_values.held_ults(by, ctx)
    assert abs(out["any"]["pp"] - 10) < 2
    assert out["any"]["lo"] < out["any"]["pp"] < out["any"]["hi"]
    assert out["rounds"] == 150 * 20 and out["matches"] == 150


def test_a_better_team_in_one_match_does_not_move_the_estimate():
    def make(rng, m, n):
        # in even matches red holds more ults and wins 90%; in odd ones blue does; no true ult effect
        if m % 2 == 0:
            return int(rng.integers(1, 3)), int(rng.integers(0, 2)), 0.9, (3000, 3000)
        return int(rng.integers(0, 2)), int(rng.integers(1, 3)), 0.1, (3000, 3000)
    by, ctx = _sim(2, 100, make)
    assert abs(ult_values.held_ults(by, ctx)["any"]["pp"]) < 2


def test_full_buys_that_win_and_hold_more_ults_leave_no_ult_effect():
    def make(rng, m, n):
        rf, bf = rng.random() < 0.5, rng.random() < 0.5
        a = int(rng.integers(0, 3)) + (1 if rf and not bf else 0)
        b = int(rng.integers(0, 3)) + (1 if bf and not rf else 0)
        p = 0.5 + (0.2 if rf and not bf else 0) - (0.2 if bf and not rf else 0)
        return a, b, p, (4500 if rf else 2000, 4500 if bf else 2000)
    by, ctx = _sim(3, 150, make)
    assert abs(ult_values.held_ults(by, ctx)["any"]["pp"]) < 2


def test_pistol_rounds_and_rounds_missing_a_start_are_left_out():
    won, rounds = {}, {}
    for n in (1, 13, 2, 3, 4):
        row, w = _held_round(2, 1, True, n, drop_start="3" if n == 4 else None)
        rounds[n], won[n] = row, w
    out = ult_values.held_ults({"m": {"rounds": rounds}}, {"m": _held_ctx("m", won)})
    assert out["rounds"] == 2


def test_a_round_with_no_loadout_for_a_team_is_left_out():
    row, w = _held_round(2, 1, True, 2)
    c = _held_ctx("m", {2: w})
    del c["loadout"][(2, "Blue")]
    assert ult_values.held_ults({"m": {"rounds": {2: row}}}, {"m": c})["rounds"] == 0


def test_the_used_and_unused_split_has_its_own_estimates():
    rng = np.random.default_rng(5)
    by, ctx = {}, {}
    for m in range(250):
        rounds, won = {}, {}
        for n in _NON_PISTOL:
            ready = int(rng.integers(1, 3))
            used = int(rng.integers(0, ready + 1))
            # only a used ult swings the round: +20 points each
            p = 0.5 + 0.2 * used
            row, w = _held_round(ready, 0, bool(rng.random() < p), n, red_used=used)
            rounds[n], won[n] = row, w
        by[f"m{m}"], ctx[f"m{m}"] = {"rounds": rounds}, _held_ctx(f"m{m}", won)
    out = ult_values.held_ults(by, ctx)
    assert abs(out["used"]["pp"] - 20) < 4 and abs(out["unused"]["pp"]) < 4
    assert out["used"]["lo"] < out["used"]["pp"] < out["used"]["hi"]


def test_no_rounds_gives_none_figures():
    out = ult_values.held_ults({}, {})
    assert out["rounds"] == 0 and out["any"]["pp"] is None


def _orb_row(orbs, kills, t_decided=60.0):
    return _round(kills=kills, t_decided=t_decided) | {"orbs": orbs}


def _risk(monkeypatch, rows, swing=0.2):
    monkeypatch.setattr(util_values, "swings", lambda rd, model: {
        util_values._kill_key(k["t"], k["killer"], k["victim"]): swing for k in rd["kills"]})
    return ult_values.orb_risk({"m": {"rounds": dict(enumerate(rows, 1))}}, {"m": {"map": "Ascent"}}, None)


def _let_go(t, t1, slot="0"):
    return {"t0": t - 1, "t1": None, "end": None, "slot": slot, "tries": [[t, t1, slot]]}


def test_a_death_2_9_s_after_a_capture_counts_and_3_1_s_does_not(monkeypatch):
    cap = {"t0": 5.0, "t1": 20.0, "end": 40.0, "slot": "0", "tries": []}
    hit = _risk(monkeypatch, [_orb_row([cap], [_kill(22.9, 5, 0, [5, 5])])])
    assert hit["overall"]["deaths"] == 1 and hit["overall"]["tries"] == 1
    miss = _risk(monkeypatch, [_orb_row([cap], [_kill(23.1, 5, 0, [5, 5])])])
    assert miss["overall"]["deaths"] == 0 and miss["overall"]["cost_pp"] == 0


def test_a_death_just_before_the_capture_ends_counts_from_two_seconds_earlier(monkeypatch):
    cap = {"t0": 5.0, "t1": 20.0, "end": 40.0, "slot": "0", "tries": []}
    assert _risk(monkeypatch, [_orb_row([cap], [_kill(18.1, 5, 0, [5, 5])])])["overall"]["deaths"] == 1
    assert _risk(monkeypatch, [_orb_row([cap], [_kill(17.9, 5, 0, [5, 5])])])["overall"]["deaths"] == 0


def test_a_let_go_with_no_t1_runs_to_t_decided(monkeypatch):
    orb = _let_go(10.0, None)
    row = _orb_row([orb], [_kill(35.0, 5, 0, [5, 5])], t_decided=40.0)
    assert _risk(monkeypatch, [row])["overall"]["deaths"] == 1


def test_a_death_after_t_decided_does_not_count(monkeypatch):
    orb = _let_go(10.0, None)
    row = _orb_row([orb], [_kill(41.0, 5, 0, [5, 5])], t_decided=40.0)
    assert _risk(monkeypatch, [row])["overall"]["deaths"] == 0


def test_a_killer_swing_of_plus_point_two_costs_minus_point_two_and_the_sign_is_kept(monkeypatch):
    orb = _let_go(10.0, 12.0)
    row = _orb_row([orb], [_kill(11.0, 5, 0, [5, 5])])
    assert _risk(monkeypatch, [row], swing=0.2)["overall"]["cost_pp"] == pytest.approx(-20.0)
    assert _risk(monkeypatch, [row], swing=-0.1)["overall"]["cost_pp"] == pytest.approx(10.0)


def test_two_deaths_cannot_count_on_one_try_and_a_teammates_death_does_not(monkeypatch):
    orb = _let_go(10.0, 20.0)
    two = _orb_row([orb], [_kill(11.0, 5, 0, [5, 5]), _kill(12.0, 6, 0, [4, 5])])
    out = _risk(monkeypatch, [two])
    assert out["overall"]["deaths"] == 1 and out["overall"]["cost_pp"] == pytest.approx(-20.0)
    mate = _orb_row([orb], [_kill(11.0, 5, 1, [5, 5])])
    assert _risk(monkeypatch, [mate])["overall"]["deaths"] == 0


def test_a_let_go_then_a_capture_and_one_death_count_one_death(monkeypatch):
    orb = {"t0": 5.0, "t1": 13.0, "end": 40.0, "slot": "0", "tries": [[10.0, 10.5, "0"]]}
    out = _risk(monkeypatch, [_orb_row([orb], [_kill(13.2, 5, 0, [5, 5])])])
    assert out["overall"]["tries"] == 2 and out["overall"]["deaths"] == 1
    assert out["overall"]["cost_pp"] == pytest.approx(-10.0)      # -20 once, over two tries


def test_two_let_go_tries_close_together_and_one_death_count_one_death(monkeypatch):
    orb = {"t0": 5.0, "t1": None, "end": None, "slot": "0", "tries": [[10.0, 10.5, "0"], [11.0, 11.5, "0"]]}
    out = _risk(monkeypatch, [_orb_row([orb], [_kill(12.0, 5, 0, [5, 5])])])
    assert out["overall"]["tries"] == 2 and out["overall"]["deaths"] == 1
    assert out["overall"]["cost_pp"] == pytest.approx(-10.0)


def test_two_orbs_close_in_time_and_one_death_count_one_death(monkeypatch):
    a = {"t0": 5.0, "t1": 20.0, "end": 40.0, "slot": "0", "tries": []}
    b = {"t0": 6.0, "t1": 21.0, "end": 40.0, "slot": "0", "tries": []}
    out = _risk(monkeypatch, [_orb_row([b, a], [_kill(22.0, 5, 0, [5, 5])])])
    assert out["overall"]["tries"] == 2 and out["overall"]["deaths"] == 1


def test_two_deaths_by_one_player_go_to_two_tries_in_time_order(monkeypatch):
    orb = {"t0": 5.0, "t1": None, "end": None, "slot": "0", "tries": [[10.0, 10.5, "0"], [11.0, 11.5, "0"]]}
    kills = [_kill(10.2, 5, 0, [5, 5]), _kill(11.2, 6, 0, [4, 5])]
    assert _risk(monkeypatch, [_orb_row([orb], kills)])["overall"]["deaths"] == 2


def test_the_cost_is_the_mean_per_try_and_maps_are_split(monkeypatch):
    died, safe = _let_go(10.0, 12.0), _let_go(30.0, 32.0)
    row = _orb_row([died, safe], [_kill(11.0, 5, 0, [5, 5])])
    out = _risk(monkeypatch, [row])
    assert out["overall"]["tries"] == 2 and out["overall"]["cost_pp"] == pytest.approx(-10.0)
    assert [m["map"] for m in out["maps"]] == ["Ascent"] and out["maps"][0]["tries"] == 2


# --- build: the whole block, on toy rounds and on a synthetic corpus

BLOCK = {"ranking", "charges", "orbs", "held", "risk"}
# slot 1 (Jett): round -> (start, gained, end, used). Jett uses in rounds 3 and 6 only.
_JETT = {1: (2, 3, 5, False), 2: (5, 3, 8, False), 3: (8, 2, 2, True), 4: (2, 3, 5, False), 5: (5, 3, 8, False),
         6: (8, 2, 2, True), 7: (2, 3, 5, False)}
# slot 5 (Sage): full from round 3 and never used, so one wasted charge at the match's last round
_SAGE = {1: (3, 0, 3), 2: (3, 0, 3), 3: (3, 5, 8), 4: (8, 0, 8), 5: (8, 0, 8), 6: (8, 0, 8), 7: (8, 0, 8)}
_N_MATCHES = 8


def ult_fixture():
    """(stored {rid: {n: row}}, ctx): 8 matches of 7 rounds with compared ult uses (Jett), a wasted charge (Sage),
    charging gains, non-pistol rounds where the blue team holds a ready ult, and an orb capture that ends in a death."""
    stored, ctx = {}, {}
    for m in range(1, _N_MATCHES + 1):
        rounds, c_rounds, loadout = {}, {}, {}
        for n in range(1, 8):
            js, jg, je, ju = _JETT[n]
            ss, sg, se = _SAGE[n]
            casts = [_ult_cast(10.0, by=1, signals={"seen_end": 15.0})] if ju else []
            # Jett is an until_end agent: its `use` is a dict in every round, t None when unused (extract._use)
            no_use = {"t": None, "ability_id": "Jett/Ult", "type": "ult", "signals": {}}
            ults = {"0": {"cap_seen": 6, "start": 2, "gained": 1, "end": 3, "used": False, "use": None},
                    "1": {"cap_seen": 8 if ju else None, "start": js, "gained": jg, "end": je, "used": ju,
                          "use": {**no_use, "t": 10.0} if ju else dict(no_use)},
                    "5": {"cap_seen": 8, "start": ss, "gained": sg, "end": se, "used": False, "use": None},
                    "6": {"cap_seen": 7, "start": 1, "gained": 1, "end": 2, "used": False, "use": None}}
            row = _round(casts=casts, kills=[_kill(21.0, 5, 0, [5, 5])], ults=ults)
            row["orbs"] = [{"t0": 5.0, "t1": 20.0, "end": 40.0, "slot": "0", "tries": []}]
            rounds[n] = row
            c_rounds[n] = {"winner": "attack" if (m + n) % 3 else "defense", "id": m * 100 + n}
            loadout[(n, "Red")], loadout[(n, "Blue")] = 3000.0 + 200 * (n % 3), 4300.0 - 100 * n
        stored[m] = rounds
        ctx[m] = {"map": "Ascent", "uuid": f"u{m}", "match_id": m, "offset": 0.0, "on_known": True,
                  "players": {0: {"player_id": 100, "agent": "Omen", "team": "Red"},
                              1: {"player_id": 101, "agent": "Jett", "team": "Red"},
                              5: {"player_id": 200, "agent": "Sage", "team": "Blue"},
                              6: {"player_id": 201, "agent": "Cypher", "team": "Blue"}},
                  "rounds": c_rounds, "loadout": loadout}
    return stored, ctx


def _prepared(stored, ctx):
    from app.critutil import corpus

    return {rid: {n: corpus._round_data(d, ctx[rid]) for n, d in rs.items()} for rid, rs in stored.items()}


def _encodable(block):
    import json

    return json.loads(json.dumps(block, allow_nan=False))


def _toy():
    from tests.critutil.test_build import toy_corpus

    return toy_corpus()


@pytest.mark.parametrize("with_model", [True, False])
def test_build_on_the_toy_corpus_is_json_encodable_with_and_without_a_model(with_model):
    from app.critutil import corpus

    stored, ctx = _toy()
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL if with_model else None, corpus.FULL_BUY_THRESHOLD)
    assert set(out) == BLOCK
    assert out["charges"] == [] and out["orbs"]["rows"] == [] and out["risk"]["overall"]["tries"] == 0
    assert _encodable(out) == out


@pytest.mark.parametrize("with_model", [True, False])
def test_build_on_the_synthetic_corpus_fills_every_part(with_model):
    stored, ctx = ult_fixture()
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL if with_model else None, 4200)
    assert set(out) == BLOCK and _encodable(out) == out
    rows = {r["agent"]: r for r in out["ranking"]["rows"]}
    assert rows["Jett"]["n_uses"] == 2 * _N_MATCHES and rows["Jett"]["n_charges"] > 0
    assert rows["Sage"]["wasted"] == _N_MATCHES and rows["Sage"]["n_uses"] == 0
    if with_model:
        assert rows["Jett"]["value"] is not None
    charges = out["charges"]
    assert all(isinstance(c, list) and len(c) == 6 for c in charges)
    assert sum(1 for c in charges if c[5] == 0) == _N_MATCHES and sum(1 for c in charges if c[5] == 1) == 2 * _N_MATCHES
    assert {c[2] for c in charges} == set(range(1, _N_MATCHES + 1))
    if with_model:
        assert any(c[0] == "Jett" and c[3] is not None for c in charges)
    jett = [r for r in out["orbs"]["rows"] if r["agent"] == "Jett"]
    assert out["orbs"]["max_cost"] == 8 and len(jett) == 1 and jett[0]["cost"] == 8 and len(jett[0]["cells"]) == 8
    assert out["held"]["rounds"] > 0 and out["held"]["matches"] == _N_MATCHES and out["held"]["any"]["pp"] is not None
    assert out["risk"]["overall"]["tries"] == 7 * _N_MATCHES and out["risk"]["overall"]["deaths"] == 7 * _N_MATCHES
    assert [m["map"] for m in out["risk"]["maps"]] == ["Ascent"]


def test_build_counts_a_wasted_charge_only_when_the_matchs_last_round_has_util_rows():
    stored, ctx = ult_fixture()
    for rid in (1, 2, 3):
        del stored[rid][7]                    # the context still knows round 7
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL, 4200)
    rows = {r["agent"]: r for r in out["ranking"]["rows"]}
    assert rows["Sage"]["wasted"] == _N_MATCHES - 3
    assert rows["Jett"]["wasted"] == 0


def test_build_figures_have_at_most_four_places():
    stored, ctx = ult_fixture()
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL, 4200)

    def walk(x):
        if isinstance(x, float):
            assert round(x, 4) == x, x
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(out)


def test_build_costs_are_the_agents_most_common_proven_cost():
    stored, ctx = ult_fixture()
    for rid in (1, 2, 3):                     # three matches where the second Jett proves another cost
        stored[rid][3]["ults"]["1"]["cap_seen"] = 9
        stored[rid][6]["ults"]["1"]["cap_seen"] = 9
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL, 4200)
    assert [r["cost"] for r in out["orbs"]["rows"] if r["agent"] == "Jett"] == [8]
    for rid in range(4, 8):
        stored[rid][3]["ults"]["1"]["cap_seen"] = 9
        stored[rid][6]["ults"]["1"]["cap_seen"] = 9
    out = ult_values.build(_prepared(stored, ctx), ctx, MODEL, 4200)
    assert [r["cost"] for r in out["orbs"]["rows"] if r["agent"] == "Jett"] == [9]


def test_a_match_with_two_replays_counts_once_by_its_lowest_replay():
    stored, ctx = ult_fixture()
    stored[11], ctx[11] = stored[1], {**ctx[1], "uuid": "dup"}      # another replay of match 1
    one = ult_values.build(_prepared(*ult_fixture()), ult_fixture()[1], MODEL, 4200)
    two = ult_values.build(_prepared(stored, ctx), ctx, MODEL, 4200)
    assert two == one


def test_the_build_does_not_change_its_inputs():
    import copy

    stored, ctx = ult_fixture()
    prepared = _prepared(stored, ctx)
    before = copy.deepcopy((prepared, ctx))
    ult_values.build(prepared, ctx, MODEL, 4200)
    assert (prepared, ctx) == before


def test_finish_values_adds_the_block_and_accepts_an_empty_build():
    from app.critutil import corpus

    stored, ctx = ult_fixture()
    got = corpus.finish_values({}, stored, ctx, MODEL)
    assert set(got) == {"ults"}
    assert got["ults"] == ult_values.build(_prepared(stored, ctx), ctx, MODEL, corpus.FULL_BUY_THRESHOLD)
    base = {"rows": [1]}
    assert corpus.finish_values(base, stored, ctx, None) is base and "ults" in base and base["rows"] == [1]
    assert corpus.finish_values({}, {}, {}, None)["ults"]["charges"] == []


def test_the_synthetic_block_is_the_same_through_ensure_values_and_compute(tmp_path, monkeypatch):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.coach import areas
    from app.critutil import corpus
    from app.services import replay_util
    from tests.critutil.test_corpus_codec import AT, parts_for

    stored, ctx = ult_fixture()
    monkeypatch.setattr(corpus, "corpus_from_inputs", lambda *a, **k: {"rows": []})
    monkeypatch.setattr(replay_util, "corpus_from_inputs", lambda *a, **k: {"rows": []})
    # the local path
    monkeypatch.setattr(areas, "AREAS_DIR", tmp_path)
    monkeypatch.setattr(areas, "_git_root", lambda: None)
    monkeypatch.setattr(replay_util, "friend_players", lambda db: set())
    monkeypatch.setattr(replay_util, "current_rows", lambda db, **kw: [])
    monkeypatch.setattr(replay_util, "_values_row", lambda db: None)
    monkeypatch.setattr(replay_util.util_model, "ensure", lambda db: MODEL)
    monkeypatch.setattr(replay_util.util_model, "load", lambda db: MODEL)
    monkeypatch.setattr(replay_util.util_model, "rebuild_lock", lambda *args: nullcontext())
    monkeypatch.setattr(replay_util, "corpus_inputs", lambda db, rows: (stored, ctx, set()))
    saved = []
    monkeypatch.setattr(replay_util, "_store_values", lambda db, key, data: saved.append(data))
    replay_util.ensure_values(SimpleNamespace(expire_all=lambda: None, commit=lambda: None))
    local = json.loads(corpus.encode_output(saved[0]["ults"]))
    # the worker path: encoded, packed, decoded, computed
    rows = [(rid, n, f"fp-{rid}-{n}", AT, data) for rid, rs in stored.items() for n, data in rs.items()]
    parts = parts_for(ctx, set(), rows)
    encoded = corpus.encode_context(ctx, set(), MODEL, rows, set(), False, parts)
    path = tmp_path / "inputs.tar"
    corpus.pack_inputs(encoded, {corpus.row_key(*r[:4]): corpus.encode_round(r[4]) for r in rows}, {}, path)
    worker = json.loads(corpus.encode_output(corpus.compute(corpus.unpack_inputs(path))["values"]["ults"]))
    assert worker == local
    assert local["ranking"]["rows"] and local["orbs"]["rows"] and local["held"]["rounds"] and local["risk"]["overall"]["tries"]
    assert local["charges"]
