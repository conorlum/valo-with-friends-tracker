# Impact measurements (Layer 1)

**Status:** measurement record, no design decisions
**Date:** 2026-09-04

## What this document is, and what it is not

This is the **descriptive layer** for the Impact scoring work. It contains
conditional quantities and nothing else: what was measured, over what
denominator, under what conditioning, with what uncertainty.

It contains **no scoring decisions**. Those live in the policy specs:

- `specs/2026-09-03-plant-window-and-time-factor-design.md` -- the time component
- `specs/2026-09-04-econ-impact-separate-component-design.md` -- the econ component

The separation exists because these were previously interleaved, and causal
language leaked from the design into the measurements -- phrases like "what the
destruction achieved" attached to what is only an association. **Nothing in this
document licenses a causal claim.** Every entry is "conditional on X, the
observed rate of Y was Z".

Policy documents cite measurements by ID (`M1`, `M2`, ...). When a measurement
is re-run and changes, the citations identify exactly which decisions need
revisiting.

## Method

**Dataset.** All measurements are on the full Render dataset synced to local
Postgres on 2026-09-03: **3,124 matches, 65,929 rounds, 487,844 kill events**,
spanning 2022-10-10 to 2026-09-01. Where a figure predates the sync and comes
from the 1,151-match subset, it is marked **[SUBSET]** and should be re-run
before it is relied on.

**Clustering, and why raw n is misleading.** Kills cluster within rounds, rounds
within matches, matches within a **small recurring friend group** -- the same
10-20 people appear across the whole dataset. A cell reporting n=5,000 kills may
be 300 matches and 40 distinct lineups. Raw n badly overstates precision.

**Bootstrap.** Intervals are 95%, computed by resampling **matches** with
replacement (2,000 draws unless noted) and recomputing the statistic. For
player-level statistics the resampling unit is the **player** instead. Where a
difference between two groups is reported, the difference itself is
bootstrapped, not inferred from whether two intervals overlap.

**Exclusions applied throughout.** Surrendered rounds; phantom plants (`M16`);
and, where a next round is required, the halftime boundaries (rounds 12 and 24).

**Standing limitation.** Every measurement conditioning on "rounds that were
eventually planted" is subject to **post-treatment selection** -- the plant is
downstream of the kills being measured. Holding man-advantage fixed addresses
one confound, not that one. These are conditional associations, not effects of
time. Attacker/defender differences, map and site, patch era, and latent execute
intensity are uncontrolled.

---

## Time and the plant window

### M1 -- Round-win rate by proximity to the plant, within even man-advantage states

Killer's-team round win %, pre-plant kills in planted rounds, by seconds until
the plant. Conditioned on the man-advantage state before the kill.

| state | <-30s | -30..-20 | -20..-10 | -10..-5 | -5..0 |
|---|---|---|---|---|---|
| 5v5 | 57.0 [56.2,57.8] | 67.5 [66.4,68.5] | 69.7 [68.9,70.5] | 71.3 [70.1,72.7] | 70.0 [67.8,72.1] |
| 4v4 | 56.7 [55.1,58.2] | 66.9 [65.1,68.7] | 70.9 [69.7,72.1] | 71.6 [70.1,73.1] | 70.7 [68.9,72.5] |
| 3v3 | 55.5 [52.9,58.0] | 66.4 [63.7,68.9] | 71.6 [69.8,73.3] | 75.1 [73.2,77.1] | 73.4 [71.1,75.6] |
| 2v2 | 57.6 [53.1,62.4] | 67.5 [63.4,71.8] | 76.6 [74.0,79.2] | 80.4 [77.6,83.1] | 80.0 [76.7,83.2] |

Far and near intervals do not overlap in any state. The `-5..0` bucket is at or
below `-10..-5` in all four states.

**Does not establish:** that timing causes the outcome, or that this holds
outside even states (it does not -- see `M3`).

### M2 -- The same analysis on ABSOLUTE round clock is flat

Bucketing pre-plant kills by clock time rather than distance to the plant, within
even states: 5v5 runs 69.4 / 69.6 / 67.9 / 71.5 / 64.2 across the clock; 3v3
moves 1.4pp end to end. **[SUBSET]**, but the direction is a null and the
mechanism is understood -- absolute clock averages "5s before a plant" together
with "5s into a quiet round". Not worth re-running.

### M3 -- The proximity association reverses sign with man-advantage

Lift from the far bucket (`<-30s`) to the near bucket (`-10..-5s`), by the
killer's man-advantage at the moment of the kill.

