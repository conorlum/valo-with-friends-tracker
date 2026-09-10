# Implementation plan: buy disruption with 30% / 80% death penalties

Status: revised after plan review; implementation still pending. Implement on
`impact-scoring-impl`. The formula is specified
in `specs/2026-09-10-econ-buy-disruption-implementation.md`, particularly section
12, which supersedes the historical wealth-based death penalty in section 7.

## 1. Freeze the reviewed behavior

Use the V2 kill credit and the owner's revised debit:

```
kill credit = small equipment-loss value + allocated buy-disruption value
death debit = 0.30 * damage value when absorbed
death debit = 0.80 * damage value when the severity pool is positive
player econ = round(C * 1007.9209 * (sum(raw kill credits) - raw death debit))
```

The damage basis is the same small-plus-disruption value on both sides. There
is no additional own-wealth scarcity debit. Non-enemy deaths get own debit
without invented opponent credit. Count each player's starting paid kit once.

**Signed scores are required.** Store, aggregate and display the resulting net
directly, including negative player-round, match and career contributions.
Do not floor net econ at zero, add the absolute minimum, subtract the worst
player's score, or normalize a match/leaderboard to make its lowest value zero.
The +928/+101 tables were hypothetical comparisons only. Abyss 1xgoofy must
remain -101, not zero. Another player's score cannot change merely because
the lowest-scoring player's value changes. Keep the formula's resource/severity
caps; those model quantities are distinct from a prohibited floor on net econ.

Keep C=1, the 0.10 background, 3900 paid-kit target, 19500 team reference,
3900 activation gap, and round-2/14 carryover targets as reviewed. Retain the
binary 30%/80% switch, including its small step at a positive funding gap.
Do not refit A/B, change timing/trades, smooth the switch, or automatically
reanchor ECON_SCALE while implementing this change. A later scale proposal
must be shown alongside these reviewed values before adoption.

## 2. Build one production calculator

Add `webapp/app/scoring/econ_buy_disruption.py` with typed, pure functions for
input validation, history-based targets, first-loss exposure, team funding,
severity, allocation and audit output. Port the reference behavior; do not
import code from `docs/` in production. Keep the old econ implementation for
explicit comparison until rollout is accepted.

Pass current and next-round loadouts/banks, agent allowances, complete rosters,
ordered kill IDs/times, pistol/current/next outcomes, and final-round status.
Require complete valid inputs; report abstention reasons rather than treating
missing values as poverty. Enforce the existing eligible rounds, surrender
guards, half/match boundaries, overtime exclusion and exact ex-ante zero gate.

Return unrounded raw per-player credits/debits and a structured audit with
targets, funding, gap, severity pool, event exposure, allocation, penalty rate
and data-quality flags. Preserve original event IDs for trace reconciliation.
Round the final player-round net only once in the scorer.

## 3. Connect the scorer and review tool

Make minimal integration changes in `webapp/app/scoring/impact.py`. Its existing
`_econ_components_for_round` uses the old allocator and lacks the history needed
for carryover targets. Add an explicit econ-model choice to the pure build path
and pass the required round history to the new calculator. Keep the prior
default while reviewing. Reject invalid model/flag combinations explicitly.

Validate and route source inputs BEFORE the existing economy-differential,
swing and combat-decoration loops. Those loops currently index killer IDs and
stats before the econ helper runs, so adding guards only inside the helper is
insufficient. Include source `KillEvent.id` in each loaded event dictionary and
preserve stable `(event_time_seconds, id)` ordering throughout the audit.

Keep a complete source-event ledger for economy, separate from eligibility for
killer combat credit. A known victim with a null/environmental killer still
contributes own equipment loss/debit, receives no invented enemy credit, and
must not cause a null-key lookup. Self/team deaths follow the specified own-loss
policy. Preserve observed victim deaths for round-state accounting even when
there is no eligible enemy killer; do not simply discard the whole event.
An unknown victim prevents reconstruction of team loss and must produce an
explicit econ abstention. Missing/invalid economy fields likewise abstain
without being substituted as zero wealth. Bypass legacy economy-factor work
whose results the new composite does not use. Preserve valid legacy-model
outputs and existing valid combat behavior. If data needed for combat itself
is missing, report a structured match validation failure before persistence;
do not invent combat scores to keep a backfill running.

