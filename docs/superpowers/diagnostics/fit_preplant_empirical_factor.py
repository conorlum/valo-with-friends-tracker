"""Fit a review-only empirical factor from the independently verified bucket CSV.

No database access; no live scoring, migration, version bump, or rescore.
Run with Python plus NumPy. An optional --output writes a candidate elsewhere.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'webapp'))
from app.scoring.preplant_empirical_factor import EmpiricalTimingCurve, smooth_bucket_rates


def fit_snapshot(source: Path, penalty: float = 1.0) -> dict:
    with source.open(newline='', encoding='utf-8') as stream:
        all_rows = list(csv.DictReader(stream))
    by_bucket = {row['bucket']: row for row in all_rows}
    if len(by_bucket) != len(all_rows):
        raise ValueError("Duplicate bucket labels")
    rows = [by_bucket[f'b{i}'] for i in range(1, 31)]
    payload = {
        'schema_version': 1,
        'status': 'experimental, uncentered, not wired into live scoring',
        'source': source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'population': 'non-self pre-plant kills in non-phantom, non-surrendered planted rounds',
        'time_units': 'seconds before plant; b_k=(k-1,k], coordinates at k-0.5',
        'estimator': 'published direct standardization on pre-kill exact alive counts; per-bucket support',
        'smoothing': 'precision-weighted second-difference penalty on probability scale',
        'penalty': penalty,
        'default_strength': 1.0,
        'mapping': 'clamp(1 + strength * (fitted_rate - side_specific_REF_rate), 0.2, 1.7)',
        'reference_policy': 'dt>30 is a separate pooled category and receives factor 1; no interpolation to REF',
        'centering': 'not calibrated: requires per-event kill-order contribution weights',
        'uncertainty': 'input marginal match-bootstrap intervals; no fitted-curve CI or held-out performance claimed',
        'sides': {},
    }
    for key, side in [('atk', 'attacker'), ('def', 'defender')]:
        p = np.array([float(row[f'adjusted_rate_{key}']) for row in rows])
        lo = [float(row[f'adjusted_rate_{key}_ci_lo']) for row in rows]
        hi = [float(row[f'adjusted_rate_{key}_ci_hi']) for row in rows]
        ref = float(by_bucket['REF'][f'adjusted_rate_{key}'])
        fitted, weights = smooth_bucket_rates(p, lo, hi, penalty=penalty)
        times = np.arange(30, dtype=float) + .5
        curve = EmpiricalTimingCurve(tuple(times), tuple(fitted), ref)
        comparisons = []
        for alternative in [0.0, 1.0, 4.0, 16.0]:
            values, _ = smooth_bucket_rates(p, lo, hi, penalty=alternative)
            comparisons.append({'penalty': alternative, 'rmse_pp': float(100*np.sqrt(np.mean((values-p)**2))),
                                'b4_minus_b16_pp': float(100*(values[3]-values[15]))})
        payload['sides'][side] = {
            'times': times.tolist(),
            'observed_rates': p.tolist(), 'fitted_rates': fitted.tolist(),
            'ci_low': lo, 'ci_high': hi, 'relative_precision_weights': weights.tolist(),
            'counts': [int(row[f'n_obs_{key}']) for row in rows],
            'coverage': [float(row[f'adjusted_coverage_{key}']) for row in rows],
            'reference_rate': ref, 'reference_count': int(by_bucket['REF'][f'n_obs_{key}']),
            'reference_coverage': float(by_bucket['REF'][f'adjusted_coverage_{key}']),
            'rmse_pp': float(100*np.sqrt(np.mean((fitted-p)**2))),
            'max_abs_residual_pp': float(100*np.max(np.abs(fitted-p))),
            'effective_degrees_of_freedom': float(np.trace(np.linalg.solve(
                np.diag(weights)+penalty*np.diff(np.eye(30),n=2,axis=0).T@np.diff(np.eye(30),n=2,axis=0),np.diag(weights)))),
            'factor_at_30': curve.factor(30),
            'factor_just_past_30': curve.factor(30.000001),
            'smoothing_sensitivity': comparisons,
            'examples': [{'dt': dt, 'adjusted_rate':curve.adjusted_rate(dt),'factor':curve.factor(dt)}
                         for dt in [1., 2., 4., 5., 10., 16., 20., 25., 30., 45.]],
        }
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('preplant_dip_investigation_buckets.csv'))
    parser.add_argument('--output', type=Path, default=ROOT/'webapp/app/scoring/preplant_empirical_factor.json')
    parser.add_argument('--penalty', type=float, default=1.0)
    args = parser.parse_args()
    payload = fit_snapshot(args.source.resolve(), args.penalty)
    args.output.write_text(json.dumps(payload, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    for side, data in payload['sides'].items():
        print(side, 'RMSE pp:', round(data['rmse_pp'], 4), 'effective df:', round(data['effective_degrees_of_freedom'],2))
        for sample in data['examples']:
            print(f"  dt={sample['dt']:4.0f} p={sample['adjusted_rate']:.4f} factor={sample['factor']:.4f}")


if __name__ == '__main__':
    main()
