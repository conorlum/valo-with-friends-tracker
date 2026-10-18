# Account groups, part 2: friends through groups, an account column, a role/agent filter

Status: design (AFK run 2026-10-18-account-groups, P1). Builds on the account groups already in place
(migration 0026, `app/services/account_groups.py`, CLAUDE.md "Account groups").

## Goal

1. **Harden** what exists (review, fixes, mutants on the lock rules, timings).
2. **Account column**: on an account group's combined view ("All accounts"), every match row says which account
   played it: its label, else the Riot name without its tag.
3. **Role/agent filter** on the player page, for one account and for the combined view: one filter at a time,
   a role (Duelist, Initiator, Controller, Sentinel) or one agent the account or group has played.
4. **Friends through groups**: one group-aware resolver replaces the per-account friend list everywhere it is
   read.

Out of scope: friendships stay stored per account; no auth; nothing of groups on the demo (the filter may show
there, over sample data); no change to Impact scoring.

## 1. Friends through groups (R3, R4)

### The circle

`app/services/circle.py` (new):

- `circle_ids(db, player_id) -> set[int]`: the player's accounts (its group, or just itself), plus every account
  any of them has friended, plus each of those friends' own grouped accounts. One level only: a friend's
  friends are not in it. Includes `player_id` itself.
