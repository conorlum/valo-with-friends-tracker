"""Tracers end at the first wall or low box (the 2026-10-05 review, item 11): the bullet mask is its own asset
(sight goes over a low box, a shot doesn't; a shot flies across a drop nobody can walk on), every committed map
has one, and the viewer's ray helper stops on it."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))

from test_control_geometry import minimap, paint_rect, tagged  # noqa: E402

from app.control import geometry as cg  # noqa: E402

REPLAY_JS = WEBAPP / "app" / "static" / "js" / "replay.js"
NODE = shutil.which("node")
BOX = (slice(210, 250), slice(210, 250))          # inside the toy minimap's closed shape


def test_a_see_over_box_stops_a_shot_and_not_sight():
    rgba = minimap()
    entry = tagged(rgba, "closed", (230, 230), "seeover")
    sight, bullet = cg.masks(rgba, entry).sight, cg.bullet_mask(rgba, entry)
    assert not sight[BOX].any() and bullet[BOX].all()
    assert np.array_equal(bullet & ~cg.tag_shapes(rgba, entry)["seeover"], sight & ~cg.tag_shapes(rgba, entry)["seeover"])


def test_an_untagged_shape_and_a_cant_walk_drop_stay_open_to_shots():
    rgba = minimap()
    drop = {"cant_walk_paint": paint_rect(680, 380, 720, 420)}
    assert not cg.masks(rgba, drop).walk[400, 700], "the drop is not walkable"
    assert not cg.bullet_mask(rgba, drop)[400, 700], "but a shot flies across it"
    assert np.array_equal(cg.bullet_mask(rgba), cg.masks(rgba).sight), "nothing tagged: the walls only"


def test_bullet_paint_adds_an_obstacle_the_map_only_outlines():
    rgba = minimap()
    entry = {"bullet_paint": paint_rect(680, 380, 720, 420)}
    assert cg.bullet_mask(rgba, entry)[400, 700]
    assert np.array_equal(cg.masks(rgba, entry).sight, cg.masks(rgba).sight), "sight is not touched"
    assert np.array_equal(cg.masks(rgba, entry).walk, cg.masks(rgba).walk), "nor walking"


def test_a_see_across_void_is_open_to_shots_too():
    rgba = minimap()
    entry = tagged(rgba, "void", (320, 620), "seeacross")
    assert cg.bullet_mask(rgba)[620, 320] and not cg.bullet_mask(rgba, entry)[620, 320]


def test_every_committed_map_has_a_bullet_mask_matching_the_index_and_its_sight_and_walk_are_as_before():
    index = json.loads((cg.ASSET_DIR / "index.json").read_text(encoding="utf-8"))["maps"]
    tags = cg.load_tags()["maps"]
    assert len(index) == 13
    for name, row in index.items():
        bullet = cg.read_mask_png(cg.ASSET_DIR / f"{name}.bullet.png")
        sight = cg.read_mask_png(cg.ASSET_DIR / f"{name}.sight.png")
        assert hashlib.sha256(np.packbits(bullet).tobytes()).hexdigest()[:12] == row["bullet_sha"], name
        assert hashlib.sha256(np.packbits(sight).tobytes()).hexdigest()[:12] == row["sight_sha"], name
        assert not (sight & ~bullet).any(), f"{name}: every sight wall stops a shot"
        extra = bool((bullet & ~sight).any())
        has_boxes = bool(tags[name].get("bullet_paint")) or any(t.get("tag") == "seeover" for t in tags[name].get("tags") or [])
        assert extra == has_boxes, name


def test_the_summit_box_stops_shots_walking_and_sight():
    # The owner, 2026-10-06 (review of D11): it is two boxes high, so it is cover, not a low box.
    bullet = cg.read_mask_png(cg.ASSET_DIR / "Summit.bullet.png")
    sight = cg.read_mask_png(cg.ASSET_DIR / "Summit.sight.png")
    walk = cg.read_mask_png(cg.ASSET_DIR / "Summit.walk.png")
    assert bullet[422, 783] and sight[422, 783] and not walk[422, 783]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_ray_stops_at_the_first_obstacle_or_the_maps_edge():
    script = """
      const R = require(process.argv[1]);
      const n = 1024, mask = new Uint8Array(n * n), k = 10000 / n;
      const block = (x0, y0, x1, y1) => { for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) mask[y * n + x] = 1; };
      block(500, 0, 510, 1024);            // a wall down x 500
      block(300, 300, 305, 310);           // a low box
      block(98, 395, 104, 405);            // something the shooter leans on
      const end = (x, y, dx, dy) => { const e = R.tracerEnd(mask, n, x * k, y * k, (x + dx) * k, (y + dy) * k);
                                      return e && [Math.round(e.u / k), Math.round(e.v / k)]; };
      process.stdout.write(JSON.stringify({
        wall: end(100, 100, 25, 0),                 // well past the old 25 m length, to the wall
        box: end(100, 305, 10, 0),
        edge_left: end(100, 100, -3, 0),
        edge_down: end(700, 700, 0, 5),
        shallow: end(100, 100, 200, 1),
        leaning: end(100, 400, 5, 0),               // starts inside an obstacle: out of it, then on to the wall
        inside_thick: end(505, 600, 0, 1),          // deep inside a wall: stops within the start allowance
        no_direction: R.tracerEnd(mask, n, 100 * k, 100 * k, 100 * k, 100 * k),
        skip: R.START_SKIP_PX
      }));"""
    done = subprocess.run([NODE, "-e", script, str(REPLAY_JS)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    assert got["wall"] == [499, 100] and got["box"] == [299, 305]
    assert got["edge_left"][0] == 0 and got["edge_down"] == [700, 1023]
    assert abs(got["shallow"][0] - 499) <= 1 and abs(got["shallow"][1] - 102) <= 1
    assert got["leaning"] == [499, 400]
    assert got["inside_thick"][1] <= 600 + got["skip"]
    assert got["no_direction"] is None


def test_the_viewer_keeps_the_old_tracer_until_the_mask_is_there_and_both_pages_pass_it():
    source = REPLAY_JS.read_text(encoding="utf-8")
    assert "var eu = end ? end.u : shot.u1, ev = end ? end.v : shot.v1;" in source
    assert "img.onerror = function () { self.bullet = null; };" in source
    page = (WEBAPP / "app" / "templates" / "replays" / "replay.html").read_text(encoding="utf-8")
    assert '.bullet.png' in page and "bulletMask" in page
    standalone = (WEBAPP / "scripts" / "render_replay_standalone.py").read_text(encoding="utf-8")
    assert "bulletMask: data.bullet || null" in standalone and ".bullet.png" in standalone
