# Predeclared values for the Impact scoring rework

**Purpose:** both specs make roughly a dozen commitments of the form
"predeclared", "declared before running", "fixed before fitting". Those are only
worth anything if they exist **before** the number they judge. This file is the
single place they live, committed ahead of the first run so the timestamp is
checkable.

**The rule this file exists to enforce:** a value here is fixed before the
measurement it governs. If a result comes in outside a tolerance, the tolerance
does **not** move -- the result is a finding. Changing a value here is allowed;
doing so **after** seeing the number it judges is not, and any such change must
be recorded as an amendment with its date and reason, never a silent edit.

Sources: `specs/2026-09-03-plant-window-and-time-factor-design.md`,
`specs/2026-09-04-econ-impact-separate-component-design.md`.

## Fixed now

| value | what it governs | source |
|---|---|---|
| **2%** | death-side residual tolerance, `mean(K*T*c*s)/mean(K*T) - 1`. `M20` expects ~+0.7% | time, "The gate is on the KILL side" |
| **0.05** | `\|c - 1\|` for both centring constants. A **reported finding**, not an assertion | time, "The fitting contract" |
| **0.05** | mean absolute calibration error over `t >= 38`. **Reported loudly, never gated** | time, "Calibration" |
| **10 fixed-width bins on [0,1]** | calibration binning -- fixed, not quantile | time, "Calibration" |
| **60 round-seconds** | support floor per cell, counted after pooling at the level being tested | time, "Estimating `V`" |
| **5 seconds** | minimum eligible seconds in a `mean_over_t` denominator before the whole cell falls back | time, "The estimator contract" |
| **W = 2** | smoother half-window, count-weighted centred moving average | time, "The estimator contract" |
| **0.2 - 1.7** | time scalar clamp, on the **pre-centred** scalar. Effective range is `[0.2c, 1.7c]` and is reported | time, "Parameterisation" |
| **adv -1..+1** | the range the middle slope is fitted through (83.0% of affected kills) | time, "Parameterisation" |
| **adv -3..+2** | fitted support; outside it, clamp to the nearest fitted level | time, "The fitting contract" |
| **30, 20, 10, 5, 0s** | shape knots, fixed not estimated; the sub-10s plateau is imposed | time, "The fitting contract" |
| **38.0 / 41.5** | band boundaries, half-open: `[0,38.0)`, `[38.0,41.5)`, `[41.5,45)`; lookup by `floor(t)` | time, "The estimator contract" |
| **0.50** | `\|corr(econ_component, leverage aggregate)\|` pass threshold, realized mode, per scored player-round, early and late reported separately | econ, "Attribution must be a share" |
| **4200** | `FULL_BUY_THRESHOLD` (existing constant, restated so it is not silently changed) | econ, scope lock |
| **19500** | `R`, the fixed full-buy reference (`5 x 3900`) | econ, §2 |
| **0.5 + 0.2n, range 0.5-1.5** | `denial(T)`; the 0.5 floor is unconditional | econ, §4, §5a-i |
| **2-4 / 14-16, 5-11 / 17-23** | early and late regime round ranges. Everything else scores 0 | econ, scope lock |
| **k=3, gamma=0.7** | forward window, shifted to `range(2, k+2)` with `weight = gamma**(step-2)` | econ, §9b + `impact_eval.py:467` |
| **two-sided 95%, 2,000 resamples, match-clustered** | every arm contrast. **Sign: `loss(arm) - loss(arm 0)`, positive = deterioration** | econ, §8d-i |
| **1 -> 2** | `IMPACT_CALCULATION_VERSION`, one bump for both specs, one rescore | both, Rollout |
| **3 -> 4** | `fight_ev.CALCULATION_VERSION` (functional -- feeds `_bootstrap_seed`) | time, Rollout |
| ~~**stays 2**~~ **-> 3** | `STATE_DIAGRAM_CALCULATION_VERSION` -- **superseded 2026-09-09**, see Amendments | both, Rollout |

## To be fixed at first run, and recorded here before any result is read

These are grids and mappings the specs deliberately leave to implementation.
**Write the chosen values into this file and commit, then run.** Backfilling
them afterwards defeats the point.

| value | constraint the spec already imposes |
|---|---|
| ~~`k`, the amplitude scale~~ | **FIXED 2026-09-07 -- see "The k / FLOOR / CEIL / W grid decision" below** |
| ~~`FLOOR` / `CEIL`, post-plant~~ | **FIXED 2026-09-07 -- see "The k / FLOOR / CEIL / W grid decision" below** |
| ~~`W` sensitivity grid~~ | **FIXED 2026-09-07 -- see "The k / FLOOR / CEIL / W grid decision" below** |
| ~~the early-regime wealth readout mapping~~ | **FIXED 2026-09-07 -- see "The early-regime f/g decision" below** |
| ~~the early-regime commitment gate~~ | **FIXED 2026-09-07 -- see "The early-regime f/g decision" below** |
| `ECON_SCALE` | a dispersion convention: matches `econ_component`'s SD to `time_impact`'s current SD, over all scored player-rounds in realized mode |
| distributional reporting thresholds | SD, p1/p5/p95/p99, largest per-player-round change, rank movement on the leaderboard -- each with a number declared before the rescore |

## The early-regime f/g decision -- FIXED 2026-09-07

Decided in a dedicated design discussion (not invented during implementation,
per the econ spec's explicit instruction and the implementation prompt's
"stop and ask" list). Governs `econ_round(T) = commitment(opp) *
denial_early(T)` for rounds 2-4 / 14-16 (econ spec section 5a). Committed
before any econ-component code runs.

```
# ---- Constants ----------------------------------------------------------
FULL_BUY_THRESHOLD = 4200    # existing; credits/player to afford a full buy
ZERO_AT            = 6300    # = 1.5 * FULL_BUY_THRESHOLD; wealth at which f reaches 0
FULL_COMMIT        = 3900    # = R / 5; the value of a full buy
SAVE_FLOOR         = 1000    # value of a sidearm + light shields

# ---- f : denial_early(T) -- the victim team's resource SCARCITY next round ----
# Reads a LEVEL, not a change. Victim team = T's enemies.
n              = expected enemy roster size in round N+1        # abstain if 0
team_wealth(T) = sum over that roster of (loadout + remaining), round N+1
avg_wealth(T)  = team_wealth(T) / n
denial_early   = clamp(1.5 * (1 - avg_wealth(T) / ZERO_AT), 0, 1.5)      # 0 .. 1.5

# ---- g : commitment(opp) -- equipment value they had on the table in round N --
# C(v) = section 1's committed value: post-buy equipment value, including gear
# carried from a previous round or received as a drop, NOT spending this round.
commit(opp)     = mean over the expected enemy roster of C(v), round N
commitment(opp) = clamp((commit(opp) - SAVE_FLOOR)
                        / (FULL_COMMIT - SAVE_FLOOR), 0, 1)              # 0 .. 1

# ---- Composition ----------------------------------------------------------
econ_round(T)   = commitment(opp) * denial_early(T)                      # 0 .. 1.5

# ---- Guards, applied BEFORE any division -----------------------------------
# n == 0, or any expected roster member missing a round N record (g) or a
# round N+1 record (f)  ->  econ_round(T) = 0.
# Abstention is zero credit, never the 0.5 baseline, and never an average
# taken over whichever rows happen to exist.
```

`denial_early` reference points, by average wealth per player: `0 -> 1.5`,
`2,100 -> 1.0`, `4,200 -> 0.5`, `6,000 -> 0.071`, `6,300 and above -> 0`.

| | `f = denial_early` | `g = commitment` |
|---|---|---|
| reads | victim team's loadout + remaining, round N+1 | victim team's `C(v)`, round N |
| over | expected enemy roster, summed then divided by `n` | expected enemy roster, averaged |
| aggregation | team wealth pooled before any clipping | per-player mean against per-player anchors |
| direction | decreasing in wealth | increasing in commitment |
| range | `[0, 1.5]` | `[0, 1]` |
| thresholds | zero at 6,300/player; 0.5 falls at the 4,200 full-buy line | 0 at 1,000/player, 1 at 3,900 |
| measures | resource scarcity -- how limited their remaining resources are. Does not measure how much your kills took from them; does not establish rebuy resilience | how much equipment value was on the table to be denied |
| why | pooled because resources are shareable across the team through drops; 6,300 is a chosen scarcity cutoff at 1.5x the full-buy reference, representing a buffer above that reference, not a guaranteed rebuy after a loss | a clamped ramp from "sidearms only" to "full buy," anchored on Valorant's price list rather than this dataset's percentiles, sending a fully-saving team to exactly zero |

**Predeclared sensitivity grid -- report across it, do not select on it.**
`ZERO_AT` in `{5250, 6300, 7350}` (= 1.25 / 1.5 / 1.75 x `FULL_BUY_THRESHOLD`)
x `SAVE_FLOOR` in `{600, 1000, 1400}` x `FULL_COMMIT` in `{3400, 3900, 4400}`,
plus one shape variant: `g` as a hard step at 2,000/player.

**Predeclared diagnostics, numbers fixed now.**

- **Saturation.** Fraction of early enemy-team-rounds at each clamp of `f`
  (0 and 1.5), reported separately. Above 50% at a single clamp is a
  finding -- reported, not gated.
- **Reserve insensitivity.** Reduce `avg_wealth` by 1,294 credits, floored
  at zero, and report the fraction of eligible situations where
  `denial_early` is unchanged. This is a sensitivity scenario built on
  `M28`'s observed contrast between kill-count strata, not a causal
  estimate of wealth removed by kills.
- Fraction of early scored player-rounds with `econ_component == 0`, and
  the fraction abstaining for missing data, reported separately -- they
  mean different things.
- The component's SD, early and late regimes reported separately.

**Two spec amendments this decision requires**, applied directly to
`specs/2026-09-04-econ-impact-separate-component-design.md`:

1. Testing, the early-regime bullet ("assert instead that a bought-in
   victim team produces non-zero early credit") is unsatisfiable as
   written -- a bought-in team left at or above 6,300 average wealth
   correctly scores zero even with `g = 1`. Scoped to: a bought-in victim
   team left below `ZERO_AT` produces non-zero early credit.
2. Section 5a's seam example (`[5000,5000,5000,5000,5000]` vs.
   `[4000,4000,4000,4000,9000]` scoring identically) is true as written
   under this pooled `f`, so it needs no change -- but the early regime can
   now score *below* the late regime's 0.5 floor, which widens the
   expected seam rather than narrowing it.

**Settled, no longer open.** Both halves of the wealth pair are confirmed
post-buy: `loadout` is post-buy equipment value, `remaining` is the
matching post-buy balance -- proven on round 1, where every player starts
with exactly 800 credits and, across 31,240 player-rows, `remaining` runs
0-800 with a mode of 0 and only 911 rows at 800 (impossible for a pre-buy
figure); the global `max(remaining)` is 9,000, exactly the credit cap. So
`loadout + remaining` is a coherent same-instant snapshot and drops/rebuys
are already reflected in it -- they are not modelled again.

**Deferred, deliberately.** Rebuy resilience (pooled cash plus the
applicable loss payout) is arguably better matched to `M27e`'s two-round
mechanism, but it requires the consecutive-loss ladder in the formula,
which would make `M13`'s ladder exposure structural rather than incidental.
That is a v2 redesign with its own measurement, not a threshold swap -- do
not substitute it while keeping this formula's interpretation.

## The k / FLOOR / CEIL / W grid decision -- FIXED 2026-09-07

Decided across five external review rounds, recorded in full in
`2026-09-07-three-grids-declaration-draft.md` (rationale, the proxy evidence
base, and the round-by-round change log -- not restated here). Governs Part 3's
pre-plant amplitude scale and Part 4's post-plant clamp and smoother
(`specs/2026-09-03-plant-window-and-time-factor-design.md`).

**`k` (Part 3, the pre-plant amplitude scale).**

```
amplitude = k * logit_lift(adv, side)
scalar    = clamp(1 + amplitude * shape(seconds_to_plant), 0.2, 1.7)
```

- Grid (11 members, fixed): `k in {0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3,
  1.7, 2.2, 2.8}`.
- Crossing rates are counted on the **raw, unclamped** scalar
  (`raw_scalar = 1 + k*logit_lift*shape`) -- `scalar` itself is already
  clamped, so testing it against 0.2/1.7 is vacuous and reports 0.00% at every
  `k`.
- Two denominators, both reported: **target** = all non-self kills in
  non-surrendered rounds (484,610 on the 2026-09-07 snapshot, `M5`'s
  denominator); **also reported** = pre-plant kills in non-phantom
  non-surrendered planted rounds (168,432).
- Targets on the target denominator: floor rate `< 1.5%`; ceiling rate in
  `[2.0%, 4.0%]`.
- Selection rule, deterministic: (1) among qualifying members, pick the one
  whose ceiling rate is closest to 3.0%; (2) if none qualifies, minimise
  `|ceiling - 3.0%|` among floor-qualifying members; (3) if none satisfies the
  floor constraint, pick the smallest `k` in the grid (this is the
  floor-rate minimiser, not merely a default -- both crossing rates are
  monotone non-decreasing in `k`). Every tie breaks toward the smaller `k`.
  Branches 2/3 are reported as a finding with the full 11-row table; the grid
  is never widened or given an off-grid value.
