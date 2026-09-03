# Stage C: refitting the kill-order graph

**Status:** revised after decision review, awaiting human review
**Date:** 2026-09-02

## Revision note (second external review)

A second review of the revised spec raised ten issues. Verified first as
before; all ten confirmed, and one was a bug the first review's own fix
introduced.

**The `q`/`d` recovery broke G3, and the obvious repair was silently wrong.**
Adopting `b = q / d` (first review, issue 1) left G3 shrinking toward a prior
via an offset -- which shrinks **`q`**, not `b`, so the deployed graph converges
on `b_prior / d`. Checked numerically rather than argued: with `d_true = 3` and
a prior of 0.6 everywhere, the offset formulation at strong shrinkage returns
`b = [73.7, 72.2, 73.6, 72.9]`, converging on `b_prior / d = 73.2`. A graph two
orders of magnitude off, produced without any error. G3 now folds the prior into
the damage column (`q = d * b_prior + delta`), which shrinks the deployed graph
correctly and **removes the need for the `offset` primitive the previous
revision added** -- `stats_math` is untouched by this stage after all.

**G5 had the same missing recovery.** Its 18 fitted numbers are `q`s, not
deployable `(w, a, t)`; every one divides by `d`, and the effective price
surfaces and non-negativity check use the recovered values.

**Family B could not attribute a win.** Comparing the full symmetric model
against `stage_a_exact` changes three things at once -- component tilts, a
kill/death base split, and a kill/death tilt split -- so an improvement could
have come entirely from constant kill/death weights. Family B is now a nested
ladder B0-B3, with **B2 vs B0 the primary test** (it isolates the component-
curve hypothesis) and the B3 comparisons secondary. B3 is still built and
reported: the symmetric parameterisation is a deliberate design choice, and
demoting the *comparison* is consistent with this spec already pre-registering
the team-level kill/death contrast as weakly identified.

**Fold compatibility was still not guaranteed.** A dataset fingerprint does not
prove two runs used the same folds -- and Stage A's committed results were
produced with the old permutation `assign_folds`, so an identical match set can
carry an entirely different assignment. Now three identity values are compared,
and the consequence is stated: Stage A must be re-run under `stable_folds`
before the two stages can share a matrix.

**The rejected per-parameter stability rule survived in two places** -- the
success definition and the test list -- after being replaced in the
identification section. Both removed, and the graph-level rule is now
operational rather than qualitative: the bootstrap upper bound of
(fold-to-fold RMS / shipped-to-candidate RMS) below 1.

Also: the prediction verdict split into **A1 (next rounds, T2)** and **A2 (match
outcome, T1)**, because the primaries declare on T2 while the `kill_diff` bar is
a T1 comparison, so a T1 null could have failed a verdict T2 had passed -- and
A2 is the closest thing here to the product question the parent project started
from. G5's per-target arithmetic corrected (59 / 48 / 50, not 59 / 30 / 31) and
its "the data can support it" claim narrowed to *predictive* comparison, since
effective rank 3.30 does not support reading six price surfaces off the fit. The
"nests Stage A exactly" claim corrected to `stage_a_exact`. G1a's **construction**
normalization separated from the display rescale, with its exposure weights
taken from the training fold. The collinearity and target-agreement thresholds
made numeric. And the whole checklist relabelled a **predeclared analysis plan**
rather than a pre-registration, since this dataset was used to design the
candidates and set the thresholds.

## Revision note (external review)

An external methodology review (pre-dating the decision review below) raised
twelve issues. Each was checked against source before being accepted; the
outcome was eight confirmed, two already fixed by the decision review, one
partly wrong, and one carrying a recommendation declined with a reason.

**The most serious was real and was mine.** The spec claimed global scale was
unidentified because `(d, b)` and `(lambda d, lambda b)` are "the same model".
That is true of a *candidate score* feeding a downstream calibration, not of a
logistic fit, where the link pins the scale and ridge breaks the invariance
besides. Worse, rescaling `b` alone for reporting changes its strength relative
to `damage`, so the printed graph was not the candidate that had been
evaluated. There is now one explicit estimator, one recovery equation
(`b_k = q_k / d`, with `d > 0` required), and a rule that every yardstick scores
the recovered candidate. See "The estimator, and how a fit becomes a deployable
graph".

**Confirmed and fixed, each verified first:**

- **`fit_logistic` cannot implement G3.** Read the source: `penalty = np.eye(p
  + 1) * l2`, zero-centred, no offset. Shrinking toward a non-zero prior needs
  an offset. "No new numeric primitives are expected" was false; an `offset`
  parameter and its tests are now specified.
- **Reusing the fold seed does not reuse the folds.** `assign_folds` permutes
  over the *collection*, so changing the match set moves every assignment. Now
  a stable per-match hash plus a dataset fingerprint that refuses to print a
  mixed Stage A / Stage C matrix.
- **The stability rule was pathological.** It flagged a parameter indeterminate
  when fold spread exceeded distance-from-shipped -- so a parameter correctly
  sitting at the shipped value was *always* indeterminate, and a large mover
  was easier to call stable. Replaced with graph-level exposure-weighted RMS
  and a bootstrap interval; per-parameter values demoted to diagnostics.
- **The round-level observation could not produce the player-level outputs it
  promised.** A team differential cannot reconstruct per-player scores. A second
  player-level extraction product is now specified, with a test that player
  vectors sum to the team row -- this is also what makes the kill/death
  player-level read possible at all.
- **The fallback parameter had no definition in the structured families.** It
  has no state, so no `dP`, no `margin`, no basis position. Now an explicit rule
  per family, plus a sensitivity that drops the 497 affected rounds.
- **`r > 0.99` does not imply no difference was possible.** Replaced with a
  two-part practical-equivalence bound tied to this study's own resolution.
- **The success test had uncontrolled multiplicity.** G5's null is now a single
  joint held-out comparison rather than twelve CI checks, and a null there
  *lowers the priority* of the deferred splits rather than retiring them.
  Primary comparisons are now predeclared (see "The predeclared primary
  comparisons"): **G2 and G3 co-primary for Family A at 97.5% each**, G5 alone
  for Family B at 95%, everything else exploratory. Per the reviewer's
  direction both Family A candidates are kept rather than one being chosen,
  with the alpha split paid for openly and a direct G2-vs-G3 comparison
  reported as a finding in its own right.
- **T1's stated restriction contradicted the stage order.** G3 and G4 are no
  longer fitted against T1 at all.
- Smaller: `stage_a_exact` added as the mandatory nested comparator, since
  rounded Stage A can never be nested exactly; G1a is fold-dependent, not
  "fixed"; `dP` relabelled an observational contrast rather than a causal value;
  and the G2 nesting test was stated backwards.

**Already fixed by the decision review**, so the review's version is
superseded: G5's `1 + a_c*...` form with free base columns (which made the
displayed tilt a ratio `phi_c/theta_c`, unstable exactly where econ is
interesting) is now `w_c + a_c*margin + t_c*total`, where the fitted
coefficients *are* `(w, a, t)`; and `margin_hat`/`total_hat` standardization is
already pinned to the 25 states unweighted. The review's underlying warning
survives in a different form and is now addressed: `a_c` is meaningless without
`w_c`, so the report presents effective price *surfaces* and never tilt
coefficients as multipliers.

**Partly wrong.** The review stated G1's non-negativity is not guaranteed. For
G1b, G2 and G5 that is correct and is now handled by a deployability gate. For
**G1a it was checkable and was checked**: estimating `dP` inside each of the 5
training folds gives **0 of 125 non-positive values, minimum +0.00305 at
`5v1`**. That is an empirical fact with a thin margin rather than a guarantee,
so it ships as a runtime assertion.

**Declined, with a reason.** The review recommended excluding the 497
fallback-affected rounds from the primary analysis. Declined: excluding them
changes the eligible match set, which under the fold-stability fix above means
Stage A and Stage C could no longer share a yardstick matrix -- the exact
failure the fingerprint check exists to prevent. Pinning the fallback at its
shipped value keeps the round set identical and costs nothing, and the
exclusion runs as the sensitivity instead.

## Revision note (decision review)

A full adversarial read of the finished spec surfaced five decisions that were
either underspecified or internally contradictory. All five are now resolved,
and one of them changed the model.

**G5 was self-contradictory and is now 9 coefficients, not 6.** It wrote the
tilt as `b_k * (1 + ...)`, pinning each component's outer weight at the shipped
1.0, while simultaneously claiming to "nest Stage A exactly" -- which is false,
since Stage A *fitted* those weights. The form is now
`b_k * (w_c + a_c*margin + t_c*total)`, so zero tilts reproduce Stage A's fitted
model and zero tilts with `w = 1` reproduce the shipped scorer.

**G5 is now symmetric in kill and death, at 18 coefficients.** Raised by the
reviewer on design grounds: `death_impact` is half of Impact and should carry
as many parameters as `kill_impact`. Measuring it first was worthwhile and did
not overturn it. In the team-differential unit the two halves correlate at
**+0.937 / +0.940 / +0.957** (econ / time / swing), because one kill enters the
differential through both halves with the same sign -- so a symmetric fit costs
nine extra coefficients for **0.36 extra effective directions** (rank 2.94 ->
3.30) at 21x Stage A's VIF. That is an identification limit of the *yardstick*,
not a defect in the principle: at player level, kills and deaths are plainly
different events. So the model is symmetric as asked, the team-level kill/death
contrast is **pre-registered as weakly identified** and reported with intervals
rather than as a finding, and the split is *also* read on the player-level
Stage 0 block, which the spec already recomputes per candidate and where the
two halves genuinely separate.

**The verdict is now three verdicts.** The checklist said the stage "has NOT
helped if ANY of the following hold" over seven items answering different
questions -- so a genuine held-out improvement would still have been written up
as a failure because econ's coefficient stayed negative. Split to mirror the
ladder: **A prediction** (items 1, 2, 6), **B collinearity** (3, 4, 5),
**C structure** (7), reported separately and never merged.

**The control ladder's floor is pinned at two columns.** Rung 4 was "terminal
man-advantage state and total kill count" with no encoding named; a rich
encoding could reconstruct the round and make the rung 4 -> 5 headline null for
the wrong reason. It is now exactly two columns -- final alive differential and
total kills -- declared before the fact.

**The deferral trigger keeps its number and gains its meaning.** ~4,000 matches
re-opens the fully free per-component fit **for a look, not a verdict**: that is
where its coefficients become readable, while ~12,000 is where the improvement
could be shown to exclude zero. Stated so the re-open is not mistaken for a
resolution. G6 moved into the same deferred bucket, since G5 now covers
kill/death symmetry at tilt resolution and G6 is the same question at a
resolution the data cannot support.

Seven smaller corrections also applied: the Stage A nesting test needs the
rounding tolerance (Stage A's features are rounded, G5's base columns are not);
`margin`/`total` are standardized over the 25 states unweighted so the
transform carries no data dependence; the per-target parameter counts omitted
the controls (T2 is 32, WPA 31, not 27); failure criterion 2 referenced fitted
candidates at a stage that runs before any fitting; G2's design matrix changes
per fold because its basis is built on the in-fold `dP` table; non-negativity
did not cover G5's effective per-component prices; and the `dP` pass cost is now
measured at 1.5 seconds rather than asserted.

## Revision note (after review discussion)

The reviewer asked whether the four sub-scores should each pull from their own
price list rather than sharing one. Measuring it changed the spec's centre of
gravity, so the candidate ladder was restructured into two families.

**The question had a stronger basis than the draft gave it.** Measured across
600 matches: the `econ` factor tracks the man-advantage margin at **-0.981**,
`swing` tracks the same axis at **+0.946**, and `time` is indifferent to the
margin (+0.045) and tracks total players remaining at **-0.956** instead. So
the three components already vary with state in different, near-perpendicular
directions -- `econ` against `time` profile correlation is **0.078**. Forcing
them through one shared curve is a real constraint. The draft had scoped the
unconstrained version as "diagnostic only" and that was underselling it.

