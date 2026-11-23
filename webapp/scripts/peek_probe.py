"""Fetch public replays once, label local openings, report rates and score owner answers.

From webapp, run::

    python scripts/peek_probe.py run --limit 10 --jobs 2 --friends friends.json
    python scripts/peek_probe.py report --out %TEMP%/valo-peeks
    python scripts/peek_probe.py score %TEMP%/valo-peeks/answers.json

Every subcommand accepts --out (default: <system temp>/valo-peeks), confined to
system temp. Raw cached responses contain private names; generated rows name only
the friends roster. All requests are GETs, in one parent pool of four threads.
Workers only read cached responses. Coverage failures keep partial output and
make run exit 1. Definitive HTTP failures are cached, including unsupported
moments; 5xx, 408 and 429 are retried on the next run.

summary.json contains counts[type][opener], pooled[type], sanity[opener], and
players[player_id] = {label, types: {type: figures + bands + jiggled}}. Figures
use decided rows only; empty rates are null. Player intervals are null below n=5.
The count table includes null openers under a separate "none" column; rows retain
their original nulls. Missing "other" reasons are counted as "unspecified".
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import multiprocessing
from pathlib import Path
import re
import sys
import tempfile
import traceback
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import engine, geometry  # noqa: E402
from app.moments import peek_constants as pc, peeks  # noqa: E402
from app.replays import format as replay_format  # noqa: E402
from app.services import stats_math  # noqa: E402
from preview_control_live import link_for  # noqa: E402

SITE = "https://valowithfriendstracker.onrender.com"
_urlopen = urlopen
_GEOMETRIES = {}
UUID_PATTERN = r"[0-9a-f-]{36}"


class FetchError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")

    @property
    def retryable(self):
        return 500 <= self.status < 600 or self.status in (408, 429)


def _cache_path(url, cache_dir):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urlsplit(SITE).netloc:
        raise ValueError("only public site URLs are supported")
    parts = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []
    if any(p in (".", "..") for p in parts):
        raise ValueError("invalid cache path")
    # Each endpoint gets its own directory: the page and its round URLs coexist.
    path = Path(cache_dir).joinpath(parsed.netloc, *(quote(p, safe="") for p in parts))
    if parsed.query:
        path /= "query-" + quote(parsed.query, safe="")
    return path / "response"


def _read_cache(url, cache_dir):
    path = _cache_path(url, cache_dir)
    status = int(path.with_suffix(".status").read_text(encoding="ascii"))
    if status != 200:
        raise FetchError(status)
    return path.read_bytes()


def fetch(url, cache_dir):
    """GET with caching, except for retryable HTTP failures."""
    path = _cache_path(url, cache_dir)
    if path.is_file() and path.with_suffix(".status").is_file():
        try:
            return _read_cache(url, cache_dir)
        except FetchError as exc:
            # Earlier probe versions cached transient errors; allow recovery.
            if not exc.retryable:
                raise
    try:
        response = _urlopen(Request(url, headers={"Accept-Encoding": "gzip"}, method="GET"), timeout=120)
    except HTTPError as exc:
        response = exc
    with response as handle:
        status = handle.status if hasattr(handle, "status") else handle.getcode()
        raw = handle.read()
    error = FetchError(status) if status != 200 else None
    if error is not None and error.retryable:
        raise error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.with_suffix(".status").write_text(str(status), encoding="ascii")
    if error is not None:
        raise error
    return raw


def _text(raw):
    return (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")


def page_data(uuid, fetch):
    """Parse replay-data using this script's cache-aware fetch callable."""
    html = _text(fetch(f"{SITE}/replays/{uuid}"))
    found = re.search(r'<script id="replay-data" type="application/json">(.*?)</script>', html, re.S)
    if found is None:
        raise ValueError("no replay-data script")
    return json.loads(found.group(1))


def _error(exc):
    # Exception messages can quote raw response data or a player's name. Keep
    # the type and HTTP status; traceback frames below identify the code path.
    return str(exc) if isinstance(exc, FetchError) else type(exc).__name__


def _log_error(exc, uuid, n=None):
    print(f"{uuid} round {n}: {_error(exc)}", file=sys.stderr)
    print("Traceback (most recent call last):", file=sys.stderr)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}', file=sys.stderr)


def _coverage():
    return {"discovered": 0, "completed": [], "skipped": {"unlinked": [], "no_moments": []}, "failed": []}


def _round_ids(uuid, numbers):
    return [{"uuid": uuid, "round": n} for n in numbers]


