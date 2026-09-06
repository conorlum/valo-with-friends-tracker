# The plant window: a shared helper and a time-factor redesign

**Status:** awaiting human review
**Date:** 2026-09-03, substantially revised 2026-09-05

**Revision note (2026-09-05).** Post-plant was frozen in the 2026-09-03 draft
because no measurements supported retuning it. Those measurements now exist
(`M21`-`M26`) and show the frozen regime carries a **larger** error than the
pre-plant one this spec was written to fix: the shipped ramp is side-blind,
while measured duel stakes depend on state, on side, and reverse direction
between `2v1` and `1v2`. Post-plant is therefore **in scope**. The pre-plant
centring gate has also been replaced -- `M19` shows the previous one
constrained 17.7% of the affected weight. Both changes are recorded in the
measurement record's register of withdrawn claims.

## Purpose

`app/scoring/impact.py`'s `_time_factor` returns a flat `1.0` for every
pre-plant kill, carrying no time signal whatsoever. On the full 3,124-match
dataset the population this spec's scalar would touch -- pre-plant kills in
rounds that were planted -- is **168,370 kills, 34.7% of all kill events**
(`M5`). (An earlier draft quoted 122,833 of 178,242 from the 1,151-match
subset, and against a wider denominator that also counted pre-plant kills in
never-planted rounds.)

Measurement on 2026-09-03 shows that is leaving a large effect on the table.
The signal is not in the round clock, which is flat; it is in **proximity to
the plant**. This spec covers a shared helper for reasoning about the plant
window, a descriptive match-page breakdown built on it, and the `_time_factor`
redesign that is the actual prize.

## What this spec covers

Four parts in dependency order. Part 1 is a precondition for Parts 3 and 4.
Parts 3 and 4 each change every displayed Impact number and need a full
rescore.

1. `app/scoring/plant_window.py` -- phantom-plant detection, a verified
   attacking-side function that finally handles overtime, and the window
   arithmetic. Also admits **1,274 overtime rounds** into the state replay,
   which is an intended product fix rather than a refactor.
2. ~~A site-participation stat on the player page.~~ **DEFERRED 2026-09-04** --
   an additive page stat, not a scoring change. Findings retained in Part 2.
3. The **pre-plant** `_time_factor` redesign -- a proximity curve whose
   amplitude is linear in man-advantage. Ex-post; invisible to the forward
   yardsticks by construction.
4. The **post-plant** redesign -- a leverage ratio in `(state, second, victim
   side)`, replacing the side-blind ramp. **Ex-ante**, so the forward
   yardsticks see it in full and it carries a non-inferiority gate Part 3 does
   not.

**DECIDED 2026-09-06: everything ships under ONE `IMPACT_CALCULATION_VERSION`
bump, 1 -> 2, with a single rescore.** An earlier version of this line required
separate bumps for Parts 3 and 4; that is withdrawn. See "Rollout" for the
decision, and for why it costs far less interpretability than it appears to.

**Part 2 changed twice during design and the reader should know why.** It was
first proposed as a player-page stat ranking players by how much their K/D
degrades under site pressure; that *efficiency* framing was measured and
**failed** (split-half r = +0.062). It was then reframed as a *participation*
question -- where do a player's kills happen, not how well they trade -- which
initially appeared to measure a stable trait. **On the full dataset it does
not** -- split-half +0.177 to +0.300, spanning zero at two of three gates
(`M17`) -- and Part 2 is deferred entirely. Both results are recorded because
the pair is the useful lesson: on this window, location replicates better than
efficiency, but neither replicates well enough to ship.

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
| The pre-plant centring gate is on the KILL side, with the death-side residual reported | `M19`, `M20` | the residual under the *actual* fitted parameters -- `M20`'s scalar family is not fitted |
| Post-plant is retuned at all | `M21`, `M22` | that the shipped ramp's *direction* is wrong for attackers -- that is `M24`'s result, not `M21`'s |
| The post-plant quantity is duel leverage, not a raw win rate | `M23` | causation; `V` is estimated from rounds that reached each state and second |
| The post-plant factor is a function of state, second and victim side | `M24`, `M25` | the functional form; cells thin above 6 players alive |
| Two deadlines, at +38.0s and +41.5s | `M26` | that either is a discontinuity rather than a steepening |
| No forced-death discount | -- | **nothing supports one either way.** Defuse progress is absent from the schema (`rounds` has only `defused`/`defuse_time`) and is not recoverable without replay parsing, so the agency question is dropped rather than guessed |

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

