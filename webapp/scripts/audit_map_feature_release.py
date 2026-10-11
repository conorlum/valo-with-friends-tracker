"""Private pre-release source/geometry audit. Reads exports and tags, never a DB.

python scripts/audit_map_feature_release.py --tags PRIVATE_TAGS --map Lotus
    --export PRIVATE_EXPORT --windows PRIVATE_CONTEXT --height PRIVATE_HEIGHT
    --out NEW_PRIVATE_FOLDER

Framing, binding, geometry and release qualification are separate. A local height
file alone does not prove that the site has accepted it. All results stay outside Git.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.replays import contract, map_feature_events as events
from app.replays.feature_audit import private_output
from app.control.map_pool_runtime import readiness
from diagnose_map_features import diagnose


def audit(raw, map_name, export, windows, height=None):
    value = json.loads(raw)
    features = value['maps'][map_name].get('map_features', {})
    manifest = contract.load_manifest(export)
    ledger, report = events.read_ledger(export, map_name, manifest)
    if not isinstance(windows, dict) or not windows.get('source_sha256') \
            or windows.get('source_sha256') != manifest.get('source_sha256'):
        raise ValueError('round windows must identify the same recording source')
    windows = windows['windows']
    previous_end = 0
    if not isinstance(windows, list) or not windows:
        raise ValueError('round windows missing')
    for window in windows:
        if not isinstance(window, (list, tuple)) or len(window) != 3 \
                or any(type(t) is not int for t in window) \
                or not previous_end <= window[0] <= window[1] < window[2]:
            raise ValueError('round windows must be ordered absolute replay milliseconds')
        previous_end = window[2]
    geometry = diagnose(raw, map_name, height=height)
    binding = []
    for feature in features.get('features', []):
        if feature.get('preset') == 'vertical_rope':
            continue
        rounds = []
        for number, (start, _, end) in enumerate(windows, 1):
            previous = windows[number - 2][2] if number > 1 else 0
            timeline = events.bind_round(feature, ledger, report, start, end, previous)
            rounds.append({'round': number, **timeline})
        binding.append({'id': feature['id'], 'name': feature.get('name'),
                        'readiness': readiness(feature), 'rounds': rounds})
    return {'map': map_name, 'source_sha256': manifest.get('source_sha256'),
            'framing': report, 'source_bindings': binding, 'geometry': geometry,
            'release_ready': False,
            'release_gates': ['verify parser recipe and recording identity',
                'use an accepted exact height asset', 'resolve every intended member dependency',
                'compare fresh full control, fresh gaps-only and cached gaps on reviewed real scenes',
                'pass Linux worker checks', 'owner approves publication and deployment']}


def render(report):
    rows = []
    bindings = {b['id']: b for b in report['source_bindings']}
    for f in report['geometry']['features']:
        b = bindings.get(f['id'])
        bound = sum(r['status'] == 'bound' for r in b['rounds']) if b else 0
        source = f'{bound}/{len(b["rounds"])} rounds bound' if b else 'timed route; no RPC binding'
        if b:
            pending = [str(r['round']) for r in b['rounds'] if r['status'] != 'bound']
            if pending:
                source += '; pending rounds ' + ', '.join(pending)
        reasons = (b['readiness'] if b else []) + f.get('runtime_reasons', [])
        reasons += [r['code'] + ': ' + r['path'] for r in f.get('reasons', [])]
        name = b.get('name') if b else None
        label = f['id'] + (': ' + name if name else '')
        rows.append('<tr><td>' + html.escape(label) + '</td><td>' + html.escape(source) +
                    '</td><td>' + html.escape(f['placement']) + '</td><td>' +
                    html.escape('; '.join(dict.fromkeys(reasons))) + '</td></tr>')
    height = report.get('site_height_selection', {})
    height_note = 'Height asset: ' + height.get('status', 'acceptance not verified')
    if height.get('height_gate_reasons'):
        height_note += ' — ' + '; '.join(height['height_gate_reasons'])
    return ('<!doctype html><meta charset="utf-8"><title>Map feature release audit</title>'
        '<style>body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:1rem}'
        'table{border-collapse:collapse;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #ccc}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{margin-bottom:.5rem}</style>'
        '<h1>' + html.escape(report['map']) + ' release audit</h1>'
        '<p><strong>' + html.escape(height_note) + '</strong></p>'
        '<p>Private local evidence. This report does not publish tags, activate heights or start a live recompute.</p>'
        '<table><thead><tr><th>Feature</th><th>Source</th><th>Placement</th><th>Remaining work</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table><details><summary>Full evidence and release gates</summary><pre>'
        + html.escape(json.dumps(report, indent=2)) + '</pre></details>')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('tags', 'export', 'windows', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--map', required=True)
    parser.add_argument('--height', type=Path)
    args = parser.parse_args(argv)
    try:
        out = private_output(args.out, args.export)
        report = audit(args.tags.read_bytes(), args.map, args.export,
                       json.loads(args.windows.read_text(encoding='utf-8')), args.height)
        out.mkdir(parents=True)
        (out / 'audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (out / 'audit.html').write_text(render(report), encoding='utf-8')
        print(json.dumps({'output': str(out), 'framing': report['framing']['status'],
                          'geometry': report['geometry']['counts'], 'release_ready': False}))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
