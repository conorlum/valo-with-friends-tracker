# Test coverage audit and planning handoff

## Scope and audit evidence

**Prepared October 25, 2026, against checkout `89fd0d79075635afa6adbb3ddbdad1bd743434dd`.** This is a coverage assessment and a set of high-level test specifications for an agent to turn into an implementation plan. The specifications below have not been implemented. Only this document was created; application code, existing tests, configuration, fixtures, databases, and deployment state were left unchanged.

The best return is to make existing coverage reliably run, protect migration and ingestion transactions, and fill gaps in everyday web and statistics behavior. The repository already has extensive numerical, replay, map-control, and release-tool tests. Another large batch of narrowly mocked replay tests is a lower priority.

### What was reviewed and verified

- Parsed all **339 Python test/support files** under `webapp/tests/` with Python's AST parser; no syntax errors were found. Inventoried all **325 `test_*.py` modules and 4,570 `test_` function definitions**. These are static counts, not collected pytest cases; parametrization, fixtures, and selection change runtime counts.
- Inspected test configuration, conftests, the PostgreSQL guard, the tracked CI workflow, representative tests in each major subsystem, routes, migration setup, ingestion orchestration, cache paths, seed tooling, and matching production services. Detailed behavioral review was targeted at important boundaries; this is not a claim to have executed or individually validated all 4,570 definitions.
- Ran only audited tests requiring no database or output files, with bytecode, pytest cache, normal plugin autoload, and output capture disabled. `test_stats_math.py`, `test_agent_roles.py`, and `test_weapon_prices.py`: **62 passed in 0.61 seconds**. `test_page_sections.py`: **7 passed in 0.21 seconds**. Total: **69 passed**. Python: existing `webapp/.venv313/Scripts/python.exe`.
- Did **not** execute the full suite, integration tests, browser tests, migrations, ingestion, release commands, seed loaders, or Docker builds. Many tests create files, mutate databases, launch processes, or can create golden files. That would exceed this audit's read-only scope.
- No line/branch coverage or full-suite failure/skip baseline was measured. Absence of a test name/import is triage evidence, not proof that no indirect coverage exists. Gaps below explain the missing assertion or boundary, rather than treating filenames as coverage percentages.
- Existing untracked items at the start were `Microsoft/`, `assets/`, `tmp_w12a.py`, `tmp_x.py`, and `webapp/review-test-results.txt`. They were left alone. The untracked results file was not used as evidence for this checkout's test status.

### Important project constraints for the next agent

1. Both deployed sites use this repository. Test both friends and demo settings. The demo uses its own sample database; never connect it to friends data. Preserve `https://valomaths.onrender.com` and its Riot verification behavior. Demo data uses `MatchSource.SCRAPED` too: that enum alone cannot distinguish sample data from friends data.
2. PostgreSQL tests must use the existing `tests/_postgres.py` boundary and a disposable database. Tests that write should declare `writes=True` or `empties_tables=True` as appropriate and use a `*_test` database. Do not use `.env.remote`, `.env.demo-remote`, a production database, or a measurement/rehearsal corpus for writes.
3. Use Python 3.13 for scoring-related validation. Read `docs/superpowers/SCORING-RELEASE-PROCESS.md` before proposing changes touching scoring. Baseline changes, changed expected scores, new weights, and formula activation are not routine coverage work. Preserve frozen references and manifests.
4. The checkout currently selects `docs/superpowers/impact-v4/candidate-manifest.json` through `app/scoring/impact_runtime.py:25`. This is source evidence, not a check of the deployed service. `build_impact_rows_for_match` without an explicit configuration evaluates the legacy formula; it does not establish coverage of the configured runtime formula.
5. This public repository must not gain credentials or private replay exports as fixtures. Existing synthetic and sanitized fixtures are the starting point.

## Existing coverage

### Inventory

The first four rows partition the suite by directory. The following table describes overlapping capabilities rather than adding another test count.

| Directory | Test modules | Test function definitions |
|---|---:|---:|
| `webapp/tests/` directly | 139 | 1,697 |
| `webapp/tests/replays/` | 159 | 2,517 |
| `webapp/tests/coach/` | 20 | 264 |
| `webapp/tests/moments/` | 7 | 92 |
| **Total** | **325** | **4,570** |