- **Selected once per training population, never once globally**: shipped
  (frozen full dataset), the temporal 70th-percentile split (select on
  pre-70th data only, carry unchanged into evaluation), and each nested
  comparison arm (side interaction in/out; pooled vs. state-specific
  proximity) selects within its own training population. A shipped-vs-training
  difference is reported, not reconciled.
  **These three are the complete list of populations needing their own
  selection** -- checked against the code 2026-09-07: `impact_eval.py`'s only
  fold/CV machinery (`stable_folds`/`assign_folds`) is invoked exclusively
  under `use_realized_swing=False` (no call site passes `True`), and Part 3's
  factor is pinned to exactly 1.0 under that mode. `k` is therefore a
  structural no-op inside every CV/bootstrap context this codebase has,
  including the econ spec's 5-arm forward measurement (`8d-i`), which runs
  "same folds" but in ex-ante mode per econ `9a`. The only places `k` is live
  (realized mode) are shipped scoring and the `ECON_SCALE` fit (econ `9b`),
  and the latter consumes whatever `k` shipped uses rather than re-selecting
  it. This closes the declaration draft's open question 8.2.
- Nothing else may influence selection -- not the death-side residual, any
  win-correlation/predictive loss, rank movement, nor `|c-1|` or the effective
  bounds (`|c-1|` moves monotonically with `k` and pulls opposite to the
  clamp target). All reported **at** the selected `k`, never used to choose
  it. No confidence intervals attached to `k`.
- **Exploratory prediction only, not a tolerance:** on the proxy in the draft
  doc's §0, `|c-1|` lands in `[0.10, 0.13]` and the rule selects `k = 0.8`.
  Neither is licence to re-tune the grid if the real fit differs.

**`FLOOR` / `CEIL` (Part 4, the post-plant clamp).**

```
post_plant_factor = clamp(D / mean_over_t(D), FLOOR, CEIL)
```

- Shipped default: `FLOOR = 0.05`, `CEIL = 2.0`.
- Sensitivity grid: all nine pairs of `FLOOR in {0.02, 0.05, 0.1} x
  CEIL in {1.5, 2.0, 2.5}`, each at `W = 2`, each with its own recomputed
  centring constant `c`, reporting effective bounds `[c*FLOOR, c*CEIL]`.
- For `FLOOR < CEIL` the two clamps act on disjoint kills -- report as two
  1-D sensitivities coupled only through `c`; nonlinear downstream summaries
  (rank movement, loss) may show real joint effects and are reported as found.
- Report alongside every row: kill-weighted floor/ceiling binding rates;
  pre-clamp ratio distribution (min, p1, p50, p99, max); count of
  non-positive denominators falling back to 1.0; count of cells per pooling
  rung; count of **negative numerators** (`D < 0` with a positive
  denominator) reported separately from ordinary floor binding -- report
  only, no gate, still scores at `FLOOR` (the negative-numerator fallback
  itself is out of scope here, see the draft doc §5.1).
- **"Final scores insensitive across the grid" != "bound inert."** Inertness
  requires **zero pre-clamp crossings** on the raw ratio; identical centred
  scores can coexist with 100% floor binding if `c` moves inversely with
  `FLOOR`. Report the observed pre-clamp extremum alongside either statement.
- No confidence intervals attached to `FLOOR`/`CEIL`.

**`W` (Part 4, the post-plant smoother half-window).**

- Shipped default: `W = 2`, unchanged, **never selected from results**.
- Sensitivity grid: `W in {0, 1, 2, 3, 4, 6}` at `FLOOR=0.05, CEIL=2.0`, `c`
  recomputed per `W`. `W=0` = "no moving-average smoothing" (support floor,
  pooling ladder, endpoint rule, neutral fallback all still active); `W=6` =
  broad-window stress test. A flat table is a finding, not a failure. Report
  supported-cell counts at each `W`.
- The pooling ladder runs first and unchanged, and fixes each cell's support
  rung; the 60-observation floor is never applied to a summed moving window.
  The window is evaluated **entirely at the target second's resolved rung**:
  each neighbour contributes its estimate at the *target's* pooling level,
  weighted by its own per-second observation count at that level (a pooled
  count is never repeated across the seconds it covers); seconds with no
  observations at that level contribute weight zero, and the window is not
  widened to compensate.
- Count-based endpoint support is independent of `W`; the **finally scored
  population is not** -- smoothing changes `V`, hence `D`, hence
  `mean_over_t D`, which can cross zero and trigger the neutral fallback.
  Report both populations (count-supported vs. ratio-scored) separately at
  each `W`.
- The window is **not** truncated at the 38.0/41.5 band boundaries; at
  `W >= 2` it routinely straddles 41.5, per the spec's "no hard discontinuity
  at plant+38".
- A result favouring another `W` is a finding requiring a dated amendment.

**Snapshot validation.** Re-run against the local DB 2026-09-07 (3,124
matches): denominators (484,610 / 168,432) and the side-specific clamp table
reproduce the draft doc's §0 numbers exactly (e.g. `k=0.60` -> floor 0.01%,
ceiling 2.17%, `|c-1|=0.103`, matching to the digit).

## Amendments

Append here with date and reason; do not edit rows above in place.

- **2026-09-07** -- the early-regime `f`/`g` mappings, previously listed
  above as "to be fixed at first run," are fixed. See "The early-regime
  f/g decision" section. Do not edit the rows above in place; the
  strikethrough marks them superseded.
- **2026-09-07** -- the `k` / `FLOOR` / `CEIL` / `W` grids, rules and targets,
  previously listed above as "to be fixed at first run," are fixed. See "The
  k / FLOOR / CEIL / W grid decision" section, and the full rationale in
  `2026-09-07-three-grids-declaration-draft.md`. Do not edit the rows above
  in place; the strikethrough marks them superseded.

### 2026-09-09 -- replay correction; descriptive figures refreshed, no governing value moved

**Reason.** The diagnostics and the two pre-plant fitting modules
(`preplant_time_model.py`, `preplant_fit_support.py`) replayed alive counts in a
way that diverged from `impact.py` on self-kills and on resurrections. 15.06% of
rounds contain at least one, and the error compounds within a round, so 6.42% of
pre-plant kills were filed under the wrong state. All of them now mirror the
scorer, and the affected measurements were rerun.

**What did NOT move.** No governing value in "Fixed now" changed. Specifically:
the clamp `0.2 - 1.7`, the knots `30, 20, 10, 5, 0s`, the fitted support
`adv -3..+2`, the support floor `60`, `W = 2`, `5 seconds`, `38.0 / 41.5`, and
the amplitude scale's selected band (`k = 0.60-0.80`) are all unchanged under
the corrected replay. The `adv -1..+1` range itself is unchanged.

**What did move, and where it is recorded:**

| figure | before | after | note |
|---|---|---|---|
| share of affected kills in `adv -1..+1` | 82.6% | **83.0%** | descriptive gloss on an unchanged row; edited in place because the *value* (`adv -1..+1`) did not change |
| affected population (`M5`) | 168,370 (34.7%) | 168,432 (34.8%) | spec §1 |
| pre-plant curve, attacker reference rate | 0.749559 | **0.752707** | fitted, not predeclared |
| pre-plant curve, defender reference rate | 0.378668 | **0.377010** | fitted, not predeclared |
| worked example, attacker at `dt=16`, alpha=3 | 1.191 | **1.182** | fitted consequence of the two above |

The reference rates are outputs of the fit, not predeclared inputs, so refitting
them is not an amendment to a fixed value -- they are listed here only so the
change is discoverable from this file rather than only from the curve JSON.

**Conclusion-level consequences** (both recorded in full at their measurements):
`M1`'s plateau now holds in three of four even states rather than all four, and
`M7`'s 2v2 elevation weakened from +4.9pp [+1.5,+8.3] to +3.6pp [+0.2,+7.0].

### 2026-09-09 -- `STATE_DIAGRAM_CALCULATION_VERSION` 2 -> 3; the "stays 2" row is superseded

**This amends a fixed value.** The row above reads:

> | **stays 2** | `STATE_DIAGRAM_CALCULATION_VERSION` | both, Rollout |

with the recorded reason: *"admitting OT rounds adds data, as ingesting more
matches does; replay semantics are unchanged."* Do not edit that row in place;
this entry supersedes it. **The new value is 3, effective immediately, not at
rollout.**

**Reason -- the premise does not hold.** `b2d1d55` deleted `state_replay.py`'s
`round_number > 24` exclusion once `plant_window.attacking_team` was made total,
admitting **1,274 overtime rounds across 374 matches** into the replay. The
analogy to ingestion is what fails: ingesting a match invalidates *that match's*
players, and every one of them, through
`player_view_cache.invalidate_player_cache`. Admitting OT invalidates nobody. It
changes the numbers for **every player who has ever played overtime** -- 2,916
players, **399 of them holding a cached row** -- including players with no new
matches, whose rows would otherwise never be revisited. "Adds data" and
"invalidates correctly" are not the same property, and only the second one was
ever true of ingestion.

The consequence, had this shipped unbumped: a cached player page served pre-OT
round-win and kill-order diamonds while any live recomputation of the same page
served OT-inclusive ones. Same page, two rules, decided by cache state.

**Not a live incident.** `b2d1d55` is on `impact-scoring-impl` only.
`origin/main` -- what Render deploys, `render.yaml` naming no branch -- still
carries the `round_number > 24` exclusion, so the deployed site is internally
consistent today. This was a MERGE-blocker, not a production one. The
remediation plan and the session that produced it both described it as live;
that was checked against `git branch --contains` and is withdrawn.

**Scope of the bump.** `cache_version()` is a composite, so moving any one
constant invalidates every row -- all 4,478, not only the 399 affected. That is
accepted rather than worked around: a targeted invalidation would need a
per-player OT predicate that nothing else in the cache layer has, to save a
recompute the project can afford.

**`fight_ev.CALCULATION_VERSION` is left at 3**, with its 3 -> 4 bump still
scheduled for rollout. `state_replay` feeds both products, so the fight-EV blob
is equally stale -- but the composite already forces the invalidation, so the
bump would buy honesty in the individual number, not correctness. Recorded here
so the staleness is discoverable rather than silent.

**Also corrected**, both stale in ways unrelated to any decision: the comment on
`STATE_DIAGRAM_CALCULATION_VERSION` still listed "overtime" among the
exclusions v2 introduced, and `cache_version()`'s worked example still read
`2_002_003_001` when the function returns `4_003_003_001` (the schema digit had
been stale since the v3/v4 pistol reshapes).

### 2026-09-09 -- Part 4's fitted figures, the death-side residual, and the arm-2 conclusion

**No governing value moved.** `c` is a fitted output, not a predeclared input;
`FLOOR = 0.05`, `CEIL = 2.0`, `W = 2`, the 60-observation support floor, the 2%
death-side tolerance and the 0.05 calibration tolerance are all unchanged and
none was tuned. This entry exists so the figures are discoverable from this
file rather than only from a script's stdout.

**Part 4, measured on the full 3,124-match dataset:**

| figure | value | against |
|---|---|---|
| `c` | **1.287003** | not predeclared; `\|c-1\| = 0.2870`. Corrected from 1.277851; see the claim-3 amendment below |
| effective bounds | `[0.0644, 2.5740]` | `[c*FLOOR, c*CEIL]` |
| supported / fallback kills | 149,937 / 39 | -- |
| out-of-fold calibration MACE, `t >= 38` | 0.0061 | tolerance 0.05, **inside** |
| out-of-fold calibration MACE, overall | 0.0010 | tolerance 0.05, **inside** |
| death-side residual | **+2.59%** | tolerance 2%, **EXCEEDS** (was +2.61% before the claim-3 correction) |

`c` is identical before and after the remediation, as it must be: it is defined
on the kill side alone.

**The death-side residual: +0.52% -> +2.61%, and the old number was wrong.**
Not a re-measurement -- a correction. `fit_postplant_factor.py` built its
baseline with `for_death` left at its default `False`, so the residual was
measured against what today's scorer pays *kills*. In `plant+38..45` that is
1.75 where a death is paid 0.50. 2,750 of 149,976 scored post-plant kills
(**1.83%**) sit in that window, which makes the wrong baseline 2.07% too large
and understates the residual by almost exactly that:

| | |
|---|---|
| what deaths would pay under Part 4 | 21,372,143 |
| what deaths pay today (correct baseline) | 20,828,581 |
| what *kills* pay today (baseline used) | 21,260,691 |

**Exceeding the tolerance is accepted and not acted on.** The spec's own rule
is that this is a finding, never something to tune away, and there is no second
free constant to pin the death side with in any case -- one constant, two
equations. Deciding factor from the project owner (2026-09-09): death impact
was always a function of kill impact, so it moving under a kill-side retune is
expected and correct behaviour rather than a defect.

**Arm 2's recorded reading is withdrawn and replaced.** The five-arm report
previously recorded the post-plant retune as a small measured improvement. That
was measured with `c` solved and then discarded, so the arm carried a 22%
shrinkage of the whole post-plant term alongside Part 4's time shape. Three
runs separate them, all 5-fold, 2,000 match-clustered draws, sign convention
`loss(arm) - loss(arm 0)`:

