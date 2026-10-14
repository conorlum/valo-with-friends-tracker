# Map streaks over the real map pool: implementation plan

Design: `docs/superpowers/specs/2026-10-14-map-pool-streaks-design.md` (revised after P2). This plan follows it; where
they differ, this plan wins and says so. All commands run from `webapp/` with
`PY=C:/Users/User/Documents/GitHub/valo-with-friends-tracker/webapp/.venv313/Scripts/python.exe`.
Research files (trimmed real pages, the draft history) are in the AFK run folder
`C:/Users/User/.claude/afk/2026-10-14-map-pool-streaks/research/` (`RES` below).

Map names everywhere are title case as in `matches.map_name` ("Abyss"); patch notes' capitals are title-cased.
Times are timezone-aware UTC `datetime`s; `matches.played_at` is `timestamptz`.

## S1. Patch-notes parser (`app/services/patch_notes.py`)

Files: `app/services/patch_notes.py` (new); `tests/test_patch_notes.py` (new); `tests/fixtures/patch_notes/`
(new): copy `RES/fixtures/patch_12_08.html`, `patch_13_00.html`, `patch_13_04.html`, `patch_13_06.html` as is, and
make `listing.html`: from `RES/listing.html`, keep only the `<script id="__NEXT_DATA__">` element with its JSON
trimmed to `props.pageProps.page.blades[2].items` holding 6 items (3 patch-notes items incl. 13.08 and 12.08, a
non-patch article, a YouTube item, the 10.07 item whose URL is outside `game-updates/`), every other key dropped
except the path to them. Under 20 KB.

