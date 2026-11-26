"""Measure public clutch replays locally; all writable paths must be in system temp.

From webapp::

    python scripts/clutch_review.py run --limit 10 --jobs 2
    python scripts/clutch_review.py report --out %TEMP%/valo-clutch
    python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/clutch_review.py export-win

Probe: python scripts/clutch_review.py run --limit 1 --jobs 1

run fetches through peek_probe's cache in one parent pool of four threads, then
workers read cache only. report reuses rows.jsonl, cutoffs.jsonl and coverage.json.
export-win alone reads the DB; run/report use win_model.json when available.
Raw cached pages stay in temp; exported rows contain roster labels or numbers.
"""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import re
import sys
import tempfile
import types

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(WEBAPP_ROOT / "scripts"))

import clutch_measures as measures  # noqa: E402
import clutch_stats as stats  # noqa: E402
from app.coach.roundview import RoundView  # noqa: E402
from app.control import engine, geometry  # noqa: E402
from app.replays import format as replay_format  # noqa: E402


def _load_probe():
    """Reuse the probe's definitions without its DB-linked preview import.

    peek_probe currently imports preview_control_live at module scope. Only
    execute the explicitly named stdlib imports, constants and fetch definitions;
    no application imports or unrelated top-level statements are executed.
    """
    source = WEBAPP_ROOT / "scripts" / "peek_probe.py"
    names = {"FetchError", "_cache_path", "_read_cache", "fetch", "_text", "page_data", "_error"}
    constants = {"SITE", "_urlopen", "UUID_PATTERN"}
    imports = {"gzip", "json", "re", "pathlib", "urllib.error", "urllib.parse", "urllib.request"}
    nodes = []
    for node in ast.parse(source.read_text(encoding="utf-8"), filename=str(source)).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Import) and all(alias.name in imports for alias in node.names):
            nodes.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in imports:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            nodes.append(node)
    module = types.ModuleType("_clutch_probe")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), module.__dict__)
    sys.modules[module.__name__] = module
    return module


probe = _load_probe()
_GEOMETRIES = {}
REPLAY_STATUSES = ("measured", "skipped_unlinked", "failed")
ROUND_STATUSES = ("measured", "no_situation", "skipped_unlinked", "skipped_no_winner", "failed")


def _record(uuid, status, n=None, error=None):
    row = {"uuid": uuid, "status": status}
    if n is not None:
        row["round"] = n
    if error is not None:
        row["error"] = error
        print(f"{uuid} round {n}: {error}", file=sys.stderr)
    return row


def _has_situation(page):
    return any((a == 1 and b >= 1) or (b == 1 and a >= 1)
               or (a == 2 and b in (2, 3)) or (b == 2 and a in (2, 3))
               for _, a, b in page.get("alive_steps", []))


