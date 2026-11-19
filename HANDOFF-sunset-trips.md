# Handoff: Cypher trips look wrong on Sunset (and trips are missing from map control)

Written 2026-11-11. The owner noticed trips drawn wrong on the Sunset replay map. Investigate, then likely an
`/afk` fix run. The owner has said what looked wrong: see "Owner's answer" below.

## Owner's answer (2026-11-11): what looked wrong

**Trips stretched very far**, far past the length a trip can have. That matches lead 1 below (the viewer pairing
the wrong anchors, e.g. the ~52 m wire in round 5). Confirm on round 5, then treat it as this bug.

**A trip's maximum length is 15 m.** The wiki's Trapwire page gives "Max wire length 15 meters" (and 20 m max
deploy distance). The owner's screenshot of a max-length trip beside a Tejo ult agrees: the ult strip is 12 m
wide (wiki: Armageddon, 12 m width, 32 m long) and about 107 px across in the screenshot; the trip is about
131 px long, so about 14.7 m.

**Owner's rule: a trip longer than the max by about 1 m is probably wrong and should be flagged.** So
`TRIP_MAX_M = 15`, flag anything over 16 m (one named constant, shared by the extractor, the viewer and Critical
Util; there is no such constant in the code today). Where it applies:

- Extractor: a pair over 16 m is not a pair. Don't store an `end` for it, and count it (`wires_too_long` in the
  parse stats) so the cross-replay query in question 1 shows it.
- Viewer: never draw a wire over 16 m, including from `pairWires`' guess. Draw the anchors alone.
- Fallback pairing (question 3): only consider second anchors within 16 m of the first.
- The flag should be visible somewhere the owner looks: e.g. a Site health kind or the parse stats; decide in scope.
- Check stored replays for paired trips over 16 m too. If correctly paired trips ever exceed it, the 1 m slack
  or the map's unit multiplier is off; show that evidence to the owner rather than raising the cap.

## What's already found (match `2add00ce-5155-4815-9977-9dc1ad6c00c2`, Sunset, from a coach snapshot)

**No trip in this match has its far end.** All 105 `4_TripWire` rows in `blob.util` lack `end` / `end_z`: 96
are carried over from the previous round (t = 0.0) and 9 were placed mid-round. The extractor pairs a first anchor
with its `4_TripWire_SecondWire` only through the placement claim (`app/replays/extras.py` ≈ 1928–1975: `claim =
claims.get(actor.guid)`, then `placed[id(claim[1])]`), and here no pair was made. `counts["wires_paired"]` in
the parse stats should confirm it.

Three consequences:

1. **The viewer guesses the pairs** (`app/static/js/replay.js` ≈ 776, `pairWires`): each first anchor takes the
   same owner's unpaired second anchor spawned closest in time, within 1 s. Carried trips all spawn at t = 0.0,
   so an owner with two trips gets them paired arbitrarily (`dt <= bestDt` takes the *last* tie). Round 5,
   one Cypher's anchors:
   - first (7225, 5586) → real second (7232, 6556), about 12 m;
   - first (3400, 7918) → real second (3403, 6910).
   Order of the rows: 14428, 14456, 14870, 14898. The tie-break pairs 14428 with 14898, a wire from (7225,
   5586) to (3403, 6910), about 52 m across the map. That would "look wrong". Check that the viewer really does
   this on round 5 before building on it. Also: does the viewer use `end` when present, and fall back to
   `pairWires` only without it?
2. **Map control ignores every trip.** `app/control/engine.py:754` builds a trip watcher only `if e.get("end")`.
   No end, no watcher, so control never sees a trip go off. That feeds the coach's `knew`, timing gaps and
   Critical Moments. It may be why round 5's broken trip and any trip triggers in this match never reached
   `knew` (see `HANDOFF-info-util-signals.md`, "destroyed own utility").
3. **Critical Util** (`app/critutil/extract.py` ≈ 224) matches second anchors to firsts by `end`. Without it,
   trip casts are probably counted wrong or twice. Check what `replay_round_util` holds for this match, keeping
   in mind that `critical_util` was missing for every round of this match anyway.

The coordinates themselves look sane: anchor pairs come out 11–12 m apart (Sunset: 1 uv ≈ 1.28 world units
from `maps.json`'s multiplier 7.8e-05). One oddity worth a look: in round 5 another Cypher has two first
anchors 0.2 m apart, (1689, 5329) and (1696, 5312), with seconds at (870, 5731) and (1301, 4529). That could be
two trips off one wall, or one trip read twice.

## Questions to settle

1. **Is it Sunset, this replay's patch, or every map?** Query `wires_paired` vs trip counts across replays
   (read-only, `scripts/with_friends_db.py --read-only`), grouped by map and by the replay's game version or
   upload date. If pairing broke on a patch, every map since then is affected. If only on Sunset, look at
   what's map-specific.
2. **Why the claim pairing fails.** Read the raw export of one round (owner's `.vrf` archive and the parser's
   JSON, as in `docs/superpowers` replay plans). Does the second anchor still appear in the first's placement
   context? Did its name or code change (e.g. a new suffix)? Does a carried trip have a placement event at all,
   or only an actor that exists from t = 0? Carried trips may need pairing by the previous round's pair (same
   actor guids, or same positions) instead of by placement.
3. **The right fallback when no claim exists:** pair by position against the previous round's pairs, or by
   owner + wall geometry (nearest second anchor within the trip's max length that the map's sight mask allows
   a wire across), never by spawn time alone for t = 0 carries.
4. **What's already stored that's wrong:** a fix in extraction means a re-parse (CONDENSE revision bump), and
   control and gaps recompute for affected rounds. Memory: revision bumps break pinned tests
   (`test_control_figures`, the `test_control_format` digest, the replay_format/util pins). Preview on 1–2
   rounds before any full recompute. The auto re-parse queue is on for uploads.

## Likely decisions for the AFK scope

- The fix layer: the extractor (proper `end` for every trip, carried ones included; the real fix) vs a viewer-only
  tie-break (cosmetic). Recommend the extractor plus a viewer that never guesses across a tie: draw an
  unpaired anchor as an anchor alone rather than a wrong wire.
- Re-parse/recompute scope and timing (CONDENSE bump): owner's call.
- Whether trips that never got an end should be treated in control as "unknown wire" (no watcher, as now) or
  approximated.

## Acceptance

- A test from a recorded fixture: carried trips at t = 0 get their own second anchors (ids 14428 ↔ 14456,
  14870 ↔ 14898 in round 5 here, or the equivalent in a committed fixture with no player names).
- `wires_paired` equals the trip count on the sample replays, Sunset and at least one other map.
- The viewer draws no wire longer than 16 m (15 m max plus 1 m slack); an unpaired anchor shows as an anchor.
- Any pair over 16 m is flagged and counted, never stored as a trip's `end`.
- Map control on a re-parsed round has trip watchers; a trip set off shows up as a `trip` signal in `knew`.

## Constraints

- Public repo: no player names in fixtures, docs or tests. Match ids are fine.
- Tests: `./.venv313-test/Scripts/python.exe -m pytest`; `python scripts/test_catalog.py plan --git`. Run the slow
  lane for control/gaps changes. Never merge; end at an open PR.