Interface:
```python
@dataclass(frozen=True)
class PoolChange:            # one map's move
    map_name: str            # title case
    in_pool: bool

@dataclass(frozen=True)
class PatchNotes:
    published_at: datetime | None   # JSON-LD datePublished (TechArticle, or any @graph node with it)
    changes: tuple[PoolChange, ...]
    mentions_pool: bool             # a block contains "map pool updates" or "map rotation" (case-insensitive)
    @property
    def is_miss(self) -> bool: return self.mentions_pool and not self.changes

@dataclass(frozen=True)
class ListingItem:
    title: str; url: str; published_at: datetime | None

def parse_patch_notes(html: str) -> PatchNotes
def parse_listing(html: str, base_url: str = "https://playvalorant.com") -> list[ListingItem]  # only items whose URL path contains "patch-notes"; relative URLs made absolute
def pool_lines(text: str) -> tuple[PoolChange, ...]   # one block of plain text -> changes (exposed for one-line tests)
```
Rules: blocks = text of each `h1-h6`, `li`, `p` element inside `ArticleRichTextBlade` sections (fall back to the
whole body if none), split at element boundaries (a nested `ul` ends its parent's text), `&nbsp;`/whitespace
normalised. A pool line: case-insensitive `^(?P<maps>.+?)\s+(?:is|are)\s+(?:back\s+)?(?P<dir>in|out)\b\s*(?:of\s+)?
(?:the\s+)?(?P<queues>.*)$` with "competitive" in `queues`; `maps` split on `,` and ` and `; each name stripped of
punctuation, must be letters/spaces, 1-3 words; title-cased. Duplicates removed, order kept. Standard library only
(`html.parser`, `re`, `json`).

Tests (each must fail if its rule is removed):
- 12.08 → {Ascent in, Bind out}, published 2026-04-28T13:00Z; 13.00 → {Summit in, Sunset in, Fracture out, Pearl out};
  13.04 → {Abyss in, Breeze out}; 13.06 → no changes, `mentions_pool` False (it says "the active map pool"), not a miss.
- one-line cases: "ABYSS and CORRODE are OUT of Competitive and Deathmatch queues." / "Abyss is back IN the
  COMPETITIVE queue." / "Pearl and Split are BACK in the COMPETITIVE QUEUE" / "ASCENT is IN the Deathmatch queue."
  (no Competitive → nothing) / "Split enters Unrated & Swift Play queues" → nothing.
- element boundaries: `<li>Map Pool Updates<ul><li><strong>ASCENT</strong> is <strong>IN</strong> the Competitive
  queue.</li></ul></li>` → exactly [Ascent in] (never "Map Pool Updates Ascent").
- miss: `<li>Map Pool Updates</li><li>Ascent joins the Competitive rotation.</li>` → `is_miss` True.
- listing: the fixture yields exactly the 3 patch-notes items with absolute URLs and dates.

Check: `$PY -m pytest tests/test_patch_notes.py -q` passes; red first.

## S2. Pool history core (`app/services/map_pool.py`, pure part)

Files: `app/services/map_pool.py` (new); `tests/test_map_pool.py` (new). Depends: none.

Interface:
```python
BLEED_DAYS = 3              # PROVISIONAL(D3)
SAFETY_NET_MIN_MATCHES = 3  # PROVISIONAL(D3)

@dataclass(frozen=True)
class Change:
    map_name: str; in_pool: bool; effective_at: datetime; patch: str; source: str  # "seed" | "patch_notes" | "safety_net"

class PoolHistory:
    def __init__(self, changes: Iterable[Change]): ...  # sorted by (effective_at, map_name); exact duplicates (map, in_pool, effective_at) fold to one
    changes: tuple[Change, ...]
    def pool_at(self, t: datetime) -> frozenset[str]        # maps whose latest change at or before t is IN
    def current_pool(self, now: datetime | None = None) -> frozenset[str]
    def intervals(self, map_name: str) -> list[tuple[datetime, datetime | None]]  # in-pool [start, end)
    def in_pool(self, map_name: str, t: datetime) -> bool
    def latest_change_at(self, now: datetime | None = None) -> datetime | None
    def with_safety_net(self, matches: Iterable[tuple[str, datetime]]) -> "PoolHistory"
```
Safety net, per map, per out-of-pool interval (from an OUT change, or from the earliest time, to the next IN or
open): the matches on that map inside it, excluding those within `BLEED_DAYS` after the interval's OUT change; if at
least `SAFETY_NET_MIN_MATCHES`, add `Change(map, True, first_such_match, "safety-net", "safety_net")` (and, if the
interval had a later IN, nothing else is needed since that IN follows). Never adds an OUT.

Tests: `pool_at` before/at/after a change (at = the change applies); fold of duplicates; `intervals` for out-and-back;
safety net: 3 strays in one interval add the map from the first stray; 2 don't; 2 + 2 in separate intervals don't;
strays within 3 days after the OUT are ignored (bleed), the same strays at day 4 count.

Check: `$PY -m pytest tests/test_map_pool.py -q`; red first.

## S3. Seed build (`scripts/build_map_pool_seed.py`) and the committed seed

Files: `scripts/build_map_pool_seed.py` (new); `scripts/map_pool_corrections.json` (new); `app/data/map_pool_seed.json`
(new, generated); `tests/test_build_map_pool_seed.py` (new); `tests/fixtures/patch_notes/wiki_maps_history.txt`
(copy of `RES/wiki_maps_history.txt`). Depends: S1 (parser for 13.00+), S2 (`Change`).

Script: `build_map_pool_seed.py [--cache DIR] [--out PATH]`. Steps per the design ("Build steps"): fetch the wiki
section wikitext (MediaWiki API, `action=parse&page=Maps&prop=wikitext&section=8&format=json`), `parse_wiki_history
(wikitext) -> (columns: list[str], pools: dict[str, set[str]], releases: dict[str, str])` (cell background
`#9eff9e` in, `#ffc7c7` out, `gray` with colspan = unreleased; tolerate `colspan="3;"` and short rows; skip columns
with no filled cell); apply corrections (JSON list of `{"patch", "after_patch"?, "add"?: [...], "remove"?: [...],
"published_at"?, "quote", "url"}`: the late-entry cases and 9.0/9.01/9.02, from `RES/pool_history_draft.json`'s
notes and `RES/patch_pool_sentences.json`); date columns from the patch pages' JSON-LD `datePublished` (URLs from the
listing items, else the `valorant-patch-notes-<maj>-<min>/` pattern with the known exceptions); for listing patches
after the wiki's last filled column parse with `parse_patch_notes` and apply; diff consecutive pools into changes;
release INs for maps released before 5.0 at their release patch dates (1.0 = 2020-06-02, 1.10 = 2020-10-13, 2.08 =
2021-04-27, 3.05 = 2021-09-08; beta maps at 1.0); pre-5.0 OUTs: Split OUT at 5.0. Pearl's IN at 5.0's live day
2022-06-22 (wiki). Writes the design's JSON shape with `patch_notes` = every patch page it read. Polite: User-Agent
`valo-with-friends-tracker map-pool seed (+https://valowithfriendstracker.onrender.com)`, 20 s timeout, 1 s between
requests; `--cache` reuses saved pages.

