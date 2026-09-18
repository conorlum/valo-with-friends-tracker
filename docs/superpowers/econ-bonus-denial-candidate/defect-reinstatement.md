# Defect reinstatement: bonus-round denial

Candidate `impact-bonus-denial-rc1` at scorer revision `c7cbfd7`. Each defect is reinstated BEHIND THE SAME API in an isolated copy of `webapp/{app,scripts,tests}`; the working tree is never modified. A mutation whose target text does not occur exactly once is reported NOT APPLIED. Declared in the 2026-09-12 ledger entry (check 5) and plan Task 11 step 4.

Tests run for every mutation: `test_econ_bonus_denial.py`, `test_impact_bonus_denial_integration.py`, `test_impact_manifest.py`, `test_round_player_spend_ingest.py`, `test_compare_econ_models.py`, `test_round_rewards.py`, `test_weapon_prices.py`.

Baseline: 102 passed in 2.22s.

| defect | file | status | detected | result | failing tests |
|---|---|---|---|---|---|
| B1 outcome factor applied twice | `app/scoring/econ_buy_disruption.py` | applied | yes | 5 failed, 97 passed in 2.17s | test_credit_recovery_nets_only_the_surplus_above_utility, test_environmental_self_and_team_deaths_take_the_debit_without_credit, test_qualifying_death_pays_factor_times_swing_value[Team, test_repeated_death_exposes_nothing ... |
| B2 threshold >= instead of strictly greater | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.20s | test_threshold_is_strictly_greater_than_1500_after_utility |
| B3 agent utility not subtracted from the kit | `app/scoring/econ_buy_disruption.py` | applied | yes | 10 failed, 92 passed in 2.24s | test_credit_recovery_nets_only_the_surplus_above_utility, test_environmental_self_and_team_deaths_take_the_debit_without_credit, test_in_round_feed_pickup_nets_the_upgrade, test_qualifying_death_pays_factor_times_swing_value[Team ... |
| B4 credit and feed recovery summed | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.21s | test_duplicate_evidence_takes_the_larger_never_the_sum |
| B5 credit recovery nets the full surplus | `app/scoring/econ_buy_disruption.py` | applied | yes | 3 failed, 99 passed in 2.19s | test_credit_recovery_nets_only_the_surplus_above_utility, test_duplicate_evidence_takes_the_larger_never_the_sum, test_recovery_is_pro_rata_across_qualifying_deaths |
| B6 carried pickup inferred without the spend test | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.19s | test_carried_feed_pickup_requires_spend_below_the_price |
| B7 net denied not floored at zero | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.14s | test_full_recovery_floors_net_denied_at_zero_and_keeps_totals_signed |
| B8 self/team kills of a pistol winner credited | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.14s | test_environmental_self_and_team_deaths_take_the_debit_without_credit |
| B9 pistol-loser victims scored by the denial branch | `app/scoring/econ_buy_disruption.py` | applied | yes | 15 failed, 87 passed in 2.27s | test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[10], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[11], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[14], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[15] ... |
| B10 a missing kill weapon is not refused | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 101 passed in 2.23s | test_missing_bonus_inputs_abstain[over10] |
| B11 kill_feed_incomplete disabled | `app/scoring/econ_buy_disruption.py` | applied | yes | 2 failed, 100 passed in 2.31s | test_kill_feed_incomplete_is_distinguished_from_a_round_without_kills, test_missing_kill_rows_abstain_rather_than_score_as_no_kills |
| B12 bonus-model abstentions report audit version 1 | `app/scoring/econ_buy_disruption.py` | applied | yes | 12 failed, 90 passed in 2.28s | test_missing_bonus_inputs_abstain[over0], test_missing_bonus_inputs_abstain[over10], test_missing_bonus_inputs_abstain[over1], test_missing_bonus_inputs_abstain[over2] ... |
| B13 source fingerprint omits the kill weapon | `app/scoring/impact_manifest.py` | applied | yes | 1 failed, 101 passed in 2.22s | test_a_weapon_only_change_moves_the_source_fingerprint |
| B14 absent spentCredits written as zero | `app/adapters/trackergg_browserstate_source.py` | applied | yes | 1 failed, 101 passed in 2.29s | test_absent_spent_credits_write_no_row_not_zero |
| B15 next-round kills not passed to the calculator | `app/scoring/impact.py` | applied | yes | 3 failed, 99 passed in 2.24s | test_small_corpus_reconciles_and_reports_the_round_two_change, test_bonus_denial_values_through_the_build_path, test_missing_kill_rows_abstain_rather_than_score_as_no_kills |
| B16 loss streak crosses halftime | `app/scoring/round_rewards.py` | applied | yes | 1 failed, 101 passed in 2.15s | test_streak_does_not_cross_halftime |

**16 of 16 mutations applied; 16 of 16 detected.**

B8 differs from the plan's wording: the plan changed only the event record for a self/team kill, which never reaches a player total, so the mutation also credits any non-null killer. B15 (next-round kills not passed) and B16 (loss streak crossing halftime) were added to cover `impact.py` and `round_rewards.py`.
