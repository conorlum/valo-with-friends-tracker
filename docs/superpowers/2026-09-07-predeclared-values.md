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

### 2026-09-16 -- rc3: the owner's lock after measurement, and the replays declared before the freeze

**What kind of entry this is.** The weights below were chosen after read-only corpus measurements at exactly these
weights, and the credit-ON figures were shown to the owner before this entry was written. Those figures are recorded as
measured before this entry; they are not preregistered results. What this entry declares in advance is everything still
to come:
- the artifacts, their contracts and their hash chain;
- the Python 3.13 reproduction;
- the stored-v1 comparison;
- the measurements, the checks and the review.

No adoption threshold is declared.

**Owner decisions.**
- ~~B = 3.0~~ -> **B = 2.5**; ~~C = 2.347~~ -> **C = 2.5**. A = 1 and D = 100 (flat per assist) are unchanged. Set in
  the session of 2026-09-15/16, and confirmed on 2026-09-16 after the credit-ON measurement below.
- **Trade credit is adopted ON**, with the rule, schedule and split declared on 2026-09-14, unchanged.
- **`trade_credit_scale` = 1.0.** A knob that multiplies the credit before B (`FormulaWeights.trade_credit_scale`,
  default 1.0, frozen by `impact_manifest.config_to_dict`). At 1.0 it changes no value.
- (2026-09-16) **Two new persisted columns on `impact_scores`, in migration 0010.** Neither changes any value.
  - `trade_credit` holds the row's existing `trade_credit` field, `round(B * trade_credit_scale * credit)`. Neither
    `leverage_component` nor `assists_component` is stored, so without it the credit is recoverable only by replay.
  - `scoring_version` records the `IMPACT_CALCULATION_VERSION` that wrote the row; rows written before 0010 are
    marked 1. **It is provenance only.**
- (2026-09-16) **Writes are enforced by a release write gate, not by the row's own version.** A row-value check cannot
  identify the writer: a checkout whose model lacks the column can update a row that already says 3 and pass it. The
  gate is a gate table plus statement-level triggers (including TRUNCATE) on `impact_scores` and the ingestion tables,
  refusing any write whose connection does not carry the open release's identity. Only a verified rc3 preflight sets
  that identity; deliberate manual fixes use a documented admin identity and are recorded here.
- (2026-09-16) **Python 3.13 for tests, freeze and scoring.** Refreshes run only on the owner's PC; 3.13 matches the
  Render runtime (3.13.5) and the rc2 freeze environment. The full suite gives identical results under 3.11.4 and
  3.13.15 (1,148 passed, 1 skipped, and the same 5 failures, all environmental).
- (2026-09-16) **Ingestion is frozen from this entry until 48 hours after activation**, held closed by the gate, so that
  restoring the pre-activation table stays a complete rollback for that window.
- (2026-09-16) **Measurement 7 below (stored v1 -> rc3) is the owner's last look before the freeze.** A retune after
  seeing it requires a new declaration and new corpus runs; otherwise this lock stands.

Structure unchanged: `impact = A*damage + B*leverage + C*econ + D*assists`; `enable_econ_component` true; econ model
`buy_disruption_v2_30_80_bonus_denial`; `use_realized_swing` true; `enable_postplant_leverage` and
`enable_preplant_empirical` false.

| term | weight | at the weight (time factor 1.00) |
|---|---:|---|
| B, leverage | 2.5 | 5v5 first blood 375, 4v4 425, 3v3 450, 2v2 500, 1v1 625; a 4v1 cleanup 125 |
| C, econ | 2.5 | match 1824 R17's anchor kill 479.4 (450 at C = 2.347); per-kill ceiling 554.4 (221.7426 x 2.5) |

**Measured before this entry (read-only).**
- **Scope:** all 3,125 matches (ids 1-3133, 659,500 player-rounds), production snapshot of 2026-09-16.
- **Environment:** Python 3.11.4, code at `7f4a63b` plus the uncommitted `trade_credit_scale` change, explicit weights
  in scratch scripts.
- **Method:** values were saved as unkeyed arrays and compared by position.

Credit OFF -> ON:
1. **Shares.** Corpus share is each term's summed magnitude over player-matches. Median share is taken over
   player-matches with >= 12 rounds (n 30,990).
   - Corpus: damage 43.5 -> 41.7, leverage 37.7 -> 40.3, econ 8.9 -> 8.5, assists 9.9 -> 9.5.
   - Median: 45.6 -> 43.7, 34.6 -> 37.3, 7.6 -> 7.3, 9.7 -> 9.3.
   - At B = 2.5 damage still leads leverage on both (at B = 3 on 2026-09-14, leverage led).
2. **Credit, measured through leverage.** ON minus OFF leverage, summed over player-rounds: 18,204,413. This includes
   per-round rounding and is **not** the sum of the `trade_credit` field.
   - It is 15.1% of positive per-round leverage with credit off (120,812,285, the 2026-09-14 basis), and 13.5% with
     credit on (134,754,165).
   - Net corpus leverage 30,479,853 -> 48,684,266.
   - 105,669 player-rounds have a positive difference (16.0%), mean 172.3.
   - The largest per-round difference is 981. It was computed separately from the same saved arrays, not printed by
     the report.
3. **Impact per round.** Mean 206.0 -> 233.6, sd 604.8 -> 613.6; range -1,958 to 5,416 both ways.
4. **Ranks.** Within-match impact order, with ties broken by player id:
   - the order changes in 2,262 of 3,125 matches (72.4%), and the top player changes in 222 (7.1%);
   - 6,884 of 31,250 player-matches change rank (by 1: 6,055; 2: 722; 3: 97; 4: 10);
   - impact rank against K-D rank (K-D from kill events; ties kept in the saved player-match order): identical
     45.4% -> 41.1%, within one place 80.8% -> 76.9%.
5. **Impact movement, which is not a rank statistic.** 29,892 player-match impact values increase, 1,358 are unchanged,
   and none decrease.
6. **Checks, by position.** Damage, econ and assists are identical OFF vs ON on every row. The impact change equals the
   leverage change on every row, and impact equals its four terms on every row. Zero negative damage; no failed
   matches.

**Consistency with 2026-09-14.** That entry measured the credit two ways over 3,124 matches:
- the sum of the row field, 21,837,543;
- ON minus OFF leverage, 21,837,990 (58,398,637 - 36,560,647).

Rescaled from B = 3 to B = 2.5, the second is 18,198,325. Today's 18,204,413 exceeds it by 6,088, which is consistent
with match 3133's rows plus rescaling rounding. The 2026-09-14 corpus excluded match 3133, which has no stored
`impact_scores` rows (ingested 2026-09-10, never scored); this corpus includes it.

**Declared cohort.** 3,125 matches, ids 1-3133, 659,500 player-rounds. If a match is ingested before the freeze anyway,
every result for this cohort is kept and additions are reported separately.

**Declared artifacts.** Three contracts, none of them re-derived from `PERSISTED_FIELDS` at run time.
1. **Comparison projection** -- the only thing the chain below compares. One CSV row per
   `(round_id, match_player_id)` over the cohort, sorted by key; the ordered header is recorded once at commit A (every
   field in `PERSISTED_FIELDS` there, plus `trade_credit`, `leverage_component`, `assists_component`);
   `scoring_version` is excluded, because it legitimately differs between review (2) and activation (3). Identified by
   SHA-256.
2. **Load projection** -- exactly the table's columns, including `trade_credit` and `scoring_version`, with
   `scoring_version = 3` asserted on every row before the swap, and verified by reading the built table back through
   the same serializer.
3. **Inputs and diagnostics** -- `match_source_fingerprint` for all 3,125 matches, computed inside the export's
   `REPEATABLE READ` snapshot, with counts, max match id, snapshot id and database name; the interpreter build and
   `pip freeze`; and the K-D counts, read from `kill_events` in that same snapshot.

One canonical serialization is used on both sides: UTF-8, LF, fixed column order, integers without separators, SQL NULL
distinct from JSON null, and `trade_detail` written as recursive canonical JSON with sorted keys.

**Declared chain.** Commit A is the first commit after this entry (the `trade_credit_scale` change and the exporter).
Commit B completes the implementation. Commit C is the freeze.
- K1: Python 3.11, commit A, explicit weights.
- K2: Python 3.13, commit A. **Prediction, recorded before the run: K2's comparison hash equals K1's.**
- K3: Python 3.13, commit B, the declared `impact_rc3` comparator. Must equal K1.
- K4: Python 3.13, commit C, `config_from_manifest`. Must equal K1.
- K5: Python 3.13, the activation commit. Must equal K1, and K5's load projection is what production receives.

A credit-OFF export is produced at commit A and at commit B, in the same configuration with only
`enable_trade_credit` false; OFF(B) must equal OFF(A). Matching ON hashes do **not** by themselves establish the
OFF-derived measurements, which is why the OFF pair is declared here.

A mismatch at any link stops the next step. An explanation does not pass it: the mismatch is eliminated, or this entry
is re-declared.

**Declared measurements.** Measurements 1-6 are computed from K2 with OFF(A) for the last look, and recomputed from K3
with OFF(B) for the RESULT entry; the ON chain fixes the ON side, and the OFF pair fixes the OFF side. Measurement 7 is
computed from K2 against the stored rows.
1. Shares, by the definitions above.
2. Credit, as two separately named measures plus the difference between them:
   - (a) **leverage difference**: ON minus OFF leverage summed over player-rounds, its share of positive leverage
     (credit-off basis), and the player-rounds with a positive difference;
   - (b) **persisted credit**: the sum of the `trade_credit` field, and the player-rounds with a nonzero value.

   Event-level credit measures (credited deaths, and credit per credited death by trade second) are **not** re-measured
   for rc3. The rule is unchanged since 2026-09-14, which measured them at B = 3; at B = 2.5 they scale by 2.5/3 up to
   rounding. The scorer's kill observer runs before the credit is split, so producing them would mean changing a
   hashed source before the freeze.
3. Impact per round: mean and standard deviation to 0.1; negative-damage player-rounds.
4. Sanity and ranks: 10 players in every match; the round-count profile; impact rank against K-D rank; OFF -> ON rank
   changes; and, reported separately from rank movement, the count of player-matches whose impact rises, is unchanged
   or falls. Tie rules and the K-D source are as above.
5. Checksums: row count, per-column sums, the comparison hash and the load-projection hash.
6. The largest absolute value in every smallint column, `trade_credit` included, and the largest per-round credit under
   both definitions in measurement 2.
7. **Stored v1 -> rc3, the owner's last look.** Over the 3,124 matches with stored rows:
   - per-player-match rank changes within each match (ties by player id);
   - matches whose top player changes;
   - the per-match Spearman correlation;
   - for each tracked player, average impact per round under v1 and under rc3, and their rank among tracked players.

   Match 3133 is reported separately.

**Declared review checks** (recorded in `SUMMARY.md`, as for rc2):
1. Source fingerprints match for the 13 declared matches: 3104, the fixed ten (3129, 3130, 3131, 3113, 3118, 3121,
   3114, 3115, 3116, 3117), 3120 and 3133; and the whole-cohort input fingerprint is unchanged between export and
   load.
2. `verify_manifest` passes, and every configuration value is exact, including `enable_trade_credit: true` and
   `trade_credit_scale: 1.0`. The release comparator is the declared `impact_rc3`, so a manifest missing
   `enable_trade_credit` fails identity.
3. The chain holds (K4 = K1, OFF(B) = OFF(A)). Zero input failures; impact equals its four terms on every row; zero
   negative damage; smallint maxima in range.
4. The decomposition check. Raw econ equals the rc2 configuration's raw econ, and every term equals its weight times its
   raw value after the scorer's rounding.
5. The site, fixed-ten and trace reviews reconcile under four-term identities wherever they total impact. Their
   "Before" is the stored production value, with the legacy replay shown separately, and `scoring_version` is compared
   explicitly rather than ignored.
6. Each reinstated defect is caught:
   - the credit flag dropped from the manifest;
   - the scale ignored;
   - the leverage weight applied to the econ term, tested with unequal weights (swapping B and C cannot be detected
     while B = C);
   - D left out of `impact`;
   - credit paid to the trader instead of the traded player;
   - the credit schedule reversed;
   - a three-term identity restored anywhere in the review;
   - `trade_credit` or `scoring_version` left out of the persisted fields (caught against the model's columns, not the
     shared list);
   - one row of the built table altered;
   - a swap killed mid-transaction (production must be unchanged);
   - **a real pre-0010 checkout updating a row that already says 3** (refused by the gate, which a row-value check
     would have allowed), and an ingestion run with no identity (refused at its first insert, nothing committed).
7. `review-results.json` covers the 13 declared matches, with its field list recorded and validated on read, and is
   compared against the built table before the swap.
8. The full suite passes under Python 3.13 with database tests against the rehearsal database, except failures
   confirmed environmental and listed.
9. A rehearsal on a scratch database on the Render instance, restored from a fresh production backup, covers:
   - migrations and the gate install;
   - export, build, verify, swap, rollback and re-swap;
   - the gate tests above;
   - page loads that miss the cache during a forward and a rollback swap, with the longest stalled request recorded;
   - prewarm coverage including cache-blob validity;
   - review of PR #67's non-scoring pages;
   - the PR #67 rollback path.

   Every duration is recorded.

**What this entry does NOT do.** No code, manifest, migration or database row is changed by it. `ACTIVE_MANIFEST` stays
None, and `IMPACT_CALCULATION_VERSION` stays 2 on the branch (1 on `main`, which produced every stored row). rc2 stays
frozen, inactive and unverifiable. No adoption threshold is declared: the owner judges the measurements, the last look
and the review.

### 2026-09-16 (RESULT, K1 vs K2) -- the cross-interpreter prediction failed; exact summation adopted; the chain is re-declared

**What was run** (read-only, production snapshot of 2026-09-16, commit A = `93c1c03`):

| link | Python | credit | player-rounds | SHA-256 |
|---|---|---|---:|---|
| K1 | 3.11.4 | ON | 659,500 | `2457a844e5fc16db0a44334ce9798573ce5b8b733b577a99d13d47967b376181` |
| K2 | 3.13.15 | ON | 659,500 | `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` |
| OFF(A) | 3.13.15 | OFF | 659,500 | `648df0403a0d48411dfd42c5b7697da1d6952de416e3a1a36bc97fc628460eac` |

All three carry the same cohort fingerprint, `2c31fbbd9b1507f32631d304dec86885f5b876440a3e31140f4c1769545bdc5f`, so the
inputs were identical.

**The declared prediction failed: K2 does not equal K1.** 18 player-rounds differ. Every one differs by exactly 1 in
`trade_credit`, and the same rows carry knock-on differences of 1 in `kill_impact` (7 rows), `impact` (1) and
`leverage_component` (1). No other column differs.

**Cause, verified.** `_trade_credits_for_round` computed `scale = max(shares) / sum(shares)`, and Python 3.12 changed
the built-in `sum()` to compensated summation for floats. In match 89, round 1776, one trade kill avenged three players
with timed shares [0.3, 0.36, 0.42]:
- `sum()` gave 1.0799999999999998 under 3.11 and 1.08 under 3.13;
- player 886's credit was therefore 23.8 and 23.799999999999997;
- at B = 2.5 that is 59.5 and 59.49999999999999, which round to 60 and 59.

Each interpreter reproduced its own value three times in one process, so this is not run-to-run nondeterminism.
`math.fsum` gives 1.08 under both.

**Owner decision (2026-09-16): eliminate the mismatch rather than re-declare around it.** The scorer sums floats with
`math.fsum` at every float total: the credit split and two totals in `impact.py`, and the budget, recovery, denial and
ledger totals in `econ_buy_disruption.py`. Integer sums (counts, loadouts, weapon prices) keep `sum()`, which is exact
for them. Commit `b65fd4f`.

**Evidence the change is exact.** `tests/test_scoring_float_sums.py` fails on values under Python 3.11 before the
change (the observed credit, and a budget total of 47325.369000000006 against 29030.962 + 18294.407) and passes under
3.11 and 3.13 after it. The full suite gives identical results under both interpreters: 1,189 passed, 30 skipped, and 1
failed (the stale `test_happy_path_blob_validates` fixture).

**Re-declared chain.** Commit A is now `b65fd4f`. K1, K2 and OFF(A) above are kept as evidence and are no longer
baselines.
- K1': Python 3.11, commit `b65fd4f`, explicit weights, credit ON.
- K2': Python 3.13, commit `b65fd4f`, explicit weights, credit ON. **Prediction, recorded before the run: K2' equals
  K1', byte for byte.**
- OFF(A'): Python 3.13, commit `b65fd4f`, credit OFF.
- K3, K4, K5 and OFF(B) as declared in the previous entry, compared against K1' and OFF(A').

The last look (measurements 1-7 of the previous entry) is computed from K2' and OFF(A'). Reported alongside, with no
pass condition: the player-rounds that differ between K2' and K2 (what exact summation changed relative to 3.13's
`sum()`), and between K1' and K1.

**The figures measured before the previous entry** were computed under Python 3.11 with the old summation. The RESULT
entry reports figures from the re-declared chain, and shows both wherever they differ.

**What this entry does NOT do.** No production row, manifest or activation changes. Ingestion stays frozen.

### 2026-09-17 (RESULT, rc3) -- the chain holds; the declared measurements; the owner's last look confirms the lock

**What was run** (read-only, production snapshot of 2026-09-16/17; 3,125 matches, 659,500 player-rounds each):

