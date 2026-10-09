# Sliding-door starting point

The owner checked full door closure in game and reported **5 seconds** on 2026-10-09. Treat that as an
owner observation for the Ascent closing behavior being discussed, not a parser-derived timestamp. Opening
duration, trigger delay, travel direction, reversing behavior and the second door's independent timing still
need observation. The earlier drafts' 3-second duration is superseded for the checked closing transition.

## Geometry and timing

Use a single rigid panel translating at constant speed. An optional `feature.sliding` record contains
`open_state`, `closed_state` and an `open_center` point in the same minimap u/v coordinates as other drawings.
The closed state's polygon or polyline footprint supplies the panel's closed pose **and the doorway
aperture**. Its centre is the arithmetic mean of its drawn vertices, shown as a yellow marker. The owner
places where that marker travels when fully open. Do not infer that location from the minimap or invent a
direction. Closed sight bounds still describe the vertical extent of the panel.

At closure fraction c, translate every panel vertex by `(open_center - closed_center) * (1 - c)`.
Rasterize that translated panel on the existing 256x256 paint grid and intersect it with the doorway mask.
Thus the panel can retreat behind a wall without requiring walkable ground there. Coverage changes in raster
steps rather than three arbitrary poses. Open and closed are 0 and 1. For a 5-second closing transition
starting at t0, c is clamped `(t-t0)/5`. Opening uses its own authored duration. This starter handles one
panel, not two independently moving leaves, vertical dropping, rotation, or material penetration.

The reducer's new `evaluate` query returns its full state and unchanged event trace. Sampling its retained
motion handles ordinary completion, stale completion events, mid-motion reversal, destruction and reset
between replay events. The existing reversal policy uses the interrupted transition's speed and virtual
start time; it does not assume a newly measured opening speed. A restart retains the existing restart
semantics, which may jump to the source pose; do not select restart unless that behavior is observed.
Observed opening/closing without a known start/duration yields unknown progress, not a guessed halfway pose.

## What is built

- Python and JavaScript pose, clipped coverage and closure-at-time helpers, checked for parity.
- A dormant Python sampler producing movement node blocks and bounded sight occluders. It verifies the
  full aperture's placement, uses the closed panel's vertical bounds, and leaves unknown vision pending.
- Tagger controls to enable sliding, choose state endpoints, place the open panel centre and scrub closure.
  Known duration gives an elapsed-seconds label. Switch/advance/reverse/break/reset in the sandbox use the
  same sampled geometry; explicit scrubbing is cleared when sandbox events or model edits occur.
- A warning when the open pose still covers the doorway. Separate closed sight edges currently prevent
  sliding preview: the owner must resolve their intended role explicitly, not have them silently deleted.
- A publication gate: any `sliding` feature remains pending until a runtime motion consumer is implemented.
  Compiler version 3 records this stricter gate; version 2 archives are rejected by the existing unsupported
  compiler path rather than replayed with different semantics. Runtime consumers remain unregistered.

This is authoring and consumer groundwork. The live replay engines do not yet sample these effects at each
tick, and the existing static composed overlay is separate from the red sliding coverage preview. Moving
states still show the compiler's existing missing-static-footprint diagnostics; copying a closed footprint
into them does **not** activate or replace the runtime motion gate. No scoring, database, worker activation,
deployment or main-branch change is included.

## Validation

The ownership run covering motion, tagger, diagnostics, inputs and artifacts passed 125 tests (98.61 s).
The separate reducer/schema/control/verification/job/transport/acceptance run passed 111 tests (369.94 s).
The final expanded motion suite passed 20 tests: linear five-second closure, narrowing sight lines, ground
and world bounds, whole-aperture placement, reversals, destruction/reset, unresolved inputs, Python/JS
parity, publication gating and real tagger-control harness interactions. Node syntax checks and diff
whitespace checks passed. The prepared private page preserves the old page and all unrelated feature/map
records exactly; the checked closing transition is 5 s, open centre remains unresolved, and all three
features remain pending in flat diagnostics. No native browser layout or real replay activation was verified.

## Owner walkthrough

1. Export/download any newer draft from the current page before using the refreshed copy. Import that draft
   if it has edits newer than the supplied source; cross-source drafts require the existing explicit restore.
2. Select the checked door. Enable **linear sliding preview**. In Behaviour, set its transition to `closed`
   to **5 s**. Keep the opening value separate until checked.
3. Use its closed footprint to outline the whole panel across the doorway. A zero-area drawing cannot
   represent a moving panel. Resolve any extra closed sight edge explicitly using its existing Remove control
   if it was only a redundant outline. Do not remove a separate physical blocker without understanding it.
4. Choose **Place open panel centre** and click where the yellow centre marker would move when fully open.
   Move it farther if the warning says the panel still covers the doorway. At 0% the red coverage should be
   empty; at 50% the gap should match a 2.5-second in-game checkpoint; at 100% it should cover the doorway.
5. Press **Follow sandbox clock**, reset, switch and advance by 2.5 seconds. Check halfway geometry. Reverse
   at halfway only if that behavior exists; break and reset to check disappearance and restoration.
6. Set verified ground/world sight bounds, then repeat on a measured height asset to test seeing over/under.
   A flat preview cannot establish vertical behavior. Save the draft/export before closing the page.

## Next implementation boundary

After panel geometry and replay timing evidence are reviewed, give moving states a compiled motion descriptor
and wire the dormant sampler into each required engine consumer at replay timestamps. Reuse downstream
visibility/path calculations while the sampled raster mask and bounds remain unchanged; this starter does
not yet provide that caching or activate a consumer. Rehearse cold archives, height rebuilds and event timing
before registering a versioned consumer. Keep bullet penetration a separate capability.
