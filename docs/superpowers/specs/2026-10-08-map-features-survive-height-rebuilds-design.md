# Map features survive automatic height rebuilds

Date: 2026-10-08. Status: proposed design for owner review; no implementation or release approval is implied.

## 1. Brief, evidence, and boundaries

Owner requirements: Summit's drop-doors, and later other gimmicks, remain authored once in minimap coordinates. Heights still build on the replay worker at 2 matches and then every 5 new matches, with `REPLAY_HEIGHTS_AUTO` on from the height-rebuild merge. A rebuild never edits `tags.json`. There is no floor tagging: exactly one floor selects itself; a multi-floor or unplaceable gimmick is pending, blocks nothing, is reported, and is never deleted. Each round identifies its compiled inputs. The existing E6 guard remains until the replacement ships. These are settled requirements, not open questions.

The owner's clarification during this design: gimmicks and reusable compiled versions belong **per map**, not duplicated per round. The recommendation is a shared database artifact store, with a digest reference in each round.

Evidence was read with `git show` and `git grep` on `afk/2026-10-07-height-slopes-rebuild` at `5387aeb7661f002c759e74415ae0d9bf220cb92d` (**R** below). Original repository `path:line` references are at R, including the frozen contract. Review corrections below explicitly cite **B**, the refreshed locally available `origin/main` at `cf75c29ee03578cc31af4241af8139946ad9ac30`. The spec branch starts at fetched `origin/main`, `d174443ec18e887e60b9e44f7d5b5f8a09497840`. The height implementation was unmerged when this spec was written. B now contains it; its tree matches the locally available height-branch tip `9713e27`. The document worktree still has the original base; implementation must start from integrated B or a newer verified main. Neither protected AFK branch is checked out or modified. Preserve the emitted-floor separation fix, `HEIGHT_RULES_REVISION=2`, failed-rebuild retirement and worker scheduling fixes in B (`c06eb02`, `c5ddb72`); this feature work does not change height rules.

**Verified** describes the inspected tree. **Proposed** sections specify future implementation requirements, not existing facilities. Estimates and unknown facts are marked `[unverified]`. No database or benchmark was run.

Verified facts:

- The contract is frozen and owner-approved; no committed map has enabled features (`docs/superpowers/specs/2026-10-04-map-features-contract.md:3-15`). `RUNTIME_CONSUMERS` is empty (`webapp/app/control/features.py:444`).
- Heights have database bytes and an active digest; generations are files selected by `index.json.features_sha` (`webapp/app/models/replay.py:106-131`; `webapp/app/control/features.py:934-1008`).
- A feature manifest includes `height: geo.height_sha` and compiled-asset hashes; missing feature floor IDs on a height map are unresolved (`webapp/app/control/features.py:173-186`, `845-896`).
- Geometry caches against a generation pointer and verifies against current tags (`webapp/app/control/task.py:105-189`). Planning reads the pointer, not a compilation against database heights (`webapp/app/services/replay_control.py:128-156`).
- Planning skips generation-bearing maps; storing, activating and turning heights off also guard them (`webapp/app/services/replay_heights_remote.py:155-185`, `232-275`; `webapp/app/services/control_heights.py:58-71`, `226-288`).
- Heavy geometry imports stay out of the web app and worker server parents (`webapp/app/control/geometry.py:37-39`; `replay_worker/control_job.py:12-15`). The web app already delegates height verification to a child (`webapp/app/services/control_heights.py:120-131`).

## 2. Architectural choice and storage (proposed)

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Shared immutable per-map database artifacts; small round references | Needs a future migration; survives worker eviction and shares the durable home of heights | Recommended, following owner clarification |
| Full feature snapshot and compilation in every round summary | Uses an existing column but duplicates map data for every round | Rejected after owner clarification |
| Worker-only cache | Cheap initially; loses historical verification after tags change and cache eviction | Rejected |

Definitions remain authoritative in committed `webapp/app/static/data/control/tags.json`, under each map's `map_features`. A database snapshot is past-compilation evidence, never a second editable catalogue. There is no database feature editor or active feature-generation pointer.

Implementation clarification: retain exact height NPZ bytes and their SHA-256 as optional archive/audit columns on the feature artifact. This also covers a committed-height fallback that has no `control_heights` row, without creating activation history or changing explicit off/fallback selection. These representation bytes are outside the runtime manifest identity; the isolated verifier checks their decoded height digest against the artifact key. Database-built historical heights remain available through the existing table as a fallback.

Propose `control_feature_artifacts`, owned by future `app/services/control_feature_artifacts.py`:

| Field | Meaning |
| --- | --- |
| `digest` | Full SHA-256 of the canonical consumed-input manifest; primary content address |
| `map_name`, `height_digest` | Map and exact height asset; explicit `flat` cache identity for no asset |
| `tags_digest`, `compiler_version` | Context-qualified tags digest (section 4) and compiler semantics version |
| `manifest` | Versions, inputs, intended/active bundles, pending outcomes, compiled-part hashes |
| `inputs` | Immutable normalized definitions and context: reducer/consumer identities, legacy reconciliation inputs, base geometry bytes needed to recompile |
| `assets` | Compressed canonical compiled data: complete masks/bounds, node bindings, arcs, base-edit deltas |
| `created_at`, `code_commit` | Audit metadata excluded from runtime hashing |

Enforce uniqueness on `(map_name, height_digest, tags_digest, compiler_version)`. Recompiling that key must produce the same digest and expanded canonical input/compiled bytes; a mismatch is a compiler/integrity failure, never an overwrite. Compression is a transport/storage representation: distinct valid gzip streams of identical canonical content do not create a different artifact or a same-key conflict. Verify actual expanded masks and numeric bytes; do not hash a zlib-specific compression stream as runtime identity. Retain historical artifacts; no pruning job in this release. Existing heights retain old bytes and are retrievable by map/digest (`webapp/app/services/control_heights.py:114-117`; `docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md:72-76`).

