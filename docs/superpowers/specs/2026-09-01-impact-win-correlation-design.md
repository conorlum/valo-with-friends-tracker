# Impact-vs-winning: evaluation tooling and fitted weights

**Status:** draft, awaiting user review
**Date:** 2026-09-01

## Purpose

Answer, with numbers rather than intuition, how much the custom Impact score
actually relates to winning -- and use that to tune it. The user's framing:
Impact is supposed to *be* impact, so the tooling exists to evaluate it "for
what it is."

Three questions were separated during brainstorming, and they do not all
belong in the same place:

- **"Does my performance predict my wins?"** -- personal, meaningful to a
  player. Goes on the **player page**.
- **"Is Impact better than K/D at predicting wins, and which of its parts are
  earning their keep?"** -- validates the metric itself. **Internal tooling**,
  not a page.
- **"Who on the roster carries?"** -- friend-group flavored. Goes on the
  **squad page**, not the stats page.

## Decomposition, and what THIS spec covers

| | Deliverable | Spec |
|---|---|---|
| P0 | `stats_math` -- pure numeric helpers | this one |
| P1 | Internal evaluation tooling + fitted weights | this one |
| P2 | Player page: impact-to-win-rate card + break-even tile | its own, later |
| P3 | Squad page: carry measure | its own, later |

**This spec covers P0 and P1 only.** P2 and P3 are deliberately not designed
yet: P1's report determines which metric variant and which framing are worth
displaying, and designing the pages first would be guessing. Each gets its own
spec once P1 has run.

P1 itself ships in two stages (see "Sequencing").

## Constraints and premises

### Data volume (local DB, 2026-09-01)

1,151 matches - 24,157 rounds - 178,242 kill events - 241,570 impact rows -
8,251 players.

Consequences: ~48k team-round observations is ample for fitting a handful of
weights, but **1,151 match outcomes is not enough to make match win the
primary training target**. Match outcome still enters training as a
*low-weight auxiliary* signal (see `forward_window_target` below) -- the user's
econ-carryover argument requires it -- but the bulk of the fitting signal comes
from the round-level observations. Match-level numbers carry the widest
confidence intervals. More matches from the tracker.gg crawl
sharpen exactly those numbers; the tool re-runs unchanged and prints `n` at
every level so growth is visible.

### The tautology trap

A round's kill events *are*, near-deterministically, that round's outcome.
Any model predicting "did we win round N" from round N's own kills scores
~99% and has learned nothing. **Training targets must be strictly
forward-looking.**

This is the same phenomenon as the user's econ-carryover point: a high-impact
*losing* round still shapes rounds N+1 and N+2 through the economy it leaves
behind. The objection and the fix are the same thing.

Note also that `round_win_impact` (kill_impact zeroed in lost rounds, see
`app/services/player_profile_types.py`) cannot be validated against round
outcome at all -- it is defined by it. Only `impact`, `kill_impact`,
`death_impact`, and the components are eligible.

### The tuning surface already exists

`app/scoring/impact.py` carries an explicit, self-described tuning surface:

```python
FACTOR_WEIGHTS = {"econ": 1.0, "time": 1.0, "swing": 1.0}
# "these are a starting proposal, not a final tuning -- adjust freely."
```

and computes

```
kill_impact  = damages + (w_e*K_econ + w_t*K_time + w_s*K_swing) / SUM(w)
death_impact =           (w_e*D_econ + w_t*D_time + w_s*D_swing) / SUM(w)
impact       = kill_impact - death_impact
```

Since `impact_scores` stores `econ_impact = K_econ - D_econ` (and likewise
`time_impact`, `swing_impact`, plus `damage`) as real columns since migration
`0006_impact_scores_columnar_breakdown`, this reduces to:

```
impact = damage + (w_e*econ_impact + w_t*time_impact + w_s*swing_impact) / SUM(w)
```

**A linear fit over four already-stored columns therefore yields
`FACTOR_WEIGHTS` directly.** Four free parameters, no black box, no recompute
needed to extract the training data. The fitted values feed the existing
`scripts/recompute_impact.py` -> `scripts/diff_impact_scores.py` workflow.

