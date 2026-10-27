"""The corpus build on the v2 baseline (replay_util.build_from_rounds), on toy rounds (plan 5, W18, D16)."""

import json

import pytest

from app.services import replay_util, util_values
from tests.critutil.test_values import MODEL, kill, v2_cast, v2_round

FRIEND = 101            # slot 1, attack
OTHER = 201             # slot 6, defense
N_MATCHES = 20
FRIEND_MATCHES = range(1, 7)
OTHER_MATCHES = range(7, 13)


def toy_cast(slot, side, t=8.0):
    return v2_cast(0, t=t, seen_end=11.0, side=side, by=slot, kills=[], enemies=[], harm=[])


def toy_corpus():
    stored, ctx = {}, {}
    for m in range(1, N_MATCHES + 1):
        casts = []
        if m in FRIEND_MATCHES:
            casts.append(toy_cast(1, "attack"))
        if m in OTHER_MATCHES:
            casts.append(toy_cast(6, "defense"))
        kt = 14.0 + (m % 3) * 4
        rd = v2_round(casts, kills=[kill(kt, 5, 0, [5, 5], k_side="defense", v_side="attack")], t_decided=40.0)
        stored[m] = {1: rd}
        ctx[m] = {"map": "Haven", "uuid": f"u{m}", "match_id": m, "offset": 0.0, "on_known": True,
                  "players": {0: {"player_id": 100, "agent": "Breach", "team": "Red"},
                              1: {"player_id": FRIEND, "agent": "Breach", "team": "Red"},
                              5: {"player_id": 200, "agent": "Sage", "team": "Blue"},
                              6: {"player_id": OTHER, "agent": "Sage", "team": "Blue"}},
                  "rounds": {1: {"winner": "attack"}}, "loadout": {}}
    return stored, ctx


def fake_gate(passing):
    def one():
        return {L: {"n": 100, "se_cell": 1.0, "se_ma": 2.0, "pass": passing, "diff_mean": 0.0, "diff_sd": 0.0,
                    "matches": 5} for L in range(1, 46)}
    return lambda tables, min_n=util_values.BASE_MIN_N: {
        "all": one(), "yours": one(), "device": one(), "by_type": {"all": {"flash": one()}, "yours": {"flash": one()}}}


@pytest.fixture
def passing(monkeypatch):
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(True))


def build():
    stored, ctx = toy_corpus()
    return replay_util.build_from_rounds(stored, ctx, MODEL, {FRIEND})


def rows_of(out, pid):
    return [r for r in out["rows"] if r["player_id"] == pid and r["kind"] != "heal"]


def test_a_friends_cast_gets_both_modes_and_a_non_friends_only_all(passing):
    out = build()
    f, o = rows_of(out, FRIEND), rows_of(out, OTHER)
    assert len(f) == 6 and len(o) == 6
    for r in f:
        assert r["lift_all"] is not None and r["lift_yours"] is not None
        assert r["gate_all"] is True and r["gate_yours"] is True and r["lift"] == r["lift_all"]
        assert r["type"] == "flash" and r["L"] == 4 and r["swing"] is not None
        assert r["lift_device"] is None and r["gate_device"] is None
    for r in o:
        assert r["lift_all"] is not None and r["lift_yours"] is None and r["gate_yours"] is None


def test_yours_leaves_out_all_of_the_lineups_matches(passing):
    out = build()
    r = rows_of(out, FRIEND)[0]
    stored, ctx = toy_corpus()
    items = [{"match": m, "map": "Haven", "data": {**stored[m][1], "clock_offset": 0.0, "on_known": True},
              "player_of": {s: p["player_id"] for s, p in ctx[m]["players"].items()}} for m in stored]
    tabs = util_values.match_tables(items, MODEL, {FRIEND})
    keys = util_values.levels(items[0]["data"], "attack", 8.0)
    want = util_values.lift_v2(util_values.totals(tabs["yours"][FRIEND]), tabs["yours"][FRIEND], "flash", keys, 4,
                               r["swing"], leave_out=set(FRIEND_MATCHES))
    assert r["yours_n"] == want["n"] and r["yours_level"] == want["level"]
    assert r["lift_yours"] == pytest.approx(want["lift"], abs=1e-4)
    # the all-players baseline leaves out only the cast's own match
    want_all = util_values.lift_v2(util_values.totals(tabs["all"]), tabs["all"], "flash", keys, 4, r["swing"],
                                   leave_out={r["match"]})
    assert r["base_n"] == want_all["n"] and r["lift_all"] == pytest.approx(want_all["lift"], abs=1e-4)


def test_the_cross_fitted_prior_leaves_out_the_lineups_matches(passing):
    out = build()
    sums = out["prior_sums"]["all"]["utility"]
    assert set(sums) == {str(m) for m in list(FRIEND_MATCHES) + list(OTHER_MATCHES)}
    f, o = rows_of(out, FRIEND), rows_of(out, OTHER)
    for m, (n, s) in sums.items():
        want = [r["lift_all"] for r in f + o if str(r["match"]) == m]
        assert n == len(want) and s == pytest.approx(sum(want))
    assert set(out["prior_sums"]["yours"]["utility"]) == {str(m) for m in FRIEND_MATCHES}
    assert out["ability_prior"]["utility"] == pytest.approx(sum(r["lift_all"] for r in f + o) / 12)
    # the friend lineup's prior is the other lineup's mean (its own six matches are out), and the reverse
    score_f = replay_util.lineup_score([r["lift_all"] for r in f], sum(r["lift_all"] for r in o) / 6)
    score_o = replay_util.lineup_score([r["lift_all"] for r in o], sum(r["lift_all"] for r in f) / 6)
    want = (sum(s * s for s in (score_f, score_o)) / 2) ** 0.5
    assert out["spread_by_mode"]["all"] == pytest.approx(want) and out["spread"] == out["spread_by_mode"]["all"]
    # Yours: one lineup, everything of it out, so the prior is 0
    yours = replay_util.lineup_score([r["lift_yours"] for r in f], 0.0)
    assert out["spread_by_mode"]["yours"] == pytest.approx(abs(yours))


