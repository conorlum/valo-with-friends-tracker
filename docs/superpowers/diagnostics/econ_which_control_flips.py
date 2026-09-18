"""Which control flips econ_impact's sign?  Add them one at a time."""
import numpy as np, sys, os
sys.path.insert(0, os.path.abspath("."))
from app.services.stats_math import back_transform, fit_logistic, standardize

# The T2 design cache lives beside the observation cache -- outside the repo,
# same convention as impact_eval_cache.cache_path(). It previously pointed at a
# session-scoped scratchpad directory that no longer exists, so every script
# reading it was unreproducible from the committed command.
def _t2_design_path():
    from app.services.impact_eval_cache import cache_path
    return str(cache_path().parent / "t2_design.npz")



d = np.load(_t2_design_path(), allow_pickle=True)
X, y, w, names = d["X"], d["y"], d["w"], list(d["names"])
col = {n: i for i, n in enumerate(names)}
COMP = ["damage", "econ_impact", "time_impact", "swing_impact"]
CTRL = ["round_result", "score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]

def fit(cols, l2=1.0):
    design = X[:, [col[c] for c in cols]]
    s, _, c_, sc_ = standardize(design, design)
    raw = back_transform(fit_logistic(s, y, weights=w, l2=l2), c_, sc_)
    return {c: raw[1 + i] for i, c in enumerate(cols)}

print(f"{'model':46s} {'econ':>13s} {'time':>13s} {'damage':>13s}")
print("-" * 88)
b = fit(COMP); print(f"{'components only':46s} {b['econ_impact']:+13.8f} {b['time_impact']:+13.8f} {b['damage']:+13.8f}")
for c in CTRL:
    b = fit(COMP + [c])
    print(f"{'+ ' + c:46s} {b['econ_impact']:+13.8f} {b['time_impact']:+13.8f} {b['damage']:+13.8f}")
print("-" * 88)
b = fit(COMP + ["loadout_diff", "full_buy_count_diff"])
print(f"{'+ both economy controls':46s} {b['econ_impact']:+13.8f} {b['time_impact']:+13.8f} {b['damage']:+13.8f}")
b = fit(COMP + CTRL)
print(f"{'+ all five controls':46s} {b['econ_impact']:+13.8f} {b['time_impact']:+13.8f} {b['damage']:+13.8f}")

print("\ncorrelation of each component with the two economy controls (weighted)")
def wc(a, b_):
    a = np.asarray(a, float); b_ = np.asarray(b_, float); ww = np.asarray(w, float)
    ma, mb = np.average(a, weights=ww), np.average(b_, weights=ww)
    ca, cb = a - ma, b_ - mb
    return float(np.average(ca*cb, weights=ww) / np.sqrt(np.average(ca**2, weights=ww)*np.average(cb**2, weights=ww)))
print(f"{'':14s} {'loadout_diff':>14s} {'full_buy_diff':>14s}")
for c in COMP:
    print(f"{c:14s} {wc(X[:, col[c]], X[:, col['loadout_diff']]):+14.4f} {wc(X[:, col[c]], X[:, col['full_buy_count_diff']]):+14.4f}")
