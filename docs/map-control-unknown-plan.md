# Map control "unknown": design (settled 2026-10-01)

Settled with the user in a grilling session on 2026-10-01, after looking at round 1 of Ascent replay
`6f12db3e-b2db-4bca-96e4-a837c85ba5a6` computed with `worktree-control-fixes`' engine (revision 3, unreleased).
The implementation plan is `docs/superpowers/plans/2026-10-01-map-control-unknown.md`.

## The problem it fixes

1. **The defenders' spawn isn't held.** Barrier-drop ground is remembered ground (D6), and remembered ground is
   eaten after `BARRIER_GRACE_S` by any *open* cell next to it (walkable, not watched, not remembered), at
   `DECAY_MPS`. Neutral mid-map is open, so it eats the spawn from the front whether or not an enemy could be
   there. Measured on round 1: the defenders keep 2,697 cells at the drop, 1,067 at 18 s, 2,516 at 20 s, 506 at
   24 s.
2. **A glance claims everything.** The jump at 18-20 s above is a Miks glancing at A: the look, plus backfill
   behind it, claims all of A site and spawn when an attacker could have walked in.

Both come from asking "is anyone looking at this?" instead of "could an enemy have got here yet?".

## The rule

**Unknown for team T** is the set of cells where an enemy of T could be. It is per team.

1. **Sources.** The enemy's live players push it out from their true positions, every tick. At the barrier
   drop, T's unknown is the enemy's side of the barriers (the enemy's start ground). It also spreads from
   itself.
2. **Speed.** It spreads at shift-walk with a rifle out, `UNKNOWN_MPS` = 3.24 m/s (silent movement; was 3.5,
   changed 2026-10-01: measured from replay tracks, walk is 0.60 x run in every tier: knife 6.75 -> 4.05,
   pistol 5.73 -> 3.43, rifle 5.40 -> 3.24; the user chose rifle-walk over a knife/gun toggle), through walkable
   cells. Everything in it must be reachable at that speed (the user's rule, 2026-10-01): each unknown cell
   keeps the earliest time an enemy could be there, and a neighbour joins at that time plus the step's true
   length (one cell straight, sqrt(2) diagonal) over the speed. It replaced whole 8-connected steps per tick,
   which ran diagonals ~41% fast (in round 2 of the sample, 20% of cells turned unknown over 0.5 s before a
   true walk could reach them). Ground T watched on one tick and not the next is entered from the later tick
   on, never earlier. Walls stop it; smokes don't (you can walk through a smoke), but a pinch between a
   smoke's edge or a wall ability's line and the map's wall narrower than `GAP_SEAL_M` = 1.5 m is sealed
   (the user's call, 2026-10-01: in round 2 of the sample, Viper's screen passed 1.26 m from a wall corner
   5 m from Momomimo and the unknown squeezed through it into the pocket behind the screen; anyone doing
   that would be seen). A pinch is the shortest segment from the blocker's edge to the wall's face that
   crosses open floor (its middle a third of its length from any wall), so a screen across a door or a
   smoke filling a corridor seals nothing: the gas stays walkable. It follows the map's
   specials (teleporters, ropes, drops) the way the Safe fill links them, as one straight step; a one-way
   special carries it one way.
3. **Clearing.** T's live control clears it on contact and stops it: the active and passive vision of T's
   players, T's watchers (trips, cameras, turret, drones, alarmbots), and each T player's own cell. A reveal
   counts only for the cells it saw. An enemy T sees pushes out nothing until they step out of sight.
4. **Remembered ground.** Ground a T player saw and looked away from stays T's passive until T's unknown
   reaches it; unknown replaces the old decay (`DECAY_MPS`) and nothing else. The barrier drop gives each team
   its side as remembered ground, and the 5 s grace (`BARRIER_GRACE_S`) is removed: unknown's walking time does
   that job.
5. **Backfill** never claims a cell in the team's unknown.
6. **Safe.** T's ground is Safe when no cell of T's unknown has line of sight to it (smoke-aware). This replaces
   the instant flood from the enemy's players.
7. **Each enemy on their own** (changed 2026-10-01, after round 4 of the sample at 56.5 s: S1mpLy, the
   attackers' last player, was in NPrightdolphin's sight and the defenders' unknown still covered ~4,880
   cells). T's unknown is the union of one unknown per live enemy. An enemy T spots (in a T player's active
   sight or a T watcher) can only be where they stand: theirs starts again from that cell and time, and walks
   out from it once they are out of sight (the sighting is a source from its own time, even on ground T only
   just stopped watching). With that tracking a dead enemy's unknown goes with them; this replaces the first
   rule here, "a dead enemy stops pushing; what is out stays", which was there because one shared unknown
   couldn't tell whose ground was whose. Measured on that moment: 0 cells while S1mpLy is in sight, where it
   was ~4,880; the unknown part of the round's compute went from 0.2 s to 0.7 s (of ~100 s). Remembered
   ground still dies with its player.
8. **Crumbs are dropped** (2026-10-01, round 3 at 98.0 s: vision had eaten a pocket down to 2 cells and it
   grew back to 15 as the ground round it was freed). A piece of a team's unknown (8-connected) of at most
   `DROP_PIECE_CELLS` = 2 cells (a 1x2) with no live enemy standing in it is dropped for good, from every
   enemy's unknown (and a sighting inside it with it). A piece with an enemy in it stays however small:
   an enemy just out of sight is a 1-cell piece, and dropping it every tick would mean they never make
   any. Measured on the whole sample (22 rounds, both teams, 13,350 team-ticks) before the change:
   enemy-free pieces of at most 1 / 2 / 3 / 4 / 6 / 8 / 12 cells came up 2,113 / 3,026 / 3,985 / 4,811 /
   6,533 / 7,613 / 9,746 times; at 2 cells, 614 of them grew on the next tick and 216 were part of more
   than 50 cells on it (joined the main unknown as watched ground was freed: the cost of the cap).
9. **Views.** The same unknown is used in the true view and in both "as Team X knew it" views.

## Drawing

Each team's unknown is hatched in the **enemy's** colour: A's unknown along "/" lines in B's colour, B's along
"\\" lines in A's colour, so cells in both teams' unknown are cross-hatched. A toggle next to the Map control
picker: Unknown off / Team 1 / Team 2 / both (default both). Shown only for rows stored with unknown.

## Out of scope (later)

- **Stretch:** spread at running speed (6.75 m/s) when the enemy is out of earshot of the team.
- A gap stat (m² of a team's unknown inside its own ground) or per-player numbers. This build is visual plus the
  decay replacement; control, taken and the tables change only through remembered ground ending differently.
- Reconsidering Safe further, or unknown built from last-seen points in the knowledge views.

## Process

- Built on `worktree-control-fixes`, still revision 3 (unreleased): re-pin its constants digest in place.
- Checked on a very small sample: rounds 1-2 of the Ascent replay above, computed locally from the live site's
  public data (no DB writes), against the user's two screenshots. **No full-corpus recompute until the user says
  the sample looks right.**
- Storage: two more optional streams per row; whatever size they cost is accepted, measured and reported.

## Decided while building (2026-10-01)

- **Control's counterfactual.** Unknown comes only from its sources (the unknown already out and the enemy's
  players). Without a player there is less live control to hold it back: what the sources can walk to without
  them, but not with them, joins the unknown for that counterfactual. Ground it could reach anyway but hasn't
  yet isn't the player's to hold. (`Tick.unknown_without`.)
- **The whole enemy team dead:** that team's unknown clears to nothing.
- **Maps without barrier paint** (Bind, Breeze, Corrode, Fracture, Icebox, Lotus, Pearl at the time): the user
  painted them the same day, so every map now has barriers. A map without paint would start its unknown from
  the enemies' own cells only.
- **Missing thin walls** (Ascent, round 1 at 51.7 s: Osmin saw through one) are map tagging, not engine: the
  user painted cover on Ascent's thin walls. Four strokes ran into doorways and blocked real kill lines
  (centre-to-centre, non-wallbang; 25/476 against the 2% bar); their door ends were trimmed by 4-20 px to 6/476.
  Two of them (x 272-287 and 312-335, y 528-535) kept only 4 px each: worth a look in the tagger.
- **Elevation the 2D map can't show** (round 3 at 80-82 s: D, running at (418, 268), had the far room past
  the doorway at (145-187, 380) as passive sight 40 m away, and it came out contested; the user: not
  visible in game, because of elevation). The rays thread between two wall corners within the 0.3 m
  tolerance. Painting the whole stair edge they cross (y 309, x 326-369) blocked a real kill line
  ((355, 317) to (436, 165)). The same lines come from 17 of the 158 cells of that upper corridor
  (x 368-435, y 200-367; S1mpLy at (424, 282) at 79.5 s too), and all of them cross the stair edge at
  x 328-358, the kill line at 359. So: cover on the stair edge x 324-355 (y 308-311), plus one 4 px
  cell at each corner the rays thread (272-275, 332-335) and (184-187, 376-379) for the last 3. Cells of
  the upper corridor that see into the room: 26 to 0; kill lines still 9/476; 4 walkable cells lost
  (cover blocks walking too, so the edge is crossed only at x 356-369 now).
  **Reverted 2026-10-02** (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 5): the stroke
  and the corner cells are out of Ascent's `cover_paint` again (5,220 walkable cells, kill lines 9/476), since
  players walk on that edge. The sightline is wrong again until the map has heights.