| arm | contrast | reading |
|---|---|---|
| shape **and** level (as previously recorded) | -0.00003 [-0.00006, -0.00000] | improvement |
| shape alone (`c` applied -- what would ship) | **+0.00000 [-0.00003, +0.00003]** | **inconclusive** |
| level alone (legacy ramp x 1/c, no shape change) | **-0.00003 [-0.00005, -0.00002]** | **improvement** |

So **Part 4's measured time shape is worth nothing detectable out-of-fold**,
and the whole of the previously recorded improvement was the level. The
level-only interval is the tighter of the two, excluding zero with margin where
the confounded one merely touched it.

Two cautions on the level result. The scale is `1/c = 0.7826`, chosen because
it is what the uncentred table implied -- it is not fitted, so this shows that
*this* scale beats 1.0, not that it is the right one. And `c` is solved on full
data and depends on `V`, which is fitted on round outcomes, so the specific
value has seen the labels indirectly: trust the sign, not the magnitude. A
clean version selects the scale per fold on training matches only. Recorded as
an open lead, not a decision.

Arms 1, 4 and 1-vs-4 are bit-identical across all three runs, as they must be:
none of them touches the flag-gated post-plant path.

**Collinearity, measured rather than asserted.** An earlier draft of this
analysis attributed arm 2's move to a collinearity artifact by analogy. That
was unmeasured and is withdrawn. Measured (fold 0, 65,727 rounds): the four
diagnostic components carry `max |r| = 0.89` (damage vs `time_impact`), above
this project's own 0.70 threshold, and near-identical across arms (0.8947 /
0.8999 / 0.8839) -- so it is real but cannot explain a *difference* between
arms. It also cannot reach the arm contrast at all, which puts one coefficient
on the fixed composite and never estimates the components separately. Where it
could reach -- the composite against its controls -- `max |r| = 0.195`. It does
bear on the labelled per-component diagnostic block, where coefficients are
refit.

### 2026-09-09 -- Part 3's centring constant, the death-side residual, and the dt=30 boundary decision

**No governing value moved.** `c` is a fitted output, not a predeclared
input. The 2% death-side tolerance, the 0.05 abs(`c`-1) reporting threshold and
the `0.2 - 1.7` clamp are all unchanged, and none was tuned. Nothing was
activated: `enable_preplant_empirical` is still `False` and
`IMPACT_CALCULATION_VERSION` is still 1. This entry exists so the figures are
discoverable from this file rather than only from a script's stdout.

**Part 3, `preplant_empirical_factor` at strength 3.0, measured on the full
3,124-match dataset** (local and Render return identical figures):

| figure | value | against |
|---|---|---|
| AFFECTED population | 168,432 | reproduces `M5` exactly |
| SCORED / FALLBACK split | **130,506 / 37,926** | 77.48% of affected by count, 76.91% by kill-order mass |
| `c` | **0.900537** | not predeclared; solved over SCORED alone |
| abs(`c`-1) | **0.099463** | the 0.05 row is a **reported finding, not an assertion**. This is roughly double it |
| effective bounds | `[0.180107, 1.530914]` | `[c*0.2, c*1.7]` |
| realised post-centring range | `[0.756372, 1.354622]` | the `0.2 - 1.7` clamp binds on **0 of 130,506** scored kills -- it is inert at this strength |
| death-side residual | **+0.4114%** | tolerance **2%**, **WITHIN** |

`c` is solved over SCORED alone, so which population is used is itself a
recorded decision: over AFFECTED it would be 0.921710 instead.

**abs(`c`-1) = 0.0995 is a finding, recorded not acted on.** The row above
governs both centring constants and calls itself a reported finding rather
than an assertion; Part 4's own `c` sits 0.287 from 1. Nothing was adjusted
to bring it closer.

**The death-side residual came in at +0.41%, below `M20`'s ~+0.7%
expectation.** Part 4's finding-4 defect -- the residual measured against a
`for_death=False` baseline -- has no analogue pre-plant, where the legacy
factor is 1.0 for kills and deaths alike, so `mean(K*T)` already is the
death-side baseline. That was checked, and deliberately not "fixed".

**The `dt = 30` boundary jump is KEPT.** Decided by the project owner on
2026-09-09 with all options measured. Under the chosen policy, post-centring
at strength 3.0: attacker `1.0123 -> 1.0000`, defender `0.7564 -> 1.0000`.
The structural reason no transition policy was adopted: because `c` applies
only inside the scored region, centring itself creates a step of size
abs(1-`c`) at whichever edge that region ends, so a taper relocates the jump
rather than removing it. Full option table in the time spec, "The `dt = 30`
boundary: the jump is KEPT, deliberately".

**Self-kills are scored on the death side and never on the kill side**,
per the project owner. That is what `impact.py` already does, so no code
changed. Exposure of the resulting centring gap -- these events are scored by
a constant solved on a population that excludes them -- is 272 events,
0.245% of pre-plant death-side mass, moving it +0.013% uncentred.

**The 2026-09-09 replay-correction amendment above did not list the
candidate doc's worked example table.** That table's figures moved with the
reference rates and are stale by up to 0.006 at strength 1; `dt=30` reads
1.0440 / 0.9452 there and is now **1.0414 / 0.9466**. Recorded here rather
than edited in place; the candidate doc carries the same note.

### 2026-09-09 (later) -- external review, three P2 findings; `c` corrected to 1.287003

A second external review of the remediated branch raised three P2 issues. All
three were checked against source and reproduced numerically before any code
moved; none was accepted on assertion. **No governing value moved**; `c` is a
fitted output and its correction is recorded here for discoverability.

**Claim 3 -- centring solved its legacy baseline at the TABLE INDEX.** Upheld,
and it is the one that moves a number. `extract_postplant_kills` stored only
`t = int(kill_time - plant_time)`, and the centring solve evaluated the legacy
ramp there, while runtime evaluates `1 + (kill_time - plant_time) / 53` at the
real timestamp.

| | |
|---|---|
| scored post-plant pre-resolution kills | 151,141 |
| carrying a fractional offset | **150,962 (99.9%)** |
| mean fractional part | 0.496 s |
| summed legacy baseline, exact times | 192,896.5 |
| summed legacy baseline, `floor(t)` | 191,507.7 |
| baseline understated by | **0.725%** |

`c` was biased LOW by the same margin: **1.277851 -> 1.287003** (+0.716%),
effective bounds `[0.0639, 2.5557] -> [0.0644, 2.5740]`, death-side residual
`+2.61% -> +2.59%` (still exceeding the 2% tolerance, still accepted). The
split is now explicit: EXACT seconds for the legacy ramp, the floored `t` for
V, support and the denominator buckets, which remain declared policy.

*One part of the review's claim 3 does not hold and is not adopted:* the
plant+38..45 window boundary is unaffected. **Zero** scored kills cross it under
flooring, because `extract_postplant_kills` already caps its window at
`plant + 45`. The example offered at 45.7s is outside the scored population.
The finding stands on the ramp arithmetic alone.

**Claim 2 -- the source revision missed the fields that decide LABELS.**
Upheld. `_REVISION_QUERIES` covered four tables while the replay consumes five.
`matches.team1_rounds_won` / `team2_rounds_won` decide `match_won_by_team_a`,
which is T1's entire label and the match-weight half of T2's: correcting a
final score from 13-11 to 11-13 flipped every label in that match while leaving
the revision byte-identical, so a cached replay was accepted with the old
labels. `match_players.agent` was likewise absent although `impact.py:536`
feeds it to `econ_component.committed_value`. Both are now in the digest.

**Claim 1 -- inner folds do not rebuild the post-plant table.** Structurally
upheld, **measured inert, and NOT actioned.** The report builds a table per
OUTER fold, then hands already-scored observations to `_select_config`, whose
inner-validation features have therefore seen their own outcomes. The outer
test fold remains clean and the review says so explicitly. But the only
quantity `_select_config` chooses on this path is L2, over `(0.1, 1.0, 10.0)`:

| L2 | pooled out-of-fold weighted log loss |
|---|---|
| 0.1 | 0.67291745 |
| 1.0 | 0.67291744 |
| 10.0 | 0.67291738 |

A spread of **7e-8** -- 400x below the smallest arm contrast the report
resolves (3e-5) and 20,000x below the econ deletion. An optimistically selected
L2 cannot move any contrast. The proposed nested rebuild costs roughly 30
additional whole-corpus replays (~15 min -> ~40 min per run) to correct a
quantity bounded at 7e-8, so it is **recorded as a known, measured limitation
rather than built.** Revisit if the feature set, sample size or L2 grid changes
enough for that spread to matter.

Worth noting on direction: the outer coefficient is fitted on training features
that are in-sample with respect to the table and applied to out-of-sample test
features. That mismatch's likely effect is to make arms 2 and 3 look WORSE, not
better, so it is not an optimism concern for the arms that matter.

### 2026-09-09 -- the one-parameter A search: declared BEFORE the run

**Nothing below has been run.** This entry is committed first so the grid, the
target, the controls and the decision rule are checkable against the timestamp
of the result. Per this file's own rule, if the answer lands outside the rule
below, the rule does not move.

**What is being fitted.** One parameter. `impact = A*damage + B*leverage +
C*econ_component`; **B is fixed at 1** and **C is excluded from this fit**.

`B = 1` is not a choice so much as an identity: the evaluator puts a FREE
coefficient on the composite, so overall scale is absorbed and only the ratio
A:B is identified. Fixing B pins the scale and leaves exactly one degree of
freedom.

`C` is excluded because the fit runs in EX-ANTE mode, where
`_econ_components_for_round` returns `{}` (the leakage gate -- the component
reads round N+1). `econ_component` is therefore identically 0 and cannot
contaminate the A:B ratio. Fitting C is separate, optional, later work
(econ spec 9b: realized mode, forward window from N+2, relative to a frozen
A:B). It is **not** a prerequisite for anything here.

**The search reduces to the existing fitter, verified rather than assumed.**
`fit_constrained_weights` searches `(damage_multiplier, w_econ, w_time,
w_swing)` under `w_econ + w_time + w_swing == 1`. In new-structure rows
`econ_impact` and `swing_impact` are written as 0, so the section 8c
zero-variance guard drops them (declared inert, below). With one live factor
`_simplex_grid_ndim(step, 1)` returns exactly `[(1.0,)]` -- checked -- so
`w_time = 1` is forced and the whole search collapses to the damage grid.
That is the one-parameter A search, with no new fitter.

The tied-coefficient gate is satisfied **by construction**: B is a single
scalar on the fused product `kill_order_bonus x time_factor`, which occupies
`time_impact` as one number. Nothing in this search can split it additively.

**NORMALIZATION -- the grid is RELATIVE, the reported A is ABSOLUTE.** The
`damage` column is already `round(1.25 * damage_and_assists)`, so the searched
multiplier `d` sits on top of the existing 1.25:

    A_absolute = 1.25 * d      d = 1.0 is today's incumbent

