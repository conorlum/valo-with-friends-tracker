# Map features surviving height rebuilds

Implementation branch: `Map_features_survive_automatic_height_rebuilds`.
Integrated height baseline: `cf75c29ee03578cc31af4241af8139946ad9ac30` (PR #124).
Design: [reviewed specification](../specs/2026-10-08-map-features-survive-height-rebuilds-design.md).
Plan: [implementation plan](../plans/2026-10-08-map-features-survive-height-rebuilds.md).

Permanent annotations are retained per map. Immutable `control_feature_artifacts` rows share the exact
map/height/normalized-tags/compiler compilation across rounds; tagged round summaries record its full digest
and actual input provenance. An intended but unplaceable bundle still has an artifact describing its pending
outcome. No runtime consumer is enabled by this release; the production registry remains empty.

Every required cell must have permanent walkability and exactly one real finite measured floor. Flat mode is
explicit. Old authored floor selectors survive source round trips but do not select floors. Required sibling,
trigger, route, state and phase placement is atomic per bundle. Bounds and behavior remain authored facts.

Height building and integrity verification load only permanent masks and map scale. Optional admitted tag
snapshots remain separate from height evidence identity. Reports distinguish worker provisional diagnostics,
fresh authoritative diagnostics and rejected candidate status. Diagnostic or compilation failure does not
veto a valid height activation. Preparation retries independently; current rows remain visible as stale.

Manual activation/off emits a fresh diagnostic audit result and prepares the selected artifact without
rewriting historical build reports. Stored-round verification returns independent integrity, recompilation
and current freshness verdicts; unsupported historical code is never downloaded or executed.

## Protocol and storage

- Additive migration `0020_control_feature_artifacts`, after `0019`. No provenance backfill or round-history table.
- Feature wire/compiler/manifest/normalization identities: `1 / 2 / 2 / 2`; consumer-version map `{}`.
- Private worker `POST /features` accepts bounded complete archives. Missing exact artifacts return
  `409 needs_features`; the web dispatcher repushes that archived digest and its exact height if needed.
- Optional height diagnostics protocol `1` is advertised under worker height health. Same-key resume retains
  the admitted snapshot. Older workers may still build height evidence; feature-bearing control jobs wait
  for compatible feature health without spending their retry budget.
- Limits: 16 MiB wire, 64 MiB expanded assets; verified process cache and atomic worker disk cache both
  bounded to 16 entries / 64 MiB. Integrity is rehashed on every use, including cache hits.
- Archives are retained indefinitely, including optional exact NPZ bytes/checksum for committed fallback
  heights lacking an activation-history row. They never create height activation history implicitly.
- Current planning uses consistent source snapshots and header-only archive reads. It does not compile on
  page loads. Both writers validate current inputs under map-then-replay locks before replacing rows.
- Unexpected old `index.json.features_sha` pointers require explicit archive conversion for affected feature
  work. They are not selectors and do not freeze height activation. Committed index preflight found none.

## Deployment window

This branch has not been pushed, deployed or applied to a site database. The owner approved schema code and
isolated SQLite tests only. Obtain approval for the migration/deployment window and any disposable PostgreSQL
rehearsal before execution. Both sites deploy from this public repository; review
[Render deployment settings](../../../webapp/RENDER_DEPLOY.md). Keep friends data separate from demo sample
data, preserve `DEMO_MODE=true` on ValoMaths, and never change its Riot-registered URL.

1. Preflight generation pointers/consumer state, preserve archives and obtain the approved database window.
2. Apply the additive table migration. Existing rounds remain valid without a provenance backfill.
3. Deploy a compatible complete worker image first and verify exact feature health identities. Untagged
   old-web traffic continues. Do not change the worker process-per-round lifecycle incidentally.
4. Deploy the complete web replacement, including preparation, provenance, writer guards and E6 retirement.
   Keep `REPLAY_HEIGHTS_AUTO` enabled. Tagged maps have no rebuild exemption.
5. Run approved synthetic/isolated acceptance, then observe natural first-two/next-five-match rebuilds and
   readiness. This release does not activate Summit doors or register production consumers.
6. Confirm unchanged untagged fingerprints/data/summary, exact synthetic tagged provenance, historical
   archive verification, stale-row visibility during replacement and dependent gap refresh.
7. If necessary pause affected derived work using existing controls and restore a compatible complete web
   and worker version. Retain archives and old rows. Do not downgrade/drop the archive table, send new feature
   jobs to a pointer-only worker or present a base fallback as a tagged result.

## Validation evidence

Local approval scope: isolated SQLite, temporary files and localhost test workers; no site database access.
Targeted height/compiler/planning/local/remote writer run: **262 passed, 3 skipped** in 298.55 s.
Tagged first-two/next-five rebuild, multi-floor pending and recovery acceptance: **2 passed** in 46.57 s.
Historical/child/pinned/isolation run: **31 passed** in 123.25 s, including H1/T1 reproduction after H2/T2,
corrupt bytes, unsupported recompilation, missing archives and current-source failure.
Tagger/source/normalization/placement parity: **58 passed** in 33.44 s, including Node page-model tests.
Acceptance exposed obsolete synthetic-scale and gap test doubles. The actual-scale owning tests pass
**7/7**; complete gap command/web/writer suites pass **69 tests, 3 skipped** in 39.00 s after the test interfaces
were aligned with exact loader/context arguments and actual geometry/choke identities. Production guards
and engine output comparisons remain intact.
An inherited disabled-route TCP reset was also reproduced on all four control/feature/height/build POST
routes with nonempty bodies. Bounded body draining preserves the advertised JSON 404; complete worker
control, feature transport and isolation suites pass **46/46** in 45.09 s. The initial full run found those
six obsolete expectations and this transport defect: 2156 passed, 22 skipped, 7 failed in 1395.80 s.
The final full-suite result follows below; the initial run is not a green release claim.
Real builder and verifier were exercised with missing and malformed tags. Provisional/authoritative hashes
were tested over HTTP upload/start/resume across a mid-build tag edit and web restart.

Final complete replay suite: **2167 passed, 22 skipped, 1 warning** in 1390.62 s (23 min 10 s), using
`python -m pytest -p no:cacheprovider -q tests/replays`. The warning is an existing invalid escape in an AST
inspection test; unavailable platform/PostgreSQL checks skip. Named reference/isolation/tagger acceptance
is included in that run and is not needlessly repeated. Final branch review follows after this acceptance
commit. Existing no-feature reference fixtures are compared in place; no fixtures, source tags, base assets,
height rules, control revision, parser revision or scoring behavior are regenerated or changed for this release.

| Release invariant | Passing targeted evidence |
| --- | --- |
| Measured sole-floor placement, all required parts and bundle atomicity | `test_control_features.py`, `test_map_feature_tagger.py` |
| Complete bounded archive, canonical identity and corruption rejection | `test_map_feature_artifacts.py`, `test_map_feature_inputs.py` |
| Shared immutable map archive, insertion races and caller rollback | `test_control_feature_artifacts_db.py` (isolated SQLite) |
| Isolated compilation, exact worker repush/cache and actual permanent context | `test_feature_job.py`, `test_feature_transport.py`, `test_control_remote.py` |
| Pinned provenance, changed-input rejection and stale-row preservation | `test_control_pinned_inputs.py`, `test_control_store.py`, `test_gaps_store.py` |
| Diagnostics independent of height evidence; admitted versus fresh source | `test_feature_diagnostics.py`, `test_control_height_job.py`, `test_heights_remote.py` |
| Normal tagged-map cadence, pending bundles and recovery | `test_heights_remote.py::test_tagged_two_then_five_match_rebuild_pending_and_recovery` |
| Historical integrity, recompilation and freshness independently reported | `test_control_feature_verification.py` |
| Unchanged untagged fingerprints/data/summary and lightweight parent imports | `test_control_features.py`, `test_control_reference.py`, `test_control_isolation.py` |
| Cross-process identities and actual worker/local cache lifecycle | `test_feature_acceptance.py` |

## Measurements

Synthetic four-cell permanent geometry, Python 3.13.5 / zlib 1.3.1. Fresh feature-child compile includes exact
NPZ loading, permanent inputs and compilation; it excludes height rebuilding and visibility generation.
These observations are not production sizing estimates or timing assertions.

| Catalogue | Cold process s | Compile + height s | Verified hit s | Peak child MiB | Wire bytes | Expanded bytes | Compressed assets bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small (one intended bundle) | 1.645 | 1.519 | 0.836 | 75.81 | 471223 | 874617 | 1562 |
| Larger (six bundles, four states each) | 6.373 | 6.253 | 4.794 | 99.83 | 494797 | 6647407 | 11644 |
| All pending | 1.227 | 1.107 | 0.493 | 70.25 | 470594 | 349834 | 670 |

Permanent inputs occupy 350859–358373 bytes; NPZ files 1805–1809 bytes. These cases fit the conservative
defaults, which are retained. Archive payload growth is approximately inputs + compressed compiled assets
+ retained NPZ + manifest per exact map/version key, shared across rounds. Cache hits still pay integrity
rehashing; the larger synthetic hit is seconds, not the design's estimated milliseconds.

Actual `ControlRunner -> control_job -> compute_task` acceptance uses exact archived NPZ bytes and cached
visibility. First worker round: 2.716 s; consecutive warm-disk round (still a fresh child): 2.671 s;
persistent local-pool hit: 1.475 s; peak worker child: 100.71 MiB. Both worker results match exactly. Tagged
control data equals the untagged result; removing additive provenance restores identical decoded summaries.
The four measurement/lifecycle acceptance tests passed in 59.96 s. Registry injection exists only in the
test wrapper, never production constants or requests.

Docker is unavailable on this host, so the worker image build/smoke test and Linux Python/zlib runtime
comparison remain unexecuted. Local web and worker children use Python 3.13.5 / zlib 1.3.1; expanded canonical
identities, part bytes and provenance are compared across fresh processes. Compressed gzip equality is not
the identity test.

## Implementation rulings

1. Use the explicitly requested checked-out feature branch rather than the earlier document worktree.
   The newer user instruction controls the implementation location. Cost if wrong: move the implementation
   checkout; unrelated files remain preserved.
2. Perform the skill's Bash bookkeeping operations in native PowerShell, retaining the same task briefs,
   bases and green-only completion records. Cost if wrong: correct the review-package bookkeeping.
3. Retain outside-feature base edits conservatively in compiler inputs because overlap reconciliation
   examines them. Cost if wrong: an unrelated outside base-edit change may trigger extra compilation;
   unrelated behavior and editorial changes do not.
4. Preserve sight/traversal policy proofs using already placed bounded occluders/arcs after manual floor
   selection becomes obsolete. New sole-floor and complete-bundle tests cover placement integration.
   Cost if wrong: add coverage at the placement-to-policy boundary.
5. Explicitly begin SQLite's outer transaction before its first savepoint so released savepoints remain
   subject to caller rollback. Concurrent insertion and rollback regressions pass; PostgreSQL uses its
   ordinary savepoint path. Cost if wrong: SQLite lock behavior differs; PostgreSQL rehearsal remains needed.
6. Retain optional exact NPZ bytes/checksum in the approved per-map artifact archive for committed fallback
   heights without activation-history rows. This preserves off/fallback semantics without creating history.
   Cost if wrong: NPZ storage repeats for each exact feature key.
7. Include the inherited disabled-POST TCP reset in the transport repair because it affects the new feature
   route and can prevent its advertised JSON response. Drain only bounded admitted bodies before the 404.
   Cost if wrong: disabled requests may occupy a handler for up to two seconds; worker scheduling and the
   process-per-round lifetime remain unchanged.

8. Keep production consumer registration empty and Summit activation unwired, as required by the release
   scope. Users receive preserved annotations and diagnostics; runtime activation remains a separate change.
   Cost if wrong: further activation work is required before those effects can run.
9. Leave PostgreSQL migration and concurrency rehearsal as a deployment gate outside the approved SQLite
   scope. SQLite evidence cannot certify PostgreSQL behavior. Cost if wrong: PostgreSQL-specific faults or
   deployment delays may be found in rehearsal.
10. Leave Docker image execution and Linux runtime comparison unverified because Docker is unavailable on
    this host. Local child-process evidence stands for the tested Windows runtime only. Cost if wrong: an
    image or Linux-specific fault remains possible until the deployment gate is exercised.
11. Retain conservative archive/cache limits and indefinite archive retention; synthetic measurements do
    not justify production sizing claims. Cost if wrong: storage or runtime use requires later adjustment.
12. Accept filesystem replacement after the final source check under the recorded-provenance and next-read
    staleness model. This check cannot lock external file writers. Cost if wrong: a short stale-result window;
    the exact historical inputs remain reproducible.
13. Treat malformed-geometry export as already guarded by structural validation and repair its editable
    preview path. Cost if wrong: an alternate export path would need additional validation coverage.
14. Accept the review's deliberate focus on changed policies and interfaces, supplemented by the complete
    replay suite, without expanding into unchanged engine internals. Cost if wrong: an existing uncovered
    engine defect remains outside this release's evidence.

## Final review

One fresh-context whole-branch review of `cf75c29..d3c2ae1` raised seven Important findings and no Critical
or Minor findings. Their grades stand based on user effect: normal route creation can crash, preview can
claim invalid ground is placeable, child bundles can ignore required parents, historical verification can
accept unavailable consumer semantics, match views can mislabel current rows, malformed JSON containers
can abort planning, and a late source edit can overwrite existing gap rows. All enter one grouped TDD fix
pass. Review topics deliberately set aside are recorded above as rulings 8–14; no minors are deferred.

| Important finding | Fix and regression evidence |
| --- | --- |
| Route creation crashes while endpoints/access sites are unplaced | Shared geometry validation runs before preview rasterization; malformed shapes produce structured pending reasons. Actual Node page creation tests cover rope/zipline and null boarding sites; five malformed-shape cases match Python placement codes. |
| Browser walkability accepts any occupied pixel | Permanent and privately restored masks are reduced after pixel composition using the engine's strictly greater than 50% rule. Ten partial-cell/restoration cases compare preview eligibility with Python bundle status. |
| Required parent placement does not gate children | Each bundle checks its transitive parents for placement, behavior, bounds and routes while retaining only its own candidate edits. Seven cases cover off-ground parents, transitive dependencies, cycles, missing dependencies and invalid behavior/bounds/routes; an unrelated bundle stays usable. |
| Historical verification trusts recorded consumer availability | The verifier checks recorded consumer names and versions against the available runtime after integrity validation. Missing/version-mismatched consumers preserve verified integrity and report unsupported recompilation, including fresh child-process tests. Test support is supplied by selected test runtimes, never inferred from requests or production constants. |
| Match summary reader uses obsolete freshness inputs | One `CurrentGeometryContext` is resolved per replay and shared across its rows. Tagged current rows, editorial edits, pending/source failure and explicit height-off with committed fallback are covered. |
| Malformed JSON containers abort planning | Current snapshot, map index and preliminary bundle containers are explicitly validated. Bad structures report `source_error`; archived reproduction still returns independent integrity/recompilation verdicts. |
| Gap replacement lacks a final source check | Both writers share a final raw-source identity guard before replacing rows. An edit after the existing-gap lookup preserves old gap rows; an editorial-only edit still stores. |

All seven findings were reproduced before their production fixes. The first regression run was 41 failed,
4 passed; extending the Node DOM harness exposed the actual route raster crash and partial restoration
failure (4 failed, 8 passed). The owning suites subsequently passed all 43 new regressions: 309 passed,
5 skipped, with two older interface/test-runtime expectations repaired. Those complete view/transport
suites then passed 33 tests, 1 skipped. The final complete replay gate follows below. No second review is
dispatched; this is the plan's one grouped fix pass.

The first post-review full gate was stopped after identifying one additional test-runtime omission:
the committed fallback-height archive test launches a fresh verifier and also requires explicit synthetic
consumer support. No production rule was relaxed. Its complete SQLite archive suite passed **9/9** in
18.46 s after that fixture correction; the complete replay suite was restarted. Interrupted output is
not a release claim. The durable [implementation record](implementation-record.md) retains task and
review decisions alongside the runbook.

Final post-review complete replay suite: **2210 passed, 22 skipped, 1 existing warning** in **1503.38 s
(25 min 3 s)**, using `python -u -m pytest -p no:cacheprovider -q tests/replays`. The 45-case focused
selection includes 43 new regressions and two existing tests whose names also match `review_`; earlier
ledger entries saying "45 new" refer to that focused selection. All seven Important findings are fixed,
with RED→GREEN evidence and a green complete suite. No minors are deferred. PostgreSQL rehearsal and
Docker/Linux execution remain unrun, as recorded above. The requested branch remains local for the
owner's integration decision; no push, PR, merge or deployment has been performed.
