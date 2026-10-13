<#
Writes and publishes one coaching report on the friends site (scripts/coach_report.py), or one for every
player on that player's team (scripts/coach_team.py).

Usage:
  .\scripts\coach.ps1 <match uuid> "Name#TAG"
  .\scripts\coach.ps1 <match uuid> "Name#TAG" -DryRun      # print the report, publish nothing
  .\scripts\coach.ps1 <match uuid> "Name#TAG" -Team        # Name#TAG and their four teammates, all at once
  .\scripts\coach.ps1 <match uuid> "Name#TAG" -Team -Jobs 2   # the same, two runs at a time

Needs webapp/.env.remote (see scripts/with_friends_db.py) and the claude command on PATH.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Match,
    [Parameter(Mandatory = $true, Position = 1)][string]$Player,
    [string]$Model = "",
    [switch]$DryRun,
    [switch]$PromptOnly,
    [switch]$Team,
    [int]$Jobs = 0
)

$ErrorActionPreference = "Stop"
$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot
$python = Join-Path $webappRoot ".venv313\Scripts\python.exe"
$entry = if ($Team) { "scripts\coach_team.py" } else { "scripts\coach_report.py" }
$command = @("scripts\with_friends_db.py", "--expect-database", "valowithfriendsdb", $entry, $Match, $Player)
if ($Model) { $command += @("--model", $Model) }
if ($Team -and $Jobs -gt 0) { $command += @("--jobs", $Jobs) }
if ($DryRun) { $command += "--dry-run" }
if ($PromptOnly) { $command += "--prompt-only" }
& $python @command
exit $LASTEXITCODE
