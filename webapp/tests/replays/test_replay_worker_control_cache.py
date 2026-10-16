"""The visibility caches a Workflow control run gets and posts back (replay_worker/control_cache.py)."""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from replay_worker import archive, control_cache  # noqa: E402


def touch(path: Path, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_inputs_are_the_maps_newest_caches_its_height_and_features(tmp_path):
    for i in range(5):
        touch(tmp_path / f"Ascent.{i:016x}.npz", age=i)
    touch(tmp_path / "Ascent.0000000000000009.tmp.npz")
    touch(tmp_path / "Haven.0000000000000001.npz")
    touch(tmp_path / "feature-rows" / f"Ascent.{'a' * 64}.npz")
    touch(tmp_path / "heights" / "Ascent.d1.height.npz")
    touch(tmp_path / "features" / "f1.json")
    got = control_cache.inputs(tmp_path, {"map": "Ascent", "height": "d1", "features": "f1"})
    assert sorted(got) == sorted([f"Ascent.{i:016x}.npz" for i in range(3)] +
                                 [f"feature-rows/Ascent.{'a' * 64}.npz", "heights/Ascent.d1.height.npz",
                                  "features/f1.json"])
    assert all(path.is_file() for path in got.values())


def test_inputs_skip_a_missing_height_or_features(tmp_path):
    assert control_cache.inputs(tmp_path, {"map": "Ascent", "height": "nope", "features": "nope"}) == {}


def test_valid_names_only_for_the_runs_map():
    assert control_cache.valid("Ascent.0123456789abcdef.npz", "Ascent")
    assert control_cache.valid(f"feature-rows/Ascent.{'b' * 64}.npz", "Ascent")
    for bad in ("Haven.0123456789abcdef.npz", "../archive/x.npz", "Ascent.0123456789abcdef.tmp.npz",
                "heights/Ascent.d1.height.npz", "Ascent.xyz.npz", "/abs/Ascent.0123456789abcdef.npz"):
        assert not control_cache.valid(bad, "Ascent"), bad


def test_store_replaces_atomically_and_prunes_to_kept(tmp_path):
    for i in range(3):
        touch(tmp_path / f"Ascent.{i:016x}.npz", age=10 + i)
    src = touch(tmp_path / "incoming.npz")
    control_cache.store(tmp_path, "Ascent.ffffffffffffffff.npz", src)
    names = sorted(p.name for p in tmp_path.glob("Ascent.*.npz"))
    assert len(names) == control_cache.KEPT and "Ascent.ffffffffffffffff.npz" in names
    assert "Ascent.0000000000000002.npz" not in names          # the oldest went
    assert not src.exists()


def test_store_refuses_a_file_past_the_reserve(tmp_path):
    touch(tmp_path / "Ascent.0000000000000001.npz")
    big = tmp_path / ".incoming" / "x.npz"
    big.parent.mkdir()
    big.write_bytes(b"y" * 100)
    assert control_cache.store(tmp_path, "Bind.0000000000000002.npz", big, reserve=50) is False
    assert not (tmp_path / "Bind.0000000000000002.npz").exists() and not big.exists()
    assert (tmp_path / "Ascent.0000000000000001.npz").exists()      # a refusal drops nothing


def sized(path: Path, size: int, age: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def test_a_full_cache_drops_old_generations_then_the_oldest_maps_newest(tmp_path):
    sized(tmp_path / "Ascent.0000000000000001.npz", 10, age=50)       # Ascent's newest
    sized(tmp_path / "Ascent.0000000000000002.npz", 10, age=60)       # an older generation
    sized(tmp_path / "Pearl.0000000000000001.npz", 10, age=900)       # a map that left the pool, long ago
    sized(tmp_path / "heights" / "Ascent.d1.height.npz", 10, age=999)  # never dropped
    src = sized(tmp_path / ".incoming" / "x.npz", 10, age=0)
    # 50 bytes with the new file, room for 30: the older Ascent generation goes first, then Pearl.
    assert control_cache.store(tmp_path, "Bind.0000000000000003.npz", src, reserve=30) is True
    left = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.npz"))
    assert left == ["Ascent.0000000000000001.npz", "Bind.0000000000000003.npz", "heights/Ascent.d1.height.npz"]


def test_a_full_cache_takes_a_maps_changed_visibility_cache(tmp_path):
    # A cache at its reserve, made only of each map's newest: before this fix every new file was refused, so a
    # map whose heights changed never warmed again.
    for i, name in enumerate(("Ascent", "Bind", "Haven")):
        sized(tmp_path / f"{name}.000000000000000{i}.npz", 10, age=100 - i)
    src = sized(tmp_path / ".incoming" / "x.npz", 10, age=0)
    assert control_cache.store(tmp_path, "Haven.00000000000000ff.npz", src, reserve=30) is True
    assert control_cache.has_visibility(tmp_path, "Haven")
    assert not (tmp_path / "Ascent.0000000000000000.npz").exists()    # the oldest went


def test_concurrent_stores_keep_kept_and_lose_nothing_mid_rename(tmp_path):
    import threading
    (tmp_path / ".incoming").mkdir()

    def put(i):
        src = tmp_path / ".incoming" / f"{i}.npz"
        src.write_bytes(b"z")
        assert control_cache.store(tmp_path, f"Ascent.{i:016x}.npz", src)
    threads = [threading.Thread(target=put, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(list(tmp_path.glob("Ascent.*.npz"))) == control_cache.KEPT


def test_has_visibility_and_clear_incoming(tmp_path):
    assert not control_cache.has_visibility(tmp_path, "Ascent")
    touch(tmp_path / f"feature-rows/Ascent.{'c' * 64}.npz")
    assert not control_cache.has_visibility(tmp_path, "Ascent")      # feature rows alone don't warm a map
    touch(tmp_path / "Ascent.0000000000000001.npz")
    assert control_cache.has_visibility(tmp_path, "Ascent")
    touch(tmp_path / ".incoming" / "left-over.npz")
    control_cache.clear_incoming(tmp_path)
    assert not any((tmp_path / ".incoming").iterdir())


def test_archive_budget_leaves_the_cache_reserve(tmp_path):
    a = archive.Archive(tmp_path, 100 * archive.GB, max_bytes=10, queue_size=1)
    before = a.budget()
    a.cache_reserve = control_cache.RESERVE
    assert before - a.budget() == control_cache.RESERVE