| value | what it governs |
|---|---|
| **relative grid `d`** | `0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0` |
| **absolute `A` searched** | `0, 0.3125, 0.625, 0.9375, 1.25, 1.5625, 1.875, 2.5, 3.75, 5.0` |
| **target** | `PRIMARY_T2` -- `TargetConfig(name="T2", k=3, gamma=0.7, match_weight=1.0)` |
| **controls** | `round_result`, `score_diff_before`, `attacking_is_team_a`, `loadout_diff`, `full_buy_count_diff` (T2's declared set) |
| **mode** | EX-ANTE, `use_realized_swing=False` |
| **candidate config** | `enable_econ_component=True`, `enable_postplant_leverage=True` |
| **declared inert factor columns** | `econ_impact`, `swing_impact` -- both written as 0 by the new structure |
| **outer folds** | 5, `stable_folds(..., seed=0)` -- the five-arm's own convention |
| **bootstrap** | 2,000 draws, match-clustered, two-sided 95% |
| **sign** | `loss(fitted A) - loss(A = 1.25)`, **positive = deterioration** |

**Leakage.** A is a FITTED OBJECT, like `V` and the post-plant table. It is
selected on each outer fold's TRAINING matches only, and the post-plant table
is rebuilt per fold on those same training matches. Selecting A once on the
whole corpus and scoring held-out folds with it would reinstate exactly the
leak the per-fold tables were built to remove.

Within a fold, A is selected by TRAINING loss across the grid. That is
acceptable here and not the defect finding 1 caught in the L2 selection: every
grid candidate has identical model complexity (one composite coefficient plus
the same controls), so training loss discriminates between genuinely different
predictors rather than rewarding flexibility. The selection is then judged
out-of-fold, which is the whole protocol.

**DECISION RULE, fixed now.** If the 95% interval on the contrast spans zero,
the result is **INCONCLUSIVE in those words** and **A stays at 1.25**. A tie
goes to the incumbent, and to the value already in the record -- which also
keeps any rescore attributable to the structure change alone, as econ spec 9c
wants. A is only moved if the interval excludes zero on the improvement side.

**Expected outcome, recorded so a null is not re-read as a surprise.** Damage
and `time_impact` carry `max |r| = 0.89`, above this project's own 0.70
threshold, so the ratio is weakly identified and the loss surface is expected
to be FLAT. The prior evidence on weight refitting is +0.005 AUC, measured
when the components correlated 0.73-0.90. The performance CURVE across the
grid is reported for this reason: a flat surface must read as "any A in this
band is equivalent", never as a point optimum.

### 2026-09-09 (later) -- the A search RESULT: inconclusive, A stays at 1.25

**No governing value moved, and none was tuned.** The grid, target, controls,
folds, draws, sign and decision rule are exactly as declared in the entry
above, which was committed before this ran (`7663395`).

**Verification of the three things the search rests on**, done before the
result was read:

| | |
|---|---|
| the one-live-factor reduction | holds -- every fold reports **effective B = 1.0000**; the script refuses silently and shouts if it ever does not |
| output normalization | the fitter rescales returned factor weights by `FACTOR_WEIGHT_TOTAL` (3), so a reported `w_time = 3.0` **is** an effective B of 1.0. `damage_multiplier` is returned RAW, so `A = 1.25 * d` stands |
| damage-column scaling and rounding | the column is already `round(1.25 * damage_and_assists)`. The gap between what the fit sees (`d * round(1.25x)`) and what the scorer computes (`round(1.25dx)`) is **<= 2.0 absolute, <= 0.39% of the term**, and exactly **0 at d = 1.0**, as it must be |

**The contrast, on identical held-out matches, 5 folds, 2,000 match-clustered
draws:**

    loss(fitted A) - loss(A = 1.25)  =  -0.000038  [-0.000082, +0.000004]

The interval spans zero. Per the rule declared before the run, this is
**INCONCLUSIVE in those words, and A STAYS AT 1.25.**

**The held-out performance curve** (weighted log loss; lower is better):

| d | A | mean over 5 folds | SD |
|---|---|---|---|
| 0.00 | 0.0000 | 0.674582 | 0.001789 |
| 0.25 | 0.3125 | 0.674522 | 0.001802 |
| 0.50 | 0.6250 | 0.674479 | 0.001815 |
| 0.75 | 0.9375 | 0.674451 | 0.001826 |
| **1.00** | **1.2500** | **0.674433** | 0.001834 |
| 1.25 | 1.5625 | 0.674421 | 0.001841 |
| 1.50 | 1.8750 | 0.674413 | 0.001846 |
| 2.00 | 2.5000 | 0.674403 | 0.001853 |
| 3.00 | 3.7500 | 0.674396 | 0.001860 |
| 4.00 | 5.0000 | 0.674394 | 0.001865 |

**FINDING 1 -- the curve is flat, and flatter than the thing it is measured
against.** Across the WHOLE grid, from `A = 0` (damage deleted from Impact
entirely) to `A = 5`, held-out loss moves **0.00019**. That is eight times
smaller than the econ-deletion arm (+0.00153) and smaller than the score's
entire measured edge over `kill_diff` (+0.00122). Between-fold SD (~0.0018) is
**ten times** the whole between-`A` spread. On this target damage and leverage
are near-substitutes rather than complements, which is what their measured
`r = 0.89` already implied. **A is not identified by this data.**

**FINDING 2 -- the optimum is at the grid edge and the folds disagree.**
Per-fold HELD-OUT argmin `d`: **4.0, 1.5, 4.0, 4.0, 2.0** -- three distinct
values, three of five at the boundary. It is not an interior optimum. The
grid was **not** widened after seeing this; widening a predeclared grid to
chase an edge is the move this file exists to prevent. A future search that
wants a wider grid declares it first, in advance, as its own entry.

The per-fold TRAINING-selected `d` was 4.0 in all five folds, which looks like
stability and is not: it is the same edge being hit five times. The held-out
argmin is the honest stability measure and it disagrees.

**Consequence for the weights question.** A is a **product choice, not a
fitted quantity** -- the same conclusion already reached for C, arrived at
from the opposite direction. Recorded so nobody re-runs this expecting the
data to pick a value.

**A second thing A does, worth stating before anyone changes it.** A sets the
damage-to-leverage RATIO and the overall DISPLAY SCALE at the same time. The
evaluator absorbs scale, so those are one knob statistically and two knobs to
a reader. Reviewed on the fixed ten: at `A = 1.25` the mean player-match delta
is **-4.4** with **54%** of players changing rank; at `A = 5.0` it is
**+12,044** (median **+271%**) with **32%** changing rank. The lower churn at
`A = 5` is not stability -- it is damage drowning out the other two terms.

### 2026-09-09 (later still) -- T4: the 4/2/1 target, and a PREDICTION recorded before the run

**Nothing below has been run.** Committed first, including a falsifiable
prediction about what the fit will return, so the result cannot be
rationalised after the fact in either direction.

**The target, at the project owner's direction.** A weighted mean of three
round outcomes, from the round the components were scored in:

| round | weight |
|---|---|
| **N** (the round itself) | **4/7** |
| N+1 | 2/7 |
| N+2 | 1/7 |

`y` = the availability-weighted mean of "team A won", `w` = the available
weight. Windows stop at the half boundary, as T2's do; round N is always
available.

**`round_result` LEAVES the controls.** It is the label now. Controls are
`CONTROLS_CONTEXT` only: `score_diff_before`, `attacking_is_team_a`,
`loadout_diff`, `full_buy_count_diff`.

**Three parameters, two identified.** `impact = A*damage + B*leverage +
C*econ_component`. The evaluator puts a free coefficient on the composite, so
overall scale is absorbed: **B is fixed at 1** and the search is a 2-D grid
over (A, C).

| value | |
|---|---|
| A grid (absolute) | `0, 0.3125, 0.625, 0.9375, 1.25, 1.5625, 1.875, 2.5, 3.75, 5.0` |
| C grid | `0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0` |
| incumbent | `A = 1.25, C = 1.0` |
| mode | **REALIZED** (`use_realized_swing=True`) -- required, or econ_component is identically 0 |
| folds / bootstrap / sign | 5, seed 0 / 2,000 match-clustered / `loss(fitted) - loss(incumbent)`, positive = worse |

**Decision rule, fixed now.** An interval spanning zero is INCONCLUSIVE in
those words and the incumbent `(1.25, 1.0)` stands.

## THE PREDICTION, recorded before the run

Round N at 4/7 makes it ~57% of the label, and the features were computed
FROM round N. Measured earlier today, against round N's own result:

| term | r | R^2 |
|---|---|---|
| leverage | +0.9379 | 0.880 |
| damage | +0.8473 | 0.718 |
| econ_component | +0.5469 | 0.299 |

**Predicted, and the reason to write it down: this fit WILL resolve** -- unlike
the A search, whose interval spanned zero -- **and the resolution will rank the
three terms by how directly each restates round N's outcome rather than by how
much each is worth.** Specifically:

1. The contrast will EXCLUDE zero (a "significant" result).
2. `C` will land at or near the BOTTOM of its grid (0 or 0.25).
3. `A` will move DOWN from 1.25, toward the term with the higher round-N
   correlation (leverage, whose weight is pinned at 1).
4. The held-out loss surface will be far steeper than the A search's 0.00019
   spread, because the label is now largely a restatement of the features.

If all four land, the fit is an artifact of the target and **must not be
adopted**, however tight its interval. A tautology with a confidence interval
is still a tautology.

**What would falsify the artifact reading**, and would be genuinely
interesting: `C` coming back LARGE. That cannot be explained by round-N
tautology, since econ_component is the WEAKEST of the three on round N.
Caveat already known: `econ_component` reads round N+1's loadouts and N+1 is
2/7 of this label, so a large C could instead be that leak. The two
explanations are distinguishable by re-running with the N+1 term removed;
that is not part of this run.

**Standing objection, recorded so adopting this target later is a decision and
not a drift.** The whole evaluation design excludes round N deliberately: "a
round's own kills are, near-deterministically, that round's outcome, so
scoring impact against it measures nothing." This target reverses that. It is
run because the project owner asked for it and because a recorded, checkable
prediction makes the result informative either way -- not because the
tautology argument has been withdrawn.

### 2026-09-09 (T4 RESULT) -- all four predictions landed; the fit is an artifact and is NOT adopted

**No governing value moved.** The incumbent `(A, C) = (1.25, 1.0)` stands.
The prediction this checks against was committed before the run (`5f67c60`).

**The fit resolved, crisply and stably.** 5 folds, 2,000 match-clustered
draws:

    loss(fitted) - loss(incumbent) = -0.011417 [-0.011947, -0.010903]

Selected `(A, C) = (0.3125, 0.25)` in **all five folds** -- both terms shrunk
to a quarter of their declared weight, with leverage pinned at B = 1.

**All four recorded predictions landed:**

| # | prediction | outcome |
|---|---|---|
| 1 | the contrast will exclude zero | **yes** -- and by 20 interval-widths |
| 2 | `C` at or near the bottom of its grid | **yes** -- 0.25, the second-lowest of eight, every fold |
| 3 | `A` moves down from 1.25 | **yes** -- 0.3125, the second-lowest of ten, every fold |
| 4 | surface far steeper than the A search's 0.00019 | **yes** -- **0.06627, 353x steeper** |

**Scale check, which is what makes this unadoptable.** The "improvement" is
**-0.0114**. For comparison: the A search's contrast was -0.000038, the
econ-deletion arm +0.00153, and today's Impact beats plain `kill_diff` by
**+0.00122**. So this target reports an improvement **nine times larger than
the score's entire measured advantage over kill difference** -- obtained by
reweighting toward the term that most restates the label.

**The mechanism, measured directly.** Correlation with round N's own result:

| column | r | R^2 |
|---|---|---|
| **`kill_order_bonus`** (no time factor at all) | **+0.9420** | 0.887 |
| leverage (`kill_order_bonus x time_factor`) | +0.9379 | 0.880 |
| `kill_diff`, plain | +0.8879 | 0.788 |
| `acs_diff` | +0.8544 | 0.730 |
| damage | +0.8473 | 0.718 |
| `econ_component` | +0.5469 | 0.299 |

The order the fit produces is exactly this order. The winner is the raw
hand-tuned man-advantage sum, which is close to a **description of the
round's ending state** -- a 5v0 man-advantage IS a won round. The fit did not
discover that damage and econ are overweighted; it discovered which column is
the nearest copy of the label and shrank the others toward it.

**Conclusion: NOT ADOPTED.** Per the rule recorded before the run, a fit whose
four predicted artifact signatures all land must not be adopted however tight
its interval. A tautology with a confidence interval is still a tautology.
The incumbent `(1.25, 1.0)` stands.

**What this does establish, and it is worth keeping.** The A search's null was
not a tooling failure. The identical machinery, pointed at a target that
shares variance with the features, resolves instantly and stably -- 353x the
surface curvature, five folds agreeing exactly. So the flat surface under T2
is a real property of that estimand, not a broken fit. The two runs together
are stronger evidence than either alone.

**Not re-run, and worth stating so nobody assumes it was:** the C-vs-N+1 leak
is still confounded here. `econ_component` reads round N+1 and N+1 carries 2/7
of this label, so C's collapse to 0.25 cannot be cleanly separated from that
leak by this run. It does not change the conclusion -- C collapsed rather than
inflated, which is the direction the tautology predicts and the opposite of
what the leak would produce -- but a clean test would drop the N+1 term.

### 2026-09-10 -- independent economic harm and the late floor: experiment declared before measurement

Authorization: the owner's `webapp/econ_issue_prompt.md` requests implementing
and measuring an independent debit, a late-regime walkthrough, corpus-wide
sign counts, reconsideration of the 0.5 floor, and recalculated anchors.
The late scope lock is reopened **for these experimental comparisons only**.
The production scorer, defaults, versions, persisted scores and deployment
remain unchanged. No production formula is selected by this declaration.

Four prespecified arms, with no parameter search:

| arm | credit pool | debit |
|---|---|---|
| incumbent | current early/late `econ_round` | current opponent-pool share |
| independent | unchanged | `denial_early(own next wealth, own roster size) * player_lost / 19500` |
| no_floor | early unchanged; late `0.2 * enemy_below_4200_next` | opponent-pool share using that arm's credit pool |
| independent_no_floor | same as no_floor | same independent debit |

All use the existing early/late eligibility, final-round/pistol/halftime/OT
abstention, committed-value definition and enemy-death eligibility. Self,
environmental and team deaths remain excluded to isolate allocation changes.
Independent harm needs a complete next-round **own** roster; unavailable
data gives zero harm, not imputation. The scarcity function is reused in
late rounds as a hypothesis, not a claim that its early calibration transfers.
`ZERO_AT=6300`, `R=19500`, full-buy threshold 4200 and all other existing
constants are held fixed. No commitment gate on independent harm. Saving
policy is left to the owner; report the affected cases separately. A low
mean commitment is only a proxy for a saving round, not observed intent.

Population: the complete available local corpus in one repeatable-read,
read-only snapshot, expected 3,124 matches. Report actual counts. Reuse the
production scorer's econ observer to obtain credits, debits, removed and lost
values; reconcile reconstructed incumbent values with both the observer and
the scorer's player-round outputs. All scored player-rounds, including zero
boundary rows, enter dispersion calculations. No live database writes.

Report: match 3104, every scored early and late round and player, especially
ternstyle#GIGI in rounds 14, 16 and 17; the existing fixed ten-match roster;
unrounded both-teams-negative/positive/zero/opposite/mixed sign counts by
early/late/boundary regime (`epsilon=1e-10`); unrounded total and rounding
residuals; player-round SD and p1/p5/p50/p95/p99; lost equipment with zero
debit split by own scarcity and commitment gate; late positive-removal cases
at count=0; repeated-loss exposure; and largest player-round changes.

Scales: first compare raw values and the SAME incumbent scale 1007.9209 at
C=1, to isolate formula changes. Separately compute each arm's proposed
anchor as `SD(legacy time_impact) / SD(unrounded arm econ)` over the identical
scored population. Keep the reference time calculation fixed (B=1, both
timing retunes off). Report these anchors and their effects without installing
them. Do not retune C or center the new non-zero mean away. A=1.25 and B=1
remain fixed for any illustrative total; these are econ-only comparisons,
not the complete release-candidate review.

Decision criteria: reproduce the incumbent; verify harm increases with own
equipment loss at fixed own scarcity, is zero when own scarcity is zero,
and is independent of the opponent's credit gate; verify no_floor removes
credit exactly when the late below-buy count is zero, without altering early
credit. Corpus sign frequencies and scale shifts are descriptive findings,
not win-prediction/causal validation or an optimization target. More non-zero
sums do not by themselves mean a better score. Adoption requires explaining
the concrete behavior and remaining asymmetric credit/debit assumptions to
the owner; these runs do not automatically select or activate a candidate.

### 2026-09-10 -- independent econ experiment RESULT (no adoption)

Completed the above declaration on 3,124 matches / 659,290 player-rounds in
a repeatable-read, read-only local snapshot. The incumbent matches its econ
observer and scorer rows exactly. Maximum unrounded round net: 4.44e-16;
rounded match sums reproduce mean -0.2692 and SD 3.1217.

The independent debit fixes the requested missing-debit behavior, but with
the floor retained it makes both teams net-positive in 26,414 / 34,142 late
rounds (77.36%). With the floor removed this is 4,376 (12.82%); both-negative
late rounds become possible, 130 (0.38%). Early results for either independent
arm: 1,132 / 18,118 both-negative (6.25%) and 277 both-positive (1.53%).
Floor removal alone remains zero-sum, as expected.

At the existing scale, match 3104's ternstyle#GIGI econ changes +11 -> -11 in
R14, stays 0 in R16, and changes -101 -> 0 in R17 under the combined arm.
In R5 both teams fully re-arm but retain positive modeled scarcity; the
combined arm produces TEAM_1 -195 and TEAM_2 -132, instead of the incumbent's
rounding-only net totals. The saving-team question remains a product choice.

Reported proposed anchors (NOT installed): incumbent 899.8970, independent
1005.3451, no_floor 1549.8498, independent_no_floor 1672.6149. The current
incumbent raw SD is 0.198992, so the old 1007.9209 anchor no longer reproduces
its historical SD match; reference time SD still reproduces 179.0723. This
entry records the discrepancy without overwriting the historical value.

Recommendation: review independent_no_floor further, but do not call its
non-zero sums proof of better attribution. Small removals can still allocate
an entire positive credit pool, late harm reuses the early scarcity curve,
and 4,952 eligible player-rounds contain repeated counted equipment losses.
No production defaults, versions, weights or scores changed. Behavioral
verification: 44 tests pass; reinstating the old allocator behind the new API
produces 15 numerical failures (four shared behaviors continue to pass).

Full record: `docs/superpowers/econ-counterfactual-results/conclusions.md`,
`walkthrough.md` and `measurements.json` in the same directory.

### 2026-09-10 -- buy-disruption specification and Abyss worked example: declared before calculation

The owner's new direction is a small equipment-loss reward plus a larger
reward when losses impair the next buy, shared across all contributing kills.
The requested worked game is match 3104 (Abyss, TEAM_1 11 - TEAM_2 13), the
game discussed in the econ issue prompt. This is a worked example, not a
weight fit, causal estimate, corpus test or activation decision.

Fixed review values: paid-loadout target 3900 per player, R=19500,
background coefficient 0.10, buy-disruption coefficient 1.00, review scale
1007.9209, external C=1. Independent debit retains the existing own-wealth
scarcity curve (ZERO_AT=6300, ceiling 1.5), with the 0.10 background added.
These are explicit review policies, not learned or optimized constants.

For round 2/14 on the pistol-winning team, target_i is
min(3900,max(1000,paid_loadout_i_this_round)); other eligible teams/rounds
target 3900/player. This preserves the post-pistol carryover-buy target even
if that team loses round 2. Rounds 1/13, 12/24, OT and the last played round
remain zero. Early history is reported, not another score multiplier.

Team funding proxy U = sum(min(next_paid_loadout_i,target_i)) + sum(next_bank_i).
Shortfall D = max(0,sum(target_i)-U). Recorded equipment exposure L counts
at most the first observed death's starting paid loadout per player. Restorable
shortfall Q=min(D,L). This is an optimistic pooled-resource / replacement-spend
restoration proxy, not an observed counterfactual or exact weapon-loss value.

Every valid enemy kill with first-loss exposure v receives raw credit
0.10*v/R + (Q/R)*(v/L), with the second term zero if L=0. Q is for the
VICTIM team and L includes all its first-loss exposure, including self/team/
environmental deaths; those do not create enemy credit. A player's raw debit
is (0.10+own_scarcity)*own_first_loss/R. This explicitly extends independent
harm to non-enemy deaths and avoids counting the same starting kit repeatedly.
Subsequent deaths retain their combat scoring but do not invent another kit.

Own scarcity uses sum(next_paid_loadout+next_bank), without the target cap,
and the existing denial_early function. This intentionally distinguishes
remaining wealth from the stricter funding proxy; the approximation and
possible divergent cases must be exposed in the review, not silently merged.

Compute unrounded event and player values; round only each final player-round
net. Report every eligible round/team in match 3104, the named R14/R16/R17
cases, and highest/lowest positive enemy-event credits (ties ordered by
round,time,id). Include actual next loadouts/banks, both target/funding values,
loss exposure, shortfall, restored-shortfall estimate and separate credit/debit
parts. No post-hoc parameter changes to make an example look better. A failed
behavior or an ambiguity is reported and any revision separately declared.

Validation criteria: no large credit if pooled funding covers the target;
large credit never exceeds the restored equipment value on this scale; no
credit without an economically eligible enemy kill; all contributing kills
share the same victim-team bonus per credit of loss, independent of order;
no duplicate starting-kit charges; scarce/wealthy cases and surviving
teammate funding are explicitly examined. Any violation fails the calculator.
Pistol-winner round-2 losses are not guaranteed to exist in this one game;
missing histories are identified rather than fabricated as match evidence.

Specification: docs/superpowers/specs/2026-09-10-econ-buy-disruption-implementation.md.

### 2026-09-10 -- Abyss development review: V2 severity rule, before recalculation

The first calculation completed with 163 reconciliation checks. It flags six
team-rounds. R22 TEAM_2 exposes a mismatch with the owner's definition:
target=19500, capped equipment plus bank=17750 (D=1750), but next loadouts are
600/2150/700/700/4650, four below 4200. A rule scoring only the 1750 funding
gap does not represent the larger coordinated buy downgrade the owner described.
The previously declared Q=min(D,L) remains a RESOURCE-restoration audit; it
is no longer the candidate's buy-disruption severity pool after this revision.

V2, declared before calculating its examples:

```
G = sum(max(0,target_i - next_paid_i))
activation = min(1,D/3900)
severity_pool = min(L, G*activation)
```

Use severity_pool instead of Q in the event-credit allocation. Everything
else stays as declared: targets, eligibility, first-kit exposure, background
0.10, disruption coefficient 1.00, independent debit, scale and C. The gate's
3900 is ONE reference full kit, chosen as a product unit, not fitted. It
avoids a hard jump for a tiny funding gap; a gap of a full kit activates the
whole observed downgrade, capped by the recorded losses. Funding adequate
for the target still means activation=0 even if the observed buy is weak.

This is a DEVELOPMENT revision prompted by a reviewed failure case, not an
independent validation on Abyss. Preserve the first calculation and report
both rules. No target outcomes or loss curves selected this formula. The
severity pool is a score index in equipment-value units, not a claim that
restoring those many credits is necessary or that every cheap buy was caused
by a kill. Round 22 cannot be reused as independent evidence for the revision.

### 2026-09-10 -- V2 Abyss measured results (after the preceding declaration)

Completed all 20 eligible rounds / 40 team-rounds from the same frozen source
snapshot as V1. Six team-rounds have a positive funding gap and loss exposure:
TEAM_1 rounds 6, 7, 16, 20, 21 and TEAM_2 round 22. All 163 match arithmetic
checks pass. Seventeen standalone reference tests pass; restoring V1's pool
behind the same API produces two numerical failures and fifteen passes.
Twenty-eight independent decimal comparisons reproduce the manually worked
R2/R3/R6/R22 inputs, team quantities and selected credit/debit values.

At the unchanged review scale and C=1: R6 Osmin -> 1xgoofy earns 124.040530;
R3 Mokalover -> Osmin earns 19.899977; R22 ZETA -> DoubleBl1nd earns 108.561606;
R2 Osmin -> 1xgoofy earns 1.550648. These are event ECON credits, not full Impact.
R22 next-equipment gap is 11950 and V2 severity pool is 5362.179487; V1 retained
only the 1750 funding-gap pool. This development revision is not held-out proof.

Material unresolved finding: all ten match-total economy nets are negative,
from -100 to -928. The independent own-wealth debit charges reserve depletion
more often/strongly than the constrained-buy reward activates. Non-zero-sum
does not justify this magnitude by itself. Review an own-buy-disruption-focused
debit before choosing the production rule or adjusting C; no such additional
arm or numerical rule is declared or measured here.

The selected match contains no pistol winner losing round 2/14. Pooled
resources and starting paid kits remain proxies, not exact transferable buys
or observed guns lost. Detailed findings: docs/superpowers/abyss-buy-disruption-review/manual-review.md.
No production scorer activation, database rescore, version change or deployment
was performed as part of this work; unrelated concurrent working-tree edits
are outside this experiment.

### 2026-09-10 -- owner-directed 30% / 80% death penalties, before calculation

Use the frozen Abyss 3104 V2 source and killer credits, C=1 and scale=1007.9209.
Replace the own-wealth scarcity debit for this comparison. Interpret the
owner's percentages relative to the death's modeled economic damage value,
not as multipliers on the retired scarcity debit or on the raw kit price:

```
background = 0.10 * own_first_loss / 19500
disruption = severity_pool / 19500 * own_first_loss / team_first_loss
rate = 0.80 if severity_pool > 0 else 0.30
own_debit = rate * (background + disruption)
```

The severity pool already requires a funding gap and observed equipment
downgrade. A positive pool is a proxy for buy disruption, not causal proof.
Self/team/environmental losses receive the same own debit without invented
enemy credit. Repeated deaths retain zero additional starting-kit exposure.
All other eligibility and target rules remain fixed. Round the final net
player-round econ once; sum those rounded nets for match totals, and divide
by all 24 played rounds for the average match contribution.

Report gross credit, revised debit, net, prior V2 net, net change, per-round
average, team and match sums, plus the prior worked deaths. This is a
read-only reference comparison; production scoring is outside this change.

### 2026-09-10 -- 30%/80% Abyss measured results

Same frozen source and killer credits as V2. All 199 artifact reconciliation
checks pass; 22 reference tests pass. Reinstating the old own-wealth debit
behind the new API produces four numerical failures and eighteen passes.

Sum of rounded player-round economy nets: TEAM_1 +502 (previously -4057),
TEAM_2 +1293 (previously -2537), match +1795 (previously -6594). Nine players
are positive; 1xgoofy remains negative at -101. Osmin leads at +708, or +29.50
per played round. Both full match sums and 24-round averages are reported.

Worked own death costs: R3 Osmin 5.969993 (30% of 19.899977); R6 1xgoofy
99.232424 (80% of 124.040530); R22 DoubleBl1nd 86.849285 (80% of 108.561606).
Positive combined enemy-event balance follows from 100% credit versus 30%/80%
debit and is not evidence of predictive improvement. The positive-severity
switch creates a rate step on the small background term even for a tiny gap.
No constants were adjusted after measurement; production remains untouched.

Full report: docs/superpowers/abyss-buy-disruption-review/death-penalty-30-80.md.

### 2026-09-10 -- owner clarification: signed economy, no minimum offset

The owner explicitly confirmed that econ may be negative and must not be
bounded or shifted to make the worst player zero. Preserve the signed formula
result in scoring, persistence, totals, averages and displayed values. Neither
the +928 previous-formula table nor the +101 new-formula table is an adopted
normalization. The frozen 30%/80% Abyss totals therefore remain unchanged,
including 1xgoofy -101. Formula resource/severity caps remain unchanged.

The implementation plan now includes the four plan-review corrections: input
routing before legacy preprocessing, an offline local backfill with recovery,
explicit comparison-model identities, and a complete configuration frozen
before integrated review. No new measurement, runtime activation or scoring
code change is part of these documentation corrections.

### 2026-09-11 -- buy-disruption 30%/80% integrated review: frozen candidate, samples and checks, declared before scoring

**Nothing below has been scored.** This entry is committed together with the
frozen manifest and before any integrated review run, so the configuration,
samples and checks are checkable against the timestamp of the results.

**Frozen candidate.** `docs/superpowers/econ-buy-disruption-candidate/candidate-manifest.json`,
candidate `impact-buy-disruption-30-80-rc1`, LF-SHA-256 `2cca86489aef31d5851350bb9e6928839df5e7a511b8845ec08f053b3a74e19f`,
scorer revision `61737f79c3277e1b9bb651d2d5405bee2e92cd18`. Review, the maintenance-window backfill and (on
activation only) ingestion load this manifest and refuse on any difference in
constants, allowances, comparators, timing flags, scoring-source digests or
reviewed source rows.

| value | governs |
|---|---|
| release comparator | `buy_disruption_v2_30_80`: `enable_econ_component=True`, realized mode |
| weights | A(damage)=1.25, B(leverage)=1.0, C(econ)=1.0 -- unchanged, not refit |
| ECON_SCALE | 1007.9209 -- unchanged, not re-anchored |
| calculator | background 0.10, disruption 1.00, R 19500, target kit 3900, activation gap 3900, carryover floor 1000, absorbed 30%, disrupted 80% (binary, not smoothed) |
| timing | post-plant table OFF and pre-plant curve OFF in EVERY comparator; the unfrozen review tool's implicit corpus-fitted post-plant table is not used |
| trade discount | unchanged: the declared cost schedule and 6s window |
| activation version | IMPACT_CALCULATION_VERSION 3; the live code stays at 2 and inactive |

**Comparators.** `live_legacy` (replay; persisted site values reported
separately), `separate_econ_legacy` (diagnostic only), `buy_disruption_v2_wealth`,
`buy_disruption_v2_30_80`. The penalty-only comparison is wealth vs 30/80; the
site comparison is live_legacy vs 30/80 and is labelled the TOTAL release change.

**Samples, fixed now.**

- Abyss 3104 -- the development match. A parity check, not validation.
- The pinned ten: 3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117.
  Read from outcomes only: 3116 contains a pistol winner losing round 14; none
  contains a pistol winner losing round 2.
- Match 3120 -- a separately labelled round-2 history, selected from round
  outcomes only, before scoring: the most recent match outside the ten and 3104
  whose round-1 pistol winner lost round 2 with a scoreable round 3 (501
  first-half histories qualified). Not a favourable-score selection.

**Integrated checks (any failure is an implementation finding that blocks
approval of this manifest; nothing is tuned to pass).**

1. Every reviewed match's source rows equal their frozen fingerprint.
2. Zero reconciliation errors: impact == A*damage + B*leverage + C*econ per
   row; econ == round(C * ECON_SCALE * raw calculator net) per row; each team's
   credit equals its enemy events; each team's debit equals its victims' event
   debits; trace totals equal the match view.
3. Penalty comparison: gross kill credits and every non-econ field identical per
   player-round.
4. Abyss parity through the build path is already pinned by tests (all 200
   player-round nets and 152 events, both artifacts).

**Read-only corpus audit** (all local matches, one repeatable-read snapshot).
Reported, not optimized: matches scored and input-validation failures
(expected 0: the corpus has no null/unknown killers or victims and no missing
stats); econ abstention reasons; event kinds; team-rounds with losses by 30%/80%
rate; scored-round team net signs (both positive / both negative / mixed / zero,
epsilon 1e-10); identity mismatch counts (expected 0); player-round and
player-match C*econ distributions including zero rows (mean, SD, p1/p5/p50/p95/p99,
min, max, sign counts); full-Impact change vs the live_legacy replay per
player-round and per player-match, with the ten largest increases and decreases;
within-match rank changes; persisted-vs-replay differing player-matches
(descriptive -- stored rows predate version 2); leaderboard movement for players
with at least 20 matches by average Impact per played round (Spearman, top-20
overlap, 15 largest rank moves).

**Interpretation rules, fixed now.** Positive aggregate econ is inherent to 100%
credit against 30%/80% debit; it is neither a defect nor evidence of improvement.
No weight, scale, constant or sample is changed from these results. A surprising
distribution is reported to the owner, not corrected. The owner reviews the
concrete match reports before any activation; this entry activates nothing.

### 2026-09-11 -- buy-disruption 30%/80% integrated review RESULT (no activation)

Run exactly as declared above, under manifest `2cca8648...e19f` (candidate
`impact-buy-disruption-30-80-rc1`, scorer `61737f7`). Every run verified the
manifest and the frozen source fingerprints of all 12 reviewed matches first.
**No governing value moved; nothing was fitted, re-anchored or activated.**

Integrated checks: zero reconciliation errors -- Abyss site 570, penalty 5,195,
trace 580; ten site 5,140, penalty 46,877; 3120 match+trace 958; 3116 trace 620.
Through the integrated scorer Abyss reproduces the frozen 30/80 totals exactly
(Osmin +708 ... 1xgoofy -101; TEAM_1 +502, TEAM_2 +1293) and the wealth
comparator's (-100 ... -928; -4057 / -2537), with identical gross credits.

Fixed ten, live_legacy -> 30/80: mean full-Impact change +157.7 per
player-match (SD 234.9, -280..+899), 25/100 within-match rank changes, C*econ
per player-match mean +152.9 (23 negative); 296 absorbed vs 37 constrained
team-rounds with losses. Histories: 3116 R14 (pistol winner loses, carryover
H=15,800, D=1,000, 80%) and separately selected 3120 R2 (carryover H=16,350,
D=100, severity pool 258.97 -- the binary step moves all five debits to 80%).

Corpus (3,124 matches, 659,290 player-rounds): 0 validation failures, 0
identity mismatches; 13,817 of 100,011 team-rounds with losses at 80%; scored
round signs 28,737 both positive, 23,370 mixed, 0 both negative; player-round
C*econ mean +7.08, SD 35.47; player-match mean +149.4 (19.8% negative);
full-Impact change per player-match mean +163.3 (SD 222.4); 19.0% within-match
rank changes; leaderboard (104 players >= 20 matches) Spearman 0.998, top-20
overlap 20/20.

Reported for the owner, not corrected: the 30%/80% step fires on tiny funding
gaps in real histories; aggregate econ is positive by construction; the
candidate's econ SD (35.5) is far below the 179 time_impact SD ECON_SCALE was
anchored to, so any scale or C change is a separate decision; the new
structure's kill/death impact (and so Round Win Impact) excludes econ; a
pre-existing legacy team-kill state bug (122 events) is unchanged.

Full record: docs/superpowers/econ-buy-disruption-candidate/SUMMARY.md.

### 2026-09-11 (later) -- external review of the candidate's code; eleven fixes and a REFREEZE

An external reviewer (Astra) reviewed the implementation read-only and raised
eleven findings. All eleven were verified against the source and **all were
correct**; none touches the owner's locked values (30%/80%, C=1, A/B, ECON_SCALE,
signed econ), and none changed a score. The measured results in the preceding
RESULT entry therefore still stand; they were re-run under the new manifest and
reproduced exactly (see below).

