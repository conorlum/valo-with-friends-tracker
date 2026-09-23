# Per-kill trace: match 3120 Summit 7-13

Candidate `impact-v4`, manifest LF-SHA-256 `2f33f13754fc429ae1fe13a8b7da58ab245763e9cc812eae2ecebf79ab3f5cf7`.

- **Left: `live_legacy`** -- enable_econ_component=False, econ_model=None, weights A/B/C/D=1.25/1.0/1.0/0.0, trade credit OFF (scale 1.0), use_realized_swing=True, post-plant table OFF, pre-plant curve OFF
- **Right: `impact_v4`** -- enable_econ_component=True, econ_model=buy_disruption_v2_30_80_bonus_denial, weights A/B/C/D=1.0/2.5/2.5/100.0, trade credit ON (scale 1.0), use_realized_swing=True, post-plant table OFF, pre-plant curve OFF

impact = A*damage + B*leverage + C*econ + D*assists with A=1.0, B=2.5, C=2.5, D=100.0; econ points = C * 1007.9209 * raw, rounded ONCE per player-round. Trade credit is ON (scale 1.0): a traded player's share of the trade kill's leverage is inside B*leverage and shown separately as `trade credit`.
Kill credit = small equipment value + allocated buy-disruption value. Death debit = 30% of the victim's damage value when the team's funding absorbed the loss, 80% when its severity pool is positive (constrained next buy). Repeated deaths expose no new kit. Under the round 2/14 bonus-round denial, a pistol winner's qualifying death instead pays factor x 1.10 x net denied kit to the killer and 80% of that as the victim's debit; that team's 30/80 budget does not apply.

## Round 1

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487156 | 16.431s | Sub asf#nuhh -> DoubleBl1nd#BEEF | combat | 5v5 | 150 | 1.000 | 375.0 | 375.0 | | | | | no economy this round |
| event 487160 | 21.152s | kilo#1688 -> Deemo#Derf | combat | 5v4 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |
| event 487157 | 30.627s | Najumi#NPC -> SirBanArthur#King6 | combat | 3v5 | 120 | 1.000 | 300.0 | 300.0 | | | | | no economy this round |
| event 487158 | 30.931s | Jasmine#6767 -> NPrightdolphin#NA1 | combat | 4v3 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |
| event 487161 | 38.014s | kilo#1688 -> Najumi#NPC | combat | 4v2 | 80 | 1.000 | 200.0 | 20.0 | | | | | no economy this round |
| event 487155 | 39.046s | ternstyle#GIGI -> kilo#1688 | combat | 1v4 | 70 | 1.000 | 175.0 | 87.5 | | | | | no economy this round |
| event 487159 | 43.116s | wqe#4119 -> ternstyle#GIGI | combat | 3v1 | 70 | 1.000 | 175.0 | 175.0 | | | | | no economy this round |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +680 | 145 | +500 | +63 | 0.00 | 0.00 | +0 | +0 | +645 | +388 |
| Jasmine#6767 | +541 | 104 | +325 | +0 | 0.00 | 0.00 | +0 | +100 | +529 | +283 |
| Sub asf#nuhh | +475 | 100 | +375 | +0 | 0.00 | 0.00 | +0 | +0 | +475 | +300 |
| wqe#4119 | +477 | 155 | +175 | +0 | 0.00 | 0.00 | +0 | +100 | +430 | +276 |
| Najumi#NPC | +433 | 34 | +374 | +95 | 0.00 | 0.00 | +0 | +0 | +408 | +173 |
| ternstyle#GIGI | +87 | 100 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +100 | +129 |
| NPrightdolphin#NA1 | -100 | 137 | -325 | +0 | 0.00 | 0.00 | +0 | +100 | -88 | +18 |
| Deemo#Derf | -116 | 109 | -325 | +0 | 0.00 | 0.00 | +0 | +100 | -116 | -16 |
| SirBanArthur#King6 | -265 | 45 | -300 | +0 | 0.00 | 0.00 | +0 | +0 | -255 | -85 |
| DoubleBl1nd#BEEF | -323 | 52 | -375 | +0 | 0.00 | 0.00 | +0 | +0 | -323 | -110 |

## Round 2

Pistol winner TEAM_1; round winner TEAM_2; half round 2.

**TEAM_1** BONUS-ROUND DENIAL (pistol winner lost this round, factor 1.0): denied 13,150; recovered 0; net 13,150.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,800 | 3,800 | 800 | 800 | 1,700 | 3,800 |
| kilo#1688 | 3,300 | 3,300 | 700 | 700 | 2,100 | 3,300 |
| Jasmine#6767 | 3,700 | 3,700 | 1,150 | 1,000 | 1,950 | 3,700 |
| wqe#4119 | 3,050 | 3,050 | 2,100 | 1,850 | 1,950 | 3,050 |
| SirBanArthur#King6 | 2,500 | 2,500 | 1,900 | 1,900 | 2,300 | 2,500 |

**TEAM_2** lost L=1,500; target H=19,500; funding U=27,750; gap D=0; observed next-equipment gap G=250; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 800 | 0 | 5,100 | 5,000 | 2,850 | 3,900 |
| NPrightdolphin#NA1 | 100 | 100 | 4,000 | 4,000 | 0 | 3,900 |
| ternstyle#GIGI | 1,100 | 1,100 | 3,800 | 3,800 | 1,000 | 3,900 |
| Deemo#Derf | 300 | 300 | 4,250 | 4,250 | 550 | 3,900 |
| Najumi#NPC | 700 | 0 | 3,750 | 3,750 | 4,100 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487169 | 13.451s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 100 | 1.29 | 0.00 | 1.29 | 0.39 (30%, absorbed) |
| event 487165 | 26.527s | Deemo#Derf -> Jasmine#6767 | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 3,700 | 0.00 | 440.64 | 440.64 | 352.51 (80%, denial) |
| event 487163 | 32.869s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 3,300 | 0.00 | 369.57 | 369.57 | 295.66 (80%, denial) |
| event 487164 | 36.810s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v3 | 130 | 1.000 | 325.0 | 325.0 | 3,800 | 0.00 | 461.96 | 461.96 | 369.57 (80%, denial) |
| event 487162 | 43.682s | SirBanArthur#King6 -> ternstyle#GIGI | enemy | 2v4 | 130 | 1.000 | 325.0 | 243.8 | 1,100 | 14.21 | 0.00 | 14.21 | 4.26 (30%, absorbed) |
| event 487168 | 43.895s | wqe#4119 -> Deemo#Derf | enemy | 2v3 | 170 | 1.000 | 425.0 | 42.5 | 300 | 3.88 | 0.00 | 3.88 | 1.16 (30%, absorbed) |
| event 487166 | 45.410s | Najumi#NPC -> wqe#4119 | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 3,050 | 0.00 | 341.14 | 341.14 | 272.91 (80%, denial) |
| event 487167 | 48.910s | Najumi#NPC -> SirBanArthur#King6 | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 2,500 | 0.00 | 255.86 | 255.86 | 204.69 (80%, denial) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +2,115 | 195 | +825 | +0 | 597.00 | 0.00 | +597 | +100 | +1,717 | +819 |
| DoubleBl1nd#BEEF | +1,837 | 66 | +750 | +0 | 831.53 | 0.00 | +832 | +0 | +1,648 | +592 |
| Deemo#Derf | +1,514 | 257 | +578 | +270 | 440.64 | 1.16 | +439 | +100 | +1,374 | +533 |
| ternstyle#GIGI | +7 | 110 | -146 | +98 | 0.00 | 4.26 | -4 | +100 | +60 | +17 |
| SirBanArthur#King6 | -60 | 162 | +0 | +0 | 14.21 | 204.69 | -190 | +0 | -28 | +142 |
| NPrightdolphin#NA1 | -161 | 114 | -375 | +0 | 0.00 | 0.39 | +0 | +100 | -161 | -4 |
| kilo#1688 | -339 | 99 | -50 | +0 | 1.29 | 295.66 | -294 | +0 | -245 | -17 |
| wqe#4119 | -312 | 78 | -75 | +0 | 3.88 | 272.91 | -269 | +0 | -266 | -66 |
| Jasmine#6767 | -716 | 22 | -350 | +0 | 0.00 | 352.51 | -353 | +0 | -681 | -203 |
| Sub asf#nuhh | -791 | 0 | -325 | +0 | 0.00 | 369.57 | -370 | +0 | -695 | -223 |

## Round 3

Pistol winner TEAM_1; round winner TEAM_2; half round 3.

**TEAM_1** lost L=6,250; target H=19,500; funding U=21,250; gap D=0; observed next-equipment gap G=450; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 800 | 800 | 3,450 | 3,450 | 650 | 3,900 |
| kilo#1688 | 700 | 700 | 4,600 | 4,600 | 100 | 3,900 |
| Jasmine#6767 | 1,000 | 1,000 | 4,650 | 4,500 | 500 | 3,900 |
| wqe#4119 | 1,850 | 1,850 | 5,100 | 4,850 | 150 | 3,900 |
| SirBanArthur#King6 | 1,900 | 1,900 | 4,600 | 4,600 | 800 | 3,900 |

