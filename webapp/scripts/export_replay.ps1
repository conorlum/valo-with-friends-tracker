# Exports one archived replay with the pinned parser build.
#
#   .\scripts\export_replay.ps1 <match uuid>      # the .vrf's file name without the extension
#
# Reads <ArchiveDir>\<uuid>.vrf (default $env:VALO_REPLAY_ARCHIVE, else
# $HOME\ValorantReplayArchive), runs <ParserDir>\bin\CliReader.exe export, and
# writes events.ndjson, movement.ndjson and manifest.json into
# $env:TEMP\valo-replay\<uuid>\ (or -OutDir). Prints one line with the exit code,
# wall time, peak working set and the .vrf size, for the worker's sizing.
#
# The binary is called with & so each path is passed as one argument even when it
# contains a space (Start-Process -ArgumentList does not quote array elements in
# Windows PowerShell 5.1, which is what broke the first Stage 1a export).
#
# Third-party code, pinned in replay_parser.json. The user or the agent may run it (docs/replay-viewer-plan.md).
param(
    [Parameter(Mandatory = $true)][string]$Uuid,
    [string]$ArchiveDir = $(if ($env:VALO_REPLAY_ARCHIVE) { $env:VALO_REPLAY_ARCHIVE } else { Join-Path $HOME 'ValorantReplayArchive' }),
    [string]$ParserDir = $(if ($env:REPLAY_PARSER_DIR) { $env:REPLAY_PARSER_DIR } else { Join-Path $HOME 'rp\parser' }),
    [string]$OutDir = $(Join-Path $env:TEMP "valo-replay\$Uuid")
)
$ErrorActionPreference = 'Stop'

if ($Uuid -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
    throw "not a match UUID: $Uuid"
}
$vrf = Join-Path $ArchiveDir "$Uuid.vrf"
if (-not (Test-Path $vrf)) { throw "not in the archive: $vrf" }
$exe = Join-Path $ParserDir 'bin\CliReader.exe'
if (-not (Test-Path $exe)) { throw "no parser build at $exe; run scripts\build_replay_parser.ps1 first" }

$pin = Get-Content -Raw (Join-Path $PSScriptRoot '..\replay_parser.json') | ConvertFrom-Json
$build = Get-Content -Raw (Join-Path $ParserDir 'bin\BUILD.json') | ConvertFrom-Json
if ($build.commit -ne $pin.commit) {
    throw "parser build is $($build.commit), replay_parser.json pins $($pin.commit); rebuild"
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$start = Get-Date
$job = Start-Job -ScriptBlock { param($exe, $vrf, $out)
    $env:VALO_MAP_AUDIT_RAW = '1'  # scoped to this child job; an older binary produces an unavailable state panel
    & $exe export $vrf --output $out *>&1
    $LASTEXITCODE
} -ArgumentList $exe, $vrf, $OutDir
$peak = 0
while ($job.State -eq 'Running') {
    $p = Get-Process CliReader -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($p) { $peak = [Math]::Max($peak, $p.PeakWorkingSet64) }
    Start-Sleep -Milliseconds 200
}
$output = Receive-Job $job
Remove-Job $job
$exitCode = $output | Select-Object -Last 1
$output | Select-Object -SkipLast 1 | ForEach-Object { Write-Host $_ }
$seconds = ((Get-Date) - $start).TotalSeconds
"exit $exitCode seconds $([Math]::Round($seconds, 2)) peakMB $([int]($peak / 1MB)) size $((Get-Item $vrf).Length)"
