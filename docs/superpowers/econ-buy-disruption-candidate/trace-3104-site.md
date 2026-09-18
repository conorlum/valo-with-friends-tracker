# Per-kill trace: match 3104 Abyss 11-13

Candidate `impact-buy-disruption-30-80-rc2`, manifest LF-SHA-256 `ae043e361e3398ee578e82e9a393e63b8977d8a9ef4cad3894d357d5c8ebdae6`.

- **Before: `live_legacy`** -- enable_econ_component=False, econ_model=None, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF
- **After: `buy_disruption_v2_30_80`** -- enable_econ_component=True, econ_model=buy_disruption_v2_30_80, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF

impact = A*damage + B*leverage + C*econ with A=1.25, B=1.0, C=1.0; econ points = C * 1007.9209 * raw, rounded ONCE per player-round.
Kill credit = small equipment value + allocated buy-disruption value. Death debit = 30% of the victim's damage value when the team's funding absorbed the loss, 80% when its severity pool is positive (constrained next buy). Repeated deaths expose no new kit.

## Round 1

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484684 | 8.351s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 484687 | 11.352s | NPrightdolphin#NA1 -> VorteXx#Val | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 484688 | 21.333s | NPrightdolphin#NA1 -> Helpless#qiqi | combat | 4v4 | 170 | 1.125 | 191.3 | 191.3 | | | | | no economy this round |
| event 484685 | 23.201s | ZETA 3y5#213 -> ternstyle#GIGI | combat | 3v4 | 160 | 1.160 | 185.7 | 185.7 | | | | | no economy this round |
| event 484689 | 29.204s | NPrightdolphin#NA1 -> Mokalover67#ILLIT | combat | 3v3 | 180 | 1.274 | 229.3 | 229.3 | | | | | no economy this round |
| event 484690 | 37.207s | NPrightdolphin#NA1 -> ZETA 3y5#213 | combat | 3v2 | 140 | 1.425 | 199.5 | 199.5 | | | | | no economy this round |
| event 484686 | 46.635s | Najumi#NPC -> 1xgoofy#56719 | combat | 3v1 | 70 | 1.603 | 112.2 | 112.2 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,622 | 844 | +760 | 0.00 | 0.00 | +0 | +1,604 |
| Najumi#NPC | +318 | 222 | +112 | 0.00 | 0.00 | +0 | +334 |
| 1xgoofy#56719 | +251 | 172 | +38 | 0.00 | 0.00 | +0 | +210 |
| ZETA 3y5#213 | +137 | 125 | -14 | 0.00 | 0.00 | +0 | +111 |
| Osmin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| ternstyle#GIGI | -97 | 98 | -186 | 0.00 | 0.00 | +0 | -88 |
| Helpless#qiqi | -130 | 75 | -191 | 0.00 | 0.00 | +0 | -116 |
| VorteXx#Val | -163 | 0 | -140 | 0.00 | 0.00 | +0 | -140 |
| DoubleBl1nd#BEEF | -175 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |
| Mokalover67#ILLIT | -226 | 0 | -229 | 0.00 | 0.00 | +0 | -229 |

## Round 2

Pistol winner TEAM_2; round winner TEAM_2; half round 2.

**TEAM_1** lost L=3,200; target H=19,500; funding U=20,200; gap D=0; observed next-equipment gap G=1,950; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 600 | 600 | 4,650 | 4,500 | 200 | 3,900 |
| Helpless#qiqi | 650 | 650 | 4,800 | 4,550 | 300 | 3,900 |
| Mokalover67#ILLIT | 700 | 700 | 4,250 | 4,250 | 200 | 3,900 |
| 1xgoofy#56719 | 300 | 300 | 4,600 | 4,600 | 200 | 3,900 |
| VorteXx#Val | 950 | 950 | 1,950 | 1,950 | 1,750 | 3,900 |

**TEAM_2** lost L=0; target H=15,700 (carryover targets); funding U=31,000; gap D=0; observed next-equipment gap G=100; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,300 | 0 | 3,200 | 3,200 | 3,200 | 3,300 |
| DoubleBl1nd#BEEF | 2,650 | 0 | 2,900 | 2,650 | 3,350 | 2,650 |
| Osmin#NA1 | 3,000 | 0 | 4,100 | 3,850 | 2,150 | 3,000 |
| Najumi#NPC | 2,900 | 0 | 2,900 | 2,900 | 3,850 | 2,900 |
| ternstyle#GIGI | 3,850 | 0 | 4,350 | 4,200 | 2,850 | 3,850 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484693 | 11.437s | Osmin#NA1 -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 650 | 3.36 | 0.00 | 3.36 | 1.01 (30%, absorbed) |
| event 484694 | 12.609s | Osmin#NA1 -> 1xgoofy#56719 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 300 | 1.55 | 0.00 | 1.55 | 0.47 (30%, absorbed) |
| event 484691 | 16.011s | ternstyle#GIGI -> Mokalover67#ILLIT | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |
| event 484695 | 16.570s | Osmin#NA1 -> VorteXx#Val | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 950 | 4.91 | 0.00 | 4.91 | 1.47 (30%, absorbed) |
| event 484692 | 21.580s | ZETA 3y5#213 -> ZETA 3y5#213 | self | 1v1 | 40 | 1.000 | 0.0 | 40.0 | 600 | 0.00 | 0.00 | 0.00 | 0.93 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +977 | 688 | +330 | 9.82 | 0.00 | +10 | +1,028 |
| ternstyle#GIGI | +204 | 125 | +90 | 3.62 | 0.00 | +4 | +219 |
| NPrightdolphin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| DoubleBl1nd#BEEF | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Najumi#NPC | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| ZETA 3y5#213 | -39 | 0 | -40 | 0.00 | 0.93 | -1 | -41 |
| VorteXx#Val | -44 | 0 | -50 | 0.00 | 1.47 | -1 | -51 |
| Mokalover67#ILLIT | -79 | 0 | -90 | 0.00 | 1.09 | -1 | -91 |
| 1xgoofy#56719 | -82 | 32 | -130 | 0.00 | 0.47 | +0 | -98 |
| Helpless#qiqi | -99 | 32 | -150 | 0.00 | 1.01 | -1 | -119 |

## Round 3

Pistol winner TEAM_2; round winner TEAM_2; half round 3.

**TEAM_1** lost L=11,000; target H=19,500; funding U=24,250; gap D=0; observed next-equipment gap G=3,700; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 2,200 | 2,050 | 1,800 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 2,700 | 2,450 | 1,200 | 3,900 |
| Mokalover67#ILLIT | 4,250 | 0 | 4,600 | 4,600 | 2,100 | 3,900 |
| 1xgoofy#56719 | 4,600 | 0 | 4,600 | 4,600 | 1,900 | 3,900 |
| VorteXx#Val | 1,950 | 1,950 | 3,500 | 3,500 | 1,450 | 3,900 |

**TEAM_2** lost L=9,700; target H=19,500; funding U=34,900; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,200 | 3,200 | 4,000 | 4,000 | 1,900 | 3,900 |
| DoubleBl1nd#BEEF | 2,650 | 2,650 | 4,800 | 4,550 | 2,200 | 3,900 |
| Osmin#NA1 | 3,850 | 3,850 | 4,750 | 4,500 | 650 | 3,900 |
| Najumi#NPC | 2,900 | 0 | 4,850 | 4,850 | 5,750 | 3,900 |
| ternstyle#GIGI | 4,200 | 0 | 4,950 | 4,800 | 4,900 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484696 | 13.890s | ternstyle#GIGI -> VorteXx#Val | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,950 | 10.08 | 0.00 | 10.08 | 3.02 (30%, absorbed) |
| event 484700 | 16.329s | Helpless#qiqi -> DoubleBl1nd#BEEF | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 2,650 | 13.70 | 0.00 | 13.70 | 4.11 (30%, absorbed) |
| event 484701 | 24.396s | NPrightdolphin#NA1 -> Helpless#qiqi | enemy | 4v4 | 170 | 1.072 | 182.2 | 182.2 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484697 | 28.895s | ternstyle#GIGI -> ZETA 3y5#213 | enemy | 4v3 | 130 | 1.157 | 150.4 | 150.4 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484698 | 38.248s | Mokalover67#ILLIT -> Osmin#NA1 | enemy | 2v4 | 130 | 1.333 | 173.3 | 173.3 | 3,850 | 19.90 | 0.00 | 19.90 | 5.97 (30%, absorbed) |
| event 484699 | 51.176s | Mokalover67#ILLIT -> NPrightdolphin#NA1 | enemy | 2v3 | 170 | 0.500 | 85.0 | 85.0 | 3,200 | 16.54 | 0.00 | 16.54 | 4.96 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mokalover67#ILLIT | +829 | 600 | +258 | 36.44 | 0.00 | +36 | +894 |
| ternstyle#GIGI | +893 | 534 | +300 | 33.34 | 0.00 | +33 | +867 |
| NPrightdolphin#NA1 | +514 | 364 | +97 | 23.52 | 4.96 | +19 | +480 |
| Najumi#NPC | +170 | 170 | +0 | 0.00 | 0.00 | +0 | +170 |
| Helpless#qiqi | +10 | 156 | -42 | 13.70 | 7.06 | +7 | +121 |
| 1xgoofy#56719 | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| VorteXx#Val | -55 | 126 | -150 | 0.00 | 3.02 | -3 | -27 |
| DoubleBl1nd#BEEF | -110 | 0 | -140 | 0.00 | 4.11 | -4 | -144 |
| ZETA 3y5#213 | -178 | 0 | -150 | 0.00 | 6.98 | -7 | -157 |
| Osmin#NA1 | -123 | 0 | -173 | 0.00 | 5.97 | -6 | -179 |

## Round 4

Pistol winner TEAM_2; round winner TEAM_2; half round 4.

**TEAM_1** lost L=17,200; target H=19,500; funding U=22,900; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,050 | 2,050 | 4,650 | 4,500 | 950 | 3,900 |
| Helpless#qiqi | 2,450 | 2,450 | 4,800 | 4,550 | 450 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,600 | 4,600 | 700 | 3,900 |
| 1xgoofy#56719 | 4,600 | 4,600 | 4,600 | 4,600 | 900 | 3,900 |
| VorteXx#Val | 3,500 | 3,500 | 4,450 | 4,450 | 400 | 3,900 |

