# Squad Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/squad` and `/squad/career`, a friends-only "who do I play with most / best" page, following the design in `docs/superpowers/specs/2026-07-30-squad-page-design.md`.

**Architecture:** New pure-logic module `app/services/squad.py` (DB-fetch function + pure aggregator, same split as `app/services/sessions.py` / `session_stats.py`), a small generalization of `app/services/shoutouts.py`'s `assign_shoutouts`, a new router `app/routers/squad.py` mirroring `app/routers/players.py`'s Recent-30/Career pair, and two new templates mirroring `players/detail.html` + `_profile_sections.html`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 ORM, Jinja2, htmx (existing project stack, no new dependencies).

## Global Constraints

- Shoutout eligibility threshold: **20 rounds together** minimum to be a shoutout candidate (no threshold to appear in the ranked table).
- Recent/Career windowing: `RECENT_MATCH_LIMIT = 30` (reuse `app/routers/players.py`'s constant), `None` = career/all-time.
- `Team` enum values are the strings `"team-1"` / `"team-2"` (`app/models/match.py`).
- `_winner_side(outcome)` pattern (outcome string starts with `"Team A"` / `"Team B"`) is duplicated per-file across this codebase rather than imported, to avoid circular imports — follow that convention in `squad.py` too.
- No new template test harness exists; template/route correctness is verified manually per the spec, not by an automated test.
- Tests are plain functions with `SimpleNamespace`/dataclass stand-ins, no live DB (`webapp/tests/test_sessions.py` is the reference convention).

---

## File Structure

- Modify `webapp/app/services/shoutouts.py` — add `categories` param to `assign_shoutouts` (default = existing `SHOUTOUT_CATEGORIES`).
- Create `webapp/app/services/squad.py` — `PairStats`, `SquadOverview`, `SharedRound` (internal), `SQUAD_SHOUTOUT_CATEGORIES`, pure aggregator `build_squad_overview(...)`, DB-facing `get_squad_overview(db, viewer_player_id, match_limit)`.
- Create `webapp/app/routers/squad.py` — `GET /squad`, `GET /squad/career`.
- Modify `webapp/app/main.py` — register `squad.router`.
- Modify `webapp/app/templates/base.html` — add nav link.
- Create `webapp/app/templates/squad/detail.html` — page shell (Recent/Career toggle, copy of the `players/detail.html` pattern minus the player-specific charts).
- Create `webapp/app/templates/squad/_squad_sections.html` — header tiles, shoutouts, sortable pair table.
- Create `webapp/tests/test_shoutouts.py` — test for the new `categories` param.
- Create `webapp/tests/test_squad.py` — tests for the pure aggregator.

---

### Task 1: Generalize `assign_shoutouts` to accept a `categories` param

**Files:**
- Modify: `webapp/app/services/shoutouts.py:51-56` (signature only; internals unchanged)
- Test: `webapp/tests/test_shoutouts.py` (new file)

**Interfaces:**
- Produces: `assign_shoutouts(roster, raw_dicts, best_single_round_impact, anchor=None, categories=SHOUTOUT_CATEGORIES)` — existing callers (`app/services/matches.py`, `app/services/session_stats.py`) pass no `categories` arg, so behavior is unchanged for them.

- [ ] **Step 1: Write the failing test**

```python
# webapp/tests/test_shoutouts.py
from app.services.shoutouts import assign_shoutouts

CUSTOM_CATEGORIES = [
    ("wins_together", "Best Duo", "won {v}% of rounds together"),
]


def test_assign_shoutouts_uses_custom_categories_param():
    roster = [(1, "Alice#NA1", "Jett"), (2, "Bob#NA1", "Omen")]
    raw_dicts = {"wins_together": {1: 80, 2: 40}}
    shoutouts = assign_shoutouts(roster, raw_dicts, {}, categories=CUSTOM_CATEGORIES)

    alice = next(s for s in shoutouts if s.player_id == 1)
    assert alice.headline == "Best Duo"
    assert alice.detail == "won 80% of rounds together"


def test_assign_shoutouts_default_categories_unchanged():
    # Sanity check the default param still exercises the real (module-constant)
    # category catalog for an existing caller-shaped case.
    roster = [(1, "Alice#NA1", "Jett")]
    raw_dicts = {"entry_kill_counts": {1: 3}}
    shoutouts = assign_shoutouts(roster, raw_dicts, {})
    assert shoutouts[0].headline == "Entry Fragger"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `webapp/`): `.\.venv\Scripts\python.exe -m pytest tests/test_shoutouts.py -v`
Expected: FAIL with `TypeError: assign_shoutouts() got an unexpected keyword argument 'categories'`

- [ ] **Step 3: Write minimal implementation**

In `webapp/app/services/shoutouts.py`, change the signature and the one place `SHOUTOUT_CATEGORIES` is read inside the function body:

```python
def assign_shoutouts(
    roster: list[tuple[int, str, str]],
    raw_dicts: dict[str, dict[int, int]],
    best_single_round_impact: dict[int, float],
    anchor: tuple[int, str, str] | None = None,
    categories: list[tuple[str, str, str]] = SHOUTOUT_CATEGORIES,
) -> list[PlayerShoutout]:
```

and in the body, replace:

```python
    for raw_key, _headline, _template in SHOUTOUT_CATEGORIES:
```

with:

```python
    for raw_key, _headline, _template in categories:
```

and:

```python
    for player_index_, category_i in category_of_player.items():
        player_id = player_ids[player_index_]
        _raw_key, headline, template = SHOUTOUT_CATEGORIES[category_i]
```

with:

```python
    for player_index_, category_i in category_of_player.items():
        player_id = player_ids[player_index_]
        _raw_key, headline, template = categories[category_i]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_shoutouts.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: all PASS (existing `test_sessions.py`, `test_map_streaks.py` unaffected since they don't call `assign_shoutouts` directly)

- [ ] **Step 6: Commit**

```bash
git add webapp/app/services/shoutouts.py webapp/tests/test_shoutouts.py
git commit -m "Generalize assign_shoutouts to accept a categories param"
```

---

### Task 2: `app/services/squad.py` — data model + pure aggregator

**Files:**
- Create: `webapp/app/services/squad.py`
- Test: `webapp/tests/test_squad.py` (new file)

**Interfaces:**
- Consumes: `PlayerShoutout`, `assign_shoutouts`, `SQUAD_SHOUTOUT_CATEGORIES` will be defined in this same file (no cross-file consumption yet at this task).
- Produces:
  - `@dataclass SharedRound(match_id: int, won: bool, viewer_round_win_impact: float, friend_round_win_impact: float, clutch: bool, traded: int, viewer_kills: int, viewer_deaths: int, friend_kills: int, friend_deaths: int)`
  - `@dataclass PairStats` (exact shape from the spec, reproduced below)
  - `@dataclass SquadOverview` (exact shape from the spec, reproduced below)
  - `SQUAD_ROUND_THRESHOLD = 20` (module constant)
  - `SQUAD_SHOUTOUT_CATEGORIES: list[tuple[str, str, str]]`
  - `build_squad_overview(pair_shared_rounds: dict[int, list[SharedRound]], friend_names: dict[int, str], friend_agent_counts: dict[int, Counter], viewer_match_ids: set[int]) -> SquadOverview` — pure, no DB. `viewer_match_ids` is the viewer's own match window (used for `total_matches_together`, per the spec's `SquadOverview.total_matches_together` being "viewer's own match count in-window").
  - Task 3 will add `get_squad_overview(db, viewer_player_id, match_limit)`, which calls `build_squad_overview`.

- [ ] **Step 1: Write the failing tests**

```python
# webapp/tests/test_squad.py
from collections import Counter

from app.services.squad import SharedRound, build_squad_overview


def _round(won=True, viewer_rwi=1.0, friend_rwi=1.0, clutch=False, traded=0,
           vk=1, vd=0, fk=1, fd=0, match_id=1):
    return SharedRound(
        match_id=match_id,
        won=won,
        viewer_round_win_impact=viewer_rwi,
        friend_round_win_impact=friend_rwi,
        clutch=clutch,
        traded=traded,
        viewer_kills=vk,
        viewer_deaths=vd,
        friend_kills=fk,
        friend_deaths=fd,
    )


def test_pairs_sorted_by_matches_together_descending():
    pair_shared_rounds = {
        1: [_round(match_id=1), _round(match_id=2)],  # 2 matches
        2: [_round(match_id=1)],  # 1 match
    }
    friend_names = {1: "Alice#NA1", 2: "Bob#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 2}), 2: Counter({"Omen": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1, 2})

    assert [p.friend_player_id for p in overview.pairs] == [1, 2]
    assert overview.squad_size == 2
    assert overview.total_matches_together == 2  # viewer's own in-window match count


def test_win_rate_round_win_impact_clutch_and_trade_aggregation():
    rounds = [
        _round(won=True, viewer_rwi=2.0, friend_rwi=1.0, clutch=True, traded=1, vk=2, vd=0, fk=1, fd=1),
        _round(won=False, viewer_rwi=-1.0, friend_rwi=0.0, clutch=False, traded=0, vk=0, vd=1, fk=0, fd=1),
    ]
    pair_shared_rounds = {1: rounds}
    friend_names = {1: "Alice#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1})
    pair = overview.pairs[0]

    assert pair.rounds_together == 2
    assert pair.win_rate_together == 0.5
    # (2.0+1.0) + (-1.0+0.0) = 2.0, / 2 rounds = 1.0
    assert pair.avg_round_win_impact_together == 1.0
    assert pair.clutches_together == 1
    assert pair.traded_together == 1
    # kills: 2+1+0+0=3, deaths: 0+1+1+1=3 -> differential 0
    assert pair.kill_differential_together == 0
    assert pair.most_played_agent_together == "Jett"


def test_friend_below_threshold_appears_in_table_but_not_shoutouts():
    below_threshold_rounds = [_round(match_id=1)] * 19  # SQUAD_ROUND_THRESHOLD is 20
    pair_shared_rounds = {1: below_threshold_rounds}
    friend_names = {1: "Alice#NA1"}
    friend_agent_counts = {1: Counter({"Jett": 1})}

    overview = build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, {1})

    assert len(overview.pairs) == 1
    assert overview.pairs[0].rounds_together == 19
    assert all(s.player_id != 1 or s.headline == "UH OH" for s in overview.shoutouts) or overview.shoutouts == []


def test_empty_squad():
    overview = build_squad_overview({}, {}, {}, set())
    assert overview.squad_size == 0
    assert overview.total_matches_together == 0
    assert overview.total_rounds_together == 0
    assert overview.pairs == []
    assert overview.shoutouts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_squad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.squad'`

- [ ] **Step 3: Write the implementation**

```python
# webapp/app/services/squad.py
from collections import Counter
from dataclasses import dataclass, field

from app.services.shoutouts import PlayerShoutout, assign_shoutouts

# Minimum shared rounds before a friend is eligible for a shoutout category --
# keeps a 2-round 100% win streak from outranking a friend with a real sample
# size. No such threshold applies to appearing in the main ranked table.
SQUAD_ROUND_THRESHOLD = 20

SQUAD_SHOUTOUT_CATEGORIES: list[tuple[str, str, str]] = [
    ("win_rate_together", "Best Duo", "won {v}% of rounds together"),
    ("avg_round_win_impact_together", "Dynamic Duo", "{v} avg round-win impact together"),
    ("clutches_together", "Clutch Partners", "{v} clutch round{s} won together"),
    ("traded_together", "Trade Partner", "traded for each other {v} time{s}"),
    ("rounds_together", "Ride or Die", "{v} round{s} played together"),
    ("kill_differential_together", "Lethal Combo", "outkilled opponents by {v} combined, together"),
]


@dataclass
class SharedRound:
    """One round the viewer and one friend both played as teammates.

    `viewer_round_win_impact`/`friend_round_win_impact` are each player's own
    win-gated round impact for that round (kill_impact only if their shared
    team won it, minus death_impact regardless -- see
    app.services.matches.average_round_win_impact for the single-player
    version this mirrors). `clutch` is True if either the viewer or this
    friend resolved a clutch (1-or-2-alive win) this round while teammates.
    `traded` is the combined count of the viewer trading for the friend plus
    the friend trading for the viewer this round.
    """

    match_id: int
    won: bool
    viewer_round_win_impact: float
    friend_round_win_impact: float
    clutch: bool
    traded: int
    viewer_kills: int
    viewer_deaths: int
    friend_kills: int
    friend_deaths: int


@dataclass
class PairStats:
    friend_player_id: int
    display_name: str
    most_played_agent_together: str
    matches_together: int
    rounds_together: int
    win_rate_together: float
    avg_round_win_impact_together: float
    clutches_together: int
    traded_together: int
    kill_differential_together: int


@dataclass
class SquadOverview:
    squad_size: int
    total_matches_together: int
    total_rounds_together: int
    pairs: list[PairStats] = field(default_factory=list)
    shoutouts: list[PlayerShoutout] = field(default_factory=list)


def _aggregate_pair(
    friend_player_id: int,
    display_name: str,
    agent_counts: Counter,
    shared_rounds: list[SharedRound],
) -> PairStats:
    matches_together = len({r.match_id for r in shared_rounds})
    rounds_together = len(shared_rounds)
    wins = sum(1 for r in shared_rounds if r.won)
    win_rate = wins / rounds_together if rounds_together else 0.0
    round_win_impact_sum = sum(r.viewer_round_win_impact + r.friend_round_win_impact for r in shared_rounds)
    avg_round_win_impact = round_win_impact_sum / rounds_together if rounds_together else 0.0
    clutches = sum(1 for r in shared_rounds if r.clutch)
    traded = sum(r.traded for r in shared_rounds)
    kills = sum(r.viewer_kills + r.friend_kills for r in shared_rounds)
    deaths = sum(r.viewer_deaths + r.friend_deaths for r in shared_rounds)
    top_agent = agent_counts.most_common(1)[0][0] if agent_counts else ""

    return PairStats(
        friend_player_id=friend_player_id,
        display_name=display_name,
        most_played_agent_together=top_agent,
        matches_together=matches_together,
        rounds_together=rounds_together,
        win_rate_together=win_rate,
        avg_round_win_impact_together=avg_round_win_impact,
        clutches_together=clutches,
        traded_together=traded,
        kill_differential_together=kills - deaths,
    )


def build_squad_overview(
    pair_shared_rounds: dict[int, list[SharedRound]],
    friend_names: dict[int, str],
    friend_agent_counts: dict[int, Counter],
    viewer_match_ids: set[int],
) -> SquadOverview:
    """Pure aggregation: given every friend's shared rounds with the viewer
    (already fetched from the DB), builds the ranked pair list and shoutouts.
    `viewer_match_ids` is the viewer's own match window (recent-30 or career)
    -- used only for `total_matches_together`, since a friend's own match
    count outside shared matches is irrelevant to this page.
    """
    pairs = [
        _aggregate_pair(fid, friend_names.get(fid, "?"), friend_agent_counts.get(fid, Counter()), rounds)
        for fid, rounds in pair_shared_rounds.items()
    ]
    pairs.sort(key=lambda p: p.matches_together, reverse=True)

    total_rounds_together = sum(p.rounds_together for p in pairs)

    eligible = [p for p in pairs if p.rounds_together >= SQUAD_ROUND_THRESHOLD]
    shoutouts: list[PlayerShoutout] = []
    if eligible:
        roster = [(p.friend_player_id, p.display_name, p.most_played_agent_together) for p in eligible]
        raw_dicts: dict[str, dict[int, int]] = {
            "win_rate_together": {p.friend_player_id: round(p.win_rate_together * 100) for p in eligible},
            "avg_round_win_impact_together": {
                p.friend_player_id: round(p.avg_round_win_impact_together) for p in eligible
            },
            "clutches_together": {p.friend_player_id: p.clutches_together for p in eligible},
            "traded_together": {p.friend_player_id: p.traded_together for p in eligible},
            "rounds_together": {p.friend_player_id: p.rounds_together for p in eligible},
            "kill_differential_together": {p.friend_player_id: p.kill_differential_together for p in eligible},
        }
        shoutouts = assign_shoutouts(roster, raw_dicts, {}, categories=SQUAD_SHOUTOUT_CATEGORIES)

    return SquadOverview(
        squad_size=len(pairs),
        total_matches_together=len(viewer_match_ids),
        total_rounds_together=total_rounds_together,
        pairs=pairs,
        shoutouts=shoutouts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_squad.py -v`
Expected: PASS (4 tests). If `test_friend_below_threshold_appears_in_table_but_not_shoutouts` fails because `assign_shoutouts`'s fallback logic (Highlight Reel / anchor) still assigns *something* to a sub-threshold player when they're the only one in `roster` -- note the test only puts the *eligible* list into `roster`, so a below-threshold friend is never a shoutout candidate at all (not even via fallback). Re-check the assertion reads correctly for that case; simplify to `assert overview.shoutouts == []` if the roster is empty (no eligible friends), which it is in this test since 19 < 20.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/squad.py webapp/tests/test_squad.py
git commit -m "Add pure squad pair-stats aggregator"
```

---

### Task 3: DB-facing `get_squad_overview` (fetch + adapt to the pure aggregator)

**Files:**
- Modify: `webapp/app/services/squad.py` (add to the file from Task 2)

**Interfaces:**
- Consumes: `app.models.{ImpactScore, Match, MatchPlayer, Player, Round, RoundPlayerStat}`, `app.services.friends.list_friend_ids`, `build_squad_overview`/`SharedRound` from Task 2.
- Produces: `get_squad_overview(db: Session, viewer_player_id: int, match_limit: int | None) -> SquadOverview` — used by Task 4's router.

This task has no isolated unit test (it's a DB-integration function, same as `get_player_profile`/`get_session_stats` which also have none) -- it's covered by Task 4's manual verification. Follow this exact structure:

- [ ] **Step 1: Add imports and the `_winner_side` helper to `squad.py`**

```python
from sqlalchemy.orm import Session, selectinload

from app.models import ImpactScore, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.services.friends import list_friend_ids


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None
```

- [ ] **Step 2: Add the per-match clutch/round builder**

This replays one match's kill events once (regardless of how many friends were teammates in it) to get, per round, which of the viewer's own teammates (by `match_player_id`) resolved a clutch -- mirrors `app/services/session_stats.py`'s `_build_replay_stats` clutch logic, scoped down to a single match and a single side.

```python
def _clutch_resolvers_by_round(db: Session, match_id: int, own_mp_ids: set[int]) -> dict[int, set[int]]:
    """round_number -> set of own_mp_ids that were part of a resolved clutch
    (round won while at 1-or-2-alive against an equal-or-larger enemy side)
    in that round, for the single match `match_id`.
    """
    all_match_players = db.query(MatchPlayer).filter_by(match_id=match_id).all()
    opp_mp_ids = {mp.id for mp in all_match_players if mp.id not in own_mp_ids}

    resolvers: dict[int, set[int]] = {}
    rounds = (
        db.query(Round)
        .filter_by(match_id=match_id)
        .options(selectinload(Round.kill_events))
        .order_by(Round.round_number)
        .all()
    )
    for round_row in rounds:
        won = _winner_side(round_row.outcome) is not None and (
            db.query(MatchPlayer.team)
            .filter(MatchPlayer.id.in_(own_mp_ids))
            .first()
        )
        alive_own = set(own_mp_ids)
        alive_opp = set(opp_mp_ids)
        clutch_state: tuple[int, int, frozenset[int]] | None = None

        events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))
        for event in events:
            death_id = event.death_match_player_id
            alive_own.discard(death_id)
            alive_opp.discard(death_id)
            own_count, opp_count = len(alive_own), len(alive_opp)
            if own_count in (1, 2) and opp_count >= own_count:
                if clutch_state is None or own_count < clutch_state[0]:
                    clutch_state = (own_count, opp_count, frozenset(alive_own))
            if own_count == 0 or opp_count == 0:
                break

        if clutch_state is not None and _winner_side(round_row.outcome) is not None:
            _, _, alive_snapshot = clutch_state
            resolvers[round_row.round_number] = set(alive_snapshot)

    return resolvers
