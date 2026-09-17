# Match 3133: Haven 13-8 -- live_legacy vs impact_rc3

Candidate `impact-rc3`, manifest LF-SHA-256 `8e5c637b34b2ccb767fe2d16b18017eb8571f158623653c8f9bc4bf2ae10e20c`.

- **Left: `live_legacy`** -- enable_econ_component=False, econ_model=None, weights A/B/C/D=1.25/1.0/1.0/0.0, trade credit OFF (scale 1.0), use_realized_swing=True, post-plant table OFF, pre-plant curve OFF
- **Right: `impact_rc3`** -- enable_econ_component=True, econ_model=buy_disruption_v2_30_80_bonus_denial, weights A/B/C/D=1.0/2.5/2.5/100.0, trade credit ON (scale 1.0), use_realized_swing=True, post-plant table OFF, pre-plant curve OFF

This is the TOTAL release change. Formula changes versus live legacy:

- structure: impact = A*damage + B*leverage + C*econ_component replaces damage + mean(econ, time, swing) kill-order products
- economy: the legacy econ-differential and swing factors leave leverage; econ is the separate buy-disruption component with 30%/80% death debits
- columns: econ_impact and swing_impact are written 0; econ_component is signed
- timing: unchanged legacy time factor (post-plant table and pre-plant curve OFF)
- weights: A(damage)=1.0, B(leverage)=2.5, C(econ)=2.5, D(assists)=100.0; ECON_SCALE unchanged
- trade credit: ON at scale 1.0 -- a player who is traded is credited a share of the trade kill's leverage on the declared schedule, added on top of the trader; the trade discount itself is unchanged

**Before is what production stores today**, so Change and Rank describe what a visitor will actually see move. The `live_legacy` REPLAY is shown separately as Legacy replay: it is what today's legacy code would compute now, which is not what production holds -- it differs from the stored value for 0 of 10 players, and 10 have no stored rows at all.

Econ points per played round divide by all 21 played rounds, including zero-econ boundary rounds. `of which trade credit` is already inside B*leverage; impact = A*damage + B*leverage + C*econ + D*assists.

| Player | Team | Before (stored) | After | Change | Rank | A*damage | B*leverage | of which trade credit | C*econ | D*assists | Gross credit | Gross debit | Econ / round | Legacy replay |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | 1 | n/a (unscored) | +8,838 | n/a | n/a->1 | 2,507 | +5,841 | +253 | -110 | +600 | 486.52 | 597.74 | -5.24 | +4,838 |
| Bigbootybruno#Bruno | 2 | n/a (unscored) | +8,230 | n/a | n/a->2 | 3,465 | +3,594 | +562 | +771 | +400 | 933.62 | 162.51 | +36.71 | +5,487 |
| Najumi#NPC | 1 | n/a (unscored) | +7,030 | n/a | n/a->3 | 2,842 | +3,288 | +683 | +100 | +800 | 693.80 | 594.58 | +4.76 | +4,694 |
| soufflégg#eggs | 2 | n/a (unscored) | +6,777 | n/a | n/a->4 | 2,239 | +2,717 | +1,150 | +1,321 | +500 | 1494.05 | 172.58 | +62.90 | +3,534 |
| NPrightdolphin#NA1 | 1 | n/a (unscored) | +5,415 | n/a | n/a->5 | 2,793 | +1,942 | +679 | +180 | +500 | 616.47 | 436.35 | +8.57 | +4,118 |
| Momomimo#hru | 1 | n/a (unscored) | +4,770 | n/a | n/a->6 | 1,878 | +1,400 | +655 | -8 | +1,500 | 436.12 | 444.06 | -0.38 | +2,537 |
| SimpLord87#3272 | 1 | n/a (unscored) | +4,742 | n/a | n/a->7 | 3,121 | +660 | +707 | +61 | +900 | 533.93 | 473.37 | +2.90 | +3,924 |
| TheAsianDude#3748 | 2 | n/a (unscored) | +4,423 | n/a | n/a->8 | 2,547 | +597 | +243 | +779 | +500 | 1007.92 | 230.98 | +37.10 | +3,438 |
| JTine#5277 | 2 | n/a (unscored) | +1,783 | n/a | n/a->9 | 1,468 | -883 | +818 | +498 | +700 | 699.62 | 201.86 | +23.71 | +1,199 |
| nemu#asg | 2 | n/a (unscored) | -507 | n/a | n/a->10 | 1,923 | -3,386 | +387 | +156 | +800 | 406.26 | 251.81 | +7.43 | +951 |

