# Agent runbook: help the owner finish Ascent

Read the [design](../specs/2026-10-09-ascent-replay-audit-and-tagger-guidance-design.md),
[implementation plan](../plans/2026-10-09-ascent-replay-audit-and-tagger-guidance.md), repository AGENTS.md,
and the latest handoff before acting. Preserve ongoing owner edits and existing authorization.
The owner explicitly requires every change to `main` to go through them. Never merge or deploy.

## How to collaborate

The owner supplies game observations and product decisions; the agent owns source inspection, tooling,
data preparation, reproduction, tests and explanations. Do useful independent work first. Ask one small
batch tied to a concrete artifact, then continue independent work while waiting. Do not ask the owner to
guess parser fields, reconstruct IDs or draw heights that the measured asset can determine.

Say what a result proves. Export validity is structural, sandbox success is simulated behavior, placement
depends on current measured heights, and real runtime support needs verified evidence plus an enabled
consumer. Never hide these distinctions under a single green "ready" badge.

## Session A — Complete definitions

For the owner's later five-second closing observation and the optional sliding preview, follow
[SLIDING-DOOR-STARTER.md](SLIDING-DOOR-STARTER.md). Its sampled panel replaces a whole-motion static
approximation in the authoring preview; runtime motion sampling remains a separate integration boundary.

1. Confirm the owner is using the regenerated page/version, and preserve Download draft before changing
   pages. Do not clear browser storage. Known older sight-edge types are repaired after integrity checks.
2. Work through named issues in order: malformed shape, missing blocking footprint, unresolved bounds,
   placement, dependencies. Clicking an issue should select the relevant state/control.
3. Offer copying an existing footprint where the owner confirms that geometry is appropriate. Explain
   that copying geometry does not prove a motion policy. Never silently turn a transparent glass into a
   sight blocker or set "every height" just to clear a warning.
4. Only ask about object height, uncertain motion behavior, and shapes the owner cannot infer from the
   minimap. Supply the feature and state name and the concrete missing fact in each question.
5. Export, validate, and reload with fresh browser storage. Keep the original source and latest export.
   Features may remain pending while observations are unresolved; the annotations must still be saved.

First possible owner batch: confirm the intended moving-door geometry policy, provide/verify object
height bounds, and confirm glass blocks movement while allowing sight. These are not inferred facts.

## Session B — Review replay evidence

1. Start with existing private local/archived Ascent exports; do not touch a live DB or request another
   recording before checking available evidence. Confirm parser/build, source identity and scan coverage.
2. Read the audit report. Keyword hits are candidates. A closed network actor/channel is not a proved
   destroyed object. Identify descriptor/filter gaps before calling missing rows absent replay evidence.
3. Give the owner a small scene list: replay label, round if established, exact clock/time window,
   feature, and question. Provide a worksheet/review page. Keep raw rows/private identifiers outside Git.
4. Ask for visible state and transition timing, allowing "uncertain" and a time interval. If the owner
   reports a countdown clock, record it verbatim and derive its export-clock conversion from phase evidence.
5. Validate the returned worksheet. An observation's verified flag is an explicit human assertion, not
   inferred from elapsed time or the existence of a nearby candidate RPC.
6. Compare observations and candidates; record disagreement and missing coverage. Request only the
   additional scenes needed to distinguish hypotheses. Verify a proposed decoding rule on another match.

First observation batch should cover one door close/open and one glass break. Add destruction, reset and
mid-motion press only as needed for the eventual support claims. Avoid asking for a whole-match annotation.

## Session C — Decide support and release

1. Present the evidence table and classify each behavior as supported, needs parser work, ambiguous, or
   not observed with the tested export/parser. Explain the practical effect of each limitation.
2. Propose unknown-state policy and any geometry approximation separately from observed facts. Obtain
   the owner's choice before activating a behavior that relies on an approximation.
3. Build decoder/runtime integration with the existing reducer and compiler; cover actor lifetimes,
   round resets, equal-time ordering and all affected movement/sight/timing-gap consumers.
