# Timing-gaps plan review

## 1. Verdict

**needs fixes first — 2 blockers, 18 should-fix, 0 nits.**

Reviewed the `worktree-timing-gaps` checkout at `36f3afd9f0d1292df25f10ce85d2ac7ee0fb3f2b`. Below, `P` means `docs/superpowers/plans/2026-10-02-timing-gaps-engine-and-detector.md`; `S` means `docs/superpowers/specs/2026-10-02-timing-gaps-design.md`. All other paths are repository-relative.

Verification used source inspection and serial, targeted commands with the specified main-checkout interpreter, from this worktree's `webapp/`. The existing presence-bubble pytest passed. Plan snippets were also executed **in memory**, with new modules and engine-method replacements; no implementation files were written. Hearing ranges were diagnostic substitutes (100 m/default, overridden by the two monkeypatch tests), not researched figures. These runs verify the indicated assertions, not a completed implementation or the whole suite. Database connectivity, actual wiki figures, real-round preview, migrations, performance overhead and cache size are **unverified**.

## 2. Findings

### R1 — blocker — Task 10, Steps 3, 5–6: existing command tests lose their database fixture

`P:2853` queries `ReplayRoundGapRun` unconditionally, including when every round is already in `planned_control`. Existing `webapp/tests/replays/test_control_store.py:33` sets `CONTROL_TABLES = TABLES`; its factory creates only those tables at `:40`, and `webapp/tests/replays/test_replay_store.py:32` contains no gap tables. Command tests at `test_control_store.py:391` and `:439` call the modified `main`. Executing the proposed planner against that exact factory produced `OperationalError: no such table: replay_round_gap_runs`. Task 10's checks omit this file; the global replacement rule then prevents an unattended fixture repair.

**Fix:** explicitly authorize extending `CONTROL_TABLES` with both new model tables, preserving the assertions, and add `tests/replays/test_control_store.py` to Task 10's checks.

### R2 — blocker — Task 1, Step 1; Task 9, Step 7: the worktree selects the wrong local database

`P:96` imports the worktree's `SessionLocal`; `webapp/app/db.py:22` uses `settings.database_url`. `webapp/app/config.py:6` reads the current folder's `.env`, and `:8` defaults to port **5432**. This worktree has no `webapp/.env`, and the command environment had no `DATABASE_URL`. `webapp/docker-compose.yml:9` exposes this repository's database on **5433**. Starting its Compose service does not correct either the query or Alembic (`webapp/alembic/env.py:12`). Availability/data on either port is **unverified**.

**Fix:** explicitly supply the local 5433 connection to Task 1; run Task 9's migration rehearsal against a separately named disposable local `*_test` database with a verified connection, rather than relying on `.env` or Compose startup.

### R3 — should-fix — Task 2, Steps 3–4: the supplied choke detector jumps through the wall

The exact snippet at `P:364` checks only the cell `WIDEN_CELLS` away, not intervening cells. On `webapp/tests/replays/control_toys.py:32` it returns **three** chokes: diagonal spans `(196,276)→(180,292)` and `(220,276)→(236,292)`, plus the actual door `(204,292),(212,292)`. The door assertion at `P:177` fails. Step 4 anticipates a multiple-choke failure, but leaves this concrete correction unresolved.

**Fix:** require every intervening along-axis cell, for steps `1..WIDEN_CELLS` in both directions, to be walkable before accepting a wider span. The false diagonals cross the wall at the intermediate step. Retain the one-door assertion and add a wall-corner regression.

### R4 — should-fix — Task 2, Step 3: re-detection changes unchanged IDs and reuses deleted history

`P:273` keeps only hand edits/tombstones, then gives every detected auto choke a new ID (`P:276`). In-memory execution of identical detections gave IDs `[1]`, then `[2]`; dropping all detections and later adding one restarted at `[1]`. This contradicts the asset's “largest ever used” contract at `P:249` and destabilizes full choke sequences, which identify gaps/patterns (`S:224`). The merge test checks only hand edits and one replacement.

