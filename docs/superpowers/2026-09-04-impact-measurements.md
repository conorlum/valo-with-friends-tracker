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

**What the match bootstrap does NOT cover.** Resampling matches handles
dependence *within* a match. It treats matches as independent, which they are
not: the same players, parties and lineups recur across the whole dataset, and
matches cluster in sessions and in time. **Intervals here are therefore lower
bounds on uncertainty**, and the narrowest of them -- `M1`'s, some spanning
under 1pp -- are the most likely to be optimistic. A session/date block
bootstrap, or multiway clustering by match and player, would be more
defensible; neither has been run. Treat interval *separation* as the usable
signal and exact widths as indicative.

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

**Sample:** non-self pre-plant kills in non-phantom planted rounds, killer's
alive count == victim's. Round-win % for the killer's team, by absolute clock.
Full dataset, match-level bootstrap.

| state | <20s | 20-35 | 35-50 | 50-65 | >=65 |
|---|---|---|---|---|---|
| 5v5 | 65.0 [64.5,65.6] n=32,362 | 65.5 [64.4,66.6] n=6,519 | 64.7 [62.2,67.4] n=1,323 | 64.3 [58.8,69.4] n=322 | 64.7 [55.8,73.2] n=116 |
| 4v4 | 67.6 [66.5,68.6] n=6,815 | 67.6 [66.5,68.7] n=7,340 | 66.2 [64.3,68.1] n=2,371 | 65.3 [62.1,68.6] n=882 | 69.9 [64.9,74.9] n=326 |
| 3v3 | 67.5 [64.5,70.4] n=948 | 69.6 [68.2,71.1] n=3,890 | 69.9 [68.0,71.9] n=2,255 | 67.1 [64.1,70.0] n=1,004 | 70.9 [67.2,74.6] n=581 |
| 2v2 | -- | 74.4 [71.7,76.9] n=991 | 73.9 [71.3,76.4] n=1,126 | 75.5 [72.2,78.8] n=677 | 73.6 [69.7,77.1] n=522 |

Flat in every state, intervals overlapping throughout. Previously **[SUBSET]**
without intervals; now full-data with them.

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

### M7 -- Late kills in never-planted rounds

**Sample:** non-self kills at `t >= 70s` in rounds never planted. Full dataset,
**9,235** such kills (the subset figure was 2,968).

Overwhelmingly lopsided states: 4v1 100.0% [100.0,100.0] n=557; 3v1 99.6%
[99.2,99.9] n=980; 2v1 99.2% [98.6,99.7] n=1,163; 3v2 96.4% [94.8,97.8] n=583.

Contested even states, against a within-state baseline of kills at `t < 50s` in
never-planted rounds, delta bootstrapped by match:

| state | early (t<50s) | late (t>=70s) | delta |
|---|---|---|---|
| 2v2 | 79.2% [77.6,80.7] n=2,792 | 84.0% [81.0,87.1] n=539 | **+4.9pp [+1.5,+8.3]** excludes 0 |
| 3v3 | 77.1% [75.9,78.2] n=5,216 | 78.3% [73.6,82.9] n=286 | +1.3pp [-3.4,+6.1] spans 0 |

**Correction to the subset version**, which stated these cells were "not
elevated". On full data 2v2 late kills are modestly but detectably elevated
(+4.9pp); 3v3 remains null. The effect is small relative to proximity (`M3`,
16-30pp), which is why the policy conclusion is unchanged -- but "not elevated"
was wrong and is withdrawn.

### M8 -- Composition of the plant window **[SUBSET]**

Kills in `[plant-30, plant+15]`, defender share: -30..-20 38.4%, -20..-10 35.0%,
-10..-5 **30.1%**, -5..0 42.7%, 0..+5 **49.7%**, +5..+15 46.3%. Total 77,431
kills over 14,998 planted rounds. Retained for shape; superseded on counts by
`M5` and `M6`.

---

## Economy

### M9 -- The killer's own loadout, holding team economy fixed

**Sample:** non-self kills where killer's alive count == victim's. `ctx` =
killer's team full-buy count minus victim's team full-buy count, both measured
in the round the kill occurs. Loadout bands: poor `<2100`, mid `2100-4249`,
rich `>=4250` credits.

| ctx | killer poor | killer mid | killer rich |
|---|---|---|---|
| -5..-3 | 57.3 [55.6,59.0] n=4,808 | 63.5 [62.3,64.6] n=10,565 | 64.0 [62.2,65.8] n=3,801 |
| -2..-1 | 59.8 [57.7,61.9] n=2,971 | 67.7 [66.5,68.8] n=9,315 | 68.2 [67.3,69.1] n=16,679 |
| 0 | 70.5 [69.8,71.1] n=19,362 | 82.8 [82.0,83.6] n=9,446 | 72.2 [71.5,72.8] n=16,904 |
| +1..+2 | 77.2 [73.9,80.6] n=759 | 81.3 [80.1,82.5] n=5,760 | 75.5 [74.8,76.2] n=26,401 |
| +3..+5 | 82.6 [76.8,87.8] n=195 | 81.8 [80.1,83.5] n=2,438 | 81.2 [80.5,81.9] n=23,965 |