Fixed, each with a test that failed on a VALUE before the fix:

| # | Defect | Fix |
|---|---|---|
| 1 | A refused backfill still deleted cache rows and committed | caches are cleared only once the run has actually started |
| 2 | A match that returned cleanly but left stale rows stayed in `succeeded`, so the documented rerun skipped it forever | failing matches are requeued out of `succeeded` at acceptance |
| 3 | Acceptance never rechecked the database's match set, so a match ingested and disconnected between session polls went unnoticed | the declared set is re-read and compared at acceptance |
| 4 | Acceptance compared 6 of the 19 persisted fields | it compares `impact.PERSISTED_FIELDS` |
| 5 | Corpus-audit identity or validation failures did not affect the exit status | they are registered with the reconciler |
| 6 | `--weights` mutated the verified manifest but kept the frozen hash and "C=1" text in artifacts | the identity is restamped `WEIGHTS-OVERRIDE ... NOT THE FROZEN CANDIDATE`, and `--results` is refused with it |
| 7 | The runbook's post-ingestion check named a command that cannot work, since a new match has no frozen fingerprint | replaced with a replay-vs-persisted comparison |
| 8 | The manifest hashed no ORM/model definitions, so a mapped column or the Team enum could change inputs invisibly | `app/models/{match,round,kill_event,impact_score}.py` are hashed |
| 9 | The runtime cached the verified manifest by path forever, so a file edited afterwards kept scoring | the cache is keyed on the file's content hash |
| 10 | For self/environmental deaths the kill observer reported the OPPONENT's alive count as `victim_team_alive` | it reports the victim's own side (reporting only; no score changed) |
| 11 | Version masking covered chained assignments, so `IMPACT_CALCULATION_VERSION = RATE = 2` could hide a behavioral change | only a lone `Name` target is masked |

