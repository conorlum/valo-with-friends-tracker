# Redrawing a map for a new layout

When Riot changes a map, replays from that patch on should be computed and drawn on the new layout, and older
replays should stay on the old one. This runbook covers one redraw: a Claude session the owner starts ("Ascent
changed in 13.10"). The tool is `webapp/scripts/map_layout.py`. The design is
`docs/superpowers/specs/2026-11-09-map-layouts-design.md` (Section 3).

Commands run from `webapp/` with `.\.venv313\Scripts\python.exe` (shown as `python` below). Keep every download,
picture and page in a scratch folder **outside the repo**, for example `~/rp/<map>-<patch>/`. Patch-note text and
pictures, screenshots and replay exports never go into the repo (it is public).

## 0. Why now

There are two signals:

- **Site health, `map_layout_walls`** (the upload page's card). A key like `Ascent:13.10` fires when a later patch's
  replays spend noticeably more time on cells the current layout calls wall: at least 4 replays, and a pooled share
  more than 0.5 percentage points over the layout's baseline. Its context names the top callouts where the positions
  land. It can't see a change the minimap doesn't show (a wall made metal, a raised wall, a moved plant zone) or a
  closed-off area.
- **The patch notes**, which cover what the signal can't. The owner usually starts from them.

## 1. Gather

```
python scripts/map_layout.py notes 13.10 --map Ascent --out ~/rp/ascent-13.10
```

This writes the map's subsection of the patch's "Map Updates" as `notes-13.10-Ascent.txt`, plus the subsection's
picture URLs (`.images.txt`). If the page's URL differs from the usual form, pass `--url`. Open the before and after
pictures. If `map_layout_walls` fired, note its context.

## 2. The new map picture

```
python scripts/map_layout.py fetch-minimap Ascent --out ~/rp/ascent-13.10
```

It compares upstream with the newest layout's minimap (the base one on a first redraw).

- `differs`: valorant-api already serves the new minimap (saved to `--out`). Use it as the picture.
- `same`, `same floor as <key>` (upstream still serves the picture an earlier redraw was built from) or `patched,
  upstream unchanged`: upstream hasn't caught up. Ask the owner for a screenshot of the map only, from Valoplant or
  in game, with no planner drawings. Valoplant has no image URLs to fetch.

## 3. Fit and diff

```
python scripts/map_layout.py align --picture ~/rp/ascent-13.10/ingame.png --crop x0,y0,x1,y1 \
    --ours app/static/img/maps/<previous layout's key>.png --out ~/rp/ascent-13.10/fit
python scripts/map_layout.py diff --fit ~/rp/ascent-13.10/fit --ours app/static/img/maps/<previous key>.png
```

`align` fits the picture's floor onto the previous layout's minimap. It searches rotation (in steps of 90°), scale
and shift, scores each fit by IoU, and writes `fit.json`, `aligned.png` (to look at), `floor.png` (the fitted floor
mask that `diff`, the overlay and `build` all use) and `diff.png`. A picture with transparency (a minimap from
`fetch-minimap`) is read with its transparent pixels as background. Crop the screenshot to the
map first, since UI around it spoils the fit. For reference, Corrode's 13.08 screenshot (crop `150,15,840,765`) fitted
the pre-redraw minimap at a 90° CCW rotation, scale 1.34 and IoU 0.907. A fit much below ~0.85 usually means a bad
crop or a background colour the floor mask doesn't recognise. `diff` lists each changed region with its kind
(`added`: floor that was wall, or `removed`: wall that was floor), its area and its box.

## 4. The owner decides

```
python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only \
    scripts/map_layout.py overlay --ours app/static/img/maps/<previous key>.png \
    --fit ~/rp/ascent-13.10/fit --map Ascent --patch 13.10 --out ~/rp/ascent-13.10/overlay
```

The overlay must read replay positions from the friends DB, so it runs through `with_friends_db.py`, read-only.
It reads at most 200 stored rounds of the map's replays played on `--patch` or later (older replays would fill an
area the patch closed off), and says how many replays they came from: with none, the positions can't confirm
anything yet. Open `overlay.html` from disk next to the patch pictures. Each changed region has one row:

- **keep**: the redraw is right; it goes into the new minimap.
- **reject**: a fit artefact or a picture defect (the replays contradict it: an "added" region nobody walks, a
  "removed" region full of positions).
- **minimap can't show**: the change is real but invisible on a minimap (a metal wall: bullet; a raised wall: sight
  and heights; a plant zone). List these in the PR, because they need hand edits to `tags.json` paints or specials.