**Fix:** retain matched auto IDs/names and persist an asset-level ID high-water mark, including when the choke list becomes empty. Test repeated detection and delete-all/re-add.

### R5 — should-fix — Tasks 6 and 10, cache replay: choke edits reuse obsolete route labels

`P:1550` keys the cache only by control fingerprint; `P:1586` pickles complete `RouteLog`s with their existing sequence IDs. A choke edit changes the gap fingerprint (`P:2842`) but not the control fingerprint (`webapp/app/replays/control_format.py:69`). The gaps-only path at `P:2929` reuses those old sequences unchanged. Rows are then stamped with the **new** choke hash at `P:2921`, falsely certifying old route labels as current.

**Fix:** rebuild each cached log's interned sequences from its nodes/parents and the current node-choke map before detection, or reject a cache whose recorded choke hash differs. Test an actual choke edit followed by a gaps-only run.

### R6 — should-fix — Task 10, Step 3: hearing-table edits do not invalidate results

`S:356` requires the hearing-table hash in the gap fingerprint. `P:2843` hashes only control fingerprint, gap revision and choke hash. The control fingerprint itself contains only a revision number, not runtime hearing data (`webapp/app/replays/control_format.py:69`). Re-pinning the constants test (`P:1338`) changes no runtime fingerprint. A hearing edit can therefore leave both rows and cached unknowns apparently current.

**Fix:** add a stdlib hearing-asset hash to gap freshness and the cache's engine-input validation; recompute unknown through the engine after hearing changes. Test a hearing-only edit without a revision change.

### R7 — should-fix — Tasks 5–7, observer/event ordering: openings are judged after locating

`S:192` requires pre-event unknown and locating history for gap openings and use. `P:1294` first replaces the region, `webapp/app/control/engine.py:1903` applies unknown before the observer, and `P:1936` appends current locating events before `_victim`. A diagnostic record with a previously eligible behind node and a same-time damage event produced **zero** openings. Task 8's `inclusive=False` handles back-shot history, but cannot restore discarded pre-event unknown or missing stood/opening states.

**Fix:** expose pre-event state or process a before/after event timeline; judge openings/use before updating unknown/history. Add a same-event opening and a kill-at-use regression, not just the existing back-shot exception.

### R8 — should-fix — Task 5, Steps 4–5: locating history silently drops events

`P:1234` gathers all events but returns only `max(found)` at `P:1256`; `P:1299` records just that winner, and a same-tick sighting can follow it. Events on the first tick are never examined because `P:1287` requires `_prev_t` to exist. `S:185` requires every locating event. Losing an earlier damage event in favor of later gunfire is consequential when Task 8 ignores that gunfire for its own-shot exception (`P:2025`).

**Fix:** enumerate and record all events in time order, including the initial interval; keep region-collapse tie selection separate from history retention. Test damage plus gunfire between ticks and an event at time zero.

### R9 — should-fix — Task 7, Step 3: non-candidates keep another enemy's gap open

`S:239` defines exposure using **candidate** enemies. `P:2022` accumulates exposed sequences from every enemy before the 5 s filter, then `P:2029` updates any open gap with that sequence. Enemy 6, who has never qualified and is absent from the candidate list, can keep enemy 5's gap latched after 5's space has disappeared.

**Fix:** update an existing gap's exposure only from enemies already in its historical candidate set; compute qualifying joins separately. Test a non-candidate with the same sequence during the close timer.

### R10 — should-fix — Task 7, Step 3: a return after expiry resurrects the old latch

`P:1944` updates exposure before checking expiry at `P:1946`. Executing `_manual` with exposed at `0`, empty at `1`, and exposed again at `6` produced one gap `(open=0, last=6, close=60)`, instead of closing at `5` and opening a new gap at `6`. The supplied close test (`P:1746`) hides this by inserting an empty frame exactly at expiry.