**It also supplies a mechanism for an unexplained finding.** Stage A drove
`econ` to a weight of exactly 0 on both T1 and T2 and called it a
multicollinearity artifact. A single global weight cannot express "economy
matters at 3v3, not at 5v1"; if that is the truth, zero is the correct average
and the artifact reading is incomplete. Family B is built to test this.

**But the fully free version is not affordable yet.** The 78-column design was
built and measured: condition number 3,569 against the shared design's 146,
median VIF 21.7 against 5.9, and -- the decisive number -- **effective rank
19.1 against 15.3. Fifty-two extra parameters buy 3.8 extra directions.** It is
not degenerate (same-edge cross-component correlations run 0.824-0.970, none
above 0.99), just expensive: ~4,300 matches for coefficients as precise as
today's, ~12,000 to detect a difference, against 1,151 today.

**Resolution, per the reviewer's direction.** A new first-class candidate, G5
`component_tilt`: each component keeps the shared curve but may tilt it along
the margin and total-alive axes. (*Superseded by the decision review above,
which found this form pinned the outer weights and so did not nest Stage A
after all, and which made the candidate symmetric in kill and death: G5 is now
18 coefficients, not 6.*) The fully free 78-parameter fit becomes a **deferred
stretch goal** with an explicit re-open trigger at ~4,000 matches -- realistically
a change of data source, i.e. the Riot API access CLAUDE.md already records as
the project's direction. The extraction builds the per-component columns
either way, so the deferral costs nothing to reverse.

**A correction that came out of the same measurement.** The conditioning
figures in the draft used a proxy with every per-kill multiplicand set to 1.
With the real multiplicands the shared design is about twice as well
conditioned -- condition number 146 not 279, median VIF 5.9 not 13.1, effective
rank 15.3 not 13.6. The draft's claim that the proxy "does not change the path
structure" was right about the structure and wrong about the severity. The
corrected figures now govern, and both are shown.

## Revision note (self-review)

Written, then re-read adversarially against the three failure modes the brief
named: hand-waved rare-edge estimation, leakage the inherited nested CV does
not actually close, and claims with no number behind them. Ten things changed;
the first four came out of the second pass over the finished draft.

**The proposed smooth basis was worse than the thing it was meant to improve
on, and the draft asserted it without checking.** The draft's G2 parametrized
`log b` on a polynomial in *(total alive, advantage margin)*. Fitted to the
shipped table, exposure-weighted: 7 terms reach R^2 0.903 (max residual 40.7),
8 terms on the log scale reach 0.9746 -- against **0.9704 from a 2-parameter
affine function of the measured swing curve**. A basis that needs eight
parameters to match what two do is not a smoothing, it is a worse
parametrization with more ways to overfit. G2 was rebuilt as a nested
`dP`-anchored family (5 terms, R^2 0.9863, max residual 10.0) that contains G1a
and G1b as special cases.

**A sign error in a cited number.** The draft said `econ_impact`'s WPA
coefficient was negative in 3 folds and positive in 2. It is the other way
round -- 3 positive, 2 negative -- which matters because the criterion built on
it excludes WPA for exactly that reason.

**Two leaks the first pass left open.** Stage C must reuse the parent
project's fold assignment (same `assign_folds` seed), or the shared
targets x yardsticks matrix compares Stage A and Stage C rows held out on
different matches. And the Stage 0 descriptive recompute under each candidate
graph must use each match's own out-of-fold candidate; an all-data fit there
would be optimistic in a block presented next to held-out numbers.

**Parameter counts were loose.** 26 leverage columns plus damage is 27
features; the unconstrained per-component matrix is 78 leverage columns, not
75; the kill/death split is 52. Stated once, precisely. (Candidate letters were
reassigned in the later pass above -- G5 is now `component_tilt`.)

The remaining six came out of the research pass, before the draft was
finished.

**The rare-edge premise was wrong, and the first draft repeated it.** Measured
on this DB: the *least*-crossed parameter is crossed 1,446 times across 1,441
distinct rounds. No parameter is data-starved. The real estimation problem is
that the columns are structurally collinear -- condition number 279, effective
rank 13.6, ten near-null directions -- so the design section was rewritten
around conditioning rather than sparsity, and the regularization proposals were
re-argued from the measured eigen-spectrum instead of from a sparsity worry
that does not exist.

**"50 edge weights" was wrong too.** The DiGraph's 50 edges are exactly 25
killer-perspective parameters duplicated by side, with zero symmetry
violations. Fitting 50 would double the parameter count for nothing and would
let the fit emit a side-dependent metric, which is a bug and not a finding.
Everything downstream is now specified on 25 (+1 fallback).

**A headroom bound was missing.** The first draft designed the estimator
without ever asking how much the answer could possibly move. It can be
measured before writing any code, and it was: swapping the shipped graph for
the data's own round-win swing curve leaves the round-level Impact
differential at r = 0.998 with zero sign flips. That number now leads the spec
and became a sequencing gate (Stage C0), because it changes what an honest
project here even looks like.

**The motivating hypothesis needed correcting, not restating.** Refitting the
shared multiplicand cannot break the shared-multiplicand structure -- and
`damage`, which contains no edge weight at all, already correlates 0.73-0.90
with the three components. So the graph cannot be the whole cause of the
collinearity, and the only candidate that actually tests the structure is the
unconstrained edge x component matrix. That candidate was added and the
hypothesis section rewritten.

**A leak the inherited protocol does not close.** The empirical swing table is
estimated from *round outcomes*; used as a candidate graph or as a shrinkage
prior it must be estimated inside each training fold, exactly as Stage B does
for `V(state)`. The first draft treated it as a fixed constant. Also added: the
T2 control ladder needs a new rung, because 26 leverage columns reconstruct the
round's terminal state far more completely than 4 components did, so "beyond
who won the round" is no longer the right control.

**A baseline unfairness.** Stage C's features are exact (pre-rounding) while
the inherited `current_impact` row reads a rounded `impact_diff`. Comparing
them would hand the candidates an arithmetic advantage. The baseline is now
required to run through the identical unrounded pipeline, with both figures
reported so the rounding gap is visible.

Also corrected: the brief's example of a monotonicity violation (`4v4`=170
against `2v2`=200) is not one -- audited, the shipped table has zero violations
of the only coherent ordering, so monotonicity became a reported diagnostic
rather than an imposed constraint. And the rescaling/VIF diagnostics were
labelled as reporting-only, so neither is mistaken for a selection surface.

## Purpose

The just-finished Impact-vs-winning project fit the **outer** weights --
`FACTOR_WEIGHTS` and a damage multiplier -- on top of four pre-computed
components, and explicitly deferred the components' own internal curves as
"Stage C". This is that stage, scoped to one curve: `_KILL_ORDER_GRAPH` in
`app/scoring/impact.py`.

The question, stated the way it was asked: those weights were hand-picked and
never fit against data. Can they be fit instead, and would it plausibly help?

The second half of that question turns out to be answerable, in part, before
building anything -- see "The headroom is small, and it is measurable up
front". This spec is designed so the cheap decisive measurement runs first and
the expensive machinery only runs after it.

## What this spec covers

| | Deliverable | Spec |
|---|---|---|
| C0 | Descriptive: how much can the graph move Impact at all | this one |
| C1 | Per-fold refit of the 25 parameters, candidate ladder | this one |
| C2 | Family B: does one shared curve suffice (per-component tilt) | this one |
| -- | Fully free per-component curves (78 params) | **deferred, this one** |
| -- | Adopting any refit graph | a separate, deliberate act |
| -- | Other inner curves (time, econ-swing, trade window, tiers) | later specs |

Everything in the parent spec's "evaluation contract" is **inherited verbatim**
and is not restated except where Stage C changes or adds to it. Parent spec:
`docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md`.

## Constraints and premises

### The graph is 25 parameters, not 50

`_KILL_ORDER_GRAPH` (`impact.py:45-99`) has 50 weighted edges over the
man-advantage lattice. `_kill_order_bonus` (`impact.py:137`) reads the edge a
kill crossed, decrementing the *victim's* side. Re-indexed from the killer's
point of view -- `own` = killer's team alive before the kill, `opp` = victim's
team alive before the kill -- the 50 edges collapse to a 5x5 table, and the two
copies of each parameter agree exactly. **Audited: zero side-symmetry
violations.**

```
hand-tuned  K[own][opp]       own\opp     1     2     3     4     5
                                    1   250   190   120    70    60
                                    2   130   200   170   130    70
                                    3    70   140   180   160   120
                                    4    50    80   130   170   140
                                    5    40    50    90   130   150
```

**Consequence, and it is not optional: the fit estimates 25 parameters and
mirrors them back onto 50 edges.** Fitting 50 independently would let the two
copies diverge, which makes Impact depend on which team you happen to be -- a
bug, not a finding. Side symmetry is a structural invariant of the metric,
enforced by construction and asserted in a test.

The 26th parameter is `_kill_order_bonus`'s fallback of `100`, returned for
transitions the graph does not contain. Measured: **543 of 178,242 kill events
(0.30%), in 497 of 23,955 rounds (2.07%)**.

**It needs an explicit rule per family, because it has no state.** A fallback
event has no `(own, opp)`, therefore no `dP`, no `margin`, no `total` and no
place in any structured basis -- so "carry it as a 26th fitted parameter"
silently turns G2 into a six-parameter family and leaves G1 and G3's prior
undefined. The rule:

| family | fallback treatment |
|---|---|
| G1a, G1b, G2, and G3's prior | **pinned at the shipped 100**, carried through the same normalization as the rest of the graph. The structured families model the lattice; the fallback is not on it. |
| G3's fitted values, G4 | **free**, shrunk toward the pinned prior like every other parameter |
| G5 | **free base weights**, with `margin_hat = total_hat = 0` so it receives no tilt |

Pinning rather than excluding keeps the eligible round set identical to Stage
A's, which the shared yardstick matrix depends on. **A sensitivity run drops
the 497 affected rounds entirely** -- if the answer moves, that is a
data-quality finding about the resurrection heuristic, not a graph finding, and
is reported as one.

### Impact is exactly linear in the edge weights

This is the fact the whole design rests on, so it was verified numerically
rather than argued. `impact.py:536-542` builds every scored term as
`kill_order_bonus * <factor>`, and `death_order_bonus` (`:544`) as
`kill_order_bonus * traded_factor`. `damage` comes from ACS and never touches
the graph. Therefore, before `round()`:

```
ImpactDiff(round r) = damage_diff(r) + SUM_k  b_k * x_r[k]

x_r[k] = SUM over kills i in round r that crossed parameter k  of  s_i * c_i

c_i = [ (w_e*econ_i + w_t*time_i + w_s*swing_i)
        + traded_i * (w_e*econ^d_i + w_t*time^d_i + w_s*swing_i) ] / SUM(w)

s_i = +1 if the killer is on team A, -1 if on team B
```

For a cross-team kill both halves of `c_i` are present, `econ^d_i == econ_i`,
and `s_i` is the killer's side -- a kill adds to the killer's team on *both*
the credit and the debit side of the differential, since the victim's negative
`death_impact` enters the other team with the opposite sign. For a **self-kill**
the kill half is zero (`impact.py:535-542`), `traded_i` is 1, `econ^d_i` takes
the separate 0.9/0.85/0.75/0.15 branch (`:547-555`), and `s_i` is *reversed*,
because the loss lands on the killer's own team.

**Feature count, stated once:** 25 lattice parameters + 1 fallback = **26
leverage columns**, plus `damage` = **27 features** (plus an intercept). Where
this spec says "26 free parameters" it means the leverage columns; "27" means
the full design.

