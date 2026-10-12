# Replay starting barriers and player state - design specification

Date: October 6, 2026. Status: design only; no application changes or data refreshes performed.

Source: [session checklist](../../bug-reports/2026-10-06/checklist.md), including the two annotated Abyss screenshots. This design covers all five requests, including the subsequently added damaging-molly movement rule. The October 5 utility/control design and implementation plan remain separate work; reconcile overlapping status, timing, geometry and parser changes before implementation.

Reviewed against the current code; corrections are incorporated. See the [design review](2026-10-06-replay-player-state-design-review.md).

## Outcome and scope

1. Abyss unknown-position regions start behind the correct buy-phase barriers and expand only as movement after barrier drop permits.
2. Player markers show replay-backed status conditions. A blinded player receives zero personal control while blinded.
3. A health bar appears between the character circle and name only after damage, showing remaining health percentage.
4. A spike icon identifies its carrier, or marks its ground position when dropped.
5. Active damaging mollies stop unknown movement for players on the opposing team; friendly mollies and non-damaging areas remain passable.

Changes concern replay extraction, visualization and map control. Impact scoring, match-stat ingestion, service configuration, domains and demo imports are outside this design. Work here does not authorize a production refresh or deployment.

## Evidence from the current checkout

Inspected local HEAD `508153f`, not a freshly fetched remote. Baseline `CONDENSE_REVISION=12`, `CONTROL_REVISION=5`; choose revisions from the actual implementation baseline later.

| Area | Existing behavior and implication |
| --- | --- |
| Barrier initialization | `app/control/engine.py:barrier_start` computes connected start ground using barrier paint; `TickRunner` seeds both Unknown and Memory from it. It rejects a side whose ground reaches an opposing start. Incorrect geometry can still include excessive territory without that particular leak check failing. |
| Abyss assets | `app/static/data/control/tags.json`, `Abyss.barrier.png` and `index.json` exist. The placement script infers lines using start positions and passage geometry; its output is evidence to review, not proof of the real barrier location. |
| Flash input | `condense.py:read_util` preserves per-target hit time and duration in `util` flash/nearsight rows. Duration can be null; older rows have only targets. |
| Flash control | `RoundInputs._flash` and Tick already blank body sight and the presence bubble during a flash. `Memory.apply` can add passive territory afterward; own-cell, fill, backfill and watcher attribution also need auditing before claiming zero personal control. |
| Status display | `replay.js:extrasFromUtil` collects status rows but not flash/nearsight hits. `drawStatuses` therefore does not currently show those hits as player conditions. |
| Health | `read_damage` keeps attacker/target/source/count runs, not health values, and drops self/unresolved-attacker damage. Those runs cannot reconstruct exact health. |
| Health candidates | Local pinned parser source declares `Health`, `MaxHealth`, `Shield`, `MaxShield` in `/Script/ShooterGame.AresAttributeSet`. This does not prove populated, correctly owned records in the reported replay. |
| Spike | `extras.py` retains a planted `Default__TimedBomb_C`; `replay.js:spikeAt` handles planted/defuse state. Existing equippable ownership helps identify a planter but is not a pre-plant possession timeline. Inventory `ItemSlots` is raw payload; current equipped item alone cannot establish spike possession. |
| Damaging areas | `RoundInputs.damage_zones` currently supplies contest checks on players. Its `DAMAGE_ZONES` list mixes sustained mollies with explosions/strikes, so it is not a suitable blanket movement-blocking allow-list. Unknown propagation and counterfactual flooding currently have no team-specific damaging-molly traversal rule. |

The screenshot match UUID and the exact player questioned at R5 18.6 s remain unconfirmed. The previous review's Abyss UUID must not be substituted merely because the map matches.

## One replay clock and evidence policy

All added events use round-relative seconds from InRound, matching the current format; minimap positions use integer `u/v` in 0..10000. Preserve actual event timing rather than resampling state changes into the movement tracks. State is right-continuous: a transition applies at its timestamp, and an effect occupies `[start, end)`. Stable source order resolves ties within one source; a documented causal order resolves cross-source ties.

Use player slots resolved through pawn/component ownership at the event time and life identity. Do not equate a component GUID with a player slot. Partial property updates preserve omitted fields; absent values are never zero. Actor destruction, dormancy, replacement, disconnect and resurrection require explicit lifecycle handling.

Every display helper answers state at the requested time from immutable event timelines. It must work identically for playback, forward seeks, backward seeks, repeat seeks and round switching. Never expose a later sample by interpolation or initialize from future knowledge.

New inputs remain optional and compatible with existing v1 rounds. Missing evidence means unavailable, not full health, no blind or a guessed carrier. Unknown optional kinds remain safe for older readers. Retain anonymous slots in standalone previews; linked identities are presentation data only.

