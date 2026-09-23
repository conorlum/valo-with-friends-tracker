r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_decl12.py --out DIR
    ... --row-motion            # row motion only, under the LIVE rc3 configuration
    ... --limit N               # last N matches only. SMOKE TEST, NOT A RESULT.

DECLARATION 12 (docs/superpowers/2026-09-07-predeclared-values.md). Read that
entry first; the arms, predictions and stop rule are fixed there.

Two things make this runner different from every earlier post-plant script:

  * IT SCORES rc3. Every earlier v4 script replayed `build_impact_rows_for_match`
    with no configuration, which is the LEGACY formula (no econ component, no
    assists, no trade credit) -- not what has shipped since 2026-09-18. Here the
    replay uses app.scoring.impact_runtime's active rc3 manifest, EX-ANTE
    (use_realized_swing=False): the buy-disruption econ component reads round
    N+1, so under ex-ante it abstains to exactly 0, which is the leakage gate
    both outcome targets require. Neither arm touches econ, so its absence is
    the same on both sides of every contrast. Row motion, which is about STORED
    rows rather than prediction, uses the live configuration unchanged.

  * One replay per arm feeds BOTH targets -- C (the round's own outcome, fixed
    L2, declaration 7's protocol) and T2 (the forward target, the report's
    outer_cv with inner L2 selection) -- so the arms are replayed four times,
    not eight.

Arms:
  P0      rc3 as shipped
  F-1.00  post-plant flat 1.0, decided rounds left at the shipped 0.5
  N       no time factor: 1.0 everywhere, 0 once the round is decided
  N+A     N, and assists on kills after the round is decided are removed
          from the assists component (D per assist)
