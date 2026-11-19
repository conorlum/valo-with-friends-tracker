# Handoff: make utility on the replay map more readable

Written 2026-11-17 from the owner's watch-through of replay `ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262` (Haven).
Items 10 and 12 in `replay-issues-ee6e0ba7/ISSUES.md`. The owner's goal: **"need to make all the util more
readable."**

## What the owner wants

**1. A drawing style like valoplant.gg's** (references: `replay-issues-ee6e0ba7/10-util-visuals-reference-valoplant.png`,
`10b-util-agent-colours-reference.png`; these are screenshots of valoplant, not our site). A smoke is a soft
filled circle with a thin ring, and a wall or line ability is a bright, colour-graded bar from the caster. The
owner finds that more readable overall. Draw our own version; don't copy their graphics.

**2. Colours.** Each piece of utility is the **agent's in-game colour** (a Jett smoke is white, the KAY/O
knife's area is blue), **plus a mark for its side** in the side palette chosen in another session (ask the
owner which palette, or look for that session's branch or PR). Outline or tint is undecided. Recommendation
given to the owner: **a thin side-coloured outline**, because a tint muddies the in-game colour (a white Jett
smoke would stop looking white). Confirm with the owner before building.

**3. Show the pulses.** Show each pulse of the abilities that hit in bursts, as it happens, not as one static
area:
- KAY/O's FRAG/ment (molly) pulses
- Miks's pulses (the owner said "miks pulses"; Miks's kit in `app/static/data/abilities.json` ≈ 92 is
  Bassquake, Harmonize, M-pulse, so confirm which ability is meant, probably M-pulse)
- Breach's Aftershock blasts (`replay.js` ≈ 279–283 already notes "Aftershock's blasts hit 2.2 and 2.8 s after
  it sticks")

Check whether the replay records each pulse's time. If not, use the ability's known timing (the wiki through the
fandom API; memory "wiki figures via API").

## Where it lives

`app/static/js/replay.js`: the ability style table (`[/^.../, { ability, shape, r, ... }]`, ≈ 110–320) and the
draw passes (`["wall", "lanes", "box", "cone", "smoke", "area", "reveal", "mesh", "shear", "line", "wire",
"badge"]`, ≈ 2592 onward). Per-agent colours would be a new field on the style entries, or a per-agent table.

## Approach

Show the owner a mock-up first: a standalone HTML page with a few rounds' util drawn in the new style (memory:
the owner can't open artifact links, so use local HTML with the data embedded; long pages screenshot badly in
Chrome). Iterate on that before touching the viewer. Then one PR for style and colours, and one for pulses.

## Constraints

- Display-only. No control/condense revision bump unless pulse times need new data from the parser.
- The demo must keep working.
- Work in a worktree (`EnterWorktree`). Open a PR and give the owner the link. **Never merge.**
