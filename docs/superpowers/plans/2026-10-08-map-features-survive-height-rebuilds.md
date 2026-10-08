# Map features survive automatic height rebuilds — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep permanent per-map gimmick tags usable across automatic height rebuilds, with automatic one-floor placement, immutable compilations and verifiable round references.

**Architecture:** Authoritative shapes and behaviour remain in `tags.json`; immutable compiled artifacts are shared per map/height/tags/compiler version in the database. Isolated children compile and verify artifacts; web and worker parents identify, archive and transport bytes. Planning, loading and both writers agree on an exact artifact digest, while pending placement contributes no effects and does not prevent a valid height from activating.

**Tech Stack:** Existing Python, FastAPI, SQLAlchemy/Alembic/Postgres, NumPy/SciPy/Pillow in isolated control children, stdlib JSON/gzip/hashlib, existing JavaScript tagger, pytest and Node parity tests. No new dependencies.

**Spec:** [2026-10-08-map-features-survive-height-rebuilds-design.md](../specs/2026-10-08-map-features-survive-height-rebuilds-design.md), initial version `c17e3cf`, with review corrections in this document branch.

## Global Constraints

- “There is no floor tagging: exactly one floor selects itself; a multi-floor or unplaceable gimmick is pending, blocks nothing, is reported, and is never deleted.”
- “Heights still build on the replay worker at 2 matches and then every 5 new matches, with `REPLAY_HEIGHTS_AUTO` on from the height-rebuild merge.”
- “A rebuild never edits `tags.json`.” Definitions remain per map; round summaries store only a digest and actual-input provenance.
- “The existing E6 guard remains until the replacement ships.” No intermediate deployment removes it.
- “No engine consumer is enabled here.” Keep the production consumer registry empty; tests inject a synthetic consumer.
- “Schema version remains 1”; retain deprecated floor fields in exported source while excluding known selectors from runtime normalization.
- “Do not bump global `CONTROL_REVISION`, `DATA_VERSION`, `SUMMARY_VERSION`, condenser recipe or height rules solely for this storage/compilation release”. Current inspected values are control 8, data 1, summary 1.
- “Retain historical artifacts; no pruning job in this release.” Preserve old height bytes too.
- Preserve bundle atomicity, legacy overlap reconciliation, fixed-domain/reducer policies, height integrity/policy, map locks, evidence-deletion off, parse preemption and existing retry rules.
- No parser, Impact, gimmick preset, state-evidence or engine-consumer work. Do not regenerate the legacy fixture to conceal changes.
- Before executing any schema change or database command, adding a dependency, pushing or creating a PR, stop for the owner's approval as required by the original request. Writing this plan authorizes none of those actions.
- Never touch credentials or either protected `afk/2026-10-07-height-slopes*` branch. The repository is public.

## Review Focus

1. A source file changes between planning and storing: both writers reject the superseded result without overwriting the visible old row (Task 7).
2. A malformed/corrupt compressed artifact attempts oversized allocation or a partial cache write: reject it before use; recover from durable bytes without accepting a poisoned cache (Tasks 3 and 6).
3. Flat legacy ground must be restored provisionally for placement, while one cell in a moving phase, trigger or route access becoming multi-floor disables all effects: pending bundles leave no candidate-ground edits, the complete dependent bundle contributes nothing, and an independent bundle remains eligible (Task 2).
4. A historical compiler is unavailable after deploy: verification distinguishes unsupported recompilation from successful hash checks and from current freshness (Task 10).
5. Source diagnostics fail during an otherwise valid height build: report failure explicitly, activate valid heights and retry feature preparation separately without rebuilding the same evidence (Task 8).

---

## Execution baseline and file ownership

This plan is a document-only deliverable. It was written on `codex/features-survive-heights`, based on `origin/main` at `d174443`. The inspected height implementation is **R**: `afk/2026-10-07-height-slopes-rebuild` at `5387aeb7661f002c759e74415ae0d9bf220cb92d`. Paths and line anchors below refer to R, not necessarily this checkout. New APIs below are proposed interfaces, not claims about existing functions.

The prerequisite has landed in locally available `origin/main`: **B** is `cf75c29ee03578cc31af4241af8139946ad9ac30`, the merge of PR #124. Its tree equals height-branch tip `9713e27edcebe6c4b1e8be1ff432ea4cb2266065`. At execution, fetch with authorization, create/reuse a managed implementation worktree from the resulting main, record its SHA, and refresh every original R anchor using `rg`/read-only Git. Preserve emitted-floor separation, `HEIGHT_RULES_REVISION=2`, failed-rebuild retirement and current worker scheduling (`c06eb02`, `c5ddb72`); do not revert to R behavior. New review-evidence anchors explicitly marked B refer to integrated main. Do not cherry-pick or check out either protected branch to satisfy this dependency. If its final interfaces differ, update this plan before editing code. Confirm the frozen-contract intent and original proof obligations while preserving all integrated B rule/test fixes; B is the implementation baseline, not an instruction to restore R. Confirm the committed consumer registry and generation-pointer state; unexpected live pointers require explicit conversion, not dual runtime selection.

The worker currently exposes private, unauthenticated service routes (`replay_worker/server.py:100`; `replay_worker/README.md:53`). Both documents use this verified private-service boundary and existing body/task limits; this plan introduces no authentication system.

| File(s), relative to repository root | Responsibility | Task |
| --- | --- | --- |
| `webapp/app/replays/map_feature_schema.py`, new `map_feature_inputs.py`, normalization/validation in `control_tagger_core.js` | Source normalization, stdlib versions/registry, context-qualified identity | 1 |
| New `webapp/tests/replays/map_feature_artifact_toys.py`, `test_map_feature_inputs.py` | Small deterministic fixtures and identity proofs | 1 |
| `webapp/app/control/features.py`, `geometry.py`; `test_control_features.py` | Sole-floor placement, atomic bundles, full compilation | 2, 3, 11 |
| New `webapp/app/replays/map_feature_artifacts.py`; new `test_map_feature_artifacts.py` | Canonical archive codec and parent-safe integrity checks | 3 |
| `webapp/app/models/replay.py`, `app/models/__init__.py`; proposed `alembic/versions/0020_control_feature_artifacts.py`; new `app/services/control_feature_artifacts.py`, `tests/replays/test_control_feature_artifacts_db.py` | Immutable database archive and exact-key lookup | 4, 5 |
| New `webapp/app/control/feature_job.py`; new `tests/replays/test_feature_job.py`; `test_control_isolation.py` | Child compile/diagnose/verify commands; no heavy parent imports | 5, 8, 10 |
| `replay_worker/server.py`, `control_job.py`, `Dockerfile`; `webapp/app/control/task.py`; `test_replay_worker_control.py` | Exact artifact transport, bounded cache and child loading | 6 |
| `webapp/app/services/replay_control.py`, `replay_control_remote.py`, `replay_control_store.py`, `replay_gaps.py`, `replay_gaps_store.py`, `control_heights.py`; `app/replays/control_format.py`, `height_inputs.py`; `app/control/geometry.py`, `encode.py`; `scripts/compute_control.py`; control/gap store/remote tests | Shared current-input lookup, explicit flat selection, control/gap provenance and writers | 7 |
| New `webapp/app/control/feature_diagnostics.py`; `app/control/geometry.py`, `height_job.py`, `height_verify.py`; `replay_worker/server.py`, `height_job.py`; `app/services/replay_control_remote.py`, `control_heights.py`, `replay_heights_remote.py`; height HTTP/builder/verifier/DB tests | Tag-independent height checks, snapshot transport, truthful reports and independent preparation | 8, 11 |
| `webapp/scripts/control_tagger.py`, `control_tagger_core.js`, `control_tagger_features.js`, `control_tagger.template.html`; `webapp/tests/replays/test_map_feature_tagger.py` | Remove floor prompts/instructions; source round-trip and preview parity | 9 |
| New `webapp/app/services/control_feature_verification.py`, new `tests/replays/test_control_feature_verification.py` | Historical verification separated from current freshness | 10 |
| `webapp/scripts/control_heights.py`, `build_control_geometry.py`; frozen contract and height design; new `docs/superpowers/map-features-survive-heights/README.md` | Final E6/pointer retirement, amendments and release runbook | 11, 12 |

Test commands run from the implementation worktree's `webapp` directory with the existing interpreter, without dependency installation:

```powershell
$py = 'C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe'
& $py -m pytest -p no:cacheprovider -q tests/replays/test_control_features.py tests/replays/test_control_reference.py tests/replays/test_control_isolation.py
```

First confirm this interpreter contains the existing requirements and Node is available. The database fixtures use isolated SQLite, never a configured site database; even those commands wait for the owner's database-command approval. Postgres/migration rehearsal needs separate approval and an explicitly isolated disposable database. Never read or print `.env` files. Run baseline tests before modifications and preserve their failures separately from new failures.

## Shared interfaces and wire contract

All new stdlib types below live in `map_feature_inputs.py` or `map_feature_artifacts.py`. JSON objects are ordinary `dict` values at the boundary; immutable snapshots mean serialize/copy them on construction, never share mutable caller dictionaries.

```python
# map_feature_inputs.py
@dataclass(frozen=True)
class FeatureKey:
    map_name: str
    height_digest: str  # existing height digest, or literal "flat"
    tags_digest: str    # full SHA-256
    compiler_version: int

@dataclass(frozen=True)
class FeatureInput:
    key: FeatureKey
    canonical_inputs: bytes  # normalized definitions + archived base/context
    diagnostic_source: bytes  # exact raw tags.json bytes, separate from runtime hashing
    source_sha256: str

class FeatureInputsError(ValueError): pass

@dataclass(frozen=True)
class SourceSnapshot:
    map_name: str
    raw_bytes: bytes           # exact original whole tags.json snapshot
    raw_sha256: str            # SHA-256(raw_bytes), before parsing
    map_entry_bytes: bytes     # separately canonicalized parsed map entry

# Proposed versions: compiler 2, manifest 2, normalization 2, artifact wire 1,
# provenance 1. Verify current constants before choosing these next versions.
FEATURE_COMPILER_VERSION = 2
FEATURE_MANIFEST_VERSION = 2
FEATURE_NORMALIZATION_VERSION = 2
FEATURE_CANONICAL_TAG_VERSION = 2
FEATURE_WIRE_VERSION = 1
RUNTIME_CONSUMERS = frozenset()
CONSUMER_VERSIONS = {}

# map_feature_artifacts.py
@dataclass(frozen=True)
class FeatureArtifact:
    digest: str
    key: FeatureKey
    manifest: dict
    inputs: bytes  # exact canonical FeatureInput.canonical_inputs
    assets: bytes  # deterministic gzip of canonical compiled JSON
    code_commit: str

class FeatureArtifactError(ValueError): pass
class FeatureArtifactMissing(FeatureArtifactError): pass
class FeatureArtifactCorrupt(FeatureArtifactError): pass
class UnsupportedFeatureCompiler(FeatureArtifactError): pass
```

Public interfaces established by tasks:

- Task 1: `capture_source_snapshot(map_name: str, raw_source: bytes) -> SourceSnapshot`; `identify_features(map_name: str, height_digest: str | None, source: SourceSnapshot, base: dict, *, consumers: dict[str, int] | None = None) -> FeatureInput | None`; `diagnostic_identity(source: SourceSnapshot) -> tuple[str, str, bytes]` returns canonical catalogue digest, exact raw-source SHA and exact raw bytes. Recompute/verify raw SHA from bytes; never derive it from a parsed dict. Parsing/normalization errors raise `FeatureInputsError`; capture preserves raw evidence in the diagnostic error envelope. `base` contains reconstructable permanent mask descriptors, specials, scale and legacy inputs. Production defaults to the empty shared registry. `FeatureInput.diagnostic_source` is the exact snapshot raw bytes; `source_sha256` is the corresponding raw hash, outside runtime identity. `None` means no intended consumer bundle, never compilation failure.
- Task 2: `resolve_cells(geo: Geometry, cells: list[int], path: str) -> Placement`; `placement(geo: Geometry, source: dict) -> dict[str, Placement]`. `Placement` in `features.py` contains `nodes: tuple[int, ...]`, `ground_m: tuple[float, ...]`, `reasons: tuple[dict, ...]`; `ok` is true only for nonempty complete placement. Reason dictionaries contain `code`, `path`, `cells`, `cell_count`, `floor_counts`, and optional `members`.
- Task 3: `compile_artifact(geo: Geometry, inputs: FeatureInput, code_commit: str) -> FeatureArtifact`; `check_artifact(artifact: FeatureArtifact) -> None` checks bytes in stdlib; `verify_artifact(artifact: FeatureArtifact, geo: Geometry) -> None` also recompiles archived definitions using supported recorded semantics. `encode_artifact(artifact: FeatureArtifact) -> bytes`; `decode_artifact(blob: bytes) -> FeatureArtifact` in the stdlib codec.
- Task 4: `find_artifact(db: Session, key: FeatureKey) -> FeatureArtifact | None`; `find_artifact_header(db: Session, key: FeatureKey) -> FeatureArtifactRef | None`; `FeatureArtifactRef` in the stdlib codec has `digest: str`, `key: FeatureKey`, `manifest: dict` only, never arrays/compressed payloads; `load_artifact(db: Session, digest: str) -> FeatureArtifact`; `store_artifact(db: Session, artifact: FeatureArtifact) -> FeatureArtifact`. No helper commits its caller's transaction.
- Task 5: `prepare_artifact(db: Session, inputs: FeatureInput, height_bytes: bytes | None) -> FeatureArtifact`; `run_feature_child(mode: str, payload: bytes) -> bytes`. It unwraps the child response and returns canonical result bytes; compile returns encoded artifact bytes, diagnose returns diagnostic JSON bytes, verify returns verification JSON bytes. Typed errors include `UnsupportedFeatureCompiler` for an unavailable recorded compiler. Modes are `compile`, `diagnose`, `verify`. Preparation failures raise `FeaturePreparationPending`, a service exception with `__init__(key: FeatureKey, reason: str)` carrying those attributes, never produce a successful `None`.
- Task 6: `feature_failure(error: FeatureArtifactError) -> dict` in the stdlib artifact module maps missing/corrupt to `error_kind="infra"` and unsupported semantics to `error_kind="compat"`, with stable `error_code` (`features_missing`, `features_corrupt`, `features_unsupported`). `ControlClient.push_features(artifact: FeatureArtifact) -> None`; `NeedsFeatures(FeatureArtifactMissing)` in the remote service; `load_task_features(task: dict, geo: Geometry, cache_dir: Path) -> FeatureArtifact | None` in `task.py`. Digest-less tasks forbid feature loading.
- Task 7: `PinnedInputs` in `replay_control.py` carries `geometry: dict`, `feature_key: FeatureKey | None`, `artifact_digest: str | None`, `snapshot_sha: str`. `PlannedRound.inputs: PinnedInputs | None` is additive; no computable feature-bearing job lacks it. `fingerprint_from_inputs(envelope: dict) -> str` in `control_format.py` preserves the existing canonical fingerprint algorithm. `verify_result_inputs(planned: PinnedInputs, result: dict) -> dict | None` in `replay_control_store.py` returns valid provenance, or raises `StaleControlInputs`/`InvalidControlInputs`, both new service exceptions. Neither writes a row.
- Task 8: `diagnose_features(geo: Geometry, source: SourceSnapshot, previous: dict | None = None, *, consumers: dict[str, int] | None = None) -> dict` in `feature_diagnostics.py`; `refresh_feature_report(db: Session, map_name: str, height_digest: str | None) -> dict` in the artifact service runs it in a child. Manual refresh returns/stores a fresh diagnostic separately from immutable build-time reports.
- Task 10: `verify_stored_round(db: Session, row: ReplayRoundControl) -> VerificationResult`; `VerificationResult` has `integrity: str` (`verified`/`failed`/`unavailable`), `recompilation: str` (`verified`/`unsupported`/`failed`/`unavailable`), `freshness: str` (`current`/`stale`/`unavailable`), `reasons: tuple[str, ...]`.

The child protocol is JSON `{mode, inputs, height, artifact, diagnostic_source, previous}`; bytes use base64. Each mode requires only its relevant fields, rejects extra/conflicting runtime identities, and returns either `{ok: true, result: ...}` or `{ok: false, code, reason}`. Build diagnostics use the separately versioned envelope defined in Task 8, not the height evidence manifest. Use existing subprocess timeout/limit conventions from height verification; parent errors carry explicit compile/source/integrity categories. Never import `app.control` at module scope from services or worker server.

## Task 1: Normalize permanent source and identify exact compilation inputs

**Files:** Modify `webapp/app/replays/map_feature_schema.py:278-312,421-463,540-545,559-560,589,628-695`, `webapp/scripts/control_tagger_core.js` normalization/validation, and existing `test_map_feature_tagger.py` parity assertions; create `map_feature_inputs.py`, both test/helper files listed above, and `webapp/tests/fixtures/control/map_features/canonical_v2.json` with shared golden vectors.

**Interfaces:** Produce `FeatureKey`, `FeatureInput`, `SourceSnapshot`, `capture_source_snapshot`, shared versions/registry, `identify_features`, `diagnostic_identity`, and `map_feature_schema.tag_canonical_bytes(value: object) -> bytes`. Source capture stores exact bytes/SHA and separately serialized parsed map entry; dictionary-only callers must be changed explicitly. Consume existing schema validation, runtime projection and state-semantics constants through their stdlib modules.

- [ ] **Step 1: Add deterministic fixtures and failing identity tests.** Define `source_case() -> dict` in `map_feature_artifact_toys.py` using `map_feature_schema.empty()`, the existing `feature`/`rect` shape conventions from `test_control_features.py:400-455`, two independent features/bundles, no floor selectors, explicit resolved bounds and behaviour. Feature 1 uses grid cell `(40,40)`, feature 2 `(40,43)`; bundle 1 is enabled for synthetic `test`, bundle 2 disabled. Use IDs `feature-1`, `feature-2`, `bundle-1`, `bundle-2`. Define `base_case() -> dict` with packed permanent sight/walk descriptors and an explicit absent barrier for a four-cell hall, scale `7e-5`, empty specials and legacy reconciliation input. Keep helpers importable without heavy imports; heavy geometry fixtures are added in Task 2.

```python
# Stdlib-only fixtures; masks match geometry_case's four-cell hall exactly.
def source_case():
    from app.replays.map_feature_schema import empty, known
    mf = empty()
    for ordinal, col in ((1, 40), (2, 43)):
        x, y = col * 8, 320
        shape = {'type': 'polygon', 'uv': [[a * 10000 / 1024, b * 10000 / 1024]
                 for a, b in ((x, y), (x + 8, y), (x + 8, y + 8), (x, y + 8))]}
        mf['features'].append({
            'id': f'feature-{ordinal}', 'name': f'Door {ordinal}',
            'bundle': f'bundle-{ordinal}', 'initial_state': 'closed', 'transitions': [],
            'states': [
                {'name': 'closed', 'blocks_movement': True, 'blocks_sight': True,
                 'footprint': shape, 'sight_bounds': {'ref': 'ground',
                     'bottom': known(0, 'm'), 'top': known(3, 'm')}},
                {'name': 'open', 'blocks_movement': False, 'blocks_sight': False}],
        })
        mf['bundles'].append({'id': f'bundle-{ordinal}', 'enabled': ordinal == 1,
                              'runtime_consumer': 'test', 'members': [f'feature-{ordinal}']})
    return {'map_features': mf, 'specials': []}


def base_case():
    import base64
    walk = bytearray(1024 * 1024 // 8)
    for row in range(320, 328):
        walk[row * 128 + 40:row * 128 + 44] = b'\xff' * 4
    def descriptor(packed):
        return {'shape': [1024, 1024], 'bitorder': 'little', 'count': 1024 * 1024,
                'bytes': len(packed), 'data': base64.b64encode(packed).decode('ascii')}
    return {'sight': descriptor(bytes(v ^ 255 for v in walk)),
            'walk': descriptor(walk), 'barrier': None,
            'scale': 7e-5, 'specials': [], 'legacy': {}}



def snapshot_case(entry=None, *, whitespace=False):
    import json
    from app.replays.map_feature_inputs import capture_source_snapshot
    raw = json.dumps({'maps': {'Summit': source_case() if entry is None else entry}},
                     indent=2 if whitespace else None, ensure_ascii=False).encode('utf-8')
    return capture_source_snapshot('Summit', raw)
def test_editorial_and_deprecated_selectors_do_not_move_runtime_identity():
    from copy import deepcopy
    first = source_case()
    second = deepcopy(first)
    second['map_features']['features'][0].update(name='Reviewed door', floors=['floor-99'])
    second['map_features']['notes'] = 'owner annotation'
    a = identify_features('Summit', 'a' * 12, snapshot_case(first), base_case(), consumers={'test': 1})
    b = identify_features('Summit', 'a' * 12, snapshot_case(second), base_case(), consumers={'test': 1})
    assert a.key == b.key
    assert a.canonical_inputs == b.canonical_inputs
    assert diagnostic_identity(snapshot_case(first)) != diagnostic_identity(snapshot_case(second))
```

Also pin every key axis; unknown nested key edits; integer/whole-float and list-order parity; changing an unregistered/disabled feature's unrelated behaviour; changing its relevant outside-overlap base edit; invalid schema version; nonfinite JSON; duplicate IDs; unresolved but intended bundles; empty production registry returning `None`. Relevant disabled overlap edits must change identity even when all intended bundles are pending.

- [ ] **Step 2: Run `& $py -m pytest -p no:cacheprovider -q tests/replays/test_map_feature_inputs.py`; expect missing-module/API failures.**
- [ ] **Step 3: Implement normalization and identity.** Copy source before normalization. Drop only known selectors at their documented paths: top-level `floors`, feature `floors`, trigger `floor`, endpoint/access `floor`, `base_edits.ground_binding`, and ground-bound `floor`. Do not recursively remove every key named `floor`; unknown nested keys remain runtime. Stop validating deprecated floor catalogues/selectors or following their dangling references; remove floor-only warnings/errors and same-coordinate route exceptions based on distinct authored floor IDs. Preserve validation of all non-floor references, behaviour and geometry. Apply these exact normalization/validation edits in JavaScript in this task so existing Python/Node parity stays green; Task 9 removes the UI and replaces preview placement. Keep export metadata/editorial exclusions and canonical number/list ordering consistent with Python/JS. Resolve intended bundles before placement and include their members, targeted triggers, owned routes and required references; include outside base-edit inputs that affect exact overlap checks.

```python
# Python-only artifact/context JSON; this is NOT the browser tag-digest format.
def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')

# After the schema's numeric normalization, construct the context envelope:
envelope = {
    'schema': 1, 'normalization': FEATURE_NORMALIZATION_VERSION,
    'runtime': intended_projection, 'outside_base_edits': outside_base_edits,
    'legacy': base['legacy'], 'base': base,
    'reducer': reducer_identity, 'consumers': consumer_versions,
}
tags_digest = hashlib.sha256(canonical_json(envelope)).hexdigest()
key = FeatureKey(map_name, height_digest or 'flat', tags_digest, FEATURE_COMPILER_VERSION)
```

Here `intended_projection`, `outside_base_edits`, `reducer_identity` and `consumer_versions` are local values built by `identify_features`; they are serialized values, not new APIs. Store exactly this envelope in `canonical_inputs`; source-only labels/snapshots stay in `diagnostic_source`. Share registry constants with `features.py` later rather than importing that heavy module in parents.


The feature-only browser/Python digest format is **tag canonical v2**, separate from the UTF-8 artifact/context JSON above and the unchanged generic v1 `digest` helper. Define its exact typed tree in `map_feature_schema.py`:

```python
def tag_canonical_bytes(value: object) -> bytes:
    import math, struct
    def typed(item):
        if item is None:
            return ['null']
        if isinstance(item, bool):
            return ['bool', item]
        if isinstance(item, (int, float)):
            if isinstance(item, int) and abs(item) > 9007199254740991:
                raise ValueError('integer outside JavaScript safe range')
            number = float(item)
            if not math.isfinite(number):
                raise ValueError('nonfinite number')
            if number.is_integer() and abs(number) > 9007199254740991:
                raise ValueError('whole number outside JavaScript safe range')
            return ['number', struct.pack('>d', 0.0 if number == 0 else number).hex()]
        if isinstance(item, str):
            return ['string', item]
        if isinstance(item, list):
            return ['array', [typed(v) for v in item]]
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            keys = sorted(item, key=lambda k: k.encode('utf-16-be', errors='surrogatepass'))
            return ['object', [[k, typed(item[k])] for k in keys]]
        raise ValueError('unsupported JSON value')
    return json.dumps(['feature-tags-v2', typed(value)], ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')
```

