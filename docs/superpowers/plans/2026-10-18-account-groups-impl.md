# Account groups, part 2: implementation plan

Design: `docs/superpowers/specs/2026-10-18-account-groups-design.md` (its "Changes from the design review" section
wins over the sections above it). Hardening findings H1-H8 and decision D2 are the run's (its review of
`a88f2ae..9910f07`). Revised after the implementation-plan review (I1-I15). One commit per step (`W<n>: …`).
Paths are under `webapp/`.

Test env for every check: `VALO_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/valo_afk_groups_test`.
"Pins" = `tests/test_player_view_cache.py tests/test_prewarm_player_cache_ids.py tests/test_site_stats.py
tests/test_step1_query_merges.py tests/test_verify_player_cache_coverage.py tests/replays/test_gap_patterns.py
tests/test_account_groups.py tests/test_account_groups_routes.py tests/test_match_page_result.py`. Every step also
runs each test file `git grep -l` finds naming a function it changes, and shows its tests bite (red first, or
mutants).

Corpus safety (I9): every script or helper that writes to `valo_groups_corpus` refuses unless `DATABASE_URL`'s
host is `localhost`/`127.0.0.1` and its database is `valo_groups_corpus`.

## Hardening

### W6 — Demo gating, groups of one, route tests (H1, H6, H4) — DONE 3f24017d

### W7 — Concurrency and the lock rules (H2, H3)
- `app/services/account_groups.py`: every write path (link, unlink, make-main, label) reads its group with
  `with_for_update().populate_existing()` on `account_groups` and re-checks its rules after the lock (a no-op
  lock on sqlite).
- `link_account` inserts the two member rows in ascending `player_id` order (I2), so two opposite links at
  once give the loser a unique violation, not a deadlock.
- `app/routers/accounts.py` add route: `IntegrityError` **or** a PG deadlock (`OperationalError` with
  pgcode `40P01`) around link + commit → rollback, re-render with "That account was just linked to another
  group." (200).
- Tests: PG (`tests/_postgres.py`, `empties_tables=True`): S1 runs make-main and holds its transaction after the
  lock; S2's leave runs in a thread with `SET lock_timeout = '2s'`; assert S2 blocks until S1 commits, and the
  end state has `main_player_id` a member (I3). PG: two opposite links in two threads → one wins, the other
  gets the message, no 500. sqlite route test with `link_account` raising `IntegrityError` → the message.
- Mutants (`mutants-W7.json`): sub can't add; a linked account can't be claimed; the main can't leave; the last
  sub's leaving deletes the group; `with_for_update` dropped (caught by the blocking test); the member insert
  order reversed; the rollback dropped. Red first for the new behaviour.
- Check: `tests/test_account_groups.py tests/test_account_groups_routes.py` + pins; mutants N/N.

### W8 — Shared matches, alt pre-warm (H5, H7)
- `app/services/player_data.py` `load_player_match_data` with `player_ids`: one row per match, the lowest
  player id's (D2), deduped **in SQL before the LIMIT** (a `min(player_id)` per `match_id` subquery), keeping
  the outer ORDER BY byte-identical (pinned at `tests/test_player_view_cache.py:476-488`) (I6).
  `recent_match_rows.recent_form_entries` with `player_ids`: same.
- `app/services/player_view_cache.py`: pre-warm recomputes per-player rows only for ids with their own rows;
  ids that only have a group row go only to `prewarm_group_caches`.
- Tests: two linked accounts in one match → one combined row; 31 matches with one shared, limit 30 → 30 rows;
  pre-warm of a group-only alt writes no `player_view_cache` row for it and refreshes the group row.
- Check: tests + `tests/test_prewarm_player_cache_ids.py` + pins; red first.

### W9 — Timing harness and "before" numbers
- `scripts/time_player_views.py` (committed; prints ids, not names, unless `--names`; corpus safety above):
  median of 3 for `prewarm_player_cache` of one account and of its group (cold), the combined career page and a
  single career page cold and cached (route functions in-process), the H8 extra queries (`_account_view`,
  `group_fingerprint`), blob bytes × rows written. A `--link`/`--unlink` mode links or unlinks the three
  accounts on the corpus copy.
- Run for one tracked account with two friends' accounts linked as stand-in subs (the corpus has no real alts). Output to the run folder only:
  `timings-before.json`.
- Check: `timings-before.json` has every figure.

