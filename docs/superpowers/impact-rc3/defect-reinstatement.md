# rc3: each declared defect, reinstated and caught

Declared review check 6 in `../2026-09-07-predeclared-values.md` lists twelve defects the review must catch. This
records, for each, how it was put back and what caught it. Run 2026-09-17 at commit `c6dbf0d`, against the frozen
manifest `8e5c637b34b2ccb767fe2d16b18017eb8571f158623653c8f9bc4bf2ae10e20c`.

Two kinds of evidence appear below:

- **Shipped tests.** The defect is reinstated inside the test itself, so the evidence re-runs with the suite forever.
- **Source mutations.** The defect cannot be expressed from outside, so the source was changed, the tests were run, and
  the file was restored byte-for-byte and verified. `git status` was clean before and after every one.

Every failure listed is a failure on a **value** -- a wrong number or a wrong set -- not a crash, an import error or a
message-text assertion.

| # | declared defect | how it was reinstated | what caught it |
|---|---|---|---|
| 1 | the credit flag dropped from the manifest | shipped test deletes `enable_trade_credit` from the frozen comparator | `test_impact_manifest_rc3.py::test_a_manifest_that_loses_the_credit_flag_is_rejected` -- the manifest no longer matches its declared identity. Without this the file still parses, and parses as credit OFF |
| 2 | the scale ignored | source: `* weights.trade_credit_scale` removed from `impact.py` | `test_impact_trade_credit.py::test_trade_credit_scale_multiplies_the_credit_independent_of_leverage` -- `assert 252 == 126` |
| 3 | the leverage weight applied to the econ term, **under unequal weights** | shipped test scores with B = 2.5 and C = 3.5 and swaps which weight multiplies which term | `test_compare_rc3_decomposition.py::test_a_scorer_that_applies_the_weights_to_the_wrong_terms_is_caught` -- both `C*econ` and `B*leverage` mismatch. rc3 ships B = C = 2.5, where the swap is invisible, which is why the declaration demands unequal weights here |
| 4 | D left out of `impact` | shipped test subtracts `assists_component` from `impact` after scoring, leaving the term computed and stored | `test_compare_rc3_decomposition.py::test_a_scorer_that_drops_the_assists_term_from_impact_is_caught` -- the four-term identity mismatches |
| 5 | credit paid to the trader instead of the traded player | shipped test moves each credit from the victim to the trader | `test_compare_rc3_decomposition.py::test_a_scorer_that_pays_the_trader_instead_is_caught` -- both players' trade credit mismatch against the independent rebuild |
| 6 | the credit schedule reversed | source: `TRADE_CREDIT_SCHEDULE` inverted (0.60..0.30 becomes 0.30..0.60) | 12 failures across `test_impact_trade_credit.py`, `test_impact_manifest_rc3.py` and `test_compare_rc3_decomposition.py`; first `assert 126 == 252` |
| 7 | a three-term identity restored anywhere in the review | source: `assists_component` dropped from the reconciliation's identity in `release_candidate_review.py` | `test_release_candidate_review_rc3.py::test_site_review_of_rc3_reconciles_with_the_assists_term` and `::test_trace_of_rc3_reconciles_with_the_assists_term` |
| 8 | `trade_credit` or `scoring_version` left out of the persisted fields | source: `"trade_credit"` removed from `_PERSISTED_FIELDS` | `test_impact_persistence_contract.py::test_persisted_fields_are_exactly_the_models_value_columns` and `::test_the_stored_row_keeps_the_credit_and_the_version` -- caught against the **model's columns**, not the shared list |
| 9 | one row of the built table altered | shipped test edits a single row after the load | `test_swap_impact_scores.py::test_verify_catches_a_single_edited_row` -- the table no longer reads back as the artifact, and since 2026-09-17 it also no longer matches the approved comparison artifact |
| 10 | a swap killed mid-transaction | shipped test performs the swap and dies before COMMIT | `test_swap_impact_scores.py::test_an_interrupted_swap_changes_nothing_and_logs_nothing` -- the live table is untouched, no `impact_scores_v1` exists, the built table survives for a retry, and the log holds no `swap swapped` entry |
| 11 | a pre-0010 checkout updating a row that already says 3 | shipped test updates a version-3 row from a connection with no identity, with the gate open for rc3 | `test_release_write_gate.py::test_a_writer_that_never_touches_the_version_is_still_refused` -- refused, and the row still reads (100, 3). A row-value CHECK would have allowed it, which is why the gate asks the writer. **A real `origin/main` worktree does this in rehearsal probe (a), Stage 6** |
| 12 | an ingestion run with no identity | shipped test inserts a match with no identity on the connection | `test_release_write_gate.py::test_a_write_with_no_identity_is_refused` -- refused at the first insert with nothing committed. **The real ingestion path does this in rehearsal probe (b), Stage 6** |

## Beyond the declared list

These were not declared, but were reinstated the same way while the release tooling was being built, and are recorded
because each protects a step the release depends on:

- the write identity claimed on connect rather than on checkout (both pooled-connection cases refused a write);
- the gate's guard accepting an empty identity against a blank gate row (the write went through);
- the swap accepting a build with no clean verification, a rebuilt table, an open gate, or a verification the database
  had moved past;
- the verification trusting the sidecar instead of the approved artifact, and accepting an approval with no matches,
  missing rows or another manifest's digest;
- the measurement report reading a truncated artifact with `zip`;
- the cache cleared by TRUNCATE (a reader holding the cache stalled the swap to its lock timeout).

## What remains for Stage 6

Items 11 and 12 above are proven by tests today; the declaration also requires them against a **real** pre-0010
checkout and a real ingestion run, which is what the rehearsal's gate probes do. Those probes roll back and exit
nonzero unless the gate behaves exactly as stated.