```

Note: the `won` local variable above is unused scaffolding from an earlier draft -- **delete that line**; the real "did our side win this round" check is `_winner_side(round_row.outcome) == viewer_team`, computed by the caller (Step 3) which already knows `viewer_team` for this match. Simplify `_clutch_resolvers_by_round` to only return clutch snapshots gated on `_winner_side(round_row.outcome) is not None`; let the caller intersect with "did we actually win" using its own known `viewer_team`, OR (simpler) pass `viewer_team` into this helper directly:

```python
def _clutch_resolvers_by_round(
    db: Session, match_id: int, own_mp_ids: set[int], own_team: str
) -> dict[int, set[int]]:
    all_match_players = db.query(MatchPlayer).filter_by(match_id=match_id).all()
    opp_mp_ids = {mp.id for mp in all_match_players if mp.id not in own_mp_ids}

    resolvers: dict[int, set[int]] = {}
    rounds = (
        db.query(Round)
        .filter_by(match_id=match_id)
        .options(selectinload(Round.kill_events))
        .order_by(Round.round_number)
        .all()
    )
    for round_row in rounds:
        alive_own = set(own_mp_ids)
        alive_opp = set(opp_mp_ids)
        clutch_state: tuple[int, int, frozenset[int]] | None = None

        events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))
        for event in events:
            death_id = event.death_match_player_id
            alive_own.discard(death_id)
            alive_opp.discard(death_id)
            own_count, opp_count = len(alive_own), len(alive_opp)
            if own_count in (1, 2) and opp_count >= own_count:
                if clutch_state is None or own_count < clutch_state[0]:
                    clutch_state = (own_count, opp_count, frozenset(alive_own))
            if own_count == 0 or opp_count == 0:
                break

        if clutch_state is not None and _winner_side(round_row.outcome) == own_team:
            _, _, alive_snapshot = clutch_state
            resolvers[round_row.round_number] = set(alive_snapshot)

    return resolvers
