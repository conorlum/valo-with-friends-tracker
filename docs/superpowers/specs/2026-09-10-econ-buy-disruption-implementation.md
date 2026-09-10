# Economy impact: replacement cost and disruption of the next buy

Date: 2026-09-10. Status: V2 kill credit with owner's revised 30%/80% death
penalty in section 12; broader validation pending. No production activation.
Owner direction: `../2026-09-10-econ-buy-disruption-design.md`.

## 1. Purpose and accepted interpretation

Equipment losses have a small economic value when a team can absorb them.
Their value is substantially larger when they impair the following buy.
Teammates' resources and carried equipment matter; three deaths is not an
automatic threshold. Allocate a round's disruption reward across all kills
that contributed equipment loss, not only the threshold-crossing kill.

The component is retrospective and separate from damage/assists and kill-order
leverage. Winning a round affects actual next-round resources, but this
component adds no direct win bonus. Its casualty/affordability readout must
not be advertised as causal attribution from observational data.

This replaces the proposed standalone 0.5 late pool and the generic early
commitment-times-poverty credit. The prior independent-debit experiment stays
as a documented comparator, not the new release formula.

## 2. Data contract and terminology

For a fixed match and round N, read:

- Full five-player rosters, teams, agents and stable match-player IDs.
- Rounds N and N+1: raw loadout value and remaining credits for all players.
- Round outcomes, pistol outcome for the current half, and the last played
  round. Do not infer a missing outcome from a winner guessed elsewhere.
- Kill events ordered by `(event_time_seconds,id)`, victim and killer IDs.

Define paid loadout `P_i = max(0, loadout_i - free_ability_credits(agent_i))`.
This is the existing project's **equipment-value proxy**. It includes armor
and paid utility; it is not the exact gun held at death. Next-round bank is
the stored `remaining` value. Use the current agent table from the frozen
candidate artifact, record its hash, and do not fetch live prices during scoring.

Resource pooling is an approximation to teammates helping fund replacements.
Not all equipment/credits can be converted or transferred freely. The score
must describe pooled *funding estimates*, not exact feasible purchases.
No raw gun count may be inferred merely from the number of loadouts below 4200.

Missing roster, next-round data or required numeric values: abstain for the
affected round and report a reason. Negative/nonfinite values: data error,
not zero wealth. Require five unique players on each of two teams for v1.

## 3. Eligibility and early-round history

Eligible N: 2-11 and 14-23, excluding the final played round and any missing
next round. Both current and next surrendered rounds abstain. Pistols 1/13,
end-of-half 12/24 and overtime remain zero in this version.

Use a half-relative index h=N for the first half, N-12 for the second.
Record pistol winner, current-round winner, and the outcomes of the earlier
rounds in this half in the audit. These describe such cases as a pistol winner
losing round 2; they do not multiply the score again.

The intended next-buy reference is:

- h=2, pistol-winning team: `target_i=min(3900,max(1000,P_i,N))`.
  The reference is retaining/replacing the equipment actually bought for the
  early carryover cycle, rather than upgrading every bonus gun to a rifle.
  It stays fixed if the team loses round 2, so defeat cannot lower its own bar.
- All other eligible cases: `target_i=3900`.

3900 is a paid-loadout reference inherited from FULL_COMMIT, not a claim that
all agents/weapons have identical optimal buys. The 1000 minimum prevents a
non-buyer's near-empty current loadout from defining an empty target. Both are
policy assumptions to expose in the early-round walkthrough.

Do not apply the usual raw-loadout 4200 threshold to decide whether a planned
round-3 bonus carryover is automatically bad. Display that familiar count as
context only, alongside the actual targets and resource test.

## 4. Equipment exposure: one starting kit, counted once

For each player with an observed death, assign their first chronological
death the exposure `v=P_i,N`. All subsequent deaths have exposure zero unless
a future schema provides independently observed replacement equipment.

- Valid enemy killer: v can create enemy credit and own debit.
- Self/team/environmental death: v creates own debit/exposure, no enemy credit.
- Unknown victim: no attributable equipment; report it and abstain if it
  prevents reconstructing the team loss. Unknown killer with known victim can
  establish own loss, but creates no invented enemy credit.
