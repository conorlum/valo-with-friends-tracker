# Map-pool gimmick release preparation

The branch now connects bounded native replay messages to the reviewed panel state machines, adds
timed quiet rope arcs on measured floor nodes, and provides computation-derived site status. The
private owner candidate is not yet qualified for live activation. Main, deployed tags, live height
activation and live recompute have not been changed by this work.

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

Versions: feature compiler 8, condenser 16, control 12; Ascent consumer 2, quiet rope consumer 1,
map-pool replay consumer 3. Gaps version stays 3; changed control inputs invalidate its caches.
No new migration or database table is required.

## Current real evidence and blockers

Private native captures were rebuilt on the new parser pin and checked against recording hashes.
All four captures frame without errors. Summit's three selected doors bind in 19 rounds, Abyss's two
breakables in 23, and the selected Lotus switches/breakable in 24. These counts establish bindings for
the selected source actors, not comprehensive instance or second-button coverage. Sunset binds 18 of
20 rounds; missing transition parameters hold the remaining two. Missing/default open parameters
must be qualified from source framing before assigning an open state.

The read-only live height inventory found active assets for Split, Sunset and Haven. Ascent, Summit,
Abyss and Lotus currently have rejected assets and no active selection. Their rejection reasons include
insufficient supported area, unresolved areas near multiple floors and blocked known kill lines.
Rejected assets were exported privately for diagnosis; they are ineligible for release. Do not weaken
the acceptance checks or activate them to make feature placement pass.

Current geometry checks keep Summit's four, Lotus's three, Sunset's one and four of Abyss's six tagged
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
source actors still need binding. The 2.75-second passage observation belongs to the old eight-second
preview; it is retained as an angle reference, not imposed as a cutoff in the corrected ten-second model.
Panel depth and angular speed remain model estimates pending real-scene comparison.

## Steps to finish, in order

1. **Freeze private evidence.** Keep the latest combined owner snapshot, original recordings, parser
   build receipt and source hashes. Never substitute an older per-map export. Stage revisions in a new
   private candidate. Verify exact actor names in another independent recording when available; an
   instance-specific name in one replay is not evidence of a universal map binding. Confirm both
   physical Lotus switches or prove they share the same native door-level activation source.
2. **Resolve geometry with the owner.** Lotus doorway spaces and center pivots are reviewed. Next,
   measure the physical rotating panel and resolve vertical bounds. For descending doors, measure ground
   relative clearance/bottom/top from replay/world evidence. For ropes, identify actual boarding
   coordinates and distinct replay position-Z floors; bind the named Choke 2 side for Abyss Mid.
   Preserve physical directions while applying the owner's quiet times and one-way restriction.
3. **Repair height evidence.** Diagnose failed global gates using source coverage and known kill lines.
   The owner selected automatic local 4-pixel refinement for mixed-height cells on every map, without
   painted guides. See `../specs/2026-10-10-automatic-local-height-refinement.md`: this is settled design,
   and the mixed-resolution builder/runtime still needs implementation. Keep sparse regions explicitly
   unknown, genuine stacked floors distinct, and acceptance metrics weighted by physical area.
   Build fresh local candidates and rerun the unchanged checks. Use only accepted exact assets in final
   qualification. Rebuild-dependent floor references are verified again; do not retain stale node IDs.
4. **Qualify private enabled bundles.** Separate panel and rope consumers. Resolve permanent-domain
   corrections and trigger interaction ground. Run source, placement, bounds and artifact verification
   for every intended member. Check local rope bypasses and alternate legitimate walking paths. No
   missing member may disappear under a map-level success label. Haven remains the reviewed baseline.
5. **Compute real acceptance scenes.** On each qualified map run full control, then fresh gaps-only and
   cached gaps with identical recording, height, tags and compiler identity. Compare bytes/outputs and
   replay-visible timestamps. Test interruption/destruction, fully closed persistence, reopening,
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

Read-only rollout planning uses the existing guarded DB wrapper and `compute_control.py --dry-run`,
optionally narrowed with `--map`, `--match` and `--round`. Write/reparse commands are the owner's
approved rollout step. The existing archive reparse and worker runbooks in `webapp/RENDER_DEPLOY.md`
describe capabilities and status. This change does not start or change those schedulers.

## Verification and rollback

Local tests cover exact/reset/ambiguous source handling, opaque destruction property framing, global
Lotus motion time, majority-area rotating cells, exact half-cell ties, thin panel edges, rotated area
conservation, one-cell traversal and release-time lower
bounds, Summit phase boundaries, continuous passage status, measured landing rebasing,
directional seconds and parent attribution, quiet-only cuts, artifact verification, and synthetic fresh
full/fresh gaps/cached gaps parity. Existing Ascent integration and schema/reference suites also pass.
These results do not certify real map placement or the Linux worker; those remain gates above.

Retain prior code/parser recipe, tags, accepted height asset digests and immutable feature artifacts.
If a canary fails, stop expanding the recompute and hold/disable the affected bundle rather than
certifying stale results. Restore the prior approved runtime inputs/code through the owner-controlled
release path, reparse on its recipe where necessary and recompute those rounds against the restored
artifact identity. Never rewrite a previous artifact or bypass a height rejection to force rollback.
