# Abyss 3104: worked buy-disruption calculation

TEAM_1 lost 11-13 to TEAM_2. The match began 2026-08-25 00:48 UTC (August 24 in Pacific time).
These are experimental ECON points only. Combat credit, damage/assist credit and total Impact are not being redefined or rescored here.
V2 fixed values: background=0.10, disruption=1.00, R=19500, activation gap=3900, scale=1007.9209, C=1. Paid-loadout targets follow the spec.

20 eligible rounds; 6/40 team-rounds have a positive estimated restorable funding gap. 163 arithmetic checks pass.

## Every eligible round and team

L = first-loss exposure; H = target; U = capped next equipment plus bank; D=max(0,H-U). V2 pool=min(L, observed equipment gap * min(1,D/3900)). It is a severity index, not literal missing cash or causal denial.

| Round | Team | Won round? | Deaths / first-loss players | L | H | U | Funding gap D | Severity pool | Next raw below 4200 | Killer credit | Own debit | Net econ |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | TEAM_1 | no | 5/5 | 3,200 | 19,500 | 20,200 | 0 | 0.0 | 1 | 0.0 | 87.4 | -87 |
| 2 | TEAM_2 | yes | 0/0 | 0 | 15,700 | 31,000 | 0 | 0.0 | 4 | 13.4 | 0.0 | +14 |
| 3 | TEAM_1 | no | 3/3 | 11,000 | 19,500 | 24,250 | 0 | 0.0 | 3 | 50.1 | 215.2 | -165 |
| 3 | TEAM_2 | yes | 3/3 | 9,700 | 19,500 | 34,900 | 0 | 0.0 | 1 | 56.9 | 50.1 | +6 |
| 4 | TEAM_1 | no | 5/5 | 17,200 | 19,500 | 22,900 | 0 | 0.0 | 0 | 67.5 | 317.5 | -250 |
| 4 | TEAM_2 | yes | 3/3 | 13,050 | 19,500 | 36,000 | 0 | 0.0 | 1 | 88.9 | 67.5 | +21 |
| 5 | TEAM_1 | yes | 4/4 | 18,200 | 19,500 | 24,500 | 0 | 0.0 | 0 | 122.0 | 306.9 | -185 |
| 5 | TEAM_2 | no | 5/5 | 23,600 | 19,500 | 24,600 | 0 | 0.0 | 0 | 94.1 | 293.3 | -198 |
| 6 | TEAM_1 | no | 6/5 | 21,750 | 19,500 | 15,850 | 3,650 | 9,171.8 | 4 | 45.5 | 918.1 | -873 |
| 6 | TEAM_2 | yes | 2/2 | 8,800 | 19,500 | 31,000 | 0 | 0.0 | 0 | 586.5 | 45.5 | +540 |
| 7 | TEAM_1 | no | 5/5 | 10,300 | 19,500 | 19,300 | 200 | 176.9 | 3 | 0.0 | 346.1 | -346 |
| 7 | TEAM_2 | yes | 0/0 | 0 | 19,500 | 42,650 | 0 | 0.0 | 0 | 62.4 | 0.0 | +63 |
| 8 | TEAM_1 | yes | 2/2 | 8,450 | 19,500 | 29,550 | 0 | 0.0 | 0 | 136.5 | 43.7 | +93 |
| 8 | TEAM_2 | no | 6/5 | 26,400 | 19,500 | 30,100 | 0 | 0.0 | 1 | 43.7 | 136.5 | -93 |
| 9 | TEAM_1 | yes | 4/3 | 14,100 | 19,500 | 32,750 | 0 | 0.0 | 0 | 124.1 | 72.9 | +51 |
| 9 | TEAM_2 | no | 5/5 | 24,000 | 19,500 | 24,000 | 0 | 0.0 | 4 | 72.9 | 540.5 | -467 |
| 10 | TEAM_1 | yes | 4/3 | 14,800 | 19,500 | 34,000 | 0 | 0.0 | 0 | 83.5 | 76.5 | +6 |
| 10 | TEAM_2 | no | 5/5 | 16,150 | 19,500 | 22,300 | 0 | 0.0 | 1 | 76.5 | 367.7 | -290 |
| 11 | TEAM_1 | yes | 1/1 | 4,550 | 19,500 | 40,100 | 0 | 0.0 | 0 | 90.2 | 23.5 | +66 |
| 11 | TEAM_2 | no | 4/4 | 17,450 | 19,500 | 20,700 | 0 | 0.0 | 4 | 23.5 | 530.4 | -507 |
| 14 | TEAM_1 | yes | 3/3 | 4,550 | 10,600 | 20,250 | 0 | 0.0 | 3 | 13.7 | 66.6 | -53 |
| 14 | TEAM_2 | no | 5/4 | 2,650 | 19,500 | 21,350 | 0 | 0.0 | 1 | 23.5 | 68.5 | -45 |
| 15 | TEAM_1 | no | 5/5 | 17,400 | 19,500 | 21,050 | 0 | 0.0 | 3 | 0.0 | 488.2 | -488 |
| 15 | TEAM_2 | yes | 0/0 | 0 | 19,500 | 36,050 | 0 | 0.0 | 1 | 89.9 | 0.0 | +90 |
| 16 | TEAM_1 | no | 5/5 | 17,050 | 19,500 | 17,950 | 1,550 | 1,251.9 | 3 | 66.4 | 612.7 | -546 |
| 16 | TEAM_2 | yes | 3/3 | 12,850 | 19,500 | 38,850 | 0 | 0.0 | 0 | 152.8 | 66.4 | +86 |
| 17 | TEAM_1 | yes | 3/3 | 12,200 | 19,500 | 23,700 | 0 | 0.0 | 2 | 115.8 | 214.7 | -98 |
| 17 | TEAM_2 | no | 5/5 | 22,400 | 19,500 | 28,950 | 0 | 0.0 | 0 | 63.1 | 115.8 | -52 |
| 18 | TEAM_1 | yes | 4/3 | 12,350 | 19,500 | 29,850 | 0 | 0.0 | 0 | 115.8 | 63.8 | +51 |
| 18 | TEAM_2 | no | 5/5 | 22,400 | 19,500 | 22,450 | 0 | 0.0 | 3 | 63.8 | 562.4 | -499 |
| 19 | TEAM_1 | no | 5/5 | 22,200 | 19,500 | 20,300 | 0 | 0.0 | 1 | 64.6 | 590.1 | -525 |
| 19 | TEAM_2 | yes | 4/4 | 12,500 | 19,500 | 26,200 | 0 | 0.0 | 0 | 93.3 | 169.2 | -75 |
| 20 | TEAM_1 | no | 5/5 | 19,800 | 19,500 | 17,400 | 2,100 | 3,123.1 | 5 | 88.4 | 789.5 | -700 |
| 20 | TEAM_2 | yes | 5/4 | 17,100 | 19,500 | 25,250 | 0 | 0.0 | 0 | 263.8 | 239.9 | +24 |
| 21 | TEAM_1 | no | 6/5 | 13,700 | 19,500 | 19,050 | 450 | 230.8 | 3 | 89.4 | 460.3 | -372 |
| 21 | TEAM_2 | yes | 5/5 | 22,150 | 19,500 | 23,050 | 0 | 0.0 | 2 | 82.7 | 471.6 | -389 |
| 22 | TEAM_1 | yes | 1/1 | 3,350 | 19,500 | 28,300 | 0 | 0.0 | 0 | 360.4 | 17.3 | +343 |
| 22 | TEAM_2 | no | 4/4 | 16,100 | 19,500 | 17,750 | 1,750 | 5,362.2 | 4 | 17.3 | 604.3 | -587 |
| 23 | TEAM_1 | yes | 2/1 | 4,150 | 19,500 | 37,600 | 0 | 0.0 | 0 | 42.1 | 21.5 | +21 |
| 23 | TEAM_2 | no | 5/5 | 8,150 | 19,500 | 21,550 | 0 | 0.0 | 0 | 21.5 | 199.6 | -179 |

## Highest positive enemy-kill econ credits

| Round | Time | Killer | Victim | Exposure | Small credit | Disruption credit | Total econ credit |
|---|---:|---|---|---:|---:|---:|---:|
| 6 | 15.581s | Osmin#NA1 | 1xgoofy#56719 | 4,600 | 23.78 | 100.26 | 124.04 |
| 6 | 14.943s | Osmin#NA1 | ZETA 3y5#213 | 4,500 | 23.26 | 98.08 | 121.34 |
| 6 | 77.337s | Osmin#NA1 | VorteXx#Val | 4,450 | 23.00 | 96.99 | 120.00 |
| 6 | 81.148s | Osmin#NA1 | Mokalover67#ILLIT | 4,250 | 21.97 | 92.64 | 114.60 |
| 22 | 86.290s | ZETA 3y5#213 | DoubleBl1nd#BEEF | 4,850 | 25.07 | 83.49 | 108.56 |
| 6 | 15.855s | Osmin#NA1 | Helpless#qiqi | 3,950 | 20.42 | 86.10 | 106.51 |
| 22 | 86.581s | Helpless#qiqi | Najumi#NPC | 4,250 | 21.97 | 73.16 | 95.13 |
| 22 | 73.500s | VorteXx#Val | NPrightdolphin#NA1 | 3,500 | 18.09 | 60.25 | 78.34 |

