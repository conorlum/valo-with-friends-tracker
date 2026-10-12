"""P04: the viewer's state selectors over a round's `player_state` (app/replays/player_state.py, version 1), its
conditions (status_intervals.py) and the planted spike's ability row: `indexRoundState` indexes one loaded round
once, and `playerConditionsAt`, `healthAt` and `spikeStateAt` read it at any t, in any order, never past t. A
parity test runs the same subsection through player_state.py's queries and the JS mirrors. Synthetic fixtures
only. The JS checks need Node; they skip without it."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.replays import player_state as ps_py
from app.replays import status_intervals as si

WEBAPP = Path(__file__).resolve().parents[2]
REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, payload):
    completed = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# One driver: index the payload's blob once, then answer its queries in the order given.
ASK = """
  const R = require(process.argv[1]);
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    const abilities = p.abilities || R.extrasFromUtil((p.blob || {}).util).abilities;
    const idx = R.indexRoundState(p.blob, abilities);
    const run = q => {
      const [fn, a, b, c] = q;
      if (fn === "health") return R.healthAt(idx, a, b);
      if (fn === "spike") return R.spikeStateAt(idx, a);
      if (fn === "hud") return R.spikeAt(abilities, a);
      if (fn === "cond") return R.playerConditionsAt(idx, a, b).map(x => [x.c, x.t0, x.t1, x.est]);
      if (fn === "vitals") return R.vitalsAt(idx, a, b, c);
      if (fn === "damaged") return R.damagedBy(idx, a, b);
      if (fn === "pspike") return R.playerSpikeAt(idx, a);
      if (fn === "pct") return R.healthPct(R.vitalsAt(idx, a, b, c));
      if (fn === "valid") return R.validatePlayerState((p.blob || {}).player_state);
      throw new Error("unknown query " + fn);
    };
    process.stdout.write(JSON.stringify((p.queries || []).map(run)));
  });"""


def ask(blob, queries, abilities=None):
    return run_node(ASK, {"blob": blob, "queries": queries, "abilities": abilities})


def vit(t, life, hp, sh, mhp=100, msh=50):
    return {"t": t, "life": life, "hp": hp, "sh": sh, "mhp": mhp, "msh": msh}


BOMB = {"k": "ability", "kind": "Bomb", "code": "", "name": "Spike", "by": 1, "t": 39.01, "t1": 100.0,
        "u": 100, "v": 200, "defuses": [[50.0, 57.0, 0, True]]}

SPIKE = [
    {"t": -30, "s": "unknown"}, {"t": -20, "s": "carried"}, {"t": 10, "s": "dropped"},
    {"t": 15, "s": "carried", "slot": 2},
    {"t": 20, "s": "dropped"}, {"t": 20, "s": "carried", "slot": 2},             # a pickup at the drop's time wins
    {"t": 22, "s": "carried", "slot": 2}, {"t": 22, "s": "carried", "slot": 3},  # contradictory: unknown
    {"t": 23, "s": "carried", "slot": 2},
    {"t": 25, "s": "planting", "slot": 2}, {"t": 27, "s": "carried", "slot": 2},  # a cancelled plant
    {"t": 31, "s": "carried", "slot": 1},                                         # slot 2 died at 30, no drop row
    {"t": 39, "s": "planted", "u": 100, "v": 200}, {"t": 39, "s": "planting", "slot": 1},
    {"t": 39, "s": "carried", "slot": 1},                                         # the completed plant wins
    {"t": 57.01, "s": "defused"},
]

PLAYER_STATE = {
    "version": 1,
    "vitals": {
        "0": [vit(5, 0, 80, 50), vit(5, 0, 70, 50), vit(39.9, 0, 10, 0), vit(60, 1, 60, 0, msh=0)],
        "1": [vit(12, 0, 100, 10), vit(14, 0, 100, 50)],                      # shield-only damage, then full
        "2": [vit(8, 0, 50, None, msh=None)],                                  # shield not proven
        "4": [vit(3.0, 0, 90, 25, msh=25), vit(3.0625, 0, 60, 0, msh=25)],      # 1/16 s apart; no alive row
        "5": [{"t": 1, "life": 0, "hp": 50, "sh": 10, "mhp": 100},             # no msh: dropped
              {"t": 2, "life": 0, "hp": 50, "sh": None, "mhp": 100, "msh": 25},  # one null shield: dropped
              {"t": 2.5, "life": 0, "hp": 50, "sh": 0, "mhp": 0, "msh": 25},      # mhp 0: dropped
              "junk"],
        "11": [vit(1, 0, 1, 1)],                                               # not a slot
    },
    "damage_taken": {"0": [5, 39.9, 60], "1": [12, 14], "2": [8], "3": [7], "4": [3.0, 3.0625, 3.0625], "5": [1]},
    "spike": SPIKE,
}

UTIL = [
    {"k": "flash", "by": 6, "t": 9.5, "ability": "phoenix_x", "hits": [[1, 10.0, 2.0], [1, 11.5, 1.0]]},
    {"k": "nearsight", "by": 6, "t": 20.0, "hits": [[3, 20.0, 0]]},                    # zero duration: none
    {"k": "status", "by": 7, "target": 1, "t": 11.0, "t1": 13.0, "status": "slowed"},
    {"k": "status", "by": 7, "target": 3, "t": 30.0, "status": "concussed"},           # no end: estimated
    {"k": "flash", "by": 6, "t": 39.0, "hits": [[0, 39.5, 2.0], [0, 45.0, 2.0]]},       # cut at death; dead: none
    {"k": "shot", "by": 2, "t": 16.5, "gun": "Vandal"}, {"k": "shot", "by": 2, "t": 17.5, "gun": "Classic"},
    {"k": "ability", "kind": "Projectile", "code": "Wraith", "name": "Q", "by": 2, "t": 18.0, "t1": 19.0,
     "u": 1, "v": 1},
    BOMB,
]

ALIVE = {"0": [[0, 40, "start"], [50, None, "revive"]], "1": [[0, None, "start"]], "2": [[0, 30, "start"]],
         "3": [[0, None, "start"]], "5": [[0, None, "start"]]}


def make_blob(player_state=PLAYER_STATE, util=UTIL, n=1):
    blob = {"v": 1, "round": n, "hz": 16, "t_end": 100.0, "tracks": {}, "players": [], "kills": [],
            "alive": ALIVE, "util": util}
    if player_state is not None:
        blob["player_state"] = player_state
    return blob


def brief(health):
    return (health["state"], health["visible"], None if health["pct"] is None else round(health["pct"], 6))


# ------------------------------------------------------------------------------------------------------- health


HEALTH_CASES = [
    (0, 4.99, ("undamaged", False, None)),
    (0, 5, ("known", True, 80.0)),             # equal time: the later source row wins (120/150)
    (0, 39.95, ("known", True, round(1000 / 150, 6))),
    (0, 40, ("known", True, round(1000 / 150, 6))),    # the death instant is still the life
    (0, 45, ("dead", False, None)),
    (0, 55, ("unavailable", True, None)),      # revived: no hit in the new life yet, the round's damage shows it
    (0, 60, ("known", True, 60.0)),            # the new life's own maxima (no armor)
    (1, 11.99, ("undamaged", False, None)),
    (1, 13, ("known", True, round(11000 / 150, 6))),   # shield-only damage
    (1, 14, ("known", True, 100.0)),           # back to full: the bar stays
    (1, 99, ("known", True, 100.0)),
    (2, 9, ("unavailable", True, None)),       # shield not proven: no number
    (2, 31, ("dead", False, None)),
    (3, 6.9, ("undamaged", False, None)),
    (3, 7, ("unavailable", True, None)),       # damage with no vitals timeline
    (4, 2.99, ("undamaged", False, None)),
    (4, 3.03, ("known", True, 92.0)),          # between 1/16 s samples: the earlier one, never interpolated
    (4, 3.0625, ("known", True, 48.0)),
    (5, 3, ("unavailable", True, None)),       # every vitals event was malformed
    (6, 50, ("undamaged", False, None)),
]


@needs_node
def test_health_before_during_and_after_hits_revival_full_heal_and_missing_shield():
    got = ask(make_blob(), [["health", s, t] for s, t, _ in HEALTH_CASES])
    assert [brief(h) for h in got] == [want for _, _, want in HEALTH_CASES]
    unproven = got[[(s, t) for s, t, _ in HEALTH_CASES].index((2, 9))]
    assert unproven["hp"] == 50 and unproven["mhp"] == 100 and unproven["sh"] is None   # HP is still known
    revived = got[[(s, t) for s, t, _ in HEALTH_CASES].index((0, 55))]
    assert revived["life"] == 1 and revived["hp"] is None                               # nothing from life 0


@needs_node
def test_answers_do_not_depend_on_query_order():
    times = [x / 8 for x in range(-80, 820)]
    queries = [["health", s, t] for s in range(6) for t in times] + [["spike", t] for t in times] \
        + [["cond", s, t] for s in range(4) for t in times]
    forward = ask(make_blob(), queries)
    rng = random.Random(4)
    order = list(range(len(queries)))
    rng.shuffle(order)
    shuffled = ask(make_blob(), [queries[i] for i in order] + [queries[i] for i in reversed(order)])
    n = len(queries)
    assert [shuffled[k] for k in sorted(range(n), key=lambda k: order[k])] == forward
    assert [shuffled[n + k] for k in sorted(range(n), key=lambda k: order[n - 1 - k])] == forward
    assert ask(make_blob(), list(reversed(queries))) == list(reversed(forward))


@needs_node
def test_missing_unsupported_and_malformed_player_state_is_unavailable_never_a_number():
    bad = [None, "x", [], {"version": 2, **{k: v for k, v in PLAYER_STATE.items() if k != "version"}},
           {"version": True}, {**PLAYER_STATE, "vitals": []}, {**PLAYER_STATE, "damage_taken": None},
           {**PLAYER_STATE, "spike": {}}]
    for player_state in bad:
        got = ask(make_blob(player_state=player_state), [["valid"], ["health", 0, 5], ["health", 1, 13],
                                                         ["spike", 26], ["spike", 52], ["cond", 1, 10]])
        assert got[0] is None, player_state
        assert [brief(h) for h in got[1:3]] == [("none", False, None)] * 2
        assert got[3]["state"] == "unknown" and got[3]["source"] == "none"
        assert got[4]["state"] == "planted" and got[4]["source"] == "ability"
        assert got[5] == [["blinded", 10.0, 12.5, False]]       # conditions come from util, not player_state
    # one part missing: the others still answer
    no_vitals = {k: v for k, v in PLAYER_STATE.items() if k != "vitals"}
    got = ask(make_blob(player_state=no_vitals), [["health", 0, 5], ["spike", 16]])
    assert brief(got[0]) == ("unavailable", True, None)
    assert got[1]["state"] == "carried" and got[1]["slot"] == 2
    no_damage = {k: v for k, v in PLAYER_STATE.items() if k != "damage_taken"}
    assert brief(ask(make_blob(player_state=no_damage), [["health", 0, 5]])[0]) == ("none", False, None)
    no_spike = {k: v for k, v in PLAYER_STATE.items() if k != "spike"}
    got = ask(make_blob(player_state=no_spike), [["spike", 16], ["health", 0, 5]])
    assert got[0]["state"] == "unknown" and got[0]["source"] == "none" and brief(got[1])[0] == "known"
    # garbage blobs never throw
    for blob in (None, {}, {"player_state": {"version": 1, "vitals": {"0": "x"}, "spike": [1, None, {"s": "zz"}]}}):
        got = ask(blob, [["health", 0, 5], ["spike", 5], ["cond", 0, 5]], abilities=[])
        assert got[0]["visible"] is False and got[1]["state"] == "unknown" and got[2] == []


# -------------------------------------------------------------------------------------------------------- spike


def spike_brief(s):
    return (s["state"], s["slot"], s["u"], s["v"], s["source"])


SPIKE_CASES = [
    (-40, ("unknown", None, None, None, "player_state")),
    (-25, ("unknown", None, None, None, "player_state")),
    (-10, ("carried", None, None, None, "player_state")),     # carried, carrier not proven
    (10, ("dropped", None, None, None, "player_state")),      # no position: no marker
    (16, ("carried", 2, None, None, "player_state")),
    (16.5, ("carried", 2, None, None, "player_state")),       # weapon switches and casts don't touch the carrier
    (17.5, ("carried", 2, None, None, "player_state")),
    (18.5, ("carried", 2, None, None, "player_state")),
    (20, ("carried", 2, None, None, "player_state")),         # pickup and drop at one time: the later row
    (22, ("unknown", None, None, None, "player_state")),      # two carriers at one time
    (22.5, ("unknown", None, None, None, "player_state")),
    (23, ("carried", 2, None, None, "player_state")),
    (26, ("planting", 2, None, None, "player_state")),
    (27, ("carried", 2, None, None, "player_state")),         # the plant was cancelled
    (30, ("carried", 2, None, None, "player_state")),
    (30.5, ("dropped", None, None, None, "player_state")),    # the carrier died with no drop row
    (31, ("carried", 1, None, None, "player_state")),
    (39, ("planted", None, 100, 200, "player_state")),        # a completed plant beats planting/carried
    (39.005, ("planted", None, 100, 200, "player_state")),
    (39.01, ("planted", 1, 100, 200, "ability")),             # the ability row's HUD from its spawn
    (52, ("planted", 1, 100, 200, "ability")),
    (57, ("defused", 0, 100, 200, "ability")),
    (57.02, ("defused", 0, 100, 200, "ability")),
]


@needs_node
def test_spike_state_drop_pickup_cancelled_plant_carrier_death_and_equal_times():
    got = ask(make_blob(), [["spike", t] for t, _ in SPIKE_CASES] + [["hud", t] for t, _ in SPIKE_CASES])
    n = len(SPIKE_CASES)
    assert [spike_brief(s) for s in got[:n]] == [want for _, want in SPIKE_CASES]
    for (t, _), state, hud in zip(SPIKE_CASES, got[:n], got[n:]):
        assert state["hud"] == (hud if state["source"] == "ability" else None), t
    assert got[[t for t, _ in SPIKE_CASES].index(52)]["hud"]["defusing"]["slot"] == 0


@needs_node
def test_a_round_without_player_state_gives_exactly_todays_planted_hud():
    times = [x / 4 for x in range(-40, 440)]
    for player_state in (None, {"version": 9}):
        got = ask(make_blob(player_state=player_state), [["spike", t] for t in times] + [["hud", t] for t in times])
        for t, state, hud in zip(times, got[:len(times)], got[len(times):]):
            assert state["hud"] == hud, t
            if hud is None:
                assert spike_brief(state) == ("unknown", None, None, None, "none"), t
            else:
                assert state["source"] == "ability" and state["state"] in ("planted", "defused"), t
    # a detonation, from the ability row alone
    bomb = {**BOMB, "t": 10.0, "defuses": []}
    got = ask(make_blob(player_state=None, util=[bomb]), [["spike", 54.9], ["spike", 55], ["hud", 55]])
    assert got[0]["state"] == "planted" and got[1]["state"] == "detonated" and got[1]["hud"] == got[2]
    assert got[2]["exploded"] is True


# --------------------------------------------------------------------------------------------------- conditions


COND_CASES = [
    (1, 9.99, []), (1, 10, [["blinded", 10.0, 12.5, False]]),
    (1, 11, [["blinded", 10.0, 12.5, False], ["slowed", 11.0, 13.0, False]]),
    (1, 12.5, [["slowed", 11.0, 13.0, False]]), (1, 13, []),        # [start, end): the end is not in
    (0, 39.99, [["blinded", 39.5, 40.0, False]]), (0, 40, []), (0, 46, []),   # cut at death; none while dead
    (3, 20, []),                                                    # a zero-length hit gives nothing
    (3, 30.99, [["concussed", 30.0, 31.0, True]]), (3, 31, []),     # no end: the policy's estimate, marked
]


@needs_node
def test_player_conditions_are_half_open_cut_at_death_and_marked_when_estimated():
    got = ask(make_blob(), [["cond", s, t] for s, t, _ in COND_CASES])
    assert got == [want for _, _, want in COND_CASES]


@needs_node
def test_the_index_is_frozen_built_once_per_round_and_round_switching_carries_nothing():
    other = {"version": 1, "vitals": {"1": [vit(2, 0, 30, 0)]}, "damage_taken": {"1": [2]},
             "spike": [{"t": 0, "s": "carried", "slot": 4}]}
    script = """
      const R = require(process.argv[1]);
      const pending = {};
      const v = Object.create(R.ReplayViewer.prototype);
      for (const name of ["renderStrip", "renderTicks", "renderBanner", "renderFeed", "renderAnalysis",
                          "renderUtilList", "refreshControl", "renderControlTable", "refreshGaps",
                          "updateControls", "draw"]) v[name] = function () {};
      let input = ""; process.stdin.on("data", d => input += d).on("end", async () => {
        const p = JSON.parse(input);
        Object.assign(v, {rounds: [1, 2], cache: {}, speed: 1, t: 0, playing: false, current: null, lastFrame: null,
                          options: {loadRound: n => new Promise(ok => { pending[n] = ok; })}});
        const tick = () => new Promise(r => setTimeout(r, 0));
        const look = () => ({round: v.current.blob.round, h0: R.healthAt(v.current.state, 0, 5),
                             h1: R.healthAt(v.current.state, 1, 13), s: R.spikeStateAt(v.current.state, 16)});
        const out = {};
        let shown = v.showRound(1); await tick(); pending[1](p.blobs[0]); await shown;
        const first = v.current.state;
        out.frozen = [Object.isFrozen(first), Object.isFrozen(first.vitals), Object.isFrozen(first.vitals["0"]),
                      Object.isFrozen(first.conditions), Object.isFrozen(first.spike)];
        out.one = look();
        first.vitals = null; try { first.vitals["0"].all.ev[0].hp = 1; } catch (e) {}
        out.one_after_poke = look();
        shown = v.showRound(2); await tick(); pending[2](p.blobs[1]); await shown;
        out.two = look();
        shown = v.showRound(1); await tick(); await shown;
        out.back = look();
        out.same_index = v.current.state === first;
        process.stdout.write(JSON.stringify(out));
      });"""
    got = run_node(script, {"blobs": [make_blob(n=1), make_blob(player_state=other, util=[], n=2)]})
    assert got["frozen"] == [True] * 5
    assert got["one"] == got["one_after_poke"] == got["back"] and got["same_index"] is True
    assert brief(got["one"]["h0"]) == ("known", True, 80.0)
    assert got["two"]["round"] == 2
    assert brief(got["two"]["h0"]) == ("undamaged", False, None)       # nothing from round 1
    assert brief(got["two"]["h1"]) == ("known", True, 20.0)
    assert got["two"]["s"]["state"] == "carried" and got["two"]["s"]["slot"] == 4


@needs_node
def test_a_stale_round_load_cannot_install_its_state():
    # The existing loadSeq token: round 1 is asked for, then round 2; round 1 lands last and is ignored.
    other = {"version": 1, "damage_taken": {"0": [1]}, "spike": [{"t": 0, "s": "dropped"}]}
    script = """
      const R = require(process.argv[1]);
      const pending = {};
      const v = Object.create(R.ReplayViewer.prototype);
      for (const name of ["renderStrip", "renderTicks", "renderBanner", "renderFeed", "renderAnalysis",
                          "renderUtilList", "refreshControl", "renderControlTable", "refreshGaps",
                          "updateControls", "draw"]) v[name] = function () {};
      let input = ""; process.stdin.on("data", d => input += d).on("end", async () => {
        const p = JSON.parse(input);
        Object.assign(v, {rounds: [1, 2], cache: {}, speed: 1, t: 0, playing: false, current: null, lastFrame: null,
                          options: {loadRound: n => new Promise(ok => { pending[n] = ok; })}});
        const tick = () => new Promise(r => setTimeout(r, 0));
        const a = v.showRound(1), b = v.showRound(2);
        await tick();
        pending[2](p.blobs[1]); await b;
        pending[1](p.blobs[0]); const stale = await a;
        await tick();
        process.stdout.write(JSON.stringify({round: v.current.blob.round, number: v.number,
          stale_round: stale.blob.round, health: R.healthAt(v.current.state, 0, 5),
          spike: R.spikeStateAt(v.current.state, 16)}));
      });"""
    got = run_node(script, {"blobs": [make_blob(n=1), make_blob(player_state=other, util=[], n=2)]})
    assert got["stale_round"] == 1                                     # it did land
    assert got["round"] == 2 and got["number"] == 2                    # and installed nothing
    assert brief(got["health"]) == ("unavailable", True, None)
    assert got["spike"]["state"] == "dropped"


# ------------------------------------------------------------------------------------------------------- parity


@needs_node
def test_the_js_selectors_match_player_state_py_at_many_times():
    expected_valid = ps_py.validate(PLAYER_STATE)
    events = sorted({e["t"] for ev in PLAYER_STATE["vitals"].values() for e in ev if isinstance(e, dict)}
                    | {e["t"] for e in SPIKE} | {t for ts in PLAYER_STATE["damage_taken"].values() for t in ts})
    times = sorted(set([x / 32 for x in range(-1300, 2300)] + events + [t - 1e-9 for t in events]
                       + [t + 1e-9 for t in events]))
    queries = [["valid"]]
    for t in times:
        queries.append(["pspike", t])
        for slot in range(7):
            queries.append(["damaged", slot, t])
            for life in (None, 0, 1):
                queries.append(["vitals", slot, t, life])
                queries.append(["pct", slot, t, life])
    got = ask(make_blob(), queries)
    assert got[0] == json.loads(json.dumps(expected_valid))
    want = []
    for t in times:
        want.append(ps_py.spike_at(expected_valid, t))
        for slot in range(7):
            want.append(ps_py.damaged_by(expected_valid, slot, t))
            for life in (None, 0, 1):
                event = ps_py.vitals_at(expected_valid, slot, t, life)
                want.append(event)
                want.append(ps_py.health_pct(event))
    assert len(got) - 1 == len(want)
    for i, (mine, theirs) in enumerate(zip(got[1:], want)):
        if isinstance(theirs, float):
            assert mine == pytest.approx(theirs, abs=1e-9), queries[i + 1]
        else:
            assert mine == json.loads(json.dumps(theirs)), queries[i + 1]


@needs_node
def test_the_js_contract_constants_are_player_state_py_s():
    got = run_node("""const R = require(process.argv[1]); process.stdout.write(JSON.stringify(R.PLAYER_STATE));""", None)
    assert got == {"version": ps_py.VERSION, "spike_states": list(ps_py.SPIKE_STATES), "t_min": ps_py.T_MIN,
                   "t_max": ps_py.T_MAX, "value_max": ps_py.VALUE_MAX, "life_max": ps_py.LIFE_MAX,
                   "max_events": ps_py.MAX_EVENTS, "max_spike": ps_py.MAX_SPIKE, "uv_max": ps_py.UV_MAX}


@needs_node
def test_the_js_conditions_at_match_status_intervals_py():
    conditions = si.normalize_conditions(UTIL, ALIVE, 100.0)
    times = sorted(set([x / 32 for x in range(-64, 3300)] + [9.5, 10.0, 11.5, 12.5, 13.0, 39.5, 40.0, 45.0, 47.0]))
    queries = [["cond", slot, t] for t in times for slot in range(5)]
    got = ask(make_blob(), queries)
    want = [[[x["c"], x["t0"], x["t1"], x["est"]] for x in si.conditions_at(conditions, slot, t)]
            for t in times for slot in range(5)]
    assert got == want
