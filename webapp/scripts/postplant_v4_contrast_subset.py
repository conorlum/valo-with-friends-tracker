r"""Run from webapp/:

    .\.venv313\Scripts\python.exe scripts\postplant_v4_contrast_subset.py \
        --out DIR --pairs "Lf:P0,Ff:P0,Ff:Lf" --tag part1

Computes a NAMED SUBSET of contrasts from out-of-fold predictions already on
disk, and writes them to `contrasts_<tag>.json`.

Why this exists. run_postplant_v4_report.py recomputes the entire contrast table
on every invocation, and with 30 arms banked that is ~33 paired bootstraps at
2,000 draws each -- over an hour of pure-Python resampling, most of it
recomputing contrasts that were already reported. Splitting the pairs across
processes turns that into minutes.

It changes NOTHING about the statistics: same `paired_oof_log_loss_delta`, same
2,000 draws, same seed, same verdict vocabulary, same sign convention
(loss(arm) - loss(reference), positive is worse). It only chooses which pairs to
evaluate and lets several processes evaluate disjoint sets at once.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.impact_eval import paired_oof_log_loss_delta

DRAWS = 2000  # predeclared; never lowered to make a run finish sooner


def load_oof(out_dir, arm):
    path = Path(out_dir) / f"oof_{arm}.npz"
    if not path.exists():
        return None
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


def verdict(point, lo, hi):
    if point == 0.0 and lo == 0.0 and hi == 0.0:
        return "IDENTICAL"
    if lo <= 0.0 <= hi:
        return "INCONCLUSIVE"
    return "HARM" if point > 0 else "IMPROVEMENT"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--pairs", required=True,
                        help="comma-separated arm:reference pairs")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    out_dir = Path(args.out)
    results = {}
    for pair in args.pairs.split(","):
        arm, reference = pair.split(":")
        a, b = load_oof(out_dir, arm), load_oof(out_dir, reference)
        label = f"{arm} vs {reference}"
        if a is None or b is None:
            print(f"{label:<22} NOT RUN (missing predictions)", flush=True)
            results[label] = {"verdict": "NOT RUN"}
            continue
        point, lo, hi = paired_oof_log_loss_delta(a, b, draws=DRAWS)
        results[label] = {"arm": arm, "reference": reference, "point": point,
                          "lo": lo, "hi": hi, "verdict": verdict(point, lo, hi)}
        print(f"{label:<22} {point:+.6e} [{lo:+.6e}, {hi:+.6e}] "
              f"{results[label]['verdict']}", flush=True)

    (out_dir / f"contrasts_{args.tag}.json").write_text(json.dumps(results, indent=2))
    print(f"written to contrasts_{args.tag}.json")


if __name__ == "__main__":
    main()
