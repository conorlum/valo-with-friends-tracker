# Ascent door reopening, and control failures a re-parse can't fix

Status: approved design, 2026-11-09. Nothing implemented yet.

## Problem

Two Ascent uploads each have one failed map-control round; every other round is done:

| Replay | Match | Round | Failed (UTC) | Replays page |
|---|---|---|---|---|
| 635 | 6f12db3e (2026-09-27) | 18 | 2026-11-07 15:45 | 21/22 |
| 644 | a5549826 (2026-09-25) | 8 | 2026-11-07 18:54 | 23/24 |

Both rows have `control_revision` 23, recipe `04dcbd92806c.c25.f1.a9965d90c` (current), and the error
`features_source_unavailable: unverified opening/default state in active feature timeline; reparse this recording`
(`app/control/feature_runtime.py:151`, kind `inputs` via `map_feature_artifacts.feature_failure`).

The automatic re-parse queue only picks replays whose recipe is out of date, so it never acts on the advice.
`replay_progress` files the rounds under `control_failed` ("needs a person").

## Findings (read-only friends DB, 2026-11-08)

**A re-parse cannot help.** The stored `map_features` timelines:

| Round | Door | Events (s from round start) |
|---|---|---|
| 635 r18 | Market | closing 21.743, closed 26.748, **unknown 59.475, unknown 64.475** |
| 644 r8 | Garden | closing 23.585, closed 28.586, **unknown 53.253, unknown 58.253** |

Both doors close, then about 30 s later produce two `PlayDoorSounds` calls exactly 5.000 s apart. That is the
closing-to-closed duration, so the pattern points to a reopening: opening, then open.

- `app/replays/ascent_features.py:178` reads only `New State` 2 (closing) and 3 (closed). Anything else becomes
  `unknown`, including opening and a value left out because it is the default. Its docstring says reopening is not
  supported. The same parser and condenser rebuild the same blob, so these rounds fail the same way on every run.
- Sunset's shared decoder already handles this case: `app/replays/map_feature_events._door_parameter_evidence`
  reads Old State=opening, isMoveTo=1, `New State` left out as `open`, but only with an explicit same-actor,
  same-timestamp `DoorState`=0 property row. Ascent never moved onto that decoder.
- The Ascent door definitions in `app/static/data/control/tags.json` already have an `opening` state (5 s,
  `mid_motion: reverse`). Only the Ascent decoder and the Ascent runtime lack it.
- `replay_control_remote.py:540` adds "; reparse this recording" to every `inputs` failure, which is why the
  advice appears where it is wrong.
- Corpus: the pattern occurs in 2 of 200 stored Ascent rounds, both on doors that weren't broken first.

**No other failure asks for a re-parse.** 1,804 control rows are ok and 21 failed: these 2, and all 19 rounds of
replay 706 (Summit), `ControlError: slot 0 has no side group and the link names no side`. That is a separate
linking bug, out of scope here (its own issue).

The blob keeps no raw RPC parameters, so the reading above is confirmed from the archived recordings before any
code is written (step 0).

## Design

### 0. Gate: confirm the raw parameters

The owner fetches both recordings (`scripts/fetch_archived_replay.py`) and exports them
(`scripts/export_replay.ps1`). A read-only script prints the `map-audit.ndjson` rows at lines 2155 and 2161
(replay 635) and 809 and 815 (replay 644), and every same-actor, same-`time_ms` `has_rep_layout` row with its
decoded `DoorState`.

- Proceed when the first call carries `New State`=1 and the second leaves `New State` out with a same-time
  `DoorState`=0 row.
- Otherwise stop and return to the owner. Reading a left-out value as `open` without that row is an inferred
  default, which the Sunset rule forbids.

### 1. Decoder (`app/replays/ascent_features.py`)

- `PlayDoorSounds` with `New State`=1 gives `opening`.
- `New State` left out, Old State=opening, isMoveTo=1, and a same-actor same-`time_ms` `DoorState`=0 property
  row gives `open`. The check is `_door_parameter_evidence`, moved to a helper both decoders import, so the two
  rules cannot drift.
- Anything else stays `unknown`.
- `read_capture` reads `has_rep_layout` rows for `DoorState` only to confirm that case. A property row never
  becomes a state change on its own.
- `CONDENSE_REVISION` goes from 25 to 26. Pinned tests are bumped with it.

### 2. Ascent runtime (`app/control/feature_runtime.py`, `ascent_features.state_at`)

- `readiness` requires `opening` in a door's states.
- `state_at` models opening as closure going from 1 to 0 over `closing_s` (5 s), mirroring closing. An opening
  event with no usable duration is pending, which fails the round as today.
- The movement-cutoff transition time is added for `opening` events as it is for `closing`, so `reopened_by`
  releases the doorway at the right moment.
- Line 151's message becomes "unqualified door-state parameter in active feature timeline": the case left after
  this fix.
- A round without a reopen produces the same door timeline as today.

### 3. Messages (`app/services/replay_control_remote.py:540`)

Stop adding "; reparse this recording" to every `inputs` failure. The stored error is
`<error_code>: <message>`. The raised messages that a re-parse can fix (`feature_runtime.py:117`,
`map_pool_runtime.py:168`) already say so themselves.

### 4. Replays page (`app/services/replay_progress.py`)

A new step, `("control_unsupported", "Map control: not supported in this round", False)`, placed before
`control_failed`. A failed control row whose error starts with `features_source_unavailable` is filed there
instead of under "needs a person".

### 5. Site health

A new live replay kind `control_unsupported`, in `replay_errors.KINDS` and `GROUPS` and in
`site_health.KNOWN_KINDS`, read from the same `replay_round_control` rows as `control_failed`, which now excludes
them. It is computed live, so it clears itself when the round computes and needs no resolved-file entry.

### 6. Rollout: global bump, auto queue unchanged

Only Ascent blobs change, but the recipe is one site-wide string compared by equality (store, upload acceptance,
auto queue, progress, heights) and the control fingerprint includes it. The bump therefore marks every replay out
of date. The auto queue re-parses them one at a time on Workflows (PR #194), and map control, gaps and Critical
Util are recomputed for every round, as with condense 25. About 89% of that recomputation is unchanged output;
the owner chose this over a per-map recipe revision (2026-11-09).

No "re-parse requested by a control failure" reason and no new attempt cap: every `features_source_unavailable`
failure depends only on the recording and the code, so the same recipe can never fix it.

## Testing

- Decoder unit tests, synthetic rows: explicit opening; `New State` left out with the `DoorState`=0 row (open);
  left out without the row (stays `unknown`); left out with a `DoorState` row at another timestamp (stays
  `unknown`).
- Runtime: closed, opening, open computes; `reopened_by` releases the doorway nodes at the cutoff; a round
  without a reopen gives the same transitions as before.
- `replay_progress` and `replay_errors` classify a `features_source_unavailable` row as `control_unsupported`,
  and any other failure as `control_failed`.
- Pinned-revision tests updated for CONDENSE 26; `tests/suites.toml` lists any new test file.
- The slow lane before merge, since control changes.
- After deploy and the re-parse: 635 r18 and 644 r8 are ok, and the Replays page shows 22/22 and 24/24.

## Out of scope

- Replay 706 (Summit), 19 rounds: `slot 0 has no side group and the link names no side`.
- A per-map decoder revision in the recipe.
- Moving Ascent onto the shared ledger (`map_feature_events` / `MapPoolRuntime`).