## Lowest positive enemy-kill econ credits

| Round | Time | Killer | Victim | Exposure | Small credit | Disruption credit | Total econ credit |
|---|---:|---|---|---:|---:|---:|---:|
| 14 | 51.248s | VorteXx#Val | Osmin#NA1 | 200 | 1.03 | 0.00 | 1.03 |
| 2 | 12.609s | Osmin#NA1 | 1xgoofy#56719 | 300 | 1.55 | 0.00 | 1.55 |
| 23 | 37.140s | ZETA 3y5#213 | Osmin#NA1 | 450 | 2.33 | 0.00 | 2.33 |
| 14 | 84.352s | ZETA 3y5#213 | NPrightdolphin#NA1 | 600 | 3.10 | 0.00 | 3.10 |
| 23 | 38.323s | VorteXx#Val | NPrightdolphin#NA1 | 600 | 3.10 | 0.00 | 3.10 |
| 2 | 11.437s | Osmin#NA1 | Helpless#qiqi | 650 | 3.36 | 0.00 | 3.36 |
| 2 | 16.011s | ternstyle#GIGI | Mokalover67#ILLIT | 700 | 3.62 | 0.00 | 3.62 |
| 14 | 82.611s | VorteXx#Val | Najumi#NPC | 700 | 3.62 | 0.00 | 3.62 |

## Detailed inputs and event calculations

### Round 1: zero (pistol_half_or_ot_boundary)

### Round 2: Team B Elimination Win

Pistol winner: TEAM_2. Plant time: none.

**TEAM_1:** L=3,200; H=19,500; U=20,200; D=0; restorable Q=0. Observed gap=1,950; activation=0.000000; severity pool=0.000. Uncapped next wealth=22,500; f=0.428571.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 600 | 600 | 4,650 | 4,500 | 200 | 3,900 | 0.000000 | 0.016264 | -16 |
| Helpless#qiqi | 650 | 650 | 4,800 | 4,550 | 300 | 3,900 | 0.000000 | 0.017619 | -18 |
| Mokalover67#ILLIT | 700 | 700 | 4,250 | 4,250 | 200 | 3,900 | 0.000000 | 0.018974 | -19 |
| 1xgoofy#56719 | 300 | 300 | 4,600 | 4,600 | 200 | 3,900 | 0.000000 | 0.008132 | -8 |
| VorteXx#Val | 950 | 950 | 1,950 | 1,950 | 1,750 | 3,900 | 0.000000 | 0.025751 | -26 |

**TEAM_2:** L=0; H=15,700; U=31,000; D=0; restorable Q=0. Observed gap=100; activation=0.000000; severity pool=0.000. Uncapped next wealth=32,200; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,300 | 0 | 3,200 | 3,200 | 3,200 | 3,300 | 0.000000 | 0.000000 | +0 |
| DoubleBl1nd#BEEF | 2,650 | 0 | 2,900 | 2,650 | 3,350 | 2,650 | 0.000000 | 0.000000 | +0 |
| Osmin#NA1 | 3,000 | 0 | 4,100 | 3,850 | 2,150 | 3,000 | 0.009744 | 0.000000 | +10 |
| Najumi#NPC | 2,900 | 0 | 2,900 | 2,900 | 3,850 | 2,900 | 0.000000 | 0.000000 | +0 |
| ternstyle#GIGI | 3,850 | 0 | 4,350 | 4,200 | 2,850 | 3,850 | 0.003590 | 0.000000 | +4 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484693 | 11.437s | Osmin#NA1 | Helpless#qiqi | 650 | 3.36 | 0.00 | 3.36 | 17.76 |
| 484694 | 12.609s | Osmin#NA1 | 1xgoofy#56719 | 300 | 1.55 | 0.00 | 1.55 | 8.20 |
| 484691 | 16.011s | ternstyle#GIGI | Mokalover67#ILLIT | 700 | 3.62 | 0.00 | 3.62 | 19.12 |
| 484695 | 16.570s | Osmin#NA1 | VorteXx#Val | 950 | 4.91 | 0.00 | 4.91 | 25.95 |
| 484692 | 21.580s | ZETA 3y5#213 | ZETA 3y5#213 | 600 | 0.00 | 0.00 | 0.00 | 16.39 |

### Round 3: Team B Defuse Win

Pistol winner: TEAM_2. Plant time: 20.582.

**TEAM_1:** L=11,000; H=19,500; U=24,250; D=0; restorable Q=0. Observed gap=3,700; activation=0.000000; severity pool=0.000. Uncapped next wealth=25,650; f=0.278571.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 2,200 | 2,050 | 1,800 | 3,900 | 0.000000 | 0.087363 | -88 |
| Helpless#qiqi | 4,550 | 4,550 | 2,700 | 2,450 | 1,200 | 3,900 | 0.013590 | 0.088333 | -75 |
| Mokalover67#ILLIT | 4,250 | 0 | 4,600 | 4,600 | 2,100 | 3,900 | 0.036154 | 0.000000 | +36 |
| 1xgoofy#56719 | 4,600 | 0 | 4,600 | 4,600 | 1,900 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 1,950 | 1,950 | 3,500 | 3,500 | 1,450 | 3,900 | 0.000000 | 0.037857 | -38 |

**TEAM_2:** L=9,700; H=19,500; U=34,900; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=38,100; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,200 | 3,200 | 4,000 | 4,000 | 1,900 | 3,900 | 0.023333 | 0.016410 | +7 |
| DoubleBl1nd#BEEF | 2,650 | 2,650 | 4,800 | 4,550 | 2,200 | 3,900 | 0.000000 | 0.013590 | -14 |
| Osmin#NA1 | 3,850 | 3,850 | 4,750 | 4,500 | 650 | 3,900 | 0.000000 | 0.019744 | -20 |
| Najumi#NPC | 2,900 | 0 | 4,850 | 4,850 | 5,750 | 3,900 | 0.000000 | 0.000000 | +0 |
| ternstyle#GIGI | 4,200 | 0 | 4,950 | 4,800 | 4,900 | 3,900 | 0.033077 | 0.000000 | +33 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484696 | 13.890s | ternstyle#GIGI | VorteXx#Val | 1,950 | 10.08 | 0.00 | 10.08 | 38.16 |
| 484700 | 16.329s | Helpless#qiqi | DoubleBl1nd#BEEF | 2,650 | 13.70 | 0.00 | 13.70 | 13.70 |
| 484701 | 24.396s | NPrightdolphin#NA1 | Helpless#qiqi | 4,550 | 23.52 | 0.00 | 23.52 | 89.03 |
| 484697 | 28.895s | ternstyle#GIGI | ZETA 3y5#213 | 4,500 | 23.26 | 0.00 | 23.26 | 88.05 |
| 484698 | 38.248s | Mokalover67#ILLIT | Osmin#NA1 | 3,850 | 19.90 | 0.00 | 19.90 | 19.90 |
| 484699 | 51.176s | Mokalover67#ILLIT | NPrightdolphin#NA1 | 3,200 | 16.54 | 0.00 | 16.54 | 16.54 |

### Round 4: Team B Defuse Win

Pistol winner: TEAM_2. Plant time: 24.747.

**TEAM_1:** L=17,200; H=19,500; U=22,900; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=26,100; f=0.257143.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,050 | 2,050 | 4,650 | 4,500 | 950 | 3,900 | 0.000000 | 0.037546 | -38 |
| Helpless#qiqi | 2,450 | 2,450 | 4,800 | 4,550 | 450 | 3,900 | 0.020513 | 0.044872 | -25 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,600 | 4,600 | 700 | 3,900 | 0.000000 | 0.084249 | -85 |
| 1xgoofy#56719 | 4,600 | 4,600 | 4,600 | 4,600 | 900 | 3,900 | 0.023333 | 0.084249 | -61 |
| VorteXx#Val | 3,500 | 3,500 | 4,450 | 4,450 | 400 | 3,900 | 0.023077 | 0.064103 | -41 |

