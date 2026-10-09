# Replay starting barriers and player state - implementation plan

Date: October 6, 2026. Status: plan only. Application implementation, runtime tests, visual previews and database dry-runs have not run.

Design: [reviewed specification](../specs/2026-10-06-replay-player-state-design.md). Design review: [findings and corrections](../specs/2026-10-06-replay-player-state-design-review.md). Source: [five-item checklist](../../bug-reports/2026-10-06/checklist.md).

Implementation-plan review: [findings and incorporated corrections](2026-10-06-replay-player-state-impl-review.md).

## Execution baseline

Current inspected HEAD is `508153f`, with condenser revision 12 and control revision 5. There are unrelated local changes in `webapp/docker-compose.yml` and October 5 planning documents; preserve them. This plan is additive and does not replace the October 5 utility/control plan.

At implementation time, inspect current instructions, fetch the remote and use a suitable isolated managed worktree based on the actual updated main, with a `codex/` branch. Inspect existing attachments first. Reconcile merged/in-progress utility, live-claim, geometry and exact-timing work. Do not merge unrelated branches automatically or assign revisions already reserved elsewhere.

Commands below run from the implementation worktree's `webapp/`. Use its working Python environment. Both `.venv` and `.venv313` currently exist in the main checkout; examples use `.venv313`. A worktree may reuse the main checkout's interpreter with its own cwd. Node is currently available; record actual Node execution rather than accepting skipped viewer tests as a pass. Keep raw exports, screenshots and identifying source traces under `%TEMP%`, with anonymized fixtures in commits.

Implement in the order below, with a reproduced defect or meaningful failing test before the correction. Run focused checks per task; run the broader suite once at integration unless further failures/changes justify repetition. Commit only task files when implementation is later authorized. This request authorizes planning only.

## Dependency order

`P00 baseline -> P01 evidence -> P02 contracts -> P03 extraction -> P04 viewer state helpers -> P05 annotation layout -> P08 integration -> P09 previews/refresh runbook`.

`P01 -> P06 Abyss geometry -> P08` and `P02 -> P07 blinded control -> P08` can proceed independently where their evidence is available. Missing spike/vitals source records must not block a verified barrier or status-display fix. Do not declare the whole feature complete while a source gate is open.

`P01/P02 -> P07a enemy damaging-molly traversal -> P08` adds the fifth request without renumbering existing tasks. Share temporary movement restrictions and chronological boundaries with P06/P07 and the October 5 wall work; do not implement a separate reachability engine.

## P00 - Establish the actual baseline and overlaps

**Read:** `AGENTS.md`, October 5 replay utility design/plan/review, current diffs in `app/control/engine.py`, `app/control/observe.py`, `app/gaps/cache.py`, `app/replays/{format,contract,condense,extras}.py`, `app/static/js/replay.js`, and any merged geometry feature contract.

1. Record base commit, revisions, geometry hashes, interpreter and Node versions in a local validation report.
2. Build an overlap table: shared status normalization, exact event boundaries, observer/cache changes, parser pin/provenance, geometry builder and marker rendering. Reuse completed work; explicitly assign ownership of unfinished overlap rather than creating another independent pipeline.
3. Capture baseline checks for the focused replay/control/gap tests and identify existing failures or unavailable database fixtures. No live recompute or parser rebuild is needed for this task.

**Done:** a clean implementation workspace and an evidence-based dependency table.

## P01 - Identify the reports and prove data availability

**Existing tools/files:** `scripts/with_friends_db.py`, archive/preview helpers, `app/replays/contract.py`, local exported manifests and the pinned parser source. Add a focused read-only diagnostic script only if existing tools cannot produce the evidence.

1. Match the screenshots to a replay using map, roster and round outcomes through local metadata or a read-only friends-DB query. Verify R1 and R5 18.6 s against the same identified source; do not borrow the earlier review's Abyss UUID without proof. Identify the marked player by position and source slot.
2. Select only R1 and R5 initially. Record source VRF SHA, export provenance/content hashes, recipe and round clocks in a local sample manifest. Retrieve existing archived inputs where possible; do not export the whole archive.
3. For barriers, overlay annotated passages with current paint/generated mask/node components, first samples and both sides' start ground. Record whether the displayed control is stale. Trace alternate paths and the mask conversion, not only visual line placement.
   The first screenshot does not show playback time: establish it if possible, or explicitly compare initial and later frames without claiming exact screenshot reproduction. Correct later propagation is not a barrier-placement defect.
