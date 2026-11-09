# Ascent door reopening, and control failures a re-parse can't fix

Status: approved design, 2026-11-09; revised after the P2 review (AFK run 2026-11-09-ascent-door-reopen, findings
F1-F14). Nothing implemented yet.

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
  `mid_motion: reverse`): Market (feature-9) and Garden (feature-11), the only doors in the `ascent_replay_v1`
  bundles. Only the Ascent decoder, the Ascent runtime and the viewer's copy of `state_at` lack it.
- `replay_control_remote.py:540` adds "; reparse this recording" to every `inputs` failure, which is why the
  advice appears where it is wrong.
- Corpus: the pattern occurs in 2 of 200 stored Ascent rounds, both on doors that weren't broken first. No round
  has an opening cut short by a closing, or the reverse.

**No other failure asks for a re-parse.** 1,804 control rows are ok and 21 failed: these 2, and all 19 rounds of
replay 706 (Summit), `ControlError: slot 0 has no side group and the link names no side`. That is a separate
linking bug, out of scope here (its own issue).

## Design

### 0. Gate: confirm the raw parameters

**PASSED in scoping, 2026-11-09**: both rows have exactly the shape below, and the captures' sha256 equal the
stored blobs'. In both captures the `PlayDoorSounds` call's parameters are `New State`, `Old State`, `isMoveTo`
(3-bit, 3-bit, 1-bit), and the confirming `DoorState` row (rep-layout handle 17) comes **after** the call in file
order, at the same `time_ms`, beside an unknown `#2` row.

### 1. Decoder (`app/replays/ascent_features.py`)

- `PlayDoorSounds` with `New State`=1 gives `opening`.
- `New State` left out, Old State=opening, isMoveTo=1, and a same-actor same-`time_ms` `DoorState`=0 property
  row gives `open`. The check is `_door_parameter_evidence`, moved verbatim to a leaf helper that both decoders
  import (in `ascent_features` or a new module; never in `map_feature_events`, which imports `ascent_features`),
  and re-exported under its old name so Sunset's call is untouched. Its docstring says an edit to it needs both a
  Sunset consumer bump and a `CONDENSE_REVISION` bump.
- The `PlayDoorSounds` arguments are decoded with unknown handles kept (a door-only setting, not `strict=True` for
  the whole map: a missing glass layout would make every Ascent round partial), so an extra unknown argument
  stays `unknown`, as in Sunset.
- `read_capture` collects the `DoorState` property rows first, keyed `(actor, class_path, time_ms)` in the shape
  the helper expects, then resolves the calls (the property row follows its call in the file). A property row
  never becomes a state change on its own.
- The derived `open` event keeps its witness (the property line) as Sunset's `parameter_evidence` does.
- Anything else stays `unknown`.
- `CONDENSE_REVISION` goes from 25 to 26. Pinned tests are bumped with it.

### 2. Ascent runtime (`app/control/feature_runtime.py`, `ascent_features.state_at`, the viewer)

- `readiness` requires `opening` in a door's states. Glass is checked separately and is unaffected.
- `state_at` models opening as closure going from 1 to 0 over the opening duration (5 s), mirroring closing. An
  opening event with no usable duration is pending, which fails the round as today.
- **A reversal fails as pending.** An opening that starts while the door is still closing, or a closing that
  starts while it is still opening, is pending (the round fails, as an unread state does today). The corpus has
  none; modelling a partial door is left until one appears.
- The movement cutoff for `opening` is where closure falls to the door's cutoff fraction:
  `t + duration * (1 - fraction)`, taken through the same closure function `state_at` uses and stepped to the
  first float where the door no longer blocks, so `reopened_by` releases the doorway at that moment (at the raw
  sum, closure computes as 0.30000000000000004 and the release would slip to the `open` event).
- `cache_resting` is False while the door is opening, as while closing.
- Line 151's message becomes "unqualified door-state parameter in active feature timeline": the case left after
  this fix.
- `CONSUMER_VERSIONS['ascent_replay_v1']` goes from 4 to 5 with this change, as Sunset's did with its CONDENSE
  bump: a worker without the new runtime is held by the protocol check instead of failing these rounds as
  infrastructure errors. Ascent shows `features_pending` until its artifact recompiles (automatic).
