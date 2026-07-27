# Map play streaks (longest stretch without playing a map)

**Status:** approved, ready for planning
**Date:** 2026-07-27

## Purpose

For each map in the *current* competitive map pool, show a player how long the
longest stretch was where they didn't play that map -- both the stretch still
in progress right now, and the longest one ever recorded for that map. This is
purely a descriptive stat (no prediction/model fitting involved, unlike
[[map_prediction_feature]]).

Works for any tracked player, not just the logged-in user -- same page-scoping
convention as the existing player detail page (`/players/{display_name}`).

## Background / constraints from map-prediction work

`app/services/map_prediction.py` already establishes the two building blocks
this reuses:

- `get_current_map_pool(db)` -- a map is "in the pool" for an act if it has
  >= `MIN_POOL_MATCHES` (3) population-wide matches within that act's date
  window. This same rule is reused per-act here, not just for the current act.
- `season_acts.json` (`webapp/scripts/season_acts.json`) -- act labels +
  `start_time`, used to bucket matches into act windows
  (`[act.start, next_act.start)`, and `[latest_act.start, now)` for the
  current act).

Real-world constraint confirmed with the user: the competitive map pool
changes by 2-3 maps *every* act -- so a generic "did the whole 7-map pool
stay identical" era-merge would almost never actually merge anything. The
design below instead checks continuity **per map**: did *this specific map*
stay in the pool across the act boundary, regardless of what else changed.

## Data flow

1. **Per-act pools.** One query pulls every match's `(map_name, played_at)`
   (population-wide, all players -- matches the existing pool-eligibility
   rule). Bucket into each act's `[start, end)` window (`end` = next act's
   `start_time`, or "now" for the latest act) and compute each act's pool set
   using the existing `MIN_POOL_MATCHES` rule. This is one query, not one
   query per act.
2. **Current pool.** The latest act's pool set is "the 7 maps" the page
   displays rows for.
3. **Per map, find its windows.** For each map in the current pool, walk the
   acts in chronological order and find which acts had that map in their
   pool. Merge *adjacent* acts (no gap in between) where the map was
   continuously present into a "window" -- `(start, end, act_labels)`, where
   `end` is `None` if the window includes the current (latest) act.
   - A map can have multiple disjoint windows over the site's history (e.g.
     in the pool for acts 1-2, dropped for act 3, back for act 4 -- that's
     two separate windows, never bridged).
