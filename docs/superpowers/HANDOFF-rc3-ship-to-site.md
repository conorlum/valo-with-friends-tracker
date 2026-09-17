# Handoff: freeze rc3 and ship the new Impact scoring to the live site

You are picking up a Valorant match-analytics project mid-stream. The owner has just **locked a scoring
configuration** and wants it on the live site. Your job is to **produce a step-by-step implementation plan**
— not to execute it. Nothing in this task authorises you to change scoring, rescore, push, merge or deploy.

## 1. The decision that was just made

The owner locked these weights after a full-corpus measurement (read-only, all 3,125 matches):

| weight | value | meaning |
|---|---|---|
| A `damage` | **1.0** | locked; everything else is scaled around it |
| B `leverage` | **2.5** | moved down from the previously-declared 3.0 |
| C `econ` | **2.5** | moved up from the previously-declared 2.347 |
| D `assists` | **100.0** | flat points per assist |
| `trade_credit_scale` | 1.0 | tuning knob for the credit, independent of B |
| `enable_trade_credit` | **True** | trade credit ships ON |

Structure: `impact = A*damage + B*leverage + C*econ + D*assists`, with
`enable_econ_component=True`, `econ_model="buy_disruption_v2_30_80_bonus_denial"`, `use_realized_swing=True`,
timing candidates off (`enable_postplant_leverage=False`, `enable_preplant_empirical=False`).

**Trade credit ships ON** (owner decision, 2026-09-16). A player killed by an enemy whose killer a teammate kills
within 6 seconds is credited a share of the trade kill's leverage — 60% in [0,1) seconds stepping down to 30% in
[5,6) — added on top of the trader, leverage only, split by max/sum when one trade avenges several teammates.
The frozen manifest must record `enable_trade_credit: true` explicitly: `impact_manifest.config_from_dict`
defaults a missing key to `False`, so an absent key silently ships the wrong configuration.
`trade_credit_scale` (default 1.0) multiplies the credit *before* B, so the credit can be retuned later without
disturbing the leverage weight.

Corpus-wide, the credit is ~15% of positive leverage, touches ~16% of player-rounds, and 22.1% of enemy-kill
deaths are traded. It lifts nearly every player rather than reordering leaderboards, but it does change ranks in
close matches.

**The share figures below were measured with credit OFF and therefore no longer describe the locked
configuration.** They are kept only as the baseline comparison. A credit-ON corpus measurement is being taken;
the ledger RESULT entry must record the credit-ON numbers, not these.
Baseline (credit OFF): corpus player-match magnitude share
damage 43.5% / leverage 37.7% / econ 8.9% / assists 9.9%; impact/round mean 206.0, sd 604.8;
**zero negative-damage player-rounds**. Sanity checks passed: all 3,125 matches have exactly 10 players;
round counts are 13–24 in regulation then even numbers only (26…38) for overtime; 45.4% of player-matches rank
identically to their K−D rank and 80.8% within one place. Matches shorter than 13 rounds are legitimate early
surrenders (Competitive allows a surrender vote from round 5), not bad data.

## 2. Where the code is right now

- Branch `impact-scoring-impl`, HEAD `7f4a63b`, **three files modified and uncommitted**:
  `webapp/app/scoring/impact.py`, `webapp/app/scoring/impact_manifest.py`,
  `webapp/tests/test_impact_trade_credit.py`. That diff adds `FormulaWeights.trade_credit_scale`
  (default 1.0, multiplies the credit before B), serialises it in `config_to_dict`, and adds two value-level
  tests. It is a no-op at scale 1.0 and was verified as such by re-running match 3104.
- **PR #67 is open** against `main`: https://github.com/conorlum/valo-with-friends-tracker/pull/67 — the whole
  108-commit branch, mergeable, not merged.
- **The weights are NOT in the code.** `FormulaWeights` defaults are still
  `damage=1.25, leverage=1.0, econ=1.0, assists=0.0`. The locked values have only ever been passed as explicit
  arguments in analysis scripts.
- `ACTIVE_MANIFEST = None` in `app/scoring/impact_runtime.py`, so live ingestion still scores with the legacy
  formula. `IMPACT_CALCULATION_VERSION = 2`.
- Tests: 1,150 pass. Two are known-red and were red before any of this work:
  `test_impact_exante_swing::test_builder_matches_stored_values` and
  `test_site_stats_cache::test_happy_path_blob_validates`. A local run also reds
  `test_impact_exante_swing::test_wrapper_still_persists_and_commits` because the *local* test DB is behind the
  models. Do not treat these as regressions, but do confirm they are unrelated before shipping.

### The trap: `FormulaWeights` defaults are what live ingestion uses

`compute_impact_for_match(db, match_id)` with no config resolves `active_scoring_config()`, which is `None`,
so no `weights` key reaches `build_impact_rows_for_match`, which falls back to `FormulaWeights()`. Therefore:

- Changing the **A** default from 1.25 to 1.0 silently changes live legacy scoring (the damage term is shared by
  both formula branches) and would make newly-ingested rows inconsistent with the 659,290 already stored.
- B, C and D are inert on the legacy path (`enable_econ_component=False` there), so changing those defaults alone
  has no live effect.