Attach source paths to numeric/type validation failures in the public normalizer. Mirror the tree in JS using `DataView.setFloat64(0, number, false)` and eight-byte hex; normalize `Object.is(number, -0)`, reject nonfinite and unsafe whole-valued numbers, and use existing UTF-16 key ordering and ASCII quoting. Hash the ASCII output with existing `sha256Hex`; do not feed UTF-8 characters to that ASCII-only hash. `runtime_digest`/`F.runtimeDigest` use v2 over the normalized runtime projection (16 hex remains advisory); the full context key uses SHA-256 of the Python-only envelope. Generic `digest`/`F.digest` remain v1 and must not be mistaken for the new runtime digest.

Store shared input/expected canonical ASCII vectors in `canonical_v2.json`: `1e-7` (binary64 `3e7ad7f29abcaf48`), `1e-6`, `-0.0`/`0`, `1`/`1.0`, `5e-324`, Unicode values and keys including U+10000 vs U+E000, `9007199254740991`, and rejection of either sign of `9007199254740992` and nonfinite input. Include literal byte expectations and digest expectations from those bytes, not expectations generated by the implementation under test. Browser-source JSON cannot preserve unsafe integers exactly, so refuse their runtime normalization instead of claiming parity; retain captured source as evidence. Test relevant unknown keys too.

```python
def test_whitespace_changes_raw_identity_but_not_runtime_identity():
    a, b = snapshot_case(), snapshot_case(whitespace=True)
    assert a.raw_bytes != b.raw_bytes and a.raw_sha256 != b.raw_sha256
    assert diagnostic_identity(a)[0] == diagnostic_identity(b)[0]
    x = identify_features('Summit', None, a, base_case(), consumers={'test': 1})
    y = identify_features('Summit', None, b, base_case(), consumers={'test': 1})
    assert x.key == y.key and x.canonical_inputs == y.canonical_inputs
```
- [ ] **Step 4: Run identity/schema tests plus `test_map_feature_tagger.py`'s existing digest parity tests; expect pass.** Preserve `legacy_inputs.json` unchanged; source export version stays 1.
- [ ] **Step 5: Commit this deliverable.**

```powershell
git add webapp/app/replays/map_feature_schema.py webapp/app/replays/map_feature_inputs.py webapp/scripts/control_tagger_core.js webapp/tests/replays/test_map_feature_tagger.py webapp/tests/replays/map_feature_artifact_toys.py webapp/tests/replays/test_map_feature_inputs.py webapp/tests/fixtures/control/map_features/canonical_v2.json
git commit -m "feat: identify permanent map feature inputs"
```

## Task 2: Resolve every required cell and suppress pending bundles atomically

**Files:** Modify `webapp/app/control/features.py:140-206,266-319,470-605,629-677`, `test_control_features.py`; extend `map_feature_artifact_toys.py`.

**Interfaces:** Produce `Placement`, `resolve_cells`, `placement`. Preserve existing movement/sight/route function return contracts. Change their internal placement source from authored bands to this shared resolver. `placement` maps feature IDs to aggregate results including required dependent triggers/routes.

- [ ] **Step 1: Add a cheap heavy fixture and failing placement tests.** Preserve the existing `test_a_publishable_bundle_restores_its_own_ground_and_sight_and_nothing_else` fixture and add full compiler coverage for its flat candidate domain. Define `geometry_case(*, ground_dm: int = 0, origin_dm: int = 100, multi: bool = False, unresolved: bool = False, flat: bool = False) -> Geometry` in the helper, with imports inside the function:

```python
def geometry_case(*, ground_dm=0, origin_dm=100, multi=False,
                  unresolved=False, flat=False):
    import numpy as np
    from app.control.geometry import geometry_from_masks, attach_heights
    from app.control.heights import HeightAsset, MAX_FLOORS
    walk = np.zeros((1024, 1024), dtype=bool)
    walk[320:328, 320:352] = True
    geo = geometry_from_masks('Summit', ~walk, walk, 7e-5, [])
    if flat:
        return geo
    floors = np.full((128, 128, MAX_FLOORS), -1, dtype=np.int16)
    floors[40, 40:44, 0] = ground_dm
    if multi:
        floors[40, 40, 1] = ground_dm + 40
    if unresolved:
        floors[40, 40, :] = -1
    missing = floors[..., 0] < 0
    asset = HeightAsset(floors, np.zeros_like(floors), ~missing, missing,
                        np.empty((0, 5), dtype=np.int32), {'origin_z': origin_dm})
    return attach_heights(geo, asset)


def test_synthetic_lowest_node_is_not_a_measured_floor():
    geo = geometry_case(unresolved=True)
    cell = 40 * 128 + 40
    assert geo.node_of[cell, 0] >= 0  # proves the misleading old shortcut
    bound = resolve_cells(geo, [cell], 'features.feature-1.states.closed.footprint')
    assert not bound.ok and bound.nodes == ()
    assert any(r['code'] in {'missing_floor', 'unresolved_height'} for r in bound.reasons)
```

`HeightAsset`, `MAX_FLOORS` and `STAND_M` are exported by `app.control.heights` at R; import those existing constants in the implementation rather than duplicating them. Add parameterized tests corrupting only one of: movement, separate sight, rotation phase, trigger area, route endpoint, authored access site, potential-ground edit. For each, assert the complete owning bundle has no blocked nodes/occluders/arcs/base deltas/trigger activation and independent bundle 2 remains active after enabling it. Assert paths, row-major cell IDs, counts and dependency members, not only a nonempty warning. Include empty/off-ground/unplaced/nonfinite/unresolved, flat vs unresolved-asset, explicit world/all-height bounds, and recovery on the next sole-floor asset. Do not snap coordinates outside the map into edge cells.

- [ ] **Step 2: Run `test_control_features.py` new selections; expect floor-selector or partial-effect failures.**
- [ ] **Step 3: Implement the shared placement rule and thread it through all compile paths.** Validate asset dimensions/domain first. For each sorted unique required cell, reject out-of-domain coordinates before raster clamping; for a flat bundle use its privately prevalidated candidate walk domain described below; if an asset exists, reject missing permanent walkable ground and count its actual finite nonnegative floor entries and check unresolved flags before selecting a node. Accumulate all errors instead of returning at the first bad cell. Return no nodes/ground whenever any cell fails.

```python
# Core selection inside resolve_cells; reasons are accumulated per code/path.
real = np.isfinite(asset.floors.reshape(128 * 128, -1)[cell]) & (
    asset.floors.reshape(128 * 128, -1)[cell] >= 0)
count = int(real.sum())
if count == 1 and not asset.unresolved.ravel()[cell] and geo.walk.ravel()[cell]:
    level = int(np.flatnonzero(real)[0])
    node = int(geo.node_of[cell, level])
    if node >= 0 and np.isfinite(geo.node_z[node]):
        nodes.append(node)
        ground_m.append(float(geo.node_z[node]) - STAND_M)
# Otherwise emit missing_floor/multi_floor/unresolved_height/off_ground;
# after processing all cells, any reason clears the complete nodes/ground lists.
```

For genuine flat geometry use one eligible cell node with no measured-ground claim. Before flat placement, validate enabled/registered intent, exact legacy paint ownership/reclassification and outside overlap without applying edits. Clone permanent masks into a private candidate domain for that bundle alone, adding only its permitted potential ground; resolve all members/states/phases/triggers/routes against that candidate. Discard the candidate on any failure and publish all base deltas only after complete eligibility. Independent bundles cannot borrow candidate ground. Preserve the existing flat restoration fixture; height-backed restoration never extends the real-floor domain. For every feature collect required cells from all states and authored rotation phases, every sight/bounds footprint, potential-ground edit and dependent trigger/endpoint/access geometry; route drawings remain decorative. Resolve them against the correct permanent-height/private-flat candidate domain before producing any state output. Reconcile legacy paints exactly as before, then compute bundle eligibility as enabled + registered + resolved behaviour + complete placement + exact/no-outside overlap. Do not invent dimensions or duration. Recompute ground bounds as median selected `node_z - STAND_M` plus offsets; convert world bounds using this asset's `origin_z / 10`. Keep fixed-domain policies. For height-backed geometry restore ground only where a real floor exists and complete placement succeeds; for flat geometry preserve authorized two-phase legacy restoration. No new measured/synthetic height floors.

- [ ] **Step 4: Run `test_control_features.py` and height geometry tests.** Replace only tests that explicitly assert obsolete manual band selection; preserve traversal, reducer, overlap and non-floor invariants. Both height digests must resolve fresh node indices, including when upper nodes are inserted elsewhere.
- [ ] **Step 5: Commit.**

```powershell
git add webapp/app/control/features.py webapp/tests/replays/test_control_features.py webapp/tests/replays/map_feature_artifact_toys.py
git commit -m "feat: place map features on their sole measured floor"
```

## Task 3: Archive complete compiled assets and verify correspondence

**Files:** Create `webapp/app/replays/map_feature_artifacts.py`, `tests/replays/test_map_feature_artifacts.py`; modify `app/control/features.py:832-929` and `test_control_features.py`.

**Interfaces:** Produce `FeatureArtifact`, exceptions, canonical codec, `compile_artifact`, `check_artifact`, `verify_artifact`. Use the mask descriptor keys `shape`, `bitorder`, `count`, `bytes`, `data` from Task 1 fixtures. An absent optional barrier is explicitly null, distinct from a present zero mask; preserve that identity when reconstructing geometry. Additional codec functions: `pack_mask(raw: bytes, shape: tuple[int, ...]) -> dict`, `unpack_mask(descriptor: dict) -> bytes`. Their raw argument/result is row-major 0/1 bytes, so parent code needs no NumPy. Compiled assets are data, not executable instructions.

- [ ] **Step 1: Add failing archive and all-pending tests.**

```python
def test_archive_contains_loadable_masks_and_pending_is_not_none():
    geo = geometry_case()
    inputs = identify_features('Summit', geo.height_sha, snapshot_case(),
                               base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inputs, '0' * 40)
    check_artifact(artifact)
    assert decode_artifact(encode_artifact(artifact)) == artifact
    verify_artifact(artifact, geo)
    pending_geo = geometry_case(multi=True)
    pending_inputs = identify_features('Summit', pending_geo.height_sha,
                                       snapshot_case(), base_case(), consumers={'test': 1})
    pending = compile_artifact(pending_geo, pending_inputs, '0' * 40)
    assert pending.manifest['intended_bundles'] == ['bundle-1']
    assert pending.manifest['active_bundles'] == []
    assert pending.manifest['pending']
```

Inspect decoded compiled JSON: each occluder has actual packed mask bytes, dimensions and bit order, not a bare mask hash. Test round-trip barriers/base masks, sight bounds, bindings, arcs, domain/base deltas, distinct height-origin bounds, two valid gzip encodings of the same canonical content, tampered mask/node/input/part/digest, unsupported versions, reordered source, code-commit/time metadata not moving runtime digest, JSON nonfinite values, invalid array sizes, excess decompressed bytes and trailing/incomplete payloads.

- [ ] **Step 2: Run `test_map_feature_artifacts.py`; expect missing codec/compiler APIs.**
- [ ] **Step 3: Implement canonical serialization.** Use fixed little-bit-order packed booleans, explicit shape, element count, byte count and padding validation; little-endian types for numeric arrays if encoded as bytes. Reject duplicate JSON keys, invalid base64 and nonfinite numbers. Use deterministic gzip `mtime=0`. Initially cap artifact wire bodies at 16 MiB and decompressed JSON at 64 MiB; keep named constants shared with worker, test them and measure suitability in Task 12 before release. Bound decompression with an incremental reader; never call unbounded `gzip.decompress` on incoming blobs.

```python
# Identity policy: canonical manifest includes input SHA and all loaded part SHAs.
manifest = {
    'v': FEATURE_MANIFEST_VERSION, 'key': dataclasses.asdict(inputs.key),
    'inputs_sha256': hashlib.sha256(inputs.canonical_inputs).hexdigest(),
    'schema': 1, 'normalization': FEATURE_NORMALIZATION_VERSION,
    'intended_bundles': intended_ids, 'active_bundles': active_ids,
    'pending': canonical_pending, 'compiled': part_hashes,
}
digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
assets = gzip.compress(canonical_json(compiled_parts), compresslevel=9, mtime=0)
```

