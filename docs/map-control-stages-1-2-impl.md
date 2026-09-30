# Map control, Stages 1 and 2: implementation plan

The design is `docs/replay-map-control-plan.md` (Q1–Q75, all settled). This file turns its Stage 1 (map geometry
assets) and Stage 2 (control engine) into ordered steps, plus the condenser inputs the engine needs (plan: "Inputs
the blob lacks"). Stage 3 and later (migration, `replay_round_control`, the command, the endpoint, the viewer layer)
are out of scope. Nothing here touches Impact scoring (`app/scoring/`) or any database.

Scope answers fixed before this plan (AFK register, 2026-09-29):

- **R1.** This build also extracts the missing inputs in the condenser and bumps `CONDENSE_REVISION` once. The
  engine uses the new fields and falls back to Stage 0b's placeholders on older blobs. The prod re-ingest is the
  user's.
- **R2.** Only small per-map assets are committed (masks, tags, specials, the badge flag, a manifest). The
  cell-to-cell visibility bitsets (about 11 MB a map) are built on demand into a gitignored local cache, keyed by
  a hash of the masks and the build parameters.
- **R3.** scipy is pinned in `requirements.txt` at the installed version. The web app never imports the engine.
- **R4.** Q1–Q75 stand as written, in particular Q72 (0.3 m corner tolerance), Q73 (Safe seen from the fill's
  boundary: frontier first, then the rest of the boundary for missed targets only), Q75 (every 0.5 s plus event
  ticks on the 16 Hz grid; integrals weight each tick by the time to the next), Q62 (signed scoring), Q63 (no
  terminal flip in a counterfactual), Q66 (stats count the live round only) and Q71 (placed utility dies with its
  owner).

## Layout

| File | What it is |
|---|---|
| `webapp/app/replays/control_geometry.py` | Masks from a minimap plus tags, the kill-line check, the raycast, and the visibility bitsets with their cache. |
| `webapp/app/replays/control.py` | The engine: round inputs, the tick schedule, per-tick states, the counterfactual, per-section totals, per-player stats. |
| `webapp/scripts/build_control_geometry.py` | Builds the committed assets for every map (and, with `--bitsets`, the local visibility cache). |
| `webapp/app/static/data/control/` | `tags.json` (the hand inputs: tags, see-across paint, specials, the badge flag), and per map `<Map>.sight.png`, `<Map>.walk.png`, plus `manifest.json`. |
| `webapp/.control_cache/` | Gitignored. `<Map>.<key>.npz` visibility bitsets. `CONTROL_CACHE_DIR` overrides the location. |
| `webapp/tests/replays/test_control_*.py` | Tests. Kill-line endpoints for the Risk 1 regression are a fixture, `tests/fixtures/control/kill_lines.json` (pixel endpoints only). |

`webapp/scripts/control_feasibility/` (0a/0b tooling) is not edited: it is the feasibility record. Product code
ports from `engine_proto.py` and `geometry.py`; the tests compare against them where noted.

## Geometry assets (Stage 1)

- **Sight mask (1024 px, 1 = blocks sight):** alpha 0, plus glyphs (opaque pixels with saturation over 80), plus
  shapes tagged `cover`, minus shapes tagged `seeacross` and pixels in the `see_across_paint` (256-cell grid, a
  cell set means see-across). `seeover` and `walkable` and `glyph` tags never block sight (`glyph` means the shape
  is only a drawing on the floor).
- **Traversal mask (1024 px, 1 = walkable):** opaque minus glyphs, minus `cover` shapes. See-across areas stay
  unwalkable. The engine's 128 grid takes a cell as walkable when more than half its pixels are, as 0b did.
- **Tags resolve to pixels at build time.** A tag in 0a's `control-tags.json` names a candidate by `id`, and ids
  depend on the candidate detector's parameters (Abyss's tags were made at glyph saturation 40). The build
  re-runs the detector with the parameters stored on the map's entry, checks each tagged id's `kind`, `bbox` and
  `px` still match, and fails loudly if not. The committed PNGs then carry the result, so the engine never needs
  the detector.
- **Specials** (`teleporter`, `rope`, `drop`, `door`): `{"kind", "a": [u, v], "b": [u, v], "one_way": bool}` in
  `tags.json`, an empty list on every map for now. The engine links `a` and `b` in the flood fill (both ways,
  or `a` to `b` only when `one_way`).
