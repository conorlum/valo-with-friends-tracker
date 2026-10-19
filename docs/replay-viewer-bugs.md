# Replay viewer bugs (found by eye)

A running list from reviewing replays on the friends site. Each entry: where to see it, what is wrong,
what should happen. Leads are guesses until checked.

## Open

### 3. Harbor's High Tide slow isn't shown

- **Where:** any round with an enemy Harbor's wall (`Mage_Q_WallManager`), e.g. match
  8214115b-8510-4f71-b695-c26cd0449041, rounds 1, 3, 4, 5, 6, 8.
- **Should be:** a brief "slowed" on an enemy as they walk through the wall (owner, 2026-10-19).
- **Data (checked 2026-10-19):** the only effect naming the wall on enemies starts on all five of them at the
  same instant, as the wall finishes forming, and runs to the wall's close (about 16 s), whoever was near it.
  It can't say who walked through, so since the fix for bug 2 it shows nothing.
- **Leads (unchecked):** look for a shorter effect or one-shot on a player's character while they are inside
  the wall's `points`, or tie the slow to a player's track crossing the wall's line.

## Fixed

### 1. Walking through a Neon Fast Lane wall shows a hit

- **Where:** https://valowithfriendstracker.onrender.com/replays/c8f512b4-a12d-45f3-9fde-1036a7df39c0,
  round 20, from 58.9 s (the enemy Neon's Fast Lane). Reported by the owner as coach feedback on report 33
  (2026-10-19).
- **Seen:** a player who passes through the wall gets a hit marker.
- **Should be:** no marker. Fast Lane blocks vision and does nothing to a player who walks through it.
- **Data (checked 2026-10-19):** the round's `util` list has the Fast Lane (`4_Tunnel`, code `Sprinter`, slot 8)
  at 58.931 s, then three `status: "hit"` rows from it: slot 5 at 60.132 s and 61.132 s, and slot 1 at
  59.593 s. Slot 1 was already dead (killed at 30.459 s), so that row is wrong twice over. None of these
  appear in `player_state.damage_taken`, so health bars and the coach's timeline are not affected.
- **Cause:** `app/replays/extras.py`: `STATUS_AGENTS` contains `Sprinter` (Neon), and `status_of` labels any
  non-pawn object of those agents that touches an enemy `"hit"`. That fallback was for agents with no
  archived replay yet. Fast Lane's effect on a player passing through is caught by it.
- **Fixed (2026-10-19, condenser revision 20):** see bug 2. Fast Lane shows nothing, and no status is kept on a
  dead player, for any agent.

### 2. The `STATUS_AGENTS` fallback is a likely source of more false statuses

- **Where:** `app/replays/extras.py`, `STATUS_AGENTS = {"Sprinter", "Cable", "Mage", "Rift"}` (Neon, Deadlock,
  Harbor, Astra) and the last branch of `status_of`.
- **What was wrong:** for these agents, any ability object (not the agent's pawn) that touched an enemy was
  shown as a generic `"hit"` status, named after the object, whatever the ability does.
- **Counted (2026-10-19, 1,358 stored rounds):** the fallback made 9 kinds of `"hit"`: Fast Lane (55, 7 on the
  dead), High Tide (50, 5 on the dead, ~16 s each), Relay Bolt (24), Sonic Sensor (13), GravNet (11),
  Reckoning (6, 2 on the dead), Annihilation (4), Storm Surge (1). Astra made none beyond her already-mapped
  Nova Pulse. Statuses on the dead came from other agents too: Thorne's slow field 11, KAY/O and Fade 1 each.
- **Fixed (2026-10-19, condenser revision 20):** the fallback is gone. Mapped by name in `STATUS_OBJECTS`:
  Relay Bolt and Sonic Sensor "concussed", GravNet "netted", Annihilation "cocooned", Reckoning and Storm Surge
  "nearsighted" for the game's 3 s and 2 s (`STATUS_GAME_MS`; their effects run until the object closes).
  Fast Lane and High Tide show nothing (High Tide's slow: bug 3). `condense.statuses_while_alive` drops a
  status that starts while its target is dead and ends one at the target's death. The revision bump re-parses
  stored replays.