**TEAM_2** lost L=7,550; target H=19,500; funding U=32,450; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 0 | 5,100 | 5,000 | 2,850 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 0 | 4,100 | 4,100 | 1,900 | 3,900 |
| ternstyle#GIGI | 3,800 | 3,800 | 4,400 | 4,400 | 2,400 | 3,900 |
| Deemo#Derf | 4,250 | 0 | 4,600 | 4,600 | 2,250 | 3,900 |
| Najumi#NPC | 3,750 | 3,750 | 5,050 | 5,050 | 3,550 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487170 | 4.368s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 800 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 487175 | 14.647s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 3,800 | 49.10 | 0.00 | 49.10 | 14.73 (30%, absorbed) |
| event 487173 | 19.781s | NPrightdolphin#NA1 -> wqe#4119 | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 1,850 | 23.91 | 0.00 | 23.91 | 7.17 (30%, absorbed) |
| event 487176 | 37.938s | kilo#1688 -> Najumi#NPC | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 3,750 | 48.46 | 0.00 | 48.46 | 14.54 (30%, absorbed) |
| event 487174 | 81.166s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 1,900 | 24.55 | 0.00 | 24.55 | 7.37 (30%, absorbed) |
| event 487171 | 92.110s | Deemo#Derf -> Jasmine#6767 | enemy | 3v2 | 140 | 1.000 | 350.0 | 350.0 | 1,000 | 12.92 | 0.00 | 12.92 | 3.88 (30%, absorbed) |
| event 487172 | 94.273s | Deemo#Derf -> kilo#1688 | enemy | 3v1 | 70 | 1.000 | 175.0 | 175.0 | 700 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,554 | 431 | +875 | +0 | 48.46 | 0.00 | +48 | +200 | +1,554 | +870 |
| Deemo#Derf | +657 | 110 | +525 | +0 | 21.97 | 0.00 | +22 | +0 | +657 | +321 |
| kilo#1688 | +561 | 290 | +225 | +0 | 48.46 | 2.71 | +46 | +0 | +561 | +496 |
| ternstyle#GIGI | +121 | 100 | +25 | +0 | 10.34 | 14.73 | -4 | +0 | +121 | +107 |
| Jasmine#6767 | +110 | 65 | +0 | +0 | 49.10 | 3.88 | +45 | +0 | +110 | +106 |
| DoubleBl1nd#BEEF | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| Najumi#NPC | -211 | 104 | -400 | +0 | 0.00 | 14.54 | -15 | +100 | -211 | -62 |
| SirBanArthur#King6 | -257 | 100 | -450 | +0 | 0.00 | 7.37 | -7 | +100 | -257 | -45 |
| Sub asf#nuhh | -378 | 0 | -375 | +0 | 0.00 | 3.10 | -3 | +0 | -378 | -131 |
| wqe#4119 | -432 | 0 | -425 | +0 | 0.00 | 7.17 | -7 | +0 | -432 | -161 |

## Round 4

Pistol winner TEAM_1; round winner TEAM_1; half round 4.

**TEAM_1** lost L=4,600; target H=19,500; funding U=31,050; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,450 | 0 | 4,250 | 4,250 | 2,600 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,600 | 4,600 | 2,300 | 3,900 |
| Jasmine#6767 | 4,500 | 0 | 4,650 | 4,500 | 2,650 | 3,900 |
| wqe#4119 | 4,850 | 0 | 5,100 | 4,850 | 700 | 3,900 |
| SirBanArthur#King6 | 4,600 | 0 | 4,600 | 4,600 | 3,300 | 3,900 |

**TEAM_2** lost L=23,150; target H=19,500; funding U=22,550; gap D=0; observed next-equipment gap G=400; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 5,100 | 5,000 | 300 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 4,100 | 3,500 | 3,500 | 100 | 3,900 |
| ternstyle#GIGI | 4,400 | 4,400 | 4,200 | 4,200 | 700 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 4,600 | 4,600 | 250 | 3,900 |
| Najumi#NPC | 5,050 | 5,050 | 4,250 | 4,250 | 2,100 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487181 | 6.766s | kilo#1688 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487182 | 20.822s | kilo#1688 -> DoubleBl1nd#BEEF | enemy | 5v4 | 130 | 1.000 | 325.0 | 325.0 | 5,000 | 64.61 | 0.00 | 64.61 | 19.38 (30%, absorbed) |
| event 487179 | 27.554s | wqe#4119 -> ternstyle#GIGI | enemy | 5v3 | 90 | 1.000 | 225.0 | 225.0 | 4,400 | 56.86 | 0.00 | 56.86 | 17.06 (30%, absorbed) |
| event 487177 | 29.859s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 5v2 | 50 | 1.000 | 125.0 | 125.0 | 4,100 | 52.98 | 0.00 | 52.98 | 15.89 (30%, absorbed) |
| event 487178 | 39.683s | Najumi#NPC -> kilo#1688 | enemy | 1v5 | 60 | 1.000 | 150.0 | 15.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487180 | 41.672s | wqe#4119 -> Najumi#NPC | enemy | 4v1 | 50 | 1.000 | 125.0 | 125.0 | 5,050 | 65.26 | 0.00 | 65.26 | 19.58 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +1,177 | 300 | +752 | +68 | 124.05 | 17.83 | +106 | +0 | +1,158 | +648 |
| wqe#4119 | +804 | 275 | +350 | +0 | 122.11 | 0.00 | +122 | +0 | +747 | +492 |
| SirBanArthur#King6 | +342 | 150 | +125 | +0 | 52.98 | 0.00 | +53 | +0 | +328 | +237 |
| Najumi#NPC | +217 | 149 | +25 | +0 | 59.44 | 19.58 | +40 | +0 | +214 | +196 |
| NPrightdolphin#NA1 | -9 | 146 | -125 | +0 | 0.00 | 15.89 | -16 | +0 | +5 | +133 |
| Sub asf#nuhh | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| Jasmine#6767 | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| ternstyle#GIGI | -257 | 0 | -225 | +0 | 0.00 | 17.06 | -17 | +0 | -242 | -92 |
| DoubleBl1nd#BEEF | -264 | 80 | -325 | +0 | 0.00 | 19.38 | -19 | +0 | -264 | -30 |
| Deemo#Derf | -349 | 44 | -375 | +0 | 0.00 | 17.83 | -18 | +0 | -349 | -95 |

## Round 5

Pistol winner TEAM_1; round winner TEAM_2; half round 5.

**TEAM_1** lost L=22,800; target H=19,500; funding U=22,700; gap D=0; observed next-equipment gap G=200; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 4,250 | 4,250 | 4,250 | 4,250 | 900 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,600 | 4,600 | 100 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,650 | 4,500 | 550 | 3,900 |
| wqe#4119 | 4,850 | 4,850 | 3,950 | 3,700 | 50 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 4,600 | 4,600 | 1,800 | 3,900 |

**TEAM_2** lost L=16,550; target H=19,500; funding U=23,850; gap D=0; observed next-equipment gap G=2,500; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 0 | 5,100 | 5,000 | 250 | 3,900 |
| NPrightdolphin#NA1 | 3,500 | 3,500 | 3,300 | 3,300 | 0 | 3,900 |
| ternstyle#GIGI | 4,200 | 4,200 | 2,000 | 2,000 | 2,500 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 4,250 | 4,250 | 2,800 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,300 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487187 | 10.914s | Sub asf#nuhh -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,200 | 54.27 | 0.00 | 54.27 | 16.28 (30%, absorbed) |
| event 487188 | 21.671s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487186 | 23.398s | Deemo#Derf -> wqe#4119 | enemy | 4v4 | 170 | 1.000 | 425.0 | 148.7 | 4,850 | 62.67 | 0.00 | 62.67 | 18.80 (30%, absorbed) |
| event 487183 | 26.445s | SirBanArthur#King6 -> Deemo#Derf | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487189 | 44.581s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487191 | 56.221s | wqe#4119 -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487190 | 60.450s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 2v3 | 170 | 1.000 | 425.0 | 318.8 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |
| event 487192 | 65.616s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 3,500 | 45.23 | 0.00 | 45.23 | 13.57 (30%, absorbed) |
| event 487184 | 66.964s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v2 | 190 | 1.000 | 475.0 | 475.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487185 | 77.672s | DoubleBl1nd#BEEF -> wqe#4119 | enemy | 1v1 | 250 | 1.000 | 625.0 | 625.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,288 | 342 | +725 | +0 | 172.51 | 13.57 | +159 | +100 | +1,326 | +767 |
| DoubleBl1nd#BEEF | +1,563 | 166 | +1,100 | +0 | 59.44 | 0.00 | +59 | +0 | +1,325 | +680 |
| wqe#4119 | +502 | 228 | +344 | +168 | 100.15 | 18.80 | +81 | +0 | +653 | +322 |
| Deemo#Derf | +255 | 185 | +25 | +0 | 62.67 | 17.83 | +45 | +0 | +255 | +241 |
| Sub asf#nuhh | +212 | 149 | +25 | +0 | 54.27 | 16.48 | +38 | +0 | +212 | +184 |
| SirBanArthur#King6 | +163 | 171 | -50 | +0 | 59.44 | 17.83 | +42 | +0 | +163 | +179 |
| Najumi#NPC | +2 | 268 | -450 | +0 | 0.00 | 16.48 | -16 | +200 | +2 | +155 |
| Jasmine#6767 | -174 | 0 | -169 | +150 | 0.00 | 17.44 | -17 | +0 | -186 | -138 |
| kilo#1688 | -291 | 50 | -475 | +0 | 0.00 | 17.83 | -18 | +200 | -243 | -134 |
| ternstyle#GIGI | -391 | 0 | -375 | +0 | 0.00 | 16.28 | -16 | +0 | -391 | -150 |

## Round 6

Pistol winner TEAM_1; round winner TEAM_2; half round 6.

**TEAM_1** lost L=21,650; target H=19,500; funding U=18,900; gap D=600; observed next-equipment gap G=4,100; activation 0.1538; severity pool 630.77 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 4,250 | 4,250 | 3,500 | 3,500 | 500 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 1,900 | 1,900 | 1,300 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 3,800 | 3,650 | 50 | 3,900 |
| wqe#4119 | 3,700 | 3,700 | 2,700 | 2,450 | 450 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 4,600 | 4,600 | 1,200 | 3,900 |

