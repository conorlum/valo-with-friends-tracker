"""Compile diagnostics from a private tags export and an explicitly chosen height context.

No database, asset publication or cache mutation. Without --height this is a flat
diagnostic, not verification against the site's active measured height asset.
Run from webapp: python scripts/diagnose_map_features.py --tags tags.json --map Ascent --out PRIVATE_DIR
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.control import geometry as cg
from app.control import heights as hc
from app.control.feature_diagnostics import diagnose_features
from app.replays import map_feature_inputs as fi, map_feature_schema as schema
from app.replays.feature_audit import private_output


def diagnose(raw: bytes, map_name: str, *, height: Path | None = None, previous=None):
    source = fi.capture_source_snapshot(map_name, raw)
    entry = fi._entry(source)
    validation = schema.validate(entry.get('map_features') or schema.empty(), map_name, entry.get('specials'))
    if validation.errors:
        raise ValueError('Structural errors: ' + json.dumps(validation.as_dict()['errors']))
    image_path = cg.MINIMAP_DIR / f'{map_name}.png'
    image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()[:12]
    if entry.get('image_sha') and entry['image_sha'] != image_sha:
        raise ValueError('Map image changed since annotation; review coordinates before compiling')
    # Rebuild permanent masks in memory from these exact tags; no use of stale committed masks.
    masks = cg.masks(np.array(Image.open(image_path).convert('RGBA')), entry)
    scale = json.loads(cg.MAPS_JSON.read_text(encoding='utf-8'))[map_name]['xMultiplier']
    geo = cg.geometry_from_masks(map_name, masks.sight, masks.walk, scale, entry.get('specials') or [])
    if entry.get('barrier_paint'):
        barrier = cg.unpack_paint(entry['barrier_paint'])
        geo.barrier = cg.barrier_cells(barrier)
        geo.barrier_sha = hashlib.sha256(np.packbits(barrier).tobytes()).hexdigest()[:12]
    if height is not None:
        cg.attach_heights(geo, hc.load_asset(height))
    report = diagnose_features(geo, source, previous)
    report['annotation_validation'] = validation.as_dict()
    report['context'] = {'image_sha': image_sha, 'permanent_masks': 'rebuilt_in_memory_from_exact_tags',
                         'height_mode': 'exact_file' if height else 'flat',
                         'site_active_height_verified': False,
                         'note': 'An exact local file is not proof that this is the deployed active height asset.'}
    report['editor_entry'] = entry
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tags', type=Path, required=True)
    parser.add_argument('--map', required=True)
    parser.add_argument('--height', type=Path)
    parser.add_argument('--previous', type=Path, help='previous diagnostic report for height comparison')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        out = private_output(args.out, args.tags)
        previous = json.loads(args.previous.read_text(encoding='utf-8'))['snapshot'] if args.previous else None
        report = diagnose(args.tags.read_bytes(), args.map, height=args.height, previous=previous)
        out.mkdir(parents=True)
        (out / 'diagnostics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        content = '<!doctype html><meta charset="utf-8"><title>Map-feature compiler diagnostics</title><style>body{font:16px system-ui;max-width:1000px;margin:2rem auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Compiler diagnostics</h1><p>Local annotation and placement report. Runtime remains disabled unless a reviewed consumer is registered.</p><pre>'
        (out / 'diagnostics.html').write_text(content + html.escape(json.dumps(report, indent=2)) + '</pre>', encoding='utf-8')
        print(json.dumps({'output': str(out), 'counts': report['counts'], 'height': report['height']}, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
