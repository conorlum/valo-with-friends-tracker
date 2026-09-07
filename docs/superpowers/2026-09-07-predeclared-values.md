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
| the early-regime wealth readout mapping | non-negative; reads total wealth (`loadout + remaining`) in round N+1; aggregation, direction and range declared before choosing |
| the early-regime commitment gate | non-negative; must send a fully-saving team to ~0; its saving threshold declared before choosing |
| `ECON_SCALE` | a dispersion convention: matches `econ_component`'s SD to `time_impact`'s current SD, over all scored player-rounds in realized mode |
| distributional reporting thresholds | SD, p1/p5/p95/p99, largest per-player-round change, rank movement on the leaderboard -- each with a number declared before the rescore |

## Amendments

None yet. Append here with date and reason; do not edit rows above in place.
