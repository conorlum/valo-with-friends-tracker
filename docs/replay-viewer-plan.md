# 2D replay viewer: plan

Goal: play back a round on the minimap in the browser, like valoplant.gg's replay page, using Valorant's own
`.vrf` replay files parsed with [michel-giehl/ValorantReplayParser](https://github.com/michel-giehl/ValorantReplayParser),
and tie it to the match and round pages this site already has.

This is the sixth pass.
- **Pass 1** (2026-09-25) was written before anyone had read the parser's source.
- **Pass 2** read that source (upstream `2b66c65`, 2026-09-23), the prior-art replayer and this repo's code.
- **Pass 3** applied two independent reviews of pass 2: the exporter contract, the linking rules, demo
  isolation, fixture sanitising and freshness.
- **Pass 4** applies the user's decisions of 2026-09-25 (see Decisions). The main change: **replays
  come in two ways**, a local ingest from the user's PC and a **friends-only upload** parsed on a private Render
  worker. An uploaded replay plays even if its match isn't in the DB, and links itself later if the match is
  crawled.
- **Pass 5** (this one, 2026-09-25 evening) records the first real export (the Swiftplay test replay). Its shape
  differs from what the parser source suggested: **no `Subject`, no round winners, no `MatchID` and no map path
  are decoded**, because Swiftplay's own player and game state classes are not decoded by the parser. The
  condenser and linker were reworked to that shape (see "Stage 1a results"), decision 1 is reopened, and
  "Status and next steps" below lists what remains. Claims still marked **[export]** now need the
  **competitive** export (Stage 1b).
- **Pass 6** (2026-09-25 night) applies two independent reviews of pass 5. Their probes on synthetic input found
  cases where the code **accepts a wrong answer** instead of refusing:
  - a wrong-but-unique assignment when spawn evidence crosses two kill-free, same-agent players;
  - a pawn claimed by two player states;
  - a dormant channel close read as a player leaving;
  - a lost or out-of-order phase RPC that drops or merges a round;
  - a truncated round set linking without winners.

  Pass 6 makes each of these a refusal or an explicit uncertainty (see "Pass 6 prerequisites"), and moves the
  side partition out of the linker's hard constraints. It also makes the gate executable and names the
  dependencies of the work-ahead list. It records the user's call that the real-match fixture needn't be
  anonymous.

## Status and next steps (2026-09-27)

**AFK run `2026-09-27-replay-1b-to-2` built Stages 1b to 4 as stacked local branches; nothing is pushed,
merged or deployed.** The file-level plan is `docs/replay-stage1b-4-impl-plan.md` (reviewed).
- `afk/2026-09-27-replay-1b` (from `replay-local-preview` @ `0570183`): the Stage 1b gate on six competitive
  matches (see "Stage 1b results": every gate row at its expected outcome, findings 14-28), the freeze of every
  provisional value (unchanged, awaiting approval), and the competitive fixture. Evidence branch; not a PR.
- `afk/2026-09-27-replay-1c` (from `origin/main` @ `c8b8a36`): **the Stage 1c PR**: the tooling, both fixtures
  and the tests; no route, migration or viewer. Its gate passed (tests, the read-only `--dry-run` linking a
  competitive export, the file checks, failing test IDs equal `origin/main`'s).
- `afk/2026-09-27-replay-2` (from 1c): **the Stage 2 PR**: stored abilities and shots (`CONDENSE_REVISION` 5),
  migration 0012, the store and link DB layer, the per-kill Impact split (decision 5 as amended), the routes,
  the viewer and the links. Its pre-merge gate passed on a throwaway cluster (see Stage 2).
- `afk/2026-09-27-replay-3` (from 2): **the Stage 3 PR**: the worker, the upload, migration 0013 and the
  `render.yaml` service (written, not applied). The local gate passed (byte-identical to local ingest).
- `afk/2026-09-27-replay-4` (from 3): **the Stage 4 PR**: the alive-count badge, the annotations and
  `reingest_replays.py`'s write mode. Its gate passed.

**Next (the user's):** merge 1c; merge 2 (the deploy applies 0012 to both DBs); ingest the six replays into the
friends DB; merge 3 (creates the paid `replay-worker` service if the Blueprint auto-syncs) and set
`REPLAY_UPLOAD_CODE`; merge 4. The run's `SUMMARY.md` has the exact commands. Still open from before: Stage
1a's two Swiftplay findings (not needed for competitive).

## Status and next steps (2026-09-25)

**Pass-6 build (2026-09-25 evening, AFK run; nothing pushed).** The file-level plan is
`docs/replay-pass6-impl-plan.md`. Four local branches, each cut from the one before it as the plan's stage order
requires:
- `afk/2026-09-25-replay-pass6` (from `overnight/2026-09-25-replay-stage1` @ `a43f5fb`), the 1c-bound work:
  P-a to P-h, W-a (the `.vrf` header), W-b (the streaming condenser: parity on the real export, 28 s and 306 MB
  instead of 49 s and 6.6 GB), and `scripts/replay_gate.py` (the executable 1b gate: every synthetic row at its
  expected outcome). The Swiftplay fixture was regenerated with the pass-6 maker.
- `…-worker` (W-c): `replay_worker/` with its tests; the local run on the real `.vrf` matched local ingest byte
  for byte. The `Dockerfile` is written, not built.
- `…-viewer` (W-d): `static/js/replay.js`, `templates/replays/`, and a standalone local page; all 9 Swiftplay
  rounds render at desktop and phone width. No route.
- `…-util` (W-e): flash and nearsight `util` entries; the real export's counts match finding 10.

**Stage 1a is still open**, with two findings from the pass-6 checks (see "Pass-6 findings on the Swiftplay
export"): P-c refuses the export (two Clove lives killed again by an enemy with no decoded revive), and P-f finds
a 4.26 s gap after `RoundEnding`. Both are for the user to decide; the checks were not loosened. Every other
pass-6 line passes.

**What 1b still needs:** the competitive recording and crawl (user); the decisions on the two 1a findings; then
`--preview` and the read-only `--dry-run` on the competitive export, `replay_gate.py --export-dir` on it, the
link-level gate rows on its real DB rows, findings 14-27, and the freeze of every `PROVISIONAL` value (pass 6 adds
the D15 set: the phase-cycle tolerance and index base, the final-round rules, the lifecycle close reasons, the
completeness and surrender rule, the partial-bunch channel classes, and the header's ASCII-copy rule).

**Where things were before the pass-6 build:**
- Branch `overnight/2026-09-25-replay-stage1` (worktree `.worktrees/overnight-2026-09-25-replay-stage1`, based on
  `replays` @ `1aaa0e3`, not pushed) holds all Stage 1c tooling: `app/replays/{format,contract,condense,link}.py`,
  `scripts/{ingest_replay,make_replay_fixture,archive_replays,with_friends_db}.py`, the parser build/export
  scripts, vendored assets, the `.gitignore` rules and 136 replay tests. `de8943c` reworks it to the real export's
  shape, and `8d37a2c` adds a real fixture (`tests/fixtures/replay/swiftplay`, round 1, movement thinned 8×,
  identity scan passed; the user decides whether it stays).
- **Stage 1a: provisionally passed, one item open.** It ran on the Swiftplay replay, with the gate amended as
  recorded below. `ingest_replay.py --preview` passed its 8 scripted checks, and the user looked at `preview.html`
  ("nothing glaringly wrong"). Parse time and memory are recorded (finding 13: 24.9 s, 147 MB). Still open: the
  pass-6 preview checks, which have not run yet: round numbering, kills outside rounds, lifecycle contradictions
  (see P-a to P-c).

  An 8/8 preview is **not** proof of a correct condense. The reviews built synthetic exports that pass 8/8 and
  are still wrong.
- Full suites under `.venv313` and `.venv`: failing test IDs identical to the pre-work baseline. There are 136
  replay tests, three of them DB-backed.

**Next, in order:**
1. **Stage 1b evidence gathering (user first):**
   - the user records a competitive match and runs the crawl;
   - the agent (or the user) archives it (`archive_replays.py`) and exports it (`export_replay.ps1 <uuid>`).

   This doesn't depend on the pass-6 fixes and can start now. **Record two or more competitive matches if
   possible**, because limits frozen from one match are a sample of one (see the 1b gate).
2. **Pass 6 prerequisites (P-a to P-h below)**, built and tested on synthetic input **before** the agent's 1b
   `--preview` and `--dry-run` count as evidence.
3. **Stage 1b analysis:** the agent runs `--preview` and the read-only `--dry-run`, answers findings 14-27, and runs
   the executable gate.
4. **Freeze** the provisional values (see "Provisional values") at the 1b gate.
5. **Stage 1c PR** from `origin/main`, carrying the plan commits and the overnight branch's tooling.
6. Stages 2, 3 and 4 as written, with the changes this pass makes.

**Pass 6 prerequisites** (code on the overnight branch, each with the synthetic case that exposed it as a test;
all are before the 1b analysis, and none needs prod or the user):
- **P-a. Phase cycle validation.** See "Exporter contract", Rounds.
  - Refuse any transition outside the expected cycle: `4` not followed by `5` before another phase, a `5` with no
    open `4`, or `4 → 3 → 5`.
  - Cross-check against `ClientGamePhaseEnded`.
  - Every kill gets a round or a recorded exclusion reason, and `kills_outside_rounds > 0` refuses.
  - When a decoded `RoundNumber` exists, it must equal the window order.
- **P-b. Pawn ownership validation.** See "Exporter contract", Players.
  - Record every claim (character `PlayerState`, `SpawnedCharacter`, `PossessedCharacter`, `Subject`) with its
    time, not just the first (`setdefault`).
  - Refuse contradictory non-null owners or Subjects.
  - Model possession as intervals.
- **P-c. Channel lifecycle.** Read `actor_closed.reason` (the export writes it: `ReplayEventJsonWriter.cs:44`).
  - A `dormancy` close, or a close followed by a reopen of the same GUID, is **unobserved**, not departed.
  - "Left" needs a non-dormant close with no reopen before the match ends.
  - Refuse contradictions: a kill by or of a player marked absent, or a second death with no revive between.
  - The Clove case is recorded as an uncertain life (see JSON v1, `alive`), not silently ended.
- **P-d. Partition out of the linker's hard constraints.** See "Linking", step 3. Enumerate teams from the DB.
  Spawn evidence only checks the result, and a slot that isn't pinned by kills or a within-team unique agent
  refuses.
- **P-e. Winnerless completeness.** See "Linking", step 2: played rounds against the DB score and mode, with
  adapter-shaped surrenders.
- **P-f. Eligibility enforced end to end.**
  - Link-blocking diagnostics, coverage and gap limits, and the P-a/P-c checks become fields in `link_inputs`
    (`eligibility`), checked by `--dry-run`, the store and the linker, not only the preview.
  - Classify partial-bunch errors by the evidence on their channel: on the channel carrying phases or lifecycle,
    they block unless P-a/P-c validate.
- **P-g. Kill pairing within the residual window.** See "Linking", step 4. This replaces independent tie groups.
- **P-h. Fixture sanitiser shape checks.** See "Public repo". Opaque fallback payloads are refused or replaced,
  and value shapes are checked recursively.

**Work that can proceed before 1b** (on side branches, merged in stage order). "Prototype" means it can be built
now but isn't a validated deliverable until the named gate:
- **W-a. Match ID from the `.vrf` header** (belongs in the 1c PR; its meaning is confirmed by finding 14).
  - Parse the header structure the way upstream does (`ReplayInfoReader.cs:47-95`): the magic, a supported file
    version and custom-version container, `LengthInMs`, `NetworkVersion`, `Changelist`, then the `FriendlyName`
    FString. A negative length means UTF-16. Bound the length.
  - Accept only a canonical UUID; no fixed byte offsets.
  - Cross-check the ASCII copy, the decoded `MatchID` when present, and the local file name. Refuse on any
    disagreement.
  - Tests: a truncated header, an ASCII-encoded name, a renamed file, a UUID-shaped string elsewhere.
  - **The header is uploader-controlled, like the file name.** W-a stops accidental mislabelling. It does not
    authenticate a match. Only the linker's proof does (see "Upload" and the dedupe rule in "Storage").
- **W-b. A streaming condenser** (belongs in the 1c PR; a prerequisite for sizing the worker).
  - It now loads the whole 1.5 GB export into memory and takes about 96 s for a 14-minute match. Stream
    `events.ndjson` and `movement.ndjson` without holding the rows.
  - It must keep the contract order (sorted by `time_ms`, equal times in file order; see "Exporter contract"),
    so check first whether the parser already writes in that order, and sort externally if not.
  - **Parity is the gate:** byte-identical blobs, `players` and `link_inputs` against the in-memory condenser, on
    the fixture and on the real export.
  - Measure peak RAM before and after. That number, plus the parser's, sizes the worker (decision 9).
  - **Built (2026-09-25, AFK run):** `contract.load_export_streaming`. The parser already writes both files in
    `time_ms` order (0 inversions in 142,091 event and 887,924 movement rows), but the loader doesn't rely on
    it: one pass keeps only the event rows the condenser reads (a substring pre-filter skips the rest unparsed),
    then stable-sorts that small subset; movement is a stream, and each slot's samples are stable-sorted by
    time. Both give exactly the contract order, so no external sort is needed.
    - Parity: byte-identical blobs, `players`, `link_inputs` and report on the synthetic exports (both shapes,
      shuffled files, equal-time rows), on the committed fixture, and on the real Swiftplay export (sha256 of
      the blobs `95c2a3b4…`, identical for both paths). The real export refuses at condense under P-c (see
      "Pass-6 findings"), so its parity run used a scratch-only override of that rule, applied to both paths
      alike; both paths also refuse with the same error without it.
    - Cost on this machine: in memory 48.8 s and a 6,579 MB peak working set; streaming **28.1 s and 306 MB**.
      With the parser's 147 MB (finding 13), a worker needs well under 1 GB for this match; Stage 3 measures
      the container.
- **W-c. The upload worker** (Stage 3 step 1; merged with Stage 3).
  - Can be built now: `replay_worker/server.py`, the queue, timeout, temp cleanup and overflow tests, with a stub
    parser command.
  - Can also be run now against the real `.vrf` with the local parser build (the agent may run the parser; see
    "The whole path"). Docker doesn't run on this machine, so the `Dockerfile` is only built on Render.
  - Final sizing waits for W-b and the Stage 3 measurement.
  - No web-service side, no `render.yaml` change.
- **W-d. The viewer front end, unlinked mode** (Stage 2 step 5, prototype). `static/js/replay.js` and a template
  driven by stored blobs, grown from the preview page's player, in the site's style, at phone width. Its reader is
  final only after the 1b freeze of JSON v1, including pass 6's `alive` causes and `t_decided`. Linked-mode names
  and the kill feed wait for Stage 2's routes. Merged with Stage 2.
- **W-e. Utility events (optional, prototype).** The export has typed flash and nearsight events whose
  caster/target subjects are null.
  - Extraction can start now: resolve casters through pawns, with an explicit rule for unresolved and absent
    actors.
  - Fill `util` with `k: "flash"`/`"nearsight"`. That needs no `v` bump, but it is a condenser change, so it
    bumps `CONDENSE_REVISION`.
  - It merges with Stage 4. Pass 5's "or earlier" contradicted "all of Stage 4 waits for 1b", and Stage 4 wins.
  - **Built (2026-09-25, AFK run, branch `afk/2026-09-25-replay-pass6-util`):** `condense.read_util`. A cast's
    caster resolves through its character pawn (or a pawn it possesses), else its player state; a hit's target the
    same way; hits join their cast by the ability actor's GUID. Unresolved casters are dropped and counted,
    unresolved targets left out and counted, and a caster or target who had left refuses like a kill (P-c). Each
    round's `util` holds `{"k": "flash" | "nearsight", "t", "by", "u", "v", "ability", "targets"}`;
    `CONDENSE_REVISION` 3, `v` unchanged. On the Swiftplay export: **4 flashes and 10 nearsights, all casters
    resolved, 14 hits (8 + 6), no orphan hits, none outside a round**, which matches finding 10.

**Must wait for 1b:**
- migration 0012 and `store.py`: the shape of `replay_players.subject` and `players.riot_subject` depends on
  findings 15 and 21;
- freezing the linker limits;
- the final JSON v1 reader;
- all of Stage 4.

**Open items for the user:** none. The parser is the agent's to run now (see "The whole path").

**Settled by the user (2026-09-25, pass 6):** the committed fixture stays, and it needn't be anonymous. The match
UUID stays in `docs/replay-viewer-handoff.md`: the match and its players are already public on tracker.gg, and
the site's own match pages are keyed by the same UUID.

## The whole path

```
                    LOCAL INGEST (the user's own games)                  UPLOAD (friends, invite code)
Valorant client     %LOCALAPPDATA%\VALORANT\Saved\Demos\<uuid>.vrf       browser → POST /replays/upload (friends site)
  │                 scripts/archive_replays.py: verified atomic copy       │  size cap, magic check, rate limit
  ▼                                                                        ▼
Source file         %USERPROFILE%\ValorantReplayArchive\<uuid>.vrf       replay-worker (private Render service, Docker):
  │                 (kept forever)                                         temp file, deleted after the job
  ▼                                                                        │
Pinned parser       scripts/export_replay.ps1 → events/movement/manifest  same pinned parser build, in the image
  ▼                                                                        ▼
Condenser           webapp/app/replays/condense.py (pure Python; the same code in both paths)
  │                 → per-round JSON v1 blobs on the replay's own clock + a private player table (agent, side group, Subject if decoded) + link_inputs
  ▼
Store               app/replays/store.py: one locked transaction → replays, replay_rounds, replay_players
  │                 local: scripts/ingest_replay.py via with_friends_db.py        upload: the web service, after the worker returns
  ▼
Linker              app/replays/link.py: if a matches row has external_id == the replay's match UUID, prove the
                    mapping (rounds, teams, players, kills, clock) and write it; otherwise stay unlinked.
                    Re-run automatically after every tracker.gg crawl. Never rewrites a blob.
  ▼
FastAPI             GET /replays/{match_uuid}              (page; 404 in demo mode)
                    GET /replays/{match_uuid}/{n}.json     (stored bytes, Content-Encoding: gzip, ETag)
                    GET /matches/{external_id}/replay      (redirect when linked)
  ▼
Browser             app/static/js/replay.js: canvas over the vendored minimap, no build step
```

Storing and linking are separate steps: the stored blob is always the same, and linking only writes a mapping
(slot → `match_players.id`, the clock offset, kill → `kill_events.id`). So a replay uploaded before its match is
crawled can be linked later without the `.vrf`.

**The agent may build and run the pinned parser** (the user's permission, 2026-09-25): `build_replay_parser.ps1`,
the parser's own tests, and `export_replay.ps1`. They read the `.vrf` and write only under `REPLAY_PARSER_DIR` and
`%TEMP%\valo-replay\`. Pass 5 and earlier said the agent may not. That was a cautious assumption, never an
observed block. The parser is still kept out of `ingest_replay.py`: `scripts/export_replay.ps1` makes the
export, and `ingest_replay.py --export-dir <dir>` reads it. The two stay separate steps so an export can be
condensed again without parsing again, and so the upload worker runs the same two steps.

## Decisions (settled 2026-09-25)

1. **Where the replay's `Subject` goes:** a new column `players.riot_subject` (UUID, unique, nullable). It is
   backfilled only under the rules in Linking step 6. `players.puuid` is left for the Riot API, whose PUUID is
   expected to be a different, encrypted string.
   **Reopened (pass 5), decided at the 1b gate by finding 21.** The Swiftplay export decodes no Subject: its
   player state (`Swiftplay_EoRCredits_PlayerState_C`) isn't decoded, and the only identities are inside an
   undecoded `AllPlayersObfuscatedPlayerInformation` blob on the recorder's own player state. The code already
   treats the Subject as optional (`replay_players` rows carry `subject = None`, the linker leaves such slots
   unanchored and backfills nothing). The options at the gate:
   - competitive decodes `BombPlayerState.Subject` → keep decision 1 as written; `replay_players.subject`
     becomes **nullable** anyway, for Swiftplay and other modes;
   - it doesn't → drop `players.riot_subject` and `replay_players.subject` from migration 0012, along with
     `UNIQUE (replay_id, subject)` and Stage 2 step 1's `players.riot_subject`. Identity then comes only from
     linking (agent, team and the kill sequence). The linker's input contract keeps a `subject` key set to None
     (or drops it in the same commit as `link.py`'s reads). Decoding the obfuscated blob is a parser change, out of
     scope.

   **(pass 6)** Finding 21 alone can't close this "keep". Decoding shows the field is present, not that it is
   **valid and stable**. If Subjects were anonymised per match, the first link would backfill a fake
   `riot_subject`, and every later link for that player would refuse for good ("already has a different
   Subject"). So "keep" also needs finding 15:
   - the recorder's decoded Subject equals the user's known PUUID; or
   - two replays show the same Subject for the same player.

   Until then, the columns can be created, but the **backfill stays off**. A replay that decodes Subjects for only
   some slots is handled per slot, as the code already does.

   **Closed 2026-09-27 (Stage 1b, findings 15 and 21): keep.** Competitive decodes 10 Subjects per match, and
   the 6 players seen in two or more of the six competitive replays each carry one stable Subject. The columns
   stay as written, and the backfill runs under Linking step 6's rules. **(2026-09-27, review B1)** The
   `players.riot_subject` column is added by migration 0012 only: `app/models/player.py` is a scoring
   HASHED_SOURCE (`impact_manifest.py:38-57`), so the ORM model is not edited, and replay code reads and writes
   the column with Core SQL.

   Either way, Subjects are compared in one canonical form: lower-case hyphenated strings on both sides. The DB
   loader converts a native `uuid.UUID` from `players.riot_subject`. A probe that passed a native UUID refused a
   correct anchor. Neither choice affects the demo, because both are additive.
2. **The friends DB** is `valowithfriendsdb` (read from `webapp/.env.remote`; only the name was printed). This is
   the value for `with_friends_db.py --expect-database`.
3. **Parser location:** a pinned build used in two places, the user's PC for local ingest and a private Render
   Docker service (`replay-worker`) for uploads. Uploads are **friends-only** (an invite code). An upload of a
   match the DB doesn't have is **stored and playable**, and links itself once the match is crawled. **Both**
   local ingest and upload are built. Local ingest ships first because it proves the shared code.
4. **ValoMaths demo:** no replays, and no upload. Enforced in code.
5. **Impact:** the replay never affects scoring. The page *shows* the stored per-player round Impact.
   **Amended 2026-09-27 (the user: "I want to see per kill impact, do what is needed").** The live kill feed also
   shows each kill's share of the stored round Impact: `leverage × kill_order_bonus_x_time` for the killer and
   `leverage × death_order_bonus_x_time` for the victim, taken from the **existing** scorer's `kill_observer`
   in a read-only rerun (`build_impact_rows_for_match`, which writes nothing). The split is stored only when the
   rerun's persisted fields equal every stored `impact_scores` row and the shares reconcile with them (the death
   side exactly, the kill side's leverage part within 1 for the rows' integer rounding), and it is shown only
   while a fingerprint of those rows is unchanged. Nothing in `app/scoring/` or its hashed sources changes, and
   no Impact value is computed any other way. It runs in user-run scripts only (`app/services/replay_impact.py`;
   see `docs/replay-stage1b-4-impl-plan.md`).
6. **Sample rate:** as granular as the parser allows, so the tracks keep the parser's native exported rate (no
   upsampling). The size budget is met with encoding, not by dropping samples (see JSON v1).
7. **Archive backup:** back up the test replay and the first competitive replay as soon as they're archived, then
   every few new matches, to private storage only.

Still open (small, with recommendations) at the end.

## Findings so far

### Environment (done)

- The .NET 10 SDK `10.0.401` is installed. The parser's `global.json` pins `10.0.301` with `rollForward:
  latestPatch`, which refuses a newer feature band. The local checkout changes that to `latestFeature`. The pinned
  build (local and in the worker image) carries the same one-line change, recorded in `webapp/replay_parser.json`.
- Clone the parser to a **short path** (`%USERPROFILE%\rp\parser`). Under the session scratchpad the checkout
  fails with "Filename too long" (MAX_PATH). Use `git -c core.longpaths=true clone`.
- Decompression is managed code: there's no Oodle DLL to ship, which also makes a Linux worker image possible.
- The agent may build, test and run the pinned parser (see "The whole path"). The user may run them too.
- **Python dependencies:** the tracker.gg ingest's preflight refuses an environment whose installed packages
  differ from the frozen scoring manifest (`ingest_preflight.py:100-115`). So nothing here adds a package to
  `requirements.txt`. The web service talks to the worker with the standard library (`urllib.request`), and
  `python-multipart` (already pinned) handles the upload.

### From the parser source (done; checked against the first export in "Stage 1a results")

This table is what the source suggested before any export. Where the Swiftplay export disagreed, "Stage 1a
results" wins; the `BombPlayerState`/`BombGameState` rows may still hold for competitive (finding 21).

| Question | What the source says | Consequence |
| --- | --- | --- |
| Is 13.06 supported? | The README lists up to 13.05, but `ValorantSeededTransform13_06` is registered (`PayloadTransformRegistry.cs:33`). | Expected to parse. **[export]** An unsupported-branch error stops Stage 1a. For uploads it's a clear "this patch isn't supported yet" message. |
| How mature is game state? | The README marks **Game State ❌** (`README.md:146`). Everything linking needs comes from it: `Subject`, `SpawnedCharacter`, `Phase`, `RoundNumber`, `RoundResults`. | Each one is an explicit go/no-go item in Stage 1a. |
| Round results | 13.05 handle layout (82-85) from 13.05 onward (`BombGameStateDescriptor.cs:46-51`, `AresRoundResults.cs:237-238`). The raw-blob fallback (`RawPayloadFallback`) fires **only** on an unknown handle (`AresRoundResults.cs:134-137,197-227`). A shift onto known handles can throw or silently misdecode (`ReadEnum` accepts any value of 8 bits or fewer, `:158-163`). Updates are partial, keyed by `encodedIndex - 1` (`:78-87`). | Fold the updates per index. Winners must pass the linker's checks, so a misdecode is refused. If the field falls back, linking refuses: v1 has no other winner source. An unlinked replay still plays. |
| Round boundaries | `MulticastEndRound(NewRoundNumber)`, `MulticastSetPhase(NewPhase)` with `EAresGamePhase` (`RoundStarting=3`, `InRound=4`, `RoundEnding=5`), and `BombGameState.RoundNumber` + `Phase` are all decoded. | A round runs from `Phase→InRound` to `Phase→RoundEnding`. The index base of `RoundNumber`/`RoundResults` is **[export]**. The condenser numbers played rounds 1, 2, … like the DB. |
| Kills | `MulticastNotifyKilledEnemy` = `KillerCharacter`, `KilledCharacter` (character net GUIDs) + `MultikillLevel` (`MulticastNotifyKilledEnemyParameters.cs:14-16`). It has no weapon or player IDs. It is an RPC on the killer's character, named "Enemy". | Resolve it through the character→Subject history. Teamkills and spike/fall deaths probably don't fire it: see the kill rules. |
| Player identity | `BombPlayerState`: `Subject`, `SpawnedCharacter`, `PossessedCharacter`, `CompetitiveTier`, a raw `UniqueId` (`BombPlayerStateDescriptor.cs:13-38`). **No team field.** | Build a character→Subject history keyed by actor over time. A new pawn per spawn is likely **[export]**. |
| Partial state | Decoded export groups carry only the properties decoded in that update (`ReplayJsonNormalizer.cs:227-233`). | Fold the updates per `actor_net_guid` in stream order. |
| Agent | Not on the player state. A character's `actor_spawned` archetype path names the agent's code name (e.g. `Wushu` = Jett). The prior art couldn't get agents without Riot's match details (`webreplayer/README.md`). | Map code name → display name with the vendored `/v1/agents` `developerName` table. An unknown code name refuses. **[export]** No agent per Subject → stop at the Stage 1a gate. |
| Team | Not decoded. | An unlabelled 2-partition ("side groups" A/B) is always derived. Linking orients it to `team-1`/`team-2`. |
| Movement | `movement.ndjson` rows carry `time_ms`, a raw `timestamp`, `shooter_character_net_guid`, `position {x,y,z}`, `yaw`, `pitch`, `velocity`, `movement_state` and `error_sentinel` (`ReplayEventJsonWriter.cs:331-367,398`). Movement comes only from `RemoteCharacterUpdates` (`ReplayExportSink.cs:129-139`). Only the latest move per update is emitted (`ReplayExportManifestWriter.cs:228-229`), and one update can hold an earlier valid move even if a later one failed (`ComponentDataStream.cs:124`, `RemoteCharacterUpdatesRpcDecoder.cs:170`). | Clock = `time_ms`. Drop `error_sentinel` rows. The exported rate is what we keep (decision 6). **Risk:** a client-side recording may be missing the recorder's own pawn, or have fog-of-war gaps in enemy tracks. The Stage 1a gate measures both. The recorder's view also means two friends' uploads of the same match can differ. |
| Map | Neither the manifest nor `BombGameState` names the map. The prior art's `MapUrl` reader waits for a field its parser doesn't emit (`webreplayer/scripts/extract-stream.mjs:51`). | Discovery from `/Game/Maps/<Map>/` actor paths is **[export]**. It is **required** for uploads, which may have no DB match. If it fails, local `--preview` takes `--map`, and an upload is refused with "map not recognised". |
| Minimap transform | The prior art uses `u = world.y · xMultiplier + xScalarToAdd`, `v = world.x · yMultiplier + yScalarToAdd` (0..1 of the square `displayIcon`). | Check it at round start. **[export]** |
| Utility | Typed events for flashes, nearsights and walls carry `caster_subject`/`target_subject`/`trigger_subject` (`ReplayEventJsonWriter.cs:132,178,196,233,253,268,302`). Smokes have descriptors but no typed event. | Recorded in Stage 1a. v1 draws none, but the `util` envelope is fixed now. |

### Stage 1a results (2026-09-25 overnight run)

- **Parser build and tests: PASS.** Upstream `2b66c65` with the `global.json` patch. The `.trx` reports 29 tests:
  23 executed, 23 passed, 0 failed. The 6 not executed all need a `.vrf` fixture that isn't on this machine, so
  they are the baseline:
  `SuppliedVyseYoruFlashReplay_EmitsCompleteRecordedLifecycle`,
  `SuppliedOmenReynaReplay_MatchesKnownCastAndHitAnchors`,
  `SuppliedPlacementReplay_EmitsSageDestructionAndVysePlacementLifecycles`,
  `SuppliedFlashReplay_EmitsCompleteRecordedLifecycle`, `SuppliedHarborReplay_EmitsRecordedNearsightLifecycle`,
  `SuppliedActivationReplay_EmitsVyseActivationWithTriggerAndActiveDuration`.
- **Export: the first attempt failed**, exiting 1 after 1.3 s without creating its output folder. Windows
  PowerShell 5.1's `Start-Process -ArgumentList` doesn't quote array elements, so a path with a space (the user
  profile) was split, and the CLI's `AcceptExistingOnly` check refused it. Step 3 now uses the scripts, which
  call the built binary with `&`.
- **Export: ran 2026-09-25** with `build_replay_parser.ps1` then `export_replay.ps1`. It writes to
  `%TEMP%\valo-replay\<full match uuid>\` (not the 8-character prefix). `events.ndjson` 814 MB,
  `movement.ndjson` 721 MB, manifest 0.6 MB, for a 23 MB `.vrf` and a match of about 14 minutes. Wall time 24.9 s and
  peak working set 147 MB (finding 13, from the agent's re-run on 2026-09-25).

**Findings 1-13 (Swiftplay, `++Ares-Core+release-13.06`, 9 rounds).** Pass 6 dropped some per-match details as
unneeded; the fixture's identifiability was then accepted (see "Public repo"):

1. **Parse:** `completed_with_warnings`. The sha256 matches the archive README. 0 malformed packets, 76
   `partial_sequence_error` diagnostics (47 on channel 1, the recorder's replay controller; the rest on object
   channels 74-221), none suppressed. They are classed **coverage**, not blocking: the per-player coverage checks
   measure what they cost, and every track was intact. `RoundResults` did **not** decode: there is no
   `BombGameState` in the export at all.
2. **Identity:** **no `Subject`**. There is no `BombPlayerState`. Each player is one character pawn for the
   **whole match** (spawned at 73 ms, never replaced), found by its `actor_spawned.archetype_path`
   `Default__<Code>_PC_C`, and its character export group carries a `PlayerState` guid pointing at an undecoded
   `Swiftplay_EoRCredits_PlayerState_C`. 10 players by `PlayerState`, exactly. `Default__Smonk_PostDeath_PC_C`
   (18 spawns) is Clove's post-death form, not a player, and is excluded. Riot IDs appear only in an undecoded,
   bit-packed `AllPlayersObfuscatedPlayerInformation` array on the recorder's `BaseReplayPlayerState` (7 of 10
   UUIDs visible, no link to a pawn).
3. **Movement:** from `ReplaysClientReceiveRemoteCharacterUpdatesSingleArrayNoAutonomous` RPCs, keyed by
   `shooter_character_net_guid`. **Native rate 125 Hz.** All 10 players present, the recorder included, with no
   fog-of-war gaps: alive-time coverage 100% for every player, largest gap 0.04 s. 60,461 movement rows belong to
   non-player characters (Clove's post-death pawns) and are dropped.
4. **Rounds:** phases arrive as `ClientGamePhaseBegin`/`ClientGamePhaseEnded` RPCs on the recorder's controller,
   not `MulticastSetPhase`. Values: 2 (between rounds), 3 (buy, 30 s; 45 s before round 1), **4 (in round, starts
   at the barrier drop)**, 5 (round ending, 7 s), 6 (side swap, after round 4 in Swiftplay). 9 complete 4→5
   windows; none dropped. No `RoundNumber`, no `MulticastEndRound`.
5. **Kills:** 66 `MulticastNotifyKilledEnemy`, all resolved through the pawns (**pass 6:** one was outside pass 5's
   4 → 5 windows; see "Pass-6 findings on the Swiftplay export"). One is
   a **self-kill** (a Clove, 15 s after she was killed): most likely her ultimate expiring after a revive.
   No resurrect RPC was seen, so the replay shows her dead from her first death. **(pass 6)** That silently drops her revived movement and shortens the life the coverage
   check measures. P-c records such a life as uncertain instead.
6. **Agents:** one per player from the archetype code. Two agents appeared twice, each time on opposite teams.
7. **MatchID:** not in the export. The `.vrf` is named by the match UUID, and the same UUID is in the file's
   header's `FriendlyName` FString (UTF-16 in this file) and later in ASCII (W-a parses the structure).
8. **Map:** **not in the export** (no `/Game/Maps/` path in any row). It is in the `.vrf`'s bytes
   (a `/Game/Maps/<code>/<code>` path); `contract.map_codes_in_file` reads it. Uploads keep the file until
   parsing ends, so this works for them too.
9. **Transform:** the preview page draws tracks over the minimap with the prior art's transform; the user
   checked it once and saw nothing wrong.
10. **Utility:** 4 flashes (8 hits), 10 nearsights (6 hits), 0 walls; `caster_subject`/`target_subject` all null.
11. **Plant and defuse:** no source. `TimedBomb_C` is in the export but undecoded. Stays null (D9).
12. **Size at the native rate:** 9 rounds, **194 KB gzipped per match, 40 KB max per round**, with delta encoding
    and linear fill of skipped grid points, no quantisation. Within budget.
13. **Parse cost:** `.vrf` 23 MB (23,130,227 bytes). `export_replay.ps1` on this machine: `exit 0 seconds 24.9
    peakMB 147`. That is well inside the 120 s gate item, with Windows memory as an early estimate only; Stage 3
    measures the worker container. The peak is sampled every 200 ms from the process's own `PeakWorkingSet64`,
    so it misses at most the last 200 ms of the run. The export writes 1.5 GB of NDJSON, so disk, not RAM, is the parser's
    cost. The condenser (not yet streaming) takes about 96 s end to end on this machine: **the condenser, not the
    parser, is now the slow step**, which makes W-b more important.

**Other observations:**
- **One player left** mid-match: that pawn's `actor_closed` arrives in a buy phase. The condenser marks
  the slot absent (`alive = []`, no track) from then on, and ends an open life with cause `"left"` if it happens
  mid-round. **(pass 6)** The condenser ignored `actor_closed.reason`, and a dormant close (a channel going quiet,
  not a player leaving) would be read the same way. P-c fixes this; finding 25 checks it on competitive.
- **Round-start positions are not a reliable side signal.** One team roamed a wide area through every buy phase;
  a 2-means split of round-start positions failed the 1500-unit radius in all 9 rounds and, in one round, produced a
  wrong 5/5 split. The **match-start spawn points** (each pawn's `actor_spawned.location`) form two tight lines
  (radius 557), and the kills alone connect all ten. Sides now come from kills, the match-start lines and only
  those round-start clusters that are tight.

**Gate, as amended:**
- build ✓;
- parse complete with no blocking diagnostics ✓;
- 10 players with one agent each, and every kill resolved ✓ (**"10 distinct Subjects" is replaced by "10
  players"**, see decision 1);
- every round has 4→5 ✓;
- tracks, coverage and gaps ✓;
- **"two 5/5 clusters at every round start" is replaced by "two 5/5 spawn lines at match start"** ✓ (per-round
  clusters are reported);
- map discovered ✓ (**from the `.vrf`**, not actor paths);
- size budget ✓;
- parse time ✓ (finding 13: 24.9 s, 147 MB peak);
- **(pass 6) run on this export, 2026-09-25 (AFK run, branch `afk/2026-09-25-replay-pass6`):**
  - P-a phase cycle ✓: 9 validated 4 → 5 windows; the `ClientGamePhaseEnded` cross-check ran (37 `Begin`, 37
    `Ended`, every one at its `Begin`'s time); no decoded `RoundNumber`; no dropped final round.
  - `kills_outside_rounds == 0` ✓ (66 kills, all inside a playback window).
  - P-b ownership ✓: 10 `PlayerState` claims on 10 player pawns, no conflict, no possession intervals.
  - P-c lifecycle ✗ (**refused at condense**): `lifecycle: a second death … with no revive or new pawn`.
    See "Pass-6 findings on the Swiftplay export" below: three Clove lives have a second death with no decoded
    revive, and only one of them has the self-kill shape the plan expected.
  - Finding 25 on the leaver ✓: the only player-pawn channel close is `destroyed`, in a buy phase (602.996 s),
    and is classified `left` (slot 0 from round 7). Every one of the export's 1,021 `actor_closed` rows is
    `destroyed`; no `dormancy` close and no reopened GUID, so this export can't show whether a leaver differs from
    dormancy.
  - P-f eligibility ✗: slot 9 has a 4.26 s track gap in round 6's post-decision period (the last sample 2.7 s
    after `RoundEnding`, the next phase 7.0 s after it). Pass 6 extended the playback window, and so the gap
    measurement, past `RoundEnding`; pass 5 measured to `RoundEnding` (largest gap 0.04 s). The replay plays; it
    is link-ineligible.
  - Every other pass-5 line still holds (tracks for every present player, coverage ≥ 94.6%, match-start spawn
    lines at radius 557, size p95 40 KB and 204 KB per match, no blocking diagnostic, blobs round-trip). The 47
    partial-bunch errors on channel 1 (phase RPCs and movement) are `phase_validated`; the other 29 fall on
    channels 74-221, which carry no evidence the condenser reads (`ignored`).
  - The match UUID now comes from the `.vrf` header (W-a): UTF-16 `FriendlyName`, 106 ASCII copies, equal to
    the file name.

Stage 1a counts as **passed** only when all of these are ✓. **It is not passed:** the two ✗ lines above are
findings for the user (R5: the checks were not loosened). The committed fixture (round 1 only) passes every
pass-6 check.

#### Pass-6 findings on the Swiftplay export (2026-09-25)

- **Clove's self-revive is not decoded, and not only as a self-kill.** Three rounds have a Clove die twice with
  no decoded revive in between: round 3 (killed at 40.1 s, then a self-kill at 55.4 s: the expected shape,
  `uncertain`), and rounds 2 and 8 (killed by an enemy, then killed **again by an enemy** 4-6 s later). The
  export has no resurrect RPC at all. Each Clove death spawns four `Default__Smonk_PostDeath_PC_C` pawns, which
  are destroyed at her next death or at round end, so the export does carry indirect evidence of the revived
  life. Under pass 6's rule ("a second death with no revive refuses, except a self-kill"), this export refuses at
  condense. A scratch-only run with every such life marked `uncertain` (not committed) condenses and passes
  every other check.
- **The post-decision period has track gaps the pre-decision period doesn't.** One 4.26 s gap (slot 9, round 6)
  in 9 rounds × 10 players, all after `RoundEnding`. With the gap limit (3 s) measured to `t_end`, the export
  is link-ineligible.
- **Finding 5 corrected:** there are two self-kills, not one. Besides round 3's, one at 733.0 s (a Fade, that player's first death of the round) falls in
  round 8's round-ending period, outside pass 5's windows, so pass 5's "none outside a round window" was wrong
  (its report didn't count `kills_outside_rounds` as a check). In pass 6's playback window it is inside round 8.

### Stage 1b results (2026-09-27, AFK run `2026-09-27-replay-1b-to-2`)

Six competitive matches (`++Ares-Core+release-13.06`), recorded by the user and crawled on 2026-09-26/27, each
archived, exported and condensed at `CONDENSE_REVISION` 4 (kills also read from lethal damage; see
`app/replays/condense.py`). Match IDs (their tracker.gg IDs, also the site's match-page keys): Ascent
`a5549826` (DB match 3671), Abyss `81e38a00` (3670), Split `47a5f123` (3672), Summit `6e52839b` (3673), Sunset
`88f60040` (3678), Haven `d07225f3` (3677). All figures below are counts; no Subject or name was printed.

**The executable gate.**
- `replay_gate.py --condensed … --db` (read-only friends DB): **144 OK, 0 MISMATCH, 0 N/A** over the six, with
  and without decoded winners. Every row of the gate table that is link-level ran on real data: each base
  replay links with one candidate and all 10 slots pinned; a shifted round number, an identity swap, a changed
  agent, a 3 s shift, the wrong match (another of the six), two kill-free opposing same-agent slots with
  crossed spawns (two candidates), a spawn moved to the other team, and the DB's last 3 rounds deleted all
  refuse; an in-window order swap and a 0.5 s shift link with the same mapping (offset +0.5 s).
- Truncation margin: **one matched kill per round** already links uniquely in all six, with and without the
  20-anchor minimum (21-28 rounds, so 21-28 anchors).
- Condense-level rows on two real exports (`--export-dir`): Ascent **63 OK, 0 MISMATCH**, base `OK` (a phase 4
  removed, a phase 5 moved past the next 3, a pawn claimed by a second player state: each refused at the check
  the table names). Abyss: **63 OK, 0 MISMATCH**, base `OK`, the same three refusals. The "kill
  after a left close" row needs a leaver, and none of the six has one (finding 25); the "dormant close and
  reopen" row runs on synthetic data only.

**Findings 14-28.**
- **14.** Match UUID from `BombGameState.MatchID` (`match_uuid_source: game_state`) in all six, agreeing with
  the `.vrf` header and the file name; `matches.external_id` equals it (case-insensitive).
- **15. Subjects are real and stable.** 36 distinct players over the six; the 6 who appear in two or more
  replays each have exactly one Subject across them, and no Subject belongs to two players. (The DB's
  `players.puuid` is empty for all 60 linked slots, so the recorder-equals-PUUID route isn't available; the
  two-replay route answers it.) **Decision 1 is closed as "keep"**, and the backfill may run (Linking step 6).
- **16.** `RoundResults` decode for every round (21-28 per match, no fallback); index i is played round
  i + 1 (D8 confirmed: every decoded winner equals the DB's); `WinningTeam` values are exactly `Red`/`Blue`
  (`Red` = team-1 confirmed). No `RoundNumber` is decoded on the game state at round start (all null), so the
  window-order check has nothing to compare and the index base (D15 `ROUND_NUMBER_BASE`) stays untested.
- **17.** DB kill classes: 1,003 matched; 5 self-kills (Summit 4, Haven 1), each also a replay self-kill;
  no killer-less and no team kills in these six. Abyss has 3 kills known only from lethal damage.
- **18. Clock.** 1,003 anchors: median offset +0.002 s, range −0.001 to +0.015 s; per match max residual
  0.004-0.013 s, median 0.001 s; **round 1's offset equals the rest** (0.000-0.001 s), so tracker.gg's
  `roundTime` counts from the barrier drop in round 1 too and needs no special rule. Every round has 5 or more
  anchors.
- **19.** Exactly one candidate per match, all 10 slots pinned, proximity agrees. Match-start spawn lines 5/5
  with radius 300-612 units (limit 1500). **No round-start cluster was tight in any round of any match**
  (`start_positions` empty), so only the match-start lines check the assignment.
- **20.** 10 proposed backfills per match (60), no conflicts (reported, not written).
- **21.** Competitive decodes what Swiftplay didn't: `BombPlayerState` with 10 Subjects per match, and
  `BombGameState` with `MatchID` and `RoundResults`.
- **22.** One pawn per player for the whole match (all six); 125 Hz; the map only in the `.vrf`; ownership
  claims 50-144 per match with 20-115 possession intervals (drones and other possessed pawns), no conflict.
- **23.** Every link-level row gives the same outcome with `round_results` emptied (the "no winners" half of
  the 144).
- **24.** Kills after `RoundEnding`: 8 in all. The 3 cross-team ones (Abyss 1, Sunset 2) are in the DB and
  match; the 5 self-kills (Summit 4, Haven 1) are excluded on both sides, as designed.
- **25.** No player-pawn channel closes at all in the six (`closes_by_reason` empty; nobody left), so whether
  a leaver's close differs from dormancy is **not answerable** from these recordings.
- **26. Surrender shape: not what P-e assumed, safe.** 37 of 3,673 DB matches have an awarded score that
  differs from their round rows; in all 37 the rows **exceed** the awarded total and include 2-10
  "Surrendered Win" padding rows. They predate the adapter's filter (`trackergg_browserstate_source.py:676-688`
  drops padding at ingest today). A replay of one of these would refuse at "played round sets differ" (safe:
  refused, never misaligned). No replay of a surrendered match exists to test the other shape.
- **27.** Partial-bunch errors: 112-161 per match on the recorder's controller channel (phase RPCs), all
  `phase_validated` (the `ClientGamePhaseEnded` cross-check ran); 69-253 on other channels `ignored`; Haven 40
  on a kill/lifecycle channel (`lifecycle_validated`). Every match is eligible.
- **28. Clove's revive decodes in competitive.** Two of the six have a Clove (Ascent, Haven); both condense with
  0 uncertain lives and 0 lifecycle contradictions, because `MulticastReceivePlayerResurrectEvent` is decoded.
  The "second death with no revive" rule needs no Clove exception for competitive.
- **Coverage and size.** Alive-time coverage 100% for every player in every match; largest track gap
  0.029-0.04 s. At c4 (no stored abilities or shots): p95 39.7-51.1 KB per round, 558-842 KB per match.

**Still open for the user (not closed by this run):** Stage 1a's two Swiftplay findings (P-c refuses the
Swiftplay export: Clove's revive isn't decoded there; P-f: a 4.26 s post-decision gap). Competitive is
unaffected (28, coverage above), and the site is competitive-only, so they only matter if Swiftplay replays are
ever wanted.

### Exporter contract (what the condenser relies on)

- **Clock:** `time_ms` on every event and movement row, the replay clock in ms. The raw movement `timestamp` is
  kept for diagnosis only.
- **Order:** rows are sorted by `time_ms`; rows with equal `time_ms` keep their file order. This is what
  `contract.read_ndjson` does. Pass 5 wrote "file order, then `time_ms`", which was wrong. W-b must keep this
  order.
- **State:** export-group rows are partial. State at time t is the fold of every earlier update for that actor.
- **Manifest:** the condenser requires:
  - `schema_version == 8`;
  - `source_sha256` equal to the input file's hash;
  - `replay_build` in the pinned parser's supported list;
  - `parser_version` containing the pinned SHA (a 12-character prefix; the real manifest carries the full SHA
    after `+`).

  Any mismatch refuses.
- **Players:**
  - Character pawns come from `actor_spawned` rows whose archetype is `Default__<Code>_PC_C`, an agent code with
    no underscore.
  - An unknown code refuses only if that pawn carries a `PlayerState`. A code with no `PlayerState` (a possible
    drone or pet pawn for an agent not yet seen) is reported and ignored.
  - Pawns are grouped into players by the character's `PlayerState` property, or by a legacy
    `BombPlayerState.SpawnedCharacter`. Exactly 10 players, or refuse.
  - **(pass 6, P-b) Ownership is validated, not first-claim-wins.** Every non-null claim is kept with its time.
    Refuse:
    - a pawn whose `PlayerState` changes to another non-null value;
    - a pawn claimed by two player states;
    - a player state whose `Subject` changes to another non-null value.

    `PossessedCharacter` is kept as intervals, so a possessed pawn resolves kills only while it is possessed.
  - A Subject is attached only when a decoded export group on that player state carries one.
- **Lifecycle (pass 6, P-c):**
  - `actor_closed` carries a `reason` (`ReplayEventJsonWriter.cs:44`, upstream `ActorChannelLifecycleService.cs:42`).
  - A player **left** only when their last pawn's channel closes for a non-dormant reason, with no reopen of that
    GUID or a new pawn before the match ends.
  - A `dormancy` close, or any close followed by a reopen, is an **unobserved** interval: the player is alive and
    unseen, not gone.
  - Refuse any contradiction, and count it in the report:
    - a kill by or of a player during a "left" interval;
    - a second death with no revive or new pawn between;
    - a reopen after a "left".
  - A death followed by a self-kill with no decoded revive (the Swiftplay Clove case) marks that life
    **uncertain**. See JSON v1, `alive`.
- **Rounds (pass 6, P-a):**
  - `NewPhase` comes from `ClientGamePhaseBegin` and `MulticastSetPhase` RPCs, plus `BombGameState.Phase` when
    present.
  - A round is a validated cycle: 4 → 5, with no other phase between.
  - Refuse:
    - a `5` with no open `4`;
    - a `4` that meets any phase other than `5` (for example `4 → 3 → 5`);
    - a second `4` while one is open.
  - `ClientGamePhaseEnded` cross-checks every begin.
  - A decoded `RoundNumber` must equal the window order (with the index base from finding 16).
  - The **playback window** runs from `4` to the next phase after `5`, so post-decision kills in the round-ending
    period are kept. The `5` time is recorded as the decision time (`t_decided`).
  - Every kill falls in exactly one window or carries an exclusion reason, and `kills_outside_rounds > 0`
    refuses.
  - Only a final `4` with no `5` may be dropped, and it is reported.
- **Match UUID:**
  - `BombGameState.MatchID` when decoded, else the `.vrf` header's `FriendlyName`, parsed structurally (W-a).
  - Each source present must agree with the others, and with the local file name for local ingest. Any
    disagreement refuses.
  - The header is an uploader-controlled claim, not proof.
- **Map:** `/Game/Maps/<code>/` in the export's paths, else `/Game/Maps/<code>/<code>` in the `.vrf`'s bytes, else
  `--map` (preview only).
- **Diagnostics**, classified in `contract.py`, and **(pass 6, P-f) carried into `link_inputs.eligibility`**, then
  enforced by `--dry-run`, `store.py` and the linker, not only shown in the preview:
  - *blocking:* any diagnostic on `BombPlayerState`, `malformed_packet_count` above 0, a bad `parse_status`, any
    suppressed diagnostic, and an unknown code;
  - *link-blocking:* `RawPayloadFallback` on `RoundResults`. The replay plays but can't link. The manifest
    diagnostic sets `eligibility.link_blocked` by itself, even if the fold didn't see the fallback;
  - *coverage:* `partial_sequence_error` and `incomplete_partial_bunch`, counted per channel and **classified by
    the evidence on that channel**:
    - on a pawn's movement channel, the coverage (≥ 90%) and gap (≤ 3 s) limits decide, and they are enforced on
      every path;
    - on the channel carrying phase RPCs (the recorder's controller: 47 of the Swiftplay export's 76), or on a
      channel carrying lifecycle or kills, they **block** unless P-a and P-c validate the whole sequence;
    - intact tracks don't prove that controller errors were harmless;
  - *ignored:* everything else, listed by code in the report.

## Architecture

### The parser build

- `webapp/replay_parser.json` (committed, no secrets) records the upstream URL, the commit SHA, the SDK
  constraint and the one-line `global.json` patch. **It is the single source for both builds.**
- **Local:** `scripts/build_replay_parser.ps1` clones or fetches into `REPLAY_PARSER_DIR` (default
  `%USERPROFILE%\rp\parser`), checks out the SHA, applies the patch, and runs `dotnet publish src\CliReader -c
  Release -o <dir>\bin`. It writes `bin\BUILD.json` (SHA, SDK version, patch hash). `scripts/export_replay.ps1
  <uuid>` exports into `%TEMP%\valo-replay\<uuid>\`.
- **Worker:** `replay_worker/Dockerfile` has two stages:
  - a .NET SDK stage that clones the same SHA, applies the same patch and publishes a Linux build;
  - a Python slim runtime stage with the .NET runtime, `webapp/app/replays/` (the condenser) and a small HTTP
    server, `replay_worker/server.py` (standard library only).
- `condense.py` refuses unless the manifest's `parser_version` and `BUILD.json` match `replay_parser.json`. A
  parser bump is one commit that changes the SHA. It rebuilds the worker on the next deploy.

Why not a submodule: it would bring a .NET tree and its CI into a Python repo. NuGet only ships the library.

### Database targets

- `scripts/with_friends_db.py` works like `with_demo_db.py` (`:80-90`):
  - it reads `webapp/.env.remote`;
  - it refuses unless `current_database()` equals `--expect-database valowithfriendsdb` and is not
    `valomaths_demo`;
  - it runs the command under `.venv313` (the frozen ingest environment) with `DATABASE_URL` set for that child
    only;
  - `--read-only` appends `options=-c default_transaction_read_only=on`.
- `app/replays/store.py` checks independently, whatever called it: it refuses when `settings.demo_mode` is true or
  `current_database()` is `valomaths_demo`.
- No bare `.venv\Scripts\python.exe <script>` against prod: that reads `.env` (`config.py:6`), and the ingest
  preflight rejects `.venv`.

### Condensed per-round timeline: JSON format v1

The blob describes the replay only, in the replay's own terms, so the same blob serves linked and unlinked
replays. The site's data (names, `match_players`, kill IDs, the DB clock) is joined in at page time through the
link mapping.

```json
{
  "v": 1,
  "round": 7,
  "map": "Ascent",
  "hz": 16,
  "t_start": -0.4,
  "t_decided": 91.4,
  "t_end": 98.4,
  "players": [
    {"slot": 0, "agent": "Jett", "side": "A"}
  ],
  "tracks": {
    "0": [
      {"t0": -0.4, "u": [4121, 9, -3], "v": [6001, 2, 0], "yaw": [90, 2, 1]},
      {"t0": 12.6, "u": [4400], "v": [5900], "yaw": [180]}
    ]
  },
  "alive": {"0": [[-0.4, 41.3, "kill"]]},
  "kills": [{"i": 0, "t": 41.3, "killer": 5, "victim": 0, "u": 4500, "v": 6012}],
  "plant": {"t": 61.2, "u": 4100, "v": 3300, "by": 5},
  "defuse": null,
  "util": []
}
```

- **Identity:**
  - `slot` (0-9) is the replay's player index. The Subject ↔ slot table lives in `replay_players`, private and
    never served. The blob never holds a name, a Subject or a PUUID.
  - `side` is the unlabelled side group A/B. Linking maps each slot to `match_players.id` and each side group to
    `team-1`/`team-2`. **(pass 6)** `side` is `null` for every slot when the condenser can't resolve a consistent,
    connected 5/5 partition (a short match, a 4v5 from the start, kill-free players). The replay is still stored
    and plays with neutral colours. Pass 5 refused the whole condense there, so such a replay couldn't even play
    unlinked. The linker never needs `side` (step 3).
  - Kills are numbered per round (`i`). Linking maps them to `kill_events.id`.
- **`round`**: played rounds numbered 1, 2, … in replay order (overtime included). A linked replay's round set is
  proven equal to the DB's.
- **Clock:** every `t` is **seconds since that round's `InRound` phase start** on the replay clock. A linked
  replay stores one match-wide `clock_offset`. The page converts DB times with `t_replay = t_db − clock_offset`
  (kill feed seeking, plant and defuse markers from the DB, Stage 4 annotations). An unlinked replay uses replay
  times only.
- **Tracks are segmented and delta-encoded.**
  - The first value of each array is absolute; the rest are differences from the previous sample.
  - A segment is a contiguous run on the `t0 + i/hz` grid, `hz` = the parser's native exported rate (decision 6),
    measured in Stage 1a (125 Hz). Samples are snapped to the grid; grid points the source skipped inside a
    segment are filled linearly (PROVISIONAL D5), never beyond the source's own rate.
  - A new segment starts at a gap over 1.0 s, a jump over 600 world units between raw moves (teleport; frozen at
    Stage 1a), a death, a revive or a pawn change.
  - Nothing is drawn between segments. The page shows a hollow last-known marker for 2 s, then hides the dot.
- **Coordinates:** `u`/`v` are ints in 0..10000. `yaw` is already projected into map space (a unit vector through
  the same affine transform), an int in degrees 0..359, interpolated along the shortest arc.
- **`t_decided` / `t_end` (pass 6):** `t_decided` is the `RoundEnding` (5) time, when the round was decided.
  `t_end` is the end of the playback window, the next phase after 5. Kills after `t_decided` are post-decision
  kills: they stay in the blob, and Stage 4 marks them as such. The DB round outcome sits at `t_decided`.
- **`alive`:** intervals `[from, to, cause]` per slot. `to` is null if they survive. `cause` is one of:
  - `"kill"`: a replay kill;
  - `"left"`: the player's last pawn closed for a non-dormant reason with no reopen, so they left the match
    (P-c);
  - `"round_end"`.

  A slot with no pawn left at the round's start has `[]` and no track. A new interval opens on a resurrect event
  (`MulticastReceivePlayerResurrectEvent`) or a new pawn for the same player.

  **(pass 6)** An interval may carry a fourth element, a flags list:
  - `"unobserved"`: its pawn's channel was dormant or closed and later reopened. The player is alive and unseen,
    and the track has a gap;
  - `"uncertain"`: the lifecycle evidence is incomplete, for example a death, then a self-kill with no decoded
    revive, as with the Swiftplay Clove.

  An uncertain life ends at the first death, and the flag tells the viewer and the coverage check that the rest
  wasn't seen.

  Pass 5's `"left"` and `[]`, and pass 6's `t_decided` and flags, arrive before any blob is stored, so `v` stays
  1. For a linked replay, DB-only deaths (the excluded kill classes) are overlaid at page time from the link's
  `db_deaths` list.
- **`kills[].u,v`:** the victim's position.
- **`util`:** a typed envelope, fixed now: `{"k": "<kind>", "t": …, "by": <slot>, …}`. Readers ignore an unknown
  `k`. A new `k` doesn't bump `v`. Changing an existing `k`'s fields does. v1 writers emit `[]`.
- **Size budget:** at most 60 KB gzipped per round at p95, and at most 1.5 MB per match, at the native rate. If
  delta encoding doesn't fit, quantise `u`/`v` to 0..4000 before dropping any samples. This is decided at the
  Stage 1b freeze.

`FORMAT_VERSION` (`"v"`) changes only when the shape changes. `replay.js` keeps a reader for every `v` still in
the DB, because uploaded replays can't be re-parsed (their `.vrf` isn't kept).

### Freshness: the recipe

- `recipe` = `"<parser sha12>.c<CONDENSE_REVISION>.f<FORMAT_VERSION>.a<ASSETS_REVISION[:8]>"`.
  `CONDENSE_REVISION` is an int in `app/replays/format.py`, bumped by any condenser change. `ASSETS_REVISION` is a
  hash of `static/data/maps.json` + `agents.json`.
- Staleness is **inequality** with the current recipe, never an ordering.
- A replay is **valid** if its rounds are complete (the replay's `round_count` rows, one recipe, each blob
  decompresses with a supported `v`). Invalid → "no replay" in the routes (logged), and listed by
  `reingest_replays.py`.
- A stale **local** replay is re-ingested from the archive, after verifying the `.vrf`'s sha256. A stale **upload**
  still plays, and the page offers "re-upload to refresh".

### Storage: migration `0012_replays`

```
replays
  id               serial PK
  match_uuid       uuid unique not null      -- MatchID if decoded, else the .vrf header's (W-a)
  match_id         int null unique  FK matches.id ON DELETE SET NULL
  map_name         varchar(64) not null
  round_count      smallint not null
  format_version   smallint not null
  recipe           varchar(80) not null
  game_branch      varchar(64) not null      -- manifest.replay_build
  source_sha256    char(64) not null         -- of the .vrf
  source           varchar(8) not null       -- 'local' | 'upload'
  link_status      varchar(12) not null      -- 'linked' | 'unlinked' | 'refused'
  link_inputs      jsonb not null            -- (pass 6) the condenser's link_inputs incl. eligibility; written once
  link_report      jsonb null                -- ids, counts, reasons; no names, no Subjects; rewritten per link
  clock_offset     real null                 -- set when linked
  kill_map         jsonb null                -- {"<round>": [kill_event_id | null, ...]}, when linked
  db_deaths        jsonb null                -- DB-only deaths per round {slot, t_db}, when linked
  created_at, linked_at  timestamptz

replay_rounds
  replay_id        int FK replays.id ON DELETE CASCADE
  round_number     smallint
  data             bytea                     -- gzip(JSON v1)
  PRIMARY KEY (replay_id, round_number)

replay_players                               -- private: never served
  replay_id        int FK replays.id ON DELETE CASCADE
  slot             smallint
  subject          uuid null                 -- null when the replay decodes none (Swiftplay); see decision 1
  agent            varchar(32) not null
  side_group       char(1) null              -- 'A' | 'B'; null when the condenser couldn't resolve sides (pass 6)
  match_player_id  int null FK match_players.id ON DELETE SET NULL
  PRIMARY KEY (replay_id, slot), UNIQUE (replay_id, subject)   -- the UNIQUE goes if decision 1 drops subject

replay_uploads                               -- upload jobs
  id               uuid PK
  status           varchar(12)               -- 'queued' | 'parsing' | 'stored' | 'failed'
  error            text null                 -- a user-facing reason
  source_sha256    char(64), size_bytes int, created_at, finished_at
  replay_id        int null FK replays.id ON DELETE SET NULL

players.riot_subject  uuid null unique       -- decision 1 (reopened): dropped if competitive has no Subject;
                                             -- created but never backfilled until finding 15 shows Subjects are stable
```

- Separate tables, so existing match and round queries never load blobs.
- **Deleting a crawled match unlinks the replay (pass 6).** `ON DELETE SET NULL` alone would leave
  `link_status = 'linked'`, `clock_offset`, `kill_map`, `db_deaths` and `linked_at` stale. So 0012 adds a row
  trigger on `replays`: when `match_id` becomes NULL, it resets `link_status` to `'unlinked'`, clears those
  fields, sets every `replay_players.match_player_id` of that replay to NULL, and adds `{"unlinked": "match
  deleted"}` to `link_report`. (A row trigger fires on the update the FK action makes.)
  - The routes also treat a replay as linked only when `link_status = 'linked' AND match_id IS NOT NULL`, so stale
    link data is never rendered.
  - The link-later hook selects replays by `match_id IS NULL`, not by status. Nothing is lost: blobs and
    `link_inputs` are untouched.
- **Writes** go through `store.py`, one transaction per replay:
  1. `pg_advisory_xact_lock(hashtext(match_uuid))`, which serialises concurrent stores and links of one replay.
  2. An existing `match_uuid` with the same `source_sha256` and recipe → no-op. With a different sha (for example
     a second friend's recording of the same match):
     - if the existing replay is **linked**, keep it and report the new one;
     - if the existing replay is `unlinked` or `refused`, and the new one links in this same transaction, the new
       one replaces it.

     A garbage, refused or mislabelled upload therefore can't squat a `match_uuid` forever. Local `--replace`
     overrides this rule. Uploads only replace under it.
  3. Insert or replace `replays` + one multi-row `INSERT` each for `replay_rounds` and `replay_players`.
  4. Run the linker inside the same transaction (below).
  5. Commit. Any failure rolls it all back and the old rows survive.
  That's about ten round trips at ~65 ms each, not one per row.
- **Linking later** (`link.py`, its own locked transaction, called by `store.py`, by
  `scripts/link_replays.py`, and best-effort at the end of `ingest_trackergg_player.py` and
  `refresh_tracked_players.py` for every newly ingested `external_id` that has a replay with `match_id IS NULL`). It writes only
  `replays.match_id`, `clock_offset`, `kill_map`, `db_deaths`, `link_status`, `link_report`,
  `replay_players.match_player_id` and the `riot_subject` backfill (only when decision 1 keeps it and finding 15
  allows it). **It never rewrites a blob or `link_inputs`**, the only copy for an upload whose `.vrf` is gone.
- No cache invalidation is needed. Replays feed no cache and **no Impact scoring**, and nothing touches
  `app/scoring/`.

### Linking (refuse rather than misalign)

`link.py` takes the stored replay (blobs, `replay_players` and `link_inputs`) and the DB rows. It either writes a
mapping or records `link_status = 'refused'` with the first failing check in `link_report`. The replay stays
playable either way. A refused link never shows site data. The report holds IDs, counts, agents and reasons only.

**What a unique survivor proves (pass 6).** "Exactly one candidate" proves uniqueness only inside the constraints
the enumeration assumes. In pass 5, the side partition was a hard constraint built partly from spawn proximity.
A review probe crossed the spawn evidence of two opposing, kill-free Jetts: the correct assignment was excluded,
and the wrong one linked with 24 anchors and zero residual. So the linker now takes teams only from the DB and
from kills, and uses proximity only as a check. Every slot must also be *pinned* by evidence that no permutation
can satisfy.

0. **Eligibility (pass 6, P-f).**
   - Refuse unless `link_inputs.eligibility` is clean: no link-blocking diagnostic (from the manifest as well as the
     fold), coverage and gap limits met, phase cycle and lifecycle validated (P-a, P-c), and
     `kills_outside_rounds == 0`.
   - `--dry-run` and `store.py` apply the same check.
1. **Match.** Exactly one `matches` row with `external_id == match_uuid` (lower-cased). None → stay `unlinked`
   (the normal case for an upload of an unknown match). **No fallback lookup** by map, time or roster.
2. **Rounds.**
   - *Replay played rounds:* the stored rounds, 1..`round_count`. `round_count` excludes a final round with no
     `RoundEnding`, which the condenser drops and reports.
   - *DB played rounds:* the `rounds` rows. The adapter already drops tracker.gg's surrender padding at ingest
     (`trackergg_browserstate_source.py:558-560,679-689`). So `is_surrender_round` finds nothing in crawled data,
     and pass 5's surrender allowance (`link.py:129`) was dead code.
   - The **sets of round numbers** must be equal. When decoded, `RoundNumber` must match too (P-a).
   - **Completeness (pass 6, P-e), with or without winners.** Played rounds are compared with the match's
     awarded score and mode:
     - with no surrender, `team1_rounds_won + team2_rounds_won` must equal the number of played rounds, and the
       winner's total must be a legal end for the mode (13 in competitive, or overtime win by 2; Swiftplay 5);
     - a surrender is detected from the score, since the padding is gone: the awarded total is higher than the
       played rounds, or no team reached a legal end. Its shape is finding 26;
     - a dropped final replay round is allowed only for a detected surrender.

     Any other discrepancy refuses. A probe linked a 4-round replay to 4 DB rounds of a 13-11 match; this closes
     that.
   - *When the replay decoded round winners* (`RoundResults`, in `link_inputs`):
     - every round's winner must equal `rounds.outcome`'s winner;
     - the played-round winners must be consistent with the awarded score (equal, unless it's a surrender);
     - a `RoundResults` fallback, from the fold or from the manifest diagnostic, refuses.
   - *When it decoded none*, the report records `winners_checked: false`. **(pass 6 answer to pass 5's open
     question)** A stricter clock limit or more anchors can't settle an ambiguity between two players who are in
     no kills, so neither is the lever. What is required instead:
     - the completeness check above;
     - the pinning rule in step 3;
     - the executable corruptions in the 1b gate.

     With those, winnerless replays use the same limits.
3. **Teams and players.**
   - Exactly 10 players with one agent each; no Subject shared by two players. Subjects may be absent.
   - *Teams come from the DB (pass 6, P-d).* Enumerate both orientations × every agent-respecting bijection from
     slots to `match_players` in which **every replay kill is cross-team** under the DB's teams. The condenser's
     side groups are **not** a hard constraint.
   - *Proximity is a check only:*
     - the match-start spawn lines (now in `link_inputs`) and the tight round-start clusters must agree with the
       winning assignment's teams, or the link refuses;
     - they never remove a candidate, so they can't exclude the true one.
   - *Pinning.* A slot is pinned when every surviving candidate maps it to the same match player. That happens when:
     - it is in a matched kill;
     - its agent appears once in the match;
     - every other slot with its agent is pinned.

     Two kill-free, same-agent slots on opposite teams (the probe's case) now give **two** survivors, so the link
     refuses. It can no longer pass wrongly. The report lists unpinned slots and their agents, and proximity may
     not break the tie.
   - A candidate must match, when decoded, the per-round winners (`WinningTeam` FName, expected `Red`/`Blue`
     **[export]**), the ordered kills (step 4), and every existing `players.riot_subject` anchor for slots that
     have a Subject. Anchors compare canonical lower-case strings (decision 1).
   - **Exactly one** candidate must survive. Zero or more → `refused`.
   - A mirrored composition is normally told apart by the kills; the tests show this
     (`test_replay_link.py:159-167`). Pass 5 wrongly called it "the expected more-than-one case". Only kill
     sequences that really are symmetric leave two candidates, and then the link refuses.
4. **Kills, per round.**
   - DB kills minus the excluded classes:
     - `killer_match_player_id IS NULL` (`trackergg_browserstate_source.py:750`);
     - killer == victim;
     - same-team pairs.

     Excluded deaths go into `db_deaths`, and nothing else may be unmatched.
   - Replay self-kills (the Swiftplay Clove case) are skipped: they pair with nothing, and their `kill_map` entry is
     null.
   - Kills in the round-ending period (after `t_decided`) are in the window and are matched like any other
     (finding 24 checks that the DB has them).
   - **Pairing (pass 6, P-g).** The (killer, victim) sequences must be identical. The pairing tolerance is the
     residual limit, not a separate 0.1 s tie window:
     - two kills may be paired out of time order only if they are within `MAX_RESIDUAL_S` of each other on both
       sides;
     - within such a window, pairs are matched as a multiset.

     Pass 5 grouped ties separately on each side, which refused real jitter (0.09 s on one side, 0.11 s on the
     other). It also allowed 0.75 s residuals while needing exact order past 0.1 s.
5. **Clock.**
   - One match-wide offset: the median of `db_time − replay_time` over every matched kill. (Plant and defuse
     anchors wait for a replay source for them: finding 11.)
   - Provisional limits: |offset| ≤ 2.0 s, ≥ 20 anchors, every residual ≤ 0.75 s, median |residual| ≤ 0.25 s.
   - Every round needs one anchor within the limit. No per-round fitting. Zero-anchor rounds and matches with few
     kills refuse. Keep that unless independent evidence justifies relaxing it.
   - The zero point of tracker.gg's `roundTime` against the barrier drop is finding 18. If it counts from the buy
     phase, the offset differs in round 1 (45 s buy) and needs its own rule before freezing.
6. **Backfill** of `players.riot_subject`: only for slots with a Subject, only if decision 1 keeps the column,
   and only once finding 15 shows Subjects are stable. It is the last statement, rechecked under lock
   (`FOR UPDATE`):
   - each linked player's `riot_subject` is NULL or already equal;
   - the incoming Subject belongs to no other player row.

   Either conflict → `refused` for the link (the stored replay stays, unlinked). No substitution, no guessed merge.

The condenser also outputs a private `link_inputs` record per replay:
- the decoded `RoundResults` per round (empty when none decoded);
- the decoded `RoundNumber`s;
- start-of-round positions per slot for the tight rounds;
- the match-start spawn points per slot;
- the replay kill list in slot terms;
- `eligibility`: the diagnostic classes, the coverage and gap results, and the phase-cycle and lifecycle results.

It holds no Subjects. **(pass 6)** It is stored in its own column, `replays.link_inputs`, written once at store
time and never by a link. Pass 5 put it inside `link_report`, which every link rewrites, and that could delete the
only copy for an upload.

The tracker.gg crawl keeps **Competitive matches only** (`trackergg_browserstate_source.py:108,245`). This answers
pass 5's [1b] question. Swiftplay and other modes never link, and they play unlinked. The winnerless path matters
only if competitive also decodes no winners (finding 21).

### Upload (friends only)

- **Gate:** the upload page and endpoint need an invite code. `REPLAY_UPLOAD_CODE` is set in the Render dashboard
  for the friends service only (never in the repo or `render.yaml` values). The code is checked with a
  constant-time compare, then remembered in the session. The site has no real login (`RENDER_DEPLOY.md`:
  "deliberately zero-auth"), so this code is the only gate. Rotating it is a dashboard change.
- **`POST /replays/upload`** (friends service; 404 in demo mode or when no code is configured):
  - it takes a multipart `UploadFile` (already spooled to disk by `python-multipart`);
  - refuses over 80 MB, or if the file doesn't start with the replay magic bytes;
  - rate limit: 10 uploads per hour per session and per IP, and one job at a time per session;
  - it computes the sha256, creates a `replay_uploads` row (`queued`) and streams the file to the worker with
    `urllib.request`;
  - it returns the job page `/replays/uploads/{id}`, which polls a small status endpoint every 3 s.
- **`replay-worker`** (a Render **private service**, Docker, reachable only over Render's private network;
  `REPLAY_WORKER_URL` on the friends service):
  - `POST /jobs` saves to a temp file and queues it (one job at a time, a queue of 5, 503 when full).
  - Each job runs the parser with a **180 s timeout** and a memory cap, as a non-root user, then runs the condenser
    and returns the condensed output (blobs + `replay_players` + `link_inputs`) on `GET /jobs/{id}`. The temp
    files are deleted when it finishes, whatever the outcome.
  - **(pass 5)** The condenser reads the map from the `.vrf` (the export doesn't name it), so the file must stay
    until condensing ends. The match UUID comes from the `.vrf` header (W-a), **never from the uploaded file
    name**, so a renamed file keeps its own `match_uuid`. **(pass 6)** The header is uploader-controlled too. It
    is a claim, not proof. What protects a match is the linker's proof, together with the dedupe rule in "Storage"
    (an unlinked or refused replay can't block a later one that links).
  - **(pass 5)** The export is about 65× the `.vrf` (1.5 GB for 23 MB). The worker writes it to a temp directory
    sized for that, and the condenser streams it (W-b); both go into the plan sizing (decision 9).
  - The worker holds **no DB credentials and no secrets**. It only parses.
- **The web service** then calls `store.py` (store + link in one transaction) and marks the job `stored` or
  `failed` with a plain reason ("patch 13.07 not supported yet", "not a Valorant replay", "map not recognised",
  "parse failed"). A job stuck in `parsing` for over 10 minutes (for example after a worker restart) becomes
  `failed: please re-upload`.
- **Uploaded `.vrf` files are not kept** (decision 8). An upload can't be re-parsed after a parser upgrade;
  it keeps playing under its stored `v`, and the page offers a re-upload.
- `render.yaml` gets the `replay-worker` service (type `pserv`, runtime `docker`, `dockerfilePath:
  replay_worker/Dockerfile`, an explicit `plan:` sized by decision 9 (the Stage 3 container measurement, after W-b), because a Blueprint
  sync resets the plan). The ValoMaths demo service is hand-configured, doesn't get `REPLAY_WORKER_URL` or
  `REPLAY_UPLOAD_CODE`, and has upload switched off in code anyway.

### The viewer

- **Routes:**
  - `GET /replays/{match_uuid}?round=n` is the page. For a linked replay it shows the site data. For an unlinked
    one it shows agents and side colours only, with "not linked to a match on this site" or the refusal reason.
  - `GET /matches/{external_id}/replay` redirects to it when a linked, valid replay exists, and 404s otherwise.
    `/matches/{id}/rounds/{n}` stays the htmx partial it is (`matches.py:126-132`).
  - Every replay route 404s when `settings.demo_mode` is true.
- **JSON:** `GET /replays/{match_uuid}/{n}.json` sends the stored bytes unchanged as a raw `Response`, with
  `Content-Encoding: gzip`, `Content-Type: application/json`, `ETag: "<sha256 of the stored bytes, 16 hex>"` and
  `Cache-Control: private, no-cache` (a cheap `304`, and a replacement is seen at once). **(pass 6)** Pass 5 used
  `"<recipe>-<n>"`. A local `--replace` with another recording under the same recipe kept that ETag, which would
  serve old tracks with a 304 while the page carried the new link mapping. The page's link data also carries the
  replay's `source_sha256`, so the two can't disagree. There's no `GZipMiddleware` (`main.py:28-33`). If
  one is added, these routes must be excluded from it. The link mapping (slot → match player, clock offset, kill
  map, DB-only deaths) is rendered into the page, not into the blob.
- **Links in:** a "Replay" button in the match page header and inside each round's detail partial (the one
  rendered with the page and the htmx-loaded ones), shown only when `not demo_mode` and a linked, valid replay
  exists. The friends site's nav gets "Upload replay" (only when a code is configured). "No replay" is normal.
- **Page** (`templates/replays/replay.html` + `static/js/replay.js`, vanilla JS):
  - A square canvas over the minimap. Dots are team-coloured when linked (`.team-name-team-1/2`, `style.css:930`)
    and side-coloured when unlinked, with agent icons (`static/img/agents/`), yaw wedges, segments and hollow
    last-known markers, and a grey × at a death.
  - Controls: play/pause (space), 0.5×/1×/2×/4×, a scrub bar with kill ticks and plant/defuse markers, ←/→ for
    5 s steps, and prev/next round with a round strip.
  - Prefetch of the next round.
  - When linked: player names, and the kill feed from `kill_events` (time, killer, weapon, victim, as in
    `_round_detail.html:13`). Clicking a kill seeks to 1 s before `t_db − clock_offset`. No trade marker: the
    round page has none today.
- **Map assets:** `scripts/vendor_map_assets.py` writes `static/img/maps/<Map>.png`, `static/data/maps.json` and
  `static/data/agents.json` (`developerName` → the DB's `match_players.agent` spelling, e.g. `KAY/O`). They are
  committed, small, covered by `ASSETS_REVISION`, and baked into the worker image through `app/`. Nothing is
  hotlinked.

### Two sites

Both web services deploy from `main` and **both run `alembic upgrade head` on build** (`render.yaml:10-13`,
`RENDER_DEPLOY.md:120`). So 0012 creates the empty tables on the friends DB *and* the demo DB. The new tables are
additive and don't break the demo. `dump_seed_data.py` lists its tables explicitly (`:23`), so the seed is
unaffected.

- **Friends site:** local ingests and friends' uploads.
- **ValoMaths demo (decision 4): no replays and no upload**, enforced three ways:
  - every replay route and the upload endpoint 404, and every link is hidden, when `demo_mode` is true, even with
    rows present;
  - `store.py` refuses a demo target on its own;
  - the demo service has no worker URL or upload code.
  A route test inserts replay rows into a `demo_mode=True` app and checks for 404s and no links.

### Public repo

- `*.vrf` is gitignored (Stage 1c's PR), plus `**/valo-replay/`, `events.ndjson`, `movement.ndjson` and
  `manifest.json`, with `!webapp/tests/fixtures/replay/**`. Exports, reports and previews go to
  `%TEMP%\valo-replay\`.
- **Fixtures** (`scripts/make_replay_fixture.py`) are built from an **allowlist**:
  - only the event types and fields the condenser reads;
  - every identity synthesised consistently: `Subject`, `caster_subject`/`target_subject`/`trigger_subject`,
    `MatchID`, `source_file`;
  - raw payloads, `source_sha256` and non-synthetic diagnostics are dropped;
  - **(pass 6, P-h) value shapes are checked, not just field names.** Every kept value is checked recursively
    against its expected shape: ints for GUIDs, numbers for positions, the fixed keys of a `RoundResults` item.
    - An opaque value (a raw fallback dict, Base64 or byte strings) is refused, or replaced by a synthetic
      `{"fallback": true}` marker.
    - The pass-5 generator copied a fallback `RoundResults` dict unchanged. A review probe hid an encoded identity
      in one, and it survived both the sanitiser and the scanner.
  - the script scans its output against the original export's identities and refuses on any hit.
  - A committed test fails on any UUID outside the synthetic set, any `C:\Users`/`%USERPROFILE%` path, any
    `Name#TAG` pattern, and any value that fails the shape check.
- **Fixtures from real matches are pseudonymised, not anonymous (pass 6).** The fixture's agents, spawn points,
  kill sequence and timings are unchanged real gameplay. With the match UUID (in
  `docs/replay-viewer-handoff.md:31`) or enough match details, anyone can find the public tracker.gg match.
  **The user accepted this** (2026-09-25): the match and its players are already public on tracker.gg, and this
  site's match pages use the same UUID. So:
  - the fixture is described as "derived from real gameplay with identifiers replaced";
  - the sanitiser still removes what isn't public elsewhere: Subjects, source paths, hashes, raw payloads and
    diagnostic messages;
  - the scanner still guards against leaks, above all credentials and local paths.
- Findings written into this plan: counts, rates, sizes and synthetic rows only.
- `REPLAY_UPLOAD_CODE` and the DB URLs live only in the Render dashboard and gitignored `.env*` files. The worker
  image contains no secrets.

### Tests

- **Parser:** see the Stage 1a baseline.
- **Repo:** run under `.venv313` and `.venv`, comparing **sets of failing test IDs** against a baseline recorded on
  `origin/main` at the start of Stage 1c.
- **Unit tests** (`tests/replays/`):
  - *exporter contract:* folding, ties, manifest checks, diagnostic classes, and a manifest-only `RoundResults`
    fallback diagnostic that makes the replay link-ineligible;
  - *ownership (P-b):*
    - a pawn claimed by two player states refuses;
    - a pawn whose `PlayerState` changes refuses;
    - a Subject that changes refuses;
    - a possessed pawn resolves kills only inside its possession interval;
  - *lifecycle (P-c):*
    - a dormant close and reopen stays alive and `unobserved`;
    - a destroyed close with no reopen is `left`;
    - a kill by a player marked absent refuses;
    - a second death with no revive refuses;
    - the Clove shape is `uncertain`;
  - *rounds (P-a):*
    - index base;
    - a missing `RoundEnding` (surrender or not);
    - `4 → 3 → 5` refuses;
    - an orphan `5` refuses;
    - a lost `4` refuses and does not renumber;
    - decoded `RoundNumber` that disagrees with the window order refuses;
    - a kill outside every window refuses;
    - a kill after `t_decided` is kept;
    - equal counts with different keys, a short match, halftime, overtime;
  - *tracks:* segments, gaps, teleports, death, revive, pawn change, native-rate grid, delta encoding, yaw
    projection, u/v against two known points;
  - *linking:*
    - the happy path;
    - a duplicate agent across teams;
    - a mirrored composition told apart by kills;
    - a **really symmetric** kill trace, built as input rather than by stubbing `match_kills` (the pass-5 test
      replaces the matcher), which refuses;
    - **two kill-free, same-agent opposing slots with crossed spawn evidence** (the review probe), which refuses;
    - same-team duplicate agents (non-competitive modes);
    - misleading tight clusters, which refuse and never exclude a candidate;
    - spawn evidence that disagrees with the kill-derived teams;
    - each excluded kill class;
    - an unexpected unmatched death;
    - jittered kills paired within the residual window;
    - offset and residual limits;
    - a round with no anchor;
    - a low-kill match, which refuses;
    - an incomplete DB round set with an unchanged score, which refuses;
    - an adapter-shaped surrender, both linked and refused cases;
    - overtime;
    - a short mode;
    - no DB match (stays unlinked);
    - a later link after a crawl;
    - an ineligible replay (coverage 52%, a 15 s gap), which refuses in the linker and in `--dry-run`;
  - *backfill:*
    - both conflict directions leave the replay stored but unlinked, with no partial writes;
    - a native `uuid.UUID` anchor matches its string Subject;
  - *header (W-a):* see W-a;
  - *streaming (W-b):* byte-identical output to the in-memory condenser;
  - *format:* round trip, size budget, unknown `util.k`, every supported `v` decodes, `t_decided`, `alive` flags;
  - *fixture scanner:* catches a planted identity, including one hidden in an opaque fallback payload.
- **DB tests** (throwaway PG18 cluster):
  - a store with a forced failure keeps the old rows;
  - a same-sha re-store is a no-op. A different-sha store keeps a linked first replay, and replaces an
    unlinked or refused one when the new one links;
  - concurrent stores and links of one replay serialise;
  - deleting a match unlinks the replay **and clears** `clock_offset`, `kill_map`, `db_deaths`, `linked_at` and
    `replay_players.match_player_id` (the trigger), leaving `link_inputs` untouched. The link-later hook then finds
    it by `match_id IS NULL`;
  - an incomplete round set reads as "no replay".
- **Route tests:**
  - 404s and no links in demo mode *with rows present*;
  - the gzip headers **and** the decoded body;
  - `ETag`/`304`;
  - a new body after a replacement following a cached fetch, **including a `--replace` under the same recipe**;
  - links on both the initial and an htmx-loaded round detail;
  - unlinked pages show no site data, and no stale link data renders after a match is deleted;
  - upload: 403 without the code; size, magic and rate-limit refusals; 404 in demo mode.
- **Worker tests:** the timeout kills a hung parse; temp files are gone after success and failure; a queue
  overflow → 503.

## Stages

| Stage | Outcome | Touches prod? | Blocked on |
| --- | --- | --- | --- |
| 1a. Parse + render feasibility | Evidence that the Swiftplay replay parses into 10 correct tracks, rounds and kills; parse time and memory measured (scratch only) | No | The export |
| 1b. Linking feasibility | Evidence that a competitive replay links exactly; limits and JSON v1 frozen (scratch only) | Read-only | The competitive replay + its crawl (user) |
| 1c. Tooling PR | Condenser, linker, store logic (no DB yet), fixtures, tests and local scripts on `main` | No | The 1b gate |
| 2. Local ingest + playback | The friends site plays a locally ingested match, linked; the demo is provably isolated | Migration on merge + the first ingest (user) | 1c merged |
| 3. Friends upload | A friend with the code uploads a `.vrf` and gets a replay page, linked or not; auto-link after crawls | New Render service + uploads | The Stage 2 gate |
| 4. Site analysis | Playback state, annotations and round Impact beside the map; re-ingest tooling | Re-ingest writes (user-run) | The Stage 2 gate |

Stages 3 and 4 are independent, and either can go first. Upload is listed first because the user asked for it.

### Stage 1a: parse + render feasibility (branch `replays`, scratch only)

**Goal:** decide whether the parser gives enough to build on, and size the upload worker.

**Steps:**
1. The test replay is already archived. The user backs up the archive folder.
2. The user builds and tests the parser (done 2026-09-25; PowerShell syntax, so run it in a PowerShell window):
   ```
   cd $HOME\rp\parser; dotnet build ValorantReplayParser.sln -c Release; if ($?) { dotnet test ValorantReplayParser.sln -c Release --no-build --logger "trx;LogFileName=$HOME\rp\parser-tests.trx" }
   ```
   A failed build stops Stage 1a. The agent reads the `.trx` and records the **exact set** of failing tests as the
   baseline. Only tests that need a missing `.vrf` fixture may be in it.
3. The user builds the pinned parser and exports, from a PowerShell window in the worktree's `webapp\`,
   measuring time and peak memory for the worker's sizing:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_replay_parser.ps1
   powershell -ExecutionPolicy Bypass -File scripts\export_replay.ps1 <match uuid>
   ```
   The export writes `%TEMP%\valo-replay\<match uuid>\` and ends with `exit … seconds … peakMB … size …`. (The
   pass-4 one-liner here used `Start-Process -ArgumentList`, which splits paths with spaces in Windows
   PowerShell 5.1; don't use it. Stage 3 measures the real peak in the worker container.) The `!` prefix in
   Claude Code runs bash, not PowerShell, so these go in a PowerShell window.
4. The agent answers, with counts and scratch Python scripts, without identities:
   1. `parse_status`; that `source_sha256` equals the archive README's hash; the diagnostics by code, classified;
      whether `RoundResults` decoded.
   2. `Subject`: 10 distinct? `SpawnedCharacter`/`PossessedCharacter` decoded? A character→Subject history?
   3. Movement: the **native exported Hz** per player, gaps, jump sizes, after-death behaviour, the error-sentinel
      rate. **All 10 players present, the recorder included? The share of alive time covered, per team?**
   4. Round boundaries from `Phase` vs `RoundNumber` vs `MulticastEndRound`, and the index bases.
   5. Kills resolved to two Subjects; the count per round vs the scoreboard; teamkills, spike and fall deaths.
   6. Agent per Subject.
   7. `MatchID` vs the file name.
   8. Map discovery from actor paths (**required for uploads**).
   9. The transform: round 1's start positions on the minimap, as a scratch HTML page shown to the user.
   10. Utility: counts per typed event, and whether caster and target resolve.
   11. Plant and defuse: the source and the times.
   12. Scratch condensation at the native rate: gzipped size per round with and without delta encoding.
   13. Parse wall time and peak memory (step 3), and the `.vrf` file size, for decisions 9 and 10.

**Gate (yes/no; any "no" stops and is reported).** Pass 5 amended three items after the Swiftplay export
("10 players" for "10 distinct Subjects", match-start spawn lines for per-round clusters, the map from the
`.vrf`); the amended gate and its results are under "Stage 1a results". The original list:
- The build succeeds, and failing tests ⊆ the missing-fixture baseline.
- `parse_status` is complete, the hash matches, and there are no blocking diagnostics.
- 10 distinct Subjects, one agent each, every kill resolved.
- Every round has `InRound` and `RoundEnding`, with consistent numbering.
- All 10 players have tracks in every round, with alive-time coverage ≥ 90% per player and no gap > 3 s (limits
  recorded here).
- At t = 0 of every round there are two 5/5 clusters within 1500 units of their centroids, inside the spawn areas
  on the scratch page (the user confirms the picture once).
- The map is discovered from actor paths.
- The native rate fits the size budget with delta encoding (and quantisation if needed).
- Parse time ≤ 120 s, with the peak memory and file size recorded here (they size the worker and the upload
  limits, decisions 9-10).

**User runs:** nothing required. The agent may run the build/test and export commands (2026-09-25).

### Stage 1b: linking feasibility (branch `replays`, scratch + read-only prod)

**Goal:** prove on real data that the linking rules accept the right match and could only refuse, never
misalign. Then freeze the numbers and JSON v1.

**Steps:**
1. The user records a competitive match. The agent (or the user) archives it with `scripts\archive_replays.py`
   (verified copy, appended to the archive's `index.json`) and exports it as in 1a step 3. The user backs up the
   archive.
2. `scripts/with_friends_db.py` (already built on the overnight branch) runs the crawl. From a PowerShell window
   in `webapp\` (the `!` prefix runs bash, which mangles `.\` paths):
   ```powershell
   .\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\ingest_trackergg_player.py "<Riot ID>" --count 20
   ```
   The agent then runs `ingest_replay.py --export-dir <dir> --preview`, and the read-only
   `with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\ingest_replay.py --export-dir <dir>
   --dry-run`.
3. The agent runs read-only checks (`with_friends_db.py --read-only`) and a scratch linker. **The pass-6
   prerequisites (P-a to P-h) must be in place first**, or a passing dry-run proves nothing. It answers:
   14. `MatchID == matches.external_id`? Also: does the structurally parsed header UUID (W-a) agree with it and
       with the file name? (If not, stop. A fallback lookup is its own design.)
   15. **Are Subjects real and stable** (not anonymised per match)? Either:
       - the recorder's decoded Subject equals the user's known PUUID; or
       - two replays show the same Subject for the same player.

       Presence alone doesn't answer this. Without it, the backfill stays off (decision 1).
   16. The round index mapping, the winner FName values, set equality, and decoded `RoundNumber` against the
       window order (P-a).
   17. The kill sequences under the exclusion rules: which classes, and how many.
   18. The clock: offset, residuals, anchors per round, and the zero point of tracker.gg's `roundTime` against the
       barrier drop (round 1's longer buy phase included). Freeze the limits (see the gate).
   19. Orientation and assignment without proximity (P-d): exactly one candidate? Which slots are pinned and by
       what? Do the spawn lines and tight clusters agree?
   20. Proposed `riot_subject` backfills and conflicts (reported only).
   21. **(pass 5)** Does competitive decode what Swiftplay didn't: `BombPlayerState` with `Subject`, and
       `BombGameState` with `Phase`, `MatchID` and `RoundResults`? The preview report shows it directly
       (`players.subjects_decoded`, `match_uuid_source`, `round_results_decoded`). Together with finding 15, this
       settles decision 1 and whether winners are checked.
   22. **(pass 5)** Do the Swiftplay observations hold?
       - one pawn per player for the match, or a pawn per round (the grouping by `PlayerState` also handles that);
       - loose round-start positions and tight match-start spawn lines;
       - the map only in the `.vrf`;
       - the header match UUID (W-a);
       - 125 Hz.

       **(pass 6)** Also: any `_PC_C` archetype that isn't a player (drones, pets), and any ownership conflict
       (P-b).
   23. **(pass 5, widened in pass 6)** Is the proof strong enough without decoded winners? Run the gate's
       corruptions with `round_results` emptied, and check each gets its expected outcome.
   24. **(pass 6)** Kills after `RoundEnding`: how many, and does tracker.gg record them? This fixes the window end
       (`t_end`) and whether `t_decided` is needed for matching.
   25. **(pass 6)** Channel lifecycle: every `actor_closed` on a pawn, by `reason`. Are there dormant closes and
       reopens? Is a leaver's close distinguishable from dormancy? Answer this on the Swiftplay export's leaver
       too.
   26. **(pass 6)** Surrender and completeness shape: for any surrendered match in the DB, the awarded score
       against the played `rounds` rows, and what a replay of one shows. This checks P-e's detection rule.
   27. **(pass 6)** Partial-bunch errors by channel and by the evidence they carry (P-f). Do any fall on the
       channel carrying phase RPCs or lifecycle events, and do P-a and P-c still validate?
   28. **(pass 6 review, 2026-09-25)** Revive evidence for Clove's ult. The pinned parser has descriptors for
       a character's `Health` (`AresAttributeSet`, `/Script/ShooterGame.AresAttributeSet`) and for
       `BombPlayerState.bUltimateActive`/`NumUltimatePoints`, but the Swiftplay export carries neither (no
       attribute-set rows; Swiftplay's player state isn't `BombPlayerState`), and the export already ran with every
       category on (`parse_profile: default` = `ExportCategory.All`). On the competitive export: do Clove's
       `bUltimateActive` or `Health` rows appear, and do they mark each undecoded revive? If so, they are the revive
       source P-c needs, and the "second death with no revive" rule needs no Clove exception (the Swiftplay
       finding). If not, decoding the revive is an upstream parser change, and the user chooses among the options
       in the Swiftplay finding.

**Gate** (executable: `scripts/replay_gate.py` or a test module runs every case below and prints the outcomes;
the gate is its output, not a prose checklist):
- The scratch linker accepts the competitive match under the limits, with exactly one candidate, every slot
  pinned, and eligibility clean.
- Deliberately altered copies, each with the **expected outcome** stated before running, with and without decoded
  winners:

  | Corruption | Expected |
  | --- | --- |
  | a shifted round number | refused |
  | two kills swapped by identity (not only within a pairing window) | refused |
  | two kills within the pairing window swapped in order | linked, same mapping |
  | an agent changed | refused |
  | a 3 s clock shift | refused |
  | a 0.5 s clock shift | linked, same mapping, offset shifted by 0.5 s |
  | the wrong match | refused |
  | two opposing same-agent slots made kill-free, spawn evidence crossed | refused (two candidates) |
  | a slot's spawn evidence moved to the other team | refused (proximity disagrees) |
  | one phase `4` removed | refused at condense (P-a) |
  | one phase `5` moved after the next `3` | refused at condense (P-a) |
  | a pawn claimed by a second player state | refused at condense (P-b) |
  | a mid-match dormant close and reopen | linked, the life `unobserved` |
  | a kill by a player after their "left" close | refused at condense (P-c) |
  | the DB's last k rounds deleted, score unchanged | refused (P-e) |
  | a manifest-only `RoundResults` fallback diagnostic | refused (P-f) |
  | coverage cut to 52% for one player | refused (P-f) |
  | the kills truncated to find the smallest prefix that still links uniquely | reported margin |
- **Built (2026-09-25, AFK run): `scripts/replay_gate.py`.** Every row above runs on a synthetic 15-round,
  13-2 competitive match and synthetic DB objects, with and without decoded winners, and the condense-level rows
  also run on a real export (`--export-dir`). On the Swiftplay export: **39 OK, 0 mismatch, 5 waiting for 1b, 1
  finding** (the export itself refuses at condense under P-c, see "Pass-6 findings"; the four condense-level
  corruptions of it still refuse at the check the table names). The truncation margin on the synthetic match: 2
  kills per round with the limits, 1 per round for the assignment alone. What waits for the competitive replay:
  the base link, every row on its data, and the limits.
- **Limits.** Freeze them from **two or more** competitive matches if the user records them. With only one, they
  stay `PROVISIONAL` in the code and are marked "frozen from n=1" here, with the observed offset, residual and
  anchor margins, and they are re-checked on the next few ingests.
- The index rules, diagnostic classes, lifecycle and ownership rules, and JSON v1 (delta encoding, any
  quantisation, `hz`, `t_decided`, `alive` flags) are written here and marked **frozen**, including every
  `PROVISIONAL(Dn)` value in the code (see "Provisional values").
- Decision 1 is closed by findings 15 **and** 21, and migration 0012's `subject` columns and the backfill switch
  follow it.
- Stage 1a's open item is closed: the pass-6 checks on the Swiftplay export (finding 13 is recorded).

**User runs:** the recording, the archive backup, and the crawl (a prod write).

### Stage 1c: tooling PR (fresh branch from `origin/main`, carrying the `replays` plan commits)

**Goal:** the frozen design as tested code on `main`, with no routes, no migration and no worker.

**Files:**
- `webapp/replay_parser.json`, `scripts/build_replay_parser.ps1`, `scripts/export_replay.ps1`
- `scripts/archive_replays.py`:
  - copy to a temp name, hash, rename atomically, append to the archive's `index.json`;
  - a file with the same name and different bytes is kept as `<uuid>.<sha8>.vrf` and reported, never
    overwritten.
- `scripts/with_friends_db.py` (from 1b), `scripts/vendor_map_assets.py` + `static/img/maps/*`,
  `static/data/{maps,agents}.json`
- `app/replays/{__init__,format,contract,condense,link}.py`. `link.py` is pure in this stage and works on objects,
  not the DB.
- `scripts/ingest_replay.py`: `--export-dir` with `--preview [--map]` and `--dry-run` only. A real write raises
  "not until Stage 2".
- `scripts/make_replay_fixture.py` (with P-h's shape checks), `tests/fixtures/replay/` (both matches, trimmed,
  pseudonymised, derived from real gameplay; kept by the user's decision), the unit tests.
  The fixture maker keeps each character's spawn point and `PlayerState`, adds the map from `--vrf` when the
  export names none, and thins movement with `--movement-step` (a round at 125 Hz is tens of MB).
- `.gitignore` additions.
- Script options with environment-variable defaults for the archive and parser paths (not `app/config.py`).
- The pass-6 prerequisites P-a to P-h (required).
- W-a (the structural header parse) and W-b (the streaming condenser, with its parity test), if built before the
  PR. W-b is required before Stage 3 sizes the worker.

**Status (pass 5):** every file above exists on `overnight/2026-09-25-replay-stage1`, reworked to the real
export. The Swiftplay fixture is committed there (`8d37a2c`); the competitive one follows 1b. The branch is based
on `replays`, not `origin/main`: the PR branch is cut fresh and the commits carried over.

**Frozen values (Stage 1b gate, 2026-09-27, n = 6 competitive matches).** Approved as one
group (AFK run decision D7, 2026-09-27); the code no longer marks them provisional.
No value changed at the freeze: the evidence sits well inside every limit, so none needed moving, and
tightening one on six matches would trade a misalignment risk that the gate already shows is covered for new
false refusals.

| Was | Value | Evidence (six matches) | Margin |
| --- | --- | --- | --- |
| D2 | `supported_replay_builds = ["++Ares-Core+release-13.06"]` (`replay_parser.json`) | all six manifests | the only build seen |
| D5 | segment gap 1.0 s; teleport 600 units; linear fill; `hz` = median interval | 125 Hz in all six; largest gap 0.029-0.04 s | 25× under the gap |
| D5 | coverage ≥ 90%, gap ≤ 3 s (eligibility) | 100% coverage, gap ≤ 0.04 s | wide |
| D5 | size budget 60 KB p95 per round, 1.5 MB per match | c4: p95 39.7-51.1 KB, 558-842 KB per match | re-measured with stored util in Stage 2 (R2) |
| D6 | malformed packets block; partial-bunch errors by channel class | 0 malformed; 112-161 per match on the phase channel, all `phase_validated` | n/a |
| D7 | parser version = 12-character SHA prefix | all six | n/a |
| D8 | `RoundResults` index i is played round i + 1 | 137 rounds, every decoded winner equals the DB's | confirmed |
| D9 | blob `plant`/`defuse` null; the page uses the DB's times | no decoded plant RPC | unchanged |
| D10 | slot order by first pawn; side A holds slot 0 | sides resolved in all six | n/a |
| D11 | \|offset\| ≤ 2.0 s, ≥ 20 anchors, residual ≤ 0.75 s, median ≤ 0.25 s; `Red` → team-1 | offset +0.002 s median, residual ≤ 0.015 s, 141-217 anchors | 50× (residual), 7× (anchors) |
| D15 | `PHASE_ENDED_TOLERANCE_MS = 0` | every `Ended` at its `Begin`'s time; a moved 5 refuses | exact |
| D15 | `ROUND_NUMBER_BASE = 0` | no `RoundNumber` decoded at round start | **untested** (nothing to compare) |
| D15 | lifecycle: any non-dormant close with no reopen = left | no player-pawn close in the six | **untested on competitive** (finding 25) |
| D15 | final-round rule, channel classes, completeness per mode, header ASCII-copy rule | all six condense, link and pass P-e; ASCII copies present | legacy surrenders refuse (finding 26) |
| pass 5/6 | spawn radius 1500 units (a check only) | match-start radius 300-612 units | 2.5× |
| (display) | `replay.js` ability radii | tuned by eye | display only |

**Gate:**
- The unit tests pass. Failing test IDs under `.venv313` and `.venv` equal the baseline.
- `ingest_replay.py --export-dir <competitive> --dry-run` (via `with_friends_db.py --read-only`) prints a passing
  report, with eligibility enforced (P-f). The agent can run it.
- The 1b gate script's output is recorded here, with every case at its expected outcome.
- `--preview` on the Swiftplay export passes the scripted checks, and the user looks at it once. 8/8 and the
  user's look were done 2026-09-25. **(pass 6)** Preview also checks, and must pass:
  - round numbering (P-a), with `kills_outside_rounds == 0`;
  - ownership (P-b) and lifecycle contradictions (P-c);
  - coverage and gaps, now enforced on every path (P-f);
  - parse time and memory recorded (finding 13: done).
- The fixture scan passes, with shape checks (P-h). `git ls-files | grep -iE '\.vrf$|valo-replay|events\.ndjson$'` prints nothing outside
  `tests/fixtures/replay/`.
- The user merges. Record the merge commit here as the Stage 1 gate commit.

**User runs:** the merge.

### Stage 2: local ingest + playback (one PR, from `origin/main` after 1c)

**Goal:** the live friends site plays a locally ingested, linked match, and the demo provably shows none.

**Steps:**
1. `alembic/versions/0012_replays.py` + the models:
   - all four tables, with `replays.link_inputs` and the unlink trigger (pass 6);
   - the `subject` columns only as decision 1 closed them (findings 15 and 21), with the backfill switch off
     unless finding 15 passed.

   The schema is then complete for Stage 3.
2. `app/replays/store.py` (advisory lock, dedupe, bulk insert, link in the same transaction, the demo refusal),
   `link.py`'s DB layer, `scripts/link_replays.py`, the real write path in `ingest_replay.py` (`--replace`), and
   `scripts/reingest_replays.py --dry-run`.
3. The best-effort link hook at the end of `ingest_trackergg_player.py` and `refresh_tracked_players.py`. A link
   failure is logged, never fails the crawl.
4. Routes: `app/routers/replays.py` (page, JSON) and the redirect plus `has_replay` in `app/routers/matches.py`,
   all gated on `demo_mode`.
5. `templates/replays/replay.html`, `static/js/replay.js`, and `style.css` for the viewer, both linked and unlinked
   modes.
6. The "Replay" links in `detail.html` and `_round_detail.html`.
7. The DB and route tests.

**Gate (before merge):**
- On a throwaway PG18 cluster (`~/pg18`; Docker doesn't run here):
  1. Load `seed_data/demo_matches.sql` at 0011, plus the competitive match's rows copied read-only from prod with
     `pg_dump --data-only`.
  2. `alembic upgrade head`: every existing table's row count and a checksum of `matches`/`rounds`/`kill_events`
     are unchanged. `downgrade -1` and back up.
  3. Uvicorn against it: `/health` is OK, and match pages render.
- Ingest the competitive match and the Swiftplay test replay into it:
  - the competitive one is `linked`, and play, scrub, round change and feed seeking all work, including at phone
    width;
  - the Swiftplay one plays `unlinked`;
  - a match page without a replay renders the same HTML as on `main`.
- Link-later test: delete the competitive match's `matches` row in the cluster. The replay turns `unlinked`,
  its link fields are cleared, `link_inputs` is unchanged, and no stale link data renders. Restore the rows, run
  `link_replays.py` (which selects `match_id IS NULL`), and it is `linked` again with the same mapping.
- `DEMO_MODE=true` on the same cluster (rows present): the replay routes 404 and no links appear.
- All tests pass. Failing test IDs equal the baseline.

**Pre-merge gate: run 2026-09-27 (AFK run, branch `afk/2026-09-27-replay-2`), every line PASS.** On a
throwaway PG18 cluster at 127.0.0.1:55432 (no prod writes):
- demo seed DB: `seed_data/demo_matches.sql` at 0011 (6 matches, 139 rounds, 1,028 kills), `upgrade head`:
  every existing table's row count and the `matches`/`rounds`/`kill_events`/`players`/`impact_scores`
  checksums unchanged (over their 0011 columns; `players.riot_subject` is all NULL); `downgrade -1` and back
  up, unchanged. PASS 3/3.
- friends-shaped DB: the six competitive matches' rows (36 players, 60 match players, 137 rounds, 1,370 stat,
  spend and Impact rows, 1,008 kills) copied read-only from the friends DB with their own IDs (a Python copy,
  not `pg_dump`), then `upgrade head`: unchanged. PASS 2/2.
- `ingest_replay.py` (the real write path) on all six exports: each `stored`, `linked`, complete, with its
  per-kill split stored after the scorer's read-only rerun reconciled with every stored row.
- link-later: deleting one match's rows unlinked its replay through the trigger (status `unlinked`, offset,
  kill map, DB-only deaths, per-kill split and `linked_at` cleared, every `replay_players.match_player_id`
  NULL, `link_report.unlinked = "match deleted"`, `link_inputs` unchanged); its page context then held no site
  data; restoring the rows and running `link_replays.py` relinked it with the same mapping, offset and kill
  map, and stored its split again. This unlinked replay stands in for the Swiftplay one, which refuses at
  condense (Stage 1a findings).
- `DEMO_MODE=true` with every row present: `/replays/<uuid>`, `/replays/<uuid>/1.json` and
  `/matches/<id>/replay` 404; the match page and the matches list link no replay.
- no replay: a match page, the matches list and `/health` render byte-identical HTML to `origin/main` on the
  same DB (static version strings masked).
- headless Chromium (Playwright), every round of all six replays at 1280 px and 390 px: a drawn canvas, play,
  scrub, a kill-feed jump and the next-round button all work, no console or page error: 12/12.
- tests: failing IDs compared with the `origin/main` baseline in the run's final check.

**Gate (after merge, user-run):**
- The deploy applies 0012 to both DBs. Check `alembic_version` read-only on both. `valomaths.onrender.com` loads
  with no replay links.
- The first prod ingest:
  `.\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\ingest_replay.py --export-dir $env:TEMP\valo-replay\<uuid>`
  (from a PowerShell window in `webapp\`; the `!` prefix runs bash).
  Then the replay plays on `valowithfriendstracker.onrender.com`.

**User runs:** the merge and the first ingest.

### Stage 3: friends upload (one PR + a new Render service)

**Goal:** a friend with the invite code uploads a `.vrf` from the browser and gets a replay page.

**Steps:**
1. `replay_worker/Dockerfile`, `replay_worker/server.py` (standard library HTTP; the job queue, the timeout, the
   memory cap, temp-file cleanup) and `replay_worker/README.md`.
2. `render.yaml`: the `replay-worker` private service with an explicit `plan:`, and `REPLAY_WORKER_URL` on the
   friends service (`fromService`). `REPLAY_UPLOAD_CODE` is `sync: false` (set by hand).
3. `app/routers/replays.py`: the upload form, `POST /replays/upload`, the job page and status, the code check, the
   size/magic/rate limits and the demo 404s. `app/services/replay_upload.py` (the worker client in `urllib`, and
   store-on-completion).
4. The "Upload replay" nav link, shown only when a code is configured and not in demo mode.
5. The worker, upload and route tests.

**Gate:**
- Locally: build the worker image if Docker is available, otherwise run `server.py` with the local parser build.
  Then:
  - upload the competitive `.vrf` → a replay whose blobs are **byte-identical** to the local ingest's (same
    recipe) and that links the same way;
  - upload the Swiftplay `.vrf` → an unlinked replay that plays;
  - a text file, a file over the size cap and an 11th upload in an hour are each refused with a reason;
  - a hung parse is killed at the timeout, and its temp files are gone.
  - the worker's temp directory is empty after every job (decision 8).
- **Local gate run 2026-09-27 (AFK run, branch `afk/2026-09-27-replay-3`):** `replay_worker/server.py` with the
  local parser build took the Summit `.vrf` (60 MB) over HTTP: parse 52.3 s, job 179 s, and all 20 stored
  blobs **byte-identical** to the local ingest's (same recipe, same match UUID from the header); the
  worker's temp folder was empty afterwards. The text-file, over-cap, 11th-upload, one-at-a-time, failed-parse
  and stuck-job refusals, and an unlinked upload that stores and dedupes, are tests
  (`tests/replays/test_replay_upload.py`, `test_replay_worker.py`; the worker's hung-parse kill and temp
  cleanup are the latter's). Not run here: the Docker build (no Docker) and so the container's memory; the
  Swiftplay upload (it refuses at condense, Stage 1a). Limits set from the six files (decision 10):
  181,035,000 bytes, a 240 s parse timeout, 10 uploads an hour, and 20 minutes before a job counts as
  stuck (the plan's 10, doubled for the measured job time); the worker's `plan: 4c-8g` (Pro Plus: 8 GB, 4 CPU; about
  2x headroom at the upload cap) until Render's own measurement. Approved as AFK run decision D10.
- On Render after merge (user):
  - the worker is not reachable from the internet;
  - it has no `DATABASE_URL` or other secrets in its environment;
  - uploading the competitive `.vrf` works end to end and takes about as long as the Stage 1a parse (plus the
    upload itself);
  - the worker's peak memory stays inside its plan;
  - `valomaths.onrender.com/replays/upload` returns 404.
- The user gives the code to friends.

**User runs:** the merge, creating the worker service (a Blueprint sync), setting `REPLAY_UPLOAD_CODE`, and the
first real upload.

### Stage 4: site analysis (one PR)

**Goal:** what valoplant doesn't have: the site's own analysis next to the playback, with no new Impact value
(decision 5).

**Steps:**
1. `app/services/replay_view.py`, read-only, for linked replays:
   - **Playback state** (physical): the alive count per team at any `t` from the blob's `alive` plus the link's
     `db_deaths`, including revives. This drives the live 5v5 → 4v5 badge.
   - **Analytical annotations** from `state_replay.replay_round`, shown only for rounds it includes. Excluded
     rounds (equal-time transitions, ambiguous lifecycle; `state_replay.py:247-251,311-316`) show the reason.
     Post-decision events are marked as such (`:281-284`), and replay kills after the blob's `t_decided` are
     post-decision too (pass 6). Defuse, detonation and time-out endings sit at their
     real DB time, converted with the clock offset, not at the last kill entry (`:348-352`).
   - The round's `impact_scores` rows (kill/death/total per player): the same numbers as the round detail, shown
     and never recomputed.
2. The viewer: the badge, the annotation panel and per-player round Impact.
3. `scripts/reingest_replays.py` write mode for local replays (user-run through `with_friends_db.py`). Stale
   uploads are listed with "re-upload to refresh".
4. Optional: flash, nearsight and wall `util` kinds (no `v` bump; a `CONDENSE_REVISION` bump; W-e).

**Not in this pass:** a win-probability line (`win_probability.py` is a
between-round match-win model with no alive-count input that must be cross-fitted; `win_probability.py:1-15,36-59`),
and trade links (the round page has none).

**Gate:**
- Badge tests: a revive, a DB-only death, an equal-time double kill, a post-decision kill, and a plant/defuse with
  no kill in between.
- Annotations equal `replay_round`'s output for every included round of the fixture match, and every excluded
  round shows its reason.
- Round Impact equals `get_round_detail`'s numbers for every round.
- **No scoring change:** `git diff origin/main -- webapp/app/scoring` is empty.
- **No new Impact calculation:** a test asserts that nothing under `app/replays/`, `app/routers/`,
  `replay_view.py`, `replay_upload.py` or `replay_worker/` imports `app.scoring.impact`, `kill_order_leverage` or
  `win_probability`. The transitive import of the unchanged `app.scoring.plant_window` through `state_replay`
  (`state_replay.py:51`) is expected. **(amended 2026-09-27, decision 5)** The one exception is
  `app/services/replay_impact.py`, the per-kill split: it may import only `build_impact_rows_for_match`,
  `PERSISTED_FIELDS`, `FormulaWeights` and `impact_runtime`'s `active_scoring_config`/`active_manifest`, and
  never `compute_impact_for_match`, a commit or an `add`; a test enforces both. Every HASHED_SOURCES path is
  unchanged against `origin/main`.
- `reingest_replays.py --dry-run` lists nothing after a fresh ingest. It lists the local replay after a
  `CONDENSE_REVISION` bump, refuses it after the archive file is corrupted, and lists uploads as "re-upload".

**Gate run 2026-09-27 (AFK run, branch `afk/2026-09-27-replay-4`), every line PASS:**
- badge tests: a revive, a DB-only death (counted once, on the replay clock), an equal-time double kill (one
  step), a post-decision kill, a plant/defuse with no kill between (`tests/replays/test_replay_view.py`);
- annotations equal `replay_round`'s output for every round of the synthetic match, excluded rounds show
  their reason, a defuse ending sits at its DB time; on the six real matches every round has badge steps from
  5v5 and annotations (7 of 137 rounds excluded: 6 ambiguous lifecycles, 1 equal-time pair), and on all 127
  comparable rounds the badge's end state equals the annotations';
- round Impact on the page equals `get_round_detail`'s for all 137 rounds of the six matches;
- no scoring change and the amended import gate (decision 5): tests, and the diff checks in the run's final
  step;
- `reingest_replays.py`: nothing listed after a fresh ingest, a stale recipe listed and re-ingested from its
  export, a changed archive file refused, uploads listed as "re-upload";
- headless Chromium on two matches at both widths: the badge reads "N v M" in every round, the Analysis tab
  lists states or the reason, no console error (this found and fixed a helper that shadowed the viewer's
  `aliveAt`).

**User runs:** the merge and any prod re-ingest.

### After this pass
- Per-kill Impact beyond the display split of decision 5 (amended 2026-09-27): any new per-kill value is a scoring-process decision.
- Smoke overlays once upstream exports them.
- A historical "rounds won from this state" rate beside the badge. It must be labelled as a historical rate over a
  stated population, with a minimum sample, and gets its own gate.
- Trade links, with a read-only derivation, an exact window and tie rule, kill IDs, and tests.
- Heatmaps and positioning stats across many rounds, as `/stats` cards.
- Choosing between two friends' recordings of the same match (their views can differ).

## Upload decisions (settled 2026-09-25)

8. **Uploaded `.vrf` files are not kept.** Once the worker has parsed and condensed a file, it is deleted. Only
   the condensed timeline is stored, which keeps Render storage small. The consequence, accepted: an upload can't
   be re-parsed after a parser upgrade. It keeps playing under its stored `v`, and the friend can re-upload.
   (This covers uploads only. The user's local archive stays under decision 7, because it's the only way to
   re-parse the user's own matches.)
9. **Worker plan:** whatever Render plan works best for this project is pre-approved. Pick the smallest plan
   whose RAM is at least 2× the peak parse memory measured in the worker container in Stage 3 (Stage 1a's Windows
   figure is only an early estimate). Write it into `render.yaml`'s `plan:`.
10. **Upload limits:** start with 80 MB per file, 10 uploads per hour per session and IP, and a 180 s parse
   timeout. They are checked against real data before Stage 3 ships:
   - Stage 1a records the `.vrf` sizes (the Swiftplay test file is 23 MB, a match of about 14 minutes) and the parse time
     (24.9 s and 147 MB peak on Windows; finding 13);
   - Stage 1b adds the competitive file;
   - the size cap becomes max(80 MB, 2× the largest observed file), and the timeout becomes max(180 s, 3× the
     slowest observed parse in the worker);
   - revisit both after the first real uploads.

## Risks

- **Patch lag:** each Valorant patch needs an upstream payload transform. Local matches wait in the archive.
  Uploads get "patch not supported yet, try again after an update" and must be re-uploaded later.
- **Alpha parser:** field handles shift (`RoundResults` moved 93→82 at 13.05), and game state is marked unfinished
  upstream. The parser is pinned, the contract checks the manifest, and linking refuses on drift.
- **Untrusted uploads:** parsing files from other people. The worker is isolated, private, holds no secrets, runs
  as non-root with a timeout and a memory cap, and handles one job at a time. The code is only as strong as the
  friends who hold it. It can be rotated.
- **Client-side recording:** the recorder's own pawn or enemies hidden by fog of war may be missing. Measured on
  the Swiftplay replay: no gaps (all 10 at 125 Hz, 100% coverage). Stage 1b checks competitive.
- **Game-state decoding (pass 5):** the parser doesn't decode Swiftplay's player and game state, so that replay
  has no Subjects, winners or MatchID. If competitive is the same, identity rests only on linking. Kill-free
  players with the same agent, or a composition with really symmetric kills, can't be told apart. **(pass 6)**
  They are refused, not misaligned, because proximity no longer removes candidates (P-d). Decoding more is an
  upstream parser change.
- **Evidence that looks harmless (pass 6):** a lost controller-channel RPC, a dormant channel close, or a pawn
  ownership change can change rounds or lives without leaving a gap in any track. The condenser now validates
  phase cycles, lifecycles and ownership (P-a to P-c) and refuses what it can't explain, so more replays may
  refuse at condense. Each refusal is reported with its reason.
- **Export size (pass 5):** 1.5 GB of NDJSON per 14-minute match. Local exports go to `%TEMP%` and can be deleted
  after ingest; the worker needs the disk and a streaming condenser.
- **Cost:** a new paid Render service (decision 9).
- **Coverage:** only matches someone recorded and ingested or uploaded. "No replay" is the normal state. Strict
  linking means some real matches stay unlinked, with the reason shown.
- **Riot policy and privacy:** only derived minimap positions are stored, never the files, and no names or
  Subjects go in the blobs. The site is public and zero-auth, so replays show teammates' and opponents'
  positions, and uploads can include matches with strangers. Check again before the Riot production-key
  application.
- **Public repo:** `*.vrf` and export output are gitignored, fixtures are allowlisted, shape-checked and scanned,
  and secrets live only in the Render dashboard and gitignored `.env*`. **(pass 6)** Fixtures from real matches
  are pseudonymised, not anonymous. The repo's docs can reconnect them to their public tracker.gg match, and the
  user accepted that.