Extend `webapp/scripts/release_candidate_review.py` to select the new model
and consume its versioned audit data. Define explicit comparator identities:

- `live_legacy`: the current runtime formula. Report its replay and persisted
  site values separately if they differ; neither silently replaces the other.
- `separate_econ_legacy`: the older pooled/zero-sum separate-econ allocator,
  retained as an optional diagnostic, not the user's previous negative column.
- `buy_disruption_v2_wealth`: V2 kill credit plus the independent wealth debit,
  which produced the previous Abyss -100 through -928 totals.
- `buy_disruption_v2_30_80`: identical V2 kill credit with the owner's new debit.

The penalty-only comparison is `buy_disruption_v2_wealth` versus
`buy_disruption_v2_30_80`, with identical gross kill credits and all non-econ
settings held equal. The site comparison is `live_legacy` versus the complete
release candidate and must label its full formula changes. Existing
`--weights`, `--match`, `--ten` and `--trace` remain available.

Every event trace must explain whether funding absorbed the loss, which
next buy was observed, the kill's small/large credit parts, and its victim's
30%/80% debit. Show round and match totals plus averages across all played
rounds, including zero-econ boundary rounds. Review and runtime must call the
same production calculator, not separate copies of the reference formula.

## 4. Prove parity and cover integration failures

Add production tests for the reviewed behaviors and integration tests around
the actual build path. Keep the saved reference artifacts immutable as
regression expectations. First demonstrate numerical failures, then verify
the fix and reinstate each defect behind the same API to confirm detection.

Required cases: absorbed losses with rich or low reserves; teammate-funded
replacement; constrained buy; cheap losses against existing poverty; zero
loss; duplicate deaths and event ordering; self/team/environmental deaths;
missing/invalid data; incomplete rosters; carryover targets including losing
round 2; final/half/OT/surrender boundaries; ex-ante zero; C applied exactly
once; observer on/off equality; and persistence matching in-memory results.

Run environmental/null-killer, unknown-victim and missing-input cases through
`build_impact_rows_for_match`, not only the pure calculator. Assert the intended
debit/abstention or structured pre-persistence failure, no null-key crash, no
invented killer credit, correct victim-state handling and intact event IDs.
Include negative net econ through persistence, aggregation and display. Assert
that neither a zero floor nor a match-minimum offset is applied, and that a
change to one player's net cannot shift every other player's displayed score.

For frozen Abyss 3104, reconcile all 200 eligible player-rounds and all events
against `abyss-buy-disruption-review/death-penalty-30-80.json`. Acceptance:
unrounded values agree within numerical tolerance and every rounded net agrees
exactly. Key match totals are Osmin +708, 1xgoofy -101, TEAM_1 +502 and TEAM_2
+1293. This is an implementation parity check, not independent model validation.

Also reproduce all ten previous wealth-debit totals from the frozen V2
`calculations.json`, and confirm unchanged gross killer credits against the
30%/80% artifact. Compare every rounded player-round net, not just team sums.

Run the new tests and affected existing econ-hook, observer, reconstruction,
persistence and cache tests. Preserve tests of the legacy model; replace its
zero-sum assertion only for the new model with allocation and debit identities.

## 5. Review the actual candidate on one match and ten matches

Before the first integrated review, create a frozen candidate manifest. Record
model IDs, A/B/C, scale, timing flags, table contents/hashes when enabled,
centering constants, agent allowances, scorer revision and source snapshot
identity. Use explicit flags: the existing review tool automatically enables
post-plant leverage and rebuilds a table from the corpus, which is not an
acceptable implicit choice. For an econ-only comparison keep timing identical
between arms; freeze any timing artifacts included in the full release.

