# Replay utility and map-control review: results and refresh runbook

Closeout of `2026-10-05-replay-control-review-impl.md` (its task W24). Built on branch
`afk/2026-10-05-utility-review`; nothing here has been merged, deployed or recomputed.

Revisions on this branch: `CONDENSE_REVISION` 13 (was 12), `CONTROL_REVISION` 7 (was 6), `GAPS_REVISION` 2
(unchanged). The game figures the engine reads (`app/control/hearing.json`, `app/control/utility.json`) are part
of control's fingerprint, so a changed number makes control and gaps stale and an edited citation does not.

## Result per checklist item

Dispositions: **done** = implemented and checked; **stale** = already correct in the engine, the reviewed page
showed an older computation or was read differently; **non-use** = confirmed not to apply; **blocked** = still
blocked by specific missing evidence. "Real data" means a locally exported replay; "fixtures" means tests only.

| Item | What | Disposition | Evidence and limits |
| --- | --- | --- | --- |
| 2 | Space anywhere on the page plays/pauses | done | Node viewer tests (body, controls, tabs, form fields); in Chrome a Space on the page toggled play once |
| 3 | Next round starts by itself, stops after the last | done | Node viewer tests (next round, final stop, stale load, pause during load, failed load). Not watched in a visible browser tab |
| 4 | Haunt reveals what it sees | done | Reveal locates the revealed enemy at the event's own time; pulse clears what the eye sees. Range 30 m is unsourced (provisional). The reviewed replay is on Summit, not Abyss |
| 5 | Suppressed Cypher devices switch off | done | Devices hold nothing while their owner is suppressed and come back after; drawn as off in the viewer |
| 6 | Trip seals the reported corner | done | A real defect: a one-cell gap beside the wire on flat maps. The wire now joins the nearest wall within 2 m. Judged case `summit-r6-trip-seals-the-corner` |
| 7 | The unknown collapse at 21.5 s | stale | A real camera sighting of four enemies; the engine was right. The Control tab now lists why the unknown changed |
| 8 | Unknown walks through a box | done | The box is on Summit (round 7, 13.5 s). Marked walk-blocking only; sight unchanged. Whether it is tall is the owner's call |
| 9 | Neural Theft locates every living enemy at each ping | done | Real data: local judged cases on Summit round 7 (1,725 -> 177 unknown cells at the first ping). Veto's immunity: see item 19 |
| 10 | Knife pulses: none, all or some enemies hit | done | Fixtures for zero, all and partial hits, deaths at the pulse, unproven rows. An old row without a proven pulse changes nothing |
| 11 | Tracers stop at the first wall | done | A bullet mask per map (13 maps); low see-over boxes stop a shot, open drops do not; no 25 m cap |
| 12 | Omen's ult: seen, heard, completed, cancelled | done | Fixture matrix; real data has 4 completed ults. Hearing range 50 m is unsourced (provisional) |
| 13 | Recon and drone sight | done for Recon Bolt pulses; blocked for drones | Pulses clear from the bolt's own place and height. No drone watcher was added: no local replay with a decoded drone view |
| 14 | Skye's flash tells her whether it hit | done | A hit is a cue even at zero duration; a proven empty pop clears what it saw; incomplete rows leave the unknown alone. Range 20 m is unsourced |
| 15 | KAY/O sees while alive; his ult pulse reveals nobody | stale | Already true; pinned by tests |
| 16 | Yoru's beacon | blocked | The rule is built and tested but not switched on: no decodable beacon signal in any local export |
| 17 | Yoru's drift exit | blocked | As item 16 |
| 18 | Phoenix's return marker makes no unknown | stale | Already true; pinned by a test. Real data has 4 return points |
| 19 | Veto's Evolution immunity | blocked on real data | The capability matrix is built on a provisional reading (any ult-slot object of his marks the span). No local Veto replay |
| 20 | Phoenix's wall | done | The wall's points are kept (7 real lines); it blocks sight only, across map walls. Straight segments between kept points |
| 21 | Waylay's recall | done | Heard at start or end restarts the unknown there; unheard leaves it. Real data: 9 recalls. Hearing range 50 m is unsourced |
| 22 | Missing abilities are drawn | done | New rows drawn by the viewer; see `W2-utility-signals.md` in the run folder for the per-ability table |
| 23 | Projectiles | done | Flights kept (75 on one Summit replay) and drawn, on by default with their own toggle |
| 24 | Deadlock's utility is recognisable | done | Mesh wall with its four arms, sensors, GravNet; drawn from real data |
| 25 | Deadlock's walls and sensors are extracted | done | 8 mesh walls / 32 nodes and 26 sensors on one Summit replay. 4 nodes of one wall have no place (the round ended first) and are reported, not guessed |
| 26 | A sensor does not stop silent movement | done | Sensors hold nothing against the unknown |
| 27 | Intact, part-broken and broken walls | done | Sage segments, mesh arms; checked across control, knowledge, the counterfactual and gaps. The incremental counterfactual matches the full one across a breaking wall |
| 28 | Vyse's Shear | done on fixtures; blocked on real data | Before, raised and ended states are built. It was never triggered in any local replay |
| 29 | Leer through walls; the big orange area | done / stale | The seen-or-unseen Leer rule is built. The large orange area was orange's own Safe ground, not unknown |

## What needs which refresh

- **Viewer only** (items 2, 3, 11, 22-24, the reasons list's layout): live on deploy. Tracers need the new
  `<Map>.bullet.png` assets, which ship with the code. Old blobs draw as before; new shapes appear after a
  re-condense.
- **Control recompute only** (no new extraction needed): reveals locating enemies (4, 9), Recon pulses (13),
  suppressed Cypher devices (5), the observed Leer (29), the trip join (6), the Summit box (8), the reasons list
  (7). Rounds computed at revision 6 are served flagged as older until recomputed.
- **Re-condense first, then control** (the rule reads rows only revision 13 writes): knife pulses (10), Skye pops
  (14), Omen outcomes (12), Waylay recalls (21), every ability wall (20, 25-28), projectiles and flash paths
  (23). **A control recompute alone does not recover these.**
- Gaps follow control: a round whose control fingerprint changed has stale gaps and is recomputed after it.

## Refresh order (none of it has been run)

1. Merge and deploy. Old blobs and old control stay readable; stale control is flagged, not hidden.
2. Check what a control recompute would touch, read-only, for one round first:

   `python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/compute_control.py --dry-run --match <uuid> --round <n>`

   then by map (`--map Summit`) for the box and trip changes. Read the counts before anything is written.
3. Re-condense from the archived `.vrf` files where extraction matters: `python scripts/reingest_replays.py
   --dry-run` lists what the archive holds; check each file's SHA-256 against the stored `source_sha256` before
   using it. No parser rebuild is needed (the condenser reads the same export). A replay that arrived by upload
   and is not in the local archive goes through the upload worker's path instead.
4. Recompute control for the re-condensed rounds, then gaps for those rounds.
5. Verify a sample: `python scripts/control_cases.py` (9 live cases) and one or two rounds by eye.

The full-corpus recompute is a separate decision. Nothing is written to the demo database.

## Still open

- Unsourced figures (provisional): reveal ranges, Skye's flash range, Leer's cast range, the hearing range of
  Omen's ult, Yoru's beacon and drift exit, and Waylay's recall. A short in-game distance test settles each.
- Missing evidence: a replay with Veto's ult, Yoru's beacon and drift, a triggered Shear, and a drone in use.
- Blob size: Abyss's 95th-percentile round is 70.5 KB against the 70 KB reported budget (+1.4%).
