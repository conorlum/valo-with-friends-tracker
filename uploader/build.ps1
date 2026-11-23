# Builds uploader/dist/ValoUploader.exe and dist/ValoUploader.exe.sha256.
# Works from any working directory: every path comes from $PSScriptRoot.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv-build'
$py = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $py)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.13 -m venv $venv
    } else {
        & python -m venv $venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'could not create the build venv' }
}

& $py -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

$dist = Join-Path $root 'dist'
$work = Join-Path $root 'build\pyi'
& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work (Join-Path $root 'ValoUploader.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$exe = Join-Path $dist 'ValoUploader.exe'
if (-not (Test-Path $exe)) { throw 'ValoUploader.exe was not produced' }
$hash = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower()
[System.IO.File]::WriteAllText((Join-Path $dist 'ValoUploader.exe.sha256'), "$hash  ValoUploader.exe`n")
Write-Host "Built $exe ($hash)"
