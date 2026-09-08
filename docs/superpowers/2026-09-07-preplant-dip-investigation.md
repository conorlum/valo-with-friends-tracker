# Investigation: the pre-plant shape curve's non-monotonic dip

**Status:** Investigation complete, but its two headline claims are
corrected by an independent verification --
`2026-09-08-preplant-dip-independent-verification.md` -- read that
alongside this document, not instead of it:

- The `dt~2-5s` dip is real (this document's finding), but the "faster
  plants" causal reading the user leaned toward afterward is **rejected**:
  the raw, unadjusted attacker win rate is actually *higher* at `dt~4`
  than `dt~16` (+2.15pp) -- the entire reported effect is manufactured by
  the pre-kill state adjustment, which can't see the subsequent kills that
  happen between the marked kill and the plant. Small `dt` is a fast,
  forced plant into an uncleared site, not a wasted kill. Do not use the
  empirical `dt` curve as a scoring input.
- "The fitting/runtime mismatch is real and large... I fixed this" (this
  document's Finding 1) was **wrong on the fix at the time it was written**.
  `350b0c9` fixed only the *fitting* instability; the *runtime
  reconstruction* formula (`shape(dt) * logit_lift(adv, side)`) remained
  mismatched against what the model actually fits, diverging by up to 3.2
  logits. See the verification's Bug B. **That has since been fixed** --
  `fit_preplant_time_model` now profiles `shape_mid_ratio` by grid search
  against the product directly, so `shape(dt) * logit_lift(adv, side)` *is*
  the fitted linear predictor rather than a reconstruction of one;
  re-running the diagnostic against the full DB reports
  `max |delta| = 0.0000 logits`. Note what the corrected fit then shows:
  `shape_mid_ratio = +1.545`, i.e. the honestly-fit shape is
  **non-monotonic** (it overshoots the near-plant plateau at the middle
  knot), which is what ultimately led Part 3 to ship an empirical curve
  instead -- see the spec's "DECIDED 2026-09-08" section.

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
regression's log-odds-vs-reference curve (trough −0.19 to −0.22 at `dt=2-4`
against a `dt=15-28` plateau of +0.21 to +0.26 -- corrected from an earlier
`+atk_main` labelling bug, see
`2026-09-08-preplant-dip-independent-verification.md` section 7 Bug A; the
contrast itself is unaffected), independently confirming it.
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
`dt=1` value (half A: −0.075 vs +0.319 and −0.056; half B: −0.254 vs +0.206
and −0.123 -- corrected from the same labelling bug as above; `atk_main`
cancels in every one of these differences, so the dip-below-plateau-and-b1
verdict is unchanged in both halves).

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