Findings 8 and 10 change hashed scoring sources, so the candidate was
**refrozen** as `impact-buy-disruption-30-80-rc2`, manifest LF-SHA-256
`ae043e361e3398ee578e82e9a393e63b8977d8a9ef4cad3894d357d5c8ebdae6`, scorer revision `bba279bc4f4f771f04809f82493edee9247a57bf`, and every review was re-run
under it. The declared samples, constants, weights, comparators, checks and
interpretation rules are unchanged from the 2026-09-11 declaration above.

**Re-run under rc2, compared against the rc1 numbers:** identical. Abyss 3104
again gives Osmin +708 ... 1xgoofy -101, TEAM_1 +502, TEAM_2 +1293 and the
wealth column -100 ... -928; the fixed ten again give mean full-Impact change
+157.7 and 25/100 rank changes; the corpus audit again reports 0 validation
failures, 0 identity mismatches, 13,817 constrained team-rounds and Spearman
0.998. The only differences in the artifacts are the manifest hash and
candidate id. The corpus audit JSON is byte-identical to the rc1 run; the reports differ only in that identity line, plus self/environmental trace rows now showing the victim own side (finding 10) and review-results.json now carrying all 19 persisted fields (finding 4).

Defect reinstatement now covers 47 defects, including one per fix above; all 47
are detected. No production defaults changed: ACTIVE_MANIFEST stays None and
IMPACT_CALCULATION_VERSION stays 2.

### 2026-09-12 -- round 2/14 bonus-round denial: frozen candidate, samples and checks, declared before scoring

**Nothing below has been scored.** This entry is committed together with the
frozen manifest and before any scored run, so the configuration, samples and
checks are checkable against the timestamp of the results.

**Frozen candidate.** `docs/superpowers/econ-bonus-denial-candidate/candidate-manifest.json`,
candidate `impact-bonus-denial-rc1`, LF-SHA-256 `2a5d247c8471b7489a2ea718d9fff3597c81b7850f7350cb000c891926798172`, scorer revision
`c7cbfd7f137692e7c453c6367af843109ecda7b0`. Spec `docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md`;
plan `docs/superpowers/plans/2026-09-12-econ-bonus-round-denial.md`.

| value | governs |
|---|---|
| release comparator | `buy_disruption_v2_30_80_bonus_denial`: identical to `buy_disruption_v2_30_80` outside half-round 2 and for victims on the pistol-losing team |
| qualifying death | a pistol winner's first death in round 2/14 with `paid - agent utility cost > 1500` (strict) |
| value | killer credit `V * 1.10 * net_denied / 19500`, V = 0.8 if the pistol winner won the round, 1.0 if lost; victim debit 0.80 of that; replaces background and disruption; self/team/environmental deaths debit only |
| non-qualifying death | background `0.10 * paid / 19500` credit, 30% of it as debit |
| recovery | per survivor `max(credit, feed)`, never summed. Credit: `surplus - utility` when `surplus > utility`, with `surplus = (next bank + next paid) - (min(9000, bank + 200*kills + plant + actual next-round reward) + paid)`. Feed: the upgrade `price(gun) - price(own)`, in round after the teammate's death, or carried into N+1 when the survivor's spend could not have bought it |
| allocation | `net_denied_i = denied_i * max(0, 1 - team_recovered / sum(denied))`; player nets stay signed |
| prices and names | spec section 6 (19 priced weapons; 34 non-purchasable names; `Weapon`/`Unknown`/`Primary` unidentified -> no feed evidence, flagged) |
| abstentions | `unknown_agent_utility`, `missing_bonus_inputs`, `unrecognised_weapon`, `kill_feed_incomplete`; audit_version 2 on every bonus-model result |
| weights | A(damage)=1.25, B(leverage)=1.0, C(econ)=1.0 -- rc2's, unchanged, not refit |
| ECON_SCALE, timing, trades | unchanged from rc2; timing candidates OFF in every comparator |
| activation | none. `ACTIVE_MANIFEST` stays None and `IMPACT_CALCULATION_VERSION` stays 2 |

