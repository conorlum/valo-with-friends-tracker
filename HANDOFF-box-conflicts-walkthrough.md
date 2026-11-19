# Handoff: walk the owner through the box-height conflicts (Ascent, Summit)

A walkthrough session: explain and show, the owner decides. Change nothing until the owner approves. DB access
is read-only, through `webapp/scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`.

## What a conflict is

`app/control/box_tops.py` `judge()`: a steady stand near cover, a box step or more above that piece's ground,
not inside any outline, and matching no learnt top. Tops are learnt only from stands *inside* an outline.
Conflicts are stored in each height asset's meta (`box_conflicts`) and listed by
`control_heights.all_box_conflicts`. Heights revision 7; Ascent built 2026-11-13 11:55, Summit 11:47.

## The 20 conflicts cluster on a few spots (px on the map, z in metres)

| Map | Piece | px | z | Cases | Learnt tops |
|---|---|---|---|---|---|
| Ascent | 3 | ~(278,297) | 9.1–9.2 | 8 | none |
| Ascent | 11 | ~(436–450, 535–542) | 4.0–8.3 | 4 | none |
| Ascent | 17 | (325,754) | 1.0 | 2 | none |
| Ascent | 7/8 | (440,481) | 8.3 | 1 | none |
| Summit | 26/27 | (504,667) / (497,675) | 3.5 | 3 | 5.5 / 4.5 |
| Summit | 11 | (520,382) | 3.0 | 1 | none |
| Summit | 5/6 | (74,316) | 3.0 | 1 | 4.0 / none |

Every stand is 0.0–0.65 m outside the outline. Likely reading: those outlines are a little smaller than (or offset
from) the real box, so players on top always land just outside and never teach a top. Summit 26/27 has tops,
but players stand at 3.5 m, which looks like a lower step that isn't modelled.

The health card's links open each case in the replay viewer (`/replays/<match>?round=N&t=T`).

## Walk through

1. Show each spot on the map with its outline drawn (export: `scripts/control_heights.py export --map Ascent`)
   and open one or two replay links per spot so the owner can see where the player stands.
2. Per spot, the owner judges: wrong outline, a real step or ledge that isn't modelled, or a bad stand.
3. Options to lay out: redraw those pieces' outlines (small, but the heights rebuild for those maps), or change
   the teaching rule so a repeated, consistent stand just outside a top-less outline teaches it (affects every
   map; needs tests and a rebuild). Present both with cost; the owner picks.
