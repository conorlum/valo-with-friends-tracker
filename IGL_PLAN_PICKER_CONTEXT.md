# ValorantIGLTutor — IGL Plan Picker App (context for rebuild/continuation)

## What this is

An in-game-leader (IGL) assistant tool for Valorant. Before/during a match, it walks
through: pick map → pick side (Attack/Defense) → shows you 6 candidate strategic
plans (site executes) for the current round, based on the round's economy type
(Pistol / ECO / Full Buy) for both teams → you pick one → it shows the plan's
map-callout image plus a pre-written "pre-round comms" script to read/say to your
team → after the round you log Win/Loss (or "no call" win/loss, meaning the plan
wasn't followed) → the app auto-advances round count, tracks side swap at round 13,
tracks overtime (win by 2 past 24), and uses simple heuristics to guess what economy
type (yours and the enemy's) the next round will be, based on the last round's
outcome and buy type — so the plan suggestions stay relevant without you re-entering
economy state every round.

Found in: `ValorantIGLTutor Before Claude Backup/` (a local backup folder, sibling to
this repo, not itself a git remote worth trusting — treat it as a snapshot to mine
for logic, not a directory to keep editing in place).

## Current implementation (what exists today)

**Stack:** Python 3, `tkinter` (desktop GUI, absolute-pixel `.place()` layout, sized
for ~1550x1080), `Pillow` for image handling, `keyboard` package for global hotkeys
(number keys 1-9, `+`, arrow keys mapped to button actions).

**Single file, single class:** `app.py` — one `Application(tk.Tk)` class, screens are
built by tearing down all widgets (`resetRoot()`) and re-drawing the next screen's
widgets by absolute pixel coordinates. No web server, no persistence layer beyond a
flat JSON file, no tests.

### Screen flow
1. **Map picker** — grid of 11 map thumbnail buttons (`mapThumbnails/*.PNG`), reads
   from `mapNamesToCoordinates` dict. A "Debug Toggle" button top-right controls
   whether match results get written to disk at the end (debug mode = don't persist).
2. **Attack/Defense picker** — two big colored buttons.
3. **Round plan picker** (`generateMapPlanPickerScreen`) — the main screen:
   - Row of 31 round-number cells across the top, each a colored (green=win/red=loss)
     text box filled in as rounds are logged, giving an at-a-glance scoreboard.
   - Current round number + current guessed economy matchup text
     (`"Round: N  ECO VS Full Buy"` etc.)
   - 6 plan-thumbnail buttons in a 3-top/3-bottom grid:
     - Top row = 3 "good" plans matching the *current guessed round type* (Pistol/
       ECO/Full Buy), read from `mapPlans/<Attack|Defense>/<Map>/<RoundType>/*.png`.
     - Bottom row = 3 "default" plans (defense special-cases "Default" → "Full Buy"
       folder), same layout logic.
     - If fewer than 3 images exist in a folder, `getGoodPlans()` pads by repeating
       the first (or first two) images so the grid never breaks — a known hack, not
       a feature.
   - Buttons: Round Win / No-Call Win / No-Call Loss / Round Loss (log outcome),
     Change Plan (re-roll the 6 shown thumbnails), Plan Type Cycle / Enemy Type
     Cycle (manually override the guessed economy state), Back to Map Selector.
   - Clicking a plan thumbnail (`mapPlanButtonAction`) swaps the screen to show that
     plan's full callout image (cropped/resized) plus its paired `.txt` file's
     contents as the "Pre Round Comms" script.
4. **End screen** — Victory.jpg/Defeat.jpg based on win/loss condition (`checkWinLossCondition`:
   first to 13, or win-by-2 in overtime past round 24), plus a text field to name/save
   the match, which (if not in debug mode) appends the full round-by-round log into
   `mapPlans/planData.json` under that map's key.

### Data layout
- `mapThumbnails/<Map>.PNG` — one map thumbnail per map (11 maps: Abyss, Ascent,
  Bind, Breeze, Fracture, Haven, Icebox, Lotus, Pearl, Split, Sunset).
- `mapPlans/<Attack|Defense>/<Map>/<RoundType>/<PlanName>.png` + a same-named
  `.txt` file with that plan's comms script. `RoundType` folders are `Default`,
  `ECO`, `Pistol`, `Full Buy` (Defense side has no `Pistol`/`Default` folder —
  Defense-Pistol logic reads `Full Buy` instead, hardcoded in `getGoodPlans`/
  `getPossiblePlans`).
- `mapPlans/planData.json` — one array per map name, each entry is
  `{ matchName: [ {roundType, outcome, round, planText, enemyRoundType, side}, ... ] }`
  logged only when not in debug mode. This is the only persistence — no database.
- Helper scripts (not part of the runtime app):
  - `mapTextCreator.py` — one-off script that walks `mapPlans/` and creates an empty
    `.txt` file next to every `.png` that's missing one (so comms scripts can be
    filled in later).
  - `textEnterMapComms.py` — interactive CLI helper: reads a list of file paths from
    `fileCreated.txt`, then for each one prompts you to type multi-line comms text
    in the terminal (blank line to finish) and overwrites that file. Used to bulk-fill
    the `.txt` comms scripts created by `mapTextCreator.py`.
  - `imageResizer.py` — a stub/debug script (just lists files per map/type via glob,
    doesn't actually resize anything despite the name).

### Round-type-guessing heuristic (`roundPlanTypeOutcomeLogic`)
Hand-tuned rules run after each round, before the next round's plans are shown:
- Default guess: you = ECO, enemy = ECO if you won last round else FULL BUY.
- If you won last round on a Full Buy, or lost on an ECO, you're guessed Full Buy
  next round (i.e., re-buy logic).
- Pistol rounds are rounds 1 and 13 (`round % 12 == 1`) for both sides, forced.
- Rounds 2/14 and 3/15 (post-pistol "anti-eco"/bonus rounds) have their own
  hardcoded win/loss branching based on the pistol round's outcome.
- Round 4/16 has an extra special case keyed on `self.wins == 3` (a specific
  bonus-round pattern) layered on top of the general logic.
- Overtime (`round >= 25`) always forces both sides to Full Buy.
This logic is a personal, hand-tuned Valorant economy model — not derived from a
formula, so if you want to change it, treat each `round % 12` branch as an
independently-tunable rule rather than a formula to "fix" analytically.

### Known rough edges (found reading the code, not yet reported by the user)
- `changeSides()` has a bug: the `else` branch sets `isAttack = True` **and**
  leaves/sets `isDefense = True` too (should be `isDefense = False`), meaning after
  the first side swap `isDefense` may end up `True` even while attacking. Worth
  fixing in any rewrite.
- Hardcoded local Windows paths in `mapTextCreator.py` and `textEnterMapComms.py`
  (`C:\Users\Conor Lum\Documents\GitHub\ValorantIGLTutor\...`) — not portable, and
  the username differs from other machines' `C:\Users\User\...` convention seen
  elsewhere in this user's repos.
- `getGoodPlans()` has no `else` covering `len(plans) == 0` — an empty plan folder
  would fall through and return `None`, crashing the caller.
- Global keyboard hotkeys via the `keyboard` package require running as
  Administrator on Windows and capture keystrokes system-wide, not just when the
  app is focused — a bit heavy-handed for what's otherwise a simple local GUI.

## Why the user wants this rebuilt / continued

The current app is a `tkinter` desktop app tied to a specific Windows machine
(hardcoded paths, admin-required global hotkeys, fixed pixel layout for one
resolution). The user wants to be able to use/work on this **from their phone**,
which means a `tkinter` desktop app is a dead end — this needs to become a
web app (mobile-friendly UI) to be usable on a phone at all, and to be
worked on via Claude on mobile/web rather than a local desktop Claude Code session.

## Suggested framing for a new Claude Project on this

If starting a fresh Claude Project/conversation to continue this work, the practical
ask is: **rebuild this as a responsive web app** (e.g. it could reuse this same
account's `webapp/` FastAPI + Jinja + Postgres stack in this repo, or be a new
lightweight static/small-backend app) that reproduces the screen flow above —
map picker → side picker → round plan picker with win/loss logging and
auto-guessed economy state → plan detail view with comms script — sourcing its
plan images/text from the same `mapPlans/<Side>/<Map>/<RoundType>/` structure
(assets would need to be copied in; there are 11 maps × 2 sides × up-to-4 round
types × up to a few plans each, so a modest, finite asset set). Match/round
history should probably persist to a real table instead of `planData.json`,
especially since this account already keeps a Postgres-backed match-tracker
webapp in this repo.

This doc intentionally does **not** commit to a tech stack or plan — that should be
decided with the user (brainstorming/planning) in whatever conversation continues
this work, since "how do I work on this from my phone" is itself the open design
question, not just a delivery detail.
