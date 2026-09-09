r"""Run from webapp/:
    .\.venv\Scripts\python.exe scripts\run_five_arm_report.py
    .\.venv\Scripts\python.exe scripts\run_five_arm_report.py --quick   # smoke test

The predeclared five-arm measurement protocol (econ spec section 8d-i, which
the plant-window spec's Part 4 leakage section also defers to). Read-only.

    | arm | what it is                                            |
    |-----|-------------------------------------------------------|
    |  0  | today's shipped scoring -- the reference               |
    |  1  | econ deletion only                                     |
    |  2  | post-plant retune only                                 |
    |  3  | both -- what actually ships                            |
    |  4  | neutralized control: the two econ terms pinned to 1.0  |
    |     | INSIDE today's combination structure                   |

Arm 4 exists because deleting terms from damages + mean(econ, time, swing)
also changes the mean's ARITY. Without it, arm 1 confounds "econ information
is gone" with "the remaining components were re-weighted", and the report
cannot say which one it caught. Hence the decomposition, reported in full:

    L1 - L0  =  (L4 - L0)  +  (L1 - L4)
     total       information    structure
     deletion    removed        changed

THIS IS A REPORT, NOT A GATE. There is no margin and no pass/fail: on this
target current_impact beats kill_diff by +0.00122 [-0.00171, +0.00420] and acs
sits at -0.00134 [-0.00328, +0.00064], so any threshold would be ~0.0003 and
would decide by noise. An arm whose interval spans zero is reported as
INCONCLUSIVE in those words, never rounded into "no harm found" -- the failure
this replaces a gate with is not shipping something worse, it is shipping
something worse WITHOUT KNOWING.

Sign convention: the contrast is loss(arm) - loss(arm 0), so POSITIVE MEANS
DETERIORATION.


REBUILT 2026-09-09, after an external review found the first version violated
two of section 8d-i's own predeclared sentences. Both are worth stating,
because the first version's numbers looked entirely reasonable while measuring
the wrong thing:

1. "Each arm is evaluated as a FIXED SCORING COMPOSITE... refitting diagnostic
   component weights per arm is not a substitute, because a search that
   recovers the loss by reweighting has answered a different question than
   what that configuration is worth."

   The first version handed each arm's COMPONENT COLUMNS to cross_validate,
   which fits a free coefficient per component. So it asked "how much signal
   is in these columns, given an evaluator allowed to reweight them" instead
   of "what is this configuration worth". Changing the scoring formula's
   top-level weights would not have moved a single reported number.

   Now every arm is evaluated on `impact_diff` -- the scorer's OWN output
   under that arm's configuration -- as a single predictor alongside the
   nuisance controls. One coefficient, which is a monotone rescale the log
   loss needs and cannot use to repair a bad composite.

   Note what this deletes: the first version's elaborate per-arm feature
   lists. Those existed only to serve the wrong estimand. Under a fixed
   composite every arm uses the SAME feature list and differs in the VALUE of
   impact_diff, which is the point.

2. "`V` and any other outcome-fitted table are estimated OUT-OF-FOLD -- a gate
   read on an in-sample table is not evidence."

   The first version built the value table and factor table once from the
   entire database, then replayed arms 2 and 3 through it before
   cross-validating. Test-fold outcomes had therefore already shaped the
   support decisions, the smoothing, the denominators and the factor values
   that became training features. A code comment there asserted the
   out-of-fold treatment "belongs to the calibration report" -- that
   contradicted the spec, and was wrong.

   Now the tables are rebuilt inside each outer fold from TRAINING matches
   only, and arms 2 and 3 are replayed once per fold against that fold's
   table. That is the expensive part of this script and it is not optional.

The component regression survives as a SEPARATELY LABELLED DIAGNOSTIC at the
foot of the report. It answers a real question -- where the signal sits -- but
it is not the arm contrast and is never quoted as one.
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.scoring.postplant_factor import build_factor_table, extract_postplant_kills
from app.scoring.postplant_value_table import (
    DEFAULT_W,
    build_value_table,
    extract_postplant_round_seconds,
)
from app.services.impact_eval import (
    PRIMARY_T2,
    _fit_and_score,
    _select_config,
    build_target,
    controls_for,
    cross_validate,
    dataset_fingerprint,
    fold_mapping_hash,
    load_all_observations,
    oof_metrics,
    paired_oof_log_loss_delta,
    split_observations,
    stable_folds,
)

# THE FIXED COMPOSITE. `impact_diff` is the round-level differential of the
# scorer's own per-player `impact`, so it already embodies whatever formula the
# arm's scoring_kwargs selected -- including the top-level weights, the mean's
# arity, and the econ term's presence or absence. This is the same column
# CURRENT_IMPACT_CANDIDATE scores with weight 1.0.
COMPOSITE = "impact_diff"

# Econ spec 8c-i. Retained ONLY for the labelled diagnostic below, never for
# an arm contrast. Scoring keeps kill_order_bonus * time_factor fused;
# evaluation splits it, because the fused column correlates with the raw
# kill-order bonus at r = +0.9845.
FEATURE_COMPONENTS_NEW = ["damage", "kill_order_bonus", "time_delta", "econ_component"]
FEATURE_COMPONENTS_OLD = ["damage", "econ_impact", "time_impact", "swing_impact"]

L2_GRID = (0.1, 1.0, 10.0)
BOOTSTRAP_DRAWS = 2000  # predeclared
N_FOLDS = 5
INNER_FOLDS = 3
SEED = 0

# label, description, scoring_kwargs builder (takes the fold's factor table or
# None), and whether the arm depends on that table at all.
ARMS = [
    ("arm 0", "today's shipped scoring", lambda t: {}, False),
    ("arm 1", "econ deletion only", lambda t: {"enable_econ_component": True}, False),
    ("arm 2", "post-plant retune only",
     lambda t: {"enable_postplant_leverage": True, "postplant_factor_table": t}, True),
    ("arm 3", "both -- what actually ships",
     lambda t: {"enable_econ_component": True, "enable_postplant_leverage": True,
                "postplant_factor_table": t}, True),
    ("arm 4", "neutralized control", lambda t: {"neutralize_econ_terms": True}, False),
]

DIAGNOSTIC_COMPONENTS = {
    "arm 0": FEATURE_COMPONENTS_OLD, "arm 1": FEATURE_COMPONENTS_NEW,
    "arm 2": FEATURE_COMPONENTS_OLD, "arm 3": FEATURE_COMPONENTS_NEW,
    "arm 4": FEATURE_COMPONENTS_OLD,
}


def _describe(label, point, lo, hi):
    # A ZERO-WIDTH interval at exactly zero is not "inconclusive" -- it means
    # the two arms produced identical predictions, which is a statement about
    # the comparison being degenerate rather than about the effect being
    # unresolvable. Reporting it as inconclusive would hide a broken contrast
    # behind a legitimate-sounding verdict.
    if point == 0.0 and lo == 0.0 and hi == 0.0:
        print(f"  {label:<34} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  "
              f"*** IDENTICAL -- this arm did not differ from its reference ***")
        return "identical"
    inconclusive = lo <= 0.0 <= hi
    verdict = (
        "INCONCLUSIVE -- the interval spans zero"
        if inconclusive
        else ("DETERIORATION" if point > 0 else "IMPROVEMENT")
    )
    print(f"  {label:<34} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  {verdict}")
    return "inconclusive" if inconclusive else None


def _outer_cv(obs_for_fold, folds, feature_names, n_folds):
    """Outer CV where the observations THEMSELVES may differ per fold.

    cross_validate cannot do this: it takes one observation list and folds it
    internally, which is exactly the assumption that forced the whole-corpus
    table. Arms 2 and 3 need a different replay per fold, because their scores
    depend on a table that must be fitted on that fold's training matches only.

    Everything else mirrors cross_validate: inner config/l2 selection on the
    training matches, standardization and fitting on training only, prediction
    on untouched test matches, and a baseline built from the TRAINING half's
    base rate so no test fold seeds its own comparator.
    """
    scores, ys, ws, mids, baselines = [], [], [], [], []
    for fold in range(n_folds):
        observations = obs_for_fold(fold)
        train_obs, test_obs = split_observations(observations, folds, fold)
        if not train_obs or not test_obs:
            continue
        config, l2 = _select_config(
            train_obs, [PRIMARY_T2], feature_names, L2_GRID, INNER_FOLDS, SEED,
            None, fold_fn=stable_folds,
        )
        train_ds = build_target(train_obs, config, feature_names)
        test_ds = build_target(test_obs, config, feature_names)
        fitted = _fit_and_score(train_ds, test_ds, l2)
        if fitted is None:
            continue
        preds, _beta = fitted
        scores.extend(preds.tolist())
        ys.extend(test_ds.y.tolist())
        ws.extend(test_ds.w.tolist())
        mids.extend(test_ds.match_ids.tolist())
        train_rate = float(np.average(train_ds.y, weights=train_ds.w))
        baselines.extend([train_rate] * len(test_ds.y))
    return {
        "scores": np.array(scores), "y": np.array(ys), "w": np.array(ws),
        "match_ids": np.array(mids, dtype=int), "baseline": np.array(baselines),
    }


def main():
    draws = BOOTSTRAP_DRAWS
    n_folds = N_FOLDS
    quick = "--quick" in sys.argv
    for index, arg in enumerate(sys.argv):
        if arg == "--draws" and index + 1 < len(sys.argv):
            draws = int(sys.argv[index + 1])
    if quick:
        draws, n_folds = 200, 2

    db = SessionLocal()

    # The post-plant ROWS are raw data, not a fitted object, so they are
    # extracted once. What must be per-fold is everything FITTED from them:
    # the value table and the factor table below.
    print("extracting post-plant rows ...", flush=True)
    all_seconds = extract_postplant_round_seconds(db)
    all_kills = extract_postplant_kills(db)
    print(f"  {len(all_seconds):,} round-seconds, {len(all_kills):,} kills", flush=True)

    # Fold assignment comes from arm 0's match set and is shared by every arm.
    print("replaying arm 0 (also fixes the fold assignment) ...", flush=True)
    t0 = time.time()
    base_obs = load_all_observations(db)
    print(f"  {len(base_obs):,} observations in {time.time() - t0:.0f}s", flush=True)
    match_ids = [o.match_id for o in base_obs]
    folds = stable_folds(match_ids, n_folds=n_folds, seed=SEED)

    # Per-fold tables, fitted on TRAINING matches only. This is the leakage
    # fix: support decisions, smoothing, denominators and centring all see
    # only the training half.
    fold_tables = {}
    for fold in range(n_folds):
        train = {m for m, f in folds.items() if f != fold}
        print(f"building fold {fold}'s post-plant table on {len(train):,} "
              f"training matches ...", flush=True)
        value_table = build_value_table(
            [s for s in all_seconds if s.match_id in train], w=DEFAULT_W
        )
        fold_tables[fold] = build_factor_table(
            value_table, [k for k in all_kills if k.match_id in train]
        )

    composite_features = [COMPOSITE] + controls_for(PRIMARY_T2)

    oofs, arm_obs = {}, {}
    for label, description, kwargs_fn, needs_table in ARMS:
        if not needs_table:
            observations = base_obs if label == "arm 0" else load_all_observations(
                db, scoring_kwargs=kwargs_fn(None)
            )
            print(f"replaying {label} ({description}) ...", flush=True)
            arm_obs[label] = {f: observations for f in range(n_folds)}
        else:
            per_fold = {}
            for fold in range(n_folds):
                print(f"replaying {label} ({description}) for fold {fold} ...",
                      flush=True)
                t0 = time.time()
                per_fold[fold] = load_all_observations(
                    db, scoring_kwargs=kwargs_fn(fold_tables[fold])
                )
                print(f"  fold {fold} replayed in {time.time() - t0:.0f}s", flush=True)
            arm_obs[label] = per_fold
        oofs[label] = _outer_cv(
            lambda f, lbl=label: arm_obs[lbl][f], folds, composite_features, n_folds
        )

    print()
    print("=" * 78)
    print("FIVE-ARM REPORT -- a report, not a gate. No margin, no pass/fail.")
    print("=" * 78)
    print(f"  target: {PRIMARY_T2.name} (k={PRIMARY_T2.k}, gamma={PRIMARY_T2.gamma}, "
          f"match_weight={PRIMARY_T2.match_weight})")
    print(f"  dataset_fingerprint: {dataset_fingerprint(match_ids)}")
    print(f"  fold_mapping_hash:   {fold_mapping_hash(folds)}")
    print(f"  ESTIMAND: each arm scored as the FIXED composite '{COMPOSITE}'")
    print(f"            plus nuisance controls {controls_for(PRIMARY_T2)}")
    print(f"            -- one coefficient on the composite, no component reweighting")
    print(f"  LEAKAGE:  post-plant value/factor tables rebuilt per outer fold on")
    print(f"            training matches only ({n_folds} folds)")
    print(f"  paired match-clustered bootstrap, {draws} resamples, two-sided 95%")
    print("  sign convention: loss(arm) - loss(arm 0), so POSITIVE MEANS WORSE")
    if quick:
        print("  *** --quick: 2 folds / 200 draws. SMOKE TEST, NOT A RESULT. ***")
    print()

    print("CONTRASTS")
    inconclusive, identical, contrasts = [], [], {}
    for label in ("arm 1", "arm 2", "arm 3", "arm 4"):
        point, lo, hi = paired_oof_log_loss_delta(
            oofs[label], oofs["arm 0"], draws=draws
        )
        contrasts[label] = point
        name = {
            "arm 1": "arm 1 vs 0  econ deletion",
            "arm 2": "arm 2 vs 0  post-plant retune",
            "arm 3": "arm 3 vs 0  BOTH (what ships)",
            "arm 4": "arm 4 vs 0  information removed",
        }[label]
        verdict = _describe(name, point, lo, hi)
        if verdict == "inconclusive":
            inconclusive.append(name)
        elif verdict == "identical":
            identical.append(name)

    point, lo, hi = paired_oof_log_loss_delta(oofs["arm 1"], oofs["arm 4"], draws=draws)
    contrasts["arm 1 vs 4"] = point
    verdict = _describe("arm 1 vs 4  structure changed", point, lo, hi)
    if verdict == "inconclusive":
        inconclusive.append("arm 1 vs 4  structure changed")
    elif verdict == "identical":
        identical.append("arm 1 vs 4  structure changed")

    print()
    print("DECOMPOSITION of the econ deletion")
    print("  L1 - L0 = (L4 - L0) + (L1 - L4)")
    print(f"  {contrasts['arm 1']:+.5f} = {contrasts['arm 4']:+.5f} + "
          f"{contrasts['arm 1 vs 4']:+.5f}")
    print("  total deletion = information removed + structure changed")
    print()
    print("  Under the FIXED composite these three are genuinely different")
    print("  quantities. The first version of this report found the structure")
    print("  term at exactly -0.00000 and explained it by the two old columns")
    print("  spanning the same linear subspace as {kill_order_bonus, time_delta}.")
    print("  That explanation only holds when an evaluator is FITTING over the")
    print("  span; a fixed composite computes an arithmetic value, and changing")
    print("  the mean's arity changes it. Treat any prior quote of 'the")
    print("  structure change is free' as withdrawn.")

    if identical:
        print()
        print("  *** These contrasts came back IDENTICAL. That is a broken")
        print("  *** comparison, not a result:")
        for name in identical:
            print(f"    - {name}")

    if inconclusive:
        print()
        print("  The following contrasts are INCONCLUSIVE -- their intervals span")
        print("  zero. That is not the same as 'no harm found':")
        for name in inconclusive:
            print(f"    - {name}")

    print()
    print("  Arm 3 is the configuration that reaches players. Its number travels")
    print("  with any later quote of a post-change yardstick figure. Effects do")
    print("  not add: two changes can each look harmless and combine badly.")
    print()
    print("  Note, and it is structural rather than a bug: this replay runs in")
    print("  EX-ANTE mode, where the econ component is exactly 0 for every row")
    print("  (its own leakage gate). So arms 1 and 3 measure the DELETION of the")
    print("  two old ex-ante econ terms with nothing replacing them in this mode.")
    print("  That is why a cost is expected here and why ECON_SCALE cannot be")
    print("  fitted by this harness at all (econ spec section 9a).")

    # ---- SEPARATELY LABELLED DIAGNOSTIC ------------------------------------
    print()
    print("=" * 78)
    print("DIAGNOSTIC ONLY -- component regression. NOT AN ARM CONTRAST.")
    print("=" * 78)
    print("  This refits a free coefficient per component, which is precisely")
    print("  what section 8d-i rules out as an arm comparison. It is reported")
    print("  because 'where does the signal sit' is a real question, and it is")
    print("  labelled because the first version of this report quoted exactly")
    print("  this quantity as though it were the arm contrast above.")
    print("  Fold-0 tables are used for arms 2/3 here; that is adequate for a")
    print("  descriptive read and would NOT be adequate for a contrast.")
    print()
    for label, _description, _kwargs_fn, _needs in ARMS:
        components = DIAGNOSTIC_COMPONENTS[label]
        result = cross_validate(
            arm_obs[label][0], [PRIMARY_T2], components + controls_for(PRIMARY_T2),
            L2_GRID, n_folds=n_folds, fold_fn=stable_folds,
        )
        metrics = oof_metrics(result["oof"], draws=200)
        lo, hi = metrics["weighted_log_loss_ci"]
        print(f"  {label}: components={components}")
        print(f"        weighted log loss {metrics['weighted_log_loss']:.6f} "
              f"[{lo:.6f}, {hi:.6f}]   n={metrics['n']:,}")
    db.close()


if __name__ == "__main__":
    main()
