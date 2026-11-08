"""A one-off look at a Workflow run's disk: where a parse's scratch (the parser's export, ~65x the .vrf) can go.

Parse runs on Workflows failed with `[Errno 28] No space left on device` under `/tmp`. Nothing in the image sets
TMPDIR, so every task writes its scratch there. This answers, from inside real runs, how big `/tmp` and the other
filesystems of a `pro` run are, whether `/tmp` is memory (tmpfs), whether runs share a machine (and so another
run's leftovers), and how much can really be written before the run is refused, since a container's quota is not
always what `statvfs` reports.

Run it from the Render dashboard, no shell: the replay-tasks workflow -> `disk_probe` -> Run, input `[]`. That
starts 10 `disk` runs on `pro` at once, half writing test data to `/tmp`, half to the root filesystem, and its
result is the summary. `[5, 0]` would start 5 runs with no write test. Each `disk` run also prints its report to its
log before writing, then a line per GB written, so a run that is killed mid-write still says how far it got.

Standard library only; it reads nothing from and posts nothing to the replay worker.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

PATHS = ("/tmp", "/var/tmp", "/", "/srv", "/opt", "/root", "/home", "/dev/shm")
# Kernel and container bookkeeping filesystems: never where scratch could go.
PSEUDO = frozenset({"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "securityfs", "debugfs", "tracefs",
                    "pstore", "bpf", "configfs", "fusectl", "hugetlbfs", "binfmt_misc", "autofs", "nsfs",
                    "rpc_pipefs", "devtmpfs", "selinuxfs"})
GB = 1e9
CHUNK = 1 << 20                  # 1 MiB per write
CHUNKS_PER_SYNC = 256            # fsync every 256 MiB, so the bytes counted really reached the filesystem
ROOT_SCRATCH = "/disk-probe-scratch"


def _log(line: str) -> None:
    sys.stderr.write(f"disk probe: {line}\n")
    sys.stderr.flush()


def parse_mounts(text: str) -> list[dict]:
    """`/proc/mounts` as [{device, point, fs}], with the kernel's octal escapes (`\\040` for a space) undone."""
    def unescape(field: str) -> str:
        return field.encode("latin-1").decode("unicode_escape")

    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            found.append({"device": unescape(parts[0]), "point": unescape(parts[1]), "fs": parts[2]})
    return found


def mount_of(path: str, mounts: list[dict]) -> dict | None:
    """The mount `path` lives on: the longest mount point that is `path` or one of its parents (the last one
    mounted wins a tie, as the kernel's does)."""
    best = None
    target = path.rstrip("/") or "/"
    for mount in mounts:
        point = mount["point"].rstrip("/") or "/"
        if target == point or point == "/" or target.startswith(point + "/"):
            if best is None or len(point) >= len(best["point"].rstrip("/") or "/"):
                best = mount
    return best


def space(path: str) -> dict | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total_gb": round(usage.total / GB, 2), "free_gb": round(usage.free / GB, 2)}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def meminfo(text: str | None) -> dict:
    """MemTotal and MemAvailable in GB: a tmpfs `/tmp` is paid for out of these."""
    out = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable") and rest.split():
            out[key] = round(int(rest.split()[0]) * 1024 / GB, 2)
    return out


