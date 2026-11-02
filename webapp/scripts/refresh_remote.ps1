<#
One-command refresh of the deployed (Render) database from the tracker.gg
roster in scripts/tracked_players.json -- same idempotent, dedup-by-
external_id ingestion pipeline the local refresh uses (see
refresh_tracked_players.py), just pointed at the remote DB instead of local
Postgres. This only ever ADDS matches the remote doesn't already have; it is
never a destructive replace (contrast with push_dump_to_render.ps1, which
drops and replaces everything).

Requires webapp/.env.remote to exist (gitignored -- this repository is public,
so never commit it) with one line:
    DATABASE_URL=<your Render connection string>

Use Render's EXTERNAL connection string here, not the internal one -- the
internal hostname only resolves from inside Render's own network, so it
works for the deployed web service but not for this script.

Also launches the dedicated tracker.gg Chrome profile
(launch_trackergg_chrome.ps1) automatically if it isn't already running,
so this is a genuine one-command refresh after the very first run (which
still needs you to log into tracker.gg once in the window that opens --
the login persists in that profile after that).

-Count is a target, not a ceiling: discovery pages through tracker.gg's
"Load More" control across the All-Acts history view, so counts above 20
now reach past the single server-rendered batch (which holds exactly 20 and
is scoped to the CURRENT act). Budget roughly 14s per 20 matches discovered
per player, plus the usual 5-12s per newly ingested match.

The run prints a per-player REACHED/REQUESTED table at the end and exits 2
if any player fell short without hitting the end of their history.

Usage:
  .\scripts\refresh_remote.ps1              # last 5 matches per tracked player (default)
  .\scripts\refresh_remote.ps1 -Count 10    # last 10 matches per tracked player
  .\scripts\refresh_remote.ps1 -Count 100   # 100 per player (~70s discovery each)
#>

param(
    [int]$Count = 5
)

$ErrorActionPreference = "Stop"
$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot

# --- Bring this checkout up to date with origin/main ------------------------
# Other sessions merge migrations from their own worktrees and the deploy
# applies them to the remote DB, so a main checkout left behind fails the
# ingest preflight ("the database is at migration X but this checkout's head
# is Y"). Fast-forward first. Local edits to files the update touches are
# stashed under a tagged message, never discarded.
$repoRoot = Split-Path -Parent $webappRoot
$branch = (git -C $repoRoot rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") {
    Write-Warning "This checkout is on '$branch', not main -- not updating it. If the ingest preflight refuses, switch it back to main."
} else {
    git -C $repoRoot fetch -q origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Couldn't fetch origin/main -- running with this checkout as it is."
    } else {
        $behind = [int](git -C $repoRoot rev-list --count HEAD..origin/main)
        if ($behind -gt 0) {
            $incoming = @(git -C $repoRoot diff --name-only HEAD origin/main)
            $local = @(git -C $repoRoot diff --name-only HEAD) + @(git -C $repoRoot ls-files --others --exclude-standard)
            $blocking = @($incoming | Where-Object { $local -contains $_ })
            if ($blocking.Count -gt 0) {
                $tag = "matches auto-stash $(Get-Date -Format s)"
                git -C $repoRoot stash push -q -u -m $tag -- $blocking
                if ($LASTEXITCODE -ne 0) { throw "Couldn't stash local changes to: $($blocking -join ', ')" }
                Write-Warning ("Stashed your local changes to $($blocking -join ', ') as '$tag' " +
                               "so main could update. See them with: git stash list")
            }
            git -C $repoRoot merge -q --ff-only origin/main
            if ($LASTEXITCODE -ne 0) {
                throw "main can't fast-forward to origin/main (it has local commits?). Sort that out, then re-run."
            }
            Write-Host "Updated main by $behind commit(s) from origin."
        }
    }
}

# --- Load the remote connection string from the local env file -------------
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
if (-not $remoteUrl) {
    throw ".env.remote exists but has no DATABASE_URL=... line."
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
# closed) -- so this always leaves you looking at tracker.gg.
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

# --- Run the existing batch refresh, DATABASE_URL overridden to the remote --
# (env var wins over .env's own DATABASE_URL per pydantic-settings' default
# precedence -- webapp/.env itself is untouched, this only affects this one
# child process.)
Write-Host "Refreshing the remote DB with the last $Count match(es) per tracked player..."
$env:DATABASE_URL = $remoteUrl
try {
    # Python 3.13 (.venv313): the release decision for scoring (rc3 plan v2, D3).
    # Ingestion's preflight also refuses any environment whose installed
    # packages differ from the frozen candidate's, which .venv's do.
    $python = ".\.venv313\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw ".venv313 not found -- create it with Python 3.13 and install requirements.txt first."
    }
    & $python "scripts\refresh_tracked_players.py" --count $Count
    # Exit 2 means the run worked but did not reach -Count for some player
    # (see refresh_tracked_players.py's EXIT_INCOMPLETE). That is a warning,
    # not a crash -- but it is never silent, because "100 requested, 20
    # returned" printing as plain success is exactly the bug this guards.
    if ($LASTEXITCODE -eq 2) {
        Write-Warning ("Refresh finished INCOMPLETE -- at least one player did not reach " +
                       "$Count matches. See the DISCOVERY REPORT above for the per-player " +
                       "reason; re-run to continue where it left off (ingestion is " +
                       "idempotent and dedupes on external_id).")
    } elseif ($LASTEXITCODE -ne 0) {
        throw "refresh_tracked_players.py failed (exit $LASTEXITCODE)."
    }
} finally {
    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
}
