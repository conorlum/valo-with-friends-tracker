"""Records what control's freshness reads for every committed map, so the map-features build can prove it
changes nothing for maps without enabled features (tests/fixtures/control/map_features/legacy_inputs.json).

    .venv\\Scripts\\python.exe tests\\replays\\map_feature_legacy.py      (from webapp/)

The file was written from the services before the map-features build touched them. It is not a permanent
golden file: regenerate it deliberately when a re-tag, a height rebuild or a CONTROL_REVISION bump lands on
main, and say so in that commit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[2]
if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))

from app.replays import control_format as cf  # noqa: E402
from app.services import replay_control  # noqa: E402

PATH = WEBAPP / "tests" / "fixtures" / "control" / "map_features" / "legacy_inputs.json"
RECIPE, SOURCE = "fixed.c10.f1.a0", "0" * 64
LINK = {"sides": {"0": "attack", "1": "defense"}, "db_deaths": [[0, 12.5]]}


def snapshot() -> dict:
    replay_control._assets.cache_clear()
    index = json.loads((replay_control.CONTROL_DIR / "index.json").read_text(encoding="utf-8")).get("maps", {})
    out = {}
    for name in sorted(index):
        inputs = replay_control.geometry_inputs(name)
        out[name] = {"geometry_inputs": inputs, "fingerprint": cf.fingerprint(RECIPE, SOURCE, LINK, inputs)}
    return {"note": __doc__.split("\n\n")[0], "recipe": RECIPE, "link": LINK, "maps": out}


if __name__ == "__main__":
    PATH.write_text(json.dumps(snapshot(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(PATH)