**Task 0 (gate):** verify that identity empirically on live rows before
building anything else. Each term is `round()`ed independently, so agreement
is expected within a couple of points, not exactly. If it does not hold, the
whole Stage A approach is invalid and must be reconsidered rather than worked
around.

### Parameterization limit (report, do not silently absorb)

The current form fixes the damage coefficient at 1 and *averages* the three
factors, so the factor block's total weight relative to damage is constrained.
An unconstrained fit produces coefficient ratios that this form may not be
able to express. The tool therefore reports **both**: the raw fitted ratios,
and the best `FACTOR_WEIGHTS` under the existing parameterization together
with the `damages` multiplier change (currently `* 1.25`) needed to absorb the
scale difference. It never quietly renormalizes one into the other.

### Scope boundary on "weight fitting"

Stage A fits the **outer** weights: `econ` / `time` / `swing` and the damage
multiplier. The **inner** curves -- the kill-order graph's per-edge bonuses,
`_econ_swing_risk_factor`, the time-factor curve -- are baked into the
`*_x_econ` / `*_x_time` / `*_x_swing` products and are not separable from the
stored columns. Re-fitting those requires re-deriving from `kill_events` and
is **out of scope**; revisit as a possible Stage C if the outer fit shows one
factor doing all the work.

## The evaluation contract

Defined once, before anything is fit, and identical across both stages. This
is what makes Stage A and Stage B comparable at all: they train on different
targets, so "which fit better" is meaningless without a common yardstick
neither was trained on.

### Three yardsticks

1. **First half -> match outcome.** Aggregate each team's weighted impact over
   rounds 1-12, take the differential, predict the winner. Strictly
   forward-looking, so no tautology. Reported **split by attack-first vs
   defense-first** (via `map_side_stats.attacking_team_for_round`), because a
   half is played entirely on one side and a player stronger on one side would
   otherwise be scored by the coin flip. Limited to 12 rounds of signal.
2. **Full match -> match outcome.** Side-balanced and uses every round, which
   the first-half yardstick cannot be. Absolute AUC here will be ~0.95 for
   *every* weighting and is meaningless on its own; it is read **only as the
   gap over the raw kill-differential baseline** on the identical scale. That
   gap is precisely "does the econ/time/swing machinery add anything beyond
   counting kills."
3. **Round N -> rounds N+2 onward.** ~24k observations, the tightest error
   bars, and the one that catches within-half econ carryover that the
   match-level yardsticks average over.

A weighting must do well on all three to be worth adopting. **Disagreement
between them is itself a finding and is printed as such**, not resolved by
picking a favourite.

### Protocol

- **5-fold cross-validation, split by match.** With 1,151 matches a single
  80/20 holdout leaves ~230 evaluation points and uselessly wide intervals;
  5-fold uses every match as an out-of-fold evaluation point with no leakage.
  All rounds of a match live in the same fold.
- Training targets may include match outcome **inside the training fold**;
  scoring always happens on out-of-fold matches.
- **Bootstrap confidence intervals on every reported number.**
- **Baselines are mandatory in every report:** current hand-tuned Impact, plain
  K/D, raw kills, damage alone. If Impact cannot beat K/D, that is the finding
  and the tool says so plainly.

## Sequencing

**Stage A** builds the entire harness end-to-end with the cheap target:
extraction, features, fitting, CV, yardsticks, baselines, report. It is a
complete standalone answer -- fitted weights, held-out numbers, and which
components are dead freight.

**Stage B** builds `V(state)` and swaps *only the target column*. Approaches 1
and 2 share the observations, the fitter, the folds, and the yardsticks; they
differ in one seam. So Stage B costs only the value model, and Stage A vs
Stage B is a direct comparison rather than an assumed one. If the WPA target
does not beat the forward-window target, Stage A's weights stand and
`V(state)` is still independently shippable as a match-page win-probability
readout -- nothing is wasted either way.

**Implementation planning splits here too:** the first implementation plan
covers P0 + Stage A only. Stage B gets its own plan, written once Stage A's
numbers exist -- both to keep either plan a reasonable size, and because Stage
A's component breakdown may change what `V(state)` should condition on.

## P0 -- `webapp/app/services/stats_math.py`

Pure numeric helpers. No domain knowledge, no DB, no imports from other
`app.services` modules. ~150 lines.

