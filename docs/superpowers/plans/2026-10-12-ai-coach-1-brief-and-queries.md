# AI Coach 1: Match Brief, Detectors and Query Commands - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one linked replay into a local snapshot, a per-player match brief with flagged moments, and read-only query commands whose every answer is logged.

**Architecture:** One DB read builds a JSON snapshot of the match on disk (site data, decoded round blobs, per-area control, what each team knew). Everything after that is pure code over the snapshot: `RoundView` primitives, detectors, the brief, and query functions that append to a run log. The model (plan 3) never touches the database.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 (snapshot read only), pytest. Standard library otherwise. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-ai-coach-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/ai-coach`, branch `worktree-ai-coach`. All paths below are relative to `webapp/` unless they start with `docs/`.
- The worktree has no virtualenv. Run Python as `$PY`, meaning `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe`, from the worktree's `webapp/` directory.
- This repository is public. Never commit a credential, a player name, a match id or a snapshot. Snapshots go under the system temp directory.
- Read-only towards the site: nothing in this plan writes to any database. Impact scoring code (`app/scoring/`) and the scoring models are read, never edited.
- Nothing here may be defined by `scripts/tracked_players.json`.
- No heredocs in shell commands. Commit messages go in a scratch file and are passed with `git commit -F`.
- Only the owner merges into `main`. Never run `gh pr merge`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Times are seconds since the round's start on the replay clock, as the round blob stores them (`app/replays/format.py`). Map positions are `u`/`v` ints in 0..10000.
- A missing data source is reported as missing. No function returns an empty answer that could be read as "nothing happened".

## Review Focus

1. A round with no control row, or control without team knowledge: `knew` and `control` queries must answer `{"available": false, "reason": ...}`, and the stack detector must skip the round. Tests in Tasks 4, 6 and 8.
2. A player token that matches nobody, or matches two players: the query fails with a message listing the valid names. Test in Task 8.
3. A time outside the round, or a round number the match does not have: the query fails with the valid range. Test in Task 8.
4. A map with no callouts (a map newer than `callouts.json`): areas read as `null`, nothing crashes, and the brief lists `areas` as a data gap. Tests in Tasks 1 and 7.
5. A position asked for a dead player: the answer says `alive: false` with where they died, never a stale position presented as current. Test in Task 2.

## Amendments after the external review (2026-10-12)

A reviewer checked this plan against the code before anything was built. Every finding was confirmed against the source and is folded into the tasks below; this list is where to look for each.

| Finding | What was wrong | Where it is fixed |
| --- | --- | --- |
| F1 | A survivor's `alive` interval ends in `None`; the first draft compared against it and would crash on any real round. | Task 2 (`_intervals`, the toy, a test) |
| F4 | The blob's `plant`/`defuse` are always `None`; damage was left out of the timeline. | Task 8 (site times, `RoundView.damage`) |
| F5 | Flags from post-decision kills, zero-second flash hits, the last player alive, and a stack flag blind to attack/defence. | Task 6 |
| F6 | The "leaves within the hold" fixture left B before the stack was known, so it tested nothing. | Task 6 (new fixture, and its mirror) |
| F7 | `knew` is sight only; sound and other locating events are stored elsewhere, without a place. | Tasks 3, 4, 8 (`signals`) |
| F8 | Malformed arguments crashed the query tool unlogged; `distance` crashed with no track sample. | Task 8 (`answer`) |
| F10 | Sides read from the blob only, which is `None` when the condenser could not resolve them. | Tasks 2, 5 (`groups`) |
| F11 | The brief lacked the role per round, both teams' economy and the player's utility; health was never read. | Tasks 7, 8 |
| F12 | A one-moment `control` query answered "no ticks"; deaths only the site has were ignored. | Tasks 4, 2, 5 |

## Amendments after the P4 review (2026-10-12, second pass)

An independent reviewer read the parts the table above rewrote, against the code. The full findings and what was done with each are in `2026-10-12-ai-coach-amendments-review.md`. **Where a row below and a task's text disagree, the row wins.** Each row is an instruction to the task's implementer, who adds the test named; a stated pass count grows by the tests added.

| Finding | Change | Where |
| --- | --- | --- |
| R-F9 | When the blob gives a player no side, RoundView must derive the group as the engine does, from the round's role: `{"attack": "A", "defense": "B"}.get(role)` with the role from the link's `sides[slot]` (`app/control/engine.py:319-321`), and fall back to the page context's fixed team group only when the link has no role either. Test to add: a round whose blob sides are `None`, whose link says slot 0 defends, and whose page group for slot 0 is "A": `side(0)` is "B". | Task 2 |
| R-F9 guard | `query.knew` and `query.control` answer `{"available": False, "reason": "side_groups_disagree"}` when the control header's `group_side` contradicts the round's role for the player's group. One test. | Task 8 |
| R-F10 | `position` for a player who is alive at `t` but has no sample at or before `t`, or whose nearest earlier sample is more than 1 s old, returns `{"alive": True, "u": None, "v": None, "area": None, "position": "unknown"}`, never `alive: False`. The test at "a living player with no sample" is rewritten to pin this (say why in the test's docstring); `where` lists such a player as alive with no place. One more test: a track with a gap mid-life answers "unknown" inside the gap. | Tasks 2, 8 |
| R-F11 | `_stack` steps past a second the player is not alive (`continue`) instead of ending the scan (`break`). Test to add: a life that starts at second 5 is still flagged for a stack at second 20. | Task 6 |
| R-F12 | The trade search reads `_live_kills(view)`, not `view.kills`. Test to add: the killer dying after `t_decided` is not a trade. | Task 6 |
| R-F13 | `answer` converts the arguments inside the `try` and calls the command outside it, so a fault inside a query is a traceback, not a "wrong arguments" answer the checker would trust. | Task 8 |
| R-F14 | The "dead: no health" assertion could not fail (round 1 had no vitals). Give slot 0 a vitals event at 13 s in round 1 and assert `"health" not in players[0]` once it is dead. | Task 8 |
| R-F8 | Health is "a sense of low or healthy, never a number" (the owner's standing rule for the viewer; applied here as the cautious reading, `PROVISIONAL`, the owner decides). `_health` returns one word from the viewer's bands (`app/static/js/replay.js` `healthWord`): with `pct = 100 * hp / mhp`, `"healthy"` above 50, `"hurt"` above 25, else `"low"`; `None` with no event. No `hp` or `shield` key leaves `where`. The health test asserts `"healthy"` for the fixture's 61 of 100, and a second event at 20 of 100 asserts `"low"`. | Task 8 |
| R-F15 | Not changed. If `./q` calls are slow in plan 3, move `snapshot`'s two service imports inside `snapshot.read`. | Task 8 |

Not built, and said so in the spec instead: "kill lines" and "timing gaps" as data gaps. The coach reads neither source, so there is nothing to report missing.

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/fetch_callouts.py` | One-off: fetch Riot's callout points, convert to `u`/`v`, write the asset. |
| `app/static/data/callouts.json` | The asset: per map, named points with their super region (A, B, Mid, ...). |
| `app/coach/__init__.py` | Empty. |
| `app/coach/areas.py` | Nearest-callout naming of a `u`/`v` point. |
| `app/coach/roundview.py` | One round's primitives: position, alive, sides, distance, area. |
| `app/coach/knowledge.py` | What a side had seen of each enemy by a time. |
| `app/coach/control_areas.py` | Stored control bytes to per-area, per-tick ownership counts. |
| `app/coach/snapshot.py` | Build a snapshot from the DB, save it, load it. |
| `app/coach/detectors.py` | Thresholds and the flag detectors. |
| `app/coach/brief.py` | The match brief for one player. |
| `app/coach/query.py` | The six query functions and the run log. |
| `scripts/coach_snapshot.py`, `scripts/coach_brief.py`, `scripts/coach_query.py` | Thin CLIs. |
| `tests/coach/toys.py` | Hand-built rounds for the tests. |
| `tests/coach/test_*.py` | One test file per module. |

---

### Task 1: Callouts asset and area naming

**Files:**
- Create: `scripts/fetch_callouts.py`, `app/static/data/callouts.json`, `app/coach/__init__.py`, `app/coach/areas.py`
- Test: `tests/coach/test_areas.py` (no `__init__.py` in `tests/coach`, like `tests/replays`)

**Interfaces:**
- Consumes: `app.replays.condense.load_maps() -> dict[str, MapInfo]`; `MapInfo.to_uv(x, y) -> tuple[int, int]`. `app/static/data/maps.json` entries carry `uuid`.
- Produces: `areas.area_at(map_name: str, u: float, v: float, table: dict | None = None) -> dict | None` returning `{"name": "A Main", "super": "A"}`; `areas.callouts(map_name, table=None) -> list[dict]`; `fetch_callouts.convert(api_maps: list[dict], maps: dict) -> dict`.

Background: the site has no named map areas. The community site valorant-api.com (`https://valorant-api.com/v1/maps`, not run by Riot; the source `maps.json` came from) publishes the game's own map data. It lists callouts per map as a `regionName`, a `superRegionName` (`A`, `B`, `C`, `Mid`, `Attacker Side`, `Defender Side`) and a world `location`. A point's area is its nearest callout.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_areas.py`:

```python
from app.coach import areas
from scripts import fetch_callouts

TABLE = {"Toy": [{"name": "A Site", "super": "A", "u": 1000, "v": 1000},
                 {"name": "B Site", "super": "B", "u": 9000, "v": 9000}]}


def test_area_is_the_nearest_callout():
    assert areas.area_at("Toy", 1200, 900, TABLE) == {"name": "A Site", "super": "A"}
    assert areas.area_at("Toy", 8000, 8500, TABLE) == {"name": "B Site", "super": "B"}


def test_a_map_without_callouts_has_no_areas():
    assert areas.area_at("Unknown", 5000, 5000, TABLE) is None
    assert areas.callouts("Unknown", TABLE) == []


class FakeMap:
    def to_uv(self, x, y):
        return int(y), int(x)


def test_convert_names_callouts_and_matches_maps_by_uuid():
    api = [{"uuid": "u-1", "displayName": "Ignored name",
            "callouts": [{"regionName": "Main", "superRegionName": "A", "location": {"x": 10.0, "y": 20.0}}]},
           {"uuid": "u-2", "displayName": "No callouts", "callouts": None},
           {"uuid": "u-9", "displayName": "Not ours", "callouts": []}]
    out = fetch_callouts.convert(api, {"Toy": FakeMap(), "Bare": FakeMap()}, {"Toy": "u-1", "Bare": "u-2"})
    assert out == {"Toy": [{"name": "A Main", "super": "A", "u": 20, "v": 10}], "Bare": []}
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_areas.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.coach'`.

- [ ] **Step 3: Implement**

`app/coach/__init__.py`: empty file.

`app/coach/areas.py`:

```python
"""Named map areas: a point's area is its nearest in-game callout (app/static/data/callouts.json, written by
scripts/fetch_callouts.py). A map the file does not have has no areas; callers get None, never a guess."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CALLOUTS_FILE = Path(__file__).resolve().parents[1] / "static" / "data" / "callouts.json"
SITES = frozenset({"A", "B", "C"})


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(CALLOUTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def callouts(map_name: str, table: dict | None = None) -> list[dict]:
    return (table if table is not None else _table()).get(map_name, [])


def area_at(map_name: str, u: float, v: float, table: dict | None = None) -> dict | None:
    best = None
    for c in callouts(map_name, table):
        d = (c["u"] - u) ** 2 + (c["v"] - v) ** 2
        if best is None or d < best[0]:
            best = (d, c)
    return None if best is None else {"name": best[1]["name"], "super": best[1]["super"]}
```

`scripts/fetch_callouts.py`:

```python
"""Writes app/static/data/callouts.json from valorant-api.com (a community site, not Riot's) map data. Run once, and again when a map is added:

    $PY scripts/fetch_callouts.py

No database. A map is matched by the `uuid` in app/static/data/maps.json, never by its display name."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

URL = "https://valorant-api.com/v1/maps"
OUT = WEBAPP_ROOT / "app" / "static" / "data" / "callouts.json"


def convert(api_maps: list[dict], maps: dict, uuid_of: dict[str, str]) -> dict:
    by_uuid = {m["uuid"]: m for m in api_maps}
    out = {}
    for name in sorted(maps):
        rows = []
        for c in (by_uuid.get(uuid_of.get(name), {}).get("callouts") or []):
            u, v = maps[name].to_uv(c["location"]["x"], c["location"]["y"])
            rows.append({"name": f'{c["superRegionName"]} {c["regionName"]}', "super": c["superRegionName"],
                         "u": u, "v": v})
        out[name] = sorted(rows, key=lambda r: (r["name"], r["u"], r["v"]))
    return out


def main() -> int:
    from app.replays.condense import load_maps

    raw = json.loads((WEBAPP_ROOT / "app" / "static" / "data" / "maps.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(URL, timeout=30) as response:
        api_maps = json.load(response)["data"]
    table = convert(api_maps, load_maps(), {name: entry["uuid"] for name, entry in raw.items()})
    OUT.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, rows in table.items():
        print(f"{name}: {len(rows)} callouts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```


