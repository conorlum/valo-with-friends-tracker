# Per-kill trace: match 3120 Summit 7-13

Candidate `impact-buy-disruption-30-80-rc2`, manifest LF-SHA-256 `ae043e361e3398ee578e82e9a393e63b8977d8a9ef4cad3894d357d5c8ebdae6`.

- **Before: `live_legacy`** -- enable_econ_component=False, econ_model=None, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF
- **After: `buy_disruption_v2_30_80`** -- enable_econ_component=True, econ_model=buy_disruption_v2_30_80, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF

impact = A*damage + B*leverage + C*econ with A=1.25, B=1.0, C=1.0; econ points = C * 1007.9209 * raw, rounded ONCE per player-round.
Kill credit = small equipment value + allocated buy-disruption value. Death debit = 30% of the victim's damage value when the team's funding absorbed the loss, 80% when its severity pool is positive (constrained next buy). Repeated deaths expose no new kit.

## Round 1

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487156 | 16.431s | Sub asf#nuhh -> DoubleBl1nd#BEEF | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 487160 | 21.152s | kilo#1688 -> Deemo#Derf | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 487157 | 30.627s | Najumi#NPC -> SirBanArthur#King6 | combat | 3v5 | 120 | 1.033 | 123.9 | 123.9 | | | | | no economy this round |
| event 487158 | 30.931s | Jasmine#6767 -> NPrightdolphin#NA1 | combat | 4v3 | 130 | 1.038 | 135.0 | 135.0 | | | | | no economy this round |
| event 487161 | 38.014s | kilo#1688 -> Najumi#NPC | combat | 4v2 | 80 | 1.172 | 93.8 | 9.4 | | | | | no economy this round |
| event 487155 | 39.046s | ternstyle#GIGI -> kilo#1688 | combat | 1v4 | 70 | 1.191 | 83.4 | 41.7 | | | | | no economy this round |
| event 487159 | 43.116s | wqe#4119 -> ternstyle#GIGI | combat | 3v1 | 70 | 1.268 | 88.8 | 88.8 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +576 | 369 | +182 | 0.00 | 0.00 | +0 | +551 |
| wqe#4119 | +276 | 194 | +89 | 0.00 | 0.00 | +0 | +283 |
| Sub asf#nuhh | +300 | 125 | +150 | 0.00 | 0.00 | +0 | +275 |
| Jasmine#6767 | +283 | 130 | +135 | 0.00 | 0.00 | +0 | +265 |
| Najumi#NPC | +173 | 42 | +115 | 0.00 | 0.00 | +0 | +157 |
| ternstyle#GIGI | +129 | 125 | -5 | 0.00 | 0.00 | +0 | +120 |
| NPrightdolphin#NA1 | +18 | 171 | -135 | 0.00 | 0.00 | +0 | +36 |
| Deemo#Derf | -16 | 136 | -130 | 0.00 | 0.00 | +0 | +6 |
| SirBanArthur#King6 | -85 | 56 | -124 | 0.00 | 0.00 | +0 | -68 |
| DoubleBl1nd#BEEF | -110 | 65 | -150 | 0.00 | 0.00 | +0 | -85 |

## Round 2

Pistol winner TEAM_1; round winner TEAM_2; half round 2.

