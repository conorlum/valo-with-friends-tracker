import json
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


def compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]:
    """For every map in the current competitive pool, the player's current
    and all-time-record longest run of consecutive matches without playing
    it -- see the design doc at
    docs/superpowers/specs/2026-07-27-map-play-streaks-design.md for the
    per-map windowing rationale (map pool rotates 2-3 maps per act, so
    continuity is checked per map, not per whole pool)."""
    acts = _load_acts()
    if not acts:
        return []

    population_matches = list(
        db.query(Match.map_name, Match.played_at).filter(Match.played_at.isnot(None)).all()
    )
    act_pools = _act_pools(acts, population_matches)

    player_matches = list(
        db.query(Match.map_name, Match.played_at)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id, Match.played_at.isnot(None))
        .order_by(Match.played_at)
        .all()
    )

    return _build_map_streaks(acts, act_pools, player_matches)