- **Badge:** `cover_reviewed` per map in `tags.json`, false on every map (no cover is tagged yet). The manifest
  repeats it with the kill-line result, so Stage 4 reads one file.
- **Visibility bitsets:** for each walkable cell, the cells it sees in 360 degrees on the sight mask with the
  Q72 tolerance (0b's `cast`, 0.5-degree rays, 2 px steps). Cache key: sha256 of the sight and traversal bytes,
  the grid, the ray parameters, the tolerance and `GEOMETRY_VERSION`.

## Condenser inputs (R1)

All in the blob's `util` list, so older readers skip them (`format.known_util`, `rounds_extras`, and the viewer's
`into[u.k]` check at `replay.js:564`):

- **Flash and nearsight hits:** `flash`/`nearsight` rows gain `hits: [[slot, t, duration_s | null], ...]` from
  `valorant_flash_player_hit` (`initial_duration_seconds`) and `valorant_nearsight_player_hit`
  (`configured_duration_seconds`; null when `duration_until_removed`). `t` is the hit's own time. `targets`
  stays as it is.
- **Possession:** a `Pawn` ability row gains `possessed: [[t0, t1 | null], ...]` from `PlayerTable.possession`,
  in round seconds, clipped to the round.
- **Yaw over time:** a `Pawn` ability row with a path gains `yaws: [[t, yaw], ...]` from the movement rows'
  `yaw` (the camera, the turret, drones): a point when the minimap yaw moved `YAW_STEP_DEG` (2) or more, at most
  one per `PATH_STEP_MS`.
- **Damage:** a new kind, `{"k": "damage", "t", "t1", "by", "target", "src": "gun" | "ability", "wall": bool,
  "n"}`, from every `MulticastNotifyDamage_Point` (`src: gun`, `wall` from `IsWallPenetration`) and
  `MulticastNotifyDamage_Base` (`src: ability`) row on a player's pawn. Consecutive hits with the same
  `(by, target, src, wall)` within `DAMAGE_MERGE_MS` (500) merge into one run (`n` hits).
- `CONDENSE_REVISION` 9 → 10.

## Engine (Stage 2)

Port of `engine_proto.py`, restricted to what the plan settled: raycast vision, boundary Safe (Q73), the
incremental counterfactual with the bridge fix, and a full recompute kept for tests and checks.

- **Inputs.** `compute_round(blob, geometry, link)`, where `link` is `ControlLink(sides: {slot: "attack" |
  "defense"}, db_deaths: [(slot, t)])` on the replay clock. Teams are the blob's side groups (A/B). DB-only deaths
  end a life with `services/replay_view.py`'s rule (`SAME_DEATH_S`); the rule is repeated in `control.py` (with a
  pointer) so the engine doesn't import the DB models.
- **Ticks (Q75).** `0, 0.5, 1, ...` up to `t_end`, plus events, all on the 1/16 s grid: deaths (snapped to the
  last grid point before the death, so the dying player is still alive on it), ability placement (`t`), throw
  (`thrown.t0`) and end (`t1`/`gone`), flash and nearsight hits (the hit's `t` when the blob has `hits`, else the
  0b pop placeholder), status and reveal start and end, and shots (one tick per 0.25 s window). Each tick weighs
  the time to the next (the last one to `t_end`).
- **R1 fields, with fallbacks.** Flash blind = `[t, t + duration]` per target from `hits`, else 0b's pop and
  full-blind placeholders. Nearsight likewise (`NEARSIGHT_DEFAULT_S` for a null duration). Cypher's camera and
  flown drones use `possessed` (else 0b: the drone's whole life, the camera not modelled) and `yaws` (else the
  path's heading for drones, the row's `yaw` for the turret). Wallbangs contest the hit holder for
  `WALLBANG_CONTEST_S` (2) after each hit run; enemy ability damage contests them while it lasts, on top of
  0b's damage-zone overlap. Each fallback used is counted in the result's `missing_inputs`.