**TEAM_1** lost L=16,350; target H=16,350 (carryover targets); funding U=16,250; gap D=100; observed next-equipment gap G=10,100; activation 0.0256; severity pool 258.97 -> CONSTRAINED next buy (80% death debits).

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
| event 487169 | 13.451s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 100 | 0.52 | 0.00 | 0.52 | 0.16 (30%, absorbed) |
| event 487165 | 26.527s | Deemo#Derf -> Jasmine#6767 | enemy | 4v5 | 140 | 1.101 | 154.1 | 154.1 | 3,700 | 19.12 | 3.03 | 22.15 | 17.72 (80%, constrained) |
| event 487163 | 32.869s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 4v4 | 170 | 1.220 | 207.5 | 207.5 | 3,300 | 17.06 | 2.70 | 19.76 | 15.81 (80%, constrained) |
| event 487164 | 36.810s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v3 | 130 | 1.295 | 168.3 | 168.3 | 3,800 | 19.64 | 3.11 | 22.75 | 18.20 (80%, constrained) |
| event 487162 | 43.682s | SirBanArthur#King6 -> ternstyle#GIGI | enemy | 2v4 | 130 | 1.424 | 185.2 | 138.9 | 1,100 | 5.69 | 0.00 | 5.69 | 1.71 (30%, absorbed) |
| event 487168 | 43.895s | wqe#4119 -> Deemo#Derf | enemy | 2v3 | 170 | 1.428 | 242.8 | 24.3 | 300 | 1.55 | 0.00 | 1.55 | 0.47 (30%, absorbed) |
| event 487166 | 45.410s | Najumi#NPC -> wqe#4119 | enemy | 2v2 | 200 | 1.457 | 291.4 | 291.4 | 3,050 | 15.76 | 2.50 | 18.26 | 14.61 (80%, constrained) |
| event 487167 | 48.910s | Najumi#NPC -> SirBanArthur#King6 | enemy | 2v1 | 130 | 1.523 | 198.0 | 198.0 | 2,500 | 12.92 | 2.05 | 14.97 | 11.98 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,006 | 431 | +489 | 33.23 | 0.00 | +33 | +953 |
| DoubleBl1nd#BEEF | +780 | 270 | +376 | 42.51 | 0.00 | +43 | +689 |
| Deemo#Derf | +533 | 321 | +130 | 22.15 | 0.47 | +22 | +473 |
| SirBanArthur#King6 | +142 | 202 | -13 | 5.69 | 11.98 | -6 | +183 |
| kilo#1688 | -17 | 124 | -57 | 0.52 | 15.81 | -15 | +52 |
| wqe#4119 | -66 | 98 | -49 | 1.55 | 14.61 | -13 | +36 |
| ternstyle#GIGI | +17 | 138 | -139 | 0.00 | 1.71 | -2 | -3 |
| NPrightdolphin#NA1 | -4 | 142 | -150 | 0.00 | 0.16 | +0 | -8 |
| Jasmine#6767 | -203 | 28 | -154 | 0.00 | 17.72 | -18 | -144 |
| Sub asf#nuhh | -223 | 0 | -168 | 0.00 | 18.20 | -18 | -186 |

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
| event 487170 | 4.368s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 800 | 4.14 | 0.00 | 4.14 | 1.24 (30%, absorbed) |
| event 487175 | 14.647s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 3,800 | 19.64 | 0.00 | 19.64 | 5.89 (30%, absorbed) |
| event 487173 | 19.781s | NPrightdolphin#NA1 -> wqe#4119 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 1,850 | 9.56 | 0.00 | 9.56 | 2.87 (30%, absorbed) |
| event 487176 | 37.938s | kilo#1688 -> Najumi#NPC | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 3,750 | 19.38 | 0.00 | 19.38 | 5.81 (30%, absorbed) |
| event 487174 | 81.166s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 487171 | 92.110s | Deemo#Derf -> Jasmine#6767 | enemy | 3v2 | 140 | 1.000 | 140.0 | 140.0 | 1,000 | 5.17 | 0.00 | 5.17 | 1.55 (30%, absorbed) |
| event 487172 | 94.273s | Deemo#Derf -> kilo#1688 | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,057 | 726 | +350 | 19.38 | 0.00 | +19 | +1,095 |
| Deemo#Derf | +508 | 325 | +210 | 8.79 | 0.00 | +9 | +544 |
| kilo#1688 | +496 | 362 | +90 | 19.38 | 1.09 | +18 | +470 |
| ternstyle#GIGI | +107 | 125 | +10 | 4.14 | 5.89 | -2 | +133 |
| Jasmine#6767 | +106 | 81 | +0 | 19.64 | 1.55 | +18 | +99 |
| DoubleBl1nd#BEEF | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Najumi#NPC | -62 | 130 | -160 | 0.00 | 5.81 | -6 | -36 |
| SirBanArthur#King6 | -45 | 125 | -180 | 0.00 | 2.95 | -3 | -58 |
| Sub asf#nuhh | -131 | 0 | -150 | 0.00 | 1.24 | -1 | -151 |
| wqe#4119 | -161 | 0 | -170 | 0.00 | 2.87 | -3 | -173 |

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
| event 487181 | 6.766s | kilo#1688 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487182 | 20.822s | kilo#1688 -> DoubleBl1nd#BEEF | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 5,000 | 25.84 | 0.00 | 25.84 | 7.75 (30%, absorbed) |
| event 487179 | 27.554s | wqe#4119 -> ternstyle#GIGI | enemy | 5v3 | 90 | 1.067 | 96.0 | 96.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 487177 | 29.859s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 5v2 | 50 | 1.111 | 55.5 | 55.5 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 487178 | 39.683s | Najumi#NPC -> kilo#1688 | enemy | 1v5 | 60 | 1.296 | 77.8 | 7.8 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487180 | 41.672s | wqe#4119 -> Najumi#NPC | enemy | 4v1 | 50 | 1.334 | 66.7 | 66.7 | 5,050 | 26.10 | 0.00 | 26.10 | 7.83 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +835 | 562 | +272 | 49.62 | 7.13 | +42 | +876 |
| wqe#4119 | +679 | 531 | +163 | 48.85 | 0.00 | +49 | +743 |
| SirBanArthur#King6 | +237 | 188 | +56 | 21.19 | 0.00 | +21 | +265 |
| Najumi#NPC | +196 | 186 | +11 | 23.78 | 7.83 | +16 | +213 |
| NPrightdolphin#NA1 | +133 | 182 | -56 | 0.00 | 6.36 | -6 | +120 |
| Sub asf#nuhh | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Jasmine#6767 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| DoubleBl1nd#BEEF | -30 | 100 | -130 | 0.00 | 7.75 | -8 | -38 |
| Deemo#Derf | -95 | 55 | -150 | 0.00 | 7.13 | -7 | -102 |
| ternstyle#GIGI | -92 | 0 | -96 | 0.00 | 6.82 | -7 | -103 |

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
| event 487187 | 10.914s | Sub asf#nuhh -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,200 | 21.71 | 0.00 | 21.71 | 6.51 (30%, absorbed) |
| event 487188 | 21.671s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487186 | 23.398s | Deemo#Derf -> wqe#4119 | enemy | 4v4 | 170 | 1.000 | 170.0 | 59.5 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 487183 | 26.445s | SirBanArthur#King6 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487189 | 44.581s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487191 | 56.221s | wqe#4119 -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487190 | 60.450s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 2v3 | 170 | 1.000 | 170.0 | 127.5 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 487192 | 65.616s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 2v2 | 200 | 1.076 | 215.2 | 215.2 | 3,500 | 18.09 | 0.00 | 18.09 | 5.43 (30%, absorbed) |
| event 487184 | 66.964s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v2 | 190 | 1.101 | 209.3 | 209.3 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487185 | 77.672s | DoubleBl1nd#BEEF -> wqe#4119 | enemy | 1v1 | 250 | 1.303 | 325.8 | 325.8 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,032 | 715 | +255 | 69.00 | 5.43 | +64 | +1,034 |
| DoubleBl1nd#BEEF | +867 | 395 | +535 | 23.78 | 0.00 | +24 | +954 |
| wqe#4119 | +509 | 472 | +10 | 40.06 | 7.52 | +33 | +515 |
| Deemo#Derf | +231 | 231 | +0 | 25.07 | 7.13 | +18 | +249 |
| SirBanArthur#King6 | +211 | 214 | +10 | 23.78 | 7.13 | +17 | +241 |
| Sub asf#nuhh | +184 | 186 | +10 | 21.71 | 6.59 | +15 | +211 |
| Najumi#NPC | +155 | 335 | -180 | 0.00 | 6.59 | -7 | +148 |
| Jasmine#6767 | -138 | 0 | -128 | 0.00 | 6.98 | -7 | -135 |
| kilo#1688 | -134 | 62 | -209 | 0.00 | 7.13 | -7 | -154 |
| ternstyle#GIGI | -150 | 0 | -150 | 0.00 | 6.51 | -7 | -157 |

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
| event 487196 | 12.919s | ternstyle#GIGI -> Jasmine#6767 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,500 | 23.26 | 6.78 | 30.04 | 24.03 (80%, constrained) |
| event 487201 | 16.677s | Sub asf#nuhh -> Najumi#NPC | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487199 | 85.296s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,250 | 21.97 | 6.40 | 28.37 | 22.69 (80%, constrained) |
| event 487200 | 100.665s | Deemo#Derf -> wqe#4119 | enemy | 4v3 | 130 | 1.068 | 138.9 | 48.6 | 3,700 | 19.12 | 5.57 | 24.70 | 19.76 (80%, constrained) |
| event 487193 | 100.796s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 2v4 | 130 | 1.071 | 139.2 | 139.2 | 3,300 | 17.06 | 0.00 | 17.06 | 5.12 (30%, absorbed) |
| event 487197 | 103.193s | ternstyle#GIGI -> kilo#1688 | enemy | 3v2 | 140 | 1.116 | 156.2 | 156.2 | 4,600 | 23.78 | 6.93 | 30.70 | 24.56 (80%, constrained) |
| event 487194 | 104.416s | SirBanArthur#King6 -> Deemo#Derf | enemy | 1v3 | 120 | 1.139 | 136.7 | 136.7 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487195 | 107.363s | SirBanArthur#King6 -> DoubleBl1nd#BEEF | enemy | 1v2 | 190 | 1.194 | 226.9 | 226.9 | 5,000 | 25.84 | 0.00 | 25.84 | 7.75 (30%, absorbed) |
| event 487198 | 119.258s | ternstyle#GIGI -> SirBanArthur#King6 | enemy | 1v1 | 250 | 1.419 | 354.7 | 354.7 | 4,600 | 23.78 | 6.93 | 30.70 | 24.56 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +1,623 | 762 | +661 | 91.44 | 0.00 | +91 | +1,514 |
| SirBanArthur#King6 | +769 | 732 | +148 | 64.87 | 24.56 | +40 | +920 |
| DoubleBl1nd#BEEF | +339 | 311 | -57 | 28.37 | 7.75 | +21 | +275 |
| Deemo#Derf | +243 | 199 | +2 | 24.70 | 6.59 | +18 | +219 |
| Sub asf#nuhh | +152 | 242 | -30 | 21.97 | 22.69 | -1 | +211 |
| NPrightdolphin#NA1 | -36 | 88 | -139 | 0.00 | 5.12 | -5 | -56 |
| kilo#1688 | -103 | 115 | -156 | 0.00 | 24.56 | -25 | -66 |
| wqe#4119 | -60 | 0 | -49 | 0.00 | 19.76 | -20 | -69 |
| Najumi#NPC | -96 | 44 | -140 | 0.00 | 6.59 | -7 | -103 |
| Jasmine#6767 | -228 | 0 | -150 | 0.00 | 24.03 | -24 | -174 |

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
| event 487205 | 23.364s | Jasmine#6767 -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,700 | 24.29 | 100.66 | 124.95 | 99.96 (80%, constrained) |
| event 487203 | 31.740s | Deemo#Derf -> Sub asf#nuhh | enemy | 4v5 | 140 | 1.000 | 140.0 | 49.0 | 3,500 | 18.09 | 0.00 | 18.09 | 5.43 (30%, absorbed) |
| event 487204 | 33.445s | Deemo#Derf -> Jasmine#6767 | enemy | 4v4 | 170 | 1.000 | 170.0 | 17.0 | 3,650 | 18.87 | 0.00 | 18.87 | 5.66 (30%, absorbed) |
| event 487207 | 35.192s | wqe#4119 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,600 | 23.78 | 98.51 | 122.29 | 97.83 (80%, constrained) |
| event 487206 | 46.677s | Jasmine#6767 -> Jasmine#6767 | self | 4v4 | 160 | 1.000 | 0.0 | 160.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487209 | 71.769s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.254 | 225.6 | 225.6 | 4,100 | 21.19 | 87.81 | 109.00 | 87.20 (80%, constrained) |
| event 487210 | 79.241s | kilo#1688 -> Najumi#NPC | enemy | 3v2 | 140 | 1.395 | 195.2 | 195.2 | 4,250 | 21.97 | 91.02 | 112.99 | 90.39 (80%, constrained) |
| event 487202 | 85.821s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v3 | 120 | 1.519 | 182.2 | 9.1 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 487208 | 86.712s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.536 | 199.6 | 199.6 | 5,000 | 25.84 | 107.08 | 132.92 | 106.34 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +1,038 | 531 | +412 | 221.98 | 2.95 | +219 | +1,162 |
| wqe#4119 | +909 | 420 | +370 | 255.22 | 0.00 | +255 | +1,045 |
| Deemo#Derf | +506 | 481 | +140 | 36.96 | 97.83 | -61 | +560 |
| Jasmine#6767 | +444 | 454 | -27 | 124.95 | 5.66 | +119 | +546 |
| DoubleBl1nd#BEEF | +58 | 156 | -17 | 9.82 | 106.34 | -97 | +42 |
| SirBanArthur#King6 | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| Sub asf#nuhh | -46 | 0 | -49 | 0.00 | 5.43 | -5 | -54 |
| ternstyle#GIGI | -170 | 50 | -150 | 0.00 | 99.96 | -100 | -200 |
| Najumi#NPC | -236 | 0 | -195 | 0.00 | 90.39 | -90 | -285 |
| NPrightdolphin#NA1 | -277 | 0 | -226 | 0.00 | 87.20 | -87 | -313 |

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
| event 487213 | 12.745s | Jasmine#6767 -> DoubleBl1nd#BEEF | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,600 | 8.27 | 0.00 | 8.27 | 2.48 (30%, absorbed) |
| event 487211 | 17.593s | Deemo#Derf -> wqe#4119 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 5,350 | 27.65 | 0.00 | 27.65 | 8.30 (30%, absorbed) |
| event 487215 | 24.955s | kilo#1688 -> Deemo#Derf | enemy | 4v4 | 170 | 1.039 | 176.6 | 176.6 | 1,550 | 8.01 | 0.00 | 8.01 | 2.40 (30%, absorbed) |
| event 487212 | 31.636s | Sub asf#nuhh -> Najumi#NPC | enemy | 4v3 | 130 | 1.165 | 151.5 | 151.5 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |
| event 487214 | 34.888s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v2 | 80 | 1.226 | 98.1 | 98.1 | 2,350 | 12.15 | 0.00 | 12.15 | 3.64 (30%, absorbed) |
| event 487216 | 36.261s | kilo#1688 -> NPrightdolphin#NA1 | enemy | 4v1 | 50 | 1.252 | 62.6 | 62.6 | 1,300 | 6.72 | 0.00 | 6.72 | 2.02 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Jasmine#6767 | +679 | 469 | +248 | 20.42 | 0.00 | +20 | +737 |
| kilo#1688 | +621 | 419 | +239 | 14.73 | 0.00 | +15 | +673 |
| Deemo#Derf | +435 | 425 | -37 | 27.65 | 2.40 | +25 | +413 |
| Sub asf#nuhh | +246 | 125 | +151 | 3.62 | 0.00 | +4 | +280 |
| SirBanArthur#King6 | +81 | 81 | +0 | 0.00 | 0.00 | +0 | +81 |
| NPrightdolphin#NA1 | +46 | 95 | -63 | 0.00 | 2.02 | -2 | +30 |
| DoubleBl1nd#BEEF | -64 | 69 | -150 | 0.00 | 2.48 | -2 | -83 |
| Najumi#NPC | -57 | 64 | -151 | 0.00 | 1.09 | -1 | -88 |
| ternstyle#GIGI | -77 | 0 | -98 | 0.00 | 3.64 | -4 | -102 |
| wqe#4119 | -163 | 0 | -140 | 0.00 | 8.30 | -8 | -148 |

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
| event 487218 | 17.832s | Deemo#Derf -> wqe#4119 | enemy | 5v5 | 150 | 1.000 | 150.0 | 7.5 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 487223 | 17.908s | Jasmine#6767 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487219 | 18.543s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 5,400 | 27.91 | 0.00 | 27.91 | 8.37 (30%, absorbed) |
| event 487224 | 25.966s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 3,600 | 18.61 | 0.00 | 18.61 | 5.58 (30%, absorbed) |
| event 487220 | 26.839s | NPrightdolphin#NA1 -> kilo#1688 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487225 | 42.861s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 3,750 | 19.38 | 0.00 | 19.38 | 5.81 (30%, absorbed) |
| event 487221 | 43.654s | Najumi#NPC -> Sub asf#nuhh | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,450 | 23.00 | 0.00 | 23.00 | 6.90 (30%, absorbed) |
| event 487222 | 51.771s | Najumi#NPC -> wqe#4119 | enemy | 2v2 | 200 | 1.065 | 212.9 | 212.9 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487217 | 56.216s | DoubleBl1nd#BEEF -> Jasmine#6767 | enemy | 2v1 | 130 | 1.148 | 149.3 | 149.3 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +633 | 308 | +383 | 23.00 | 0.00 | +23 | +714 |
| NPrightdolphin#NA1 | +647 | 530 | +120 | 51.69 | 5.81 | +46 | +696 |
| Jasmine#6767 | +571 | 381 | +171 | 42.38 | 6.98 | +35 | +587 |
| Deemo#Derf | +324 | 344 | +0 | 25.07 | 7.13 | +18 | +362 |
| DoubleBl1nd#BEEF | +125 | 6 | +149 | 23.26 | 0.00 | +23 | +178 |
| wqe#4119 | +190 | 206 | -40 | 19.38 | 7.52 | +12 | +178 |
| ternstyle#GIGI | +191 | 350 | -170 | 0.00 | 5.58 | -6 | +174 |
| SirBanArthur#King6 | -33 | 100 | -140 | 0.00 | 8.37 | -8 | -48 |
| kilo#1688 | -121 | 31 | -160 | 0.00 | 7.13 | -7 | -136 |
| Sub asf#nuhh | -147 | 0 | -170 | 0.00 | 6.90 | -7 | -177 |

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
| event 487227 | 3.465s | ternstyle#GIGI -> wqe#4119 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 487231 | 10.250s | Jasmine#6767 -> Deemo#Derf | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,600 | 23.78 | 26.06 | 49.83 | 39.87 (80%, constrained) |
| event 487228 | 12.190s | Sub asf#nuhh -> ternstyle#GIGI | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,050 | 20.93 | 22.94 | 43.87 | 35.10 (80%, constrained) |
| event 487232 | 15.065s | Jasmine#6767 -> Najumi#NPC | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 24.07 | 46.04 | 36.83 (80%, constrained) |
| event 487229 | 28.026s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 2v4 | 130 | 1.000 | 130.0 | 130.0 | 1,550 | 8.01 | 0.00 | 8.01 | 2.40 (30%, absorbed) |
| event 487230 | 40.031s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 487233 | 52.926s | Jasmine#6767 -> Jasmine#6767 | self | 3v3 | 170 | 1.000 | 0.0 | 190.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487226 | 61.667s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 2v2 | 200 | 1.283 | 256.5 | 256.5 | 4,000 | 20.68 | 22.66 | 43.33 | 34.67 (80%, constrained) |
| event 487234 | 65.224s | kilo#1688 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.350 | 175.5 | 175.5 | 5,000 | 25.84 | 28.32 | 54.17 | 43.33 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| kilo#1688 | +544 | 364 | +175 | 54.17 | 0.00 | +54 | +593 |
| NPrightdolphin#NA1 | +597 | 549 | +43 | 31.27 | 34.67 | -3 | +589 |
| Jasmine#6767 | +497 | 556 | -90 | 95.87 | 6.98 | +89 | +555 |
| SirBanArthur#King6 | +369 | 110 | +257 | 43.33 | 0.00 | +43 | +410 |
| Sub asf#nuhh | +242 | 138 | +40 | 43.87 | 2.40 | +41 | +219 |
| ternstyle#GIGI | +123 | 188 | -20 | 23.52 | 35.10 | -12 | +156 |
| Deemo#Derf | -43 | 134 | -140 | 0.00 | 39.87 | -40 | -46 |
| Najumi#NPC | -77 | 88 | -130 | 0.00 | 36.83 | -37 | -79 |
| wqe#4119 | -112 | 50 | -150 | 0.00 | 7.06 | -7 | -107 |
| DoubleBl1nd#BEEF | -136 | 44 | -175 | 0.00 | 43.33 | -43 | -174 |

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
| event 487236 | 13.077s | ternstyle#GIGI -> Jasmine#6767 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 487240 | 29.878s | Najumi#NPC -> wqe#4119 | enemy | 5v4 | 130 | 1.000 | 130.0 | 6.5 | 3,900 | 20.16 | 0.00 | 20.16 | 6.05 (30%, absorbed) |
| event 487235 | 30.036s | SirBanArthur#King6 -> Najumi#NPC | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 1,950 | 10.08 | 0.00 | 10.08 | 3.02 (30%, absorbed) |
| event 487238 | 40.431s | Deemo#Derf -> SirBanArthur#King6 | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487241 | 105.211s | kilo#1688 -> Deemo#Derf | enemy | 2v4 | 130 | 1.000 | 130.0 | 13.0 | 2,150 | 11.11 | 0.00 | 11.11 | 3.33 (30%, absorbed) |
| event 487237 | 106.497s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 3v2 | 140 | 1.000 | 140.0 | 140.0 | 4,450 | 23.00 | 0.00 | 23.00 | 6.90 (30%, absorbed) |
| event 487239 | 106.824s | NPrightdolphin#NA1 -> kilo#1688 | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +846 | 556 | +290 | 46.26 | 0.00 | +46 | +892 |
| kilo#1688 | +374 | 341 | +60 | 11.11 | 7.13 | +4 | +405 |
| NPrightdolphin#NA1 | +216 | 146 | +70 | 23.78 | 0.00 | +24 | +240 |
| Deemo#Derf | +216 | 96 | +117 | 23.78 | 3.33 | +20 | +233 |
| Najumi#NPC | +208 | 186 | +10 | 20.16 | 3.02 | +17 | +213 |
| DoubleBl1nd#BEEF | +160 | 160 | +0 | 0.00 | 0.00 | +0 | +160 |
| wqe#4119 | +75 | 81 | -6 | 0.00 | 6.05 | -6 | +69 |
| SirBanArthur#King6 | +40 | 75 | -10 | 10.08 | 7.13 | +3 | +68 |
| Sub asf#nuhh | -140 | 0 | -140 | 0.00 | 6.90 | -7 | -147 |
| Jasmine#6767 | -150 | 0 | -150 | 0.00 | 6.98 | -7 | -157 |

