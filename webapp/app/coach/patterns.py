"""Find replay moments and log the ordinary answers that ground each one."""

from __future__ import annotations

import json
from pathlib import Path

from app.coach import detectors, query

COMMANDS = {"deaths", "kills", "teammate_deaths", "moment"}


def _player(snap, directory: Path, args: list) -> int:
    if len(args) > 1:
        raise query.QueryError("expected at most one player argument")
    if args:
        return query._slot(snap, args[0])
    try:
        slot = json.loads((Path(directory) / "subject.json").read_text(encoding="utf-8"))["slot"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        raise query.QueryError("player argument required: subject.json is missing or invalid") from None
    return query._slot(snap, slot)


def _answers(snap, directory: Path, n: int, t: float) -> list[dict]:
    entries = []
    for command, args in (("timeline", [n, t, t]), ("where", [n, max(0.0, t - 0.5)]),
                          ("knew", [n, t, "A"]), ("knew", [n, t, "B"])):
        result = query.run(snap, directory, command, args)
        entries.append({"id": result["id"], "cmd": command, "args": args,
                        "result": {k: v for k, v in result.items() if k != "id"}})
    return entries


def run(snap, directory: Path, command: str, args: list) -> dict:
    """Patterns return small rows; moment returns four full, citable entries. Neither adds a log command."""
    if command == "moment":
        try:
            if len(args) != 2:
                raise ValueError
            n, t = int(args[0]), float(args[1])
        except (TypeError, ValueError):
            raise query.QueryError("moment requires round and t arguments (seconds into the round)") from None
        query._view(snap, n, t)
        return {"entries": _answers(snap, directory, n, t)}
    if command not in COMMANDS:
        raise query.QueryError(f"unknown pattern {command!r}")
    slot = _player(snap, directory, args)
    moments = []
    for n in snap.round_numbers:
        view = snap.view(n)
        teammates = view.teammates(slot) if command == "teammate_deaths" else ()
        for kill in detectors._live_kills(view):
            matches = (kill["victim"] == slot if command == "deaths" else
                       kill.get("killer") == slot if command == "kills" else
                       kill["victim"] in teammates)
            if matches:
                moments.append((n, kill["t"]))
    rows = []
    for n, t in moments:
        entries = _answers(snap, directory, n, t)
        rows.append({"round": n, "t": t, **dict(zip(
            ("timeline_id", "where_id", "knew_a_id", "knew_b_id"),
            (entry["id"] for entry in entries),
        ))})
    return {"moments": rows}
