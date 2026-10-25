"""The round-win estimator (app/services/util_model.py) and cast valuation (app/services/util_values.py), pure."""

import pytest

from app.services import util_model, util_values


def model_from(rows):
    return util_model.fit(rows)


def test_estimator_shrinks_a_thin_cell_toward_its_parent():
    rows = [(5, 5, False, 10.0, None, True)] * 50 + [(5, 5, False, 10.0, None, False)] * 50
    rows += [(5, 5, False, 80.0, None, True)]           # one observation late in the round
    m = model_from(rows)
    p_late = util_model.p_attack(m, 5, 5, False, 80.0, None)
    assert 0.5 < p_late < 0.6, "one win in a cell barely moves it off the 5v5 rate"


def test_estimator_terminal_states():
    m = model_from([])
    assert util_model.p_attack(m, 3, 0, False, 30.0, None) == 1.0
    assert util_model.p_attack(m, 0, 3, False, 30.0, None) == 0.0
    assert util_model.p_side(m, "defense", 0, 3, False, 30.0, None) == 1.0


def test_estimator_scores_better_than_man_advantage_alone_when_time_matters():
    # post-plant 1v1s late go to the attackers, early to the defenders
    rows = [(1, 1, True, 100.0, 60.0, True)] * 80 + [(1, 1, True, 62.0, 60.0, False)] * 80
    m = model_from(rows)
    s = util_model.score(m, rows)
    assert s["log_loss"] < s["log_loss_man"]


def round_data(casts, kills, plant=None):
    return {"sides": {"0": "attack", "1": "attack", "5": "defense", "6": "defense"}, "plant": plant,
            "kills": kills, "casts": casts, "heals": [], "unused": []}


def kill(t, killer, victim, before, k_side="attack", v_side="defense"):
    return {"t": t, "killer": killer, "victim": victim, "k_side": k_side, "v_side": v_side, "before": before}


def cast(i, by=1, side="attack", kills=(), **kw):
    return {"i": i, "by": by, "side": side, "t": 8.0, "kinds": kw.pop("kinds", ["blind"]), "kills": list(kills),
            "effects": kw.pop("effects", []), "enemies": kw.pop("enemies", []), "harm": kw.pop("harm", []), **kw}


# attackers win a/(a+d) of the time in an a-v-d state
MODEL = model_from([(a, d, False, 10.0, None, i < 20 * a // (a + d)) for a in range(1, 6) for d in range(1, 6)
                    for i in range(20)])


def test_a_kill_is_split_once_between_casts_and_the_gun():
    k = kill(9.0, 0, 5, [5, 5])
    ev = {"t": 9.0, "killer": 0, "victim": 5, "ours": True}
    casts = [cast(0, kills=[{**ev, "ev": "affected"}]), cast(1, by=0, kills=[{**ev, "ev": "revealed"}])]
    led = util_values.ledger(round_data(casts, [k]), MODEL)
    swing = util_values.swings(round_data(casts, [k]), MODEL)[(9.0, 0, 5)]
    assert swing > 0
    total = led[0]["value"] + led[1]["value"]
    assert total == pytest.approx(swing * (1.0 + 0.7) / (1.0 + 0.7 + 1.0)), "the gun keeps its share"
    assert led[0]["value"] > led[1]["value"], "an effect on the victim outweighs a reveal"


def test_a_teammate_killed_by_an_enemy_we_affected_is_negative():
    k = kill(9.0, 5, 0, [5, 5], k_side="defense", v_side="attack")
    casts = [cast(0, kills=[{"t": 9.0, "killer": 5, "victim": 0, "ours": False, "ev": "failed_follow"}])]
    assert util_values.ledger(round_data(casts, [k]), MODEL)[0]["value"] < 0


def test_teamflash_death_is_negative_and_soft_harm_costs_through_the_weights():
    k = kill(9.0, 5, 0, [5, 5], k_side="defense", v_side="attack")
    died = cast(0, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True, "died_t": 9.0}])
    soft = cast(1, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": False, "died_t": None}])
    v = util_values.value_round(round_data([died, soft], [k]), MODEL)
    assert v["casts"][0]["ledger"] < 0
    assert v["casts"][1]["jobs"] < 0 and v["casts"][1]["ledger"] == 0


def test_a_teamflash_is_charged_with_the_death_it_was_on_not_a_later_life():
    # flashed teammate 0 dies at 9.0, is resurrected, and dies again at 40.0: the flash claims the first death only
    first = kill(9.0, 5, 0, [5, 5], k_side="defense", v_side="attack")
    later = kill(40.0, 6, 0, [4, 5], k_side="defense", v_side="attack")
    died = cast(0, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True, "died_t": 9.0}])
    items = util_values.ledger(round_data([died], [first, later]), MODEL)[0]["items"]
    assert [i["t"] for i in items] == [9.0]
    old = cast(0, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True}])     # no `died_t`
    assert util_values.ledger(round_data([old], [first, later]), MODEL)[0]["items"] == []