**TEAM_2** lost L=13,050; target H=19,500; funding U=36,000; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,000 | 4,000 | 4,100 | 4,100 | 3,400 | 3,900 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 5,100 | 4,850 | 4,050 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 2,200 | 3,900 |
| Najumi#NPC | 4,850 | 0 | 4,850 | 4,850 | 3,000 | 3,900 |
| ternstyle#GIGI | 4,800 | 0 | 5,450 | 5,300 | 3,850 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484706 | 19.788s | Helpless#qiqi -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,000 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |
| event 484702 | 28.417s | DoubleBl1nd#BEEF -> Helpless#qiqi | enemy | 4v5 | 140 | 1.069 | 149.7 | 149.7 | 2,450 | 12.66 | 0.00 | 12.66 | 3.80 (30%, absorbed) |
| event 484703 | 32.751s | DoubleBl1nd#BEEF -> Mokalover67#ILLIT | enemy | 4v4 | 170 | 1.151 | 195.7 | 195.7 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484709 | 38.012s | VorteXx#Val -> Osmin#NA1 | enemy | 3v4 | 160 | 1.250 | 200.0 | 10.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484704 | 38.791s | DoubleBl1nd#BEEF -> VorteXx#Val | enemy | 3v3 | 180 | 1.265 | 227.7 | 79.7 | 3,500 | 18.09 | 0.00 | 18.09 | 5.43 (30%, absorbed) |
| event 484705 | 42.237s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | enemy | 2v3 | 170 | 1.330 | 226.1 | 22.6 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484707 | 43.437s | Najumi#NPC -> 1xgoofy#56719 | enemy | 2v2 | 200 | 1.353 | 270.5 | 270.5 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484708 | 46.357s | Najumi#NPC -> ZETA 3y5#213 | enemy | 2v1 | 130 | 1.408 | 183.0 | 183.0 | 2,050 | 10.60 | 0.00 | 10.60 | 3.18 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +1,199 | 810 | +550 | 54.53 | 7.06 | +47 | +1,407 |
| Najumi#NPC | +686 | 384 | +454 | 34.37 | 0.00 | +34 | +872 |
| 1xgoofy#56719 | +368 | 369 | -44 | 23.52 | 7.13 | +16 | +341 |
| VorteXx#Val | +321 | 188 | +120 | 23.26 | 5.43 | +18 | +326 |
| Helpless#qiqi | +244 | 188 | +0 | 20.68 | 3.80 | +17 | +205 |
| NPrightdolphin#NA1 | +21 | 181 | -150 | 0.00 | 6.20 | -6 | +25 |
| ternstyle#GIGI | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Osmin#NA1 | -9 | 0 | -10 | 0.00 | 6.98 | -7 | -17 |
| ZETA 3y5#213 | -81 | 31 | -183 | 0.00 | 3.18 | -3 | -155 |
| Mokalover67#ILLIT | -150 | 0 | -196 | 0.00 | 7.13 | -7 | -203 |

## Round 5

Pistol winner TEAM_2; round winner TEAM_1; half round 5.

**TEAM_1** lost L=18,200; target H=19,500; funding U=24,500; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,650 | 4,500 | 4,500 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 4,200 | 3,950 | 200 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,250 | 4,250 | 100 | 3,900 |
| 1xgoofy#56719 | 4,600 | 4,600 | 4,600 | 4,600 | 0 | 3,900 |
| VorteXx#Val | 4,450 | 4,450 | 4,450 | 4,450 | 200 | 3,900 |

**TEAM_2** lost L=23,600; target H=19,500; funding U=24,600; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,300 | 4,300 | 1,400 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 4,800 | 4,550 | 2,050 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,500 | 4,250 | 200 | 3,900 |
| Najumi#NPC | 4,850 | 4,850 | 4,250 | 4,250 | 1,350 | 3,900 |
| ternstyle#GIGI | 5,300 | 5,300 | 6,250 | 6,100 | 100 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484714 | 13.973s | Osmin#NA1 -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 7.5 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484715 | 14.441s | VorteXx#Val -> Osmin#NA1 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484711 | 21.220s | Mokalover67#ILLIT -> Najumi#NPC | enemy | 4v4 | 170 | 1.000 | 170.0 | 17.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484710 | 22.867s | ternstyle#GIGI -> Mokalover67#ILLIT | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484716 | 25.243s | VorteXx#Val -> DoubleBl1nd#BEEF | enemy | 3v3 | 180 | 1.004 | 180.6 | 180.6 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484712 | 29.139s | ZETA 3y5#213 -> ternstyle#GIGI | enemy | 3v2 | 140 | 1.077 | 150.8 | 150.8 | 5,300 | 27.39 | 0.00 | 27.39 | 8.22 (30%, absorbed) |
| event 484717 | 36.959s | NPrightdolphin#NA1 -> VorteXx#Val | enemy | 1v3 | 120 | 1.225 | 146.9 | 110.2 | 4,450 | 23.00 | 0.00 | 23.00 | 6.90 (30%, absorbed) |
| event 484718 | 39.146s | NPrightdolphin#NA1 -> 1xgoofy#56719 | enemy | 1v2 | 190 | 1.266 | 240.5 | 84.2 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484713 | 42.189s | ZETA 3y5#213 -> NPrightdolphin#NA1 | enemy | 1v1 | 250 | 1.323 | 330.8 | 330.8 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| VorteXx#Val | +971 | 808 | +210 | 48.33 | 6.90 | +41 | +1,059 |
| ZETA 3y5#213 | +667 | 328 | +482 | 48.59 | 0.00 | +49 | +859 |
| NPrightdolphin#NA1 | +613 | 470 | +57 | 46.78 | 6.36 | +40 | +567 |
| Osmin#NA1 | +339 | 306 | +10 | 23.52 | 6.98 | +17 | +333 |
| Mokalover67#ILLIT | +138 | 156 | +10 | 25.07 | 7.13 | +18 | +184 |
| ternstyle#GIGI | +159 | 119 | +9 | 23.78 | 8.22 | +16 | +144 |
| Helpless#qiqi | +42 | 50 | -8 | 0.00 | 7.06 | -7 | +35 |
| Najumi#NPC | -14 | 0 | -17 | 0.00 | 7.52 | -8 | -25 |
| 1xgoofy#56719 | -78 | 0 | -84 | 0.00 | 7.13 | -7 | -91 |
| DoubleBl1nd#BEEF | -109 | 41 | -181 | 0.00 | 7.52 | -8 | -148 |

## Round 6

Pistol winner TEAM_2; round winner TEAM_2; half round 6.

**TEAM_1** lost L=21,750; target H=19,500; funding U=15,850; gap D=3,650; observed next-equipment gap G=9,800; activation 0.9359; severity pool 9,171.79 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,650 | 4,500 | 2,350 | 3,900 |
| Helpless#qiqi | 3,950 | 3,950 | 1,850 | 1,600 | 1,450 | 3,900 |
| Mokalover67#ILLIT | 4,250 | 4,250 | 1,900 | 1,900 | 0 | 3,900 |
| 1xgoofy#56719 | 4,600 | 4,600 | 1,100 | 1,100 | 1,300 | 3,900 |
| VorteXx#Val | 4,450 | 4,450 | 1,200 | 1,200 | 1,050 | 3,900 |

**TEAM_2** lost L=8,800; target H=19,500; funding U=31,000; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 4,300 | 4,300 | 4,000 | 3,900 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 4,800 | 4,550 | 650 | 3,900 |
| Osmin#NA1 | 4,250 | 0 | 4,750 | 4,500 | 3,750 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 350 | 3,900 |
| ternstyle#GIGI | 6,100 | 0 | 6,450 | 6,300 | 2,750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484722 | 14.943s | Osmin#NA1 -> ZETA 3y5#213 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,500 | 23.26 | 98.08 | 121.34 | 97.08 (80%, constrained) |
| event 484723 | 15.581s | Osmin#NA1 -> 1xgoofy#56719 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 100.26 | 124.04 | 99.23 (80%, constrained) |
| event 484724 | 15.855s | Osmin#NA1 -> Helpless#qiqi | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 3,950 | 20.42 | 86.10 | 106.51 | 85.21 (80%, constrained) |
| event 484719 | 28.759s | DoubleBl1nd#BEEF -> ZETA 3y5#213 | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (80%, constrained) |
| event 484720 | 46.955s | Mokalover67#ILLIT -> Najumi#NPC | enemy | 2v5 | 70 | 1.000 | 70.0 | 70.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484721 | 47.091s | Mokalover67#ILLIT -> DoubleBl1nd#BEEF | enemy | 2v4 | 130 | 1.000 | 130.0 | 130.0 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484725 | 77.337s | Osmin#NA1 -> VorteXx#Val | enemy | 3v2 | 140 | 1.000 | 140.0 | 140.0 | 4,450 | 23.00 | 96.99 | 120.00 | 96.00 (80%, constrained) |
| event 484726 | 81.148s | Osmin#NA1 -> Mokalover67#ILLIT | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 4,250 | 21.97 | 92.64 | 114.60 | 91.68 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +2,276 | 1,371 | +640 | 586.50 | 0.00 | +586 | +2,597 |
| Mokalover67#ILLIT | +570 | 469 | +130 | 45.49 | 91.68 | -46 | +553 |
| DoubleBl1nd#BEEF | +166 | 169 | -40 | 0.00 | 7.06 | -7 | +122 |
| NPrightdolphin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| ternstyle#GIGI | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Najumi#NPC | -8 | 62 | -70 | 0.00 | 6.59 | -7 | -15 |
| 1xgoofy#56719 | -112 | 100 | -150 | 0.00 | 99.23 | -99 | -149 |
| Helpless#qiqi | -184 | 0 | -130 | 0.00 | 85.21 | -85 | -215 |
| VorteXx#Val | -198 | 0 | -140 | 0.00 | 96.00 | -96 | -236 |
| ZETA 3y5#213 | -241 | 98 | -240 | 0.00 | 97.08 | -97 | -239 |

## Round 7