"Export kept regions as mask png" saves the changed-region mask, which is `build`'s `--regions` input.

A change that moves heights but not the minimap (a raised wall, a new box) produces no region here. Paint its area
white on a black 1024 x 1024 png in the minimap's frame and pass it to `build` as `--extra-regions`: it joins the
regions file, which keeps other layouts' positions out of the new layout's heights there, without touching the
minimap.

## 5. Build

```
python scripts/map_layout.py build Ascent 13.10 --floor ~/rp/ascent-13.10/fit/floor.png \
    --regions ~/rp/ascent-13.10/regions-Ascent.png [--extra-regions ~/rp/ascent-13.10/extra-Ascent.png]
```

This writes the layout's assets under `app/static`, keyed `Ascent_13_10`:

- the minimap `img/maps/Ascent_13_10.png`, in the vendored style (wall transparent, floor (118,118,118), 2 px white
  outline), with every pixel outside the regions kept from the previous layout;
- the masks and chokes (`data/control/Ascent_13_10.{sight,walk,bullet,regions}.png`, `.chokes.json`);
- an empty `must_block.Ascent_13_10.json`;
- the entries for the key in `index.json`, `tags.json` (copied from the previous layout), `callouts.json` (copied)
  and `layouts.json`.

If the layout is already in `layouts.json` as a pending entry (a one-line PR that registered `Ascent@13.10` while
the redraw waited), `build` completes that entry in place; a key that already has assets is refused.

It prints the callouts inside a changed region, which need review: move or rename them in `callouts.json[key]`.
`maps.json` and every base entry stay byte-identical.

**Kill lines.** A key without passing kill lines reads as `no_map` and computes nothing. Without any, `build` writes
everything else and exits 2. To make them:

1. Run `scripts/control_feasibility/kill_lines.py <export dir> ~/rp/ascent-13.10/kills` on each export of the new
   patch (only the new patch's: a fresh out dir).
2. `python scripts/map_layout.py kill-lines --kills ~/rp/ascent-13.10/kills --map Ascent --key Ascent_13_10`
   keeps Risk 1's qualifying kills (`risk1.qualifies`: non-wallbang bullet hits with both positions known, off the
   image edge) and writes their `[killer x, y, victim x, y]` endpoints under `"maps"."Ascent_13_10"` in
   `tests/fixtures/control/kill_lines.json`. (`risk1.py` itself reads the base map's geometry and minimap, so it
   isn't run for a layout key.)
3. Re-run `python scripts/build_control_geometry.py --map Ascent_13_10`, which judges those lines against the key's
   sight mask, and check that the key's `index.json` entry has `kill_lines.passes: true` before opening the PR.

Then:

- Apply the "minimap can't show" changes to the key's `tags.json` entry, and rebuild its geometry.
- Regenerate the map-feature fixtures and pins (`tests/replays/map_feature_legacy.py`,
  `tests/replays/test_control_features.py`) if the map has feature bundles.
- Run the `[map-layouts]` and control suites (`python scripts/test_catalog.py plan --git`). The base-identity test
  (`test_layout_base_identity.py`) must stay green: a redraw never changes the base layout.

## 6. PR

Open a PR with the overlay decisions, the "can't show" list and the fit's IoU. The owner reviews and merges it.

### What the merge sets off

- **Replays from the patch on.** These recompute on the new layout once the worker's image names the new key
  (`/health` lists its layouts). Until then the site holds those rounds at no cost to their tries: the deploy gap.
  Their stored control stays visible and is marked out of date.
- **Older replays.** These keep the previous layout; nothing about them changes.
- **Heights.** With `REPLAY_HEIGHTS_AUTO` on, the new layout's heights build by themselves once it has a replay of
  its own (its pool counts every layout's positions outside the changed regions). The previous layout's heights
  rebuild without the new patch's positions inside the changed regions. A height build with a regions mask needs a
  worker on heights protocol 1.
- **Site health.** `map_layout_walls` clears for every patch the new layout's `drawn_from` covers. A false alarm with
  no redraw is cleared with an entry in `app/data/site_health_resolved.json`; it stays cleared until the evidence
  changes (another replay of that patch, or a recomputed round), when the alarm is judged afresh.
