# Bonus-Round Denial Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: Tasks 1-6 implemented 2026-09-12; Task 7 (refreeze + re-review) in progress.** Owner decisions: Task 5 -- a survivor of a round their team lost always banks 1,000 (verified exact at team level on the raw captures once spike-detonation deaths, which tracker's deaths stat omits, are counted as deaths); Task 6 -- an in-round pickup requires that the survivor's paid kit could not cover the picked-up gun plus every gun they had already fired (e.g. Vandal + Sheriff = 3,700).

**Goal:** Fix the seven verified defects found in the code review of `f28afe5..6e67c4d` (the round 2/14 bonus-denial model), then refreeze the candidate and re-run its declared review.

**Architecture:** The fixes fall into three groups.
- Reporting and tooling fixes that change no score: Tasks 1-4. `compare_econ_models.py`, the trace renderer, the adapter guard, the manifest description, activation docs.
- Two scoring-rule corrections that need an owner decision first: Tasks 5-6. The survive-loss reward in credit recovery; the kill-feed "own weapon" rule.
- One refreeze-and-review pass at the end: Task 7. Tasks 2, 3, 5 and 6 edit hashed scoring sources, which invalidates manifest `2a5d247c...`.

**Tech Stack:** Python 3.13, SQLAlchemy, pytest (sqlite fixtures), local Postgres 18 read-only.

**Spec:** `docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md`; original plan `docs/superpowers/plans/2026-09-12-econ-bonus-round-denial.md`.

## Global Constraints

- **Frozen folders stay untouched.** Nothing in `docs/superpowers/econ-buy-disruption-candidate/` (rc2) or in the current `docs/superpowers/econ-bonus-denial-candidate/` artifacts is edited in place. New results go in new files, and ledger changes are appended amendments; a recorded value is never edited.
- **Nothing goes live.** `ACTIVE_MANIFEST` stays None; `IMPACT_CALCULATION_VERSION` stays 2; no rescore, backfill or deploy.
- **The owner decides the scoring rules.** Tasks 5 and 6 change scoring rules and do not start until the owner has chosen the option in their "Decision" block. The choice is declared in the ledger before any measurement that could be tuned to.
- **Test first.** Each test fails on a wrong VALUE before the fix.
- **Read-only database work** (`REPEATABLE READ, READ ONLY`, rollback). Scripts print player ids, not handles, into committed docs.
- **Known red:** `test_impact_exante_swing::test_builder_matches_stored_values` and `test_site_stats_cache::test_happy_path_blob_validates`.

---

## Findings this plan fixes (verified)

| # | severity | finding | evidence |
|---|---|---|---|
| F1 | medium | Kill-feed pickup rule reads "sidearm kill, then own SMG/rifle kill" as picking up a dead teammate's gun (the spec defines "own weapon" as the most recent kill weapon) | review fork example: Sheriff kill then own Vandal after a Vandal teammate dies credits 2,100; earlier probe's 5 of 5 sample in-round signatures had a sidearm as the "own" weapon (Ghost/Sheriff -> Spectre/Stinger/Guardian) |
| F2 | medium | Credit recovery gives a survivor of a LOST round the full team loss reward (1,900) as cash | 95 pistol winners survived a lost round 2/14: 5% land near 1,900, 11% near 1,000, median residual 400; `impact.py:159` documents `SURVIVE_LOSS_BONUS = 1000`. Overstated cash hides recovery (only 95 survivors corpus-wide) |
| F3 | medium | Frozen traces explain a pistol winner's round 2/14 deaths with the 30/80 budget ("severity pool 258.97 -> CONSTRAINED next buy") and never show the bonus audit (denied / net / recovery) | `trace-3120-site.md` round 2; renderer `release_candidate_review.py:800-808`, `828-833` |
| F4 | low | `compare_econ_models.py` counts abstaining rounds in `scored_rounds`, diluting per-round averages; the declared flag (a) was evaluated on diluted means | `model-comparison.json`: round 1 3,124 "scored" at 0.0, round 13 3,088, round 24 778; 67 of 6,183 rounds 2/14 abstained (final_round 52, surrender 15) |
| F5 | low | Frozen manifest `formula_changes_vs_live_legacy` describes only 30/80; the bonus rule is absent | `build_manifest` hard-codes the list (`impact_manifest.py`) |
| F6 | low | Candidate not activation-ready: no README runbook, no `review-results.json` for `backfill_impact_candidate.py --approved-results`; nothing records that rc2 can now only be activated from `f28afe5` | folder listing; `backfill_impact_candidate.py:268`; rc2 manifest fails verification at HEAD |
| F7 | low | Adapter inserts `spentCredits` into a NOT NULL column without a None check; a null would abort the whole ingest | adapter `pending_spend` block; 0 nulls in 1,780 captured values, so defensive only |