**TEAM_2** lost L=16,800; target H=19,500; funding U=24,700; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 5,100 | 5,000 | 1,350 | 3,900 |
| NPrightdolphin#NA1 | 3,300 | 3,300 | 4,100 | 4,100 | 1,400 | 3,900 |
| ternstyle#GIGI | 2,000 | 0 | 4,700 | 4,700 | 300 | 3,900 |
| Deemo#Derf | 4,250 | 4,250 | 4,600 | 4,600 | 1,400 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487196 | 12.919s | ternstyle#GIGI -> Jasmine#6767 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,500 | 58.15 | 16.94 | 75.09 | 60.07 (80%, constrained) |
| event 487201 | 16.677s | Sub asf#nuhh -> Najumi#NPC | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487199 | 85.296s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 4,250 | 54.92 | 16.00 | 70.92 | 56.74 (80%, constrained) |
| event 487200 | 100.665s | Deemo#Derf -> wqe#4119 | enemy | 4v3 | 130 | 1.000 | 325.0 | 113.8 | 3,700 | 47.81 | 13.93 | 61.74 | 49.39 (80%, constrained) |
| event 487193 | 100.796s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 2v4 | 130 | 1.000 | 325.0 | 325.0 | 3,300 | 42.64 | 0.00 | 42.64 | 12.79 (30%, absorbed) |
| event 487197 | 103.193s | ternstyle#GIGI -> kilo#1688 | enemy | 3v2 | 140 | 1.000 | 350.0 | 350.0 | 4,600 | 59.44 | 17.32 | 76.76 | 61.41 (80%, constrained) |
| event 487194 | 104.416s | SirBanArthur#King6 -> Deemo#Derf | enemy | 1v3 | 120 | 1.000 | 300.0 | 300.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487195 | 107.363s | SirBanArthur#King6 -> DoubleBl1nd#BEEF | enemy | 1v2 | 190 | 1.000 | 475.0 | 475.0 | 5,000 | 64.61 | 0.00 | 64.61 | 19.38 (30%, absorbed) |
| event 487198 | 119.258s | ternstyle#GIGI -> SirBanArthur#King6 | enemy | 1v1 | 250 | 1.000 | 625.0 | 625.0 | 4,600 | 59.44 | 17.32 | 76.76 | 61.41 (80%, constrained) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +2,241 | 360 | +1,350 | +0 | 228.61 | 0.00 | +229 | +0 | +1,939 | +1,311 |
| SirBanArthur#King6 | +807 | 336 | +475 | +0 | 162.17 | 61.41 | +101 | +0 | +912 | +457 |
| DoubleBl1nd#BEEF | +359 | 249 | -50 | +0 | 70.92 | 19.38 | +52 | +200 | +451 | +339 |
| Deemo#Derf | +309 | 159 | +25 | +0 | 61.74 | 16.48 | +45 | +100 | +329 | +243 |
| Sub asf#nuhh | +217 | 194 | -75 | +0 | 54.92 | 56.74 | -2 | +100 | +217 | +152 |
| kilo#1688 | -60 | 92 | -350 | +0 | 0.00 | 61.41 | -61 | +300 | -19 | -103 |
| wqe#4119 | -27 | 0 | +12 | +126 | 0.00 | 49.39 | -49 | +0 | -37 | -60 |
| NPrightdolphin#NA1 | -191 | 70 | -325 | +0 | 0.00 | 12.79 | -13 | +100 | -168 | -36 |
| Najumi#NPC | -331 | 35 | -350 | +0 | 0.00 | 16.48 | -16 | +0 | -331 | -96 |
| Jasmine#6767 | -435 | 0 | -375 | +0 | 0.00 | 60.07 | -60 | +0 | -435 | -228 |

## Round 7

Pistol winner TEAM_1; round winner TEAM_1; half round 7.

**TEAM_1** lost L=9,050; target H=19,500; funding U=27,450; gap D=0; observed next-equipment gap G=400; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,500 | 3,500 | 3,500 | 3,500 | 500 | 3,900 |
| kilo#1688 | 1,900 | 1,900 | 4,600 | 4,600 | 400 | 3,900 |
| Jasmine#6767 | 3,650 | 3,650 | 4,650 | 4,500 | 2,200 | 3,900 |
| wqe#4119 | 2,450 | 0 | 5,600 | 5,350 | 3,650 | 3,900 |
| SirBanArthur#King6 | 4,600 | 0 | 4,600 | 4,600 | 1,600 | 3,900 |

**TEAM_2** lost L=22,650; target H=19,500; funding U=16,450; gap D=3,050; observed next-equipment gap G=12,000; activation 0.7821; severity pool 9,384.62 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 1,700 | 1,600 | 2,050 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 4,100 | 1,300 | 1,300 | 1,700 | 3,900 |
| ternstyle#GIGI | 4,700 | 4,700 | 2,350 | 2,350 | 650 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 1,550 | 1,550 | 2,150 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 700 | 700 | 2,400 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487205 | 23.364s | Jasmine#6767 -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,700 | 60.73 | 251.64 | 312.37 | 249.90 (80%, constrained) |
| event 487203 | 31.740s | Deemo#Derf -> Sub asf#nuhh | enemy | 4v5 | 140 | 1.000 | 350.0 | 122.5 | 3,500 | 45.23 | 0.00 | 45.23 | 13.57 (30%, absorbed) |
| event 487204 | 33.445s | Deemo#Derf -> Jasmine#6767 | enemy | 4v4 | 170 | 1.000 | 425.0 | 42.5 | 3,650 | 47.17 | 0.00 | 47.17 | 14.15 (30%, absorbed) |
| event 487207 | 35.192s | wqe#4119 -> Deemo#Derf | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 4,600 | 59.44 | 246.28 | 305.73 | 244.58 (80%, constrained) |
| event 487206 | 46.677s | Jasmine#6767 -> Jasmine#6767 | self | 4v4 | 160 | 1.000 | 0.0 | 400.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487209 | 71.769s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 4,100 | 52.98 | 219.51 | 272.50 | 218.00 (80%, constrained) |
| event 487210 | 79.241s | kilo#1688 -> Najumi#NPC | enemy | 3v2 | 140 | 1.000 | 350.0 | 350.0 | 4,250 | 54.92 | 227.55 | 282.46 | 225.97 (80%, constrained) |
| event 487202 | 85.821s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v3 | 120 | 1.000 | 300.0 | 15.0 | 1,900 | 24.55 | 0.00 | 24.55 | 7.37 (30%, absorbed) |
| event 487208 | 86.712s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 5,000 | 64.61 | 267.70 | 332.31 | 265.85 (80%, constrained) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +2,152 | 275 | +980 | +195 | 554.96 | 7.37 | +548 | +0 | +1,803 | +851 |
| wqe#4119 | +1,723 | 186 | +725 | +0 | 638.04 | 0.00 | +638 | +0 | +1,549 | +706 |
| Jasmine#6767 | +715 | 263 | +54 | +122 | 312.37 | 14.15 | +298 | +100 | +715 | +320 |
| Deemo#Derf | +458 | 235 | +375 | +0 | 92.39 | 244.58 | -152 | +0 | +458 | +334 |
| SirBanArthur#King6 | +125 | 25 | +0 | +0 | 0.00 | 0.00 | +0 | +100 | +125 | +31 |
| Sub asf#nuhh | -42 | 0 | -28 | +95 | 0.00 | 13.57 | -14 | +0 | -42 | -46 |
| DoubleBl1nd#BEEF | -159 | 125 | -25 | +0 | 24.55 | 265.85 | -241 | +0 | -141 | +58 |
| Najumi#NPC | -714 | 0 | -350 | +0 | 0.00 | 225.97 | -226 | +0 | -576 | -236 |
| ternstyle#GIGI | -585 | 40 | -375 | +0 | 0.00 | 249.90 | -250 | +0 | -585 | -170 |
| NPrightdolphin#NA1 | -782 | 0 | -450 | +0 | 0.00 | 218.00 | -218 | +0 | -668 | -277 |

## Round 8

Pistol winner TEAM_1; round winner TEAM_1; half round 8.

**TEAM_1** lost L=5,350; target H=19,500; funding U=36,250; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,500 | 0 | 4,450 | 4,450 | 2,650 | 3,900 |
| kilo#1688 | 4,600 | 0 | 4,600 | 4,600 | 2,400 | 3,900 |
| Jasmine#6767 | 4,500 | 0 | 4,650 | 4,500 | 4,550 | 3,900 |
| wqe#4119 | 5,350 | 5,350 | 5,100 | 4,850 | 2,250 | 3,900 |
| SirBanArthur#King6 | 4,600 | 0 | 5,400 | 5,400 | 4,900 | 3,900 |