They get a flat 1.0. **This is a conservative policy choice, not an empirical
finding, and an earlier draft wrongly cited `M2` for it.** `M2` measures the
absolute clock among pre-plant kills in rounds that *were* planted; it says
nothing about never-planted rounds. `M7` covers only very late never-planted
kills and is subset-only. To claim empirical support, measure clock effects
*within* never-planted rounds across states against an equivalence bound.
Until then the flat value is chosen because it is neutral, not because it is
verified.

Flat also creates a normalisation hazard: if planted-round pre-plant kills can exceed
1.0 while unplanted-round kills cannot, planted rounds gain Impact relative to
unplanted ones for no modelled reason.

**The pre-plant factor is therefore centred -- but on contribution, not on the
factor itself.** An earlier draft required the sample-weighted mean of the
*factor* to equal 1.0. That does not preserve total Impact: the scorer uses
`kill_order_bonus * time_factor` (`impact.py:539-541`) and deaths additionally
carry `_traded_factor` (`:544`), so `mean(f)=1` only preserves the mean
contribution if `f` and `kill_order_bonus` are independent -- and they are not,
since timing and man-advantage state co-vary.

### The gate is on the KILL side, and the death-side residual is reported

A previous version of this spec gated the **net** contribution,
`mean(K*s) - mean(K*T*s)`, and asserted that was "the one achievable invariant
of the three". **That is withdrawn.** The per-kill integrand of that expression
is `K*s*(1-T)`, and `_traded_factor` returns *exactly* 1 for any kill whose
killer was not traded back within 10s -- so `(1-T) = 0` there. `M19` measures
the consequence: **71.7% of affected kills enter that gate at zero weight**,
and it sees only **17.7% of the kill-side mass**. A scalar badly miscalibrated
across the untraded majority passes it untouched.

The underlying algebra the old text relied on is still correct: with one
multiplicative constant you cannot satisfy
`mean(K*s) = mean(K)` **and** `mean(K*T*s) = mean(K*T)` simultaneously. Two
equations, one unknown. But "cannot hit both exactly" does not force the net;
it forces a choice of which one to pin.

**Pin the kill side.** `kill_impact` is not an intermediate -- it is a stored
column (`impact_score.py:41`) surfaced as `average_kill_impact`
(`routers/players.py:154,165`) and `total_kill_impact` / `kill_impact_by_round`
(`services/matches.py:105,84`), and it is what the win-gated Round Win Impact
metric displays. Gating only the net lets `kill_impact` and `death_impact`
inflate together while the check reads green, moving every displayed Round Win
Impact number.

- **Gate (exact).** Solve one constant `c` so the sample-weighted mean of
  `kill_order_bonus * c * s` over affected kills equals its value under
  today's flat-1.0 factor. This is the hazard named above -- planted rounds
  gaining relative to unplanted ones -- and pinning the kill side addresses it
  directly.
- **Reported, with a predeclared tolerance.** The death-side residual
  `mean(K*T*c*s)/mean(K*T) - 1`. `M20` puts this at **+0.35% to +1.00%** across
  a `k` grid spanning far past any plausible amplitude, and **~+0.7%** at the
  amplitude this spec's own clamp rates imply. Tolerance: **2%**, declared
  before fitting. Exceeding it is a finding, not something to tune away.
- The residual is non-zero because `s` and `T` are correlated through
  man-advantage, not through proximity. `M20` shows K-weighted mean `T` is flat
  across proximity buckets marginally, while at `adv = -2` it falls 0.884 ->
  0.713 and at `adv = +2` it rises 0.736 -> 0.919. The margin hides it; the
  scalar, being a function of advantage, does not get to.

The rejected option "a documented aggregate shift" is the one adopted here. It
was rejected without a stated reason; measured, it costs ~0.7%.

**Post-plant is centred separately** -- see Part 4. The two regimes have
different populations and different factors, and a single constant across both
would let one absorb the other's drift.

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
`TEAM_2` on even. **294 determinable OT rounds, 0 exceptions.** There are
**1,274** OT rounds over **374 matches**. They are excluded from `impact.py`
and `credit_events.py`, and dropped outright by `state_replay.py:229` -- but
**not** from `map_side_stats.py`, which already handles them. "Excluded from
every side-aware calculation" was false and is corrected.