- [ ] **Step 4: Run tests, then generate the asset**

Run: `$PY -m pytest tests/coach/test_areas.py -q` — Expected: 3 passed.
Run: `$PY scripts/fetch_callouts.py` — Expected: one line per map, Ascent 22, every competitive map above 10.

- [ ] **Step 5: Check the asset against a real map, by eye**

Run: `$PY -c "from app.coach import areas; print(areas.area_at('Ascent', 5000, 5000)); print(sorted({c['super'] for c in areas.callouts('Ascent')}))"`
Expected: a Mid area for the centre, and supers `['A', 'Attacker Side', 'B', 'Defender Side', 'Mid']`. If the centre of Ascent is not a Mid area, `to_uv`'s axis order has been applied wrongly in `convert`: stop and fix before committing.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/coach webapp/tests/coach webapp/scripts/fetch_callouts.py webapp/app/static/data/callouts.json
git commit -F <scratch file: "Add named map areas from Riot's callouts">
```

---

### Task 2: RoundView primitives

**Files:**
- Create: `app/coach/roundview.py`, `tests/coach/toys.py`
- Test: `tests/coach/test_roundview.py`

**Interfaces:**
- Consumes: `app.replays.format.decode_segment(segment, hz) -> list[(t, u, v, yaw)]`, `fmt.encode_segment(t0, u, v, yaw)`; `areas.area_at`.
- Produces:
  - `RoundView(blob: dict, names: dict[int, str], uv_per_unit: float, map_name: str, callout_table: dict | None = None, groups: dict[int, str] | None = None, db_deaths: list | tuple = ())`. `groups` is each slot's side group from the link, used where the blob has none; `db_deaths` is `[[slot, t], ...]` on the replay clock, deaths the site's data has and the replay lacks.
  - `.number -> int`, `.t_end -> float`, `.t_decided -> float`, `.slots -> list[int]`
  - `.utility(by: int | None = None) -> list[dict]`, `.damage() -> list[dict]` (the blob's `util` rows with names in place of slots, for the brief and the timeline)
  - `.side(slot) -> str` (`"A"` or `"B"`), `.teammates(slot) -> list[int]` (excludes `slot`), `.enemies(slot) -> list[int]`
  - `.alive(slot, t) -> bool`, `.living(side, t) -> list[int]`
  - `.position(slot, t) -> dict` : `{"alive": True, "u", "v", "yaw", "area"}` or `{"alive": False, "died_at", "u", "v", "area"}` or `{"alive": False, "died_at": None}` when there is no track
  - `.distance_m(a, b, t) -> float | None` (None unless both are alive with positions)
  - `.metres(uv_distance) -> float`
  - `.kills -> list[dict]` sorted by `t` (the blob's `kills`)
  - `toys.round_blob(...)`, `toys.stand(...)`, `toys.NAMES`, `toys.UV_PER_UNIT` for later tests.

Blob facts (from `app/replays/format.py` and `condense.py`, checked 2026-10-12):

- `players` is `[{"slot", "agent", "side"}]`. `side` is the side group `"A"`/`"B"`, and it is `None` for all ten when the condenser could not resolve sides (`condense.py:1465`). The engine then falls back to the link (`app/control/engine.py:318-324`), and so does `RoundView`, through `groups`.
- `alive[str(slot)]` is `[[t0, t1, reason, ...], ...]`, and **a life that lasts to the end of the round has `t1 = None`** (`condense.py:1012`, `:1484`). Read `None` as "never ended", as `engine.py:388` does.
- A death the site's data has and the replay lacks is not in `alive` or `kills`. The engine ends that life at the death (`engine.py:389-394`, `SAME_DEATH_S = 1.0`); `RoundView` does the same with `db_deaths`.
- `tracks[str(slot)]` is a list of segments; `kills` is `[{"i", "t", "killer", "victim", "u", "v"}]`, and kills after `t_decided` stay in the list.
- `util` rows: `flash`/`nearsight` (`hits` is `[[slot, hit t, duration s | null], ...]`, a hit kept "whatever its duration, zero included"), `ability`, `cast`, `shot`, and `damage` (`{"t", "t1", "by", "target", "src", "wall", "n"}`).
- `uv_per_unit` is `u`/`v` units per world centimetre, so metres = uv / uv_per_unit / 100.

- [ ] **Step 1: Write the toys**

`tests/coach/toys.py`:

```python
"""Hand-built rounds for the coach tests. Slots 0-4 are side A, 5-9 side B. UV_PER_UNIT 0.7 is Ascent's, so
70 uv = 1 m. Every player stands still unless a test passes its own track."""

from app.replays import format as fmt

HZ = 4
UV_PER_UNIT = 0.7
NAMES = {i: f"P{i}" for i in range(10)}
CALLOUTS = {"Toy": [{"name": "A Site", "super": "A", "u": 2000, "v": 2000},
                    {"name": "B Site", "super": "B", "u": 8000, "v": 8000},
                    {"name": "Mid Courtyard", "super": "Mid", "u": 5000, "v": 5000}]}


def stand(u, v, t0=0.0, t1=60.0, yaw=0):
    n = int((t1 - t0) * HZ) + 1
    return [fmt.encode_segment(t0, [u] * n, [v] * n, [yaw] * n)]


def walk(u0, v0, u1, v1, t0, t1):
    n = int((t1 - t0) * HZ) + 1
    us = [round(u0 + (u1 - u0) * i / (n - 1)) for i in range(n)]
    vs = [round(v0 + (v1 - v0) * i / (n - 1)) for i in range(n)]
    return [fmt.encode_segment(t0, us, vs, [0] * n)]


def round_blob(positions, kills=(), util=(), t_end=60.0, t_decided=None, n=1, tracks=None, player_state=True):
    """`positions`: {slot: (u, v)}. A killed player's track and alive interval end at their death; a survivor's
    interval ends in None, as the condenser stores it."""
    died = {k["victim"]: k["t"] for k in kills}
    blob = {"v": 1, "round": n, "map": "Toy", "hz": HZ, "t_start": 0.0, "t_end": t_end,
            "t_decided": t_end if t_decided is None else t_decided,
            "players": [{"slot": s, "agent": "Jett", "side": "A" if s < 5 else "B"} for s in sorted(positions)],
            "tracks": {}, "alive": {}, "plant": None, "defuse": None, "util": list(util),
            "kills": [{"i": i, "u": positions[k["victim"]][0], "v": positions[k["victim"]][1], **k}
                      for i, k in enumerate(sorted(kills, key=lambda k: k["t"]))]}
    for s, (u, v) in positions.items():
        end = died.get(s, t_end)
        blob["tracks"][str(s)] = (tracks or {}).get(s) or stand(u, v, 0.0, end)
        blob["alive"][str(s)] = [[0.0, died[s], "kill"]] if s in died else [[0.0, None, "round_end"]]
    if player_state:
        blob["player_state"] = {"version": 1}      # presence is all the coach checks
    return blob


def ten(a=(2000, 2000), b=(8000, 8000)):
    """Five on A Site, five on B Site."""
    return {**{s: a for s in range(5)}, **{s: b for s in range(5, 10)}}
```

- [ ] **Step 2: Write the failing tests**

`tests/coach/test_roundview.py`:

```python
import pytest

from app.coach.roundview import RoundView
from tests.coach import toys


def view(blob):
    return RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


def test_sides_teammates_and_enemies():
    v = view(toys.round_blob(toys.ten()))
    assert v.side(0) == "A" and v.side(7) == "B"
    assert v.teammates(0) == [1, 2, 3, 4] and v.enemies(0) == [5, 6, 7, 8, 9]


def test_position_of_a_living_player_carries_its_area():
    v = view(toys.round_blob(toys.ten()))
    assert v.position(0, 10.0) == {"alive": True, "u": 2000, "v": 2000, "yaw": 0,
                                   "area": {"name": "A Site", "super": "A"}}


def test_position_follows_a_moving_track():
    pos = toys.ten()
    blob = toys.round_blob(pos, tracks={0: toys.walk(2000, 2000, 8000, 8000, 0.0, 60.0)})
    assert view(blob).position(0, 30.0)["area"]["name"] == "Mid Courtyard"