| Team | Before | After | C*econ after | Gross credit | Gross debit |
|---|---:|---:|---:|---:|---:|
| 1 | +0 (5 unscored) | +30,795 | +223 | 2766.85 | 2546.11 |
| 2 | +0 (5 unscored) | +20,706 | +3,525 | 4541.47 | 1019.74 |

## Per-round changes

Each cell: STORED impact -> after impact (C*econ after).

| Round | nemu#asg | TheAsianDude#3 | Osmin#NA1 | NPrightdolphin | soufflégg#eggs | JTine#5277 | Momomimo#hru | Najumi#NPC | Bigbootybruno# | SimpLord87#327 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | n/a->-154 (+0) | n/a->-345 (+0) | n/a->+1058 (+0) | n/a->+834 (+0) | n/a->-43 (+0) | n/a->-86 (+0) | n/a->+278 (+0) | n/a->+1006 (+0) | n/a->+57 (+0) | n/a->+0 (+0) |
| 2 | n/a->+522 (-1) | n/a->+1489 (+481) | n/a->-1047 (-387) | n/a->-122 (-246) | n/a->+2201 (+803) | n/a->+936 (+310) | n/a->-512 (-256) | n/a->-38 (-355) | n/a->+5 (-5) | n/a->+55 (+10) |
| 3 | n/a->+484 (+9) | n/a->+926 (+26) | n/a->-16 (-4) | n/a->+206 (+43) | n/a->+134 (-12) | n/a->+126 (-18) | n/a->+158 (+34) | n/a->-368 (-3) | n/a->+1183 (+27) | n/a->+92 (+51) |
| 4 | n/a->-248 (-19) | n/a->+222 (+38) | n/a->+586 (+50) | n/a->-112 (-16) | n/a->-73 (-11) | n/a->-242 (-7) | n/a->+250 (+0) | n/a->+0 (+0) | n/a->-342 (-17) | n/a->+1495 (+143) |
| 5 | n/a->-467 (-17) | n/a->+105 (+40) | n/a->+983 (+13) | n/a->+85 (+10) | n/a->+512 (+116) | n/a->+382 (+46) | n/a->+222 (+40) | n/a->+703 (+62) | n/a->-124 (-8) | n/a->+48 (-17) |
| 6 | n/a->+755 (+114) | n/a->-1 (-17) | n/a->-142 (-17) | n/a->-241 (-16) | n/a->+607 (+41) | n/a->+59 (-18) | n/a->+550 (+101) | n/a->-341 (-16) | n/a->+933 (+108) | n/a->-387 (-12) |
| 7 | n/a->+784 (+211) | n/a->+59 (-17) | n/a->+93 (-32) | n/a->-427 (-52) | n/a->+0 (+0) | n/a->+411 (+111) | n/a->-381 (-91) | n/a->+69 (-31) | n/a->+1371 (+183) | n/a->-114 (-93) |
| 8 | n/a->+365 (+0) | n/a->+9 (+0) | n/a->-116 (-10) | n/a->-76 (-5) | n/a->+595 (+20) | n/a->+409 (+52) | n/a->-79 (-6) | n/a->-331 (-6) | n/a->+900 (+43) | n/a->-383 (-8) |
| 9 | n/a->-550 (-17) | n/a->+87 (+37) | n/a->+994 (+58) | n/a->+1338 (+114) | n/a->-366 (-16) | n/a->-593 (-18) | n/a->+601 (+58) | n/a->+659 (-16) | n/a->+647 (+56) | n/a->-352 (-17) |
| 10 | n/a->+286 (+36) | n/a->-20 (+37) | n/a->+1079 (+109) | n/a->+877 (+41) | n/a->-400 (-15) | n/a->+492 (+40) | n/a->+814 (+58) | n/a->+613 (+43) | n/a->-357 (-17) | n/a->+217 (-17) |
| 11 | n/a->-294 (-17) | n/a->+384 (+36) | n/a->+0 (+0) | n/a->-366 (-16) | n/a->-173 (-10) | n/a->-443 (-18) | n/a->+158 (+0) | n/a->+741 (+57) | n/a->-312 (-17) | n/a->+1964 (+211) |
| 12 | n/a->-255 (+0) | n/a->-187 (+0) | n/a->+970 (+0) | n/a->+603 (+0) | n/a->-320 (+0) | n/a->-62 (+0) | n/a->+394 (+0) | n/a->+484 (+0) | n/a->+147 (+0) | n/a->+187 (+0) |
| 13 | n/a->+125 (+0) | n/a->+70 (+0) | n/a->+2318 (+0) | n/a->-258 (+0) | n/a->+399 (+0) | n/a->-461 (+0) | n/a->+104 (+0) | n/a->+326 (+0) | n/a->+262 (+0) | n/a->+587 (+0) |
| 14 | n/a->-214 (-19) | n/a->-130 (-9) | n/a->+0 (+0) | n/a->+626 (+71) | n/a->+508 (+235) | n/a->+73 (-11) | n/a->+250 (+0) | n/a->+209 (+0) | n/a->-352 (-16) | n/a->+880 (-173) |
| 15 | n/a->-135 (-15) | n/a->-199 (-17) | n/a->+338 (+13) | n/a->+0 (+0) | n/a->+108 (+19) | n/a->-306 (-16) | n/a->+539 (+50) | n/a->+1376 (+163) | n/a->-310 (-15) | n/a->+497 (-7) |
| 16 | n/a->-388 (-13) | n/a->+548 (+54) | n/a->-170 (-16) | n/a->+302 (+28) | n/a->+776 (+32) | n/a->+456 (+56) | n/a->+161 (+39) | n/a->-302 (-16) | n/a->+1059 (+110) | n/a->-367 (-17) |
| 17 | n/a->-342 (-17) | n/a->+161 (+41) | n/a->+777 (+78) | n/a->+408 (+58) | n/a->+292 (-12) | n/a->-38 (-18) | n/a->+125 (+0) | n/a->+334 (+59) | n/a->+54 (-17) | n/a->+1004 (+58) |
| 18 | n/a->-424 (-49) | n/a->+565 (+8) | n/a->+37 (-17) | n/a->+1316 (+91) | n/a->+115 (-21) | n/a->+101 (+7) | n/a->+0 (+0) | n/a->+1318 (+133) | n/a->+76 (+33) | n/a->-442 (-17) |
| 19 | n/a->-603 (-11) | n/a->+715 (+58) | n/a->+1080 (+69) | n/a->-341 (-16) | n/a->+1225 (+110) | n/a->+290 (+0) | n/a->+93 (-17) | n/a->-332 (-16) | n/a->+1025 (+94) | n/a->-322 (-17) |
| 20 | n/a->-330 (-19) | n/a->+112 (-17) | n/a->-289 (-17) | n/a->+596 (+91) | n/a->+528 (+42) | n/a->+587 (+0) | n/a->-351 (-18) | n/a->+577 (+42) | n/a->+2245 (+229) | n/a->-262 (-17) |
| 21 | n/a->+576 (+0) | n/a->-147 (+0) | n/a->+305 (+0) | n/a->+167 (+0) | n/a->+152 (+0) | n/a->-308 (+0) | n/a->+1396 (+0) | n/a->+327 (+0) | n/a->+63 (+0) | n/a->+345 (+0) |

