# Running and extending the tests

Coverage expansion branch: `codex/test-coverage-expansion`, based on `89fd0d79075635afa6adbb3ddbdad1bd743434dd`.
The original [audit](test-coverage-audit-2026-10-25.md) describes the starting checkout;
this document records its implementation and remaining limits.

## Required lanes

Run from `webapp/`, using Python 3.13 and `requirements.txt`. On this Windows checkout,
use `./.venv313/Scripts/python.exe` in place of `python`. Node is needed for JavaScript
contracts; Chromium is needed only for browser tests.

Set `DATABASE_URL=sqlite://` and leave `VALO_TEST_DATABASE_URL` unset for offline,
browser, and slow lanes. In PowerShell use `$env:DATABASE_URL='sqlite://'` and
`$env:VALO_TEST_DATABASE_URL=''`. These tests supply their own temporary stores.

```sh
# Offline application, numerical, worker, and sanitized-fixture contracts
python -m pytest -n 2 -m 'not slow and not postgres and not browser and not corpus' --require-node --require-fixtures -ra

# Slow independent references, geometry, and real-map contracts
python -m pytest -n 2 -m 'slow and not postgres and not browser and not corpus' --require-node --require-fixtures -ra

# Real browser journeys, with external requests blocked
python -m playwright install chromium --only-shell
python -m pytest tests/browser -n 0 -m browser --require-browser --require-fixtures -ra

# PostgreSQL: explicitly supply a disposable *_test URL first
python -m pytest -n 0 -m 'postgres and not corpus and not slow' --require-postgres -ra
```

`.github/workflows/tests.yml` runs all four lanes for every pull request and main
push, and uploads JUnit results. The existing production worker-image review remains
separate; its path filters now cover its shared runtime, configuration, and fixture inputs.
The default local command excludes `slow`, `browser`, and `corpus`; PostgreSQL tests
may skip locally without an explicit test URL. Use the lane commands for acceptance.

### Database isolation

- Use PostgreSQL 18 and a disposable parent database ending in `_test`, with a role
  allowed to create/drop databases. Never use `.env.remote`, `.env.demo-remote`, or
  a production/demo/rehearsal database for this lane.
- `tests/_postgres.py` validates the backend, database suffix, and query overrides
  before connecting. A nonempty `VALO_TEST_DATABASE_URL` refuses xdist: use `-n 0`.
- In required mode, each PostgreSQL case gets a randomly named child `*_test`
  database migrated by the real Alembic CLI. Existing fixtures that delete whole
  tables or rename them cannot erase another case's state. New migration tests own
  their child database directly. Teardown drops only the database that fixture created.
- Release-tool tests explicitly install a closed write gate inside their child
  database. A required PostgreSQL or browser case that skips fails the lane.
- Corpus measurements are separately marked `corpus`; their approved input data
  and release procedure are not part of ordinary CI. Their deliberate exclusion is
  not a passing measurement result.

## Audit implementation map

Paths below are relative to `webapp/tests/` unless stated otherwise. Expected
statistics use small hand-counted examples. Integration tests assert results after
fresh sessions/processes rather than comparing an output with itself.

| Audit task | Added or strengthened coverage |
|---|---|
| T01: execution and prerequisites | `test_test_lanes.py`, `_postgres.py`, `conftest.py`, and `support/lanes.py`: missing requirements fail; real slow failures stay visible; database cases classify through fixtures; parallel shared-DB runs refuse; worker trigger matrix is checked. Four required CI lanes. |
| T02: goldens and active scoring | `test_golden_contract.py`: absent, unreadable, malformed, or changed goldens fail without creating/updating a baseline. Historical formula tests explicitly select legacy behavior; runtime-version tests follow the selected manifest. `test_tracker_ingest_contract.py` connects verified runtime scoring to every persisted score field and refusal before writes. |
| T03: deployment migrations | `test_migration_lifecycle.py`: empty database through head, fresh-process persistence, repeated upgrade, 0027/0028 cache preservation, lock timeout/rollback/retry, and real statement-timeout cancellation with recovery. |
| T04: HTTP identity/navigation | `test_auth_navigation.py`: real forms, signed sessions, logout, deleted/tampered identities, search ambiguity, tags, literal wildcard characters, Unicode, encoded redirects, local friend redirects, Riot verification, cookie attributes. |
| T05: statistics | `test_force_buy_stats.py`, `test_halftime_conversion_stats.py`, `test_score_reached_stats.py`, `test_round_streak_stats.py`, `test_map_side_stats.py`: thresholds, both perspectives, exact sample denominators, empty samples, side swaps, overtime, and classification boundaries. |
| T06: committed ingestion/recovery | `test_tracker_ingest_contract.py`: synthetic API-shaped payload through loader, active scorer, cache invalidation, and PostgreSQL persistence; duplicate ingest, malformed rollback, unscored-match recovery, manifest/gate refusals, and surrender padding. |
| T07: command orchestration | `test_ingest_commands.py`: both actual CLI main functions, temporary ledgers, one bad roster member, backfill/prewarm ordering, no-change/no-prewarm paths, CDP failures, and cleanup on exceptions. Browser/network boundaries are simulated. |
| T08: observable cache behavior | `test_profile_cache_journey.py`: PostgreSQL cold/warm profile requests, recent 30 versus career 31, filtered storage, stale/corrupt cache recovery, nonfatal cache write errors, committed/rolled-back social invalidation, and unaffected-owner retention. Replaces the old list-slice tautology. |
| T09: demo protection | `test_demo_http_contract.py`: real request refusals and unchanged state for disabled route families, plus fresh-process settings/branding. Migration lifecycle tests load the committed six-match sample seed twice and request profile/match/statistics pages from that scratch database. |
| T10: browser seams | `browser/test_user_journeys.py`: desktop/narrow profile filtering and career HTMX insertion; keyboard focus and duplicate-ID check; real linked replay JSON, canvas, seeking, round changes, playback, and unavailable control. Test-only pinned scripts remove CDN dependence. |
| T11: required inputs/isolation | Required sanitized fixtures fail when absent. `test_architecture_guard.py` and strengthened `replays/test_replay_isolation.py` inspect nested, relative, and literal dynamic imports. `test_replay_score_isolation.py` replaces a real replay and checks persisted Impact rows remain identical. |
| T12: lifespan | `test_app_lifespan.py`: normal shutdown, application-body failure, each partial-start failure, and demo mode with service flags enabled. Started services receive stop signals. |
| T13: coaching | `test_coach_journey.py`: deterministic snapshot through actual runner/checker/store, escaped report HTTP rendering, feedback form persistence, current-version fallback, and foreign evidence refusal. The model boundary is offline. |
| T14: local import adapter | `test_demo_adapter_contract.py`: date/key parsing, known date override, real schema mapping, replacement/orphan removal, and rollback preserving the original match. The GUI parser is supplied as deterministic parsed input. |

