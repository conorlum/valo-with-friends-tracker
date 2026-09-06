# Econ impact as a separate realized economy-state allocation

**Status:** UNBLOCKED 2026-09-05, awaiting human review. The block was not
cleared by finding better weights -- it was cleared by **changing the
quantity**. See "How the block was resolved". The early rounds stopped being
zero on 2026-09-06; the late regime is scope-locked and unchanged.
**Date:** 2026-09-04, substantially revised 2026-09-05 and 2026-09-06

**Revision note (2026-09-05).** This spec was blocked because `w(state)` was to
be fitted from `M12`, and `M12a` showed that effect was largely kill count.
Three further measurements changed the picture:

- `M12b` -- adjusted, the buy-state ordering **inverts**. Destruction is worth
  most against **broke** teams and is **negative** against full-buy teams. The
  proposed `w(state)` was not merely unfittable, it pointed the wrong way.
- `M12c` -- `M12a` holds enemies-killed fixed, which **controls a mediator** if
  the economic path runs through kill count. It answered the enemy-wealth
  question, not the kill-count one.
- `M12d` -- conditioned on the enemy's **bank**, four kills deny 0.60 armed
  enemies against a committed team and 0.09 against a cash-rich one.

The component is therefore rebuilt around **guns the enemy could not replace**
rather than credits removed. Sections 2, 3 and 5 change; 1, 4, 6, 7, 9, 10 and
11 stand.

**Revision note (2026-09-06) -- the early rounds are no longer zero.** The
2026-09-05 draft scored rounds 2-4 / 14-16 at zero, reasoning that the early
pattern was the acting team's own save decision. Four measurements overturned
that:

- `M27` -- conditioning on the round outcome pins the loss-bonus ladder that
  made early readouts unusable. At round 3 the denial is large, monotone and
  accelerating (full-buys R4 **-1.54**, win R4 **-12.11pp**), and survives
  stratification by enemy wealth.
- `M27e` -- the round-2 denial is **deferred**: it drains the bank (-841.91)
  and the bill arrives at round 4, conditional on them losing round 3.
- `M27f` -- the round-2 sign anomaly is a **pooling artifact** of savers and
  buyers. Gated on commitment it dissolves entirely.
- `M28` -- a **total-wealth** readout is negative and excludes zero at all ten
  round positions. Early denial is 97% bank and 3% equipment, which is why a
  full-buy-count readout inverts there.

**The late regime is unchanged and is now scope-locked** (see the box at the
head of "The design"). Section 5a is rewritten; the scope lock and section 5b's
regime table are new.

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

## Blocking issue (HISTORICAL -- resolved 2026-09-05, kept as the record)

> This section describes the block as it stood on 2026-09-04. It is retained
> because the reasoning is still correct and the trap it names is still live for
> anyone who reopens `M12`. **The resolution is the section immediately after
> it.** Nothing here is an outstanding blocker.

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

## How the block was resolved

Not by rescuing `w(state)`. By replacing the quantity it weighted.

**The diagnosis.** `removed(T)` conflates two things that run in **opposite
directions**, which is why every fit of it produced a different sign:

| axis | what varies | rounds 2-4 | rounds 8-11 |
|---|---|---|---|
| **wealth** -- at fixed kill count, how rich were the enemies (`M10`, `M12a`, `M12b`) | +10.4pp | -5.2pp |
| **kill count** -- how many did you take (`M12c`) | -6.8pp | +6.8pp |

`M12` summed them and got a middling positive. `M12a` held kill count fixed and
found the wealth axis dead. Neither measured the axis that carries the
mechanism.

**The mechanism, and the quantity it implies.** A kill destroys a gun. Whether
that *matters* depends on whether the victim's team can buy another one, which
depends on their **bank**, not on the gun's price. `M12d` measures it directly:

```
(>=4 kills) - (<=1 kill), rounds 5-11 / 17-23, teams that lost round N
enemy bank <500     enemy ARMED next  -0.60 [-0.68,-0.52]
enemy bank >=1500   enemy ARMED next  -0.09 [-0.10,-0.08]
```

Same four kills, comparable credits nominally destroyed, **6.7x** the denial.
So the component's quantity is **armament the enemy could not replace**, and
`removed(T)` is dropped.

**Why this satisfies the three requirements the block listed.**

1. *An estimand that is not a function of how many enemies were killed.* It is
   kills **x replaceability**, and it saturates -- once they are broken, another
   kill adds nothing. `removed(T)` just keeps summing, which is how it reached
   +0.652 with kill count.
2. *Weights from an adjusted measurement, or an argument against
   outcome-fitting.* Neither is needed: the component **describes a realized
   state** (how many of them are armed next round) and fits nothing. It cannot
   be undermined by a predictive null because it makes no prediction.
3. *A decision on `swing_impact` absorption.* **Resolved 2026-09-05** -- swing
   leaves the Impact formula. Its realized half is absorbed as this component;
   its ex-ante half is deleted. `swing` takes no man-advantage input, so it was
   always an econ term under a misleading name. See section 8a-8d.