## Round 12

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487243 | 10.889s | Deemo#Derf -> Sub asf#nuhh | combat | 5v5 | 150 | 1.000 | 150.0 | 7.5 | | | | | no economy this round |
| event 487247 | 11.213s | Jasmine#6767 -> Deemo#Derf | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 487248 | 14.320s | Jasmine#6767 -> DoubleBl1nd#BEEF | combat | 4v4 | 170 | 1.000 | 170.0 | 170.0 | | | | | no economy this round |
| event 487244 | 27.633s | Najumi#NPC -> SirBanArthur#King6 | combat | 3v4 | 160 | 1.000 | 160.0 | 160.0 | | | | | no economy this round |
| event 487245 | 28.382s | Najumi#NPC -> kilo#1688 | combat | 3v3 | 180 | 1.000 | 180.0 | 180.0 | | | | | no economy this round |
| event 487246 | 37.674s | Najumi#NPC -> Jasmine#6767 | combat | 3v2 | 140 | 1.020 | 142.8 | 142.8 | | | | | no economy this round |
| event 487242 | 41.400s | ternstyle#GIGI -> wqe#4119 | combat | 3v1 | 70 | 1.090 | 76.3 | 76.3 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,319 | 838 | +483 | 0.00 | 0.00 | +0 | +1,321 |
| Jasmine#6767 | +681 | 512 | +167 | 0.00 | 0.00 | +0 | +679 |
| Deemo#Derf | +334 | 324 | +10 | 0.00 | 0.00 | +0 | +334 |
| ternstyle#GIGI | +204 | 132 | +76 | 0.00 | 0.00 | +0 | +208 |
| Sub asf#nuhh | +92 | 100 | -8 | 0.00 | 0.00 | +0 | +92 |
| NPrightdolphin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| wqe#4119 | -72 | 0 | -76 | 0.00 | 0.00 | +0 | -76 |
| SirBanArthur#King6 | -98 | 62 | -160 | 0.00 | 0.00 | +0 | -98 |
| DoubleBl1nd#BEEF | -158 | 12 | -170 | 0.00 | 0.00 | +0 | -158 |
| kilo#1688 | -180 | 0 | -180 | 0.00 | 0.00 | +0 | -180 |

