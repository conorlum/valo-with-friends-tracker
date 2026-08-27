<#
One-command tracker.gg roster refresh: brings up Docker Desktop -> Postgres
container -> Alembic migrations -> the dedicated debug-Chrome profile, then
runs refresh_tracked_players.py against the whole scripts/tracked_players.json
roster. Each stage is skipped if it's already up, so re-runs in the same
session get progressively cheaper. Finishes by calling refresh_remote.ps1 with
the same -Count, so the live (Render) database gets the same new matches --
no separate manual step needed.

Usage:
    powershell -File scripts\refresh_matches.ps1
    powershell -File scripts\refresh_matches.ps1 -Count 10
#>

param(
    [int]$Count = 20
)

$ErrorActionPreference = "Stop"

$webappRoot = Split-Path -Parent $PSScriptRoot
$dockerDesktopExe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
$dockerCli = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
$cdpVersionUrl = "http://127.0.0.1:9222/json/version"

function Test-DockerRunning {
    try {
        & $dockerCli version --format '{{.Server.Version}}' *>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-CdpRunning {
    # Use 127.0.0.1, not localhost -- .NET's HTTP stack tries the IPv6
    # loopback leg first, and on this machine that leg stalls until timeout
    # before ever falling back to IPv4, which silently ate the whole per-poll
    # budget on every attempt even once Chrome's CDP server was actually up.
    try {
        Invoke-WebRequest -Uri $cdpVersionUrl -UseBasicParsing -TimeoutSec 3 *>$null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-DockerRunning)) {
    Write-Host "Docker engine not responding, starting Docker Desktop..."
    if (-not (Test-Path $dockerDesktopExe)) {
        throw "Docker Desktop.exe not found at $dockerDesktopExe -- adjust the path in this script."
    }
    Start-Process -FilePath $dockerDesktopExe

    $waited = 0
    $timeoutSeconds = 120
    while (-not (Test-DockerRunning)) {
        if ($waited -ge $timeoutSeconds) {
            throw "Docker engine did not come up within $timeoutSeconds seconds."
        }
        Start-Sleep -Seconds 3
        $waited += 3
        Write-Host "  waiting for Docker engine... ($waited s)"
    }
    Write-Host "Docker engine is up."
} else {
    Write-Host "Docker engine already running."
}

Set-Location $webappRoot

Write-Host "Starting Postgres (project: valomaths-private, host port 5433)..."
docker compose -p valomaths-private up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

Write-Host "Waiting for Postgres to accept connections..."
$pgWaited = 0
$pgTimeout = 60
while ($true) {
    docker compose -p valomaths-private exec -T postgres pg_isready -U valorant *>$null
    if ($LASTEXITCODE -eq 0) { break }
    if ($pgWaited -ge $pgTimeout) {
        throw "Postgres did not become ready within $pgTimeout seconds."
    }
    Start-Sleep -Seconds 2
    $pgWaited += 2
    Write-Host "  waiting for Postgres... ($pgWaited s)"
}
Write-Host "Postgres is ready."

$python = Join-Path $webappRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw ".venv not found at $python -- create it and install requirements.txt first."
}

Write-Host "Applying Alembic migrations..."
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed." }

if (Test-CdpRunning) {
    Write-Host "Debug Chrome profile already running (CDP responding on 9222)."
} else {
    # A stale singleton lock in the debug profile (left behind if that Chrome
    # process was killed/crashed rather than closed cleanly) makes a fresh
    # launch silently hand off to "an existing browser session" instead of
    # actually starting with --remote-debugging-port. Since CDP isn't
    # responding, no legitimate owner of the profile is running -- clear any
    # lock files and stray processes for it before relaunching.
    $debugProfileDir = "$env:LOCALAPPDATA\ValoMathsScraper\ChromeProfile"
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains("ValoMathsScraper") } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($lockName in @("lockfile", "SingletonLock", "SingletonSocket", "SingletonCookie")) {
        Remove-Item -Path (Join-Path $debugProfileDir $lockName) -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Launching debug Chrome profile..."
    & powershell -File (Join-Path $PSScriptRoot "launch_trackergg_chrome.ps1")

    $cdpWaited = 0
    $cdpTimeout = 45
    while (-not (Test-CdpRunning)) {
        if ($cdpWaited -ge $cdpTimeout) {
            throw "Debug Chrome profile's CDP port did not come up within $cdpTimeout seconds."
        }
        Start-Sleep -Seconds 2
        $cdpWaited += 2
        Write-Host "  waiting for CDP port 9222... ($cdpWaited s)"
    }
    Write-Host "Debug Chrome profile is up."
}

Write-Host "Refreshing tracked_players.json roster (count=$Count per player)..."
& $python "scripts\refresh_tracked_players.py" --count $Count
if ($LASTEXITCODE -ne 0) { throw "refresh_tracked_players.py failed." }

Write-Host "Refreshing the remote DB (live site) with the same roster..."
& powershell -File (Join-Path $PSScriptRoot "refresh_remote.ps1") -Count $Count
if ($LASTEXITCODE -ne 0) { throw "refresh_remote.ps1 failed." }

Write-Host "Done."