## Friends through groups

### W10 — The circle resolver
- New `app/services/circle.py`: `own_account_ids(x)` (its group, or {x}), `circle_ids(x)`,
  `circle_friend_ids(x)` (= circle − {x}; keeps own alts, design §1), `circle_teammate_ids(x)`
  (= circle − own accounts, the fight-EV pool, P10), `circle_dependents(ids)`; one SQL statement each.
- New `tests/test_circle.py` (sqlite): {self} alone; own alts; friends' alts; friend-of-friend excluded;
  friendships owned by an alt count; each derived set; `circle_dependents`; one statement per call.
- Check: the file; red first.

### W11a — Viewer-side readers use the circle (R4)
| Reader | Gets |
| --- | --- |
| `site_stats.viewer_group_player_ids` (`:172`) | `circle_ids(viewer)` |
| `matches.py:426` (`get_viewer_team`) | friends = `circle_friend_ids(viewer)`; "You" for `own_account_ids(viewer)` (P8) |
| `matches.py:747` | `circle_ids(viewer)` |
| `routers/sessions.py:22-25` | all: `circle_ids(viewer)`; "just mine": `own_account_ids(viewer)` (P9) |
| `session_stats.py:215` | `circle_ids(viewer)` |
| `squad.py:236` | `circle_friend_ids(viewer)` |
| `gap_patterns.py:274` | `circle_ids(viewer)` |
- Rewrite the tests that patch `list_friend_ids` on these modules (`tests/test_site_stats.py:92`,
  `tests/test_match_page_result.py:51-66`) to patch the circle function (I5).
- Tests: per reader, a friend's alt and an own alt count as the table says.
- Check: the tests + grep-found files + pins.

### W11b — Page-side readers use the circle
- fight-EV pool: `fight_ev.py:603`, `player_views._roster_player_ids` (`:116-119`) → `circle_teammate_ids`.
- `recent_match_rows.py:238` → `circle_ids(shown) − shown accounts`.
- `group_fingerprint` hashes `circle_ids(main)`.
- Tests: a friend's alt is in the pool; the fingerprint changes when a friend links an alt.
- Check: the tests + grep-found files + pins.

### W12 — Invalidation and pre-warm of circles
- `app/routers/friends.py` add/remove: `invalidate_player_cache(own_account_ids(owner))`.
- `app/routers/accounts.py` link/unlink: `invalidate_player_cache(circle_dependents(before ∪ after))` in the
  write's transaction.
- `site_stats.prewarm_viewer_site_stats` (`:210`): owners plus their group-mates (P5).
- Tests: alt linked to a friend → viewer's rows gone; friend add from an alt → main's rows gone; /stats viewer
  row misses after a friend links an alt; the pre-warm covers a friendless sub.
- Mutants: each invalidation call removed; the pre-warm widening removed.
- Check: tests + pins; mutants N/N.

**Checkpoint 1: full suite (parts, `--deselect` the 42 known).**

### W13 — Account column on the combined Form card (R1 b)
- `RecentMatchRow.account`; `build_recent_match_rows` fills it (label, else name without tag) only with
  `player_ids`; `_recent_match_rows.html` shows "Account" after Result when any row has one.
- Tests: combined rows carry labels / stripped names; single-account rows have none, no header.
- Check: tests + pins; red first.

## Filter

### W14 — Agent → role table
- `app/data/agent_roles.json` (the 29 agents), `app/services/agent_roles.py`: `ROLES`, `role_of`,
  `parse_filter(key, agents_played) -> str` ("" when bad), `filter_agents(key, agents_played) -> frozenset`,
  `filter_label(key)`, `rank_agents(counts_and_last_played)` (most matches, then most recent, then name; used
  by both top-3 and the dropdown, I14).
- Tests: 4 roles, 29 agents, `KAY/O` initiator, unknown → None, `parse_filter` cases, `rank_agents` ties.
- Check: the file; red first.

### W15 — Migration 0027
- `alembic/versions/0027_view_filter_scope.py`: widen `scope` to 48 and replace both CHECKs (design P1);
  downgrade deletes `|` rows, narrows, restores. Models match. `player_view_cache.cache_scope_key(scope, key)`.
- Tests: sqlite round-trip of a filtered row on both tables; the model CHECK accepts `career|role:duelist`,
  refuses `bogus`.