Pistol winner TEAM_2; round winner TEAM_2; half round 7.

**TEAM_1** lost L=10,300; target H=19,500; funding U=19,300; gap D=200; observed next-equipment gap G=3,450; activation 0.0513; severity pool 176.92 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,650 | 4,500 | 700 | 3,900 |
| Helpless#qiqi | 1,600 | 1,600 | 4,200 | 3,950 | 50 | 3,900 |
| Mokalover67#ILLIT | 1,900 | 1,900 | 1,900 | 1,900 | 800 | 3,900 |
| 1xgoofy#56719 | 1,100 | 1,100 | 2,500 | 2,500 | 1,700 | 3,900 |
| VorteXx#Val | 1,200 | 1,200 | 3,850 | 3,850 | 0 | 3,900 |

**TEAM_2** lost L=0; target H=19,500; funding U=42,650; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 5,100 | 5,100 | 6,000 | 3,900 |
| DoubleBl1nd#BEEF | 4,550 | 0 | 5,100 | 4,850 | 3,100 | 3,900 |
| Osmin#NA1 | 4,500 | 0 | 5,050 | 4,800 | 5,850 | 3,900 |
| Najumi#NPC | 4,250 | 0 | 5,050 | 5,050 | 3,550 | 3,900 |
| ternstyle#GIGI | 6,300 | 0 | 6,750 | 6,600 | 4,650 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484727 | 19.173s | ternstyle#GIGI -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,600 | 8.27 | 1.42 | 9.69 | 7.75 (80%, constrained) |
| event 484728 | 23.017s | DoubleBl1nd#BEEF -> VorteXx#Val | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 1,200 | 6.20 | 1.07 | 7.27 | 5.81 (80%, constrained) |
| event 484729 | 41.060s | Najumi#NPC -> Mokalover67#ILLIT | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 1,900 | 9.82 | 1.69 | 11.51 | 9.21 (80%, constrained) |
| event 484730 | 63.934s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 4,500 | 23.26 | 4.00 | 27.26 | 21.80 (80%, constrained) |
| event 484731 | 71.728s | NPrightdolphin#NA1 -> 1xgoofy#56719 | enemy | 5v1 | 40 | 1.000 | 40.0 | 40.0 | 1,100 | 5.69 | 0.98 | 6.66 | 5.33 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +632 | 531 | +90 | 33.92 | 0.00 | +34 | +655 |
| ternstyle#GIGI | +316 | 156 | +150 | 9.69 | 0.00 | +10 | +316 |
| DoubleBl1nd#BEEF | +295 | 156 | +130 | 7.27 | 0.00 | +7 | +293 |
| Najumi#NPC | +251 | 155 | +90 | 11.51 | 0.00 | +12 | +257 |
| 1xgoofy#56719 | +55 | 98 | -40 | 0.00 | 5.33 | -5 | +53 |
| Mokalover67#ILLIT | +4 | 100 | -90 | 0.00 | 9.21 | -9 | +1 |
| Osmin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| ZETA 3y5#213 | -59 | 0 | -50 | 0.00 | 21.80 | -22 | -72 |
| VorteXx#Val | -139 | 0 | -130 | 0.00 | 5.81 | -6 | -136 |
| Helpless#qiqi | -160 | 0 | -150 | 0.00 | 7.75 | -8 | -158 |

## Round 8

Pistol winner TEAM_2; round winner TEAM_1; half round 8.

**TEAM_1** lost L=8,450; target H=19,500; funding U=29,550; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,300 | 4,150 | 250 | 3,900 |
| Helpless#qiqi | 3,950 | 3,950 | 4,800 | 4,550 | 1,850 | 3,900 |
| Mokalover67#ILLIT | 1,900 | 0 | 5,400 | 5,400 | 3,700 | 3,900 |
| 1xgoofy#56719 | 2,500 | 0 | 5,400 | 5,400 | 2,100 | 3,900 |
| VorteXx#Val | 3,850 | 0 | 6,250 | 6,250 | 2,150 | 3,900 |

**TEAM_2** lost L=26,400; target H=19,500; funding U=30,100; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 5,100 | 5,100 | 4,100 | 4,100 | 3,600 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 800 | 3,900 |
| Osmin#NA1 | 4,800 | 4,800 | 4,750 | 4,500 | 3,600 | 3,900 |
| Najumi#NPC | 5,050 | 5,050 | 4,250 | 4,250 | 1,900 | 3,900 |
| ternstyle#GIGI | 6,600 | 6,600 | 6,450 | 6,300 | 700 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484732 | 10.369s | ternstyle#GIGI -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,950 | 20.42 | 0.00 | 20.42 | 6.13 (30%, absorbed) |
| event 484736 | 16.332s | ZETA 3y5#213 -> NPrightdolphin#NA1 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 5,100 | 26.36 | 0.00 | 26.36 | 7.91 (30%, absorbed) |
| event 484738 | 25.862s | VorteXx#Val -> DoubleBl1nd#BEEF | enemy | 4v4 | 170 | 1.054 | 179.2 | 179.2 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484733 | 25.990s | Mokalover67#ILLIT -> Najumi#NPC | enemy | 4v3 | 130 | 1.056 | 137.3 | 137.3 | 5,050 | 26.10 | 0.00 | 26.10 | 7.83 (30%, absorbed) |
| event 484734 | 31.537s | Mokalover67#ILLIT -> ternstyle#GIGI | enemy | 4v2 | 80 | 1.161 | 92.9 | 92.9 | 6,600 | 34.11 | 0.00 | 34.11 | 10.23 (30%, absorbed) |
| event 484737 | 34.224s | Osmin#NA1 -> ZETA 3y5#213 | enemy | 2v4 | 130 | 1.212 | 157.5 | 7.9 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484739 | 34.306s | VorteXx#Val -> Osmin#NA1 | enemy | 3v2 | 140 | 1.213 | 169.9 | 169.9 | 4,800 | 24.81 | 0.00 | 24.81 | 7.44 (30%, absorbed) |
| event 484735 | 37.469s | Mokalover67#ILLIT -> ternstyle#GIGI | enemy | 3v1 | 70 | 1.273 | 89.1 | 89.1 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mokalover67#ILLIT | +1,011 | 709 | +319 | 60.22 | 0.00 | +60 | +1,088 |
| VorteXx#Val | +942 | 634 | +349 | 49.88 | 0.00 | +50 | +1,033 |
| ZETA 3y5#213 | +302 | 188 | +132 | 26.36 | 6.98 | +19 | +339 |
| ternstyle#GIGI | +140 | 156 | -32 | 20.42 | 10.23 | +10 | +134 |
| Osmin#NA1 | +121 | 125 | -12 | 23.26 | 7.44 | +16 | +129 |
| 1xgoofy#56719 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| NPrightdolphin#NA1 | -23 | 98 | -140 | 0.00 | 7.91 | -8 | -50 |
| Najumi#NPC | -137 | 0 | -137 | 0.00 | 7.83 | -8 | -145 |
| Helpless#qiqi | -150 | 0 | -150 | 0.00 | 6.13 | -6 | -156 |
| DoubleBl1nd#BEEF | -165 | 0 | -179 | 0.00 | 7.52 | -8 | -187 |

## Round 9

Pistol winner TEAM_2; round winner TEAM_1; half round 9.

**TEAM_1** lost L=14,100; target H=19,500; funding U=32,750; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,300 | 4,150 | 2,450 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 4,800 | 4,550 | 700 | 3,900 |
| Mokalover67#ILLIT | 5,400 | 5,400 | 4,600 | 4,600 | 2,250 | 3,900 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 4,800 | 3,900 |
| VorteXx#Val | 6,250 | 0 | 6,100 | 6,100 | 3,050 | 3,900 |

**TEAM_2** lost L=24,000; target H=19,500; funding U=24,000; gap D=0; observed next-equipment gap G=3,800; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 1,900 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 2,400 | 2,150 | 1,200 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,400 | 4,150 | 2,200 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 3,150 | 3,150 | 1,850 | 3,900 |
| ternstyle#GIGI | 6,300 | 6,300 | 2,750 | 2,600 | 1,150 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484740 | 9.914s | ternstyle#GIGI -> Mokalover67#ILLIT | enemy | 5v5 | 150 | 1.000 | 150.0 | 52.5 | 5,400 | 27.91 | 0.00 | 27.91 | 8.37 (30%, absorbed) |
| event 484741 | 13.618s | Helpless#qiqi -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 150.0 | 75.0 | 6,300 | 32.56 | 0.00 | 32.56 | 9.77 (30%, absorbed) |
| event 484747 | 15.234s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 484743 | 15.498s | VorteXx#Val -> Najumi#NPC | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484748 | 17.855s | NPrightdolphin#NA1 -> Helpless#qiqi | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484744 | 21.699s | VorteXx#Val -> DoubleBl1nd#BEEF | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484742 | 71.848s | Osmin#NA1 -> Mokalover67#ILLIT | enemy | 2v3 | 170 | 1.000 | 170.0 | 59.5 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484745 | 75.627s | VorteXx#Val -> Osmin#NA1 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484746 | 79.563s | VorteXx#Val -> NPrightdolphin#NA1 | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| VorteXx#Val | +1,963 | 1,156 | +680 | 91.49 | 0.00 | +91 | +1,927 |
| NPrightdolphin#NA1 | +714 | 536 | +170 | 44.97 | 6.36 | +39 | +745 |
| ternstyle#GIGI | +248 | 188 | +75 | 27.91 | 9.77 | +18 | +281 |
| Helpless#qiqi | +195 | 188 | -10 | 32.56 | 7.06 | +26 | +204 |
| Osmin#NA1 | +65 | 135 | -30 | 0.00 | 6.98 | -7 | +98 |
| 1xgoofy#56719 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Mokalover67#ILLIT | -112 | 0 | -112 | 0.00 | 8.37 | -8 | -120 |
| ZETA 3y5#213 | -152 | 0 | -140 | 0.00 | 6.44 | -6 | -146 |
| Najumi#NPC | -204 | 0 | -170 | 0.00 | 6.59 | -7 | -177 |
| DoubleBl1nd#BEEF | -216 | 0 | -180 | 0.00 | 7.52 | -8 | -188 |

