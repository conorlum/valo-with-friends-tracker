# Implementation plan: Ascent door reopening and `control_unsupported`

Design: `docs/superpowers/specs/2026-11-09-control-unsupported-reopen-design.md` (revised after P2). Paths are
relative to `webapp/`. Every test command runs from `webapp/` with `-n 0`. Item numbers are the AFK run's (W4-W7).
The plan was written and built on a base at CONDENSE 25, so it says 26 throughout; ported onto main (#195 and
#196 took 26 and 27), the bump is 27 -> 28.

Pin parts (the run's re-pack of the pin tests, each run with `-m "slow or not slow" -n 0`): A, B
(`test_heights_remote.py`, by id), C (format/condense/contract/feature artifacts/Ascent integration/pinned
inputs), D (critutil, control features/figures/reference/store, replay errors/upload/reparse/util/worker), E
(control cases, crouch, isolation, test catalog).

The judged-case runner (`scripts/control_cases.py`) reads the live site's public responses, which this build may
not contact; its offline shape test is in pin part E. W5's "no-reopen round unchanged" tests and W7's probe stand
in for it (the one Ascent case is not on a reopened round).

## W4: shared door-evidence helper and the Ascent decoder (CONDENSE 26)

Files:
- `app/replays/map_feature_events.py:136-153` `_door_parameter_evidence`: move verbatim to a new leaf module
  `app/replays/map_feature_door_evidence.py` (named so the existing `map_feature_*` guard in `tests/suites.toml`
  covers it: `tests/test_test_catalog.py` fails on an unguarded module; it may import nothing from `map_feature_events`; take the WindowShield class
  path as a constant there, equal to `ascent_features.DOOR_CLASS` and `map_feature_events.CLASSES['WindowShield']`).
  Re-export it in `map_feature_events` under the old name; Sunset's call at `:192` is unchanged. Docstring: an
  edit needs a Sunset consumer bump and a `CONDENSE_REVISION` bump. `DOOR_STATE_PROPERTY` (`:28`) stays where
  it is (the helper doesn't use it).
- `app/replays/ascent_features.py:135-195` `read_capture`: two passes over the parsed rows (or collect then
  resolve): first gather the door actor's `has_rep_layout` rows that serialize `DoorState`, as
  `{(actor, class_path, time_ms): [{'params': {'DoorState': (v, bits)}, 'line': n}]}` (decode as
  `map_feature_events.py:80-87` does); then for each `PlayDoorSounds` call: `New State` 1 -> `opening`, 2 ->
  `closing`, 3 -> `closed`; otherwise run the helper with the row in its shape
  (`{'actor','class_path','time_ms','rpc','params'}`) and give `open` only when it returns witnesses, keeping the
  witness lines on the signal (`'evidence': [lines]`); else `unknown`. The door's `PlayDoorSounds` arguments are
  decoded with unknown handles kept: a new keyword on `invocations` (`:95`), e.g.
  `keep_unknown_rpcs={'PlayDoorSounds'}`, that passes `preserve_unknown=True` to `fields` for those RPCs
  (`opaque_rpcs` can't do it: `:124` sets `opaque` only for an RPC with no layout, and the door's has one); NOT
  `strict=True` for the whole map. Sunset's calls of `invocations` are unchanged. The digest, row
  count, size limits and error handling are unchanged. Update the module docstring (`:1-5`).
- `app/replays/ascent_features.py` `attach` (`:198-223`): the event dicts carry the evidence lines when present
  (only on derived `open` events; other events keep today's keys exactly).
- `app/replays/format.py:119` `CONDENSE_REVISION = 26`.

Tests (red first):
- `tests/replays/test_ascent_features.py`: follow `:21-54`. New fixture in the real rows' shape: a three-field
  `PlayDoorSounds` descriptor (`New State`, `Old State`, `isMoveTo` with 3/3/1-bit values), a rep-layout group for
  the door class with `DoorState` at handle 17, an unknown `#2` property row at the same time, and the property
  row after the call in file order (GATE: call line 2161, property line 2164). Cases: explicit opening; omitted
  `New State` + same-time `DoorState`=0 -> `open` with the witness line; omitted without the row -> `unknown`;
  with `DoorState`=0 at another `time_ms` -> `unknown`; with `DoorState`=1 at the same time -> `unknown`; an extra
  unknown argument handle -> `unknown`; `New State`=255 -> `unknown`. Rewrite
  `test_opening_unknown_values_and_missing_parameter_are_not_guessed` (`:51-54`): 1 is now `opening`. Red
  first are the opening and open cases; the others already give `unknown` on the base and are guards, so the
  extra-handle and `DoorState`=1 cases are built on the otherwise qualifying shape (omitted `New State`,
  Old=1/3 bits, isMoveTo=1, same-time `DoorState`=0 row) to test anything.
- A test that a capture with no reopen gives byte-identical signals and `attach` output to today (build it from
  the existing fixture; compare to a literal expected list).
- Pins re-recorded for 26: `tests/replays/test_control_figures.py:96`, `tests/replays/test_replay_crouch.py:132`,
  `tests/replays/test_replay_format.py:63-64`, `tests/replays/test_replay_util.py:103`.
- Sunset unchanged: `tests/replays/test_map_feature_events.py` and `tests/replays/test_map_pool_runtime.py` pass
  untouched.

Done-check:
- `python -m pytest -n 0 tests/replays/test_ascent_features.py tests/replays/test_map_feature_events.py tests/replays/test_map_pool_runtime.py tests/replays/test_ascent_feature_integration.py tests/replays/test_feature_review_regressions.py`
- Pin parts A, B, C, D, E (`-m "slow or not slow" -n 0`), one per call.
- Probe (read-only, from `webapp/`): `<python> <run>/probes/probe_signals.py`. Unit: decoder signal rows from
  `read_capture` over each export's `map-audit.ndjson`. On the base (`probe_signals_base.txt`): 635 has 124
  signals, `('PlayDoorSounds','unknown')` 17; 644 has 123, unknown 15. Expected after W4: the same totals (124,
  123: no signal appears or disappears); 635 line 2155 `opening` and 2161 `open` (witness line 2164); 644 line 809
  `opening` and 815 `open` (witness 818); every other non-`unknown` signal identical to the base output; status
  `decoded`, errors 0. The remaining unknowns and new opening/open signals are listed (W7 checks they fall
  outside every round's window).

## W5: the Ascent runtime and the viewer read opening (consumer 4 -> 5)

Files:
- `app/replays/ascent_features.py:226-245` `state_at`: `opening` keeps `began` and moves closure 1 -> 0 over
  `closing_s` (the same duration rules and the +0.25 s tolerance as closing); `modeled` is True for opening too;
  `pending` when opening has no usable closure. A reversal (a `closing` event while the door is opening, i.e.
  before the opening's closure reaches 0, or an `opening` event while closing) makes the result pending from that
  event on, until an endpoint (`open`, `closed`, `broken`) event: `state` stays the last event's name (so the
  reducer cross-check at `app/control/feature_runtime.py:221-224` agrees and the pending check after it raises
  `feature endpoint/state unobserved at calculation time`), `closure` is None, `pending` True. Terminal `broken`
  and `unknown` rules unchanged.
- `app/control/feature_runtime.py:22` `readiness`: the door set adds `opening`. `:139-144`: for each `opening`
  event add the release time: the smallest float `x >= t + 5*(1-fraction)` at which
  `map_feature_motion.movement_blocked(f, state_at(timeline, x)['closure'])` is False (step with `math.nextafter`,
  at most 64 steps; if the bound is hit, or `movement_blocked` returns None, add no time: the `open` event's
  own transition then releases). Only on the cutoff path (`movement_cutoff(f)` not None). `:244` `cache_resting` is False for `opening` too. `:151` message becomes
  `unqualified door-state parameter in active feature timeline`.
- `app/replays/map_feature_inputs.py:16` `CONSUMER_VERSIONS['ascent_replay_v1']` 4 -> 5.
- `app/static/js/replay.js:3948-3958` (`stateAt` for map features): the same opening model and reversal rule;
  `:4008` emphasis treats opening as moving like closing.

Tests (red first):
- Fixtures first: the shared `runtime_case` fixture (`tests/replays/test_ascent_features.py:120-121`) and any
  door definition in `tests/replays/test_ascent_feature_integration.py` gain an `opening` state shaped like the
  committed tags (5 s, `mid_motion: reverse`), or every runtime test fails once `readiness` requires it; a test
  that a door definition without `opening` is refused by `readiness`. The test matching `'unverified'`
  (`test_ascent_features.py:159`) matches the new message.
- `tests/replays/test_ascent_features.py`: extend the Python/JS parity test (`:97-105`) with opening and
  reversal times.
- `tests/replays/test_ascent_features.py`: `state_at` for closed -> opening -> open (closure 1, 0.5 at +2.5 s, 0
  at +5 s, `open` after); opening with no `closing_s` is pending; a closing during an opening and an opening
  during a closing are pending until the next endpoint; a timeline without opening gives the same results as
  before (literal values).
- `tests/replays/test_ascent_feature_integration.py`: follow its existing door tests. With the file's toy
  compiled artifact (`compiled_case`; no Ascent artifact is committed): a Market timeline closing 21.743, closed 26.748, opening 59.475, open 64.475 builds
  without raising; `reopened_by` gives the doorway nodes a release in (59.475 + 5*(1-cutoff), +1e-6]; the
  occluder has `cache_resting` False at 61.0 and True at 65.0; a timeline without a reopen gives the same
  `transitions` as before (literal list).
- `tests/replays/test_replay_viewer.py` near `:126`: the JS `stateAt` gives closure 0.5 at opening + 2.5 s and a
  pending state for a reversal.
- Re-record any pin that hashes the consumer versions or protocol identity (expected in pin part C/D: the
  reviewer found none pinning the value 4); a re-record says why in the test's comment.

Done-check:
- `python -m pytest -n 0 tests/replays/test_ascent_features.py tests/replays/test_ascent_feature_integration.py tests/replays/test_replay_viewer.py tests/replays/test_control_viewer.py tests/replays/test_vertical_feature_motion.py tests/replays/test_feature_visibility_rows.py tests/replays/test_feature_review_regressions.py tests/replays/test_feature_transport.py tests/replays/test_freshness_memos.py tests/replays/test_cover_identity.py tests/replays/test_cover_pieces.py tests/replays/test_panel_feature_grid.py tests/replays/test_rope_walking_ground.py tests/replays/test_feature_acceptance.py tests/replays/test_control_feature_verification.py tests/replays/test_map_pool_runtime.py tests/replays/test_control_remote.py --require-node`
- Pin parts C, D, E.
- Real-data check: W7.

**Checkpoint cp1** (full default suite, by the loop) after W5.

## W6: messages, the Replays step and the site-health kind

Files:
- `app/services/replay_control_remote.py:540`: the stored error is `f"{error_code}: {message}"` with no suffix;
  `:527-531` fallbacks drop "; reparse this recording".
- The local path stores the same text: `app/services/replay_control_store.py` `store_round` (`:75` area) prefixes
  an `inputs` result's `error` with its `error_code` only when `result.get('error_code')` is present and the
  text doesn't already start with it (the remote row passed in at `remote.py:540-541` carries no `error_code` and
  is already prefixed; the local `feature_failure` dict does carry it, `map_feature_artifacts.py:69`), so
  `scripts/compute_control.py:158` rows match the remote ones. Never a default code.
- `app/services/replay_progress.py:29-45` STEPS: `("control_unsupported", "Map control: not supported in this
  round", False)` before `control_failed`. `round_states` (`:84-98`): a failed control row whose stored error
  starts with `features_source_unavailable` -> `control_unsupported` (one extra query of failed rows' `error`, or
  read it in the existing control query). `compute` (`:121-129`): a third total `unsupported`; `needs_a_person`
  leaves `control_unsupported` out.
  The step order follows R4 (before `control_failed`; settled). Within one round the two can't both apply, so
  the order only picks a match's label when it has both; the match list shows that the match needs a person
  through the rule below, not through the label alone.
- A set `UNSUPPORTED_STEPS = {'control_unsupported'}` in `replay_progress` marks steps nobody acts on: the
  per-step "A person" text at `app/templates/replays/upload.html:104` reads "Nobody: not supported" for them,
  and `match_states` (`replay_progress.py:192-194`) gives the match list (`templates/replays/_up_to_date_cell.html:5`)
  a "not supported" wording instead of "needs a person" when the earliest stuck step is unsupported and no
  round of the match needs a person. A test of each wording.
- `app/templates/replays/upload.html:95-96`: print the unsupported count when it is non-zero (plain wording, e.g.
  "N not supported in their round").
- `app/services/replay_errors.py`: `KINDS` (`:33`) adds `("control_unsupported", "Map control: not supported in
  this round")` beside `control_failed` and the kind goes into `GROUPS` with it; the loop at `:431` adds
  `("control_unsupported", ReplayRoundControl, "control_revision")`; the needs-a-person exclusion at `:507` adds
  it; `models` at `:691` adds it (reproduce SQL).
- `app/services/site_health.py:720` `KNOWN_KINDS` adds `control_unsupported` (not `RECORDED_KINDS`,
  `EVENT_KINDS` or `replay_errors.LIVE_KINDS`).

Tests (red first):
- `tests/replays/test_control_remote.py:552-562`: the stored error equals `features_source_unavailable: reset
  unobserved` (no "reparse"); an engine failure is unchanged.
- `tests/replays/test_control_store.py`: a local `inputs` result is stored with the prefix; an already-prefixed
  one is not prefixed twice.
- `tests/replays/test_replay_progress.py` (follow `:48-51`): a `features_source_unavailable` failed row is
  `control_unsupported`, another failed row is `control_failed`; `needs_a_person` leaves the unsupported one
  out and `unsupported` counts it.
- `tests/replays/test_replay_errors.py`: `:36-38` pinned kind list and `:125` (`KINDS == KNOWN_KINDS`, the
  completeness test) updated; the unsupported row is listed under `control_unsupported`, not `control_failed`,
  not as stale and not in the needs-a-person list. The frozen reference copy (`:768-822`) compares whole dicts
  and fails once `compute` gains `unsupported`: add the key (0) to the reference's expected output, and say so
  in a comment; nothing else in it changes.
- `tests/test_site_health.py:579-580` stays green.

Done-check:
- Codex (offline): `python -m pytest -n 0 tests/replays/test_control_remote.py tests/replays/test_control_store.py tests/replays/test_replay_progress.py tests/replays/test_replay_errors.py tests/replays/test_replay_match_states.py tests/replays/test_map_feature_sources_cache.py tests/test_site_health.py tests/coach/test_brief.py tests/coach/test_coach_panel.py tests/coach/test_coach_store.py tests/coach/test_prompt.py tests/replays/test_control_feature_verification.py tests/replays/test_feature_transport.py tests/replays/test_replay_errors_web.py`
- PostgreSQL lane (afk-implementer, `VALO_TEST_DATABASE_URL` from the run's Check env):
  `python -m pytest -n 0 -m "postgres and not corpus and not browser and not slow" --require-postgres tests/replays/test_control_remote.py tests/replays/test_control_store.py tests/replays/test_control_views.py tests/replays/test_replay_archive_web.py tests/replays/test_replay_match_states.py tests/replays/test_replay_reparse_auto.py tests/replays/test_replay_routes.py tests/replays/test_replay_store.py tests/replays/test_replay_upload.py tests/replays/test_replay_upload_held.py tests/test_site_health_db.py`
  (the exact file list is re-taken from `test_catalog.py plan` on W6's changed files).
- Pin parts D, E.

## W7: real-data probe (read-only)

`<run>/probes/probe_w7.py`, run with the main checkout's `.venv313` under
`scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only` (it reads the stored blobs of the two
replays). `with_friends_db.py` runs in the main checkout's `webapp/`, so the probe puts the worktree's `webapp/`
first on `sys.path` and prints `ascent_features.__file__` to prove which decoder ran. It does not re-derive round
windows (that needs `read_game_state` over each ~3.2 GB `events.ndjson`): every stored event carries its source
`line`, and the decoder emits exactly one signal per source line it reads (W4's probe: totals unchanged), so the
probe maps each stored round's events to the branch's `read_capture` signals by line and compares state names.
Cost: `read_capture` on a ~1.1 MB `map-audit.ndjson` takes about 1 s (W4 probe), so ~1 s × 2 exports × 1 run,
plus two blob reads; no timing limit.

Expected (unit: rounds; command: the probe; source: the stored blobs and the two exports):
- 635 round 18 Market: closing 21.743, closed 26.748, opening 59.475, open 64.475.
- 644 round 8 Garden: closing 23.585, closed 28.586, opening 53.253, open 58.253.
- Every other stored event of both replays (all rounds: 21 + 23 = 44 other rounds) maps to a branch signal with
  the same state name; the only changed lines are 635: 2155, 2161 and 644: 809, 815. A stored event whose line has
  no signal, or a change at any other line, is a FAIL (a relabel after a terminal `broken` included: W4's listing
  shows whether any such line sits inside a stored round; if one does, it is reported and judged, not passed).
- The two new timelines (the stored round's events with the branch's states) build in `FeatureRuntime` on the
  toy compiled artifact `tests/replays/test_ascent_feature_integration.py` uses (`compiled_case`; no Ascent artifact
  is committed, and the real geometry is only in the friends DB) without raising, and `reopened_by` releases the
  doorway nodes within 1e-6 s after `opening + 5*(1-cutoff)`.
- Full control for the two rounds is not computed locally: it needs the friends DB's geometry assets and a local
  write path (`scripts/compute_control.py` stores rows); the runtime check above covers the failing step.
- Nothing is written anywhere.

## After W7

- Slow lane checkpoint: `python scripts/test_catalog.py plan --slow <the run's changed paths>`, re-collected with
  `-m slow`, packed by `split_suite.py`, requested with `--marker slow`.
- Docs: none beyond the design. `CLAUDE.md` names no replay kinds or progress steps individually, and
  `RENDER_DEPLOY.md` has no step for this rollout (no switch, R7). New test files (none planned) would go into
  `tests/suites.toml`.
- Final checkpoint.
