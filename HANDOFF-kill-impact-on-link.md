# Handoff: compute per-kill Impact whenever the site links a replay (one PR)

Branch from a fresh `origin/main` in a worktree (`EnterWorktree`). Never merge: open a PR and hand back the link.

## The bug (found 2026-11-13)

- The health card lists 63 linked replays with no per-kill Impact (`replays.kill_impact IS NULL`).
- The site's upload and auto re-parse path stores through `webapp/app/replays/store.py`, which calls
  `replay_db.link_replay` → `write_link` (`app/replays/db.py`), and that sets `kill_impact = None`
  ("recomputed after commit"). `store.py` carries the old split over on a re-parse only if one existed.
- Nothing on the site ever recomputes it. Only scripts call `refresh_replay_impact`
  (`app/services/replay_impact.py`): `ingest_replay.py`, `reingest_replays.py`, and `link_pending_replays`
  (from `link_replays.py` and the two tracker.gg crawl scripts).
- Data: all 90 replays were re-parsed on 2026-11-13 (recipe `04dcbd92806c.c29.f1.a9965d90c`). The 27 with a
  split are older matches (~3689–3702, probably ingested by script); the 63 without one are newer (~3837–4033,
  probably site uploads), so they likely never had one.

## The fix

After a successful link on the site (upload store and auto re-parse store), call
`refresh_replay_impact(session_factory, replay_id)` after the commit, best-effort, as the scripts do. Things
to check while building:

- Which process runs it (web request, worker-result handler, re-parse loop) and how long `compute_split`
  takes per match (it runs `build_impact_rows_for_match`); keep it off the request thread if it's slow.
- `compute_split` needs the active scoring config (`SplitRefused` otherwise) and `.venv313`-equal scoring
  packages (the frozen manifest digest). Confirm the site environment satisfies it, as it does for scoring.
- The `players.riot_subject` backfill is behind the release write gate; the split itself only writes
  `replays.kill_impact`, so it shouldn't need the gate. Verify.
- Never in demo mode.
- Tests: add one that a site store + link leaves `kill_impact` filled. Run the suites
  `python scripts/test_catalog.py plan --git` names.

## The one-off repair (owner runs it; it writes prod)

From an up-to-date checkout (local `main` was 125 behind), in `webapp/`:
`.\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\link_replays.py`
(no `--refresh-impact`: it fills only the empty ones). The PR doesn't depend on it.