**TEAM_2:** L=13,050; H=19,500; U=36,000; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=40,100; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,000 | 4,000 | 4,100 | 4,100 | 3,400 | 3,900 | 0.000000 | 0.020513 | -21 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 5,100 | 4,850 | 4,050 | 3,900 | 0.054103 | 0.023333 | +31 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 2,200 | 3,900 | 0.000000 | 0.023077 | -23 |
| Najumi#NPC | 4,850 | 0 | 4,850 | 4,850 | 3,000 | 3,900 | 0.034103 | 0.000000 | +34 |
| ternstyle#GIGI | 4,800 | 0 | 5,450 | 5,300 | 3,850 | 3,900 | 0.000000 | 0.000000 | +0 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484706 | 19.788s | Helpless#qiqi | NPrightdolphin#NA1 | 4,000 | 20.68 | 0.00 | 20.68 | 20.68 |
| 484702 | 28.417s | DoubleBl1nd#BEEF | Helpless#qiqi | 2,450 | 12.66 | 0.00 | 12.66 | 45.23 |
| 484703 | 32.751s | DoubleBl1nd#BEEF | Mokalover67#ILLIT | 4,600 | 23.78 | 0.00 | 23.78 | 84.92 |
| 484709 | 38.012s | VorteXx#Val | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 23.26 |
| 484704 | 38.791s | DoubleBl1nd#BEEF | VorteXx#Val | 3,500 | 18.09 | 0.00 | 18.09 | 64.61 |
| 484705 | 42.237s | 1xgoofy#56719 | DoubleBl1nd#BEEF | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484707 | 43.437s | Najumi#NPC | 1xgoofy#56719 | 4,600 | 23.78 | 0.00 | 23.78 | 84.92 |
| 484708 | 46.357s | Najumi#NPC | ZETA 3y5#213 | 2,050 | 10.60 | 0.00 | 10.60 | 37.84 |

### Round 5: Team A Elimination Win

Pistol winner: TEAM_2. Plant time: 25.057.

**TEAM_1:** L=18,200; H=19,500; U=24,500; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=26,750; f=0.226190.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,650 | 4,500 | 4,500 | 3,900 | 0.048205 | 0.000000 | +49 |
| Helpless#qiqi | 4,550 | 4,550 | 4,200 | 3,950 | 200 | 3,900 | 0.000000 | 0.076111 | -77 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,250 | 4,250 | 100 | 3,900 | 0.024872 | 0.076947 | -52 |
| 1xgoofy#56719 | 4,600 | 4,600 | 4,600 | 4,600 | 0 | 3,900 | 0.000000 | 0.076947 | -78 |
| VorteXx#Val | 4,450 | 4,450 | 4,450 | 4,450 | 200 | 3,900 | 0.047949 | 0.074438 | -27 |

**TEAM_2:** L=23,600; H=19,500; U=24,600; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=28,550; f=0.140476.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,300 | 4,300 | 1,400 | 3,900 | 0.046410 | 0.050562 | -4 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 4,800 | 4,550 | 2,050 | 3,900 | 0.000000 | 0.059811 | -60 |
| Osmin#NA1 | 4,500 | 4,500 | 4,500 | 4,250 | 200 | 3,900 | 0.023333 | 0.055495 | -32 |
| Najumi#NPC | 4,850 | 4,850 | 4,250 | 4,250 | 1,350 | 3,900 | 0.000000 | 0.059811 | -60 |
| ternstyle#GIGI | 5,300 | 5,300 | 6,250 | 6,100 | 100 | 3,900 | 0.023590 | 0.065360 | -42 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484714 | 13.973s | Osmin#NA1 | Helpless#qiqi | 4,550 | 23.52 | 0.00 | 23.52 | 76.71 |
| 484715 | 14.441s | VorteXx#Val | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 55.93 |
| 484711 | 21.220s | Mokalover67#ILLIT | Najumi#NPC | 4,850 | 25.07 | 0.00 | 25.07 | 60.28 |
| 484710 | 22.867s | ternstyle#GIGI | Mokalover67#ILLIT | 4,600 | 23.78 | 0.00 | 23.78 | 77.56 |
| 484716 | 25.243s | VorteXx#Val | DoubleBl1nd#BEEF | 4,850 | 25.07 | 0.00 | 25.07 | 60.28 |
| 484712 | 29.139s | ZETA 3y5#213 | ternstyle#GIGI | 5,300 | 27.39 | 0.00 | 27.39 | 65.88 |
| 484717 | 36.959s | NPrightdolphin#NA1 | VorteXx#Val | 4,450 | 23.00 | 0.00 | 23.00 | 75.03 |
| 484718 | 39.146s | NPrightdolphin#NA1 | 1xgoofy#56719 | 4,600 | 23.78 | 0.00 | 23.78 | 77.56 |
| 484713 | 42.189s | ZETA 3y5#213 | NPrightdolphin#NA1 | 4,100 | 21.19 | 0.00 | 21.19 | 50.96 |

### Round 6: Team B Elimination Win

Pistol winner: TEAM_2. Plant time: none.

**TEAM_1:** L=21,750; H=19,500; U=15,850; D=3,650; restorable Q=3,650. Observed gap=9,800; activation=0.935897; severity pool=9,171.795. Uncapped next wealth=16,450; f=0.716667.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,650 | 4,500 | 2,350 | 3,900 | 0.000000 | 0.188462 | -190 |
| Helpless#qiqi | 3,950 | 3,950 | 1,850 | 1,600 | 1,450 | 3,900 | 0.000000 | 0.165427 | -167 |
| Mokalover67#ILLIT | 4,250 | 4,250 | 1,900 | 1,900 | 0 | 3,900 | 0.045128 | 0.177991 | -134 |
| 1xgoofy#56719 | 4,600 | 4,600 | 1,100 | 1,100 | 1,300 | 3,900 | 0.000000 | 0.192650 | -194 |
| VorteXx#Val | 4,450 | 4,450 | 1,200 | 1,200 | 1,050 | 3,900 | 0.000000 | 0.186368 | -188 |

**TEAM_2:** L=8,800; H=19,500; U=31,000; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=35,400; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 4,300 | 4,300 | 4,000 | 3,900 | 0.000000 | 0.000000 | +0 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 4,800 | 4,550 | 650 | 3,900 | 0.000000 | 0.023333 | -24 |
| Osmin#NA1 | 4,250 | 0 | 4,750 | 4,500 | 3,750 | 3,900 | 0.581887 | 0.000000 | +586 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 350 | 3,900 | 0.000000 | 0.021795 | -22 |
| ternstyle#GIGI | 6,100 | 0 | 6,450 | 6,300 | 2,750 | 3,900 | 0.000000 | 0.000000 | +0 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484722 | 14.943s | Osmin#NA1 | ZETA 3y5#213 | 4,500 | 23.26 | 98.08 | 121.34 | 189.95 |
| 484723 | 15.581s | Osmin#NA1 | 1xgoofy#56719 | 4,600 | 23.78 | 100.26 | 124.04 | 194.18 |
| 484724 | 15.855s | Osmin#NA1 | Helpless#qiqi | 3,950 | 20.42 | 86.10 | 106.51 | 166.74 |
| 484719 | 28.759s | DoubleBl1nd#BEEF | ZETA 3y5#213 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484720 | 46.955s | Mokalover67#ILLIT | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 21.97 |
| 484721 | 47.091s | Mokalover67#ILLIT | DoubleBl1nd#BEEF | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484725 | 77.337s | Osmin#NA1 | VorteXx#Val | 4,450 | 23.00 | 96.99 | 120.00 | 187.84 |
| 484726 | 81.148s | Osmin#NA1 | Mokalover67#ILLIT | 4,250 | 21.97 | 92.64 | 114.60 | 179.40 |

### Round 7: Team B Elimination Win

Pistol winner: TEAM_2. Plant time: none.

**TEAM_1:** L=10,300; H=19,500; U=19,300; D=200; restorable Q=200. Observed gap=3,450; activation=0.051282; severity pool=176.923. Uncapped next wealth=19,950; f=0.550000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,650 | 4,500 | 700 | 3,900 | 0.000000 | 0.150000 | -151 |
| Helpless#qiqi | 1,600 | 1,600 | 4,200 | 3,950 | 50 | 3,900 | 0.000000 | 0.053333 | -54 |
| Mokalover67#ILLIT | 1,900 | 1,900 | 1,900 | 1,900 | 800 | 3,900 | 0.000000 | 0.063333 | -64 |
| 1xgoofy#56719 | 1,100 | 1,100 | 2,500 | 2,500 | 1,700 | 3,900 | 0.000000 | 0.036667 | -37 |
| VorteXx#Val | 1,200 | 1,200 | 3,850 | 3,850 | 0 | 3,900 | 0.000000 | 0.040000 | -40 |

