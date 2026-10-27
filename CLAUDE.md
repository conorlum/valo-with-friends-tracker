# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## One repo, two deployed sites

This repo started as a fork of `conorlum/ValoMaths` (originally `ValorantIGLTutor`) that added a tracker.gg data source for friend-group match stats. Since 2026-09-24 (PRs #74, #75) it is the only codebase: `conorlum/ValoMaths` is archived, and both sites deploy from this repo's `main` on every merge. See `webapp/RENDER_DEPLOY.md`.

- **`valowithfriendstracker`** (https://valowithfriendstracker.onrender.com): the friends site, with real tracker.gg data in the friends DB. It is managed by `render.yaml`. The `ValoWithFriendsTracker.com` domain does not resolve.
- **`valomaths`** (https://valomaths.onrender.com): the public ValoMaths demo. It runs with `DEMO_MODE=true` and `SITE_NAME=ValoMaths`, serves `/riot.txt` for Riot's domain verification, and reads `valomaths-demo-db`, which holds sample data only. **That URL is registered with Riot and must never change.** The service is configured by hand in the dashboard, outside `render.yaml`. Never add `load_seed_data.py` to its build command.
- Code differences between the two sites are settings (`app/config.py`: `demo_mode`, `site_name`, `enable_riot_txt`), not branches or forks. The demo must never show scraped tracker.gg data.

- **This GitHub repo is PUBLIC** (`conorlum/valo-with-friends-tracker`). Treat everything committed here as world-readable. **Never commit a credential** -- `webapp/.env.remote` held the live Render Postgres password in a public repo from 2026-08-26 until it was caught and rotated on 2026-09-03. `.env.*` is now gitignored (with `!.env.example`); that covers `.env.remote` (friends DB) and `.env.demo-remote` (demo DB).
- **Docker Compose gotcha**: this repo's `webapp/` folder and the old ValoMaths clone's `webapp/` folder share a name, so Docker Compose's default project name (derived from the folder name) collides between the two — running plain `docker compose up -d` here can recreate/repoint the *other* checkout's Postgres container. Always bring this repo's Postgres up with an explicit project name: `docker compose -p valomaths-private up -d`. It's mapped to host port **5433** — see `.env`.
- Repo name history: this was previously named `valomaths-private` (both on GitHub and as the local folder name). The GitHub repo is now `valo-with-friends-tracker`, and the local folder was renamed to match. Some internal paths (the Docker Compose project name, local Postgres db name) intentionally still use `valomaths`/`valomaths-private` — they're independent of the folder name and don't need to be changed.

## What this is

A Valorant match-analytics project. The primary, forward-looking piece is `webapp/` — a FastAPI + SQLAlchemy + Postgres site that stores match/round/kill-event data in a source-agnostic schema and computes a custom per-player, per-round "Impact" score based on kill order, economy state, post-plant timing, and trade windows.

At the root of the repo, `matchDataPipeline.py` is a personal, local-only utility used occasionally to pull data from a saved match page into that schema — it's how the webapp's demo data got populated, and how new demo matches can be added later. It's a one-person tool (GUI automation, hardcoded local paths) and isn't part of the deployed site.

## What this hopes to be

Eventually `webapp/` will pull match data live from the Riot API instead of (or alongside) the local import pipeline. Riot does not grant Valorant match-data API access on a self-serve personal key — production access requires demonstrating a working product first — so the schema in `app/models/` is deliberately source-agnostic (a `MatchSource` enum distinguishes `SCRAPED` vs `RIOT_API` rows) so a `RiotAPISource` adapter can be added later without touching the schema or the Impact scoring logic.

## `webapp/` — the site

A FastAPI + SQLAlchemy 2.0 + Alembic + Postgres project, independent of the root-level `matchDataPipeline.py` (which stays as the local seed-data tool until Riot API approval).

- `app/models/` — the canonical, source-agnostic schema: `players`, `matches`/`match_players`, `rounds`/`round_player_stats`, `kill_events`, `impact_scores`, `friendships`, `player_view_cache`. Designed so both the local import pipeline's output and a future `RiotAPISource` can feed the same tables via pluggable adapters.
- `app/adapters/demo_match_source.py` — adapter that loads a match from `matchDataPipeline.py`'s parsed output into the schema above.
- `app/db.py` / `app/config.py` — SQLAlchemy engine/session setup; DB connection comes from `DATABASE_URL` (see `.env.example`), defaulting to the local docker-compose Postgres.
- `app/main.py` — FastAPI app, currently just a `/health` endpoint that round-trips the DB.
- `alembic/versions/` — schema migrations; `0001_initial_schema.py` creates all 7 tables, `0002_friendships.py` adds `friendships`, `0003_player_view_cache.py` adds `player_view_cache`.
- `docker-compose.yml` — local Postgres 18 for development. The data volume holds a Postgres 18 cluster and is mounted at `/var/lib/postgresql` (the 18 image keeps its data under `18/docker`).
- `render.yaml` (repo root) — the Render Blueprint for the `valowithfriendstracker` web service only: it runs `alembic upgrade head` on build, and it declares no databases. A Blueprint sync applies its `plan:` over the dashboard's setting. The `valomaths` service isn't in it (see above).
- `control_heights` (migration 0019): a map's height assets, built on the replay worker and activated behind a gate (`app/replays/height_inputs.py`, `app/services/control_heights.py`, `app/services/replay_heights_remote.py`; `REPLAY_HEIGHTS_AUTO`, on in `render.yaml`; `scripts/control_heights.py list | activate | off | export`). The demo never has any. See `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md`.
- `map_pool_changes` / `map_pool_patch_notes` (migration 0023): the Competitive map pool's history, from the committed seed `app/data/map_pool_seed.json` (`scripts/build_map_pool_seed.py`) plus pages read from playvalorant.com's patch notes once a day (`app/services/map_pool_refresh.py`; `MAP_POOL_AUTO`, on in `render.yaml`, always off in demo mode). `app/services/map_pool.py` reads it (`load_pool_history`); map streaks use it. `scripts/map_pool.py show [--at ISO] | refresh | seed`.
- `scripts/seed_demo_matches.py` / `scripts/ingest_demo_match.py` — one-off scripts to bulk- or single-ingest match JSONs from `MatchHTMLJsons/` into the DB via the adapter above. Not part of the deploy path.
- `seed_data/demo_matches.sql` — the public ValoMaths demo's sample data (six matches plus a fixed sample friend group), a data-only dump at the schema head. `scripts/load_seed_data.py` loads it and `scripts/dump_seed_data.py` regenerates it. Every command against the demo DB goes through `scripts/with_demo_db.py`, which reads `webapp/.env.demo-remote` and refuses to run unless it is connected to `valomaths_demo`. See `load_seed_data.py`'s docstring for the rebuild order.

### Stats page caches

`/stats` has two populations. **All Players** (every match) is one row in `site_stats_cache`. **Friends** is the logged-in viewer's circle (their linked accounts and their friends', see "Account groups" below). It is computed per viewer and cached one row per viewer in `viewer_site_stats_cache` (migration 0011), keyed by a hash of that player-ID set. Logged-out visitors get All Players only. `scripts/tracked_players.json` only chooses which matches to crawl, so nothing on the site may be defined by it. Any match ingest clears both caches. A friend add or remove clears that viewer's row. The ingest scripts then pre-warm the rows of every friendship owner unless `--no-prewarm` is passed. See `app/services/site_stats.py`.

### Account groups

One person's several Riot accounts (migration 0027): `account_groups` (with its `main_player_id`) +
`account_group_members` (an account is in at most one group, with an optional label). `/accounts` links them,
zero-auth like friendships, never on the demo. The first account to add another becomes the main; only the main
adds, removes and labels accounts or hands the main role to a sub, and a sub can leave. A linked account can't
be added to another group, so a wrong claim is undone by leaving.
Each grouped account's player page shows an "All accounts" chip (`?accounts=all`, also passed to the career and
recent-matches fragments) and one chip per account. The combined page is the same replay over every account's
matches (`player_ids=` on `load_player_match_data` / `compute_player_views`) and is cached per group in
`account_group_view_cache`. A row is valid only while its fingerprint matches (accounts, their matches and Impact
rows, their friendships: `group_fingerprint`), so ingest paths need no new hooks; `invalidate_player_cache` and
`invalidate_all_player_caches` clear group rows too, and `prewarm_player_cache` re-warms them. See
`app/services/account_groups.py`. A match two linked accounts both played counts once in the combined view
(`player_data.one_row_per_match`, lowest player id's row). The combined recent-matches table has an Account
column (the member's label, else its name without the tag).

**Friends through groups.** A person is a group: their circle is all their accounts, everyone friended from any
of them, and those friends' own grouped accounts, one level deep (`app/services/circle.py`, one SQL statement
each). Every reader of "friends" uses it: `/stats` Friends, fight-EV's teammate pool, the match list, sessions,
squad, gap patterns and recent match rows. Friendships stay stored per account. A friend add or remove clears
the player and `/stats` viewer rows of all the owner's accounts; a link or unlink clears every circle that holds
either account (`circle_dependents`), in the same transaction.

**Role/agent filter.** The player page (single account or `?accounts=all`) takes `?filter=role:<role>` or
`?filter=agent:<Agent>`, one at a time, from a dropdown of the agents that account or group played. Roles come
from the committed `app/data/agent_roles.json` (`app/services/agent_roles.py`). Every section built from the
player's matches is filtered, except map streaks; "recent" is the last 30 matches on that filter. One career load
and one replay feed every filter (`compute_player_views_by_filter`). The pre-warm computes the unfiltered view,
each role played and the top 3 agents (`precompute_keys`); any other filter is computed on first visit and
cached. Filtered rows live in the same caches: migration 0028 widens `player_view_cache.scope` and
`account_group_view_cache.scope` to keys like `career|role:sentinel` (`cache_scope_key`), and they are cleared
with the unfiltered rows.

### Player page pre-compute cache

The player detail page's two heaviest computations (the round-win/kill-order state diagrams and the fight-EV diamond, both of which replay every round of every match a player has played) are pre-computed and cached in the `player_view_cache` table instead of being recomputed on every page load. See `docs/player_page_precompute.txt` for the full design.

- `app/services/player_data.py` — the shared, leaf-level DB load (`load_player_match_data`) both products replay from; also owns `RECENT_MATCH_LIMIT` (30).
- `app/services/player_views.py` — orchestrates one load into both products (`compute_player_views`, `compute_player_views_by_scope`).
- `app/services/player_view_cache.py` — cache read/write/validate/invalidate: `get_cached_views`, `store_player_views`, `invalidate_player_cache`, `find_cached_player_ids_for_match`, `recompute_player_views`, `prewarm_player_cache`. Best-effort everywhere — a miss, a corrupt blob, or a stale `version` all degrade to a live recompute; the cache is never load-bearing for correctness.
- Two rows per player: `scope='recent'` (last `RECENT_MATCH_LIMIT` matches) and `scope='career'` (full history).
- Invalidated when: a new match is ingested for a cached player, a friendship the profile owner has is added/removed, or `PLAYER_VIEW_CACHE_SCHEMA_VERSION`/`fight_ev.CALCULATION_VERSION` is bumped. Both `app/adapters/trackergg_browserstate_source.py` and the demo-match scripts invalidate right after a match is committed, before impact scoring, so a scoring failure leaves the cache empty rather than stale.
- `scripts/recompute_player_views.py` — batch recompute for every player (or `--cached-only`); run after a bulk import/crawl, a manual DB fixup, or a schema/calculation version bump: `.\.venv\Scripts\python.exe scripts\recompute_player_views.py`.
- Both tracker.gg ingest scripts (`ingest_trackergg_player.py`, `refresh_tracked_players.py`) pre-warm the cache for every player whose rows were invalidated, once each, at the end of the run — pass `--no-prewarm` to skip this (a full career recompute is several seconds per affected player).

### AI coach

Coaching reports for one player on one match with a replay: three things done well, three to improve, each linked
to every moment in the replay (`docs/superpowers/specs/2026-10-12-ai-coach-design.md`). Never on the demo.

- `coach <match uuid> "Name#TAG"` (`scripts/coach.ps1`) snapshots the match from the friends DB, runs headless
  Claude Code in a temp directory with one permitted command (the logged query tool), checks every point against
  the run log (`app/coach/checker.py`) and publishes three and three. A point whose evidence still fails after
  one repair is published anyway if it can be stored, and listed with its problems in the command's output
  (`unchecked`): the owner's choice, feedback prunes weak points. `-DryRun` prints instead; `-PromptOnly` writes
  the first prompt to `prompt.txt` in the run directory and calls no model. `-Team` does the same for that
  player and their four teammates (`scripts/coach_team.py`): one `coach_report.py` process each, one after another,
  never two at once (the owner's choice); one player's failed run leaves the others' reports alone.
- What the checker holds a point to: a figure with a unit against cited answers of that kind (metres, seconds),
  a named area against a cited answer, a count like 2v1 against who was alive, and, in a point to improve, no
  distance to or place of an enemy the player's side could not see. It does not check who did what: "X killed
  you" passes if X is in the match. Each round's economy and score are citable (`facts_id` in the brief).
- The model's working directory is `<run dir>/model`, holding only `q`: Claude Code lets a headless session read
  files inside its working directory without asking, so the snapshot and the log stay one level up. Its
  environment is an allow-list of named variables (`coach_report.child_env`), never a prefix. Never add a flag that skips permission checks.
- `webapp/coach/playbook.md` is the owner's principles and the model's rules. This repo is public: no names or
  match ids in it. Bump its `version` on any edit; only the owner approves edits. Every coach command
  refuses to run on a playbook at version 0 or with nothing under `# Principles`: the principles are the
  owner's own lines. Never write stand-in principles for them.
  The model writes in its own plain voice, one tone for every point: no voice samples go into the prompt
  (the owner's choice, 2026-10-18). `coach_notes` (`scripts/coach_notes.py`) still stores notes; none are read.
- The page is the replay viewer: `/replays/<uuid>/coach` (`app/routers/coach.py`). A strip above it names the
  player and version (`replays/_coach.html`); the points are the first two tabs of the viewer's side column,
  "Did well" and "To improve", beside the map (`replays/_coach_points.html`). Reports are immutable; a re-run adds a version. Tables: migration `0021`.
- Feedback is per moment (Right / Wrong / Correction and a note, under each of a point's times) plus one overall
  note per point, sent in one form, from any logged-in viewer (login has no password, so authors are
  unverified); `coach_feedback.instance_index` (migration 0025) says which moment, NULL for the point. "Prompt
  out" gives each moment's round and time with its feedback, and text for a Claude session that checks the feedback against the data; the owner decides any playbook
  change, then `scripts/coach_feedback.py handled --report <id> [<id> ...]`. "Prompt out" under a report covers
  that report; "Prompt out, all players" in the panel's header covers every report of the match that has open
  feedback, whatever its version (`/replays/<uuid>/coach/prompt` with no `report`).
- Opponents' names never appear in the coach or Critical Moments tabs; they are "the enemy <agent>"
  (`app/services/enemy_names.py`). A run's snapshot renames them before the model sees it
  (`snapshot.hide_enemy_names`), the coach page swaps any left in stored text, and the moments payload names the
  team without the friends (by friendships; both teams on a tie) by agent.
- Named areas come from `app/static/data/callouts.json` (`scripts/fetch_callouts.py`; re-run when a map is added).

### Critical Util

Which utility wins rounds, each friend's best and worst util, and the patterns in how they use it
(`docs/superpowers/specs/2026-10-21-critical-util-design.md`). Never on the demo. No AI in the per-match tab.

- The worker extracts each round's casts and what followed them (`app/critutil/extract.py`) in the gap run, beside
  Critical Moments, with its own failure status: who each cast affected, kills on affected enemies, teammates it
  hit, sight lines a smoke cut, enemies held back or waiting at an area cast, a device's trigger, defuses stopped,
  heals that kept someone alive, deaths holding util after engaging first. Stored in `replay_round_util`
  (migration 0029) under the gap run's keys (`GAPS_REVISION` 5, gap protocol 3: the site and the worker image
  must both speak it, `replay_worker/Dockerfile` ships `app/critutil`). The gap run also steps an unknown probe
  (`app/critutil/unknown.py`): how long each trip, camera, turret or molly held the map-control unknown at its edge.
  Statuses on teammates are kept from `CONDENSE_REVISION` 22, flagged `friendly`; control and Critical Moments
  never see them (`RoundInputs` reads `engine.control_view(blob)`).
- Values are the site's (`app/services/util_values.py`): the change in the caster's side's round-win chance.
  Kills go through one ledger per round, each kill's swing split once between the casts with evidence on it and
  the gunplay. Jobs with no kill (cover, hold, stall, triggers, heals, soft teammate harm) go through a few
  non-negative weights fitted on round wins, adopted only when they beat the starting weights on held-out
  matches, each match then valued with its own fold's weights. The round-win estimator (`app/services/util_model.py`)
  is fitted on the tracker.gg kill feed of every match without a replay, on the DB clock
  (`t_db = t_replay + clock_offset`); it stores the replay matches it left out and is refitted before a match that
  later got a replay is valued (`util_model.ensure`).
- Scores and calls compare each cast with the **no-util baseline** (`util_values` "the no-util baseline"): the
  change in the side's round-win chance over the next 10 s, against moments in other matches in the same state and
  phase with no util of that kind up (`lift`), shrunk toward the ability's mean lift (its prior). `value` is the
  ledger-and-jobs credit, shown beside it.
- Only rows whose gap fingerprint is current are read (`replay_util.current_rows`, Critical Moments' rule).
  Products in `util_model` ("win", "values"), rebuilt when what they were built from changes (`corpus_key`,
  computed from the rows a build read; `replay_util.ensure_values`, one rebuild at a time under a Postgres advisory
  lock; the first page visit fits a missing or stale estimator). `scripts/critical_util.py fit-win | refresh |
  measure | show` (through `with_friends_db.py`; `measure` is read-only).
- The tab is `replays/_player.html` "Critical Util" (`app/static/js/critutil.js`, `/replays/<uuid>/util.json`); the
  page is `/util` (`app/routers/util_page.py`), the viewer's circle only (`app/services/circle.py`). The coach
  brief's `util_facts` are logged with ids the checker trusts (`app/coach/runner.py`).

### Running the webapp locally

```
cd webapp
docker compose -p valomaths-private up -d            # start local Postgres on port 5433 (see gotcha above)
.\.venv\Scripts\python.exe -m alembic upgrade head    # apply migrations
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

A `.venv` with `requirements.txt` installed already exists in `webapp/` (includes `playwright`, used by the tracker.gg pipeline below). `.env` already points `DATABASE_URL` at port 5433.

### Running the tests

Run from `webapp/` with `./.venv313/Scripts/python.exe -m pytest`. The default excludes `slow`,
`browser`, and optional `corpus` measurements; xdist uses a third of the cores and reserves the last four.
See `docs/testing.md` for prerequisites, required CI lanes, and the coverage-audit implementation map.

| Lane | Arguments after `-m pytest` |
|---|---|
| Offline, including Node helpers | `-n 2 -m "not slow and not postgres and not browser and not corpus" --require-node --require-fixtures` |
| PostgreSQL integration | `-n 0 -m "postgres and not corpus and not slow" --require-postgres` |
| Browser journeys | `tests/browser -n 0 -m browser --require-browser --require-fixtures` |
| Slow geometry/reference | `-n 2 -m "slow and not postgres and not browser and not corpus" --require-node --require-fixtures` |

PostgreSQL is opt-in through `VALO_TEST_DATABASE_URL` only. Use a disposable local `*_test` database and a role
allowed to create scratch databases. The required lane gives each legacy case its own Alembic-migrated database,
installs the release gate only for gate/release tests, and drops only the scratch databases it created.
It runs serially: a shared PostgreSQL URL with xdist workers is refused. Never use the friends/demo DB or a
measurement restore for writing tests. Optional corpus measurements remain separate and require approved inputs.

`--require-node`, `--require-fixtures`, `--require-postgres`, and `--require-browser` turn missing prerequisites
into failures in their designated lanes. Only private-owner replay inputs and corpus probes may remain optional.
Run the slow lane before merging changes to control, gaps, heights, or features. Mark expensive new cases `slow`;
do not make correctness depend on available memory, machine load, or long sleeps.

## Changing how Impact is scored

Any change to Impact scoring -- a new term, a changed factor, new weights -- and any release of one follows
`docs/superpowers/SCORING-RELEASE-PROCESS.md`: declare before measuring, implement behind flags that default off,
prove equivalence against a reference built from unchanged code, freeze, rehearse, and activate in one gated
window. Read it before touching `app/scoring/`; plan only the release, not the process. Each release keeps its own
runbook at `docs/superpowers/impact-<id>/README.md` (rc3 shipped; v4 in progress).

## tracker.gg ingestion pipeline

tracker.gg has no public API for Valorant match data, so this reads match data the same way a browser session naturally loads it: a dedicated Chrome profile with remote debugging enabled, attached to via Playwright's CDP connection, browsing the public match-history and match-detail pages.

```
webapp\scripts\launch_trackergg_chrome.ps1                          # one-time per session: opens a dedicated Chrome profile with --remote-debugging-port=9222
.\.venv\Scripts\python.exe scripts\ingest_trackergg_player.py "RiotName#TAG" --count 5
```

- `scripts/ingest_trackergg_player.py` — given a Riot ID, discovers their N most recent matches via their public tracker.gg match-history page, skips any whose tracker.gg match ID (`matches.external_id`) is already in the DB, and ingests the rest with a 5-12s delay between matches to keep request pacing reasonable.
- `app/adapters/trackergg_browserstate_source.py` — maps tracker.gg's own internal match-detail API response (`api.tracker.gg/api/v2/valorant/standard/matches/<id>`, read from the attached browser's network traffic as the user's own session loads match pages) into the same schema `demo_match_source.py` populates. Also computes `ImpactScore` rows via `app/scoring/impact.py`'s existing `compute_impact_for_match`, which `demo_match_source.py`'s own callers don't always do. Its `ingest_recent_matches(db, page, riot_id, count)` is the shared discover→dedup→ingest→score loop used by both `ingest_trackergg_player.py` and `scripts/refresh_tracked_players.py` below.
- Works for any player's Riot ID, not just your own — tracker.gg profile/match pages are public, no login required.
- `scripts/tracked_players.json` + `scripts/refresh_tracked_players.py` — a fixed roster of friends' Riot IDs (plain JSON list) the user actually cares about, and a one-command batch refresh that walks the whole roster over a single Chrome/CDP connection, pulling each person's `--count` (default 20) most recent matches. One player's private profile or a fetch error is logged and skipped rather than aborting the batch. Run it the same way as the single-player script, just without a Riot ID argument: `.\.venv\Scripts\python.exe scripts\refresh_tracked_players.py`.

## `matchDataPipeline.py` — local match-data import utility

Not part of the deployed site. A single-script, semi-manual pipeline run directly with `python matchDataPipeline.py`, used to bring a new match's data into `MatchHTMLJsons/` and, from there, into the webapp's DB via the adapter above.

### Running it

```
python matchDataPipeline.py
```

The script prompts interactively for a match key in the form `<Map><MMDDYYHHMM>` (e.g. `Haven101725908`). The `__main__` block has most pipeline stages commented out — only `measureOutgoingImpact(filename)` runs by default, which means a corresponding JSON file must already exist under `MatchHTMLJsons/` before running (see Pipeline stages below for how to regenerate one).

No `requirements.txt` exists. Dependencies inferred from imports: `pyautogui`, `Pillow` (`PIL`), `imagehash`, `networkx`, `matplotlib`, and `pydot` (required by `networkx.drawing.nx_pydot.graphviz_layout`, which also needs Graphviz installed on the system).

### Per-machine config (edit before running on a new machine)

At the top of `matchDataPipeline.py`:
- `User` — Windows username, used to build absolute `C:\Users\{User}\...` paths throughout the script (paths are not portable/relative).
- `resolutionScaling` — scales `pyautogui` mouse-move offsets in `moveOneRoundOver()`; set to `1.5` for 4K displays.

### Pipeline stages

The script is one file organized into sequential stages, each consuming the previous stage's output. Stage functions are mostly invoked ad hoc from `__main__` (commented in/out) rather than as a single orchestrated run:

1. **Aquire (GUI automation)** — `createMapFolder`, `clickAndSaveRound`, `saveAllRounds`, `moveOneRoundOver`, `cleanUpFiles`. Uses `pyautogui` to drive the browser's "Save As" dialog and save each round of a match as a complete webpage (HTML + `_files/` assets) into `MatchPages/<filename>_ALL_FILES/<filename>-Round<N>/`. This requires the match page to already be open in a browser window and the mouse positioned correctly — it's a semi-manual, human-supervised loop (`input()` prompts between batches of 20 rounds).

2. **Condense to JSON** — `parseOutRoundCount` (reads total rounds from the saved page's team-A/team-B round-win counters), `saveHTMLToJson` (extracts just the relevant `<body>`-adjacent HTML line per round and writes one combined JSON per match to `MatchHTMLJsons/<filename>.json`), `loadHTMLSFromJson`.

3. **Extract structured data from HTML** — `parseEconPerRound`, `parseRoundOutcome`, `parseRoundKillList`, `parseTeamPlayers`, `parsePlayerRoundInfo`. These parse the saved page's HTML via string-splitting on known CSS class name fragments (not a real HTML parser) — the class names being matched against are documented in `MatchPageClasses.txt`. This is brittle to the source site's frontend changes; if parsing breaks, check whether these class-name substrings still appear in a freshly saved page.

4. **Icon classification via perceptual hashing** — `agentDisplayIconLookup`, `weaponNewImageLookup`. Saved pages embed agent/weapon icons as numbered local image files (`displayicon<N>.png`, `newimage<N>.png`); these functions identify which agent/weapon an icon represents by comparing `imagehash.average_hash` against the reference sets in `agentIconReferences/` and `weaponIconReferences/` (closest hash wins, distance > 5 is rejected as `"BAD CLASSIFICATION!"`). Adding a new agent/weapon means dropping a new reference PNG into the matching folder.

5. **Impact calculation** — the core analytics, all feeding `measureOutgoingImpact`:
   - `calculateKillOrderBonus` — a `networkx` `DiGraph` of every man-advantage state (`5v5` → ... → `0v0`) with a hand-tuned bonus weight on each edge, used to reward/penalize kills based on how much they swung the round state.
   - `calculateEconSwingRiskFactor` — estimates how "econ-swingy" a round is based on team loadouts/remaining credits and buy thresholds, with special-cased pistol/anti-eco rounds (round 1/13, 2/14, 12/24).
   - `calculateTimeFactor` — weights kills near/after plant or during post-plant defuse windows more heavily.
   - `calculateTradedFactor` — discounts a death's negative impact if the killer was traded back within 10s.
   - `calculateEconDifferential` — categorizes each side's loadout value (save/econ/full-buy) and factors the mismatch into kill value.
   - `calculateDamageAndAssists_KillOrderSum_KillFactorAverage` and `calculateRoundImpact` combine the above into per-round `killImpact`/`deathImpact`/`Impact` for every player.
   - `displayImpact` prints players ranked by average `Impact`; `createAndDisplayKillOrderGraph` renders a `matplotlib` graph of one player's kill/death transitions through the man-advantage state graph.

### Data layout / naming convention

Match keys follow `<MapName><MMDDYYHHMM>` (e.g. `Haven101725908` = Haven, played 10/17, ~08:xx) and are used as the lookup key across all of these directories:

- `MatchPages/<key>_ALL_FILES/` — raw saved webpages per round (large, browser-snapshot output of stage 1).
- `MatchHTMLJsons/<key>.json` — condensed per-round HTML produced by stage 2; this is what all downstream parsing (`loadHTMLSFromJson`) actually reads.
- `agentIconReferences/*.png` — one reference icon per agent, filename is the agent name (`KAYO.png` is looked up but normalized to display as `"KAY/O"`).
- `weaponIconReferences/*.png` — one reference icon per weapon, filename is the weapon name.
- `MatchPageClasses.txt` — a running notes file of CSS class-name fragments used as string-split anchors throughout stage 3; consult/update this when HTML parsing breaks.
