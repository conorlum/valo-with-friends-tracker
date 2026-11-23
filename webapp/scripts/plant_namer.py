"""Build the owner's offline plant naming page (positions only).

Run from webapp through the read-only wrapper:
  scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/plant_namer.py
Open the printed file, review/name the circles, then Export plant_spots.json.
Copy the reviewed download to app/static/data/plant_spots.json for review in git.
The generated page always lives in %TEMP%/valo-plant-namer, never the repository.
"""
import argparse
import base64
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.services import plant_spots  # noqa: E402
from app.services.map_rotation import quarter_turns  # noqa: E402
from app.services.round_plants import asset_key_for  # noqa: E402

HERE = Path(__file__).resolve().parent
STATIC = WEBAPP_ROOT / "app" / "static"


# Position fields plus what decides the layout (map and the linked replay's branch).
# No player, match or replay identifier is selected; the join is plant_page._joined's.
POINTS_SQL = (
    "SELECT m.map_name, r.game_branch, p.site, p.u, p.v FROM round_plants p "
    "JOIN matches m ON m.id = p.match_id "
    "LEFT JOIN replays r ON r.match_id = p.match_id AND r.link_status = 'linked'"
)


def read_points():
    """Read position fields; refuse a URL without the wrapper's read-only option."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("Run through scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only")
    parsed = make_url(url)
    if (parsed.database != "valowithfriendsdb"
            or "default_transaction_read_only=on" not in parsed.query.get("options", "")):
        raise ValueError("Run through scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only")
    parsed = parsed.set(drivername="postgresql+psycopg2")
    engine = create_engine(parsed)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = list(connection.execute(text(POINTS_SQL)))
    finally:
        engine.dispose()
    # The layout is decided here, from the replay's game branch, never from the stored
    # asset_key (R9): a row whose layout is unknown now is dropped, not guessed.
    chosen = {}
    points = []
    for map_name, game_branch, site, u, v in rows:
        if (map_name, game_branch) not in chosen:
            chosen[map_name, game_branch] = asset_key_for(map_name, game_branch)
        if chosen[map_name, game_branch] is not None:
            points.append((chosen[map_name, game_branch], site, u, v))
    return points


def build(points, saved=None):
    """Embed known minimaps, saved circles and proposals from (asset_key, site, u, v)."""
    saved = plant_spots.load() if saved is None else saved
    manifest = json.loads((STATIC / "data" / "layouts.json").read_text(encoding="utf-8"))["maps"]
    map_names = {layout["key"]: name for name, layouts in manifest.items() for layout in layouts}
    grouped = defaultdict(lambda: defaultdict(list))
    for asset_key, site, u, v in points:
        if (asset_key in map_names and site in ("A", "B", "C")
                and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 10000 for n in (u, v))):
            grouped[asset_key][site].append([u, v])
    layouts = json.loads(json.dumps(saved))
    maps = {}
    for asset_key, name in sorted(map_names.items()):
        image = STATIC / "img" / "maps" / f"{asset_key}.png"
        if not image.is_file():
            image = STATIC / "img" / "maps" / f"{name}.png"
        sites = {"A", "B"} | set(grouped[asset_key]) | set(saved.get(asset_key, {}))
        if name in ("Haven", "Lotus"):
            sites.add("C")
        site_points = {site: sorted(grouped[asset_key][site]) for site in sorted(sites)}
        layouts.setdefault(asset_key, {})
        for site, rows in site_points.items():
            if not layouts[asset_key].get(site):   # no saved spots, including a site saved as []
                layouts[asset_key][site] = plant_spots.propose(rows, plant_spots.m_per_uv(name))
        maps[asset_key] = {
            "image": "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode("ascii"),
            "rotation": quarter_turns(asset_key), "points": site_points,
        }
    return {"maps": maps, "layouts": layouts, "saved": {"version": 1, "layouts": saved}}


def render(data):
    # JSON inside a script must not let owner-entered text close its script tag.
    embedded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    template = (HERE / "plant_namer.template.html").read_text(encoding="utf-8")
    core = (HERE / "plant_namer_core.js").read_text(encoding="utf-8")
    return template.replace("/*CORE*/", core).replace("/*DATA*/null", embedded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    try:
        data = build(read_points())
    except ValueError as exc:
        parser.error(str(exc))
    out = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-plant-namer" / "plant-namer.html"
    if out.resolve().is_relative_to(WEBAPP_ROOT.parent):
        parser.error("TEMP must be outside the repository")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    print(f"{len(data['maps'])} layouts -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