| Capability | Existing evidence worth preserving | Remaining limitation |
|---|---|---|
| Impact formula, economy, plant timing, trade windows, alive counts | `test_impact_*`, `test_econ_*`, `test_preplant_*`, `test_postplant_*`, `test_trade_schedule.py`, `test_trade_count_window.py`, `test_plant_window.py` | Strong branch-level and mathematical coverage does not make historical configurations current-runtime coverage. |
| Scoring releases and persistence | `test_impact_persistence_contract.py`, `test_impact_manifest*`, `test_release_*`, `test_swap_*`, `test_compare_v4_reference.py`, `test_backfill_impact_candidate.py` | Important PostgreSQL and corpus tests are opt-in; baseline generation has a specific fail-open path described below. |
| Player profiles, filters, account groups, social circles | `test_player_views_filter.py`, `test_player_filter_page.py`, `test_filter_cache.py`, `test_account_groups*`, `test_circle*`, `test_form_card_filter.py` | Many route tests inject a Request/session and replace cache I/O. Full HTTP, actual cache storage, and multi-session recovery need a small connecting layer. |
| Cache validation and invalidation | `test_player_view_cache.py`, `test_site_stats.py`, `test_site_stats_cache.py`, `test_verify_*`, `test_circle_invalidation.py` | Good serializer/version/fingerprint tests already exist. Add transaction and observable-page tests rather than repeating validator cases. |
| Statistics and sessions | `test_round_combo_stats.py`, `test_eco_followup.py`, `test_enemy_at_11_response.py`, `test_sessions.py`, `test_squad.py`, `test_match_streaks.py`, `test_map_streaks.py` | Several other live statistics calculators lack comparable numerical boundary tests. Match streaks and map streaks do not test round-win momentum. |
| Ingestion | `test_trackergg_history_pagination.py`, `test_ingest_retry.py`, `test_ingest_outcome_reporting.py`, `test_ingest_preflight.py`, `test_ingest_ledger.py`, `test_round_player_spend_ingest.py` | Retry tests stub the loader, scorer, and invalidation. Spend tests exercise a small synthetic payload, not the full committed ingest/cache/recovery contract. |
| Replay validation, ownership, lifecycle, storage, HTTP data | `test_replay_contract.py`, `test_replay_link.py`, `test_replay_lifecycle.py`, `test_replay_store.py`, `test_replay_routes.py`, `test_replay_upload.py` | Already broad; avoid claiming uploads or replay lifecycle have no coverage. Most site routes are exercised directly, while the worker has actual HTTP tests. |
| Replay recovery and workers | `test_replay_reparse_auto.py`, `test_replay_archive_web.py`, `test_replay_worker_archive.py`, `test_replay_worker_*workflow.py`, `test_replay_worker_runs.py` | Lost replies, crashes, deduplication, deletion races, and concurrency already have substantial coverage. PostgreSQL execution and real app/browser boundaries are the main additions. |
| Control, heights, features, gaps | `test_control_*`, `test_feature_*`, `test_height_*`, `test_gaps_*`, real-map and reference tests | Many costly cases are excluded by the default marker. Keep the independent references, geometry tests, and worker-image checks. |
| Browser-side calculations and layouts | Node subprocess tests in `test_replay_viewer.py`, `test_control_viewer.py`, `test_gaps_viewer.py`, other replay modules; rendered-template `test_wide_layout_*` and `test_page_sections.py` | Node helpers and HTML structure do not prove browser initialization, fetches, event wiring, canvas rendering, or responsive usability. |
| Coaching and moments | `coach/test_checker.py`, `test_runner.py`, `test_feedback.py`, `test_coach_store.py`, `moments/test_fights_*`, `moments/test_golden.py` | Strong deterministic pieces. A small offline snapshot-to-stored-report-to-page test would connect the layers without calling a model. |
| Migrations and administrative guards | `test_migration_0023.py`, `test_migration_0028.py`, migration tests inside replay modules, `test_with_demo_db.py`, `test_with_friends_db.py`, `test_postgres_test_guard.py` | Individual migrations and guard functions are tested, but no full empty-database-to-head persistence test was found. |

### Test execution and CI gaps

- `webapp/pytest.ini` selects `not slow` and starts xdist workers by default. Geometry is marked using a source-text heuristic in `tests/replays/conftest.py`; a marker is not evidence the cases ran.
- The only tracked workflow found was `.github/workflows/replay-worker-review.yml`. It builds the production worker image and invokes **13 replay test modules**. Keep it. It is not a general FastAPI/scoring/statistics/PostgreSQL test lane.
- That workflow's path filter does not include several runtime inputs it builds or uses, including `replay_tasks/**`, `webapp/app/moments/**`, `webapp/app/static/data/**`, and `webapp/requirements.txt`. A change confined to one of those paths can miss this review job. Verify intended trigger coverage when making the plan.
- PostgreSQL cases skip when `VALO_TEST_DATABASE_URL` is absent; some additionally skip when the release write gate is absent. Several viewer tests skip when Node is unavailable. The Swiftplay and Competitive sanitized export fixtures are actually committed under `webapp/tests/fixtures/replay/`; they should be required in their designated lane, rather than treated as perpetually pending.
- Shared PostgreSQL fixtures perform broad DELETEs, gate changes, and table renames. Examples: `test_account_groups_pg.py` and `test_swap_impact_scores.py`. The default parallel run is not a safe isolation policy for a shared test database. Use a serial PostgreSQL lane first; any later parallel design needs independent databases/schema state and separate gate state.
- `CLAUDE.md:146` has newer test commands and timing guidance than the mirrored `AGENTS.md`. Plan a synchronized testing section after choosing the actual lanes; neither file was edited in this audit.

## Prioritized high-level test specifications

Each ID below is a planning unit. Turn its scenarios into concrete tests with fixtures, expected results, placement, runtime lane, and a regression it will catch. Proposed filenames are suggestions, not files created by this audit. **P0** repairs confidence in the test process or deployment schema; **P1** protects important unverified behavior; **P2** connects layers already substantially covered.

### T01 — Make required coverage run and make omissions visible (P0)

**Evidence:** `webapp/pytest.ini`, all three conftests, `tests/_postgres.py`, `.github/workflows/replay-worker-review.yml`.

**Deliverable:** a lane/selection plan plus small tests for the harness's own contracts, not a test that merely checks workflow YAML contains a string.