**TEAM_2** lost L=7,500; target H=19,500; funding U=21,000; gap D=0; observed next-equipment gap G=450; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 1,600 | 1,600 | 5,100 | 5,000 | 150 | 3,900 |
| NPrightdolphin#NA1 | 1,300 | 1,300 | 3,750 | 3,750 | 150 | 3,900 |
| ternstyle#GIGI | 2,350 | 2,350 | 3,600 | 3,600 | 250 | 3,900 |
| Deemo#Derf | 1,550 | 1,550 | 4,600 | 4,600 | 150 | 3,900 |
| Najumi#NPC | 700 | 700 | 4,250 | 4,250 | 1,250 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487213 | 12.745s | Jasmine#6767 -> DoubleBl1nd#BEEF | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 1,600 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |
| event 487211 | 17.593s | Deemo#Derf -> wqe#4119 | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 5,350 | 69.13 | 0.00 | 69.13 | 20.74 (30%, absorbed) |
| event 487215 | 24.955s | kilo#1688 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 1,550 | 20.03 | 0.00 | 20.03 | 6.01 (30%, absorbed) |
| event 487212 | 31.636s | Sub asf#nuhh -> Najumi#NPC | enemy | 4v3 | 130 | 1.000 | 325.0 | 325.0 | 700 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |
| event 487214 | 34.888s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v2 | 80 | 1.000 | 200.0 | 200.0 | 2,350 | 30.37 | 0.00 | 30.37 | 9.11 (30%, absorbed) |
| event 487216 | 36.261s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 4v1 | 50 | 1.000 | 125.0 | 125.0 | 1,300 | 16.80 | 0.00 | 16.80 | 5.04 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +920 | 185 | +550 | +0 | 36.83 | 0.00 | +37 | +100 | +872 | +433 |
| Jasmine#6767 | +896 | 225 | +575 | +0 | 51.04 | 0.00 | +51 | +0 | +851 | +491 |
| Sub asf#nuhh | +488 | 100 | +325 | +0 | 9.05 | 0.00 | +9 | +0 | +434 | +246 |
| Deemo#Derf | +311 | 340 | -75 | +0 | 69.13 | 6.01 | +63 | +0 | +328 | +435 |
| SirBanArthur#King6 | +165 | 65 | +0 | +0 | 0.00 | 0.00 | +0 | +100 | +165 | +81 |
| NPrightdolphin#NA1 | -86 | 76 | -125 | +0 | 0.00 | 5.04 | -5 | +0 | -54 | +46 |
| ternstyle#GIGI | -254 | 0 | -200 | +0 | 0.00 | 9.11 | -9 | +0 | -209 | -77 |
| Najumi#NPC | -331 | 51 | -325 | +0 | 0.00 | 2.71 | -3 | +0 | -277 | -57 |
| DoubleBl1nd#BEEF | -326 | 55 | -375 | +0 | 0.00 | 6.20 | -6 | +0 | -326 | -64 |
| wqe#4119 | -371 | 0 | -350 | +0 | 0.00 | 20.74 | -21 | +0 | -371 | -163 |

## Round 9

Pistol winner TEAM_1; round winner TEAM_2; half round 9.

**TEAM_1** lost L=23,800; target H=19,500; funding U=27,850; gap D=0; observed next-equipment gap G=2,350; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 4,450 | 4,450 | 1,550 | 1,550 | 3,850 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,600 | 4,600 | 700 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,650 | 4,500 | 2,900 | 3,900 |
| wqe#4119 | 4,850 | 4,850 | 4,800 | 4,550 | 50 | 3,900 |
| SirBanArthur#King6 | 5,400 | 5,400 | 4,600 | 4,600 | 3,200 | 3,900 |

**TEAM_2** lost L=11,950; target H=19,500; funding U=25,450; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 0 | 5,100 | 5,000 | 100 | 3,900 |
| NPrightdolphin#NA1 | 3,750 | 3,750 | 4,000 | 4,000 | 2,050 | 3,900 |
| ternstyle#GIGI | 3,600 | 3,600 | 4,050 | 4,050 | 0 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 4,600 | 4,600 | 2,050 | 3,900 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 1,750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487218 | 17.832s | Deemo#Derf -> wqe#4119 | enemy | 5v5 | 150 | 1.000 | 375.0 | 18.8 | 4,850 | 62.67 | 0.00 | 62.67 | 18.80 (30%, absorbed) |
| event 487223 | 17.908s | Jasmine#6767 -> Deemo#Derf | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487219 | 18.543s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 5,400 | 69.78 | 0.00 | 69.78 | 20.93 (30%, absorbed) |
| event 487224 | 25.966s | Jasmine#6767 -> ternstyle#GIGI | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 3,600 | 46.52 | 0.00 | 46.52 | 13.96 (30%, absorbed) |
| event 487220 | 26.839s | NPrightdolphin#NA1 -> kilo#1688 | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487225 | 42.861s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 3,750 | 48.46 | 0.00 | 48.46 | 14.54 (30%, absorbed) |
| event 487221 | 43.654s | Najumi#NPC -> Sub asf#nuhh | enemy | 2v3 | 170 | 1.000 | 425.0 | 425.0 | 4,450 | 57.50 | 0.00 | 57.50 | 17.25 (30%, absorbed) |
| event 487222 | 51.771s | Najumi#NPC -> wqe#4119 | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487217 | 56.216s | DoubleBl1nd#BEEF -> Jasmine#6767 | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,111 | 96 | +925 | +0 | 57.50 | 0.00 | +58 | +0 | +1,079 | +445 |
| NPrightdolphin#NA1 | +954 | 314 | +425 | +0 | 129.22 | 14.54 | +115 | +100 | +954 | +556 |
| Jasmine#6767 | +621 | 155 | +425 | +0 | 105.96 | 17.44 | +89 | +0 | +669 | +364 |
| Deemo#Derf | +545 | 275 | +25 | +0 | 62.67 | 17.83 | +45 | +200 | +545 | +334 |
| DoubleBl1nd#BEEF | +436 | 5 | +325 | +0 | 58.15 | 0.00 | +58 | +0 | +388 | +125 |
| wqe#4119 | +304 | 165 | +141 | +210 | 48.46 | 18.80 | +30 | +0 | +336 | +190 |
| ternstyle#GIGI | +66 | 280 | -400 | +0 | 0.00 | 13.96 | -14 | +200 | +66 | +201 |
| SirBanArthur#King6 | -266 | 80 | -425 | +0 | 0.00 | 20.93 | -21 | +100 | -266 | -62 |
| kilo#1688 | -343 | 25 | -450 | +0 | 0.00 | 17.83 | -18 | +100 | -343 | -140 |
| Sub asf#nuhh | -442 | 0 | -425 | +0 | 0.00 | 17.25 | -17 | +0 | -442 | -147 |

## Round 10

Pistol winner TEAM_1; round winner TEAM_1; half round 10.

**TEAM_1** lost L=10,600; target H=19,500; funding U=34,450; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 1,550 | 1,550 | 4,450 | 4,450 | 3,300 | 3,900 |
| kilo#1688 | 4,600 | 0 | 4,600 | 4,600 | 2,500 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,650 | 4,500 | 2,350 | 3,900 |
| wqe#4119 | 4,550 | 4,550 | 4,150 | 3,900 | 2,350 | 3,900 |
| SirBanArthur#King6 | 4,600 | 0 | 4,600 | 4,600 | 4,450 | 3,900 |

**TEAM_2** lost L=21,900; target H=19,500; funding U=18,600; gap D=900; observed next-equipment gap G=10,400; activation 0.2308; severity pool 2,400.00 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 1,500 | 1,400 | 1,750 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 4,000 | 1,950 | 1,950 | 2,000 | 3,900 |
| ternstyle#GIGI | 4,050 | 4,050 | 1,650 | 1,650 | 1,450 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 2,150 | 2,150 | 1,900 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 1,950 | 1,950 | 2,400 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487227 | 3.465s | ternstyle#GIGI -> wqe#4119 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,550 | 58.80 | 0.00 | 58.80 | 17.64 (30%, absorbed) |
| event 487231 | 10.250s | Jasmine#6767 -> Deemo#Derf | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,600 | 59.44 | 65.14 | 124.58 | 99.67 (80%, constrained) |
| event 487228 | 12.190s | Sub asf#nuhh -> ternstyle#GIGI | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 4,050 | 52.33 | 57.35 | 109.69 | 87.75 (80%, constrained) |
| event 487232 | 15.065s | Jasmine#6767 -> Najumi#NPC | enemy | 4v3 | 130 | 1.000 | 325.0 | 325.0 | 4,250 | 54.92 | 60.18 | 115.10 | 92.08 (80%, constrained) |
| event 487229 | 28.026s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 2v4 | 130 | 1.000 | 325.0 | 325.0 | 1,550 | 20.03 | 0.00 | 20.03 | 6.01 (30%, absorbed) |
| event 487230 | 40.031s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 2v3 | 170 | 1.000 | 425.0 | 425.0 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |
| event 487233 | 52.926s | Jasmine#6767 -> Jasmine#6767 | self | 3v3 | 170 | 1.000 | 0.0 | 425.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487226 | 61.667s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 4,000 | 51.69 | 56.64 | 108.33 | 86.67 (80%, constrained) |
| event 487234 | 65.224s | kilo#1688 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 5,000 | 64.61 | 70.81 | 135.42 | 108.33 (80%, constrained) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +1,065 | 291 | +325 | +0 | 135.42 | 0.00 | +135 | +200 | +951 | +544 |
| SirBanArthur#King6 | +837 | 88 | +500 | +0 | 108.33 | 0.00 | +108 | +0 | +696 | +369 |
| NPrightdolphin#NA1 | +390 | 289 | +250 | +0 | 78.18 | 86.67 | -8 | +0 | +531 | +409 |
| Sub asf#nuhh | +314 | 110 | +100 | +0 | 109.69 | 6.01 | +104 | +0 | +314 | +242 |
| Jasmine#6767 | +242 | 245 | -175 | +0 | 239.69 | 17.44 | +222 | +0 | +292 | +247 |
| ternstyle#GIGI | +71 | 150 | -50 | +0 | 58.80 | 87.75 | -29 | +0 | +71 | +123 |
| Deemo#Derf | -243 | 107 | -350 | +0 | 0.00 | 99.67 | -100 | +100 | -243 | -43 |
| Najumi#NPC | -347 | 70 | -325 | +0 | 0.00 | 92.08 | -92 | +0 | -347 | -77 |
| wqe#4119 | -353 | 40 | -375 | +0 | 0.00 | 17.64 | -18 | +0 | -353 | -112 |
| DoubleBl1nd#BEEF | -512 | 35 | -325 | +0 | 0.00 | 108.33 | -108 | +0 | -398 | -136 |