def test_a_cast_at_a_failing_length_keeps_its_lift_but_enters_nothing(monkeypatch):
    monkeypatch.setattr(util_values, "gate_all_modes", fake_gate(False))
    out = build()
    for r in rows_of(out, FRIEND) + rows_of(out, OTHER):
        assert r["lift_all"] is not None and r["gate_all"] is False and r["lift"] is None
        assert r["gate_yours"] in (False, None)
    assert out["prior_sums"] == {"all": {}, "yours": {}} and out["ability_prior"] == {}
    assert out["spread"] == 0.0 and out["spread_by_mode"] == {"all": 0.0, "yours": 0.0}


def test_the_build_keeps_the_pages_keys_and_dumps_as_json(passing):
    out = build()
    json.dumps(out)
    for k in ("rows", "fit", "ability_prior", "spread", "overlap", "matches", "estimator", "prior_sums",
              "spread_by_mode", "gate", "same_type_overlap"):
        assert k in out
    assert "baseline" not in out and "personal" not in out
    assert out["matches"] == list(range(1, N_MATCHES + 1))
    assert len(out["gate"]["all"]) == 45 and len(out["gate"]["yours"]) == 45 and len(out["gate"]["device"]) == 45
    assert set(out["gate"]["by_type"]) == {"all", "yours"}
    assert len(out["same_type_overlap"]["flash"]) == 46


def test_the_real_gate_builds_and_dumps_too():
    out = build()
    json.dumps(out)
    assert all(isinstance(g["pass"], bool) for g in out["gate"]["all"])
    rows = rows_of(out, FRIEND)
    assert all((r["lift"] is None) == (not r["gate_all"]) for r in rows)


def test_on_known_follows_the_condense_revision():
    on = replay_util.on_known
    assert on("p1.c22.f1.a1") and on("p1.c23.f1.a1") and not on("p1.c21.f1.a1") and not on("junk")
    assert replay_util.ON_SPANS_FROM == 22


def device_corpus():
    stored, ctx = toy_corpus()
    for m in stored:
        stored[m] = {1: v2_round([], kills=[kill(14.0 + (m % 3) * 4, 5, 0, [5, 5], k_side="defense", v_side="attack")],
                                 t_decided=40.0)}
        stored[m][1]["agents"] = {"1": "Test"}          # the device's agent on attack: its absence is a comparison
    plac = v2_cast(0, t=8.0, seen_end=11.0, type_="placeable", side="attack", by=1, kills=[], enemies=[], harm=[],
                   ability_id="Test/Device")
    stored[1][1] = v2_round([plac], kills=[kill(14.0, 5, 0, [5, 5], k_side="defense", v_side="attack")],
                            t_decided=40.0)
    # the cast's own match also has a round without the device, whose moments differ from every other match's
    stored[1][2] = v2_round([], kills=[kill(30.0, 5, 0, [5, 5], k_side="defense", v_side="attack")], t_decided=40.0)
    for n in (1, 2):
        stored[1][n]["agents"] = {"1": "Test"}
    ctx[1]["rounds"][2] = {"winner": "attack"}
    return stored, ctx


def test_a_placeables_device_lift_leaves_out_only_its_own_match(passing):
    stored, ctx = device_corpus()
    out = replay_util.build_from_rounds(stored, ctx, MODEL, {FRIEND})
    (r,) = [r for r in out["rows"] if r["type"] == "placeable"]
    assert r["lift_device"] is not None and r["device_level"] is not None and r["device_n"] >= 30
    assert r["gate_device"] is True
    items = []
    for m in stored:
        for n, rd in stored[m].items():
            items.append({"match": m, "map": "Haven", "data": {**rd, "clock_offset": 0.0, "on_known": True},
                          "player_of": {s: p["player_id"] for s, p in ctx[m]["players"].items()}})
    per = util_values.match_tables(items, MODEL, {FRIEND})["device"]["Test/Device|Haven|attack"]
    wrapped = {m: {"device": cells} for m, cells in per.items()}
    assert 1 in wrapped, "the own match has a round without the device"
    keys = util_values.levels(items[0]["data"], "attack", 8.0)
    want = util_values.lift_v2(util_values.totals(wrapped), wrapped, "device", keys, r["L"], r["swing"], leave_out={1})
    assert r["device_n"] == want["n"] and r["device_level"] == want["level"]
    assert r["lift_device"] == pytest.approx(want["lift"], abs=1e-4)
    keep = util_values.lift_v2(util_values.totals(wrapped), wrapped, "device", keys, r["L"], r["swing"], leave_out=())
    assert keep["n"] != want["n"] or abs(keep["lift"] - want["lift"]) > 1e-3, "the own match would change the number"