- `circle_friend_ids(db, player_id) -> set[int]`: `circle_ids` minus `player_id`. The drop-in for
  `list_friend_ids` (callers that add the viewer back keep doing so; the result is a superset of today's).
- Both cost one round trip: one SQL statement with the group expansion done in subqueries
  (`account_group_members` on both ends, `friendships` in the middle). No caching of the circle itself: it is a
  small indexed query, and every cache that depends on it is keyed or invalidated below.

`list_friend_ids` stays (the Friends page lists what this account owns, and add/remove are per account).

### Every reader switches (R4)

| Place | Today | After |
| --- | --- | --- |
| /stats Friends: `site_stats.viewer_group_player_ids` | viewer + own friends | `circle_ids(viewer)` |
| fight-EV teammate pool: `fight_ev.py:603`, `player_views._roster_player_ids` | profile owner's friends (a group: union over its accounts) | `circle_friend_ids(owner)` (a group: the circle of its main, which is the same set for every member) |
| match list: `matches.py:426`, `:747` | viewer's friends | `circle_friend_ids(viewer)` |
| sessions: `routers/sessions.py:25`, `session_stats.py:215` | viewer + friends | `circle_ids(viewer)` |
| squad: `squad.py:236` | viewer's friends | `circle_friend_ids(viewer)` |
| gap patterns: `gap_patterns.py:274` | viewer + friends | `circle_ids(viewer)` |
| recent match rows: `recent_match_rows.py:238` | friends of each shown account, minus the accounts | `circle_friend_ids` of the shown account (or the group's main), minus the shown accounts |

The viewer's own other accounts are in their circle (R3: "all your accounts"). They can't share a match with
the viewer, so on the match pages they only add their own matches to the viewer's "friends" lists, which is
what R3 asks for.

### Caches that depend on the circle

- **`viewer_site_stats_cache`** is keyed by `friend_set_hash` of the player-ID set it was built from
  (`viewer_site_stats_cache.py:54`, checked at `:85`). Fed `circle_ids`, a group change or a friend's new alt
  re-keys it with no new hook.
- **`player_view_cache`** (fight-EV's pool) has no fingerprint. Today a friend add/remove clears only the owner
  (`routers/friends.py:66,81`). After: every write that can change a circle clears the rows of every account
  whose circle it changes, via one helper `circle_dependents(db, account_ids) -> set[int]`:
  the accounts' own groups, plus every account that friends one of them, plus those accounts' groups.
  - friend add/remove by account A: `invalidate_player_cache(group of A)` (A's group-mates' circles changed
    too).
  - link/unlink: `invalidate_player_cache(circle_dependents(members before ∪ members after))`.
  - make-main and label changes don't change any circle; they clear nothing beyond what they do today.
  `invalidate_player_cache` already clears the group rows of the accounts it is given.
- **`account_group_view_cache`**: `group_fingerprint` hashes its members' friendships today
  (`account_groups.py` `group_fingerprint`). It hashes the main's `circle_ids` instead, so a friend's new alt
  changes the fingerprint too.

Everything else on the list above is computed live per request.

## 2. The account column (R1 b)

The only per-match table on the player page is the Form card's recent-match rows
(`_recent_match_rows.html`, built live by `build_recent_match_rows`, not cached). On the combined view it gains
an **Account** column after Result: the member's label if set, else the display name without its tag
(the existing `strip_tag` filter). `RecentMatchRow` gets `account: str | None`, filled only when
`player_ids` (the group) is given; the column renders only when any row has it. Single-account pages show
nothing new. The cached page blob is unchanged, so no `PLAYER_VIEW_CACHE_SCHEMA_VERSION` bump.

## 3. The role/agent filter (R5, R6)

### Filter keys

A filter is a string: `""` (none), `role:<role>` with `<role>` one of `duelist`, `initiator`, `controller`,
`sentinel`, or `agent:<Agent>` with the agent name as stored in `match_players.agent`. URL:
`?filter=role:duelist`, `?filter=agent:Jett`, passed to the career and recent-matches fragments like
`accounts=all`. A key that isn't a role and isn't an agent this account or group played is ignored (the page
shows unfiltered, with no error): the agent list bounds what can be cached on demand.

### Agent → role table

`app/data/agent_roles.json`, committed: the 29 agents from valorant.fandom.com's role categories
(`Category:Controllers` / `Duelists` / `Initiators` / `Sentinels`, MediaWiki API, 2026-10-18), with `KAYO`
written as stored, `KAY/O`. `app/services/agent_roles.py` loads it (`role_of(agent) -> str | None`). An agent
missing from the table (a new release) has no role: it is in no role filter, still has its own agent filter,
and a test lists every agent in the table so adding one is a one-line data edit.

### Data path: one load, one replay (R5's cost)

`compute_player_views_by_scope` already does one career load and one replay, then slices per scope
(`player_views.py`). The filter extends the slicing:

- new `compute_player_views_by_filter(db, player, filters, draws, player_ids) -> dict[cache_key, PlayerViews]`:
  one `load_player_match_data`, one `_replay_all_matches`, one per-match state/fight-EV block pass, one Impact
  load; then, per filter, keep the match players whose agent matches it (order kept, newest first), and per
  scope slice as today: `recent` = the first `RECENT_MATCH_LIMIT` of the **filtered** list (the last 30 matches
  on that role or agent), `career` = all of it. Per filter and scope only the bootstrap and the
  profile/econ builders run.
- `compute_player_views_by_scope` becomes the `filters=("",)` case and keeps its signature and output.
- The roster (circle) is resolved once per call.

So the pre-warm's added cost is 7 filters × 2 scopes of bootstrap + profile/econ on smaller match sets, never
another load or replay. The stop condition (one load per filter) doesn't apply. The impl plan measures it.

### What is pre-computed (R5)

For each account and each group, in `recompute_player_views` / `prewarm_group_caches` (so the ingest pre-warm
and `scripts/recompute_player_views.py` both get it): the unfiltered rows, the 4 roles, and the top 3 agents,
both scopes. "Top 3 agents" = the 3 agents with the most career matches in that account or group, ties broken
by the most recent match played on it, then by name. A role or agent with no matches isn't stored. Top 3 is
recomputed at every pre-warm, so it moves with new matches; a former top-3 row stays until the next
invalidation.

### On demand

A filter with no row (an agent outside the top 3, or before the pre-warm) is computed on the request, for both
scopes in one call, and written through on its own session like today's miss path. A miss costs one career load
and replay of that account or group, the same as today's career miss.

### Cache shape

The filter goes into the existing tables' `scope` column as `"<scope>|<filter>"` (`"career|role:duelist"`,
`"recent|agent:KAY/O"`); unfiltered rows keep `"recent"` / `"career"`. Migration **0027** widens `scope` from
`String(16)` to `String(48)` on `player_view_cache` and `account_group_view_cache`, and replaces both tables'
CHECK constraints (`ck_player_view_cache_scope`, `0003_player_view_cache.py:31`; `ck_account_group_view_cache_scope`,
`0026_account_groups.py:51`; both `scope IN ('recent','career')`) with
`scope IN ('recent','career') OR scope LIKE 'recent|%' OR scope LIKE 'career|%'` (no new column, no key
change; downgrade deletes rows whose scope contains `|`, narrows back and restores the old CHECKs). Why this and not a new column: every
existing read (`filter_by(player_id=, scope=)…one_or_none()`), the unique key `(player_id, scope)`, the group
key `(group_id, scope)`, the upserts' conflict targets and every invalidation keep working unchanged, and
invalidation reaches filtered rows for free (they share `player_id` / `group_id`). Anything that counts rows per
player (`scripts/verify_player_cache_coverage.py`, `filter_cached_player_ids`) is checked to count only
unfiltered scopes or not care.

Group rows carry the same fingerprint for every filter.

### The page

- A "Filter" `<select>` in the page header beside the account chips: "All agents", the 4 roles, then the agents
  played (most matches first, with counts). Changing it reloads the page with `?filter=` (keeping
  `accounts=all`). Plain GET form, no JS needed beyond submit-on-change.
- It filters every section built from the player's matches: the profile summary and charts, maps, agents,
  econ, pistols, the state diagrams, fight-EV, the Form card and its recent-match rows. **Map streaks aren't
  filtered**: they're about which maps the queue gives, not how the player plays.
- A filtered page says so under the header: "Showing N matches as <role/agent>". 0 matches: an empty-state
  line instead of the sections. No minimum sample beyond what each card already applies (fight-EV's own
  minimums stay).
- Shown on the demo too (sample data only); the account parts stay hidden there.

## Changes from the design review (P2, run folder `PLAN-REVIEW.md`)

These override the sections above where they differ.

- **P1** CHECK constraints replaced in 0027 (above). A test writes and reads back a filtered row on both tables.
- **P2** Empty filters cost nothing: the played-agents counts (one grouped query on `match_players` for the
  account or group, needed by the dropdown anyway) decide emptiness. A role with no played agent, or 0 matches,
  renders the empty state without any compute or cache lookup.
- **P3** On demand works per scope, like today: `load_player_match_data` gains `agents: frozenset[str] | None`
  (an `agent IN (…)` filter in SQL), so a filtered miss on the recent page loads at most 30 matches and stores
  only that scope, and the career fragment's miss loads the filtered career. Both write-throughs store keyed
  rows (`store_player_views` already takes a dict of scopes; `store_group_views` keeps its one-scope shape with
  a key). The pre-compute still does one unfiltered load and slices in memory.
- **P4** The Form card is filtered too: `recent_form_entries` and `build_recent_match_rows`'s baseline query
  take the same agent set; the stand-out thresholds are over the filtered baseline (the player's own top 20%
  on that role or agent). The fragment takes `?filter=`. The two queries' NULL `played_at` ordering
  (`player_data.py:86` NULLS FIRST, `recent_match_rows.py:137` NULLS LAST) is pre-existing and left alone
  (every match has `played_at` in practice).
- **P5** `prewarm_viewer_site_stats` (`site_stats.py:210`) warms friendship owners plus every account grouped
  with one, since those now have a circle.
- **P6** Pre-warm writes up to 8× the rows: one multi-row upsert per account (one statement for every key),
  and the timing items measure blob size × rows and the wall time.
- **P7** Rows cleared by a circle change are not re-warmed in the request: they re-warm lazily on the next visit
  (or the next ingest's pre-warm). Links and friend changes are rare; today's friend add already works this
  way.
- **P8** The match page's "You" team label (`matches.py:423-426`) covers the viewer's own grouped accounts.
- **P9** The player page's "Add friend" button and `is_friend` stay per account (friendships are stored per
  account). Sessions' "just mine" mode (`routers/sessions.py:22-25`) means the viewer's own accounts (its group).
- **P10** The fight-EV pool is `circle_ids(x) − accounts(x)` for one account and for a group (x = any member).
- **P11** The dropdown's agent counts are the one grouped query of P2, on every page render (measured with H8).

## 4. Hardening

From the W5 review (`HARDEN-REVIEW.md` in the run folder): each real finding becomes an item. Already required:
mutants for the lock rules (a sub can't add; a linked account can't be claimed; the main can't leave; the last
sub's leaving deletes the group) and for each new cache's transaction boundary and isolation; the full suite;
the cold and cached load of the combined career page.

## Decisions

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| a | Circle depth | one level: my accounts, their friends, those friends' accounts | R3 as written |
| b | Circle caching | none; one-query resolver, dependent caches keyed (/stats) or invalidated (player pages) | smallest change that is always correct |
| c | Filtered "recent" | last 30 matches on that filter | slicing the filtered list in memory; a role page should show 30 of that role |
| d | Cache shape | filter folded into `scope` (`recent|role:duelist`), column widened, migration 0027 | no key, read or invalidation changes |
| e | Unknown agent | no role, own agent filter only | a new agent still filters; one-line data fix |
| f | Bad filter key | ignored, unfiltered page | bounds on-demand cache rows to agents played |
| g | Minimum sample | none new; the count is shown | don't hide silently (register) |
| h | Map streaks | not filtered | not about the player's play |
| i | Account column | Form card's recent-match rows, after Result, combined view only | the page's only per-match table |
