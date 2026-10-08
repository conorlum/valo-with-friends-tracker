# Replay utility and map-control corrections — agreed design

Date: 2026-10-05. Source: the completed replay review and the owner's subsequent answers in chat.
This document records the design for the implementation plan at
`../plans/2026-10-05-replay-control-review-impl.md`.

There are 28 active checklist items, numbered 2–29. Item 1 was removed because clicking a round already starts playback.
The original checklist and nine screenshots are local at
`%TEMP%/control-review-2026-10-05/`.

## Playback and visuals

- **2:** Space toggles playback without first focusing the replay. Preserve typing in editable controls.
- **3:** Finished playback advances to the next round and starts it. The final round stops.
- **11:** Tracers run to the first wall rather than a fixed 25 m. For this release, low boxes also stop tracers. Matching shots to damage, victim endpoints, wallbang drawing and inferred shoot-over boxes are later work.
- **20:** Phoenix Blaze is a wall line, including both sides of map walls. Prefer recorded segments/curve. If shape is unavailable, identify the limitation and agree a straight cast-facing fallback before treating it as precise geometry.
- **22–23:** Show all ability projectiles by default, including Omen Paranoia, KAY/O FLASH/drive and Phoenix Curveball. Provide an independent hide toggle.
- **24–25:** Give Deadlock utility proper visuals and investigate dropped wall/sensor/ult data. Raw Summit data proves eight Barrier Mesh deployments; CableJam/CableJamRoot naming is currently discarded or unmatched. Sensor and ult class definitions alone do not prove use.
- **7:** Explain unknown changes simply: a timestamp, player and short reason in the Control panel, with seeking. Diagnose the actual Abyss R6 collapse before calling it a bug.

## Knowledge and vision

Unknown is per observing team and per living enemy. Team unknown is the union of those enemy regions. Information about one enemy never removes another enemy's region. Keep the existing rifle shift-walk propagation model and true-position sources except where a specified lifecycle rule changes them.

- **4, 13:** Haunt, recon and player-controlled scouting utility clear the area they actually see and locate enemies they reveal. Calculate ability LOS from recorded x/y/z against available floors and cover. Drone/dog cones respect their facing, active use and ranges. Fine-tune excessive Haunt/dart coverage later through replay review.
- Ability heights already exist in revision-11+ data: object z, thrown z, trip end_z, path z and player track z. Current main has no committed map height assets; use the existing explicit flat fallback where geometry is unavailable. Reuse the map-feature/cover contract being implemented rather than create a competing model.
- **5:** Cypher suppression pauses his trips/camera; they resume at suppression end unless destroyed. Suppression does not remove a living player's own sight.
- **6:** Live Cypher trips seal unknown, including diagonal paths and location-area fills. Inspect this reported case against the already-merged fixes before changing the rule again.
- **9:** Each Neural Theft pulse locates every eligible living enemy exactly. Evolution Veto is excluded.
- **10:** KAY/O knife gives unknown information only when it suppresses zero enemies or every living enemy. Zero hits clear the suppress volume for susceptible enemies; all hits restrict them to that volume. Partial hits do nothing, including no per-target shrinking.
- **14:** A recorded Skye enemy target hit means her sound cue played, regardless of blind duration, including zero. No separate cue-verification task. Any enemy hit gives no unknown change. A complete activation with zero enemy hits clears the range-and-LOS footprint for susceptible enemies. Target facing does not change this detection rule. Missing/unresolved records are not an explicit zero-hit activation.
- **15:** Living KAY/O retains vision during NULL/cmd. Ult suppression pulses do not reveal enemies or clear unknown.
- **19:** Evolution Veto is immune to utility blinds, suppression and reveal/tag effects. Ordinary sight and drone/dog cones can still see him. Fade utility and Wingman do not trigger on him. Gekko's flash glob can travel toward him without blinding him. Cypher trips can trigger without concussing him; an unseen, unshot trigger still supplies evidence of him. An empty knife/Skye result must not clear where he could be.
- **29:** Observing Reyna's eye constrains her unknown to possible casting positions around that eye, through walls. The candidate placement distance is 10 m, distinct from its nearsight effect range; retain an in-game measurement fallback if needed. Apply the constraint at the information timestamp and allow subsequent movement. Verify Clove's kill-location updates in the same case. Do not force aggregate unknown to zero merely to match a visual expectation.

## Movement barriers

- **8:** Mark the reported Abyss box so unknown walks around it. Use movement and sight masks according to the real obstacle, not a sight block solely to slow walking.
- **26:** Deadlock Sonic Sensors do not hold unknown; enemies can walk past without triggering.
- **27:** Deadlock Barrier Mesh and Sage Barrier Orb block unknown through intact sections while active. Broken sections/expiry reopen passage. Movement blocking and sight blocking are separate properties.
- **28:** Vyse Shear permits the triggering crossing, then blocks further passage while raised. Preserve unknown already across it. End of the wall reopens passage.

## Teleports and temporary bodies

- **12, Omen:** Seen and completed means zero unknown at that sighting. An unheard destination adds unknown throughout spaces outside every living enemy's hearing range whether completed or cancelled. An unheard cancellation does not retract this extra unknown. A heard/seen cancellation adds nothing and preserves pre-ult uncertainty. Heard-only completion keeps the previously specified behavior, with normal actual-position spread at landing. He cannot move during the channel, so his movement propagation pauses. What his temporary shadow sees counts for control. Implement unheard broadening at destination manifestation/sound time, before the outcome.
- **16, Yoru Gatecrash:** Continue actual-body unknown. Hearing an activated/faked beacon adds uncertainty spreading from the beacon; vision clears it normally. Hearing Yoru teleporting adds no special change.
- **17, Yoru drift exit:** Heard exit causes no special change. Unheard exit adds unknown throughout space outside the opposing team's hearing range.
- **18, Phoenix:** His stationary Run It Back return body stops generating unknown while the ult is active. Model the active moving ult body separately from the return marker.
- **21, Waylay:** A heard completed recall removes her old spread since return-point placement and restarts at that point. An unheard recall preserves the earlier region. A recall cannot be faked.

## Verification and data refresh

Use event times, explicit owners and living observers. Keep hypothesis changes, route history and control counterfactuals consistent. Ability-specific hearing ranges need sourced values or focused in-game measurements; do not silently substitute footstep ranges.

Preserve existing judged cases. Preview one or two local rounds per implementation batch before any broad recompute. A viewer fix does not need a database recompute; dropped utility requires re-condensing original data before control can be recomputed. Full-corpus refresh remains a separate owner decision after sample acceptance.

Replay examples:

| Match | Round/time | Checklist |
| --- | --- | --- |
| Abyss `650e6c6c-a0b4-425d-ba36-f0c0559e8f37` | R6: 6.6, 12.5, 16.4, 20–22 s; R7: 13.5, 31.0 s; R11: 10.5 s; R21: 73.4 s | 4–10, 20 |
| `47a5f123-7ba7-40b2-8d34-c3a386817dea` | R3: 15.5 s; named flash examples to select from its data | 21–23 |
| Summit `2db387cd-f254-4f42-bb56-319c08ababe0` | R1: 52.193–53.943 s; R11: 7.830–8.541 s; R12: 33.4 s | 24–25, 29 |
