# Replay issues: ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262

Page: https://valowithfriendstracker.onrender.com/replays/ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262/coach

Logged while the owner watches the replay. Screenshots sit next to this file.

| # | Round · time | Area | What's wrong | Screenshot |
|---|---|---|---|---|
| 1 | R1 · 4.8 s | Map control: blue (Cypher, NPrightdolphin's side) vision along the lower mid corridor toward soralux (circled in red) | Cypher's vision reaches too far west along that corridor. The doors block line of sight, so blue control should stop at the doors. | `01-r1-cypher-vision-through-doors.png` |
| 2 | R1 · 9.5 s | Map control: lower mid, near the orange tripwire left of NPrightdolphin's corridor (two walls marked with red lines) | Vision passes through two walls as if they don't exist. Orange and blue control streak diagonally across mid through them. | `02-r1-two-walls-missing.png` |
| 3 | R3 · 1.8 s | Map control: the area around Deemo/Osmin by the blue tripwire, mid-right (outlined in red; the wall is the red vertical line on its left) | Shown as contested, but the wall on its left blocks orange's sight. It should be blue only. | `03-r3-contested-behind-wall.png` |
| 4 | R4 · 8.9 s | Map control: the corridor right of Osmin/DoubleBl1nd, east side (outlined in red; the wall is the red line on its left by DoubleBl1nd) | Orange control reaches into the area through the wall. The wall blocks vision there. | `04-r4-vision-through-wall-into-area.png` |
| 5 | R5 · 0.8 s | Map control: Osmin's (blue) vision west along lower mid to the far-left corridor (red = the leaked sightline; black = the real rooms and walls it crosses) | Same as #1–#4: the blue streak runs diagonally through several thin walls between rooms. It should stop at the first wall. | `05-r5-osmin-vision-through-mid-walls.png` |
| 6 | R5 · 25.9 s | Players panel: LeakyTeeth (Clove), team 2 (outlined in red); Pandora's and soralux's rows are also underlined | Clove's health bar is dashed (unknown), with no armor figure and no weapon shown. The replay should give her health. Pandora and soralux show no weapon either (owner's underlines; not yet confirmed that this is the point). | `06-r5-clove-health-unknown.png` |
| 7 | R6 · 13.6 s | Map control: mid, between LeakyTeeth and NPrightdolphin (outlined in red; the scribble marks the wall) | Contested hatching runs straight across mid through the wall between the two rooms. | `07-r6-mid-walls-ignored.png` |
| 8 | R7 · 27.1 s | Spike plant: NPrightdolphin planting on site (circled) | **Feature.** (a) Show the first tap of the plant as a loud sound, since starting a plant makes noise. (b) Draw a progress ring around the planter: a full ring is a full plant. If they stop early, the ring stays at how far the plant got. (c) Control should also use the plant's start tap (see the lead below). | `08-r7-plant-progress-and-sound.png` |
| 9 | Any round (shown at R16) | Controls row under the map (the owner wrote "NEXT" at the right end of the Play / Previous kill / Next kill / Speed row) | **UI.** Add a text "Next round" button at the right end of that row. The existing → arrow under the round numbers does this, but it is too far down and doesn't say what it does. Move it up here. Probably add "Previous round" to match. | `09-next-round-button.png` |
| 10 | All rounds | Utility drawing on the map | **Visual.** Draw utility more like valoplant.gg's (reference screenshot, circled): a smoke is a soft filled circle in the ability's colours with a thin ring around it, and a wall or line ability is a bright, colour-graded bar from the caster. The owner finds it more readable overall. **Colours:** each piece of util is the agent's in-game colour (a Jett smoke is white, the KAY/O knife's area is blue), **plus a mark for its side** in the side colours chosen in the other session. The mark is either a slight outline or a slight tint; the owner hasn't decided. Recommendation: **outline**, because a tint muddies the in-game colour (a white Jett smoke would stop looking white), while a thin side-coloured ring keeps both readable. | `10-util-visuals-reference-valoplant.png`, `10b-util-agent-colours-reference.png` (references, not our site) |
| 12 | All rounds | Utility that pulses | **Visual.** Show each pulse of the abilities that hit in bursts: KAY/O's FRAG/ment (molly) pulses, Miks's pulses, and Breach's (Aftershock) pulses. Each burst should show as it happens, not as one static area. Part of making all the util more readable (#10). Check that the replay gives the pulse times; otherwise take them from the ability's known timing (wiki/valoplant figures). | (none) |
| 13 | All rounds | Each player's facing on the map | **Visual.** Mark the centre of each player's vision so it is obvious exactly where they're looking. Best is a thin line along their facing that runs to the **first wall it hits**, showing what their crosshair is holding, like the spectator view's aim lines in the reference (circled: thin lines from each player to the wall). Fallback: a short line or a dot at the centre of the vision cone. Uses yaw only (the top-down map can't show pitch). Where the line stops depends on the wall geometry, so it shares the Haven walls problem (#1–#7): fix the walls first or the lines will go through walls too. | `13-crosshair-line-reference.png` (reference: a pro-match spectator view) |
| 11 | All maps | Map orientation | **Visual.** Rotate the maps 90° so the **attacker spawn is at the bottom and the defender spawn at the top**. The spawns are fixed per map, so each map gets one fixed rotation (whatever brings its attacker spawn to the bottom), not a change at halftime. Ideally this is display-only (a canvas transform in the viewer), so stored coordinates, control layers, paint, callouts and the tagger stay as they are. Check every drawing on the map (labels, arrows, tooltips and hover hit tests) under the rotation. | (none) |

## Pattern to investigate

Owner's read (after #4): **Haven is struggling with thin walls.** All four so far are vision going through thin
walls or doors. After #7: **"basically no walls are being taken seriously on Haven"**, so it is likely
map-wide, not a few thin walls. Next steps:

1. Find why thin walls on Haven don't block vision: is it the wall geometry (missing or too thin in the
   visibility mesh or grid) or the sight check's step size passing between samples?