def test_a_dead_player_reads_as_dead_with_where_they_died():
    v = view(toys.round_blob(toys.ten(), kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    assert v.alive(0, 19.9) and not v.alive(0, 20.0)
    assert v.position(0, 30.0) == {"alive": False, "died_at": 20.0, "u": 2000, "v": 2000,
                                   "area": {"name": "A Site", "super": "A"}}
    assert v.living("A", 30.0) == [1, 2, 3, 4]


def test_distance_is_in_metres_and_needs_both_alive():
    pos = {**toys.ten(), 0: (2000, 2000), 1: (2700, 2000)}
    v = view(toys.round_blob(pos, kills=[{"t": 20.0, "killer": 5, "victim": 1}]))
    assert v.distance_m(0, 1, 10.0) == pytest.approx(10.0)
    assert v.distance_m(0, 1, 25.0) is None


def test_a_map_without_callouts_gives_no_area():
    v = RoundView(toys.round_blob(toys.ten()), toys.NAMES, toys.UV_PER_UNIT, "Nowhere", toys.CALLOUTS)
    assert v.position(0, 10.0)["area"] is None


def test_a_survivor_is_alive_to_the_end_of_the_round():
    blob = toys.round_blob(toys.ten())
    assert blob["alive"]["1"] == [[0.0, None, "round_end"]]       # the stored shape: no end time
    v = view(blob)
    assert v.alive(1, 59.9) and v.position(1, 60.0)["alive"] and len(v.living("A", 60.0)) == 5


def test_a_death_only_the_site_has_ends_the_life():
    v = RoundView(toys.round_blob(toys.ten()), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS,
                  db_deaths=[[2, 30.0]])
    assert v.alive(2, 29.9) and not v.alive(2, 30.0)
    assert v.position(2, 40.0)["died_at"] == 30.0 and v.living("A", 40.0) == [0, 1, 3, 4]


def test_sides_come_from_the_link_when_the_blob_has_none():
    blob = toys.round_blob(toys.ten())
    for p in blob["players"]:
        p["side"] = None
    groups = {s: "A" if s < 5 else "B" for s in range(10)}
    v = RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS, groups=groups)
    assert v.enemies(0) == [5, 6, 7, 8, 9]
    with pytest.raises(ValueError, match="no side"):
        RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


def test_utility_and_damage_rows_carry_names():
    util = [{"k": "flash", "t": 9.0, "by": 0, "u": 0, "v": 0, "hits": [[5, 9.5, 1.5]],
             "activation": {"state": "completed", "targets_complete": True}},
            {"k": "ability", "t": 3.0, "by": 1, "code": "Wraith"},
            {"k": "damage", "t": 13.0, "t1": 13.2, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 3},
            {"k": "shot", "t": 13.0, "by": 5}]
    v = view(toys.round_blob(toys.ten(), util=util))
    assert v.utility(by=0) == [{"t": 9.0, "kind": "flash", "by": "P0", "hits": [{"name": "P5", "seconds": 1.5}],
                                "hits_complete": True}]
    assert [row["kind"] for row in v.utility()] == ["flash", "ability"] and v.utility()[1]["ability"] == "Wraith"
    assert v.damage() == [{"t": 13.0, "kind": "damage", "by": "P5", "target": "P0", "source": "gun", "hits": 3}]
```

- [ ] **Step 3: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_roundview.py -q`
Expected: FAIL, `No module named 'app.coach.roundview'`.

- [ ] **Step 4: Implement**

`app/coach/roundview.py`:

```python
"""One stored round's primitives for the coach: who was where, who was alive, how far apart. Pure over the
decoded blob (app/replays/format.py); every later module reads rounds through this."""

from __future__ import annotations

import math
from bisect import bisect_right

from app.coach import areas
from app.replays import format as fmt


SAME_DEATH_S = 1.0      # app/control/engine.py's: a site death this close to the replay's own is the same death
_UTIL_KINDS = ("flash", "nearsight", "ability", "cast")


class RoundView:
    def __init__(self, blob: dict, names: dict[int, str], uv_per_unit: float, map_name: str,
                 callout_table: dict | None = None, groups: dict[int, str] | None = None, db_deaths=()):
        self.blob, self.names, self.uv_per_unit, self.map_name = blob, names, uv_per_unit, map_name
        self._table = callout_table
        self._side = {}
        for p in blob["players"]:
            slot = int(p["slot"])
            group = p.get("side") if p.get("side") is not None else (groups or {}).get(slot)
            if group is None:
                raise ValueError(f"round {blob.get('round')}: slot {slot} has no side group and the link names none")
            self._side[slot] = group
        self._db_deaths = [(int(s), float(t)) for s, t in db_deaths]
        self._life: dict[int, list] = {}
        self._samples: dict[int, list] = {}
        self.kills = sorted(blob.get("kills", []), key=lambda k: k["t"])

    number = property(lambda self: int(self.blob["round"]))
    t_end = property(lambda self: float(self.blob["t_end"]))
    t_decided = property(lambda self: float(self.blob["t_decided"]))
    slots = property(lambda self: sorted(self._side))

    def side(self, slot: int) -> str:
        return self._side[slot]

    def teammates(self, slot: int) -> list[int]:
        return [s for s in self.slots if s != slot and self._side[s] == self._side[slot]]

    def enemies(self, slot: int) -> list[int]:
        return [s for s in self.slots if self._side[s] != self._side[slot]]

    def _intervals(self, slot: int) -> list:
        """The slot's lives as [t0, t1]: a stored end of None (alive to the end of the round) reads as infinity,
        and a death only the site's data has ends the life it falls in, as the control engine does."""
        if slot not in self._life:
            lives = [[iv[0], math.inf if iv[1] is None else iv[1]]
                     for iv in self.blob.get("alive", {}).get(str(slot), [])]
            for dead, t in self._db_deaths:
                if dead != slot:
                    continue
                for life in lives:
                    if life[0] <= t < life[1] - SAME_DEATH_S:
                        life[1] = t
                        break
            self._life[slot] = lives
        return self._life[slot]

    def alive(self, slot: int, t: float) -> bool:
        return any(t0 <= t < t1 for t0, t1 in self._intervals(slot))

    def living(self, side: str, t: float) -> list[int]:
        return [s for s in self.slots if self._side[s] == side and self.alive(s, t)]

    def samples(self, slot: int) -> list:
        if slot not in self._samples:
            out = []
            for segment in self.blob.get("tracks", {}).get(str(slot), []):
                out.extend(fmt.decode_segment(segment, self.blob["hz"]))
            self._samples[slot] = sorted(out)
        return self._samples[slot]

    def _sample_at(self, slot: int, t: float):
        samples = self.samples(slot)
        i = bisect_right(samples, (t + 0.5 / self.blob["hz"], math.inf, math.inf, math.inf)) - 1
        return samples[i] if i >= 0 else None

    def area(self, u: float, v: float) -> dict | None:
        return areas.area_at(self.map_name, u, v, self._table)

    def position(self, slot: int, t: float) -> dict:
        if self.alive(slot, t):
            sample = self._sample_at(slot, t)
            if sample is not None:
                _, u, v, yaw = sample
                return {"alive": True, "u": u, "v": v, "yaw": yaw, "area": self.area(u, v)}
        ended = [t1 for _, t1 in self._intervals(slot) if t1 <= t]
        samples = self.samples(slot)
        if not ended or not samples:
            return {"alive": False, "died_at": None}
        died = max(ended)
        _, u, v, _ = self._sample_at(slot, died) or samples[-1]
        return {"alive": False, "died_at": died, "u": u, "v": v, "area": self.area(u, v)}

    def metres(self, uv_distance: float) -> float:
        return uv_distance / self.uv_per_unit / 100.0

    def distance_m(self, a: int, b: int, t: float) -> float | None:
        pa, pb = self.position(a, t), self.position(b, t)
        if not (pa["alive"] and pb["alive"]):
            return None
        return self.metres(math.hypot(pa["u"] - pb["u"], pa["v"] - pb["v"]))

    def utility(self, by: int | None = None) -> list[dict]:
        """Flashes, nearsights, abilities and movement casts, in stored order, with names for slots. A flash's
        `hits_complete` is true only when an empty or short hit list is proven to be the whole list."""
        out = []
        for e in self.blob.get("util", []):
            if e.get("k") not in _UTIL_KINDS or (by is not None and e.get("by") != by):
                continue
            row = {"t": e["t"], "kind": e["k"], "by": self.names.get(e.get("by"))}
            ability = e.get("name") or e.get("code") or e.get("ability")
            if ability:
                row["ability"] = ability
            if e.get("hits") is not None:
                proof = e.get("activation") or {}
                row["hits"] = [{"name": self.names.get(h[0]), "seconds": h[2] if len(h) > 2 else None}
                               for h in e["hits"]]
                row["hits_complete"] = bool(proof.get("state") == "completed" and proof.get("targets_complete"))
            out.append(row)
        return out

    def damage(self) -> list[dict]:
        return [{"t": e["t"], "kind": "damage", "by": self.names.get(e.get("by")),
                 "target": self.names.get(e.get("target")), "source": e.get("src"), "hits": e.get("n")}
                for e in self.blob.get("util", []) if e.get("k") == "damage"]
```

- [ ] **Step 5: Run to verify they pass**

Run: `$PY -m pytest tests/coach/test_roundview.py -q` — Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/coach/roundview.py webapp/tests/coach/toys.py webapp/tests/coach/test_roundview.py
git commit -F <scratch file: "Add the coach's per-round primitives">
```

---

### Task 3: What a side knew

**Files:**
- Create: `app/coach/knowledge.py`
- Test: `tests/coach/test_knowledge.py`

**Interfaces:**
- Consumes: the control header's `knew` (from `app/control/encode.py`): `{"A": {"<enemy slot>": [[t0, t1, u, v], ...]}, "B": {...}}`, runs in time order, `u`/`v` at the run's last sighting. `RoundView.position`, `.alive`, `.area`.
- Produces: `knowledge.known_enemies(knew: dict, side: str, t: float, view: RoundView) -> list[dict]`, one entry per enemy seen at or before `t`: `{"slot", "name", "first_seen", "last_seen", "in_view", "u", "v", "area", "alive"}` sorted by slot.

  - `knowledge.signals(events: dict | None, side: str, t: float, names: dict[int, str]) -> list[dict] | None`: `[{"t", "name", "reason"}]` for the side's stored locating events at or before `t`; `None` when the round stores none.

A run still open at `t` stores the position of its *end*, which is in the future. For an enemy in view at `t`, use their real position at `t` instead.

**`knew` holds sightings only** (checked 2026-10-12: `app/control/engine.py:1923-1931` appends a run only for an enemy in `seen_now`, which is sight, watchers and reveals). What the team *heard* (footsteps, gunfire) or learned from a kill or damage is stored separately, as the header's `knowledge_events`: `{"A": [[t, enemy slot, reason], ...], "B": [...]}` (`app/control/encode.py:95`), with a time and a reason and **no position**. The spec's "seen or heard" is therefore answered in two parts: `known_enemies` (placed sightings) and `signals` (everything else that located an enemy, unplaced). A row stored before the events existed has no list, and `signals` returns `None` for it.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_knowledge.py`:

```python
from app.coach import knowledge
from app.coach.roundview import RoundView
from tests.coach import toys


def view(blob):
    return RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)


KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000], [20.0, 40.0, 5000, 5000]], "6": [[30.0, 31.0, 8000, 8000]]},
        "B": {}}


def test_nothing_is_known_before_the_first_sighting():
    assert knowledge.known_enemies(KNEW, "A", 4.0, view(toys.round_blob(toys.ten()))) == []


def test_a_past_sighting_keeps_its_last_place_and_time():
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, view(toys.round_blob(toys.ten())))
    assert (e["slot"], e["name"], e["first_seen"], e["last_seen"], e["in_view"]) == (5, "P5", 5.0, 8.0, False)
    assert e["area"]["name"] == "B Site" and e["alive"] is True


def test_an_enemy_in_view_is_placed_where_they_are_now():
    v = view(toys.round_blob(toys.ten()))          # slot 5 really stands on B Site the whole round
    (e,) = knowledge.known_enemies(KNEW, "A", 25.0, v)
    assert e["in_view"] is True and e["last_seen"] == 25.0 and e["area"]["name"] == "B Site"


def test_a_dead_enemy_is_marked_dead():
    v = view(toys.round_blob(toys.ten(), kills=[{"t": 10.0, "killer": 0, "victim": 5}]))
    (e,) = knowledge.known_enemies(KNEW, "A", 15.0, v)
    assert e["alive"] is False


def test_the_other_side_knows_only_its_own_sightings():
    assert knowledge.known_enemies(KNEW, "B", 50.0, view(toys.round_blob(toys.ten()))) == []


EVENTS = {"A": [[3.0, 6, "footsteps"], [12.5, 7, "gunfire"], [40.0, 6, "seen"]], "B": []}


def test_signals_are_the_sides_locating_events_so_far():
    assert knowledge.signals(EVENTS, "A", 20.0, toys.NAMES) == [{"t": 3.0, "name": "P6", "reason": "footsteps"},
                                                                {"t": 12.5, "name": "P7", "reason": "gunfire"}]
    assert knowledge.signals(EVENTS, "B", 20.0, toys.NAMES) == []


def test_a_round_that_stores_no_events_has_no_signals_answer():
    assert knowledge.signals(None, "A", 20.0, toys.NAMES) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_knowledge.py -q` — Expected: FAIL, `No module named 'app.coach.knowledge'`.

- [ ] **Step 3: Implement**

`app/coach/knowledge.py`:

```python
"""What a side knew of each enemy by a time, from map control's stored rows: `header["knew"]` (sightings, each
with a place) and `header["knowledge_events"]` (every locating event, sound included, with a reason and no place).
The spec's "knowable": team knowledge, perfect comms."""

from __future__ import annotations


def signals(events: dict | None, side: str, t: float, names: dict[int, str]) -> list[dict] | None:
    if events is None:
        return None
    return [{"t": row[0], "name": names.get(int(row[1])), "reason": row[2]}
            for row in events.get(side) or [] if row[0] <= t]


def known_enemies(knew: dict, side: str, t: float, view) -> list[dict]:
    out = []
    for slot_text, runs in (knew.get(side) or {}).items():
        seen = [run for run in runs if run[0] <= t]
        if not seen:
            continue
        slot = int(slot_text)
        t0, t1, u, v = seen[-1]
        in_view = t <= t1
        if in_view:
            now = view.position(slot, t)
            if now.get("alive"):
                u, v = now["u"], now["v"]
        out.append({"slot": slot, "name": view.names.get(slot), "first_seen": seen[0][0],
                    "last_seen": t if in_view else t1, "in_view": in_view, "u": u, "v": v,
                    "area": view.area(u, v), "alive": view.alive(slot, t)})
    return sorted(out, key=lambda e: e["slot"])
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach/test_knowledge.py -q` — Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/knowledge.py webapp/tests/coach/test_knowledge.py
git commit -F <scratch file: "Add the coach's team-knowledge read">
```

---

### Task 4: Per-area control

**Files:**
- Create: `app/coach/control_areas.py`
- Test: `tests/coach/test_control_areas.py`

**Interfaces:**
- Consumes: `app.replays.control_format` as `cf`: `cf.unpack_data(data) -> (header, streams)`, `cf.walk_bitmap(header) -> list[int]`, `cf.decode_states(stream, ticks, cells) -> list[list[int]]`, `cf.pack_data(header, streams)`, `cf.GRID` (128), `cf.STATE_NAMES`. A flat cell `c` is column `c % GRID`, row `c // GRID`. Tick `i` is at `header["ticks"][i] / header["hz"]` seconds.
- Produces:
  - `control_areas.area_shares(data: bytes, map_name: str, table: dict | None = None) -> dict` : `{"t": [seconds per tick], "cells": {area: n}, "areas": {area: [[a, b, contested], ...per tick]}, "knew": header knew or None, "signals": header knowledge_events or None, "group_side": header value or None}`
  - `control_areas.control_over(shares: dict, area: str, t0: float, t1: float) -> dict | None` : `{"a", "b", "contested", "none"}` as time-weighted fractions of the area's cells, rounded to 2 places; None when the area is unknown or the span has no ticks. A single moment (`t0 == t1`) is answered from the tick in force then.

State codes: 0 none; 1-3 side A; 4-6 side B; 7-8 contested.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_control_areas.py`:

```python
import base64

from app.coach import control_areas
from app.replays import control_format as cf

# Two walkable cells: cell 0 (top-left, nearest "A Site") and the last cell (bottom-right, nearest "B Site").
TABLE = {"Toy": [{"name": "A Site", "super": "A", "u": 0, "v": 0},
                 {"name": "B Site", "super": "B", "u": 10000, "v": 10000}]}
CELLS = [0, cf.GRID * cf.GRID - 1]


def control_bytes(frames, ticks, knew=None):
    """`frames`: per tick, the two cells' state codes. Every tick stored as a checkpoint (kind 0, 4 bits a cell)."""
    bits = bytearray(cf.GRID * cf.GRID // 8)
    for c in CELLS:
        bits[c >> 3] |= 1 << (7 - (c & 7))
    stream = bytearray()
    for a, b in frames:
        stream += bytes([0, (a << 4) | b])
    header = {"hz": 16, "ticks": ticks, "walk": base64.b64encode(bytes(bits)).decode("ascii"), "cells": 2,
              "group_side": {"A": "attack", "B": "defense"}}       # the link's words (replay_control.round_link)
    if knew is not None:
        header["knew"] = knew
        header["knowledge_events"] = {"A": [[1.0, 5, "seen"]], "B": []}
    return cf.pack_data(header, {"states": bytes(stream), "coverage": b"", "control": b""})


def test_each_area_counts_its_cells_by_owner_per_tick():
    shares = control_areas.area_shares(control_bytes([(1, 4), (7, 0)], [0, 160]), "Toy", TABLE)
    assert shares["t"] == [0.0, 10.0]
    assert shares["cells"] == {"A Site": 1, "B Site": 1}
    assert shares["areas"]["A Site"] == [[1, 0, 0], [0, 0, 1]]
    assert shares["areas"]["B Site"] == [[0, 1, 0], [0, 0, 0]]
    assert shares["group_side"] == {"A": "attack", "B": "defense"} and shares["knew"] is None
    assert shares["signals"] is None


def test_control_over_a_span_is_time_weighted():
    shares = control_areas.area_shares(control_bytes([(1, 4), (4, 4), (4, 4)], [0, 160, 320]), "Toy", TABLE)
    # A Site: side A for 0-10 s, side B for 10-20 s
    assert control_areas.control_over(shares, "A Site", 0.0, 20.0) == {"a": 0.5, "b": 0.5, "contested": 0.0, "none": 0.0}
    assert control_areas.control_over(shares, "A Site", 0.0, 5.0)["a"] == 1.0
    # one moment is the tick in force then: side A at 4 s, side B from the tick at 10 s
    assert control_areas.control_over(shares, "A Site", 4.0, 4.0)["a"] == 1.0
    assert control_areas.control_over(shares, "A Site", 10.0, 10.0)["b"] == 1.0


def test_an_unknown_area_or_empty_span_has_no_answer():
    shares = control_areas.area_shares(control_bytes([(1, 4)], [80]), "Toy", TABLE)   # the only tick is at 5 s
    assert control_areas.control_over(shares, "C Site", 0.0, 10.0) is None
    assert control_areas.control_over(shares, "A Site", 0.0, 2.0) is None
    assert control_areas.control_over(shares, "A Site", 2.0, 2.0) is None      # a moment before the first tick
    # the last tick holds to the end of the span asked about
    assert control_areas.control_over(shares, "A Site", 50.0, 60.0)["a"] == 1.0


def test_stored_sightings_are_carried_through():
    knew = {"A": {"5": [[1.0, 2.0, 100, 100]]}, "B": {}}
    shares = control_areas.area_shares(control_bytes([(0, 0)], [0], knew), "Toy", TABLE)
    assert shares["knew"] == knew and shares["signals"] == {"A": [[1.0, 5, "seen"]], "B": []}
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_control_areas.py -q` — Expected: FAIL, `No module named 'app.coach.control_areas'`.

- [ ] **Step 3: Implement**

`app/coach/control_areas.py`:

```python
"""A round's stored map control (app/replays/control_format.py) reduced to what the coach asks about: who held
each named area at each tick, and the sightings each side had. Decoded once per round when a snapshot is built."""

from __future__ import annotations

from bisect import bisect_right

from app.coach import areas
from app.replays import control_format as cf
from app.replays import format as fmt

# index into [a, b, contested] per state code; None is unowned
_OWNER = (None, 0, 0, 0, 1, 1, 1, 2, 2)


def area_shares(data: bytes, map_name: str, table: dict | None = None) -> dict:
    header, streams = cf.unpack_data(data)
    walk = cf.walk_bitmap(header)
    labels = []
    for cell in walk:
        u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
        area = areas.area_at(map_name, u, v, table)
        labels.append(area["name"] if area else None)
    names = sorted({name for name in labels if name})
    frames = cf.decode_states(streams["states"], len(header["ticks"]), len(walk))
    per_area = {name: [] for name in names}
    for frame in frames:
        counts = {name: [0, 0, 0] for name in names}
        for label, code in zip(labels, frame):
            owner = _OWNER[code]
            if label is not None and owner is not None:
                counts[label][owner] += 1
        for name in names:
            per_area[name].append(counts[name])
    return {"t": [tick / header["hz"] for tick in header["ticks"]],
            "cells": {name: labels.count(name) for name in names}, "areas": per_area,
            "knew": header.get("knew"), "signals": header.get("knowledge_events"),
            "group_side": header.get("group_side")}


def control_over(shares: dict, area: str, t0: float, t1: float) -> dict | None:
    rows, times = shares["areas"].get(area), shares["t"]
    if rows is None or not shares["cells"].get(area):
        return None
    totals, covered = [0.0, 0.0, 0.0], 0.0
    if t1 <= t0:                                    # one moment: the tick in force then
        i = bisect_right(times, t0) - 1
        if i < 0:
            return None
        totals, covered = [float(x) for x in rows[i]], 1.0
        times = []
    for i, start in enumerate(times):
        end = times[i + 1] if i + 1 < len(times) else max(t1, start)
        span = min(end, t1) - max(start, t0)
        if span <= 0 and not (i + 1 == len(times) and t0 <= start <= t1):
            continue
        span = max(span, 1e-9)
        covered += span
        for k in range(3):
            totals[k] += rows[i][k] * span
    if covered == 0:
        return None
    cells = shares["cells"][area]
    a, b, contested = (round(v / covered / cells, 2) for v in totals)
    return {"a": a, "b": b, "contested": contested, "none": round(max(0.0, 1 - a - b - contested), 2)}
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach/test_control_areas.py -q` — Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/control_areas.py webapp/tests/coach/test_control_areas.py
git commit -F <scratch file: "Add per-area control for the coach">
```

---

### Task 5: Match snapshot

**Files:**
- Create: `app/coach/snapshot.py`, `scripts/coach_snapshot.py`
- Test: `tests/coach/test_snapshot.py`

**Interfaces:**
- Consumes: `app.services.replays.find_replay(db, uuid) -> Replay | None`, `.page_context(db, replay) -> {"match", "players", "rounds"}`, `.round_blob(db, replay, n) -> bytes | None`; `app.services.replay_control.round_control(db, replay, n) -> RoundControlAnswer(status, row, stale)` where status is `ok | not_ready | failed | no_map | old_blob` and `row.data` holds the bytes when ok; `fmt.decode_blob`; `control_areas.area_shares`; `RoundView`.
- Produces:
  - `snapshot.assemble(context: dict, blobs: dict[int, dict], controls: dict[int, tuple[str, bool, dict | None]], links: dict[int, dict] | None = None) -> dict` (pure). `controls[n]` is `(status, stale, shares or None)`. `links[n]` is `replay_control.round_link(replay, groups, n)`: `{"linked", "sides": {"<slot>": "attack" | "defense"}, "db_deaths": [[slot, t on the replay clock], ...]}` (`app/services/replay_control.py:300-315`).
  - `snapshot.read(db, match_uuid: str) -> dict` (the DB read; raises `SnapshotError` for no replay, an unlinked one, or a player with no side group anywhere).
  - `snapshot.save(data: dict, directory: Path) -> None`, `snapshot.load(directory: Path) -> Snapshot`.
  - `Snapshot`: `.context`, `.map`, `.uuid`, `.round_numbers -> list[int]`, `.names -> dict[int, str]`, `.view(n) -> RoundView`, `.control(n) -> dict | None`, `.gaps(n) -> list[str]`, `.site(n) -> dict` (the page context's round entry), `.role(n, slot) -> "attack" | "defense" | None`, `.slot_of(token: str) -> int` (raises `SnapshotError` listing valid names).
  - Gap names: `control_not_ready`, `control_failed`, `control_no_map`, `control_old_blob`, `control_stale`, `team_knowledge`, `player_state`, `db_only_deaths` (the round has a death with no kill row in the replay: its life ends on time, and no killer, trade or distance is known for it). (A map with no callouts is a match-level gap the brief reports, Task 7.)

Sides (checked 2026-10-12): `context["players"][slot]["side"]` is the side **group** (`"A"`/`"B"`), from `rp.side_group or team_side.get(team)` (`app/services/replays.py:119`), so it is filled when the blob's own `side` is not. Which group attacks changes at the half; `round_link(...)["sides"]` gives each slot's role for one round.

The saved layout is `<dir>/snapshot.json` (one file). It contains player names and match data: it lives under the temp directory and is never committed.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_snapshot.py`:

```python
import pytest

from app.coach import snapshot
from tests.coach import toys

CONTEXT = {"match": {"uuid": "u-1", "map": "Toy", "rounds": [1, 2], "linked": True, "uv_per_unit": toys.UV_PER_UNIT,
                     "score": [1, 1], "external_id": "u-1"},
           "players": {str(s): {"slot": s, "name": f"P{s}#T{s}", "agent": "Jett", "team": "team1" if s < 5 else "team2",
                                "side": "A" if s < 5 else "B", "player_id": 100 + s} for s in range(10)},
           "rounds": {"1": {"db": {"winner": "team1"}, "stats": {}, "kills": {}},
                      "2": {"db": {"winner": "team2"}, "stats": {}, "kills": {}}}}
SHARES = {"t": [0.0], "cells": {"A Site": 1}, "areas": {"A Site": [[1, 0, 0]]},
          "knew": {"A": {}, "B": {}}, "group_side": None}


def built(controls=None, blobs=None):
    blobs = blobs or {1: toys.round_blob(toys.ten(), n=1), 2: toys.round_blob(toys.ten(), n=2)}
    controls = controls or {1: ("ok", False, SHARES), 2: ("ok", False, SHARES)}
    return snapshot.assemble(CONTEXT, blobs, controls)


def test_a_complete_match_has_no_gaps(tmp_path):
    snapshot.save(built(), tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.round_numbers == [1, 2] and snap.map == "Toy"
    assert snap.gaps(1) == [] and snap.control(1)["cells"] == {"A Site": 1}
    assert snap.view(2).number == 2 and snap.view(2).position(0, 1.0)["alive"]


def test_each_missing_source_is_named(tmp_path):
    no_knew = {**SHARES, "knew": None}
    blob = toys.round_blob(toys.ten(), n=2)
    blob.pop("player_state", None)
    data = built(controls={1: ("not_ready", False, None), 2: ("ok", True, no_knew)},
                 blobs={1: toys.round_blob(toys.ten(), n=1), 2: blob})
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.gaps(1) == ["control_not_ready", "team_knowledge"]
    assert snap.gaps(2) == ["control_stale", "team_knowledge", "player_state"]
    assert snap.control(1) is None


def test_the_link_gives_roles_site_only_deaths_and_sides_the_blob_lacks(tmp_path):
    blob = toys.round_blob(toys.ten(), n=1)
    for p in blob["players"]:
        p["side"] = None                                   # the condenser could not resolve sides
    links = {1: {"linked": True, "sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)},
                 "db_deaths": [[3, 25.0]]}}
    data = snapshot.assemble(CONTEXT, {1: blob, 2: toys.round_blob(toys.ten(), n=2)},
                             {1: ("ok", False, SHARES), 2: ("ok", False, SHARES)}, links)
    snapshot.save(data, tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.role(1, 0) == "attack" and snap.role(1, 7) == "defense" and snap.role(2, 0) is None
    assert snap.view(1).enemies(0) == [5, 6, 7, 8, 9]      # groups came from the page context
    assert not snap.view(1).alive(3, 30.0) and snap.gaps(1) == ["db_only_deaths"] and snap.gaps(2) == []


def test_a_player_is_found_by_slot_name_or_name_without_tag(tmp_path):
    snapshot.save(built(), tmp_path)
    snap = snapshot.load(tmp_path)
    assert snap.slot_of("3") == 3 and snap.slot_of("p3#t3") == 3 and snap.slot_of("P3") == 3


def test_an_unknown_player_fails_with_the_valid_names(tmp_path):
    snapshot.save(built(), tmp_path)
    with pytest.raises(snapshot.SnapshotError, match="P0#T0"):
        snapshot.load(tmp_path).slot_of("nobody")


def test_read_refuses_an_unlinked_replay(monkeypatch):
    class Replay:
        map_name = "Toy"

    monkeypatch.setattr(snapshot.replays, "find_replay", lambda db, uuid: Replay())
    monkeypatch.setattr(snapshot.replays, "page_context",
                        lambda db, r: {"match": {"linked": False, "reason": "not linked"}, "players": {}, "rounds": {}})
    with pytest.raises(snapshot.SnapshotError, match="not linked"):
        snapshot.read(None, "u-1")
    monkeypatch.setattr(snapshot.replays, "find_replay", lambda db, uuid: None)
    with pytest.raises(snapshot.SnapshotError, match="no replay"):
        snapshot.read(None, "u-1")
```

`snapshot.assemble` receives decoded blobs and only checks that the `player_state` key is present, so the toy's stand-in value is never validated.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_snapshot.py -q` — Expected: FAIL, `No module named 'app.coach.snapshot'`.

- [ ] **Step 3: Implement**

`app/coach/snapshot.py`:

```python
"""Everything one coach run needs about a match, read from the database once and kept as one JSON file on
disk. The brief, the detectors and the query commands read the file; the model never reaches the database."""

from __future__ import annotations

import json
from pathlib import Path

from app.coach import control_areas
from app.coach.roundview import RoundView
from app.replays import format as fmt
from app.services import replay_control, replays

VERSION = 1
FILE = "snapshot.json"


class SnapshotError(Exception):
    pass


def assemble(context: dict, blobs: dict[int, dict], controls: dict[int, tuple], links: dict | None = None) -> dict:
    rounds = {}
    for n, blob in sorted(blobs.items()):
        status, stale, shares = controls.get(n, ("not_ready", False, None))
        link = (links or {}).get(n) or {}
        link = {"sides": dict(link.get("sides") or {}), "db_deaths": [list(d) for d in link.get("db_deaths") or []]}
        gaps = []
        if status != "ok":
            gaps.append(f"control_{status}")
        elif stale:
            gaps.append("control_stale")
        if not (shares and shares.get("knew")):
            gaps.append("team_knowledge")
        if "player_state" not in blob:
            gaps.append("player_state")
        if link["db_deaths"]:
            gaps.append("db_only_deaths")
        rounds[str(n)] = {"blob": blob, "control": shares, "link": link, "gaps": gaps}
    return {"v": VERSION, "context": context, "rounds": rounds}


def _groups(context: dict) -> dict[int, str]:
    return {int(slot): p["side"] for slot, p in context["players"].items() if p.get("side") is not None}


def read(db, match_uuid: str) -> dict:
    replay = replays.find_replay(db, match_uuid)
    if replay is None:
        raise SnapshotError(f"no replay stored for {match_uuid}")
    context = replays.page_context(db, replay)
    if not context["match"].get("linked"):
        raise SnapshotError(f"replay {match_uuid} is {context['match'].get('reason') or 'not linked'}: nothing to coach")
    groups = _groups(context)
    blobs, controls, links = {}, {}, {}
    for n in context["match"]["rounds"]:
        blobs[n] = fmt.decode_blob(replays.round_blob(db, replay, n))
        for p in blobs[n]["players"]:
            if p.get("side") is None and int(p["slot"]) not in groups:
                raise SnapshotError(f"round {n}: slot {p['slot']} has no side in the replay or the link: "
                                    "nothing can be said about teammates or enemies")
        answer = replay_control.round_control(db, replay, n)
        shares = control_areas.area_shares(answer.row.data, replay.map_name) if answer.status == "ok" else None
        controls[n] = (answer.status, bool(answer.stale), shares)
        links[n] = replay_control.round_link(replay, groups, n)
    return assemble(context, blobs, controls, links)


def save(data: dict, directory: Path) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / FILE).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


class Snapshot:
    def __init__(self, data: dict):
        if data.get("v") != VERSION:
            raise SnapshotError(f"snapshot version {data.get('v')!r} is not {VERSION}")
        self.data, self.context = data, data["context"]
        self.map, self.uuid = self.context["match"]["map"], self.context["match"]["uuid"]
        self.names = {int(slot): p["name"] for slot, p in self.context["players"].items()}
        self._views: dict[int, RoundView] = {}

    round_numbers = property(lambda self: sorted(int(n) for n in self.data["rounds"]))

    def _round(self, n: int) -> dict:
        try:
            return self.data["rounds"][str(n)]
        except KeyError:
            raise SnapshotError(f"round {n} is not in this match (rounds {self.round_numbers[0]}"
                                f"-{self.round_numbers[-1]})") from None

    def view(self, n: int) -> RoundView:
        if n not in self._views:
            entry = self._round(n)
            self._views[n] = RoundView(entry["blob"], self.names, self.context["match"]["uv_per_unit"], self.map,
                                       groups=_groups(self.context),
                                       db_deaths=(entry.get("link") or {}).get("db_deaths") or ())
        return self._views[n]

    def role(self, n: int, slot: int) -> str | None:
        """`attack` or `defense` for this slot in round n, from the link; None when the link names none."""
        return ((self._round(n).get("link") or {}).get("sides") or {}).get(str(slot))

    def control(self, n: int) -> dict | None:
        return self._round(n)["control"]

    def gaps(self, n: int) -> list[str]:
        return list(self._round(n)["gaps"])

    def site(self, n: int) -> dict:
        return self.context["rounds"].get(str(n), {})

    def slot_of(self, token: str) -> int:
        token = str(token).strip()
        if token.isdigit() and int(token) in self.names:
            return int(token)
        wanted = token.lower()
        exact = [s for s, name in self.names.items() if name.lower() == wanted]
        short = [s for s, name in self.names.items() if name.lower().split("#")[0] == wanted]
        found = exact or short
        if len(found) != 1:
            raise SnapshotError(f"{token!r} is {'ambiguous' if found else 'not in this match'}; players: "
                                + ", ".join(self.names[s] for s in sorted(self.names)))
        return found[0]


def load(directory: Path) -> Snapshot:
    return Snapshot(json.loads((Path(directory) / FILE).read_text(encoding="utf-8")))
```

`scripts/coach_snapshot.py`:

```python
"""Reads one match's replay data from the friends database into a local snapshot for the coach.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_snapshot.py <match uuid> --out <dir>

Read-only. The snapshot holds player names: keep <dir> outside the repository. Exits 2 when refused."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if WEBAPP_ROOT.parent in args.out.resolve().parents:
        print("refused: --out is inside the repository", file=sys.stderr)
        return 2
    from app.coach import snapshot
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            data = snapshot.read(db, args.match_uuid.lower())
        except snapshot.SnapshotError as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
    snapshot.save(data, args.out)
    gaps = {n: r["gaps"] for n, r in data["rounds"].items() if r["gaps"]}
    print(f"snapshot of {len(data['rounds'])} rounds written to {args.out}; rounds with gaps: {gaps or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.

- [ ] **Step 5: Take one real snapshot (owner's machine, read-only)**

Ask the owner for the UUID of one of their own matches that has a replay, then run:

`$PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_snapshot.py <uuid> --out $env:TEMP\valo-coach\<uuid>`

Expected: "snapshot of N rounds written", with the gaps listed. Note the time it took and the file's size in the commit message body. If it takes over two minutes, the cost is `cf.decode_states`; report it and stop before optimising.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/coach/snapshot.py webapp/scripts/coach_snapshot.py webapp/tests/coach
git commit -F <scratch file: "Add the coach's match snapshot">
```

---

### Task 6: Detectors

**Files:**
- Create: `app/coach/detectors.py`
- Test: `tests/coach/test_detectors.py`

**Interfaces:**
- Consumes: `RoundView`, `knowledge.known_enemies`, `areas.SITES`, `app.scoring.impact.TRADE_WINDOW_SECONDS` (6.0; imported, never changed).
- Produces: `detectors.flags(view: RoundView, slot: int, knew: dict | None, role: str | None = None) -> list[dict]`, each `{"kind", "round", "t", "values": {...}}`, sorted by `t`. Kinds: `death_untraded_alone`, `death_traded`, `death_with_advantage`, `first_kill`, `first_death`, `flash_hit_no_enemy`, `flash_led_to_kill`, `flash_hit_teammate`, `stack_seen_elsewhere`. Thresholds are module constants. `role` is the player's `attack`/`defense` for the round (`Snapshot.role`).

Blob facts: a flash is a `util` row `{"k": "flash", "t", "by", "hits": [[target slot, hit t, duration | null], ...], "activation": {"state", "targets_complete"}}`. An empty `hits` means "hit nobody" only when `activation.state == "completed"` and `activation.targets_complete` is true.

Four rules that come from how the data is stored, each with its own test:

- **Only kills up to `t_decided` count.** Later kills stay in the blob (`format.py`), and a death after the round is decided is no mistake.
- **A flash hit counts only with a duration that is unknown (`null`) or above zero.** The blob keeps zero-second hits.
- **The last player alive is not "alone".** With no living teammate there was nobody to trade, so `death_untraded_alone` needs at least one living teammate with a known position.
- **A stack flag carries the player's role.** Four defenders seen on A while an attacker stays B means B is open, the opposite of the defender's case. The detector does not decide which it is: it reports `role`, and the model reads it.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_detectors.py`:

```python
from app.coach import detectors
from app.coach.roundview import RoundView
from tests.coach import toys


def run(blob, slot=0, knew=None, role=None):
    view = RoundView(blob, toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    return detectors.flags(view, slot, knew, role)


def kinds(found):
    return [f["kind"] for f in found]


def test_a_death_far_from_everyone_and_never_avenged_is_flagged():
    pos = {**toys.ten(), 0: (5000, 5000)}                       # slot 0 alone in mid
    found = run(toys.round_blob(pos, kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    (flag,) = [f for f in found if f["kind"] == "death_untraded_alone"]
    assert flag["t"] == 20.0 and flag["values"]["killer"] == "P5"
    assert flag["values"]["nearest_teammate_m"] > detectors.TRADE_RANGE_M


def test_a_death_beside_a_teammate_is_not_flagged_as_alone():
    found = run(toys.round_blob(toys.ten(), kills=[{"t": 20.0, "killer": 5, "victim": 0}]))
    assert "death_untraded_alone" not in kinds(found)


def test_a_death_avenged_inside_the_window_is_a_trade():
    kills = [{"t": 20.0, "killer": 5, "victim": 0}, {"t": 23.0, "killer": 1, "victim": 5}]
    found = run(toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=kills))
    assert "death_traded" in kinds(found) and "death_untraded_alone" not in kinds(found)


def test_a_kill_on_the_killer_after_the_window_is_not_a_trade():
    kills = [{"t": 20.0, "killer": 5, "victim": 0}, {"t": 27.0, "killer": 1, "victim": 5}]
    assert "death_traded" not in kinds(run(toys.round_blob(toys.ten(), kills=kills)))


def test_the_last_player_alive_is_not_flagged_as_alone():
    kills = [{"t": 5.0 + s, "killer": 5, "victim": s} for s in (1, 2, 3, 4)] + [{"t": 20.0, "killer": 5, "victim": 0}]
    assert "death_untraded_alone" not in kinds(run(toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=kills)))


def test_a_death_after_the_round_was_decided_is_not_flagged():
    blob = toys.round_blob({**toys.ten(), 0: (5000, 5000)}, kills=[{"t": 50.0, "killer": 5, "victim": 0}],
                           t_decided=40.0)
    assert kinds(run(blob)) == []                          # no death flag and no first-contact flag


def test_dying_while_ahead_is_flagged_with_the_count():
    kills = [{"t": 10.0, "killer": 1, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 0}]
    (flag,) = [f for f in run(toys.round_blob(toys.ten(), kills=kills)) if f["kind"] == "death_with_advantage"]
    assert flag["values"]["alive_before"] == {"own": 5, "enemy": 4}


def test_first_contact_names_the_players_part():
    kills = [{"t": 10.0, "killer": 0, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 1}]
    assert "first_kill" in kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=0))
    assert "first_death" in kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=5))
    assert not {"first_kill", "first_death"} & set(kinds(run(toys.round_blob(toys.ten(), kills=kills), slot=1)))


def flash(by, hits, t=10.0, complete=True):
    return {"k": "flash", "t": t, "by": by, "u": 0, "v": 0, "hits": hits,
            "activation": {"state": "completed" if complete else "unknown", "targets_complete": complete}}


def test_a_flash_that_provably_hit_no_one_is_flagged():
    assert "flash_hit_no_enemy" in kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [])])))