## Round 11

Pistol winner TEAM_1; round winner TEAM_2; half round 11.

**TEAM_1** lost L=22,050; target H=19,500; funding U=23,050; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 4,450 | 4,450 | 4,750 | 4,750 | 800 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,900 | 4,900 | 100 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,650 | 4,500 | 200 | 3,900 |
| wqe#4119 | 3,900 | 3,900 | 4,850 | 4,600 | 100 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 4,600 | 4,600 | 2,350 | 3,900 |

**TEAM_2** lost L=4,100; target H=19,500; funding U=29,250; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 1,400 | 0 | 5,400 | 5,300 | 2,950 | 3,900 |
| NPrightdolphin#NA1 | 1,950 | 0 | 4,100 | 4,100 | 700 | 3,900 |
| ternstyle#GIGI | 1,650 | 0 | 4,700 | 4,700 | 3,550 | 3,900 |
| Deemo#Derf | 2,150 | 2,150 | 4,600 | 4,600 | 500 | 3,900 |
| Najumi#NPC | 1,950 | 1,950 | 4,250 | 4,250 | 2,050 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487236 | 13.077s | ternstyle#GIGI -> Jasmine#6767 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |
| event 487240 | 29.878s | Najumi#NPC -> wqe#4119 | enemy | 5v4 | 130 | 1.000 | 325.0 | 16.2 | 3,900 | 50.40 | 0.00 | 50.40 | 15.12 (30%, absorbed) |
| event 487235 | 30.036s | SirBanArthur#King6 -> Najumi#NPC | enemy | 3v5 | 120 | 1.000 | 300.0 | 300.0 | 1,950 | 25.20 | 0.00 | 25.20 | 7.56 (30%, absorbed) |
| event 487238 | 40.431s | Deemo#Derf -> SirBanArthur#King6 | enemy | 4v3 | 130 | 1.000 | 325.0 | 325.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487241 | 105.211s | kilo#1688 -> Deemo#Derf | enemy | 2v4 | 130 | 0.000 | 0.0 | 0.0 | 2,150 | 27.78 | 0.00 | 27.78 | 8.33 (30%, absorbed) |
| event 487237 | 106.497s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 3v2 | 140 | 0.000 | 0.0 | 0.0 | 4,450 | 57.50 | 0.00 | 57.50 | 17.25 (30%, absorbed) |
| event 487239 | 106.824s | NPrightdolphin#NA1 -> kilo#1688 | enemy | 3v1 | 70 | 0.000 | 0.0 | 0.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +1,136 | 295 | +375 | +0 | 115.65 | 0.00 | +116 | +0 | +786 | +659 |
| Deemo#Derf | +615 | 77 | +325 | +0 | 59.44 | 8.33 | +51 | +0 | +453 | +216 |
| wqe#4119 | +314 | 65 | +164 | +180 | 0.00 | 15.12 | -15 | +100 | +314 | +75 |
| kilo#1688 | +433 | 273 | +0 | +0 | 27.78 | 17.83 | +10 | +0 | +283 | +374 |
| DoubleBl1nd#BEEF | +228 | 128 | +0 | +0 | 0.00 | 0.00 | +0 | +100 | +228 | +160 |
| Najumi#NPC | +217 | 149 | +25 | +0 | 50.40 | 7.56 | +43 | +0 | +217 | +208 |
| NPrightdolphin#NA1 | +351 | 117 | +0 | +0 | 59.44 | 0.00 | +59 | +0 | +176 | +216 |
| SirBanArthur#King6 | +42 | 60 | -25 | +0 | 25.20 | 17.83 | +7 | +0 | +42 | +40 |
| Sub asf#nuhh | -367 | 0 | +0 | +0 | 0.00 | 17.25 | -17 | +0 | -17 | -140 |
| Jasmine#6767 | -392 | 0 | -375 | +0 | 0.00 | 17.44 | -17 | +0 | -392 | -150 |

## Round 12

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487243 | 10.889s | Deemo#Derf -> Sub asf#nuhh | combat | 5v5 | 150 | 1.000 | 375.0 | 18.8 | | | | | no economy this round |
| event 487247 | 11.213s | Jasmine#6767 -> Deemo#Derf | combat | 4v5 | 140 | 1.000 | 350.0 | 350.0 | | | | | no economy this round |
| event 487248 | 14.320s | Jasmine#6767 -> DoubleBl1nd#BEEF | combat | 4v4 | 170 | 1.000 | 425.0 | 425.0 | | | | | no economy this round |
| event 487244 | 27.633s | Najumi#NPC -> SirBanArthur#King6 | combat | 3v4 | 160 | 1.000 | 400.0 | 400.0 | | | | | no economy this round |
| event 487245 | 28.382s | Najumi#NPC -> kilo#1688 | combat | 3v3 | 180 | 1.000 | 450.0 | 450.0 | | | | | no economy this round |
| event 487246 | 37.674s | Najumi#NPC -> Jasmine#6767 | combat | 3v2 | 140 | 1.000 | 350.0 | 350.0 | | | | | no economy this round |
| event 487242 | 41.400s | ternstyle#GIGI -> wqe#4119 | combat | 3v1 | 70 | 1.000 | 175.0 | 175.0 | | | | | no economy this round |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,727 | 420 | +1,200 | +0 | 0.00 | 0.00 | +0 | +100 | +1,720 | +1,006 |
| Jasmine#6767 | +678 | 260 | +425 | +0 | 0.00 | 0.00 | +0 | +0 | +685 | +494 |
| Deemo#Derf | +484 | 259 | +25 | +0 | 0.00 | 0.00 | +0 | +200 | +484 | +334 |
| Sub asf#nuhh | +371 | 80 | +191 | +210 | 0.00 | 0.00 | +0 | +100 | +371 | +92 |
| ternstyle#GIGI | +297 | 106 | +175 | +0 | 0.00 | 0.00 | +0 | +0 | +281 | +204 |
| NPrightdolphin#NA1 | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| SirBanArthur#King6 | -150 | 50 | -400 | +0 | 0.00 | 0.00 | +0 | +200 | -150 | -98 |
| wqe#4119 | -191 | 0 | -175 | +0 | 0.00 | 0.00 | +0 | +0 | -175 | -72 |
| DoubleBl1nd#BEEF | -415 | 10 | -425 | +0 | 0.00 | 0.00 | +0 | +0 | -415 | -158 |
| kilo#1688 | -450 | 0 | -450 | +0 | 0.00 | 0.00 | +0 | +0 | -450 | -180 |

## Round 13

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487249 | 11.317s | ternstyle#GIGI -> Jasmine#6767 | combat | 5v5 | 150 | 1.000 | 375.0 | 375.0 | | | | | no economy this round |
| event 487250 | 24.101s | ternstyle#GIGI -> SirBanArthur#King6 | combat | 5v4 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |
| event 487252 | 29.728s | NPrightdolphin#NA1 -> Sub asf#nuhh | combat | 5v3 | 90 | 1.000 | 225.0 | 225.0 | | | | | no economy this round |
| event 487256 | 51.577s | kilo#1688 -> NPrightdolphin#NA1 | combat | 2v5 | 70 | 1.000 | 175.0 | 175.0 | | | | | no economy this round |
| event 487254 | 52.908s | wqe#4119 -> DoubleBl1nd#BEEF | combat | 2v4 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |
| event 487253 | 65.109s | Najumi#NPC -> kilo#1688 | combat | 3v2 | 140 | 1.000 | 350.0 | 35.0 | | | | | no economy this round |
| event 487255 | 67.046s | wqe#4119 -> Najumi#NPC | combat | 1v3 | 120 | 1.000 | 300.0 | 15.0 | | | | | no economy this round |
| event 487251 | 67.247s | ternstyle#GIGI -> wqe#4119 | combat | 2v1 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +1,508 | 262 | +1,025 | +0 | 0.00 | 0.00 | +0 | +100 | +1,387 | +837 |
| Najumi#NPC | +948 | 135 | +530 | +195 | 0.00 | 0.00 | +0 | +100 | +765 | +341 |
| wqe#4119 | +573 | 250 | +300 | +0 | 0.00 | 0.00 | +0 | +0 | +550 | +420 |
| kilo#1688 | +494 | 130 | +302 | +162 | 0.00 | 0.00 | +0 | +0 | +432 | +227 |
| Deemo#Derf | +235 | 135 | +0 | +0 | 0.00 | 0.00 | +0 | +100 | +235 | +169 |
| NPrightdolphin#NA1 | +42 | 5 | +50 | +0 | 0.00 | 0.00 | +0 | +0 | +55 | +28 |
| Sub asf#nuhh | -225 | 0 | -225 | +0 | 0.00 | 0.00 | +0 | +0 | -225 | -105 |
| SirBanArthur#King6 | -300 | 25 | -325 | +0 | 0.00 | 0.00 | +0 | +0 | -300 | -121 |
| DoubleBl1nd#BEEF | -358 | 0 | -325 | +0 | 0.00 | 0.00 | +0 | +0 | -325 | -145 |
| Jasmine#6767 | -375 | 0 | -375 | +0 | 0.00 | 0.00 | +0 | +0 | -375 | -175 |

## Round 14

Pistol winner TEAM_2; round winner TEAM_2; half round 2.