**TEAM_2:** L=0; H=19,500; U=42,650; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=49,550; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 5,100 | 5,100 | 6,000 | 3,900 | 0.033651 | 0.000000 | +34 |
| DoubleBl1nd#BEEF | 4,550 | 0 | 5,100 | 4,850 | 3,100 | 3,900 | 0.007211 | 0.000000 | +7 |
| Osmin#NA1 | 4,500 | 0 | 5,050 | 4,800 | 5,850 | 3,900 | 0.000000 | 0.000000 | +0 |
| Najumi#NPC | 4,250 | 0 | 5,050 | 5,050 | 3,550 | 3,900 | 0.011417 | 0.000000 | +12 |
| ternstyle#GIGI | 6,300 | 0 | 6,750 | 6,600 | 4,650 | 3,900 | 0.009615 | 0.000000 | +10 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484727 | 19.173s | ternstyle#GIGI | Helpless#qiqi | 1,600 | 8.27 | 1.42 | 9.69 | 53.76 |
| 484728 | 23.017s | DoubleBl1nd#BEEF | VorteXx#Val | 1,200 | 6.20 | 1.07 | 7.27 | 40.32 |
| 484729 | 41.060s | Najumi#NPC | Mokalover67#ILLIT | 1,900 | 9.82 | 1.69 | 11.51 | 63.83 |
| 484730 | 63.934s | NPrightdolphin#NA1 | ZETA 3y5#213 | 4,500 | 23.26 | 4.00 | 27.26 | 151.19 |
| 484731 | 71.728s | NPrightdolphin#NA1 | 1xgoofy#56719 | 1,100 | 5.69 | 0.98 | 6.66 | 36.96 |

### Round 8: Team A Elimination Win

Pistol winner: TEAM_2. Plant time: 23.001.

**TEAM_1:** L=8,450; H=19,500; U=29,550; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=35,800; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,300 | 4,150 | 250 | 3,900 | 0.026154 | 0.023077 | +3 |
| Helpless#qiqi | 3,950 | 3,950 | 4,800 | 4,550 | 1,850 | 3,900 | 0.000000 | 0.020256 | -20 |
| Mokalover67#ILLIT | 1,900 | 0 | 5,400 | 5,400 | 3,700 | 3,900 | 0.059744 | 0.000000 | +60 |
| 1xgoofy#56719 | 2,500 | 0 | 5,400 | 5,400 | 2,100 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 3,850 | 0 | 6,250 | 6,250 | 2,150 | 3,900 | 0.049487 | 0.000000 | +50 |

**TEAM_2:** L=26,400; H=19,500; U=30,100; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=34,600; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 5,100 | 5,100 | 4,100 | 4,100 | 3,600 | 3,900 | 0.000000 | 0.026154 | -26 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 800 | 3,900 | 0.000000 | 0.024872 | -25 |
| Osmin#NA1 | 4,800 | 4,800 | 4,750 | 4,500 | 3,600 | 3,900 | 0.023077 | 0.024615 | -2 |
| Najumi#NPC | 5,050 | 5,050 | 4,250 | 4,250 | 1,900 | 3,900 | 0.000000 | 0.025897 | -26 |
| ternstyle#GIGI | 6,600 | 6,600 | 6,450 | 6,300 | 700 | 3,900 | 0.020256 | 0.033846 | -14 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484732 | 10.369s | ternstyle#GIGI | Helpless#qiqi | 3,950 | 20.42 | 0.00 | 20.42 | 20.42 |
| 484736 | 16.332s | ZETA 3y5#213 | NPrightdolphin#NA1 | 5,100 | 26.36 | 0.00 | 26.36 | 26.36 |
| 484738 | 25.862s | VorteXx#Val | DoubleBl1nd#BEEF | 4,850 | 25.07 | 0.00 | 25.07 | 25.07 |
| 484733 | 25.990s | Mokalover67#ILLIT | Najumi#NPC | 5,050 | 26.10 | 0.00 | 26.10 | 26.10 |
| 484734 | 31.537s | Mokalover67#ILLIT | ternstyle#GIGI | 6,600 | 34.11 | 0.00 | 34.11 | 34.11 |
| 484737 | 34.224s | Osmin#NA1 | ZETA 3y5#213 | 4,500 | 23.26 | 0.00 | 23.26 | 23.26 |
| 484739 | 34.306s | VorteXx#Val | Osmin#NA1 | 4,800 | 24.81 | 0.00 | 24.81 | 24.81 |
| 484735 | 37.469s | Mokalover67#ILLIT | ternstyle#GIGI | 0 | 0.00 | 0.00 | 0.00 | 0.00 |

### Round 9: Team A Elimination Win

Pistol winner: TEAM_2. Plant time: none.

**TEAM_1:** L=14,100; H=19,500; U=32,750; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=38,050; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,300 | 4,150 | 2,450 | 3,900 | 0.000000 | 0.021282 | -21 |
| Helpless#qiqi | 4,550 | 4,550 | 4,800 | 4,550 | 700 | 3,900 | 0.032308 | 0.023333 | +9 |
| Mokalover67#ILLIT | 5,400 | 5,400 | 4,600 | 4,600 | 2,250 | 3,900 | 0.000000 | 0.027692 | -28 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 4,800 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 6,250 | 0 | 6,100 | 6,100 | 3,050 | 3,900 | 0.090769 | 0.000000 | +91 |

**TEAM_2:** L=24,000; H=19,500; U=24,000; D=0; restorable Q=0. Observed gap=3,800; activation=0.000000; severity pool=0.000. Uncapped next wealth=24,450; f=0.335714.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 1,900 | 3,900 | 0.044615 | 0.091612 | -47 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 2,400 | 2,150 | 1,200 | 3,900 | 0.000000 | 0.108370 | -109 |
| Osmin#NA1 | 4,500 | 4,500 | 4,400 | 4,150 | 2,200 | 3,900 | 0.000000 | 0.100549 | -101 |
| Najumi#NPC | 4,250 | 4,250 | 3,150 | 3,150 | 1,850 | 3,900 | 0.000000 | 0.094963 | -96 |
| ternstyle#GIGI | 6,300 | 6,300 | 2,750 | 2,600 | 1,150 | 3,900 | 0.027692 | 0.140769 | -114 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484740 | 9.914s | ternstyle#GIGI | Mokalover67#ILLIT | 5,400 | 27.91 | 0.00 | 27.91 | 27.91 |
| 484741 | 13.618s | Helpless#qiqi | ternstyle#GIGI | 6,300 | 32.56 | 0.00 | 32.56 | 141.88 |
| 484747 | 15.234s | NPrightdolphin#NA1 | ZETA 3y5#213 | 4,150 | 21.45 | 0.00 | 21.45 | 21.45 |
| 484743 | 15.498s | VorteXx#Val | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 95.72 |
| 484748 | 17.855s | NPrightdolphin#NA1 | Helpless#qiqi | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484744 | 21.699s | VorteXx#Val | DoubleBl1nd#BEEF | 4,850 | 25.07 | 0.00 | 25.07 | 109.23 |
| 484742 | 71.848s | Osmin#NA1 | Mokalover67#ILLIT | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484745 | 75.627s | VorteXx#Val | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 101.35 |
| 484746 | 79.563s | VorteXx#Val | NPrightdolphin#NA1 | 4,100 | 21.19 | 0.00 | 21.19 | 92.34 |

### Round 10: Team A Elimination Win

Pistol winner: TEAM_2. Plant time: 32.235.

**TEAM_1:** L=14,800; H=19,500; U=34,000; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=39,800; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 2,100 | 3,900 | 0.034615 | 0.021282 | +13 |
| Helpless#qiqi | 4,550 | 4,550 | 4,800 | 4,550 | 2,750 | 3,900 | 0.000000 | 0.023333 | -24 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 1,950 | 3,900 | 0.000000 | 0.000000 | +0 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 7,200 | 3,900 | 0.048205 | 0.000000 | +49 |
| VorteXx#Val | 6,100 | 6,100 | 6,250 | 6,250 | 500 | 3,900 | 0.000000 | 0.031282 | -32 |