"""

import argparse
import dataclasses
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app.services.impact_eval as impact_eval
from app.db import SessionLocal
from app.models.kill_event import KillEvent
from app.models.match import MatchPlayer
from app.models.player import Player
from app.models.round import Round
from app.scoring import impact as impact_module
from app.scoring.impact_runtime import active_scoring_config
from app.services.impact_eval import (
    PRIMARY_T2,
    _feature_value,
    controls_for,
    load_all_observations,
    paired_oof_log_loss_delta,
    stable_folds,
)
from app.services.stats_math import (
    fit_logistic,
    predict_proba,
    standardize,
    weighted_log_loss,
)

import postplant_v4_variants as variants
import run_postplant_v4_report as runner

N_FOLDS, SEED, DRAWS, L2_C = 5, 0, 2000, 1.0
C_FLOOR, T2_FLOOR = 0.000407, 0.00107
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff",
                    "full_buy_count_diff"]
C_FEATURES = ["impact_diff"] + CONTROLS_CONTEXT
T2_FEATURES = ["impact_diff"] + controls_for(PRIMARY_T2)

ARMS = ["P0", "F-1.00", "N", "N+A"]
PAIRS = [("N", "P0"), ("N", "F-1.00"), ("N+A", "N"), ("F-1.00", "P0")]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def rc3_kwargs(ex_ante: bool):
    cfg = active_scoring_config()
    if cfg is None or cfg.config_id != "impact_rc3":
        raise SystemExit(f"STOP: active scoring config is {cfg!r}, not impact_rc3")
    kw = cfg.build_kwargs()
    if ex_ante:
        kw["use_realized_swing"] = False
    return kw


# --- N+A: assists on kills after the round is decided ----------------------

def post_decided_assists(db, match_id, report):
    """{(round_id, match_player_id): assists on kills after the round was
    decided}. Assistants are stored per kill as Riot IDs; they are mapped to
    this match's players by display name (99.94% map on the corpus; the rest
    are counted in `report` and left in the component)."""
    by_name = {
        name.lower(): mp_id for mp_id, name in
        db.query(MatchPlayer.id, Player.display_name)
        .join(Player, Player.id == MatchPlayer.player_id)
        .filter(MatchPlayer.match_id == match_id)
    }
    out = defaultdict(int)
    rows = (db.query(KillEvent, Round).join(Round, Round.id == KillEvent.round_id)
            .filter(Round.match_id == match_id))
    for kill, round_row in rows:
        names = (kill.source_meta or {}).get("assistants") or []
        if not names or not variants.round_decided(round_row, kill.event_time_seconds):
            continue
        for name in names:
            mp_id = by_name.get(name.lower())
            if mp_id is None:
                report["unmapped"] += 1
                continue
            out[(round_row.id, mp_id)] += 1
            report["removed"] += 1
    return out


_ORIGINAL_BUILD = impact_eval.build_impact_rows_for_match
_ASSIST_REPORT = {"removed": 0, "unmapped": 0}


def _build_removing_assists(db, match_id, **kwargs):
    rows = _ORIGINAL_BUILD(db, match_id, **kwargs)
    per_assist = kwargs["weights"].assists
    removed = post_decided_assists(db, match_id, _ASSIST_REPORT)
    out = []
    for row in rows:
        n = removed.get((row.round_id, row.match_player_id), 0)
        if n:
            # assists_component is round(D * assists) with an integer count,
            # so subtracting round(D * n) is exact.
            cut = round(per_assist * n)
            row = dataclasses.replace(
                row, impact=row.impact - cut, kill_impact=row.kill_impact - cut,
                assists_component=row.assists_component - cut)
        out.append(row)
    return out


def set_assist_removal(on: bool):
    fn = _build_removing_assists if on else _ORIGINAL_BUILD
    impact_eval.build_impact_rows_for_match = fn
    return fn


def arm_setup(arm):
    """(time-factor variant, remove post-decided assists?)"""
    if arm == "P0":
        return None, False
    if arm == "N+A":
        return variants.variant_for("N"), True
    return variants.variant_for(arm), False


# --- identity gate ---------------------------------------------------------

def identity_gate(db, match_ids, kw):
    """The wrapper installed with no variant, and the assists hook installed
    with nothing to remove, must reproduce rc3 exactly."""
    sample = match_ids[:: max(1, len(match_ids) // 150)]
    variants.uninstall()
    reference = {m: impact_module.build_impact_rows_for_match(db, m, **kw) for m in sample}
    variants.install()
    variants.activate(None)
    bad = 0
    for m in sample:
        rows = impact_module.build_impact_rows_for_match(db, m, **kw)
        bad += sum(a != b for a, b in zip(rows, reference[m])) + abs(len(rows) - len(reference[m]))
    db.rollback()
    if bad:
        raise SystemExit(f"IDENTITY GATE FAILED: {bad} rows differ over {len(sample)} matches")
    log(f"IDENTITY GATE PASSED: {len(sample)} matches, wrapper transparent under rc3")


# --- the two targets -------------------------------------------------------

def c_dataset(obs):
    rows, y, mids = [], [], []
    for o in obs:
        if o.round_won_by_team_a is None:
            continue
        rows.append([_feature_value(o, f) for f in C_FEATURES])
        y.append(1.0 if o.round_won_by_team_a else 0.0)
        mids.append(o.match_id)
    return np.array(rows, dtype=float), np.array(y), np.array(mids, dtype=int)


def c_oof(X, y, mids, folds):
    w = np.ones(len(y))
    preds = np.zeros(len(y))
    te_mask = np.array([folds.get(int(m), -1) for m in mids])
    for fold in range(N_FOLDS):
        te = te_mask == fold
        s_tr, s_te, _c, _s = standardize(X[~te], X[te])
        beta = fit_logistic(s_tr, y[~te], weights=w[~te], l2=L2_C)
        preds[te] = predict_proba(beta, s_te)
    return {"scores": preds, "y": y, "w": w, "match_ids": mids}


def verdict(point, lo, hi):
    if lo <= 0 <= hi:
        return "INCONCLUSIVE"
    return "HARM" if point > 0 else "IMPROVEMENT"


# --- main: the contrasts ---------------------------------------------------

def run_contrasts(db, out_dir, limit):
    kw = rc3_kwargs(ex_ante=True)
    kw.pop("use_realized_swing")  # load_all_observations takes it separately
    variants.install()

    all_ids = [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]
    db.rollback()
    identity_gate(db, all_ids[-limit:] if limit else all_ids,
                  dict(kw, use_realized_swing=False))

    if limit:
        keep = set(all_ids[-limit:])
        original_ids = impact_eval._match_ids
        impact_eval._match_ids = lambda d: [m for m in original_ids(d) if m in keep]

    oof_c, oof_t2, diffs, results = {}, {}, {}, {}
    base_mids = folds = None
    for arm in ARMS:
        variant, remove_assists = arm_setup(arm)
        set_assist_removal(remove_assists)
        variants.activate(variant)
        log(f"replaying {arm} (rc3, ex-ante) ...")
        try:
            obs = load_all_observations(db, use_realized_swing=False, scoring_kwargs=kw)
        finally:
            variants.activate(None)
            set_assist_removal(False)
            db.rollback()
        X, y, mids = c_dataset(obs)
        if base_mids is None:
            base_mids = mids
            folds = stable_folds([o.match_id for o in obs], n_folds=N_FOLDS, seed=SEED)
        elif not np.array_equal(mids, base_mids):
            raise SystemExit(f"STOP: row misalignment on {arm}")
        diffs[arm] = X[:, 0]
        oof_c[arm] = c_oof(X, y, mids, folds)
        log(f"  {arm}: C log loss {weighted_log_loss(oof_c[arm]['scores'], y, oof_c[arm]['w']):.8f}")
        oof_t2[arm] = runner.outer_cv(lambda f: obs, folds, T2_FEATURES, N_FOLDS)
        o = oof_t2[arm]
        log(f"  {arm}: T2 log loss {weighted_log_loss(o['scores'], o['y'], o['w']):.8f} "
            f"({len(o['y']):,} rows)")
        results[arm] = {
            "C_log_loss": float(weighted_log_loss(oof_c[arm]["scores"], y, oof_c[arm]["w"])),
            "T2_log_loss": float(weighted_log_loss(o["scores"], o["y"], o["w"])),
            "n_C": int(len(y)), "n_T2": int(len(o["y"])),
        }
        if arm == "N+A":
            results["_assists"] = dict(_ASSIST_REPORT)
            log(f"  N+A: {_ASSIST_REPORT['removed']:,} assists removed, "
                f"{_ASSIST_REPORT['unmapped']:,} unmapped (left in)")
        del obs
    variants.uninstall()

    for arm in ARMS[1:]:
        problem = runner.check_pairing(arm, oof_t2[arm], oof_t2["P0"])
        if problem:
            raise SystemExit(f"STOP: T2 pairing, {arm}: {problem}")

    print()
    print("PROTOCOL GATE -- separability (target-free), before any bootstrap")
    gate = {}
    for arm, ref in PAIRS:
        r2 = float(np.corrcoef(diffs[arm], diffs[ref])[0, 1]) ** 2
        resid = 1.0 - r2
        gate[f"{arm} vs {ref}"] = {"residual_variance": resid,
                                   "below_C_floor": bool(resid < C_FLOOR),
                                   "below_T2_floor": bool(resid < T2_FLOOR)}
        print(f"  {arm:7s} vs {ref:7s}  resid var {resid*100:8.4f}%  "
              f"C {'UNTESTABLE' if resid < C_FLOOR else 'testable'}  "
              f"T2 {'UNTESTABLE' if resid < T2_FLOOR else 'testable'}")
    results["_gate"] = {"C_floor": C_FLOOR, "T2_floor": T2_FLOOR, "pairs": gate}

    print()
    print("DECLARATION 12 -- no time factor; decided rounds pay nothing")
    for target, oofs in (("T2", oof_t2), ("C", oof_c)):
        for arm, ref in PAIRS:
            key = f"{arm} vs {ref} [{target}]"
            point, lo, hi = paired_oof_log_loss_delta(oofs[arm], oofs[ref], draws=DRAWS)
            below = gate[f"{arm} vs {ref}"][f"below_{target}_floor"]
            v = "UNTESTABLE" if below else verdict(point, lo, hi)
            results[key] = {"point": point, "lo": lo, "hi": hi, "verdict": v,
                            "interval_verdict": verdict(point, lo, hi)}
            print(f"  {key:26s} {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  {v}", flush=True)
    print("  NOTE: C is partly circular -- declaration 7.")
    (out_dir / "decl12.json").write_text(json.dumps(results, indent=2))
    log(f"written to {out_dir / 'decl12.json'}")


# --- row motion, under the LIVE configuration ------------------------------

def run_row_motion(db, out_dir, limit):
    from postplant_v4_row_motion import rank_order
    kw = rc3_kwargs(ex_ante=False)
    variants.install()
    ids = [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]
    if limit:
        ids = ids[-limit:]
    arms = ["N", "N+A"]
    stats = {a: {"rows_changed": 0, "matches_changed": 0, "matches_reordered": 0,
                 "abs_delta_sum": 0.0} for a in arms}
    total_rows = 0
    for i, m in enumerate(ids, 1):
        variants.activate(None)
        base = impact_module.build_impact_rows_for_match(db, m, **kw)
        if not base:
            continue
        total_rows += len(base)
        base_by_key = {(r.match_player_id, r.round_id): r.impact for r in base}
        order = rank_order(base)
        for arm in arms:
            variant, remove_assists = arm_setup(arm)
            variants.activate(variant)
            build = _build_removing_assists if remove_assists else _ORIGINAL_BUILD
            rows = build(db, m, **kw)
            changed = 0
            for r in rows:
                before = base_by_key.get((r.match_player_id, r.round_id))
                if before != r.impact:
                    changed += 1
                    if before is not None:
                        stats[arm]["abs_delta_sum"] += abs(r.impact - before)
            stats[arm]["rows_changed"] += changed
            if changed:
                stats[arm]["matches_changed"] += 1
                if rank_order(rows) != order:
                    stats[arm]["matches_reordered"] += 1
        variants.activate(None)
        db.rollback()
        if i % 500 == 0:
            log(f"  {i:,}/{len(ids):,}")
    variants.uninstall()
    report = {"matches": len(ids), "rows": total_rows, "config": "impact_rc3 live", "arms": {}}
    print(f"\nROW MOTION (rc3 live) -- {len(ids):,} matches, {total_rows:,} rows. Motion is not improvement.")
    for arm in arms:
        s = stats[arm]
        r = {"rows_changed": s["rows_changed"],
             "rows_changed_pct": 100 * s["rows_changed"] / total_rows,
             "matches_reordered": s["matches_reordered"],
             "matches_reordered_pct": 100 * s["matches_reordered"] / len(ids),
             "mean_abs_delta": s["abs_delta_sum"] / max(1, s["rows_changed"])}
        report["arms"][arm] = r
        print(f"  {arm:4s} rows {s['rows_changed']:>8,} ({r['rows_changed_pct']:.2f}%)  "
              f"reordered {s['matches_reordered']:>5,} ({r['matches_reordered_pct']:.1f}%)  "
              f"mean |delta| {r['mean_abs_delta']:.1f}")
    (out_dir / "decl12_row_motion.json").write_text(json.dumps(report, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--row-motion", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.limit:
        log(f"*** --limit {args.limit}: SMOKE TEST, NOT A RESULT ***")
    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    try:
        if args.row_motion:
            run_row_motion(db, out_dir, args.limit)
        else:
            run_contrasts(db, out_dir, args.limit)
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