Archive bit-packed permanent sight/walk/barrier masks with dimensions and bit order, specials, scale and relevant legacy reconciliation masks in `inputs`. Hashes alone cannot reconstruct masks after base-asset edits. Reference durable height bytes by map/digest instead of duplicating them. Record the compiler code commit; definition-to-output recompilation uses that recorded version/code. A current compiler refuses unsupported historical versions rather than claiming it reproduced them.

Each successful **feature-bearing** round stores additive, internally versioned `input_provenance` in its existing compressed summary: actual geometry inputs including `features: artifact_digest`, control/data/summary revisions, recipe, source hash, link snapshot, consumed figures hash and round fingerprint. It contains no feature shapes or compiled arrays. `geometry_used` returns the same artifact digest. A round without a runtime feature input has no new member or `features` key.

Verified extension points: summaries are gzip JSON dictionaries (`webapp/app/replays/control_format.py:250-259`); `encode_summary` constructs them (`webapp/app/control/encode.py:158-174`); the writer stores summary bytes/fingerprint but discards returned geometry (`webapp/app/services/replay_control_store.py:31-55`). Durable actual-input provenance therefore needs an explicit encoder/writer change. No new round column is needed; **a future artifact-table migration is proposed**. This task executes no schema change.

## 3. Data flow and module ownership (proposed)

| Step | Owner | Input -> output |
| --- | --- | --- |
| Author/export | `scripts/control_tagger.py`, `control_tagger_features.js`, `TaggerCore.Features` in `control_tagger_core.js` | Shapes/behaviour -> whole-map `map_features` export; remove floor prompts only |
| Source validation/normalization | `app/replays/map_feature_schema.py` | Version-1 source -> deterministic runtime projection; deprecated floor selectors excluded, unknown/source data preserved |
| Identify inputs | Proposed stdlib `app/replays/map_feature_inputs.py` | Consistent tags/base snapshot + exact height -> envelope and four-part key |
| Resolve artifact/miss | Proposed `app/services/control_feature_artifacts.py` | Exact key -> verified database artifact; isolated child compiles misses |
| Compile | `app/control/features.py` | Permanent geometry + named heights + normalized source -> one-floor bindings, statuses, complete output, manifest |
| Rebuild diagnostics | `app/control/height_job.py`, via `replay_worker/height_job.py` | Newly built heights + explicit diagnostic tags -> report for all annotations, including disabled ones |
| Plan round | `app/services/replay_control.py` | Current height + intended feature inputs -> exact artifact digest in `geometry_inputs` |
| Dispatch/cache transport | `app/services/replay_control_remote.py`, `replay_worker/server.py` | Task pins height/artifact digests; shared bytes pushed on cache miss |
| Load/check/compute | `app/control/task.py`, via `replay_worker/control_job.py` or local `compute_control.py` | Exact inputs -> verified loaded artifact, actual `geometry_used`, result/provenance |
| Store/freshness | `replay_control_remote.py`, `replay_control_store.py`, `control_format.py` | Planned/used/current input agreement -> atomic round result and small artifact reference |

Verified tagger workflow downloads the export, then the owner copies it to `tags.json`; the browser does not directly write the repo (`webapp/scripts/control_tagger.py:18`, `25-37`; `webapp/scripts/control_tagger_core.js:1129-1147`; `webapp/scripts/control_tagger_features.js:1144-1145`). Keep that workflow. A feature-only edit does not require rebuilding permanent masks to publish a generation; mixed legacy paint edits still use the geometry build workflow. Rebuild jobs never call the exporter or write the catalogue.

Load permanent masks, attach the **task's** height asset, then compile; never reread a latest-height pointer midway. Upper-floor node indices can change when an asset adds floors elsewhere (`webapp/app/control/geometry.py:340-364`); never carry node indices across height digests.

Asset builds/checks stay on permanent/base geometry. Feature compilation emits fixed-domain/base-edit data for later consumers; it does not simulate door states during height building. Preserve exact legacy overlap reclassification and bundle atomicity (`webapp/app/control/features.py:510-605`). No engine consumer is enabled here.

## 4. Compilation location, identity, and caching (proposed)

### One compiler, two execution sites

Use `app/control/features.py` as the sole deterministic compiler, executed in control-capable child processes on **both** sides. The worker compiles rebuild diagnostics and fully verifies artifacts on first control-child load; local control uses the same path. The web app delegates artifact misses and authoritative report validation to an isolated verification/control child. Its feature-service parent projects inputs and reads database manifests without importing numpy, scipy or `app.control`. Preserve the existing app boundary: `app.main` already imports NumPy through fight-EV; new feature services must introduce no heavy control imports, while the worker server remains strictly free of NumPy/SciPy/Pillow and control modules (B: `webapp/tests/replays/test_control_isolation.py:27-83`).

The artifact service ensures artifacts outside HTTP page rendering: on startup/current-input discovery, tags/version changes, height activation and before dispatch. Missing artifacts hold that map's feature-bearing rounds for preparation; previous results remain visible as stale. Never label base-only fallback as consuming features. Unreadable source, corrupt cache and compiler exceptions are retryable infrastructure failures; a valid pending compilation succeeds.

### Exact four-part key

Use `(map, height_digest_or_flat, tags_digest, compiler_version)`.

`tags_digest` is full SHA-256 of a **context-qualified runtime tag envelope**, not file bytes or the export's claimed `runtime_digest`. The stdlib input module recomputes it from:

