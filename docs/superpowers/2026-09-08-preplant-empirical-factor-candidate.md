# Empirical pre-plant timing factor: fitted review candidate

Date: 2026-09-08. User request: follow the state-adjusted data, state conclusions,
and make a fitted function that returns a factor for Impact.

Status: implemented as an additive candidate. No changes to the live scoring
path, calculation version, stored scores, or existing spec requirements. This
candidate permits a non-monotone curve and is therefore not a claim that the
existing Part 3 monotonicity contract has been satisfied.

## A. Conclusions supported by the measurements

1. Timing is associated with round outcome even after standardizing on exact
   pre-kill alive-count state. The attacker b4 and b16 rates are 74.262% and
   81.936%; the paired difference is -7.673pp, with a previously reproduced
   match-bootstrap interval of [-9.787, -4.737]pp.
2. A simple assumption that attacker kills closer to the plant always receive
   a larger timing adjustment does not describe these data. The broad adjusted
   curve is lower around 2-5s, higher around 15-24s, and then lower again.
   Individual one-second wiggles are much less certain than the broad contrast.
3. The raw curve answers a different question because favorable alive states
   are distributed differently over time. It gives 82.336% versus 81.604% at
   b4 and b16. State adjustment reverses that comparison.
4. Side matters descriptively: defender adjusted win rates generally rise
   toward planting, while attackers show the middle-window elevation. Use
   separate curves when following those marginal adjusted estimates.
5. This supports an empirical retrospective timing adjustment, if adopted as
   a scoring policy. It does not identify individual causal kill value or
   whether a team should plant faster. Later combat, selection into planted
   rounds, economy, positions, and lifecycle ambiguity remain limitations.

## B. Implemented function

`webapp/app/scoring/preplant_empirical_factor.py` exposes:

```python
empirical_preplant_factor(
    dt,                  # plant_time - kill_time, in seconds
    is_attacker,         # KILLER's side
    strength=1.0,
    use_realized=True,
)
```

The curve is estimated after accounting for state; the resulting runtime
function only needs time and killer side. It does not assert that every exact
state has the same response. It is the timing response averaged over the
published reference-state distribution and supported cells.

For 0 < dt <= 30:

```
factor(side, dt) = clamp(
    1 + strength * (fitted_adjusted_rate(side, dt) - reference_rate(side)),
    0.2, 1.7
)
```

The reference is each side's adjusted dt>30 rate:

- Attacker: 0.7495589315301505.
- Defender: 0.3786676668318836.

Probabilities enter as fractions, not numbers from 0 to 100. Thus strength=1
maps a +1 percentage-point difference to a +1% timing-component multiplier.
Strength=2 doubles the departures from 1 before clipping; strength=0 is neutral.
The magnitude is an explicit scoring choice, not something identified by the
win-rate data. Strength=1 is an illustrative default, not a calibrated selection
from the older log-odds k grid.

Use the same event scalar for kill and death transfer, always using the killer's
side; do not call the defender curve merely because the victim is a defender.

## Fitting method and reproducibility

Input: the existing `diagnostics/preplant_dip_investigation_buckets.csv`, whose
point estimates were independently reproduced in this task. No fresh DB query
was required. The incorrect regression-reference columns are not used.

For each side separately, fit 30 unknown rates f_i by minimizing:

```
sum_i weight_i * (f_i - observed_adjusted_rate_i)^2
    + penalty * sum_i (f_i - 2*f_(i+1) + f_(i+2))^2
```

Weights are inverse squared widths of the marginal match-bootstrap intervals,
normalized to mean 1. Penalty=1 is a light, explicit smoothing choice. Penalties
0, 1, 4, and 16 are recorded as sensitivity comparisons, not as independent
validation or outcome-tuned parameter selection. The fit uses NumPy; runtime
evaluation uses only the standard library and the adjacent JSON snapshot.

The one-second observations are located at bucket midpoints 0.5, 1.5, ..., 29.5.
Evaluation linearly interpolates the fitted rates, holding the first/last rate
constant inside the outer half-bins. Consequently the function's value at
dt=4 interpolates around 4 seconds; b4's fitted value is located at dt=3.5.
No monotonicity or sub-10-second plateau is imposed.

Descriptive in-sample fit errors against the 30 adjusted estimates:

| Side | RMSE | Effective degrees of freedom |
|---|---:|---:|
| Attacker | 0.577 percentage points | 12.35 |
| Defender | 0.935 percentage points | 12.42 |

These are fit residuals, not prediction error estimates. The input intervals
remain uncertainty intervals on the observed buckets, not on the fitted line.
Cross-bucket covariance is unavailable, so precision weighting is approximate.
The source's per-bucket sparse-state coverage differences also carry through.

Reproduce from the implementation worktree with Python plus NumPy:

```powershell
python docs/superpowers/diagnostics/fit_preplant_empirical_factor.py
```

The generated `preplant_empirical_factor.json` records all fitted rates,
observations, counts, coverage, source SHA-256, smoothing sensitivity, and
reference assumptions. Runtime evaluates that exact fitted table, avoiding
the old shape-times-amplitude reconstruction mismatch entirely.

## Example factors before contribution centering

| Seconds before plant | Attacker factor | Defender factor |
|---|---:|---:|
| 1 | 1.0065 | 1.1660 |
| 4 | 0.9989 | 1.1345 |
| 10 | 1.0436 | 1.0124 |
| 16 | 1.0637 | 0.9831 |
| 20 | 1.0675 | 0.9610 |
| 25 | 1.0634 | 0.9616 |
| 30 | 1.0440 | 0.9452 |
| >30, pooled reference category | 1.0000 | 1.0000 |

At strength=1, a base kill-order credit of 100 at dt=16 for an attacker would
become about 106.37 in the time component. With the current equal weights for
econ/time/swing, that increases the combined kill-credit contribution by about
2.12 points before rounding if the other components are unchanged. It does not
multiply damage, assists, or the entire final Impact score by 1.0637.

## Boundaries and remaining integration work

- The saved data pool all dt>30 observations. REF cannot be placed at second
  31 or used to infer a smooth decline after second 30. The candidate treats
  it categorically. This deliberately leaves a boundary jump: attacker 1.0440
  to 1.0000, defender 0.9452 to 1.0000. This is a modeling discontinuity, not
  evidence of an in-game threshold. Resolve it with finer data beyond 30s or
  an explicitly chosen transition policy before live activation.
- Missing/invalid timestamps, dt<=0, and use_realized=False return 1 exactly.
  Never-planted/phantom rounds must pass dt=None; this leaf receives no round
  object and cannot determine phantom status itself. Post-plant scoring must
  still use its own regime.
- The candidate is uncentered. Live activation needs per-event replay and
  contribution-weighted centering, `c = sum(K)/sum(K*factor)`, on a precisely
  declared affected population, followed by the death-side residual check.
  This must be recalculated for the chosen strength and boundary policy.
  Bucket kill counts cannot substitute for kill-order contribution weights.
- Fresh match-based validation, lifecycle-consistent state reconstruction,
  and actual player/kill redistribution checks remain necessary to assess
  deployment behavior. Good fit to the saved curve does not establish those.

Validation: 22 focused database-free tests passed, including unit conversion,
interpolation, forward-mode neutrality, bounds, invalid inputs, smoothing known
signals, and retaining the measured opposite attacker/defender timing orders.
