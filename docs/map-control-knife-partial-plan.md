# Map control: partial ZERO/point hits (plan, 2026-10-16)

The user's idea (2026-10-15): KAY/O's knife shows every enemy inside its radius, so the set it catches is complete
for that area. If it catches 4, the 5th is not in the radius, and if the 5th is seen elsewhere at the same time,
nobody is unknown.

## Where the engine is today

Unknown is per observing team and per living enemy (`docs/map-control-unknown-plan.md`, rule 7), and utility feeds
it through knowledge operations (`app/control/utility.py`: `locate`, `restrict`, `exclude`, ...).

| Utility result | Today | Where |
|---|---|---|
| Recon bolt or Haunt pulse | Each enemy it didn't reveal: its sight within range is excluded. Each it revealed: located. | `read_pulses`, `read_reveals` |
| Knife, proven pulse, nobody hit | Each living susceptible enemy: radius excluded (`knife_zero`) | `read_knives` |
| Knife, every living enemy hit | Each: restricted to the radius (`knife_all`) | `read_knives` |
| Knife, some hit | **Nothing, for anyone** | `read_knives` |
| Enemy seen anywhere | Located where they stand | engine sight |

So the empty-pulse half of the idea is already built, and the "seen elsewhere" half falls out of per-enemy unknown:
a sighting locates that enemy whatever the knife did. The one gap is the partial knife.

**This reverses part of a settled decision.** Item 10 of the 2026-10-05 review
(`docs/superpowers/specs/2026-10-05-replay-control-review-design.md`) says "Partial hits do nothing, including no
per-target shrinking." That spec gives no reason. The user asked for this change on 2026-10-15/16.

## The rule

For a knife with a proven pulse and a complete hit list (`activation.state == "completed"`,
`targets_complete`, `pulse.r` present: the same gate as today), at the pulse time `t`:

1. **Each living enemy it hit** is restricted to the radius at `t` (`restrict`, reason `knife_hit`): somewhere
   inside it, not pinned to a point (D1).
2. **Each living enemy it didn't hit, and who could have been hit** (not an ulting Veto), has the radius excluded
   (`exclude`, reason `knife_missed`). The radius is geometric, through walls, as today.
3. **An ulting Veto who wasn't hit** keeps his region: he can be inside without being hit.
4. Nobody hit and everyone hit keep today's behaviour and reasons (`knife_zero` exclude, `knife_all` restrict).
   They are the two ends of the same rule.
5. An unproven pulse or incomplete hit list still tells nothing, as today.

The user's worked example: 4 hit, 1 outside, the 1 seen across the map. The 4 are restricted to the radius by
rule 1. The 5th is located by sight, and the radius exclusion from rule 2 also holds for them until that
sighting. So the team's unknown is just the radius (the 4's regions, spreading again from the pulse on), where
today it is wherever any of the 4 could have walked.

## Decisions

- **D1 (user, 2026-10-16): a hit enemy is restricted to the radius, not located.** Locating them (the way recon
  reveals are) was offered and declined. `knife_all` stays as it is.
- **D2 (settled by the user's request): partial knives now give information**, reversing item 10's "partial hits
  do nothing".

## Build

1. `read_knives` (`app/control/utility.py:214`): replace the `else: continue` branch. With some but not all
   enemies hit, each hit living enemy gets a `restrict` on the radius (`knife_hit`) and each unhit susceptible
   one an `exclude` (`knife_missed`); an unhit ulting Veto gets nothing. Keep `detail` with `needs`, `hits` and
   `living`. Neither is locating evidence, so `Unknown.events` and the gap detector don't change.
2. Reasons: add `knife_hit` ("knife hit them: inside its radius") and `knife_missed` ("knife missed them: not in
   its radius") to the labels in `app/static/js/replay_control.js:488` and the reason list in
   `tests/replays/test_control_reasons.py:105`.
3. Tests (`tests/replays/test_control_knife_skye.py`):
   - rewrite `test_a_partial_knife_tells_nothing_not_even_about_who_it_hit` into: hit ones restricted, the rest
     excluded;
   - Veto: a partial knife with an unhit ulting Veto excludes the others, never him; with every other enemy hit,
     the others are restricted (`knife_hit`, not `knife_all`) and Veto keeps his region;
   - zero, all, deaths at the pulse, allies in the hit list and unproven rows keep their current tests;
   - an engine-level test like `test_a_zero_knife_clears_its_radius_from_the_unknown`: with one hit and one
     unhit enemy, the hit one's unknown is cut to the radius and the unhit one's loses it.
4. Release: `CONTROL_REVISION` 16 -> 17 (`app/replays/control_format.py:47`) with its note, and re-pin whatever
   `tests/replays/test_control_figures.py` and the constants digest check. No re-condense: the hit lists are
   already in condense revision 13+ rows.
5. Preview: find a round in the local replays with a partial knife (scan `util` rows for `KNIFE` with
   `0 < len(hits ∩ living) < len(living)`), compute it locally with no DB writes, and look at the unknown before
   and after the pulse with the user. **No full-corpus recompute until the user says the sample looks right**
   (owner decision, as with every control revision).

## Out of scope

- Haunt and recon partial hits already exclude their sight for the unrevealed enemies; nothing changes there.
- The "what the team knew" pictures read the same unknown, so they pick this up with no extra work.
