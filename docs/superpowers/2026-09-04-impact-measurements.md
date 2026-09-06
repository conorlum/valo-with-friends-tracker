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

## Post-plant time

**Standing limitation for this whole section.** A round only *reaches* a given
second post-plant by not having resolved earlier, so every late cell conditions
on the round having stayed contested. Holding state and second fixed is
stronger than the raw curve but does not remove that selection. These are
conditional associations, not effects of time.

**A variable this schema does not have.** `rounds` carries only `defused` and
`defuse_time` — there is **no defuse-progress field**. A spike taken to half
and abandoned keeps that state, so a defender who dies mid-defuse leaves the
survivors needing 3.5s rather than 7s. Every state below is therefore a blend
of "no progress" and "partial progress" rounds, and the two cannot be
separated on this data. Riot's match API is not known to expose it either;
recovering it would need replay parsing.

### M21 -- Post-plant round-win rate by seconds since plant, split by side

**Sample:** 149,976 non-self post-plant pre-resolution kills in non-phantom,
non-surrendered planted rounds with a determinable winner. 81,598 attacker
kills, 68,378 defender kills. Full dataset, match-level bootstrap.

| bucket | ramp pays | attacker win% | defender win% | n att | n def |
|---|---|---|---|---|---|
| 0..5 | 1.048 | 80.9 [80.2,81.6] | 53.8 [52.8,54.7] | 14,490 | 14,378 |
| 5..10 | 1.141 | 80.0 [79.2,80.8] | 52.5 [51.5,53.5] | 15,375 | 13,397 |
| 10..15 | 1.235 | 80.7 [79.9,81.4] | 52.6 [51.5,53.6] | 14,678 | 12,605 |
| 15..25 | 1.369 | 83.5 [82.8,84.1] | 52.5 [51.5,53.4] | 22,532 | 18,217 |
| 25..35 | 1.550 | 90.2 [89.6,90.9] | 48.0 [46.6,49.4] | 11,186 | 7,759 |
| 35..45 | 1.720 | 97.8 [97.2,98.2] | 23.3 [21.3,25.4] | 3,337 | 2,022 |

"Ramp pays" is the mean `_time_factor` actually returned. It is side-blind.
The pattern holds **within every advantage level** (defender flat-to-declining,
attacker rising), so it is not a Simpson's artifact of the advantage mix.

**Does not establish** that these kills caused the outcomes. Raw win rate
cannot separate "this kill decided the round" from "this round was already
decided and a kill happened in it". For scoring purposes it is **superseded by
`M24`**, which measures the stakes instead.

Script: `diagnostics/measure_post_plant_time_curve.py`

### M22 -- The full-defuse deadline

**Sample:** as `M21`. A full defuse takes 7.0s against a 45s spike, so a
defender must start by plant+38 for it to complete.

| side | before +38s | at/after +38s |
|---|---|---|
| attacker | 83.0 [82.6,83.4] n=79,843 | 98.9 [98.3,99.4] n=1,755 |
| defender | 52.0 [51.3,52.7] n=67,383 | **11.7 [9.7,13.9]** n=995 |

Script: `diagnostics/measure_post_plant_time_curve.py`

### M23 -- Post-plant state-value function, and why the marginal understates

**Sample:** 42,404 usable planted rounds. `V(a, d, t)` = observed share of
rounds the attacking team won, given `a` attackers and `d` defenders alive at
whole second `t` after the plant with the round unresolved. One observation per
(round, second). **1,219 cells** met the 60-observation floor.

`V` for a 1v1, with nobody killing anybody:

| t | 0 | 15 | 25 | 30 | 35 | 38 | 43 |
|---|---|---|---|---|---|---|---|
| 1v1 | 0.515 | 0.566 | 0.634 | 0.715 | 0.824 | 0.925 | 0.981 |

**The attacking side gains ~41pp between the plant and +38s without any kill
occurring.**

**Estimand caveat, load-bearing.** `V(before)` already prices in the duel:
`V(before) = p*V(atk wins) + (1-p)*V(def wins)`, so

```
V(after | atk wins) - V(before)  =  (1 - p) * [V(atk wins) - V(def wins)]
```

A marginal difference is therefore **leverage shrunk by the probability of the
other outcome**, and collapses toward zero for expected results by construction.
It credits surprise, not stakes. `M24` measures the unshrunk quantity.

Script: `diagnostics/measure_post_plant_marginal_value.py`

### M24 -- Duel leverage by state and second

`leverage(a, d, t) = V(a, d-1, t) - V(a-1, d, t)` -- the swing in attacker win
probability between the two ways a duel can go, at fixed state and second.
Symmetric in the outcomes, so not subject to `M23`'s shrinkage.

Leverage as a **ratio to that state's own time-average** (the state term
divided out):

| t | 1v1 | 2v2 | 2v1 | 1v2 | 3v2 |
|---|---|---|---|---|---|
| 5 | 1.07 | 1.15 | 1.33 | 0.89 | 1.41 |
| 20 | 1.07 | 1.19 | 1.25 | 0.93 | 1.16 |
| 30 | 1.07 | 1.10 | 0.86 | 1.13 | 0.67 |
| 37 | 0.98 | 0.61 | 0.31 | 1.35 | 0.22 |
| 41 | 0.59 | 0.30 | 0.10 | 1.09 | -- |