def _fetch_matches(args, coverage):
    """Sole network phase. Decode no blobs and close the pool before computing."""
    tasks, contexts = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        if args.uuids is None:
            try:
                raw = pool.submit(probe.fetch, probe.SITE + "/replays", args.cache).result()
                uuids = sorted(set(re.findall(r"/replays/(" + probe.UUID_PATTERN + r")", probe._text(raw))))
            except Exception as exc:
                coverage["replay_status"].append(_record(None, "failed", error=probe._error(exc)))
                return tasks
        else:
            uuids = sorted(set(args.uuids))
        if args.limit is not None:
            uuids = uuids[:args.limit]
        pages = {u: pool.submit(probe.page_data, u, lambda url: probe.fetch(url, args.cache)) for u in uuids}
        for uuid, future in pages.items():
            numbers = []
            try:
                ctx = future.result()
                # Unlinked pages carry round numbers only in match.rounds.
                numbers = sorted({int(n) for n in ctx["match"].get("rounds", [])} | {int(n) for n in ctx["rounds"]})
                if not ctx["players"] or ctx["match"].get("linked") is False:
                    coverage["replay_status"].append(_record(uuid, "skipped_unlinked"))
                    coverage["round_status"].extend(_record(uuid, "skipped_unlinked", n) for n in numbers)
                    continue
                if any(str(n) not in ctx["rounds"] for n in numbers):
                    raise ValueError("missing linked round metadata")
                ctx["match"]["uuid"] = uuid
                # Validate label inputs before any worker can emit partial rows.
                stats.labels([ctx], ())
                contexts.append(ctx)
                tasks.append({"uuid": uuid, "rounds": [],
                              "cache": str(args.cache), "geometry_cache": str(args.geometry_cache),
                              "missing_control": []})
            except Exception as exc:
                error = probe._error(exc)
                coverage["replay_status"].append(_record(uuid, "failed", error=error))
                coverage["round_status"].extend(_record(uuid, "failed", n, error) for n in numbers)
        labels = stats.labels(contexts, args.friends)
        futures = {}
        for task, ctx in zip(tasks, contexts):
            uuid = task["uuid"]
            task["labels"] = labels[uuid]
            for key, page in sorted(ctx["rounds"].items(), key=lambda kv: int(kv[0])):
                n = int(key)
                if page.get("db", {}).get("winner") not in ("team-1", "team-2"):
                    coverage["round_status"].append(_record(uuid, "skipped_no_winner", n))
                    continue
                blob = pool.submit(probe.fetch, f"{probe.SITE}/replays/{uuid}/{n}.json", args.cache)
                control = (pool.submit(probe.fetch, f"{probe.SITE}/replays/{uuid}/{n}/control.bin", args.cache)
                           if _has_situation(page) else None)
                futures[uuid, n] = blob, control
        for task, ctx in zip(tasks, contexts):
            uuid = task["uuid"]
            for n in sorted(int(key) for key in ctx["rounds"]):
                if (uuid, n) not in futures:
                    continue
                blob, control = futures[uuid, n]
                try:
                    blob.result()
                    if control is not None:
                        try:
                            control.result()
                        except probe.FetchError as exc:
                            if exc.status != 404:
                                raise
                            task["missing_control"].append(n)
                    task["rounds"].append(n)
                except Exception as exc:
                    coverage["round_status"].append(_record(uuid, "failed", n, probe._error(exc)))
    return tasks


def _measure_round(task, ctx, n, blob, caps):
    rows, cutoffs = measures.situations(ctx, n, blob)
    cutoffs = [dict(row, uuid=task["uuid"], round=n) for row in cutoffs]
    if not rows and not cutoffs:
        return [], [], "no_situation", False
    cache = Path(task["cache"])
    ctl = None
    if n not in task["missing_control"]:
        ctl = measures.ControlRound(probe._read_cache(f"{probe.SITE}/replays/{task['uuid']}/{n}/control.bin", cache))
    if not rows:
        return [], cutoffs, "measured", ctl is None or not ctl.has_unknown
    key = (task["geometry_cache"], blob["map"])
    if key not in _GEOMETRIES:
        _GEOMETRIES[key] = geometry.visibility(geometry.load_geometry(blob["map"]), Path(task["geometry_cache"]))
    geo = _GEOMETRIES[key]
    roles = {int(s): side for s, side in measures.sides(ctx, n).items()}
    deaths = measures.db_deaths(ctx, n)
    rnd = engine.RoundInputs(blob, geo, engine.ControlLink(sides=roles, db_deaths=tuple(map(tuple, deaths))))
    players = {int(s): p for s, p in ctx["players"].items()}
    view = RoundView(blob, {s: str(s) for s in players}, geo.uv_per_unit, ctx["match"]["asset"],
                     groups={s: p["side"] for s, p in players.items()}, roles=roles, db_deaths=deaths)
    plant = ctx["rounds"][str(n)]["db"].get("plant")  # Page times already use the replay clock.
    if plant is None:
        plant = next((e["t"] for e in (blob.get("player_state") or {}).get("spike", [])
                      if e["s"] == "planted"), None)
    for row in rows:
        slot, start, end = row["slot"], row["t_s"], row["t_end_window"]
        enemies = view.enemies(slot)
        engagements = measures.engagements(rnd, blob, slot, start, end)
        for engagement in engagements:
            engagement.update(measures.isolation(rnd, slot, engagement["t"], enemies))
        clock = measures.clock(view, rnd, blob, row)
        if engagements:
            clock["time_left_first_engagement"] = measures.time_left(blob, engagements[0]["t"])
        death = next((t for s, t in rnd.deaths() if s == slot and start <= t <= rnd.t_decided), None)
        row.update(uuid=task["uuid"], round=n, match_id=ctx["match"]["match_id"],
                   clock_offset=ctx["match"].get("clock_offset") or 0, t_plant=plant,
                   label=task["labels"][slot], agent=players[slot]["agent"],
                   enemy_agents={s: players[s]["agent"] for s in enemies if view.alive(s, start)}, clock=clock,
                   state=measures.state(view, rnd, blob, ctx, n, row),
                   isolation=measures.isolation_summary(engagements), engagement_rows=engagements,
                   unknown=(measures.unknown(rnd, ctl, row) if ctl else dict.fromkeys(
                       ("unknown_share_start", "unknown_share_end", "into_unknown_share"))),
                   backshots=measures.backshots(rnd, blob, slot, start, end),
                   information=(measures.information(ctl, rnd.team[slot], engagements)
                                if ctl else {"located_share": None}),
                   fakes=measures.fakes(view, rnd, blob, row, engagements),
                   util=measures.util(rnd, blob, slot, start, death, caps.get(slot), agent=players[slot]["agent"]))
    return rows, cutoffs, "measured", ctl is None or not ctl.has_unknown


