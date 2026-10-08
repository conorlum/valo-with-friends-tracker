# Replay utility and map-control corrections — implementation plan

**Design:** [2026-10-05-replay-control-review-design.md](../specs/2026-10-05-replay-control-review-design.md).
**Goal:** implement and validate every active checklist item without losing the existing control/gaps rules.
**Status:** plan only; none of the implementation tasks below have run.
**Review incorporated:** [validated reviews, 2026-10-05](2026-10-05-replay-control-review-impl-review-validation.md). The initial eight findings, additional ingestion/cache evidence and follow-up findings F1–F3 are incorporated below. The agreed product rules and checklist items 2–29 are unchanged.

## Baseline and execution rules

Origin was fetched on 2026-10-05; `origin/main` is `508153f`, with CONTROL_REVISION 5 and CONDENSE_REVISION 12.
The earlier timing-gaps and trip/KJ/sliver fixes are merged. The live-claim and map-tagger branches remain ahead of main.

- Before W01, fetch again, inspect attached worktrees, and use a suitable isolated managed worktree from the updated `origin/main`; use a `codex/` branch. Do not implement on local main. Codex create_worktree is the available equivalent of the handoff's EnterWorktree request.
- Reconcile `afk/2026-10-04-live-claim` (revision 6 and approved state/memory cases), `afk/2026-10-04-map-tagger` (frozen map-feature geometry contract) and `codex/map-gimmicks`. Read their diffs; do not silently merge unrelated branches or invent a second feature schema. If prerequisites have merged, use the merged versions.
- Application code, parser work and data refreshes follow the agreed design. No Impact scoring changes, broad height build, demo database imports or service/domain changes belong here.
- Keep raw exports, credentials, player-identifying scratch reports and screenshot copies outside commits. Use small anonymized event fixtures for extraction tests. Public match UUIDs may identify the judged cases.
- Task commands below run from the implementation worktree's `webapp/` in PowerShell. Use its working Python environment; examples assume `.venv313`. If the worktree lacks an environment, invoke the existing main checkout's `.venv313/Scripts/python.exe` with the worktree as cwd. Verify Node exists so viewer tests do not all skip.
- Each task: add a meaningful failing test or reproduce the existing failure, make the smallest change, run its listed checks, inspect the diff, and commit only the task's files. Investigation-only tasks record evidence rather than invent a failing assertion. Suggested commit subjects are examples, not permission to skip checks.
- No production database writes are part of implementing this plan. Prepare the targeted refresh only after local samples are accepted.

## Architecture and contracts

### Stored utility inputs

Extend the existing `util` rows; do not create a separate source of utility ownership/time data.

- Retain existing `ability`, `flash`, `nearsight`, `reveal`, `status`, `shot` and `damage` compatibility.
- Add a stdlib-only helper module under `app/replays/` for verified ability metadata/normalization if sharing it is useful; do not import numpy/scipy/control geometry into the condenser or upload worker.
- Preserve stable actor identity for correlation, owner slot, round-relative times, actual detonation position, optional world-z decimetres, path/laid points and explicit activation/active/off/destroyed transitions. Type/class registrations do not count as activations. Normalize coordinates by their verified source units before projecting them; retain source kind, sample time and normalization provenance. Missing or stale position evidence cannot silently become a precise detonation.
- Introduce additive normalized knowledge/lifecycle rows only where existing rows cannot represent the event. Specify and test their shape in `format.py` before consumers are added. Proposed discriminator: `k='info'`, with `kind`, `t`, `by`, source identifier and event-specific payload; its exact payload is finalized from W02's real signals, not guessed. Viewer fallback ignores an unknown optional kind safely.
- A flash activation's recorded enemy target list determines Skye's cue. Retain zero-duration hits. Add an activation evidence contract, proposed as `activation: {state: "completed"|"unknown", evidence: <verified source>, targets_complete: true|false, diagnostics: [...]}`. Finalize source-specific evidence in W02/W06. A valid completed detonation/pulse plus proven target-record coverage, resolved ownership/targets and no relevant correlation/decoder gaps is required for a confirmed empty result. An empty list, actor-destroyed fallback or legacy absence alone proves neither activation nor completeness. If the source cannot prove coverage, keep `targets_complete=false` and prohibit negative inference. A positive recorded enemy hit needs no completeness proof to establish Skye's cue.
- Keep actual-world z distinct from minimap u/v and apply the height asset's origin exactly once. Missing z remains missing, never zero.
- Include typed detonation/path events in `contract.py` streaming filters and derived needles; test populated normalized output, not just equality of two empty outputs.
- If a parser patch is required, bind its commit/patch hash to each generated export at export time. A current `BUILD.json` supplied beside an older export is not export provenance. W06 defines the enforced binding and legacy-export policy before patched exports are accepted.

### Engine knowledge operations

Use a small module such as `app/control/utility.py` for parsed event/state helpers if extracting them keeps `engine.py` manageable; use current RoundInputs/TickRunner/Unknown as the integration points.

1. **Locate:** replace one enemy's region with an exact known location at the event time.
2. **Restrict:** intersect one enemy's region with geometric possible origins, retaining valid disconnected components and valid route entries.
3. **Exclude:** remove only a sensed footprint from susceptible enemies' regions.
4. **Add hypothesis:** union a new origin/region without losing the real-body region.
5. **Pause/resume source motion:** stop a specified body's propagation without deleting existing uncertainty or accumulating movement time to spend after resume.

Run operations per observing side and enemy at exact event timestamps internally. The stored clock has 1/16-second resolution; this is not a requirement to compute or publish sixteen frames per second. Define three schedules explicitly:

1. **Internal transitions:** retain the existing analytical sample instants and insert original utility, locating, movement-block, death/revive and immunity boundaries. Split elapsed propagation/clearing at those boundaries without snapping them. Preserve intermediate state even when several transitions fall between viewer frames.
2. **Analytical accounting and observer records:** evaluate full relevant control state, coverage, team knowledge and counterfactuals at each analytical boundary. Integrate each resulting state over its actual following subinterval, clipped to life/round/section bounds. Credit space taken once for each qualifying analytical state transition, and death loss once from the corresponding pre-death state. Feed complete chronological records to gaps at these instants, independently of viewer publication. Do not compute summaries solely from viewer-frame weights or repeatedly credit an internal transition when publishing it.
3. **Serialized viewer frames:** preserve the sparse schedule of `TICK_STEP_S=0.5` plus event frames and existing pre-death frames, on the 1/16-second grid. Add a frame at the first grid instant at or after a new exact event when needed; coalesce equal frame times. Sample state at the frame's own time. An event cannot affect an earlier frame, and exact internal records must not be rounded through `encode._grid_units` into viewer frames. No-new-event inputs retain the existing cadence and output. Any intentional legacy timing correction needs a separately explained reference change.

Define stable equal-time engine ordering: ending eligibility/lifetimes, starting eligibility, information effects, then cleanup, with source order as a tie-breaker within each phase. Explicit causal lifecycle precedence overrides default phases, such as Vyse trigger crossing before raise. Gap judgment uses the separate same-instant contract below. Record original timestamps in display reasons. Clear with current vision, then propagate only for elapsed permitted movement. Preserve the existing true-position source rule outside the explicit special cases.

