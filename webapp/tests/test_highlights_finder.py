"""Synthetic, DB-free examples of the highlight ladder and replay-clock contract."""

from copy import deepcopy

import pytest

from app.services import highlights as h
from app.services import util_model


def players():
    return {
        i: {"team": "team-1" if i < 5 else "team-2", "group": "A" if i < 5 else "B",
            "name": f"Synthetic{i}#TEST", "agent": f"Agent{i}", "friend": i in (0, 1, 2)}
        for i in range(10)
    }


def kill(t, killer=2, victim=5):
    return {"t": t, "killer": killer, "victim": victim}


def round_(kills=(), **overrides):
    value = {"round": 1, "t_decided": 60.0, "t_end": 65.0, "winner": "team-1",
             "attack": "team-1", "kills": list(kills), "alive_steps": [[0, 5, 5]],
             "alive": {i: [[0, None]] for i in range(10)}, "plant": None,
             "score": {"team-1": 3, "team-2": 7}}
    value.update(overrides)
    return value


def clutch_round():
    return round_(t_decided=41.0, t_end=43.0, alive_steps=[[0, 5, 5], [30, 1, 3], [41, 1, 0]],
                  alive={i: [[0, None if i == 2 or i >= 5 else 25]] for i in range(10)})


def inputs(rounds=None, **overrides):
    value = {"players": players(), "rounds": {1: round_()} if rounds is None else rounds,
             "clock_offset": 0.0, "team_fights": {}, "util": [], "model": {"cells": {}}}
    value.update(overrides)
    return value


def test_won_clutch_and_life_intervals():
    r, p = clutch_round(), players()
    item, = h.clutch_of(r, p)
    assert (item["kind"], item["slot"], item["summary"], item["rung"]) == ("clutch", 2, "1v3 clutch", 4)
    assert (item["t_start"], item["t_end"], item["speed_s"]) == (27.0, 43.0, 11.0)
    assert item["events"] == [30.0, 41.0]
    assert item["score"] == [3, 7]
    p[2]["friend"] = False
    assert h.clutch_of(r, p) == []
    p[2]["friend"] = True
    r["alive"][2] = [[0, 12], [29, None]]
    assert h.clutch_of(r, p)[0]["slot"] == 2
    # Slot 1 disconnected at 25 with no corresponding kill; intervals decide the survivor.
    assert not r["kills"]
    r["alive"][2] = [[0, 30]]
    assert h.clutch_of(r, p) == []


def test_clutches_require_winner_friend_and_predecision_step():
    r = clutch_round()
    r["winner"] = "team-2"
    assert h.clutch_of(r, players()) == []
    r = round_(t_decided=41, alive_steps=[[0, 5, 5], [45, 1, 2]])
    assert h.clutch_of(r, players()) == []
    r = round_(winner="team-2", alive_steps=[[0, 5, 5], [30, 3, 1]],
               alive={i: [[0, None if i == 5 or i < 5 else 25]] for i in range(10)})
    assert h.clutch_of(r, players()) == []


@pytest.mark.parametrize("times,kind,rung", [([5, 10, 20, 30, 45], "ace", 3),
                                                   ([5, 15, 30, 45], "4k", 5),
                                                   ([5, 10, 14.5], "3k", 7),
                                                   ([5, 10, 15], "3k", 7)])
def test_multikills(times, kind, rung):
    item, = h.multikills(round_([kill(t, victim=5 + i) for i, t in enumerate(times)]), players())
    assert (item["kind"], item["rung"], item["events"]) == (kind, rung, times)


def test_multikills_exclude_slow_triples_friendly_fire_suicide_and_late_kills():
    assert h.multikills(round_([kill(t, victim=5 + i) for i, t in enumerate([5, 10, 15.5])]), players()) == []
    assert h.multikills(round_([kill(5), kill(6, victim=0), kill(7, victim=2), kill(61, victim=6)]), players()) == []
    assert h.multikills(round_([kill(t, killer=5, victim=i) for i, t in enumerate([1, 2, 3])]), players()) == []


@pytest.mark.parametrize("gap,killer,expected", [(4, 2, True), (5, 2, True), (6, 2, False), (4, 5, False)])
def test_entry(gap, killer, expected):
    victims = (5, 6) if killer == 2 else (0, 1)
    result = h.entry(round_([kill(10, killer, victims[0]), kill(10 + gap, killer, victims[1])]), players())
    assert bool(result) == expected
    if expected:
        assert result[0]["kind"] == "entry2k"


def test_entry_uses_valid_chronological_kills():
    r = round_([kill(14, victim=6), kill(3, victim=0), kill(10), kill(2, victim=2)])
    assert h.entry(r, players())[0]["events"] == [10, 14]
    r["kills"].append(kill(8, killer=5, victim=1))
    assert h.entry(r, players()) == []