Checked and **not** defects: calculator branch order and parity (fork + 0 parity mismatches), reward streak halftime reset, weapon fingerprinting, migration revision chain, 102 new/touched tests. The fork's "103.6 vs the spec's 158 pts/round" comparison is withdrawn: 158 was the swing-only term at C=3.871, 103.6 is gross at C=1 (401 at C=3.871, matching `by_round.json`).

Known limitation, **not planned** (owner design choice): credit evidence also counts a teammate's buy-phase gun DROP in round N+1 as a pickup.

---

### Task 1: Count only scored rounds in the comparison (F4)

**Files:** Modify `webapp/scripts/compare_econ_models.py` (the `for rn, kw in new["econ"].items()` loop, ~line 88); Test `webapp/tests/test_compare_econ_models.py`.

- [ ] **Step 1: Failing test**
```python
def test_abstaining_rounds_are_not_counted_as_scored():
    db = session()
    build_match(db, "c3", kills={2: [("B1", "A1", 10.0)]}, weapons={2: ["Spectre"]},
                loadouts={2: {"A1": 2600}}, count_stats=True)
    report = cmp.compare(db, _manifest(), min_matches=1)
    assert "1" not in report["by_round_number"]          # the pistol round always abstains
    assert report["by_round_number"]["2"]["scored_rounds"] == 1
    assert report["abstained_rounds_by_number"]["1"] == 1
```
Run `.\.venv\Scripts\python.exe -m pytest tests/test_compare_econ_models.py -q`. Expected: FAIL (`"1"` present; KeyError on `abstained_rounds_by_number`).

- [ ] **Step 2: Fix.** Count per model only when that model scored, and report abstentions separately:
```python
    abstained_by_rn = defaultdict(int)
    ...
        for rn, kw in new["econ"].items():
            n_res, o_res = kw["result"], old["econ"][rn]["result"]
            if n_res.abstention:
                abstained_by_rn[rn] += 1
            else:
                by_rn[rn]["rounds"] += 1
                by_rn[rn]["old"] += _gross_points(o_res, scale_c)
                by_rn[rn]["new"] += _gross_points(n_res, scale_c)
```
Add `"abstained_rounds_by_number": {str(k): v for k, v in sorted(abstained_by_rn.items())}` to the returned dict. The existing `if v["rounds"]` filter then drops rounds that never scored. (30/80 abstains wherever the bonus model abstains outside half-round 2, and inside it the bonus model is stricter, so both columns share one denominator.)

- [ ] **Step 3:** Run the test file. Expected: PASS. Commit "Count only scored rounds in the econ model comparison".

### Task 2: Render the bonus audit in traces and reviews (F3)

**Files:** Modify `webapp/scripts/release_candidate_review.py:800-808` and `:828-833`; Test `webapp/tests/test_release_candidate_review_frozen.py`.

