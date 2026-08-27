<#
Dumps the local docker-compose Postgres (project "valomaths-private") and restores
it into a target database -- e.g. the deployed Render Postgres (pass its EXTERNAL
connection string; the internal hostname only resolves inside Render's network).

NOTE: this requires Docker, which is not installed on the current machine. To dump
or restore against the deployed DB directly, use native PostgreSQL 18 client
binaries instead (pg_dump/pg_restore must be >= the server's major version).

Reusable both for the initial data load and every subsequent refresh (re-run
after ingesting new matches locally) -- just pass the same target URL each time.

Requires: Docker Desktop running, and the local Postgres already up
(docker compose -p valomaths-private up -d). Does NOT require pg_dump/psql/pg_restore
installed locally -- both run inside postgres:16 containers.

Usage:
  .\push_dump_to_render.ps1 -TargetDatabaseUrl "postgresql://user:pass@host/db?sslmode=require"
  .\push_dump_to_render.ps1 -TargetDatabaseUrl "..." -DryRun    # dump only, skip restore
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDatabaseUrl,

    [string]$LocalDbName = "valorant_igl_tutor",
    [string]$LocalDbUser = "valorant",

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot

docker compose -p valomaths-private exec -T postgres pg_isready -U $LocalDbUser *>$null
if ($LASTEXITCODE -ne 0) {
    throw "Local postgres container not responding. Start it with: docker compose -p valomaths-private up -d"
}

$dumpsDir = Join-Path $webappRoot "scripts\.dumps"
New-Item -ItemType Directory -Force -Path $dumpsDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dumpFile = Join-Path $dumpsDir "valomaths-$stamp.dump"

$containerId = (docker compose -p valomaths-private ps -q postgres).Trim()
if (-not $containerId) { throw "Could not resolve the local postgres container." }

Write-Host "Dumping local DB ($LocalDbName)..."
# Dump to a file *inside* the container, then docker cp it out -- piping binary
# pg_dump output through PowerShell's `>` redirection corrupts it (text reinterpretation).
docker exec $containerId pg_dump -U $LocalDbUser -d $LocalDbName --format=custom --no-owner --no-privileges -f /tmp/dump.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed." }
docker cp "${containerId}:/tmp/dump.dump" $dumpFile
if ($LASTEXITCODE -ne 0) { throw "docker cp of dump file failed." }
Write-Host "Dump saved to $dumpFile"

if ($DryRun) {
    Write-Host "Dry run: skipping restore."
    exit 0
}

Write-Host "Restoring into target database..."
docker run --rm `
    -v "${dumpFile}:/tmp/restore.dump:ro" `
    postgres:16 `
    pg_restore --clean --if-exists --no-owner --no-privileges `
    --dbname="$TargetDatabaseUrl" /tmp/restore.dump
if ($LASTEXITCODE -ne 0) { throw "pg_restore failed." }

Write-Host "Done. Restored $dumpFile into target database."