Movement reachability, LOS and geometric cast/suppress radii are different operations. Leer candidates can lie across walls, though player candidates must still be walkable. Never force a disconnected geometric region to share one artificial central source. Keep RouteLog, entry, seen and located state synchronized with changed regions.

Keep locating history and display reasons separate. Preserve `Unknown.events`/`TickRecord.events` as the existing locating stream consumed by gaps, including continuous per-tick sight refresh. Add a separate display-reason collection; deduplicate only that presentation stream. Classify new effects explicitly: exact locate can update locating history, while broadening, hypothesis creation and cleanup cannot. Restrict/exclude update regions and route validity without automatically resetting last-located time. Valid alternative origins carry identity/provenance through cleanup: protect their source-containing component from size-only sliver removal until ordinary vision legitimately clears that source. Keep ordinary sliver cleanup elsewhere.

Gap processing must consume each complete analytical observer record chronologically through `GapDetector.step`, including players/view, unknown entries, relevant pre-locating entries, geometry and stable route-log references. Cache that same ordered stream, not a bundle of earlier events on the next viewer frame. For a locate at time t, `_judged` uses the enemy's state immediately before that locate, judges victims at t, then adds only t's locating events to history (existing R7). A later turn sees the already updated history/state. Earlier locates cannot put the enemy into the later record's `evented` set or cause `_judged` to substitute pre-locate uncertainty again. Complete same-time events form one record/transaction so source iteration cannot circumvent R7. Preserve observer failure isolation and immutable/cache-replayable state.

### Geometry

Reuse the frozen map-feature contract's movement blocks and bounded sight occluders when that dependency is available (`app/control/features.py`, `app/replays/map_feature_schema.py`, `map_feature_state.py`). Feed ability footprints/active states through its geometry interfaces; temporary agent walls must not be committed as permanent map paint. If a minimal adapter is needed on the current baseline, preserve that contract and provide parity tests against it before integrating.

Pass actual ability x/y/z to LOS. Use the existing explicit 2D fallback where a map has no height/cover data; do not begin a map-height rebuild just to unblock these corrections. Directional drone/dog vision remains directional. Movement blockers must affect topology, unknown and relevant counterfactuals without being treated as vision coverage.

Apply temporal feature state to every frozen-contract consumer, including `Knowledge.tick_for`/`possible_region`, special traversals and `GapDetector._sight/step/_judged`. Reachability must account for routes available during each elapsed interval, not only the wall mask at the interval's end. Analytical observer records and tick caches must reproduce the same exact feature/locating transitions as live computation independently of viewer frames. Reuse existing `smokes` Wall objects where sufficient for sight; carry compact versioned feature snapshots or stable references for remaining movement/bounded-cover state. Register runtime consumers only after their implementations and parity tests work.

Tracer collision uses a dedicated bullet-obstacle mask, not the sight mask or the inverse walk mask. Generate verified wall/low-box footprints separately; see-over boxes can stop bullets while remaining transparent to sight, and visible unwalkable drops do not automatically stop bullets.

## Task dependency map

| Group | Tasks | Prerequisites |
| --- | --- | --- |
| Baseline and evidence | W01–W03 | None |
| Playback | W04 | W01 |
| Extraction | W05–W08 | W02 |
| Shared engine operations, clock and immunity | W09–W10 | W03, W06–W08 |
| Detection, suppression and Leer | W11–W13 | W09–W10 |
| Movement topology and walls | W14–W15 | W05, W08–W09; map-feature reconciliation |
| Teleport/body lifecycle | W16–W18 | W07, W09–W10 |
| Utility visuals and tracers | W19–W20 | Relevant W05–W08 data; W14 geometry; W20 bullet assets before tracer validation |
| Explanations | W21 | W09, W11–W18 |
| Integration and release preparation | W22–W24 | All applicable tasks |

This is a dependency map, not an instruction to launch parallel agents. Work in one implementation chat unless the owner asks otherwise.

## W01 — Freeze the implementation baseline

**Files:** this plan; new local baseline report under `%TEMP%/control-review-2026-10-05/implementation/`.

1. Fetch, create/reuse the isolated worktree, record HEAD and environment paths.
2. Compare merged commits with the named overlap branches. Record whether live-claim and map-feature consumers are present and which revision is reserved.
3. Run the current relevant replay/control/gaps tests once to distinguish baseline failures from new ones. Check reference digests unchanged at this stage.
4. Inventory source exports/VRFs without printing secrets. Verify the export manifest/source SHA matches the reviewed replay before using it.

**Check:** `python -m pytest tests/replays/test_replay_extras.py tests/replays/test_replay_format.py tests/replays/test_control_reference.py tests/replays/test_control_unknown.py tests/replays/test_gaps_locating.py -q`.
**Done:** reproducible baseline and explicit branch/dependency disposition. No code fix in this task.

## W02 — Inventory raw utility signals

**Items:** 4–5, 9–10, 12–25, 27–29.
**Files:** new `scripts/inspect_replay_utility.py` only if a reusable streaming inspector is necessary; scratch evidence tables, minimal fixtures under `tests/fixtures/replays/utility/`.

1. Stream raw events, correlating spawned actor GUIDs, class paths, owners, target hits, detonation/effect/pulse times, close/destroy signals and moving paths.
2. Produce an ability-by-ability table: proven signal, current normalized output, dropped data, available z and missing evidence. Select named Omen/Phoenix/KAY/O flash examples. Audit coordinate source units, freshness and transforms: Summit Phoenix actor 3714 has spawn `(4787.3,8758.9,265.2)` versus replicated/detonation `(47.87,87.59,2.65)`. Trace `ValorantFlashEventEnricher.cs` before treating exported detonation coordinates as precise; do not multiply all coordinates by 100.
3. For Deadlock, confirm the eight wall projectiles/roots and 32 deployer nodes already found. Explicitly correlate `GameObject_StealthingTrap_SoundSensor` (26 spawned actors), `GameObject_SoundSensor_SweetSpotFissure` (three), `Actor_FishingHook` (one) and associated `FishingHook_BouncingTrajectoryWarning`, `FishingHook_CageSphere` and `FishingHook_EndOfTrajectoryWarning` actors. Counts are actor instances, not unique uses. Establish ownership, replacements and lifetimes; do not close sensor/ult issues as non-use from the earlier narrow scan.
4. Identify Phoenix Blaze points, Omen cancel/complete, Yoru beacon/fake/drift exit, Phoenix body lifecycle, Waylay recall, Veto active spans, knife pulses and both Cypher ult pulses.
5. Record hearing ranges from sources/measurements. Unknown values become specific owner in-game test tasks rather than arbitrary footstep-range substitutions.
6. Establish affirmative completion evidence and target coverage for each knife/flash activation. Inventory unresolved/orphan hits, correlation gaps, decoder loss and destroyed-actor fallbacks. Record which source can prove a complete empty result; otherwise gate negative inference off. Document parser fixes/pin changes that require re-exporting the original VRF before condensation.

