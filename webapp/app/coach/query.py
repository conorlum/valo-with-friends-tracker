"""The coach's questions about a round, answered from the snapshot. Read-only and computed: the model never
estimates a distance or a time, it asks. Every call is appended to the run's log with an id, and a report's
evidence cites those ids, which is how a claim is checked (app/coach/checker.py, plan 3)."""

from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path

from app.coach import control_areas, knowledge
from app.coach.snapshot import SnapshotError
from app.replays import player_state

LOG = "log.jsonl"


class QueryError(Exception):
    pass


def _view(snap, n: int, *times: float):
    try:
        view = snap.view(int(n))
    except SnapshotError as error:
        raise QueryError(str(error)) from None
    for t in times:
        if not 0.0 <= t <= view.t_end:
            raise QueryError(f"t={t} is outside round {n} (0.0 to {view.t_end})")
    return view


def _slot(snap, token) -> int:
    try:
        return snap.slot_of(token)
    except SnapshotError as error:
        raise QueryError(str(error)) from None


def _missing(reason: str) -> dict:
    return {"available": False, "reason": reason}


def _groups_disagree(snap, n: int, view, shares: dict) -> bool:
    """True when the control header's group_side contradicts the round's roles (R-F9). No header, no guard."""
    group_side = shares.get("group_side")
    if not group_side:
        return False
    for slot in view.slots:
        role, wanted = snap.role(n, slot), group_side.get(view.side(slot))
        if role and wanted and role != wanted:
            return True
    return False


# Health reaches the coach as a word, never a number (the owner's rule for the viewer, kept here).
def _health(view, slot: int, t: float) -> str | None:
    """The replay viewer's bands (replay.js healthWord): healthy above 50 percent, hurt above 25, else low."""
    event = player_state.vitals_at(view.blob.get("player_state"), slot, t)
    if event is None:
        return None
    pct = 100.0 * event["hp"] / (event.get("mhp") or 100)
    return "healthy" if pct > 50 else "hurt" if pct > 25 else "low"


UTIL_NEAR_M = 8.0       # a smoke is about 4 to 5 m across its radius: this reaches a player at its edge


def _util_near(snap, view, standing: list[dict], position: dict) -> list[dict]:
    if position.get("u") is None:
        return []
    out = []
    for e in standing:
        metres = view.metres(math.hypot(position["u"] - e["u"], position["v"] - e["v"]))
        if metres <= UTIL_NEAR_M:
            row = {"ability": e.get("name") or e.get("code"), "by": snap.names.get(e.get("by")),
                   "metres": round(metres, 1), "since": e["t"]}
            if e.get("t1") is not None:
                row["until"] = e["t1"]
            out.append(row)
    return out


def where(snap, n, t) -> dict:
    t = float(t)
    view = _view(snap, n, t)
    players, standing = [], view.placed_util(t)
    for s in view.slots:
        position = view.position(s, t)
        if position["alive"] and position.get("u") is None:       # alive, no usable sample: alive with no place
            position = {"alive": True}
        row = {"slot": s, "name": snap.names.get(s), "side": view.side(s), "role": snap.role(int(n), s), **position}
        if row["alive"]:
            row["health"] = _health(view, s, t)
            row["util_near"] = _util_near(snap, view, standing, position)
        row["guns"] = view.guns(s)
        players.append(row)
    return {"round": int(n), "t": t, "players": players,
            "note": "health is a word (healthy, hurt, low) after the player's last recorded hit; null means no hit "
                    "is recorded so far, not full health; heals are not recorded; a living player with no u/v has "
                    "no usable position at that time. util_near: placed util (a smoke, a wall, a trip, a camera) "
                    f"standing at t within {UTIL_NEAR_M:g} m of the player, with metres to its centre, not its "
                    "edge, and the span it stood. guns: each gun the player fired in the whole round and the time "
                    "of its first shot; a player who never fired has none listed"}


def knew(snap, n, t, side) -> dict:
    t = float(t)
    view = _view(snap, n, t)
    side = side if side in ("A", "B") else view.side(_slot(snap, side))
    control = snap.control(int(n)) or {}
    if not control.get("knew"):
        return _missing("team_knowledge")
    if _groups_disagree(snap, int(n), view, control):
        return _missing("side_groups_disagree")
    return {"round": int(n), "t": t, "side": side,
            "enemies": knowledge.known_enemies(control["knew"], side, t, view),
            "signals": knowledge.signals(control.get("signals"), side, t, snap.names, view),
            "note": "team knowledge, assuming everything any teammate learned was called. enemies: those the team "
                    "had seen, with the place and time of the last sighting. signals: every stored event that "
                    "located an enemy for the team (sight, sound, damage, a kill), with its time and reason and "
                    "only a rough_place: from the nearest teammate, a minimap direction (up, down, left, right: "
                    "a 90 degree quarter) and a distance band in metres. It is never a spot or a named area; "
                    "signals is null when this round stores none"}


def distance(snap, n, t, a, b) -> dict:
    t = float(t)
    view = _view(snap, n, t)
    sa, sb = _slot(snap, a), _slot(snap, b)
    for s in (sa, sb):
        if not view.alive(s, t):
            return _missing(f"{snap.names[s]} was dead at {t}")
    metres = view.distance_m(sa, sb, t)
    if metres is None:
        return _missing(f"no position for one of them at {t}")
    return {"round": int(n), "t": t, "a": snap.names[sa], "b": snap.names[sb], "metres": round(metres, 1)}


