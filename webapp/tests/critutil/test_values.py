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
    died = cast(0, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": True}])
    soft = cast(1, harm=[{"mate": 0, "how": "blind", "t": 8.2, "dur": 2.0, "died": False}])
    v = util_values.value_round(round_data([died, soft], [k]), MODEL)
    assert v["casts"][0]["ledger"] < 0
    assert v["casts"][1]["jobs"] < 0 and v["casts"][1]["ledger"] == 0


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


def test_calls_grade_by_spread():
    assert util_values.call(0.02, 0.01) == "this wins rounds, keep throwing it"
    assert util_values.call(0.004, 0.01) == "keep throwing it"
    assert util_values.call(0.0, 0.01) == "no clear value"
    assert util_values.call(-0.005, 0.01) == "this is costing you"
    assert util_values.shrunk([1.0] * 5, 0.0, 5.0) == 0.5