**Check:** fixtures preserve actual actor/time/ownership and mixed coordinate-source shapes; diagnostics distinguish missing records from complete empty activations; the inspector bounds memory while reading the Summit multi-GB export.
**Done:** extraction tasks below use measured field names; missing source files are listed per ability. Continue independent tasks if a source is unavailable.

## W03 — Diagnose existing control failures

**Items:** 6–7, 15, 29.
**Files:** local diagnostic harness using `compute_round(observer=...)`; `tests/fixtures/control/unknown_cases.json`, `scripts/control_cases.py`; local-input harness tests.

1. Recompute Abyss R6 locally and log Unknown.events and region sizes per enemy at 20–22 s. Compare served revision/fingerprint and local rules.
2. Inspect the R6 16.4 s trip endpoints, active/suppressed state and diagonal/floor crossings against main's existing fix. Decide new defect versus stale output.
3. Inspect KAY/O alive/downed status and coverage across ult pulses. Do not assume suppression currently removes his vision merely because that was suspected.
4. Inspect Summit R12's surviving-enemy regions and Clove's locating events; keep new Leer behavior separate from existing kill/damage/gunfire rules.
5. Add exact cells/state/time judged cases after agreeing what each screenshot asserts. Record already-fixed cases as regression tests, not duplicate fixes.
6. Add an explicit local input mode before extraction-dependent cases: proposed CLI `control_cases.py --inputs <manifest.json>`. Map `(replay UUID, round)` to local blob/context paths with source SHA, condenser revision, clock/team context and computation recipe. Validate identity/context and assert required normalized events before spatial checks. Missing/mismatched inputs must fail, never silently fetch the public blob. Keep the existing live regression mode separate.

**Check:** current live judged-case runner; local mode performs no network request, rejects missing/mismatched inputs and absent required events; identical served/local inputs yield equal results. Toy harness proves each proposed assertion actually exercises the intended source.
**Done:** short cause report and explicit regressions for each diagnosed issue.

## W04 — Playback controls

**Items:** 2–3.
**Files:** `app/static/js/replay.js`; `tests/replays/test_replay_viewer.py`; use existing viewer DOM harness or a small browser test.

1. Add page-level Space handling for the active viewer, ignoring key repeats and editable fields. Prevent page scroll and double native button toggles.
2. At t_end, transition through playRound to the next listed round. Reset last-frame timing after load.
3. Add a transition/request token so manual round selection/pause supersedes an in-flight automatic load. Failed load and final round stop cleanly.
4. Preserve round-strip click-to-play, paused previous/next navigation and speed.

**Checks:** Node tests for final-round/next-round selection and stale promises; browser interactions from body, controls, tabs and editable fields; `python -m pytest tests/replays/test_replay_viewer.py -q`.
**Commit:** `replay: support page playback shortcuts and round continuation`.

## W05 — Recover Deadlock actors

**Items:** 24–25.
**Files:** `app/replays/extras.py:read_raw/normalize_archetype/build_extras`; `tests/replays/test_replay_extras.py`; W02 fixtures.

1. Recognize verified CableJam projectile/deployer shapes and CableJamRoot's nonstandard root shape. Canonicalize to Deadlock while retaining actor identity and ownership evidence.
2. Correlate wall root, arms and nodes; record active/destruction spans. Reject lookalike non-agent actors.
3. Handle the W02 SoundSensor/SweetSpotFissure and FishingHook actor families, correlating owners and lifecycle roles rather than counting warnings as casts. Fix upstream extraction if that is the missing layer, preserving the parser pin/patch workflow. Record unresolved actors explicitly rather than labelling the abilities unused.
4. Locally re-condense Summit; compare wall counts, ownership and lifetimes against raw evidence. Preserve the twelve GravNet throws.

**Check:** `python -m pytest tests/replays/test_replay_extras.py tests/replays/test_replay_util.py tests/replays/test_replay_condense.py -q`.
**Done:** eight proven walls survive import; sensors/ult have an evidence-backed disposition. No production re-import yet.
**Commit:** `replay: retain Deadlock wall actors and related utility`.

## W06 — Preserve information activations and precise positions

**Items:** 4, 9–10, 13–15, 19, 29.
**Files:** `app/replays/contract.py:UTIL_EVENT_TYPES/STREAM_EVENT_TYPES/keep_event/check_manifest`, `extras.py`, `condense.py:UtilCast/read_util/condense_export_dir`, `format.py`; optional stdlib utility metadata helper; existing extras/util/format/streaming/contract tests. If W02 requires a parser patch: upstream parser patch/pin, `scripts/build_replay_parser.ps1`, `export_replay.ps1`, `export_replay_preview.py`, `reingest_replays.py`, and production worker/export entry points found during the provenance audit.

1. Specify and test the additive activation evidence contract above using W02 signals; preserve source ID, owner, target lists, affirmative completion and target-coverage diagnostics. Keep proven zero-hit knife/flash activations even without status rows. Legacy empty rows, unresolved/orphan targets and `actor_destroyed_fallback` cannot assert a complete empty result; lack of source coverage leaves negative inference disabled.
2. Admit `valorant_flash_exploded` and `valorant_flash_path_updated` through streaming event types, derived needles and exact filters. Preserve detonation time/position only after source-specific coordinate normalization and freshness checks, correlate by actor, retain z and coordinate provenance. Fix the parser overwrite/source-unit defect when required, pin the patch and re-export a small original sample before re-condensing. Do not substitute cast origin or a generic fuse for known detonation, or apply a universal scale factor.
3. Preserve zero-duration Skye target hits. Derive any-enemy-hit from target teams; ignore friendly hits for the cue. No sound-cue decoding or facing simulation task.
4. Preserve both Cypher ult pulse times/targets, Haunt/recon pulses, revealing tags and suppressed spans; distinguish NULL/cmd from ZERO/point.
5. Add optional z to typed cast data or correlate to actor z where needed. Test round clock clipping and cross-round actor reuse; preserve exact lifecycle/status boundaries for W09 rather than pre-snapping them.
6. Conditional on a parser patch, make export-bound provenance enforceable. Before export, verify both pinned commit and patch hash of the build actually invoked; snapshot that identity. On successful export, write an additive manifest field or export-bound sidecar with commit, patch hash, source SHA and export-content hashes (events/movement and any other consumed outputs), then publish it atomically. At condensation, validate that binding against the pin and actual export files; a separately supplied current BUILD cannot replace missing/mismatched export evidence. Apply this path to local, preview and worker exporters/readers. Reject an old export paired with a newly patched build, including a same-upstream-commit patch change. Legacy exports lacking the binding remain readable as old stored blobs or explicitly unverified diagnostics; they require re-export before acceptance as patched production inputs. Never backfill a new patch identity onto an old export by copying the current BUILD.

**Check:** populated explosion/path output plus streaming/in-memory equality; mixed spawn/replicated coordinates and stale/missing samples; complete empty, legacy empty, unresolved/orphan hits, destroyed fallback and zero-duration positive hits. If patched, test old-export/new-build refusal, absent/mismatched binding, changed output hashes, successful newly bound export and legacy policy through condense/reingest/worker paths. Run utility/extras/format/condense/streaming/contract tests and affected exporter/worker tests.
**Commit:** `replay: preserve utility detection pulses and detonation geometry`.

