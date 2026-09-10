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
