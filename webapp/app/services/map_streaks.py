import json
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer
from app.services.map_prediction import MIN_POOL_MATCHES, SEASON_ACTS_PATH


@dataclass
class Act:
    label: str
    start: datetime
    end: datetime | None  # None only for the current (latest) act


@dataclass
class MapStreakWindow:
    act_labels: list[str]
    gap: int
    ongoing: bool


@dataclass
class MapStreak:
    map_name: str
    current: MapStreakWindow
    record: MapStreakWindow
    record_is_current: bool


def _map_windows(map_name: str, acts: list[Act], act_pools: list[set[str]]) -> list[tuple[int, int]]:
    """Contiguous runs of act-indices where `map_name` is in that act's pool.
    Only adjacent acts merge -- a gap where the map drops out starts a new,
    disjoint window."""
    windows: list[tuple[int, int]] = []
    start: int | None = None
    for i, pool in enumerate(act_pools):
        present = map_name in pool
        if present and start is None:
            start = i
        elif not present and start is not None:
            windows.append((start, i - 1))
            start = None
    if start is not None:
        windows.append((start, len(acts) - 1))
    return windows


def _act_pools(acts: list[Act], population_matches: list[tuple[str, datetime]]) -> list[set[str]]:
    """Population-wide map pool per act -- same MIN_POOL_MATCHES rule as
    map_prediction.get_current_map_pool, applied to every act instead of
    just the latest one."""
    counts_per_act: list[dict[str, int]] = [dict() for _ in acts]
    for map_name, played_at in population_matches:
        if map_name is None or played_at is None:
            continue
        for i, act in enumerate(acts):
            if played_at >= act.start and (act.end is None or played_at < act.end):
                counts_per_act[i][map_name] = counts_per_act[i].get(map_name, 0) + 1
                break
    return [{m for m, c in counts.items() if c >= MIN_POOL_MATCHES} for counts in counts_per_act]


def _gap_in_window(
    window_start: datetime,
    window_end: datetime | None,
    map_name: str,
    player_matches: list[tuple[str, datetime]],
) -> tuple[int, int] | None:
    """Returns (best, trailing): `best` is the longest run of consecutive
    matches without playing `map_name` anywhere in the window; `trailing` is
    the run counted from the last play of the map (or window start) up to
    the most recent match -- the live, still-accruing streak. Returns None
    if the player has no matches in range at all -- nothing to measure."""
    in_window = [
        (m, t) for m, t in player_matches if t >= window_start and (window_end is None or t < window_end)
    ]
    if not in_window:
        return None

    best = 0
    running = 0
    for m, _ in in_window:
        if m == map_name:
            running = 0
        else:
            running += 1
            best = max(best, running)

    return best, running


def _build_map_streaks(
    acts: list[Act],
    act_pools: list[set[str]],
    player_matches: list[tuple[str, datetime]],
) -> list[MapStreak]:
    if not acts:
        return []

    current_pool = act_pools[-1]
    current_act_index = len(acts) - 1
    streaks: list[MapStreak] = []

    for map_name in sorted(current_pool):
        # (window end act-index, best, trailing, act_labels)
        window_results: list[tuple[int, int, int, list[str]]] = []
        for w_start, w_end in _map_windows(map_name, acts, act_pools):
            outcome = _gap_in_window(acts[w_start].start, acts[w_end].end, map_name, player_matches)
            if outcome is None:
                continue
            best, trailing = outcome
            act_labels = [acts[i].label for i in range(w_start, w_end + 1)]
            window_results.append((w_end, best, trailing, act_labels))

        current_entry = next((w for w in window_results if w[0] == current_act_index), None)
        if current_entry is None:
            continue  # no matches yet in the current act -- omit (page-level empty state covers this)

        _, _, current_trailing, current_labels = current_entry
        current = MapStreakWindow(act_labels=current_labels, gap=current_trailing, ongoing=True)

        record_idx, record_best, record_trailing, record_labels = max(window_results, key=lambda w: w[1])
        record_ongoing = record_idx == current_act_index and record_best == record_trailing and record_best > 0
        record = MapStreakWindow(act_labels=record_labels, gap=record_best, ongoing=record_ongoing)

        record_is_current = record_idx == current_act_index and record.gap == current.gap

        streaks.append(
            MapStreak(
                map_name=map_name,
                current=current,
                record=record,
                record_is_current=record_is_current,
            )
        )

    return streaks


