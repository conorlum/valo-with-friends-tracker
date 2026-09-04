# The plant window: a shared helper, a match breakdown, and a time-factor redesign

**Status:** awaiting human review
**Date:** 2026-09-03

## Purpose

`app/scoring/impact.py`'s `_time_factor` returns a flat `1.0` for every
pre-plant kill. That is 122,833 of 178,242 kills in the database -- 69% of all
kill events -- carrying no time signal whatsoever.

Measurement on 2026-09-03 shows that is leaving a large effect on the table.
The signal is not in the round clock, which is flat; it is in **proximity to
the plant**. This spec covers a shared helper for reasoning about the plant
window, a descriptive match-page breakdown built on it, and the `_time_factor`
redesign that is the actual prize.

## What this spec covers

Three deliverables in dependency order. Part 1 is a precondition for both
others. Part 2 is additive and touches no scoring. Part 3 changes every
displayed Impact number and needs a full rescore.

1. `app/scoring/plant_window.py` -- phantom-plant detection, a verified
   attacking-side function that finally handles overtime, and the window
   arithmetic.
2. ~~A site-participation stat on the player page.~~ **DEFERRED 2026-09-04** --
   an additive page stat, not a scoring change. Findings retained in Part 2.
3. The `_time_factor` redesign.

**Part 2 changed twice during design and the reader should know why.** It was
first proposed as a player-page stat ranking players by how much their K/D
degrades under site pressure; that *efficiency* framing was measured and
**failed** (split-half r = +0.062). It was then reframed as a *participation*
question -- where do a player's kills happen, not how well they trade -- which
measures as a genuinely stable trait (split-half r = +0.44 to +0.52) and is
almost entirely independent of agent choice. Part 2 is the participation
version. Both results are recorded below, because the pair is the useful
lesson: on this window, location replicates and efficiency does not.

## Layer note -- this is a POLICY document

This spec contains **scoring decisions only**: the normative mapping from
measured quantities into Impact points, plus units, invariants, boundary
behaviour and validation.

The measurements it rests on live in [`../2026-09-04-impact-measurements.md`](../2026-09-04-impact-measurements.md) and are cited by ID (`M1`,
`M2`, ...). They are not restated here.

The two layers are separate because they were previously interleaved, and
causal language leaked from the design into the measurements. A measurement is
"conditional on X, the observed rate of Y was Z"; a policy is "therefore a kill
in that situation is worth W points". **The second does not follow from the
first without a decision, and those decisions are the content of this
document.**

When a measurement is re-run and changes, the citations below identify which
decisions need revisiting.

## What this spec rests on

| decision here | measurement | what the measurement does NOT establish |
|---|---|---|
| A proximity term exists at all | `M1` | that timing *causes* the outcome; conditioning on eventually-planted rounds is post-treatment selection |
| Absolute clock is not used | `M2` | -- |
| The amplitude is a function of man-advantage | `M3` | the functional form outside adv -3..+2 |
| Attacker and defender get separate terms | `M4` | that pooled side figures mean anything -- they are a Simpson's artifact |
| Bounds are cosmetic; fit the middle slope | `M5` | -- |
| The shape is not an economy artifact | `M6` | that OT is an independent holdout -- it is used to make this argument |
| No deadline / plant-denial term | `M7` | -- |
| Phantom plants excluded | `M16` | -- |
| One consolidated attacking-side helper | `M15` | novelty -- `map_side_stats.py` already derived the OT rule |
| Site participation deferred | `M17`, `M18` | -- |

**Every claim previously made in this spec that has since been withdrawn is
listed in the measurements document's register.** Do not reintroduce them.

## Constraints and premises

### Proximity to the plant is ex-post information -- this is a leakage boundary

`plant_time` is not known when the kill happens. Any factor reading it is
using information from the future of the round. This is the same hazard
`_realized_econ_swing_factor` carries, and it already has an established
treatment in this codebase: `build_impact_rows_for_match(db, match_id,
use_realized_swing: bool = True)`, where the evaluation harness passes `False`
to get an ex-ante replay (`impact.py:502-516`).