4. For blind status at 18.6 s, list the relevant target hits, actual duration, source owner, life and expiration. If there is no blind, document the actual condition without forcing a blind fixture to match the screenshot.
5. For health, find populated AresAttributeSet/life-change records and verify temporal component-to-pawn ownership, initial values, sparse updates, healing and damage cause. Prove whether these are available for every relevant player or only a subset. Class definitions are not coverage evidence.
6. For spike, prove initial possession while a gun is equipped, a drop, a pickup and planting/cancellation from recorded membership/attachment/world-object state. Inspect raw `ItemSlots`, owner/outer relations and ground transforms; equipped-item/Instigator alone is insufficient.
7. Add one additional local round only if needed to cover healing, carrier death or a missing lifecycle transition. Produce an evidence table of available, missing or parser-required signals.
8. For damaging mollies, select a verified active ground-zone example with owner/side, footprint and damaging start/end. Inspect whether existing util rows already prove those signals. Separate sustained damaging ground effects from projectiles, inactive devices, warnings, non-damaging zones and explosions/strikes mixed into `DAMAGE_ZONES`. Do not require an actual hit to establish a damaging interval. Record missing signals for P03's conditional extraction path.

**Done:** confirmed report identity and fixtures for each available signal. Any unavailable signal has a precise next step. No stored round or public fixture containing identifying source data is written.

## P02 - Freeze state and timing contracts

**Files:** `app/replays/format.py`; proposed stdlib-only `app/replays/player_state.py`; `app/control/engine.py` integration points; `tests/replays/test_replay_format.py`, new focused player-state tests; browser parity tests in `test_replay_viewer.py`.

1. Add optional top-level `player_state` with its own version 1. Freeze concrete structures from P01; proposed shape:

   - `vitals`: per-slot ordered events containing `t`, life identity, current/max HP, optional verified shield values, explicit quality and validity end.
   - `damage_taken`: per-slot distinct confirmed positive-damage events with `t` and life identity; identity/source sequence is available for deduplication.
   - `spike`: ordered state transitions with `t`, state, optional slot or minimap `u/v`, evidence and validity.

   Exact upstream property names/relationships are evidence-driven; do not invent typed raw events simply to populate this shape. Keep wire fields minimal and bound counts/slot values/coordinates/numbers. Reject NaN, negative invalid maxima, malformed states and unsupported subsection versions safely.
2. Define partial-update behavior and validity breaks. Missing state is unavailable, not 0 HP or an indefinitely carried spike. Define stable ordering and same-time precedence: actual completed planting supersedes a carried state; a later pickup supersedes an earlier drop in source order; contradictory unresolved records become unknown with diagnostics. Never infer a pickup from nearest distance.
3. Introduce a pure status interval normalizer for existing flash/nearsight/status util rows. Use `[start,end)`, merge duplicate/overlapping same-condition intervals for display while keeping source attribution, and intersect with lives/round end. Treat zero-duration and unknown-duration differently. Export legacy fallback parameters through a shared lightweight policy or normalized payload; parity tests must prevent independent browser/game-value defaults drifting.
4. Reconcile the exact-event architecture already implemented from the October 5 plan, or implement the scoped boundary integration needed here. Actual hits/ends split analytical state and duration accounting; published frames never select future state. Preserve sparse cadence and one observer delivery per analytical boundary.
5. Record HP-only as the proposed assumption unless the chat answer changes it. Before health implementation, freeze an explicit formula; combined HP/shields would need a valid measured maximum-capacity basis and regeneration semantics.
6. Freeze the active-molly classification and movement restriction contract from P01: source identity, temporally resolved owner/round side, damaging interval, footprint and floor applicability. Friendly/enemy is relative to the unknown mover; `unknown[A]` tracks B players and is blocked by A mollies, not B mollies. Keep uncertain ownership/activation diagnostic rather than guessing hostile. Use existing ability rows when complete; if additional fields are necessary, share P03's extraction/versioning path.

**Checks:** format round-trip, missing/unsupported optional subsection, malformed subsection preserving ordinary playback, interval end-exclusive behavior, overlapping and zero/null duration, life replacement, equal-time order, Python/JS boundary parity. Use synthetic fixtures with distinguishable event times between 1/16-second samples.