**TEAM_2:** L=16,150; H=19,500; U=22,300; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=24,350; f=0.340476.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 1,100 | 3,900 | 0.044615 | 0.092613 | -48 |
| DoubleBl1nd#BEEF | 2,150 | 2,150 | 4,450 | 4,200 | 50 | 3,900 | 0.000000 | 0.048565 | -49 |
| Osmin#NA1 | 4,150 | 4,150 | 4,750 | 4,500 | 750 | 3,900 | 0.000000 | 0.093742 | -94 |
| Najumi#NPC | 3,150 | 3,150 | 4,250 | 4,250 | 900 | 3,900 | 0.031282 | 0.071154 | -40 |
| ternstyle#GIGI | 2,600 | 2,600 | 4,650 | 4,500 | 0 | 3,900 | 0.000000 | 0.058730 | -59 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484754 | 16.982s | Najumi#NPC | VorteXx#Val | 6,100 | 31.53 | 0.00 | 31.53 | 31.53 |
| 484755 | 34.555s | NPrightdolphin#NA1 | Helpless#qiqi | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484749 | 35.648s | 1xgoofy#56719 | Najumi#NPC | 3,150 | 16.28 | 0.00 | 16.28 | 71.72 |
| 484750 | 36.624s | 1xgoofy#56719 | DoubleBl1nd#BEEF | 2,150 | 11.11 | 0.00 | 11.11 | 48.95 |
| 484752 | 48.477s | ZETA 3y5#213 | Osmin#NA1 | 4,150 | 21.45 | 0.00 | 21.45 | 94.48 |
| 484753 | 49.165s | ZETA 3y5#213 | ternstyle#GIGI | 2,600 | 13.44 | 0.00 | 13.44 | 59.20 |
| 484756 | 57.065s | NPrightdolphin#NA1 | ZETA 3y5#213 | 4,150 | 21.45 | 0.00 | 21.45 | 21.45 |
| 484757 | 63.055s | NPrightdolphin#NA1 | ZETA 3y5#213 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484751 | 63.381s | 1xgoofy#56719 | NPrightdolphin#NA1 | 4,100 | 21.19 | 0.00 | 21.19 | 93.35 |

### Round 11: Team A Detonate Win

Pistol winner: TEAM_2. Plant time: 44.564.

**TEAM_1:** L=4,550; H=19,500; U=40,100; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=46,200; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,650 | 4,500 | 4,250 | 3,900 | 0.000000 | 0.000000 | +0 |
| Helpless#qiqi | 4,550 | 4,550 | 5,100 | 4,850 | 2,000 | 3,900 | 0.066410 | 0.023333 | +43 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 4,950 | 3,900 | 0.000000 | 0.000000 | +0 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 5,400 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 6,250 | 0 | 6,250 | 6,250 | 4,000 | 3,900 | 0.023077 | 0.000000 | +23 |

**TEAM_2:** L=17,450; H=19,500; U=20,700; D=0; restorable Q=0. Observed gap=750; activation=0.000000; severity pool=0.000. Uncapped next wealth=21,250; f=0.488095.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 1,400 | 3,900 | 0.000000 | 0.000000 | +0 |
| DoubleBl1nd#BEEF | 4,200 | 4,200 | 4,100 | 3,850 | 50 | 3,900 | 0.000000 | 0.126667 | -128 |
| Osmin#NA1 | 4,500 | 4,500 | 4,100 | 3,850 | 150 | 3,900 | 0.023333 | 0.135714 | -113 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 250 | 3,900 | 0.000000 | 0.128175 | -129 |
| ternstyle#GIGI | 4,500 | 4,500 | 3,400 | 3,250 | 100 | 3,900 | 0.000000 | 0.135714 | -137 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484758 | 10.132s | Helpless#qiqi | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 129.19 |
| 484759 | 11.048s | Helpless#qiqi | DoubleBl1nd#BEEF | 4,200 | 21.71 | 0.00 | 21.71 | 127.67 |
| 484760 | 18.720s | Helpless#qiqi | ternstyle#GIGI | 4,500 | 23.26 | 0.00 | 23.26 | 136.79 |
| 484761 | 24.669s | Osmin#NA1 | Helpless#qiqi | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484762 | 52.367s | VorteXx#Val | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 136.79 |

### Round 12: zero (pistol_half_or_ot_boundary)

### Round 13: zero (pistol_half_or_ot_boundary)

### Round 14: Team A Defuse Win

Pistol winner: TEAM_1. Plant time: 77.45.

**TEAM_1:** L=4,550; H=10,600; U=20,250; D=0; restorable Q=0. Observed gap=600; activation=0.000000; severity pool=0.000. Uncapped next wealth=27,650; f=0.183333.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 0 | 2,250 | 2,100 | 3,300 | 2,100 | 0.003077 | 0.000000 | +3 |
| Helpless#qiqi | 1,050 | 1,050 | 4,450 | 4,200 | 1,400 | 1,050 | 0.005897 | 0.015256 | -9 |
| Mokalover67#ILLIT | 2,700 | 2,700 | 3,350 | 3,350 | 100 | 2,700 | 0.000000 | 0.039231 | -40 |
| 1xgoofy#56719 | 800 | 800 | 4,600 | 4,600 | 1,800 | 1,000 | 0.000000 | 0.011624 | -12 |
| VorteXx#Val | 3,750 | 0 | 3,150 | 3,150 | 3,650 | 3,750 | 0.004615 | 0.000000 | +5 |

**TEAM_2:** L=2,650; H=19,500; U=21,350; D=0; restorable Q=0. Observed gap=350; activation=0.000000; severity pool=0.000. Uncapped next wealth=23,100; f=0.400000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 600 | 600 | 4,300 | 4,300 | 400 | 3,900 | 0.005385 | 0.015385 | -10 |
| DoubleBl1nd#BEEF | 0 | 0 | 4,800 | 4,550 | 50 | 3,900 | 0.000000 | 0.000000 | +0 |
| Osmin#NA1 | 200 | 200 | 4,500 | 4,250 | 150 | 3,900 | 0.000000 | 0.005128 | -5 |
| Najumi#NPC | 700 | 700 | 4,250 | 4,250 | 1,100 | 3,900 | 0.013846 | 0.017949 | -4 |
| ternstyle#GIGI | 1,150 | 1,150 | 3,700 | 3,550 | 500 | 3,900 | 0.004103 | 0.029487 | -26 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484780 | 17.301s | ternstyle#GIGI | 1xgoofy#56719 | 800 | 4.14 | 0.00 | 4.14 | 11.72 |
| 484784 | 27.210s | Najumi#NPC | Mokalover67#ILLIT | 2,700 | 13.96 | 0.00 | 13.96 | 39.54 |
| 484781 | 28.428s | ZETA 3y5#213 | DoubleBl1nd#BEEF | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484783 | 39.175s | Helpless#qiqi | ternstyle#GIGI | 1,150 | 5.94 | 0.00 | 5.94 | 29.72 |
| 484785 | 51.248s | VorteXx#Val | Osmin#NA1 | 200 | 1.03 | 0.00 | 1.03 | 5.17 |
| 484787 | 55.669s | NPrightdolphin#NA1 | Helpless#qiqi | 1,050 | 5.43 | 0.00 | 5.43 | 15.38 |
| 484786 | 82.611s | VorteXx#Val | Najumi#NPC | 700 | 3.62 | 0.00 | 3.62 | 18.09 |
| 484782 | 84.352s | ZETA 3y5#213 | NPrightdolphin#NA1 | 600 | 3.10 | 0.00 | 3.10 | 15.51 |

### Round 15: Team B Elimination Win

Pistol winner: TEAM_1. Plant time: 49.782.

**TEAM_1:** L=17,400; H=19,500; U=21,050; D=0; restorable Q=0. Observed gap=3,600; activation=0.000000; severity pool=0.000. Uncapped next wealth=22,200; f=0.442857.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 2,100 | 4,650 | 4,500 | 1,150 | 3,900 | 0.000000 | 0.058462 | -59 |
| Helpless#qiqi | 4,200 | 4,200 | 3,950 | 3,700 | 0 | 3,900 | 0.000000 | 0.116923 | -118 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 1,900 | 1,900 | 800 | 3,900 | 0.000000 | 0.093260 | -94 |
| 1xgoofy#56719 | 4,600 | 4,600 | 2,500 | 2,500 | 1,700 | 3,900 | 0.000000 | 0.128059 | -129 |
| VorteXx#Val | 3,150 | 3,150 | 4,450 | 4,450 | 1,500 | 3,900 | 0.000000 | 0.087692 | -88 |

**TEAM_2:** L=0; H=19,500; U=36,050; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=38,150; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 0 | 4,300 | 4,300 | 3,700 | 3,900 | 0.051538 | 0.000000 | +52 |
| DoubleBl1nd#BEEF | 4,550 | 0 | 4,800 | 4,550 | 2,150 | 3,900 | 0.000000 | 0.000000 | +0 |
| Osmin#NA1 | 4,250 | 0 | 4,750 | 4,500 | 3,400 | 3,900 | 0.037692 | 0.000000 | +38 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 3,950 | 3,900 | 0.000000 | 0.000000 | +0 |
| ternstyle#GIGI | 3,550 | 0 | 4,150 | 4,000 | 3,350 | 3,900 | 0.000000 | 0.000000 | +0 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484790 | 19.315s | NPrightdolphin#NA1 | 1xgoofy#56719 | 4,600 | 23.78 | 0.00 | 23.78 | 129.07 |
| 484791 | 20.069s | NPrightdolphin#NA1 | Mokalover67#ILLIT | 3,350 | 17.32 | 0.00 | 17.32 | 94.00 |
| 484788 | 23.760s | Osmin#NA1 | Helpless#qiqi | 4,200 | 21.71 | 0.00 | 21.71 | 117.85 |
| 484792 | 27.304s | NPrightdolphin#NA1 | ZETA 3y5#213 | 2,100 | 10.85 | 0.00 | 10.85 | 58.92 |
| 484789 | 82.311s | Osmin#NA1 | VorteXx#Val | 3,150 | 16.28 | 0.00 | 16.28 | 88.39 |

