# Declaration draft: `k`, `FLOOR`/`CEIL`, and the `W` grid

**Status:** DECIDED 2026-09-07, signed off after five review rounds. Nothing
here has been run against the specified regression -- these are grids, rules
and targets, not fitted values. Superseded the three matching rows in
`2026-09-07-predeclared-values.md`'s "To be fixed at first run" table, which
now point to "The k / FLOOR / CEIL / W grid decision" section there. §8's
"newly open" question 2 (outer folds beyond the three named populations) was
closed the same day -- see that file's version of this section for the
resolution; not repeated here to avoid two copies drifting.

**Amends:** `2026-09-07-predeclared-values.md`.
**Source spec:** `specs/2026-09-03-plant-window-and-time-factor-design.md`,
Part 3 ("Parameterisation", "The fitting contract", "Centering is on
CONTRIBUTION") and Part 4 ("Parameterisation", "The estimator contract",
"Centring").

**The rule this document lives under, restated:** a value here is fixed before
the measurement it governs. If a result lands outside a stated tolerance, the
tolerance does not move -- the result is a finding. Changes are allowed;
changes made *after* seeing the number they judge are not, and must be recorded
as a dated amendment.

---

## 0. Evidence base and provenance -- read before the declarations

Grid placement used **exploratory fits and clipping diagnostics on the existing
dataset**. The specified exact-state logistic regression has **not** been run.
No scoring outcome, centring residual, predictive loss or ranking informed any
choice below. This section exists so the record is honest about what was seen
before the grids were fixed; it is not a result.