- Given a clean checkout with only documented dependencies, each lane collects and executes its intended tests. Missing required Node, PostgreSQL setup, sanitized fixtures, or the release gate fails that lane's setup with a clear reason. Optional local corpus probes may skip and must be reported separately.
- Given two PostgreSQL tests that both change shared tables or the gate, the runner either serializes them or gives them independent databases. A test's committed sentinel cannot be removed by another test's cleanup.
- Given a normal change to routes, statistics, scoring, schema, or test helpers, general CI runs. Given a change to each worker runtime input listed above, the worker-image review runs. Verify with a documented representative path matrix and the resulting job selection.
- Given `slow` tests, default/fast counts and full/release counts are reported separately. Demonstrate that a deliberately failing slow test fails the required full lane; do not accept a green run that simply deselects it.

**Acceptance:** baseline records collected/passed/failed/skipped/deselected counts and skip reasons per lane, and the PostgreSQL lane executes actual database cases. First reproduce the existing workflow as written; check its pytest/plugin/config behavior inside the image before adding more cases.

### T02 — Golden files fail closed; current and historical formulas are explicit (P0)

**Evidence:** `tests/test_impact_golden_both_regimes.py:125`, `app/scoring/impact_runtime.py`, `test_impact_v4_flags.py`, `test_impact_manifest_v4.py`, `test_impact_persistence_contract.py`.

- Given a missing golden file, running the ordinary assertion fails and creates nothing. Given a modified produced value, it fails against the existing baseline. Given an unreadable or malformed baseline, it fails with a diagnostic. Test the helper against an isolated test directory, never by deleting the committed golden.
- Given explicit legacy, rc3, and v4 configurations for a small match with pre-plant, post-plant, decided-round, trade, and economy events, the intended configuration is named in the test and its independently reviewed expectations are checked. Historical baselines remain historical; do not regenerate them to make a runtime test pass.
- Given the runtime-selected verified configuration, the production scoring entry point and persisted fields/version agree with that configuration. A missing, tampered, or version-incompatible manifest must refuse before score writes. Reuse existing manifest refusal cases and add only the runtime-to-persistence connection they do not prove.

**Acceptance:** no ordinary test creates or updates a baseline; current-runtime assertions cannot accidentally pass using the no-kwargs legacy path. Any new expected score or golden is reviewed under the scoring process, not copied from the current implementation as its own oracle.

### T03 — Full Alembic deployment path persists and preserves data (P0)

**Evidence:** `app/migration_guards.py:26`, `alembic/env.py`, migrations `0001`–`0028`, `test_migration_0023.py`, `test_migration_0028.py`. `migration_guards` had no name reference anywhere in test/support source during this audit.

**Suggested home:** `tests/integration/test_migration_lifecycle.py`, serial disposable PostgreSQL lane.

- Given an empty disposable PostgreSQL database, run the real `alembic upgrade head` command with an explicitly supplied scratch URL. Reconnect using a new connection/process and assert the head revision, representative tables, columns, constraints, indexes, and data access persist. Run upgrade again and assert it is a no-op. This specifically guards the historical SQLAlchemy autobegin/rolled-back-migration failure described in `migration_guards.py`.
- Given a database at `0027` containing unfiltered player/group cache rows and representative application data, upgrade to head, write filtered rows, and verify a fresh session can use both. Exercise the documented `0028` downgrade/upgrade semantics: unfiltered data survives and filtered rows are deliberately retired. Extend existing migration tests rather than duplicating every statement.
- Given a second session holding an incompatible lock, migrations fail within a shortened test timeout and leave the revision/data unchanged. After releasing the lock, retry succeeds. Verify PostgreSQL connection options actually apply; a unit assertion on the options dictionary alone is insufficient.

**Acceptance:** database setup uses Alembic, not only `Base.metadata.create_all`. Release write-gate installation is a separate explicit setup step where required; do not assume migrations install it. Do not prescribe a full head-to-base downgrade as a general requirement when migrations intentionally retire derived data.

### T04 — Login, logout, search, and redirects through the real app (P1)

**Evidence:** `app/routers/auth.py`, `app/services/auth.py`, `app/services/players.py:70`, `app/main.py:74`, `app/routers/friends.py:_safe_next`. No tests directly named/called `login_submit`, `login_form`, `find_player_by_search_query`, or `_safe_next` were found. Current search strips the tag and uses `ilike`; search redirects also interpolate an unencoded display name.

**Suggested home:** `tests/http/test_auth_navigation.py`; use the real router registration and session middleware with an isolated database.

- Given an anonymous browser, GET login, submit a known player as a form, follow the 303 redirect, and verify the signed session selects that player. Logout clears it and remains safe when repeated. A deleted-player session and a tampered cookie are handled as anonymous. Assert cookie security attributes for configured HTTP/HTTPS modes through actual headers.
- Given an unknown player or a missing form field, assert the intended status and no session mutation. Exercise the actual request parser and dependency injection; a direct Python call cannot test these failures.
- Given tagged friends identities, untagged demo identities, whitespace/case variants, duplicate base names with different tags, Unicode, and names containing `#`, spaces, `%`, or `&`, searching either resolves the intended unique player or displays an intentional no-match/ambiguity result. Following the Location header reaches that player's page.
- Given friend-add redirects with a local path/query versus an external URL, `//host`, and malformed path input, assert the approved local redirect contract through HTTP.

**Decision before implementation:** define exact full-Riot-ID versus base-name fallback search behavior, ambiguity handling, and whether wildcard input is literal. The present code is evidence of a possible bug, not a reason to encode incorrect behavior as the expected result. Login is currently identity selection, so do not silently turn this coverage task into an authentication redesign.

