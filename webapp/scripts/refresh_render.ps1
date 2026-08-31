<#
One-command refresh of the deployed (Render) database from the tracker.gg
roster in scripts/tracked_players.json -- same idempotent, dedup-by-
external_id ingestion pipeline the local refresh uses (see
refresh_tracked_players.py), just pointed at Render's Postgres instead of
local Postgres. This only ever ADDS matches Render doesn't already have; it
is never a destructive replace (contrast with push_dump_to_render.ps1, which
drops and replaces everything).

Requires webapp/.env.render to exist with one line:
    DATABASE_URL=<your Render external Postgres connection string>?sslmode=require

Also launches the dedicated tracker.gg Chrome profile
(launch_trackergg_chrome.ps1) automatically if it isn't already running,
so this is a genuine one-command refresh after the very first run (which
still needs you to log into tracker.gg once in the window that opens --
the login persists in that profile after that).

Usage:
  .\scripts\refresh_render.ps1              # last 5 matches per tracked player (default)
  .\scripts\refresh_render.ps1 -Count 10    # last 10 matches per tracked player
#>

param(
    [int]$Count = 5
)

$ErrorActionPreference = "Stop"
$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot

# --- Load the Render connection string from the gitignored local file ------
$envRenderPath = Join-Path $webappRoot ".env.render"
if (-not (Test-Path $envRenderPath)) {
    throw ".env.render not found at $envRenderPath. Create it with one line:`n    DATABASE_URL=<your Render external Postgres connection string>?sslmode=require"
}

$renderUrl = $null
foreach ($line in Get-Content $envRenderPath) {
    if ($line -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
        $renderUrl = $Matches[1].Trim()
        break
    }
}
if (-not $renderUrl) {
    throw ".env.render exists but has no DATABASE_URL=... line."
}

# --- Make sure the tracker.gg Chrome profile (remote debugging on 9222) is up
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 2 | Out-Null
    Write-Host "tracker.gg Chrome profile already running."
} catch {
    Write-Host "Launching tracker.gg Chrome profile..."
    & (Join-Path $PSScriptRoot "launch_trackergg_chrome.ps1")
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {}
    }
    if (-not $ready) {
        throw "Chrome didn't come up with remote debugging on port 9222 within 20s -- check it launched correctly."
    }
}

# --- Make sure tracker.gg is actually open in a tab, whether Chrome was just
# launched (launch_trackergg_chrome.ps1 already opens it) or was already
# running from an earlier session (where it may have been navigated away or
# closed) -- so `matches` always leaves you looking at tracker.gg.
try {
    $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/list" -TimeoutSec 3
    $hasTrackerTab = $tabs | Where-Object { $_.url -like "https://tracker.gg*" }
    if (-not $hasTrackerTab) {
        Write-Host "Opening tracker.gg tab..."
        Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/new?https://tracker.gg/valorant" -Method PUT -UseBasicParsing -TimeoutSec 3 | Out-Null
    }
} catch {
    Write-Host "Warning: couldn't confirm/open a tracker.gg tab via CDP ($_)."
}

# --- Run the existing batch refresh, DATABASE_URL overridden to Render -----
# (env var wins over .env's own DATABASE_URL per pydantic-settings' default
# precedence -- webapp/.env itself is untouched, this only affects this one
# child process.)
Write-Host "Refreshing Render with the last $Count match(es) per tracked player..."
$env:DATABASE_URL = $renderUrl
try {
    & ".\.venv\Scripts\python.exe" "scripts\refresh_tracked_players.py" --count $Count
} finally {
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
}
