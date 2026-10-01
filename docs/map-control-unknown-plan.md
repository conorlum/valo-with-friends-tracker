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
2. **Speed.** It spreads at Valorant's shift-walk, `UNKNOWN_MPS` = 3.5 m/s (silent movement), through walkable
   cells in 8-connected steps. Walls stop it; smokes don't (you can walk through a smoke). It follows the map's
   specials (teleporters, ropes, drops) the way the Safe fill links them; a one-way special carries it one way.
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
7. **Deaths.** A dead enemy stops pushing out unknown; what is already out stays until T clears it. Remembered
   ground still dies with its player.
8. **Views.** The same unknown is used in the true view and in both "as Team X knew it" views.

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
