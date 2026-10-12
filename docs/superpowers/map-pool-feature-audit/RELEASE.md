# Map-pool gimmick release preparation

**Current status:** [Post-review fixes](REVIEW-FIXES.md) supersedes this historical
compiler-15 selection. The combined candidate has 19 active features; four Split
Vent ropes remain pending under the new walking-exit gate. Main integration and
Linux worker-image validation are complete on the feature branch.

The branch now connects bounded native replay messages to the reviewed panel state machines, adds
timed quiet rope arcs on measured floor nodes, and provides computation-derived site status. The
private owner candidate is not yet qualified for live activation. Main, deployed tags, live height
activation and live recompute have not been changed by this work.

The [Abyss rope completion](ABYSS-ROPES.md) supersedes the earlier eleven/nineteen-feature
selections with all 23 tagged features. Abyss uses bounded local walking ground for its ropes
while terrain sight remains flat; rejected global heights stay inactive. [FIRST-PASS.md](FIRST-PASS.md)
retains the earlier receipts and owner-controlled rollout. The historical global-terrain
checklist below remains separate from this completed feature selection.

## What is built

- The parser patch stack captures the additional Lotus switch and Summit shootable families. Its
  source hashes and build recipe are pinned. The existing parser capture remains opt-in and bounded.
- Per-round map RPC ledgers retain absolute source clocks and buy-phase resets. Explicit actor/class,
  reset and RPC rules bind Sunset, Summit, Abyss and Lotus; arbitrary proximity/damage guesses do not.
- Summit phases, breakable destruction, continuous descending sight, and Lotus's single ten-second
  rotation clock feed movement, vision and unknown propagation. The owner corrected the older eight-second
  recollection. Lotus movement follows the rotating slab at 16 Hz: a cell blocks movement only when
  more than 50% of its area is covered; exactly half remains passable. Passable cells may be entered
  at normal unknown speed, without a fixed doorway-wide passability window. Sight retains the panel's
  actual footprint regardless of movement coverage. Cell changes
  register as release/block calculation boundaries. Scheduled motion phases register as
  calculation boundaries. Destruction cancels later motion effects.
- Split/Summit/Abyss quiet rope directions use seconds and measured endpoint floors. Actual physical
  directions remain separate. Local quiet connector cuts can exclude unreviewed shortcuts.
- Generic site markers and event rows come from the calculated state trace. Door passage status agrees
  with the selected 30% cutoff. Existing Ascent display and consumer remain supported.
- `scripts/audit_map_feature_release.py` generates private source/placement reports. It does not write
  a DB, activate heights, publish tags or recompute live data. Framing, binding, geometry and release
  qualification have separate outcomes.

Current versions: feature compiler 15, condenser 17, control 14; Ascent consumer 3, quiet rope consumer 5,
map-pool replay consumer 5; height format 3, height rules 5 and height manifest 2.
Gaps version stays 3; changed control inputs invalidate its caches.
No new migration or database table is required.

The compiler revision fixes vertical rope editor markers: a nonblocking point marker is not an
object on a single floor. Its measured route landings own placement, while blocking geometry still
requires the ordinary floor checks. Compiler/consumer identities invalidate affected feature
artifacts. Control now consumes mixed 8/4-pixel height footprints as described below.
Compiler 11 also permits valid explicitly replay-bound shootable trigger markers on walls, using
the same geometry/source checks as switches. Actual panels still require their usual placement checks.
Height rules 5 separate physical sight validation from team attribution and allow exact permanent
walls to prove required blocked views without fabricating missing endpoint heights.
Compiler 12 allows explicitly replay-bound door panels to attach to adjacent permanent walls by
one paint cell. It retains the entire sight mask, resolves every touched walkable leaf normally,
and creates no traversal node on the wall. Unbound panels, deep wall extensions, entirely off-ground
panels, and missing or multiple ground floors remain pending. This avoids trimming a doorway's
vision blocker merely to satisfy coarse wall-edge placement.
Compiler 13 applies the same verified attachment rule to explicitly replay-bound breakable panels.
This resolves Abyss's original Heaven-breakable footprint without the withdrawn trim or any loss of
sight coverage. Its failed map-height gates still prevent release. The expanded feature/compiler,
artifact and mixed-grid suites pass 152 tests.

## Geometry and evidence checkpoint