`intended_ids`, `active_ids`, `canonical_pending`, `part_hashes`, `compiled_parts` are local compiler results. Include complete per-state blocked nodes, occluder masks/bands, route arcs, trigger bindings, base-domain and base-edit delta data even though this release wires no consumer. A pending-only artifact still stores an empty effect set with outcomes. Shared identity code reconstructs the input key from archived envelope; `check_artifact` verifies key/input/manifest/parts/content address. Heavy verification reconstructs permanent geometry from archived bytes, attaches the named height, re-runs the sole compiler and compares canonical manifest and expanded canonical compiled bytes (including actual packed mask/numeric bytes), not the gzip stream. Heavy recompilation refuses unsupported compiler/normalization/reducer/consumer identities. Stdlib integrity checking can still hash a supported archive format with unavailable compiler semantics, enabling the independent historical verdict in Task 10. Code commit is audit metadata, not permission to execute arbitrary historical code.

- [ ] **Step 4: Run codec/compiler/identity tests; pass.** Verify old no-intended `manifest` remains `None`; do not globally invalidate no-tag inputs.
- [ ] **Step 5: Commit.**

```powershell
git add webapp/app/replays/map_feature_artifacts.py webapp/app/control/features.py webapp/tests/replays/test_map_feature_artifacts.py webapp/tests/replays/test_control_features.py
git commit -m "feat: archive and verify complete map feature compilations"
```

## Task 4: Add the immutable per-map database archive

**Files:** Modify `webapp/app/models/replay.py:106-131`, `app/models/__init__.py`, `app/replays/map_feature_artifacts.py` (header reference type); create `app/services/control_feature_artifacts.py`, `tests/replays/test_control_feature_artifacts_db.py`, proposed `alembic/versions/0020_control_feature_artifacts.py`. Inspect the landed Alembic head; if `0020` is occupied, choose the next free four-digit prefix and record that actual filename/head in this plan and runbook before proceeding.

**Interfaces:** Produce `ControlFeatureArtifact`, `FeatureArtifactRef`, `find_artifact_header` and `find_artifact`/`load_artifact`/`store_artifact`. Header-only lookup loads/rechecks key and canonical manifest digest without decompressing assets; full loading/transport/verification still rehash actual archive bytes. Consume stdlib `FeatureKey`, `FeatureArtifact`, codec/integrity checks. Parent code must not import compiler modules.

- [ ] **Step 1: Obtain the owner's schema/database-command approval with a concrete migration proposal.** Show the table model below, migration filename/down-revision, immutable insert semantics and isolated test/rehearsal commands. Do not run a database fixture, `create_all`, Alembic or site query until approved. No dependency addition is needed.
- [ ] **Step 2: Write failing isolated database tests.** Extend the existing SQLite factory's explicit `CONTROL_TABLES` list to include the new table only. Verify per-map sharing; map/height/tags/compiler uniqueness; idempotent identical insert; conflicting same-key insert; missing/corrupt lookup; rollback atomicity; retained old heights/artifacts; independent sessions racing inserts. Test concurrency with separate connections rather than SQLite's single in-memory connection.

```python
def test_same_key_never_overwrites_different_bytes(db, artifact):
    stored = store_artifact(db, artifact)
    db.flush()
    assert load_artifact(db, stored.digest) == stored
    from dataclasses import replace
    with pytest.raises(FeatureArtifactError):
        store_artifact(db, replace(artifact, assets=artifact.assets + b'x'))
    assert load_artifact(db, stored.digest).assets == artifact.assets
```

Define the local `artifact` pytest fixture by compiling `source_case`/`geometry_case` with the Task 1 synthetic registry; the existing `db` fixture is from `test_control_store.py`'s factory. Run the new tests; expect missing model/service.

- [ ] **Step 3: Implement the model, migration and immutable insert service.**

```python
class ControlFeatureArtifact(Base):
    __tablename__ = 'control_feature_artifacts'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    map_name: Mapped[str] = mapped_column(String(64), nullable=False)
    height_digest: Mapped[str] = mapped_column(String(12), nullable=False)
    tags_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    compiler_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    inputs: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    assets: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    code_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                               server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint('map_name', 'height_digest', 'tags_digest',
                                      'compiler_version', name='uq_control_feature_key'),)
```

Use `flat` in the height string, not null. Use existing JSON conventions for this codebase. Validate archive bytes before insert, query by key, and compare canonical runtime bytes on an existing key; different audit timestamp/commit or valid compression representation alone is not a conflict. Compare expanded canonical assets and exact canonical inputs; return the existing durable representation on an equivalent insert. Use a nested transaction/savepoint for an insert conflict, reload and compare after `IntegrityError`; do not roll back or commit unrelated caller work. Digest lookup rehashes rather than trusting DB metadata. No round-column migration and no pruning. Enforce referenced-height availability for nonflat artifacts in the service because the height table's existing keys/policies differ from this new content address.

- [ ] **Step 4: Run approved SQLite tests.** Rehearse Alembic upgrade on an approved disposable Postgres database, inspect uniqueness/schema head and preservation of existing rows. Rehearse downgrade only on that disposable database, document that dropping this archive destroys verification and is unsuitable after live round references exist. Never run against friends/demo databases as part of this test.
- [ ] **Step 5: Commit.** Use the actual selected migration filename.

```powershell
git add webapp/app/models/replay.py webapp/app/models/__init__.py webapp/app/replays/map_feature_artifacts.py webapp/app/services/control_feature_artifacts.py webapp/alembic/versions/0020_control_feature_artifacts.py webapp/tests/replays/test_control_feature_artifacts_db.py webapp/tests/replays/test_control_store.py
git commit -m "feat: retain immutable per-map feature artifacts"
```

## Task 5: Prepare artifacts in isolated children and resolve misses before dispatch

**Files:** Create `webapp/app/control/feature_job.py`, `tests/replays/test_feature_job.py`; extend `app/services/control_feature_artifacts.py`, `test_control_feature_artifacts_db.py`, `test_control_isolation.py:27-83`; modify `features.py` to alias the stdlib registry/constants.

**Interfaces:** Produce `FeaturePreparationPending`, `run_feature_child` and `prepare_artifact`. Child entry point `python -m app.control.feature_job` reads one bounded JSON request from stdin and writes one JSON response to stdout. Heavy imports occur inside the selected child mode. Diagnostics/verify modes are completed in their owning tasks; unsupported/unknown modes fail explicitly meanwhile.

- [ ] **Step 1: Add failing service/child/isolation tests.** Assert a cold key compiles once, persists and reuses after process restart; exact-key hits are rehashed; simultaneous same-key preparations converge; compiler failure/timeout leaves no successful cache entry; same-key differences in expanded canonical content raise integrity failure; harmless differences in gzip encoding do not; tag/height changes use new keys. Stub child responses in parent tests and compare real web-child/worker-child outputs later. Extend AST/runtime isolation checks to both new replay stdlib files, the artifact/verification services and the new worker paths.

```python
def test_child_failure_does_not_become_no_features(db, monkeypatch, inputs):
    def fail(mode, payload):
        raise RuntimeError('compiler unavailable')
    monkeypatch.setattr('app.services.control_feature_artifacts.run_feature_child', fail)
    with pytest.raises(FeaturePreparationPending):
        prepare_artifact(db, inputs, None)
    assert find_artifact(db, inputs.key) is None
```

Define `inputs` from `source_case` against the flat geometry in the local fixture. Run `test_feature_job.py`, database and isolation selections; expect missing preparation support.

- [ ] **Step 2: Implement bounded child invocation and cache-before-compile service.** Resolve exact archived heights, never active-height pointer in the child; encode inputs/height bytes in the request. Validate child stdout framing, return status, size and identity; turn source/compile/integrity errors into explicit preparation states. Do not treat a stale last-valid tags snapshot as current after a read error.

```python
# prepare_artifact control flow (caller owns commit):
existing = find_artifact(db, inputs.key)
if existing is not None:
    check_artifact(existing)
    return existing
reply = run_feature_child('compile', compile_request_bytes)
artifact = decode_artifact(reply)
if artifact.key != inputs.key or artifact.inputs != inputs.canonical_inputs:
    raise FeatureArtifactError('child compiled different inputs')
return store_artifact(db, artifact)
```

`compile_request_bytes` is the specified child request encoded with canonical JSON. In the compile mode rebuild base geometry from archived masks, attach request height bytes, compare its digest with the key, invoke `compile_artifact` and emit encoded artifact. Never regenerate visibility or build heights merely to compile features. Introduce bounded process caches with a measured-byte accounting API: initial maximum 16 entries and 64 MiB total per process, evict least recently used, no failure caching; verify correspondence on first process use and rehash bytes on every hit. Disk cache is introduced in Task 6. These conservative defaults are measured in Task 12.

- [ ] **Step 3: Prepare at startup/discovery/version/tag/height change and before dispatch.** Add a preparation pass that computes keys cheaply in parents and queues only misses for isolated compilation; do not put compilation in a page request. Startup and idle discovery call the same helper with a dedicated short-lived preparation transaction per map; commit successful archive insertion before dispatch, and roll back failed preparation without touching height/round transactions. Callers retain old visible rounds while a needed artifact is unavailable. Feature-only failure gets its own retry identity/backoff keyed by `FeatureKey`, independent from height-evidence attempt identity; a pending compilation outcome is a successful artifact, not an infrastructure retry. Task 7 wires the planner to these states; Task 8 wires height/manual transitions.
- [ ] **Step 4: Run the above tests; pass.** Preserve `app.main` isolation against SciPy/control/gaps imports; its existing NumPy import through fight-EV is allowed (B: `test_control_isolation.py:27-83`). Import each new feature service alone and assert it introduces no NumPy/SciPy/Pillow/control imports. Worker server retains the stricter zero-heavy-import boundary. Do not change unrelated analytics. Keep synthetic consumer injection explicit in tests, never production registry mutation.
- [ ] **Step 5: Commit.**

```powershell
git add webapp/app/control/feature_job.py webapp/app/control/features.py webapp/app/services/control_feature_artifacts.py webapp/tests/replays/test_feature_job.py webapp/tests/replays/test_control_feature_artifacts_db.py webapp/tests/replays/test_control_isolation.py
git commit -m "feat: prepare feature artifacts outside server processes"
```

## Task 6: Transport and load exact artifacts on the worker

**Files:** Modify `replay_worker/server.py:904-921,961,1075-1100,1370-1395,1502-1546`, `control_job.py`, `Dockerfile`; `webapp/app/services/replay_control_remote.py:140-175,330-395,447-484`; `app/control/task.py:66-189,366-411`, `app/replays/map_feature_artifacts.py`; `scripts/compute_control.py`; `tests/replays/test_replay_worker_control.py`, `test_control_isolation.py`, `test_control_store.py`, `test_control_remote.py`.

**Interfaces:** Produce `ControlClient.push_features`, `NeedsFeatures`, `load_task_features`. `POST /features` accepts the JSON object returned by `json.loads(encode_artifact(artifact))`; inner binary fields use base64 and wire version is 1. Bound the complete HTTP body, not just decoded asset bytes. Missing exact artifacts on `/control` return HTTP 409 `{code: "needs_features", digest: <expected>}`. Do not conflate this with `needs_height`.

- [ ] **Step 1: Extend the existing fake worker/HTTP rig with failing protocol tests.** Test cold cache push/retry, warm hit, redeploy eviction, corrupt cache, wrong map/height/context/version, interrupted atomic write, same-map different digests and unexpected feature presence/absence. Missing height/artifact retries must push only the corresponding planned bytes. Remove/corrupt the artifact after `/control` admission but before child loading: both remote collection and local CLI must recover without overwriting an existing successful row or storing an engine failure. Unsupported child semantics yield compatibility waiting at no retry cost. Old worker health has no feature protocol: hold affected jobs without spending retries; untagged jobs continue.

```python
def test_digestless_task_does_not_load_a_warm_feature_artifact(tmp_path):
    geo = geometry_case(flat=True)
    # A valid archive is present, but the task intentionally has no features input.
    inp = identify_features('Summit', None, snapshot_case(), base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    (tmp_path / (artifact.digest + '.json')).write_bytes(encode_artifact(artifact))
    assert load_task_features({'map': 'Summit'}, geo, tmp_path) is None
```


