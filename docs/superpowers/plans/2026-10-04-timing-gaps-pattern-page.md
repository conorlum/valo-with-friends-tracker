# Timing gaps, plan 3: the pattern page

Written 2026-10-04 (AFK run `2026-10-04-gaps-viewer`). Spec:
`docs/superpowers/specs/2026-10-02-timing-gaps-design.md`, section 9 and decisions 12, 13, 16. Builds on
plan 2 (`2026-10-04-timing-gaps-viewer.md`): its merged rows (`GAPS_REVISION` 2), its view rows
(`app/services/replay_gaps_view.py`) and its viewer links.

## Goal

One page per map that lists recurring gap routes, grouped by full choke sequence, with filters, for Everyone
and for Friends, as `/stats` does; each pattern draws its routes on the map and links to its rounds in the
viewer. The page says the data is too thin for conclusions, and every figure shows its sample size.

## In scope

- `GET /gaps` (a map index with each map's eligible round count) and `GET /gaps/{map_name}` (the page).
  Both 404 in demo mode, like every replay route. `/gaps` does not clash with `/replays/{match_uuid}`.
- `app/services/gap_patterns.py`: the query, filters, populations, grouping and figures (standard library and
  the DB only: no `app.control`).
- `app/templates/gaps/index.html`, `app/templates/gaps/map.html`, `app/static/js/gap_patterns.js` (drawing a
  pattern's routes on the minimap; no build step).
- A "Patterns" link from the replay viewer's Gaps tab to the map's page.

## Out of scope

Caching (spec: none in the first version), statistical tests, `scripts/tracked_players.json` (never read).

## Architecture and key decisions

### Which rounds count (spec section 9)

A round counts when its `replay_round_gap_runs` row is `ok`, its `gaps_revision` equals `GAPS_REVISION`, and
its `chokes_hash` equals the current asset's (`choke_assets.asset_hash(map)`), and its replay is valid and on
this map. The page shows, for the map, how many replay rounds were left out as stale (an older revision or
choke hash), failed, or not yet computed (no run row). The full fingerprint (control fingerprint, hearing hash)
is not recomputed per round on the page: the spec names revision and choke hash as the test, and recomputing
every round's control fingerprint is the expensive part of `replay_control.plan`.

### Populations (decision 16)

- **Everyone:** every row of every counted round.
- **Friends** (logged-in only; `app.services.auth.get_current_player` + `friends.list_friend_ids`): the
  viewer's own player id plus the friends they own. Resolved at query time: `replay_players.match_player_id` →
  `match_players.player_id` for each slot of a **linked** replay (`replay_db.is_linked`). Rows of unlinked
  replays count under Everyone only.
- **Direction switch** under Friends: "gaps my group left open" (the victim is in the group) or "gaps our
  opponents left open" (any candidate slot, or a back-shot's shooter, is in the group).
- A logged-out visitor sees Everyone only, with a line saying Friends needs a login.

### Filters (query string, so a filtered view is a link)

`kind` (predicted / back-shot / both, default both), `side` (attack / defense / both; rows with a null side
are excluded by a side filter), `cause` (any of the four), `use` (any / stood / shot / killed / unused;
predicted rows only, back-shots unaffected per spec), `t_max` (seconds into the round, from
`context.t_round`), `flickers` (off by default), `pop` (everyone / friends), `dir` (left / opponents).

### Patterns and figures

- Rows grouped by full `choke_seq` (a tuple); rows with a null `choke_seq` (a broken back-shot path) are left
  out and counted.
- Per pattern: predicted count; the share stood in, shot and killed, each as `k / n` of that pattern's
  predicted rows; the most common cause; the median `t_round`. Back-shots: count, linked vs standalone
  (`linked_seq` not null), and the share that killed (`killed_at` not null), `k / n`. Never summed with
  predicted rows.
- Sorted by total rows, then by sequence.
- **Route shape** for the empty sequence only, within map and side: each row's route (its unbroken pieces
  joined in order; predicted rows have one piece) resampled to 16 equally spaced points; distance = mean of
  matching points' distances, in metres (`m_per_px` from the map's control layer, via
  `replay_control.geometry_inputs` / the layer index, without importing `app.control`); greedy grouping in a
  fixed order (match date, replay id, round number, `seq`) at `SHAPE_THRESHOLD_M` = 4 m; at most the 2,000
  most recent eligible rows per map and side, and the page says when it cut the list. Each shape group is
  listed as "no choke, shape group k".
- Selecting a pattern draws its routes (from each row's `route`) on the minimap and lists its rounds, each
  linking to `/replays/{uuid}?round=n&t=<t_open>` (the viewer's start time; plan 2 adds `t`).

### Names

Choke names come from the map's choke asset (`replay_gaps_view.choke_points`). Player names never show on the
pattern page (a pattern aggregates rounds; the viewer link shows who).

### Wording

Site copy is tier 1 (judgment.md): plain words, a line saying the data is too thin for conclusions, and every
figure with its `n`.

## Risks

- Query cost: rows per map are small (hundreds) for now; one query for runs, one for rows, one for the slot →
  player map of the involved replays. No cache, per the spec.
- The friends join needs `match_player_id` on `replay_players`; an unlinked or partly linked replay simply
  contributes no friend matches.