def test_an_unproven_empty_flash_is_not_flagged():
    assert kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [], complete=False)]))) == []


def test_a_flash_followed_by_a_kill_on_its_target_is_credited():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, 1.5]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    (flag,) = [f for f in run(blob) if f["kind"] == "flash_led_to_kill"]
    assert flag["values"] == {"hit": ["P5"], "killed": ["P5"], "killers": ["P1"]}


def test_a_flash_on_a_teammate_is_flagged():
    assert "flash_hit_teammate" in kinds(run(toys.round_blob(toys.ten(), util=[flash(0, [[1, 10.5, 1.0]])])))


def test_a_zero_second_hit_is_not_a_hit():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, 0], [1, 10.5, 0.0]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    assert kinds(run(blob)) == ["flash_hit_no_enemy"]      # not credited with the kill, not blamed for the teammate


def test_a_hit_of_unknown_length_still_counts():
    blob = toys.round_blob(toys.ten(), util=[flash(0, [[5, 10.5, None]])],
                           kills=[{"t": 12.0, "killer": 1, "victim": 5}])
    assert "flash_led_to_kill" in kinds(run(blob))


STACK = {"A": {str(s): [[10.0, 40.0, 2000, 2000]] for s in (5, 6, 7, 8)}, "B": {}}


def stack_blob(mover=None):
    pos = {**{s: (8000, 8000) for s in range(5)}, **{s: (2000, 2000) for s in range(5, 10)}}
    tracks = {0: mover} if mover else None
    return toys.round_blob(pos, tracks=tracks)


