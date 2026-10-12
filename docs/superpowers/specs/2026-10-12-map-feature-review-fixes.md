# Map feature review fixes

This follow-up addresses the two owner-supplied reviews of PR #128. Work stays on
`codex/ascent-audit-and-tagger-guidance`; main, deployment, live databases, height
selection and annotation publication remain owner-controlled.

## Runtime geometry

Static compilation and sampled sliding panels share the exact spatial-leaf
reduction. Sampled placement carries the owning feature's qualified wall
attachment policy. The complete sight mask remains present; permanent walls
acquire no walking nodes. Compilation also qualifies open, cutoff, partial and
closed motion samples, so a subsequent height refinement cannot silently enable
a door whose movement sampling fails.

Lotus's swept panel areas receive local 4-pixel computational leaves. These
inherit the selected parent's existing floor model, including unknowns and
stacked floors. Existing measured fine leaves take precedence. The moving wall
blocks a leaf only when it covers more than half its area; exactly half remains
passable. This improves spatial resolution without claiming new terrain evidence.
The selected height identity, grid identity and feature input identity travel
through the artifact and visibility cache. Recovery rebuilds the same node order
from archived inputs and the original height asset. Global height activation
rejects this derived computational asset.

The merged player-state engine's damaging-molly footprints also use exact leaf
centres and floor binding, so the extra Lotus leaves retain that movement rule.

## Quiet walking exits

Unknown-height ground is classified by walking distance to measured supports,
rather than assigning every unknown approach to the lower level. Automatic cuts
remain local and retain their rope shortcut protection. A compile/runtime gate
rejects a route if those cuts disconnect a landing that previously belonged to
the main walking component. Already separate platforms are not forced to grow
an invented stair exit.

The owner confirms that Split's upper Vent area has an ordinary stair exit.
Existing accepted ground walks repeatedly cross one connector at that exit.
The private candidate records that directed connection in both directions for
the affected Vent routes. `quiet_walk_connections` contains two UV points with
world position-z and tolerance evidence. It can preserve only an existing local
quiet walk edge. It cannot create a connection, assign a floor, bind two unknown
ends or bypass an ambiguous stacked floor. A single unresolved leaf may retain
its existing fallback connection to measured ground. Explicit `quiet_cuts` win
over these records. Compiler and runtime use the same binding path, and all
records are rechecked when the selected height generation changes.

These records are immutable annotation inputs, like the existing Abyss walking
crop; they are not a claim that replay data refreshes annotations automatically.
Wrong or stale bindings leave the corresponding bundle pending. New recordings
can improve measured terrain through the existing height build/acceptance gates.

## Integration and diagnostics

Current main's player-state, blind, damaging-molly and corrected Abyss barrier
changes are integrated on the feature branch. Combined identities are CONDENSE
18, CONTROL 15, GAPS 3 and feature compiler 16. Consumer versions are Ascent 4,
quiet ropes 6 and map pool 6. Reference behavior remains pinned independently.

Missing, partial or unbound round messages report `inputs` /
`features_source_unavailable`, avoiding infrastructure retries. The decision is
to keep the existing fail-closed contract and require a reparse, rather than
silently publish a feature-free result under a feature-bearing identity.

Feature layer toggles, states, provenance and seekable event lists are exposed
for Ascent, Sunset, Split, Summit, Lotus and Abyss in linked and standalone
viewers. Haven retains its no-feature baseline.

## Release preparation

Regenerate the private publication package from current main's legacy tags and
copy only reviewed map features, plus the explicitly owner-reviewed Ascent
Catwalk corner correction. Abyss barrier paint and PNG must match current main
byte for byte. The package remains a proposal until the owner approves it.

Fresh combined-recipe checks must replace the historical compiler-15 receipts.
Include one scene per runtime consumer, production-shaped artifact transport,
fresh/cached gap equality and enabled/disabled feature comparisons. Compilation
and parity do not themselves prove a feature's physical accuracy.

The PR's Linux check builds the production Dockerfile, including the pinned
patched parser, then runs the refined-panel, archive and quiet-route regressions
inside that image. Its result is a separate release gate. Interactive browser
validation and deployed web/worker recipe agreement are separate checks too.
No database migration is added by this follow-up.
