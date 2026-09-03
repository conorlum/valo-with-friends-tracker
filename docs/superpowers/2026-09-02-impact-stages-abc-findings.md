# Impact evaluation, Stages 0/A/B/C — findings and provenance

**For external review.** Written 2026-09-02 for Sol. Every number below was
produced by the two committed CLIs on this machine on 2026-09-02; nothing is
quoted from an earlier session or from the specs.

> **Revision 2, after Sol's peer review.** All ten original findings were
> upheld. Several of my *own* conclusions did not survive and are corrected in
> place, each marked where it appears:
>
> - **Stage C is quarantined in full**, not just P1/P2 — P3, Verdict C, target
>   agreement and stability run through the same broken recovery.
> - **The "econ is an artifact" argument was wrong** as stated. Replaced with a
>   stronger measured result: the negative sign is specification-dependent and
>   *migrates to `time_impact`* when the economy controls are added. This is
>   **finding 11**, and it is arguably the most consequential thing in this
>   document, because Stage A's sign diagnostics turn out to run on a different
>   model from the ladder and the weight search they are quoted beside.
> - **"Statistically tied" replaced with "no detectable difference"**, and the
>   yardstick summary tightened — `fitted_T1` does *not* beat the baseline on its
>   own first-half yardstick.
> - **Impact-vs-ACS is now a paired test**, which it should have been from the
>   start. The gap survives it.
> - **The `death_impact` result is demoted to a hypothesis** with four caveats.
> - Findings **11–17** added.
>
> Where I checked a review point and did not adopt it, that is stated with the
> source — see the note under finding 15 on Stage A's WPA being cross-fit.

- Branch `impact-win-correlation`, HEAD `13dbdd8`
- Specs: `docs/superpowers/specs/2026-09-01-impact-win-correlation-design.md`
  (Stages 0/A/B) and `docs/superpowers/specs/2026-09-02-impact-kill-order-graph-refit-design.md`
  (Stage C)

## How to reproduce

```
cd webapp
docker compose -p valomaths-private up -d          # Postgres on :5433
.\.venv\Scripts\python.exe scripts\evaluate_impact.py --stable-folds --sensitivity --out stage_ab.json
.\.venv\Scripts\python.exe scripts\evaluate_kill_order.py --out stage_c.json
```

Stage A/B takes ~8 min, Stage C ~10 min; both are read-only replays of every
match. `--stable-folds` is required on the Stage A run — without it the two
stages draw different folds and cannot share a matrix.

**Dataset**, counted directly from the DB at run time:

| matches | rounds | kill events | impact rows | players |
|---|---|---|---|---|
| 1,151 | 24,157 | 178,242 | 241,570 | 8,251 |

**Run identity**, printed by both tools and matching exactly:

| | value |
|---|---|
| `dataset_fingerprint` | `1151:9bbcb55f16ad4ede` |
| `fold_mapping_hash` | `843ed875ab3ce079` |
| fold function | `stable_folds` |
| Stage C `calculation_version` | `1/1` |

**Tests:** 261 passed across the twelve Stage 0/A/B/C test files
(`test_stats_math`, `test_impact_eval`, `test_stage0_cohorts`,
`test_kill_order_*`, `test_win_probability`, `test_impact_reconstruction`,
`test_impact_exante_swing`).

---

## Part 1 — What the numbers say

### Stage 0: the shipped Impact score does track winning, and clearly beats ACS

Descriptive, current stored scores (the `realized` swing variant — i.e. Impact
exactly as the live scorer writes it). Source: `impact_stage0.stage0_report`.

| measure | Impact | straight ACS |
|---|---|---|
| pooled point-biserial (n=11,360, 8,154 players) | **0.3710** CI [0.3574, 0.3855] | 0.2394 CI [0.2284, 0.2501] |
| mean in wins vs losses | 250.47 / 164.62 | 231.52 / 197.03 |
| within-player centred, recurrent cohort (n=3,635, 429 players) | **0.3863** CI [0.3498, 0.4015] | 0.2532 CI [0.2190, 0.2750] |
| per-player correlation, median (70 players, ≥10 matches) | **0.4221** CI [0.3748, 0.4768] | 0.2542 CI [0.2049, 0.3330] |
| …fraction of players positive | **100%** | 94.3% |
| within-player tercile win-rate lift (80 players) | **+0.4560** CI [0.3999, 0.4847] | +0.2887 CI [0.2429, 0.3238] |

The tracked-roster cohort (11 players, 767 player-matches) is stronger still:
pooled 0.4137 CI [0.3676, 0.4516], within-player 0.4575 CI [0.4114, 0.4979].

Read plainly: **on this data, when you play a top-third game by your own
standard, you win 70.3% of the time; a bottom-third game wins 24.7%.**

**The Impact-vs-ACS comparison is now a paired test, not two marginal intervals.**
The first version of this document argued from non-overlapping CIs, which is not
the direct hypothesis test available here — Impact and ACS are measured on the
same player-matches, so the difference can be bootstrapped directly. Run on all
11,360 paired player-matches, clustered by match, 2,000 draws, with cohort
eligibility **frozen from the original sample by distinct match id**
(`docs/superpowers/diagnostics/paired_impact_vs_acs.py`):

| statistic | Impact − ACS | 95% CI |
|---|---|---|
| pooled point-biserial | **+0.1316** | [+0.1266, +0.1367] |
| within-player centred | **+0.1331** | [+0.1209, +0.1418] |

The gap survives the proper test comfortably. Note this also re-derives the
within-player statistic under frozen distinct-match eligibility, so it is not
exposed to the bootstrap defect in finding 3 below — the point estimates are
unchanged (Impact 0.3863, ACS 0.2532).

**One framing caveat that applies to this whole section.** Stage 0's score and
the match outcome come from the same match, and Impact is built from kills and
deaths that are mechanically related to winning. These results show Impact
*describes* same-match winning better than ACS does. They do not show it predicts
future wins, and they do not measure causal player contribution. The
forward-looking question is Stage A's, and its answer is more equivocal.

### The components carry real information beyond "who won the round"

Stage A's T2 control ladder, on the frozen target (k=3, γ=0.7, match_weight=1.0),
five outer folds, L2 selected inside each training half. Source:
`evaluate_impact._control_ladder`.

| rung | weighted log loss |
|---|---|
| 1 — round result alone | 0.68138 |
| 2 — + score/side/economy context | 0.67140 |
| 3 — + damage differential | 0.67133 |
| 4 — + full ex-ante components | **0.66783** |

**Headline 3 → 4: −0.003501, CI [−0.004202, −0.002756].** The interval excludes
zero, so the econ/time/swing machinery predicts future rounds better than
knowing who won round N, what the teams could afford, and how much damage they
did. This is the single number the parent spec nominated as the point of the
exercise, and it comes out positive.

