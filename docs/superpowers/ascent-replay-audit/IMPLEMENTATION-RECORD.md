# Ascent audit preparation: implementation record

Date: 2026-10-09. Work branch: `codex/ascent-audit-and-tagger-guidance`.
Base: `dcb6c44`, the open PR #127 saved-draft repair. That dependency remains separate.
The owner prohibits merges or changes to `main`. No merge, deployment, database operation, parser
checkout/pin change, scoring change, canonical tag publication or consumer activation was performed.

## Delivered

- [Design](../specs/2026-10-09-ascent-replay-audit-and-tagger-guidance-design.md),
  [implementation plan](../plans/2026-10-09-ascent-replay-audit-and-tagger-guidance.md), and
  [agent walkthrough with commands and continuation prompt](AGENT-RUNBOOK.md).
- Tagger placement issues name the feature, state and field; buttons select/focus the relevant control.
  Duplicate dependency/bundle reports combine affected cells. Cell problems highlight their actual cells;
  missing shapes/bounds do not display meaningless zero-cell counts. The editor explains unused sight
  edges on transparent states and distinguishes structural exportability from pending placement.
- Explicit footprint-only or footprint-and-vision-bounds copying from another state, one undo step,
  retaining unrelated flags, motion behavior, IDs, unknown fields and browser draft persistence.
- `scripts/diagnose_map_features.py`: exact-source masks rebuilt in memory, existing compiler diagnostics,
  explicit flat or exact local height context, and comparison against a previous height report. No cache
  or asset write. An exact local asset is explicitly not proof of the deployed active asset.
- Tagger `--diagnostics`: attachment requires matching annotation, image, height and schema/compiler
  identities. Subsequent annotation edits mark the report stale; undo can restore its validity.
- `app/replays/feature_audit.py` / `scripts/audit_map_features.py`: raw streaming inventory without the
  condenser filter, parser/source/export/tool identities, bounded retention, explicit limits/ambiguities,
  descriptor-name discovery, scalar property changes and actor lifetimes. Actor closure is lifecycle
  evidence, never an inferred destroy event. Unsupported parser metadata stays labeled incompatible.
- Private JSON/HTML review packets and downloadable/restorable observation worksheets. Scenes have stable
  feature IDs, distinct scene IDs, explicit export clock, uncertainty and unverified defaults. CLI review
  checks audit integrity, clock/ranges/references and returns nearby retained samples/property changes
  without converting proximity into bindings. Output refuses Git repositories, source exports and
  existing directories. Raw evidence never goes into this public checkout.

## Evidence actually collected

The owner's latest complete catalogue contains three Ascent features and two linked switches. The page
harness checks unchanged annotations, structural exportability and exact preservation of all other maps.
The compiler report has three tagged / zero placeable / three pending in an explicitly flat context, with
runtime disabled. Remaining categories are missing geometry, unresolved bounds and off-ground placement
on authored geometry. Flat results do not verify placement against a deployed measured height asset.

Private local raw exports exist for other maps; condensed Ascent round files also exist. No complete raw
Ascent export or source was found in the checked temp export/archive/game-replay locations. Condensed
rounds cannot recover omitted actor/RPC data. This is a local evidence availability finding, not a claim
that another machine or the worker archive lacks the recording.

A genuine local non-Ascent export was scanned completely: 362,550 event rows, 62 exploratory candidates,
no scanner limit/error flags, and compatible pinned schema/build/commit metadata. Repeated full scans
produce the same events hash. Its source recording was unavailable, so source checksum verification is
false. Candidate count includes unrelated audio, abilities and weapons; it is not a gimmick count.

The pinned parser source explicitly omits undecoded export groups and decoded groups without payloads.
Manifest descriptors expose some map-door state fields, while filtered summaries show missing decoded
payload coverage. The scanned door candidate has lifecycle/RPC evidence without decoded door-state
property changes. No Ascent actor binding, state-value meaning, door timing, glass-break rule or runtime
decoder was verified or fabricated. Private reports and identifiers remain outside the repo.

## Verification

```powershell
.\.venv313\Scripts\python.exe -m pytest tests/replays/test_feature_audit.py tests/replays/test_feature_audit_tools.py tests/replays/test_map_feature_tagger.py tests/replays/test_control_tagger.py tests/replays/test_feature_diagnostics.py -q -p no:cacheprovider
```

119 passed in 68.33 seconds. A subsequent audit-tool/source-fingerprint check passed 43 tests in 3.11
seconds. The final private raw inventory, compiler command, report attachment and CLI worksheet review
also run successfully. Node syntax checks and `git diff --check` pass.

Regressions include state/field focus, dotted state names, grouped cell unions, measured multi-floor
pending/highlighting, overlay clearing after edits, copy/undo/redo/reload preservation, stale compiler
attachment/version/image/height refusal, exact-height rebuild comparisons, streaming/sample/actor limits,
GUID reuse, channel ambiguity, malformed manifests/observations, source checksums, deterministic audits,
synthetic provenance, HTML escaping and transactional worksheet restore.

The DOM harness was corrected to clear children on `innerHTML` assignment and implement selected-option
behavior; otherwise it could hide rerender bugs. This is a Node page-wiring test, not a visual browser
test. Browser automation rejects local file URLs, so live browser storage/layout was not inspected or
bypassed. The owner should first download the current draft, then check the separate guided page.

No PostgreSQL/Linux/production restart rehearsal was attempted: this change has no schema or worker/runtime
activation. Those earlier activation boundaries remain future gates. The unrelated untracked owner work
was left untouched. Local `main` remains at its pre-task ref; its last reflog change predates this work.

## Owner input needed next

1. Locate a complete raw Ascent export or the recording/archive location. Check that source before choosing
   scene windows or proposing parser descriptors; do not ask for a whole-match manual annotation.
2. Confirm door motion geometry: state-specific panel coverage or an explicitly accepted conservative
   footprint. The tool makes copying easier without selecting that policy.
3. Supply/verify the vision-blocking height bounds. Measured floor elevation does not provide object height;
   `all_height` must remain an explicit verified choice.
4. Confirm the glass movement shape and transparent-state intent, then explicitly remove unused sight
   geometry if unintended. Its current sight edge alone does not define a movement barrier.

Once the raw export is available, request one clear close, one open and one glass break, with clock
evidence/uncertainty. Verify destruction, reset and mid-motion behavior only as needed for support claims.
Hosted server drafts/publication and runtime activation have their own proposed designs and owner gates.
The private morning packet links the exact editor/report files and supplies the first-session steps.
