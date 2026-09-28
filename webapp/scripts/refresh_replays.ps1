<#
One command for a night's replays: crawl the newest matches, archive the replay folder, then
upload the newest replays the friends site doesn't have yet.

  1. refresh_remote.ps1 -Count N   (the `matches N` crawl), so the replays' matches are in the
     DB and each upload links to its match as it is stored;
  2. archive_replays.py            (copies new .vrf files out of Valorant's Demos folder, which
     the game prunes);
  3. upload_replays.py --count N   (uploads up to N replays not on the site, one at a time,
     waiting for each to be stored; about 6-7 minutes each).

A crawl that fails or falls short is reported and the uploads still run: a replay whose match
isn't crawled is stored anyway and links after a later crawl.

Usage:
  .\scripts\refresh_replays.ps1            # 5 matches per player, then up to 5 replays
  .\scripts\refresh_replays.ps1 -Count 6
#>

param(
    [int]$Count = 5
)

$webappRoot = Split-Path -Parent $PSScriptRoot
Set-Location $webappRoot
$python = ".\.venv313\Scripts\python.exe"

Write-Host "== 1/3: crawling the last $Count match(es) per tracked player"
try {
    & (Join-Path $PSScriptRoot "refresh_remote.ps1") -Count $Count
} catch {
    Write-Warning "The crawl failed ($_). Uploading anyway; the replays link after the next crawl."
}

Write-Host "== 2/3: archiving the replay folder"
& $python "scripts\archive_replays.py"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Archiving reported a problem (exit $LASTEXITCODE); see above. Uploading anyway."
}

Write-Host "== 3/3: uploading up to $Count replay(s) not on the site"
& $python "scripts\upload_replays.py" --count $Count
exit $LASTEXITCODE
