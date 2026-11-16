"""Add one agent's identity assets, or inventory its replay object names.

identity requires explicit C/Q/E utility classes; it never fills behaviour tables.
Use --api-json with a saved valorant-api response to avoid fetching the agent list.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import re
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vendor_ability_icons import agent_slug, vendor_agent_abilities  # noqa: E402
from vendor_map_assets import API, fetch, fetch_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SLOTS = {"Grenade": "C", "Ability1": "Q", "Ability2": "E", "Ultimate": "X"}
BEHAVIOUR_FILES = (
    "app/static/js/replay.js", "app/moments/constants.py",
    "app/critutil/abilities_seed.json", "app/critutil/abilities.json",
    "app/replays/extras.py", "app/control/utility.py", "app/control/heights.py",
)


def classes_arg(value: str) -> dict[str, str]:
    result = {}
    for entry in value.split(","):
        slot, separator, kind = entry.strip().partition("=")
        if not separator or slot not in {"C", "Q", "E"} or not kind.strip() or slot in result:
            raise argparse.ArgumentTypeError("--classes must supply C, Q and E exactly once: C=type,Q=type,E=type")
        result[slot] = kind.strip()
    if set(result) != {"C", "Q", "E"}:
        raise argparse.ArgumentTypeError("--classes requires all three slots C, Q and E")
    return result


def json_entry(text: str, key: str, value, *, compact: bool = False) -> str:
    """Edit just one top-level JSON member, retaining every other member's text."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    if key in data and data[key] == value:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    rendered = json.dumps(value, ensure_ascii=False, **({} if compact else {"indent": 2}))
    rendered = rendered.replace("\n", newline + "  ")
    decoder = json.JSONDecoder()
    members = []
    position = text.index("{") + 1
    while True:
        position = re.compile(r"\s*").match(text, position).end()
        if text[position] == "}":
            break
        start = position
        member_key, position = decoder.raw_decode(text, position)
        position = re.compile(r"\s*:\s*").match(text, position).end()
        value_start = position
        _, end = decoder.raw_decode(text, position)
        if member_key == key:
            return text[:value_start] + rendered + text[end:]
        members.append((member_key, start, end))
        position = re.compile(r"\s*").match(text, end).end()
        if text[position] == ",":
            position += 1
    entry = f"  {json.dumps(key, ensure_ascii=False)}: {rendered}"
    for member_key, start, _ in members:
        if member_key > key:
            line_start = text.rfind("\n", 0, start) + 1
            if text[line_start:start].strip():
                raise ValueError("expected one top-level JSON member per line")
            return text[:line_start] + entry + "," + newline + text[line_start:]
    if members:
        end = members[-1][2]
        return text[:end] + "," + newline + entry + text[end:]
    return text[:position] + newline + entry + newline + text[position:]


def agent_keys_entry(text: str, name: str, values: dict) -> str:
    """Require the one-line-per-agent literal shape before inserting or replacing a line."""
    module = ast.parse(text)
    assignments = [node for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "AGENT_KEYS" for t in node.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Dict):
        raise ValueError("AGENT_KEYS: expected one dictionary literal")
    assignment = assignments[0]
    block = assignment.value
    lines = text.splitlines(keepends=True)
    if (lines[assignment.lineno - 1].strip() != "AGENT_KEYS = {"
            or lines[block.end_lineno - 1].strip() != "}"):
        raise ValueError("AGENT_KEYS: expected a standalone opening and closing brace")
    names = []
    for key, value in zip(block.keys, block.values):
        if (not isinstance(key, ast.Constant) or not isinstance(key.value, str)
                or not isinstance(value, ast.Dict) or key.lineno != value.end_lineno
                or not lines[key.lineno - 1].startswith("    \"")):
            raise ValueError("AGENT_KEYS: expected one double-quoted agent entry per line")
        names.append(key.value)
    if names != sorted(set(names)):
        raise ValueError("AGENT_KEYS: expected unique alphabetically ordered agents")
    newline = "\r\n" if "\r\n" in text else "\n"
    quoted = lambda value: json.dumps(value, ensure_ascii=False)
    slots = ", ".join(f'{quoted(slot)}: ({quoted(ability)}, {quoted(kind)})'
                      for slot, (ability, kind) in values.items())
    entry = f"    {quoted(name)}: {{{slots}}},{newline}"
    for key, value in zip(block.keys, block.values):
        if key.value == name:
            if ast.literal_eval(value) == values:
                return text
            lines[key.lineno - 1] = entry
            return "".join(lines)
        if key.value > name:
            lines.insert(key.lineno - 1, entry)
            return "".join(lines)
    lines.insert(block.end_lineno - 1, entry)
    return "".join(lines)


def write_changed(path: Path, content: bytes) -> None:
    if not path.is_file() or path.read_bytes() != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")


