# Deploying to Render — DontTellRiotTracker.com

This deploys `webapp/` as its own site, separate from the public repo's future
Riot-application domain. Deliberately zero-auth (anyone with the URL can pick
any seeded player at `/login`) and on Render's free Postgres tier (which gets
hard-deleted 30 days after creation) — see `CLAUDE.md` and the plan history for why.

## (a) One-time initial setup

1. In the Render dashboard: **New → Blueprint**, connect the `valomaths-private`
   GitHub repo, point it at `webapp/render.yaml`. This creates the web service
   (`donttellriottracker`) and the free Postgres (`donttellriottracker-db`)
   together in one action.
2. Wait for the first deploy to finish. The build runs `pip install` then
   `alembic upgrade head` against the brand-new (empty) Postgres — schema only,
   zero rows.
3. Load real data: make sure your local Postgres is up and populated
   (`docker compose -p valomaths-private up -d`, then your usual tracker.gg
   ingestion). In the Render dashboard, open the `donttellriottracker-db`
   Postgres service → **Connections** → copy the **External Database URL**.
   Then from `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<external-url-from-render>"
   ```
4. Verify: `https://<render-service>.onrender.com/health` should return
   `{"status": "ok"}`. Log in as a seeded player and spot-check a sessions page.
5. Custom domain: web service → **Settings → Custom Domains** → add
   `DontTellRiotTracker.com` (and `www.DontTellRiotTracker.com`). Render will
   show the DNS records to add (typically an ALIAS/ANAME or A record for the
   apex, CNAME for `www`) — add them at wherever the domain is registered/DNS
   is managed. Render auto-provisions a TLS cert once DNS resolves; this can
   take anywhere from minutes to a few hours.
6. Confirm `SESSION_SECRET` was auto-generated (web service → **Environment**)
   rather than left as the code's `dev-only-change-me` default — the blueprint's
   `generateValue: true` should have handled this automatically.

## (b) Routine workflow — pushing freshly ingested matches live

1. Ingest locally as usual (`scripts/refresh_tracked_players.py` /
   `scripts/ingest_trackergg_player.py` against the local Chrome/CDP session).
2. Spot-check locally (`scripts/start_local.ps1` or your usual local run) that
   the new data looks right.
3. Copy the **current** Render Postgres external connection string from the
   dashboard (same one as before, unless it's been recreated — see section c).
4. From `webapp/`:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<current-render-external-url>"
   ```
5. No redeploy needed — this is a data-only push into the existing DB. Spot-check
   the live site.

## (c) ~30-day workflow — free Postgres got deleted, recreate it

Render's free-tier Postgres instances are deleted 30 days after creation. This
is expected to recur roughly monthly for as long as the free tier is used.

1. **Detect**: watch for `/health` failing (DB connection refused), a Render
   dashboard/email notice, or just set yourself a reminder ~28 days after each
   recreation rather than waiting to notice an outage.
2. **Recreate the database**: in the Render dashboard, create a new Postgres
   instance (free tier). It'll have a new name/id — that's expected on free tier.
3. **Update `DATABASE_URL`**: on the web service's **Environment** tab, point
   `DATABASE_URL` at the new instance's *internal* connection string (re-link
   via the dashboard's database picker if it offers one, otherwise paste the
   internal URL directly). Save — this triggers a redeploy, which runs the
   normal build command (`alembic upgrade head`) against the fresh DB
   automatically, recreating the schema. No manual Alembic step needed.
4. **Restore data**: copy the new instance's *external* connection string and
   run the same push script again:
   ```powershell
   .\scripts\push_dump_to_render.ps1 -TargetDatabaseUrl "<new-render-external-url>"
   ```
5. **Verify**: `/health` and a player page.

If this monthly chore becomes too disruptive, the only structural fix is
upgrading the Postgres plan to paid (persists indefinitely) — deliberately
not done here.