**What this costs.** The buy-state gradient goes. `M12b` says it was pointing
the wrong way, and taking `w(full) < 0` literally would have **debited a player
for killing a rich enemy** -- which collides with the standing constraint that
no kill is ever worth negative Impact.

**Honest limits, recorded here rather than in a footnote.** `M12d`'s mediator
is cleanly graded by bank with non-overlapping intervals; its **outcome is
not** -- the win-rate differences run +4.93 / +5.10 / +3.27pp and the committed
cell spans zero on n=2,100. The mechanism is well evidenced; the payoff to it is
not. This component is therefore justified as **description of what happened**,
which is the register the whole spec claims, and must not be re-argued as
prediction.

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
| ~~One regime, conditioned on buy state, at all rounds~~ **WITHDRAWN** | `M12b` | -- the adjusted ordering inverts; the proposed `w(state)` pointed the wrong way |
| The quantity is armament the enemy could not replace, not credits removed | `M12d` | that the denial *caused* the next round's result -- the mediator is cleanly graded, the outcome is not |
| Kill count is the exposure, not a nuisance control | `M12c` | that `M12a` was wrong -- it answers the enemy-wealth question correctly, just not this one |
| ~~**Two regimes, split at round 5 / 17**, the early one scoring 0~~ **SUPERSEDED 2026-09-06** | `M12b`, `M12c`, `M12d` | -- the zero was withdrawn by `M27`/`M28`; two regimes remain but both now score |
| The early rounds are scored, on a **total-wealth** readout | `M28` | causation, and that the *outcome* follows -- it separates at only 5 of 10 round positions |
| The early regime is gated on the victim team's **commitment** | `M27f` | that commitment *causes* the difference; buy-in is chosen by the team and correlates with prior rounds, role and score state |
| The round-2 denial is real but paid at N+2 | `M27e` | causation; the exposure and the conditioning split are two rounds apart |
| Round 4 / 16 belongs in the early regime | `M28` | -- `M27`'s pistol conditioning reaches only rounds 2 and 3 |
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

### SCOPE LOCK -- read this before changing anything below

This box exists because the scope of the denial regime has been re-derived from
scattered text more than once, and re-litigated each time. It is stated here in
one place so it does not have to be reconstructed again.

> **The denial regime covers rounds 5-11 and 17-23. Nothing else.**
>
> Its quantity is
> `denial(T) = 0.5 + 0.2 * (enemy players whose round-N+1 loadout is below
> FULL_BUY_THRESHOLD = 4200)`, allocated among the killers by their share of
> credits removed (section 6).
>
> It is a **description of a realized state**, not a prediction. `M12d`'s bank
> stratification is how the mechanism was **verified**, not a term in the
> formula (section 5b).
>
> **It is not to be extended to other rounds without its own measurement.**
> `M28` measures a *different* readout for the early rounds; it does not
> re-open this one.

Which rounds are scored, and by what:

| rounds | `econ_round(T)` | basis |
|---|---|---|
| 1, 13 (pistol) | **0** | no prior economy to damage |
| **2-4, 14-16** | `commitment(opp) * denial_early(T)` | `M27f`, `M28`, `M27e` -- section 5a |
| **5-11, 17-23** | `denial(T)` -- **LOCKED** | `M12d` -- section 4 |
| 12, 24 (halftime) | **0** | economy resets, no next-round link |
| overtime | **0** | v1; OT economy is ~uniform (`M6`) |
| final round of a match | **0** | no next round |

Every player still earns full Impact in every round through `damage` and the
leverage component. A zero here means **one of three components abstains**, not
that the round is unscored.

### Notation and units

| symbol | meaning | units |
|---|---|---|
| `C(v)` | victim `v`'s committed value in the round | credits |
| `R` | fixed full-buy reference, `5 * 3900 = 19500` | credits |
| `removed(T)` | sum of `C(v)` over enemies of team `T` killed this round | credits |
| `magnitude(T)` | `removed(T) / R` -- **demoted 2026-09-05**, survives only inside section 6's attribution share | dimensionless, ~0..1 |
| ~~`w(state)`~~ | **REMOVED 2026-09-05** (`M12b` -- the ordering inverts) | -- |
| `denial(T)` | realized next-round denial -- **now the component itself**, not a multiplier | dimensionless |
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

**DEMOTED 2026-09-05 -- `magnitude` is no longer the component's quantity.**
The reasoning above is correct as far as it goes: a *share* is the wrong shape,
and the fixed reference fixed that. But `M12d` shows the whole variable is
measuring the wrong thing. Credits removed is the **price** of what you
destroyed; what matters is whether they can **replace** it, which depends on
their bank and not on the price tag:

```
(>=4 kills) - (<=1 kill), rounds 5-11 / 17-23, teams that lost round N
enemy bank <500     enemy ARMED next  -0.60 [-0.68,-0.52]
enemy bank >=1500   enemy ARMED next  -0.09 [-0.10,-0.08]
```

