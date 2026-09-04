# Econ impact as a separate realized economy-state allocation

**Status:** BLOCKED -- do not implement. The buy-state weights this design
depends on cannot be derived from the measurements available (`M12a`). See
"Blocking issue" below.
**Date:** 2026-09-04

## Purpose

`econ_impact` and `swing_impact` are both `kill_order_bonus x <factor>`. So are
`time_impact` and, empirically, `damage`. That shared multiplicand is why the
four components correlate 0.73-0.90, why fitted weights disagree across
targets, why `econ_impact`'s sign is specification-dependent, and why Stage C's
refit died on an unidentified damage coefficient.

This spec makes econ a **separate number computed from quantities that are not
functions of `kill_order_bonus`**. That is the point: not a better econ factor,
a differently-derived one.

**Units, stated precisely, because an earlier draft got this wrong.** That
draft called the component "credits-denominated". It is not. The player formula
multiplies a round-level scaler by a *share* -- and committed credits appear in
both numerator and denominator of that share, so **they cancel**. The realized
denial factor is itself dimensionless (`0.5 + 0.2 * denied_count`, range
0.5-1.5, `impact.py:360`). The component is therefore a **dimensionless
allocation of a round-level economy-state quantity**, mapped into Impact units
by `ECON_SCALE`.

The component must pick exactly one of these and say so:
(a) credits removed, (b) next-round buy denial, (c) an Impact-scaled allocation
of (a) or (b). This spec chooses **(c) an allocation of (b)**. The equations
below are normative; any implementation that silently mixes them is wrong.

## Blocking issue -- the weight derivation does not survive adjustment

**Added 2026-09-04 after external review.** Section 3 fits `w(state)` from
`M12`. `M12a` re-ran that measurement holding **enemies killed** and **round-N
outcome** fixed, and the effect does not survive:

- `corr(destroyed, enemies killed) = +0.652`, `corr(destroyed, won round N) = +0.474`
- among teams that *won* round N and wiped the enemy, more destruction predicts
  winning N+1 **less** (-3.0pp, interval excluding zero)
- within the three buy-state bands, the fitted weights would take the **wrong
  sign in two of three** (broke -4.2pp, full -9.8pp, partial +2.8pp)

This matters more than a normal null. The redesign exists to escape the
components' collinearity with kill count and leverage; fitting `w(state)` from
an unadjusted `M12` would have **reintroduced that correlation through the
weights themselves**, while appearing to be an independent economic signal.

**What survives:** `M10` (destroyed value is largely a measure of enemy
committed wealth) and `M11` (enemy next-round buy state associates with winning
that round) are unaffected -- they are descriptive and were not the confounded
step. The confounded step is the link from *a player's destruction* to *the next
round's outcome*.

**What is required before this spec can be implemented:**

1. An estimand for the econ component that is not a function of how many
   enemies were killed -- the current `magnitude` is close to a linear function
   of kill count at fixed enemy wealth.
2. Weights derived from a measurement that adjusts for kill count, round
   outcome, and man-advantage trajectory, or an argument that the component
   should not be outcome-fitted at all and is a *descriptive* allocation of
   credits destroyed with no predictive claim attached.
3. A decision on `swing_impact` absorption, which section 8 still leaves open.

Option 2's second branch is worth serious consideration: an econ term that
simply *describes* economic damage, scaled by nothing fitted, makes no
predictive claim and therefore cannot be undermined by one. That would be a
smaller and more defensible component than the one specified below.

**Everything below is retained as the design as it stood, not as an approved
plan.**

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
| The killer's own loadout is dropped | `M9` | -- |
| The per-kill quantity is the victim's *committed* value | `M10` | a threshold effect -- that reading was withdrawn |
| The round scaler is the enemy's next-round buy state | `M11` | that the kills *caused* that buy state; it is jointly determined by prior cash, round result, loss bonus, survival, recovery and purchase choice |
| One regime, conditioned on buy state, at all rounds | `M12` | the exact form of the mild early/late taper |
| Buy state is part of the estimand, not a nuisance control | `M12`, `M13` | -- |
| The realized denial measure is pulled out of the swing path | `M14` | that removing the ex-ante half is harmless -- see the non-inferiority gate |

