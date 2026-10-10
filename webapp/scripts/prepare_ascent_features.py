r"""Prepare a private review candidate from the owner's full tags export. Never publishes annotations.

python scripts/prepare_ascent_features.py --tags C:\private\tags.json --market feature-9
  --garden feature-11 --glass feature-12 --out C:\private\Ascent.runtime-candidate.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.replays import map_feature_schema as ms


def prepare(tags, bindings):
    result = copy.deepcopy(tags)
    mf = result['maps']['Ascent']['map_features']
    features = {f['id']:f for f in mf['features']}
    if set(bindings) != {'ascent_market', 'ascent_garden', 'ascent_heaven_glass'}:
        raise ValueError('provide the Market, Garden and Heaven Glass bindings')
    if len(set(bindings.values())) != 3 or not set(bindings.values()) <= features.keys():
        raise ValueError('each binding needs its own existing authored feature ID')
    for key, identifier in bindings.items():
        feature = features[identifier]
        feature['replay_key'] = key
        if key != 'ascent_heaven_glass':
            if (feature.get('sliding') or {}).get('axis') != 'vertical':
                raise ValueError('choose descending geometry in the tagger before binding a door')
            for transition in feature.get('transitions', []):
                if transition.get('motion') and transition.get('to') in ('open','closed'):
                    transition['motion']['duration'] = ms.known(5,'s')
        existing = next((b for b in mf.get('bundles', []) if b.get('members') == [identifier]
                         and b.get('runtime_consumer') == 'ascent_replay_v1'), None)
        numbers = [int(b['id'].split('-')[1]) for b in mf.get('bundles', [])]
        bundle_id = existing['id'] if existing else 'bundle-' + str(max(numbers, default=0)+1)
        if any(identifier in b.get('members', []) and b['id'] != bundle_id for b in mf.get('bundles', [])):
            raise ValueError('feature already belongs to another bundle; review ownership explicitly')
        if existing is None:
            mf.setdefault('bundles', []).append({'id':bundle_id,'members':[identifier],
                'enabled':True,'runtime_consumer':'ascent_replay_v1'})
        else:
            existing['enabled'] = True
        feature['bundle'] = bundle_id
    report = ms.validate(mf, specials=result['maps']['Ascent'].get('specials'))
    if not report.ok:
        raise ValueError('; '.join(f"{e['where']}: {e['message']}" for e in report.errors))
    return result, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tags', type=Path, required=True)
    for key in ('market','garden','glass'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.out.resolve()
    if output.exists() or any((p/'.git').exists() for p in output.parents):
        parser.error('output must be a new private file outside Git')
    try:
        tags, report = prepare(json.loads(args.tags.read_text(encoding='utf8')), {
            'ascent_market':args.market,'ascent_garden':args.garden,'ascent_heaven_glass':args.glass})
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        json.dump(tags, stream, indent=2)
        stream.write('\n')
    print(f'{output}\nReview candidate only: run diagnose_map_features.py with the exact height asset. {len(report.warnings)} schema warnings retained.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
