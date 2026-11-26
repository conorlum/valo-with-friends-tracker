"""Pure post-plant figures on synthetic, W1-shaped evidence (no accounts or DB)."""

import math
from collections import Counter
from dataclasses import replace

import pytest

from app.coach import areas
from app.services import postplant_stats as stats


MODEL = {"synthetic": True}


def event(t=10.2, killer=0, victim=5, ku=100, vu=200):
    return {"t": t, "killer": killer, "victim": victim,
            "k_side": None if killer is None else "attack" if killer < 5 else "defense",
            "v_side": "attack" if victim < 5 else "defense", "before": [5, 5],
            "ku": ku, "kv": 100, "kz": 20, "vu": vu, "vv": 100, "vz": 30,
            "k_line": 1}


def rnd(i=0, *, samples=None, kills=None, asset_key="Toy", site="A", match_id=None):
    data = {"v": 2, "plant": {"t": 10, "u": 0, "v": 0, "by": 0},
            "end": 12, "step": 0.5,
            "sides": {"0": "attack", "1": "attack", "5": "defense"},
            "sides_known": True,
            "samples": [[10, 0, 100, 100, 20, 1]] if samples is None else samples,
            "kills": [] if kills is None else kills}
    return stats.RoundInput(match_id=i if match_id is None else match_id,
                            replay_uuid=f"00000000-0000-0000-0000-{i:012d}",
                            round_number=i + 1, map_name="Toy", asset_key=asset_key,
                            site=site, spot="default", data=data, clock_offset=3,
                            slot_players={0: None, 1: None, 5: None})


@pytest.fixture
def figures(monkeypatch):
    monkeypatch.setattr(areas, "load_painted", lambda key: None)
    monkeypatch.setattr(areas, "area_at", lambda key, u, v, z, *, painted_table:
                        {"name": {100: "A", 200: "B", 300: "C"}.get(u, "Unnamed")})
    monkeypatch.setattr(stats.util_values, "swings", lambda data, model:
                        {(round(k["t"], 2), k["killer"], k["victim"]):
                         (0.10 if k["k_side"] == "attack" else 0.08)
                         for k in data["kills"]})


def by_name(table):
    return {row["name"]: row for row in table["areas"]}


@pytest.fixture
def real_figures(monkeypatch):
    """Synthetic map attribution, with the production swings and win estimator."""
    monkeypatch.setattr(areas, "load_painted", lambda key: None)
    monkeypatch.setattr(areas, "area_at", lambda key, u, v, z, *, painted_table:
                        {"name": {100: "A", 200: "B", 300: "C"}[u]})
    return {"cells": {"5v4": [8, 10], "4v5": [2, 10]}}


@pytest.mark.parametrize("killer,victim,k_side,v_side,enemy", [
    (0, 1, "attack", "attack", False),
    (0, 0, "attack", "attack", False),
    (5, 5, "defense", "defense", False),
    (None, 0, None, "attack", False),
    (None, 5, None, "defense", False),
    (0, 5, None, "defense", False),
    (0, 5, "A", "defense", False),
    (0, 5, "attack", None, False),
    (0, 5, "attack", "B", False),
    (0, 5, "attack", "defense", True),
    (5, 0, "defense", "attack", True),
], ids=["team", "self", "defender-self", "environment", "defender-environment",
        "unknown-killer-side", "unlinked-killer-side", "unknown-victim-side", "unlinked-victim-side",
        "enemy", "defender-enemy"])
