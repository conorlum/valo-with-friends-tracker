# Handoff: named map areas are wrong more often than right; paint them

Written 2026-11-11. The aim is an `/afk` run: plan → build → PR. The owner wants areas that are **right**, and would
rather have none than wrong ones. This is separate from `HANDOFF-info-util-signals.md`.

## How areas work today

`app/coach/areas.py:area_at(map, u, v)` returns the **nearest of a handful of label points** by straight-line
distance in minimap units. The points come from valorant-api.com (a community site): one point per callout,
probably where the in-game label is drawn (`scripts/fetch_callouts.py` → `app/static/data/callouts.json`).
Counts: Sunset 17, Haven 21, Corrode 21, Ascent 22, Fracture 22, Abyss 23, Breeze 23, Bind 24, Split 24,
Summit 24, Icebox 25, Pearl 26, Lotus 28. So every area is a Voronoi cell of one label point. It ignores
walls, so a point gets the nearest label even through a wall. It ignores floors, so stacked areas (Abyss,
Lotus, Icebox, Bind's teleporter rooms) can't be told apart. It has no notion of the callout's real shape,
since a label point is not the middle of the area.

### Evidence (match `2add00ce-5155-4815-9977-9dc1ad6c00c2`, Sunset; the owner's feedback on coach report 39)

| Moment | Position (u, v) | `area_at` says | Nearest label distances | Owner |
|---|---|---|---|---|
| R10 81–82 s | (3475, 4892) | Mid Courtyard | Courtyard 939, B Market 1203, Mid Top 1296 | "that is technically called market" |
| R18 44.8 s | (8627, 4063) | A Elbow | A Elbow 1000, A Site 1173, A Link 1430 | "momo is not in A elbow" |
| R13 23.2 s | (2096, 5213) | B Market | — | correct; the model cited an earlier `where` (Mid Top at 17 s) |

Two of three disputed names are a label-point boundary falling in the wrong place. The third is a coach-prompt
issue (the playbook v10 proposal adds "a named area must come from a `where` within a second").

## Who reads areas (each must keep working, and each changes when areas change)

- Coach: `app/coach/roundview.py:97` (`where`, `knew` enemies' `area`), `app/coach/brief.py:89` (the area
  name list given to the model), `app/coach/control_areas.py:23` (the `control` query's per-area shares: it
  labels **every walk cell** with `area_at` of its centre), `app/coach/detectors.py`, and
  `app/coach/checker.py` (≈ 106–220: a named area in a point must match a cited answer, and the area names in
  prose are matched as the brief spells them).
- Critical Util: `app/services/replay_util.py:area_of` uses only the **super region** (A / B / Mid / own half /
  enemy half). Super regions change less, but any change moves Critical Util values. Check whether
  `util_model`'s `corpus_key` notices; it probably doesn't, since the file isn't a DB row.
- Check `app/templates/_eco_followup_card.html` and `_enemy_at_11_response_card.html`: grep matched
  "callouts" there, but it may be unrelated wording.

## The idea: a painted area layer per map, on the control grid

The map-control grid already exists for every map: walk cells (`app/static/data/control/<Map>.walk.png`,
`geometry.cell_of_px`, `geo.centres`), and with heights, floor nodes (`geo.node_at(cell, z)`). There's already
a local drawing tool that paints cells: `scripts/control_tagger.py` (+ `control_tagger_core.js`; Chokes mode
paints cells and exports `<Map>.chokes.json`, Map features mode annotates floors). So:

1. **An "Areas" mode in the tagger.** Paint every walk cell with one area name, with a brush and a fill
   bounded by walls. Start from the current nearest-label Voronoi so it's a correction pass, not a blank map.
   Unpainted cells are allowed. Show the label points, the minimap, and the replay's own position samples
   (a heatmap helps find where players actually stand). Export `app/static/data/control/<Map>.areas.json`
   (version, map, names with their super region, and cells → name id, compact like `chokes.json`). Hand-only:
   no re-detection overwrites it.
2. **More granular names where the owner wants them.** The owner's own callouts (e.g. "market" covering what
   the API calls Mid Courtyard's edge) override the API's. The name list per map lives in the areas file, not
   `callouts.json`. Keep `super` for each name, since Critical Util and the coach's site logic use it.
3. **`area_at` reads the painted file first**: the cell under (u, v); with heights, optionally per floor node
   (phase 2). A position on an unpainted cell, or with no areas file for its map, returns **None** rather than
   the nearest-label guess. The owner said wrong is worse than missing. Decide in the scope whether the
   Voronoi fallback stays for unpainted maps (recommend: off for any map that has an areas file, kept for
   maps that don't until they're painted, and say so in `where`'s note).
4. **Fallout to handle:**
   - `checker.py`: a point naming an area must still match a cited answer; an answer with no area means the
     model can't name one, so no name and no failure.
   - `brief.py`: the area list given to the model comes from the areas file.
   - `control_areas.py`: per-cell labels come straight from the file (cheaper and exact).
   - Critical Util: if super regions change, the stored build goes stale. Make `corpus_key` (or its inputs)
     include the areas file's hash, or note that a manual `scripts/critical_util.py refresh` is needed.
   - Published coach reports are immutable; nothing to migrate.
5. **Floors (phase 2, optional).** For maps with stacked walkable space, paint per height node rather than per
   cell. Find out how the heights asset numbers nodes and whether the tagger's Map features mode (floors)
   already gives a way to pick a floor. Phase 1 can mark stacked cells as "uncertain area" (returns None) instead.

## What to measure before and after (the acceptance bar)

- Build a ground-truth set from **owner feedback**: every `coach_feedback` row (read-only, through
  `scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`) whose verdict is wrong or
  correction and whose note talks about a location ("not in", "is called", "location", an area name). Pair each
  with the report's cited `where` answer (round, t, player) to get (map, u, v, owner's name). The repo is
  public: keep this set out of git, or store only map + u,v + area, with no names.
- Report agreement for the old `area_at` vs the painted one. Expect the old one below 50% on disputed points
  (by construction, since these are the disputed ones). The painted one should match all the cases it covers.
- Add a small committed fixture of (map, u, v, expected area) per painted map, with no player data, so a
  repaint can't silently move a known boundary.

## Decisions for the AFK scope

- Who paints. The run can build the tool and a **draft** paint from the Voronoi plus wall-bounded fills, but
  the final names and boundaries are the owner's game knowledge. Plan for: run builds the tool + draft for one
  map (Sunset, where the evidence is) → owner paints/corrects → a follow-up applies it. Don't have the run invent
  area boundaries and ship them as right.
- Fallback when unpainted: None vs nearest label (see 3).
- Area names: keep the API's names where they're fine, and add owner names; super region per name.
- Floors: phase 1 or later.
- Whether the replay viewer should also draw the painted areas (a toggle on the minimap). It's useful for the
  owner to spot errors, but optional.

## Constraints and pointers

- No player names in committed files; this repo is public. Map coordinates and area names are fine.
- The tagger is a local tool that writes to `%TEMP%`, never the repo. Exports are copied in by hand. Keep that pattern.
- `docs/superpowers/plans/2026-10-04-map-interaction-tagger*.md` and `scripts/control_tagger.py`'s docstring
  describe how modes, exports and the core JS are tested (`tests/replays/test_map_feature_tagger.py`).
- Any change to the control inputs makes rounds stale. An areas file should **not** be a control input: keep it
  out of the control fingerprint so painting doesn't trigger a map-control recompute. `control_areas.py` reads
  it at query time.
- Tests: `./.venv313-test/Scripts/python.exe -m pytest`, suites via `python scripts/test_catalog.py plan --git`,
  new files into `tests/suites.toml`. Never merge; the run ends at an open PR.
