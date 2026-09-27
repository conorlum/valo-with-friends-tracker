# Replay viewer, pass 6: implementation plan

Revised after an independent review (18 findings: 1 blocker, 12 should-fix, 5 nits; all applied).

The file-level steps for the pass-6 prerequisites (P-a to P-h) and the pre-1b work (W-a to W-e, the gate
harness) in `docs/replay-viewer-plan.md` (pass 6, `a43f5fb`). That plan is the design. This file only orders the
work, names the files, and gives each step a runnable yes/no check. Where the design leaves a value open, the
step adds a `PROVISIONAL(Dn)` constant; the 1b gate freezes them.

All paths are under `webapp/` unless they start with `replay_worker/` or `docs/`. Every test command runs from
`webapp/` with `.venv313` (`PY` below); the final step also runs both full suites.

```
PY = <repo>\webapp\.venv313\Scripts\python.exe
EXPORT = %TEMP%\valo-replay\d45b2844-d7dd-4efd-bbf7-551854710350
VRF = %USERPROFILE%\ValorantReplayArchive\d45b2844-d7dd-4efd-bbf7-551854710350.vrf
```

## Facts measured before planning (the real Swiftplay export, streamed; counts only)

- **File order:** `events.ndjson` and `movement.ndjson` are already sorted by `time_ms` (0 inversions in
  142,091 event rows and 887,924 movement rows). W-b can stream them.
- **Close reasons:** 1,021 `actor_closed` rows, every one `destroyed`. The parser's enum is `Destroyed`,
  `Dormancy`, then raw numbers up to 15 (`ChannelCloseReason.cs`), written snake-cased. No dormant close in this
  export.
- **Phases:** 37 `ClientGamePhaseBegin` and 37 `ClientGamePhaseEnded`, all on channel 1. Each `Ended.OldPhase`
  equals the previous `Begin.NewPhase` at the same `time_ms`. The first `Ended` (`OldPhase 1`) precedes the first
  `Begin`, and the last `Begin` (5) has no `Ended`. The cycle is `2 → 3 → 4 → 5 → 2`, with one `6` after round 4.