```

(Use this final version -- it replaces the draft above; there is only one `_clutch_resolvers_by_round` function in the finished file.)

- [ ] **Step 3: Add `get_squad_overview`**

```python
def get_squad_overview(db: Session, viewer_player_id: int, match_limit: int | None) -> SquadOverview:
    friend_ids = list_friend_ids(db, viewer_player_id)
    friend_names = {
        p.id: p.display_name
        for p in db.query(Player).filter(Player.id.in_(friend_ids)).all()
    } if friend_ids else {}

    viewer_query = (
        db.query(MatchPlayer)
        .filter_by(player_id=viewer_player_id)
        .join(Match, Match.id == MatchPlayer.match_id)
    )
    if match_limit is not None:
        viewer_mps = list(
            reversed(
                viewer_query.order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
                .limit(match_limit)
                .all()
            )
        )
    else:
        viewer_mps = viewer_query.order_by(Match.played_at.nullslast(), Match.id).all()

    viewer_match_ids = {mp.match_id for mp in viewer_mps}
    viewer_mp_by_match: dict[int, MatchPlayer] = {mp.match_id: mp for mp in viewer_mps}

    if not friend_ids or not viewer_match_ids:
        return build_squad_overview({}, {}, {}, viewer_match_ids)

    # Every friend teammate (same match, same team as the viewer) across the viewer's window.
    all_teammates = (
        db.query(MatchPlayer)
        .filter(
            MatchPlayer.match_id.in_(viewer_match_ids),
            MatchPlayer.player_id.in_(friend_ids),
        )
        .all()
    )
    friend_mps_by_match: dict[int, list[MatchPlayer]] = {}
    for mp in all_teammates:
        viewer_mp = viewer_mp_by_match.get(mp.match_id)
        if viewer_mp is None or mp.team != viewer_mp.team:
            continue
        friend_mps_by_match.setdefault(mp.match_id, []).append(mp)

    relevant_match_ids = list(friend_mps_by_match.keys())
    if not relevant_match_ids:
        return build_squad_overview({}, {}, {}, viewer_match_ids)

    pair_shared_rounds: dict[int, list[SharedRound]] = {}
    friend_agent_counts: dict[int, Counter] = {}

    for match_id in relevant_match_ids:
        viewer_mp = viewer_mp_by_match[match_id]
        viewer_team = viewer_mp.team.value if hasattr(viewer_mp.team, "value") else viewer_mp.team
        friend_mps = friend_mps_by_match[match_id]
        friend_mp_ids = {mp.id for mp in friend_mps}
        own_mp_ids = {viewer_mp.id} | friend_mp_ids

        own_relevant_ids = own_mp_ids
        clutch_resolvers = _clutch_resolvers_by_round(db, match_id, own_relevant_ids, viewer_team)

        rounds_by_number = {
            r.round_number: r
            for r in db.query(Round).filter_by(match_id=match_id).all()
        }

        impact_rows = (
            db.query(ImpactScore, Round.round_number)
            .join(Round, Round.id == ImpactScore.round_id)
            .filter(
                Round.match_id == match_id,
                ImpactScore.match_player_id.in_(own_mp_ids),
            )
            .all()
        )
        impact_by_mp_and_round: dict[tuple[int, int], ImpactScore] = {
            (score.match_player_id, round_number): score for score, round_number in impact_rows
        }

        kda_rows = (
            db.query(RoundPlayerStat, Round.round_number)
            .join(Round, Round.id == RoundPlayerStat.round_id)
            .filter(
                Round.match_id == match_id,
                RoundPlayerStat.match_player_id.in_(own_mp_ids),
            )
            .all()
        )
        kda_by_mp_and_round: dict[tuple[int, int], RoundPlayerStat] = {
            (stat.match_player_id, round_number): stat for stat, round_number in kda_rows
        }

        for friend_mp in friend_mps:
            for round_number, round_row in rounds_by_number.items():
                viewer_score = impact_by_mp_and_round.get((viewer_mp.id, round_number))
                friend_score = impact_by_mp_and_round.get((friend_mp.id, round_number))
                if viewer_score is None or friend_score is None:
                    continue  # one of them sat out this round (rare, but possible)

                won = _winner_side(round_row.outcome) == viewer_team
                viewer_rwi = (viewer_score.kill_impact if won else 0.0) - viewer_score.death_impact
                friend_rwi = (friend_score.kill_impact if won else 0.0) - friend_score.death_impact

                resolvers = clutch_resolvers.get(round_number, set())
                clutch = viewer_mp.id in resolvers or friend_mp.id in resolvers

                viewer_breakdown = viewer_score.breakdown or {}
                friend_breakdown = friend_score.breakdown or {}
                traded = viewer_breakdown.get("traded_teammate_targets", {}).get(str(friend_mp.id), 0)
                traded += friend_breakdown.get("traded_teammate_targets", {}).get(str(viewer_mp.id), 0)

                viewer_kda = kda_by_mp_and_round.get((viewer_mp.id, round_number))
                friend_kda = kda_by_mp_and_round.get((friend_mp.id, round_number))

                pair_shared_rounds.setdefault(friend_mp.player_id, []).append(
                    SharedRound(
                        match_id=match_id,
                        won=won,
                        viewer_round_win_impact=viewer_rwi,
                        friend_round_win_impact=friend_rwi,
                        clutch=clutch,
                        traded=traded,
                        viewer_kills=viewer_kda.kills if viewer_kda else 0,
                        viewer_deaths=viewer_kda.deaths if viewer_kda else 0,
                        friend_kills=friend_kda.kills if friend_kda else 0,
                        friend_deaths=friend_kda.deaths if friend_kda else 0,
                    )
                )
            friend_agent_counts.setdefault(friend_mp.player_id, Counter())[friend_mp.agent] += 1

    return build_squad_overview(pair_shared_rounds, friend_names, friend_agent_counts, viewer_match_ids)
