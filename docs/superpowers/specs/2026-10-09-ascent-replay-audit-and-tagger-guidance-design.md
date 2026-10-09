# Ascent replay evidence and guided map authoring

Date: 2026-10-09. Owner request: design the assisted workflow, build its supporting tools autonomously,
and return the remaining owner observations/decisions after an overnight window of at most eight hours.
The owner explicitly prohibits merging or changing `main`; deployment is outside this preparatory work. Work belongs on
`codex/ascent-audit-and-tagger-guidance`; the saved-draft repair in open PR #127 is its dependency.

## Outcome

An agent can help the owner finish a map's definitions and investigate real replay signals with small,
specific requests for evidence. The editor explains exactly which feature/state/field needs attention.
An offline audit produces candidate evidence, a review packet, and an explicit account of what is still
unknown. Neither a successful export nor an attractive sandbox is represented as verified replay support.

The first worked example is the owner's Ascent export: Market Door, Garden Door, Heaven Glass, and the
two explicitly linked switches. The export is structurally valid and changes no other map or permanent
Ascent geometry. Opening/closing door footprints, sight bounds, and the glass movement footprint remain
incomplete. Three-second motion and reversal are owner-entered facts, awaiting replay verification.

## Existing facilities and boundaries

Reuse `control_tagger.py`, its embedded JavaScript panel/core, the schema and reducer, the exact-key
artifact archive, compiler placement/bounds reports, and the pinned parser contract. Production consumer
registries are empty and the control/gap engines do not apply per-tick map feature state. This work does
not silently register a consumer or rewrite historical results.

The tagger is currently a self-contained local authoring page. The deployed app has no map editor save
endpoint. Source annotations remain committed `tags.json`; archived compilation inputs are evidence,
not a second editable catalogue. A future site editor requires a separate publication/storage design.
Merely hosting the HTML would not make browser drafts durable on the server.

### Future site editor

Use the same editor/schema/compiler on the friends site's authenticated admin surface. Reuse the existing
admin access checks rather than exposing an unrestricted write endpoint; current replay admin access is
disabled in demo mode. Keep the public demo's data separation. Do not place an admin token in a shared
HTML artifact or browser storage.

Treat drafts, publication and immutable compilation artifacts as separate lifecycles. A server draft needs
per-map revisions, an expected-revision check on save, an owner/audit trail and export/restore. Publication
must validate the entire map and exact current image/height identities, report pending features, and require
an explicit publish action. Keep per-map artifact references shared across rounds; publishing a newer map
must not overwrite historical inputs or silently activate a consumer.

The owner should choose between reviewed `tags.json` PRs as the first publication mechanism, and a server
catalogue with revisioned drafts/publications. The second requires a separately reviewed storage/schema and
web/worker synchronization design. Neither is implemented here: the current branch supplies editing and
audit tools, without claiming server persistence. A later agent should present a concrete proposed save/
publish design after the first evidence session, not ask the owner to design database tables up front.

Existing local parser exports may be read without touching either site's database. Parser source may be
inspected read-only. Do not alter the external parser checkout, its pin, or production parser behavior
until evidence identifies a required change. Raw replays/exports, player information and private audit
packets must stay outside this public repository. Committed examples/tests are explicitly synthetic.

## Assisted workflow

1. **Prepare:** preserve the latest owner export/draft; identify map, parser recipe and available inputs.
   Read branch state before editing. Record each input's identity and whether it is synthetic, verified
   against a source replay, or only an export with an unverified source.
2. **Explain definitions:** show named, actionable issues. Finish what can be derived from the existing
   model; request only facts that cannot be recovered from geometry or replay evidence.
3. **Inventory replay candidates:** stream full raw event exports. Look for relevant actor metadata,
   properties and RPCs, plus parser metadata describing filtered or undecoded groups. Candidate discovery
   is a heuristic; no keyword, actor closure, sound or player proximity proves a state change.
4. **Prepare a human review packet:** present a short list of candidate actors/times and an observation
   worksheet. Keep review windows bounded. Ask the owner to watch specific scenes, not annotate an entire
   match. Preserve disagreement, uncertainty and unknown observations.
5. **Establish decoding:** after identities and signals are verified, map evidence to stable feature IDs
   and reducer events. Account for actor lifetimes, GUID/channel reuse, equal-time ordering, round reset,
   missing/late initial state, partial updates and parser/build coverage.
6. **Prove correspondence:** compare decoded traces to owner-confirmed observations on an independent
   match. Pin evidence references and extract minimal redacted fixtures. Synthetic tests prove logic, not
   the existence of a signal in real replays.
7. **Design activation:** only after the audit succeeds, specify consumer integration, unknown-state
   policy, compatibility/versioning and rollout. Publish reviewed annotations separately from activation.
   All integration into `main` remains the owner's action.

The agent should batch owner input around a concrete artifact and continue independent work between
input points. Silence is never a confirmed observation or permission to invent a state/height.

## Authoring diagnostics

### Structured presentation

Use the existing schema report and preview placement reasons; do not create another geometry compiler.
Convert each reason to a presentation record with stable object ID, display name, state (when present),
field, severity, code, original source path, explanatory text and focus target. Deduplicate only the same
reason at the same field; never collapse errors in different motion states into one unnamed warning.

Examples:

| Source | Owner-facing issue | Action |
| --- | --- | --- |
| `features.feature-9.states.opening.footprint`, invalid geometry, missing shape | Market Door / opening: draw the movement footprint | Select the door and opening state; focus its drawing controls |
| closed footprint bounds absent | Market Door / closed: vision-blocking footprint needs height bounds | Select the bounds editor |
| glass has movement flag but only a sight edge | Heaven Glass / intact: draw the movement barrier footprint | Select intact; explain that sight edges do not block walking |
| required cell has several measured floors | Named feature: placement is ambiguous in these highlighted cells | Keep it pending; never prompt for manual floor selection |