## W07 — Extract teleport and body lifecycle events

**Items:** 12, 16–19, 21.
**Files:** `app/replays/extras.py`, `condense.py`, `format.py`; W02 fixtures and extras/lifecycle tests.

1. Normalize Omen cast/channel, destination manifestation, cancellation and completion separately.
2. Normalize Yoru beacon activation (including fake), real teleport and drift exit without conflating the beacon with the player.
3. Normalize Phoenix return marker, active ult body and return/end; Waylay anchor and completed recall; Veto Evolution active interval.
4. Resolve owners from recorded evidence. Keep missing evidence explicit; do not infer a successful teleport solely from a track jump or close.
5. Preserve event sound origin/height and timings needed by observer hearing logic.

**Check:** `python -m pytest tests/replays/test_replay_extras.py tests/replays/test_replay_lifecycle.py tests/replays/test_replay_condense.py -q`.
**Commit:** `replay: retain teleport and temporary-body transitions`.

## W08 — Extract walls and projectile motion

**Items:** 20, 22–24, 27–28.
**Files:** `app/replays/contract.py` event ingress; `extras.py:read_raw/wall_line/_control_inputs`; `condense.py`, `format.py`; extras/streaming tests.

1. Preserve exported projectile movement, parent/thrown correlation and true detonation/lifetime. Verify every consumed typed path/lifecycle event survives streaming ingress; assert non-empty paths against real-shaped fixtures in both readers. Apply W06 source-unit/freshness rules to path samples. Avoid emitting duplicate flights for a Projectile and its placed object's thrown record.
2. Extract Blaze's points/segments and active span if present; maintain curves and wall crossings. If absent, record the specific fallback decision needed.
3. Preserve Sage intact segment spans and Deadlock wall arm/node transitions from W05.
4. Preserve Vyse trigger versus raised versus lowered states. The trigger crossing cannot be collapsed into the entire raised interval.
5. Publish the documented optional row shapes, including missing-path behavior. Keep round blob growth measured on the sample, with bounded streaming memory.

**Check:** `python -m pytest tests/replays/test_replay_extras.py tests/replays/test_replay_streaming.py tests/replays/test_replay_format.py -q`.
**Commit:** `replay: preserve moving utility and wall state geometry`.

## W09 — Add per-enemy knowledge operations and causal event timing

**Items:** shared foundation for 4, 9–10, 12–14, 16–21, 29.
**Files:** `app/control/engine.py:RoundInputs/Unknown/TickRunner/compute_round`, `observe.py`, `encode.py`, `app/replays/control_format.py`; `app/gaps/detect.py:step/_judged` and observer/cache replay paths; new `app/control/utility.py` if useful; control utility/summary/reference/format and JS timing tests; gaps locating/spread/detector tests.

1. Implement the three-schedule contract above: exact internal transitions, analytical accounting/observer records, sparse viewer publication on the 1/16-second grid. Preserve original boundaries through `_span`/status parsing; split propagation at utility, locating, wall, death/revive and immunity transitions. Preserve `TICK_STEP_S=0.5` ordinary publication, adding/coalescing required event frames without generating 16 frames per second. Apply stable same-time engine phases and causal lifecycle precedence. Keep original timestamps for reasons and analytical records; serialize only viewer samples, never rounded internal records.
2. Implement locate, restrict, exclude, add-hypothesis and pause/resume operations against reached/seen/entry/located state. Keep team and enemy attribution explicit.
3. Restriction removes impossible nodes and routes without creating new impossible paths or resetting disconnected origins to a single centre. Track valid alternative source IDs/provenance and protect source-containing disconnected components from size-only `_drop_pieces` cleanup. Visual clearing removes protection and cannot resurrect a cleared source; ordinary sliver regressions must still pass.
4. Pause logic shifts movement-time baselines or uses a per-body movement clock so resume does not spend the paused duration in one large spread step. Vision can still clear stationary uncertainty.
5. Keep display reasons separate from `Unknown.events`/`TickRecord.events` locating history. Explicitly classify effects; only locating evidence updates gap locating time. Feed each complete exact-time analytical record to `GapDetector.step` immediately and persist that chronological stream for cache replay, independent of viewer publication. Update `step/_judged` to use immediate pre-locating entries only for enemies located at this record's actual time; judge before adding same-time locates (R7), with earlier locates already in history. Do not batch 6.200 evidence into a 6.250 turn record. Preserve continuous sight refresh, route state and observer failure boundaries. Version/test the observer contract with W14/W22. Display reasons never enter the detector's locating stream.
6. Separate `compute_round` analytical accumulation from viewer arrays. At every analytical boundary evaluate base control, coverage, relevant team knowledge and incremental/full counterfactuals, then integrate control/active/passive/alive/redundancy/section totals using analytical subinterval durations rather than `RoundControl.weights` alone. Compare adjacent analytical states for taken-space attribution, avoiding duplicate publication credits; capture death loss once from the actual pre-death state. Preserve legacy no-new-event results and measure extra work by analytical/event counts, not a universal eightfold sampling increase.

**Checks:** exact sighting, restriction across walls, one-enemy exclusion, alternative origins immediately/next tick, death/revive cleanup and pause without catch-up. Engine -> encoded blob -> JS must show 6.200/6.210 transitions no earlier than the next scheduled grid frame while preserving both internal effects. A blocker raised at 6.200 and lowered at 6.210 must contribute an independently expected `delta_control_m2 * 0.010 s` to duration accounting even if no viewer frame samples it; test counterfactual contribution, taken-space attribution and life/section clipping. No-new-event fixtures must retain sparse tick arrays, masks and summaries. Paired detector tests: locate at 6.200 then turn at 6.250 suppresses the inappropriate gap; simultaneous locate/turn retains R7. Run both live and cached paths, asserting correct outcomes rather than parity alone. Continuous sight/loss and non-locating broadening/cleanup retain timers. Run control utility/unknown/summary/reference/format/viewer and gaps detector/locating/spread/cache tests, with Node checks actually executed.
**Commit:** `control: apply utility knowledge per enemy at event time`.

## W10 — Centralize Veto utility immunity

**Item:** 19; dependencies of 4, 9–10, 13–14.
**Files:** engine/utility helpers; shared stdlib ability policy if needed by visuals; utility/sight tests.

1. Read active Evolution intervals from W07. Define separate capabilities for ordinary sight, drone cone sight, blind, suppress, reveal/tag and trigger.
2. Filter effects at their hit/pulse timestamp. Preserve ordinary and drone/dog visual sightings while rejecting forbidden tags, blinds and suppression.
3. Keep Fade/Wingman trigger rules, Gekko glob-without-blind behavior and Cypher trip trigger-without-concuss distinct.
4. Preserve Veto's region in empty knife/Skye/Haunt inference. A partial knife result remains partial when Veto is alive, even if all other enemies were suppressed.
5. Override `control_toys.blob`'s default Jett labels with Veto and explicit Evolution spans. Assert fixture agent identity and active eligibility before checking immunity results.