**The new time factor must follow that pattern exactly.** The parameter
generalises to `use_realized: bool = True`, gating both the realized swing
term and the new plant-proximity term. Shipped scoring passes `True`;
`app/services/impact_eval.py` and `impact_eval_cache.py` pass `False`.

Consequence to state plainly: **this change will look like nothing, or worse
than nothing, on the forward-looking yardsticks in `impact_eval.py`**, because
those deliberately strip the information it depends on. It validates as
attribution -- "this kill was part of taking site" -- not as prediction. Per
`docs/superpowers/2026-09-02-impact-stages-abc-findings.md`, attribution is a
legitimate register for this score, and the user's stated definition is about
impact on winning rather than forecasting it. Do not read a flat forward-looking
result as failure, and do not tune against those yardsticks here.

### Never-planted rounds have no plant to be proximate to

They get a flat 1.0, which the clock null above supports empirically. This
creates a normalisation hazard: if planted-round pre-plant kills can exceed
1.0 while unplanted-round kills cannot, planted rounds gain Impact relative to
unplanted ones for no modelled reason.

**The pre-plant factor is therefore centred -- but on contribution, not on the
factor itself.** An earlier draft required the sample-weighted mean of the
*factor* to equal 1.0. That does not preserve total Impact: the scorer uses
`kill_order_bonus * time_factor` (`impact.py:539-541`) and deaths additionally
carry `_traded_factor` (`:544`), so `mean(f)=1` only preserves the mean
contribution if `f` and `kill_order_bonus` are independent -- and they are not,
since timing and man-advantage state co-vary.

The gate is therefore on the **contribution**: the sample-weighted mean of
`kill_order_bonus * time_factor` over pre-plant kills must match its value
under the current flat-1.0 factor, within tolerance, computed separately for
kills and deaths. Note also that retuning the post-plant regime means a
pre-plant-only gate cannot by itself guarantee total Impact is stable -- the
post-plant contribution must be included in the same check or the
mean-preservation claim must be dropped explicitly.

### Phantom plants

24 planted rounds end in a *Time Win*, all with `plant_time > 100s`. A real
plant forces an explode or a defuse, so a planted round decided by the timer
never armed -- the plant registered as the round expired. These must be treated
as unplanted everywhere.

The rule is the outcome string, not the timestamp. Six further rounds have
`plant_time > 100s` and end in Elimination Wins; those are legitimate rounds
with a noisy timestamp and are kept.

### The attacking-side convention is now verified -- and was already solved once

**Read this before writing `attacking_team`.** There are already **four**
implementations in the repo, and one of them is OT-aware:

| location | OT behaviour |
|---|---|
| `app/scoring/impact.py:248` | returns `None` |
| `app/scoring/credit_events.py:25` | returns `None` |
| `app/services/state_replay.py:132` | duplicate; OT excluded upstream at `:229` |
| **`app/services/map_side_stats.py:35`** | **handles OT, empirically verified 2026-08-23** |

`map_side_stats.py` derived the OT rule from 20 regulation and 10 overtime
matches in August. Its rule (`offset = rn - 25; TEAM_1 if offset % 2 == 0`) is
**algebraically identical** to the one below -- 25 is odd, so `rn` odd iff
`rn - 25` even. The 5,251-round check is therefore a larger-sample
*confirmation* of an existing result, not a new discovery, and this spec
previously presented it as new.

**Part 1's job is consolidation, not invention:** absorb all four into one
helper, enumerate every consumer, and migrate them. Otherwise "finally handles
overtime" stays true for some products and false for others -- `state_replay`
would keep discarding OT rounds regardless.