**TEAM_1** lost L=2,800; target H=19,500; funding U=19,700; gap D=0; observed next-equipment gap G=1,800; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 1,000 | 1,000 | 3,500 | 3,500 | 300 | 3,900 |
| kilo#1688 | 700 | 700 | 2,500 | 2,500 | 1,500 | 3,900 |
| Jasmine#6767 | 200 | 200 | 4,650 | 4,500 | 100 | 3,900 |
| wqe#4119 | 650 | 650 | 4,550 | 4,300 | 100 | 3,900 |
| SirBanArthur#King6 | 250 | 250 | 4,600 | 4,600 | 0 | 3,900 |

**TEAM_2** BONUS-ROUND DENIAL (pistol winner won this round, factor 0.8): denied 3,100; recovered 0; net 3,100.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 3,050 | 0 | 3,450 | 3,350 | 3,300 | 3,050 |
| NPrightdolphin#NA1 | 3,000 | 0 | 2,200 | 2,200 | 3,200 | 3,000 |
| ternstyle#GIGI | 4,100 | 4,100 | 4,000 | 4,000 | 2,200 | 3,900 |
| Deemo#Derf | 2,150 | 0 | 2,950 | 2,950 | 5,050 | 2,150 |
| Najumi#NPC | 2,450 | 0 | 2,200 | 2,200 | 4,950 | 2,450 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487260 | 40.511s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 250 | 3.23 | 0.00 | 3.23 | 0.97 (30%, absorbed) |
| event 487257 | 68.074s | ternstyle#GIGI -> wqe#4119 | enemy | 5v4 | 130 | 1.000 | 325.0 | 325.0 | 650 | 8.40 | 0.00 | 8.40 | 2.52 (30%, absorbed) |
| event 487261 | 72.217s | Najumi#NPC -> Jasmine#6767 | enemy | 5v3 | 90 | 1.000 | 225.0 | 225.0 | 200 | 2.58 | 0.00 | 2.58 | 0.78 (30%, absorbed) |
| event 487258 | 81.156s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 5v2 | 50 | 1.000 | 125.0 | 125.0 | 1,000 | 12.92 | 0.00 | 12.92 | 3.88 (30%, absorbed) |
| event 487262 | 87.598s | kilo#1688 -> ternstyle#GIGI | enemy | 1v5 | 60 | 1.000 | 150.0 | 7.5 | 4,100 | 0.00 | 352.51 | 352.51 | 282.01 (80%, denial) |
| event 487259 | 87.896s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 4v1 | 50 | 1.000 | 125.0 | 125.0 | 700 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +603 | 125 | +375 | +0 | 3.23 | 0.00 | +3 | +100 | +603 | +294 |
| kilo#1688 | +538 | 150 | +25 | +0 | 352.51 | 2.71 | +350 | +0 | +525 | +228 |
| ternstyle#GIGI | +600 | 200 | +518 | +75 | 21.32 | 282.01 | -261 | +0 | +457 | +420 |
| DoubleBl1nd#BEEF | +447 | 145 | +125 | +0 | 9.05 | 0.00 | +9 | +100 | +379 | +236 |
| Deemo#Derf | +354 | 154 | +0 | +0 | 0.00 | 0.00 | +0 | +200 | +354 | +192 |
| Najumi#NPC | +329 | 45 | +225 | +0 | 2.58 | 0.00 | +3 | +0 | +273 | +146 |
| Sub asf#nuhh | -181 | 0 | -125 | +0 | 0.00 | 3.88 | -4 | +0 | -129 | -54 |
| Jasmine#6767 | -273 | 9 | -225 | +0 | 0.00 | 0.78 | -1 | +0 | -217 | -79 |
| wqe#4119 | -383 | 0 | -325 | +0 | 0.00 | 2.52 | -3 | +0 | -328 | -121 |
| SirBanArthur#King6 | -350 | 26 | -375 | +0 | 0.00 | 0.97 | -1 | +0 | -350 | -106 |

## Round 15

Pistol winner TEAM_2; round winner TEAM_2; half round 3.

**TEAM_1** lost L=19,400; target H=19,500; funding U=18,900; gap D=600; observed next-equipment gap G=8,100; activation 0.1538; severity pool 1,246.15 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,500 | 3,500 | 3,500 | 3,500 | 100 | 3,900 |
| kilo#1688 | 2,500 | 2,500 | 700 | 700 | 4,400 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 3,350 | 3,200 | 250 | 3,900 |
| wqe#4119 | 4,300 | 4,300 | 2,350 | 2,100 | 1,050 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 1,900 | 1,900 | 1,700 | 3,900 |

**TEAM_2** lost L=2,200; target H=19,500; funding U=46,500; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 3,350 | 0 | 5,100 | 5,000 | 5,650 | 3,900 |
| NPrightdolphin#NA1 | 2,200 | 2,200 | 4,300 | 4,300 | 5,300 | 3,900 |
| ternstyle#GIGI | 4,000 | 0 | 4,200 | 4,200 | 4,300 | 3,900 |
| Deemo#Derf | 2,950 | 0 | 4,100 | 4,100 | 6,650 | 3,900 |
| Najumi#NPC | 2,200 | 0 | 5,050 | 5,050 | 5,100 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487267 | 2.796s | Sub asf#nuhh -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 2,200 | 28.43 | 0.00 | 28.43 | 8.53 (30%, absorbed) |
| event 487263 | 27.505s | ternstyle#GIGI -> wqe#4119 | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,300 | 55.56 | 35.69 | 91.26 | 73.01 (80%, constrained) |
| event 487264 | 30.467s | DoubleBl1nd#BEEF -> Jasmine#6767 | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 4,500 | 58.15 | 37.35 | 95.50 | 76.40 (80%, constrained) |
| event 487265 | 54.865s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v3 | 130 | 1.000 | 325.0 | 325.0 | 3,500 | 45.23 | 29.05 | 74.28 | 59.42 (80%, constrained) |
| event 487266 | 62.114s | DoubleBl1nd#BEEF -> SirBanArthur#King6 | enemy | 4v2 | 80 | 1.000 | 200.0 | 200.0 | 4,600 | 59.44 | 38.18 | 97.62 | 78.10 (80%, constrained) |
| event 487268 | 92.478s | Najumi#NPC -> kilo#1688 | enemy | 4v1 | 50 | 1.000 | 125.0 | 125.0 | 2,500 | 32.31 | 20.75 | 53.06 | 42.45 (80%, constrained) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +1,565 | 330 | +950 | +0 | 267.40 | 0.00 | +267 | +0 | +1,547 | +942 |
| ternstyle#GIGI | +569 | 128 | +350 | +0 | 91.26 | 0.00 | +91 | +0 | +569 | +358 |
| Najumi#NPC | +453 | 192 | +125 | +0 | 53.06 | 0.00 | +53 | +0 | +370 | +318 |
| Sub asf#nuhh | +226 | 207 | +50 | +0 | 28.43 | 59.42 | -31 | +0 | +226 | +193 |
| Deemo#Derf | +216 | 116 | +0 | +0 | 0.00 | 0.00 | +0 | +100 | +216 | +145 |
| kilo#1688 | -250 | 0 | -125 | +0 | 0.00 | 42.45 | -42 | +0 | -167 | -78 |
| SirBanArthur#King6 | -296 | 0 | -200 | +0 | 0.00 | 78.10 | -78 | +0 | -278 | -116 |
| wqe#4119 | -383 | 40 | -350 | +0 | 0.00 | 73.01 | -73 | +0 | -383 | -148 |
| NPrightdolphin#NA1 | -384 | 0 | -375 | +0 | 0.00 | 8.53 | -9 | +0 | -384 | -107 |
| Jasmine#6767 | -501 | 0 | -425 | +0 | 0.00 | 76.40 | -76 | +0 | -501 | -241 |

## Round 16

Pistol winner TEAM_2; round winner TEAM_2; half round 4.

**TEAM_1** lost L=11,400; target H=19,500; funding U=22,450; gap D=0; observed next-equipment gap G=4,550; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,500 | 3,500 | 3,300 | 3,300 | 800 | 3,900 |
| kilo#1688 | 700 | 700 | 0 | 0 | 4,400 | 3,900 |
| Jasmine#6767 | 3,200 | 3,200 | 4,000 | 3,850 | 450 | 3,900 |
| wqe#4119 | 2,100 | 2,100 | 4,800 | 4,550 | 250 | 3,900 |
| SirBanArthur#King6 | 1,900 | 1,900 | 4,600 | 4,600 | 1,600 | 3,900 |