## 1. Abyss barrier correction

At `t=0`, each observing team's initial unknown is the opposing team's accessible start ground, excluding any enemies currently located by valid evidence. Correct start ground must not include the marked forward pockets. Validate both attacker and defender starts, side swaps and overtime rather than assuming fixed A/B equals attack/defense.

Compare the screenshot with the tagged paint, generated mask, loaded node geometry, first round samples and stored-control fingerprint. Separate four possible causes: incorrect placement, a gap/alternate route at node resolution, incorrect clock/start-side selection, and stale computed data. Record which is proven; no global movement-speed reduction or permanent barrier is justified by this report.

Correct only the Abyss barrier paint when evidence supports an asset defect; rebuild its barrier asset and index entry using the existing builder. Keep walk/sight masks unchanged unless a separately demonstrated geometry defect requires correction. Check endpoint closure, diagonal bypasses, floor projection and special traversal against the geometry contract actually merged at implementation time.

Barriers constrain initialization only. After drop, passages reopen and unknown propagates at the existing movement model and elapsed time. Initial connected-component containment, earliest arrival beyond each barrier, and unchanged other-map assets are acceptance conditions. Existing invalid/missing-paint diagnostics remain visible rather than silently filling the whole map.

## 2. Player conditions and blinded control

Derive `BLINDED` indicators from per-target flash hits, using hit time, not cast time. Derive `NEARSIGHTED` separately; nearsight keeps its existing restricted-vision rule and does not become full blindness. Combine these with existing concussed, hindered, suppressed, fragile, tethered, decayed and slowed records into one normalized display timeline. Preserve multiple effects while deduplicating duplicate records; display one chip per active condition and retain sources in the tooltip.

Positive recorded duration yields a confirmed interval. Zero duration yields no lasting blind. Unknown duration/legacy timing uses only the existing explicitly identified fallback policy and is labelled estimated, never presented as confirmed. A persistent effect needs its actual removal/destruction end if available. No arbitrary null-to-one-second conversion may assert a full blind. End conditions on death/life transition so revived players do not inherit an earlier life's flash.

Use text and a distinct ring/icon; tooltips identify effect, source and interval. Core player-condition indicators stay visible when general ability geometry is hidden. Keep status chips above the character, health beneath it and the spike to its upper-right. Clamp annotations at map edges and reserve hit areas for readable tooltips.

The user's zero-control rule means no personal active/passive body claim, remembered-territory claim, presence claim, backfill/fill source or credited coverage/control mask while blinded. Blinded players remain alive, positioned and subject to damage; the enemy's knowledge and possible-position tracking still include them. They must not become dead or physically absent in the simulation.

Introduce explicit eligibility for personal control instead of zeroing only final numbers. Update aggregate claims, knowledge views, body vision used by gaps, counterfactual attribution, per-frame masks and duration-weighted summaries consistently. Retain historical memory as evidence but allow normal unknown to invalidate it; never reinstall a pre-blind snapshot when sight returns. Recovery at the actual end recalculates control using current position, information and valid memory.

Keep separate sets for living/positioned bodies, eligible personal contributors and independent utility sources. The last-living-player Q63 path in `compute_round` must honor eligibility: a blinded sole survivor still accrues alive time but zero personal control/coverage/taken-space credit. Removing a blinded player's personal contribution in either full or incremental counterfactuals leaves team state unchanged; it must not erase their physical body or independent utility. Lost-control/death accounting must not manufacture a second loss from already-disabled personal control.

Share interval normalization in a stdlib-only Python helper with an explicit browser parity contract. Analytical events occur at actual starts/ends and duration summaries split at those boundaries; serialized frames never show future effects. Reuse the October 5 exact-boundary/cadence design if implemented. Preserve sparse output cadence on rounds with no new events; do not create duplicate observer/gap events just to draw a chip.

Autonomous devices continue their recorded lifecycle and information effects while their owner is blinded. They are independent team sources during that interval and do not give the blinded owner personal control credit. Manually operated sight does not bypass the blinded player's personal-control exclusion. This attribution policy is a proposed design choice interpreting zero personal control; confirm it against the current utility rules during implementation and document the resulting team-only contribution. Blinding does not introduce suppression or destroy equipment.

Boundary tests must prove zero personal contribution throughout a confirmed blind, preserved teammate/independent-device contribution, and recovery without resurrecting stale territory. Existing non-blind behavior and nearsight behavior remain regression gates.

## 3. Health percentage

Proposed default pending the optional chat clarification: HP only, `100 * current_hp / max_hp`, clamped to 0..100 for display. Shields are not added to HP by default. This is a presentation decision, not a claim that the user settled the meaning of total health. If combined HP/shields is chosen, specify the denominator and regeneration behavior before freezing extraction/display fixtures; no hardcoded 150-point denominator.