```python
@pytest.mark.parametrize('error, kind, code', [
    (FeatureArtifactMissing('evicted'), 'infra', 'features_missing'),
    (FeatureArtifactCorrupt('bad packed mask'), 'infra', 'features_corrupt'),
    (UnsupportedFeatureCompiler('other compiler'), 'compat', 'features_unsupported'),
])
def test_artifact_failures_are_never_engine_failures(error, kind, code):
    result = feature_failure(error)
    assert result['error_kind'] == kind and result['error_code'] == code
```
Additionally pass a task whose `features` digest differs from its `geometry.features`, then assert failure rather than loading either. Oversized compressed payload and invalid dimensions must fail before a child allocates arrays. Run worker-control/isolation selections; expect missing endpoint/protocol.

- [ ] **Step 2: Implement atomic cache transport and compatibility health.** Parent endpoint validates wire/size/key/part hashes using stdlib codec; child proves correspondence. Cache location is content-addressed by full digest, with metadata for the four-part key; stage in the same directory, flush and `os.replace`, then prune by least-recently-used byte/entry limits. Use the Task 5 initial 16-entry/64-MiB limits until measured. Eviction never removes durable DB artifacts. Corrupt files are quarantined/deleted and reported as recoverable misses, not successful hits.

```python
# Dispatcher: on a specific miss, push the already planned archive, not "latest".
try:
    job = client.submit(task)
except NeedsFeatures as missing:
    if missing.digest != planned.inputs.artifact_digest:
        raise FeatureArtifactError('worker requested an unplanned artifact')
    client.push_features(load_artifact(db, missing.digest))
    job = client.submit(task)
```

Define `NeedsFeatures.__init__(digest: str)` with its public `digest` attribute. Decode this 409 in `ControlClient.submit`; retain existing height cache-miss handling. Bound this immediate retry; repeated corruption becomes an explicit infrastructure failure using existing backoff. The parent's `/health` control object adds `features: {wire: 1, compiler: 2, manifest: 2, normalization: 2, consumers: {}}`; compare all identities before affected dispatch. No retry charge for waiting on compatible worker deployment. Add the stdlib modules to Docker smoke imports and the heavy child entry point to image packaging without adding packages.


Handle post-admission failures explicitly rather than relying on the existing generic exception heuristic (B: `app/control/task.py:405-408`). Add the stdlib failure mapper, catch it before engine errors, and preserve `error_code` through `ControlRunner`, job/public JSON and both collectors:

```python
def feature_failure(error: FeatureArtifactError) -> dict:
    unsupported = isinstance(error, UnsupportedFeatureCompiler)
    missing = isinstance(error, FeatureArtifactMissing)
    return {'status': 'failed', 'error_kind': 'compat' if unsupported else 'infra',
            'error_code': 'features_unsupported' if unsupported else
                          'features_missing' if missing else 'features_corrupt',
            'error': str(error)}

# compute_task catch ordering:
# except FeatureArtifactError as error:
#     result = feature_failure(error)
# except engine_errors as error:
#     existing engine failure path
```

On missing/corrupt errors invalidate only the exact bad cache entry, rehash/repush its durable archive, and retry under existing bounded infrastructure backoff; repeated compilation mismatch remains an explicit infrastructure failure, never a permanent round failure. On compatibility errors wait for a supported worker without charging retries. Before any `store_round`/`store_gaps`, remote and local collectors recognize these error kinds/codes and skip successful/engine-failure persistence. Decoder/load/verify wrappers must translate their low-level JSON/base64/gzip/schema exceptions into these typed feature failures before leaving the feature loader; an incidental `ValueError` must not fall through as engine failure. Do not depend on the Python exception base class or name. The HTTP 409 admission check alone cannot cover this race. Add local collector coverage in `test_control_store.py`/`test_control_remote.py`, and update `scripts/compute_control.py` in this task; Task 7 adds its final freshness/provenance checks.
- [ ] **Step 3: Load base, named height, named artifact, in that order.** New feature-aware task loading bypasses the generation pointer. Require the task feature key/digest to agree with loaded map/height/base-context and archived manifest; check current child semantic support. Use `verify_artifact` on first process use, then immutable byte rehashes on hits. Cache geometry and worker warm scheduling by `(map, height, features)` rather than map or generation filename. Do not apply any stateful engine effects in this release; expose the verified compiled data to future consumers and return its exact digest in `geometry_used`. A digest-less task loads no archive and has no `features` geometry key. Task 11 retires the old loader completely, after all callers use this path.
- [ ] **Step 4: Run worker protocol, child and isolation tests.** Compare canonical artifacts generated by web-child and worker-child test harnesses with the same injected synthetic registry. Registry injection belongs only to tests; requests cannot register arbitrary production consumers.
- [ ] **Step 5: Commit.**

```powershell
git add replay_worker/server.py replay_worker/control_job.py replay_worker/Dockerfile webapp/app/services/replay_control_remote.py webapp/app/control/task.py webapp/app/replays/map_feature_artifacts.py webapp/scripts/compute_control.py webapp/tests/replays/test_replay_worker_control.py webapp/tests/replays/test_control_isolation.py webapp/tests/replays/test_control_store.py webapp/tests/replays/test_control_remote.py
git commit -m "feat: pin worker control to exact feature artifacts"
```

## Task 7: Pin planning, record actual provenance and guard both writers

**Files:** Modify `webapp/app/services/replay_control.py:42-156,194-305`, `replay_control_remote.py:322-366,447-484,558-590`, `replay_control_store.py:31-62`, `replay_gaps.py` (`round_gaps`/`plan_gaps`), `replay_gaps_store.py:38-75`, `control_heights.py` (explicit selection); `app/replays/height_inputs.py` (selection type); `app/control/geometry.py` (explicit flat loading); `app/replays/control_format.py:111-116,250-259`; `app/control/task.py:366-411`, `encode.py:158-174`; `scripts/compute_control.py:147-181,232-236`; tests `test_control_store.py`, `test_control_remote.py`, `test_control_features.py`, `test_control_reference.py`, `test_gaps_store.py`, `test_control_heights_db.py` (gap-writer tests are `test_gaps_store.py`; remote gap cases use `test_control_remote.py` at B).

**Interfaces:** Produce `PinnedInputs`, add `PlannedRound.inputs`, `fingerprint_from_inputs`, `verify_result_inputs`, `StaleControlInputs`, `InvalidControlInputs`. Extend `geometry_inputs(map_name: str, heights: dict | None = None, *, context: CurrentGeometryContext | None = None) -> dict | None`; missing required artifacts must yield an explicit not-ready plan, never a successful base-only input. Extend `store_round(..., require_current: bool = False, planned_inputs: PinnedInputs | None = None) -> str`; both production callers explicitly set `require_current=True` and supply the snapshot. Keep existing untagged test/caller compatibility.


Define the parent-safe lookup and selection interfaces explicitly:

```python
# app/replays/height_inputs.py
@dataclass(frozen=True)
class HeightSelection:
    mode: str  # "asset", "flat", or "legacy_default" (offline existing callers only)
    digest: str | None = None

# app/services/replay_control.py
@dataclass(frozen=True)
class CurrentGeometryContext:
    map_name: str
    state: str  # "ready", "features_pending", "source_error", "no_map"
    height: HeightSelection
    pinned: PinnedInputs | None
```

- `control_heights.select_height(db: Session, map_name: str, committed_digest: str | None) -> HeightSelection`: select a usable active database asset; if height history exists but no usable active row, return explicit flat (manual off, deletion/retirement or unsupported old rules cannot resurrect committed heights). Only an untouched map with no DB history retains the existing committed fallback. Offline legacy callers can explicitly choose `legacy_default`.
- `replay_control.resolve_current_geometry(db: Session, map_name: str) -> CurrentGeometryContext`: read one consistent base/raw-tags snapshot, select height, call `identify_features`, then header-only exact-key lookup. Ready means immutable `PinnedInputs`; missing required artifact/source failure yields an explicit unavailable state. It never invokes a child or writes the DB; batch/page callers memoize per map for that operation only.
- `round_fingerprint(replay, groups, n: int, heights: dict | None = None, *, context: CurrentGeometryContext | None = None) -> str | None`: production callers pass the resolved context, unavailable returns `None`; tagged work cannot silently use the legacy no-context path. Preserve old untagged offline/test callers and the existing fingerprint algorithm.
- `geometry_inputs(map_name: str, heights: dict | None = None, *, context: CurrentGeometryContext | None = None) -> dict | None`: use selected height and ready artifact header from the same context. Flat omits the existing geometry height field and uses `flat` only in the feature key. Extend `load_geometry(name: str, asset_dir: Path = ASSET_DIR, heights: Path | None = None, *, height_mode: str = 'legacy_default') -> Geometry`; `flat` bypasses both explicit/committed height loading, `asset` requires the named supplied path/digest, and `legacy_default` retains ordinary offline behavior. All DB-planned worker/local jobs include `height_mode='asset'` or `'flat'`; cache keys include that exact selection. Never use `None` as both off and fallback.

Name every production freshness caller: `replay_control.plan` and `round_control`, `replay_gaps.round_gaps` and `plan_gaps`, `replay_control_store.store_round`, `replay_gaps_store._stale`, local control/gap planning, and historical verification's separate current-freshness check. They obtain context from their existing DB session, pass it to `round_fingerprint`, and do no page-time compilation. Final writers reload context under map-then-replay locks; do not reuse pre-lock page context.
- [ ] **Step 1: Add failing live race/provenance tests using the existing remote rig and database fixtures.** Assert exact artifacts in task, `geometry_used`, summary and stored round; mutate tags/height/compiler between plan/send, send/collect and collect/store. A job retains planned inputs; a superseded result cannot replace the old row. For local CLI, use the same store helper and checks, including gap-only tasks. Seed a valid gap result, run an older gap-only task, change height/tags and store a newer result, then finish the older task; its write must be skipped and preserve the newer rows. Add committed-height-pointer + explicit off tests in planning/loading/provenance, and page freshness tests for a missing artifact/source error without any subprocess call. Feature preparation pending makes `PlannedRound.computable` false with `reason='features_pending'`; old displayed rows are stale. Editorial/deprecated-selector-only changes between plan and store keep the runtime key and permit the result. Old-worker waiting, no-map and untagged paths preserve existing behavior.

```python
def test_fingerprint_reconstruction_does_not_read_current_figures(monkeypatch):
    envelope = {'control': 8, 'data': 1, 'summary': 1,
                'recipe': 'fixed.c10.f1.a0', 'source': '0' * 64,
                'link': {'slots': [0, 1]}, 'geometry': {'height': 'flat'},
                'figures': '1' * 16}
    first = fingerprint_from_inputs(envelope)
    monkeypatch.setattr(cf, 'figures_hash', lambda: '2' * 16)
    assert fingerprint_from_inputs(envelope) == first
```

The envelope here is a hash-function unit input, not a claimed valid replay link. Replay race tests use the existing `linked` fixture's actual link/source/recipe. Tamper provenance, result geometry, digest, versions, figures, recipe/source/link and fingerprint; reject each. Unexpected presence and absence are both failures. Failed/infra results do not write successful provenance. Run these selections; expect missing pinned snapshots and stale-output rejection.

- [ ] **Step 2: Implement ready-only fingerprinting and immutable dispatch.** Read tags/index/base as one consistent snapshot, select DB height and resolve the exact artifact before fingerprinting. Add a preparation pass to startup/idle dispatcher discovery via Task 5; pages do cheap key/DB lookup only. On preparation/read failure, hold affected rounds and mark old rows stale; no silent use of `_assets`' last-valid snapshot as today's feature identity. Store `PinnedInputs` in each job and pass it through collection; `_send` must not reread current tags/features. For no intended bundle, keep the old dictionary exactly without null-valued feature members.

```python
# Keep the historical fingerprint byte encoding, including its default ensure_ascii.
def fingerprint_from_inputs(envelope: dict) -> str:
    encoded = json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]

# fingerprint() assembles the unchanged eight-key envelope, then delegates.
# Feature preparation is a separate dispatcher pass:
for map_name in discovered_maps:
    inputs = identify_features(map_name, active_height_digest, source_snapshot, base_snapshot)
    if inputs is not None:
        try:
            prepare_artifact(db, inputs, exact_height_bytes)
            db.commit()  # preparation session only; durable before dispatch
        except FeaturePreparationPending:
            db.rollback()
            held_feature_keys.add(inputs.key)
```