def test_job_features_and_waste_last():
    smoke = cast(0, kinds=["smoke"], cover={"blocked_s": 6.0, "pairs": 2, "covered": [0], "covered_lived": [0]})
    empty = cast(1, kinds=["blind"])
    v = util_values.value_round(round_data([smoke, empty], []), MODEL)
    assert v["casts"][0]["jobs"] > 0 and not v["casts"][0]["wasted"]
    assert v["casts"][1]["wasted"] and v["casts"][1]["total"] == 0


def test_a_device_holding_its_lane_is_not_wasted():
    trip = cast(0, kinds=["device", "status"], presence={"armed_s": 40.0, "entered": [], "held_back": [],
                                                         "waiting_s": 0.0})
    assert not util_values.is_wasted(trip, 0.0, util_values.features(trip))
    gone = cast(1, kinds=["device"], presence={"armed_s": 1.0, "entered": [], "held_back": []})
    assert util_values.is_wasted(gone, 0.0, util_values.features(gone))


def test_casts_after_the_round_was_decided_are_never_scored():
    v = util_values.value_round(round_data([cast(0, after_decided=True)], []), MODEL)
    assert v["casts"][0]["scored"] is False


def test_fit_weights_needs_enough_rounds_then_stays_non_negative():
    few = [{"match": 1, "y": 0.1, "x": dict.fromkeys(util_values.DEFAULT_WEIGHTS, 1.0)}]
    assert util_values.fit_weights(few)["fitted"] is False
    import random

    rnd = random.Random(0)
    rows = []
    for i in range(400):
        x = dict.fromkeys(util_values.DEFAULT_WEIGHTS, 0.0)
        x.update(saves=rnd.random() * 3, harm_soft=rnd.random() * 3)
        rows.append({"match": i % 40, "x": x, "y": 0.02 * x["saves"] - 0.03 * x["harm_soft"] + rnd.gauss(0, 0.05)})
    fit = util_values.fit_weights(rows)
    assert fit["fitted"] and all(w >= 0 for w in fit["weights"].values())
    assert fit["weights"]["harm_soft"] > 0, "harm enters negatively, so its weight is a cost"
    assert fit["check"]["mse_fitted"] < fit["check"]["mse_default"]
    # each match is valued with the weights of the fold that held it out, never with weights fitted on it
    assert set(fit["by_match"]) == {str(m) for m in range(40)}
    assert util_values.weights_for(fit, 7) == fit["by_match"]["7"] != fit["weights"]
    assert util_values.weights_for(fit, 999) == fit["weights"], "a match the fit never saw gets the full fit"
    assert util_values.weights_for({"fitted": False, "weights": util_values.DEFAULT_WEIGHTS}, 7) == \
        util_values.DEFAULT_WEIGHTS


def baseline_round(casts, kills=(), t_decided=60.0):
    return {"sides": {"0": "attack", "1": "attack", "5": "defense", "6": "defense"}, "plant": None, "contact": 5.0,
            "t_decided": t_decided, "kills": list(kills), "casts": casts, "heals": [], "unused": []}


def test_calls_grade_by_spread():
    assert util_values.call(0.02, 0.01) == "this wins rounds, keep throwing it"
    assert util_values.call(0.004, 0.01) == "keep throwing it"
    assert util_values.call(0.0, 0.01) == "no clear value"
    assert util_values.call(-0.005, 0.01) == "this is costing you"
    assert util_values.shrunk([1.0] * 5, 0.0, 5.0) == 0.5


# --- the type-and-duration baseline (v2)

import numpy as np  # noqa: E402


def v2_cast(i, t=10.0, seen_end=12.0, type_="flash", side="attack", **kw):
    return {"i": i, "by": 1, "side": side, "t": t, "type": type_, "kinds": ["blind"], "signals": {"seen_end": seen_end},
            "effects": [], **kw}