4. Prove correspondence on held-out real scenes and exact archive/provenance behavior. Rehearse the
   worker/web versions and database/runtime boundaries on approved disposable environments.
5. Present a concrete PR and release checklist to the owner. They decide merge and deployment.

## Decisions log

For each unresolved item, record: ID, factual observation/product decision/operational approval,
feature/state, evidence already collected, options, recommendation, impact, and whether other work can
continue. Mark owner answers with date/source; silence never changes the item's status.

Do not repeatedly request existing approvals. The current overnight task authorizes branch code, local
artifact generation and tests; it does not authorize production database changes, new security access,
merging or deployment. Site-hosted editing requires a deliberate publication/storage design.

## Tool commands and checks

Run these from `webapp/` using the existing environment. Keep the owner's downloaded `tags.json`, full
exports, compiler reports and review packets in a private directory outside any Git checkout. Substitute
actual private paths; each output directory must be new. Neither command accesses the database.

```powershell
# Flat context until an exact exported active height file is available.
.\.venv313\Scripts\python.exe scripts\diagnose_map_features.py --tags "C:\private\tags.json" --map Ascent --out "C:\private\compiler-1"

# With measured heights, add --height "C:\private\Ascent.height.npz".
# Compare after a rebuild by also adding --previous "C:\private\compiler-1\diagnostics.json".
.\.venv313\Scripts\python.exe scripts\control_tagger.py --tags "C:\private\tags.json" --map Ascent --diagnostics "C:\private\compiler-1\diagnostics.json" --out "C:\private\Ascent.guided.html"

# Full raw export, no condenser filtering. --source is optional checksum verification.
.\.venv313\Scripts\python.exe scripts\audit_map_features.py "C:\private\raw-export" --tags "C:\private\tags.json" --map Ascent --out "C:\private\audit-1"

# Review the downloaded worksheet against its immutable saved audit.
.\.venv313\Scripts\python.exe scripts\audit_map_features.py --audit "C:\private\audit-1\audit.json" --review "C:\private\observations.json"
```

The editor's `--heights-dir` expects a directory containing `Ascent.height.npz`; a compiler report made
with that file must be attached to a page built with that same height context. Do not attach a measured
report to a flat page. Mismatched tag/image/height/version identities are refused, and edits make an attached
report stale. Export and regenerate it rather than trusting a previous green result.

For the inventory, check `scan.complete`, `scan.flags`, hash availability, `contract.compatible`, source
verification and coverage/filtered groups before proposing a support claim. `--max-rows` is useful for
exploration but explicitly produces an incomplete scan. Samples/property changes have reported retention
caps; a nearby retained row is neither a decoder nor proof that no other change occurred.

The HTML worksheet defaults to unverified. It supports multiple scenes per feature, download, and
transactional restore of a worksheet from the same audit. Download before closing; there is no server save.
The CLI requires an explicit export clock and rejects stale audit identities, unknown states/actions,
invalid ranges and incomplete verified scenes. Owner verification does not activate a consumer.

If only an archived `.vrf` is available, use the existing pinned exporter after checking its source and
BUILD identity. `scripts/export_replay.ps1 <uuid> -ArchiveDir <private-dir> -OutDir <new-private-export-dir>`
uses the UUID basename and preserves the recording. Choose a new output directory; do not overwrite a
prior export during evidence review. Do not alter the parser pin just because map groups were filtered.

## Starting prompt for the next agent

> Continue the Ascent assisted audit on its work branch. Read this runbook, the design, plan,
> implementation record and private owner packet. Never merge, change main or deploy. Preserve the
> current browser draft and latest full export. Start by presenting the guided editor and its exact
> remaining geometry issues, then ask one small batch for the concrete missing owner facts. Locate a
> complete raw Ascent export or source before claiming real replay support; available condensed rounds
> alone are insufficient. Run the offline audit, explain parser coverage, prepare a few specific scene
> windows and validate the owner's downloaded worksheet. Build any verified decoder in a separate gated
> change with held-out real scenes and consumer coverage. Do not activate candidate keyword matches,
> infer destruction from actor closure, guess heights or turn silence into approval.