**Snapshot:** Render DB, 3,124 matches, read-only, 2026-09-07. 484,610 non-self
kills in non-surrendered rounds; 168,432 of those are pre-plant kills in
non-phantom, non-surrendered planted rounds (34.8%, reproducing `M5`'s 34.7%).

**Validation of the replay.** On the old percentage-point amplitude basis the
replay reproduces `M20`'s published table: mean scalar 1.0510 / 1.1017 / 1.1343
/ 1.1597 / 1.2056 against `M20`'s 1.052 / 1.103 / 1.136 / 1.162 / 1.209, and
clamp rates 2.4% / 8.0% / 27.0% / 31.3% against 2.4% / 7.9% / 26.9% / 31.1%. It
reproduces `M3`'s lifts exactly.

**Percentage points do not convert to log-odds by a single factor.** Using the
actual far (`<-30s`) and near (`-10..-5s`) baselines:

| adv | far% | near% | pp lift | logit lift | ratio |
|---|---|---|---|---|---|
| -3 | 38.2 | 7.0 | -31.2 | -2.109 | 6.75 |
| -2 | 46.9 | 20.5 | -26.5 | -1.235 | 4.66 |
| -1 | 52.7 | 48.2 | -4.5 | -0.181 | 4.00 |
| 0 | 56.8 | 73.3 | +16.5 | +0.737 | 4.46 |
| +1 | 61.5 | 91.0 | +29.6 | +1.851 | 6.26 |
| +2 | 70.2 | 98.1 | +27.9 | +3.100 | 11.11 |

The ratio runs 4.00 to 11.11, so the old `{1, 2, 3, 5, 8}` grid cannot be
carried over by rescaling, and a rescaling that uses the minimum multiplier
(4, at `p = 0.5`) understates the levels that actually drive ceiling clamping.

**Proxy amplitude lines** (weighted least squares on the six bucket-level
differences, by side, per `M4`'s warning that pooled side figures are a
Simpson's artifact):

```
L_attacker(adv) = 0.302 + 0.536 * adv
L_defender(adv) = 0.555 + 0.631 * adv
```

These are **proxies for the specified regression, not the regression.** They
support grid placement only. They do not license a claim about which `k` the
real fit will select, nor a quantified uncertainty band.

**Clamp rates under those proxy lines** (all-kill denominator; `c` is the
pre-plant centring constant, `ΣK / Σ(K·s)`, since today's pre-plant factor is a
flat `1` at `impact.py:180`):

| k | floor% | ceil% | K-wtd s | c | \|c-1\| | effective bounds | in target |
|---|---|---|---|---|---|---|---|
| 0.20 | 0.00 | 0.00 | 1.039 | 0.962 | 0.038 | [0.192, 1.636] | |
| 0.27 | 0.00 | 0.00 | 1.053 | 0.950 | 0.050 | [0.190, 1.615] | |
| 0.35 | 0.00 | 0.00 | 1.069 | 0.936 | 0.064 | [0.187, 1.591] | |
| 0.45 | 0.00 | 0.14 | 1.088 | 0.919 | 0.081 | [0.184, 1.562] | |
| 0.55 | 0.00 | 1.36 | 1.107 | 0.904 | 0.096 | [0.181, 1.536] | |
| 0.60 | 0.01 | 2.17 | 1.115 | 0.897 | 0.103 | [0.179, 1.525] | yes |
| 0.65 | 0.31 | 2.76 | 1.122 | 0.891 | 0.109 | [0.178, 1.515] | yes |
| 0.70 | 0.52 | 2.95 | 1.129 | 0.886 | 0.114 | [0.177, 1.506] | yes |
| 0.75 | 0.56 | 3.07 | 1.136 | 0.880 | 0.120 | [0.176, 1.497] | yes |
| 0.80 | 0.58 | 3.16 | 1.143 | 0.875 | 0.125 | [0.175, 1.488] | yes |
| 0.90 | 0.60 | 5.21 | 1.154 | 0.866 | 0.134 | [0.173, 1.473] | |
| 1.00 | 0.61 | 6.57 | 1.162 | 0.861 | 0.139 | [0.172, 1.463] | |

Mechanism, for a reader checking the edges: the ceiling engages defender
`adv = +1` at `k ≈ 0.59` (`0.7 / 1.186`) and attacker `adv = +1` at `k ≈ 0.84`
(`0.7 / 0.838`), which sets the lower and upper edges of the window.

**Known approximations in the above:** bucket-difference WLS rather than a
per-kill logistic with exact-state fixed effects; `M1`'s 5v5 shape row min-max
normalised rather than a continuously fitted shape; the `kill_order_bonus`
replay does not update alive-indices on self-kills.

Script: `docs/superpowers/diagnostics/` -- to be committed alongside this
document under the naming convention of the existing measurement scripts.

---

## 1. `k` -- the pre-plant amplitude scale

`amplitude = k * logit_lift`;
`scalar = clamp(1 + amplitude(adv, side) * shape(seconds_to_plant), 0.2, 1.7)`.

### 1.1 Grid

```
k in {0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8}
```

Eleven members. Largest consecutive ratio **1.35**; span **14x**. `k` is a
post-fit multiplier on `logit_lift`, so one fit serves the whole grid -- no
refit per member.

**Why this spacing.** The target window's multiplicative width is ~1.5 (proxy:
`0.87 / 0.57 = 1.53`; the pooled contrast, shown only to demonstrate that the
window *moves*, has a similar width). Because the largest grid spacing (1.35)
is smaller than the window width (~1.5), **at least one member falls inside the
window for any shift that preserves its width and stays within span.** That
property, not proximity to any particular value, is what the grid is for.

**Why it extends to 0.2.** The spec's nested comparison may drop the side
interaction. The pooled contrast in §0 shows that **a specification change can
move the window materially** -- it does not, and cannot, establish where a
side-dropped exact-state regression's window would actually fall, being a
Simpson's-contaminated bucket-difference artifact. The extension is therefore
cheap insurance against an unknown, not a targeted placement: additive, at the
low end only, decided before the fit, and unable to remove a candidate.

### 1.2 Authorisation for data-dependent selection

**This declaration explicitly authorises selection of `k` from the grid above by
the rule in §1.5, notwithstanding this file's general "exact value before
running" rule.** The spec's fitting contract requires `k` to be "chosen so the
fitted scalar spans the intended range over the observed data", and the
1.5% / 2-4% clamp targets are the only operational definition of that range. A
fixed default **does not guarantee the target** -- it may happen to satisfy it,
but nothing in the procedure makes it do so, and the spec's rule then forbids
correcting it without an amendment. The grid, criterion, denominator, tie-breaks
and fallbacks are all fixed here. Only which member wins is left to the run.

### 1.3 Clamp-rate definition -- counted on the RAW scalar

Define, before any clamping:

```
raw_scalar = 1 + k * logit_lift(adv, side) * shape(seconds_to_plant)
```

A kill counts **at the floor** if `raw_scalar < 0.2`, **at the ceiling** if
`raw_scalar > 1.7`. Then clamp to `[0.2, 1.7]`, then centre, per the spec's
fit → clamp → centre order.

**An earlier draft said "the pre-centred scalar", which is wrong.** The spec
defines `scalar` as already clamped, so testing it against 0.2 or 1.7 is
vacuous and would report 0.00% at every `k`. "Pre-centred" is not "unclamped".
The §0 diagnostic already counts crossings on the raw quantity, so the tables
there are unaffected -- only this definition was defective.

One row per scored non-self pre-plant kill in a non-phantom, non-surrendered
planted round. Deaths reuse the same scalar and are **not** counted a second
time.

### 1.4 Denominators -- both reported, every row

- **Target denominator: all non-self kills in non-surrendered rounds** (484,610
  on the 2026-09-07 snapshot). This is `M5`'s denominator.
- **Also reported: the affected-population rate**, denominator = pre-plant kills
  in non-phantom, non-surrendered planted rounds (168,432).

**This is an interpretation and is recorded as one.** The spec's target sentence
-- "they bind on under 1.5% of kills at the floor and 2-4% at the ceiling" --
names no denominator. It is read as all-kills because `M5`'s all-kill masses are
visibly where those figures came from (`adv <= -2` within 10s of the plant =
0.70%, 1.42% either side; `adv >= +1` within 10s = 2.21%, 4.08%), and because
the neighbouring sentence says "82.6% of *affected* kills" when it means
affected. The two denominators differ by ~2.9x, so the reading is load-bearing.

**The two populations carry deliberately different exclusions**: the denominator
follows `M5` and excludes only surrendered rounds; the numerator population
additionally excludes phantom plants. Stated so the asymmetry is a recorded
choice rather than an accident.

### 1.5 Selection rule -- deterministic and total

Targets: floor rate `< 1.5%`; ceiling rate in `[2.0%, 4.0%]`, both on the target
denominator.

1. Among members with floor rate `< 1.5%` **and** ceiling rate in
   `[2.0%, 4.0%]`, select the one whose ceiling rate is closest to **3.0%**.
2. If none qualifies, select the member minimising `|ceiling rate - 3.0%|`
   among those with floor rate `< 1.5%`.
3. If none satisfies the floor constraint, select the **smallest `k` in the
   grid**.

**Every tie, in every branch, breaks toward the smaller `k`.**

In branches 2 and 3 the miss is **reported as a finding** with the full
eleven-row table. The grid is not widened, no member is added, and no off-grid
value is used.

**Branch 3 is the minimiser, not merely a conservative default.** With the
fitted lift and shape held fixed, `raw_scalar - 1 = k * logit_lift * shape`, so
reducing positive `k` moves every raw scalar monotonically toward 1 and cannot
increase either crossing rate. If no member clears the floor constraint, the
smallest `k` is the member with the lowest floor rate. The §0 tables confirm the
monotonicity empirically across the full sweep.

**A consequence worth stating: both crossing rates are monotone non-decreasing
in `k`, so the qualifying set is a contiguous run of grid members** and branch 1
is a bracketing choice between at most two neighbours. This is also why the
spacing argument in §1.1 works -- a window in clamp-rate space maps to a
connected interval in `k`.

### 1.5.1 Selection population -- one selection per training population

`k` is selected **once per training population**, never once globally and then
reused. The clamp criterion is not itself an outcome measure, but it is computed
from coefficients fitted on round outcomes, so a `k` chosen using full-data
coefficients and carried into a held-out evaluation leaks that evaluation's
outcomes.

- **Shipped scoring:** select on the frozen full dataset.
- **Temporal split** (fit before the 70th percentile of `played_at`, evaluate
  after): select using **only the pre-70th-percentile training data**, and carry
  that `k` unchanged into the post-70th evaluation.
- **Nested comparisons** (side interaction in/out, pooled vs state-specific
  proximity response): each arm selects within its own training population and
  carries that `k` unchanged into its evaluation.

**"One fit serves the whole grid" means one fit per applicable training
population**, not one fit overall. Within a population, `k` is still a post-fit
multiplier and no member requires a refit.

If the shipped `k` and a training-population `k` differ, **that difference is
reported**, not reconciled.

### 1.6 What may not influence the selection

Only the clamp rates in §1.5. Explicitly **not**: the `M20`-style death-side
residual, any win-correlation or predictive loss, rank or leaderboard movement,
and **not `|c - 1|` or the effective bounds**.

`|c - 1|` is named because it is the sharpest temptation here: it moves
monotonically with `k` and pulls in the opposite direction to the clamp target
(§4.1), so minimising it would drive `k` down until the ceiling clamp is inert.
All of these quantities are **reported at** the selected `k`, never used to
choose it.

### 1.7 No intervals

No confidence intervals are attached to `k`, per the spec: the amplitude was
normatively selected.

---

## 2. `FLOOR` / `CEIL` -- the post-plant clamp

`post_plant_factor = clamp(D / mean_over_t D, FLOOR, CEIL)`.

### 2.1 Values

**Shipped default: `FLOOR = 0.05`, `CEIL = 2.0`.**

**Sensitivity grid: all nine pairs** of
`FLOOR in {0.02, 0.05, 0.1} x CEIL in {1.5, 2.0, 2.5}`, each evaluated at
`W = 2`, each with its own centring constant `c` recomputed and effective bounds
`[c*FLOOR, c*CEIL]` reported.

### 2.2 Justification of record

Measured ratios span ~0.06 (`2v1` at `t = 43`) to ~1.50 (`3v2` at `t = 0`).
Measured as **headroom beyond the observed range in log terms**, the bounds are
near-balanced: `ln(0.06/0.05) = 0.18` below, `ln(2.0/1.50) = 0.29` above. That
is the right way to read headroom on a ratio, and it is the justification.

The multiplicative asymmetry (20x down, 2x up) **follows the observed shape
under this smoother and the five-second eligibility rule. It is not a
mathematical bound**: a mean-normalised ratio can exceed 2.0 when leverage
concentrates at a low-weight second. An earlier draft called the asymmetry
structural; that overstated it and is withdrawn.

Note also that "0.05 is below the observed 0.06, so the floor never binds" is
**not** safe. 0.06-1.50 is the range of `M24`'s published, well-supported cells;
the scored population is every cell clearing the pooling ladder.

### 2.3 How the nine-cell table is read

For `FLOOR < CEIL` the two clamps act on disjoint kills, so
`SUM_supported(K*s)` is additively separable in `FLOOR` and `CEIL`, and `c`
depends on them only through that sum. **The clamp rates and the scalar
distribution are therefore reported as two one-dimensional sensitivities
coupled only through `c`.** Nonlinear downstream summaries (rank movement, loss
metrics) may show genuine joint effects and are reported as found; no blanket
prohibition on reporting an interaction.

### 2.4 Required alongside every row

- Kill-weighted floor and ceiling binding rates.
- Pre-clamp ratio distribution: min, p1, p50, p99, max.
- Count of non-positive denominators falling back to 1.0.
- Count of scored cells landing on each pooling rung.
- **Count of negative numerators** (`D < 0` with a positive denominator),
  reported **separately from ordinary floor binding**. See §5.1 -- under this
  declaration such a kill still scores at `FLOOR`; only the counting is new, and
  it carries no gate.
- **Two distinct statements, never conflated:**
  - "**Final scores insensitive across the tested bounds**" -- identical or
    near-identical centred scores across the grid. This is a statement about the
    report.
  - "**Bound inert**" -- **zero pre-clamp crossings on the scored population**,
    measured on the raw ratio `D / mean_over_t D` before clamping.

  Identical centred scores do **not** establish inertness. If every supported
  raw ratio lies below all three candidate `FLOOR` values, every pre-centred
  factor equals `FLOOR`, `c` varies inversely with `FLOOR`, and `c*FLOOR` is
  identical across the grid -- identical final scores under **universal** floor
  binding. Only the crossing count separates the two cases. (This is the same
  clamped-versus-raw confusion corrected in §1.3, in its second location.)
- Report the observed pre-clamp extremum alongside either statement, so a future
  data shift can be checked against it.

### 2.5 No intervals

No confidence intervals are attached to `FLOOR` or `CEIL`.

---

## 3. `W` -- the post-plant smoother half-window

### 3.1 Values

**Shipped default: `W = 2`, unchanged and never selected from results.**

**Sensitivity grid: `W in {0, 1, 2, 3, 4, 6}`.** `W = 0` is labelled "no
moving-average smoothing" -- the support floor, pooling ladder, endpoint rule
and neutral fallback all remain active, so it measures what the moving average
contributes beyond those protections, not an unprotected estimator. `W = 6` is
labelled a **broad-window stress test**; it may or may not produce visible
differences, and a flat table is a finding rather than a failure.

Evaluated at `FLOOR = 0.05, CEIL = 2.0`, with `c` recomputed per `W` and the
effective bounds reported. **Report the number of supported cells at each `W`.**

### 3.2 Clarification -- what smoothing may and may not change

The pooling ladder runs **first and unchanged** (exact `(a,d,t)` cell → `d`
pooled upward → deadline band → unsupported) and fixes each cell's support
level. **The 60-observation floor is never applied to a summed moving window.**
An earlier draft proposed exactly that; it would introduce a second route to
eligibility, bypass the ladder's fixed ordering, and make varying `W` change
both smoothing and support selection at once. Withdrawn as a redesign.

**The window is evaluated entirely at the target second's resolved rung.** When
neighbouring seconds resolve at different rungs, each neighbour contributes its
estimate **at the target second's pooling level**, not its own, weighted by its
**own per-second observation count at that level**. A pooled count is never
repeated across the seconds it covers. Seconds with no observations at that
level contribute **weight zero**, and the window is **not** widened to
compensate.

Rationale: a moving average must average one estimand. Letting each neighbour
contribute the estimate from whatever rung it happened to land on averages
exact-cell `V` against `d`-pooled `V`, which drags the target toward the pooled
value by an amount that depends on its neighbours' support rather than on time.

At rung (iii) the estimate is constant within its deadline band, so smoothing is
a no-op except within `W` seconds of a band boundary, where it blends the two
band values -- consistent with §3.3.

**Invariance claim, stated precisely.** Running the ladder first makes
**count-based endpoint support** independent of `W`. It does **not** make the
final scored population independent of `W`: smoothing changes `V`, hence `D`,
hence `mean_over_t D`, which can cross zero and trigger the spec's existing
neutral fallback. **Report the two populations separately at each `W`** -- cells
with count-based endpoint support, and cells actually receiving ratio-based
scoring rather than the 1.0 fallback.

### 3.3 Clarification -- band boundaries

The moving-average window is **not** truncated at the 38.0 / 41.5 band
boundaries, consistent with the spec's "No hard discontinuity at plant+38". At
`W >= 2` the window routinely straddles 41.5; stated because two implementers
would otherwise diverge here.

### 3.4 `W` is never selected from results

A result favouring another `W` is a finding requiring a dated amendment.

---

## 4. Exploratory expectations -- recorded so a result is anticipated, not chased

### 4.1 `|c - 1|` may well exceed its 0.05 tolerance -- an exploratory prediction

**This section states an expectation. It is not a tolerance, not an acceptance
criterion, and not a claim about the specified regression.** Nothing here may be
used to judge a result as passing or failing.

Today's pre-plant time factor is a flat `1` (`impact.py:180`), so
`c = ΣK / Σ(K·s)`. Under the §0 proxy the K-weighted mean scalar sits near 1.14
and `c` near 0.88, giving `|c - 1|` of 0.10-0.13 across the window and effective
bounds near `[0.175, 1.49]`.

**Exploratory prediction: `|c - 1|` in `[0.10, 0.13]` at the selected `k`.** A
result inside that range is consistent with the proxy mechanism; a result
outside it says the specified regression differs from the proxy. Neither is a
licence to re-tune `k` (§1.6).

**The conflict demonstrated is a conflict for the proxy.** Positive side
intercepts do not by themselves determine the K-weighted scalar mean --
advantage mix, shape, `kill_order_bonus` weights and clipping all enter it. On
the proxy, `|c - 1| <= 0.05` is reached only at `k <= ~0.27` (0.050 at 0.27,
0.038 at 0.20), where the ceiling never binds at all. Whether the specified
regression reproduces that trade-off is unknown until it runs. If it does, the
clamp target wins and `|c - 1|` is reported as a finding, never an assertion
that fails the build, exactly as already declared.

### 4.2 The selection the proxy predicts

On the proxy the rule selects **`k = 0.8`** (ceiling 3.16%, floor 0.58%), with
`k = 0.6` the only other qualifying member (2.17%). Recorded so that a real fit
selecting 0.8 is an anticipated outcome rather than a surprise, and a real fit
selecting something else is informative about how the specified regression
differs from the proxy. **This is a prediction, not a preference** -- the rule in
§1.5 resolves either outcome without discretion, and a mismatch is neither a
failure nor grounds to revisit the grid.

---

## 5. Deliberately out of scope

Both items below are real and both change scores. Neither is bundled into a
grid declaration.

### 5.1 The negative-numerator fallback

The spec handles a non-positive **denominator** explicitly -- "dividing by it
flips the factor's sign, which the `FLOOR > 0` clamp then hides rather than
catches" -- and says nothing about a negative **numerator**, though it concedes
`V` is "monotone in alive counts in expectation but not necessarily in every
smoothed cell". So `D < 0` is possible and currently gets silently floored to
`FLOOR` as if it were an ordinary low-leverage second.

**In scope here:** counting and separate reporting (§2.4). That changes no
score and is the only thing that surfaces the defect.

**Out of scope here:** whether such a cell should instead fall back to 1.0.
That is a scoring-policy change. Note that its two apparent open questions are
already answered by the existing estimator contract -- item 5 excludes
fallback-to-1.0 seconds from `mean_over_t`, and a cell with fewer than five
eligible seconds is unsupported and returns 1.0 -- so the amendment would be
small. It is still an amendment.

**Report-only. There is no gate.** An earlier draft said "a non-trivial
negative-numerator count blocks the rescore", which introduced a blocking policy
inside a section describing a counting-only change, and left its threshold to be
fixed after the count arrived -- the precise move this file exists to forbid.
Withdrawn. The count is reported; whether it warrants the fallback is a decision
taken with the number in hand and recorded as a dated amendment. This matches
the owner's standing decision that the non-inferiority gates are loud reports
rather than build failures.

### 5.2 Any window-based support redesign

See §3.2. Out of scope; if it is ever wanted it is a separate, dated change to
the estimator contract, evaluated on its own.

---

## 6. Spec premise flagged for revisit AFTER the fit -- no change requested now

Part 3, "The bounds are policy on the PRE-centred scalar" reasons: "with
`|c - 1|` expected at or under 0.05, the excursion is at most 1.785 / 0.19 and
the question is not worth solving harder than this."

**The §0 proxy puts that premise in doubt** -- it implies an excursion nearer
`[0.175, 1.49]`. But a proxy cannot establish the fitted `c`, so **no correction
is requested and no number in the spec changes now.** An earlier draft asserted
"the real ceiling is 1.49"; that claim is withdrawn as premature.

What stands unchanged either way: `[0.2c, 1.7c]` remains the general statement
of the effective bounds, the unit test continues to assert the **effective**
bound rather than the nominal one, and re-clamping after centring stays rejected
(breaking an exact preservation gate to fix a cosmetic bound is the wrong trade
at 5% and more so at 12%).

**Action:** report the fitted `c` and the effective bounds it implies. If the
excursion materially exceeds 5%, amend the spec's prose then, with the fitted
number, as a dated amendment.

---

## 7. What changed across review rounds, and why

| Round | Change | Reason |
|---|---|---|
| 1 → 2 | Old `{1,2,3,5,8}` grid dropped | Percentage-point basis; ratio to log-odds runs 4.0-11.1, so no single rescaling transfers |
| 1 → 2 | Fixed-default `k` rejected in favour of a predeclared selection rule | Spec requires `k` "chosen so the fitted scalar spans the intended range over the observed data"; a fixed default does not guarantee the clamp target (reason corrected in round 5 -- it originally read "unreachable by construction") |
| 2 → 3 | Clamp denominator named explicitly, both rates reported | Spec's target sentence names no denominator; `M5` and `M20` use different ones, a ~2.9x difference |
| 2 → 3 | Tie-break extended to every branch | Branch 2 was left undefined |
| 2 → 3 | "No fitted model informed it" disclosure replaced | Inaccurate: proxy lines were fitted. Replaced with exploratory-fits wording |
| 2 → 3 | "±50%" uncertainty claim withdrawn | Invented, not a verified bound |
| 2 → 3 | "Asymmetry is structural" withdrawn | A mean-normalised ratio is not bounded near 2.0 in general |
| 2 → 3 | "No interaction beyond `c` may be reported" narrowed | Separability holds for the scalar distribution, not for nonlinear downstream summaries |
| 2 → 3 | Negative-`D` fallback split out; counting retained | The fallback is a scoring-policy change, not a grid decision |
| 2 → 3 | Window-based support floor withdrawn | Bypasses the ladder's fixed ordering and confounds the `W` sensitivity |
| 3 → 4 | Grid extended down to 0.2 and 0.27 | Side-interaction-dropped branch moves the window below the old grid's floor |
| 3 → 4 | `\|c - 1\|` added to the selection-exclusion list | Computed to be monotone in `k` and to pull against the clamp target |
| 3 → 4 | §4 predeclared expectations added | `\|c - 1\|` conflict and the proxy's predicted selection, recorded before the run |
| 3 → 4 | §6 spec correction requested | The "5% cosmetic" premise is wrong by ~2.5x |
| 4 → 5 | **[P1]** Clamp rates counted on `raw_scalar`, not "the pre-centred scalar" | `scalar` is defined as already clamped, so the old test was vacuous and would report 0.00% at every `k`. Diagnostic was already correct; the definition was not |
| 4 → 5 | **[P1]** §1.5.1 added: one selection per training population | Clamp rates derive from outcome-fitted coefficients, so a full-data `k` carried into a holdout leaks that holdout's outcomes |
| 4 → 5 | §3.2 specifies smoothing across differently pooled seconds | "Within the resolved cell" and "observation-count weights" were both ambiguous when neighbours resolve at different rungs; the choice changes `V`, `D` and the `W` sensitivity |
| 4 → 5 | §3.2 invariance claim narrowed to count-based endpoint support | Smoothing changes `D`, so `mean_over_t D` can cross zero and move a cell to the neutral fallback -- `W` does affect the finally scored population |
| 4 → 5 | §5.1 negative-`D` gate removed, report-only | A blocking policy with a threshold left open until after results is what this file forbids |
| 4 → 5 | §4.1 and §6 made conditional; "structurally" and "the real ceiling is 1.49" withdrawn | A proxy demonstrates a conflict for the proxy, not an incompatibility in the specified regression |
| 4 → 5 | §2.4 separates "final scores insensitive" from "bound inert" | Centring can cancel a universally binding clamp -- identical scores are compatible with 100% floor binding |
| 4 → 5 | §1.2 "unreachable by construction" → "does not guarantee the target" | A fixed default can happen to satisfy it |
| 4 → 5 | §1.1 side-dropped claim softened; §1.5 branch 3 justified by monotonicity | The pooled proxy shows a window can move, not where it lands. Reducing positive `k` moves every raw scalar toward 1, so branch 3 is the minimiser, not just a default |

---

## 8. Status of the open questions

### Closed by the round-4 review

- **All-kill denominator (§1.4)** -- accepted as the explicitly recorded
  interpretation, both denominators reported.
- **The grid's low extension (§1.1)** -- accepted as reasonable coverage, with
  the supporting claim softened: the pooled proxy shows a window can move, not
  where the side-dropped exact-state regression's window lands.
- **Recording proxy predictions (§4.1)** -- accepted, provided they stay
  conditional expectations rather than acceptance criteria. Rewritten as such.
- **§1.5 branch 3** -- resolved, and upgraded. With lift and shape fixed,
  reducing positive `k` moves every raw scalar toward 1 and cannot increase
  either crossing rate, so the smallest `k` is the floor-rate minimiser, not
  merely a conservative default.

### Newly open -- introduced by this round's fixes, not yet reviewed

1. **§3.2's resolution of differently pooled neighbours.** The declaration now
   says each neighbour contributes its estimate **at the target second's**
   pooling level, weighted by its own per-second count at that level. The
   alternative -- each neighbour contributes its own resolved estimate -- was
   rejected on the grounds that a moving average must average one estimand. This
   choice changes `V`, `D` and the whole `W` sensitivity, and it is the least
   reviewed line in the document.
2. **§1.5.1's scope.** Three training populations are named (shipped, temporal
   split, nested arms). If the pipeline has outer folds beyond these -- the
   commit record mentions the whole feature pipeline being estimated inside each
   outer training fold -- the rule should name them explicitly rather than rely
   on "each applicable training population".
3. **§2.4's crossing-count requirement.** Distinguishing "bound inert" from
   "final scores insensitive" now requires counting pre-clamp crossings on the
   post-plant scored population. Confirm the estimator actually exposes the raw
   ratio at that point, rather than only the clamped factor.

### Standing, unresolved by design

4. **Anything in §0 treated as more solid than it is.** The proxy lines are the
   load-bearing input to grid placement and they are not the specified
   regression. Grid placement is the only thing they are permitted to support.