### Round 16: Team B Elimination Win

Pistol winner: TEAM_1. Plant time: 42.804.

**TEAM_1:** L=17,050; H=19,500; U=17,950; D=1,550; restorable Q=1,550. Observed gap=3,150; activation=0.397436; severity pool=1,251.923. Uncapped next wealth=19,000; f=0.595238.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 3,900 | 3,750 | 50 | 3,900 | 0.000000 | 0.160440 | -162 |
| Helpless#qiqi | 3,700 | 3,700 | 1,700 | 1,450 | 1,100 | 3,900 | 0.020513 | 0.131917 | -112 |
| Mokalover67#ILLIT | 1,900 | 1,900 | 3,350 | 3,350 | 150 | 3,900 | 0.000000 | 0.067741 | -68 |
| 1xgoofy#56719 | 2,500 | 2,500 | 4,400 | 4,400 | 100 | 3,900 | 0.023333 | 0.089133 | -66 |
| VorteXx#Val | 4,450 | 4,450 | 4,450 | 4,450 | 200 | 3,900 | 0.022051 | 0.158657 | -138 |

**TEAM_2:** L=12,850; H=19,500; U=38,850; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=41,750; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 2,900 | 3,900 | 0.000000 | 0.022051 | -22 |
| DoubleBl1nd#BEEF | 4,550 | 4,550 | 5,100 | 4,850 | 3,850 | 3,900 | 0.039577 | 0.023333 | +16 |
| Osmin#NA1 | 4,500 | 0 | 4,750 | 4,500 | 5,700 | 3,900 | 0.072039 | 0.000000 | +73 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 4,300 | 3,900 | 0.040022 | 0.000000 | +40 |
| ternstyle#GIGI | 4,000 | 4,000 | 4,650 | 4,500 | 2,600 | 3,900 | 0.000000 | 0.020513 | -21 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484795 | 7.647s | Helpless#qiqi | ternstyle#GIGI | 4,000 | 20.68 | 0.00 | 20.68 | 20.68 |
| 484797 | 17.617s | Osmin#NA1 | Helpless#qiqi | 3,700 | 19.12 | 14.04 | 33.17 | 132.96 |
| 484800 | 32.447s | VorteXx#Val | NPrightdolphin#NA1 | 4,300 | 22.23 | 0.00 | 22.23 | 22.23 |
| 484793 | 39.235s | DoubleBl1nd#BEEF | VorteXx#Val | 4,450 | 23.00 | 16.89 | 39.89 | 159.91 |
| 484798 | 52.075s | Osmin#NA1 | Mokalover67#ILLIT | 1,900 | 9.82 | 7.21 | 17.03 | 68.28 |
| 484794 | 62.711s | 1xgoofy#56719 | DoubleBl1nd#BEEF | 4,550 | 23.52 | 0.00 | 23.52 | 23.52 |
| 484796 | 64.818s | Najumi#NPC | ZETA 3y5#213 | 4,500 | 23.26 | 17.08 | 40.34 | 161.71 |
| 484799 | 74.670s | Osmin#NA1 | 1xgoofy#56719 | 2,500 | 12.92 | 9.49 | 22.41 | 89.84 |

### Round 17: Team A Defuse Win

Pistol winner: TEAM_1. Plant time: 29.48.

**TEAM_1:** L=12,200; H=19,500; U=23,700; D=0; restorable Q=0. Observed gap=2,950; activation=0.000000; severity pool=0.000. Uncapped next wealth=26,450; f=0.240476.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 3,750 | 0 | 4,650 | 4,500 | 2,100 | 3,900 | 0.023077 | 0.000000 | +23 |
| Helpless#qiqi | 1,450 | 0 | 5,600 | 5,350 | 350 | 3,900 | 0.070000 | 0.000000 | +71 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 4,600 | 4,600 | 2,000 | 3,900 | 0.000000 | 0.058492 | -59 |
| 1xgoofy#56719 | 4,400 | 4,400 | 2,500 | 2,500 | 1,100 | 3,900 | 0.000000 | 0.076825 | -77 |
| VorteXx#Val | 4,450 | 4,450 | 2,350 | 2,350 | 1,600 | 3,900 | 0.021795 | 0.077698 | -56 |

**TEAM_2:** L=22,400; H=19,500; U=28,950; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=31,850; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 900 | 3,900 | 0.000000 | 0.022051 | -22 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 1,350 | 3,900 | 0.022564 | 0.024872 | -2 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 3,950 | 3,900 | 0.040000 | 0.023077 | +17 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 2,500 | 3,900 | 0.000000 | 0.021795 | -22 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 750 | 3,900 | 0.000000 | 0.023077 | -23 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484803 | 10.907s | Helpless#qiqi | ternstyle#GIGI | 4,500 | 23.26 | 0.00 | 23.26 | 23.26 |
| 484801 | 12.647s | DoubleBl1nd#BEEF | 1xgoofy#56719 | 4,400 | 22.74 | 0.00 | 22.74 | 77.43 |
| 484806 | 17.938s | Osmin#NA1 | Mokalover67#ILLIT | 3,350 | 17.32 | 0.00 | 17.32 | 58.96 |
| 484804 | 26.234s | Helpless#qiqi | DoubleBl1nd#BEEF | 4,850 | 25.07 | 0.00 | 25.07 | 25.07 |
| 484808 | 32.021s | VorteXx#Val | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 21.97 |
| 484805 | 35.800s | Helpless#qiqi | NPrightdolphin#NA1 | 4,300 | 22.23 | 0.00 | 22.23 | 22.23 |
| 484807 | 36.670s | Osmin#NA1 | VorteXx#Val | 4,450 | 23.00 | 0.00 | 23.00 | 78.31 |
| 484802 | 43.036s | ZETA 3y5#213 | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 23.26 |

### Round 18: Team A Elimination Win

Pistol winner: TEAM_1. Plant time: none.

**TEAM_1:** L=12,350; H=19,500; U=29,850; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=32,550; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 4,300 | 4,150 | 1,800 | 3,900 | 0.046923 | 0.023077 | +24 |
| Helpless#qiqi | 5,350 | 5,350 | 4,200 | 3,950 | 50 | 3,900 | 0.000000 | 0.027436 | -28 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 3,500 | 3,900 | 0.023077 | 0.000000 | +23 |
| 1xgoofy#56719 | 2,500 | 2,500 | 4,250 | 4,250 | 150 | 3,900 | 0.000000 | 0.012821 | -13 |
| VorteXx#Val | 2,350 | 0 | 5,250 | 5,250 | 4,850 | 3,900 | 0.044872 | 0.000000 | +45 |

**TEAM_2:** L=22,400; H=19,500; U=22,450; D=0; restorable Q=0. Observed gap=3,700; activation=0.000000; severity pool=0.000. Uncapped next wealth=23,400; f=0.385714.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 2,100 | 2,100 | 1,500 | 3,900 | 0.000000 | 0.107106 | -108 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 3,550 | 3,300 | 600 | 3,900 | 0.000000 | 0.120806 | -122 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 2,050 | 3,900 | 0.023077 | 0.112088 | -90 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,750 | 3,900 | 0.040256 | 0.105861 | -66 |
| ternstyle#GIGI | 4,500 | 4,500 | 2,750 | 2,600 | 750 | 3,900 | 0.000000 | 0.112088 | -113 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484812 | 4.153s | Najumi#NPC | Helpless#qiqi | 5,350 | 27.65 | 0.00 | 27.65 | 27.65 |
| 484810 | 48.552s | ZETA 3y5#213 | DoubleBl1nd#BEEF | 4,850 | 25.07 | 0.00 | 25.07 | 121.76 |
| 484811 | 52.281s | ZETA 3y5#213 | NPrightdolphin#NA1 | 4,300 | 22.23 | 0.00 | 22.23 | 107.95 |
| 484815 | 52.440s | Osmin#NA1 | ZETA 3y5#213 | 4,500 | 23.26 | 0.00 | 23.26 | 23.26 |
| 484816 | 60.093s | VorteXx#Val | ternstyle#GIGI | 4,500 | 23.26 | 0.00 | 23.26 | 112.98 |
| 484809 | 62.816s | Mokalover67#ILLIT | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 112.98 |
| 484813 | 65.277s | Najumi#NPC | ZETA 3y5#213 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484814 | 67.023s | Najumi#NPC | 1xgoofy#56719 | 2,500 | 12.92 | 0.00 | 12.92 | 12.92 |
| 484817 | 73.201s | VorteXx#Val | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 106.70 |