## Economy by round (after)

| Round | Abstention | Team | L lost | H target | U funding | D gap | G observed gap | Severity pool | Rate | Next raw < 4200 | Credit pts | Debit pts |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 1 | pistol_half_or_ot_boundary | | | | | | | | | | | |
| 2 | | TEAM_1 | 15,750 | 15,650 | 16,250 | 0 | 9,950 | 0.0 | 80% | 5 | 31.01 | 1264.29 |
| 2 | | TEAM_2 | 2,400 | 19,500 | 25,950 | 0 | 1,300 | 0.0 | 30% | 4 | 1596.52 | 9.30 |
| 3 | | TEAM_1 | 5,850 | 19,500 | 22,350 | 0 | 500 | 0.0 | 30% | 2 | 144.08 | 22.68 |
| 3 | | TEAM_2 | 11,150 | 19,500 | 29,150 | 0 | 3,050 | 0.0 | 30% | 3 | 75.59 | 43.22 |
| 4 | | TEAM_1 | 4,100 | 19,500 | 31,900 | 0 | 0 | 0.0 | 30% | 1 | 193.18 | 15.89 |
| 4 | | TEAM_2 | 17,850 | 19,500 | 22,200 | 0 | 7,000 | 0.0 | 30% | 3 | 52.98 | 69.20 |
| 5 | | TEAM_1 | 17,850 | 19,500 | 31,900 | 0 | 750 | 0.0 | 30% | 2 | 177.03 | 69.20 |
| 5 | | TEAM_2 | 13,700 | 19,500 | 22,300 | 0 | 3,000 | 0.0 | 30% | 2 | 230.66 | 53.11 |
| 6 | | TEAM_1 | 20,350 | 19,500 | 22,700 | 0 | 1,400 | 0.0 | 30% | 1 | 117.59 | 78.89 |
| 6 | | TEAM_2 | 9,100 | 19,500 | 29,750 | 0 | 0 | 0.0 | 30% | 1 | 262.96 | 35.28 |
| 7 | | TEAM_1 | 20,050 | 19,500 | 18,750 | 750 | 10,650 | 2,048.1 | 80% | 5 | 120.18 | 418.99 |
| 7 | | TEAM_2 | 9,300 | 19,500 | 34,050 | 0 | 0 | 0.0 | 30% | 2 | 523.74 | 36.05 |
| 8 | | TEAM_1 | 8,850 | 19,500 | 24,550 | 0 | 0 | 0.0 | 30% | 1 | 0.00 | 34.31 |
| 8 | | TEAM_2 | 0 | 19,500 | 45,900 | 0 | 0 | 0.0 | 30% | 0 | 114.36 | 0.00 |
| 9 | | TEAM_1 | 8,550 | 19,500 | 30,800 | 0 | 0 | 0.0 | 30% | 1 | 229.37 | 33.15 |
| 9 | | TEAM_2 | 17,750 | 19,500 | 36,100 | 0 | 0 | 0.0 | 30% | 1 | 110.48 | 68.81 |
| 10 | | TEAM_1 | 12,800 | 19,500 | 34,000 | 0 | 0 | 0.0 | 30% | 1 | 282.99 | 49.62 |
| 10 | | TEAM_2 | 21,900 | 19,500 | 27,650 | 0 | 1,200 | 0.0 | 30% | 1 | 165.40 | 84.90 |
| 11 | | TEAM_1 | 4,100 | 19,500 | 43,100 | 0 | 0 | 0.0 | 30% | 1 | 267.49 | 15.89 |
| 11 | | TEAM_2 | 20,700 | 19,500 | 22,400 | 0 | 550 | 0.0 | 30% | 2 | 52.98 | 80.25 |
| 12 | pistol_half_or_ot_boundary | | | | | | | | | | | |
| 13 | pistol_half_or_ot_boundary | | | | | | | | | | | |
| 14 | | TEAM_1 | 2,950 | 15,700 | 27,600 | 0 | 1,500 | 0.0 | 80% | 5 | 115.98 | 218.33 |
| 14 | | TEAM_2 | 5,950 | 19,500 | 19,100 | 400 | 2,950 | 302.6 | 80% | 3 | 272.91 | 92.79 |
| 15 | | TEAM_1 | 1,800 | 19,500 | 41,050 | 0 | 150 | 0.0 | 30% | 1 | 226.14 | 6.98 |
| 15 | | TEAM_2 | 17,500 | 19,500 | 19,700 | 0 | 5,600 | 0.0 | 30% | 4 | 23.26 | 67.84 |
| 16 | | TEAM_1 | 20,800 | 19,500 | 31,100 | 0 | 1,650 | 0.0 | 30% | 2 | 98.21 | 80.63 |
| 16 | | TEAM_2 | 7,600 | 19,500 | 30,750 | 0 | 900 | 0.0 | 30% | 1 | 268.78 | 29.46 |
| 17 | | TEAM_1 | 4,500 | 19,500 | 42,850 | 0 | 0 | 0.0 | 30% | 1 | 271.36 | 17.44 |
| 17 | | TEAM_2 | 21,000 | 19,500 | 21,700 | 0 | 3,900 | 0.0 | 30% | 2 | 58.15 | 81.41 |
| 18 | | TEAM_1 | 13,200 | 19,500 | 45,300 | 0 | 0 | 0.0 | 30% | 1 | 240.90 | 51.17 |
| 18 | | TEAM_2 | 17,450 | 19,500 | 19,200 | 300 | 1,550 | 119.2 | 80% | 3 | 170.57 | 192.72 |
| 19 | | TEAM_1 | 21,500 | 19,500 | 33,100 | 0 | 0 | 0.0 | 30% | 1 | 86.58 | 83.35 |
| 19 | | TEAM_2 | 6,700 | 19,500 | 25,500 | 0 | 850 | 0.0 | 30% | 2 | 277.82 | 25.97 |
| 20 | | TEAM_1 | 22,000 | 19,500 | 25,650 | 0 | 500 | 0.0 | 30% | 2 | 164.76 | 85.29 |
| 20 | | TEAM_2 | 12,750 | 19,500 | 30,100 | 0 | 700 | 0.0 | 30% | 1 | 284.29 | 49.43 |
| 21 | final_round | | | | | | | | | | | |
