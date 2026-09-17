# Session handoff — 2026-09-16, resume point

Written at the owner's request so the session can be closed overnight. Read this first, then
`HANDOFF-rc3-ship-to-site.md` (same directory), which is the durable task brief and is still accurate.

> **Latest (2026-09-16, late night): Stages 1–3 are committed on `impact-scoring-impl` (not pushed; HEAD at or after
> 4e2aa23). Read plan v2 section 7 (progress, results, deviations) and the runbook
> `docs/superpowers/impact-rc3/README.md`, which holds every command from here to activation and rollback.**
> - K2' = K1' (the declared prediction held after exact summation).
> - Next: K3 and OFF(B) at commit B (runbook section 2), the Stage 4 RESULT, then the rehearsal restore and Stage 5.
> - Production is unchanged. Point-in-time recovery is still pending the owner's Render check.
>
> **Earlier (2026-09-16, later): the plan to follow is `docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md`,
> now at revision 2** after a second external review (`2026-09-16-rc3-plan-astra-review-2.md`). Revision 2 replaced the
> row-value gate with a release write gate, and truncate-and-insert with build-and-rename. Owner decisions are in its
> section 0. The rc3 ledger entry is appended to the ledger, uncommitted, pending owner review. Point-in-time recovery
> is pending the owner's Render check and gates Stage 7.
> Next step: the owner reviews the ledger entry → commit it alone → plan v2 Stage 2.
>
> **Update, 2026-09-16 (resumed session): suggested first moves 1 and 2 are DONE — do not redo them.**
> - The credit-ON corpus run completed (all 3,125 matches, 0 failures). Its results, and the rc3 plan the relaunched
>   planning pass produced (checked against code, git and the live DB, with four corrections), are in
>   `docs/superpowers/plans/2026-09-16-rc3-freeze-and-ship.md`. Read that next.
> - Open decision 2 (the ~210-row gap) is resolved: it is exactly match 3133, which has no `impact_scores` rows.
> - Do NOT run refreshes from this branch against production: they crash after committing cache deletes (see the
>   plan's "Other facts" section). The partial-data warning below about `bc25on\` is obsolete — it is now complete.
> - The plan's local-mirror steps cannot work on this machine's PostgreSQL install (no `share\`); see its correction 1.

## Where things stand right now

**Owner decision, freshly made and NOT yet measured or frozen:** the scoring is locked at

| weight | value |
|---|---|
| A `damage` | 1.0 (locked; everything scales around it) |
| B `leverage` | 2.5 |
| C `econ` | 2.5 |
| D `assists` | 100.0 |
| `trade_credit_scale` | 1.0 |
| `enable_trade_credit` | **True** — trade credit ships ON |

plus `enable_econ_component=True`, `econ_model="buy_disruption_v2_30_80_bonus_denial"`,
`use_realized_swing=True`, timing candidates off.

The trade-credit-ON decision was made late in the session and **superseded** a whole afternoon of analysis that
had been run with it OFF. That is the main thing to be careful about tomorrow: most numbers in the transcript and
in `HANDOFF-rc3-ship-to-site.md` are credit-OFF baselines.

### Git

Branch `impact-scoring-impl`, HEAD `7f4a63b`, **nothing committed this session**. Working tree:

```
 M webapp/app/scoring/impact.py
 M webapp/app/scoring/impact_manifest.py
 M webapp/tests/test_impact_trade_credit.py
?? docs/superpowers/HANDOFF-rc3-ship-to-site.md
?? docs/superpowers/HANDOFF-2026-09-16-resume.md   (this file)
```

The modified files add `FormulaWeights.trade_credit_scale` (default 1.0, multiplies the credit before B),
serialise it in `impact_manifest.config_to_dict`, and add two value-level tests. It is a verified no-op at
scale 1.0. `tests/test_impact_trade_credit.py` passes 42/42 including the two new tests.

PR #67 is still open, still the whole 108-commit branch, still unmerged.

## What I stopped, and what to do about it

### 1. Planning subagent — STOPPED, produced nothing usable

A `Plan` subagent was given `HANDOFF-rc3-ship-to-site.md` and asked to produce the rc3 freeze-and-deploy plan.
It was mid-flight when the owner asked to shut down, and **it was killed before delivering a plan**. Its last
reported activity was "Two final checks — the cache-clearing trap and its scope", so it was close to finishing,
but **no plan text was ever returned and none should be assumed**. It had already received the
trade-credit-ON correction, so a fresh run does not need to re-send that.

**To resume:** relaunch a `Plan` subagent against `HANDOFF-rc3-ship-to-site.md`. That file has already been
updated for trade credit ON, so it can be handed over as-is with no correction message. Its "cache-clearing
trap" hint is worth chasing independently: bumping `IMPACT_CALCULATION_VERSION` 2 → 3 invalidates
`player_view_cache` (4,479 rows today), and `PLAYER_VIEW_CACHE_SCHEMA_VERSION` /
`fight_ev.CALCULATION_VERSION` form a composite version — see `docs/player_page_precompute.txt`.

### 2. Corpus re-measurement with credit ON — STOPPED at 2000/3125 matches

Killed roughly 5 minutes short of finishing. It checkpoints every 500 matches, so
`C:\Users\Public\Documents\Wondershare\CreatorTemp\bc25on\` holds a **consistent but partial** result covering
matches 1..~2000 by ascending id. **Do not quote it as a corpus figure** — it is the oldest ~64% of matches, not
a random sample, and the ledger RESULT entry needs the full corpus.

**To resume — one unattended command, ~17 minutes:**

```bash
cd "C:/Users/Conor Lum/Documents/GitHub/valo-with-friends-tracker/webapp"
PYTHONIOENCODING=utf-8 DATABASE_URL=$(grep DATABASE_URL .env.remote | cut -d= -f2-) \
  PYTHONPATH=. .venv/Scripts/python.exe /tmp/bc25/run_on.py
```

Then report it with `/tmp/bc25/report.py` (edit its `"bc25"` to `"bc25on"`, or copy it). The comparison script
`/tmp/bc25/compare.py` can rescale stored weighted values to other B/C without rescoring — that trick
reproduced the ledger's recorded B=3/C=2.347 figures exactly, so it is trustworthy for share comparisons.

## Analysis artifacts on disk (this machine only — not in git)

`C:\Users\Public\Documents\Wondershare\CreatorTemp\` — note this is where `tempfile.gettempdir()` resolves
(Wondershare hijacked `TEMP`); it is **not** the same place Bash's `/tmp` maps to.

| path | what |
|---|---|
| `bc25\perf.json` | 31,250 player-matches, credit **OFF**, complete (7.7 MB) |
| `bc25\rounds.json` | 659,500 player-rounds, credit OFF, complete (14.7 MB) |
| `bc25on\perf.json`, `rounds.json` | credit **ON**, **PARTIAL — ~2000/3125 matches** |

Scratch scripts live at `C:\Users\CONORL~1\AppData\Local\Temp\bc25\` (Bash `/tmp/bc25/`):
`run.py` (credit off), `run_on.py` (credit on), `report.py`, `compare.py`, `sanity.py`, `verify.py`,
`short.py`, `assists.py`.

**Gotcha that cost 14 minutes today:** native Windows Python reads `/tmp/x` as `C:\tmp\x`, which does not exist,
while Bash maps `/tmp` to the user's AppData Temp. Always use `tempfile.gettempdir()` (or a real Windows path)
for Python file I/O, and write a probe file *before* a long run.

## Verified facts about production (checked live this session)

- DB is Render Postgres; external URL is the `DATABASE_URL` line in `webapp/.env.remote`. **That file was
  recreated this session** from a credential the owner pasted; it is gitignored and untracked (confirmed).
  The repo is PUBLIC — never commit it.
- `alembic_version` = **0007**. Migrations 0008 and 0009 are **not applied**.
  - 0008 adds `econ_component`, `econ_pickup`, `kill_order_bonus` to `impact_scores` — all three missing in
    production today, and the new structure writes them.
  - 0009 creates `round_player_spend` — missing in production, written only by the ingest adapter, **never read
    by the scorer** (confirmed by a clean 3,125-match rescore against production).
- `impact_scores` 659,290 rows · `player_view_cache` 4,479 · `site_stats_cache` **0 (empty)`.
- A full read-only rescore of all 3,125 matches takes **16.7 min** against Render. Budget a writing backfill of
  659,290 rows well above that.
- `ACTIVE_MANIFEST = None`, `IMPACT_CALCULATION_VERSION = 2`.
- **Docker is not installed on this machine at all** (no binary in Program Files or Start Menu; BIOS
  virtualization off). Every `docker exec` step in the rc2 runbook must be rewritten against Render using the
  PG18 client binaries at `C:\Program Files\PostgreSQL\18\bin\` (pg_dump/pg_restore/psql all present).
- `webapp/.env` still points at the **old pre-cutover Neon DB** (matches only to id 3100). Do not use it for
  verification.

## Open decisions the owner still has to make

1. **`trade_credit` and `assists_component` are not persisted columns** on `impact_scores`. With credit now ON,
   the credit moves `impact` and `leverage_component` with nothing in the DB isolating it, so it cannot be
   audited from stored rows — only by replay. Add a migration to expose them, or accept derivable-only?
2. **The ~210-row gap**: the rescore produced 659,500 player-rounds vs 659,290 stored. Explain it before the
   backfill's acceptance check compares stored rows to a replay.
3. **PR #67**: merge whole, or split the activation into its own PR? Merging is itself a deploy — Render builds
   from `main` and runs `alembic upgrade head`, which applies 0008 and 0009.
4. Whether `FormulaWeights` dataclass defaults change at all (see the "defaults trap" section of
   `HANDOFF-rc3-ship-to-site.md` — changing A from 1.25 to 1.0 silently alters live legacy scoring).

## Suggested first moves tomorrow

1. Relaunch the `Plan` subagent on `HANDOFF-rc3-ship-to-site.md` (no correction needed).
2. In parallel, rerun the credit-ON corpus measurement (command above) so the ledger RESULT entry has real
   full-corpus numbers for the locked config.
3. Decide items 1–4 above.
4. Only then: predeclared ledger entry → implementation → RESULT entry → rc3 freeze → review → activation.
   Nothing is committed, frozen, rescored, activated or deployed yet.

## Corrections made during the session, worth not re-deriving

- The repo is **public**, not the "private sister-repo" CLAUDE.md used to describe.
- A Competitive surrender vote opens at **round 5**, so 4-round matches are legitimate early surrenders. An
  earlier "mode leakage" hypothesis about 36 short matches was wrong; the corpus is clean on that front.
- The B=3 / C=2.347 weights that appear throughout the ledger were **never in the code** — they were always
  passed explicitly in analysis scripts. Same is true of today's B=2.5 / C=2.5.
