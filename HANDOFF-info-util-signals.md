# Handoff: info utility reported as locating enemies it never found

Written 2026-11-11 for an investigation that should become an `/afk` run (scope → plan → build). Nothing has been
changed in code yet. The playbook v10 proposal from the same session is separate and is not part of this.

## The problem

The coach's `knew` query, and Critical Moments' "found by utility" list, report a Haunt (and probably a recon bolt,
a KAY/O knife, a Skye flash, and some engine cleanup events) as having *located* enemies it actually ruled out.
Coach report 38 (match `2add00ce-5155-4815-9977-9dc1ad6c00c2`, Fade player) published "Haunt located all five
enemies" in 10 rounds. The owner marked 7 wrong and 3 corrected. The replay's own reveal rows agree with the owner:

| Round | `knew` signals with reason `haunt` | Reveal rows in `blob.util` (k = reveal) | Owner |
|---|---|---|---|
| 6, 11, 13, 16, 18, 20 | all 5 enemies | none | "scanned nothing" |
| 9 | all 5 | 1 (slot 5) | "scanned one" |
| 21 | all 5 | 4 (slots 1, 2, 3, 5) | "only 4" |
| 22 | all 5 | 1 (slot 5) | "only 1" |
| 25 | all 5 | none | "haunt is broken" (destroyed) |

So the reveal extraction (`app/replays/extras.py:find_reveals`) looks right. The fault is downstream.

## The cause (confirmed by reading the code, not yet by a test)

1. `app/control/utility.py:read_pulses` (≈ line 168) emits an `Info(kind="exclude", reason="haunt"|"recon")` for
   **every living enemy the pulse did not reveal**: "the Haunt's sight shows they are not in this ground". That
   is correct for the control engine.
2. The engine appends every applied Info to `self.reasons[side]` as `(t, slot, kind, reason, source)`
   (`app/control/engine.py` ≈ 2871; also 2664 `cleanup/died`, 2742 and 2756 `locate`, 2767
   `cleanup/hypothesis_cleared`).
3. `app/control/encode.py:knowledge_events` stores only `[t, slot, reason]`: **it drops `kind`**. So for
   `haunt`/`recon`, a locate (from `read_reveals`) and an exclude (from `read_pulses`) can't be told apart.
4. Readers that treat every stored event as "located this enemy":
   - `app/coach/knowledge.py:signals` → `knew`'s `signals`, whose note says "every stored event that located an
     enemy". It also computes a `rough_place` from the enemy's **true** position for each one, so an
     exclude leaks where an unrevealed enemy actually was (direction + distance band).
   - `app/services/replay_moments.py:player_info` → Critical Moments `found_by_utility` (everything that isn't
     seen/footsteps/gunfire/damage/kill, which includes `haunt`, `recon`, `knife_missed`, `knife_zero`,
     `skye_no_cue`, `hypothesis_cleared`, `died`, `omen_unheard`, `yoru_drift_unheard`, `omen_channel`, ...).
   - `app/static/js/replay_control.js` `REASON_TEXT` shows `haunt` as "Haunt saw the area". That wording is
     honest for both kinds, so the Control panel is probably fine. Check it anyway.

Info kinds by reason in `app/control/utility.py` (from `grep 'Info('`): locate = reveal reasons, `waylay_recall`;
restrict = `leer_seen`, `knife_hit`, `knife_all`; exclude = `haunt`/`recon` (pulse), `knife_zero`,
`knife_missed`, `skye_no_cue`; broaden = `omen_unheard`, `yoru_drift_unheard`; hypothesis = `yoru_beacon`;
pause/resume = `omen_channel`; retract = `omen_seen`. The engine adds `cleanup` (`died`, `hypothesis_cleared`).

## A second, related gap: destroyed own utility is invisible