Stage C's own ladder repeats it with the 26 kill-order leverage columns in place
of the three components, adding two extra control rungs (final alive
differential, total kills): **rung 4 → 5, −0.002513, CI [−0.003213, −0.001748]**.
The extra controls themselves added nothing measurable (−0.0000618, CI
[−0.000220, +0.0000625]), which is the informative result there.

### But the three targets fit three different, mutually contradictory weightings

Constrained `FACTOR_WEIGHTS` fitted per outer fold with nuisance controls
present. Median across folds; all 5 folds usable for every target. Source:
`evaluate_impact._weights_summary`.

| target | damage multiplier | econ | time | swing |
|---|---|---|---|---|
| **T1** (first half → match) | 0.0 (0–0.25) | **0.0** | **2.4** (2.4–2.7) | 0.6 (0.3–0.6) |
| **T2** (round N → N+1..N+3) | 0.0 | **0.0** | **0.0** | **3.0** (all folds) |
| **WPA** (Stage B leverage) | 0.25 | **1.5** (1.2–1.5) | 1.5 (1.5–1.8) | **0.0** |

T1 wants time, T2 wants swing and nothing else, WPA wants econ and time and
zero swing. This is not fold noise — the per-fold ranges are tight and the
disagreement is total. Per the spec's own convention it is reported as a
finding, not resolved by picking a favourite.

### Follow-up: the disagreement is about aggregation, not about match-versus-round

Added after the review, to test the obvious hypothesis — that T1 and T2 disagree
because one predicts the match and the other predicts rounds. **They do not.**

`match_primary_target` (T3) makes the match/round balance an explicit constant
share and sweeps it. Fitted per outer fold, scored on the fixed yardsticks
(`scripts/sweep_match_share.py`):

| match share | econ | time | swing | damage × | forward-rounds gap over `kill_diff` |
|---|---|---|---|---|---|
| 0.00 (rounds only) | 0.0 | 0.0 | **3.0** | 0.00 | +0.01025 [+0.00485, +0.01579] |
| 0.31 (where T2 nominally sits) | 0.0 | 0.0 | **3.0** | 0.00 | +0.01025 [+0.00485, +0.01579] |
| 0.50 | 0.0 | 0.0 | **3.0** | 0.00 | +0.01025 [+0.00485, +0.01579] |
| 0.67 (frozen primary) | 0.0 | 0.0 | **3.0** | 0.25 | +0.01068 [+0.00544, +0.01609] |
| 0.95 (almost pure match) | 0.3 | 0.0 | 2.7 | 0.50 | +0.00941 [+0.00498, +0.01398] |

**The weighting is invariant to the balance.** Going from a target that is 0%
about the match to one that is 95% about the match does not move it off
swing-only. If the T1/T2 split were caused by match-versus-round, this sweep
would have walked the weights from T2's answer to T1's. It does not move at all.

Controls are not the cause either — tested directly, T2 and T3 return swing-only
with the five controls *and* with T1's empty control set.

What is left is **aggregation level**, and it isolates cleanly:

| target | outcome | unit | fitted weighting |
|---|---|---|---|
| T3 at share 0.95 | ~95% match outcome | **one row per round** | econ 0, time 0, **swing 2.7**, dmg 0.50 |
| T1 | 100% match outcome | **one row per match**, components summed over rounds 1–12 | econ 0, **time 2.4**, swing 0.6, dmg 0.25 |

Same outcome being predicted, different unit of observation, opposite answers.
**Per round, `swing_impact` carries everything; aggregated over a half,
`time_impact` does.** That is a much more specific statement than "the targets
disagree", and it points somewhere concrete: per-round `time_impact` is
presumably too noisy to help until it is summed, while `swing_impact` is
informative round by round.

It also matters for the product, because Impact is **built per round and consumed
as an average**. The two fits correspond to those two things, and they want
different weights.

Full yardstick comparison of the three fitted weightings on identical folds:

| candidate | first half → match | full match | forward rounds |
|---|---|---|---|
| `current_impact` | −0.00166 [−0.00708, +0.00377] | +0.00318 [−0.00064, +0.00683] | +0.00122 [−0.00197, +0.00415] |
| `fitted_T1` | +0.00077 [−0.00610, +0.00756] | **+0.00831 [+0.00339, +0.01305]** | +0.00101 [−0.00327, +0.00552] |
| `fitted_T2` | −0.00814 [−0.01719, +0.00108] | −0.01753 [−0.02526, −0.01050] | **+0.01025 [+0.00451, +0.01604]** |
| `fitted_T3` (match-primary) | −0.00818 [−0.01692, +0.00067] | −0.01565 [−0.02298, −0.00895] | **+0.01068 [+0.00504, +0.01625]** |

`fitted_T3` tracks `fitted_T2` almost exactly, which is the same result stated a
third way: **a match-primary target scored per round still yields the
round-predicting weighting.** Only the aggregate fit improves match prediction,
and `fitted_T1` remains the only weighting that beats `kill_diff` on a
match-level yardstick with an interval excluding zero.

Caveats carried from findings 13 and 15: these are still the coarse 0.3-increment
grid with in-sample L2 selection, and the intervals resample fixed predictions
rather than refitting. The *invariance* is robust to all of that — it is the same
answer eleven times — but the specific weights are not precise estimates.

### Screening test: the seven never-fitted columns add nothing

`impact_scores` persists more than the four columns Stage A fits. These have
never been in `FEATURE_COMPONENTS` and were never tested:

| column | non-zero rows | relationship to the fitted four |
|---|---|---|
| `clutch_kill` / `clutch_death` | 68,734 / 91,997 | raw `kill_order_bonus` on clutch kills — **not a subset of any fitted component** |
| `post_plant_kill` / `post_plant_death` | 39,601 / 54,671 | a strict **partition of `time_impact`** |
| `econ_kill` / `econ_death` | 67,405 | a strict **partition of `econ_impact`** |
| `traded_teammate` / `traded_by_teammate` | 40,608 / 51,587 | trade counts, both directions |

The partitions are interesting in principle: fitting `post_plant_kill`
separately lets post-plant timing carry a different weight from ordinary
timing — the same rank-one relaxation Family B was built to test, but reachable
without a graph refit or the `q/d` recovery that broke.

Tested as one extra control-ladder rung on the frozen T2 target, same stable
folds, paired match-clustered bootstrap, 2,000 draws. The stored columns are
legitimate here: the documented leakage lives in `_realized_econ_swing_factor`,
which feeds `swing_impact` only, and no swing-derived extra column is used.

