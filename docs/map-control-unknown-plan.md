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
