# Replay starting barriers and player state: results and refresh runbook

Closeout of `2026-10-06-replay-player-state-impl.md` (its task P09). Built on branch
`afk/2026-10-07-replay-player-state`; nothing here has been merged, deployed, re-parsed or recomputed.

Revisions on this branch: `CONDENSE_REVISION` 15 (was 14), `CONTROL_REVISION` 9 (was 8), `GAPS_REVISION`
unchanged. Both numbers were still unused on `origin/main` at `f776165`, and a trial merge with it was clean.

All real-data evidence comes from one replay: Abyss `7a278f4b-ace8-49df-b176-9101c63c8808`, from its local
export of 2026-10-05 (parser `2b66c65`), re-condensed locally at CONDENSE 15. Rounds 1 and 5 are the reported
ones; round 18 holds the match's only damaging molly. No player names appear in this document. The preview
pages and screenshots name players, so they stay in the run folder and aren't committed.

## Result per request

Dispositions: **done** means implemented and checked. **done, partial** means the part the replay proves is
done and the rest is blocked by evidence that is named. "Real data" means the local re-condense of the replay
above. "Fixtures" means tests only.

| Request | What | Disposition | Evidence and limits |
| --- | --- | --- | --- |
| BUG-01 | Abyss starting barriers let the attackers' start ground leak forward | done | Cause 2 of the design's four: two painted strokes of the attackers' lower-right barrier stopped 33 px short of each other, so their start ground ran into the centre and lower corridors (2,939 cells, should be 1,358). The strokes are now joined. The sight, walk and bullet masks are byte-identical, and the Abyss index row changes only `barrier_sha`. Defense is unchanged at 3,290 cells. No other map changes. Round 1 previewed at 0, 4 and 8 s |
| BUG-02 | The round 5 blind: the condition shows, and a blinded player holds no control | done | Real data: a Skye flash hits the Phoenix (B) at 18.626 s for 1.615 s. The blind ends at 20.242 s per the replay's blind manager (20.241 computed). A teammate's 0.05 s hit gives 18.634-18.684. A BLINDED chip shows whether abilities are on or off. While blinded or nearsighted (exact `[t, t+dur)`, not rounded to a frame), the player contributes no sight, memory, presence, backfill, sole-survivor credit or device credit. Their own cell still blocks the enemy's unknown. Fixtures cover overlapping flashes, death mid-flash, a zero-length hit and the sole survivor. The `midwall` reference was re-recorded deliberately, with independent checks |
| REQUEST-03 | A health bar under each player | done, partial | Real data: HP and shield after every hit, 934/934 hits decoded (`sum(-delta) == damage` on all of them). Max shield comes from the armour bought (168/168 tied to the buyer). Max HP is 100 by rule (202/202 first hits). Bar = `100 * (hp + shield) / (100 + max shield)`, hidden until the player's first hit. The tooltip says "as of the last hit at m:ss". **Heals, regen and overheal have no records** (the parser declares them but they have 0 rows), so a heal shows only at the player's next hit. Round 5 shows 82% and 36% at 19 s. Round 18 shows four bars at 14 s |
| REQUEST-04 | Spike: who carries it, where it lies | done, partial | Real data: exact spike state times (spawned, carried, dropped, planted, defused) and every plant start, cancel and completion with the planter. The carrier is shown only where the replay proves it: from the last pickup to the plant (round 5: slot 9 from 49.794 s; round 18: slot 3 from -21.4 s). Exactly one spike glyph shows at every moment, and the post-plant HUD is unchanged. **Blocked by evidence:** the first carrier and a dropped spike's position need a parser change (`ItemSlot.Contents`, the spike's `ReplicatedMovement`). A death position isn't a substitute: in round 5 it is 238.7 units off and 400 units lower |
| REQUEST-05 | A damaging molly stops the enemy's unknown spreading through it | done | While a team's own damaging molly burns, the area where that team thinks an unseen enemy could be can't spread into it, across it or diagonally past it. Ground already covered inside stays covered, and the area only spreads on once the fire is out, from that moment. Allow-list: the five sustained damage zones. Real data: Hot Hands in round 18 at 12.0 s. Its burn time (4 s) and the 4.5 m radius are **provisional**: the replay records neither (its row ends 7.2 s after landing, when the object closes). Fixtures: mirrored teams, frame-boundary lifecycle, earliest arrival after expiry, full vs incremental and cached vs live parity, and an unchanged no-molly round |

Judged real-round control cases (`scripts/control_cases.py`): 12/12 pass at CONTROL 9, including the two
local Summit cases re-condensed at CONDENSE 15.

Round-size cost of `player_state` (gzip): Abyss round 1 +461 B (0.7%), round 5 +569 B (1.5%), Summit round 7
+377 B (1.4%).

## Previews made