`magnitude` cannot distinguish those two cases -- comparable credits removed,
6.7x the denial. It is retained only inside section 6's attribution share,
where it apportions a team quantity among the players who produced it and its
absolute level cancels. It no longer appears in `econ_round`.

### 3. ~~Buy-state weight~~ -- REMOVED 2026-09-05

The fitted `w(state)` of ~0.5 / 1.0 / 1.0 is **deleted**, not re-estimated.

`M12a` showed the gradient it was to be fitted from was largely kill count.
`M12b` then showed that once kill count and round-N outcome are held fixed the
ordering **inverts** -- destruction is worth most against **broke** teams
(+15.32pp [+13.58,+17.21] in rounds 2-4) and is **negative** against full-buy
teams (-4.23 / -4.50 / -4.74 across the three round groups). The original
weights had the sign backwards in two of three bands.

Fitting the inverted version was considered and rejected. `w(full) < 0` means
the component **debits a player for killing a rich enemy**, which is
indefensible as product behaviour and collides with the standing constraint that
no kill is ever worth negative Impact.

There is no fitted parameter anywhere in this component. That is deliberate: it
is what makes the design immune to the class of null that blocked it.

**What replaces it is not a weight but a different quantity** -- see section 4,
now the core of the component rather than a multiplier on it.

### 4. Realized denial

```python
denial(T) = 0.5 + 0.2 * enemy_players_below_full_buy_next_round     # 0.5 .. 1.5
```

This is `_realized_econ_swing_factor`'s existing body (`impact.py:336-360`),
extracted from the swing path. It **no longer passes through
`_combine_swing_factors`**, which returns a neutral 1.0 for 44.9% of team-rounds
and, when it does not, usually lets the wider-ranging ex-ante factor dominate
(`M14`). Both functions remain defined for `kill_order_leverage.py`.

### 5. Team-round econ quantity -- REWRITTEN 2026-09-05, REVISED 2026-09-06

```python
econ_round(T) = denial(T)                              # rounds 5-11 / 17-23  -- LOCKED
econ_round(T) = commitment(opp) * denial_early(T)      # rounds 2-4 / 14-16
econ_round(T) = 0                                      # pistols, halftime, OT, final round
```

`magnitude` and `w(state)` are both gone. The late regime **is** the realized
denial, unchanged and locked (see the scope lock above). The early regime is
new, and is the subject of `5a`.

### 5a. The early regime -- REWRITTEN 2026-09-06, it is no longer zero

**The previous version of this section scored rounds 2-4 / 14-16 at zero.
That is withdrawn.** It rested on `M12c`'s observation that the enemy's
next-round full-buy count barely moves early (2.66 -> 3.14 across 0-5 kills)
and concluded the early pattern was the acting team's own save decision. The
first half was right about *that readout*; the conclusion was wrong. The
blanket zero was a boundary drawn where no working readout had been found, not
where none exists.

**Two measurements found one.**

**(i) The readout was wrong, not the rounds.** `M28` tested total wealth
(`loadout + remaining`) in round N+1 against a rule declared before running --
it replaces a regime split only if it is negative and excludes zero at *every*
round position. It is, at all ten, monotone in every band:

| rounds | total wealth denied | loadout share | bank share |
|---|---|---|---|
| 2-4 | -1294 [-1337,-1248] | 3% | **97%** |
| 5-7 | -2067 [-2127,-2004] | 12% | 88% |
| 8-11 | -2074 [-2139,-2008] | 10% | 90% |

The denial early is **97% bank and 3% equipment**. A readout keyed on full-buy
*count* -- which is what the late regime uses -- reads only the equipment
account, which is why it inverts early and works late. Nothing was wrong with
the rounds; the instrument was measuring the wrong pocket.

**(ii) It has to be gated on what they committed.** Both `M27`(a) and `M28`
show the round-2 *outcome* running the wrong way (+3.45pp and +11.74pp on
winning round 3). `M27f` shows this is a **pooling artifact**, and dissolves it
by conditioning on the victim team's own round-2 loadout:

| stratum | mean R2 loadout | wealth denied | win R3 | win R4 |
|---|---|---|---|---|
| SAVED <2570 | 1549 | -737 | **+19.70pp** [+15.06,+24.80] | -1.97pp spans 0 |
| LIGHT 2570-3100 | 2851 | -1006 | +0.37pp spans 0 | **-7.88pp** [-13.43,-2.38] |
| BOUGHT IN >=3100 | 3372 | **-1309** | -0.48pp spans 0 | **-6.60pp** [-11.69,-1.43] |

The whole anomaly is the saving stratum. **You can only deny what they bought**:
the denial scales monotonically with their commitment, and the round-4 outcome
is correctly signed and excludes zero in both bought-in strata while staying
null for savers.

