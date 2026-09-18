# Per-kill trace: match 3116 Ascent 12-14

Candidate `impact-buy-disruption-30-80-rc2`, manifest LF-SHA-256 `ae043e361e3398ee578e82e9a393e63b8977d8a9ef4cad3894d357d5c8ebdae6`.

- **Before: `live_legacy`** -- enable_econ_component=False, econ_model=None, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF
- **After: `buy_disruption_v2_30_80`** -- enable_econ_component=True, econ_model=buy_disruption_v2_30_80, weights A/B/C=1.25/1.0/1.0, use_realized_swing=True, post-plant table OFF, pre-plant curve OFF

impact = A*damage + B*leverage + C*econ with A=1.25, B=1.0, C=1.0; econ points = C * 1007.9209 * raw, rounded ONCE per player-round.
Kill credit = small equipment value + allocated buy-disruption value. Death debit = 30% of the victim's damage value when the team's funding absorbed the loss, 80% when its severity pool is positive (constrained next buy). Repeated deaths expose no new kit.

## Round 1

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486534 | 11.275s | Momomimo#hru -> Fentlie#Freak | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486537 | 14.422s | IP Thoaiyama#Phan -> steamerbtw123#6897 | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 486535 | 18.955s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | combat | 3v5 | 120 | 1.000 | 120.0 | 42.0 | | | | | no economy this round |
| event 486529 | 21.990s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | combat | 4v3 | 130 | 1.008 | 131.0 | 131.0 | | | | | no economy this round |
| event 486536 | 23.735s | Diamondkidflash#9059 -> Deemo#Derf | combat | 2v4 | 130 | 1.041 | 135.3 | 6.8 | | | | | no economy this round |
| event 486530 | 23.998s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | combat | 3v2 | 140 | 1.046 | 146.4 | 73.2 | | | | | no economy this round |
| event 486531 | 28.381s | chickenfries27#6819 -> NPrightdolphin#NA1 | combat | 1v3 | 120 | 1.128 | 135.4 | 135.4 | | | | | no economy this round |
| event 486532 | 33.022s | chickenfries27#6819 -> Osmin#NA1 | combat | 1v2 | 190 | 1.216 | 231.0 | 231.0 | | | | | no economy this round |
| event 486538 | 67.224s | IP Thoaiyama#Phan -> IP Thoaiyama#Phan | self | 1v1 | 250 | 1.000 | 0.0 | 125.0 | | | | | no economy this round |
| event 486533 | 67.703s | chickenfries27#6819 -> chickenfries27#6819 | self | 1v1 | 100 | 1.000 | 0.0 | 50.0 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| chickenfries27#6819 | +980 | 672 | +316 | 0.00 | 0.00 | +0 | +988 |
| NPrightdolphin#NA1 | +602 | 430 | +142 | 0.00 | 0.00 | +0 | +572 |
| IP Thoaiyama#Phan | +314 | 341 | +5 | 0.00 | 0.00 | +0 | +346 |
| Diamondkidflash#9059 | +195 | 125 | +62 | 0.00 | 0.00 | +0 | +187 |
| Momomimo#hru | +190 | 64 | +108 | 0.00 | 0.00 | +0 | +172 |
| Deemo#Derf | +104 | 112 | -7 | 0.00 | 0.00 | +0 | +105 |
| IDKNotDumbIGuess#NA1 | +64 | 76 | -11 | 0.00 | 0.00 | +0 | +65 |
| steamerbtw123#6897 | -83 | 69 | -130 | 0.00 | 0.00 | +0 | -61 |
| Fentlie#Freak | -175 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |
| Osmin#NA1 | -175 | 60 | -231 | 0.00 | 0.00 | +0 | -171 |

## Round 2

Pistol winner TEAM_1; round winner TEAM_1; half round 2.

**TEAM_1** lost L=5,500; target H=15,800 (carryover targets); funding U=29,000; gap D=0; observed next-equipment gap G=250; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 3,300 | 0 | 3,250 | 3,250 | 3,150 | 3,300 |
| chickenfries27#6819 | 3,650 | 0 | 3,800 | 3,650 | 3,250 | 3,650 |
| Fentlie#Freak | 3,300 | 3,300 | 3,100 | 3,100 | 500 | 3,300 |
| Diamondkidflash#9059 | 3,350 | 0 | 3,350 | 3,350 | 3,250 | 3,350 |
| IDKNotDumbIGuess#NA1 | 2,200 | 2,200 | 2,200 | 2,200 | 3,300 | 2,200 |

**TEAM_2** lost L=2,000; target H=19,500; funding U=20,150; gap D=0; observed next-equipment gap G=1,050; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 0 | 0 | 4,200 | 4,200 | 150 | 3,900 |
| Momomimo#hru | 1,000 | 1,000 | 2,850 | 2,850 | 850 | 3,900 |
| NPrightdolphin#NA1 | 200 | 200 | 4,000 | 4,000 | 0 | 3,900 |
| Deemo#Derf | 500 | 500 | 4,400 | 4,400 | 0 | 3,900 |
| IP Thoaiyama#Phan | 300 | 300 | 4,400 | 4,400 | 700 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486539 | 10.042s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 2,200 | 11.37 | 0.00 | 11.37 | 3.41 (30%, absorbed) |
| event 486540 | 11.597s | Deemo#Derf -> Fentlie#Freak | enemy | 5v4 | 130 | 1.000 | 130.0 | 6.5 | 3,300 | 17.06 | 0.00 | 17.06 | 5.12 (30%, absorbed) |
| event 486542 | 12.167s | steamerbtw123#6897 -> Deemo#Derf | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 500 | 2.58 | 0.00 | 2.58 | 0.78 (30%, absorbed) |
| event 486541 | 16.432s | chickenfries27#6819 -> Osmin#NA1 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 486543 | 19.001s | steamerbtw123#6897 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 200 | 1.03 | 0.00 | 1.03 | 0.31 (30%, absorbed) |
| event 486544 | 25.236s | steamerbtw123#6897 -> IP Thoaiyama#Phan | enemy | 3v2 | 140 | 1.000 | 140.0 | 140.0 | 300 | 1.55 | 0.00 | 1.55 | 0.47 (30%, absorbed) |
| event 486545 | 26.213s | steamerbtw123#6897 -> Momomimo#hru | enemy | 3v1 | 70 | 1.008 | 70.5 | 70.5 | 1,000 | 5.17 | 0.00 | 5.17 | 1.55 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +1,216 | 765 | +511 | 10.34 | 0.00 | +10 | +1,286 |
| chickenfries27#6819 | +296 | 156 | +160 | 0.00 | 0.00 | +0 | +316 |
| NPrightdolphin#NA1 | +272 | 216 | -30 | 11.37 | 0.31 | +11 | +197 |
| Fentlie#Freak | +190 | 200 | -6 | 0.00 | 5.12 | -5 | +189 |
| Deemo#Derf | +158 | 66 | +10 | 17.06 | 0.78 | +16 | +92 |
| Momomimo#hru | +32 | 98 | -71 | 0.00 | 1.55 | -2 | +25 |
| Diamondkidflash#9059 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Osmin#NA1 | -10 | 130 | -160 | 0.00 | 0.00 | +0 | -30 |
| IP Thoaiyama#Phan | -122 | 0 | -140 | 0.00 | 0.47 | +0 | -140 |
| IDKNotDumbIGuess#NA1 | -214 | 0 | -150 | 0.00 | 3.41 | -3 | -153 |

## Round 3

Pistol winner TEAM_1; round winner TEAM_2; half round 3.

**TEAM_1** lost L=15,550; target H=19,500; funding U=22,600; gap D=0; observed next-equipment gap G=4,500; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 3,250 | 3,250 | 1,800 | 1,800 | 1,450 | 3,900 |
| chickenfries27#6819 | 3,650 | 3,650 | 4,400 | 4,250 | 1,400 | 3,900 |
| Fentlie#Freak | 3,100 | 3,100 | 1,500 | 1,500 | 1,600 | 3,900 |
| Diamondkidflash#9059 | 3,350 | 3,350 | 4,600 | 4,600 | 1,750 | 3,900 |
| IDKNotDumbIGuess#NA1 | 2,200 | 2,200 | 4,500 | 4,500 | 1,400 | 3,900 |

**TEAM_2** lost L=17,000; target H=19,500; funding U=21,650; gap D=0; observed next-equipment gap G=10,250; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,200 | 4,200 | 1,500 | 1,500 | 2,150 | 3,900 |
| Momomimo#hru | 2,850 | 0 | 4,150 | 4,150 | 3,300 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 4,000 | 400 | 400 | 2,500 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 1,700 | 1,700 | 2,200 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 1,750 | 1,750 | 2,250 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486546 | 9.528s | Deemo#Derf -> chickenfries27#6819 | enemy | 5v5 | 150 | 1.000 | 150.0 | 25.5 | 3,650 | 18.87 | 0.00 | 18.87 | 5.66 (30%, absorbed) |
| event 486547 | 10.631s | Deemo#Derf -> IDKNotDumbIGuess#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 13.0 | 2,200 | 11.37 | 0.00 | 11.37 | 3.41 (30%, absorbed) |
| event 486554 | 11.882s | Diamondkidflash#9059 -> Deemo#Derf | enemy | 3v5 | 120 | 1.000 | 120.0 | 6.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486548 | 12.454s | Osmin#NA1 -> Diamondkidflash#9059 | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 3,350 | 17.32 | 0.00 | 17.32 | 5.19 (30%, absorbed) |
| event 486551 | 14.365s | steamerbtw123#6897 -> NPrightdolphin#NA1 | enemy | 2v4 | 130 | 1.000 | 130.0 | 130.0 | 4,000 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |
| event 486549 | 20.572s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486552 | 22.242s | Momomimo#hru -> steamerbtw123#6897 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 3,250 | 16.80 | 0.00 | 16.80 | 5.04 (30%, absorbed) |
| event 486550 | 24.359s | Fentlie#Freak -> Osmin#NA1 | enemy | 1v2 | 190 | 1.000 | 190.0 | 190.0 | 4,200 | 21.71 | 0.00 | 21.71 | 6.51 (30%, absorbed) |
| event 486553 | 68.178s | Momomimo#hru -> Fentlie#Freak | enemy | 1v1 | 250 | 1.099 | 274.7 | 274.7 | 3,100 | 16.02 | 0.00 | 16.02 | 4.81 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deemo#Derf | +798 | 551 | +274 | 30.24 | 6.82 | +23 | +848 |
| Momomimo#hru | +811 | 339 | +475 | 32.82 | 0.00 | +33 | +847 |
| Fentlie#Freak | +948 | 654 | +85 | 44.45 | 4.81 | +40 | +779 |
| steamerbtw123#6897 | +276 | 311 | -70 | 20.68 | 5.04 | +16 | +257 |
| Diamondkidflash#9059 | +241 | 188 | -10 | 22.74 | 5.19 | +18 | +196 |
| NPrightdolphin#NA1 | +51 | 229 | -130 | 0.00 | 6.20 | -6 | +93 |
| Osmin#NA1 | -50 | 120 | -60 | 17.32 | 6.51 | +11 | +71 |
| chickenfries27#6819 | +7 | 31 | -26 | 0.00 | 5.66 | -6 | -1 |
| IDKNotDumbIGuess#NA1 | -12 | 0 | -13 | 0.00 | 3.41 | -3 | -16 |
| IP Thoaiyama#Phan | -261 | 0 | -170 | 0.00 | 6.82 | -7 | -177 |

