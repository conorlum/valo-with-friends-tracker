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

### Suites: what to run for a change

`webapp/tests/suites.toml` sorts every test file into one of 24 suites. Each suite says what it checks
(`about`) and which code it guards (`guards`). `scripts/test_catalog.py` reads it, from `webapp/`:

```
python scripts/test_catalog.py list                    # every suite: what it checks, its tests, serial time
python scripts/test_catalog.py plan --git              # the suites this branch needs, per-lane time and commands
python scripts/test_catalog.py plan app/control/engine.py
python scripts/test_catalog.py run --git [--slow]      # run them serially, then a per-suite report
python scripts/test_catalog.py check                   # the catalog is complete (tests/test_test_catalog.py)
```

How `plan` works:
- It takes paths in git's form or relative to `webapp/`.
- It picks each suite that guards a changed file, plus the suite of any changed test.
- A changed helper in a test folder picks every suite with tests in that folder.
- A change to `pytest.ini`, `tests/conftest.py`, `tests/support/` or the requirements picks every suite.
- Changed code that no suite guards is listed, never silently dropped.

`plan` then collects the chosen files and sorts their tests by marker into lanes:
- default
- slow (`--slow`)
- postgres (needs `VALO_TEST_DATABASE_URL`)
- browser (`--browser`)

For each lane it prints a time estimate from `timings.json`, a window allowing for a loaded machine, and the
command. It also warns when the toy-map cache has no build for the current casting code.

`run` runs the lanes and then reports each suite: passed, failed and skipped tests, and its actual time against
the estimate. It also lists any test that took over 3x its recorded time. `--record` writes the run's times
back to `timings.json`.

`check` fails when:
- a test file is in no suite, or in two;
- a glob matches nothing;
- a file under `app/` or `replay_worker/` is guarded by no suite.

So a new test file or module must be added to `suites.toml`.

### Run times

`webapp/tests/timings.json` holds each test's serial run time, so a selection can be sized and split into
parts without first timing the whole suite (`python scripts/test_timings.py show --by file`). Refresh it from
any serial run's JUnit XML: `python scripts/test_timings.py refresh <junit.xml> [--prune]`. A parallel run's
times are inflated by its neighbours, so don't feed it those.