- **Pawns:** no GUID is spawned twice. 11 character GUIDs carry a `PlayerState` (10 player pawns plus a Clove
  post-death pawn naming Clove's player state); no pawn names two player states.
- **Utility:** `valorant_flash_cast` 4, `valorant_flash_player_hit` 8, `valorant_nearsight_cast` 10,
  `valorant_nearsight_player_hit` 6 (plus path and effect rows). Casts carry `caster_character_net_guid` and
  `caster_player_state_net_guid`; hits carry `target_character_net_guid`/`target_player_state_net_guid`.
- **Header:** upstream `ReplayInfoReader.cs:47-95`: magic `0x43F4EFDD`, file version 7, a custom-version
  container (count, then GUID + int32) holding only LocalFileReplay `95A4F03E-7E0B-49E4-BA43-D35694FF87D9` at
  version 7, then `LengthInMs` int32, `NetworkVersion` uint32, `Changelist` uint32, then the `FriendlyName`
  FString (int32 length; negative means UTF-16 of `-length` code units; bound 64 KiB; trailing NULs trimmed).

## Order

```
S0 baselines ─ S1 plan ─ S2 review ─┬─ S3 P-a ─┐
                                    ├─ S4 P-b ─┤
                                    ├─ S5 P-c ─┼─ S7 P-e/P-f/P-g ─┐
                                    ├─ S6 P-d ─┘                  │
                                    ├─ S8 P-h ────────────────────┼─ S9 1a close ─┬─ S11 W-b ─┬─ S13 W-c (-worker)
                                    └─ S10 W-a ───────────────────┘               │           └─ S12 gate harness
                                                                                  ├─ S14 W-d (-viewer)
                                                                                  └─ S15 W-e (-util)
                                                                   S16 final (all branches)
```

S3-S6, S8 and S10 are independent; they run in that order on the main line. S12 needs S7 and S9, and it uses
S11's streaming loader to run condense-level corruptions on the 1.5 GB export in reasonable time, so it follows
S11. Side branches (S13-S15) are cut from the main line's tip after S9 (S13 after S11) and never merge into each
other.

## Steps

### S3. P-a: phase-cycle validation (W3)

Implements "Exporter contract", Rounds, and JSON v1 `t_decided`/`t_end`.

- `app/replays/contract.py`: `RPC_PHASE_ENDED = "ClientGamePhaseEnded"`.
- `app/replays/condense.py`:
  - `read_game_state` validates the collapsed phase sequence: refuse (`ContractError("phase_cycle", …)`) on a
    `5` with no open `4`, a `4` meeting any phase other than `5` while a later `4` exists, or a second `4` while
    one is open. The **final open `4`** is one with no later `4`: phases other than `5` may follow it (a
    surrender's match-end phase), and it is dropped and reported (`PROVISIONAL(D15)`, finding 26). `4 → 3 → 5`
    still refuses anywhere, as an orphan `5`.
  - Each window becomes `(t_in_round, t_decided, t_end)`: `t_end` is the time of the next phase after the `5`.
    For the last window, with no later phase, `t_end` is the export's last event time
    (`PROVISIONAL(D15)`: the recording's end).
  - The `ClientGamePhaseEnded` cross-check, when the export has any `Ended` RPC, over the **raw**
    `ClientGamePhaseBegin` rows (not the collapsed list): the `k`-th `Ended` pairs with the `k`-th `Begin` and
    its time must be within `PHASE_ENDED_TOLERANCE_MS` (`PROVISIONAL(D15)`, 0 ms: the export shows equal
    times); for `k ≥ 1` its `OldPhase` must equal `Begin[k-1].NewPhase`. `Ended[0].OldPhase` is unchecked (the
    phase before the recording began). The counts must be equal, or the last `Begin` may lack its `Ended` (the
    phase still running at the end). Anything else refuses. The report records `phase_ended_checked`.
  - A decoded `RoundNumber` at a window's start must equal `n - 1 + ROUND_NUMBER_BASE`
    (`PROVISIONAL(D15)`, base 0, as D8 assumes for `RoundResults`), else refuse.
  - Kills: each kill is assigned to the one playback window `[t_in_round, t_end]` that holds it. A kill in the
    dropped final round gets the exclusion reason `dropped_final_round`. Any other kill is outside: the report
    counts `kills_outside_rounds`, and a count above 0 refuses.
  - Blobs gain `t_decided` (seconds from `InRound`); `t_end` is the playback end. Alive intervals and tracks run
    to `t_end`.
- `app/replays/format.py`: `CONDENSE_REVISION = 2` (docstring shows `t_decided`).
- `tests/replays/replay_synthetic.py`: `movement()` runs to each round's `t_end` (the next phase), so coverage
  stays whole; the Swiftplay shape also emits `ClientGamePhaseEnded` rows like the real export.
- Existing tests whose expectations change on purpose (each edited, none loosened): `test_round_blob_shape`
  (`t_end`, the new `t_decided` key), `test_a_mid_match_round_without_round_ending_refuses`
  (`round_without_end` → `phase_cycle`), and the preview's check count.
- Tests, `tests/replays/test_replay_phases.py` (synthetic, both shapes where relevant): index base; a missing
  final `RoundEnding`; `4 → 3 → 5` refuses; an orphan `5` refuses; a lost `4` refuses and doesn't renumber; a
  second `4` refuses; a decoded `RoundNumber` that disagrees refuses; a kill outside every window refuses; a
  kill after `t_decided` is kept (in `kills`, `t > t_decided`); a final `4` followed by a match-end phase is
  dropped; `t_decided` round-trips through `format.encode_blob`/`decode_blob`; `Ended` mismatches (wrong `OldPhase`, missing
  `Ended` in the middle, time skew) refuse; no `Ended` at all → `phase_ended_checked` false; halftime (a `6`)
  and overtime keep numbering.
- **Check:** `PY -m pytest -q tests/replays -p no:cacheprovider` passes, and
  `PY -m pytest -q tests/replays/test_replay_phases.py` collects at least 14 tests.

### S4. P-b: pawn ownership (W4)

Implements "Exporter contract", Players (P-b).

- `condense.build_players`: keep every non-null claim with its time: a character's `PlayerState`, a player
  state's `SpawnedCharacter` and `PossessedCharacter`, and each player state's `Subject`. Refuse
  (`ContractError("ownership", …)`) on a pawn whose `PlayerState` changes to another non-null value, a pawn
  claimed by two player states (by any mix of the two sources), or a `Subject` that changes to another non-null
  value.
- `PossessedCharacter` becomes intervals per player state: from each update until that state's next
  `PossessedCharacter` update (or the match end). `PlayerTable.resolve(guid, t_ms)` resolves a possessed pawn
  only inside its interval; the player's own character pawns resolve at any time. Kills and revives call it
  with their time.
- The report gains `ownership: {"claims": n, "pawns": n, "possession_intervals": n}`.
- Tests, `tests/replays/test_replay_ownership.py`: a pawn claimed by two player states refuses (both source
  mixes); a pawn whose `PlayerState` changes refuses; a Subject that changes refuses; a possessed pawn resolves a
  kill inside its interval and refuses as unresolved outside it; repeated identical claims are fine.
- **Check:** `PY -m pytest -q tests/replays -p no:cacheprovider` passes, with the new file's tests included.

### S5. P-c: channel lifecycle (W5)

Implements "Exporter contract", Lifecycle, and JSON v1 `alive` flags.

- `condense.py`, a `read_lifecycle(export, players)` pass over each player pawn's `actor_spawned` and
  `actor_closed` rows (with `reason`):
  - a close followed by a reopen of the same GUID (a later `actor_spawned`), or a `dormancy` close, is an
    **unobserved** span `[close, reopen or match end]`;
  - a player **left** only when their last pawn closes for a reason other than `dormancy`
    (`LEFT_CLOSE_REASONS`: `PROVISIONAL(D15)`, any non-`dormancy` reason) with no reopen and no later pawn,
    **before the last round's `t_decided`**; a close after it is the match ending, not a leaver;
  - an `actor_closed` row with no `reason` refuses (the export always writes one);
  - the close of a pawn that a later pawn replaces is a pawn change (as now).
- Contradictions refuse (`ContractError("lifecycle", …)`) and are counted in the report: a kill by or of a
  player at or after their `left` time; a second death **within one round window** with no revive or new pawn
  between (a round's start opens a new life), unless it is a **self-kill** with no decoded revive (the Swiftplay
  Clove shape), which marks that life `uncertain` instead.
  "A reopen after a left" can't occur under the definition (a reopen makes the span unobserved); a test pins
  that.
- `alive_intervals` returns `[from, to, cause]` plus an optional fourth element, the flags list
  (`["unobserved"]`, `["uncertain"]`), only when non-empty. An uncertain life ends at its first death.
  `build_segments`, `_interval_index` and the coverage loop read the first three elements only.
- Coverage and gap stats measure observed alive time only: unobserved spans are excluded from the alive time
  and from gap measurement.
- The report gains `lifecycle: {"closes_by_reason", "left", "unobserved_spans", "uncertain_lives",
  "contradictions"}`.
- `synthetic` helper (`tests/replays/replay_synthetic.py`): `actor_closed` rows carry `reason`, and a
  `closes` list lets tests add closes and reopens.
- Tests, `tests/replays/test_replay_lifecycle.py`: a dormant close then reopen stays alive and `unobserved`,
  with coverage unaffected; a dormant close with no reopen is `unobserved` to the end, not `left`; a destroyed
  close with no reopen is `left`; a close after the last `t_decided` is not `left`; a close with no `reason`
  refuses; a destroyed close then a reopen of the same GUID is `unobserved` (the "reopen after a left" pin); a
  kill by a player marked absent refuses; a kill of one refuses; a second death in one round with no revive
  refuses, while deaths in two rounds are fine; the Clove shape (death, then a self-kill, no revive) is
  `uncertain`; `alive` flags round-trip through the blob encoder.
- **Check:** `PY -m pytest -q tests/replays -p no:cacheprovider` passes.

### S6. P-d: the partition out of the linker's hard constraints (W6)

Implements "Linking", step 3, and JSON v1 `side: null`.

- `condense.py`: `side_groups` failing (inconsistent, disconnected, not 5/5) no longer refuses the condense:
  every slot's `side` is `null`, `replay_players.side_group` is `None`, and the report records
  `sides: {"resolved": false, "reason": …}`. `link_inputs` gains `spawn_points` (the match-start spawn point per
  slot, in **world** units, like `SPAWN_CLUSTER_RADIUS`).
- `app/replays/link.py`:
  - `check_partition` keeps only "10 players, one agent each, no shared Subject".
  - `assignment_candidates` enumerates every agent-respecting bijection from the 10 slots to the 10 match
    players (both teams at once), keeps those in which every non-self replay kill is cross-team under the DB's
    teams, and applies the Subject anchors.
  - The kill match (step 4) filters those. The survivors must number exactly one; the report lists `pinned`
    and `unpinned` slots (with agents) either way.
  - Proximity checks the survivor only. The spawn points are **partitioned by the survivor's teams**: refuse if
    either team's radius (distance to its own centroid) exceeds `SPAWN_CLUSTER_RADIUS`, or any slot is nearer
    the other team's centroid. Every tight round-start cluster (`start_positions`) gets the centroid rule (unit
    free). The condenser's side groups (when resolved) must map one-to-one onto the survivor's teams. Any
    failure is `refused` (`check: "proximity"`). Proximity never removes a candidate.
- Tests (added to `tests/replays/test_replay_link.py`, replacing the pass-5 partition tests): two kill-free,
  same-agent opposing slots with crossed spawn evidence refuse with two candidates; a really symmetric kill
  trace built as input refuses; a slot's spawn evidence moved to the other team refuses; misleading tight
  clusters refuse and never exclude a candidate; an unresolved partition (`side` null) still links on kills;
  same-team duplicate agents; the mirrored composition is told apart by kills.
- **Check:** `PY -m pytest -q tests/replays -p no:cacheprovider` passes, including
  `-k "kill_free and crossed"`, which selects at least one test.

### S7. P-e, P-f, P-g (W7)

**P-e, completeness** (`link.check_rounds`), implementing "Linking", step 2:
- DB played rounds are all `rounds` rows (the adapter already drops the padding; the pass-5
  `is_surrender_round` exclusion goes). `DbMatch` gains `mode` (default `"competitive"`: the crawl keeps
  Competitive only).
- The rows' winners (from `outcome`) give `(w1, w2)`. With no surrender, `(w1, w2)` must equal the awarded
  score and the winner's total must be a legal end: competitive 13 with the loser at most 11, or overtime with
  the winner at least 14 and ahead by 2; Swiftplay 5 with the loser at most 4 (`PROVISIONAL(D15)`). An unknown
  mode refuses.
- A surrender is detected when the rows reach no legal end. It links only if the awarded score is not tied,
  the awarded winner's total is a legal end for the mode (tracker.gg pads a surrender's `roundsWon`), the
  loser's awarded total equals their row wins, and the winner's awarded total is at least theirs
  (`PROVISIONAL(D15)`; finding 26 settles the shape). A dropped final replay round is allowed only for a
  detected surrender. Anything else refuses.
- The synthetic link fixtures (4 rounds at 2-2, `test_replay_link.py`, `test_ingest_replay.py`) reach no legal
  end today. They move to a legal short-mode match (`mode="swiftplay"`, a 5-0 or equivalent), so the happy
  path is a completed match, not a degenerate surrender; the pass-5 surrender/drop tests
  (`test_replay_link.py:126-137`) are rewritten to the adapter's shape.

**P-f, eligibility end to end** (`condense.py`, `contract.py`, `link.py`, `scripts/ingest_replay.py`):
- `contract.classify_diagnostics` keeps each partial-bunch error's channel. `condense` classifies each error
  channel by what it carried, with precedence **phase > lifecycle/kill > movement > other**: on a phase channel
  an error blocks linking unless the `Ended` cross-check ran (`phase_ended_checked`); on a lifecycle or kill
  channel it is recorded as validated (P-c passed, or condense would have refused); on a movement channel the
  coverage and gap limits decide; on any other channel (the real export's 74-221 carry non-player utility
  actors) it is ignored and listed. Channel kinds come from the rows' `channel` fields (events and movement).
- `link_inputs["eligibility"]`: `{"eligible", "reasons", "link_blocking", "coverage_ok", "min_coverage",
  "max_gap_s", "limits", "phase_cycle_ok", "phase_ended_checked", "lifecycle_ok", "kills_outside_rounds",
  "partial_errors": {"<channel>": {"count", "class"}}}`. A manifest-only `RoundResults` fallback diagnostic
  sets it ineligible by itself.
- `link._link` step 0 refuses (`check: "eligibility"`) unless `eligible`. `ingest_replay.py --dry-run` prints
  the eligibility and refuses before loading any DB row when it is false; `--preview` shows it as a check.
- **P-g, pairing** (`link.match_kills`): pair each replay kill (time order) with the earliest unused DB kill of
  the same mapped identity; the counts must agree; any two pairs out of time order must be within
  `MAX_RESIDUAL_S` of each other on both clocks. `TIE_WINDOW_S` goes.
- Tests, `tests/replays/test_replay_eligibility.py` and additions to `test_replay_link.py`: an incomplete DB round
  set with an unchanged score refuses (the probe: a 4-round replay against 4 rows of a 13-11 match); an
  adapter-shaped surrender links, and a padded score that the rows contradict refuses; overtime; a short mode;
  a manifest-only fallback diagnostic refuses in the linker and in `--dry-run`; coverage 52% and a 15 s gap
  refuse in both; a phase-channel partial error with and without the cross-check; jitter 0.09 s / 0.11 s pairs;
  an out-of-order pair beyond the window refuses; identity swaps refuse.
- **Check:** `PY -m pytest -q tests/replays -p no:cacheprovider` passes.

### S8. P-h: fixture sanitiser shape checks (W8)

Implements "Public repo", Fixtures.

- `scripts/make_replay_fixture.py`:
  - keeps what P-a, P-c and P-f now read: `ClientGamePhaseEnded` (`OldPhase`), `actor_closed.reason` and each
    row's `channel`;
  - cuts at the last kept round's `t_end` (the next phase) plus 2 s, not `RoundEnding + 2 s`, so the
    post-decision period is kept;
  - `SHAPES`: the expected shape of every kept field (ints for GUIDs, phases and numbers; `{x, y, z}` numbers
    for positions; numbers for yaw and times; synthetic UUIDs for `Subject`/`MatchID`; an identifier of at most
    32 characters of `[A-Za-z0-9_]` for enum-like strings such as `reason`, `WinningTeam`; `null` allowed;
    `RoundResults` items with exactly `RoundNumber`, `WinningTeam`, `WinningTeamRole`, `RoundResult`; the
    manifest's kept fields: `schema_version`, `replay_build`, `replay_version`, `parser_version` (a version
    string), `parse_status`, `stats` (ints), the synthetic `source_file`/`source_sha256`);
  - `check_shapes(value, shape)` recurses; an opaque value (a dict where a list is expected, Base64 or byte
    strings, unknown keys) in `RoundResults` is replaced by `{"fallback": true}`, and anywhere else refuses;
  - `scan_dir` also runs the shape check over every row, so the committed-fixture test catches a bad shape.