Change in leverage from t=10 to t=35, match-bootstrapped, 400 draws:

| state | t=10 | t=35 | change |
|---|---|---|---|
| 1v1 | +0.992 | +0.966 | +0.026 [+0.016, +0.040] |
| 2v2 | +0.653 | +0.416 | +0.236 [+0.181, +0.289] |
| 2v1 | +0.462 | +0.176 | +0.287 [+0.255, +0.319] |
| 1v2 | +0.538 | +0.810 | **-0.273 [-0.306, -0.241]** |

**All four exclude zero and `1v2` takes the opposite sign.** The clock's effect
on duel stakes is state-dependent with reversing direction: with the attackers
up a man the duel matters progressively less; with them down a man it matters
progressively more.

Time-averaged leverage per state: 1v1 +0.927, 1v2 +0.631, 2v3 +0.574,
2v2 +0.567, 3v3 +0.493, 2v1 +0.330, 3v2 +0.274.

Two observations on `kill_order_bonus`, which is a hand-tuned state-transition
weight and therefore the same kind of object: (a) it ranks `1v1` highest at 250
against a floor of 40, which **agrees** with the measured ordering; (b) it is
**side-blind**, while `2v1` (+0.330) and `1v2` (+0.631) differ by ~2x.

**Does not establish** that the graph's weights are wrong -- only that a
side-blind graph cannot express a ~2x post-plant difference that the data shows.

Script: `diagnostics/measure_post_plant_duel_leverage.py`

### M25 -- What a death costs, split by which side lost the player

Leverage decomposes exactly:
`leverage = [V(a,d,t) - V(a-1,d,t)] + [V(a,d-1,t) - V(a,d,t)]`, the first term
what the attacking team loses to a death and the second what the defending team
loses. Weighted by where deaths actually occur, match-bootstrapped:

| band | attacker death cost | defender death cost |
|---|---|---|
| 0-15 | +0.1968 [+0.1954,+0.1982] n=39,971 | +0.1414 [+0.1403,+0.1425] n=44,542 |
| 15-25 | +0.2276 [+0.2256,+0.2305] n=18,042 | +0.1432 [+0.1413,+0.1455] n=22,372 |
| 25-32 | +0.2862 [+0.2808,+0.2917] n=6,045 | +0.1317 [+0.1290,+0.1347] n=8,684 |
| 32-38 | **+0.3542** [+0.3438,+0.3657] n=2,384 | +0.0992 [+0.0959,+0.1025] n=3,662 |
| 38-41.5 | +0.3396 [+0.3150,+0.3623] n=627 | +0.0489 [+0.0459,+0.0528] n=1,161 |
| 41.5-45 | +0.1765 [+0.1520,+0.2000] n=289 | **+0.0228** [+0.0201,+0.0257] n=467 |

Defender death cost falls monotonically to about a sixth of its early value.
Attacker death cost **rises** to a peak in 32-38s, then falls only past 41.5s.

Pooled attacker cost hides a large state split. At t=38: `2v1` **+0.066**
(a teammate survives to contest the defuse), `1v2` +0.597, `1v1` **+0.754**
(no attacker left to punish the exposure).

Script: `diagnostics/measure_post_plant_death_cost.py`

### M26 -- Two deadlines, and why late post-plant states are thin

A full defuse (7.0s) cannot complete past plant+38.0; a **half** defuse (3.5s),
including on a spike already taken to half and held, cannot complete past
**plant+41.5**. 1v1 leverage, decomposed:

| t | leverage | attacker risk | defender risk |
|---|---|---|---|
| 37 | +0.907 | +0.804 | +0.103 |
| 38 | +0.829 | +0.754 | +0.075 |
| 40 | +0.663 | +0.615 | +0.048 |
| 41 | +0.546 | +0.515 | +0.031 |
| 42 | +0.375 | +0.350 | +0.025 |
| 43 | +0.220 | +0.201 | +0.019 |
| 44 | +0.000 | +0.000 | +0.000 |

The largest single-second drops are **41 -> 42 (-0.171)** and 43 -> 44 (-0.220);
the 38s boundary produces a gentler bend. At t=38 the attacker carries **10x**
the defender's risk in the same duel.

Share of live post-plant rounds with 6 or more players alive: 16.3% at t=0,
6.9% at t=20, 1.7% at t=30, 0.5% at t=43 -- so `3v3` and larger states run out
of data late because they almost cease to exist, not through any filter.

Script: `diagnostics/measure_post_plant_death_cost.py`

---

## Scoring mechanics

### M19 -- The traded factor is 1 for most kills, so a net-contribution gate has little support

**Sample:** 168,432 non-self pre-plant kills in non-phantom, non-surrendered
planted rounds. Full dataset.

`_traded_factor` (`impact.py:185`) returns **exactly 1** unless the killer was
killed back within 10s, so `(1 - T) = 0` for every untraded kill.

| quantity | value |
|---|---|
| kills with `T < 1` | 47,631 / 168,432 = **28.3%** |
| `sum K*(1-T)` -- a net gate's total weight | 4,269,022 |
| `sum K` -- a kill-side gate's total weight | 24,095,920 |
| ratio | **17.7%** |

