"""Create a private offline inventory; never publishes tags or activates replay effects.

Run from webapp: python scripts/audit_map_features.py EXPORT --out PRIVATE_DIR
Optionally --tags tags.json --map Ascent --source replay.vrf --max-rows 100000.
Review: python scripts/audit_map_features.py --review observations.json --audit audit.json
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.replays.feature_audit import Limits, inventory, private_output, render_report, worksheet, review_observations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path, nargs='?', help='raw export directory, required for inventory')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--tags', type=Path)
    parser.add_argument('--map')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--max-rows', type=int)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--audit', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.review:
            if not args.audit:
                parser.error('--review requires --audit')
            if args.export or args.out or args.tags or args.map or args.source or args.max_rows:
                parser.error('review uses the saved audit; omit inventory/export arguments')
            report = json.loads(args.audit.read_text(encoding='utf-8'))
            observations = json.loads(args.review.read_text(encoding='utf-8'))
            review = review_observations(observations, report)
            print(json.dumps(review, indent=2))
            return 0 if review['valid'] else 1
        if not args.out or not args.export:
            parser.error('inventory requires an export directory and --out')
        out = private_output(args.out, args.export)
        tags = json.loads(args.tags.read_text(encoding='utf-8')) if args.tags else None
        if args.map and tags and args.map not in tags.get('maps', {}):
            parser.error('map is not in the provided catalogue')
        report = inventory(args.export, tags=tags, map_name=args.map, source=args.source, limits=Limits(rows=args.max_rows))
        out.mkdir(parents=True)
        for name, value in [('audit.json', report), ('observations.json', worksheet(report))]:
            (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
        (out / 'review.html').write_text(render_report(report), encoding='utf-8')
        print(json.dumps({'output': str(out), 'rows': report['scan']['rows'], 'complete': report['scan']['complete'],
                          'flags': report['scan']['flags'], 'candidates': len(report['candidates']),
                          'contract_compatible': report['contract']['compatible']}, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
