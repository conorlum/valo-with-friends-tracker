r"""Run from webapp/, read-only:

    DATABASE_URL="$PROD" .\.venv313\Scripts\python.exe scripts\run_postplant_v4_report.py --out DIR
    ... --arms P0,P1            # a subset; anything already cached in DIR is reused
    ... --quick                 # 2 folds / 200 draws. SMOKE TEST, NOT A RESULT.

Arms are independent once the fold assignment exists, and a replay is dominated
by database round trips rather than by CPU, so several processes may share one
--out directory with disjoint --arms. They write disjoint per-arm files; the
identity gate and the fold assignment are computed once and reused by the rest.

The measurement declared in docs/superpowers/2026-09-07-predeclared-values.md,
entry "2026-09-19 -- DECLARATION: the post-plant time factor". Read that entry
before reading this file: the arms, the bucketing and the decision rule are
fixed there, and this script is only their execution.

It reuses the predeclared out-of-fold protocol of run_five_arm_report.py
unchanged -- fixed composite `impact_diff`, target PRIMARY_T2, its control set,
5 match-clustered outer folds at seed 0, inner 3-fold L2 selection on training
matches only, and a 2,000-draw paired match-clustered bootstrap. The sign
convention is the same: loss(arm) - loss(P0), POSITIVE MEANS WORSE.

What this script adds is the arms, and three things they need:

  * a wrapper round impact.py's `_time_factor` (postplant_v4_variants) rather
    than any edit to app/. Arm P0' is the identity gate on that wrapper and
    runs before any contrast;
  * per-fold fitting for the arms that fit anything -- P2L's level constant,
    P4f's scale selection, P5's and P6's value and factor tables, including
    the centring constant c. Those arms are replayed once PER FOLD;
  * per-arm caching. A replay of the corpus is ~16 minutes and there are two
    dozen of them, so every arm's out-of-fold predictions are written to DIR
    as they are produced and reused on a rerun. Nothing else is cached: the
    contrasts are recomputed from the stored predictions every time.
"""

import argparse
import json
import pickle
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.postplant_centering import DegenerateCentering
from app.scoring.postplant_factor import (
    build_factor_table,
    extract_postplant_kills,
    solve_and_apply_centering,
)
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
    dataset_fingerprint,
    fold_mapping_hash,
    load_all_observations,
    paired_oof_log_loss_delta,
    split_observations,
    stable_folds,
)
from app.services.stats_math import weighted_log_loss

import postplant_v4_variants as variants
from postplant_v4_diff_table import build_differential_value_table

COMPOSITE = "impact_diff"
L2_GRID = (0.1, 1.0, 10.0)
BOOTSTRAP_DRAWS = 2000
N_FOLDS = 5
INNER_FOLDS = 3
SEED = 0

# C4's declared grid. 1.00 is P0 itself and is carried as a member so the
# per-fold selection in P4f can decline to change anything.
P4_GRID = (0.70, 0.7826, 0.90, 1.00)

# Arms that fit something and are therefore replayed once per fold.
PER_FOLD_ARMS = {"P2L", "P5", "P5b", "P6"}
# Arms that are pure deterministic rule changes: one replay each.
SIMPLE_ARMS = ["P1", "P2b", "P3a", "P3b", "PC", "PC+"] + [f"P4-{s}" for s in P4_GRID if s != 1.00]

ALL_ARMS = ["P0"] + SIMPLE_ARMS + ["P2L", "P6", "P5", "P5b"]


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# --- persistence -----------------------------------------------------------

def _oof_path(out_dir, arm):
    return Path(out_dir) / f"oof_{arm}.npz"


def save_oof(out_dir, arm, oof):
    np.savez_compressed(_oof_path(out_dir, arm), **oof)


def load_oof(out_dir, arm):
    path = _oof_path(out_dir, arm)
    if not path.exists():
        return None
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


# --- the out-of-fold engine ------------------------------------------------

