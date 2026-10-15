# Map streaks over the real map pool: design

Status: revised after the P2 review (AFK run 2026-10-14-map-pool-streaks; review in the run folder, PLAN-REVIEW.md). Register answers R1-R6 are settled; `D<n>` are the run's
decisions (in the run's `DECISIONS.md`).

## Goal

For each map in the current Competitive pool, the player page's "Map streaks" section shows the longest run of a
player's matches without that map, counting only matches played while the map was in the pool (R1). Today the pool
is guessed from our own matches per act (`app/services/map_streaks.py:_act_pools`, `MIN_POOL_MATCHES` = 3) and
`scripts/season_acts.json` stops at V26:A4 (2026-06-23), so the last "act" mixes pools and Abyss's streak counts June
and July, when Abyss couldn't be played. Map prediction's current pool (`map_prediction.get_current_map_pool`)
guesses the same way (R3).

The fix: a real, dated pool history (seeded from the wiki and the patch notes, R2), stored as pool changes in the DB
(R4), kept current by a daily check of playvalorant.com's patch notes behind a switch (R4, R5), and read by both
consumers through one pure service.

## In scope

1. A committed seed of the pool history, plus the script that rebuilds it from the wiki and the patch notes.
2. A patch-notes parser (standard library only), tested on trimmed saved copies of real pages.
3. Migration 0023 (the next unused on the base): two tables, seeded from the committed seed.
4. A daily refresh thread in the web service, on behind `MAP_POOL_AUTO` (R5), never in demo mode, plus the
   matches safety net (R4).
5. `app/services/map_pool.py`: `pool_at(t)` and per-map in-pool intervals, pure over loaded data.
6. `map_streaks` rewritten on R1; `get_current_map_pool` reads the real pool (R3); the section's copy updated.
7. A CLI, `scripts/map_pool.py show | refresh | seed`.
8. A real-data before/after check for 3 tracked players from the local DB (run folder only).

## Out of scope

Impact scoring, replays and map control, the tracker.gg crawler, `scripts/analyze_map_diversity.py` (keeps reading
`season_acts.json`), Deathmatch/Unrated pools, the `valomaths` service config, `load_seed_data.py`.

## Architecture

### Pool data model: change events (D1, tier 1)

The pool is stored as dated changes, not per-patch snapshots: patch notes only state deltas ("X is IN / OUT"), and a
snapshot is a fold over the deltas. `pool_at(t)` = every map whose latest change at or before `t` is IN.

Pre-rotation history (before patch 5.0, 2022-06-22) is one IN change per map at that map's release (R2: before 5.0
every released map was in the pool), dated from the wiki's release dates (Bind/Haven/Split at the 1.0 launch,
2020-06-02, the beta being out of scope). Our matches start 2022-10-10, so only the 5.0 pool and later matter in
practice, but the release rows make `pool_at` total. (P2 #8)

Tables (migration `0023_map_pool.py`):

- `map_pool_changes`: `id` serial PK; `map_name` text (tracker.gg's title case, as `matches.map_name`: "Abyss");
  `in_pool` boolean; `effective_at` timestamptz; `patch` text ("12.08", or "release" for pre-5.0 rows);
  `source` text, check in (`seed`, `patch_notes`); `source_url` text null; `created_at` timestamptz default now().
  Unique (`map_name`, `effective_at`, `in_pool`). Index on `effective_at`.
- `map_pool_patch_notes`: `url` text PK; `patch` text; `published_at` timestamptz; `first_seen_at` and `read_at`
  timestamptz (`read_at` null while unread); `change_count` integer null; `miss_count` integer default 0;
  `last_error` text null. One row per patch-notes page the refresh has looked at, including pages that changed
  nothing, so a read page is fetched once. The seed's own patch pages are inserted too, so the refresh never
  re-reads history.

**Where the reader takes the history from (D11, tier 2; P2 #2):** the committed seed JSON is folded with the DB's
`source='patch_notes'` rows. The migration still inserts the seed as `source='seed'` rows (R4: the table is seeded
with the full history; `scripts/map_pool.py show` and plain SQL see the whole history), but the reader does not use
them, so a later seed correction ships on merge to both sites without a migration or a hand-run against Render.
`scripts/map_pool.py seed` replaces every `source='seed'` row with the current JSON (delete + insert in one
transaction) to keep the copy in step. Duplicates between the seed and a fetched row (the same map, direction and
time) fold to one.

The upgrade inserts the seed rows with `ON CONFLICT DO NOTHING`, from a copy of the seed frozen into the migration
module (`SEED_AT_0023`), so the migration never depends on a file that changes later. The downgrade drops both
tables. The migration is additive: nothing existing reads or changes.

### Seed: `app/data/map_pool_seed.json` + `scripts/build_map_pool_seed.py` (R2)

JSON: `{"built_at", "sources": [...], "changes": [{"map", "in_pool", "effective_at", "patch", "source_url"}],
"patch_notes": [{"url", "patch", "published_at"}]}`. Built by `scripts/build_map_pool_seed.py`. The committed JSON is
reviewed history: the site never reads the wiki (R2). The script writes the file; a human diffs and commits it.

What the research found (run folder `research/`, 68 read-only requests, 2026-10-14):

- **Wiki** (`Maps`, section 8 "Map rotations > History"): one table, rows = maps, column 2 = release patch, then one
  cell per "Starting Patch" column (5.0, 6.0, 6.08, 7.04, 8.0, 8.11, 9.08, 10.00 … 12.08, 13.00, 13.04, 13.08). The
  marker is the cell background: `#9eff9e` in, `#ffc7c7` out, `gray` not released (`colspan=N`). Columns 13.00 on are
  empty (not filled in yet). The Fracture row is one cell short; `colspan="3;"` appears on Sunset.
- **Patch notes** agree with the wiki up to 12.08 except where a new map reached Competitive a patch late (Pearl after
  5.0, Lotus at 6.01, Sunset at 7.05, Abyss at 9.0; all footnoted on the wiki) and the 9.0 reset to five maps
  (Lotus back at 9.01, Icebox at 9.02), which has no wiki column.
- **playvalorant.com** pages are server-rendered Next.js. Each patch page carries JSON-LD `datePublished` (always 13:00
  or 14:00 UTC from 10.00 on); the pool lines are in the article HTML only (`ArticleRichTextBlade`), not in
  `__NEXT_DATA__`. The game-updates listing's `__NEXT_DATA__` lists 200 articles (back to 5.04) with `title`,
  `publishedAt` and `action.payload.url`; URLs mostly follow `valorant-patch-notes-<maj>-<min>/` with exceptions
  (`-811`, `-901`, 10.07 outside `game-updates/`, `5-0`), so the refresh takes URLs from the listing, never builds them.
- The 13.08 notes (2026-10-13 13:00Z) changed the pool: Bind and Corrode in, Ascent and Split out. Current pool:
  Abyss, Bind, Corrode, Haven, Lotus, Summit, Sunset.

Build steps: (1) parse the wiki table into a pool per column; (2) apply `scripts/map_pool_corrections.json`, a short
committed list of the late-entry cases above, each with its patch-notes quote (D10, tier 1); (3) date each column by
its patch page's `datePublished` (5.0's page says 2022-06-16 but the patch went live 2022-06-22: before our first
match, so harmless); (4) for patches after the wiki's last filled column, parse the patch notes with the site's parser;
(5) diff consecutive pools into changes; pre-5.0 maps get an IN at release. Mid-patch pulls with no patch note (Split
for an exploit during 8.07) are not recorded (D10).

### Effective time (D2, tier 2)

A change takes effect at its patch notes' `datePublished` (UTC). Riot deploys a patch region by region, often after
the notes are published, so a few matches on an outgoing map land after its OUT time (Bind's last local match is
2026-04-29 02:40Z, after 12.08's 2026-04-28 13:00Z). Those are handled as boundary bleed (below), not by guessing a
regional offset per patch.

Checked on the local DB (3,709 matches) against the draft history: across 30 OUT changes, the latest match on an
outgoing map came 19 h after publish (12.05 Abyss), and only 6 changes had any such match (1-5 each). Across 33 IN
changes, the earliest match on an incoming map came 4 h after publish (13.04 Abyss); none came before, except Pearl
before 6.0, which is the draft's missing "Pearl after 5.0" entry, fixed by the corrections list.

### Safety net (R4) and boundary bleed (D3, tier 2)

`pool_at` is the stored pool plus a correction from our own matches:

- **Bleed:** a match on map X within `BLEED_DAYS` = 3 days after X's OUT change (observed max 19 h) is old-pool play in a late region.
  It does not put X back in the pool. (It still resets X's streak: a play is a play, R1.)
- **Missing IN:** counted per out-of-pool interval of a map (from an OUT change, or from before its first IN, to the
  next IN or now). If one interval holds at least `SAFETY_NET_MIN_MATCHES` = 3 matches on the map (population-wide)
  outside the bleed window, the map counts as in the pool from the first of those matches to the interval's end.
  Strays in different intervals never add up (P2 #3). This covers a patch the parser missed. It never removes a map.

Both constants are first values (tier 2, grouped on one card).

### Reader: `app/services/map_pool.py` (pure core + one cached loader)

- `PoolHistory` (frozen): sorted changes. `pool_at(t) -> frozenset[str]`; `intervals(map) -> list[(start, end|None)]`;
  `current_pool() -> frozenset[str]`; `with_safety_net(population_matches)` returns a corrected history.
- `load_pool_history(db) -> PoolHistory`: the committed seed plus the DB's `patch_notes` rows (D11); then the safety
  net over `(map_name, played_at)` of all matches. Process-memoized with the existing 300 s TTL pattern
  (`map_streaks._get_acts_and_pools`), cleared by the refresh when it stores a change (D6, tier 1). The DB reads run
  inside a SAVEPOINT (`db.begin_nested()`) that is rolled back on error, so a missing table or a failed query falls
  back to the seed alone, logs, and leaves the request's session usable for its next query (P2 #5). A test drops
  the table (or points at a DB without it) and still renders streaks and predictions.
- Map names compare case-insensitively against patch notes ("ASCENT") and are stored in the title case used by
  `matches.map_name`.

### Map streaks (R1)

`compute_map_streaks(db, player_id)`: for each map in `current_pool()` (sorted), walk the player's matches in time
order: a match on the map resets the run to 0; a match on another map while the map is in the pool adds 1; a match
while the map is out is skipped (neither counts nor resets). `current` = the trailing run; `record` = the longest run,
with the dates of its first and last counted match. A map row is shown when the player has at least one match while
that map was in the pool (or on the map).

Output fields (P2 #4): `MapStreakWindow(gap, first_at, last_at)` (`act_labels` and `ongoing` go: the template never
read `ongoing`). `first_at`/`last_at` are the first and last counted match of the run, both None when `gap` is 0.
`record` is the longest run, the latest one on a tie. `record_is_current = current.gap >= record.gap and current.gap
> 0` (a tie with an older record is shown as record-equalling, as "record breaking" today; a 0/0 row is never a
record). The record's label becomes a date range ("Jun 2025 - Aug 2026") instead of act labels (D4, tier 1:
`season_acts.json` no longer feeds this section); no date line when the record is 0. The empty state ("No
current-act match data yet") becomes "No matches yet while these maps were in the pool".

The section copy becomes, plainly: for each map in the current pool, the longest run of your matches without it,
counting only matches played while it was in the pool; matches while it was out are skipped and the run carries on
when it returns (D5, tier 1: site copy per judgment.md).

Map streaks are not in `player_view_cache` (computed live in `routers/players.py:96`), so no cache version bump.

### Map prediction (R3, D7 tier 2)

`get_current_map_pool(db)`: the pool is `current_pool()`. Popularity weights come from our matches since the current
pool began (the latest change's `effective_at`), restricted to pool maps, with add-one smoothing so a map just added
(no matches yet) gets a small, non-zero weight instead of disappearing. Right after a pool change (13.08 is a day
old) the weights are close to uniform until matches come in; that is accepted under R3 (P2 #12, on D7's card).
`MIN_POOL_MATCHES` and `_current_act_start`
go; `SEASON_ACTS_PATH` stays only if something else imports it.

### Refresh: `app/services/map_pool_refresh.py` (R4, R5)

- Switch: `settings.map_pool_auto` (`MAP_POOL_AUTO`), default false; on only when true and not `demo_mode`.
  `render.yaml`: `MAP_POOL_AUTO: "true"` on `valowithfriendstracker` only, on from the merge (R5).
- Trigger (D8, tier 1): a daemon thread started in `main.lifespan` beside the archive sync. It runs one check soon after
  start (after a short delay) and then every 24 h; a check is skipped if this process checked under 24 h ago. Never on a
  request path, so it never slows a page.
- A check: GET the game-updates listing (honest User-Agent, 20 s timeout); take the items whose URL path contains
  `patch-notes` (P2 #10; pinned by a trimmed listing fixture); keep those published after the seed's newest patch and
  not yet read (`read_at` null or no row); fetch at most 5 a check, **newest first** (P2 #1); for each: GET it, parse
  `datePublished` and the pool lines, and in one transaction insert the changes and mark the page read.
- **Parser miss** (one definition, P2 #1): a page whose text has a block (heading or list item) containing "map pool
  updates" or "map rotation" but yields no pool line. 13.06 ("the active map pool", a lobby feature) is not a miss; a
  test pins that. A miss is stored (`miss_count` + 1, `last_error`), logged as a warning, and retried on later checks
  for 7 days after the page's publish date, then left unread and logged once as given up. A page with no such block
  and no pool line is read with `change_count` 0.
- Writes use `INSERT … ON CONFLICT DO NOTHING` on both tables, so two instances checking at once during a deploy
  never fail (P2 #6). Once a day per process means one fetch of the listing per deploy, which is fine.
- Any network or parse error: log, keep the last known pool.
- `scripts/map_pool.py refresh` runs one check by hand (same code, ignores the switch), `show [--at ISO]` prints the
  pool, `seed` replaces the DB's `seed` rows with the committed JSON.
- Supporting edits (P2 #13): `map_pool_auto` in `app/config.py` with a comment like its neighbours, a line in
  `webapp/.env.example`, and a CLAUDE.md note beside the other switches.

### Parser: `app/services/patch_notes.py` (standard library)

`parse_patch_notes(html) -> PatchNotes(published_at, changes)`; `parse_listing(html) -> [(title, url, published_at)]`.
The date is JSON-LD `datePublished`; the text is the article HTML (`ArticleRichTextBlade`), turned into plain text per
`<li>`/`<p>` with `html.parser` (`&nbsp;` normalised). A pool line, case-insensitive, within one list item or sentence:
`<maps> (is|are) [back] (IN|OUT) [of] [the] <queues>` where `<maps>` is names joined by "," / "and" and `<queues>`
contains "competitive". This covers every form from 9.08 to 13.08 ("SUMMIT and SUNSET are IN the COMPETITIVE and
DEATHMATCH queues.", "ABYSS and CORRODE are OUT of Competitive …", "is back IN the", "are BACK in the COMPETITIVE
QUEUE"). Older free-form wordings (pre-9.08) are not parsed: that history comes from the wiki. A new map's name is
accepted as written and stored title-cased only under a "Map Pool Updates" block (P4 #2). Parser miss: as defined in
the Refresh section ("map pool updates" or "map rotation" block, no pool line). Tests: trimmed saved copies of 12.08, 13.00, 13.04 and 13.06 (no pool change) under
`webapp/tests/fixtures/patch_notes/`, plus one-line cases for the other wordings and a trimmed listing.

Text extraction splits at element boundaries (each `h1-h6`, `li`, `p` is its own block; a nested `ul` closes the
parent's text), so "Map Pool Updates" never runs into "ASCENT is IN" and the map list never picks up "Updates"
(P2 #11, tested).

## Tests (P2 #7)

Each rule gets a test that fails when the rule is removed:

- R1: an out-of-pool match is skipped (neither counts nor resets); the run carries on when the map returns; a play
  resets to 0; current = trailing run; record = longest, latest on a tie; `record_is_current` on ties and on 0/0;
  first/last dates; a row for a player with no matches since a map was added is omitted; maps outside the current
  pool get no row.
- Safety net: a bleed play resets the map's streak but doesn't restore it to the pool; 3 strays in one interval add
  the map from the first stray, 2 don't; strays in separate intervals don't add up.
- Pool history: `pool_at` before, at and after a change; seed + fetched rows fold; a duplicate folds to one.
- Prediction: the pool is the real pool; a map with zero matches gets the add-one weight; a non-pool map gets none.
- Reader: a DB without the tables still renders streaks and predictions, and the session stays usable.
- Parser: the four fixtures (12.08, 13.00, 13.04, 13.06 as no change and not a miss), one-line wording cases, the
  miss rule, element-boundary splitting, the listing filter.
- Refresh: newest first, the 5-page cap, a miss stored and retried, given up after 7 days, ON CONFLICT on a second
  writer, off in demo mode and when unset, the memo cleared on a stored change. No test touches the network (a fake
  fetcher).
- Migration 0023: up, down, up on the throwaway `afk_map_pool_test` (`VALO_TEST_DATABASE_URL`), with the row counts.

Old tests removed: `tests/test_map_streaks.py` tests of `_map_windows`, `_act_pools`, `_gap_in_window` and the
act-pool cache (`_get_acts_and_pools`), replaced by the R1 tests above and by memo tests (TTL hit, cleared on store).

## Decisions (summary; full entries in DECISIONS.md)

- D1 change events, two tables (tier 1, R4's agent-decides).
- D2 effective time = `datePublished` (tier 2).
- D3 bleed 3 days, safety net 3 matches per out-of-pool interval (tier 2).
- D11 the reader takes history from the committed seed plus fetched DB rows (tier 2).
- D4 record label as a date range (tier 1).
- D5 section copy (tier 1, judgment).
- D6 cache: 300 s memo cleared by the refresh (tier 1).
- D7 prediction weights since the current pool began, add-one smoothing (tier 2).
- D8 daily thread in lifespan, at most once a day per process, 5 pages a check (tier 1).
- D9 migration 0023 ships to both sites at merge (tier 2 card, judgment line 24).
- D10 seed corrections list for the wiki's late-entry cases; unannounced mid-patch pulls not recorded (tier 1).

The seed is built through 13.08, so at merge the refresh finds nothing new; the first page it reads is 13.09 or later.

## Risks

- Wiki and patch notes disagree on a column: the seed script reports conflicts; patch notes win (they are Riot's).
- Patch-notes wording changes: the parser-miss rule keeps the page unread and logs it; the safety net covers matches.
- Listing markup changes: the check finds no URLs and logs; nothing breaks.
- The demo DB gets the seeded tables from the migration and never fetches; its streaks change to the real pool too
  (sample data only).
