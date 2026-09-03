"""Cache the T2 design matrix once so the econ investigation can iterate cheaply."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath("."))
from app.db import SessionLocal
from app.services.impact_eval import (
    PRIMARY_T2, FEATURE_COMPONENTS, build_target, controls_for, load_all_observations,
)
db = SessionLocal()
obs = load_all_observations(db, use_realized_swing=False)
db.close()
names = FEATURE_COMPONENTS + list(controls_for(PRIMARY_T2))
ds = build_target(obs, PRIMARY_T2, names)
mids = np.array([o.match_id for o in obs], dtype=np.int64)
out = r"C:/Users/User/AppData/Local/Temp/claude/C--Users-User-Documents-GitHub-valo-with-friends-tracker/0b4fe174-30b0-48fa-8d46-9389e837f3e2/scratchpad/t2_design.npz"
np.savez(out, X=ds.X, y=ds.y, w=ds.w, names=np.array(names), match_ids=ds.match_ids)
print("cached", ds.X.shape, "->", out)
print("names:", names)