## Round 13

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487249 | 11.317s | ternstyle#GIGI -> Jasmine#6767 | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 487250 | 24.101s | ternstyle#GIGI -> SirBanArthur#King6 | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 487252 | 29.728s | NPrightdolphin#NA1 -> Sub asf#nuhh | combat | 5v3 | 90 | 1.000 | 90.0 | 90.0 | | | | | no economy this round |
| event 487256 | 51.577s | kilo#1688 -> NPrightdolphin#NA1 | combat | 2v5 | 70 | 1.077 | 75.4 | 75.4 | | | | | no economy this round |
| event 487254 | 52.908s | wqe#4119 -> DoubleBl1nd#BEEF | combat | 2v4 | 130 | 1.102 | 143.2 | 143.2 | | | | | no economy this round |
| event 487253 | 65.109s | Najumi#NPC -> kilo#1688 | combat | 3v2 | 140 | 1.332 | 186.5 | 18.6 | | | | | no economy this round |
| event 487255 | 67.046s | wqe#4119 -> Najumi#NPC | combat | 1v3 | 120 | 1.369 | 164.2 | 8.2 | | | | | no economy this round |
| event 487251 | 67.247s | ternstyle#GIGI -> wqe#4119 | combat | 2v1 | 130 | 1.372 | 178.4 | 178.4 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +1,149 | 640 | +458 | 0.00 | 0.00 | +0 | +1,098 |
| wqe#4119 | +608 | 500 | +129 | 0.00 | 0.00 | +0 | +629 |
| Najumi#NPC | +341 | 169 | +178 | 0.00 | 0.00 | +0 | +347 |
| kilo#1688 | +227 | 162 | +57 | 0.00 | 0.00 | +0 | +219 |
| Deemo#Derf | +169 | 169 | +0 | 0.00 | 0.00 | +0 | +169 |
| NPrightdolphin#NA1 | +28 | 6 | +15 | 0.00 | 0.00 | +0 | +21 |
| Sub asf#nuhh | -105 | 0 | -90 | 0.00 | 0.00 | +0 | -90 |
| SirBanArthur#King6 | -121 | 31 | -130 | 0.00 | 0.00 | +0 | -99 |
| DoubleBl1nd#BEEF | -145 | 0 | -143 | 0.00 | 0.00 | +0 | -143 |
| Jasmine#6767 | -175 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |

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