- Check: tests + pins; on `valo_afk_groups_test`: `upgrade head`, insert `career|role:duelist` into both tables,
  `bogus` refused, `downgrade 0026` (filtered rows gone), `upgrade head` (I8); on `valo_groups_corpus`:
  up/down/up with both tables' row counts equal (localhost `DATABASE_URL`).

### W16 — One load, many filters
- `load_player_match_data(…, agents=None)`: SQL `agent IN (…)` when given (P3).
- `player_views.compute_player_views_by_filter(db, player, keys, draws, player_ids) -> {cache key: PlayerViews}`
  (one load, one replay, slices per key and scope; recent = first 30 of the filtered list);
  `compute_player_views_by_scope` is its `("",)` case, same output; `compute_player_views(…, agents=)`.
- Tests: filtered views equal a run on a DB holding only those matches; filtered recent = last 30 of that
  filter; one `load_player_match_data` call for 8 keys; unfiltered output unchanged (pins green unchanged).
- Check: tests + pins; red first.

### W17 — Pre-compute and the keyed cache
- `recompute_player_views` and `prewarm_group_caches`: "" + roles with matches + top 3 agents (`rank_agents`),
  both scopes, one multi-row upsert and one commit per account / group.
- `get_cached_views`, `get_cached_group_views`, the stores, and `players.get_player_and_cached_views` (the
  merged Q1+Q2, pinned at `tests/test_step1_query_merges.py:116-129`) take a key, default "" (I7).
- `filter_cached_player_ids` and `scripts/verify_player_cache_coverage.py` ignore `|` scopes (I14).
- Tests **on PG** (`tests/_postgres.py`, `empties_tables=True`; `pg_insert` doesn't run on sqlite, I1): written
  keys; invalidation clears filtered rows; isolation (mutating returned views doesn't change a later read);
  fresh-session read-back after the store. sqlite: top-3 selection, coverage ignoring `|` rows.
- Mutants: the commit removed; the tie rule; the key dropped from the upsert.
- Check: tests + pins; mutants N/N.

**Checkpoint 2: full suite.**

### W18a — The Form card filtered (P4)
- `recent_form_entries` and `build_recent_match_rows`'s baseline query take `agents`; the
  `/recent-matches` fragment takes `?filter=` (parsed with `parse_filter` against the played-agents query).
- Tests: filtered rows only that role/agent; thresholds over the filtered baseline; bad key → unfiltered.
- Check: tests + pins; red first.

### W18b — The filter on the page
- `routers/players.py`: `filter` on the page and `/career`. Order (I7): unfiltered request = today's single
  merged query; filtered = player + played-agents query, then a keyed cache read. Empty filter → empty state,
  no compute. A miss computes that scope with `agents=` and writes through keyed.
- `detail.html`: GET form with a `<select>` (All agents / 4 roles / agents played, `rank_agents` order, with
  counts), keeps `accounts=all`, "Showing N matches as X", fragment URLs carry it. Map streaks not filtered.
  On the demo the filter shows and the account parts don't.
- Tests: a role and an agent on one account and on the combined view; bad key → unfiltered; empty role → no
  compute (counter); map streaks identical with and without `?filter=` (I14); demo.
- Check: tests + pins.

## Measure, show, write up

### W19 — "After" numbers and R4's reach
- `timings-after.json` (same script, plus a filtered page cold and cached, and the played-agents query, I13).
- R4's reach on the corpus, viewer that same account: `--unlink`, count, `--link`, count: /stats Friends
  (players, matches), match-list friends, sessions, squad → `r4-reach.json`.
- If H8's or P11's queries cost over ~10% of a cached page load: a `W19b` folding them (D3); else logged.
- Check: both files complete.

### W20 — Screenshots (`shot_page.py`, dev server on the corpus copy)
- Combined page with the Account column; filter (a role, one agent) on one account and on the combined view;
  `/accounts` as a main and as a sub; /stats Friends unlinked then linked (the order: `--unlink`, shot,
  `--link`, shot).
- Check: each PNG exists; before/after pairs differ, and /stats Friends' counts differ per `r4-reach.json` (I12).

### W21 — PR body and CLAUDE.md
- `<run folder>/PR-BODY.md`; CLAUDE.md "Account groups" covers the circle, the filter and 0027.
- Check: the file exists; CLAUDE.md mentions all three.

The wrap-up's full suite is the third run (W19-W21 change only a script and docs).