def _load_acts() -> list[Act]:
    if not SEASON_ACTS_PATH.exists():
        return []
    acts_raw = sorted(
        json.loads(SEASON_ACTS_PATH.read_text(encoding="utf-8")), key=lambda a: a["start_time"]
    )
    acts: list[Act] = []
    for i, a in enumerate(acts_raw):
        start = datetime.fromisoformat(a["start_time"])
        end = datetime.fromisoformat(acts_raw[i + 1]["start_time"]) if i + 1 < len(acts_raw) else None
        acts.append(Act(label=a["label"], start=start, end=end))
    return acts


# Step 4 (docs/player_page_render_speed.txt): _act_pools scans EVERY Match in
# the DB and is IDENTICAL for every player -- it doesn't belong in a
# per-player cache row, but a naive per-request MAX(Match.id) freshness probe
# still leaves this at two serial statements (a big query traded for a small
# one) and is incomplete invalidation besides (misses corrections to an
# existing match, deletions, and season_acts.json edits -- none of which are
# in the DB at all). A process-level memo with a short TTL and NO per-request
# probe achieves fewer rows, less CPU, AND fewer round trips: on a cache hit
# this costs zero queries, not one. Bounded staleness (population pools can
# lag an ingest by up to the TTL) is accepted deliberately -- streaks only
# move when a match is ingested, a manual/batch event on this local-only
# site, not a live one.
_ACT_POOLS_TTL_SECONDS = 300


@dataclass
class _ActPoolsCacheEntry:
    acts: list[Act]
    act_pools: list[set[str]]
    computed_at_monotonic: float
    season_acts_mtime: float


_act_pools_cache: _ActPoolsCacheEntry | None = None


def _season_acts_mtime() -> float:
    try:
        return SEASON_ACTS_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _get_acts_and_pools(db: Session) -> tuple[list[Act], list[set[str]]]:
    """Cached (acts, act_pools) pair -- see _ACT_POOLS_TTL_SECONDS above.
    Keyed on season_acts.json's mtime (included per Step 4's explicit
    instruction) so an edit to that file is picked up on the very next
    request rather than waiting out the TTL, even though the TTL alone would
    eventually catch it too."""
    global _act_pools_cache
    now = time.monotonic()
    mtime = _season_acts_mtime()
    cached = _act_pools_cache
    if (
        cached is not None
        and cached.season_acts_mtime == mtime
        and (now - cached.computed_at_monotonic) < _ACT_POOLS_TTL_SECONDS
    ):
        return cached.acts, cached.act_pools

    acts = _load_acts()
    act_pools: list[set[str]] = []
    if acts:
        population_matches = list(
            db.query(Match.map_name, Match.played_at).filter(Match.played_at.isnot(None)).all()
        )
        act_pools = _act_pools(acts, population_matches)

    _act_pools_cache = _ActPoolsCacheEntry(
        acts=acts, act_pools=act_pools, computed_at_monotonic=now, season_acts_mtime=mtime,
    )
    return acts, act_pools


def compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]:
    """For every map in the current competitive pool, the player's current
    and all-time-record longest run of consecutive matches without playing
    it -- see the design doc at
    docs/superpowers/specs/2026-07-27-map-play-streaks-design.md for the
    per-map windowing rationale (map pool rotates 2-3 maps per act, so
    continuity is checked per map, not per whole pool).

    The population-wide half of this (acts + act_pools) is identical for
    every player and process-memoized -- see _get_acts_and_pools (Step 4)."""
    acts, act_pools = _get_acts_and_pools(db)
    if not acts:
        return []

    player_matches = list(
        db.query(Match.map_name, Match.played_at)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id, Match.played_at.isnot(None))
        .order_by(Match.played_at)
        .all()
    )

    return _build_map_streaks(acts, act_pools, player_matches)