| model | OOF weighted log loss |
|---|---|
| 4 components + 5 controls | 0.667831 |
| + all 8 never-fitted columns | 0.667871 |

**delta = +0.000040, CI [−0.000131, +0.000204] — no detectable difference**, and
an order of magnitude smaller than the four components' own contribution
(−0.003501). The extra columns are, if anything, very slightly worse.

**Reading: the finer decomposition carries no information the aggregate
components do not already have.** Clutch kills, post-plant kills and
econ-mismatch kills are not separately informative once the components that
contain them are in the model. This is an independent confirmation of the
direction Stage C's Family B pointed before it was quarantined — relaxing the
shared-curve constraint at this resolution buys nothing — obtained here without
any of the machinery that failed.

It also lowers the prior for adding *new* engineered components from the same
kill events, since the fine-grained structure already on disk turned out to be
redundant.

### The comparison the project never made: fitted versus SHIPPED

Every gap above is measured against `kill_diff`, which answers "is this better
than counting kills". **No number in this project answered the question an
adoption decision actually turns on — is any fitted weighting better than the
Impact score the site shows today?** That needs a paired comparison against
`current_impact`, and none existed. Computed now
(`scripts/compare_to_shipped.py`, paired cluster bootstrap of the AUC
difference, 2,000 draws, same folds, identical rows):

| candidate | first half → match | full match | forward rounds |
|---|---|---|---|
| **`fitted_T1`** | +0.00243 [−0.00205, +0.00706] | **+0.00513 [+0.00251, +0.00798]** | −0.00021 [−0.00332, +0.00322] |
| `fitted_T2` | −0.00648 [−0.01381, +0.00091] | −0.02070 [−0.02710, −0.01507] | **+0.00903 [+0.00437, +0.01389]** |
| `fitted_T3` | −0.00651 [−0.01354, +0.00043] | −0.01883 [−0.02468, −0.01365] | **+0.00947 [+0.00509, +0.01395]** |

Positive = ranks better than what ships today.

**`fitted_T1` is the only weighting that improves on the shipped score without
making anything else worse.** It beats it on full-match by an interval excluding
zero, and is statistically indistinguishable on the other two yardsticks — a
clean, if small, dominance. `fitted_T2` and `fitted_T3` trade: clearly better at
predicting the next rounds, clearly worse at describing the match just played.

That makes **econ 0, time 2.4, swing 0.6, damage ×0.25** the only weighting this
project has produced that is a defensible candidate for adoption. The gain is
modest — full-match AUC roughly 0.9834 → 0.9886 — and it comes with a large
change in the score's composition (drop econ entirely, more than double time,
cut swing, cut damage roughly fourfold), which would visibly move every displayed
number. Whether that trade is worth making is a product judgement, not a
statistical one, and findings 13 and 15 still apply to the precision of the
weights themselves.

### The econ sign is a property of the model specification, not of `econ_impact`

> **Revised after peer review.** The first version of this section argued that
> econ's negative sign was "an artifact" on the strength of a positive drop-one
> cost. That argument does not work — a negative suppressor can legitimately
> improve prediction, so a positive drop-one cost is compatible with a genuinely
> negative conditional coefficient. The review was right to reject it. What
> follows is a different and stronger line of evidence, measured afterwards.

`econ_impact`'s unconstrained partial coefficient is **negative in 5/5 folds on
T1 and 5/5 on T2** (WPA is mixed: 4 positive, 1 negative). The refitting
bootstrap agrees: `sign_direction` for econ is **0.0** — negative in every
single resample — at `sign_stability` 1.0. It is stably negative, not unstable.

**But that is a model without controls, and it is not the model the rest of
Stage A uses.** `evaluate_impact.py` passes `FEATURE_COMPONENTS` alone to both
`cross_validate` and `coefficient_diagnostics`, while the control ladder, the
constrained weight search and all of Stage C run *with* the five nuisance
controls. Adding them reverses the finding
(`docs/superpowers/diagnostics/econ_sign_specification.py`, out-of-fold over the
same 22,660 rows):

| component | coefficient without controls | negative folds | coefficient with controls | negative folds |
|---|---|---|---|---|
| `econ_impact` | **−0.00025071** | **5/5** | **+0.00010471** | **0/5** |
| `time_impact` | +0.00010768 | 0/5 | **−0.00024333** | **5/5** |
| `damage` | +0.00007715 | 0/5 | −0.00000247 | 3/5 |
| `swing_impact` | +0.00031816 | 0/5 | +0.00021692 | 0/5 |

The negative sign does not belong to `econ_impact`. It **migrates to
`time_impact`** when the economy controls are added, and `damage` becomes
unstable at the same time. No component holds a stable sign across two
defensible specifications.

Which control does it is identifiable, and the mechanism is clean:

| model | econ | time | damage |
|---|---|---|---|
| components only | −0.00024839 | +0.00010011 | +0.00006708 |
| + `round_result` | −0.00024755 | −0.00011341 | +0.00005508 |
| + `attacking_is_team_a` | −0.00025562 | +0.00011517 | +0.00004681 |
| + `score_diff_before` | −0.00007492 | +0.00007176 | +0.00005599 |
| + `loadout_diff` | −0.00000324 | −0.00009952 | +0.00001756 |
| + `full_buy_count_diff` | **+0.00006788** | −0.00012016 | −0.00000474 |
| + all five | +0.00010538 | −0.00025240 | −0.00001546 |

It is the **economy** controls that do it, and `econ_impact` is the one component
that correlates *negatively* with them — −0.110 against `loadout_diff` and −0.175
against `full_buy_count_diff`, where `damage`, `time` and `swing` all sit between
+0.15 and +0.31. That matches the Stage C spec's own measurement that the econ
factor is largest when you are the underdog.

So the readable mechanism is: **in a model with no economy controls,
`econ_impact` partly proxies “we are the poorer team”, which predicts losing.
Add the actual economy state and that job is taken over by the controls, leaving
`econ_impact`'s own contribution positive.** It is an omitted-variable story with
a direct test, not an unexplained collapse — and not a claim that economy is
anti-predictive.

Two consequences worth carrying forward. **Stage A's headline collinearity
diagnostic is computed on a different model from everything it is quoted
alongside** — that is worth fixing regardless of which sign one believes. And
**Verdict B item 5 (`econ_negative_every_fold`) is fed from the no-controls
result**; under the with-controls model it is false in 5/5 folds, so that verdict
item would flip.

The four columns are collinear by construction (`impact.py:496-502` builds three
of them as `kill_order_bonus × factor`). Measured pairwise correlations on the
ex-ante T2 design:

| | damage | econ | time | swing |
|---|---|---|---|---|
| damage | 1.000 | 0.842 | 0.895 | 0.733 |
| econ | | 1.000 | 0.873 | 0.727 |
| time | | | 1.000 | 0.792 |
| swing | | | | 1.000 |

Note `damage` contains no kill-order bonus at all and still sits at 0.733–0.895,
so a floor of shared variance exists that no reweighting can remove.

Separately, **Stage B's `econ_increment`** — raw economy state added to a clean
win-probability model untouched by the Impact formula — comes in at **+0.002927,
CI [+0.001772, +0.004082]**. That establishes economy carries predictive signal.
It does *not* by itself validate the multiplicative `econ_impact` construction,
and the first version of this document over-read it as if it did.

Drop-one costs, which move under the same specification change:

| dropped | cost without controls | cost with controls |
|---|---|---|
| `swing_impact` | **+0.008366** | **+0.003276** |
| `econ_impact` | +0.001014 | +0.000014 |
| `time_impact` | +0.000084 | +0.000246 |
| `damage` | +0.000006 | **−0.000042** |

`swing_impact` is the only column carrying substantial weight under either
specification. **`damage` costs nothing to drop without controls and actively
*helps* to drop with them** — so it is not an identified predictor in the
specification Stage C actually fits. That, not the drop-one argument, is what
connects to Stage C's failure below.

### Against the plain baselines, each fitted weighting wins only on its own turf

Targets × yardsticks, ex-ante components, all out-of-fold, gaps are paired AUC
against `kill_diff` on identical rows. Source: `impact_eval.yardstick_matrix`.

| candidate | first half → match (n=1,114) | full match (n=1,136) | round N → N+2.. (n=16,576) |
|---|---|---|---|
| `current_impact` | −0.00166 [−0.00769, +0.00388] | +0.00318 [−0.00151, +0.00680] | +0.00122 [−0.00171, +0.00420] |
| `damage_only` | −0.01556 [−0.02460, −0.00630] | −0.01625 [−0.02263, −0.01111] | −0.00497 [−0.00837, −0.00108] |
| `acs` | −0.00965 [−0.01449, −0.00513] | −0.01095 [−0.01458, −0.00729] | −0.00134 [−0.00328, +0.00064] |
| `fitted_T1` | +0.00077 [−0.00597, +0.00725] | **+0.00831 [+0.00350, +0.01259]** | +0.00101 [−0.00348, +0.00488] |
| `fitted_T2` | −0.00814 [−0.01822, +0.00155] | −0.01753 [−0.02533, −0.01098] | **+0.01025 [+0.00466, +0.01579]** |
| `fitted_WPA` | −0.00711 [−0.01781, +0.00100] | **+0.00668 [+0.00235, +0.01043]** | −0.01190 [−0.01576, −0.00786] |

Three things worth stating:

1. **Shipped Impact shows no detectable difference from plain kill differential
   on any yardstick.** The earlier phrasing here was "statistically tied", which
   is wrong: an interval spanning zero supports "no difference detected", not
   equivalence. Demonstrating equivalence would need a predeclared margin and an
   equivalence test — the machinery Stage C already has for its own item 2 and
   which is not applied here. What *is* established is that Impact beats
   damage-only and beats ACS by intervals excluding zero.
2. **`fitted_T2` does beat kill differential on forward rounds** (+0.01025, CI
   excludes zero) — the one place a fitted weighting clears the baseline on a
   genuinely predictive yardstick. It pays for it by being *worse* than the
   baseline on full-match (−0.01753).
3. The looser summary this section previously carried — "each fitted weighting
   wins on the yardstick nearest its own target" — does not survive the table.
   **`fitted_T1` does not beat the baseline on its own first-half yardstick**
   (+0.00077, interval spanning zero); it wins only on full-match.
   **`fitted_WPA` does not beat it on first-half either.** And full-match
   discrimination is retrospective — the features contain the match's own combat
   events, so an AUC of 0.98 there is mechanical rather than impressive, which is
   why the spec says to read that column only as a gap over `kill_diff`. The
   defensible statement is narrower: **`fitted_T2` is the only weighting that
   beats the baseline on a genuinely forward-looking yardstick, and it is clearly
   worse than the baseline on the retrospective one.**

### Stage C0: the hand-tuned kill-order graph was already about right

Before any fitting. Source: `kill_order_refit.stage_c0_report`, all 23,955
non-surrender rounds.

Regressing the shipped 25-parameter table on the data's own round-win swing
`dP = P(win | own, opp−1) − P(win | own, opp)`, exposure-weighted by crossings:

```
shipped  ≈  48.9  +  482.4 × dP        exposure-weighted R² = 0.9727
```

So the hand-picked numbers are, to within ~3%, a flat ~49-point per-kill
constant plus ~482× the empirical swing. That is a strong result for a table
that was set by intuition.

Swapping the shipped graph for the pure swing curve barely moves the metric:

| | value |
|---|---|
| round-level Impact differential, Pearson | 0.99623 CI [0.99617, 0.99630] |
| round-level, Spearman | 0.99331 |
| sd(reference) / sd(difference) | 1,526.98 / 132.50 → **8.68%** |
| rounds where the differential flips sign | **0.4467%** (107 of 23,955) |
| player-match average Impact, Pearson | 0.99803 (n=11,510) |

Conditioning, on the full data and the real per-kill multiplicands (26 columns):
max pairwise \|r\| **0.7605**, condition number **154.5**, effective rank
**15.26**. As the spec predicted: exposure is fine (rarest parameter crossed
1,446 times), conditioning is the constraint — roughly fifteen of twenty-five
directions are estimable and ten are not.

### Stage C's verdicts: all four "not helped" — but not for the reason they look like

| verdict | question | helped |
|---|---|---|
| A1 | does a refit graph predict future rounds better? | **False** |
| A2 | does first-half Impact predict the match better? | **False** |
| B | did we explain the econ collapse? | **False** |
| C | do the components want different state curves? | **False** |

Verdict B's three items all tripped as predicted: targets still disagree, max
component correlation stays at **0.8746** (threshold was 0.70), econ still
negative under the model the verdict reads (see the specification caveat above —
that input flips with controls present).