The plan must state explicitly how the locked weights reach production — via a **frozen rc3 manifest** that is
activated, not by quietly editing dataclass defaults. Decide and justify whether the defaults change at all.

## 3. Production state (verified against the live Render DB this session)

- DB is **Render Postgres**, external connection string in `webapp/.env.remote` (gitignored, recreated this
  session). Internal and external URLs are not interchangeable.
- `alembic_version` = **0007**. Migrations **0008 and 0009 are not applied.**
  - `0008_econ_component_and_kill_order_bonus.py` adds three columns to `impact_scores`:
    `econ_component`, `econ_pickup`, `kill_order_bonus`. These are **missing in production today** and the new
    structure writes them.
  - `0009_round_player_spend.py` creates `round_player_spend`, which is **missing in production**. It is written
    only by the tracker.gg ingest adapter and **never read by the scorer**, so its absence does not block
    scoring — confirmed by a clean 3,125-match rescore against production.
- Row counts: `impact_scores` 659,290; `player_view_cache` 4,479; `site_stats_cache` **0** (empty).
- Note the rescore produced 659,500 player-rounds against 659,290 stored — a ~210-row gap worth explaining
  before the backfill's acceptance check runs, since that check compares stored rows to a replay.
- `assists_component` and `trade_credit` are **not persisted columns** on `impact_scores`. Both terms therefore
  contribute to `impact` without being separately visible. With trade credit now shipping ON this matters more:
  the credit moves both `impact` and `leverage_component` with no stored column isolating it, so it cannot be
  audited from the database alone. Decide whether a first pass needs a migration to expose either, or whether
  leaving them derivable-only is acceptable.

## 4. The process this project requires (non-negotiable)

Read `docs/superpowers/2026-09-07-predeclared-values.md` — it is append-only. Never edit a recorded value in
place; strike through and restate.

1. A **predeclared ledger entry before any scoring**, stating the weights, the trade-credit decision and the
   measurements to be taken.
2. Implementation.
3. A **RESULT entry** recording what was measured.
4. An **rc3 freeze**: a `candidate-manifest.json` plus the full review. Use
   `docs/superpowers/econ-bonus-denial-candidate-rc2/README.md` as the template — it is the best existing model
   of a freeze, its reproduction commands and its activation/rollback runbook.
5. Activation bumps `IMPACT_CALCULATION_VERSION` 2 → **3** and sets `ACTIVE_MANIFEST` to the rc3 manifest path,
   in one commit. The manifest refuses to load at any other version.

rc2 itself is frozen, **inactive, and no longer verifies** (its hashed sources changed when `impact.py` changed).
Do not try to activate it. rc3 must be frozen from the current checkout.

TDD is required, and a test must be proven to check values: a first failure of `TypeError`/`AttributeError`
proves nothing — mutate the implementation and confirm a *value* failure.

## 5. What is different from every previous runbook: there is no Docker here

The rc2 activation runbook assumes local Docker Postgres (`docker exec valomaths-private-postgres-1 ...`).
**Docker is not installed on this machine** — not merely stopped; there is no Docker binary in Program Files or
the Start Menu, and BIOS virtualization is off. Every `docker exec` step must be rewritten.

- Use the native PostgreSQL 18 client binaries at `C:\Program Files\PostgreSQL\18\bin\`
  (`pg_dump.exe`, `pg_restore.exe`, `psql.exe` all present) pointed at the Render external URL.
- There is **no local mirror of production**. The checked-in `webapp/.env` still points at the old pre-cutover
  Neon database, which only holds matches up to id 3100 and must not be used for verification.
- Consequently the backfill runs **against the live production database over the network**. A read-only rescore
  of all 3,125 matches took **16.7 minutes**; a writing backfill will be materially slower. Size the maintenance
  window accordingly and say so in the plan.
- The site is deployed on Render from `main`, so **merging PR #67 is itself a deploy**: `render.yaml`'s build
  command runs `alembic upgrade head`, which applies 0008 and 0009. Sequencing matters — work out whether
  migrations land before, with, or after the activation commit, and what the site serves in between.

## 6. Deliverable

A step-by-step plan covering, at minimum:

1. The trade-credit confirmation and any other decision the owner must make before a freeze.
2. How the locked weights reach production (manifest vs. defaults), and what happens to the legacy A=1.25 path.
3. The predeclared ledger entry, then implementation, then the RESULT entry.
4. Building and verifying the rc3 manifest and its review artifacts, adapted from the rc2 runbook.
5. Migration sequencing (0008, 0009) relative to the activation commit and the Render deploy.
6. Backup and rollback **without Docker**, against Render.
7. The backfill of 659,290 stored rows, its acceptance check, the ~210-row discrepancy, and a realistic
   maintenance-window estimate.
8. Cache rebuild (`scripts/recompute_player_views.py`) and what to do about the empty `site_stats_cache`.
9. Post-deploy verification against the live site.
10. PR #67: whether to merge it whole or split the activation into its own PR.

Call out anything you find that contradicts this handoff — it was written from live inspection, but verify
rather than trust. Flag risks and open questions explicitly instead of inventing answers. Produce the plan
only; do not implement it.
