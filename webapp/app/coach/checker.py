"""Decides whether a point the model wrote may be published (docs/superpowers/specs/2026-10-12-ai-coach-design.md,
"The checker"). Plain code over the run log: a claim stands only on answers the model actually got, about the
round it is talking about, and only on the measured values in them."""

from __future__ import annotations

import json
import re

from app.coach import knowledge, query

NO_KNOWLEDGE_NOTE = "no team-knowledge data"
POLARITIES = ("good", "improve")
KNEW_WINDOW_S = 30.0        # a mistake's knew answer must be this recent; a first value, reviewed with the owner
SPAN_MAX_S = 10.0           # the longest an instance's t..t1 may run; a first value
BODY_MAX = 4000             # longer text is refused before the regexes run
TITLE_MAX = 160            # app/services/coach.py validate_payload's limit, checked here so it can be repaired

# The keys of a query answer or a flag whose numbers are measurements. Everything else (slot, u, v, yaw, round)
# is an identifier or a coordinate and grounds nothing.
_TIME_KEYS = frozenset({"t", "t0", "t1", "first_seen", "last_seen", "died_at", "decided_at"})
_SHARE_KEYS = frozenset({"a", "b", "contested", "none"})
_MEASURED = _TIME_KEYS | _SHARE_KEYS | frozenset({
    "metres", "impact", "kill_impact", "death_impact", "damage", "econ", "trade_credit", "kills", "deaths",
    "assists", "seconds", "hits", "nearest_teammate_m", "after_s", "teammates_alive", "own",
    "enemy", "enemies_seen", "hold_s", "metres_min", "metres_max", "loadout", "remaining",
    "spike_left_s"})
# A number written with a unit is held to answers of that kind: "20 m" must be a distance, never a time or a
# round that happens to be 20 (PR 132 review, finding 1).
_METRE_KEYS = frozenset({"metres", "nearest_teammate_m", "metres_min", "metres_max"})
_SECOND_KEYS = _TIME_KEYS | frozenset({"seconds", "after_s", "hold_s", "spike_left_s"})
SEEN_SLACK_S = 1.0          # a distance to an enemy counts in a mistake if they were in view this close to it

_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_FIFTY = re.compile(r"(?<![\w/])50\s?/\s?50(?![\w/])")                 # "a 50/50" is a figure of speech
_ROUND = re.compile(r"(?<!\w)round\s+#?(\d+)(?!\.?\d)", re.IGNORECASE)
_ROUNDS = re.compile(r"(?<!\w)rounds\s+(\d+(?:\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)\d+)*)(?!\.?\d)", re.IGNORECASE)
_TALLY = re.compile(r"(?<![\w.#])(\d+)\s+(?:times?|instances?|moments?|rounds?)(?!\w)", re.IGNORECASE)
_FIGURE = r"(\d+(?:\.\d+)?)"
_UNIT = r"(m|metres?|meters?|s|secs?|seconds?)(?!\w)"
_UNIT_RANGE = re.compile(r"(?<![\w.#])" + _FIGURE + r"\s?(?:to|and|-|–)\s?" + _FIGURE + r"\s?" + _UNIT, re.IGNORECASE)
_UNIT_ONE = re.compile(r"(?<![\w.#])" + _FIGURE + r"\s?" + _UNIT, re.IGNORECASE)
# P4 review F1: a unit written against the digits ("5m", "3s", "2vs1") must not hide the number from the check.
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_COUNT = r"(\d|one|two|three|four|five)"
_VERSUS = re.compile(r"(?<!\w)" + _COUNT + r"\s?-?\s?(?:versus|against|vs|v|on)\.?\s?-?\s?" + _COUNT + r"(?!\w)",
                     re.IGNORECASE)
_ORDINAL = re.compile(r"(?<![\w.#])\d+(?:st|nd|rd|th)(?!\w)", re.IGNORECASE)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"(?<![\w.#])\d+(?:\.\d+)?(?!\.?\d|#)")
# A sign counts only where it is clearly a signed figure: at the start, after ( = : or "of/was/is/at/by/gained/lost".
# "alone -121.2 m" is a dash, not a sign.
_SIGNED = re.compile(r"(?:^|[(=:]\s?|(?<!\w)(?:of|was|is|at|by|gained|lost)\s)([+\-−])(\d+(?:\.\d+)?)(%?)(?!\d)",
                     re.IGNORECASE)