`impact.py:248-256` and `credit_events.py:25-33` both carry comments calling
the 1-12 / 13-24 split "a documented convention, not derived from a stored
fact". It can now be stated as measured. Deriving the attacker independently
from outcomes -- a Time Win or Defuse Win means that team defended, a Detonate
Win means it attacked -- and comparing against the convention over all
outcome-determinable rounds gives **5,251 agreements and 0 disagreements**.

The same derivation settles overtime, which both functions currently return
`None` for: past round 24 the side alternates per round, `TEAM_1` on odd and
`TEAM_2` on even. **97 determinable OT rounds, 0 exceptions.** There are 448 OT
rounds in the database, currently excluded from every side-aware calculation.

## Part 1 -- `app/scoring/plant_window.py`

A new leaf module beside `credit_events.py`, depending only on the models and
`app.models.match.Team`. No DB session, no service imports.

```python
PRE_WINDOW  = 30.0
POST_WINDOW = 15.0

def is_phantom_plant(round_row) -> bool
def effective_plant_time(round_row) -> float | None   # None for unplanted or phantom
def attacking_team(round_number: int) -> Team | None  # None only for round_number < 1
def seconds_to_plant(round_row, kill_time: float) -> float | None
def in_plant_window(round_row, kill_time: float) -> bool
def window_bucket(round_row, kill_time: float) -> str | None
```

`effective_plant_time` is the single choke point: everything downstream asks
it rather than reading `round_row.plant_time`, so phantom exclusion cannot be
forgotten at a call site.

### The `_attacking_team` migration needs care

Three call sites currently rely on the `None` return for overtime, and they do
not all want the same fix:

- `impact.py:_econ_swing_risk_factor` returns `1` for `round_number > 24`
  before it ever consults `_attacking_team` (`impact.py:271-272`), so extending
  the function **cannot** change Impact scores through this path. Verify by
  rescoring a match with OT rounds and diffing `impact_scores` before the Part 3
  work lands.
- `credit_events.py:compute_round_credit_events` uses it for `plant_bonus`.
  Extending it **will** change Sugar Daddy and Scavenger credit figures for OT
  rounds, which is a correctness fix but a visible change to shipped shoutouts.
- `enemy_at_11_response.py` imports `round_bonus`, not `_attacking_team`, and
  is unaffected.

Both `impact.py` and `credit_events.py` keep thin private wrappers delegating
to the shared function, so the duplicated convention comment disappears and
neither module grows a new import cycle.

## Part 2 -- DEFERRED: site participation

**Cut from this spec on 2026-09-04.** It is an additive page stat, not a scoring
change, and this spec's remaining parts are the ones that move Impact. The
measurement work below is retained so it is not re-derived.

**State when deferred:**

- The **share** measure (window kills / all defensive kills in planted rounds)
  is **too noisy to ship**. Full-dataset chronological split-half is +0.177 CI
  [-0.021,+0.352] at >=30/half and +0.300 [+0.090,+0.483] at >=50/half -- half
  what the 1,151-match subset suggested, spanning zero at two of three gates.
- **Why:** a variance decomposition over 86 players with >=100 qualifying kills
  gives observed between-player SD 5.49pp, expected binomial sampling SD
  3.44pp, implied **true SD 4.28pp** and a **reliability ceiling of 0.608**.
  Real differences exist but are barely larger than the measurement error --
  the middle half of players sits inside 7pp.
- **It also drifts.** At >=30/half, a random split gives +0.446 against the
  chronological +0.177. The measure describes how a player is playing *at the
  time*, not a persistent trait -- which suggests the `recent` scope, never
  `career`, if it is ever revived.
- **The denominator was the main problem, and there is a fix.** The share
  divides by the player's own kill count, itself random. A **rate** over
  eligible rounds has a fixed denominator and roughly doubles reliability from
  identical events: window-kill rate reaches chronological **+0.573** at >=150
  rounds/half (n=46) and +0.855 at >=300 (n=24), against the share's +0.290 and
  +0.499. Window-*death* rate behaves similarly (+0.550 / +0.776).
