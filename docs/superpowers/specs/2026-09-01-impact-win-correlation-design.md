# Impact-vs-winning: evaluation tooling and fitted weights

**Status:** revised after review, awaiting user approval
**Date:** 2026-09-01 (revised 2026-09-01 after methodology review)

## Purpose

Answer, with numbers rather than intuition, how much the custom Impact score
actually relates to winning -- and use that to tune it. The user's framing:
Impact is supposed to *be* impact, so the tooling exists to evaluate it "for
what it is."

Three questions were separated during brainstorming, and they do not all
belong in the same place:

- **"Does my performance predict my wins?"** -- personal, meaningful to a
  player. Goes on the **player page**.
- **"Is Impact better than K/D at predicting wins, and which of its parts are
  earning their keep?"** -- validates the metric itself. **Internal tooling**,
  not a page.
- **"Who on the roster carries?"** -- friend-group flavored. Goes on the
  **squad page**, not the stats page.

## Decomposition, and what THIS spec covers

| | Deliverable | Spec |
|---|---|---|
| P0 | `stats_math` -- pure numeric helpers | this one |
| P1 Stage 0 | Descriptive report on Impact **as it exists today** | this one |
| P1 Stage A | Fitted weights against co-primary targets | this one |
| P1 Stage B | Leverage-weighted WPA attribution | this one |
| P2 | Player page: impact-to-win-rate card + break-even tile | its own, later |
| P3 | Squad page: carry measure | its own, later |

**This spec covers P0 and P1.** P2 and P3 are designed later, from Stage 0's
observed effect sizes.

## Constraints and premises

### Data volume (local DB, 2026-09-01)

1,151 matches - 24,157 rounds - 178,242 kill events - 241,570 impact rows -
8,251 players.

**Effective sample sizes**, stated carefully because an earlier draft got this
wrong:

- **~24k rounds**, not 48k. A round's two (round, team) rows have perfectly
  complementary outcomes and mirrored structure; they are not independent
  observations. The design uses **one row per round** (see "Observation
  unit").
- **1,151 match outcomes.** This is ample to *fit* a four-coefficient model.
  What it does not support is finely *discriminating* small AUC differences
  between candidate weightings -- that is where the confidence intervals bite,
  and it is the reason match-level results are always reported with CIs.
- Minus surrender placeholder rounds (below), and minus rounds dropped at
  half boundaries by the forward-window target.

More matches from the tracker.gg crawl sharpen the match-level numbers
specifically. The tool re-runs unchanged and prints `n` at every level.

### Surrender placeholder rounds must be excluded

`app/services/surrender_rounds.py` documents 202 all-zero placeholder rounds
across 37 matches that tracker.gg pads surrendered matches with. Every
extraction query in this project uses the existing `NOT_A_SURRENDER_ROUND`
predicate.

### The tautology trap

A round's kill events *are*, near-deterministically, that round's outcome. Any
model predicting "did we win round N" from round N's own kills scores ~99% and
has learned nothing. Training targets that involve the round's own outcome are
therefore **attribution schemes, not predictive validation**, and are labelled
as such throughout.

Note also that `round_win_impact` (kill_impact zeroed in lost rounds, see
`app/services/player_profile_types.py`) cannot be validated against round
outcome at all -- it is defined by it. Only `impact`, `kill_impact`,
`death_impact`, and the components are eligible.

### LEAKAGE: stored `swing_impact` contains next-round information

**This invalidates the naive forward-window target and forces a recompute.**

`_realized_econ_swing_factor` (`app/scoring/impact.py:309`) reads
`round_player_stats.get(round_number + 1)` and counts how many of a team's
players are below `FULL_BUY_THRESHOLD` **in the next round**. It is called at
`impact.py:473-476`, merged through `_combine_swing_factors`, multiplied into
`kill_order_bonus_x_swing` at `impact.py:502`, and summed into the stored
`swing_impact` column at `impact.py:639`.

So the stored `swing_impact` for round N encodes round N+1's economy.
Predicting round N+1 from it leaks.

The realized term **cannot be backed out of the stored column**:
`_combine_swing_factors` collapses to `1.0` whenever the two factors disagree
in direction, so the ex-ante value is unrecoverable after the fact.

**Resolution: re-derive ex-ante components from `kill_events`.**
`_econ_swing_risk_factor` was checked and is clean -- it projects from
current-round credits (`remaining + WIN_BONUS >= loadout_threshold + ...`) and
reads no future rows. So the ex-ante signal exists; it just has to be
recomputed rather than read.

**Mechanism: split calculation from persistence.** An earlier draft proposed
only adding a `use_realized_swing` keyword to `compute_impact_for_match`. That
is unsafe and would have corrupted data: the function queries `ImpactScore`
(`impact.py:624`), `db.add`s new rows (`:630`), mutates every column, and calls
`db.commit()` unconditionally (`:657`). Passing `use_realized_swing=False`
would have **written ex-ante values over the stored scores**.

The required change is a structural extraction:

```python
build_impact_rows_for_match(db, match_id, use_realized_swing=True)
    -> list[CalculatedImpact]
```

- `compute_impact_for_match` becomes a thin wrapper: call the builder, persist
  its results, commit. Its signature and behaviour are unchanged.
- `impact_eval` calls the builder directly and never writes.
- The formula itself is untouched; only the calculation/persistence boundary
  moves.

- **A test must assert the default path is value-identical to today's stored
  output** (field-by-field equality over a sample of matches -- there is no
  serialization here, so "byte-identical" was the wrong bar).
- **Cost:** an in-memory replay of all 1,151 matches per run, comparable to
  `scripts/recompute_impact.py`. Acceptable for an offline tool, but it does
  retire the earlier draft's "no recompute needed" claim.

Both variants are extracted and **always labelled distinctly** in the report:
`ex_ante` ("available at the end of round N") and `realized` ("incorporates
round N+1's observed economy"). Only `ex_ante` is eligible for forward-looking
fitting; `realized` appears only in retrospective/attribution contexts.

### Forward windows respect the economy boundaries

Valorant resets economy at halftime, and `impact.py:309`'s own guard already
encodes the convention: `if round_number in (12, 24) or round_number > 24`.
Forward windows use the same rule -- they never cross the halftime reset or
the modeled OT boundary.

### The tuning surface already exists

`app/scoring/impact.py` carries an explicit, self-described tuning surface:

```python
FACTOR_WEIGHTS = {"econ": 1.0, "time": 1.0, "swing": 1.0}
# "these are a starting proposal, not a final tuning -- adjust freely."
```

and computes

```
kill_impact  = damages + (w_e*K_econ + w_t*K_time + w_s*K_swing) / SUM(w)
death_impact =           (w_e*D_econ + w_t*D_time + w_s*D_swing) / SUM(w)
impact       = kill_impact - death_impact
```

Since `impact_scores` stores `econ_impact = K_econ - D_econ` (and likewise
`time_impact`, `swing_impact`, plus `damage`) as real columns since migration
`0006_impact_scores_columnar_breakdown`, this reduces to:

```
impact = damage + (w_e*econ_impact + w_t*time_impact + w_s*swing_impact) / SUM(w)
```

**Task 0 (gate):** verify that identity empirically on live rows before
building anything else. Each term is `round()`ed independently, so agreement is
expected within a couple of points, not exactly. If it does not hold, Stage A
is invalid and must be reconsidered rather than worked around. **Ships as a
test**, so a future change to the combination step fails loudly.

Task 0 validates the **parameterization** -- that this linear form is the right
target for fitting. It is checked against the stored columns because that is
what the live scorer wrote. The Stage A *training features* are a different
thing: ex-ante components re-derived per the leakage section below. Both share
this same linear form, which is why the gate transfers.

### The three components are collinear by construction

`impact.py:496-502` builds all three as `kill_order_bonus * <factor>` -- they
share the same multiplicand. Unstable raw coefficients are therefore expected,
not a risk to be discovered later. Every fit reports:

- coefficients across all outer folds
- bootstrap sign stability per coefficient. **This requires a refitting
  bootstrap** -- the model is re-fit on each resampled set of matches.
  Resampling fixed out-of-fold *predictions* can give metric CIs but says
  nothing about coefficient stability. Cheap here: 4-8 parameters per fit.
- drop-one-component performance
- the component correlation matrix

A coefficient whose sign flips across folds is reported as indeterminate, never
as a finding.

### Mapping a fit back to `FACTOR_WEIGHTS` is a separate constrained problem

Weighted IRLS produces unconstrained coefficients on standardized features. It
does **not** by itself yield "the best `FACTOR_WEIGHTS` under the existing
parameterization." Those are two different optimizations, and the spec treats
them as such:

1. **Unconstrained:** report raw fitted coefficients (back-transformed to raw
   feature scale) and their ratios. A **negative** component coefficient is
   reported honestly as evidence that the component is anti-predictive -- it is
   never clipped or silently absorbed.
2. **Constrained:** a separate small optimization for `(w_e, w_t, w_s, d)`
   subject to the existing form `impact = d*damage + (SUM w_i*f_i)/SUM(w)`.
   Scale-invariant in `w`, so it is 3 effective degrees of freedom -- solved by
   projected search / coarse grid, not by IRLS. **Decision: `w_i >= 0` is
   enforced**, since a negative factor weight is not meaningful in the current
   design. The unconstrained result is always reported alongside, so the cost
   of that constraint is visible rather than hidden.
3. Report explicitly whether the unconstrained optimum is representable in the
   existing parameterization at all.

### Scope boundary on "weight fitting"

Stage A fits the **outer** weights: `econ` / `time` / `swing` and the damage
multiplier. The **inner** curves -- the kill-order graph's per-edge bonuses,
the time-factor curve -- stay fixed. (`_econ_swing_risk_factor` is now
partially in scope only insofar as the ex-ante/realized split above requires
re-running it; its internals are not refit.) Inner-curve refitting is **Stage
C**, out of scope here.

## Stage 0 -- descriptive report on Impact as it exists today

**Runs before any fitting.** Its purpose is to answer the original question
directly, and to supply the effect sizes that P2's design needs. Without it,
this project could ship sophisticated fitted weights while never reporting the
simple correlation that was actually asked for.

Read-only, no fitting, **current stored scores** -- which means the `realized`
swing variant, since that is what the live scorer wrote and therefore what
"Impact as it exists today" actually is. Stage 0 is descriptive of the shipped
metric, not an input to any forward-looking fit, so the leakage constraint does
not apply to it. The report labels it accordingly.

It reports:

- player-match average Impact versus win/loss
- point-biserial correlation
- top-tercile versus bottom-tercile win-rate lift
- within-player-centered pooled relationship (each player's Impact centered on
  their own mean, which controls for skill level)
- first-half team Impact differential versus match outcome
- full-match differential versus kill differential (diagnostic only)
- confidence intervals (bootstrapped by match) and sample counts throughout

### Cohorts are mandatory, because the player distribution is extreme

Measured on this DB: **7,814 of 8,251 players (94.7%) have exactly one match.**
A naive all-player within-person calculation would be overwhelmingly composed
of players whose centered Impact is exactly zero by construction -- a
zero-variance artifact, not a finding.

Stage 0 therefore reports by cohort, never pooled blindly:

| Cohort | Rule | n (2026-09-01) |
|---|---|---|
| Tracked roster | `scripts/tracked_players.json` | roster size |
| Recurrent | >= 2 decided matches | 437 players |
| Per-player correlation | >= 10 matches | 71 players |
| Per-player terciles | >= 9 matches (>= 3 per bucket) | 81 players |

**Terciles are computed within each player and then pooled**, not globally
after centering. This is the form P2's card will actually display -- "when I
play a top-third game *for me*" -- so Stage 0 should measure the thing the page
will show.

**Player means, cohort eligibility, and tercile boundaries are all recomputed
inside every bootstrap resample.** Treating them as fixed would understate the
intervals.

## The evaluation contract

Defined once, before anything is fit, and identical across every stage.

### Observation unit

**One row per round**, not two. Features are **team-A-minus-team-B
differentials** of each component; the label is whether team A achieved the
target outcome. Deterministic team-A orientation.

**Consequence for side, and it is not optional:**
`map_side_stats.attacking_team_for_round` returns `TEAM_1` for *every* round
<= 12. Under team-A orientation, therefore, **every first-half row is
attack-first** -- there is no defense-first subset to compare against, and side
is a constant, not a control, within T1. The first-half yardstick is reported
as a single number; any attack-vs-defense split there would be vacuous.

Side remains a genuine control for **T2**, which spans rounds 1-24+ where the
attacking team does vary.

T1's match-level evaluation is still side-balanced at the level that matters:
both teams play one half on each side before the match ends.

This also matches the first-half and full-match yardsticks, which are
inherently differential, so training and evaluation use the same
representation.

### Co-primary targets

Both are fit and reported side by side. **Disagreement between them is a
finding and is printed as such**, not resolved by picking a favourite.

- **T1 -- first-half component differential -> match result.** Strictly
  forward-looking with respect to the second half; no leakage.
  **n = 1,129, not 1,151:** a match must have all 12 genuine (non-surrender)
  first-half rounds to be eligible. 22 matches fall short after surrender
  placeholders are removed, and a truncated first-half total is not comparable
  to a full one. Exclusion rather than normalization -- it is cleaner and the
  cost is 1.9% of matches.
- **T2 -- ex-ante components at round N -> rounds N+1..N+k**, respecting half
  boundaries. n = ~24k rounds, minus terminal rounds (below).

**Both targets are fit on `ex_ante` components**, so their coefficients are
directly comparable. The resulting weights are then evaluated on both `ex_ante`
and `realized` yardsticks. Without this, a "targets x yardsticks" matrix would
quietly be comparing different feature definitions.

**Adoption caveat, stated up front:** today's shipped scorer computes the
`realized` variant. Weights fitted on `ex_ante` would, if adopted as-is, be
applied to a formula that still includes the realized swing term. Whether the
scorer itself should drop that term is a real question this tooling will inform
-- it belongs to Stage C, not here, but the report must not present fitted
weights as drop-in without saying so.

**Terminal rounds are excluded from T2** -- a match's last round has no
eligible future outcome.

**The match-outcome auxiliary is restricted.** An earlier draft attached the
final match result to every round-N observation. For late rounds the match
outcome is substantially or entirely determined by round N, which reintroduces
exactly the tautology this design otherwise avoids. It is therefore restricted
to **rounds N <= 12**, and **`match_weight=0` is included in the sweep** so the
data decides whether it earns its place. T1 already carries the match-level
objective, so nothing is lost if it drops out.

### T2 requires a control ladder

A round-N feature can predict future rounds merely because it reveals who won
round N, and winning N creates the next-round economy advantage. T2 is
therefore always reported as nested models:

1. round-N result alone
2. + score differential, side, pre-round economy differential
3. + damage differential
4. + full ex-ante component differentials

**Control timing is specified exactly**, so nothing post-round leaks into what
is labelled pre-round context:

| Control | Measured at |
|---|---|
| Score differential | **before** round N (excludes N's own result) |
| Side | during round N |
| Loadout / economy | **start** of round N |
| Round-N result | a separate control, never folded into the others |

**Economy encoding:** `economy_graphs._tier_for` returns categorical
`PISTOL`/`ECO`/`FULL_BUY`, and collapsing that to a single ordinal difference
discards information. The economy control is therefore the **raw team-average
loadout differential plus the full-buy player-count differential**, with
**one encoding, not a menu**: an alternative one-hot tier encoding was
considered and dropped rather than left as an inner-CV choice, since it would
be one more thing selected on data without earning its complexity.

**The incremental gain from 3 to 4 is the headline result** -- it is the only
number that shows Impact's machinery carries information beyond "who won the
round and what they could afford next." Each step reports ΔAUC and Δlog-loss
with CIs.

### Yardsticks (scoring, always out-of-fold)

Every candidate is scored on all three, giving a targets x yardsticks matrix so
cross-target generalization is visible:

1. **First half -> match outcome.** A single number, not split by side -- see
   the observation-unit section for why an attack/defense split is vacuous
   under team-A orientation. Eligible matches only (n = 1,129).
2. **Full match -> match outcome.** Read **only as the gap over the raw
   kill-differential baseline** on the identical scale. The absolute figure is
   expected to be high for every weighting because the features contain the
   outcome's own kills; the report establishes what it actually is rather than
   asserting a number here.
3. **Round N -> rounds N+2 onward**, with the control ladder above.

### Protocol

- **The targets are FROZEN, not selected.** `k`, `gamma` and `match_weight`
  change the *definition of y*, not merely how well a model predicts a fixed
  outcome. A smoother target, or one diluted with the more-predictable match
  result, has lower achievable entropy and would win a log-loss comparison for
  reasons unrelated to whether Impact predicts winning -- and pooling folds
  that chose different configurations would pool predictions of different
  quantities. So one primary target is declared up front per family
  (`T1`; `T2` at k=3, gamma=0.7, match_weight=1.0; `WPA`), and alternatives run
  as **sensitivity analyses compared only on the fixed binary yardsticks**,
  whose labels are identical across configurations.
- **Nested cross-validation.** Outer 5-fold by match for all reported numbers;
  inner folds *within each training fold* select **L2 only** -- the one
  hyperparameter that does not change the outcome being predicted. Selecting
  anything on the folds used for reporting would manufacture optimism. **The
  inner-CV objective is weighted log loss**, matching the weights the fit uses.
- **AUC is for yardsticks; weighted log loss is for targets.** T2's target is a
  weighted fraction of later round wins. Rounding it at 0.5 to manufacture a
  binary label would change the estimand and discard the observation weights
  that `gamma` and `match_weight` exist to set, so no AUC is computed against a
  target. The yardsticks' labels are genuinely binary and carry the AUC.
- **The constrained `FACTOR_WEIGHTS` search and the damage-multiplier choice
  also run inside the training/inner folds**, never once over all data --
  they are model selection like any other.
- All rounds of a match live in the same fold.
- **Bootstrap by match** (cluster resampling, keeping all of a match's rounds
  together). Never resample expanded target rows independently.
- **Standardize features using training-fold statistics** before ridge fitting,
  so L2 penalizes differently-scaled columns comparably. Back-transform
  coefficients for reporting.
- **Baselines are mandatory in every report:** current hand-tuned Impact, team
  kill differential, damage alone. **Not a K/D ratio** (undefined at zero
  deaths), and **not kills and deaths as separate columns** -- in the
  differential representation those are the same baseline twice, since
  `deaths_A == kills_B` in 99.1% of this DB's 24,157 rounds.
- Each baseline is converted to a probability by a logistic calibration fit
  **on the training fold only**; AUC can consume raw scores, log-loss cannot.
- If Impact cannot beat kill differential, that is the finding and the tool
  says so plainly.

## Sequencing

1. **Task 0** reconstruction gate (ships as a test).
2. **Stage 0** descriptive report on current Impact.
3. **Stage A** co-primary fitting: T1 and T2, with the control ladder, ex-ante
   components, and the constrained mapping back to `FACTOR_WEIGHTS`.
4. **Stage B** leverage-weighted WPA attribution.
5. **P2 / P3** designed from the observed effect sizes.

**Implementation planning splits here:** the first plan covers P0 + Task 0 +
Stage 0 + Stage A. **Superseded by an explicit user decision:** the
implementation plan covers Stage B as well, so that the T1/T2-vs-WPA comparison
the user asked for exists in one common yardstick matrix rather than being
deferred. Stage B's tasks still carry a hard gate -- they are not started until
Stage A's report has been produced and read.

## P0 -- `webapp/app/services/stats_math.py`

Pure numeric helpers. No domain knowledge, no DB, no imports from other
`app.services` modules.

- `fit_logistic(X, y, weights, l2)` -- weighted IRLS, accepting fractional `y`
  in [0, 1]
- `auc(scores, labels)`, `log_loss(probs, labels)`
- `platt_calibrate(scores, labels)` -- training-fold calibration for baselines
- `point_biserial(values, labels)`
- `tercile_buckets(values)`
- `cluster_bootstrap_ci(fn, groups, draws, seed)` -- resamples groups (matches)

**numpy only.** `scipy` is installed locally but absent from
`requirements.txt`, which `render.yaml` installs from; hand-rolling IRLS keeps
the deploy untouched.

## P1 -- `webapp/app/services/impact_eval.py` + `webapp/scripts/evaluate_impact.py`

Mirrors the existing `app/services/fight_ev.py` + `scripts/validate_fight_ev.py`
split: computation in a service module, CLI wrapper in `scripts/`.

### Per-round observation

- **Features (differential, team A minus team B):** `damage`, `econ_impact`,
  `time_impact`, `swing_impact` -- in both `ex_ante` and `realized` variants,
  never mixed.
- **Controls** (timing per the table in the control-ladder section): score
  differential before round N, side during round N, start-of-round-N economy
  (raw team-average loadout differential + full-buy count differential from
  `RoundPlayerStat.loadout`, as a TEAM AVERAGE not a sum -- a sum silently
  encodes how many player-stat rows a round happens to have), round number, and
  round-N result as its own separate control.
- **Baselines:** kills, deaths, kill differential, damage.
- **Context:** `match_id`, `round_number`, round outcome, match outcome.

### Target seam

Every target builder returns `(X, y, w)` with `y` in [0, 1]:

- `first_half_target(observations)` -- T1. Eligible matches only (all 12
  genuine first-half rounds present).
- `forward_window_target(observations, k, gamma, match_weight)` -- T2. Expands
  round N into one weighted observation per future round N+1..N+k with weight
  `gamma**j`, never crossing a half boundary, skipping terminal rounds, and
  attaching the match outcome at `match_weight` **only for N <= 12**. Sweep
  `k` in {2, 3, 4}, `gamma` in {0.5, 0.7, 0.9}, `match_weight` in **{0, 0.5,
  1.0}** -- zero included so the auxiliary has to earn its place. These are
  **sensitivity runs scored on the fixed yardsticks**, never selected against
  their own losses; the primary target is frozen at k=3, gamma=0.7,
  match_weight=1.0.
- `wpa_target(observations, value_model)` -- Stage B.

### Output

Printed table plus JSON (`--out`, `DATABASE_URL` override honoured, matching
`validate_fight_ev.py`):

- Stage 0's descriptive block
- fitted coefficients per outer fold, with sign-stability and the collinearity
  diagnostics above
- both mappings back to `FACTOR_WEIGHTS` (unconstrained and constrained)
- the T2 control ladder with incremental ΔAUC / Δlog-loss
- the targets x yardsticks matrix, every cell with cluster-bootstrapped CIs
- `n` at every level, and the ex_ante/realized label on every component number

## P1 Stage B -- `webapp/app/services/win_probability.py`

`V(state) = P(win match | round differential, rounds played, side)`.

**Framing, corrected:** Stage B **defines an impact measure; it is not
independent predictive validation.** `V(after) - V(before)` is dominated by the
round's own outcome, which the features nearly determine -- so it does not
escape the tautology. Its value is leverage-aware attribution: it says a swing
in a close, late, economically pivotal round matters more than the same swing
in a decided one.

**Formulation (corrected):** signed `ΔV` is not a probability and cannot be the
`y` of a logistic fit. Instead:

- **label** = did this team win the round (0/1)
- **sample weight** = `abs(V(after) - V(before))`

so the fit weights high-leverage rounds more without treating signed WPA as a
probability.

**`V(state)` is fit inside each outer training fold.** Fitting it once over all
matches and then running outer CV would leak evaluation outcomes into the
target.

**Econ enters as a measured second step.** Fit the base feature set, then add
econ state and report the held-out log-loss delta. That delta is the
quantitative answer to "how much does econ carryover actually matter."

**Resolved: econ enters `V(before)` only, and `V(after)` refuses it.** Round
N+1's pre-buy economy is not a quantity this project extracts; the observation's
loadout is round N's. Carrying round N's economy into the after-state would make
an econ-aware `V(after)` quietly wrong -- and wrong in the direction that
flatters econ, since it would look as though the economy had not moved. So the
after-state is marked `econ_known=False` and `value_of` **raises** rather than
guessing. The measured econ increment is therefore reported on before-states
only; an econ-aware leverage weight would first require genuinely extracting
next-round pre-buy state, which is deferred.

## Testing

- `tests/test_stats_math.py` -- synthetic data with analytically known answers.
- `tests/test_impact_eval.py` -- fixtures for differential observation
  extraction, forward-window expansion **including the half-boundary cut**,
  terminal-round exclusion, the N <= 12 match-auxiliary restriction, T1
  first-half completeness filtering, fold assignment (no match spans two
  folds), and surrender-round exclusion.
- `tests/test_impact_exante_swing.py` -- three assertions:
  1. `build_impact_rows_for_match(..., use_realized_swing=True)` is
     **value-identical** to today's stored rows, field by field, over a sample
     of matches;
  2. `compute_impact_for_match` still persists and commits exactly as before
     (the wrapper is behaviour-preserving);
  3. the builder **writes nothing** when called directly -- no `ImpactScore`
     row is added, mutated, or committed. This is the regression test for the
     data-corruption bug the first revision would have shipped.
- `tests/test_stage0_cohorts.py` -- cohort thresholds, within-player tercile
  construction, and that bootstrap resamples recompute player means and tercile
  boundaries rather than reusing fixed ones.
- The Task 0 reconstruction identity ships as a test.

## Out of scope

- No new tables, no migrations.
- No change to `impact.py`'s **formula**. The permitted edit is structural
  only: extracting `build_impact_rows_for_match` so calculation and persistence
  are separable, with `compute_impact_for_match` kept as a wrapper whose
  behaviour is unchanged and covered by a value-identity test. Adopting fitted
  weights is a separate deliberate act through `recompute_impact.py` /
  `diff_impact_scores.py`.
- No web endpoint, no router, no template. Nothing here is imported by
  `app/main.py`, so the deploy path is untouched.
- Inner-curve refitting (Stage C), P2, P3 -- each its own spec.

## Revision note (second review)

Two blockers were found and fixed. **The `use_realized_swing` flag proposed in
the first revision was data-corrupting:** `compute_impact_for_match` commits
unconditionally (`impact.py:657`), so the flag would have overwritten stored
scores with ex-ante values. Replaced with a calculation/persistence split
(`build_impact_rows_for_match`). **The first-half attack/defense split was
impossible:** `attacking_team_for_round` returns `TEAM_1` for every round <= 12,
so under team-A orientation that subset is empty; the split is removed and side
is documented as a T2-only control.

Also: Stage 0 gained mandatory cohorts (94.7% of players have a single match,
measured); T1's n corrected to 1,129 for first-half completeness; both targets
pinned to `ex_ante` with the adoption caveat stated; T2's match auxiliary
restricted to N <= 12 with `match_weight=0` in the sweep; terminal rounds
excluded; control timing tabulated; economy encoding changed off the ordinal
tier collapse; constrained search and damage-multiplier selection moved inside
the inner folds; the inner-CV objective named; sign stability specified as a
refitting bootstrap; and the asserted "~0.95" full-match AUC removed in favour
of letting the report establish it.

## Revision note (first review)

Revised after a methodology review. Changes: the `swing_impact` leakage was
found and forced the ex-ante recompute (retiring the "no recompute needed"
premise); the observation unit went from two team-rows to one differential row
per round (24k, not 48k); a descriptive Stage 0 was added so the original
question is answered before any fitting; T2 gained the control ladder; Stage B
was reframed as attribution rather than validation and its target/fitter type
mismatch was fixed; and nested CV, cluster bootstrapping, training-fold
standardization, baseline calibration, surrender-round exclusion, and the
constrained-mapping optimization were all specified. The earlier claim that
1,151 matches could not support match outcome as a primary target was
overstated and has been corrected; T1 and T2 are now co-primary per the user's
decision.