**Why the payoff is at N+2 and not N+1.** `M27e` gives the mechanism. A round-2
death forces a round-3 rebuy that drains the bank by **-841.91**
[-880.96,-801.84]; the bill arrives in round 4, and only for teams that then
lost round 3 (full-buys R4 **-1.24**, win R4 **-7.42pp**) rather than won it
(-0.17, -2.30pp spanning zero). The drain is the same in both arms (-830
against -854) while only the losing arm pays, which is what makes a pure
selection reading hard to sustain.

**The rule.**

```python
denial_early(T)  = f(enemy total wealth in round N+1)      # loadout + remaining
commitment(opp)  = g(opp's committed value in round N)     # gates on what they bought
```

Two things are **decided** here: the early readout is total wealth, not
full-buy count; and it is gated by the victim team's commitment in round N.
Both follow directly from `M28` and `M27f`.

Two things are **not** decided and must not be invented during implementation.
The exact mappings `f` and `g` are **policy constants on the same footing as
`ECON_SCALE`** -- chosen, reported with sensitivity across a predeclared grid,
never fitted to an outcome. `g` must send a fully-saving team to approximately
zero, which is the behavioural test in Testing. The standing constraint that no
kill is worth negative Impact requires both to stay non-negative.

**The component is still built on the mediator, never the outcome.** The win-rate
columns above are mechanism evidence and appear nowhere in the formula. That is
what preserves "no fitted parameter anywhere in this component", and it is why
`M28`'s outcome column separating at only 5 of 10 positions does not undermine
the design: this is `M12d`'s situation restated -- **the mediator is cleanly
graded, the outcome is not** -- and the component describes the mediator.

**Known and accepted.** The identification in `M27`/`M27f`/`M28` conditions on
the victim team having *won* round N, which pins the loss-bonus ladder (`M13`,
a ~3 full-buy spread fixed before round 4 begins). **The component does not
condition on the round outcome**, so it inherits ladder exposure that the
measurement closed. This is a pre-existing property of the late regime too --
`denial(T)` counts below-full-buy enemies whatever made them poor -- and is
recorded here rather than silently carried.

Pistol rounds 1 and 13 remain 0 for the original reason: no prior economy to
damage.

**Round 4 / 16 is included in the early regime on `M28`'s evidence** (position
4: total wealth -2099 [-2184,-2022], win N+1 -4.00pp [-7.12,-0.67]), not on
`M27`'s, whose pistol conditioning only reaches rounds 2 and 3.

### 5b. What the component does NOT condition on, and why

`M12d` conditions on the enemy's **bank** in round N, and the denial is 6.7x
larger against a committed team than a cash-rich one. That gradient is **not**
re-encoded as a multiplier here, because `denial(T)` already contains it: a
cash-rich team you kill four of simply is not below a full buy next round, so
`denial` does not fire. Adding a bank term on top would count the same thing
twice.

The bank stratification is how the mechanism was **verified**, not a parameter
to be carried into the formula.

**This applies to the LATE regime only, and the early regime's `commitment(opp)`
gate is not a violation of it.** The two condition on different quantities for
different reasons:

| | late regime | early regime |
|---|---|---|
| what is *not* re-encoded | the enemy's **bank** in round N | -- |
| why | `denial(T)` already contains it: a cash-rich team you kill four of is not below a full buy next round | -- |
| what *is* gated on | -- | the enemy's **committed value** (loadout) in round N |
| why it does not double count | -- | `denial_early(T)` reads their **total wealth**, which cannot distinguish a team that was poor from a team that was made poor. Commitment is the missing information, not a second copy of it |

The distinction is that the late readout is already commitment-sensitive by
construction and the early one is not. `M27f` is the evidence: ungated, the
early rounds pool savers and buyers and the outcome inverts (+19.70pp against
-0.48pp).

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

**RESOLVED 2026-09-05: `swing_impact` leaves the Impact formula.** The open
decision above is closed. The structure shown is now the whole structure.

### 8a. Why -- swing is an econ term, and was never named as one

`_econ_swing_risk_factor` (`impact.py:258-334`) takes **no man-advantage input
of any kind**. Every variable is monetary: `remaining` (bank), `kills *
KILL_REWARD`, `plant_bonus`, the loss-bonus ladder, `loadout`, and buy
thresholds. `deaths > 0` appears only as "this player must rebuy". The realized
half is `0.5 + 0.2 * players_below_full_buy_next_round`. Neither reads an alive
count.

The man-advantage swing is `kill_order_bonus`, which the factor *multiplies*.
So:

```
swing_impact = kill_order_bonus x (enemy's economic fragility)
                ^ man advantage    ^ econ
econ_impact  = kill_order_bonus x (killer_tier / victim_tier)
                ^ man advantage    ^ econ
```

**Today's score therefore carries two economic components**, and with
`FACTOR_WEIGHTS = {econ 1.0, time 1.0, swing 1.0}` econ concepts hold two-thirds
of the non-damage weight. The name "swing" obscured this -- it reads as a swing
in the man count and is nothing of the kind.