def v2_round(casts=(), heals=(), kills=None, t_decided=60.0):
    kills = [kill(14.0, 5, 0, [5, 5], k_side="defense", v_side="attack")] if kills is None else kills
    r = baseline_round(list(casts), kills, t_decided)
    r["heals"] = list(heals)
    return r


def quiet_table(n_rounds=12):
    table: dict = {}
    for _ in range(n_rounds):
        util_values.add_moments_v2(table, v2_round(), MODEL)
    return table


def test_a_cast_is_compared_at_its_own_length():
    r = v2_round()
    keys = util_values.levels(r, "attack", 10.0)
    arr = np.array([40.0] + [4.0 * L for L in range(1, 46)])
    table = {"flash": {keys[0]: arr}}
    assert util_values.baseline_v2(table, "flash", keys, 3)["mean"] == pytest.approx(12.0 / 40)
    out = util_values.cast_lift_v2(r, MODEL, v2_cast(0), table)      # up 10..13: three seconds
    assert out["L"] == 3 and out["type"] == "flash" and out["base"] == pytest.approx(0.3)
    assert out["base_n"] == 40 and out["base_level"] == util_values.BASE_LEVELS[0]


def test_only_on_time_counts(monkeypatch):
    r = v2_round()
    monkeypatch.setattr(util_values, "item_segments", lambda rd, item, typ=None: [[10.0, 12.0], [20.0, 23.0]])
    grid = util_values.swing_grid(r, MODEL, "attack")
    want = (util_values.p_on_grid(*grid, 12.0) - util_values.p_on_grid(*grid, 10.0)
            + util_values.p_on_grid(*grid, 23.0) - util_values.p_on_grid(*grid, 20.0))
    out = util_values.cast_lift_v2(r, MODEL, v2_cast(0), quiet_table())
    assert out["L"] == 5 and out["swing"] == pytest.approx(want, abs=1e-4)


def test_the_baseline_reads_the_level_at_the_first_segments_start(monkeypatch):
    r = v2_round()                      # a kill at 14 s changes the man advantage
    monkeypatch.setattr(util_values, "item_segments", lambda rd, item, typ=None: [[20.0, 25.0]])
    at_start = util_values.levels(r, "attack", 20.0)
    at_cast = util_values.levels(r, "attack", 10.0)
    assert at_start[0] != at_cast[0]
    arr = np.array([40.0] + [2.0 * L for L in range(1, 46)])
    out = util_values.cast_lift_v2(r, MODEL, v2_cast(0), {"flash": {at_start[1]: arr}})
    assert out["base_level"] == util_values.BASE_LEVELS[1] and out["base"] == pytest.approx(10.0 / 40)
    assert util_values.cast_lift_v2(r, MODEL, v2_cast(0), {"flash": {at_cast[1]: arr}})["lift"] is None


def test_a_long_window_is_cut_at_45_seconds_of_on_time(monkeypatch):
    r = v2_round(kills=[kill(50.0, 5, 0, [5, 5], k_side="defense", v_side="attack")])
    monkeypatch.setattr(util_values, "item_segments", lambda rd, item, typ=None: [[0.0, 60.0]])
    grid = util_values.swing_grid(r, MODEL, "attack")
    out = util_values.cast_lift_v2(r, MODEL, v2_cast(0), quiet_table())
    assert out["L"] == 45
    assert abs(util_values.p_on_grid(*grid, 60.0) - util_values.p_on_grid(*grid, 45.0)) > 0.01, "P moves after 45 s"
    assert out["swing"] == pytest.approx(util_values.p_on_grid(*grid, 45.0) - util_values.p_on_grid(*grid, 0.0),
                                         abs=1e-4)


def test_a_heal_row_makes_heal_up_and_nothing_else():
    heal = {"by": 1, "t": 10.0}
    table: dict = {}
    util_values.add_moments_v2(table, v2_round(heals=[heal]), MODEL)

    def n_attack(kind):
        return sum(int(a[0]) for k, a in table[kind].items() if k.startswith("attack|") and k.count("|") == 1)

    assert n_attack("smoke") == 30 and n_attack("heal") == 5, "moments from 10 s on leave the heal cells only"


def test_an_item_with_no_segments_has_no_lift():
    out = util_values.cast_lift_v2(v2_round(), MODEL, v2_cast(0, seen_end=None), quiet_table())
    assert out["lift"] is None and out["swing"] is None