@pytest.mark.parametrize("with_model", [True, False], ids=["win-model", "no-model"])
def test_only_enemy_kills_earn_credit(real_figures, killer, victim, k_side, v_side, enemy, with_model):
    positions = {0: 100, 1: 200, 5: 300}
    k = event(killer=killer, victim=victim, ku=positions.get(killer), vu=positions[victim])
    k.update(k_side=k_side, v_side=v_side)
    samples = [[10, slot, u, 100, 20, 1] for slot, u in positions.items()]
    rounds = [rnd(i, samples=samples, kills=[k]) for i in range(2)]
    model = real_figures if with_model else None
    swings = stats.kill_swings(rounds[0], model)
    swing = swings.get((k["t"], killer, victim), 0.0)
    if with_model:
        assert bool(swings) is enemy
        assert swing > 0 if enemy else swing == 0
    for side, slots in [("attack", (0, 1)), ("defense", (5,))]:
        table = stats.area_table(rounds, model, side=side)
        totals = stats.site_totals(rounds, model, side)
        expected_kills = 2 * int(enemy and killer in slots)
        expected_deaths = 2 * int(victim in slots)
        assert totals["kills"] == expected_kills
        assert totals["deaths"] == expected_deaths
        assert totals["distinct_n"] == table["distinct_n"] == 2 * len(slots)
        for row, slot in zip(table["areas"], slots):
            credit = int(enemy and killer == slot)
            death = int(victim == slot)
            assert row["kills"] == 2 * credit
            assert row["deaths"] == 2 * death
            assert row["kills_per_n"] == credit
            assert row["kd"] == (credit / death if death else None)
            assert row["kills_ci"] == (credit, credit)
            assert len(row["examples"]) == (2 if credit and with_model else 0)
            if with_model:
                assert row["win_gained"] == pytest.approx(2 * credit * swing)
                assert row["net_win"] == pytest.approx(2 * (credit - death) * swing)
                assert row["gained_ci"] == pytest.approx((credit * swing,) * 2)
                assert row["net_ci"] == pytest.approx(((credit - death) * swing,) * 2)
        for field in ("kills", "deaths", "win_gained", "net_win"):
            if not with_model and field in ("win_gained", "net_win"):
                assert totals[field] is None
            else:
                assert sum(row[field] for row in table["areas"]) == pytest.approx(totals[field])


@pytest.mark.parametrize("killer_located,victim_located", [(False, False), (False, True), (True, False)])
def test_arena_null_positions_add_no_area_events_or_holdings(real_figures, killer_located, victim_located):
    k = event()
    for prefix, located in [("k", killer_located), ("v", victim_located)]:
        if not located:
            k.update({prefix + axis: None for axis in ("u", "v", "z")})
    if not killer_located:
        k["k_line"] = None
    r = rnd(samples=[], kills=[k])
    swing = stats.kill_swings(r, real_figures)[(k["t"], 0, 5)]
    for side, located in [("attack", killer_located), ("defense", victim_located)]:
        table = stats.area_table([r], real_figures, side=side)
        totals = stats.site_totals([r], real_figures, side)
        assert totals["kills"] == int(side == "attack" and located)
        assert totals["deaths"] == int(side == "defense" and located)
        assert totals["win_gained"] == pytest.approx(swing if side == "attack" and located else 0)
        assert totals["net_win"] == pytest.approx((swing if side == "attack" else -swing) if located else 0)
        assert totals["distinct_n"] == table["distinct_n"] == 0
        assert len(table["areas"]) == int(located)
        for row in table["areas"]:
            assert row["held_s"] == row["n"] == 0
            assert row["line_share"] is row["crossfire_share"] is None
        for field in ("kills", "deaths", "win_gained", "net_win"):
            assert sum(row[field] for row in table["areas"]) == pytest.approx(totals[field])


def test_calculation_version_invalidates_cached_team_kill_figures():
    assert stats.CALCULATION_VERSION == 2


def paint(key, name="Paint", cell_id=1, bands=None):
    names = {1: {"name": name, "super": "A"}, 2: {"name": "Upper", "super": "A"}}
    return {key: areas.Painted(names, (cell_id,) * (areas.GRID ** 2), (),
                               bands or {}, {1: (name,), 2: ("Upper",)}, b"",
                               {1: frozenset({1}), 2: frozenset({2})})}   # no parents: each is its own only ancestor