## Round 10

Pistol winner TEAM_2; round winner TEAM_1; half round 10.

**TEAM_1** lost L=14,800; target H=19,500; funding U=34,000; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 2,100 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 4,800 | 4,550 | 2,750 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 1,950 | 3,900 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 7,200 | 3,900 |
| VorteXx#Val | 6,100 | 6,100 | 6,250 | 6,250 | 500 | 3,900 |

**TEAM_2** lost L=16,150; target H=19,500; funding U=22,300; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 1,100 | 3,900 |
| DoubleBl1nd#BEEF | 2,150 | 2,150 | 4,450 | 4,200 | 50 | 3,900 |
| Osmin#NA1 | 4,150 | 4,150 | 4,750 | 4,500 | 750 | 3,900 |
| Najumi#NPC | 3,150 | 3,150 | 4,250 | 4,250 | 900 | 3,900 |
| ternstyle#GIGI | 2,600 | 2,600 | 4,650 | 4,500 | 0 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484754 | 16.982s | Najumi#NPC -> VorteXx#Val | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 6,100 | 31.53 | 0.00 | 31.53 | 9.46 (30%, absorbed) |
| event 484755 | 34.555s | NPrightdolphin#NA1 -> Helpless#qiqi | enemy | 5v4 | 130 | 1.044 | 135.7 | 135.7 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484749 | 35.648s | 1xgoofy#56719 -> Najumi#NPC | enemy | 3v5 | 120 | 1.064 | 127.7 | 127.7 | 3,150 | 16.28 | 0.00 | 16.28 | 4.88 (30%, absorbed) |
| event 484750 | 36.624s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | enemy | 3v4 | 160 | 1.083 | 173.2 | 173.2 | 2,150 | 11.11 | 0.00 | 11.11 | 3.33 (30%, absorbed) |
| event 484752 | 48.477s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 3v3 | 180 | 1.306 | 235.2 | 235.2 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 484753 | 49.165s | ZETA 3y5#213 -> ternstyle#GIGI | enemy | 3v2 | 140 | 1.319 | 184.7 | 184.7 | 2,600 | 13.44 | 0.00 | 13.44 | 4.03 (30%, absorbed) |
| event 484756 | 57.065s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 1v3 | 120 | 1.468 | 176.2 | 176.2 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 484757 | 63.055s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 1v3 | 120 | 1.582 | 189.8 | 9.5 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484751 | 63.381s | 1xgoofy#56719 -> NPrightdolphin#NA1 | enemy | 2v1 | 130 | 1.588 | 206.4 | 206.4 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1xgoofy#56719 | +1,277 | 874 | +507 | 48.59 | 0.00 | +49 | +1,430 |
| NPrightdolphin#NA1 | +1,242 | 1,006 | +295 | 44.97 | 6.36 | +39 | +1,340 |
| ZETA 3y5#213 | +703 | 500 | +234 | 34.89 | 6.44 | +28 | +762 |
| Najumi#NPC | +195 | 154 | +22 | 31.53 | 4.88 | +27 | +203 |
| Mokalover67#ILLIT | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Helpless#qiqi | -121 | 0 | -136 | 0.00 | 7.06 | -7 | -143 |
| DoubleBl1nd#BEEF | -119 | 28 | -173 | 0.00 | 3.33 | -3 | -148 |
| VorteXx#Val | -150 | 0 | -150 | 0.00 | 9.46 | -9 | -159 |
| ternstyle#GIGI | -139 | 0 | -185 | 0.00 | 4.03 | -4 | -189 |
| Osmin#NA1 | -198 | 0 | -235 | 0.00 | 6.44 | -6 | -241 |

## Round 11

Pistol winner TEAM_2; round winner TEAM_1; half round 11.

**TEAM_1** lost L=4,550; target H=19,500; funding U=40,100; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,650 | 4,500 | 4,250 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 5,100 | 4,850 | 2,000 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 4,950 | 3,900 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 5,400 | 3,900 |
| VorteXx#Val | 6,250 | 0 | 6,250 | 6,250 | 4,000 | 3,900 |

**TEAM_2** lost L=17,450; target H=19,500; funding U=20,700; gap D=0; observed next-equipment gap G=750; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 1,400 | 3,900 |
| DoubleBl1nd#BEEF | 4,200 | 4,200 | 4,100 | 3,850 | 50 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,100 | 3,850 | 150 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 250 | 3,900 |
| ternstyle#GIGI | 4,500 | 4,500 | 3,400 | 3,250 | 100 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484758 | 10.132s | Helpless#qiqi -> Najumi#NPC | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484759 | 11.048s | Helpless#qiqi -> DoubleBl1nd#BEEF | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,200 | 21.71 | 0.00 | 21.71 | 6.51 (30%, absorbed) |
| event 484760 | 18.720s | Helpless#qiqi -> ternstyle#GIGI | enemy | 5v3 | 90 | 1.000 | 90.0 | 67.5 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484761 | 24.669s | Osmin#NA1 -> Helpless#qiqi | enemy | 2v5 | 70 | 1.000 | 70.0 | 70.0 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484762 | 52.367s | VorteXx#Val -> Osmin#NA1 | enemy | 4v2 | 80 | 1.147 | 91.8 | 91.8 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helpless#qiqi | +1,285 | 812 | +300 | 66.94 | 7.06 | +60 | +1,172 |
| VorteXx#Val | +309 | 188 | +92 | 23.26 | 0.00 | +23 | +303 |
| Osmin#NA1 | +51 | 102 | -22 | 23.52 | 6.98 | +17 | +97 |
| ternstyle#GIGI | +47 | 146 | -68 | 0.00 | 6.98 | -7 | +71 |
| NPrightdolphin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| ZETA 3y5#213 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Mokalover67#ILLIT | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| 1xgoofy#56719 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| DoubleBl1nd#BEEF | -191 | 0 | -130 | 0.00 | 6.51 | -7 | -137 |
| Najumi#NPC | -220 | 0 | -150 | 0.00 | 6.59 | -7 | -157 |

## Round 12

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484770 | 9.637s | NPrightdolphin#NA1 -> Helpless#qiqi | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 484768 | 31.939s | Najumi#NPC -> VorteXx#Val | combat | 5v4 | 130 | 1.215 | 158.0 | 158.0 | | | | | no economy this round |
| event 484767 | 34.710s | ZETA 3y5#213 -> Osmin#NA1 | combat | 3v5 | 120 | 1.267 | 152.1 | 152.1 | | | | | no economy this round |
| event 484766 | 36.609s | 1xgoofy#56719 -> ternstyle#GIGI | combat | 3v4 | 160 | 1.303 | 208.5 | 208.5 | | | | | no economy this round |
| event 484763 | 45.724s | Mokalover67#ILLIT -> DoubleBl1nd#BEEF | combat | 3v3 | 180 | 1.475 | 265.5 | 265.5 | | | | | no economy this round |
| event 484771 | 47.736s | NPrightdolphin#NA1 -> 1xgoofy#56719 | combat | 2v3 | 170 | 1.513 | 257.2 | 257.2 | | | | | no economy this round |
| event 484769 | 48.243s | Najumi#NPC -> ZETA 3y5#213 | combat | 2v2 | 200 | 1.523 | 304.5 | 304.5 | | | | | no economy this round |
| event 484764 | 58.140s | Mokalover67#ILLIT -> Najumi#NPC | combat | 1v2 | 190 | 1.709 | 324.8 | 324.8 | | | | | no economy this round |
| event 484765 | 59.387s | Mokalover67#ILLIT -> NPrightdolphin#NA1 | combat | 1v1 | 250 | 0.500 | 125.0 | 125.0 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mokalover67#ILLIT | +1,434 | 811 | +715 | 0.00 | 0.00 | +0 | +1,526 |
| Najumi#NPC | +905 | 766 | +138 | 0.00 | 0.00 | +0 | +904 |
| NPrightdolphin#NA1 | +605 | 421 | +282 | 0.00 | 0.00 | +0 | +703 |
| 1xgoofy#56719 | +109 | 156 | -49 | 0.00 | 0.00 | +0 | +107 |
| ZETA 3y5#213 | +107 | 219 | -152 | 0.00 | 0.00 | +0 | +67 |
| Osmin#NA1 | -31 | 92 | -152 | 0.00 | 0.00 | +0 | -60 |
| Helpless#qiqi | -162 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |
| VorteXx#Val | -139 | 0 | -158 | 0.00 | 0.00 | +0 | -158 |
| ternstyle#GIGI | -166 | 0 | -209 | 0.00 | 0.00 | +0 | -209 |
| DoubleBl1nd#BEEF | -197 | 0 | -266 | 0.00 | 0.00 | +0 | -266 |

## Round 13

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484775 | 6.948s | Helpless#qiqi -> Osmin#NA1 | combat | 5v5 | 150 | 1.000 | 150.0 | 52.5 | | | | | no economy this round |
| event 484772 | 10.444s | ternstyle#GIGI -> Helpless#qiqi | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 484776 | 40.922s | Najumi#NPC -> Mokalover67#ILLIT | combat | 4v4 | 170 | 1.000 | 170.0 | 170.0 | | | | | no economy this round |
| event 484778 | 46.612s | VorteXx#Val -> ternstyle#GIGI | combat | 3v4 | 160 | 1.000 | 160.0 | 160.0 | | | | | no economy this round |
| event 484779 | 49.835s | NPrightdolphin#NA1 -> NPrightdolphin#NA1 | self | 3v3 | 180 | 1.000 | 0.0 | 180.0 | | | | | no economy this round |
| event 484773 | 52.013s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | combat | 3v2 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 484777 | 66.036s | Najumi#NPC -> ZETA 3y5#213 | combat | 1v3 | 120 | 1.000 | 120.0 | 120.0 | | | | | no economy this round |
| event 484774 | 83.877s | 1xgoofy#56719 -> Najumi#NPC | combat | 2v1 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +792 | 606 | +160 | 0.00 | 0.00 | +0 | +766 |
| 1xgoofy#56719 | +784 | 469 | +270 | 0.00 | 0.00 | +0 | +739 |
| VorteXx#Val | +312 | 125 | +160 | 0.00 | 0.00 | +0 | +285 |
| Helpless#qiqi | +154 | 125 | +10 | 0.00 | 0.00 | +0 | +135 |
| ternstyle#GIGI | +25 | 49 | -20 | 0.00 | 0.00 | +0 | +29 |
| Osmin#NA1 | +2 | 69 | -52 | 0.00 | 0.00 | +0 | +17 |
| DoubleBl1nd#BEEF | -88 | 75 | -140 | 0.00 | 0.00 | +0 | -65 |
| ZETA 3y5#213 | -140 | 0 | -120 | 0.00 | 0.00 | +0 | -120 |
| Mokalover67#ILLIT | -198 | 0 | -170 | 0.00 | 0.00 | +0 | -170 |
| NPrightdolphin#NA1 | -159 | 0 | -180 | 0.00 | 0.00 | +0 | -180 |

