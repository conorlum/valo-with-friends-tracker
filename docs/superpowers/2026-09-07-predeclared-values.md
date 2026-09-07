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
| **adv -1..+1** | the range the middle slope is fitted through (82.6% of affected kills) | time, "Parameterisation" |
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
| **stays 2** | `STATE_DIAGRAM_CALCULATION_VERSION` | both, Rollout |

## To be fixed at first run, and recorded here before any result is read

These are grids and mappings the specs deliberately leave to implementation.
**Write the chosen values into this file and commit, then run.** Backfilling
them afterwards defeats the point.

| value | constraint the spec already imposes |
|---|---|
| `k`, the amplitude scale | policy parameter, not an estimate. Several predeclared values, sensitivity reported. No confidence intervals attached to it |
| `FLOOR` / `CEIL`, post-plant | policy parameters. `FLOOR > 0` is required by the no-negative-kill constraint. Measured ratios span ~0.06 to ~1.50 |
| `W` sensitivity grid | `W = 2` is the default; the grid around it is reported |
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

## Amendments

- **2026-09-07** -- the early-regime `f`/`g` mappings, previously listed
  above as "to be fixed at first run," are fixed. See "The early-regime
  f/g decision" section. Do not edit the rows above in place; the
  strikethrough marks them superseded.
