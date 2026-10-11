# Automatic local height refinement

Status: owner-selected approach; runtime implementation remains to be built. Supersedes the
optional owner-paint step in the Lotus resolution proposal. Applies to every map, with no
map-name exceptions. Main, deployment and live height activation require the owner's approval.

## Problem and settled behavior

The existing 128 x 128 movement/height grid uses 8-pixel cells on a 1024-pixel minimap. A cell
can straddle a ledge, slope or small box. Collapsing supported samples to whole-cell heights
can merge nearby surfaces or represent side-by-side surfaces as stacked floors. Additional
replays improve support but do not restore the XY distinctions discarded during binning.

Keep reliable 8-pixel cells. Automatically divide eligible ambiguous cells into four
4-pixel children. Preserve the finer XY evidence and use each child's actual footprint in
sight and traversal. No painted guide or owner action is required. Do not introduce 2-pixel
refinement in this implementation: the local diagnostic showed excessive sample sparsity.

This decision does not enable an adaptive runtime yet. Reporting subdivisions without
consuming them in the calculation engine does not satisfy the feature.

## Candidate selection on every rebuild

Use the existing accepted stand/walk extraction, including jump, flight, movement-ability and
temporary-platform exclusions. Retain per-cell XY samples of those accepted runs; a run's
map-wide mean position cannot identify which side of a boundary its samples occupied.

Candidate parents include directly sampled cells refused for conflicting levels, excessive
height spread or too many floors. Evaluate supported multi-floor cells as candidates too:
some may be ledge edges rather than stacked passages. Keep a reliable parent coarse when
the finer evidence does not reveal a spatial distinction. Preserve genuine stacked floors
when multiple supported heights occur in the same child footprint; do not assume every
conflict is a ledge.

Neighbour disagreement alone is not proof of an internal boundary. It may suggest a candidate
where accepted samples within the parent show mixed elevations. No samples or insufficient
support alone do not trigger splitting: refinement would create more empty bins.

Once a parent is selected for refinement, retain that decision across subsequent builds and
add newly qualifying parents automatically. Carry the prior refinement set as an explicit,
hashed per-map build input, not hidden worker-local state. Reconstruct it from archived inputs
if the worker cache is lost. Start an intentional policy reset explicitly if the selection
algorithm changes; do not silently depend on whichever previous build happens to be nearby.

## Child evidence and unknown regions

Count one observation per accepted run and child, never one vote per replay frame. Keep the
existing independent-round/match support requirements. Each child records supported floors,
spread, evidence kind and refusal reason. More replays can resolve unknown children without
changing their footprint. Do not copy a parent's conflicting floor into every child.

Apply existing bounded flat/gradient fill rules at the appropriate spatial scale. Distances
and slope limits remain physical quantities; a fixed number of smaller cells must not change
the allowed rise or reach. Filling cannot invent a connection across an unseen drop, connect
the bottom and top of a box, or erase a genuine lower passage. Unsupported children retain
the documented unknown fallback and remain visible in diagnostics and release checks.

## Runtime contract

Represent a floor node by its spatial footprint, height and valid neighbouring transitions.
A reliable parent retains its coarse footprint; a refined parent uses child footprints.
Position lookup selects the containing child and a compatible floor there, including at
coarse/fine boundaries. Fine children do not masquerade as four whole-area votes.

Sight samples the surface at the ray's actual XY intersection. An upper surface occupies
only its measured child footprint, rather than becoming a plate across the whole parent.
Movement connects touching footprints through qualified steps, ramps and drops. Distances
and travel times use physical positions. Coarse/fine borders and diagonals must not allow
corner cuts or free height changes.

Control coverage, uncertainty, supported share and unresolved-area sizes use physical area.
Splitting one cell into four cannot quadruple its importance. Translate the existing maximum
unresolved-area threshold into the same physical parent-cell area; do not raise its allowance.
Neighbours touching a multi-floor region are tested geometrically across resolution levels.
Keep the existing kill-line and must-block checks unchanged.

Feature placement, door masks, rope landing selection, barriers, utilities, trips and gaps
must consume the same spatial nodes. Existing whole-cell assumptions are audited explicitly;
changing only the floor builder would leave calculations inconsistent. Site heatmaps may
aggregate to the existing display grid, but computation and cached gaps retain fine identity.

## Persistence and release

Extend the versioned height asset with refinement selection, child floors/footprints,
support/refusal metadata and connections. Hash every runtime-relevant value. Preserve
loading/verifying archived coarse assets under their recorded format and rules. Bump the
height format/rules, control and dependent feature-consumer identities when the new runtime
lands. Ordinary code deployment must not reinterpret old cached results as refined results.

Use the existing opaque height/artifact storage; no new SQL table or migration is proposed.
Carry refinement history in immutable build inputs and receipts. Integrate automatic selection
with the worker rebuild recipe so additional data updates existing children and newly
ambiguous regions without owner retagging. Rejected assets remain ineligible for activation.

## Implementation and acceptance sequence

1. Preserve accepted per-cell XY evidence and implement a deterministic, map-independent
   selection/rebinning module. Test with separated ledge samples, genuine stacked floors,
   ramps, jumps/temporary platforms, sparse samples and repeated input order.
2. Extend asset serialization, hashing and archived-version verification. Test round trips,
   tampering, missing history, and deterministic growth of the refinement selection.
3. Integrate mixed footprints into node lookup, sight and topology. Verify heights and routes
   at coarse/fine boundaries; preserve physical travel costs and area-weighted metrics.
4. Update all feature/utility consumers and diagnostics. Show coarse and fine footprints,
   evidence quality and remaining uncertainty without treating a proposed split as resolved.
5. Rebuild private candidates on all available maps using frozen input identities. Compare
   supported area, conflicting area, legitimate walking, kill-line visibility, memory and
   run time. Establish exact latest-build evidence separately from older local probes.
6. Qualify a new height candidate through unchanged acceptance checks, then prepare its
   owner review. Resume the deferred switch, rope and real control/gaps walkthrough after
   the height work is settled. No merge, live activation or recompute is authorized here.

## Evidence supporting the starting resolution

A private diagnostic using 61 frozen local Lotus rounds re-binned accepted ground runs while
holding support requirements constant. Within four conflicting coarse cells, 4-pixel children
resolved 56.25% of the area, left 31.25% insufficiently supported and 12.5% conflicting.
At 2 pixels, 29.69% was supported and 70.31% lacked evidence. This is an earlier local input
set, not qualification of the latest rejected height asset or a guarantee for other maps.
