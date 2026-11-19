# Handoff: Site health card bug fixes (build-out session)

Branch from a fresh `origin/main` in a worktree (`EnterWorktree`). Never merge: open one PR and hand back the
link. A health row is cleared only by a PR (`app/data/site_health_resolved.json`), never by a site route; but
these two items come from `replay_uploads`, which the card recomputes each time, so the fix is in the card's
logic. Read the Site health section of `CLAUDE.md` and
`docs/superpowers/specs/2026-11-05-site-health-design.md` first.

## Bug 1: a "Replay storage failed" row stays after the same file was stored

- Upload `cdbd8472-4f53-4a87-991c-6636939bdfd9` (2026-10-08, auto re-parse, "could not store it: another
  recipe") is still listed. The same file (sha `42bed4f2…`) has been stored 14 times since; its replay exists
  (id 1202, match `9e12debc-…`).
- Cause: `app/services/replay_errors.py` (~lines 500–516) computes `superseded` (a later stored upload of the
  same `source_sha256`) and applies it to `upload_failed` but not to `store_failed`.
- Fix: apply `superseded` to `store_failed` too. Check the card's cheap count query (~line 733, `filters`) agrees
  with the list. Add a test.

## Bug 2: failed uploads whose files are gone stay on the card forever

- Seven failed uploads, none kept (`archive_ack` is NULL), so nothing can retry them:
  - 2026-10-14, "parse failed (this patch may not be supported yet)": 3 files uploaded twice each
    (`c3c63dbd`/`2c399b3a`, `99d06615`/`8094c097`, `89ad01f8`/`91799cba`; 82, 84 and 56 MB).
  - 2026-10-16, "not condensable: lifecycle": `cde7e3f5` (23 MB), probably a remake or cut-short match.
- The card has no way to retire them (by design: "once a recompute agrees, a problem disappears").
- **Ask the owner first** how to retire them; this is a design choice. Options: entries in a resolved list
  (like `site_health_resolved.json`, kind + upload id + reason), or an age limit for failed uploads with no
  kept file. Also ask whether they still have the `.vrf` files to re-upload (the parser now handles
  13.06/13.08), which would supersede them under the existing rule.

## Checks

Run what `python scripts/test_catalog.py plan --git` names (from `webapp/`). DB checks only through
`scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`.