| link | Python | code | configuration | credit | SHA-256 |
|---|---|---|---|---|---|
| K1' | 3.11.4 | `b501ae2` | explicit locked weights | ON | `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` |
| K2' | 3.13.15 | `b501ae2` | explicit locked weights | ON | the same, byte for byte |
| OFF(A') | 3.13.15 | `b501ae2` | explicit locked weights | OFF | `648df0403a0d48411dfd42c5b7697da1d6952de416e3a1a36bc97fc628460eac` |
| K3 | 3.13.15 | commit B `5c369f8` | the declared `impact_rc3` comparator | ON | equal to K1' |
| OFF(B) | 3.13.15 | commit B `5c369f8` | `impact_rc3`, credit off | OFF | equal to OFF(A') |

`b501ae2` is the ledger commit whose `webapp/` tree is exactly commit A (`b65fd4f`); `git diff b65fd4f b501ae2 --
webapp` is empty. All five artifacts carry the cohort fingerprint
`2c31fbbd9b1507f32631d304dec86885f5b876440a3e31140f4c1769545bdc5f`, so the inputs were identical, and each ON artifact
is 53,437,561 bytes, each OFF artifact 53,252,671.

**The declared prediction holds: K2' equals K1'.** Rows differing from K2': K1' 0, and the pre-fix K2 0 as well -- exact
summation reproduced what 3.13's compensated `sum()` already computed, so only 3.11 moved. Against the pre-fix K1,
18 rows differ, as the previous entry recorded.

**K3 and OFF(B) hold the chain through the comparator.** K3 was exported from a clean checkout of commit B through
`COMPARATORS["impact_rc3"]` rather than explicit weights, and reproduces K1' exactly; OFF(B) reproduces OFF(A'). The
sidecars record the source (`comparator impact_rc3`, with a credit override for OFF(B)) and HEAD.

**Declared measurements 1-6, from K3 and OFF(B)** (`rc3-release/chain/rc3-measurements.json`; identical to the last
look's figures, the artifacts being byte-identical):

1. **Shares.** Corpus: damage 41.7%, leverage 40.3%, econ 8.5%, assists 9.5%. Median over the 30,990 player-matches
   with at least 12 rounds: 43.7%, 37.3%, 7.3%, 9.3%.
2. **Credit**, as the two declared measures:
   - (a) leverage difference: 18,204,414 summed over player-rounds, 15.1% of the credit-off positive leverage
     (120,812,285), positive on 105,669 player-rounds, largest 981;
   - (b) persisted credit: 18,206,528 over the same 105,669 player-rounds, largest 981;
   - (a) minus (b) is -2,114, which is the scorer's per-row rounding, not a disagreement about who is credited.

   Event-level credit measures are not re-measured, as declared: the rule is unchanged since 2026-09-14, which
   measured them at B = 3, and at B = 2.5 they scale by 2.5/3 up to rounding.
3. **Impact per round.** Mean 233.6, standard deviation 613.6. Negative-damage player-rounds: 0.
4. **Sanity and ranks.** Ten players in every one of the 3,125 matches. Round counts run 4 to 38, with 404 matches at
   24 rounds the mode. Impact rank equals K-D rank for 41.1% of the 31,250 player-matches and is within one for 76.9%.
   Credit off to on: 6,884 player-match rank changes, 2,262 matches whose order changes at all, 222 whose top player
   changes; movement is 0 places for 24,366, one for 6,055, two for 722, three for 97 and four for 10. Reported
   separately, impact rises for 29,892 player-matches, is unchanged for 1,358 and falls for none.
5. **Checksums.** 659,500 rows. Column sums: kill_impact 291,572,774; death_impact 149,485,256; impact 154,070,781;
   damage 76,040,271; econ_impact 0; time_impact 19,473,492; swing_impact 0; econ_kill 34,151,593; econ_death
   28,177,150; clutch_kill 37,397,270; clutch_death 31,225,516; post_plant_kill 26,726,383; post_plant_death
   21,936,853; traded_teammate 106,160; traded_by_teammate 106,160; kill_order_bonus 10,694,197; econ_component
   11,984,643; econ_pickup 0; trade_credit 18,206,528; leverage_component 48,684,267; assists_component 17,361,600.
   The comparison hash is the K3 hash above.

   **The load-projection hash is not reported here, and cannot be.** That projection includes `scoring_version`, which
   is 2 under the reviewed code and 3 at activation -- the same fact that keeps `scoring_version` out of the comparison
   projection. The hash that matters is the one actually loaded; it is recorded at the activation export and bound by
   `verify-build`, which also compares the approved comparison artifact against the built table row by row.
6. **Smallint maxima** (largest absolute value): damage 953, econ_impact 0, time_impact 1,497, swing_impact 0,
   econ_kill 1,833, econ_death 800, clutch_kill 1,030, clutch_death 500, post_plant_kill 1,420, post_plant_death 805,
   traded_teammate 5, traded_by_teammate 3, kill_order_bonus 1,170, econ_component 2,397, econ_pickup 0,
   trade_credit 981. Every one is far inside a smallint. The largest per-round credit is 981 under both definitions in
   measurement 2.

**Measurement 7, the owner's last look** (from K2' against the stored rows; `rc3-artifacts-A2/last-look.json`). Over
the 3,124 matches with stored rows, 31,240 player-matches:
- 15,179 player-match ranks change: 16,061 stay, 10,372 move one place, 3,534 two, 955 three, 262 four, 50 five, 5 six
  and 1 seven;
- 633 matches change their top player;
- per-match Spearman correlation: median 0.939, p10 0.842, p90 0.988;
- tracked players, average impact per round v1 -> rc3 and rank among tracked players: NPrightdolphin#NA1 269.1 ->
  329.3 (1 -> 1), SambuUwU#NA1 263.7 -> 329.1 (2 -> 2), Osmin#NA1 241.1 -> 291.9 (5 -> 3), flatcat#woof 236.1 ->
  286.8 (6 -> 4), Deemo#Derf 261.4 -> 281.8 (3 -> 5), ternstyle#GIGI 242.4 -> 268.3 (4 -> 6), DoubleBl1nd#BEEF 206.4
  -> 246.0 (7 -> 7), Beef Shortrib#Galbi 180.9 -> 219.3 (8 -> 8), Najumi#NPC 175.7 -> 201.3 (9 -> 9), Yosher#Toshi
  165.5 -> 184.3 (10 -> 10), zopecow#1570 117.3 -> 106.5 (11 -> 11), Momomimo#hru 101.0 -> 97.9 (12 -> 12);
- match 3133, reported separately, is scored for the first time: ten players from 8,838 down to -507 over 21 rounds.

**Owner decision, 2026-09-17: the lock stands.** A = 1, B = 2.5, C = 2.5, D = 100, trade credit on at
`trade_credit_scale` 1.0, econ model `buy_disruption_v2_30_80_bonus_denial`, realized swing, timing candidates off.
The freeze proceeds against this scoring.

**Implementation evidence.** The full suite passes on both interpreters: 1,251 passed and 63 skipped under 3.11
offline, 1,301 passed and 13 skipped under 3.13 with the database tests, none failed. A third external review of the
implementation (2026-09-17) raised eight findings, all fixed with tests shown to fail on values; dispositions are in
section 7 of `plans/2026-09-16-rc3-ship-plan-v2.md`.

**What this entry does NOT do.** No production row, manifest or activation changes. `ACTIVE_MANIFEST` stays None and
`IMPACT_CALCULATION_VERSION` stays 2 on the branch. Ingestion stays frozen. The freeze, the reviews and the rehearsal
are still ahead, and the declared review checks are answered in `SUMMARY.md`, not here.

### 2026-09-19 — RESULT: rc3 activated in production (Stage 8)

**rc3 is live.** `IMPACT_CALCULATION_VERSION = 3`, `ACTIVE_MANIFEST =
docs/superpowers/impact-rc3/candidate-manifest.json`. A = 1, B = 2.5, C = 2.5, D = 100, trade credit on at
`trade_credit_scale` 1.0, econ model `buy_disruption_v2_30_80_bonus_denial`, realized swing, **timing
candidates off** — the lock of 2026-09-17, unchanged.

**Commits.** rc3 frozen at `82d8e6b` (commit C). PR #67 (the release, without activation) merged
2026-09-18 07:32:49 UTC as `c0cd59a`. The activation commit — one commit, `ACTIVE_MANIFEST` and
`IMPACT_CALCULATION_VERSION` only — was created as `b2940fd` off the reviewed tip and rebased onto merged
`main` as **`17612c3`**; PR #68 merged 2026-09-18 09:50:04 UTC as **`510204f`**, a true merge commit with
parents `c0cd59a` and `17612c3`.

**The chain, reproduced a seventh time by K5** (exported at `17612c3`, `code.dirty: []`, against production
at alembic 0010, cohort 3,125 matches / max id 3133):

| | |
|---|---|
| comparison sha256 | `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` |
| cohort fingerprint | `2c31fbbd9b1507f32631d304dec86885f5b876440a3e31140f4c1769545bdc5f` |
| load projection | `3674f8a0160a87b1b862ae902dcad228a1d0967fb43482f0b0730d86a46e5a00`, 659,500 rows |
| manifest LF-sha256 | `8e5c637b34b2ccb767fe2d16b18017eb8571f158623653c8f9bc4bf2ae10e20c` |

Equal to K1', K2', K3, K4, KR and KR2 on **both** the hash and the cohort fingerprint. `K5.csv` is
byte-identical in size to `K3-on.csv` and `K4.csv` (53,437,561); `K5.load.csv` to `KR2.load.csv`
(50,393,745).

**Durations (all 2026-09-18 UTC unless stated).**

| step | window | duration |
|---|---|---|
| 8.1 preflight + K5 export | 08:33:17 – 09:02:04 | `28m43.374s` (scoring 14.8 min) |
| 8.2 build | 09:05:02 | `54.545s` — 659,500 rows, staged oid 65265 |
| 8.2 verify-build | – 09:22:37 | `16m39.616s` (fingerprinting 14.65 min) |
| 8.3 capture prewarm lists | 09:24:00 | `3.674s` — 12 roster, 2,238 recent |
| **8.4 swap** | 09:27:32 | **`35.25s` committed** |
| 8.4 verify-live | 09:28:41 – 09:44:11 | `15m27.928s` |
| 8.5 merge + Render deploy | 09:50:04 – ~09:52:13 | deploy live in **~2 min** |
| 8.6 caches | 09:54:01 – 09:55:53 | roster prewarm `1m5.200s`, 12 of 12 |
| 8.7 acceptance replay | 09:56:42 – 10:15:39 | `18m56.710s` |
| 8.7 final verify-live | 10:15:48 – 10:31:42 | `15m50.083s` |
| 8.9 observation hold | 10:34:41 – 2026-09-19 08:31:37 | **21h47m** (ended early, below) |
| 8.10 roster refresh | 2026-09-19 08:32:53 – 09:21:45 | `48m52s`, 73 matches |

**Gate transitions.** Installed **closed** at 7.4 (`note: installed before merging PR #67`). Opened at 8.10:
`scoring_gate` now `state=open, release_id=impact-rc3, admin_id=rc3-runbook, note='48-hour hold over'`,
**`updated_at 2026-09-19 08:31:39.544768+00`**.

`scoring_release_log`, complete:

| id | at (UTC) | identity | operation | outcome |
|---|---|---|---|---|
| 1 | 2026-09-18 09:05:05.390584+00 | rc3-runbook | build | built |
| 2 | 2026-09-18 09:22:36.119087+00 | rc3-runbook | verify-build | clean |
| 3 | **2026-09-18 09:27:35.050690+00** | rc3-runbook | **swap** | **swapped** |
| 4 | 2026-09-18 09:44:08.880701+00 | rc3-runbook | verify-live | clean |
| 5 | 2026-09-18 10:31:41.146059+00 | rc3-runbook | verify-live | clean |

**The swap.** `impact_scores` oid 25941 → **65265**, 659,290 rows → **659,500**, `scoring_version` 1 → **3**.
The pre-activation table is preserved as `impact_scores_v1` (oid 25941, 659,290 rows, retained per 8.12).
`player_view_cache` cleared by DELETE. **Match 3133 scored for the first time: exactly 210 rows**, stranded
unscored since 2026-09-10.

**Nothing moved between build and live.** All six row digests are byte-identical at 8.2, at 8.4 and at the
final verify-live: `impact_scores` (as `impact_scores_new` at build) `-7994785726743056784682`,
`round_player_stats` `-2715131475311374433553`, `rounds` `-1435023103768587768058`, `kill_events`
`999620965623172311516`, `match_players` `-416037733228552673585`, `matches` `-141510464020508552521`.

**Verification results.** `verify-build` and `verify-live` both `clean`, `problems: []`, `scoring_versions
[3]`, 22 compared columns, `read_back_sha256 == artifact_sha256`, `scores_without_a_stat_row 0`,
`stat_rows_without_a_score 0`. **8.7 acceptance: `3125 matches replayed, 0 differ`.** 8.6 cache agreement:
`cache agrees with scores: 24 player-scopes, 12 players, tolerance 1e-09`, coverage 24 of 24 usable at
`4003003003`. 8.8: match 3104's ten player totals equal the AFTER column of `match-3104-site.md` exactly
(every diff 0); match 3133's page renders Avg Impact equal to the stored score for all ten players; all 12
roster pages 200.

**Exposure window (8.4.0).** Open 09:27:35 → ~09:52:13 UTC, **about 24.5 minutes**, accepted in advance by
owner decision. The live code was confirmed by the cache version it writes, caught mid-flip:
`4003003002` at 09:51:25, **`4003003003`** at 09:52:13. No `...002` row survived.

**8.9 ended early, 2026-09-19 08:31 UTC, owner decision** — 21h47m of the declared 48h. D11's purpose is a
window in which rollback is lossless, not a soak test: nothing accumulates with elapsed time, the site has
few visitors and no periodic job runs. Its substance is a human judging the numbers, and that was done —
five of the owner's own matches, five of Najumi's and five across the roster, plus two deep dives that both
resolved to the model behaving correctly (a −425 pistol death explained by `_present_players` shifting a 5v4
round onto the 4v4 node at 180; a −872 post-plant death decomposed to 3v3 × 1.346 time factor = −606
leverage, −330 econ, +64 damage). **No defect was found in rc3 at any point.**

**8.10 reopening.** 73 matches ingested (3,125 → 3,198, max id 3206), spanning 2026-09-05 to 2026-09-19.
**674,530 `impact_scores` rows, `scoring_version` 3 only. Zero unscored matches.** Match 3134 verified:
`scoring_version` 3 only, and `equals its replay`. R1 expired here, as declared.

**Catch-up is incomplete, and by how much is now measured.** `-Count 100` was requested; the single
`__INITIAL_STATE__` batch returns **exactly 20**, so **9 of 12 roster players hit that ceiling**
(NPrightdolphin, Najumi, Beef Shortrib, Deemo, Osmin, ternstyle, DoubleBl1nd, flatcat, Momomimo) and their
history is still truncated. Yosher#Toshi returned 16 — under the ceiling, therefore complete. **SambuUwU#NA1
and zopecow#1570 returned 0**, which the current output cannot distinguish from a silent failure. tracker.gg
exposes a "load more" control that pages further; the code does not use it
(`discover_recent_match_ids` reads one page load). Deepening this is deferred work, not a release gate.

**What this entry does NOT do.** No scoring change, no formula change, no manifest change. The timing
candidates remain off and Part 4 remains dormant and unshipped — its measured shape was worth nothing
out-of-fold, and that finding stands.

### 2026-09-19 — DECLARATION: the post-plant time factor, five candidates, declared before measurement

**What kind of entry this is.** Predeclaration. Nothing is implemented, nothing is activated, no scoring code is
edited, nothing is written to the database. It fixes the arms, the estimand, the bucketing and the decision rule for
a measurement session that has not yet run a single contrast. It exists because this project has already been burned
once by the opposite order: Part 4's post-plant factor was recorded as a win, and the win turned out to be a 22%
level shrink riding alongside a shape that measured nothing. That reading was withdrawn (entry 2026-09-16 and the
remediation commits). This entry is written so the same thing cannot happen twice.

**Why now.** rc3 is live (activation RESULT, 2026-09-19): `IMPACT_CALCULATION_VERSION = 3`, A 1 / B 2.5 / C 2.5 /
D 100, trade credit 1.0, econ `buy_disruption_v2_30_80_bonus_denial`, realized swing, **both timing candidates OFF**.
The post-plant time factor is therefore the legacy one, and four defects in it plus one owner redesign were written
down on 2026-09-19 with their populations counted. None has been measured out of fold. Any change to any of them is
`IMPACT_CALCULATION_VERSION` 4 and a full rescore, so the question is not "is this defensible" but "is this worth a
rescore", and that is an evidence question.

#### 1. What is live, stated exactly

`app/scoring/impact.py::_time_factor`, with `enable_postplant_leverage=False` and
`enable_preplant_empirical=False`:

| region | kill | death |
|---|---:|---:|
| pre-plant | 1.0 | 1.0 |
| post-plant, `t < plant+38` | `1 + (t − plant)/53` | same |
| `plant+38 <= t <= plant+45` | 1.75 flat | **0.50 flat** |
| `t >= plant+45` in an `exploded` round, or `t >= defuse_time` in a `defused` round | 0.50 | 0.50 |
| `t > plant+45` with neither flag set (a phantom plant) | **the ramp, uncapped** | same |

Corpus-wide the scalar is modest: median effective multiplier 1.000, mean 1.022, contributing +2.43% of total
impact. It scales 152,083 rows down and 100,058 up — a redistribution, not a bonus layer.

#### 2. The candidates, and which are arguments and which are claims

Two kinds, and they get **different decision rules**, declared here rather than chosen once the numbers are in.

**Tier A — correctness fixes.** Their case is a priori: the shipped rule contradicts its own stated reasoning. They
are measured to check they do no harm, not to discover whether they are right.

- **C1. A decided round pays 0, not 0.5.** After detonation or defuse the round is over; nothing is at stake and
  econ already prices the gear. Owner decision, 2026-09-19: **0, not 0.5**. Population **2,856 events** (1,662 post
  detonation, 1,194 post defuse) of 323,394 kills in planted rounds — **0.9%**.
- **C2. The death cliff at plant+38.** A death at t=37.9 is charged 1.715 and at t=38.0 is charged 0.500: a **70.8%
  cut across 0.1 seconds**, where the kill side steps 1.715 → 1.750. The comment justifying it ("the round is
  basically already decided") describes something continuous and implements it as a step. **1,577 events within
  ±1s of the boundary**; 2,924 of 154,031 post-plant kills reach the window at all.
- **C3. The phantom-plant ramp is unbounded.** The 0.5 resolution value fires only on the `exploded` or `defused`
  flag. A phantom plant (never armed, decided on the round timer) sets neither, so the ramp keeps climbing past the
  point the bomb should have gone off: **1.851 at 45.1s, 1.981 at 52s**, both above the design's own 1.75 ceiling.
  **618 kills.** Known and deliberate: `_time_factor` does not route the legacy branches through
  `effective_plant_time` because it would move stored Impact for 76 rounds without a version bump (review finding 5).
  A version 4 removes that objection.

**Tier B — empirical claims.** Their case is evidence and nothing else. Both are shape-or-level changes to a scalar
that the last measurement could not distinguish from noise.

- **C4. The post-plant level is too high by about a quarter.** The only candidate with prior out-of-fold evidence:
  legacy ramp × 0.7826 scored −0.00003 [−0.00005, −0.00002], an IMPROVEMENT. Caveat on record and carried forward:
  **0.7826 is not fitted.** It is `1/c` from the uncentred Part 4 table, so the existing result shows that *this*
  scale beats 1.0, not that it is the best scale.
- **C5. Group post-plant states by man-advantage differential, not absolute counts** (owner's redesign). Empirical
  attacker win rate over 1,686,190 post-plant round-seconds: even states cluster tightly (1v1 65.5, 2v2 64.0,
  3v3 62.5, 4v4 63.4, 5v5 67.3 — spread 4.8pp) and 1v1 vs 1v2 differ by 39.4pp. Refuted within that same table:
  1v2/1v3/1v4 are *not* alike (26.1 / 8.0 / 3.0), and there is a real second-order gradient along total alive
  (+1 is worth 91.0% at 2v1 but 80.4% at 5v4). The argument for trying it where Part 4 failed is data density —
  Part 4 spread thin counts over 54 supported cells keyed `(a, d, t, victim_side)`. **That is a hypothesis. It is
  still a SHAPE change, and shape is exactly what measured zero.**

#### 3. The instrument — inherited, not invented

The predeclared out-of-fold protocol of `scripts/run_five_arm_report.py` (econ spec 8d-i), unchanged:

- **Estimand:** each arm scored as the FIXED composite `impact_diff` — the scorer's own output under that arm's
  configuration — as a single predictor beside the nuisance controls. One coefficient, no component reweighting.
  An arm cannot repair a bad composite by being refit.
- **Target:** `PRIMARY_T2` (k=3, gamma=0.7, match_weight=1.0). **Controls:** `round_result`, `score_diff_before`,
  `attacking_is_team_a`, `loadout_diff`, `full_buy_count_diff`.
- **Folds:** 5 outer, `stable_folds(seed=0)`, match-clustered, assignment fixed once from the reference arm's match
  set and shared by every arm. Inner 3-fold config/L2 selection on training matches only, L2 grid (0.1, 1.0, 10.0).
- **Uncertainty:** paired match-clustered bootstrap, **2,000 draws**, two-sided 95%.
- **Sign convention:** `loss(arm) − loss(P0)`. **Positive is worse.**
- **Replay mode:** ex-ante (`use_realized_swing=False`), as the protocol runs it. The time factor is not gated by
  that switch, so every arm here is measurable in this mode; the econ component is 0 throughout for every arm alike
  and therefore cannot differentiate them.
- **Scale anchor, carried forward so no contrast is read without it:** baseline pooled out-of-fold weighted log loss
  0.6729 against a coin flip's 0.6931. The entire scoring system buys about 0.02. A 0.00003 contrast is roughly
  1/700th of that.

**Corpus.** Every arm runs on the full current corpus — **3,198 matches** as of this entry, up from the 3,125 the
Part 4 numbers were measured on. Contrasts within this session are mutually comparable; **they are not directly
comparable to the recorded Part 4 figures**, which is why C4 and Part 4 itself are both re-measured here rather than
quoted. The dataset fingerprint and fold-mapping hash are recorded in the RESULT entry.

#### 4. How the arms are produced without touching scoring code

`webapp/app/` is not edited — not one line — and `git diff --stat webapp/app/` is required to be empty at the end
of the session. Four of the five candidates cannot be expressed through the existing `scoring_kwargs`: the
`postplant_factor_table` hook is consulted only for genuine plants and non-self kills, is reached only *after* the
exploded/defused early return, and is never told whether it is pricing a kill or a death. So:

**A measurement-local replacement for `_time_factor`, installed by monkeypatch from `webapp/scripts/`, is the
mechanism.** It is a copy of the live function plus one variant switch, and the live file is untouched on disk.

**The identity gate, run before any contrast and reported in the RESULT whatever it says:** the patched function with
its variant set to NONE must reproduce the reference replay **exactly** — every observation of every round of all
3,198 matches identical. A single differing value voids the mechanism and the session stops. This is the same
discipline as commit 88643a3 ("stop a formula change from reporting itself as a broken identity"), applied to the
harness rather than to the formula.

#### 5. The arms

`P0` is the reference. Every contrast is against `P0`.

| arm | what it changes | fitted? |
|---|---|---|
| **P0** | nothing — rc3 as shipped | no |
| **P0′** | the patch, variant NONE — the identity gate, not a contrast | no |
| **P1** | C1: the exploded/defused branch returns **0.0** instead of 0.5, kill side and death side alike | no |
| **P2b** | C2: the flat 0.5 death charge in `[plant+38, plant+45]` is replaced by a linear decay from the ramp's own value at t=38 (`1 + 38/53 = 1.71698`) down to **0.5 at t=45**. Continuous at both ends. **The kill side is left exactly as shipped** (flat 1.75), so this arm moves the death side only | no |
| **P2L** | the level-matched control for P2b: the same total extra death-side post-plant leverage as P2b, spread **uniformly across all post-plant deaths** instead of concentrated in the window. `P2b − P2L` is what the *targeting* is worth once the level is held equal | yes, per fold |
| **P3a** | C3, minimal: the resolution value applies at `t >= plant+45` **regardless of the exploded/defused flags**, so the ramp is capped | no |
| **P3b** | C3, structural: a phantom plant gets **no post-plant regime at all** — the pre-plant 1.0 throughout — which is what routing the legacy branches through `effective_plant_time` would do | no |
| **P4-0.70 / P4-0.7826 / P4-0.90** | C4: the legacy post-plant ramp multiplied by a fixed scale s. `s = 1.0` is P0 | no |
| **P4f** | C4 with s selected **per fold on training matches only** from the declared grid {0.70, 0.7826, 0.90, 1.00} and applied to the held-out fold. Assembled from the arms above; no extra replay | yes, per fold |
| **P5** | C5: Part 4 with **one thing changed — the pooling ladder key** (section 6). Everything else identical: the differencing, the kill-weighted time-mean denominator, FLOOR 0.05, CEIL 2.0, W=2, the 60-observation floor, `solve_and_apply_centering` | yes, per fold |
| **P6** | Part 4 exactly as built, on this corpus. Both a replication of the withdrawn result and the only honest comparator for P5 | yes, per fold |
| **PC** | the Tier A fixes combined: P1 + P2b + P3b | no |
| **PC+** | PC plus each Tier B arm that reaches IMPROVEMENT under section 8. Contingent by rule, not by outcome: the rule is fixed here, the membership is whatever the results make it | as its members |

**On C2 and the level/shape split.** The separation is required "wherever both could move". For C2 they **cannot be
separated by construction**: removing a 70.8% discount necessarily raises what those deaths are charged, and a
rescaling that restores the level reintroduces the discontinuity the candidate exists to remove. That is stated here
rather than discovered later, and `P2L` is the decomposition offered in its place.

**Every declared arm that is not run is reported as NOT RUN, with its reason, in the RESULT.** None is dropped
silently, and compute cost is not a reason to omit one from the report.

#### 6. C5's bucketing — fixed here, in full

P5 changes the pooling ladder of `ValueTable` and nothing else. With `a` attackers and `d` defenders alive:

- **differential** `g = a − d`, which takes exactly the values −4 … +4 over the 25 reachable states;
- **size bucket** `nb = LOW if (a + d) <= 5 else HIGH`.

The ladder, tried in this order, each rung requiring **60 observations** (the existing `MIN_OBSERVATIONS`, applied to
the target second's own counts before smoothing, exactly as today):

1. `exact` — `(a, d, t)`. **Unchanged from Part 4.**
2. `diff_size` — `(g, nb, t)`, pooled over every state sharing that differential and size bucket.
3. `diff` — `(g, t)`, pooled over every state sharing that differential.
4. `diff_band` — `(g, band(t))`, with the existing half-open bands [0, 38.0), [38.0, 41.5), [41.5, 45).
5. `unsupported`.

`V(a, 0, t)` stays analytically pinned as today. The W=2 moving-average smoother, the differencing, the denominators,
the clamp and the centring constant are untouched.

Three consequences, stated before the fact so neither can be presented as a finding afterwards:

- P5 is **identical to P6 on every cell that resolves at rung 1**, so it can only differ where Part 4 was thin. The
  share of scored post-plant kills that resolves below rung 1 is reported in the RESULT; if that share is small,
  a null result for P5 says little about the differential idea and much about how rarely it is reached, and it will
  be reported that way.
- The rungs pool **more** where the data is thinnest and **not at all** where it is dense, which is the hypothesis's
  own logic.
- The choice to key on `(a − d)` with a size correction **was informed by the whole-corpus win-rate table** quoted
  in section 2. That is prior information about the structure, disclosed here. What is fitted per fold on training
  matches only is every *value*: the cells, the smoothing, the denominators and `c`.

#### 7. Leakage rules

- Anything fitted — V, the factor table, `c`, P2L's scalar, P4f's selection of s — is fitted on **training folds
  only** and applied to held-out folds. Arms P2L, P4f, P5 and P6 are therefore replayed once per fold.
- `solve_and_apply_centering`'s own warning is binding: solving `c` on the whole corpus is the exact leak the
  per-fold tables exist to remove. `c` is solved per fold, on that fold's training kills, and the per-fold values
  are reported.
- Arms that are pure deterministic rule changes (P1, P2b, P3a, P3b, P4-s, PC) are fitted from nothing and are
  replayed once.

#### 8. The decision rule

Fixed now. **If a result lands outside it, the result is recorded against the rule as written and the rule does not
move.**

Verdict vocabulary, used in these words and no others: **IMPROVEMENT** (interval entirely below zero),
**HARM** (interval entirely above zero), **INCONCLUSIVE** (interval spans zero). An interval spanning zero is never
reported as "no harm found", and a zero-width interval at exactly zero is reported as IDENTICAL — a broken contrast,
not a verdict.

- **Tier A (P1, P2b, P3a, P3b).** Ship if the arm is **not HARM**. INCONCLUSIVE is an acceptable ship verdict for
  Tier A, because the case for these is a priori and the measurement is a harm check. This is declared here
  precisely so it cannot be invented after an inconclusive result arrives.
- **Tier B (P4f, P5).** Ship **only on IMPROVEMENT**. INCONCLUSIVE does not ship. HARM does not ship. This is the
  rule that Part 4's shape failed, and it is applied unchanged.
- **P5 additionally** must beat P6 — the contrast `P5 − P6` must not be HARM — or the regrouping has not earned its
  place over the design it replaces.
- **The combination is measured, not assumed.** Whatever set passes is also run as `PC` / `PC+`, and if the combined
  arm is HARM while its parts are not, **nothing ships**. Effects do not add.
- **Where a whole version 4 is justified.** A version 4 costs a full rescore of ~674,530 rows plus a release. It is
  recommended if **either**: (a) at least one Tier B arm is IMPROVEMENT and the combined arm is not HARM; **or**
  (b) the Tier A arms are not HARM *and* their combination moves enough stored Impact to make leaving a known-wrong
  rule live worse than the rescore — declared threshold: **>= 1% of `impact_scores` rows changed, or >= 5% of
  matches reordered by within-match player rank**. If neither holds, the recommendation is **no version 4 now**, with
  the Tier A fixes recorded as accepted and deferred to ride along with the next version bump for another reason.

#### 9. Predictions, declared before running

Falsifiable, and recorded so the RESULT can be scored against them rather than narrated:

1. **P1, P2b, P3a, P3b are each INCONCLUSIVE.** Power, not merit: they move 0.9%, 1.9% and 0.2% of post-plant kills
   respectively, and a change to *all* post-plant kills (Part 4's shape) already measured +0.00000 [−0.00003,
   +0.00003]. A fraction of a population that cannot be resolved is not resolvable either.
2. **P4f is IMPROVEMENT**, replicating the level result on the larger corpus.
3. **P5 is INCONCLUSIVE**, and `P5 − P6` is INCONCLUSIVE: the differential regrouping changes which thin cells
   borrow from which, and thin cells are by definition where few kills are scored.
4. **PC is INCONCLUSIVE.**

If prediction 2 fails, C4's prior evidence does not survive contact with a larger corpus and C4 does not ship. If
prediction 3 fails in the improving direction, the owner's redesign has done something Part 4 could not, and that is
the finding this session was worth running for.

#### 10. Scope, and what this entry does not do

Work is confined to `webapp/scripts/` (new analysis scripts only), `docs/superpowers/` (this entry and the RESULT),
and read-only queries against production under `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`. Nothing
under `webapp/app/`, `docs/superpowers/impact-rc3/`, `alembic/versions/` or `.env*` is touched. No row is written, no
match is ingested, no manifest is frozen, no version is bumped. **Starting a version 4 is a separate release with its
own runbook and is not begun here.**

### 2026-09-19 (amendment) — one more arm, P5b, declared before it runs

**Why this amendment exists.** The declaration above fixed C5's ladder with Part 4's exact `(a, d, t)` rung left
untouched at rung 1, and said in advance that P5 "can differ from Part 4 only on cells that fall BELOW it", that the
share of lookups reaching the lower rungs would be reported, and that "if that share is small, a null result for P5
says little about the differential idea and much about how rarely it is reached". **That share has now been measured,
and it is small.**

Building both tables once on the whole corpus as a construction check — 1,726,739 post-plant round-seconds,
153,481 kills, no contrast computed and no arm scored — the P5 ladder resolves:

| rung | lookups | share |
|---|---:|---:|
| `exact` | 668,487 | **99.04%** |
| `diff_size` | 3,316 | 0.49% |
| `diff` | 2,809 | 0.42% |
| `diff_band` | 338 | 0.05% |
| `unsupported` | 32 | 0.005% |

So P5 as declared regroups **under 1% of value lookups** and is identical to Part 4 on the other 99%. It is a fair
test of "does a differential fallback beat Part 4's defender-pooled fallback on thin cells". It is **not** a test of
the owner's actual proposition, which is that the differential is the right way to group post-plant states *at all*.

**What is added.** One arm, and nothing else changes:

- **P5b** — the same differential ladder with the exact rung **removed**, so `(g, nb, t)` becomes rung 1 and the
  absolute `(a, d)` cell is never consulted. The regrouping then applies to the whole scored population instead of
  to the 0.96% Part 4 could not support. `g = a − d`, `nb = LOW if a + d <= 5 else HIGH`, and the remaining rungs,
  the 60-observation floor, W=2, the differencing, the denominators, the clamp and `solve_and_apply_centering` are
  all exactly as section 6 fixes them. Fitted per fold on training matches only, like P5 and P6.

**What has not changed, and is not allowed to.** P5's own bucketing stays exactly as declared and P5 still runs. The
decision rule of section 8 applies to P5b unchanged: it is **Tier B**, so it ships **only on IMPROVEMENT**, and it
must additionally not be HARM against P6. No threshold, no verdict vocabulary and no prediction from the entry above
is revised.

**The honest description of what happened here.** A descriptive support count — not a contrast, not an arm, not a
loss — showed that a declared arm could not answer the question it was declared for. Adding an arm to answer it, and
saying so in advance and in the open, is the intended use of this ledger. Deleting or re-tuning P5 after the fact
would not have been.

**Prediction for P5b, declared now:** INCONCLUSIVE against P0, and INCONCLUSIVE against P6. The reasoning is
unchanged from prediction 3 — this is still a SHAPE change to a scalar whose shape has already measured zero — but
P5b is the version of that test with real statistical weight behind it, so a failure of this prediction is
informative in a way P5's would not have been.

**Two other pre-measurement facts from the same construction check, recorded because they are inputs to the run and
not results of it.** The wrapper's identity gate passes at row level on a 25-match sample (5,170 rows, every
`impact`, `time_impact`, `kill_impact` and `death_impact` identical) — the full-corpus gate still runs before any
contrast. And a replay costs about 0.29s per match, so the declared arm set is roughly nine hours of read-only
replay; that is a scheduling fact, and section 5's rule stands that no arm is dropped from the report for costing
time.

### 2026-09-19 (RESULT) — the post-plant time factor: the level is the only thing that measured

Every arm declared on 2026-09-19 and amended the same day, run to completion. **No arm was left unrun.** Nothing was
implemented, no scoring code was edited (`git diff --stat webapp/app/` is empty), nothing was written to production,
and no version 4 was begun.

**Provenance.** 3,198 matches, 67,251 round-observations, `dataset_fingerprint 3198:f9a31bb2df2586ec`,
`fold_mapping_hash cebae50f85e94736`, shared by every arm. Target T2 (k=3, gamma=0.7, match_weight=1.0), controls
`round_result, score_diff_before, attacking_is_team_a, loadout_diff, full_buy_count_diff`, fixed composite
`impact_diff`, 5 match-clustered outer folds at seed 0, inner 3-fold L2 selection on training matches only, paired
match-clustered bootstrap at 2,000 draws, two-sided 95%. Sign convention `loss(arm) − loss(P0)`, so **positive is
worse**.

**The identity gate passed**: the wrapper with no variant reproduced the unpatched replay on **67,251 of 67,251
observations**, every field identical. Arms therefore measure their declared change and not the way it was injected.

**Where it ran, and why that is worth recording.** Against production the replay cost 28.4 minutes, of which almost
all was latency: `build_impact_rows_for_match` issues exactly 4 queries per call with no caching, at ~65ms a round
trip, 3,198 times a pass. The corpus was copied to a local PostgreSQL 18.6 instance — counts, `alembic 0010` and the
match-id md5 `1d639f01ece40d3cf43b7b94352edccc` all verified identical to production — and the same replay then cost
**80 seconds, a 21x speedup**. 30 full-corpus passes ran in about 15 minutes. The bulk dump of the same data takes
19 seconds; the 28 minutes was never data volume, it was 12,792 round trips. See [[project_local_postgres_for_replays]].

#### The contrasts

| arm | contrast | 95% interval | verdict |
|---|---:|---|---|
| **P1** post-resolution kills pay 0 | −4.7362e−06 | [−1.0710e−05, +1.0597e−06] | **INCONCLUSIVE** |
| **P2b** death cliff at plant+38 → linear decay | +3.6193e−06 | [−2.9623e−06, +1.0090e−05] | **INCONCLUSIVE** |
| **P3a** ramp capped at plant+45 regardless of flags | +2.8970e−07 | [−2.7251e−06, +3.4408e−06] | **INCONCLUSIVE** |
| **P3b** phantom plants get no post-plant regime | +5.0325e−08 | [−2.2719e−08, +1.3035e−07] | **INCONCLUSIVE** |
| **PC** the three Tier A fixes combined | −9.8504e−07 | [−1.0421e−05, +8.0107e−06] | **INCONCLUSIVE** |
| **P4-0.90** level scaled 0.90 | −2.1789e−05 | [−3.1501e−05, −1.1819e−05] | **IMPROVEMENT** |
| **P4-0.7826** level scaled 0.7826 | −4.4022e−05 | [−6.5051e−05, −2.2381e−05] | **IMPROVEMENT** |
| **P4-0.70** level scaled 0.70 | −5.7483e−05 | [−8.6576e−05, −2.7736e−05] | **IMPROVEMENT** |
| **P4f** level selected per fold | −5.7483e−05 | [−8.6576e−05, −2.7736e−05] | **IMPROVEMENT** |
| **P6** Part 4 exactly as built | +1.8235e−06 | [−3.8102e−05, +4.0623e−05] | **INCONCLUSIVE** |
| **P5** differential ladder beneath Part 4's exact rung | −5.5814e−06 | [−4.5572e−05, +3.1335e−05] | **INCONCLUSIVE** |
| **P5b** differential ladder as rung 1 | −8.4907e−06 | [−4.8962e−05, +3.0285e−05] | **INCONCLUSIVE** |
| **PC+** Tier A plus the level | −6.0502e−05 | [−9.0421e−05, −3.0167e−05] | **IMPROVEMENT** |
| **P2L** level-matched control for P2b | +1.8140e−06 | [+9.9321e−07, +2.6912e−06] | **HARM** |
| P5 vs P6 | −7.4049e−06 | [−1.7948e−05, +3.1908e−06] | **INCONCLUSIVE** |
| P5b vs P6 | −1.0314e−05 | [−2.4998e−05, +4.8692e−06] | **INCONCLUSIVE** |
| P2b vs P2L | +1.8053e−06 | [−4.7734e−06, +8.3146e−06] | **INCONCLUSIVE** |

Scale anchor, so none of these is read without it: the baseline pooled out-of-fold weighted log loss is 0.6729
against a coin flip's 0.6931, and the entire scoring system buys about 0.02. The largest effect here, PC+ at
6.05e−05, is about **1/330th** of that.

#### The four declared predictions, scored

All four held. That is not a virtue — it means nothing below is being explained after the fact.

1. **P1, P2b, P3a, P3b each INCONCLUSIVE** — CONFIRMED, all five Tier A arms including PC.
2. **P4f IMPROVEMENT** — CONFIRMED, and the level result replicates on the larger corpus.
3. **P5 INCONCLUSIVE, and P5 vs P6 INCONCLUSIVE** — CONFIRMED, both.
4. **PC INCONCLUSIVE** — CONFIRMED.

The amendment's prediction for P5b — INCONCLUSIVE against P0 and against P6 — also held.

#### What actually measured

**The level is the entire result, and it is monotone.** 0.90 → 0.7826 → 0.70 gives −2.18e−05 → −4.40e−05 →
−5.75e−05, improving all the way down, and PC+ (Tier A plus the level) is statistically indistinguishable from the
level alone. Every detectable gain in this session comes from charging the post-plant regime **less**.

**The shape measured nothing, again, and P5b is the decisive version of that test.** P5 only regrouped the 1.14% of
lookups that fall below Part 4's exact rung. P5b removed the exact rung entirely: **99.41% of its lookups resolved
on the differential rung** (7,782,489 of 7,828,364), so the owner's grouping was applied to essentially the whole
scored population. It still cannot be distinguished from zero, nor from Part 4, whose own arm is likewise
INCONCLUSIVE here. Three different post-plant *shapes* — Part 4's cell-keyed table, the differential fallback, and
the differential throughout — have now each measured nothing out of fold. The finding recorded on 2026-09-19 that
Part 4's shape is worth nothing survives a fair test of the alternative.

**P2L is the sharpest methodological result in the set.** Spreading P2b's extra death-side leverage uniformly over
every post-plant death instead of concentrating it in the window is **HARM** — +1.81e−06, interval entirely above
zero — while `P2b vs P2L`, the contrast that isolates whether *targeting* the window beats spreading it, is
INCONCLUSIVE. So C2's shape is not demonstrably worth anything; only its level moved, and it moved the wrong way.
The magnitude, ~1/11,000th of what the system buys, is resolvable only because it is a uniform shift over a large
population. **Statistically real and practically nil is a coherent verdict, and it is this one.**

#### C3's stated cause is wrong

C3 was declared as a phantom-plant defect. It is not one. Of the kills charged an uncapped ramp past plant+45:

| exploded | defused | "Time Win" | rounds | kills |
|---|---|---|---:|---:|
| false | false | **false** | 566 | 632 |

**Zero are phantom plants.** All 566 rounds are "Team A/B Elimination Win" with both resolution flags absent, and the
worst factor charged is 1.884 against the design's own 1.75 ceiling. That is why P3b, which keys on
`is_phantom_plant` (outcome "Time Win"), changes 45 rows while P3a, which caps at plant+45 regardless of flags,
changes 634.

The wider fact behind it, and the more important one: **28,774 of 43,515 planted rounds (66.1%) carry neither
`exploded` nor `defused`.** tracker.gg appears not to set them when a round ends by elimination. So the
post-resolution branch of `_time_factor` never fires for two-thirds of planted rounds — which also bounds C1, whose
population exists only in the third that do have flags. Kills after a decided round in the other two-thirds get no
discount at all today, and **neither C1 nor C3 as declared addresses them.** That is a larger instance of the same
defect than either candidate was written against, and it is the strongest lead this session produced. It is recorded
here as a finding, not measured — measuring it is a new declaration.

#### Two limitations of the declaration itself

Recorded as limitations, not repaired after the fact.

1. **The P4 grid did not bracket its optimum.** P4f selected **0.70 on all five folds** — the floor of the declared
   grid {0.70, 0.7826, 0.90, 1.00} — and the contrasts are monotone toward it. Every fold's inner cross-validation
   wanted to go lower than the grid allowed. So P4f measures "the best of four values declared in advance", not
   "the best level", and **the true optimum is unbracketed and below 0.70**. The grid was fixed before measurement,
   which is the discipline working as intended; it was also too narrow, which is a defect in my declaration and not
   in the result.
2. **PC was composed with P3b.** The declaration fixed PC = P1 + P2b + P3b before the row-motion evidence existed.
   P3b turns out to address 45 rows of C3 against P3a's 634, so the declared combination leaves most of C3 unfixed.
   PC and PC+ are reported exactly as declared; the recommendation below says plainly that **P3a is the C3 fix worth
   shipping**, and that is a recommendation, not a retrofitted arm.

#### Row motion — not a contrast, no verdict

3,198 matches, 674,530 rows. Reported separately from evidence because Part 4 reordered 81.5% of matches while being
worth nothing out of fold.

| arm | rows changed | matches changed | matches reordered | mean abs delta |
|---|---:|---:|---:|---:|
| **PC** | **6,891 (1.02%)** | 2,216 (69.3%) | **242 (7.6%)** | 29.6 |
| P1 | 4,078 (0.60%) | 1,590 (49.7%) | 105 (3.3%) | 24.1 |
| P2b | 2,774 (0.41%) | 1,589 (49.7%) | 152 (4.8%) | 38.1 |
| P3a | 634 (0.09%) | 505 (15.8%) | 30 (0.9%) | 45.4 |
| P3b | 45 (0.01%) | 21 (0.7%) | 0 (0.0%) | 3.4 |

PC clears **both** limbs of section 8's threshold: >= 1% of rows (1.02%) and >= 5% of matches reordered (7.6%). The
row limb clears by 0.02pp, which is a hair; the reorder limb clears comfortably.

#### The decision, under the rule as written

- **Tier A (P1, P2b, P3a, P3b, PC): all INCONCLUSIVE, therefore not HARM, therefore eligible to ship.** The rule
  declared INCONCLUSIVE an acceptable Tier A verdict in advance, precisely so this could not be argued afterwards.
  Their case remains what it was: a decided round has nothing at stake, a 70.8% cut across 0.1s is not a model of a
  continuous quantity, and a factor above the design's own ceiling after the bomb should have detonated is outside
  the model's stated range.
- **Tier B: P4f IMPROVEMENT, so the level ships. P5 and P5b INCONCLUSIVE, so the regrouping does not.** P5b
  additionally fails to beat P6. The owner's hypothesis was given the fairest test available — applied to 99.41% of
  the population, not to a 1% remainder — and did not survive it.
- **The combined arm is not HARM:** PC+ is IMPROVEMENT at −6.05e−05.
- **Version 4 is justified**, on limb (a) — a Tier B arm is IMPROVEMENT and the combination is not HARM — and
  independently on limb (b), since Tier A is not HARM and PC clears both motion thresholds.

**Recommendation: a version 4 carrying the Tier A fixes with P3a in place of P3b, plus a post-plant level constant —
but the level constant is not yet known, and must be fitted before it is frozen.** Shipping 0.70 would ship the edge
of a grid that every fold pushed against. The next step is one declared measurement: the same P4f arm over a wider
grid extending well below 0.70, declared in advance, with the boundary condition checked. That is cheap now — a full
replay is 80 seconds against the local corpus — and it is the difference between shipping a fitted constant and
shipping an artefact of the grid I chose.

**Nothing in this entry activates anything.** The timing candidates stay off, `IMPACT_CALCULATION_VERSION` stays 3,
and beginning a version 4 is a separate release with its own runbook.

#### Follow-ups this session did not take

- **The missing-flag population.** 66.1% of planted rounds have no resolution flags; kills after those rounds are
  decided are charged in full. Larger than C1 and C3 combined, and not addressed by either.
- **A wider level grid**, per the recommendation above.
- **`paired_bootstrap_delta` is a pure-Python loop** over 2,000 draws x ~67,000 rows. Once the replays moved local it
  became the single largest compute cost in this session — larger than all 30 corpus passes combined. Vectorising it
  is a clear win; it lives in `app/services/stats_math.py`, which this session was scoped out of touching.

### 2026-09-19 (CORRECTION) — the "missing resolution flags" finding was wrong

The RESULT entry above claims that 66.1% of planted rounds carry neither `exploded` nor `defused`, that the
post-resolution branch therefore "never fires for two-thirds of planted rounds", and that kills after a decided round
in that majority "get no discount at all today". It calls this "the strongest lead this session produced".

**That is wrong, and it is withdrawn.** The owner's question — after a plant, the round can only end by detonation or
defuse — is what exposed it. There is a third ending, and it is the common one:

| planted round ends by | winner | rounds |
|---|---|---:|
| **elimination** | **attacker** | **28,695** |
| defuse | defender | 12,498 |
| detonate | attacker | 2,243 |
| time (phantom plant) | defender | 77 |
| elimination | defender | **2** |

Attackers plant and then wipe the defenders: the round ends *at that kill* and the spike never detonates. That is
normal Valorant, not absent data. The flags are not missing — they track the outcome string exactly (`exploded` iff
"Detonate Win", `defused` iff "Defuse Win").

And the consequence the withdrawn claim drew does not follow. A round that ends by elimination has **no
post-decision period at all**, because its last kill is its ending; there are no kills left to discount. The
post-resolution branch correctly does not fire. Only 2 rounds in 41,515 are genuinely impossible (defenders winning
by elimination after a plant, which cannot end a round).

**What survives.** C3's defect is real but far smaller than stated. The 632 kills charged an uncapped ramp all fall
within **45.00 to 46.88 seconds** after the plant — at most 1.88s past the spike timer, a clock-skew artefact
between the plant timestamp and kill timestamps — and are charged at most 1.884 against the design's 1.75 ceiling.
Capping the ramp is still right; it is a rounding-scale correction, not a structural hole.

The RESULT's contrasts, verdicts, predictions and recommendation are unaffected: no arm measured this population,
and the row-motion figures were measured, not inferred. What changes is the follow-up list — "the missing-flag
population" is struck from it.

### 2026-09-19 (DECLARATION 2) — the level's real optimum, and whether any post-plant shape beats none

Declared before running. Two questions the first session left open, and one the owner raised in response to it.

**Question 1 — what IS the level?** `P4f` selected 0.70 on all five folds, the floor of the declared grid, and the
contrasts were monotone toward it. The level is too high; how much is unknown, because the grid did not bracket it.

**Question 2 — is a principled shape worth anything over no shape at all?** Three post-plant shapes have now each
measured nothing out of fold. The owner's position is that principled scoring beats none. That is testable rather
than a matter of taste: if shape genuinely carries no signal, a **flat** post-plant factor should do as well as the
shipped ramp — and a flat factor of exactly 1.0 is *no post-plant timing model whatsoever*. If the ramp beats flat,
the ramp has earned its place; if it does not, the shipped ramp is decoration and the honest choice is the simplest
form at the right level.

#### The arms

Everything from the first declaration carries over unchanged: target T2, its control set, the fixed composite
`impact_diff`, 5 match-clustered folds at seed 0, inner 3-fold selection on training matches only, 2,000-draw paired
bootstrap, `loss(arm) − loss(P0)` so positive is worse, and the verdict vocabulary IMPROVEMENT / HARM /
INCONCLUSIVE with an interval spanning zero never reported as "no harm found". P0 is the same reference, on the same
corpus and fold assignment (`3198:f9a31bb2df2586ec`, `cebae50f85e94736`).

| arm | what it is |
|---|---|
| **L-s** | the legacy post-plant regime scaled by a constant s, for **s in {0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00}**. Same construction as the first declaration's P4-s; 0.70, 0.90 and 1.00 are already measured and are reused, not re-run |
| **Lf** | s selected **per fold on training matches only** from that grid, applied to the held-out fold |
| **F-k** | the post-plant regime **replaced by a flat constant k** — no ramp, no plant+38..45 override — for **k in {0.40, 0.60, 0.80, 1.00, 1.20, 1.40}**. Pre-plant stays 1.0 and the post-resolution value stays 0.5, both untouched. **F-1.00 is the null model: no post-plant timing at all** |
| **Ff** | k selected per fold on training matches only from that grid |
| **Ff vs Lf** | the question. Does the shipped ramp's shape beat a flat factor once each is allowed its own best level? |

#### Decision rule

Unchanged in kind from the first declaration, and fixed here before any of it runs.

- **Both Lf and Ff are Tier B**: each ships only on IMPROVEMENT against P0.
- **The shape question is decided by `Ff vs Lf`, not by which has the better headline contrast.** If that contrast is
  INCONCLUSIVE, the ramp is **not** shown to beat flat, and the recommendation is the simpler form — a flat
  post-plant factor at the fitted level — on the grounds that between two forms that cannot be told apart, the one
  with fewer arbitrary constants is preferred. If it favours the ramp (negative, interval below zero), the ramp
  earns its place and ships. If it favours flat, flat ships on evidence rather than on parsimony.
- **The boundary rule, which the first declaration lacked and needed.** If `Lf` or `Ff` selects a value at the edge
  of its grid on any fold, that is reported as **UNBRACKETED** alongside the contrast, and the constant is **not**
  recommended for freezing — the same failure as last time, named in advance so it cannot be quietly accepted.
  The grids above are deliberately wide enough that an interior optimum is the expected outcome.
- **No constant is frozen by this entry**, and nothing is implemented or activated.

#### Prediction, declared before running

1. `Lf` selects an **interior** value, most likely in 0.40–0.60, and is IMPROVEMENT.
2. `Ff` is IMPROVEMENT, and its selected k lands below 1.00.
3. **`Ff vs Lf` is INCONCLUSIVE** — the ramp will not be shown to beat a flat factor.
4. `F-1.00`, the no-timing-at-all null, is **not** IMPROVEMENT: removing the post-plant scalar entirely without
   re-levelling loses the level correction that is the only thing measuring so far.

If prediction 3 fails in the ramp's favour, the shipped shape is doing real work and the case for principled timing
is evidential rather than aesthetic — which is the outcome the owner expects and which this session exists to give a
fair chance.

### 2026-09-19 (DECLARATION 3) — bracket the post-plant constant, and test whether zero is the answer

Declared before running. Declaration 2's two fitted arms both selected their grid **floor** on all five folds
(`Lf` 0.30, `Ff` 0.40) and were reported UNBRACKETED under its own boundary rule, so neither constant is fitted and
neither may be frozen. This entry widens downward until the optimum is interior, and adds the limiting case.

**What Declaration 2 established, and is not re-opened.** `Ff vs Lf` was INCONCLUSIVE, so the shipped ramp is not
shown to beat a flat factor and the simpler form is preferred; that rule was fixed in advance and stands. `F-1.00` —
no post-plant timing model at all — was IMPROVEMENT against the shipped ramp, refuting this session's own
prediction 4. Both facts are settled and this entry does not re-measure them.

**Arms.** Same protocol throughout: same corpus and fold assignment (`3198:f9a31bb2df2586ec`, `cebae50f85e94736`),
target T2 and its controls, fixed composite `impact_diff`, 2,000-draw paired bootstrap, `loss(arm) − loss(P0)`,
positive is worse, and the same verdict vocabulary.

| arm | what it is |
|---|---|
| **F-k** | flat post-plant constant, for **k in {0.00, 0.05, 0.10, 0.15, 0.20, 0.30}**. 0.40 and above are already measured and are reused |
| **Ff2** | k selected per fold on training matches only, over the **union** grid {0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 0.80, 1.00, 1.20, 1.40} |
| **L-s** | the ramp scaled, for **s in {0.10, 0.20}**, so the ramp family is bracketed on the same range as the flat family |
| **Lf2** | s selected per fold over {0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00} |

**`F-0.00` is the limiting case and is in the grid deliberately.** It sets the post-plant factor to exactly zero:
a post-plant kill contributes NOTHING to leverage. It is almost certainly wrong as a model — a kill that wins a
post-plant 1v1 is not worth nothing — and it is included precisely so the measurement can say whether the data
distinguishes "much less than pre-plant" from "nothing at all". If `F-0.00` is not clearly worse than `F-0.10`, the
target cannot tell those apart, and that is a statement about the limits of this evidence, not a licence to ship
zero. **No arm shipping recommendation will be made for `F-0.00` whatever it measures**, because scoring a decisive
duel at zero contradicts the standing constraint that no kill is worth nothing.

**The boundary rule carries over, unchanged.** A fold selecting a grid edge means UNBRACKETED and the constant is
not recommended for freezing. `Lf2`'s grid bottoms at 0.10 and `Ff2`'s at 0.00, which is the floor of the
representable range, so an interior selection is now the only outcome that yields a freezable constant.

**Predictions, declared before running.**

1. `Ff2` selects an **interior** value in **0.10–0.30**, and is IMPROVEMENT against P0.
2. `F-0.00` is **worse** than `Ff2` — the data does distinguish "small" from "nothing".
3. `Lf2` selects interior, and `Ff2 vs Lf2` is again INCONCLUSIVE, leaving the flat form preferred on parsimony.
4. The improvement curve is **shallow** across 0.10–0.40: the spread among those arms is smaller than the gap from
   any of them to the shipped ramp, so the exact constant matters much less than the decision to stop boosting
   post-plant kills.

Nothing is implemented, activated or frozen by this entry.

### 2026-09-19 (DECLARATION 4) — the side asymmetry, which both models discard

Declared before running. The owner's observation drove this: kills at the defuse deadline are kills on the defuser,
and they are must-win fights for the side that is behind. Measuring that produced the largest effect this
investigation has found, and it is in a quantity neither the shipped model nor Part 4 represents.

**The measurement that motivates the arm** (descriptive, whole-corpus, no contrast computed). In win-probability
points, over the 153,450 scored post-plant kills:

| victim | mean D | n | weight vs grand mean |
|---|---:|---:|---:|
| **attacker** | **21.92pp** | 69,946 | 1.254 |
| **defender** | **13.76pp** | 83,504 | 0.787 |

Ratio **1.59** overall, and **3.26** late (t >= 30). Per band in a 1v1: at 30-38s an attacker killing the defender
gains 21.2pp while an attacker dying costs 74.1pp; by 38-45s it is 4.0pp against 50.0pp. The same event at the same
second is worth 3.5x to 12.5x more to one side than the other.

**Why neither model carries it.** The shipped factor is **side-blind** — `_time_factor` applies the same
`1 + t/53` to the kill and the death, so a 4pp event and a 50pp event are both multiplied by 1.75. Part 4 keys on
`victim_is_attacker`, which looks correct, but its factor is `D / mean_over_t D` computed **within**
`(a, d, victim_side)`: the per-side level divides out by construction and only the within-side time shape survives.
It normalised away the asymmetry it was built to represent. That is a candidate explanation for why three separate
shape models each measured nothing, and it is recorded as a hypothesis, not a conclusion.

**Why this is not double-counting `K(s)`.** The kill-order graph is spike-blind, verified directly:
`1v1 -> 0v1` and `1v1 -> 1v0` both carry weight 250. Post-plant those are not equivalent — one ends the round for
the attackers, the other leaves the spike ticking — so the state term provably does not encode the asymmetry and a
side-dependent factor supplies new information rather than repeating `K`.

#### The arms

Protocol unchanged throughout: same corpus and fold assignment (`3198:f9a31bb2df2586ec`, `cebae50f85e94736`), target
T2 and its controls, fixed composite `impact_diff`, 5 match-clustered folds, 2,000-draw paired bootstrap,
`loss(arm) − loss(P0)` with positive worse, and the same verdict vocabulary.

| arm | what it is |
|---|---|
| **A1** | post-plant factor `= 0.40 × w(victim side)`, where `w` is `mean D` for that victim side divided by the kill-weighted grand mean, **fitted per fold on training matches only** and normalised so the training population's kill-weighted mean `w` is exactly 1. One constant per side, no time shape at all |
| **A2** | the same, with `w` fitted per `(victim side × time band)`, bands `t < 30` and `t >= 30`, again per fold. Tests whether the asymmetry *growing* with the clock adds anything over a constant asymmetry |

**The level is deliberately pinned at 0.40 and not fitted.** `F-0.40` — a side-blind flat factor at exactly that
level — is already measured, so `A1 vs F-0.40` isolates the side split with the level held identical. Fitting a
level here would reintroduce the confound this whole investigation exists to avoid, and a level that beat `F-0.40`
by being better-fitted would say nothing about asymmetry.

#### Decision rule

- **A1 and A2 are Tier B**: each ships only on IMPROVEMENT against P0.
- **The question is decided by `A1 vs F-0.40`, not by the headline contrast.** IMPROVEMENT there means the side
  asymmetry earns its place over a side-blind factor at the same level. INCONCLUSIVE means it does not, and the
  recommendation stays with the simpler side-blind form however good A1's own contrast looks.
- **`A2 vs A1`** decides the time-varying asymmetry on the same terms: INCONCLUSIVE leaves the constant asymmetry
  preferred on parsimony.
- If `A1 vs F-0.40` is IMPROVEMENT, the level is then fitted in a **separate** declared measurement. No constant is
  frozen here.

#### Predictions, declared before running

1. **`A1 vs F-0.40` is IMPROVEMENT.** This is the first arm in the investigation whose underlying effect is measured
   in tens of win-probability points rather than single digits, and unlike the shape arms its signal is concentrated
   where the kills actually are, not in the 1% tail.
2. `A1 vs P0` is IMPROVEMENT and larger in magnitude than `F-0.40 vs P0` (−9.26e−05).
3. **`A2 vs A1` is INCONCLUSIVE** — the asymmetry's growth with the clock is a time shape, and every time shape
   tested so far has measured nothing.
4. If prediction 1 fails, the asymmetry is real in win probability but not recoverable by this target, and the
   recommendation returns to a side-blind flat factor. That outcome would also weaken the hypothesis above about
   why Part 4 failed.

Nothing is implemented, activated or frozen by this entry.

### 2026-09-19 (RESULT, declarations 2–4) — no structure beats a constant; the constant is bracketed at last

Every arm from declarations 2, 3 and 4 run to completion, none left unrun. Same protocol throughout: corpus
`3198:f9a31bb2df2586ec`, folds `cebae50f85e94736`, target T2 and its controls, fixed composite `impact_diff`,
5 match-clustered folds, 2,000-draw paired bootstrap, `loss(arm) − loss(P0)` with positive worse. Nothing
implemented, activated or frozen.

#### The headline

| contrast | point | 95% interval | verdict |
|---|---:|---|---|
| **F-1.00 vs P0** — *no post-plant model at all* | −5.270e−05 | [−7.883e−05, −2.691e−05] | **IMPROVEMENT** |
| **Ff2 vs P0** — flat, level fitted per fold | −9.073e−05 | [−1.627e−04, −1.768e−05] | **IMPROVEMENT** |
| **Lf2 vs P0** — ramp, level fitted per fold | −8.830e−05 | [−1.620e−04, −1.243e−05] | **IMPROVEMENT** |
| **Ff2 vs Lf2** — does the ramp's shape beat flat? | −2.431e−06 | [−7.384e−06, +2.503e−06] | **INCONCLUSIVE** |
| **A1 vs F-0.40** — does the side split beat side-blind? | +1.096e−05 | [−3.068e−06, +2.436e−05] | **INCONCLUSIVE** |
| **A2 vs F-0.40** — side split, time-banded | +1.743e−05 | [+9.068e−07, +3.368e−05] | **HARM** |
| **A2 vs A1** | +6.473e−06 | [+5.802e−07, +1.275e−05] | **HARM** |
| **F-0.00 vs Ff2** — is nothing worse than a little? | +8.073e−06 | [−1.615e−05, +3.333e−05] | **INCONCLUSIVE** |
| F-0.30 vs F-0.40 | +1.705e−07 | [−7.114e−06, +7.815e−06] | INCONCLUSIVE |

Level family, flat: F-0.00 −8.27e−05 (INCONCLUSIVE) · 0.05 −8.49e−05 (INC) · 0.10 −8.78e−05 (INC) · 0.15 −8.97e−05
(IMPROVEMENT) · 0.20 −9.07e−05 · 0.30 −9.24e−05 · 0.40 −9.26e−05 · 0.60 −8.63e−05 · 0.80 −7.29e−05 · 1.00 −5.27e−05
· 1.20 −2.49e−05 · 1.40 +9.47e−06 (INCONCLUSIVE). Ramp family: L-0.10 −8.78e−05 · 0.20 −9.04e−05 · 0.30 −8.99e−05
· 0.40 −8.62e−05 · 0.50 −7.98e−05 · 0.60 −6.99e−05 · 0.70 −5.75e−05 · 0.80 −4.11e−05 · 0.90 −2.18e−05.

#### The constant is finally bracketed

| arm | selected per fold | grid | bracketed? |
|---|---|---|---|
| `Lf` (decl. 2) | 0.30 ×5 | 0.30–1.00 | **no — floor** |
| `Ff` (decl. 2) | 0.40 ×5 | 0.40–1.40 | **no — floor** |
| **`Lf2`** (decl. 3) | 0.30, 0.20, 0.20, 0.30, 0.20 | 0.10–1.00 | **yes — interior** |
| **`Ff2`** (decl. 3) | 0.40, 0.30, 0.30, 0.40, 0.30 | 0.00–1.40 | **yes — interior** |

Declaration 3's boundary rule is satisfied for the first time, so a constant may now be recommended for freezing.
The flat curve is shallow across 0.15–0.40 and `F-0.30 vs F-0.40` is INCONCLUSIVE, so the evidence picks a **region,
around 0.2–0.4**, not a point. Against the shipped regime's 1.00 rising to 1.85.

#### Predictions, scored — four of seven failed

The discipline's value is visible here: more predictions failed than held, and each failure is a thing learned.

| # | declared | outcome |
|---|---|---|
| 2.1 | `Lf` selects interior, 0.40–0.60 | **FAILED** — floor on all five folds |
| 2.2 | `Ff` IMPROVEMENT, k below 1.00 | held (k = 0.40, also the floor) |
| 2.3 | `Ff vs Lf` INCONCLUSIVE | **held** |
| 2.4 | `F-1.00` **not** IMPROVEMENT | **FAILED** — removing the model outright improves on it |
| 3.1 | `Ff2` selects interior in 0.10–0.30 | half held — interior, but 0.30/0.40 |
| 3.2 | `F-0.00` worse than `Ff2` | **FAILED** — INCONCLUSIVE; the target cannot distinguish "much less" from "nothing" |
| 4.1 | `A1 vs F-0.40` IMPROVEMENT | **FAILED** — INCONCLUSIVE, and the point estimate is positive |
| 4.3 | `A2 vs A1` INCONCLUSIVE | **FAILED**, and worse than predicted — HARM |

#### What this establishes

**No structure beats a constant.** Four structural models have now each been measured against a side-blind constant
and none has beaten it: Part 4's `(a, d, t, victim_side)` table, the differential regrouping as a fallback rung, the
differential regrouping applied to 99.41% of the population, and the side-asymmetric level. The last is the sharpest
negative, because its underlying effect is the largest anything in this investigation has found — a kill whose
victim is an attacker is worth 21.92pp against 13.76pp for a defender victim, and in a 1v1 at 38–45s it is 50.0pp
against 4.0pp — and it was fitted stably across five independent folds (defender weight 0.785–0.789, attacker weight
1.252–1.256). **Real, large, reproducible, and predictively worthless at this sample size.** Making it time-varying
is HARM, not merely useless.

**The hypothesis that Part 4 failed because it normalised the asymmetry away is now unsupported.** Declaration 4
recorded it as a candidate explanation. A1 restores exactly that discarded level asymmetry and does not beat a flat
constant, so the explanation does not survive its own test and is withdrawn.

**The level is the whole finding, and its direction is the opposite of what ships.** Pre-plant is 1.0 by definition;
the evidence puts post-plant at 0.2–0.4. The shipped model raises post-plant kills to 1.00–1.85. Not a
mis-calibration — a sign error. The mechanism that fits: the plant is the decisive event, and once it lands the
timer does most of the work, so kills after it move the outcome less than the kills that decided whether the plant
happened at all.

**A limit on the evidence, declared in advance and now reached.** `F-0.00` — post-plant kills contributing nothing
at all — cannot be distinguished from the fitted constant. Declaration 3 pre-committed that zero is never shippable
whatever it measured, because a decisive duel scoring nothing contradicts the standing constraint that no kill is
worth nothing. That commitment is load-bearing now rather than decorative.

#### Recommendation for version 4

1. **Replace the post-plant regime with a flat constant in the 0.2–0.4 region** — no ramp, no plant+38..45 override.
   It is simpler than what ships, it is the only change with evidence behind it, and `Ff2 vs Lf2` says the ramp's
   shape cannot be told from flat.
2. **Take the three Tier A correctness fixes, using `P3a` (cap the factor at plant+45) rather than `P3b`** — P3a
   addresses 634 rows of the defect against P3b's 45, and the earlier entry establishes why.
3. **Ship no structure**: no state table, no differential grouping, no side asymmetry, no time shape.
4. **Do not ship zero**, per the standing constraint and declaration 3.

Scale, so none of this is oversold: the baseline pooled out-of-fold log loss is 0.6729 against a coin flip's 0.6931,
and the whole scoring system buys about 0.02. The best contrast here is 9.3e−05, about **1/215th** of that. The
honest framing is "stop overpaying post-plant kills", not "this transforms the score". What justifies the version
bump is the correctness fixes plus a sign error, not the size of the log-loss gain.

Version 4 remains unstarted: it is a separate release with its own runbook, and `IMPACT_CALCULATION_VERSION` stays 3.

### 2026-09-20 (CORRECTION) — "INCONCLUSIVE" was read as "does not help" without checking whether the test could see the arm

The owner asked how the side-asymmetric arm lost, and whether the logic was flawed. It was. This entry records the
flaw, what it invalidates, what survives, and the protocol gate that should have existed from the first declaration.

**The flaw.** Every arm is scored as the fixed composite `impact_diff` with **one free coefficient**. Any change that
amounts to a rescale of `impact_diff` is absorbed entirely by that coefficient and produces a contrast of zero — not
because the change is worthless, but because the estimator cannot see it. The verdict vocabulary has no word for
that case, so it comes back INCONCLUSIVE, which is then read as "the effect does not help". **Those are different
statements and the entries above conflated them.**

**The diagnostic, which costs nothing and was never run.** The R² between an arm's `impact_diff` and its
comparator's bounds what any contrast can resolve, before a single bootstrap draw:

| contrast | R² | residual variance | resid sd / signal sd | reported verdict |
|---|---:|---:|---:|---|
| `F-0.4` vs `P0` | 0.992660 | **0.734%** | 8.57% | IMPROVEMENT |
| `P6` vs `P0` | 0.997802 | **0.220%** | 4.69% | INCONCLUSIVE |
| `F-1.0` vs `P0` | 0.998927 | **0.107%** | 3.28% | **IMPROVEMENT** |
| **`A1` vs `F-0.40`** | **0.999502** | **0.0498%** | **2.23%** | INCONCLUSIVE |

`A1 = 0.99672 × F-0.40 − 2.53`. It is a 0.997 rescale of the arm it was compared against, plus a residual carrying
2.2% of the signal's spread.

**The demonstrated detection floor is 0.107%** — `F-1.0` registered a clear IMPROVEMENT at that separability. `A1`
sits at 0.0498%, **below the lowest separability at which this harness has ever detected anything.**

#### What is withdrawn

**The claim that the side asymmetry does not help.** The RESULT entry above says of A1: "Real, large, reproducible,
and predictively worthless at this sample size," and calls it "the sharpest negative." That is not supported. The
contrast is arithmetically correct and the verdict INCONCLUSIVE is correct; the **interpretation** is wrong. The
supportable statement is that **A1 as parameterised is collinear with its comparator and the contrast is
uninformative** — the test could not have detected the effect had it been there.

**The conclusion drawn from it about Part 4** is withdrawn with it. That entry said A1's failure retires the
hypothesis that Part 4 lost because it normalised the per-side level away. A1 never tested that hypothesis with any
power, so the hypothesis returns to open.

**The count of "four structural models each lost to a constant" is wrong.** A1 did not lose; it was not measured.

#### What survives, and why

- **The level results stand.** `F-0.4 vs P0` at 0.734% residual variance is the most separable contrast in the whole
  investigation — 15x A1's — and it registered. The bracketing, the monotone curve and the 0.2–0.4 region are
  unaffected.
- **Part 4's null stands.** `P6 vs P0` at 0.220% had **twice** the separability of `F-1.0`, which the harness
  detected. It had room to be seen and was not seen. That null is evidence, not a power failure.
- **The Tier A correctness fixes are unaffected** — they were always argued a priori and measured only for harm.

#### The parameterisation problem underneath

Worth stating because it is the deeper reason A1 collapsed to a scalar. The measured asymmetry is a claim about
**two teams' stakes in the same duel** — in a 1v1 at 38–45s the attacker's death costs 50.0pp while the defender's
costs 4.0pp. But `_time_factor` returns **one number per event**, applied to the killer's credit and the victim's
debit alike, so an arm built on it can only say "this event counts more". Within a round those re-weightings largely
cancel in the differential, which is precisely why A1 came out a near-rescale. **Expressing a two-sided stake
asymmetry requires a scorer that can charge the two sides differently for the same event** — a change to the scoring
interface, not a new constant inside the existing one. That is why no arm reachable through this wrapper could have
tested it.

#### Protocol gate, declared now for every future arm

1. Before any bootstrap, compute R² between the arm's `impact_diff` and its comparator's.
2. An arm whose residual variance is **below the smallest value at which this harness has detected an effect**
   (currently **0.107%**, set by `F-1.0`) is reported as **UNTESTABLE**, never INCONCLUSIVE. UNTESTABLE is not a
   licence to ship and not evidence against; it means the question was not asked.
3. The floor is empirical and moves as more arms register; it is recorded with each result so later entries can see
   which floor applied.

This gate would have flagged `A1` before it ran, and would have saved the arm from being built in a form that could
not carry its own hypothesis.

#### Addendum — the differential arms, split by comparator

The same gate applied to the remaining nulls, and it separates two claims the RESULT entry ran together.

| contrast | residual variance | status under the gate |
|---|---:|---|
| `P5` vs `P0` | ~0.22% (as `P6` vs `P0`) | testable — the null stands |
| `P5b` vs `P0` | ~0.22% | testable — the null stands |
| **`P5` vs `P6`** | **0.0132%** | **UNTESTABLE** — 8x below the floor |
| **`P5b` vs `P6`** | **0.0336%** | **UNTESTABLE** — 3x below the floor |

So **"the differential regrouping does not beat the SHIPPED model" survives** — those contrasts had the same
separability as `P6 vs P0`, which sits at twice the demonstrated floor. **"The differential regrouping does not beat
Part 4" is withdrawn**: `P5` and `P5b` differ from `P6` only in the pooling ladder, and the resulting scores are
0.999+ correlated, so those two contrasts never had the power to separate them.

That also revises the declaration-2 framing of `P5b` as "the decisive test of the owner's hypothesis". It was
decisive against the shipped ramp and inert against Part 4, which is the comparator the hypothesis was actually
about.

**Corrected count.** Of the structural models: **one** (Part 4's own shape, `P6 vs P0`) has a credible null against
the shipped model. The differential regroupings have credible nulls against the shipped model and untestable ones
against Part 4. The side asymmetry has no test at all. "Four structural models each lost to a constant" overstated
the evidence by three.

### 2026-09-20 (DECLARATION 5) — does A1 carry information F-0.40 does not?

Declared before running. The paired-loss contrast could not answer this: with one free coefficient on `impact_diff`,
log loss is invariant to an affine rescale, and `A1 = 0.99672 x F-0.40 − 2.53` with R² 0.999502. The arms make
near-identical predictions by construction. This entry asks the question that test could not.

**The estimand.** Not "is A1's loss lower" but "does A1's `impact_diff` earn a non-zero coefficient **beyond**
F-0.40's". Two nested models, both out-of-fold on the same folds:

```
baseline     y ~ impact_diff[F-0.40] + controls
incremental  y ~ impact_diff[F-0.40] + impact_diff[A1] + controls
```

Target T2 and its control set unchanged, same corpus and fold assignment (`3198:f9a31bb2df2586ec`,
`cebae50f85e94736`), L2 selected per fold per model by inner 3-fold CV on training matches only, contrast
`loss(incremental) − loss(baseline)` with a 2,000-draw paired match-clustered bootstrap, positive worse.

**Why this has power where the paired test did not.** The second column is fitted on exactly the part of A1 the
first column does not explain — the residual carrying 2.2% of the signal's spread, which the paired-loss comparison
discarded. It does not violate the fixed-composite rule: both terms are frozen scoring configurations, and nothing
searches over the owner's locked weights A/B/C/D.

**Also reported, whatever the verdict:** the fitted coefficient on A1's column per fold, its sign and its stability.
A coefficient that flips sign across folds is noise being fitted, and will be reported as such even if the loss
improves.

#### Decision rule

- **IMPROVEMENT** — A1 carries information F-0.40 does not. The side asymmetry is real **and recoverable**, and the
  right response is a scoring interface that can charge the two sides differently for the same event.
- **INCONCLUSIVE** — no evidence the residual carries signal. The hypothesis stays **open but unsupported**; it is
  not refuted, because a per-event multiplier is a weak encoding of a two-sided stake asymmetry and this tests the
  encoding as much as the idea.
- **HARM** — the extra column costs out-of-fold, i.e. the residual is noise the model overfits.

**What IMPROVEMENT does NOT license, pre-committed here.** It does not mean ship A1. A two-composite model is not a
scoring configuration — the scorer emits one number per player-round, and this test deliberately uses two. A positive
result motivates a **design change**, and the design change then needs its own declaration and its own arm. Nothing
here may be read as evidence for activating A1 or any per-event side multiplier.

**Prediction.** INCONCLUSIVE. The within-round cancellation that flattened A1 into a rescale is structural — a kill
credits one side and debits the other through the same factor — so little of the 50pp-vs-4pp asymmetry should
survive into the round differential at all, however the test is posed. An IMPROVEMENT would be strong evidence that
the information is recoverable and the encoding, not the idea, was the problem.

### 2026-09-20 (RESULT, declaration 5) — A1 carries nothing F-0.40 does not; only the coefficient SUM is identified

Run as declared, on the same corpus and folds. Prediction held.

```
loss(base + A1) − loss(base)   +8.390625e-06  [−4.221189e-06, +2.158141e-05]   INCONCLUSIVE
```

**The coefficients are the real finding**, and they were pre-committed to be reported whatever the loss did:

| fold | β on `F-0.40` | β on `A1` | **sum** | L2 |
|---|---:|---:|---:|---:|
| 0 | +0.2665 | +0.0436 | **+0.3101** | 10.0 |
| 1 | +0.1959 | +0.1059 | **+0.3018** | 10.0 |
| 2 | +0.3706 | −0.0725 | **+0.2981** | 10.0 |
| 3 | +0.7990 | −0.5126 | **+0.2864** | 0.1 |
| 4 | +0.6181 | −0.3109 | **+0.3072** | 1.0 |

Each coefficient swings across a range of ~0.60 and **the sum is stable to 0.024 (CV 3.1%)**. The sign on A1's
column flips across folds — twice positive, three times negative — which the declaration named in advance as noise
being fitted. **The model cannot identify the split, only the total.** That is the textbook signature of two
predictors carrying the same information, and it is a stronger statement than the loss contrast: it is not that
A1's extra column fails to help, it is that the estimator cannot tell the two columns apart at all.

#### Verdict under the declared rule

INCONCLUSIVE, so: **no evidence the residual carries signal. The hypothesis stays open but unsupported — not
refuted.** The declaration fixed that reading in advance precisely because a per-event multiplier is a weak encoding
of a two-sided stake asymmetry, and this tests the encoding at least as much as the idea.

**What is now established about the encoding, rather than the idea.** Three independent diagnostics agree:
`A1 = 0.99672 × F-0.40 − 2.53`; R² 0.999502 with 0.0498% residual variance, below the harness's demonstrated
detection floor of 0.107%; and now, only the coefficient sum identified in a nested fit. **A per-event multiplier
cannot express a two-sided stake asymmetry, and no arm built on `_time_factor` ever could.** The cancellation is
structural: one kill credits the killer and debits the victim through the same scalar, so the two sides' differing
stakes never reach the round differential.

#### What would actually test the owner's observation

The measured asymmetry is real and large — in a 1v1 at 38–45s the attacker's death costs 50.0pp of win probability
while the defender's costs 4.0pp — and none of the above touches it. Testing it requires the scorer to charge the
two sides **differently for the same event**, which means `_time_factor` returning a pair rather than a scalar, or
the kill and death legs taking separate factors keyed on their own side. That is a change to
`app/scoring/impact.py`'s interface, out of scope for this measurement session, and it needs its own declaration
with its own arm.

**Standing conclusion unchanged.** Nothing here revises the level finding, the Tier A fixes, or the recommendation
for version 4. It closes one methodological question and reopens one design question.

### 2026-09-20 (CORRECTION + DECLARATION 6) — a kill should be worth what it changes

**First, a correction to this session's own framing.** Declaration 4 and the entries around it describe the 21.2pp
vs 74.1pp comparison as "the same event at the same second, worth 3.5x more to one side than the other". **That is
wrong.** Those are two *different* events reachable from the same state — the attacker killing the defender
(1v1 -> 1v0) and the attacker dying (1v1 -> 0v1). A single kill is **zero-sum in win probability**: the killer's
team gains exactly what the victim's team loses. There is no two-sided asymmetry within one event, and the
prescription that followed from it — "`_time_factor` should return a pair" — does not follow.

What is true, and is a better target: **different events from the same state differ enormously in magnitude**, and
the scorer prices them all as `K(s) x T(t)` where `T` is side-blind and `K` comes from a graph verified spike-blind.

#### The redesign

Replace the post-plant leverage payout with the measured win-probability swing:

```
today   leverage contribution = K(s) x T(t)
D1      leverage contribution = S x clamp(D(a, d, t, victim_side), FLOOR, CEIL)
```

`D` is Part 4's own quantity — `V(a,d,t) − V(a−1,d,t)` for an attacker victim, `V(a,d−1,t) − V(a,d,t)` for a
defender victim — used **directly**, without the `D / mean_over_t D` normalisation that divided its level away.
Pre-plant is untouched; only the post-plant regime is replaced.

**Why this is not the double-count Part 4 avoided.** Part 4 kept `K` and divided `D` by its own time-mean precisely
so the two would not multiply. D1 does the opposite: it **replaces** `K x T` outright, so `K` is not applied to a
post-plant kill at all and nothing is counted twice. The state, the side and the clock all enter once, through the
measured swing.

**The pre-check, run before declaring and reported whatever it said** (the gate instituted 2026-09-20). Over the
153,450 post-plant kills with a supported `D`:

| | |
|---|---|
| `corr(D, K x T)` today | **+0.7306**, R² 0.534 |
| variance in what a kill is worth that today's payout does not capture | **46.6%** |
| `corr(D, K)` | +0.8230 |

Against A1's 0.05% unexplained, this has room by three orders of magnitude. It is event-level, not round-level, so
it is a necessary and not a sufficient condition — the round-differential R² gate still applies below.

#### Construction

- `V` and therefore `D` are built **per fold on training matches only**, exactly as P5/P6 do.
- **`FLOOR = 0.005` (0.5pp), `CEIL = 1.0`.** A floor is required: `D` runs to −19.6pp in thin cells, and the
  standing constraint is that no kill is worth negative Impact. The floor is a policy value, declared here, not
  fitted.
- **`S` is fitted per fold so that `Σ S·D` over training post-plant kills equals `Σ K x 0.40` over the same kills.**
  So D1 and `F-0.40` carry **identical total post-plant leverage** and differ only in how it is distributed across
  events. That is what isolates the redistribution from the level.
- Implemented through the existing wrapper as `T = S·D/K`, which makes the kill leg `S·D` and the death leg
  `S·D x traded_factor`, preserving the trade discount. The kill-order graph is verified symmetric under team
  relabeling (0 asymmetric edges), so `K` is recoverable from the alive counts alone.
- An unsupported cell (31 of 153,481 kills) falls back to the flat `0.40`, matching the comparator.
- Self-kills and phantom plants are excluded as everywhere else.

#### Rule

- **Primary contrast `D1 vs F-0.40`**, level-matched by construction. Secondary `D1 vs P0`.
- **The R² gate runs first.** If D1's round-level `impact_diff` has residual variance against `F-0.40` below
  **0.107%**, D1 is reported **UNTESTABLE** and no bootstrap is run.
- Tier B: ships only on IMPROVEMENT.
- Report the event-level redistribution regardless: how much payout moves, and to which states.

**Prediction.** D1 passes the gate (residual variance above 0.5%) and is **IMPROVEMENT**. Reasoning: this is the
first arm that changes what a kill is worth *as a function of state*, not a scalar re-weighting, and 46.6% of the
event-level variance is currently unpriced. If it fails the gate, then round aggregation destroys even a
redistribution this large, and that is a finding about the target rather than about the model.

### 2026-09-20 (DECLARATION 7) — three ways to read the scalar that do not depend on a grid search

Declared before running. All three come from the owner's question: is there a metric that can say something about
the time scalar, given that the paired-loss harness has proven blind to whole classes of change? Each estimates the
same quantity by different machinery, so agreement between them is evidence the grid search alone cannot supply.

#### Method A — the coefficient ratio

Split the round's leverage differential into its **pre-plant** and **post-plant** halves and fit both:

```
y ~ b_pre * leverage_diff[pre-plant] + b_post * leverage_diff[post-plant] + controls
```

**`b_post / b_pre` IS the time scalar**, read off directly with an interval, instead of searched for on a grid. The
shipped model asserts that ratio is 1.00–1.85; the flat arms estimated ~0.3 by search. Extracted through
`build_impact_rows_for_match`'s existing `kill_observer` hook, which reports the scorer's own per-kill values, so
nothing is re-derived. Target T2 and its controls, same folds, 2,000-draw bootstrap on the ratio.

**Declared caveat:** the two columns are not orthogonal — a round with more post-plant action has less pre-plant
action — so `corr(pre, post)` is reported beside the ratio, and a correlation near -1 would make the split
ill-conditioned and the ratio unreliable. That is reported whatever it says.

#### Method B — equalise the scalar from measured swings

No target, no folds, no log loss. Build `V(a, d, planted)` over **whole-round** second-by-second occupancy — not
just post-plant, which is all Part 4 ever covered — then compute the win-probability swing of every scored kill and
take

```
scalar = mean |dV| over post-plant kills  /  mean |dV| over pre-plant kills
```

This measures what a post-plant kill is worth relative to a pre-plant one, directly. It cannot be defeated by
collinearity or by a weak target, because it never predicts anything. Its weakness is the mirror image: it says what
the states are worth on average and nothing about whether re-weighting them helps a downstream model.

`planted` must be in the state because planting is itself a large jump in attacker win probability; omitting it
would attribute the plant's value to the kills around it.

#### Method C — target the round itself

Re-evaluate the arms on `y = did team A win THIS round`, with the context controls only and **without**
`round_result`, which is the label here rather than a nuisance. Run for `P0`, `F-0.40` and `F-0.30`.

**Declared caveat, stated before the numbers:** this is partly circular. Impact is computed from the kills that
decided the round, so any variant tracking "who won the fights" scores well, which is close to but not the same as
"which weighting reflects contribution". It measures something different from T2 and will not be reported as the
same quantity.

#### What would count

- **The three agreeing near a common value** is the strongest evidence available for a scalar, precisely because
  they share no machinery: a fitted coefficient ratio, a direct measurement, and a different target.
- **Disagreement is equally informative** and will be reported as such: if A and B say ~0.3 while C says ~1.0, the
  forward-looking and within-round questions have different answers, and the shipped 1.00–1.85 may be right for a
  question nobody has been asking.
- No constant is frozen by this entry, and none of these three is a shipping gate on its own.

**Prediction.** A and B both land in **0.2–0.5**, agreeing with the grid search. C lands **higher**, nearer 1.0,
because a within-round target rewards tracking the kills that decided that round and post-plant kills are
disproportionately the deciding ones. If C comes in near 0.3 as well, the case for the change is much stronger than
tonight's contrasts made it look.

### 2026-09-20 (RESULT, declarations 6 and 7) — the answer depends on the target, and that reframes everything

All four measurements complete. **They disagree, and the disagreement is the finding.**

| method | machinery | scalar it implies |
|---|---|---|
| grid search (T2, forward window) | loss contrasts over a grid | **~0.3** |
| **A** coefficient ratio (T2) | `b_post / b_pre`, fitted | **0.431** (sd 0.017, folds 0.41–0.46) |
| **B** direct swing (no target at all) | mean \|dV\| post / pre | **1.023** (median-based 0.874) |
| **C** round's own outcome | flat arms vs shipped | **favours the shipped ramp** |

Method C in full: `F-0.40 vs P0` **+6.953e−03 [+6.218e−03, +7.706e−03] HARM**; `F-0.30 vs P0`
**+8.428e−03 [+7.617e−03, +9.276e−03] HARM**. Note the magnitude — these are **~100x larger** than anything measured
on T2, because the round outcome is nearly determined by the kills that decided it. That is the circularity
declared in advance, visible in the numbers.

**A's split is well-conditioned**: `corr(pre_leverage, post_leverage) = +0.060`, so the two columns are nearly
orthogonal and the ratio is trustworthy as an estimate of what T2 wants.

#### The reconciliation

`K(s)` is **already correct**. Measured over the scored population: mean `K` is 1.018x larger post-plant, and the
measured win-probability swing is 1.023x larger. The kill-order bonus tracks what a kill is worth almost exactly,
without knowing anything about the spike. The time factor then multiplies by a further **1.264x** on average, and
nothing in the win-probability data justifies that.

So three of the four agree the shipped ramp **overpays** post-plant kills. They disagree on by how much, and the
disagreement tracks **which question is being asked**:

- **"Who decided the round in front of us?"** — B (1.02) and C (the ramp wins) say post-plant kills are worth at
  least as much as pre-plant ones.
- **"Who will win the rounds after this one?"** — the grid (~0.3) and A (0.43) say post-plant kills predict future
  rounds substantially less well.

Both are true statements about different quantities. Post-plant play decides the round it happens in, and predicts
subsequent rounds poorly — consistent with post-plant outcomes being driven more by position, timer and spike state
than by repeatable individual skill.

#### D1, and its gate

`D1 vs P0` **−8.716e−05 [−1.505e−04, −2.202e−05] IMPROVEMENT**. `D1 vs F-0.40` **+5.430e−06, and the gate says
UNTESTABLE** — residual variance 0.0625% against the 0.107% floor. So D1 beats the shipped model and is
**indistinguishable from a flat constant**, which the pre-check already implied: `corr(D, K) = 0.823`, so paying the
measured swing is close to paying `K` times a constant. **The redesign collapses onto the flat arm.** That is a
real result about the redesign, not a failure of it: it says the swing information is already carried by `K`.

**Process deviation, recorded.** Declaration 6 says the gate runs BEFORE any bootstrap. It did not — the contrast
was computed first and the gate after. The gate's verdict governs regardless, and `D1 vs F-0.40` is reported
UNTESTABLE rather than INCONCLUSIVE.

#### Predictions, scored

| declared | outcome |
|---|---|
| A lands 0.2–0.5 | **held** — 0.431 |
| B lands 0.2–0.5 | **FAILED** — 1.023 |
| C lands higher, nearer 1.0 | **held** directionally — C favours the shipped ramp outright |
| D1 passes the gate and is IMPROVEMENT | **half failed** — IMPROVEMENT vs P0, but UNTESTABLE vs its level-matched comparator |

#### What this does to the version 4 recommendation

**It suspends it.** The earlier entries recommend replacing the post-plant regime with a flat constant near 0.3.
That recommendation rests entirely on T2, a forward-looking target, and **method C says the same change is HARM on
the round's own outcome by a margin two orders of magnitude larger than the gains that motivated it.**

The choice is no longer statistical. It is: **what is Impact for?**

- If Impact rates contribution to the match being played, the round-outcome reading governs, and flattening is
  wrong. The defensible change shrinks to `T ~ 1.0` — remove the *ramp's growth* and the plant+38..45 override,
  keep post-plant kills at parity with pre-plant ones, which is what B measures and what `F-1.00` already showed
  beats the shipped ramp on T2 as well.
- If Impact forecasts future performance, the T2 reading governs and ~0.3–0.43 is right.

**`T = 1.00` is the only value that is not contradicted by any of the four measurements**: B measures it directly,
A and the grid say "well below 1.26" which it satisfies, C prefers the shipped ramp but `F-1.00` was never run on
C. **That gap should be closed before any version 4 is specified** — run `F-1.00` on the round target.

The Tier A correctness fixes are untouched by any of this and remain recommended.

### 2026-09-20 (DECLARATION 8) — close the `F-1.00` gap on the round target, and bracket C's optimum

Declared before running. Declaration 7's RESULT left exactly one measurement unrun, and this entry runs it.

Restated so this entry stands alone: `T = 1.00` is the only post-plant scalar no method contradicts — B measures it
directly (1.023), A (0.431) and the grid (~0.3) say "well below the shipped 1.264", which 1.00 satisfies, and C
prefers the shipped ramp over both flat arms it was given — **but neither of those arms was 1.00**. C was run at
0.40 and 0.30 only, both far below the shipped level, so C has never been asked about parity.

#### What is run

Method C's harness unchanged — `y = did team A win THIS round`, context controls only, no `round_result` — extended
from three arms to seven:

| arm | why it is in |
|---|---|
| `P0` | shipped, the comparator |
| `F-0.30`, `F-0.40` | already measured on C — **re-run as a reproduction check**, not as new evidence |
| **`F-1.00`** | **the declared gap** |
| `F-0.70` | between the arms that lost and 1.00; makes the level a curve rather than two points and an extrapolation |
| `F-1.26` | **level-matched** — mean shipped post-plant `T` is 1.264. Shipped level, no shape. |
| `F-1.60` | above the shipped level, so an interior minimum can be **bracketed** instead of inferred |

**Why more than the one arm the handoff named.** `F-1.00` alone returns a verdict on `F-1.00` and nothing else. If
it is HARM — which prediction 2 below says it will be — the handoff's proposed resolution fails and the very next
question is "then what level does C want?", which is these same seven replays run a day later. The level-matched
arm additionally splits what C likes about the ramp into **level** and **shape**, a decomposition no arm run on
this target so far can make.

#### The gate runs first, as declared

R² between each arm's round-level `impact_diff` and `P0`'s is computed and reported **before** that arm's
bootstrap. Declaration 6 recorded a deviation on exactly this point; this entry does not repeat it.

Standing floor: **0.107%** residual variance. Recorded caveat, stated in advance: that floor is a property of
**T2's** detection power. C's contrasts run ~100x larger, so C's own floor is almost certainly well below 0.107%,
and under gate clause 3 a decisive verdict beneath the standing floor on this target **moves the floor** rather
than being suppressed as UNTESTABLE. Residual variance and verdict are both reported for every arm whatever they
say.

#### Predictions, all falsifiable

1. **Reproduction is bit-for-bit.** `paired_oof_log_loss_delta` defaults to `seed=0` and mode C passes no seed, so
   `P0`, `F-0.40` and `F-0.30` must return their declaration-7 log losses and intervals exactly. Any drift means
   the corpus or the scorer moved, and the new arms are not comparable to declaration 7.

2. **`F-1.00` is HARM on C, and small — point estimate in `[+2e-4, +2e-3]`.** This contradicts the handoff's stated
   hope and is declared before the number is seen. C's two measured points fit a quadratic in `k` almost exactly;
   solving `c(0.30-k*)^2 = 8.428e-3` against `c(0.40-k*)^2 = 6.953e-3` gives **`k* = 1.390`** and **`c = 7.089e-3`**.
   C's optimum therefore sits slightly **above** the shipped mean `T` of 1.264 — not at 1.00. That model puts
   `F-1.00` at **+1.1e-3**: roughly one sixth of `F-0.40`'s harm, but still several times the interval half-width,
   so it should register rather than land INCONCLUSIVE.

3. **The rest of the curve, from the same two-point model:** `F-0.70` at **+3.4e-3**, `F-1.26` at **+1.2e-4**,
   `F-1.60` at **+3.1e-4**. Scored against the measured values as a curve-shape prediction. The model is fitted on
   two points and assumes a quadratic; it is offered as a falsifiable guess, not as a result.

4. **`F-1.26` may return UNTESTABLE.** `F-1.0 vs P0` sits at 0.107% residual variance on T2, and `F-1.26` is nearer
   `P0` still. If it lands below the floor, that is itself the finding: the shipped ramp's **shape** is
   arithmetically near-indistinguishable from its **level** through this wrapper, and the post-plant question
   collapses to choosing a level.

5. **The minimum is interior** — `F-1.60` loses to `F-1.26`. If loss instead falls monotonically through 1.60, the
   grid is pinned at its edge, no constant has been fitted, and the level is declared inconclusive and re-run
   wider. The grid-edge rule applies.

#### What this entry does not do

It freezes no constant, does not touch `webapp/app/`, and does not move `IMPACT_CALCULATION_VERSION` off 3. It
closes one gap, and either supports or refutes `T = 1.00` as the defensible within-round value.

#### Addendum to declaration 8, written while the run was replaying and before any C result was seen

**Prediction 2 is under-identified, and the T2 grid proves it.** The two-point solve reads
`delta(k) = c(k - k*)^2` and so silently forces a third parameter to zero: it assumes **the best flat arm exactly
ties `P0`**. The honest model has an offset, `delta(k) = c(k - k*)^2 + d`, and two points cannot identify three
parameters.

The flat-arm grid already run on **T2** settles whether `d = 0` is safe. It is not:

| k | `F-k vs P0` on T2 | verdict |
|---:|---:|---|
| 0.4 | −9.259e−05 | IMPROVEMENT |
| 0.6 | −8.633e−05 | IMPROVEMENT |
| 0.8 | −7.285e−05 | IMPROVEMENT |
| 1.0 | **−5.270e−05** | **IMPROVEMENT** |
| 1.2 | −2.494e−05 | IMPROVEMENT |
| 1.4 | +9.469e−06 | INCONCLUSIVE |

A quadratic through those six points fits to a **maximum residual of 1.6e−07 against a curve spanning 1.0e−04** —
0.16% of the range. So:

- **The quadratic form is validated**, and independently: its vertex lands at **k\* = 0.3221**, against the grid
  search's own selection of 0.3–0.4 by a completely separate mechanism. The shape assumption behind prediction 2 is
  sound.
- **The `d = 0` assumption is refuted.** T2's best flat arm beats `P0` by **d = −9.31e−05**. Every T2 delta in the
  table is negative, so the two-point solve applied there does not merely mis-estimate the vertex — it **fails
  outright**, asking for the square root of a negative number.
- T2's curve crosses zero at **k = 1.349**: on the forward target, every flat constant below ~1.35 beats the
  shipped ramp.

**What this does to the declared predictions.** Prediction 2's *point value* (+1.1e−3) and the derived vertex
(k\* = 1.390) are conditional on `d = 0` and are scored as conditional. Its *direction* — `F-1.00` is HARM on C —
holds only if `d >= 0`, i.e. only if no flat arm beats the shipped ramp on C. That is an open question this run
answers rather than an assumption it is entitled to. Prediction 3's curve values inherit the same condition.

**What survives untouched:** predictions 1 (bit-for-bit reproduction), 4 (`F-1.26` may be UNTESTABLE) and 5 (the
minimum is interior) do not depend on the offset at all.

**And this is now the sharpest argument for the seven arms.** Six non-`P0` points identify `c`, `k*` and `d`
together, with three degrees of freedom left over to test the quadratic form on C the same way it was just tested
on T2. The single arm the handoff named could not have identified any of them.

**One asymmetry to carry into the result, stated before the numbers.** `F-1.00` is already a **measured
IMPROVEMENT on T2** (−5.270e−05, interval excluding zero). So whatever C returns, `T = 1.00` is not a compromise
between one target that wants it and one that does not — it is a value the forward-looking target actively
prefers to the shipped ramp, being tested against the one target that might not.

### 2026-09-20 (RESULT, declaration 8) — `T = 1.00` is refuted, and the ramp's **shape** is the defect

**The gap is closed, in the direction the handoff did not want.**

```
F-1.00 vs P0, round's own outcome:  +9.823565e-04  [+6.548e-04, +1.304e-03]  HARM
```

The interval excludes zero. The value declaration 7 left standing as "the only one no measurement contradicts" **is
contradicted.** `T = 1.00` is not uncontradicted, and version 4 cannot be specified around it on the strength of
"nothing says no".

#### Reproduction: bit-for-bit

`P0`, `F-0.40` and `F-0.30` returned their declaration-7 values **exactly** — log loss to all 17 significant
figures, and `point`, `lo` and `hi` identical on both contrasts. Prediction 1 held. The corpus (3,198 matches /
67,453 rounds / 499,093 kill_events, alembic 0010, match-id md5 `1d639f01ece40d3cf43b7b94352edccc`) and the scorer
are where declaration 7 left them, so the four new arms are directly comparable to the three old ones. `P0`
reproduced a further two times in the independent runs below.

#### The curve

| k | `F-k vs P0` | 95% interval | verdict | resid. var. |
|---:|---:|---|---|---:|
| 0.30 | +8.427835e−03 | [+7.6167e−03, +9.2757e−03] | HARM | 0.9092% |
| 0.40 | +6.953219e−03 | [+6.2181e−03, +7.7058e−03] | HARM | 0.7340% |
| 0.70 | +3.417382e−03 | [+2.9005e−03, +3.9310e−03] | HARM | 0.3332% |
| **1.00** | **+9.823565e−04** | **[+6.5482e−04, +1.3043e−03]** | **HARM** | 0.1073% |
| 1.26 | −4.118715e−04 | [−6.2024e−04, −2.0070e−04] | IMPROVEMENT | 0.0407% |
| 1.60 | −1.446362e−03 | [−1.6914e−03, −1.2084e−03] | IMPROVEMENT | 0.1164% |

Monotone across the whole range, decelerating. Log losses: `P0` 0.092374, and 0.100802 / 0.099327 / 0.095791 /
0.093356 / 0.091962 / 0.090927 for k = 0.30 … 1.60.

#### What the extra arms bought: **level** and **shape** separate

This is the finding the single named arm could not have produced.

`F-1.26` is **level-matched** — 1.26 is the shipped ramp's own mean post-plant `T` (1.264). It carries the shipped
level and none of the shipped shape. It is an **IMPROVEMENT on C** (−4.119e−04, interval excluding zero).

So on the target that *prefers* the shipped ramp to every flat arm below it, **holding the level fixed and deleting
the shape still helps.** The ramp's shape is not merely unsupported by measurement, as the earlier entries had it —
it is **worse than no shape at all**, on the one target that was supposed to be defending it.

The two questions therefore come apart cleanly:

- **Shape** — both targets want it gone. Settled, and it needs no answer to "what is Impact for".
- **Level** — the targets disagree, and that disagreement is the real open question.

#### The protocol gate has a hole: the floor is per-target, not global

`F-1.26 vs P0` registered a **decisive** IMPROVEMENT at **0.0407%** residual variance — comfortably below the
**0.107%** standing floor, and below `A1`'s 0.0498% and `D1`'s 0.0625%, both of which were reported UNTESTABLE.

The floor is **a property of the target, not of the harness.** C's target is ~30x more determined by its own
features than T2's, so C resolves separations T2 cannot. Concretely:

- On **T2** the floor stands at 0.107%. `A1` and `D1` remain UNTESTABLE; nothing here rescues them.
- On **C** the demonstrated floor is now **at most 0.0407%**.

Gate clause 3 anticipated a moving floor but wrote it as one global number. **It should be recorded per target**,
and the declaration-6 addendum's single-column table should be read as "the T2 floor" throughout. Recorded as a
defect in the gate's statement, not in any verdict it produced.

#### Scale: the "two orders of magnitude" was a property of the target, not of the effect

Declaration 7 suspended the version-4 recommendation partly because C's harms are "~100x larger than anything
measured on T2". That comparison is between raw log-loss deltas on two targets whose headroom differs by 30x.

| | what the whole scoring system buys | best/worst flat-arm effect | as % of headroom |
|---|---:|---:|---:|
| T2 | 0.0202 (0.6931 → 0.6729) | −9.259e−05 gain | **0.458%** |
| C | 0.6007 (0.6931 → 0.0924) | +6.953e−03 harm at k=0.40 | **1.157%** |

`F-0.40`'s harm on C is **2.5x** T2's best gain in headroom terms, not the 75x the raw numbers suggest. And
`F-1.00`'s harm on C is **0.36x** T2's best gain — in headroom terms it **loses less on C than it gains on T2**.
The asymmetry that drove the suspension is largely an artifact of comparing two targets' log losses directly.

#### Predictions, scored

| declared | outcome |
|---|---|
| 1. reproduction is bit-for-bit | **held** — exact, on all three arms |
| 2. `F-1.00` is HARM on C, in [+2e−4, +2e−3] | **held on both counts** — +9.82e−04. The conditional point estimate (+1.1e−3) landed within 12% despite resting on an offset withdrawn mid-run |
| 3. curve values | `F-0.70` **1.01x** (+3.40e−3 predicted vs +3.42e−3). `F-1.26` and `F-1.60` **wrong in sign**, exactly as the addendum said they would be once `d != 0` |
| 4. `F-1.26` may be UNTESTABLE | **held mechanically, and it is the interesting failure** — 0.0407%, below the floor, and it registered anyway |
| 5. the minimum is interior | **FAILED** — still falling at 1.60, grid pinned at its upper edge. The declared re-run-wider rule fires |

The addendum's withdrawal of prediction 2's *identification* was correct and load-bearing: the two-point solve put
C's vertex at 1.390 and a six-point fit puts it at **1.685**, outside the grid.

#### The re-run wider, and where C's optimum actually is

Prediction 5's failure fired the grid-edge rule, so a second grid ran at k = 1.9, 2.2, 2.6. **It was killed by the
OS for memory pressure after completing all four replays but before its bootstraps**, so these three arms have
**point estimates and no intervals**. Recorded as such; they are not registered contrasts.

| k | log loss | `F-k vs P0` | status |
|---:|---:|---:|---|
| 0.30 | 0.100802 | +8.428e−03 | bootstrapped, HARM |
| 0.40 | 0.099327 | +6.953e−03 | bootstrapped, HARM |
| 0.70 | 0.095791 | +3.417e−03 | bootstrapped, HARM |
| 1.00 | 0.093356 | +9.824e−04 | bootstrapped, **HARM** |
| 1.26 | 0.091962 | −4.119e−04 | bootstrapped, IMPROVEMENT |
| 1.60 | 0.090927 | −1.446e−03 | bootstrapped, IMPROVEMENT |
| **1.90** | **0.090614** | **−1.760e−03** | point only — **minimum** |
| 2.20 | 0.090733 | −1.641e−03 | point only |
| 2.60 | 0.091425 | −9.490e−04 | point only |

**The minimum is bracketed and the grid is no longer pinned.** `F-1.9` beats both neighbours; a three-point
parabola through 1.6 / 1.9 / 2.2 puts the vertex at **k\* = 1.97**. Located loosely — the 1.9-vs-2.2 gap
(1.19e−04) is inside a typical bootstrap half-width, so the honest reading is **the optimum lies around 1.7–2.3**.
The 1.9-vs-1.6 gap (3.13e−04) is not, so "above 1.6" is secure.

The six-point quadratic had put the vertex at 1.685. It was wrong, as its fit quality warned: 1.02% residuals on C
against 0.16% on T2. **The quadratic form holds on T2 and does not hold on C** — a decelerating curve, not a
parabola. Any future extrapolation on this target should be treated as indicative only.

#### The reconciliation: B is the anchor, and the other two bracket it

Putting the four estimates of "what a post-plant kill is worth relative to a pre-plant one" on one line:

| estimate | machinery | scalar |
|---|---|---:|
| T2 optimum | forward 3-round window, fitted composite | **0.32** |
| **B** | **mean \|dV\| ratio — no target, no folds, no loss** | **1.02** |
| shipped ramp, mean | the model in production | 1.26 |
| C optimum | the round's own outcome | **~1.97** |

**B is the only one that measures the quantity directly**, and the two target-based estimates sit on either side of
it — T2 at roughly a third of B, C at roughly double. That is the signature of two biases pulling opposite ways,
not of three disagreeing measurements:

- **T2 undershoots** because post-plant play predicts *later rounds* poorly. It is answering a forecasting
  question, and post-plant outcomes are driven by position, timer and spike state more than by repeatable skill.
- **C overshoots** because of the circularity declared in advance in declaration 7. Its target is nearly determined
  by its own features — pooled out-of-fold log loss **0.0924** against a coin flip's 0.6931, i.e. the model is
  about **91% confident and right**. Post-plant kills are disproportionately the *last* kills, so up-weighting them
  reconstructs the label better almost tautologically. C's ~1.97 is an estimate of **which kills are most
  diagnostic of the round result**, which is not the same quantity as which kills were worth the most.

C is not noise and should not be discarded — it is the operationalisation of the within-round reading, and
declaration 7 committed in advance to reporting it. But **its optimum is a biased estimate of the scalar**, and the
size of the bias is visible: 1.97 against B's directly-measured 1.02.

#### What is now settled without needing "what is Impact for"

**The shape goes.** `F-1.26` carries the shipped ramp's own mean level and none of its shape, and it is an
IMPROVEMENT on the target that prefers the ramp to every flat arm below it. Both targets want the ramp's growth and
the plant+38..45 override removed. No weighting of the two targets changes this.

**The level is the open question, and it is narrow.** A flat constant beats the shipped ramp on:

- **T2** for k < 1.349 (bracketed by measured IMPROVEMENT at 1.2 and INCONCLUSIVE at 1.4)
- **C** for k > ~1.18 (bracketed by measured HARM at 1.00 and IMPROVEMENT at 1.26)

giving a **joint window of roughly 1.18 < k < 1.35** in which one constant improves both targets at once.

**The honest caveat, and the one measurement that closes it.** There is as yet **no single k measured as an
IMPROVEMENT on both targets.** T2's highest measured improvement is k = 1.2; C's lowest is k = 1.26. They are
adjacent and do not overlap. The missing cell is **T2 at k = 1.26** — the run that was killed for memory. Its fit
value is −1.54e−05, an improvement, but it is a fit.

```bash
cd webapp
DATABASE_URL="postgresql+psycopg2://postgres@localhost:5434/valo_v4" \
  ./.venv313/Scripts/python.exe scripts/run_postplant_v4_report.py \
    --out <DIR> --arms "P0,F-1.2,F-1.26,F-1.4"
```
`F-1.2` and `F-1.4` are in that list as reproduction checks against declaration 5's `contrasts_flat2.json`. The
killed run got through its identity gate (**PASSED**, 67,251 observations identical) and banked `oof_P0.npz`, and
it re-confirmed `dataset_fingerprint 3198:f9a31bb2df2586ec` / `fold_mapping_hash cebae50f85e94736`, so a rerun
pointed at that directory resumes rather than restarting.

#### What this does to the version 4 recommendation

It **unblocks the part that was blocked, and narrows the part that was not.**

1. **Remove the ramp's shape and the plant+38..45 override.** Settled by measurement on both targets. This no
   longer waits on the owner's answer to "what is Impact for".
2. **Replace it with a flat constant in the joint window, 1.18–1.35.** The shipped ramp's own mean level, 1.26,
   sits inside it. Pending the one measurement above.
3. **`T = 1.00` is out.** It is HARM on C with an interval excluding zero, and it sits below the joint window.
4. **0.30–0.40 is out too.** It was the earlier entries' recommendation, and it is 1.16% of C's headroom worse
   against 0.46% of T2's headroom better — the worst net of any arm tested.
5. Tier A fixes with `P3a` are untouched by all of this and remain recommended.

Note what has changed about the *character* of the recommendation. The earlier entries proposed moving the level a
long way (1.26 → 0.3) on T2's authority alone. This one proposes **leaving the level almost exactly where it is and
deleting the shape**. Impact scores will move less, and the case no longer depends on which target is preferred.

`IMPACT_CALCULATION_VERSION` stays **3**, `git diff webapp/app/` is empty, and no constant is frozen by this entry.
Row motion for `F-1.26` has not been measured and should be, via `postplant_v4_row_motion.py`, before any version-4
runbook is written.

### 2026-09-20 (RESULT, declaration 8 — addendum) — both killed runs completed; the joint-window claim is corrected

Run sequentially at the owner's instruction, one Python process at a time, while a game held ~2GB. Both finished.

#### The wide grid, now registered rather than salvaged

| k | `F-k vs P0` on C | 95% interval | verdict | resid. var. |
|---:|---:|---|---|---:|
| 1.90 | −1.760123e−03 | [−2.1330e−03, −1.3937e−03] | IMPROVEMENT | 0.3203% |
| 2.20 | −1.641140e−03 | [−2.1568e−03, −1.1331e−03] | IMPROVEMENT | 0.6373% |
| 2.60 | −9.486863e−04 | [−1.6587e−03, −2.4499e−04] | IMPROVEMENT | 1.2122% |

All three clear the gate comfortably. **C's vertex is confirmed at k\* = 1.9675**, minimum bracketed (`F-1.9` beats
`F-1.6` by −3.138e−04 and `F-2.2` by −1.190e−04). Declaration 8's grid-edge rule is discharged.

**The salvage was sound.** The killed run's log losses, transcribed from its flushed run log, reproduced to every
printed digit (0.090614 / 0.090733 / 0.091425), and the point estimate derived as `loss(arm) − loss(P0)` — −1.760e−03
— matched the measured −1.7601230e−03. Recovering point estimates from a run log when the JSON never lands is a
valid technique, worth keeping.

#### `F-1.26` on T2 — and the claim it does not support

```
F-1.26 vs P0, target T2:  -1.515723e-05  [-3.189432e-05, +1.117182e-06]  INCONCLUSIVE
```

The fit predicted −1.5435e−05 and the measurement came in at −1.5157e−05, **1.8% off on the point estimate**. But
the interval spans zero — barely, upper bound +1.12e−06 — so the verdict is **INCONCLUSIVE, which by this ledger's
own vocabulary is never "no harm found".**

**The joint-window claim as the RESULT entry above framed it is therefore not established.** That entry said a
constant in 1.18–1.35 "improves both targets at once", and predicted this measurement would make it fully measured.
It did not. **There is still no k measured as an IMPROVEMENT on both targets, and now there is a reason to think
there cannot be one.**

**Why it is structural, not bad luck.** The window is *defined* by the two targets' zero-crossings — C's at ~1.18,
T2's at ~1.35. Near a crossing an effect is small by construction. So any k inside the window is necessarily close
to zero on at least one target, and "a constant that decisively improves both" is **unachievable in principle
here**, not merely unmeasured. Running more arms inside the window cannot fix this.

#### What the measurement does support

At k = 1.26, in each target's own headroom:

| | effect | as % of that target's headroom |
|---|---:|---:|
| C | −4.119e−04, interval excludes zero | **0.0686% gain** |
| T2 | worst case +1.117e−06 (interval upper bound) | **0.0055% cost** |

**The decisive gain on one target is 12x the worst-case cost on the other**, and that cost is bounded by measurement
rather than assumed. The defensible claim is therefore *not* "improves both". It is:

> Replacing the ramp with a flat constant at the shipped ramp's own mean level **decisively improves the
> within-round target and costs the forward-looking target nothing measurable**, while being a strict
> simplification — one constant instead of a ramp plus an override.

That is still a shipping case. It is a weaker claim than the one it replaces, and it is the one the evidence bears.

**An unresolved alternative, flagged not answered:** `F-1.2` is a measured IMPROVEMENT on T2 (−2.494e−05, interval
excluding zero) where `F-1.26` is inconclusive, and C at 1.2 is unmeasured but interpolates to roughly −2.7e−05,
just past its crossing. **k = 1.2 may dominate k = 1.26** — one mode-C arm would settle it. Not run.

#### Two process failures worth recording

**1. `run_postplant_v4_report.py` silently drops unknown arms — twice over.** The first attempt requested
`P0,F-1.2,F-1.26,F-1.4`, **exited 0, and measured two of three**: `F-1.26` is on no grid, so it never entered
`ALL_ARMS` and produced no row at all. The two arms that did run were the reproduction checks, so the output looked
healthy. Worse, the obvious fix — adding it to `ALL_ARMS` — made it appear in the table as `NOT RUN` while still
never executing, because **`ALL_ARMS` is the REPORT loop and `SIMPLE_ARMS` is the REPLAY loop**. A second run was
burned on that. Fixed: `F3_GRID = (1.26,)` feeds `SIMPLE_ARMS`, and an unrecognised `--arms` entry is now a hard
`SystemExit` listing the known flat arms instead of a silent skip. Same family as `build_target` dropping rows —
**the harness quietly answering a smaller question than the one asked.**

**2. The T2 harness does not reproduce bit-for-bit.** `F-1.2` and `F-1.4` came back at −2.494432e−05 and
+9.467676e−06 against declaration 5's −2.494206e−05 and +9.469171e−06 — agreeing to **four significant figures**
with identical verdicts, but differing by ~2e−09. The two arms' drifts are unequal (−2.26e−09 vs −1.49e−09), so it
is not a shared `P0` offset; the likely source is the inner 3-fold L2 selection, which mode C does not perform
(it fixes `L2 = 1.0`). Immaterial to any verdict, but **"bit-for-bit" is true of mode C only** — the RESULT entry
above says so of mode C, correctly, and it must not be generalised to this harness.

### 2026-09-20 (DECLARATION 9) — ask the side-asymmetry question on the target that can hear it

Declared before running. This entry exists **only because of declaration 8's finding that the detection floor is a
property of the target, not of the harness.**

`A1` — the post-plant factor as a constant per victim side, at level 0.40 so `A1 vs F-0.40` isolates the split and
nothing else — was reported **UNTESTABLE** on T2: residual variance **0.0498%** against T2's demonstrated floor of
**0.107%**. The arm is a 0.997 rescale of its own comparator and the estimator is blind to a rescale.

But C's demonstrated floor is **at most 0.0407%**, set when `F-1.26` registered a decisive IMPROVEMENT there.
**0.0498% > 0.0407%.** If `A1`'s separability on C's row set is comparable to its separability on T2's, then the
question that could not be asked on the forward target **can be asked on the round-outcome target** — for the first
time in this investigation.

This is the single highest-value experiment the per-target floor unlocks, and it is the one the owner asked for.

#### What is run

| arm | why |
|---|---|
| `P0` | shipped, the comparator of record |
| `F-0.40` | **the comparator that matters** — same level, no split |
| `A1` | level 0.40, per-victim-side weights |

Target C (`y = did team A win THIS round`, context controls, no `round_result`), the same machinery declaration 8
used, so the result is directly comparable to that entry's nine-arm curve.

#### The weights are reused verbatim, and that is legitimate

`a1_weights.json`'s per-fold weights are taken **unchanged**, not refitted. Justification, stated before the run:

1. They are fitted from the **kill population** — the mean measured swing `D` per victim side, normalised so the
   kill-weighted mean is exactly 1 — and **not from any target**. Nothing about T2 entered them.
2. They were fitted **per fold on training matches only**, and this run uses **the same fold split**:
   `stable_folds(seed=0)`, `fold_mapping_hash cebae50f85e94736`, re-confirmed three times today.
3. Fold `f`'s predictions come from fold `f`'s weights, fitted on the complement of `f`. Out-of-fold purity is
   preserved exactly as the original run preserved it. **`A1` is replayed five times, once per fold** — it is not a
   single-replay arm and must not be run as one.

Refitting on C would change nothing (the fitter never sees the target) and would risk transcription error, so the
stored values are used and this paragraph is the record of that choice.

#### The gate runs first, and is RECOMPUTED, not inherited

`build_target` drops rows — 53,730 of 67,251 survive T2's forward window, where C keeps all 67,251 with a known
winner. **The 0.0498% figure is a property of T2's row set and does not transfer.** R² between `A1`'s out-of-fold
`impact_diff` and `F-0.40`'s is recomputed on C's rows before any bootstrap. For a per-fold arm the out-of-fold
assembly — each row taking the value from the replay of the fold in which it was a test row — is the arm's column.

#### Predictions

1. **The gate passes, narrowly.** Separability on C's rows lands in **0.04%–0.07%**, above C's 0.0407% floor. It is
   close enough that landing below is a real possibility, and **if it does, the answer is "still untestable, now on
   both targets"** — which would close the side-asymmetry question properly rather than leaving it open, and is a
   legitimate outcome of this run rather than a failure of it.

2. **`A1 vs F-0.40` on C is not an IMPROVEMENT.** Direction declared before the number: on T2 the point estimate
   was **+1.096e−05** (the harm direction, though inconclusive), and `A2` — the same idea with a time band — was
   **decisively HARM** vs `F-0.40`. The side split has never once produced a favourable point estimate against a
   level-matched flat comparator.

   **Stated against my own prior:** there is a real mechanism by which C could disagree. C rewards weighting kills
   by how decisive they were for *this* round, `A1` up-weights attacker-victim kills (≈1.25) and down-weights
   defender-victim ones (≈0.79), and post-plant an attacker's death does plausibly move the round more. **If `A1`
   comes back IMPROVEMENT on C, that is a genuinely new finding** — the first evidence in this investigation that
   any structure beats a flat constant — and it would reopen the side asymmetry rather than close it.

3. `A1 vs P0` on C is **HARM**, and close to `F-0.40`'s **+6.953e−03**, because `A1` is `F-0.40` plus a split and
   `F-0.40` is far below C's optimum of ~1.97.

#### What this entry does not do

It freezes nothing, touches no `webapp/app/` code, and leaves `IMPACT_CALCULATION_VERSION` at 3. A favourable
result would **not** be a licence to ship a side-asymmetric factor — it would be grounds to build the arm properly,
which per declaration 6's parameterisation note means a scorer that can charge the two sides of a duel separately,
not another constant inside `_time_factor`.

### 2026-09-20 (RESULT, declaration 9) — the side asymmetry is a measured negative, not an untestable one

**The question is answered. It had never been answered before.**

```
A1 vs F-0.40, target C:  +1.544677e-03  [+1.406638e-03, +1.684454e-03]  HARM
A1 vs P0,     target C:  +8.497896e-03  [+7.800013e-03, +9.200988e-03]  HARM
F-0.40 vs P0, target C:  +6.953219e-03  [+6.218082e-03, +7.705842e-03]  HARM  (reproduced exactly)
```

`A1` is `F-0.40` plus a per-victim-side split and nothing else. Against its own level-matched comparator it is
**decisively worse**, by an interval nowhere near zero.

#### The gate, and a correction to how it was described

| | residual variance | floor | status |
|---|---:|---:|---|
| `A1` vs `F-0.40` on **C** | **0.0498%** | 0.0407% | **TESTABLE** |
| `A1` vs `F-0.40` on **T2** | 0.0498% | 0.107% | UNTESTABLE |
| `A1` vs `P0` on C | 0.7602% | 0.0407% | testable |
| `F-0.40` vs `P0` on C | 0.7340% | 0.0407% | testable |

Declaration 9 insisted the gate be **recomputed on C's rows rather than inherited**, on the grounds that
`build_target` drops rows for T2. The recomputation returned **0.0498%, identical to the recorded T2 figure to
four significant figures.** That is not a coincidence and it sharpens the rule:

> **Separability is a property of the two `impact_diff` COLUMNS and is target-independent. The FLOOR is
> target-dependent.** An arm pair has one separability; whether it can be resolved depends on which target you ask.

So the caution was right in principle and the recomputation confirmed the number instead of changing it. The
declaration-6 addendum's table should be read as "separability" (target-free) beside "the T2 floor" (target-bound).

#### What this settles

**The hypothesis that `A1` lost because the test was blind is refuted.** Given a target that *can* see the arm —
same separability, lower floor — it loses on the merits, and not narrowly.

Every side-split contrast that has ever been testable is now HARM:

| contrast | target | separability | verdict |
|---|---|---:|---|
| `A2` vs `F-0.40` | T2 | above floor | **HARM** (+1.743e−05) |
| `A2` vs `A1` | T2 | above floor | **HARM** (+6.473e−06) |
| **`A1` vs `F-0.40`** | **C** | 0.0498% | **HARM (+1.545e−03)** |
| `A1` vs `F-0.40` | T2 | 0.0498% | UNTESTABLE — the only one still unanswered |

`A1`'s log loss on C is **0.10087170**, worse than **every flat constant measured on C** — worse even than
`F-0.30` (0.10080164), the worst point on the nine-arm curve. In headroom terms the split costs **0.257% of C's
headroom** against its own comparator, which is larger than `F-1.00`'s **0.164%** harm.

**This closes the question the 2026-09-20 correction explicitly reopened.** That entry withdrew "the side asymmetry
does not help", correctly, because `A1` had never been measured with any power — and said "the hypothesis returns
to open". It is now closed, on evidence rather than on a blind test.

#### Why a real effect makes the metric worse

The underlying asymmetry is real and large: a kill whose victim is an attacker moves win probability **21.92pp**
against **13.76pp** for a defender victim. The mistake is in what that difference *is*.

**A kill is zero-sum in win probability** — the ledger established this when it withdrew the "same event worth 3.5x
more to one side" claim. So 21.92 vs 13.76 does not mean one event is worth more to one side; it means
**attacker-victim kills happen in systematically different STATES than defender-victim kills.** And the state is
exactly what `K(s)` already encodes — it is the man-advantage transition's worth, verified symmetric under team
relabeling.

Multiplying by a victim-side weight therefore **double-counts state information `K(s)` already carries**, and
distorts rather than refines. That is the same mechanism as `D1`: `corr(D, K) = 0.823`, the swing was already in
`K`, and paying it again bought nothing. The side split is the sharper case because paying it again is not merely
redundant — it is **measurably harmful**.

#### Predictions, scored

| declared | outcome |
|---|---|
| 1. the gate passes narrowly, separability in 0.04–0.07% | **held** — 0.0498%, above C's 0.0407% floor |
| 2. `A1 vs F-0.40` on C is **not** an IMPROVEMENT | **held**, and decisively: HARM, interval far from zero |
| 3. `A1 vs P0` is HARM and **close to** `F-0.40`'s +6.953e−03 | **direction held, magnitude FAILED** — +8.498e−03 is 22% worse, not close. The split does real additional damage on top of the level being wrong |

Declaration 9 recorded in advance that an IMPROVEMENT would be "the first evidence in this investigation that any
structure beats a flat constant" and would reopen the question. It did not happen; the record of having staked that
claim before the number stands either way.

#### What does not follow

C is **partly circular by construction** (declaration 7), so the precise claim is: **the side split is decisively
harmful for the within-round question, and remains unmeasurable for the forward-looking one.** `A1` vs `F-0.40` on
T2 is still UNTESTABLE and no run can change that without changing the arm's parameterisation.

The parameterisation note from declaration 6 is unaffected and still stands: a two-sided *stake* asymmetry — the
two outcomes of one duel carrying different consequences — cannot be expressed by a function returning one number
per event. What this result kills is the specific idea that **a per-event multiplier keyed on victim side** is a
useful way to encode it. That idea is now measured, and it is worse than doing nothing.

`IMPACT_CALCULATION_VERSION` stays 3, `git diff webapp/app/` is empty, no constant is frozen, and the version-4
recommendation is unchanged: flat constant, no structure.

### 2026-09-21 (DECLARATION 10) — the first ADDITIVE arm: `K(s) + f`, not `K(s) · T`

Declared before running. Asked for by the owner, and it is a hypothesis this investigation has never tested.

Every arm from declarations 1–9 is **multiplicative** (`P4`, `L`, `F`, `A1`, `A2`, `P2L`) or a **replacement**
(`D1` returns `T = S·D/K` so the product becomes `S·D`). **Nothing has ever added.**

```
shipped and every arm so far:   leverage = K(s) · T(t)
this declaration:               leverage = K(s) + f
```

That is a different belief, not a different curve. **A multiplier says a late kill AMPLIFIES whatever the kill was
worth; an additive term says being late is worth something IN ITSELF — the same amount whether the kill was
decisive or marginal.** Verified at build time: at `alive=(2,4)`, `K=80`, the term lifts `T` to 2.68; at
`alive=(1,1)`, `K=250`, the same term reaches only 1.49.

Delivered through the existing wrapper as `T = 1 + f/K`, so `K·T = K + f` exactly — identity verified to 1e−9 on
four states before any run. The base is **1.0**, not the shipped ramp: this **replaces** the multiplicative factor
rather than stacking on it.

#### The design point that makes this a real test: level-matching

An additive term **raises the post-plant payout**, and C's optimum is ~1.97 while `F-1.00` is far below it. So an
additive arm on a flat-1.0 base would beat `F-1.00` **merely by raising the level**, and that would prove nothing.

So `alpha` is **not** chosen from a grid. For each arm it is **calibrated so the arm's mean post-plant payout
equals a flat arm that has already been measured on C**:

```
mean T = 1 + (1/N_postplant) * SUM_applies (alpha * kbar * shape_i / K_i)      solved for alpha
```

`kbar = 137.71`, the kill-weighted mean post-plant `K` from `derived_tables.json`
(143.19 × 69,946 attacker-victims, 133.13 × 83,504 defender-victims). Calibration reads `kill_order_bonus_raw`
and `seconds_to_plant` out of the scorer's own `kill_observer`, so `K` and `t` are the scorer's values, not a
re-derivation. Self-kills, phantom plants and `K = 0` events take the flat base and are counted in `N` but
contribute nothing to the sum, exactly as the variant treats them.

**Each additive arm is then contrasted against its own level-matched flat twin.** That isolates *additive vs flat*
with the level held constant — the same move `F-1.26 vs P0` used to isolate shape from level.

| arm | shape | level-matched to |
|---|---|---|
| `ADD-T` × 3 | `t / 45`, rising | `F-1.26`, `F-1.6`, `F-1.9` |
| `ADD-S` | flat in `t`, per victim side | `F-1.6` |
| `ADD-TS` | `t / 45` × per victim side | `F-1.6` |

Comparators replayed in the same run: `P0`, `F-1.26`, `F-1.6`, `F-1.9`.

#### Side weights are FIXED, and the caveat is recorded in advance

`{victim_is_attacker: 1.25, else: 0.79}`, rounded from `A1`'s per-fold fits (1.250–1.256 / 0.785–0.790) and
kill-weighted mean 0.9997, so they carry only the split. **They are a declared constant, not fitted here** — but
they descend from a fit that saw the whole corpus, so the side arms carry a mild optimistic bias. **Declaration 9
found the per-victim-side split is measurably harmful, so I expect these arms to fail; a favourable-to-them bias
makes a negative result stronger, not weaker.** Stated now rather than after.

#### Target, and what it cannot settle

**Target C**, because its demonstrated floor (0.0407%) is well below T2's (0.107%) and small effects stand a chance
of being resolved. C is **partly circular** (declaration 7). **A favourable result here would not be a shipping
result** — it would require T2 confirmation, and `ADD` vs its flat twin on T2 may well be UNTESTABLE.

#### Predictions

1. **The gate passes comfortably.** An additive term is **not** a rescale of its comparator — over most of the mass
   (72% of post-plant kills fall in the first 20s where `t/45` is nearly flat) it behaves like *a constant per
   post-plant kill*, i.e. roughly a **count** of post-plant kills, which is a genuinely different feature from the
   `K`-weighted sum. Residual variance against the flat twin lands **above 0.3%**, far clear of the floor. This is
   the first arm I expect to be comfortably testable against a level-matched comparator.

2. **`ADD-T` does not beat its level-matched flat twin** — INCONCLUSIVE or HARM. The distinctive part of `t/45` is
   the late tail, which is 1.02% of events, and `K(s)` already prices post-plant kills at 1.018x against a measured
   1.023x swing, leaving little room for any additional term.

3. **`ADD-S` and `ADD-TS` are HARM** against their flat twin, following declaration 9.

4. **`ADD-TS` does not beat `ADD-T`** — the side split adds damage, not information.

**The falsifier, stated plainly:** if `ADD-T` beats its level-matched flat twin with an interval excluding zero,
that is **the first structure in this entire investigation to beat a flat constant**, and it reopens the whole
shape question rather than closing it.

#### What this entry does not do

No constant is frozen, `webapp/app/` is untouched, `IMPACT_CALCULATION_VERSION` stays 3. The new variant lives in
`scripts/postplant_v4_variants.py` as a wrapper around the shipped `_time_factor`, like every arm before it.

### 2026-09-21 (RESULT, declaration 10) — additive is not neutral, it is actively worse, and the time shape carries nothing

**Every additive arm loses to its level-matched flat twin, every interval excludes zero, and the harm grows with
how much additive mass is added.**

| arm | vs level-matched twin | 95% interval | verdict | % of C headroom | separability |
|---|---:|---|---|---:|---:|
| `ADD-T@1.26` | +1.364339e−03 | [+1.2342e−03, +1.4999e−03] | HARM | 0.227% | 0.0247% **UNTESTABLE** |
| `ADD-T@1.6` | +3.045884e−03 | [+2.7570e−03, +3.3479e−03] | **HARM** | 0.507% | 0.1259% |
| `ADD-T@1.9` | +4.432181e−03 | [+4.0161e−03, +4.8729e−03] | **HARM** | 0.738% | 0.2729% |
| `ADD-S@1.6` | +3.017792e−03 | [+2.8423e−03, +3.1832e−03] | **HARM** | 0.502% | 0.0975% |
| `ADD-TS@1.6` | +6.960561e−03 | [+6.5461e−03, +7.4085e−03] | **HARM** | 1.159% | 0.1682% |
| `ADD-TS@1.6` vs `ADD-T@1.6` | +3.914677e−03 | [+3.7001e−03, +4.1350e−03] | **HARM** | — | 0.0776% |

Every additive arm is also worse than the **shipped `P0`**, not merely worse than flat.

**Reproduction:** `F-1.26 vs P0` and `F-1.6 vs P0` returned **−4.118714982721e−04** and **−1.446361904629e−03**,
bit-for-bit identical to declaration 8. `F-1.9 vs P0` is now a registered contrast (−1.760123e−03) rather than the
point estimate salvaged from the OS-killed run, and it matches that salvage exactly.

#### Two findings, and the second is the sharper one

**1. Additive is worse than multiplicative, and monotonically so.** At matched mean payout the harm runs
0.227% → 0.507% → 0.738% of C's headroom as the level goes 1.26 → 1.6 → 1.9. The more additive mass, the worse.
`ADD-TS@1.6` at **1.159%** is as harmful as `F-0.40 vs P0` (1.157%), the largest harm measured anywhere in this
investigation.

**2. The time shape carries essentially nothing — measured directly for the first time.** `ADD-T@1.6` rises with
`t`; `ADD-S@1.6` is **flat in `t`** and differs only by the side split. Their harm against the same twin:

```
ADD-T@1.6  (rises with t)   +3.0459e-03
ADD-S@1.6  (flat in t)      +3.0178e-03
difference                   2.81e-05     <- 0.9% of the effect
```

Whether the additive term rises with time or ignores time entirely **makes almost no difference**. Every earlier
entry inferred this from the mass distribution (72% of post-plant kills in the first 20 seconds, 1.02% past t=40);
this is the first arm pair that isolates the time shape with everything else held equal, and it **confirms the
inference directly**. The shape is not merely undetectable — at this level of aggregation it is inert.

#### Why additive is harmful rather than merely useless

An additive term pays **the same absolute bonus regardless of how decisive the kill was**. Verified at build time:
at `alive=(2,4)`, `K=80`, it lifts `T` to 2.68; at `alive=(1,1)`, `K=250`, the same term reaches only 1.49.

So as a *fraction*, it up-weights marginal kills far more than decisive ones. That **partially erases the
kill-order ordering** — and `K(s)` is the one component this investigation has established is **already correct**
(mean `K` 1.018x post/pre against a measured swing of 1.023x, while verifiably spike-blind). The additive form
degrades information that was right, which is why it does not merely fail to help.

This is the same lesson as `D1` and `A1`, in its strongest form yet: **everything that tries to add to `K(s)`
either cannot be seen, or makes things worse.**

#### Predictions, scored

| declared | outcome |
|---|---|
| 1. the gate passes comfortably, separability **above 0.3%** | **FAILED** — measured 0.0247% / 0.1259% / 0.2729% / 0.0975% / 0.1682%. **None reached 0.3%**, and `ADD-T@1.26` came back **UNTESTABLE** |
| 2. `ADD-T` does not beat its level-matched twin | **held**, and more strongly than declared — HARM at every level, not merely INCONCLUSIVE |
| 3. `ADD-S` and `ADD-TS` are HARM | **held**, both decisively |
| 4. `ADD-TS` does not beat `ADD-T` | **held** — +3.915e−03 HARM |

**Prediction 1's reasoning was wrong and is worth recording.** I argued an additive term "behaves like a count of
post-plant kills, a genuinely different feature from the `K`-weighted sum", and therefore would be comfortably
testable. The flaw: **the flat twin's `impact_diff` also scales with that count**, and level-matching forces the
two to share their mean, so the round-to-round variation of both is driven by the same underlying quantity — how
much post-plant action the round had. The additive column is far more collinear with flat than I predicted.

Note the useful regularity that came out of the miss: **separability grows with additive mass** (0.0247% → 0.1259%
→ 0.2729% as the level rises), because a larger `alpha` means more deviation from flat. Separability is not a fixed
property of an *idea*; it scales with how hard the arm is pushed.

The failed cell does not weaken the conclusion. `ADD-T@1.26` is UNTESTABLE, but `ADD-T@1.6` and `ADD-T@1.9` are
both comfortably testable and both decisively HARM, and the trend across the three is monotone.

**The falsifier did not fire.** Declaration 10 stated that an `ADD-T` win over its twin would be the first
structure in this investigation to beat a flat constant. It lost at every level.

#### What this does not settle

C is partly circular (declaration 7), so this is a within-round result. `ADD` vs its twin on **T2** was not run and
would likely be UNTESTABLE at these separabilities against T2's 0.107% floor — `ADD-T@1.26`'s 0.0247% is below even
C's floor. The additive form is therefore **measured harmful for the within-round question and unmeasured for the
forward-looking one**, which is the same shape as declaration 9's finding for the side split.

`IMPACT_CALCULATION_VERSION` stays 3, `git diff webapp/app/` is empty, and the version-4 recommendation is
unchanged: **flat multiplicative constant, no structure, no additive term.**

### 2026-09-21 (RESULT, declaration 11) — the falsifier fired: a cliff DOWN beats a flat constant

**For the first time in this investigation, a structural arm beats a flat constant, clears the gate, and returns an
interval excluding zero.** Declaration 10 named that outcome as the falsifier. It has happened.

| contrast | point | 95% interval | verdict | separability |
|---|---:|---|---|---:|
| **`STEP30@1.6` vs `F-1.6`** | **−2.402741e−03** | [−2.8645e−03, −1.9449e−03] | **IMPROVEMENT** | 0.0768% |
| **`STEP38@1.6` vs `F-1.6`** | **−1.990558e−03** | [−2.4078e−03, −1.5686e−03] | **IMPROVEMENT** | 0.0572% |
| `STEP41.5@1.6` vs `F-1.6` | −7.011557e−04 | [−9.2477e−04, −4.8043e−04] | IMPROVEMENT | 0.0136% **UNTESTABLE** |
| `STEP41.5@1.6` vs `STEP38@1.6` | +1.289403e−03 | [+9.2059e−04, +1.6579e−03] | **HARM** | 0.0482% |
| `STEP30@1.6` vs `STEP38@1.6` | −4.121825e−04 | [−6.1258e−04, −2.0761e−04] | IMPROVEMENT | 0.0217% **UNTESTABLE** |
| `P6` vs `F-1.6` | +1.878019e−03 | [+1.4863e−03, +2.2729e−03] | **HARM** | 0.2299% |
| `P6` vs `F-1.26` | +8.435287e−04 | [+4.8543e−04, +1.2119e−03] | **HARM** | 0.1600% |
| `P6` vs `P0` | +4.316572e−04 | [+2.6184e−05, +8.2879e−04] | **HARM** | 0.2220% |

All three step arms are level-matched by a kill-weighted norm, so `F-1.6` and `STEP*@1.6` have **the same mean
post-plant payout** — the contrast is the step shape and nothing else.

#### The size of it

| | gain over shipped `P0`, as % of C's headroom |
|---|---:|
| `F-1.26` | 0.069% |
| `F-1.6` (best flat tested) | 0.241% |
| `STEP38@1.6` | **0.572%** |
| `STEP30@1.6` | **0.641%** |

**The cliff adds 0.331–0.400% of headroom on top of the best flat arm — larger than that flat arm's own 0.241%
gain over the shipped model.** Every previous entry's framing ("no structure beats a constant") is now wrong as a
general claim, and must be narrowed to the arms that were actually tried.

#### Where the break belongs

- **38 beats 41.5 decisively.** `STEP41.5 vs STEP38` is **+1.289e−03 HARM**, testable at 0.0482%. Moving the cliff
  to the half-defuse boundary gives back most of the gain. The owner's instinct that 38 and 41.5 were both
  candidates is **half right**: 38 carries it, 41.5 is measurably worse.
- **30 vs 38 cannot be separated.** `STEP30 vs STEP38` is −4.122e−04 at **0.0217% — below C's floor, UNTESTABLE.**
  The point estimate favours 30 and the measured swing does decline from 30s, but this harness cannot tell them
  apart. **Do not claim 30 over 38.**

#### Part 4 is now dead on both targets

`P6` was a genuine null on T2 (0.220% separability, twice the floor). On C, at 0.2220% separability, it is
**HARM against every comparator** — the shipped model included. Part 4's `(a,d,t)` table is the only arm with both
a real null and a real negative on two different targets. **It is finished.**

And note what that separates: the win here is **not** "state×time modelling works". `P6` has far more information
than `STEP38` and does worse. It is specifically **the cliff** — and `P6`'s per-state normalisation
(`D / mean_t D`, clamped to [0.05, 2.0], centred at c≈1.287) is precisely what erases a cliff.

#### Three caveats, and the first one is serious

**1. The step profile is IN-SAMPLE INFORMED, so this result is optimistic.** The band multipliers (0.136 for
38–45s; 0.715 / 0.133 for the two-step) were read off `V_postplant_by_band`, which is computed over the **whole
corpus**, not per fold. The out-of-fold protocol protects the fitted *coefficient*; it does **not** protect the
*choice of profile*. A clean test refits the band multipliers on training matches only, per fold, exactly as `A1`
and `P6` fit theirs. **Until that is run, treat the magnitude as an upper bound.** The direction is not in doubt —
the shipped model pays 1.75 where the measured swing is 2.54pp, and any correction of that sign helps — but the
size is not yet earned.

**2. C is partly circular** (declaration 7), so this is a within-round result.

**3. T2 confirmation may be impossible.** Separability is **target-free** (established 2026-09-20), and
`STEP38 vs F-1.6` sits at **0.0572%**, `STEP30 vs F-1.6` at **0.0768%** — both **above C's 0.0407% floor and below
T2's 0.107% floor.** So the forward-target check this result needs would most likely return **UNTESTABLE**. The
lever, from declaration 10's regularity: separability grows with how hard an arm is pushed, so a deeper or earlier
cliff would raise it. That is the way to make the question askable on T2, and it should be declared before it is
tried.

#### What this changes

The version-4 recommendation is **no longer "flat constant, no structure"**. It becomes: a flat constant **plus a
cliff at plant+38**, pending (1) a per-fold refit of the profile and (2) whatever T2 can say. Nothing is frozen and
`IMPACT_CALCULATION_VERSION` stays 3, but this is the first change with a positive result behind it rather than a
correctness argument.

The mechanism is not subtle and was visible in the swing table before any arm ran: **the shipped model pays its
MAXIMUM (1.75) in the window where a kill is worth least (2.54pp against 20.03pp at 20–30s).** It is a sign error,
and correcting it is worth more than getting the level right.

### 2026-09-21 (OBSERVATIONS, unrun) — three things found while reading the tables, none of them yet tested

Recorded because they came out of reading saved artifacts during discussion, not out of an arm, and would otherwise
exist only in a conversation. **None of these is a result.** Each is a lead or a defect.

#### 1. The side asymmetry REVERSES with time — which is why `A1` was always going to fail

Mean |swing| by victim side, from `V_postplant_by_band` (Part 4's extractor, so post-resolution seconds are
correctly excluded):

| band | attacker-victim | defender-victim | ratio |
|---|---:|---:|---:|
| 0–10s | 19.61pp | 16.43pp | 1.19 |
| 10–20s | 20.60pp | 17.35pp | 1.19 |
| 20–30s | 20.46pp | 18.29pp | 1.12 |
| 30–38s | 13.89pp | **16.97pp** | **0.82** |
| 38–45s | 2.54pp | **8.16pp** | **0.31** |

Early, killing an attacker swings ~19% more. Late, killing a **defender** swings **3.2x** more. **The crossover is
around 30s.**

`A1` applied a *fixed* split (attacker-victim 1.25, defender-victim 0.79) at **all** times. The data says +19%
early and −69% late, so a constant split has the late region **backwards** — which is a mechanism for declaration
9's finding that `A1` is measurably HARM, not merely inert. **Side-ness is not wrong; side-ness WITHOUT time is
wrong.** `A2` banded at t≥30 but was fitted to the same pooled `D`, so it has never been tested with the crossover
pointing the right way. **A side×time arm with the reversal built in is UNTESTED.**

Caveats: the late bands are dominated by `Xv1` states so composition does some of this work, and these numbers
exclude terminal kills (see 3).

#### 2. DEFECT — `whole_round_states` counts seconds after the round was already decided

`scripts/postplant_v4_alt_metrics.py`'s `whole_round_states` runs its occupancy loop to `plant + 45`
**unconditionally**:

```python
end = max(end, plant + 45.0)      # defused / defuse_time are SELECTed and never used
for t in range(0, int(end) + 1):
```

`defused` and `defuse_time` are queried and then never consulted, so every second **after a successful defuse** is
still counted into `V(a, d, planted)`. Measured on the corpus: **216,465 of 1,958,175 post-plant seconds = 11.05%**
of the table is post-resolution. Of 43,515 planted rounds, 12,498 (28.7%) are defused and 2,243 (5.2%) exploded.

This is what produces the impossible cells: `1v0|post` has attacker win **0.972**, i.e. attackers lose 2.8% of
rounds in which the last defender is dead and the spike is planted — only possible if the defuse had already
landed.

**Scope, which matters:** `app/scoring/postplant_value_table.py` (Part 4's extractor, used by `P6` and by
`V_postplant_by_band`) is **correct** — it explicitly lowers the horizon to `defuse_time`. The defect is confined
to `whole_round_states`, i.e. to **`V_whole_round` and METHOD B**.

**Method B is the anchor of the whole reconciliation** (the only estimate with no target, no folds and no free
coefficient; it is why 1.02 is treated as the true swing ratio). Post-defuse seconds are defender wins, so they
add defender mass to post-plant states and most likely bias B's post/pre ratio **downward** — magnitude unknown
until recomputed. **B's 1.023 should be treated as provisional until `whole_round_states` excludes resolved
seconds.**

#### 3. The banded table has no terminal states, so round-ENDING kills are unpriced by band

`V_postplant_by_band` covers 1–5 × 1–5 only. `0v1` and `1v0` are **absent**, so every kill that takes a side to
zero is missing from every band number above — including the side table in 1 above.

`V_whole_round` does have them, but is not time-resolved (pre/post only):

| | attacker win % | swing |
|---|---:|---:|
| `1v1` post-plant | 0.636 (n=85,236 round-seconds) | — |
| attacker dies → `0v1` | 0.039 | **−59.7pp** |
| defender dies → `1v0` | 0.972 | **+33.6pp** |

Those are the largest swings in the game, they are exactly what a 1v1 consists of, and **they cannot currently be
priced by time band.** (They also come from the defective table in 2.)

**1v1 is the most occupied state at 38–45s** (n=6,026, ahead of `2v1`'s 4,142), and attacker win % in 1v1 runs
0.550 / 0.562 / 0.633 / 0.787 / **0.960** across the bands — by 38–45s the defender's wincon is gone, which is the
mechanism behind the 8x swing collapse that `STEP38` exploits.

**What is needed:** rebuild the banded table including `0v1`/`1v0`, over the corrected (resolution-aware) horizon.
One replay. That would (a) let a 1v1-specific model be priced at all, (b) tell whether the side crossover in 1
survives once terminal kills are included, and (c) re-anchor method B.

Only occupancy (round-seconds) is saved anywhere; **a count of distinct 1v1 post-plant situations does not exist**
and needs a pass over `kill_events`.


### 2026-09-21 (CORRECTION) — every post-plant v4 measurement scored the LEGACY formula, not rc3

**Found while building declaration 12's assists arm, before anything in it was run.** Every v4 script
(`run_postplant_v4_report.py`, `postplant_v4_row_motion.py`, `postplant_v4_steps_and_p6_on_c.py`, the additive and
side runners) replays `build_impact_rows_for_match` with **no scoring configuration**. With no configuration the
scorer runs the **legacy** formula: no econ component, no assists term (`FormulaWeights()` defaults `assists=0`), no
trade credit, time folded into `damages + mean(econ, time, swing)`. What has shipped since 2026-09-18 is the rc3
manifest (`impact_runtime.active_scoring_config()`: A 1 / B 2.5 / C 2.5 / D 100, trade credit on,
`buy_disruption_v2_30_80_bonus_denial`). HANDOFF.md §2 writes the rc3 formula down as the thing being measured; the
harness never scored it.

Measured, on the latest match in the local corpus: **185 of 190 rows** score differently under the two; the legacy
replay has **0** nonzero `econ_component` and `assists_component` rows against 143 and 53 under rc3.

**Scope.** Every v4 contrast, every separability figure, both demonstrated floors (T2 0.107%, C 0.0407%), and every
row-motion count (including this morning's STEP38 run: 28.9–30.8% of rows) describe the time factor **inside the
legacy formula**. Direction probably transfers — the time factor is the same function in both — but magnitude does
not: under rc3 leverage carries B = 2.5 against damage's 1.0, so the time factor is a larger share of Impact than it
was in the legacy mean. **None of the v4 numbers is withdrawn; all of them are re-labelled "legacy formula".**
Declaration 12 re-measures the one prior result it leans on (`F-1.00 vs P0`) under rc3 instead of assuming it.

**The ex-ante constraint, which rc3 makes visible.** The buy-disruption econ component reads round N+1, so under
`use_realized_swing=False` it abstains to exactly 0 (`econ_buy_disruption.score_round`, the leakage gate). Both
outcome targets need ex-ante scoring, so the honest harness configuration is **rc3 ex-ante**: rc3's weights,
assists and trade credit, with econ necessarily zero. On a 60-match sample realized vs ex-ante changes 71% of rc3
rows, so the choice is not cosmetic. Row motion — about stored rows, not prediction — uses rc3 live, unchanged.

### 2026-09-21 (DECLARATION 12) — no time factor: every kill is worth the same, and a decided round pays nothing

**The owner's decision, made before this was measured, and the reason the arms look the way they do.** The time
factor was meant to say that kills under pressure are worth more. The owner's position after declaration 11: they
are not — *every fight is under some clock*, pre-plant and post-plant alike, and a phase of the round is not a
premium. Timing is kept for exactly one purpose: **to recognise kills that can no longer change the round**, which
get no leverage and no assists credit, and keep full econ. This is consistent with method B (post/pre swing ratio
1.023, provisional per the OBSERVATIONS entry) and with `K(s)` already pricing post-plant kills at 1.018 of
pre-plant. It deliberately overrides target C's preference for a post-plant premium, on the grounds that C is
circular (declaration 7). **That override is a decision, recorded here as one, not a measured result.**

**Closed by that decision, not by measurement** — and moved to a future /stats card, not to Impact:
the plant+38 cliff (declaration 11), side×time with the ~30s crossover, and a 1v1-by-time model. With no fitted
parameter left in the design, declaration 11's per-fold profile refit is **moot**. The `whole_round_states` defect
(method B) stays open; it no longer anchors any decision here.

**"Decided"** (`postplant_v4_variants.round_decided`), exactly these three and nothing else:

1. spike defused, kill at or after `defuse_time`;
2. real plant (`effective_plant_time`, phantoms excluded), kill at or after plant + 45 — exploded whatever the flags
   say (C3's cap);
3. no real plant, outcome is a **Time Win**, kill after 100s. The Time Win condition guards against noisy clocks
   (14 Elimination Wins carry plant_time > 100s); on the corpus all 338 such post-100s kills are in Time Win rounds,
   so it removes nothing today.

Not the 38–45s window: at plant+38 a round is *nearly* decided (1v1 attackers win 96%, not 100%), a defender with a
banked half-defuse can still win until 41.5s, and killing a mid-defuser is the round. The owner chose strictly
decided over clock-decided.

**Arms**, all scored **rc3 ex-ante** (CORRECTION above), one replay each feeding both targets
(`scripts/postplant_v4_decl12.py`):

| arm | definition |
|---|---|
| `P0` | rc3 as shipped |
| `F-1.00` | post-plant flat 1.0; decided rounds keep the shipped 0.5 (re-measures the legacy result under rc3) |
| **`N`** | **`T = 1` everywhere, `T = 0` once decided** — no ramp, no override, no premium |
| `N+A` | `N`, plus each assist on a kill after the round was decided is removed from the assists component (D = 100 each) |

**Not in any arm, and why.** Damage has no timestamps — it is a per-round total derived from combat score — so
post-decision damage cannot be separated and stays in. The combat-score assist points inside that damage total
(Valorant's 25 per non-damaging assist; `FormulaWeights` docstring) stay with it for the same reason. Econ is
untouched by both arms and is zero under ex-ante on both sides of every contrast. Assistants are mapped to players
by Riot ID; 112 of 177,781 (0.06%) do not map and are left in.

**Contrasts, gate and floors.** `N vs P0`, `N vs F-1.00`, `N+A vs N`, `F-1.00 vs P0`, each on **T2** (the report's
`outer_cv`, inner L2 selection, 5 folds, seed 0) and **C** (fixed L2 1.0, declaration 7's features), 2,000-draw
paired match-clustered bootstrap. Separability is computed once (target-free) **before any bootstrap**; below a
target's floor the verdict is **UNTESTABLE**. The floors are carried over from the legacy-formula runs and are **not
re-derived for rc3** — flagged, not fixed; a floor re-derivation is its own declaration if a verdict ends up
depending on it. Identity gate: the wrapper installed with no variant must reproduce rc3 bit-for-bit on a ~150-match
sample, or the run stops.

**Predictions**, to be scored afterwards whatever happens:

| # | prediction | confidence |
|---|---|---|
| 12.1 | identity gate passes | high |
| 12.2 | `N vs P0` **[T2]: IMPROVEMENT** | moderate — rests on legacy `F-1.00`, which the formula change could move |
| 12.3 | `N vs P0` **[C]: HARM**, order 1e-03 | moderate on sign; magnitude not predicted (formula changed) |
| 12.4 | `F-1.00 vs P0` has the **same sign on each target as it did under legacy** (T2 improvement, C harm) | moderate |
| 12.5 | `N vs F-1.00`: **UNTESTABLE on both targets** (0.5→0 on ~0.8% of kills) | moderate |
| 12.6 | `N+A vs N`: **UNTESTABLE on both targets** (~1.1% of assists) | high |
| 12.7 | row motion (rc3 live): `N` clears section 8's threshold (≥1% of rows or ≥5% of matches reordered) | high |

**Stop rule, fixed now.** Version 4 does **not** ship `N` if `N vs P0` **[T2] is HARM** (interval excluding zero).
INCONCLUSIVE ships: the design is the owner's conceptual decision and the forward cost is then bounded by the
interval. C HARM is predicted (12.3) and does not stop anything. `N+A` ships with `N` unless `N+A vs N` [T2] is HARM.
If `N` is stopped, nothing ships and the finding is written up — including whether it contradicts 12.4, which would
mean the legacy-formula conclusions did not transfer.

Only after the stop rule clears: edit `_time_factor` in `webapp/app/scoring/impact.py` (and the assists component),
bump `IMPACT_CALCULATION_VERSION` 3 → 4, code review, rc3-style release path. **Not before.**


### 2026-09-21 (RESULT, declaration 12) — no time factor improves BOTH targets under rc3; the legacy "T = 1.00 is refuted" does not transfer

**The stop rule clears. `N` ships; `N+A` ships with it.** `N vs P0` on T2 is an **IMPROVEMENT with the interval
excluding zero**, and on C — where a HARM was predicted and the owner had decided to ship through it — it is **also
an IMPROVEMENT**, the largest effect measured in the whole v4 investigation. This is the first design in the
investigation that beats the shipped model on both targets at once.

Artifacts: `postplant-v4/decl12.json`, `postplant-v4/decl12_row_motion.json`. Scored rc3 ex-ante, 3,198 matches,
67,251 C rows / 63,633 T2 rows, 2,000 draws. Identity gate passed on 153 matches.

| arm | C log loss | T2 log loss |
|---|---:|---:|
| `P0` (rc3) | 0.08678503 | 0.67451429 |
| `F-1.00` | 0.07804499 | 0.67442468 |
| **`N`** | **0.07564680** | **0.67442437** |
| `N+A` | 0.07666090 | 0.67442359 |

Protocol gate, before any bootstrap (floors carried from legacy, not re-derived — no verdict below sits near one):
`N vs P0` 1.692%, `F-1.00 vs P0` 1.571%, `N vs F-1.00` 0.162% — all testable on both targets; `N+A vs N` **0.0058%**,
below both floors.

| contrast | T2 | C |
|---|---|---|
| **`N vs P0`** | **−8.992e−05 [−1.349e−04, −4.750e−05] IMPROVEMENT** | **−1.114e−02 [−1.229e−02, −9.921e−03] IMPROVEMENT** |
| `F-1.00 vs P0` | −8.961e−05 [−1.318e−04, −4.782e−05] IMPROVEMENT | −8.740e−03 [−9.812e−03, −7.704e−03] IMPROVEMENT |
| `N vs F-1.00` | −3.137e−07 [−1.268e−05, +1.188e−05] INCONCLUSIVE | −2.398e−03 [−3.245e−03, −1.526e−03] IMPROVEMENT |
| `N+A vs N` | UNTESTABLE (point −7.80e−07) | UNTESTABLE (point +1.01e−03 — **not** a harm finding) |

**Scale.** Against the legacy-derived headrooms (rc3's are not re-derived; C's is ~unchanged since P0's C loss moved
0.0924 → 0.0868 against a 0.6931 coin flip), `N vs P0` is **~1.84% of C's headroom** and **~0.45% of T2's** — about
5x the cliff's 0.33–0.40% on C, and the cliff was measured on the wrong formula. Still small in absolute terms; the
standing framing ("stop overpaying post-plant kills") holds, but it is no longer 1/150th-scale on C.

**Where the T2 gain comes from.** Almost entirely from deleting the ramp and override: `F-1.00 vs P0` and `N vs P0`
agree on T2 to three significant figures, and `N vs F-1.00` is INCONCLUSIVE there. Zeroing decided rounds is
invisible to the forward target and helps the within-round one.

**Row motion, rc3 live** (section 8 threshold ≥1% rows or ≥5% reordered): `N` **216,804 rows (32.14%)**, **2,301
matches reordered (72.0%)**, mean |Δ| 97.2 on changed rows; `N+A` 32.22% / 71.9% / 97.3. Clears both limbs by far.
**Motion is not improvement** — but 72% of matches reordering is what the site will visibly do, versus ~20% the
legacy-formula runs suggested: under rc3 leverage carries B = 2.5, so a time-factor change moves far more.

#### Predictions, scored

| # | prediction | outcome | |
|---|---|---|---|
| 12.1 | identity gate passes | passed, 153 matches | **right** |
| 12.2 | `N vs P0` [T2] IMPROVEMENT | IMPROVEMENT | **right** |
| 12.3 | `N vs P0` [C] HARM, ~1e−03 | **IMPROVEMENT, −1.11e−02** | **WRONG — sign and order of magnitude** |
| 12.4 | `F-1.00 vs P0` keeps legacy signs (T2 improve, C harm) | T2 improve; **C IMPROVEMENT** | **WRONG on C** |
| 12.5 | `N vs F-1.00` UNTESTABLE on both | testable (0.162%); T2 INCONCLUSIVE, C IMPROVEMENT | **WRONG** |
| 12.6 | `N+A vs N` UNTESTABLE on both | 0.0058% | **right** |
| 12.7 | row motion clears section 8 | 32.1% rows / 72.0% reordered | **right** |

**12.3 and 12.4 fail for the same reason, and it is the important finding of the day: the legacy-formula conclusions
about the LEVEL do not transfer to rc3.** Under legacy, `F-1.00 vs P0` on C was HARM (+9.82e−04) and grounded "T = 1.00
is refuted", the 1.18–1.35 "joint window", and C's ~1.97 optimum. Under rc3 the same arm is an IMPROVEMENT of
−8.74e−03, excluding zero. **Those three conclusions are withdrawn as statements about the shipped scorer**; they
stand only as descriptions of the legacy formula. The shape conclusion (the ramp is wrong) survives and is
strengthened. Why the sign flips is not established; a plausible mechanism — not tested — is that rc3's trade credit
already routes post-plant-trade value through `T`, so the ramp double-counts there in a way the legacy mean did not.

**12.5 fails** because zeroing decided rounds is far more separable than its 0.8% kill share suggested: each such
event moves B × K × 0.5 under rc3. Recorded as a wrong prediction, not explained away.

**Owner's override, revisited.** Declaration 12 recorded shipping through a predicted C harm as a decision. There is
no C harm to ship through; the override was never exercised. It stays in the record as the reason the design did not
depend on this outcome.

**Next, per the declaration:** edit `_time_factor` and the assists component, bump `IMPACT_CALCULATION_VERSION` 3 → 4.
Because the rc3 manifest freezes the scoring sources' digests, an edit to `impact.py` invalidates rc3 verification —
version 4 has to ship as a new frozen manifest activated with the bump, the rc3 way; flags default off so rc3 and
legacy stay reproducible.


### 2026-09-21 (DECLARATION 13, release) — Impact v4 is implemented behind two flags and must equal an independent reference, row by row

Written and committed **before any equivalence comparison is run** (plan
`plans/2026-09-21-impact-v4-no-time-factor-plan.md` r5, §3.1). This entry covers the branch-only part of the release:
the implementation (branch `impact-v4-implementation`, from `4ecf7f0`), the independent reference, and the
equivalence between them. It fixes what ships **as code**. Declaration 12 and its RESULT fixed what ships **as a
formula**, and nothing here re-opens them.

#### What is implemented

Two flags on `ImpactScoringConfig` and `build_impact_rows_for_match`, both defaulting to **False**:

- `enable_decided_only_time`: `_time_factor` returns `0.0` if `plant_window.round_decided(round, t)` and `1.0`
  otherwise, **before every other branch**, including the legacy exploded/defused `0.5`. "Decided" means exactly
  declaration 12's three cases. The check is ported verbatim from `scripts/postplant_v4_variants.py::round_decided`,
  with `ROUND_SECONDS = 100.0` and `SPIKE_SECONDS = 45.0`. Combining it with either legacy timing flag raises an error.
- `remove_post_decided_assists`: for each kill made after the round was decided, each name in
  `kill_events.source_meta["assistants"]` is mapped to one of this match's players by case-insensitive
  `Player.display_name`. The assists component then pays `D × (assists − min(n, assists))`. `stat["assists"]`, damage
  and every other term are untouched.

The implementation makes two choices that the measured reference does not. Both are **declared here as extensions;
neither was measured**:

1. **The clamp** `min(n, stat["assists"])` (plan R10). The reference subtracts `D × n` with no clamp. The clamp is
   predicted to fire on **0** rows of the measurement cohort (13.7), so it cannot move the equivalence.
2. **Ambiguous names are left in.** The reference builds a `{lower(name): match_player}` dict. On a case-insensitive
   name collision within one match, whichever row the query returned last wins. The implementation instead treats a
   name that matches two players as **ambiguous**: it removes nothing for that name and reports it, just as it does
   for an unmapped name. The cohort has **0** such collisions (queried 2026-09-21: no match has two players whose
   lowercased `display_name` agree), so this cannot move the equivalence either.

Each kill's removed, clamped, unmapped and ambiguous assistants are reported through the existing `kill_observer`
hook, in that kill's context. The keys are present only when `remove_post_decided_assists` is on. Nothing that is
reported influences a score.

`kill_order_leverage` keeps the legacy time factor explicitly (plan R8). `postplant_factor.py`'s shim is unchanged.
On this branch `IMPACT_CALCULATION_VERSION` stays **3** and `ACTIVE_MANIFEST` stays rc3.

#### The measurement cohort (plan §1, "three cohorts")

The cohort is every match in the local copy `valo_v4` (PostgreSQL 18, port 5434; no `impact_scores` rows):

| | |
|---|---|
| matches | **3,198**, ids 1..3206 |
| id list | `sha256(canonical_json(sorted ids))` = `8d97eba943a212bc07d14815470280c898d602f7e670f8e87ec2c559dc9b973b` |
| per-match fingerprints, **rc3 (v1) contract** | `match_source_fingerprint` at `4ecf7f0` (`app/` identical to `f96aee9`), taken in one REPEATABLE READ snapshot |
| cohort fingerprint, v1 | `sha256(canonical_json({id: fingerprint}))` = `ff0854b9a8ef94cba1d594f563d440f8e5109661c14a21e880bba34b11dd81aa` |
| stored at | `~/Documents/valo-backups/v4-release/measurement/cohort_v1.json`, sha256 `8596b903068ba09d88d6e6b0a71c1d9cee469d03e9cc59e9111725e0f20af4d7` |

`canonical_json` is `export_impact_artifact.canonical_json` (sorted keys, no whitespace). The v2 fingerprints of the
same cohort will be recorded in the RESULT entry once the v2 contract exists. Until then, the v1 fingerprints are what
pin the cohort.

**The plan states a fact that is no longer literally true.** Plan §2.6 says `git diff origin/main -- webapp/app` is
empty at `f96aee9`. Since PR #70 merged, it is not: one file differs,
`app/adapters/trackergg_browserstate_source.py` (ingestion pagination). Every file in `HASHED_SOURCES`, and all of
`app/scoring`, `app/models` and `app/services`, is byte-identical between `f96aee9` and `origin/main` (checked
2026-09-21). So `f96aee9`'s scorer is still exactly production rc3's, and the reference is taken there as planned.

#### The new fingerprint contract (plan §2.4, R1)

`SOURCE_FINGERPRINT_VERSION = 2` in `app/scoring/impact_manifest.py`. It differs from v1 in exactly three ways:

- `events` gains a canonical projection of the assistants payload, `(k.source_meta::jsonb -> 'assistants')::text`.
  It does not take the whole `source_meta`: the scorer reads nothing else in it, and jsonb's text form is canonical.
- A new `players` query returns each match player's `match_players.id`, `player_id` and `players.display_name`,
  ordered by `match_players.id`.
- `app/models/player.py` joins `HASHED_SOURCES`.

The version is recorded in every new manifest (`source_snapshots.fingerprint_version`) and every new export sidecar
(`inputs.fingerprint_version`). The release tools refuse to compare fingerprints across contracts; a missing version
reads as 1. `ARTIFACT_CONTRACT_VERSION` goes from 1 to 2, because contract (c) changed.

**Old frozen evidence is not rewritten.** rc3's manifest, sidecars and approvals stay exactly as they are. They no
longer verify against this branch's code, which is expected: any change to `impact.py` breaks rc3's source digest
(plan §1).

Release write protection (§2.5): `players` joins the swap's source digests, its lock set and the release write gate.

#### The independent reference (plan §2.6 step 1)

A separate worktree at `f96aee9` gets one **scripts-only** commit, which adds a row-dump mode to
`scripts/postplant_v4_decl12.py`. At that commit, `git diff f96aee9 -- webapp/app` must be empty. rc3's configuration
is read as data from `docs/superpowers/impact-rc3/candidate-manifest.json` (`comparators.impact_rc3`), **not**
through `active_scoring_config()`. The mode dumps every `CalculatedImpact` field for `P0`, `N` and `N+A` on the
measurement cohort, under both **ex-ante and realized** scoring. That makes six artifacts, stored under
`~/Documents/valo-backups/v4-release/reference/`. Their sha256s are recorded in an addendum before the comparison
runs.

#### Predictions (scored in the RESULT entry, whatever happens)

Every comparison is made **row by key** `(round_id, match_player_id)`, within one scoring mode. It covers **every**
`CalculatedImpact` field except `scoring_version`: all scoring and diagnostic columns, including the non-persisted
`leverage_component`, `assists_component` and `trade_credit`, and `trade_detail`.

| # | prediction | confidence |
|---|---|---|
| 13.1 | with the flags off (`impact_rc3` from `COMPARATORS`), the new code equals reference `P0`: **0 rows differ**, ex-ante **and** realized | high |
| 13.2 | `impact_v4_n` equals reference `N`: **0 rows differ**, both modes | high |
| 13.3 | `impact_v4` equals reference `N+A`: **0 rows differ**, both modes | high |
| 13.4 | the row **sets** are identical in all six pairs: no key appears on only one side, and the row counts are equal | high |
| 13.5 | `scoring_version`, checked separately, is **3** on every row on both sides (there is no bump on this branch) | high |
| 13.6 | across the full cohort, the new code with the flags off equals rc3 at the production scorer (reference `P0`, realized). This is the §2.6 step 3 check, run as its own command through the `impact_rc3` comparator | high |
| 13.7 | the assists clamp fires on **0** (round, player) rows, and there are **0** ambiguous names. **1,960** assists are removed and **2** assistant names on decided kills are unmapped, as declaration 12's `N+A` replay reported | high on the two zeros; moderate on the counts, which the reference replays again |
| 13.8 | each mutation of a newly fingerprinted input changes the v2 fingerprint: an assistant name, a display name, a match player's `player_id` | high |

**Stop rule.** Any nonzero count in 13.1–13.6 stops the work. The difference is then either explained and eliminated
in the implementation, or reported. **The reference is never adjusted to match.**

#### Declared at freeze, not here

These items need production, so they are left for the freeze declaration (plan §3.1, §1):
- the **activation cohort**;
- the **review cohort's** three rule-exercising match ids;
- the binding **K4 = K5** chain, and the rehearsal-grade K3 = K4;
- the **production row-motion tolerance** around 32.1% of rows and 72.0% of matches reordered.

Nothing on this branch reads production. The separability wording stays "below both carried floors" (R5.7).


### 2026-09-21 (ADDENDUM to declaration 13) — the reference artifacts, recorded before any comparison

Produced by `scripts/postplant_v4_decl12.py --dump-rows` at **`c470670`** on branch `v4-reference-f96aee9`, a
scripts-only commit on top of `f96aee9`. At that commit `git diff f96aee9 -- webapp/app` is **empty**, and the script
itself refuses to run otherwise. The run used Python 3.13.15 against `valo_v4`.

- rc3's configuration was read as data from `docs/superpowers/impact-rc3/candidate-manifest.json`,
  `comparators.impact_rc3` (LF sha256 `8e5c637b34b2ccb767fe2d16b18017eb8571f158623653c8f9bc4bf2ae10e20c`). It was not
  verified, and `active_scoring_config()` was not consulted.
- The cohort is **3,198** matches, and the id-list sha256 is `8d97eba9…b973b`, equal to declaration 13's.
- Each artifact has one CSV row per `(round_id, match_player_id)`, sorted by that key. It carries every
  `CalculatedImpact` field, with `trade_detail` as canonical JSON.
- Each artifact holds **674,530** rows.

| artifact | sha256 |
|---|---|
| `ref_P0_exante.csv` | `f286f4b0c21dcb636ffa6f2bbd424503f1640382d39de260fbd6dd40e9dd0ca6` |
| `ref_N_exante.csv` | `9fd87abfd6157c2d9603c0c06fe9f87b49defaadad0bb803d3d422a83aa1f464` |
| `ref_NplusA_exante.csv` | `5ace786c36ee7664188a3f54619e40247f46e722baf3ec0e98d3d7c858d10031` |
| `ref_P0_realized.csv` | `8109eb685970040af390cec2a80d58022a7593e7b7ef00489d129d307496b7bb` |
| `ref_N_realized.csv` | `4338589db37f50039de6cde6fc502bbf1822c6b784d4cb8edd33bbe29a8da451` |
| `ref_NplusA_realized.csv` | `c1c2a19346633b68a3ad9fdeab7a56949719560d2610ae97db36e33dbe7b4fbd` |
| `reference_sidecar.json` | `a82f9d6a95781973d535f9a4cf8d1fe3b74957ade896dc1bb4a468d1cac46303` |

The artifacts are stored in `~/Documents/valo-backups/v4-release/reference/`.

The reference's own `N+A` hook reported **1,960** assists removed and **2** unmapped in each mode. That reproduces
declaration 12's replay, and it is the reference side of prediction 13.7.


### 2026-09-21 (RESULT, declaration 13) — the implementation reproduces the independent reference exactly, in both modes, on every row

**Every prediction held.** All six comparisons are **0 rows differing** over **674,530** rows each, with identical key
sets and identical `scoring_version`, and each implementation CSV is **byte-identical** to its reference artifact. The
assists counters match declaration 12's replay exactly. Nothing was adjusted on the reference side, and nothing
needed to be.

Branch `impact-v4-implementation` (from `4ecf7f0`), Python 3.13.15, `valo_v4` (3,198 matches, id-list sha256
`8d97eba9…b973b` as declared). Reference: `c470670` on `v4-reference-f96aee9`, whose `webapp/app` is production rc3.
Artifacts: `~/Documents/valo-backups/v4-release/equivalence/step2_all_pairs.json` and
`equivalence_step3/step3_flags_off_vs_rc3.json`.

| comparison | comparator scored | rows | rows differing | keys only on one side | bytes equal |
|---|---|---:|---:|---:|---|
| `P0` ex-ante | `impact_rc3` (flags off) | 674,530 | **0** | 0 / 0 | yes |
| `N` ex-ante | `impact_v4_n` | 674,530 | **0** | 0 / 0 | yes |
| `N+A` ex-ante | `impact_v4` | 674,530 | **0** | 0 / 0 | yes |
| `P0` realized | `impact_rc3` (flags off) | 674,530 | **0** | 0 / 0 | yes |
| `N` realized | `impact_v4_n` | 674,530 | **0** | 0 / 0 | yes |
| `N+A` realized | `impact_v4` | 674,530 | **0** | 0 / 0 | yes |
| §2.6 step 3, its own command: `P0` realized vs the production scorer | `impact_rc3` | 674,530 | **0** | 0 / 0 | yes |

`scoring_version` was projected out of the row comparison and checked separately: **3 on all 674,530 rows on both
sides** of every pair, as it must be with no bump on this branch.

The assists hook reported, identically in both modes: **1,960 removed, 0 clamped, 2 unmapped, 0 ambiguous**, over
3,891 kills made after their round was decided.

#### Predictions, scored

| # | prediction | outcome | |
|---|---|---|---|
| 13.1 | flags off = reference `P0`, 0 rows, both modes | 0 and 0 | **right** |
| 13.2 | `impact_v4_n` = reference `N`, both modes | 0 and 0 | **right** |
| 13.3 | `impact_v4` = reference `N+A`, both modes | 0 and 0 | **right** |
| 13.4 | identical row sets and counts in all six pairs | 674,530 each, no key on one side only | **right** |
| 13.5 | `scoring_version` 3 on every row on both sides | 3 × 674,530 on both | **right** |
| 13.6 | full-cohort flags-off = rc3, as its own command | 0 rows differing, bytes equal | **right** |
| 13.7 | clamp 0, ambiguous 0, removed 1,960, unmapped 2 | 0 / 0 / 1,960 / 2 | **right** |
| 13.8 | each newly fingerprinted input moves the v2 fingerprint | assistant payload, display name and match-player remap each move it; an unrelated match's rename does not; a non-assistants `source_meta` key does not | **right on the v2 half; the v1 half was not measured** |

**13.8, honestly.** The second clause — "and none changes the v1 one except where v1 already read it" — was **not
measured**, and cannot be on this checkout: v1 fingerprints are computable only by code that predates the contract.
It is recorded as unmeasured rather than as passed. What is measured is the v2 contract's own behaviour
(`tests/test_impact_manifest_v4.py`), including that only the `assistants` key of `source_meta` is read.

#### The measurement cohort under the new contract

| | |
|---|---|
| cohort fingerprint, **v2** contract | `44367dead07770b2f94f5718442a116457fa9e88757936777e064fd376554001` |
| per-match v2 fingerprints | `~/Documents/valo-backups/v4-release/measurement/cohort_v2.json`, sha256 `6d8569a945c43ae7e4c134d09a465ae8aac3c30bac872fa9d92e37b54ad5657d` |

The v1 figure declaration 13 pinned (`ff0854b9…81aa`) is unchanged and not rewritten; the two are simply not
comparable, which is what the version is for.

#### Two departures from declaration 13's letter, both recorded rather than waved through

1. **The assistants projection is computed in Python, not in SQL.** Declaration 13 wrote the contract as
   `(k.source_meta::jsonb -> 'assistants')::text`. The fingerprint also runs on sqlite (the manifest's own tests use
   it), where that cast does not exist, so `events` selects `k.source_meta` and the projection is
   `json.dumps(source_meta["assistants"], sort_keys=True, separators=(",", ":"))`, with `None` when the key is
   absent. Same content and the same canonical intent; a different text form, which matters to nothing because only
   v2 code ever computes a v2 fingerprint.
2. **`build_kwargs()` emits the two flags only when True**, which declaration 13 did not say. It is the same rule the
   manifest's serialisation follows, and it is load-bearing: the K-chain compares a comparator's kwargs against
   explicitly built kwargs (`tests/test_export_configuration_sources.py`), and always emitting the keys made rc3's
   kwargs a different dict. With this rule an rc3 configuration's kwargs are exactly what they were before v4
   existed.

#### The suite, and the 15 tests that fail on this branch

Offline on **3.11 and 3.13**: identical results on both — 1,366 passed, 3 failed, 13 errors, 77 skipped. With the
local scratch database `valo_v4_test` on 3.13: 1,430 passed, 4 failed, 13 errors.

- **2 failures are pre-existing**, identical on `origin/main`: `test_default_persistence_path_is_unchanged_legacy`
  and `test_runtime_default_is_the_live_legacy_formula` (the latter asserts `ACTIVE_MANIFEST is None`, which stopped
  being true when rc3 was activated).
- **15 are branch-only, and all 15 have one cause**: `compute_impact_for_match` / `active_scoring_config()` resolve
  the repository's **rc3** manifest, whose source digests this branch's `impact.py`, `impact_config.py`,
  `plant_window.py` and `player.py` no longer match (and whose fingerprints are v1). That is exactly what plan §1
  says must happen to any §2 change. They are the 13 errors in `test_backfill_impact_candidate` (its `world`
  fixture scores three matches before it monkeypatches `ACTIVE_MANIFEST`),
  `test_release_candidate_review_rc3::test_site_before_is_what_production_stores_not_a_replay`, and
  `test_impact_exante_swing::test_builder_matches_stored_values`.
- **Not grandfathered as environmental.** The same 15 fail on `origin/main` **under Python 3.11**, because rc3's
  manifest records `python: "3.13"` and fails the same verification there. So the failure is "the active manifest
  cannot verify against the running checkout", not anything about v4's scoring, and it resolves at activation, when
  `ACTIVE_MANIFEST` names the v4 manifest frozen from this code. Whether to make those three fixtures pass an
  explicit configuration instead of resolving the active one is an **owner decision**, left open here.
- One failure appears on `origin/main` only and is a database-state artifact, not code:
  `test_every_declared_table_is_gated_for_every_write`, because the scratch database's gate was installed from this
  branch's SQL and therefore also guards `players`.


### 2026-09-21 (ADDENDUM to RESULT, declaration 13) — the equivalence checker could not fail; fixed, and the equivalence re-run through it

**An external read-only review of the branch found that the instrument behind 13.1–13.6 could not fail.** The
result does not change. What changes is what the result rests on.

**What was wrong** in `scripts/compare_v4_reference.py`, both confirmed by the reviewer:

1. **A corrupted reference could produce a clean report.** The reference CSV was read into a dict, so a duplicate
   key was silently collapsed. A conflicting duplicate placed before the correct row therefore vanished. The row
   count was taken from that dict, which hid the duplicate. And `reference_sha256` was **copied from the sidecar**
   rather than computed from the file actually compared, so `bytes_equal: true` could be reported against a file
   that had changed.
2. **A failed equivalence exited 0.** `scoring_version` was only reported. A score difference, an unequal key set or
   an unexpected version all left the command's exit status at success. Any chain relying on that status would have
   carried on past this declaration's stop rule.

**Why the recorded result survives regardless.** When the RESULT above was written, byte equality was also checked
outside the checker: `sha256sum` of each reference file against each implementation file, all six equal. The
reviewer independently re-hashed and scanned all six pairs as well, and found 674,530 unique keys per pair and
version 3 throughout. So the claim was true. It was simply not proved by the tool that claimed to prove it.

**The fix** (`6d00d08`, with tests that corrupt the reference and the implementation each way, `test_compare_v4_reference.py`):
- each reference file is hashed and must equal its pinned sha256 and row count;
- a duplicate key stops the run;
- `--expect-scoring-version` is required and is a failure condition on **both** sides;
- the command exits **1** on any problem, after writing the full report.

The companion review findings (rollback drift on swap entries that predate `players`, `state` discoverability, the
decided rule on a NULL `plant_time`, malformed `--pairs`) were fixed in `9bb5618`. None of them touches a scoring path.

**The re-run through the fixed checker, `--expect-scoring-version 3`:**

| command | pairs | rows differing | keys on one side only | reference hash (computed from the file) = pinned | exit |
|---|---|---:|---:|---|---:|
| all six pairs | P0 / N / N+A × ex-ante / realized | **0** each | 0 / 0 each | yes, all six | **0** |
| §2.6 step 3, its own command | P0 realized | **0** | 0 / 0 | yes (`8109eb68…`) | **0** |

Every implementation artifact is again byte-identical to its reference. The assists counters are unchanged:
1,960 removed, 0 clamped, 2 unmapped, 0 ambiguous in both modes.

Reports:
- `~/Documents/valo-backups/v4-release/equivalence_rerun/step2_all_pairs_hardened.json`, sha256
  `7364b486caca85d5c33028886b3c33c479c0a1af52d7beebc93832e6f2ef2395`;
- `equivalence_rerun_step3/step3_flags_off_vs_rc3_hardened.json`, sha256
  `f6cad5056f4cbe167e3fc06ff877ae679f16cc2063eea18ddcbff9610d3996e0`.

**The lesson worth keeping:** a checker whose only outcome is "pass" is not evidence. Every instrument a declaration
relies on needs a test showing it can fail, run through the same entry point the declaration uses.


### 2026-09-22 (DECLARATION 14, freeze) — v4 is frozen on a sixteen-match review cohort chosen on the restore it is reviewed against

Written and committed **before** `freeze_impact_candidate.py` runs. This is process §F2
(`SCORING-RELEASE-PROCESS.md`) and plan r5 §3.1–3.2. Gate G3 was given by the owner on 2026-09-22.

#### The state it is frozen from (F0, read-only)

Production `valowithfriendsdb` at 2026-09-22 04:44:55 UTC:
- alembic `0010`;
- **3,649 matches**, max id 3657;
- gate `open` for `impact-rc3`, admin `rc3-runbook`;
- `impact_scores` 769,120 rows, all `scoring_version` 3;
- `impact_scores_v1` present, `impact_scores_v3` free.

Backup B0: `B0-v4-valowithfriendsdb.dump`, sha256 `0d1723cf4deffcd47ff0a2184115ea141bdcf69c69c4242ebda13c201d9789f5`.

It was restored into `valo_v4_rehearsal` on the same instance. The restore's gate is closed (`impact-rc3` /
`v4-runbook`), and its preflight is equal to production's.

#### The review cohort (F1): sixteen matches

- rc3's thirteen: `3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133`.
- Three matches that exercise v4's rules, each the newest qualifying match **on the restore**
  (`impact-v4/review-cohort.sql`):
  - **3655**: an assist on a kill after the round was decided. The `impact_v4` scorer removes 1 assist there;
  - **3652**: a kill after a defuse;
  - **3642**: a Time Win round with a kill after 100 s.

The cohort is chosen on the restore, frozen from production, and reviewed on the restore. The freeze is therefore
followed immediately by `verify_source_snapshots` against the restore (F3). A mismatch means re-restore and freeze
again; the manifest is never edited.

#### The K-chain

- **K3** (`--comparator impact_v4`) and **K4** (`--manifest`), both exported from production at F4, over every match
  then present. Their common hash is **`PREP_CHAIN`**. It is preparation-grade: it proves the frozen manifest
  reproduces the comparator on production as it stood then. **No later verification expects it.**
- **K4 and K5 in the window**, on the gated dataset after the gate closes, are **binding**. Their common hash is
  **`CHAIN`**, which the window's `verify-build` and `verify-live` expect.
- The **activation cohort** is every match in production at the moment the gate closes in H1.1.

#### Production row motion

The motion measured on the local corpus (rc3 live, `N+A`) was **32.22% of player-rounds changed and 71.9% of matches
reordered**; for `N` alone it was 32.14% and 72.0%. The declared tolerance for `impact_v4` on production is:

- **player-rounds changed: 27.2% to 37.2%** (±5 points);
- **matches reordered: 64.9% to 78.9%** (±7 points).

Why this width: 3,198 of production's 3,649 matches (87.6%) are the measured corpus. If the other 451 moved not at
all, the row figure would fall to about 28.2%, still inside the band. To pass 37.2%, more than about 73% of their
rows would have to move, against 32% measured. So the band holds for any plausible mix of new matches, and a result
outside it means a real change in behaviour.

Row motion is measured on the restore during rehearsal (rehearsal-grade), and **in the window** between the pre-swap
capture and the swap: the capture (rc3 as stored) against K5's rows (v4), by key. **Outside the band, the swap does
not happen** until the difference is explained. As always, motion is not improvement.

#### Wording carried

`N+A vs N` stays **UNTESTABLE, below both carried floors** — not "below any plausible rc3 floor" (R5.7). `N+A` ships
on the owner's concept, not on evidence.

#### Predictions (scored in a RESULT entry, whatever happens)

| # | prediction | confidence |
|---|---|---|
| 14.1 | the freeze succeeds from a clean tree, and `verify_source_snapshots` against the restore passes for all 16 matches | high |
| 14.2 | K3 = K4 on production (`PREP_CHAIN`) | high |
| 14.3 | the decomposition check on the 16-match cohort reports **0 mismatches** | high |
| 14.4 | `review-results.json` covers exactly the 16 frozen matches | high |
| 14.5 | at least one removed post-decided assist and at least one zeroed decided kill are visible in the reviews of 3655 / 3652 / 3642 | high |
| 14.6 | rehearsal-grade row motion on the restore falls inside the declared band | moderate |
| 14.7 | in the window, K4 = K5 (binding), and row motion falls inside the band | high on K4 = K5; moderate on motion |

**Stop rule.** Any of 14.1–14.4 failing stops the release at F until the cause is eliminated or reported. 14.7 failing
means **no swap**.


### 2026-09-22 (RESULT, declaration 14 — phase F) — the freeze holds, and the chain closes on production

Phase F of the process is complete. **Predictions 14.1 to 14.5 are all right**; 14.6 and 14.7 belong to rehearsal and
the window and are not yet scored. Production was only read. Nothing is merged, deployed or activated:
`IMPACT_CALCULATION_VERSION` is 3 and `ACTIVE_MANIFEST` is still rc3's.

| # | prediction | outcome | |
|---|---|---|---|
| 14.1 | the freeze succeeds from a clean tree, and the restore matches all 16 fingerprints | frozen at `f9b5cc0`, manifest LF-sha256 `2f33f137…`; `verify_source_snapshots` on the restore printed "restore matches the freeze" | **right** |
| 14.2 | K3 = K4 on production (`PREP_CHAIN`) | **EQUAL** `2cd448e2edb3d4616dfd3ce7abf0150a33a851ab126dc21245ef3e430c4424c2` | **right** |
| 14.3 | the decomposition check reports 0 mismatches | **29,606 checks, 0 mismatches** over the 16 matches | **right** |
| 14.4 | `review-results.json` covers exactly the 16 frozen matches | exactly the 16 | **right** |
| 14.5 | a removed post-decided assist and a zeroed decided kill are visible in 3655 / 3652 / 3642 | zeroed decided events 2 / 4 / 2, assists removed 1 / 1 / 3 (−100 / −100 / −300 on the assists component) | **right** |
| 14.6 | rehearsal-grade row motion inside the declared band | not yet run (§G) | — |
| 14.7 | in the window, K4 = K5, and row motion inside the band | not yet run (§H) | — |

#### The chain, as it now stands

| link | what it is | value |
|---|---|---|
| `PREP_CHAIN` | K3 (comparator `impact_v4`) = K4 (frozen manifest), both on production over the 3,649 matches present at F0, 769,120 player-rounds | `2cd448e2edb3d4616dfd3ce7abf0150a33a851ab126dc21245ef3e430c4424c2` |
| cohort fingerprint | equal in both sidecars, contract **v2** | `768c86b30b68d77a…` |
| both exports | revision `2e5140e`, artifact contract 2, `impact_calculation_version` 3 (no bump on the branch) | |

K3 took 45.6 minutes and K4 44.7, each about 23 minutes of scoring and the rest per-match fingerprinting. Both were
pinned with `--matches` to the id list captured at F0 (sha256 `92077535…`), so a match ingested while they ran could
not make them differ; production's max match id was 3657 before and after. Each export holds the whole corpus in
memory, and a first attempt at running both in one job was killed by the machine's low-memory reaper after K3 had
scored but before it wrote its sidecar. They were rerun one at a time. **That is a lesson for the process: run one
export per job.**

#### The reviews

Every reconciliation passes: 5,140 checks over the fixed ten, and 450 to 958 per single match. `review-results.json`
holds all 16. The reviewed "Before" column is what production stores, which is rc3, so the tables are the rc3 → v4
comparison the owner's G4 look needs.

Across the ten fixed matches, 100 player-rounds: **every one changes** (mean −155, median −115, p5 −915, p95 +371),
and **6 of 10 matches change at least one player's rank**. The largest single move is −1,949 (match 3115), which also
swaps rank 1 and 2 there. This is consistent with the corpus measurement behind declaration 12 — about a third of
rows and about 72% of matches — and it is the visible consequence of removing the post-plant ramp and the
plant+38..45 override.

**Motion is not improvement.** The evidence that v4 is better is declaration 12's, on both targets; this entry only
records that the freeze reproduces, that the reviews reconcile, and what the site will look like.

### 2026-09-23 (RESULT, declaration 14 — rehearsal and window; ACTIVATION NOTE) — v4 is live, ingestion is reopened, and the motion gate was skipped before the swap

Impact v4 is live at `IMPACT_CALCULATION_VERSION` 4, and ingestion reopened under it at gate G7. Declaration 14's last
two predictions are scored below, including how late one of them was measured. The full record is
`impact-v4/README.md` sections H1 and I.

| # | prediction | outcome | |
|---|---|---|---|
| 14.6 | rehearsal-grade row motion inside the declared band | **32.42% of rows / 72.13% of matches reordered** (band 27.2–37.2 / 64.9–78.9). Measured at close-out from the rehearsal's own files, **not during rehearsal** | **right, measured late** |
| 14.7 | in the window, K4 = K5 (binding), and row motion inside the band | K4 = K5 **EQUAL** `2cd448e2…` (= `PREP_CHAIN`). Motion **32.42% / 72.13%, inside**, but measured at close-out from the window's files (pre-swap capture against `K5.load.csv`), **after the swap** | **right**; the motion half was **not checked before the swap** |

**The stop rule was not enforced as written.** Declaration 14 says a 14.7 failure means no swap. The window swapped
without measuring motion. There is no record of it in H1 and no artifact of it, so it was skipped, not lost. It
would have passed, since the window's rows are byte-identical to the rehearsal's (`K5.load.csv` sha256
`32f3994c…` in both, and byte-identical pre-swap captures). But passing afterwards is not the same as gating. The
process doc's H1 step 4 now puts motion first, as a printed check. Method: rows changed = `impact` differs by
`(round_id, match_player_id)`. Reordered = the players' order by mean Impact changed, as in declaration 12's measurement.
Totals: 249,349 of 769,120 rows, 2,632 of 3,649 matches.

#### Activation note

| what | value |
|---|---|
| manifest frozen | `2e5140e` (LF-sha256 `2f33f137…`) |
| activation commit / PR | `fe5411b`, PR **#71** merged 2026-09-23 07:30:16 UTC as **`e2453ce`**, Render deploy green |
| pre-release production `main` | `6f45476` (PR #70) |
| `CHAIN` | `2cd448e2edb3d4616dfd3ce7abf0150a33a851ab126dc21245ef3e430c4424c2` (K4 = K5 = `PREP_CHAIN`) |
| load artifact / read-back | `32f3994c…`, 769,120 rows |
| pre-swap capture (rc3 rows) | `37c81af806c58ec559deb4c9c8b47fafc4e1eac6c7061e5dbde2a0cb44865a65` |
| W0 backup | 49,712,365 bytes, `fd6b7f6cdfa4b28f72c9679794b40310452781f8603a7257366ed52f772a2d8a`; R3 restore point `2026-09-23 04:16:10.790079+00` |
| activation cohort | 3,649 matches, max id 3657, id-list sha256 `92077535…` |
| cache version | `4003003004` |

| step | at (UTC) | duration |
|---|---|---|
| gate closed (H1.1) | 04:16:07 | |
| K4 / K5 | | 45.0 min / 42.3 min |
| build (release log 6) | 06:48:09 | 1.07 min, oid 80081 |
| verify-build (7) | 07:11:40 | 22.5 min, clean |
| **swap (8)** | **07:12:54** | **36.98 s**; oid 65265 → `impact_scores_v3`, 80081 → `impact_scores` |
| PR #71 merged | 07:30:16 | |
| verify-live (9) | 08:02:34 | 22.5 min, clean |
| cache DELETE + prewarm | | 1 row deleted; 12 players in 2 m 39 s; 24/24 scopes, agreement clean |
| acceptance replay | | 3,649 matches, **0 differ**, 27.1 min |
| verify-live again (10) | 09:01:10 | 23.4 min, clean, max match id still 3657 |
| hold | 07:12:54 – 21:25:55 | **about 14h13m**, ended early by the owner after checking the site |
| **gate opened for `impact-v4` (G7)** | **21:25:55.346579** | `open`/`impact-v4`/`v4-runbook` |
| catch-up ingest | 21:26:33 – 21:38:23 | 12 matches, 3658–3669 |

**Gate transitions.** `open`/`impact-rc3`/`rc3-runbook` → `closed`/`impact-rc3`/`v4-runbook` (04:16:07, "impact-v4
activation window") → `open`/`impact-v4`/`v4-runbook` (21:25:55, "G7: hold ended early by owner; inventory 11/12,
SambuUwU#NA1 private accepted").

**G7.** The inventory was 11 of 12 players complete, and SambuUwU#NA1's private profile was accepted by the owner. It
expected 12 matches. The canary, **3658**, was at `scoring_version` 4 and equal to its replay: the first real exercise
of the adapter's commit-then-score path under v4, which the rehearsal had waived. All 12 matches reconciled, each
present, rows = rounds × 10, at version 4 and equal to its replay. The sweep through the reopening moment found
nothing new. Probe (a) from `6f45476` was refused, with scores unchanged. **Production now: `impact_scores` 771,780
rows, all version 4; 3,661 matches.** Ordinary rollback ended at 3658. From here, fix forward.

**Retention.** `impact_scores_v3` (rc3's 769,120 rows) is kept at least through the 7-day recovery window.
`impact_scores_v1` is kept to 2026-10-03, per rc3. The two rehearsal databases and the rehearsal and release-tools
worktrees await the owner's say-so.