**Fix:** expire latches against their previous `t_last_exposed` before consuming current exposure; integrate qualified duration only to that close. Add sparse-timeline and exact-expiry return tests.

### R11 — should-fix — Task 7, Step 3: the cause selector overrides released routes

`S:270` permits `open_timing` only when no route cell was observed since locating. `P:2107` nevertheless always adds it with the later arrival/ready time. A route released at `1 s`, completing at `2 s`, returned `('open_timing', {})` in a focused probe despite a recorded release. Also, `P:2103` requires prior exposure for `victim_moved`, omitting movement into a newly exposed line (`S:269`). No cause test exercises either situation.

**Fix:** restrict `open_timing` to routes with no relevant observation; evaluate release, actual turn and movement causality independently. Test release-delayed arrival and movement into new line of sight with the declared tie order.

### R12 — should-fix — Tasks 6–7: view/utility attribution cannot be recovered from the record

`P:1504` stores only union `Tick.live` and utility. `P:1960` reconstructs view as `live & ~utility`, erasing cells covered by **both**, contrary to view-before-utility (`S:267`). The same subtraction loses overlapping glance checks (`P:2062`) and context (`P:2087`). Existing engine inputs preserve the split before memory: `webapp/app/control/engine.py:828`–`:837`; memory runs later at `:1906`. Separately, `_reason` calls a missing-position live player “died” (`P:1984`) and treats any newly added smoke as causal (`P:1997`).

**Fix:** snapshot an independent pre-memory view mask; retain overlap and prioritize view. Check lives before “died”, and test whether the changed smoke blocks the released cell. Add overlap, missing-position and unrelated-smoke release tests.

### R13 — should-fix — Task 7, Step 3: predicted visibility uses the wrong eye/position

`P:2015` uses `seen_from` from the victim's node. `webapp/app/control/engine.py:718` reads static visibility rows, built at **cell centres** and `node_z + EYE_M` (`webapp/app/control/geometry.py:762`). `S:141` instead requires full-circle `cast` at the player's actual position and eye height. A player above their floor or near a corner can expose a different set; the engine's own `_eye` handles real track height (`engine.py:872`). This is a semantic mismatch, not a per-node array-size error.

**Fix:** use `cast(geo, p.x, p.y, full_circle, smokes, eye_z=actual_eye, own=p.node)` with the engine's unresolved-height fallback. Add raised-player and within-cell corner tests.

### R14 — should-fix — Task 5, Step 5: revival without a sample is located too early

`P:1284` records `revived` immediately whenever an enemy region is recreated after the first tick, regardless of `h`. `_enemies` intentionally includes live enemies lacking samples (`webapp/app/control/engine.py:1703`). The region remains empty, then a later holder pushes its own cell at `P:1310`, but no new locating event is emitted. `S:202` requires the first sample of the new life when no position exists at revival. The supplied test has a sample at revival (`P:1100`), so it passes while missing this case.

**Fix:** retain a pending new-life origin until a sample exists, then seed and locate at that sample's time. Test missing samples across revival and the resulting 5 s wait.

### R15 — should-fix — Tasks 4–5: observed source nodes disappear from route history

`P:878` logs only finite post-spread nodes, and `P:885` refuses a parent's entry when its node is not finite. Sighting centres and area centres can be outside `room` (`P:1311`); `_spread` still uses a sighting as a transient seed (`P:858`), but its log source is never created. Descendants then become unrelated roots or inherit old entries. Their paths can omit the true locating origin and its choke, contrary to `S:158` and `S:312`.

**Fix:** append explicit source entries even when the source is currently observed; associate transient spread seeds/area origins with those entry IDs independently of region membership. Test a watched centre with reachable unobserved neighbours and clearing/re-entry.

### R16 — should-fix — Task 8, Step 3: back-shot context measures the wrong quantities

