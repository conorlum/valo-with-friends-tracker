# Deploying to Render — ValoWithFriendsTracker.com

This deploys `webapp/` as its own site, separate from the public repo's future
Riot-application domain. Deliberately zero-auth (anyone with the URL can pick
any seeded player at `/login`).

Both the web app and the Postgres database run on Render.

## (a) One-time initial setup

1. **Render**: in the dashboard, **New → PostgreSQL** (Postgres 16 to match
   local `docker-compose.yml`'s `postgres:16`). Copy the **External Database
   URL** it gives you — it'll look like
   `postgresql://user:pass@dpg-xxx.oregon-postgres.render.com/dbname`.
2. **Render**: **New → Blueprint**, connect the `valo-with-friends-tracker`
   GitHub repo. Render auto-detects `render.yaml` at the repo root. This
   creates the web service (`valowithfriendstracker`).
3. On the web service's **Environment** tab, set `DATABASE_URL` to the
   connection string from step 1, appending `?sslmode=require` (the blueprint
   leaves it as `sync: false` on purpose so the real value only lives in the
   dashboard, never in a committed file).
4. Deploy (or redeploy after saving the env var). The build runs `pip install`
   then `alembic upgrade head` against the Render DB — schema only, zero rows.
5. Load real data: make sure your local Postgres is up and populated
   (`docker compose -p valomaths-private up -d`, then your usual tracker.gg
   ingestion). From `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<render-connection-string>"
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

Two ways to get newly ingested matches onto the live Render DB, depending on
how much you want to redo:

**Incremental (preferred day to day)** — the `matches` PowerShell function
calls `scripts/refresh_render.ps1` directly (no Docker/local Postgres/Alembic
involved -- it only launches the tracker.gg Chrome profile if needed and
pushes straight to Render). It's idempotent (dedupes by
`matches.external_id`), so it only adds what's missing rather than touching
existing rows. Requires `webapp/.env.render` to hold
`DATABASE_URL=<render-connection-string>?sslmode=require`. Can also be run
directly:
```powershell
.\scripts\refresh_render.ps1 -Count 10
```

(`scripts/refresh_matches.ps1` still exists separately for refreshing your
*local* dev DB -- Docker, local Postgres, Alembic migrations, local ingest --
and finishes by calling `refresh_render.ps1` too, but `matches` no longer
goes through it.)

**Full dump/replace (initial load, or to force the live DB back in sync with
local)** — drops and restores the entire live DB from a dump of local
Postgres:
1. Ingest locally as usual (`scripts/refresh_tracked_players.py` /
   `scripts/ingest_trackergg_player.py` against the local Chrome/CDP session).
2. Spot-check locally (`scripts/start_local.ps1` or your usual local run) that
   the new data looks right.
3. From `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<render-connection-string>"
   ```
   (Same connection string as always — it doesn't rotate or expire, so
   there's no "grab a fresh URL" step here.)
4. No redeploy needed — this is a data-only push into the existing DB. Spot-check
   the live site.
