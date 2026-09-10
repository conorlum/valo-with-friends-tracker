# Abyss: manual review of the proposed economy score

Match **3104**, Abyss, TEAM_1 **11–13** TEAM_2, August 24, 2026 Pacific.
This is the game from the economy issue prompt. The calculations below use
the V2 [implementation spec](../specs/2026-09-10-econ-buy-disruption-implementation.md).

**Finding:** the proposed kill credit distinguishes affordable losses from
losses followed by a constrained buy in this game. Three deaths do not
automatically earn a large bonus. However, the independent death penalty is
still a provisional design: all ten players finish this match with negative
net economy points. I would review that penalty before activating this model.

These are **economy points only**, at the declared review scale 1007.9209 and
C=1. They are not full kill Impact scores, round-win probabilities or fitted
weights. Damage, assists, kill-order and time scoring are outside this review.
The database was read in a read-only transaction; this work did not rescore it.

## What is being calculated

A lost starting kit earns a small credit. A larger, shared bonus activates
when next-round equipment plus pooled remaining credits cannot meet the
team's target. The size also reflects how far the observed buy fell below
that target. Each contributing kill receives a share proportional to the
equipment value exposed. The third kill does not collect everyone else's work.

For ordinary rounds, the reference is 3,900 paid equipment per player:

```
target H = 5 × 3900 = 19500
funding U = sum(min(next paid equipment, individual target)) + next team bank
funding gap D = max(0, H − U)
observed equipment gap G = sum(max(0, individual target − next paid equipment))
bonus pool = min(first-loss equipment L, G × min(1, D / 3900))

kill credit = 1007.9209 × [0.10 × victim exposure / 19500
                        + bonus pool / 19500 × victim exposure / L]
```

“Paid equipment” subtracts the project's agent free-ability allowance from
the raw loadout. It includes more than guns. The event's weapon field is the
killer's weapon, so it cannot tell us the victim's gun at death.
The bonus pool is a scoring index, not literal missing cash.

## 1. Round 6: expensive losses followed by a weak, constrained buy

TEAM_1 loses the round. Its five players lose 21,750 of starting paid
equipment. There are six recorded deaths because ZETA dies twice; counting
the starting kit twice would incorrectly raise the exposure to 26,250.

Here is TEAM_1 entering round 7:

| Player | Raw loadout | Paid loadout | Remaining bank | Equipment counted toward target |
|---|---:|---:|---:|---:|
| ZETA 3y5 | 4,650 | 4,500 | 2,350 | 3,900 |
| Helpless | 1,850 | 1,600 | 1,450 | 1,600 |
| Mokalover67 | 1,900 | 1,900 | 0 | 1,900 |
| 1xgoofy | 1,100 | 1,100 | 1,300 | 1,100 |
| VorteXx | 1,200 | 1,200 | 1,050 | 1,200 |

Manually adding those columns:

```
counted equipment = 3900 + 1600 + 1900 + 1100 + 1200 = 9700
bank = 2350 + 1450 + 0 + 1300 + 1050 = 6150
funding = 9700 + 6150 = 15850
funding gap = 19500 − 15850 = 3650
observed equipment gap = 19500 − 9700 = 9800
bonus pool = min(21750, 9800 × 3650/3900) = 9171.794872
```

Four players actually enter with weak loadouts, and the pooled estimate
cannot fund the reference buy. This is a strong example of the behavior the
owner wants to reward, subject to the resource-model limitations below.

Osmin gets all five first-kit enemy kills:

| Time | Victim | Exposed equipment | Small credit | Disruption credit | Total econ credit |
|---|---|---:|---:|---:|---:|
| 14.943s | ZETA | 4,500 | 23.26 | 98.08 | **121.34** |
| 15.581s | 1xgoofy | 4,600 | 23.78 | 100.26 | **124.04** |
| 15.855s | Helpless | 3,950 | 20.42 | 86.10 | **106.51** |
| 77.337s | VorteXx | 4,450 | 23.00 | 96.99 | **120.00** |
| 81.148s | Mokalover67 | 4,250 | 21.97 | 92.64 | **114.60** |

