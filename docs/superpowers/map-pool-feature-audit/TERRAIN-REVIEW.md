# Terrain and quiet movement follow-up

Owner terrain labels identify boxes, ledges, stairs, covered ground and noisy or quiet movement.
They are qualitative evidence. They do not supply a measured elevation or an exact transition mask.
Keep the drawings, region scopes and recording identities outside the public repository.

Height rules 4 make two changes. Ambiguous cells with accepted ground footprints retain 4-pixel
children even when some children are not yet supported; entirely unsampled cells are not invented,
and reliable stacked floors remain intact. An observed climb grants a return descent, but that
descent is a fall unless ground evidence supports a slide. A fall above the existing silent-drop
limit stays closed to quiet unknown spread. Grounded slopes remain quiet. This applies in both
coarse and mixed-footprint builds, without changing archived assets or the asset byte format.

The height input manifest includes the rule revision. Rules 4 therefore require new builds; a page
reload does not reinterpret a stored asset. Normal verification and activation gates still apply.

Current height rules are 5. Physical kill-line checks now accept replay tracks without team labels,
while normal control calculations still require team membership. Smoke and wall exclusions are
unchanged. Required blocked views can be proved by an exact permanent 2D wall even when endpoint
heights are absent; clear 2D views with absent heights still fail as unchecked. No elevation is invented.

The real-round checks also exposed stale player membership in a reused control fill. Removing a
player can leave the same reachable area, but the remaining entry must choose its path back using
the surviving teammates. The fast counterfactual now retains the area and updates its membership,
matching the full calculation. Control revision 14 invalidates earlier control/gap inputs; it does
not alter Impact scoring or the stored byte format.

## Read-only route audit

`scripts/audit_terrain_routes.py` attaches an explicitly pinned height candidate to the permanent
map domain and reports graph facts for private owner regions. It never grants routes, changes heights,
activates assets, writes a database or certifies a map for release. The input JSON has this structure:

```json
{
  "map": "MapName",
  "regions": [
    {"id": "1", "bbox_px": [200, 200, 208, 208], "description": "Owner terrain description"}
  ]
}
```

The example coordinates are a toy footprint. Real coordinates use the 1024-pixel minimap. Bounds
are approximate review scopes; the default 16-pixel context is explicit in the report.

```powershell
.\.venv313\Scripts\python.exe scripts\audit_terrain_routes.py `
  --height <private-candidate.height.npz> --height-sha <exact-candidate-digest> `
  --regions <private-regions.json> --out <new-private-report-directory>
```

The report records both input byte hashes, unresolved ground and routes, multiple-floor footprints,
noisy falls, grounded descents and large quiet STEP descents that need review. Unresolved area is also
reported in pixels so four finer unknown nodes are not mistaken for four times the missing area.
A noisy direct drop can have a quiet alternative through steps or landings. The audit checks for
that alternative over known ground only; it excludes unknown fallback. This is graph reachability,
not proof of a particular owner-colored route or its travel time.

## Local verification and remaining gates

Seven private candidates were rebuilt from 619 frozen rounds. Split and Abyss use separately frozen
read-only snapshots, so changes against their earlier saved assets include additional data as well as
the new rules. All seven serialized assets passed independent structural verification.

The final control/unknown/engine/figures/storage/gaps suite passed 256 tests with one skipped.
The terrain audit has 11 passing cases, including unknown fallback exclusion and a quiet alternative
through known landings. Height/refinement/motion checks and worker/SQLite integrations also pass;
their suite totals overlap rather than representing independent unique counts.

In that earlier snapshot, Sunset, Split, Haven and Lotus passed their combined local ground, kill-line
and must-block checks. Ascent failed kill-line and must-block checks; Summit lacked supported ground.
Abyss still fails the unresolved-overlap and kill-line checks. A successful calculation or gap-cache
comparison does not clear these failures, qualify pending rope landings or complete the Linux worker
rehearsal. Owner-colored regions that lack exact numeric attribution remain qualitative.

Private receipts retain per-map hashes, region-level before/after graph facts, source snapshots,
pending feature placements and fresh calculation checks. Do not publish those receipts. Use the
release checklist in [RELEASE.md](RELEASE.md) before any activation or archive recompute.

The subsequent read-only snapshot contains 131 Ascent rounds and 56 Summit rounds. Summit now
passes with the additional evidence and team-independent physical validation. The owner reconfirmed
all three Ascent required views as blocked and marked the permanent wall corner crossed by the first.
A private two-cell cover-paint correction adds 24 blocking pixels on that wall outline. Rebuilding
against that exact domain passes all three required views, supports 74.7% of walkable ground, and
leaves the kill-line result unchanged at 16 of 863 (1.85%, below the unchanged 2% bar). This qualifies
the private height candidate only; the corrected masks and pending features have not been published.