- Tests (`tests/replays/test_replay_fixture.py`): a planted identity hidden in a fallback `RoundResults` dict
  is replaced, never copied; a Base64 string in an allowlisted field refuses; a nested unknown key refuses; the
  committed fixture passes the shape check.
- **Check:** `PY -m pytest -q tests/replays/test_replay_fixture.py -p no:cacheprovider` passes, including
  `test_every_committed_fixture_is_identity_free`.

### S9. Close Stage 1a's open item on the real export (W9)

- Run `PY scripts/ingest_replay.py --export-dir EXPORT --preview --vrf VRF --out <run folder>\preview-1a`.
  The preview adds checks for: round numbering and `phase_ended_checked` (P-a), `kills_outside_rounds == 0`,
  ownership (P-b), lifecycle contradictions and the classification of the leaver and the Clove life (P-c),
  eligibility (P-f).
- Finding 25 on the leaver: the reason of each player pawn's `actor_closed`, counted.
- Record every pass-6 gate line with PASS/FAIL and its evidence in the design plan's "Stage 1a results" and
  gate. A refusal is a finding (R5), never loosened.
- Regenerate `tests/fixtures/replay/swiftplay` with `make_replay_fixture.py` only if condense output changed
  (it does: `t_decided`, the `Ended` rows and close reasons); a D entry records it.
