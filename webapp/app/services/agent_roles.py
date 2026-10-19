"""Agent -> role table and player-page filter keys.

A filter key is "" (none), "role:<role>" or "agent:<Agent>" (the name as stored in
match_players.agent). Anything else is ignored by parse_filter.
"""

from __future__ import annotations

import json
from pathlib import Path

ROLES: tuple[str, ...] = ("duelist", "initiator", "controller", "sentinel")

_DATA = Path(__file__).resolve().parent.parent / "data" / "agent_roles.json"
_AGENT_ROLE: dict[str, str] = {}
for _role, _agents in json.loads(_DATA.read_text(encoding="utf-8")).items():
    if not _role.startswith("_"):
        for _agent in _agents:
            _AGENT_ROLE[_agent] = _role.lower()


def role_of(agent: str | None) -> str | None:
    return _AGENT_ROLE.get(agent) if agent else None


def parse_filter(key: str | None, agents_played) -> str:
    """The key normalised, or "" when it is not a known role or a played agent."""
    if not key:
        return ""
    kind, _, value = key.partition(":")
    if kind == "role" and value.lower() in ROLES:
        return "role:" + value.lower()
    if kind == "agent" and value and value in set(agents_played):
        return key
    return ""


def filter_agents(key: str, agents_played) -> frozenset[str]:
    """Agents a valid key selects. Callers treat "" as no filter and never pass it
    (it returns an empty set)."""
    kind, _, value = key.partition(":")
    if kind == "role":
        return frozenset(a for a in agents_played if role_of(a) == value)
    if kind == "agent":
        return frozenset({value})
    return frozenset()


def filter_label(key: str) -> str:
    kind, _, value = key.partition(":")
    if kind == "role":
        return value.capitalize()
    if kind == "agent":
        return value
    return ""


def rank_agents(rows) -> list[str]:
    """rows: (agent, match_count, last_played_at). Most matches, then most recent
    (None last), then name."""
    ordered = sorted(
        rows,
        key=lambda r: (-r[1], r[2] is None, -(r[2].timestamp() if r[2] else 0), r[0]),
    )
    return [r[0] for r in ordered]