Pre-placement shape/bounds failures do not show misleading `0 cells` counts. Real cell-related failures
retain meaningful counts and affected-cell overlays. Source paths remain available as technical detail.
The issue must be keyboard-accessible and usable as a link/button, not only a colored canvas annotation.

### Readiness and provenance

Show distinct states: draft saved in this browser (or saving failed), structurally exportable, geometry
described, preview placement ready/pending, measured-height context or flat preview, replay evidence
unverified, and runtime support disabled. Never call a disabled or hypothetical consumer "live".
An authoritative compiler report is distinguished from fast browser preview and is valid only for its
recorded tag/height identities. Stale reports must say so rather than continue showing a green verdict.

### Explicit editing conveniences

Offer copying a drawn footprint and its associated sight bounds from another state as one undoable edit.
The owner chooses the source; nothing is copied automatically into motion states. Label this as a geometry
choice, not a claim that the real moving panel occupies that shape throughout its animation. Preserve
feature IDs, unrelated state flags/transitions, unknown fields and high-water marks.

Explain movement footprints versus vision edges. A state marked transparent may still contain unused
sight geometry; explain the contradiction and allow explicit removal without guessing the intention.
Object height cannot be inferred from a floor elevation. Unknown bounds remain unresolved. Existing
legacy-line repair and draft checksum validation remain in place.

## Offline audit contract

### Inputs

- Full parser export directory with `manifest.json` and `events.ndjson`; movement is optional for
  candidate inventory and explicitly reported if absent. Condensed round blobs alone are insufficient.
- Optional source `.vrf` for checksum verification; optional map override is an owner assertion, not
  independently verified map evidence.
- The owner's exported tags catalogue for stable feature IDs and names.
- Optional observation worksheet: verified/unverified/uncertain observations on an explicitly stated
  clock. Absolute export milliseconds are preferred initially. Round timer values require a proven
  phase-to-export conversion, never subtraction from an assumed round length.

### Streaming inventory

Record the parser version/build/profile, contract compatibility, source verification, export hash,
scan completeness, row/byte limits and selection rules. Read events without the condenser's keep filter,
because that filter intentionally omits unrelated export groups/RPCs. Keep memory bounded and sample
retention explicit. Truncation, errors and filtered/undecoded groups prevent absence claims.

Discover candidate actors from metadata/path/function/property names, not arbitrary player payload text.
Retain source row number and export clock, actor/object/channel IDs, actor lifetime and relevant metadata.
Summarize decoded property names/value changes and RPCs. Cap samples per candidate and flag saturation.
Do not equate actor closure with destruction. Unbound rows and reused/ambiguous identities are retained
as such. Keyword matches are candidate evidence only.

Report parser descriptor/filter gaps separately: "not seen in this export" differs from "not decoded by
this parser" and from "absent in the replay". An export manifest alone cannot establish the last claim.
No audit tool modifies the source export, canonical annotations, production DB, worker cache or parser pin.

### Owner review artifacts

Write private JSON and a self-contained HTML review page outside Git repositories. Include candidate
summaries and bounded source samples, an observation worksheet with unresolved defaults, and explicit
next scenes to inspect. Each observation names stable feature ID, action/state, export time (or pending
conversion), uncertainty, evidence reference and review status. Never pre-check an observation as verified.

The review reader checks schema, identities, feature/state references, finite times and time ranges before
accepting an observation. A verified human observation proves what was watched, not which candidate
property encodes it. Comparison can identify nearby candidate rows for investigation, but closeness in
time is not event correspondence. No runtime decoder is generated from nearest matches.

Compiler diagnostics rebuild permanent masks from the exact supplied tags in memory and optionally attach
one explicit height file. They use the existing compiler diagnostic path. A local height file's identity
does not prove that it is deployed. The editor accepts an attached report only when map entry, image/height
context and compiler/schema versions match; any later annotation edit marks the report stale.

## Tests and evidence

- Page harness: named issues, distinct states, keyboard/button focus, malformed input safety, dedup,
  explicit copy/undo/export/reload, browser save failures and legacy draft restoration.
- Existing Python/browser placement parity stays green. Presentation changes cannot relax compiler gates.
- Audit: streaming limits, complete/incomplete scan labeling, noisy keyword false positives, partial
  updates, GUID reuse, source mismatch, unsupported parser metadata, synthetic provenance, HTML escaping,
  malicious/malformed observations, deterministic reports and refusal to publish private raw packets.
- Real local audit results remain private. Public report contains only anonymized counts, methodology,
  reproducible command forms, limitations and the concrete remaining owner tasks.

## Decisions reserved for the owner

The agent can build diagnostics, worksheets, inventory and evidence handling now. The owner must confirm
visual observations, actual timings/motion policy and object heights when data cannot supply them; approve
any proposed conservative approximation; choose the eventual site editor's publication workflow; and
approve activation/merge/deployment. Record whether an item is a factual observation, a product choice,
or operational permission. Do not ask all of these before showing a concrete review packet.

## Completion for the overnight work

Deliver design, implementation plan and agent walkthrough; working tested diagnostic/editing and audit
tools; a regenerated local Ascent editor/review packet; actual investigation of available local evidence;
and a morning handoff with exact branch/commit/test status, remaining gaps and a short prioritized owner
input list. Completing this preparatory release does not assert that Ascent is live in replay calculations.