def test_own_match_is_taken_out_of_the_comparison_set():
    r = v2_round()
    own: dict = {}
    util_values.add_moments_v2(own, r, MODEL)
    table = quiet_table()
    util_values.merge_tables_v2(table, own)
    with_own = util_values.cast_lift_v2(r, MODEL, v2_cast(0), table)
    without = util_values.cast_lift_v2(r, MODEL, v2_cast(0), table, own)
    assert 0 < without["base_n"] < with_own["base_n"] and without["lift"] is not None


def test_the_numpy_table_equals_a_plain_python_reference():
    casts = [v2_cast(0, t=6.0, seen_end=9.0), v2_cast(1, t=20.0, seen_end=30.0, type_="smoke", side="defense")]
    r = v2_round(casts)
    table: dict = {}
    util_values.add_moments_v2(table, r, MODEL)
    ref: dict = {}
    end = r["t_decided"]
    t = 0.0
    while t < end:
        for side in ("attack", "defense"):
            up = {c["type"] for c in casts if c["side"] == side
                  and c["t"] <= t < c["signals"]["seen_end"] + 1.0}
            vec = [1.0] + [util_values._p_at(r, MODEL, side, min(t + L, end)) - util_values._p_at(r, MODEL, side, t)
                           for L in range(1, 46)]
            for typ in util_values.TYPES:
                if typ in up:
                    continue
                for key in util_values.levels(r, side, t):
                    cell = ref.setdefault(typ, {}).setdefault(key, [0.0] * 46)
                    for j, v in enumerate(vec):
                        cell[j] += v
        t += util_values.BASE_STEP_S
    assert set(table) == set(ref)
    for typ in ref:
        assert set(table[typ]) == set(ref[typ])
        for key, cell in ref[typ].items():
            assert table[typ][key].shape == (46,)
            assert table[typ][key] == pytest.approx(cell, abs=1e-6)


# --- per-match tables, the gate, Yours, devices (W17)

KILL_TIMES = [10.0, 14.0, 18.0, 22.0, 26.0, 12.0]


def corpus(n=6, with_flash=(1, 3)):
    items = []
    for m in range(1, n + 1):
        casts = [v2_cast(0, t=8.0, seen_end=11.0)] if m in with_flash else []
        r = v2_round(casts, kills=[kill(KILL_TIMES[(m - 1) % len(KILL_TIMES)], 5, 0, [5, 5], k_side="defense", v_side="attack")],
                     t_decided=40.0)
        items.append({"match": m, "map": "Haven", "data": r, "player_of": {0: 100, 1: 101, 5: 200, 6: 201}})
    return items


def brute_gate(items, min_n, L):
    """Every held-out moment, one by one: predicted means from the other matches' moments, real squared errors."""
    moms = []       # (match, typ, side, keys, row)
    for it in items:
        for side, t, keys, up, row, alive, nxt in util_values.moment_rows(it["data"], MODEL):
            for typ in util_values.TYPES:
                if typ not in up:
                    moms.append((it["match"], typ, keys, row))
    se_cell = se_ma = 0.0
    used = set()
    for m, typ, keys, row in moms:
        others = [x for x in moms if x[0] != m and x[1] == typ]

        def mean_at(lvl):
            sel = [x[3] for x in others if x[2][0].split("|")[:[4, 4, 2][lvl] if lvl else 5] ==
                   keys[0].split("|")[:[4, 4, 2][lvl] if lvl else 5]]
            return (len(sel), sum(s[L] for s in sel) / len(sel) if sel else 0.0)
        lv = None
        for i, depth in enumerate((5, 4, 2)):
            n, mu = (lambda sel: (len(sel), sum(s[3][L] for s in sel) / len(sel) if sel else 0.0))(
                [x for x in others if x[2][0].split("|")[:depth] == keys[0].split("|")[:depth]])
            if n >= min_n:
                lv = i
                mu_cell = mu
                break
        n2 = [x for x in others if x[2][0].split("|")[:2] == keys[0].split("|")[:2]]
        if lv is None or len(n2) < min_n:
            continue
        mu_ma = sum(x[3][L] for x in n2) / len(n2)
        used.add(lv)
        y = row[L]
        se_cell += (y - mu_cell) ** 2
        se_ma += (y - mu_ma) ** 2
    return se_cell - se_ma, used