**TEAM_2** lost L=4,100; target H=14,550 (carryover targets); funding U=32,200; gap D=0; observed next-equipment gap G=1,050; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | 3,050 | 0 | 3,450 | 3,350 | 3,300 | 3,050 |
| NPrightdolphin#NA1 | 3,000 | 0 | 2,200 | 2,200 | 3,200 | 3,000 |
| ternstyle#GIGI | 4,100 | 4,100 | 4,000 | 4,000 | 2,200 | 3,900 |
| Deemo#Derf | 2,150 | 0 | 2,950 | 2,950 | 5,050 | 2,150 |
| Najumi#NPC | 2,450 | 0 | 2,200 | 2,200 | 4,950 | 2,450 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487260 | 40.511s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 250 | 1.29 | 0.00 | 1.29 | 0.39 (30%, absorbed) |
| event 487257 | 68.074s | ternstyle#GIGI -> wqe#4119 | enemy | 5v4 | 130 | 1.169 | 151.9 | 151.9 | 650 | 3.36 | 0.00 | 3.36 | 1.01 (30%, absorbed) |
| event 487261 | 72.217s | Najumi#NPC -> Jasmine#6767 | enemy | 5v3 | 90 | 1.247 | 112.2 | 112.2 | 200 | 1.03 | 0.00 | 1.03 | 0.31 (30%, absorbed) |
| event 487258 | 81.156s | ternstyle#GIGI -> Sub asf#nuhh | enemy | 5v2 | 50 | 1.415 | 70.8 | 70.8 | 1,000 | 5.17 | 0.00 | 5.17 | 1.55 (30%, absorbed) |
| event 487262 | 87.598s | kilo#1688 -> ternstyle#GIGI | enemy | 1v5 | 60 | 1.537 | 92.2 | 4.6 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 487259 | 87.896s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 4v1 | 50 | 1.543 | 77.1 | 77.1 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| ternstyle#GIGI | +608 | 438 | +218 | 8.53 | 6.36 | +2 | +658 |
| NPrightdolphin#NA1 | +294 | 156 | +150 | 1.29 | 0.00 | +1 | +307 |
| DoubleBl1nd#BEEF | +236 | 181 | +77 | 3.62 | 0.00 | +4 | +262 |
| kilo#1688 | +228 | 188 | +15 | 21.19 | 1.09 | +20 | +223 |
| Deemo#Derf | +192 | 192 | +0 | 0.00 | 0.00 | +0 | +192 |
| Najumi#NPC | +146 | 56 | +112 | 1.03 | 0.00 | +1 | +169 |
| Sub asf#nuhh | -54 | 0 | -71 | 0.00 | 1.55 | -2 | -73 |
| Jasmine#6767 | -79 | 11 | -112 | 0.00 | 0.31 | +0 | -101 |
| SirBanArthur#King6 | -106 | 32 | -150 | 0.00 | 0.39 | +0 | -118 |
| wqe#4119 | -121 | 0 | -152 | 0.00 | 1.01 | -1 | -153 |

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
| event 487267 | 2.796s | Sub asf#nuhh -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 2,200 | 11.37 | 0.00 | 11.37 | 3.41 (30%, absorbed) |
| event 487263 | 27.505s | ternstyle#GIGI -> wqe#4119 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,300 | 22.23 | 14.28 | 36.50 | 29.20 (80%, constrained) |
| event 487264 | 30.467s | DoubleBl1nd#BEEF -> Jasmine#6767 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,500 | 23.26 | 14.94 | 38.20 | 30.56 (80%, constrained) |
| event 487265 | 54.865s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 3,500 | 18.09 | 11.62 | 29.71 | 23.77 (80%, constrained) |
| event 487266 | 62.114s | DoubleBl1nd#BEEF -> SirBanArthur#King6 | enemy | 4v2 | 80 | 1.092 | 87.3 | 87.3 | 4,600 | 23.78 | 15.27 | 39.05 | 31.24 (80%, constrained) |
| event 487268 | 92.478s | Najumi#NPC -> kilo#1688 | enemy | 4v1 | 50 | 1.664 | 83.2 | 83.2 | 2,500 | 12.92 | 8.30 | 21.22 | 16.98 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +1,255 | 725 | +387 | 106.96 | 0.00 | +107 | +1,219 |
| Najumi#NPC | +318 | 240 | +83 | 21.22 | 0.00 | +21 | +344 |
| ternstyle#GIGI | +358 | 160 | +140 | 36.50 | 0.00 | +37 | +337 |
| Sub asf#nuhh | +193 | 259 | +20 | 11.37 | 23.77 | -12 | +267 |
| Deemo#Derf | +145 | 145 | +0 | 0.00 | 0.00 | +0 | +145 |
| kilo#1688 | -78 | 0 | -83 | 0.00 | 16.98 | -17 | -100 |
| SirBanArthur#King6 | -116 | 0 | -87 | 0.00 | 31.24 | -31 | -118 |
| wqe#4119 | -148 | 50 | -140 | 0.00 | 29.20 | -29 | -119 |
| NPrightdolphin#NA1 | -107 | 0 | -150 | 0.00 | 3.41 | -3 | -153 |
| Jasmine#6767 | -241 | 0 | -170 | 0.00 | 30.56 | -31 | -201 |

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
| event 487272 | 2.406s | Sub asf#nuhh -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 487269 | 4.379s | SirBanArthur#King6 -> NPrightdolphin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 6.5 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 487274 | 5.265s | Najumi#NPC -> SirBanArthur#King6 | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 487275 | 25.720s | Najumi#NPC -> wqe#4119 | enemy | 3v4 | 160 | 1.000 | 160.0 | 80.0 | 2,100 | 10.85 | 0.00 | 10.85 | 3.26 (30%, absorbed) |
| event 487276 | 28.009s | Najumi#NPC -> Jasmine#6767 | enemy | 3v3 | 180 | 1.000 | 180.0 | 30.6 | 3,200 | 16.54 | 0.00 | 16.54 | 4.96 (30%, absorbed) |
| event 487273 | 30.256s | Sub asf#nuhh -> Najumi#NPC | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 5,050 | 26.10 | 0.00 | 26.10 | 7.83 (30%, absorbed) |
| event 487271 | 47.728s | DoubleBl1nd#BEEF -> Sub asf#nuhh | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 3,500 | 18.09 | 0.00 | 18.09 | 5.43 (30%, absorbed) |
| event 487270 | 72.579s | ternstyle#GIGI -> kilo#1688 | enemy | 2v1 | 130 | 1.060 | 137.8 | 137.8 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +1,149 | 844 | +290 | 37.22 | 7.83 | +29 | +1,163 |
| Sub asf#nuhh | +539 | 472 | +120 | 47.29 | 5.43 | +42 | +634 |
| DoubleBl1nd#BEEF | +278 | 75 | +200 | 18.09 | 0.00 | +18 | +293 |
| ternstyle#GIGI | +247 | 125 | +138 | 3.62 | 0.00 | +4 | +267 |
| SirBanArthur#King6 | +197 | 188 | +10 | 22.23 | 2.95 | +19 | +217 |
| Jasmine#6767 | +79 | 110 | -31 | 0.00 | 4.96 | -5 | +74 |
| NPrightdolphin#NA1 | +43 | 49 | -6 | 0.00 | 6.67 | -7 | +36 |
| wqe#4119 | -78 | 0 | -80 | 0.00 | 3.26 | -3 | -83 |
| kilo#1688 | -122 | 0 | -138 | 0.00 | 1.09 | -1 | -139 |
| Deemo#Derf | -120 | 0 | -150 | 0.00 | 6.36 | -6 | -156 |

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
| event 487283 | 7.065s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 5v5 | 150 | 1.000 | 150.0 | 7.5 | 5,000 | 25.84 | 0.00 | 25.84 | 7.75 (30%, absorbed) |
| event 487277 | 7.846s | ternstyle#GIGI -> wqe#4119 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 487280 | 14.520s | Jasmine#6767 -> ternstyle#GIGI | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,900 | 25.33 | 0.00 | 25.33 | 7.60 (30%, absorbed) |
| event 487281 | 18.651s | Jasmine#6767 -> Najumi#NPC | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487282 | 20.475s | Jasmine#6767 -> Deemo#Derf | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 5,200 | 26.88 | 0.00 | 26.88 | 8.06 (30%, absorbed) |
| event 487279 | 23.281s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 1v4 | 70 | 1.000 | 70.0 | 3.5 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487278 | 24.060s | Sub asf#nuhh -> NPrightdolphin#NA1 | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Jasmine#6767 | +1,192 | 844 | +380 | 74.17 | 0.00 | +74 | +1,298 |
| wqe#4119 | +173 | 188 | +10 | 25.84 | 7.06 | +19 | +217 |
| NPrightdolphin#NA1 | +167 | 161 | +0 | 23.78 | 6.67 | +17 | +178 |
| SirBanArthur#King6 | +146 | 150 | -4 | 0.00 | 7.13 | -7 | +139 |
| Sub asf#nuhh | +102 | 38 | +70 | 22.23 | 0.00 | +22 | +130 |
| ternstyle#GIGI | +94 | 110 | -30 | 23.52 | 7.60 | +16 | +96 |
| DoubleBl1nd#BEEF | +44 | 50 | -8 | 0.00 | 7.75 | -8 | +34 |
| kilo#1688 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Deemo#Derf | -23 | 50 | -80 | 0.00 | 8.06 | -8 | -38 |
| Najumi#NPC | -78 | 41 | -130 | 0.00 | 6.59 | -7 | -96 |

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
| event 487290 | 9.262s | Jasmine#6767 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 25.5 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487286 | 11.686s | ternstyle#GIGI -> Jasmine#6767 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 487291 | 19.649s | Jasmine#6767 -> ternstyle#GIGI | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 7,000 | 36.18 | 0.00 | 36.18 | 10.85 (30%, absorbed) |
| event 487289 | 27.530s | Najumi#NPC -> Jasmine#6767 | enemy | 3v5 | 120 | 1.000 | 120.0 | 12.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 487292 | 27.822s | wqe#4119 -> NPrightdolphin#NA1 | enemy | 4v3 | 130 | 1.000 | 130.0 | 22.1 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 487284 | 29.361s | SirBanArthur#King6 -> Najumi#NPC | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487287 | 30.472s | DoubleBl1nd#BEEF -> wqe#4119 | enemy | 1v4 | 70 | 1.000 | 70.0 | 70.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 487288 | 35.060s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 1v3 | 120 | 1.000 | 120.0 | 120.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487285 | 58.305s | SirBanArthur#King6 -> DoubleBl1nd#BEEF | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 5,000 | 25.84 | 0.00 | 25.84 | 7.75 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +746 | 669 | +60 | 48.85 | 7.75 | +41 | +770 |
| SirBanArthur#King6 | +663 | 481 | +210 | 47.81 | 0.00 | +48 | +739 |
| Jasmine#6767 | +583 | 492 | +128 | 59.96 | 6.98 | +53 | +673 |
| wqe#4119 | +340 | 306 | +60 | 21.19 | 7.52 | +14 | +380 |
| Najumi#NPC | +263 | 212 | +40 | 0.00 | 6.59 | -7 | +245 |
| ternstyle#GIGI | +142 | 115 | +10 | 23.26 | 10.85 | +12 | +137 |
| Deemo#Derf | +128 | 150 | -26 | 0.00 | 7.13 | -7 | +117 |
| Sub asf#nuhh | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| NPrightdolphin#NA1 | -18 | 0 | -22 | 0.00 | 6.36 | -6 | -28 |
| kilo#1688 | -120 | 0 | -120 | 0.00 | 7.13 | -7 | -127 |

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
| event 487293 | 3.406s | DoubleBl1nd#BEEF -> kilo#1688 | enemy | 5v5 | 150 | 1.000 | 150.0 | 15.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487300 | 4.931s | wqe#4119 -> DoubleBl1nd#BEEF | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 5,000 | 25.84 | 0.00 | 25.84 | 7.75 (30%, absorbed) |
| event 487301 | 5.314s | wqe#4119 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487298 | 14.636s | Najumi#NPC -> wqe#4119 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 487294 | 31.538s | Sub asf#nuhh -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 180.0 | 9.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 487295 | 32.101s | NPrightdolphin#NA1 -> Sub asf#nuhh | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,450 | 23.00 | 0.00 | 23.00 | 6.90 (30%, absorbed) |
| event 487299 | 104.563s | Jasmine#6767 -> ternstyle#GIGI | enemy | 2v2 | 200 | 1.342 | 268.4 | 268.4 | 1,800 | 9.30 | 0.00 | 9.30 | 2.79 (30%, absorbed) |
| event 487296 | 107.212s | NPrightdolphin#NA1 -> SirBanArthur#King6 | enemy | 1v2 | 190 | 1.392 | 264.4 | 264.4 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 487297 | 123.544s | NPrightdolphin#NA1 -> Jasmine#6767 | enemy | 1v1 | 250 | 1.700 | 425.0 | 425.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,399 | 655 | +859 | 70.04 | 0.00 | +70 | +1,584 |
| wqe#4119 | +721 | 612 | +150 | 49.62 | 7.52 | +42 | +804 |
| Najumi#NPC | +427 | 275 | +151 | 25.07 | 6.59 | +18 | +444 |
| DoubleBl1nd#BEEF | +217 | 188 | +10 | 23.78 | 7.75 | +16 | +214 |
| Sub asf#nuhh | +110 | 138 | +10 | 21.97 | 6.90 | +15 | +163 |
| Jasmine#6767 | +33 | 188 | -157 | 9.30 | 6.98 | +2 | +33 |
| kilo#1688 | -15 | 0 | -15 | 0.00 | 7.13 | -7 | -22 |
| ternstyle#GIGI | -43 | 131 | -268 | 0.00 | 2.79 | -3 | -140 |
| Deemo#Derf | -147 | 0 | -170 | 0.00 | 7.13 | -7 | -177 |
| SirBanArthur#King6 | -200 | 31 | -264 | 0.00 | 7.13 | -7 | -240 |

