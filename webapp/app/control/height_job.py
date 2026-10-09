"""A map's height build as one job (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 2):
what scripts/build_control_heights.py runs at a desk and what the replay worker's child
(replay_worker/height_job.py) runs with nobody watching.

`run` builds the map from its rounds (app/control/height_build.py), runs the kill-line and must-block checks
on the new heights, compares them with the asset before, and returns the asset's bytes with its report. It
never decides whether the asset goes live: the web app verifies the result and applies the gate
(app/services/control_heights.py).

**The checks fail closed.** The must-block check needs its check set (app/replays/height_inputs.py): a file
that is missing or unreadable fails the check and says why. A readable set with no line for this map passes,
with `lines: 0`. The result always names the set it was run against (`set`), so the web app can tell a check
run against another deploy's list.

Rounds are read one at a time and more than once (the build, then the kill lines), so they come as something
re-iterable: a list, or a `BlobDir` over a folder of stored blobs, which holds one decoded round in memory at
a time. A map's rounds decoded all at once don't fit the worker's memory.

Local tooling, like the engine: the web app never imports this module.
"""

from __future__ import annotations

import copy
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

from app.control import geometry as cg
from app.control import height_build as hb
from app.control import heights as hc
from app.control.task import peak_memory
from app.replays import format as fmt
from app.replays import height_inputs

MOVED_M = 0.5                                       # a cell whose ground moved more than this is reported


class BlobDir:
    """A map's rounds in `<directory>/<match>/<n>.json.gz`, in match then round order, decoded as they are read."""

    def __init__(self, directory: Path, map_name: str):
        self.map_name = map_name
        self.paths = sorted(Path(directory).glob("*/*.json.gz"),
                            key=lambda p: (p.parent.name, int(p.name.split(".")[0])))
        self.skipped = 0
        self.read: list[Path] = []          # the files of this map's rounds, from the last pass

    def __len__(self) -> int:
        return len(self.paths)

    def __iter__(self):
        self.skipped, self.read = 0, []
        for path in self.paths:
            blob = fmt.decode_blob(path.read_bytes())
            if blob.get("map") != self.map_name:
                self.skipped += 1
                continue
            self.read.append(path)
            yield path.parent.name, int(path.name.split(".")[0]), blob

    def per_match(self) -> dict:
        """{match: how many of its rounds were this map's}, from the last pass over the folder."""
        return dict(Counter(path.parent.name for path in self.read))


def run_checks(map_name: str, flat_geo, build: hb.HeightBuild, rounds, must_block: Path | None = None) -> dict:
    """The kill-line and must-block checks on the new heights, stored in the build's report."""
    geo = cg.attach_heights(copy.copy(flat_geo), build.asset)
    kills = hb.kill_line_check(rounds, geo)
    try:
        lines, sha = height_inputs.must_block_set(must_block)
        must = {"set": sha, **hb.must_block_check(lines, geo, map_name)}
    except height_inputs.CheckSetError as error:
        must = {"set": None, "lines": 0, "checked": 0, "unchecked": 0, "blocked": 0, "passes": False, "results": [],
                "error": str(error)}
    build.report["kill_lines"], build.report["must_block"] = kills, must
    return {"kill_lines": kills, "must_block": must}


def compare(new: hc.HeightAsset, old: hc.HeightAsset | None) -> dict | None:
    """The new ground against the asset before, in world heights: cells that gained a height, lost theirs, or
    moved by more than MOVED_M. For looking at afterwards; never a gate."""
    if old is None:
        return None
    had, has = old.floors[..., 0] >= 0, new.floors[..., 0] >= 0
    both = had & has
    shift = (new.floors[..., 0].astype(int) + new.origin_z) - (old.floors[..., 0].astype(int) + old.origin_z)
    return {"previous": old.digest, "cells_gained": int((has & ~had).sum()), "cells_lost": int((had & ~has).sum()),
            "cells_moved": int((both & (np.abs(shift) > MOVED_M * 10)).sum())}


def features_pending(map_name: str, geo, asset_dir: Path | None = None, diagnostic=None) -> dict:
    """Provisional full-catalogue diagnostics, separate from all height checks."""
    from app.control.feature_diagnostics import diagnose_features
    from app.replays.map_feature_diagnostics import diagnostic_envelope, read_diagnostic
    source_sha = None
    try:
        if diagnostic is None:
            diagnostic = diagnostic_envelope(map_name, ((asset_dir or cg.ASSET_DIR) / 'tags.json').read_bytes(), bounded=False)
        source_sha = diagnostic.get('raw_source_sha256')
        source = read_diagnostic(diagnostic, map_name)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {'status': 'error', 'code': 'source_failure', 'reason': str(exc), 'source_sha256': source_sha}
    try:
        from app.replays.map_feature_inputs import read_json
        diagnostic_geo = copy.copy(geo)
        diagnostic_geo.specials = read_json(source.map_entry_bytes).get('specials', [])
        return diagnose_features(diagnostic_geo, source, diagnostic.get('previous'))
    except Exception as exc:
        return {'status': 'error', 'code': 'compile_failure', 'reason': str(exc), 'source_sha256': source_sha}


def run(map_name: str, rounds, *, previous: Path | None = None, asset_dir: Path | None = None,
        must_block: Path | None = None, diagnostic=None) -> dict:
    """Builds `map_name` from `rounds` (re-iterable [(match, n, blob)]) and checks it. `previous` is the asset
    file it replaces, for the comparison; `must_block` another check file than the committed one (tests)."""
    started = time.time()
    flat_geo = cg.load_base_geometry(map_name, asset_dir or cg.ASSET_DIR)
    build = hb.build(rounds, flat_geo)
    run_checks(map_name, flat_geo, build, rounds, must_block)
    old = None
    if previous is not None and Path(previous).is_file():
        try:
            old = hc.load_asset(Path(previous))
        except Exception:  # noqa: BLE001 - an asset of another format: nothing to compare with
            old = None
    build.report["compare"] = compare(build.asset, old)
    build.report["features"] = features_pending(map_name, cg.attach_heights(copy.copy(flat_geo), build.asset),
                                                asset_dir, diagnostic)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{map_name}.height.npz"
        hc.save_asset(path, build.asset)
        data = path.read_bytes()
    return {"status": "ok", "map": map_name, "digest": build.asset.digest, "asset": data,
            "report": json.loads(json.dumps(build.report, default=str)), "rules": hc.rules(),
            "seconds": round(time.time() - started, 1), "peak": peak_memory()}