**Corrected 2026-09-06.** This paragraph previously read "97 determinable OT
rounds" and "there are 448 OT rounds", contradicting both `M15` and this
spec's own Part 1 text below. Both were unmarked `[SUBSET]` figures -- 448 is
exactly the OT-round count of the newest 1,151 matches. See the measurement
record's register.

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

`app/services/state_replay.py:132` is the fourth implementation and the one
with real blast radius. It currently **discards every OT round**
(`:229`, `excluded_rounds_by_reason["overtime_unknown_side"]`).

**Making OT rounds visible is an intended product fix, not blast radius.**
There are **1,274** OT rounds across **374 matches** -- 12.0% of the corpus,
1.93% of all rounds (`M15`; an earlier draft said 448, an unmarked subset
figure now withdrawn). Every one of them clears every other replay exclusion.
They are real rounds that were played, including every overtime clutch anyone
on the roster has pulled off, currently erased from their fight-EV and state
diagrams because a side convention had not been derived when `state_replay`
was written. The exclusion reason string says so: `overtime_unknown_side`.
The side is now known and verified at 294 determinable rounds, 0 exceptions.

**Decision: OT rounds are pooled, not tagged.** OT economy is compressed
(`M6`: 94.7% full-buy against regulation's 57.4%), so pooling mixes two
economic regimes into diamonds that carry no economy term. Accepted: at 1.93%
of rounds it cannot move a bootstrapped cell, dropping rounds that happened is
the worse bias, and more full-buy-versus-full-buy fights is a fine thing for
the diamond to contain. Side is already in the fight-EV key, so an OT flag
remains separable later if it ever matters.

### The full consumer enumeration -- seven call sites, not three

An earlier draft listed three and told the reader to "enumerate every consumer".
The complete list:

| call site | current source | effect of migration |
|---|---|---|
| `impact.py:277` | own | **none** -- OT early-returns at `:271` before consulting it |
| `credit_events.py:107` | own | **changes OT** Sugar Daddy / Scavenger credit figures |
| `fight_ev.py:545` | `state_replay` (import at `:33`) | **transitive** -- see below |
| `impact_eval.py:195` | `map_side_stats` | none -- already OT-aware |
| `win_probability.py:98` | `map_side_stats` | none -- already OT-aware |
| `round_streak_stats.py:84` | `map_side_stats` | none -- already OT-aware |
| `map_side_stats.py:85` | own (the reference) | none |

`enemy_at_11_response.py` imports `round_bonus`, not `_attacking_team`
(`credit_events.py:91`), and is unaffected.

**State explicitly:** `impact_eval.py:195` is the forward-yardstick harness and
already uses the OT-aware version, so **the yardsticks do not move for
side-convention reasons.** Part 3 and Part 4 lean on those being interpretable;
this is asserted here rather than left for a reader to re-derive.

### The two edits are atomic

Part 1 is two changes in two places, and only one ordering is safe:

- **Edit A** -- make the side function total (handle OT).
- **Edit B** -- delete the `:229` exclusion.

`fight_ev.py:545` builds its side map over `match_input.rounds` (all rounds)
but consumes it only against `entries`/`duels`, which today have OT already
stripped upstream. So **Edit A alone changes nothing**, and A+B is the intended
end state.

**Edit B alone is the failure mode.** OT rounds enter the replay; the state
diagrams pick them up, because `player_graphs.accumulate_state_stats_from_replay`
takes no side argument and is side-agnostic; but fight-EV asks the un-migrated
function, gets `None`, and **silently skips every one** at
`fight_ev.py:172-174` and `:186-188` (`if side is None: continue`). The result
is two products built from the same replay disagreeing about which rounds
exist, with both written into `player_view_cache`. Nothing errors.

Requirements:

1. A and B ship in the same change. Edit B is a one-line deletion ten lines
   below the function Edit A touches, which is exactly why this needs stating.
2. A test asserts the side map is **total** over the rounds present in
   `entries`, and fails otherwise.
3. After migration `side is None` is unreachable, so the two `continue`
   branches become an **assert**. A silent skip that leaves no diagnostic is a
   regression against `state_replay`'s counted
   `excluded_rounds_by_reason["overtime_unknown_side"]`, and this project has
   already lost months to numbers computed over a quietly wrong population
   (`M15`).
4. `_round_side_map`'s `if attacking_team is None` branch (`:545-547`) becomes
   dead code, since `map_side_stats.attacking_team_for_round` returns `Team`,
   not `Team | None`. Remove it rather than leaving it as false reassurance.

### The four implementations do not agree on return type

Not mentioned in the earlier draft and it will surface mid-migration:
`impact.py` and `state_replay.py` return `Team | None`, `map_side_stats.py`
returns `Team`, and **`credit_events.py` returns `str | None`** -- literal
`"team-1"` / `"team-2"`, compared against string teams at `:107`. The thin
private wrapper `credit_events` keeps must do the enum-to-string conversion.

`app/services/map_side_stats.py:35` is the reference implementation and should
be the one absorbed; the others delegate to it. Both `impact.py` and
`credit_events.py` keep thin private wrappers so the duplicated convention
comment disappears and no module grows an import cycle.

**Enumerate every consumer before migrating.** Otherwise "finally handles
overtime" stays true for some products and false for others.

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
   Part 3.
2. **Post-plant, pre-resolution** -- a leverage ratio in `(state, second,
   victim side)`. **Retuned in this version**; see Part 4. The 2026-09-03 draft
   froze this regime for want of evidence. `M21`-`M26` supply it and show the
   frozen ramp is wrong in three separate ways at once.
3. **Post-resolution** (after detonation at plant+45, or after `defuse_time`) --
   flat 0.5, unchanged. Nothing measured here; the round is over.
4. **Pre-plant, never-planted round** -- flat 1.0. **This is a conservative
   policy choice, not an empirical finding.** An earlier draft cited `M2` for
   it; `M2` measures the absolute clock among pre-plant kills in rounds that
   *were* planted and says nothing about never-planted rounds. To claim
   empirical support, measure clock effects within never-planted rounds across
   states against an equivalence bound. Until then the flat value is chosen
   because it is neutral.

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

### The fitting contract

`M3` and `M4` report **percentage-point lifts in round-win rate**. The scoring
equation needs a **dimensionless amplitude**. That conversion is a decision,
not a measurement, and must be stated rather than left implicit:

- **Link.** Fit on the log-odds scale, not on raw percentage points. A lift
  from 57% to 71% and one from 85% to 99% are not the same quantity in pp, and
  the clamp interacts badly with a raw-pp mapping near the boundaries.
- **Amplitude mapping.** `amplitude = k * logit_lift`, with a single global `k`
  chosen so the fitted scalar spans the intended range over the observed data.
  `k` is reported, not hand-tuned per cell.
- **Observations.** One row per non-self pre-plant kill in a non-phantom,
  non-surrendered planted round. Deaths are scored with the same fitted
  parameters (symmetric, per the Deaths section) and do **not** enter the fit
  as separate rows.
- **Weights and errors.** Logistic regression of round win on
  `shape(dt) x adv x side` **plus exact pre-kill state fixed effects**.
  Advantage alone collapses 5v5, 4v4, 3v3 and 2v2 into one value, and `M1`
  shows those states have different baselines *and* different proximity lifts
  -- so without exact-state terms a shift in the 5v5-versus-2v2 mixture can
  masquerade as a time effect at fixed advantage. `kill_order_bonus` depends
  on the exact transition, not on the differential, for the same reason.
  **Match-clustered** standard errors; the clustering premise in `M1`'s method
  section applies to the fit too.
- **The side terms must earn their keep -- a predeclared nested comparison.**
  This spec's rests-on table cites `M4` for separate attacker and defender
  terms, but `M4`'s population is *pre-plant kills in rounds that were
  planted*, so every defender in it is by construction a defender who was
  about to lose the site. The measured side difference may therefore be the
  selection speaking rather than the two sides responding differently to plant
  proximity. **This cannot be settled empirically inside the estimand** --
  proximity to the plant does not exist in a round with no plant, so the
  conditioning is structural. What can be settled is whether the parameters
  pay for themselves: fit with and without the side interaction, against exact
  pre-kill state fixed effects, and compare. If dropping side costs nothing,
  ship two parameters instead of four and delete the claim. Declared before
  fitting so the answer cannot be chosen after seeing it.
- **Shape knots.** Fixed at the measurement boundaries (30, 20, 10, 5, 0
  seconds) rather than estimated, so the shape is not free to chase noise. The
  plateau below 10s is imposed, not fitted, per `M1`.
- **Advantage outside -3..+2.** Clamped to the nearest fitted level. `M3` has
  no support beyond that range and `M5` shows those cells are a rounding
  error.
- **Order of operations.** Fit, then clamp, then centre. Centring is computed
  on the clamped scalar, because the clamped value is what actually scores.
- **Temporal split -- a stability check, not a holdout.** Fit before the 70th
  percentile by `played_at`, evaluate after; chosen by date and fixed before
  fitting. But the full dataset already selected the buckets, the plateau, the
  interaction and the clamp concept, so this **does not** produce independent
  validation and must not be described as such. A genuine holdout needs matches
  not yet crawled.
- **`k` is a policy parameter, not an estimate.** The amplitude scale and the
  0.2-1.7 clamps are chosen, not fitted. Report sensitivity across several
  predeclared values of `k`; do **not** attach confidence intervals to a scalar
  whose amplitude was normatively selected.

### Centering is on CONTRIBUTION, not on the factor

An earlier draft required the sample-weighted mean of the *factor* to equal 1.0.
That does not preserve total Impact: the scorer computes
`kill_order_bonus * time_factor` (`impact.py:539-541`), and deaths additionally
carry `_traded_factor` (`:544`). `mean(f) = 1` preserves the mean contribution
only if `f` and `kill_order_bonus` are independent, and they demonstrably are
not -- the amplitude is a function of man-advantage, which *is* the graph's own
input.

**Gate:** as specified in "The gate is on the KILL side" above -- one constant
solved exactly on `mean(kill_order_bonus * scalar)` over affected pre-plant
kills, with the death-side residual reported against a 2% predeclared
tolerance. Post-plant is centred **separately**, on its own population, in
Part 4. A single constant spanning both regimes would let one absorb the
other's drift and make the rescore uninterpretable.

### Deaths

`for_death` currently distinguishes only the denial window. Whether the advantage
interaction should differ for deaths is **not settled here**, and the team-level
data cannot identify it -- the same limitation the Stage C spec recorded for the
kill/death base split.

**Ship symmetric**, consistent with the standing decision that death impact
carries as many variables as kill impact. Revisit only with a player-level read.

**This survives into post-plant, contrary to an intermediate reading of `M25`.**
A post-plant event transfers what the *victim's team* lost, and both the
killer's credit and the victim's debit reference that same quantity -- so kill
and death remain mirror images and one scalar serves both. The asymmetry `M25`
found is over **which side the victim was on**, not over kill versus death. See
Part 4.

### Rollout

- **ONE `IMPACT_CALCULATION_VERSION` bump, 1 -> 2, covering this spec AND the
  econ spec together. DECIDED 2026-09-06.** An earlier version of this bullet
  required a bump per change (Part 3, Part 4, the econ swap) so that movement
  in displayed numbers stayed attributable. Withdrawn. The site is a
  friend-group tracker with no external consumers, nobody is reading these
  numbers closely enough for un-attributable movement to cost anything, and if
  the result is bad the remedy is one more rescore back. Three rescores to
  protect attribution nobody needs is the wrong trade.
- **This costs much less interpretability than it looks, and the reason is
  load-bearing.** The two non-inferiority gates (Part 4 here, the econ deletion
  in the econ spec) run through `impact_eval.py`, which **replays from raw
  data** -- `load_all_observations` calls `build_impact_rows_for_match`
  directly (`:1519`) and never reads stored `impact_scores`. **The gates
  therefore need no rescore at all.** Each change can be evaluated on its own
  by toggling it in the replay, and the database is rescored once at the end.
- **Requirement that follows: the changes must be independently switchable in
  the replay path.** `use_realized_swing` is already such a switch; Part 3,
  Part 4 and the econ swap need equivalents. Without them the single bump does
  become un-diagnosable, because the two gates push in opposite directions --
  the post-plant retune should help, the econ deletion should cost -- and
  measured together they can cancel and both read clean.
- Full rescore via `scripts/recompute_impact.py`, then
  `scripts/recompute_player_views.py`.
- **Rollback is one rescore.** Revert the scoring code and rescore; migration
  `0008`'s added columns are left in place and unused, which is harmless.
- `.impact_eval_cache/` self-invalidates on the version key.
- Stage C artifacts become non-comparable (`kill_order_refit.py:1837` stamps
  results with the version). Costs nothing -- every Stage C candidate was
  non-deployable -- but do not quote those numbers afterwards.
- **`fight_ev.CALCULATION_VERSION` 3 -> 4 as well, and this is functional, not
  bookkeeping.** That constant is an input to `_bootstrap_seed`
  (`fight_ev.py:273`), so every stored confidence interval on the diamond
  depends on it, and its own comment (`:44-46`) names this exact situation:
  *"use it to force a reshuffle if the replay/aggregation logic changes in a
  way that should not be silently blended with old draws."* Admitting 1,274 OT
  rounds is that change. Bumping `IMPACT_CALCULATION_VERSION` alone would
  invalidate the cache correctly but leave the new draws -- over a different
  population of rounds -- reusing seeds computed for the old one.
- **`STATE_DIAGRAM_CALCULATION_VERSION` stays at 2. DECIDED 2026-09-06.** An
  earlier version of this bullet called for 2 -> 3 on the grounds that the
  diagrams' round population changes. Withdrawn: **admitting overtime rounds
  adds data, exactly as ingesting more matches does, and that has never bumped
  this constant.** The replay *rules* are unchanged; only the set of rounds
  they run over grows. Bumping it would imply a semantic change that did not
  happen.

  Note this constant feeds no calculation -- unlike `fight_ev.CALCULATION_
  VERSION`, which is an input to `_bootstrap_seed` -- so leaving it alone has
  no functional consequence. The player-view cache is invalidated regardless,
  by the fight-EV and Impact bumps shipping alongside it
  (`player_view_cache.py:116-121`).
- `validate_fight_ev.py` writes reports stamped `"calculation_version"`
  (`:277`); any saved report predating the bump describes a different
  population.

### Validation

- **Primary: a temporal STABILITY CHECK, not a holdout.** Fit on earlier
  matches, evaluate the lift table on later ones. **This is not an
  uncontaminated holdout** -- the full dataset already selected the buckets, the
  plateau, the side and advantage interactions and the clamp concept, so a
  later date split cannot make it independent. It tests whether coefficients
  drift, nothing stronger. A genuine holdout needs matches not yet crawled.
- **NOT a holdout: overtime.** OT is used in this spec's findings to argue the
  shape is not an economy artifact, so it cannot also serve as independent
  validation. It is a robustness sample. An earlier draft listed it as the
  primary check; that is withdrawn.
- **State coverage:** the fitted amplitude must reproduce the measured lift at
  each advantage level within its bootstrap interval, including the negative
  levels the clamp then floors.
- **Reported, not gated:** `impact_eval.py`'s forward yardsticks, with the
  leakage caveat. Pre-plant proximity is stripped under `use_realized=False`,
  so the yardsticks are blind to **Part 3**. They are reported for the record
  only. **This does not extend to Part 4** -- see its leakage section.

## Part 4 -- the post-plant regime

### Why this is no longer frozen

The 2026-09-03 draft froze post-plant for want of evidence. `M21`-`M26` supply
it, and the frozen regime turns out to carry the larger error. The shipped
`1 + (t - plant)/53` ramp is **side-blind**, and measured value is not:

| | ramp does | measured | verdict |
|---|---|---|---|
| attacker kills | rises 1.05 -> 1.75 | leverage falls late | **backwards** |
| defender kills | rises 1.05 -> 1.75 (+67%) | leverage rises +19pp -> +39pp (+105%) | right sign, **too weak** |
| defender deaths | rises 1.00 -> 1.72, then cliffs to 0.5 | cost falls 0.141 -> 0.023 | **backwards for 38s** |

The `plant+38..45` window pays **1.75 to both sides** at the moment their
stakes are furthest apart -- in a 1v1 at t=38 the attacker carries **10x** the
defender's risk (`M26`). Population affected: **149,976 post-plant kills**,
30.7% of the database, of which **68,378 are defender kills** scored on a curve
that is flat then backwards.

### The estimand, and why not the obvious one

The raw win-rate curve (`M21`) cannot separate "this kill decided the round"
from "this round was already decided and a kill happened in it". Nor can a
marginal difference against `V(before)`: `M23` shows

```
V(after | killer wins) - V(before)  =  (1 - p) * [V(atk wins) - V(def wins)]
```

so it is **leverage shrunk by the probability of the other outcome**, and
collapses toward zero exactly when the favourite wins. It credits surprise, not
stakes. Neither is the quantity this factor should track.

The quantity is **what the victim's team lost** (`M25`):

```
D(a, d, t, victim=defender) = V(a, d-1, t) - V(a, d, t)
D(a, d, t, victim=attacker) = V(a, d, t)   - V(a-1, d, t)
```

`V(a, d, t)` is the attacking team's win rate given `a` attackers and `d`
defenders alive at whole second `t` after the plant, round unresolved. These
two sum exactly to the duel's leverage, so the decomposition is complete.

**Kill and death take the same value**, per the Deaths section: the event
transfers `D`, the killer is credited it and the victim debited it.

### Parameterisation

```
post_plant_factor(a, d, t, victim_side)
    = clamp( D(a, d, t, victim_side) / mean_over_t D(a, d, ., victim_side),
             FLOOR, CEIL )
```

- **A ratio, deliberately.** `D` is a state-transition value, which is exactly
  what `kill_order_bonus` already is. Using `D` directly would multiply two
  measures of the same thing and reintroduce the collinearity the econ spec
  exists to escape. Dividing by that state's own time-average leaves **only
  the time shape**, and leaves the state level where it already lives.
- **Normalise within `(state, victim_side)`**, not across sides. `M24`'s ratio
  table shows the time shapes differ by state with **reversing sign** -- `2v1`
  falls 1.33 -> 0.10 while `1v2` rises 0.89 -> 1.09 -- so a single shared
  shape cannot represent them.
- **`FLOOR` and `CEIL` are policy parameters, not estimates**, on the same
  footing as `k` in Part 3. Measured ratios span roughly 0.06 (`2v1` at t=43)
  to 1.50 (`3v2` at t=0). Report sensitivity across a predeclared grid; do not
  attach confidence intervals to a normatively chosen bound. The standing
  constraint that no kill is ever worth negative Impact requires `FLOOR > 0`.
- **No hard discontinuity at plant+38.** The shipped denial window opens at the
  *weaker* of the two deadlines. `M26` shows the full-defuse boundary (38.0s)
  produces a gentle bend while the half-defuse boundary (**41.5s**) produces
  the sharpest drops -- 41->42 is -0.171. The flat 1.75 override is deleted
  outright; the measured shape already contains both deadlines and does not
  need either hard-coded.
- **Post-resolution stays 0.5**, unchanged and unmeasured.

### Estimating `V` without overfitting

- Minimum **60 observations** per `(a, d, t)` cell, the floor `M23` used;
  1,219 cells clear it.
- Cells below the floor fall back to a factor of exactly **1.0** (neutral), not
  to a neighbouring cell. `M26` shows states with 6+ players alive fall to 1.7%
  of live rounds by t=30 and 0.5% by t=43 -- they run out because they cease to
  exist, so a neutral fallback is honest and the affected mass is negligible.
- `V` is smoothed across `t` within a state before differencing; knots fixed at
  the second boundaries, not estimated. Match-clustered errors throughout.
- Recomputed and versioned with the scorer, never at request time.

### Known limitation, recorded rather than fixed

`kill_order_bonus` is **side-blind** -- it keys on alive counts, not on who is
attacking. `M24` measures time-averaged leverage of **+0.330 for `2v1` against
+0.631 for `1v2`**, a ~2x gap for the same man-advantage held by opposite
sides. Because this spec's factor is a pure ratio, that level difference is
divided out and **nothing models it**.

This is a defect in the kill-order graph, not in the time factor, and fixing it
means refitting the graph -- explicitly out of scope here, and changing two
things at once would make the rescore uninterpretable. Recorded so it is not
rediscovered. `M24` also finds the graph **agrees** with measured leverage at
the top (`1v1` ranks highest, weight 250 against a floor of 40).

### Leakage -- Part 4 is NOT ex-post, and this matters

Part 3 reads `plant_time`, which is future information at the moment of a
pre-plant kill, so it is gated by `use_realized` and the forward yardsticks are
blind to it by construction.

**Part 4 is different.** At a post-plant kill the plant has already happened;
seconds-since-plant, the alive counts and the side are all **known at kill
time**. Nothing here is leakage, nothing is stripped under
`use_realized=False`, and `impact_eval.py`'s forward yardsticks therefore **see
this change in full**.

Consequence: Part 4 carries a **non-inferiority gate** on those yardsticks,
where Part 3 carries none. Replacing a ramp the data contradicts should not
make forward performance worse; if it does, that is a finding about the
yardsticks or the design and must be reported, not tuned around.

### Centring

Separate constant, solved on the post-plant population alone: the
sample-weighted mean of `kill_order_bonus * post_plant_factor` over post-plant
pre-resolution kills matches its value under today's ramp. Death-side residual
reported against the same 2% tolerance as Part 3.

Post-plant and pre-plant are **not** centred jointly. Their populations,
factors and leakage properties all differ, and one constant across both would
let either regime absorb the other's drift.

## Testing

Part 1:
- `is_phantom_plant` accepts the 24 known rounds and rejects the 6
  Elimination-Win rounds with `plant_time > 100s`.
- `attacking_team` reproduces the convention on 1-24 and the verified
  alternation past 24; a table-driven test over sampled real rounds asserts
  agreement with the outcome-derived attacker.
- `seconds_to_plant` returns `None` for unplanted and phantom rounds.

- **The side map is total.** Every round present in `entries` resolves to a
  side; the test fails otherwise. This is the guard against Edit B shipping
  without Edit A.
- An OT round scores identically before and after the `attacking_team` change,
  confirming the `_econ_swing_risk_factor` early return at `impact.py:271`.
- OT rounds appear in **both** the state diagrams and fight-EV after
  migration, not one or the other.
- `credit_events`' wrapper returns `"team-1"`/`"team-2"` strings, not `Team`.

Part 3:
- **The centring gate, on the KILL side, stated once.** The sample-weighted
  mean of `kill_order_bonus * scalar` over affected pre-plant kills equals its
  value under today's flat-1.0 factor, exactly, to solver tolerance. An earlier
  draft of this section specified a *net* invariant contradicting the design
  section's contribution-weighted one; the net version is withdrawn (`M19`) --
  it carries zero weight on 71.7% of the affected kills.
- **The death-side residual is reported and bounded**, not required to be zero:
  `mean(K*T*c*s)/mean(K*T) - 1` within the 2% predeclared tolerance. `M20`
  expects ~+0.7%.
- Monotone non-decreasing in proximity up to the 10s plateau.
- `use_realized=False` returns exactly 1.0 for every pre-plant kill, so the
  ex-ante replay is unchanged from today's behaviour. **This is the leakage
  gate** and it must be exact, not approximate.

Part 4:
- **Kill and death use the same factor** for the same event -- the transfer is
  one number, credited to one player and debited from the other.
- **Side-dependence is real, not incidental:** an attacker kill and a defender
  kill in the same state at the same second receive **different** factors.
  A test asserting they are equal would encode the shipped bug.
- `2v1` and `1v2` factors move in **opposite directions** across `t`, per
  `M24`. This is the single sharpest behavioural test in Part 4 -- if a
  refactor collapses them to a shared shape, this catches it.
- Cells below the 60-observation floor return exactly **1.0**, not a
  neighbouring cell's value.
- The `plant+38..45` flat 1.75 override is **gone**; a kill at plant+39 and one
  at plant+44 in the same state receive different factors.
- Post-resolution kills still return 0.5.
- `use_realized=False` changes **nothing** in Part 4 -- post-plant state is
  known at kill time. This is the mirror of Part 3's leakage gate and must
  also be exact.
- Golden-file test on one fully-scored match containing both regimes.

Per the note in `feedback_plan_execution_test_fixtures`: **verify each
synthetic fixture actually produces the relationship it claims before writing
assertions against it.**

## Out of scope

- Anything econ -- a separate spec
  (`2026-09-04-econ-impact-separate-component-design.md`), **UNBLOCKED
  2026-09-05** and revised again 2026-09-06. The decision this bullet
  previously said it "does not yet record" has been taken: the outcome-fitted
  `w(state)` weights are deleted (`M12b`) and the component is a descriptive
  allocation. Its late regime is now scope-locked to rounds 5-11 / 17-23 and
  its early regime scores rounds 2-4 / 14-16 on a total-wealth readout gated by
  commitment (`M27f`, `M28`). This spec still does not depend on it -- the two
  are separable and take separate version bumps -- but the dependency note is
  no longer accurate and is corrected. Note the claim that econ points
  "opposite" to the current factor was **withdrawn**: `M9` shows it was a
  between-context artifact.
- Refitting the kill-order graph, including the side-blindness `M24` exposes
  (`2v1` +0.330 against `1v2` +0.631). Recorded in Part 4, out of scope here.
- A pre-plant deadline / plant-denial term. The policy conclusion stands, but
  **not on the wording used here before 2026-09-06**: `M7`'s "not elevated" was
  itself withdrawn on full data, where 2v2 late kills in never-planted rounds
  are elevated **+4.9pp [+1.5,+8.3]** (3v3 remains null). The term stays out of
  scope because that effect is small against proximity's 16-30pp (`M3`), not
  because there is no effect.
- Any player-level site-pressure ranking. Measured and unreliable.
- Refitting `FACTOR_WEIGHTS`. Unrelated, already measured at +0.005 AUC, and
  changing two things at once would make the rescore uninterpretable.
- Re-crawling for fresh matches. Worth doing, independent of this.
