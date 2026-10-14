"""The Competitive map pool over time: which maps were in the queue at a given moment.

Pure code (no network, no DB; a later item adds a loader).

- `Change`: one map going IN or OUT of the pool at `effective_at` (`source` is "seed", "patch_notes" or
  "safety_net").
- `PoolHistory`: the sorted, de-duplicated changes, with `pool_at(t)`, `current_pool()`, `intervals(map)`,
  `in_pool(map, t)` and `latest_change_at()`. A map with no change at or before `t` is out.
- `PoolHistory.with_safety_net(matches)`: a new history in which a map that was played at least
  `SAFETY_NET_MIN_MATCHES` times inside one out-of-pool interval (ignoring the first `BLEED_DAYS` after the OUT
  change, when games still bleed over) is put back in from the first such match. It never adds an OUT.

Standard library only. Map names are title case, as in `matches.map_name`. Times are timezone-aware UTC;
naive datetimes are taken to be UTC.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

BLEED_DAYS = 3  # PROVISIONAL(D3)
SAFETY_NET_MIN_MATCHES = 3  # PROVISIONAL(D3)

SAFETY_NET_PATCH = "safety-net"
SAFETY_NET_SOURCE = "safety_net"


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


@dataclass(frozen=True)
class Change:
    map_name: str
    in_pool: bool
    effective_at: datetime
    patch: str
    source: str  # "seed" | "patch_notes" | "safety_net"

    def __post_init__(self):
        object.__setattr__(self, "effective_at", _utc(self.effective_at))


class PoolHistory:
    def __init__(self, changes: Iterable[Change]):
        seen = set()
        kept = []
        for c in sorted(changes, key=lambda c: (_utc(c.effective_at), c.map_name)):
            key = (c.map_name, c.in_pool, c.effective_at)
            if key in seen:
                continue
            seen.add(key)
            kept.append(c)
        self.changes: tuple[Change, ...] = tuple(kept)

    def _for_map(self, map_name: str) -> list[Change]:
        return [c for c in self.changes if c.map_name == map_name]

    def pool_at(self, t: datetime) -> frozenset[str]:
        t = _utc(t)
        state: dict[str, bool] = {}
        for c in self.changes:
            if c.effective_at > t:
                break
            state[c.map_name] = c.in_pool
        return frozenset(m for m, v in state.items() if v)

    def current_pool(self, now: datetime | None = None) -> frozenset[str]:
        return self.pool_at(now if now is not None else datetime.now(timezone.utc))

    def in_pool(self, map_name: str, t: datetime) -> bool:
        return map_name in self.pool_at(t)

    def intervals(self, map_name: str) -> list[tuple[datetime, datetime | None]]:
        out: list[tuple[datetime, datetime | None]] = []
        start: datetime | None = None
        for c in self._for_map(map_name):
            if c.in_pool and start is None:
                start = c.effective_at
            elif not c.in_pool and start is not None:
                out.append((start, c.effective_at))
                start = None
        if start is not None:
            out.append((start, None))
        return out

    def latest_change_at(self, now: datetime | None = None, official_only: bool = True) -> datetime | None:
        now = _utc(now) if now is not None else datetime.now(timezone.utc)
        times = [
            c.effective_at
            for c in self.changes
            if c.effective_at <= now and not (official_only and c.source == SAFETY_NET_SOURCE)
        ]
        return max(times) if times else None

    def _out_intervals(self, map_name: str):
        """Out-of-pool stretches as (start, end, bleed_from): start None = since the beginning of time,
        end None = still open, bleed_from = the OUT change that began it (None when none did)."""
        res = []
        start: datetime | None = None
        anchor: datetime | None = None
        out = True
        for c in self._for_map(map_name):
            if c.in_pool and out:
                res.append((start, c.effective_at, anchor))
                out = False
            elif not c.in_pool and not out:
                out = True
                start = anchor = c.effective_at
        if out:
            res.append((start, None, anchor))
        return res

    def with_safety_net(self, matches: Iterable[tuple[str, datetime]]) -> "PoolHistory":
        by_map: dict[str, list[datetime]] = defaultdict(list)
        for name, played_at in matches:
            by_map[name].append(_utc(played_at))
        bleed = timedelta(days=BLEED_DAYS)
        added = []
        names = set(by_map)
        for name in sorted(names):
            times = sorted(by_map[name])
            for start, end, anchor in self._out_intervals(name):
                inside = [
                    t
                    for t in times
                    if (start is None or t >= start)
                    and (end is None or t < end)
                    and (anchor is None or t >= anchor + bleed)
                ]
                if len(inside) >= SAFETY_NET_MIN_MATCHES:
                    added.append(Change(name, True, inside[0], SAFETY_NET_PATCH, SAFETY_NET_SOURCE))
        return PoolHistory(list(self.changes) + added)
