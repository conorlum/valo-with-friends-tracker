# Ascent replay states on the site

This branch adds a Doors & glass tab and map markers to the existing replay player. New Ascent parses
retain door resets, closing, fully closed and destruction, plus transparent glass reset/destruction.
The tab has click-to-seek event times and a separately labelled five-second closing model. An old stored
replay explains that it needs parsing again. This is site code; the private standalone page embeds the
same template and JavaScript for review before deployment.

The runtime consumer is built but the owner's current annotations remain pending. Actual metre bounds
and the glass movement footprint are still unresolved. The owner selected a conservative movement cutoff
at 30% closure for Ascent and Sunset, instead of waiting for exact passage-clearance measurements. No canonical map tags are
published by this change. Approximate viewer markers are labels, not calculation geometry.

## Supported evidence and calculation

The pinned parser stays at `2b66c65a7b116154e18ebb84d9f6795f2b080233`, with a SHA-256-checked source
patch for bounded map-message capture. Both the local build script and the worker image apply it. The
worker enables capture only in its parser child. Standard event and movement output stays unchanged;
the optional sidecar has its own size, bit-boundary and descriptor checks.

The stdlib condenser maps exact Ascent actor names to explicit authored replay bindings. Network GUIDs
are evidence identifiers, never cross-match bindings. The reviewed class-net-cache descriptors identify
closing/closed, destruction and round reset RPCs. Missing/default parameters, intact opening, ambiguous
actor lifetimes, unsupported checksums and corrupt/truncated frames stay unknown or partial. Replicated
property snapshots are validated but do not become inferred transitions. Every round needs an explicit
reset in its own preceding buy phase; changes after that reset are retained even before barriers drop.

For doors, the observed closing time starts linear descent over five seconds. The closed RPC supplies
the endpoint; a 0.25-second grace accommodates replay timestamp skew, rather than inferring a missing
endpoint indefinitely. Destruction wins over later stale intact states. Opening and reversals remain
unsupported by this evidence consumer even though the authoring model can preview them.

The qualified `ascent_replay_v1` consumer requires explicit replay bindings and fully resolved geometry.
It reuses the existing reducer for observed states and the descending height-band sampler for sight.
Movement uses verified clearance or an explicitly selected conservative closure cutoff; transparent intact glass blocks its movement footprint only. Parent
platforms, rotation, authored route arcs and duplicate bindings are refused. A door needs measured height
geometry. Missing reset, incomplete capture or unsupported in-round state fails an active calculation;
it never silently computes an open door.

The compiler archives the reconciled permanent domain per map. Runtime reconstructs that domain without
mutating a cached source or changing node identities. Active and passive visibility, line-of-sight,
movement/unknown propagation, observer records and gaps share the sampled effects. Height bands do not
become flat smoke-pinch barriers. Destruction releases movement at its exact time. Gap release attribution
can say "lost sight as a map feature closed." Control headers/summaries retain artifact and timeline
identities and tell the viewer which features its computation included.

Revisions: condenser **15**, control **9**, feature compiler **5**, gaps **3**, Ascent consumer version **2**. Optional fields preserve old
reader compatibility, while versioned recipes invalidate stale derived products. The gaps service mirrors
the detector revision. Unfeatured control reference checks compare the unchanged calculations after
normalizing the intentionally bumped version header; they do not claim identical current-version bytes.

## Review and calibration sequence

1. Open the private site preview on round 16. Select Doors & glass and seek through the Market closing,
   closed and broken events. The observed closing interval is 5.002 seconds and the intact closed interval
   is 8.369 seconds. Compare the clock against the already-reviewed replay. Repeat the interrupted Garden
   closes and glass break; no whole-match hand annotation is required.
2. Keep the current full tags export as a backup. `scripts/prepare_ascent_features.py` creates a new private
   review candidate, binds the three explicitly supplied feature IDs, and creates their runtime bundles.
   It retains all other maps and unresolved fields. It refuses conflicting ownership and never writes
   inside a Git checkout or overwrites an existing output. Existing compatible bundles are reused.
3. In the tagger, verify each doorway's footprint, ground-relative bottom/top metre bounds and fully open
   clearance. Choose verified passage clearance or the owner's conservative 30% movement cutoff. A floor elevation
   or "one and a half players" estimate does not establish these metre values. Review any extra sight edge.
4. Draw/verify the intact glass movement footprint and explicitly remove unused sight geometry if the
   glass is transparent. Its break event is already supported. Diagnose the candidate using the exact
   measured Ascent height asset; check that only the intended bundles become eligible.
5. Compile/archive that reviewed candidate and compute one reviewed round through the normal control task.
   Check open/partial/closed/broken views from both sides, a low line under the moving panel, movement
   clearance, unknown propagation after destruction, the feature identities in the control header, and
   gaps-only parity with the full run. Then verify one scene in another recording. A second local Ascent
   capture already frames successfully with different network identities, but its scenes are not human
   reviewed and it is not held-out semantic acceptance evidence.

