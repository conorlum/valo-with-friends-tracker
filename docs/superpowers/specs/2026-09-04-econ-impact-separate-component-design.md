# Econ impact as a separate, credits-denominated component

**Status:** awaiting human review
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

### 1. Per-kill raw quantity: the victim's committed value

```python
committed_value(round, victim) = max(0, loadout - free_ability_credits(agent))
```

`free_ability_credits` is already in `app/scoring/agent_economy.py` and strips
the phantom credit value tracker.gg assigns a free signature charge. **The
killer's loadout does not appear.**

### 2. Per-round, per-team scaler: one rule, conditioned on the enemy's buy state

**Single regime.** An earlier draft split rounds 2-4/14-16 (raw destruction)
from 5+/17+ (econ flip). That split rested on a decay which was a conditioning
artifact (`M12`, `M13`) and is removed. The econ-flip formulation
is not needed and is dropped entirely.

The scaler has two inputs:

- **`destruction_share`** -- the enemy's committed value your team removed,
  over what they had.
- **`enemy_buy_state`** -- their full-buy count entering the round, bucketed
  broke (0-1) / partial (2-3) / full (4-5).

Destruction is worth roughly **twice as much against a partial or full buy as
against an already-broken team** (+22.6 / +23.8 vs +10.9pp early). The buy
state is part of the estimand, not a nuisance control.

A mild round taper is permitted (~+23pp early to ~+13pp late) but must be
fitted from `M12` with intervals, not hand-set, and it is **optional**
-- the flat version is defensible and simpler. Do not reintroduce a hard
round-number boundary; the evidence for one was an artifact.

`_realized_econ_swing_factor`'s body supplies the realized denial measure. It is
extracted from the swing path and **no longer passes through
`_combine_swing_factors`**, which the 44.9%-neutral measurement argues for. It
remains defined for `kill_order_leverage.py`.
- `econ_tier_name` and `FORCE_THRESHOLD` are untouched -- public and consumed
  by two services.
- `_realized_econ_swing_factor` keeps its name and signature (a test
  monkeypatches it) but its result is routed to the new econ scaler instead of
  into `_combine_swing_factors`.
- `_econ_swing_risk_factor` and `_combine_swing_factors` remain defined. If the
  swing-absorption decision is taken they simply stop being called by the
  scoring path; `kill_order_leverage.py` continues to use the former.
- `FACTOR_WEIGHTS` loses its `econ` and `swing` keys.
- New: `app/scoring/econ_value.py` for committed-value, denial and attribution
  maths, keeping `impact.py` from growing further. It is already 721 lines.

**`tests/test_impact_exante_swing.py` already asserts that
`_realized_econ_swing_factor` is not called when `use_realized_swing=False`.**
That is the existing leakage gate and the new econ component must extend it
rather than route around it.

## Persistence and rollout

- **Migration `0008`**: add `econ_component` (and `econ_pickup` for the gated
  extension) to `impact_scores` **alongside** the existing `econ_impact` and
  `swing_impact` rather than redefining them. The evaluation harness reads
  those columns by name; silently changing their meaning would invalidate
  every stored comparison. Old columns are written as `0` under the new
  scheme and dropped in a later migration once nothing reads them.
- `IMPACT_CALCULATION_VERSION` 1 -> 2, folding mechanically into
  `player_view_cache.cache_version()`.
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
- **Construct validation (primary):** association between `econ_component` and
  next-round purchasing power **after adjusting** for pre-round economy, round
  result, survival count, side, score differential, map and patch era. Next-round
  loadout is caused by prior cash, the round result, the loss-bonus ladder,
  survival, weapon recovery, teammate drops and purchase choice -- not only by
  the kills being credited. Report with intervals.
- **External validation:** held-out association with later outcomes not used to
  build the component, on a temporal split.
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