`S:310` requires peak speed along the stored path. `P:2037` checks only the five seconds before damage, potentially dropping an earlier sprint. `S:349` requires candidate distance from the **spot**; for a back-shot the spot is the shooter's cell (`S:342`), but `P:2036` stores shooter-to-victim distance again. Neither is asserted by the supplied stationary tests.

**Fix:** evaluate speed across all unbroken path pieces from `since`/round start through `t0`; calculate candidate distance from the shooter's real position to `geo.centres[shooter_node]`. Test an early sprint and separate victim/spot distances.

### R17 — should-fix — Task 10, Steps 3 and 5: “gaps-only” does not imply fresh successful control

`webapp/app/services/replay_control.py:208` omits current failed control unless retrying; `:202` includes it under `force=True`. Thus `P:2958` adds that round to `every` while `planned_control` omits it, and `P:2857` schedules it as gaps-only. Conversely, `P:2853` loads no gap-run status, so `--retry-failed` never retries a failed gap run whose fingerprint is current. This contradicts the advertised fresh-control selection and obstructs recovery.

**Fix:** require a matching successful control row for gaps-only jobs; load gap-run status and honor explicit retry-failed for gap failures. Test fresh-ok, current-failed control, and current-failed gap runs.

### R18 — should-fix — Task 10, Step 4: cache errors escape the gaps failure boundary

The proposed observer and `observer.close()` run inside control's outer `try` (`P:2900`, `P:2907`), before `_gaps`'s protected detector block. An observer/cache write exception therefore discards already computed control and is handled as a control failure by `webapp/app/control/task.py:118`. The proposed failure test injects only `finish` (`P:2796`), which does not exercise this path. `S:361` requires the control result to survive a gap-processing failure.

**Fix:** protect cache observer operations and finalization separately, preserve `rc` and its encoded result, and return a failed gap run. Add observer-call and close/write failure tests in addition to detector-finish failure.

### R19 — should-fix — Task 10, Step 5: a failed gap run is reported as successful work

`P:2976` ignores the gap-run status and `P:2979` ignores the storage outcome. Existing `webapp/scripts/compute_control.py:200` and `:227` mark/count failures solely from `result['status']`, which stays `ok` for detector failure; gaps-only also sets it to `ok` (`P:2895`). The run can exit 0 after every detector failed or after gap storage was skipped. `P:2983` additionally uses `len(rows)` without specifying a local binding in `run`.

**Fix:** bind `rows = result.get('gaps', {}).get('rows', [])`; separately track gap computation/storage failures, print their errors/outcomes, and return nonzero when any required gap write failed. Add a parent-command failure/exit test.

### R20 — should-fix — Tasks 5 and 10: required missing-data counts are not recorded

`S:212` requires missing speed/position cases in round `missing` and the gap run. `P:1197` silently returns no speed across missing samples/segments; `P:1289` silently skips locating without a position. `P:2942` reports a separately constructed `RoundInputs` (`P:2926`), not the one whose tick processing updated engine missing counts (`webapp/app/control/engine.py:1956`). Existing candidate and back-shot notes cover only two downstream cases.

**Fix:** count each specified missing case without multiplying one absence by every check; persist actual compute-time missing counts in the tick cache and reuse them in both gap execution paths. Test all cases and equal live/cache notes.

## 3. Spec coverage and verified task contracts

Requirements in sections 1–7 with no corresponding implementation or sufficient regression in this plan:

- Section 2's choke tagger mode (`S:114`) is explicitly deferred to plan 2 (`P:20`); it is still absent from this sections-1–7 build. Hand-editing JSON is the documented interim workflow. `SHAPE_THRESHOLD_M` in section 1 (`S:100`) is likewise deferred to the section-9 pattern implementation.
- Section 3's split observer masks and actual-eye visibility are not built (R12–R13); no height-aware gap detector test exists. Tick-cache tests compare unknown arrays, not final live-versus-cache detector rows; Task 10 compares two **cache** runs (`P:2807`), so a common serialization bug could pass.
- Section 4's complete event timeline, first-tick events, missing-position revival, missing-data counts and preserved true route origins lack tests (R7–R8, R14–R15, R20). The node-topology spread test checks arrival equality and dtype (`P:515`), not that node parents are valid height edges. Route history tests clear but do not prove a subsequent re-entry retains the original trace.
- Section 5 has no test for the exact rear-angle boundary, neighboring cells with distinct sequences, merging across enemies, a second route, candidate-only exposure, revival as a new victim life, each cause/release reason, or cause ties. The gap test helper accepts `chokes` but never uses it (`P:1667`); no test injects custom choke labels. R9–R12 show resulting blind spots.
- Section 6 has no actual intervening wall in the wall-bang test (`P:2226` uses `open_hall`), no broken-track or peak-speed assertion, no multiple-gap/nearest linking test, and no varying-enemy use/result-window tests. Its stationary rear/front and own-gunfire cases do exercise their stated directional/history situations.
- Section 7 lacks new-table cascade/constraint tests, transactional rollback assertions, and revision/choke/hearing staleness tests. SQLite fixtures do not enable foreign keys, so replacement tests alone cannot establish cascades. The migration rehearsal is unverified. Models do declare both round foreign keys; demo refusal is inherited from `compute_control.main` at `webapp/scripts/compute_control.py:250`, rather than being wholly absent.

Task-by-task symbol/line and toy verification:

| Task | Verified code contracts and test implications |
|---|---|
| 1 | Shot `gun` is the equippable name at `webapp/app/replays/extras.py:1138`; `ReplayRound.data` exists at `webapp/app/models/replay.py:71`. Whether the local database has replay rows or `None` gun names is **unverified**. R2 prevents assuming the intended connection. |
| 2 | `STATIC_DIR` is `webapp/app/replays/format.py:57`; control `ASSET_DIR` is `webapp/app/control/geometry.py:79`. `Geometry`'s referenced fields/methods exist at `:243`, `:271`, `:297`, `:300`; `centres` and `node_cell` become per-node arrays at `:330` and `:342`. Toy imports exist at `control_toys.py:28`, `:32`, `:38`, `:43`, `:69`. Six snippet tests ran: open hall, passage, special link, merge and node mapping passed; the door test failed as R3 describes. Save/load/hash test execution is **unverified**. |
| 3 | Spread methods are exactly `webapp/app/control/topology.py:97` and `:216`; `SPREAD_ORDER`, `in_from`, `in_cost`, `n` exist at `:36`, `:164`, `:165`, `:128`. Their proposed signatures preserve old positional calls and add optional `parents`. The toy-height factory is `control_toys.py:247`. Execution of this task's standalone parent assertions/reference suite is **unverified**; both proposed spread methods were exercised by the later diagnostic harness. |
| 4 | `Unknown.__init__`, `apply`, `_drop_pieces`, `_spread`, `TickRunner.__init__`, and runner construction are `engine.py:1548`, `:1624`, `:1676`, `:1707`, `:1889`, `:1945`. `_Tk` and `_at` are `test_control_unknown.py:15` and `:22`; these fake holders have only explicitly supplied views, not the real presence bubble. The route-to-source, door-sequence and cleared-entry assertions passed in the diagnostic harness; byte identity and the 20% overhead claim are **unverified**. `RouteLog`'s proposed field order and parent-entry construction compile; semantic origin loss is R15. |
| 5 | Track/sample, plant, shot and damage insertion anchors match `engine.py:259`, `:314`, `:370`, `:400`, `:439`; `toy_ability` and `barrier_hall` exist at `control_toys.py:105` and `:111`. All seven locating test functions passed with the diagnostic hearing substitutes. West-facing A at x120 cannot see east B at x400: separation is about 39 m, outside `PRESENCE_M=4` (`engine.py:98`), and behind the FOV. The kill is by B, and its area correctly uses B's track rather than the victim coordinates. Ability damage stays non-locating; gun damage locates; 1 m Quiet hearing does not reach A, 100 m does; 3 m/s walk stays below 4.5, 6 m/s run exceeds it; the bomb owner is slot 5. The revival is sampled at exactly 6 s, so that test does not exercise R14. Revision is currently 4 (`control_format.py:46`); the pinned digest test exists at `test_control_format.py:59`. |
| 6 | `compute_round` signature/loop are `engine.py:1913`/`:1952`; `Holder.watch` exists at `:658`, and `Tick.live` is built before memory at `:846`. Proposed `PlayerView`/`TickRecord` positional calls match their declared field orders. `Unknown.events` exists only after Task 5. The proposed path distinguishes replay and round. Full observer/cache test execution is **unverified**; R12 and R5 remain despite correct field arity. |
| 7 | All eleven supplied detector test functions passed in the harness. Toy yaw is held piecewise (`control_toys.py:80`): at exactly 3 s the sighting-wait toy already faces west; the last preceding regular sighting is 2.5 s, so the test's 7.5 s lower bound is consistent. The turn-back toy stays one gap because the engine re-seeds/retains B's true cell (`engine.py:1668`, `:1715`) in A's full-circle line, even when facing clearance removes surrounding ground. Front-facing A continually sees/locates B; after a 0.5 s turn to face B the initial rear gap becomes a flicker. The manual close tests insert an expiry frame; R10 exposes what they miss. Actual gaps existed in the decided-round test, so its assertions were not vacuous in this harness. |
| 8 | All six supplied back-shot tests passed in the harness. A faces west at x150, B is east at x400, so the shot angle is 180 degrees and the presence bubble does not reach B. Damage at 8 s is excluded from its own pre-event history; the 7.8 s gunfire lies within the 0.5 s exception. Prior damage at 8 s prevents a second back-shot at 9 s. The victim's 8.5 s death closes its predicted gap; damage at 8 and the kill at 8.5 fall inside the 3 s result window and link to that gap. `RoundInputs.blob`, `hz`, `lives`, `pos`, `node` exist at `engine.py:241`, `:242`, `:270`, `:328`, `:343`. The deferred import from `finish` avoids a circular-import initialization failure. |
| 9 | `TABLES`, `condensed`, `add_match` are `test_replay_store.py:32`, `:39`, `:43`. Referenced SQLAlchemy imports and `JSONType` exist at `webapp/app/models/replay.py:11` and `:34`; the plan explicitly adds `Index`. Current migration head is `0015_replay_archive.py`; 0016 is available in this checkout. Proposed model declarations compile in memory. Store assertions and migrations are **unverified**. |
| 10 | `compute_task` and its replacement body are `webapp/app/control/task.py:94` and `:106`; all existing outer imports needed by the snippet (`engine`, encoders, `fmt`, `traceback`) are in scope. `PlannedRound` fields are `webapp/app/services/replay_control.py:143`; the implementation/call use three `plan_gaps` arguments even though the interface summary lists two (`P:2764`). `compute_control`'s planner, task launch and result store anchors exist at `:259`, `:188`, `:196`. The new service imports remain stdlib/SQLAlchemy and do not import `app.control`; isolation checker is `test_control_isolation.py:55`. No Task 10 integration pass is claimed; the existing-fixture SQL failure was directly confirmed (R1). |
| 11 | `preview_control_live.link_for` exists at `webapp/scripts/preview_control_live.py:51`; `context.json` and numbered blobs are written at `:130` and `:87`. `geometry.visibility/load_geometry` exist at `geometry.py:772`/`:360`. The preview loop's fields match `Gap`. Network access, the selected UUID's availability and runtime are **unverified**. The owner handoff's interpreter/environment divergence is documented below. |

