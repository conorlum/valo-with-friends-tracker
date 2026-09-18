# Stage 6 — the rehearsal: results

**Run 2026-09-17/18 against `valo_rc3_rehearsal` only. Production was never written to**, and was
re-checked after every phase: `alembic 0007, 3125 matches (max id 3133), gate absent` still holds as of
2026-09-18 06:39:50 UTC.

**Verdict: the forward path works end to end, and the rehearsal earned its keep.** It found three defects,
one of which made the release's two verification commands impossible to run at all. None were in the scoring.

Durations, raw output and the full diagnosis of each finding are in `rc3-release/rehearsal/durations.md`
outside the repository.

---

## What ran

| step | result | time |
|---|---|---|
| activation commit | created off the reviewed tip; `chain surface identical to the freeze (36 files compared)` | — |
| 6.3 PR #67's pages | **pass** | ~18 min |
| 6.4 forward path | **pass**, `verify-live` clean, `problems: []` | export 31 m 39 s, verify-build 16 m 01 s, swap 33 s, verify-live 16 m 43 s |
| 6.4a corpus tests | **13 passed, 0 skipped, 1 failed** — see open item below | 49 m 58 s |
| 6.4b cache agreement | **pass**, 24 player-scopes | 4.4 s |
| 6.5 gate probes | **pass**, all three | 22 s |
| 6.6 concurrency | **defect found**; measurement void, not re-run | — |
| 6.7 rollback + interrupt proofs | **pass** | rollback 17–22 s, full cycle ~20 min |
| 6.8 R2 dry run | **skipped** (owner decision: forward only) | — |
| KR2 re-export after the fix | **pass**, EQUAL to K3 and to KR | 30 m 02 s |

## The chain

Reproduced **six** times now, twice during this rehearsal:

| link | hash |
|---|---|
| K1' / K2' / K3 / K4 | `7e5ff2789ed1ea3a61425e1a6138576bc250cddc5de4e63cb3740a6d9f42f1bb` |
| **KR** (activation checkout, `--active`, pre-fix) | equal |
| **KR2** (same, after the write_gate fix) | equal |

Cohort fingerprint `2c31fbbd…5bdc5f` and load projection `3674f8a0…5a00` equal across KR and KR2 too, so the
fix demonstrably moved no score. `verify-live` reported `read_back_sha256 == artifact_sha256`: the table read
back equals the artifact byte for byte, on 22 compared columns (`leverage_component` and `assists_component`
are never stored — the C2 disposition).

**659,500 rows, `scoring_version` 3 only.** 659,290 existing + **210** for match 3133, unscored since
2026-09-10 and scored here for the first time.

---

## Defects found

### 1. `verify-build` and `verify-live` could not run at all — FIXED (`1894534`)

`write_gate.py`'s `checkout` listener ran `SELECT set_config(...)` on the raw connection; psycopg2 opens a
transaction implicitly on that first statement, so every pooled connection arrived **mid-transaction**. Both
ways of taking a snapshot then fail — `SET TRANSACTION ISOLATION LEVEL` must be a transaction's first
statement, and psycopg2's `set_session()` refuses inside one outright.

Deterministic, and it would have fired on production steps **8.2 and 8.4**. Only these two commands were
affected: ten scripts take that snapshot, but only the swap tool also installs a write identity.

*Fix:* commit in the listener, handing connections over idle. The identity survives — `set_config`'s third
argument is `false`, session scope — and committing is in fact stronger, since a later rollback would
otherwise revert the claim. Validated: 67 tests green, both commands clean, KR2 equal.

This is exactly what the remaining-steps plan predicted could not be proven "until a table exists".

### 2. `test_impact_reconstruction` asserts an identity rc3 cannot satisfy — OPEN

It checks `impact = damage + (econ + time + swing)/3` using the legacy `FACTOR_WEIGHTS`. Measured:

| | breaches |
|---|---|
| on v1 rows (`impact_scores_v1`) | **0** of 659,290 |
| on rc3 rows | 618,393 of 659,500 |

