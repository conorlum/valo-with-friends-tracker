"""Does econ_impact's negative partial coefficient depend on the CONTROL SET?

Stage A reports it negative in 5/5 folds. That report fits FEATURE_COMPONENTS
only -- no controls. The constrained weight search and the control ladder both
run WITH controls. So: which model is the -0.00025 from, and does it survive?
"""
import numpy as np, sys, os
sys.path.insert(0, os.path.abspath("."))
from app.services.stats_math import back_transform, fit_logistic, standardize

d = np.load(r"C:/Users/User/AppData/Local/Temp/claude/C--Users-User-Documents-GitHub-valo-with-friends-tracker/0b4fe174-30b0-48fa-8d46-9389e837f3e2/scratchpad/t2_design.npz", allow_pickle=True)
X, y, w, names, mids = d["X"], d["y"], d["w"], list(d["names"]), d["match_ids"]
col = {n: i for i, n in enumerate(names)}
COMP = ["damage", "econ_impact", "time_impact", "swing_impact"]
CTRL = ["round_result", "score_diff_before", "attacking_is_team_a",
        "loadout_diff", "full_buy_count_diff"]

def fit(cols, l2=1.0, rows=None):
    r = slice(None) if rows is None else rows
    design = X[r][:, [col[c] for c in cols]]
    scaled, _, c_, s_ = standardize(design, design)
    beta = fit_logistic(scaled, y[r], weights=w[r], l2=l2)
    raw = back_transform(beta, c_, s_)
    return {c: raw[1 + i] for i, c in enumerate(cols)}

print("Coefficient on econ_impact (and damage), by model, all data, l2=1.0")
print("-" * 68)
for label, cols in [
    ("4 components ONLY  (what Stage A reports)", COMP),
    ("4 components + all 5 controls", COMP + CTRL),
    ("4 components + round_result only", COMP + ["round_result"]),
    ("4 components + context controls (no round_result)", COMP + CTRL[1:]),
]:
    b = fit(cols)
    print(f"{label:52s} econ={b['econ_impact']:+.8f}  damage={b['damage']:+.8f}")

print("\nSame, across L2, for the two headline models")
print("-" * 68)
for l2 in (0.01, 0.1, 1.0, 10.0):
    a = fit(COMP, l2=l2)["econ_impact"]
    b = fit(COMP + CTRL, l2=l2)["econ_impact"]
    print(f"  l2={l2:<6} components-only econ={a:+.8f}   with-controls econ={b:+.8f}")

print("\nPer-fold, both models (stable_folds, 5 folds, train halves)")
print("-" * 68)
sys.path.insert(0, os.path.abspath("."))
from app.services.impact_eval import stable_folds
folds = stable_folds(sorted(set(int(m) for m in mids)), n_folds=5, seed=0)
fold_of = np.array([folds[int(m)] for m in mids])
neg_no, neg_yes = 0, 0
for f in range(5):
    tr = np.flatnonzero(fold_of != f)
    a = fit(COMP, rows=tr)["econ_impact"]
    b = fit(COMP + CTRL, rows=tr)["econ_impact"]
    neg_no += a < 0
    neg_yes += b < 0
    print(f"  fold {f}: components-only {a:+.8f}    with-controls {b:+.8f}")
print(f"\n  negative in {neg_no}/5 folds without controls, {neg_yes}/5 folds with controls")
