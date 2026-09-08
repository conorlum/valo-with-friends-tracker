# Investigation: the pre-plant shape curve's non-monotonic dip

**Status:** Investigation complete. Decision on how to treat the remaining
real (small) non-monotonicity is still open -- see "What this doesn't
decide" below.

**Why this exists.** Running `scripts/fit_preplant_time_factor.py` (Part 3
implementation plan, Task 7) against the real DB produced a fitted
pre-plant `shape()` curve that dipped and reversed sign around
`dt = seconds_to_plant = 20`, violating the spec's own monotonicity
requirement (`specs/2026-09-03-plant-window-and-time-factor-design.md`,
Part 3 Testing: "shape() is monotone non-decreasing in proximity up to the
10s plateau"). This investigation determines whether that was a
fitting/scoring bug, an unstable 2-knot estimate, or a real feature of the
data -- kept entirely separate from live scoring: no rescore, no
`IMPACT_CALCULATION_VERSION` change, no relaxation of the monotonicity
requirement.

**Artifact (charts + narrative):** https://claude.ai/code/artifact/f1dfed95-e951-4284-99a4-197a3dc40aba

## Method

- `docs/superpowers/diagnostics/investigate_preplant_dip_1s_buckets.py` --
  the main investigation. Population: the same 168,432 usable pre-plant
  observations `preplant_time_model.extract_preplant_observations` produces
  (0 excluded for an undeterminable winner on this snapshot). Buckets: `b1`
  through `b30` (`dt` in `(k-1, k]`), a `b0_exact` bucket (0 observations --
  extraction requires `dt > 0` strictly, so a kill exactly at the plant
  instant can never appear), and a `REF` population (`dt > 30`, the far
  reference the shape curve is already pinned to zero at).
- Two independent state-adjusted estimates, deliberately not sharing the
  shape/amplitude decomposition that turned out to be the bug:
  1. **Direct standardization** (Mantel-Haenszel style): per bucket and
     side, a weighted average of each exact state's raw win rate, weighted
     by that state's overall share among all usable observations for that
     side. Cells under 20 observations are dropped and reference weight
     renormalized over the remaining states (coverage reported per row).
     Match-clustered bootstrap, 500 draws, resampling whole matches and
     recomputing the reference weights and the standardization inside each
     draw.
  2. **A joint regression**: one logistic fit across every bucket at once,
     `logit(win) ~ exact_state_FE + dt_bucket(ref=dt>30) x side` -- no
     spline, no shape/amplitude split, no monotonicity imposed. 96 columns,
     168,432 rows, ridge `l2=1.0`, standardized before fitting
     (`app.services.stats_math.fit_logistic`/`standardize`/`back_transform`).
- Sensitivity: the original 2-knot model's ridge strength (`l2` in
  `{0.1, 1, 10, 100}`) and a match-based 50/50 split
  (`investigate_preplant_dip_match_split_check.py`, same regression
  methodology as (2), refit separately on each half).