**The naming rule this table enforces:** this component is a *realized
economy-state allocation*. Phrases like "what the destruction achieved" are
causal and are not supported by `M11`. Do not use them in code comments, commit
messages or UI copy.

## Constraints and premises

### This is ex-post. It is attribution, not prediction.

Everything here reads round N+1. Same leakage boundary as the plant-window
spec: the `use_realized` parameter gates it, shipped scoring passes `True`,
`impact_eval.py` and `impact_eval_cache.py` pass `False`.

**But "the forward yardsticks are blind to this change" is only half true, and
an earlier draft stated it without qualification.** Today's ex-ante mode drops
*only* the realized swing term (`impact.py:502-516`); `econ_differential_factor`
reads current-round loadouts (`:474-476`) and the ex-ante swing factor both
survive into it. This proposal removes both from the scoring path and makes the
new component exactly 0 under `use_realized=False`.

So forward evaluation is blind to the **new** term but **highly sensitive to
deleting the old ones** -- ex-ante mode would lose econ information it currently
has. That makes the forward yardsticks a required **non-inferiority gate on the
deletion decision**, not a metric to be waved away. The same qualification
applies to any post-plant retuning in the time spec, since post-plant state is
known at kill time and is therefore *not* leakage.

Accepted explicitly as a product decision: the score describes what happened,
and economic damage is only observable after the fact.

### Attribution must be a share, not a sum

A per-player sum over their kills rises with kill count and re-correlates with
every other component -- exactly how `damage` ended up 0.869 correlated with
the leverage aggregate and killed Stage C. **The player term is a share of a
round-level quantity**, which does not carry that scaling.

### Boundary behaviour must be enumerated, not inherited

Pistol rounds 1 and 13 are 0 -- no prior economy to damage.

**The reused helper's neutral value is not a safe default here.**
`_realized_econ_swing_factor` returns a neutral `1.0` for rounds 12 and 24,
overtime, and missing next-round data (`impact.py:342-350`). `1.0` is neutral
for a *multiplicative* factor but is a *positive quantity* when reused as a
denial magnitude, which would silently award econ credit where none was earned.

Every one of these needs an explicit, tested value:
rounds 12 and 24 (halftime reset, no next-round link); the final round of a
match; surrendered and incomplete matches; overtime; missing
`round_player_stats`; an exactly tied wealth differential; and teams already
ahead or tied when a "flip" is evaluated.

## The design

### Notation and units

| symbol | meaning | units |
|---|---|---|
| `C(v)` | victim `v`'s committed value in the round | credits |
| `R` | fixed full-buy reference, `5 * 3900 = 19500` | credits |
| `removed(T)` | sum of `C(v)` over enemies of team `T` killed this round | credits |
| `magnitude(T)` | `removed(T) / R` | dimensionless, ~0..1 |
| `w(state)` | buy-state weight from `M12` | dimensionless |
| `denial(T)` | realized next-round denial | dimensionless |
| `ECON_SCALE` | Impact points per unit of allocation | Impact points per dimensionless unit |

### 1. Per-kill raw quantity: the victim's committed value

```python
C(v) = max(0, loadout(v) - free_ability_credits(agent(v)))
```

`free_ability_credits` is in `app/scoring/agent_economy.py` and strips the
phantom value tracker.gg assigns a free signature charge. **The killer's loadout
does not appear** (`M9`).

### 2. Team-round magnitude: ABSOLUTE, against a fixed reference

```python
magnitude(T) = removed(T) / R          # R = 19500, a constant
```