- Zero paid loadout: zero economic exposure, regardless of combat credit.

This is deliberately conservative about resurrection and pickups. Do not
reuse the starting loadout at every death. A later death can still have normal
combat impact without another modeled starting-kit loss.

For each team T, `L_T=sum(v)` over all its first-loss events. A player's
`lost_i` is their own first-loss exposure, zero if they survive. Equipment
recovered by teammates cannot be observed exactly; the next resource state
can soften estimated disruption but does not establish an exact recovery.

## 5. Team buy-disruption estimate

Compute for the team that LOST the equipment:

```
H_T = sum(target_i)
U_T = sum(min(P_i,N+1, target_i)) + sum(bank_i,N+1)
D_T = max(0, H_T - U_T)
Q_T = min(D_T, L_T)
G_T = sum(max(0, target_i - P_i,N+1))
activation_T = min(1, D_T / 3900)
severity_pool_T = min(L_T, G_T * activation_T)
```

U caps each player's equipment at their target so an expensive surplus item
is not treated as cash available to equip another teammate. Remaining bank
is pooled. D is the resulting target-funding shortfall.

Q asks how much of that shortfall could be closed by restoring resources up
to the recorded lost-equipment exposure. Equivalently:
`Q=D-max(0,H-(U+L))`.

This is a **resource-restoration proxy**. It holds the actual round outcome,
income and observed next spending fixed; adding lost kit value approximates
replacement resources freed, not a simulated replay in which the deaths never
happened. It cannot prove that these kills caused all of Q. It limits the
reward for killing already-poor opponents to the value actually exposed:
one cheap kill cannot claim an arbitrarily large pre-existing shortfall.

No deaths, L=0 -> Q=0. Adequate team funding, U>=H -> Q=0. A weak observed
loadout with enough unspent pooled resources -> no large disruption bonus.
Any claim that the exact gun buy was prevented must additionally be supported
by the shown next-round loadouts, not just the scalar estimate.

**V2: next-buy severity, not only the cash gap.** G measures the observed
equipment downgrade from the reference. The funding gap D activates it
smoothly: a gap of one reference kit, 3900, activates the whole observed
downgrade. Smaller gaps activate proportionately less. The loss cap prevents
a cheap casualty claiming an unlimited existing downgrade. If D=0, a weak
observed buy alone gets no large reward.

Use severity_pool, not Q, in scoring. Q remains an audit quantity measuring
restorable resource shortfall; severity_pool is an equipment-value **score
index**, not literal missing cash or a causal loss estimate. It may exceed D.
The one-kit activation threshold is an explicit policy choice, not a fit.

The initial V1 used Q as the bonus pool. Abyss R22 exposed why this was too
narrow for the owner's coordinated force/save definition. V2 was declared
before recalculation and the V1 artifact is preserved. Abyss therefore serves
as a development walkthrough, not independent validation of V2.

## 6. Kill-side economic credit

Review constants, declared before the worked calculation:

```
R = 19500
BACKGROUND = 0.10
DISRUPTION = 1.00
```

For each eligible enemy event e against team T, with exposure v_e:

```
background_e = BACKGROUND * v_e / R
disruption_e = DISRUPTION * severity_pool_T / R * v_e / L_T   if L_T > 0, else 0
credit_e = background_e + disruption_e
```

L includes non-enemy losses, so enemy kills cannot claim the part of the
team's resource exposure belonging to suicides/team/environmental deaths.
All qualifying kills receive the same disruption multiplier per credit of
exposed equipment. Order does not choose who gets the bonus. Allocation is
proportional to paid equipment exposure, not necessarily equal per kill.

The background is small and proportional to what was lost. There is no
automatic 0.5 award for a single cheap kill. At the review scale, a 3900-kit
enemy kill gives about 20 points when severity_pool=0, up to about 222 if
severity_pool=L. A cheap
500-kit kill is proportionately smaller (about 3 to 28 points). These are
policy examples, not fitted estimates or percentages of round win probability.

## 7. Independent victim debit

Historical V2 candidate, superseded by the owner's rule in section 12.
Retained here to explain the original Abyss comparison, not for new activation.

