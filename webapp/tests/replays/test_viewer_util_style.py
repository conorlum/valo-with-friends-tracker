"""Utility styles, pulse timing and derived zones work without map control."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2]
REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, payload):
    completed = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_util_colour_uses_ability_then_agent():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const rows = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(JSON.stringify(rows.map(([a, style]) => R.utilColorOf(a, style))));
    """, [
        [{"agent": "Jett"}, {"ability": "Cloudburst"}],
        [{"agent": "Brimstone"}, {"ability": "Sky Smoke"}],
        [{"agent": "Nobody"}, {"ability": "X"}],
        [{}, {"agent": "Omen", "ability": "Paranoia"}],
        [{"agent": "Veto"}, {"ability": "X"}],
        [{"agent": "Jett"}, {"agent": "Omen", "ability": "X"}],
        [{"agent": "Astra"}, {"ability": "Nebula  / Dissipate"}],
        [{"agent": "Omen"}, {"ability": "Dark Cover"}],
        [{"agent": "Astra"}, {"ability": "Gravity Well"}],
        [{"agent": "Cypher"}, {"ability": "Cyber Cage"}],
        [{"agent": "Sage"}, {"ability": "Barrier Orb"}],
        [{"agent": "Brimstone"}, {"ability": "Stim Beacon"}],
    ])
    assert actual == ["#f4f7fb", "#c9b08a", None, "#7a5ad6", "#7fc8b8", "#f4f7fb",
                      "#c98bff", "#5c46a8", "#9b6bff", "#d9dee6", "#8ff0e4", "#ff9b54"]


@needs_node
def test_side_colour_uses_team_then_unlinked_group():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      console.log(JSON.stringify([
        R.sideColorOf("green-red", "team-1", "B"),
        R.sideColorOf("green-red", "team-2", "A"),
        R.sideColorOf("green-red", undefined, "A"),
        R.sideColorOf("green-red", undefined, "B"),
        R.sideColorOf("teal-coral", undefined, "A"),
        R.sideColorOf("bogus", undefined, "A"),
        R.sideColorOf("green-red", undefined, undefined),
        R.sideColorOf("green-red", "unknown-team", "A"),
        R.sideColorOf("green-red", null, "unknown-group")
      ]));
    """, None)
    assert actual == ["#5ccf8f", "#d9534f", "#5ccf8f", "#d9534f", "#1fb8c9", "#5ccf8f",
                      None, None, None]


@needs_node
def test_viewer_side_colour_without_control_module():
    actual = run_node(r"""
      Object.defineProperty(global, "ReplayControl", {get() { throw Error("control module accessed"); }});
      const R = require(process.argv[1]);
      const v = Object.create(R.ReplayViewer.prototype);
      v.linked = null;
      v.controlPalette = "purple-yellow";
      v.current = {blob: {players: [{slot: 3, side: "B"}]}};
      v.controlColors = () => { throw Error("controlColors called"); };
      const cssCalls = [];
      v.css = (name, fallback) => { cssCalls.push([name, fallback]); return fallback; };
      const colours = [v.sideColor(3), v.sideColor(9)];
      v.linked = {players: {"3": {team: "team-1"}}};
      colours.push(v.sideColor(3));
      v.controlPalette = "teal-coral";
      colours.push(v.sideColor(3));
      console.log(JSON.stringify({colours, cssCalls}));
    """, None)
    assert actual == {"colours": ["#f2c230", "#77787f", "#9b6bff", "#1fb8c9"],
                      "cssCalls": [["--replay-unknown", "#77787f"]]}


@needs_node
def test_viewer_util_colour_falls_back_to_owner_only_when_unknown():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const v = Object.create(R.ReplayViewer.prototype);
      const slots = [];
      v.ownerColor = slot => { slots.push(slot); return "#123456"; };
      const colours = [v.utilColor({slot: 3, agent: "Jett"}, {ability: "Cloudburst"}),
                       v.utilColor({slot: 9, agent: "Nobody"}, {ability: "X"})];
      console.log(JSON.stringify({colours, slots, veto: R.UTIL_AGENT_COLORS.Veto}));
    """, None)
    assert actual == {"colours": ["#f4f7fb", "#123456"], "slots": [9], "veto": "#7fc8b8"}


SEMTEX = {"kind": "Projectile", "code": "Grenadier", "name": "Q_SemtexBasic", "agent": "KAY/O", "slot": 4,
          "t0": 17.689, "t1": 22.21, "flight": [[17.689, 1, 2, 0], [18.704, 3, 4, 0]]}
FRAGMENT_ZONE = {"kind": "GameObject", "code": "Grenadier", "name": "Q_SemtexBasic_Zone", "agent": "KAY/O",
                 "slot": 4, "u": 3, "v": 4, "t0": 18.704, "t1": 22.21, "zoneOf": 0}


