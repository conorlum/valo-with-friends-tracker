"""Investigation: is the pre-plant shape curve's dip (found while running
scripts/fit_preplant_time_factor.py against the real DB) a fitting/runtime
mismatch, an unstable 2-knot estimate, or a reproducible feature of the
data? Ordered exactly per the investigation brief:

  0. Confirm/quantify the shape*amplitude reconstruction mismatch.
  1-2. 1-second dt buckets, raw counts and win rates, pooled and by side.
  3. State-adjusted estimates (direct standardization AND a joint
     regression with dt-bucket dummies), no shape/amplitude decomposition.
  4. Match-clustered bootstrap CIs on the direct-standardized estimate.
  5. No monotonicity/plateau imposed on the diagnostic bins; the CURRENT
     2-knot model is overlaid separately, on the same logit-relative-to-
     far-reference scale.
  6. Regularization and match-split sensitivity of the ORIGINAL 2-knot
     model; a per-state breakdown.

INVESTIGATIVE ONLY. Reads the live DB, writes nothing to it, does not
import or touch impact.py, does not change IMPACT_CALCULATION_VERSION, does
not rescore anything, does not relax any monotonicity requirement in the
spec or the implementation -- that stays a Task 8/plan-level decision.

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\investigate_preplant_dip_1s_buckets.py

Outputs (written next to this script):
    preplant_dip_investigation_buckets.csv   -- one row per (bucket, side)
    preplant_dip_investigation_report.json   -- everything else (mismatch
                                                 numbers, sensitivity grids,
                                                 state breakdown, reference
                                                 weights) for the writeup
                                                 and the chart artifact.
"""
import collections
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath("."))

import numpy as np
from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.preplant_time_model import (
    PreplantKillObservation,
    _clamp_adv,
    extract_preplant_observations,
    fit_preplant_time_model,
    shape_basis,
)
from app.services.stats_math import back_transform, fit_logistic, standardize

OUT_DIR = Path(__file__).resolve().parent
CSV_PATH = OUT_DIR / "preplant_dip_investigation_buckets.csv"
JSON_PATH = OUT_DIR / "preplant_dip_investigation_report.json"

BUCKET_REF = "REF"  # dt > 30, the far reference population (shape's own zero anchor)


def bucket_label(dt: float) -> str:
    """dt == 0 gets its own explicit bucket (empty on this dataset -- the
    extraction requires dt > 0 strictly, so a kill exactly at the plant
    instant can never appear here; reported, not silently dropped).
    Buckets 1..30 are (k-1, k], so dt=0.5 -> b1, dt=30.0 -> b30. dt > 30 is
    the reference population, kept OUT of the 1-30 bucket grid."""
    if dt == 0.0:
        return "b0_exact"
    if dt > 30.0:
        return BUCKET_REF
    return f"b{int(np.ceil(dt))}"