## Round 20

Economy abstains: **final_round** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 487303 | 15.885s | Sub asf#nuhh -> Deemo#Derf | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 487306 | 28.670s | NPrightdolphin#NA1 -> Jasmine#6767 | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 487304 | 67.812s | Sub asf#nuhh -> ternstyle#GIGI | combat | 5v4 | 130 | 1.014 | 131.8 | 131.8 | | | | | no economy this round |
| event 487305 | 72.969s | Sub asf#nuhh -> Najumi#NPC | combat | 5v3 | 90 | 1.111 | 100.0 | 10.0 | | | | | no economy this round |
| event 487307 | 74.888s | NPrightdolphin#NA1 -> Sub asf#nuhh | combat | 2v5 | 70 | 1.147 | 80.3 | 80.3 | | | | | no economy this round |
| event 487308 | 77.454s | NPrightdolphin#NA1 -> wqe#4119 | combat | 2v4 | 130 | 1.196 | 155.5 | 155.5 | | | | | no economy this round |
| event 487302 | 80.575s | DoubleBl1nd#BEEF -> SirBanArthur#King6 | combat | 2v3 | 170 | 1.255 | 213.3 | 213.3 | | | | | no economy this round |
| event 487311 | 89.562s | kilo#1688 -> DoubleBl1nd#BEEF | combat | 2v2 | 200 | 1.424 | 284.8 | 284.8 | | | | | no economy this round |
| event 487309 | 99.831s | NPrightdolphin#NA1 -> Jasmine#6767 | combat | 1v2 | 190 | 1.618 | 307.4 | 307.4 | | | | | no economy this round |
| event 487310 | 101.546s | NPrightdolphin#NA1 -> kilo#1688 | combat | 1v1 | 250 | 1.650 | 412.6 | 412.6 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +2,213 | 1,281 | +1,096 | 0.00 | 0.00 | +0 | +2,377 |
| Sub asf#nuhh | +1,137 | 842 | +301 | 0.00 | 0.00 | +0 | +1,143 |
| DoubleBl1nd#BEEF | +232 | 294 | -72 | 0.00 | 0.00 | +0 | +222 |
| kilo#1688 | +91 | 188 | -128 | 0.00 | 0.00 | +0 | +60 |
| Najumi#NPC | +12 | 21 | -10 | 0.00 | 0.00 | +0 | +11 |
| wqe#4119 | -42 | 89 | -155 | 0.00 | 0.00 | +0 | -66 |
| Deemo#Derf | -81 | 69 | -150 | 0.00 | 0.00 | +0 | -81 |
| ternstyle#GIGI | -131 | 0 | -132 | 0.00 | 0.00 | +0 | -132 |
| SirBanArthur#King6 | -166 | 0 | -213 | 0.00 | 0.00 | +0 | -213 |
| Jasmine#6767 | -347 | 50 | -447 | 0.00 | 0.00 | +0 | -397 |