Once econ is rebuilt around realized denial, swing is covering the same subject
with worse tools, and one of its two halves is covering it with the *identical
number*.

### 8b. What happens to each half

The scorer cross-assigns the factor (`impact.py:532`):

```python
combined_swing_factor = team1_combined_swing if killer_team == Team.TEAM_2 else team2_combined_swing
```

so the killer receives the value computed for the **other** team. The realized
half as actually used is therefore *"how many enemies are below a full buy next
round"* -- verbatim section 4, this component's entire quantity.

| half | disposition |
|---|---|
| **realized** (`_realized_econ_swing_factor`) | **absorbed** -- it becomes `econ_component` via section 4. Not deleted; relocated. |
| **ex-ante** (`_econ_swing_risk_factor`) | **deleted from the Impact formula.** It is a *prediction* of roughly what the realized half measures, and the two agree only 62.3% of the time when both are informative (`M14`). Impact is an attribution score, not a forecast -- where the realized quantity is available, a prediction of it is the wrong register. |
| `_combine_swing_factors` | **no longer called by the scorer.** `M14`: it returns a neutral 1.0 for 44.9% of team-rounds, in which `swing_impact` is a verbatim copy of `kill_order_bonus` contributing zero information and a full unit of collinearity. |

**Both functions remain defined.** `app/services/kill_order_leverage.py:31,138,142`
imports `_econ_swing_risk_factor` directly and is documented EX-ANTE ONLY at
`:127`. Deleting the function breaks it; deleting the *term* does not.

### 8c. The column stays, written as 0

Same treatment migration `0008` already specifies for `econ_impact`. Consumers
that read `swing_impact` by name and must not silently change meaning:

- `app/services/impact_eval.py:219` -- `FEATURE_COMPONENTS`, the fitted feature
  list; also `:128`, `:190`, `:993`
- `app/services/kill_order_refit.py:1518,1764` -- Stage C per-round recomputation
- `app/models/impact_score.py:56` and `alembic/versions/0006_*`
- `tests/test_impact_eval.py:58,213,463,541`

`FEATURE_COMPONENTS` needs an explicit decision rather than being left to fit a
dead all-zero column. Dropping it there is a change to the evaluation harness
and must be made deliberately, not inherited.

**This is not a tidiness issue. Measured 2026-09-06 on the real code path**, on
paired synthetic data where only the swing column differed:

| | damage mult | econ | time | **swing** | train log loss |
|---|---|---|---|---|---|
| swing live | 1.0 | 1.20 | 1.80 | **0.00** | 0.691665 |
| swing all-zero | 0.5 | 0.60 | 0.90 | **1.50** | 0.691665 |

Identical loss to six decimals, `usable=True`, positive slope, no warning --
and **half the total weight assigned to a column contributing nothing**, with
every live weight halved to compensate.

**Mechanism.** The composite is `standardize()`d before fitting
(`impact_eval.py:1032-1033`), so `(d, w_econ, w_time)` and any scalar multiple
give an identical standardised composite and identical loss. Normally the
simplex constraint `w_econ + w_time + w_swing = 1` pins that scale. A dead
column breaks the pin -- the dead coordinate absorbs whatever slack a rescale
needs -- so an entire one-parameter family of points becomes observationally
identical. The tie-break `key[:3] < best[:3]` over `_simplex_grid`'s ascending
order then takes the first, which is the **largest** dead weight and the
**smallest** damage multiplier.

The existing guard at `:1030` checks `composite.std() == 0` -- a constant
*composite*, not a constant *factor column* inside a varying composite. It does
not catch this.

**Required, and it is a prerequisite for anything in section 9b:**
`fit_constrained_weights` must check each factor column's variance and **refuse
rather than proceed** on a constant one. Refusing beats silently dropping the
dimension because this function's output is a *deployment proposal* -- it flows
through `candidate_from_constrained` (`:1198`) into published `FACTOR_WEIGHTS`
-- and a dead column almost always means the caller passed the wrong feature
list or the wrong replay mode, which is information worth surfacing.

**This is not specific to migration 0008.** The same degeneracy appears
whenever a factor column is constant in the mode being fitted, and by section
9a `econ_component` is constant-zero in ex-ante mode -- which is the mode the
harness runs in by default. After this change the harness's normal operating
condition includes a structurally dead column. The guard is the invariant that
makes the weight search safe to run at all, not cleanup for one migration.

A milder version affects the regression path: an all-zero column takes
coefficient ~0 under L2 so the fit itself is fine, but drop-one diagnostics
report its cost as exactly `0.0000` and rank it most-droppable. True, entirely
vacuous, and it reads like a substantive finding.

### 8c-i. DECIDED 2026-09-06 -- what `FEATURE_COMPONENTS` becomes

```python
FEATURE_COMPONENTS = ["damage", "kill_order_bonus", "time_delta", "econ_component"]
```

