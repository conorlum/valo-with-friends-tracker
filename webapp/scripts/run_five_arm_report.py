r"""Run from webapp/:
    .\.venv\Scripts\python.exe scripts\run_five_arm_report.py

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
"""

import sys
from pathlib import Path

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
    controls_for,
    cross_validate,
    dataset_fingerprint,
    fold_mapping_hash,
    load_all_observations,
    paired_oof_log_loss_delta,
    stable_folds,
)

# Econ spec 8c-i. The harness's list has always mirrored the scoring formula
# one-for-one and it does not have to: the formula's job is to score, the
# harness's is to diagnose. Scoring keeps kill_order_bonus * time_factor fused;
# evaluation splits it, because the fused column correlates with the raw
# kill-order bonus at r = +0.9845 and a weight fitted on it is close to a
# weight on the state term alone.
FEATURE_COMPONENTS_NEW = ["damage", "kill_order_bonus", "time_delta", "econ_component"]

L2_GRID = (0.1, 1.0, 10.0)
BOOTSTRAP_DRAWS = 2000  # predeclared


def _arms(postplant_table):
    return [
        ("arm 0", "today's shipped scoring", {}),
        ("arm 1", "econ deletion only", {"enable_econ_component": True}),
        ("arm 2", "post-plant retune only", {
            "enable_postplant_leverage": True, "postplant_factor_table": postplant_table,
        }),
        ("arm 3", "both -- what actually ships", {
            "enable_econ_component": True, "enable_postplant_leverage": True,
            "postplant_factor_table": postplant_table,
        }),
        ("arm 4", "neutralized control", {"neutralize_econ_terms": True}),
    ]


def _describe(label, point, lo, hi):
    inconclusive = lo <= 0.0 <= hi
    verdict = (
        "INCONCLUSIVE -- the interval spans zero"
        if inconclusive
        else ("DETERIORATION" if point > 0 else "IMPROVEMENT")
    )
    print(f"  {label:<34} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  {verdict}")
    return inconclusive


def main():
    draws = BOOTSTRAP_DRAWS
    for index, arg in enumerate(sys.argv):
        if arg == "--draws" and index + 1 < len(sys.argv):
            draws = int(sys.argv[index + 1])

    db = SessionLocal()

    # Part 4's table is fitted ONCE here, on the full corpus, and used for
    # scoring. Its own out-of-fold treatment belongs to the calibration report
    # in fit_postplant_factor.py; what this protocol requires is that every arm
    # sees the same table and the same folds.
    print("building the post-plant factor table ...", flush=True)
    value_table = build_value_table(extract_postplant_round_seconds(db), w=DEFAULT_W)
    postplant_table = build_factor_table(value_table, extract_postplant_kills(db))

    oofs = {}
    for label, description, scoring_kwargs in _arms(postplant_table):
        print(f"replaying {label} ({description}) ...", flush=True)
        observations = load_all_observations(db, scoring_kwargs=scoring_kwargs)
        if not observations:
            print(f"  {label}: no observations; aborting")
            return
        feature_names = FEATURE_COMPONENTS_NEW + controls_for(PRIMARY_T2)
        result = cross_validate(
            observations, [PRIMARY_T2], feature_names, L2_GRID,
            fold_fn=stable_folds,
        )
        oofs[label] = result["oof"]
        if label == "arm 0":
            match_ids = [o.match_id for o in observations]
            print()
            print("=" * 78)
            print("FIVE-ARM REPORT -- a report, not a gate. No margin, no pass/fail.")
            print("=" * 78)
            print(f"  target: {PRIMARY_T2.name} (k={PRIMARY_T2.k}, gamma={PRIMARY_T2.gamma}, "
                  f"match_weight={PRIMARY_T2.match_weight})")
            print(f"  dataset_fingerprint: {dataset_fingerprint(match_ids)}")
            print(f"  fold_mapping_hash:   {fold_mapping_hash(stable_folds(match_ids))}")
            print(f"  features: {FEATURE_COMPONENTS_NEW}")
            print(f"  paired match-clustered bootstrap, {draws} resamples, two-sided 95%")
            print("  sign convention: loss(arm) - loss(arm 0), so POSITIVE MEANS WORSE")
            print()

    print("CONTRASTS")
    inconclusive = []
    contrasts = {}
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
        if _describe(name, point, lo, hi):
            inconclusive.append(name)

    point, lo, hi = paired_oof_log_loss_delta(oofs["arm 1"], oofs["arm 4"], draws=draws)
    contrasts["arm 1 vs 4"] = point
    if _describe("arm 1 vs 4  structure changed", point, lo, hi):
        inconclusive.append("arm 1 vs 4  structure changed")

    print()
    print("DECOMPOSITION of the econ deletion")
    print(f"  L1 - L0 = (L4 - L0) + (L1 - L4)")
    print(f"  {contrasts['arm 1']:+.5f} = {contrasts['arm 4']:+.5f} + "
          f"{contrasts['arm 1 vs 4']:+.5f}")
    print("  total deletion = information removed + structure changed")

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
    db.close()


if __name__ == "__main__":
    main()