**Done:** no unresolved schema/timing ambiguity; source-dependent branches are recorded explicitly.

## P03 - Extract health and spike state through every ingestion path

**Files:** `app/replays/{player_state,contract,condense,extras,format}.py`, relevant parser pin/build/export helpers if required; `replay_worker/server.py` and parser application build path only as necessary; existing ingestion scripts; tests for contract, streaming, condenser, extras, isolation and worker output.

1. Prefer the current export's verified records. Add both cheap line needles and exact streaming filters for required records, and retain ownership metadata needed to resolve attribute/inventory components over time. Share reconstruction with full loading; prove equality on anonymized populated fixtures.
2. Track sparse vitals without resetting omitted fields. Seed round-start state only from valid buy-phase evidence for the current life. Maintain validity through known replication semantics; explicitly invalidate ownership/decoder gaps. Preserve death/resurrection/disconnect boundaries and record current/max values instead of calculating HP from outgoing damage.
3. Extract damage visibility events separately from merged control damage runs, retaining self/friendly/environment damage and shield-only damage if confirmed. Deduplicate multiple RPC representations of one received hit using verified source identity; never double-subtract damage. Handle decay separately and healing as a vitals update.
4. Reconstruct pre-plant spike membership/attachment with explicit detach/drop/pickup, world-object position and lifecycle end. A weapon switch must not remove the carrier icon. Ignore unrelated equippables. Keep Bomb ability rows for planted state, defuse and HUD compatibility.
5. If raw fields are undecoded or absent, first make a reproducible pinned parser patch/pin update with focused decoder tests. Update local and worker build paths together. Bind exported manifest/sidecar provenance to commit, patch identity, VRF SHA and output-content hashes; enforce it at local/worker/preview ingestion. Old export plus new BUILD metadata must fail the new-data completeness check. Re-export only the sample original before judging extraction.
6. Add extraction counters/diagnostics for unresolved owners, invalid/max-health values, missing damage cause and conflicting spike state. Keep diagnostics local or anonymous. Maintain import isolation: web/cold ingestion code must not load numpy/scipy/control geometry to read player state.

**Checks:** full/streaming parity with real-shaped sparse payloads; initial state while gun equipped; fields omitted versus zero; component re-parenting; disconnected/replaced life; positive/zero/self/environment damage; healing/decay; drop/death/pickup/plant cancel/completion; parser-old-export refusal if patched; local/upload/standalone payload parity. Run focused `test_replay_contract`, `test_replay_streaming`, `test_replay_condense`, `test_replay_extras`, `test_replay_isolation` and relevant worker tests.

**Done:** new condensed sample contains proven expected state events. A control recompute alone is not considered an extraction fix.

Only select R1/R5 blobs for local previews. Any later ingestion through `store_replay` must supply the complete validated replay: a changed recipe currently replaces the whole replay and its rounds/control rows. Never store a two-round preview subset as a complete match. Preserve source/upload ownership, archive/tombstone behavior and link metadata through the established source-specific store path.

## P04 - Build deterministic viewer state selectors

**Files:** `app/static/js/replay.js`; `app/replays/extras.py:rounds_extras` and standalone helpers if their existing shapes need adapting; `tests/replays/test_replay_viewer.py`, new state helper tests.

1. Normalize flash/nearsight hit rows into visible player conditions and combine with existing statuses. Do not replace the Utility tab's cast history. Guard invalid optional payloads and show estimates/unavailable data honestly.
2. Add pure `playerConditionsAt`, `healthAt` and pre-plant spike-state selectors over immutable indexed timelines; names are proposed. Select the latest valid event at or before `t` within that life, never future interpolation. Binary search/index once per loaded round avoids rescanning exports per animation frame.
3. Health visibility uses any confirmed damage at/before `t` in the round, independently of the current life; hide dead players and reset on round switch. Healing to full retains the bar. Missing vitals does not produce an invented percentage.
4. Resolve current spike state together with existing `spikeAt` post-plant results; one explicit state feeds rendering. Cancelled plant, carrier death without drop position and old rounds lacking player_state receive deterministic supported/unknown behavior.
5. Verify load/request races: stale round promises cannot install state for a newly selected round. Existing fetch caches must not accumulate future damage/status/carrier state.