`support/` holds deliberately small shared builders, raw ASGI transport, scratch-site
binding, PostgreSQL lifecycle, synthetic tracker/replay histories, and import inspection.
The worker image can load root conftest without importing SQLAlchemy or psycopg2.

### Obsolete expectations and narrow fixes

Keep historical scoring contracts and frozen goldens. Their configuration is now
explicit; active-manifest tests no longer assume the formerly active version was 3.
Automatic golden creation is removed, and regeneration always fails for human review.
No scoring source, scoring models, frozen manifest, or golden values were changed.

Replay reparse tests now distinguish local/workflow worker budgets and use the
current gaps protocol. Three PostgreSQL toy-worker cases use the same feature-free
source fixture as their SQLite equivalents. The replay-only historical branch-diff
assertion was retired; persistent import and stored-score isolation checks replace it.
Committed sanitized fixtures are required; optional private VRF probes remain optional.
The durable-workflow-record test waits for completed real atomic writes before
reading them, avoiding a Windows reader/rename race without replacing persistence.

The new regressions required small application fixes: unique full-ID/literal search
and escaped search redirects; restoration of enclosing request-trace context;
lifespan cleanup after partial startup/errors; CLI page cleanup on errors; and graceful
rejection of malformed nonnumeric demo filename dates. No deployment settings changed.

Runtime-ingest fixtures create a test-only manifest from the unchanged active
comparator and installed test dependencies. This preserves production's strict frozen
environment check while allowing pytest/xdist in the test environment; it does not
replace a frozen release manifest or prove production-environment equivalence.

## Local validation record

Verified with Python 3.13.15, temporary PostgreSQL 18.6, Node, and Playwright Chromium.
These are separate runs and overlap; do not add their counts as unique coverage.

| Selection | Result |
|---|---|
| All newly added tests together | **195 passed**, no skips, in 116.61 seconds; PostgreSQL, browser, Node, and sanitized fixtures required. |
| Non-replay offline suite | 2,399 passed; later auth/adapter changes separately rechecked: 33 passed. |
| PostgreSQL required lane | Initial full run: 119 passed, 3 stale toy-fixture failures. All three passed after fixture corrections; migration/seed additions and their corrections also passed in focused reruns. |
| Real ingestion and real cache journeys | 6 ingestion cases and 7 cache cases passed against PostgreSQL. |
| Browser journeys | 3 passed (desktop, narrow layout, replay). |
| Replay nongeometry suite | 1,244 passed; 2 Windows timeout-cleanup failures passed when rerun outside the process-restricted sandbox. No parser cleanup code was changed. |
| Replay reparse after expectation repairs | 129 passed. |
| Harness self-tests | 9 passed, including refusal of parallel shared-database runs. |
| Additional migration connection checks | 2 passed, including actual PostgreSQL statement cancellation/recovery. |
| Worker lightweight dependency smoke | 1 passed with SQLAlchemy/psycopg2 imports forbidden and plugin autoload disabled. |
| Slow lane | 151 passed, 3 failed in 27:58. Two process-preemption cases passed outside the Windows sandbox. The remaining durable-record reader/rename race was repaired using write-completion events and passed its focused rerun. |

The first broad replay run stopped after ten stale expectation failures (2,434 passed,
11 optional skips); later targeted runs corrected those cases. This record is not a
claim that a single final invocation of the entire existing suite completed without skips.

## Deliberate limits

- No new scoring oracle or golden values: release mathematics remain governed by
  `superpowers/SCORING-RELEASE-PROCESS.md` and existing independent references.
- No live tracker.gg browsing, Riot API calls, model requests, private replay corpus,
  production/demo database operations, deployments, or root GUI automation.
- Browser tests cover representative navigation and playback, not complete WCAG or
  cross-browser certification. The import checker recognizes literal dynamic imports,
  not arbitrary computed Python imports.
- Ingest excludes fabricated surrender padding. Existing statistics treatment of
  historical surrender placeholders is unchanged pending a product-wide policy.
- Docker was unavailable locally; CI must build and execute the actual worker image.
  The lightweight conftest smoke does not substitute for that image run.
- Line/branch coverage percentages have not been measured. Case counts are execution
  evidence, not percentages; add coverage tooling separately if numerical tracking is wanted.