Use the existing own-wealth scarcity definition as the initial independent
debit candidate, adding the small cost so even affordable losses are visible:

```
W_T = sum(P_i,N+1 + bank_i,N+1)             # uncapped paid wealth
f_T = clamp(1.5 * (1 - W_T/(5*6300)), 0, 1.5)
debit_i = (BACKGROUND + f_T) * lost_i / R
```

This measures the resource burden of own lost equipment given remaining
wealth, rather than borrowing the opponent's credit pool. It treats depleted
reserves as a burden even if the immediate buy is preserved. Rich teams pay
only the background. Teams with little equipment loss pay proportionately
little even when poor. No loss means zero debit.

**Provisional policy to review explicitly:** the debit still uses a wealth
curve, whereas large killer credit uses a target-funding shortfall. That
distinction makes the component non-zero-sum. It is justified only if the
owner wants own reserve depletion charged separately from next-buy denial;
different totals alone are not proof that the distinction is right. Show the
background and scarcity portions separately. Do not silently imply this debit
is already a validated implementation of the owner's preferences.

Both net-negative teams are possible. Neither both-positive nor both-negative
frequency is an objective to optimize. The zero-sum invariant is removed from
this candidate's tests and replaced with credit-allocation and loss-accounting
identities.

## 8. Player totals, scale and runtime integration

```
raw_econ_i = sum(credit_e for kills by i) - debit_i
econ_component_i = round(C * ECON_SCALE * raw_econ_i)
Impact_i = damage_component_i + leverage_component_i + econ_component_i
```

Compute at full precision; round the final player-round econ net once. Event
points in the trace are explanatory, not independently rounded inputs to the
player total. C is a top-level policy multiplier, distinct from either the
preplant centering constant or the econ conversion scale.

For the single-match review, keep C=1 and ECON_SCALE=1007.9209 so the change
is not hidden by reanchoring. Do not calibrate SD on one match. Before release,
report raw distributions and a separately recalculated full-corpus anchor,
then let the owner review normalization and C explicitly. A and B stay fixed.

Proposed additive module: `app/scoring/econ_buy_disruption.py`, pure functions:

- `build_buy_targets(history, current_paid_loadouts)`
- `first_loss_exposures(roster, current_paid_loadouts, ordered_events)`
- `estimate_buy_disruption(targets, next_paid_loadouts, next_bank, exposures)`
- `attribute_buy_disruption(exposures, team_results)`

Return a typed audit record containing H/U/D/Q/L/G, activation, severity pool,
uncapped wealth, scarcity,
data-quality flags and per-event credit/per-player debit parts. Runtime and
review must call the same implementation. Add an opt-in candidate flag and
an econ observer payload version; leave current defaults unchanged until
approval. Do not modify concurrent kill-order/timing changes to implement it.

Evaluation keeps `use_realized=False` exactly zero. This model reads N+1;
no same-round/N+1 predictive claim may be made from it. The reference
calculator is for a transparent worked review, not prediction validation.

## 9. Required behavioral and integration tests

1. Rich team replaces a lost 3900 kit: background credit/debit remain, Q=0.
2. Two casualties funded by surviving teammates: Q=0 despite low individual
   banks; moving bank between teammates leaves the pooled calculation unchanged.
3. Same loss size with a funded versus unfunded next target: the latter gets
   larger credit; there is no unconditional three-death trigger.
4. Single cheap loss against a pre-existing large shortfall: Q<=that loss;
   cannot collect the whole five-player deficit.
5. Permuting the order of distinct players' deaths preserves their economic
   shares. First/third/fifth order alone is not an economic allocation rule.
6. Repeated death of a player counts one starting kit. Non-enemy deaths create
   own cost, never opponent credit; their share of the severity pool is not
   credited to enemies.
7. Pistol-winning h=2 has carryover targets; losing that round does not lower
   those targets. A low-value survivor carryover is not automatically a failed
   full buy. Other histories use the declared standard target.
8. Boundaries, missing data and ex-ante mode abstain; final/half/OT cannot read
   an unrelated following round. Rejections carry explicit reasons.
9. Sum event credits equals killer team credit, sum player debits equals own
   burden, and round/match totals reconcile with exact rounding specified above.
