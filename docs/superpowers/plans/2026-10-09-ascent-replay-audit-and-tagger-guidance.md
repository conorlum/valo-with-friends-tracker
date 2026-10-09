# Implementation plan: Ascent audit and guided authoring

Date: 2026-10-09. Design: [spec](../specs/2026-10-09-ascent-replay-audit-and-tagger-guidance-design.md).
Work branch: `codex/ascent-audit-and-tagger-guidance`, based on the open PR #127 draft repair.
No merge, `main` change, deployment, production DB operation or feature activation is authorized.

## Task 1 — Preserve inputs and establish evidence

1. Keep the owner's original attachments immutable; retain the complete Ascent export outside the repo.
2. Record the branch/base and the eight-hour deadline. Preserve unrelated untracked work.
3. Inventory existing local exports, parser source/build and height exports without reading credentials
   or connecting to either site's DB. Distinguish synthetic test exports from real evidence.
4. Document available/absent inputs. Do not ask for new replay files if existing samples are sufficient.

Exit: design and this plan exist; the agent runbook names independent work and human gates explicitly.

## Task 2 — Actionable editor issues and readiness

1. Add a pure presentation model around schema/placement reports. Preserve original codes/paths.
2. Generate human labels from stable feature IDs, state names and fields. Identify missing footprint,
   unresolved bounds, malformed geometry, off-ground, zero/multiple floors and unused sight edges.
3. Render grouped keyboard-accessible issue buttons. Clicking selects the correct object/state and
   focuses a field. Deduplicate only same-field reasons; hide meaningless pre-placement cell counts.
4. Show measured-height/flat context and separate saved/export/geometry/evidence/runtime states.
5. Show affected cells when meaningful; never imply failed source loading means zero problems.

Tests: actual page harness interactions; keep existing structural/preview parity coverage. Ascent's
current export must point to both doors' motion footprints and sight bounds and the glass footprint.

## Task 3 — Explicit geometry reuse and clearer tools

1. Add copy-from-state controls for footprint and associated bounds. One operation, one undo step.
2. Retain flags, transition policy, unrelated fields and IDs. No automatic motion policy/height inference.
3. Clarify footprint versus sight-edge labels/help. Transparent states with sight edges get useful advice.
4. Verify export/reimport and draft persistence, including known legacy-line repair and save failure.

Exit: finishing the current model no longer requires repeatedly redrawing equivalent geometry or
interpreting engine-shaped diagnostics. Unknown facts remain unknown.

## Task 4 — Streaming raw replay inventory

1. Implement stdlib offline analysis that scans raw events without the condenser keep filter.
2. Validate/report parser metadata, synthetic marker and optional source checksum. Record export identity.
3. Discover candidate metadata/fields/RPCs; trace actor lifetimes and reuse. Bound memory/sample retention.
4. Account for malformed/truncated exports, parser filtering/raw fields and unsupported contract.
5. Output private JSON/HTML and a schema-valid empty observation worksheet outside any Git repository.
6. Keep reports deterministic, HTML-safe and explicitly exploratory. No production ingestion changes.

Tests: realistic synthetic streams with partial updates, reused IDs and non-gimmick keyword hits;
resource limits, private-output guard, metadata mismatch and absence-claim prevention.

## Task 5 — Observation review and guided owner packet

1. Define observation format with stable IDs, expected action/state, explicit clock, uncertainty and
   verified/unverified status. Validate unknown states, invalid times, duplicate scene IDs and mismatched input.
2. Provide a local review page/worksheet that saves observations by download; unresolved is the default.
3. Produce a short scene list and coverage report. Nearby rows are candidates, never automatic proof.
4. Reuse the state's reducer only for verified, explicitly mapped evidence; candidate inventory itself
   must not manufacture switch/destroy events. Decoder implementation remains behind the human gate.

Exit: another agent can ask the owner to review a few scenes and incorporate answers reproducibly.

## Task 6 — Real evidence investigation

1. Run the inventory on available local exports with private outputs; scan completeness is explicit.
2. Examine matching actor/group metadata in the pinned parser source. Record what is filtered or undecoded.
3. Identify which samples are Ascent by genuine map evidence or clearly labeled owner assertion.
4. Prepare an anonymized public evidence summary and private review packet. Do not commit replay IDs,
   player data, raw rows, exported files or private packet contents.
5. When blocked on watching scenes, keep owner observation rows unresolved and continue independent tests.

Exit: honest candidate findings and specific next observations, not an invented real-state decoder.

## Task 7 — Verify and hand off

1. Run owning suites; broaden to replay regressions only when shared-code changes justify it.
2. Inspect the final diff for schema/runtime/scoring/credential changes and private data. Preserve no-tag
   behavior, empty production consumer registries and existing height/archival rules.
3. Regenerate a local editor from the latest Ascent export and provide tested audit/review artifacts.
4. Commit only task-owned code/docs/explicitly synthetic fixtures on the work branch. A reviewable draft
   PR may be opened; do not merge it or change `main`. Clearly name dependency on PR #127 if still open.
5. Write the morning handoff: completed files, commands/results, real-versus-synthetic evidence,
   limitations, staged changes, and a short owner decision/observation list.

## Later release — not part of silent overnight activation

After real evidence is reviewed: design actor bindings and per-round event decoding, add redacted real
regressions, integrate all affected movement/vision/gap consumers, specify unknown-state behavior and
consumer versioning, finish PostgreSQL/Linux/restart/rebuild rehearsals, and let the owner approve
publication, merge and deployment. The site admin editor's durable storage/publish design is separate.

## Implementation status

Tasks 1–5 are implemented for the preparatory workflow: grouped issue presentation, state/field focus and
cell highlights; explicit shape-only or shape-and-bounds copying; exact-source compiler diagnostics with
optional explicit height comparison and stale-report attachment checks; bounded raw inventory and private
HTML/JSON output; and a downloadable/restorable observation worksheet with CLI validation/comparison.

Task 6 found genuine local exports and condensed Ascent rounds. A complete non-Ascent export was used for
the real scanner smoke test; no complete raw Ascent export/source was found in the checked local locations.
This is an evidence availability limit, not a claim about the game or remote archive. Owner Ascent
annotations remain preserved, and the compiler checks all three features without activating effects.

See [implementation record](../ascent-replay-audit/IMPLEMENTATION-RECORD.md) and the private owner packet
for verification, remaining observations, and the exact next session. The final review/test/commit status
is recorded there rather than marking parser decoding or hosted persistence complete.