## Match totals

| Player | Before | A*damage | B*leverage | Gross econ credit | Gross econ debit | C*econ | Econ / played round | After |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +7,448 | 5,652 | +2,011 | 290.23 | 169.86 | +122 | +6.10 | +7,785 |
| Najumi#NPC | +5,740 | 4,510 | +1,249 | 184.71 | 185.76 | -4 | -0.20 | +5,755 |
| ternstyle#GIGI | +5,175 | 4,145 | +743 | 260.79 | 192.82 | +65 | +3.25 | +4,953 |
| DoubleBl1nd#BEEF | +4,694 | 3,770 | +722 | 329.03 | 190.92 | +139 | +6.95 | +4,631 |
| kilo#1688 | +4,140 | 3,818 | +256 | 392.71 | 106.35 | +286 | +14.30 | +4,360 |
| Jasmine#6767 | +3,587 | 4,306 | -537 | 446.70 | 126.66 | +318 | +15.90 | +4,087 |
| wqe#4119 | +3,457 | 3,797 | -226 | 461.71 | 129.24 | +334 | +16.70 | +3,905 |
| Deemo#Derf | +3,439 | 3,846 | -424 | 194.16 | 200.58 | -6 | -0.30 | +3,416 |
| Sub asf#nuhh | +2,451 | 2,665 | -4 | 194.03 | 108.01 | +86 | +4.30 | +2,747 |
| SirBanArthur#King6 | +1,916 | 2,889 | -784 | 238.97 | 110.96 | +129 | +6.45 | +2,234 |