- The viewer's `state_at` (`app/static/js/replay.js`, near :3948) gets the same opening model, since Ascent's
  viewer reads the blob.
- A round without a reopen produces the same door timeline, the same transitions and the same control as today.

### 3. Messages (`app/services/replay_control_remote.py:540`, `app/control/task.py`)

Stop adding "; reparse this recording" to every `inputs` failure. The stored error is `<error_code>: <message>` on
both paths: the remote path already writes it, and the local command (`task.py` -> `feature_failure` ->
`compute_control.py` -> `store_round`) stores the same text, so the classifier below sees the same prefix
wherever the round was computed. The gaps-only fallbacks at `remote.py:527-531` drop the suffix too. The raised
messages that a re-parse can fix (`feature_runtime.py:117`, `map_pool_runtime.py:168`) keep saying so; they also
carry `features_source_unavailable` and are filed as not supported, which is accepted: on a current recipe they
fire only for partial captures, which a re-parse doesn't fix either.

### 4. Replays page (`app/services/replay_progress.py`)

A new step, `("control_unsupported", "Map control: not supported in this round", False)`, placed before
`control_failed`. A failed control row whose error starts with `features_source_unavailable` is filed there
instead of under "needs a person". `round_states` reads the failed rows' error text for it. The totals get a third
count, `unsupported`, which `needs_a_person` leaves out, and the upload page prints it beside the other two.

### 5. Site health

A new replay kind `control_unsupported`, computed from `replay_round_control` rows like `control_failed` (which
now excludes them): in `replay_errors.KINDS` and `GROUPS` and in `site_health.KNOWN_KINDS`; not a `LIVE_KINDS`,
`RECORDED_KINDS` or `EVENT_KINDS` member. `replay_errors` lists its rows itself (not as stale rows the worker will
retry), keeps it out of the needs-a-person list, and gives it reproduce SQL. It clears itself when the round
computes and needs no resolved-file entry.

### 6. Rollout: global bump, auto queue unchanged

Only Ascent blobs change, but the recipe is one site-wide string compared by equality (store, upload acceptance,
auto queue, progress, heights) and the control fingerprint includes it. The bump therefore marks every replay out
of date. The auto queue re-parses them one at a time on Workflows (PR #194), and map control, gaps and Critical
Util are recomputed for every round, as with condense 25. About 89% of that recomputation is unchanged output;
the owner chose this over a per-map recipe revision (2026-11-09). The Ascent consumer bump (section 2) adds one
artifact compile: Ascent rounds wait in `features_pending` until it is done, then compute.

No "re-parse requested by a control failure" reason and no new attempt cap: every `features_source_unavailable`
failure depends only on the recording and the code, so the same recipe can never fix it.

## Testing

- Decoder unit tests, synthetic rows in the real rows' shape (three-field `PlayDoorSounds` layout, 3/3/1-bit
  counts, a rep-layout group with `DoorState` at handle 17 and an unknown `#2` row, the property row after the
  call): explicit opening; `New State` left out with the `DoorState`=0 row (open); left out without the row (stays
  `unknown`); with a `DoorState` row at another timestamp (stays `unknown`); with an extra unknown argument (stays
  `unknown`); `New State`=255 stays `unknown`. Sunset's decoder tests pass unchanged.
- Runtime: closed, opening, open computes; `reopened_by` releases the doorway in (cutoff time, +1e-6] for a real
  start time (59.475); `cache_resting` is False while opening; a reversal in either order is pending; a round
  without a reopen gives the same transitions as before.
- Viewer: the JS `state_at` gives the opening closure.
- `replay_progress` and `replay_errors` classify a `features_source_unavailable` row as `control_unsupported`
  (from the remote and the local path), and any other failure as `control_failed`; `needs_a_person` leaves it out.
- Pinned-revision tests updated for CONDENSE 26; `tests/suites.toml` lists any new test file.
- The slow lane before merge, since control changes.
- After deploy and the re-parse: 635 r18 and 644 r8 are ok, and the Replays page shows 22/22 and 24/24.

## Out of scope

- Replay 706 (Summit), 19 rounds: `slot 0 has no side group and the link names no side`.
- A per-map decoder revision in the recipe.
- Moving Ascent onto the shared ledger (`map_feature_events` / `MapPoolRuntime`).
- Modelling a door reversed mid-motion (it fails as pending until a real case appears).
