# Map-control bugs from the 2026-10-04 review: implementation plan

Branch `afk/2026-10-04-control-bugs` from `worktree-control-cases` (8b29c14). Python is
`webapp\.venv\Scripts\python.exe` of the main checkout, run from the worktree's `webapp\`. All checks below
are run from `webapp\` with that interpreter (written `py`).

Settled before this plan (the run's register): KJ deactivation comes from the replay signal, not a distance
(R2); images 9 and 13 are found by the run (R3); the round 4 corner case keeps passing (R4); small samples
only, no recompute, no reparse, no database (R1, R5); never edit `tags.json`, the tagger or geometry assets.

## What the KJ signal is (measured, both replays, every device)

`tmp_kj_probe.py`/`tmp_kj_rule.py` in the run folder dumped every effect RPC on every
`Pawn_Killjoy_E_Turret_C` and `Pawn_Killjoy_Q_StealthAlarmbot_C` (Sunset 0f452716: 32 devices; Lotus
a33bd0ff: 48). Container ids differ per replay (Sunset turret boot 4297, off 6019; Lotus 4699, 4997), so the
rule uses only the shape:

- **Boot**: the first continuous effect that starts 1.4-2.6 s after the device spawns (turrets 1.99-2.01 s,
  alarmbots 1.69-1.71 s). That effect is what "watching" looks like; its container is the boot container.
- **Off at T**: a watching effect is stopped and, **in the same millisecond**, a new continuous effect E
  starts, and E is not a trigger or an attack: E is not stopped within OFF_MIN_S (1.5 s), its first stop
  does not coincide (10 ms) with a one-shot on the device, and the device is still open 1.5 s after T.
  - turret attack (an enemy in front of it): a new effect starts 8 ms *before* the boot stops and its
    partner at the same ms is stopped 0.7-0.75 s later, then the boot container replays or the turret is
    killed: never off.
  - alarmbot trigger: the partner is stopped 0.4-1.7 s later together with a one-shot (the alarm going
    off): never off. (Sunset guid 24636 at 1.7 s and Lotus 27224 at 1.6 s are why the one-shot test is
    needed on top of the 1.5 s one.)
- **On again**: E is stopped while the device is still open. On every observed turret reactivation (Sunset
  37478, 46958; Lotus 27504, 32224) the boot container replays in that same millisecond, and that new boot
  effect is the watching effect from then on (Lotus 32224 goes off a second time 2.5 s later).
- **KJ's death** is not an "on": the off effect is never stopped at the death (Lotus 3504/3776 at 75972 ms,
  where Cypher killed KJ). The engine already stops every watcher at its owner's death.
- **Correction to the brief**: there is no alarmbot reactivation in either replay. The "alarmbot ~1.8 s"
  pattern in the brief is Lotus 3504 at 74212-75972 ms, a reactivation cut short by KJ's death. An
  alarmbot's on-again therefore uses the turret's rule (E stopped while open) unverified; see D-entry.

Result on all 80 devices: Sunset round 2's turret (guid 6666) is off from 188976 ms (21.6 s round time) to
its close; Lotus turret 32224 has two spans (+29.6 to +59.7 s, +62.2 to +68.3 s after spawn).

**Revised after the P4 review (IMPL-REVIEW.md, finding 1; D1).** The boot rule above, as first scripted,
read two turret attacks as offs and missed two real offs (Lotus 8394 at +43.9 s, after an attack had replayed
the boot; Lotus 49190 at +8.4 s, an off that began mid-attack). The rule built is:

- **Turret**: the *spawn effect* is the continuous effect played at the spawn ms (Sunset container 4265,
  Lotus 4677); "current" = the latest play in that container. Off at T: the current spawn effect is stopped
  at T, a continuous effect E in another container starts at exactly T, the spawn container does not replay
  at T (that is a reactivation), E is not stopped within OFF_MIN_S, and the device is open OFF_MIN_S past
  T. Attacks never stop the spawn effect (checked on every turret of both dumps).
- **Alarmbot** (no spawn effect): the boot rule above with 0 ms tolerance, "current" = the latest play in the
  boot container, plus the one-shot trigger test.
- **On again** (both): E is stopped while the device is open.
- Events are kept only for the two KJ archetypes and only within the actor's own life (GUID reuse).
- W3's real check prints the table for all 80 devices, checked by eye against kj_sunset.txt/kj_lotus.txt.

## Steps

### W3. Condenser: `off` spans on KJ turret and alarmbot rows

Files: `app/replays/extras.py` (read_raw keeps continuous plays/stops and one-shots per ability object with
effect id and container; a new `device_off_spans(actor, raw)`; `build_extras` writes `entry["off"]`),
`app/replays/format.py` (`CONDENSE_REVISION` 11 -> 12 with its comment; docstring: the `off` key),
pins `tests/replays/test_replay_format.py:61-62`, `tests/replays/test_replay_util.py:101`,
`tests/replays/test_control_store.py:416` (replace the literal `.c11.` with the current revision string),
new tests in `tests/replays/test_replay_extras.py`.

- `off` is `[[t0, t1 | null], ...]` in round seconds, clipped to the round like `wall_on`; the key is left
  out when there are none. Only the two KJ device archetypes get it.
- stdlib only (the upload worker copies `app/replays`).
- Unit tests on synthetic event lists (no export): off once; off then on (boot replays); off twice; attack
  (8 ms early partner, short) is not off; trigger (partner stopped with a one-shot) is not off; destroyed
  1 s after is not off; the death shape (off effect never stopped) stays off to the close; round end clips.
- Check: `py -m pytest tests/replays/test_replay_extras.py tests/replays/test_replay_format.py
  tests/replays/test_replay_util.py tests/replays/test_replay_condense.py tests/replays/test_control_store.py -q`
  passes (the store file holds the third pin); then
  `py <run>/tmp_w3_real.py` (feeds the two dumps to the pure rule) prints all 80 devices, Sunset 6666 off from
  188976 ms, Lotus 32224's two spans, 8394 and 49190's offs, and no off on 53774 / 24636 / 27224. Round time
  is W5's (from the condensed blob).

### W4. Engine: watchers don't watch while off

Files: `app/control/engine.py` (`Watcher.off`; `RoundInputs._watcher` reads `e.get("off")`, snapped, a None
end -> the watcher's own t1; the span edges join `self.events`; one helper `_watching(w, t)` used at the
three `w.t0 <= t < w.t1` sites, lines ~802, ~988, ~1717), docstring "Watchers" line;
`tests/replays/test_control_engine.py` (new tests).

- Rows without `off` behave exactly as today (old blobs keep "never off"; decision logged).
- CONTROL_REVISION stays 5: no constant changes, so the pin in `test_control_format.py` stays green; the
  docstring comment for 5 gains "and KJ devices' off spans".
- Tests (toy round from `control_toys.py`): a turret with an off span watches before, not during, again
  after; an alarmbot the same; the same row without `off` gives identical cells.
- Check: `py -m pytest tests/replays/test_control_engine.py tests/replays/test_control_format.py -q` passes;
  `py scripts/control_cases.py` all PASS.

### W5. KJ on the real round

Files: none in the repo; `<run>/tmp_w5_round2.py`.

- Condense the Sunset export locally with this branch (`condense_export_dir` into the run folder, the same
  call `scripts/ingest_replay.py` makes, no DB), take round 2's blob, build the engine's link from the public
  page (`preview_control_live.link_for`), build `engine.RoundInputs` and a `Tick` at 21.0 and 22.0 s, and
  print `tick._watch(7, t).sum()` (KJ = slot 7) and `rnd.alive(7, t)` for the new blob and for the public
  (c11) blob; also the turret row's `off` in round seconds (expect ~21.6). First confirm `check_manifest`
  accepts the local CliReader export.
- Check: the printed line shows non-zero then zero for the new blob. Then delete the export folders.

### W6. The hatched areas the user calls contested (images 5, 14, 15)

Files: `<run>/FINDINGS-W6.md`, `<run>/tmp_w6_decode.py`; a fix, if any, in `engine.py` with a test first.

- Compute Sunset rounds 2 and 7 with the current engine from the public site (as `control_cases.py` does) and
  decode the states and both unknown masks at 44.7-46 s (round 2), ~26.4 s and ~55.7 s (round 7). For each
  area say: Unknown (which side's), contested, or both; whether it follows the engine's rules; the cause.
  Positions of named players come from the blob's tracks.
- A rule bug: failing test first, then the fix, `control_cases.py` green. A viewer-legibility change
  (Unknown hatch read as contested stripes) is tier 2, built only if it is small and contained in
  `replay_control.js`/CSS.
- Check: FINDINGS-W6.md has one paragraph per image with cells, state and cause; any fix has a test that
  failed before.

### W7. Image 9 sliver (Unknown along a wall next to Osmin)

Files: `<run>/tmp_w7_find.py`; a fix in `engine.py`/`topology.py` with a test; a `clear` case in
`tests/fixtures/control/unknown_cases.json`.

- Search every round of 0f452716 for thin Unknown pieces (width <= 1 cell along their length, no enemy in
  them) within ~6 m of Osmin (slot from the blob, checked) while Osmin is alive. Report candidates (round,
  t, cells), pick the one matching "a thin strip along a wall where nobody fits".
- If it's an engine bug, fix it test-first; a new rule (for example "a strip no wider than X is dropped") is
  tier 2. The round 4 corner (3 cells) must still be unknown.
- Check: found (round, t, cells) or a card; if fixed, the new test passes, `control_cases.py` all PASS with
  the new case.

### W8. Image 13 box near NPrightdolphin (Cypher)

Files: `<run>/tmp_w8_find.py`, a CHECKLIST entry. No edits to tags.

- Find Cypher's death(s) in 0f452716; around each, look for a tagged or untagged box (geometry/tags assets,
  read only) within a few metres; compare what the engine treats as blocking with what the minimap shows.
- Check: a checklist entry naming the map, the box's px position and what to change in the tagger (or the
  engine misread, if the tag is right).

### W9. Viewer: clicking a round number plays it

Files: `app/static/js/replay.js` (a `playRound(n)` that shows the round from 0 and starts playing; the strip
buttons call it); `tests/replays/test_replay_viewer.py` (new node test calling `ReplayViewer.prototype`
methods on a stub viewer).

- Decided details: clicking any round, the current one included, restarts it from 0 and plays; prev/next
  buttons keep today's behaviour (paused); playback stops at the round's end as today.
- `playRound` sets `playing = true` in `showRound`'s `.then`. The test builds the strip with a stubbed
  `document`, fires a button's click listener, awaits, and asserts `playing`.
- Check: the new test fails on the old JS (`playing` stays false) and passes after; `py -m pytest
  tests/replays/test_replay_viewer.py -q`.

### W10. Morning checklist

`<run>/CHECKLIST.md`: tagger items (image 7 untagged box, image 10 low box tagged as cover, image 13), the
cards, local preview pages (paths under %TEMP%), and the run order: merge, reparse
(`webapp/scripts/reparse_archive.py`, CONDENSE_REVISION 12), then `compute_control.py`, each with its
prerequisite check; the timing-gaps branch conflict (topology.py/engine.py, CONTROL_REVISION 5).

## Suite

`tests/replays` takes ~8 min. Per item: its own tests and the touched modules' tests. After W4 and W7 and at
wrap-up: H1 = `tests/replays/test_control_*.py`, H2 = the other `tests/replays` files, each foreground,
deselecting `tests/replays/test_replay_worker_control.py::test_control_off_answers_404_and_parsing_is_untouched`.

## Risks

- The KJ rule is fitted on two replays (80 devices). A container pattern in other matches (a KJ ult, a
  suppressed turret) could look like an off; the 1.5 s and one-shot tests are what keeps triggers and
  attacks out. The report counts `kj_off_spans` so a reparse shows how often it fires.
- CONDENSE_REVISION 12 makes every stored round stale for the reparse; the control store requires
  `MIN_CONDENSE_REVISION = 10`, unaffected.
- The timing-gaps branch also edits engine.py/topology.py and uses CONTROL_REVISION 5.