The toy maps in `tests/replays/control_toys.py` keep their visibility bitsets in
`~/.cache/valo-tests/control-toys/<digest>`, shared by every checkout and worker. The digest covers the code
that builds them (`geometry.py`, `heights.py`, `height_build.py`, `format.py`), so a change there rebuilds
them on the next run. That build is most of a slow height test's time: `test_control_floors.py` ran 587 s
cold and 8 s warm, serially.
`CONTROL_TOY_CACHE_DIR=<dir>` moves the cache and `CONTROL_TOY_CACHE_DIR=off` gives each run a fresh one.

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
| T01: execution and prerequisites | `test_test_lanes.py`, `_postgres.py`, `conftest.py`, and `support/lanes.py`: missing requirements fail; real slow failures stay visible; database cases classify through fixtures; parallel shared-DB runs refuse. The worker trigger check enumerates actual Docker COPY sources and workflow bind mounts, including `app/__init__.py` and `scripts/control_reference.py`. Four required CI lanes. |
| T02: goldens and active scoring | `test_golden_contract.py`: absent, unreadable, malformed, or changed goldens fail without creating/updating a baseline. Historical formula tests explicitly select legacy behavior; runtime-version tests follow the selected manifest. `test_tracker_ingest_contract.py` connects verified runtime scoring to every persisted score field and refusal before writes. |
| T03: deployment migrations | `test_migration_lifecycle.py`: empty database through head, fresh-process persistence, repeated upgrade, 0027/0028 cache preservation, lock timeout/rollback/retry, and real statement-timeout cancellation with recovery. |
| T04: HTTP identity/navigation | `test_auth_navigation.py`: real forms, signed sessions, logout, deleted/tampered identities, search ambiguity, tags, literal wildcard characters, Unicode, encoded redirects, local friend redirects, Riot verification. HTTPS cookie attributes are checked on a standalone session-middleware app. |
| T05: statistics | `test_force_buy_stats.py`, `test_halftime_conversion_stats.py`, `test_score_reached_stats.py`, `test_round_streak_stats.py`, `test_map_side_stats.py`: thresholds, both perspectives, exact sample denominators, empty samples, side swaps, overtime, and classification boundaries. |
| T06: committed ingestion/recovery | `test_tracker_ingest_contract.py`: synthetic API-shaped payload through loader, active scorer, cache invalidation, and PostgreSQL persistence; duplicate ingest, malformed rollback, unscored-match recovery, manifest/gate refusals, and surrender padding. |
| T07: command orchestration | `test_ingest_commands.py`: both actual CLI main functions, temporary ledgers, one bad roster member, dirty-ID aggregation, no-change/no-prewarm paths, CDP failures, and cleanup on exceptions. Browser/network boundaries are simulated. |
| T08: observable cache behavior | `test_profile_cache_journey.py`: PostgreSQL cold/warm profile requests, recent 30 versus career 31, filtered storage, stale/corrupt cache recovery, nonfatal cache write errors, committed/rolled-back social invalidation, and unaffected-owner retention. Replaces the old list-slice tautology. |
| T09: demo protection | `test_demo_http_contract.py`: disabled route families and fresh-process settings/branding. Resource-dependent replay reads first succeed against the same real linked replay, then return 404 in demo mode with all stored rows unchanged. An authenticated JSON deletion is refused without a tombstone, then succeeds against that retained replay outside demo mode. Migration lifecycle tests load the committed six-match sample seed twice and request profile/match/statistics pages. |
| T10: browser seams | `browser/test_user_journeys.py`: desktop/narrow profile filtering and career HTMX insertion; keyboard focus and duplicate-ID check. Replay checks require painted minimap pixels, a team-colored player ring at independently projected 5/35-second positions, disappearance of the previous ring after scrubbing, real time/clock advancement and pause, and the required control checkbox with a 202 response and exact unavailable-state text. Test-only pinned scripts remove CDN dependence. |
| T11: required inputs/isolation | Required sanitized fixtures fail when absent. `test_architecture_guard.py` and strengthened `replays/test_replay_isolation.py` inspect nested, relative, and literal dynamic imports. `test_replay_score_isolation.py` replaces a real replay and checks persisted Impact rows remain identical. |
| T12: lifespan | `test_app_lifespan.py`: normal shutdown, application-body failure, each partial-start failure, and demo mode with service flags enabled. Started services receive stop signals. |
| T13: coaching | `test_coach_journey.py`: deterministic snapshot through actual runner/checker/store, escaped report HTTP rendering, feedback form persistence, current-version fallback, and missing-point refusal. The model boundary is offline. |
| T14: local import adapter | `test_demo_adapter_contract.py`: date/key parsing, known date override, real schema mapping, replacement/orphan removal, and rollback preserving the original match. The GUI parser is supplied as deterministic parsed input. |

`support/` holds deliberately small shared builders, raw ASGI transport, scratch-site
binding, PostgreSQL lifecycle, synthetic tracker/replay histories, and import inspection.
The worker image can load root conftest without importing SQLAlchemy or psycopg2.

### Remaining audit scenarios

Every T01–T14 area has additions; this does not mean every original scenario is
complete. T03, T05, T11, and T12 exercise their principal boundary, with representative
rather than exhaustive schema/roster/operation checks. The following remain partial:

