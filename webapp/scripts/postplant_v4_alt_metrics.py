r"""Run from webapp/, read-only:

    DATABASE_URL=... .\.venv313\Scripts\python.exe scripts\postplant_v4_alt_metrics.py \
        --out DIR --mode A|B|C

The three readings declared in docs/superpowers/2026-09-07-predeclared-values.md,
entry "2026-09-20 -- DECLARATION 7". Read it first.

Each estimates the SAME quantity -- what a post-plant kill is worth relative to
a pre-plant one -- by machinery the others do not share, so agreement between
them is evidence the grid search alone cannot supply.

  A  fit pre-plant and post-plant leverage as separate columns and read
     b_post / b_pre directly, with an interval, instead of searching a grid.
  B  measure the win-probability swing of every scored kill against a
     whole-round V(a, d, planted) and take the ratio of the means. No target,
     no folds, no log loss -- it cannot be defeated by collinearity, and it
     equally cannot say whether re-weighting helps a model.
  C  re-evaluate arms on "did team A win THIS round" instead of the three-round
     forward window. Declared circular in a specific way; see the entry.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import SessionLocal
from app.models.match import Team
from app.scoring.impact import (
    _alive_before_each_kill,
    _present_players,
    _round_stats_for_presence,
    _scoreable_kills,
    build_impact_rows_for_match,
)
from app.scoring.plant_window import attacking_team
from app.services.impact_eval import (
    PRIMARY_T2,
    build_target,
    controls_for,
    load_all_observations,
    split_observations,
    stable_folds,
)
from app.services.stats_math import (
    fit_logistic,
    predict_proba,
    standardize,
    weighted_log_loss,
)

import postplant_v4_variants as variants

N_FOLDS, SEED, DRAWS, L2 = 5, 0, 2000, 1.0
CONTROLS_CONTEXT = ["score_diff_before", "attacking_is_team_a", "loadout_diff",
                    "full_buy_count_diff"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _match_ids(db):
    return [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]


# --- A: pre/post leverage split, via the scorer's own per-kill observer -----

def split_leverage(db):
    """Per (match, round): the team-A-minus-team-B leverage differential, split
    into kills before the plant and kills at or after it.

    Read out of `kill_observer`, which reports the scorer's OWN mutated kill
    dict after it has been fully scored, so these are the shipped numbers and
    not a re-derivation. `seconds_to_plant` is positive BEFORE the plant.
    """
    out = defaultdict(lambda: [0.0, 0.0])  # (match, round_number) -> [pre, post]
    for match_id in _match_ids(db):
        def observe(round_number, kill_index, kill, context, _m=match_id):
            stp = context["seconds_to_plant"]
            post = context["planted"] and stp is not None and stp <= 0
            sign = 1.0 if context["killer_team"] == Team.TEAM_1 else -1.0
            if context["self_kill"]:
                contribution = -sign * kill.get("death_order_bonus_x_time", 0.0)
            else:
                contribution = sign * (kill.get("kill_order_bonus_x_time", 0.0)
                                       + kill.get("death_order_bonus_x_time", 0.0))
            out[(_m, round_number)][1 if post else 0] += contribution
        build_impact_rows_for_match(db, match_id, kill_observer=observe)
        db.rollback()
    return out


def mode_a(db, out_dir):
    log("replaying with the per-kill observer ...")
    split = split_leverage(db)
    log(f"  {len(split):,} rounds with a leverage split")
    log("replaying observations for target and controls ...")
    obs = load_all_observations(db)
    db.rollback()

    keep, pre, post = [], [], []
    for o in obs:
        key = (o.match_id, o.round_number)
        if key in split:
            keep.append(o)
            pre.append(split[key][0])
            post.append(split[key][1])
    pre, post = np.array(pre), np.array(post)
    log(f"  matched {len(keep):,} observations")
    corr = float(np.corrcoef(pre, post)[0, 1])

    # The two new columns are routed THROUGH build_target rather than stapled
    # to its output: build_target drops rounds with no valid forward window
    # (53,730 of 67,251 survive), so any array built alongside it silently
    # misaligns. Patching the feature accessor makes it filter them identically.
    import app.services.impact_eval as ie
    lookup = {(o.match_id, o.round_number): (p, q)
              for o, p, q in zip(keep, pre, post)}
    original = ie._feature_value

    def patched(obs, name):
        if name in ("pre_leverage_diff", "post_leverage_diff"):
            pair = lookup.get((obs.match_id, obs.round_number))
            if pair is None:
                return 0.0
            return float(pair[0] if name == "pre_leverage_diff" else pair[1])
        return original(obs, name)

    ie._feature_value = patched
    try:
        folds = stable_folds([o.match_id for o in keep], n_folds=N_FOLDS, seed=SEED)
        features = ["pre_leverage_diff", "post_leverage_diff"] + controls_for(PRIMARY_T2)
        ratios, betas = [], []
        for fold in range(N_FOLDS):
            tr = [o for o in keep if folds.get(o.match_id, -1) != fold]
            ds = build_target(tr, PRIMARY_T2, features)
            X = ds.X
            s_tr, _s_te, _c, _sc = standardize(X, X)
            beta = fit_logistic(s_tr, ds.y, weights=ds.w, l2=L2)
            b_pre, b_post = float(beta[1]), float(beta[2])
            # standardized coefficients: rescale to raw units so the RATIO is a
            # ratio of per-point weights, not of per-sd weights.
            sd_pre, sd_post = X[:, 0].std(), X[:, 1].std()
            raw_pre = b_pre / sd_pre if sd_pre else float("nan")
            raw_post = b_post / sd_post if sd_post else float("nan")
            ratios.append(raw_post / raw_pre if raw_pre else float("nan"))
            betas.append({"fold": fold, "b_pre_std": b_pre, "b_post_std": b_post,
                          "b_pre_raw": raw_pre, "b_post_raw": raw_post,
                          "ratio": ratios[-1]})
            log(f"  fold {fold}: b_pre={raw_pre:+.3e}  b_post={raw_post:+.3e}  "
                f"ratio={ratios[-1]:+.4f}")
    finally:
        ie._feature_value = original

    arr = np.array(ratios)
    res = {"method": "A", "corr_pre_post": corr, "per_fold": betas,
           "ratio_mean": float(arr.mean()), "ratio_sd": float(arr.std()),
           "ratio_min": float(arr.min()), "ratio_max": float(arr.max())}
    print()
    print("METHOD A -- the scalar as a fitted coefficient ratio")
    print(f"  b_post / b_pre per fold: " + ", ".join(f"{r:+.4f}" for r in ratios))
    print(f"  mean {arr.mean():+.4f}   sd {arr.std():.4f}   "
          f"range {arr.min():+.4f} to {arr.max():+.4f}")
    print(f"  corr(pre, post) = {corr:+.4f}  "
          f"({'ill-conditioned' if abs(corr) > 0.8 else 'well-conditioned'})")
    print(f"  shipped asserts 1.00-1.85; grid search estimated ~0.3")
    (out_dir / "alt_metric_A.json").write_text(json.dumps(res, indent=2))


# --- B: whole-round V, and the ratio of mean swings -------------------------

def whole_round_states(db):
    """(a, d, planted) occupancy second by second across the WHOLE round, with
    the round's outcome. Part 4's extractor covers post-plant only; the ratio
    this method computes needs both halves on one footing."""
    rounds = {r["id"]: dict(r) for r in db.execute(text(
        "SELECT id, match_id, round_number, outcome, planted, plant_time, "
        "defused, defuse_time FROM rounds")).mappings()}
    team_of = {mp["id"]: Team[mp["team"]] for mp in db.execute(
        text("SELECT id, team FROM match_players")).mappings()}
    stats = _round_stats_for_presence(db)
    by_round = defaultdict(list)
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, "
        "event_time_seconds, weapon FROM kill_events "
        "ORDER BY round_id, event_time_seconds, id")).mappings():
        by_round[k["round_id"]].append(dict(k))

    occ = defaultdict(lambda: [0, 0])      # (a,d,planted) -> [wins, n]
    swings = {"pre": [], "post": []}
    events = []
    for rid, row in rounds.items():
        outcome = row["outcome"] or ""
        if "Surrender" in outcome or "Time Win" in outcome:
            continue
        atk = attacking_team(row["round_number"])
        if atk is None:
            continue
        if outcome.startswith("Team A"):
            winner = Team.TEAM_1
        elif outcome.startswith("Team B"):
            winner = Team.TEAM_2
        else:
            continue
        won = winner == atk
        kills = _scoreable_kills(by_round.get(rid, []), team_of)
        present = _present_players(stats.get(rid, {}), kills, team_of)
        dfn = Team.TEAM_2 if atk == Team.TEAM_1 else Team.TEAM_1
        a, d = present[atk], present[dfn]
        plant = row["plant_time"] if row["planted"] else None
        end = max([k["event_time_seconds"] for k in kills], default=0.0)
        if plant is not None:
            end = max(end, plant + 45.0)
        # occupancy
        seq = list(zip(kills, _alive_before_each_kill(kills, team_of, present)))
        ca, cd, i = a, d, 0
        for t in range(0, int(end) + 1):
            while i < len(seq) and seq[i][0]["event_time_seconds"] <= t:
                before = seq[i][1]
                victim = team_of[seq[i][0]["death_match_player_id"]]
                if victim == atk:
                    ca = before[atk] - 1
                    cd = before[dfn]
                else:
                    ca = before[atk]
                    cd = before[dfn] - 1
                i += 1
            planted_now = plant is not None and t >= plant
            occ[(ca, cd, planted_now)][0] += 1 if won else 0
            occ[(ca, cd, planted_now)][1] += 1
        # the kills themselves, for the swing population
        for kill, before in seq:
            victim = team_of[kill["death_match_player_id"]]
            ba, bd = before[atk], before[dfn]
            planted_now = plant is not None and kill["event_time_seconds"] >= plant
            aa, ad = (ba - 1, bd) if victim == atk else (ba, bd - 1)
            events.append((ba, bd, aa, ad, planted_now))
    return occ, events


def mode_b(db, out_dir):
    log("building whole-round V(a, d, planted) ...")
    occ, events = whole_round_states(db)
    MIN = 60
    V = {k: w / n for k, (w, n) in occ.items() if n >= MIN}
    log(f"  {len(V):,} supported states, {len(events):,} kill events")

    swings = {"pre": [], "post": []}
    unsupported = 0
    for ba, bd, aa, ad, planted in events:
        vb, va = V.get((ba, bd, planted)), V.get((aa, ad, planted))
        if vb is None or va is None:
            unsupported += 1
            continue
        swings["post" if planted else "pre"].append(abs(va - vb))
    pre = np.array(swings["pre"])
    post = np.array(swings["post"])
    ratio = post.mean() / pre.mean()
    res = {"method": "B", "pre_mean_pp": float(pre.mean() * 100),
           "post_mean_pp": float(post.mean() * 100), "ratio": float(ratio),
           "n_pre": len(pre), "n_post": len(post), "unsupported": unsupported,
           "pre_median_pp": float(np.median(pre) * 100),
           "post_median_pp": float(np.median(post) * 100),
           "ratio_median": float(np.median(post) / np.median(pre))}
    print()
    print("METHOD B -- the scalar as a direct measurement of mean swing")
    print(f"  pre-plant  kills n={len(pre):>7,}  mean |dV| {pre.mean()*100:6.3f}pp  "
          f"median {np.median(pre)*100:6.3f}pp")
    print(f"  post-plant kills n={len(post):>7,}  mean |dV| {post.mean()*100:6.3f}pp  "
          f"median {np.median(post)*100:6.3f}pp")
    print(f"  RATIO post/pre = {ratio:.4f}  (median-based {res['ratio_median']:.4f})")
    print(f"  unsupported states skipped: {unsupported:,}")
    print(f"  shipped asserts 1.00-1.85; grid search estimated ~0.3")
    (out_dir / "alt_metric_B.json").write_text(json.dumps(res, indent=2))


# --- C: the round's own outcome as the target -------------------------------

def round_dataset(obs, features):
    rows, y, w, mids = [], [], [], []
    for o in obs:
        if o.round_won_by_team_a is None:
            continue
        from app.services.impact_eval import _feature_value
        rows.append([_feature_value(o, f) for f in features])
        y.append(1.0 if o.round_won_by_team_a else 0.0)
        w.append(1.0)
        mids.append(o.match_id)
    return (np.array(rows, dtype=float), np.array(y), np.array(w),
            np.array(mids, dtype=int))


def mode_c(db, out_dir, arms_csv=None, tag=""):
    """DECLARATION 8: the seven-arm level curve on the round's own outcome.

    Declaration 7 ran P0, F-0.40 and F-0.30 only. F-0.30 and F-0.40 are re-run
    here unchanged as a REPRODUCTION CHECK -- the bootstrap seeds to 0 and the
    corpus is fixed, so they must return their recorded numbers exactly or the
    new arms are not comparable to declaration 7.

    F-1.26 is level-matched to the shipped mean post-plant T (1.264), so the
    P0 contrast isolates the ramp's SHAPE from its LEVEL. F-1.60 sits above the
    shipped level so an interior minimum can be bracketed rather than inferred.
    """
    features = ["impact_diff"] + CONTROLS_CONTEXT
    variants.install()
    results = {}
    arm_names = ([a.strip() for a in arms_csv.split(",")] if arms_csv else
                 ["P0", "F-0.3", "F-0.4", "F-0.7", "F-1.0", "F-1.26", "F-1.6"])
    if arm_names[0] != "P0":
        raise SystemExit("P0 must be the first arm; it is the comparator")
    arms = {n: (None if n == "P0" else variants.variant_for(n)) for n in arm_names}
    oofs, diffs = {}, {}
    for name, v in arms.items():
        log(f"replaying {name} ...")
        variants.activate(v)
        obs = load_all_observations(db)
        variants.activate(None)
        db.rollback()
        X, y, w, mids = round_dataset(obs, features)
        diffs[name] = X[:, 0].copy()
        folds = stable_folds(mids.tolist(), n_folds=N_FOLDS, seed=SEED)
        preds = np.zeros(len(y))
        for fold in range(N_FOLDS):
            te = np.array([folds.get(int(m), -1) == fold for m in mids])
            s_tr, s_te, _c, _s = standardize(X[~te], X[te])
            beta = fit_logistic(s_tr, y[~te], weights=w[~te], l2=L2)
            preds[te] = predict_proba(beta, s_te)
        loss = weighted_log_loss(preds, y, w)
        oofs[name] = {"scores": preds, "y": y, "w": w, "match_ids": mids}
        results[name] = {"log_loss": float(loss), "n": int(len(y))}
        log(f"  {name}: round-target log loss {loss:.6f}  n={len(y):,}")
    variants.uninstall()

    # Every arm must land on the SAME rows, or neither the gate's R2 nor the
    # paired bootstrap is comparing what it claims to compare.
    for name in arm_names:
        if not np.array_equal(oofs[name]["match_ids"], oofs["P0"]["match_ids"]):
            raise SystemExit(f"row misalignment: {name} does not match P0")

    # --- the protocol gate, BEFORE any bootstrap (declaration 6's deviation) --
    FLOOR = 0.00107  # 0.107% residual variance, set by F-1.0 on target T2
    print()
    print("PROTOCOL GATE -- separability against P0, computed before any bootstrap")
    print(f"  standing floor {FLOOR*100:.3f}% residual variance (set by F-1.0 on T2)")
    gate = {}
    base = diffs["P0"]
    for name in arm_names:
        if name == "P0":
            continue
        # R2 of a simple linear fit is corr^2 and so is direction-free.
        r2 = float(np.corrcoef(diffs[name], base)[0, 1]) ** 2
        resid = 1.0 - r2
        below = bool(resid < FLOOR)
        gate[name] = {"r2": r2, "residual_variance": resid,
                      "resid_sd_over_signal_sd": float(np.sqrt(resid)),
                      "below_standing_floor": below}
        print(f"  {name:6s} vs P0   R2 {r2:.6f}   residual variance {resid*100:7.4f}%   "
              f"resid/signal {np.sqrt(resid)*100:5.2f}%   "
              f"{'BELOW STANDING FLOOR' if below else 'testable'}")
    results["_gate"] = {"standing_floor_residual_variance": FLOOR, "arms": gate}

    from app.services.impact_eval import paired_oof_log_loss_delta
    print()
    print("METHOD C -- the round's own outcome as the target")
    for name in arm_names:
        if name == "P0":
            continue
        point, lo, hi = paired_oof_log_loss_delta(oofs[name], oofs["P0"], draws=DRAWS)
        verdict = ("INCONCLUSIVE" if lo <= 0 <= hi else
                   ("HARM" if point > 0 else "IMPROVEMENT"))
        results[f"{name} vs P0"] = {
            "point": point, "lo": lo, "hi": hi, "verdict": verdict,
            "residual_variance": gate[name]["residual_variance"],
            "below_standing_floor": gate[name]["below_standing_floor"],
        }
        note = "  [below standing floor]" if gate[name]["below_standing_floor"] else ""
        print(f"  {name:6s} vs P0   {point:+.6e}  [{lo:+.6e}, {hi:+.6e}]  "
              f"{verdict}{note}")
    print("  NOTE: partly circular by construction -- see declaration 7.")
    print("  NOTE: a decisive verdict below the standing floor MOVES the floor")
    print("        on this target (gate clause 3); it is not suppressed.")
    (out_dir / f"alt_metric_C{tag}.json").write_text(json.dumps(results, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", required=True, choices=["A", "B", "C"])
    ap.add_argument("--arms", default=None,
                    help="mode C only: comma-separated arms, P0 first. "
                         "Default is declaration 8's seven.")
    ap.add_argument("--tag", default="",
                    help="mode C only: suffix for the output filename")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    if args.mode == "C":
        mode_c(db, out_dir, arms_csv=args.arms, tag=args.tag)
    else:
        {"A": mode_a, "B": mode_b}[args.mode](db, out_dir)
    db.rollback()
    db.close()


if __name__ == "__main__":
    main()
