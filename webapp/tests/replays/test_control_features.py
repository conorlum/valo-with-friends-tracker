"""Map features in control (app/control/features.py; plan docs/superpowers/plans/2026-10-04-map-interaction-tagger.md,
M0 and M5): nothing changes for a map without enabled features, and the compiled feature contracts behave as
the plan's synthetic fixtures require."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import map_feature_legacy  # noqa: E402


def test_every_committed_maps_control_inputs_and_fingerprint_are_as_recorded():
    recorded = json.loads(map_feature_legacy.PATH.read_text(encoding="utf-8"))
    assert recorded["maps"], "the snapshot should cover the committed maps"
    assert map_feature_legacy.snapshot()["maps"] == recorded["maps"]