The map-independent `scripts/measure_rope_positions.py` measures stable XY during monotone Z
movement regardless of weapon, noise or climb speed. It rejects typical jump acceleration and
horizontal departures and requires repeated rounds and a dominant spatial cluster. It hashes the
actual movement stream and verifies source/context identity. Output is a private coordinate proposal,
not certification of rope attachment, travel cost or endpoint floors.
It also produced private proposals for Summit's one and Abyss's four tagged vertical ropes. Those
proposals were staged in a new private disabled candidate. This changes XY and marker geometry only;
it does not certify landing floors or change reviewed travel times and quiet-direction semantics.

All six Split rope coordinates were measured from the local recording and staged privately without
changing the owner snapshot or other maps. Two A-Heaven ropes have supported stationary endpoint
heights and compile in an isolated enabled trial against the exact saved accepted Split asset.
Four remain pending: one has unresolved landing ground, and three lack complete stationary landing
evidence. Mid-ride height extrema are not substituted for boarding floors. All staged bundles remain
disabled; successful placement is not full real control/gaps acceptance.

The tagger now uses an exact attached placement report when the annotations and height identity
match. Edits invalidate it; coarse assets restore the approximate preview, while mixed assets require
a regenerated compiler report. This avoids false single-floor
warnings on the two measured vertical ropes without treating stale diagnostics as current.

Known transition RPCs with missing/unmatched parameters report the exact source row, timestamp,
parameters and expected conditions and hold the feature pending. Sunset's reviewed omitted open
completion is now qualified by an explicitly serialized three-bit `DoorState=0` for the same actor
at the exact RPC timestamp, with `Old State=1` and `isMoveTo=true`. Raw RPC parameters remain
unchanged; timeline evidence records both source lines. No absent/default value is assumed.
Properties alone, stale/nearby snapshots, wrong actors/widths, conflicting states and other omitted
parameters cannot activate a feature. The capture adds state witnesses only for Sunset.

On 2026-10-11 the owner matched the two additional Lotus switch scenes: B-side activation for
B–C and the outer, farther-from-site button for A. The private candidate now includes both sources
per door; each door binds successfully across all 24 rounds of that recording. This confirms door
identity and button side in that recording, not independent-recording stability or new timing evidence.

Follow-up pinned captures now establish independent-recording source bindings for Lotus (20/20
rounds, with activations from all four switch sources) and Summit (18/18 for each of the three
doors). Sunset originally bound 18/20 and 21/22 rounds. Re-reading both raw captures with exact
state-property witnesses now binds all 20/20 and 22/22 rounds, including the three previously
pending reopening completions. Reducer checks pass for every round; removing the witnesses restores
the pending outcomes. This resolves that source-decoding blocker without assuming defaults.
These are source checks, not new owner-reviewed behavior or geometry evidence.

Automatic local height refinement is implemented through versioned mixed-footprint assets, durable
worker history and runtime consumers. Unchanged acceptance gates use physical area. The initial
private builds passed on historical Lotus, Haven and Sunset inputs; Ascent and Summit failed then.
Later frozen rebuilds and the current checkpoint below supersede those outcomes. See the refinement
spec for exact local counts and limits. No rejected height asset has been activated.

The owner's follow-up Ascent terrain review distinguishes a genuinely covered lower surface,
stairs beside a flat wall, discrete box heights, and a ledge into a pit with side-by-side levels.
Those descriptions remain private evidence tied to the diagnostic input; they are not painted
height overrides. The earlier read-only sample check found both a suspect height boundary beside a wall
and remaining multiple-floor fine cells on the nonoverlapping ledge. Diagnose sample attribution
and footprint mixing before treating those as stacked geometry. The later wall-corner correction and
additional data clear the current private Ascent gates without painted elevation overrides.

## Current real evidence and blockers

The current-data follow-up rebuilt private height candidates under rules 5 and independently
verified their exact permanent domains. Six maps pass unchanged gates; Abyss remains rejected.
Compiler 13 supports the eleven intended first-pass features. Six complete real-round checks now
pass full control/fresh gaps-only/cached gaps parity on this generation, with 126 matching gap rows
in total across the six scenes. See FIRST-PASS.md for scope, recorded comparison counts and
inherited warnings. The earlier compiler-11/12 results below remain historical evidence.