**Checks:** Node tests seek before/during/after effects, arbitrary backward/forward/repeat order, round switching, revival, equal-time events, heal-to-full, shield-only damage, missing timelines, unsupported versions, weapon switch, drop/pickup/cancelled plant, and old planted HUD results. Tests must execute Node, with skip counts reported.

## P05 - Render conditions, health and spike as readable annotations

**Files:** `app/static/js/replay.js`, player template/CSS only where needed; `scripts/render_replay_standalone.py`; viewer/layout tests.

1. Use one annotation layout per player: character circle, health/name block below, active condition chips above and spike badge upper-right. Measure the whole health/name block for existing collision handling and leader lines. Reserve status/spike bounds and clamp at map edges. Health stays attached when names are hidden or absent in standalone mode.
2. Draw confirmed `BLINDED` and separate `NEARSIGHTED` with text/ring or glyph; retain all other active recorded conditions and source/time tooltips. Indicators are independent of the general abilities toggle and respect the existing dimming in team-knowledge views.
3. Draw a thin health bar and whole-number percentage, with dark backing/outline for contrast. Never show raw HP/shield strips. Unknown exact health after damage uses a neutral unavailable marker/tooltip. Annotate health/status/carrier information in existing player hover text and accessible replay state text where useful.
4. Move spike glyph rendering into one dedicated pass outside the abilities toggle. Reuse the existing planted style; remove it from the general abilities drawing branch. Draw drop locations below player annotations but above map control; carrier badge above the circle layer. Keep the current post-plant HUD/defuse calculations and one marker at every time.
5. Ensure annotations and tooltip hit areas use the same zoom/transform and do not mask the existing player selection action.

**Checks:** canvas/mock tests for paint order, annotation bounds and one spike glyph; isolated and stacked player fixtures; names/abilities toggles; death/revive; unlinked preview; map edge; multiple conditions; normal and zoomed maps. Visual acceptance is required in P09 because mock canvas checks cannot prove readability.

Explicit plant-transition gate: with abilities on and off, just before/at/after completion the carrier icon becomes exactly one planted marker and the same post-plant HUD state. No spike disappearance or duplicate marker is permitted.

## P06 - Correct Abyss barriers using the proven diagnosis

**Files:** `app/static/data/control/tags.json`, `Abyss.barrier.png`, `index.json`; `app/control/{geometry,topology,engine}.py` only if P01 proves a conversion/initialization defect; `tests/replays/test_control_barriers.py`, `test_control_unknown.py`, geometry/assets tests.

1. Reproduce at initial time and establish expected behind-barrier probes from the real map/screenshot. Use the existing tagger to adjust only proven incorrect Abyss strokes. Review wall-to-wall closure and the placement script's suggestions against actual barriers rather than globally rerunning inferred placement.
2. Build only Abyss geometry: `.\.venv313\Scripts\python.exe scripts\build_control_geometry.py --map Abyss`. Diff generated assets and JSON; no other maps, walk/sight changes or height rebuild belongs here unless separately proven necessary.
3. Add deterministic barrier/component probes on the actual Abyss asset, plus toy regression fixtures for endpoint/diagonal leaks, players on barrier cells and the applicable floor/special cases. Check A/B swaps and no ground crossing the wrong start barrier at initialization.
4. Compare earliest post-drop propagation with graph walking distance and existing UNKNOWN_MPS. Test one instant at `t=0` and later times independently; barriers must not persist as movement blockers.
5. If assets are already correct and the bug is stale data or an incorrect starting clock/side, fix that demonstrated path instead. Preserve leak diagnostics and avoid arbitrary movement-speed or global unknown-rule changes.
   If the marked pockets appear only after a physically valid arrival time, record that disposition and the remaining reported barrier mismatch; do not change assets merely to force an unknown-time screenshot into the initial-state expectation.

**Checks:** `test_control_barriers`, `test_control_geometry`, `test_control_assets`, selected `test_control_unknown`; original masks/hashes outside Abyss unchanged; local R1 scene matches computed/encoded/viewed initial state and later propagation.

## P07 - Apply zero personal control throughout blindness

**Files:** `app/control/engine.py`, `app/control/observe.py`, `app/gaps/{cache,detect}.py` only as needed; shared status helper; control engine/unknown/knew/reference and gaps observer/cache tests.