### Round 19: Team B Detonate Win

Pistol winner: TEAM_1. Plant time: 28.344.

**TEAM_1:** L=22,200; H=19,500; U=20,300; D=0; restorable Q=0. Observed gap=2,200; activation=0.000000; severity pool=0.000. Uncapped next wealth=22,800; f=0.414286.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 50 | 3,900 | 0.040000 | 0.109451 | -70 |
| Helpless#qiqi | 3,950 | 3,950 | 4,800 | 4,550 | 950 | 3,900 | 0.000000 | 0.104176 | -105 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 4,600 | 4,600 | 1,100 | 3,900 | 0.000000 | 0.121319 | -122 |
| 1xgoofy#56719 | 4,250 | 4,250 | 1,700 | 1,700 | 750 | 3,900 | 0.000000 | 0.112088 | -113 |
| VorteXx#Val | 5,250 | 5,250 | 4,450 | 4,450 | 150 | 3,900 | 0.024103 | 0.138462 | -115 |

**TEAM_2:** L=12,500; H=19,500; U=26,200; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=28,100; f=0.161905.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 2,100 | 2,100 | 4,300 | 4,300 | 900 | 3,900 | 0.000000 | 0.028205 | -28 |
| DoubleBl1nd#BEEF | 3,300 | 3,300 | 4,550 | 4,300 | 150 | 3,900 | 0.068974 | 0.044322 | +25 |
| Osmin#NA1 | 4,500 | 4,500 | 4,750 | 4,500 | 850 | 3,900 | 0.000000 | 0.060440 | -61 |
| Najumi#NPC | 4,250 | 0 | 4,250 | 4,250 | 4,800 | 3,900 | 0.023590 | 0.000000 | +24 |
| ternstyle#GIGI | 2,600 | 2,600 | 4,200 | 4,050 | 0 | 3,900 | 0.000000 | 0.034921 | -35 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484818 | 14.985s | DoubleBl1nd#BEEF | Helpless#qiqi | 3,950 | 20.42 | 0.00 | 20.42 | 105.00 |
| 484819 | 18.942s | DoubleBl1nd#BEEF | 1xgoofy#56719 | 4,250 | 21.97 | 0.00 | 21.97 | 112.98 |
| 484825 | 26.289s | VorteXx#Val | NPrightdolphin#NA1 | 2,100 | 10.85 | 0.00 | 10.85 | 28.43 |
| 484824 | 32.566s | Najumi#NPC | Mokalover67#ILLIT | 4,600 | 23.78 | 0.00 | 23.78 | 122.28 |
| 484826 | 39.575s | VorteXx#Val | ternstyle#GIGI | 2,600 | 13.44 | 0.00 | 13.44 | 35.20 |
| 484821 | 39.607s | ZETA 3y5#213 | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 60.92 |
| 484820 | 49.640s | DoubleBl1nd#BEEF | VorteXx#Val | 5,250 | 27.14 | 0.00 | 27.14 | 139.56 |
| 484822 | 56.576s | ZETA 3y5#213 | DoubleBl1nd#BEEF | 3,300 | 17.06 | 0.00 | 17.06 | 44.67 |
| 484823 | 74.476s | ZETA 3y5#213 | ZETA 3y5#213 | 4,150 | 0.00 | 0.00 | 0.00 | 110.32 |

### Round 20: Team B Elimination Win

Pistol winner: TEAM_1. Plant time: 56.09.

**TEAM_1:** L=19,800; H=19,500; U=17,400; D=2,100; restorable Q=2,100. Observed gap=5,800; activation=0.538462; severity pool=3,123.077. Uncapped next wealth=17,400; f=0.671429.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 4,500 | 2,250 | 2,100 | 1,200 | 3,900 | 0.043846 | 0.178022 | -135 |
| Helpless#qiqi | 4,550 | 4,550 | 2,700 | 2,450 | 1,250 | 3,900 | 0.043846 | 0.180000 | -137 |
| Mokalover67#ILLIT | 4,600 | 4,600 | 3,600 | 3,600 | 50 | 3,900 | 0.000000 | 0.181978 | -183 |
| 1xgoofy#56719 | 1,700 | 1,700 | 3,600 | 3,600 | 250 | 3,900 | 0.000000 | 0.067253 | -68 |
| VorteXx#Val | 4,450 | 4,450 | 1,950 | 1,950 | 950 | 3,900 | 0.000000 | 0.176044 | -177 |

**TEAM_2:** L=17,100; H=19,500; U=25,250; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=27,900; f=0.171429.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,300 | 4,300 | 100 | 3,900 | 0.000000 | 0.059853 | -60 |
| DoubleBl1nd#BEEF | 4,300 | 0 | 5,100 | 4,850 | 2,250 | 3,900 | 0.201559 | 0.000000 | +203 |
| Osmin#NA1 | 4,500 | 4,500 | 4,500 | 4,250 | 50 | 3,900 | 0.000000 | 0.062637 | -63 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,450 | 3,900 | 0.000000 | 0.059158 | -60 |
| ternstyle#GIGI | 4,050 | 4,050 | 4,650 | 4,500 | 1,900 | 3,900 | 0.060137 | 0.056374 | +4 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484834 | 26.110s | Helpless#qiqi | NPrightdolphin#NA1 | 4,300 | 22.23 | 0.00 | 22.23 | 60.33 |
| 484832 | 29.899s | ZETA 3y5#213 | Osmin#NA1 | 4,500 | 23.26 | 0.00 | 23.26 | 63.13 |
| 484833 | 31.118s | ZETA 3y5#213 | ternstyle#GIGI | 4,050 | 20.93 | 0.00 | 20.93 | 56.82 |
| 484828 | 32.177s | DoubleBl1nd#BEEF | ZETA 3y5#213 | 4,500 | 23.26 | 36.69 | 59.95 | 179.43 |
| 484829 | 36.598s | DoubleBl1nd#BEEF | Mokalover67#ILLIT | 4,600 | 23.78 | 37.50 | 61.28 | 183.42 |
| 484835 | 45.299s | Helpless#qiqi | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 59.63 |
| 484827 | 47.065s | ternstyle#GIGI | Helpless#qiqi | 4,550 | 23.52 | 37.10 | 60.61 | 181.43 |
| 484830 | 49.689s | DoubleBl1nd#BEEF | 1xgoofy#56719 | 1,700 | 8.79 | 13.86 | 22.65 | 67.79 |
| 484836 | 68.067s | VorteXx#Val | ternstyle#GIGI | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484831 | 78.242s | DoubleBl1nd#BEEF | VorteXx#Val | 4,450 | 23.00 | 36.28 | 59.28 | 177.44 |

### Round 21: Team B Elimination Win

Pistol winner: TEAM_1. Plant time: 49.807.

**TEAM_1:** L=13,700; H=19,500; U=19,050; D=450; restorable Q=450. Observed gap=2,000; activation=0.115385; severity pool=230.769. Uncapped next wealth=19,950; f=0.550000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 2,100 | 2,100 | 4,650 | 4,500 | 50 | 3,900 | 0.000000 | 0.070000 | -71 |
| Helpless#qiqi | 2,450 | 2,450 | 4,450 | 4,200 | 300 | 3,900 | 0.045128 | 0.081667 | -37 |
| Mokalover67#ILLIT | 3,600 | 3,600 | 3,350 | 3,350 | 0 | 3,900 | 0.043590 | 0.120000 | -77 |
| 1xgoofy#56719 | 3,600 | 3,600 | 2,500 | 2,500 | 1,050 | 3,900 | 0.000000 | 0.120000 | -121 |
| VorteXx#Val | 1,950 | 1,950 | 3,850 | 3,850 | 150 | 3,900 | 0.000000 | 0.065000 | -66 |