def test_area_lookup_fallback_zero_height_and_layout(monkeypatch):
    monkeypatch.setattr(areas, "_table", lambda: {
        "Toy": [{"name": "Callout", "u": 100, "v": 100, "super": "A"}],
        "Toy_12_0": [{"name": "Later callout", "u": 100, "v": 100, "super": "A"}]})
    assert stats.area_of("Toy", 101, 100, None, painted_table={"Toy": None}) == "Callout"
    assert stats.area_of("Toy_12_0", 100, 100, None,
                         painted_table={"Toy_12_0": None}) == "Later callout"
    assert stats.area_of("Toy", 100, 100, 20, painted_table=paint("Toy", cell_id=0)) == "Unnamed"
    cell = areas.cell_at(100, 100)
    table = paint("Toy", bands={cell: ((0, 10, 1), (20, 30, 2))})
    assert stats.area_of("Toy", 100, 100, 15, painted_table=table) == "Unnamed"
    assert stats.area_of("Toy", 100, 100, 25, painted_table=table) == "Upper"
    table.update(paint("Toy_12_0", name="Later paint"))
    assert stats.area_of("Toy_12_0", 100, 100, 25, painted_table=table) == "Later paint"


@pytest.mark.parametrize("angles,lines,expected", [
    ((0, 90), (1, 1), {(10, 0), (10, 1)}),
    ((0, 50), (1, 1), set()), ((350, 30), (1, 1), set()),
    ((0, 90), (1, 0), set()), ((0, 60), (1, 1), set()),
])
def test_crossfire_smallest_angle_and_translation(angles, lines, expected):
    data = rnd().data
    data["samples"] = [[10, slot, 100 * math.cos(math.radians(a)),
                        100 * math.sin(math.radians(a)), 20, line]
                       for slot, (a, line) in enumerate(zip(angles, lines))]
    assert stats.crossfire_flags(data) == expected
    data["plant"].update(u=400, v=500)
    for sample in data["samples"]:
        sample[2] += 400
        sample[3] += 500
    assert stats.crossfire_flags(data) == expected


def test_crossfire_requires_same_side_and_time():
    data = rnd(samples=[[10, 0, 100, 0, 20, 1], [10, 5, 0, 100, 20, 1],
                        [10.5, 1, 0, 100, 20, 1]]).data
    assert stats.crossfire_flags(data) == set()
    data["sides"] = {"0": "A", "5": "B", "1": "A"}
    assert stats.crossfire_flags(data) == set()


def test_kill_swings_adapts_w1_and_does_not_build(monkeypatch):
    r = rnd(kills=[event()])
    seen = []
    monkeypatch.setattr(stats.util_values, "swings", lambda data, model: seen.append((data, model)) or {})
    assert stats.kill_swings(r, None) == {}
    assert not seen
    stats.kill_swings(r, MODEL)
    assert seen == [({"clock_offset": 3, "plant": 10, "kills": [
        {k: event()[k] for k in ("t", "killer", "victim", "k_side", "v_side", "before")}]}, MODEL)]


def test_n_time_shares_and_population(figures):
    r = rnd(samples=[[10, 0, 100, 100, 20, 1], [10.5, 0, 200, 100, 20, 0],
                     [11, 0, 200, 100, 20, 1], [10, 1, 100, 100, 20, 1]])
    table = stats.area_table([r], MODEL, side="attack", include_slot=lambda r, slot: slot == 0)
    a, b = by_name(table).values()
    assert table["distinct_n"] == 1
    assert (a["n"], b["n"], a["held_s"], b["held_s"]) == (1, 1, 0.5, 1.0)
    assert a["time_share"] == pytest.approx(1 / 3)
    assert b["line_share"] == 0.5


