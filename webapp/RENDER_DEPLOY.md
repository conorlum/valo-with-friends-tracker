# Deploying to Render — DontTellRiotTracker.com

This deploys `webapp/` as its own site, separate from the public repo's future
Riot-application domain. Deliberately zero-auth (anyone with the URL can pick
any seeded player at `/login`).

The web app runs on Render; the Postgres database runs on **Neon** (its own
account, separate from Render). Render's free tier only allows one active free
Postgres per account, and this account's slot was already in use, so the DB
lives elsewhere instead of fighting that limit. Neon's free tier doesn't get
hard-deleted on a schedule the way Render's does — the main quirk is that a
Neon project's compute auto-suspends after a period of inactivity and takes a
few seconds to wake back up on the next request (a cold-start delay, not data
loss).

## (a) One-time initial setup

1. **Neon**: sign up, create a project (Postgres 16 to match local
   `docker-compose.yml`'s `postgres:16`). Copy the connection string it gives
   you — it'll look like `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`.
2. **Render**: in the dashboard, **New → Blueprint**, connect the
   `valomaths-private` GitHub repo. Render auto-detects `render.yaml` at the
   repo root. This creates the web service (`donttellriottracker`) — there's
   no `databases:` block anymore, so Render won't try to provision its own
   Postgres.
3. On the web service's **Environment** tab, set `DATABASE_URL` to the Neon
   connection string from step 1 (the blueprint leaves it as `sync: false` on
   purpose so the real value only lives in the dashboard, never in a committed
   file).
4. Deploy (or redeploy after saving the env var). The build runs `pip install`
   then `alembic upgrade head` against the Neon DB — schema only, zero rows.
5. Load real data: make sure your local Postgres is up and populated
   (`docker compose -p valomaths-private up -d`, then your usual tracker.gg
   ingestion). From `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<neon-connection-string>"
   ```
6. Verify: `https://<render-service>.onrender.com/health` should return
   `{"status": "ok"}`. Log in as a seeded player and spot-check a sessions page.
7. Custom domain: web service → **Settings → Custom Domains** → add
   `DontTellRiotTracker.com` (and `www.DontTellRiotTracker.com`). Render will
   show the DNS records to add (typically an ALIAS/ANAME or A record for the
   apex, CNAME for `www`) — add them at wherever the domain is registered/DNS
   is managed. Render auto-provisions a TLS cert once DNS resolves; this can
   take anywhere from minutes to a few hours.
8. Confirm `SESSION_SECRET` was auto-generated (web service → **Environment**)
   rather than left as the code's `dev-only-change-me` default — the blueprint's
   `generateValue: true` should have handled this automatically.

## (b) Routine workflow — pushing freshly ingested matches live

1. Ingest locally as usual (`scripts/refresh_tracked_players.py` /
   `scripts/ingest_trackergg_player.py` against the local Chrome/CDP session).
2. Spot-check locally (`scripts/start_local.ps1` or your usual local run) that
   the new data looks right.
3. From `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<neon-connection-string>"
   ```
   (Same Neon connection string as always — it doesn't rotate or expire, so
   there's no equivalent of Render's "grab a fresh URL" step here.)
4. No redeploy needed — this is a data-only push into the existing DB. Spot-check
   the live site.
