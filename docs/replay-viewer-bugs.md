# Replay viewer bugs (found by eye)

A running list from reviewing replays on the friends site. Each entry: where to see it, what is wrong,
what should happen. Leads are guesses until checked.

## Open

None.

## Fixed

### 4. Reckoning's nearsight doesn't reach map control

- **Where:** `app/control/engine.py` read nearsight from `nearsight` rows only; Reckoning's are `status` rows
  (`nearsighted`), which it ignored (neither set in `CONTEST_STATUSES` / `DOWNGRADE_STATUSES`).
- **Should be:** a Reckoning hit blinds like Storm Surge's, whose nearsight is the export's own `nearsight`
  row. The parser emits no such row for Reckoning.
- **Fixed (2026-10-19, control revision 19):** `_blinds` also passes `status` rows in `BLIND_STATUSES`
  (`nearsighted`, `blinded`) to the shared normaliser, which already reads a status row's `status` as its
  condition; an ulting Veto is left out as for nearsight rows.

### 3. Harbor's High Tide slow isn't shown, and Reckoning's hits are wrong

- **Where:** match 8214115b-8510-4f71-b695-c26cd0449041 (10 walls, 2 Reckonings), and three local exports.
- **Should be (owner, 2026-10-19; valorant.fandom.com High Tide / Reckoning):** High Tide slows anyone who
  touches it, either team, 30% for 0.6 s. Reckoning nearsights (3 s) and slows (2 s) players its wave touches,
  at any point, including walking into it after it was stopped (it then stands like a smoke). The owner first
  said enemies only; the data shows teammates debuffed too (wiki: friendly fire), and the owner chose the data.
- **Data (checked 2026-10-19):** High Tide's one effect starts on all ten players at once as the wall goes up
  and lasts its whole life: no row names a touch. But tracks crossing the wall's line (its projectile's
  `flight`) while it stands slow down: on the 23 enemy crossings, speed over the next 0.6 s was a median 0.69
  of the 0.6 s before, against 0.98 at other moments. Reckoning's wave plays touch effects on anyone it
  touches, the debuff (`FXC.Buff.Duration` 3) only on some (two of Harbor's teammates, but not two enemies
  touched 1.0 s and 2.7 s after a cast), and a force module (`NetMulticastApplyForceModule`, `Source` = the
  chunk) on every frame of a touch; one export has a player walking back into a stopped wave and being
  debuffed again. Storm Surge already has the export's `nearsight` row (at the moment it goes off, 2 s).
- **Fixed (2026-10-19, condenser revision 20):** `condense.high_tide_slows` writes a 0.6 s `slowed` for every
  track that crosses a standing wall, any team. Reckoning counts only its debuff effect, on whoever has it
  (teammates included, never Harbor), for its 3 s, and each later touch of a player it already debuffed
  restarts it (`extras.DEBUFF_ONLY`, `Raw.forces`). Storm Surge's
  object effect is no status (its `nearsight` row stands).

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
  Relay Bolt and Sonic Sensor "concussed", GravNet "netted", Annihilation "cocooned", Reckoning "nearsighted"
  (bug 3). Fast Lane, High Tide's effect and Storm Surge's effect show nothing (High Tide's slow: bug 3). `condense.statuses_while_alive` drops a
  status that starts while its target is dead and ends one at the target's death. The revision bump re-parses
  stored replays.
