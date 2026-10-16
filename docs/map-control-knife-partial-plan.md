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
4. Nobody hit and everyone hit keep today's reasons (`knife_zero` exclude, `knife_all` restrict). They are the
   two ends of the same rule. An ulting Veto named in the hit list doesn't make it "everyone": the others get
   `knife_hit`, and the engine drops Veto's own op (below).
5. An unproven pulse or incomplete hit list still tells nothing, as today.
6. **Rim margin (D3, provisional):** every knife restrict (`knife_hit`, `knife_all`) uses r + `knife_rim_m`, and
   every knife exclude (`knife_missed`, `knife_zero`) uses r - `knife_rim_m` (never below 0), with
   `knife_rim_m` = 2 m in `app/control/utility.json`. Measured on the 45 proven knives of two local exports
   (Haven `d07225f3`, Summit `650e6c6c`; r = 15 m): 7 of 202 living enemies were on the wrong side of the radius
   at `pulse.t`, all between 0.88 r and 1.01 r, and every one is on the right side 0.3 s before or after
   (the suppress lands over a short window; players at the rim run across it). Height explained none of them.

One code path: `read_knives` emits an op for every living enemy, with `needs: suppress`, and the engine drops
it for anyone immune at `t` (`engine.py:383-387`), as it already does for `knife_zero`. So an unhit ulting Veto
keeps his region with no special case in the reader.

The user's worked example: 4 hit, 1 outside, the 1 seen across the map. The 4 are restricted to the radius by
rule 1. The 5th is located by sight, and the radius exclusion from rule 2 also holds for them until that
sighting. So the team's unknown is just the radius (the 4's regions, spreading again from the pulse on), where
today it is wherever any of the 4 could have walked.

## Decisions

- **D1 (user, 2026-10-16): a hit enemy is restricted to the radius, not located.** Locating them (the way recon
  reveals are) was offered and declined. `knife_all` stays as it is.
- **D2 (settled by the user's request): partial knives now give information**, reversing item 10's "partial hits
  do nothing".
- **D3 (AFK run 2026-10-16, provisional, needs the user's approval): a 2 m rim margin** on every knife op (rule 6),
  from the hit-list check above. Setting `knife_rim_m` to 0 gives the plain radius.
- **Not done: a 3D volume for the excludes.** The footprint is a 2D disk on every floor. No measured case needed
  height, and neither measured map has heights; revisit if a multi-floor map shows it.

## Build

1. `read_knives` (`app/control/utility.py:214`): replace the `else: continue` branch. With some but not all
   enemies hit, each hit living enemy gets a `restrict` (`knife_hit`) and every other living enemy an `exclude`
   (`knife_missed`), all with `needs: suppress` (the engine drops an immune Veto's op). Restricts use
   r + `knife_rim_m`, excludes r - `knife_rim_m` (rule 6, also for `knife_zero` and `knife_all`); `detail` keeps
   `needs`, `hits`, `living` and adds the plain radius `r_m`. The knife's reasons become a module tuple
   (`KNIFE_REASONS`). Neither op is locating evidence, so `Unknown.events` and the gap detector don't change.
   Rewrite the `read_knives` docstring.
2. Reasons: add `knife_hit` ("knife hit them: inside its radius") and `knife_missed` ("knife missed them: not in
   its radius") to the labels in `app/static/js/replay_control.js:488`; the reason list in
   `tests/replays/test_control_reasons.py:105` reads `ut.KNIFE_REASONS`.
3. Tests (`tests/replays/test_control_knife_skye.py`, through `RoundInputs` as its `infos()` helper does):
   - `test_a_partial_knife_tells_nothing_not_even_about_who_it_hit` becomes: `knife([5])` →
     `[(5, "restrict", "knife_hit"), (6, "exclude", "knife_missed")]`;
   - `test_the_dead_dont_count_at_the_pulse`'s second assert (`deaths={6: 3.5}`, line 50) becomes the same pair;
   - `test_an_ulting_veto_keeps_his_region_and_makes_all_impossible`'s second assert (line 69): `knife([6])` with
     Veto 5 → `[(6, "restrict", "knife_hit")]` (Veto's exclude dropped by the engine); add Veto named in the hit
     list (`knife([5, 6])`, veto 5) → `[(6, "restrict", "knife_hit")]`;
   - the radius test: excludes carry r - rim, restricts r + rim, `detail["r_m"]` the plain radius, never below 0;
   - zero, all, allies in the hit list and unproven rows keep their current tests;
   - an engine-level test beside `test_a_zero_knife_clears_its_radius_from_the_unknown`: one enemy hit inside the
     radius, the other unhit with its track outside it; at the tick before the pulse the hit one's region has
     cells outside the restrict disk and at the pulse none; at the pulse the unhit one's region has no cells in
     the exclude disk (fails if restrict and exclude swap or the old `continue` returns).
4. Docs: amend item 10 of `docs/superpowers/specs/2026-10-05-replay-control-review-design.md` with a dated pointer
   to this plan; rewrite the test module's docstring.
5. Release: `CONTROL_REVISION` 16 -> 17 (`app/replays/control_format.py:47`) with its note; `PINNED[17]` in
   `tests/replays/test_control_format.py` is the new digest (`knife_rim_m` is a new figure);
   `tests/replays/test_control_figures.py:91` pins `(18, 17)`. `test_control_reference.py` needs no re-record
   (its toys have no knife). No re-condense: the hit lists are in condense revision 13+ rows.
6. Preview: one or two of the 23 partial knives in the local blobs, computed locally (no DB writes), before and
   after. **Merging is the recompute**: with `REPLAY_REPARSE_AUTO` on, the worker recomputes every stored round
   once revision 17 deploys (`app/services/replay_reparse_auto.py:574`). So the user approves the sample before
   merging, not after.

## Out of scope

- Haunt and recon partial hits already exclude their sight for the unrevealed enemies; nothing changes there.
- The "what the team knew" pictures read the same unknown, so they pick this up with no extra work.