def test_dedupe_keeps_best_kind_and_merges_only_kill_kinds():
    r = clutch_round()
    r["kills"] = [kill(t, victim=5 + i) for i, t in enumerate([10, 31, 35, 41])]
    candidates = h.clutch_of(r, players()) + h.multikills(r, players())
    merged, = h.dedupe(candidates)
    assert (merged["kind"], merged["tags"], merged["summary"]) == ("clutch", ["4k"], "1v3 clutch · 4k")
    util = h.util_plays([{"round": 1, "t": 32, "slot": 2, "value": 12, "ability": "smoke"}], players())
    assert len(h.dedupe(candidates + util)) == 2
    assert candidates[0]["tags"] == []


def test_order_ladder_metric_speed_round_and_start():
    items = [dict(rung=rung, metric=None, speed_s=speed, round=1, t_start=0)
             for rung, speed in [(6, 30), (3, 20), (2, 25), (5, 8), (5, 5)]]
    assert [(x["rung"], x["speed_s"]) for x in h.order(items)] == [(2, 25), (3, 20), (5, 5), (5, 8), (6, 30)]
    items = [dict(rung=9, metric=m, speed_s=s, round=r, t_start=t)
             for m, s, r, t in [(3, 2, 1, 4), (4, 8, 2, 8), (4, 8, 1, 9), (4, 8, 1, 2)]]
    assert [(x["metric"], x["t_start"]) for x in h.order(items)] == [(4, 2), (4, 9), (4, 8), (3, 4)]


def fight(t, gain, deaths=None):
    return {"t0": t - 1, "t1": t + 1, "deaths": [[5, t, "B"]] if deaths is None else deaths,
            "lost_m2": {"A": 100, "B": 100 + gain}, "wipe": False}


def test_fight_top_three_and_kill_matching():
    fights = [fight(t, gain) for t, gain in [(10, 900), (20, 300), (30, -50), (40, 600)]]
    rounds = {1: round_([kill(t) for t in (10, 20, 30, 40)])}
    assert [x["metric"] for x in h.fight_swings({1: fights}, rounds, players())] == [900, 600, 300]
    rounds[1]["kills"][0]["t"] = 9.7
    assert [x["metric"] for x in h.fight_swings({1: fights}, rounds, players())] == [600, 300]
    rounds[1]["kills"][0]["t"] = 10.25
    assert h.fight_swings({1: fights}, rounds, players())[0]["metric"] == 900
    # A second friend's kill ties slot 2; an unmatched slot 0 kill must not win the tie.
    rounds = {1: round_([kill(10), kill(10.1, 1, 6), kill(10.2, 0, 7)])}
    f = fight(10, 900, [[5, 10, "B"], [6, 10.1, "B"]])
    assert h.fight_swings({1: [f]}, rounds, players())[0]["slot"] == 1
    f["deaths"].append([5, 11, "B"])
    rounds[1]["kills"].append(kill(11))
    assert h.fight_swings({1: [f]}, rounds, players())[0]["slot"] == 2


def test_fights_use_friend_group_and_only_predecision_matched_kills():
    p = players()
    p[5]["friend"] = True
    r = round_([kill(10, 5, 0), kill(61, 2, 6)])
    items = h.fight_swings({1: [fight(10, -50, [[0, 10, "A"]]), fight(61, 900, [[6, 61, "B"]])]}, {1: r}, p)
    assert [(x["slot"], x["metric"], x["score"]) for x in items] == [(5, 50, [7, 3])]


def test_util_top_three_scored_positive_friend_casts_and_tail():
    casts = [{"round": 1, "t": 10 + i, "slot": 2, "value": v, "ability": "smoke"}
             for i, v in enumerate([12, -3, 8, 15, 2])]
    casts += [{"round": 1, "t": 20, "slot": 5, "value": 20, "ability": "smoke"},
              {"round": 1, "t": 22, "slot": 2, "value": 99, "ability": "smoke", "scored": False}]
    items = h.util_plays(casts, players())
    assert [x["metric"] for x in items] == [15, 12, 8]
    assert (items[0]["t_start"], items[0]["t_end"], items[0]["events"]) == (10, 19, [13, 17])


@pytest.mark.parametrize('top_casts', [[], [
    {'round': 1, 'i': i, 'slot': 0, 't': 10. + i, 'kind': 'smoke', 'ability': 'Q',
     'scored': True, 'value': 10. + i} for i in range(3)]], ids=['highlight', 'fallback'])
