# ValoUploader

A small Windows tray app that uploads your finished Valorant replay files to the friends site, so you don't have to
do it by hand.

## For friends

1. Open the site's replay upload page and click the ValoUploader download button (it links to the latest release's
   `ValoUploader.exe`).
2. Windows SmartScreen will warn that the app is unrecognised. Click **More info**, then **Run anyway**. The app is
   not code-signed.
3. On the upload page, create your app key and **copy it**. Then run `ValoUploader.exe`; it reads the key from your
   clipboard (or asks you to paste it). The key is stored on your PC, and the site keeps only a hash of it.
4. The first run installs the app under your user profile and adds a tray icon. After that it runs quietly.

What it does: it looks for finished replay files, uploads each one to the site, and checks the site stored it. While
Valorant is running it scans the Demos folder every 30 minutes, starting 30 minutes after the game opens
(or the app starts with the game already running). After the game closes it scans once, 10 minutes later;
reopening the game cancels that scan. There is no startup scan or other closed-game scan. Each scheduled scan
gets at most one follow-up look, at least 5 seconds later, to confirm files have stopped changing. A scan due
while paused runs once on resume. The app checks whether the game is running every 5 seconds, and processes
queued uploads and unfinished deletion sweeps every 10 seconds without scanning the folder. Update checks run
independently while the game is closed, at most every 6 hours. A replay the site could not use is tried again after 1, 2, 4
and 8 days, then every 14 days; a match deleted on request is never sent again. When the site refuses or is down,
the whole app waits (10 minutes to an hour) before it tries anything. A replay is deleted once the site has stored that file and the file
is at least 14 days old (by its modified time). A recording the site did not keep (a friend's copy of the same match
got there first) stays on your PC. It never deletes a file whose contents changed since upload. Nothing else is
touched or deleted.

Controls (right-click the tray icon):

- **Pause / Resume** stops and restarts uploading.
- **Start with Windows** turns the per-user startup entry on or off. Turn it off to stop it starting at login.
- **Open log** opens the app's log file. **Change key** replaces your key.
- **Quit** closes the app.

To remove it completely: Quit, turn off "Start with Windows" first, then delete the `ValoUploader` folder under
`%LOCALAPPDATA%\Programs` and its data folder.

What it touches: it only lists running processes (to see whether Valorant is running) and reads replay files. It
does not read or write Valorant's memory or game files, and does not inject anything. How Riot Vanguard reacts to
it has not been tested.

## For the owner

Release (the exe is never committed; GitHub Actions builds it):

1. Bump `__version__` in `uploader/valo_uploader/__init__.py` and merge.
2. `git tag uploader-vX.Y.Z` (the same version) and `git push origin uploader-vX.Y.Z`.
3. `.github/workflows/uploader-release.yml` fails unless the tag equals `__version__`. It builds with
   `uploader/build.ps1`, runs `ValoUploader.exe --selftest`, then publishes a release with `ValoUploader.exe` and
   `ValoUploader.exe.sha256` (`<hex>  ValoUploader.exe`). Installed apps self-update from those assets.

Re-sending what failed: bump `RETRY_GENERATION` in `uploader/valo_uploader/constants.py` together with `__version__`,
then tag. Each friend's app, after it updates, gives every `failed` and `rejected` file a fresh start and sends them one
at a time through the normal queue (`/check` first), except matches deleted on request. Updates that leave
`RETRY_GENERATION` alone re-send nothing.

Build locally: `powershell -NoProfile -ExecutionPolicy Bypass -File uploader/build.ps1` (output in `uploader/dist/`).

App keys are minted on the upload page and stored as SHA-256 only. From `webapp`, through the friends-DB wrapper:

```
python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/upload_tokens.py list
python scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/upload_tokens.py issue <label>
python scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/upload_tokens.py revoke <id>
```

Merges that only touch `uploader/**` do not deploy the site (`deploy.yml` `paths-ignore`).
