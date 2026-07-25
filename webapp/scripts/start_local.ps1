<#
Brings up the private valomaths webapp from a cold machine state:
Docker Desktop -> Postgres container -> Alembic migrations -> Uvicorn dev server.
#>

$ErrorActionPreference = "Stop"

$webappRoot = Split-Path -Parent $PSScriptRoot
$dockerDesktopExe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
$dockerCli = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"

function Test-DockerRunning {
    try {
        & $dockerCli version --format '{{.Server.Version}}' *>$null
        return $LASTEXITCODE -eq 0
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

Write-Host "Starting Uvicorn (http://127.0.0.1:8000)..."
& $python -m uvicorn app.main:app --reload