- **Watchers.** Trips, alarmbots, Chamber traps, the Killjoy turret (100-degree cone along its yaw), Cypher's
  camera (103 degrees, only while possessed, replacing Cypher's own view), flown drones (Sova full view, Skye's
  Trailblazer 90 degrees to 22.5 m). All stop at the owner's death (Q71). Deadlock's sensor and Tejo's drone have
  no archetype in the three replays; they're listed as not modelled.
- **Per tick:** vision, claims, fills with specials, boundary Safe with smokes, the contest and loss rules, the
  entry's way back, one state per walkable cell, coverage (shared cells split evenly), and control per alive
  player by the incremental counterfactual.
- **Outputs** (`RoundControl`): tick times and weights, states (`ticks x walkable cells`, uint8, 0b's 9 codes),
  per-player control and coverage masks, per-section totals, per-player stats, the team's redundant control,
  `missing_inputs`, and `CONTROL_REVISION` (1). No byte encoder yet (Stage 3).
- **Per-section totals (Heatmaps):** live round only (`t_start` to `t_decided`). 10 s sections by the round clock
  up to the plant (`r0`, `r1`, ...), then by the spike clock (`p0`, ...), the plant time from the `Bomb` ability
  row. Each section keeps seconds per state per walkable cell in A/B terms plus the link's side of each group,
  so both the attack/defense and the team 1/team 2 views derive from it.
- **Per-player stats (Q50, Q66), live round only:** seconds alive; coverage active and passive m²·s; control
  m²·s; each divided by seconds alive; the active ratio (None when both are zero); lost control at each real
  death (m², % of the team's owned area at that tick, split by level lost and by where it went: enemy,
  contested, nobody). **Redundant control** per team: the team's owned area minus the sum of its players'
  control, per tick, integrated (signed).

## Steps

Each step is one commit, `W<n>: ...`. Tests run with `.venv313` (primary) from `webapp/`.

### W3. scipy pin and import isolation
- Files: `webapp/requirements.txt` (`scipy==1.18.1`, the `.venv313` version; Render runs Python 3.13.5; the 3.11
  venv's 1.17.1 is noted), `webapp/tests/replays/test_control_isolation.py`.
- The test imports `app.main` in a fresh interpreter and asserts `app.replays.control`,
  `app.replays.control_geometry` and `scipy` are not in `sys.modules`.
- Check: `pytest tests/replays/test_control_isolation.py` passes; `python -c "import scipy;
  print(scipy.__version__)"` in each venv prints 1.18.1 (313) and 1.17.1 (311, noted).
- Depends on: W2.

### W4a. Geometry module
- Files: `webapp/app/replays/control_geometry.py`, `webapp/tests/replays/test_control_geometry.py`.
- `masks(rgba, entry)` (sight, traversal, the 0a candidate detector ported for tag resolution), `Geometry`
  (1024 sight, 128 walk, scale, tolerance samples, specials), `load_geometry(name)` from the committed assets,
  `line_blocked(sight, a, b)` (the 0a tagger's rule: any trimmed pixel on a sight wall), `cast(...)`,
  `visibility(geo)` (build or load from cache), `cache_key(...)`.
- Tests (synthetic minimap): a clear line; a line through a wall blocked; a line through a tagged cover box
  blocked; the same box tagged see-over clear; see-across paint opens a void; a glyph pixel is a wall and not
  walkable; a tag whose bbox no longer matches refuses; the corner tolerance lets a one-pixel corner clip
  through; a second visibility call loads the cache (build not called), and a changed mask misses it.
- Check: `pytest tests/replays/test_control_geometry.py` passes.
- Depends on: W2.

### W4b. Build script, committed assets, plan text
- Files: `webapp/scripts/build_control_geometry.py`, `webapp/app/static/data/control/{tags.json, manifest.json,
  <Map>.sight.png, <Map>.walk.png}`, `webapp/tests/fixtures/control/kill_lines.json`,
  `webapp/tests/replays/test_control_assets.py`, `webapp/.gitignore` (`.control_cache/`),
  `docs/replay-map-control-plan.md` (Stage 1 text for R2).
- `tags.json` starts as 0a's Abyss entry (tags, cleaned paint, parameters) plus `specials: []` and
  `cover_reviewed: false` for every map. The kill-line fixture is 0a's qualifying non-wallbang bullet kills as
  pixel endpoints (`risk1.json`), per map.
- Tests: the committed PNGs equal a fresh `masks()` build from the minimap and `tags.json` (assets aren't stale);
  per map, blocked kill lines ≤ 2% (Risk 1 bar); Abyss 0 blocked with its tags and > 2% without them; the
  manifest lists every map in `maps.json` that has a minimap, with `cover_reviewed` false.
- Check: `pytest tests/replays/test_control_assets.py` passes; `build_control_geometry.py --bitsets --map Ascent`
  run twice, the second reports a cache hit (logged).
- Depends on: W4a.

### W5. Condenser inputs
- Files: `webapp/app/replays/condense.py` (`read_util` hits), `webapp/app/replays/extras.py` (damage runs,
  `possessed`, `yaws`, `WorldPositions` keeping other pawns' yaw), `webapp/app/replays/format.py` (revision 10,
  the docstring's util kinds), tests in `webapp/tests/replays/test_replay_condense.py` and
  `test_replay_extras.py` (synthetic rows), plus any golden or fixture expectation the revision bump changes.
- Check: the new tests pass; `pytest tests/replays` passes; one real export condensed locally (a run-folder
  script, never stored) shows each new field populated with counts logged, flash durations within 0.03–2.3 s, and
  the round size p95 before and after.
- Depends on: W2. Runs one export at a time (about 1 GB peak).

### W6a. Engine core
- Files: `webapp/app/replays/control.py`, `webapp/tests/replays/test_control_engine.py`,
  `webapp/tests/replays/control_toys.py` (toy geometry and blob builders; toy maps are small rooms so their
  visibility builds in seconds, cached per session).
- Tests, one or more per rule in the plan's Stage 2 list: movement cones; safe space behind a holder; all enemies
  dead makes the map the team's; the entry's view and way back; holder death; mutual-sight contest; both teams
  watching without sight; both teams claiming a cell; hollow vs solid smoke; molly and wallbang contest;
  concuss and reveal downgrade; nearsight bubble; flash duration (from `hits`, and the placeholder); a flashed
  holder whose teammate still covers the cells; lost cells going to the enemy or contested; a trip, and a trip
  dying with its owner; the turret cone; the camera replacing its owner's view; the tick schedule (0.5 s,
  events, shot windows, death snapping, weights); DB-only deaths; specials linking a fill.
- Check: `pytest tests/replays/test_control_engine.py` passes.
- Depends on: W4a, W5 (the field shapes).

### W6b. Counterfactual, stats and sections
- Files: `control.py`, `webapp/tests/replays/test_control_stats.py`.
- Tests: coverage split evenly between two holders; redundant holders score zero; signed scoring (ours→theirs
  costs 2); both teams recomputed; no terminal flip when the last player is removed; the spawn-rotator (≈0) and
  lurker (> 0, mostly denial) examples with asserted signs; incremental equals full on the toy rounds; the
  bridge case recomputes; per-section totals sum to the live seconds and split at the plant; stats ignore time
  outside the live round; lost control at a death.
- Check: `pytest tests/replays/test_control_stats.py` passes.
- Depends on: W6a.

### W7. Real rounds
- A run-folder script (not committed): the engine on every round of the three current-recipe blobs, and on one
  round condensed with W5's fields; the parity check against `engine_proto` on a5549826 round 4.
- Check: per-tick cost (vision, base, incremental counterfactual) within 2x of 0b's (Ascent r4 at 16 Hz:
  59 / 48 / 80 ms); incremental equals full on ≥ 95% of player-ticks; no crash on any round; state agreement with
  `engine_proto` on the same ticks reported.
- Depends on: W6b.

### W8. Wrap-up
- Both suites again; failure sets compared with W0's. The re-ingest command for the user, from
  `scripts/reingest_replays.py`'s docstring.

## Risks

- **Visibility build time.** 0b built it in minutes a map. Toy maps in tests stay small.
- **Blob size.** Damage runs and yaws add bytes against the 70 KB p95 budget (a reported check). W5 measures
  it on a real export.
- **Existing fixtures.** The revision bump may change expected recipes in the replay tests; those are updated in
  W5, never loosened.
- **Parity with 0b.** The engine should agree with `engine_proto` on the 0b round when the blob has no R1
  fields. A disagreement is investigated before W7 passes.