The loop's snapshot/height/bytes are each map's consistent values; `held_feature_keys` is dispatcher state governed by existing retry scheduling plus Task 5's per-key feature backoff. Do not hold untagged rounds because another map's preparation failed. Add actual-input envelope to task fields for successful feature-bearing computations. The child validates geometry/figures against what it loaded, constructs provenance `{v: 1, inputs: envelope, fingerprint: fingerprint_from_inputs(envelope)}`, and includes it in the existing summary via an optional `encode_summary` argument. In summaries for no intended feature input, omit the key entirely and preserve gzip bytes.

- [ ] **Step 3: Unify final storage checks and lock order.** `verify_result_inputs` compares returned actual geometry to planned geometry, validates additive provenance/fingerprint/revisions and loads the artifact archive by digest. It raises explicit exceptions for invalid/superseded input rather than returning `None` except the valid no-feature case. Both local and remote paths call it. Inside `store_round`, acquire the existing height map advisory lock **before** replay advisory lock, then reload current active height/key, replay link/source/recipe, figures and deployment identities. Recheck tag snapshot immediately before merging; a changed raw snapshot requires cheap re-identification of its runtime key, not automatic rejection for editorial-only changes. Reject only a changed consumed-input identity or an unreadable current snapshot. Verify referenced artifact exists in the same transaction. No in-lock heavy compilation; unavailable current artifact means skip/queue. Store data, summary and fingerprint atomically. Preserve the already-current row shortcut only after successful input checks. If tags replace just after final check, exact provenance remains valid and the next freshness check marks the row stale.

```python
# Writer control flow under map -> replay locks:
provenance = verify_result_inputs(planned_inputs, result)
if current_geometry != planned_inputs.geometry or current_fingerprint != fingerprint:
    return 'skipped: its inputs changed while computing'
if provenance is not None:
    load_artifact(session, provenance['inputs']['geometry']['features'])
# Existing session.merge(...) + commit stores the exact validated summary bytes.
```

Use `replay_db.advisory_lock` and existing map-lock helper names verified at execution; do not add a second lock implementation. Audit all writers for the same order. Local CLI must use actual DB-selected height for feature identity instead of `geometry_inputs(..., heights=None)`, prepare/load exact archives before compute and pass `planned_inputs`; remove its weaker duplicate feature-only check. Gap keys already include the control fingerprint, so no separate version bump or parser work is needed; this does not replace a guarded write. Local `store_gaps` must always pass `expected_control_fingerprint=p.fingerprint`, including `gaps_only`, and remote `_keep_gaps` must preserve that argument. Extend the gap writer to acquire the map lock before replay lock, resolve current geometry afresh and run `_stale` before deleting/replacing any rows. A missing current artifact makes the result stale/unavailable, never valid. Query map/UUID before locking and recheck them afterward; a re-ingest changing the map must roll back/replan rather than proceed under the wrong map lock. Apply this ordering to both writers.

- [ ] **Step 4: Run store/remote/worker/reference tests.** Compare existing no-feature deterministic data/summary bytes and the recorded legacy snapshot under its original recipe/link/revision/figures context; also compare unchanged-reference and new code under identical current inputs. Do not regenerate fixtures, change global revisions or accept altered no-feature bytes.
- [ ] **Step 5: Commit.**

```powershell
git add webapp/app/services/replay_control.py webapp/app/services/replay_control_remote.py webapp/app/services/replay_control_store.py webapp/app/services/replay_gaps.py webapp/app/services/replay_gaps_store.py webapp/app/services/control_heights.py webapp/app/replays/height_inputs.py webapp/app/control/geometry.py webapp/app/replays/control_format.py webapp/app/control/task.py webapp/app/control/encode.py webapp/scripts/compute_control.py webapp/tests/replays/test_control_store.py webapp/tests/replays/test_control_remote.py webapp/tests/replays/test_control_features.py webapp/tests/replays/test_control_reference.py webapp/tests/replays/test_gaps_store.py webapp/tests/replays/test_control_heights_db.py
git commit -m "feat: persist and validate actual control input provenance"
```

## Task 8: Make rebuild and manual diagnostics authoritative without blocking heights

**Files:** Create `webapp/app/control/feature_diagnostics.py`; modify `app/control/geometry.py` (base-only loader), `app/control/height_job.py:97-134`, `height_verify.py`, `feature_job.py`; `replay_worker/height_job.py:31-65`, `server.py:1185-1250,1555-1564`; `app/services/replay_control_remote.py:174-180`; `app/services/control_heights.py:120-131,226-294`, `replay_heights_remote.py:155-275`, `control_feature_artifacts.py`; tests `test_feature_job.py`, `test_heights_remote.py`, `test_replay_worker_control.py`, `test_control_heights_db.py`, `test_control_features.py`, `test_control_height_job.py` (existing real builder/verifier suite), `test_replay_worker_heights.py` (HTTP protocol).

**Interfaces:** Produce `diagnose_features` and `refresh_feature_report`; complete child `diagnose` mode. Worker build request has an explicit diagnostic tag snapshot and previous placement snapshot outside the height-evidence envelope. Stored height reports distinguish provisional source identity, authoritative source identity and candidate-vs-active status.


Additional explicit interfaces:

- `geometry.load_base_geometry(name: str, asset_dir: Path = ASSET_DIR) -> Geometry`: permanent sight/walk masks and map scale only, no tags/specials/feature or height pointers. Height building/checking does not consume barriers at B either; do not read unrelated optional barrier or tag files in this height-only loader. Ordinary control loading retains its barrier conventions. No feature effects. Height builder/checks do not consume specials at B, and the evidence manifest already names masks/scale/checks/rules only. Use this loader in both `height_job.run` and `height_verify.describe`; ordinary control loading still consumes its exact archived/live specials.
- `ControlClient.open_build(key: str, map_name: str, rounds: int, manifest: dict, *, diagnostic: dict | None = None) -> dict`; mirror optional `diagnostic` in `/heights/build`, `HeightBuilds.open` and stored build record. `HeightBuilds.start` forwards the stored envelope into the child task; it never accepts a fresh snapshot that replaces an admitted one.
- Diagnostic envelope: `{v: 1, map, status: 'ok' | 'error', raw_source_b64, raw_source_sha256, previous}`. For `ok`, child recomputes raw hash, parses its own map view and verifies map/version; `previous` is a bounded saved placement summary (IDs/statuses/physical-ground ranges/height/source identities), not another complete raw file. For `error`, include explicit code/reason/hash when available and no fabricated empty catalogue. If full source bytes exceed existing task/body limits, send an explicit diagnostic-unavailable error envelope, retain authoritative web diagnostics, and do not fail/restart valid height evidence for oversized diagnostics.
- Worker height health advertises `diagnostics_protocol: 1`. For a surviving same-key build, `open/get` return its admitted diagnostic identity, and the child/report preserves it even when web restart submits newer tags. A worker restart that forgets the build follows existing resend behavior; a replacement build records its own new snapshot. Never include diagnostic identity in height input hash/key or force a repeated evidence build just because tags changed.
- Old worker without this optional protocol can still build compatible height evidence; omit the new envelope and record provisional identity/comparison as unavailable. Authoritative web child diagnostics remain required independently. New feature-bearing control jobs still obey Task 6 feature compatibility waiting. Do not mistake optional diagnostic compatibility for feature-task compatibility.
- [ ] **Step 1: Add failing structured-report and race tests.** Assert every annotation is listed, including disabled/unregistered ones; placement status differs from runtime eligibility. Pin required report metadata, counts, exact codes/paths/cells/floor counts, dependency IDs and deterministic ordering. Check newly pending/still pending/recovered and old/new physical-ground ranges/max world shift; no previous snapshot reports comparison unavailable. Test valid height + pending feature; valid height + unreadable tags/diagnostic crash; rejected candidate report; manual old-height activation and flat off; source file bytes never change. Mid-build tag-only edits must update authoritative diagnostics without a second evidence build.

```python
def test_report_includes_disabled_annotation_without_a_false_floor_error():
    report = diagnose_features(geometry_case(), snapshot_case(), consumers={'test': 1})
    entries = {entry['id']: entry for entry in report['features']}
    assert entries['feature-2']['placement'] == 'placeable'
    assert entries['feature-2']['runtime'] == 'disabled'
    assert not any(r['code'] in {'multi_floor', 'missing_floor'}
                   for r in entries['feature-2']['reasons'])
    assert report['counts']['total_tagged'] == 2
```

Use existing height `Rig` for orchestration, but also exercise the real `height_job.run` builder and `height_verify.describe` with malformed and missing tags. Stubbed worker success alone does not prove tag-independent integrity. Run the actual HTTP open/upload/start/resume path with a mid-build tag edit and web restart: provisional raw hash stays original, authoritative raw hash changes, evidence build count stays one. Include old-worker/missing-diagnostic, oversize and invalid-hash cases. For activation-failure cases, assert a separate feature-preparation retry is scheduled, `active` advances, height-evidence attempt count does not increase and affected rounds stay held. Run new report/height tests; expect string-list reports or diagnostic-gate failures.

- [ ] **Step 2: Remove the early tags dependency before implementing diagnostics.** Change the real builder and verifier to `load_base_geometry`; prove the real missing/malformed-tag tests pass without dropping any integrity/policy checks. Then implement one diagnostic compiler path. Use Task 2 placement for all annotations, then Task 3 runtime compilation for intended bundles. Sort IDs/reasons/cells and store complete cell lists; CLI output may truncate display only. Full catalogue digest/raw snapshot SHA/compiler/schema/normalization/new and previous height identities plus snapshot must be explicit. Compare physical world ground using each asset's origin; do not compare node indices as elevations.

```python
old_pending = set(previous['pending_ids']) if previous is not None else None
new_pending = {entry['id'] for entry in entries if entry['placement'] == 'pending'}
comparison = {'available': False, 'reason': 'previous placement unavailable'}
if old_pending is not None:
    comparison = {'available': True,
                  'newly_pending': sorted(new_pending - old_pending),
                  'still_pending': sorted(new_pending & old_pending),
                  'recovered': sorted(old_pending - new_pending)}
```

`previous` is a saved diagnostic snapshot with `pending_ids`, height identity and per-feature physical-ground ranges; `entries` are current complete results. Add range/maximum-world-shift fields for comparable placeable ground-relative entries; otherwise emit an explicit unavailable reason. Authoritative web verification passes a current consistent source snapshot into its isolated child after candidate integrity/policy checks. If tags changed, re-run only diagnostics/compilation; keep worker provisional identity as audit. Do not incorporate diagnostic/source identity into height retry/evidence hash or reject heights for feature pending.

- [ ] **Step 3: Handle diagnostic failure and manual transitions separately.** Replace catch-and-empty-list behavior with `{status: 'error', code: 'source_failure' | 'compile_failure', reason, source_sha256: ...}` using null when source identity cannot be obtained. A valid candidate may activate; feature preparation failure holds affected round jobs and retries independently. Build-report JSON records the authoritative diagnostic result for that build, including an explicit candidate flag for rejected builds. Manual activation/off calls `refresh_feature_report` using current tags and requested old height/flat identity; emit it as a new result/audit log, not a rewrite of historical build diagnostics. Run artifact preparation on activation/off; it must not repeat a height build if compilation fails. Retain E6 checks until Task 11.
- [ ] **Step 4: Run height/diagnostic/remote tests; pass.** Assert unchanged tags bytes for first/next builds and manual transitions. Verify valid-height activation does not depend on diagnostic success.
- [ ] **Step 5: Commit.**

```powershell
git add webapp/app/control/feature_diagnostics.py webapp/app/control/geometry.py webapp/app/control/height_job.py webapp/app/control/height_verify.py webapp/app/control/feature_job.py replay_worker/height_job.py replay_worker/server.py webapp/app/services/replay_control_remote.py webapp/app/services/control_heights.py webapp/app/services/replay_heights_remote.py webapp/app/services/control_feature_artifacts.py webapp/tests/replays/test_feature_job.py webapp/tests/replays/test_heights_remote.py webapp/tests/replays/test_control_heights_db.py webapp/tests/replays/test_control_features.py webapp/tests/replays/test_replay_worker_control.py webapp/tests/replays/test_control_height_job.py webapp/tests/replays/test_replay_worker_heights.py
git commit -m "feat: report feature placement for every height version"
```