- Expected on the real export (from the review's count): two self-kills. One (round 3) follows the same
  player's death in that round: the one `uncertain` life. The other (about 733 s) falls after round 8's
  `RoundEnding`; it was outside pass 5's windows (so pass 5's "none outside a round window" was wrong), and in
  the new playback window it is that player's first death of the round, a plain `kill`. Record this as a
  correction to finding 5.
- **Check:** the design plan's gate list has a PASS/FAIL line with evidence for P-a, P-b and P-c;
  `PY -m pytest -q tests/replays -p no:cacheprovider` passes with the regenerated fixture.

### S10. W-a: the `.vrf` header (W10)

- `app/replays/header.py`: `read_header(path) -> Header(match_uuid, length_ms, network_version, changelist,
  friendly_name_encoding)`, parsing the structure above, bounded reads, `HeaderError(ContractError)` on any
  deviation; the `FriendlyName` must be a canonical UUID (hyphenated; compared lower-case).
- `ascii_copies(path, match_uuid)`: the header UUID must also appear in ASCII in the file (finding 7), and no
  other UUID may appear in its place (the check is on the header's value; UUID-shaped strings elsewhere are
  ignored).
- `tests/replays/replay_synthetic.py`: `write_vrf` writes a valid header (UTF-16 UUID `FriendlyName`, an ASCII
  copy, the map path), so every synthetic `.vrf` parses; `test_a_vrf_not_named_by_a_match_uuid_refuses` is
  updated to the header rules.