### T05 — Statistics cards with hand-calculated denominators (P1)

**Evidence:** `app/services/force_buy_stats.py`, `halftime_conversion_stats.py`, `score_reached_stats.py`, `round_streak_stats.py`, `map_side_stats.py`. Existing `test_site_stats.py` checks group selection and blob shape; `test_site_stats_cache.py` and `test_wide_layout_squad_stats.py` mainly validate supplied aggregates/rendering. No comparable direct numerical suites for these five calculators were found. Existing round-combo, eco-followup, enemy-at-11, match-streak, and map-streak tests cover different products.

**Suggested homes:** `test_force_buy_stats.py`, `test_halftime_conversion_stats.py`, `test_score_reached_stats.py`, `test_round_streak_stats.py`, `test_map_side_stats.py`. These should mostly be fast tests over explicitly built model objects.

| Product | Minimum high-level cases and independently calculated assertions |
|---|---|
| Force buy after losing pistol | Team spends just below, exactly at, and above 70% of team `(loadout + remaining)` in rounds 2/14. Assert only the loser is sampled. Use uneven player budgets to distinguish ratio of team sums from average player ratios. Missing/zero economy produces no fabricated sample. Truncate after the forced round: `forced`, `next`, `next2`, and `match` retain their own valid denominators. Include a tie/unknown match result. |
| Halftime conversion | A full 9–3 first half and decisive final result contributes complementary team samples to buckets 9 and 3. Missing/unknown rounds among 1–12 exclude the sample. Changes after round 12 do not change the halftime bucket. Tied final scores are excluded. Builder orders 12 down to 0 and omits empty rows. |
| Score reached and overtime | A 13–11 match contributes each team's samples to every reached bucket from 0 through its score, capped at 12. Overtime is counted once per match, even with multiple friends or friends on both teams. Compare rounds ending at 24 with a round 25/26 match. Tied/unknown results and zero-sample display remain intentional. |
| Round-win momentum | For a short explicit W/L sequence, calculate totals and wins for each follow-up length by hand, including windows that contain a loss. Missing/unknown rounds stop the window rather than become losses. No window crosses 12→13, 24→25, or an overtime side swap. Unresolved final match scores can still contribute valid round windows. |
| Map side and verdict | Check round boundaries 1/12/13/24/25/26, both winners, and unknown outcomes. Multiple friends in one match do not multiply match/round samples. A missing map name is grouped intentionally. Check verdict thresholds on both sides of `z=±2`, no-round data, and ordering by significance rather than rounded percentage. |

For **every** calculator, include outsider-only matches, group players on the opposite team, multiple group members on one team, and members on both teams. Team-level and match-level samples must not accidentally become per-player samples. The personal pistol-match metric deliberately counts players and must keep that distinct contract.

**Decision to document:** legacy surrendered placeholder rounds exist in stored data. Specify their treatment for each card before adding cross-card assertions; do not assume every card already shares the same exclusion policy. Assert `0 <= wins <= total`, and verify empty-state builders do not divide by zero. Use fixed expectations, not another production calculator as the sole oracle.

### T06 — Tracker payload to committed match, score failure, and recovery (P1)

**Evidence:** `app/adapters/trackergg_browserstate_source.py:629,819,948`, `test_ingest_retry.py`'s `wiring` fixture, `test_round_player_spend_ingest.py`.

**Suggested home:** `tests/integration/test_tracker_ingest_contract.py`, isolated PostgreSQL. Feed sanitized static API-shaped payloads or a fake page; no live tracker.gg access.

- Given a small complete Competitive payload, ingest through the real loader/orchestrator and verify from a fresh session the expected players, match, rounds, kills, optional spend rows, and configured scores. Include assists, an ordinary trade, halftime/overtime boundaries, and a surrendered placeholder round. Pin the expected omission of placeholder rounds and preservation of NULL versus known zero.
- Given a malformed later segment that fails after some rows were flushed, no partial new match survives; a subsequent valid match on the same session succeeds. Previously ingested unrelated matches remain unchanged.
- Given an already imported external ID, a second normal ingest does not duplicate data or repeat fetch/scoring work. Given a scoring failure **after** the match commits, the match remains recorded, stale affected caches are removed, and reporting describes the split outcome accurately.
- Given that stranded unscored match, the real backfill entry point repairs it under an explicit valid scoring configuration, invalidates/rewarms as appropriate, and is idempotent on the next run. A closed gate or invalid manifest refuses before new import writes or cache deletion.

**Acceptance:** fetches and waiting may be replaced; do not replace the loader, session commit/rollback, cache deletion, and scorer all at once. Capture committed state and unrelated-data sentinels. Concurrent duplicate import is a separate policy decision if normal CLI operation is intended to be serial.

### T07 — CLI orchestration: one bad player does not lose successful work (P1)

**Evidence:** `scripts/ingest_trackergg_player.py:72`, `scripts/refresh_tracked_players.py:141`; existing discovery/outcome/ledger tests cover component behavior, but no script-main tests for this complete orchestration were found.

