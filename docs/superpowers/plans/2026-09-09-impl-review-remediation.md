# Remediation plan: the impact-scoring-impl review (2026-09-09)

Source: external review of `impact-scoring-impl` at `3019530`. **All 13 findings
were independently validated** against source and against the specs' own text --
none was rejected, and none was accepted on assertion alone.

Two are already fixed:

| # | what | commit |
|---|---|---|
| 1, 2 | five-arm report: whole-corpus tables, and a refit estimand instead of a fixed composite | `3311b6c` |
| 12 | nested death-cost bootstrap resampled the wrong match population | `ee7231a` |

**Eleven remain.** This plan sequences them. The ordering is not cosmetic:
Phase 1 changes scored values, so every number Phase 3 records depends on
Phase 1 landing first.

Everything here is dormant behind default-`False` flags EXCEPT finding 11,
which affects the live site today.

---

## Phase 0 -- two decisions before any code

### D1. Finding 11 conflicts with a decision already on record

The overtime replay change (`b2d1d55`) is **active regardless of the scoring
flags**, but `fight_ev.CALCULATION_VERSION` is still 3 and
`STATE_DIAGRAM_CALCULATION_VERSION` is still 2. Cached player views therefore
exclude overtime while any live recomputation includes it, on the deployed
site, until something unrelated invalidates them.

`predeclared-values.md` records: *"`STATE_DIAGRAM_CALCULATION_VERSION` stays at
2 -- admitting OT rounds adds data, as ingesting more matches does; replay
semantics are unchanged."*

The premise does not hold. Ingesting a match invalidates **that match's
players**; the OT change silently affects **every player who has ever played
overtime**, including those with no new matches, and nothing invalidates them.

Options: (a) bump both versions now and recompute; (b) bump only
`fight_ev.CALCULATION_VERSION` (already scheduled 3 -> 4 at rollout) and pull
it forward; (c) accept the staleness until the Impact rollout, documented as a
known live inconsistency. **This is the project owner's call, and it amends a
recorded decision -- it must go in `predeclared-values.md`'s Amendments
section with its reason, never as a silent edit.**

### D2. Is the post-plant centring constant `c` solved per fold?

Finding 3 requires the scored table to carry and apply `c`. In the five-arm
report the tables are now built per outer fold on training matches only, so
**`c` must be solved per fold on that fold's training data too** -- solving it
once on the full corpus would reintroduce exactly the leakage finding 1 just
removed. Confirm this before implementing; it shapes the `PostPlantFactorTable`
interface.

Default assumption if unanswered: **solve per fold.**

---

## Phase 1 -- the scoring path (the ship-blockers)

These four change scored values. Do them together, then rerun.

### 1a. Finding 5 -- phantom plants reach the enabled timing path

`impact.py:185` computes `plant_time = round_row.plant_time if round_row.planted
else None`. `effective_plant_time` (shared, in `plant_window.py`) additionally
excludes planted rounds that ended in a Time Win. Fitting and extraction use the
helper; runtime does not. An enabled pre-plant replay therefore applies a curve
trained on real plants to an event with no effective plant.

**TRAP -- do not "fix" this globally.** The legacy branches (the `plant+38..45`
override, the `1 + (t - plant)/53` ramp, the exploded/defused early return) read
the same `plant_time` and are the SHIPPED behaviour on arm 0. Changing them
changes today's scoring and silently moves the reference arm of the whole
five-arm report. Route **only** the flag-gated paths through
`effective_plant_time`; leave the legacy computation exactly as it is, and say
so in a comment.

Also check the post-plant classification under the same invalid metadata, per
the review.

### 1b. Finding 6 -- post-plant self-deaths take the opposite side's curve

`impact.py:219` passes `victim_is_attacker=not is_attacker`.

**TRAP -- the surrounding convention is deliberate and must survive.** The
death-side call site (`impact.py:747`) carries an explicit comment that it
passes *the killer's* side on purpose: "a death is the transfer of what the
victim's team lost, referencing the same event/side the kill-side scalar used".
That is correct for enemy kills and is not the bug.

The bug is narrow: on a **self-kill** the killer and victim are the same player,
so `not is_attacker` names the wrong side. Note the kill-side call is already
guarded by `if not self_kill else 0`; the death-side call is not.

Two acceptable fixes -- pass the true victim side, or keep self-kills on the
neutral/legacy factor explicitly, on the grounds that the factor was estimated
on the non-self population. **Prefer the second unless there is a reason not
to**, and state the choice. Add a self-death case to the hook tests either way.

### 1c. Finding 3 -- the centring constant is never applied

`PostPlantFactorTable.factor` returns the clamped raw ratio and `_time_factor`
returns it unchanged. `postplant_centering.solve_postplant_centering` exists and
`fit_postplant_factor.py` prints `c`, but nothing plumbs it into the table used
by scoring or by the five-arm report. The string `c` does not appear in
`postplant_factor.py`.

Requirements, from the review and the spec:
- the scored table carries `c` and applies it **on supported cells only**;
- **fallback cells stay exactly 1.0**;
- apply `c` **after** the declared raw clamp, and do **not** clamp a second time;
- per D2, solve `c` per fold in the five-arm path.

The predeclared `|c - 1| <= 0.05` is *"a reported finding, not an assertion"* --
report it, do not gate on it, and do not tune anything to hit it.

### 1d. Finding 4 -- the death residual uses the kill-side baseline

`fit_postplant_factor.py:226` builds `ramp` via `_time_factor(shim, ...)` with
`for_death` left at its default `False`. `postplant_centering.py:82` then reuses
those kill-side factors as the **death** baseline. In `plant+38..45` the legacy
scorer pays 1.75 on the kill side and 0.5 on the death side, so the baseline is
wrong exactly where the two diverge.