**Does not establish** that any particular scalar fails such a gate -- only
that 71.7% of the affected kills enter it at exactly zero weight, carrying
82.3% of the kill-side mass.

Script: `diagnostics/measure_traded_factor_vs_proximity.py`

### M20 -- Death-side residual after centring on the kill side

**Sample:** as `M19`. `resid = [E[K*T*s]/E[K*T]] / [E[K*s]/E[K]] - 1`, which is
0 iff `s` and `T` are uncorrelated under `K`-weighting. Match-level bootstrap,
2,000 draws. The scalar family is **not fitted** -- shape from `M1`'s 5v5 row
min-max normalised, amplitude from `M3`'s line `(8.9 + 13.9*adv)/100`, with `k`
a predeclared policy grid.

| k | mean scalar | % clamped | residual |
|---|---|---|---|
| 1.0 | 1.052 | 0.0% | +0.351% [+0.331, +0.371] |
| 2.0 | 1.103 | 2.4% | +0.670% [+0.632, +0.709] |
| 3.0 | 1.136 | 7.9% | +0.858% [+0.807, +0.911] |
| 5.0 | 1.162 | 26.9% | +0.981% [+0.913, +1.049] |
| 8.0 | 1.209 | 31.1% | +1.004% [+0.922, +1.086] |

Every interval excludes zero; the residual saturates below 1%.

The mechanism is an interaction that cancels in the margin. K-weighted mean `T`
by proximity bucket is nearly flat (0.8321, 0.8309, 0.8053, 0.8277, 0.8293),
but within advantage levels it is not: at `adv = -2` it falls 0.884 -> 0.713
from far to near, and at `adv = +2` it rises 0.736 -> 0.919.

**Does not establish** the residual under the *actual* fitted parameters. The
`k` sweep bounds magnitude sensitivity, not shape sensitivity, and the
advantage interaction is what drives the residual.

Script: `diagnostics/measure_traded_factor_vs_proximity.py`

### M29 -- The leverage column is 98.5% raw kill-order bonus

**Sample:** 83,840 player-rounds over 400 matches, replayed in **ex-ante** mode
(`use_realized_swing=False`), the mode the evaluation harness fits in. The
400-match cap is a **compute bound, not a selection** -- the replay runs twice
per match -- and is the same restriction `M14` carries.

`time_impact` (`impact.py:674`) stores `kill_order_bonus * time_factor` netted
across kills and deaths. To separate the two without reimplementing the scorer,
each match is replayed a second time with `_time_factor` pinned to `1.0`; that
run's `time_impact` **is** the raw kill-order bonus exactly, and
`delta = normal - pinned`.

| pair | correlation | 95% CI |
|---|---|---|
| `kill_order_bonus` vs **`time_impact`** (the fused column) | **+0.9845** | [+0.9838, +0.9853] |
| `kill_order_bonus` vs `time_delta` | **+0.4386** | [+0.4287, +0.4485] |
| `damage` vs `kill_order_bonus` | +0.8107 | [+0.8082, +0.8134] |
| `damage` vs `time_delta` | +0.3060 | [+0.2977, +0.3148] |

**The fused leverage column is almost entirely the state term.** At +0.9845 the
time factor contributes very little of `time_impact`'s variance, so a weight
fitted on that column is close to a weight on `kill_order_bonus` alone.

`time_delta` is zero on **58,437 / 83,840 = 69.7%** of player-rounds --
in ex-ante mode the pre-plant factor returns exactly 1.0, so the delta is
non-zero only where the player had post-plant involvement. That share agrees
with `M5`'s independent finding that ~69% of kills are not post-plant, which is
a consistency check on the replay. Restricted to the 25,403 rows where it is
non-zero, `corr(kill_order_bonus, delta)` is **+0.6493** [+0.6375, +0.6599].
Delta spread: p05 -32, p50 0, p95 +43, range -250 to +478.

**Relevance to the refit ceiling, offered as interpretation not measurement:**
the shipped formula's three factor terms are all `kill_order_bonus * <factor>`,
and this entry shows the multiplicand dominates at least one of them. That is
consistent with -- though it does not prove -- the +0.005 AUC refit ceiling
being an artifact of reweighting three near-copies of the same column.

**Does not establish** anything about the *redesigned* time factor, which does
not exist yet. These are today's shipped factors. A pre-plant proximity term
would raise the delta's share; this measures the current floor, not the future
one.

Script: `diagnostics/measure_leverage_decomposition.py`

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

### M12b -- The adjusted effect by round group: it survives early and inverts late

**Sample:** as `M12a`, restricted to `M12`'s own predeclared round groups
(rounds 1 and 13 excluded as pistols). n = 104,222 team-rounds over 3,124
matches. Terciles of destroyed formed inside each (round group x round-N
outcome x enemies killed) cell, cut points fixed from the full sample, HIGH-LOW
pooled across cells weighted by cell size, match bootstrap 600 draws.