2. Check whether other maps' thin walls leak the same way.

## #8 lead: plant progress

The viewer already has a `"planting"` spike state with the planter's slot (`replay.js:941`, `1451`; the
tooltip shows "is planting the spike", line 3245). The spike state also records when the plant began
(`since`, line 1439). So the ring's progress is (t − start) ÷ the plant time. For a plant that stops early, the
ring stays at the fraction reached when the state left `"planting"`. The tap sound would be a new sound event
at the start time, alongside the existing spike sounds.

**(c) Control doesn't use the plant tap.** Checked `app/control/engine.py`: control locates the planter only
when the plant **completes** (`"plant"`, a 5 m area at the plant time, lines 636–641, 852). The only `"tap"`
it hears is a **defuse** start, taken from the planted spike's `defuses` (lines 643–645), heard within
`spike_tap_range_m`. So the plant's start tap, which comes about 4 s before the plant completes, and any plant
that is started and then abandoned locate nobody. Fix: add a plant-start `"tap"` to `rnd.sounds`, using the
start time and planter from player_state's `"planting"` state. This changes control output, so it needs a
CONTROL revision bump and a re-parse, and that breaks the pinned digest tests.

**Owner's ruling (2026-11-17):** the plant's start tap is a **sound**, handled like gunfire and the other heard
cues:
- It is heard by enemies within hearing range of it (the spike tap's range, `spike_tap_range_m`, unless measured
  otherwise).
- When heard, it places the planter within a **5 m radius** of where they stood (`SOUND_AREA_M` is already
  5 m). A planter is at least on site; the 5 m area narrows that.
- It counts whether or not the plant is finished, so an abandoned plant still gives away where the planter is.
- It is anonymous like the defuse tap, so it should name the planter only under the R1 rule (only one enemy
  could have made it). Owner to confirm whether that rule applies here.

## #6 lead: Clove's health

The viewer's `healthAt` (`webapp/app/static/js/replay.js:1267`) gives `"unavailable"` when a player has taken
damage this round but their latest hit has no complete vitals (hp, shield and both maxima; `healthPct`, line
1255). The map's hover text says which case it is: "not hit in this life" or "the hit at X doesn't record the
shield". So what needs checking is why LeakyTeeth's damage event in R5 has no vitals: the parser's export of
this player's damage, or a Clove-specific source of damage or healing.