## Round 14

Pistol winner TEAM_1; round winner TEAM_1; half round 2.

**TEAM_1** lost L=4,550; target H=10,600 (carryover targets); funding U=20,250; gap D=0; observed next-equipment gap G=600; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 0 | 2,250 | 2,100 | 3,300 | 2,100 |
| Helpless#qiqi | 1,050 | 1,050 | 4,450 | 4,200 | 1,400 | 1,050 |
| Mokalover67#ILLIT | 2,700 | 2,700 | 3,350 | 3,350 | 100 | 2,700 |
| 1xgoofy#56719 | 800 | 800 | 4,600 | 4,600 | 1,800 | 1,000 |
| VorteXx#Val | 3,750 | 0 | 3,150 | 3,150 | 3,650 | 3,750 |

**TEAM_2** lost L=2,650; target H=19,500; funding U=21,350; gap D=0; observed next-equipment gap G=350; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 600 | 600 | 4,300 | 4,300 | 400 | 3,900 |
| DoubleBl1nd#BEEF | 0 | 0 | 4,800 | 4,550 | 50 | 3,900 |
| Osmin#NA1 | 200 | 200 | 4,500 | 4,250 | 150 | 3,900 |
| Najumi#NPC | 700 | 700 | 4,250 | 4,250 | 1,100 | 3,900 |
| ternstyle#GIGI | 1,150 | 1,150 | 3,700 | 3,550 | 500 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484780 | 17.301s | ternstyle#GIGI -> 1xgoofy#56719 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 800 | 4.14 | 0.00 | 4.14 | 1.24 (30%, absorbed) |
| event 484784 | 27.210s | Najumi#NPC -> Mokalover67#ILLIT | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 2,700 | 13.96 | 0.00 | 13.96 | 4.19 (30%, absorbed) |
| event 484781 | 28.428s | ZETA 3y5#213 -> DoubleBl1nd#BEEF | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484783 | 39.175s | Helpless#qiqi -> ternstyle#GIGI | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 1,150 | 5.94 | 0.00 | 5.94 | 1.78 (30%, absorbed) |
| event 484785 | 51.248s | VorteXx#Val -> Osmin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 200 | 1.03 | 0.00 | 1.03 | 0.31 (30%, absorbed) |
| event 484787 | 55.669s | NPrightdolphin#NA1 -> Helpless#qiqi | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 1,050 | 5.43 | 0.00 | 5.43 | 1.63 (30%, absorbed) |
| event 484786 | 82.611s | VorteXx#Val -> Najumi#NPC | enemy | 2v2 | 200 | 1.097 | 219.5 | 219.5 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |
| event 484782 | 84.352s | ZETA 3y5#213 -> NPrightdolphin#NA1 | enemy | 2v1 | 130 | 1.130 | 146.9 | 146.9 | 600 | 3.10 | 0.00 | 3.10 | 0.93 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| VorteXx#Val | +844 | 505 | +399 | 4.65 | 0.00 | +5 | +909 |
| ZETA 3y5#213 | +511 | 276 | +267 | 3.10 | 0.00 | +3 | +546 |
| ternstyle#GIGI | +256 | 251 | -10 | 4.14 | 1.78 | +2 | +243 |
| NPrightdolphin#NA1 | +219 | 124 | +23 | 5.43 | 0.93 | +4 | +151 |
| Helpless#qiqi | +65 | 125 | -10 | 5.94 | 1.63 | +4 | +119 |
| Najumi#NPC | +143 | 156 | -89 | 13.96 | 1.09 | +13 | +80 |
| Osmin#NA1 | +54 | 212 | -180 | 0.00 | 0.31 | +0 | +32 |
| Mokalover67#ILLIT | -69 | 99 | -130 | 0.00 | 4.19 | -4 | -35 |
| DoubleBl1nd#BEEF | -45 | 65 | -120 | 0.00 | 0.00 | +0 | -55 |
| 1xgoofy#56719 | -165 | 0 | -150 | 0.00 | 1.24 | -1 | -151 |

## Round 15

Pistol winner TEAM_1; round winner TEAM_2; half round 3.

**TEAM_1** lost L=17,400; target H=19,500; funding U=21,050; gap D=0; observed next-equipment gap G=3,600; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 2,100 | 4,650 | 4,500 | 1,150 | 3,900 |
| Helpless#qiqi | 4,200 | 4,200 | 3,950 | 3,700 | 0 | 3,900 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 1,900 | 1,900 | 800 | 3,900 |
| 1xgoofy#56719 | 4,600 | 4,600 | 2,500 | 2,500 | 1,700 | 3,900 |
| VorteXx#Val | 3,150 | 3,150 | 4,450 | 4,450 | 1,500 | 3,900 |

**TEAM_2** lost L=0; target H=19,500; funding U=36,050; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 4,300 | 4,300 | 3,700 | 3,900 |
| DoubleBl1nd#BEEF | 4,550 | 0 | 4,800 | 4,550 | 2,150 | 3,900 |
| Osmin#NA1 | 4,250 | 0 | 4,750 | 4,500 | 3,400 | 3,900 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 3,950 | 3,900 |
| ternstyle#GIGI | 3,550 | 0 | 4,150 | 4,000 | 3,350 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484790 | 19.315s | NPrightdolphin#NA1 -> 1xgoofy#56719 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484791 | 20.069s | NPrightdolphin#NA1 -> Mokalover67#ILLIT | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 3,350 | 17.32 | 0.00 | 17.32 | 5.19 (30%, absorbed) |
| event 484788 | 23.760s | Osmin#NA1 -> Helpless#qiqi | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,200 | 21.71 | 0.00 | 21.71 | 6.51 (30%, absorbed) |
| event 484792 | 27.304s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 2,100 | 10.85 | 0.00 | 10.85 | 3.26 (30%, absorbed) |
| event 484789 | 82.311s | Osmin#NA1 -> VorteXx#Val | enemy | 5v1 | 40 | 1.614 | 64.6 | 64.6 | 3,150 | 16.28 | 0.00 | 16.28 | 4.88 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,182 | 819 | +330 | 51.95 | 0.00 | +52 | +1,201 |
| Osmin#NA1 | +521 | 369 | +155 | 37.99 | 0.00 | +38 | +562 |
| DoubleBl1nd#BEEF | +154 | 154 | +0 | 0.00 | 0.00 | +0 | +154 |
| Najumi#NPC | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| ternstyle#GIGI | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Helpless#qiqi | -27 | 76 | -90 | 0.00 | 6.51 | -7 | -21 |
| ZETA 3y5#213 | -52 | 0 | -50 | 0.00 | 3.26 | -3 | -53 |
| VorteXx#Val | -49 | 0 | -65 | 0.00 | 4.88 | -5 | -70 |
| Mokalover67#ILLIT | -140 | 0 | -130 | 0.00 | 5.19 | -5 | -135 |
| 1xgoofy#56719 | -172 | 0 | -150 | 0.00 | 7.13 | -7 | -157 |

## Round 16

Pistol winner TEAM_1; round winner TEAM_2; half round 4.

**TEAM_1** lost L=17,050; target H=19,500; funding U=17,950; gap D=1,550; observed next-equipment gap G=3,150; activation 0.3974; severity pool 1,251.92 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 3,900 | 3,750 | 50 | 3,900 |
| Helpless#qiqi | 3,700 | 3,700 | 1,700 | 1,450 | 1,100 | 3,900 |
| Mokalover67#ILLIT | 1,900 | 1,900 | 3,350 | 3,350 | 150 | 3,900 |
| 1xgoofy#56719 | 2,500 | 2,500 | 4,400 | 4,400 | 100 | 3,900 |
| VorteXx#Val | 4,450 | 4,450 | 4,450 | 4,450 | 200 | 3,900 |

**TEAM_2** lost L=12,850; target H=19,500; funding U=38,850; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 2,900 | 3,900 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 5,100 | 4,850 | 3,850 | 3,900 |
| Osmin#NA1 | 4,500 | 0 | 4,750 | 4,500 | 5,700 | 3,900 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 4,300 | 3,900 |
| ternstyle#GIGI | 4,000 | 4,000 | 4,650 | 4,500 | 2,600 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484795 | 7.647s | Helpless#qiqi -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,000 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |
| event 484797 | 17.617s | Osmin#NA1 -> Helpless#qiqi | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 3,700 | 19.12 | 14.04 | 33.17 | 26.53 (80%, constrained) |
| event 484800 | 32.447s | VorteXx#Val -> NPrightdolphin#NA1 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 484793 | 39.235s | DoubleBl1nd#BEEF -> VorteXx#Val | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,450 | 23.00 | 16.89 | 39.89 | 31.91 (80%, constrained) |
| event 484798 | 52.075s | Osmin#NA1 -> Mokalover67#ILLIT | enemy | 3v3 | 180 | 1.175 | 211.5 | 211.5 | 1,900 | 9.82 | 7.21 | 17.03 | 13.63 (80%, constrained) |
| event 484794 | 62.711s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | enemy | 2v3 | 170 | 1.376 | 233.9 | 233.9 | 4,550 | 23.52 | 0.00 | 23.52 | 7.06 (30%, absorbed) |
| event 484796 | 64.818s | Najumi#NPC -> ZETA 3y5#213 | enemy | 2v2 | 200 | 1.415 | 283.1 | 283.1 | 4,500 | 23.26 | 17.08 | 40.34 | 32.27 (80%, constrained) |
| event 484799 | 74.670s | Osmin#NA1 -> 1xgoofy#56719 | enemy | 2v1 | 130 | 1.601 | 208.2 | 208.2 | 2,500 | 12.92 | 9.49 | 22.41 | 17.93 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +1,230 | 675 | +560 | 72.61 | 0.00 | +73 | +1,308 |
| Najumi#NPC | +488 | 210 | +283 | 40.34 | 0.00 | +40 | +533 |
| DoubleBl1nd#BEEF | +343 | 334 | -74 | 39.89 | 7.06 | +33 | +293 |
| VorteXx#Val | +228 | 286 | +10 | 22.23 | 31.91 | -10 | +286 |
| 1xgoofy#56719 | +179 | 162 | +26 | 23.52 | 17.93 | +6 | +194 |
| Helpless#qiqi | +147 | 188 | +10 | 20.68 | 26.53 | -6 | +192 |
| NPrightdolphin#NA1 | -63 | 79 | -170 | 0.00 | 6.67 | -7 | -98 |
| Mokalover67#ILLIT | -103 | 112 | -211 | 0.00 | 13.63 | -14 | -113 |
| ternstyle#GIGI | -125 | 0 | -150 | 0.00 | 6.20 | -6 | -156 |
| ZETA 3y5#213 | -132 | 146 | -283 | 0.00 | 32.27 | -32 | -169 |