_NUMBER_WORDS = (r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                 r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                 r"hundred|thousand|dozen|half\s+a")
# Only measurements (a distance, a duration) must be written as figures. A spelled-out count ("two kills", "one
# round") passes unchecked: the owner would rather a loose point be published than an honest one refused.
_WORD_UNIT = re.compile(
    r"(?<!\w)(?:" + _NUMBER_WORDS + r")\s+(?:metres?|meters?|m|seconds?|secs?|s)(?!\w)", re.IGNORECASE)
_TAGGED = re.compile(r"[^\s,;:()\"'#]+#\w+")          # a name before the #: "#1 job" is not a player


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
        if not isinstance(entry.get("cmd"), str):
            continue
        if entry["cmd"] == "flag":
            # the whole entry must be the one the runner logged: the round is read from args, which are writable
            ours = {key: entry.get(key) for key in ("cmd", "args", "result")}
            good = log_id in flags and _plain(flags[log_id]) == ours
        else:
            try:
                good = _plain(query.answer(snap, entry["cmd"], entry.get("args"))) == entry["result"]
            except Exception:                       # a hostile line must make the entry untrusted, not crash
                good = False
        if good:
            out[log_id] = entry
    return out


class _Cited:
    """What the cited answers measured, by kind. `rounds`, `tallies` and the instance times in `seconds` are the
    point's own figures: they ground "round 7", "3 times" and "at 20 s", never a bare number or a distance."""

    def __init__(self):
        self.numbers: list[float] = []
        self.shares: list[float] = []
        self.times: list[float] = []
        self.metres: list[float] = []
        self.seconds: list[float] = []
        self.pairs: set[tuple[int, int]] = set()
        self.areas: set[str] = set()
        self.rounds: set[int] = set()
        self.tallies: set[int] = set()