Carry the real legacy **death** factors separately and report the residual
against those. **Do not force that residual to zero** -- the point is to measure
the change a player would actually receive, and the 2% tolerance is a reported
finding.

### 1e. Reruns that Phase 1 forces

In this order:
1. `scripts/fit_postplant_factor.py` -- new `c`, new death-side residual, and
   the out-of-fold calibration MACE.
2. `scripts/run_five_arm_report.py` -- arms 2 and 3 replay through the table,
   so 1a/1b/1c all move them. ~15 min; 10 per-fold replays at ~38s each.

Record the before/after for every number either run publishes.

---

## Phase 2 -- evaluation and reporting

Independent of Phase 1; can proceed in parallel.

### 2a. Finding 7 -- every Family B fold is silently skipped

`kill_order_refit.py:1595` skips folds where `fitted.graph is None`.
`fit_family_b` (`kill_order_curves.py:414`) returns a `ScoredCandidate` with
**no `graph=` field** -- deliberately, since Family B fits weights over a fixed
graph. The Family A branch immediately above sets `graph=np.asarray(graph)`. So
the matrix that the previous review's "incomplete family matrix" finding asked
for is still empty for Family B; adding the names to the results dict did not
fix it.

Reconstruct Family B scores through `family_b_columns` and the fold's recovered
weights, on both training and test observations.

### 2b. Finding 8 -- WPA inner-fold selection is still contaminated

`_wpa_context(train_obs)` fixed the outer leak. `_select_l2`
(`kill_order_refit.py:433`) then slices `aligned.y[train_mask][inner_train]` and
`aligned.weights[...]` -- targets produced by a value model fitted on the whole
outer training set, including the inner-validation matches.

**Note the stale comment.** `_select_l2`'s docstring claims "EVERYTHING
data-derived is rebuilt per inner split" and explains at length why reusing the
swing table was a leak. That rebuild is real; the WPA value model simply was
not included. Rebuild the WPA context and weights inside each inner training
split, and fix the docstring so it stops overstating.

Give `_select_l2_b` the same treatment if WPA is supported there.

### 2c. Finding 9 -- practical equivalence mixes two candidates

`_practically_equivalent_for_candidate` computes a candidate-specific loss bound
and then calls `_practically_equivalent_stage_c0`, which reads
`stage_c0["current_vs_swing_plugin"]` -- the preliminary plugin, not the fitted
candidate that cleared the primary.

The helper's own docstring is candid about this. The defect is the **caller**
combining a candidate-specific bound with a candidate-agnostic one. Compute the
score deviation from each candidate's actual held-out scores against the
matching reference, and apply both bounds to the same candidate.

### 2d. Finding 10 -- the cache/report identity misses scoring inputs

`impact_eval_cache._source_revision` hashes three aggregate totals plus a
round-outcome digest. **`plant_time` appears nowhere**, and it drives every
timing score; changing a planted round's `plant_time` from 30 to 20 leaves the
revision identical while default replay scores move. Aggregate sums also cannot
see balanced edits or values swapped between players.

Use a deterministic digest of the row contents the replay actually consumes, not
sums. Also: `RunIdentity.source_revision` is declared `= ""` and **never
assigned** -- `build_full_report` must populate it or the field protects
nothing. Include the port in `_database_identity`.

### 2e. Finding 13 (P3) -- the retained diagnostic drops the global intercept

`preplant_time_model.py:346` stores `state_effects` from `beta[1+i]` with the
reference pinned to 0.0 and never keeps `beta[0]`; `PreplantFit` has no field
for it. `fit_preplant_time_factor.py` reconstructs `state_effect + shape * lift`,
so its loss and temporal-reliability diagnostics are not predictions from the
fitted model.

Retain the global intercept or fold it into every stored state effect. This does
**not** touch the empirical runtime curve that superseded this model -- only the
retained diagnostic's honesty.

---

## Phase 3 -- records, after Phases 1 and 2 land

1. `2026-09-04-impact-measurements.md` -- any M-number Phase 1 moved.
2. The two specs -- Part 4's centring and calibration figures.
3. `predeclared-values.md` -- D1's amendment, and `c` if its reported value
   changes. Amendments section, dated, never an in-place edit.
4. `project_impact_scoring_specs` memory -- the five-arm numbers there are
   already superseded by `3311b6c`; update again if Phase 1 moves them.

---

## Verification required

- Full suite green: **730 passed, 1 pre-existing unrelated failure**
  (`test_site_stats_cache.py::test_happy_path_blob_validates` -- three fixture
  fields against nine required; unchanged since `8569733`, not yours).
- **Every behavioural fix gets a test that fails before it and passes after.**
  Verify that by reverting the fix and watching the test fail -- do not assume
  it. Findings 3, 5, 6 and 7 had no covering test, which is why they survived.
- No flag default flipped. `IMPACT_CALCULATION_VERSION` stays 1. No rescore, no
  migration, no deployment.
- Report before/after for every published number a change moves, and say plainly
  when a conclusion moves rather than renumbering quietly.

## Standing traps in this codebase

- Raw SQL returns the enum **name**: `Team["TEAM_1"]`, never `Team("TEAM_1")`.
- Synthetic fixtures must be checked to actually produce the relationship they
  claim before assertions are written against them.
- A spec sentence that predeclares *how* to measure must be read back against
  the implementation. Findings 1 and 2 were both single sentences in section
  8d-i that the code contradicted while looking entirely reasonable.
- Fixing a visible symptom inside a wrong frame raises confidence faster than
  correctness. Commit `b9ad0f8` did exactly that to the five-arm report.