**Checks:** parameterized capability matrix before/during/after ult, both teams, cue-positive unaffected, and Cypher trigger evidence; `python -m pytest tests/replays/test_control_utility.py tests/replays/test_control_sight.py -q`.
**Commit:** `control: respect Evolution immunity by utility effect`.

## W11 — Reveal coverage and Cypher ult

**Items:** 4, 9, 13.
**Files:** `engine.py:_read_util/_add_watcher/Knowledge.seen_now/Unknown.apply`; utility helpers; control sight/floors/utility tests.

1. Compute Haunt/recon pulse footprints from actual source x/y/z, range and the shared map LOS adapter. Use available height/cover inputs and explicit fallback diagnostics.
2. Clear visible susceptible-enemy regions and apply exact recorded reveal targets; do not treat a whole circle behind cover as empty.
3. Add Tejo drone to the supported watcher paths; retain drone/dog possession, yaw, range and lifetime. Apply exact tags separately from cone sightings.
4. Apply each Cypher pulse as an exact location reset for eligible living enemies. Do not turn separated pulses into continuous omniscience.

**Checks:** hidden room, raised source seeing over low cover, separate floors/tunnel, source destroyed before pulse, two Cypher pulses with movement between them, Veto seen by drone but not tag; `python -m pytest tests/replays/test_control_utility.py tests/replays/test_control_sight.py tests/replays/test_control_floors.py tests/replays/test_control_knew.py -q`.
**Commit:** `control: clear unknown with utility sight and reveal pulses`.

## W12 — Knife and Skye information rules

**Items:** 10, 14–15.
**Files:** engine/utility helpers; utility tests and KAY/O lifecycle regression.

1. Evaluate verified knife hit count against living enemies at activation. Proven complete zero -> exclude radius for susceptible enemies; proven all -> restrict to radius; partial -> do nothing for every enemy. Missing/unresolved target coverage cannot be counted as zero or all.
2. Use Skye's recorded enemy target hits as cue=true independent of duration. Any hit -> unchanged; explicit complete empty activation -> exclude range/LOS footprint.
3. Use actual detonation geometry and through-wall suppress radius, with Veto exemptions; do not use target facing to determine Skye cue.
4. Leave living KAY/O's own vision intact during ult and suppression. Ensure NULL/cmd pulses cannot enter reveal/knife knowledge handling.

**Checks:** zero/some/all, deaths at pulse boundary, allies-only hits, zero blind duration, source cast far from pop, legacy empty rows, unresolved/orphan targets and destroyed fallback (no negative inference), verified complete empty activation, explicitly activated Veto, living versus downed KAY/O; `python -m pytest tests/replays/test_control_utility.py tests/replays/test_control_engine.py tests/replays/test_control_sight.py -q`.
**Commit:** `control: infer unknown from knife and Skye detection results`.

## W13 — Cypher suppression and observed Leer

**Items:** 5, 29; verify 6.
**Files:** `engine.py:Watcher/_watcher/_watching/Unknown`; utility helpers; replay.js status rendering; relevant tests.

1. Combine Cypher owner suppression intervals with device off state; pause trips and cameras for that duration. Resume immediately unless the device ended/died. Keep player vision intact.
2. Expose the same effective device state to the viewer so visible utility does not imply active control while suppressed.
3. When the opposing team observes Leer, restrict only Reyna's possible casting positions to the geometric placement disk through walls at the information time. Do not use an unobserved cast as team knowledge.
4. Use the sourced 10 m candidate range and record terrain/height treatment; if a required value cannot be confirmed, prepare the focused in-game measurement task. Preserve normal movement after the constraint.
5. Verify Clove kill updates in Summit R12 independently, then inspect the union of the two survivors. Do not retune generic kill radii merely to shrink this screenshot.

**Checks:** suppressed/reenabled/destroyed devices, simultaneous off intervals, unseen eye, across-wall candidate, other enemy unchanged, delayed observation and growth; `python -m pytest tests/replays/test_control_utility.py tests/replays/test_control_unknown.py tests/replays/test_gaps_locating.py tests/replays/test_replay_viewer.py -q`.
**Commit:** `control: pause Cypher devices and constrain observed Leer casts`.

## W14 — Integrate temporal geometry across control and gaps

**Items:** 6, 26–28.
**Files:** `app/control/topology.py`, `geometry.py`, `engine.py:Unknown/Knowledge.tick_for/possible_region/_area/unknown_without/compose`, `observe.py` and tick-cache readers/writers; `app/gaps/detect.py:GapDetector._sight/step/_judged`; reconciled `features.py`, `app/replays/map_feature_schema.py`/`map_feature_state.py`; frozen consumer inventory/registration and control ability-wall/gaps/cache tests.

1. Reuse reconciled feature geometry to derive active blocked movement edges/nodes. Keep blockers distinct from watched/live coverage and from sight occluders.
2. Apply the same temporal movement policy to normal spreading, locating movement fills, `Knowledge.tick_for`/`possible_region`, special links/traversals and relevant control counterfactual paths. Integrate elapsed reachability against each interval's state, retaining routes available before a later seal instead of using only the current blocked mask. Preserve floor binding and diagonal seal rules.
3. Block both teams at intact Sage and Deadlock sections; reopen only broken sections or ended walls. Deadlock mesh does not become an opaque wall.
4. For Vyse, allow the trigger crossing, then activate the blocker. Split scheduling at transitions; preserve unknown already across it and prevent coarse-tick tunneling during the raised interval.
5. Explicitly exclude Sonic Sensors from passage barriers. Use real trigger knowledge separately. Apply only a diagnosed additional Cypher fix from W03.
6. Wire gap sight through bounded temporal occluders and integrate W09's exact chronological records into `step/_judged`. Reuse `TickRecord.smokes` Wall representation where sufficient; add compact versioned feature state or stable replayable references for remaining bounded-cover/movement state. Cache immutable state, not mutable geometry references. Ensure cached and live gaps consume complete analytical records with identical transition times, players/view, pre/post locating entries, route references, floor scope and metadata. Viewer frames are not the cache's analytical clock.
7. Reconcile every consumer named by the frozen map-feature contract and its AST inventory. Add runtime registration only with working implementation and tests; an adapter/schema alone does not complete integration. Version/invalidate changed observer/cache shapes in W22.

**Checks:** partial destruction, expiry, diagonal corner, two floors, one-way special crossing, pre-trigger/post-trigger/raised/end, boundary standing and counterfactual removing a holder. One wall transition must agree across aggregate control, team knowledge, full/incremental counterfactuals and cached/live gaps; test a route available earlier but blocked at the final frame and a wall active entirely between viewer frames. Re-run locate-before-turn versus same-instant R7 with temporal occluders. Run ability-wall/unknown/barrier/floor/knowledge/summary, gap sight/detector/spread/cache and map-feature consumer inventory tests.
**Commit:** `control: block unknown at active ability walls`.

## W15 — Correct the Abyss box footprint