def _collect(value, key, cited: _Cited, hidden: frozenset = frozenset()) -> None:
    """`hidden`: players whose place this answer may not ground (an enemy the side could not see)."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if key in _MEASURED:
            cited.numbers.append(float(value))
        if key in _SHARE_KEYS:
            cited.shares.append(float(value))
        if key in _TIME_KEYS:
            cited.times.append(float(value))
        if key in _METRE_KEYS:
            cited.metres.append(float(value))
        if key in _SECOND_KEYS:
            cited.seconds.append(float(value))
    elif isinstance(value, str):
        if key == "area":
            cited.areas.add(value)
    elif isinstance(value, dict):
        if key == "area" and isinstance(value.get("name"), str):
            cited.areas.add(value["name"])
            return
        place_hidden = value.get("alive") is True and value.get("name") in hidden
        for k, item in value.items():
            if not (place_hidden and k == "area"):
                _collect(item, k, cited, hidden)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, key, cited, hidden)


def _unseen_enemy(snap, n: int, side: str, name, t: float) -> bool:
    """True when `name` is a living enemy of `side` whom that side did not have in view around second t."""
    try:
        slot = snap.slot_of(name)
    except Exception:                               # noqa: BLE001 - not a player of this match: nothing to hide
        return False
    view = snap.view(n)
    if view.side(slot) == side or not view.alive(slot, t):
        return False
    knew = (snap.control(n) or {}).get("knew")
    if not knew:
        return False
    for moment in (t, max(0.0, t - SEEN_SLACK_S), min(view.t_end, t + SEEN_SLACK_S)):
        if any(e["slot"] == slot and e["in_view"] for e in knowledge.known_enemies(knew, side, moment, view)):
            return False
    return True


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
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
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
            short = name.split("#")[0]
            # a name that is only digits is left in when written without its tag: it must not hide a number
            known |= {name} if short.strip().isdigit() else {name, short}
    for name in sorted((k for k in known if k.strip()), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text, flags=re.IGNORECASE)
    return text


def _near(x: float, pool: list[float]) -> bool:
    return any(abs(x - abs(y)) < 0.051 or (float(x).is_integer() and round(abs(y)) == x) for y in pool)


def _text_problems(text, cited: _Cited, names, area_names=(), improve: bool = False) -> list[str]:
    problems = []
    numbers, shares, times, pairs = cited.numbers, cited.shares, cited.times, cited.pairs
    for area in area_names:                       # as the brief spells them, so "a site" in prose is not an area
        if area not in cited.areas and re.search(r"(?<![\w-])" + re.escape(area) + r"(?![\w-])", text):
            problems.append(f"the area {area} is not in a cited answer"
                            + (" (an enemy's place counts only while your side could see them)" if improve else ""))
    text = _strip_names(text, names)
    for token in _TAGGED.findall(text):
        problems.append(f"{token} is not a player of this match")
    text = _FIFTY.sub(" ", _TAGGED.sub(" ", text))
    for match in _CLOCK.finditer(text):
        seconds = int(match.group(1)) * 60 + int(match.group(2))
        if not any(abs(seconds - y) <= 1.0 for y in times):
            problems.append(f"the time {match.group(0)} is not within a second of any cited time")
    text = _CLOCK.sub(" ", text)
    for match in _VERSUS.finditer(text):
        counts = tuple(int(g) if g.isdigit() else _COUNT_WORDS[g.lower()] for g in match.groups())
        # "a 1v1" names a duel, the playbook's own word for it, not how many were alive
        if counts != (1, 1) and counts not in pairs:
            problems.append(f"the count {match.group(0)} matches no cited where answer or flag")
    text = _VERSUS.sub(" ", text)
    text = _THOUSANDS.sub("", _ORDINAL.sub(" ", text))      # "2nd" is not a measurement; "12,000" is 12000
    # The point's own figures, each only in its own form: "round 7", "rounds 4, 9 and 15", "3 times".
    for match in list(_ROUND.finditer(text)) + list(_ROUNDS.finditer(text)):
        for digits in re.findall(r"\d+", match.group(1)):
            if int(digits) not in cited.rounds:
                problems.append(f"round {digits} is not the round of any instance of this point")
    text = _ROUNDS.sub(" ", _ROUND.sub(" ", text))
    text = _TALLY.sub(lambda m: " " if int(m.group(1)) in cited.tallies else m.group(0), text)

    def unit_problem(figure: str, unit: str) -> None:
        metres = unit.lower().startswith("m")
        if not _near(float(figure), cited.metres if metres else cited.seconds):
            problems.append(f"the number {figure} {unit} is not a {'distance' if metres else 'time or duration'} "
                            "in a cited answer")

    for match in _UNIT_RANGE.finditer(text):
        unit_problem(match.group(1), match.group(3))
        unit_problem(match.group(2), match.group(3))
    text = _UNIT_RANGE.sub(" ", text)
    for match in _UNIT_ONE.finditer(text):
        unit_problem(match.group(1), match.group(2))
    for match in _WORD_UNIT.finditer(text):
        problems.append(f"'{match.group(0)}': write the figure, so it can be checked against an answer")
    for match in _SIGNED.finditer(text):
        # Step 5b: "+250 Impact" must not ride on a cited -250; an explicit sign has to match a cited value's.
        negative, size = match.group(1) != "+", float(match.group(2))
        if match.group(3):                       # a signed percentage is a share, times 100
            agrees = any(abs(abs(y) * 100 - size) < 0.51 and (y < 0) == negative for y in shares)
        else:
            agrees = any((abs(abs(y) - size) < 0.051 or (size.is_integer() and round(abs(y)) == size))
                         and (y < 0) == negative for y in numbers)
        if not agrees:
            problems.append(f"the signed number {match.group(0)} has the opposite sign in every cited answer")
    text = _UNIT_ONE.sub(" ", text)                         # checked above, against answers of their own kind
    for match in _NUMBER.finditer(text):
        if not _matches(float(match.group(0)), numbers, shares):
            problems.append(f"the number {match.group(0)} is not a measured value in a cited answer")
    return problems


def shape_problems(point, snap, themes: tuple[str, ...]) -> list[str]:
    """What stops a point from being stored and shown at all, its evidence aside. The owner wants three points a
    side even when some are weak, so a point whose evidence fails after its repair is still published if it has
    none of these (app/coach/runner.py)."""
    if not isinstance(point, dict):
        return ["the point is not an object"]
    problems = []
    if point.get("polarity") not in POLARITIES:
        problems.append(f"polarity must be one of {POLARITIES}")
    if point.get("theme") not in themes:
        problems.append(f"theme {point.get('theme')!r} is not in the playbook's themes {themes}")
    for key in ("title", "body"):
        if not isinstance(point.get(key), str) or not point[key].strip():
            problems.append(f"{key} is empty")
    if isinstance(point.get("title"), str) and len(point["title"].strip()) > TITLE_MAX:
        problems.append(f"title is over {TITLE_MAX} characters")
    instances = point.get("instances")
    if not isinstance(instances, list) or not instances:
        return problems + ["the point has no instances"]
    captions = []
    for i, inst in enumerate(instances):
        label = f"instance {i + 1}"
        if not isinstance(inst, dict):
            problems.append(f"{label} is not an object")
            continue
        captions.append(str(inst.get("caption") or ""))
        n = inst.get("round")
        if isinstance(n, bool) or not isinstance(n, int) or n not in snap.round_numbers:
            problems.append(f"{label}: round {n!r} is not a round of this match")
            continue
        end = snap.view(n).t_end
        spans = [inst.get("t")] + ([inst["t1"]] if inst.get("t1") is not None else [])
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= end for v in spans):
            problems.append(f"{label}: a time is outside round {n} (0 to {end})")
    text = " ".join([str(point.get("title") or ""), str(point.get("body") or "")] + captions)
    if len(text) > BODY_MAX:
        problems.append(f"the text is over {BODY_MAX} characters")
    return problems


def check_point(point: dict, log: dict[str, dict], snap, themes: tuple[str, ...], slot: int) -> list[str]:
    if not isinstance(point, dict):
        return ["the point is not an object"]
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
    cited = _Cited()
    cited.tallies.add(len(instances))
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
        if len(spans) > 1 and not t <= spans[1] <= t + SPAN_MAX_S:
            problems.append(f"{label}: t1 must be within {SPAN_MAX_S:g} s after t")
            continue
        cited.rounds.add(n)
        cited.tallies.add(len(cited.rounds))
        cited.times.extend(float(v) for v in spans)          # "0:20"
        cited.seconds.extend(float(v) for v in spans)        # "at 20 s", "from 20 to 25 s": never a bare number
        side = snap.view(n).side(slot)
        guarded = polarity == "improve" and "team_knowledge" not in snap.gaps(n)
        ids = inst.get("evidence")
        if not isinstance(ids, list) or any(not isinstance(log_id, str) for log_id in ids):
            problems.append(f"{label}: evidence must be a list of log ids")
            ids = []
        elif not ids:
            problems.append(f"{label}: cites no evidence")
        entries = []
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
                entries.append(entry)
                result, hidden = entry["result"], frozenset()
                # A mistake is judged on what the side could know: an enemy they could not see grounds no
                # distance and no place (PR 132 review, finding 3). `knew` gives their last sighting instead.
                if guarded and entry["cmd"] == "distance" and "metres" in result:
                    unseen = [name for name in (result.get("a"), result.get("b"))
                              if _unseen_enemy(snap, n, side, name, float(result["t"]))]
                    if unseen:
                        problems.append(f"{label}: {log_id} measures to {unseen[0]}, whom side {side} could not "
                                        f"see at {result['t']}; a mistake cannot rest on it")
                        continue
                if guarded and entry["cmd"] == "where":
                    hidden = frozenset(p.get("name") for p in result.get("players") or [] if isinstance(p, dict)
                                       and _unseen_enemy(snap, n, side, p.get("name"), float(result["t"])))
                _collect(result, None, cited, hidden)
                cited.pairs |= _pairs(entry)
        if polarity == "improve":
            if "team_knowledge" in snap.gaps(n):
                if NO_KNOWLEDGE_NOTE not in captions[-1].lower():
                    problems.append(f"{label}: round {n} has no team-knowledge data; the caption must say "
                                    f"'({NO_KNOWLEDGE_NOTE})'")
            else:
                if not any(e["cmd"] == "knew" and "enemies" in e["result"] and e["result"].get("side") == side
                           and t - KNEW_WINDOW_S <= float(e["result"]["t"]) <= t for e in entries):
                    problems.append(f"{label}: a mistake must cite a knew answer for side {side} in round {n}, "
                                    f"asked for a time from {max(0.0, t - KNEW_WINDOW_S)} to {t}")
    text = " ".join([str(point.get("title") or ""), str(point.get("body") or "")] + captions)
    if len(text) > BODY_MAX:
        return problems + [f"the text is over {BODY_MAX} characters"]
    area_names = snap.view(snap.round_numbers[0]).area_names() if snap.round_numbers else []
    return problems + _text_problems(text, cited, snap.names, area_names, polarity == "improve")
