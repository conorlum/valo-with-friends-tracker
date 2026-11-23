"""Offline naming model and position-only, self-contained page generation."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from app.services import plant_spots


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "scripts" / "plant_namer_core.js"


def node(body, layouts=None):
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is unavailable (use --require-node to require it)")
    program = "const P = require(process.argv[1]); const m = P.create(JSON.parse(process.argv[2]));\n"
    result = subprocess.run(
        [executable, "-e", program + body, str(CORE), json.dumps(layouts or {})],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def spot(**changes):
    return {"id": "spot-1", "name": "Default", "u": 2000, "v": 3000, "r": 100, **changes}


def test_add_stops_at_ten_and_reuses_no_ids():
    got = node("""
      const ids = Array.from({length: 11}, () => m.add('Ascent', 'A', 2000, 3000));
      console.log(JSON.stringify({ids, spots: m.layouts.Ascent.A}));
    """)
    assert len(got["spots"]) == 10
    assert len(set(got["ids"][:10])) == 10
    assert got["ids"][10] is None


@pytest.mark.parametrize("second", [spot(id="spot-2", u=2400, r=200), spot(id="spot-2", r=20)])
def test_merge_keeps_one_circle_covering_both(second):
    first = spot()
    got = node("""
      m.merge('Ascent', 'A', 'spot-1', 'spot-2');
      console.log(JSON.stringify(m.layouts.Ascent.A));
    """, {"Ascent": {"A": [first, second]}})
    assert len(got) == 1
    assert got[0]["id"] == first["id"] and got[0]["name"] == first["name"]
    for old in [first, second]:
        distance = ((old["u"] - got[0]["u"]) ** 2 + (old["v"] - got[0]["v"]) ** 2) ** .5
        assert distance + old["r"] <= got[0]["r"] + 1e-8


def test_discard_and_undo_restore_exact_spot():
    initial = {"Ascent": {"A": [spot()]}}
    got = node("""
      m.discard('Ascent', 'A', 'spot-1');
      const empty = JSON.parse(JSON.stringify(m.layouts));
      m.undo(); console.log(JSON.stringify({empty, restored: m.layouts}));
    """, initial)
    assert got["empty"]["Ascent"]["A"] == []
    assert got["restored"] == initial


def test_unnamed_spot_anywhere_blocks_export():
    got = node("""
      let error = ''; try { m.exportJSON(); } catch (e) { error = e.message; }
      console.log(JSON.stringify(error));
    """, {"Ascent": {"B": [spot(name="   ")]}})
    assert "name" in got.lower() and "Ascent/B" in got


def test_export_passes_python_loader_and_edits_undo(tmp_path):
    initial = {"Ascent": {"A": [spot()], "B": []}}
    got = node("""
      m.update('Ascent', 'A', 'spot-1', {u: 4500, v: 5500, r: 250, name: 'Box'});
      const edited = JSON.parse(m.exportJSON());
      m.undo(); console.log(JSON.stringify({edited, restored: m.layouts}));
    """, initial)
    path = tmp_path / "plant_spots.json"
    path.write_text(json.dumps(got["edited"]), encoding="utf-8")
    assert plant_spots.load(path)["Ascent"]["A"] == [spot(u=4500, v=5500, r=250, name="Box")]
    assert got["restored"] == initial


@pytest.mark.parametrize("turns,display", [(0, (2000, 3000)), (1, (7000, 2000)),
                                           (2, (8000, 7000)), (3, (3000, 8000))])
def test_pointer_maps_back_to_unrotated_uv(turns, display):
    x, y = display
    got = node(f"""
      console.log(JSON.stringify(P.pointerUV({40 + x * .05}, {70 + y * .05},
        {{left:40, top:70, width:500, height:500}}, {turns})));
    """)
    assert got == pytest.approx({"u": 2000, "v": 3000})


def test_invalid_edits_and_impossible_merge_leave_state_unchanged():
    initial = {"Ascent": {"A": [spot(u=0, r=10000), spot(id="spot-2", u=10000, r=10000)]}}
    got = node("""
      const errors = [];
      for (const action of [() => m.update('Ascent','A','spot-1',{u:-1}),
                            () => m.update('Ascent','A','spot-1',{r:0}),
                            () => m.merge('Ascent','A','spot-1','spot-2')]) {
        try { action(); } catch(e) { errors.push(e.message); }
      }
      console.log(JSON.stringify({errors, layouts:m.layouts}));
    """, initial)
    assert len(got["errors"]) == 3
    assert got["layouts"] == initial


def test_python_builds_fake_points_page_without_external_urls():
    from scripts import plant_namer

    rows = [("Ascent", "A", 2000, 3000)] * 6 + [("Ascent", "B", 6000, 5000)] * 6
    rows += [(None, "A", 1000, 1000)]  # Unknown layout never guessed.
    saved = {"Ascent": {"A": [spot(name="</script><script>throw 1</script>")], "B": []}}
    data = plant_namer.build(rows, saved)
    assert len(data["maps"]) == 13
    assert data["layouts"]["Ascent"] == saved["Ascent"]
    assert len(data["maps"]["Ascent"]["points"]["A"]) == 6
    assert data["maps"]["Ascent"]["rotation"] in range(4)
    assert all(row["image"].startswith("data:image/png;base64,") for row in data["maps"].values())
    page = plant_namer.render(data)
    without_images = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "data:image/png;base64,", page)
    assert not re.search(r"(?:https?:)?//|(?:src|href)=[\"'](?!data:|#)", without_images)
    assert "/*DATA*/null" not in page and "/*CORE*/" not in page
    assert "</script><script>throw" not in page
    proposals = plant_namer.build(rows, {})["layouts"]["Ascent"]
    assert len(proposals["A"]) == len(proposals["B"]) == 1
    assert proposals["A"][0]["name"] == ""


def test_main_writes_only_temp_page(monkeypatch, tmp_path):
    from scripts import plant_namer

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(plant_namer, "read_points", lambda: [("Ascent", "A", 2000, 3000)] * 6)
    assert plant_namer.main([]) == 0
    assert (tmp_path / "valo-plant-namer" / "plant-namer.html").is_file()
    assert len(list(tmp_path.rglob("*.*"))) == 1


def test_reader_requires_wrapper_read_only_url_before_connecting(monkeypatch):
    from scripts import plant_namer

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="with_friends_db"):
        plant_namer.read_points()


def _fake_database(monkeypatch, rows):
    from scripts import plant_namer

    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            calls.append(str(statement))
            return rows

    class Engine:
        def begin(self):
            return Connection()

        def dispose(self):
            calls.append("disposed")

    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/valowithfriendsdb?options=-c%20default_transaction_read_only%3Don")
    monkeypatch.setattr(plant_namer, "create_engine", lambda *args, **kwargs: Engine())
    return plant_namer, calls


def test_reader_selects_only_position_fields_in_read_only_transaction(monkeypatch):
    plant_namer, calls = _fake_database(monkeypatch, [("Ascent", "release-13.08", "A", 2000, 3000)])
    assert plant_namer.read_points() == [("Ascent", "A", 2000, 3000)]
    assert calls[0] == "SET TRANSACTION READ ONLY"
    select = " ".join(calls[1].lower().split())
    assert select.startswith("select m.map_name, r.game_branch, p.site, p.u, p.v from round_plants p")
    assert "link_status = 'linked'" in select
    for private in ("display_name", "external_id", "match_uuid", "player", "riot"):
        assert private not in select
    assert calls[2] == "disposed"


def test_reader_decides_the_layout_when_rows_are_read(monkeypatch):
    from app.replays import layouts

    def row(key, start):
        return {"id": key, "key": key, "start": start, "drawn_from": "13.08", "regions": None}

    registry = {"maps": {"Ascent": [row("Ascent", None), row("Ascent_14_0", "14.0")],
                         "Bind": [row("Bind", None)]}}
    monkeypatch.setattr(layouts, "load_registry", lambda path=None: registry)
    plant_namer, _ = _fake_database(monkeypatch, [
        ("Ascent", None, "A", 1, 1),                        # tracker row, no replay: layout unknown
        ("Ascent", "release-13.08", "A", 2000, 3000),       # base layout
        ("Ascent", "release-14.00", "B", 4000, 5000),       # the later layout
        ("Bind", None, "A", 10, 20),                        # one layout: known without a replay
        (None, None, "A", 7, 7),                            # no map at all
    ])
    assert plant_namer.read_points() == [
        ("Ascent", "A", 2000, 3000), ("Ascent_14_0", "B", 4000, 5000), ("Bind", "A", 10, 20)]
