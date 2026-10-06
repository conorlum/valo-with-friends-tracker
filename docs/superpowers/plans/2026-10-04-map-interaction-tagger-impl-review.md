# Review of the step-level implementation plan (P4)

Reviewer: a fresh subagent with only the documents, the cited code, `CLAUDE.md`, `judgment.md` and the run
register. Findings below in short form, each with its resolution. All are accepted; the impl plan's
"Amendments from review" section carries them into the steps.

| # | Severity | Finding (evidence) | Resolution |
| --- | --- | --- | --- |
| 1 | blocker | "Unchanged vs base" for `geometry_inputs`/fingerprints compares the code with itself (`replay_control.py:56-70`, `control_format.py:67-71`) | New step W4b (before any service edit) records `tests/fixtures/control/map_features/legacy_inputs.json` from the unmodified services: every index.json map's `geometry_inputs` and one fixed fingerprint. W10/W17 assert against the file. |
| 2 | blocker | Varying `walk_px` per state changes node count/indices on height maps (`geometry.py:307, 323-339`) | Contract: base walk = permanent ground ∪ every enabled bundle's `potential_ground`; per-state movement is a node mask over `walk_n` only, never a rebuilt geometry. W8 asserts `geo.n`/`node_cell` identical across fixture states. |
| 3 | should-fix | Height assets have no floor identity (`heights.py:88-93`) | A floor binding is `{z band in map-relative metres, height_sha}` (+ optional cells); resolved per cell to the node whose `node_z` lies in the band; none/several = pending. World bounds convert through the asset origin. |
| 4 | should-fix | Node tests skip silently without node (`test_control_tagger.py:25-26`) | `test_map_feature_tagger.py` fails, not skips, when node is missing. |
| 5 | should-fix | Isolation test only rejects numpy/scipy/PIL (`test_control_isolation.py:37-43`) | W5 adds a test: every import in the two new modules is stdlib or `app.replays`. |
| 6 | should-fix | W17 must keep `replay_control.py` free of `app.control` | Features digest read from index.json via `_assets()`; `"features"` added to `_matches_geometry`'s keys; `replay_gaps.py` untouched (its key derives from the fingerprint). |
| 7 | should-fix | Local store path has no freshness check (`compute_control.py:146-150`) | Compare the consumed manifest only when a result carries `features`; absent key = no verification, so the deployed worker is unaffected. |
| 8 | should-fix | `build_control_geometry` writes to `ASSET_DIR` and drops unknown index keys (`:83-92, 100, 127`) | Publication is a function taking `asset_dir` (tests use tmp); `features*` keys carried forward like `keep_heights`. The run never executes the script. |
| 9 | should-fix | Consumer grep misses `topology.of(...)`, `around`, `edge_out`, `links`, `.specials` reads, `los` in `height_build.py`, `cast` in `gaps/detect.py` | W11 uses an AST walk over `app/control` and `app/gaps` keyed `module:Class.func`. |
| 10 | should-fix | `exportTags` on a touched map adds legacy keys (`control_tagger_core.js:149-153`) | Feature edits get their own dirty flag; `exportCatalogue` = `exportTags` + a `map_features` merge for feature-dirty maps; test that with no feature edits it deep-equals `exportTags`. |
| 11 | should-fix | Geometry-dependent warnings can't live in stdlib `validate` | `validate` is structural; `features.diagnose(geo, mf)` gives the geometry warnings (W6/W8). |
| 12 | should-fix | Reducer clock underspecified | Already so in W3: every event has `t`, `run` owns the clock and merges pending; external completions only inject stale ones; unresolved durations schedule nothing. Documented in the contract. |
| 13 | should-fix | Legacy `specials` not covered; full-suite halves exceed the 600 s foreground cap | Specials stay read-only; `validate` warns when a route duplicates a special. Full suite runs in three foreground parts. |
| n1 | nit | W3 check counts cases | The test parametrises by case name. |
| n2 | nit | W5 validates guards but doesn't depend on W3 | W5 imports the vocabulary from `map_feature_state`. |
| n3 | nit | W7 target points unnamed; `seen_from_with` offsets are new semantics | Eye `node_z + EYE_M`, target `node_z + BODY_M` at `centres[node]` for all three adapters; stated in the contract. |
| n4 | nit | W14 too big for one commit | Split into W14a (wiring, panel, properties, persistence) and W14b (tools, zoom/pan, linking, arrows). |
| n5 | nit | Export lacks image checksum and feature-definition digest | `exportCatalogue` writes `image_sha` and `runtime_digest` into `map_features`. |

One re-check of the two blocker fixes follows (the skill's rule when a review finds blockers); its result is
appended below.

## Re-check

Both blockers resolved; no new blocker. Two should-fixes, applied: (1) `image_sha` and `runtime_digest` live
inside `map_features`, are excluded from the runtime projection (no self-hash), are recomputed by readers
rather than trusted, and a features-only edit never sets the entry-level `image_sha`; (2) the coverage table
names W14a/W14b and W4b. `legacy_inputs.json`'s docstring says it is regenerated deliberately when a re-tag,
height rebuild or revision bump lands, not a permanent golden file.
