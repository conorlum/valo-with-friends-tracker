"""Privately audit ground/quiet routes without publishing or activating inputs.

--regions JSON is {"map": "...", "regions": [{"id": "1", "bbox_px": [x0,y0,x1,y1],
"description": "..."}]}. Coordinates refer to the 1024px minimap and remain private.
The caller must pin the candidate height digest explicitly. Labels never grant routes.
"""
from pathlib import Path
import argparse, hashlib, json, sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.control import geometry as cg, heights as hc, terrain_audit
from app.replays.feature_audit import private_output


def run(height, expected_sha, region_path, out):
    data = json.loads(Path(region_path).read_text(encoding='utf-8'))
    asset = hc.load_asset(Path(height))
    if asset.digest != expected_sha:
        raise ValueError('height digest differs from the explicitly selected candidate')
    if not isinstance(data, dict) or not isinstance(data.get('regions'), list):
        raise ValueError('expected a map and explicit terrain regions')
    target = private_output(Path(out), Path(region_path).parent)
    geo = cg.attach_heights(cg.load_base_geometry(data['map']), asset)
    result = terrain_audit.audit(geo, data['regions'])
    result['regions_sha256'] = hashlib.sha256(Path(region_path).read_bytes()).hexdigest()
    result['asset_sha256'] = hashlib.sha256(Path(height).read_bytes()).hexdigest()
    target.mkdir(parents=True)
    (target/'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--height', type=Path, required=True)
    parser.add_argument('--height-sha', required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.height, args.height_sha, args.regions, args.out)
        print(json.dumps({'map': result['map'], 'height': result['height_sha'], 'regions': len(result['regions']),
            'quiet_large_step_descents_to_review': sum(r['quiet_large_step_descents_to_review'] for r in result['regions']),
            'out': str(args.out)}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
