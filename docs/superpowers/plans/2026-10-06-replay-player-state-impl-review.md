# Implementation plan review - replay starting barriers and player state

Date: October 6, 2026. Method: self-review against the revised design and current implementation paths. No separate reviewer agent was used. No application tests or production reads/writes were run for this document review.

Reviewed: [implementation plan](2026-10-06-replay-player-state-impl.md). Design: [specification](../specs/2026-10-06-replay-player-state-design.md).

## Findings and incorporated corrections

| ID | Finding and evidence | Correction |
| --- | --- | --- |
| I1 - extraction refresh operates at whole-replay granularity | `app/replays/store.py:_delete` removes the Replay, all ReplayRounds, players and control rows before replacing a changed recipe. `reingest_replays.py` passes a complete condensed replay. A R1/R5-only extraction write would destroy other rounds or violate completeness. | P03/P08/P09 now distinguish sample-only previews from complete whole-match ingestion. Target re-ingest by match/source manifest; compute every round whose stored control was removed. List counts/costs before future writes. |
| I2 - final-survivor rule and attribution need independent oracles | `compute_round` Q63 credits all owned cells to the last holder, and coverage/counterfactual loops can continue even after body masks are blank. Parity of two paths could preserve the same bug. | P07 requires explicit expected zero masks/areas/durations for lone and all-blinded cases, retained alive seconds, preserved autonomous utility and body knowledge, and independent accounting assertions. |
| I3 - observer/cache tuple shape is a separate compatibility surface | `app/gaps/cache.py` has FORMAT=1 and positional PlayerView tuples. Adding eligibility only to live observer records would diverge from cached gap runs. | P07/P08 require paired writer/reader updates, a version decision and an old-cache refusal/miss fixture if the tuple changes, plus equivalent cached/live results. |
| I4 - shader screenshot is not yet proof of bad initialization | The first screenshot omits playback time. Excess ground could be valid later propagation, although the user reports starting barriers. | P01/P06 now explicitly verify time or compare start versus later frames; do not repaint a correct barrier to match an unknown-time screenshot. |
| I5 - planted spike regression gate must test abilities hidden | Existing planted glyph lives inside `drawAbilities`, while this design puts carrier/drop markers outside it. Testing only default layers misses a plant-time disappearance or duplicate. | P05/P09 now require one lifecycle marker across plant completion with the abilities layer both enabled and disabled, retaining HUD results. |
| I6 - schema/provenance and refresh cannot be inferred from current BUILD | Current contract checks parser prefix/build metadata but does not bind an old export's new-data completeness to patches/content. New parser records, if needed, require export-bound checks on every path. | P03 conditional provenance gate retained; P08 requires a populated sample before judging extraction and recipe invalidation. No claim of completed parser support. |
| I7 - new request assumptions should be visible to the user | HP versus HP/shields, heal/revive bar persistence and independent-device attribution were not all explicitly specified in chat. | Design identifies defaults; implementation begins with assumption/overlap review and may revise them from user input. Final delivery states HP-only is proposed, not agreed. |

## Coverage and sequencing audit

All five requests have evidence, implementation, regression and visual acceptance tasks. The task order is executable without writing production data. Source investigation precedes final schema/decoder choices; deterministic state selectors precede layout; eligibility, geometry and molly traversal integrate before freshness/preview.

Checks cover source filtering, full/streaming and local/worker/standalone parity; status boundary/seek/life behavior; actual Abyss start reachability; personal control beyond raw vision; old optional payloads/cache formats; and spike possession rather than active weapon. Runtime execution and source coverage remain future work, not review results.

Review outcome: identified plan defects are incorporated. No unresolved document defect prevents using the plan as the implementation guide. Open evidence/default decisions remain explicitly gated in P01/P02. Refresh scope must be finalized from actual ingestion semantics and sample results before a later authorized refresh.

## Added damaging-molly rule - review addendum

P07a and its dependencies were reviewed against the code and design section 5. No runtime tests were performed.

| ID | Finding | Incorporated gate |
| --- | --- | --- |
| I8 - broad damage classification would over-block | Instant explosions/strikes and inactive ground objects are not active damaging mollies. | P01/P02 verify classification and damaging intervals; P07a tests inactive, non-damaging, no-hit active and blast cases explicitly. |
| I9 - ownership direction must be tested independently | Comparing a molly to the observer instead of the uncertain player's team reverses friendly/hostile. | Mirrored A/B and round-side-swap fixtures use independent expected blocked cells. |
| I10 - old arrival times can cross during expiry | Retained unknown and coarse tick timing can give a reopened route movement credit from before the damaging interval. | P07a requires exact activation/expiry, overlap/re-entry and independent earliest-arrival assertions between published frames. |
| I11 - counterfactual and cache state are separate paths | Updating only Unknown.apply leaves `_flood` or cached gap reachability unrestricted. | P07a inventories every movement consumer; P08 includes semantic hashes/cache schema and paired writer/reader tests. |
| I12 - movement restrictions must not become reveals | Removing interior/far-side uncertainty or clipping geometric information hypotheses would assert absence the molly does not prove. | Preserve occupancy, actual-position evidence and information-only inference; prevent only traversal through the damaging footprint. |

Addendum outcome: the fifth request is covered through evidence, implementation, integration, local preview and targeted refresh. Remaining source coverage is investigated rather than presumed.