def compute_match(task):
    """Spawn-safe worker. Reads the cache only; failed rounds do not stop siblings."""
    uuid, cache = task["uuid"], Path(task["cache"])
    result = {"rows": [], "cutoffs": [], "round_status": [], "without_unknown_streams": 0}
    blobs = {}
    try:
        ctx = probe.page_data(uuid, lambda url: probe._read_cache(url, cache))
    except Exception as exc:
        result["round_status"] = [_record(uuid, "failed", n, probe._error(exc)) for n in task["rounds"]]
        result["error"] = probe._error(exc)
        return result
    for n in task["rounds"]:
        try:
            raw = probe._read_cache(f"{probe.SITE}/replays/{uuid}/{n}.json", cache)
            blobs[n] = json.loads(raw) if raw.startswith(b"{") else replay_format.decode_blob(raw)
        except Exception as exc:
            result["round_status"].append(_record(uuid, "failed", n, probe._error(exc)))
    try:
        caps = measures.ult_caps(blobs, {int(s): p["agent"] for s, p in ctx["players"].items()})
    except Exception as exc:
        result["round_status"].extend(_record(uuid, "failed", n, probe._error(exc)) for n in blobs)
        return result
    for n, blob in blobs.items():
        try:
            rows, cutoffs, status, without = _measure_round(task, ctx, n, blob, caps)
            result["rows"].extend(rows)
            result["cutoffs"].extend(cutoffs)
            result["round_status"].append(_record(uuid, status, n))
            result["without_unknown_streams"] += int(without)
        except Exception as exc:
            result["round_status"].append(_record(uuid, "failed", n, probe._error(exc)))
    return result


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows), encoding="utf-8")


def _read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _analyse(out, rows, cutoffs, coverage):
    path = out / "win_model.json"
    model = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    note = stats.NO_EXPORT
    if model is not None:
        excluded = set(model.get("excluded") or [])
        covered = {r["match_id"] for r in rows} <= excluded
        note = (f"Stored win model version={model['version']}, n={model['n']}, cells={len(model['cells'])}; "
                f"every measured replay match excluded: {covered}.")
    stats.expected(rows, model)
    stats.fit_leave_one_match_out(rows)
    results = [stats.compare(rows, measure, group) for measure in stats.MEASURES for group in stats.GROUPS]
    stats.write_report(out, rows, results, coverage, cutoffs, note)
    stats.write_csv(out, rows)