- [ ] **Step 1: Failing test**
```python
def test_trace_explains_bonus_denial_rounds_with_the_bonus_audit():
    db = session()
    match, players, _ = build_match(db, "tb", kills={2: [("B1", "A1", 10.0)]}, weapons={2: ["Spectre"]},
                                    loadouts={2: {"A1": 2600}}, outcomes={2: "Team B Elimination Win"},
                                    count_stats=True)
    manifest = build_manifest(candidate_id="t", created="2026-09-12", scorer_revision="t",
                              activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                              source_snapshots={"matches": {}}, release_comparator=bd.MODEL_V2_30_80_BONUS_DENIAL)
    rec = review.Reconciler()
    text = review.render_frozen_trace(db, match.id, manifest, rec)
    round_two = text.split("## Round 2")[1].split("## Round 3")[0]
    assert "BONUS-ROUND DENIAL" in round_two
    assert "denied 2,050" in round_two and "net 2,050" in round_two
    assert "severity pool" not in round_two.split("**TEAM_2**")[0]   # the pistol winner's line
    assert "(80%, denial)" in round_two
    assert rec.failures == []
```
Expected: FAIL (the old verdict text is rendered).

- [ ] **Step 2: Fix.** In the team loop, branch on `audit.bonus` before the budget line:
```python
                if audit.bonus is not None:
                    bo = audit.bonus
                    L.append(f"**{str(team)[5:]}** BONUS-ROUND DENIAL (pistol winner {'won' if bo.won else 'lost'} "
                             f"this round, factor {bo.factor}): denied {sum(bo.denied.values()):,.0f}, "
                             f"recovered {bo.team_recovered:,.0f}, net {sum(bo.net_denied.values()):,.0f}.")
                    for s in bo.survivors:
                        if s.recovery:
                            L.append(f"- {who(s.match_player_id)} recovered {s.recovery:,.0f} "
                                     f"(credit {s.credit_recovery:,.0f}; kill feed {s.feed_recovery:,.0f}"
                                     f"{' ' + s.feed_inference + ' ' + str(s.feed_weapon) if s.feed_inference else ''}; inferred)")
                else:
                    b = audit.budget
                    verdict = (...)   # unchanged 30/80 text
                    L.append(...)     # unchanged budget line
```
In the event cells, label qualifying events: `state = "denial" if getattr(econ_event, "bonus_qualifying", False) else ("absorbed" if econ_event.absorbed else "constrained")`. Extend the round-intro prose (lines 781-783) with one sentence describing the round 2/14 denial when `right_cfg.econ_model == bd.MODEL_V2_30_80_BONUS_DENIAL`.

- [ ] **Step 3:** Run `tests/test_release_candidate_review_frozen.py`. Expected: PASS, including the existing 30/80 trace tests (their text is unchanged). Commit.

### Task 3: Refuse a null spend value without aborting ingestion (F7)

**Files:** Modify `webapp/app/adapters/trackergg_browserstate_source.py` (the `if "spentCredits" in stats:` line); Test `webapp/tests/test_round_player_spend_ingest.py`.

- [ ] **Step 1: Failing test**
```python
def test_null_spent_credits_write_no_row_and_do_not_abort_ingest():
    db = session(all_tables=True)
    payload = _payload(spent_for=lambda r, i: 5)
    for s in payload["segments"]:
        if s["type"] == "player-round" and s["attributes"]["platformUserIdentifier"] == IDS[0]:
            s["stats"]["spentCredits"] = {"value": None}
    load_match(db, payload)
    assert db.query(RoundPlayerSpend).count() == 18
```
Expected: FAIL (IntegrityError on the NOT NULL column).

- [ ] **Step 2: Fix.**
```python
        spent = (stats.get("spentCredits") or {}).get("value")
        if isinstance(spent, int) and not isinstance(spent, bool):
            pending_spend.append((stat_row, spent))
```
- [ ] **Step 3:** Run the spend tests. Expected: PASS. Commit.

### Task 4: Describe the bonus rule in the manifest; activation docs (F5, F6)

**Files:** Modify `webapp/app/scoring/impact_manifest.py` (`build_manifest`); Test `webapp/tests/test_impact_manifest.py`; Create at refreeze time (Task 7): `docs/superpowers/econ-bonus-denial-candidate-rc2/README.md`.

