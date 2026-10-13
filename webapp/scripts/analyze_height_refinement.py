"""Analyze local refinement on frozen replay blobs; never activate or publish heights.

Run from webapp with --frozen MAP.frozen.json --history history.json --out PRIVATE_DIR.
Use --new-history for an explicit first build. Subsequent runs must pass the saved history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.control import geometry as cg, height_build as hb, height_refinement as hr
from app.replays import format as fmt
from app.replays.feature_audit import private_output


class FrozenRounds:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.raw = self.path.read_bytes()
        self.manifest = json.loads(self.raw)
        self.map_name = self.manifest['map']
        self.rows = self.manifest['round_list']
        if not isinstance(self.rows, list) or not self.rows:
            raise ValueError('frozen input requires explicit nonempty round identities')
        identities = []
        for match, number, digest in self.rows:
            if str(UUID(match)) != match or type(number) is not int or number < 1 \
                    or not isinstance(digest, str) or len(digest) != 64 \
                    or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('invalid frozen round identity')
            identities.append((match, number))
        if len(set(identities)) != len(identities):
            raise ValueError('duplicate frozen round identity')
        actual_sha = hashlib.sha256(json.dumps(sorted(self.rows), separators=(',', ':'))
                                    .encode('ascii')).hexdigest()[:16]
        if self.manifest.get('rounds_sha') != actual_sha \
                or self.manifest.get('rounds') != len(self.rows) \
                or self.manifest.get('matches') != len({match for match, _ in identities}):
            raise ValueError('frozen input summary disagrees with explicit round identities')

    def __iter__(self):
        for match, number, digest in sorted(self.rows):
            raw = (self.path.parent / match / f'{number}.json.gz').read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(f'frozen round changed: {match}/{number}')
            blob = fmt.decode_blob(raw)
            if blob.get('map') != self.map_name:
                raise ValueError('frozen round map disagrees with manifest')
            yield match, number, blob


def analyze_frozen(path, previous):
    started = time.perf_counter()
    rounds = FrozenRounds(path)
    geo = cg.load_base_geometry(rounds.map_name)
    stands, walks, matches, counts = hb.all_ground(rounds, geo)
    floors, reasons, _ = hb.cell_floors(stands, matches, geo, walks)
    result = hr.analyze(geo, stands, walks, matches, floors, reasons, previous)
    result['inputs'] = {'frozen_sha256': hashlib.sha256(rounds.raw).hexdigest(),
        'rounds_sha': rounds.manifest.get('rounds_sha'), 'rounds': counts['rounds'], 'matches': counts['matches'],
        'walk_sha256': hashlib.sha256(geo.walk_px.tobytes()).hexdigest(),
        'scope': 'these frozen local rounds; not the latest live height selection'}
    result['elapsed_s'] = round(time.perf_counter() - started, 3)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--history', type=Path)
    group.add_argument('--new-history', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        out = private_output(args.out, args.frozen.parent)
        frozen = FrozenRounds(args.frozen)
        previous = json.loads(args.history.read_text()) if args.history else hr.history(frozen.map_name)
        result = analyze_frozen(args.frozen, previous)
        out.mkdir(parents=True)
        (out / 'analysis.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        (out / 'history.json').write_text(json.dumps(result['history'], indent=2), encoding='utf-8')
        print(json.dumps({'map': result['map'], 'inputs': result['inputs'], 'area': result['area'],
                          'elapsed_s': result['elapsed_s'], 'runtime_enabled': False, 'out': str(out)}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
