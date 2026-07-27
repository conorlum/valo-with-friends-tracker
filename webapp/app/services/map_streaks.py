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
) -> tuple[int, bool] | None:
    """Longest run of consecutive matches (within [window_start, window_end))
    that aren't `map_name`. Returns None if the player has no matches in
    range at all -- nothing to measure. `ongoing` is True only when the
    window is still open (`window_end is None`) and the longest run found is
    the trailing one (i.e. it's still accruing, not already closed off by a
    later play of the map)."""
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

    ongoing = window_end is None and running == best and best > 0
    return best, ongoing


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
        window_results: list[tuple[int, MapStreakWindow]] = []
        for w_start, w_end in _map_windows(map_name, acts, act_pools):
            outcome = _gap_in_window(acts[w_start].start, acts[w_end].end, map_name, player_matches)
            if outcome is None:
                continue
            gap, ongoing = outcome
            act_labels = [acts[i].label for i in range(w_start, w_end + 1)]
            window_results.append((w_end, MapStreakWindow(act_labels=act_labels, gap=gap, ongoing=ongoing)))

        current_result = next((w for idx, w in window_results if idx == current_act_index), None)
        if current_result is None:
            continue  # no matches yet in the current act -- omit (page-level empty state covers this)

        record_idx, record_result = max(window_results, key=lambda pair: pair[1].gap)
        record_is_current = record_idx == current_act_index and record_result.gap == current_result.gap

        streaks.append(
            MapStreak(
                map_name=map_name,
                current=current_result,
                record=record_result,
                record_is_current=record_is_current,
            )
        )

    return streaks