def test_the_leave_one_match_out_gate_equals_a_brute_force_recompute():
    items = corpus()
    tabs = util_values.match_tables(items, MODEL)
    for min_n in (3, 6):
        g = util_values.gate(tabs["all"], min_n=min_n)
        used = set()
        for L in (1, 5, 10, 30, 45):
            want, u = brute_gate(items, min_n, L)
            used |= u
            got = g["all"][L]
            assert got["se_cell"] - got["se_ma"] == pytest.approx(want, abs=1e-9)
            assert got["pass"] == (want < -1e-12)
        assert 0 in used or min_n == 6
        assert used & {1, 2}, "a fallback level was used"
    assert 0 in brute_gate(items, 3, 5)[1], "and level 0 on the small corpus"


def test_yours_leaves_out_every_match_given():
    items = corpus(8, with_flash=())
    tabs = util_values.match_tables(items, MODEL)
    per_match = tabs["all"]
    totals = util_values.totals(per_match)
    r = items[0]["data"]
    keys = util_values.levels(r, "attack", 2.0)
    out = util_values.lift_v2(totals, per_match, "flash", keys, 4, 0.5, leave_out={1, 2})
    others: dict = {}
    for it in items[2:]:
        util_values.add_moments_v2(others, it["data"], MODEL)
    want = util_values.baseline_v2(others, "flash", keys, 4)
    assert out["mean"] == pytest.approx(want["mean"]) and out["n"] == want["n"] and out["level"] == want["level"]
    assert out["lift"] == pytest.approx(0.5 - want["mean"])
    # a table with too few other moments has no baseline
    assert util_values.lift_v2(totals, per_match, "flash", keys, 4, 0.5, leave_out=set(range(1, 8))) is None


def test_a_device_table_holds_the_other_rounds_of_its_map_only():
    plac = {"i": 0, "by": 1, "side": "attack", "t": 8.0, "type": "placeable", "ability_id": "Sage/Barrier",
            "kinds": ["wall"], "effects": [], "signals": {}}
    sages = {"1": "Sage", "6": "Sage"}                  # one Sage a side
    r_with = {**v2_round([plac], t_decided=20.0), "agents": sages}
    r_without = {**v2_round(t_decided=20.0), "agents": sages}
    items = [{"match": 1, "map": "Haven", "data": r_with, "player_of": {}},
             {"match": 2, "map": "Haven", "data": r_without, "player_of": {}},
             {"match": 3, "map": "Bind", "data": r_without, "player_of": {}},
             {"match": 4, "map": "Haven", "data": {**v2_round(t_decided=20.0), "agents": {"1": "Jett", "6": "Omen"}},
              "player_of": {}}]                                                                  # no Sage
    dev = util_values.match_tables(items, MODEL)["device"]

    def count(key, m):
        return sum(a[0] for a in dev[key].get(m, {}).values())
    atk, dfn, bind = "Sage/Barrier|Haven|attack", "Sage/Barrier|Haven|defense", "Sage/Barrier|Bind|attack"
    assert 1 not in dev[atk] and count(atk, 2) > 0, "the round where attack placed it is out of attack's table"
    assert count(dfn, 1) > 0 and count(dfn, 2) > 0, "defense never placed it"
    assert 3 not in dev[atk] and 3 not in dev[dfn] and count(bind, 3) > 0, "each map has its own"
    assert 1 not in dev[bind] and 2 not in dev[bind]
    assert 4 not in dev[atk] and 4 not in dev[dfn], "a side without the ability's agent is no comparison"


