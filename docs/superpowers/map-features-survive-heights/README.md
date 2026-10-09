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
Real builder and verifier were exercised with missing and malformed tags. Provisional/authoritative hashes
were tested over HTTP upload/start/resume across a mid-build tag edit and web restart.

Full release-suite results and final review are recorded below when completed. Existing no-feature reference
fixtures are compared in place; no fixtures, source tags, base assets, height rules, control revision, parser
revision or scoring behavior are regenerated or changed for this release.

## Measurements

Synthetic four-cell permanent geometry, Python 3.13.5 / zlib 1.3.1. Fresh feature-child compile includes exact
NPZ loading, permanent inputs and compilation; it excludes height rebuilding and visibility generation.
These observations are not production sizing estimates or timing assertions.

| Catalogue | Cold process s | Compile + height s | Verified hit s | Peak child MiB | Wire bytes | Expanded bytes | Compressed assets bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Small (one intended bundle) | 1.593 | 1.471 | 0.838 | 78.05 | 471223 | 874617 | 1562 |
| Larger (six bundles, four states each) | 6.364 | 6.245 | 4.789 | 102.50 | 494797 | 6647407 | 11644 |
| All pending | 1.218 | 1.101 | 0.489 | 72.52 | 470594 | 349834 | 670 |

Permanent inputs occupy 350859–358373 bytes; NPZ files 1805–1809 bytes. These cases fit the conservative
defaults, which are retained. Archive payload growth is approximately inputs + compressed compiled assets
+ retained NPZ + manifest per exact map/version key, shared across rounds. Cache hits still pay integrity
rehashing; the larger synthetic hit is seconds, not the design's estimated milliseconds.

Actual worker fresh-child/warm-disk and persistent local-pool measurements are added after lifecycle
acceptance. Docker is unavailable on this host, so the worker image build/smoke test and Linux Python/zlib
runtime comparison remain unexecuted. Local web and worker children use the same available interpreter;
expanded canonical identities, not compressed gzip equality, define correspondence.
