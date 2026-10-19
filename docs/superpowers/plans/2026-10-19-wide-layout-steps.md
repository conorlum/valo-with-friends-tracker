# Wide layout: implementation steps

Steps for `2026-10-19-wide-layout-impl.md` (the design: read it for the target layout). Each step is one
commit, sized for one implementer, with a yes/no check. Paths are under `webapp/` unless they start with
`<run>` (the AFK run folder, outside the repo: page checks and screenshots name real players, so they never go
in the repo).

Test commands run from `webapp/` with `VALO_TEST_DATABASE_URL` set, `-p no:randomly` not needed, `-n 0` for
single files.

## How sections are kept (R3, R6)

Two checks, used by every page step:

1. **Source check, in the repo** (`tests/test_page_sections.py`, made in S2): for each page template it
   collects every `<h2>` text in the template and, recursively, in every `{% include %}` it names (literal
   names only), and compares the result with a fixed list taken from the base. Each page step's change must
   leave its list equal, except the match page, which loses only `Cumulative Impact` (S6). A section that
   disappears from a template fails this test.
2. **Real-page check, in `<run>`** (`check_pages.py`, made in S1): every `h2` on the live page, before and
   after. After must equal before, except `Cumulative Impact` on the match page.

## Browser check (`<run>/check_pages.py`)

One script, run by the controller for each page check. For each page in `<run>/pages.json` and each size
(2560x1440, 1920x1080, 1280x800, 390x844) it loads the page, runs the page's interactions, and asserts in the
page:
- `document.documentElement.scrollWidth <= innerWidth`;
- every child of a `.page-row` or `.page-stack` has `scrollWidth <= clientWidth + 1` (nothing pokes out of
  its grid cell; tables scroll inside `.table-scroll`);
- every visible `canvas` has a width above 0 after each interaction that shows it;
- no console error and no page error.
It saves a full-page screenshot per page, size and interaction, and writes `<run>/checks/<label>.json` with
the results and the page's `h2` list. Exit code 0 only if every assertion passed. `--sizes`, `--pages` and
`--label` (`before`/`after`) narrow a run. `--no-interactions` takes first-render shots only.

## Steps

### S1. Browser-check script, page list and "before" shots (no repo change)

- Write `<run>/check_pages.py` (Playwright, the 3.11 venv) as above, and `<run>/pages.json`: the match page
  (a match with a linked replay, shoutouts and a coach report if one exists; find it with read-only HTTP:
  `/replays`, the match page's coach link), its replay viewer, its coaching page `/replays/<uuid>/coach`, a
  player page (a player with grouped accounts if one exists: the "All accounts" chip), the same player's
  `?filter=agent:<an agent they never played>` page if reachable (else an agent filter with few matches),
  a session page (`/sessions` → first), `/squad` (logged in as that player; logged out it is a prompt), and
  `/stats`. The interactions per page are the design's "Step 4: verify" list; each is skipped with a note
  when its control isn't on the page (the before pages still have the economy tabs, for example).
- Start the read-only dev server (`web_local.sh`, see `PROMPT.md`), then run `--label before
  --no-interactions --sizes 2560x1440,1280x800`.
- Check: `<run>/shots/before/` has a 2560 and a 1280 shot of every page in `pages.json`; `<run>/checks/before.json`
  has a non-empty `h2` list for every page; the script's own assertions ran (pass or fail recorded; a fail
  on the base is recorded, not fixed).

### S2. Shared wide-page CSS and the sections test

- `app/static/css/style.css`, after the `main` rule (`style.css:297`): `main:has(.page-wide) { max-width:
  2400px; }`; `.page-row` (grid, gap equal to the card's bottom margin, cards inside drop their margin),
  `.page-row--60-40` (`minmax(0, 3fr) minmax(0, 2fr)`), `.page-row--halves` (`repeat(2, minmax(0, 1fr))`),
  `.page-stack` (a column of cards in one cell, same gap), `.page-row > *, .page-stack > * { min-width: 0; }`;
  `@media (max-width: 1300px) { .page-row { grid-template-columns: minmax(0, 1fr); } }`. No bare `1fr` in a
  `.page-row` rule. The `.table-scroll` rule exists (`style.css:1043`): reuse it.
- `tests/test_page_sections.py`: the source check above, with the base's list for each of the match page,
  `players/detail.html`, `sessions/detail.html`, `squad/detail.html`, `stats/detail.html` (taken by the test's
  own collector from the base templates, then pasted as literals); and a CSS test: the rules above exist,
  every `grid-template-columns` inside a `.page-row…` rule uses `minmax(0,` (no bare `1fr`), and the 1300 px
  media query sets one column.
- Check: `pytest tests/test_page_sections.py -n 0` passes; it fails if one `<h2>` line is deleted from
  `sessions/detail.html` (shown by the implementer once, then restored) and if `.page-row--halves` is changed
  to `repeat(2, 1fr)`.

### S3. Match page rows 1 and 2, shoutouts in the side column

- `templates/matches/detail.html`: wrap the page in `<div class="page-wide">`. Banner full width. Row 1
  `.page-row--60-40`: Scoreboard card (`:87`) | Shoutouts card (`:150`), with its script kept after the row;
  no shoutouts → the scoreboard is the row's only child and spans it (`.page-row--solo` or a conditional
  class: one column). Row 2 `.page-row--halves`: `.page-stack` of Match Data Highlights (`:237`) and the
  Economy card (`:277`, moved here unchanged, inside still on tabs until S4) | Round win rate by game state
  (`:318`); no game-state graph → the stack spans the row. Scripts move with their cards, unchanged.