1. Intended bundles: enabled and named to a registered consumer, selected **before** placement validation; their runtime member definitions, targeted triggers, owned routes and references.
2. Other feature base edits needed to detect outside overlap, and relevant legacy tag/paint reconciliation inputs. Unrelated disabled behaviour is excluded; outside base edits affecting overlap are relevant.
3. Permanent sight/walk/barrier identities, specials, scale, schema/normalization identity, reducer semantics and consumer versions/registry. Qualifying the tags digest with these dependencies preserves the four-part key when base geometry changes.

Height is its own axis. Known deprecated floor selectors are retained in source but excluded from normalized runtime input. Names, notes, review, checklist, UI, list order and export metadata stay editorial. Unknown keys remain runtime. Verified precedents: `webapp/app/replays/map_feature_schema.py:278-312` and enabled-subset projection `webapp/app/control/features.py:832-842`; the expanded dependency envelope is **proposed**.

Reports have a separate full-catalogue `diagnostic_tags_digest` and exact raw-source snapshot hash. Capture the original `tags.json` bytes in the consistent-read operation, hash those bytes before parsing, and pass the raw bytes/hash explicitly with the parsed map view. A parsed dictionary cannot recover the raw hash. Canonical catalogue bytes/hash and raw file bytes/hash are distinct fields; whitespace/key-order-only edits change raw identity while runtime identity stays unchanged. Neither diagnostic identity enters control freshness. Display labels are outside runtime hashing.

The artifact manifest covers map, exact height, tags digest, schema/compiler/reducer/consumer identities, intended/active bundles, canonical pending outcomes and full compiled-part hashes. Its hash is the artifact digest. **All intended bundles pending still produces a manifest**, recording that outcome; only no intended bundles produces no runtime manifest. Today's empty registry and no-tag inputs remain unchanged.

Canonical formats are explicit and separate. New feature-only normalization version 2 uses an ASCII, typed canonical tree for Python/browser runtime and diagnostic catalogue digests: finite numbers are IEEE-754 binary64 big-endian hex, negative zero normalizes to positive zero, booleans have a distinct type, object keys sort by UTF-16 code units and strings use JSON ASCII escaping. Whole-valued numbers outside JavaScript safe-integer range are rejected with a source path instead of silently rounded; raw source remains retained. This avoids exponent-format discrepancies such as `1e-7` vs `1e-07`. Keep the existing generic version-1 digest helper and all ordinary replay/control fingerprints unchanged. Artifact/context JSON uses the separately named Python-only UTF-8 canonical format with `ensure_ascii=False`; browser export digests are advisory and never substituted for the context-qualified key. Shared golden vectors cover exponent fractions, negative zero, Unicode values/keys, safe-integer boundaries and nonfinite rejection (B: `webapp/app/replays/map_feature_schema.py:293-308`; `webapp/scripts/control_tagger_core.js:764-812`).

### Cache tiers and estimated cost

The database is the durable immutable cache/archive. Control processes have a bounded in-memory cache; worker/local disk caches use the exact key/content address with atomic staged writes. Never cache by map/filename alone. Rehash bytes on a hit; first process use proves definition-to-output correspondence. Repeated verified immutable use within one surviving process avoids recompilation. The worker currently starts a fresh control subprocess for each round (B: `replay_worker/server.py:1075-1089`), so first-process verification/recompilation repeats per worker round even with warm disk bytes. Measure that actual lifecycle, separately from a persistent local pool; no new persistent worker architecture is proposed here. Failures are not successful cache entries; pending outcomes can be cached for their exact key. Eviction deletes neither source nor database history.

Extend height pushing with an artifact-push endpoint in `replay_worker/server.py`, using the existing private-service boundary and task/body limits. The routes are currently unauthenticated within that private boundary (B: `replay_worker/server.py:100`); no authentication subsystem is added. The stdlib server checks structure/content address; the child checks geometry, assets and correspondence. A missing artifact yields a specific cache-miss response; the dispatcher pushes that exact database artifact and retries. Missing/corrupt artifacts after admission or in the child are explicit infrastructure outcomes, and unsupported worker semantics are compatibility-wait outcomes. Both local and remote collectors must recognize them before the existing engine-failure persistence path; never store a permanent failed round for artifact/preparation/compatibility failure. Eviction/corruption between admission and execution must be tested (B: `webapp/app/control/task.py:405-408`; `webapp/app/services/replay_control_remote.py:378-382`). Digest-less tasks never silently load cached features. Verified analogues: `replay_worker/server.py:904-921`, `1502-1546`; `webapp/app/services/replay_control_remote.py:470-483`; `webapp/app/control/task.py:87-101`.

**Estimate [unverified]:** a few door shapes/states should compile in tens to hundreds of milliseconds with geometry warm (planning range 10-300 ms). Rasterization evaluates 256 x 256 cells per polygon edge, expansion touches 1024 x 1024 pixels, placement visits affected 128 x 128 cells, bundle checks compare masks, and `compile_assets` repeats movement/sight work per state (`webapp/app/control/features.py:67-114`, `140-186`, `300-319`, `510-581`, `845-867`; constants `webapp/app/control/geometry.py:60-65`). More vertices/states/occluders and overlap pairs increase cost. Child startup/import/attachment may add 1-5 seconds [unverified]; large catalogues may take seconds [unverified]. These estimates exclude height rebuilding and full visibility generation, neither required to compile features. Measure cold/warm seconds, peak memory and compressed bytes before choosing cache limits; no benchmark or production-size catalogue was measured here.

## 5. One-floor default and pending/report rules (proposed)

The owner confirmed no gimmick on any map overlies two floors. This is a supplied domain constraint, not a map finding.

