# Bug checklist - October 6, 2026

Collect reports here; investigate and fix later when requested.

Planning completed: [reviewed design specification](../../superpowers/specs/2026-10-06-replay-player-state-design.md) and [reviewed implementation plan](../../superpowers/plans/2026-10-06-replay-player-state-impl.md). Both reviews are linked within the documents. Application implementation remains deferred.

- [ ] **BUG-01: Abyss starting barriers / team unknown-position area extends too far forward.**
  - Report: the team's unknown-position shading reaches well past the starting barriers.
  - Context: Abyss replay, Round 1. The attached screenshot marks the affected central and lower corridors with green circles and indicates barrier locations with green strokes.
  - Expected: at round start, each team's possible unknown positions stay within the area accessible behind its starting barriers; expansion beyond them should reflect elapsed time after barriers drop.
  - Evidence: [Annotated screenshot](abyss-starting-barriers.png).
  - Reproduction to confirm later: open an Abyss replay at round start and compare the unknown-position overlay with the starting barriers. Exact match URL and playback timestamp were not supplied.
  - Status: reported; cause and fix not yet investigated.

- [ ] **BUG-02: Show replay status conditions and suppress a blinded player's control.**
  - Report: the marked player may be blinded, but the replay does not make their status condition clear. Use replay status data to establish the effect rather than inferring it from the map shading.
  - Context: Abyss replay, Round 5 at 18.6 seconds, 4 v 5. The green annotation surrounds the encounter near Yosher and jaquarium; the exact intended player and whether they are blinded need confirmation from the replay data.
  - Expected: clearly display each player's active status conditions at the current playback time. A blinded player contributes no control while blinded, and their contribution resumes when the effect ends. Other teammates' valid control contributions still apply.
  - Evidence: [Annotated screenshot](abyss-status-conditions.png).
  - Verification for later: inspect replay status events at this timestamp; check the indicator and control contribution before, during, and after a blind, including seeking through the effect.
  - Status: reported; actual blind state, cause, and fix not yet investigated. Exact match URL was not supplied.

- [ ] **REQUEST-03: Show health bars after a player takes damage.**
  - Placement: below the character circle and above the player's name.
  - Visibility: initially hidden; appear only after the player takes damage in the current round.
  - Display: remaining total health as a percentage. The user has not yet specified how armor should factor into total health.
  - Verification for later: confirm no bar before damage, the correct percentage after damage, and correct state when seeking backward/forward or switching rounds. Check healing behavior against the requirement that the bar appears after damage.
  - Status: requested; implementation deferred with the bug-fixing session.

- [ ] **REQUEST-04: Show the spike carrier and dropped spike on the replay map.**
  - Carrier: show a spike icon above or beside the player carrying it; exact placement can be decided when implementing, keeping names, health bars, and status indicators readable.
  - Dropped spike: show a spike marker at its ground location whenever it is dropped.
  - Expected: the marker follows the current spike state and playback time, moving between carrier and ground as the spike is dropped or picked up.
  - Verification for later: check carrying, dropping (including carrier death), pickup, and the transition to planting/planted state. Confirm correct markers after seeking and switching rounds.
  - Status: requested; implementation deferred with the bug-fixing session.

- [ ] **REQUEST-05: Active enemy damaging mollies block unknown movement.**
  - Rule: unknown cannot travel through an active damaging molly belonging to the opposing team of the player represented by that unknown. Friendly mollies and non-damaging areas remain passable.
  - Ownership example: A's unknown represents B players, so A mollies block it and B mollies do not; mirror this for B's unknown.
  - Lifecycle: block only while the damaging ground zone is active; reopen at its actual end without crediting travel during blockage. Preserve uncertainty already beyond the zone rather than treating a molly as a reveal.
  - Verification for later: mirrored ownership, activation/expiry, friendly/non-damaging passability, overlap, existing uncertainty and chronological/counterfactual/cached reachability.
  - Planning: covered in design section 5 and implementation task P07a, with review addenda.
  - Status: requested; implementation deferred with the bug-fixing session.