def outer_cv(obs_for_fold, folds, feature_names, n_folds):
    """run_five_arm_report._outer_cv, unchanged in substance: the observations
    themselves may differ per fold, because a fitted arm's scores depend on a
    table built from that fold's training matches only."""
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
        baselines.extend([float(np.average(train_ds.y, weights=train_ds.w))] * len(test_ds.y))
    return {
        "scores": np.array(scores), "y": np.array(ys), "w": np.array(ws),
        "match_ids": np.array(mids, dtype=int), "baseline": np.array(baselines),
    }


def outer_cv_selecting_scale(obs_by_scale, folds, feature_names, n_folds):
    """P4f. Per outer fold, choose the scale on an INNER cross-validation over
    the training matches only, then fit on the whole training half at that
    scale and predict the untouched test fold.

    Selecting the scale by looking at the outer contrast would be the same
    optimism the fold structure exists to remove -- it would report the best of
    four draws as though it were one.
    """
    scores, ys, ws, mids, baselines, chosen = [], [], [], [], [], {}
    for fold in range(n_folds):
        train_match_ids = sorted({m for m, f in folds.items() if f != fold})
        inner = stable_folds(train_match_ids, n_folds=INNER_FOLDS, seed=SEED)

        best_scale, best_loss = None, float("inf")
        for scale, observations in obs_by_scale.items():
            train_obs, _ = split_observations(observations, folds, fold)
            total_loss, total_weight = 0.0, 0.0
            for inner_fold in range(INNER_FOLDS):
                itrain, itest = split_observations(train_obs, inner, inner_fold)
                if not itrain or not itest:
                    continue
                itrain_ds = build_target(itrain, PRIMARY_T2, feature_names)
                itest_ds = build_target(itest, PRIMARY_T2, feature_names)
                fitted = _fit_and_score(itrain_ds, itest_ds, 1.0)
                if fitted is None:
                    continue
                preds, _ = fitted
                weight = float(itest_ds.w.sum())
                total_loss += weighted_log_loss(preds, itest_ds.y, itest_ds.w) * weight
                total_weight += weight
            if total_weight and total_loss / total_weight < best_loss:
                best_loss, best_scale = total_loss / total_weight, scale
        chosen[fold] = best_scale
        log(f"  P4f fold {fold}: selected scale {best_scale} "
            f"(inner weighted log loss {best_loss:.6f})")

        observations = obs_by_scale[best_scale]
        train_obs, test_obs = split_observations(observations, folds, fold)
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
        baselines.extend([float(np.average(train_ds.y, weights=train_ds.w))] * len(test_ds.y))
    return {
        "scores": np.array(scores), "y": np.array(ys), "w": np.array(ws),
        "match_ids": np.array(mids, dtype=int), "baseline": np.array(baselines),
    }, chosen


# --- P2L's level constant --------------------------------------------------

def p2l_lambda(kills):
    """The constant that spreads P2b's extra death-side leverage uniformly over
    every post-plant death, so P2b - P2L isolates the TARGETING from the LEVEL.

    Solved on the kills of the matches handed in -- training matches only when
    the caller is a fold. Weighted by what a death actually scores with,
    kill_order_bonus * traded_factor, not by the factor alone.
    """
    extra, base_total = 0.0, 0.0
    for kill in kills:
        seconds = kill.exact_seconds
        weight = kill.kill_order_bonus * kill.traded_factor
        if variants.OVERRIDE_START <= seconds <= variants.SPIKE_SECONDS:
            shipped = variants.POST_RESOLUTION_FACTOR
            extra += weight * (variants.p2b_death_decay(seconds) - shipped)
        else:
            shipped = 1 + seconds / 53
        base_total += weight * shipped
    if not base_total:
        raise ValueError("no post-plant death weight to spread the level over")
    return 1 + extra / base_total


# --- replays ---------------------------------------------------------------

EXPECTED_MATCHES = None  # set from P0; every later arm is checked against it


def replay(db, arm, variant=None, scoring_kwargs=None):
    variants.activate(variant)
    try:
        observations = load_all_observations(db, scoring_kwargs=scoring_kwargs or {})
    finally:
        variants.activate(None)
        # End the read transaction between replays so no snapshot is held open
        # across the run. Read-only, so there is nothing to lose by rolling back.
        db.rollback()
    seen = frozenset(o.match_id for o in observations)
    if EXPECTED_MATCHES is not None and seen != EXPECTED_MATCHES:
        added = sorted(seen - EXPECTED_MATCHES)
        removed = sorted(EXPECTED_MATCHES - seen)
        raise SystemExit(
            f"STOP: arm {arm} saw a different corpus than P0 "
            f"({len(added)} added {added[:5]}, {len(removed)} removed {removed[:5]}). "
            f"The arms would not be comparable; rerun from a clean output directory.")
    return observations


