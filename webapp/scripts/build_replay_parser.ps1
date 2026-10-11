# Builds the pinned ValorantReplayParser CLI described by webapp/replay_parser.json.
#
#   .\scripts\build_replay_parser.ps1                 # into $env:REPLAY_PARSER_DIR, default $HOME\rp\parser
#   .\scripts\build_replay_parser.ps1 -ParserDir D:\rp\parser
#
# Clones (or fetches) the upstream repo, checks out the pinned commit, applies the
# recorded patches, publishes src\CliReader to <ParserDir>\bin and writes
# <ParserDir>\bin\BUILD.json (commit, SDK version, patch hash). Keep the path
# short: a deep checkout fails with "Filename too long" on Windows.
#
# Third-party code, pinned in replay_parser.json. The user or the agent may run it (docs/replay-viewer-plan.md).
param(
    [string]$ParserDir = $(if ($env:REPLAY_PARSER_DIR) { $env:REPLAY_PARSER_DIR } else { Join-Path $HOME 'rp\parser' })
)
$ErrorActionPreference = 'Stop'

$pinFile = Join-Path $PSScriptRoot '..\replay_parser.json'
$pin = Get-Content -Raw $pinFile | ConvertFrom-Json

if (-not (Test-Path (Join-Path $ParserDir '.git'))) {
    git -c core.longpaths=true clone $pin.upstream $ParserDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed ($LASTEXITCODE)" }
} else {
    git -C $ParserDir fetch origin
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed ($LASTEXITCODE)" }
}

# Drop any earlier patch so the checkout is exactly the pinned commit, then re-apply.
foreach ($patch in $pin.patches) {
    git -C $ParserDir checkout -- $patch.file
}
git -C $ParserDir -c advice.detachedHead=false checkout $pin.commit
if ($LASTEXITCODE -ne 0) { throw "git checkout $($pin.commit) failed ($LASTEXITCODE)" }

$patchText = ''
foreach ($patch in $pin.patches) {
    $path = Join-Path $ParserDir $patch.file
    $text = Get-Content -Raw $path
    if (-not $text.Contains($patch.find)) { throw "patch for $($patch.file) no longer applies: '$($patch.find)' not found" }
    $text = $text.Replace($patch.find, $patch.replace)
    [System.IO.File]::WriteAllText($path, $text)
    $patchText += "$($patch.file)`n$($patch.find)`n$($patch.replace)`n"
}
$sha = [System.Security.Cryptography.SHA256]::Create()
$patchHash = -join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($patchText)) | ForEach-Object { $_.ToString('x2') })

$bin = Join-Path $ParserDir 'bin'
# Patches can extend the same method. Remove recognised patches in reverse order
# before applying the current stack; checking the first patch against the last
# patch's modified context is not an idempotence check.
$sourceStack = @($pin.source_patches)
foreach ($sourcePatch in $sourceStack) {
    $sourcePath = Join-Path (Split-Path $pinFile) $sourcePatch.file
    $sourceText = [IO.File]::ReadAllText($sourcePath).Replace("`r`n", "`n")
    $sourceHash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sourceText)) | ForEach-Object { $_.ToString('x2') })
    if ($sourceHash -ne $sourcePatch.sha256) { throw 'source patch digest mismatch' }
}
[array]::Reverse($sourceStack)
foreach ($sourcePatch in $sourceStack) {
    $sourcePath = Join-Path (Split-Path $pinFile) $sourcePatch.file
    git -C $ParserDir apply --reverse --check $sourcePath 2>$null
    if ($LASTEXITCODE -eq 0) {
        git -C $ParserDir apply --reverse $sourcePath
        if ($LASTEXITCODE -ne 0) { throw 'source patch reverse failed' }
    }
}
foreach ($sourcePatch in $pin.source_patches) {
    $sourcePath = Join-Path (Split-Path $pinFile) $sourcePatch.file
    $sourceText = [IO.File]::ReadAllText($sourcePath).Replace("`r`n", "`n")
    $sourceHash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sourceText)) | ForEach-Object { $_.ToString('x2') })
    if ($sourceHash -ne $sourcePatch.sha256) { throw 'source patch digest mismatch' }
    # An identical patch already applied on this pin needs no reset of the checkout.
    git -C $ParserDir apply --reverse --check $sourcePath 2>$null
    if ($LASTEXITCODE -ne 0) {
        git -C $ParserDir apply --check $sourcePath
        if ($LASTEXITCODE -ne 0) { throw 'source patch does not apply cleanly; use a fresh parser checkout' }
        git -C $ParserDir apply $sourcePath
        if ($LASTEXITCODE -ne 0) { throw 'source patch apply failed' }
    }
    $patchText += "$($sourcePatch.file)`n$($sourcePatch.sha256)`n"
}
$patchHash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($patchText)) | ForEach-Object { $_.ToString('x2') })
Push-Location $ParserDir
try {
    dotnet publish $pin.cli_project -c Release -o $bin
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed ($LASTEXITCODE)" }
    $sdk = (dotnet --version).Trim()
} finally {
    Pop-Location
}

$build = [ordered]@{
    commit     = $pin.commit
    sdk        = $sdk
    patch_hash = $patchHash
    built_at   = (Get-Date).ToUniversalTime().ToString('o')
}
$build | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $bin 'BUILD.json')
Write-Host "Built $($pin.commit.Substring(0, 12)) with SDK $sdk into $bin"
