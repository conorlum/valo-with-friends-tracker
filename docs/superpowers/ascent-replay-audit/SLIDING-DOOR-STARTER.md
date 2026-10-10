# Sliding-door starting point

The owner reported a **5-second full closing duration** on 2026-10-09, then confirmed that **both Ascent doors descend from above**, with a fully open gap of **about 1.5 player heights**. These are owner observations, not parser-derived measurements. The prepared local starter applies the reported closing duration to both doors. Opening speed, switch delay, reversal behavior, exact metric heights and passage thresholds still need evidence; the older authored opening durations and policies are preserved.

## Descending panel geometry

Ascent uses `feature.sliding.axis = "vertical"`. The optional record names its open/closed states and contains:

- `open_clearance`: a typed metre value, initially unresolved. The owner's approximation is retained as `estimate_player_heights: 1.5` with a note. That estimate never resolves the metre value.
- `movement_clearance`: the verified minimum passage clearance in metres, initially unresolved. It needs an explicit movement/stance policy; the starter does not invent standing or crouching thresholds.

The closed state's footprint is the fixed doorway/panel projection. Its **ground-relative** sight bounds give the closed panel's bottom B and top T. Do not use all-height bounds for a descending panel: seeing under it requires a finite vertical interval. For closure c, lower the panel by sampling `offset = (open_clearance - B) * (1 - c)` and block the interval from `min(B + offset, T)` to T. The top is clipped to the authored doorway, so material raised above it cannot block that opening. A zero-width interval blocks nothing. All extra closed sight edges retain their geometry and authored bounds and move by the same vertical offset, each clipped to its own closed top. Their open bands must clear fully.

For the 5-second close, c is clamped `(t-t0)/5`. The map footprint remains fixed at every phase; it is the height band that changes. Ground-relative bands resolve against the measured floor under that doorway. The sampler refuses to claim height-aware sight on a flat map. Unknown heights produce pending results, not guessed all-height blockers. Partial movement remains pending until its clearance threshold is known; fully open and fully closed use their endpoint movement behavior.

## Preview and timing

The tagger shows **descends from above** as the motion direction and a side-view diagram. With only the owner's approximation, the diagram shows a proportional sketch in player heights: approximately 1.5 at open, 0.75 halfway and 0 at closed. It clearly states that metres remain unresolved. With verified metric bounds it shows the sampled clearance in metres instead. This diagram is not evidence of visibility or traversal and cannot substitute for a real replay/height rehearsal.

The minimap outline stays fixed. The static flat composed overlay excludes sliding models, so it cannot misrepresent a partly lowered door as a full planar wall. Changing motion direction is explicit; newly enabled sliding models start with direction unresolved. Existing horizontal drafts without an axis keep their original horizontal interpretation, preserving entered centres and other unknown fields.

The reducer's `evaluate` query returns its full state and unchanged event trace. Sampling retained motion supports completion, stale events, reversal, destruction and reset between events. The existing reversal policy retains the interrupted transition's speed and a virtual start time. It does not verify real reversal behavior or substitute for a separately measured opening speed. An observed moving state without a known start/duration remains unresolved. Restart retains its existing source-pose restart semantics and should only be selected if observed. Explicit scrubbing is cleared on model edits, sandbox events and map changes.

## Horizontal models

Generic models can explicitly choose `axis = "horizontal"`, an `open_center` point and the closed footprint. Translate the panel from that centre to its closed centre (the arithmetic mean of drawn vertices), rasterize on the existing paint grid and clip to the closed doorway aperture. This produces raster-sized steps in planar coverage. The owner supplies travel direction/position; the tool does not infer it. Separate closed sight edges remain unsupported for this horizontal preview and are never silently removed.

## Runtime boundary (initial preview phase)

The boundary below records the initial dormant implementation. The branch now implements the qualified
Ascent replay consumer, while the owner's metric annotations remain unresolved. See
[site integration](SITE-INTEGRATION.md) for current behavior and release steps.

Python/JavaScript helpers and the dormant sampler are built; the live replay engines do not call them yet. Every sliding definition remains gated from publication until a runtime motion consumer exists. Compiler version 3 records that gate; older version 2 archives use the existing unsupported-compiler path rather than being replayed under changed semantics. Runtime consumer registries remain empty. Static moving-state footprint diagnostics may still appear; copying a closed footprint into them does not replace this gate.

After geometry and timing evidence are reviewed, compile a motion descriptor and wire sampling into each required engine consumer at replay timestamps. For a vertical door, visibility caching must include the sampled height band as well as its fixed planar mask. A fixed footprint alone is not a cache hit. Rehearse cold archives, height rebuilds, observed/unknown transitions and tick timing before registering a consumer. Bullet penetration remains separate. No schema, scoring, main, deployment or runtime activation is included.

## Owner walkthrough

1. Download/export any newer draft from the current page before switching to the separate vertical preview. Import it if it has newer edits; choose the vertical direction for both doors and retain the five-second closing value after restoring an older draft. Do not clear browser storage.
2. Select Market Door or Garden Door. Under Sliding panel, confirm **descends from above**. The approximate open clearance is 1.5 player heights and the metric clearance remains unresolved.
3. Scrub 0%, 50%, 100%. Check the side view against in-game checkpoints at 0, 2.5 and 5 seconds. The outline must not move sideways. Market's extra sight edge is preserved; it still needs its own verified bounds.
4. Once measured, enter the fully open clearance in metres and ground-relative closed bottom/top bounds. Supply a passage threshold only after deciding/observing the movement policy. No extra open map point is required for these descending doors.
5. Follow the sandbox clock, switch, advance, then check reversal only if supported in game. Break/reset should remove/restore the panel. Sandbox success is simulated behavior, not replay verification.
6. With a measured height asset, test rays below, through and above the panel, and independently verify passage. Audit real replay events before connecting runtime consumers. Save before closing the page.

## Validation

Horizontal groundwork passed its 20-test motion suite plus the owning tagger/diagnostic/archive checks and reducer/control/worker boundary checks. The vertical correction adds tests for fixed planar geometry, five-second descending bands, progressively blocked elevations, ground bounds, partial-movement uncertainty, flat-map refusal, extra sight edges, player-height estimates, invalid units/inputs, Python/JS parity and the side-view UI. Native browser layout and real Ascent replay activation remain unverified.

The vertical ownership run passed 209 tests (158.13 s); the final horizontal/vertical motion run passed 37 tests (2.74 s), including floor-elevation changes after a height rebuild. The prepared private page preserves both old pages and all owner-drawn shapes, with both doors marked descending, the reported five-second closing duration, approximate 1.5-player-height clearance and unresolved metres.
