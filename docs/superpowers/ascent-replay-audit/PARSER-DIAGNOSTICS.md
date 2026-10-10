# Recover skipped map messages without activating a consumer

This records the earlier isolated diagnostic phase. The branch now includes the reviewed source-only
capture patch in its application build pin and a qualified site consumer. See
[site integration and release steps](SITE-INTEGRATION.md) for the current implementation; the experiment
below remains reproducible independently.

The pinned parser skips static map actors whose content has no decoder binding. Selecting its viewer
profile does not recover those payloads. The [diagnostic patch](../../../webapp/scripts/ascent_map_audit_parser.patch)
adds a separate, opt-in `map-audit.ndjson` containing original and transformed payload bits before that
dispatch decision. It does not assign feature IDs or semantic states, or add rows to the normal event stream.

## Reproduce in a separate parser checkout

Base parser commit: `2b66c65a7b116154e18ebb84d9f6795f2b080233`. Isolated tested patch commit:
`3794b1a4d10aa41c9746075cd785528da6b9c4b2`. The patch is saved here for review; it is not applied by
application builds, and does not change `webapp/replay_parser.json`. Never run the application's production
parser build script for this experiment. It can reset the configured checkout.

Create a new checkout of that exact base on a diagnostic branch, then run these commands there.
Substitute the actual app checkout and private source/output paths. Use a new output directory outside Git.

```powershell
git apply --check "C:\app-checkout\webapp\scripts\ascent_map_audit_parser.patch"
git apply "C:\app-checkout\webapp\scripts\ascent_map_audit_parser.patch"
dotnet test tests\Replay.Unreal.Tests\Replay.Unreal.Tests.csproj --configuration Release --filter "FullyQualifiedName~ContentBlockFramerTests|FullyQualifiedName~ContentBlockPathResolverTests|FullyQualifiedName~FieldPayloadParserTests"
dotnet publish src\CliReader\CliReader.csproj --configuration Release --output .\audit-bin

$auditPreviousFlag = $env:VALO_MAP_AUDIT_RAW
try {
    $env:VALO_MAP_AUDIT_RAW = '1'
    .\audit-bin\CliReader.exe export "C:\private\recording.vrf" --profile viewer --output "C:\private\new-map-diagnostic-export"
    if ($LASTEXITCODE -ne 0) { throw "Diagnostic export failed" }
} finally {
    $env:VALO_MAP_AUDIT_RAW = $auditPreviousFlag
}
```

The SDK roll-forward change permits the installed .NET 10 feature band; it is the same local build
adjustment already used for the pinned parser. Keep source commit, patch SHA-256, built assembly hashes,
source-recording hash, sidecar hash and export metadata in a separate private receipt. The manifest alone
does not identify all local modifications. Retain warnings and partial-parse counts.

## Sidecar contract and limits

Each `map_audit_raw_content` row has diagnostic version 1, replay time, packet/object/actor/channel identity,
available paths, actor/rep-layout flags, payload bit length, a truncation flag, and base64 wire/transformed
bytes. `was_decoded` is false. A downstream experiment must validate bit boundaries and retain its
descriptor hypotheses separately; raw numeric values are not verified state names.

Capture currently selects path prefixes `WindowShield`, `Switch_BlackMarket`, `B_Site_Door_Switch`,
`RespawningDestructible` and `RespawningWallPlate`. This is an evidence-retention heuristic, not a stable
map-feature binding. Subobjects can lack a class path. Payloads above 65,536 bits produce an explicit
truncated row with empty arrays; they cannot be treated as decoded evidence. Capturing content blocks
does not prove coverage of every actor lifetime, deleted block or replay message.

The capture restores the source bit position before normal processing. Verify that invariant with the
synthetic odd-bit and oversized-frame tests, then compare both SHA-256 and size of `events.ndjson` and
`movement.ndjson` with a pinned baseline exported from the same recording/profile. Also inspect parser
warnings: identical output does not establish complete replay coverage. The application audit CLI consumes
the normal export contract, not this new diagnostic sidecar.

## Evidence collected and next boundary

One complete local Ascent recording was exported and compared with four owner-reviewed rounds. Its
sidecar retained 2,813 frames with no truncation. Offline descriptor hypotheses framed 1,168 actor
messages without a framing error. Explicit door-destruction and glass `OnDie` messages were recovered.
Normal event and movement files matched the pinned baseline byte for byte; 25 focused parser tests passed.
The replay still reports its existing partial-bunch warnings. Raw files, actor IDs and scene receipts
remain private.

The full Market close took 5.002 seconds between the two recovered state-change RPCs. The reviewed
near-complete close also emitted the second state before destruction. These observations support a
closing/closed interpretation of those two values in this recording; they do not establish a universal
enum, switch-to-door association, reset/default-value rule, or intact reopening decoder. The owner reports
five seconds for both opening and closing; an intact opening scene remains unobserved in this recording.

Next, implement explicit class/descriptor and authored-feature bindings in a separate gated parser
change. Verify close, destruction and transparent-glass break on another Ascent match, preserve unknown
values, and distinguish round resets from intact reopening. Then connect the existing reducer and moving
height-band sampler to all control/gap consumers, using verified metre bounds and movement clearance.
Do not enable a flat whole-door blocker to avoid those unresolved inputs. Keep parser/schema versioning,
archive reproducibility and worker/web rollout in the release checklist. The owner decides merge and
deployment; this diagnostic patch enables neither.
