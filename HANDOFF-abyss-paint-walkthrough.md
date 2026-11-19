# Handoff: walk the owner through getting Abyss's map heights live

A walkthrough session: explain and show; the owner paints and decides. Supersedes
`HANDOFF-abyss-heights-walkthrough.md` (2026-11-13), whose kill-line problem is fixed. DB access is read-only,
through `webapp/scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only` (it reads
`webapp/.env.remote`; in a worktree, copy that file in and delete it after). Work in a worktree off a fresh
`origin/main` (`EnterWorktree`). Never merge: the paint goes in by PR, and the owner merges. Activating anything
in prod is the owner's `!` command (bash), never run by the session.

## Where Abyss stands (checked 2026-11-15)

- Abyss has never had active heights. Latest build: id **2371**, 2026-11-14 09:54 UTC, heights revision 8,
  digest `477db6a9c1bf`, 12 matches, `status = rejected`. No Abyss build since (no new Abyss replays).
- One reason left (`app/control/adaptive.py` `area_readiness`, ~line 314): *1 unresolved area larger than 12
  parent-cell areas touches a footprint with two floors (largest 15)*. `UNRESOLVED_MAX` is 12.
- Kill lines now pass: 33 of ~1,680 blocked = **1.97%** against the 2% bar (`KILL_LINE_BAR`). It's a narrow
  pass, so any paint must be re-checked against it.
- Supported share 82.8%.
- `app/static/data/control/height_paint.json` is `{"maps": {}}`: nobody has painted any map yet. The brushes
  (PR #222) were built for exactly this.
- The build report's largest unresolved areas (bbox in map px, `cells` as the report counts them). Which one
  the gate means is **not yet confirmed**; step 1 below finds it:
  - [32,360]–[119,559]: 157 cells, mostly "no samples" (143), 14 "neighbours disagree". Most likely.
  - [904,376]–[991,455]: 55, no samples.
  - [112,264]–[143,343]: 24, no samples / neighbours disagree.
  - [656,256]–[751,271]: 21, floors too close.

## What the owner has to do (the session prepares each step)

1. **See the blocking area.** The session exports the build and finds the one area the gate counts: the
   connected unresolved region (3x3 neighbourhood) next to a two-floor footprint, larger than 12. Recompute it
   from the export the way `area_readiness` does, not from the report's list. Then show it on the minimap
   with its neighbouring floors, and open one or two replay rounds that cross it, so the owner sees what is
   there (a drop, a ledge, a bridge over a lower floor, or a spot nobody walks).
   `scripts/with_friends_db.py ... --read-only scripts/control_heights.py export --map Abyss --out "$TEMP/Abyss_heights.npz"`
2. **Decide what the area is.** For each part of it, the owner picks a brush:
   - **Open underneath**: a floor with open space below (a bridge or an overhang).
   - **Ground only**: only one floor, the ground.
   - **Solid to the top**: a solid block up to its top.
   Or leave it unpainted if it's walkable but genuinely unknown. Then the answer is more Abyss replays, not paint.
3. **Paint it in the tagger.** The session builds the page with Abyss's active-build export loaded:
   `.venv313\Scripts\python.exe scripts\control_tagger.py --heights-dir <export dir>` (opens
   `%TEMP%\valo-control-tagger\control-tagger.html`). The owner paints with the height brushes, then
   **Export height paint**. Paint applies only to walkable fine cells (R1.1).
4. **Preview before any PR.** The session copies the export over `app/static/data/control/height_paint.json`
   in the worktree and runs
   `scripts\build_control_heights.py --map Abyss --preview --out %TEMP%\valo-replay\abyss-preview` (DB blobs
   through the read-only wrapper). Check: `not_ready` is empty; kill lines still under 2%; `painted` counts in
   the report match what was painted; `paint_unresolved` is 0. Show the owner the preview's paint-check page
   (every blocked line and the gate).
5. **PR.** The session opens a PR with only `height_paint.json` (the paint's hash keys the build). After the
   owner merges and it deploys, the next Abyss height run (`REPLAY_HEIGHTS_AUTO`) should come out `active`.
   It also clears the "Height builds rejected" health row. If that run doesn't happen on its own, check
   how a build is asked for (`scripts/control_heights.py`, `app/services/replay_heights_remote.py`) and
   hand the owner the command.

## Don'ts

- Don't loosen `UNRESOLVED_MAX` or `KILL_LINE_BAR` to get Abyss through; only the owner can change a rule.
- Don't `activate` a rejected build. Check first how `scripts/control_heights.py activate` treats one; the
  owner decides.
- Don't commit a preview's output; `--out` must be outside the repo.