- `fit_logistic(X, y, weights, l2)` -- weighted IRLS. Accepts fractional
  `y` in [0, 1] (quasi-binomial), so one code path serves both stages' targets.
- `auc(scores, labels)`, `log_loss(probs, labels)`
- `point_biserial(values, labels)`
- `tercile_buckets(values)` -- for P2 later
- `bootstrap_ci(fn, data, draws, seed)`

**numpy only.** `scipy` is installed locally but absent from
`requirements.txt`, which `render.yaml` installs from; hand-rolling IRLS keeps
the deploy untouched.

## P1 Stage A -- `webapp/app/services/impact_eval.py` + `webapp/scripts/evaluate_impact.py`

Mirrors the existing `app/services/fight_ev.py` + `scripts/validate_fight_ev.py`
split: computation in a service module, CLI wrapper in `scripts/`.

### Observation unit

**(round, team)** -- not (round, player). Ten players share one outcome, so
fitting per-player against a team result is confounded; the team is the honest
unit. ~48k observations.

Per observation:

- **Features:** sum over that team's five `MatchPlayer`s of `ImpactScore.damage`,
  `.econ_impact`, `.time_impact`, `.swing_impact`.
- **Baseline features:** kills/deaths from `RoundPlayerStat`, plus stored
  `kill_impact` / `death_impact` / `impact`.
- **Context:** `match_id`, `round_number`, team, side for that round
  (`attacking_team_for_round`), round outcome, match outcome.

### Target seam

Every target builder returns `(X, y, w)` with `y` in [0, 1]:

- `forward_window_target(observations, k, gamma, match_weight)` -- Stage A.
  Expands round N into one weighted observation per future round N+1..N+k with
  weight `gamma**j`, plus the match outcome as one additional observation at
  `match_weight`. **Defaults: `k=3`, `gamma=0.7`, `match_weight=1.0`** (so the
  match contributes roughly as much as a single future round, keeping it
  auxiliary rather than dominant). All three are CLI flags, and the report
  includes a small sweep over them so the choice is visible rather than
  asserted.
- `wpa_target(observations, value_model)` -- Stage B. Same signature.

### Output

Printed table plus JSON (`--out`, matching `validate_fight_ev.py`'s
conventions, `DATABASE_URL` override honoured):

- fitted coefficients, and the two mappings back to `FACTOR_WEIGHTS` described
  under "Parameterization limit"
- per-yardstick AUC and log-loss with bootstrap CIs, for every candidate
  including the mandatory baselines
- per-component contribution -- which of econ/time/swing/damage is carrying the
  prediction and which is dead freight
- `n` at every level

## P1 Stage B -- `webapp/app/services/win_probability.py`

`V(state) = P(win match | round differential, rounds played, side)`, fit on
~24k round starts. Round impact becomes `V(after) - V(before)`; `wpa_target`
feeds that through the unchanged harness.

**Econ enters as a measured second step, not from the start.** Fit the base
feature set first, then add econ state (reusing `economy_graphs._tier_for` and
`RoundPlayerStat.loadout`, both existing) and report the held-out log-loss
delta. That delta *is* the quantitative answer to "how much does econ carryover
actually matter" -- the question that motivated this whole design. Including
econ from the start yields a model but no number for it.

## Testing

- `tests/test_stats_math.py` -- synthetic data with analytically known answers
  (a separable logistic fit, an AUC with a hand-computable value, a bootstrap
  with a fixed seed).
- `tests/test_impact_eval.py` -- hand-built fixtures for observation
  extraction, the forward-window target expansion, and fold assignment
  (asserting no match spans two folds).
- **The Task 0 reconstruction check ships as a test**, not just a one-off
  script run, so a future change to `impact.py`'s combination step fails loudly
  here.

## Out of scope

- No new tables, no migrations.
- No change to `impact.py`'s formula in this project. The tool *proposes*
  weights; adopting them is a separate deliberate act through the existing
  `recompute_impact.py` / `diff_impact_scores.py` scripts.
- No web endpoint, no router, no template. Nothing here is imported by
  `app/main.py`, so the deploy path is untouched.
- Inner-curve refitting (Stage C), P2, P3 -- each its own spec.