| round group | unadjusted | **adjusted** |
|---|---|---|
| 2-4 / 14-16 | +15.67 [+14.53,+16.78] n=23,859 | **+10.42 [+9.35,+11.48]** n=22,600 |
| 5-7 / 17-19 | +15.00 [+13.50,+16.50] n=21,720 | **-2.19 [-3.73,-0.56]** n=21,084 |
| 8-11 / 20-23 | +11.82 [+10.32,+13.49] n=23,218 | **-5.19 [-6.57,-3.77]** n=22,690 |

Round by round, adjusted: 2/14 **+23.89 [+21.51,+26.31]**; 3/15 +13.86
[+11.47,+16.20]; 4/16 +1.23 [-1.41,+3.71] spans 0; 5/17 -0.91 [-3.38,+1.55]
spans 0; 6/18 -3.59 [-5.93,-0.92]; 7/19 -2.58 [-5.23,+0.15] spans 0.

Split by the enemy's buy state entering round N:

| round group | broke 0-1 | partial 2-3 | full 4-5 |
|---|---|---|---|
| 2-4 / 14-16 | **+15.32 [+13.58,+17.21]** | +2.76 [-0.04,+5.30] | **-4.23 [-6.93,-1.17]** |
| 5-7 / 17-19 | +8.29 [+4.91,+11.83] | +1.27 [-1.27,+4.00] | -4.50 [-6.28,-2.63] |
| 8-11 / 20-23 | +9.66 [+5.95,+13.28] | +1.02 [-1.70,+3.83] | -4.74 [-6.46,-3.08] |

Two readings, and the second matters more. **(i)** The adjusted effect lives in
rounds 2-3 / 14-15 and is gone by 4/16. **(ii)** The partial and full bands are
flat across round groups (+2.8/+1.3/+1.0 and -4.2/-4.5/-4.7), so the round-group
gradient in the first table is **largely composition** -- early rounds contain
more broke enemies. Buy state, not round number, is the operative axis.

**The buy-state ordering is the reverse of `M12`'s.** Adjusted, destruction is
worth most against **broke** teams and is **negative** against full-buy teams,
consistently in all three groups. This is the wealth-persistence mechanism
stated positively: destroy a broke team's gear and they stay broke; destroy a
rich team's gear and they collect a large loss bonus and rebuy.

**Does not establish** causation, and inherits `M11`'s caveat entirely. Note
also that a scoring rule taking `w(full) < 0` literally would **debit a player
for killing a rich enemy**, which collides with the standing constraint that no
kill is worth negative Impact.

Script: `diagnostics/measure_destruction_adjusted_by_round_group.py`

### M12c -- Kill count as the EXPOSURE, not a confound

`M12a` held enemies-killed fixed. If the economic path runs *through* kill count
-- kills -> they cannot rebuy -> you win N+1 -- then holding it fixed controls a
**mediator** and blocks the path being asked about. `M12a` therefore answers a
different question (at fixed kill count, does killing *richer* enemies help?
no), and this entry asks the other one.

**Sample:** as `M12`, split by round-N outcome. Read the LOST rows: losing
closes the won-the-round path, leaving the damage done on the way out.

Teams that **lost** round N -- your win rate in N+1, and the mediator (enemy
full-buy count entering N+1):

| kills | 2-4 / 14-16 | 5-7 / 17-19 | 8-11 / 20-23 |
|---|---|---|---|
| 0 | 48.3% (FB 2.66) | 41.3% (4.57) | 41.4% (4.68) |
| 1 | 46.6% (2.97) | 40.4% (4.51) | 40.0% (4.61) |
| 2 | 44.9% (3.10) | 39.8% (4.40) | 42.0% (4.51) |
| 3 | 42.6% (3.17) | 41.3% (4.21) | 43.6% (4.35) |
| 4 | 42.6% (3.10) | 43.5% (3.93) | 45.9% (4.08) |
| 5 | 41.5% (3.14) | 43.0% (3.75) | 46.8% (3.89) |

A kink rather than a cliff: flat or falling through 1-2 kills, then roughly
+1.5 to +2.3pp per kill. **It reverses before round 5** -- killing more while
losing predicts winning N+1 *less* in 2-4/14-16, and the enemy's next-round
full-buy count barely moves there, so the early pattern is the acting team's own
save decision rather than damage to the enemy.

**Caveat, load-bearing:** the differences in this entry are **point estimates
only** -- they were read off non-overlapping intervals, which this document's
own method section forbids. `M12d` is the version with bootstrapped differences
and should be cited in preference wherever the two overlap.

Script: `diagnostics/measure_kill_count_econ_threshold.py`

### M12d -- The denial depends on whether the enemy can REPLACE what was taken

**Sample:** as `M12c`, teams that lost round N, additionally stratified by the
enemy team's mean `round_player_stats.remaining` (their bank) in round N.
`remaining` is verified as a bank: 95.8% of players are under 500 on round 1 and
97.6% on round 13, the two pistol resets, capped at 9,000. Differences **are**
bootstrapped by match, 800 draws.

Rounds 5-11 / 17-23, enemy **committed** (bank < 500), n = 2,100:

| kills | enemy armed next | enemy full-buy next | gun diff (you-them) | win N+1 |
|---|---|---|---|---|
| 1 | 4.89 [4.85,4.92] | 4.18 [4.10,4.27] | -0.75 [-0.91,-0.61] | 46.6% |
| 2 | 4.74 [4.69,4.78] | 3.73 [3.65,3.81] | -0.50 [-0.61,-0.38] | 46.8% |
| 3 | 4.53 [4.48,4.58] | **2.96 [2.89,3.04]** | -0.18 [-0.28,-0.08] | 51.0% |
| 4 | 4.35 [4.26,4.42] | **2.15 [2.06,2.25]** | -0.00 [-0.13,+0.12] | 51.6% |

Bootstrapped differences, (>=4 kills) - (<=1 kill), rounds 5-11 / 17-23:

| enemy bank in round N | enemy **armed** next | gun differential | win N+1 |
|---|---|---|---|
| committed <500 (n=2,100) | **-0.60 [-0.68,-0.52]** | +0.82 [+0.65,+0.98] | +4.93pp [-1.18,+11.13] spans 0 |
| partial 500-1500 (n=9,297) | -0.38 [-0.42,-0.35] | +0.61 [+0.52,+0.70] | +5.10pp [+2.20,+8.08] |
| cash >=1500 (n=22,631) | **-0.09 [-0.10,-0.08]** | +0.09 [+0.04,+0.15] | +3.27pp [+1.34,+4.95] |

**Four kills against a committed team denies 6.7x more armament than four kills
against a cash-rich one** (0.60 players against 0.09), for the same kills and
comparable credits nominally destroyed. This is the axis `removed(T)` cannot
express.

In rounds 2-4 / 14-16 the same differences on winning N+1 are **negative**
(-6.71pp [-10.06,-3.37] and -5.42pp [-8.85,-2.24]), consistent with `M12c`.

**The mediator is cleanly graded; the outcome is not.** The armament differences
order monotonically by bank with non-overlapping intervals, but the win-rate
differences run +4.93 / +5.10 / +3.27 and the committed cell **spans zero** on
n=2,100. The mechanism is well evidenced; the payoff to it is not.

**Not measurable on this dataset:** the case of a team that *saved* round N
(own mean loadout < 2000) while killing 3-4 committed enemies and losing is
**n = 3 team-rounds**. The tactical concept is real; it cannot be fitted here.

Script: `diagnostics/measure_kills_vs_enemy_bank.py`

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

### M27 -- Own deaths while WINNING a round, and what carries forward

**A different exposure from every other `M12*` entry.** Those measure *enemies
killed / credits destroyed*. This measures a team's **own deaths**, conditioned
on that team having **won** round N. The two are the same table read from
opposite sides -- my deaths are your kills -- so one measurement serves both
readings.

**Why the outcome conditioning.** `M13` shows the loss-bonus ladder fixes a ~3
full-buy spread before round 4 begins, which is why a raw round-N+1 readout is
mostly reading the ladder in early rounds. Conditioning on the round-N outcome
pins the ladder position and varies only what was carried forward. Both arms of
every comparison below won the round.

**Sample:** 24,324 team-rounds over 3,124 matches, rounds 2/14 and 3/15, each
requiring the pistol round and round N+1 usable. Cells below 120 rows are
suppressed. Differences bootstrapped by match, 800 draws.

**(a) Round 2, pistol WINNERS that won round 2** -- n=5,047 over 3,010 matches:

| own deaths | own loadout R3 | own armed R3 | own full-buys R3 | win R3 | n |
|---|---|---|---|---|---|
| 0 | 3569 | 3.96 | 1.08 | 39.5% | 898 |
| 1 | 3470 | 3.67 | 1.17 | 40.7% | 1,420 |
| 2 | 3372 | 3.46 | 1.20 | 42.3% | 1,339 |
| 3 | 3331 | 3.28 | 1.39 | 44.4% | 922 |
| 4 | 3309 | 3.19 | 1.60 | 42.3% | 435 |

(3-4) - (0-1): loadout **-184.27** [-224.05,-144.97]; armed **-0.53**
[-0.61,-0.45]; full-buys **+0.32** [+0.25,+0.41]; win R3 **+3.45pp**
[+0.11,+6.69]. **The armed count falls but the full-buy count and the win rate
RISE.** Superseded on interpretation by `M27f`: the positive outcome is
concentrated entirely in teams that had *saved*, and is a pooling artifact.

**(b) Round 2, pistol winners that LOST round 2** -- n=1,069, **degenerate**:
1,009 of 1,069 have exactly 5 deaths, because losing round 2 early is usually
an elimination. No exposure variation, nothing measurable.

**(c) Round 3, the FULL-BUY team that won round 3** -- n=1,823 over 1,516
matches. Full-buy team = own round-3 mean loadout >= 4200:

| own deaths | own loadout R4 | own full-buys R4 | win R4 | n |
|---|---|---|---|---|
| 0 | 4653 | 4.49 | 59.0% | 183 |
| 1 | 4471 | 4.23 | 58.8% | 398 |
| 2 | 4314 | 3.80 | 54.5% | 483 |
| 3 | 4019 | 3.11 | 45.8% | 456 |
| 4 | 3634 | 2.19 | 48.3% | 269 |