Not a tuning problem: rc3 does not store `leverage_component` or `assists_component`, so **no identity over
stored columns can reconstruct its `impact`**. Its docstring scopes it to a weight-*fitting* workflow
("TASK 0 GATE … Stage A fits FACTOR_WEIGHTS"), which rc3 does not perform.

*Recommended:* scope it to the generation it describes, as `5ec6a93` did for
`test_builder_matches_stored_values`. Also correct 6.4a's stated expectation: **14** tests are collected
after the deselect, not 13.

### 3. 6.6's load generator measured nothing, and the failure read as a pass — FIXED

Python's `print()` writes CRLF on Windows; `read -r path` keeps the `\r`; every URL is malformed; curl
returns `000` in ~17 µs. Observed: **899 samples, every one `000`**, while the server was healthy throughout.

The severity is not the broken command. 6.6's acceptance is "no deadlock, no 5xx, nothing stalled over 5 s",
and a run where nothing connects satisfies all three — the plan's own category of *"a check that would pass
even if the thing it guards were broken"*. 6.6 is also the only step exercising the C1 hazard.

*Fixed two ways:* write LF at the source (`sys.stdout.reconfigure(newline="\n")`) with a CRLF guard, **and**
an analysis block requiring **≥200 2xx responses**, zero `000`, zero 5xx, zero stalls — so "no 5xx" can never
again be reached by "no responses". Demonstrated against the real bad run, which it converts into two STOPs.

---

## Proven this stage, beyond the forward path

- **A lost connection mid-swap commits nothing.** The swap's backend was terminated server-side
  (`pg_terminate_backend`) during the row digest. No new `swap swapped` entry, identical row count,
  **identical live-table oid**. A killed connection leaves **no log entry at all** — the operation log jumps
  straight to the next operation — so the standing rule reads exactly right: the log carries a `swap swapped`
  entry *precisely* when the swap committed, and its absence is the signal. Expect silence, not a failure row.
- **The lock-timeout path returns exit 4 having changed nothing**, and records `swap / lock timeout`.
  **Runbook correction:** 8.4's "a lock did not come free within 5 seconds" invites the operator to expect a
  failure 5 seconds in. Measured, exit 4 arrived **~35 s** in — the swap takes its first two locks and
  completes the ~31 s digest before attempting the live table, and only that acquisition times out. Without
  the wall-clock expectation stated, a correct exit 4 looks like a hang.
- **The rollback path works**, twice, in 17–22 s, renaming constraints both directions and clearing the cache.
- **The gate refuses what it must**, including a real `origin/main` worktree on Python 3.11 — not a synthetic
  trigger test.
- **Cache and scores agree after a full rollback-and-re-swap round trip** (open item 1's second requirement).
- **Side resolution is right**, re-derived independently against 14,407 plant-outcome rounds: 100 % agreement
  in both regulation and overtime, far stronger than the 43/43 overtime sample the docstring cites.

## An invalid attempt, recorded rather than replaced

The first interrupt test was worthless: MSYS bash's `kill -INT` does not reach a native Windows Python
process, so the swap ran to completion (`committed in 34.05 s`) and exit 130 was bash reporting the signal it
*sent*. It also consumed the staged table, so the lock test behind it was refused for an unrelated reason.
Redone with `pg_terminate_backend`. Left in the record because a passing test that tests nothing is the same
defect as finding 3, and deleting the attempt would hide that it happened.

---

## What gates Stage 7

| gate | state |
|---|---|
| open item 1 — cache agreement | **closed**: tool written, 10 tests incl. 2 mutation proofs, clean before and after rollback |
| open item 2 — swap→deploy window | **closed** by owner decision, recorded as 8.4.0 |
| open item 3 — catch-up | **partial**: database boundary captured; browser pagination still to design. Gates **8.10**, not Stage 7 |
| chain surface | **closed**: exception recorded for `write_gate.py` alone |
| 6.4a | **open**: disposition for `test_impact_reconstruction`, and 6.4a's expectation corrected |
| 6.6 | **open**: fixed in the runbook, but the concurrency measurement was never actually taken |

Stage 7 also needs per-step authorization for every production write, and PR #67 must carry the release
commits before 7.3 can check out "the commit PR #67 will merge".