## Round 4

Pistol winner TEAM_1; round winner TEAM_1; half round 4.

**TEAM_1** lost L=3,300; target H=19,500; funding U=34,250; gap D=0; observed next-equipment gap G=2,100; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 1,800 | 1,800 | 1,800 | 1,800 | 3,950 | 3,900 |
| chickenfries27#6819 | 4,250 | 0 | 4,400 | 4,250 | 4,750 | 3,900 |
| Fentlie#Freak | 1,500 | 1,500 | 4,600 | 4,600 | 300 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 0 | 4,600 | 4,600 | 3,750 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,500 | 0 | 4,300 | 4,300 | 4,100 | 3,900 |

**TEAM_2** lost L=9,500; target H=19,500; funding U=21,800; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 1,500 | 1,500 | 4,200 | 4,200 | 350 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 4,150 | 4,150 | 1,350 | 3,900 |
| NPrightdolphin#NA1 | 400 | 400 | 4,000 | 4,000 | 300 | 3,900 |
| Deemo#Derf | 1,700 | 1,700 | 4,050 | 4,050 | 50 | 3,900 |
| IP Thoaiyama#Phan | 1,750 | 1,750 | 4,400 | 4,400 | 250 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486560 | 15.691s | Diamondkidflash#9059 -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,750 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |
| event 486558 | 19.510s | steamerbtw123#6897 -> Deemo#Derf | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 1,700 | 8.79 | 0.00 | 8.79 | 2.64 (30%, absorbed) |
| event 486559 | 27.544s | steamerbtw123#6897 -> Momomimo#hru | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486556 | 36.944s | Osmin#NA1 -> steamerbtw123#6897 | enemy | 2v5 | 70 | 1.113 | 77.9 | 77.9 | 1,800 | 9.30 | 0.00 | 9.30 | 2.79 (30%, absorbed) |
| event 486555 | 43.186s | NPrightdolphin#NA1 -> Fentlie#Freak | enemy | 2v4 | 130 | 1.231 | 160.0 | 56.0 | 1,500 | 7.75 | 0.00 | 7.75 | 2.33 (30%, absorbed) |
| event 486557 | 46.029s | chickenfries27#6819 -> Osmin#NA1 | enemy | 3v2 | 140 | 1.285 | 179.8 | 179.8 | 1,500 | 7.75 | 0.00 | 7.75 | 2.33 (30%, absorbed) |
| event 486561 | 46.630s | Diamondkidflash#9059 -> NPrightdolphin#NA1 | enemy | 3v1 | 70 | 1.296 | 90.7 | 90.7 | 400 | 2.07 | 0.00 | 2.07 | 0.62 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +710 | 550 | +142 | 30.24 | 2.79 | +27 | +719 |
| Diamondkidflash#9059 | +625 | 419 | +241 | 11.11 | 0.00 | +11 | +671 |
| chickenfries27#6819 | +248 | 106 | +180 | 7.75 | 0.00 | +8 | +294 |
| NPrightdolphin#NA1 | +211 | 125 | +69 | 7.75 | 0.62 | +7 | +201 |
| Osmin#NA1 | +119 | 188 | -102 | 9.30 | 2.33 | +7 | +93 |
| Fentlie#Freak | +27 | 81 | -56 | 0.00 | 2.33 | -2 | +23 |
| IDKNotDumbIGuess#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| IP Thoaiyama#Phan | -10 | 128 | -150 | 0.00 | 2.71 | -3 | -25 |
| Momomimo#hru | -99 | 0 | -90 | 0.00 | 6.44 | -6 | -96 |
| Deemo#Derf | -134 | 0 | -130 | 0.00 | 2.64 | -3 | -133 |

## Round 5

Pistol winner TEAM_1; round winner TEAM_1; half round 5.

**TEAM_1** lost L=8,850; target H=19,500; funding U=45,300; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 1,800 | 0 | 4,700 | 4,700 | 6,450 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 6,900 | 3,900 |
| Fentlie#Freak | 4,600 | 0 | 4,600 | 4,600 | 2,100 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 6,050 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,300 | 0 | 4,300 | 4,300 | 4,300 | 3,900 |

**TEAM_2** lost L=20,800; target H=19,500; funding U=14,900; gap D=4,600; observed next-equipment gap G=12,150; activation 1.0000; severity pool 12,150.00 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,200 | 4,200 | 800 | 800 | 1,950 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 2,100 | 2,100 | 1,950 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 4,000 | 1,100 | 1,100 | 1,200 | 3,900 |
| Deemo#Derf | 4,050 | 4,050 | 1,350 | 1,350 | 1,300 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 2,000 | 2,000 | 1,150 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486568 | 13.001s | IP Thoaiyama#Phan -> Diamondkidflash#9059 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486564 | 45.435s | Fentlie#Freak -> NPrightdolphin#NA1 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,000 | 20.68 | 120.77 | 141.45 | 113.16 (80%, constrained) |
| event 486562 | 50.683s | Deemo#Derf -> chickenfries27#6819 | enemy | 4v4 | 170 | 1.000 | 170.0 | 85.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486563 | 55.472s | chickenfries27#6819 -> Deemo#Derf | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,050 | 20.93 | 122.28 | 143.21 | 114.57 (80%, constrained) |
| event 486565 | 61.473s | steamerbtw123#6897 -> Momomimo#hru | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,150 | 21.45 | 125.30 | 146.75 | 117.40 (80%, constrained) |
| event 486566 | 67.798s | steamerbtw123#6897 -> IP Thoaiyama#Phan | enemy | 4v2 | 80 | 1.094 | 87.5 | 87.5 | 4,400 | 22.74 | 132.85 | 155.59 | 124.47 (80%, constrained) |
| event 486567 | 69.850s | IDKNotDumbIGuess#NA1 -> Osmin#NA1 | enemy | 4v1 | 50 | 1.132 | 56.6 | 56.6 | 4,200 | 21.71 | 126.81 | 148.52 | 118.82 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +838 | 531 | +217 | 302.34 | 0.00 | +302 | +1,050 |
| Fentlie#Freak | +365 | 186 | +140 | 141.45 | 0.00 | +141 | +467 |
| IDKNotDumbIGuess#NA1 | +257 | 188 | +57 | 148.52 | 0.00 | +149 | +394 |
| chickenfries27#6819 | +295 | 156 | +85 | 143.21 | 6.59 | +137 | +378 |
| IP Thoaiyama#Phan | +361 | 359 | +63 | 23.78 | 124.47 | -101 | +321 |
| Deemo#Derf | +127 | 188 | +0 | 21.97 | 114.57 | -93 | +95 |
| NPrightdolphin#NA1 | -33 | 146 | -140 | 0.00 | 113.16 | -113 | -107 |
| Osmin#NA1 | -37 | 32 | -57 | 0.00 | 118.82 | -119 | -144 |
| Diamondkidflash#9059 | -125 | 0 | -150 | 0.00 | 7.13 | -7 | -157 |
| Momomimo#hru | -183 | 0 | -130 | 0.00 | 117.40 | -117 | -247 |

## Round 6

Pistol winner TEAM_1; round winner TEAM_1; half round 6.

**TEAM_1** lost L=4,600; target H=19,500; funding U=50,950; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,700 | 0 | 5,200 | 5,200 | 8,500 | 3,900 |
| chickenfries27#6819 | 4,250 | 0 | 4,400 | 4,250 | 7,650 | 3,900 |
| Fentlie#Freak | 4,600 | 0 | 4,600 | 4,600 | 3,900 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 4,400 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,300 | 0 | 5,300 | 5,300 | 7,000 | 3,900 |

**TEAM_2** lost L=7,350; target H=19,500; funding U=21,200; gap D=0; observed next-equipment gap G=250; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 800 | 800 | 4,600 | 4,600 | 250 | 3,900 |
| Momomimo#hru | 2,100 | 2,100 | 4,150 | 4,150 | 1,000 | 3,900 |
| NPrightdolphin#NA1 | 1,100 | 1,100 | 3,650 | 3,650 | 250 | 3,900 |
| Deemo#Derf | 1,350 | 1,350 | 4,400 | 4,400 | 300 | 3,900 |
| IP Thoaiyama#Phan | 2,000 | 2,000 | 4,400 | 4,400 | 150 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486572 | 17.934s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486570 | 23.298s | chickenfries27#6819 -> Deemo#Derf | enemy | 5v4 | 130 | 1.039 | 135.1 | 135.1 | 1,350 | 6.98 | 0.00 | 6.98 | 2.09 (30%, absorbed) |
| event 486569 | 25.539s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 3v5 | 120 | 1.081 | 129.7 | 45.4 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486573 | 27.227s | steamerbtw123#6897 -> Momomimo#hru | enemy | 4v3 | 130 | 1.113 | 144.7 | 144.7 | 2,100 | 10.85 | 0.00 | 10.85 | 3.26 (30%, absorbed) |
| event 486574 | 29.019s | steamerbtw123#6897 -> NPrightdolphin#NA1 | enemy | 4v2 | 80 | 1.147 | 91.7 | 91.7 | 1,100 | 5.69 | 0.00 | 5.69 | 1.71 (30%, absorbed) |
| event 486571 | 39.462s | chickenfries27#6819 -> Osmin#NA1 | enemy | 4v1 | 50 | 1.344 | 67.2 | 67.2 | 800 | 4.14 | 0.00 | 4.14 | 1.24 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +645 | 450 | +236 | 16.54 | 0.00 | +17 | +703 |
| chickenfries27#6819 | +533 | 368 | +202 | 11.11 | 0.00 | +11 | +581 |
| Fentlie#Freak | +289 | 156 | +150 | 10.34 | 0.00 | +10 | +316 |
| NPrightdolphin#NA1 | +216 | 168 | +38 | 23.78 | 1.71 | +22 | +228 |
| IDKNotDumbIGuess#NA1 | +50 | 50 | +0 | 0.00 | 0.00 | +0 | +50 |
| Diamondkidflash#9059 | +57 | 100 | -45 | 0.00 | 7.13 | -7 | +48 |
| Osmin#NA1 | -47 | 0 | -67 | 0.00 | 1.24 | -1 | -68 |
| IP Thoaiyama#Phan | -67 | 66 | -150 | 0.00 | 3.10 | -3 | -87 |
| Momomimo#hru | -89 | 31 | -145 | 0.00 | 3.26 | -3 | -117 |
| Deemo#Derf | -117 | 0 | -135 | 0.00 | 2.09 | -2 | -137 |