```

Note the `traded_teammate_targets` dict keys are stored as strings (JSON round-trip) per the existing pattern in `app/services/matches.py:166` (`int(teammate_id)`); this reads them with `str(friend_mp.id)` matching how they're written in `app/scoring/impact.py` -- verify this key type against `app/scoring/impact.py`'s actual write path before wiring this up, adjusting to `int(...)` keys if breakdown dicts are stored with int keys instead (JSON only supports string keys, but check whether the ORM layer is already normalizing on read, as `app/services/matches.py:166` already does via `int(teammate_id)` — mirror whichever convention is confirmed there).

- [ ] **Step 4: Sanity-check with a manual Python shell against local data**

Run (from `webapp/`, local Postgres up and migrated):
```
.\.venv\Scripts\python.exe -c "
from app.db import SessionLocal
from app.services.squad import get_squad_overview
from app.models import Player
db = SessionLocal()
me = db.query(Player).filter(Player.display_name.ilike('%')).first()
overview = get_squad_overview(db, me.id, match_limit=30)
print(overview.squad_size, overview.total_matches_together, overview.total_rounds_together)
for p in overview.pairs[:5]:
    print(p)
"
```
Expected: runs without exceptions; pair numbers look sane (rounds_together <= matches_together * ~24, win_rate in [0,1]).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/squad.py
git commit -m "Add DB-facing get_squad_overview"
```

