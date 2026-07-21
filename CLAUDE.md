# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## This is the private sister-repo

This repo (`valomaths-private`) is a **private**, personal-use-only fork of the public `ValorantIGLTutor`/valomaths repo. It exists because the public repo is being groomed for Riot API production access (it serves `/riot.txt` for Riot's domain-verification check) and shouldn't also display scraped third-party (tracker.gg) data.

- `git remote -v` here has `origin` = this private GitHub repo, and `public` = the original `ValorantIGLTutor` repo. Pull new public-site features in with `git fetch public && git merge public/main`.
- Runs **local-only** — no cloud deployment. Keep private-only changes additive (new files) rather than editing shared files, so future merges from `public` stay clean.
- **Docker Compose gotcha**: both this repo's `webapp/` folder and the original repo's `webapp/` folder are literally both named `webapp`, so Docker Compose's default project name (derived from the folder name) collides between the two repos — running plain `docker compose up -d` here can recreate/repoint the *other* repo's Postgres container. Always bring this repo's Postgres up with an explicit project name: `docker compose -p valomaths-private up -d`. It's mapped to host port **5433** (not 5432, which the original repo's Postgres uses) — see `.env`.
- `app/adapters/trackergg_browserstate_source.py` + `scripts/ingest_trackergg_player.py` — the tracker.gg ingestion pipeline (see below). Not present in the public repo.

## What this is

A Valorant match-analytics project. The primary, forward-looking piece is `webapp/` — a FastAPI + SQLAlchemy + Postgres site that stores match/round/kill-event data in a source-agnostic schema and computes a custom per-player, per-round "Impact" score based on kill order, economy state, post-plant timing, and trade windows.

At the root of the repo, `matchDataPipeline.py` is a personal, local-only utility used occasionally to pull data from a saved match page into that schema — it's how the webapp's demo data got populated, and how new demo matches can be added later. It's a one-person tool (GUI automation, hardcoded local paths) and isn't part of the deployed site.

## What this hopes to be

Eventually `webapp/` will pull match data live from the Riot API instead of (or alongside) the local import pipeline. Riot does not grant Valorant match-data API access on a self-serve personal key — production access requires demonstrating a working product first — so the schema in `app/models/` is deliberately source-agnostic (a `MatchSource` enum distinguishes `SCRAPED` vs `RIOT_API` rows) so a `RiotAPISource` adapter can be added later without touching the schema or the Impact scoring logic.

## `webapp/` — the site

A FastAPI + SQLAlchemy 2.0 + Alembic + Postgres project, independent of the root-level `matchDataPipeline.py` (which stays as the local seed-data tool until Riot API approval).

- `app/models/` — the canonical, source-agnostic schema: `players`, `matches`/`match_players`, `rounds`/`round_player_stats`, `kill_events`, `impact_scores`. Designed so both the local import pipeline's output and a future `RiotAPISource` can feed the same tables via pluggable adapters.
- `app/adapters/demo_match_source.py` — adapter that loads a match from `matchDataPipeline.py`'s parsed output into the schema above.
- `app/db.py` / `app/config.py` — SQLAlchemy engine/session setup; DB connection comes from `DATABASE_URL` (see `.env.example`), defaulting to the local docker-compose Postgres.
- `app/main.py` — FastAPI app, currently just a `/health` endpoint that round-trips the DB.
- `alembic/versions/` — schema migrations; `0001_initial_schema.py` creates all 7 tables.
- `docker-compose.yml` — local Postgres 16 for development.
- `render.yaml` — Render blueprint for deployment (Postgres + web service, runs `alembic upgrade head` on build).
- `scripts/seed_demo_matches.py` / `scripts/ingest_demo_match.py` — one-off scripts to bulk- or single-ingest match JSONs from `MatchHTMLJsons/` into the DB via the adapter above. Not part of the deploy path — `render.yaml`'s build command seeds from the static `seed_data/demo_matches.sql` dump instead, via `scripts/load_seed_data.py`.

### Running the webapp locally

```
cd webapp
docker compose -p valomaths-private up -d            # start local Postgres on port 5433 (see gotcha above)
.\.venv\Scripts\python.exe -m alembic upgrade head    # apply migrations
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

A `.venv` with `requirements.txt` installed already exists in `webapp/` (includes `playwright`, used by the tracker.gg pipeline below). `.env` already points `DATABASE_URL` at port 5433.

## tracker.gg ingestion pipeline (private-only)

tracker.gg has no usable public API for Valorant match data, so this pulls data the same way a human browsing the site would: a dedicated Chrome profile with remote debugging enabled, attached to via Playwright's CDP connection. No independent/autonomous HTTP scraping, no fingerprint spoofing.

```
webapp\scripts\launch_trackergg_chrome.ps1                          # one-time per session: opens a dedicated Chrome profile with --remote-debugging-port=9222
.\.venv\Scripts\python.exe scripts\ingest_trackergg_player.py "RiotName#TAG" --count 5
```

- `scripts/ingest_trackergg_player.py` — given a Riot ID, discovers their N most recent matches via their public tracker.gg match-history page, skips any whose tracker.gg match ID (`matches.external_id`) is already in the DB, and ingests the rest with human-paced delays (5-12s) between matches.
- `app/adapters/trackergg_browserstate_source.py` — maps tracker.gg's own internal match-detail API response (`api.tracker.gg/api/v2/valorant/standard/matches/<id>`, observed via the attached browser's network traffic — never called directly) into the same schema `demo_match_source.py` populates. Also computes `ImpactScore` rows via `app/scoring/impact.py`'s existing `compute_impact_for_match`, which `demo_match_source.py`'s own callers don't always do.
- Works for any player's Riot ID, not just your own — tracker.gg profile/match pages are public, no login required.

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