def test_four_enemies_seen_on_the_other_site_while_the_player_stays_is_flagged():
    (flag,) = [f for f in run(stack_blob(), knew=STACK, role="defense") if f["kind"] == "stack_seen_elsewhere"]
    assert flag["t"] == 10.0
    assert flag["values"]["site"] == "A" and flag["values"]["enemies_seen"] == 4
    assert flag["values"]["player_area_then"] == "B Site" and flag["values"]["player_area_after"] == "B Site"
    assert flag["values"]["role"] == "defense"


def test_a_stack_flag_without_a_known_role_says_so():
    (flag,) = [f for f in run(stack_blob(), knew=STACK) if f["kind"] == "stack_seen_elsewhere"]
    assert flag["values"]["role"] is None


def test_a_player_who_leaves_within_the_hold_is_not_flagged():
    # On B Site until 12 s, so the stack (known from 10 s) is seen from B; in Mid by 17 s, inside the 8 s hold.
    mover = (toys.stand(8000, 8000, 0.0, 12.0) + toys.walk(8000, 8000, 5000, 5000, 12.25, 17.0)
             + toys.stand(5000, 5000, 17.25, 60.0))
    view = RoundView(stack_blob(mover), toys.NAMES, toys.UV_PER_UNIT, "Toy", toys.CALLOUTS)
    assert view.position(0, 10.0)["area"]["name"] == "B Site"        # the fixture's own claim, checked
    assert view.position(0, 18.0)["area"]["name"] == "Mid Courtyard"
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(mover), knew=STACK))