## Round 17

Pistol winner TEAM_1; round winner TEAM_1; half round 5.

**TEAM_1** lost L=12,200; target H=19,500; funding U=23,700; gap D=0; observed next-equipment gap G=2,950; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 3,750 | 0 | 4,650 | 4,500 | 2,100 | 3,900 |
| Helpless#qiqi | 1,450 | 0 | 5,600 | 5,350 | 350 | 3,900 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 4,600 | 4,600 | 2,000 | 3,900 |
| 1xgoofy#56719 | 4,400 | 4,400 | 2,500 | 2,500 | 1,100 | 3,900 |
| VorteXx#Val | 4,450 | 4,450 | 2,350 | 2,350 | 1,600 | 3,900 |

**TEAM_2** lost L=22,400; target H=19,500; funding U=28,950; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 900 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 1,350 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 3,950 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 2,500 | 3,900 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484803 | 10.907s | Helpless#qiqi -> ternstyle#GIGI | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484801 | 12.647s | DoubleBl1nd#BEEF -> 1xgoofy#56719 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 484806 | 17.938s | Osmin#NA1 -> Mokalover67#ILLIT | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 3,350 | 17.32 | 0.00 | 17.32 | 5.19 (30%, absorbed) |
| event 484804 | 26.234s | Helpless#qiqi -> DoubleBl1nd#BEEF | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484808 | 32.021s | VorteXx#Val -> Najumi#NPC | enemy | 3v3 | 180 | 1.048 | 188.6 | 94.3 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484805 | 35.800s | Helpless#qiqi -> NPrightdolphin#NA1 | enemy | 3v2 | 140 | 1.119 | 156.7 | 156.7 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 484807 | 36.670s | Osmin#NA1 -> VorteXx#Val | enemy | 1v3 | 120 | 1.136 | 136.3 | 136.3 | 4,450 | 23.00 | 0.00 | 23.00 | 6.90 (30%, absorbed) |
| event 484802 | 43.036s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 2v1 | 130 | 1.256 | 163.3 | 163.3 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Helpless#qiqi | +1,412 | 956 | +467 | 70.55 | 0.00 | +71 | +1,494 |
| Osmin#NA1 | +755 | 601 | +143 | 40.32 | 6.98 | +33 | +777 |
| VorteXx#Val | +233 | 205 | +52 | 21.97 | 6.90 | +15 | +272 |
| ZETA 3y5#213 | +186 | 56 | +163 | 23.26 | 0.00 | +23 | +242 |
| DoubleBl1nd#BEEF | +166 | 186 | -20 | 22.74 | 7.52 | +15 | +181 |
| 1xgoofy#56719 | -46 | 94 | -140 | 0.00 | 6.82 | -7 | -53 |
| Najumi#NPC | -76 | 0 | -94 | 0.00 | 6.59 | -7 | -101 |
| ternstyle#GIGI | -101 | 49 | -150 | 0.00 | 6.98 | -7 | -108 |
| NPrightdolphin#NA1 | -146 | 0 | -157 | 0.00 | 6.67 | -7 | -164 |
| Mokalover67#ILLIT | -159 | 0 | -170 | 0.00 | 5.19 | -5 | -175 |

## Round 18

Pistol winner TEAM_1; round winner TEAM_1; half round 6.

**TEAM_1** lost L=12,350; target H=19,500; funding U=29,850; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,300 | 4,150 | 1,800 | 3,900 |
| Helpless#qiqi | 5,350 | 5,350 | 4,200 | 3,950 | 50 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 3,500 | 3,900 |
| 1xgoofy#56719 | 2,500 | 2,500 | 4,250 | 4,250 | 150 | 3,900 |
| VorteXx#Val | 2,350 | 0 | 5,250 | 5,250 | 4,850 | 3,900 |

**TEAM_2** lost L=22,400; target H=19,500; funding U=22,450; gap D=0; observed next-equipment gap G=3,700; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 2,100 | 2,100 | 1,500 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 3,550 | 3,300 | 600 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 2,050 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,750 | 3,900 |
| ternstyle#GIGI | 4,500 | 4,500 | 2,750 | 2,600 | 750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484812 | 4.153s | Najumi#NPC -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 5,350 | 27.65 | 0.00 | 27.65 | 8.30 (30%, absorbed) |
| event 484810 | 48.552s | ZETA 3y5#213 -> DoubleBl1nd#BEEF | enemy | 4v5 | 140 | 1.000 | 140.0 | 49.0 | 4,850 | 25.07 | 0.00 | 25.07 | 7.52 (30%, absorbed) |
| event 484811 | 52.281s | ZETA 3y5#213 -> NPrightdolphin#NA1 | enemy | 4v4 | 170 | 1.000 | 170.0 | 8.5 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 484815 | 52.440s | Osmin#NA1 -> ZETA 3y5#213 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484816 | 60.093s | VorteXx#Val -> ternstyle#GIGI | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484809 | 62.816s | Mokalover67#ILLIT -> Osmin#NA1 | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484813 | 65.277s | Najumi#NPC -> ZETA 3y5#213 | enemy | 1v4 | 70 | 1.000 | 70.0 | 70.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484814 | 67.023s | Najumi#NPC -> 1xgoofy#56719 | enemy | 1v3 | 120 | 1.000 | 120.0 | 120.0 | 2,500 | 12.92 | 0.00 | 12.92 | 3.88 (30%, absorbed) |
| event 484817 | 73.201s | VorteXx#Val -> Najumi#NPC | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +901 | 761 | +210 | 40.58 | 6.59 | +34 | +1,005 |
| ZETA 3y5#213 | +725 | 561 | +80 | 47.29 | 6.98 | +40 | +681 |
| VorteXx#Val | +686 | 312 | +260 | 45.23 | 0.00 | +45 | +617 |
| Mokalover67#ILLIT | +313 | 211 | +80 | 23.26 | 0.00 | +23 | +314 |
| Osmin#NA1 | +208 | 150 | +80 | 23.26 | 6.98 | +16 | +246 |
| NPrightdolphin#NA1 | +135 | 146 | -8 | 0.00 | 6.67 | -7 | +131 |
| 1xgoofy#56719 | +143 | 250 | -120 | 0.00 | 3.88 | -4 | +126 |
| DoubleBl1nd#BEEF | -62 | 0 | -49 | 0.00 | 7.52 | -8 | -57 |
| ternstyle#GIGI | -166 | 21 | -130 | 0.00 | 6.98 | -7 | -116 |
| Helpless#qiqi | -150 | 0 | -150 | 0.00 | 8.30 | -8 | -158 |

## Round 19

Pistol winner TEAM_1; round winner TEAM_2; half round 7.

**TEAM_1** lost L=22,200; target H=19,500; funding U=20,300; gap D=0; observed next-equipment gap G=2,200; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 50 | 3,900 |
| Helpless#qiqi | 3,950 | 3,950 | 4,800 | 4,550 | 950 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,600 | 4,600 | 1,100 | 3,900 |
| 1xgoofy#56719 | 4,250 | 4,250 | 1,700 | 1,700 | 750 | 3,900 |
| VorteXx#Val | 5,250 | 5,250 | 4,450 | 4,450 | 150 | 3,900 |

