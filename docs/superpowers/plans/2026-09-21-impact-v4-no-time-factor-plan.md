# Impact version 4 — no time factor, decided rounds pay nothing: implementation plan

**Status:** r5, 2026-09-21. **Approved by review for branch-only implementation** after four external reviews
(R1–R11, S1–S6, T1–T5, U1–U3; §6). This is **not** approval to release: rehearsal and acceptance must still pass.
Nothing is implemented.
`webapp/app/` is untouched, `IMPACT_CALCULATION_VERSION` is 3, and `ACTIVE_MANIFEST` is the rc3 manifest.

**Authority.** The ledger (`../2026-09-07-predeclared-values.md`) fixes what ships and why, in the entries
*2026-09-21 CORRECTION*, *DECLARATION 12* and *RESULT, declaration 12*. This plan covers only how. The release
machinery is rc3's; its runbook (`../impact-rc3/README.md`) and plan (`2026-09-16-rc3-ship-plan-v2.md`) are the
command-level reference. This plan lists the deltas and the new risks. Where it says "as rc3", the rc3 step applies
unchanged **except** for the explicit v4 values given here.

---

## 0. What ships

Two behaviour changes, each behind its own config flag, both defaulting **off**:

| flag | behaviour | measured as |
|---|---|---|
| `enable_decided_only_time` | the time factor is **1.0 for every kill and death**, before and after the plant: no `1 + t/53` ramp and no plant+38..45 override (1.75 kill / 0.50 death). It is **0.0 for every declared decided event**. That covers every way rc3 paid those events (S6, T5): the half-credit when rc3's own exploded/defused conditions hold (`impact.py:290`); the override (1.75 kill / 0.5 death) at exactly plant+45 when there is no `exploded` flag, because rc3's override window includes plant+45 (`:345`); the ramp after plant+45 when there is no `exploded` flag; and flat or ramp credit on post-100s Time Win events, depending on whether the raw `plant_time` is set (rc3 reads raw timestamps, so phantom plants take the ramp). All of these become 0. Trade credit is a share of the trade kill's `K·T` (`impact.py:505`), so a trade after the round is decided credits nothing, with no extra rule. | arm `N` |
| `remove_post_decided_assists` | each assist on a kill made **after the round was decided** is removed from the assists component (D = 100 each) | arm `N+A` |

**"Decided"** means exactly the three cases in declaration 12. The reference is
`scripts/postplant_v4_variants.py::round_decided`:
1. The spike was defused and `kill_time >= defuse_time`.
2. The spike was really planted (`plant_window.effective_plant_time`; phantom plants excluded) and
   `kill_time >= plant + 45`, whatever the `exploded` flag says.
3. There was no real plant, the `outcome` contains `"Time Win"`, and `kill_time > 100`.

**What does not change:**
- Damage: it's a per-round total derived from combat score, with no timestamps, so post-decision damage stays in.
  That includes Valorant's 25 combat-score points per non-damaging assist.
- Econ, `K(s)`, and weights A/B/C/D.
- Pre-plant empirical and Part 4 timing: both stay off. Combining either with `enable_decided_only_time` raises an
  error.

**Evidence** (rc3 ex-ante, 3,198 matches):
- `N vs P0` is an IMPROVEMENT on T2 (−8.99e−05) and on C (−1.11e−02).
- `N+A vs N` is UNTESTABLE (separability 0.0058%, below both floors *as carried over from the legacy runs*). It ships
  on the owner's concept, not on evidence.
- Row motion under rc3 live: **32.1% of rows change and 72.0% of matches reorder**.

---

## 1. Sequencing: one gated release window (decided after review, R2/R3)

**Why the code can't merge ahead of activation.**
- `impact_manifest.current_code_identity()` digests the scoring sources (`HASHED_SOURCES`), so any §2 change makes
  the rc3 manifest fail verification, even with both flags off.
- The rc3 manifest is only re-verified when its path or content changes (`impact_runtime._load_verified` caches on
  the manifest file). So a deploy of the new code with the rc3 manifest fails in a **fresh process**, while a
  long-running process may carry on unchecked. Neither outcome is acceptable. **Every release step requires clean
  process restarts.**

