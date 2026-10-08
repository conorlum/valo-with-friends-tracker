"""scripts/compare_height_builds.py: two builds of one map side by side (the slopes spec, "How it is judged")."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import heights as hc

WEBAPP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WEBAPP / "scripts"))

import compare_height_builds as compare  # noqa: E402

GRID, MAXF = 128, hc.MAX_FLOORS
INPUTS = {"rounds_sha": "r" * 16, "rounds": 44, "matches": 2, "sight_sha": "s" * 12, "walk_sha": "w" * 12,
          "preview_min_matches": None}


def write(folder: Path, ground_dm: dict, origin: int, kills: tuple, name="Toy", supported=None, inputs=INPUTS,
          checks=True):
    """A preview: {(y, x): dm above `origin`} as ground, each supported unless listed otherwise."""
    folder.mkdir(parents=True, exist_ok=True)
    floors = np.full((GRID, GRID, MAXF), -1, np.int16)
    sup = np.zeros((GRID, GRID), bool)
    for (y, x), dm in ground_dm.items():
        floors[y, x, 0], sup[y, x] = dm, True if supported is None else (y, x) in supported
    asset = hc.HeightAsset(floors, np.zeros_like(floors), sup, np.zeros((GRID, GRID), bool), np.zeros((0, 5), np.int32),
                           {"origin_z": origin})
    hc.save_asset(folder / f"{name}.height.npz", asset)
    blocked, qualifying = kills
    report = {"supported": 0.5, "unresolved_cells": 0, "unresolved_why": {}, "filled_cells": 0, "one_way_edges": 0,
              "ready": False, "not_ready": ["thin"]}
    if checks:
        report.update({"must_block": {"blocked": 0, "checked": 0, "lines": 0, "passes": True},
                       "kill_lines": {"blocked": blocked, "qualifying": qualifying, "share": blocked / qualifying,
                                      "passes": True}})
    wrapper = {"height_sha": asset.digest, "height": report}
    if inputs is not None:
        wrapper["inputs"] = dict(inputs)
    (folder / f"{name}.height.json").write_text(json.dumps(wrapper), encoding="utf-8")


def test_flat_ground_is_compared_in_world_heights_and_lost_cells_are_counted_apart(tmp_path):
    # Old: origin 100; supported cells at 0, 0, 20 and 40 dm, and one filled. New: origin 90 (a lower floor was
    # found), the same ground 10 dm up in its own frame, except one cell 5 dm lower and one with no height.
    write(tmp_path / "old", {(5, 5): 0, (5, 6): 0, (5, 7): 20, (5, 4): 40, (5, 8): 0}, 100, (0, 100),
          supported={(5, 5), (5, 6), (5, 7), (5, 4)})
    write(tmp_path / "new", {(5, 5): 10, (5, 6): 5, (5, 7): 30, (5, 9): 0}, 90, (1, 100))
    old, new = compare.read(tmp_path / "old", "Toy"), compare.read(tmp_path / "new", "Toy")
    assert compare.refusals(old, new) == []
    flat = compare.flat_ground(old, new)
    assert (flat["cells"], flat["lost"]) == (3, 1), "the lost cell is not in the distribution"
    assert abs(flat["within"] - 2 / 3) < 1e-9 and -0.5 <= min(flat["pct"]) <= -0.45
    lines, worse = compare.compare("Toy", old, new)
    assert worse and any("WORSE" in line for line in lines)
    assert any("supported before, no height now: 1 cells" in line for line in lines)
    assert any("gained 1 cells, lost 2 in all" in line for line in lines)
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "new")]) == 1


def test_builds_of_different_rounds_or_without_their_checks_are_refused(tmp_path, capsys):
    ground = {(5, 5): 0}
    write(tmp_path / "old", ground, 100, (0, 10))
    cases = {"other rounds": {"inputs": {**INPUTS, "rounds_sha": "x" * 16}},
             "another walk mask": {"inputs": {**INPUTS, "walk_sha": "y" * 12}},
             "another preview rule": {"inputs": {**INPUTS, "preview_min_matches": 1}},
             "no record of its rounds": {"inputs": None},
             "built from the database": {"inputs": {**INPUTS, "rounds_sha": None}},
             "no checks": {"checks": False}}
    for name, kw in cases.items():
        write(tmp_path / name, ground, 100, (0, 10), **kw)
        old, new = compare.read(tmp_path / "old", "Toy"), compare.read(tmp_path / name, "Toy")
        assert compare.refusals(old, new), name
        assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / name)]) == 2, name
    out = capsys.readouterr().out
    assert "REFUSED" in out and "rounds_sha differs" in out and "no kill_lines result" in out
    assert "doesn't say which rounds it read" in out


def test_it_reads_an_older_assets_file_and_says_when_nothing_matches(tmp_path, capsys):
    write(tmp_path / "old", {(5, 5): 0}, 100, (0, 10))
    with np.load(tmp_path / "old" / "Toy.height.npz") as z:      # rewrite it as a version-1 file: 4 edge columns
        parts = {k: z[k] for k in z.files if k != "kind"}
    meta = json.loads(bytes(parts["meta"]).decode("utf-8"))
    meta["version"] = 1
    parts["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), np.uint8)
    parts["edges"] = np.zeros((0, 4), np.int32)
    np.savez_compressed(tmp_path / "old" / "Toy.height.npz", **parts)
    write(tmp_path / "new", {(5, 5): 0}, 100, (0, 10))
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "new")]) == 0
    assert "old: v1" in capsys.readouterr().out
    assert compare.main(["--old", str(tmp_path / "old"), "--new", str(tmp_path / "empty")]) == 2


# ---------------------------------------------------------------- one frozen set of rounds for both builds

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_control_heights import covered_rounds, toy_assets, write_blobs  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

import build_control_heights as command  # noqa: E402
import freeze_height_rounds as freeze  # noqa: E402
from app.models.replay import ReplayRound  # noqa: E402
from app.replays import format as fmt  # noqa: E402


def test_a_preview_records_what_it_read_and_two_builds_of_one_folder_agree(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "picture_path", lambda name: tmp_path / f"{name}.height.png")
    empty = tmp_path / "no-lines.json"
    empty.write_text('{"lines": []}', encoding="utf-8")
    monkeypatch.setattr(command, "MUST_BLOCK", empty)
    assets = toy_assets(tmp_path)
    blobs = write_blobs(tmp_path / "blobs", covered_rounds())
    base = ["--map", "Ascent", "--blobs-dir", str(blobs), "--preview"]
    assert command.main([*base, "--out", str(tmp_path / "a")], asset_dir=assets) == 0
    assert command.main([*base, "--out", str(tmp_path / "b")], asset_dir=assets) == 0
    a = json.loads((tmp_path / "a" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    b = json.loads((tmp_path / "b" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    assert a == b and (a["rounds"], a["matches"]) == (6, 2) and len(a["rounds_sha"]) == 16
    assert len(a["walk_sha"]) == 12 and len(a["sight_sha"]) == 12 and a["preview_min_matches"] is None
    assert compare.main(["--old", str(tmp_path / "a"), "--new", str(tmp_path / "b")]) == 0
    one = next(blobs.glob("*/1.json.gz"))
    one.write_bytes(fmt.encode_blob({**fmt.decode_blob(one.read_bytes()), "note": 1}))    # one round changes
    assert command.main([*base, "--out", str(tmp_path / "c")], asset_dir=assets) == 0
    c = json.loads((tmp_path / "c" / "Ascent.height.json").read_text(encoding="utf-8"))["inputs"]
    assert c["rounds_sha"] != a["rounds_sha"]
    assert compare.main(["--old", str(tmp_path / "a"), "--new", str(tmp_path / "c")]) == 2
    assert command.main([*base, "--preview-min-matches", "1", "--out", str(tmp_path / "d")], asset_dir=assets) == 0
    assert compare.main(["--old", str(tmp_path / "c"), "--new", str(tmp_path / "d")]) == 2, "another preview rule"


def test_freezing_writes_the_stored_bytes_and_a_changed_folder_is_refused(tmp_path, db, linked, monkeypatch, capsys):
    out = tmp_path / "frozen"
    manifest = freeze.freeze(db, linked.map_name, out, min_revision=0)
    rows = db.query(ReplayRound).filter(ReplayRound.replay_id == linked.id).all()
    uuid = str(linked.match_uuid).lower()
    assert manifest["rounds"] == len(rows) == len(list(out.glob("*/*.json.gz"))) and manifest["matches"] == 1
    assert all((out / uuid / f"{r.round_number}.json.gz").read_bytes() == bytes(r.data) for r in rows)
    on_disk = json.loads((out / f"{linked.map_name}.frozen.json").read_text(encoding="utf-8"))
    assert on_disk["rounds_sha"] == manifest["rounds_sha"] and len(on_disk["round_list"]) == len(rows)
    assert freeze.freeze(db, linked.map_name, tmp_path / "again", min_revision=0)["rounds_sha"] == manifest["rounds_sha"]
    assert freeze.freeze(db, linked.map_name, tmp_path / "none", min_revision=10 ** 6)["rounds"] == 0

    class Args:
        blobs_dir, map, preview_min_matches = out, linked.map_name, None

    from tests.replays.control_toys import open_hall

    rounds = [(uuid, r.round_number, None) for r in rows]
    assert command.build_inputs(Args, open_hall(), rounds)["rounds_sha"] == manifest["rounds_sha"]
    (out / uuid / "1.json.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="not what was frozen"):
        command.build_inputs(Args, open_hall(), rounds)
    assert freeze.main(["--map", linked.map_name, "--out", str(WEBAPP / "frozen-here")], session_factory=None) == 2, \
        "never inside the repository"