- **The open question that blocks the rate.** It is more reliable partly
  because it also carries volume -- a player with more kills overall has a
  higher window-kill rate. **`corr(window-kill rate, overall kill rate)` has not
  been measured.** If it is ~0.95 the rate is kills-per-round wearing a hat,
  exactly the trap the window-K/D residual fell into at 0.777. Run that check
  first when this is picked up again.
- The **efficiency residual** (window K/D minus overall K/D) stays permanently
  dead: split-half +0.062, and it predicts nothing out of sample while plain
  K/D predicts fine on the same rounds.
- Sol's naming objection stands: the estimand is *plant-window share of
  defensive kills, conditional on a plant*, and its denominator structurally
  excludes defenders who **prevent** plants. Do not ship it as "fights for the
  site".

## Part 3 -- the `_time_factor` redesign

### Standing design constraint: no kill is ever worth negative Impact

Stated here so it is not relitigated. A kill may be worth very little; it is
never worth less than nothing. **The existing kill-order graph already embodies
this** -- its weights run 40 (`1v5 -> 0v5`, `5v1 -> 5v0`) to 250 (`1v1`), a
6.2x range floored well above zero.

There is a substantive argument for it, not only a product preference. The
negative association at `adv < 0` says *you got a kill and the plant happened
anyway*. That is information about the **round's** outcome, not the kill's
value -- the kill still removed an enemy. Round outcome is already represented
through the states the player's other kills and deaths occur in, so attributing
it back onto an individual kill double-counts it. Flooring the scalar corrects a
misattribution rather than merely softening the data.

**The distinction that makes this compatible with centering:** a scalar *below
1.0 is not a negative kill*. `kill_order_bonus x 0.6` on a 5v5 kill is 90 --
still well above the graph's own floor of 40. Only a scalar `<= 0` would violate
the constraint, so the scalar may range across 1.0 freely.

### Shape

`_time_factor` becomes a **strictly positive scalar on `kill_order_bonus`**, not
one of three parallel factors averaged together. Three regimes:

1. **Pre-plant, planted round** -- a proximity curve whose amplitude is a linear
   function of man-advantage, with separate attacker and defender terms.
2. **Post-plant** -- retained and retuned. The existing `plant+38..plant+45`
   denial bonus (1.75 kill / 0.5 death) and the post-resolution 0.5 survive.
   Post-plant state is known at kill time, so this half is **not** leakage.
3. **Pre-plant, never-planted round** -- flat 1.0, supported by the clock null.

### Parameterisation

```
scalar = clamp( 1 + amplitude(adv, side) * shape(seconds_to_plant),  0.2, 1.7 )
amplitude(adv, side) = intercept_side + slope_side * adv
```

- `shape()` is the shared proximity curve, 0 at the far end rising to 1 near the
  plant, **plateauing below 10s** -- the `-5..0` bucket is at or below the
  `-10..-5` bucket in all four even states, so forcing a continued rise would fit
  a shape the data contradicts.
- Four fitted interaction parameters (slope and intercept x attacker/defender)
  plus the shape knots. Fitted with intervals, not hand-set.
- **Continuous in advantage.** No hard cutoff at `adv = 0` -- the measured effect
  is continuous, and a step there would be an artifact of bucketing.
- **Bounds 0.2 - 1.7.** Deliberately wide, and cheap: they bind on under 1.5% of
  kills at the floor and 2-4% at the ceiling. **Do not spend fitting effort on
  the clamps.** The middle slope through `adv -1..+1` governs 82.6% of affected
  kills and is what must be fitted well.

### Centering is on CONTRIBUTION, not on the factor

An earlier draft required the sample-weighted mean of the *factor* to equal 1.0.
That does not preserve total Impact: the scorer computes
`kill_order_bonus * time_factor` (`impact.py:539-541`), and deaths additionally
carry `_traded_factor` (`:544`). `mean(f) = 1` preserves the mean contribution
only if `f` and `kill_order_bonus` are independent, and they demonstrably are
not -- the amplitude is a function of man-advantage, which *is* the graph's own
input.

