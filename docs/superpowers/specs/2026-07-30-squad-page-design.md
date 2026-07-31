# Squad page (friend-pair stats)

**Status:** approved, ready for planning
**Date:** 2026-07-30

## Purpose

A new page, scoped to the logged-in viewer's own friends list, answering "who
do I play with the most, and who do I play *best* with" -- distinct from the
existing Player page (individual stats) and Sessions page (one night's shared
roster). Every stat here is a **pair** stat: viewer + one friend, not a
friend-to-friend (non-viewer) relationship, and not a single per-player
aggregate.

Same Recent 30 / Career windowing convention as the player page
(`app/routers/players.py`'s `RECENT_MATCH_LIMIT = 30`): the page loads with
the viewer's most-recent-30-matches view first, then lazily loads the career
(all-time) view via htmx in the background, exactly like
`players/detail.html`'s `#career-sections` div does today.

## Scope decisions (confirmed with user)

- **Friends only.** No acquaintances, no non-friend teammates -- pulls from
  `list_friend_ids`/`list_friends` (`app/services/friends.py`), same as the
  Friends page.
- **"Played with" = teammate.** Same `match_id` + same `MatchPlayer.team` as
  the viewer. `MatchPlayer.team` is fixed per match, so once matched as
  teammates in a match, they're teammates for every round of it -- no
  round-by-round team-membership check needed.
- **One ranked list, not a social graph.** Earlier drafts considered showing,
  for each friend, *that friend's own* top teammates (who may not be the
  viewer's friends) -- rejected. This page is "your squad": one list of the
  viewer's own friends, ranked by `matches_together`, each annotated with
  pair stats against the viewer specifically.
- **Round-win-impact-together** is the sum of the *viewer's* + *that
  friend's* own win-gated round impact (see `average_round_win_impact` in
  `app/services/matches.py`), averaged per shared round. This isolates "how
  these two do together," not the whole 5-stack's performance -- confirmed
  with the user rather than pulling in the other 3 teammates' impact too.
- **Shoutout eligibility threshold: 20 rounds together** (~1 map), so a
  2-round 100% win streak can't outrank a friend with real sample size for a
  shoutout category. No threshold for appearing in the main ranked
  list/table at all -- even a single shared match shows up, ranked low.

## Data flow

For a given `match_limit` (30 for "recent", `None` for "career"):

1. Fetch the viewer's own `MatchPlayer` rows for their most-recent
   `match_limit` matches (or all matches, for career) -- same query shape as
   `get_player_profile` in `app/services/players.py` (order by
   `Match.played_at.desc().nullsfirst()`, limit, then reverse). This defines
   the viewer's match window; nothing about a friend's *own* match count
   matters here.
2. For each of those matches, fetch every other `MatchPlayer` on the same
   `match_id` + same `team`, filtered to the viewer's friend IDs.
3. For every (match, friend) pair found, pull that match's rounds + the
   viewer's and friend's `ImpactScore` rows (kill_impact/death_impact) +
   `Round.outcome`, plus the same clutch-detection/trade-breakdown data
   `session_stats.py`'s `_build_replay_stats`/`_build_trade_stats` already
   compute -- adapted to only track the (viewer, friend) pair instead of the
   whole roster.
