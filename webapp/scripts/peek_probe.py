"""Fetch public replays once, label local openings, report rates and score owner answers.

From webapp, run::

    python scripts/peek_probe.py run --limit 10 --jobs 2 --friends friends.json
    python scripts/peek_probe.py report --out %TEMP%/valo-peeks
    python scripts/peek_probe.py page --out %TEMP%/valo-peeks --per-type 8 --seed 0
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
from html import escape
import json
import multiprocessing
from pathlib import Path
import random
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


def _answer_key(row):
    return f"{row['uuid']}|{row['round']}|{row['slot']}|{row['i']}"


def _moment(row):
    return next(row[k] for k in ("t_onset_exact", "t_onset", "t_event", "t0") if row.get(k) is not None)


def _paths(row, rnd):
    """Engine pixels; px_to_image multiplies the image's natural width/height.

    Null samples preserve missing tracks and life boundaries; the fourth value is
    the segment ID, so drawing also breaks across short gaps between grid times.
    The exact moment is kept separately for onset dots and sight.
    """
    ref = _moment(row)
    def point(slot, t):
        pos = rnd.pos(slot, t) if slot is not None and rnd.alive(slot, t) else None
        return [float(pos[0]), float(pos[1])] if pos is not None else [None, None]
    def track(slot):
        times = [ref - 2 + k / 16 for k in range(49)]
        samples = []
        for t in times:
            xy = point(slot, t)
            i = rnd._sample(slot, t) if xy[0] is not None else None
            segment = int(rnd._segment[slot][i]) if i is not None else None
            samples.append([t, *xy, segment])
        return samples
    opponent = row["opponent"]
    return {"player": track(row["slot"]), "opponent": track(opponent) if opponent is not None else [],
            "at_ref": [point(row["slot"], ref), point(opponent, ref)],
            "sight_line": row.get("t_onset") is not None,
            "px_to_image": 1 / geometry.PX, "m_per_px": float(rnd.geo.m_per_px)}


def compute_match(task):
    """Spawn-safe worker entry point: reads cache only, with per-process geometry."""
    uuid, out = task["uuid"], Path(task["out"])
    result = {"uuid": uuid, "rows": [], "paths": [], "players": {}, "completed": [], "failed": [], "error": None}
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
            rows, paths = [], []
            for opening in peeks.round_openings(rnd, blob):
                slot, opponent = opening["slot"], opening["opponent"]
                rows.append({**opening, "uuid": uuid, "round": n, "player_id": players[slot]["player_id"],
                             "agent": players[slot]["agent"], "match_id": match_id, "asset": asset,
                             "friend": slot in named, "player_label": labels[slot],
                             "opponent_label": None if opponent is None else labels[opponent]})
                paths.append({"key": _answer_key(rows[-1]), "paths": _paths(rows[-1], rnd)})
            result["rows"].extend(rows)
            result["paths"].extend(paths)
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
        key = _answer_key(r)
        if key in answers:
            confusion[r["type"]][answers[key]] += 1
    def figures(n, right):
        return {"n": n, "right": right, "rate": right / n if n else None}
    by_type = {t: figures(sum(cs.values()), cs[t]) for t, cs in sorted(confusion.items())}
    return {"overall": figures(sum(f["n"] for f in by_type.values()), sum(f["right"] for f in by_type.values())),
            "by_type": by_type, "confusion": {t: dict(sorted(cs.items())) for t, cs in sorted(confusion.items())}}


def _read_rows(out):
    return [json.loads(line) for line in (out / "openings.jsonl").read_text(encoding="utf-8").splitlines() if line]


def build_page(rows, players, out_dir, per_type=8, seed=0) -> Path:
    """Build the local review page without fetching or rebuilding round inputs."""
    out = Path(out_dir).resolve()
    if not out.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("--out must be under the system temp folder")
    if per_type < 1:
        raise ValueError("per_type must be at least 1")
    out.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    sidecar = out / "paths.jsonl"
    paths = {}
    if sidecar.exists():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if line:
                entry = json.loads(line)
                paths[entry["key"]] = entry["paths"]
    rng = random.Random(seed)
    cards, sections = [], []
    # Do not embed source rows wholesale: upstream/cache-only names and IDs are
    # deliberately excluded. Labels have already been made safe by the worker.
    fields = ("uuid", "round", "slot", "i", "opponent", "type", "reason", "opener", "result", "band", "jiggles",
              "t0", "t_event", "t_onset", "t_onset_exact", "speed_mps", "sideways_m", "exposure_s", "repeek_gap_s",
              "repeek_spot_m", "distance_m", "asset", "friend", "player_label", "opponent_label")
    def named(r):
        return bool(r["friend"] and str(r["player_id"]) in players)
    def label(t):
        return t.replace("_", " ")
    def card_html(r):
        safe = {k: r.get(k) for k in fields}
        if not named(r):
            agent = r.get("agent", "unknown agent")
            if safe["player_label"] not in (agent, "the enemy " + agent):
                safe["player_label"] = agent
        ref = _moment(r)
        key = _answer_key(r)
        url = f"{SITE}/replays/{r['uuid']}?round={r['round']}&t={max(0, ref - 2):.2f}"
        asset = r.get("asset", "")
        map_path = f"maps/{asset}.png" if re.fullmatch(r"[A-Za-z0-9_-]+", asset) else None
        cards.append({"key": key, "row": safe, "paths": paths.get(key), "t_ref": ref,
                      "viewer_url": url, "map": map_path})
        title = f"{safe['player_label']} vs {safe['opponent_label'] or 'no opponent seen'}"
        adjective = f" after {r['jiggles']} jiggle{'s' if r['jiggles'] != 1 else ''}" if r["jiggles"] else ""
        details = [f"Round {r['round']}", label(r["type"]) + adjective,
                   "opener: " + (r["opener"] or "none"), "result: " + r["result"]]
        if r.get("reason"):
            details.append("reason: " + label(r["reason"]))
        for field, name, unit in (("distance_m", "distance", "m"), ("speed_mps", "speed", "m/s"),
                                  ("sideways_m", "sideways", "m"), ("exposure_s", "exposure", "s"),
                                  ("repeek_gap_s", "re-peek gap", "s"), ("repeek_spot_m", "re-peek spot", "m")):
            if r.get(field) is not None:
                details.append(f"{name}: {r[field]:g} {unit}")
        if r.get("band") is not None:
            details.append("band: " + r["band"])
        options = ''.join(f'<option value="{escape(t)}">{escape(label(t))}</option>' for t in pc.TYPES)
        return (f'<article data-type="{escape(r["type"])}" data-key="{escape(key, quote=True)}">'
                f'<h3>{escape(title)}</h3><p>{escape(" · ".join(details))}</p>'
                '<canvas width="240" height="240" aria-label="Minimap paths"></canvas>'
                f'<p><a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open replay</a> '
                '<button class="play" type="button">Play</button></p>'
                '<button class="right" type="button">Right</button> '
                '<label>Correct type <select class="correction"><option value="">Choose type</option>'
                f'{options}</select></label><p class="answer" aria-live="polite"></p></article>')
    def examples(group):
        preferred = [r for r in group if named(r)]
        remaining = [r for r in group if not named(r)]
        chosen = rng.sample(preferred, min(per_type, len(preferred)))
        chosen += rng.sample(remaining, min(per_type - len(chosen), len(remaining)))
        return chosen
    for t in pc.TYPES:
        all_rows = [r for r in rows if r["type"] == t]
        body = []
        if t == "other":
            for reason in sorted({r["reason"] or "unspecified" for r in all_rows}):
                group = [r for r in all_rows if (r["reason"] or "unspecified") == reason]
                body.append(f'<h3>{escape(label(reason))} ({len(group)})</h3><div class="cards">'
                            + ''.join(card_html(r) for r in examples(group)) + '</div>')
        else:
            body.append('<div class="cards">' + ''.join(card_html(r) for r in examples(all_rows)) + '</div>')
        sections.append(f'<section data-type="{t}" data-total="{len(all_rows)}">'
                        f'<h2>{escape(label(t))} ({len(all_rows)})</h2>{"".join(body)}</section>')
    summary = aggregate(rows)
    table = []
    for pid, player in summary["players"].items():
        if pid not in players:
            continue
        for t, figures in player["types"].items():
            splits = [(label(t), figures)]
            splits += [(f"{label(t)} / jiggled {j}", f) for j, f in figures["jiggled"].items()]
            for split, f in splits:
                if not f["n"]:
                    continue
                cells = [players[pid], split, str(f["n"]), _percent(f["rate"]), _percent(f["shrunk"]),
                         _interval_text(f["interval"]), _percent(summary["pooled"][t]["rate"])]
                table.append('<tr>' + ''.join(f'<td>{escape(c)}</td>' for c in cells) + '</tr>')
    data = json.dumps({"cards": cards, "types": pc.TYPES}, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    js = Path(__file__).with_name("peek_page.js").read_text(encoding="utf-8")
    html = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Peek examples</title>'
            '<style>body{font:16px system-ui;background:#141c28;color:#eee;margin:24px}a{color:#8cceff}'
            '.cards{display:flex;flex-wrap:wrap;gap:16px}article{background:#243044;padding:16px;width:280px;'
            'border-radius:8px}canvas{background:#0a1018}button,select{padding:6px;margin:3px}'
            'td,th{padding:8px;text-align:left;border-bottom:1px solid #567}table{border-collapse:collapse}'
            '.table{overflow:auto}</style><body><h1>Peek examples</h1>'
            '<p>The sight lines here use the map\'s walls and smokes only: box stances (heights) and doors '
            '(map features) are worker assets the public site does not serve, so a peek over a box or through '
            'a door may be labelled from the 2D line.</p><button id="export" type="button">Export answers</button>'
            '<p id="answer-count" aria-live="polite"></p>' + ''.join(sections)
            + '<section><h2>Per-player win rates</h2><p>The thresholds are a draft until the check.</p>'
            '<div class="table"><table><thead><tr><th>Player</th><th>Type / split</th><th>n</th><th>Raw</th>'
            '<th>Shrunk</th><th>Interval</th><th>Everyone\'s average</th></tr></thead><tbody>'
            + ''.join(table) + '</tbody></table></div></section>'
            + f'<script id="peek-data" type="application/json">{data}</script><script>{js}</script></body></html>\n')
    page = out / "peeks.html"
    page.write_text(html, encoding="utf-8")
    return page


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
    rows, players, paths = [], {}, []
    for task, result in zip(tasks, results):
        uuid = result["uuid"]
        rows.extend(result["rows"])
        players.update(result["players"])
        paths.extend(result["paths"])
        coverage["rounds"]["completed"].extend(result["completed"])
        coverage["rounds"]["failed"].extend(result["failed"])
        if task["fetch_failed"] or result["failed"] or result["error"]:
            coverage["replays"]["failed"].append({"uuid": uuid, "error": result["error"] or "round failure"})
        else:
            coverage["replays"]["completed"].append(uuid)
    (out / "openings.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows),
                                        encoding="utf-8")
    _write_json(out / "players.json", players)
    (out / "paths.jsonl").write_text("".join(json.dumps(p, allow_nan=False) + "\n" for p in paths), encoding="utf-8")
    _write_json(out / "coverage.json", coverage)
    for kind, c in coverage.items():
        print(f"{kind}: discovered {c['discovered']}, completed {len(c['completed'])}, "
              f"skipped {sum(map(len, c['skipped'].values()))} {json.dumps(c['skipped'])}, failed {len(c['failed'])}")
    report(rows, out)
    build_page(rows, players, out, args.per_type, args.seed)
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
    page_parser = commands.add_parser("page")
    for command in (run, page_parser):
        command.add_argument("--per-type", type=_positive, default=8)
        command.add_argument("--seed", type=int, default=0)
    score_parser = commands.add_parser("score")
    score_parser.add_argument("answers", type=Path)
    for command in (run, report_parser, page_parser, score_parser):
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
    elif args.command == "page":
        build_page(rows, json.loads((out / "players.json").read_text(encoding="utf-8")), out,
                   args.per_type, args.seed)
    else:
        results = score(rows, json.loads(args.answers.read_text(encoding="utf-8")))
        for label, f in [("overall", results["overall"]), *results["by_type"].items()]:
            print(f"{label}: {_percent(f['rate'])} right, n={f['n']}")
        print("Confusion (rule × owner): " + json.dumps(results["confusion"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
