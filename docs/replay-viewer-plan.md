# 2D replay viewer: plan

Goal: play back a round on the minimap in the browser, like valoplant.gg's replay page, using Valorant's own
`.vrf` replay files parsed with [michel-giehl/ValorantReplayParser](https://github.com/michel-giehl/ValorantReplayParser),
and tie it to the match and round pages this site already has.

This is the fourth pass.
- **Pass 1** (2026-09-25) was written before anyone had read the parser's source.
- **Pass 2** read that source (upstream `2b66c65`, 2026-09-23), the prior-art replayer and this repo's code.
- **Pass 3** applied two independent reviews of pass 2: the exporter contract, the linking rules, demo
  isolation, fixture sanitising and freshness.
- **Pass 4** (this one) applies the user's decisions of 2026-09-25 (see Decisions). The main change: **replays
  come in two ways**, a local ingest from the user's PC and a **friends-only upload** parsed on a private Render
  worker. An uploaded replay plays even if its match isn't in the DB, and links itself later if the match is
  crawled.

The test replay's export still hasn't run, so every claim that needs it is marked **[export]**, and each one has
a stated failure rule.

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
  │                 → per-round JSON v1 blobs on the replay's own clock + a private player table (Subject, agent, side group)
  ▼
Store               app/replays/store.py: one locked transaction → replays, replay_rounds, replay_players
  │                 local: scripts/ingest_replay.py via with_friends_db.py        upload: the web service, after the worker returns
  ▼
Linker              app/replays/link.py: if a matches row has external_id == the replay's MatchID, prove the
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

In auto mode the agent may not build or run the third-party parser, but it can run the condenser, linker and
tests on an export that already exists. So `ingest_replay.py` never calls the parser: `scripts/export_replay.ps1`
(run by the user) does, and `ingest_replay.py --export-dir <dir>` reads its output.

## Decisions (settled 2026-09-25)

1. **Where the replay's `Subject` goes:** a new column `players.riot_subject` (UUID, unique, nullable). It is
   backfilled only under the rules in Linking step 6. `players.puuid` is left for the Riot API, whose PUUID is
   expected to be a different, encrypted string.
2. **The friends DB** is `valowithfriendsdb` (read from `webapp/.env.remote`; only the name was printed). This is
   the value for `with_friends_db.py --expect-database`.
3. **Parser location:** a pinned build used in two places, the user's PC for local ingest and a private Render
   Docker service (`replay-worker`) for uploads. Uploads are **friends-only** (an invite code). An upload of a
   match the DB doesn't have is **stored and playable**, and links itself once the match is crawled. **Both**
   local ingest and upload are built. Local ingest ships first because it proves the shared code.
4. **ValoMaths demo:** no replays, and no upload. Enforced in code.
5. **Impact:** the replay never affects scoring. The page *shows* the stored per-player round Impact. Per-kill
   Impact is out of scope for this pass.
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
- In auto mode, the agent may not build or run the third-party parser. The user runs the build and export
  commands.
- **Python dependencies:** the tracker.gg ingest's preflight refuses an environment whose installed packages
  differ from the frozen scoring manifest (`ingest_preflight.py:100-115`). So nothing here adds a package to
  `requirements.txt`. The web service talks to the worker with the standard library (`urllib.request`), and
  `python-multipart` (already pinned) handles the upload.

### From the parser source (done, to be confirmed by the export)

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

### Exporter contract (what the condenser relies on)

- **Clock:** `time_ms` on every event and movement row, the replay clock in ms. The raw movement `timestamp` is
  kept for diagnosis only.
- **Order:** file order, then `time_ms`. Rows with equal `time_ms` keep file order.
- **State:** export-group rows are partial. State at time t is the fold of every earlier update for that actor.
- **Manifest:** the condenser requires `schema_version == 8`, `source_sha256` equal to the input file's hash,
  `replay_build` in the pinned parser's supported list, and `parser_version` containing the pinned SHA. Any
  mismatch refuses.
- **Diagnostics**, classified in `condense.py`:
  - *blocking:* `RawPayloadFallback` on `RoundResults`, any diagnostic on `BombPlayerState`, and
    `malformed_packet_count` or `partial_error_count` above a threshold frozen at Stage 1a. For a replay with no
    DB match, a `RoundResults` fallback is not blocking: it just can't link;
  - *coverage:* movement decode errors, which count against the per-player coverage limit;
  - *ignored:* everything else, listed by code in the report.
  An unknown code is blocking until someone classifies it.

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
    `team-1`/`team-2`.
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
    measured in Stage 1a. Samples are snapped to the grid, never invented beyond the source's own rate.
  - A new segment starts at a gap over 1.0 s, a jump over 600 world units between raw moves (teleport; frozen at
    Stage 1a), a death, a revive or a pawn change.
  - Nothing is drawn between segments. The page shows a hollow last-known marker for 2 s, then hides the dot.
- **Coordinates:** `u`/`v` are ints in 0..10000. `yaw` is already projected into map space (a unit vector through
  the same affine transform), an int in degrees 0..359, interpolated along the shortest arc.
- **`alive`:** intervals `[from, to, cause]` per slot. `to` is null if they survive. `cause` is `"kill"` (a replay
  kill) or `"round_end"`. A new interval opens on a resurrect event (`MulticastReceivePlayerResurrectEvent`) or a
  new pawn for the same Subject. For a linked replay, DB-only deaths (the excluded kill classes) are overlaid at
  page time from the link's `db_deaths` list.
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
  match_uuid       uuid unique not null      -- BombGameState.MatchID
  match_id         int null unique  FK matches.id ON DELETE SET NULL
  map_name         varchar(64) not null
  round_count      smallint not null
  format_version   smallint not null
  recipe           varchar(80) not null
  game_branch      varchar(64) not null      -- manifest.replay_build
  source_sha256    char(64) not null         -- of the .vrf
  source           varchar(8) not null       -- 'local' | 'upload'
  link_status      varchar(12) not null      -- 'linked' | 'unlinked' | 'refused'
  link_report      jsonb null                -- ids, counts, reasons; no names, no Subjects
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
  subject          uuid not null
  agent            varchar(32) not null
  side_group       char(1) not null          -- 'A' | 'B'
  match_player_id  int null FK match_players.id ON DELETE SET NULL
  PRIMARY KEY (replay_id, slot), UNIQUE (replay_id, subject)

replay_uploads                               -- upload jobs
  id               uuid PK
  status           varchar(12)               -- 'queued' | 'parsing' | 'stored' | 'failed'
  error            text null                 -- a user-facing reason
  source_sha256    char(64), size_bytes int, created_at, finished_at
  replay_id        int null FK replays.id ON DELETE SET NULL

players.riot_subject  uuid null unique       -- decision 1
```

- Separate tables, so existing match and round queries never load blobs.
- Deleting a crawled match sets the replay back to unlinked (`SET NULL`). Nothing is lost.
- **Writes** go through `store.py`, one transaction per replay:
  1. `pg_advisory_xact_lock(hashtext(match_uuid))`, which serialises concurrent stores and links of one replay.
  2. An existing `match_uuid` with the same `source_sha256` and recipe → no-op. With a different sha (for example
     a second friend's recording of the same match) → keep the existing one and report it. Local `--replace`
     overrides this, and uploads never do.
  3. Insert or replace `replays` + one multi-row `INSERT` each for `replay_rounds` and `replay_players`.
  4. Run the linker inside the same transaction (below).
  5. Commit. Any failure rolls it all back and the old rows survive.
  That's about ten round trips at ~65 ms each, not one per row.
- **Linking later** (`link.py`, its own locked transaction, called by `store.py`, by
  `scripts/link_replays.py`, and best-effort at the end of `ingest_trackergg_player.py` and
  `refresh_tracked_players.py` for every newly ingested `external_id` that has an unlinked replay). It writes only
  `replays.match_id`, `clock_offset`, `kill_map`, `db_deaths`, `link_status`, `link_report`,
  `replay_players.match_player_id` and the `riot_subject` backfill. **It never rewrites a blob.**
- No cache invalidation is needed. Replays feed no cache and **no Impact scoring**, and nothing touches
  `app/scoring/`.

### Linking (refuse rather than misalign)

`link.py` takes the stored replay (blobs + `replay_players`) and the DB rows, and either writes a mapping or
records `link_status = 'refused'` with the first failing check in `link_report`. The replay itself stays playable
either way. A refused link never shows site data. The report holds IDs, counts, agents and reasons only.

1. **Match.** Exactly one `matches` row with `external_id == match_uuid` (lower-cased). None → stay `unlinked`
   (the normal case for an upload of an unknown match). **No fallback lookup** by map, time or roster.
2. **Rounds.**
   - *Replay played rounds:* the stored rounds, 1..`round_count`. `round_count` excludes a final round with no
     `RoundEnding`, which the condenser drops and reports.
   - *DB played rounds:* `rounds` minus `is_surrender_round` (`state_replay.py:143`). The adapter already drops
     tracker.gg's padding (`trackergg_browserstate_source.py:679-689`).
   - A dropped final replay round is allowed only if the DB rounds end in a surrender or the DB score shows the
     match ended early.
   - The **sets of round numbers** must be equal. Every round needs a decoded `RoundResults` winner (kept in the
     private `link_inputs` of the condensed output, see below) equal to `rounds.outcome`'s winner. The final score
     must equal `team1_rounds_won`/`team2_rounds_won`.
3. **Teams and players.**
   - Exactly 10 distinct Subjects, one agent each.
   - *Partition:* built from the replay kills that survive the kill rules (killer and victim on opposite sides),
     checked against every round's start positions (each round's two spawn clusters = the two side groups). It
     must be connected and 5/5. Team membership never changes across halftime or overtime.
   - *Assignment:* try both orientations (A = `team-1`/Red or `team-2`/Blue; `trackergg_browserstate_source.py:
     554-555`) × every agent- and team-respecting bijection from slots to `match_players`. A candidate must match
     the per-round winners (`WinningTeam` FName, expected `Red`/`Blue` **[export]**), the ordered kills (step 4),
     and every existing `players.riot_subject` anchor.
   - **Exactly one** candidate must survive. Zero or more → `refused`. A fully mirrored composition with no
     decoded winners is the expected "more than one" case.
4. **Kills, per round.**
   - DB kills minus the excluded classes: `killer_match_player_id IS NULL` (`trackergg_browserstate_source.py:
     750`), killer == victim, and same-team pairs. Excluded deaths go into `db_deaths`, and nothing else may be
     unmatched.
   - Both sides are sorted by time. Kills within 0.1 s form a tie group, compared as a multiset. The (killer,
     victim) sequences must be identical. Identity and order are matched first, the clock second.
5. **Clock.**
   - One match-wide offset: the median of `db_time − replay_time` over every matched kill, plus DB plant and
     defuse times.
   - Provisional limits, frozen at the Stage 1b gate: |offset| ≤ 2.0 s, ≥ 20 anchors, every residual ≤ 0.75 s,
     median |residual| ≤ 0.25 s.
   - Every round needs one anchor within the limit. No per-round fitting.
6. **Backfill** of `players.riot_subject`, the last statement, rechecked under lock (`FOR UPDATE`):
   - each linked player's `riot_subject` is NULL or already equal;
   - the incoming Subject belongs to no other player row.
   Either conflict → `refused` for the link (the stored replay stays, unlinked). No substitution, no guessed merge.

The condenser also outputs a private `link_inputs` record per replay: the decoded `RoundResults` per round,
start-of-round positions per slot, and the replay kill list in slot terms. It's stored in `replays.link_report`
under a `inputs` key (no Subjects), so a later link needs no parser.

Swiftplay and other non-competitive modes never link, and that's fine: they play unlinked.

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
  - The worker holds **no DB credentials and no secrets**. It only parses.
- **The web service** then calls `store.py` (store + link in one transaction) and marks the job `stored` or
  `failed` with a plain reason ("patch 13.07 not supported yet", "not a Valorant replay", "map not recognised",
  "parse failed"). A job stuck in `parsing` for over 10 minutes (for example after a worker restart) becomes
  `failed: please re-upload`.
- **Uploaded `.vrf` files are not kept** (decision 8). An upload can't be re-parsed after a parser upgrade;
  it keeps playing under its stored `v`, and the page offers a re-upload.
- `render.yaml` gets the `replay-worker` service (type `pserv`, runtime `docker`, `dockerfilePath:
  replay_worker/Dockerfile`, an explicit `plan:` sized from the Stage 1a memory measurement, because a Blueprint
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
  `Content-Encoding: gzip`, `Content-Type: application/json`, `ETag: "<recipe>-<n>"` and `Cache-Control: private,
  no-cache` (a cheap `304`, and a replacement is seen at once). There's no `GZipMiddleware` (`main.py:28-33`). If
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
  - the script scans its output against the original export's identities and refuses on any hit.
  - A committed test fails on any UUID outside the synthetic set, any `C:\Users`/`%USERPROFILE%` path, and any
    `Name#TAG` pattern.
- Findings written into this plan: counts, rates, sizes and synthetic rows only.
- `REPLAY_UPLOAD_CODE` and the DB URLs live only in the Render dashboard and gitignored `.env*` files. The worker
  image contains no secrets.

### Tests

- **Parser:** see the Stage 1a baseline.
- **Repo:** run under `.venv313` and `.venv`, comparing **sets of failing test IDs** against a baseline recorded on
  `origin/main` at the start of Stage 1c.
- **Unit tests** (`tests/replays/`):
  - *exporter contract:* folding, ties, manifest checks, diagnostic classes;
  - *rounds:* index base, missing `RoundEnding` (surrender vs. not), equal counts with different keys, a short
    match, halftime, overtime;
  - *tracks:* segments, gaps, teleports, death, revive, pawn change, native-rate grid, delta encoding, yaw
    projection, u/v against two known points;
  - *linking:* the happy path, a duplicate agent across teams, **a fully mirrored composition**, a disconnected
    partition, a spawn cluster that disagrees, each excluded kill class, an unexpected unmatched death, a tie
    group, offset and residual limits, a round with no anchor, no DB match (stays unlinked), a later link after a
    crawl;
  - *backfill:* both conflict directions leave the replay stored but unlinked, with no partial writes;
  - *format:* round trip, size budget, unknown `util.k`, every supported `v` decodes;
  - *fixture scanner:* catches a planted identity.
- **DB tests** (throwaway PG18 cluster):
  - a store with a forced failure keeps the old rows;
  - a same-sha re-store is a no-op, and a different-sha store keeps the first;
  - concurrent stores and links of one replay serialise;
  - deleting a match sets the replay back to unlinked;
  - an incomplete round set reads as "no replay".
- **Route tests:**
  - 404s and no links in demo mode *with rows present*;
  - the gzip headers **and** the decoded body; `ETag`/`304`; a new body after a replacement following a cached
    fetch;
  - links on both the initial and an htmx-loaded round detail;
  - unlinked pages show no site data;
  - upload: 403 without the code; size, magic and rate-limit refusals; 404 in demo mode.
- **Worker tests:** the timeout kills a hung parse; temp files are gone after success and failure; a queue
  overflow → 503.

## Stages

| Stage | Outcome | Touches prod? | Blocked on |
| --- | --- | --- | --- |
| 1a. Parse + render feasibility | Evidence that the Swiftplay replay parses into 10 correct tracks, rounds and kills; parse time and memory measured (scratch only) | No | The export (user) |
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
2. The user builds and tests the parser:
   ```
   ! cd $HOME\rp\parser; dotnet build ValorantReplayParser.sln -c Release; if ($?) { dotnet test ValorantReplayParser.sln -c Release --no-build --logger "trx;LogFileName=$HOME\rp\parser-tests.trx" }
   ```
   A failed build stops Stage 1a. The agent reads the `.trx` and records the **exact set** of failing tests as the
   baseline. Only tests that need a missing `.vrf` fixture may be in it.
3. The user exports, measuring time and peak memory for the worker's sizing:
   ```
   ! cd $HOME\rp\parser; $p = Start-Process dotnet -ArgumentList 'run','-c','Release','--no-build','--project','src\CliReader\CliReader.csproj','--','export',"$HOME\ValorantReplayArchive\d45b2844-d7dd-4efd-bbf7-551854710350.vrf",'--output',"$env:TEMP\valo-replay\d45b2844" -NoNewWindow -PassThru; $h = $p.Handle; $peak = 0; while (-not $p.HasExited) { $p.Refresh(); $peak = [Math]::Max($peak, $p.PeakWorkingSet64); Start-Sleep -Milliseconds 200 }; "exit $($p.ExitCode) seconds $(($p.ExitTime - $p.StartTime).TotalSeconds) peakMB $([int]($peak/1MB))"
   ```
   (`dotnet run` launches a child process, so this peak is a lower bound. Stage 3 measures the real value in the
   worker container.)
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

**Gate (yes/no; any "no" stops and is reported):**
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

**User runs:** the build/test and export commands.

### Stage 1b: linking feasibility (branch `replays`, scratch + read-only prod)

**Goal:** prove on real data that the linking rules accept the right match and could only refuse, never
misalign. Then freeze the numbers and JSON v1.

**Steps:**
1. The user records a competitive match, archives it (copy, compare sha256), backs it up, and exports it as in
   1a step 3.
2. `scripts/with_friends_db.py` is written now (the one early repo file), and the user crawls:
   ```
   ! cd webapp; .\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\ingest_trackergg_player.py "NPrightdolphin#NA1" --count 20
   ```
3. The agent runs read-only checks (`with_friends_db.py --read-only`) and a scratch linker, and answers:
   14. `MatchID == matches.external_id`? (If not, stop. A fallback lookup is its own design.)
   15. Are Subjects real (not anonymised) in competitive?
   16. The round index mapping, the winner FName values, and set equality.
   17. The kill sequences under the exclusion rules: which classes, and how many.
   18. The clock: offset, residuals, anchors per round. Freeze the limits.
   19. Partition, orientation, assignment: exactly one candidate?
   20. Proposed `riot_subject` backfills and conflicts (reported only).

**Gate:**
- The scratch linker accepts the competitive match under the frozen limits, with exactly one assignment
  candidate.
- Deliberately corrupted copies are each refused: a shifted round number, two kills swapped, an agent changed, a
  3 s clock shift, the wrong match.
- The limits, index rules, diagnostic classes and JSON v1 (delta encoding, any quantisation, `hz`) are written
  here and marked **frozen**.

**User runs:** the recording and archiving, the export, the crawl.

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
- `scripts/make_replay_fixture.py`, `tests/fixtures/replay/` (both matches, trimmed, synthesised), the unit tests.
- `.gitignore` additions.
- Script options with environment-variable defaults for the archive and parser paths (not `app/config.py`).

**Gate:**
- The unit tests pass. Failing test IDs under `.venv313` and `.venv` equal the baseline.
- `ingest_replay.py --export-dir <competitive> --dry-run` (via `with_friends_db.py --read-only`) prints a passing
  report. The agent can run it.
- `--preview` on the Swiftplay export passes the scripted checks, and the user looks at it once.
- The fixture scan passes. `git ls-files | grep -iE '\.vrf$|valo-replay|events\.ndjson$'` prints nothing outside
  `tests/fixtures/replay/`.
- The user merges. Record the merge commit here as the Stage 1 gate commit.

**User runs:** the merge.

### Stage 2: local ingest + playback (one PR, from `origin/main` after 1c)

**Goal:** the live friends site plays a locally ingested, linked match, and the demo provably shows none.

**Steps:**
1. `alembic/versions/0012_replays.py` (all four tables + `players.riot_subject`, so the schema is complete for
   Stage 3) + the models.
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
- Link-later test: delete the competitive match's `matches` row in the cluster, and the replay turns
  `unlinked`. Restore the rows, run `link_replays.py`, and it is `linked` again with the same mapping.
- `DEMO_MODE=true` on the same cluster (rows present): the replay routes 404 and no links appear.
- All tests pass. Failing test IDs equal the baseline.

**Gate (after merge, user-run):**
- The deploy applies 0012 to both DBs. Check `alembic_version` read-only on both. `valomaths.onrender.com` loads
  with no replay links.
- The first prod ingest:
  `! cd webapp; .\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\ingest_replay.py --export-dir $env:TEMP\valo-replay\<uuid>`
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
     Post-decision events are marked as such (`:281-284`). Defuse, detonation and time-out endings sit at their
     real DB time, converted with the clock offset, not at the last kill entry (`:348-352`).
   - The round's `impact_scores` rows (kill/death/total per player): the same numbers as the round detail, shown
     and never recomputed.
2. The viewer: the badge, the annotation panel and per-player round Impact.
3. `scripts/reingest_replays.py` write mode for local replays (user-run through `with_friends_db.py`). Stale
   uploads are listed with "re-upload to refresh".
4. Optional: flash, nearsight and wall `util` kinds (no `v` bump).

**Not in this pass:** per-kill Impact (decision 5), a win-probability line (`win_probability.py` is a
between-round match-win model with no alive-count input that must be cross-fitted; `win_probability.py:1-15,36-59`),
and trade links (the round page has none).

**Gate:**
- Badge tests: a revive, a DB-only death, an equal-time double kill, a post-decision kill, and a plant/defuse with
  no kill in between.
- Annotations equal `replay_round`'s output for every included round of the fixture match, and every excluded
  round shows its reason.
- Round Impact equals `get_round_detail`'s numbers for every round.
- **No scoring change:** `git diff origin/main -- webapp/app/scoring` is empty.
- **No new Impact calculation:** a test asserts that nothing under `app/replays/`, `replay_view.py`,
  `replay_upload.py` or `replay_worker/` imports `app.scoring.impact`, `kill_order_leverage` or
  `win_probability`. The transitive import of the unchanged `app.scoring.plant_window` through `state_replay`
  (`state_replay.py:51`) is expected.
- `reingest_replays.py --dry-run` lists nothing after a fresh ingest. It lists the local replay after a
  `CONDENSE_REVISION` bump, refuses it after the archive file is corrupted, and lists uploads as "re-upload".

**User runs:** the merge and any prod re-ingest.

### After this pass
- Per-kill Impact (a scoring-process decision).
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
   - Stage 1a records the `.vrf` sizes (the Swiftplay test file is 23 MB) and the parse time;
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
- **Client-side recording:** the recorder's own pawn or enemies hidden by fog of war may be missing. The Stage 1a
  gate measures this, and a fail is a no-go.
- **Cost:** a new paid Render service (decision 9).
- **Coverage:** only matches someone recorded and ingested or uploaded. "No replay" is the normal state. Strict
  linking means some real matches stay unlinked, with the reason shown.
- **Riot policy and privacy:** only derived minimap positions are stored, never the files, and no names or
  Subjects go in the blobs. The site is public and zero-auth, so replays show teammates' and opponents'
  positions, and uploads can include matches with strangers. Check again before the Riot production-key
  application.
- **Public repo:** `*.vrf` and export output are gitignored, fixtures are allowlisted and scanned, and secrets live
  only in the Render dashboard and gitignored `.env*`.