## Round 7

Pistol winner TEAM_1; round winner TEAM_2; half round 7.

**TEAM_1** lost L=23,950; target H=19,500; funding U=36,550; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 5,200 | 5,200 | 4,900 | 4,900 | 4,900 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 4,750 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 4,600 | 4,600 | 1,400 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 1,900 | 3,900 |
| IDKNotDumbIGuess#NA1 | 5,300 | 5,300 | 4,600 | 4,600 | 4,100 | 3,900 |

**TEAM_2** lost L=8,550; target H=19,500; funding U=27,350; gap D=0; observed next-equipment gap G=1,850; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 0 | 4,600 | 4,600 | 2,550 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 2,850 | 2,850 | 1,150 | 3,900 |
| NPrightdolphin#NA1 | 3,650 | 0 | 4,100 | 4,100 | 2,050 | 3,900 |
| Deemo#Derf | 4,400 | 0 | 4,400 | 4,400 | 3,400 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 3,100 | 3,100 | 550 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486577 | 16.444s | Deemo#Derf -> Diamondkidflash#9059 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486580 | 24.519s | chickenfries27#6819 -> Momomimo#hru | enemy | 4v5 | 140 | 1.000 | 140.0 | 23.8 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486575 | 27.072s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486581 | 32.562s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 3v4 | 160 | 1.000 | 160.0 | 27.2 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486576 | 35.274s | NPrightdolphin#NA1 -> Fentlie#Freak | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486578 | 60.418s | Deemo#Derf -> steamerbtw123#6897 | enemy | 3v2 | 140 | 1.000 | 140.0 | 140.0 | 5,200 | 26.88 | 0.00 | 26.88 | 8.06 (30%, absorbed) |
| event 486579 | 79.430s | Deemo#Derf -> IDKNotDumbIGuess#NA1 | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 5,300 | 27.39 | 0.00 | 27.39 | 8.22 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,065 | 744 | +350 | 45.74 | 0.00 | +46 | +1,140 |
| Deemo#Derf | +874 | 574 | +360 | 78.05 | 0.00 | +78 | +1,012 |
| Fentlie#Freak | +284 | 236 | -20 | 22.74 | 7.13 | +16 | +232 |
| chickenfries27#6819 | +242 | 221 | -30 | 21.45 | 6.59 | +15 | +206 |
| Osmin#NA1 | +84 | 84 | +0 | 0.00 | 0.00 | +0 | +84 |
| IP Thoaiyama#Phan | +62 | 98 | -27 | 0.00 | 6.82 | -7 | +64 |
| Momomimo#hru | -30 | 0 | -24 | 0.00 | 6.44 | -6 | -30 |
| IDKNotDumbIGuess#NA1 | -58 | 0 | -70 | 0.00 | 8.22 | -8 | -78 |
| steamerbtw123#6897 | -117 | 0 | -140 | 0.00 | 8.06 | -8 | -148 |
| Diamondkidflash#9059 | -125 | 0 | -150 | 0.00 | 7.13 | -7 | -157 |

## Round 8

Pistol winner TEAM_1; round winner TEAM_2; half round 8.

**TEAM_1** lost L=22,950; target H=19,500; funding U=26,750; gap D=0; observed next-equipment gap G=4,300; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,900 | 4,900 | 2,000 | 2,000 | 3,200 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 3,100 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 1,500 | 1,500 | 2,700 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 350 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,600 | 4,600 | 4,500 | 4,500 | 2,200 | 3,900 |

**TEAM_2** lost L=3,100; target H=19,500; funding U=39,050; gap D=0; observed next-equipment gap G=800; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 0 | 4,600 | 4,600 | 4,850 | 3,900 |
| Momomimo#hru | 2,850 | 0 | 4,150 | 4,150 | 3,300 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,000 | 4,000 | 4,850 | 3,900 |
| Deemo#Derf | 4,400 | 0 | 4,400 | 4,400 | 6,600 | 3,900 |
| IP Thoaiyama#Phan | 3,100 | 3,100 | 3,100 | 3,100 | 750 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486588 | 24.742s | Diamondkidflash#9059 -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,100 | 16.02 | 0.00 | 16.02 | 4.81 (30%, absorbed) |
| event 486586 | 28.486s | Momomimo#hru -> IDKNotDumbIGuess#NA1 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486587 | 34.101s | Momomimo#hru -> Diamondkidflash#9059 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486582 | 39.601s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486583 | 46.704s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 486584 | 59.791s | Deemo#Derf -> steamerbtw123#6897 | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 4,900 | 25.33 | 0.00 | 25.33 | 7.60 (30%, absorbed) |
| event 486585 | 59.791s | Deemo#Derf -> Fentlie#Freak | enemy | 4v1 | 50 | 1.000 | 50.0 | 50.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| IP Thoaiyama#Phan | +690 | 828 | -150 | 0.00 | 4.81 | -5 | +673 |
| NPrightdolphin#NA1 | +619 | 355 | +260 | 21.97 | 0.00 | +22 | +637 |
| Momomimo#hru | +570 | 229 | +310 | 47.55 | 0.00 | +48 | +587 |
| Deemo#Derf | +349 | 228 | +130 | 49.10 | 0.00 | +49 | +407 |
| Diamondkidflash#9059 | +139 | 188 | -20 | 16.02 | 7.13 | +9 | +177 |
| Osmin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Fentlie#Freak | -47 | 0 | -50 | 0.00 | 7.13 | -7 | -57 |
| steamerbtw123#6897 | -75 | 0 | -80 | 0.00 | 7.60 | -8 | -88 |
| IDKNotDumbIGuess#NA1 | -104 | 50 | -140 | 0.00 | 7.13 | -7 | -97 |
| chickenfries27#6819 | -264 | 0 | -260 | 0.00 | 6.59 | -7 | -267 |

## Round 9

Pistol winner TEAM_1; round winner TEAM_1; half round 9.

**TEAM_1** lost L=12,350; target H=19,500; funding U=29,800; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 2,000 | 2,000 | 4,700 | 4,700 | 2,600 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 2,550 | 3,900 |
| Fentlie#Freak | 1,500 | 1,500 | 4,600 | 4,600 | 1,400 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 2,150 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,500 | 0 | 4,300 | 4,300 | 1,600 | 3,900 |

**TEAM_2** lost L=20,250; target H=19,500; funding U=30,150; gap D=0; observed next-equipment gap G=2,950; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 4,600 | 4,600 | 2,550 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 2,850 | 2,850 | 2,350 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 4,000 | 4,100 | 4,100 | 2,950 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 4,400 | 4,400 | 4,400 | 3,900 |
| IP Thoaiyama#Phan | 3,100 | 3,100 | 2,000 | 2,000 | 1,350 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486593 | 17.147s | steamerbtw123#6897 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486589 | 29.524s | NPrightdolphin#NA1 -> Fentlie#Freak | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 1,500 | 7.75 | 0.00 | 7.75 | 2.33 (30%, absorbed) |
| event 486597 | 32.234s | IP Thoaiyama#Phan -> steamerbtw123#6897 | enemy | 4v4 | 170 | 1.032 | 175.4 | 175.4 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486592 | 34.558s | chickenfries27#6819 -> Momomimo#hru | enemy | 3v4 | 160 | 1.075 | 172.1 | 8.6 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486590 | 34.612s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 3v3 | 180 | 1.076 | 193.8 | 96.9 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486591 | 37.120s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 3v2 | 140 | 1.124 | 157.3 | 15.7 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486594 | 38.656s | IDKNotDumbIGuess#NA1 -> NPrightdolphin#NA1 | enemy | 1v3 | 120 | 1.153 | 138.3 | 138.3 | 4,000 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |
| event 486595 | 42.353s | IDKNotDumbIGuess#NA1 -> Osmin#NA1 | enemy | 1v2 | 190 | 1.222 | 232.3 | 232.3 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486596 | 44.388s | IDKNotDumbIGuess#NA1 -> IP Thoaiyama#Phan | enemy | 1v1 | 250 | 1.261 | 315.2 | 315.2 | 3,100 | 16.02 | 0.00 | 16.02 | 4.81 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| IDKNotDumbIGuess#NA1 | +1,382 | 816 | +686 | 60.48 | 0.00 | +60 | +1,562 |
| NPrightdolphin#NA1 | +1,171 | 800 | +353 | 53.50 | 6.20 | +47 | +1,200 |
| chickenfries27#6819 | +208 | 155 | +75 | 21.45 | 6.59 | +15 | +245 |
| steamerbtw123#6897 | +191 | 188 | -25 | 22.74 | 3.10 | +20 | +183 |
| IP Thoaiyama#Phan | +106 | 178 | -140 | 10.34 | 4.81 | +6 | +44 |
| Deemo#Derf | +0 | 175 | -150 | 0.00 | 6.82 | -7 | +18 |
| Momomimo#hru | -8 | 0 | -9 | 0.00 | 6.44 | -6 | -15 |
| Diamondkidflash#9059 | -16 | 0 | -16 | 0.00 | 7.13 | -7 | -23 |
| Fentlie#Freak | -74 | 58 | -140 | 0.00 | 2.33 | -2 | -84 |
| Osmin#NA1 | -150 | 54 | -232 | 0.00 | 7.13 | -7 | -185 |

## Round 10

Pistol winner TEAM_1; round winner TEAM_2; half round 10.

**TEAM_1** lost L=22,450; target H=19,500; funding U=21,900; gap D=0; observed next-equipment gap G=6,050; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,700 | 4,700 | 1,800 | 1,800 | 3,800 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 700 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 2,000 | 2,000 | 2,000 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 0 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,300 | 4,300 | 1,850 | 1,850 | 1,950 | 3,900 |