**TEAM_2:** L=22,150; H=19,500; U=23,050; D=0; restorable Q=0. Observed gap=800; activation=0.000000; severity pool=0.000. Uncapped next wealth=24,950; f=0.311905.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 4,300 | 4,300 | 3,500 | 3,500 | 100 | 3,900 | 0.000000 | 0.090830 | -92 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 5,100 | 4,850 | 1,650 | 3,900 | 0.057823 | 0.102448 | -45 |
| Osmin#NA1 | 4,250 | 4,250 | 3,750 | 3,500 | 50 | 3,900 | 0.012583 | 0.089774 | -78 |
| Najumi#NPC | 4,250 | 4,250 | 4,250 | 4,250 | 1,200 | 3,900 | 0.000000 | 0.089774 | -90 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 1,350 | 3,900 | 0.011684 | 0.095055 | -84 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484837 | 31.512s | ternstyle#GIGI | VorteXx#Val | 1,950 | 10.08 | 1.70 | 11.78 | 65.51 |
| 484847 | 34.235s | Osmin#NA1 | ZETA 3y5#213 | 2,100 | 10.85 | 1.83 | 12.68 | 70.55 |
| 484845 | 43.106s | Helpless#qiqi | ternstyle#GIGI | 4,500 | 23.26 | 0.00 | 23.26 | 95.81 |
| 484843 | 57.746s | Mokalover67#ILLIT | Najumi#NPC | 4,250 | 21.97 | 0.00 | 21.97 | 90.49 |
| 484844 | 69.746s | Mokalover67#ILLIT | Osmin#NA1 | 4,250 | 21.97 | 0.00 | 21.97 | 90.49 |
| 484846 | 76.135s | Helpless#qiqi | NPrightdolphin#NA1 | 4,300 | 22.23 | 0.00 | 22.23 | 91.55 |
| 484838 | 78.124s | DoubleBl1nd#BEEF | Mokalover67#ILLIT | 3,600 | 18.61 | 3.13 | 21.74 | 120.95 |
| 484839 | 79.856s | DoubleBl1nd#BEEF | 1xgoofy#56719 | 3,600 | 18.61 | 3.13 | 21.74 | 120.95 |
| 484840 | 87.950s | DoubleBl1nd#BEEF | Helpless#qiqi | 2,450 | 12.66 | 2.13 | 14.80 | 82.31 |
| 484841 | 89.351s | DoubleBl1nd#BEEF | ZETA 3y5#213 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484842 | 95.995s | DoubleBl1nd#BEEF | DoubleBl1nd#BEEF | 4,850 | 0.00 | 0.00 | 0.00 | 103.26 |

### Round 22: Team A Time Win

Pistol winner: TEAM_1. Plant time: none.

**TEAM_1:** L=3,350; H=19,500; U=28,300; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=32,250; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,500 | 0 | 4,300 | 4,150 | 2,650 | 3,900 | 0.185436 | 0.000000 | +187 |
| Helpless#qiqi | 4,200 | 0 | 5,100 | 4,850 | 1,700 | 3,900 | 0.094384 | 0.000000 | +95 |
| Mokalover67#ILLIT | 3,350 | 3,350 | 4,600 | 4,600 | 1,450 | 3,900 | 0.000000 | 0.017179 | -17 |
| 1xgoofy#56719 | 2,500 | 0 | 5,400 | 5,400 | 650 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 3,850 | 0 | 4,450 | 4,450 | 2,350 | 3,900 | 0.077728 | 0.000000 | +78 |

**TEAM_2:** L=16,100; H=19,500; U=17,750; D=1,750; restorable Q=1,750. Observed gap=11,950; activation=0.448718; severity pool=5,362.179. Uncapped next wealth=18,350; f=0.626190.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 3,500 | 3,500 | 600 | 600 | 1,600 | 3,900 | 0.000000 | 0.130342 | -131 |
| DoubleBl1nd#BEEF | 4,850 | 4,850 | 2,150 | 1,900 | 1,800 | 3,900 | 0.000000 | 0.180617 | -182 |
| Osmin#NA1 | 3,500 | 3,500 | 700 | 450 | 1,950 | 3,900 | 0.000000 | 0.130342 | -131 |
| Najumi#NPC | 4,250 | 4,250 | 700 | 700 | 2,650 | 3,900 | 0.000000 | 0.158272 | -160 |
| ternstyle#GIGI | 4,500 | 0 | 4,650 | 4,500 | 2,200 | 3,900 | 0.017179 | 0.000000 | +17 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484852 | 73.500s | VorteXx#Val | NPrightdolphin#NA1 | 3,500 | 18.09 | 60.25 | 78.34 | 131.37 |
| 484849 | 84.290s | ZETA 3y5#213 | Osmin#NA1 | 3,500 | 18.09 | 60.25 | 78.34 | 131.37 |
| 484850 | 86.290s | ZETA 3y5#213 | DoubleBl1nd#BEEF | 4,850 | 25.07 | 83.49 | 108.56 | 182.05 |
| 484851 | 86.581s | Helpless#qiqi | Najumi#NPC | 4,250 | 21.97 | 73.16 | 95.13 | 159.53 |
| 484848 | 90.550s | ternstyle#GIGI | Mokalover67#ILLIT | 3,350 | 17.32 | 0.00 | 17.32 | 17.32 |

### Round 23: Team A Elimination Win

Pistol winner: TEAM_1. Plant time: none.

**TEAM_1:** L=4,150; H=19,500; U=37,600; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=41,900; f=0.000000.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ZETA 3y5#213 | 4,150 | 4,150 | 4,650 | 4,500 | 1,800 | 3,900 | 0.002308 | 0.021282 | -19 |
| Helpless#qiqi | 4,850 | 0 | 5,100 | 4,850 | 3,400 | 3,900 | 0.026667 | 0.000000 | +27 |
| Mokalover67#ILLIT | 4,600 | 0 | 4,600 | 4,600 | 4,050 | 3,900 | 0.000000 | 0.000000 | +0 |
| 1xgoofy#56719 | 5,400 | 0 | 5,400 | 5,400 | 3,650 | 3,900 | 0.000000 | 0.000000 | +0 |
| VorteXx#Val | 4,450 | 0 | 4,450 | 4,450 | 5,200 | 3,900 | 0.012821 | 0.000000 | +13 |

**TEAM_2:** L=8,150; H=19,500; U=21,550; D=0; restorable Q=0. Observed gap=0; activation=0.000000; severity pool=0.000. Uncapped next wealth=23,650; f=0.373810.

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | 600 | 600 | 4,300 | 4,300 | 100 | 3,900 | 0.000000 | 0.014579 | -15 |
| DoubleBl1nd#BEEF | 1,900 | 1,900 | 4,550 | 4,300 | 50 | 3,900 | 0.000000 | 0.046166 | -47 |
| Osmin#NA1 | 450 | 450 | 4,500 | 4,250 | 100 | 3,900 | 0.000000 | 0.010934 | -11 |
| Najumi#NPC | 700 | 700 | 4,250 | 4,250 | 1,050 | 3,900 | 0.000000 | 0.017009 | -17 |
| ternstyle#GIGI | 4,500 | 4,500 | 4,650 | 4,500 | 750 | 3,900 | 0.021282 | 0.109341 | -89 |

| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |
|---|---:|---|---|---:|---:|---:|---:|---:|
| 484857 | 20.581s | VorteXx#Val | DoubleBl1nd#BEEF | 1,900 | 9.82 | 0.00 | 9.82 | 46.53 |
| 484853 | 24.363s | ternstyle#GIGI | ZETA 3y5#213 | 4,150 | 21.45 | 0.00 | 21.45 | 21.45 |
| 484854 | 37.140s | ZETA 3y5#213 | Osmin#NA1 | 450 | 2.33 | 0.00 | 2.33 | 11.02 |
| 484859 | 37.548s | NPrightdolphin#NA1 | ZETA 3y5#213 | 0 | 0.00 | 0.00 | 0.00 | 0.00 |
| 484858 | 38.323s | VorteXx#Val | NPrightdolphin#NA1 | 600 | 3.10 | 0.00 | 3.10 | 14.69 |
| 484855 | 39.340s | Helpless#qiqi | Najumi#NPC | 700 | 3.62 | 0.00 | 3.62 | 17.14 |
| 484856 | 41.060s | Helpless#qiqi | ternstyle#GIGI | 4,500 | 23.26 | 0.00 | 23.26 | 110.21 |

### Round 24: zero (final_round)

## Match economy totals

Sum of rounded player-round economy nets; these are not full Impact scores.

| Player | Team | Econ points |
|---|---|---:|
| Osmin#NA1 | TEAM_2 | -100 |
| DoubleBl1nd#BEEF | TEAM_2 | -525 |
| NPrightdolphin#NA1 | TEAM_2 | -541 |
| Najumi#NPC | TEAM_2 | -682 |
| ternstyle#GIGI | TEAM_2 | -689 |
| ZETA 3y5#213 | TEAM_1 | -718 |
| VorteXx#Val | TEAM_1 | -727 |
| Helpless#qiqi | TEAM_1 | -761 |
| Mokalover67#ILLIT | TEAM_1 | -923 |
| 1xgoofy#56719 | TEAM_1 | -928 |