Roughly 6-8pp when the team is behind; flat or reversed at parity and above
(`+1..+2` runs 77.2 / 81.3 / 75.5, rich lowest). Team context spans 57% -> 82%.

**Known limitation of `ctx`:** a differential of 0 conflates "both teams have
zero full buys" with "both have five". The 82.8% mid cell at `ctx = 0` is
likely that conflation rather than a real spike, and the band should be split
by level before anything is built on it.

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

### M11 -- Enemy full-buy count next round vs winning that round

**Sample:** one row per (team, round N) where rounds N and N+1 both exist and
are usable, N not in (12, 24), N <= 24, both teams have `round_player_stats`.
**No restriction on round N's outcome.** n = 116,538 team-rounds over 3,124
matches.

| enemy full-buys in N+1 | you win N+1 | n |
|---|---|---|
| 0 | 62.9% [62.4, 63.3] | 21,093 |
| 1 | 56.2% [55.3, 57.0] | 10,945 |
| 2 | 55.0% [54.2, 55.9] | 11,994 |
| 3 | 50.2% [49.5, 50.8] | 16,833 |
| 4 | 44.4% [43.9, 45.0] | 25,458 |
| 5 | 41.4% [40.9, 42.0] | 30,215 |

Monotone, non-overlapping end to end, **21.5pp** across the range.

**Correction:** an earlier version of this entry reported 76.2% -> 39.4%, a
36.8pp spread. That was computed only on rounds 2/3/14/15 **that the team won**,
a restriction the entry did not state. Conditioning on winning round N inflates
the spread. The unrestricted figures above supersede it.

**Does not establish** that the kills caused the next-round buy state, which is
jointly determined by prior cash, the round result, the loss-bonus ladder,
survival, weapon recovery, teammate drops and purchase choice.

### M12 -- Destruction vs next round, conditioned on the enemy's buy state

**Sample:** as `M11`. `enemy buy state` is their full-buy count **entering round
N** -- the round the kills happen in, not N+1. `destroyed` is the summed loadout
of that team's players killed in round N, in credits. Terciles are formed
**within each (round group x buy state) cell**, so cut points differ by cell and
are given below. Deltas are bootstrapped by match, not inferred from overlap.

| rounds | buy state | tercile cuts | LOW | HIGH | delta |
|---|---|---|---|---|---|
| 2-4 / 14-16 | broke 0-1 | 3,700 / 9,600 | 44.1 [43.2,45.0] n=6,622 | 55.0 [53.9,56.2] n=6,639 | **+10.9 [+9.6,+12.4]** |
| 2-4 / 14-16 | partial 2-3 | 10,800 / 18,300 | 40.9 [39.0,42.7] n=2,789 | 63.5 [61.7,65.4] n=2,798 | **+22.6 [+20.1,+25.1]** |
| 2-4 / 14-16 | full 4-5 | 9,700 / 21,050 | 39.3 [37.4,41.2] n=2,497 | 63.1 [61.3,64.9] n=2,515 | **+23.8 [+21.3,+26.4]** |
| 5-7 / 17-19 | broke 0-1 | 8,800 / 12,250 | 46.6 [44.4,48.7] n=1,885 | 59.9 [57.6,62.1] n=1,891 | **+13.3 [+10.2,+16.3]** |
| 5-7 / 17-19 | partial 2-3 | 12,400 / 18,550 | 42.3 [40.5,44.1] n=2,843 | 63.2 [61.4,65.0] n=2,869 | **+20.9 [+18.4,+23.5]** |
| 5-7 / 17-19 | full 4-5 | 13,250 / 22,300 | 39.5 [38.2,40.7] n=6,106 | 54.8 [53.6,56.0] n=6,145 | **+15.3 [+13.5,+17.1]** |
| 8-11 / 20-23 | broke 0-1 | 9,300 / 13,000 | 47.3 [44.7,49.7] n=1,543 | 60.2 [57.7,62.5] n=1,582 | **+12.9 [+9.3,+16.3]** |
| 8-11 / 20-23 | partial 2-3 | 12,950 / 18,750 | 46.1 [44.3,48.0] n=2,863 | 60.8 [59.0,62.6] n=2,901 | **+14.7 [+12.2,+17.3]** |
| 8-11 / 20-23 | full 4-5 | 13,500 / 22,450 | 40.1 [38.9,41.3] n=7,148 | 52.8 [51.7,54.0] n=7,243 | **+12.7 [+11.1,+14.3]** |

**All nine deltas exclude zero.** A mild taper (~+23pp early to ~+13pp late),
not a cliff. Note the tercile cuts themselves rise with round number and buy
state, so "HIGH" is not a fixed credit amount across rows.

**Withdrawn on the basis of this:** an unconditioned version reported the
association "decaying to nothing by round 4/16". That was an artifact of not
conditioning on the state the destruction acted against.

