# Review of the control-heights implementation plan (2026-10-02)

Reviewer: a fresh subagent with the plan, the spec, the register and the code, and none of the author's
context. It read only; nothing was run. Findings are the reviewer's; "Applied" is the author's answer.
The plan (`2026-10-02-control-heights-impl.md`) is revised in place.

Verdict: 1 blocker, 8 should-fixes, 7 nits. All applied; none rejected. No open blocker.

## Blocker

**1. `NodeTopology` has no walking rule for cells without a floor.** The plan built the graph only from the
asset's floor-to-floor edges, while the bar accepts a map at 60% supported. Today every walk-mask cell is
connected (`engine.py` `_pocket`, `fill`, `_flood`, `_spread`), so the unknown, fills, backfill and
`dist_from` could not cross an unresolved cell, and no test would catch it. Also unstated: diagonal edges,
and what happens when the asset's `walk_sha` differs from the current walk mask.

Applied: an unresolved or floorless walkable cell is one node with today's 2D links (8 neighbours, the
diagonal flag from the cells' positions) to every floor of each walkable neighbour, both ways. The build
emits edges for all 8 neighbours. A walkable cell the asset doesn't know (the walk mask changed since the
build) is treated as unresolved and counted in `missing_inputs`; `build_control_geometry.py` prints a
`WARNING` when a map's `walk_sha` no longer matches its asset's. The reviewer suggested refusing the asset
instead; the spec's own rule is that uncertain terrain falls back to 2D locally and loudly, and a refusal
would fail every round of the map after any paint change, so the fallback is kept and flagged (it is on the
grouped approval card). W12 gains a toy test where the unknown crosses an unresolved strip.

## Should-fix

**2. W12-W16 checks did not prove the flat path unchanged.** The toys have no barrier, so `barrier_start` and
`_share_by_walk` were never in the reference, and the real-round comparison ran only in W11.
Applied: every check from W11 to W16 runs `pytest tests/replays -k control` and the `REFERENCE-REAL.json`
comparison. W9's toy set gains a barrier, an area watcher, a camera and a drone.

**3. The PINNED constants digest would break mid-run or miss the height constants.**
Applied: `CONTROL_REVISION = 4` moves to W10 (the reference normalises the revision); `_constants_digest`
also hashes `app.control.heights` and `app.control.topology`; `PINNED[4]` is re-pinned in place at each
step that changes a constant (revision 4 is unreleased).

**4. Cell-indexed code was not enumerated for the node switch.** Applied: the plan's W12 lists the two
rules and the sites. Holder's own node: `Tick.live`, `sees`, `_live_of`, `unknown_without`, the way back,
`Knowledge.seen_now`/`tick_for`/`start`, `Unknown.apply` and `_drop_pieces`, `barrier_start`'s starts.
Every floor of a cell: barrier paint, `Unknown.sealed`'s pinches, smokes and damage zones (2D columns),
specials (`special_links`), `cant_walk_paint`.

**5. "`los` agrees with `cast` on every toy" is unlikely to hold** (cast marks any cell a ray sample lands in,
with wall tolerance). Applied: W10 asserts `los` clear implies `cast` seen, and equality only on wall-free
height toys.

**6. The ray test left three cases open.** Applied: only the viewer's own node of its own cell is seen (a
bridge viewer doesn't see the tunnel node beneath); slabs are blocked slope intervals per ray, not one
horizon; a holder below every floor of its cell takes the lowest floor. W12 gains the same-cell tunnel test.

**7. Module layering was circular.** Applied: `app/control/heights.py` holds only the constants, the asset
and its I/O (numpy, no `app.control` imports); `app/control/height_build.py` (stands, floors, fill,
connections, report, kill lines, must-block) sits above `geometry` and `engine`.

**8. The cherry-pick was not conflict-free as claimed** (`test_replay_viewer.py` differs between the bases),
and the p2 gate skipped `test_ingest_replay.py`. Applied: W5 doesn't touch `test_replay_viewer.py` (its
existing decoder test now runs on blobs that carry z, since the synthetic match has heights); p2 is gated
on the whole of `pytest tests/replays` with the base's known failure deselected.

**9. Tier-2 flags from the register were missing from the steps**, and the collapse rule was undefined for
three floors, for `NONE` against a held floor, and for `knew_states`. Applied: the rule is defined (below)
and W6 (platform list), W12 (collapse rule) and W13 (Safe shortcut) each name their D entry.

## Nits (all applied)

- The known failure is named in the checks.
- The real flat round is named: `%TEMP%\valo-replay\6f12db3e-b2db-4bca-96e4-a837c85ba5a6-current`.
- "Floors reached only through the air are listed in the report" gets a test in W7.
- W9 hashes the decompressed payload, not the gzip bytes.
- If z pushes a round over the 70 KB budget it is reported in `LOTUS.md`; the budget is not raised.
- W14's files include `path_at` (it returns the drone's z).
- `scripts/ingest_replay.py`'s inline decoder ignores `z` (checked by reading; its preview test runs on z blobs).
