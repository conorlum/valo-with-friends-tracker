# Warm builds: the state table

The replay worker's warm-build scheduler (`replay_worker/server.py`, `ControlRunner`) as one table: every event, what
it does to each field of one warm identity, and the test that pins it. Written after five branch-review rounds kept
finding interleavings the code handled one way and the next fix another. Any change to the scheduler changes this
table first, and each row keeps a test.

## The fields (per warm identity, `warm_id` = `map:heights[:features]|height_mode`)

| Field | Meaning |
|---|---|
| `warm_names` | the visibility file its warm build named ("tracked") |
| `warm_read` | some round has read the tracked file (its later eviction is cleanup, not a D3 loss) |
| `warm_confirmed` | cleared for parallel rounds: this cycle's probe read the tracked file |
| `warm_epoch` | the confirmation cycle; a released round carries its cycle (`job.warm_epoch`) |
| `warm_probe` | the one probe round in flight |
| `warm_failures`, `warm_missed`, `warm_vanished` | the fallback counts; `_spent` adds them; at `WARM_BUILD_TRIES` (2) the identity falls back |
| `warm_ids` | the fallback's warmth: a non-leased round of this identity warmed the map |
| `warm_vanished_seen`, `warm_rewarmed` | cumulative, for `/health` only |
| `warm` (per `map:heights`, shared) | the old warmth flag, used only with warm builds off |

`_withdraw` = `warm_confirmed` off, `warm_epoch` + 1, `warm_ids` off. Only the current cycle's probe confirms and
clears the counts. Counts live in memory: a worker restart starts every identity over.

## Events

| # | Event | Effect | Test |
|---|---|---|---|
| 1 | warm build ok, a new file name, on the disk | track it; `warm_read` off; `_withdraw` | `test_a_cold_map_gets_one_warm_build_one_probe_then_every_round_at_once` |
| 2 | warm build ok, but its file left the disk before it settled | `_note_gone` (unread, so a D3 loss) | `test_an_identity_whose_file_leaves_the_disk_after_every_warm_build_ends_in_the_fallback` |
| 3 | warm build failed | `warm_failures` + 1 | `test_a_warm_build_that_keeps_failing_falls_back_to_one_warming_round` |
| 4 | warm start refused, within 1800 s of the first refusal | retry after 60 s, nothing counted | `test_a_refused_warm_start_spends_no_try_and_is_asked_again` |
| 5 | warm start refused, past 1800 s | `warm_failures` + 1 | `test_refused_warm_starts_are_free_only_for_so_long` |
| 6 | scheduling: tracked file missing, its probe still out | wait for the probe | `test_a_probe_whose_file_goes_while_it_settles_is_not_a_d3_loss` |
| 7 | scheduling: tracked file missing, it was read | `_note_gone`: `warm_rewarmed` + 1, untrack, `_withdraw`, `warm` off | `test_a_file_read_before_a_withdrawal_is_not_a_d3_loss_when_evicted` |
| 8 | scheduling: tracked file missing, never read | `_note_gone`: `warm_vanished` + 1 (D3 alarm), untrack, `_withdraw`; checked before the tries | `test_a_file_lost_after_the_tries_are_spent_still_raises_the_alarm` |
| 9 | scheduling: tries left, file ready, not confirmed, no probe | release one probe, leased, with the cycle | `test_a_cold_map_gets_one_warm_build_one_probe_then_every_round_at_once` |
| 10 | scheduling: tries left, file ready, confirmed | release every round, leased, with the cycle | `test_after_confirmation_rounds_run_side_by_side` |
| 11 | scheduling: tries left, no file | ask one warm build for the identity; rounds wait | `test_a_new_height_digest_gets_its_own_warm_build` |
| 12 | scheduling: tries spent | fallback: with `warm_ids`, every round; else one round warms the map alone (a warm build holds no map) | `test_a_falling_back_height_mode_does_not_trust_another_modes_warm_flag`, `test_after_its_files_are_lost_the_fallback_warms_the_map_alone`, `test_a_warm_build_does_not_hold_its_map_from_another_identitys_warming_round` |
| 13 | a settled run names the tracked file unreadable, and nothing above counted it | delete it here, untrack, `warm_read` off, `_withdraw` | `test_a_fallback_round_finding_the_tracked_file_unreadable_raises_no_d3_alarm` |
| 14 | this cycle's probe read its leased file | `warm_read` on, confirm, clear the three counts | `test_a_probe_that_reads_the_file_clears_the_identitys_earlier_trouble` |
| 15 | an older cycle's round read the leased file | `warm_read` on only | `test_a_late_success_from_before_a_withdrawal_does_not_reopen_parallel_rounds` |
| 16 | this cycle's round built its own cache, couldn't read the leased file, or read another file | `warm_missed` + 1, untrack, `warm_read` off, `_withdraw`; an unreadable file is deleted here | `test_a_probe_that_built_the_cache_holds_the_rest_and_warms_again`, `test_a_leased_file_no_run_can_read_is_removed_and_built_again`, `test_a_probe_that_read_another_file_does_not_confirm_the_leased_one` |
| 17 | the same, from an older cycle | nothing (an unreadable file is still deleted, row 13) | `test_a_late_built_answer_from_an_older_cycle_spends_no_try` |
| 18 | `cache_input` (couldn't download the leased file), this cycle | `warm_missed` + 1, `_withdraw`, requeue at the front | `test_a_round_that_cannot_download_its_leased_file_is_handed_back_and_counted_as_a_miss` |
| 19 | `cache_input`, an older cycle | requeue only | `test_a_confirmed_batch_failing_its_downloads_together_counts_one_miss` |
| 20 | `rewarm` (lease refused), this cycle, file really gone | `_note_gone`, requeue | `test_a_round_whose_lease_fails_is_requeued_uncounted_and_runs_after_a_new_warm_build` |
| 21 | `rewarm`, an older cycle or the file on the disk | requeue only | `test_a_failed_lease_while_the_file_is_on_the_disk_counts_nothing` |
| 22 | a leased round failed for the machine (no visibility source) | probe slot freed; nothing counted; `warm` off | `test_a_round_that_fails_for_the_machine_leaves_its_identity_confirmed` |
| 23 | a fallback round read the tracked file | `warm_read` on | `test_a_fallback_round_that_reads_the_tracked_file_marks_it_read` |
| 24 | a fallback round succeeded with a visibility file on the disk | `warm_ids` on, `warm` on | `test_a_falling_back_height_mode_does_not_trust_another_modes_warm_flag` |

Every requeue frees the probe slot when the job held it, and clears `released_name`; a released round's cycle is set
again whenever it is released.

## The site

A round's running backstop comes from the worker's admission answer (`runner`: `workflow` or `local`), never from a
`/health` read (`_admitted_stale_s`; `test_a_rounds_backstop_comes_from_the_workers_admission_answer_whatever_health_says`).
The D3 alarm compares `vanished` counts within one worker `boot` and keeps earlier boots' counts for the errors page.
