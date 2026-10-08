"""Heights in the database (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md): what a rebuild
costs, the table's reads and writes, the active digest in a round's inputs, the gate, and the operator's
commands. SQLite, and no engine in this process."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WEBAPP / "scripts"))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)


def test_sizes_and_the_cycle_estimate(tmp_path):
    import measure_height_rebuild as measure

    for match, n, size in (("m1", 1, 1000), ("m1", 2, 3000), ("m2", 1, 2000)):
        (tmp_path / match).mkdir(exist_ok=True)
        (tmp_path / match / f"{n}.json.gz").write_bytes(b"x" * size)
    got = measure.sizes(tmp_path)
    assert got == {"matches": 2, "rounds": 3, "bytes": 6000, "largest": 3000}
    est = measure.estimate(got, build_s=30.0, warm_s=120.0, round_s=40.0, workers=2)
    assert est == {"sent_mb": 0.008, "batches": 1, "build_min": 0.5, "warm_min": 2.0, "rounds_min": 1.0,
                   "cycle_min": 3.5}


def test_cost_probe_uses_only_the_requested_maps_rounds_and_can_read_them_twice(tmp_path):
    import measure_height_rebuild as measure
    from app.replays import format as fmt

    for match, map_name in (("a-bind", "Bind"), ("z-ascent", "Ascent")):
        (tmp_path / match).mkdir()
        (tmp_path / match / "1.json.gz").write_bytes(fmt.encode_blob({"v": 1, "map": map_name}))
    rounds = measure.FrozenRounds(tmp_path, "Ascent")
    assert [p.parent.name for p in rounds.paths[:3]] == ["z-ascent"]  # the timing loop's exact selection
    for _ in range(2):
        assert [(m, n, b["map"]) for m, n, b in rounds] == [("z-ascent", 1, "Ascent")]
    assert measure.sizes(tmp_path, "Ascent")["rounds"] == 1