def test_a_player_who_leaves_after_the_hold_is_flagged():
    mover = (toys.stand(8000, 8000, 0.0, 30.0) + toys.walk(8000, 8000, 5000, 5000, 30.25, 35.0)
             + toys.stand(5000, 5000, 35.25, 60.0))
    assert "stack_seen_elsewhere" in kinds(run(stack_blob(mover), knew=STACK))


def test_three_enemies_are_not_a_stack():
    knew = {"A": {str(s): [[10.0, 40.0, 2000, 2000]] for s in (5, 6, 7)}, "B": {}}
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(), knew=knew))


def test_without_team_knowledge_the_stack_check_is_skipped():
    assert "stack_seen_elsewhere" not in kinds(run(stack_blob(), knew=None))
```

Each fixture really contains its claimed relationship: check by reading it against `toys.CALLOUTS` (A Site at 2000,2000; B Site at 8000,8000; Mid at 5000,5000) before implementing. Mid to either site is about 4243 uv, about 61 m, so "alone" holds.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_detectors.py -q` — Expected: FAIL, `No module named 'app.coach.detectors'`.

- [ ] **Step 3: Implement**

`app/coach/detectors.py`:

```python
"""Moments worth the coach's attention, found by fixed rules over one round. A flag is a pointer with measured
values, never a verdict: the model decides what it means and checks it with the query commands.

Thresholds are first values, to be reviewed against real rounds (plan 1, Task 9) and changed only here."""

from __future__ import annotations

from app.coach import areas, knowledge
from app.scoring.impact import TRADE_WINDOW_SECONDS

TRADE_WINDOW_S = float(TRADE_WINDOW_SECONDS)   # the scoring formula's own trade window
TRADE_RANGE_M = 20.0        # a teammate farther than this at the death could not have traded it
STACK_ENEMIES = 4           # this many enemies known on one site is a stack
STACK_FRESH_S = 10.0        # a sighting older than this no longer places an enemy
STACK_HOLD_S = 8.0          # still on another site this long after the stack was known
FLASH_FOLLOWUP_S = 3.0      # a kill on a flashed enemy inside this is credited to the flash
_EPS = 0.01


def _flag(view, kind: str, t: float, **values) -> dict:
    return {"kind": kind, "round": view.number, "t": round(float(t), 2), "values": values}


def _live_kills(view) -> list[dict]:
    """Kills while the round was still open: later ones stay in the blob and decide nothing."""
    return [k for k in view.kills if k["t"] <= view.t_decided]


def _deaths(view, slot):
    out = []
    for k in _live_kills(view):
        if k["victim"] != slot:
            continue
        t, killer, before = k["t"], k.get("killer"), k["t"] - _EPS
        mates = [m for m in view.teammates(slot) if view.alive(m, before)]
        gaps = [d for d in (view.distance_m(slot, m, before) for m in mates) if d is not None]
        nearest = round(min(gaps), 1) if gaps else None
        traded_by = None
        if killer is not None and killer != slot:
            for k2 in view.kills:
                if k2["victim"] == killer and k2.get("killer") in view.teammates(slot) \
                        and 0 <= k2["t"] - t <= TRADE_WINDOW_S:
                    traded_by = (k2["killer"], round(k2["t"] - t, 2))
                    break
        name = view.names.get(killer) if killer is not None else None
        if traded_by:
            out.append(_flag(view, "death_traded", t, killer=name, traded_by=view.names.get(traded_by[0]),
                             after_s=traded_by[1], nearest_teammate_m=nearest))
        elif nearest is not None and nearest > TRADE_RANGE_M:      # None: nobody left, or no position to measure
            out.append(_flag(view, "death_untraded_alone", t, killer=name, nearest_teammate_m=nearest,
                             teammates_alive=len(mates)))
        own = len(view.living(view.side(slot), before))
        enemy = len([e for e in view.enemies(slot) if view.alive(e, before)])
        if own > enemy:
            out.append(_flag(view, "death_with_advantage", t, killer=name, alive_before={"own": own, "enemy": enemy}))
    return out


def _first_contact(view, slot):
    kills = _live_kills(view)
    if not kills:
        return []
    first = kills[0]
    if first.get("killer") == slot:
        return [_flag(view, "first_kill", first["t"], victim=view.names.get(first["victim"]))]
    if first["victim"] == slot:
        killer = first.get("killer")
        return [_flag(view, "first_death", first["t"], killer=view.names.get(killer) if killer is not None else None)]
    return []


def _flashes(view, slot):
    out, mates, enemies = [], set(view.teammates(slot)), set(view.enemies(slot))
    for e in view.blob.get("util", []):
        if e.get("k") != "flash" or e.get("by") != slot:
            continue
        # a stored hit of zero seconds blinded nobody; null is a hit of unknown length
        hits = [h for h in e.get("hits") or [] if len(h) < 3 or h[2] is None or h[2] > 0]
        on_enemy = [h for h in hits if h[0] in enemies]
        on_mate = [h for h in hits if h[0] in mates]
        proof = e.get("activation") or {}
        if on_mate:
            out.append(_flag(view, "flash_hit_teammate", e["t"], hit=[view.names.get(h[0]) for h in on_mate]))
        if not on_enemy:
            if not on_mate and proof.get("state") == "completed" and proof.get("targets_complete"):
                out.append(_flag(view, "flash_hit_no_enemy", e["t"]))
            continue
        hit_at = {h[0]: h[1] for h in on_enemy}
        followed = [k for k in _live_kills(view) if k["victim"] in hit_at
                    and (k.get("killer") == slot or k.get("killer") in mates)
                    and 0 <= k["t"] - hit_at[k["victim"]] <= FLASH_FOLLOWUP_S]
        if followed:
            out.append(_flag(view, "flash_led_to_kill", e["t"], hit=[view.names.get(s) for s in hit_at],
                             killed=[view.names.get(k["victim"]) for k in followed],
                             killers=[view.names.get(k["killer"]) for k in followed]))
    return out


def _site_of(area: dict | None) -> str | None:
    return area["super"] if area and area["super"] in areas.SITES else None


def _stack(view, slot, knew, role):
    if not knew:
        return []
    side, t = view.side(slot), 0.0
    while t <= view.t_decided:
        if not view.alive(slot, t):
            break
        here = view.position(slot, t)
        mine = _site_of(here.get("area"))
        if mine is not None:
            placed: dict[str, list] = {}
            for e in knowledge.known_enemies(knew, side, t, view):
                site = _site_of(e["area"])
                if site and e["alive"] and (e["in_view"] or t - e["last_seen"] <= STACK_FRESH_S):
                    placed.setdefault(site, []).append(e["name"])
            for site, names in sorted(placed.items()):
                later = t + STACK_HOLD_S
                if site == mine or len(names) < STACK_ENEMIES or later > view.t_decided or not view.alive(slot, later):
                    continue
                after = view.position(slot, later)
                if _site_of(after.get("area")) == mine:
                    return [_flag(view, "stack_seen_elsewhere", t, site=site, enemies_seen=len(names), enemies=names,
                                  player_area_then=here["area"]["name"], player_area_after=after["area"]["name"],
                                  hold_s=STACK_HOLD_S, role=role)]
        t += 1.0
    return []


def flags(view, slot: int, knew: dict | None, role: str | None = None) -> list[dict]:
    found = _deaths(view, slot) + _first_contact(view, slot) + _flashes(view, slot) + _stack(view, slot, knew, role)
    return sorted(found, key=lambda f: (f["t"], f["kind"]))
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach/test_detectors.py -q` — Expected: 20 passed.

If `from app.scoring.impact import TRADE_WINDOW_SECONDS` fails or drags in a database at import, do not edit scoring: replace the import with `TRADE_WINDOW_S = 6.0` and add a test `assert detectors.TRADE_WINDOW_S == TRADE_WINDOW_SECONDS` guarded by `pytest.importorskip`.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/detectors.py webapp/tests/coach/test_detectors.py
git commit -F <scratch file: "Add the coach's moment detectors">
```

---

### Task 7: The match brief

**Files:**
- Create: `app/coach/brief.py`, `scripts/coach_brief.py`
- Test: `tests/coach/test_brief.py`

**Interfaces:**
- Consumes: `Snapshot` (Task 5), `detectors.flags`, `areas.callouts`. The page context's shapes (from `app/services/replays.py: page_context`): `context["match"]` has `map`, `uuid`, `score`, `rounds`; `context["players"][str(slot)]` has `slot`, `name`, `agent`, `team`, `side`; `context["rounds"][str(n)]` has `db` (`outcome`, `winner`, `plant`, `defuse`), `stats[str(slot)]` (`kills`, `deaths`, `assists`, `loadout`, `remaining`, `impact`, `kill_impact`, `death_impact`, `damage`, `econ`, `trade_credit`).
- Produces: `brief.build(snap: Snapshot, slot: int) -> dict`:

```
{"v": 1,
 "match": {"uuid", "map", "score", "rounds": [numbers]},
 "subject": {"slot", "name", "agent", "team", "side"},
 "players": [{"slot", "name", "agent", "team", "side"}],
 "areas": [area names on this map],
 "rounds": [{"round", "role": "attack" | "defense" | None, "winner", "won": bool | None, "decided_at",
             "plant", "defuse", "stats": {...} | None,
             "economy": {"<team>": {"loadout", "remaining"}} | None,     # both teams, summed over their players
             "deaths": [t...], "kills": [{"t", "victim"}],               # up to decided_at only
             "utility": [{"t", "kind", "ability"?, "hits"?, "hits_complete"?}],   # the subject's own
             "flags": [...], "gaps": [...]}],
 "ranked": [{"round", "impact"}],          # by |impact|, largest first; rounds without Impact left out
 "data_gaps": {"<round>": [gap names]}}    # only rounds that have any
```

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_brief.py`:

```python
from app.coach import brief, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT, SHARES


def snap(tmp_path, impacts, kills_by_round=None, controls=None):
    context = {**CONTEXT, "rounds": {}}
    blobs = {}
    for n, impact in impacts.items():
        context["rounds"][str(n)] = {"db": {"winner": "team1" if n % 2 else "team2", "plant": None, "defuse": None},
                                     "stats": {"0": {"kills": 1, "deaths": 0, "impact": impact}}, "kills": {}}
        blobs[n] = toys.round_blob(toys.ten(), kills=(kills_by_round or {}).get(n, ()), n=n)
    context["match"] = {**CONTEXT["match"], "rounds": sorted(impacts)}
    controls = controls or {n: ("ok", False, SHARES) for n in impacts}
    snapshot.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapshot.load(tmp_path)


def test_the_brief_names_the_subject_and_every_player(tmp_path):
    b = brief.build(snap(tmp_path, {1: 100}), 0)
    assert b["subject"] == {"slot": 0, "name": "P0#T0", "agent": "Jett", "team": "team1", "side": "A"}
    assert len(b["players"]) == 10 and b["match"]["map"] == "Toy"


def test_rounds_are_ranked_by_the_size_of_the_impact_swing(tmp_path):
    b = brief.build(snap(tmp_path, {1: 50, 2: -400, 3: 120}), 0)
    assert b["ranked"] == [{"round": 2, "impact": -400}, {"round": 3, "impact": 120}, {"round": 1, "impact": 50}]


def test_a_round_carries_its_outcome_stats_and_the_players_kills_and_deaths(tmp_path):
    kills = {1: [{"t": 10.0, "killer": 0, "victim": 5}, {"t": 20.0, "killer": 6, "victim": 0}]}
    (r,) = brief.build(snap(tmp_path, {1: 100}, kills), 0)["rounds"]
    assert r["won"] is True and r["stats"]["impact"] == 100
    assert r["kills"] == [{"t": 10.0, "victim": "P5#T5"}] and r["deaths"] == [20.0]
    assert "first_kill" in [f["kind"] for f in r["flags"]]


def test_a_round_carries_the_role_both_economies_and_the_players_utility(tmp_path):
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {
        "db": {"winner": "team1", "plant": 31.0, "defuse": None}, "kills": {},
        "stats": {str(s): {"loadout": 1000 * (s + 1), "remaining": 100, "impact": 0} for s in range(10)}}}}
    util = [{"k": "flash", "t": 9.0, "by": 0, "u": 0, "v": 0, "hits": [[5, 9.5, 1.5]]},
            {"k": "flash", "t": 11.0, "by": 1, "u": 0, "v": 0, "hits": []}]
    blob = toys.round_blob(toys.ten(), util=util, kills=[{"t": 50.0, "killer": 5, "victim": 0}], t_decided=40.0)
    links = {1: {"sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)}, "db_deaths": []}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {1: ("ok", False, SHARES)}, links), tmp_path)
    (r,) = brief.build(snapshot.load(tmp_path), 0)["rounds"]
    assert r["role"] == "attack" and r["plant"] == 31.0 and r["decided_at"] == 40.0
    assert r["economy"] == {"team1": {"loadout": 15000, "remaining": 500},     # 1000+2000+3000+4000+5000
                            "team2": {"loadout": 40000, "remaining": 500}}     # 6000+...+10000
    assert [u["t"] for u in r["utility"]] == [9.0] and r["utility"][0]["hits"] == [{"name": "P5#T5", "seconds": 1.5}]
    assert r["deaths"] == []                               # the death at 50 s came after the round was decided


def test_gaps_are_listed_per_round_and_only_where_they_exist(tmp_path):
    controls = {1: ("ok", False, SHARES), 2: ("failed", False, None)}
    b = brief.build(snap(tmp_path, {1: 10, 2: 20}, controls=controls), 0)
    assert b["data_gaps"] == {"match": ["areas"], "2": ["control_failed", "team_knowledge"]}


def test_a_map_without_callouts_lists_no_areas_and_says_so(tmp_path):
    b = brief.build(snap(tmp_path, {1: 10}), 0)           # "Toy" is not in the real callouts file
    assert b["areas"] == [] and b["data_gaps"] == {"match": ["areas"]}
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_brief.py -q` — Expected: FAIL, `No module named 'app.coach.brief'`.

- [ ] **Step 3: Implement**

`app/coach/brief.py`:

```python
"""The match brief: what the coach is handed before it asks anything. Facts and pointers for one player across
one match. Impact ranks where to look first; the detectors' flags add what Impact cannot see."""

from __future__ import annotations

from app.coach import areas, detectors

VERSION = 1
_PLAYER_KEYS = ("slot", "name", "agent", "team", "side")


def _player(p: dict) -> dict:
    return {k: p.get(k) for k in _PLAYER_KEYS}


def _economy(players: dict, stats: dict) -> dict | None:
    """Each team's loadout and remaining credits, summed over the players the site has a row for."""
    out: dict[str, dict] = {}
    for slot, row in (stats or {}).items():
        team = (players.get(str(slot)) or {}).get("team")
        if team is None or row.get("loadout") is None:
            continue
        sums = out.setdefault(team, {"loadout": 0, "remaining": 0})
        sums["loadout"] += row["loadout"]
        sums["remaining"] += row.get("remaining") or 0
    return dict(sorted(out.items())) or None


def build(snap, slot: int) -> dict:
    players = snap.context["players"]
    subject = _player(players[str(slot)])
    rounds, ranked, gaps = [], [], {}
    for n in snap.round_numbers:
        view, site, control = snap.view(n), snap.site(n), snap.control(n)
        db = site.get("db") or {}
        stats = (site.get("stats") or {}).get(str(slot))
        winner = db.get("winner")
        live = [k for k in view.kills if k["t"] <= view.t_decided]
        role = snap.role(n, slot)
        rounds.append({
            "round": n, "role": role, "winner": winner,
            "won": None if winner is None else winner == subject["team"], "decided_at": view.t_decided,
            "plant": db.get("plant"), "defuse": db.get("defuse"), "stats": stats,
            "economy": _economy(players, site.get("stats")),
            "deaths": [k["t"] for k in live if k["victim"] == slot],
            "kills": [{"t": k["t"], "victim": snap.names.get(k["victim"])} for k in live if k.get("killer") == slot],
            "utility": [{k: v for k, v in row.items() if k != "by"} for row in view.utility(by=slot)],
            "flags": detectors.flags(view, slot, (control or {}).get("knew"), role),
            "gaps": snap.gaps(n)})
        if stats and stats.get("impact") is not None:
            ranked.append({"round": n, "impact": stats["impact"]})
        if snap.gaps(n):
            gaps[str(n)] = snap.gaps(n)
    ranked.sort(key=lambda r: (-abs(r["impact"]), r["round"]))
    match = snap.context["match"]
    names = sorted({c["name"] for c in areas.callouts(match["map"])})
    if not names:
        gaps = {"match": ["areas"], **gaps}
    return {"v": VERSION,
            "match": {"uuid": match["uuid"], "map": match["map"], "score": match.get("score"),
                      "rounds": snap.round_numbers},
            "subject": subject,
            "players": [_player(players[s]) for s in sorted(players, key=int)],
            "areas": names,
            "rounds": rounds, "ranked": ranked, "data_gaps": gaps}
```

`scripts/coach_brief.py`:

```python
"""Prints one player's match brief from a local snapshot (scripts/coach_snapshot.py). No database.

    $PY scripts/coach_brief.py --run <dir> --player "Name#TAG" [--flags-only]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--player", required=True)
    parser.add_argument("--flags-only", action="store_true")
    args = parser.parse_args()
    from app.coach import brief, snapshot

    try:
        snap = snapshot.load(args.run)
        built = brief.build(snap, snap.slot_of(args.player))
    except snapshot.SnapshotError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.flags_only:
        for r in built["rounds"]:
            for f in r["flags"]:
                print(f'r{f["round"]:>2} {f["t"]:>6.1f}s {f["kind"]:<24} {json.dumps(f["values"])}')
        return 0
    print(json.dumps(built, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/brief.py webapp/scripts/coach_brief.py webapp/tests/coach/test_brief.py
git commit -F <scratch file: "Add the coach's match brief">
```

---

### Task 8: Query commands and the run log

**Files:**
- Create: `app/coach/query.py`, `scripts/coach_query.py`
- Test: `tests/coach/test_query.py`

