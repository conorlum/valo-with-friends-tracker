<#
Writes and publishes one coaching report on the friends site (scripts/coach_report.py).

Usage:
  .\scripts\coach.ps1 <match uuid> "Name#TAG"
  .\scripts\coach.ps1 <match uuid> "Name#TAG" -DryRun      # print the report, publish nothing

Needs webapp/.env.remote (see scripts/with_friends_db.py) and the claude command on PATH.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Match,
    [Parameter(Mandatory = $true, Position = 1)][string]$Player,
    [string]$Model = "",
    [switch]$DryRun,
    [switch]$PromptOnly
)

$ErrorActionPreference = "Stop"
$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot
$python = Join-Path $webappRoot ".venv313\Scripts\python.exe"
$command = @("scripts\with_friends_db.py", "--expect-database", "valowithfriendsdb", "scripts\coach_report.py", $Match, $Player)
if ($Model) { $command += @("--model", $Model) }
if ($DryRun) { $command += "--dry-run" }
if ($PromptOnly) { $command += "--prompt-only" }
& $python @command
exit $LASTEXITCODE