**This must not be normalised by what the enemy happened to hold.** An earlier
draft defined `destruction_share = removed / enemy_total_committed`, which is
wrong and self-contradictory: by `M10`, wiping a saving team removes 1,869 of
1,890 credits and wiping a full-buy team removes 10,767 of 10,861 -- **shares of
0.989 and 0.991, indistinguishable** -- while the associated next-round win
rates are 36.3% and 66.2%. A share normalises away precisely the quantity `M10`
identifies as carrying the signal, and makes the required test in Testing
(a saved round yields ~0 credit) impossible to satisfy.

A fixed reference keeps the magnitude proportional to credits actually removed.
`R` is a constant, not fitted; it exists only to put the term on a sane scale.

### 3. Buy-state weight

`M12` shows the same destruction is worth about twice as much against a partial
or full buy as against an already-broken team. The enemy's buy state is
**entering round N** (their full-buy count in the round where the kills happen),
not N+1:

| enemy full-buys entering round N | `w(state)` |
|---|---|
| 0-1 (broke) | fitted, ~0.5 |
| 2-3 (partial) | fitted, ~1.0 |
| 4-5 (full) | fitted, ~1.0 |

Fitted from `M12`'s nine cells with intervals. A mild early/late round taper is
permitted (`M12` shows ~+23pp early to ~+13pp late) but is **optional** -- the
flat version is defensible and simpler. **No hard round-number boundary**; the
evidence for one was an artifact (`M12`, `M13`).

### 4. Realized denial

```python
denial(T) = 0.5 + 0.2 * enemy_players_below_full_buy_next_round     # 0.5 .. 1.5
```

This is `_realized_econ_swing_factor`'s existing body (`impact.py:336-360`),
extracted from the swing path. It **no longer passes through
`_combine_swing_factors`**, which returns a neutral 1.0 for 44.9% of team-rounds
and, when it does not, usually lets the wider-ranging ex-ante factor dominate
(`M14`). Both functions remain defined for `kill_order_leverage.py`.

### 5. Team-round econ quantity

```python
econ_round(T) = magnitude(T) * w(state(T)) * denial(T)
```

### 6. Player attribution -- a share, but only of the ALLOCATION

```python
credit(p) = econ_round(T) * C_removed_by(p) / removed(T)      if removed(T) > 0 else 0
debit(p)  = econ_round(opp) * C_lost_by(p)  / removed(opp)    if removed(opp) > 0 else 0
econ_component(p) = ECON_SCALE * (credit(p) - debit(p))
```

The share appears **here and only here** -- dividing a team-level magnitude
among the players who produced it. It is not used to compute the magnitude
itself, which is the error corrected in section 2. A share is required at this
step because a per-player *sum* rises with kill count and re-correlates with
every other component, which is how `damage` reached 0.869 against the leverage
aggregate and killed Stage C.

By construction `sum(credit(p)) = econ_round(T)` over the team.

### 7. Zero-sum -- it IS, and an earlier draft said otherwise

The equations in section 6 are **exactly zero-sum** over the ten players in a
round. A victim's debit is scaled by `econ_round(opp)`, where `opp` is the team
that produced the removal, and the debit shares sum to 1 over that team's
losses -- so `sum(credit) over T == econ_round(T) == sum(debit) over the enemy`.

A previous version of this section claimed the component was deliberately *not*
zero-sum, on the reasoning that credit and debit use different states and denial
values. That is wrong: they use the *same* `econ_round` term, viewed from the
two sides. **The prose was describing a design the equations do not implement.**

Zero-sum is accepted rather than worked around: the component is a transfer, it
distinguishes players within a round, and it gives a clean testable invariant
(the ten values sum to zero).

**Self-kills and environmental deaths are therefore excluded entirely** -- no
credit and no debit. They are not transfers: no enemy gains from them, and they
do not appear in `removed(opp)`, so the section 6 denominator cannot express
them. An earlier boundary table assigned them a debit, which is not
implementable through that denominator. The cost is that a player who falls off
the map loses their team real economic value and is not charged for it; that is
a known simplification, not an oversight.