**Gate:** the sample-weighted mean of `kill_order_bonus * scalar` over affected
kills must match its value under today's flat-1.0 factor, within tolerance,
computed separately for kills and for deaths. Because post-plant is also being
retuned, the check must span both regimes, or the mean-preservation claim must be
dropped explicitly rather than quietly weakened.

### Deaths

`for_death` currently distinguishes only the denial window. Whether the advantage
interaction should differ for deaths is **not settled here**, and the team-level
data cannot identify it -- the same limitation the Stage C spec recorded for the
kill/death base split.

**Ship symmetric**, consistent with the standing decision that death impact
carries as many variables as kill impact. Revisit only with a player-level read.

### Rollout

- `IMPACT_CALCULATION_VERSION` 1 -> 2, folding mechanically into
  `player_view_cache.cache_version()`.
- Full rescore via `scripts/recompute_impact.py`, then
  `scripts/recompute_player_views.py`.
- `.impact_eval_cache/` self-invalidates on the version key.
- Stage C artifacts become non-comparable (`kill_order_refit.py:1837` stamps
  results with the version). Costs nothing -- every Stage C candidate was
  non-deployable -- but do not quote those numbers afterwards.

### Validation

- **Primary: a temporal holdout.** Fit on earlier matches, evaluate the lift
  table on later ones. This is the only validation here not contaminated by the
  data used to design the curve.
- **NOT a holdout: overtime.** OT is used in this spec's findings to argue the
  shape is not an economy artifact, so it cannot also serve as independent
  validation. It is a robustness sample. An earlier draft listed it as the
  primary check; that is withdrawn.
- **State coverage:** the fitted amplitude must reproduce the measured lift at
  each advantage level within its bootstrap interval, including the negative
  levels the clamp then floors.
- **Reported, not gated:** `impact_eval.py`'s forward yardsticks, with the
  leakage caveat. Pre-plant proximity is stripped under `use_realized=False`;
  the post-plant retuning is **not** leakage and *will* appear there, so that
  half is a legitimate non-inferiority check.

## Testing

Part 1:
- `is_phantom_plant` accepts the 24 known rounds and rejects the 6
  Elimination-Win rounds with `plant_time > 100s`.
- `attacking_team` reproduces the convention on 1-24 and the verified
  alternation past 24; a table-driven test over sampled real rounds asserts
  agreement with the outcome-derived attacker.
- `seconds_to_plant` returns `None` for unplanted and phantom rounds.

Part 2:
- A fixture match with a known plant produces the expected per-bucket counts.
- A match with only phantom plants produces an empty summary, not a crash.

Part 3:
- The centring gate: sample-weighted mean of the pre-plant factor is 1.0 within
  tolerance over the real kill distribution.
- Monotone non-decreasing in proximity up to the 10s plateau.
- `use_realized=False` returns exactly 1.0 for every pre-plant kill, so the
  ex-ante replay is unchanged from today's behaviour. **This is the leakage
  gate** and it must be exact, not approximate.
- An OT round scores identically before and after the Part 1 `attacking_team`
  change, confirming the `_econ_swing_risk_factor` early return.
- Golden-file test on one fully-scored match.

Per the note in `feedback_plan_execution_test_fixtures`: **verify each
synthetic fixture actually produces the relationship it claims before writing
assertions against it.**

## Out of scope

- Anything econ. The killer/victim loadout cross-tab found a large effect
  pointing opposite to the current `econ_differential_factor`, and the user has
  explicitly parked it. It is a separate spec.
- A deadline / plant-denial term. Measured and not elevated.
- Any player-level site-pressure ranking. Measured and unreliable.
- Refitting `FACTOR_WEIGHTS`. Unrelated, already measured at +0.005 AUC, and
  changing two things at once would make the rescore uninterpretable.
- Re-crawling for fresh matches. Worth doing, independent of this.
