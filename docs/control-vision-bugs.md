# Control and vision bugs (found by eye)

A running list from reviewing replays on the friends site. Each entry: where to see it, what is wrong,
what should happen. Leads are guesses until checked.

## Open

### 1. Sight passes through a closed door

- **Where:** https://valowithfriendstracker.onrender.com/replays/3b29b5e9-ba26-4b0b-bca3-8eda8d150215,
  round 3, about 9.5 s (5v5).
- **Seen:** the door marker in mid-map is drawn closed (yellow), but Chamber's (orange) sight still
  reaches through it and colours the far side.
- **Should be:** a closed door blocks all vision. No sight through it at any height.
- **Leads (unchecked):**
  - Door sight uses a descending height band with estimated metre bounds (PR #128, "Record accepted door
    estimates"). If the closed band's bottom/top doesn't cover the eye-to-eye line, the ray passes
    under or over the panel.
  - The marker may be reading "closed" while the sight sampler still has the door as moving/partial.
  - Door sight row caching ("Reuse unchanged door sight") could be serving a row from before the close.

### 2. Clove's sight reaches an area she can't see

- **Where:** same replay, round 7, 33.2 s (3v3, after the 30.4 s kill).
- **Seen:** Clove (blue, bottom of mid) gets a diagonal band of sight running up-left into the
  bottom-left corridor/room by the orange camera, and the engine treats that area as seen by blue.
  From where Clove stands she has no line of sight to it.
- **Should be:** the engine must not grant sight there. Walls between Clove and that area block it.
- **Leads (unchecked):**
  - The band looks like it threads a wall corner. That could be a diagonal ray slipping between two
    blocked grid cells, or a gap/missing wall in the sight geometry there.
  - A height-aware ray may be clearing a wall whose top height is wrong in the height asset.

### 3. Area marked contested with no one contesting it

- **Where:** same replay, round 13, 11.2 s (5v5).
- **Seen:** the far-left room (top-left, behind blue's player there) is blue but carries contested
  stripes and a few orange flecks. No orange player sees it or is near it.
- **Should be:** blue's. Contested needs a live claim from both sides; bullets passing through the
  area are not a claim.
- **Leads (unchecked):** the owner suspects tracers fired through the area are being counted as an
  orange claim (or as orange sight). Check what source the orange cells there come from.

### 4. Hearing footsteps wipes out an area's unknown

- **Where:** same replay, round 13, 27.5 s (5v5). The Control tab's "why the unknown changed" list shows
  footsteps heard just before (27.5 s team 2 on 没天分的小孩, 27.2 s team 2 on 有没有可能我是萌新,
  26.5 s team 1 on NPrightdolphin and Deemo).
- **Seen:** the right side of the map (B side, top right down to the trip) turns grey, as if the
  unknown there was resolved, right after footsteps were heard.
- **Should be:** footsteps tell the listener where *a* player is, not *which* player. They can't be
  used to pin one named enemy and so clear the ground that enemy could have been on. Hearing alone
  should not shrink the unknown over this area.
- **Leads (unchecked):** the hearing engine attributes each footstep to a named player and shrinks
  that player's unknown to the sound. The ground that player could otherwise have held becomes safe
  for the listener, and that safe ground against blue's remembered ground turns into nobody's (the
  D5 rule, `project_control_bugs_2026_10_04`). If so, the fix is in how hearing is attributed. The
  rule that turns safe-against-memory into nobody's is working as intended.

## Fixed

(none yet)
