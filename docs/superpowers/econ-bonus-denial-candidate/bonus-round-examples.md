# Bonus-round examples: current 30/80 vs the bonus-denial model

Candidate `impact-bonus-denial-rc1` (manifest LF-SHA-256 `2a5d247c…6798172`), weights A=1.25, B=1, C=1.
Econ component points per player for one round, as persisted under each comparator. `PW` = pistol-winning
team, `PL` = pistol-losing team. Players by match_player id only. Matches 3120 and 3116 are the declared
samples with a pistol winner losing a bonus round (3120 round 2, 3116 round 14).

## Match 3120, round 2 -- pistol winner LOST the round (factor 1.0)

| mp | side | K/D | loadout | 30/80 | bonus | change | note |
|---:|---|---|---:|---:|---:|---:|---|
| 31192 | PW | 0/1 | 3800 | -18 | -148 | -130 | denied 3250, net 3250 |
| 31193 | PW | 1/1 | 3300 | -15 | -118 | -103 | denied 2600, net 2600 |
| 31195 | PW | 0/1 | 3850 | -18 | -141 | -123 | denied 3100, net 3100 |
| 31196 | PW | 1/1 | 3300 | -13 | -108 | -95 | denied 2400, net 2400 |
| 31200 | PW | 1/1 | 2500 | -6 | -76 | -70 | denied 1800, net 1800 |
| 31191 | PL | 2/0 | 900 | 43 | 333 | +290 | |
| 31194 | PL | 0/1 | 100 | 0 | 0 | 0 | |
| 31197 | PL | 0/1 | 1100 | -2 | -2 | 0 | |
| 31198 | PL | 1/1 | 300 | 22 | 176 | +154 | |
| 31199 | PL | 2/0 | 700 | 33 | 239 | +206 | |

Team totals: pistol winner -70 -> **-591**; pistol loser 96 -> **746**.

## Match 3120, round 14 -- pistol winner WON the round (factor 0.8)

| mp | side | K/D | loadout | 30/80 | bonus | change | note |
|---:|---|---|---:|---:|---:|---:|---|
| 31197 | PW | 2/1 | 4100 | 2 | -104 | -106 | denied 3100, net 3100 |
| 31193 | PL | 1/1 | 700 | 20 | 140 | +120 | |
| others | | | | | | 0 | unchanged |

Team totals: pistol winner 8 -> **-98**; pistol loser 17 -> **137**.

## Match 3116, round 2 -- pistol winner WON the round (factor 0.8)

| mp | side | K/D | loadout | 30/80 | bonus | change | note |
|---:|---|---|---:|---:|---:|---:|---|
| 31158 | PW | 0/1 | 3300 | -5 | -95 | -90 | denied 2600, net 2600 |
| 31160 | PW | 0/1 | 2200 | -3 | -58 | -55 | denied 1600, net 1600 |
| 31153 | PL | 1/1 | 200 | 11 | 72 | +61 | |
| 31155 | PL | 1/1 | 500 | 16 | 117 | +101 | |
| others | | | | | | 0 | unchanged |

Team totals: pistol winner 2 -> **-143**; pistol loser 25 -> **187**.

## Match 3116, round 14 -- pistol winner LOST the round (factor 1.0)

| mp | side | K/D | loadout | 30/80 | bonus | change | note |
|---:|---|---|---:|---:|---:|---:|---|
| 31151 | PW | 2/1 | 3600 | -34 | -127 | -93 | denied 2900, net 2900 |
| 31152 | PW | 0/1 | 2850 | -31 | -102 | -71 | denied 2250, net 2250 |
| 31153 | PW | 2/1 | 3500 | -35 | -128 | -93 | denied 2900, net 2900 |
| 31155 | PW | 0/1 | 2750 | -30 | -102 | -72 | denied 2250, net 2250 |
| 31156 | PW | 0/1 | 3100 | -34 | -118 | -84 | denied 2600, net 2600 |
| 31158 | PL | 3/0 | 200 | 139 | 478 | +339 | |
| 31160 | PL | 2/1 | 500 | 76 | 255 | +179 | |
| others | | | | | | 0 | unchanged |

Team totals: pistol winner -164 -> **-577**; pistol loser 214 -> **732**.

No survivor in these four rounds showed recovery evidence, so every denial is netted at 100%. Deaths on the
pistol-losing team (e.g. 31197 and 31194 in 3120 round 2) score exactly as under 30/80.