**TEAM_2** lost L=9,450; target H=19,500; funding U=33,850; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 4,600 | 4,600 | 1,150 | 3,900 |
| Momomimo#hru | 2,850 | 2,850 | 4,150 | 4,150 | 1,400 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,000 | 4,000 | 4,750 | 3,900 |
| Deemo#Derf | 4,400 | 0 | 4,400 | 4,400 | 6,600 | 3,900 |
| IP Thoaiyama#Phan | 2,000 | 2,000 | 4,400 | 4,400 | 450 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486604 | 2.334s | steamerbtw123#6897 -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486601 | 14.946s | Osmin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,300 | 22.23 | 0.00 | 22.23 | 6.67 (30%, absorbed) |
| event 486605 | 30.093s | Momomimo#hru -> chickenfries27#6819 | enemy | 4v4 | 170 | 1.165 | 198.1 | 9.9 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486602 | 30.326s | Fentlie#Freak -> Momomimo#hru | enemy | 3v4 | 160 | 1.170 | 187.1 | 187.1 | 2,850 | 14.73 | 0.00 | 14.73 | 4.42 (30%, absorbed) |
| event 486603 | 33.041s | Fentlie#Freak -> Osmin#NA1 | enemy | 3v3 | 180 | 1.221 | 219.7 | 219.7 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486599 | 35.174s | Deemo#Derf -> Diamondkidflash#9059 | enemy | 2v3 | 170 | 1.261 | 214.4 | 214.4 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486600 | 40.595s | Deemo#Derf -> steamerbtw123#6897 | enemy | 2v2 | 200 | 1.363 | 272.7 | 272.7 | 4,700 | 24.29 | 0.00 | 24.29 | 7.29 (30%, absorbed) |
| event 486598 | 46.541s | NPrightdolphin#NA1 -> Fentlie#Freak | enemy | 2v1 | 130 | 1.476 | 191.8 | 191.8 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deemo#Derf | +936 | 460 | +487 | 48.07 | 0.00 | +48 | +995 |
| Fentlie#Freak | +514 | 411 | +215 | 38.51 | 7.13 | +31 | +657 |
| NPrightdolphin#NA1 | +380 | 195 | +192 | 23.78 | 0.00 | +24 | +411 |
| steamerbtw123#6897 | +206 | 358 | -123 | 10.34 | 7.29 | +3 | +238 |
| Momomimo#hru | +299 | 186 | +11 | 21.97 | 4.42 | +18 | +215 |
| Osmin#NA1 | +280 | 278 | -80 | 22.23 | 7.13 | +15 | +213 |
| chickenfries27#6819 | +127 | 139 | -10 | 0.00 | 6.59 | -7 | +122 |
| Diamondkidflash#9059 | -115 | 100 | -214 | 0.00 | 7.13 | -7 | -121 |
| IDKNotDumbIGuess#NA1 | -165 | 0 | -140 | 0.00 | 6.67 | -7 | -147 |
| IP Thoaiyama#Phan | -108 | 0 | -150 | 0.00 | 3.10 | -3 | -153 |

## Round 11

Pistol winner TEAM_1; round winner TEAM_1; half round 11.

**TEAM_1** lost L=6,250; target H=19,500; funding U=32,900; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 1,800 | 0 | 6,800 | 6,800 | 1,500 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 2,850 | 3,900 |
| Fentlie#Freak | 2,000 | 2,000 | 4,600 | 4,600 | 3,600 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 0 | 4,600 | 4,600 | 1,600 | 3,900 |
| IDKNotDumbIGuess#NA1 | 1,850 | 0 | 5,100 | 5,100 | 3,850 | 3,900 |

**TEAM_2** lost L=21,550; target H=19,500; funding U=23,850; gap D=0; observed next-equipment gap G=1,400; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 3,200 | 3,200 | 50 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 3,200 | 3,200 | 400 | 3,900 |
| NPrightdolphin#NA1 | 4,000 | 4,000 | 4,000 | 4,000 | 2,450 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 4,400 | 4,400 | 1,500 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 4,400 | 4,400 | 1,350 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486609 | 19.873s | steamerbtw123#6897 -> Momomimo#hru | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486610 | 25.047s | steamerbtw123#6897 -> IP Thoaiyama#Phan | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486612 | 32.799s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486607 | 40.073s | Osmin#NA1 -> Fentlie#Freak | enemy | 2v5 | 70 | 1.000 | 70.0 | 70.0 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486608 | 47.104s | chickenfries27#6819 -> Osmin#NA1 | enemy | 4v2 | 80 | 1.099 | 88.0 | 30.8 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486606 | 50.877s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 1v4 | 70 | 1.171 | 81.9 | 28.7 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486611 | 54.506s | steamerbtw123#6897 -> NPrightdolphin#NA1 | enemy | 3v1 | 70 | 1.239 | 86.7 | 86.7 | 4,000 | 20.68 | 0.00 | 20.68 | 6.20 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +1,273 | 844 | +367 | 64.87 | 0.00 | +65 | +1,276 |
| Osmin#NA1 | +336 | 306 | +39 | 10.34 | 7.13 | +3 | +348 |
| IDKNotDumbIGuess#NA1 | +303 | 188 | +90 | 22.74 | 0.00 | +23 | +301 |
| chickenfries27#6819 | +251 | 188 | +59 | 23.78 | 6.59 | +17 | +264 |
| NPrightdolphin#NA1 | +180 | 188 | -5 | 21.97 | 6.20 | +16 | +199 |
| IP Thoaiyama#Phan | +9 | 175 | -130 | 0.00 | 6.82 | -7 | +38 |
| Diamondkidflash#9059 | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| Deemo#Derf | -35 | 80 | -90 | 0.00 | 6.82 | -7 | -17 |
| Fentlie#Freak | -62 | 0 | -70 | 0.00 | 3.10 | -3 | -73 |
| Momomimo#hru | -176 | 0 | -150 | 0.00 | 6.44 | -6 | -156 |

## Round 12

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486613 | 13.977s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486619 | 16.015s | IP Thoaiyama#Phan -> chickenfries27#6819 | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 486614 | 18.186s | Osmin#NA1 -> Diamondkidflash#9059 | combat | 5v3 | 90 | 1.000 | 90.0 | 90.0 | | | | | no economy this round |
| event 486617 | 22.544s | Fentlie#Freak -> NPrightdolphin#NA1 | combat | 2v5 | 70 | 1.000 | 70.0 | 11.9 | | | | | no economy this round |
| event 486618 | 23.816s | Fentlie#Freak -> IP Thoaiyama#Phan | combat | 2v4 | 130 | 1.000 | 130.0 | 6.5 | | | | | no economy this round |
| event 486615 | 24.711s | Osmin#NA1 -> Fentlie#Freak | combat | 3v2 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 486616 | 41.161s | Osmin#NA1 -> steamerbtw123#6897 | combat | 3v1 | 70 | 1.000 | 70.0 | 70.0 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +1,085 | 735 | +300 | 0.00 | 0.00 | +0 | +1,035 |
| Fentlie#Freak | +670 | 638 | +60 | 0.00 | 0.00 | +0 | +698 |
| IP Thoaiyama#Phan | +472 | 348 | +124 | 0.00 | 0.00 | +0 | +472 |
| NPrightdolphin#NA1 | +443 | 291 | +138 | 0.00 | 0.00 | +0 | +429 |
| Momomimo#hru | +31 | 31 | +0 | 0.00 | 0.00 | +0 | +31 |
| Deemo#Derf | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| chickenfries27#6819 | -68 | 62 | -130 | 0.00 | 0.00 | +0 | -68 |
| steamerbtw123#6897 | -82 | 0 | -70 | 0.00 | 0.00 | +0 | -70 |
| Diamondkidflash#9059 | -105 | 0 | -90 | 0.00 | 0.00 | +0 | -90 |
| IDKNotDumbIGuess#NA1 | -162 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |

## Round 13

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486620 | 13.718s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486625 | 38.477s | steamerbtw123#6897 -> Deemo#Derf | combat | 4v5 | 140 | 1.000 | 140.0 | 140.0 | | | | | no economy this round |
| event 486623 | 66.002s | Osmin#NA1 -> Fentlie#Freak | combat | 4v4 | 170 | 1.220 | 207.4 | 207.4 | | | | | no economy this round |
| event 486627 | 66.241s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | combat | 3v4 | 160 | 1.224 | 195.9 | 195.9 | | | | | no economy this round |
| event 486624 | 74.552s | Osmin#NA1 -> chickenfries27#6819 | combat | 3v3 | 180 | 1.381 | 248.6 | 24.9 | | | | | no economy this round |
| event 486626 | 75.743s | steamerbtw123#6897 -> Osmin#NA1 | combat | 2v3 | 170 | 1.404 | 238.6 | 238.6 | | | | | no economy this round |
| event 486628 | 85.970s | IDKNotDumbIGuess#NA1 -> IP Thoaiyama#Phan | combat | 2v2 | 200 | 1.597 | 319.3 | 159.7 | | | | | no economy this round |
| event 486621 | 90.960s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | combat | 1v2 | 190 | 1.691 | 321.3 | 321.3 | | | | | no economy this round |
| event 486622 | 94.271s | NPrightdolphin#NA1 -> steamerbtw123#6897 | combat | 1v1 | 250 | 1.750 | 437.5 | 125.0 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +1,386 | 591 | +909 | 0.00 | 0.00 | +0 | +1,500 |
| steamerbtw123#6897 | +573 | 438 | +254 | 0.00 | 0.00 | +0 | +692 |
| Osmin#NA1 | +594 | 371 | +217 | 0.00 | 0.00 | +0 | +588 |
| IDKNotDumbIGuess#NA1 | +512 | 305 | +194 | 0.00 | 0.00 | +0 | +499 |
| IP Thoaiyama#Phan | +53 | 190 | -160 | 0.00 | 0.00 | +0 | +30 |
| chickenfries27#6819 | -23 | 0 | -25 | 0.00 | 0.00 | +0 | -25 |
| Fentlie#Freak | -31 | 180 | -207 | 0.00 | 0.00 | +0 | -27 |
| Deemo#Derf | -163 | 0 | -140 | 0.00 | 0.00 | +0 | -140 |
| Diamondkidflash#9059 | -175 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |
| Momomimo#hru | -199 | 0 | -196 | 0.00 | 0.00 | +0 | -196 |

## Round 14

Pistol winner TEAM_2; round winner TEAM_1; half round 2.

**TEAM_1** lost L=1,700; target H=19,500; funding U=25,350; gap D=0; observed next-equipment gap G=2,900; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 300 | 300 | 4,050 | 4,050 | 3,000 | 3,900 |
| chickenfries27#6819 | 200 | 200 | 4,400 | 4,250 | 900 | 3,900 |
| Fentlie#Freak | 200 | 0 | 4,600 | 4,600 | 900 | 3,900 |
| Diamondkidflash#9059 | 700 | 700 | 4,600 | 4,600 | 350 | 3,900 |
| IDKNotDumbIGuess#NA1 | 500 | 500 | 1,000 | 1,000 | 3,600 | 3,900 |