def _run(args, out):
    args.friends = json.loads(args.friends.read_text(encoding="utf-8"))
    coverage = {"replay_status": [], "round_status": [], "without_unknown_streams": 0}
    tasks = _fetch_matches(args, coverage)
    _GEOMETRIES.clear()
    if args.jobs == 1:
        results = [compute_match(task) for task in tasks]
    elif tasks:
        with multiprocessing.get_context("spawn").Pool(args.jobs) as pool:
            results = pool.map(compute_match, tasks, chunksize=1)
    else:
        results = []
    rows, cutoffs = [], []
    for task, result in zip(tasks, results):
        rows.extend(result["rows"])
        cutoffs.extend(result["cutoffs"])
        coverage["round_status"].extend(result["round_status"])
        coverage["without_unknown_streams"] += result["without_unknown_streams"]
        failures = [r for r in coverage["round_status"] if r["uuid"] == task["uuid"] and r["status"] == "failed"]
        error = result.get("error") or (failures[0]["error"] if failures else None)
        coverage["replay_status"].append(_record(task["uuid"], "failed" if error else "measured", error=error))
    for kind, statuses in (("replays", REPLAY_STATUSES), ("rounds", ROUND_STATUSES)):
        records = coverage["replay_status" if kind == "replays" else "round_status"]
        records.sort(key=lambda r: (r["uuid"] or "", r.get("round", 0)))
        coverage[kind] = {"discovered": len(records), **{s: sum(r["status"] == s for r in records) for s in statuses}}
        print(f"{kind}: " + ", ".join(f"{key}={value}" for key, value in coverage[kind].items()))
    _write_json(out / "coverage.json", coverage)
    _write_rows(out / "cutoffs.jsonl", cutoffs)
    _analyse(out, rows, cutoffs, coverage)
    _write_rows(out / "rows.jsonl", rows)
    return int(coverage["replays"]["failed"] > 0 or coverage["rounds"]["failed"] > 0)


def _export_win(out):
    """The only DB entry point; invoke through with_friends_db.py --read-only."""
    from app.db import SessionLocal
    from app.services import util_model

    with SessionLocal() as db:
        model = util_model.load(db)
        replay_matches = util_model.replay_match_ids(db)
    if model is None:
        print("No stored win model", file=sys.stderr)
        return 1
    _write_json(out / "win_model.json", model)
    print(f"version={model['version']} n={model['n']} cells={len(model['cells'])}; "
          f"every replay match excluded: {util_model.covers(model, replay_matches)}")
    return 0


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--limit", type=_positive)
    run.add_argument("--uuids", nargs="+")
    run.add_argument("--jobs", type=_positive, default=2)
    run.add_argument("--friends", type=Path, default=WEBAPP_ROOT / "scripts" / "tracked_players.json")
    run.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "valo-peeks" / "cache")
    run.add_argument("--geometry-cache", type=Path, default=Path(tempfile.gettempdir()) / "valo-peeks" / "geometry-cache")
    for command in (run, commands.add_parser("report"), commands.add_parser("export-win")):
        command.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "valo-clutch")
    args = parser.parse_args(argv)
    for name in ("out", "cache", "geometry_cache"):
        if hasattr(args, name):
            path = getattr(args, name).resolve()
            if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                parser.error(f"--{name.replace('_', '-')} must be under the system temp folder")
            setattr(args, name, path)
    if args.command == "run" and args.uuids and any(not re.fullmatch(probe.UUID_PATTERN, u) for u in args.uuids):
        parser.error("invalid replay uuid")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    try:
        if args.command == "run":
            return _run(args, out)
        if args.command == "export-win":
            return _export_win(out)
        _analyse(out, _read_rows(out / "rows.jsonl"), _read_rows(out / "cutoffs.jsonl"),
                 json.loads((out / "coverage.json").read_text(encoding="utf-8")))
        return 0
    except Exception as exc:
        print(probe._error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