---

### Task 4: Router + wiring

**Files:**
- Create: `webapp/app/routers/squad.py`
- Modify: `webapp/app/main.py`
- Modify: `webapp/app/templates/base.html`

**Interfaces:**
- Consumes: `get_squad_overview` from Task 3.
- Produces: `GET /squad`, `GET /squad/career` routes rendering `squad/detail.html` / `squad/_squad_sections.html` (built in Task 5).

- [ ] **Step 1: Write `app/routers/squad.py`**

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.squad import get_squad_overview
from app.templates import templates

router = APIRouter(prefix="/squad", tags=["squad"])

RECENT_MATCH_LIMIT = 30


@router.get("")
def squad_page(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    overview = get_squad_overview(db, current_player.id, match_limit=RECENT_MATCH_LIMIT)
    return templates.TemplateResponse(
        request, "squad/detail.html", {"overview": overview, "scope": "recent"}
    )


@router.get("/career")
def squad_career_fragment(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    overview = get_squad_overview(db, current_player.id, match_limit=None)
    return templates.TemplateResponse(
        request, "squad/_squad_sections.html", {"overview": overview, "scope": "career"}
    )
```

- [ ] **Step 2: Register the router in `app/main.py`**

Change:

```python
from app.routers import auth, friends, map_prediction, matches, players, sessions
```

to:

```python
from app.routers import auth, friends, map_prediction, matches, players, sessions, squad
```

and add, after `app.include_router(sessions.router)`:

```python
app.include_router(squad.router)
```

- [ ] **Step 3: Add the nav link in `app/templates/base.html`**

Change:

```html
      {% if current_player %}
      <a href="/search">Search</a>
      <a href="/friends">Friends</a>
      <a href="/map-prediction">Map Prediction</a>
      {% endif %}
```

to:

```html
      {% if current_player %}
      <a href="/search">Search</a>
      <a href="/friends">Friends</a>
      <a href="/squad">Squad</a>
      <a href="/map-prediction">Map Prediction</a>
      {% endif %}
```

- [ ] **Step 4: Verify the app still starts (templates for squad don't exist yet, so `/squad` itself will 500 until Task 5 -- just confirm no import/wiring errors)**

Run (from `webapp/`): `.\.venv\Scripts\python.exe -c "import app.main"`
Expected: no exception (import-time only; route bodies aren't executed).

- [ ] **Step 5: Commit**

```bash
git add webapp/app/routers/squad.py webapp/app/main.py webapp/app/templates/base.html
git commit -m "Wire up the squad router and nav link"
```

---

### Task 5: Templates

**Files:**
- Create: `webapp/app/templates/squad/detail.html`
- Create: `webapp/app/templates/squad/_squad_sections.html`

**Interfaces:**
- Consumes: `overview: SquadOverview` (from Task 3/4), `scope: "recent" | "career"`.

- [ ] **Step 1: `app/templates/squad/detail.html`**

```html
{% extends "base.html" %}

{% block title %}Squad - ValoWithFriendsTracker{% endblock %}

{% block content %}
<h1>Squad</h1>
<p class="page-meta">Who you play with most, and who you play best with -- scoped to your friends list.</p>

<div class="scope-toggle">
  <button type="button" class="scope-toggle-btn active" data-scope="recent">Recent 30</button>
  <button type="button" class="scope-toggle-btn" data-scope="career">Career</button>
</div>

<div id="recent-sections">
  {% include "squad/_squad_sections.html" %}
</div>

<div id="career-sections" style="display:none;"
     hx-get="/squad/career"
     hx-trigger="load"
     hx-swap="innerHTML">
  <p class="page-meta htmx-indicator">Loading career stats&hellip;</p>
</div>

<script>
  document.querySelectorAll(".scope-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".scope-toggle-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("recent-sections").style.display = btn.dataset.scope === "recent" ? "" : "none";
      document.getElementById("career-sections").style.display = btn.dataset.scope === "career" ? "" : "none";
    });
  });
