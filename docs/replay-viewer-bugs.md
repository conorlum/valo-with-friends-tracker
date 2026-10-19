# Replay viewer bugs (found by eye)

A running list from reviewing replays on the friends site. Each entry: where to see it, what is wrong,
what should happen. Leads are guesses until checked.

## Open

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
- **Fix (not done):** leave `4_Tunnel` out of the fallback, or drop `Sprinter` from `STATUS_AGENTS` now that
  Neon replays exist and map Relay Bolt (`Q_ElectricSphere`, a concuss) by name like the other agents. Also
  check whether a status on a dead player should be dropped for every agent. Re-parse affected replays after.

### 2. The `STATUS_AGENTS` fallback is a likely source of more false statuses

- **Where:** `app/replays/extras.py`, `STATUS_AGENTS = {"Sprinter", "Cable", "Mage", "Rift"}` (Neon, Deadlock,
  Harbor, Astra) and the last branch of `status_of`.
- **What is wrong:** for these agents, any ability object (not the agent's pawn) that touches an enemy is shown
  as a generic `"hit"` status, named after the object. It doesn't check what the ability does. Bug 1 (Fast
  Lane) is one case. Other likely ones: walls, smokes, and barriers walked through or touched (Harbor's walls,
  Astra's smokes and Cosmic Divide, Deadlock's Barrier Mesh), each of which would show a hit that did nothing
  in game.
- **Should be:** a status only for abilities that really act on a player (a concuss, a slow, a net, a pull),
  each mapped by name in `STATUS_OBJECTS` like the other agents. Everything else from these agents shows
  nothing.
- **Leads (unchecked):** the fallback's comment says these agents had no archived replay yet. Neon now has
  replays (match c8f512b4-a12d-45f3-9fde-1036a7df39c0, both teams). Count the `"hit"` statuses per object
  name across stored replays for all four agents, check each object in game, then map the real ones by name
  and drop the fallback. Re-parse affected replays after.
