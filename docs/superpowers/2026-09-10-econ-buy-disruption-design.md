# Econ scoring: equipment loss and disruption of the next buy

Working design from the owner's 2026-09-10 clarification. This records the
desired behavior, not a fitted or activated formula. No numerical parameters,
new measurement results, or production defaults are declared here.

## What the owner wants the score to mean

Taking equipment away has some economic value even when the enemy can replace
it. It has substantially more value when enough equipment is lost that the
team cannot maintain its next buy, including replacement purchases by teammates.
The relevant unit is the team's ability to replace equipment, not an isolated
victim's remaining credits and not a universal three-kill threshold.

The large reward belongs to the whole collection of contributing kills. Do
not award it solely to whichever kill happened to be third chronologically.
The round's economic readout is retrospective, so it may be allocated back
over earlier kills once the next-round state is available.

## Phase and history

- Pistol rounds themselves are primarily about winning; do not introduce a
  substantial extra pistol-equipment scoring term on this interpretation.
- Round 2: distinguish the pistol winner from the pistol loser. Lost equipment
  bought for carryover matters, even if replacements preserve the next round's
  immediate loadout. A pistol winner losing round 2 is a separate economic
  history from winning round 2 with casualties.
- Rounds 3-4: distinguish those histories and whether teammates can fund
  replacements. Three or more lost guns is the owner's proposed common tipping
  point, to be checked against resources and subsequent equipment, not imposed
  as an unconditional cliff. A rich team may absorb more deaths; a poor team
  may be disrupted by fewer.
- Rounds 5-11: use the ordinary replacement/next-buy logic. Apply the analogous
  histories to rounds 14-16 and ordinary logic to 17-23.
- Keep next-round economic credit zero at half/match termination where there
  is no relevant equipment carryover. Existing overtime treatment is unchanged
  by this working note.

"Pistol-winning team losing its gun" is interpreted here as losing the weapon
bought after winning pistol, especially in round 2. This does not assume every
gun is newly purchased: surviving and recovered equipment can matter too.

## Proposed structure, before selecting constants

Two distinct channels:

1. **Replacement cost:** a small equipment-dependent contribution for lost
   resources, including cases where reserves absorb replacement purchases.
2. **Buy disruption:** a larger team-level contribution when equipment loss
   contributes to an impaired subsequent buy. This must account for team
   resources, surviving equipment and early-round history.

Schematically, the attacker's reward is:

```
small equipment-loss credit
    + share of the round's larger next-buy-disruption credit
```

The magnitude of the first term must depend on equipment lost. A fixed 0.5
pool for any positive removal is not the desired small background term.
Deleting the floor entirely also loses behavior the owner wants: replacing
lost equipment consumes resources even when the next buy is preserved.

The next-buy term must not treat all existing opponent poverty as caused by
this round's kills. Compare like early-round histories and resource positions;
show both next loadout and remaining resources. An observed low loadout alone
does not distinguish inability to buy from a deliberate save or carryover.

Allocation of the large pool should include all economically contributing
kills, with the allocation rule displayed in the review. Exact allocation
weights and small/large term scales remain to be specified and measured.

## Zero-sum is a separate policy

This replacement/disruption mechanism can be implemented either as a transfer
or with an independent debit. The owner's described mechanism alone does not
mathematically require either choice.

To retain the requested independent-debit direction, calculate the victim
team's own resource burden separately, rather than setting its debit equal
to the opponent's credit. Teams can then both incur resource costs, and both
can also receive credit for disrupting the other side. Different magnitudes
must be justified by those distinct meanings, not chosen merely to force
non-zero totals. The same harm may be represented from two perspectives; using
separate function names is not itself evidence they should differ.

This clarification supersedes a simple adoption recommendation for the prior
`independent_no_floor` experiment. That experiment isolated two defects and
supplied useful examples; it did not implement this full buy-disruption design.

## What the stored data can support

The schema stores per-player round loadout, remaining credits, deaths, round
outcomes and kill events. It can show the actual next-round loadout and bank,
and it can stratify early-round histories and casualty counts.

It does not directly record every replacement purchase, donation, pickup,
victim weapon at death, or the counterfactual buy if equipment had survived.
The current committed-value proxy includes more than the gun itself. A
replacement/disruption estimator must label these approximations and should
not claim exact causal denial from observed next-round poverty alone.

## Next review to prepare

Before tuning A, B or C again, declare the exact replacement/disruption
estimator and comparisons, then show for the same example rounds:

- pistol/round-2 history and round outcome;
- equipment lost and deaths (including repeated-death ambiguity);
- survivors' equipment and the team's resources;
- actual next-round loadout and remaining bank;
- small replacement-cost term versus large buy-disruption term;
- each killer's allocation and each victim's independent debit.

Compare 0-5 deaths within comparable histories/resource groups, rather than
assuming three is a universal threshold. Reuse the fixed match review set and
match 3104's R5/R14/R16/R17 examples. No new corpus run or production formula
change is authorized by a numerical choice in this note, because none has
been made. The owner's direction authorizes developing this design further.