- `condense.match_uuid_of(game, manifest, header, check_file_name)`: the game state's `MatchID` when decoded,
  else the header's; each source present must agree, and with the local file name when `check_file_name`
  (local ingest). Without a `.vrf` (preview only) the file name stays the fallback, reported as such.
- Tests, `tests/replays/test_replay_header.py`: a good UTF-16 header; an ASCII-encoded name; a truncated
  header; a renamed file (local ingest refuses, upload mode doesn't read the name); a UUID-shaped string
  elsewhere in the file doesn't change the answer; a bad magic, version or custom version; an over-long length.
- **Check:** the tests pass, and
  `PY -c "import os,sys;from pathlib import Path;from app.replays.header import header_match_uuid as h;p=Path(os.environ['USERPROFILE'],'ValorantReplayArchive','d45b2844-d7dd-4efd-bbf7-551854710350.vrf');u=h(p)[0];print(u);sys.exit(u!=p.stem.lower())"`
  exits 0.

### S11. W-b: the streaming condenser (W11)

- `contract.load_export_streaming(export_dir)`: one pass over `events.ndjson` keeps only the rows the
  condenser reads (a cheap substring pre-filter, then the exact predicate after `json.loads`), then stable-sorts
  that small subset by `time_ms`; stable sorting commutes with filtering, so the order equals the full
  contract order's subsequence. `movement` is a re-iterable stream in file order; `read_movement` stable-sorts
  each slot's samples by time, which gives the same per-slot lists as a global stable sort. No external sort
  is needed (a D entry; the files are already in order). The pass also records each channel's row kinds (for
  P-f), including the movement RPC rows the pre-filter drops, and keeps any row whose path fields name
  `/Game/Maps/` (for `discover_map`).
