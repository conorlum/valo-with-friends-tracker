"""Build and independently verify mixed ground from hashed frozen rounds, privately.

Use --new-history for the first build, then --history with its saved history.json.
This command never connects to a database, publishes tags, or activates heights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_height_refinement import FrozenRounds
from app.control import height_job, height_verify
from app.replays import height_inputs as hi
from app.replays.feature_audit import private_output


def build_preview(frozen, history, out):
    rounds = FrozenRounds(frozen)
    target = private_output(Path(out), Path(frozen).parent)
    prior = hi.refinement_history(rounds.map_name, history)
    result = height_job.run(rounds.map_name, rounds, refinement_history=prior)
    verified = height_verify.describe(result['asset'], rounds.map_name)
    if verified.get('error') or verified['digest'] != result['digest']:
        raise ValueError(f'independent asset verification failed: {verified}')
    target.mkdir(parents=True)
    (target / f'{rounds.map_name}.height.npz').write_bytes(result.pop('asset'))
    result['verified'] = verified
    result['inputs'] = {'frozen_sha256': hashlib.sha256(rounds.raw).hexdigest(),
                        'rounds_sha': rounds.manifest['rounds_sha'], 'rounds': len(rounds.rows),
                        'history': prior}
    result['runtime_enabled'] = False
    (target / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (target / 'history.json').write_text(json.dumps(result['report']['refinement']['history'], indent=2), encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument('--history', type=Path)
    choices.add_argument('--new-history', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        previous = json.loads(args.history.read_text()) if args.history else None
        result = build_preview(args.frozen, previous, args.out)
        print(json.dumps({'map': result['map'], 'digest': result['digest'], 'seconds': result['seconds'],
                          'ready': result['report']['ready'], 'not_ready': result['report']['not_ready'],
                          'checks': {k: result['report'][k] for k in ('kill_lines', 'must_block')},
                          'refined_parents': result['report']['refined_parents'], 'out': str(args.out)}, default=str))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