The new test files/modules do not yet exist in the checkout, so their initial import failures are consistent with the prescribed red stages. This is not evidence that every test meaningfully detects its intended behavior: the specific missing assertions above remain.

## 4. Unattended hazards and sequencing

- **Task 1, Steps 1–4:** requires Docker startup, a populated local replay database, wiki network access, and a buy-menu check by the owner (`P:85`, `P:112`, `P:117`). The run should preflight an explicitly selected existing local database, save research/provenance and a resumable owner-check artifact, and continue independent work while approval is pending. Do not silently substitute remote data or invented hearing ranges. This review started/stopped no database and performed no research network requests.
- **Task 2, Step 6:** the Ascent count is an owner gate if outside 10–80 (`P:450`). Record the count and detector diagnostics, pause asset acceptance/tuning at that gate, and continue Task 3/other independent work. The count and per-map runtime are **unverified**; generated assets should be processed/checkpointed one map at a time if the all-map job exceeds ten minutes.
- **Task 4, Step 7:** the baseline measurement must occur before Step 3, despite appearing late in the task (`P:943`). A slowdown over 20% requires owner attention (`P:945`). Save baseline and post-change timings and stop dependent engine activation at that gate; do not leave an unattended runner waiting interactively.
- **Tasks 2–4 and 6–10 while Task 1 waits:** Tasks 2–4 can proceed in order, subject to their own owner gates. Task 6 records `unknown.events` (`P:1506`), which Task 5 creates; Tasks 7–8 require Task 5's `kills`, `gun_runs`, speed and events; Task 10's integration requires all of those plus Task 9. Thus **6–8 and 10 cannot pass as written before Task 5**, which cannot start before hearing approval. Task 9's models/migration source, dict-based store tests and duck-typed row converter can be prepared independently. To permit more parallel progress, specify temporary test doubles/interfaces for the later tasks and a distinct integration phase after Task 5. No earlier-task dependency on the later Task 8 implementation is unresolved: Task 7 deliberately creates its stub.
- **Task 9, Step 7:** starts Compose and runs three schema-changing commands (`P:2739`), despite the global prohibition on DB writes outside tests before the owner handoff (`P:39`). Designate this explicitly as a disposable local migration test, provide/verify its URL, and require an already-running Postgres. If it is unavailable, preserve completed code and mark migration validation pending; do not start services or mutate a non-test database automatically. R1's fixture change also needs explicit authorization under the existing-test rule before unattended work begins.
- **Task 11, Steps 2–3:** preview fetches the deployed friends site (`preview_control_live.py:33`, `:38`) and computes control therefrom; it is not local-only input acquisition. Two rounds are a bounded sample, but duration is **unverified**. Obtain the preview input bundle once and retain it; run/resume each round separately, checkpoint measurements, and leave the visual/cache-retention approval pending. Current standalone rendering has no gap overlay until plan 2, so the owner check is a manual seek-and-compare, not a gap-layer preview.
- **Task 11, Step 5:** deliberately belongs to the owner and writes the remote friends database. `webapp/scripts/with_friends_db.py:36` requires this checkout's `.env.remote`, which is absent; `:121` will read it before running either command. It also selects `.venv313` (`:38`, `:91`), overriding the plan's `PY` interpreter. The handoff should identify this override, provision the ignored credential file securely in the worktree, and verify that interpreter has the required packages. Do not copy credentials into the review/commits or let the unattended agents run this step. The long full run already commits each finished round (`compute_control.py:196`, `:213`) and is resumable; preserve that behavior and fix R17–R19 so failures and retries remain visible.
- **Global execution:** no plan step requires a push, merge or dependency install. The named `superpowers` execution skills in `P:3` are not in this review session's available-skill catalog; their availability to the eventual builder is **unverified**, so provide a direct execution fallback rather than an unspecified skill-install detour. No subagents were used for this review.