- Shoutouts in the side column: `shoutouts | balanced_rows(3)` and a `.shoutout-rows--side` modifier;
  CSS: above 700 px `.shoutout-rows--side .shoutout-row { flex-wrap: nowrap; }` and
  `.shoutout-rows--side .shoutout-card { flex: 1 1 0; min-width: 0; }`; at 700 px and below the existing
  phone rule applies. Sessions and squad keep `balanced_rows` (4).
- `tests/test_wide_layout_match.py`: render `matches/detail.html` with a small context built from
  `tests/test_match_page_result.py`'s `_player`/`_summary` helpers (`:19`, `:36`) and plain dicts for the rest;
  assert `page-wide`, row 1 holds Scoreboard then Shoutouts, row 2's stack holds Highlights then Economy and
  the right cell holds the game-state card, 10 shoutouts render as rows of 3,3,2,2 in `.shoutout-rows--side`,
  no shoutouts → no Shoutouts card and the scoreboard alone in row 1; the same render with `demo_mode=True`.
- Check: the new test file, `tests/test_page_sections.py`, `tests/test_shoutouts.py`,
  `tests/test_match_page_result.py` pass.

### S4. Economy: both teams side by side

- `templates/matches/detail.html` Economy card: drop the team tab row and its script (`:280-283`, `:300-315`);
  inside the card a `.page-row--halves` of two cells, each `team-panel team-panel-team-N` with a small team
  heading (`h3`, `team-name-team-N`, with the You/Friends tag as the tab had it) and its
  `_econ_tier_matrix_body.html` include. One explanation line above both. Tighten the grid for this card only
  (a modifier class on the card, e.g. `.econ-compact`: smaller cell font and padding). `_econ_tier_matrix.html`
  and `_econ_tier_matrix_body.html` are not changed.
- Test (in `tests/test_wide_layout_match.py`): both team grids render in the same card, no `data-econ-team-tab`
  button, each grid under its team's heading; one team with no rounds still renders the other.
- Check: that file, `tests/test_page_sections.py`, `tests/test_economy_graphs.py` pass.

### S5. Rounds: kill table left, round Impact right

- `templates/matches/_round_detail.html`: keep the `h3` (`:1`) on top; wrap the kill table (`:11`) and the
  Impact card (`:50`, with its per-player table `:61`) in a two-cell grid (`.round-detail-cols`, kill table
  left, Impact card right, `minmax(0, …)` columns, one column below 1300 px); both tables in `.table-scroll`.
  The Rounds card stays full width in `detail.html`.
- Test: render `_round_detail.html` through the round-click route function (`routers/matches.py:136`) with
  `get_match_or_404`, `get_round_detail` and `replay_url_for_match` monkeypatched on the router module and
  `TemplateResponse` captured, as `tests/replays/test_gaps_web.py:219` does; assert the two columns, the kill
  table in the left and the Impact card in the right, and both tables inside `.table-scroll`. Also the same
  partial inside the full match page render.
- Check: the test file, `tests/test_page_sections.py`, and `git grep -l get_round_detail -- tests` pass.

### S6. Remove Cumulative Impact

