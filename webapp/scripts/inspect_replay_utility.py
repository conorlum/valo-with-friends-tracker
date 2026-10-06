"""Streams one parser export and prints each matching utility actor's timeline: its spawn, the
export-group updates and RPCs on it, the effects elsewhere that name it, the typed utility rows
that carry its GUID, and its close.

    .\\.venv\\Scripts\\python.exe scripts\\inspect_replay_utility.py <export dir> --match "CableJam|FishingHook" [--limit 6]

Read-only and bounded: one pass over `events.ndjson`, a line is parsed only when it mentions a live
matching actor, and each actor keeps at most `--events` rows (the rest are counted). The output
names no player: pawns are shown as `pawn:<agent code>#<guid>`. Used for the utility signal
inventory (docs/superpowers/plans/2026-10-05-replay-control-review-impl.md, W02).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ACTOR = re.compile(r'"actor_net_guid":(\d+)')
VALUE = re.compile(r'"Value":(\d+)')
TYPED_GUID = re.compile(r'"(?:flash|nearsight|wall|segment|source|causing|active_wall)_actor_net_guid":(\d+)')
PAWN = re.compile(r"Default__([A-Za-z0-9]+)_PC_C$")
TYPE = re.compile(r'^\{"type":"([a-z_]+)"')


def _short(value, width: int = 160):
    text = json.dumps(value, separators=(",", ":"))
    return text if len(text) <= width else text[:width] + "..."


def inspect(events_path: Path, pattern: re.Pattern, per_actor: int, t_from: int | None, t_to: int | None) -> list[dict]:
    live: dict[int, dict] = {}
    done: list[dict] = []
    pawns: dict[int, str] = {}

    def label(guid: int) -> str:
        if guid in pawns:
            return f"pawn:{pawns[guid]}#{guid}"
        if guid in live:
            return f"actor:{live[guid]['archetype']}#{guid}"
        return str(guid)

    def note(actor: dict, row: dict) -> None:
        actor["counts"][row["what"]] += 1
        if len(actor["events"]) < per_actor:
            actor["events"].append(row)

    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            head = TYPE.match(line)
            kind = head.group(1) if head else ""
            if kind == "actor_spawned":
                data = json.loads(line)
                guid = int(data.get("actor_net_guid") or 0)
                archetype = str(data.get("archetype_path") or "")
                found = PAWN.search(archetype)
                if found:
                    pawns[guid] = found.group(1)
                if guid in live:
                    done.append(live.pop(guid))   # a reused GUID: the earlier actor is over
                t_ms = int(data.get("time_ms", 0))
                if pattern.search(archetype) and (t_from is None or t_ms >= t_from) and (t_to is None or t_ms <= t_to):
                    live[guid] = {"guid": guid, "archetype": archetype.replace("Default__", "").removesuffix("_C"),
                                  "t_ms": t_ms, "location": data.get("location"), "rotation": data.get("rotation"),
                                  "velocity": data.get("velocity"), "closed_ms": None, "events": [],
                                  "counts": Counter()}
                continue
            if not live:
                continue
            found = ACTOR.search(line)
            guid = int(found.group(1)) if found else 0
            if kind == "actor_closed":
                if guid in live:
                    actor = live.pop(guid)
                    data = json.loads(line)
                    actor["closed_ms"] = int(data.get("time_ms", 0))
                    actor["close_reason"] = data.get("close_reason") or data.get("reason")
                    done.append(actor)
                continue
            if kind.startswith("valorant_") and kind != "valorant_shot_received":
                named = {int(g) for g in TYPED_GUID.findall(line)} & live.keys()
                if named:
                    data = json.loads(line)
                    rest = {k: v for k, v in data.items() if k not in ("type", "time_ms", "packet_id")}
                    for g in named:
                        note(live[g], {"t_ms": int(data.get("time_ms", 0)), "what": kind, "data": _short(rest, 400)})
                continue
            if guid in live:
                data = json.loads(line)
                t_ms = int(data.get("time_ms", 0))
                payload = data.get("payload")
                if kind == "rpc_received":
                    note(live[guid], {"t_ms": t_ms, "what": f"rpc {data.get('function_name')}", "data": _short(payload, 400)})
                elif kind == "export_group_received":
                    group = str(data.get("export_group_path") or "").rsplit(".", 1)[-1]
                    note(live[guid], {"t_ms": t_ms, "what": f"group {group}", "data": _short(payload, 400)})
                continue
            if '"FunctionObjectValues"' in line:
                named = {int(v) for v in VALUE.findall(line)} & live.keys()
                if named:
                    data = json.loads(line)
                    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                    for g in named:
                        note(live[g], {"t_ms": int(data.get("time_ms", 0)),
                                       "what": f"named by {data.get('function_name')} on {label(guid).split('#')[0]}",
                                       "data": _short({"on": label(guid), "EffectId": payload.get("EffectId"),
                                                       "EffectContainer": payload.get("EffectContainer")}, 200)})
    done.extend(live.values())
    return sorted(done, key=lambda a: (a["t_ms"], a["guid"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--match", required=True, help="a regular expression on the actor's archetype path")
    parser.add_argument("--limit", type=int, default=6, help="actors printed per archetype (all are counted)")
    parser.add_argument("--events", type=int, default=40, help="rows kept per actor")
    parser.add_argument("--from-ms", type=int)
    parser.add_argument("--to-ms", type=int)
    parser.add_argument("--json", type=Path, help="also write every matching actor here")
    args = parser.parse_args(argv)
    actors = inspect(args.export_dir / "events.ndjson", re.compile(args.match), args.events, args.from_ms, args.to_ms)
    by_archetype: dict[str, list[dict]] = {}
    for actor in actors:
        by_archetype.setdefault(actor["archetype"], []).append(actor)
    for archetype, group in sorted(by_archetype.items()):
        lives = [a["closed_ms"] - a["t_ms"] for a in group if a["closed_ms"] is not None]
        totals = Counter()
        for a in group:
            totals.update(a["counts"])
        print(f"== {archetype}: {len(group)} actors; lifetimes ms min/max "
              f"{min(lives) if lives else None}/{max(lives) if lives else None}; open at end {len(group) - len(lives)}")
        print(f"   rows: {dict(totals.most_common())}")
        for actor in group[:args.limit]:
            print(f"  - guid {actor['guid']} t {actor['t_ms']} closed {actor['closed_ms']} "
                  f"loc {_short(actor['location'])} rot {_short(actor['rotation'])} vel {_short(actor['velocity'])}")
            for row in actor["events"]:
                print(f"      +{row['t_ms'] - actor['t_ms']:>6} {row['what']}: {row['data']}")
    if args.json:
        for actor in actors:
            actor["counts"] = dict(actor["counts"])
        args.json.write_text(json.dumps(actors, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