| advantage | lift | 95% CI | n |
|---|---|---|---|
| -3 | -31.2pp | [-36.7, -26.1] | 1,217 |
| -2 | -26.5pp | [-29.0, -23.9] | 5,463 |
| -1 | -4.5pp | [-6.1, -2.9] | 15,938 |
| 0 | +16.5pp | [+15.5, +17.6] | 30,104 |
| +1 | +29.6pp | [+28.0, +31.2] | 11,970 |
| +2 | +27.9pp | [+24.2, +31.7] | 3,656 |

Linear in advantage: `lift = +8.9pp + 13.9pp per man`, **R^2 = 0.933**, with
mild saturation at the extremes (residuals -7.7pp at -2, -8.7pp at +2).

**Note on the mechanism, offered as interpretation not measurement:** these are
pre-plant kills in rounds that *were* planted, so a kill while heavily behind
co-occurs with the plant happening regardless.

### M4 -- Attacker and defender differ in both slope and intercept

Same lift statistic, fitted separately by side:

| side | slope | intercept |
|---|---|---|
| attacker | +6.4pp per man | -0.5pp |
| defender | +9.6pp per man | +11.6pp |

**Pooled side figures are a Simpson's artifact and must not be quoted.** At every
advantage level both sides order the same way; a pooled comparison reverses
(+10.9pp attacker vs -2.5pp defender) purely through their differing advantage
distributions.

### M5 -- Mass distribution over the affected kills

Pre-plant kills in planted rounds -- the only population any proximity term
touches -- are **168,370 = 34.7% of all kills**. The remaining 65.3%
(post-plant, and all never-planted rounds) are untouched.

Within those, by advantage: -1 = 22.9%, 0 = 42.1%, +1 = 17.6%, so **82.6% lie in
adv -1..+1**. Tail cells: `adv <= -2` within 10s of the plant is **0.70%** of all
kills (1.42% within 10s of either side); `adv >= +1` within 10s is 2.21%
(4.08%).

### M6 -- Overtime: the shape persists; the level differences mostly do not

OT loadouts are 94.7% full-buy with p10 = 4350, against regulation's 57.4% and
p10 = 1550 -- economy is heavily compressed, **not eliminated**.

Defender share of window kills, difference bootstrapped by match:

| bucket | regulation | overtime | difference |
|---|---|---|---|
| -30..-20 | 37.8% (26,167) | 35.9% (482) | -1.9pp [-5.5, +1.7] |
| -20..-10 | 35.2% (48,135) | 37.6% (939) | +2.4pp [-0.1, +4.9] |
| -10..-5 | 30.0% (30,790) | 32.9% (636) | +2.9pp [-0.3, +5.9] |
| -5..0 | 42.6% (22,933) | 42.7% (424) | +0.1pp [-4.3, +4.8] |
| 0..+5 | 49.7% (28,321) | 54.5% (547) | +4.8pp [+0.4, +8.6] |
| +5..+15 | 46.4% (54,926) | 45.1% (1,192) | -1.3pp [-4.0, +1.5] |

**One of six differences excludes zero; two are negative.** What does hold is the
*shape*: OT reproduces the `-10..-5` trough (32.9%, its lowest) and the
post-plant rise to near-even (54.5%, its highest).

### M7 -- Late kills in never-planted rounds are mop-up

2,968 kills at t>=70s in never-planted rounds are overwhelmingly lopsided states
(2v1 99.7%, 3v1 99.4%, 4v1 100%). Even-state cells are rare and not elevated:
2v2 85.3% (n=170) against an 80.3% baseline; 3v3 75.9% (n=83) against 77.8%.
**[SUBSET]**

### M8 -- Composition of the plant window **[SUBSET]**

Kills in `[plant-30, plant+15]`, defender share: -30..-20 38.4%, -20..-10 35.0%,
-10..-5 **30.1%**, -5..0 42.7%, 0..+5 **49.7%**, +5..+15 46.3%. Total 77,431
kills over 14,998 planted rounds. Retained for shape; superseded on counts by
`M5` and `M6`.

---

## Economy

### M9 -- The killer's own loadout, holding team economy fixed

Killer's-team round win %, even states, by the killer's loadout band within a
fixed team full-buy differential:

| team full-buy diff | killer poor | killer mid | killer rich |
|---|---|---|---|
| -5..-3 | 55.2% | 62.8% | 64.3% |
| -2..-1 | 55.0% | 66.2% | 67.5% |
| 0 | 60.0% | 84.8% | 71.5% |
| +1..+2 | 81.3% | 82.8% | 78.0% |
| +3..+5 | 79.6% | 82.8% | 82.3% |