1. Add explicit personal-control eligibility per living player/time. Keep all living bodies for physical positions, lives, enemy information and unknown sources; avoid redefining `team()` globally because many callers need living rather than contributing players.
2. Audit every claim path: Tick raw/body sight, active/passive/presence, own-cell live clearing, Memory overlay, territorial fill sources, backfill, safe/control counterfactuals and taken-space credit. Keep historical memory valid only where ordinary unknown has not eaten it; blind removes its current personal claim without freezing the territory.
3. Separate independent autonomous utility from blinded personal claims. Keep recorded device lifecycle/information and team utility contributions, but no blinded-owner coverage/control credit. Manually operated sight must not restore personal contribution during blindness. Do not destroy devices or apply suppression.
4. Adapt full/incremental counterfactual inputs to remove a personal contributor rather than deleting a blinded physical body or independent source. Removing an already-ineligible personal contributor produces no team-state difference. Update cache/observer fields only where they need explicit eligibility; the gap detector must consume actual body vision and independent utility consistently.
5. Guard `compute_round`'s last-living-player Q63 branch: a blinded sole survivor receives zero personal control and coverage, while their alive seconds continue. Prevent death/taken-space metrics from crediting disabled control or charging a second loss. Preserve team accounting and distinguish uncredited independent utility in summaries where needed.
6. Split duration-weighted accounting at actual blind starts/ends; reuse the existing exact-boundary implementation from P00/P02. Recovery is recomputed using current evidence. Zero/null/overlapping flashes and nearsight must follow the shared policy.

**Checks:** during confirmed blindness, all personal masks and control/coverage/taken-space credit are zero; own-cell/memory/fill cannot reintroduce them. Verify lone survivor, another teammate, independently functioning trip/device, operated watcher, incoming damage/death during blind, memory eaten during blind, and clean recovery. Full/incremental counterfactual parity plus independent expected areas/durations; all-personal-blinded case; body still visible to opponents; no new locate/death event. Cached/live gap parity and a no-blind fixture unchanged.

Use an independent expected-duration fixture where a blind starts and ends between published frames: alive time is unchanged and personally credited area-time over that interval is zero. If PlayerView or the positional cache tuple changes, update `app/gaps/cache.py` writer/replay reader together and test old-format refusal/cache miss rather than silently defaulting eligibility.

## P07a - Block unknown traversal through active enemy damaging mollies

**Files:** `app/control/engine.py` (`RoundInputs`, `Unknown.apply/_spread`, `_area`, Tick counterfactual reachability), shared movement topology/lifecycle helpers from the actual baseline, `app/control/observe.py` and `app/gaps/cache.py` where snapshot fields need extending; optional utility extraction from P03; `tests/replays/test_control_unknown.py`, `test_control_knew.py`, `test_control_engine.py`, gap cache/observer tests and proposed `test_control_mollies.py`.

1. Build a verified allow-list/metadata view of sustained damaging molly zones. Reuse proven footprints from existing utility metadata; do not turn the whole `DAMAGE_ZONES` list into barriers. Map activation and stop/expiry/destruction to exact analytical boundaries, including pulsed/off intervals only if supported. No blockage during flight, warning/arming or a non-damaging phase. An active zone blocks even when nobody takes damage.
2. Produce a team-aware traversal mask/edge restriction at each analytical instant. For B-player uncertainty seen by A, A zones block and B zones are passable; mirror for A-player uncertainty seen by B. Resolve ownership at the event time. Unknown owners add no guessed blockage and increment an input diagnostic. Owner death/blindness does not cancel a still-active zone.
3. Keep occupancy evidence separate from traversability: preserve uncertainty already beyond or inside the activated zone unless ordinary sight/locating evidence clears it, but prevent it from using the footprint as a bridge. Do not reuse a shared `sealed` mask that discards team ownership or pretend the zone is a wall/sighting. Retain actual-position source rules without allowing the exception to reopen the entire zone.
4. Update reachability and `free_since`/frontier timing so reopening starts new movement from actual expiry, never from pre-activation arrival times. Handle overlap until the final hostile zone ends, reactivation, footprint changes and exact endpoints. Preserve unaffected routes around a partially covered corridor. Prevent diagonal corner cuts and special-link bypasses through the footprint while retaining verified routes on other floors or outside it.
5. Use the same restrictions for main per-enemy spreading/aggregate union, area/source reachability, team-knowledge computations and full/incremental `unknown_without/_flood` paths. Audit route/entry history and the chronological/cached gap replay stream. Serialize active source identity, owner, footprint/interval or equivalent reproducible traversal state where a cached consumer needs it; update writer/reader and format guards together.
   Classify each helper by semantics before applying the mask: ground-travel reachability obeys it; geometric information hypotheses and verified teleport/displacement can supply a source across it without proving that someone walked through. Add a source-across-zone fixture that remains possible but cannot then propagate through the active ground hazard.