**Comparators.** `buy_disruption_v2_30_80` (the current candidate) vs
`buy_disruption_v2_30_80_bonus_denial` (new); `live_legacy` for the site reviews.

**Samples, fixed now.** Abyss 3104 (development match; parity only), the pinned ten
(3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117), match 3120 (a
pistol winner losing round 2, selected from outcomes only on 2026-09-11), and the
whole local corpus (3,124 matches).

**Checks (any failure blocks approval; nothing is tuned to pass).**

1. Every reviewed match's source rows equal their frozen fingerprint (now including kill weapons).
2. `scripts/compare_econ_models.py` over the corpus: parity mismatches == 0 and input-validation failures == 0.
3. rc2 parity demonstration: a scratch manifest at this revision with `release_comparator=buy_disruption_v2_30_80`
   produces a `--corpus` audit equal to rc2's `corpus-audit.json` on every key except `snapshot`.
4. Site reviews under the frozen manifest (3104, the ten, 3120 with trace) reconcile with zero failures.
5. Defect reinstatement: all 14 declared mutations (plan Task 11 step 4) apply and are detected.

**Reported, not optimized.** Gross econ points per scored round by round number under both models;
bonus evidence counts (qualifying deaths, denied and net denied credits, survivors with credit / in-round
feed / carried feed / both evidence, unidentified-weapon flags); half-round-2 abstentions by reason;
player-round econ change and player-match Impact change quantiles; within-match rank changes; leaderboard
(>= 20 matches, average Impact per played round) Spearman, top-20 overlap and largest moves.

**Problem flags, fixed now.** (a) mean gross econ per round over rounds 2 and 14 above twice the mean over
rounds 3, 4, 15 and 16 under the bonus model; (b) any half-round-2 abstention reason above 1% of rounds 2/14;
(c) any parity or validation failure. A flag is reported to the owner, not corrected.

**Deviation from the spec's "reviews use the README commands".** The release-review `--corpus` audit is not
run under this manifest: its wealth-vs-release gross-credit identity holds only for 30/80 by construction and
would count every changed round-2/14 kill as a mismatch. The comparison script's victim-side parity replaces
it, and check 3 runs the unchanged audit for 30/80 itself.

**Disclosure.** The sizing (~1,190 pts per round 2/14 from this term vs ~305 today), the pickup calibration
(credit rule flags 12.4% of kill-feed-inferred pickups and 2.6% of kept-own-gun survivors), the round-number
profile and 8 raw tracker captures were all seen during design. They are development evidence, not an
untouched validation set.

**Interpretation rules.** No constant, price, weight, scale or sample is changed from these results. This
entry activates nothing.

### 2026-09-12 (RESULT) -- round 2/14 bonus-round denial: every declared check passes; no flag raised; not activated

Reviewed under the entry above: manifest LF-SHA-256 `2a5d247c...6798172`, scorer revision `c7cbfd7`. Nothing was
tuned, refit or re-sampled after scoring.

1. Source fingerprints match for 3104, the ten and 3120.
2. `compare_econ_models.py`: 3,124/3,124 matches, parity mismatches 0, input failures 0.
3. rc2 parity: the unchanged 30/80 corpus audit at `c7cbfd7` is identical to rc2's on all 18 keys but `snapshot`.
4. Site reviews: 570 (3104), 5,140 (the ten), 958 (3120 + trace) reconciliation checks, all pass.
5. Defect reinstatement: 16 of 16 mutations applied and detected.

Reported (C = 1): gross econ per round, rounds 2/14 137.2 -> 437.5; rounds 3/4/15/16 310.5 under both; every
other round identical. 10,181 qualifying deaths; recovery netted 2.33% of denied credits; survivors with
credit evidence 807, in-round feed 92, carried feed
198, both 69 of 16,536. Player-match Impact change
p1 -195, p50 0, p99 307; within-match rank changes 2,556/31,240;
leaderboard Spearman 0.9997, top-20 overlap 20/20.

Flags: (a) 437.5 vs 2 x 310.5 -- not raised; (b) worst half-round-2 abstention
0.84% (existing final_round/surrender guards only; no new bonus reason fired) -- not raised;
(c) not raised. Details: `docs/superpowers/econ-bonus-denial-candidate/SUMMARY.md`. Activation remains the
owner's decision and would need its own refreeze if the weights change.

### 2026-09-12 (amendment) -- round 2/14 bonus-round denial rc2: code-review fixes and two owner rules, declared before scoring

**Nothing below has been scored under rc2.** rc1's recorded values above stay as recorded; this entry
supersedes them only for the rc2 candidate.

**Why rc2 exists.** A code review of `f28afe5..6e67c4d` found seven defects, all verified against source or data
before fixing:

| # | defect | fix (commit) |
|---|---|---|
| F1 | a sidearm kill followed by a kill with the survivor's own gun was inferred as picking up a dead teammate's gun | owner rule: an in-round pickup needs the survivor's paid kit to be below the picked-up gun's price plus every distinct gun they had already fired (`9c2cf87`) |
| F2 | a survivor of a lost round was credited the team loss bonus as cash | owner rule: a survivor of a lost round always banks 1,000 (`90d9cd1`) |
| F3 | traces explained a pistol winner's round 2/14 deaths with the 30/80 budget and never showed the bonus audit | bonus audit rendered; qualifying events labelled "denial" (`bfd9d2b`) |
| F4 | `compare_econ_models.py` counted abstaining rounds as scored, diluting per-round averages and rc1's flag (a) | only scored rounds are averaged; abstentions reported per round (`2e9c052`) |
| F5 | a bonus manifest described only the 30/80 formula | the bonus formula line is added for a bonus release (`58b3646`) |
| F6 | no README runbook or `review-results.json` for activation | both produced for rc2 (this review) |
| F7 | a null `spentCredits` would abort an ingest | invalid spend writes no row (`0ce78fe`) |

**Owner rule F2, verified before adoption.** On the 8 raw tracker captures, team-summed start credits
(`remaining + spentCredits`, where teammate drops cancel) give:
- winners: exactly 15,000 in 126 of 127 team-rounds;
- losers with no survivors: exactly the streak bonus in 138 of 140.

Of the 10 lost team-rounds with a stat-survivor, 4 matched 1,000 per survivor and 6 matched the loss bonus. In
every one of the 4 defender cases that matched the loss bonus, the "survivor" appears in the kill feed as a
**"Bomb"** victim: killed by the detonation, which tracker's deaths stat omits. Counted as deaths, every true
survivor banked 1,000. The calculator already reads survival from kill events, not the stat.

**Frozen candidate.** `docs/superpowers/econ-bonus-denial-candidate-rc2/candidate-manifest.json`, candidate
`impact-bonus-denial-rc2`, LF-SHA-256 `85b873cd19803c831c20930f3f3c84a8f6f6f3ff6197edb464f0182fd4156f68`, scorer revision `90d9cd12dd9b31fce154a7f6d601ee78b389d3e3`. Release comparator
`buy_disruption_v2_30_80_bonus_denial`; weights A=1.25, B=1, C=1; every other value as in the rc1 declaration,
plus `SURVIVED_LOSS_REWARD = 1000` and the loadout-coverage condition (spec sections 4.1 and 4.2, amended).

**Activation note.** At this revision the 30/80 rc2 manifest (`econ-buy-disruption-candidate/`) and the bonus rc1
manifest no longer verify, as intended: their hashed sources changed. Activating either requires a checkout of
its own scorer revision (`bba279b` for 30/80 rc2, `c7cbfd7` for bonus rc1). Their folders are not edited.

**Samples, checks and reports: unchanged from the rc1 declaration**, with three additions:
- Check 5 (defect reinstatement) now covers the 16 rc1 mutations plus 8 for the fixes (B17-B24).
- `release_candidate_review.py --ten --extra 3104 --extra 3120 --results review-results.json` is produced for
  backfill acceptance, with a `README.md` runbook in the rc2 folder.
- Flag (a) is evaluated on the corrected comparison, which averages only scored rounds.

**Problem flags and interpretation rules: unchanged.** Nothing is tuned from rc2's results; this entry activates nothing.

### 2026-09-12 (RESULT, rc2) -- round 2/14 bonus-round denial rc2: declared checks pass; flags none raised; not activated

Reviewed under the rc2 amendment above: manifest LF-SHA-256 `85b873cd...4156f68`, scorer revision `90d9cd1`.
Nothing was tuned, refit or re-sampled after scoring.

1. Source fingerprints match for 3104, the ten and 3120.
2. `compare_econ_models.py`: 3,124/3,124 matches, parity mismatches 0, input failures 0.
3. The unchanged 30/80 corpus audit at `90d9cd1` is identical to the 30/80 candidate's on all 18 keys but `snapshot`.
4. Site reviews: 570 (3104), 5,140 (the ten), 958 (3120 + trace) reconciliation checks, all pass; `review-results.json`
   written for 12 matches.
5. Defect reinstatement: 24 of 24 applied mutations detected (24 declared).

Reported (C = 1, scored rounds only): rounds 2/14 gross 138.7 -> 442.5 per round, 1.40x rounds 3/4/15/16
(316.1); rc1 recorded 437.5 on diluted averages. Qualifying deaths 10,181; recovery netted 2.28%
(rc1 2.33%); survivors with credit evidence 816, in-round feed 64
(rc1 92), carried feed 199. Player-match Impact change p1 -195,
p99 307; leaderboard Spearman 0.9997, top-20 overlap 20/20.

Flags: (a) 442.5 vs 2 x 316.1 -- not raised; (b) worst 0.84%, new reasons fired
none -- not raised; (c) not raised. Details:
`docs/superpowers/econ-bonus-denial-candidate-rc2/SUMMARY.md`. Activation remains the owner's decision (runbook: that
folder's README).

### 2026-09-12 -- owner locks the four-term weights: A=1, D=5, B=4.25, C=3.875