def test_heal_is_eligible_for_util_highlight_and_fallback(top_casts):
    heal = {'round': 1, 'i': None, 'slot': 1, 't': 25., 'kind': 'heal', 'ability': 'E',
            'scored': True, 'value': 5., 'heal': {'hp': 50}}
    result = h.find(inputs(util=[*top_casts, heal], model=None))
    player = result['players']['1']
    assert player['best']['kind'] == 'util' and player['best']['metric'] == 5
    assert player['fallback'] == ('util' if top_casts else None)
    assert any(i['slot'] == 1 and i['kind'] == 'util' for i in result['highlights']) == (not top_casts)


def test_win_chance_largest_positive_and_nonpositive(monkeypatch):
    monkeypatch.setattr(util_model, "p_attack", lambda model, a, d, planted, t, tp: {5: .1, 4: .2, 3: .6, 2: .65}[d])
    r = round_([kill(10), kill(20, victim=6), kill(30, victim=7)],
               alive_steps=[[0, 5, 5], [10, 5, 4], [20, 5, 3], [30, 5, 2]])
    gains = h.win_chance(r, players(), {"cells": {}}, 0)
    assert h.order(gains)[0]["events"] == [20]
    result = h.find(inputs({1: r}))
    item, = [x for x in result["highlights"] if x["kind"] == "winchance"]
    assert item["metric"] == pytest.approx(.4)
    monkeypatch.setattr(util_model, "p_attack", lambda *args: .5)
    assert not [x for x in h.find(inputs({1: r}))["highlights"] if x["kind"] == "winchance"]


def test_win_chance_real_model_clock_plant_and_defending_side(monkeypatch):
    model = {"version": 1, "n": 400, "cells": {
        "5v5:0:t1": [80, 100], "4v5:0:t1": [20, 100],
        "4v5:1:p1": [60, 100], "3v5:1:p1": [40, 100]}}
    calls = []
    real = util_model.p_side

    def spy(model, side, a, d, planted, t, t_plant):
        calls.append((side, a, d, planted, t, t_plant))
        return real(model, side, a, d, planted, t, t_plant)

    monkeypatch.setattr(util_model, "p_side", spy)
    r = round_([kill(15), kill(40, victim=6)], attack="team-2", plant=25,
               alive_steps=[[0, 5, 5], [15, 5, 4], [40, 5, 3]])
    gains = h.win_chance(r, players(), model, 7.5)
    assert calls == [("defense", 5, 5, False, 22.5, 32.5), ("defense", 4, 5, False, 22.5, 32.5),
                     ("defense", 4, 5, True, 47.5, 32.5), ("defense", 3, 5, True, 47.5, 32.5)]
    item = h.order(gains)[0]
    assert item["events"] == [15]
    assert item["metric"] == pytest.approx(60 / 110)
    selected, = [i for i in h.find(inputs({1: r}, model=model, clock_offset=7.5))["highlights"]
                 if i["kind"] == "winchance"]
    assert selected == item


def test_kill_fallback_keeps_each_friends_gain_outside_global_winner(monkeypatch):
    monkeypatch.setattr(util_model, "p_attack", lambda model, a, d, planted, t, tp: {5: .1, 4: .2, 3: .6}[d])
    result = h.find(inputs({1: round_([kill(10, 0), kill(20, 1, 6)])}))
    assert [(i["kind"], i["slot"]) for i in result["highlights"]] == [("winchance", 1)]
    fallback = result["players"]["0"]
    assert fallback["fallback"] == "kill"
    assert fallback["best"]["metric"] == pytest.approx(.1)
    assert fallback["best"]["id"].startswith("r1-kill-0-")
    # With an estimator but no positive gain, a friend's only kill is still their best play.
    monkeypatch.setattr(util_model, "p_attack", lambda *args: .5)
    result = h.find(inputs({1: round_([kill(10, 0)])}))
    assert result["highlights"] == []
    assert result["players"]["0"]["fallback"] == "kill"


def test_win_chance_replays_same_time_deaths_and_extra_life_steps(monkeypatch):
    calls = []

    def spy(model, side, a, d, planted, t, t_plant):
        calls.append((a, d))
        return .5

    monkeypatch.setattr(util_model, "p_side", spy)
    r = round_([kill(10, 0), kill(10, 1, 6), kill(20, 2, 7), kill(61, 2, 8)],
               alive_steps=[[0, 5, 5], [10, 5, 3], [15, 5, 4], [20, 5, 3]])
    h.win_chance(r, players(), {"cells": {}}, 0)
    assert calls == [(5, 5), (5, 4), (5, 4), (5, 3), (5, 4), (5, 3)]