6. Keep sight masks and recorded player movement unchanged. No automatic locate, reveal, hit or death follows from a blocked potential route. Retain deployed autonomous mollies when a personal contributor is removed; any explicit utility-removal counterfactual must regenerate restrictions via the same source/lifecycle contract, not bypass them accidentally.

**Checks:** mirrored A/B and round-side-swap fixtures; hostile damaging molly blocks, same ability friendly passes, non-damaging areas and instant blasts pass; unknown owner fallback; active without recorded hits; pre-activation/at-start/just-before-end/at-end, with boundaries between output frames; retained far-side/interior uncertainty with no spurious locating event; independent earliest-arrival expectation after expiry; overlap and owner death/blindness; a route around partial coverage; diagonal/special/floor cases. Compare full/incremental counterfactuals and cached/live gaps against independent expected reachable cells/times, not only parity. A no-molly round remains unchanged.

**Done:** one validated local active-molly sequence plus focused synthetic cases proves the team/lifecycle rule end to end. If required inputs were missing, use the P03 recovered blobs rather than claiming an old public blob proves extraction.

## P08 - Version, integrate and verify

**Files:** `app/replays/{format,control_format}.py`, `app/services/{replay_control,replay_gaps}.py` freshness as needed; `app/gaps/cache.py`; store/fingerprint/regression tests; targeted refresh script support if missing.

1. Select one unused condenser revision for new extraction and one unused control revision for eligibility/timing changes. Keep base FORMAT_VERSION 1 and the new optional subsection version independent. Do not bump a byte format merely for added optional JSON.
2. Barrier hash must invalidate Abyss control. Updated engine revision/recipe must invalidate dependent control and gaps. Version any changed observer-cache shape and align writers/readers; old caches cannot silently omit eligibility. Test semantic input mutation, not only constants in a test.
   Integrate P07a in the same unused control revision. Hash consumed molly classification/footprint/lifecycle parameters and any required cached traversal schema; test that a semantic zone-policy change invalidates control/gaps while a prose edit does not. Existing complete util rows need engine recompute; re-condense/re-export only for proven missing inputs.
3. Compare real sample before/after; status-only old hit data works without re-condense. Account for the current recipe-driven control invalidation when adding viewer-only inputs, rather than treating health/spike extraction as magically free of recompute. Report the exact refresh scope and costs.
   Current `store_replay` replaces all rounds/control rows when the recipe changes, so re-ingested matches need their entire removed control set restored, not only R1/R5. Verify preservation of linked match data and existing scoring configuration; this work does not change scoring rules or activate scoring flags. Source-specific storage may assign a new replay ID, so verify cache/URL/context behavior against the UUID and actual stored identity.
4. Run `.\.venv313\Scripts\python.exe -m pytest tests\replays -q`, plus `.\.venv313\Scripts\python.exe scripts\control_cases.py` where its required sources are available. Run focused new local cases using the validated P01/P03 sample blobs; do not fall back to public old blobs for new extraction tests. Preserve existing frozen references unless a deliberate semantic change has an independently justified expected result.
5. Record executed/skipped checks, Node results, source manifest identities, array/frame counts, round-size deltas and runtime. Separate intended control changes from regressions. Keep web/worker isolation and old-data playback as completion gates.

**Done:** meaningful validation passed or specific limitations recorded. Tests are not marked passed merely because their fixtures/Node were unavailable.

## P09 - Preview the sample and prepare the refresh runbook

