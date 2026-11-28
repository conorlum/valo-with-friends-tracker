"""The replay-search filter parser (R2): `key=value` arguments to a frozen `Filters`.

Standard library only. An unknown key, a bad value, a repeated key (except `tag`), `as` with `side`, or an
our/their filter without `as`/`side` is a `FilterError` naming the key, never an empty result.
`since` and `until` are UTC calendar days of the match start (an evening match in the US can fall on the next day).
`ability` is a case-insensitive part of an ability's name (two characters or more); never a keybind letter.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

from app.search.match import BUYS, KINDS


class FilterError(ValueError):
    """A filter argument the parser refuses; the message names the key."""


Range = tuple[int, int]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STATE = re.compile(r"^([0-5])v([0-5])$")
_RANGE = re.compile(r"^(\d+)(?:\.\.(\d+))?$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")

_KEYS = (
    "kind", "map", "since", "until", "with", "circle", "as", "side", "our_buy", "their_buy", "won", "our_score",
    "their_score", "round", "planted", "site", "state", "post_plant", "left", "after", "before", "player", "agent",
    "team", "ability", "ult", "first", "tag", "page",
)
# Keys that only mean something from one team's point of view.
_PERSPECTIVE_KEYS = ("won", "our_buy", "their_buy", "our_score", "their_score", "state", "team")


@dataclass(frozen=True)
class Filters:
    kind: str | None = None
    map: str | None = None  # lower-cased
    since: _dt.date | None = None
    until: _dt.date | None = None
    with_: str | None = None
    circle: str | None = None
    as_: str | None = None
    side: str | None = None
    our_buy: str | None = None
    their_buy: str | None = None
    won: bool | None = None
    our_score: Range | None = None
    their_score: Range | None = None
    round: Range | None = None
    planted: bool | None = None
    site: str | None = None
    state: tuple[int, int] | None = None  # (ours, theirs)
    post_plant: bool | None = None
    left: float | None = None
    after: float | None = None
    before: float | None = None
    player: str | None = None
    agent: str | None = None
    team: str | None = None
    ability: str | None = None
    ult: bool | None = None
    first: bool | None = None
    tags: tuple[str, ...] = ()
    page: int = 1


def _bad(key: str, value: str, why: str) -> FilterError:
    return FilterError(f"{key}: {why} (got {value!r})")


def _bool(key: str, value: str) -> bool:
    if value not in ("0", "1"):
        raise _bad(key, value, "expected 0 or 1")
    return value == "1"


def _choice(key: str, value: str, options) -> str:
    v = value.lower()
    if v not in options:
        raise _bad(key, value, "expected one of " + ", ".join(sorted(options)))
    return v


def _range(key: str, value: str) -> Range:
    m = _RANGE.match(value)
    if not m:
        raise _bad(key, value, "expected n or a..b")
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) is not None else a
    if a > b:
        raise _bad(key, value, "range start is above its end")
    return (a, b)


def _seconds(key: str, value: str) -> float:
    if not _NUMBER.match(value):
        raise _bad(key, value, "expected a number of seconds")
    return float(value)


def _date(key: str, value: str) -> _dt.date:
    if not _DATE.match(value):
        raise _bad(key, value, "expected YYYY-MM-DD")
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise _bad(key, value, "not a real date") from None


def _state(key: str, value: str) -> tuple[int, int]:
    m = _STATE.match(value)
    if not m:
        raise _bad(key, value, "expected XvY with 0-5 each")
    return (int(m.group(1)), int(m.group(2)))


def _page(key: str, value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise _bad(key, value, "expected a whole number from 1")
    return int(value)


def parse(args: list[str]) -> Filters:
    """Parse `key=value` arguments; raises FilterError naming the key on any problem."""
    seen: dict[str, str] = {}
    tags: list[str] = []
    for arg in args:
        key, sep, value = arg.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not sep:
            raise FilterError(f"{arg!r}: expected key=value")
        if key not in _KEYS:
            raise FilterError(f"unknown filter key {key!r}")
        if not value:
            raise FilterError(f"{key}: no value")
        if key == "tag":
            tags.append(value)
            continue
        if key in seen:
            raise FilterError(f"{key}: given more than once")
        seen[key] = value

    if "as" in seen and "side" in seen:
        raise FilterError("as: cannot be combined with side")
    if "as" not in seen and "side" not in seen:
        for k in _PERSPECTIVE_KEYS:
            if k in seen:
                raise FilterError(f"{k}: needs a perspective (give as=<Riot ID> or side=attack|defense)")

    v: dict = {}
    for key, value in seen.items():
        if key == "kind":
            v["kind"] = _choice(key, value, KINDS)
        elif key == "map":
            v["map"] = value.lower()
        elif key == "since":
            v["since"] = _date(key, value)
        elif key == "until":
            v["until"] = _date(key, value)
        elif key == "with":
            v["with_"] = value
        elif key == "as":
            v["as_"] = value
        elif key == "side":
            v["side"] = _choice(key, value, ("attack", "defense"))
        elif key in ("our_buy", "their_buy"):
            v[key] = _choice(key, value, BUYS)
        elif key in ("won", "planted", "post_plant", "ult", "first"):
            v[key] = _bool(key, value)
        elif key in ("our_score", "their_score", "round"):
            v[key] = _range(key, value)
        elif key == "site":
            v["site"] = _choice(key, value, ("a", "b", "c")).upper()
        elif key == "state":
            v["state"] = _state(key, value)
        elif key in ("left", "after", "before"):
            v[key] = _seconds(key, value)
        elif key == "team":
            v["team"] = _choice(key, value, ("ours", "theirs"))
        elif key == "page":
            v["page"] = _page(key, value)
        elif key == "ability":
            # keybinds can change per player, so only an ability's name is searchable
            if len(value) < 2:
                raise FilterError("ability: give the ability's name, e.g. ability=owl")
            v[key] = value
        else:  # circle, player, agent
            v[key] = value

    if "left" in v:
        if v.get("post_plant") is False:
            raise FilterError("left: implies post_plant=1, which conflicts with post_plant=0")
        v["post_plant"] = True
    return Filters(tags=tuple(tags), **v)
