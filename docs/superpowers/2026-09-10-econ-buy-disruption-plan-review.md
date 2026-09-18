# Review of the 30% / 80% implementation plan

Reviewed 2026-09-10 against the current working-tree plan and scorer. Static
review only; no production scoring, tests or rescore were run for this review.
The formula itself matches the owner's reviewed interpretation. Four plan
changes are needed to make the integration and rollout reliable.

Resolution update: the owner requested these corrections and explicitly
confirmed signed net economy scores with no minimum-to-zero adjustment. All
four findings are now incorporated into the implementation plan, with execution
and acceptance tests still pending. The findings below preserve the original
review; their line references refer to the pre-amendment plan.

## P1: handle invalid and non-enemy events before legacy preprocessing

Plan location: implementation-plan.md lines 50-54, with promised behavior at
37-41 and 75-80. The new calculator's validation cannot enforce these policies
by itself: `build_impact_rows_for_match` first computes legacy economy factors
and combat decorations. Its economy-differential loop indexes current stats
with `killer_id` and `death_id`, and subsequent combat code directly indexes
`match_players[killer_id]`. A nullable environmental killer therefore causes
a KeyError before the new econ hook. Missing current stats similarly fail
before the intended econ abstention. `KillEvent` explicitly permits nullable
killer and victim IDs. Legacy swing calculations also run unconditionally even
when their results will not be used by the new composite.

Change: add a scorer-level input routing/validation step before those loops.
Keep the full ordered source-event ledger for economy; route only appropriate
events through combat scoring, with an explicit policy for non-enemy deaths
and for rounds with insufficient combat data. Bypass unused legacy econ factor
calculations for the new structure rather than relying on their outputs being
discarded later. Preserve legacy-model outputs for valid inputs. Add source
event IDs to the loaded kill dictionaries; the query orders by ID but currently
discards it when building those dictionaries.

Acceptance: exercise the actual build path with a known victim/null killer,
unknown victim, missing current stats and invalid econ inputs. Demonstrate the
specified debit or explicit abstention, no crash, no fabricated combat credit,
and preserved event identity. Pure calculator tests alone cannot prove this.

## P1: prevent reads/writes against a partially rescored database

Plan location: implementation-plan.md lines 125-131. The recompute script
clears caches once, then `compute_impact_for_match` commits each match. During
that interval, persisted rows contain both scoring formulas. Player routes
can compute aggregates from those mixed rows and write them back under the
new cache version. Clearing caches at the start is therefore insufficient;
failure or interruption can leave mixed scores and newly populated caches.

Change: for this local site, stop the web process and ingestion/prewarming
workers before switching configuration and running the backfill. Keep them
stopped until every match succeeds, caches are rebuilt and parity is checked.
Record the match set and completion/failure counts. On failure either resume
under the same frozen configuration or restore the previous database/config;
do not reopen a partially migrated site. No complex online migration system
is needed for the local-only setup.

Acceptance: test an interrupted multi-match rescore and its recovery, ensuring
no cache can be built from mixed versions and no concurrent ingest races the
declared backfill set. This is in addition to per-match persistence tests.

## P2: distinguish the historical comparison formulas explicitly

Plan location: implementation-plan.md lines 56-60. "Prior separate-econ
candidate" is ambiguous. The existing production `econ_component.py` is the
old pooled/zero-sum allocator. The user's previous Abyss column (-100 through
-928) comes from V2 buy-disruption credit plus the independent wealth debit,
which exists in the reference artifacts. They are different comparators.
Wiring the tool's current separate-econ candidate as the "previous formula"
would not reproduce the before/after table used to choose 30%/80%.

Change: use explicit identifiers for live legacy scoring, old separate-econ
scoring, buy-disruption V2 with wealth debit, and buy-disruption V2 with 30%/80%
debit. The owner-facing penalty comparison is the last two with identical
credits and non-econ settings. The site rollout comparison is live legacy
versus the chosen complete release candidate; label it as the total release
change. The old separate-econ comparator can remain diagnostic rather than
being another required product view. Compare persisted live values and replay
values separately if they disagree; do not silently equate current code with
the scores currently displayed.

Acceptance: reproduce all ten previous V2 totals as well as all ten revised
totals, and show that their gross killer credits agree. Print the complete
configuration/model identity with each comparison.

## P2: freeze the complete candidate before review, not at activation

Plan location: implementation-plan.md lines 113-117; review occurs earlier
at 94-109. The existing review tool rebuilds its post-plant table from the
current corpus and always enables post-plant leverage for its candidate. Its
optional preplant mode also monkey-patches a centering wrapper. Freezing only
the econ constants cannot make the full Impact review reproducible. Fitting
or choosing these inputs later at activation can ship different scores from
the approved ten-match report, even if all econ-only Abyss checks pass.

Change: create the full release configuration before the one/ten-match review.
Pin A/B/C, econ model, scale, timing flags, any table contents and hashes,
centering settings, agent allowances, code revision and review source identity.
Use the same artifacts for review, ingestion and recompute. If the task is
strictly econ-only, turn off unrelated candidate timing changes consistently
instead of inheriting them from the review tool. No post-review automatic refit.

Acceptance: running the candidate after unrelated new matches are ingested
must reproduce the approved frozen-source results. Persisted full Impact must
match the approved integrated report, not only its econ component.

## Conclusion

Proceed with the selected formula after these plan amendments. Retaining the
reviewed scale, avoiding unnecessary A/B fitting, sharing calculator code and
checking exact Abyss parity are appropriate. The positive totals and binary
rate step are already documented policy properties, not new implementation
defects. These findings concern integration, reproducibility and rollout;
they do not justify reopening the agreed 30%/80% values.