1. Render local R1 and R5 from exactly the validated blobs/geometry used by tests. For changed extraction, locally re-condense or re-export originals first as required; `scripts/render_replay_standalone.py --blobs <folder> --out <local-page>` embeds them. Use existing control preview tooling for unchanged-extraction cases; never use stale stored control as proof of an engine fix.
2. Inspect R1 initialization and post-drop movement; R5 before/during/after the actual condition; one health damage/heal sequence; spike carrying while a gun is equipped, drop, pickup, cancelled plant, completed plant and terminal HUD. Seek backward and switch rounds. Inspect narrow/wide viewports, stacked players and zoom. Capture anonymized/local before/after evidence and console errors.
   Also preview the verified P07a molly sequence before activation, during blockage and after expiry, using both team unknown layers. Show the ownership-mirrored synthetic scene when the real sequence lacks a friendly comparison. Check backward seeking and ensure displayed utility activity matches computed blocking intervals.
3. Write a coverage table for BUG-01, BUG-02, REQUEST-03, REQUEST-04 and REQUEST-05 with implemented, checked, already-correct/stale or blocked-by-evidence disposition. Include proposed health default and any unresolved source coverage; update the checklist without declaring completion prematurely.
4. Prepare a concrete targeted refresh manifest: affected replays/rounds/maps, expected input revisions, source archive checks, re-export/re-condense requirements, compatible viewer/worker deployment order, control/gap recompute and rollback inputs. Retain prior local blobs/assets for comparison without committing raw exports.
5. Existing `reingest_replays.py` currently selects all stale candidates and has no demonstrated match filters. Before any future writes, add tested explicit match/source-manifest targeting if needed, with dry-run parity and refusal of out-of-scope entries. Keep full-replay completeness: round filters are for preview or control-only computation, not partial replacement through `store_replay`. Do not invent a nonexistent `--match` argument or run the broad command for this session.
6. Example supported read-only control dry-run after implementation:

   `.\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\compute_control.py --dry-run --match <verified-uuid> --round 1 --round 5`

   This does not re-condense inputs and is appropriate to a control-only sample check. The runbook must separately list whole-match targeted ingestion and any upload/archive-worker refresh path. If ingestion removed every control round, enumerate and restore all removed rounds for each authorized match, rather than stopping at this two-round command. Global control revision changes can mark other maps stale; list that wider staleness explicitly without silently refreshing the full corpus.
7. Return reviewable code/results and the runbook. Sample visual review and any later production refresh/deployment occur according to the user's authorization at that time; no demo writes or full-corpus refresh is part of this plan.

## Acceptance mapping

| Request | Tasks | Required evidence |
| --- | --- | --- |
| BUG-01 Abyss barriers | P01, P06, P08-P09 | Correct enemy-start containment for both sides; no initial forward pockets; correct later arrival; hashes/staleness verified |
| BUG-02 conditions/blind control | P01-P02, P04-P05, P07-P09 | Actual R5 condition established; visible chip; zero personal control including sole survivor; retained team utility and deterministic expiry |
| REQUEST-03 health | P01-P05, P08-P09 | Verified HP/max source; hidden before damage; percentage beneath circle/above name; healing, revive and seek behavior |
| REQUEST-04 spike | P01-P05, P08-P09 | Possession while gun equipped; precise or honestly unavailable ground marker; drop/pickup/cancel/plant transitions; one marker and unchanged HUD |
| REQUEST-05 damaging mollies | P01-P03 where required, P07a-P09 | Hostile active damaging zones block; friendly/non-damaging zones pass; preserved pre-existing uncertainty; correct expiry/arrival timing; counterfactual/cache parity |

No implementation or runtime validation is claimed by writing this plan. Remaining real-data evidence is intentionally acquired before locking extraction semantics.

## Amendments after the independent review (October 8, 2026)

These win over the text above. Source: [independent review](2026-10-06-replay-player-state-impl-review-independent.md), findings F1-F20.