4. Aggregate per friend across every shared match:
   - `matches_together`, `rounds_together`
   - `wins_together` / `win_rate_together` (shared team won the round, via
     the existing `_winner_side` pattern)
   - `avg_round_win_impact_together` (per scope decision above)
   - `clutches_together` (rounds where either the viewer or the friend
     resolved a clutch while both were teammates)
   - `traded_together` (viewer traded for friend + friend traded for viewer,
     summed -- one combined number, not two separate directions)
   - `kill_differential_together` (combined kills minus combined deaths,
     across both the viewer and the friend, for the "Lethal Combo" shoutout
     category below)
   - `most_played_agent_together` (mode of the friend's agent across shared
     matches, same "most common agent" tally `session_stats.py` already does
     per player -- purely cosmetic, for the shoutout card's agent icon)

## New module: `app/services/squad.py`

```python
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
    squad_size: int              # friends with >= 1 shared match
    total_matches_together: int  # distinct matches across all friends (viewer's own match count in-window)
    total_rounds_together: int
    pairs: list[PairStats]       # sorted by matches_together desc
    shoutouts: list[PlayerShoutout]  # from app.services.shoutouts

def get_squad_overview(db: Session, viewer_player_id: int, match_limit: int | None) -> SquadOverview: ...
```

`SQUAD_SHOUTOUT_CATEGORIES` (module-level constant, same shape as
`SHOUTOUT_CATEGORIES`), passed to the generalized `assign_shoutouts`:

| raw key | headline | template |
|---|---|---|
| `win_rate_together` (as rounded %) | Best Duo | won `{v}%` of rounds together |
| `avg_round_win_impact_together` (rounded) | Dynamic Duo | `{v}` avg round-win impact together |
| `clutches_together` | Clutch Partners | `{v}` clutch round`{s}` won together |
| `traded_together` | Trade Partner | traded for each other `{v}` time`{s}` |
| `rounds_together` | Ride or Die | `{v}` round`{s}` played together |
| `kill_differential_together` | Lethal Combo | outkilled opponents by `{v}` combined, together |

Roster passed to `assign_shoutouts` is every friend clearing the 20-round
threshold; `raw_dicts` built from the fields above (filtered to the
threshold before being handed to the assigner, so a sub-threshold friend
never becomes a candidate even if their raw number would otherwise rank
well).

## Shared-file change: `app/services/shoutouts.py`

Generalize `assign_shoutouts` to accept a `categories` parameter:

```python
def assign_shoutouts(
    roster: list[tuple[int, str, str]],
    raw_dicts: dict[str, dict[int, int]],
    best_single_round_impact: dict[int, float],
    anchor: tuple[int, str, str] | None = None,
    categories: list[tuple[str, str, str]] = SHOUTOUT_CATEGORIES,
) -> list[PlayerShoutout]: ...
```

Internals unchanged (still the same bitmask-DP optimal assignment) --
just reads `categories` instead of the module constant. Existing callers
(`app/services/matches.py`, `app/services/session_stats.py`) need zero
changes since the default preserves current behavior.

**Explicitly accepted risk:** `shoutouts.py` has active history on the
`public` remote (this repo's upstream, per `CLAUDE.md`), so this edit carries
real merge-conflict risk on a future `git fetch public && git merge
public/main`. Confirmed with the user; proceeding anyway in favor of one
shared, DRY, optimal-matching implementation over a duplicated one.

## New router: `app/routers/squad.py`

Mirrors `app/routers/players.py`'s `player_detail` / `player_career_fragment`
pair exactly:

```python
router = APIRouter(prefix="/squad", tags=["squad"])

@router.get("")
def squad_page(request, db): ...
    # requires login (redirect to /login if not), like /sessions and /friends
    # renders squad/detail.html with match_limit=RECENT_MATCH_LIMIT (30)

@router.get("/career")
def squad_career_fragment(request, db): ...
    # renders squad/_squad_sections.html with match_limit=None
```

## New templates

- `app/templates/squad/detail.html` -- page shell, Recent 30 / Career toggle
  (same `.scope-toggle-btn` markup + inline show/hide JS as
  `players/detail.html`; duplicated rather than extracted into shared JS,
  consistent with how that page already does it).
- `app/templates/squad/_squad_sections.html` -- the actual content, included
  by the page shell for "recent" and fetched via htmx for "career":
  - Header stat tiles: squad size, total matches together, total rounds
    together.
  - Shoutouts card row -- same visual style as the existing Shoutouts
    sections on `matches/detail.html` / `sessions/detail.html`.
  - Full sortable table, one row per friend with >= 1 shared match, columns:
    Player, Matches Together, Rounds Together, Win Rate Together, Avg
    Round-Win Impact Together, Clutches Together, Traded Together, Kill
    Differential Together. Same sortable-table convention (`data-sort`
    attributes) as the match/session pages.

## Wiring

- Register `squad.router` in `app/main.py` alongside the other routers.
- Add a "Squad" nav link in `app/templates/base.html`, next to
  Sessions/Players/Friends.

## Testing

Follow `webapp/tests/test_sessions.py`'s convention: plain functions,
`SimpleNamespace`/dataclass stand-ins for ORM rows, no live DB. New
`webapp/tests/test_squad.py` covering the pure aggregation logic (factor it
so it's testable against plain tuples/dicts rather than requiring a live
`Session`):

- Two friends with different `matches_together` counts sort correctly.
- Win rate, round-win-impact, clutch, and trade aggregation each produce the
  expected numbers from a small hand-built set of shared rounds.
- A friend below the 20-round threshold appears in the ranked table but is
  never a shoutout candidate.
- Squad with zero eligible friends (empty state).

No template/UI test harness exists in this repo; verify `/squad` and
`/squad/career` manually against real data per `CLAUDE.md`'s local-run
instructions (or against Neon directly, as done for the recent Impact-metric
changes), covering: a viewer with a real friend list and shared match
history, a viewer with friends but no shared matches, and a viewer with no
friends at all.