## Task 9: Remove floor authoring and keep tagger preview/export consistent

**Files:** Modify `webapp/scripts/control_tagger.py`, `control_tagger_features.js`, `control_tagger_core.js`, `control_tagger.template.html:166-168`; `webapp/tests/replays/test_map_feature_tagger.py:36,86,136,293-334,484-501,897-904`.

**Interfaces:** Preserve existing `TaggerCore.Features` source export/digest/validation APIs. Add `TaggerCore.Features.resolvePlacement(cells: number[], context: object) -> object` returning `{ok, cells, floor_counts, reasons}` with the same placement reason semantics as Task 2. Context contains engine walkability, actual asset floor counts and unresolved cells, or an explicit flat indicator; never infer actual floors from synthetic node counts. Browser preview cannot create a runtime artifact or register a consumer.

- [ ] **Step 1: Add failing Python/Node parity and source round-trip tests.** Use existing `run_node(body, payload)` harness to compare single/multi/zero/unresolved/off-ground/empty cell outcomes and normalization/digests, including whole-float numbers, unknown keys and Unicode names. A floor-selector-only edit must not move runtime projection/digest. Export/import retains historical selectors and unknown fields; new feature/trigger/endpoint creation has no required floor field or floor-picker prompt. Ground/world/all-height preview obeys the same placement gate. Browser still downloads a whole-map export rather than writing the repository.

```python
def test_deprecated_floor_fields_round_trip_without_moving_runtime():
    source = source_case()['map_features']
    source['features'][0]['floors'] = ['floor-old']
    got = run_node('''function run(p) {
      return {runtime: F.runtimeDigest(p), source: p};
    }''', source)
    assert got['runtime'] == ms.runtime_digest(source)
    assert got['source']['features'][0]['floors'] == ['floor-old']
```

Check the existing harness's exposed binding (`F` alias vs `TaggerCore`) when inserting this test; use its actual binding consistently. Add rendered-page tests for removal of floor selectors and obsolete instructions: no “Give each landing its floor” or “Add floor label”, and no list of floors as an unresolved authoring input. Preserve instructions for authored times/height bounds/states/shapes; explain automatic placement/pending in the existing guidance. Run tagger tests; expect obsolete floor UI/normalization failures.

- [ ] **Step 2: Implement source normalization parity and preview resolver.** Remove floor catalogue/picker prompts and ground-binding requirements from current authoring paths, and update the rendered `control_tagger.template.html` instructions in the same commit. Removing JS controls alone leaves contradictory published guidance. Preserve source fields by copying unknown/legacy input rather than deleting it during serialization. Do not remove authored ground-relative offsets or invent dimensions/durations. Carry asset-derived floor counts/unresolved flags in the existing preview data from `control_tagger.py`; flat preview has an explicit discriminator.

```javascript
// Core selection inside Features.resolvePlacement:
const ordered = [...new Set(cells)].sort((a, b) => a - b);
const bad = ordered.filter(cell =>
  !context.walk[cell] || (!context.flat &&
    (context.unresolved[cell] || context.floor_counts[cell] !== 1)));
const ok = ordered.length > 0 && bad.length === 0;
// Return stable reasons for bad cells using the Python codes/counts;
// aggregate feature/trigger/route required cells before drawing active preview.
```

Keep all required state/phase/dependent placements atomic in preview. List pending status with reason/count and retain the source; display names never enter runtime hashes. JavaScript mirrors only deterministic preview/normalization, not a second publishing compiler. Production loading remains the sole Python artifact compiler.
- [ ] **Step 3: Run `& $py -m pytest -p no:cacheprovider -q tests/replays/test_map_feature_tagger.py tests/replays/test_map_feature_inputs.py`; pass.** Check the tagger page manually using existing local tooling if available; no source tags or assets need modification. Do not launch a database-connected site without database-command authorization.
- [ ] **Step 4: Commit.**

```powershell
git add webapp/scripts/control_tagger.py webapp/scripts/control_tagger_features.js webapp/scripts/control_tagger_core.js webapp/scripts/control_tagger.template.html webapp/tests/replays/test_map_feature_tagger.py
git commit -m "feat: author and preview features without floor tagging"
```

## Task 10: Verify stored rounds from archives and distinguish freshness

**Files:** Create `webapp/app/services/control_feature_verification.py`, `tests/replays/test_control_feature_verification.py`; modify `app/control/feature_job.py`, `app/services/control_feature_artifacts.py`, `tests/replays/test_feature_job.py`, `test_control_isolation.py`.

**Interfaces:** Produce `VerificationResult` and `verify_stored_round(db: Session, row: ReplayRoundControl) -> VerificationResult`; complete child `verify` mode. Consume `load_artifact`, archived heights by map/digest, `check_artifact`, `verify_artifact`, summary decoder and `fingerprint_from_inputs`. No round-history table or verification UI is added.

- [ ] **Step 1: Add failing historical verification tests.** Store a feature-bearing H1/T1/A1 round using Task 7 fixtures; activate H2/edit source to T2 and clear process/worker caches. Verify the old round from A1's definitions/base bytes and H1's bytes; it is verifiable and currently stale. Then recompute and confirm the existing row is replaced while A1/H1 archives remain. Exercise missing artifact/height, altered mask/node/bounds/definition/part hash, provenance/fingerprint mismatches and unsupported historical compiler.

```python
def test_unsupported_compiler_is_not_reported_as_reproduced(db, historical_row,
                                                           monkeypatch):
    monkeypatch.setattr('app.services.control_feature_verification.run_feature_child',
                        lambda mode, payload: (_ for _ in ()).throw(
                            UnsupportedFeatureCompiler('recorded compiler unavailable')))
    verdict = verify_stored_round(db, historical_row)
    assert verdict.integrity == 'verified'
    assert verdict.recompilation == 'unsupported'
    assert verdict.freshness == 'stale'
```

Define `historical_row` with the H1/T1/A1 then H2/T2 sequence in the local test fixture, using the existing `linked` replay/factory and real artifact bytes. Separate tests show corrupt bytes remain `failed` even when recompilation is unsupported. Run verification tests; expect missing service or accidental current-tags use.

- [ ] **Step 2: Implement archive-only integrity/correspondence checks.** Decode the additive summary member and validate its internal version, exact expected key set, feature digest and recorded fingerprint. Fetch that artifact and its nonflat archived height, not active assets. Parent rehashes artifact/provenance; child reconstructs permanent masks, scale, specials, legacy context, recorded source and exact height, then recompiles under supported recorded semantics.

```python
provenance = cf.unpack_summary(row.summary)['input_provenance']
envelope = provenance['inputs']
if fingerprint_from_inputs(envelope) != row.fingerprint:
    raise FeatureArtifactError('recorded fingerprint does not match provenance')
artifact = load_artifact(db, envelope['geometry']['features'])
check_artifact(artifact)
# Child verification takes artifact.inputs and archived height bytes only.
# Current source/height are consulted solely by the separate freshness check.
```

Use the actual existing summary decoder name verified at execution (R: `unpack_summary`). Add explicit hash comparisons for archived base descriptors and loaded height digest. Integrity validation does not reject a recorded compiler solely because it cannot run here; heavy recompilation yields `unsupported` for that case. Never dynamically download or execute a code commit from the archive. Return independent freshness computed by the normal current planner; current source/preparation failure yields `unavailable`, not current. For no-provenance legacy rounds, return integrity/recompilation `unavailable` with an explicit legacy-provenance reason; add `unavailable` to both verdict enums and report historical feature verification unavailable rather than inventing a past feature input; existing ordinary current freshness remains available.
- [ ] **Step 3: Run verification/codec/store/isolation tests; pass.** Ensure every diagnostic distinguishes integrity failure, unsupported recompilation and staleness, with stable explicit reasons.
- [ ] **Step 4: Commit.**

```powershell
git add webapp/app/services/control_feature_verification.py webapp/app/control/feature_job.py webapp/app/services/control_feature_artifacts.py webapp/tests/replays/test_control_feature_verification.py webapp/tests/replays/test_feature_job.py webapp/tests/replays/test_control_isolation.py
git commit -m "feat: verify historical feature-bearing control rounds"
```

## Task 11: Retire E6 and generation pointers in one complete replacement

**Files:** Modify `webapp/app/services/control_heights.py:58-71,226-294`, `replay_heights_remote.py:155-185,205-215,232-275`; `scripts/control_heights.py:110-123`, `build_control_geometry.py:100-104,128`; `app/control/geometry.py:381-413`, `features.py:934-1008`, `task.py`; tests `test_control_features.py:27-47,1095-1111`, `test_heights_remote.py:275-280,563`, `test_control_heights_db.py:276`; frozen contract and height-design documents; create `docs/superpowers/map-features-survive-heights/README.md`.

**Interfaces:** Final `activate(db, map_name: str, digest: str) -> str | None`, `deactivate(db, map_name: str) -> bool` have no obsolete generation parameter. `store_build` retains its existing inputs/result contract without `HasGeneration`. `load_geometry` keeps backward-compatible base/height arguments and Task 7 explicit `height_mode`, with no feature pointer lookup; task-specific artifact loading belongs exclusively to Task 6. No production caller may use `active_sha`, `publish_generation` or `load_generation` after this task.

- [ ] **Step 1: Replace obsolete guard tests with failing end-to-end rebuild tests.** Build synthetic tagged Summit at 2 matches, add 5 distinct matching evidence items and build again; source bytes unchanged, new heights active, exact artifact ready, existing rounds stale and new tasks pin the replacement. Introduce multi-floor cell, assert valid height activation + whole bundle pending; remove it in next build, assert recovery without re-tagging. Include evidence-deletion off, manual rollback/off and old publication-during-build race rewritten as tags-changing-during-build. Inject an unexpected legacy pointer during a build: flag conversion/readiness explicitly, never silently load it or freeze otherwise valid height activation.

```python
def test_intended_pending_bundle_still_has_an_artifact_after_rebuild():
    geo = geometry_case(multi=True)
    source = source_case()
    before = canonical_json(source)
    inp = identify_features('Summit', geo.height_sha, snapshot_case(source), base_case(), consumers={'test': 1})
    artifact = compile_artifact(geo, inp, '0' * 40)
    assert artifact.digest and artifact.manifest['active_bundles'] == []
    assert canonical_json(source) == before
```

The full cadence/activation test uses the existing `test_heights_remote.py` `Rig` and real DB/worker stub flow; the unit above pins the important all-pending boundary. Keep old untagged cadence/policy/deletion/retry tests. Run new final tests; expect generation guard/planner skips until removed.

- [ ] **Step 2: Remove all E6 entry points together.** Delete `HasGeneration`, `GENERATION_NOTE`, `_generation_note`, final-store guard, activation/off checks and generation arguments/catches. Delete planner generation skip and `_finish` generation-arrival refusal; update CLI lookups/callers. Maintain existing map locks, height policy/integrity checks, cadence and evidence retry identity. Activation commits valid height first; independent feature preparation may hold rounds but cannot undo activation or repeatedly build its evidence.

```python
# Final planner behavior: tagging is not an exclusion condition.
# Remove only generation tests; preserve all ordinary evidence/rules/cadence checks.
# Final height service activation flow under the existing map lock:
_make_active(db, map_name, row_id)
db.commit()
db.expire_all()
# Caller prepares/refreshes current artifacts and diagnostics independently.
```

`_make_active` is the existing helper in `control_heights.py:214-224`; retain its semantics. Do not hold a DB/map lock while a compiler subprocess runs. A race produces a valid archived artifact for its exact key; next planning uses the current key.

- [ ] **Step 3: Retire the old generation selector/publisher.** Remove runtime `index.json.features_sha` lookup from geometry/planning/task cache, the publisher/loader/active-pointer APIs in `features.py` and geometry builder's `keep_features` carry-forward. Preflight existing static index and working/runtime state before release; unexpected pointers are a conversion-required error for affected feature work, never a second selector. Do not remove ordinary height pointers, source fields or unrelated assets. Search all production references, tests and scripts:

```powershell
rg -n 'HasGeneration|GENERATION_NOTE|_generation_note|active_sha|publish_generation|load_generation|features_sha|keep_features|generation_of' webapp/app webapp/scripts replay_worker webapp/tests
```