### 8. Top-level structure

```
impact = damage
       + leverage_component     # kill_order_bonus * time_factor  (time spec)
       + econ_component         # section 6
```

replacing today's `damages + mean(econ, time, swing)`, in which all three terms
share `kill_order_bonus`.

**Open decision, not settled here: whether this absorbs `swing_impact`.**
Absorbing it completes the decollinearisation; not absorbing leaves a
`kill_order_bonus` multiplicand in place. Larger than "replace the econ term",
so it must be confirmed rather than assumed.

### 9. ECON_SCALE

A single constant chosen so `econ_component`'s standard deviation over the full
dataset equals `time_impact`'s current standard deviation -- the component
enters with influence comparable to what it replaces, rather than a hand-picked
weight. Recorded with its derivation and gated by a test.

### 10. Boundary behaviour -- decisions, not just a list of cases

`_realized_econ_swing_factor` returns a neutral `1.0` for rounds 12, 24,
overtime and missing next-round data (`impact.py:342-350`). `1.0` is neutral for
a *multiplicative* factor but is a *positive quantity* when reused as denial,
which would award credit where none was earned. Hence explicit values:

| case | `econ_component` |
|---|---|
| rounds 1 and 13 (pistol) | **0** -- no prior economy to damage |
| rounds 12 and 24 (halftime) | **0** -- economy resets, no next-round link |
| final round of a match | **0** -- no next round |
| overtime | **0** in v1; OT economy is ~uniform (`M6`), so there is little to measure |
| surrendered / incomplete match | **0**, consistent with existing surrender handling |
| missing `round_player_stats` | **0**, and counted in a diagnostic |
| `removed(T) == 0` (no enemy killed) | **0** by the guard in section 6 |
| self-kill / environmental death | **0** -- excluded entirely; see section 7 |
| victim `C(v) == 0` | contributes 0; not an error |

### 11. The weapon-pickup extension (data-gated, ships inert)

A kill near the victim lets the killer take their weapon, converting *destroyed*
value into *transferred* value. Designed in now, shipped disabled.

- Optional `KillEvent` distance field, populated only when the source provides
  it, `NULL` otherwise.
- `pickup_bonus(kill)` returns **0** when distance is `NULL`. The feature must be
  a no-op on all 487,844 currently-ingested rows.
- `PICKUP_BONUS_ENABLED = False` by default.

**The value-differential rule an earlier draft proposed is withdrawn as not
computable.** `KillEvent.weapon` is the weapon the *killer used*
(`models/kill_event.py:18`); the victim's held weapon at death is not stored,
and neither is the killer's weapon at that instant. Distance alone establishes
neither the dropped weapon's value nor whether anyone picked it up.

**Unverified:** whether tracker.gg's response carries location at all. The
adapter keeps only `weaponName`, `roundTime` and `assistants`
(`trackergg_browserstate_source.py:312-325`). Confirm with one captured match
(`scripts/launch_trackergg_chrome.ps1`, then
`scripts/capture_trackergg_state.py`) **before** building the adapter side.
Backfill would mean re-crawling ~3,124 matches at 5-12s pacing -- roughly 7
hours -- and is not proposed.

Validation when picked up: a ~30-50 match sample to confirm the field exists and
its units, then synthetic fixtures. Per `feedback_plan_execution_test_fixtures`,
**verify each fixture actually produces the relationship it claims before
writing assertions against it.**

## Persistence and rollout

- **Migration `0008`**: add `econ_component` (and `econ_pickup` for the gated
  extension) to `impact_scores` **alongside** the existing `econ_impact` and
  `swing_impact` rather than redefining them. The evaluation harness reads
  those columns by name; silently changing their meaning would invalidate
  every stored comparison. Old columns are written as `0` under the new
  scheme and dropped in a later migration once nothing reads them.
