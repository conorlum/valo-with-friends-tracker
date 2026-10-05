"""The map-features half of the tagger (scripts/control_tagger_core.js `TaggerCore.Features`; plan
docs/superpowers/plans/2026-10-04-map-interaction-tagger.md): its pure model agrees with the Python
contract modules on shared fixtures. Unlike test_control_tagger.py these tests fail, not skip, without Node:
a parity check that silently skips proves nothing."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.replays import map_feature_state as fs

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
CORE = WEBAPP / "scripts" / "control_tagger_core.js"
FIXTURES = HERE.parent / "fixtures" / "control" / "map_features"
NODE = shutil.which("node") or (r"C:\Program Files\nodejs\node.exe" if Path(r"C:\Program Files\nodejs\node.exe").is_file() else None)
CASES = json.loads((FIXTURES / "reducer_cases.json").read_text(encoding="utf-8"))

PRELUDE = """
  const T = require(process.argv[1]);
  const F = T.Features;
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const p = JSON.parse(input);
    process.stdout.write(JSON.stringify(run(p)));
  });
"""


def run_node(body: str, payload):
    assert NODE, "node is required for the map-features parity tests"
    completed = subprocess.run([NODE, "-e", PRELUDE + body, str(CORE)], input=json.dumps(payload), capture_output=True,
                               text=True, encoding="utf-8", timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


REDUCER = """
  function run(p) {
    return p.cases.map(c => {
      try { return {trace: F.run(p.features[c.feature], c.events, c.until)}; }
      catch (e) { return {error: e.name}; }
    });
  }
"""


def test_reducer_traces_match_python_exactly():
    extra = [{"name": "until", "feature": "compound_door", "events": [{"t": 1, "kind": "switch"}], "until": 2},
             {"name": "bad event", "feature": "compound_door", "events": [{"t": 1, "kind": "teleport"}]}]
    cases = CASES["cases"] + extra
    got = run_node(REDUCER, {"features": CASES["features"], "cases": cases})
    for case, js in zip(cases, got):
        feature = CASES["features"][case["feature"]]
        try:
            expected = {"trace": fs.run(feature, case["events"], case.get("until"))}
        except fs.FeatureStateError:
            expected = {"error": "FeatureStateError"}
        assert js == expected, case["name"]
