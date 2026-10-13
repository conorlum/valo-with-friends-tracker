"""Decides whether a point the model wrote may be published (docs/superpowers/specs/2026-10-12-ai-coach-design.md,
"The checker"). Plain code over the run log: a claim stands only on answers the model actually got, about the
round it is talking about, and only on the measured values in them."""

from __future__ import annotations

import json
import re

from app.coach import query

NO_KNOWLEDGE_NOTE = "no team-knowledge data"
POLARITIES = ("good", "improve")
KNEW_WINDOW_S = 30.0        # a mistake's knew answer must be this recent; a first value, reviewed with the owner
TITLE_MAX = 160             # app/services/coach.py validate_payload's limit, checked here so it can be repaired

# The keys of a query answer or a flag whose numbers are measurements. Everything else (slot, u, v, yaw, round)
# is an identifier or a coordinate and grounds nothing.
_TIME_KEYS = frozenset({"t", "t0", "t1", "first_seen", "last_seen", "died_at", "decided_at"})
_SHARE_KEYS = frozenset({"a", "b", "contested", "none"})
_MEASURED = _TIME_KEYS | _SHARE_KEYS | frozenset({
    "metres", "impact", "kill_impact", "death_impact", "damage", "econ", "trade_credit", "kills", "deaths",
    "assists", "seconds", "hits", "nearest_teammate_m", "after_s", "teammates_alive", "own",
    "enemy", "enemies_seen", "hold_s"})

_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
# P4 review F1: a unit written against the digits ("5m", "3s", "2vs1") must not hide the number from the check.
_VERSUS = re.compile(r"(?<!\w)(\d)\s?-?\s?(?:vs|v|on)\.?\s?-?\s?(\d)(?!\d)", re.IGNORECASE)
_ORDINAL = re.compile(r"(?<![\w.#])\d+(?:st|nd|rd|th)(?!\w)", re.IGNORECASE)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"(?<![\w.#])\d+(?:\.\d+)?(?!\.?\d|#)")
_SIGNED = re.compile(r"(?<![\w.])([+\-−])(\d+(?:\.\d+)?)(?!\d)")
_WORD_UNIT = re.compile(
    r"(?<!\w)(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"sixty|hundred)\s+(?:metres?|meters?|m|seconds?|secs?|s)(?!\w)", re.IGNORECASE)
_TAGGED =re.compile(r"[^\s,;:()\"']*#\w+")


def _plain(value):
    """As the value reads after a trip through the log file."""
    return json.loads(json.dumps(value))


def trusted_log(log: dict[str, dict], snap, flags: dict[str, dict]) -> dict[str, dict]:
    """The log entries this process can reproduce. The log file lives where the model works, so it is not
    believed: a query entry counts only if asking again gives the same answer, and a flag entry only if it is
    the flag the runner logged under that id."""
    out = {}
    for log_id, entry in log.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
            continue
        if entry.get("cmd") == "flag":
            good = log_id in flags and _plain(flags[log_id]) == entry["result"]
        else:
            good = _plain(query.answer(snap, entry.get("cmd"), entry.get("args"))) == entry["result"]
        if good:
            out[log_id] = entry
    return out


def _collect(value, key, numbers: list[float], shares: list[float], times: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if key in _MEASURED:
            numbers.append(float(value))
        if key in _SHARE_KEYS:
            shares.append(float(value))
        if key in _TIME_KEYS:
            times.append(float(value))
    elif isinstance(value, dict):
        for k, item in value.items():
            _collect(item, k, numbers, shares, times)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, key, numbers, shares, times)


def _pairs(entry: dict) -> set[tuple[int, int]]:
    """The NvM counts an answer supports: the living players per side in a `where`, or a flag's alive_before."""
    result, out = entry["result"], set()
    if entry["cmd"] == "where":
        alive = {"A": 0, "B": 0}
        for p in result.get("players") or []:
            if p.get("alive") and p.get("side") in alive:
                alive[p["side"]] += 1
        out |= {(alive["A"], alive["B"]), (alive["B"], alive["A"])}
    before = result.get("alive_before")
    if isinstance(before, dict) and "own" in before and "enemy" in before:
        out |= {(before["own"], before["enemy"]), (before["enemy"], before["own"])}
    return out


def _round_of(entry: dict):
    """The round an entry is about: every query's and every flag's first argument."""
    try:
        return int(entry["args"][0])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _in_moment(entry: dict, t: float, t1) -> bool:
    """P4 review F3: an answer asked for one second (where, distance, knew: a top-level `t`) is evidence only
    for an instance that second belongs to, from KNEW_WINDOW_S before it starts to its end. Flags and answers
    over a span (timeline, control) carry no single second and are tied to the round only."""
    asked = entry["result"].get("t")
    if entry.get("cmd") == "flag" or isinstance(asked, bool) or not isinstance(asked, (int, float)):
        return True
    return t - KNEW_WINDOW_S <= float(asked) <= float(t1 if t1 is not None else t)


def _matches(x: float, numbers: list[float], shares: list[float]) -> bool:
    # P4 review F2: the text's number carries no sign ("Impact of -250" reads as 250), so compare sizes.
    return (any(abs(x - abs(y)) < 0.051 or (float(x).is_integer() and round(abs(y)) == x) for y in numbers)
            or any(abs(y * 100 - x) < 0.51 for y in shares))          # a share of 0.5 may be written 50


def _strip_names(text: str, names: dict) -> str:
    """The text without this match's player names, full and without the tag, longest first, so a name with a
    space or a digit in it is not read as prose or as a number."""
    known = set()
    for name in names.values():
        if name:
            known |= {name, name.split("#")[0]}
    for name in sorted((k for k in known if k.strip()), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text, flags=re.IGNORECASE)
    return text