Consecutive differences on full-buys R4 -- **every one excludes zero and the
cost per death accelerates**: -0.27 [-0.39,-0.14], -0.43 [-0.53,-0.33],
**-0.69** [-0.83,-0.56], **-0.92** [-1.11,-0.72].

Consecutive differences on win R4: only **2 -> 3 deaths** separates, at
**-8.62pp [-14.82,-2.02]**. It is the single consecutive outcome step that
excludes zero anywhere in `M27`.

(3-4) - (0-1): loadout **-651.87** [-704.14,-596.68]; full-buys **-1.54**
[-1.65,-1.43]; win R4 **-12.11pp** [-17.74,-6.29]. Within the enemy-mid
loadout stratum (n=1,464) it is **stronger**, -16.31pp [-22.40,-9.99], so it is
not "the enemy simply bought well".

**(d) Round 3, pistol LOSERS that won round 3** -- n=3,725 over 2,586 matches.
Same direction, every consecutive mediator difference excluding zero, headline
loadout -603.99 [-639.33,-569.93], full-buys -1.34 [-1.42,-1.26], win R4
**-6.53pp** [-10.50,-2.57]. **No individual consecutive win-rate step
separates** -- a steady drift, not the step (c) shows. Stratified: enemy poor
(n=592) loadout -142.45, win +5.79pp spanning zero; enemy mid (n=2,958)
loadout -653.72, win **-8.22pp** [-12.79,-3.66].

**The 5-death cell is below the 120-row floor in every block** (n=33/34/70), so
nothing here measures the all-five-dead case in either direction.

**Enemy-wealth spread, early against late** -- run because the wider design
assumed the `M10` wealth confound would be weaker early. It is not:

| rounds | n | mean | sd | p10 | p50 | p90 | p90-p10 |
|---|---|---|---|---|---|---|---|
| 2-4 / 14-16 | 36,640 | 3078 | **1312** | 750 | 3370 | 4520 | **3770** |
| 5-11 / 17-23 | 72,190 | 4099 | 839 | 2780 | 4400 | 4880 | 2100 |

Early rounds mix savers at 750 with full buys at 4520; by round 5 everyone is
compressed. **The wealth confound is more live early, not less**, which is why
the stratified rows above are the ones to read rather than the pooled ones.

**Does not establish** causation. Own deaths, round outcome and both teams'
buys are jointly determined within a round; site taken, plant status,
man-advantage trajectory and the team's own spending decision are uncontrolled.
Conditioning on the round-N outcome closes the ladder path only. A death here
is any death recorded against the player, self-kills included.

Script: `diagnostics/measure_early_round_carryover.py`

### M27e -- The round-2 denial is DEFERRED to round 4, and conditional

`M27`(a) looks at round 3 and finds the wrong sign. Measured one round later it
reverses: a round-2 death forces a round-3 rebuy that **drains the bank**, and
the bill arrives in round 4 -- but only for teams that then lost round 3 and so
lost the equipment they had just paid for.

**Sample:** 4,988 team-halves over 3,001 matches -- pistol winners that won
round 2, followed to round 4. `remaining` is the bank, verified in `M12d`.

| R2 deaths | bank entering R3 | loadout R4 | full-buys R4 | win R4 | n |
|---|---|---|---|---|---|
| 0 | 3104 | 4494 | 4.21 | 60.6% | 889 |
| 1 | 2915 | 4379 | 3.98 | 61.0% | 1,409 |
| 2 | 2645 | 4260 | 3.79 | 59.7% | 1,325 |
| 3 | 2288 | 4088 | 3.49 | 57.0% | 905 |
| 4 | 1846 | 3741 | 2.90 | 54.3% | 427 |

(3-4) - (0-1) overall: bank R3 **-841.91** [-880.96,-801.84]; loadout R4
-446.38; full-buys R4 -0.77 [-0.87,-0.68]; win R4 **-4.72pp** [-8.34,-1.38].

Split on the round-3 outcome:

| split | n | bank R3 drain | full-buys R4 | win R4 |
|---|---|---|---|---|
| then **LOST** R3 | 2,938 | -829.79 | **-1.24** [-1.36,-1.12] | **-7.42pp** [-12.02,-2.89] |
| then **WON** R3 | 2,050 | -854.11 | -0.17 [-0.26,-0.08] | -2.30pp [-6.97,+3.02] spans 0 |

**The bank drain is the same in both arms (-830 against -854) while the
consequence appears only in the losing arm.** That symmetry is what makes a
pure selection reading hard to sustain: if more-deaths teams were simply worse,
the drain would differ between the arms too.

**Does not establish** causation, and inherits every limitation of `M27`. Note
also that the exposure and the split are two rounds apart, so this conditions
on an event *after* the round being described.

Script: `diagnostics/measure_early_round_carryover.py`

### M27f -- Round 2 conditioned on BUY-IN: you can only deny what they bought

`M27`(a)'s positive outcome and `M28`'s +11.74pp at round 2 both dissolve once
the victim team's own commitment is conditioned on.

**Sample:** 6,046 team-rounds -- teams that **won** round 2/14 with rounds 3 and
4 available. Strata are terciles of that team's own round-2 mean loadout, with
cut points taken from the **exposure's** distribution alone and never from any
outcome: p10=870, **t1=2570**, p50=2860, **t2=3100**, p90=3460.