- Given a roster containing a successful player, a private profile, a transient failure, and another success, the command continues as intended, unions affected player IDs, prewarms each affected player/group without duplicate work, and reports truthful discovery/import results and the documented exit code.
- Given `--no-prewarm`, player and viewer prewarming are skipped but the required site-wide invalidation/refresh still happens when appropriate. A no-change run does not manufacture dirty IDs or refresh work.
- Given an unexpected player failure or prewarm failure, verify the intended continuation policy, ledger end record, database close, and page/browser resource cleanup. Test a malformed roster and CDP connection failure before any imports too.
- Given a newly ingested match with a previously unlinked replay, the command attempts the existing best-effort linking step. A linking failure cannot undo committed imports or falsely mark successful matches as missing.

**Acceptance:** fake browser/CDP and sleep boundaries and use temporary test ledger paths. Do not launch the owner's Chrome profile. Specify exit/failure policy from the command's supported contract instead of assuming every nonfatal message implies exit zero.

### T08 — Real cache write-through and social invalidation visible on pages (P1)

**Evidence:** `test_player_filter_page.py` spies cache functions; `test_filter_cache.py` already tests PostgreSQL upserts and keyed row isolation; `test_circle_invalidation.py` checks several fresh-session deletions. `app/routers/players.py` writes through using its own session and concurrently loads map streaks.

**Suggested home:** `tests/integration/test_profile_cache_journey.py`, serial PostgreSQL and the actual HTTP app.

- Given more than 30 matches, tied timestamps, one undated match, and linked accounts sharing a match, load recent/career and a role/agent filter. On a cold read, live values and persisted keys are correct; on a fresh warm request, visible values match and the heavy computation is not called. Keep existing newest-first/NULL ordering and deduplication contracts.
- Given corrupt or old-version player/group cache rows, request the page through its normal read path and verify a successful live result, correct keyed replacement, and a usable request session. Inject a cache write error in its separate session: the page still returns the calculated content. Repeat for a combined filtered view.
- Given a friendship or account membership change through a real form POST, previously cached player/group/viewer views for affected social circles are retired in the same committed operation and the next request reflects the new circle. Unrelated viewers retain their rows. A forced mutation failure leaves both membership and relevant cache state consistent.
- Given a new imported match, repeat the page/cache assertions after T06's real invalidation. Include a player represented only by filtered cache rows so cleanup does not leave stale filtered results behind.

**Acceptance:** reuse existing compute/serialization fixtures and spies only to count work, not to fabricate cache hits. Compare semantic page data/chart payloads, not entire HTML strings. Do not add another full validator matrix.

### T09 — Demo startup, request restrictions, and sample-data rebuild (P1)

**Evidence:** `test_site_modes.py`, `test_account_groups_routes.py`, `test_replay_routes.py`, `test_with_demo_db.py`, `app/main.py:27`, `scripts/load_seed_data.py`. Existing mode guards are real coverage, but individual route calls do not test complete app startup and sample-schema compatibility.

- Given the real app configured for demo mode, startup does not start replay/control/archive/map-pool/state background activity or contact the worker. Exercise representative reads and all relevant mutation/privileged route families: friend/account changes refuse, replay/upload/admin/coach/gap routes follow their demo contract, and no hidden request mutates the seeded state.
- Given `ENABLE_RIOT_TXT` on/off independently of demo mode, GET `/riot.txt` and `//riot.txt` through HTTP and assert status, exact body, and plain-text behavior. Assert demo branding from actual configuration initialization in a fresh app/process, not only manual Jinja-global replacement.
- Given the committed `seed_data/demo_matches.sql` and an empty disposable database migrated to head, load the seed, exercise representative player/match/stats pages, and check deterministic sample entities and expected empty replay/height features. Confirm a new connection can read the result. Keep real remote wrapper tests separate from this scratch-only rebuild.
- Given non-demo settings, the same app fixture uses a separate scratch dataset. Demonstrate database/config separation; do not claim a `source != SCRAPED` query would protect the demo.

**Acceptance:** never use the live demo or friends database. The rebuild runs only in a test lane, never as an addition to the deployed demo build command. Test seed re-run behavior only after determining whether the supported rebuild contract requires an empty database.

### T10 — Full HTTP and browser contracts beyond template structure (P1)

**Evidence:** `test_wide_layout_*`, `test_page_sections.py`, Node viewer tests. A raw-ASGI GET helper already exists in `replays/test_replay_reparse_auto.py:1394`; it tests an admin router on a small FastAPI app, not the whole middleware/session/browser journey.

**Suggested homes:** shared HTTP test helper and a small browser smoke suite. Reuse/extend the existing ASGI approach where it is adequate. If proposing `httpx`, keep the test dependency decision explicit; do not casually change frozen runtime/scoring environments.

- Given player names with tags and a filter/combined-account selection, navigate in a browser, change the dropdown, open recent/career fragments, and verify content and selection remain consistent without duplicate IDs or JS exceptions. Repeat one representative stats/session expansion that fetches a fragment.
- Given a stored sanitized replay, open the actual viewer and verify its round fetch succeeds, the canvas initializes, a time/round seek changes the displayed state, and moments/control/gaps toggles correspond to the selected round. Click a coach/moment link and verify round/time navigation. Assert meaningful state/geometry rather than pixel-identical screenshots.
- Given empty or unavailable optional replay/control data, the viewer shows the supported state and remains usable. Capture browser console errors and failed required network requests as failures. Node decoder parity tests remain in place.
- Given desktop and narrow viewports, critical controls and tables are reachable and fragment interactions still work. Keep a small accessibility smoke check for control labels and keyboard operation; do not create a full visual-baseline project in the first pass.