10. Freeze parameters and lookup versions in the review artifact; changing
    them or source data invalidates cached candidate values.

For new behavior, demonstrate a value failure with the defect present before
implementing the correction. A broken import/API is not test evidence.

## 10. Worked-example and release acceptance

Work through all eligible team-rounds of Abyss match 3104 and provide selected
high/low event calculations with their exact inputs. Include the previous
R14/R16/R17 cases and compare actual buy state with predicted disruption.
Disagreement is a finding; do not tune the constants afterward to make this
one match look right. Identify missing histories and ambiguous cases.

Before production: run the fixed ten-match review and the full corpus with
predeclared coverage/distribution checks, assess the target and debit choices,
and prepare the single approved runtime configuration. Verify it reproduces
review totals, then follow the existing version/migration/rescore/cache rollout
on the verified site database. This spec and walkthrough do not activate it.

## 11. Completed Abyss development review and implementation handoff

See the [manual review](../abyss-buy-disruption-review/manual-review.md) for
hand-substituted formulas and the complete event ledger. The reference
calculator passes 17 tests, 163 match reconciliation checks and 28 separate
decimal comparisons for the selected worked cases. Reinstating V1's bonus
pool behind the same API produces two numerical failures.

R6 supplies a constrained next buy and a 124.04-point kill; R3 supplies three
absorbed losses and a 19.90-point kill. R22 prompted the declared V2 revision
and supplies a 108.56-point kill. All are ECON only at C=1 and the fixed review
scale. These examples establish arithmetic and intended local behavior, not
the validity of the chosen constants or counterfactual causation.

**Release acceptance remains open for the independent debit.** All ten match
economy totals are negative. The current debit charges reserve depletion even
when next-buy denial is zero, so implementing it as an already-approved final
design would overstate this review. Keep it exposed as a candidate. Before
release, compare an own-buy-disruption-focused debit with this reserve-burden
debit in the same review views; declare its rule before computing that arm.
Do not use C alone to hide this structural choice.

The match contains no pistol-winning team losing round 2/14. A separate game
must cover that history. Pistols themselves remain excluded here; this spec
does not yet award a pistol-round equipment-retention effect. Resource sharing,
partial-kit upgrade feasibility, pickups and exact lost guns remain documented
data limitations. Broader behavior and runtime integration tests in section 9
are requirements for the production implementation, not claims about coverage
of the standalone 17-test reference suite.

## 12. Owner revision: absorbed deaths at 30%, disrupted deaths at 80%

The owner replaced the reserve-depletion debit after reviewing section 11.
Keep the V2 credit and team severity calculation unchanged. For each player's
first lost kit, compute the same damage basis used to value an enemy kill:

```
background_loss = 0.10 * lost_i / 19500
disruption_loss = severity_pool_T / 19500 * lost_i / L_T  # zero if L_T=0
rate_T = 0.80 if severity_pool_T > 0 else 0.30
debit_i = rate_T * (background_loss + disruption_loss)
```

There is no additional scarcity or low-reserve charge. The 30% case applies
when the funding test says the team can absorb the loss; 80% applies when
the positive severity pool indicates a constrained next buy. The percentages
are of the modeled damage basis, not of the previous V2 debit or raw kit price.
This uses an observational buy-disruption proxy, not a new causal estimator.

Enemy kills retain 100% credit. Self/team/environmental first-kit losses use
this same own damage basis without creating enemy credit. Subsequent deaths
still do not invent another starting kit. Sum at full precision and round
the player-round net once, as in section 8.

This intentionally produces positive combined credit-minus-debit for enemy
kills, while individual players or teams can still finish negative. The rate
on the background term jumps from 30% to 80% at any positive severity pool;
retain this explicit policy in review rather than silently smoothing it.

The [Abyss comparison](../abyss-buy-disruption-review/death-penalty-30-80.md)
reports old and new totals, gross credits, revised debits, and selected deaths.
At the unchanged scale and C=1, TEAM_1 moves from -4057 to +502 and TEAM_2
from -2537 to +1293. Nine players finish positive and one negative. This
replaces section 11's open debit choice for this reference candidate; the
broader match review and activation requirements remain. The reference suite
now has 22 passing tests; restoring the old debit produces four value failures.