**TEAM_2** lost L=15,800; target H=15,800 (carryover targets); funding U=14,800; gap D=1,000; observed next-equipment gap G=10,100; activation 0.2564; severity pool 2,589.74 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 3,600 | 3,600 | 1,200 | 1,200 | 1,600 | 3,600 |
| Momomimo#hru | 2,850 | 2,850 | 1,250 | 1,250 | 1,900 | 2,850 |
| NPrightdolphin#NA1 | 3,500 | 3,500 | 1,000 | 1,000 | 2,000 | 3,500 |
| Deemo#Derf | 2,750 | 2,750 | 1,300 | 1,300 | 1,650 | 2,750 |
| IP Thoaiyama#Phan | 3,100 | 3,100 | 950 | 950 | 1,950 | 3,100 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486636 | 10.161s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 2,850 | 14.73 | 24.15 | 38.88 | 31.10 (80%, constrained) |
| event 486637 | 10.902s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 2,750 | 14.21 | 23.30 | 37.51 | 30.01 (80%, constrained) |
| event 486633 | 20.884s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 3,100 | 16.02 | 26.26 | 42.29 | 33.83 (80%, constrained) |
| event 486629 | 21.021s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 2v5 | 70 | 1.000 | 70.0 | 70.0 | 500 | 2.58 | 0.00 | 2.58 | 0.78 (30%, absorbed) |
| event 486630 | 32.959s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 2v4 | 130 | 1.000 | 130.0 | 130.0 | 200 | 1.03 | 0.00 | 1.03 | 0.31 (30%, absorbed) |
| event 486631 | 36.524s | Osmin#NA1 -> Diamondkidflash#9059 | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 700 | 3.62 | 0.00 | 3.62 | 1.09 (30%, absorbed) |
| event 486632 | 41.407s | Osmin#NA1 -> steamerbtw123#6897 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 300 | 1.55 | 0.00 | 1.55 | 0.47 (30%, absorbed) |
| event 486634 | 44.703s | Fentlie#Freak -> NPrightdolphin#NA1 | enemy | 1v2 | 190 | 1.000 | 190.0 | 190.0 | 3,500 | 18.09 | 29.65 | 47.74 | 38.19 (80%, constrained) |
| event 486635 | 49.210s | Fentlie#Freak -> Osmin#NA1 | enemy | 1v1 | 250 | 1.000 | 250.0 | 250.0 | 3,600 | 18.61 | 30.50 | 49.11 | 39.29 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fentlie#Freak | +1,453 | 604 | +530 | 139.14 | 0.00 | +139 | +1,273 |
| IDKNotDumbIGuess#NA1 | +946 | 565 | +210 | 76.39 | 0.78 | +76 | +851 |
| Osmin#NA1 | +255 | 409 | +120 | 5.17 | 39.29 | -34 | +495 |
| NPrightdolphin#NA1 | +189 | 361 | +10 | 3.62 | 38.19 | -35 | +336 |
| chickenfries27#6819 | +40 | 128 | -130 | 0.00 | 0.31 | +0 | -2 |
| IP Thoaiyama#Phan | -100 | 38 | -90 | 0.00 | 33.83 | -34 | -86 |
| Deemo#Derf | -134 | 65 | -130 | 0.00 | 30.01 | -30 | -95 |
| steamerbtw123#6897 | -59 | 76 | -200 | 0.00 | 0.47 | +0 | -124 |
| Diamondkidflash#9059 | -94 | 21 | -170 | 0.00 | 1.09 | -1 | -150 |
| Momomimo#hru | -229 | 0 | -150 | 0.00 | 31.10 | -31 | -181 |

## Round 15

Pistol winner TEAM_2; round winner TEAM_1; half round 3.

**TEAM_1** lost L=13,450; target H=19,500; funding U=27,300; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,050 | 0 | 4,700 | 4,700 | 2,300 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 2,750 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 4,600 | 4,600 | 400 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 2,150 | 3,900 |
| IDKNotDumbIGuess#NA1 | 1,000 | 0 | 4,100 | 4,100 | 200 | 3,900 |

**TEAM_2** lost L=5,700; target H=19,500; funding U=20,650; gap D=0; observed next-equipment gap G=50; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 1,200 | 1,200 | 3,850 | 3,850 | 150 | 3,900 |
| Momomimo#hru | 1,250 | 1,250 | 4,150 | 4,150 | 150 | 3,900 |
| NPrightdolphin#NA1 | 1,000 | 1,000 | 4,100 | 4,100 | 400 | 3,900 |
| Deemo#Derf | 1,300 | 1,300 | 4,400 | 4,400 | 50 | 3,900 |
| IP Thoaiyama#Phan | 950 | 950 | 4,400 | 4,400 | 450 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486642 | 4.907s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 950 | 4.91 | 0.00 | 4.91 | 1.47 (30%, absorbed) |
| event 486643 | 11.254s | Fentlie#Freak -> Momomimo#hru | enemy | 5v4 | 130 | 1.000 | 130.0 | 22.1 | 1,250 | 6.46 | 0.00 | 6.46 | 1.94 (30%, absorbed) |
| event 486639 | 13.788s | Deemo#Derf -> Fentlie#Freak | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486638 | 71.861s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 3v4 | 160 | 1.000 | 160.0 | 27.2 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486641 | 74.220s | chickenfries27#6819 -> NPrightdolphin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 1,000 | 5.17 | 0.00 | 5.17 | 1.55 (30%, absorbed) |
| event 486640 | 87.153s | Deemo#Derf -> chickenfries27#6819 | enemy | 2v3 | 170 | 1.000 | 170.0 | 17.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486644 | 88.824s | steamerbtw123#6897 -> Deemo#Derf | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 1,300 | 6.72 | 0.00 | 6.72 | 2.02 (30%, absorbed) |
| event 486645 | 89.981s | steamerbtw123#6897 -> Osmin#NA1 | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 1,200 | 6.20 | 0.00 | 6.20 | 1.86 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | +761 | 438 | +330 | 12.92 | 0.00 | +13 | +781 |
| Fentlie#Freak | +579 | 469 | +160 | 11.37 | 7.13 | +4 | +633 |
| Deemo#Derf | +368 | 226 | +90 | 45.74 | 2.02 | +44 | +360 |
| NPrightdolphin#NA1 | +340 | 319 | -20 | 23.78 | 1.55 | +22 | +321 |
| chickenfries27#6819 | +271 | 125 | +163 | 5.17 | 6.59 | -1 | +287 |
| Osmin#NA1 | +129 | 256 | -130 | 0.00 | 1.86 | -2 | +124 |
| Momomimo#hru | +78 | 98 | -22 | 0.00 | 1.94 | -2 | +74 |
| IDKNotDumbIGuess#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Diamondkidflash#9059 | -32 | 0 | -27 | 0.00 | 7.13 | -7 | -34 |
| IP Thoaiyama#Phan | -104 | 26 | -150 | 0.00 | 1.47 | -1 | -125 |

## Round 16

Pistol winner TEAM_2; round winner TEAM_2; half round 4.

**TEAM_1** lost L=22,250; target H=19,500; funding U=19,550; gap D=0; observed next-equipment gap G=8,800; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,700 | 4,700 | 2,000 | 2,000 | 3,000 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 600 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 1,500 | 1,500 | 1,700 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 1,900 | 1,900 | 2,050 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,100 | 4,100 | 1,400 | 1,400 | 1,500 | 3,900 |

**TEAM_2** lost L=8,550; target H=19,500; funding U=27,900; gap D=0; observed next-equipment gap G=1,050; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 3,850 | 0 | 4,250 | 4,250 | 350 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 2,850 | 2,850 | 900 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 2,500 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 4,400 | 4,400 | 2,250 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 0 | 4,400 | 4,400 | 3,450 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486647 | 3.274s | Deemo#Derf -> Diamondkidflash#9059 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486648 | 8.700s | Deemo#Derf -> steamerbtw123#6897 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,700 | 24.29 | 0.00 | 24.29 | 7.29 (30%, absorbed) |
| event 486646 | 21.160s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486651 | 28.567s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | enemy | 2v5 | 70 | 1.000 | 70.0 | 24.5 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486649 | 32.018s | Osmin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 486650 | 37.038s | Fentlie#Freak -> Momomimo#hru | enemy | 1v4 | 70 | 1.000 | 70.0 | 3.5 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486652 | 37.856s | IP Thoaiyama#Phan -> Fentlie#Freak | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deemo#Derf | +886 | 561 | +256 | 48.07 | 6.82 | +41 | +858 |
| NPrightdolphin#NA1 | +331 | 210 | +90 | 21.97 | 0.00 | +22 | +322 |
| Fentlie#Freak | +238 | 261 | +0 | 21.45 | 7.13 | +14 | +275 |
| Osmin#NA1 | +270 | 169 | +80 | 21.19 | 0.00 | +21 | +270 |
| IP Thoaiyama#Phan | +250 | 162 | +70 | 23.78 | 0.00 | +24 | +256 |
| IDKNotDumbIGuess#NA1 | +75 | 100 | -10 | 22.74 | 6.36 | +16 | +106 |
| Momomimo#hru | -3 | 0 | -4 | 0.00 | 6.44 | -6 | -10 |
| steamerbtw123#6897 | -75 | 88 | -130 | 0.00 | 7.29 | -7 | -49 |
| chickenfries27#6819 | -121 | 0 | -90 | 0.00 | 6.59 | -7 | -97 |
| Diamondkidflash#9059 | -188 | 0 | -150 | 0.00 | 7.13 | -7 | -157 |

## Round 17

Pistol winner TEAM_2; round winner TEAM_2; half round 5.

**TEAM_1** lost L=11,050; target H=19,500; funding U=20,850; gap D=0; observed next-equipment gap G=7,350; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 2,000 | 2,000 | 1,800 | 1,800 | 3,600 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 500 | 350 | 2,850 | 3,900 |
| Fentlie#Freak | 1,500 | 1,500 | 4,600 | 4,600 | 200 | 3,900 |
| Diamondkidflash#9059 | 1,900 | 1,900 | 4,600 | 4,600 | 350 | 3,900 |
| IDKNotDumbIGuess#NA1 | 1,400 | 1,400 | 2,200 | 2,200 | 1,700 | 3,900 |

**TEAM_2** lost L=2,850; target H=19,500; funding U=40,100; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,250 | 0 | 4,250 | 4,250 | 3,350 | 3,900 |
| Momomimo#hru | 2,850 | 2,850 | 4,150 | 4,150 | 2,950 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 5,800 | 3,900 |
| Deemo#Derf | 4,400 | 0 | 5,200 | 5,200 | 5,450 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 0 | 5,200 | 5,200 | 3,050 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486655 | 12.112s | Deemo#Derf -> Fentlie#Freak | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,500 | 7.75 | 0.00 | 7.75 | 2.33 (30%, absorbed) |
| event 486653 | 19.066s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 486654 | 27.605s | NPrightdolphin#NA1 -> chickenfries27#6819 | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486658 | 31.545s | IP Thoaiyama#Phan -> steamerbtw123#6897 | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486657 | 38.342s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | enemy | 1v5 | 60 | 1.000 | 60.0 | 10.2 | 2,850 | 14.73 | 0.00 | 14.73 | 4.42 (30%, absorbed) |
| event 486656 | 40.646s | Deemo#Derf -> IDKNotDumbIGuess#NA1 | enemy | 4v1 | 50 | 1.000 | 50.0 | 50.0 | 1,400 | 7.24 | 0.00 | 7.24 | 2.17 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +818 | 574 | +220 | 31.79 | 0.00 | +32 | +826 |
| Deemo#Derf | +666 | 466 | +200 | 14.99 | 0.00 | +15 | +681 |
| IP Thoaiyama#Phan | +146 | 96 | +50 | 10.34 | 0.00 | +10 | +156 |
| IDKNotDumbIGuess#NA1 | +129 | 119 | +10 | 14.73 | 2.17 | +13 | +142 |
| steamerbtw123#6897 | +88 | 138 | -50 | 0.00 | 3.10 | -3 | +85 |
| Momomimo#hru | +35 | 45 | -10 | 0.00 | 4.42 | -4 | +31 |
| Osmin#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| chickenfries27#6819 | -107 | 0 | -90 | 0.00 | 6.59 | -7 | -97 |
| Diamondkidflash#9059 | -137 | 0 | -130 | 0.00 | 2.95 | -3 | -133 |
| Fentlie#Freak | -150 | 0 | -150 | 0.00 | 2.33 | -2 | -152 |