| Task | Follow-up coverage still needed |
|---|---|
| T01 | Execute the actual Linux worker image and hosted CI; classification follows recognized test/fixture calls, not an exhaustive call graph. Database isolation is required-mode behavior. |
| T02 | Frozen production-environment equivalence and additional integrated tamper/version refusals. Persisted-field comparison checks scorer wiring, not an independent mathematical oracle. |
| T04 | Real-app HTTPS configuration, PostgreSQL search collation, and more malformed redirect inputs. |
| T06 | Halftime/overtime payloads, exhaustive player assist/spend mapping, pre-existing-cache sentinels during refusal, and unrelated committed-match sentinels during rollback. |
| T07 | CLI argument parsing in subprocesses, exhaustive ordering, malformed rosters, and replay-link failure behavior. |
| T08 | Compare actual displayed/chart values across cold/warm cache requests, tied/NULL dates, combined-account deduplication, and membership changes through HTTP. |
| T09 | Detailed seed statistics and disabled-feature mutations beyond the exercised route/state contracts. |
| T10 | Combined accounts, statistics/session expansion, coach/moment navigation, and substantive available control/gaps rendering. Keyboard coverage remains a focus smoke check. |
| T13 | Existing authenticated cross-match/report feedback refusal, checked/unchecked status, opponent-name policy, and a specific missing-source disclosure assertion through the complete journey. |
| T14 | Root HTML/icon parsing, GUI automation, and PostgreSQL adapter-specific integration. The GUI/parser boundary remains simulated. |

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

## Initial implementation validation (e5869084)

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

## Review corrections and validation

The review reproduced the original 195 new cases and the 123-case required PostgreSQL
lane. It found three P2 gaps in what passing tests proved. The follow-up corrects them:

1. Replay refusal now uses a complete, accessible linked replay, with successful
   non-demo reads before demo refusal. Valid authenticated JSON deletion checks that
   refusal retains every stored row and creates no tombstone before the same request
   successfully deletes the retained replay outside demo mode.
2. Replay rendering now checks real minimap pixels and team-colored player geometry
   at independently calculated locations before/after scrubbing, clears the old marker,
   requires the control checkbox and its exact not-ready response, and observes time
   advancing during playback and stopping after pause.
3. Worker triggers include both previously omitted inputs. The check walks actual
   literal Docker COPY sources and workflow host mounts; the entire mounted tests
   directory is covered instead of a hand-picked subset.

Follow-up run, from `webapp/` with the committed added-test file selection:

```sh
python -m pytest <added-test-files> -n 0 -m 'not postgres and not corpus' --require-browser --require-node --require-fixtures -q
```

**183 passed, 18 deselected, no skips, in 125.66 seconds.** The deselected PostgreSQL
cases were unchanged and were not rerun in this follow-up. The added-file inventory
now collects 201 cases, including six additional resource-dependent demo cases.

Seven temporary mutation probes failed at the intended assertions: demo mode forced
off; all canvas drawing disabled; player arcs disabled while the map still draws;
control checkbox removed; playback frames frozen; each missing CI path removed in
turn. These deliberate failures are validation of the assertions, not ordinary-suite
failures. The probes used a temporary pytest plugin outside the checkout and did not
edit application source or become an ordinary CI dependency. Docker/hosted Linux
execution remains pending; no application or scoring source changed in this follow-up.

### First hosted CI run and integration fixes

The first PR run passed Chromium, the slow lane, and the actual Linux worker image.
The offline lane passed 5,931 cases, skipped one private-input probe, and failed two:
the copied-input guard correctly found `app/critutil` added on main, and an existing
recovery fixture could post a fake Workflow callback before its HTTP listener existed.
PostgreSQL never reached pytest: single quotes around its multi-word Docker health
command were not preserved by the Actions argument parser (`unknown shorthand flag: U`).

The branch now includes current main, adds the new worker path, uses double quotes
for the health command, and gates fake callbacks on a ready listener. The recovery
case also forces dispatch before listener setup and checks its saved deadline as
well as its token. Main added migration 0029; the deployment test now compares the
database revision with Alembic's actual source heads instead of a stale `0028` pin.
These are workflow/test fixes; no production worker behavior changed.
Local integration verification: 35 harness/workflow cases passed (one slow case
deselected), both recovery schedules passed with two xdist workers, 20 PostgreSQL
cases passed (one non-PostgreSQL case deselected), and all three browser journeys
passed against the merged main. Hosted CI is the validation for Docker argument parsing.

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
- Docker is unavailable locally. The first hosted run built and passed the actual
  worker image; local conftest smoke checks do not substitute for that image run.
- Line/branch coverage percentages have not been measured. Case counts are execution
  evidence, not percentages; add coverage tooling separately if numerical tracking is wanted.
