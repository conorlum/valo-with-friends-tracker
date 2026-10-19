# Coach: the spike's defuse attempts and plant place in `timeline` (implementation plan)

**Goal.** The AI coach's `./q timeline` shows each defuse attempt (who, how long, tap / half / defused) and where the
spike was planted and by whom, so the playbook's post-plant principles (taps, halves, late sticks, spike play) can
be checked against data. The checker accepts captions citing them. No extractor change, no re-ingest, no
migration, no deploy switch (the coach runs locally; the site only renders stored reports).

## The data (checked on a real Summit snapshot)

Each round's `blob.util` holds at most one Bomb entry, written by `app/replays/extras.py:1715`:

```
{"k": "ability", "kind": "Bomb", "name": "Spike", "by": <planter slot>, "t": <plant s>, "t1": <end s | null>,
 "u": ..., "v": ..., "z": ..., "defuses": [[start, stop | null, defuser slot, finished], ...]}
```

8 of 38 rounds had attempts: one with a 1.89 s attempt then a 6.04 s unfinished one (the defuser killed at its
stop), one with 34 taps of ~0.07 s then a finished 7.0 s one, six with a single finished 7.0 s one. No stop was
null in that match; the extractor writes null when the attempt never ended in the recording. A detonation round
has the Bomb entry's `t1` exactly 45.0 s after its `t` (the spike timer).

The site DB's `plant` / `defuse` times (`snap.site(n)["db"]`) are already on the replay clock and differ from the
Bomb entry's by under 0.01 s.

## Rules (R1 settled; the rest are the agent's, logged in DECISIONS.md)

- **Kind (R1):** `defused` if the attempt finished; else `half` if it lasted at least `HALF_S = 3.5` s; else
  `tap`. Finished beats half. An attempt that fell short of 3.5 s is a tap whatever was intended. The length
  judged is the displayed `seconds` (rounded to 2 decimals), so the label never contradicts the figure and
  float noise (13.6 - 10.1 = 3.4999…) can't flip it.
- **Null stop:** the cap is `min(Bomb entry t1 if set, t_end)`. The attempt runs to the end of the defuser's
  alive interval that holds its start (`RoundView._intervals`, which includes site-only deaths) or the cap,
  whichever is first: `"until": "death"` when the life ends before the cap, else `"round_end"`. A start in no
  alive interval (a data glitch) runs to the cap with `"until": "round_end"`. Its kind is judged by R1 on that
  duration (never `defused`).
- **A resumed attempt** (any attempt after an earlier `half` in the same round) is labelled by its own length
  under R1, and carries `"resumed": true`. A resumed attempt that finishes is `defused`.
- **Spike time left:** `spike_left_s = Bomb entry t + SPIKE_TIMER_S (45.0) - attempt start`, rounded to 2
  decimals, on each attempt (attempts only exist on a Bomb entry, so the site DB's plant time is never used);
  omitted when the Bomb entry has no `t`. Not asked for in the goal, but "under-10-seconds sticks"
  can't be judged without it and the coach is told never to compute a time itself. Tier 2 (D-entry), as
  `PROVISIONAL`.
- **Event shapes** (all times and seconds rounded to 3 and 2 decimals respectively, as stored):
  - plant: `{"t", "kind": "plant", "by": <planter name | null>, "area": {"name", "super"} | null}`. `t` and `by`
    come from the Bomb entry, `by` only when its `owner_by` is in
    `app.replays.player_state_extract.PROVEN_PLANTER` (else null: `nearest` is a guess); `area` from `RoundView.area(u, v)` (the same nearest-callout lookup `where` uses).
    Without a Bomb entry it falls back to the site DB's plant time with `by` and `area` null. Bomb entry with no
    u/v: `area` null.
  - attempt: `{"t": start, "t1": stop, "kind": "defuse", "by": <defuser name>, "seconds": <stop - start>,
    "result": "tap" | "half" | "defused", "spike_left_s"?, "until"?, "resumed"?}`.
  - The site DB's single `defuse` event is kept only when no attempt in the Bomb entry finished (a fallback for a
    round whose Bomb entry is missing or disagrees).
  - The Bomb entry no longer appears a second time as a `kind: "ability"` "Spike" row in `timeline`
    (`RoundView.utility(spike=False)`); `utility()`'s default is unchanged, so the brief (`brief.py:48`) is
    unchanged.
- **Span cut:** an attempt is in `timeline(t0, t1)` when its start is in the span (as every other event).

## Steps

### S1 (W3) — `RoundView.spike()` and the timeline events

