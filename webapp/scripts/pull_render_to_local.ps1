<#
Replaces the local docker-compose Postgres (project "valomaths-private", port 5433)
with an exact copy of the deployed Render database, then proves the copy matches.
The opposite direction of push_dump_to_render.ps1.

Render is authoritative: matches are ingested there (refresh_remote.ps1) and replays
are uploaded there. Local is a disposable copy for analysis -- re-run this whenever
it needs to be current, and do not ingest into local in between, or the two diverge.

Steps:
  1. pg_dump Render (read-only) and snapshot it: alembic head, latest match, every
     table's row count, every sequence position. Nothing local is touched until the
     dump has succeeded.
  2. WIPE the local database (docker compose down -v) and recreate it empty.
  3. pg_restore the dump into it.
  4. Snapshot local the same way and diff it against Render's. Any difference fails.
  5. Rebuild the impact-eval observation cache (scripts/cache_observations.py).

Requires webapp/.env.remote (gitignored -- this repository is public) with Render's
EXTERNAL connection string:
    DATABASE_URL=<your Render connection string>
The URL is never printed. Requires Docker Desktop running; pg_dump/pg_restore run in
postgres:18 containers, so no PostgreSQL client install is needed.

Dumps are kept OUTSIDE the repo (they hold production data and this repo is public),
in -DumpDir, newest -Keep of them. They double as restore points older than Render's
own point-in-time-recovery window.

Usage:
  .\scripts\pull_render_to_local.ps1
  .\scripts\pull_render_to_local.ps1 -SkipCache    # skip step 5
#>

param(
    [string]$DumpDir = (Join-Path $env:LOCALAPPDATA "valomaths\render-dumps"),
    [int]$Keep = 3,
    [string]$LocalDbName = "valorant_igl_tutor",
    [string]$LocalDbUser = "valorant",
    [switch]$SkipCache
)

$ErrorActionPreference = "Stop"
$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot

function Invoke-Native([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)." }
}

# --- Preflight --------------------------------------------------------------
$envRemotePath = Join-Path $webappRoot ".env.remote"
if (-not (Test-Path $envRemotePath)) {
    throw ".env.remote not found at $envRemotePath. Create it with one line:`n    DATABASE_URL=<your Render connection string>"
}
$remoteUrl = $null
foreach ($line in Get-Content $envRemotePath) {
    if ($line -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
        $remoteUrl = $Matches[1].Trim()
        break
    }
}
if (-not $remoteUrl) { throw ".env.remote exists but has no DATABASE_URL=... line." }
if ($remoteUrl -notlike "*render.com*") { throw "DATABASE_URL in .env.remote is not a Render host; stopping." }

docker info *>$null
if ($LASTEXITCODE -ne 0) { throw "Docker engine is not answering. Start Docker Desktop first." }

New-Item -ItemType Directory -Force -Path $DumpDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$dumpName = "render-$stamp.dump"
$renderSnapName = "render-$stamp.snapshot.txt"
$localSnapName = "local-$stamp.snapshot.txt"

# --- 1. Dump Render ---------------------------------------------------------
Write-Host "1/5 Dumping Render (read-only) to $DumpDir\$dumpName ..."
$env:RURL = $remoteUrl
try {
    Invoke-Native "pg_dump of Render" {
        docker run --rm -e RURL -v "${DumpDir}:/dumps" -v "${PSScriptRoot}:/scripts:ro" postgres:18 `
            sh /scripts/pull_render_to_local.sh dump "/dumps/$dumpName" "/dumps/$renderSnapName"
    }
} finally {
    Remove-Item Env:\RURL -ErrorAction SilentlyContinue
}
$dumpPath = Join-Path $DumpDir $dumpName
if (-not (Test-Path $dumpPath) -or (Get-Item $dumpPath).Length -eq 0) {
    throw "Dump file is missing or empty; local database left untouched."
}
Write-Host ("    dump is {0:N0} MB" -f ((Get-Item $dumpPath).Length / 1MB))

# --- 2. Wipe and recreate local ---------------------------------------------
Write-Host "2/5 Wiping and recreating the local database ..."
Invoke-Native "docker compose down -v" { docker compose -p valomaths-private down -v }
Invoke-Native "docker compose up -d" { docker compose -p valomaths-private up -d }
# Probe over TCP: the image's first-boot init runs a temporary server on the Unix
# socket only, then restarts, so a socket probe can pass before the real server is up.
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    docker compose -p valomaths-private exec -T postgres pg_isready -h 127.0.0.1 -U $LocalDbUser -d $LocalDbName *>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw "Local Postgres did not come up within 2 minutes. The dump is safe at $dumpPath." }

# --- 3. Restore -------------------------------------------------------------
Write-Host "3/5 Restoring the dump into local ..."
Invoke-Native "docker compose cp (dump)" { docker compose -p valomaths-private cp $dumpPath "postgres:/tmp/render.dump" }
Invoke-Native "pg_restore" {
    docker compose -p valomaths-private exec -T postgres `
        pg_restore --no-owner --no-privileges --exit-on-error --jobs 4 -U $LocalDbUser -d $LocalDbName /tmp/render.dump
}
docker compose -p valomaths-private exec -T postgres rm -f /tmp/render.dump *>$null

# --- 4. Verify --------------------------------------------------------------
Write-Host "4/5 Comparing local against Render's snapshot ..."
Invoke-Native "docker compose cp (helper)" {
    docker compose -p valomaths-private cp (Join-Path $PSScriptRoot "pull_render_to_local.sh") "postgres:/tmp/pull_render_to_local.sh"
}
Invoke-Native "local snapshot" {
    docker compose -p valomaths-private exec -T -e "RURL=postgresql://${LocalDbUser}@localhost/${LocalDbName}" postgres `
        sh /tmp/pull_render_to_local.sh snapshot "/tmp/$localSnapName"
}
Invoke-Native "docker compose cp (snapshot)" {
    docker compose -p valomaths-private cp "postgres:/tmp/$localSnapName" (Join-Path $DumpDir $localSnapName)
}
$renderSnap = @(Get-Content (Join-Path $DumpDir $renderSnapName) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$localSnap = @(Get-Content (Join-Path $DumpDir $localSnapName) | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$diff = Compare-Object $renderSnap $localSnap
if ($diff) {
    $diff | Format-Table -AutoSize | Out-String | Write-Host
    throw "Local does not match Render's snapshot (<= Render only, => local only)."
}
Write-Host "    identical on all $($renderSnap.Count) lines:"
$renderSnap | Where-Object { $_ -match '^(alembic|max_played_at|table matches |table replay_rounds )' } |
    ForEach-Object { Write-Host "      $_" }

# --- 5. Observation cache ---------------------------------------------------
if ($SkipCache) {
    Write-Host "5/5 Skipped the observation cache (-SkipCache)."
} else {
    Write-Host "5/5 Rebuilding the observation cache ..."
    Invoke-Native "cache_observations.py" { & ".\.venv\Scripts\python.exe" "scripts\cache_observations.py" }
}

# --- Retention --------------------------------------------------------------
Get-ChildItem $DumpDir -Filter "render-*.dump" | Sort-Object Name -Descending | Select-Object -Skip $Keep |
    ForEach-Object {
        $prefix = $_.BaseName.Substring("render-".Length)
        Remove-Item $_.FullName
        Remove-Item (Join-Path $DumpDir "render-$prefix.snapshot.txt"), (Join-Path $DumpDir "local-$prefix.snapshot.txt") -ErrorAction SilentlyContinue
    }

Write-Host "Done. Local now matches Render."