Verified on live data: scaling every edge weight by 2 scales `econ_impact`,
`time_impact` and `swing_impact` by a median of exactly 2.000 (spread
1.00-2.65 entirely from each column's independent `round()` on small integers),
and leaves `damage` at exactly 1.000 for every row. Bumping one edge by 1000
and then by 2000 moves the affected rows by a ratio of 1.9985-2.0370 -- 2.0 to
within rounding.

**The state walk itself does not depend on the weights.** Alive counts,
`_check_for_resurrection`, self-kill handling and the trade window are all
weight-independent, so `x_r` is computed once and every candidate graph is
scored on the identical design matrix. Only the coefficients differ.

### The estimator, and how a fit becomes a deployable graph

An earlier draft asserted that "global scale is not identified by the fit,
since `(d, b)` and `(lambda d, lambda b)` are the same model." **That was
wrong about the regression.** Logistic regression pins the scale through its
link; `d` and `q` below are separately identified, and the ridge penalty breaks
the invariance besides. What is scale-free is the *candidate score* fed to a
downstream Platt calibration -- a different object. The consequence of the
error was worse than the error: rescaling `b` alone for reporting changes its
strength **relative to `damage`**, so the printed graph was not the candidate
that had been evaluated.

The estimator and the recovery are therefore written out once, exactly:

```
fit          eta = controls . gamma  +  d * damage_diff  +  SUM_k q_k * x_r[k]

recover      b_k = q_k / d                        (requires d > 0)

candidate    S_r = damage_diff(r) + SUM_k b_k * x_r[k]
```

Three rules follow, and they are not optional:

1. **`d > 0` is required for a candidate to exist.** A fit returning `d <= 0`
   has no deployable graph -- the recovery is undefined or sign-flipped -- and
   is reported as *non-deployable*, never silently rescaled into one.
2. **Held-out calibration and every yardstick run on `S_r`**, the recovered
   candidate, not on the fitted `eta`. The thing evaluated must be the thing
   that could ship.
3. **`d`, `q` and the recovered `b` are all reported.** The exposure-weighted
   rescale to mean 136.6 is applied *only* to a display copy of `b`, alongside
   the un-rescaled recovered values, and never to anything that gets scored.

This is what makes "the fitted graph" a well-defined object at all, and every
later section depends on it.

### Consequence: the observation unit does not change

The brief anticipated that refitting per-edge weights would force a move to
kill-event observations. It does not. Because the aggregation is exactly
linear, kill-event granularity is fully captured by expanding
`RoundObservation` with a 26-vector of signed leverage, and the unit stays
**one differential row per round** -- the same unit, the same team-A
orientation, the same fold assignment, the same targets, the same yardsticks as
the parent project.

The three component columns (`econ_impact`, `time_impact`, `swing_impact`) are
*replaced* by the leverage columns, since under a refit graph they are no
longer the stored values. `damage` stays as its own column. The controls
(`score_diff_before`, `attacking_is_team_a`, `loadout_diff`,
`full_buy_count_diff`, `round_result`) are all graph-free and are unchanged.

A kill-level fit was considered and rejected: the outcomes the project cares
about are round- and match-level, kills within a round are not independent
observations, and the exact linearity means a kill-level formulation would
recover the same estimates through more machinery.

**But a second, player-level extraction product is required, and an earlier
draft promised outputs it could not produce.** One signed team differential
plus a 26-vector cannot reconstruct individual player scores -- yet the Output
section promises Stage 0's per-player correlations and within-player terciles
recomputed under each candidate, and the kill/death split is to be read at
player level (see below). Both need per-player rows.

So the extractor emits **two** products from the same replay:

| product | row | carries |
|---|---|---|
| team | one per round | `damage_diff`, signed 26-vector `x_r[k]`, split by kill / death half |
| player | one per (round, match_player) | `damage`; kill-side 26-vector; death-side 26-vector **after** the traded discount; death-side 26-vector **before** it; `traded_teammate` and `traded_by_teammate` counts |

The player product is what Stage 0 and the kill/death read consume; the team
product is what every fit and yardstick consumes. **A test asserts the two are
consistent**: summing team A's player vectors minus team B's reproduces the
team row's `x_r[k]` exactly, and the per-player kill/death split reproduces the
production scorer's `kill_impact` / `death_impact` split under the shipped
graph. Without that test the two products can drift and nobody would notice.

### The shipped graph is already, to ~3%, the data's own swing curve

The natural data-driven meaning of "how much did this kill swing the round" is
`P(win round | own, opp-1) - P(win round | own, opp)`. That is directly
measurable, and it was measured, replaying all 23,955 non-surrender rounds
through `impact.py`'s own state walk.

**Labelled precisely: `dP` is an observational contrast between two state
values, not the causal value of crossing that state.** Teams that reach `3v2`
differ from teams that reach `3v3` in ways beyond the kill, and nothing here
adjusts for that. It is used as a *prior shape* for a fitted curve and as a
descriptive benchmark -- never as an estimate of a kill's causal effect, and
the report must not describe it as one.

```
dP * 1000                     own\opp     1     2     3     4     5
                                    1   431   328   125    35     9
                                    2   164   328   262   142    60
                                    3    47   190   262   226   137
                                    4    13    84   177   226   197
                                    5     4    33   101   166   197
```

Regressing the hand-tuned table on it, exposure-weighted by crossings:

```
hand  =  50.0  +  478.0 * dP        exposure-weighted R^2 = 0.9704
```

Every residual is within +-17 points on a 40-250 scale; the largest is `1v2`
(hand 190, implied 206.6). Rank correlation between the two tables is 0.991.

So the hand-tuned numbers are not arbitrary. They are, within a few percent, a
flat 50-point per-kill constant plus 478x the data's own round-win swing. **The
report must state this before it states anything else it finds**, because it
sets the prior for everything that follows.

The one genuinely interesting degree of freedom this exposes is that constant:
a flat per-kill term is a *kill-count* term, and "Impact versus plain kill
differential" is the comparison the parent project already ran, where
`current_impact` did not beat `kill_diff` on the first-half yardstick (paired
AUC gap -0.0017, CI [-0.0067, +0.0038]).

### The headroom is small, and it is measurable up front

Before designing any estimator, the obvious question is how much swapping the
graph can move Impact at all. Measured, on 250 matches / 5,259 rounds, with the
pure swing curve above rescaled to the shipped graph's exposure-weighted mean
(136.6):

| | value |
|---|---|
| round-level Impact differential, Pearson r | **0.99816** |
| round-level Impact differential, Spearman | 0.99856 |
| sd(hand) 501.7 vs sd(difference) | 34.5 (6.9% of sd) |
| rounds where the differential changes sign | **0.00%** of 5,259 |
| player-match average Impact, Pearson r | 0.99822 |

That is a large shape change -- `5v1` moves 40 -> 3, `1v1` moves 250 -> 324 --
and the metric barely notices, because per-kill weight differences largely
cancel between the two teams inside a round.

**This is a bound on one alternative graph, not on all of them.** A fitted
graph is chosen to maximize held-out likelihood and could in principle find a
direction the swing curve does not. But combined with the conditioning result
below -- ten of twenty-five directions are barely identified -- the honest
prior is: whatever the fit finds in the well-determined directions will look
much like today's graph, and whatever it finds in the near-null directions will
not survive out of fold.

Caveats stated plainly: 250 matches, not 1,151; and the swing table used here
was estimated on all data, so this figure is descriptive, not a held-out
estimate. Stage C0 re-runs it properly.

### Exposure is not the problem; conditioning is

The brief expected rare edges to be the estimation difficulty. Measured over
all 23,955 non-surrender rounds, they are not. Crossings per parameter, and the
number of distinct rounds crossing it:

| param | crossings | rounds | param | crossings | rounds |
|---|---|---|---|---|---|
| `1v5` | 1,446 | 1,441 | `2v1` | 6,173 | 6,142 |
| `5v1` | 1,788 | 1,788 | `4v2` | 6,571 | 6,527 |
| `2v5` | 2,838 | 2,814 | `2v2` | 6,792 | 6,648 |
| `5v2` | 3,302 | 3,291 | `2v3` | 6,801 | 6,740 |
| `1v4` | 3,391 | 3,377 | `3v2` | 7,638 | 7,572 |
| `4v1` | 4,444 | 4,438 | `3v3` | 8,837 | 8,642 |
| `1v3` | 4,550 | 4,516 | `3v4` | 8,862 | 8,764 |
| `1v1` | 4,561 | 4,509 | `4v3` | 9,424 | 9,328 |
| `1v2` | 4,825 | 4,782 | `5v4` | 12,098 | 11,999 |
| `2v4` | 5,651 | 5,591 | `4v4` | 12,389 | 12,097 |
| `3v5` | 5,934 | 5,861 | `4v5` | 12,455 | 12,286 |
| `3v1` | 6,132 | 6,125 | `5v5` | 24,639 | 23,955 |
| `5v3` | 6,158 | 6,125 | | | |

The rarest parameter has 1,446 crossings. Every parameter is crossed in at
least 6% of rounds. `5v5` exceeds one crossing per round because
`_check_for_resurrection` lets a round re-enter a state it has already left --
3,416 flagged events across 3,113 rounds (13.0%).

What *is* hard is that a round's path down the lattice is highly structured, so
the columns are collinear. Measured two ways -- once with the per-kill
multiplicands set to 1 as a first proxy, and then again with the **real** `c_i`
values, which is the design the tool will actually fit:

| | proxy (`c_i` = 1) | real `c_i` |
|---|---|---|
| rounds x columns | 23,955 x 25 | 12,635 x 26 |
| max pairwise \|correlation\| | 0.725 | 0.756 |
| condition number | 278.7 | **146** |
| effective rank | 13.6 | **15.3** |
| VIF median / max | 13.1 / 32.7 | **5.9 / 14.9** |

**The real design is roughly twice as well conditioned as the proxy suggested.**
An earlier draft asserted the multiplicands "add noise but do not change the
path structure"; the structure claim holds, but the severity claim was wrong
and the corrected figures are the ones that govern. The proxy's eigen-spectrum
still describes the *shape* of the problem -- fifteen directions with
eigenvalue >= 0.75, then a cliff to 0.045 and below for the remaining ten -- and
the near-null directions are *localized contrasts among lopsided states*, the
smallest being `5v2` against `5v3`, with `4v2` against `4v3` and `2v5` against
`2v4`.

So the honest statement of the problem is: **about fifteen linear combinations
of the twenty-five are estimable from round-level data and about ten are not.**
That is what the regularization has to answer to, and it is a different problem
from "some edges are rare".

(The real-`c_i` figures come from 600 matches rather than all 1,151, since a
correlation matrix over 26 columns converges long before that. The tool
recomputes both on the full set and reports them.)

### The three factors run along different state axes

This measurement was not in the brief and changes one of its premises, so it is
recorded here in full. For each of the 25 states, the mean value of each factor
across the kills crossing it (600 matches):

| factor | tracks | corr. with margin `own-opp` | corr. with total alive | range | CV across states |
|---|---|---|---|---|---|
| `econ` | margin, **negatively** | **-0.981** | -0.137 | 0.883-1.122 | 0.045 |
| `time` | total alive, negatively | +0.045 | **-0.956** | 1.003-1.327 | 0.075 |
| `swing` | margin, **positively** | **+0.946** | -0.072 | 1.206-1.387 | 0.031 |
| `traded` | margin, positively | +0.888 | -0.207 | 0.532-1.000 | 0.112 |

Read plainly: the economy factor is largest when you are the underdog and
smallest when you are stomping; the swing factor does the exact opposite; and
the timing factor is indifferent to the margin and cares only how late in the
round the kill lands. `econ` and `swing` are near-mirror images along one axis
(their per-state profiles correlate **-0.935**), and `time` sits on a
perpendicular one -- the `econ`-vs-`time` profile correlation is **0.078**.

**Consequence: forcing all three components through one shared curve is a real
constraint, not a harmless one.** The three dimensions already vary with state
in different directions, and the shipped formula gives them no way to say so
except through a single global weight each.

That matters because of a finding the parent project could not explain.
Stage A drove `econ` to a weight of **exactly 0** on both T1 and T2, with its
partial coefficient negative in all five folds of each, and wrote it up as a
multicollinearity artifact. There is a second explanation that has never been
testable: `FACTOR_WEIGHTS` gives econ *one global number*. If the economy
dimension is informative in even fights and misleading in lopsided ones, the
best single global weight can easily be zero. A state-dependent econ weight
could express "economy matters at 3v3, ignore it at 5v1"; the current formula
cannot. G5 below is built to test exactly that.

Tempering it: the factors' state variation is small in amplitude -- CV 0.031 to
0.112 -- against the price list's own CV of 0.313 across kills. The *shapes*
differ clearly; the *sizes* of those differences are a few percent. This is a
real degree of freedom, not a large one.

Note `traded`, which is not one of the three components but multiplies the
death half of all of them: it carries the **largest** state variation in the
table (CV 0.112) and tracks the margin at +0.888 -- a lone player's kills are
traded back almost always (0.53 at `1v5`), a `5v1` closing kill essentially
never (1.00). So the death side already carries a different effective curve
from the kill side, `b_k * traded(k)`, set by a hand-tuned 10-second rule and
never fitted. That is the direct motivation for G5 being symmetric.

### Kill and death barely separate at team level -- but must still be modelled

`Impact = kill_impact - death_impact`, and `death_impact` is half the metric,
so it should carry as many parameters as the kill half. The evaluation,
however, can barely see the difference, and it is better to know that up front
than to discover it in the results.

Measured on the round-level design (600 matches / 12,635 rounds, real `c_i`),
the kill-half and death-half columns for the same component correlate at:

| component | corr(kill half, death half) |
|---|---|
| `econ` | **+0.937** |
| `time` | **+0.940** |
| `swing` | **+0.957** |

This is arithmetic, not a data quirk. One cross-team kill enters the team-A
-minus-team-B differential through *both* halves with the *same* sign: the
killer's team gains the kill term, and the victim's negative `death_impact`
sits on the other team, so it raises the differential too. What little
separates them is exactly `traded_i` and the `for_death=True` time variant.

The cost of symmetry, measured:

| design | non-damage cols | condition | eff. rank | VIF median |
|---|---|---|---|---|
| Stage A (3 combined components) | 3 | 22 | 1.61 | 4.3 |
| G5 combined (3 base + 6 tilt) | 9 | 445 | 2.94 | 12.8 |
| **G5 symmetric (6 base + 12 tilt)** | 18 | **8,302** | **3.30** | **91.1** |

**Nine extra coefficients buy 0.36 extra effective directions, at 21x Stage A's
VIF.**

**Decision: model it symmetrically anyway, and evaluate it in both places.**
The identification limit belongs to the *yardstick*, not to the principle -- at
player level a 20-kill/20-death game and a 5-kill/5-death game are plainly
different, and the team differential collapses that distinction by
construction. So:

1. **The model is symmetric.** Kill and death halves each get their own outer
   weight and their own two-axis tilt per component: 18 coefficients.
2. **The team-level kill/death contrast is pre-registered as weakly
   identified.** It is reported with its interval and flagged, never presented
   as a finding, under the same convention the parent spec set for
   sign-flipping coefficients. Wide intervals here are the expected result and
   are not written up as a surprise.
3. **The split is also read at player level, and that read covers death impact
   AND trades.** This is the one place in the project where the two halves
   separate, so it is where a kill/death asymmetry has to earn its keep, and a
   read that stopped at aggregate Impact would miss the thing that makes the
   death side interesting.

   Reported per candidate graph, over the player-level product:

   | quantity | why it is here |
   |---|---|
   | per-player `kill_impact` and `death_impact` separately | the two halves of the metric, which the team differential fuses |
   | Stage 0's within-player tercile lift and per-player correlations | already recomputed per candidate; now split by half as well as pooled |
   | **death cost as scored vs death cost with no trade credit** | the difference is the discount `_traded_factor` applied to that player's deaths |
   | `traded_teammate` / `traded_by_teammate` counts | already stored on `impact_scores`; the two directions are different player qualities |

   **Why trades specifically.** `_traded_factor` discounts a death when the
   killer is traded back within 10 seconds -- so a player's death cost depends
   heavily on whether their *team* trades for them, which is a team quality
   being charged to an individual. Measured, it is also the most
   state-dependent factor in the formula (CV 0.112, tracking the margin at
   +0.888) and it is the main reason kill and death do not collapse to the same
   column entirely. At team level that discount is invisible; per player it is
   directly attributable, and it is the quantity a refit graph would move most.

   The player product therefore carries the death leverage **both before and
   after** the traded factor, so the discount is a subtraction rather than a
   re-derivation.

A player-level *fitting* target is not introduced here -- that would be a new
observation unit and a new evaluation contract, and it is out of scope. Only
the existing descriptive block is read.

### Effective sample per parameter, by target

Counts include the nuisance controls each target carries, which an earlier
draft omitted. Family A's free candidates are 26 leverage columns + `damage`;
T2 adds 5 controls and WPA adds 4.

| target | rows | independent units | params | units/param |
|---|---|---|---|---|
| T1 (first half -> match) | 1,114 | 1,114 matches | 27 | **41** |
| T2 (round N -> N+1..N+k) | 22,660 collapsed | 1,151 matches | 32 | 36 |
| WPA | 23,955 | 1,151 matches | 31 | 37 |

G5's largest rung (B3) is 19 features, sitting at 59 / 48 / 50 on the same
three targets (T2 adds 5 controls, WPA adds 4).

**Nominal matches-per-parameter is the weaker of the two constraints and must
not be quoted alone.** B3's 18 leverage columns have effective rank 3.30 and
condition number 8,302. Held-out *predictive* comparison is supportable at that
conditioning; *interpreting six separate price surfaces* is not, unless their
bootstrap stability is demonstrated. The report treats the surfaces as
regularization-dependent diagnostics until the stability criterion below is
met for them.

Stage A fit 4 feature coefficients on the same data -- 1,114 / 4 = 279 matches
per parameter on T1. Stage C fits 27. **T1 in particular cannot support a free
26-parameter fit**, and the report must not present one as if it could: on T1
only the low-dimensional candidates have a defensible ratio (G1a 0 free
parameters, G1b 557 matches each, G2 223 each). This is stated now rather than
discovered in the results.

(T1's n is 1,114, not the 1,129 the parent spec estimated -- 1,114 is what the
completed run reported and is the figure used here.)

### The collinearity hypothesis, corrected

The brief's motivating hypothesis: since `kill_order_bonus` is the shared
multiplicand causing the 0.73-0.90 component correlations, refitting it might
reshape or resolve that collinearity rather than relocate it.

Two things about that need saying before any code is written.

**Refitting `b` cannot break the shared-multiplicand structure.** Under any
graph, `econ_impact_r = SUM_k b_k x^econ_r[k]` and
`time_impact_r = SUM_k b_k x^time_r[k]` still share `b` and still sum over the
same kills. Changing the *values* of `b` changes their correlation only through
how much `b` varies relative to the factors -- a flatter graph differentiates
them slightly, a more extreme one couples them more. Measured, today's graph
has exposure-weighted mean 136.6, sd 42.7, **CV 0.313**.

**And the graph cannot be the whole cause anyway.** `damage` contains no
`kill_order_bonus` at all, and it already correlates **0.842 / 0.895 / 0.733**
with `econ_impact` / `time_impact` / `swing_impact` (parent report,
`diagnostics_T2.correlation_matrix`). Whatever fraction of the collinearity
comes from all four columns being sums over the same kill events is untouchable
by any graph refit. A report claiming a refit "fixed the collinearity" without
accounting for this floor would be claiming something the data does not
support.

The structure that *could* be relaxed is different, and it is worth naming
precisely. Write the full model as a 26x3 matrix `B[k][c]` on the 78 columns
`x^c_r[k]`:

```
ImpactDiff = damage_diff + SUM_{k,c} B[k][c] * x^c_r[k]
```

The shipped formula constrains `B = b (outer) w` -- rank one. Stage A fit `w`
with `b` fixed; Stage C's Family A fits `b` with `w` fixed; and **the rank-one
constraint itself is what forces the components to share a multiplicand.** So
relaxing rank one is the only thing that can genuinely test the hypothesis --
which is what Family B exists for.

Family B relaxes it in two sizes. **G5 relaxes it cheaply**, letting each
component tilt the shared curve along two axes: six parameters, a rank-one-
plus-low-rank-correction structure rather than a free matrix. **The fully
unconstrained `B`** is the complete relaxation, and it is deferred on data
volume -- see "Deferred: fully free per-component curves" for the measured
reason and the re-open trigger. The report therefore answers the
collinearity question at the resolution today's data supports, and says
explicitly that the higher-resolution version is waiting on more matches rather
than pretending G5 settles it.

### Monotonicity: audited, and the shipped table passes

The brief suggested checking whether the hand-tuned numbers satisfy "larger
swings should score at least as much as smaller ones", offering `4v4`=170
against `2v2`=200 as a possible violation. Audited: **it is not one**, and the
table has zero violations of the only coherent ordering.

- Within a fixed number of players remaining, weight is non-decreasing as the
  state gets closer to even. **0 violations** across all comparable pairs.
- Along the diagonal, as the round narrows: 150, 170, 180, 200, 250 -- rising,
  and `4v4`=170 < `2v2`=200 is an instance of that, not a violation of it.
- Every row peaks on the diagonal (`own=2`: 130, **200**, 170, 130, 70).
- Killing from behind consistently outscores extending a lead: `1v2` 190 >
  `2v1` 130, `2v3` 170 > `3v2` 140, `3v4` 160 > `4v3` 130, `4v5` 140 > `5v4`
  130.

The measured swing table satisfies the same ordering, and all 25 of its `dP`
values are positive (minimum +0.0037 at `5v1`), so "a kill never hurts" holds
empirically too.

**Decision: monotonicity is a reported diagnostic, not an imposed constraint.**
A constraint that the prior, the data-driven table and the shipped table all
already satisfy is non-binding where we expect the answer to land, and binds
only where the data disagrees with the prior -- exactly the place we want to
hear from the data rather than silence it. Each fitted candidate reports its
violation count and where.

### Which state walk, and why it matters

`app/services/state_replay.py` is the canonical replay engine for Fight-EV and
the player-page diamonds, and it **disagrees with `impact.py` on
resurrections**: it excludes an ambiguous-lifecycle round entirely, while
`impact.py`'s `_check_for_resurrection` keeps the round and declines to
decrement. 13.0% of rounds contain at least one such event.

Stage C extracts leverage using **`impact.py`'s own walk**, not
`state_replay.py`'s. The point of this stage is to refit the parameters of the
shipped scorer; extracting through a different engine would refit a graph for a
metric that is not the one that ships. This is a deliberate divergence and is
documented at the extractor.

### Inherited without change

Restated only so it is clear they are not being relaxed:

- **Ex-ante components only.** Leverage columns come through
  `build_impact_rows_for_match(..., use_realized_swing=False)`'s code path,
  never a hand-rolled copy. `_realized_econ_swing_factor` reads round N+1 and
  would leak.
- **Surrender placeholder rounds excluded** via `NOT_A_SURRENDER_ROUND`.
- **The tautology trap.** A round's kills near-determine its own outcome;
  anything scored against the round's own result is attribution, labelled as
  such.
- **Half boundaries.** Forward windows never cross round 12/24.
- **Frozen targets.** `PRIMARY_T1`, `PRIMARY_T2` (k=3, gamma=0.7,
  match_weight=1.0) and the WPA target are reused **exactly as defined**, not
  re-tuned. Sensitivity configs stay sensitivity configs, compared only on the
  fixed binary yardsticks.
- **Nested CV**: outer 5-fold by match, inner folds inside each training fold
  select L2 only, weighted log loss as the inner objective, all of a match's
  rounds in one fold, cluster bootstrap by match, standardize on training-fold
  statistics and back-transform, calibrate fitted candidates inside their own
  outer fold.
- **numpy only.** No scipy, no sklearn, no pandas.
- **Baselines mandatory**: current graph, `kill_diff`, `damage`, `acs`.

## What is new, and why the inherited protocol does not already cover it

### The swing table is estimated data and must be cross-fit

`dP(own, opp)` is estimated from **round outcomes**. Used as a candidate graph
(G1) or as a shrinkage prior (G3), it must be estimated **inside each outer
training fold**, and inside each inner fold wherever it participates in
selection. Estimating it once over all matches and then cross-validating would
put test-match round outcomes into the candidate -- the same mistake the parent
spec caught for `V(state)`, in a new place.

The descriptive tables printed in this spec are all-data estimates and are
labelled descriptive. The tool never uses an all-data table for a held-out
number.

### Folds are reused, not re-drawn

Stage A's and Stage C's rows in the shared targets x yardsticks matrix must be
held out on the *same* matches, or the matrix lines up rows that are not
comparable -- worse than not putting them on one page at all.

**Reusing the same seed does not achieve this, and an earlier draft assumed it
did.** `assign_folds` (`impact_eval.py:280`) sorts the unique match ids, draws
`rng.permutation(len(unique))`, and assigns by position. The mapping therefore
depends on the *membership and size* of the match set: add, drop or exclude a
single match and every assignment can move, same seed or not.

Two changes close it:

- **Stable per-match assignment.** Fold membership comes from a deterministic
  hash of the match id, not from a permutation over the collection, so a match
  lands in the same fold regardless of what else is in the set. This is a
  change to shared code and must keep `assign_folds`' existing behaviour
  available, since the parent project's committed results were produced with
  it.
- **Three identity values** are recorded with every run, not one: the
  **dataset fingerprint** (match count plus a SHA-256 of the sorted eligible
  match ids), the **fold-mapping hash** (a SHA-256 over the sorted
  `match_id -> fold` pairs actually used), and the **calculation version**
  (`IMPACT_CALCULATION_VERSION` plus this stage's own schema version). The
  shared matrix **refuses to print Stage A and Stage C rows together unless all
  three match**, and says which one differed.

  The fold-mapping hash is not redundant with the fingerprint, and assuming it
  was is the hole this closes: **the parent project's committed results were
  produced with the old permutation-based `assign_folds`**, so an identical
  match set can still carry an entirely different fold assignment. Same
  fingerprint, different folds, a matrix that looks comparable and is not.

  **Consequence, stated plainly: Stage A must be re-run under `stable_folds` on
  Stage C's snapshot before the two can share a matrix.** That is a re-run of
  existing, tested code, not new work -- but it is not optional, and until it
  happens the report prints the two stages as separate tables.

### The Stage 0 recompute is out-of-fold too

Recomputing Stage 0's descriptive block under each candidate graph is
straightforward only for **G0**, which is fixed. Every other candidate is
fold-dependent -- **including G1a**, whose `dP` table is estimated from round
outcomes and is therefore a different graph in each fold, a point an earlier
draft got wrong by calling it "fixed". So each match must be scored by the
candidate built on the folds that exclude it, exactly as the yardstick matrix
does. An all-data fit here would
put an optimistic block next to held-out numbers on the same page. The block is
reported and never selected on, but "descriptive" is not a licence to be
in-sample.

### Two things that are reporting-only, and are not selection surfaces

- **Two different normalizations exist and must not be confused**, which an
  earlier draft did.

  **Construction normalization** *defines* a candidate and therefore is scored.
  G1a is the clearest case: `dP` values are probabilities in [0, 1] and the
  shipped graph is on a 40-250 scale, so a plug-in candidate is not a candidate
  at all until its scale is fixed. G1a's rule: rescale the in-fold `dP` table so
  its **training-fold** exposure-weighted mean equals the **training-fold**
  exposure-weighted mean of the shipped graph. Both the `dP` table and the
  exposure weights come from training matches only.

  **Display normalization** to mean 136.6 is a transform on a *copy* of a
  recovered graph, for reading and comparing shapes across folds and candidates.
  It is never applied to anything scored -- rescaling `b` without `d` changes
  its strength relative to `damage` and would evaluate a candidate nobody
  proposed. See "The estimator, and how a fit becomes a deployable graph".
- **The VIF, condition number and near-null directions** are computed once over
  all data as diagnostics for the report. Nothing is selected, dropped or
  shrunk on the basis of them; L2 and the shrinkage strength come from inner CV
  like every other hyperparameter.

### The T2 control ladder needs a new rung

The ladder's headline is step 3 -> 4: what the components add beyond knowing
who won the round and what the teams could afford next. With 26 leverage
columns instead of 4 components, step 4 can now reconstruct the round's
terminal man-advantage state and its total kill count almost exactly. That is
not leakage of T2's label -- the label is rounds N+1..N+k -- but it does mean
the increment can no longer be attributed to "Impact's machinery" as opposed to
"detailed knowledge of how the round ended."

So the ladder gains a rung, and the headline moves to the new step:

1. round-N result alone
2. plus score differential, side, start-of-round economy
3. plus damage differential
4. **plus terminal man-advantage state and total kill count** *(new)* --
   **exactly two columns: the final alive differential, and total kills in the
   round.** Pinned here, before the fact, because rung 4 -> 5 is this stage's
   headline and a richer encoding (a slot per end state, say) could reconstruct
   the round well enough to make rung 5 look null for reasons that have nothing
   to do with the price list. Two numbers is a genuine floor -- it knows how the
   round ended and how bloody it was -- without being able to replay it.
5. plus the leverage columns

**Step 4 -> 5 is the Stage C headline**, reported as paired
delta-weighted-log-loss with a match-clustered CI. Step 3 -> 4 is reported too,
because a large jump there is itself the finding that the graph's apparent
contribution was mostly "who was left standing."

The ladder is deliberately built so that a **null at 4 -> 5 is informative
rather than a failure of the tool**: it would say that weighting kills by the
state they crossed adds nothing beyond knowing where the round's state
trajectory ended and how many kills it took to get there. That is the sharpest
form of the question this stage exists to ask, and the report must read a small
4 -> 5 that way rather than as a disappointing number to be explained away.

### The baseline must run through the same arithmetic

`current_impact` in the parent project reads the stored/calculated
`impact_diff`, which carries `round()` error at three places per player-round.
Stage C's candidate scores are exact pre-rounding linear forms. Comparing them
directly hands the candidates an arithmetic edge on close calls -- precisely
the reason the parent plan stopped rebuilding `current_impact` from components
in the first place.

**Rule:** the primary baseline is `current_graph` -- the shipped 25 values fed
through the identical unrounded leverage pipeline. `current_impact` (rounded,
as shipped) is reported alongside, and the gap between the two rows is printed
as its own line so the rounding cost is visible rather than absorbed into a
result.

### Identifiability, scale, and sign

- **Scale.** See "The estimator, and how a fit becomes a deployable graph":
  `d` and `q` are separately identified by the fit, `b_k = q_k / d` is the
  recovered graph, and the exposure-weighted rescale to mean 136.6 is a
  **display transform only**, applied to a copy so that fold-to-fold and
  candidate-to-candidate comparisons are about shape. Nothing is ever scored on
  a rescaled graph, because that would change `b` relative to `damage` and
  evaluate a candidate nobody proposed.
- **The level/shape split *is* identified**, because a constant `b` produces a
  distinct feature (leverage-weighted kill count). It is also close to the
  `kill_diff` baseline, so the report states the fitted constant term next to
  the existing `kill_diff` gap rather than presenting it as new information.
- **Non-negativity is a deployability gate, not a constraint on the fit.** A
  negative price means "this kill hurt your team", which is not meaningful in
  the current design -- the same argument that made `w_i >= 0` the decision in
  Stage A. `fit_logistic` is unconstrained and a 27-parameter simplex grid is
  not feasible the way Stage A's 3-parameter one was, so nothing is clipped
  during fitting. (An earlier draft proposed a "clip at zero and refit the
  rest" pass; it is dropped, because it needs an active-set procedure that was
  never specified and would have been invented at implementation time.)

  Instead: **the unconstrained ridge fit is always the estimator and is always
  reported**, and every candidate is checked for deployability afterwards. A
  candidate is **non-deployable** if `d <= 0`, or if its effective price is
  negative at any state carrying non-trivial exposure -- for G5, at any
  (component, side, state). Non-deployable candidates are reported in full,
  with the offending states and magnitudes listed, and **cannot satisfy the
  success criterion**, since a graph that cannot ship has not improved the
  metric.

  Which candidates are safe, stated accurately rather than by assertion:
  **G1a's per-fold `dP` was measured non-negative in all 5 training folds --
  0 of 125 values non-positive, minimum +0.00305 at `5v1`.** That is an
  empirical fact on this data with a thin margin, not a guarantee, so it ships
  as a runtime assertion rather than a claim. **G1b, G2 and G5 carry no such
  guarantee at all** -- an unconstrained affine or polynomial function of `dP`,
  or a tilted price, can go negative anywhere -- and are checked like everything
  else.
- **Stability is measured at graph level, not per parameter.** An earlier
  draft flagged a parameter indeterminate when its fold-to-fold spread exceeded
  its distance from the shipped value. That rule is pathological in three ways:
  a parameter the data says should stay exactly where it is has distance ~0 and
  is therefore *always* flagged indeterminate; a parameter that moves far is
  *easier* to call stable; and 5-fold training sets overlap in 3/5 of their
  matches, so fold spread badly understates true variability and is not
  independent evidence of anything.

  Replaced with: **exposure-weighted RMS deviation of the whole recovered graph
  from the shipped one, and exposure-weighted RMS fold-to-fold deviation of the
  graph from its own mean, each with a match-clustered bootstrap interval** --
  the refitting bootstrap the parent spec already specifies. A candidate is
  stable when its bootstrap interval on the second quantity is small relative
  to the first. Per-parameter fold values, crossing counts, VIFs and near-null
  projections stay in the report as **diagnostics**, and no verdict is derived
  from them individually.

## The candidate ladder

Every candidate is a `Candidate` in the existing sense: a linear form over
named `RoundObservation` features. All are fitted (where they are fitted) per
outer fold on training matches only, scored on that fold's held-out matches,
and enter the same targets x yardsticks matrix as the parent project's
candidates, so Stage C and Stage A results are read on one page.

The ladder has two families, because there are two separable questions.
**Family A** asks whether the shared curve has the right *shape*. **Family B**
asks whether one shared curve is enough at all.

**Family A -- is the shared curve's shape right?**

| | name | free coefficients | fit against |
|---|---|---|---|
| G0 | `current_graph` | 0 | -- (baseline, shipped values) |
| G1a | `swing_plugin` | 0 | -- (in-fold `dP`, construction-normalized) |
| G1b | `swing_affine` | 2 | alpha + beta*`dP`, in-fold |
| G2 | `swing_basis` | 5 | frozen target |
| G3 | `pooled` | 26 shrunk | frozen target, prior = in-fold G1a |
| G4 | `free` | 26 | frozen target, ridge only |

**Family B -- does one shared curve suffice?**

Family B is a **nested ladder**, not a single candidate. An earlier draft ran
the full symmetric model against `stage_a_exact` in one comparison -- but that
changes three things at once (component-by-state tilts, a kill/death split of
the base weights, and a kill/death split of the tilts), so a win could not be
attributed to any of them. It could have come entirely from constant kill/death
weights, which is not the hypothesis Family B exists to test.

| | name | free coefficients | what it adds |
|---|---|---|---|
| B0 | `stage_a_exact` | 3 | -- (nested comparator) |
| B1 | `kd_split_base` | 6 | constant kill/death asymmetry, no tilts |
| B2 | `component_tilt` | 9 (3 base + 6 tilts) | component-by-state curves |
| B3 | `component_tilt_symmetric` | 18 (6 base + 12 tilts) | both together |
| -- | `split_kill_death` | 52 (26 x 2) | **deferred, see below** |
| -- | `unconstrained_B` | 78 (26 x 3) | **deferred, see below** |

Each rung answers one question:

| comparison | asks |
|---|---|
| **B2 vs B0** | do the components want different state curves? |
| B1 vs B0 | is there a constant kill/death asymmetry? |
| B3 vs B1 | do tilts add anything once kill/death is split? |
| B3 vs B2 | does splitting kill/death add anything to component tilts? |

**B2 vs B0 is Family B's primary test**, because it isolates the hypothesis the
measured factor profiles motivate. **B3 is still built and reported, and its
player-level read -- death impact and trades -- is a required output, not an
optional one** -- the
symmetric parameterisation is a deliberate design choice, `death_impact` being
half of Impact -- but the comparisons involving it are secondary, which is
consistent with what this spec already pre-registered: at team level the
kill/death contrast is weakly identified (columns correlate 0.937-0.957, VIF
median 91.1) and is reported with intervals rather than as a finding. Its
informative read is the player-level block.

**G1** is the plug-in: no target fitting at all, just the state's own round-win
swing. It has the strongest prior claim to being "the right answer" and the
weakest claim to novelty, and it costs a counting pass. G1a and G1b are worth
separating because the intercept does real work: pure `beta*dP` with no
constant reproduces the shipped table at only R^2 0.713 (max residual 53.7),
against 0.9704 once a constant is allowed.

**G2** is the smooth family, and it is anchored on `dP` rather than on the
lattice coordinates:

```
b(own, opp) = alpha + beta*dP + gamma*dP^2 + delta*sign(m) + eps*1[m == 0]
                                                    where m = own - opp
```

Five parameters, **nested**: G1a is `beta` alone with `beta` pinned by the
exposure-mean rescale rather than fitted, and G1b is `alpha + beta`. The choice
is empirical and was measured against the alternative rather than asserted.
Fitted to the shipped table, exposure-weighted by crossings:

| family | params | R^2 | max residual |
|---|---|---|---|
| polynomial in `(T, m)`, raw scale | 7 | 0.9029 | 40.7 |
| polynomial in `(T, m)` + `T*abs(m)` | 8 | 0.9491 | 27.5 |
| same, fitted on the log scale | 8 | 0.9746 | 16.6 |
| `alpha + beta*dP` | 2 | 0.9704 | 16.6 |
| **`dP` family above** | **5** | **0.9863** | **10.0** |
| `dP` family + `T` | 6 | 0.9875 | 10.0 |

**G2's design matrix therefore changes per fold**, because its basis is built
on that fold's own `dP` table. That is correct and required by the cross-fitting
rule above, but it must be built inside the fold loop -- computing `dP` once and
reusing it is the easy mistake here.

A lattice polynomial needs eight parameters to match what two `dP` parameters
do, so it is not a smoothing -- it is a worse parametrization with more ways to
overfit. Adding `T` on top of the five buys 0.0012 of R^2 and nothing on the
residual, which is where the family stops. The basis is fixed here, up front,
and is **not** selected on data.

**G3** is partial pooling: all 26 free, ridge-shrunk toward the in-fold G1a
graph rather than toward zero, with the shrinkage strength selected by inner CV
like any other L2. This is the principled middle: parameters with enough
independent variation move away from the prior, the ten near-null directions
stay at it.

**Shrinking toward a prior has to be done in the `q`/`d` parameterization, and
the obvious way is wrong.** The natural implementation -- put `X @ b_prior` in
as an offset and fit `delta`, so `q = b_prior + delta` -- shrinks **`q`** toward
the prior. But the deployable graph is `b = q / d`, so that drives `b` toward
`b_prior / d`, not toward `b_prior`. Checked numerically rather than argued: with
`d_true = 3` and a prior of 0.6 at every parameter, the offset formulation at
strong shrinkage returns `b = [73.7, 72.2, 73.6, 72.9]` -- converging on
`b_prior / d = 73.2` -- while the correct formulation returns values heading for
0.6. A graph 100x off, produced silently.

**The parameterization G3 actually uses** folds the prior into the damage column
instead, which needs no offset and no new numeric primitive:

```
    q = d * b_prior + delta

    eta = controls . gamma
          + d * ( damage_diff + SUM_k b_prior_k * x_r[k] )     <- one composite column
          + SUM_k delta_k * x_r[k]

    b = q / d = b_prior + delta / d
```

`delta = 0` recovers `b = b_prior` exactly, which is the nesting the candidate
needs. The penalty falls on `delta = q - d * b_prior`, so in graph units it is
`d^2 * ||b - b_prior||^2` -- shrinkage toward the prior with a strength that
scales with `d^2`. **That is stated rather than hidden**: the inner CV selects
the penalty anyway, so it adapts, but the reported shrinkage strength is not
directly comparable across folds with different `d`, and the report prints `d`
beside it. The exactly-`||b - b_prior||^2` penalty would require optimising
`q/d`, which is non-convex, and is not worth it for that.

**G4** is the naive fit. It is included **so the overfitting claim is measured
rather than asserted.** If G4 matches G3 out of fold, the pooling was
unnecessary and the report says so.

**G5, `component_tilt`, is the first-class member of Family B** and the
candidate most likely to move anything, because it is the only one addressing a
constraint the data shows to be **binding** (see "The three factors run along
different state axes"). Stated carefully: the measured factor profiles show the
three components *do* vary with state in different directions, so the shared
curve genuinely constrains them. They do **not** show that relaxing the
constraint improves prediction -- only a held-out comparison can, and that is
what G5 is for. G5 is a well-motivated hypothesis, not an established
correction, and the report must not slide between the two. Each component, **on each side of the ledger**, keeps
the shared curve but is allowed its own weight and its own tilt along the two
axes that turned out to matter:

```
price_{c,side}(own, opp) = b(own, opp)
                           * ( w_{c,side}
                               + a_{c,side} * margin_hat
                               + t_{c,side} * total_hat )

    c    in {econ, time, swing}
    side in {kill, death}
    margin_hat = standardized (own - opp)
    total_hat  = standardized (own + opp)
```

**18 free coefficients** -- three per (component, side) -- plus `damage`, for
**19 features** and an intercept. The columns are, for each of the six
(component, side) pairs, `SUM_k b_k * h^{c,side}_r[k]` and the same weighted by
`margin_hat_k` and by `total_hat_k`, where `h^{c,kill}` is the kill half of
that component's per-kill term and `h^{c,death}` is `traded_i` times its death
half.

`margin_hat` and `total_hat` are standardized **over the 25 lattice states,
unweighted** -- a fixed constant, not a data-dependent transform, so no
per-fold recomputation and no leakage. The fallback parameter has no state and
takes `margin_hat = total_hat = 0`.

Four properties earn it first-class status:

1. **It nests `stage_a_exact` exactly, and approximates published Stage A.**
   All tilts zero with `w_{c,kill} = w_{c,death}` reproduces `stage_a_exact` --
   the refit of Stage A's four-feature model on the exact pre-rounding columns;
   additionally setting `w = 1` reproduces the shipped scorer. It only
   *approximates* Stage A **as published**, which was fitted on the `round()`ed
   component columns, and no amount of care makes that comparison exact. That is
   why `stage_a_exact` exists.
2. **It stays convex.** `b` is held fixed -- at the shipped curve for the
   primary run, at the best Family A curve as a sensitivity -- so the
   coefficients enter linearly and the fit is the same weighted ridge IRLS as
   everything else. Fitting `b` and the tilts jointly would be bilinear again,
   and is rejected for the same reasons the joint `b`/`w` fit is.
3. **The data can support the component split predictively.** 19 features
   against 1,114 T1 matches is 59 matches per parameter -- thinner than Stage
   A's 279, far healthier than anything Family A's larger members can claim.
   That justifies a held-out *comparison*; it does **not** by itself justify
   reading six price surfaces off the fit, given effective rank 3.30. The
   surfaces are diagnostics until they pass the stability criterion.
4. **It answers the econ question in a form that can be read.** Because the
   base weight `w_c` is fitted rather than pinned at 1, the fitted coefficients
   *are* `(w, a, t)` directly -- there is no ratio to back out and no division
   by a near-zero base, which is the failure mode the pinned form would have
   had precisely where it matters most.

   **But `a_c` on its own is still not interpretable as "how much tilt",**
   because its meaning is relative to `w_c`, and Stage A's headline finding is
   that `w_econ` collapses to ~0. "Econ wants more weight in underdog states"
   is a statement about `a_c / w_c`, which explodes exactly when econ is most
   interesting. **So the report never presents a tilt coefficient as a
   multiplier.** It prints the *effective price surface* -- the resulting 5x5
   table per (component, side) -- next to the shared curve, and reads the answer
   off that. Coefficients appear in the diagnostics; surfaces carry the
   findings.

**The kill/death half of it is a different story, and is pre-registered as
such.** Per "Kill and death barely separate at team level", those columns
correlate at 0.937-0.957 and the symmetric design runs at VIF median 91.1. The
`w_{c,kill}` vs `w_{c,death}` contrasts are therefore reported with intervals
and flagged weakly identified; the informative read on that split is the
player-level Stage 0 block, not the team-level yardsticks. G5 is run
symmetrically because `death_impact` is half of Impact and deserves equal
parameterisation -- not because the team-differential yardstick can resolve it.

No identifiability constraint is needed among the tilts: with `b` fixed and the
six (component, side) base columns kept separate, a tilt common to all of them
is *not* absorbable into the base columns, so all 18 are free.

**G5's coefficients need the same `q`/`d` recovery as everything else**, and an
earlier draft said otherwise. `damage` carries its own fitted coefficient `d`
here too, so the 18 fitted numbers are regression coefficients, not deployable
weights. Every one is divided by `d`:

```
    w_{c,side} = q^w_{c,side} / d
    a_{c,side} = q^a_{c,side} / d
    t_{c,side} = q^t_{c,side} / d          requires d > 0
```

**The effective price surfaces, the non-negativity check and every reported
number use the recovered values, never the raw `q`.** A test asserts that the
recovered 18 coefficients, applied to the leverage columns and added to
`damage_diff`, reproduce the fitted score up to a single positive global scale
-- which is exactly what the downstream Platt calibration absorbs.

## Deferred: full-resolution curve splits

Two candidates are **deferred on data volume, not on interest.** Both are the
full-resolution version of something G5 tests at tilt resolution.

- **`unconstrained_B`** (78 coefficients): each of the three components gets its
  own unconstrained 26-value curve.
- **`split_kill_death`** (52 coefficients): the kill side and death side each
  get their own unconstrained 26-value curve. Deferred for the same reason and
  by the same measurement -- the two sides correlate at 0.937-0.957 at team
  level, so a full-resolution split is even less identified than the
  18-coefficient symmetric G5, which already runs at VIF median 91.1.

The sizing analysis below is for `unconstrained_B`; `split_kill_death` is
smaller but faces the harder correlation, and its threshold is not separately
estimated here. Re-derive both at re-open rather than assuming either ratio
holds.

The design was built and measured (600 matches / 12,635 rounds, real per-kill
multiplicands):

| | shared curve (26 cols) | per-component (78 cols) |
|---|---|---|
| condition number | 146 | **3,569** |
| effective rank | 15.3 | **19.1** |
| VIF median / 90th pct | 5.9 / 12.5 | **21.7 / 47.0** |

**Fifty-two extra parameters buy 3.8 extra independent directions.** That is
the cost of the idea in one number, and it is why this is deferred rather than
run.

It is not degenerate, though, which is the reason it is deferred rather than
dropped: the three copies of each edge correlate at 0.824-0.970, median 0.921,
and **none exceed 0.99**. The curves are genuinely distinguishable; there is
simply not enough data yet to distinguish them.

**How much data.** Three defensible bars:

| bar | matches | basis |
|---|---|---|
| coefficients as precise as the shared curve's are today | **~4,300** | median VIF inflation 3.7x on today's 1,151 |
| enough to *detect* a difference | **~12,000** | see below |
| Stage A's own 279-matches-per-parameter discipline | ~22,000 | 79 features |

The middle figure scales from the parent report: at 1,151 matches the T2
weighted-log-loss CI half-width is ~0.0008, and the *entire* components-beyond-
controls effect is 0.00348. A refinement of a refinement is plausibly 10-30% of
that, so ~0.0005, needing an interval roughly 3x tighter and therefore ~10x the
matches. **That assumes an effect size nobody has measured yet** -- treat it as
an order of magnitude, not a target.

Against 1,151 matches today, that is 4x to 20x the database. The tracker.gg
crawl walks an 11-player roster at 20 matches each per refresh, which is not a
path to those numbers in any reasonable time. **The realistic trigger is a
change of data source** -- Riot API production access, which CLAUDE.md already
records as the project's intended direction, would make the first bar
plausible and the second conceivable.

**Re-open when `matches` in the local DB exceeds ~4,000** -- and be clear about
what that buys. **4,000 re-opens it for a look, not a verdict.** At that point
the 78 coefficients are precise enough to read and compare as three curves, and
seeing whether they visibly diverge is what tells you whether waiting for
~12,000 is worth it. It is *not* the point at which the improvement can be
shown to exclude zero. A report that re-opens at 4,000 and then declares a
result would be over-reading its own threshold.

Re-run this spec's conditioning measurement at re-open rather than assuming the
ratios hold; the crawl's composition may change what the design looks like.

Two things make the deferral cheap to reverse. The extraction already builds
the per-component and per-side columns -- G5 needs both splits anyway -- so
nothing has to be rewritten, only fitted. And G5's fitted coefficients are a
direct preview: if the tilts come back indistinguishable from zero on today's
data, both deferred candidates have correspondingly less to find, and the
deferral gets cheaper still.

## What gets refit, and what is held fixed

**Decision: refit the graph alone, with the outer weights held at the shipped
`FACTOR_WEIGHTS = {econ: 1.0, time: 1.0, swing: 1.0}` and the damage multiplier
at 1.0.** Three reasons, in order of weight:

1. **It is the only variant that answers the question asked.** "Would fitting
   the graph rather than the outer weights have helped?" is a marginal, and a
   joint fit confounds the two: an improvement could not be attributed.
2. **It is convex.** With `w` fixed, `ImpactDiff` is linear in `b`, so the fit
   is the same weighted ridge IRLS already implemented and already covered by
   tests. A joint fit is bilinear in `(b, w)` -- non-convex,
   initialization-dependent, and identified only up to the `b`-versus-`w` scale
   split, so per-fold coefficients would not be comparable without an arbitrary
   pinning convention that would itself need defending.
3. **Stage A produced three different outer weightings**, one per target: T1
   chose econ 0 / time 2.4 / swing 0.6 with damage 0.25; T2 chose swing 3.0 and
   nothing else; WPA chose econ ~1.2 / time ~1.8 / swing 0 with damage 0.25.
   Conditioning the graph on any one of them inherits that disagreement into
   the graph, with no principled way to choose. The shipped defaults are the
   thing that actually ships.

**Run as sensitivity, reported, never adopted:**

- **(a) Per-target outer weights.** Repeat the primary fit three more times
  with `w` held at each target's Stage A per-fold weighting. This measures how
  much the graph answer depends on the outer weights -- and if the three refit
  graphs agree with each other far better than the three outer weightings did,
  *that is a finding worth having* and is reported as one.
- **(b) One bounded alternation.** `b` (with shipped `w`) -> `w` (with that
  `b`, via the existing `fit_constrained_weights`) -> `b` again. **Exactly two
  `b` steps, declared up front.** The report states whether the second `b` step
  moved anything.

**Rejected: alternating to convergence.** The objective is non-convex, so there
is no stopping rule that is not itself a hyperparameter; running it inside 5
outer folds x inner folds is expensive; and "we iterated until it stopped
moving" is a selection surface dressed as a numerical detail.

## Stage order

1. **Stage C0 gate.** Leverage extraction plus the reconstruction and linearity
   tests; the in-fold `dP` table; `swing_plugin` scored on the full yardstick
   matrix; and the correlation of every candidate-so-far against
   `current_graph` at round and player-match level. **Read before C1 starts.**
   If C0 shows the metric does not move, C1 still runs -- a null needs the
   fitted candidates to be a null -- but the report leads with C0 and C1's
   results are framed as confirming or contradicting it, not as the headline.
2. **Stage C1.** Family A against the frozen targets, the five-rung control
   ladder, diagnostics, the full matrix. **G3 and G4 are not fitted against T1**
   -- 26 free parameters against 1,114 matches is the ratio this spec already
   says is indefensible, and running it anyway then declining to believe it
   would be theatre. T1 carries G1a, G1b and G2 only; G3 and G4 run on T2 and
   WPA, and their T1 column reads "not fitted -- insufficient matches per
   parameter" rather than a number.
3. **Stage C2.** Family B: **G5 (symmetric)**, then sensitivities (a) and (b),
   then the deferral check. Gated on C1's report existing and having been read,
   mirroring the parent project's Stage B gate. G5 is the whole of Family B on
   today's data -- both full-resolution splits are deferred -- and it is the
   candidate with a measured mechanism behind it.
4. **Adoption, or not.** A separate decision, made by a human, from the report.

**Cost.** `load_all_observations` already replays all 1,151 matches per run and
takes minutes; Stage C's extractor is the same replay retaining per-kill terms,
so the same order of magnitude. The `dP` table is a counting pass over 178,242
kill events, **measured at 1.5 seconds**, so running it once per fold is free. The
design matrix is built once and shared by every candidate, so the ladder's cost
is dominated by the fits, not the extraction.

## What success looks like, and what failure looks like

Declared before any fitting. The report prints this list and marks each line
satisfied or not.

**Called a predeclared analysis plan, not a pre-registration, and the
distinction is honest rather than pedantic.** This dataset was used extensively
to *design* what is being tested: G2's basis was chosen by fitting candidate
bases to this data, G5's two tilt axes came from measuring this data's factor
profiles, the practical-equivalence bound is this study's own CI width, and the
collinearity threshold is set just below this data's observed minimum. None of
that is illegitimate -- but it is not a pre-registration, and calling it one
would claim an independence the analysis does not have. What the plan does buy
is that the thresholds cannot be moved after the results are seen.

**Three separate verdicts, mirroring the ladder, never merged into one.** An
earlier draft asked whether *any* of the items below tripped, which would have
written up a genuine held-out improvement as a failure because econ's
coefficient stayed negative. The items answer three different questions and are
reported as three:

| verdict | question | items |
|---|---|---|
| **A1 -- prediction, next rounds** | does a refit graph predict future rounds better? | 1, 2 |
| **A2 -- prediction, match outcome** | does first-half Impact predict the match better? | 6 |
| **B -- collinearity** | did we explain the econ collapse? | 3, 4, 5 |
| **C -- structure** | do the components want different curves? | 7 |

**A1 and A2 are separated because they are different questions on different
targets**, and an earlier draft merged them: the primary comparisons declare on
T2, while the `kill_diff` bar is a T1 first-half-to-match comparison, so a T1
null could have failed a verdict that T2 had passed. A2 is also the closest
thing here to the product question the parent project started from -- *does my
performance predict my wins* -- so it earns its own line.

Each verdict is "not helped" if any of *its own* items trip. The report prints
all three side by side and never summarizes them into a single line -- a
Verdict A null alongside a Verdict C signal is a coherent and expected outcome,
and collapsing it would destroy the finding.

### The predeclared primary comparisons

Named here, before any fitting, because "any candidate on any target" is a
dozen-plus tests at 95% and would produce roughly one false winner even if the
refit does nothing. Everything else in the ladder is still fitted, scored and
printed -- it simply cannot declare success.

| | comparison | interval | declares |
|---|---|---|---|
| P1 | **G2** vs `current_graph`, paired held-out weighted log loss | **97.5%** | Verdict A |
| P2 | **G3** vs `current_graph`, paired held-out weighted log loss | **97.5%** | Verdict A |
| P3 | **G5** vs `stage_a_exact`, joint paired held-out weighted log loss | 95% | Verdict C |

**G2 and G3 are co-primary for Family A**, at 97.5% each -- two shots at the
same null, so the bar rises to compensate. The cost is real and is stated
rather than hidden: with the headroom this spec already measured, a marginal
improvement that would have cleared 95% will not clear 97.5%. That is the
intended trade, since the action a success licenses is changing a shipped
metric.

**Each is compared on one frozen target, declared now: T2**, which has the
matches to support them; T1's column is reported but is not a success test (and
G3 is not fitted on T1 at all -- see the stage order). WPA is a yardstick row,
never a success test, since it is attribution rather than prediction.

**P4 -- G2 against G3, paired and direct -- is reported as a finding in its own
right and carries no success claim.** It is the comparison that actually
teaches something, and it follows the parent spec's own convention for
co-primaries: *disagreement is printed as such, not resolved by picking a
favourite*.

- **They agree** -- the answer is robust to how much freedom the curve is
  allowed, which is the strongest form of the result.
- **G3 wins** -- the extra freedom earned its keep, and the curve has structure
  G2's five-parameter basis cannot express.
- **G2 wins** -- either G3's shrinkage was too weak, or the shape assumption was
  right all along. Both readings are stated; neither is asserted.

Family B needs no adjustment: G5 is its only candidate, so P3 is a single test.

**The items, and what trips each:**

1. **No held-out improvement.** Neither P1 nor P2 has a match-clustered CI
   excluding zero at 97.5%. Non-primary candidates that look better are
   reported as exploratory and explicitly cannot trip or clear this item -- a
   candidate selected for beating the baseline *because* it beat the baseline
   is not evidence of anything.
2. **The metric did not move, to within this study's resolution.** At Stage C0
   this is `swing_plugin` against `current_graph`; the same statistics are
   recomputed for every *fitted* candidate at the end of C1 and C2, since none
   exist when C0 runs.

   **A high correlation alone does not establish this**, and an earlier draft
   treated it as if it did: two scores correlating at 0.999 can still differ in
   ranking near decision boundaries, in calibration, and after aggregation to
   match level. So the condition is an explicit **practical-equivalence bound**,
   both parts required: the paired held-out weighted-log-loss difference has a
   CI contained within **+-0.0008** -- the parent report's own T2 interval
   half-width, i.e. this study's resolution floor -- **and** the
   exposure-weighted RMS deviation between the two candidates' round scores is
   under 1% of the score sd. Correlation and sign-flip rate are reported as
   context, not as the test.
3. **Targets still disagree.** Operationally, the three graphs fitted against
   T1, T2 and WPA **agree** only if *both* hold for all three pairs: Spearman
   rank correlation between the rescaled `b` vectors **above 0.90**, and
   exposure-weighted RMS difference **under 15%** of the exposure-weighted mean
   price. Item 3 trips if they do not. The bar is set where it is because the
   outer weightings disagreed to the point of putting zero weight on different
   components; rank agreement is the natural analogue for a curve.
4. **Collinearity unchanged.** Operationally: the **maximum** pairwise
   |correlation| among `econ_impact`, `time_impact` and `swing_impact` under the
   refit graph stays **at or above 0.70**. Today's range is 0.733-0.895, so 0.70
   is just below the current minimum -- clearing it means the refit moved the
   *tightest* pair off where it sits now. Read against the stated floor:
   `damage` is graph-free and already correlates 0.733-0.895 with the three, so
   nothing here can drive them to zero and a failure to do so is not a finding.
5. **econ still flips.** `econ_impact`'s partial coefficient remains negative
   in every fold under the refit graph. (Measured today: negative in 5/5 folds
   for T2 and 5/5 for T1; WPA is mixed -- 3 positive, 2 negative -- so WPA is
   not part of this criterion.)
6. **Still no gain over the plain baseline.** No candidate beats `kill_diff` on
   the first-half yardstick by a CI-positive margin -- the bar `current_impact`
   currently fails at -0.0017, CI [-0.0067, +0.0038].
7. **The tilts add nothing.** Tested as a **single joint comparison, not 12
   separate CI checks**: G5's paired held-out weighted-log-loss improvement over
   the nested exact-arithmetic Stage A model, with a match-clustered bootstrap
   CI. Twelve "does this interval span zero" checks are not a valid joint null
   test and would have had a false-positive rate nobody had budgeted. The
   individual coefficients are still reported, as diagnostics.

   If that joint interval spans zero, Family B found nothing on these two linear
   axes. **That lowers the priority of the deferred full-resolution splits; it
   does not retire them** -- an earlier draft claimed it did, which overreads a
   null on two linear directions into a null on 78 free parameters. The `kill`
   vs `death` contrast is separately flagged weakly identified per the
   identification section.

**A null result is the most likely outcome for Family A and is a complete
deliverable.** The prior is stated in this spec with numbers: R^2 0.970 between
the shipped graph and the data's own swing curve, r = 0.998 between the two
resulting metrics, ten of twenty-five directions barely identified. If the
report confirms it, the finding is "the hand-tuned kill-order graph was already
approximately right, here is how we know, and here is the graph the data would
have written" -- which is worth having, and is worth not overturning.

**Family B carries a different prior, and the report must not pool the two.**
G5 is the one candidate attacking a constraint the data measurably objects to
(profile correlations -0.935 for `econ` against `swing`, 0.078 for `econ`
against `time`), and it is the one with a concrete mechanism for an existing
unexplained finding. It is also small in amplitude -- factor CVs of 0.031-0.112
against the curve's own 0.313 -- so a modest effect is the expectation, not a
disappointment. A Family A null alongside a Family B signal is a coherent and
likely outcome: *the shared curve's shape was right, and the mistake was
sharing it.* The report states the two families' results separately and never
summarizes them as one verdict.

**This stage HAS helped only if both of:**

- a candidate's paired held-out delta against `current_graph` has a CI
  excluding zero on at least one frozen target, **and**
- that candidate passes the **graph-level stability criterion**: the upper
  bootstrap bound of (fold-to-fold RMS deviation / shipped-to-candidate RMS
  deviation), both exposure-weighted, is **below 1**. In words -- the candidate
  differs from the shipped graph by more than it differs from itself across
  folds. The rejected per-parameter version of this rule survived into two
  places in an earlier draft and is gone from both.

Either one alone is reported as suggestive and nothing more.

## Output

Printed table plus JSON (`--out`, `DATABASE_URL` override honoured), matching
`scripts/evaluate_impact.py`:

- **Stage C0 block, first:** the measured `dP` table with visit counts and
  cluster-bootstrapped CIs; the exposure-weighted regression of the shipped
  graph on it with R^2 and per-parameter residuals; and, **over all eligible
  matches rather than this spec's 250-match probe**, the correlation between
  `current_graph` and `swing_plugin` Impact at round and player-match level,
  with the sign-flip rate.
- Per-parameter exposure: crossings, distinct rounds, VIF, near-null
  projection.
- Per-candidate: the rescaled 5x5 graph per outer fold and pooled, fold-to-fold
  spread, negative-parameter count, monotonicity violation count, indeterminacy
  flags.
- **G5's 18 coefficients** per outer fold with match-clustered CIs, and the
  effective 5x5 curve each (component, side) pair ends up with once its tilt is
  applied, printed next to the shared curve so the difference is visible as a
  table rather than as coefficients. The **`w_{c,kill}` vs `w_{c,death}`
  contrasts carry an explicit weakly-identified flag** and the measured
  0.937-0.957 column correlations beside them, so no reader mistakes a wide
  interval there for an absence of effect.
- **The player-level read of the kill/death split, including trades**, on the
  Stage 0 block recomputed under each Family B rung: per-player `kill_impact`
  and `death_impact` separately, the within-player tercile lift and per-player
  correlations for each half as well as pooled, the trade discount (death cost
  as scored minus death cost with no trade credit), and the
  `traded_teammate` / `traded_by_teammate` counts. This is the only place in the
  report where the two halves separate, so the report states its player-level
  result next to the team-level one and says plainly which yardstick can see
  what. Family A and Family B results are printed under separate headings and
  never summarized into one verdict.
- The measured per-state factor profiles (`econ`, `time`, `swing`, `traded`)
  with their axis correlations, since they are the evidence G5 rests on and
  should be re-derived rather than trusted from this spec.
- **The deferral check:** current match count against the ~4,000 re-open
  threshold for the fully free per-component fit, so the report says plainly
  whether that stretch goal has become reachable.
- The five-rung T2 control ladder with paired deltas and CIs, headline on
  4 -> 5.
- The targets x yardsticks matrix with every Stage C candidate alongside the
  parent project's `current_impact`, `kill_diff`, `damage_only`, `acs`,
  `fitted_T1`, `fitted_T2` and `fitted_WPA` rows, so the two stages are read on
  one page. Both `current_graph` (unrounded) and `current_impact` (rounded)
  rows, with their gap printed.
- Component correlation matrix and drop-one costs recomputed under each
  candidate graph, against today's values (correlations 0.733-0.895; drop-one
  weighted-log-loss costs: swing 0.00836 [0.00707, 0.00968], econ 0.00098
  [0.00059, 0.00136], time 0.00007 [-0.00007, 0.00019], damage 0.0000177
  [-0.00007, 0.00012]).
- Stage 0's descriptive block recomputed under each candidate graph
  (within-player tercile lift, per-player correlations), each match scored by
  the candidate fitted on the folds excluding it -- **reported, never selected
  on**.
- The pre-registered success/failure checklist with each line marked.
- `n` at every level; `ex_ante` labelled on every component number.

## Testing

Same conventions as the parent project: plain ORM construction, no DB session,
following `tests/test_player_profile_types.py`; anything needing live Postgres
skips cleanly when unreachable.

`tests/test_kill_order_leverage.py`:

- **Reconstruction gate (ships as a test, same role as the parent's Task 0).**
  For a sample of matches, `damage_diff + SUM_k b_k^shipped * x_r[k]` equals
  `build_impact_rows_for_match`'s round-level impact differential to within a
  bound derived from the rounding, not "approximately": at most
  `0.5 * (players in round) * (rounded terms per player)`. A future change to
  `impact.py`'s combination step fails here loudly.
- **Linearity gate.** Scaling every shipped weight by `c` scales the
  reconstructed non-damage part by exactly `c`, and leaves `damage` untouched.
- **Side symmetry.** The shipped 50-edge graph round-trips through the
  25 <-> 50 mapping unchanged, and any fitted 25-vector mirrors to a graph with
  zero symmetry violations.
- **Self-kills** contribute on the death side only, with the sign flipped, and
  zero on the kill side.
- **Untracked transitions** land on the fallback parameter and flag the round;
  they are never silently folded into a neighbouring parameter.
- **The extractor writes nothing** -- no `ImpactScore` row added, mutated or
  committed. Same regression guard as `tests/test_impact_exante_swing.py`'s
  third assertion.
- **Resurrection policy** matches `impact.py`'s, not `state_replay.py`'s, on a
  fixture round containing a re-referenced dead player.

`tests/test_kill_order_refit.py`:

- The in-fold `dP` table is built from training matches only -- a fixture where
  a held-out match's outcome, if leaked, would change the table detectably.
- The G3 shrinkage prior comes from the training fold, never the full data.
- The 26-column and 78-column builders agree when `w` is the shipped
  `(1, 1, 1)`. (The 78-column builder ships with G5 even though the fully free
  fit is deferred -- G5 needs the per-component split, and testing it now is
  what makes the deferral cheap to reverse.)
- **G5 nests `stage_a_exact`, exactly.** Stage A as published was fitted on the
  `round()`ed component columns; G5's base columns are the unrounded sums, so
  the two can never agree coefficient-for-coefficient and an earlier draft's
  "nests Stage A exactly" was unachievable. Resolved by adding
  **`stage_a_exact`** -- a refit of Stage A's four-feature model on the exact
  pre-rounding columns -- as the **mandatory nested comparator**, against which
  the nesting assertion is exact. The originally published rounded Stage A
  stays in the matrix as a historical row, clearly labelled, and the gap
  between the two is printed so the rounding cost is visible.
- **G5 nests the shipped scorer**: tilts zero and all `w = 1` reproduces
  `current_graph`, exactly, since both are then the same linear form.
- **The six (component, side) base columns sum correctly**: kill half plus
  `traded`-weighted death half equals the combined component column, which
  equals today's `econ_impact` / `time_impact` / `swing_impact` within the
  rounding bound.
- **`margin_hat` / `total_hat` are fixed constants** -- computed over the 25
  lattice states unweighted, identical in every fold, and 0 for the fallback
  parameter. A fixture asserts they do not change when the training set does.
- For a fixture round whose kills all cross the *same* state `k`, each of G5's
  tilt columns is exactly `margin_hat_k` (or `total_hat_k`) times that
  component's base column -- proportional, not zero. Asserted as proportional,
  because getting this backwards is the easy way to ship a tilt column that
  silently duplicates a base column.
- The tilt columns are built from a `b` held fixed, and the fit never updates
  `b` and the tilts in the same step.
- G2's basis contains G1b and G1a exactly, in that order: constraining
  `gamma = delta = eps = 0` reproduces G1b, the affine fit; additionally
  constraining `alpha = 0`, with `beta` pinned by the normalization rather than
  fitted, reproduces G1a, the plug-in. (An earlier draft stated this backwards,
  as though adding `alpha` produced the plug-in.)
- Stage C's fold assignment is identical to the parent project's for the same
  match set.
- Scale normalization is applied when a graph is reported or compared, and
  never before scoring.
- The stability criterion is computed at graph level, not per parameter: the
  bootstrap upper bound of (fold-to-fold RMS / shipped-to-candidate RMS),
  exposure-weighted, with a fixture where a candidate identical to the shipped
  graph does **not** come out "stable" by virtue of having nothing to measure.

**No new numeric primitive is required.** An intermediate draft concluded that
G3 needed an `offset` argument on `fit_logistic`; the composite-damage-column
reparameterization above removes that need, and is the *correct* shrinkage
besides -- the offset version shrinks `q` rather than `b`. `stats_math` is
therefore untouched by this stage: `fit_logistic`, `standardize` /
`back_transform`, `weighted_log_loss`, `auc`, `platt_calibrate`,
`cluster_bootstrap_ci` and `paired_bootstrap_delta` all serve as-is. If the G2
basis needs a design-matrix builder, it is tested against an analytically known
case like everything else there.

A test does pin the G3 parameterization down, because getting it wrong is
silent: with a deliberately large penalty and a prior far from the truth, the
recovered `b` must converge on `b_prior` and **not** on `b_prior / d`.

## Out of scope

- **No change to `_KILL_ORDER_GRAPH`.** This spec and its eventual plan produce
  a report and a proposed table, nothing else. Adoption is a separate
  deliberate act, and it is **larger than adopting new `FACTOR_WEIGHTS` was**:
  `kill_order_bonus` also feeds the display columns `clutch_kill`,
  `clutch_death`, `post_plant_kill`, `post_plant_death`, `econ_kill` and
  `econ_death`, which the site shows. Adoption would need an
  `IMPACT_CALCULATION_VERSION` bump, a full rescore via
  `scripts/recompute_impact.py`, a `diff_impact_scores.py` pass, and the
  `player_view_cache` invalidation the version bump already triggers through
  `cache_version()`. Said here so it is priced; not done here.
- **No new tables, no migrations.**
- **No web endpoint, router or template.** Nothing here is imported by
  `app/main.py`.
- **The other inner curves stay fixed**, and are named so a later stage has a
  list: `_time_factor`'s post-plant ramp and its 38-45s window,
  `_econ_swing_risk_factor`'s internals and thresholds, `_traded_factor`'s
  10-second window, `_categorize_econ`'s tier codes, and the ACS -> damage 1.25
  multiplier.
- **No change to `state_replay.py`**, and no attempt to reconcile its
  resurrection policy with `impact.py`'s. That divergence is documented here
  and left alone; reconciling it would change Fight-EV and the player-page
  diamonds, which is a different project.
- **Both full-resolution splits are deferred on data volume** --
  `unconstrained_B` (78) and `split_kill_death` (52) -- with a stated re-open
  trigger of ~4,000 matches, which re-opens them for a look rather than a
  verdict. Neither is a deployable graph and no part of this spec proposes
  adopting them. G5 *is* a first-class candidate, but adopting it would still
  be a formula change rather than a weight change -- it adds eighteen numbers
  the shipped scorer has no slot for -- so it goes through the same separate,
  deliberate decision as everything else here.
- **No player-level fitting target.** G5's kill/death split is *read* on the
  existing player-level descriptive block, not fitted against a player-level
  outcome. A player-level target would be a new observation unit and a new
  evaluation contract, and belongs to its own spec if it is ever wanted.
- P2 (player page) and P3 (squad page) remain the parent spec's, unchanged.