**Interfaces:**
- Consumes: `Snapshot`, `RoundView`, `knowledge.known_enemies`, `control_areas.control_over`.
- Produces:
  - `query.QueryError(Exception)`
  - `query.where(snap, n, t) -> dict`
  - `query.knew(snap, n, t, side) -> dict` (`side` is `"A"`, `"B"`, or a player token meaning that player's side)
  - `query.distance(snap, n, t, a, b) -> dict`
  - `query.control(snap, n, t0, t1, area) -> dict`
  - `query.timeline(snap, n, t0=None, t1=None) -> dict`
  - `query.impact(snap, n, player) -> dict`
  - `query.COMMANDS: dict[str, callable]`
  - `query.answer(snap, command: str, args: list[str]) -> dict` : the answer alone, nothing written. Never raises for bad input: an unknown command, a wrong argument count, or text where a number belongs (`7.0` for a round, `0:41` for a time) gives `{"error": message}`. Plan 3's checker calls it again to confirm a logged answer.
  - `query.run(snap, directory: Path, command: str, args: list[str]) -> dict` : calls `answer`, appends `{"id", "cmd", "args", "result"}` to `<directory>/log.jsonl`, returns the result with its `"id"` (`q1`, `q2`, ...). An error is logged too, as `{"error": message}`, and gets an id.
  - `query.read_log(directory: Path) -> dict[str, dict]` keyed by id.

Every answer for a missing source is `{"available": False, "reason": "<gap name>"}` and still gets logged.

Where each fact comes from (checked 2026-10-12):

- **Plant and defuse** are read from the page context, `snap.site(n)["db"]["plant"]` / `["defuse"]`, which are already on the replay clock (`app/services/replays.py:161-162`). The blob's own `plant`/`defuse` are hardcoded `None` (`condense.py:1502`) and are not read.
- **Damage** is the blob's `damage` rows, through `RoundView.damage()`.
- **Health** is `app.replays.player_state.vitals_at(blob.get("player_state"), slot, t)`: the values after the player's latest recorded hit at or before `t`, or `None` when no hit is recorded. The replay has no heal records, so `where` labels it as such.
- **`knew`** returns placed sightings as `enemies` and the unplaced locating events (sound, damage, kills) as `signals` (Task 3).

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_query.py`:

```python
import pytest

from app.coach import query, snapshot
from tests.coach import toys
from tests.coach.test_snapshot import CONTEXT

KNEW = {"A": {"5": [[5.0, 8.0, 8000, 8000]]}, "B": {}}
SHARES = {"t": [0.0, 10.0], "cells": {"A Site": 2}, "areas": {"A Site": [[2, 0, 0], [0, 2, 0]]},
          "knew": KNEW, "signals": {"A": [[3.0, 6, "footsteps"], [40.0, 7, "gunfire"]], "B": []}, "group_side": None}
KILLS = [{"t": 20.0, "killer": 5, "victim": 0}]
DAMAGE = {"k": "damage", "t": 13.0, "t1": 13.2, "by": 5, "target": 0, "src": "gun", "wall": False, "n": 3}


@pytest.fixture
def snap(tmp_path):
    context = {**CONTEXT, "rounds": {"1": {"db": {"winner": "team1"}, "kills": {},
                                           "stats": {"0": {"impact": -250, "kill_impact": 0, "death_impact": -250}}},
                                     "2": {"db": {"plant": 12.5, "defuse": 40.0}, "stats": {}, "kills": {}}}}
    hurt = toys.round_blob(toys.ten(), n=2, util=[DAMAGE])
    hurt["player_state"] = {"version": 1, "vitals": {"0": [{"t": 13.0, "life": 0, "hp": 61, "sh": 0, "mhp": 100,
                                                            "msh": 50}]}}
    blobs = {1: toys.round_blob(toys.ten(), kills=KILLS, n=1,
                                util=[{"k": "flash", "t": 9.0, "by": 1, "u": 0, "v": 0, "hits": []}]),
             2: hurt}
    controls = {1: ("ok", False, SHARES), 2: ("not_ready", False, None)}
    snapshot.save(snapshot.assemble(context, blobs, controls), tmp_path)
    return snapshot.load(tmp_path)


def test_where_lists_every_player_with_side_and_state(snap):
    out = query.where(snap, 1, 25.0)
    assert len(out["players"]) == 10
    dead = out["players"][0]
    assert (dead["name"], dead["side"], dead["alive"], dead["died_at"]) == ("P0#T0", "A", False, 20.0)
    assert out["players"][1]["alive"] is True and "u" in out["players"][1]


def test_knew_answers_for_a_side_or_for_a_players_side(snap):
    by_side = query.knew(snap, 1, 15.0, "A")
    assert by_side["side"] == "A" and [e["name"] for e in by_side["enemies"]] == ["P5#T5"]
    assert query.knew(snap, 1, 15.0, "P2")["side"] == "A"
    assert query.knew(snap, 1, 15.0, "B")["enemies"] == []


def test_knew_lists_what_was_heard_apart_from_what_was_seen(snap):
    out = query.knew(snap, 1, 15.0, "A")
    assert out["signals"] == [{"t": 3.0, "name": "P6#T6", "reason": "footsteps"}]      # the 40 s one is later
    assert "no place" in out["note"]


def test_where_gives_health_after_the_last_recorded_hit(snap):
    before, after = query.where(snap, 2, 5.0), query.where(snap, 2, 20.0)
    assert before["players"][0]["health"] is None                  # no hit recorded yet: unknown, not full
    assert after["players"][0]["health"] == {"hp": 61, "shield": 0}
    assert query.where(snap, 1, 25.0)["players"][0].get("health") is None      # dead: no health


def test_knew_without_team_knowledge_says_so(snap):
    assert query.knew(snap, 2, 15.0, "A") == {"available": False, "reason": "team_knowledge"}


def test_distance_between_two_living_players(snap):
    out = query.distance(snap, 1, 10.0, "P0", "P5")
    assert out["metres"] == pytest.approx(121.2, abs=0.1)
    assert query.distance(snap, 1, 25.0, "P0", "P5") == {"available": False, "reason": "P0#T0 was dead at 25.0"}


def test_control_reports_shares_or_says_what_is_missing(snap):
    assert query.control(snap, 1, 0.0, 20.0, "A Site")["shares"] == {"a": 0.5, "b": 0.5, "contested": 0.0, "none": 0.0}
    assert query.control(snap, 2, 0.0, 20.0, "A Site") == {"available": False, "reason": "control_not_ready"}
    with pytest.raises(query.QueryError, match="A Site"):
        query.control(snap, 1, 0.0, 20.0, "Nowhere")


def test_timeline_orders_events_and_can_be_cut_to_a_span(snap):
    events = query.timeline(snap, 1)["events"]
    assert [(e["t"], e["kind"]) for e in events] == [(9.0, "flash"), (20.0, "kill")]
    assert events[1]["killer"] == "P5#T5" and events[1]["victim"] == "P0#T0"
    assert query.timeline(snap, 1, 15.0, 30.0)["events"] == events[1:]


def test_timeline_has_the_plant_the_defuse_and_damage(snap):
    events = query.timeline(snap, 2)["events"]
    assert [(e["t"], e["kind"]) for e in events] == [(12.5, "plant"), (13.0, "damage"), (40.0, "defuse")]
    assert events[1] == {"t": 13.0, "kind": "damage", "by": "P5#T5", "target": "P0#T0", "source": "gun", "hits": 3}


def test_a_kill_after_the_round_was_decided_is_marked(tmp_path):
    blob = toys.round_blob(toys.ten(), kills=[{"t": 50.0, "killer": 5, "victim": 0}], t_decided=40.0)
    context = {**CONTEXT, "match": {**CONTEXT["match"], "rounds": [1]}, "rounds": {"1": {"db": {}, "stats": {}}}}
    snapshot.save(snapshot.assemble(context, {1: blob}, {}), tmp_path)
    (kill,) = query.timeline(snapshot.load(tmp_path), 1)["events"]
    assert kill["after_decided"] is True


def test_impact_is_the_stored_row(snap):
    assert query.impact(snap, 1, "P0")["impact"] == -250
    assert query.impact(snap, 2, "P0") == {"available": False, "reason": "no Impact row for this round"}


def test_bad_rounds_times_and_players_fail_with_the_valid_values(snap):
    with pytest.raises(query.QueryError, match="rounds 1-2"):
        query.where(snap, 9, 1.0)
    with pytest.raises(query.QueryError, match="0.0 to 60.0"):
        query.where(snap, 1, 500.0)
    with pytest.raises(query.QueryError, match="P0#T0"):
        query.distance(snap, 1, 1.0, "nobody", "P5")


def test_every_call_is_logged_with_an_id_errors_included(snap, tmp_path):
    first = query.run(snap, tmp_path, "where", ["1", "25"])
    second = query.run(snap, tmp_path, "where", ["9", "1"])
    third = query.run(snap, tmp_path, "nonsense", [])
    assert (first["id"], second["id"], third["id"]) == ("q1", "q2", "q3")
    assert "error" in second and "error" in third
    log = query.read_log(tmp_path)
    assert log["q1"]["cmd"] == "where" and log["q1"]["args"] == ["1", "25"]
    assert log["q1"]["result"]["players"][0]["died_at"] == 20.0


@pytest.mark.parametrize("command, args", [
    ("where", ["7.0", "10"]),                # a round is a whole number
    ("where", ["1", "0:41"]),                # a time is seconds, not a clock
    ("distance", ["1", "ten", "P0", "P5"]),
    ("where", ["1"]),                        # too few arguments
])
def test_malformed_arguments_are_an_answer_not_a_crash(snap, tmp_path, command, args):
    out = query.run(snap, tmp_path, command, args)
    assert "error" in out and out["id"] == "q1" and query.read_log(tmp_path)["q1"]["args"] == args


def test_answer_computes_without_logging(snap, tmp_path):
    assert query.answer(snap, "impact", ["1", "P0"])["impact"] == -250
    assert query.read_log(tmp_path) == {}


def test_distance_without_a_position_says_so(snap):
    snap.view(1).blob["tracks"]["5"] = []                  # alive, and no track sample
    assert query.distance(snap, 1, 10.0, "P0", "P5") == {"available": False, "reason": "no position for one of them at 10.0"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_query.py -q` — Expected: FAIL, `No module named 'app.coach.query'`.

- [ ] **Step 3: Implement**

`app/coach/query.py`:

```python
"""The coach's questions about a round, answered from the snapshot. Read-only and computed: the model never
estimates a distance or a time, it asks. Every call is appended to the run's log with an id, and a report's
evidence cites those ids, which is how a claim is checked (app/coach/checker.py, plan 3)."""

from __future__ import annotations

import json
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


def _health(view, slot: int, t: float) -> dict | None:
    event = player_state.vitals_at(view.blob.get("player_state"), slot, t)
    return None if event is None else {"hp": event["hp"], "shield": event["sh"]}


def where(snap, n, t) -> dict:
    t = float(t)
    view = _view(snap, n, t)
    players = []
    for s in view.slots:
        row = {"slot": s, "name": snap.names.get(s), "side": view.side(s), "role": snap.role(int(n), s),
               **view.position(s, t)}
        if row["alive"]:
            row["health"] = _health(view, s, t)
        players.append(row)
    return {"round": int(n), "t": t, "players": players,
            "note": "health is hp and shield after the player's last recorded hit; null means no hit is recorded "
                    "so far, not full health; heals are not recorded"}


def knew(snap, n, t, side) -> dict:
    t = float(t)
    view = _view(snap, n, t)
    side = side if side in ("A", "B") else view.side(_slot(snap, side))
    control = snap.control(int(n)) or {}
    if not control.get("knew"):
        return _missing("team_knowledge")
    return {"round": int(n), "t": t, "side": side,
            "enemies": knowledge.known_enemies(control["knew"], side, t, view),
            "signals": knowledge.signals(control.get("signals"), side, t, snap.names),
            "note": "team knowledge, assuming everything any teammate learned was called. enemies: those the team "
                    "had seen, with the place and time of the last sighting. signals: every stored event that "
                    "located an enemy for the team (sight, sound, damage, a kill), with its time and reason and "
                    "no place; null when this round stores none"}


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
    _view(snap, n, t0, t1)
    shares = snap.control(int(n))
    if shares is None:
        gap = next((g for g in snap.gaps(int(n)) if g.startswith("control_")), "control_not_ready")
        return _missing(gap)
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


def read_log(directory: Path) -> dict[str, dict]:
    path = Path(directory) / LOG
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["id"]: row for row in rows}


def answer(snap, command: str, args: list[str]) -> dict:
    """The answer alone. Bad input is an answer too, so the caller reads it instead of retrying blind."""
    try:
        if command not in COMMANDS:
            raise QueryError(f"unknown command {command!r}; commands: {', '.join(sorted(COMMANDS))}")
        return COMMANDS[command](snap, *(args if isinstance(args, list) else []))
    except QueryError as error:
        return {"error": str(error)}
    except (TypeError, ValueError):
        return {"error": f"wrong arguments for {command}: {args}. Rounds are whole numbers and times are "
                         "seconds into the round (41.5, not 0:41)"}


def run(snap, directory: Path, command: str, args: list[str]) -> dict:
    result = answer(snap, command, args)
    path = Path(directory) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"id": f"q{len(read_log(directory)) + 1}", "cmd": command, "args": list(args), "result": result}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return {"id": entry["id"], **result}
```

`scripts/coach_query.py`:

```python
"""One coach question about a round, answered from a local snapshot and logged. No database, no network.

    $PY scripts/coach_query.py --run <dir> where <round> <t>
    $PY scripts/coach_query.py --run <dir> knew <round> <t> <A|B|player>
    $PY scripts/coach_query.py --run <dir> distance <round> <t> <player> <player>
    $PY scripts/coach_query.py --run <dir> control <round> <t0> <t1> "<area>"
    $PY scripts/coach_query.py --run <dir> timeline <round> [<t0> <t1>]
    $PY scripts/coach_query.py --run <dir> impact <round> <player>

Prints JSON with the call's log id. Times are seconds into the round. Always exits 0: an error is an answer
(`{"id", "error"}`), so the caller reads it instead of retrying blind."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("command")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    from app.coach import query, snapshot

    print(json.dumps(query.run(snapshot.load(args.run), args.run, args.command, args.args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/query.py webapp/scripts/coach_query.py webapp/tests/coach/test_query.py
git commit -F <scratch file: "Add the coach's query commands and run log">
```

---

### Task 9: Check against a real match, and set the thresholds with the owner

**Files:**
- Modify (only if the owner changes a value): `app/coach/detectors.py` constants, and the matching test expectations.

This task has no new code. It is the spec's "thresholds are set from a look at real rounds".

- [ ] **Step 1: Print the flags for the owner's own match**

Using the snapshot from Task 5, Step 5:

`$PY scripts/coach_brief.py --run $env:TEMP\valo-coach\<uuid> --player "<owner's Riot ID>" --flags-only`

- [ ] **Step 1b: Read what the real snapshot says about the things the tests could only assume**

From the same snapshot, print and report to the owner (a few lines of throwaway Python over `snapshot.json`, not committed):

- how many rounds have a survivor whose `alive` interval ends in `null` (expected: nearly all; this is the case that crashed the first draft of this plan),
- whether any round's blob has `side: null` players, and how many rounds carry the `db_only_deaths` gap,
- the distinct `reason` values in `control.signals` across the match, so the playbook (plan 3) can name them in the owner's terms,
- one planted round's `timeline`, checked against the viewer's plant marker,
- how many rounds have `role` filled for the owner's slot (expected: all).

A surprise here is a finding: stop and report it before going on.

- [ ] **Step 2: Spot-check three flags in the replay viewer**

For three flags of different kinds, open `https://valowithfriendstracker.onrender.com/replays/<uuid>?round=<n>&t=<t>` and confirm the moment shows what the flag says (who died, who was near, what was seen). Then run the matching query, for example `coach_query.py --run <dir> where <n> <t>`, and confirm it agrees with the picture. Any disagreement is a bug in this plan's code: fix it under a failing test before going on.

- [ ] **Step 3: Show the owner the list and the five thresholds**

Present the flag list and `TRADE_RANGE_M`, `STACK_ENEMIES`, `STACK_FRESH_S`, `STACK_HOLD_S`, `FLASH_FOLLOWUP_S` with their current values. Ask which flags are noise and which moments are missing. Change constants only on the owner's word, update the tests that pin them, and re-run `$PY -m pytest tests/coach -q`.

- [ ] **Step 4: Run the whole suite**

Run: `$PY -m pytest tests -q -x` — Expected: no new failures against the branch's starting point (note any failures that already exist on `origin/main` rather than fixing them here).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/coach/detectors.py webapp/tests/coach
git commit -F <scratch file: "Set the coach's detector thresholds from real rounds">
```

Skip the commit if nothing changed.