def identity(args) -> int:
    if args.api_json:
        body = json.loads(args.api_json.read_text(encoding="utf-8"))
        agents = body["data"] if isinstance(body, dict) else body
    else:
        agents = fetch_json(f"{API}/agents?isPlayableCharacter=true")
    matches = [agent for agent in agents if agent["displayName"] == args.name
               and agent.get("isPlayableCharacter", True)]
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous playable agent: {args.name}")
    agent = matches[0]
    code = agent["developerName"]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", code):
        raise ValueError("invalid developerName")
    root = args.root
    agents_path = root / "app/static/data/agents.json"
    roles_path = root / "app/data/agent_roles.json"
    abilities_path = root / "app/static/data/abilities.json"
    keys_path = root / "app/critutil/constants.py"
    texts = {p: p.read_bytes().decode("utf-8") for p in (agents_path, roles_path, abilities_path, keys_path)}
    mapping = json.loads(texts[agents_path])
    if any(other != code and name == args.name for other, name in mapping.items()):
        raise ValueError(f"a different code already maps to {args.name}")
    if code in mapping and mapping[code] != args.name:
        raise ValueError(f"{code} already maps to a different display name")
    roles = json.loads(texts[roles_path])
    role = agent["role"]["displayName"]
    if role.startswith("_") or role not in roles or not isinstance(roles[role], list):
        raise ValueError(f"unknown role: {role}")
    if any(args.name in members for key, members in roles.items() if key != role and not key.startswith("_")):
        raise ValueError(f"{args.name} already belongs to a different role")
    abilities = [a for a in agent["abilities"] if a["slot"] != "Passive"]
    if (Counter(a["slot"] for a in abilities) != Counter(SLOTS.keys())
            or any(not a.get("displayName") or not a.get("displayIcon") for a in abilities)
            or len({a["displayName"] for a in abilities}) != 4):
        raise ValueError("expected four named abilities with icons: Grenade, Ability1, Ability2, Ultimate")
    slug = agent_slug(args.name)
    entries = dict(sorted((a["displayName"], f'/static/img/abilities/{slug}/{a["slot"].lower()}.png')
                          for a in abilities))
    by_slot = {SLOTS[a["slot"]]: a["displayName"] for a in abilities}
    values = {slot: (by_slot[slot], args.classes[slot]) for slot in ("C", "Q", "E")}
    updates = {
        agents_path: json_entry(texts[agents_path], code, args.name),
        roles_path: json_entry(texts[roles_path], role, sorted(set(roles[role]) | {args.name}), compact=True),
        abilities_path: json_entry(texts[abilities_path], args.name, entries),
        keys_path: agent_keys_entry(texts[keys_path], args.name, values),
    }
    # Complete preflight and downloads before changing any files.
    downloaded = {a["displayIcon"]: fetch(a["displayIcon"]) for a in abilities}
    with Image.open(io.BytesIO(fetch(agent["displayIcon"]))) as image:
        portrait = image.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
        png = io.BytesIO()
        portrait.save(png, format="PNG")
    for path, content in updates.items():
        write_changed(path, content.encode("utf-8"))
    vendor_agent_abilities(agent, root / "app/static", downloaded.__getitem__)
    for ability in abilities:
        print(f'ability icon: app/static/img/abilities/{slug}/{ability["slot"].lower()}.png')
    write_changed(root / f"app/static/img/agents/{slug}.png", png.getvalue())
    print("Remaining checklist: replay.js ULT_COST; app/critutil/abilities_seed.json; behaviour: "
          f"run objects {code} <export dir> and review the behaviour tables.")
    return 0


def string_tokens(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from string_tokens(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_tokens(item)


def objects(args) -> int:
    counts = defaultdict(Counter)
    for export in args.exports:
        with (export / "events.ndjson").open(encoding="utf-8") as events:
            for number, line in enumerate(events, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{export}/events.ndjson:{number}: {error}") from error
                event_type = str(row.get("type", "unknown"))
                for value in string_tokens(row):
                    name = value.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
                    name = re.sub(r"_C(?:_\d+)?$", "", name)
                    if name.startswith(args.code) or f"_{args.code}_" in name:
                        counts[name][event_type] += 1
    sources = {relative: (args.root / relative).read_text(encoding="utf-8")
               for relative in BEHAVIOUR_FILES if (args.root / relative).is_file()}
    unreferenced = False
    for name, types in sorted(counts.items()):
        suffix = name.split(args.code + "_", 1)[-1]
        references = [path for path, text in sources.items() if name in text or suffix in text]
        unreferenced |= not references
        by_type = ", ".join(f"{kind}={count}" for kind, count in sorted(types.items()))
        print(f'{name} total={sum(types.values())} {by_type} ' + (", ".join(references) or "UNREFERENCED"))
    return int(args.fail_unreferenced and unreferenced)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    identity_parser = commands.add_parser("identity", help="vendor one playable agent's identity")
    identity_parser.add_argument("name", help="exact API display name")
    identity_parser.add_argument("--classes", type=classes_arg, required=True)
    identity_parser.add_argument("--api-json", type=Path)
    objects_parser = commands.add_parser("objects", help="inventory object names from exports")
    objects_parser.add_argument("code")
    objects_parser.add_argument("exports", type=Path, nargs="+")
    objects_parser.add_argument("--fail-unreferenced", action="store_true")
    for command in (identity_parser, objects_parser):
        command.add_argument("--root", type=Path, default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        return identity(args) if args.command == "identity" else objects(args)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    sys.exit(main())