For the 1xgoofy kill, the independent substitution is:

```
1007.9209 × [0.10 × 4600/19500 + 9171.794872/19500 × 4600/21750]
= 23.776596 + 100.263934
= 124.040530 econ points
```

Earlier kills share the bonus with later kills. Their combat value can still
differ because of round state and timing. Within this same round, Mokalover's
kills on Najumi and DoubleBl1nd earn only **21.97** and **23.52** economy points:
the opposing team's following buy is adequately funded.

DoubleBl1nd's 28.759s kill on ZETA is a second recorded death and earns zero
*additional modeled equipment* credit. It still has combat impact. The data
cannot establish whether a newly picked-up gun was lost on that second death.

## 2. Round 3: three deaths, but the team absorbs the losses

TEAM_2 won the pistol and round 2, then wins round 3 while losing three
players. The lost kits total 9,700: DoubleBl1nd 2,650, Osmin 3,850 and
NPrightdolphin 3,200.

Their next raw loadouts are **4,000 / 4,800 / 4,750 / 4,850 / 4,950**.
Paid loadouts are 4,000 / 4,550 / 4,500 / 4,850 / 4,800; each reaches the
3,900 reference. Their next banks sum to:

```
1900 + 2200 + 650 + 5750 + 4900 = 15400
funding = 19500 + 15400 = 34900
funding gap = 0; observed equipment gap = 0; bonus pool = 0
```

Mokalover's 38.248s kill on Osmin therefore earns:

```
1007.9209 × 0.10 × 3850/19500 = 19.899977 points
```

The other two kills earn 13.70 and 16.54. These kills have a small equipment
cost, but there is no large next-buy bonus. **This directly rejects a fixed
“three deaths means damaged economy” rule.**

## 3. Round 22: four weak next loadouts, and why the first draft changed

TEAM_2 loses four starting kits totaling 16,100 and loses the round.
Its following raw loadouts are **600 / 2,150 / 700 / 700 / 4,650**.
In roster order NPrightdolphin, DoubleBl1nd, Osmin, Najumi, ternstyle:

```
next paid equipment = 600 / 1900 / 450 / 700 / 4500
counted equipment = 600 + 1900 + 450 + 700 + 3900 = 7550
bank = 1600 + 1800 + 1950 + 2650 + 2200 = 10200
funding = 7550 + 10200 = 17750
funding gap = 19500 − 17750 = 1750
observed equipment gap = 3300 + 2000 + 3450 + 3200 = 11950
bonus pool = min(16100, 11950 × 1750/3900) = 5362.179487
```

ZETA's 86.290s kill on DoubleBl1nd exposes 4,850:

```
1007.9209 × [0.10 × 4850/19500 + 5362.179487/19500 × 4850/16100]
= 25.068802 + 83.492804
= 108.561606 points
```

The other three contributing kills receive **78.34, 78.34 and 95.13**.
The survivor ternstyle's kill on Mokalover earns **17.32**, since TEAM_1's
next buy is funded. This distinguishes the two teams' economic consequences
within the same round.

