# Deploying to Render — two sites from one repo

`webapp/` is deployed twice from this repo's `main`. Every merge to `main`
auto-deploys both:

| Service | URL | Database | Configured by |
|---|---|---|---|
| `valowithfriendstracker` | https://valowithfriendstracker.onrender.com | the friends DB (real tracker.gg data) | `render.yaml` (the DontTellRiotTracker Blueprint) |
| `valomaths` | https://valomaths.onrender.com | `valomaths-demo-db` (sample data only) | **by hand** in the dashboard — see (d) |

Both are deliberately zero-auth (anyone with the URL can pick any seeded
player at `/login`). The `ValoWithFriendsTracker.com` custom domain in (a) step 7
does not resolve; the onrender URL is the real one.

Both web apps and both Postgres databases run on **Render**, in the same
region (Oregon), so each app reaches its DB over Render's internal network.
Neither database is in `render.yaml` (deliberate: a `render.yaml` mistake
should not be able to destroy one).

The friends DB previously lived on Neon. It was migrated to a paid Render
Postgres on 2026-08-26 — see "Migrating the database" below — and the Neon
account has since been deleted. Two connection strings matter and they are not
interchangeable:

- **Internal** (`dpg-xxxx-a`, no domain suffix) — for the web service's
  `DATABASE_URL`. Only resolves inside Render's network.
- **External** (`dpg-xxxx-a.oregon-postgres.render.com`) — for anything run
  from a dev machine, including `scripts/refresh_remote.ps1`.

## (a) One-time initial setup

1. **Render Postgres**: in the dashboard, **New → Postgres**. Pick PostgreSQL
   **18** and the same region as the web service (Oregon). Copy both the
   Internal and External connection strings.
2. **Render web service**: **New → Blueprint**, connect the
   `valo-with-friends-tracker` GitHub repo. Render auto-detects `render.yaml` at the
   repo root. This creates the web service (`valowithfriendstracker`) — there's
   no `databases:` block, so the DB above stays outside blueprint management
   (deliberate: a `render.yaml` mistake should not be able to destroy it).
3. On the web service's **Environment** tab, set `DATABASE_URL` to the
   **Internal** connection string from step 1.
4. Deploy (or redeploy after saving the env var). The build runs `pip install`
   then `alembic upgrade head` against the DB — schema only, zero rows.
5. Load real data — see "(c) Migrating the database" below, or from a populated
   local Postgres (`docker compose -p valomaths-private up -d`):
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<render-EXTERNAL-connection-string>"
   ```
6. Verify: `https://<render-service>.onrender.com/health` should return
   `{"status": "ok"}`. Log in as a seeded player and spot-check a sessions page.
7. Custom domain: web service → **Settings → Custom Domains** → add
   `ValoWithFriendsTracker.com` (and `www.ValoWithFriendsTracker.com`). Render will
   show the DNS records to add (typically an ALIAS/ANAME or A record for the
   apex, CNAME for `www`) — add them at wherever the domain is registered/DNS
   is managed. Render auto-provisions a TLS cert once DNS resolves; this can
   take anywhere from minutes to a few hours.
8. Confirm `SESSION_SECRET` was auto-generated (web service → **Environment**)
   rather than left as the code's `dev-only-change-me` default — the blueprint's
   `generateValue: true` should have handled this automatically.

## (b) Routine workflow — pushing freshly ingested matches live

The normal path ingests straight into the deployed DB, no local Postgres and no
dump/restore involved:

```powershell
matches            # PowerShell profile alias -> scripts\refresh_remote.ps1 -Count 5
matches -Count 20
```