**TEAM_2** lost L=13,450; target H=19,500; funding U=50,450; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 0 | 5,100 | 5,000 | 8,300 | 3,900 |
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 4,500 | 3,900 |
| ternstyle#GIGI | 4,200 | 0 | 4,900 | 4,900 | 7,600 | 3,900 |
| Deemo#Derf | 4,100 | 4,100 | 5,200 | 5,200 | 5,100 | 3,900 |
| Najumi#NPC | 5,050 | 5,050 | 4,250 | 4,250 | 5,450 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487272 | 2.406s | Sub asf#nuhh -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 375.0 | 375.0 | 4,100 | 52.98 | 0.00 | 52.98 | 15.89 (30%, absorbed) |
| event 487269 | 4.379s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 5v4 | 130 | 1.000 | 325.0 | 16.2 | 4,300 | 55.56 | 0.00 | 55.56 | 16.67 (30%, absorbed) |
| event 487274 | 5.265s | Najumi#NPC -> SirBanArthur#King6 | enemy | 3v5 | 120 | 1.000 | 300.0 | 300.0 | 1,900 | 24.55 | 0.00 | 24.55 | 7.37 (30%, absorbed) |
| event 487275 | 25.720s | Najumi#NPC -> wqe#4119 | enemy | 3v4 | 160 | 1.000 | 400.0 | 200.0 | 2,100 | 27.14 | 0.00 | 27.14 | 8.14 (30%, absorbed) |
| event 487276 | 28.009s | Najumi#NPC -> Jasmine#6767 | enemy | 3v3 | 180 | 1.000 | 450.0 | 76.5 | 3,200 | 41.35 | 0.00 | 41.35 | 12.41 (30%, absorbed) |
| event 487273 | 30.256s | Sub asf#nuhh -> Najumi#NPC | enemy | 2v3 | 170 | 1.000 | 425.0 | 425.0 | 5,050 | 65.26 | 0.00 | 65.26 | 19.58 (30%, absorbed) |
| event 487271 | 47.728s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 3,500 | 45.23 | 0.00 | 45.23 | 13.57 (30%, absorbed) |
| event 487270 | 72.579s | ternstyle#GIGI -> kilo#1688 | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 700 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,323 | 425 | +725 | +0 | 93.04 | 19.58 | +73 | +100 | +1,323 | +836 |
| Sub asf#nuhh | +633 | 228 | +300 | +0 | 118.24 | 13.57 | +105 | +0 | +633 | +352 |
| DoubleBl1nd#BEEF | +605 | 60 | +500 | +0 | 45.23 | 0.00 | +45 | +0 | +605 | +278 |
| ternstyle#GIGI | +453 | 100 | +325 | +0 | 9.05 | 0.00 | +9 | +0 | +434 | +247 |
| SirBanArthur#King6 | +223 | 150 | +25 | +0 | 55.56 | 7.37 | +48 | +0 | +223 | +197 |
| Jasmine#6767 | +216 | 88 | +40 | +117 | 0.00 | 12.41 | -12 | +100 | +216 | +79 |
| NPrightdolphin#NA1 | +186 | 39 | +164 | +180 | 0.00 | 16.67 | -17 | +0 | +186 | +43 |
| wqe#4119 | -121 | 0 | -113 | +87 | 0.00 | 8.14 | -8 | +0 | -121 | -78 |
| kilo#1688 | -347 | 0 | -325 | +0 | 0.00 | 2.71 | -3 | +0 | -328 | -122 |
| Deemo#Derf | -391 | 0 | -375 | +0 | 0.00 | 15.89 | -16 | +0 | -391 | -120 |

## Round 17

Pistol winner TEAM_2; round winner TEAM_1; half round 5.

**TEAM_1** lost L=9,150; target H=19,500; funding U=28,800; gap D=0; observed next-equipment gap G=50; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,300 | 0 | 3,850 | 3,850 | 3,450 | 3,900 |
| kilo#1688 | 0 | 0 | 4,600 | 4,600 | 600 | 3,900 |
| Jasmine#6767 | 3,850 | 0 | 4,650 | 4,500 | 2,700 | 3,900 |
| wqe#4119 | 4,550 | 4,550 | 5,100 | 4,850 | 1,900 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 4,600 | 4,600 | 700 | 3,900 |

**TEAM_2** lost L=23,650; target H=19,500; funding U=36,150; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 5,100 | 5,000 | 4,800 | 3,900 |
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,100 | 4,100 | 2,200 | 3,900 |
| ternstyle#GIGI | 4,900 | 4,900 | 7,000 | 7,000 | 3,000 | 3,900 |
| Deemo#Derf | 5,200 | 5,200 | 4,600 | 4,600 | 3,100 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 3,550 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487283 | 7.065s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 4v5 | 140 | 1.000 | 350.0 | 17.5 | 5,000 | 64.61 | 0.00 | 64.61 | 19.38 (30%, absorbed) |
| event 487277 | 7.846s | ternstyle#GIGI -> wqe#4119 | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 4,550 | 58.80 | 0.00 | 58.80 | 17.64 (30%, absorbed) |
| event 487280 | 14.520s | Jasmine#6767 -> ternstyle#GIGI | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 4,900 | 63.32 | 0.00 | 63.32 | 19.00 (30%, absorbed) |
| event 487281 | 18.651s | Jasmine#6767 -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 450.0 | 450.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487282 | 20.475s | Jasmine#6767 -> Deemo#Derf | enemy | 3v2 | 140 | 1.000 | 350.0 | 350.0 | 5,200 | 67.19 | 0.00 | 67.19 | 20.16 (30%, absorbed) |
| event 487279 | 23.281s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 1v3 | 120 | 1.000 | 300.0 | 15.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487278 | 24.060s | Sub asf#nuhh -> NPrightdolphin#NA1 | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 4,300 | 55.56 | 0.00 | 55.56 | 16.67 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Jasmine#6767 | +1,810 | 425 | +1,200 | +0 | 185.43 | 0.00 | +185 | +0 | +1,810 | +971 |
| Sub asf#nuhh | +411 | 30 | +325 | +0 | 55.56 | 0.00 | +56 | +0 | +411 | +157 |
| SirBanArthur#King6 | +382 | 120 | +180 | +195 | 0.00 | 17.83 | -18 | +100 | +382 | +144 |
| DoubleBl1nd#BEEF | +259 | 40 | +238 | +255 | 0.00 | 19.38 | -19 | +0 | +259 | +44 |
| ternstyle#GIGI | +173 | 108 | +25 | +0 | 58.80 | 19.00 | +40 | +0 | +173 | +158 |
| NPrightdolphin#NA1 | +167 | 149 | -25 | +0 | 59.44 | 16.67 | +43 | +0 | +167 | +187 |
| wqe#4119 | +122 | 150 | -75 | +0 | 64.61 | 17.64 | +47 | +0 | +122 | +135 |
| kilo#1688 | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| Deemo#Derf | -330 | 40 | -350 | +0 | 0.00 | 20.16 | -20 | +0 | -330 | -78 |
| Najumi#NPC | -433 | 33 | -450 | +0 | 0.00 | 16.48 | -16 | +0 | -433 | -124 |

## Round 18

Pistol winner TEAM_2; round winner TEAM_1; half round 6.

**TEAM_1** lost L=13,950; target H=19,500; funding U=29,800; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 3,850 | 0 | 4,450 | 4,450 | 5,250 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,600 | 4,600 | 2,600 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,650 | 4,500 | 1,850 | 3,900 |
| wqe#4119 | 4,850 | 4,850 | 5,100 | 4,850 | 400 | 3,900 |
| SirBanArthur#King6 | 4,600 | 0 | 4,600 | 4,600 | 200 | 3,900 |

**TEAM_2** lost L=24,950; target H=19,500; funding U=29,600; gap D=0; observed next-equipment gap G=2,100; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 5,100 | 5,000 | 3,300 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 300 | 3,900 |
| ternstyle#GIGI | 7,000 | 7,000 | 1,800 | 1,800 | 4,400 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 4,600 | 4,600 | 1,600 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 2,600 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487290 | 9.262s | Jasmine#6767 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 375.0 | 63.8 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487286 | 11.686s | ternstyle#GIGI -> Jasmine#6767 | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |
| event 487291 | 19.649s | Jasmine#6767 -> ternstyle#GIGI | enemy | 5v4 | 130 | 1.000 | 325.0 | 325.0 | 7,000 | 90.45 | 0.00 | 90.45 | 27.14 (30%, absorbed) |
| event 487289 | 27.530s | Najumi#NPC -> Jasmine#6767 | enemy | 3v5 | 120 | 1.000 | 300.0 | 30.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487292 | 27.822s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 4v3 | 130 | 1.000 | 325.0 | 55.2 | 4,100 | 52.98 | 0.00 | 52.98 | 15.89 (30%, absorbed) |
| event 487284 | 29.361s | SirBanArthur#King6 -> Najumi#NPC | enemy | 4v2 | 80 | 1.000 | 200.0 | 200.0 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487287 | 30.472s | DoubleBl1nd#BEEF -> wqe#4119 | enemy | 1v4 | 70 | 1.000 | 175.0 | 175.0 | 4,850 | 62.67 | 0.00 | 62.67 | 18.80 (30%, absorbed) |
| event 487288 | 35.060s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v3 | 120 | 1.000 | 300.0 | 300.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487285 | 58.305s | SirBanArthur#King6 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.000 | 325.0 | 325.0 | 5,000 | 64.61 | 0.00 | 64.61 | 19.38 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SirBanArthur#King6 | +880 | 235 | +525 | +0 | 119.53 | 0.00 | +120 | +0 | +880 | +476 |
| Jasmine#6767 | +804 | 244 | +428 | +108 | 149.90 | 17.44 | +132 | +0 | +804 | +396 |
| DoubleBl1nd#BEEF | +638 | 385 | +150 | +0 | 122.11 | 19.38 | +103 | +0 | +638 | +558 |
| wqe#4119 | +529 | 245 | +150 | +0 | 52.98 | 18.80 | +34 | +100 | +529 | +340 |
| Deemo#Derf | +306 | 120 | +104 | +168 | 0.00 | 17.83 | -18 | +100 | +306 | +128 |
| Najumi#NPC | +254 | 170 | +100 | +0 | 0.00 | 16.48 | -16 | +0 | +254 | +263 |
| ternstyle#GIGI | +148 | 92 | +25 | +0 | 58.15 | 27.14 | +31 | +0 | +148 | +142 |
| NPrightdolphin#NA1 | +13 | 0 | +29 | +84 | 0.00 | 15.89 | -16 | +0 | +13 | -18 |
| Sub asf#nuhh | +0 | 0 | +0 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +0 |
| kilo#1688 | -318 | 0 | -300 | +0 | 0.00 | 17.83 | -18 | +0 | -318 | -120 |

