# Review of timing gaps plans 2 and 3 (2026-10-04)

One independent review (fresh subagent, read-only) of `2026-10-04-timing-gaps-viewer.md` and
`2026-10-04-timing-gaps-pattern-page.md`. No blockers; nine should-fix, five nits. Each is applied below
(A = accepted and folded into the plans' "Amendments from review" sections).

| # | Finding | Outcome |
|---|---------|---------|
| 1 | `choke_assets.asset_hash` hashes raw bytes, so renaming a choke (the tagger's job) stales every round of the map | A: hash only `id`, `cells`, `deleted` as sorted JSON (like `hearing_hash`); done in S1 so the copy DB recomputes gaps once; tier 2 card |
| 2 | The merged gap mixes moments: `t_open` from the earliest member, everything else from the longest | A: `context.t_round` = merged `t_open - t_start`; `context["merged"]` keeps each member's `t_open` and sequence; `checked_at` from the longest only; the R4 check is run on the pre-merge row and says so |
| 3 | The empty sequence folds into any choked gap | A: literal R1 kept; the card gives the count of empty-sequence folds from the preview on its own line |
| 4 | Plan 3 relies on a `?t=` start time plan 2 never adds | A: S6 adds `t` to `replay_page` and the page seeks to it |
| 5 | The `gaps.json` ETag over rows only can serve a stale status or names | A: ETag over the whole body |
| 6 | Which control fingerprint decides freshness; the two pages could disagree | A: viewer: `replay_control.round_fingerprint(replay, side_groups(db, replay), n)` as `round_control`; pattern page: `run.fingerprint == gap_fingerprint(stored control row's fingerprint, map)` |
| 7 | The import rule misses `app.gaps` (numpy, `app.control`) | A: the web-side modules import neither; added to `test_control_isolation.py`'s list |
| 8 | Plan 3 assumes hundreds of rows; Ascent will have about 10k | A: `route` loaded only for the selected pattern and empty-sequence rows; page time measured and logged |
| 9 | Every choke is named by its number, so "naming chokes" isn't met | A: hovering a list row highlights its chokes and numbers on the map; the legend says names come from the tagger's choke mode |
| n1 | `choke_points` not defined in plan 2 | A: named in S2 |
| n2 | `unlinked` 404 goes beyond control.bin's rules; round range check; tombstones | A: kept (`unlinked` matches the page, which hides control when unlinked), logged as a decision; range check added; tombstoned chokes dropped |
| n3 | Sorting by total rows adds predicted and back-shots | A: sort by predicted count, then back-shots |
| n4 | Missing values in shape ordering, null sides, old_blob rounds | A: `replays.created_at` when no match date; null-side rows get their own shape group; `old_blob` rounds left out of "not yet computed" |
| n5 | `use` filter reads 100% | A: with a `use` filter the shares for that level are hidden (they are 100% by construction); `use=killed` also applies to back-shots (`killed_at`) |