</script>
{% endblock %}
```

- [ ] **Step 2: `app/templates/squad/_squad_sections.html`**

```html
<p class="page-meta">
  {% if scope == 'recent' %}Stats below are over your last 30 matches.{% else %}Stats below are over your full match history.{% endif %}
</p>

<div class="stat-grid">
  <div class="stat-tile">
    <div class="stat-label">Squad size</div>
    <div class="stat-value">{{ overview.squad_size }}</div>
  </div>
  <div class="stat-tile">
    <div class="stat-label">Matches{% if scope == 'recent' %} (last 30){% endif %}</div>
    <div class="stat-value">{{ overview.total_matches_together }}</div>
  </div>
  <div class="stat-tile">
    <div class="stat-label">Rounds played with friends</div>
    <div class="stat-value">{{ overview.total_rounds_together }}</div>
  </div>
</div>

{% if overview.shoutouts %}
<div class="card">
  <h2>Shoutouts</h2>
  <p class="page-meta">One highlight per friend with at least 20 rounds played together.</p>
  <div class="shoutout-rows">
    {% for row in overview.shoutouts | balanced_rows %}
    <div class="shoutout-row">
      {% for shoutout in row %}
      {% set icon = agent_icon_slug(shoutout.agent) %}
      <div class="shoutout-card">
        <div class="shoutout-portrait-frame">
          {% if icon %}
          <img class="shoutout-portrait" src="/static/img/agents/{{ icon }}.png" alt="{{ shoutout.agent }}">
          {% else %}
          <div class="shoutout-portrait shoutout-portrait--fallback">{{ (shoutout.agent or "?")[:1] }}</div>
          {% endif %}
        </div>
        <div class="shoutout-name"><a href="/players/{{ shoutout.display_name | urlencode }}">{{ shoutout.display_name | strip_tag }}</a></div>
        {% if shoutout.agent %}
        <div class="shoutout-agent">{{ shoutout.agent }}</div>
        {% endif %}
        <div class="shoutout-headline">{{ shoutout.headline }}</div>
        <div class="shoutout-detail">{{ shoutout.detail }}</div>
      </div>
      {% endfor %}
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}