def test_a_device_table_takes_a_round_whose_agent_died_early_from_the_death():
    def table(death_t, aid="Cypher/Trapwire"):
        agent = aid.split("/")[0]
        plac = {"i": 0, "by": 1, "side": "attack", "t": 0.0, "type": "placeable", "ability_id": aid,
                "kinds": ["device"], "effects": [], "signals": {}}
        died = kill(death_t, 5, 1, [5, 5], k_side="defense", v_side="attack")
        rd = {**v2_round([plac], kills=[died], t_decided=40.0), "agents": {"1": agent}}
        cells = util_values.match_tables([{"match": 1, "map": "Haven", "data": rd, "player_of": {}}],
                                         MODEL)["device"][f"{aid}|Haven|attack"]
        return sum(a[0] for a in cells.get(1, {}).values())
    assert table(14.0) == len([t for t in range(0, 40, 2) if t >= 14.0])     # from the death on
    assert table(25.0) == 0                                                  # died later: not "early"
    assert table(14.0, "Yoru/Fakeout") == 0                                  # not a sentinel's: on after death
    plac = {"i": 0, "by": 1, "side": "attack", "t": 0.0, "type": "placeable", "ability_id": "Sage/Barrier",
            "kinds": ["wall"], "effects": [], "signals": {}}
    # rows extracted before `agents` was stored: the agent comes from the slot's casts or its ult
    old = {**v2_round(t_decided=20.0), "ults": {"1": {"use": {"ability_id": "Sage/Resurrection", "t": None}}}}
    assert util_values.round_agents(old) == {1: "Sage"}
    # ... and from any round of the match: a round with no Sage cast is still a round with Sage
    placed = {**plac, "agent": "Sage"}
    items = [{"match": 1, "map": "Haven", "data": v2_round([placed], t_decided=20.0), "player_of": {}},
             {"match": 1, "map": "Haven", "data": v2_round(t_decided=20.0), "player_of": {}}]
    cells = util_values.match_tables(items, MODEL)["device"]["Sage/Barrier|Haven|attack"]
    assert sum(a[0] for a in cells.get(1, {}).values()) == 10           # the second round's 10 moments


def test_equal_errors_do_not_pass():
    def arr(n):
        return np.array([float(n)] + [n * L / 8.0 for L in range(1, 46)])
    per_match = {m: {"flash": {"attack|5v5|0|contact|a": arr(10), "attack|5v5|0|contact|b": arr(10)}}
                 for m in range(1, 5)}
    g = util_values.gate(per_match, min_n=3)
    assert all(not g["all"][L]["pass"] for L in range(1, 46)) and g["all"][3]["n"] > 0
    assert all(not g["by_type"]["flash"][L]["pass"] for L in range(1, 46))
    assert not util_values.gate({}, min_n=3)["all"][1]["pass"]


def test_the_gates_paired_difference_is_one_per_match_summed_over_types():
    def arr(n, slope):
        return np.array([float(n)] + [n * slope * L for L in range(1, 46)])
    per_match = {m: {typ: {"attack|5v5|0|contact|a": arr(10, 0.01 * m), "attack|5v5|0|contact|b": arr(10, -0.01 * m)}
                     for typ in ("flash", "smoke")} for m in range(1, 5)}
    g = util_values.gate(per_match, min_n=3)
    assert g["all"][5]["matches"] == 4, "two types in one match are one paired difference"
    one = util_values.gate({m: {"flash": v["flash"]} for m, v in per_match.items()}, min_n=3)
    assert g["all"][5]["diff_mean"] == pytest.approx(2 * one["all"][5]["diff_mean"])


def test_overlap_counts_a_same_type_cast_starting_inside_the_window(monkeypatch):
    monkeypatch.setattr(util_values, "item_segments", lambda rd, item, typ=None: [[13.0, 15.0]])
    r = v2_round([v2_cast(0, t=13.0)], kills=[], t_decided=12.0)
    items = [{"match": 1, "map": "Haven", "data": r, "player_of": {}}]
    ov = util_values.match_tables(items, MODEL)["overlap"]["flash"]
    steps = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert ov[0] == 12                      # six moments per side, none with a flash up yet
    for L in (1, 3, 4, 6, 45):
        assert ov[L] == sum(1 for t in steps if 13.0 < t + L), L
    assert ov[3] == 0 and ov[4] == 1


def test_a_yours_row_is_added_for_each_alive_friend_slot():
    items = corpus(2, with_flash=())
    tabs = util_values.match_tables(items, MODEL, friend_players=frozenset({100, 200}))
    assert set(tabs["yours"]) == {100, 200}
    # 100 is an attacker who dies at 10 s in match 1: fewer moments than the all-table's attack moments
    n100 = sum(a[0] for a in tabs["yours"][100][1]["flash"].values())
    n_all_atk = sum(a[0] for k, a in tabs["all"][1]["flash"].items() if k.startswith("attack"))
    assert 0 < n100 < n_all_atk
    n200 = sum(a[0] for a in tabs["yours"][200][1]["flash"].values())
    assert n200 == sum(a[0] for k, a in tabs["all"][1]["flash"].items() if k.startswith("defense"))
    g = util_values.gate_all_modes(tabs)
    assert set(g) >= {"all", "yours", "device", "by_type"} and set(g["yours"]) == set(range(1, 46))
