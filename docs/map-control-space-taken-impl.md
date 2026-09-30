# Map control "space taken": implementation plan (R3.2, P3)

The plan's stretch stat (`docs/replay-map-control-plan.md`, "How active a player is"): "space taken, the cells this
player's vision flipped from the enemy or nobody to their team."

## Definition (the one line, made exact)

At each control tick n after the first, a cell is **taken** when its state at tick n-1 was the enemy's or nobody's
and at tick n is the team's (any level: passive, safe or active). The team's players whose coverage at tick n
(active or passive vision, or their own live utility: the same mask as coverage) includes the cell share it
evenly; a cell nobody on the team sees (it became safe because the lines moved) is taken by nobody. Only ticks
inside the live round count (`t_start` < t <= `t_decided`), and only players alive at the tick. A player's
**space taken** is the sum over the round, in m² (cells x the map's cell area). Per match: the sum over rounds.

It is a count of flips, not an integral: holding ground isn't taking it.

## Why a new revision

It is a new stored number (a player's `taken_m2` in the round summary), so per the run's hard rule it bumps
`CONTROL_REVISION` 1 -> 2 on its own branch: every stored round becomes stale and keeps being served (with
`X-Control-Stale`) until the user reruns `scripts/compute_control.py` (tier 3). No format change: the summary's
per-player dict gains a key, so `SUMMARY_VERSION` and `DATA_VERSION` stay.

## Steps

1. **Engine** (`app/control/engine.py`): in `compute_round`, keep the previous tick's state; after `coverage()`,
   for each side compute `flipped = (prev not ours) & (prev not contested) & (state ours)` (walkable cells), the
   per-cell count of covering teammates, and add `cells / count * cell_m2` to each covering player's
   `taken_m2s` when the tick is live and they are alive. `PlayerStats.taken_m2` in `as_dict`.
   `CONTROL_REVISION = 2` in `app/replays/control_format.py` and the engine; pin revision 2's constants digest.
   - **Check:** new toy-grid tests in `tests/replays/test_control_stats.py`: a holder turning to face an empty
     hall takes it (their m² equals the newly held cells); two holders turning onto the same cells split it;
     cells that were already ours count nothing; ground that flips while nobody watches is credited to nobody;
     nothing counts after `t_decided`. The revision pin test passes with the new entry.
2. **Views** (`app/services/replay_control_views.py`): `taken_m2` per round and summed per match (None when the
   summary predates revision 2).
   - **Check:** `tests/replays/test_control_views.py` covers a summary with and without `taken_m2`.
3. **Table** (`app/static/js/replay.js`): a "Taken" column (m², round and match); "—" when not stored yet.
   - **Check:** node test of `controlRows` with and without the field.
4. **Local proof on real data:** recompute two real rounds locally (no DB write: `compute_task` on blobs fetched
   read-only) and look at the numbers: taken > 0 for entries and rotations, ~0 for a passive anchor.