Roughly 9pp when the team is behind, flat or reversed otherwise, against a
team-context effect spanning 55% -> 82%. The pooled cross-tab (+12 to +26pp) is a
composition artifact.

### M10 -- "Value destroyed" is largely a measure of enemy wealth

Round 2, teams that won it, by credits destroyed:

| bucket | credits destroyed | n | mean enemies killed | mean destroyed | enemy team TOTAL | win R3 |
|---|---|---|---|---|---|---|
| Q1 | 0 - 2,500 | 769 | 4.96 | 1,869 | 1,890 | 36.3% |
| Q2 | 2,500 - 3,450 | 793 | 4.98 | 2,938 | 2,944 | 37.7% |
| Q3 | 3,450 - 4,900 | 765 | 4.99 | 4,022 | 4,031 | 42.6% |
| Q4 | 4,900+ | 797 | 4.96 | 10,767 | 10,861 | 66.2% |

**Every bucket killed ~5 enemies and destroyed ~99% of the enemy loadout.** The
gradient is the enemy's committed value, not the amount destroyed.

**Withdrawn on the basis of this:** a decile version of the same table was read
as showing a *threshold*. That reading was confounded by enemy wealth and is
retracted.

### M11 -- Enemy full-buy count next round vs winning it

| enemy full-buys next round | you win it |
|---|---|
| 0 | 76.2% [73.8, 78.5] |
| 1 | 64.6% [61.5, 67.5] |
| 2 | 55.5% [52.9, 58.3] |
| 3 | 49.8% [47.7, 51.8] |
| 4 | 43.0% [41.6, 44.5] |
| 5 | 39.4% [37.9, 40.9] |

Monotone, non-overlapping end to end.

**Does not establish** that the kills caused the next-round buy state. Next-round
loadout is determined by prior cash, the round result, the loss-bonus ladder,
survival, weapon recovery, teammate drops and purchase choice.

### M12 -- Destruction vs next round, conditioned on the enemy's buy state

LOW vs HIGH destruction tercile, win the next round:

| rounds | enemy buy state | LOW | HIGH | delta |
|---|---|---|---|---|
| 2-4 / 14-16 | broke (0-1) | 44.1% [43.2,45.0] | 55.0% [53.9,56.1] | +10.9pp |
| 2-4 / 14-16 | partial (2-3) | 40.9% [39.0,42.7] | 63.5% [61.7,65.3] | +22.6pp |
| 2-4 / 14-16 | full (4-5) | 39.3% [37.5,41.2] | 63.1% [61.4,64.9] | +23.8pp |
| 5-7 / 17-19 | broke (0-1) | 46.6% [44.3,48.7] | 59.9% [57.7,62.1] | +13.3pp |
| 5-7 / 17-19 | partial (2-3) | 42.3% [40.5,44.0] | 63.2% [61.4,65.0] | +20.9pp |
| 5-7 / 17-19 | full (4-5) | 39.5% [38.4,40.8] | 54.8% [53.6,56.0] | +15.3pp |
| 8-11 / 20-23 | broke (0-1) | 47.3% [44.7,49.6] | 60.2% [57.8,62.6] | +12.9pp |
| 8-11 / 20-23 | partial (2-3) | 46.1% [44.3,48.0] | 60.8% [59.0,62.6] | +14.7pp |
| 8-11 / 20-23 | full (4-5) | 40.1% [38.9,41.2] | 52.8% [51.7,53.9] | +12.7pp |

Nine cells, all positive, all intervals separated. A mild taper (~+23pp early to
~+13pp late), not a cliff.

**Withdrawn on the basis of this:** an unconditioned version of this test
reported the association "decaying to nothing by round 4/16". That was an
artifact of not conditioning on the state the destruction acted against.

### M13 -- Round 4's economy is largely determined by rounds 2-3

Mean enemy full-buys in round 4, by their record in rounds 2-3:

| their record | mean enemy full-buys in R4 | n |
|---|---|---|
| lost both | 1.18 | 2,754 |
| split 1-1 | 3.29 | 3,167 |
| won the round before | 3.54 | 5,921 |

Conditioned on that state, round 4 destruction shows +12.8 / +26.7 / +22.7pp
across the three buy bands -- among the largest, not the smallest.

### M14 -- `_combine_swing_factors` suppresses the realized signal

Over 37,774 team-rounds (900 most recent matches), using the shipped functions:

| outcome | count | share |
|---|---|---|
| agree -> kept | 20,800 | 55.1% |
| disagree -> discarded | 12,563 | 33.3% |
| ex-ante exactly 1.0 | 3,102 | 8.2% |
| realized exactly 1.0 | 1,309 | 3.5% |