Run it once (network read-only; `--cache "RES/raw"` may be used), then check the result.

Tests (offline): `parse_wiki_history` on the fixture gives 5.0's pool {Ascent, Bind, Breeze, Fracture, Haven,
Icebox, Pearl} and 12.08's {Ascent, Breeze, Fracture, Haven, Lotus, Pearl, Split}, skips 13.00+, and the release
patches; corrections applied to a small pool set; the diff into changes. And one test over the committed seed:
`PoolHistory(seed).pool_at(t)` equals each `RES`-draft pool for every entry from 6.0 to 13.08 (the expected table is
written into the test, not read from RES), and `current_pool` = {Abyss, Bind, Corrode, Haven, Lotus, Summit, Sunset}.

Check: `$PY -m pytest tests/test_build_map_pool_seed.py -q`; the seed file exists and passes the history test.

## S4. Model + migration 0023

Files: `app/models/map_pool.py` (new; `MapPoolChange`, `MapPoolPatchNotes` per the design's tables, added to
`app/models/__init__.py`); `alembic/versions/0023_map_pool.py` (new; `down_revision = '0022'`; `SEED_AT_0023` = the
seed's changes and patch_notes frozen as a Python literal in the module; upgrade creates both tables + the unique
constraint + index and bulk-inserts seed rows with `source='seed'` via `ON CONFLICT DO NOTHING`
(`sqlalchemy.dialects.postgresql.insert`); downgrade drops both); `tests/test_migration_0023.py` (new). Depends: S3.

Tests: module's `(revision, down_revision) == ("0023", "0022")`; on `VALO_TEST_DATABASE_URL` (skip when unset, the
`tests/_postgres.py` helper): in one rolled-back transaction, upgrade → counts equal the seed's, downgrade → tables
gone, upgrade → same counts. Also check by hand on the throwaway DB:
`VALO_TEST_DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/afk_map_pool_test` — first bring it
to 0022 (`DATABASE_URL=<that url> $PY -m alembic upgrade 0022`), then `upgrade head`, `downgrade 0022`,
`upgrade head`, recording the row counts after each up in LOG.md. Leave it at head.

Check: `$PY -m pytest tests/test_migration_0023.py -q` with the env var set (passes, not skipped); the manual
up/down/up counts equal.

## S5. Loader (`load_pool_history`) with fallback and memo

Files: `app/services/map_pool.py` (add); `tests/test_map_pool_loader.py` (new, sqlite via
`Base.metadata.create_all(engine, tables=[...])` like `tests/replays/test_control_feature_artifacts_db.py:18`, plus
`Match` for population matches). Depends: S2, S3, S4.

Interface: `seed_history() -> PoolHistory` (reads `app/data/map_pool_seed.json` once, cached);
`load_pool_history(db) -> PoolHistory` = seed changes + DB `source='patch_notes'` rows, then `with_safety_net` over
all `(Match.map_name, Match.played_at)`; the DB reads inside `db.begin_nested()`, rolled back on any exception
(logged), falling back to the seed (safety net skipped if the match query failed). Memo: `_POOL_TTL_SECONDS = 300`,
process-level, `clear_pool_cache()` public.

Tests: seed + a fetched row fold (a fetched 13.12 change shows in `current_pool`); seed rows in the DB are ignored
(a bogus `source='seed'` row changes nothing); missing tables → seed pool, and the session runs another query
afterwards; memo hit issues no query (count via an event listener or a mock), TTL expiry recomputes,
`clear_pool_cache` recomputes.

Check: `$PY -m pytest tests/test_map_pool_loader.py tests/test_map_pool.py -q`.

## S6. Map streaks on R1 + template

Files: `app/services/map_streaks.py` (rewrite: `MapStreakWindow(gap, first_at, last_at)`, `MapStreak(map_name,
current, record, record_is_current)`, pure `build_map_streaks(history, player_matches, now=None)`,
`compute_map_streaks(db, player_id)` using `load_pool_history`; remove Act, `_act_pools`, `_map_windows`,
`_gap_in_window`, the act memo, the `map_prediction` import); `app/templates/players/detail.html:45-80` (copy per
design D5, record date line from `first_at`/`last_at` as "Mon YYYY – Mon YYYY" (one month if equal), none when gap
0, new empty state); `tests/test_map_streaks.py` (rewrite: drop the act/window/cache tests, add the design's R1
tests). Depends: S5. Check `git grep -n "map_streaks\|act_labels\|MapStreakWindow" -- app tests scripts` for other
users first.