def test_segments_join_clamp_and_clutch_finish():
    item = {"kind": "3k", "events": [10, 11.5, 20], "t_start": 7, "t_end": 22}
    assert h.segments(item) == [[8, 12.5], [18, 21]]
    item.update(kind="clutch", events=[7, 10, 11.5, 20, 23], t_end=24)
    assert h.segments(item) == [[8, 12.5], [18, 24]]
    item.update(kind="3k", events=[10, 14], t_start=9, t_end=14.5)
    assert h.segments(item) == [[9, 14.5]]
    item.update(kind="util", events=[10, 14])
    assert h.segments(item) == [[9, 14.5]]


@pytest.mark.parametrize("field,missing", [("team_fights", "fight"), ("model", "winchance"), ("util", "util")])
def test_missing_inputs_are_independent(field, missing):
    r = round_([kill(10), kill(14, victim=6)])
    result = h.find(inputs({1: r}, **{field: None}))
    assert result["missing"] == [missing]
    assert any(x["kind"] == "entry2k" for x in result["highlights"])
    if field == "model":
        assert all(p["fallback"] != "kill" for p in result["players"].values())


def test_best_plays_fallbacks_and_more():
    p = players()
    p[3]["friend"] = True
    p[4]["friend"] = True
    highlights = []
    for n in range(1, 5):
        highlights += h.multikills(round_([kill(t, 0, 5 + i) for i, t in enumerate([1, 3, 5, 7, 9])], round=n), p)
    gains = h.win_chance(round_([kill(20, 1)]), p, {"cells": {"5v4": [90, 100]}}, 0)
    util = h.util_plays([{"round": 1, "t": 25, "slot": 2, "value": 2, "ability": "smoke"}], p)
    result = h.best_plays(highlights, gains, util, p)
    assert result["0"]["best"]["kind"] == "ace"
    assert result["0"]["fallback"] is None
    assert len(result["0"]["more"]) == 2
    assert result["1"]["best"]["kind"] == result["1"]["fallback"] == "kill"
    assert result["1"]["best"]["rung"] == 12
    assert result["2"]["best"]["kind"] == result["2"]["fallback"] == "util"
    assert result["2"]["best"]["rung"] == 12
    assert result["3"] == {"name": "Synthetic3#TEST", "agent": "Agent3", "best": None, "fallback": None, "more": []}


def test_fallback_util_is_per_friend_even_outside_match_top_three():
    casts = [{"round": 1, "t": i + 10, "slot": 0, "value": 30 - i, "ability": "smoke"} for i in range(3)]
    casts.append({"round": 1, "t": 25, "slot": 1, "value": 1, "ability": "smoke"})
    result = h.find(inputs(util=casts, model=None))
    assert result["players"]["1"]["fallback"] == "util"
    assert result["players"]["1"]["best"]["rung"] == 12
    assert all(x["rung"] < 12 for x in result["highlights"])


def test_current_rounds_only_potg_empty_and_full_payload_contract():
    r = round_([kill(10)], round=3)
    data = inputs({3: r, 5: round_([kill(20)], round=5)}, team_fights={3: [fight(10, 600)]}, model=None)
    original = deepcopy(data)
    result = h.find(data)
    assert result["potg"] == result["highlights"][0]
    assert [(x["kind"], x["round"]) for x in result["highlights"]] == [("fight", 3)]
    assert "fight" not in result["missing"]
    assert h.find(inputs({}))["potg"] is None
    assert data == original
    expected = {"id", "round", "slot", "kind", "rung", "t_start", "t_end", "events", "segments",
                "metric", "speed_s", "summary", "tags", "player", "agent", "score"}
    for item in result["highlights"]:
        assert set(item) == expected
        assert item["id"].startswith("r3-fight-2-")
        assert item["player"] == "Synthetic2"
        assert item["score"] == [3, 7]


def test_windows_clamped_rounded_ids_unique_and_summaries_have_no_names():
    r = clutch_round()
    r["kills"] = [kill(t, victim=5 + i) for i, t in enumerate([1.12345, 31, 35, 41])]
    casts = [{"round": 1, "t": t, "slot": 2, "value": 12, "ability": "Synthetic5"} for t in (1.12345, 40.999)]
    result = h.find(inputs({1: r}, util=casts))
    items = result["highlights"] + [p["best"] for p in result["players"].values() if p["best"]]
    assert len({x["id"] for x in result["highlights"]}) == len(result["highlights"])
    for item in items:
        assert 0 <= item["t_start"] <= item["t_end"] <= 43
        assert all(t == round(t, 2) for t in item["events"] + [item["t_start"], item["t_end"], item["speed_s"]])
        assert all(p["name"].split("#")[0] not in item["summary"] for p in players().values())
        assert all(item["t_start"] <= a <= b <= item["t_end"] for a, b in item["segments"])