- **Authorization and base.** Implementation is authorized. The base is `origin/main` `cf75c29` (CONDENSE 14 / CONTROL 8, the October 5 utility-review and height-slopes work merged). The bumps are CONDENSE 15 and CONTROL 9. P01 step 1 is done: the reported replay is Abyss `7a278f4b-ace8-49df-b176-9101c63c8808`. No database query, parser rebuild, recompute of stored rounds or re-ingest belongs to this build.
- **Health (P02 step 5, P03, acceptance row REQUEST-03).** The bar shows combined HP + shield: `100 * (hp + shield) / (max_hp + max_shield)`, the maxima measured for that life and round, never a fixed 150. `vitals` carries current and max shield as required fields. If shield values aren't proven, the bar shows the unavailable marker; HP is still extracted. (F12)
- **Revisions (P08 step 1).** CONDENSE 15 lands in P03's commit, with `test_replay_format.py`'s pin. CONTROL 9 lands in P07's commit, the first engine change, and P07a uses the same unreleased number. P08 checks the bumps and the freshness tests. (F1)
- **Exact flash and nearsight intervals (P02 steps 3-4, P07 step 6).** The engine keeps them as `[t, t+dur)`, unsnapped, drops zero-length hits, and adds their ends to `RoundInputs.transitions`, so they are analytic instants like ability walls. Checks: a zero-duration hit changes no tick; a hit at x.626 is not blind at x.625. (F2)
- **Frozen references (P07, P07a).** The `midwall` toy reference is expected to move: once for the blind rule and exact flash ends, and once for the molly rule. Each move is its own commit with an independent expected-result test and old/new digests recorded. Every other reference stays byte-identical. (F3)
- **Blinded control (P07 steps 2-4).** The own cell stays physical evidence in Unknown and the gaps observer. Removed while blinded: active/passive sight, memory, presence, backfill/fill sources, coverage, the Q63 sole-survivor credit and device credit. A blinded holder's trip, area and turret watch moves to a per-side independent source (like `extra_passive`), so `compose(removed=s)` changes nothing and the holder's coverage is zero. An operated camera or drone gives no sight while its owner is blinded. No PlayerView or gaps cache shape change: assert cache `FORMAT == 1`. Step 5's "distinguish uncredited independent utility in summaries" is dropped. (F10, F11, F16)
- **Molly traversal (P07a).** A per-side hazard list next to `blockers` (`hazards_at(side, t)`, reopen times), not the shared walls: spread with the zone's nodes taken out of `room`, then restore interior arrival times without letting them grow; reuse `free = max(free, reopened)` and topology's diagonal cut; a per-side seal for `unknown_without`; the side's hazards join the knowledge-view walls. First allow-list: the five sustained `DAMAGE_ZONES` entries (`Phoenix_MolotovFire`, `Sarge_Q_Molotov_Production`, `Pandemic_AcidMolotov_NewMolotov`, `Killjoy_4_BeeSwarm_Damage`, `Aggrobot_C_ExplodeyPatch`) with the existing radii, provisional. The classification lives in engine code; radii and timings in `app/control/utility.json` (hashed by `figures_hash`). Test a semantic change and a prose edit. No gaps cache serialization unless a field is really added. (F4, F8, F15, F20)
- **Viewer (P04, P05).** A new `playerConditionsAt` with `[start, end)` and no invented duration; `statusesAt` stays as it is, because device-down drawing uses it. P05 extends `drawStatuses` and moves it out of the abilities gate. P04 step 5 is a regression test of the existing `loadSeq` token. (F13, F14)
- **Parser (P03 step 5).** A needed parser patch is written as an unwired file with decoder tests, never into `replay_parser.json` (the worker rebuilds the parser from the pin on merge, and patches don't change the recipe). Rebuild and re-export are the owner's. Provenance enforcement is a design note. (F7)
- **Abyss geometry (P06).** The builder rewrites every Abyss asset and its index row, so the check is: sight, walk and bullet PNGs byte-identical; the Abyss index row differs only in `barrier_sha`; `git diff --stat` lists only `Abyss.barrier.png`, `tags.json`, `index.json` and tests. (F18)
- **Isolation.** `test_control_isolation.py` (stdlib-only `app/replays`) runs beside `test_replay_isolation.py` in P02, P03 and P08. (F9)
- **Suite (P08 step 4).** The full suite runs in the recorded parts with the base's known failures deselected, not one `pytest tests\replays` call. (F19)
- **Previews and refresh (P09).** A local `--blobs <dir>` mode for `preview_control_live.py` (tested on a toy) renders local re-condensed blobs with control; otherwise viewer-only and control previews are separate. The runbook states that merging CONDENSE 15 starts the live automatic re-parse of archived uploads (`REPLAY_REPARSE_AUTO`) and CONTROL 9 makes all control stale; it names `reingest_replays.py`'s missing match filter instead of adding one; any friends-DB dry-run is text for the owner. Visual acceptance comes back as review cards; the checklist file is not edited. (F5, F6, F17)