**TEAM_2** lost L=12,500; target H=19,500; funding U=26,200; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 2,100 | 2,100 | 4,300 | 4,300 | 900 | 3,900 |
| DoubleBl1nd#BEEF | 3,300 | 3,300 | 4,550 | 4,300 | 150 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 850 | 3,900 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 4,800 | 3,900 |
| ternstyle#GIGI | 2,600 | 2,600 | 4,200 | 4,050 | 0 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484818 | 14.985s | DoubleBl1nd#BEEF -> Helpless#qiqi | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,950 | 20.42 | 0.00 | 20.42 | 6.13 (30%, absorbed) |
| event 484819 | 18.942s | DoubleBl1nd#BEEF -> 1xgoofy#56719 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484825 | 26.289s | VorteXx#Val -> NPrightdolphin#NA1 | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 2,100 | 10.85 | 0.00 | 10.85 | 3.26 (30%, absorbed) |
| event 484824 | 32.566s | Najumi#NPC -> Mokalover67#ILLIT | enemy | 4v3 | 130 | 1.080 | 140.4 | 140.4 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 484826 | 39.575s | VorteXx#Val -> ternstyle#GIGI | enemy | 2v4 | 130 | 1.212 | 157.5 | 157.5 | 2,600 | 13.44 | 0.00 | 13.44 | 4.03 (30%, absorbed) |
| event 484821 | 39.607s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 2v3 | 170 | 1.213 | 206.1 | 206.1 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484820 | 49.640s | DoubleBl1nd#BEEF -> VorteXx#Val | enemy | 2v2 | 200 | 1.402 | 280.4 | 280.4 | 5,250 | 27.14 | 0.00 | 27.14 | 8.14 (30%, absorbed) |
| event 484822 | 56.576s | ZETA 3y5#213 -> DoubleBl1nd#BEEF | enemy | 1v2 | 190 | 1.533 | 291.2 | 291.2 | 3,300 | 17.06 | 0.00 | 17.06 | 5.12 (30%, absorbed) |
| event 484823 | 74.476s | ZETA 3y5#213 -> ZETA 3y5#213 | self | 1v1 | 250 | 1.000 | 0.0 | 125.0 | 4,150 | 0.00 | 0.00 | 0.00 | 6.44 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +1,118 | 782 | +269 | 69.52 | 5.12 | +64 | +1,115 |
| ZETA 3y5#213 | +795 | 602 | +372 | 40.32 | 6.44 | +34 | +1,008 |
| VorteXx#Val | +600 | 612 | -3 | 24.29 | 8.14 | +16 | +625 |
| Najumi#NPC | +233 | 100 | +140 | 23.78 | 0.00 | +24 | +264 |
| ternstyle#GIGI | -10 | 115 | -158 | 0.00 | 4.03 | -4 | -47 |
| NPrightdolphin#NA1 | -107 | 0 | -120 | 0.00 | 3.26 | -3 | -123 |
| 1xgoofy#56719 | -141 | 0 | -130 | 0.00 | 6.59 | -7 | -137 |
| Mokalover67#ILLIT | -133 | 0 | -140 | 0.00 | 7.13 | -7 | -147 |
| Helpless#qiqi | -162 | 0 | -150 | 0.00 | 6.13 | -6 | -156 |
| Osmin#NA1 | -182 | 0 | -206 | 0.00 | 6.98 | -7 | -213 |

## Round 20

Pistol winner TEAM_1; round winner TEAM_2; half round 8.

**TEAM_1** lost L=19,800; target H=19,500; funding U=17,400; gap D=2,100; observed next-equipment gap G=5,800; activation 0.5385; severity pool 3,123.08 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 2,250 | 2,100 | 1,200 | 3,900 |
| Helpless#qiqi | 4,550 | 4,550 | 2,700 | 2,450 | 1,250 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 3,600 | 3,600 | 50 | 3,900 |
| 1xgoofy#56719 | 1,700 | 1,700 | 3,600 | 3,600 | 250 | 3,900 |
| VorteXx#Val | 4,450 | 4,450 | 1,950 | 1,950 | 950 | 3,900 |

**TEAM_2** lost L=17,100; target H=19,500; funding U=25,250; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 100 | 3,900 |
| DoubleBl1nd#BEEF | 4,300 | 0 | 5,100 | 4,850 | 2,250 | 3,900 |
| Osmin#NA1 | 4,500 | 4,500 | 4,500 | 4,250 | 50 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,450 | 3,900 |
| ternstyle#GIGI | 4,050 | 4,050 | 4,650 | 4,500 | 1,900 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484834 | 26.110s | Helpless#qiqi -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 484832 | 29.899s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 22.1 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484833 | 31.118s | ZETA 3y5#213 -> ternstyle#GIGI | enemy | 5v3 | 90 | 1.000 | 90.0 | 9.0 | 4,050 | 20.93 | 0.00 | 20.93 | 6.28 (30%, absorbed) |
| event 484828 | 32.177s | DoubleBl1nd#BEEF -> ZETA 3y5#213 | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 4,500 | 23.26 | 36.69 | 59.95 | 47.96 (80%, constrained) |
| event 484829 | 36.598s | DoubleBl1nd#BEEF -> Mokalover67#ILLIT | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,600 | 23.78 | 37.50 | 61.28 | 49.02 (80%, constrained) |
| event 484835 | 45.299s | Helpless#qiqi -> Najumi#NPC | enemy | 3v3 | 180 | 1.000 | 180.0 | 18.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484827 | 47.065s | ternstyle#GIGI -> Helpless#qiqi | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,550 | 23.52 | 37.10 | 60.61 | 48.49 (80%, constrained) |
| event 484830 | 49.689s | DoubleBl1nd#BEEF -> 1xgoofy#56719 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 1,700 | 8.79 | 13.86 | 22.65 | 18.12 (80%, constrained) |
| event 484836 | 68.067s | VorteXx#Val -> ternstyle#GIGI | enemy | 1v2 | 190 | 1.226 | 232.9 | 232.9 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484831 | 78.242s | DoubleBl1nd#BEEF -> VorteXx#Val | enemy | 1v1 | 250 | 1.418 | 354.5 | 354.5 | 4,450 | 23.00 | 36.28 | 59.28 | 47.43 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +2,049 | 966 | +834 | 203.16 | 0.00 | +203 | +2,003 |
| Helpless#qiqi | +662 | 581 | +160 | 44.19 | 48.49 | -4 | +737 |
| ZETA 3y5#213 | +655 | 611 | +100 | 44.19 | 47.96 | -4 | +707 |
| ternstyle#GIGI | +307 | 271 | -72 | 60.61 | 6.28 | +54 | +253 |
| Osmin#NA1 | +70 | 92 | -22 | 0.00 | 6.98 | -7 | +63 |
| Najumi#NPC | +26 | 44 | -18 | 0.00 | 6.59 | -7 | +19 |
| VorteXx#Val | -122 | 75 | -122 | 0.00 | 47.43 | -47 | -94 |
| NPrightdolphin#NA1 | -150 | 0 | -150 | 0.00 | 6.67 | -7 | -157 |
| Mokalover67#ILLIT | -185 | 50 | -160 | 0.00 | 49.02 | -49 | -159 |
| 1xgoofy#56719 | -271 | 0 | -200 | 0.00 | 18.12 | -18 | -218 |

## Round 21

Pistol winner TEAM_1; round winner TEAM_2; half round 9.

**TEAM_1** lost L=13,700; target H=19,500; funding U=19,050; gap D=450; observed next-equipment gap G=2,000; activation 0.1154; severity pool 230.77 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 2,100 | 4,650 | 4,500 | 50 | 3,900 |
| Helpless#qiqi | 2,450 | 2,450 | 4,450 | 4,200 | 300 | 3,900 |
| Mokalover67#ILLIT | 3,600 | 3,600 | 3,350 | 3,350 | 0 | 3,900 |
| 1xgoofy#56719 | 3,600 | 3,600 | 2,500 | 2,500 | 1,050 | 3,900 |
| VorteXx#Val | 1,950 | 1,950 | 3,850 | 3,850 | 150 | 3,900 |

**TEAM_2** lost L=22,150; target H=19,500; funding U=23,050; gap D=0; observed next-equipment gap G=800; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 3,500 | 3,500 | 100 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 1,650 | 3,900 |
| Osmin#NA1 | 4,250 | 4,250 | 3,750 | 3,500 | 50 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,200 | 3,900 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 1,350 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484837 | 31.512s | ternstyle#GIGI -> VorteXx#Val | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,950 | 10.08 | 1.70 | 11.78 | 9.42 (80%, constrained) |
| event 484847 | 34.235s | Osmin#NA1 -> ZETA 3y5#213 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 2,100 | 10.85 | 1.83 | 12.68 | 10.15 (80%, constrained) |
| event 484845 | 43.106s | Helpless#qiqi -> ternstyle#GIGI | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |
| event 484843 | 57.746s | Mokalover67#ILLIT -> Najumi#NPC | enemy | 4v4 | 170 | 1.150 | 195.5 | 195.5 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484844 | 69.746s | Mokalover67#ILLIT -> Osmin#NA1 | enemy | 4v3 | 130 | 1.376 | 178.9 | 178.9 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 484846 | 76.135s | Helpless#qiqi -> NPrightdolphin#NA1 | enemy | 4v2 | 80 | 1.497 | 119.7 | 119.7 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 484838 | 78.124s | DoubleBl1nd#BEEF -> Mokalover67#ILLIT | enemy | 1v4 | 70 | 1.534 | 107.4 | 107.4 | 3,600 | 18.61 | 3.13 | 21.74 | 17.39 (80%, constrained) |
| event 484839 | 79.856s | DoubleBl1nd#BEEF -> 1xgoofy#56719 | enemy | 1v3 | 120 | 1.567 | 188.0 | 188.0 | 3,600 | 18.61 | 3.13 | 21.74 | 17.39 (80%, constrained) |
| event 484840 | 87.950s | DoubleBl1nd#BEEF -> Helpless#qiqi | enemy | 1v2 | 190 | 1.750 | 332.5 | 95.0 | 2,450 | 12.66 | 2.13 | 14.80 | 11.84 (80%, constrained) |
| event 484841 | 89.351s | DoubleBl1nd#BEEF -> ZETA 3y5#213 | enemy | 1v1 | 250 | 1.750 | 437.5 | 125.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (80%, constrained) |
| event 484842 | 95.995s | DoubleBl1nd#BEEF -> DoubleBl1nd#BEEF | self | 1v1 | 100 | 1.000 | 0.0 | 187.1 | 4,850 | 0.00 | 0.00 | 0.00 | 7.52 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| DoubleBl1nd#BEEF | +1,844 | 1,170 | +878 | 58.28 | 7.52 | +51 | +2,099 |
| Helpless#qiqi | +826 | 724 | +165 | 45.49 | 11.84 | +34 | +923 |
| Mokalover67#ILLIT | +630 | 369 | +267 | 43.94 | 17.39 | +27 | +663 |
| ternstyle#GIGI | +183 | 188 | +10 | 11.78 | 6.98 | +5 | +203 |
| Osmin#NA1 | +130 | 150 | -49 | 12.68 | 6.59 | +6 | +107 |
| NPrightdolphin#NA1 | +29 | 136 | -120 | 0.00 | 6.67 | -7 | +9 |
| VorteXx#Val | -158 | 0 | -150 | 0.00 | 9.42 | -9 | -159 |
| 1xgoofy#56719 | -123 | 31 | -188 | 0.00 | 17.39 | -17 | -174 |
| Najumi#NPC | -193 | 0 | -195 | 0.00 | 6.59 | -7 | -202 |
| ZETA 3y5#213 | -327 | 31 | -255 | 0.00 | 10.15 | -10 | -234 |

## Round 22

