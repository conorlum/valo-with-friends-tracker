"""Freeze base-layout identities: python -m tests.replays.layout_golden --write.

All replay/link inputs are synthetic. Only the feature artifact lookup is faked;
feature identification reads the real committed bundles and masks.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import warnings

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.coach import areas
from app.config import settings
from app.db import Base
from app.models.replay import ControlFeatureArtifact, ControlHeight
from app.replays import choke_assets, control_format, height_inputs
from app.replays import format as replay_format
from app.replays.map_feature_artifacts import FeatureArtifactRef
from app.services import control_feature_artifacts, replay_control, replay_gaps


GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures/control/layout_golden.json"
INDEX_PATH = replay_control.CONTROL_DIR / "index.json"
FIXED_RECIPE = "0123456789ab.c22.f1.a01234567"
FIXED_SHA = "a" * 64
FIXED_CONTROL_FP = "b" * 16
FIXED_LINK = {"linked": True, "sides": {"0": "attack", "5": "defense"},
              "db_deaths": [[5, 12.5]]}
FIXED_REPLAYS = [
    ["00000000-0000-0000-0000-000000000002", FIXED_RECIPE, "c" * 64, 2],
    ["00000000-0000-0000-0000-000000000001", FIXED_RECIPE, FIXED_SHA, 3],
]
FIXED_ARTIFACT_DIGEST = "f" * 64
# These are the only tables the resolver reads; no replay rows are needed.
CONTROL_TABLES = [ControlHeight.__table__, ControlFeatureArtifact.__table__]


def map_names():
    return sorted(json.loads(INDEX_PATH.read_text(encoding="utf-8"))["maps"])


def _figure(label, compute):
    """Keep unavailable figures visible as null, reporting raised errors by name."""
    try:
        return compute()
    except Exception as error:
        warnings.warn(f"{label}: recorded null ({type(error).__name__})", stacklevel=2)
        return None


def _json_sha(value):
    if value is None:
        return None
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _current_geometry(db, map_name):
    context = replay_control.resolve_current_geometry(db, map_name)
    return {"state": context.state,
            "feature_key": asdict(context.feature_input.key) if context.feature_input else None,
            "geometry": context.pinned.geometry if context.pinned else None}


def _fixed_header(db, key):
    return FeatureArtifactRef(FIXED_ARTIFACT_DIGEST, key, {})


def _must_block_sha_lf():
    raw = height_inputs.MUST_BLOCK.read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="layout-golden-") as folder:
        path = Path(folder) / "must_block.json"
        path.write_bytes(raw)
        return height_inputs.must_block_sha(path)


def build_figures(*, asset_keys=None):
    """Read local figures, optionally resolving map names through selected asset keys."""
    must_block = _figure("must_block_sha_lf", _must_block_sha_lf)
    figures = {"hearing_hash": _figure("hearing_hash", replay_gaps.hearing_hash),
               "must_block_sha_lf": must_block,
               "assets_revision": _figure("assets_revision", replay_format.assets_revision),
               "maps": {}}
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine, tables=CONTROL_TABLES)
        with Session(engine) as db, patch.object(settings, "demo_mode", False), \
                patch.object(control_feature_artifacts, "find_artifact_header", _fixed_header):
            for name in map_names():
                key = name if asset_keys is None else asset_keys[name]
                def read(figure, compute):
                    return _figure(f"{name}.{figure}", compute)

                geometry = read("geometry_inputs", lambda: replay_control.geometry_inputs(key, None))
                identity = read("height_geometry_identity", lambda: height_inputs.geometry_identity(key))
                fp = read("control_fingerprint", lambda: control_format.fingerprint(
                    FIXED_RECIPE, FIXED_SHA, FIXED_LINK, geometry)) if geometry is not None else None
                figures["maps"][name] = {
                    "geometry_inputs": geometry,
                    "current_geometry": read("current_geometry", lambda: _current_geometry(db, key)),
                    "map_layer_sha256": read("map_layer_sha256", lambda: _json_sha(replay_control.map_layer(key))),
                    "choke_asset_hash": read("choke_asset_hash", lambda: choke_assets.asset_hash(key)),
                    "engine_key": read("engine_key", lambda: replay_gaps.engine_key(FIXED_CONTROL_FP, key)),
                    "height_geometry_identity": identity,
                    "height_manifest_digest": read("height_manifest_digest", lambda: height_inputs.digest(
                        height_inputs.manifest(key, FIXED_REPLAYS, identity, must_block)))
                        if identity is not None else None,
                    "callouts_sha256": read("callouts_sha256", lambda: _json_sha(areas.callouts(key))),
                    "control_fingerprint": fp,
                    "gap_fingerprint": read("gap_fingerprint", lambda: replay_gaps.gap_fingerprint(fp, key))
                        if fp is not None else None,
                }
    finally:
        engine.dispose()
    return figures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="replace the committed base-layout golden file")
    args = parser.parse_args()
    figures = build_figures()
    if args.write:
        GOLDEN_PATH.write_text(json.dumps(figures, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {len(figures['maps'])} maps to {GOLDEN_PATH}")
    else:
        print(json.dumps(figures, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()