def _fetch_matches(args, out, coverage):
    """The sole network phase. No worker or geometry computation runs here."""
    cache = out / "cache"
    tasks = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        if args.uuids is None:
            try:
                raw = executor.submit(fetch, SITE + "/replays", cache).result()
                uuids = sorted(set(re.findall(r"/replays/(" + UUID_PATTERN + r")", _text(raw))))
            except Exception as exc:
                coverage["replays"]["failed"].append({"uuid": None, "error": _error(exc)})
                return []
        else:
            uuids = sorted(set(args.uuids))
        if args.limit is not None:
            uuids = uuids[:args.limit]
        coverage["replays"]["discovered"] = len(uuids)
        pages = {u: executor.submit(page_data, u, lambda url: fetch(url, cache)) for u in uuids}
        linked = {}
        for uuid, future in pages.items():
            try:
                ctx = future.result()
                numbers = sorted(int(n) for n in ctx["rounds"])
                coverage["rounds"]["discovered"] += len(numbers)
                if not ctx["players"] or not numbers:
                    coverage["replays"]["skipped"]["unlinked"].append(uuid)
                    coverage["rounds"]["skipped"]["unlinked"] += _round_ids(uuid, numbers)
                else:
                    linked[uuid] = (ctx, numbers)
            except Exception as exc:
                coverage["replays"]["failed"].append({"uuid": uuid, "error": _error(exc)})
        moments = {u: executor.submit(fetch, f"{SITE}/replays/{u}/moments.json", cache) for u in linked}
        supported = {}
        for uuid, future in moments.items():
            ctx, numbers = linked[uuid]
            try:
                future.result()  # Coverage only: never use its names or fights.
                asset = ctx["match"]["asset"]
                if not isinstance(asset, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", asset):
                    raise ValueError("invalid map asset")
                supported[uuid] = (asset, numbers)
            except Exception as exc:
                if isinstance(exc, FetchError) and not exc.retryable:
                    coverage["replays"]["skipped"]["no_moments"].append(uuid)
                    coverage["rounds"]["skipped"]["no_moments"] += _round_ids(uuid, numbers)
                else:
                    coverage["replays"]["failed"].append({"uuid": uuid, "error": _error(exc)})
                    coverage["rounds"]["failed"] += [dict(r, error=_error(exc)) for r in _round_ids(uuid, numbers)]
        maps = {asset: executor.submit(fetch, f"{SITE}/static/img/maps/{asset}.png", cache)
                for asset in sorted({a for a, _ in supported.values()})}
        rounds = {(u, n): executor.submit(fetch, f"{SITE}/replays/{u}/{n}.json", cache)
                  for u, (_, numbers) in supported.items() for n in numbers}
        map_errors = {}
        for asset, future in maps.items():
            try:
                raw = future.result()
                (out / "maps").mkdir(exist_ok=True)
                (out / "maps" / f"{asset}.png").write_bytes(raw)
            except Exception as exc:
                map_errors[asset] = _error(exc)
        for uuid, (asset, numbers) in supported.items():
            good, errors = [], []
            for n in numbers:
                try:
                    rounds[uuid, n].result()
                    if asset in map_errors:
                        raise ValueError("map fetch failed")
                    good.append(n)
                except Exception as exc:
                    error = map_errors.get(asset, _error(exc))
                    coverage["rounds"]["failed"].append({"uuid": uuid, "round": n, "error": error})
                    errors.append(error)
            # Compute the successful rounds even when another round failed.
            tasks.append({"uuid": uuid, "rounds": good, "out": str(out), "fetch_failed": bool(errors)})
    return tasks


def _display_players(ctx, friends):
    players = {int(k): v for k, v in ctx["players"].items()}
    named = {s for s, p in players.items() if p["name"].casefold() in friends}
    teams = {players[s]["team"] for s in named}
    labels = {}
    for s, p in players.items():
        enemy = bool(teams) and p["team"] not in teams
        labels[s] = p["name"] if s in named else ("the enemy " if enemy else "") + p["agent"]
    return players, named, labels


def compute_match(task):
    """Spawn-safe worker entry point: reads cache only, with per-process geometry."""
    uuid, out = task["uuid"], Path(task["out"])
    result = {"uuid": uuid, "rows": [], "players": {}, "completed": [], "failed": [], "error": None}
    try:
        ctx = page_data(uuid, lambda url: _read_cache(url, out / "cache"))
        players, named, labels = _display_players(ctx, task["friends"])
        match = ctx["match"]
        result["players"] = {str(players[s]["player_id"]): labels[s] for s in sorted(named)}
        match_id, asset = match["match_id"], match["asset"]
    except Exception as exc:
        _log_error(exc, uuid)
        result["error"] = _error(exc)
        result["failed"] = [dict(r, error=_error(exc)) for r in _round_ids(uuid, task["rounds"])]
        return result
    for n in task["rounds"]:
        try:
            raw = _read_cache(f"{SITE}/replays/{uuid}/{n}.json", out / "cache")
            blob = json.loads(raw) if raw[:1] == b"{" else replay_format.decode_blob(raw)
            key = (str(out), blob["map"])
            if key not in _GEOMETRIES:
                _GEOMETRIES[key] = geometry.visibility(geometry.load_geometry(blob["map"]), out / "geometry-cache")
            link = link_for(ctx, n)
            control_link = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                              db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
            rnd = engine.RoundInputs(blob, _GEOMETRIES[key], control_link)
            rows = []
            for opening in peeks.round_openings(rnd, blob):
                slot, opponent = opening["slot"], opening["opponent"]
                rows.append({**opening, "uuid": uuid, "round": n, "player_id": players[slot]["player_id"],
                             "agent": players[slot]["agent"], "match_id": match_id, "asset": asset,
                             "friend": slot in named, "player_label": labels[slot],
                             "opponent_label": None if opponent is None else labels[opponent]})
            result["rows"].extend(rows)
            result["completed"].append({"uuid": uuid, "round": n})
        except Exception as exc:
            _log_error(exc, uuid, n)
            result["failed"].append({"uuid": uuid, "round": n, "error": _error(exc)})
    return result


def _rate_interval(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["match_id"]].append(int(r["result"] == "won"))
    def rate(clusters):
        n = sum(len(c) for c in clusters)
        return sum(sum(c) for c in clusters) / n if n else None
    return list(stats_math.cluster_bootstrap_ci(rate, dict(groups), draws=1000, seed=0))


def _figures(rows, pooled=None, minimum=5):
    decided = [r for r in rows if r["result"] in ("won", "lost")]
    n = len(decided)
    wins = sum(r["result"] == "won" for r in decided)
    return {"n": n, "wins": wins, "rate": wins / n if n else None,
            "shrunk": (wins + pc.SHRINK_K * pooled) / (n + pc.SHRINK_K) if pooled is not None else None,
            "interval": _rate_interval(decided) if n >= minimum else None}


def aggregate(rows):
    """Pure aggregate; friend booleans alone authorize per-player figures."""
    rows = list(rows)
    by_type = {t: [r for r in rows if r["type"] == t] for t in pc.TYPES}
    pooled = {t: _figures(rs) for t, rs in by_type.items()}
    players = {}
    for pid in sorted({r["player_id"] for r in rows if r["friend"]}, key=str):
        own = [r for r in rows if r["friend"] and r["player_id"] == pid]
        types = {}
        for t in pc.TYPES:
            rs = [r for r in own if r["type"] == t]
            prior = pooled[t]["rate"]
            types[t] = {**_figures(rs, prior),
                        "bands": {b: _figures([r for r in rs if r["band"] == b], prior)
                                  for b in ("close", "mid", "long")},
                        "jiggled": {label: _figures([r for r in rs if bool(r["jiggles"]) == value], prior)
                                    for label, value in (("yes", True), ("no", False))}}
        players[str(pid)] = {"label": own[0]["player_label"], "types": types}
    return {"counts": {t: {op: sum((r["opener"] or "none") == op for r in rs) for op in (*pc.OPENERS, "none")}
                       for t, rs in by_type.items()},
            "pooled": pooled, "players": players,
            "sanity": {op: _figures([r for r in rows if r["opener"] == op], minimum=1)
                       for op in ("peeker", "holder")},
            "other_reasons": dict(sorted(Counter(r["reason"] or "unspecified" for r in by_type["other"]).items()))}


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _percent(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _interval_text(interval):
    return "CI n/a" if interval is None else f"CI {_percent(interval[0])}–{_percent(interval[1])}"


def report(rows, out):
    summary = aggregate(rows)
    _write_json(out / "summary.json", summary)
    print("Type × opener counts:")
    for t, counts in summary["counts"].items():
        print(t + ": " + ", ".join(f"{op}={n}" for op, n in counts.items()))
    print("Other reasons: " + json.dumps(summary["other_reasons"]))
    p, h = summary["sanity"]["peeker"], summary["sanity"]["holder"]
    print(f"peeker won {_percent(p['rate'])} ({_interval_text(p['interval'])}) of {p['n']}, "
          f"holder {_percent(h['rate'])} ({_interval_text(h['interval'])}) of {h['n']}")
    print("Player | type / split | n | wins | raw | shrunk | raw interval")
    for player in summary["players"].values():
        for t, figures in player["types"].items():
            splits = [(t, figures)] + [(f"{t} / {b}", f) for b, f in figures["bands"].items()]
            splits += [(f"{t} / jiggled {j}", f) for j, f in figures["jiggled"].items()]
            for label, f in splits:
                if f["n"]:
                    print(f"{player['label']} | {label} | {f['n']} | {f['wins']} | {_percent(f['rate'])} | "
                          f"{_percent(f['shrunk'])} | {_interval_text(f['interval'])}")
    return summary


def score(rows, answers):
    confusion = defaultdict(Counter)
    for r in rows:
        key = f"{r['uuid']}|{r['round']}|{r['slot']}|{r['i']}"
        if key in answers:
            confusion[r["type"]][answers[key]] += 1
    def figures(n, right):
        return {"n": n, "right": right, "rate": right / n if n else None}
    by_type = {t: figures(sum(cs.values()), cs[t]) for t, cs in sorted(confusion.items())}
    return {"overall": figures(sum(f["n"] for f in by_type.values()), sum(f["right"] for f in by_type.values())),
            "by_type": by_type, "confusion": {t: dict(sorted(cs.items())) for t, cs in sorted(confusion.items())}}


def _read_rows(out):
    return [json.loads(line) for line in (out / "openings.jsonl").read_text(encoding="utf-8").splitlines() if line]


def _run(args, out):
    friends = frozenset(name.casefold() for name in json.loads(args.friends.read_text(encoding="utf-8")))
    coverage = {"replays": _coverage(), "rounds": _coverage()}
    tasks = _fetch_matches(args, out, coverage)
    for task in tasks:
        task["friends"] = friends
    _GEOMETRIES.clear()
    if args.jobs == 1:
        results = [compute_match(task) for task in tasks]
    elif tasks:
        with multiprocessing.Pool(args.jobs) as pool:
            results = pool.map(compute_match, tasks, chunksize=1)
    else:
        results = []
    rows, players = [], {}
    for task, result in zip(tasks, results):
        uuid = result["uuid"]
        rows.extend(result["rows"])
        players.update(result["players"])
        coverage["rounds"]["completed"].extend(result["completed"])
        coverage["rounds"]["failed"].extend(result["failed"])
        if task["fetch_failed"] or result["failed"] or result["error"]:
            coverage["replays"]["failed"].append({"uuid": uuid, "error": result["error"] or "round failure"})
        else:
            coverage["replays"]["completed"].append(uuid)
    (out / "openings.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows),
                                        encoding="utf-8")
    _write_json(out / "players.json", players)
    _write_json(out / "coverage.json", coverage)
    for kind, c in coverage.items():
        print(f"{kind}: discovered {c['discovered']}, completed {len(c['completed'])}, "
              f"skipped {sum(map(len, c['skipped'].values()))} {json.dumps(c['skipped'])}, failed {len(c['failed'])}")
    report(rows, out)
    return int(any(c["failed"] for c in coverage.values()))


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--uuids", nargs="+")
    run.add_argument("--limit", type=_positive)
    run.add_argument("--jobs", type=_positive, default=2)
    run.add_argument("--friends", type=Path, default=WEBAPP_ROOT / "scripts" / "tracked_players.json")
    report_parser = commands.add_parser("report")
    score_parser = commands.add_parser("score")
    score_parser.add_argument("answers", type=Path)
    for command in (run, report_parser, score_parser):
        command.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "valo-peeks")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        parser.error("--out must be under the system temp folder")
    if args.command == "run" and args.uuids and any(not re.fullmatch(UUID_PATTERN, u) for u in args.uuids):
        parser.error("invalid replay uuid")
    out.mkdir(parents=True, exist_ok=True)
    if args.command == "run":
        return _run(args, out)
    rows = _read_rows(out)
    if args.command == "report":
        report(rows, out)
    else:
        results = score(rows, json.loads(args.answers.read_text(encoding="utf-8")))
        for label, f in [("overall", results["overall"]), *results["by_type"].items()]:
            print(f"{label}: {_percent(f['rate'])} right, n={f['n']}")
        print("Confusion (rule × owner): " + json.dumps(results["confusion"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