- `IMPACT_CALCULATION_VERSION` bump, folding mechanically into
  `player_view_cache.cache_version()`. **The two specs must not both claim
  1 -> 2.** Whichever ships first takes 2; the second takes 3. If they ship
  together in one bump, the rescore cannot attribute any movement in the
  numbers to either change -- separate bumps are recommended for that reason.
- Full rescore: `scripts/recompute_impact.py` then
  `scripts/recompute_player_views.py`.
- `.impact_eval_cache/` self-invalidates on the version key.
- Stage C artifacts become non-comparable (`kill_order_refit.py:1837` stamps
  results with `IMPACT_CALCULATION_VERSION`). Costs nothing -- every Stage C
  candidate was non-deployable -- but do not quote those numbers afterwards.

**If the plant-window spec lands in the same version bump**, the rescore
cannot attribute movement to either change. Recommend separate bumps unless
the rescore cost is prohibitive.

## Validation

- **NOT a validation, and an earlier draft wrongly listed it as the primary
  one:** checking that `econ_component` is monotone in next-round full-buy
  count. The component *uses* that quantity as its scaler, so monotonicity is
  guaranteed by construction. It belongs in the unit tests as a
  reconstruction check, and nowhere else.
- **NOT construct validation either:** association with next-round *purchasing
  power*. An earlier draft proposed this as primary. It is computed from the
  same next-round loadout data that `denial()` is built from, so covariate
  adjustment does not remove the circularity -- it is the scaler measured a
  second way.
- **Construct validation (primary), on an endpoint the component does not
  contain:** association with **round N+2** purchasing power, and with the
  round N+1 outcome computed *without* reference to `denial()`. Adjust for
  pre-round economy, round result, survival count, side, score differential,
  map and patch era. Report with intervals.
- **Temporal stability check -- NOT external validation.** The whole dataset
  has already been used to choose this component's structure, so splitting it by
  date afterwards does not create an untouched holdout. Report it as a stability
  check. Genuine external validation needs matches not yet crawled.
- **Naming:** this is a *realized economy-state allocation*, not causal
  attribution. Do not write "what the destruction achieved" in code comments or
  UI copy without a counterfactual analysis to support it.
- **Collinearity, and this is the whole point:** `corr(econ_component,
  leverage aggregate)` must come in **well below** the 0.73-0.90 the current
  components show and below `damage`'s 0.869. If it does not, the
  redimensioning failed and the design should be reconsidered before shipping.
- **Distributional:** mean player-round Impact should move by less than the
  scale-reconciliation tolerance.
- **Reported, not gated:** `impact_eval.py`'s forward yardsticks, with the
  leakage caveat attached.

## Testing

- Committed value strips free-ability credits per agent, and never goes
  negative.
- A round where the enemy fully saved yields ~0 econ credit despite a 5-kill
  round -- the Q1 case, and the single most important behavioural test here.
- Two rounds with identical credits destroyed but different resulting
  full-buy counts produce **different** econ credit -- the (B) finding.
- Attribution shares sum to the team's round total, within rounding.
- Rounds 1 and 13 produce exactly 0.
- `use_realized=False` produces exactly 0 econ component for every row. **This
  is the leakage gate and must be exact.**
- `pickup_bonus` returns 0 for every row with `NULL` distance -- i.e. the
  entire current dataset is unaffected.
- Golden-file test on one fully-scored match.

## Out of scope

- The `_time_factor` / plant-proximity redesign -- its own spec,
  `2026-09-03-plant-window-and-time-factor-design.md`.
- Refitting `FACTOR_WEIGHTS`. Changing two things at once makes the rescore
  uninterpretable.
- Backfilling distance to existing matches.
- Any econ term keyed on the killer's loadout. Measured, and dropped.
- A threshold term on raw destruction. The evidence for it was a confound.