**Item:** 8.
**Files:** `app/static/data/control/tags.json`, regenerated Abyss walk/sight/index assets; judged case fixture.

1. Isolate the box from screenshot 4 at R7 13.5 s in the tagger. Confirm its walk and sight properties from existing map annotations/source or the owner if ambiguity remains.
2. Update movement footprint and sight footprint only as warranted; preserve nearby doorways/routes. Use bounded cover where the feature geometry supports the real low box.
3. Rebuild only Abyss using `python scripts/build_control_geometry.py --map Abyss`.
4. Compare walkable cells and known qualifying kill lines against the existing 2% blocked-line bar. Preview the reported path; do not add pixels solely to force a desired timing.

**Check:** `python -m pytest tests/replays/test_control_geometry.py tests/replays/test_control_assets.py -q`, judged case, local R7 preview.
**Commit:** `control: mark the reported Abyss box obstacle`.

## W16 — Omen ult channel and outcomes

**Item:** 12.
**Files:** engine/utility lifecycle helpers; new `tests/replays/test_control_teleports.py`.

1. Pause Omen's movement propagation through channel start/end; retain uncertainty and keep temporary-shadow vision counted.
2. At destination manifestation determine observed/heard/unheard using living opposing players and the ability's sourced hearing range.
3. Seen + completed -> exact sighting, zero unknown at that instant. Heard/seen + cancelled -> no new region; preserve previous region minus valid observation clearing.
4. Unheard destination -> add all unheard walkable spaces before outcome; retain this addition for BOTH cancellation and completion. Normal spread resumes after the channel, without paused-time catch-up.
5. Preserve the specified heard-only completion behavior and ordinary post-landing true-position source. A seen outcome takes precedence over unheard classification.

**Checks:** matrix of seen/heard/unheard × complete/cancel, dead listener, boundary distance, channel freeze, temporary vision, cancelled unseen addition retained; `python -m pytest tests/replays/test_control_teleports.py tests/replays/test_control_unknown.py tests/replays/test_control_sight.py -q`.
**Commit:** `control: model Omen ult uncertainty and stationary channel`.

## W17 — Yoru beacon and drift exit

**Items:** 16–17.
**Files:** engine/utility lifecycle helpers; teleport tests.

1. Keep actual-body spread. A heard activated/faked beacon adds a separate origin, not a replacement sighting.
2. Propagate the beacon hypothesis at ordinary unknown speed and clear it through ordinary vision, not just when the beacon actor closes. Preserve its W09 source identity/provenance through size-only sliver cleanup, including a disconnected one-cell room immediately at creation and on following ticks. Remove protection when vision legitimately clears the source; never re-add it on later ticks. Prevent stale GUID reuse from joining a later beacon.
3. Hearing Yoru teleporting adds no special reset. Heard drift exit adds no broadening; unheard exit adds the region outside all living opponent hearing ranges at exit time.

**Checks:** fake and real activation, disconnected one-cell beacon at creation/next ticks, ordinary sliver cleanup elsewhere, actor closes while hypothesis persists, visual clearing without resurrection, real-body region retained, unheard exit, dead listeners, movement barriers; `python -m pytest tests/replays/test_control_teleports.py tests/replays/test_control_unknown.py tests/replays/test_gaps_spread.py -q`.
**Commit:** `control: preserve Yoru beacon and drift hypotheses`.

## W18 — Phoenix return body and Waylay recall

**Items:** 18, 21.
**Files:** engine/utility lifecycle helpers; teleport/lifecycle tests.

1. Phoenix's return marker is not a second living moving source. Stop return-body spreading during Run It Back while preserving normal active-body sight/movement behavior.
2. Restore real-body source handling at return/end using explicit events. Apply only normal observable location knowledge; do not invent an additional global clear on an unseen return.
3. On a heard completed Waylay recall, remove her accumulated pre-return spread and restart at the matching return point. Unheard -> keep old uncertainty. Other enemies' regions are unchanged.
4. Test multiple anchors/ult casts, death, missing body samples and event boundaries; prevent old hypotheses reappearing through automatic holder reseeding.

**Check:** `python -m pytest tests/replays/test_control_teleports.py tests/replays/test_replay_lifecycle.py tests/replays/test_control_unknown.py -q`.
**Commit:** `control: handle Phoenix return bodies and Waylay recall`.

## W19 — Render projectiles, flashes, walls and Deadlock utility

**Items:** 20, 22–24; visual consistency for 5, 19, 27–28.
**Files:** `app/static/js/replay.js:ABILITY_STYLES/abilityStyle/pathAt/drawAbilities`, `_player.html`, replay styles, standalone renderer if needed; viewer/util tests.

1. Remove the blanket Projectile hidden result. Add Projectiles to the layer controls, on by default and persisted independently of Abilities.
2. Sample real paths at replay time; use team-colored moving marks with ability names/tooltips. Avoid duplicate projectile/thrown marks and long artificial trails. For absent motion data, show the recorded position without inventing travel.
3. Add explicit Paranoia travel shape, Phoenix/KAY/O flash flight and short detonation visuals. Preserve zero-duration hit information independently from drawing a blind status.
4. Draw Blaze from W08's laid line; pass through map walls. If recorded curve is missing, keep the agreed approximation visibly distinguishable and report its scope.
5. Add Deadlock GravNet area, wall arms/nodes, sensor and supported ult visuals. Draw Cypher disabled spans and partial wall destruction consistently with engine states. Keep Gekko's glob visible for Veto without a blind.

**Checks:** pure style/path/active-state tests plus headless screenshot of named utility before/during/after; toggle changes projectile pixels but preserves placed objects; `python -m pytest tests/replays/test_replay_viewer.py tests/replays/test_replay_util.py tests/replays/test_control_viewer.py -q`.
**Commit:** `replay: show ability projectiles and complete utility visuals`.

## W20 — Clip shot tracers at walls

**Item:** 11.
**Files:** `app/static/js/replay.js:drawTracers` and a pure ray helper; replay page/map options; `scripts/render_replay_standalone.py`; `scripts/build_control_geometry.py`, tag/feature obstacle metadata and generated `<Map>.bullet.png`/index hashes; geometry/assets/viewer tests. Change `app/replays/extras.py` only if W02 proves an old row lacks usable shot direction.

1. Use the existing recorded u/v -> u1/v1 vector as direction when valid, extending the ray to the map boundary instead of retaining its old 25 m length. Preserve an unclamped direction in extraction only where needed.
2. Generate a dedicated conservative bullet-obstacle mask from verified wall/low-box footprints and feature metadata, including physical see-over boxes. Do not use `<Map>.sight.png`, which omits low boxes, or invert the walk mask, which would block visible open drops. Audit tags to distinguish physical obstacles from glyphs, ordinary walkable surfaces and void/drop regions; unresolved obstacle classification needs explicit evidence rather than a blanket inversion. Include bullet assets in build/index hashes and validate available map coverage before declaring the all-walls/low-box rule complete.
3. Load `static/data/control/<Map>.bullet.png` once per viewer/map into an offscreen bitmap with a tested blocking-pixel convention. Add a pure first-obstacle ray helper, cache the mask and inline the same asset in standalone previews. Keep clipping in the viewer without engine imports in the upload/web worker; it must work when Map control is unchecked. Stop at verified static walls/low boxes. Use temporary footprints only for established bullet blockers; Deadlock mesh or a visual smoke/wall line is insufficient. Preserve the existing tracer while loading or on asset failure, and report missing-mask fallback in validation.
4. Keep old rows readable. No shot-to-damage matching, wallbang styling or learned low-box exceptions in this task.