Files: `webapp/app/coach/roundview.py` (`utility()` at :126; new `spike()` and `defuse_attempts()`),
`webapp/app/coach/query.py` (`timeline` at :137). Tests: new `webapp/tests/coach/test_spike_timeline.py`,
built from `tests/coach/toys.py` blobs with a hand-written Bomb entry, through a snapshot as
`tests/coach/test_query.py:14-30` builds one; named areas by `monkeypatch.setattr(areas, "callouts", ...)` as
`tests/coach/test_checker.py:387`.

Toy shapes (synthetic names `P0`-`P9` only):
- a 1.9 s attempt then a 6.04 s unfinished one ending at the defuser's kill → `tap`, `half` (`resumed` absent on
  both: the half is the second);
- 34 taps of 0.07 s then a finished 7 s one, with the site DB `defuse` set to the finish → 34 `tap` and 1
  `defused`, and no site `defuse` event; the reverse: one unfinished attempt plus a site DB `defuse` keeps the
  site event;
- a single finished 7 s one → 1 `defused`, `spike_left_s` = Bomb t + 45 - start;
- a half (4 s, unfinished) then a 3.5 s finished one → `half`, then `defused` with `resumed: true`;
- boundaries from exactly representable times: 10.0→13.5 unfinished → `half`; 10.0→13.49 → `tap`; 2 s finished
  → `defused`;
- null stop: defuser dies 2 s in → `tap`, `until: "death"`; defuser alive, round ends 5 s in → `half`,
  `until: "round_end"`; Bomb `t1` earlier than `t_end` caps it; a start in no alive interval → cap,
  `round_end`;
- plant carries `by` and `area`: the Bomb's u/v at the toy B Site (8000, 8000) while side A stands on A Site, so
  an area taken from a player fails; `owner_by: "nearest"` → `by` null; no Bomb entry → site plant time with
  `by`/`area` null (the existing `test_timeline_has_the_plant_the_defuse_and_damage` still passes unchanged); no
  "Spike" ability row in `timeline`, but `view.utility()` still has it.

Check: `cd webapp; <python> -m pytest tests/coach -q` all pass, and the new tests were run red first. Mutants
(`mutant_check.py`, spec in the run folder): `>= HALF_S` → `> HALF_S`; finished checked after half; `area` taken
from the first player's position instead of the Bomb entry's u/v (or `None`); spike timer 45 → 40. All caught.

### S2 (W4) — checker accepts the new citations; the prompt line

Files: `webapp/app/coach/checker.py` (`_MEASURED` :23, `_SECOND_KEYS` :30: add `spike_left_s`, always: until
then it grounds nothing); `webapp/app/coach/prompt.py:12` (the `timeline` line). Tests: add to
`webapp/tests/coach/test_checker.py` (pattern: :385-398, the monkeypatched callouts and `_check`).

Tests: a caption citing a timeline answer passes for (a) "planted on A Site" (the plant's `area`), (b) "a 1.9 s
tap at 1:49" style text (`seconds` and the attempt `t`), (c) "with 15 s on the spike" (`spike_left_s`), and fails
for a duration not in the answer ("a 2.5 s tap"). The toy is built so no other cited value is within 0.5 of the
(c) figure or within 0.051 of 2.5, so (c) is red before the key is added. `test_prompt.py` still passes; the prompt line reads, in one
line: `kills, damage, utility, the plant (who, where) and each defuse attempt (who, seconds, tap/half/defused,
spike time left) in order`.

Check: `cd webapp; <python> -m pytest tests/coach -q` all pass; (c) red before the key is added (if it is
needed).

### S3 (W5) — real snapshot counts and the full suite

No repo change. Copy the run folder's `snapshot-ac9fd52a.json` to `<run folder>/real/snapshot.json` and run
`timeline` for rounds 2, 15, 17, 23, 25, 30, 35, 38 through `app.coach.snapshot.load` (as `scripts/coach_query.py:37`). Record in
`LOG.md` the counts by kind and result. Expected: round 2: 1 defused; 15: 34 tap + 1 defused; 30: 1 tap + 1 half
(the half ends at the defuser's kill time); 35, 17, 23, 25, 38: 1 defused each; each round one plant event with a
non-null area and planter. Then the full suite in the baseline's 6 parts (`prereqs.json`, each ≤ 420 s), one per
Bash call, with the known base failures deselected; this is also the wrap-up run, since nothing in the repo
changes after it.

Check: the counts equal the expected ones; the suite shows no failure outside the known list.

### S4 (W6) — PR body

`<run folder>/PR-BODY.md`: what changed, the real-snapshot counts (no player names), the test commands.

Check: the file exists and names no player.