### M12a -- M12 adjusted for kill count and round-N outcome: the effect does not survive

`M12` compares LOW vs HIGH *total destroyed value*. High destruction co-occurs
with killing more enemies and with winning round N, both of which predict
winning N+1 on their own. Measured:

```
corr(destroyed, enemies killed) = +0.652
corr(destroyed, won round N)    = +0.474
```

Holding **both** fixed -- terciles of destroyed formed inside each
(round-N outcome x enemies killed) cell, deltas bootstrapped by match:

| round N | enemies killed | delta (HIGH - LOW) on winning N+1 |
|---|---|---|
| WON | 4 | -0.8pp [-5.7, +4.1] spans 0 |
| WON | 5 | **-3.0pp [-4.0, -2.0]** excludes 0, **negative** |
| LOST | 1 | +0.4pp [-1.7, +2.6] spans 0 |
| LOST | 2 | +4.4pp [+2.7, +6.4] |
| LOST | 3 | +6.7pp [+4.8, +8.5] |
| LOST | 4 | +8.7pp [+6.4, +10.8] |
| LOST | 5 | +3.9pp [-1.5, +9.4] spans 0 |

And within the buy-state bands the econ weights would have been fitted from,
restricted to won rounds with all five enemies killed:

| buy state | delta | |
|---|---|---|
| broke (0-1) | **-4.2pp [-5.6, -2.8]** | negative |
| partial (2-3) | +2.8pp [+0.6, +4.8] | positive |
| full (4-5) | **-9.8pp [-11.5, -8.0]** | negative |

**`M12`'s headline effect is substantially kill count, and the remainder changes
sign by cell.** Among teams that *lost* round N, destruction still predicts
(+4 to +9pp). Among teams that *won* it and wiped the enemy, more destruction
predicts winning N+1 **less** -- consistent with the wealth-persistence
mechanism: a rich team you wipe collects a large loss bonus and rebuys, while a
poor team you wipe stays poor.

**Consequence:** the buy-state weights `w(state)` proposed in the econ policy
spec **cannot be fitted from `M12`**. Two of the three bands would have taken
the wrong sign. `M12` remains valid as the unadjusted association it reports;
it is not a basis for scoring weights.

### M13 -- Round 4/16 economy is largely determined by rounds 2-3

**Sample:** one row per (team, half) where rounds 2, 3 and 4 of that half all
exist and are usable. Categories are the **exact pair of outcomes** in rounds 2
and 3, so they are mutually exclusive by construction -- an earlier version used
a loss-streak encoding whose labels ("split 1-1", "won the round before")
overlapped and collapsed two distinct states.

| enemy's rounds 2-3 record | mean enemy full-buys entering R4 | 95% CI | n |
|---|---|---|---|
| lost both | **1.17** | [1.12, 1.23] | 2,837 |
| won 2, lost 3 | 3.29 | [3.24, 3.33] | 3,209 |
| lost 2, won 3 | 3.02 | [2.97, 3.06] | 3,209 |
| won both | **4.15** | [4.11, 4.18] | 2,837 |

A **~3 full-buy** spread fixed before round 4 begins. The two split records
differ from each other (3.29 vs 3.02, intervals non-overlapping), which the
earlier three-category version hid.

### M14 -- `_combine_swing_factors` suppresses the realized signal

**Sample:** 37,774 team-rounds from the **900 most recent matches**, using the
shipped functions unmodified. The restriction is a compute bound, not a
selection: this measurement replays `_econ_swing_risk_factor` through the ORM
per round rather than in bulk SQL, so the full 3,124 matches would take roughly
3.5x as long. The quantity measured is a property of the *function*, not of the
data distribution, so the restriction is unlikely to matter -- but it has not
been verified on the full set and the figures should be read as approximate.

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
reliability ceiling **0.608**. **Caveat: the binomial term assumes independent
kills, which contradicts this document's own clustering premise** -- kills share
a player, match, map, agent and session. Real sampling variance is therefore
higher, the implied true SD lower, and 0.608 is an *over*estimate of the
ceiling. A hierarchical binomial model would be the defensible version. Real differences exist but barely exceed
measurement error; the middle half of players sits inside 7pp.

**Drift:** random split gives +0.446 against chronological +0.177 at the >=30
gate. This is *consistent with* drift but does not establish it -- differing
time composition and unequal opportunity across periods produce the same
pattern. "Describes current form" is an interpretation, not a measurement.

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
| `M12` supports fitting econ buy-state weights | `M12a` -- effect is largely kill count; sign flips by cell once adjusted |
| Within-player role effect ~5pp | `M18` -- subset artifact, n=8-10 |
| Site participation is a stable player trait | `M17` -- reliability halved and spans zero at two of three gates |
| The econ factor's direction is "exactly backwards" | `M9` -- a between-context artifact |
| Pooled attacker vs defender proximity difference | `M4` -- Simpson's artifact |