**Checks:** see-over box stops tracer while remaining sight-transparent; visible unwalkable drop stays open; long corridor/map boundary, immediate opaque wall, shallow angle, starting on a boundary, missing bullet asset and standalone parity. Verify sight/walk assets and semantics unchanged by bullet generation. Run geometry/assets and relevant extras/viewer tests.
**Commit:** `replay: draw tracers to the first wall`.

## W21 — Store and display unknown-change reasons

**Item:** 7.
**Files:** `engine.py:RoundControl/compute_round`, `observe.py` locating/display contract, `app/control/encode.py:encode_data`, `app/replays/control_format.py`, `app/static/js/replay_control.js`, `replay.js`, `_player.html`; format/viewer/utility and gaps locating/cache tests.

1. Collect the separate W09 display-reason stream at analytical transitions before resets, including utility/lifecycle and cleanup reasons. Accumulate it for the optional viewer header without changing chronological gap record delivery. Attribute observing side, enemy slot and original event time. Leave `Unknown.events`/`TickRecord.events` locating history intact for gaps; do not feed the new reasons to `GapDetector.step`.
2. Deduplicate continuous sightings in presentation only; per-tick sightings must still refresh gap locating time. Retain simultaneous different-enemy causes. Add cleanup/death display reasons where regions actually disappear; broadening/hypothesis/cleanup do not become locating evidence. Avoid listing unchanged immunity every tick.
3. Add an optional compact header event list (e.g. `knowledge_events: {A:[[t,slot,kind],...],B:[...]}`) to existing control data. Store reason timestamps as round-relative seconds at original precision, independently of grid tick units; document/test the units. A seek to the exact event time cannot select a later frame or reveal future state. Test older blobs without the list and keep web-side decoding stdlib-only.
4. Display a small time-sorted reason list in Control with a seek action and side selection. Seeking backwards must restore the correct recent reasons, not accumulated future events.
5. Explain the diagnosed R6 collapse using W03's actual causes. Missing explanations in old data get an unavailable state, not a guessed reason.

**Checks:** Python-to-JS header/time parity, zero-event old blob, two causes at one time, no duplicate display noise, seeking and attribution. Continuous sight followed by loss, unheard Omen broadening/Yoru source creation and cleanup must preserve correct locating timers and cached/live gap parity. Run control format/viewer/isolation/utility and gaps locating/cache tests.
**Commit:** `replay: explain unknown changes in the Control panel`.

## W22 — Integrate revisions, fingerprints and regressions

**Files:** `app/replays/format.py`, `control_format.py`, `app/services/replay_gaps.py:hearing_hash/engine_key`, control input fingerprints, gaps freshness/cache/observer code; shared stdlib metadata semantic view; geometry asset index; reference/digest pins and relevant tests.

1. Choose one integrated CONDENSE_REVISION bump for changed utility extraction and one CONTROL_REVISION bump for the integrated engine release. If live-claim revision 6 is incorporated/reserved, use the next available revision, never overwrite that pin.
2. Define one consumed semantic metadata view shared by control/gaps freshness: include numerical ability hearing ranges, cast/detection ranges and consumed policy values, plus relevant geometry assets/feature-state contracts. The existing `hearing_hash` covers only footsteps/default gun/guns and must be extended deliberately. Include this view in control fingerprints, `engine_key`/gap keys and tick-cache guards with writer/reader agreement; keep condenser/web helpers stdlib-only. Source citations/prose do not affect semantic hashes. Test that changing a consumed number invalidates control and gaps, while editing a citation does not. Version/invalidate W14 observer/cache state changes explicitly. Bump GAPS_REVISION only if detector/output semantics require it; changed control inputs must still invalidate dependent gaps.
3. Check full versus incremental control counterfactuals at analytical boundaries on a tiny toy/real sample, particularly new walls, held-back uncertainty and removal of a watcher owner. Assert duration-weighted totals for between-frame changes independently; parity between two wrong paths is insufficient. Ensure route history contains no removed/teleported obsolete path and no duplicate taken-space/death credits from publication.
4. Run reference tests. Preserve no-new-event sparse tick arrays, viewer masks and analytical summaries; record sample/event counts and runtime. Classify deliberate legacy timing corrections separately before updating any frozen digest. Do not regenerate references merely to make red tests green.
5. Check old missing optional keys, round/cue completeness and process isolation. Update the final checklist disposition for diagnosed-already-fixed cases.
6. Run existing public-blob regression cases separately from new extraction-dependent cases using W03's `--inputs <manifest.json>` mode. Require normalized recovered actors/pulses/paths in those local blobs before judging control; record the same sample manifest for W23. Verify timing end to end and all frozen geometry consumers, not just main Unknown spreading.
7. Gate chronological observer/cache integration on both correct locate-before-turn and preserved simultaneous R7 behavior. If W06 patches the parser, require export-bound provenance tests on every ingestion path before treating the new condenser revision as ready; current-build checks alone do not satisfy this gate.

**Check:** `python -m pytest tests/replays -q`; existing live cases via `python scripts/control_cases.py`; new extraction cases via `python scripts/control_cases.py --inputs <manifest.json>`. Report Node execution/skip counts, source manifests and semantic hash mutation results. If baseline DB-dependent checks cannot run, identify them and run the available meaningful subset without calling the full suite passed.
**Commit:** `control: version utility knowledge inputs and preserve regressions`.

## W23 — Preview a small sample for the owner

**Files:** preview scripts only if they need to accept locally re-condensed data; all preview artifacts under `%TEMP%`, never application data writes.

1. Preview one or two rounds per batch: Abyss R6–7; Abyss R11/R21; Waylay R3 plus the selected flash example; Summit R11–12. Do not compute all rounds to review one change.
2. For unchanged extraction, use `python scripts/preview_control_live.py <uuid> <round> [<round>] --tag utility-review-<batch>`.
3. For changed extraction, use the matching original export, or re-export the original VRF first if W02/W06 required a parser fix. Record original/archive SHA and validate W06's export-bound commit/patch/content identity, not just the currently installed BUILD; then locally re-condense, select only sample blobs, run compute_task and inline the result with `render_replay_standalone.py --blobs`. Use those exact blobs/context in W03/W22's local manifest and assert required events before previewing. Current live blobs cannot demonstrate recovered actors or repaired coordinates.
4. Open standalone previews, verify no console errors, and capture before/after utility and unknown states at the reported timestamps. Include both aggregate and team knowledge views.
5. Report fallback LOS areas, unresolved source signals, output sizes and any runtime regression. Obtain the owner's visual acceptance before proposing a broad refresh.