def _text_problems(text, numbers, shares, times, pairs, names) -> list[str]:
    problems = []
    text = _strip_names(text, names)
    for token in _TAGGED.findall(text):
        problems.append(f"{token} is not a player of this match")
    text = _TAGGED.sub(" ", text)
    for match in _CLOCK.finditer(text):
        seconds = int(match.group(1)) * 60 + int(match.group(2))
        if not any(abs(seconds - y) <= 1.0 for y in times):
            problems.append(f"the time {match.group(0)} is not within a second of any cited time")
    text = _CLOCK.sub(" ", text)
    for match in _VERSUS.finditer(text):
        if (int(match.group(1)), int(match.group(2))) not in pairs:
            problems.append(f"the count {match.group(0)} matches no cited where answer or flag")
    text = _VERSUS.sub(" ", text)
    text = _THOUSANDS.sub("", _ORDINAL.sub(" ", text))      # "2nd" is not a measurement; "12,000" is 12000
    for match in _WORD_UNIT.finditer(text):
        problems.append(f"'{match.group(0)}': write the figure, so it can be checked against an answer")
    for match in _SIGNED.finditer(text):
        # Step 5b: "+250 Impact" must not ride on a cited -250; an explicit sign has to match a cited value's.
        negative, size = match.group(1) in "-−", float(match.group(2))
        if not any(abs(abs(y) - size) < 0.051 and (y < 0) == negative for y in numbers):
            problems.append(f"the signed number {match.group(0)} has the opposite sign in every cited answer")
    for match in _NUMBER.finditer(text):
        if not _matches(float(match.group(0)), numbers, shares):
            problems.append(f"the number {match.group(0)} is not a measured value in a cited answer")
    return problems


def check_point(point: dict, log: dict[str, dict], snap, themes: tuple[str, ...], slot: int) -> list[str]:
    problems = []
    polarity = point.get("polarity")
    if polarity not in POLARITIES:
        problems.append(f"polarity must be one of {POLARITIES}")
    if point.get("theme") not in themes:
        problems.append(f"theme {point.get('theme')!r} is not in the playbook's themes {themes}")
    for key in ("title", "body"):
        if not isinstance(point.get(key), str) or not point[key].strip():
            problems.append(f"{key} is empty")
    if isinstance(point.get("title"), str) and len(point["title"].strip()) > TITLE_MAX:
        problems.append(f"title is over {TITLE_MAX} characters")
    instances = point.get("instances") if isinstance(point.get("instances"), list) else []
    if not instances:
        problems.append("the point has no instances")
    numbers: list[float] = [float(len(instances))]
    shares: list[float] = []
    times: list[float] = []
    pairs: set[tuple[int, int]] = set()
    captions = []
    for i, inst in enumerate(instances):
        label = f"instance {i + 1}"
        if not isinstance(inst, dict):
            problems.append(f"{label} is not an object")
            continue
        captions.append(str(inst.get("caption") or ""))
        n, t = inst.get("round"), inst.get("t")
        if isinstance(n, bool) or not isinstance(n, int):
            problems.append(f"{label}: round must be a whole number, got {n!r}")
            continue
        if n not in snap.round_numbers:
            problems.append(f"{label}: round {n} is not in this match")
            continue
        end = snap.view(n).t_end
        spans = [t] + ([inst["t1"]] if inst.get("t1") is not None else [])
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= end for v in spans):
            problems.append(f"{label}: a time is outside round {n} (0 to {end})")
            continue
        numbers.append(float(n))
        numbers.extend(float(v) for v in spans)
        times.extend(float(v) for v in spans)
        ids = inst.get("evidence")
        if not isinstance(ids, list) or any(not isinstance(log_id, str) for log_id in ids):
            problems.append(f"{label}: evidence must be a list of log ids")
            ids = []
        elif not ids:
            problems.append(f"{label}: cites no evidence")
        cited = []
        for log_id in ids:
            entry = log.get(log_id)
            if entry is None:
                problems.append(f"{label}: {log_id} is not in this run's log, or its answer could not be reproduced")
            elif "error" in entry["result"]:
                problems.append(f"{label}: {log_id} is an error answer, not evidence")
            elif _round_of(entry) != n:
                problems.append(f"{label}: {log_id} is about round {_round_of(entry)}, not round {n}")
            elif not _in_moment(entry, t, inst.get("t1")):
                problems.append(f"{label}: {log_id} was asked for second {entry['result']['t']}, outside this "
                                f"moment ({max(0.0, t - KNEW_WINDOW_S)} to {inst.get('t1') or t})")
            else:
                cited.append(entry)
                _collect(entry["result"], None, numbers, shares, times)
                pairs |= _pairs(entry)
        if polarity == "improve":
            if "team_knowledge" in snap.gaps(n):
                if NO_KNOWLEDGE_NOTE not in captions[-1].lower():
                    problems.append(f"{label}: round {n} has no team-knowledge data; the caption must say "
                                    f"'({NO_KNOWLEDGE_NOTE})'")
            else:
                side = snap.view(n).side(slot)
                if not any(e["cmd"] == "knew" and "enemies" in e["result"] and e["result"].get("side") == side
                           and t - KNEW_WINDOW_S <= float(e["result"]["t"]) <= t for e in cited):
                    problems.append(f"{label}: a mistake must cite a knew answer for side {side} in round {n}, "
                                    f"asked for a time from {max(0.0, t - KNEW_WINDOW_S)} to {t}")
    text = " ".join([str(point.get("title") or ""), str(point.get("body") or "")] + captions)
    return problems + _text_problems(text, numbers, shares, times, pairs, snap.names)