def _tree_bytes(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path, onerror=lambda error: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def leftovers(folder: str, limit: int = 40) -> list[dict]:
    """What is already in the temp folder when the run starts: on a fresh machine, nothing of ours. Biggest first."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            size = _tree_bytes(Path(entry.path)) if entry.is_dir(follow_symlinks=False) else entry.stat().st_size
            age = time.time() - entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        found.append({"name": entry.name, "gb": round(size / GB, 3), "age_s": round(age)})
    return sorted(found, key=lambda e: -e["gb"])[:limit]


def report() -> dict:
    """Everything about this run's disk that can be read without writing."""
    mounts = parse_mounts(_read("/proc/mounts") or "")
    paths = {}
    for path in (*PATHS, tempfile.gettempdir(), os.getcwd()):
        if path in paths or not os.path.exists(path):
            continue
        mount = mount_of(path, mounts)
        paths[path] = {"mount": mount["point"] if mount else None, "fs": mount["fs"] if mount else None,
                       **(space(path) or {})}
    filesystems = []
    for mount in mounts:
        if mount["fs"] in PSEUDO:
            continue
        filesystems.append({**mount, **(space(mount["point"]) or {})})
    uptime = (_read("/proc/uptime") or "").split()
    return {
        "host": socket.gethostname(),
        "boot_id": (_read("/proc/sys/kernel/random/boot_id") or "").strip() or None,
        "uptime_s": round(float(uptime[0])) if uptime else None,
        "cpus": os.cpu_count(),
        "memory_gb": meminfo(_read("/proc/meminfo")),
        "tmpdir_env": os.environ.get("TMPDIR"),
        "tempdir": tempfile.gettempdir(),
        "cwd": os.getcwd(),
        "paths": paths,
        "filesystems": filesystems,
        "leftovers": leftovers(tempfile.gettempdir()),
    }


def write_test(folder: str, limit_gb: float, *, chunk: int = CHUNK, clock=time.monotonic) -> dict:
    """Writes up to `limit_gb` into a new file in `folder` until the filesystem refuses, then deletes it. The free
    space a filesystem reports can be more than a container may use; this is what it actually took."""
    started = clock()
    written, error, next_log = 0, None, GB
    path = None
    block = os.urandom(chunk)          # random, so a compressing filesystem can't make the test look bigger
    try:
        os.makedirs(folder, exist_ok=True)
        handle, path = tempfile.mkstemp(prefix="disk-probe-", dir=folder)
        with os.fdopen(handle, "wb") as sink:
            count = 0
            while written + chunk <= limit_gb * GB:
                sink.write(block)
                count += 1
                if count % CHUNKS_PER_SYNC == 0:
                    sink.flush()
                    os.fsync(sink.fileno())
                written += chunk
                if written >= next_log:
                    _log(f"{folder}: {written / GB:.1f} GB written")
                    next_log += GB
            sink.flush()
            os.fsync(sink.fileno())
    except OSError as failure:
        error = f"{type(failure).__name__}: {failure}"
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass
    return {"folder": folder, "limit_gb": limit_gb, "written_gb": round(written / GB, 2), "error": error,
            "seconds": round(clock() - started, 1)}


def run(args: dict | None) -> dict:
    """One `disk` run: the report, logged before anything is written, then the write test on `target`."""
    args = args or {}
    found = report()
    found["index"] = args.get("index")
    _log(f"report {found}")
    target = args.get("target", "/tmp")
    write_gb = float(args.get("write_gb", 0))
    if write_gb > 0:
        folder = ROOT_SCRATCH if target == "/" else target
        found["write"] = write_test(folder, write_gb)
        if folder == ROOT_SCRATCH:
            shutil.rmtree(folder, ignore_errors=True)
        _log(f"write {found['write']}")
    return found


def plan(runs: int, write_gb: float) -> list[dict]:
    """The `disk` runs a probe starts: alternately writing to `/tmp` and to the root filesystem."""
    return [{"index": i, "target": "/tmp" if i % 2 == 0 else "/", "write_gb": write_gb} for i in range(max(1, runs))]


def summarize(answers: list) -> dict:
    """The probe's answer: one line per run, then what the runs agreed on and where they differed. A run that
    failed (killed for using too much disk, say) appears with its error."""
    rows = []
    for i, answer in enumerate(answers):
        if isinstance(answer, BaseException) or not isinstance(answer, dict):
            rows.append({"index": i, "failed": f"{type(answer).__name__}: {answer}"})
            continue
        paths = answer.get("paths", {})
        write = answer.get("write") or {}
        rows.append({
            "index": answer.get("index", i),
            "boot_id": answer.get("boot_id"),
            "tmp": paths.get("/tmp"),
            "root": paths.get("/"),
            "memory_gb": answer.get("memory_gb"),
            "leftover_gb": round(sum(e["gb"] for e in answer.get("leftovers", [])), 3),
            "write_folder": write.get("folder"),
            "written_gb": write.get("written_gb"),
            "write_error": write.get("error"),
        })
    ok = [r for r in rows if "failed" not in r]

    def distinct(key, field):
        return sorted({str((r.get(key) or {}).get(field)) for r in ok})

    by_folder: dict[str, list] = {}
    for r in ok:
        if r["write_folder"]:
            by_folder.setdefault(r["write_folder"], []).append(r["written_gb"])
    return {
        "runs": len(rows),
        "failed": len(rows) - len(ok),
        "machines": len({r["boot_id"] for r in ok if r["boot_id"]}),
        "tmp_fs": distinct("tmp", "fs"),
        "tmp_total_gb": distinct("tmp", "total_gb"),
        "root_fs": distinct("root", "fs"),
        "root_total_gb": distinct("root", "total_gb"),
        "written_gb_by_folder": {k: sorted(v) for k, v in by_folder.items()},
        "rows": rows,
        "reports": [a if isinstance(a, dict) else None for a in answers],
    }
