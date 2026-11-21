"""Every playable agent must have complete entries in the shipped agent tables."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app import templates
from app.critutil.constants import AGENT_KEYS

APP = Path(__file__).resolve().parents[1] / "app"
STATIC = APP / "static"
EXCEPTIONS = {
    ("Chamber", "critutil_abilities", "Q"): "Headhunter is a gun, not util (constants.py).",
    ("Chamber", "agent_keys", "Q"): "Headhunter is a gun, not util (constants.py).",
}


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ult_cost_agents(script):
    block = re.search(r"\bvar\s+ULT_COST\s*=\s*\{(.*?)\}\s*;", script, re.DOTALL)
    assert block is not None, "replay.js ult_cost block missing"
    keys = re.findall(
        r'''(?:^|,)\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*:''',
        block.group(1),
    )
    return {next(key for key in match if key) for match in keys}


def _util_color_agents(script):
    block = re.search(r"\bvar\s+UTIL_AGENT_COLORS\s*=\s*\{(.*?)\}\s*;", script, re.DOTALL)
    assert block is not None, "replay.js util_colors block missing"
    keys = re.findall(
        r'''(?:^|,)\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*:''',
        block.group(1),
    )
    return {next(key for key in match if key) for match in keys}


def agent_table_problems(agents):
    """Return raw (agent, table, slot) problems, including the named exceptions."""
    names = set(agents.values())
    roles = _read_json(APP / "data" / "agent_roles.json")
    role_counts = Counter(
        agent for role, members in roles.items() if not role.startswith("_") for agent in members
    )
    abilities = _read_json(STATIC / "data" / "abilities.json")
    util_rows = _read_json(APP / "critutil" / "abilities.json")["abilities"]
    util_slots = {}
    for row in util_rows:
        util_slots.setdefault(row["agent"], set()).add(row["slot"])
    script = (STATIC / "js" / "replay.js").read_text(encoding="utf-8")
    ult_agents = _ult_cost_agents(script)
    util_agents = _util_color_agents(script)
    icon_slugs = {path.stem for path in (STATIC / "img" / "agents").glob("*.png")}
    problems = {}

    for agent in sorted(names):
        slug = agent.replace("/", "").lower()
        if role_counts[agent] != 1:
            problems[agent, "agent_roles", None] = f"{agent} agent_roles expected one role, got {role_counts[agent]}"
        if agent not in abilities:
            problems[agent, "abilities", None] = f"{agent} abilities entry missing"
        else:
            entries = abilities[agent]
            issues = []
            if len(entries) != 4:
                issues.append(f"expected four entries, got {len(entries)}")
            for ability, icon in entries.items():
                if not icon.startswith(f"/static/img/abilities/{slug}/") or not (APP / icon.lstrip("/")).is_file():
                    issues.append(f"{ability} icon missing or outside its agent directory ({icon})")
            if issues:
                problems[agent, "abilities", None] = f"{agent} abilities " + "; ".join(issues)
        if agent not in AGENT_KEYS:
            problems[agent, "agent_keys", None] = f"{agent} agent_keys entry missing"
        else:
            for slot in sorted({"C", "Q", "E"} - set(AGENT_KEYS[agent])):
                problems[agent, "agent_keys", slot] = f"{agent} agent_keys slot {slot} missing"
            for slot in sorted(set(AGENT_KEYS[agent]) - {"C", "Q", "E"}):
                problems[agent, "agent_keys", slot] = f"{agent} agent_keys unexpected slot {slot}"
        for slot in sorted({"C", "Q", "E", "X"} - util_slots.get(agent, set())):
            problems[agent, "critutil_abilities", slot] = f"{agent} critutil_abilities slot {slot} missing"
        if agent not in ult_agents:
            problems[agent, "ult_cost", None] = f"{agent} ult_cost entry missing"
        if agent not in util_agents:
            problems[agent, "util_colors", None] = f"{agent} util_colors entry missing"
        if slug not in icon_slugs:
            problems[agent, "agent_icons", None] = f"{agent} agent_icons {slug}.png missing"
        if templates.agent_icon_slug(agent) is None:
            problems[agent, "agent_icon_slug", None] = f"{agent} agent_icon_slug returned None"

    for table, members in (
        ("agent_roles", role_counts),
        ("abilities", abilities),
        ("agent_keys", AGENT_KEYS),
        ("critutil_abilities", util_slots),
        ("ult_cost", ult_agents),
        ("util_colors", util_agents),
    ):
        for agent in sorted(set(members) - names):
            problems[agent, table, None] = f"{agent} {table} absent from agents.json"
    expected_slugs = {agent.replace("/", "").lower() for agent in names}
    for table, slugs in (
        ("agent_icons", icon_slugs),
        ("agent_icon_slug", templates._AGENT_ICON_SLUGS),
    ):
        for slug in sorted(slugs - expected_slugs):
            problems[slug, table, None] = f"{slug} {table} absent from agents.json"
    return problems


def test_agent_tables_cover_playable_agents():
    problems = agent_table_problems(_read_json(STATIC / "data" / "agents.json"))
    unexpected = {key: message for key, message in problems.items() if key not in EXCEPTIONS}
    assert not unexpected, "\n".join(unexpected.values())


def test_exceptions_still_apply():
    problems = agent_table_problems(_read_json(STATIC / "data" / "agents.json"))
    stale = set(EXCEPTIONS) - problems.keys()
    assert not stale, f"Remove resolved agent/table/slot exceptions {sorted(stale, key=str)}"
    assert all(EXCEPTIONS.values()), "Every exception needs its evidence"


def test_extra_playable_agent_reports_every_missing_table():
    agents = _read_json(STATIC / "data" / "agents.json")
    agents["NobodyCode"] = "Nobody"
    problems = agent_table_problems(agents)
    for table in (
        "agent_roles", "abilities", "agent_keys", "critutil_abilities",
        "ult_cost", "util_colors", "agent_icons", "agent_icon_slug",
    ):
        messages = [message for (agent, name, _), message in problems.items() if agent == "Nobody" and name == table]
        assert messages, f"Nobody {table} missing from coverage problems"
        assert all("Nobody" in message and table in message for message in messages)


def test_agents_absent_from_roster_report_every_table():
    agents = _read_json(STATIC / "data" / "agents.json")
    agents = {code: name for code, name in agents.items() if name != "Jett"}
    problems = agent_table_problems(agents)
    for table in ("agent_roles", "abilities", "agent_keys", "critutil_abilities", "ult_cost", "util_colors"):
        assert ("Jett", table, None) in problems, f"Jett {table} extra entry undetected"
    for table in ("agent_icons", "agent_icon_slug"):
        assert ("jett", table, None) in problems, f"Jett {table} extra entry undetected"


@pytest.mark.parametrize("key", ["Jett", '"KAY/O"', "'KAY/O'"])
def test_ult_cost_parser_accepts_bare_and_quoted_keys(key):
    assert _ult_cost_agents(f"var ULT_COST = {{ {key}: 8, }};") == {key.strip("\"'")}


@pytest.mark.parametrize("key", ["Jett", '"KAY/O"', "'KAY/O'"])
def test_util_color_parser_accepts_bare_and_quoted_keys(key):
    assert _util_color_agents(f'var UTIL_AGENT_COLORS = {{ {key}: "#f4f7fb", }};') == {key.strip("\"'")}


def test_deleted_util_colour_reports_missing_agent(monkeypatch):
    script_path = STATIC / "js" / "replay.js"
    script = script_path.read_text(encoding="utf-8")
    assert "Jett" in _util_color_agents(script)
    changed = re.sub(r'(?:"Jett"|Jett)\s*:\s*"#[0-9a-fA-F]{6}"\s*,?', "", script, count=1)
    assert _util_color_agents(changed) == _util_color_agents(script) - {"Jett"}
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
                        changed if path == script_path else read_text(path, *args, **kwargs))
    assert ("Jett", "util_colors", None) in agent_table_problems({"Wushu": "Jett"})


def test_icon_slugs_are_read_from_files_once_at_import(monkeypatch):
    assert templates._AGENT_ICON_SLUGS == {
        path.stem for path in (STATIC / "img" / "agents").glob("*.png")
    }
    monkeypatch.setattr(Path, "glob", lambda *args: pytest.fail("agent_icon_slug reread the filesystem"))
    assert templates.agent_icon_slug("KAY/O") == "kayo"
    assert templates.agent_icon_slug("Nobody") is None
    assert templates.agent_icon_slug(None) is None
    assert templates.agent_icon_slug("") is None