Use one placement resolver for movement footprints, sight occluders, ground-relative bounds, triggers, route endpoints/access sites and potential-ground edits. Rasterize as today, then inspect every required engine grid cell. Decorative route paths stay drawing-only; only endpoints/authored access sites require placement (`webapp/app/control/features.py:645-659`).

On a height asset, count real finite floors in the **asset**, after validating its domain, not merely `geo.node_of >= 0`: there is a synthetic lowest node even for unknown cells (`webapp/app/control/geometry.py:345-362`). Exactly one real floor selects itself despite height drift/origin changes. Zero floors, unresolved/nonfinite heights, multiple floors, empty required raster, unplaced endpoints or unavailable required ground means pending. Never snap to another cell, pick a nearest floor, reuse a band, or use unresolved all-floor fallback.

Check every state and authored moving-phase shape. Any pending required part makes **the whole gimmick** contribute no movement block, sight occluder, transport arc, trigger activation or base edit. Never bind only its good cells. A trigger placement failure also makes its dependent intended bundle pending. All members of that bundle contribute nothing; independent bundles continue. Existing atomicity is at `webapp/app/control/features.py:510-605`; this extends coverage beyond state checks (`470-485`). Placement success never invents behaviour, dimensions, durations or activation evidence.

Ground-relative occluders use the existing median physical ground, `node_z - STAND_M`, under their footprint, plus authored offsets (`webapp/app/control/features.py:266-292`). Recompute for every height version, including origin shifts. World bounds stay world-relative; explicit `all_height` stays explicit, but neither bypasses gimmick placement checks. Unresolved bounds still block nothing.

A genuinely flat map (no usable height asset) keeps one walkable 2D node per eligible cell and existing 2D sight behaviour; there is no measured-ground claim. Preserve flat legacy-ground restoration through two-phase compilation: first validate bundle ownership/exact legacy reconciliation, then construct a private candidate walk domain containing only permitted potential-ground edits of that bundle. Resolve all required parts against that candidate domain, and commit the complete base-edit delta only if the whole bundle is eligible. Pending bundles leave no temporary ground or sight edits; one bundle cannot borrow another candidate domain (B: `webapp/app/control/features.py:585-605`; `webapp/tests/replays/test_control_features.py:710-719`). Height-backed placement still requires a real asset floor and cannot fabricate floors outside its permanent build domain. Unresolved cells **inside a height asset** are not exempt. `off` explicitly selects `flat`, never reuses the old 3D artifact or falls back to a committed height pointer. Database selection distinguishes an active usable asset, known off/no-usable-active state after height history, and an untouched map with its existing committed fallback. Planning and tasks carry that explicit selection; flat control geometry omits the height key as before, while its artifact key uses `flat` (B: `webapp/app/services/replay_control.py:151`; `webapp/app/control/geometry.py:407`). Restored ground without a floor in a height asset stays pending; automatic fabrication of floors outside the permanent build domain is not added.

### Exact rebuild report

Replace the existing state-problem string list (`webapp/app/control/height_job.py:97-115`, `132-134`) with deterministic structured `report.features`, verified against the candidate asset and explicit tag snapshot:

- Map, new height digest, previous digest/null, compiler/schema/normalization identities, full-catalogue `diagnostic_tags_digest`, raw source snapshot hash and the snapshot used.
- Counts: total tagged, placeable, pending, disabled/unregistered, active intended bundles, pending bundles; runtime artifact digest/null. Placement and runtime eligibility are separate statuses.
- One entry for **every** tagged feature, including disabled/unregistered: stable ID, display name as recorded, bundle ID, placement status, runtime status, sorted reasons. No registered consumer is an eligibility reason, not a false floor error.
- Reasons: stable code (`multi_floor`, `missing_floor`, `unresolved_height`, `off_ground`, `empty_geometry`, `unplaced`, `unresolved_bounds`, `unresolved_behaviour`, `legacy_overlap`, `bundle_dependency`, or source/compile failure), exact source path/state/endpoint, sorted row-major cell IDs, cell count and actual floor counts. Bundle dependencies name blocked members. Other unresolved facts retain specific paths/reasons.
- Comparison, when a previous placement snapshot exists: newly pending, still pending and recovered IDs; old/new physical-ground ranges and maximum world-ground shift for placeable ground-relative features. Missing comparison is explicitly unavailable, not an empty success.

Full cell lists stay in stored JSON; command output may show counts/bounded samples. Pending is diagnostic, never a height-policy failure, and never requires re-tagging before activation.

Height building and integrity/policy verification load only permanent masks, scale and the existing check set, through a tag-independent base loader. They must not read tags, feature pointers or implicit heights before diagnostics; otherwise malformed source fails before diagnostic error handling. Specials currently read by those loaders are not consumed by height building/checks and are outside the height evidence manifest. Preserve all actual height checks/rules. Test the real builder and real verifier with missing/malformed tags, not only worker stubs (B: `webapp/app/control/height_job.py:123`; `webapp/app/control/height_verify.py:25-28`; `webapp/app/replays/height_inputs.py:61-85`).

The worker uses an explicit build-time tag snapshot for its provisional report. Extend `ControlClient.open_build`, worker HTTP handler, `HeightBuilds.open/start` and the child task to carry a separate version-1 diagnostic envelope containing exact raw source bytes/hash, map and previous placement snapshot. Store it immutably with the build and return its identity when a web restart resumes that same evidence key; never replace it with current tags. It is excluded from height-evidence/retry identity. New workers advertise diagnostics protocol 1; old workers may build valid heights without provisional diagnostics, marked explicitly unavailable, while the web child still supplies authoritative diagnostics (B: `webapp/app/services/replay_control_remote.py:174-180`; `replay_worker/server.py:1185-1250,1555-1564`). Before storage, the web app's isolated verifier rechecks against its current consistent snapshot. Tag-only changes trigger re-reporting/recompilation, not a repeat height build. Preserve the provisional identity as audit metadata and store the authoritative identity separately. If source reading or diagnostic compilation fails, report that error, allow heights that passed integrity/policy to activate, and retry feature preparation independently; hold feature-bearing rounds if their artifact is unavailable. Never report unreadable source as 'zero pending', as the current helper can (`webapp/app/control/height_job.py:103-109`).