A confirmed damage event sets `has_taken_damage` for that player in the current round. Self, friendly, fall, spike and shield-only damage count if the source confirms positive received damage. Zero-damage notifications and decay without damage do not trigger it. Once shown, the bar remains eligible for the rest of the round, including healing to full; hide while dead, and a revival resumes with that round's damage history and the new life's recorded health. A new round resets visibility.

Display a thin bar below the character and a whole-number percentage; move the name downward by the bar's reserved height. With names hidden or an unlinked preview, the bar still sits below the character. No separate shield strip or raw HP value is required. Missing exact health after confirmed damage gives an unavailable tooltip/neutral marker, never a guessed percentage.

Lay out the health/name block together, so collision-driven label movement also moves its bar and preserves the link to the character. Reserve the block's full bounds along with status chips and spike badge; use a leader when displaced. Inspect stacked players and map-edge clamping at multiple zoom levels, not only isolated markers.

Extract a compact step timeline from verified replicated vitals (preferred) or a verified complete life-change channel. Do not subtract `DamageDealt` from 100: armor, overheal, healing, decay, missing hits and overkill invalidate that calculation. Preserve individual damage timing for visibility rather than reusing 500 ms merged combat runs. An HP drop alone cannot distinguish damage from decay unless the source proves the cause.

Proposed additive contract, finalized against real records: top-level `player_state` with version 1, per-slot `vitals` events containing time, life identity, current/max HP and evidence quality, plus per-slot confirmed `damage_taken` times. Sparse updates apply only to their own fields; ownership gaps explicitly terminate validity until fresh evidence arrives. Include an initial `t=0` state only from prior buy-phase evidence still valid for that round/life. Never borrow a later state backward.

Freeze concrete field names, validity ends, source ordering, deduplication identity and validation limits only after proving representative records. Carry missing values as null/omitted with diagnostics, never default-filled floats. The optional subsection has its own version; an unsupported subsection version is unavailable while the base round remains playable. Both Python and JavaScript readers must reject malformed subsection values safely without failing ordinary replay playback.

## 4. Spike lifecycle

Choose a small spike silhouette at the upper-right of the carrier circle to avoid the health/name stack. Use the same silhouette with a contrasting outline at the projected ground location when dropped. It stays legible over control shading and does not depend on the ability-layer toggle.

Canonical states are `unknown`, `carried(slot)`, `dropped(position)`, `planting(slot)`, `planted(position)`, and terminal defused/detonated. Only one marker represents the spike. Beginning a plant keeps the carrier icon until planting completes; cancelled planting returns to the known carried state. A planted Bomb row takes precedence at completion and the existing planted drawing/HUD remain the single source for post-plant visuals.

Give one dedicated spike drawing pass ownership of carried, ground and planted markers, outside the general abilities toggle. Remove the old planted glyph from the abilities pass to avoid duplicates; retain its visual style and HUD semantics. The HUD need not appear before planting. Terminal state keeps the current post-plant result behavior, with no lingering carrier/drop marker.

Require recorded possession/attachment or inventory membership, not selected weapon, equipping animations, eventual planter identity or nearest player. Ownership resolved from sparse replication needs temporal validity; a last instigator can remain after dropping and must not count as continuing possession. A carrier death does not prove the exact drop location: use the recorded world object/drop transform; preserve unknown position if absent. Apply pickup, drop and movement at their actual timestamps. Do not animate across a missing position interval or project an unknown position to the map origin.

Proposed contract: `player_state.spike`, a time-ordered list of transitions with state, optional slot or `u/v`, evidence and validity. Keep source identity available for diagnostics. Initial carried/dropped state may come from valid buy-phase evidence. A new round resets it; missing pre-plant history on an old round still permits the existing planted marker/HUD.

## 5. Enemy damaging mollies block unknown movement

Only a sustained ground molly that is currently dealing damage acts as a temporary unknown-traversal barrier. Classify the active damaging zone, not its projectile, dormant deployable, warning/arming effect or a broad ability radius. A player need not actually be hit for an active damaging molly to block potential movement. Do not treat slows, decay-only areas, smoke, non-damaging fields, instant explosions or every `DAMAGE_ZONES` entry as mollies.

Friendly/enemy is relative to the player whose possible movement is being modeled. For observing side A, `unknown[A]` represents B players: A-owned damaging mollies block their propagation; B-owned mollies do not. Conversely, B-owned mollies block `unknown[B]`, which represents A players. Use current round side mapping and temporally resolved ownership; do not compare the zone owner to the observer and accidentally invert the rule. Unresolved ownership is not automatically hostile: retain the existing no-added-block behavior and report missing evidence.

