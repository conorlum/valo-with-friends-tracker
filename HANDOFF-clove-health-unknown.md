# Handoff: Clove's health shows as unknown (and some weapons are missing) in the players panel

Written 2026-11-17 from the owner's watch-through of replay `ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262` (Haven),
https://valowithfriendstracker.onrender.com/replays/ee6e0ba7-9e9b-40e0-8e7e-6bc3945cb262/coach.
Item 6 in `replay-issues-ee6e0ba7/ISSUES.md`; screenshot `replay-issues-ee6e0ba7/06-r5-clove-health-unknown.png`.

## What the owner saw

R5 at 25.9 s, players panel, team 2: **LeakyTeeth (Clove)** has a dashed (unknown) health bar, no armour figure
and no weapon. The owner: "whats up with cloves health not being known? we should know this from the replay."
Pandora's and soralux's rows are also underlined in red. Their weapon slot is empty too, but whether that is
what the owner meant is unconfirmed, so ask.

## What's known

- The viewer's `healthAt` (`app/static/js/replay.js` ≈ 1267) returns `"unavailable"` when the player has taken
  damage this round but the latest hit in this life has no complete vitals: hp, shield and both maxima
  (`healthPct` ≈ 1255, `PS_VITAL_KEYS`). Hovering the player on the map gives the reason: "not hit in this
  life" or "the hit at X doesn't record the shield" (≈ 3322).
- The player row's armour and total come from `splitPct` / the panel's health function (≈ 1290 onward), and are
  null unless proven.

## To find out

1. Pull R5's damage timeline and vitals for LeakyTeeth from the stored blob. Is it Clove-specific, or every
   player hit by a particular source? Clove's kit includes self-heal (Pick-Me-Up) and decay (Meddle). Check
   whether their events arrive without vitals and whether `damagedBy` counts them as damage.
2. Check whether the parser's export has the vitals and the condenser drops them (`app/replays/condense.py`,
   `player_state_extract.py`), or whether the export never had them.
3. Missing weapons: check which source the panel's weapon reads, and why it is empty for these three players
   at that moment.
4. Check whether other replays show the same pattern (every Clove, or this match only).

## Constraints

- A condenser change is a CONDENSE revision bump, which re-parses replays and breaks the pinned digest tests
  (memory "revision bump tests").
- Work in a worktree (`EnterWorktree`). Open a PR and give the owner the link. **Never merge.**