## Round 19

Pistol winner TEAM_2; round winner TEAM_2; half round 7.

**TEAM_1** lost L=23,000; target H=19,500; funding U=21,450; gap D=0; observed next-equipment gap G=2,850; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Sub asf#nuhh | 4,450 | 4,450 | 4,450 | 4,450 | 3,450 | 3,900 |
| kilo#1688 | 4,600 | 4,600 | 4,600 | 4,600 | 600 | 3,900 |
| Jasmine#6767 | 4,500 | 4,500 | 4,300 | 4,150 | 0 | 3,900 |
| wqe#4119 | 4,850 | 4,850 | 2,700 | 2,450 | 700 | 3,900 |
| SirBanArthur#King6 | 4,600 | 4,600 | 2,500 | 2,500 | 50 | 3,900 |

**TEAM_2** lost L=15,650; target H=19,500; funding U=30,850; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 5,000 | 5,000 | 5,100 | 5,000 | 2,600 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 3,600 | 3,900 |
| ternstyle#GIGI | 1,800 | 1,800 | 6,700 | 6,700 | 1,800 | 3,900 |
| Deemo#Derf | 4,600 | 4,600 | 4,600 | 4,600 | 1,000 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 2,350 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487293 | 3.406s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 5v5 | 150 | 1.000 | 375.0 | 37.5 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487300 | 4.931s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 4v5 | 140 | 1.000 | 350.0 | 350.0 | 5,000 | 64.61 | 0.00 | 64.61 | 19.38 (30%, absorbed) |
| event 487301 | 5.314s | wqe#4119 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 425.0 | 425.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487298 | 14.636s | Najumi#NPC -> wqe#4119 | enemy | 3v4 | 160 | 1.000 | 400.0 | 400.0 | 4,850 | 62.67 | 0.00 | 62.67 | 18.80 (30%, absorbed) |
| event 487294 | 31.538s | Sub asf#nuhh -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 450.0 | 22.5 | 4,250 | 54.92 | 0.00 | 54.92 | 16.48 (30%, absorbed) |
| event 487295 | 32.101s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 2v3 | 170 | 1.000 | 425.0 | 425.0 | 4,450 | 57.50 | 0.00 | 57.50 | 17.25 (30%, absorbed) |
| event 487299 | 104.563s | Jasmine#6767 -> ternstyle#GIGI | enemy | 2v2 | 200 | 1.000 | 500.0 | 500.0 | 1,800 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 487296 | 107.212s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 1v2 | 190 | 1.000 | 475.0 | 475.0 | 4,600 | 59.44 | 0.00 | 59.44 | 17.83 (30%, absorbed) |
| event 487297 | 123.544s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 1v1 | 250 | 1.000 | 625.0 | 625.0 | 4,500 | 58.15 | 0.00 | 58.15 | 17.44 (30%, absorbed) |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +2,598 | 274 | +1,525 | +0 | 175.09 | 0.00 | +175 | +0 | +1,974 | +1,086 |
| Najumi#NPC | +998 | 220 | +632 | +255 | 62.67 | 16.48 | +46 | +100 | +998 | +427 |
| wqe#4119 | +820 | 340 | +375 | +0 | 124.05 | 18.80 | +105 | +0 | +820 | +534 |
| DoubleBl1nd#BEEF | +215 | 150 | +25 | +0 | 59.44 | 19.38 | +40 | +0 | +215 | +217 |
| Sub asf#nuhh | +173 | 110 | +25 | +0 | 54.92 | 17.25 | +38 | +0 | +173 | +110 |
| kilo#1688 | +134 | 0 | +152 | +189 | 0.00 | 17.83 | -18 | +0 | +134 | -15 |
| Jasmine#6767 | -236 | 150 | -125 | +0 | 23.26 | 17.44 | +6 | +0 | +31 | +33 |
| ternstyle#GIGI | -473 | 105 | -500 | +0 | 0.00 | 6.98 | -7 | +100 | -302 | -43 |
| SirBanArthur#King6 | -554 | 25 | -475 | +0 | 0.00 | 17.83 | -18 | +100 | -368 | -200 |
| Deemo#Derf | -443 | 0 | -425 | +0 | 0.00 | 17.83 | -18 | +0 | -443 | -147 |

## Round 20

Economy abstains: **final_round** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487303 | 15.885s | Sub asf#nuhh -> Deemo#Derf | combat | 5v5 | 150 | 1.000 | 375.0 | 375.0 | | | | | no economy this round |
| event 487306 | 28.670s | NPrightdolphin#NA1 -> Jasmine#6767 | combat | 4v5 | 140 | 1.000 | 350.0 | 350.0 | | | | | no economy this round |
| event 487304 | 67.812s | Sub asf#nuhh -> ternstyle#GIGI | combat | 4v4 | 170 | 1.000 | 425.0 | 425.0 | | | | | no economy this round |
| event 487305 | 72.969s | Sub asf#nuhh -> Najumi#NPC | combat | 4v3 | 130 | 1.000 | 325.0 | 32.5 | | | | | no economy this round |
| event 487307 | 74.888s | NPrightdolphin#NA1 -> Sub asf#nuhh | combat | 2v4 | 130 | 1.000 | 325.0 | 325.0 | | | | | no economy this round |
| event 487308 | 77.454s | NPrightdolphin#NA1 -> wqe#4119 | combat | 2v3 | 170 | 1.000 | 425.0 | 425.0 | | | | | no economy this round |
| event 487302 | 80.575s | DoubleBl1nd#BEEF -> SirBanArthur#King6 | combat | 2v2 | 200 | 1.000 | 500.0 | 500.0 | | | | | no economy this round |
| event 487311 | 89.562s | kilo#1688 -> DoubleBl1nd#BEEF | combat | 1v2 | 190 | 1.000 | 475.0 | 475.0 | | | | | no economy this round |
| event 487309 | 99.831s | NPrightdolphin#NA1 -> Jasmine#6767 | combat | 1v2 | 190 | 1.000 | 475.0 | 475.0 | | | | | no economy this round |
| event 487310 | 101.546s | NPrightdolphin#NA1 -> kilo#1688 | combat | 1v1 | 250 | 1.000 | 625.0 | 625.0 | | | | | no economy this round |

| Player | Stored impact | A*damage | B*leverage | of which trade credit | Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +3,646 | 615 | +2,200 | +0 | 0.00 | 0.00 | +0 | +0 | +2,815 | +1,810 |
| Sub asf#nuhh | +1,218 | 424 | +800 | +0 | 0.00 | 0.00 | +0 | +0 | +1,224 | +839 |
| DoubleBl1nd#BEEF | +406 | 255 | +25 | +0 | 0.00 | 0.00 | +0 | +200 | +480 | +297 |
| Najumi#NPC | +182 | 17 | +143 | +176 | 0.00 | 0.00 | +0 | +0 | +160 | +8 |
| kilo#1688 | -205 | 150 | -150 | +0 | 0.00 | 0.00 | +0 | +0 | +0 | +80 |
| Deemo#Derf | -220 | 55 | -375 | +0 | 0.00 | 0.00 | +0 | +100 | -220 | -81 |
| wqe#4119 | -337 | 71 | -425 | +0 | 0.00 | 0.00 | +0 | +100 | -254 | -83 |
| ternstyle#GIGI | -431 | 0 | -425 | +0 | 0.00 | 0.00 | +0 | +0 | -425 | -171 |
| SirBanArthur#King6 | -627 | 0 | -500 | +0 | 0.00 | 0.00 | +0 | +0 | -500 | -195 |
| Jasmine#6767 | -1,079 | 40 | -825 | +0 | 0.00 | 0.00 | +0 | +0 | -785 | -347 |

## Match totals

| Player | Stored | A*damage | B*leverage | of which trade credit | Gross econ credit | Gross econ debit | C*econ | D*assists | Econ / played round | After | Legacy replay |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +10,079 | 3,243 | +4,493 | +264 | 725.57 | 424.64 | +300 | +800 | +15.00 | +8,836 | +6,021 |
| Najumi#NPC | +7,942 | 2,808 | +3,204 | +721 | 975.69 | 464.40 | +513 | +800 | +25.65 | +7,325 | +4,689 |
| DoubleBl1nd#BEEF | +6,201 | 2,336 | +1,963 | +255 | 1547.83 | 477.30 | +1,072 | +600 | +53.60 | +5,971 | +3,883 |
| kilo#1688 | +5,551 | 2,455 | +1,136 | +677 | 1281.30 | 522.01 | +760 | +900 | +38.00 | +5,251 | +3,360 |
| ternstyle#GIGI | +5,086 | 2,536 | +1,497 | +173 | 651.96 | 748.17 | -95 | +500 | -4.75 | +4,438 | +4,210 |
| Deemo#Derf | +4,167 | 2,777 | -643 | +438 | 870.66 | 501.44 | +368 | +1,400 | +18.40 | +3,902 | +3,045 |
| wqe#4119 | +3,258 | 2,288 | +48 | +771 | 1154.27 | 559.49 | +594 | +400 | +29.70 | +3,330 | +2,426 |
| Jasmine#6767 | +2,452 | 2,295 | +53 | +497 | 1116.75 | 624.86 | +492 | +300 | +24.60 | +3,140 | +2,223 |
| Sub asf#nuhh | +2,312 | 1,732 | +938 | +305 | 485.07 | 594.08 | -108 | +200 | -5.40 | +2,762 | +2,021 |
| SirBanArthur#King6 | +841 | 1,913 | -1,695 | +195 | 597.43 | 452.16 | +146 | +800 | +7.30 | +1,164 | +1,325 |