- `scripts/ingest_replay.py` reads only `manifest.json` before condensing (it now calls `load_export`, which
  reads the whole 1.5 GB).
- `condense_export_dir(..., streaming=True)` becomes the default; `streaming=False` keeps the in-memory path
  for the parity test.
- Tests, `tests/replays/test_replay_streaming.py`: byte-identical blobs and equal `players`, `link_inputs` and
  report on the synthetic exports (both shapes, including an out-of-order file) and on the committed fixture.
- Scratch parity on the real export (`<run folder>/parity.py`): both paths, sha256 digests of the blobs,
  `players` and `link_inputs`, wall time and peak working set (`GetProcessMemoryInfo`), each path in its own
  process. Record the before/after in the design plan (W-b).
- **Check:** the tests pass, and `parity.py` prints identical digests for both paths.

### S12. The gate harness (W12)

- `scripts/replay_gate.py`: every row of the plan's 1b gate table, each with its stated expected outcome, with
  and without decoded winners where the row allows.
  - Condense-level rows run on the real Swiftplay export (`--export-dir`, loaded once with the streaming
    loader, each corruption applied to a copy of the filtered rows): a phase `4` removed, a `5` moved after the
    next `3`, a pawn claimed by a second player state, a mid-match dormant close and reopen (expected: no
    refusal, the life `unobserved`, eligibility clean), a kill by a player after their `left` close.
  - Link-level rows run on synthetic DB objects built from a synthetic condensed replay (the test builder),
    with and without winners.
  - Rows that need the competitive replay print `WAITS (1b)`.
- Output: one line per row, `row | expected | actual | OK/MISMATCH`, exit 1 on any mismatch.
  - The "dormant close and reopen" row also runs its link half on synthetic input (expected: linked, the life
    `unobserved`).
- A test (`tests/replays/test_replay_gate.py`) runs the synthetic rows. `test_replay_isolation.py` covers
  `scripts/replay_gate.py`.
- **Check:** `PY scripts/replay_gate.py --export-dir EXPORT --vrf VRF` prints every row, and every row runnable
  now is `OK`.