def identity_gate(db, out_dir):
    """Declared: the wrapper with no variant must reproduce the reference
    replay exactly, every observation of every round. Reported whatever it
    says; a single difference voids the mechanism and stops the session."""
    log("identity gate: replaying P0 unpatched ...")
    variants.uninstall()
    t0 = time.time()
    reference = load_all_observations(db)
    unpatched_seconds = time.time() - t0
    log(f"  {len(reference):,} observations in {unpatched_seconds:.0f}s")

    log("identity gate: replaying P0' through the wrapper, variant NONE ...")
    variants.install()
    t0 = time.time()
    through_wrapper = replay(db, "P0'", variant=None)
    patched_seconds = time.time() - t0
    log(f"  {len(through_wrapper):,} observations in {patched_seconds:.0f}s")

    differences = []
    if len(reference) != len(through_wrapper):
        differences.append(f"length {len(reference)} vs {len(through_wrapper)}")
    else:
        for index, (left, right) in enumerate(zip(reference, through_wrapper)):
            if left != right:
                differences.append(f"observation {index}: {left} != {right}")
                if len(differences) >= 5:
                    break

    result = {
        "observations": len(reference),
        "identical": not differences,
        "differences": differences,
        "unpatched_replay_seconds": round(unpatched_seconds, 1),
        "patched_replay_seconds": round(patched_seconds, 1),
    }
    (Path(out_dir) / "identity_gate.json").write_text(json.dumps(result, indent=2))
    if differences:
        log("IDENTITY GATE FAILED -- the wrapper is not transparent. STOP.")
        for difference in differences:
            log(f"  {difference}")
        raise SystemExit(2)
    log(f"  IDENTITY GATE PASSED: {len(reference):,} observations identical")
    return reference


# --- fitted per-fold tables ------------------------------------------------

# The three value-table constructions the per-fold arms use. Only the ladder
# differs; the differencing, denominators, clamp and centring are Part 4's in
# every case.
TABLE_KINDS = {
    "P6": "part4",           # Part 4 exactly as built
    "P5": "diff",            # differential rungs BELOW Part 4's exact rung
    "P5b": "diff_no_exact",  # differential rung IS rung 1 (the amendment)
}


def build_fold_tables(all_seconds, all_kills, folds, n_folds, label):
    """Part 4's per-fold construction, or a differential ladder swapped in.
    Training matches only, including the centring constant."""
    kind = TABLE_KINDS[label]
    tables, centring = {}, {}
    for fold in range(n_folds):
        train = {m for m, f in folds.items() if f != fold}
        rows = [s for s in all_seconds if s.match_id in train]
        kills = [k for k in all_kills if k.match_id in train]
        log(f"  {label} fold {fold}: {len(rows):,} round-seconds, {len(kills):,} kills")
        if kind == "part4":
            value_table = build_value_table(rows, w=DEFAULT_W)
        else:
            value_table = build_differential_value_table(
                rows, DEFAULT_W, use_exact_rung=(kind == "diff"))
        table = build_factor_table(value_table, kills)
        try:
            centring[fold] = solve_and_apply_centering(table, kills)
        except DegenerateCentering as exc:
            log(f"  {label} fold {fold}: DEGENERATE centring ({exc}); c = 1.0")
            centring[fold] = None
        else:
            r = centring[fold]
            log(f"  {label} fold {fold}: c = {r.c:.6f}  death-side residual "
                f"{r.death_side_residual:+.4%}  ({r.supported_kills:,} supported / "
                f"{r.fallback_kills:,} fallback)")
        tables[fold] = table
    return tables, centring


# --- reporting -------------------------------------------------------------