In the same match, round 5, the Cypher player's A-side trip (`4_TripWire_SecondWire`, id 14456) has `gone: 17.219`.
It was shot, about 4 s before that player's kill on the enemy Sova at 21.497 s. The owner says the trip breaking
(plus noise) is why the fight was not risky. Neither `timeline` nor `knew` reports a destroyed trip or camera, so
the coach called it "a risky choice that worked". In-game, a destroyed trip tells the Cypher roughly where someone
is. Decide whether this belongs in control (a locate/restrict at the trip) or only in the coach's `timeline`
(a `destroyed` event). Check how `critutil/extract.py` already reads a device "broken" (≈ line 490) before
inventing a second detector.

## What the investigation should settle (in this order)

1. **Reproduce with a test** on the round-6 shape: a Haunt pulse with zero reveal rows must yield no `knew`
   signal and no Critical Moments `found_by_utility` entry for any enemy. Also round 21 (4 revealed → exactly 4).
2. **Audit every reason** in the table above: for each reader (coach `knew`, Critical Moments, Control panel),
   should it be shown, and with what wording? Mark which are "found", "ruled out", "roughly placed (radius)"
   and "lost track".
3. **Choose the fix's layer.** Options, cheapest first:
   - a. Readers only: drop exclude-only reasons, and keep `haunt`/`recon` only when a reveal row
     (`blob.util`, k=reveal, same by/target, |Δt| ≤ 1 s) backs it. No stored-format change, no recompute. The
     coach snapshot has `blob.util`, but Critical Moments reads only control headers, so it would need the reveal
     rows too.
   - b. Store the kind: `knowledge_events` rows become `[t, slot, reason, kind]`. That's a CONTROL revision bump,
     which means every row recomputes and the pinned control tests break (memory: revision bumps break
     `test_control_figures`, `test_control_format` digest and the replay_format/util pins). Old rows without a
     kind need a reader fallback (option a).
   - c. Rename the pulse's exclude reason (`haunt_clear`, `recon_clear`): also a revision bump, but simplest
     for every reader.
   Recommendation to test first: (c) or (b) for correctness, with (a) as the fallback for rows stored before.
   Measure the recompute cost before choosing; don't run a full recompute to find out (preview 1–2 rounds).
4. **Coach-side checks:** `app/coach/checker.py` treats a cited `knew` signal as evidence. After the fix, a
   published point citing a pulse's exclude must fail. Add a checker test.
5. **How far back is the damage?** List published coach reports whose text says a Haunt/recon/knife "located"
   or "revealed" enemies. Read-only, through `scripts/with_friends_db.py --read-only`. Reports are immutable, so
   this is only for the owner to decide on re-runs.
6. **Destroyed utility** (section above): what the replay records (`gone`, and who shot it, if the shot rows say),
   and where it should surface.

## Likely decisions for the AFK scope (tier them up front)

- Fix layer (a/b/c above): owner decision if it needs a CONTROL revision bump plus a full recompute.
- Wording of each reason in Critical Moments and the coach (`found` vs `ruled out`): can follow the audit table.
- Whether a destroyed trip/camera is a control Info (changes map control) or a coach `timeline` event only.
  A control change is the owner's call.
- Whether a `knife_hit`/`knife_all` restrict counts as "located" for the coach's "seen vs signal" rule
  (playbook rule 2 says "seen" only for `enemies`).

## Constraints and pointers

- Repo is public: no player names in committed files, docs or tests. Use slots or agents. Match UUIDs are fine.
- Run tests with `./.venv313-test/Scripts/python.exe -m pytest`. Pick suites with
  `python scripts/test_catalog.py plan --git`. New test files go into `tests/suites.toml`.
- Gap/control work: run the slow lane before merging. The worker image must speak any new protocol
  (`replay_worker/Dockerfile`).
- Never merge. The run ends at an open PR.
- Snapshot for local checking (no DB needed after this):
  `scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_snapshot.py
  2add00ce-5155-4815-9977-9dc1ad6c00c2 --out <dir>` (takes over 5 min), then
  `scripts/coach_query.py --run <dir> knew 6 16 A` and read `rounds["6"]["blob"]["util"]` rows with `k == "reveal"`.
- `critical_util` was missing for every round of this match, so the coach had no `util_facts` (which would have
  said who the Haunt hit). Worth a look at why the gap run didn't produce them, but that's a separate issue.