<div class="card">
  <h2>Your friends, ranked</h2>
  {% if overview.pairs %}
  <table>
    <thead>
      <tr>
        <th data-sort="string">Player</th>
        <th data-sort="number" aria-sort="descending">Matches Together</th>
        <th data-sort="number">Rounds Together</th>
        <th data-sort="number">Win Rate Together</th>
        <th data-sort="number">Avg Round-Win Impact Together</th>
        <th data-sort="number">Clutches Together</th>
        <th data-sort="number">Traded Together</th>
        <th data-sort="number">Kill Differential Together</th>
      </tr>
    </thead>
    <tbody>
      {% for p in overview.pairs %}
      <tr>
        <td><a href="/players/{{ p.display_name | urlencode }}">{{ p.display_name | strip_tag }}</a></td>
        <td data-sort-value="{{ p.matches_together }}">{{ p.matches_together }}</td>
        <td data-sort-value="{{ p.rounds_together }}">{{ p.rounds_together }}</td>
        <td data-sort-value="{{ p.win_rate_together }}">{{ (p.win_rate_together * 100) | round | int }}%</td>
        <td data-sort-value="{{ p.avg_round_win_impact_together }}" class="{{ 'impact-positive' if p.avg_round_win_impact_together >= 0 else 'impact-negative' }}">
          {{ p.avg_round_win_impact_together | round | int }}
        </td>
        <td data-sort-value="{{ p.clutches_together }}">{{ p.clutches_together }}</td>
        <td data-sort-value="{{ p.traded_together }}">{{ p.traded_together }}</td>
        <td data-sort-value="{{ p.kill_differential_together }}">{{ p.kill_differential_together }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="page-meta">No shared matches with friends yet{% if scope == 'recent' %} in your last 30 matches{% endif %}.</p>
  {% endif %}
</div>
```

- [ ] **Step 3: Manual verification (no automated template test harness exists in this repo)**

Run locally per `CLAUDE.md`:
```
cd webapp
docker compose -p valomaths-private up -d
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```
Then in a browser, logged in as a player with a real friend list and shared match history:
- [ ] `/squad` loads, shows correct squad size / matches / rounds tiles, ranked table sorted by Matches Together descending, sortable via column headers.
- [ ] Shoutouts card appears with sensible headlines (or is absent if no friend clears 20 rounds together).
- [ ] Switching to "Career" lazily loads via htmx and shows a (usually larger) all-time view.
- [ ] A viewer with friends but zero shared matches shows the pairs-empty state, not an error.
- [ ] A viewer with no friends at all shows squad_size 0 and the empty state, not an error.
- [ ] Nav bar shows the "Squad" link only when logged in.

- [ ] **Step 4: Commit**

```bash
git add webapp/app/templates/squad/
git commit -m "Add squad page templates"
```

---

## Self-Review Notes

- Spec coverage: friends-only scope (Task 3's `list_friend_ids` + team-match filter), one ranked list not a social graph (Task 2/3 aggregate only viewer+friend pairs), round-win-impact-together as sum of both own win-gated impacts (Task 2's `avg_round_win_impact_together`), 20-round shoutout threshold (`SQUAD_ROUND_THRESHOLD`), `assign_shoutouts` generalization (Task 1), Recent 30/Career htmx pattern (Task 4/5), sortable table (Task 5), nav link (Task 4), manual verification checklist (Task 5) -- all covered.
- Task 3's `traded_teammate_targets` key-type caveat is flagged explicitly rather than guessed at, since it depends on how `app/scoring/impact.py` actually serializes that dict -- confirm against that file during implementation before trusting the `str(...)` lookup.