`refresh_remote.ps1` reads `webapp/.env.remote` (Render's **External** URL),
launches the tracker.gg debug Chrome profile if needed, and runs the same
idempotent, dedup-by-`external_id` ingestion as the local refresh. It only ever
ADDS matches — re-running is safe. No redeploy needed; spot-check the live site.

`scripts/refresh_matches.ps1` does the same thing but brings up a local Docker
Postgres first — it requires Docker, which is not installed on the current
machine. Use `matches` / `refresh_remote.ps1` instead.

### Pulling Render down to local

Local Postgres is a disposable copy of Render for analysis. To make it current:

```powershell
.\scripts\pull_render_to_local.ps1
```

It dumps Render (read-only), **wipes** the local `valomaths-private` database,
restores the dump, diffs every table count and sequence against Render, and
rebuilds the observation cache. Dumps are kept outside the repo, newest 3, in
`%LOCALAPPDATA%\valomaths\render-dumps`. Don't ingest into local in between pulls.

## (c) Migrating the database

Moving the deployed DB to a new host (as was done Neon → Render on 2026-08-26)
without Docker, using native PostgreSQL client binaries:

1. Install PostgreSQL client binaries whose major version is **>= the source
   server** (`pg_dump` refuses to dump a newer server). EDB publishes a
   no-installer Windows zip; `winget install PostgreSQL.PostgreSQL.18` also works.
2. Create the new DB, matching the source's major version. Leave it **empty** —
   don't point the web service at it yet, or `alembic upgrade head` will create
   the schema and collide with the restore.
3. Baseline the source: record `count(*)` for every table plus
   `max(matches.played_at)` and the `alembic_version` head.
4. `pg_dump --format=custom --no-owner --no-privileges -d "<source>" -f dump.dump`
5. `pg_restore --no-owner --no-privileges --jobs 4 -d "<target-external>" dump.dump`
   — no `--clean` against an empty target; it only produces misleading
   "does not exist, skipping" noise.
6. Verify against the step-3 baseline: row counts, `max(played_at)`, alembic
   head, and **sequence positions** (`last_value` vs `max(id)` for every table —
   `COPY` does not advance sequences, and a sequence left behind causes
   duplicate-key errors on the next insert).
7. Swap the web service's `DATABASE_URL` to the new **Internal** URL, redeploy,
   check `/health`.
8. Point `webapp/.env.remote` at the new **External** URL so `matches` follows.
9. Keep the old DB and the dump file for a week before deleting.

## (d) The public ValoMaths demo — `valomaths`

`https://valomaths.onrender.com` is the URL registered with Riot (it serves
`/riot.txt`), so **it must never change**. An onrender URL belongs to the
service it was created with. So on 2026-09-24 the old ValoMaths service (which
built from `conorlum/ValoMaths`) was disconnected from its Blueprint and
switched to this repo in place, rather than being replaced (PRs #74, #75).

It is **not** in `render.yaml`. Its settings live only in the dashboard.
Before changing any of them, check **Settings → Source** to confirm you are in
`valomaths`, not `valowithfriendstracker`:

- **Source** `conorlum/valo-with-friends-tracker`, **Branch** `main`,
  **Root Directory** `webapp`, Auto-Deploy on commit.
- **Build Command** `pip install --upgrade pip && pip install -r requirements.txt && alembic upgrade head`.
  **Never add `scripts/load_seed_data.py` to it** — the old ValoMaths build had
  that step, and it would reload the seed on every deploy.
- **Pre-Deploy Command** empty. **Start Command**
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. **Health Check Path** `/health`.
- **Environment**: `DATABASE_URL` (the demo DB's **Internal** URL),
  `SITE_NAME=ValoMaths`, `DEMO_MODE=true`, `ENABLE_RIOT_TXT=true`,
  `SESSION_COOKIE_HTTPS_ONLY=true`, `PYTHON_VERSION=3.13.5`, `SESSION_SECRET`
  (generated).

**The demo database.** `valomaths-demo-db` (Render Postgres 18, Oregon,
database name `valomaths_demo`) holds only `seed_data/demo_matches.sql`: six
matches plus a fixed sample friend group. Run every command against it from a
dev machine through `scripts/with_demo_db.py`, which reads the External URL
from `webapp/.env.demo-remote` (gitignored) and refuses to run anywhere else. A
full rebuild is the sequence in `scripts/load_seed_data.py`'s docstring. After
a scoring release, it also needs the steps in
`docs/superpowers/SCORING-RELEASE-PROCESS.md` §J.

**Blueprint syncs set the plan.** A sync applies `render.yaml`'s `plan:` to its
service, overriding the dashboard. PR #74's sync moved
`valowithfriendstracker` from Standard to Free this way, and in October 2026
every merge moved it from 2c-4g back to Standard until `render.yaml` said
`plan: 2c-4g` (2 CPU, 4 GB, $85/month). Change a plan in `render.yaml`, not
only in the dashboard.

`valowithfriendstracker` is on Standard (1 CPU, 2 GB) again since 2026-10-08:
it runs one uvicorn process, which can't use a second core. The two cores are
for the `replay-worker`, a separate service that stays on `2c-4g` for map
control's two children.

**Timing gaps on the worker (2026-10-07).** The site and the `replay-worker` deploy separately from the same
merge. Either deployment order is supported by the health capability gate: a new site with an old worker
sends plain control and leaves gaps pending; an old site with a new worker asks for no gaps. Once the worker
advertises control.gaps_protocol=1, the next planning pass picks up missing gaps without a site restart.
After both are live, the site's log line `map control dispatch: ...` shows `gaps_stored`. A parse and one
control child now run together, so look at the worker's memory graph on the first upload after the deploy;
if it nears 4 GB, lower `REPLAY_CONTROL_MEMORY_MB` in the dashboard.

There is no switch on this: once both services are live, the site also sends a gaps-only task for every
stored round whose control is current and whose timing gaps are missing or out of date. That is the whole
backlog of rounds computed before this change, one worker child at a time beside parses and two when the
worker is idle; each such task runs the control engine again without rewriting control. The dispatch log
shows `gaps_sent` until it drains. To see its size beforehand, this read-only query counts the rounds with
stored control and no gap run at all (rounds whose gaps are merely out of date come on top):

```sql
SELECT count(*) FROM replay_round_control c
LEFT JOIN replay_round_gap_runs g ON g.replay_id = c.replay_id AND g.round_number = c.round_number
WHERE c.status = 'ok' AND g.replay_id IS NULL;
```

The worker's server process keeps the last 200 finished control results in memory, now with their gap rows;
watch that process's memory as well as the children's while the backlog runs.

**The automatic re-parse queue (2026-10-07; on since 2026-10-08).** When a deploy changes the recipe (a parser, condenser or
asset change), stored replays go stale. With `REPLAY_REPARSE_AUTO` on, the site parses each stale *uploaded*
replay again from the worker's archived recording: one at a time, newest match first, behind every upload,
and only once map control has drained. A stale *local* replay stays with `scripts/reingest_replays.py`,
even when the same file is archived. Code: `app/services/replay_reparse_auto.py`, run by the control
dispatcher's thread. `render.yaml` sets it on (with `REPLAY_CONTROL_REMOTE` and the worker's `REPLAY_ARCHIVE_DIR`); the code default is off.

*What merging does while it is off.* Migration `0018` runs in the build on both sites: one nullable column,
`replay_uploads.auto_context`, no row rewritten, and no replay data on the demo. The worker gains its attempt
protocol (`replay_worker/README.md`, "Re-parse attempts") and `/health` gains `recipe` and
`archive.reparse_protocol`. Nothing calls any of it until the switch is on. Either service may deploy
first: the site asks a worker for an attempt only when the worker reports the site's own recipe, archive
and map control on, `control.gaps_protocol` 1 and `archive.reparse_protocol` 1. Anything else, an older
worker included, shows as "stopped" with the reason; nothing is sent on a best guess.

*How an attempt is kept safe.* The site first commits a reservation (a `replay_uploads` row whose id is the
attempt's UUID) and only then asks the worker, which gives that id exactly one job. If the reply is lost,
the site restarts, or the acceptance can't be recorded, the next pass looks the same id up or sends it
again; it never makes a second row or a second parse. An attempt whose acceptance is unknown shows as
`resolving` until the worker answers. Switching off stops new attempts; one the worker already accepted is
still collected, and one it never accepted is closed on the worker so a late request can't start it. While
the site and the worker are on different recipes (a deploy in progress), every attempt is left exactly as
it is, and only a site that matches the worker settles it. A result is stored only if the replay is still
the one that was selected; otherwise the attempt ends and the replay is left alone.

*Pacing.* A file gets at most two accepted attempts per recipe, an hour apart; a refusal before the worker
accepts (no archived file, another recording) uses none. After an attempt settles the queue waits 3 minutes,
and it takes no new replay until the last replaced one has its map control and timing gaps back, read from
the database. If machine failures used up a round's retries, that shows as `restoration pending` with the
rounds still missing, and the queue waits: restart the site (retries are per process) or run
`compute_control.py` for that match before turning on more.

*Status.* `REPLAY_ADMIN_TOKEN=... .venv\Scripts\python.exe scripts\reparse_archive.py --status` prints
running or the stopped reason, both recipes, the worker's capabilities, how many replays are in each state,
any unfinished attempt, what gave up and why, and the rounds waiting. "Running" means switched on with a
matching worker, not that a parse is in progress. It changes nothing.

*Before switching on* (the owner's checks; none was run by the build):

1. Both PRs are deployed, `alembic current` is `0018`, and `--status` shows equal recipes, archive and
   control on, gaps protocol 1, re-parse protocol 1, and no unfinished attempt.
2. Re-parse one match by hand (`reparse_archive.py --match <uuid>`) and check the new replay, its map
   control and gaps once recomputed, and that its per-kill Impact values are still there.
3. Watch the worker's memory while it parses the largest file beside one control child, and note how long
   a parse, a round's control and a round's gaps take.
4. Set `REPLAY_REPARSE_AUTO=true` in the dashboard and watch the first attempt in `--status` and the dispatch
   log (`reparse_reserved`, `reparse_sent`, `reparse_finished`, `held_back`). Restart the site or the worker
   during it once: the same attempt id must come back with one job (`reparse_recovered`).

Then leave it on. `render.yaml` carries it (since 2026-10-08), so a Blueprint sync keeps it.