def test_shrinkage_uses_distinct_site_population(figures):
    rounds = [rnd(i, samples=[[10, 0, 100 if i < 10 else 200, 100, 20, 1]],
                  kills=[event()] if i < 5 else []) for i in range(25)]
    a = by_name(stats.area_table(rounds, MODEL, side="attack"))["A"]
    assert (a["n"], a["kills"]) == (10, 5)
    assert a["shrunk_kills_per_n"] == 0.35
    selected = stats.area_table(rounds[:10], MODEL, side="attack")
    assert by_name(selected)["A"]["shrunk_kills_per_n"] == 0.5


def test_net_additivity_own_positions_and_units(figures):
    r = rnd(kills=[event(), event(11, killer=5, victim=0, ku=200, vu=100)])
    table = stats.area_table([r], MODEL, side="attack")
    a = by_name(table)["A"]
    assert a["win_gained"] == 0.10
    assert a["net_win"] == pytest.approx(0.02)
    assert (a["n"], a["kills"], a["deaths"], a["kd"]) == (1, 1, 1, 1)
    assert a["deaths_per_min"] == 120
    for side in ("attack", "defense"):
        table = stats.area_table([r], MODEL, side=side)
        totals = stats.site_totals([r], MODEL, side)
        for field in ("kills", "deaths", "win_gained", "net_win"):
            assert sum(row[field] for row in table["areas"]) == pytest.approx(totals[field])
    assert stats.pts(0.10) == "+10.0 pts"
    assert stats.pts(-0.02) == "-2.0 pts"
    assert stats.pts(0) == "+0.0 pts"


@pytest.mark.parametrize("n, coloured", [(19, False), (20, True)])
def test_colour_threshold(figures, n, coloured):
    row = by_name(stats.area_table([rnd(i) for i in range(n)], MODEL, side="attack"))["A"]
    assert row["coloured"] is coloured


