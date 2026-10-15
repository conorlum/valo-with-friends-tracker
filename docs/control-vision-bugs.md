# Control and vision bugs (found by eye)

A running list from reviewing replays on the friends site. Each entry: where to see it, what is wrong,
what should happen. Leads are guesses until checked.

## Open

### 2. Clove's sight reaches an area she can't see

- **Where:** https://valowithfriendstracker.onrender.com/replays/3b29b5e9-ba26-4b0b-bca3-8eda8d150215,
  round 7, 33.2 s (3v3, after the 30.4 s kill).
- **Seen:** Clove (blue, bottom of mid) gets a diagonal band of sight running up-left into the
  bottom-left corridor/room by the orange camera, and the engine treats that area as seen by blue.
  From where Clove stands she has no line of sight to it.
- **Should be:** the engine must not grant sight there. Walls between Clove and that area block it.
- **Leads (unchecked):**
  - The band looks like it threads a wall corner. That could be a diagonal ray slipping between two
    blocked grid cells, or a gap/missing wall in the sight geometry there.
  - A height-aware ray may be clearing a wall whose top height is wrong in the height asset.
- **Cause (checked 2026-10-14):** not a sight leak. Clove saw down that diagonal at 30.25-31.5 s; memory keeps the
  thin strip and backfill fills the pocket it closes off. Whether that should count is an open rule question
  (design section 4.2). Not changed by the 2026-10-14 run: it waits for the owner's rule.
- **Wrong area (owner, 2026-10-14 review):** that check covered only a 49-cell strip near the corridor's mouth.
  The area the owner means is wider: the whole bottom-left corridor/room by the orange camera (circled in the
  review's screenshot of round 7 at 33.2 s, x ~60-330, y ~560-700 px on the 1024 map). Re-investigate that area
  before any rule is chosen.
- **That area, checked (2026-10-14 review, this branch's engine):** the remembered strip and the backfilled
  pocket are gone. With the hearing fix there is no blue Safe in the area at any time from 28 to 36 s (up to 51
  cells under the old rule). What stays blue at 33.0 s is Clove's own live sight: 59 cells, 51 in her cone, in
  the room's lower right. From her eye at (480, 773) px, 49 of them have a straight line that crosses no wall
  pixel on the minimap: they come in through the opening where the thin wall on the room's right (x ~338-350)
  ends above the corridor. 10 edge cells graze a wall corner (1-14 px). So the engine is right if that opening
  is real in game; if something there blocks sight (a wall or prop the minimap doesn't draw), it needs tagging.
- **Checked in game (owner, 2026-10-14):** Clove does see up that diagonal through the opening, but not past the
  2-high box in the room (minimap candidate 23, px x 282-297, y 634-677), which the engine let sight through: no
  Sunset box was tagged. **Fix:** candidate 23 is tagged `cover` (`tags.json`, rebuilt with
  `scripts/build_control_geometry.py --map Sunset`): it blocks sight and walking. Kill lines unchanged (1/204
  blocked), 12 cells fewer walkable, no height asset to rebuild. At 31.0 s Clove's band now stops at the box.
  Sunset's other boxes are still untagged ("cover not reviewed").

### Lotus feature-2's last slit (fixed 2026-10-14 review)

With the touched raster (bug 1's fix), Lotus feature-2 (a rotating door) went from 766 leaking cell pairs to
30: its footprint stopped at px row 607 and the wall below starts at 609. Its bottom edge now reaches uv v 5945 in
all four states: 0 pairs (`tests/replays/test_lotus_door_seals.py`). Lotus feature-1 keeps a 2 px slit with no
leaking pair.

## Fixed (on branch `afk/2026-10-14-hearing-and-vision-bugs`, not yet merged; CONTROL_REVISION 16)

### 1. Sight passes through a closed door

- **Where:** same replay, round 3, about 9.5 s (5v5).
- **Seen:** the door marker in mid-map is drawn closed (yellow), but Chamber's (orange) sight still
  reaches through it and colours the far side.
- **Should be:** a closed door blocks all vision. No sight through it at any height.
- **Cause (checked 2026-10-14):** none of the leads (height band, marker state, sight row cache). The closed
  panel's raster covered px rows 448-479 but the doorway is open 446-482: slits at both ends, and Chamber's
  sight went through them. Design section 4.1.
- **Fix:** a sight occluder blocks every cell its footprint touches, not only the cells whose centre is inside
  (`raster_touched` in `webapp/app/control/features.py`; the owner chose this over a Sunset-only data edit,
  2026-10-14 review). Leaking cell pairs around the door 1,849 -> 0, and on every map: Abyss feature-1 108 -> 0,
  Ascent 9/11's slits closed. FEATURE_COMPILER_VERSION 19.

### 3. Area marked contested with no one contesting it

- **Where:** same replay, round 13, 11.2 s (5v5).
- **Seen:** the far-left room (top-left, behind blue's player there) is blue but carries contested
  stripes and a few orange flecks. No orange player sees it or is near it.
- **Should be:** blue's. Contested needs a live claim from both sides; bullets passing through the
  area are not a claim.
- **Cause (checked 2026-10-14):** not tracers (control never reads bullets). Storing a cell with several height
  nodes called it contested whenever the nodes' states differed, even blue against blue (`collapse_states`).
  Design section 4.3.
- **Fix:** commits cfa9877, adae8ae: one team's nodes give that team's strongest state; contested needs both
  teams. Judged case `sunset-r13-blue-room-not-contested`.

### 4. Hearing footsteps wipes out an area's unknown

- **Where:** same replay, round 13, 27.5 s (5v5). The Control tab's "why the unknown changed" list shows
  footsteps heard just before (27.5 s team 2 on 没天分的小孩, 27.2 s team 2 on 有没有可能我是萌新,
  26.5 s team 1 on NPrightdolphin and Deemo).
- **Seen:** the right side of the map (B side, top right down to the trip) turns grey, as if the
  unknown there was resolved, right after footsteps were heard.
- **Should be:** footsteps tell the listener where *a* player is, not *which* player.
- **Cause (checked 2026-10-14):** confirmed. Team 2's (group A) unknown over B side fell from 1,670 cells to
  120 when 有没有可能我是萌新 (27.188 s) and 没天分的小孩 (27.5 s) were heard. Five enemies were alive and none in
  sight.
- **Fix:** commits 4cbbecb, ad460b3: heard footsteps and gunfire name an enemy only when just one enemy could
  have made them (the only one alive, or every other living enemy in sight). The gun heard is no clue (the
  owner dropped it in the 2026-10-14 review: the replay records no loadouts). The unknown there now stays at 1,611 cells. Judged case
  `sunset-r13-footsteps-name-nobody`; three older cases re-recorded (commit 4cb813a).