| stratum | mean R2 loadout | n | wealth denied R3 | win R3 | win R4 | full-buys R4 |
|---|---|---|---|---|---|---|
| SAVED <2570 | 1549 | 2,000 | -736.78 | **+19.70pp** [+15.06,+24.80] | -1.97pp spans 0 | -0.42 |
| LIGHT 2570-3100 | 2851 | 2,018 | -1005.71 | +0.37pp spans 0 | **-7.88pp** [-13.43,-2.38] | -0.87 |
| BOUGHT IN >=3100 | 3372 | 2,028 | **-1308.58** [-1362.22,-1255.73] | -0.48pp spans 0 | **-6.60pp** [-11.69,-1.43] | -0.82 |

Three results in one table. **The positive round-3 outcome is entirely the
savers** (+19.70pp, against +0.37 and -0.48 both spanning zero). **The wealth
denied scales monotonically with what they committed** (-737 / -1006 / -1309).
**The round-4 outcome is correctly signed and excludes zero in both bought-in
strata** and is null for savers.

The pistol outcome is a **worse instrument** than buy-in measured directly:

| proxy | mean R2 loadout | n | win R3 | win R4 | full-buys R4 |
|---|---|---|---|---|---|
| pistol WINNERS | 2947 | 4,988 | +2.87pp spans 0 | -4.72pp [-8.34,-1.38] | -0.77 |
| pistol LOSERS | 937 | 1,058 | +4.69pp spans 0 | -3.92pp spans 0 | -0.14 spans 0 |

Pistol winners average 2947 credits and straddle the light/bought-in boundary;
pistol losers average 937 and are savers. The pistol result is the buy-in
result seen through a noisy proxy.

**Does not establish** that commitment *causes* the difference. Buy-in is
chosen by the team and correlates with prior rounds, role and score state.

Script: `diagnostics/measure_early_round_carryover.py`

### M28 -- One readout, total wealth, across every round

Tests whether `loadout + remaining` in round N+1 carries the denial at every
round position, which would remove the need for a round-keyed regime split on
the mediator.