- [ ] **Step 1: Failing test**
```python
def test_bonus_release_manifest_describes_the_bonus_rule():
    manifest = _manifest(release_comparator=impact_manifest.V2_30_80_BONUS)
    text = " ".join(manifest["formula_changes_vs_live_legacy"])
    assert "round 2/14 bonus-round denial" in text
    assert "round 2/14" not in " ".join(_manifest()["formula_changes_vs_live_legacy"])
```
Expected: FAIL.

- [ ] **Step 2: Fix.** In `build_manifest`, append when `release_comparator == V2_30_80_BONUS`:
```python
    changes = [...existing list...]
    if release_comparator == V2_30_80_BONUS:
        changes.insert(2, "economy: round 2/14 bonus-round denial -- a pistol winner's first death with paid kit "
                          "net of agent utility above 1,500 pays V*1.10*net denied/19,500 (V 0.8 won / 1.0 lost) "
                          "and 80% of that as the victim's debit, net of inferred pickups; spec 2026-09-12")
```
- [ ] **Step 3:** Run the manifest tests. Expected: PASS. Commit.
- [ ] **Step 4 (docs, done in Task 7):**
  - The new candidate folder gets a `README.md` adapted from rc2's: review commands with `--compare site` only, the comparison script replacing `--corpus`, the backfill command pointing at the new manifest and the new `review-results.json`, and rollback.
  - A "rc2 is no longer activatable at HEAD; activate it only from a checkout of `f28afe5`" note goes in the new README and in the ledger amendment. rc2's folder is not edited.

### Task 5: Survivors of a lost round get the survive-loss reward, not the loss bonus (F2)

**Decision (owner, before any code):**
- A. Use Valorant's survive-loss reward of 1,000 credits, which matches `impact.py`'s `SURVIVE_LOSS_BONUS` (recommended, pending the source check in Step 1).
- B. Use 0: a survivor of a lost round banks no reward.
- C. Leave the credit rule as specced and record this as a known limitation (no code change; skip to Task 6).

- [ ] **Step 1: Confirm the rule before declaring it.**
  - Cite a current Valorant economy source for the survive-loss reward.
  - Re-run the scratchpad probe `survive_loss_probe.py` restricted to survivors whose round-N+1 `spentCredits` is known (8 captures), where spend removes utility noise.
  - Declare the chosen value in a ledger amendment before Task 7 measures anything.
- [ ] **Step 2: Failing test** (append to `tests/test_econ_bonus_denial.py`):
```python
def test_survivor_of_a_lost_round_banks_the_survive_loss_reward_not_the_loss_bonus():
    result = score(outcome=WIN_B, events=[ev(1, 10.0, 6, 1)], reward={A: 1900, B: 3000},
                   player_kw=dict(deaths={1: 1}))
    s2 = next(s for s in result.teams[A].bonus.survivors if s.match_player_id == 2)
    assert s2.cash == 1000 + bd.SURVIVED_LOSS_REWARD       # remaining 1000, no kills, no plant
```
Expected: FAIL (AttributeError -> add `SURVIVED_LOSS_REWARD = -1` first; then cash 2900 != 2000).
- [ ] **Step 3: Fix.**
  - Add `SURVIVED_LOSS_REWARD = 1000.0` (the declared value) to the bonus constants and to `CALCULATOR_CONSTANT_NAMES`.
  - In `_survivor_recovery`, take `won` from the caller (`_bonus_denial` passes `won`), and use `reward = inputs.next_round_reward[team] if won else SURVIVED_LOSS_REWARD`. Only survivors reach this function, so dead players are unaffected.
  - Update the spec section 4.1 formula as a dated amendment line.
- [ ] **Step 4:** Run the bonus calculator suite plus the Abyss parity. Expected: PASS. Commit.

### Task 6: Stop reading a sidearm-to-own-gun switch as a pickup (F1)