R1 rules as in the design's "Map streaks" + "Output fields". A play on the map always resets, even while it is
listed out (bleed). Row shown when the player has at least one match on the map or while it was in the pool.

Tests: the design's R1 list. Plus a router smoke test if one exists for the player page (`git grep -l
"map_streaks" -- tests`).

Check: `$PY -m pytest tests/test_map_streaks.py tests/test_match_streaks.py -q` and every test file that
`git grep -l -E "map_streaks|players/detail|routers.players" -- tests` names.

## S7. Map prediction on the real pool

Files: `app/services/map_prediction.py` (`get_current_map_pool(db)`: pool = `load_pool_history(db).current_pool()`;
counts per pool map of matches with `played_at >= history.latest_change_at()`; weight = (count + 1) / sum(count + 1)
with `# PROVISIONAL(D7)`; drop `MIN_POOL_MATCHES`, `_current_act_start`, `SEASON_ACTS_PATH` if nothing else imports
them, `git grep` first); `tests/test_map_prediction.py` (new). Depends: S5.

Tests: a pool map with zero matches gets 1/sum; a non-pool map with matches gets nothing; matches before the latest
change are not counted; `predict_map_probabilities` still sums to 1 over pool maps (mock or sqlite).

Check: `$PY -m pytest tests/test_map_prediction.py -q` and every file `git grep -l map_prediction -- tests` names.

## S8. Refresh, switch, CLI

Files: `app/services/map_pool_refresh.py` (new); `app/config.py` (`map_pool_auto: bool = False` with a comment);
`app/main.py` lifespan (start it beside the archive sync); `render.yaml` (`MAP_POOL_AUTO` `"true"` on
`valowithfriendstracker` only, with a one-line comment); `webapp/.env.example` (`# MAP_POOL_AUTO=false`);
`CLAUDE.md` (one short paragraph under the webapp section); `scripts/map_pool.py` (new: `show [--at ISO]`,
`refresh`, `seed`); `tests/test_map_pool_refresh.py` (new, sqlite + a fake fetcher). Depends: S1, S5.

Interface: `enabled() -> bool` (`settings.map_pool_auto and not settings.demo_mode`); `check(session_factory,
fetch: Callable[[str], str], now=None) -> dict` (counts: listed, new, read, changes, misses, given_up, errors);
`run_forever(session_factory, fetch, stop, every_s=86400, first_delay_s=60)`; `start(session_factory) ->
threading.Event | None` (None when not enabled); `http_fetch(url)` (urllib, the User-Agent above, 20 s timeout).
Design rules: listing → patch-notes items published after the seed's newest `published_at`, unread; newest first; at
most 5 pages; each page's changes (dated at the page's `datePublished`, falling back to the listing date) and its read
row in one transaction; misses stored and retried up to 7 days after publish, then `given_up` logged once and the
row's `last_error` says so; `ON CONFLICT DO NOTHING` on both tables; `clear_pool_cache()` after storing a change;
every exception caught and logged.

Tests: newest first and the cap; a miss stored, retried next check, given up after 7 days; a no-change page read with
`change_count` 0 and not fetched again; a stored change appears in `load_pool_history`; a second identical insert
doesn't raise; `start` returns None in demo mode and when unset; a fetch error leaves everything unchanged; the memo
is cleared on a stored change.

Check: `$PY -m pytest tests/test_map_pool_refresh.py -q` and the pin test `tests/replays/test_heights_remote.py`
(reads `render.yaml`).

## S9. Real-data before/after check (run folder only, no repo commit except none)

Files: `RUN/realdata_check.py` (run folder, not the repo). Depends: S6.

For 3 tracked players with the most local matches (names from `scripts/tracked_players.json`, local DB read-only,
`DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/valorant_igl_tutor`): the old streak table
(the base's `map_streaks.py` from `git show 571e656:webapp/app/services/map_streaks.py` loaded as a module) and the
new one, per map: current, record, record dates; plus the safety-net additions on local data (expected none) and the
out-of-pool match counts. Writes `RUN/realdata_before_after.md`.

Check: the file exists with 3 players' tables; any safety-net addition is explained.

## Order and segments

S1 → S2 → S3 → S4 → S5 → (S6, S7, S8 in that order) → S9. Full-suite checkpoint after S5 and at wrap-up.