`scripts/preview_control_live.py <uuid> 1 5 18 --blobs <dir>` (new `--blobs` mode: the round blobs come from a
local re-condense, the link data from the public page, and a missing local round is an error) rendered rounds
1, 5 and 18 with this branch's engine, at CONTROL 9, from the CONDENSE 15 blobs. All three computed in 232 s.
Screenshots were taken in headless Chromium at these points:
- round 1 at 0, 4 and 8 s (the barrier drop);
- round 5 at 18.7 s (blinded, with and without the blinded player's control selected), 19 s (health tooltip),
  21 s, 22.95 s (spike dropped, place unknown), 55 s (carrier badge) and 80 s (planted);
- a backward seek from 80 s to 18.7 s, which drew a canvas identical to a forward one;
- round 18 at 11.5, 14 and 20 s (before, during and after the molly), with each team's unknown layer.
No page logged a console error.

## What needs which refresh

- **Viewer only:** the chip, health bar and spike layout ships with the code. Blobs from before CONDENSE 15
  have no `player_state`, so they play as before, with no bars and no carrier badge (tested).
- **Re-condense first (CONDENSE 15), then control:** health and spike state for any stored match.
- **Control recompute only (CONTROL 9):** the blind rule, the molly rule and the Abyss barrier. The molly figures
  and the allow-list are in control's fingerprint, so changing a number later makes control stale again. Editing
  only a comment doesn't.
- Timing gaps follow control. A round whose control fingerprint changed has stale gaps, and they're recomputed
  after it.

## What merging starts by itself

This is read from `origin/main`'s `render.yaml`: both services have `autoDeploy: true`, and
`REPLAY_REPARSE_AUTO`, `REPLAY_CONTROL_REMOTE` and `REPLAY_HEIGHTS_AUTO` are all `"true"`.

1. **Re-parses (CONDENSE 15).** After the deploy, the worker re-parses archived uploads by itself, one at a
   time, behind any new upload. A replay waiting for its re-parse has no rounds sent for control. Matches with no
   archived `.vrf` keep their CONDENSE 14 blobs and show no health bar or spike carrier. These are the 10 older
   local replays and the Lotus replay without kill lines.
2. **Control and gaps (CONTROL 9).** Every stored control round on every map now has an out-of-date
   fingerprint. The web app's control cycle (`app/services/replay_control_remote.py`) submits every round
   `plan()` lists as `stale` to the worker, newest linked match first, running only while the worker isn't
   parsing. Gaps follow each round. So **the whole corpus is recomputed by the worker without anyone running a
   command.** Until a round is recomputed, its old control is shown, flagged as older.
3. Nothing in this build writes to the demo database.

To count the live backlog before merging (read-only, run by the owner):

`python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/compute_control.py --dry-run --brief`

## Targeted refresh manifest (none of it has been run)

Only needed to check a sample by hand before or alongside the automatic pass.

| Replay | Rounds | Why | Input revisions expected after | Source |
| --- | --- | --- | --- | --- |
| Abyss `7a278f4b-…8808` | every round | the barrier is in every Abyss round's fingerprint | blob c15, control r9 | archived `.vrf`, checked against the stored `source_sha256` before use |
| same | 1 | BUG-01 at the drop (0-8 s) | blob c15, control r9 | same |
| same | 5 | BUG-02 blind 18.626-20.242 s; health; spike drop at 22.879 s and plant at 79.814 s | blob c15, control r9 | same |
| same | 18 | REQUEST-05 Hot Hands at 12.0 s | blob c15, control r9 | same |
| every other Abyss replay | every round | the barrier | control r9 | the automatic pass |
| every other map | every round | CONTROL 9's wider staleness (blind and molly rules) | control r9 | the automatic pass; not refreshed by hand |

Order:
1. Merge. Both services deploy from the same commit, the worker image included.
2. Read-only, for the sample's control:
   `python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/compute_control.py --dry-run --match 7a278f4b-ace8-49df-b176-9101c63c8808 --round 1 --round 5 --round 18`
   This doesn't re-condense anything. It only shows what control would compute.
3. A re-condense of one chosen match by hand is **not supported yet**. `scripts/reingest_replays.py` has no
   match filter: it selects every stale candidate in the archive. Don't run it to refresh one match. If a
   targeted re-ingest is wanted ahead of the automatic re-parse, it needs a tested `--match` or manifest filter
   first, with dry-run parity and a refusal of anything outside the list. Keep whole-match completeness: never
   replace part of a match through `store_replay`.
4. Once the match's blob is at c15 (re-parsed by the worker), its control and gaps follow automatically. Check
   round 1 at 0-8 s, round 5 at 18-23 s and round 18 at 11-20 s by eye on the site.
5. `python scripts/control_cases.py`: the 10 live judged cases.

**Rollback inputs:** revert the two bump commits (CONTROL 9: `ba9a256`; CONDENSE 15: `eabca7b`, which also
holds the extraction) and the rule commits. The re-parse keeps the source `.vrf` files, so an older condenser can
re-parse again. The local CONDENSE 15 blobs of rounds 1, 5 and 18 and the CONDENSE 14 export are kept outside
the repo for comparison.

## Still open

- **Spike carrier and drop position** need a parser session: decode `ItemSlot.Contents` and the spike's
  `ReplicatedMovement`, rebuild, re-export one replay and check it.
- **Heals** have no records, so the bar follows a heal only at the next hit.
- **Molly figures** are provisional (burn times for all five, the 4.5 m radius). An in-game test settles each.
- **Only Hot Hands** appears in the sample. The other four mollies have fixtures only.
