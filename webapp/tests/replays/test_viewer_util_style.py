"""Utility and side colours are available without the map-control module."""

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
