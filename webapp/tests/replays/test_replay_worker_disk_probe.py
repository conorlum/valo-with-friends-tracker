"""The one-off disk probe (replay_tasks/disk_probe.py): what a Workflow run reports about its disk, the write test,
and the summary the dashboard shows."""

import ast
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from replay_tasks import disk_probe  # noqa: E402

MOUNTS = """overlay / overlay rw,relatime 0 0
proc /proc proc rw 0 0
tmpfs /tmp tmpfs rw,size=2097152k 0 0
/dev/sdb1 /mnt/my\\040disk ext4 rw 0 0
cgroup2 /sys/fs/cgroup cgroup2 rw 0 0
"""


def test_mounts_are_read_with_their_escapes_and_a_path_finds_its_own():
    mounts = disk_probe.parse_mounts(MOUNTS)
    assert mounts[3] == {"device": "/dev/sdb1", "point": "/mnt/my disk", "fs": "ext4"}
    assert disk_probe.mount_of("/tmp", mounts)["fs"] == "tmpfs"
    assert disk_probe.mount_of("/tmp/parse-x/export", mounts)["fs"] == "tmpfs"
    assert disk_probe.mount_of("/tmpfoo", mounts)["fs"] == "overlay"      # a prefix of the name is not a parent
    assert disk_probe.mount_of("/srv/replay_tasks", mounts)["fs"] == "overlay"
    assert disk_probe.mount_of("/mnt/my disk/x", mounts)["point"] == "/mnt/my disk"


def test_meminfo_reads_total_and_available_in_gb():
    assert disk_probe.meminfo("MemTotal:  4000000 kB\nMemFree: 1 kB\nMemAvailable: 2000000 kB\n") == {
        "MemTotal": 4.1, "MemAvailable": 2.05}
    assert disk_probe.meminfo(None) == {}


def test_the_write_test_writes_up_to_its_limit_and_leaves_nothing(tmp_path):
    got = disk_probe.write_test(str(tmp_path), 3 * (1 << 20) / 1e9, chunk=1 << 20)
    assert got["error"] is None and got["written_gb"] == round(3 * (1 << 20) / 1e9, 2)
    assert list(tmp_path.iterdir()) == []


def test_a_refused_write_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    got = disk_probe.write_test(str(blocker / "below"), 1.0)       # a folder under a file can't be made
    assert got["written_gb"] == 0 and got["error"]


def test_a_run_without_a_write_test_reports_its_disk_and_leftovers(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    (tmp_path / "parse-old").mkdir()
    (tmp_path / "parse-old" / "export.bin").write_bytes(b"\0" * 5000)
    found = disk_probe.run({"index": 3, "write_gb": 0})
    assert found["index"] == 3 and "write" not in found
    assert found["tempdir"] == str(tmp_path) and str(tmp_path) in found["paths"]
    assert found["paths"][str(tmp_path)]["total_gb"] > 0
    assert found["leftovers"][0]["name"] == "parse-old"


def test_a_run_writes_to_its_target(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_probe, "write_test", lambda folder, gb: {"folder": folder, "written_gb": gb})
    assert disk_probe.run({"target": str(tmp_path), "write_gb": 2})["write"] == {"folder": str(tmp_path),
                                                                                  "written_gb": 2.0}
    root = disk_probe.run({"target": "/", "write_gb": 1})["write"]
    assert root["folder"] == disk_probe.ROOT_SCRATCH


def test_the_plan_alternates_tmp_and_root():
    assert [p["target"] for p in disk_probe.plan(4, 15)] == ["/tmp", "/", "/tmp", "/"]
    assert [p["index"] for p in disk_probe.plan(3, 0)] == [0, 1, 2]
    assert len(disk_probe.plan(0, 0)) == 1


def test_the_summary_counts_machines_failures_and_what_each_folder_took():
    def answer(i, boot, folder, gb, error=None):
        return {"index": i, "boot_id": boot, "paths": {"/tmp": {"fs": "tmpfs", "total_gb": 2.1},
                                                       "/": {"fs": "overlay", "total_gb": 50.0}},
                "leftovers": [{"name": "parse-a", "gb": 1.5}], "write": {"folder": folder, "written_gb": gb,
                                                                         "error": error}}
    answers = [answer(0, "b1", "/tmp", 2.1, "OSError: [Errno 28]"), answer(1, "b1", "/disk-probe-scratch", 15.0),
               RuntimeError("Subtask failed: killed"), answer(3, "b2", "/tmp", 2.0, "OSError: [Errno 28]")]
    got = disk_probe.summarize(answers)
    assert got["runs"] == 4 and got["failed"] == 1 and got["machines"] == 2
    assert got["tmp_fs"] == ["tmpfs"] and got["root_total_gb"] == ["50.0"]
    assert got["written_gb_by_folder"] == {"/tmp": [2.0, 2.1], "/disk-probe-scratch": [15.0]}
    assert got["rows"][2] == {"index": 2, "failed": "RuntimeError: Subtask failed: killed"}
    assert got["rows"][0]["leftover_gb"] == 1.5 and got["reports"][2] is None


def test_the_probe_tasks_are_registered_with_disk_on_pro():
    tree = ast.parse((REPO / "replay_tasks" / "main.py").read_text(encoding="utf-8"))
    tasks = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "task":
            kw = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
            tasks[kw["name"]] = kw
    assert tasks["disk"]["plan"] == "pro"
    assert "plan" not in tasks["disk_probe"]            # it only waits for the others: flex
    probe = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    assert probe.args.args[0].arg == "ctx" and [d.value for d in probe.args.defaults] == [10, 15]


def test_the_probe_reads_nothing_from_the_worker():
    source = (REPO / "replay_tasks" / "disk_probe.py").read_text(encoding="utf-8")
    assert "urllib" not in source and "WorkerLink" not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            assert all(n.split(".")[0] in sys.stdlib_module_names or n == "__future__" for n in names), names


def test_cleanup_of_root_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(disk_probe, "ROOT_SCRATCH", str(scratch))
    got = disk_probe.run({"target": "/", "write_gb": 0.002})
    assert got["write"]["error"] is None and not scratch.exists()
    assert os.path.exists(tmp_path)