The newest private height checkpoint clears Ascent and Summit. Additional frozen replay evidence
and team-independent physical validation let Summit pass without changing coverage thresholds.
The owner identified Ascent's crossed permanent wall corner; a private mask correction followed by
an exact-domain rebuild blocks all three required views. Its kill-line share remains 1.85% and its
supported area is 74.7%. Existing failures described below are historical checkpoints. Abyss still
fails its unresolved-overlap and kill-line checks. These results do not publish corrected masks,
activate heights, resolve every feature, or qualify fresh control/gaps.

Staged private bounds use the owner's approximate 1.5-player descending-door height as an explicit
2.7m model estimate, not a measured clearance. Ascent opening is five seconds per the owner.
Enabled diagnostic trials place both Ascent doors, Sunset's door, and Summit's Cave and Mid doors.
Ascent glass also places after removing its authored extension into a permanent wall: the exact set
of walkable footprints covered by the glass is unchanged. An earlier Abyss Heaven-breakable trim
was withdrawn after its private sight-mask proof was found to use the opposite mask polarity.
Compiler 13 instead qualifies the complete original panel's wall attachment. Both Abyss breakables
are placeable, while its failed height gates still hold every Abyss bundle disabled.
The owner confirmed Summit polygons are approximate doorway guides and panels should be flush
with surrounding walls. All three were privately aligned to the minimap wall edges. Cave and Mid
place with compiler 12; Heaven still has ten unresolved ground leaves, and its rope landings remain
pending. The permanent masks and reviewed timing/source rules are unchanged. All saved owner
bundles remain disabled; only isolated diagnostic copies enable them.
The 4-pixel panel raster is rounded outward into its wall attachments, with a private check that it
covers every pixel of the opening. It is not rounded inward to create a vision slit. The wall-edge
follow-up passes 135 compiler/runtime/artifact and mixed-grid diagnostic tests, including rejection
of missing child floors, unbound/off-ground panels, deep wall extensions and false wall anchors.
The current frozen Summit evidence contains no accepted stand or walk runs in those ten pending
Heaven-doorway cells across 56 rounds from three matches. This is missing local ground support,
not evidence of overlapping floors. Preserve the pending outcome until source evidence resolves it.

A complete fresh Sunset round on the staged bounds includes closing, fully closed, reopening and
fully open states. All 54 sampled full counterfactuals match incremental control, and all 23 gap
rows match replay of its tick cache. The round uses condenser 17 and compiler 11, an exact private
height asset, and the normal analytical schedule (355 instants). Knowledge layers are excluded.
The receipt retains existing unresolved-terrain and input fallbacks; this is one reviewed recording,
not full map-pool qualification. The separate cache-miss gaps-only run and its cached rerun produce
the same 23 rows as the full calculation. All three paths therefore agree for this round.
This proof uses compiler 11 and predates the wall-attachment correction; preserve its exact input
identity rather than labeling it as fresh compiler-12 acceptance.

The activation-fix regressions pass 153 focused height/job/input/map-runtime cases and 97 feature,
artifact and acceptance cases. Normal control still rejects missing team attribution. Source markers
without a valid binding, invalid coordinates and off-ground panels remain pending. Repeated Split
shaft motion was also compared with nearby accepted ground runs: mid-ride pauses are not landing
evidence, so those pending endpoints were retained rather than bound to incidental heights.

One older stored Ascent round lacks its map-feature timeline and correctly refuses computation with
active features. Regenerate affected stored rounds through the existing native-capture/condensation
path before recomputing control and gaps. Do not inject an assumed open state into older blobs.

Fresh native condensation resolves that missing timeline in the local Ascent trial. The first 25
seconds of a real round include Garden-door activation, destruction before full closure, and glass
destruction. All three Ascent features compile against the corrected exact-domain height asset.
All 60 sampled full counterfactuals match incremental control across 114 analytical instants, and
all seven fresh gap rows match cached replay. This is a round prefix, not a full-round acceptance
run; Market remains open in this scene. Unresolved-terrain fallback remains recorded. A fully
closed Market-door scene and independent gaps-only parity still need their own fresh checks.
This Ascent calculation also predates compiler 12; its receipt remains evidence for its own exact
compiler-11 inputs, not a new wall-attachment acceptance run.