@needs_node
def test_aftershock_pulses_and_empty_schedule():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = {kind: "GameObject", code: "Breach", name: "4_FusionBlast", t0: 35.693, t1: 39.996};
      const early = {...a, t0: 10, t1: 11};
      const open = {...early, t1: null};
      console.log(JSON.stringify({times: R.popTimes(a), until: R.popUntil(a, 0.6),
        visible: [39.0, 39.2].map(t => R.abilitiesAt([a], t, 90).length),
        empty: R.popTimes(early), emptyUntil: R.popUntil(early, 0.6),
        earlyVisible: [10.5, 11.5].map(t => R.abilitiesAt([early], t, 90).length),
        open: R.popTimes(open), fx: R.popTimes({...a, fx: [36, 38]}),
        fxTimed: R.popTimesTimed({...a, fx: [36, 38]}), emptyFx: R.popTimes({...a, fx: []})}));
    """, None)
    assert actual["times"] == pytest.approx([37.893, 38.493], rel=0, abs=1e-9)
    assert actual["until"] == pytest.approx(39.093, rel=0, abs=1e-9)
    assert actual["visible"] == [1, 0]
    assert actual["empty"] == [] and actual["emptyUntil"] == 11
    assert actual["earlyVisible"] == [1, 0]
    assert actual["open"] == pytest.approx([12.2, 12.8], rel=0, abs=1e-9)
    assert actual["fx"] == [36, 38] and actual["fxTimed"] is False
    assert actual["emptyFx"] == pytest.approx(actual["times"], rel=0, abs=1e-9)


@needs_node
def test_mpulse_heal_clips_pulses_but_concuss_keeps_recorded_fx():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const rows = [[24.395, 26.586], [24.395, 25.0], [64.947, 69.947]].map(([t0, t1]) =>
        ({kind: "GameObject", code: "Iris", name: "Heal", t0, t1}));
      rows.push({kind: "GameObject", code: "Iris", name: "Concuss", t0: 32.208, t1: 37,
                 fx: [32.208, 34.209, 36.213]});
      console.log(JSON.stringify(rows.map(a => ({times: R.popTimes(a), timed: R.popTimesTimed(a)}))));
    """, None)
    for row, times in zip(actual, [[24.395, 26.395], [24.395], [64.947, 66.947, 68.947],
                                   [32.208, 34.209, 36.213]], strict=True):
        assert row["times"] == pytest.approx(times, rel=0, abs=1e-9)
    assert [row["timed"] for row in actual] == [True, True, True, False]


@needs_node
def test_timed_pulses_stay_opaque_past_close_with_gone_and_round_end_limits():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const smoke = {kind: "Zone", code: "Wraith", name: "4_Smoke", t0: 10, t1: 25};
      console.log(JSON.stringify({
        visible: [22.4, 22.7, 22.9].map(t => R.abilitiesAt([a], t, 90).length),
        alpha: [22.4, 22.7].map(t => R.abilityAlpha(a, t)),
        gone: [21.09, 21.1, 21.11].map(t => R.abilitiesAt([{...a, gone: 20.5}], t, 90).length),
        end: [21, 21.1].map(t => R.abilitiesAt([a], t, 21).length),
        smoke: [24.9, 25.1].map(t => R.abilitiesAt([smoke], t, 90).length),
        smokeAlpha: R.abilityAlpha(smoke, 25.1)}));
    """, FRAGMENT_ZONE)
    assert actual == {"visible": [1, 1, 0], "alpha": [1, 1], "gone": [1, 1, 0], "end": [1, 0],
                      "smoke": [1, 0], "smokeAlpha": pytest.approx(0.5)}


@needs_node
def test_pulse_styles_and_semtex_projectile_name():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const style = (code, name, kind = "GameObject") => R.abilityStyle({kind, code, name});
      console.log(JSON.stringify({heal: style("Iris", "Heal"), aftershock: style("Breach", "4_FusionBlast"),
        zone: style("Grenadier", "Q_SemtexBasic_Zone"),
        projectile: style("Grenadier", "Q_SemtexBasic", "Projectile")}));
    """, None)
    for name, fields in {
        "heal": {"ability": "M-pulse", "label": "M-pulse (heal)", "shape": "area", "r": 500,
                 "pop": 0.8, "pulses": [0, 2, 4]},
        "aftershock": {"ability": "Aftershock", "shape": "area", "r": 300, "capsule": 1000,
                       "pop": 0.6, "pulses": [2.2, 2.8]},
        "zone": {"ability": "FRAG/ment", "shape": "area", "r": 400, "pop": 0.6,
                 "pulses": [0.5, 1.5, 2.5, 3.5], "part": True, "unlisted": True},
        "projectile": {"ability": "FRAG/ment"},
    }.items():
        assert {key: actual[name].get(key) for key in fields} == fields


