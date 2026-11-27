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
    return {"cap_seen": cap, "end": end, "t": t, "used": used,
            "use": {"t": t, "ability_id": "X/Ult", "type": "ult", "signals": {}} if used else None}


def _ctx(match="m1", agent="Jett", pid=7):
    return {"match": match, "agents": {"1": agent}, "players": {"1": pid}}


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
