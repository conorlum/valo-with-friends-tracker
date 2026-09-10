# Abyss 3104: 30% absorbed / 80% disrupted death penalties

Same V2 killer credits and frozen source. Only the death penalty changes. ECON points only, C=1, scale=1007.9209.

Absorbed loss: debit = 30% of the small equipment damage value. Constrained next buy: debit = 80% of (small value + allocated disruption value). Neither rate multiplies the previous scarcity debit.

A positive existing V2 severity pool selects 80%; otherwise 30%. Existing eligibility, carryover targets and first-kit loss deduplication stay fixed. The funding test estimates disruption; it does not establish counterfactual causation.

| Player | Team | Gross credit | New debit | Old net | New net | Change | New net / played round |
|---|---|---:|---:|---:|---:|---:|---:|
| Osmin#NA1 | TEAM_2 | 853.47 | 145.94 | -100 | +708 | +808 | +29.50 |
| DoubleBl1nd#BEEF | TEAM_2 | 455.39 | 175.16 | -525 | +277 | +802 | +11.54 |
| VorteXx#Val | TEAM_1 | 445.85 | 236.78 | -727 | +209 | +936 | +8.71 |
| Helpless#qiqi | TEAM_1 | 442.73 | 248.60 | -761 | +196 | +957 | +8.17 |
| ZETA 3y5#213 | TEAM_1 | 457.23 | 263.29 | -718 | +194 | +912 | +8.08 |
| ternstyle#GIGI | TEAM_2 | 234.04 | 85.44 | -689 | +148 | +837 | +6.17 |
| NPrightdolphin#NA1 | TEAM_2 | 251.52 | 139.28 | -541 | +111 | +652 | +4.62 |
| Najumi#NPC | TEAM_2 | 196.06 | 144.64 | -682 | +49 | +731 | +2.04 |
| Mokalover67#ILLIT | TEAM_1 | 234.41 | 231.56 | -923 | +4 | +927 | +0.17 |
| 1xgoofy#56719 | TEAM_1 | 95.62 | 198.40 | -928 | -101 | +827 | -4.21 |

Net totals sum individually rounded player-round nets. Gross credit/debit columns retain full precision until display, so subtracting displayed match totals can differ slightly. The per-round denominator is all 24 played rounds.

| Team | Gross credit | New debit | Old net | New net |
|---|---:|---:|---:|---:|
| TEAM_1 | 1675.85 | 1178.63 | -4057 | +502 |
| TEAM_2 | 1990.49 | 690.46 | -2537 | +1293 |

## Selected deaths

| Round | Victim | Killer credit | Old death debit | New death debit | Rate |
|---|---|---:|---:|---:|---:|
| 2 | 1xgoofy#56719 | 1.55 | 8.20 | 0.47 | 30% |
| 3 | Osmin#NA1 | 19.90 | 19.90 | 5.97 | 30% |
| 6 | 1xgoofy#56719 | 124.04 | 194.18 | 99.23 | 80% |
| 16 | ternstyle#GIGI | 20.68 | 20.68 | 6.20 | 30% |
| 17 | ternstyle#GIGI | 23.26 | 23.26 | 6.98 | 30% |
| 22 | DoubleBl1nd#BEEF | 108.56 | 182.05 | 86.85 | 80% |

## Interpretation

Absorbed losses now have a small cost even if reserves are low. The large penalty requires the same constrained-buy condition used by the killer credit. This removes the separate reserve-depletion charge that dominated V2.

Crediting 100% and debiting 30%/80% creates a positive match-wide balance from enemy kills by construction. That is the requested non-zero-sum policy, not independent evidence of model quality. Individual players and teams can still finish negative. Self/team/environmental losses add debit without enemy credit.

The binary rate changes from 30% to 80% when a positive severity pool appears, including a tiny funding gap. The disruption amount is smooth, but the rate on the small background term has a step. This is preserved as requested, not silently smoothed.

199 artifact reconciliation checks pass. The comparison uses no database connection and changes no production scores.

Full player-round and death ledger: [JSON artifact](death-penalty-30-80.json). Previous calculation: [V2 walkthrough](walkthrough.md).