Review every remaining hit: historical test fixtures/docs can mention retired names; executable runtime selection and obsolete guard callers cannot. Keep new `features` digest in `geometry_used` and distinct artifact APIs intact.

- [ ] **Step 4: Apply the exact spec section 7 amendment ledger.** Amend contract header and sections 1, 2, 4, 8 with the quoted replacement sentences/field bullets; retain sections 3, 5, 6, 7 and sight/traversal/reducer policies. Add a superseding height-design amendment identifying section 3 generation skip, section 4 generation-arrival refusal, section 5 floor-binding freeze and E6 as retired by this shipped replacement. Preserve auto enablement, cadence, DB heights, gate and deletion-off. Do not amend contracts before the implementation passes its final checks.
- [ ] **Step 5: Write the release runbook with the concrete ordering below.** Record code/main dependency SHA, chosen version constants/migration, private endpoint protocol, measured cache limits, no-tag reference evidence and compatibility checks. Deployment itself requires the owner's approval. One implementation release contains every task; do not deploy partial E6 removal.

1. Obtain approved migration/deployment/database window; confirm friends and demo service settings/data isolation from `webapp/RENDER_DEPLOY.md`.
2. Check generation-pointer/consumer state and preserve archives. Apply the additive table migration; existing rounds do not require a provenance backfill.
3. Deploy compatible worker image first; health advertises exact feature identities. Old-web untagged traffic continues. If old worker persists after web activation, affected tagged rounds wait without retry charges.
4. Deploy complete web replacement, including preparation/provenance/guards and E6 retirement. Keep `REPLAY_HEIGHTS_AUTO` on; no tagging exemption.
5. Run approved isolated/synthetic acceptance first, then observe natural first/5-match rebuild and readiness. Empty production registry remains empty; this release does not activate Summit doors.
6. Confirm untagged fingerprints/bytes unchanged, feature-round provenance where synthetic test consumers are used, old archive verification, stale rows visible while recomputing and dependent gaps refreshed from the new control fingerprint.
7. On failure pause affected derived work using existing operational controls, retain archives/old rows and restore a compatible complete web/worker version. Do not downgrade/drop the archive table or deploy an old pointer-only worker to new feature tasks. Never pretend a base fallback is a tagged result.

- [ ] **Step 6: Run targeted final height/planning/local/remote tests; pass.** Inspect diff for accidental Impact/parser/height-rule/version/tag changes. Confirm no intended features in production still follows byte-identical legacy behavior.
- [ ] **Step 7: Commit.**

```powershell
git add webapp/app/services/control_heights.py webapp/app/services/replay_heights_remote.py webapp/scripts/control_heights.py webapp/scripts/build_control_geometry.py webapp/app/control/geometry.py webapp/app/control/features.py webapp/app/control/task.py webapp/tests/replays/test_control_features.py webapp/tests/replays/test_heights_remote.py webapp/tests/replays/test_control_heights_db.py docs/superpowers/specs/2026-10-04-map-features-contract.md docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md docs/superpowers/map-features-survive-heights/README.md
git commit -m "feat: let tagged maps rebuild heights without frozen generations"
```

## Task 12: Prove release invariants, measure caches and prepare review

**Files:** Create `webapp/tests/replays/test_feature_acceptance.py`; update only `docs/superpowers/map-features-survive-heights/README.md` with actual validation/measurement results. Any code repairs return to their owning task's tests and commit, rather than masking failures here.

**Interfaces:** Consume all finished APIs. Add no production interface, version bump, consumer or dependency.

- [ ] **Step 1: Add cross-process and unchanged-reference acceptance tests.** Test compile/encode/rehash/recompile across fresh children, worker cache loss with database repush and historical H1/T1/A1 verification after H2/T2. Compare no-feature control data/summary/input/fingerprint against unchanged reference code and recorded legacy context. Assert source bytes and production empty registry unchanged. Record cold/warm elapsed time and canonical/compressed bytes with existing pytest `record_property`; no external benchmark package.

```python
def test_artifact_measurements_are_recorded(record_property):
    from time import perf_counter
    geo = geometry_case()
    inp = identify_features('Summit', geo.height_sha, snapshot_case(), base_case(), consumers={'test': 1})
    start = perf_counter()
    artifact = compile_artifact(geo, inp, '0' * 40)
    record_property('warm_compile_seconds', perf_counter() - start)
    record_property('wire_bytes', len(encode_artifact(artifact)))
    record_property('compressed_assets_bytes', len(artifact.assets))
    verify_artifact(artifact, geo)
```

This measures warm direct compilation only and cannot establish worker warm performance. `ControlRunner` starts a fresh subprocess per round at B, so every worker round pays first-process artifact verification/recompilation even when disk bytes/visibility are warm. Measure actual `ControlRunner -> control_job -> compute_task` consecutive-round lifecycle with warm disk cache as well as a persistent local pool; do not change server lifetime as an incidental optimization. Separately measure a fresh real `feature_job` subprocess's cold compile, repeated verified cache hits and peak child memory using existing platform process tooling; include permanent input bytes and height-loading cost, exclude height rebuilding/visibility generation. Measure representative small and larger catalogues/state counts and all-pending artifacts. Synthetic timing is not production sizing; record that limitation. Runtime test registry injection never enters production requests/constants.

- [ ] **Step 2: Run the appropriate complete suites once after targeted checks pass.** Database-related suites run only after approval. From `webapp`:

```powershell
& $py -m pytest -p no:cacheprovider -q tests/replays
& $py -m pytest -p no:cacheprovider -q tests/replays/test_control_reference.py tests/replays/test_control_isolation.py tests/replays/test_map_feature_tagger.py
```

The second command is a named final acceptance check only if the full suite did not already include these tests or if a repair changed their code; otherwise record their full-suite results without repeating. Run the archive golden/correspondence tests in the actual web and worker Python/zlib runtimes; record both versions, compare expanded canonical identity/part bytes and round provenance, and record compressed sizes separately. Equivalent valid gzip streams may differ, so compressed equality is not the identity test. Preserve deterministic local gzip encoding and frozen no-feature summary bytes; never relax geometry/number-content comparisons. Run existing worker Docker smoke/build checks from its documented build context when Docker is available; report unavailable tooling explicitly. Do not broaden into Impact/scoring or site database operations.
- [ ] **Step 3: Record measured budgets and choose final limits.** Document cold/warm times, peak memory, compressed/expanded artifact size, cache hit/miss behavior and DB archive growth per map/version. Compare with the conservative 16-MiB wire/64-MiB expanded/16-entry/64-MiB cache defaults. If representative supported inputs exceed a limit, adjust the named constants and boundary tests before release; if they fit, retain defaults. Do not turn the spec's 10–300 ms and 1–5 s estimates into fabricated measured results or brittle timing assertions. Retention stays indefinite; size findings do not authorize pruning.
- [ ] **Step 4: Review spec coverage and release diff.** Each row below must have passing evidence. Confirm line/type/interface names against final code, final lock order, all pointer consumers retired, no schema/global revision drift, no credentials, tags edits or accidental deployment. Summarize tests/limitations in the runbook and later PR description. Owner approval is required before push/PR/deployment; show the complete reviewable branch first.
- [ ] **Step 5: Commit test/runbook evidence.**

```powershell
git add webapp/tests/replays/test_feature_acceptance.py docs/superpowers/map-features-survive-heights/README.md
git commit -m "test: prove feature rebuild and archive release invariants"
```

## Coverage and completion criteria

| Spec requirement | Owning tasks and required evidence |
| --- | --- |
| Author once per map; no floor tagging; never delete pending tags | 1, 2, 9, 11: normalization/source round-trip, placement, unchanged tag bytes |
| Shared immutable artifacts; retained heights; small round references | 3, 4, 7, 10: complete bytes, same-key conflict, no dangling archive reference, historic reconstruction |
| Four-part key includes relevant base/legacy/versions; editorial/disabled independence | 1, 3: axis/dependency/unknown-key tests, all-pending nonnull/no-intended null |
| Heavy imports isolated; parent-safe preparation and transport | 5, 6, 8, 10: AST/runtime isolation and real-child correspondence |
| Every state/phase/sight/trigger/endpoint/access/potential-ground placement | 2, 9: single bad cell makes owning bundle effect-free; independent bundle remains |
| New height follows current ground/origin/node indices; flat vs unknown distinction | 2, 3, 8: two assets, off/rollback, world-ground report comparison |
| Structured full-catalogue report including disabled/error/candidate/manual cases | 8: exact identities/counts/reasons/cells, explicit unavailable comparisons |
| Worker provisional/current authoritative snapshot; no repeat build after tag-only change/error | 8, 11: race and retry identities, valid height still activates |
| Plan/use/current agreement; both writers; stale display and dependent gaps | 6, 7: immutable job, presence mismatch, map-before-replay locks, actual provenance |
| Cache miss/corruption/concurrency/redeploy recoverable; no filename cache identity | 3–6, 12: limits, atomic writes, conflicts, exact repush and process tests |
| Historical integrity/recompilation vs current freshness | 10: H1/T1/A1 after H2/T2, corrupt/missing/unsupported distinctions |
| Complete E6 and pointer retirement, first2/next5, manual/off/deletion/preemption | 11: full caller audit and existing/new height integration tests |
| No-tag bytes/fingerprints unchanged; feature-only versioning; no consumer wired | 1, 7, 11, 12: frozen fixture/current-reference tests and production registry audit |
| Frozen contract amendment ledger; compatible worker/web shipment | 11, 12: exact ledger, runbook/health protocol, approval gates |

Completion means all targeted/full-suite checks pass or a specific pre-existing/tooling limitation is documented for owner review; every new failure must be fixed. Actual runtime measurements, migration rehearsal and deployment verification are execution work, not claims made by this plan. Nothing in this document authorizes an engine consumer, database execution, push, PR or release.

## Review disposition and reuse boundaries

This revision addresses all nine reported findings at the document/interface level; the tests are execution obligations, not tests run while editing the documents.

| Review finding | Correction and required evidence |
| --- | --- |
| P1 post-admission artifact failure becomes permanent engine failure | Task 6 explicit child/worker/local/remote infrastructure or compatibility outcome; admission-to-execution deletion/corruption tests preserve old successful rows |
| P1 height checks fail before diagnostic error handling | Task 8 tag-independent base loader in both real builder and verifier; malformed/missing-tag tests exercise actual implementations |
| P2 flat restoration loses its own placement domain | Task 2 private bundle candidate domain with exact legacy prevalidation and all-or-nothing publish; retain existing restoration fixture |
| P2 diagnostic request fields lost by transport | Task 8 client/HTTP/open/build-record/start/child changes; same-key web restart retains original snapshot; real HTTP race test |
| P2 gap-only local writer bypasses freshness | Task 7 local expected-control fingerprint and map-before-replay locks; current context in `_stale`; stale gap-only result cannot replace newer rows |
| P2 decimal exponent/Unicode canonical mismatch | Task 1 explicit ASCII typed tag v2 and separately named UTF-8 artifact/context format; shared literal golden vectors |
| P2 raw source hash derived from parsed dict | Task 1 `SourceSnapshot` captured from exact original bytes; whitespace changes raw audit identity only |
| P2 web isolation criterion contradicts existing fight-EV | Task 5 preserves existing app NumPy use, tests new services independently and retains stricter worker isolation |
| P2 template still instructs floor tagging | Task 9 template and rendered-guidance tests in same commit as floor-prompt removal |

The four review questions are explicit contracts/validation work: Task 7 carries asset/flat/default selection and names every current-freshness caller; Task 12 measures fresh-child worker lifecycle and cross-runtime canonical content/compression separately. Integrated B replaces the old “wait for height branch” prerequisite and preserves its later fixes.

Reuse existing tagger, schema, reducer, rasterizer, geometry, compiler, bundle reconciliation and height checks. New work is the archive codec/table/preparation transport and orchestration around those components. Do not build a second compiler/tagger/editor or rebuild permanent geometry for feature-only edits. No runtime verification, migration, benchmark or deployment is claimed by this document revision; obtain the specified approvals and pass its owning tests before execution/release claims.

Document-revision checks: all 29 embedded Python snippets parsed; a stdlib/Node probe confirmed the proposed ASCII typed encoding on 11 exponent/zero/Unicode/key-order/integer-boundary vectors and checked numeric rejection. These checks do not execute application tests or prove runtime integrations. The locally available B index has no committed height/feature pointers; deployment/database state still requires release preflight.