def test_intervals_cluster_whole_matches(figures):
    rounds = [rnd(i, match_id=i // 5, kills=[event()] if i < 5 else []) for i in range(10)]
    a = by_name(stats.area_table(rounds, MODEL, side="attack"))["A"]
    assert a["kills_ci"] == (0, 1)
    for field in ("kills_ci", "gained_ci", "net_ci"):
        assert all(math.isfinite(v) for v in a[field])
        single = by_name(stats.area_table(rounds[:5], MODEL, side="attack"))["A"]
        assert single[field] is None


def test_missing_model_keeps_kills_but_no_win_estimates(figures):
    row = by_name(stats.area_table([rnd(kills=[event()])], None, side="attack"))["A"]
    assert row["kills"] == 1 and row["kills_per_n"] == 1
    for field in ("win_gained", "net_win", "win_gained_per_n", "net_win_per_n",
                  "shrunk_win_gained_per_n", "shrunk_net_win_per_n", "gained_ci", "net_ci"):
        assert row[field] is None


def test_examples_largest_three_and_clamped_time(figures, monkeypatch):
    kills = [event(t) for t in (1, 12, 13, 14)]
    r = rnd(kills=kills)
    monkeypatch.setattr(stats.util_values, "swings", lambda data, model:
                        {(k["t"], 0, 5): swing for k, swing in zip(kills, (0.4, 0.1, 0.3, 0.2))})
    a = by_name(stats.area_table([r], MODEL, side="attack"))["A"]
    assert [e["url"] for e in a["examples"]] == [
        f"/replays/{r.replay_uuid}?round=1&t={t}" for t in ("0.0", "11.0", "12.0")]
    assert [e["swing"] for e in a["examples"]] == [0.4, 0.3, 0.2]


def test_event_only_area_and_unknown_positions(figures):
    kills = [event(10.2, ku=300), event(10.3, killer=5, victim=0, ku=200, vu=300),
             event(10.4, ku=None)]
    r = rnd(samples=[[10, 0, 100, 100, 20, 1], [10.5, 0, 100, 100, 20, 1]], kills=kills)
    table = stats.area_table([r], MODEL, side="attack")
    c = by_name(table)["C"]
    assert (c["kills"], c["deaths"], c["n"], c["held_s"]) == (1, 1, 0, 0)
    assert not c["coloured"]
    for key in ("kills_per_n", "deaths_per_n", "win_gained_per_n", "net_win_per_n",
                "deaths_per_min", "shrunk_kills_per_n", "shrunk_win_gained_per_n",
                "shrunk_net_win_per_n", "kills_ci", "gained_ci", "net_ci"):
        assert c[key] is None
    totals = stats.site_totals([r], MODEL, "attack")
    assert totals["kills"] == sum(row["kills"] for row in table["areas"]) == 1
    assert totals["win_gained"] == sum(row["win_gained"] for row in table["areas"]) == 0.1


def test_unnamed_never_coloured_or_drawn(figures):
    rounds = [rnd(i, samples=[[10, 0, 999, 100, 20, 1]]) for i in range(25)]
    row = by_name(stats.area_table(rounds, MODEL, side="attack"))["Unnamed"]
    assert row["n"] == 25
    assert row["coloured"] is False and row["marker"] is False


def test_paint_loaded_once_per_key_and_sample_z_passed(figures, monkeypatch):
    calls, lookups = Counter(), []
    monkeypatch.setattr(areas, "load_painted", lambda key: calls.update([key]))
    assert stats.painted_tables(["Toy", "Toy", "Toy_12_0"]) == {"Toy": None, "Toy_12_0": None}
    calls.clear()
    monkeypatch.setattr(areas, "area_at", lambda key, u, v, z, *, painted_table:
                        lookups.append((key, z, painted_table)) or {"name": "A"})
    rounds = [rnd(i, asset_key="Toy" if i < 8 else "Toy_12_0") for i in range(16)]
    stats.area_table(rounds, MODEL, side="attack")
    assert calls == {"Toy": 1, "Toy_12_0": 1}
    assert len(lookups) == 16 and all(z == 20 and key in table for key, z, table in lookups)
    calls.clear()
    slots = {(r.replay_uuid, r.round_number): 0 for r in rounds}
    stats.player_blocks(rounds, slots, MODEL)
    assert calls == {"Toy": 1, "Toy_12_0": 1}


def player_result(rounds):
    slots = {(r.replay_uuid, r.round_number): 0 for r in rounds}
    return next(b for b in stats.player_blocks(rounds, slots, MODEL) if b["side"] == "attack")


def test_player_layouts_stay_separate(figures):
    rounds = [rnd(i, asset_key="Toy" if i < 10 else "Toy_12_0", samples=[
        [10, 0, 100, 100, 20, 1], [10.5, 0, 100 if i < 10 else 200, 100, 20, 1]])
        for i in range(20)]
    result = player_result(rounds)
    assert result["total_n"] == 20 and result["eligible"]
    assert [b["asset_key"] for b in result["blocks"]] == ["Toy", "Toy_12_0"]
    a, b = result["blocks"]
    assert by_name(a)["A"]["held_share"] == 1
    assert by_name(b)["A"]["held_share"] == 0.5


def test_player_distinct_eligibility_and_hidden_blocks(figures):
    rounds = [rnd(i, samples=[[10 + j / 2, 0, u, 100, 20, 1]
                              for j, u in enumerate((100, 200, 300))]) for i in range(10)]
    result = player_result(rounds)
    assert result["total_n"] == 10 and not result["eligible"]
    assert len(result["blocks"]) == 1
    assert len(player_result(rounds[:8])["blocks"]) == 1
    assert player_result(rounds[:7])["blocks"] == []
    rounds = [rnd(i, site="A" if i < 7 else "B" if i < 14 else "C") for i in range(20)]
    result = player_result(rounds)
    assert result["total_n"] == 20 and result["eligible"]
    assert result["blocks"] == []


def test_player_block_retains_event_only_round_without_inventing_n(figures):
    rounds = [rnd(i, kills=[event()]) for i in range(8)]
    rounds.append(rnd(8, samples=[], kills=[event()]))
    result = player_result(rounds)
    block = result["blocks"][0]
    assert result["total_n"] == block["n"] == 8
    assert by_name(block)["A"]["kills"] == 9


def test_population_filters_events_and_shrinkage_but_not_crossfire_partner(figures):
    rounds = [rnd(i, samples=[[10, 0, 100, 0, 20, 1], [10, 1, 0, 100, 20, 1],
                             [10.5, 0, 100, 0, 20, 0]],
                  kills=[event(killer=1, ku=200)]) for i in range(2)]
    all_slots = stats.area_table(rounds, MODEL, side="attack")
    assert all_slots["distinct_n"] == 4
    selected = stats.area_table(rounds, MODEL, side="attack", include_slot=lambda r, slot: slot == 0)
    a = by_name(selected)["A"]
    assert selected["distinct_n"] == 2
    assert a["kills"] == a["shrunk_kills_per_n"] == 0
    assert a["held_s"] == 2
    assert a["line_share"] == a["crossfire_share"] == 0.5
    assert "B" not in by_name(selected)


def test_same_replay_different_rounds_are_distinct_and_side_totals_are_independent(figures):
    rounds = [rnd(i, samples=[[10, 0, 100, 100, 20, 1], [10, 5, 200, 100, 20, 1]])
              for i in range(20)]
    rounds = [replace(r, replay_uuid=rounds[0].replay_uuid) for r in rounds]
    slots = {(r.replay_uuid, r.round_number): 0 if i < 12 else 5 for i, r in enumerate(rounds)}
    result = {row["side"]: row for row in stats.player_blocks(rounds, slots, MODEL)}
    assert result["attack"]["total_n"] == 12
    assert result["defense"]["total_n"] == 8
    assert not result["attack"]["eligible"] and not result["defense"]["eligible"]


def test_sentence_uses_best_coloured_net_and_signed_points(figures):
    def rounds_with_holdings(n, holdings, kill_u):
        rounds = [rnd(i, samples=[[10 + j * 0.5, 0, u, 100, 20, 1]
                                  for j, u in enumerate(holdings)],
                      kills=[event(ku=kill_u)]) for i in range(n)]
        for r in rounds:
            r.data["end"] = 15
        return rounds

    block = player_result(rounds_with_holdings(20, [100] * 7 + [200] * 3, 100))["blocks"][0]
    everyone = stats.area_table(
        rounds_with_holdings(40, [100] + [200] * 6 + [300] * 3, 300), MODEL, side="attack")["areas"]
    assert by_name(block)["A"]["held_share"] == 0.7
    assert max(everyone, key=lambda row: row["held_s"])["name"] == "B"
    assert stats.sentence(block, everyone) == (
        "You hold from A 70% of post-plant time; C gets +10.0 pts net win per post-plant (n=40)")
    assert stats.sentence(dict(block, areas=[]), everyone) is None
    assert stats.sentence(dict(block, areas=[dict(row, held_s=0, held_share=0)
                                            for row in block["areas"]]), everyone) is None
    assert stats.sentence(block, []) is None
    assert stats.sentence(block, [dict(row, coloured=False) for row in everyone]) is None
    assert stats.sentence(block, [dict(row, shrunk_net_win_per_n=None) for row in everyone]) is None


def test_unlinked_sides_and_no_plant_make_no_figures(figures):
    r = rnd(kills=[event()])
    r.data["sides"] = {"0": "A", "5": "B"}
    r.data["sides_known"] = False
    assert stats.area_table([r], MODEL, side="attack")["areas"] == []
    r = replace(r, data=dict(r.data, plant=None))
    assert stats.site_totals([r], MODEL, "attack")["kills"] == 0