## Round 18

Pistol winner TEAM_2; round winner TEAM_2; half round 6.

**TEAM_1** lost L=13,550; target H=19,500; funding U=21,800; gap D=0; observed next-equipment gap G=3,950; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 1,800 | 1,800 | 6,800 | 6,800 | 500 | 3,900 |
| chickenfries27#6819 | 350 | 350 | 4,400 | 4,250 | 1,700 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 3,650 | 3,650 | 250 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 1,900 | 1,900 | 1,600 | 3,900 |
| IDKNotDumbIGuess#NA1 | 2,200 | 2,200 | 2,200 | 2,200 | 2,200 | 3,900 |

**TEAM_2** lost L=13,600; target H=19,500; funding U=41,450; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,250 | 4,250 | 4,600 | 4,600 | 2,600 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 4,150 | 4,150 | 2,300 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,300 | 4,300 | 7,400 | 3,900 |
| Deemo#Derf | 5,200 | 0 | 5,200 | 5,200 | 7,500 | 3,900 |
| IP Thoaiyama#Phan | 5,200 | 5,200 | 4,400 | 4,400 | 2,150 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486663 | 12.157s | Fentlie#Freak -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 5,200 | 26.88 | 0.00 | 26.88 | 8.06 (30%, absorbed) |
| event 486665 | 16.064s | steamerbtw123#6897 -> Osmin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486666 | 33.035s | Momomimo#hru -> Diamondkidflash#9059 | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486660 | 37.670s | Deemo#Derf -> steamerbtw123#6897 | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 1,800 | 9.30 | 0.00 | 9.30 | 2.79 (30%, absorbed) |
| event 486659 | 44.488s | NPrightdolphin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 2,200 | 11.37 | 0.00 | 11.37 | 3.41 (30%, absorbed) |
| event 486664 | 47.359s | Fentlie#Freak -> Momomimo#hru | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486661 | 53.026s | Deemo#Derf -> chickenfries27#6819 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 350 | 1.81 | 0.00 | 1.81 | 0.54 (30%, absorbed) |
| event 486662 | 68.860s | Deemo#Derf -> Fentlie#Freak | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Deemo#Derf | +1,163 | 664 | +490 | 34.89 | 0.00 | +35 | +1,189 |
| Fentlie#Freak | +619 | 510 | +190 | 48.33 | 7.13 | +41 | +741 |
| NPrightdolphin#NA1 | +472 | 280 | +180 | 11.37 | 0.00 | +11 | +471 |
| steamerbtw123#6897 | +309 | 341 | -30 | 21.97 | 2.79 | +19 | +330 |
| Momomimo#hru | +153 | 138 | -50 | 23.78 | 6.44 | +17 | +105 |
| Diamondkidflash#9059 | +5 | 150 | -120 | 0.00 | 7.13 | -7 | +23 |
| Osmin#NA1 | -80 | 50 | -130 | 0.00 | 6.59 | -7 | -87 |
| IP Thoaiyama#Phan | -76 | 49 | -150 | 0.00 | 8.06 | -8 | -109 |
| IDKNotDumbIGuess#NA1 | -123 | 69 | -180 | 0.00 | 3.41 | -3 | -114 |
| chickenfries27#6819 | -191 | 0 | -200 | 0.00 | 0.54 | -1 | -201 |

## Round 19

Pistol winner TEAM_2; round winner TEAM_2; half round 7.

**TEAM_1** lost L=18,800; target H=19,500; funding U=21,900; gap D=0; observed next-equipment gap G=9,000; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 6,800 | 6,800 | 1,750 | 1,750 | 2,250 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 550 | 3,900 |
| Fentlie#Freak | 3,650 | 3,650 | 800 | 800 | 2,350 | 3,900 |
| Diamondkidflash#9059 | 1,900 | 1,900 | 2,050 | 2,050 | 3,350 | 3,900 |
| IDKNotDumbIGuess#NA1 | 2,200 | 2,200 | 2,000 | 2,000 | 2,900 | 3,900 |

**TEAM_2** lost L=13,950; target H=19,500; funding U=40,200; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 6,400 | 6,400 | 4,400 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 4,150 | 4,150 | 1,450 | 3,900 |
| NPrightdolphin#NA1 | 4,300 | 0 | 4,100 | 4,100 | 5,700 | 3,900 |
| Deemo#Derf | 5,200 | 5,200 | 4,400 | 4,400 | 4,600 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 0 | 4,400 | 4,400 | 4,550 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486668 | 15.514s | Deemo#Derf -> Fentlie#Freak | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,650 | 18.87 | 0.00 | 18.87 | 5.66 (30%, absorbed) |
| event 486669 | 19.378s | Osmin#NA1 -> steamerbtw123#6897 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 6,800 | 35.15 | 0.00 | 35.15 | 10.54 (30%, absorbed) |
| event 486670 | 30.484s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 5,200 | 26.88 | 0.00 | 26.88 | 8.06 (30%, absorbed) |
| event 486671 | 36.770s | Diamondkidflash#9059 -> Momomimo#hru | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486672 | 38.064s | Diamondkidflash#9059 -> Osmin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486673 | 53.500s | IP Thoaiyama#Phan -> chickenfries27#6819 | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486667 | 57.748s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 1,900 | 9.82 | 0.00 | 9.82 | 2.95 (30%, absorbed) |
| event 486674 | 64.176s | IP Thoaiyama#Phan -> IDKNotDumbIGuess#NA1 | enemy | 2v1 | 130 | 1.000 | 130.0 | 130.0 | 2,200 | 11.37 | 0.00 | 11.37 | 3.41 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| IP Thoaiyama#Phan | +882 | 559 | +300 | 33.34 | 0.00 | +33 | +892 |
| Diamondkidflash#9059 | +664 | 531 | +140 | 45.23 | 2.95 | +42 | +713 |
| Osmin#NA1 | +298 | 338 | -50 | 35.15 | 7.13 | +28 | +316 |
| IDKNotDumbIGuess#NA1 | +280 | 288 | -10 | 26.88 | 3.41 | +23 | +301 |
| Deemo#Derf | +222 | 188 | +30 | 18.87 | 8.06 | +11 | +229 |
| NPrightdolphin#NA1 | +208 | 6 | +200 | 9.82 | 0.00 | +10 | +216 |
| chickenfries27#6819 | -110 | 81 | -170 | 0.00 | 6.59 | -7 | -96 |
| Momomimo#hru | -115 | 34 | -160 | 0.00 | 6.44 | -6 | -132 |
| steamerbtw123#6897 | -146 | 0 | -130 | 0.00 | 10.54 | -11 | -141 |
| Fentlie#Freak | -158 | 0 | -150 | 0.00 | 5.66 | -6 | -156 |

## Round 20

Pistol winner TEAM_2; round winner TEAM_1; half round 8.

**TEAM_1** lost L=6,600; target H=19,500; funding U=27,550; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 1,750 | 1,750 | 4,900 | 4,900 | 1,050 | 3,900 |
| chickenfries27#6819 | 4,250 | 0 | 4,400 | 4,250 | 4,200 | 3,900 |
| Fentlie#Freak | 800 | 800 | 4,600 | 4,600 | 750 | 3,900 |
| Diamondkidflash#9059 | 2,050 | 2,050 | 6,400 | 6,400 | 650 | 3,900 |
| IDKNotDumbIGuess#NA1 | 2,000 | 2,000 | 4,100 | 4,100 | 1,400 | 3,900 |

**TEAM_2** lost L=23,450; target H=19,500; funding U=29,650; gap D=0; observed next-equipment gap G=700; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 6,400 | 6,400 | 4,600 | 4,600 | 2,250 | 3,900 |
| Momomimo#hru | 4,150 | 4,150 | 3,200 | 3,200 | 350 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,100 | 4,100 | 3,600 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 4,400 | 4,400 | 2,400 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 4,400 | 4,400 | 2,250 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486675 | 14.057s | NPrightdolphin#NA1 -> steamerbtw123#6897 | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 1,750 | 9.05 | 0.00 | 9.05 | 2.71 (30%, absorbed) |
| event 486682 | 27.945s | Diamondkidflash#9059 -> Deemo#Derf | enemy | 4v5 | 140 | 1.000 | 140.0 | 140.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486683 | 28.551s | IP Thoaiyama#Phan -> IDKNotDumbIGuess#NA1 | enemy | 4v4 | 170 | 1.000 | 170.0 | 170.0 | 2,000 | 10.34 | 0.00 | 10.34 | 3.10 (30%, absorbed) |
| event 486677 | 38.895s | chickenfries27#6819 -> IP Thoaiyama#Phan | enemy | 3v4 | 160 | 1.000 | 160.0 | 160.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486678 | 40.403s | chickenfries27#6819 -> Osmin#NA1 | enemy | 3v3 | 180 | 1.000 | 180.0 | 180.0 | 6,400 | 33.08 | 0.00 | 33.08 | 9.92 (30%, absorbed) |
| event 486681 | 55.243s | Momomimo#hru -> Fentlie#Freak | enemy | 2v3 | 170 | 1.000 | 170.0 | 85.0 | 800 | 4.14 | 0.00 | 4.14 | 1.24 (30%, absorbed) |
| event 486676 | 56.452s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 2,050 | 10.60 | 0.00 | 10.60 | 3.18 (30%, absorbed) |
| event 486679 | 59.433s | chickenfries27#6819 -> Momomimo#hru | enemy | 1v2 | 190 | 1.000 | 190.0 | 190.0 | 4,150 | 21.45 | 0.00 | 21.45 | 6.44 (30%, absorbed) |
| event 486680 | 80.855s | chickenfries27#6819 -> NPrightdolphin#NA1 | enemy | 1v1 | 250 | 1.000 | 250.0 | 250.0 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| chickenfries27#6819 | +1,467 | 820 | +780 | 98.47 | 0.00 | +98 | +1,698 |
| NPrightdolphin#NA1 | +572 | 500 | +100 | 19.64 | 6.36 | +13 | +613 |
| Diamondkidflash#9059 | +301 | 312 | -60 | 22.74 | 3.18 | +20 | +272 |
| IP Thoaiyama#Phan | +102 | 118 | +10 | 10.34 | 6.82 | +4 | +132 |
| Momomimo#hru | +93 | 125 | -20 | 4.14 | 6.44 | -2 | +103 |
| Fentlie#Freak | +2 | 62 | -85 | 0.00 | 1.24 | -1 | -24 |
| IDKNotDumbIGuess#NA1 | +15 | 138 | -170 | 0.00 | 3.10 | -3 | -35 |
| Deemo#Derf | -77 | 68 | -140 | 0.00 | 6.82 | -7 | -79 |
| steamerbtw123#6897 | -48 | 69 | -150 | 0.00 | 2.71 | -3 | -84 |
| Osmin#NA1 | -156 | 0 | -180 | 0.00 | 9.92 | -10 | -190 |

