# First release with current replay evidence

[Abyss rope completion](ABYSS-ROPES.md) is now the final selection: all 23 tagged features.
This file retains the original checkpoint and the owner-controlled rollout.

The later [existing-data follow-up](EXISTING-DATA-FOLLOWUP.md) supersedes this original
eleven-feature selection: it qualifies nineteen features, including all Split/Summit ropes,
Summit Heaven's door and both Abyss breakables on the existing flat fallback. Keep the receipts
below as historical evidence, rather than mixing their compiler-13 outputs with the follow-up.

The owner accepts the current door geometry estimates and defers unavailable replay evidence. This
release is an explicit subset. Missing floors, ambiguous landings and failed height gates remain
disabled; additional replays can qualify them in a later release. No further geometry drawings or
timing decisions are required for this first pass.

## Scope

| Map | Intended first-pass features | Deferred |
| --- | --- | --- |
| Ascent | Garden and Market doors; transparent Heaven glass | No placement blockers |
| Sunset | Descending door, including reopening | No placement blockers |
| Split | Two measured Vent–A Heaven rope connections | Left Vent, Right Vent, Heaven and Sewers ropes need landing evidence |
| Haven | Existing terrain/control baseline | No tagged gimmicks |
| Summit | Cave and Mid drop doors | Heaven doorway has ten cells with no accepted ground runs; rope needs landings |
| Abyss | None | Global height checks fail; four ropes need bindings, including Mid's named quiet direction |
| Lotus | Both rotating doors and the breakable | No placement blockers |

The eleven selected bundles have resolved placement against six locally passing height candidates.
Both Abyss breakables now place without trimming their original sight footprints, but passing
placement does not override the map's rejected height asset. Its entire feature set stays disabled.
The owner's Abyss Mid policy is retained: normal walking approach, a 1.5-second quiet climb from
Choke 2 toward the Mid breakable, then normal walking departure. It is not assigned to an arbitrary
`a`/`b` direction before those sides and floors are physically bound.

## Prepared evidence

Private receipts pin the candidate, exact masks, height files, parser recipe, native recording
hashes, compiled artifacts and calculation inputs. The current runtime uses compiler 13, height
rules 5, height format 3, condenser 17, control 14 and gaps 3. The local selection is not live
publication or DB height activation.

The final compiler/runtime/artifact/mixed-grid suites pass 152 tests; worker protocol, artifact
verification, isolated SQLite storage and height-job suites pass 138. The Dockerfile's import and
parser-pin smoke checks pass locally. Docker is unavailable here; the Linux image remains checked
by its existing build/import steps on the owner's approved deployment. These local runs are not a
Linux benchmark, and an additional memory rehearsal is not an owner prerequisite.

All 23 captured Ascent, 20 Sunset, 19 Summit and 24 Lotus rounds have supported timelines for the
selected panels. The supported static Split model also initializes across its 24 recaptured rounds;
it does not need a door RPC timeline. These checks are separate from full control/gaps calculations.

Six complete real-round checks on compiler 13 pass fresh full control, fresh gaps-only and cached
gaps parity. The exact independent gap counts are:

| Scene | Gap rows | Recorded sampled full/incremental comparisons |
| --- | ---: | --- |
| Ascent Garden/glass round, knowledge enabled | 15 | 32/32 |
| Ascent Market fully closes, persists, then breaks | 33 | Assertions passed; count not retained |
| Sunset closing and reopening | 23 | Assertions passed; count not retained |
| Split supported rope model | 8 | 33/33 |
| Summit qualified drop doors | 23 | 45/45 |
| Lotus B–C rotation twice and breakable destruction | 24 | 44/44 |

Ascent Market and Sunset initially hit a Windows path limit in the private gaps-cache directory.
Their completed full control/tick outputs were preserved; the separate gaps-only check was rerun
with short paths and all three outputs matched. Exact counterfactual counts had not been written
before that failure and are not invented. All other scenes retained counts. Ascent's Garden/glass
scene includes knowledge layers; the other checks exclude them. Existing unresolved-terrain,
barrier-start and utility/input fallbacks remain in the private receipts and site previews. Parity
does not erase those limitations or qualify the deferred bundles.

The friendly site feature labels now come from current map annotations instead of identifiers in
the calculation archive. Names remain editorial: renaming does not change control/gaps identities
or mutate old calculation payloads. Viewer/context/storage checks pass 113 tests with two existing
skips; these suites overlap the earlier totals. Real receipts and previews stay private.

The publication proposal contains the complete annotations with deferred bundles disabled, seven
regenerated map mask sets and an updated geometry index. Every existing geometry kill-line fixture
passes on those masks. The proposal excludes recordings and height blobs, stays outside Git and
has not been published. Existing index height metadata is preserved rather than promoting local
diagnostic files into the static fallback.

## Owner-controlled rollout

1. Review the private selection, complete calculation receipts and publication proposal. Keep the
   owner snapshot and deferred annotations. Approve only the selected subset; pending bundles must
   stay disabled. Review publishable annotation metadata before copying it into the public repo.
2. Merge the prerequisite PR #127, then the feature branch PR #128, through the owner; retarget
   #128 to main after its prerequisite merges if needed, reviewing its resulting diff. Apply the
   approved annotation/mask/index files through the same reviewed release path. No database schema
   change, scheduler edit, scoring change or demo replay import is needed for this follow-up.
3. Let the web service and worker deploy the matching code/parser recipe. Check worker health and
   archive recipe agreement using `scripts/reparse_archive.py --status`. The public demo remains
   isolated from friends recordings and DB height selections.
4. Let the worker produce and gate current heights against the approved permanent masks. Check
   `scripts/control_heights.py list` through the friends DB's read-only wrapper. A local passing
   receipt is not a live accepted row; never activate the rejected Abyss asset. Recheck selected
   rope bindings against the actual accepted generation rather than copying node IDs from a preview.
5. Recondense/reparse old recordings on the new recipe before the feature-enabled recompute.
   Older stored blobs can lack complete map timelines. An ordinary control/gaps recompute or page
   reload does not add those events. Use the existing archive reparse path for one canary match per
   enabled map before expanding the archive job.
6. Check the canary's calculated feature names/states, exact artifact/height identities, missing-input
   notes and fresh gaps. Confirm a closed door stays blocking, destruction clears it at the explicit
   status event, and the supported rope pays its quiet travel time. Then expand the recompute with
   the existing stale-round workflow. No additional worker memory approval window is introduced.

Release and recompute commands that write external state remain the owner's approval step. This
preparation does not merge, deploy, publish tags, activate heights, reparse the live archive or
start a live recompute. Main has not changed.

## Later, when more data exists

Collect ordinary grounded traversal through Summit Heaven's open doorway and complete boarding/
departure samples for the unresolved ropes. Additional Abyss matches may resolve sparse overlapping
regions and its failed kill-line check. Rebuild heights with retained refinement history, rebase
bindings using world heights and physical positions, and run the same source, placement and
control/gaps checks. Enable only newly qualified bundles in a later reviewed candidate. Existing
quiet costs, directions, door timing and geometry decisions remain settled unless evidence contradicts
them.
