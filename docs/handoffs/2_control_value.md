# Handoff 02: Which areas are worth controlling

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first.

## Goal

For each map, side and phase of the round, show which areas of the map matter to control: how much holding an
area at a given time changes your side's chance of winning the round. Area-level, not individual off-angles.

Target finding: "On Ascent attack, holding Mid Top at 0:30 goes with +8% round-win chance after accounting for
who is alive; A Main control barely matters. We usually give up Mid Top."

## What already exists

- **Map control per tick** from the control engine (`app/control/engine.py`): each walkable cell is held,
  contested, enemy-held or unknown for each side, with per-player control; stored per round in
  `replay_round_control` (`app/services/replay_control_store.py`, read through
  `app/services/replay_control_views.py`). Check the stored format first: whether per-tick cell states are
  stored, or only section totals and heatmaps. If only totals, area shares per snapshot must be computed on the
  worker (see Storage).
- **Areas:** callouts (`callouts.json`, with `super` regions) and reviewed paint
  (`app/static/data/control/<asset>.areas.json`, `app/services/replay_areas.py`). The coach already turns control
  into area shares: `app/coach/control_areas.py` and the query tool's `control` command (`app/coach/query.py`)
  return held shares for an area over a time span. Reuse that code path.
- **Fight swing** (`docs/superpowers/specs/2026-10-23-fight-swing-design.md`) already measures "held ground" as a
  2 s mean of stored frames; reuse its reading so the two features agree.
- **Round-win estimator:** `app/services/util_model.py` (`p_attack(model, a, d, planted, t, t_plant)`), fitted on
  matches without a replay, so every replay round is out of sample.

## Definitions (declare before measuring)

- **Snapshots:** pre-plant at 15, 30, 45 and 60 s after barrier drop; post-plant at 10 and 25 s after the plant.
  A snapshot exists only if the round is still live then.
- **Area share:** for each named area and side, the share of the area's walkable cells held by that side (held =
  the engine's held state; contested counts half; propose and fix this in the spec), averaged over 2 s ending at
  the snapshot.
- **Outcome:** two, reported separately. (a) The round result. (b) The change in the estimator's win chance over
  the next 15 s (less noisy, more local). Declare which is primary; propose (a) for the headline, (b) as a check.
- **Expected:** the estimator's win chance at the snapshot from alive counts, spike and time.

## Method

1. Build one row per (round, snapshot, side): area shares, alive counts, spike state, time, layout, outcome,
   expected.
2. **Residual model:** outcome minus expected, regressed on area shares, per map + side + snapshot, with ridge
   shrinkage (or a hierarchical prior where each area's effect is pulled toward 0 and toward the same area's
   effect at neighbouring snapshots). The coefficient per area is its value: the change in round-win chance per
   unit of share held, beyond what alive counts and the spike explain.
3. **Matching check:** for each area, compare rounds with high vs low share at the same snapshot within the
   same man-advantage state. Should agree in sign with the regression for the strongest areas; report where it
   does not.
4. **Cross-fit by match:** fit on K−1 folds, evaluate on the held-out fold; report out-of-fold log loss of
   expected vs expected + areas. If areas do not improve held-out prediction on a map, say "no detectable effect
   yet on this map" rather than showing a noisy map.
5. **Intervals:** cluster bootstrap by match on every area's value.
6. **Pool both teams:** every replay records both sides, so strangers' rounds double the data. They are numbered,
   never named, and they are only used in the pooled model.

## Causation caveat (put it on the page in one line)

Holding an area may be a sign of winning rather than a cause. Conditioning on alive counts removes the biggest
part (teams with more players alive hold more); the rest remains. The page says "goes with", never "causes".

## Output

- **Per-map page** (`/maps/<map>/control` or a section on an existing map page; check what exists): minimap with
  areas coloured by value on a diverging scale, a side selector, a snapshot slider (15/30/45/60 s, post-plant),
  hover showing value, CI and n. Areas without enough data are hatched.
- **"Us vs what matters":** per map and side, two columns: how much of each area our team usually holds at each
  snapshot, and its value. The interesting lines are high-value areas we usually give up, and low-value areas we
  spend players on.
- **Later (optional):** per round in the viewer, "gave up Mid Top at 0:31 (worth about −6%)".

## Storage and pipeline

- Phase 1: `scripts/control_value.py measure` through `with_friends_db.py --read-only`.
- If kept: area shares per snapshot are small; store them per round (a JSON column or a small table) when the
  control run finishes (on the worker if the per-tick states are only available there), then a stored build like
  Critical Util's: one `util_model`-style row keyed by a `corpus_key` of its inputs, rebuilt when inputs change,
  never built inside a request; pages show the last build with an "updating" note when stale.
- A rebuild loop, if any, checks `deploy_drain.paused` and records failures in Site health.

## Tests

- Area-share computation on a synthetic control grid; snapshot selection (round over, post-plant); the residual
  fit recovering a planted effect on simulated data; cross-fitting has no leakage. Add to `tests/suites.toml`.

## Phases and done criteria

1. Feasibility: on the map with most replays, area shares at 30 s and their raw correlation with the round
   result, with counts. Done when the owner has the table.
2. Residual model + cross-fit on all maps. Done when out-of-fold improvement and per-area values with CIs are
   reported.
3. Page. Done when the map view and "us vs what matters" render, off on the demo, tests pass.

## Open questions for the owner

- Snapshot times; whether contested counts half.
- Whether to show maps where no effect is detectable at all, or hide them until there is data.

## Pitfalls

- Few replays per map: expect wide intervals. Do not tune snapshot times to make the map look interesting.
- Area sizes differ; shares normalise that, but tiny areas are noisy. Merge very small areas into their `super`
  region if needed.
- Layout changes: separate builds per asset key.