## Round 21

Pistol winner TEAM_2; round winner TEAM_1; half round 9.

**TEAM_1** lost L=0; target H=19,500; funding U=40,600; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 4,900 | 0 | 6,500 | 6,500 | 4,050 | 3,900 |
| chickenfries27#6819 | 4,250 | 0 | 4,400 | 4,250 | 7,200 | 3,900 |
| Fentlie#Freak | 4,600 | 0 | 4,600 | 4,600 | 2,650 | 3,900 |
| Diamondkidflash#9059 | 6,400 | 0 | 4,600 | 4,600 | 3,200 | 3,900 |
| IDKNotDumbIGuess#NA1 | 4,100 | 0 | 3,900 | 3,900 | 4,000 | 3,900 |

**TEAM_2** lost L=20,700; target H=19,500; funding U=22,750; gap D=0; observed next-equipment gap G=700; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 4,600 | 4,600 | 750 | 3,900 |
| Momomimo#hru | 3,200 | 3,200 | 3,200 | 3,200 | 150 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 4,100 | 4,300 | 4,300 | 1,700 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 4,400 | 4,400 | 900 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 4,400 | 4,400 | 450 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486684 | 6.649s | Fentlie#Freak -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486687 | 12.363s | steamerbtw123#6897 -> IP Thoaiyama#Phan | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,400 | 22.74 | 0.00 | 22.74 | 6.82 (30%, absorbed) |
| event 486685 | 27.547s | Fentlie#Freak -> NPrightdolphin#NA1 | enemy | 5v3 | 90 | 1.000 | 90.0 | 90.0 | 4,100 | 21.19 | 0.00 | 21.19 | 6.36 (30%, absorbed) |
| event 486686 | 28.541s | Fentlie#Freak -> Osmin#NA1 | enemy | 5v2 | 50 | 1.000 | 50.0 | 50.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486688 | 41.925s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | enemy | 5v1 | 40 | 1.000 | 40.0 | 40.0 | 3,200 | 16.54 | 0.00 | 16.54 | 4.96 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fentlie#Freak | +1,158 | 874 | +290 | 67.71 | 0.00 | +68 | +1,232 |
| steamerbtw123#6897 | +318 | 188 | +130 | 22.74 | 0.00 | +23 | +341 |
| IDKNotDumbIGuess#NA1 | +226 | 188 | +40 | 16.54 | 0.00 | +17 | +245 |
| chickenfries27#6819 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Diamondkidflash#9059 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| Osmin#NA1 | +0 | 50 | -50 | 0.00 | 7.13 | -7 | -7 |
| Momomimo#hru | -38 | 0 | -40 | 0.00 | 4.96 | -5 | -45 |
| NPrightdolphin#NA1 | -84 | 0 | -90 | 0.00 | 6.36 | -6 | -96 |
| Deemo#Derf | -100 | 50 | -150 | 0.00 | 6.82 | -7 | -107 |
| IP Thoaiyama#Phan | -130 | 0 | -130 | 0.00 | 6.82 | -7 | -137 |

## Round 22

Pistol winner TEAM_2; round winner TEAM_1; half round 10.

**TEAM_1** lost L=8,850; target H=19,500; funding U=44,650; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 6,500 | 0 | 6,400 | 6,400 | 5,450 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 4,950 | 3,900 |
| Fentlie#Freak | 4,600 | 0 | 4,600 | 4,600 | 5,650 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 2,700 | 3,900 |
| IDKNotDumbIGuess#NA1 | 3,900 | 0 | 3,900 | 3,900 | 6,400 | 3,900 |