def main():
    db = SessionLocal()
    print("matches:", db.execute(text("select count(*) from matches")).scalar())

    t0 = time.time()
    all_obs = extract_preplant_observations(db)
    usable = [o for o in all_obs if o.round_won_by_killer_team is not None]
    excluded_undeterminable = len(all_obs) - len(usable)
    print(f"extraction: {len(all_obs):,} pre-plant observations, "
          f"{excluded_undeterminable:,} excluded (undeterminable winner), "
          f"{len(usable):,} usable ({time.time()-t0:.1f}s)")

    for o in usable:
        pass  # bucket assignment done inline below to avoid a second pass

    report: dict = {}

    # =====================================================================
    # 0. The fitting/runtime mismatch, quantified against the ALREADY
    #    FITTED (committed) 2-knot model.
    # =====================================================================
    print("\n=== 0. fitting/runtime mismatch check ===")
    fit = fit_preplant_time_model(usable, include_side_interaction=True)
    theta2_pooled = fit.intercept_def  # == theta2_pooled exactly, no offset
    theta1_pooled = fit.shape_mid_ratio * theta2_pooled
    print(f"  fitted: theta1(dt=20, pooled)={theta1_pooled:+.4f}   "
          f"theta2(dt<=10, pooled)={theta2_pooled:+.4f}   shape_mid_ratio={fit.shape_mid_ratio:+.4f}")

    mismatch_rows = []
    for dt in (30.0, 25.0, 20.0, 15.0, 10.0, 5.0, 0.0):
        for adv in (-2, 0, 2):
            for is_attacker in (True, False):
                w1, w2 = shape_basis(dt)
                lift = fit.logit_lift(adv, is_attacker)
                eta_true = w1 * theta1_pooled + w2 * lift  # the model actually fitted
                eta_runtime = fit.shape(dt) * lift          # what shape()*logit_lift() computes
                mismatch_rows.append({
                    "dt": dt, "adv": adv, "is_attacker": is_attacker,
                    "eta_true": round(float(eta_true), 4),
                    "eta_runtime_shape_times_lift": round(float(eta_runtime), 4),
                    "delta": round(float(eta_runtime - eta_true), 4),
                })
    for row in mismatch_rows:
        if row["dt"] in (20.0, 0.0):  # print the two extremes; full table goes to JSON
            print(f"  dt={row['dt']:>4.0f} adv={row['adv']:+d} atk={row['is_attacker']!s:5} "
                  f"eta_true={row['eta_true']:+.4f}  shape*lift={row['eta_runtime_shape_times_lift']:+.4f}  "
                  f"delta={row['delta']:+.4f}")
    max_abs_delta = max(abs(r["delta"]) for r in mismatch_rows)
    print(f"  max |delta| across the grid above: {max_abs_delta:.4f} logits")
    print("  CONCLUSION: shape(dt)*logit_lift(adv,side) != the model's own fitted linear")
    print("  predictor whenever logit_lift(adv,side) != theta2_pooled (i.e. whenever adv!=0")
    print("  or side != the reference), because w1 was fit WITHOUT any side/adv interaction")
    print("  (a deliberate, tested design choice -- Task 3), so its true contribution to eta")
    print("  is the FLAT constant theta1_pooled, not theta1_pooled*(lift(adv,side)/theta2_pooled).")
    report["mismatch"] = {
        "theta1_pooled": float(theta1_pooled), "theta2_pooled": float(theta2_pooled),
        "shape_mid_ratio": float(fit.shape_mid_ratio), "grid": mismatch_rows,
        "max_abs_delta_logits": float(max_abs_delta),
    }

    # =====================================================================
    # 1-2. 1-second buckets: counts and raw win rates, pooled and by side.
    #      Resolve the mismatch for THIS investigation by NOT using the
    #      shape/amplitude decomposition at all below -- buckets are
    #      compared directly against the far reference (dt>30), exactly
    #      the same anchor the production model already pins shape=0 to.
    # =====================================================================
    print("\n=== 1-2. bucket construction ===")
    buckets = collections.defaultdict(list)  # label -> list[obs]
    for o in usable:
        buckets[bucket_label(o.dt)].append(o)

    bucket_order = ["b0_exact"] + [f"b{k}" for k in range(1, 31)] + [BUCKET_REF]
    for label in bucket_order:
        if label not in buckets:
            buckets[label] = []

    def _rate(obs_list):
        if not obs_list:
            return float("nan")
        return sum(o.round_won_by_killer_team for o in obs_list) / len(obs_list)

    bucket_stats = []
    for label in bucket_order:
        rows = buckets[label]
        atk_rows = [o for o in rows if o.is_attacker]
        def_rows = [o for o in rows if not o.is_attacker]
        bucket_stats.append({
            "bucket": label,
            "n_obs": len(rows),
            "n_rounds": len({(o.match_id, o.round_id) for o in rows}),
            "n_matches": len({o.match_id for o in rows}),
            "raw_rate_pooled": _rate(rows),
            "n_obs_atk": len(atk_rows), "raw_rate_atk": _rate(atk_rows),
            "n_obs_def": len(def_rows), "raw_rate_def": _rate(def_rows),
        })
    for row in bucket_stats:
        if row["bucket"] in ("b0_exact", "b1", "b5", "b10", "b15", "b20", "b25", "b30", BUCKET_REF):
            print(f"  {row['bucket']:>8}  n={row['n_obs']:>6,}  rounds={row['n_rounds']:>6,}  "
                  f"matches={row['n_matches']:>5,}  pooled={row['raw_rate_pooled']:.3f}  "
                  f"atk={row['raw_rate_atk']:.3f} (n={row['n_obs_atk']:,})  "
                  f"def={row['raw_rate_def']:.3f} (n={row['n_obs_def']:,})")
    print(f"  b0_exact: {buckets['b0_exact'].__len__()} observations "
          f"(dt>0 is required by extraction, so an exact-zero kill can never appear here)")

    # =====================================================================
    # 3. State-adjusted estimate -- direct standardization, no regression,
    #    no shape/amplitude split. Reference population = the OVERALL
    #    per-side state distribution over ALL usable pre-plant observations
    #    (dt<=30 AND dt>30 together), so bucket-to-bucket comparisons are
    #    not confounded by which states happen to occur in that bucket.
    # =====================================================================
    print("\n=== 3. state-adjusted (direct standardization) ===")
    MIN_CELL = 20  # a (bucket, state, side) cell below this is dropped and its reference weight redistributed

    def state_weights(obs_list):
        c = collections.Counter(o.exact_state for o in obs_list)
        total = sum(c.values())
        return {s: n / total for s, n in c.items()} if total else {}

    ref_weights_by_side = {
        True: state_weights([o for o in usable if o.is_attacker]),
        False: state_weights([o for o in usable if not o.is_attacker]),
    }
    report["reference_state_weights"] = {
        "attacker": ref_weights_by_side[True], "defender": ref_weights_by_side[False],
    }

    def standardized_rate(obs_list, side_is_attacker):
        by_state = collections.defaultdict(list)
        for o in obs_list:
            by_state[o.exact_state].append(o)
        ref = ref_weights_by_side[side_is_attacker]
        used_weight, weighted_sum = 0.0, 0.0
        for state, weight in ref.items():
            cell = by_state.get(state, [])
            if len(cell) < MIN_CELL:
                continue
            rate = sum(o.round_won_by_killer_team for o in cell) / len(cell)
            weighted_sum += weight * rate
            used_weight += weight
        if used_weight == 0:
            return float("nan"), 0.0
        return weighted_sum / used_weight, used_weight  # renormalized over included states

    for row in bucket_stats:
        rows = buckets[row["bucket"]]
        for side_key, side_is_attacker in (("atk", True), ("def", False)):
            side_rows = [o for o in rows if o.is_attacker == side_is_attacker]
            adj, covered_weight = standardized_rate(side_rows, side_is_attacker)
            row[f"adjusted_rate_{side_key}"] = adj
            row[f"adjusted_coverage_{side_key}"] = covered_weight  # fraction of reference weight actually used
    for row in bucket_stats:
        if row["bucket"] in ("b1", "b5", "b10", "b15", "b20", "b25", "b30", BUCKET_REF):
            print(f"  {row['bucket']:>8}  raw_atk={row['raw_rate_atk']:.3f}  "
                  f"adj_atk={row['adjusted_rate_atk']:.3f} (cov={row['adjusted_coverage_atk']:.2f})   "
                  f"raw_def={row['raw_rate_def']:.3f}  adj_def={row['adjusted_rate_def']:.3f} "
                  f"(cov={row['adjusted_coverage_def']:.2f})")

    # =====================================================================
    # 3b. State-adjusted estimate -- a JOINT regression alternative:
    #     logit(win) ~ state_FE + dt_bucket(ref=dt>30) * side. No spline, no
    #     shape/amplitude split, no monotonicity imposed. This is the
    #     regression-based cross-check the brief also asked for; unlike
    #     direct standardization it borrows statistical strength across
    #     buckets for the state-FE coefficients, at the cost of one point
    #     estimate rather than a cheaply-bootstrapped one (see the note
    #     below where its own bootstrap is skipped for compute cost).
    # =====================================================================
    print("\n=== 3b. joint regression cross-check (dt-bucket x side, state FE) ===")
    t0 = time.time()
    states = sorted({o.exact_state for o in usable})
    ref_state = "5v5" if "5v5" in states else states[0]
    other_states = [s for s in states if s != ref_state]
    dt_buckets_ordered = [f"b{k}" for k in range(1, 31)]  # b0_exact folded into REF: 0 observations anyway

    def _bucket_for_regression(dt):
        lbl = bucket_label(dt)
        return BUCKET_REF if lbl == "b0_exact" else lbl

    rows_X, labels_y = [], []
    for o in usable:
        atk = 1.0 if o.is_attacker else 0.0
        b = _bucket_for_regression(o.dt)
        row = [1.0 if o.exact_state == s else 0.0 for s in other_states]
        row += [1.0 if b == bl else 0.0 for bl in dt_buckets_ordered]
        row += [atk]
        row += [(1.0 if b == bl else 0.0) * atk for bl in dt_buckets_ordered]
        rows_X.append(row)
        labels_y.append(1.0 if o.round_won_by_killer_team else 0.0)
    X = np.array(rows_X, dtype=float)
    y = np.array(labels_y, dtype=float)
    scaled, _, centre, scale = standardize(X, X)
    beta_scaled = fit_logistic(scaled, y, l2=1.0)
    beta = back_transform(beta_scaled, centre, scale)
    print(f"  fit: {X.shape[0]:,} rows x {X.shape[1]} columns, {time.time()-t0:.1f}s")

    n_state = len(other_states)
    idx_bucket_def = 1 + n_state          # 30 columns: dt_bucket dummies (defender/pooled base)
    idx_atk_main = idx_bucket_def + 30
    idx_bucket_atk = idx_atk_main + 1     # 30 more columns: dt_bucket x atk

    def_bucket_coef = {bl: float(beta[idx_bucket_def + i]) for i, bl in enumerate(dt_buckets_ordered)}
    atk_main = float(beta[idx_atk_main])
    atk_bucket_coef = {
        bl: def_bucket_coef[bl] + atk_main + float(beta[idx_bucket_atk + i])
        for i, bl in enumerate(dt_buckets_ordered)
    }
    def_bucket_coef = {bl: def_bucket_coef[bl] for bl in dt_buckets_ordered}
    # REF's own coefficient is 0 by construction (the omitted category);
    # both curves are therefore already on the "relative to dt>30" scale
    # the mismatch check above also uses.
    for row in bucket_stats:
        bl = row["bucket"]
        if bl in def_bucket_coef:
            row["regression_logit_atk_vs_ref"] = atk_bucket_coef[bl]
            row["regression_logit_def_vs_ref"] = def_bucket_coef[bl]
        else:
            row["regression_logit_atk_vs_ref"] = 0.0 if bl == BUCKET_REF else None
            row["regression_logit_def_vs_ref"] = 0.0 if bl == BUCKET_REF else None

    for bl in ("b1", "b5", "b10", "b15", "b20", "b25", "b30"):
        print(f"  {bl:>4}  atk_logit_vs_ref={atk_bucket_coef[bl]:+.4f}   def_logit_vs_ref={def_bucket_coef[bl]:+.4f}")

    report["regression_cross_check"] = {
        "n_rows": int(X.shape[0]), "n_columns": int(X.shape[1]),
        "atk_bucket_coef_vs_ref": atk_bucket_coef, "def_bucket_coef_vs_ref": def_bucket_coef,
    }

    # =====================================================================
    # 4. Match-clustered bootstrap on the DIRECT-STANDARDIZATION estimate
    #    (cheap enough to bootstrap; the joint regression above is not, at
    #    reasonable draw counts -- reported as a point estimate only, and
    #    stated as a limitation in the writeup).
    # =====================================================================
    print("\n=== 4. match-clustered bootstrap (500 draws) on adjusted rates ===")
    t0 = time.time()
    by_match: dict[int, list] = collections.defaultdict(list)
    for o in usable:
        by_match[o.match_id].append(o)
    match_ids = list(by_match.keys())
    rng = np.random.default_rng(0)
    DRAWS = 500

    boot_results = {row["bucket"]: {"atk": [], "def": []} for row in bucket_stats}
    for _ in range(DRAWS):
        picks = rng.integers(0, len(match_ids), size=len(match_ids))
        sample_obs = [o for i in picks for o in by_match[match_ids[i]]]
        sample_buckets = collections.defaultdict(list)
        for o in sample_obs:
            sample_buckets[bucket_label(o.dt)].append(o)
        # Reference weights are recomputed WITHIN the resample too -- the
        # reference population is itself a function of the data, so its own
        # sampling variability belongs inside the interval.
        draw_ref = {
            True: state_weights([o for o in sample_obs if o.is_attacker]),
            False: state_weights([o for o in sample_obs if not o.is_attacker]),
        }

        def draw_standardized(obs_list, side_is_attacker):
            by_state = collections.defaultdict(list)
            for o in obs_list:
                by_state[o.exact_state].append(o)
            ref = draw_ref[side_is_attacker]
            used_weight, weighted_sum = 0.0, 0.0
            for state, weight in ref.items():
                cell = by_state.get(state, [])
                if len(cell) < MIN_CELL:
                    continue
                rate = sum(o.round_won_by_killer_team for o in cell) / len(cell)
                weighted_sum += weight * rate
                used_weight += weight
            return (weighted_sum / used_weight) if used_weight else float("nan")

        for label in bucket_order:
            rows = sample_buckets.get(label, [])
            atk_rows = [o for o in rows if o.is_attacker]
            def_rows = [o for o in rows if not o.is_attacker]
            boot_results[label]["atk"].append(draw_standardized(atk_rows, True))
            boot_results[label]["def"].append(draw_standardized(def_rows, False))
    print(f"  {DRAWS} draws, {time.time()-t0:.1f}s")

    for row in bucket_stats:
        for side_key in ("atk", "def"):
            vals = [v for v in boot_results[row["bucket"]][side_key] if v == v]  # drop NaN
            if len(vals) >= 20:
                lo, hi = np.percentile(vals, [2.5, 97.5])
                row[f"adjusted_rate_{side_key}_ci_lo"] = float(lo)
                row[f"adjusted_rate_{side_key}_ci_hi"] = float(hi)
                row[f"adjusted_rate_{side_key}_n_valid_draws"] = len(vals)
            else:
                row[f"adjusted_rate_{side_key}_ci_lo"] = None
                row[f"adjusted_rate_{side_key}_ci_hi"] = None
                row[f"adjusted_rate_{side_key}_n_valid_draws"] = len(vals)
                row[f"adjusted_rate_{side_key}_sparse"] = True

    # =====================================================================
    # 6a. Regularization sensitivity of the ORIGINAL 2-knot model.
    # =====================================================================
    print("\n=== 6a. regularization sensitivity, original 2-knot model ===")
    reg_sensitivity = []
    for l2 in (0.1, 1.0, 10.0, 100.0):
        f = _fit_2knot_with_l2(usable, l2)
        theta2 = f.intercept_def
        theta1 = f.shape_mid_ratio * theta2
        reg_sensitivity.append({
            "l2": l2, "theta1_dt20": float(theta1), "theta2_dt10_plateau": float(theta2),
            "shape_mid_ratio": float(f.shape_mid_ratio),
            "intercept_atk": float(f.intercept_atk), "slope_atk": float(f.slope_atk),
            "slope_def": float(f.slope_def),
        })
        print(f"  l2={l2:>6.1f}  theta1={theta1:+.4f}  theta2={theta2:+.4f}  "
              f"shape_mid_ratio={f.shape_mid_ratio:+.4f}")
    report["regularization_sensitivity"] = reg_sensitivity

    # =====================================================================
    # 6b. Match-split stability of the ORIGINAL 2-knot model.
    # =====================================================================
    print("\n=== 6b. match-split stability, original 2-knot model ===")
    rng2 = np.random.default_rng(1)
    shuffled = match_ids.copy()
    rng2.shuffle(shuffled)
    half = len(shuffled) // 2
    half_a_ids, half_b_ids = set(shuffled[:half]), set(shuffled[half:])
    half_a = [o for o in usable if o.match_id in half_a_ids]
    half_b = [o for o in usable if o.match_id in half_b_ids]
    fit_a = fit_preplant_time_model(half_a, include_side_interaction=True)
    fit_b = fit_preplant_time_model(half_b, include_side_interaction=True)
    theta1_a, theta2_a = fit_a.shape_mid_ratio * fit_a.intercept_def, fit_a.intercept_def
    theta1_b, theta2_b = fit_b.shape_mid_ratio * fit_b.intercept_def, fit_b.intercept_def
    print(f"  half A ({len(half_a):,} obs): theta1={theta1_a:+.4f}  theta2={theta2_a:+.4f}  "
          f"shape_mid_ratio={fit_a.shape_mid_ratio:+.4f}")
    print(f"  half B ({len(half_b):,} obs): theta1={theta1_b:+.4f}  theta2={theta2_b:+.4f}  "
          f"shape_mid_ratio={fit_b.shape_mid_ratio:+.4f}")
    report["match_split_stability"] = {
        "half_a": {"n": len(half_a), "theta1": float(theta1_a), "theta2": float(theta2_a),
                   "shape_mid_ratio": float(fit_a.shape_mid_ratio)},
        "half_b": {"n": len(half_b), "theta1": float(theta1_b), "theta2": float(theta2_b),
                   "shape_mid_ratio": float(fit_b.shape_mid_ratio)},
    }

    # =====================================================================
    # 6c. Per-state breakdown: which states drive the b15-b25 region.
    # =====================================================================
    print("\n=== 6c. per-state raw rates in the b15-b25 region vs the far reference ===")
    top_states = [s for s, _ in collections.Counter(o.exact_state for o in usable).most_common(8)]
    state_breakdown = []
    for state in top_states:
        mid_rows = [o for o in usable if o.exact_state == state and bucket_label(o.dt) in
                    {f"b{k}" for k in range(15, 26)}]
        ref_rows = [o for o in usable if o.exact_state == state and bucket_label(o.dt) == BUCKET_REF]
        state_breakdown.append({
            "state": state, "n_mid": len(mid_rows), "rate_mid": _rate(mid_rows),
            "n_ref": len(ref_rows), "rate_ref": _rate(ref_rows),
            "delta": _rate(mid_rows) - _rate(ref_rows) if mid_rows and ref_rows else None,
        })
        print(f"  {state:>4}  mid(b15-25) n={len(mid_rows):>6,} rate={_rate(mid_rows):.3f}   "
              f"ref(dt>30) n={len(ref_rows):>6,} rate={_rate(ref_rows):.3f}")
    report["state_breakdown_b15_25_vs_ref"] = state_breakdown

    # =====================================================================
    # Write outputs
    # =====================================================================
    import csv
    fieldnames = list(bucket_stats[0].keys())
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in bucket_stats:
            writer.writerow(row)
    print(f"\nCSV written: {CSV_PATH}")

    report["bucket_stats"] = bucket_stats
    report["population"] = {
        "total_preplant_observations": len(all_obs),
        "excluded_undeterminable_winner": excluded_undeterminable,
        "usable": len(usable),
        "dt_le_30": sum(1 for o in usable if o.dt <= 30),
        "dt_gt_30_reference": sum(1 for o in usable if o.dt > 30),
        "dt_exact_0": sum(1 for o in usable if o.dt == 0),
    }
    with open(JSON_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"JSON written: {JSON_PATH}")


