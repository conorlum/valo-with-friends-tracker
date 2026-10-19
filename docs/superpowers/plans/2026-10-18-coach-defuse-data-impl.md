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
  `tap`. Finished beats half. An attempt that fell short of 3.5 s is a tap whatever was intended.
- **Null stop:** the attempt runs to the defuser's death (the end of the alive interval it falls in, via
  `RoundView._intervals`, which includes site-only deaths) or the round's `t_end`, whichever is first. Its kind is
  judged by R1 on that duration (never `defused`), and the event says `"until": "death" | "round_end"`.
- **A resumed attempt** (any attempt after an earlier `half` in the same round) is labelled by its own length
  under R1, and carries `"resumed": true`. A resumed attempt that finishes is `defused`.
- **Spike time left:** `spike_left_s = plant t + SPIKE_TIMER_S (45.0) - attempt start`, rounded to 2 decimals, on
  each attempt; omitted when no plant time is known. Not asked for in the goal, but "under-10-seconds sticks"
  can't be judged without it and the coach is told never to compute a time itself. Tier 2 (D-entry), as
  `PROVISIONAL`.
- **Event shapes** (all times and seconds rounded to 3 and 2 decimals respectively, as stored):
  - plant: `{"t", "kind": "plant", "by": <planter name | null>, "area": {"name", "super"} | null}`. `t` and `by`
    come from the Bomb entry; `area` from `RoundView.area(u, v)` (the same nearest-callout lookup `where` uses).
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
- 34 taps of 0.07 s then a finished 7 s one → 34 `tap` and 1 `defused`, and no extra site `defuse` event;
- a single finished 7 s one → 1 `defused`, `spike_left_s` = plant + 45 - start;
- a half (4 s, unfinished) then a 3.5 s finished one → `half`, then `defused` with `resumed: true`;
- boundaries: 3.5 s unfinished → `half`; 3.49 s → `tap`; 2 s finished → `defused`;
- null stop: defuser dies 2 s in → `tap`, `until: "death"`; defuser alive, round ends 5 s in → `half`,
  `until: "round_end"`;
- plant carries `by` and `area` (`{"name": "A Site", ...}` for u/v at the toy A Site); no Bomb entry → site
  plant time with `by`/`area` null (the existing `test_timeline_has_the_plant_the_defuse_and_damage` still passes
  unchanged); no "Spike" ability row in `timeline`, but `view.utility()` still has it.

Check: `cd webapp; <python> -m pytest tests/coach -q` all pass, and the new tests were run red first. Mutants
(`mutant_check.py`, spec in the run folder): `>= HALF_S` → `> HALF_S`; finished checked after half; `area` taken
from the first player's position instead of the Bomb entry's u/v (or `None`); spike timer 45 → 40. All caught.

### S2 (W4) — checker accepts the new citations; the prompt line

Files: `webapp/app/coach/checker.py` (`_MEASURED` :23, `_SECOND_KEYS` :30: add `spike_left_s`), only if tests
fail without it; `webapp/app/coach/prompt.py:12` (the `timeline` line). Tests: add to
`webapp/tests/coach/test_checker.py` (pattern: :385-398, the monkeypatched callouts and `_check`).

Tests: a caption citing a timeline answer passes for (a) "planted on A Site" (the plant's `area`), (b) "a 1.9 s
tap at 1:49" style text (`seconds` and the attempt `t`), (c) "with 15 s on the spike" (`spike_left_s`), and fails
for a duration not in the answer ("a 2.5 s tap"). `test_prompt.py` still passes; the prompt line reads, in one
line: `kills, damage, utility, the plant (who, where) and each defuse attempt (who, seconds, tap/half/defused,
spike time left) in order`.

Check: `cd webapp; <python> -m pytest tests/coach -q` all pass; (c) red before the key is added (if it is
needed).

### S3 (W5) — real snapshot counts and the full suite

No repo change. Copy the run folder's `snapshot-ac9fd52a.json` to `<run folder>/real/snapshot.json` and run
`timeline` for rounds 2, 15, 30, 35 through `app.coach.snapshot.load` (as `scripts/coach_query.py:37`). Record in
`LOG.md` the counts by kind and result. Expected: round 2: 1 defused; 15: 34 tap + 1 defused; 30: 1 tap + 1 half
(the half ends at the defuser's kill time); 35: 1 defused; each round one plant event with a non-null area and
planter. Then the full suite in the baseline's parts, one per call, with the known base failures deselected.

Check: the counts equal the expected ones; the suite shows no failure outside the known list.

### S4 (W6) — PR body

`<run folder>/PR-BODY.md`: what changed, the real-snapshot counts (no player names), the test commands.

Check: the file exists and names no player.