@needs_node
def test_fragment_zones_use_landing_and_preserve_inputs():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const rows = [a, {...a, t1: undefined}, {...a, flight: [a.flight[0]]}, {...a, flight: undefined},
                    {...a, flight: []}, {...a, name: "C_Flash"}, {...a, kind: "GameObject"}];
      const before = JSON.stringify(rows);
      const zones = R.fragmentZones(rows);
      console.log(JSON.stringify({zones, pops: R.popTimes(zones[0]), unchanged: before === JSON.stringify(rows)}));
    """, SEMTEX)
    assert actual["zones"][0] == FRAGMENT_ZONE
    assert actual["zones"][1] == {**FRAGMENT_ZONE, "t1": 22.204, "zoneOf": 1}
    assert actual["zones"][2] == {**FRAGMENT_ZONE, "u": 1, "v": 2, "t0": 17.689, "zoneOf": 2}
    assert len(actual["zones"]) == 3
    assert actual["pops"] == pytest.approx([19.204, 20.204, 21.204, 22.204], rel=0, abs=1e-9)
    assert actual["unchanged"] is True


@needs_node
def test_fragment_zone_does_not_add_a_utility_item_or_cast():
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const a = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const v = Object.create(R.ReplayViewer.prototype);
      v.current = {blob: {util: []}, extras: {abilities: [a,
        {kind: "GameObject", code: "Iris", name: "Heal", agent: "Miks", slot: 3, t0: 10, t1: 15}]}};
      const before = v.utilItems();
      v.current.extras.abilities.push(...R.fragmentZones([a]));
      console.log(JSON.stringify({before, after: v.utilItems()}));
    """, SEMTEX)
    assert len(actual["before"]) == 1 and actual["before"][0]["cast"] is True
    assert actual["after"] == actual["before"]


@needs_node
@pytest.mark.parametrize("own_extras", [False, True])
def test_fetch_round_appends_zones_after_indexes_without_mutating_extras(own_extras):
    actual = run_node(r"""
      const R = require(process.argv[1]);
      const {a, ownExtras} = JSON.parse(require("fs").readFileSync(0, "utf8"));
      const {t0, slot, ...rest} = a;
      const blob = {v: 1, round: 1, hz: 16, t_end: 90, tracks: {}, players: [], kills: [],
                    util: [{...rest, k: "ability", t: t0, by: slot}]};
      // Include an actual wall trace and bomb so both reference indexes contain data.
      const abilities = [a,
        {kind: "Projectile", code: "Mage", name: "Q_Wall", slot: 2, t0: 5, t1: 10,
         flight: [[5, 0, 0, 0], [6, 1, 1, 0]]},
        {kind: "GameObject", code: "Mage", name: "Q_WallManager", slot: 2, t0: 5, t1: 10},
        {kind: "Bomb", t0: 30, t1: 60}];
      if (ownExtras) blob.extras = {abilities, shots: [], custom: "preserved"};
      const original = blob.extras || R.extrasFromUtil(blob.util);
      const before = JSON.stringify(original);
      const traces = R.traceWalls(original.abilities), state = R.indexRoundState(blob, original.abilities);
      const v = Object.create(R.ReplayViewer.prototype);
      let loads = 0;
      v.cache = {}; v.options = {loadRound: () => {loads++; return Promise.resolve(blob);}};
      (async () => {
        const round = await v.fetchRound(1), again = await v.fetchRound(1);
        console.log(JSON.stringify({zone: round.extras.abilities.at(-1), length: round.extras.abilities.length,
          originalLength: original.abilities.length, unchanged: before === JSON.stringify(original),
          copied: round.extras !== original, custom: round.extras.custom, traceCount: Object.keys(traces).length,
          tracesSame: JSON.stringify(round.traces) === JSON.stringify(traces),
          stateSame: JSON.stringify(round.state) === JSON.stringify(state), cached: round === again, loads}));
      })().catch(e => {console.error(e); process.exitCode = 1;});
    """, {"a": SEMTEX, "ownExtras": own_extras})
    assert actual["zone"] == FRAGMENT_ZONE
    assert actual["length"] == actual["originalLength"] + 1
    for key in ("unchanged", "copied", "tracesSame", "stateSame", "cached"):
        assert actual[key] is True, key
    assert actual["loads"] == 1
    if own_extras:
        assert actual["custom"] == "preserved" and actual["originalLength"] == 4
        assert actual["traceCount"] == 1