The review, runtime and recompute paths must load the same manifest/artifacts.
Do not rebuild tables or apply review-only monkey patches between approval and
activation. Fail visibly if an expected artifact/hash is missing or differs.
Verify that adding unrelated new matches cannot change the approved scores
when replaying the frozen source under the frozen configuration. A scoring
code change after approval requires re-running affected review checks.

Re-run Abyss through the integrated scorer and review tool. Then use the
tool's existing fixed ten-match selection. Before scoring, record sample IDs,
configuration and coverage/distribution checks in predeclared-values.md.
If that sample lacks a pistol winner losing round 2/14, add a separately
labeled history example selected from outcomes rather than favorable scores.

Show per-player old/new full Impact, econ contribution, match rankings and
per-round changes. Include gross credit/debit so net totals cannot hide large
offsetting terms. Inspect absorbed losses, forced/weak buys, early carryover,
and the 30%/80% boundary. Report ambiguities and data abstentions.

Run a read-only corpus audit for coverage, score distributions, largest changes
and leaderboard movement under the declared checks. Do not fit weights merely
to make these results positive or zero-centered: positive combined enemy-event
balances are inherent in 100% credit versus 30%/80% debit. The owner reviews
the concrete match reports before activation. A/B fitting is not a prerequisite.

## 6. Activate once and verify the local site

Use one explicit runtime configuration shared by ingestion, recompute and
review. Currently `compute_impact_for_match` calls the build function without
candidate flags, so changing the calculator alone will not activate it.
Load the configuration and artifacts frozen BEFORE review in section 5; do
not refit or select tables/flags at activation. Verify full Impact matches the
approved integrated report, in addition to the isolated econ parity checks.

Coordinate one Impact version bump with the actual release baseline. The
current code has `IMPACT_CALCULATION_VERSION=2`; do not repeat the obsolete
1-to-2 instruction. Its version already feeds player-view cache identity.
Bump fight-EV/state-diagram versions only if their own calculations change.
Inspect schema/persisted fields; add a migration only if integration needs one.

Use a maintenance window for this local site. Stop the web process and all
ingestion/prewarming workers before changing the active configuration or
starting the backfill; keep them stopped through verification. Confirm no
remaining process can serve mixed scores or write new match/cache rows.

Back up the verified local database and preserve the previous runtime config,
artifacts and code revision for rollback. Record the complete backfill match
set and track attempted/succeeded/failed IDs. Run `scripts/recompute_impact.py`,
then `scripts/recompute_player_views.py`. The former clears caches initially
but commits matches individually: on its own it does NOT prevent mixed scores
or new caches from being populated halfway through the backfill.

Require every recorded match to succeed, with no missing expected score rows,
before accepting the backfill. Verify saved Abyss/full candidate scores and
recent/career caches; perform local page checks while ingestion stays paused.
Resume normal access and workers only after these checks pass. Confirm new
ingestion uses the identical active configuration.

On interruption/failure, keep the site/workers stopped. Either rerun the
backfill under the same frozen configuration (idempotent upserts), then clear
and rebuild caches, or restore the old database/configuration/artifacts/code.
Never reopen on a partly converted database. Test an interrupted multi-match
rescore and its recovery, including that no request or worker can populate
caches from mixed versions. A rollback must restore/recompute scores and
refresh caches as well as revert code.

The repository's current AGENTS.md specifies local-only operation and Postgres
port 5433. This plan does not include the historical Render deployment path.
If starting Postgres, use `docker compose -p valomaths-private up -d` in webapp.

Completion means the approved formula is persisted and visible on the local
site with matching traces, not merely that the standalone calculator passes.

## Review resolution

The four findings in `2026-09-10-econ-buy-disruption-plan-review.md` are addressed
in this plan: early input routing in section 3 and its integration tests in
section 4; maintenance/backfill recovery in section 6; named historical
comparators in sections 3-4; and pre-review configuration freezing in section 5.
These are corrected implementation requirements, not claims that the production
code has already been changed or the new acceptance tests have already passed.
