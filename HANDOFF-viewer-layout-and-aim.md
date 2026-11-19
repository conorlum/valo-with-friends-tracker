# Handoff: replay viewer layout: a Next round button, map rotation, and aim lines

Written 2026-11-17 from the owner's watch-through of replay `ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262` (Haven).
Items 9, 11 and 13 in `replay-issues-ee6e0ba7/ISSUES.md`. Three separate changes that can be separate PRs.

## 1. A "Next round" button (item 9, small)

Screenshot: `replay-issues-ee6e0ba7/09-next-round-button.png`. Add a text **"Next round"** button at the right
end of the controls row under the map (Play / Previous kill / Next kill / Speed / round clock). The → arrow
under the round numbers already does this (`ReplayViewer.prototype.nextRound`, `app/static/js/replay.js`
≈ 1886), but it is too far down and isn't labelled. Move it up. A matching "Previous round" on the left is
likely wanted; confirm with the owner.

## 2. Rotate the maps: attackers at the bottom, defenders at the top (item 11)

The owner: "the attacking spawn should be on bottom and the defenders spawn should be on top." Spawns are
fixed per map, so each map gets **one fixed rotation** (whatever brings its attacker spawn to the bottom; not
always exactly 90°), and it doesn't change at halftime.
- Make it **display-only**: a transform where the viewer draws, so stored coordinates, control layers, paint,
  callouts, the height tagger and the coach's area names are untouched.
- Every drawing on the map has to work rotated: labels and name blocks (stay upright), arrows, cones and
  facings (yaw has to rotate too), util shapes, tooltips and hover hit tests (`hits` / `layout` in
  `replay.js`), and the control and gap canvases.
- Per-map rotation needs a table. Derive it from the spawn positions in the map data, or hand-list it and check
  each map with the owner.
- Other pages that draw the map (the `/util` page, any map in Critical Util or the coach strip) should match.
  Check them.

## 3. Aim lines: where each player's crosshair is (item 13)

Reference: `replay-issues-ee6e0ba7/13-crosshair-line-reference.png` (a pro-match spectator view with thin aim
lines). Mark each player's facing so it's obvious exactly where they're looking. Best is a **thin line along
their facing that runs to the first wall it hits**, showing what their crosshair is holding. Fallback: a short
line or a dot at the centre of the vision cone (the `cones` layer, `replay.js` ≈ 1580).
- Yaw only; a top-down map can't show pitch.
- Stopping at the first wall needs wall geometry in the browser. Check what the viewer already loads (the
  control layers and the sight mask). The aim lines use the same walls as map control, so **they inherit the
  Haven walls problem** (`HANDOFF-haven-walls-vision.md`). Do that fix first, or ship the short-line fallback
  first.
- Probably a toggle in the layer list, like the other layers.

## Constraints

- Display-only. No revision bumps.
- The demo must keep working.
- Work in a worktree (`EnterWorktree`). Open a PR and give the owner the link. **Never merge.**