Use each zone's verified damaging footprint, floor/height applicability and actual active interval `[start,end)`. Activation begins blocking; expiry, destruction or a recorded stop reopens passage. An owner's death, blindness or departure does not end a deployed molly if its recorded damaging lifecycle continues. Overlapping hostile zones remain blocked until the last applicable zone ends. Friendly overlap never cancels a hostile blocker. Apply footprint changes or pulses only where verified damage intervals support them.

This changes possible movement, not sight or physical map geometry. Do not add a sight wall, award a locate/reveal, assume damage/death, or change the player's recorded position. A molly appearing does not prove the absence of an enemy inside its footprint and does not erase possible positions already on its far side. Retain pre-existing uncertainty, including interior positions when otherwise valid, but prevent blocked nodes/edges from carrying propagation through the damaging footprint. Actual recorded position/locating evidence still supplies its ordinary source even when the player really crossed the hazard.

Separate occupancy evidence from traversable paths rather than blindly deleting all reached cells under a molly. Retain boundary/interior sources as appropriate without allowing them to bridge an active hazard. On reopening, newly enabled routes accrue movement time from the reopening instant; neither a retained old arrival timestamp nor a later coarse tick may credit travel during the blocked interval. Multiple activations and expiry between display frames must respect the same chronological analytical schedule as other temporary barriers.

Use one team-aware traversal restriction across chronological Unknown spreading, per-enemy union, knowledge-view reachability, special/diagonal path checks, full and incremental counterfactual unknown flooding, route/entry history and cached/live timing-gap consumers. Do not permit any alternate reachability helper to bypass the hazard. Apply geometric restrictions to traversed ground on the applicable floor; do not block an unrelated floor or a verified traversal that bypasses the footprint. Preserve source/lifecycle state required to replay the same restrictions from cache. Removing a personal contributor must not silently cancel an already deployed autonomous molly; reconcile any explicit utility-removal counterfactual with the shared lifecycle contract.

Acceptance requires mirrored A/B ownership cases, friendly/non-damaging passability, correct activation/end timing, no tunneling via diagonal/special routes, preserved far-side/interior uncertainty without unsupported clearing, and correct post-expiry arrival times. Match chronological and counterfactual/cached results against independent expected reachability and durations. Do not add a new UI switch or molly visual style for this request; existing utility rendering should align with the damaging lifecycle used by the engine.

Apply the restriction only to operations representing travel through ground. A geometric information hypothesis (for example, possible cast positions or a sound-location area) may legitimately place uncertainty across a molly; do not clip it merely because a walking route is blocked. Preserve existing verified teleport/displacement semantics that bypass the ground footprint, without allowing ordinary walking or diagonal/special shortcuts through it.

## Compatibility, refresh and completion

Health/spike extraction changes require one coordinated condenser revision bump. Update full and streaming readers, cheap line needles and exact filters, serializer contracts, worker/local ingestion and standalone helpers together. If the pinned parser cannot export the verified state, implement a reproducible parser patch/pin update first, bind exports to their parser build/patch and content identity, and re-export originals before re-condensing. A current BUILD file cannot prove an old export contains new data.

Barrier hash changes invalidate Abyss control through existing fingerprints. Engine eligibility/timing changes require the next unused control revision and matching cached/live gap freshness. A viewer-only status repair can use existing hit data without re-condensing; geometry/control fixes require recompute, and extraction fixes require re-condense (plus re-export only if necessary). A changed recipe currently invalidates dependent control even for new presentation-only data; account for this real behavior rather than promising no recompute.

The molly traversal rule belongs in the same integrated control revision. Include consumed damaging-zone classification, footprint, ownership and lifecycle semantics in freshness/cache keys. If existing utility rows prove the required signals, recompute control and dependent gaps without inventing an extraction refresh; missing active-state/footprint evidence follows the conditional parser/re-condense path already described above.

Current storage replaces a complete replay on recipe change and removes its prior round/control rows. Sample previews may use R1/R5 only, but a future ingestion must provide every round and target whole matches. Refresh estimates must include restoring every removed control round for those matches; do not confuse a two-round visual sample with a two-round replacement write.

Completion requires: confirmed report match/time, tested barrier containment and propagation, replay-backed status intervals and zero blind control, verified health and spike extraction on real examples, enemy-only active-molly traversal and expiry checks, deterministic seeking, old-data compatibility, visual inspection at narrow and wide viewport/zoom, and a targeted refresh runbook. Planning review is not execution evidence. Any missing raw evidence remains explicit, with unaffected work able to proceed.

Screenshots and raw identifying exports are local review evidence; do not include them in a public implementation commit. Commit anonymized focused fixtures. Validate a few local rounds before proposing production data refresh; preserve friends/demo database separation and both deployed URLs.
