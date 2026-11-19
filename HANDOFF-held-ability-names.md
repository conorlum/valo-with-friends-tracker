# HANDOFF: name the ability a player has out (parser + viewer + coach)

Written 2026-11-17. Not started. Do it in its own worktree off origin/main (`EnterWorktree`), its own PR.

## What the owner wants

1. The replay players panel names the util a player has out when no gun or knife is out ("Spycam out",
   "Holding Cloudburst", ...), not just the camera/drone case (that one is in the replay-panel work, see
   HANDOFF-replay-panel-polish.md: `possessingAt`, from ability rows' `possessed` spans).
2. The coach gets it as a fact: "you died N times with your <ability> out" (owner: "that is important").

## Why it's missing today (verified)

- `webapp/app/replays/extras.py` `_held_guns` (~line 2248): a `NewCurrentEquippable` switch becomes a hold row.
  For an item that isn't a firearm/melee (`gun.other` or `cat == "ability"`, line ~2303) the row is written
  as `{"gun": None, "cat": "other"}`, so the ability's identity is dropped.
- The identity IS available at that point: `gun.archetype` on the `_Gun` (built ~line 703, `other=True` for
  equippables). Ability equippables match `EQUIPPABLE_ARCHETYPE = ^Default__Ability_([A-Za-z0-9]+)_(.+)_C$`
  (line 446): group 1 = agent code (e.g. `Gumshoe` = Cypher, `app/static/data/agents.json`), group 2 = name.
  The spike's equippable (`Default__BombEquippable_C`) is NOT admitted as a gun actor, so a switch to the spike
  also lands as `cat: "other"`. Keep it distinguishable (e.g. `cat: "spike"`) if the raw allows, else "other".
- Unverified: which equippable archetype appears when Cypher *views* his camera vs. holds it to place it.
  Check a real export before fixing naming rules (`~/rp/parser/tests/Test.Integration/Replays/*.vrf` via the
  parser, or the worker's raw).

## Plan

1. extras.py: on a hold to an ability equippable, write `{"gun": None, "cat": "ability", "code": <code>,
   "name": <name>}` (keep `cat: "other"` for unknown items). Update the format docs in `extras.py` (~line 23)
   and `app/replays/format.py` (~line 43).
2. Stored rows change: bump `CONDENSE_REVISION` (memory `project_revision_bump_pinned_tests.md`: pinned digest
   tests break; the auto re-parse queue, PR #122, then re-parses every replay). Check what control/gaps
   fingerprints include CONDENSE and what re-runs follow.
3. Viewer (`app/static/js/replay.js`): `gunAt` (~line 1342) returns null on `cat === "other"`; return
   `{ability: <display name>, held: true}` for `cat === "ability"` and render it in the gun slot (CSS class
   `replay-pl-gun is-util` from the panel work). Display name: `abilityStyle({code, name}).ability`
   (ABILITY_STYLES ~line 100-260) or `app/critutil/abilities.json`. `possessingAt` keeps priority.
4. Coach: a per-player fact "deaths with an ability out" (ability, round, time) in the brief
   (`app/coach/snapshot.py` / brief facts), citable by `app/coach/checker.py`. Follow CLAUDE.md's AI coach
   section (fact ids the checker trusts; no opponent names).
5. Tests: extras unit test, viewer node test for gunAt, coach fact test; new test files into
   `tests/suites.toml`. `python scripts/test_catalog.py plan --git`; slow lane before merging (touches condense).

## Bundle with: unclamped Iso arena tracks

The same re-parse should also store Kill Contract arena positions unclamped: today the condenser clamps u/v to
the minimap, so in the arena v is always 10000 and one axis is lost; on Haven u is clamped too, so both are
lost, and on Summit some tracks have u stuck at 0 (47 duels checked, see
HANDOFF-replay-panel-polish.md item B). One CONDENSE bump, one re-parse for both.

## Owner rules

Open a PR and give the link; never merge. No heredocs. Tests from `webapp/` with `./.venv313-test/Scripts/python.exe -m pytest`.
