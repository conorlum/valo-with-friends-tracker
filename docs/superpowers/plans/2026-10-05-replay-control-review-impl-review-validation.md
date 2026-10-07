# Validation of the external design/implementation review

Date: 2026-10-05. The initial validation below was review only. The implementation plan subsequently incorporated it and the follow-up validation at the end of this report. Application code and the agreed design rules remain unchanged.

## Verdict

The review is well supported. All eight numbered findings identify real omissions or ambiguous integration contracts in the implementation plan. They are predictions about the proposed implementation, not evidence that all eight faults already affect production. The agreed owner rules and the 28-item coverage remain intact. W01–W03 investigation can proceed; dependent implementation needs the corrections below first.

Verification used current code at `508153f`, the local parser, the map-tagger branch and raw Summit event scans. No pytest run, database write or replay recompute was performed. A small PowerShell numeric check reproduced the timestamp collision.

## Finding-by-finding assessment

| Finding | Verdict | Verified evidence | Required plan correction |
| --- | --- | --- | --- |
| 1. Flash coordinates/provenance | Accept, high priority | Summit actor 3714 spawns at (4787.3,8758.9,265.2); its same-time replicated sample is (47.87,87.59,2.65), and its detonation uses that smaller coordinate tuple. The flash enricher overwrites LastLocation from replicated movement. | W02/W06 must validate source units, sample time and coordinate provenance before calling detonation geometry precise. Test mixed sources. Re-export originals if the fix changes parser output; do not blindly multiply all positions by 100. |
| 2. Explanation events become locating events | Accept, high priority | observe.record forwards Unknown.events, and GapDetector.step treats each of them as locating history and as an event that selects the prior unknown route state. | Separate display-reason output from locating events or classify event effects explicitly. Unheard broadening and cleanup must not reset last-located time. Keep per-tick sight refresh; deduplicate only presentation. |
| 3. Exact scheduling versus serialized clock | Accept, high priority | RoundInputs.tick_times snaps to 16 Hz; encode._grid_units rounds again. Both 6.200 and 6.210 serialize to tick 99, displayed at 6.1875. | Define internal event ordering and output time representation together. Either evaluate exact transitions between grid frames and publish their resulting state no earlier than the frame after the event, or extend the stored clock/decoder consistently. Merely adding exact ticks is insufficient. Test engine-to-encoded-to-JS timing. |
| 4. Streaming ingress missing typed events | Accept, high priority | contract.UTIL_EVENT_TYPES excludes valorant_flash_exploded and valorant_flash_path_updated. Typed read_util reads the filtered events. | Name contract.py and update its exact filters/needles, or explicitly read these events through the existing raw streaming pass. Assert populated detonation/path output as well as streaming/in-memory equality. |
| 5. Incomplete temporary-geometry consumers | Accept, high priority, with qualification | Knowledge.tick_for uses separate possible_region reachability; GapDetector._sight casts with static geo/smokes. TickRecord/cache carry smokes but no general temporal feature state. The map-tagger consumer inventory names these consumers and RUNTIME_CONSUMERS is empty. | Wire knowledge reachability, gap sight, temporal traversal and replayable observer/cache geometry explicitly. Test aggregate, team knowledge, counterfactual and cached/live gaps for one wall transition. Existing smokes already carries Wall objects: reuse that representation where adequate, rather than assume every sight blocker needs a second snapshot. General bounded cover/movement changes still require equivalent state. |
| 6. Hypothesis cleanup | Accept | Unknown._drop_pieces exempts components containing actual enemy holders, not synthetic origins. A disconnected one-cell fake-beacon hypothesis otherwise qualifies for deletion. | Protect valid alternative source/provenance while applying ordinary visual clearing; remove its protection when the source is legitimately cleared. Do not disable sliver cleanup globally. Test creation and the next tick in a disconnected room. |
| 7. Sight mask is not the required tracer mask | Accept | Geometry explicitly excludes seeover tags from sight blocking; its tests show a seeover box is transparent to sight. Visible drops are unwalkable, so inverting the walk mask is also wrong. | Generate/serve a conservative bullet-obstacle footprint including verified low boxes separately from sight/walk masks. Test a seeover box and an open drop/void. Preserve the owner's all-walls/low-box rule. |
| 8. Judge new extraction against local blobs | Accept | control_cases.py fetches public round blobs and has no local source override. New wall extraction would therefore be tested against the old wall-free data. | Add local blob/context inputs before extraction-dependent cases run, or separate those from live regression cases. Assert the recovered event exists before spatial assertions. |

## Additional evidence and boundaries

**Deadlock:** the external review's stronger evidence is confirmed. Actor-spawn scans find 26 `GameObject_StealthingTrap_SoundSensor` objects, three `GameObject_SoundSensor_SweetSpotFissure` objects, one `Actor_FishingHook`, and associated FishingHook warning/cage objects, in addition to the known eight wall deployments. The earlier Cable-only scan missed these names. Add them explicitly to W02/W05. Counts are actor instances, not necessarily unique uses; ownership, replacement and lifetime correlation still need validation. Do not close sensor/ult issues as non-use.

