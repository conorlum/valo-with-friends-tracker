# Handoff prompt: design the 2D replay viewer

Paste the prompt below into a fresh Claude Code session started in this repo, on branch `replays`.

---

You are designing (not yet building) a 2D round-replay viewer for this repo's friends site, in the spirit of
valoplant.gg's replay page (https://valoplant.gg/60D7CD): a round plays back as dots on the map's minimap in
the browser. Your deliverable is a detailed, implementation-ready design and phased plan that a later session can
execute step by step. Work on branch `replays`; do not merge anything or touch production data.

## Read first

1. `CLAUDE.md`, the two-sites model (friends site vs. the ValoMaths demo, which must never show scraped
   tracker.gg data), the public-repo rule (never commit a credential or large binary), the Docker Compose
   project-name gotcha, and the tracker.gg ingestion pipeline section.
2. `docs/replay-viewer-plan.md`, the first-pass plan. It is a starting point, not a decision. Keep what holds
   up, change what your research disproves, and say which you did.
3. The parser: https://github.com/michel-giehl/ValorantReplayParser (C#, .NET 10, MIT, alpha). Clone it
   **outside the repo** (use your scratchpad). Read `README.md`, `src/CliReader/JsonExport/*`,
   `src/Replay.Valorant/Movement/*`, `src/Replay.Valorant/GameState/BombGameStateDescriptor.cs`,
   `BombPlayerStateDescriptor.cs`, `AresRoundResults.cs` and `src/Replay.Valorant/Combat/*`.
4. Prior art, for reference only: https://github.com/talhakoek/ValorantWebReplayer, a 2D web replayer built on a
   patched copy of this parser. Look at how it maps world coordinates to the minimap and gets ability locations.
5. The site's existing match and round pages: `webapp/app/routers/matches.py` and
   `webapp/app/templates/matches/` (Jinja, no JS build step), plus the models in `webapp/app/models/` (in
   particular `matches.external_id`, `match_players`, `rounds`, `kill_events` and `impact_scores`).

## The test replay

- `%USERPROFILE%\ValorantReplayArchive\d45b2844-d7dd-4efd-bbf7-551854710350.vrf` (23 MB, game branch
  `++Ares-Core+release-13.06`, which the parser registers; sha256 `ca8d3f2e…a9553`, full value in the archive's
  README.txt). Read from the archive copy, not `%LOCALAPPDATA%\VALORANT\Saved\Demos`, because the game client
  deletes old replays there.
- It is a **Swiftplay** match: use it only as a proof of concept for parsing and rendering. Don't crawl it or
  link it to the DB. Swiftplay's short halves mean its round and side logic isn't representative of competitive.
- The **linking fixture** is a competitive replay the user will record and archive into the same folder. Check the
  archive's README.txt for it. If it's there, use it for every linking question. It must be crawled into the
  friends DB before linking can be tested, and the crawl writes to prod, so **ask the user to run it**:
  `.\.venv\Scripts\python.exe scripts\ingest_trackergg_player.py "NPrightdolphin#NA1" --count 5` from `webapp/`
  (it needs `scripts\launch_trackergg_chrome.ps1` running). If the competitive replay isn't there yet, do all
  the parser and rendering work on the Swiftplay file and leave the linking questions open.

## Phase 0 (do this, it grounds the design)

1. The machine had only the .NET 6 runtime. Check `dotnet --list-sdks`. If there's no .NET 10 SDK, tell the user
   what to install and stop until it's there. Don't install system software yourself.
2. Build the parser, run its tests, then run
   `dotnet run --project src\CliReader\CliReader.csproj -- export <test replay> --output <scratchpad>\export`.
3. Answer each of these with evidence from the export: row counts, sample rows, and small Python analysis scripts
   in your scratchpad:
   - Did it parse cleanly? Report the status and any diagnostics.
   - Movement sample rate per player, gaps, and behaviour after death.
   - How do you map `shooter_character_net_guid` to a player? Try `BombPlayerState.Subject` (PUUID) plus
     `SpawnedCharacter`/`PossessedCharacter`, and check whether character GUIDs change between rounds.
   - Where are the round boundaries? Check `BombGameState.RoundNumber`/`Phase`, whether `RoundResults` is
     populated (the README marks Game State as unfinished), and what the fallback is if not.
   - Kills: can each `MulticastNotifyKilledEnemy` be resolved to killer and victim players with times? Do they
     line up with `kill_events` for the same match (once it's crawled)?
   - Is `BombGameState.MatchID` the same as the file name, and does it equal `matches.external_id` once crawled?
   - Minimap transform: fetch the map's `xMultiplier`, `yMultiplier`, `xScalarToAdd` and `yScalarToAdd` from
     valorant-api.com `/v1/maps`. Check that round-start positions land in the correct spawn areas.
     Render one static PNG or HTML check in your scratchpad and show it to the user.
   - Utility: which of the flash, nearsight, wall and smoke events are usable today?
4. Measure the size of a condensed per-round timeline at 4, 8 and 16 Hz, then pick one and justify it.

## Design questions the plan must settle

- **Storage:** table shape (the draft is migration `0012` `match_replay_rounds`, one gzipped JSON blob per
  round), the JSON format version, and how to re-ingest when the parser improves.
- **Linking:** match lookup, and player linking. The tracker.gg adapter never fills `players.puuid`, so the
  draft matches on team + agent. Decide whether to backfill `puuid`, and what happens on any mismatch. Refuse
  to store rather than misalign.
- **Local tooling:** archive script, ingest script (`--dry-run`), pinning the parser version, where the parser
  lives relative to the repo (submodule, separate checkout, or a published build), and batching writes (Render
  is ~65 ms per round trip).
- **The viewer:** route, template, vanilla JS canvas player, and controls (play, speed, scrub with kill and
  plant ticks, round prev/next). Vendoring the minimap images and multipliers into `app/static`. A kill feed
  that shows this site's Impact score per kill, which is the main thing it adds over valoplant. Behaviour when a
  match has no replay, which is the normal case.
- **Demo site:** whether to seed a replay for a demo sample match. The demo must never show scraped data.
- **Tests:** a small derived ndjson fixture committed for condenser and linking unit tests. Never the `.vrf`.
- **Risks:** patch lag (the parser is locked to specific game branches), alpha API churn, coverage (only matches
  the user played), and Riot policy.

## Constraints

- The repo is public: `*.vrf` is gitignored, and don't commit credentials or raw replay files.
- Don't write to the prod DB. For read-only checks, append
  `options=-c default_transaction_read_only=on` to the `webapp/.env.remote` URL. The user runs anything that
  writes to prod (crawls, migrations); give them a one-line `!` command.
- Nothing here may change Impact scoring (`app/scoring/`). If the design ever needs to, stop and flag it,
  because it would fall under `docs/superpowers/SCORING-RELEASE-PROCESS.md`.

## Deliverable

Update `docs/replay-viewer-plan.md` in place:

- Add a Phase 0 findings section with the evidence.
- Firm up the architecture, schema and JSON format.
- Break the build into phases. Each phase gets its files to touch, acceptance checks, and what the user must run.
- List any open decisions for the user, each with a recommendation.

Commit it to `replays` (no PR unless asked), then summarise the findings and recommendations for the user in a
few short paragraphs.