**Acceptance:** no live Render or tracker.gg dependency. Local test app, explicit scratch data, vendored resources or controlled stubs for external assets, and deterministic waits for observable state. Start with two or three journeys, not exhaustive UI permutations.

### T11 — Required sanitized fixtures and architectural guards cannot quietly disappear (P1)

**Evidence:** `test_replay_fixture.py:124,161`, `test_replay_condense.py:678`, `test_replay_streaming.py:17`, `test_replay_isolation.py`.

- Given a checkout missing a required Swiftplay/Competitive fixture, the designated fixture lane fails. Given each committed fixture, the existing allowlist and identity scanner still run. Keep the private-owner `.vrf` probe optional and separate; it is a different input from the committed sanitized exports.
- Given a deliberately forbidden scoring import inside a nested replay/worker module or an alternative import form, the architecture checker fails. Given the specifically allowed read-only replay-impact imports, it passes. Test transitive boundary risks only where they matter; retain explicit exceptions rather than expanding an opaque allowlist.
- Given a replay ingest/replacement operation, inspect the before/after score-table state in an isolated integration test and assert no Impact rows changed. This complements import analysis, which alone cannot prove runtime write isolation.

**Acceptance:** required directories/modules cannot vanish and produce a vacuous pass. The architecture rule protects replay/scoring isolation without forbidding legitimate scoring changes elsewhere in the project.

### T12 — App lifespan starts and stops all owned services predictably (P2)

**Evidence:** `app/main.py:27`, `test_replay_match_states.py:test_lifespan_starts_the_refresher`, subsystem-specific `start` tests. Current lifespan starts four service entry points and sets returned stop events on shutdown.

- Given normal startup with four fake service factories, every service receives the intended session factory once; each returned stop event is set when lifespan exits. Services returning `None` are handled.
- Given an exception while the app is running, shutdown still signals the services it started. Given a later service's startup failure, the plan must specify cleanup of earlier starts. These are failure-path expectations to investigate, not observed failures from this audit.
- Given demo/off configuration, the full app creates no unexpected background network/database activity. Keep per-service switch tests and add this aggregate negative assertion.

**Acceptance:** no real daemon/thread/network work in this test. If failure-path cleanup needs production code changes, propose the narrow fix separately from adding tests and demonstrate the failing scenario first.

### T13 — Offline coaching data survives the complete storage/display seam (P2)

**Evidence:** existing `coach/test_runner.py`, `test_snapshot.py`, `test_coach_store.py`, `test_coach_page.py`, `test_feedback.py`, `test_enemy_names.py` already cover their components well.

- Given one linked replay snapshot and deterministic model replies, run the real checker/runner, store the resulting report with the real service, and read the coach page through HTTP. Preserve checked/unchecked status, source gaps, report version, and instances' round/time links.
- Given opponent names and text requiring HTML escaping, assert final rendered names follow the existing teammate/opponent policy and report text remains escaped. A subsequent logged-in feedback POST is attached to the intended match/point/moment and appears on a fresh read.
- Given an obsolete report or a replay whose required data changed, verify the existing supported fallback rather than silently presenting an unrelated report as current. Inspect the current version association before specifying this case.

**Acceptance:** deterministic model stub, no model API, no owner setup, no live replay data, and no duplicate checker test matrix.

### T14 — Demo adapter and legacy import utility get proportional coverage (P2)

**Evidence:** `app/adapters/demo_match_source.py` has no direct reference in the test/support source scan. It imports root `matchDataPipeline.py`, whose parser/GUI dependencies and hardcoded paths make uncontrolled import/run unsuitable for normal CI.

- Given synthetic parsed round/player/kill data at the adapter boundary, loading a sample match maps teams, rounds, events, and optional metadata correctly. Replacing the same external ID leaves no orphan children; a failure during replacement rolls back the old data. Use real foreign keys in the integration version.
- Given ambiguous, invalid, variable-width, and overridden match keys, test `_parse_played_at` and map-name parsing against explicitly documented expected values. Preserve the demo tool's PM assumption as a local import convention, not a future Riot adapter requirement.
- For the root utility, propose only a small offline parsing/icon-classification fixture if it can be isolated without GUI/path refactoring. Automated mouse movement, Save As dialogs, live snapshots, and a large modernization of this one-person tool are out of scope for the first coverage plan.

**Acceptance:** prioritize the deployed sample seed rebuild in T09 first. Do not require the root GUI dependency stack for every web-app test run. Defer extraction work unless a minimal test seam cannot be achieved safely.

## Stale tests and maintenance candidates

These are findings to bring forward, **not instructions to delete old tests**. No obsolete assertion was changed in this audit.

