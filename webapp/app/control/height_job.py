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
from app.control import height_motion as hm
from app.control import heights as hc
from app.control.task import peak_memory
from app.replays import format as fmt
from app.replays import height_inputs, layouts

MOVED_M = 0.5                                       # a cell whose ground moved more than this is reported


class BlobDir:
    """A map's rounds in `<directory>/<match>/<n>.json.gz`, in match then round order, decoded as they are read."""

    def __init__(self, directory: Path, map_name: str, *, pool=()):
        self.map_name = map_name
        self.pool = {str(match).lower(): from_key for match, from_key in pool}
        self.masks = {}
        if self.pool:
            registry = layouts.load_registry()
            by_key = {layout.asset_key: layout for layout in layouts.layouts_of(layouts.map_of(map_name), registry)}
            for from_key in set(self.pool.values()):
                union = None
                for key in height_inputs.changed_between(layouts.map_of(map_name), from_key, map_name, registry):
                    path = height_inputs.CONTROL_DIR / by_key[key].region_mask
                    try:
                        mask = cg.read_mask_png(path)
                    except OSError as error:
                        raise height_inputs.CheckSetError(f"{path} is missing or unreadable: {error}") from error
                    union = mask if union is None else union | mask
                self.masks[from_key] = union
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
            if blob.get("map") != layouts.map_of(self.map_name):
                self.skipped += 1
                continue
            self.read.append(path)
            mask = self.masks.get(self.pool.get(path.parent.name.lower()))
            if mask is not None:
                blob = _mask_blob(blob, mask)
            yield path.parent.name, int(path.name.split(".")[0]), blob

    def per_match(self) -> dict:
        """{match: how many of its rounds were this map's}, from the last pass over the folder."""
        return dict(Counter(path.parent.name for path in self.read))


def _inside(mask, u, v):
    """Whether stored minimap coordinates fall on a changed pixel, keeping off-grid positions outside."""
    x = np.floor(np.asarray(u) * cg.PX / 10000).astype(int)
    y = np.floor(np.asarray(v) * cg.PX / 10000).astype(int)
    height, width = mask.shape
    return ((x >= 0) & (x < width) & (y >= 0) & (y < height)
            & mask[np.clip(y, 0, height - 1), np.clip(x, 0, width - 1)])


def _mask_blob(blob: dict, mask: np.ndarray) -> dict:
    """Remove borrowed positions and kill endpoints in changed regions, preserving each remaining segment."""
    tracks, positions = {}, {}
    for slot, segments in (blob.get("tracks") or {}).items():
        pieces, times, removed = [], [], []
        for segment in segments:
            inside = _inside(mask, np.cumsum(segment["u"]), np.cumsum(segment["v"]))
            times.append(segment["t0"] + np.arange(len(inside)) / blob["hz"])
            removed.append(inside)
            for first, end in hm._runs_of(~inside):
                piece = {**segment, "t0": segment["t0"] + first / blob["hz"]}
                for field in ("u", "v", "yaw", "z"):
                    if field in segment:
                        absolute = int(sum(segment[field][:first + 1]))
                        if field == "yaw":
                            absolute %= 360
                        piece[field] = [absolute, *segment[field][first + 1:end]]
                pieces.append(piece)
        tracks[slot] = pieces
        if times:
            positions[int(slot)] = (np.concatenate(times), np.concatenate(removed))

    def blocked(kill):
        if kill.get("u") is not None and kill.get("v") is not None and _inside(mask, kill["u"], kill["v"]):
            return True
        if kill.get("t") is None:
            return False
        for slot in (kill.get("killer"), kill.get("victim")):
            ts, inside = positions.get(slot, ((), ()))
            if len(ts):
                nearest = int(np.argmin(np.abs(ts - kill["t"])))
                if abs(ts[nearest] - kill["t"]) <= hc.KILL_SAMPLE_S and inside[nearest]:
                    return True
        return False

    return {**blob, "tracks": tracks, "kills": [kill for kill in blob.get("kills") or [] if not blocked(kill)]}


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
    if old.refinement is not None or new.refinement is not None:
        def ground(asset):
            values = asset.floors[..., 0].astype(float)
            values[values < 0] = np.nan
            values = np.repeat(np.repeat(values + asset.origin_z, 2, 0), 2, 1)
            if asset.refinement is not None:
                for c, floors in zip(asset.fine_cells, asset.fine_floors):
                    values.ravel()[int(c)] = floors[0] + asset.origin_z if floors[0] >= 0 else np.nan
            return values
        was, now = ground(old), ground(new)
        had, has = np.isfinite(was), np.isfinite(now)
        return {'previous': old.digest, 'cells_gained': float((has & ~had).sum()) / 4,
                'cells_lost': float((had & ~has).sum()) / 4,
                'cells_moved': float((had & has & (np.abs(now-was) > MOVED_M*10)).sum()) / 4}
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
        must_block: Path | None = None, diagnostic=None, refinement_history=None) -> dict:
    """Builds `map_name` from `rounds` (re-iterable [(match, n, blob)]) and checks it. `previous` is the asset
    file it replaces, for the comparison; `must_block` another check file than the committed one (tests)."""
    started = time.time()
    flat_geo = cg.load_base_geometry(map_name, asset_dir or cg.ASSET_DIR)
    history = height_inputs.refinement_history(map_name, refinement_history)
    cover_px = cg.load_cover_labels(map_name, asset_dir or cg.ASSET_DIR)      # None: the map has no cover.png
    build = hb.build(rounds, flat_geo, refinement_history=history, cover_px=cover_px)
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
