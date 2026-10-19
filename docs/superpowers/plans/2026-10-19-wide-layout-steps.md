# Wide layout: implementation steps

Steps for `2026-10-19-wide-layout-impl.md` (the design: read it for the target layout). Each step is one
commit, sized for one implementer, with a yes/no check. Paths are under `webapp/` unless they start with
`<run>` (the AFK run folder, outside the repo: page checks and screenshots name real players, so they never go
in the repo). Revised after the P4 review (findings 1-20, all applied).

Test commands run from `webapp/` with `VALO_TEST_DATABASE_URL` set, `-n 0` for single files.

## Layout rules every step follows

- Rows are `.page-row` (+ `--60-40` or `--halves`), a column of cards in one cell is `.page-stack`. A row or
  stack holds only cards (or a stack): every `<script>` stays **outside** rows and stacks, so that
  `.page-row > :only-child` (a row where the partner card didn't render) spans the full row.
- Every table that sits in a row cell is inside `.table-scroll`.
- Render tests use `templates.env.overlay(undefined=StrictUndefined)` (or assert each card's `h2` inside its
  expected cell): with the app's silent `Undefined` (`app/templates.py:26`) a sparse context renders nothing
  and a class-name test passes on an empty page.

## How sections are kept (R3, R6)

1. **Source check, in the repo** (`tests/test_page_sections.py`, made in S2): for each page template it
   collects every `<h2>` text in the template and, recursively, in every `{% include %}` with a literal name,
   as a multiset (`Counter`: `/stats` includes `_stats_sections.html` twice, `stats/detail.html:28,50`), and
   compares it with a fixed multiset taken from the base. Order is not compared (rows reorder cards). Only
   the match page may lose one entry, `Cumulative Impact` (S6).
2. **Real-page check, in `<run>`** (`check_pages.py`): the first-render `h2` multiset of each page, before and
   after, equal except `Cumulative Impact` on the match page.

## Browser check (`<run>/check_pages.py`)

One script, run by the controller. For each page in `<run>/pages.json` and each size (2560x1440, 1920x1080,
1366x768, 1280x800, 390x844):
- logs in first when the page says so (POST `/login` with `display_name`, `routers/auth.py:19-23`, cookie only:
  works on the read-only DB) and asserts the page shows "Logged in as" and its final URL is the one asked for
  (`/squad` redirects to `/login` logged out, `routers/squad.py:19-20`; `/stats` to `/stats/all`,
  `routers/site_stats.py:59-60`);
- waits until no element has `htmx-request` and no "Loading" placeholder is left (recent rows in
  `_form_card.html:35`, `#career-sections`, the `/stats` fragments);
- asserts: `document.documentElement.scrollWidth <= innerWidth`; every `.card` and every child of a
  `.page-row`, `.page-stack` or `.round-detail-cols` has `scrollWidth <= clientWidth + 1`; at 1920 and above,
  each row's children sit side by side (different left x), and at 1280 and below they share one left x;
  every canvas whose parent passes `checkVisibility()` has a width above 0, and each visible game-state SVG
  too; per-page minimum counts where given (below); no console or page error;
- records (not asserted) every `.table-scroll` / `.chart-scroll` whose `scrollWidth > clientWidth`, per size,
  and each card's bounding box, for review against the shots;
- runs the page's interactions, with a screenshot after each, and re-asserts after each;
- saves full-page screenshots to `<run>/shots/<label>/<page>-<w>x<h>[-<interaction>].png` and
  `<run>/checks/<label>.json`.
Pass for an `after` run = **no new failure** compared with `before.json` for the same page and size (the base
may already have some; they go in the PR body). `--sizes`, `--pages`, `--label`, `--no-interactions` narrow a
run.

## Steps

### S1. Browser-check script, page list and "before" shots (no repo change)

- Write `<run>/check_pages.py` (Playwright, the 3.11 venv) and `<run>/pages.json`, finding every id with
  read-only HTTP in the same step: the match page (one with a linked replay, shoutouts and a coach report if
  one exists: `/replays`, the match page's coach link), its replay viewer, its coaching page
  `/replays/<uuid>/coach`, a player page (one with grouped accounts if one exists: the "All accounts" chip),
  the same player's `?filter=role:<a role they never played>` page (`filter_empty`; an unknown agent key is
  ignored, `tests/test_player_filter_page.py:108-146`), a session page, `/squad` and `/stats` (both logged in as
  that player), and `/stats/all`.
- Interactions per page (each skipped with a note when its control isn't on the page):
  - match: Scoreboard "By team" toggle and one column sort; a later round in the strip (wait for
    `#round-detail` to swap); the Highlights player tab; the game-state Team tab; logged out and logged in;
  - player: Career (assert at least 3 canvases under `#career-sections` and child nodes in
    `#fight-ev-svg-career`), back to Recent, the account chip if there is one;
  - player-filter: the page shows "No matches as", has no `#recent-sections`, and still shows Map streaks;
  - session, squad (Recent/Career), stats (Friends / All Players);
  - replay: at 2560/1920 the players panel's box is left of the map canvas's box, at 1280 below it; play 3 s
    and step a round, the panel's box doesn't move by more than a few px;
  - coach: the coach tabs are visible and readable (their box inside the viewport).
- Start the read-only dev server (`web_local.sh`, see `PROMPT.md`), run `--label before --no-interactions`
  at all five sizes (the h2 multisets, and the base's failures to compare against), keeping the 2560 and
  1280 shots for the cards.
- Check: `<run>/shots/before/` has a 2560 and a 1280 shot of every page; `<run>/checks/before.json` has a
  non-empty `h2` multiset for every page and the logged-in pages passed their login assertion.

### S2. Shared wide-page CSS and the sections test

- `app/static/css/style.css`, after the `main` rule (`style.css:297`): `main:has(.page-wide) { max-width:
  2400px; }`; `.page-row` (grid, gap equal to the card's bottom margin; cards inside drop their margin),
  `.page-row--60-40` (`minmax(0, 3fr) minmax(0, 2fr)`), `.page-row--halves` (`repeat(2, minmax(0, 1fr))`),
  `.page-row > :only-child { grid-column: 1 / -1; }`, `.page-stack` (a column of cards in one cell, same gap),
  `.page-row > *, .page-stack > * { min-width: 0; }`; then, **after** those rules,
  `@media (max-width: 1300px) { .page-row, .page-row--60-40, .page-row--halves { grid-template-columns:
  minmax(0, 1fr); } }`. Reuse `.table-scroll` (`style.css:1043`).
- `tests/test_page_sections.py`: the source check above, with the base's multisets for `matches/detail.html`,
  `players/detail.html`, `sessions/detail.html`, `squad/detail.html`, `stats/detail.html` (taken by the test's
  own collector on the base, pasted as literals); and a CSS test: the rules exist, every
  `grid-template-columns` in a `.page-row…` rule uses `minmax(0,` (no bare `1fr`), the `:only-child` rule
  exists, and the 1300 px media rule names the modifiers or comes after them in the file.
- Check: `pytest tests/test_page_sections.py -n 0` passes; the implementer shows it fails once each when an
  `<h2>` line is deleted from `sessions/detail.html` and when `.page-row--halves` uses `repeat(2, 1fr)`, then
  restores both.

### S3. Match page rows 1 and 2, Rounds below them, shoutouts in the side column

- `templates/matches/detail.html`: wrap the page in `<div class="page-wide">`. Banner full width. Row 1
  `.page-row--60-40`: Scoreboard card (`:87`) | Shoutouts card (`:150`); the Scoreboard's script (`:120-147`)
  after the row. Row 2 `.page-row--halves`: `.page-stack` of Match Data Highlights (`:237`) and the Economy
  card (`:277`, moved unchanged, still on tabs until S4) | Round win rate by game state (`:318`). Then the
  **Rounds card and its script (`:180-235`) move after row 2**, full width. The Cumulative Impact card
  (`:262`) stays as its own full-width card after Rounds until S6. Scripts move with their cards, outside
  rows. A missing partner (no shoutouts, no game-state graph) is handled by `:only-child`.
- Shoutouts in the side column: `shoutouts | balanced_rows(3)` and a `.shoutout-rows--side` modifier;
  CSS: above 700 px `.shoutout-rows--side .shoutout-row { flex-wrap: nowrap; }` and
  `.shoutout-rows--side .shoutout-card { flex: 1 1 0; min-width: 0; }`; at 700 px and below the existing
  phone rule applies.
- `tests/test_wide_layout_match.py`: render `matches/detail.html` (StrictUndefined overlay) with a context
  built from `tests/test_match_page_result.py`'s `_player`/`_summary` helpers (`:19`, `:36`) and plain values
  for the rest; assert `page-wide`; row 1 holds Scoreboard then Shoutouts; row 2's stack holds Highlights then
  Economy and its right cell the game-state card; Rounds comes after the game-state card; 10 shoutouts render
  as rows of 3,3,2,2 in `.shoutout-rows--side`; no shoutouts → no Shoutouts card and the Scoreboard alone in
  row 1; no `<script>` inside a `.page-row`; the same render with `demo_mode=True`.
- Check: the new test file, `tests/test_page_sections.py`, `tests/test_shoutouts.py`,
  `tests/test_match_page_result.py`, `tests/test_player_filter_page.py`.

### S4. Economy: both teams side by side

- `templates/matches/detail.html` Economy card: drop the team tab row and its script (`:280-283`, `:300-315`);
  inside the card a `.page-row--halves` of two cells, each `team-panel team-panel-team-N` with a small team
  heading (`h3`, `team-name-team-N`, with the You/Friends tag as the tab had it) and its
  `_econ_tier_matrix_body.html` include. One explanation line above both. Tighten the grid for this card only
  (a modifier on the card, e.g. `.econ-compact`: smaller cell font and padding; the row inside it has no
  extra bottom margin). `_econ_tier_matrix.html` and `_econ_tier_matrix_body.html` are not changed.
- Test (in `tests/test_wide_layout_match.py`): both team grids render in the same card, no
  `data-econ-team-tab`, each grid under its team's heading; one team with no rounds still renders the other.
- Check: that file, `tests/test_page_sections.py`, `tests/test_economy_graphs.py`,
  `tests/test_player_filter_page.py`.

### S5. Rounds: kill table left, round Impact right

- `templates/matches/_round_detail.html`: keep the `h3` (`:1`) on top; wrap the kill table (`:11`) and the
  Impact card (`:50`, with its per-player table `:61`) in a two-cell grid (`.round-detail-cols`, kill table
  left, Impact card right, `minmax(0, …)` columns, one column at 1300 px and below, added to the S2 CSS test);
  both tables in `.table-scroll`.
- Test: call the round-click route function (`routers/matches.py:136`) with `get_match_or_404`,
  `get_round_detail` and `replay_url_for_match` monkeypatched on the router module and `TemplateResponse`
  captured (as `tests/replays/test_gaps_web.py:219`), then **render the captured context** with
  `get_template("matches/_round_detail.html").render(ctx)` (StrictUndefined); assert the two cells, the kill
  table in the left and the Impact card in the right, both tables inside `.table-scroll`. Also the partial
  inside the full match page render.
- Check: the test file, `tests/test_page_sections.py`, and every file `git grep -l get_round_detail -- tests`
  lists.

### S6. Remove Cumulative Impact

- Delete the card (`detail.html`, the `Cumulative Impact` card) and its script (the `chartData` / `"impact-chart"`
  code and its selection/clear handlers), `chart_data` in `routers/matches.py:76-80,119`,
  `cumulative_impact_series` in `services/matches.py:398` and its import (`routers/matches.py:12`), and its test
  (`tests/test_match_page_result.py:124`) with its import (`:15`).
- Check: `git grep -n -e cumulative_impact_series -e chartData -e '"impact-chart' -- webapp` is empty;
  `tests/test_page_sections.py` (match multiset now without `Cumulative Impact`, asserted absent),
  `tests/test_wide_layout_match.py`, `tests/test_match_page_result.py` pass.

**Checkpoint 1: the full suite, in the baseline's parts, with the known failures deselected.**

### S7. Browser check: match page, replay viewer, coaching page (controller)

- Restart the dev server; `check_pages.py --label after --pages match,replay,coach` at all five sizes with
  interactions. The match page's h2 multiset equals `before` minus `Cumulative Impact`.
- Check: no new failure against `before`; the shots exist. Failures go back as a fix round to the step that
  caused them.

### S8. Player page

- `templates/players/_profile_sections.html` (shared by Recent and the `/career` fragment): rows as the design
  says: `_form_card.html` include (`:50`) | Match Data Highlights (`:52`); Round win rate by game state
  (`:123`) | Kill order graph (`:147`); Fight-EV (`:227`) full width; Economy (`_econ_tier_matrix.html`
  include, `:287`) | Pistol (`_pistol_match_stats_card.html`, `:291`); Maps (`:293`) full width; Kill vs death
  impact by match (`:323`) full width. Tables to wrap in `.table-scroll`: Agents played (`:88`), the two swing
  tables (`:188`, `:206`); Maps (`:296`) is full width (wrap it too). `_pistol_match_stats_card.html` is shared
  with `/stats`: a wrapper added inside it shows on both. `players/detail.html`: the `page-wide` opt-in; Map
  streaks (`:79`) stays where it is, full width. `maybeRenderCareerCharts` is not changed.
- Test: `tests/test_page_sections.py` unchanged multiset; a render of `_profile_sections.html` (through the
  context `tests/test_player_filter_page.py` builds, or its route with `TemplateResponse` captured and then
  rendered) asserting each pair's `h2`s in one row, also with `demo_mode=True`.
- Check: those, plus `tests/test_map_streaks.py`, `tests/test_player_filter_page.py`,
  `tests/test_account_groups.py`, `tests/test_account_groups_routes.py`.

### S9. Browser check: player page (controller)

- As S7 for `player` and `player-filter`, with their interactions. Note in the card the full-width "Kill vs
  death impact by match" chart's height at 2560 (it keeps its aspect ratio; not shrunk, R6).

### S10. Sessions

- `templates/sessions/detail.html`: `page-wide`; `.page-stack` of Roster (`:31`) + Impact Leaderboard (`:52`,
  its table `:55` into `.table-scroll`) | Shoutouts (`:89`, `balanced_rows(3)` and `.shoutout-rows--side`, as
  the match page); Round win rate by game state (`:138`) | Economy (`:208` include); Fun Stats (`:212`) | KDA
  by match (`:297`, table `:301` in `.table-scroll`); Matches (`:329`) full width (table in `.table-scroll`).
- Test: a new `tests/test_wide_layout_sessions.py` rendering the page (StrictUndefined, a context built with
  the session service's data classes or plain values) asserting each pair's `h2`s in one row and shoutouts
  grouped by 3; also `demo_mode=True`. Sections multiset unchanged.
- Check: those, plus `tests/test_shoutouts.py`, `tests/test_sessions.py`.

### S11. Squad and /stats

- `templates/squad/detail.html` + `_squad_sections.html`: `page-wide`; `_form_card.html` (`:23`) | Shoutouts
  (`:26`, `balanced_rows(3)` + `.shoutout-rows--side`); Your friends, ranked (`:56`) full width.
- `templates/stats/detail.html` + `_stats_sections.html`: `page-wide`; the cards in `.page-row--halves`
  pairs in their current order: pistol | eco follow-up, force buy | enemy at 11, round combo (full), round
  streak | halftime, score reached | map side, map control (full). Every table in a paired card partial
  goes in `.table-scroll` (`_eco_followup_card.html:55`, `_force_buy_card.html:7`, `_round_streak_card.html:7`
  and every other `<table>` in the paired partials: `git grep -n "<table" -- app/templates/_*_card.html`).
- Test: a render of each (StrictUndefined or h2-in-cell) showing the rows, also with `demo_mode=True`;
  sections multisets unchanged.
- Check: those, plus `tests/test_squad.py`, `tests/test_map_control_card.py` (pin: reads
  `_stats_sections.html`), and every file `git grep -l "_stats_sections" -- tests` lists.

**Checkpoint 2: the full suite, in parts.**

### S12. Browser check: sessions, squad, /stats (controller)

- As S7 for `session`, `squad`, `stats`, `stats-all`, with their toggles.
- Demo mode (D1): a second dev server on another port with `DEMO_MODE=true` against the local corpus copy
  (`valo_groups_corpus` on localhost:5432; never the demo DB), first-render shots of the match page and
  `/stats/all` at 2560 and 390. If it can't start, the PR body says there was no demo browser check.

### S13. PR body and cards (controller)

- `<run>/PR-BODY.md`: what changed per page, the one removal, the tests, the browser checks' result (and the
  base's own failures), the demo check's result, the screenshots described (not attached: they name players).
  The page cards for the review.
- Check: the file exists and names every page.
