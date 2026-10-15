"""Map streaks: the longest number of a player's matches without seeing a map
while that map is in the map pool.

Rules (R1): a match on the map resets the run to 0, always -- even when the map
is listed out of the pool at that time (a play is a play). A match on another
map while the map is in the pool adds 1. A match on another map while the map
is out of the pool is skipped: it neither counts nor resets, so the run carries
on when the map returns. Pool membership comes from app.services.map_pool.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer
from app.services.map_pool import PoolHistory, load_pool_history


@dataclass
class MapStreakWindow:
    gap: int
    first_at: datetime | None  # first / last counted match of the run; None when gap is 0
    last_at: datetime | None


@dataclass
class MapStreak:
    map_name: str
    current: MapStreakWindow
    record: MapStreakWindow
    record_is_current: bool


def _window(gap: int, first_at: datetime | None, last_at: datetime | None) -> MapStreakWindow:
    if gap == 0:
        return MapStreakWindow(0, None, None)
    return MapStreakWindow(gap, first_at, last_at)


def build_map_streaks(
    history: PoolHistory,
    player_matches: list[tuple[str, datetime]],
    now: datetime | None = None,
) -> list[MapStreak]:
    """player_matches must be in time order."""
    streaks: list[MapStreak] = []
    for map_name in sorted(history.current_pool(now)):
        seen = False  # any match in the pool, or on the map
        gap = 0
        first_at: datetime | None = None
        last_at: datetime | None = None
        best = (0, None, None)
        for m, t in player_matches:
            if m == map_name:
                seen = True
                gap, first_at, last_at = 0, None, None
            elif history.in_pool(map_name, t):
                seen = True
                if gap == 0:
                    first_at = t
                gap += 1
                last_at = t
                if gap >= best[0]:  # >= : the latest run wins a tie
                    best = (gap, first_at, last_at)
        if not seen:
            continue
        current = _window(gap, first_at, last_at)
        record = _window(*best)
        streaks.append(
            MapStreak(
                map_name=map_name,
                current=current,
                record=record,
                record_is_current=current.gap >= record.gap and current.gap > 0,
            )
        )
    return streaks


def compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]:
    """For every map in the current pool, the player's current and record run
    of matches without it, counting only matches played while it was in the pool."""
    history = load_pool_history(db)
    player_matches = list(
        db.query(Match.map_name, Match.played_at)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id, Match.played_at.isnot(None))
        .order_by(Match.played_at)
        .all()
    )
    return build_map_streaks(history, player_matches)