**Rejected: bridging via a re-frozen rc3 (the r1 plan's "Option A").**
- Ingestion requires `gate.release_id == manifest["candidate_id"]` (`ingest_preflight.py:133`), so a bridge manifest
  needs its own gate transition.
- Its approval can't be inherited either. The swap checks the approval's manifest hash, candidate ID and comparator
  (`swap_impact_scores.py:453`), so rc3's review results can't approve a different manifest.
- Equal exports prove equality on one cohort, not flags-off equivalence in general.

A bridge is therefore a full second release. Its only benefit, "new code live while v3 is still active", isn't worth
that cost.

**Adopted: prepare everything offline, then one window.**
1. Implement (§2) and commit. The v4 manifest is frozen at that commit (§3), and the reviews are regenerated for it.
   **Nothing is merged or deployed.** Production runs the rc3 code and the rc3 manifest throughout preparation.
2. Rehearse the entire window on a fresh restore (§4.3).
3. **Window.** The window fixes the **activation cohort** (S4; see "Three cohorts" below). Stop and drain
   ingestion. Check for stranded matches: the match commit and the score commit are
   separate transactions (`trackergg_browserstate_source.py:431`), and a match stranded between them is the known
   shape from the 3133 incident. Then:
   1. close the gate;
   2. on the gated dataset: K4 through the frozen manifest, then K5 from the **activation checkout** (the manifest
      pointer and the version bump, committed but not yet deployed); the hashes and fingerprints must be equal;
   3. build, verify-build (version 4), swap;
   4. deploy that checkout;
   5. restart all processes;
   6. run verify-live (version 4);
   7. apply the cache barriers (§4.4);
   8. **the rollback window opens.** Ordinary rollback is only possible until the next match is ingested
      (`swap_impact_scores.py:725` refuses it after that) (S2). Every doubt is resolved **before step 9 ends**,
      with the gate still closed;
   9. **observation hold: 48 hours with the gate closed** (rc3 D11, carried over). Ordinary rollback stays
      complete throughout. Then take the **catch-up inventory** (rc3 8.10a). **Before reopening, show that the
      catch-up is complete** (U, execution prerequisite). Discovery fetches one history state per act
      (`trackergg_browserstate_source.py:133`), and nothing proves pagination within an act or coverage beyond one
      returned page, so a boundary that can't be reached is recorded as **incomplete**, never assumed covered.
      Then **open the gate explicitly for `impact-v4`**. From this moment ordinary rollback is gone, and problems are fixed forward (§4.5);
   10. test that a stale rc3 checkout is refused, and that v4 ingestion works.

**Three cohorts, never conflated** (S4):

| cohort | what it is | fixed when | used for |
|---|---|---|---|
| **measurement** | the 3,198 local match ids declaration 12 was measured on, plus their input fingerprints | now (ledger, declaration 13) | the §2.6 equivalence against the independent reference |
| **review** | rc3's ten plus 3104/3120/3133 and the three rule-exercising matches | at freeze (`--matches`) | review results, which must cover exactly this set |
| **activation** | every match in production at the moment the gate closes | window step 1 | K4, K5, build, verify-build and swap. These must cover every current match (`swap_impact_scores.py:538`, `:573`), which is why they can only run once ingestion is frozen |

Exports taken during preparation, on a production snapshot, are **rehearsal-grade**: they catch defects but prove
nothing about the activation cohort. Only the in-window K4 = K5 binds.

**Duration, two separate figures** (T4):
- **hands-on activation:** drain → exports → build → verify → swap → deploy → verify-live → caches → acceptance.
  The exports alone take about 30 minutes each (rc3 runbook §0) and verify-build about 15. The operational estimate
  is whatever the rehearsal measures, not this sum. The machine must not sleep.
- **total ingestion freeze:** hands-on activation **plus the 48-hour hold**. Friends' matches played during that
  time are ingested afterwards through the catch-up inventory. tracker.gg history pages hold 20 matches, so check
  the inventory covers the gap.

**Identities at each stage.** This table must be filled in before rehearsal.

| stage | checkout | `ACTIVE_MANIFEST` | `candidate_id` | gate state / `release_id` | admin identity |
|---|---|---|---|---|---|
| preparation | rc3 (main) | rc3 | impact-rc3 | open / impact-rc3 | — |
| window, pre-swap | activation commit, **not deployed** | v4 | impact-v4 | **closed** | `v4-runbook` |
| window, post-deploy | activation commit | v4 | impact-v4 | closed | `v4-runbook` |
| after acceptance | activation commit | v4 | impact-v4 | **open / impact-v4** | — |
| rollback (only before the gate reopens) | **the exact pre-v4 application revision** (recorded SHA of production's deployed main) | rc3 (original manifest) | impact-rc3 | **stays closed** until validation and an explicit recovery decision | `v4-runbook`, run from the separate release-tools checkout |

---

## 2. Implementation (a fresh branch; test-first, each test shown to fail before its code)

Anything that scores uses Python 3.13 (`.venv313`), as in the rc3 runbook §0.

### 2.1 `app/scoring/plant_window.py`
- Add `ROUND_SECONDS = 100.0` and `SPIKE_SECONDS = 45.0`. The latter is defined separately in the variants module
  (R11), so it must be ported too.
- Port `round_decided(round_row, kill_time) -> bool` with its docstring.
- Tests:
  - the defuse boundary;
  - the plant+45 boundary, with and without the `exploded` flag;
  - a phantom plant with a kill at 101 (decided by rule 3);
  - an **Elimination Win with `plant_time > 100` and a kill at 103: not decided**;
  - an unplanted Elimination Win with a kill at 100.5: not decided;
  - a surrendered round.

### 2.2 `app/scoring/impact_config.py`
- Add `enable_decided_only_time` and `remove_post_decided_assists`, both `False`, and pass both through
  `build_kwargs()`.
- Raise if `enable_decided_only_time` is combined with either legacy timing flag.
- `remove_post_decided_assists` is allowed on its own.

### 2.3 `app/scoring/impact.py`
- Add both flags as kwargs of `build_impact_rows_for_match`.
- Add `_time_factor(..., decided_only=False)`. When `True` it returns `0.0 if round_decided(...) else 1.0` **before
  every other branch**, including the legacy exploded/defused early return of `0.5`. Wire it into both scorer call
  sites (kill `:1280`, death `:1314`). **Self-kills** (S5): the self-kill's **kill** credit stays zero, as the
  caller already does at `:1280`. Its **death** contribution follows the measured timing rule. The death path at
  `:1314` still calls `_time_factor`, and `_traded_factor` returns 1 for a self-kill (`:459`), so a self-kill or
  environmental death costs `K·1` before the round is decided and `0` after, exactly as arm `N` scored it. Add tests
  for a self-kill and an environmental death on both sides of the decided boundary.
- **The two callers outside the scorer keep their legacy semantics** (R8):
  - `app/services/kill_order_leverage.py:214,221` reconstructs legacy ex-ante components for the evaluation and
    refit tooling (`kill_order_curves`, `kill_order_refit`). No route reaches it. Following the runtime flag there
    would mix v4 timing into a legacy decomposition. **Leave it explicitly legacy**, and add a comment plus a test
    that pins it.
  - `postplant_factor.py:373` (Part 4 shim, dormant) is unchanged.
- **Assists:**
  - Build `{(round_number, match_player_id): n}` from `kill_events.source_meta["assistants"]` for decided kills.
  - Map names to this match's players by case-insensitive `Player.display_name`, as measured.
  - Subtract **`min(n, stat["assists"])`**. This clamp is a **deliberate defensive extension** beyond the measured
    reference (R10); on the corpus it never fires (0 rows). It is declared and tested as such.
  - Report unmapped, ambiguous and clamped cases through the existing observer hooks.
  - Leave `stat["assists"]` and damage untouched.
  - Demo-pipeline matches have no `assistants`, so nothing is removed.
- **Persisted semantics** (R5.6 confirmed): no scoring-column migration; 0010 already supplies `scoring_version`.
  - `time_impact` stays unweighted net `K·T` including trade credit, now with T ∈ {0, 1}.
  - `post_plant_kill` / `post_plant_death` stay weighted sums. Match pages sum them (`services/matches.py:140`).
  - Record this in the model docstring.
- Tests:
  - flags-off rows bit-identical on the fixture matches;
  - T at pre-plant, plant+10, and plant+39 (kill **and** death);
  - T after a defuse;
  - zero trade credit for a decided trade kill;
  - the assists clamp;
  - unmapped and ambiguous names;
  - demo matches;
  - incompatible flag combinations raising.

### 2.4 `app/scoring/impact_manifest.py`
- **Serialisation (R5.2): emit the new keys only when True.** `config_from_dict` reads them with
  `.get(key, False)`. This keeps rc3's frozen comparator dict identical. Because `impact_v4` is declared in
  `COMPARATORS` with both flags True, omitting either key fails `verify_manifest`'s exact comparison. Add omission
  tests for both keys.
  - Known limit: this protection covers declared comparators only, not arbitrary undeclared names.
- Add `V4 = "impact_v4"`: rc3 plus both flags.
- Add the diagnostic comparator `impact_v4_n`: rc3 plus the decided-time flag only, so the review can show the split.
- **Generate the manifest's description from the selected flags** (R11). `impact_manifest.py:294` would otherwise
  freeze the text "unchanged legacy time factor".
- **Provenance (R1, BLOCKER):**
  - Extend `_FINGERPRINT_QUERIES`:
    - `events` gains `k.source_meta`, or at minimum a canonical projection of the `assistants` payload;
    - a new `players` query covers each match player's `player_id` and `display_name`.
  - Add `app/models/player.py` to `HASHED_SOURCES`.
  - This is a **new fingerprint contract**:
    - declare it in the ledger;
    - bump whatever version names the contract;
    - **never rewrite old frozen evidence** — rc3's manifest and artifacts stay as they are.
  - Add a mutation test for each newly consumed input: an assistant payload edit, a display-name change, and a
    match-player remap. Each must change the fingerprint.

### 2.5 Release write protection (R1)
- `kill_events` is **already** fully covered: the swap digests hash whole rows, including `source_meta`
  (`swap_impact_scores.py:205`), and the table is already locked and gated (S6). The gap for assistant metadata is
  only in the export/review fingerprint (§2.4).
- `players` is the real gap: `swap_impact_scores.py:92` excludes it from the source-table digests and locks, and
  `scripts/sql/release_write_gate.sql:109` doesn't gate it. Add `players` to:
  - the source digests;
  - the lock set;
  - the write gate.
- Otherwise a rename between export and swap passes every check.
- Add a test for each.

### 2.6 The equivalence proof (R4, S3): an independent reference, not a self-comparison
Both the variant wrapper and the `N+A` hook capture whatever scorer the **current checkout** imports
(`postplant_v4_decl12.py:130`, `postplant_v4_variants.py:43`). Run on the implementation branch, both sides of a
wrapper-vs-flags comparison would execute the modified scorer. An unintended shared change to damage, econ, kill
ordering or aggregation would then match on both sides and reproduce nothing. So the reference has to come from code
that has **not** been changed.

1. **Reference artifacts from the measurement commit.**
   - Check out `f96aee9`, where `git diff origin/main -- webapp/app` is **empty**: the scorer is exactly production
     rc3.
   - Add a row-dump mode to `postplant_v4_decl12.py` there, as a **scripts-only** commit. Assert that
     `git diff f96aee9 -- webapp/app` stays empty.
   - Load rc3's configuration from the **original** rc3 manifest file as historical data, not through
     `active_scoring_config()`. The old manifest is loaded, not treated as verifying new code.
   - Produce row artifacts for `P0`, `N` and `N+A` on the measurement cohort, under **both ex-ante and realized**
     scoring.
   - Record the artifact hashes in declaration 13. Store the artifacts outside the repository, alongside the
     release artifacts.
2. **Compare the implementation against those artifacts**, never against a wrapper running on the new code:
   - the flags-off new code against reference `P0`, and `impact_v4_n` / `impact_v4` against reference `N` / `N+A`;
   - both modes;
   - **row by key**, every scoring field plus the diagnostic columns;
   - `scoring_version` checked separately, as the existing comparison projection does.
3. **Keep the full-cohort flags-off rc3 comparison.** r2 dropped it. The new code with both flags off must equal rc3
   at `origin/main` on the **whole** measurement cohort, not only the review matches.
4. Econ differs legitimately between ex-ante and realized. The comparison is always within one mode.
5. Any difference stops the release until it is explained and eliminated.

### 2.7 Tests that encode "rc3 is the newest formula" (R9)
- `tests/test_impact_reconstruction.py:67` takes the active manifest's activation version as "the first non-legacy
  version". Under v4 that classifies v3 rows as legacy.
- Fix: identify legacy rows by their actual formula generation, independently of the active version.
- Grep for other tests that read `active_manifest()` or `IMPACT_CALCULATION_VERSION` as a boundary.

### 2.8 Full suite
- Run offline on 3.11 and 3.13, then the DB tests on the scratch test database.
- **Compare named failures, and do not grandfather environmental errors into acceptance** (R9). rc3's runbook
  records clean, isolated suites, and v4 is held to the same standard.

---

## 3. Freeze and review (rc3 Stage 5, deltas)

1. **Declaration 13 (release) in the ledger, before any freeze.** It holds:
   - the pinned cohort;
   - the new fingerprint contract;
   - the §2.6 equivalence (both modes);
   - the reference-artifact hashes (§2.6);
   - the three cohorts (§1), each stated exactly;
   - K-chain expectations:
     - rehearsal-grade: K3 (the v4 comparator at the implementation commit) equals K4 (through the frozen
       manifest) on a preparation snapshot;
     - **binding, in the window, on the activation cohort**: K4 equals K5 (from the activation checkout).
       Ingestion is closed for both exports, so nothing arrives between them.
   - production row motion, with a declared tolerance around 32.1% of rows and 72.0% reordered;
   - the wording "below both carried floors" — not "below any plausible rc3 floor" (R5.7).
2. **Fix the complete review cohort before freezing** (R5). The freeze CLI requires `--matches`, and acceptance
   requires review results covering exactly that set. The cohort is:
   - rc3's ten plus 3104, 3120 and 3133;
   - **one match where a post-decision assist is demonstrably removed**, chosen by query and recorded;
   - one match with a post-defuse kill;
   - one Time Win round with a post-100s kill.
3. Run `freeze_impact_candidate.py --candidate-id impact-v4 --release-comparator impact_v4 --activation-version 4
   --matches <cohort>` from a clean, committed tree, writing to `docs/superpowers/impact-v4/candidate-manifest.json`.
   Commit the manifest alone.
4. **Adapt the independent decomposition check** (R5). `compare_rc3_decomposition.py:181` hard-selects `RC3`, and
   line 152 checks D × *raw* assists. Parameterise it on the manifest's release comparator, and check D × **eligible**
   assists when `remove_post_decided_assists` is set. Borrowing the rc3 command unchanged would silently review rc3.
5. Generate `review-results.json` covering exactly the frozen cohort.
6. Owner's last look: 72% of matches reorder. Show a site comparison for several friend-group matches, as in rc3's
   `SUMMARY.md`.

---

## 4. Rehearsal and activation (rc3 Stages 6 and 8, deltas)

### 4.1 Parameterise the swap tool (R6), for any future release
`swap_impact_scores.py` hardcodes the following, and v4 needs its own values:

| hardcoded | v4 value / change |
|---|---|
| `PREVIOUS = "impact_scores_v1"` | `impact_scores_v3`. The tool already **refuses** when this name is occupied (`:640`). With `impact_scores_v1` retained until 2026-10-03, an unmodified v4 swap is refused. |
| `ROLLED_BACK = "impact_scores_rc3_rolled_back"` | a v4 name |
| `--expect-scoring-version` default 3 | required and explicit. It is used by **verify-build and verify-live**, not by `swap`. |
| `--identity` default `rc3-runbook` | `v4-runbook` |

- Propagate the validated names through swap, rollback, `state`, owned-object and index renaming, and the operation
  records. **No rc3 defaults.**
- Validate and quote SQL identifiers, and reject conflicting names.
- **Rehearse with `impact_scores_v1` present.**
- Parameterise now. **Do not drop v1 early** to suit the tool; its presence in production and its retention date
  are to be verified, not assumed.

### 4.2 Storage (R11)
A rename swap doesn't hold four copies at once. Budget the actual live, staged and retained sizes, plus indexes and
build/WAL headroom, and a rehearsal restore if it's on the same instance. Measure production capacity before the
build; it has not been verified.

### 4.3 Rehearsal (all timed), in this order (S2)
On a fresh restore, `valo_v4_rehearsal`:
1. drain;
2. check for stranded matches;
3. close the gate, then **capture the stored v3 rows** (§4.5). A successful capture, with its hash recorded, is a
   **prerequisite for the swap** (U2). The swap replaces the live table (`swap_impact_scores.py:630`), so a capture
   taken afterwards would record v4;
4. K4, then K5, which must be equal;
5. build, verify-build (version 4), swap, deploy the activation checkout locally, restart, verify-live (version 4);
6. cache barriers, prewarm, coverage, numeric agreement.
7. **Rollback while the gate is still closed**, with §4.5's verification in full, against the step 3 capture:
   - roll back;
   - deploy the **exact pre-v4 revision**, and restart;
   - the original rc3 manifest must pass;
   - the capture hash must be equal;
   - replay the review cohort under rc3;
   - cache `DELETE`, prewarm, and 6.4b agreement.

   Then **re-swap to v4** and repeat steps 5–6.
8. An interrupted swap.
9. Open the gate for `impact-v4`, and run the rc3-rejection probe.

On a **second, separate restore** (`valo_v4_rehearsal_b`), because it consumes the rollback precondition:
10. perform steps 1–6. Then **open the gate for `impact-v4`**, ingest one match, and verify it committed with its
    scores. The gate is closed after steps 1–6, and ingestion refuses a closed gate
    (`ingest_preflight.py:128`) (U1). Then **close the gate again**;
11. attempt `rollback`, and **assert it refuses specifically because matches changed**: the `matches were ingested
    after the swap` refusal (`swap_impact_scores.py:725`), not merely a nonzero exit. This demonstrates the D11
    boundary: past this point the policy is fix-forward, and there is no recovery procedure to rehearse.

### 4.4 Cache barriers (R7)
- `player_view_cache` validity keys on code-version constants, not table contents (`player_view_cache.py:100`).
- `swap` clears `player_view_cache` only (`swap_impact_scores.py:280`).
- Keep every rc3 barrier explicitly (`impact-rc3/README.md:750`):
  - capture the prewarm ids before the swap;
  - drain processes;
  - **`DELETE` the cache after deploy, and again after any revert**;
  - prewarm;
  - check coverage **and numeric agreement between cache and table**;
  - **stop background prewarm before a rollback**. Otherwise a v3 process can cache v4 values under a v3 cache
    version, and they survive the rollback.
- `site_stats_cache` (v19) is **not** cleared by the swap, and **needs no v4 bump**. Its products read match and
  round data, not Impact (`services/site_stats.py:181`). This is confirmed by review; note it and move on.

### 4.5 Rollback (S1, S2)

**Reverting the activation commit alone cannot restore rc3.** The implementation changes several hashed source files
and adds `player.py` to `HASHED_SOURCES`. With only the manifest pointer and version reverted, the original rc3
manifest fails source-digest verification (`impact_manifest.py:332`), and every restarted process fails. So the
rollback **deployment** is the **exact pre-v4 application revision**: production's deployed `main` SHA, recorded
before the window. It is not a revert commit. The **database** rollback runs from a separate checkout of the
release-tools commit that holds the parameterised swap tool, which the pre-v4 revision lacks.

**Validity window.** Ordinary rollback is valid **only while no match has been ingested since the swap**. The swap
tool enforces this (`:725`). Closing the gate again does **not** restore that precondition. So the window is the
48-hour closed-gate hold (§1 step 9): rc3's D11, carried over unchanged.

**The pre-swap capture of stored v3 rows** (T3). Historical rc3 release artifacts don't cover today's activation
cohort, and they use the old fingerprint contract, so they can't verify a restored table. Instead, in the window,
after the gate closes and **before the swap**:
- export the live `impact_scores` as a canonical file: every persisted column, ordered by key, with `scoring_version`
  included, using `COPY (SELECT ... ORDER BY round_id, match_player_id)`;
- record its sha256, its row count and the activation cohort's source fingerprints.

After a rollback, the same export of the restored table must hash **equal**.

The separate release-tools checkout (S1) uses the **new** fingerprint contract. The rollback's own source re-digest
compares against digests that the same tools recorded at swap time, so the two are consistent. Historical rc3
approvals are never rewritten.

**Ordinary rollback** (before the gate reopens):
1. The gate is already closed; keep it closed.
2. Stop background prewarm.
3. `rollback` to `impact_scores_v3`, with explicit names, from the release-tools checkout.
4. Deploy the recorded pre-v4 revision, and restart all processes.
5. Verify:
   - `state` shows `rollback rolled back`. The rollback's own source re-digest and retained-table oid check have
     passed. If it exits 3 on source drift, follow rc3 R1.2 and find out what changed first. Use
     `--accept-source-drift` (`swap_impact_scores.py:743`) only if **the currently live v4 scores are the
     emergency** (U3; rc3's wording named the table being abandoned, which here is v4). The tool records the
     accepted mismatch in the log, and ingestion stays closed until it is resolved;
   - the restored table's canonical export hashes equal to the pre-swap capture;
   - the original rc3 manifest verifies under the pre-v4 revision;
   - a replay of the review cohort under rc3 matches the restored rows;
   - the cache `DELETE`, then prewarm from the **reverted** checkout, then the 6.4b cache/table agreement (rc3 R1.4).
6. The gate **stays closed** until those checks pass **and** the owner makes an explicit decision to reopen it for
   `impact-rc3`.

**After the gate has reopened: fix forward only** (rc3 D11, T1/T2). There is no rollback to rc3 once v4 has scored a
new match. A problem is fixed as a v4.1 through this same release path. The r3 "capture-then-restore" idea is
**dropped**:
- no command sequence for it exists (`:727` refuses whatever has been added to the retained table, and
  `compute_impact_for_match` writes only to the live table);
- restoring only the post-swap matches would also bring back stale scores for any older match corrected during the
  hold.

If the owner ever wants recovery to rc3 after reopening, it is a new operation to design and review on its own:
build a fresh rc3 replacement table over a newly frozen cohort. It is not part of this release.

### 4.6 Retention
Keep `impact_scores_v3` for about two weeks after activation. Drop `impact_scores_v1` on its own schedule,
independently.

---

## 5. Analysis hygiene after activation
- `impact_eval.load_stored_observations` doesn't filter by `scoring_version`. Any analysis of stored rows must
  assert and report a homogeneous version (R5.6).
- The v4 evaluation harness must pass an explicit configuration, never rely on the implicit one. This is the defect
  the 2026-09-21 CORRECTION recorded.

---

## 6. Review log

**Review 1** (2026-09-21): external, read-only; no production access. All findings were verified against the code
before this revision.

| # | severity | finding | disposition |
|---|---|---|---|
| R1 | BLOCKER | assistant metadata was missing from the **export/review fingerprints**. `players` was also missing from the swap's source digests, the locks and the gate. `kill_events`, including `source_meta`, was already hashed, locked and gated (corrected per S6). | §2.4, §2.5 |
| R2 | BLOCKER | the gate's `release_id` must equal `candidate_id`, so a bridge manifest breaks ingestion; closing the gate mid-ingest can strand a match | §1: one gated window, drain first |
| R3 | MAJOR | equal exports don't carry rc3's approval over to a re-frozen manifest | §1: bridge rejected |
| R4 | MAJOR | the equivalence proof needs a pinned reference, both scoring modes, and a fixed cohort | §2.6, §3.1 |
| R5 | MAJOR | the rc3 decomposition check hard-selects RC3; the review cohort must be fixed before freezing | §3.2, §3.4 |
| R6 | MAJOR | swap parameterisation was incomplete, and the version flag was attached to the wrong step | §4.1 |
| R7 | MAJOR | the cache barriers after deploy and on rollback were not carried over | §4.4, §4.5 |
| R8 | MAJOR | `kill_order_leverage` was misclassified as site-facing | §2.3: stays legacy (r1 was wrong) |
| R9 | MAJOR | a reconstruction test treats the active version as the boundary; the baseline must not be grandfathered | §2.7, §2.8 |
| R10 | MINOR | the assist clamp goes beyond the measured reference | §2.3: declared as a defensive extension |
| R11 | MINOR | `SPIKE_SECONDS` port; frozen description text; runtime verification is cached; storage arithmetic | §2.1, §2.4, §1, §4.2 |

**Review 2** (2026-09-21, r2): the same constraints. Every finding was verified against the code.

| # | severity | finding | disposition |
|---|---|---|---|
| S1 | BLOCKER | reverting only the activation commit leaves source files changed, so the original rc3 manifest can't verify | §4.5: roll back to the exact pre-v4 revision; the DB rollback runs from a separate tools checkout |
| S2 | MAJOR | rollback is refused once a match is ingested, but it was scheduled after reopening, in both the rehearsal and the runbook | §1 step 8 is the decision point; §4.3 is reordered with a second restore for the post-ingest case; §4.5 defines the validity window and the post-reopen recovery |
| S3 | MAJOR | both sides of the equivalence ran the modified scorer; the full-cohort rc3 comparison was dropped | §2.6: reference artifacts from `f96aee9` (app/ identical to origin/main); the full-cohort flags-off comparison is restored |
| S4 | MAJOR | the activation cohort wasn't operationally fixed; verification needs every current match | §1: three cohorts; K4 = K5 binds in the window, on the gated dataset |
| S5 | MINOR | "self-kills zeroed by the caller" is true on the kill side only | §2.3 reworded, with tests |
| S6 | MINOR | §0 understated what becomes 0; R1's log misstated what was missing | §0 and the R1 row corrected |

R-series status after r3: R2, R4 and R7 were partial in r2. They are now addressed through S1–S4.

**Review 3** (2026-09-21, r3): the same constraints. Every finding was verified; rc3's D11 was checked in
`2026-09-16-rc3-ship-plan-v2.md:31`.

| # | severity | finding | disposition |
|---|---|---|---|
| T1 | BLOCKER | the post-reopen capture-then-restore has no executable command sequence | §4.5: dropped. **Fix-forward after reopening**, per rc3's existing D11 ("rollback expires when ingestion reopens … after that, problems are fixed forward"). Recovery to rc3 after reopening would be a separately designed operation. |
| T2 | MAJOR | restoring only the post-swap matches brings back stale scores for corrected older matches | resolved by T1; recorded as the reason in §4.5 |
| T3 | MAJOR | "verify-live (version 3)" needs artifacts that nothing produces | §4.5: a pre-swap canonical capture of stored v3 rows, hash-equal after rollback, plus rc3's R1 checks; the tools checkout's fingerprint contract is stated |
| T4 | MINOR | "about two hours" ignored the 48-hour hold | §1: hands-on time and total freeze are stated separately; catch-up inventory kept |
| T5 | MINOR | the legacy-timing description missed boundary cases | §0 reworded |

**Review 4** (2026-09-21, r4): "ready for branch-only implementation; no remaining structural blocker".

| # | severity | finding | disposition |
|---|---|---|---|
| U1 | MINOR | the rehearsal's ingestion probe started with the gate closed, so it could not commit | §4.3 step 10: open → ingest → verify → close; step 11 asserts the specific "matches changed" refusal |
| U2 | MINOR | the pre-swap capture appeared after the swap in the checklist | §4.3 step 3: capture after closing the gate, as a prerequisite for the swap |
| U3 | MINOR | the `--accept-source-drift` rationale named rc3's emergency, not v4's | §4.5 reworded |
| — | prerequisite | catch-up completeness must be shown before reopening; an unreachable boundary stays "incomplete" | §1 step 9 |

**r3 was wrong on two points:**
- It proposed a recovery that could not be executed.
- It invented a rollback verification step. rc3's own R1 never ran `verify-live` at the old version.

**The r1 plan was wrong on three points:**
- It said the swap would "collide" with `impact_scores_v1`. In fact it refuses to run.
- It called `kill_order_leverage` site-facing. It isn't.
- It claimed "verified on every ingestion". Verification is cached per manifest.