| Candidate | Evidence and classification | Proposed treatment |
|---|---|---|
| Missing golden automatically becomes a passing baseline | `test_impact_golden_both_regimes.py:125`: creates the directory and writes when `REGENERATE` is true **or the file does not exist**; the missing-file branch returns successfully. **Confirmed weakness.** Both golden files are currently committed. | Fail on missing/malformed baselines; keep regeneration an explicit reviewed operation. Add negative helper tests from T02. |
| Legacy golden called “shipped scoring” | Same file's module description and `test_golden_shipped_scoring_both_regimes` call the scorer with no explicit configuration. Runtime source selects a v4 manifest. **Confirmed stale labeling/coverage interpretation.** | Rename/reword it as legacy configuration coverage and make historical configuration explicit; add separate runtime coverage without changing historical values. |
| “No DB fixture exists in this repo” | `test_step1_query_merges.py:1` gives this reason for MagicMock sessions. Many current modules now have SQLite/PostgreSQL fixtures. **Confirmed stale rationale**, not proof those tests are useless. | Update the explanation and keep useful query-shape checks. Add real scoped-join/read behavior through T08. |
| Scope slicing test never invokes production code | `test_player_view_cache.py:test_scope_slicing_matches_recent_match_limit` slices `list(range(50))` and checks Python's list behavior. **Confirmed low-value test.** | Replace or retire after mapping to `test_player_views_filter.py:test_filtered_recent_is_the_newest_thirty_of_that_filter` and account-group limit tests. Preserve a production-level unfiltered 30/31-match boundary if absent. |
| Scoring isolation uses moving `origin/main` | `test_replay_isolation.py:test_no_scoring_hashed_source_changed` compares all manifest-hashed source paths to `origin/main` and skips when unavailable. Its docstring describes migration 0012. **Confirmed branch-task-specific restriction**, not a general scoring invariant. | Move it into a replay-only review check with a documented base revision, or replace it with architecture/runtime non-write tests. Do not keep a universal assertion that legitimate scoring branches must have no scoring diff. |
| Shallow import scan and missing-file tolerance | The same file uses nonrecursive `glob('*.py')`, and its first test skips nonexistent listed paths. **Confirmed boundary of the checker**; no claim that forbidden nested code currently exists. | Require expected roots and cover future nested modules/import forms with positive and negative test fixtures. Keep meaningful worker/no-database guards. |
| Required-fixture tests still say “pending” or skip absent fixtures | `test_replay_fixture.py` skip reason says the Swiftplay export is pending, while both sanitized export directories are tracked. `test_replay_condense.py` and `test_replay_streaming.py` have existence-based skips. **Confirmed stale message and fail-open prerequisite policy.** | Required fixture lane fails if tracked assets disappear; keep local optional behavior only if clearly distinguished. Private `.vrf` owner-machine header probe remains optional. |
| Heading/layout snapshots freeze product text/structure | `test_page_sections.py` compares source-derived heading multisets; some CSS checks assert literal selectors. **Maintenance candidate**, not obsolete: all seven cases passed in this audit. Other `test_wide_layout_*` cases render HTML and check valuable uniqueness/scroll rules. | Keep card presence, duplicate-ID, script-target, and scrollability contracts. Relax incidental exact heading/row/CSS spelling only alongside T10's behavior tests. |
| Cross-imported test fixtures and global dialect shims | Coach conftest imports replay test helpers; group/filter/circle tests import fixtures from other test modules. Some modules globally register JSONB-on-SQLite compilation. **Coupling/order-risk candidate**, no observed failure claimed. | Extract only genuinely shared factories into focused support modules; test isolated module collection and representative order changes. Do not rewrite the entire suite or replace PostgreSQL semantics with SQLite shims. |
| Older rc3/v4 release and legacy evaluation tests | `test_swap_impact_scores.py` explicitly models rc3 names; `test_swap_parameterised_names.py` covers v4 names. `test_kill_order_leverage_v4_legacy.py` intentionally retains legacy timing for analysis. **Still relevant**, not obsolete because of age. | Retain release/rollback compatibility, explicit legacy comparators, and independent references. Label historical/corpus/operational tests correctly and provide the environments they need. |

Do not infer obsolescence from an old date, an internal helper name, or a skipped optional corpus. A replacement should protect the same requirement through a stronger boundary before removing an older regression.

## Execution and verification plan

### Phase 1: Establish a reproducible baseline

After authorization to implement/run write-capable tests, inspect current git state and record the new HEAD. Inventory current collected node IDs and prerequisites. Capture baseline results for fast, Node, PostgreSQL, slow geometry, and worker-image suites. Record failures before editing expected values. Resolve test-harness failures separately from product bugs or genuinely obsolete expectations.

Finish T01/T02 first. Create disposable PostgreSQL databases through an explicit scratch setup, run Alembic, then install the gate only for the lanes that need it. Run shared-state database cases serially. Existing tests using `*_rehearsal` for read-only corpus measurements belong in a separate opt-in lane, not ordinary pull-request CI.

### Phase 2: Fill the highest-value product holes

T03 and T05 can be planned independently once fixture/environment design is settled. Then T04, T06, T07, T08, and T09 connect request parsing, durable state, and recovery. Use one coherent synthetic match family and small expected aggregates; avoid importing a production database dump to make fixtures convenient.

### Phase 3: Add a few cross-layer acceptance journeys

Implement T10/T11, then T12/T13 if the baseline confirms their value. T14 is a deliberately smaller final task. Preserve the existing extensive replay/crash/race coverage; do not replace it with a handful of smoke tests.

### Proposed lanes

Markers below are **proposed**. Only `slow` and `geometry` are registered in the audited pytest configuration. Do not copy commands assuming `postgres`, `browser`, or `corpus` already exist.