**Done:** judged sample outcomes accepted or a concrete follow-up defect recorded. No full-corpus recompute.

## W24 — Prepare the targeted refresh and closeout

**Files:** release notes/runbook and checklist status; scripts only if a missing targeting capability is required.

1. Produce a per-item result table: implemented and checked, already fixed but stale, confirmed non-use, or still blocked by specific missing evidence. No silent completion for an unobserved ability.
2. List affected replay sources/maps and refresh order: deploy compatible readers/writers and generated bullet assets; re-export originals with the pinned parser if export semantics changed; validate export-bound patch/content provenance; re-condense where extraction changed; recompute control and dependent gaps with the new semantic metadata/geometry/analytical-cache versions. Verify required events before computation. Viewer-only playback/visual fixes do not need engine recompute.
3. Prepare a guarded dry-run through the established friends-DB wrapper, with `--match`, `--map` and `--round` filters. Example read-only planning command:

   `python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/compute_control.py --dry-run --match <uuid> --round <n>`

4. Verify archive SHA and use the established source-specific re-ingest/re-upload route. Do not claim a control recompute recovers omitted utility. Upload-origin refresh may need the worker/archive path instead of the local-only reingest script.
5. After sample acceptance, ask for any still-unprovided refresh authorization with the concrete scope, dry-run counts and expected work. The owner's earlier full-corpus preference remains a separate decision. No demo DB writes.

**Done:** reviewable code changes, completed validation evidence, precise remaining limitations and a ready targeted refresh runbook. Merging/deploying or performing refreshes is subsequent work according to the owner's authorization.

## Checklist coverage and completion evidence

| Item | Tasks | Required evidence |
| --- | --- | --- |
| 2 | W04 | Space outside map toggles once |
| 3 | W04 | Auto-next, final stop, manual-action race |
| 4 | W06, W10–W11, W23 | Abyss Haunt footprint/pulse and LOS |
| 5 | W06, W13, W19 | Device off/on matches suppression and drawing |
| 6 | W03, W14 | Existing/new trip seal verified at reported corner |
| 7 | W03, W21 | Actual collapse cause plus seeking explanation |
| 8 | W15 | Correct box path without unwanted LOS change |
| 9 | W06, W10–W11 | Two exact pulses, immune Veto preserved |
| 10 | W06, W10, W12 | Zero/all/partial knife matrix |
| 11 | W20 | Dedicated bullet mask; low box stops, open drop passes; first-wall endpoint beyond old 25 m |
| 12 | W07, W09, W16 | Omen observation/outcome/freeze matrix |
| 13 | W06, W10–W11 | Source-height LOS, drone cones and tags |
| 14 | W06, W10, W12 | Hit cue independent of duration; proven complete empty LOS; incomplete data preserves unknown |
| 15 | W03, W12 | Living KAY/O vision; ult pulse gives no reveal |
| 16 | W07, W09, W17 | Real body and heard beacon coexist through sliver cleanup until visual clearing |
| 17 | W07, W17 | Heard/unheard drift exit |
| 18 | W07, W18 | Return marker generates no moving unknown |
| 19 | W07, W10, W19 | Capability matrix including glob/trip exceptions |
| 20 | W02, W08, W19 | Blaze line crosses walls; curve/fallback stated |
| 21 | W07, W18 | Heard recall restart versus unheard preservation |
| 22 | W02, W08, W19 | Three named abilities visible |
| 23 | W08, W19 | All projectiles default-on, independent toggle |
| 24 | W05, W19 | Deadlock recognizable utility visuals |
| 25 | W02, W05 | Proven walls plus SoundSensor/FishingHook ownership/lifetimes; no unsupported non-use verdict |
| 26 | W14 | Sensor permits silent unknown passage |
| 27 | W05, W08, W14 | Intact/partial/broken walls across aggregate/knowledge/counterfactual and cached/live gaps |
| 28 | W08, W09, W14 | Exact Vyse trigger crossing, raised block and end; no early serialized state |
| 29 | W03, W06, W13 | Through-wall Leer candidates plus Clove events |

## Explicit remaining evidence and owner-test tasks

- Locate missing raw exports/VRFs for the reviewed Abyss and Waylay matches if archive retrieval cannot supply them. This blocks only their extraction evidence, not all development.
- Verify ability-specific hearing radii for Omen, Yoru and Waylay. If no reliable value exists, request a small in-game distance test; leave the numeric decision recorded as provisional until measured.
- Leer placement uses the sourced 10 m candidate. If current terrain/height behavior cannot be confirmed from export/source, request the owner's in-game placement test. Do not confuse this with blind range.
- Phoenix wall point/curve availability is an export investigation. If absent, present the concrete straight-line fallback and its limitations before using it for authoritative control.
- The Abyss box's movement/sight footprint may need the owner to identify its real properties after isolating it in the tagger.
- No extra Skye sound-cue test is requested: the owner settled recorded enemy target hits as sufficient.

## Review correction traceability

| Validated finding | Tasks and completion gate |
| --- | --- |
| R1 — Coordinate units/provenance | W02/W06 mixed-source and freshness fixtures; W23/W24 parser re-export before re-condense when needed |
| R2 — Display versus locating events | W09/W21 separate streams; continuous sight timing and non-locating broadening/cleanup; W22 cached/live gaps |
| R3 — Internal and serialized clock | W09 separates exact transitions, analytical records/accounting and sparse frames on the 1/16-second grid; W14 temporal crossing; W21 reason times; engine-to-blob-to-JS tests |
| R4 — Streaming ingress | W06/W08 contract filters/needles plus populated detonation/path assertions and reader parity |
| R5 — Every temporal geometry consumer | W14 frozen inventory, knowledge reachability, special traversal, gap sight and replayable cache state; W22 parity/version guards |
| R6 — Hypothesis survives cleanup | W09/W17 source provenance, disconnected one-cell creation/next-tick tests, visual clearing without resurrection |
| R7 — Bullet obstacles distinct from sight | W20 generated bullet mask and asset hashes; see-over box/open-drop/standalone tests |
| R8 — Judge new extraction locally | W03 explicit validated local manifest; W22/W23 same sample blobs, required-event assertions and no public fallback |
| Additional evidence | W02/W05 Deadlock actor families; W06/W12 activation completeness; W10 explicit Veto fixture eligibility; W22 consumed numeric metadata hash mutation checks |
| F1 — Subframe gap judgment | W09/W14 exact complete chronological observer/cache stream; explicit `step/_judged` ordering; W22 locate-before-turn and simultaneous R7 outcome gates |
| F2 — Cadence and analytical accounting | W09 preserves sparse publication; full analytical subinterval control/counterfactual/summary accounting; W22 unchanged no-event output plus independent between-frame duration expectation |
| F3 — Conditional export-bound parser provenance | W06 export-time commit/patch/source/content binding and ingestion enforcement if patched; W22 old-export/new-build refusal; W23/W24 validated refresh inputs |

Plan validation is document-only. Implementation evidence, pytest/Node results, parser sample re-exports and database dry-run results must be recorded when their tasks run; this revision does not claim those checks passed.