Private native captures were rebuilt on the new parser pin and checked against recording hashes.
All four captures frame without errors. Summit's three selected doors bind in 19 rounds, Abyss's two
breakables in 23, and the selected Lotus switches/breakable in 24. These counts establish bindings for
the selected source actors, not comprehensive instance or second-button coverage. Sunset binds
all 42 rounds across two captures after the explicit-state witness correction. New unmatched
transitions still remain pending; these source checks do not certify all geometry or worker performance.

The earlier read-only live height inventory found active assets for Split, Sunset and Haven. Ascent, Summit,
Abyss and Lotus currently have rejected assets and no active selection. Their rejection reasons include
insufficient supported area, unresolved areas near multiple floors and blocked known kill lines.
Rejected assets were exported privately for diagnosis; they are ineligible for release. Do not weaken
the acceptance checks or activate them to make feature placement pass.

The earlier geometry checkpoint kept Summit's four, Lotus's three, Sunset's one and four of Abyss's six tagged
features pending. Two Abyss features are placeable against the diagnostic asset, whose rejection still
prevents a combined release. Many vertical rope endpoint cells expose only one measured floor; the
author's coincident endpoint coordinates are retained. A separate lower landing cannot be invented.
The owner clarified that the Lotus outlines are rough doorway-space guides: the door fills the
opening flush with surrounding walls and rotates about its center. The owner accepted the private
map-aligned doorway outlines and centered pivot placements. Preserve the original drawings separately;
their legacy pivot fields do not establish the physical panel or its thickness. Approximate player-height descriptions
remain estimates and do not resolve metric sight bounds.
The owner reviewed the centered rectangular panel preview, specified floor-to-ceiling coverage with
no vertical gaps, and confirmed both nearby switches activate their corresponding door. Both native
switch sources per door are now bound in the private reviewed recording and a second independent
capture. The 2.75-second passage observation belongs to the old eight-second
preview; it is retained as an angle reference, not imposed as a cutoff in the corrected ten-second model.
The owner accepts the current panel geometry and majority-cell/one-cell passage approximation
for this estimated unknown-position model. Further manual panel-depth or clearance measurement is
not a release prerequisite. Existing finite compiler bounds and floor-placement requirements still
apply; accepted estimates do not supply missing landing or ground data. Destruction removes the
blocker at the destruction-status event and cancels subsequent motion; ordinary damage does not
remove it. No additional owner decision on that behavior is required.

The local Lotus trial compiles all three bundles against the exact locally passing refined
asset. Explicit replay-bound switches may be mounted off walkable ground; their markers are not
movement or sight blockers. This exemption requires a valid source binding and finite in-map
geometry, and does not exempt the actual door panels. At that earlier checkpoint, Summit's four
features remained pending. The current staged bounds and wall-trigger correction place Cave and
Mid against its passing asset; Heaven's permanent doorway domain and the rope landings remain
pending at that checkpoint. The later flush-wall alignment preserves the existing doorway domain
and leaves Heaven pending specifically on ground heights. A single supported lower rope height
is insufficient to invent its upper boarding floor.

## Steps to finish, in order

1. **Freeze private evidence.** Keep the latest combined owner snapshot, original recordings, parser
   build receipt and source hashes. Never substitute an older per-map export. Stage revisions in a new
   private candidate. Verify exact actor names in another independent recording when available; an
   instance-specific name in one replay is not evidence of a universal map binding. Both physical
   Lotus switch sources are matched in two recordings, and Summit's three doors also bind in two;
   preserve those receipts. Abyss still has one local recording. Preserve Sunset's two-capture
   explicit-state witness receipt; do not generalize it into defaults for unrelated omitted fields.
2. **Resolve remaining ground and landing placement.** Door geometry estimates are accepted by the
   owner; retain the built majority-area Lotus model and the reviewed descending-door movement
   cutoff. Complete any still-missing finite bounds and floor references in compiler inputs without
   adding another manual geometry-review gate. For ropes, identify actual boarding
   coordinates and distinct replay position-Z floors; bind the named Choke 2 side for Abyss Mid.
   Preserve physical directions while applying the owner's quiet times and one-way restriction.