**Sample:** 52,111 team-rounds over 3,124 matches -- one row per round a team
**won**, rounds 2-11 and 14-23, halves pooled by position. Rounds 12/24 are
excluded as round N (the economy resets into the next half's pistol). Exposure
and identification as `M27`.

**Predeclared before running:** the unified readout replaces the split only if
the (3-4 deaths) - (0-1 deaths) difference on total wealth is negative and
excludes zero at **every** position.

| round | n | total wealth N+1 | loadout | bank | win N+1 |
|---|---|---|---|---|---|
| 2 | 6,116 | **-960** [-988,-929] | +32 spans 0 | -992 | **+11.74pp** [+8.92,+14.93] |
| 3 | 6,046 | **-1524** [-1594,-1449] | -330 | -1194 | -0.76pp spans 0 |
| 4 | 5,921 | **-2099** [-2184,-2022] | -275 | -1824 | -4.00pp [-7.12,-0.67] |
| 5 | 5,723 | **-2030** [-2118,-1931] | -267 | -1762 | -0.43pp spans 0 |
| 6 | 5,495 | **-2076** [-2180,-1973] | -246 | -1830 | -1.31pp spans 0 |
| 7 | 5,226 | **-2121** [-2225,-2010] | -209 | -1913 | -3.28pp [-6.67,-0.05] |
| 8 | 4,906 | **-2097** [-2202,-1981] | -228 | -1869 | -5.98pp [-9.36,-2.52] |
| 9 | 4,595 | **-2065** [-2199,-1934] | -204 | -1861 | -5.37pp [-9.07,-1.82] |
| 10 | 4,229 | **-2131** [-2255,-2003] | -196 | -1935 | -2.28pp spans 0 |
| 11 | 3,854 | **-2000** [-2129,-1878] | -182 | -1818 | -2.59pp spans 0 |

**Total wealth is negative and excludes zero at all 10 positions**, and is
monotone across the full 0-4 curve in every band (rounds 2-4:
7681/7340/6913/6378/5816; 5-7: 9731/8956/8180/7397/6706; 8-11:
9918/9070/8294/7542/6817). The readout passes its predeclared rule.

The magnitude is arithmetically sensible, which is a check on what the readout
measures: ~2 deaths of difference costing ~2,000 credits of total wealth is
~700-1000 per death, about a rifle plus armour.

**Where the denial lands -- the account shift is NOT what was predicted:**

| rounds | n | total wealth | loadout share | bank share |
|---|---|---|---|---|
| 2-4 | 18,083 | -1294 [-1337,-1248] | 3% | **97%** |
| 5-7 | 16,444 | -2067 [-2127,-2004] | 12% | **88%** |
| 8-11 | 17,584 | -2074 [-2139,-2008] | 10% | **90%** |

The design expected bank-dominated early and loadout-dominated late. It is
**bank-dominated throughout**: even at round 11 they pay in cash rather than in
guns. The loadout share rises from 3% to ~10-12% but never dominates.

**Two things this does not settle.** The round-2 outcome column is
**+11.74pp** -- the mediator's sign is fixed at every round, the outcome's is
not; `M27f` shows that column is a buy-in pooling artifact. And the outcome
separates at only 5 of 10 positions, so this is `M12d`'s situation again:
**the mediator is cleanly graded, the outcome is not.**

**Does not establish** causation; inherits `M27`'s limitations entirely.

Script: `diagnostics/measure_unified_wealth_denial.py`

---

## Data hygiene

### M15 -- The attacking-side convention, verified

Deriving the attacker independently from round outcomes (a Time Win or Defuse
Win implies that team defended; a Detonate Win implies it attacked) and
comparing against the 1-12 / 13-24 convention: **5,251 outcome-determinable
rounds, 100.00% agreement, 0 exceptions.**

Overtime alternates per round: `TEAM_1` on odd rounds past 24, `TEAM_2` on even.
**294 determinable OT rounds, 0 exceptions.** There are **1,274** OT rounds,
spread over **374 matches** (12.0% of the corpus, 1.93% of all rounds). All
1,274 clear every other `state_replay` exclusion — zero `unknown_winner`, zero
`unrecognized_round_result`, zero surrendered.

**Correction 2026-09-04.** This entry previously read "97 determinable OT
rounds" and "there are 448 OT rounds". Both were **unmarked `[SUBSET]`
figures**: 448 is exactly the OT-round count of the newest 1,151 matches (the
oldest 1,151 give 444). The alternation rule itself is unaffected and is now
confirmed on 3x the sample. Reproduction: inline query, see the register.

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
| "There are 448 OT rounds" / "97 determinable OT rounds" | `M15` re-run -- both unmarked `[SUBSET]` figures. 448 is exactly the newest-1,151-match count; full data has **1,274** over 374 matches, and 294 determinable. The alternation rule itself survives at 3x the sample |
| Preserving the net time contribution is "the one achievable invariant of the three" (time spec `:463-472`) | `M19`, `M20` -- the net gate carries zero weight on 71.7% of affected kills (17.7% of kill-side mass), and the option it rejected as unachievable, a documented aggregate shift, costs ~0.7% |
| Late post-plant kills are "more predictable, not more valuable", so payment should be flat-or-declining for BOTH sides | `M24` -- defender-kill leverage **rises** to +39pp by t=36 and `1v2` reverses sign against every other state. Only attacker kills decline, and only past +41.5s |
| The raw post-plant win-rate curve measures what a kill was worth | `M23` -- `V(before)` already prices in the duel, so a raw curve confounds who was winning with what the kill did. `M24` is the estimand for stakes |
| Destruction is worth ~2x more against a partial or full buy than against a broken team (`M12`, and the `w(state)` ~0.5/1.0/1.0 the econ spec proposed) | `M12b` -- adjusted, the ordering **inverts**: most valuable against broke teams, negative against full-buy teams, consistently in all three round groups |
| `M12a` settles whether destruction has an economic effect | `M12c` -- `M12a` holds enemies-killed fixed, which controls a **mediator** if the economic path runs through kill count. It answers the enemy-wealth question, not the kill-count one |
| "Summed credits destroyed" is the econ quantity | `M12d` -- the same four kills deny 0.60 armed enemies against a committed team and 0.09 against a cash-rich one. The quantity is **guns they could not replace**, not credits removed |
| Rounds 2-4 / 14-16 cannot be scored at all, because the early pattern is the acting team's own save decision (econ spec `§5a`) | `M27`, `M28` -- correct for round 2 and wrong as a blanket claim. At round 3 the denial is large, monotone, accelerating in deaths and survives the enemy-wealth stratification (full-buys R4 **-1.54**, win R4 **-12.11pp**). The blanket zero was a boundary drawn where no readout had been found, not where none exists |
| The econ component needs a regime split at round 5 / 17 | `M28` -- on the unified total-wealth readout the difference is negative and excludes zero at **all 10** round positions, monotone in every band. The split was an artifact of reading only the full-buy-count account |
| The denial shifts accounts -- bank-dominated early, **loadout**-dominated late | `M28` -- it is **bank**-dominated throughout (97% / 88% / 90%). The loadout share rises from 3% to ~10-12% and never dominates. `M27`(c)'s -652 loadout figure is specific to the full-buy subgroup and was over-generalised to all late rounds |
| Killing enemies in round 2 leaves them better off (`M27`(a) +3.45pp, `M28` +11.74pp on winning round 3) | `M27f` -- a **pooling artifact**. The positive outcome is entirely the saving stratum (+19.70pp); teams that bought in show +0.37pp and -0.48pp, both spanning zero, and lose round 4 by -7.88pp and -6.60pp. You can only deny what they bought |
| The pistol-round outcome identifies which teams have equipment to lose | `M27f` -- a noisy proxy for buy-in. Pistol winners average 2947 credits and straddle the light/bought-in boundary; measured directly, buy-in separates cleanly where the pistol proxy does not |
| `M10`'s enemy-wealth confound is weaker in the early rounds because economy is low for both teams | `M27` -- the spread is **wider** early, not narrower: sd 1312 against 839, p90-p10 3770 against 2100. Early rounds mix savers at 750 with full buys at 4520. The confound is more live there, which is why the stratified rows are the ones to read |
