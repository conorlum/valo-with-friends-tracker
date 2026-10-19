# Wide layout: match page first, then the other long pages

Branch `worktree-replay-teams-left` (worktree `.claude/worktrees/replay-teams-left`), one PR. Already on it
(uncommitted): the replay viewer's players panel moved left of the map, team 1 above team 2, names unclipped,
the page's width caps removed.

## Decisions (from the 2026-10-19 grilling)

- Design for 2560x1440. Content caps at ~2400px; anything wider is blank margin. Scale down at ~1920 (same
  rows), one column per pair below ~1300px, one section per row on phones (<700px).
- Match page order: what everyone did first, "things Valorant's summary doesn't show" next, in-depth lowest.
- Match page content changes: only the Cumulative Impact chart goes. Everything else keeps its numbers.
- Economy keeps its tier grid, smaller, Team 1 and Team 2 side by side instead of on tabs.
- Other pages: made as similar to the match page as possible; screenshots shown before done; any section
  removal or shrink is asked, not decided.

## Target layout (match page, 2560 wide)

```
┌──────────────── result banner (full width) ────────────────┐
├────────── Scoreboard (~60%) ──────┬──── Shoutouts (~40%) ───┤
├── Match Data Highlights ──────────┬── Round win rate by     ┤
│── Economy: Team 1 | Team 2 grids  │   game state            │
├──────── Rounds: round strip (full width) ───────────────────┤
│   kill table                       │  round Impact card      │
└─────────────────────────────────────────────────────────────┘
```

## Step 1: shared wide-page CSS (`app/static/css/style.css`)

- `main:has(.page-wide) { max-width: 2400px; }` -- the opt-in for a page; normal pages keep 1040px.
- Layout helpers, one set reused by every page in this PR:
  - `.page-row` -- a grid row of cards, `gap` matching the card spacing (cards drop their own bottom margin
    inside it).
  - `.page-row--60-40` (`minmax(0, 3fr) minmax(0, 2fr)`), `.page-row--halves` (`repeat(2, minmax(0, 1fr))`).
    Never a bare `1fr`: its minimum is the content's intrinsic width, so a wide table would push the column
    (and the page) past the viewport.
  - `.page-stack` -- a column of cards inside one grid cell.
  - `.page-row > *, .page-stack > * { min-width: 0; }` so no grid child grows to its content.
  - Every table inside a row sits in `.table-scroll` (scrolls inside its card). Already wrapped: the match
    scoreboard, recent matches, the squad table. To wrap: the sessions Impact Leaderboard, the round detail's
    two tables, and any other unwrapped table found while moving a card into a row.
- `@media (max-width: 1300px)`: every `.page-row` becomes one column. `@media (max-width: 700px)`: existing
  phone rules apply unchanged.
- Charts fixed at `max-width: 620px` inline (Highlights canvas, game-state SVG) stay as they are; they centre in
  their cell.

## Step 2: match page (`templates/matches/detail.html`)

1. Wrap the page in `<div class="page-wide">`.
2. Row 1 (`.page-row--60-40`): Scoreboard card | Shoutouts card. No shoutouts (none for this viewer): the
   scoreboard takes the full row.
   Shoutouts reflow: the cards are grouped server-side by `balanced_rows` (`app/templates.py`, max 4 per row) into
   `.shoutout-row` flex rows that wrap, so a group of 4 in a 40% column would wrap 3 + 1. In the side column:
   - group with `balanced_rows(3)` (10 cards -> 3,3,2,2; 5 -> 3,2; 4 -> 2,2), via a `.shoutout-rows--side`
     modifier on the container;
   - above 700px, a side row does not wrap: `.shoutout-rows--side .shoutout-card { flex: 1 1 0; min-width: 0; }`
     so its 2-3 cards share the row's width (at ~940px, about 300px each; at the 1300px breakpoint the column
     is full width and still 3 per row);
   - at 700px and below, the existing rule (two per row, centred) applies; an odd group's last card is centred
     alone, as today.
   Sessions and squad keep `balanced_rows` at 4 unless their shoutouts also move into a narrow column.
3. Row 2 (`.page-row--halves`): left `.page-stack` = Match Data Highlights card, then the Economy card; right =
   Round win rate by game state card. No game-state graph: the left stack takes the row.