**Decision (owner, before any code):**
- A. **Could-have-bought test, mirroring the carried rule** (recommended). An in-round pickup of gun G by survivor S counts only when S's round-N paid loadout is below `price(G)`, so S could not have bought G themselves.
- B. **Own weapon = the most expensive priced weapon S has killed with so far that round**, not the most recent. A sidearm kill no longer masks an owned primary, but a real pickup by a survivor who only ever fired a sidearm is still detected.
- C. **Ignore sidearm-to-primary switches entirely:** own weapons that are sidearms never qualify. Simplest, and misses genuine eco pickups.

- [ ] **Step 1: Size it (read-only, before deciding).** Over rounds 2/14, count the in-round kill-feed inferences whose own weapon is a sidearm, and how many of those have `paid_N >= price(G)`. This means extending `compare_econ_models.py` or a scratch script to print the survivors' `own_weapon` / `feed_weapon` / `paid`. Report it to the owner with the decision.
- [ ] **Step 2: Failing test** (for option A):
```python
def test_in_round_pickup_requires_that_the_survivor_could_not_have_bought_it():
    events = [ev(1, 5.0, 1, 7, "Vandal"), ev(2, 6.0, 2, 8, "Sheriff"), ev(3, 10.0, 6, 1, "Vandal"),
              ev(4, 20.0, 2, 9, "Vandal")]
    owned = score(events=events, player_kw=dict(deaths={1: 1, 7: 1, 8: 1, 9: 1}, loadout={2: 3900}))
    assert next(s for s in owned.teams[A].bonus.survivors if s.match_player_id == 2).feed_recovery == 0
    picked = score(events=events, player_kw=dict(deaths={1: 1, 7: 1, 8: 1, 9: 1}, loadout={2: 1200}))
    assert next(s for s in picked.teams[A].bonus.survivors if s.match_player_id == 2).feed_recovery == 2100
```
Expected: FAIL on `owned` (2,100 != 0).
- [ ] **Step 3: Fix (option A).** Pass `paid[pid]` into `_feed_recovery` and add `and paid_s < prices[gun]` to the in-round condition. Amend spec section 4.2 (a) with a dated line.
- [ ] **Step 4:** Run the bonus suite. Expected: PASS (re-check `test_in_round_feed_pickup_nets_the_upgrade`: survivor 2's paid is 3,900 >= Spectre 1,600, so that fixture needs `loadout={2: 1500}` -- **ask the owner before changing an existing assertion's fixture**, per the plan-execution feedback memory). Commit.

### Task 7: Refreeze, declare, re-run every check

- [ ] **Step 1:** Full suite: only the two known-red tests fail.
- [ ] **Step 2: Ledger amendment, appended before scoring.** It records:
  - the defects and their fixes
  - the owner's Task 5/6 decisions
  - flag (a) re-evaluated on undiluted means
  - the rc2-activatable-only-from-`f28afe5` note
  - the same five checks as the original declaration, plus mutations for Tasks 1-6
- [ ] **Step 3: Freeze `impact-bonus-denial-rc2`** into a NEW folder `docs/superpowers/econ-bonus-denial-candidate-rc2/`, with the scratchpad freeze script retargeted. Commit the manifest and the amendment together.
- [ ] **Step 4: Re-run everything:**
  - `compare_econ_models.py` (Task 1 counts)
  - rc2 parity (30/80 audit identical)
  - site reviews for 3104, the ten, and 3120 with trace (Task 2 rendering)
  - `--ten --extra 3104 --extra 3120 --results review-results.json` (F6)
  - the defect harness with added mutations: survive-loss reward ignored, could-have-bought test removed, trace falls back to the 30/80 verdict, comparison counts abstentions, null spend inserted
- [ ] **Step 5:** New `SUMMARY.md` and `README.md` in the rc2 folder; ledger RESULT entry; commit. Report to the owner what moved relative to `impact-bonus-denial-rc1`.