- Delete the card (`detail.html:262-274`) and its script (the `chartData` / `impact-chart` code and its
  selection/clear handlers; find them by `impact-chart`), `chart_data` in `routers/matches.py:76-80,119`,
  `cumulative_impact_series` in `services/matches.py:398` and its import, and its test
  (`tests/test_match_page_result.py:124`). `git grep -n "cumulative_impact_series\|impact-chart\|chartData"`
  finds nothing else after (the player page's own `chart_data` stays).
- Update `tests/test_page_sections.py`'s match list (drop `Cumulative Impact`) and assert it is absent.
- Check: `git grep` above is empty; `tests/test_page_sections.py`, `tests/test_wide_layout_match.py`,
  `tests/test_match_page_result.py` pass.

**Checkpoint 1: the full suite, in the baseline's parts, with the known failures deselected.**

### S7. Browser check: match page, replay viewer, coaching page (controller)

- Restart the dev server; `check_pages.py --label after --pages match,replay,coach` at all four sizes with
  interactions. Compare the match page's `h2` list with `before` (only `Cumulative Impact` gone).
- Check: exit 0; the shots exist; the replay viewer's panel is left of the map at 2560 and 1920, under it at
  1280 (by the panel's and canvas's bounding boxes, recorded in the JSON). Failures go back as a fix round
  to the step that caused them.

### S8. Player page

- `templates/players/_profile_sections.html` (shared by Recent and the `/career` fragment): rows as the design
  says: `_form_card.html` include (`:50`) | Match Data Highlights (`:52`); Round win rate by game state
  (`:123`) | Kill order graph (`:147`); Fight-EV (`:227`) full width; Economy (`_econ_tier_matrix.html`
  include, `:287`) | Pistol (`_pistol_match_stats_card.html`, `:291`); Maps (`:293`) full width; Kill vs death
  impact by match (`:323`) full width. A card left alone when its partner is absent spans its row. Wide tables
  in `.table-scroll`. `players/detail.html`: the `page-wide` opt-in; Map streaks (`:79`) stays where it is, full
  width. The Career chart rule (`maybeRenderCareerCharts`) is not changed.
- Test: `tests/test_page_sections.py` unchanged list passes; a render of `_profile_sections.html` (using the
  context `tests/test_player_filter_page.py` builds, or its route with `TemplateResponse` captured) shows the
  rows, with and without `demo_mode`.
- Check: those, plus `tests/test_map_streaks.py`, `tests/test_player_filter_page.py`,
  `tests/test_account_groups.py`, `tests/test_account_groups_routes.py`.

### S9. Browser check: player page (controller)

- As S7 for `player` and `player-filter`: Career switch (fragment loaded, its canvases above 0 wide, the
  Fight-EV diamond drawn), back to Recent, the empty filter still shows Map streaks, the account chip.

### S10. Sessions

- `templates/sessions/detail.html`: `page-wide`; Roster (`:31`) + Impact Leaderboard (`:52`, its table into
  `.table-scroll`) in a `.page-stack` | Shoutouts (`:89`, `balanced_rows` stays 4 unless the shots show a
  wrapped row: then `balanced_rows(3)` and the side modifier, as the match page); Round win rate by game state
  (`:138`) | Economy (`:208` include); Fun Stats (`:212`) | KDA by match (`:297`, table in `.table-scroll`);
  Matches (`:329`) full width.
- Test: sections list unchanged; a render of the page (route with `TemplateResponse` captured, or a direct
  render) shows the rows, also with `demo_mode=True`.
- Check: those, plus `tests/test_shoutouts.py` and the session tests `git grep -l "sessions/detail" -- tests`
  finds.

### S11. Squad and /stats

- `templates/squad/detail.html` + `_squad_sections.html`: `page-wide`; `_form_card.html` (`:23`) | Shoutouts
  (`:26`); Your friends, ranked (`:56`) full width.
- `templates/stats/detail.html` + `_stats_sections.html`: `page-wide`; the ten cards in `.page-row--halves`
  pairs in their current order, except the round-combo card and the map-control card, each full width. The
  pairs follow the order: pistol | eco follow-up, force buy | enemy at 11, round combo (full), round streak |
  halftime, score reached | map side, map control (full).
- Test: sections lists unchanged; renders show the rows, also with `demo_mode=True`.
- Check: those, plus `tests/test_squad.py`, `tests/test_map_control_card.py` (pin: reads
  `_stats_sections.html`), `git grep -l "_stats_sections" -- tests`.

**Checkpoint 2: the full suite, in parts.**

### S12. Browser check: sessions, squad, /stats (controller)

- As S7 for `session`, `squad`, `stats`, with their toggles (Recent/Career on squad, Friends/All Players on
  /stats, logged in).

### S13. PR body and cards (controller)

- `<run>/PR-BODY.md`: what changed per page, the one removal, the tests, the browser checks' result, the
  screenshots described (not attached: they name players). The page cards for the review.
- Check: the file exists and names every page.