4. Economy card: drop the team tabs and their script; render both `_econ_tier_matrix_body.html` includes side
   by side (`.page-row--halves` inside the card, each with its team's heading and the existing
   `team-panel-team-N` tint), with the cell text and padding tightened. The per-team explanation line becomes
   one line for both. Only the match page changes: `_econ_tier_matrix.html` (player page, sessions) is untouched.
5. Row 3: the Rounds card stays full width. `matches/_round_detail.html`: wrap the kill table and the Impact
   card in a two-column grid (kill table left, Impact card right), one column below 1300px. The same partial is
   returned by the round-click route (`routers/matches.py:140`), so the swap keeps the layout.
6. Remove Cumulative Impact: the card (`detail.html` ~262-274), its script (`chartData`, the `impact-chart`
   Chart and the selection/clear handlers), `chart_data` in `routers/matches.py`, `cumulative_impact_series` in
   `services/matches.py` and its test in `tests/test_match_page_result.py`. Grep for any other caller first.

## Step 3: the other pages, proposed to match

Same opt-in class, same row helpers. Proposed pairings (most-viewed top left, deeper charts lower); anything
that would remove or shrink a section is a question for the owner before it's built.

- **Player page** (`players/_profile_sections.html`, `players/detail.html`): inside the scoped sections (both
  the Recent include and the Career fragment, since they share `_profile_sections.html`): form card | Match Data
  Highlights; Round win rate by game state | Kill order graph; Fight-EV full width; Economy | Pistol stats;
  Maps full width; Kill vs death impact by match full width.
  Map streaks is not paired with Maps. It is included after `#recent-sections`/`#career-sections`
  (`players/detail.html:79`) on purpose: it ignores Recent/Career, still shows when the agent filter matches
  nothing (`filter_empty`), and the `/career` fragment doesn't supply its data. It stays where it is, as its own
  full-width card under whichever scope is showing; only its width changes.
  Charts: the Career charts are built after the fragment is visible (`maybeRenderCareerCharts`) because Chart.js
  in a `display:none` container gets a broken width. Putting canvases in grid cells doesn't change that rule;
  don't add any code that builds them earlier.
- **Sessions** (`sessions/detail.html`): Roster + Impact Leaderboard | Shoutouts; Round win rate by game state |
  Economy; Fun Stats | KDA by match; Matches full width.
- **Squad** (`squad/_squad_sections.html`): form card | Shoutouts; Your friends, ranked full width.
- **/stats** (`stats/_stats_sections.html`): the ten cards in pairs (`.page-row--halves`), wide tables
  (round combos, map control) full width.

## Step 4: verify

- Playwright (scratchpad script, read-only friends DB via `scripts/with_friends_db.py --read-only` and
  `.venv313`) at 2560x1440, 1920x1080, 1280x800 and 390x844 for each page. On every page and size: no
  horizontal page scroll (`document.documentElement.scrollWidth <= innerWidth`), no card wider than its grid
  cell, no console errors. Show the owner the screenshots per page.
- Interactions, not just the first render (each at 2560 and 1280, with a screenshot after):
  - Match page: click a later round in the strip and wait for `#round-detail` to swap; the kill table and the
    Impact card are side by side (2560) / stacked (1280) and the tables scroll inside their card. Switch the
    Highlights player tab and the game-state Team tab; both charts redraw at a non-zero width. Shoutouts:
    a logged-out view (10 cards -> rows 3,3,2,2, no row wraps) and a logged-in view.
  - Player page: switch to Career, wait for the fragment, check its charts have a non-zero width (the
    "Kill vs death impact by match" bar chart especially) and the Fight-EV diamond draws; switch back to Recent.
    An agent filter with no matches: Map streaks still shows. Account toggle on a player with accounts.
  - Sessions, squad, /stats: their tabs/toggles (Friends / All Players on /stats) after the move.
  - Replay viewer (already in this PR): at 2560 and 1920 the players panel is left of the map, team 1 above
    team 2, as tall as the map, no clipped names; at 1280 it sits under the map; at 390 one column. Play a few
    seconds and step a round: the panel updates and the layout doesn't jump. Also the coaching page
    (`/replays/<uuid>/coach`) with a report: three columns, the coach tabs readable.
- Demo check: the same pages on the demo data render (layout only; no data-source change).
- Tests: `tests/test_match_page_result.py` and the template/route tests for the touched pages; then the default
  suite, comparing the failure set against the known baseline (23 known fails).
- Bump `static_version` if the CSS isn't already cache-busted per deploy.

## Out of scope

New stats ("interesting things Valorant doesn't show") -- a later list, not this branch.