3. **Repair height evidence.** Diagnose failed global gates using source coverage and known kill lines.
   The owner selected automatic local 4-pixel refinement for mixed-height cells on every map, without
   painted guides. See `../specs/2026-10-10-automatic-local-height-refinement.md`: this is settled design,
   with the mixed-resolution asset, worker history and runtime built. Keep sparse regions explicitly
   unknown, genuine stacked floors distinct, and acceptance metrics weighted by physical area.
   Build fresh local candidates and rerun the unchanged checks. Use only accepted exact assets in final
   qualification. Rebuild-dependent floor references are verified again; do not retain stale node IDs.
4. **Qualify private enabled bundles.** Separate panel and rope consumers. Resolve permanent-domain
   corrections and trigger interaction ground. Run source, placement, bounds and artifact verification
   for every intended member. Check local rope bypasses and alternate legitimate walking paths. No
   missing member may disappear under a map-level success label. Haven remains the reviewed baseline.
5. **Compute real acceptance scenes.** On each qualified map run full control, then fresh gaps-only and
   cached gaps with identical recording, height, tags and compiler identity. Compare bytes/outputs and
   replay-visible timestamps. Destruction at its explicit status event is settled and covered by
   runtime tests; verify source binding and computed release without another owner behavior review.
   Test fully closed persistence, reopening,
   round reset, rope earliest arrival/parent, both directions and forbidden quiet travel. Inspect passive
   and active vision as well as unknown movement. Record synthetic and real evidence separately.
6. **Prepare the owner review.** Freeze runtime inputs, intended bundle matrix, private evidence receipts,
   tests and failures in the draft PR. Run the Linux worker image with its rebuilt parser. Verify recipe
   agreement and artifact/cache verification in a fresh worker environment. Estimate live recompute
   time from representative measured round jobs, not this branch's synthetic tests.
7. **Owner approves merge/deployment and publication.** Only then publish approved tags/assets and rebuild
   the worker image alongside the web deployment. This repo deploys both sites from main; the public
   demo remains isolated from friends recordings, heights and recompute writes.
8. **Reparse first, then recompute.** Check existing reparse capability/recipe status. Reparse one archived
   match per map on the new recipe, verify the ledger and unchanged existing replay/player data, then
   run one map's control/gaps as a canary. Inspect artifact digests, pending/failed counts and UI status
   before allowing the idle worker to process the remaining archive. Existing stale-round planning and
   retry/force semantics apply; do not assume a page reload changes stored calculations.

## Local audit commands

Run from `webapp`, with all inputs and output outside the public Git checkout:

```powershell
.\.venv313\Scripts\python.exe scripts\audit_map_feature_release.py `
  --tags <private-candidate.json> --map Lotus --export <private-export-directory> `
  --windows <private-scene-context.json> --height <exact-accepted-height.npz> `
  --out <new-private-report-directory>
```

Scene context must contain the matching `source_sha256` and ordered absolute-millisecond `windows`.
An audit always reports release gates; a local height file does not certify live acceptance. Generate
the tagger from the same candidate and exact height context using `control_tagger.py --tags ... --out ...`.
Backend diagnostics, rather than the approximate canvas preview, qualify explicit rope bindings.

Rope coordinate measurement accepts a matching recording, export and scene context:

```powershell
.\.venv313\Scripts\python.exe scripts\measure_rope_positions.py `
  --tags <private-candidate.json> --map Split --export <private-export-directory> `
  --source <local-recording.vrf> --context <private-scene-context.json> `
  --out <new-private-coordinate-directory>
```

It requires nonoverlapping absolute round windows and unambiguous pawn/slot identities. Review the
proposal and stationary boarding evidence separately before updating an immutable private candidate.

Read-only rollout planning uses the existing guarded DB wrapper and `compute_control.py --dry-run`,
optionally narrowed with `--map`, `--match` and `--round`. Write/reparse commands are the owner's
approved rollout step. The existing archive reparse and worker runbooks in `webapp/RENDER_DEPLOY.md`
describe capabilities and status. This change does not start or change those schedulers.

## Verification and rollback