A neutral 1.0 is returned for **44.9%** of team-rounds. Where both halves are
informative they agree 62.3% of the time. When kept, the combination takes
whichever deviates further from 1; the ex-ante factor spans 0.20-2.74 (mean
1.301) against realized's 0.50-1.50 (mean 0.930), so ex-ante usually dominates
even when realized survives.

---

## Data hygiene

### M15 -- The attacking-side convention, verified

Deriving the attacker independently from round outcomes (a Time Win or Defuse
Win implies that team defended; a Detonate Win implies it attacked) and
comparing against the 1-12 / 13-24 convention: **5,251 outcome-determinable
rounds, 100.00% agreement, 0 exceptions.**

Overtime alternates per round: `TEAM_1` on odd rounds past 24, `TEAM_2` on even.
**97 determinable OT rounds, 0 exceptions.** There are 448 OT rounds.

**Prior art:** `app/services/map_side_stats.py:35` derived the same OT rule from
20 regulation and 10 overtime matches on 2026-08-23. Its form
(`offset = rn - 25; TEAM_1 if offset % 2 == 0`) is algebraically identical. This
measurement is a larger-sample confirmation, not a new result.

### M16 -- Phantom plants

24 planted rounds end in a *Time Win*, all with `plant_time > 100s`. A genuine
plant forces an explode or a defuse, so a planted round decided by the timer
never armed. Six further rounds have `plant_time > 100s` but end in Elimination
Wins; those are legitimate rounds with noisy timestamps.

---

## Deferred measurements

### M17 -- Site participation (deferred 2026-09-04)

Share of a defender's kills in planted rounds landing inside the window.
Chronological split-half, bootstrapped by player:

| gate | n players | r | 95% CI | Spearman-Brown |
|---|---|---|---|---|
| >=30 kills/half | 138 | +0.177 | [-0.021, +0.352] | +0.301 |
| >=50 kills/half | 78 | +0.300 | [+0.090, +0.483] | +0.461 |
| >=80 kills/half | 51 | +0.267 | [-0.005, +0.517] | +0.422 |

**Variance decomposition** (86 players, >=100 kills): observed between-player SD
5.49pp, expected binomial sampling SD 3.44pp, implied **true SD 4.28pp**,
reliability ceiling **0.608**. Real differences exist but barely exceed
measurement error; the middle half of players sits inside 7pp.

**Drift:** random split gives +0.446 against chronological +0.177 at the >=30
gate -- the measure describes current form, not a persistent trait.

**Denominator effect:** a *rate* over eligible rounds roughly doubles
reliability on identical events -- window-kill rate +0.573 chronological at
>=150 rounds/half (n=46), +0.855 at >=300 (n=24); window-death rate +0.550 /
+0.776. **`corr(window-kill rate, overall kill rate)` is unmeasured**, so the
rate may be carrying volume rather than location.

**Withdrawn:** "a genuinely stable trait, an order of magnitude more reliable
than the efficiency residual". The efficiency residual (window K/D minus overall
K/D) remains dead: split-half +0.062, no out-of-sample prediction.

### M18 -- Agent role and site participation

Between players, agent explains **2%** of variance in the participation share
(sd 5.66pp -> 5.60pp after removing agent-mix expectation, 27 players).

Within players, on the full dataset: Sentinel-Duelist **-0.7pp (13/29)**,
Controller-Duelist **+2.1pp (17/25)**, Sentinel-Controller -2.2pp (8/23),
Initiator-Duelist +1.1pp (13/24).

**Withdrawn:** a subset measurement gave Sentinel-Duelist +4.6pp (n=10) and
Controller-Duelist +5.7pp (n=8), and was written into a design as "where the
agent signal lives". The full data does not support it. **Any role claim needs
n >= 25 players per pair.**

---

## Register of withdrawn claims

Kept so they are not rediscovered as findings.

| claim | why withdrawn |
|---|---|
| "Six for six is not noise" (OT defender share) | `M6` -- two of six negative, one of six excludes zero |
| Destruction shows a *threshold* in the deciles | `M10` -- confounded by enemy wealth |
| Destruction decays to nothing by round 4/16 | `M12`, `M13` -- artifact of not conditioning on buy state |
| Within-player role effect ~5pp | `M18` -- subset artifact, n=8-10 |
| Site participation is a stable player trait | `M17` -- reliability halved and spans zero at two of three gates |
| The econ factor's direction is "exactly backwards" | `M9` -- a between-context artifact |
| Pooled attacker vs defender proximity difference | `M4` -- Simpson's artifact |