def check_pairing(name, oof, reference):
    """paired_oof_log_loss_delta pairs by MATCH, not by row index, so it will
    happily compare two arms whose row sets differ within a match and report a
    contrast that is partly a difference in population. Two arms of the same
    target should produce identical row counts per match; if they do not,
    something upstream dropped rows for one arm only and the contrast is not
    the quantity it claims to be."""
    import collections
    left = collections.Counter(int(m) for m in oof["match_ids"])
    right = collections.Counter(int(m) for m in reference["match_ids"])
    if left != right:
        differing = [m for m in set(left) | set(right) if left.get(m) != right.get(m)]
        return (f"row counts differ from the reference on {len(differing)} matches "
                f"(e.g. {sorted(differing)[:5]})")
    return None


def verdict(point, lo, hi):
    if point == 0.0 and lo == 0.0 and hi == 0.0:
        return "IDENTICAL"
    if lo <= 0.0 <= hi:
        return "INCONCLUSIVE"
    return "HARM" if point > 0 else "IMPROVEMENT"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--arms", default="")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_DRAWS)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    draws, n_folds = args.draws, N_FOLDS
    if args.quick:
        draws, n_folds = 200, 2
        log("*** --quick: SMOKE TEST, NOT A RESULT ***")

    wanted = set(args.arms.split(",")) if args.arms else set(ALL_ARMS + ["P4f"])
    db = SessionLocal()
    # Declared constraint: every query here is read-only, enforced by the
    # database rather than by reading the code and trusting it.
    #
    # SESSION characteristics, not one REPEATABLE READ transaction. A single
    # snapshot would make the arms trivially comparable, but this run is ~9
    # hours long and holding one open that long against PRODUCTION pins every
    # table's dead tuples for the duration -- vacuum cannot pass a transaction
    # older than the rows it wants to remove. Read-only is what the constraint
    # is actually for; the comparability it also bought is bought back below by
    # checking every arm against P0's match set instead, which detects a corpus
    # change rather than merely hiding it.
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    features = [COMPOSITE] + controls_for(PRIMARY_T2)

    # The identity gate is a property of the WRAPPER, not of an arm, so it runs
    # once per output directory and its result is kept. Re-running it per
    # process would cost two full replays each and prove the same thing again.
    base_obs = None
    gate_path = out_dir / "identity_gate.json"
    if not gate_path.exists():
        base_obs = identity_gate(db, out_dir)
    elif not json.loads(gate_path.read_text())["identical"]:
        raise SystemExit("STOP: the recorded identity gate did not pass")
    variants.install()

    # The fold assignment comes from P0's match set and is shared by every arm.
    # Cached, because deriving it otherwise costs a whole replay on every rerun
    # and the assignment must not drift between runs in any case.
    ids_path = out_dir / "p0_match_ids.json"
    if base_obs is None and ids_path.exists():
        match_ids = json.loads(ids_path.read_text())
    else:
        if base_obs is None:
            base_obs = replay(db, "P0")
        match_ids = [o.match_id for o in base_obs]
        ids_path.write_text(json.dumps(match_ids))
    globals()["EXPECTED_MATCHES"] = frozenset(match_ids)
    folds = stable_folds(match_ids, n_folds=n_folds, seed=SEED)
    meta = {
        "matches": len(set(match_ids)),
        # From match_ids, not base_obs: a process that reuses the recorded
        # identity gate and the cached fold assignment never replays P0 at all,
        # and match_ids is one entry per observation either way.
        "observations": len(match_ids),
        "dataset_fingerprint": dataset_fingerprint(match_ids),
        "fold_mapping_hash": fold_mapping_hash(folds),
        "n_folds": n_folds,
        "draws": draws,
        "quick": args.quick,
        "target": PRIMARY_T2.name,
        "controls": controls_for(PRIMARY_T2),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    log(f"corpus {meta['matches']:,} matches, {meta['observations']:,} observations")
    log(f"dataset_fingerprint {meta['dataset_fingerprint']}")
    log(f"fold_mapping_hash   {meta['fold_mapping_hash']}")

    # P0's observations are kept on disk as well as its predictions: P4f needs
    # the grid's 1.00 member, which IS P0's configuration, and reloading them
    # costs seconds against the half hour a re-replay would.
    obs_path = out_dir / "obs_P0.pkl"
    if base_obs is not None and not obs_path.exists():
        obs_path.write_bytes(pickle.dumps(base_obs, protocol=pickle.HIGHEST_PROTOCOL))
        log(f"  P0 observations cached for reuse ({obs_path.stat().st_size / 1e6:.0f} MB)")
    if load_oof(out_dir, "P0") is None:
        log("P0: cross-validating ...")
        save_oof(out_dir, "P0", outer_cv(lambda f: base_obs, folds, features, n_folds))
    del base_obs

    # P4f selects among the SAME replays the individual P4-s arms report, so
    # the grid is replayed once and held until both have used it. Replaying it
    # twice would cost an hour to produce identical numbers.
    need_p4f = "P4f" in wanted and load_oof(out_dir, "P4f") is None
    obs_by_scale = {}

    for arm in SIMPLE_ARMS:
        is_grid_member = arm.startswith("P4-")
        if arm not in wanted and not (is_grid_member and need_p4f):
            continue
        cached = load_oof(out_dir, arm) is not None
        if cached and not (is_grid_member and need_p4f):
            continue
        log(f"{arm}: replaying ...")
        t0 = time.time()
        observations = replay(db, arm, variant=variants.variant_for(arm))
        log(f"  replayed in {time.time() - t0:.0f}s")
        if not cached:
            log(f"{arm}: cross-validating ...")
            save_oof(out_dir, arm, outer_cv(lambda f: observations, folds, features, n_folds))
        if is_grid_member and need_p4f:
            obs_by_scale[float(arm.split("-", 1)[1])] = observations
        else:
            del observations

    if need_p4f:
        obs_path = out_dir / "obs_P0.pkl"
        if obs_path.exists():
            log("P4f: loading the grid's 1.00 member from P0's cached observations ...")
            obs_by_scale[1.00] = pickle.loads(obs_path.read_bytes())
        else:
            log("P4f: replaying the grid's 1.00 member (P0's own configuration) ...")
            obs_by_scale[1.00] = replay(db, "P0", variant=None)
        missing = [s for s in P4_GRID if s not in obs_by_scale]
        if missing:
            raise SystemExit(f"P4f needs every declared scale replayed; missing {missing}")
        oof, chosen = outer_cv_selecting_scale(obs_by_scale, folds, features, n_folds)
        save_oof(out_dir, "P4f", oof)
        (out_dir / "p4f_selected_scales.json").write_text(
            json.dumps({str(k): v for k, v in chosen.items()}, indent=2))
        del obs_by_scale

    if wanted & {"P2L", "P5", "P5b", "P6"}:
        log("extracting post-plant rows (raw data, extracted once) ...")
        all_seconds = extract_postplant_round_seconds(db)
        all_kills = extract_postplant_kills(db)
        log(f"  {len(all_seconds):,} round-seconds, {len(all_kills):,} kills")

        if "P2L" in wanted and load_oof(out_dir, "P2L") is None:
            lambdas = {}
            per_fold_obs = {}
            for fold in range(n_folds):
                train = {m for m, f in folds.items() if f != fold}
                lam = p2l_lambda([k for k in all_kills if k.match_id in train])
                lambdas[fold] = lam
                log(f"P2L fold {fold}: lambda = {lam:.6f}; replaying ...")
                per_fold_obs[fold] = replay(
                    db, "P2L", variant=variants.variant_for("P2L", lam=lam))
            save_oof(out_dir, "P2L",
                     outer_cv(lambda f: per_fold_obs[f], folds, features, n_folds))
            (out_dir / "p2l_lambdas.json").write_text(
                json.dumps({str(k): v for k, v in lambdas.items()}, indent=2))
            del per_fold_obs

        for arm in ("P6", "P5", "P5b"):
            if arm not in wanted or load_oof(out_dir, arm) is not None:
                continue
            log(f"{arm}: building per-fold tables ...")
            tables, centring = build_fold_tables(
                all_seconds, all_kills, folds, n_folds, arm)
            per_fold_obs = {}
            for fold in range(n_folds):
                log(f"{arm}: replaying fold {fold} ...")
                t0 = time.time()
                per_fold_obs[fold] = replay(db, arm, scoring_kwargs={
                    "enable_postplant_leverage": True,
                    "postplant_factor_table": tables[fold],
                })
                log(f"  fold {fold} replayed in {time.time() - t0:.0f}s")
            save_oof(out_dir, arm,
                     outer_cv(lambda f: per_fold_obs[f], folds, features, n_folds))
            (out_dir / f"{arm.lower()}_centring.json").write_text(json.dumps({
                str(f): (None if r is None else {
                    "c": r.c, "death_side_residual": r.death_side_residual,
                    "supported_kills": r.supported_kills,
                    "fallback_kills": r.fallback_kills,
                }) for f, r in centring.items()
            }, indent=2))
            if TABLE_KINDS[arm] != "part4":
                (out_dir / f"{arm.lower()}_rungs.json").write_text(json.dumps({
                    str(f): dict(tables[f]._value_table.rung_counts)
                    for f in range(n_folds)
                }, indent=2))
            del per_fold_obs, tables

    variants.uninstall()
    db.rollback()
    db.close()

    # ---- contrasts, recomputed from the stored predictions every run -------
    reference = load_oof(out_dir, "P0")
    if reference is None:
        log("no P0 predictions yet; nothing to contrast")
        return

    print()
    print("=" * 78)
    print("POST-PLANT TIME FACTOR -- out-of-fold contrasts against P0")
    print("=" * 78)
    print(f"  target {PRIMARY_T2.name}, controls {controls_for(PRIMARY_T2)}")
    print(f"  fixed composite '{COMPOSITE}', {n_folds} match-clustered folds, "
          f"{draws} bootstrap draws, two-sided 95%")
    print("  sign convention: loss(arm) - loss(P0). POSITIVE MEANS WORSE.")
    print("  an interval spanning zero is INCONCLUSIVE, never 'no harm found'")
    if args.quick:
        print("  *** --quick: SMOKE TEST, NOT A RESULT ***")
    print()

    results = {}
    for arm in ALL_ARMS + ["P4f"]:
        if arm == "P0":
            continue
        oof = load_oof(out_dir, arm)
        if oof is None:
            print(f"  {arm:<12} NOT RUN")
            continue
        mismatch = check_pairing(arm, oof, reference)
        if mismatch:
            print(f"  {arm:<12} UNPAIRED -- {mismatch}")
            results[arm] = {"verdict": "UNPAIRED", "detail": mismatch}
            continue
        point, lo, hi = paired_oof_log_loss_delta(oof, reference, draws=draws)
        results[arm] = {"point": point, "lo": lo, "hi": hi,
                        "verdict": verdict(point, lo, hi)}
        print(f"  {arm:<12} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  "
              f"{results[arm]['verdict']}")

    p6 = load_oof(out_dir, "P6")
    if p6 is not None:
        printed = False
        for arm in ("P5", "P5b"):
            other = load_oof(out_dir, arm)
            if other is None:
                continue
            if not printed:
                print()
                print("  the regrouping against the design it replaces "
                      "(loss(arm) - loss(P6)):")
                printed = True
            point, lo, hi = paired_oof_log_loss_delta(other, p6, draws=draws)
            name = f"{arm} vs P6"
            results[name] = {"point": point, "lo": lo, "hi": hi,
                             "verdict": verdict(point, lo, hi)}
            print(f"  {name:<12} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  "
                  f"{results[name]['verdict']}")

    p2b, p2l = load_oof(out_dir, "P2b"), load_oof(out_dir, "P2L")
    if p2b is not None and p2l is not None:
        point, lo, hi = paired_oof_log_loss_delta(p2b, p2l, draws=draws)
        results["P2b vs P2L"] = {"point": point, "lo": lo, "hi": hi,
                                 "verdict": verdict(point, lo, hi)}
        print()
        print("  the targeting, with the level held equal "
              "(loss(P2b) - loss(P2L)):")
        print(f"  {'P2b vs P2L':<12} {point:+.5f}  [{lo:+.5f}, {hi:+.5f}]  "
              f"{results['P2b vs P2L']['verdict']}")

    (out_dir / "contrasts.json").write_text(json.dumps(results, indent=2))
    print()
    print(f"  written to {out_dir}")


if __name__ == "__main__":
    main()