> **Revised after peer review: every fitted Stage C result is quarantined, not
> just P1 and P2.** The first version of this document treated Verdict C as
> having "tripped for a real reason". It has not. **P3 runs through the same
> `q/d` recovery and the same unconstrained Platt calibration as P1 and P2, and
> both of its arms are non-deployable** — `stage_a_exact` has `d` in
> [−0.00005, +0.00001] across folds and `component_tilt` in [−0.00008, −0.00002].
> A calibrated-loss comparison between two sign-inverted scores cannot test
> whether a valid component-specific curve would help. The same contamination
> reaches **target agreement** (its T2 graph comes from the invalid recovery, and
> display normalization does not repair that) and **graph stability** (the
> stability of a non-deployable graph says nothing about a deployable one).
>
> So P1, P2, P3, P4, Verdict C, Verdict A1, the graph target-agreement check and
> the stability block are **all uninterpretable until the recovery is repaired
> and Stage C is re-run.** For the record the raw numbers were: P3 +0.001840, CI
> [+0.001440, +0.002282], favouring `stage_a_exact`; B2 0.680773 and B3 0.681118
> against B0's 0.678933. None of them currently mean anything.

**Verdict A1 is where the failure is visible, and this is the part that most
needs review.** Both co-primaries reported a held-out improvement whose interval
excludes zero:

| | comparison | delta | CI | favours |
|---|---|---|---|---|
| P1 | `swing_basis` vs `current_graph` | −0.002120 | [−0.003074, −0.001298] | swing_basis |
| P2 | `pooled` vs `current_graph` | −0.001142 | [−0.002134, −0.000248] | pooled |
| P4 | `swing_basis` vs `pooled` | −0.000978 | [−0.001437, −0.000544] | swing_basis |

…and neither cleared, because **every fitted candidate is non-deployable**. That
is the headline Stage C result and it is a mechanical failure, not a scientific
one. See Part 2, issue 1.

### The player-level read is the most interesting product finding here

Source: `kill_order_refit.player_level_report`, 80 players with ≥9 matches,
11,360 player-matches, shipped graph.

| half | point-biserial with winning | within-player tercile lift |
|---|---|---|
| `impact` (combined) | +0.3776 | +0.4704 CI [+0.4117, +0.5045] |
| `kill_impact` | +0.2725 | +0.3237 CI [+0.2768, +0.3634] |
| `death_impact` | **−0.5665** | **−0.6685** CI [−0.6853, −0.5959] |

`death_impact` is a cost, so the negative sign is the expected direction, and its
magnitude is more than double `kill_impact`'s on both measures.

**This is a hypothesis, not a product recommendation, and the first version of
this document pitched it too strongly.** Four caveats, three of them raised in
peer review and all of them checked:

- **The comparison is not like-for-like.** `kill_impact` is
  `damage + (weighted kill factors)`, while `death_impact` is the death cost
  alone (`impact.py`'s formula). Comparing their correlations compares a column
  that contains damage against one that does not.
- **Death frequency is mechanically coupled to losing.** Losing an elimination
  round often means most of the team died. Some of this correlation is the
  definition of the combat outcome, not a player quality.
- **The earlier "on a third of the spread" remark was wrong** and has been
  removed. Correlation is scale-invariant, so `death_impact`'s smaller standard
  deviation (25.6 against 110.9) makes its correlation neither more nor less
  surprising.
- **This block uses ex-ante components under the shipped graph**
  (`kill_order_leverage.py:451`, `use_realized_swing=False`), not the stored
  realized Impact that Stage 0 reports — so it is not directly comparable to the
  Stage 0 numbers above.

Before anything changes in the product, `death_impact` needs benchmarking against
raw deaths per round, survival rate, and deaths conditional on round state and
result. Only if it beats those does the constructed column earn its place.

The trade discount is separately large:

| | mean per player-match |
|---|---|
| death cost as scored | 94.13 |
| death cost with no trade credit | 115.22 |
| **discount `_traded_factor` forgave** | **21.09** (18.3%) |

That discount depends on whether the player's *team* traded for them — a team
quality currently credited to an individual. The 18.3% shows the magnitude of the
credit, not whether it is correctly attributed; testing that means checking
whether trade-adjusted death cost beats unadjusted death cost out of fold.

---

## Part 2 — Issues found, ranked. These are the retest candidates.

### 1. Every fitted Stage C candidate is sign-inverted, because `d` goes negative

**Severity: blocks the entire Stage C result.**

The recovery is `b_k = q_k / d`, where `d` is the fitted damage coefficient.
Measured `d`, all five folds, every fitted candidate:

| candidate | `d` per fold | deployable |
|---|---|---|
| `swing_affine` | −0.00023 … −0.00026 | ✗ ×5 |
| `swing_basis` (P1) | −0.00021 … −0.00027 | ✗ ×5 |
| `pooled` (P2) | −0.00014 … −0.00020 | ✗ ×5 |
| `free` | −0.00020 … −0.00026 | ✗ ×5 |
| `stage_a_exact` (B0) | −0.00005 … +0.00001 | ✗ ×5 |
| `component_tilt` (P3's candidate) | −0.00008 … −0.00002 | ✗ ×5 |

Only `current_graph` and `swing_plugin` are deployable, and only because their
`d` is pinned at 1.0 by construction rather than fitted.

**I diagnosed the cause numerically** (`docs/superpowers/diagnostics/why_d_negative.py`, nested
designs on the same frozen T2 target and the same 22,660 rows — only the columns
change):

```
corr(damage_diff, shipped-graph leverage aggregate)  = 0.8691

controls + damage                              d = +0.0000817   POSITIVE
controls + damage + shipped leverage (1 col)   d = -0.0001573   NEGATIVE
controls + damage + 26 free leverage cols      d = -0.0002712   NEGATIVE
controls + damage + 25 lattice cols            d = -0.0002772   NEGATIVE
```

A **single** leverage column is enough to flip it. This is the same
multicollinearity sign-flip Stage A already documented for `econ_impact` — and
Stage A's drop-one table above already showed `damage` contributes nothing once
the components are present (cost +0.000018, CI spanning zero). A column that
adds nothing has an unidentified partial coefficient, and Stage C put exactly
that coefficient in the denominator of the recovery.

Note the `+0.0000817` in the first row reproduces Stage A's own T2 damage
coefficients (+0.00003 to +0.00009 across folds) — so the two stages agree, and
the flip is caused purely by adding the leverage columns.

**For Sol:** the design assumes `d` is a well-identified positive scale, and the
data guarantees it is not. The drop-one table sharpens this beyond what the first
version said: with the controls present, `damage` costs **−0.000042** to drop —
removing it *improves* held-out prediction. `d` is not merely weakly identified,
it has no established positive sign in the specification Stage C actually fits,
and it was used as a denominator.

**Merely constraining `d > 0` while still computing `q/d` is not a fix** — it
pushes `d` toward zero and explodes the graph, the same failure mode as the
`offset` and unmasked-ridge attempts the spec already documents. The safer repair
is to fit the deployable score directly: predeclare a positive damage scale, or
search a constrained positive blend, and require non-negative graph prices.

**There is precedent in the codebase.** Stage A's `fit_constrained_weights`
already guards against exactly this (`impact_eval.py:924-929`): it rejects any
candidate whose composite slope is `<= 0`, reasoning that such a weighting
"predicts well by saying *more Impact, more likely to LOSE*", and returns an
unusable result rather than publishing it. Stage C's fitter has no equivalent
guard. Porting that constraint is the most direct repair available.

**Everything downstream needs re-running afterwards** — P1–P4, Verdict A1,
Verdict C, target agreement and stability. None of them currently measures a
deployable graph.

### 2. Platt calibration hides the inversion from the primary comparisons

**Severity: high — it is why P1/P2 look like wins.**

`paired_delta` scores `result.oof_probabilities`. Those come from
`platt_calibrate` fitted in-fold and applied to the recovered candidate score
`S_r`. A Platt fit with a *negative* slope maps a sign-inverted score onto
correct probabilities, so the log loss improves while the score itself is
anti-correlated with winning.

The yardstick matrix, which reads raw ordering, exposes it plainly:

| candidate | first half AUC | full match AUC | forward rounds AUC |
|---|---|---|---|
| `current_graph` | 0.8451 | 0.9834 | 0.5671 |
| `swing_plugin` | 0.8457 | 0.9862 | 0.5659 |
| `swing_affine` | **0.1650** | **0.0306** | **0.4311** |
| `swing_basis` | **0.1669** | **0.0361** | **0.4308** |
| `pooled` | **0.1695** | **0.0446** | **0.4307** |
| `free` | **0.1725** | **0.0511** | **0.4313** |

AUC 0.03 is a near-perfect inversion. The spec's rule 2 ("every yardstick scores
the recovered candidate") is honoured — `scores = score_rounds(test_leverage,
test_damage, graph)` with the recovered `graph`. The gap is that the *primary
comparison* is calibrated and the *yardstick* is not, so they disagree
completely and only the deployability flag caught it.

**For Sol:** should `paired_delta` refuse a candidate whose uncalibrated AUC is
below 0.5, or report both? As written, a report that printed P1/P2 without the
deployability line would claim a significant improvement from a graph that is
the negative of a working one.

### 3. Stage 0's top-level within-player CI excludes its own point estimate

**Severity: medium — a published interval is wrong.**

| block | point estimate | bootstrap 95% CI | contains? |
|---|---|---|---|
| `stage0.within_player_centered` (Impact) | 0.38629 | [0.22761, 0.25989] | **no** |
| `stage0_acs.within_player_centered` (ACS) | 0.25318 | [0.14427, 0.17893] | **no** |
| `stage0.cohorts.recurrent.within_player_centered` | 0.38629 | [0.34982, 0.40146] | yes |

The point estimates are correct (identical to the cohort's, since
`within_player_centered` filters to `min_matches=2` internally). Only the
top-level intervals are wrong, and the cohort block is fine.

**Mechanism, reproduced on synthetic data with a known effect**
(`docs/superpowers/diagnostics/within_player_ci_bias.py`): `_ci` resamples whole matches with replacement and
recomputes eligibility inside each draw — correct in principle, and the spec
explicitly asks for it. But 94.7% of players have exactly one match, and when
that single match is drawn twice they become "eligible" with two *identical*
rows, which centre to exactly 0.0 and carry no signal. In one resample of the
synthetic top-level set:

```
eligible players in the resample : 2410   (true eligible: 429)
  all rows from ONE match        : 2000   (83.0%)
rows contributed by those        : 4836 of 8133 (59.5%) -- all exactly 0.0
```

Sixty percent of the resampled rows are zero-variance filler, so the correlation
is dragged toward zero in every draw. The cohort block escapes because
single-match players are filtered out *before* the bootstrap.

**The defect is broader than the one broken interval.** Eligibility is a row
count everywhere it appears, so every player-level bootstrap in the project is
exposed — checked, and confirmed:

| block | eligibility test | exposed |
|---|---|---|
| `stage0.within_player_centered` | `len(player_rows) < min_matches` (`impact_stage0.py:71`) | yes — visibly broken |
| `stage0.within_player_terciles` | same helper | yes |
| `stage0.per_player_correlations` median CI | `len(player_rows) < threshold` | yes |
| Stage C `player_level` tercile CIs | `len(mine) < min_matches` (`kill_order_refit.py:1097`) | yes |

The last one matters most, because Stage C's player-level terciles are where the
`death_impact` result's intervals come from.

**For Sol:** eligibility should be frozen from the original dataset using
**distinct match ids**, before any resampling. I did that for the paired
Impact-vs-ACS test in Part 1 and the point estimates were unchanged, which
suggests the repair moves intervals rather than headlines — but that needs
checking per block rather than assumed.

One further point I agree with and cannot fix by freezing eligibility alone:
these data have **crossed dependence**. Teammates share a match, and recurrent
players recur across matches. A match-clustered bootstrap handles the first and
not the second. For a statistic whose unit is the player — the per-player median
correlation especially — the resampling unit arguably ought to be the player.

### 4. The stability gate consumes a statistic labelled "must not gate"

**Severity: medium — latent, masked in this run.**

`stability_report`'s docstring: *"Without `refit` this returns a DESCRIPTIVE
fold-resampling figure and sets `gate_eligible=False`, and the verdict must not
consume it."* Every entry in this run carries `gate_eligible: false` and
`"rule": "fold-resampling only -- DESCRIPTIVE, must not gate a success claim"`,
because `build_full_report` calls it at `kill_order_refit.py:1895` without
`refit=`.

`verdict_report` then does exactly what the docstring forbids
(`kill_order_refit.py:1256`):

```python
if not stability.get(candidate, {}).get("stable", False):
    notes["A1"].append(f"{name}: {candidate} did not pass the stability criterion")
```

No `gate_eligible` check. It did not bite here only because the deployability
check runs first and `continue`s. Fix the `d` problem and this becomes live: P1
and P2 would be gated on a five-overlapping-fold dispersion figure that the
module itself says cannot support the interval.

### 5. Stage C0's printed reading is hardcoded and contradicts the measurements

**Severity: low, but it is the first thing a reader sees.**

The string at `kill_order_refit.py:981` is a constant:

> "A correlation above 0.99 with **no sign flips** means **no downstream
> yardstick difference was ever possible** — but correlation alone is NOT the
> practical-equivalence test; see the verdict checklist."

Measured in the same block: sign flips **0.4467%** (107 rounds), not zero. And
"no downstream yardstick difference was ever possible" is contradicted by P1 and
P2, whose intervals exclude zero, and by `swing_plugin` beating `kill_diff` on
full-match (+0.00597, CI [+0.00131, +0.00970]). The hedge in the second clause
is right; the first clause asserts two things the data denies.

### 6. The shared Stage A / Stage C matrix never actually happens

**Severity: medium — it is what commit `13dbdd8` was for.**

`yardstick_matrix.stage_a_joined` is `false`, with
`stage_a_refusal: ["no Stage A identity supplied"]`. The refusal is working as
designed, but the CLI never passes a Stage A identity, so the join is refused
unconditionally — even though I verified the two runs' `dataset_fingerprint` and
`fold_mapping_hash` are byte-identical. The whole point of adding `stable_folds`
was to make this join possible, and no invocation of the CLI can currently
produce it.

### 7. One predeclared sensitivity errored out

`sensitivities.fallback` returned:

```
ValueError: alignment produced 22163 rows for T2, but the parent builder produced 22017
```

This is the fallback-drop sensitivity — the check the spec promised in place of
the reviewer's recommended exclusion of the 497 fallback-affected rounds. It did
not run. The failure is reported rather than swallowed, which is right, but the
sensitivity is missing from the results.

Separately, `sensitivities.alternation` did produce output, and it is
contaminated by issue 1: both alternation graphs `b1` and `b2` are negative at
every parameter (`b1` ≈ −156 to −304, `b2` ≈ −807 to −2,836), with
`graph_rms_shift` 1,654.9. Nothing there is readable as a graph.

### 8. Two verdict inputs are defaults, not measurements

`build_full_report`'s signature carries `econ_negative_every_fold=True`, and the
CLI does not pass it — so verdict B item 5 uses the *prior Stage A finding* as a
constant rather than the value from the Stage A run sitting next to it. The
docstring is honest about this, and the `inputs.source` block records it. But
this run had a fresh Stage A report available (econ negative in 5/5 folds on both
T1 and T2 — the default happens to be right), and wiring it would remove the
one hardcoded verdict input.

Likewise `outer_weights_by_target` is omitted, so the outer-weight sensitivity is
silently skipped rather than reported as skipped.

### 9. Item 2's practical-equivalence test implements half the spec

The spec requires **both** parts: the paired held-out log-loss CI contained
within ±0.0008 **and** exposure-weighted RMS deviation under 1% of score sd.
`_practically_equivalent_stage_c0` (`kill_order_refit.py:1789`) implements only
the RMS half, and only for `swing_plugin` — not, as the spec requires,
recomputed for every fitted candidate at the end of C1/C2.

In this run it is non-binding (RMS share 8.68% ≫ 1%, so item 2 passes), but
that is worth its own look: with a threshold of 1% against a measured 8.68%,
item 2 will essentially always pass, while the correlation of 0.996 and the
0.45% sign-flip rate say the metric barely moved. The threshold may be
mis-calibrated for what the item is trying to catch.

### 10. P3 tests B2, the spec's table says G5

`PRIMARY_COMPARISONS` names `component_tilt` (B2, 9 coefficients). The spec's
predeclared table says "P3 | **G5** vs `stage_a_exact`", and G5 is defined as the
*symmetric* 18-coefficient model (B3). The second revision note then says "B2 vs
B0 [is] the primary test", which is what the code implements. So the code
follows the later decision and the spec's own table is stale — but the two
disagree in the document, and a reviewer reading the table would expect B3.

For what it is worth, both rungs lose to `stage_a_exact` here (B2 0.680773, B3
0.681118, vs B0 0.678933) — though per the quarantine above, neither comparison
is currently interpretable.

---

## Findings added after peer review

These came out of Sol's review. Each was checked against source before being
accepted.

### 11. Stage A's sign diagnostics are computed on a different model from everything else

**Severity: high — it changes the project's most-quoted finding.**

Covered in full in Part 1's econ section. In short: `evaluate_impact.py` passes
`FEATURE_COMPONENTS` alone to `cross_validate` and `coefficient_diagnostics`,
while the control ladder, `fit_constrained_weights` and all of Stage C run with
the five nuisance controls. The spec's own second revision note says the
constrained search runs "with nuisance controls present, so reported weights come
from the same model the control ladder validates" — but the *coefficients and
sign stability* reported alongside them do not. Adding the controls moves the
negative sign from `econ_impact` to `time_impact` and destabilizes `damage`.

Whichever specification is preferred, the diagnostics and the ladder should agree
on one, and the report should say which.

### 12. Platt calibration is fitted on in-sample scores

**Severity: medium.**

Inside each outer fold, `run_nested_cv` fits the candidate on the training rows,
scores it on **those same rows**, and fits the Platt calibrator from those scores
(`kill_order_refit.py:317-318`, where `in_fold` is `fit_family_a(..., train,
train_on_train, ...)`). The outer test set stays unseen, so this is not direct
test leakage — but it can make calibration optimistic or unstable, most for the
flexible graph models. Calibration should come from inner out-of-fold predictions
or a held-out calibration split. Requiring a **positive calibration slope** would
also have caught finding 1 independently, since Impact's contract is that higher
is better.

Stage A's `yardstick_matrix` has the same shape at `impact_eval.py:1335-1338`.

### 13. Uncertainty is understated: 200 draws, and no refitting

**Severity: medium.**

Both reproduction commands use the default `--draws 200`. For P1 and P2's 97.5%
interval each tail is 1.25% — the second or third most extreme draw of 200 — so a
seed change can move an endpoint materially. Final intervals want at least 5,000
draws with reported seed sensitivity.

More substantively, `paired_delta` and `oof_metrics` resample **fixed OOF
predictions** without refitting. They estimate test-sample variability
conditional on one fold assignment and one set of trained models, not total
training and selection uncertainty. Stage A's `coefficient_diagnostics` does use
a refitting bootstrap for sign stability, so the machinery exists — it just is
not what the headline intervals use. Repeated nested CV or a full refitting
bootstrap would close the gap.

Related: the five outer-fold training halves overlap heavily — any two share 3/5
of their matches — so the tight per-fold weight ranges in Part 1 are **not five
independent replications**, and should not be read as evidence of precision.

### 14. Verdict A2 ignores its own interval, and only looks at `current_graph`

**Severity: medium.**

`beats_kill_diff_t1` is a bare point check (`kill_order_refit.py:1939`):

```python
beats_kill_diff_t1 = bool((current_graph_cell.get("gap_over_kill_diff") or 0.0) > 0)
```

It reads the point estimate, ignores the confidence interval that the same cell
carries, and never looks at any fitted candidate — even though verdict A2 asks
"does first-half Impact predict the match better?" and the fitted candidates are
the ones that might. With the gap at −0.00161 the answer is False either way here,
but the test as written could return True on a point estimate whose interval
comfortably spans zero.

### 15. The constrained weight grid is coarse and the L2 is picked in-sample

**Severity: medium — it weakens the "targets disagree" claim.**

Verified in `impact_eval.py`:

- `_simplex_grid(0.1)` × `FACTOR_WEIGHT_TOTAL = 3.0` means reported weights move
  in **increments of 0.3**.
- `DEFAULT_DAMAGE_GRID = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]`.
- **T2's (econ 0, time 0, swing 3.0) is a simplex vertex** — a boundary
  preference under the tested grid, not a precise interior estimate.
- The L2 for the constrained search is chosen by `weighted_log_loss` on the
  **same rows it was fitted on** (`impact_eval.py:900-906`), not by inner
  validation.

The T1/T2/WPA disagreement is still real and still worth reporting — but
"mutually contradictory weights" overstates what a coarse boundary-hitting grid
with in-sample L2 selection can support. A finer grid, profile-loss curves, and
bootstrap uncertainty on the weights themselves would settle it. It is also worth
saying plainly that **different horizons legitimately may optimize different
weightings**; disagreement between a match-outcome target and a next-round target
is not automatically a defect.

*(One review point I checked and did not adopt: the claim that WPA's value model
is fitted on all observations applies to Stage C's `run_nested_cv`
(`kill_order_refit.py:275`), which feeds `target_agreement` only. **Stage A's WPA
weights — the ones in Part 1's table — are cross-fit per outer fold**, via
`_value_context` and `cross_validate`'s `context_builder`, which
`impact_eval.py:694-698` documents as being called on "each outer fold's TRAINING
observations only". So the Part 1 table is not affected; the Stage C
target-agreement input is.)*

### 16. The dataset fingerprint does not identify the dataset

**Severity: medium — it is a reproducibility claim this document makes.**

`dataset_fingerprint` hashes **only the eligible match ids**
(`impact_eval.py:333-337`). Round data, kill events, or impact rows could change
entirely while the fingerprint stays identical — so the matching fingerprints
this document opens with prove the two runs saw the same *match set*, not the
same *data*. Reproducibility needs a checksum over the actual analysis rows, or
an immutable snapshot.

### 18. The frozen T2 target is not one target — its match/round blend varies row by row

**Severity: high — it changes how every T2 result should be read.**

Found while building a match-primary target, not during the review. The whole
project rests on "targets are frozen, never selected" — but nobody checked that
the frozen target was *internally consistent*, and T2 is not.

`forward_window_target` blends future-round outcomes with the match outcome using
an **absolute** `match_weight`, not a share. The future-round weights sum to
`1 + 0.7 + 0.49 = 2.19` at the frozen `k=3, γ=0.7`, so `match_weight=1.0` gives
the match `1.0/3.19 = 31.35%` of the target — *when a full three-round window is
available*. Near a half boundary fewer future rounds exist, the denominator
shrinks, and the match share silently rises. Measured over all 22,660 T2 rows:

| per-row match share | rows | share of dataset |
|---|---|---|
| exactly 31.35% (full window) | 10,110 | 44.6% |
| **exactly 100% (no future rounds at all)** | **2,364** | **10.4%** |
| median across all rows | **45.66%** | — |
| range | **31.35% → 100%** | — |

So a tenth of T2's rows are pure match-outcome rows carrying no round signal, and
the median row is far more match-weighted than the nominal 31%. **T2 is a mixture
of two different prediction problems in unstated proportions**, varying with a
row's distance from a half boundary — which is exactly the "pooling folds that
chose different configurations would pool predictions of different quantities"
failure the spec's freezing rule exists to prevent, occurring *inside* a single
frozen target.

1,108 of those rows exist *only* because of the match term: with
`match_weight=0` the target has 21,552 rows, with `match_weight=1.0` it has
22,660.

This is worth knowing before interpreting anything T2-derived, including
`fitted_T2`'s swing-only weighting and its win on the forward-rounds yardstick.
The fix is to specify the blend as a constant share and derive the per-row weight
from it, which is what `match_primary_target` (T3) does — it reduces exactly to
T2 at `match_weight=0`, verified by an equality gate on all 21,552 rows.

### 17. Generalizability is narrower than random match folds suggest

**Severity: medium — a framing issue, not a bug.**

This is a convenience sample built around a tracked friend roster and whoever
they were matched against; 94.7% of players appear in exactly one match. Random
match folds therefore test **new matches from this same social and
data-collection ecosystem** — not new players, not future patches, not the
broader Valorant population. Useful sensitivities would be a temporal holdout, a
player- or roster-disjoint holdout, and stratification by map, side, rank and
patch period, plus exact counts for excluded ties, surrenders, truncated matches
and fallback/resurrection events.

---

## What I would ask a reviewer to focus on

Reordered after peer review. The work now splits cleanly into *repair before
re-running* and *open questions*.

**Repair first, then re-run all of Stage C:**

1. **Findings 1 + 12** — fix the recovery and the calibration together. Port
   Stage A's positive-slope rejection into Stage C's fitter, and fit calibration
   from inner out-of-fold scores with a positive-slope constraint. Until then
   nothing in Stage C's fitted half means anything.
2. **Finding 11** — decide which specification the coefficient diagnostics
   should use, and make the ladder, the weight search and the diagnostics agree.
   This is the one that changes a headline finding rather than a caveat.
3. **Findings 3 + 13** — freeze player eligibility on distinct match ids before
   resampling, raise the draw count, and decide whether the player-level
   statistics want a player-level resampling unit.

**Genuinely open questions:**

4. Whether the **T1/T2/WPA weight disagreement** survives a finer grid and honest
   uncertainty (finding 15), and whether it indicates a wrong parameterization
   rather than two targets that legitimately want different answers.
5. Whether **`death_impact`** beats raw deaths, survival rate, and
   state-conditioned deaths. If it does not, the interesting number is about
   dying, not about the constructed column.
6. Whether Stage C's headroom result — the shipped graph already sitting at
   R² 0.9727 against the empirical swing curve — means the whole refit is not
   worth repairing at this sample size. That is a legitimate reading of Part 1
   and it deserves an explicit decision rather than defaulting into more work.

## What is not in scope of these findings

- Nothing here was adopted. `impact.py`'s `FACTOR_WEIGHTS` and
  `_KILL_ORDER_GRAPH` are unchanged; the only edit ever made to the scorer was
  the calculation/persistence split (`build_impact_rows_for_match`).
- The 78-parameter per-component fit stays deferred: `deferral_check` reports
  1,151 matches against a 4,000 re-open threshold, `reachable: false`.
- `rounding_gap` confirms the two pipelines agree: mean absolute gap 0.873 impact
  points between the rounded stored `impact_diff` and the unrounded leverage
  pipeline, Pearson 0.9999997 over 23,955 rounds.
