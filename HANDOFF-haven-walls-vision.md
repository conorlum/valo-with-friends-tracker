# Handoff: vision goes through walls on Haven (and maybe on every map)

Written 2026-11-17 from the owner's watch-through of replay `ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262` (Haven),
https://valowithfriendstracker.onrender.com/replays/ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262/coach.
Full list and screenshots: `replay-issues-ee6e0ba7/ISSUES.md` (items 1–5 and 7).

## What the owner saw

Map control (the blue/orange areas, with hatching where contested) passes straight through walls and doors on
Haven. In the owner's words after six examples: **"basically no walls are being taken seriously on Haven."**

| # | Round · time | Where | Screenshot (`replay-issues-ee6e0ba7/`) |
|---|---|---|---|
| 1 | R1 · 4.8 s | Cypher's (blue) vision runs west along the lower mid corridor. The doors should stop it. | `01-r1-cypher-vision-through-doors.png` |
| 2 | R1 · 9.5 s | Two walls in lower mid act as if they aren't there, and control streaks diagonally through them. | `02-r1-two-walls-missing.png` |
| 3 | R3 · 1.8 s | The area by Deemo/Osmin, mid-right, is shown as contested, but a wall blocks orange's sight. | `03-r3-contested-behind-wall.png` |
| 4 | R4 · 8.9 s | Orange control reaches the east corridor through the wall by DoubleBl1nd. | `04-r4-vision-through-wall-into-area.png` |
| 5 | R5 · 0.8 s | Osmin's vision runs diagonally west through several thin walls to the far-left corridor. | `05-r5-osmin-vision-through-mid-walls.png` |
| 7 | R6 · 13.6 s | Contested hatching runs across mid between LeakyTeeth and NPrightdolphin, through the wall. | `07-r6-mid-walls-ignored.png` |

#5 makes the best test case: one long sightline crossing several thin walls at a shallow angle.

## Leads (not yet verified)

1. **The corner tolerance may be erasing thin walls.** `app/control/geometry.py`: sight rays are cast on the 1024 px
   sight mask, and a ray "stops once it has crossed more than CORNER_TOLERANCE_M of wall in total"
   (`CORNER_TOLERANCE_M = 0.3`, line 75; `RAY_STEP_PX = 2`, line 77; `tol_hits = int(CORNER_TOLERANCE_M /
   m_per_px // RAY_STEP_PX)`, line 488). If Haven's thin walls are only a pixel or two wide in
   `Haven.sight.png`, a ray can cross one or more of them within the tolerance. Sampling every 2 px could also
   step over a 1 px wall completely. Measure Haven's `m_per_px` and the pixel thickness of the marked walls,
   then work out whether the rays in #5 are cleared by the tolerance, by the step size, or by the wall missing
   from the mask.
2. **The wall may be missing from the mask.** Open `app/static/data/control/Haven.sight.png` beside the
   screenshots and check whether each marked wall is there. The masks come from the minimap via the candidate
   detector and the hand tags (`tags.json`, `scripts/build_control_geometry.py`). A thin wall drawn faintly on
   the minimap may never have been detected.
3. **Layout / patch.** `layouts.json` has only `Haven@base`. Check this replay's `game_branch` and whether
   Haven changed in a later patch (`docs/map-layout-redraw.md`, `scripts/map_layout.py diff`).
4. **Every map.** The tolerance and the step size apply to all maps. Once the cause is known, measure how many
   thin walls on other maps let sight through the same way (e.g. count the sight-mask wall segments narrower
   than the tolerance, per map), and show the owner the numbers before widening the fix.

## Constraints

- Control output changes mean a CONTROL revision bump and a re-parse of every replay. The pinned digest tests
  break on a revision bump (`tests/fixtures/control/layout_golden.json`, `test_layout_base_identity.py`;
  memory "revision bump tests"). The base layout is pinned byte-identical on purpose: if the fix changes it,
  raise that with the owner before regenerating the golden file.
- Preview on one or two rounds (R1 and R5 of this replay) before any full recompute.
- Run the slow lane before merging control/geometry changes (CLAUDE.md, "Running the tests";
  `python scripts/test_catalog.py plan --git`).
- Work in a worktree (`EnterWorktree`). Open a PR and give the owner the link. **Never merge.**

## Suggested first step

Reproduce #5 offline: load Haven's geometry, cast from Osmin's R5 0.8 s position along the leaked line, and
report which pixels of wall the ray crosses and why it isn't stopped. Show the owner the cause before changing
anything.