- **Presence bubble** (after the first look): each live player holds the walkable ground within `PRESENCE_M`
  = 4 m that they can walk to and see (all round them, smoke-aware: anything that blocks sight stops it) as
  passive, so unknown can't slip past within reach. Tried 2, 4 and
  6 m on Ascent round 1 (Momo, 15-18 s): 2 m let unknown wrap behind her; 4 and 6 m held it on one flank; 4 m
  chosen "for now". Kept while concussed or revealed; not while flashed.
- **Neutral ground:** where both teams would be Safe (neither unknown sees it), it is nobody's, not contested.
- **A seen player** (a duel) has their lines contested only where their team's unknown is, and never their
  remembered ground (that flashed whole rooms to contested for a tick: round 2, 48 s).

## What the sample showed (Ascent 6f12db3e, rounds 1-2, computed locally, no DB writes)

- Compute: r1 86 s, r2 123 s (revision 3 before unknown: 82 s and 103 s).
- Size: r1 283 KB gzipped (unknown_a 54 KB, unknown_b 47 KB raw); r2 315 KB gzipped (unknown_a 61 KB,
  unknown_b 49 KB raw). Before unknown, revision 3's r1 was larger (its states stream alone was 380 KB raw).
- Defenders' start ground kept, round 1 (of 2,924 cells): 0 s 2,924; 2 s 2,743; 4 s 2,741; 6 s 2,681; 8 s 2,577;
  10 s 2,428; 12 s 2,512; 14 s 2,234; 16 s 1,815; 18 s 1,517; 20 s 1,808; 22 s 1,288; 24 s 542; 26 s 764.
- What it loses goes to the defenders' unknown, not to the attackers: of those cells, 290 are in B's unknown at
  10 s, 690 at 14 s, 1,407 at 18 s, 1,116 at 20 s (the Miks glance clears what it sees), 1,636 at 22 s and
  2,298 at 24 s; attacker-held stays under 200 throughout.
