# Implementation plan: partial ZERO/point hits (2026-10-16)

Design: `docs/map-control-knife-partial-plan.md` (rules 1-6, build steps 1-6). Paths below are under `webapp/`
unless they start with `docs/`. Python: `.venv313` (3.13); pytest cwd `webapp/`.

The rule adds a figure (`knife_rim_m`), which changes the constants digest, so the rule and the revision bump
land in one step: the digest test can't be green between them.

## S1. The rule, its figure, the revision and the pins (one commit)

Files:
- `app/control/utility.json`: `"knife_rim_m": 2.0` plus its `sources` note (measured, provisional: 7 of 202
  living enemies within 0.88-1.01 r on 45 local knives, all on the right side at ±0.3 s; run speed 6.75 m/s ×
  0.3 s).
- `app/control/utility.py:214-247` `read_knives`: `KNIFE_REASONS = ("knife_zero", "knife_all", "knife_hit",
  "knife_missed")`; per knife: no living enemy hit → `exclude knife_zero` for each living enemy; every living
  enemy hit and none immune → `restrict knife_all` for each; otherwise (some hit) → `restrict knife_hit` for each
  hit living enemy, `exclude knife_missed` for each other living enemy. Every op has `needs: suppress`. Restrict
  radius r + rim, exclude max(0, r - rim). `detail` = `needs`, `hits`, `living`, `r_m` (plain radius). Docstring
  rewritten.
- `app/static/js/replay_control.js:488` `REASON_TEXT`: `knife_hit: "knife hit them: inside its radius"`,
  `knife_missed: "knife missed them: not in its radius"`.
- `tests/replays/test_control_reasons.py:105`: the literal `"knife_zero", "knife_all"` → `*ut.KNIFE_REASONS`.
- `tests/replays/test_control_knife_skye.py`: the rewrites and new tests in the design's step 3; module docstring.
- `app/replays/control_format.py:47`: `CONTROL_REVISION = 17` with a one-line note.
- `tests/replays/test_control_format.py`: `17: "<new digest>"` with a comment (partial knives, the rim figure).
- `tests/replays/test_control_figures.py:91`: `(18, 17)`.
- `docs/superpowers/specs/2026-10-05-replay-control-review-design.md` item 10 (line ~30): a dated note that
  partial hits now give information, pointing at `docs/map-control-knife-partial-plan.md`.

Order: write the knife tests first and run them red, then the code.

Check (yes/no, all green, `timeout: 600000`):
`python -m pytest tests/replays/test_control_knife_skye.py tests/replays/test_control_reasons.py
tests/replays/test_control_veto.py tests/replays/test_control_utility.py tests/replays/test_control_cypher_leer.py
tests/replays/test_control_cases.py tests/replays/test_replay_activations.py tests/replays/test_replay_extras.py
tests/replays/test_replay_viewer.py tests/replays/test_utility_fixtures.py tests/replays/test_control_format.py
tests/replays/test_control_figures.py tests/replays/test_control_reference.py tests/replays/test_control_store.py
-q -p no:cacheprovider` → 0 failed. Plus mutants (controller): (a) partial branch back to `continue`;
(b) `knife_hit` and `knife_missed` kinds swapped; (c) rim sign flipped on restrict; (d) rim sign flipped on
exclude; (e) `needs` dropped from the partial ops; (f) `KNIFE_REASONS` missing `knife_missed` → every mutant caught.

## S2. Case runner and full-suite checkpoint (no commit unless something needs fixing)

Check: `python scripts/control_cases.py` from `webapp/` → exit 0 (time it); the full suite in the baseline's
parts with the 3 known base failures deselected → no other failure.

## S3. Preview (run folder only, nothing in the repo)

"Before" is computed from this worktree before S1 lands (base engine); "after" once S1 is committed:
`python scripts/preview_control_live.py <uuid> <round> --blobs <run folder>/blobs/<uuid> --tag <before|after>`
for Haven `d07225f3-cb83-4e08-87a4-487d332e77fa` round 17 (pulse 15.04 s, 1 of 5 hit) and Summit
`650e6c6c-a0b4-425d-ba36-f0c0559e8f37` round 24 (pulse 11.44 s, 1 of 5 hit). The output folders go under
`%TEMP%\valo-replay\`; copy each `preview.html` folder into the run folder. Screenshots with
`~/.claude/skills/afk/tools/shot_page.py`, the Control layer's unknown on, just before and just after the pulse.

Check: 4-8 screenshots in the run folder, and for each round the unknown's cell count for the hit enemy and an
unhit enemy before and after the pulse (from the engine, in-process, via the `Watch` observer pattern in
`tests/replays/test_control_knife_skye.py`).