- Outputs: `preplant_dip_investigation_buckets.csv` (one row per bucket:
  counts, raw and adjusted rates by side, bootstrap CIs, the regression
  cross-check's logit-vs-reference), `preplant_dip_investigation_report.json`
  (the mismatch grid, regularization sensitivity, match-split stability,
  per-state breakdown, reference weights), `preplant_dip_match_split.json`
  (full per-bucket coefficients for both match-based halves).

Reproduce: from `webapp/`,
`.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\investigate_preplant_dip_1s_buckets.py`,
then `investigate_preplant_dip_match_split_check.py`. Read-only; ~35s total
against the local DB (3,124 matches).

## Findings

**1. The fitting/runtime mismatch is real and large, and explains the
originally-reported dip's location.** Task 3's `fit_preplant_time_model`
fits `w1` (the `dt=20` knot) as a single coefficient, pooled across side and
advantage -- a deliberate, tested design choice (`shape()` is meant to be a
function of `dt` alone). But `preplant_k_selection.raw_scalar` and
`preplant_scalar.preplant_proximity_scalar` compute
`shape(dt) * logit_lift(adv, side)`, which at `dt=20` evaluates to
`shape_mid_ratio * logit_lift(adv, side)` -- a quantity that *does* vary
with `adv`/`side`, because `logit_lift` does. The model's own true linear
predictor at `dt=20` is the flat constant `theta1_pooled = +0.312` for every
side and advantage; the runtime formula's reconstruction diverges from that
by up to **3.2 logits** (at `adv=+2`, attacker) and is exact only where
`logit_lift(adv, side) == theta2_pooled` (`adv=0`, defender -- not
coincidentally the exact reference cell the reported `shape_mid_ratio` used
as its denominator). Neither of the two independent, decomposition-free
estimates below shows a dip anywhere near `dt=20` -- confirming this was a
reconstruction artifact, not a real feature of the data.

**2. A real, smaller, attacker-only dip exists at `dt` &asymp; 2-5s.**
Both direct standardization and the joint regression -- built independently,
sharing no code path with each other or with the flawed reconstruction --
show attacker kills dipping in the final ~5 seconds before the plant:
adjusted win rate falls from a `dt=10-28` plateau of roughly 0.80-0.82 down
to about 0.75 at `dt=2-5`, before a partial recovery at `dt=1` (0.766). The
95% bootstrap CIs at the trough (`dt=4`: `[0.720, 0.766]`) and the plateau
(`dt=20`: `[0.797, 0.827]`) do not overlap. The same pattern appears in the
regression's log-odds-vs-reference curve (trough ~1.55-1.59 at `dt=2-4`
against a `dt=15-28` plateau of ~1.9-2.1), independently confirming it.
**Defenders show no such dip** -- their curve declines close to
monotonically (in win-rate terms, rises close to monotonically toward the
plant) across the full range on both methods.

**3. The attacker dip survives regularization and a match-based split.**
The original 2-knot model's `dt=20` numbers are essentially insensitive to
ridge strength (`l2` 0.1 to 100 moves `shape_mid_ratio` from -1.122 to
-1.142) -- the mismatch, not the ridge penalty, was the problem. More to the
point, the *corrected* finding (the joint regression's `dt=2-5` dip) was
refit independently on two random halves of matches (1,561 / 1,562 matches,
~84k observations each): in **both halves independently**, the mean
`dt=2-5` coefficient sits below both the `dt=15-25` plateau mean and the
`dt=1` value (half A: 1.598 vs 1.992 and 1.617; half B: 1.612 vs 2.071 and
1.742).

**4. A per-state breakdown found no single state driving it.** The eight
most common exact states each show a similar direction of effect
(`b15-25` window rate above the far-reference rate, per `report.json`'s
`state_breakdown_b15_25_vs_ref`) -- consistent with a proximity effect that
generalizes across states rather than one state's idiosyncrasy leaking
through the pooling.

**What remains uncertain.** This is an association (kill proximity in the
last ~5s vs. round outcome, adjusted for exact alive-count state), not a
causal claim about what happens in those seconds. The `REF` (`dt>30`)
population is itself heterogeneous -- it spans everything from 31 seconds
before the plant back to the start of the round -- so comparisons against it
carry more compositional risk than comparisons between two 1-second buckets;
the defender curve's `REF` value sitting *above* `b30` (0.379 vs 0.315,
CIs non-overlapping) is a visible instance of that and should not be read as
a `dt=30`-to-`dt=31` discontinuity. The mechanism behind the attacker dip
specifically (rather than defender) is not established here.

## What this doesn't decide

Whether to (a) treat the `dt=2-5s` attacker non-monotonicity as a
predeclared, loudly reported finding rather than a hard monotonicity
assertion, (b) leave the spec's imposed sub-10s plateau as-is (which would
flatten over this exact region) and accept the resulting mismatch between
the plateau's flat assumption and this evidence, or (c) revisit the
plateau's boundary/shape specifically for attackers, is a project-owner
call, not resolved by this investigation. The investigation's own
recommendation (option a, not blocking Part 3 on it) is stated in the
artifact but is a recommendation, not a decision record.