Rejected builds also retain diagnostics, explicitly describing the candidate rather than active map. Cadence/retry identity stays based only on height evidence (`webapp/app/replays/height_inputs.py:78-85`, `105-116`). Manual activation or `off` prepares current tags and emits a fresh diagnostic result; it does not rewrite an old report as if that build used today's tags.

## 6. Replacing generation publication and verification (proposed)

Runtime selection no longer uses `publish_generation` or `active_sha`. Store immutable artifacts without moving `index.json.features_sha`; exact current height/tags/compiler inputs select a key. Retire generation-pointer loading and task caching together (`webapp/app/control/geometry.py:398-404`; `webapp/app/control/features.py:934-1008`).

Preserve both verification duties: rehash actual compiled bytes, then prove definitions compile to them (`webapp/app/control/features.py:910-929`). Current `compile_assets` stores occluder **mask hashes**, not mask bytes (`845-867`); new artifacts carry complete bit-packed masks, dimensions/bit order and base-domain/delta data, making them loadable. Digest claims alone are not runtime assets.

Live-task rules:

1. Plan from a consistent base/tags snapshot and database height selection. Resolve the artifact **before** fingerprinting; retain those inputs in the job instead of rereading tags during send.
2. Load the exact task height and artifact. Check map/height/context/version, content address, all part hashes and definition-to-output correspondence. Reject unexpected presence as well as absence; replace today's optional truthy feature check (`webapp/app/control/task.py:379-381`).
3. Return actual geometry and artifact digest. The web app checks planned/used agreement, verifies provenance and artifact, and rechecks current inputs before storage. Newer heights/tags/compiler supersede the job; preserve the old displayed result and queue the new version. Existing checks: `webapp/app/services/replay_control_remote.py:322-366`; `replay_control_store.py:36-48`.
4. Coordinate final validation with height-map and replay-write locks using one order, map then replay. Recheck deploy/tag snapshot immediately before writing. Atomic tag replacement after that check leaves exact provenance and makes the row stale at its next freshness read. Insert artifact/reference atomically enough to prohibit dangling references; height activation requires no frozen feature pointer.

Historical verification and current freshness are separate:

- A stored round resolves **its recorded artifact**, archived height and snapshots, rehashes them, verifies correspondence under its recorded compiler, and reconstructs its fingerprint from the recorded envelope. It never compiles against today's tags/active heights. Later repo edits do not invalidate past evidence.
- Freshness compares with current recipe/link/geometry/figures/versions. A shared parent-safe current-geometry resolver supplies exact height selection and artifact headers without compiling: planning, control pages, gap pages and both control/gap writers use it. A missing artifact/source error marks old rows stale and holds affected work, never a base-only current result. Gap-only jobs pass their planned control fingerprint to the gap writer and perform the same map-then-replay lock/current-input check before replacing rows (B: `webapp/app/services/replay_gaps_store.py:38-75`; `webapp/scripts/compute_control.py:254`). Historically verifiable can still mean stale. Existing round storage replaces the row on recompute; no round-history subsystem is added. Retained artifacts do not imply retained old round results.
- Missing/corrupt archive is a verification failure, never 'no features'. Missing worker cache is recoverable by pushing database bytes. Unsupported historical compiler yields 'unsupported for recompilation', not 'verified'; hashes/provenance may still be checked.

No-map/no-intended-feature paths retain fingerprints, summary bytes and loading behaviour. E6 remains in current code until this replacement is implemented, proven and shipped.

## 7. Contract amendment ledger (proposed; no contract edit in this task)

Change the amendment header and sections **1, 2, 4 and 8**. Sections **3, 5, 6 and 7** retain reducer, fixed-domain, traversal and consumer policies; none is wired here. Exact old sentences or complete field bullets below are from `docs/superpowers/specs/2026-10-04-map-features-contract.md` at R. Proposed replacements follow each quote.

### Header amendment, lines 9-15