The first draft used only the 1,750 cash gap as its bonus pool. That gave
ZETA's kill **52.32** and understated the wider observed buy downgrade relative
to the intended design. V2 adds the observed equipment gap, with the cash
gap controlling activation. This change was separately declared before
recalculation; [V1's results](walkthrough-v1.md) remain available.
**Round 22 helped develop V2; it is not independent evidence validating V2.**

## 4. Pistol cycle: a cheap loss does not automatically break the next buy

TEAM_1 loses the first pistol and round 2. Its round-2 first-kit exposure is
only 3,200, including a 600 self-death that creates no opponent credit.
Entering round 3, capped equipment is 17,550 and bank is 2,650, giving funding
of 20,200 against a 19,500 target. The large bonus is zero.

Osmin's 12.609s kill on 1xgoofy exposes just 300:

```
1007.9209 × 0.10 × 300/19500 = 1.550648 points
```

That kill can help win the current round while doing little additional harm
to the opponent's next buy. This is why economy and combat credit stay separate.

The pistol-winning TEAM_2 loses nobody in round 2. Its carryover targets are
3,300 / 2,650 / 3,000 / 2,900 / 3,850 rather than five forced rifle upgrades.
In the second half, the pistol-winning TEAM_1 loses three players in round 14
but still meets the carryover funding test: target 10,600 versus funding 20,250.

Both pistol winners win round 2/14 in this match. **There is no real example
here of the pistol-winning team losing its second round.** The target function
preserves the same replacement target after a loss, but that history still
needs a separate match review. Pistols themselves remain excluded in this
candidate; it evaluates the subsequent carryover cycle.

## 5. The previous suspicious deaths: rounds 14, 16 and 17

In round 14, ternstyle removes 800 and loses 1,150. His enemy credit is only
4.14, while his independent own loss costs 29.72, producing **−26** after
rounding the net once. There is no requirement that his death cost equal
Helpless's 5.94 credit for killing him.

For the consecutive first deaths to Helpless:

| Round | ternstyle's exposed equipment | Team next paid wealth | Large enemy bonus | Own econ loss |
|---|---:|---:|---:|---:|
| 16 | 4,000 | 41,750 | 0 | 20.68 |
| 17 | 4,500 | 31,850 | 0 | 23.26 |

Both teams' relevant next buys are adequately funded. The small difference
comes from equipment exposure; the old early/late floor jump disappears.
These become rounded player-round econ nets of **−21 and −23** for ternstyle.

## Findings that keep this a candidate

1. **The kill-side behavior is promising.** Six of 40 eligible team-rounds
   activate disruption: TEAM_1 rounds 6, 7, 16, 20 and 21; TEAM_2 round 22.
   The report accounts for every event in all 20 eligible rounds, including
   small and zero credits. Round 3 demonstrates that losses can be absorbed.
2. **The death-side magnitude is unresolved.** All ten match-total econ nets
   are negative, ranging from Osmin −100 to 1xgoofy −928. In round 6,
   1xgoofy's death costs 194.18 while Osmin earns 124.04. The debit charges
   depleted reserves even when a next buy remains possible. That is an extra
   product choice, not a consequence required by the user's buy-disruption
   definition. Non-zero-sum alone does not establish that it is fair. My
   recommendation is to review a debit focused on own buy disruption before
   tuning C; shrinking C would shrink both the useful credits and this cost.
3. **Affordability is estimated.** Pooled bank assumes optimistic sharing,
   and equipment value does not distinguish transferable guns from personal
   armor/utility. A partial kit plus cash is not always a feasible upgrade.
   A weak buy with adequate pooled funding earns no large bonus, even if the
   players chose poorly or the pooling approximation is overoptimistic.
4. **Causation is not demonstrated.** Pre-existing poverty, round outcome,
   spending choices, consumed utility and weapon recovery affect these inputs.
   The loss cap prevents an unbounded bonus, but does not isolate the damage
   these particular kills caused. “Constrained next buy” is the justified
   description; an exact counterfactual forced save is not established.
5. **One development match does not set the weights.** The 3,900 reference,
   activation threshold, 0.10 background and independent debit remain policy
   choices. Review a fixed ten-match sample, including missing pistol histories,
   before accepting the production formula or recalibrating its scale.

## Verification and reproducibility

The standalone calculator passes 17 numerical tests. Reintroducing V1's
cash-gap-only allocation behind the same API produces two numerical test
failures. The final calculator also passes 163 reconciliation checks on this
match. The selected worked examples are checked separately with decimal
arithmetic, without calling the calculator.

The saved [source snapshot](source.json) and [exact calculations](calculations.json)
allow inspection without relying on current database contents. The
[complete walkthrough](walkthrough.md) lists every eligible round's inputs,
every kill's economy credit and victim debit, and every player's totals.
The reference calculator is outside production and has no activation path.

From `webapp`, reproduce the saved snapshot calculation with:

```powershell
.\.venv\Scripts\python.exe scripts\review_abyss_buy_disruption.py --source ../docs/superpowers/abyss-buy-disruption-review/source.json
.\.venv\Scripts\python.exe -m pytest tests/test_econ_buy_disruption_reference.py -q
```
