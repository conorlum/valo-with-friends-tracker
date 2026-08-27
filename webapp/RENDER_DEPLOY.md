# Deploying to Render — ValoWithFriendsTracker.com

This deploys `webapp/` as its own site, separate from the public repo's future
Riot-application domain. Deliberately zero-auth (anyone with the URL can pick
any seeded player at `/login`).

Both the web app and the Postgres database run on **Render**, in the same
region (Oregon), so the app reaches the DB over Render's internal network.

The DB previously lived on Neon, because Render's free tier allows only one
active free Postgres per account and that slot was already taken. It was
migrated to a paid Render Postgres on 2026-08-26 — see "Migrating the
database" below. Two connection strings matter and they are not
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