**Owner decision, recorded as given ("put D to 5 flat. lets make B 4.25 and C be 3.875. just for simplicity. lets
lock those numbers in").** These replace the analysis set A=1 / D=4.993 / B=4.292 / C=3.871 as the owner's chosen
weights for the four-term score:

```
impact = A*damage + D*assists + B*leverage + C*econ
```

| term | weight | meaning at the weight |
|---|---:|---|
| A, damage (combat score less kill points, assists carved out) | 1 | unchanged |
| D, assists (25 per assist, carved out of the damage column) | 5 | 125 points per assist |
| B, leverage (kill-order edge x time factor) | 4.25 | 2v2 kill 850, 3v3 kill 765, 4v4 kill 722.5, 5v5 first blood 637.5 (time factor 1.0) |
| C, econ (buy disruption incl. the round 2/14 denial) | 3.875 | max full-kit denial 221.7426 x 3.875 = 859.3, still about one 2v2 kill |

**Measured shares at the locked weights**, bonus-denial rc2 code (scorer revision `90d9cd1`), from the saved
per-player-match terms of all 3,124 matches, rescaled from the previous analysis weights (read-only):
- corpus share: damage 37.5%, assists 8.6%, leverage 44.4%, econ 9.5%;
- median player-match share (18+ rounds): damage 40.6%, assists 8.9%, leverage 39.8%, econ 8.4%.

The previous analysis weights gave 37.3 / 8.6 / 44.7 / 9.5 and 40.4 / 8.9 / 40.0 / 8.3. The rounding moves no share
by more than 0.3 points.

**Context the owner gave before locking.** Leverage outliers (for example 25/10/0 with 16,104 leverage, or rank 6->1
from leverage) are "super impactful ... something i want to showcase more than damage". The owner agrees with
the econ outliers, but "realistically ... this needs to be toned down". **No econ toning change is made by this
entry**; that remains open.

**What this entry does NOT do.**
- It changes no code or frozen manifest. The frozen candidate `impact-bonus-denial-rc2` still scores
  A(damage)=1.25 / B=1.0 / C=1.0 with no D term (damage includes assists). `ACTIVE_MANIFEST` stays None;
  `IMPACT_CALCULATION_VERSION` stays 2.
- **Adopting these weights in scoring is a separate change:**
  1. a D term in the scorer (a persisted or derived assists term);
  2. `FormulaWeights` / manifest comparators carrying A, D, B, C;
  3. the `IMPACT_CALCULATION_VERSION` bump at activation;
  4. a new frozen candidate with its own declaration before scoring;
  5. the full review re-run.
- The 25-per-assist carve-out has been checked on Abyss only (0 of 240 player-rounds negative), not corpus-wide.

### 2026-09-13 -- owner re-anchors B and C, and fixes the ACS -> damage decomposition

**Supersedes two of the locked weights above.** ~~B = 4.25~~ -> **B = 3.0**; ~~C = 3.875~~ -> **C = 2.347**. A = 1 and
D = 5 (125 per assist) are unchanged. Each weight is now set from an anchor the owner stated in damage units.

| term | weight | owner's anchor | what it means at the weight |
|---|---:|---|---|
| B, leverage | 3.0 | a 1v1 kill at time factor 1.00 = 750 (the 1v1 edge is 250) | 5v5 first blood 450, 4v4 510, 3v3 540, 2v2 600, 1v1 750; a 4v1 cleanup 150 |
| C, econ | 2.347 | a full-kit denial kill = 3 full enemy damages = 450 | match 1824 R17's anchor kill 743.083 -> 450 (x 0.605585, C = 2.346642 rounded); per-kill ceiling 859 -> 520 |

B is the opening kill in damage units: the 5v5 edge is exactly 150 and its median time factor is 1.00, so B = 3 means
first blood is worth three full enemy damages.

**Econ stays state-blind, on purpose** ("econ should ignore game state that's the point"). Scaling econ by the kill's
edge is rejected and is not an open option.

**ACS -> damage decomposition (owner's model, recorded as given):**

```
ACS = damage + 25 per assist + kill bonus (150 - 20 * (5 - alive)) + 50 per kill after the first
```

Everything except damage is stripped. Two defects in `build_impact_rows_for_match` are fixed to match it:
1. **Multikill sign.** The scorer computed `acs - (-50 * kills)` for multikill rounds, which ADDED 50 per kill (+250 on
   an ace) instead of removing Valorant's bonus. Now `acs -= 50 * (scoring_kills - 1)`.
2. **Team kills.** A team kill earns no combat score, so no kill bonus is backed out of it and it does not count toward
   the multikill total (`scores_a_kill`).

This is a defect fix to match a stated model, not a tuned value, and it was measured before this entry was written
(2026-09-13, read-only rescore): negative-damage player-rounds 142 -> 42, ace damage term mean 1,029 -> 581.

**Measured shares at A=1 / D=5 / B=3 / C=2.347 with the multikill fix**, from the saved per-player-round terms of all
3,124 matches (rc2 code, rescaled; the team-kill part of the fix is not in these figures):
- corpus share (player-match totals): damage 38.3%, assists 11.6%, leverage 42.3%, econ 7.8%;
- median player-match share: damage 40.2%, assists 11.4%, leverage 39.2%, econ 6.7%.

With C re-anchored alone (B still 4.25, no ACS fix) the corpus split is 38.9 / 8.9 / 46.2 / 6.0. A figure of
40.9 / 11.1 / 40.5 / 7.4 circulated in session notes; it removed the erroneous +50 per kill without subtracting the
real bonus, and is wrong.

**Open, not decided by this entry.** A fit of combat score against every term at once on the 8 raw captures
(1,040 player-rounds) gives 48.7 per extra kill (the 50 above), **11.8 per assist** (not 25) and kill bonuses of
112.8 / 95.9 / 71.7 / 49.9 / 23.9, each 35-46 below the 150-20(5-alive) schedule. Score equals damage exactly in
703 of 711 rounds with no kills and no assists, so the gap is per-kill. It is what leaves the remaining 42 rounds
negative (all 1 kill, 0 assists, -1 to -40); carving 25 per assist would take that to 102.

**What this entry does NOT do.** No weight is in code. `impact-bonus-denial-rc2` stays frozen and NOT active; its
manifest no longer verifies because `impact.py` changed. Adoption still needs the D term, `FormulaWeights` carrying
A/D/B/C, a new freeze (rc3) declared before scoring, the full review and the `IMPACT_CALCULATION_VERSION` bump.

### 2026-09-14 -- D becomes a flat per-assist knob; the alive count is fixed; correction to the 2026-09-13 entry

**Owner decision, recorded as given ("i dont want to see negative damage numbers. so lets just fold assists into
damage for now ... lets keep a scalor for just straight assists * D").**
- ~~D = 5, 125 per assist, carved out of the damage column~~ -> nothing is carved out of damage. The damage term is
  combat score less the kill bonuses (150 - 20 * (5 - alive)) and 50 per kill after the first, so it keeps Valorant's
  25 for each non-damaging assist and cannot go negative.
- **D = 100 points per assist**, on the raw assist count, as a separate term:
  `impact = A*damage + B*leverage + C*econ + D*assists`. In code as `FormulaWeights.assists` (default 0, so no existing
  candidate changes) and frozen with the other weights by `impact_manifest.config_to_dict`. D = 100 applies with
  A=1 / B=3 / C=2.347 at the next freeze.
- Approximate shares at A=1 / B=3 / C=2.347 / D=100, from the saved per-player-round terms (multikill fix applied, no
  assist carve; alive-count fix NOT applied, it moves damage in 1.5% of player-rounds): corpus damage 40.6%,
  assists 9.3%, leverage 42.3%, econ 7.8%; median player-match 42.8 / 9.1 / 39.2 / 6.7. At D = 0: 44.8 / 0 / 46.6 / 8.6.

**Correction to the 2026-09-13 entry.** Its "Open" paragraph is wrong on two counts.
1. The 35-46 per-kill gap was not in combat score. It came from checking score against **tracker's damage stat, which
   counts overkill** (a Vandal headshot on a 150-HP player reads 160) and damage to teammates. Combat score counts HP
   removed. On the 8 captures, 61.6% of killed players "received" 160 or more; on single kills the residual equals
   minus the overkill in every bin. With overkill removed the fitted bonuses are 142 / 124 / 104 / 85 / 64 (the rest is
   unseen overkill on light- and no-shield victims, in 25-point steps) and the extra-kill term 47.8. The owner's model
   `ACS = damage + 25 per non-damaging assist + 150 - 20 * (5 - alive) + 50 per kill after the first` is confirmed.
   Tracker's damage stat is never used by the scorer and must not be used as a reference.
2. The "11.8 per assist" was an average: **damaging assists fit at 1.5, non-damaging at 24.3**. Riot pays 25 only for
   non-damaging assists (owner confirmed). 65% of captured assists (316 of 483) are damaging, which is why a flat 25
   carve drove damage negative.
3. The gap did not cause the negative damage rounds. The alive count did (below).

**Alive-count fixes (defects, not tuned values).** The count feeds the combat-score bonus backed out of ACS and the
kill-order state leverage is keyed on. Negative damage traced to it in all 42 player-rounds:
1. A disconnected or AFK player (score, kills, deaths, assists and loadout all zero; not in the kill feed) is not
   alive. Valorant's bonus counts only players in the round; the scorer started every team at 5.
2. A dead player who kills later with lingering utility (Showstopper, Orbital Strike, Boom Bot, ...) stays dead. The
   old rule treated ANY later appearance as a killer as a revive; of 2,232 such deaths, ~934 were Clove ults and most
   of the rest Sage-revived players shooting, but the utility kills were not revives.
3. A revived player is dead from their death until their next appearance in the feed (a later death, or a later kill
   with a gun or an ability that needs its user alive). The old rule never removed them at all.
4. A kill logged at the instant of its killer's death is a trade, not a revive.
5. A team kill removes a player from the victim's team (the old walk decremented the enemy).

Checked against the raw captures' per-kill player positions: the new count is right on 1,286 of 1,289 kills (old rule
1,283). Over the whole corpus with the real scorer (read-only), negative damage player-rounds: **42 at `07166da` ->
0**. Between the two versions damage changes in 9,907 player-rounds (1.50%, mean +25.5) and unweighted leverage in
37,488 player-rounds (5.69%) across 7,733 rounds (mean |change| 40.1); corpus sum |leverage| moves +0.15%.

The walk is now one function, `impact._alive_before_each_kill`, used by the scorer, the kill-order refit service
(`kill_order_leverage`: kill terms and state visits) and the four fit replays (`preplant_time_model`,
`preplant_fit_support`, `postplant_factor`, `postplant_value_table`), which had each hand-rolled their own and also
skipped environmental deaths. No stored table, curve or fit is regenerated by this; the next refit reads the new states.

**Noted, unchanged.** A team kill still pays its killer kill-order leverage (the legacy `_kill_order_bonus` call treats
it as an enemy kill). The player page's state diagrams (`player_graphs.py`) keep their own alive rule. The revive
payout question (first kill on a player who is later revived) stays open, and its earlier win-rate evidence should be
re-run under the new classification.

**What this entry does NOT do.** No rescore, no activation. `impact-bonus-denial-rc2` stays frozen, inactive and
unverifiable. Adoption still needs a freeze (rc3) declared before scoring, the full review and the
`IMPACT_CALCULATION_VERSION` bump.

### 2026-09-14 (later) -- trade credit for the traded player: declared before scoring

**Owner decision** ("the person who gets traded should get a portion of the impact of the kill that they were traded.
this shows the need for entry and the space and pressure it can make ... 0-1 gets 60% of the trades kill impact and
then scale down to 30% at 5-6 seconds"), with the owner's choices on 2026-09-14: added on top, per-second steps,
leverage only, and split when one trade kill avenges several teammates.

**Rule.** A player killed by an enemy, whose killer is then killed by the player's team within the trade window,
is credited a share of the TRADE KILL's leverage (`kill_order_bonus x time_factor`, then weighted by B). The share is
timed from the credited player's own death:

| seconds from death to trade kill | [0,1) | [1,2) | [2,3) | [3,4) | [4,5) | [5,6) | >= 6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| share of the trade kill's leverage | 60% | 54% | 48% | 42% | 36% | 30% | 0 (not a trade) |

- **Added on top.** The trader keeps the whole kill.
- **Several teammates avenged by one trade kill:** each timed share is scaled by `max(shares) / sum(shares)`, so the
  credited shares sum to the fastest one's share (e.g. 42% and 54% become 23.625% and 30.375%).
- **The trade is the existing one** (`_traded_factor`): the first kill of the killer in [0, 6) seconds that is not by
  the killer's own side. When that kill is a self or environmental death, the existing death discount still applies
  and there is no credit (no teammate's kill leverage to share).
- **Only enemy kills earn credit:** a self-kill, environmental death or team kill does not.
- **Where it lands:** inside leverage. `leverage_component = B * (kill_x_time + trade_credit - death_x_time)`;
  `time_impact` and `kill_impact` carry it too. A non-persisted `trade_credit` field shows `B * credit` alone. Damage,
  econ, assists and the legacy formula are untouched.
- **Switch:** `enable_trade_credit`, default False on `build_impact_rows_for_match` and `ImpactScoringConfig`, and
  frozen by `impact_manifest.config_to_dict`. No existing candidate changes.

Measured on the corpus before this entry (read-only): 22.1% of enemy-kill deaths are traded (106,960); by second
27,726 / 22,525 / 17,860 / 14,627 / 12,830 / 11,392; trade kills avenge one teammate 89.5%, two 9.8%, three 0.6%.

**Declared measurements, read-only, all 3,124 matches, A=1 / B=3 / C=2.347 / D=100, rc2 econ model, alive-count fix,
credit OFF versus ON:**
1. Corpus and median player-match term shares.
2. Total credit as a share of positive leverage; credited deaths; credit per credited death by trade second.
3. Mean credit per player-round and change in mean impact per round, by agent.
4. Match 3104 player table, and player 226's rounds 20 and 24.
5. Checks: with the flag off every row is identical to the scorer without the change; impact reconciles to its four
   terms; no negative damage.

No adoption threshold is declared: the owner judges the measured results. Nothing is rescored in the database,
activated or frozen by this entry.

### 2026-09-14 (RESULT) -- trade credit: the declared measurements

Read-only, all 3,124 matches (659,290 player-rounds), A=1 / B=3 / C=2.347 / D=100, rc2 econ model, alive-count fix,
credit OFF versus ON, in the order declared.

1. **Shares.** Corpus: damage 40.6 -> 38.8, assists 9.3 -> 8.9, leverage 42.3 -> 44.9, econ 7.8 -> 7.4.
   Median player-match: 42.9 -> 40.8, 9.1 -> 8.7, 39.2 -> 41.9, 6.6 -> 6.4. Leverage now leads damage on both.
2. **Size.** Total credit 21,837,543 = 15.1% of positive leverage (144,930,512); net corpus leverage
   36,560,647 -> 58,398,637. 106,110 credited deaths (the 850 fewer than traded deaths are trades by a self or
   environmental death), in 105,632 player-rounds (16.0%). Mean credit per credited death 206; by trade second
   256 / 230 / 201 / 180 / 155 / 133 (n 27,667 / 22,444 / 17,725 / 14,435 / 12,647 / 11,192). The per-death
   reconstruction totals 21,837,644 against the rows' 21,837,543 (rounding).
3. **By agent**, credit per player-round (= change in mean impact per round), agents with 2,000+ rounds: highest
   Clove 38.9, Neon 36.2, Raze 35.8, Iso 35.2, Waylay 34.9, Breach 34.7; lowest Chamber 28.4, Astra 29.4, Cypher 29.8,
   Veto 30.3, Killjoy 30.4. The spread is narrow (28.4 to 38.9): being traded is common for every role.
4. **Match 3104.** Every player gains (+169 to +855); only ranks 4 and 5 swap (players Najumi and NPrightdolphin).
   Player 226: match leverage -2,281 -> -1,754 (credit +528); round 20 leverage -216 -> -156 (+60), impact
   +129 -> +189; round 24 leverage -456 -> -245 (+211), impact -423 -> -212.
5. **Checks.** With the flag off, all 659,290 rows equal the pre-change rescore exactly; impact reconciles to its four
   terms on every row; no negative damage. Suite 1,150 pass, 2 known-red. Mutating the schedule, the split or the
   enemy-kill rule each fails its test.

No adoption decision is recorded here; the owner judges these. Nothing is rescored in the database or frozen.
