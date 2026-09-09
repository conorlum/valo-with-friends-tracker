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
