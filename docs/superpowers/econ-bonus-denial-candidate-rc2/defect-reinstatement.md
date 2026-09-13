# Defect reinstatement: bonus-round denial rc2

Candidate `impact-bonus-denial-rc2` at scorer revision `90d9cd1`. Each defect is reinstated BEHIND THE SAME API in an isolated copy of `webapp/{app,scripts,tests}`; the working tree is never modified. A mutation whose target text does not occur exactly once is reported NOT APPLIED. Declared in the rc2 ledger amendment (check 5).

Tests run for every mutation: `test_econ_bonus_denial.py`, `test_impact_bonus_denial_integration.py`, `test_impact_manifest.py`, `test_round_player_spend_ingest.py`, `test_compare_econ_models.py`, `test_round_rewards.py`, `test_weapon_prices.py`, `test_release_candidate_review_frozen.py`.

Baseline: 120 passed in 2.93s.

| defect | file | status | detected | result | failing tests |
|---|---|---|---|---|---|
| B1 outcome factor applied twice | `app/scoring/econ_buy_disruption.py` | applied | yes | 5 failed, 115 passed in 2.86s | test_credit_recovery_nets_only_the_surplus_above_utility, test_environmental_self_and_team_deaths_take_the_debit_without_credit, test_qualifying_death_pays_factor_times_swing_value[Team, test_repeated_death_exposes_nothing ... |
| B2 threshold >= instead of strictly greater | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.82s | test_threshold_is_strictly_greater_than_1500_after_utility |
| B3 agent utility not subtracted from the kit | `app/scoring/econ_buy_disruption.py` | applied | yes | 11 failed, 109 passed in 2.94s | test_credit_recovery_nets_only_the_surplus_above_utility, test_environmental_self_and_team_deaths_take_the_debit_without_credit, test_in_round_feed_pickup_nets_the_upgrade, test_qualifying_death_pays_factor_times_swing_value[Team ... |
| B4 credit and feed recovery summed | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.86s | test_duplicate_evidence_takes_the_larger_never_the_sum |
| B5 credit recovery nets the full surplus | `app/scoring/econ_buy_disruption.py` | applied | yes | 3 failed, 117 passed in 3.00s | test_credit_recovery_nets_only_the_surplus_above_utility, test_duplicate_evidence_takes_the_larger_never_the_sum, test_recovery_is_pro_rata_across_qualifying_deaths |
| B6 carried pickup inferred without the spend test | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.85s | test_carried_feed_pickup_requires_spend_below_the_price |
| B7 net denied not floored at zero | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.89s | test_full_recovery_floors_net_denied_at_zero_and_keeps_totals_signed |
| B8 self/team kills of a pistol winner credited | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.85s | test_environmental_self_and_team_deaths_take_the_debit_without_credit |
| B9 pistol-loser victims scored by the denial branch | `app/scoring/econ_buy_disruption.py` | applied | yes | 15 failed, 105 passed in 2.96s | test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[10], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[11], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[14], test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80[15] ... |
| B10 a missing kill weapon is not refused | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.92s | test_missing_bonus_inputs_abstain[over10] |
| B11 kill_feed_incomplete disabled | `app/scoring/econ_buy_disruption.py` | applied | yes | 2 failed, 118 passed in 2.91s | test_kill_feed_incomplete_is_distinguished_from_a_round_without_kills, test_missing_kill_rows_abstain_rather_than_score_as_no_kills |
| B12 bonus-model abstentions report audit version 1 | `app/scoring/econ_buy_disruption.py` | applied | yes | 12 failed, 108 passed in 2.88s | test_missing_bonus_inputs_abstain[over0], test_missing_bonus_inputs_abstain[over10], test_missing_bonus_inputs_abstain[over1], test_missing_bonus_inputs_abstain[over2] ... |
| B13 source fingerprint omits the kill weapon | `app/scoring/impact_manifest.py` | applied | yes | 1 failed, 119 passed in 2.82s | test_a_weapon_only_change_moves_the_source_fingerprint |
| B14 absent spentCredits written as zero | `app/adapters/trackergg_browserstate_source.py` | applied | yes | 2 failed, 118 passed in 2.87s | test_absent_spent_credits_write_no_row_not_zero, test_null_spent_credits_write_no_row_and_do_not_abort_ingest |
| B15 next-round kills not passed to the calculator | `app/scoring/impact.py` | applied | yes | 5 failed, 115 passed in 2.85s | test_abstaining_rounds_are_not_counted_as_scored, test_small_corpus_reconciles_and_reports_the_round_two_change, test_bonus_denial_values_through_the_build_path, test_missing_kill_rows_abstain_rather_than_score_as_no_kills ... |
| B16 loss streak crosses halftime | `app/scoring/round_rewards.py` | applied | yes | 1 failed, 119 passed in 2.81s | test_streak_does_not_cross_halftime |
| B17 survivors of a lost round credited the loss bonus | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.87s | test_survivor_of_a_lost_round_banks_1000_not_the_loss_bonus |
| B18 in-round pickup without the loadout-coverage rule | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.85s | test_in_round_pickup_requires_that_the_killer_could_not_own_every_gun_they_fired |
| B19 loadout coverage ignores the guns already fired | `app/scoring/econ_buy_disruption.py` | applied | yes | 3 failed, 117 passed in 2.85s | test_duplicate_evidence_takes_the_larger_never_the_sum, test_in_round_feed_pickup_nets_the_upgrade, test_in_round_pickup_requires_that_the_killer_could_not_own_every_gun_they_fired |
| B20 spike-detonation deaths counted as survivals | `app/scoring/econ_buy_disruption.py` | applied | yes | 1 failed, 119 passed in 2.91s | test_a_spike_death_is_a_death_not_a_survival |
| B21 trace falls back to the 30/80 budget for bonus rounds | `scripts/release_candidate_review.py` | applied | yes | 1 failed, 119 passed in 2.85s | test_trace_explains_bonus_denial_rounds_with_the_bonus_audit |
| B22 comparison counts abstaining rounds as scored | `scripts/compare_econ_models.py` | applied | yes | 1 failed, 119 passed in 2.83s | test_abstaining_rounds_are_not_counted_as_scored |
| B23 bonus manifest omits the bonus formula line | `app/scoring/impact_manifest.py` | applied | yes | 1 failed, 119 passed in 2.81s | test_bonus_release_manifest_describes_the_bonus_rule |
| B24 null spentCredits inserted | `app/adapters/trackergg_browserstate_source.py` | applied | yes | 1 failed, 119 passed in 2.97s | test_null_spent_credits_write_no_row_and_do_not_abort_ingest |

**24 of 24 mutations applied; 24 of 24 detected.**

Relative to rc1's 16 mutations:
- **B14 rewritten:** the adapter's spend guard changed, so it now makes an absent value default to 0.
- **B17-B24 added** for the review fixes and the owner's rules:
  - survive-loss reward ignored
  - loadout-coverage rule removed, or applied without the guns already fired
  - spike-detonation deaths counted as survivals
  - trace falling back to the 30/80 budget
  - comparison counting abstentions
  - bonus manifest description dropped
  - null spend inserted