where `time_delta = time_impact - kill_order_bonus`. The two sum back to
`time_impact` exactly, so nothing is invented -- the fused leverage term is
split into *what the kill was worth for the state it happened in* and *what it
was worth extra for when it happened*.

**The principle this rests on: the harness's list has always mirrored the
scoring formula one-for-one, and it does not have to.** The formula's job is to
score; the harness's job is to diagnose. Scoring keeps
`kill_order_bonus * time_factor` fused (the time factor is dimensionless and
meaningless standing alone -- see the time spec's Part 3). Evaluation may split
it, because a split answers questions the fused column cannot.

**Note what does NOT die.** `time_impact` is
`kill_order_bonus_x_time_sum - death_order_bonus_x_time_sum` (`impact.py:674`),
which **is** the leverage component under the new formula. Only `econ_impact`
and `swing_impact` go to zero.

**Why split rather than keep it fused -- measured, not assumed (`M29`).**

| pair | correlation |
|---|---|
| `kill_order_bonus` vs `time_impact` (fused) | **+0.9845** |
| `kill_order_bonus` vs `time_delta` | **+0.4386** |
| `damage` vs `time_delta` | +0.3060 |

The fused column is **98.5% the raw kill-order bonus**, so a weight fitted on it
is close to a weight on the state term alone -- the timing signal is nearly
invisible inside it. The split's two columns correlate at +0.4386, well below
the 0.73-0.90 band this spec exists to escape, so they carry separable variance.
The pre-check declared before running was that a correlation approaching that
band would send the decision back to a fused three-column list; it did not.

**What the split buys.**

1. **It asks whether the time modulation earns its place** on top of raw
   kill-order. With state and timing fused in every column there is currently no
   way to find out.
2. **It isolates the post-plant retune.** In ex-ante mode the pre-plant factor
   returns exactly 1.0, so `time_delta` is non-zero **only for post-plant
   kills** -- `M29` measures 69.7% of player-rounds at exactly zero, matching
   `M5`'s independent ~69% non-post-plant share. That makes `time_delta`
   precisely the column the plant-window spec's **Part 4 non-inferiority gate**
   needs to read, and that gate currently has nowhere to look.
3. **Two live factor columns in ex-ante mode instead of one**, since
   `econ_component` is zero there (section 9a). Without the split the ex-ante
   weight search has a single live factor and nothing to search.

**Implementation cost is one field.** `death_order_bonus` already exists
(`impact.py:544`), so the scorer needs to expose the **net `kill_order_bonus`**
alongside the existing sums; `time_delta` is then derived in the harness by
subtraction and needs no second column. Migration `0008` is already adding
columns, so it rides along -- and `load_stored_observations` needs it stored,
not just replayed. Three factor columns also match `FACTOR_WEIGHTS`' current
arity, so `ConstrainedWeights` and the simplex machinery are renamed rather than
restructured.

**Accepted cost.** Dropping `econ_impact` and `swing_impact` from the list
changes the feature set every stored harness comparison was computed on. That
is already accepted in the rollout section -- Stage C artifacts are written off
and `.impact_eval_cache` self-invalidates on the version key.

**This does not remove the need for the zero-variance guard.** `econ_component`
is still constant-zero in ex-ante mode by design.

### 8d. This makes the non-inferiority gate MANDATORY, not advisory

The constraints section already notes that ex-ante mode keeps
`econ_differential_factor` (`:474-476`) and the ex-ante swing factor today.
**Removing both leaves `use_realized=False` with no economic information at
all.** That is the largest deletion in this spec and the forward yardsticks are
fully sensitive to it.

So: `impact_eval.py`'s yardsticks are **reported, not gated** for the new
component, and **a hard non-inferiority gate** on the deletion of the two old
ex-ante terms. Failing it is a finding about the deletion, not a reason to
retune the new component.

**Rescore attribution. DECIDED 2026-09-06: one bump, one rescore.** An earlier
version of this paragraph required separate bumps because a single one makes
displayed movement unattributable. That concern is real but is not paid for by
the site's readers, and it is **not** how the gate is protected anyway.

The gate does not depend on the rescore. `impact_eval.py` **replays from raw
data** -- `load_all_observations` calls `build_impact_rows_for_match` directly
(`:1519`) and never reads stored `impact_scores`. So the econ deletion can be
gated on its own, with the post-plant retune switched off, without rescoring
anything.

**What that requires:** the changes must be **independently switchable in the
replay path**, the way `use_realized_swing` already is. This matters most here,
because the post-plant retune is expected to *help* the yardsticks while this
deletion is expected to *cost* -- measured together they can cancel and both
read clean, which is the failure this gate exists to prevent.

### 9. ECON_SCALE

**Anchor (ships with the structural change).** A single constant chosen so
`econ_component`'s standard deviation over the full dataset equals
`time_impact`'s current standard deviation -- the component enters with
influence comparable to what it replaces, rather than a hand-picked weight.
Recorded with its derivation and gated by a test.

This is a **scale-matching rule, not an estimate**, and it exists because the
evaluation harness cannot see this component at all (section 9a). It is what
ships first, per the sequencing decision in section 9c.

### 9a. Why the harness cannot fit this constant as things stand

`app/services/impact_eval.py` runs an **ex-ante replay**:
`load_all_observations(db, use_realized_swing=False)` (`:1504`), with
`impact_eval_cache.py:147` hardcoding `False`. Under `use_realized=False` this
component is **exactly 0 for every row** -- that is this spec's own leakage
gate, and it must stay exact.

A constant multiplied by a constant-zero column is zero. So the harness can fit
the damage and leverage scalars and is **structurally blind to `ECON_SCALE`**.
That is not a gap to engineer around; it follows from the component being
ex-post, which is a deliberate product decision (see "This is ex-post").

The reason the harness strips realized information is not bureaucratic. **The
enemy's round N+1 economy substantially encodes who won round N**, through the
loss-bonus ladder: `M13` measures a ~3 full-buy spread fixed by the rounds 2-3
record alone (lost both 1.17, won both 4.15). A component built from
next-round economy therefore carries a proxy for the round's own result. Scored
against that round's outcome it would look excellent for reasons that have
nothing to do with whether it measures anything.

### 9b. The method for fitting ECON_SCALE -- a forward window that starts at N+2

**The problem is not the target's level, it is that the target's window
overlaps the component's information horizon.** This component reads round
N+1 and nothing later. Any target drawn strictly from round **N+2 onward** is
therefore free of the direct leak.

**Match win/loss does NOT solve this, and the reason is worth stating** because
it is the obvious first idea. A match result is built from every round,
including N+1, so the peeked round sits inside the label. Worse, the match
label is **shared across every observation in the match**: round 5's row peeked
at round 6, round 6's peeked at round 7, and so on, so pooled across the match
the feature set has collectively seen every round the label is made of. The
contamination does not dilute with match length; it accumulates.

A forward window is different because **each row gets its own target and each
one excludes what that row saw**: round 5 is judged on rounds 7+, round 6 on
rounds 8+, round 7 on rounds 9+.

**The specified target.**

- Take `forward_window_target` (`impact_eval.py:467`) and start the window at
  **N+2** rather than N+1. The current loop is
  `for step in range(1, k + 1)` at `:492`; the shifted version starts at
  `step = 2`. Everything else -- the `gamma` discount, the per-row weight equal
  to the total discount mass, the match-clustered bootstrap -- is unchanged.
- **Windows still never cross halftime or the OT boundary.** That rule already
  exists at `:494` and is correct here for a substantive reason, not a
  technical one: the economy resets at halftime, so a round-11 kill cannot deny
  anyone anything past round 12. There is no lasting effect to measure, which
  is the same fact that makes rounds 12 and 24 score 0 in section 10.
- **Every component must share this target.** Coefficients from separate
  regressions on different targets are not commensurable and must not be
  combined. Damage and leverage lose a little power being judged on a later
  window; that cost is accepted in exchange for a valid joint fit.
- **Realized mode is required** for the column to be non-zero. Note this also
  switches on the pre-plant proximity term, which is ex-post as well -- turning
  realized mode on changes two components, not one, and the report must say so.

**What this does and does not remove.** It removes the feature containing a
proxy for its own label. It does not remove ordinary confounding -- a team
ahead on economy at round 6 tends to win rounds 7-9 too. That path is already
handled by the existing control ladder (`round_result`, `score_diff_before`,
`loadout_diff`, `full_buy_count_diff`), whose stated purpose is to measure what
the components add *on top of* knowing who won the round. Nor does it remove
indirect propagation: round N's result still ripples into round N+2's economy.
These are conditional associations, exactly as everywhere else in this project.

### 9b-i. LIMITATION -- the fit has a blind spot at the end of the match

**This must not be dropped when the fitted number is quoted.** Shifting the
window costs the rounds nearest each half's end:

| scoring round | rounds it is judged on |
|---|---|
| 8 | 10, 11, 12 -- full window |
| 9 | 11, 12 -- partial |
| 10 | 12 -- thin |
| **11** | **none -- contributes nothing to the fit** |
| 12 | already scores 0 (section 10) |

The same shape applies at 20-24, and it is **worse in the second half because
matches end**: a 13-4 match finishes at round 17, so rounds 14-16 have almost
no future either.

Two different things are happening and only one of them is acceptable:

- **At halftime this is correct.** The reset destroys the mechanism, so there
  is genuinely nothing downstream to measure. The method is reporting a real
  absence.
- **At the end of the match it is not.** A round-21 kill that leaves the enemy
  broke for round 22 has a completely real effect with the match on the line.
  The effect exists; there is simply no future left to measure it against.

**Consequence, stated plainly: `ECON_SCALE` would be fitted on early and middle
rounds and then applied to closeout rounds as well.** That assumes econ is
worth the same at 11-11 as at 3-1, which is very likely false -- money matters
differently when the match is on the line. The harness degrades gracefully
rather than failing, because rows are already weighted by how much future they
actually have, so thin windows count for less automatically. But roughly 2 of
the 20 scored round positions contribute nothing and several more contribute
little, and those are systematically the high-stakes ones.

This is the failure pattern this project has been burned by before -- a number
estimated on a convenient subset and then applied everywhere, which is exactly
how `M15` carried a wrong overtime count for weeks. **Any reported
`ECON_SCALE` must carry this limitation with it**, and the fit report must
state the effective round coverage rather than only the point estimate.

### 9c. Sequencing -- the anchor ships first, the fit comes after

**Decision: make the structural change work before changing any scalar.** The
restructure already moves three things at once; adding a weight refit on top
would make the rescore uninterpretable, which is the same reason the rollout
section wants separate version bumps.

So: ship with the section 9 anchor, then run the section 9b fit as its own
change with its own bump.

**A hypothesis worth recording, because it motivates doing the fit at all.**
The prior evidence that refitting weights is not worth much -- **+0.005 AUC**,
cited in both specs' out-of-scope sections -- was measured while the components
correlated **0.73-0.90**. When features are near-collinear, every weighting
produces nearly the same composite, so the achievable spread from reweighting
is small almost by construction. Decorrelating the components is the entire
point of this spec, so that ceiling may not survive the change.

**This is directly testable on existing code, and cheaply.**
`fit_constrained_weights` already evaluates every point of the simplex grid;
record the **min-max log-loss range across the grid** rather than only the
argmin. A near-flat range before the change and a wider one after would confirm
it. A range that stays flat would show the ceiling is structural rather than a
collinearity artifact, which is itself worth knowing before spending on a
refit.

**First supporting evidence, 2026-09-06.** `M29` measures
`corr(kill_order_bonus, time_impact) = +0.9845` -- the shipped leverage column
is almost entirely its own multiplicand. Since all three shipped factor terms
are `kill_order_bonus * <factor>`, this is consistent with the +0.005 ceiling
being an artifact of reweighting three near-copies of one column. It is
suggestive, not decisive: it measures one of the three terms, and the min-max
range test above remains the check that would settle it.

**Prerequisite either way:** `fit_constrained_weights` must reject
zero-variance factor columns before any of this is run. See the rollout
section.

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
- **ONE `IMPACT_CALCULATION_VERSION` bump, 1 -> 2, shared with the plant-window
  spec. DECIDED 2026-09-06.** Both specs ship under the same bump and a single
  rescore. The earlier rule that "the two specs must not both claim 1 -> 2" is
  withdrawn: they now deliberately do. Rationale in section 8d and in the
  plant-window spec's Rollout -- the site has no external consumers, the gates
  do not need a rescore to read cleanly, and rollback is one rescore back.
- Full rescore: `scripts/recompute_impact.py` then
  `scripts/recompute_player_views.py`.
- `.impact_eval_cache/` self-invalidates on the version key.
- Stage C artifacts become non-comparable (`kill_order_refit.py:1837` stamps
  results with `IMPACT_CALCULATION_VERSION`). Costs nothing -- every Stage C
  candidate was non-deployable -- but do not quote those numbers afterwards.

**The plant-window spec lands in the same version bump, by decision.** Movement
in displayed numbers is therefore not attributable to one change or the other.
Accepted: nobody reads these numbers closely enough for that to cost anything,
and the diagnostic question that *does* matter -- how each change moves the
forward yardsticks -- is answered by replay, not by the rescore.

`STATE_DIAGRAM_CALCULATION_VERSION` also **stays at 2**; admitting overtime
rounds adds data rather than changing replay semantics. See the plant-window
spec's Rollout.

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
  map and patch era. Report with intervals. **Same principle as the
  `ECON_SCALE` fit in section 9b** -- the component reads round N+1, so every
  endpoint used to judge it is drawn from N+2 onward. The two share a
  justification and should share a round-coverage report.
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
  **In the early regime this is the `commitment(opp)` gate** and it is what
  `M27f` requires: a fully-saving victim team must send `econ_round` to
  approximately zero. A test asserting the early regime pays the same against a
  saving team as against a bought-in one would encode the exact pooling artifact
  `M27f` identified.
- **Early and late regimes use different readouts, deliberately.** A round-3
  team-round and a round-7 team-round with identical enemy next-round full-buy
  counts but different enemy next-round *bank* receive **different** early-regime
  credit and **identical** late-regime credit. `M28`: the early denial is 97%
  bank, the late readout does not see bank at all.
- **Rounds 2-4 / 14-16 are not zero.** A regression test pinning them to zero
  would reinstate the withdrawn blanket rule; assert instead that a bought-in
  victim team produces non-zero early credit.
- The early regime fires in **rounds 2, 3, 4 and their second-half mirrors, and
  nowhere else**; the late regime fires in **5-11 / 17-23 and nowhere else**.
  This is the scope lock expressed as a test, and it is the guard against the
  drift that box exists to stop.
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
