"""Cache the T2 design matrix once so the econ investigation can iterate cheaply."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath("."))
from app.db import SessionLocal
from app.services.impact_eval import (

    PRIMARY_T2, FEATURE_COMPONENTS, build_target, controls_for, load_all_observations,
)

# The T2 design cache lives beside the observation cache -- outside the repo,
# same convention as impact_eval_cache.cache_path(). It previously pointed at a
# session-scoped scratchpad directory that no longer exists, so every script
# reading it was unreproducible from the committed command.
def _t2_design_path():
    from app.services.impact_eval_cache import cache_path
    return str(cache_path().parent / "t2_design.npz")

db = SessionLocal()
obs = load_all_observations(db, use_realized_swing=False)
db.close()
names = FEATURE_COMPONENTS + list(controls_for(PRIMARY_T2))
ds = build_target(obs, PRIMARY_T2, names)
mids = np.array([o.match_id for o in obs], dtype=np.int64)
out = _t2_design_path()
np.savez(out, X=ds.X, y=ds.y, w=ds.w, names=np.array(names), match_ids=ds.match_ids)
print("cached", ds.X.shape, "->", out)
print("names:", names)