def _fit_2knot_with_l2(observations: list[PreplantKillObservation], l2: float):
    """Re-runs Task 3's exact-state fit at a different ridge strength, to
    check whether the shape_mid_ratio finding is an artifact of l2=1.0.
    Duplicates fit_preplant_time_model's design construction rather than
    adding an l2 parameter to that function -- this is throwaway
    investigation code, not a production API change."""
    from app.scoring.preplant_time_model import PreplantFit

    usable = [o for o in observations if o.round_won_by_killer_team is not None]
    states = sorted({o.exact_state for o in usable})
    reference_state = "5v5" if "5v5" in states else (states[0] if states else None)
    other_states = [s for s in states if s != reference_state]

    rows, labels = [], []
    for o in usable:
        w1, w2 = shape_basis(o.dt)
        adv_c = _clamp_adv(o.adv)
        atk = 1.0 if o.is_attacker else 0.0
        row = [1.0 if o.exact_state == s else 0.0 for s in other_states]
        row += [w1, w2, w2 * adv_c, w2 * atk, w2 * adv_c * atk]
        rows.append(row)
        labels.append(1.0 if o.round_won_by_killer_team else 0.0)

    X = np.array(rows, dtype=float)
    scaled, _, centre, scale = standardize(X, X)
    beta_scaled = fit_logistic(scaled, np.array(labels), l2=l2)
    beta = back_transform(beta_scaled, centre, scale)

    n_state = len(other_states)
    idx = 1 + n_state
    theta2_pooled = beta[idx + 1]
    theta2_adv_pooled = beta[idx + 2]
    theta2_pooled_atk = beta[idx + 3]
    theta2_adv_atk = beta[idx + 4]
    theta1_pooled = beta[idx + 0]

    intercept_def = theta2_pooled
    intercept_atk = theta2_pooled + theta2_pooled_atk
    slope_def = theta2_adv_pooled
    slope_atk = theta2_adv_pooled + theta2_adv_atk
    shape_mid_ratio = float(theta1_pooled / theta2_pooled) if abs(theta2_pooled) > 1e-9 else 0.5

    return PreplantFit(
        intercept_atk=float(intercept_atk), slope_atk=float(slope_atk),
        intercept_def=float(intercept_def), slope_def=float(slope_def),
        shape_mid_ratio=shape_mid_ratio, state_effects={},
        include_side_interaction=True, n_observations=len(usable),
    )


if __name__ == "__main__":
    main()
