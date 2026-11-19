# HANDOFF: replay players panel polish + Iso arena + control colours

Written 2026-11-17. Worktree `.claude/worktrees/replay-shield-left-badge`, branch
`worktree-replay-shield-left-badge`. NOTHING COMMITTED YET. Enter it with `EnterWorktree` (`path`).
(This file and HANDOFF-held-ability-names.md sit untracked in the worktree root: don't commit them.)

## Done in the worktree (uncommitted); `tests/replays/test_replay_viewer.py`: 73 passed

Files: `webapp/app/static/js/replay.js`, `webapp/app/static/css/style.css`, `webapp/tests/replays/test_replay_viewer.py`.

1. Shield badge shows `left/bought` on its face (26/50); hover "Shield left 26 of 50 bought". `armorBadge(v)`.
2. Kit row bigger (linked-layout CSS ~line 1798: ability 1.7rem, ult 1.85rem, gun icon 2.8rem, gap 0.35rem).
3. Dead rows: faded red background + red left edge, contents at 50% (`.replay-pl-row.is-dead`, plus a linked
   override `padding-inline: 0.4rem`).
4. Iso Kill Contract: `mapPlaceAt(segments, t)` pins a duellist (marker, death X, kill line) to the last
   on-map place before the arena (z < ARENA_Z = -2500 dm). Used in the kill X loop and the player markers.
5. Camera/drone in use: `possessingAt(abilities, slot, t)` (ability rows' `possessed` spans) replaces the gun
   with "Watching Spycam" / "Flying Owl Drone". Confirm on a real round that the viewer's rows carry `possessed`.

None of it has been seen in a browser (the Chrome extension wasn't connected). Check before the PR.

## Asked for, not done

A. **Iso marker style** (owner): while in the duel, the pinned marker should look different: a team-colour
   outline with the person in colour, "or something to show in ult".
B. **Arena inset** (owner): draw the arena on the canvas with both duellists moving inside it. No public
   source gives its size; guides: a rectangular room, Iso at one end and the target at the other, temporary
   walls (2 for Iso, 1 for the target) that drop after about 2.5 s. Measuring real extents from live replays:
   `C:\Users\CONORL~1\AppData\Local\Temp\claude\297f97a3010000a8\37e4d34d-8b33-4c3d-941d-f069fc0182e4\scratchpad\arena_scan.js`
   (Node; replay.js's `decodeRound`; fetches the public `https://valowithfriendstracker.onrender.com/replays/<uuid>/<n>.json`,
   skips matches without an Iso, prints each duellist's arena u/v/z ranges). Output was going to
   `arena_out.txt` / `arena_err.txt` in that scratchpad (background run, may have finished). Run it with
   PowerShell (Bash trips the worktree guard on paths containing GitHub):
   `node <scratchpad>\arena_scan.js <abs path to webapp\app\static\js\replay.js> <uuid> ...`.
   **Scan result (47 duels in 93 matches, `arena_out.txt`): the stored tracks can't draw the arena.** Every
   arena sample has `v = 10000`: the condenser clamps u/v to the minimap, so one axis of the arena is gone (this
   is also why the death X sat on the map's bottom edge). On most maps only u varies, and the start points are
   fixed per map (Iso / target: Sunset u 5780 / 4142, Lotus 5268 / 3756). It is worse on two maps: on Haven
   (12 duel tracks, 2 matches) u is clamped too (always 10000), so both axes are gone; on Summit 6 of 20 tracks
   have u stuck at its edge (range 0-1236). Per map: Sunset 8, Lotus 22, Ascent 14, Abyss 16, Summit 20,
   Corrode 2, Haven 12 duellist tracks (94 = 47 duels x 2). The inset needs the condenser to store arena
   positions unclamped (e.g. a separate arena track in metres relative to the room) plus a CONDENSE bump and a
   re-parse. Bundle it with the held-ability re-parse (HANDOFF-held-ability-names.md) so replays re-parse once.
C. **Control colours + borders**: mock-up at
   `C:\Users\CONORL~1\AppData\Local\Temp\claude\297f97a3010000a8\37e4d34d-8b33-4c3d-941d-f069fc0182e4\scratchpad\control-colors-mockup.html`
   (five palettes incl. two green/red; three border styles). Waiting on the owner's pick. Then implement in
   `app/static/js/replay_control.js` (`paintStates` / `paintUnknown` ~line 340) and the `--replay-team-*`
   and key CSS (~line 2156). Borders: an edge where a cell meets a different owner, or leaves an unknown region.
D. Held-ability names + coach fact: separate PR, `HANDOFF-held-ability-names.md`.

## Answered

"Safe" is still computed (`app/control/engine.py` `unknown_safe` ~line 2269): cells outside the team's unknown
that no unknown cell can see (smoke-aware), painted at 30%.

## Ship

Run the replay viewer tests, commit the three files, "push it" (open PR, give link, never merge).