4. **Per window, find two numbers.** Take the target player's own matches
   (`played_at` not null), chronologically ordered, restricted to the
   window's date range. Scan them, tracking a running count of consecutive
   matches where the map wasn't played (a play of the map resets the running
   count to 0; a match on any other map -- pool or not -- increments it).
   Record two values from the scan: `best` (the highest the running count
   ever reached anywhere in the window) and `trailing` (the running count's
   final value, i.e. the live streak counted from the last play up through
   the player's most recent match in the window).
   - If the player has zero matches in a window, that window contributes no
     data point (skip it -- nothing to measure).
   - If the player never played the map at all within a window, `best` and
     `trailing` are both simply the count of all their matches in that
     window.
5. **Per map, report two numbers -- `current` and `record` are genuinely
   different quantities, not just two labels on the same scan result:**
   - **Current gap** -- `trailing` from the window that contains the current
     act (every current-pool map has exactly one such window, since it's in
     the pool by construction). This is deliberately the *live* number --
     "how long has it been since I played this map" -- not necessarily that
     window's historical `best`. Earlier drafts of this design conflated the
     two (labeling `best` as "current"), which silently mislabeled a closed,
     already-surpassed historical run as if it were still accruing; caught
     during implementation review by running the real algorithm against real
     match data, where it produced exactly that on 6 of 7 rows.
   - **Record gap** -- the max `best` across *all* of that map's windows
     (including the current one), with the act-label range of whichever
     window produced it. Flagged `ongoing` only when that maximizing window
     is the current (open) one *and* its `best == trailing` -- i.e. the
     all-time record is the live streak itself, still capable of growing.
   - `record_is_current` is true iff the record's window is the current
     window *and* `record.gap == current.gap` (equivalently: the current
     window's `best` equals its own `trailing`). This can be false even when
     the record technically "lives" in the current window, if that window
     also contains an earlier, larger, already-closed run -- in which case
     both numbers should render (see UI section), since they mean different
     things.

## New module: `app/services/map_streaks.py`

```python
@dataclass
class MapStreakWindow:
    act_labels: list[str]   # e.g. ["V26:A3", "V26:A4"]
    gap: int                 # matches played without this map, in this window
    ongoing: bool             # window is still open (touches "now")

@dataclass
class MapStreak:
    map_name: str
    current: MapStreakWindow
    record: MapStreakWindow          # may be the same window as `current`
    record_is_current: bool

def compute_map_streaks(db: Session, player_id: int) -> list[MapStreak]: ...
```

Imports `MIN_POOL_MATCHES` and the `SEASON_ACTS_PATH` loading logic from
`app/services/map_prediction.py` rather than duplicating the constant/act
loading (extract the act-loading helper if needed so both modules share it
without one importing private internals of the other).

Sort order for the returned list: same order as `get_current_map_pool`
iteration is fine (no popularity requirement here) -- alphabetical by map
name is simplest and most scannable in a table.

## UI

New card on `app/templates/players/detail.html`, placed after the existing
per-map stats section (`profile.map_stats`). Plain table (matches the
existing site convention for tabular stats; no chart -- this is a lookup, not
a magnitude comparison):

| Map | Current gap | Record gap |
|---|---|---|
| Ascent | 12 matches (ongoing, since V26:A4) | -- |
| Bind | 3 matches (since V26:A4) | 9 matches (V26:A2-A3) |

Act-label ranges collapse to first/last (`V26:A2-A3`, not every act in
between) rather than printing every merged act's label -- a map that's
stayed in the pool across many consecutive acts would otherwise print a
long chain in one table cell.

- "Current gap" always shown -- this is the live number (see `trailing`
  above), not necessarily that window's best-ever run.
- "Record gap" column only rendered with its own act-range text when it
  differs from the current window (`record_is_current == False`); otherwise
  leave the cell as `--` to avoid showing the identical number twice.
- Empty state (player has no matches in the current pool's window for any
  map -- e.g. brand new tracked player): render the existing site's
  `page-meta` empty-state paragraph style, matching how
  `map_prediction/index.html` handles "no current-act map data available
  yet".

## Wiring

`app/routers/players.py`'s `player_detail` handler calls
`compute_map_streaks(db, player.id)` alongside the existing
`get_player_profile` call and passes the result into the template context as
`map_streaks`.

## Testing

`webapp/tests/` already has a pytest convention for pure-function service
logic (see `test_sessions.py`: plain functions, `SimpleNamespace`/dataclass
stand-ins for ORM rows, no real DB or fixtures). Follow the same pattern in
a new `webapp/tests/test_map_streaks.py`, factoring the windowing/gap-scan
logic so it's testable against plain `(map_name, played_at)` tuples and an
in-memory act list rather than requiring a live `Session`. Cases to cover:

- A map continuously in the pool across 2 adjacent acts (window should
  merge, not reset the count at the act boundary).
- A map that drops out of the pool for one act then returns (two disjoint
  windows -- record must not bridge across the gap).
- A player who has never played a given current-pool map (gap == total
  matches in the window, `ongoing=True`).
- A player with zero matches in the current act's window at all (empty
  state).
- Record window differing from current window (`record_is_current=False`)
  and record == current window (`record_is_current=True`).

No template/UI test harness exists in this repo (FastAPI + Jinja, checked);
verify the new player-detail section manually by loading
`/players/{display_name}` locally per CLAUDE.md's local-run instructions.