**Skye:** the owner's rule remains settled: recorded enemy hit means cue played, regardless of duration. Completeness is a separate ingestion concern. Existing read_util counts unresolved hits and _util_entry can still produce an empty list. A destroyed-actor fallback is not by itself a proven flash activation. Specify how records establish a valid completed activation and attach missing/correlation diagnostics so an explicit zero-hit result is distinguishable from missing data. This must not reopen a sound-cue or target-facing test requirement.

**Fixtures:** control_toys.blob labels every player Jett; Veto tests must explicitly supply Veto and Evolution spans. Assert fixture eligibility/activation before interpreting the result. Node availability is useful but does not prove the tests run; retain the plan's skip checks.

**Freshness:** replay_gaps.hearing_hash currently hashes only footsteps/default gun/gun values. Adding ability hearing metadata requires an explicit consumed numeric view, cache/control/gaps fingerprint mutation tests and writer/reader agreement. Merely naming cache invalidation in W22 is not enough. Citation edits should remain non-semantic.

**Unproven signal fields:** teleport outcomes, wall sections and ability hearing values still need the planned evidence gates. Actor closure or a track discontinuity alone is not an outcome contract. No new owner-rule clarification is required to inspect them.

## Recommended revision order

1. Strengthen W02/W05/W06 with actor coverage, coordinate normalization/provenance, affirmative activation records and streaming ingress.
2. Make W09/W21 distinguish display reasons from gap locating history; preserve alternative origins through cleanup.
3. Specify the event clock and serialized representation with an end-to-end test before lifecycle/wall tasks.
4. Expand W14/W22 to the frozen geometry consumer inventory and replayable temporal state; add ability-metadata fingerprint mutation tests.
5. Replace W20's sight-only tracer mask with explicit bullet-obstacle geometry.
6. Add local-input judged cases before W22/W23 depend on re-condensed samples.

These are implementation-plan corrections, not changes to the agreed product behavior. The design only needs evidence notes updated for Deadlock and any newly chosen serialization/geometry contracts documented alongside it.

## Follow-up review validation and plan revision

The second review's two timing findings are accepted, and its parser-provenance finding is accepted conditionally on W06 requiring a parser patch. Static inspection confirmed each cited mechanism. No application tests, export/recompute or database operation was performed; this follow-up changed planning documents only.

| Finding | Verified evidence | Incorporated correction |
| --- | --- | --- |
| F1 — Subframe locating events must be processed, not merely timestamped | `GapDetector.step` judges victims before appending record events to locating history. `_judged` substitutes the previous record's entries for any enemy in that record's event set. Bundling an earlier locate into a later turn record therefore uses old history and uncertainty even with accurate event timestamps. | W09 explicitly feeds complete chronological analytical records to `step`, with immediate pre-locating entries for same-time R7 judgment. Earlier locates are processed before later turns, not bundled into viewer publication. W14 caches/replays that exact stream and W22 requires both locate-before-turn suppression and preserved simultaneous R7 behavior. |
| F2 — Clock resolution differs from sampling cadence; accounting needs its own schedule | `engine.TICK_STEP_S` is 0.5, while `RoundInputs.tick_times` adds event frames on a 1/16-second grid. `compute_round` currently shares that loop for viewer arrays, observer delivery, counterfactuals, space-taken credit and duration-weighted player/section totals. | Architecture/W09 distinguish exact internal transitions, analytical evaluation/accounting/observer records and sparse viewer frames. Preserve ordinary 0.5-second publication plus event/pre-death frames; integrate control/counterfactual totals over exact analytical subintervals and avoid duplicate transition/death credits. Test unchanged no-event arrays/summaries and an independently calculated between-frame blocker duration effect. |
| F3 — Current parser build is not provenance of an old export | `contract.check_manifest` checks the export's upstream commit and the supplied build's patch hash separately. `reingest_replays.py` supplies the current BUILD beside an existing export directory. `export_replay.ps1` checks the build commit but does not bind its patch identity to the generated export. | If patched, W06 verifies commit/patch identity before invoking export, writes export-bound source/content provenance on successful output and validates it during condensation across local/preview/worker entry points. Missing/mismatched legacy binding cannot be replaced with a current BUILD. Test refusal of an older export paired with a new same-commit patched build; W23/W24 enforce this before refresh. |

The plan retains 24 tasks and all 28 active checklist items (2–29). F1/F2 are W09 prerequisites; F3 is a conditional W06 ingestion gate. No new owner product-rule clarification is needed for these corrections. Document validation checks task/checklist inventories, review traceability and local document links; implementation tests remain scheduled work.