Pistol winner TEAM_1; round winner TEAM_1; half round 10.

**TEAM_1** lost L=3,350; target H=19,500; funding U=28,300; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,300 | 4,150 | 2,650 | 3,900 |
| Helpless#qiqi | 4,200 | 0 | 5,100 | 4,850 | 1,700 | 3,900 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 4,600 | 4,600 | 1,450 | 3,900 |
| 1xgoofy#56719 | 2,500 | 0 | 5,400 | 5,400 | 650 | 3,900 |
| VorteXx#Val | 3,850 | 0 | 4,450 | 4,450 | 2,350 | 3,900 |

**TEAM_2** lost L=16,100; target H=19,500; funding U=17,750; gap D=1,750; observed next-equipment gap G=11,950; activation 0.4487; severity pool 5,362.18 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,500 | 3,500 | 600 | 600 | 1,600 | 3,900 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 2,150 | 1,900 | 1,800 | 3,900 |
| Osmin#NA1 | 3,500 | 3,500 | 700 | 450 | 1,950 | 3,900 |
| Najumi#NPC | 4,250 | 4,250 | 700 | 700 | 2,650 | 3,900 |
| ternstyle#GIGI | 4,500 | 0 | 4,650 | 4,500 | 2,200 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484852 | 73.500s | VorteXx#Val -> NPrightdolphin#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,500 | 18.09 | 60.25 | 78.34 | 62.67 (80%, constrained) |
| event 484849 | 84.290s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 3,500 | 18.09 | 60.25 | 78.34 | 62.67 (80%, constrained) |
| event 484850 | 86.290s | ZETA 3y5#213 -> DoubleBl1nd#BEEF | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,850 | 25.07 | 83.49 | 108.56 | 86.85 (80%, constrained) |
| event 484851 | 86.581s | Helpless#qiqi -> Najumi#NPC | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 4,250 | 21.97 | 73.16 | 95.13 | 76.11 (80%, constrained) |
| event 484848 | 90.550s | ternstyle#GIGI -> Mokalover67#ILLIT | enemy | 1v5 | 60 | 1.000 | 60.0 | 60.0 | 3,350 | 17.32 | 0.00 | 17.32 | 5.19 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | +806 | 530 | +220 | 186.91 | 0.00 | +187 | +937 |
| VorteXx#Val | +350 | 156 | +150 | 78.34 | 0.00 | +78 | +384 |
| ternstyle#GIGI | +352 | 296 | +60 | 17.32 | 0.00 | +17 | +373 |
| Helpless#qiqi | +221 | 156 | +50 | 95.13 | 0.00 | +95 | +301 |
| 1xgoofy#56719 | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| Mokalover67#ILLIT | -56 | 0 | -60 | 0.00 | 5.19 | -5 | -65 |
| Najumi#NPC | -65 | 0 | -50 | 0.00 | 76.11 | -76 | -126 |
| DoubleBl1nd#BEEF | -66 | 50 | -90 | 0.00 | 86.85 | -87 | -127 |
| Osmin#NA1 | -159 | 0 | -130 | 0.00 | 62.67 | -63 | -193 |
| NPrightdolphin#NA1 | -194 | 0 | -150 | 0.00 | 62.67 | -63 | -213 |

## Round 23

Pistol winner TEAM_1; round winner TEAM_1; half round 11.

**TEAM_1** lost L=4,150; target H=19,500; funding U=37,600; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 1,800 | 3,900 |
| Helpless#qiqi | 4,850 | 0 | 5,100 | 4,850 | 3,400 | 3,900 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 4,050 | 3,900 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 3,650 | 3,900 |
| VorteXx#Val | 4,450 | 0 | 4,450 | 4,450 | 5,200 | 3,900 |

**TEAM_2** lost L=8,150; target H=19,500; funding U=21,550; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 600 | 600 | 4,300 | 4,300 | 100 | 3,900 |
| DoubleBl1nd#BEEF | 1,900 | 1,900 | 4,550 | 4,300 | 50 | 3,900 |
| Osmin#NA1 | 450 | 450 | 4,500 | 4,250 | 100 | 3,900 |
| Najumi#NPC | 700 | 700 | 4,250 | 4,250 | 1,050 | 3,900 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484857 | 20.581s | VorteXx#Val -> DoubleBl1nd#BEEF | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 484853 | 24.363s | ternstyle#GIGI -> ZETA 3y5#213 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 484854 | 37.140s | ZETA 3y5#213 -> Osmin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 6.5 | 450 | 2.33 | 0.00 | 2.33 | 0.70 (30%, absorbed) |
| event 484859 | 37.548s | NPrightdolphin#NA1 -> ZETA 3y5#213 | enemy | 3v5 | 120 | 1.000 | 120.0 | 6.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 484858 | 38.323s | VorteXx#Val -> NPrightdolphin#NA1 | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 600 | 3.10 | 0.00 | 3.10 | 0.93 (30%, absorbed) |
| event 484855 | 39.340s | Helpless#qiqi -> Najumi#NPC | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |
| event 484856 | 41.060s | Helpless#qiqi -> ternstyle#GIGI | enemy | 4v1 | 50 | 1.000 | 50.0 | 50.0 | 4,500 | 23.26 | 0.00 | 23.26 | 6.98 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| VorteXx#Val | +634 | 439 | +280 | 12.92 | 0.00 | +13 | +732 |
| Helpless#qiqi | +546 | 451 | +130 | 26.88 | 0.00 | +27 | +608 |
| ternstyle#GIGI | +304 | 206 | +90 | 21.45 | 6.98 | +14 | +310 |
| ZETA 3y5#213 | +173 | 234 | -16 | 2.33 | 6.44 | -4 | +214 |
| NPrightdolphin#NA1 | +195 | 122 | -10 | 0.00 | 0.93 | -1 | +111 |
| 1xgoofy#56719 | +62 | 62 | +0 | 0.00 | 0.00 | +0 | +62 |
| Osmin#NA1 | +61 | 65 | -6 | 0.00 | 0.70 | -1 | +58 |
| Mokalover67#ILLIT | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Najumi#NPC | -29 | 24 | -80 | 0.00 | 1.09 | -1 | -57 |
| DoubleBl1nd#BEEF | -108 | 0 | -150 | 0.00 | 2.95 | -3 | -153 |

## Round 24

Economy abstains: **final_round** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 484866 | 14.101s | Osmin#NA1 -> Mokalover67#ILLIT | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 484862 | 18.795s | Helpless#qiqi -> ternstyle#GIGI | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 484860 | 23.693s | DoubleBl1nd#BEEF -> ZETA 3y5#213 | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 484863 | 24.644s | Helpless#qiqi -> ternstyle#GIGI | combat | 3v5 | 120 | 1.000 | 120.0 | 12.0 | | | | | no economy this round |
| event 484864 | 26.174s | Najumi#NPC -> Helpless#qiqi | combat | 4v3 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 484861 | 38.477s | 1xgoofy#56719 -> DoubleBl1nd#BEEF | combat | 2v4 | 130 | 1.018 | 132.4 | 132.4 | | | | | no economy this round |
| event 484865 | 49.625s | Najumi#NPC -> 1xgoofy#56719 | combat | 3v2 | 140 | 1.229 | 172.0 | 172.0 | | | | | no economy this round |
| event 484868 | 56.222s | VorteXx#Val -> Najumi#NPC | combat | 1v3 | 120 | 1.353 | 162.4 | 162.4 | | | | | no economy this round |
| event 484867 | 68.025s | Osmin#NA1 -> VorteXx#Val | combat | 2v1 | 130 | 1.576 | 204.8 | 204.8 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Najumi#NPC | +806 | 659 | +140 | 0.00 | 0.00 | +0 | +799 |
| Osmin#NA1 | +720 | 415 | +355 | 0.00 | 0.00 | +0 | +770 |
| Helpless#qiqi | +630 | 500 | +130 | 0.00 | 0.00 | +0 | +630 |
| DoubleBl1nd#BEEF | +187 | 188 | -2 | 0.00 | 0.00 | +0 | +186 |
| VorteXx#Val | +198 | 219 | -42 | 0.00 | 0.00 | +0 | +177 |
| 1xgoofy#56719 | +134 | 154 | -40 | 0.00 | 0.00 | +0 | +114 |
| NPrightdolphin#NA1 | +49 | 49 | +0 | 0.00 | 0.00 | +0 | +49 |
| ZETA 3y5#213 | -81 | 49 | -130 | 0.00 | 0.00 | +0 | -81 |
| ternstyle#GIGI | -111 | 41 | -152 | 0.00 | 0.00 | +0 | -111 |
| Mokalover67#ILLIT | -150 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |

## Match totals

| Player | Before | A*damage | B*leverage | Gross econ credit | Gross econ debit | C*econ | Econ / played round | After |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| VorteXx#Val | +7,374 | 6,034 | +1,462 | 445.85 | 236.78 | +209 | +8.71 | +7,705 |
| Osmin#NA1 | +6,878 | 5,617 | +994 | 853.47 | 145.94 | +708 | +29.50 | +7,319 |
| NPrightdolphin#NA1 | +6,930 | 5,926 | +749 | 251.52 | 139.28 | +111 | +4.62 | +6,786 |
| DoubleBl1nd#BEEF | +5,870 | 5,174 | +577 | 455.39 | 175.16 | +277 | +11.54 | +6,028 |
| Helpless#qiqi | +5,094 | 5,383 | -95 | 442.73 | 248.60 | +196 | +8.17 | +5,484 |
| Najumi#NPC | +4,999 | 4,504 | +679 | 196.06 | 144.64 | +49 | +2.04 | +5,232 |
| ZETA 3y5#213 | +4,285 | 4,585 | +227 | 457.23 | 263.29 | +194 | +8.08 | +5,006 |
| Mokalover67#ILLIT | +3,169 | 3,686 | -259 | 234.41 | 231.56 | +4 | +0.17 | +3,431 |
| ternstyle#GIGI | +2,519 | 3,009 | -738 | 234.04 | 85.44 | +148 | +6.17 | +2,419 |
| 1xgoofy#56719 | +2,234 | 3,085 | -774 | 95.62 | 198.40 | -101 | -4.21 | +2,210 |
