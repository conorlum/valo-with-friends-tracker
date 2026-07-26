<#
Dumps the local docker-compose Postgres (project "valomaths-private") and restores
it into a target database -- typically a Render Postgres instance's *external*
connection string, copied fresh from the Render dashboard.

Reusable both for the initial data load onto a brand-new Render Postgres, and for
every subsequent refresh (re-run after ingesting new matches locally) or ~30-day
free-tier recreate (after creating a new Render Postgres instance).

Requires: Docker Desktop running, and the local Postgres already up
(docker compose -p valomaths-private up -d). Does NOT require pg_dump/psql/pg_restore
installed locally -- both run inside postgres:16 containers.

Usage:
  .\push_dump_to_render.ps1 -TargetDatabaseUrl "postgresql://user:pass@host/db"
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

Write-Host "Dumping local DB ($LocalDbName)..."
docker compose -p valomaths-private exec -T postgres pg_dump -U $LocalDbUser -d $LocalDbName --format=custom --no-owner --no-privileges > $dumpFile
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed." }
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