Example candidate command (use actual private paths and reviewed IDs):

```powershell
.\.venv313\Scripts\python.exe scripts\prepare_ascent_features.py --tags C:\private\full-tags.json --market feature-9 --garden feature-11 --glass feature-12 --out C:\private\new-ascent-runtime-candidate.json
```

## Owner-controlled release

The branch is in draft PR #128, stacked on the tagger repair branch in PR #127. The owner reviews the stack,
merges and deploys. This work never changes `main` or deploys either site. There is no new database schema,
Impact scoring change, hosted tagger draft/save service, or automatic publication of tags.

Before release, build the worker image on Linux and verify its pin/patch load, parsing and control smoke
checks. This was not rehearsed locally. The runtime image must contain the hashed patch file as well as the
pin; its build-time smoke check calls `load_pin()` to catch a missing file. Deploy compatible worker and
web code, verify matching recipes, then reparse the retained Ascent recording through the existing supported
ingest/reingest workflow. Reloading alone cannot add messages to old blobs. Run the control/gap recompute
workflow after publishing the resolved map artifact. Do not enable unresolved bundles just to make a metric
appear. Existing replay storage retains source recordings under its configured archive policy.

Before publishing tags, retain the previous map artifact and exact height input so the prior generation can
be restored. Inspect per-round failures after recomputation, and only then repeat on further matches.
Reopening verification can follow when the owner records a clear intact open; until then affected active
rounds remain unsupported. Production deployment and metric calibration are separate from passing the
synthetic integration checks.

## Conservative movement choice and Sunset evidence

The owner observed passage at approximately 30% closure and no standing passage at halfway. Crouching
at halfway may be possible but was uncertain and would slow movement. The chosen model permits movement
through 30% closure and blocks beyond it, for every stance; it does not estimate crouching speed. On a
five-second full close the cutoff is 1.5 seconds after closing starts. The same fraction applies when a
panel reopens, independent of the direction of travel, once an opening timeline is supported.

`sliding.movement_cutoff` stores `{status: "known", value: 0.3, unit: "fraction", basis: "conservative"}`.
"Known" records a chosen computation policy, not an exact physical cutoff. Invalid cutoffs are refused,
and a specified cutoff takes precedence over the optional metric movement clearance. Without this option,
existing metric behavior is preserved. The tagger exposes both models, previews passage at the chosen
fraction, and labels the conservative approximation. The cutoff changes the archived runtime identity;
compiler and consumer versions advance so old artifacts cannot silently acquire the new semantics.

Sight retains the continuous descending height-band model and still needs measured metre bounds. The
movement choice cannot resolve uncertain height geometry or make a flat preview a sight verification.
Sunset's open doorway is approximately the same height as Ascent's, but this estimate is not a metre value.

Sunset replay rounds 3, 10 and 11 were checked in game by the owner: full close then reopening, full close
then destruction, and destruction during closure respectively. Captured complete opening and closing
intervals are approximately five seconds. The owner drew a closed door footprint and a linked switch;
the private diagnostic retains an off-ground switch placement and unresolved sight bounds. Sunset's
timeline decoder, runtime binding and site overlay integration still need implementation; the shared
movement policy alone does not enable Sunset calculations. Private exports and identities stay outside Git.

## Verification record

The exact base-plus-hashed-patch parser built successfully on Windows; 25 focused parser tests passed.
The first recording's event and movement files have identical SHA-256 and size to the unmodified baseline.
Its 2,813 sidecar rows and a second recording's 3,089 rows decode without frame errors. Private source
hashes, assemblies, full receipts, replay blobs and site previews remain outside this public repository.

`test_ascent_feature_integration.py` compiles and verifies a real archive, removes static door paint,
checks observer sight at open/closed/destruction, checks feature release attribution, and proves full-run,
cached gaps-only and fresh gaps-only parity. It also refuses flat/unresolved/duplicate definitions and
checks candidate preservation and ownership. The decoder tests cover framing, exact bindings, resets,
unknown values, model/terminal semantics and Python/JavaScript parity. Existing compiler/archive/transport,
unfeatured references, sight, viewer, worker and condenser suites supply regression coverage. Browser
layout/storage and live PostgreSQL/deployment checks remain owner release work.

Final focused results: 37 Ascent/vertical/isolation tests and 27 viewer tests passed. The corrected
parser/condensation/worker/version subset passed 117 tests; the owning compiler/reference/gap subset
passed 164, and the engine/sight/viewer/contract/gap-task subset passed 147. These overlapping runs are
not an aggregate test count. Node syntax, application whitespace and the isolated parser patch checks
also pass. No immutable reference fixture was regenerated from the new implementation.

The conservative-cutoff follow-up passed 61 door-motion/runtime/preview tests and 90 tagger/input/archive
acceptance/isolation tests. These include exact cutoff boundaries, unchanged continuous sight bands,
invalid-policy refusal, restored passage when closure decreases, archived eligibility and destruction release.