| Lane | Contents | Environment and scheduling |
|---|---|---|
| Fast offline | Pure math, statistics calculators, validation, small synthetic services, HTTP tests that do not need PostgreSQL | Every relevant PR; Python 3.13; no external network; normal parallelism only after fixture isolation is verified. |
| Node helpers | Existing JS/Python parity, viewer/tagger helper behavior | Every affected PR; Node required, not silently skipped. Keep helper tests even after browser tests exist. |
| PostgreSQL | Locks, triggers, upserts, constraints, transaction recovery, migration/seed integration | Disposable PostgreSQL matching the project's major version (18 in Compose); explicit scratch URL, migrations/setup, **`-n 0`** initially, required prerequisites. |
| Browser acceptance | Small T04/T08/T10 navigation set with real app middleware and stored scratch data | Explicit browser installation and isolated local server; no background production clients; controlled external resources. |
| Slow geometry/reference | Existing real-map, expensive engine, acceptance/reference cases | Run before merges affecting control/gaps/heights/features, plus a scheduled/full validation policy. Pin assets and record exclusions. |
| Worker image | Existing Docker workflow and production packaging checks | Preserve existing lane; fix trigger/input omissions after proving its current behavior. Include `replay_tasks` and relevant built assets/dependencies. |
| Optional corpus/private input | Real-corpus measurement tests and private-owner `.vrf` probe | Explicit opt-in with read-only approved inputs; not a hidden requirement for a clean public checkout. |

The existing local run commands from `webapp/` are `python -m pytest` (default excludes slow), `python -m pytest -m slow`, and `python -m pytest -m ""` (all markers selected). Use `.venv313/Scripts/python.exe`. These are **not read-only commands** and were not run in this audit. A full run still needs explicit environment isolation; `-m ""` does not supply PostgreSQL, Node, or private corpus inputs.

For the two restricted checks actually run here, the equivalent invocation was the following, fed to the existing Python interpreter on stdin from `webapp/`. It loads xdist only to register the conftest's hook; no workers are requested, no capture files or cache are used, and the selected tests need no output fixtures:

```python
import os
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import pytest
raise SystemExit(pytest.main([
    "-o", "addopts=", "-p", "xdist.plugin", "-p", "no:cacheprovider", "-s", "-q",
    "tests/test_stats_math.py", "tests/test_agent_roles.py", "tests/test_weapon_prices.py",
    # test_page_sections.py was run separately with the same options.
]))
```

Invoke with Python `-B` as well. This narrow selection is audit evidence only, not a substitute for the proposed lanes.

### Quality and completion criteria

- Map each new test to one ID/scenario, the current production entry point, and the specific regression it catches. Extend an existing module when it already owns the requirement; avoid duplicate test matrices.
- Use hand-calculated aggregates, explicit declared formula configurations, and frozen independent references. A serialized output compared with itself is not correctness evidence.
- For critical refusal/checker paths, prove the check can fail by injecting a bad input through the same public entry point. Especially cover missing baselines, invalid manifests, unsafe database targets, and missing required fixtures.
- Record current pass/fail/skip/deselected counts, prerequisites, runtime, and the command used for each lane. No unexplained skips in a required lane. Distinguish historical/corpus probes from normal regressions.
- Run representative tests individually as well as together when fixtures install global shims, mutate settings, or import another test module. Stabilize against machine load, available memory, wall clocks, and test order using controlled inputs rather than longer sleeps.
- Measure line and branch coverage only after a reliable baseline exists, scoped to changed/risk-relevant modules. Publish uncovered branches for the five calculators, auth/search, migration guard, and orchestration. Do not invent a repository-wide percentage target before measurement, and do not count a test-file inventory as executable coverage.
- An eventual patch should include only the planned tests/harness changes and separately justified narrow fixes. No formula changes, reference refresh, schema redesign, production operation, deployment, or broad fixture refactor is implied by this audit.

## Instructions for the planning agent

Use the following as the handoff prompt:

> Read `AGENTS.md`, `CLAUDE.md`, and this audit. Produce an implementation plan for better coverage; do not start implementation or change existing expectations yet. Revalidate the checkout and evidence, since the audit describes commit `89fd0d79075635afa6adbb3ddbdad1bd743434dd`.
>
> Start with T01/T02's harness and golden-file problems, T03's complete migration path, and T05's statistics calculators. Map T04/T06–T11 to existing tests so the plan adds the missing request/database/browser boundaries without duplicating the large current suite. Keep T12–T14 proportionate and explain any deferral.
>
> For each task specify: priority; scenario IDs; existing coverage it extends; proposed test file and boundary; fixture/data design; independent expected results; environment/marker/CI lane; negative/failure cases; estimated runtime and small/medium/large effort; dependencies; and exact verification commands after the required setup. Define how required PostgreSQL/Node/fixture prerequisites fail visibly and how shared-state database tests stay isolated.
>
> Classify stale candidates as keep, relabel, strengthen, or retire-with-replacement, with a reason. Preserve historical scoring tests, compatibility checks, independent references, and privacy scans. Do not regenerate goldens or adjust score expectations as a way to get green tests. Scoring-related changes must respect `docs/superpowers/SCORING-RELEASE-PROCESS.md`.
>
> Surface the few product decisions explicitly: full-ID versus base-name search and ambiguity, wildcard handling, surrender treatment across statistics, and any unsupported concurrent-ingestion or lifespan-cleanup behavior. Propose defaults using current product intent, but do not silently enshrine a suspected bug as a contract.
>
> Return an ordered plan of small reviewable changes, a coverage-to-requirement matrix, the test-lane/CI design, baseline and final acceptance criteria, and deferred items. Preserve both site modes and their separate databases. No production or live-demo access is needed. This handoff authorizes planning only; further implementation and write-capable execution require the user's instruction.