The earlier terrain-review follow-up used height rules 4. Seven private candidates were rebuilt from 619
frozen rounds, with independent structural checks and private graph audits. Sunset, Split, Haven
and Lotus passed the combined local height and sight gates; Ascent, Summit and Abyss failed then.
The rules-5 Ascent and Summit results above supersede those two historical failures; Abyss remains
unqualified.
Large inferred return descents now distinguish falls from grounded slides, and sampled ambiguous
parents retain finer children. This does not qualify pending feature geometry or activate inputs.
See [TERRAIN-REVIEW.md](TERRAIN-REVIEW.md) for the audit tool, evidence limits and current gates.
The real Haven probe found retained removed-player membership in reused fill components. The fast
counterfactual now updates that membership while retaining the geometry; control revision 14 marks
older calculations stale. A toy entry/teammate regression and the isolated real failure agree with
the full calculation after the fix. Height rule changes do not replace this control invalidation.
Earlier baseline results elsewhere in this runbook predate these rules-4 candidates.

Local tests cover exact/reset/ambiguous source handling, opaque destruction property framing, global
Lotus motion time, majority-area rotating cells, exact half-cell ties, thin panel edges, rotated area
conservation, one-cell traversal and release-time lower
bounds, Summit phase boundaries, continuous passage status, measured landing rebasing,
directional seconds and parent attribution, quiet-only cuts, artifact verification, and synthetic fresh
full/fresh gaps/cached gaps parity. Existing Ascent integration and schema/reference suites also pass.
These results do not certify real map placement or the Linux worker; those remain gates above.

The Sunset follow-up passed 249 decoder, Ascent compatibility, runtime, condenser, recipe, artifact, cache and control
format tests. The moving-door integration verifies closed blocking, opening movement release at
the reviewed cutoff, and complete sight/movement removal after the witnessed open completion.
The raw parser pin is unchanged: its existing capture already included the required state property.
Condenser 17 requires fresh condensation; consumer 5 invalidates affected feature identities.
No archive reparse, live activation or recompute was performed during these local checks.

Additional mixed-grid regressions cover exact footprint lookup, sight and topology, corner cuts,
physical travel time and area, section-clock calculations, candidate geometry rebuilding, version-2
compatibility, receipt tampering, missing history, rejected-build history recovery, and independent
service integrity. Focused local suites passed, including 143 runtime/height/source tests, 110 worker/
SQLite/artifact tests, and 125 control/storage tests (one skipped). These overlapping suite totals
are not a unique test count. Docker is unavailable on this machine, so the Linux rehearsal remains
unperformed. No scheduler, publication, activation or live recompute was started.

The real Lotus trial exposed repeated dense sight masks in tick-cache observations. Panel sampling
now reuses fixed-state masks and exact quantized rotating poses; descending panels reuse XY while
preserving each observation's changing vertical band. An independent check of all 612 analytical
instants in a real round compared 1,836 occluders exactly with uncached resolution. Retained masks
fell from 1,925,185,536 to 314,572,800 bytes. This measures mask retention only, not total worker
memory or Linux acceptance. The baseline full-round run predates this allocation optimization.

Two baseline real Lotus rounds completed with 1,006 viewer frames and 1,356 analytical instants.
All 106 fresh gap rows matched tick-cache replay and all 129 sampled full counterfactual checks
matched incremental results exactly. The second scene includes four door activations and breakable
destruction. Knowledge layers were excluded. The 1,846- and 2,079-second baselines include fresh
and cached gap detection and predate the allocation/culling optimization; they are not production
worker timings or cold-cache measurements. Barrier-start paint and
unresolved terrain fallback remain input warnings. A false height-domain warning caused by cleared
refined parents was corrected to count actual undeclared child holes, with a regression retaining
real missing-child warnings. The second round also reports a nearsight duration placeholder.
Parity alone does not certify those remaining input warnings.

Conservative sight-line culling uses compiler-owned panel pixel bounds to exclude impossible
crossings before detailed sampling. It retains the original longest-target sampling interval;
unbounded directly constructed masks retain the reference path. Random thin/vertical/clipped
fixtures and the motion/runtime suite pass (147 tests). A reduced real window compared every one
of 17,802 calls, covering 704,777 target lines and diagnostic counters, with the unculled reference
and found no differences. Reduced-window profiling is not an archive recompute or Linux benchmark.

Retain prior code/parser recipe, tags, accepted height asset digests and immutable feature artifacts.
If a canary fails, stop expanding the recompute and hold/disable the affected bundle rather than
certifying stale results. Restore the prior approved runtime inputs/code through the owner-controlled
release path, reparse on its recipe where necessary and recompute those rounds against the restored
artifact identity. Never rewrite a previous artifact or bypass a height rejection to force rollback.