### S13. W-c: the upload worker (W13, branch `-worker`)

- `replay_worker/server.py` (standard library only): `POST /jobs` (the `.vrf` body, a size cap), `GET /jobs/{id}`
  (status, then the condensed output), `GET /health`. One worker thread, a queue of 5 (`503` when full), a
  parse timeout (180 s) that kills the process tree, a memory cap where the OS allows (`resource.RLIMIT_AS` on
  Linux; reported as unsupported on Windows), temp files in one per-job directory removed in `finally`.
  It runs a configurable parser command (`REPLAY_PARSER_CMD`) then `condense_export_dir`.
- `replay_worker/Dockerfile` (written, not built) and `replay_worker/README.md`.
- Tests, `tests/replays/test_replay_worker.py`, with a stub parser command (a Python script that writes the
  synthetic export): success returns the blobs; the timeout kills a hung parse; the temp directory is empty
  after success and failure; a full queue gives `503`; an oversized body is refused.
- `test_replay_isolation.py` covers `replay_worker/*.py`.
- One local run on `127.0.0.1` against `VRF` with the local parser build; compare the returned blobs with the
  local ingest path's (same recipe) and list the temp directory afterwards.
- **Check:** the tests pass; the local run's blob digests equal the local path's; the temp directory is empty.

### S14. W-d: the viewer front end, unlinked (W14, branch `-viewer`)

- `app/static/js/replay.js` (vanilla, no build step): decodes v1 blobs, canvas over the minimap, side colours
  (neutral when `side` is null), agent labels, yaw wedges, hollow last-known markers, kill ×, `alive` flags
  shown, play/pause (space), 0.5-4×, scrub with kill ticks and `t_decided`, ←/→ 5 s, a round strip, next-round
  prefetch.
- `app/templates/replays/replay.html` (extends `base.html`) and `style.css` additions; no route.
- `scripts/render_replay_standalone.py`: condenses (or reads stored blobs) and writes a self-contained page
  under `%TEMP%\valo-replay\` inlining the template's markup, `style.css` and `replay.js`.
- `test_replay_isolation.py` covers `scripts/render_replay_standalone.py`.
- Checks: a Node smoke test if Node exists, else a Python test that runs the page's decode against the Python
  decoder's output; the standalone page for the Swiftplay export renders all 9 rounds (headless or Chrome).
- **Check:** the smoke test passes, and the standalone page shows 9 rounds with tracks.

### S15. W-e: utility events (W15, branch `-util`)

- `condense.read_util(export, players)`: `valorant_flash_cast`/`valorant_nearsight_cast` resolve the caster
  through `caster_character_net_guid` (then the player-state GUID); `*_player_hit` rows resolve targets the same
  way. Rule (a D entry): an unresolved caster drops the event and counts it; an unresolved target is left out of
  `targets` and counted; an event whose caster is absent (`left`) refuses like a kill would.
- `util` entries: `{"k": "flash" | "nearsight", "t", "by", "u", "v", "targets": [slot, …]}`, per round window.
  `CONDENSE_REVISION` bumps; `v` stays 1.
- Tests with synthetic util rows; the counts on the real export against finding 10 (4 flashes, 10 nearsights).
- **Check:** the tests pass, and the real export's counts are 4 and 10 or the difference is explained.

### S16. Final (W16)

- Both full suites on every AFK branch, compared with the baseline sets.
- `git diff a43f5fb -- webapp/app/scoring` and `git diff a43f5fb -- webapp/requirements.txt` print nothing on
  every branch; the isolation test covers `replay_worker/`.
- `git ls-files | grep -iE '\.vrf$|valo-replay|events\.ndjson$' | grep -v '^webapp/tests/fixtures/replay/'`
  prints nothing.
- The design plan's "Status and next steps" says what was done and what 1b still needs.
- **Check:** no new failing test IDs; each invariant command prints nothing.

## Provisional values this plan adds (all under D15, one grouped approval)

`PHASE_ENDED_TOLERANCE_MS = 0`; `ROUND_NUMBER_BASE = 0`; the final open `4` rule; a `.vrf` header UUID with no
ASCII copy refuses; the last window's end (the recording's end);
`LEFT_CLOSE_REASONS` (any reason except `dormancy`); the legal ends per mode and the surrender rule; the
channel classes for partial-bunch errors; the pairing window equal to `MAX_RESIDUAL_S`. The 1b gate freezes them
with the rest of "Provisional values".
