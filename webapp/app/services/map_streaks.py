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


def _act_pools(*args, **kwargs):
    raise NotImplementedError


def _gap_in_window(*args, **kwargs):
    raise NotImplementedError


def _build_map_streaks(*args, **kwargs):
    raise NotImplementedError
