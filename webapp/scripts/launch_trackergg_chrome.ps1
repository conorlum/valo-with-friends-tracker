# Launches a dedicated Chrome profile with remote debugging enabled, for
# capture_trackergg_state.py to attach to. Separate from your daily-driver
# Chrome profile so this never touches your normal browsing session/cookies.
#
# First run: log into tracker.gg in the window that opens. The login persists
# in this profile, so later runs will already be signed in.
#
# Usage: powershell -File scripts\launch_trackergg_chrome.ps1

$profileDir = "$env:LOCALAPPDATA\ValoMathsScraper\ChromeProfile"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$chromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromePath = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chromePath) {
    throw "Couldn't find chrome.exe in any of: $($chromeCandidates -join ', ')"
}

& $chromePath `
    "--remote-debugging-port=9222" `
    "--user-data-dir=$profileDir" `
    "https://tracker.gg/valorant"