**TEAM_2** lost L=20,900; target H=19,500; funding U=18,700; gap D=800; observed next-equipment gap G=1,450; activation 0.2051; severity pool 297.44 -> CONSTRAINED next buy (80% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,600 | 4,600 | 4,200 | 4,200 | 0 | 3,900 |
| Momomimo#hru | 3,200 | 3,200 | 2,850 | 2,850 | 200 | 3,900 |
| NPrightdolphin#NA1 | 4,300 | 4,300 | 4,100 | 4,100 | 300 | 3,900 |
| Deemo#Derf | 4,400 | 4,400 | 3,850 | 3,850 | 150 | 3,900 |
| IP Thoaiyama#Phan | 4,400 | 4,400 | 3,550 | 3,550 | 0 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486694 | 59.778s | Diamondkidflash#9059 -> IP Thoaiyama#Phan | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 4,400 | 22.74 | 3.24 | 25.98 | 20.78 (80%, constrained) |
| event 486695 | 60.076s | Diamondkidflash#9059 -> NPrightdolphin#NA1 | enemy | 5v4 | 130 | 1.000 | 130.0 | 130.0 | 4,300 | 22.23 | 3.16 | 25.39 | 20.31 (80%, constrained) |
| event 486696 | 64.586s | Diamondkidflash#9059 -> Momomimo#hru | enemy | 5v3 | 90 | 1.000 | 90.0 | 31.5 | 3,200 | 16.54 | 2.35 | 18.89 | 15.12 (80%, constrained) |
| event 486690 | 66.567s | Osmin#NA1 -> chickenfries27#6819 | enemy | 2v5 | 70 | 1.000 | 70.0 | 70.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486689 | 68.146s | Deemo#Derf -> Diamondkidflash#9059 | enemy | 2v5 | 70 | 1.000 | 70.0 | 52.5 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486692 | 73.553s | Fentlie#Freak -> Deemo#Derf | enemy | 4v2 | 80 | 1.000 | 80.0 | 80.0 | 4,400 | 22.74 | 3.24 | 25.98 | 20.78 (80%, constrained) |
| event 486691 | 73.948s | Osmin#NA1 -> chickenfries27#6819 | enemy | 1v4 | 70 | 1.000 | 70.0 | 11.9 | 0 | 0.00 | 0.00 | 0.00 | 0.00 (30%, absorbed) |
| event 486693 | 76.426s | Fentlie#Freak -> Osmin#NA1 | enemy | 3v1 | 70 | 1.000 | 70.0 | 70.0 | 4,600 | 23.78 | 3.38 | 27.16 | 21.73 (80%, constrained) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Diamondkidflash#9059 | +1,382 | 924 | +318 | 70.26 | 7.13 | +63 | +1,305 |
| Osmin#NA1 | +553 | 529 | +70 | 21.97 | 21.73 | +0 | +599 |
| Fentlie#Freak | +588 | 380 | +150 | 53.14 | 0.00 | +53 | +583 |
| Deemo#Derf | +186 | 236 | -10 | 23.78 | 20.78 | +3 | +229 |
| chickenfries27#6819 | +59 | 130 | -82 | 0.00 | 6.59 | -7 | +41 |
| steamerbtw123#6897 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| IDKNotDumbIGuess#NA1 | +0 | 0 | +0 | 0.00 | 0.00 | +0 | +0 |
| NPrightdolphin#NA1 | -50 | 131 | -130 | 0.00 | 20.31 | -20 | -19 |
| Momomimo#hru | -40 | 0 | -31 | 0.00 | 15.12 | -15 | -46 |
| IP Thoaiyama#Phan | -177 | 31 | -150 | 0.00 | 20.78 | -21 | -140 |

## Round 23

Pistol winner TEAM_2; round winner TEAM_2; half round 11.

**TEAM_1** lost L=23,750; target H=19,500; funding U=31,350; gap D=0; observed next-equipment gap G=0; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| steamerbtw123#6897 | 6,400 | 6,400 | 6,800 | 6,800 | 1,250 | 3,900 |
| chickenfries27#6819 | 4,250 | 4,250 | 4,400 | 4,250 | 3,000 | 3,900 |
| Fentlie#Freak | 4,600 | 4,600 | 4,400 | 4,400 | 3,450 | 3,900 |
| Diamondkidflash#9059 | 4,600 | 4,600 | 4,600 | 4,600 | 450 | 3,900 |
| IDKNotDumbIGuess#NA1 | 3,900 | 3,900 | 4,400 | 4,400 | 3,700 | 3,900 |

**TEAM_2** lost L=10,250; target H=19,500; funding U=23,650; gap D=0; observed next-equipment gap G=1,400; activation 0.0000; severity pool 0.00 -> funding ABSORBED its losses (30% death debits).

| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |
|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 4,200 | 0 | 4,600 | 4,600 | 300 | 3,900 |
| Momomimo#hru | 2,850 | 2,850 | 3,200 | 3,200 | 300 | 3,900 |
| NPrightdolphin#NA1 | 4,100 | 0 | 4,100 | 4,100 | 2,400 | 3,900 |
| Deemo#Derf | 3,850 | 3,850 | 4,400 | 4,400 | 2,250 | 3,900 |
| IP Thoaiyama#Phan | 3,550 | 3,550 | 3,200 | 3,200 | 300 | 3,900 |

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486703 | 12.485s | steamerbtw123#6897 -> Deemo#Derf | enemy | 5v5 | 150 | 1.000 | 150.0 | 150.0 | 3,850 | 19.90 | 0.00 | 19.90 | 5.97 (30%, absorbed) |
| event 486702 | 27.077s | chickenfries27#6819 -> Momomimo#hru | enemy | 5v4 | 130 | 1.000 | 130.0 | 45.5 | 2,850 | 14.73 | 0.00 | 14.73 | 4.42 (30%, absorbed) |
| event 486697 | 28.974s | NPrightdolphin#NA1 -> steamerbtw123#6897 | enemy | 3v5 | 120 | 1.000 | 120.0 | 120.0 | 6,400 | 33.08 | 0.00 | 33.08 | 9.92 (30%, absorbed) |
| event 486704 | 29.441s | IDKNotDumbIGuess#NA1 -> IP Thoaiyama#Phan | enemy | 4v3 | 130 | 1.000 | 130.0 | 130.0 | 3,550 | 18.35 | 0.00 | 18.35 | 5.50 (30%, absorbed) |
| event 486699 | 30.835s | Osmin#NA1 -> chickenfries27#6819 | enemy | 2v4 | 130 | 1.000 | 130.0 | 130.0 | 4,250 | 21.97 | 0.00 | 21.97 | 6.59 (30%, absorbed) |
| event 486698 | 41.020s | NPrightdolphin#NA1 -> Diamondkidflash#9059 | enemy | 2v3 | 170 | 1.000 | 170.0 | 170.0 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |
| event 486700 | 41.417s | Osmin#NA1 -> IDKNotDumbIGuess#NA1 | enemy | 2v2 | 200 | 1.000 | 200.0 | 200.0 | 3,900 | 20.16 | 0.00 | 20.16 | 6.05 (30%, absorbed) |
| event 486701 | 73.838s | Osmin#NA1 -> Fentlie#Freak | enemy | 2v1 | 130 | 1.487 | 193.4 | 193.4 | 4,600 | 23.78 | 0.00 | 23.78 | 7.13 (30%, absorbed) |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +1,095 | 704 | +523 | 65.90 | 0.00 | +66 | +1,293 |
| NPrightdolphin#NA1 | +777 | 511 | +290 | 56.86 | 0.00 | +57 | +858 |
| IDKNotDumbIGuess#NA1 | +349 | 338 | -70 | 18.35 | 6.05 | +12 | +280 |
| steamerbtw123#6897 | +225 | 156 | +30 | 19.90 | 9.92 | +10 | +196 |
| chickenfries27#6819 | +129 | 88 | +0 | 14.73 | 6.59 | +8 | +96 |
| IP Thoaiyama#Phan | +4 | 168 | -130 | 0.00 | 5.50 | -6 | +32 |
| Momomimo#hru | -52 | 0 | -46 | 0.00 | 4.42 | -4 | -50 |
| Deemo#Derf | -129 | 50 | -150 | 0.00 | 5.97 | -6 | -106 |
| Diamondkidflash#9059 | -106 | 50 | -170 | 0.00 | 7.13 | -7 | -127 |
| Fentlie#Freak | -129 | 0 | -193 | 0.00 | 7.13 | -7 | -200 |

## Round 24

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486706 | 13.295s | Osmin#NA1 -> steamerbtw123#6897 | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486709 | 21.478s | IP Thoaiyama#Phan -> Diamondkidflash#9059 | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 486710 | 23.610s | IP Thoaiyama#Phan -> Fentlie#Freak | combat | 5v3 | 90 | 1.000 | 90.0 | 90.0 | | | | | no economy this round |
| event 486705 | 25.783s | NPrightdolphin#NA1 -> chickenfries27#6819 | combat | 5v2 | 50 | 1.000 | 50.0 | 50.0 | | | | | no economy this round |
| event 486707 | 26.189s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | combat | 1v5 | 60 | 1.000 | 60.0 | 60.0 | | | | | no economy this round |
| event 486708 | 50.725s | IDKNotDumbIGuess#NA1 -> Momomimo#hru | combat | 1v4 | 70 | 1.000 | 70.0 | 70.0 | | | | | no economy this round |
| event 486711 | 77.923s | IP Thoaiyama#Phan -> IDKNotDumbIGuess#NA1 | combat | 3v1 | 70 | 1.301 | 91.1 | 91.1 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| IP Thoaiyama#Phan | +1,203 | 858 | +311 | 0.00 | 0.00 | +0 | +1,169 |
| IDKNotDumbIGuess#NA1 | +595 | 562 | +39 | 0.00 | 0.00 | +0 | +601 |
| Osmin#NA1 | +338 | 188 | +150 | 0.00 | 0.00 | +0 | +338 |
| NPrightdolphin#NA1 | +242 | 188 | +50 | 0.00 | 0.00 | +0 | +238 |
| Fentlie#Freak | -25 | 80 | -90 | 0.00 | 0.00 | +0 | -10 |
| Momomimo#hru | -40 | 22 | -70 | 0.00 | 0.00 | +0 | -48 |
| chickenfries27#6819 | -54 | 0 | -50 | 0.00 | 0.00 | +0 | -50 |
| Deemo#Derf | -60 | 0 | -60 | 0.00 | 0.00 | +0 | -60 |
| Diamondkidflash#9059 | -152 | 0 | -130 | 0.00 | 0.00 | +0 | -130 |
| steamerbtw123#6897 | -150 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |

## Round 25

Economy abstains: **pistol_half_or_ot_boundary** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486719 | 17.116s | IDKNotDumbIGuess#NA1 -> IP Thoaiyama#Phan | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486716 | 21.098s | steamerbtw123#6897 -> Deemo#Derf | combat | 5v4 | 130 | 1.000 | 130.0 | 65.0 | | | | | no economy this round |
| event 486712 | 25.653s | Osmin#NA1 -> steamerbtw123#6897 | combat | 3v5 | 120 | 1.000 | 120.0 | 120.0 | | | | | no economy this round |
| event 486713 | 31.116s | Osmin#NA1 -> chickenfries27#6819 | combat | 3v4 | 160 | 1.007 | 161.1 | 161.1 | | | | | no economy this round |
| event 486715 | 38.614s | Fentlie#Freak -> NPrightdolphin#NA1 | combat | 3v3 | 180 | 1.148 | 206.7 | 20.7 | | | | | no economy this round |
| event 486717 | 39.774s | Momomimo#hru -> Fentlie#Freak | combat | 2v3 | 170 | 1.170 | 198.9 | 198.9 | | | | | no economy this round |
| event 486714 | 41.709s | Osmin#NA1 -> IDKNotDumbIGuess#NA1 | combat | 2v2 | 200 | 1.206 | 241.3 | 241.3 | | | | | no economy this round |
| event 486718 | 47.781s | Momomimo#hru -> Diamondkidflash#9059 | combat | 2v1 | 130 | 1.321 | 171.7 | 171.7 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | +1,483 | 989 | +522 | 0.00 | 0.00 | +0 | +1,511 |
| Momomimo#hru | +635 | 311 | +371 | 0.00 | 0.00 | +0 | +682 |
| Fentlie#Freak | +233 | 236 | +8 | 0.00 | 0.00 | +0 | +244 |
| steamerbtw123#6897 | +198 | 188 | +10 | 0.00 | 0.00 | +0 | +198 |
| IDKNotDumbIGuess#NA1 | +124 | 188 | -91 | 0.00 | 0.00 | +0 | +97 |
| NPrightdolphin#NA1 | +80 | 98 | -21 | 0.00 | 0.00 | +0 | +77 |
| chickenfries27#6819 | -29 | 131 | -161 | 0.00 | 0.00 | +0 | -30 |
| Deemo#Derf | -65 | 0 | -65 | 0.00 | 0.00 | +0 | -65 |
| Diamondkidflash#9059 | -44 | 100 | -172 | 0.00 | 0.00 | +0 | -72 |
| IP Thoaiyama#Phan | -114 | 36 | -150 | 0.00 | 0.00 | +0 | -114 |

## Round 26

Economy abstains: **final_round** -- econ is exactly 0 this round.

| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | Killer econ credit | Victim econ debit (rate) |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| event 486720 | 25.192s | Deemo#Derf -> steamerbtw123#6897 | combat | 5v5 | 150 | 1.000 | 150.0 | 150.0 | | | | | no economy this round |
| event 486721 | 29.796s | Deemo#Derf -> Fentlie#Freak | combat | 5v4 | 130 | 1.000 | 130.0 | 130.0 | | | | | no economy this round |
| event 486724 | 40.690s | IDKNotDumbIGuess#NA1 -> Deemo#Derf | combat | 3v5 | 120 | 1.000 | 120.0 | 120.0 | | | | | no economy this round |
| event 486723 | 45.607s | chickenfries27#6819 -> Momomimo#hru | combat | 3v4 | 160 | 1.000 | 160.0 | 160.0 | | | | | no economy this round |
| event 486725 | 58.210s | Diamondkidflash#9059 -> NPrightdolphin#NA1 | combat | 3v3 | 180 | 1.000 | 180.0 | 180.0 | | | | | no economy this round |
| event 486722 | 68.611s | Osmin#NA1 -> IDKNotDumbIGuess#NA1 | combat | 2v3 | 170 | 1.000 | 170.0 | 170.0 | | | | | no economy this round |
| event 486726 | 84.075s | IP Thoaiyama#Phan -> Diamondkidflash#9059 | combat | 2v2 | 200 | 1.000 | 200.0 | 200.0 | | | | | no economy this round |
| event 486727 | 109.217s | IP Thoaiyama#Phan -> chickenfries27#6819 | combat | 2v1 | 130 | 1.296 | 168.4 | 168.4 | | | | | no economy this round |

| Player | Before impact | A*damage | B*leverage | Econ credit pts | Econ debit pts | C*econ | = After impact |
|---|---:|---:|---:|---:|---:|---:|---:|
| IP Thoaiyama#Phan | +773 | 430 | +368 | 0.00 | 0.00 | +0 | +798 |
| Deemo#Derf | +722 | 562 | +160 | 0.00 | 0.00 | +0 | +722 |
| Osmin#NA1 | +405 | 235 | +170 | 0.00 | 0.00 | +0 | +405 |
| chickenfries27#6819 | +203 | 186 | -8 | 0.00 | 0.00 | +0 | +178 |
| Diamondkidflash#9059 | +156 | 188 | -20 | 0.00 | 0.00 | +0 | +168 |
| Fentlie#Freak | +45 | 175 | -130 | 0.00 | 0.00 | +0 | +45 |
| Momomimo#hru | -31 | 129 | -160 | 0.00 | 0.00 | +0 | -31 |
| IDKNotDumbIGuess#NA1 | -38 | 12 | -50 | 0.00 | 0.00 | +0 | -38 |
| steamerbtw123#6897 | -150 | 0 | -150 | 0.00 | 0.00 | +0 | -150 |
| NPrightdolphin#NA1 | -168 | 0 | -180 | 0.00 | 0.00 | +0 | -180 |

## Match totals

| Player | Before | A*damage | B*leverage | Gross econ credit | Gross econ debit | C*econ | Econ / played round | After |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NPrightdolphin#NA1 | +10,290 | 7,656 | +2,845 | 388.70 | 207.17 | +182 | +7.00 | +10,683 |
| Fentlie#Freak | +7,351 | 6,451 | +481 | 598.63 | 76.83 | +522 | +20.08 | +7,454 |
| steamerbtw123#6897 | +6,842 | 6,186 | +599 | 555.62 | 70.71 | +485 | +18.65 | +7,270 |
| Deemo#Derf | +6,545 | 5,570 | +1,130 | 430.82 | 227.86 | +201 | +7.73 | +6,901 |
| Osmin#NA1 | +6,619 | 6,275 | +662 | 208.56 | 243.95 | -36 | -1.38 | +6,901 |
| chickenfries27#6819 | +4,389 | 3,943 | +558 | 347.13 | 85.60 | +259 | +9.96 | +4,760 |
| IDKNotDumbIGuess#NA1 | +4,431 | 4,240 | +71 | 407.37 | 54.12 | +355 | +13.65 | +4,666 |
| IP Thoaiyama#Phan | +4,158 | 5,282 | -1,016 | 111.91 | 247.24 | -136 | -5.23 | +4,130 |
| Diamondkidflash#9059 | +2,382 | 3,427 | -1,233 | 188.11 | 93.81 | +96 | +3.69 | +2,290 |
| Momomimo#hru | +1,595 | 1,880 | -303 | 130.25 | 240.06 | -105 | -4.04 | +1,472 |
