"""Opponents are named by their agent, never by their Riot ID, in the coach's reports and the Critical Moments tab:
"the enemy Neon". A team holds each agent at most once, so the label is unique within a match."""

from __future__ import annotations

import re


def label(agent: str | None) -> str:
    return f"the enemy {agent}" if agent else "an enemy"


def hide_in(players: dict, team, name_key: str = "name") -> dict:
    """Copies of the players (slot -> dict with `team` and `agent`) with every player not on `team` renamed."""
    return {slot: (p if p.get("team") == team else {**p, name_key: label(p.get("agent"))})
            for slot, p in players.items()}


def replacements(players) -> dict[str, str]:
    """Each opponent's name, full and without the tag, to their label; `players` is (name, agent) pairs. A name
    that is only digits is left out without its tag, so a number in the text is never taken for it."""
    out = {}
    for name, agent in players:
        if not name:
            continue
        out[name] = label(agent)
        short = name.split("#")[0]
        if short.strip() and not short.strip().isdigit():
            out.setdefault(short, label(agent))
    return out


def scrub(text: str | None, swaps: dict[str, str]) -> str | None:
    """The text with each name in `swaps` replaced, whole words, any case, longest first; "The" at the start of a
    sentence."""
    if not text or not swaps:
        return text
    names = sorted((n for n in swaps if n.strip()), key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.IGNORECASE)
    lowered = {n.lower(): swaps[n] for n in names}

    def swap(m):
        new = lowered[m.group(0).lower()]
        before = text[:m.start()].rstrip()
        return new[:1].upper() + new[1:] if not before or before[-1] in ".!?:\n" else new

    return pattern.sub(swap, text)