> Amended 2026-10-08 (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5, the owner's P5): a floor binding no longer has to be read from the map's current height asset.

Replace: 'Amended by the 2026-10-08 map-features-survive-height-rebuilds design: authored minimap shapes and behaviour are permanent; each required cell with exactly one real floor selects that floor from the compiled height asset, without owner floor tagging.'

> It records the lowest floor of the asset it was read from (origin_z) and its band is rebased to the current asset's, so a tagged feature persists across rebuilds and follows its floor; one whose rebased band stops picking exactly one floor per cell is pending and is listed in that rebuild's report.

Replace: 'A gimmick touching a multi-floor cell or otherwise unplaceable is pending, contributes no runtime effects, is listed with reasons in that rebuild report, and is never deleted; ground-relative bounds are derived afresh from the selected floor.'

> **Not amended:** section 8.

Replace: 'Section 8 selects immutable per-map compilation artifacts by exact height, normalized tags and compiler inputs, with each round recording its artifact digest.'

> A published generation still names the height digest; a map that has one is not rebuilt automatically and its heights can't be changed by hand until generations can be recompiled.

Replace: 'After the replacement ships, tagged maps rebuild and permit manual height activation/off under normal height rules; before then the existing E6 generation guards remain.'

### Section 1 field bullets, lines 32-48

> Feature: `id`, `name`, `preset`, `kind`, `category` (checklist key), `capabilities`, `states` (`name`, `blocks_movement`, `blocks_sight`, `terminal`, `footprint` geometry, `sight` occluders, `sight_bounds` for the footprint), `initial_state` (null = uncertain, a warning), `transitions`, `reset`, `floors` (floor ids, or unresolved), `parent`, `bundle`, `rotation` (`pivot`, `panel`, `direction`, `start_deg`, `end_deg`, `phases`), `noise` (`makes_noise`, `origin`, `notes`; annotation only, no hearing), `base_edits` (`potential_ground`, `remove_sight`, `ground_binding`, `reclassify` [{`source`, `geometry`}]), `review` (draft / needs_verification / user_reviewed), `notes`, `parser_bindings`.

Replace: 'Feature: `id`, `name`, `preset`, `kind`, `category` (checklist key), `capabilities`, `states` (`name`, `blocks_movement`, `blocks_sight`, `terminal`, `footprint` geometry, `sight` occluders, `sight_bounds` for the footprint), `initial_state` (null = uncertain, a warning), `transitions`, `reset`, `floors` (preserved legacy source; excluded from runtime), `parent`, `bundle`, `rotation` (`pivot`, `panel`, `direction`, `start_deg`, `end_deg`, `phases`), `noise` (`makes_noise`, `origin`, `notes`; annotation only, no hearing), `base_edits` (`potential_ground`, `remove_sight`, `ground_binding` (preserved legacy source; excluded from runtime), `reclassify` [{`source`, `geometry`}]), `review` (draft / needs_verification / user_reviewed), `notes`, `parser_bindings`; feature and restored-ground placement use the automatic one-floor rule.'

> Trigger: `id`, `name`, `type` (switch / shoot / proximity / other), `geometry` (point, polygon or paint), `range` (proximity without an area: a value object, unresolved allowed, never a default radius), `floor`, `targets` [{`feature`, `event`}] (explicit; nothing pairs by proximity), `timing`, `noise`.

Replace: 'Trigger: `id`, `name`, `type` (switch / shoot / proximity / other), `geometry` (point, polygon or paint), `range` (proximity without an area: a value object, unresolved allowed, never a default radius), `floor` (preserved legacy source; excluded from runtime), `targets` [{`feature`, `event`}] (explicit; nothing pairs by proximity), `timing`, `noise`; trigger placement uses the automatic one-floor rule.'

> Route: `id`, `name`, `owner`, `kind` (zipline / rope / teleporter / drop / custom), `endpoints` (exactly two, {`id`, `uv`, `floor`}), `path` (drawing only), `access` (`endpoint_only` or {`sites`: [{`id`, `uv`, `floor`}]}), `directions` [{`from`, `to`, `entry` s, `transit` s, `length` m}], `states` (owner states it runs in, or null), `in_transit` (complete / abort / unresolved).

Replace: 'Route: `id`, `name`, `owner`, `kind` (zipline / rope / teleporter / drop / custom), `endpoints` (exactly two, {`id`, `uv`, `floor`}), `path` (drawing only), `access` (`endpoint_only` or {`sites`: [{`id`, `uv`, `floor`}]}), `directions` [{`from`, `to`, `entry` s, `transit` s, `length` m}], `states` (owner states it runs in, or null), `in_transit` (complete / abort / unresolved); endpoint/access `floor` fields are preserved legacy source excluded from runtime, and placement uses the automatic one-floor rule.'

> Floor binding: `id`, `label`, `z_band` [lo, hi] (metres of position-z above `origin_z`) or null (a manual label), `origin_z` (the lowest floor, in world decimetres, of the asset the band was read from), `height_sha` (that asset: a record, not a condition).

Replace: 'The legacy `floors` catalogue and its fields are preserved for source round trips but no longer select runtime floors or prompt the owner; compilation derives placement only from shapes and the exact height asset. Schema version remains 1; this explicit semantics change has a new normalization/compiler version, and unsupported source versions remain refused.'

Add after the editorial bullet (lines 52-53): 'Known legacy floor selectors, including ground-relative bounds.floor, are excluded from the new normalized runtime view; other unknown keys are retained and hashed. Ground-relative bounds themselves remain authored behaviour.' The existing editorial sentence is unchanged.

### Section 2 pending/bundles, lines 62-69

> A floor binding without a band, with a band whose frame is unknown (no `origin_z`, and another asset than the map's), or matching zero or several floors of a cell after rebasing, is pending: it binds nothing (never all floors).

Replace: 'On a height asset, every required cell of a gimmick must have exactly one real finite floor; zero floors, unresolved heights, multiple floors or unplaceable required geometry makes the whole gimmick pending with no runtime effects, with every reason listed in that rebuild report and all authored data retained. A flat map uses its one walkable 2D node per cell.'

> A bundle publishes its base edits only when enabled, named to a registered runtime consumer (`features.RUNTIME_CONSUMERS`, empty in this build), its behaviour is resolved, its floor bindings are verified, legacy overlaps are reclassified exactly and nothing outside it overlaps.

Replace: 'A bundle contributes runtime assets and base edits only when enabled, named to a registered runtime consumer, its required behaviour is resolved, every member/dependent placement satisfies the one-floor rule, legacy overlaps are reclassified exactly and nothing outside it overlaps.'

> Otherwise nothing of it is published and the map's geometry is unchanged.

Replace: 'Otherwise the whole bundle contributes nothing; its pending or disabled status is recorded, independent bundles remain eligible, and a height rebuild passing the height gate still goes live.'

### Section 4 references, lines 89-93

> Floors are bound by height bands in metres above the binding's own `origin_z`; before use a band is rebased into the `node_z` frame (position-z metres above the map's current lowest floor).

Replace: 'Each required cell binds to its sole real floor in the exact height asset named by the compilation; a multi-floor or unplaceable gimmick is pending, and authored bands/old floor IDs never choose among floors.'

> Occluder bounds are ground-relative (`ref: ground`, on a bound floor: the floor's physical ground is its node z - `STAND_M`, the median over the occluder's cells), world (`ref: world`, converted through the height asset's `origin_z`), `all_height`, or unresolved.

Replace: 'Occluder bounds are ground-relative (`ref: ground`, using median physical ground, node z - `STAND_M`, of automatically selected floors under its cells), world (`ref: world`, converted through the exact height asset's `origin_z`), `all_height`, or unresolved; all resolved forms still require gimmick placement to pass the one-floor rule.'

The next sentence, 'The band is [bottom, top).', sight sampling and flat-map sight rules remain unchanged.

### Section 8 freshness, lines 165-169

> `features.manifest` is None for a map with no publishable bundle: no `features` key appears in its control inputs, so its fingerprint is unchanged.

Replace: '`features.manifest` is None when no bundle is intended for a registered runtime consumer: no `features` key appears in control inputs and legacy fingerprints remain unchanged; intended pending bundles produce a manifest recording their disabled outcome.'

> Otherwise it covers the enabled runtime definitions, compiled blocked nodes / occluders / arcs (hashed from canonical bytes), the height digest and the schema, compiler, reducer-semantics and consumer versions; `features.verify` re-hashes what was loaded and recompiles the definitions.

Replace: 'Otherwise its digest identifies an immutable per-map database artifact covering intended runtime definitions, pending outcomes, complete compiled nodes / occluders / arcs / base edits and canonical hashes, exact height digest, relevant base/legacy inputs, and schema, normalization, compiler, reducer-semantics and consumer versions; a round records this digest, and verification rehashes loaded bytes and recompiles archived definitions against recorded geometry under the recorded compiler.'

> Editorial edits never move it.

Retain unchanged. Report display labels never enter the runtime manifest. No other contract sentences change.

A future superseding amendment is also needed in the auto-rebuild design: section 3 generation skip, section 4 generation-arrival failure row, section 5 band-selection/freeze, and E6. Do not edit it in this task. Cadence, database heights, gate, evidence-deletion rule and enablement remain (`docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md:113-132`, `161-225`, `227-258`, `275-283`, `357`).

## 8. E6 transition, revisions, and recomputation (proposed)

Ship compatible web/worker changes after the height-rebuild dependency is available. Until then retain E6. At shipment:

- Remove `HasGeneration`, `GENERATION_NOTE`, `_generation_note` and checks in `store_build`, `activate`, `deactivate`, plus obsolete `generation` arguments/catches (`webapp/app/services/control_heights.py:58-71`, `226-288`; `webapp/app/services/replay_heights_remote.py:205-215`, `265-275`). Also remove the CLI generation lookup/arguments and exception handler (`webapp/scripts/control_heights.py:110-123`); search all callers before removing the exception.
- Remove `plan_maps`' generation skip and `_finish`'s generation-arrived refusal (`webapp/app/services/replay_heights_remote.py:161-163`, `234-235`). Add report/artifact preparation, never a tagging exemption from rebuilds.
- Keep map locks, height integrity/policy, first/5-match cadence, evidence-change rebuilds, deletion-driven off and parse preemption. Feature pending is not a gate. Activation prepares the new artifact; held feature-bearing rounds resume when it is ready. Independent feature-preparation failure must not repeatedly fail/rebuild valid height evidence.
- Retire `index.json.features_sha` runtime selection/publishing. Preflight unexpected legacy pointers and flag explicit conversion rather than run two selectors. R has no committed generation and an empty registry, so no live-feature conversion is needed there (`docs/superpowers/specs/2026-10-04-map-features-contract.md:5-15`; `webapp/app/control/features.py:444`). Shipment-time state must be rechecked [unverified].

Bump `COMPILER_VERSION` and feature manifest/normalization versions for placement/serialization. Keep source schema version 1, explicitly preserving deprecated floor fields; this is a declared compatibility rule, not inferred migration. Reducer/consumer identities change the context-qualified tags digest. Do not bump global `CONTROL_REVISION`, `DATA_VERSION`, `SUMMARY_VERSION`, condenser recipe or height rules solely for this storage/compilation release: no engine consumer or existing byte interpretation changes, and global bumps would stale no-tag maps. Internally version additive provenance. The later doors release follows the normal `CONTROL_REVISION` rule when computations change (`webapp/app/replays/control_format.py:30-35`, `47-57`).

| Change | Staleness / work |
| --- | --- |
| Active height changes, tagged or untagged | All computable control rounds of that map go stale; compile artifacts for that height; worker recomputes round control and dependent gaps. |
| Manual rollback/off | Same rule; current tags select the exact old-height/flat key, reusing a verified identical artifact when available. |
| Relevant runtime shape/behaviour/enablement edit | New tags key/artifact; affected map control/gaps stale; no repeat height-evidence build. |
| Compiler/normalization/reducer/consumer version | New artifact identity and affected control/gaps; no global no-tag invalidation. |
| Editorial, irrelevant disabled behaviour, retired floor-selector-only edits | Runtime identity unchanged; report identity/labels can change independently. Relevant outside base-edit overlap still counts. |
| Cache eviction/redeploy | Rehash/reverify/repush database artifact; no fingerprint change by itself. |
| Parser/condenser recipe, source, link, existing engine rules | Existing replay/control freshness applies independently; this release changes none of them. |

Verified: fingerprints cover versions, recipe, source, link, geometry and consumed figures (`webapp/app/replays/control_format.py:111-116`); differing fingerprints make planned rounds stale (`webapp/app/services/replay_control.py:289-305`); gap fingerprint/tick-cache key depend on control fingerprint (`webapp/app/services/replay_gaps.py:50-67`). Parse recipe depends on parser commit, condenser/format and maps/agents assets, not control tags/heights (`webapp/app/replays/contract.py:104-109`; `webapp/app/replays/format.py:88`, `178-187`). Feature changes need no re-upload/re-condense of `.vrf`. Idle work recomputes derived control/gaps; builds precede that map's rounds and old stale results stay visible (`docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md:129-159`).

Deploy compatibility must include feature compiler/manifest/consumer identities, not just the parser recipe: that recipe does not detect this release. A worker unable to consume the new artifact protocol is waited for at no retry cost for affected maps; tagged tasks are not sent without their expected feature input. Untagged tasks continue. Only remove E6 in the same shipped implementation that replaces all pointer-dependent planning/loading/checking; no intermediate release removes the guard by itself.

## 9. Proof obligations and scope (proposed tests, not tests run here)

| Test | Required evidence |
| --- | --- |
| Tagged Summit doors stay live | Synthetic valid doors: first build at 2 and next after 5 additions; no planner skip, trusted height active, tags bytes unchanged, artifact ready, rounds stale/requeued. Include the old publication-during-build race. |
| Doors follow new heights | Unchanged shapes without floor fields, two sole-floor assets with changed world ground/origin: new bindings/bounds match new ground, digests differ, each verifies with its own snapshot/height. Add upper floors elsewhere to prove fresh node indices. |
| Multi-floor cell makes whole gimmick pending | Add a second floor in one required movement/sight/phase/trigger/endpoint/access cell: no partial blocks/occluders/arcs/base edits, dependent bundle contributes nothing, independent bundle remains, height activates, exact report reasons/cells, source preserved. Later sole-floor asset recovers automatically. |
| Unplaceable and flat distinction | Zero floor, unresolved synthetic node, off-ground, empty shape/missing endpoint pending. Flat mode obeys 2D policy; unresolved height-asset cells are not exempt. World/all-height cannot evade placement checks. |
| No-tag maps byte-identical | Compare canonical inputs/fingerprints with `tests/fixtures/control/map_features/legacy_inputs.json` using its recorded recipe/link, fixed source hash and unchanged revision-8 context; additionally compare unchanged and new code under identical current revisions. Compare deterministic control data/summary bytes with no features/provenance key. Do not regenerate the fixture merely for this release. |
| Pending/disabled identity | Intended all-pending has a non-null manifest; no intended bundle has none. Enabling/recovering moves runtime digest; unrelated disabled/editorial edits do not; relevant outside overlap does. Disabled annotations still appear in rebuild reports. |
| Deterministic cache | Same key gives identical canonical bytes on web child/worker. Every axis/base/context dependency changes correctly. Cold cache, corrupt part, interrupted atomic write, concurrent inserts and redeploy recover without accepting mismatches. No-tag tasks need no artifact. |
| Historical verification | Round references H1/T1/A1; activate H2/edit T2/lose worker cache: stored round verifies from A1/H1 archives and is currently stale. Tampered mask/node/snapshot/digest/provenance fails. Unsupported old compiler is accurately reported. |
| Races/report truth | Height/tag/version changes between plan/send/store cannot label old output current. Tag-only mid-build edit updates diagnostics without rebuilding evidence. Snapshot mismatch detected; unreadable source explicitly reported. Rejected candidate diagnostics are not active placement. |
| Both writers/isolation | Local/remote store the same actual artifact reference. Infra/failed results do not write successful provenance. Web/server parent stays stdlib/DB-only; consumer registry stays empty in this release. |

Later test changes extend `webapp/tests/replays/test_control_features.py:27-47`, `937-979`, `1095-1111` (currently pins generation failure), `test_heights_remote.py:275-280`, `563` (guards), and `test_control_heights_db.py:276` (manual guard). Keep `test_control_isolation.py:27-65`. The legacy snapshot helper warns other deliberate revision/geometry changes can move fingerprints (`webapp/tests/replays/map_feature_legacy.py:1-9`, `23-36`); use its historical revision context rather than regenerate it to hide this release's effects.

Out of scope: wiring any engine consumer; Summit activation/state evidence; adding gimmicks/presets; changing tagger interface beyond removing floor prompts, obsolete floor-authoring instructions in `control_tagger.template.html`, and corresponding internal validation/preview semantics; height rules/gates/cadence; synthesizing floors; state-driven permanent-mask builds; Impact; replay parser; round-history UI; database execution; dependencies; push/deployment/PR. The original design task produced only this spec. This review revision updates this spec and its requested companion implementation plan; it changes no code, source tags, assets, migrations or unrelated documents.

## 10. Open questions and recommendations

1. **Historical per-map artifact retention?** Recommend no pruning in this release, matching retained heights. A later policy must keep every artifact referenced by a stored round. Database growth/compressed sizes are [unverified]; measure before designing retention.
2. **Does one pending member disable its whole bundle?** Recommend keeping existing bundle atomicity for base edits and dependent trigger/route placement. The pending gimmick blocks nothing; independent bundles continue. Automatic ownership splitting would change safety semantics and is excluded.

Storage was resolved by the owner's per-map clarification: shared immutable map/version artifacts with round references. Floor tagging, cadence, frozen pointers and today's E6 state are not reopened. The recommendations above are design defaults pending written-spec review; neither prevents committing the spec.