def control(snap, n, t0, t1, area) -> dict:
    t0, t1 = float(t0), float(t1)
    view = _view(snap, n, t0, t1)
    shares = snap.control(int(n))
    if shares is None:
        gap = next((g for g in snap.gaps(int(n)) if g.startswith("control_")), "control_not_ready")
        return _missing(gap)
    if _groups_disagree(snap, int(n), view, shares):
        return _missing("side_groups_disagree")
    if area not in shares["areas"]:
        raise QueryError(f"no area {area!r} on this map; areas: {', '.join(sorted(shares['areas']))}")
    answer = control_areas.control_over(shares, area, t0, t1)
    if answer is None:
        return _missing("no control ticks in that span")
    return {"round": int(n), "t0": t0, "t1": t1, "area": area, "shares": answer,
            "note": "a and b are the round's side groups; shares are fractions of the area's cells over the span"}


def timeline(snap, n, t0=None, t1=None) -> dict:
    view = _view(snap, n)
    lo = 0.0 if t0 is None else float(t0)
    hi = view.t_end if t1 is None else float(t1)
    name = snap.names.get
    events = [{"t": k["t"], "kind": "kill", "killer": name(k.get("killer")), "victim": name(k["victim"]),
               "after_decided": k["t"] > view.t_decided} for k in view.kills]
    events += view.utility() + view.damage()
    db = snap.site(int(n)).get("db") or {}                  # the site's times, already on the replay clock
    for key in ("plant", "defuse"):
        if db.get(key) is not None:
            events.append({"t": db[key], "kind": key})
    events = sorted((e for e in events if lo <= e["t"] <= hi), key=lambda e: (e["t"], e["kind"]))
    return {"round": int(n), "t0": lo, "t1": hi, "decided_at": view.t_decided, "events": events}


def impact(snap, n, player) -> dict:
    _view(snap, n)
    slot = _slot(snap, player)
    stats = (snap.site(int(n)).get("stats") or {}).get(str(slot)) or {}
    if stats.get("impact") is None:
        return _missing("no Impact row for this round")
    keys = ("impact", "kill_impact", "death_impact", "damage", "econ", "trade_credit", "kills", "deaths", "assists")
    return {"round": int(n), "player": snap.names[slot], **{k: stats[k] for k in keys if k in stats}}


COMMANDS = {"where": where, "knew": knew, "distance": distance, "control": control, "timeline": timeline,
            "impact": impact}

# Which positions of each command's arguments are whole numbers or seconds (converted before the call).
_INT, _SEC, _TXT = int, float, str
_SIGNATURES = {"where": (_INT, _SEC), "knew": (_INT, _SEC, _TXT), "distance": (_INT, _SEC, _TXT, _TXT),
               "control": (_INT, _SEC, _SEC, _TXT), "timeline": (_INT, _SEC, _SEC), "impact": (_INT, _TXT)}


def read_log(directory: Path) -> dict[str, dict]:
    path = Path(directory) / LOG
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            continue                    # the file is writable by the model: a bad line is skipped, not fatal
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            out[row["id"]] = row
    return out


def answer(snap, command: str, args: list[str]) -> dict:
    """The answer alone. Bad input is an answer too, so the caller reads it instead of retrying blind. Only the
    argument conversion is guarded: a fault inside a query raises (R-F13)."""
    if command not in COMMANDS:
        return {"error": f"unknown command {command!r}; commands: {', '.join(sorted(COMMANDS))}"}
    args = args if isinstance(args, list) else []
    kinds = _SIGNATURES[command]
    try:
        if len(args) > len(kinds) or len(args) < (1 if command == "timeline" else len(kinds)):
            raise TypeError("argument count")
        converted = [kind(arg) for kind, arg in zip(kinds, args)]
    except (TypeError, ValueError):
        return {"error": f"wrong arguments for {command}: {args}. Rounds are whole numbers and times are "
                         "seconds into the round (41.5, not 0:41)"}
    try:
        return COMMANDS[command](snap, *converted)
    except QueryError as error:
        return {"error": str(error)}


def append(directory: Path, cmd: str, args: list, result: dict) -> str:
    """Adds one entry to the run log and returns its id. Query answers and the brief's flags both go through
    here, so a report's evidence can cite either."""
    path = Path(directory) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path.with_name(LOG + ".lock")):        # the model may run several ./q calls at once
        entry = {"id": f"q{len(read_log(directory)) + 1}", "cmd": cmd, "args": list(args), "result": result}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return entry["id"]


LOCK_WAIT_S = 10.0      # longer than any honest append; a lock older than this was left by a killed process


@contextmanager
def _locked(lock: Path):
    """One writer at a time, across processes: the id is the line count, so read-then-append must not interleave
    (PR 132 review, finding 6). Creating the lock file is the lock; a stale one is taken over."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except (FileExistsError, PermissionError):
            if time.monotonic() > deadline:
                try:
                    lock.unlink()
                except OSError:
                    pass
                deadline = time.monotonic() + LOCK_WAIT_S
            time.sleep(0.005)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def run(snap, directory: Path, command: str, args: list[str]) -> dict:
    result = answer(snap, command, args)
    return {"id": append(directory, command, args, result), **result}
