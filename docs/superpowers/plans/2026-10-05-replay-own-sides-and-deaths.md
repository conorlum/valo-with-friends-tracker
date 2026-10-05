# The replay's own sides and deaths: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map control stops needing the tracker.gg link: the condenser records which side group attacked each round and the deaths no player caused, so an unlinked replay's control is as correct as a linked one's and its page can show it.

**Architecture:** A probe runs first, against real exports, and it gates everything after it. Then the condenser moves to revision 13 with two additions. (1) **World deaths:** a lethal damage notify that names no player as attacker. It is admitted in time order against the life it falls in, and only if no kill ends that life. World deaths close lives and never become kills. (2) **`attack`:** taken from the spike's planter (the Bomb `util` entry with `owner_by == "planted"`, which exists only after `attach_extras`) under the mode's side-swap schedule. The mode is read from the replay itself; an unknown mode leaves `attack` null. The engine (revision 6) uses both when the link is missing. Every stored replay is re-condensed, and the idle queue (companion plan) recomputes the stale rows.

**Tech Stack:** Python 3.13, the condenser and engine as they are, pytest with `tests/replays/replay_synthetic.py` and the helpers in `test_replay_condense.py` (`run`, `lethal_damage`) and `control_toys.py` (`blob`, `toy_geometry`).

**Spec:** Agreed in conversation on 2026-10-05; no separate spec file. Revised the same day after an external review (findings 4-8 and 11; see "Review log"). Depends on `docs/superpowers/plans/2026-10-05-control-idle-queue.md` being merged first.

| # | Decision | Source |
|---|---|---|
| E1 | The replay already holds the deaths the link supplies. Today `read_kills` drops a lethal damage notify whose attacker resolves to no player (`condense.py:752`, counted `damage_without_killer`), and `test_lethal_damage_on_an_object_or_with_no_killer_adds_no_kill` pins that. The plan keeps these notifies as **world deaths** and changes that test on purpose. | user: "how could the replay not know these deaths?" |
| E2 | Attack side from the replay. The user proposed spawn areas; this plan uses the **spike's planter** under the **mode's** swap schedule. Competitive (12-round halves, overtime alternating every round) is the one the link already assumes (`attacking_team`, `plant_window.py:80`). Swiftplay (4-round halves) is used only if the probe verifies its schedule on real exports. Any other or unknown mode gives `attack = null`, never a guess. Spawn areas are measured as a fallback, but **not built in this plan**: if the probe says they're needed, the plan stops and the user decides. | user (spawns); review finding 6 |
| E3 | World deaths are **not** kills. They never enter `kills` or `link_kills` (the linker pairs replay kills with tracker.gg kills one to one, and `side_groups` reads kills as cross-side evidence); they only close lives, with a new cause `"world"`. | this plan |
| E4 | The link still wins where it exists. When the link names sides, the engine uses them; a disagreement with the blob's `attack` is recorded in the round's summary (`side_disagreement`), not raised. | this plan |
| E5 | Reprocessing is the user's step: local replays through `scripts/reingest_replays.py`, uploads through the worker's archive reparse (`/admin/replays/*`) if the archive is on; otherwise they stay at c12 until re-uploaded. | standing rule |
| E6 | Team kills are **out of scope**. A team kill sent as a kill RPC already closes the victim's life, but it also breaks `side_groups` (inconsistent evidence) and the linker (it's in the replay's kill sequence but excluded from tracker.gg's). Those replays are unlinked or refused today, for a reason this plan doesn't touch. The probe counts them so the size of that problem is known. | review finding 7 |

## Global Constraints

- Run tests from `webapp/` with `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ... -p no:cacheprovider` (`PY -m pytest` below).
- Reading the friends DB goes through `scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`; auto mode can't read prod, so anything against Render is run by the user. The local DB (pulled 2026-10-02) is enough for the probe.
- Exports: `scripts\export_replay.ps1 <uuid>` into `%TEMP%\valo-replay\<uuid>`; `.vrf` archive at `%USERPROFILE%\ValorantReplayArchive` (or `VALO_REPLAY_ARCHIVE`).
- `CONDENSE_REVISION` 12 → 13 once (Task 4); `FORMAT_VERSION` doesn't change unless `format.py`'s rules say the new `alive` cause `"world"` needs it (check its docstring and any cause validation first; if it does, bump it in Task 3 and say so in the commit).
- `CONTROL_REVISION` 5 → 6 once (Task 5).
- The linker (`app/replays/link.py`) and what it reads (`link_inputs["kills"]`, the blob's `kills`) don't change.
- Small sample before a full recompute (memory: `feedback_small_sample_before_full_recompute`).

## Review Focus

1. **Several world hits on one player** (spike damage over several ticks, a corpse hit again). Expected: the first one in an open life closes it; the rest fall in no life and are dropped, never a lifecycle refusal. Pinned in Task 3.
2. **A world hit followed by a kill of the same player in the same life** (the world hit wasn't really fatal: a Sage res the parser missed, a misflagged notify). Expected: the kill wins, the world hit is dropped and counted. Pinned in Task 3.
3. **A world death next to a revive, a pawn change or a disconnect.** Expected: admitted only inside an open life; a revive after it opens a new life as today. Pinned in Task 3.
4. **A match with plants in one half only, or none.** Expected: one half's plants label the whole match under a verified schedule; with none, every `attack` is null. Pinned in Task 2.
5. **Swiftplay, or a mode the probe couldn't identify.** Expected: Swiftplay labelled only if Q0/Q3 verified its schedule; anything unknown is null. Pinned in Task 2 with independently labelled cases.

---

### Task 1: The probe (a gate)

**Files:**
- Create: `webapp/scripts/probe_replay_sides_deaths.py`
- Create: `docs/superpowers/plans/2026-10-05-replay-own-sides-and-deaths-probe.md` (findings)

Populations: **linked** replays (the link is the answer key, and the crawl keeps Competitive only, so these are Competitive) and **unlinked/refused** replays with exports (for Q0's other modes and E6's count). Every gate needs nonzero evidence in the class it judges; a class with no evidence is "unmeasured", not "passed".

| Q | Measured | Gate |
|---|---|---|
| Q0 | The mode signature of each export: the player-state and game-state archetype names (`actor_spawned` rows whose archetype ends `PlayerState_C` / `GameState_C`; contract.py names `Swiftplay_EoRCredits_PlayerState_C` and the legacy `BombPlayerState`). Tabulate signature × linked/unlinked. | Linked replays share one signature (Competitive). If they don't, stop. |
| Q1 | Deaths, on linked replays only. Partition the link's excluded DB kills (`link.split_db_kills`) by class: **no killer**, **self**, **teammate**. Build the candidate world deaths with the **same rules Task 3 will use**: a lethal notify, no player attacker, and not within `DAMAGE_KILL_MATCH_MS` of a kill of that victim. Match them to the no-killer and self classes on (slot, \|dt\| ≤ 1.0 s) on the replay clock. | No-killer + self: ≥ 95% found, ≥ 5 deaths measured, ≤ 5% of candidates unmatched. The teammate class is reported only (E6). |
| Q2 | E6's size: on unlinked/refused exports, rounds whose kill RPCs put killer and victim in the same tight spawn cluster (`two_clusters` on round-start positions, as condense does). | Reported only. |
| Q3 | Planters, on linked replays: condense with extras, and for each Bomb `util` entry with `owner_by == "planted"`, take its `by` slot's side group. Compare with the link's attacking group for that round (`round_link(...)["sides"]`). Also report how many matches have no plant, and how many have plants in only one half. | 100% agreement over ≥ 30 planted rounds. Any miss stops the plan. |
| Q3s | Swiftplay, only if Q0 found Swiftplay exports: put their planters through the 4-round-half schedule and check that the planters agree with a single assignment of groups to sides. Plants on both halves are what make this a real check. | All agree, with ≥ 3 matches that have plants in both halves → Swiftplay is supported. Otherwise it stays unsupported (null). |
| Q4 | Spawn areas as a fallback, on linked replays: per map, the centroid of the attacking group's round-start positions (`link_inputs["start_positions"]`, uv per slot, by round). Then, holding out one replay at a time, how often "the group nearer the attack centroid" gives the link's answer. | Reported. If Q3 leaves more than 5% of matches with no plant at all, **stop**: the user decides whether a spawn-fallback plan is worth writing. |

- [ ] **Step 1: Write the probe.** Read-only. It imports the condenser's own functions rather than re-implementing them. Its candidate world-death reader:

```python
from app.replays import condense as c


def world_death_candidates(export, players, kills) -> list[tuple[int, int]]:
    """(t_ms, victim slot): lethal notifies with no player attacker, minus those beside a kill of the same victim:
    exactly the rule Task 3 gives read_kills."""
    out = []
    for row in export.events:
        data = row.data
        if data.get("type") != "rpc_received" or data.get("function_name") not in c.RPC_DAMAGE:
            continue
        payload = data.get("payload") or {}
        if payload.get("DamageKilledTarget") is not True:
            continue
        victim = players.resolve(int(payload.get("Character") or 0), row.time_ms)
        if victim is None or players.resolve(int(payload.get("EventInstigatorPawn") or 0), row.time_ms) is not None:
            continue
        if any(k.victim == victim and abs(k.t_ms - row.time_ms) <= c.DAMAGE_KILL_MATCH_MS for k in kills):
            continue
        out.append((row.time_ms, victim))
    return out
```

To get `export`, `players` and `kills`, follow the first lines of `condense()` exactly (how it opens the export, builds `PlayerTable` and calls `read_kills`); don't re-derive them. For blobs, the report and `link_inputs`, call `c.condense_export_dir(export_dir, source_sha256=..., with_extras=True)`. Per-round times convert with the round window's start, as `condense()` does with `_seconds`.

Q3 reads the Bomb entry as the reviewer's synthetic run showed it: `{"k": "ability", "kind": "Bomb", "by": <slot>, "owner_by": "planted", ...}` in `replay.rounds[n]["util"]` after `attach_extras`. Print one real entry first and stop if the keys differ.

Output: one table per question, then a JSON line of totals.

- [ ] **Step 2: Run it.** Export every linked replay that has a `.vrf` in the archive, plus every unlinked/refused one that does (`scripts\export_replay.ps1 <uuid>`), then run `.\.venv313\Scripts\python.exe scripts\probe_replay_sides_deaths.py --export-root %TEMP%\valo-replay` (local DB). If any gate is "unmeasured" because there are too few exports, stop and say how many more are needed.

- [ ] **Step 3: Write the findings** (`...-probe.md`): the tables, each gate's verdict, the Competitive signature from Q0, whether Swiftplay is supported (Q3s), E6's count, and the decision for Tasks 2-4.

- [ ] **Step 4: Commit** (`"probe: can a replay supply its own sides and deaths?"`).

**Gate:** Tasks 2-7 start only after the user has read the findings.

---

### Task 2: The schedule and the attack labels (pure functions)

**Files:**
- Create: `webapp/app/replays/sides.py`
- Test: `webapp/tests/replays/test_replay_sides.py`

**Interfaces:**
- Produces: `SCHEDULES: dict[str, Callable[[int], int | None]]` mapping a mode name to "which half-parity attacks in round n": `0` means the side group that attacks in round 1, `1` means the other one, `None` means unknown (a sudden-death round, for example). `attack_sides(planters: dict[int, str], rounds: int, mode: str | None) -> tuple[dict[int, str | None], dict]`.

- [ ] **Step 1: Write the failing tests.** These are labelled independently of `attacking_team`, from the published schedules:

```python
from app.replays.sides import SCHEDULES, attack_sides


def test_competitive_halves_and_overtime():
    s = SCHEDULES["competitive"]
    assert [s(n) for n in (1, 12, 13, 24, 25, 26, 27)] == [0, 0, 1, 1, 0, 1, 0]


def test_one_plant_labels_a_competitive_match():
    sides, report = attack_sides({3: "B"}, 26, "competitive")
    assert (sides[1], sides[12], sides[13], sides[24], sides[25], sides[26]) == ("B", "B", "A", "A", "B", "A")
    assert report == {"resolved": True, "mode": "competitive", "evidence": 1}


def test_plants_that_contradict_the_schedule_leave_every_round_unknown():
    sides, report = attack_sides({3: "B", 15: "B"}, 24, "competitive")
    assert set(sides.values()) == {None} and report["resolved"] is False and report["reason"] == "plants disagree"


def test_no_plant_or_an_unknown_mode_leaves_every_round_unknown():
    assert attack_sides({}, 20, "competitive")[1] == {"resolved": False, "mode": "competitive", "evidence": 0,
                                                      "reason": "no plant"}
    sides, report = attack_sides({1: "A"}, 9, None)
    assert set(sides.values()) == {None} and report["reason"] == "unsupported mode"
    assert attack_sides({1: "A"}, 9, "deathmatch")[1]["reason"] == "unsupported mode"
```

Only if the probe's Q3s supported Swiftplay, also add:

```python
def test_swiftplay_swaps_after_four_rounds():
    s = SCHEDULES["swiftplay"]
    assert [s(n) for n in range(1, 10)] == [0, 0, 0, 0, 1, 1, 1, 1, None]   # round 9: sudden death, unknown
    sides, _ = attack_sides({1: "A", 5: "B"}, 9, "swiftplay")
    assert (sides[1], sides[4], sides[5], sides[8], sides[9]) == ("A", "A", "B", "B", None)
```

If the probe left it unsupported, add `assert "swiftplay" not in SCHEDULES` instead.

- [ ] **Step 2: Run** `PY -m pytest tests/replays/test_replay_sides.py -q -p no:cacheprovider` → FAIL (no module).

- [ ] **Step 3: Implement** `app/replays/sides.py`:

```python
"""Which side group attacked each round, from the replay alone (docs/superpowers/plans/2026-10-05-replay-own-sides-and-deaths.md,
E2): the spike's planters and the mode's swap schedule. Never guessed: any contradiction, no plant, or a mode with no
verified schedule gives None for every round."""

from __future__ import annotations


def _competitive(n: int) -> int | None:
    """12-round halves; overtime from round 25 alternates every round, starting as round 1 did."""
    if n < 1:
        return None
    if n <= 12:
        return 0
    if n <= 24:
        return 1
    return (n - 25) % 2


SCHEDULES = {"competitive": _competitive}
# Swiftplay is added here only if the probe verified its schedule on real exports (Task 1, Q3s).


def attack_sides(planters: dict[int, str], rounds: int, mode: str | None) -> tuple[dict[int, str | None], dict]:
    unknown = {n: None for n in range(1, rounds + 1)}
    schedule = SCHEDULES.get(mode or "")
    if schedule is None:
        return unknown, {"resolved": False, "mode": mode, "evidence": len(planters), "reason": "unsupported mode"}
    if not planters:
        return unknown, {"resolved": False, "mode": mode, "evidence": 0, "reason": "no plant"}
    other = {"A": "B", "B": "A"}
    firsts = set()
    for n, group in planters.items():
        parity = schedule(n)
        if parity is None or group not in other:
            continue
        firsts.add(group if parity == 0 else other[group])
    if len(firsts) != 1:
        reason = "plants disagree" if firsts else "no usable plant"
        return unknown, {"resolved": False, "mode": mode, "evidence": len(planters), "reason": reason}
    first = firsts.pop()
    sides = {}
    for n in range(1, rounds + 1):
        parity = schedule(n)
        sides[n] = None if parity is None else (first if parity == 0 else other[first])
    return sides, {"resolved": True, "mode": mode, "evidence": len(planters)}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** (`"replay sides: the swap schedule and attack labels from planters"`)

---

### Task 3: World deaths, admitted in time order

**Files:**
- Modify: `webapp/app/replays/condense.py` (`read_kills` → also returns world deaths; `alive_intervals` gains `world` for the cause; new `admit_world_deaths`; the alive loop; the blob's `world_deaths`; the report's `kill_sources`)
- Test: `webapp/tests/replays/test_replay_condense.py`, `webapp/tests/replays/test_replay_lifecycle.py`

**Interfaces:**
- Produces: `read_kills(export, players) -> tuple[list[Kill], list[tuple[int, int]], dict]` (kills, world deaths `(t_ms, slot)`, counts; `damage_without_killer` is renamed `world_deaths`). `alive_intervals(..., world: Iterable[int] = ())`: a death whose time is in `world` closes the life with cause `"world"` instead of `"kill"`. `admit_world_deaths(start, end, kill_deaths, world_times, **lifecycle) -> tuple[list[int], int]`: the accepted world-death times and how many were dropped. Each round blob gains `"world_deaths": [[t_seconds, slot], ...]`.

- [ ] **Step 1: Write the failing tests**

In `test_replay_lifecycle.py` (pure; `alive_intervals`' own tests live here):

```python
from app.replays.condense import admit_world_deaths


def test_repeated_world_hits_close_the_life_once():
    accepted, dropped = admit_world_deaths(0, 100_000, [], [40_000, 60_000], revives=[], pawn_changes=[])
    assert accepted == [40_000] and dropped == 1


def test_a_later_kill_in_the_same_life_beats_a_world_hit():
    accepted, dropped = admit_world_deaths(0, 100_000, [80_000], [40_000], revives=[], pawn_changes=[])
    assert accepted == [] and dropped == 1


def test_a_world_death_after_a_revive_closes_the_new_life():
    accepted, _ = admit_world_deaths(0, 100_000, [30_000], [70_000], revives=[50_000], pawn_changes=[])
    assert accepted == [70_000]


def test_a_world_hit_while_disconnected_is_dropped():
    accepted, dropped = admit_world_deaths(0, 100_000, [], [50_000], revives=[], pawn_changes=[60_000],
                                           away=[(40_000, 60_000)])
    assert accepted == [] and dropped == 1


def test_world_closes_with_its_own_cause():
    assert alive_intervals(0, 100_000, [40_000], [], [], world=[40_000]) == [[0, 40_000, "world"]]
```

In `test_replay_condense.py`, change `test_lethal_damage_on_an_object_or_with_no_killer_adds_no_kill` (E1 changes what it pins) and add two more:

```python
def test_lethal_damage_on_an_object_adds_nothing_and_with_no_killer_is_a_world_death(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [
        lethal_damage(match, 3, 25.0, 99999, match.pawn(3, 5), "MulticastNotifyDamage_Base"),  # a camera
        lethal_damage(match, 3, 26.0, match.pawn(3, 0), 99998),                                # no killer
    ]
    out = run(tmp_path, match, events=events)
    assert out.rounds[3]["alive"]["0"] == [[0.0, 26.0, "world"]]
    assert out.rounds[3]["world_deaths"] == [[26.0, 0]]
    assert all(row[2] != 0 for row in out.link_inputs["kills"]["3"]), "not a kill (E3)"
    assert out.report["kill_sources"]["world_deaths"] == 1


def test_a_world_hit_beside_a_kill_rpc_is_that_kill(tmp_path):
    match = SyntheticMatch()
    # Round 1's first kill: 10.0 s, slot 0 kills slot 5. A killerless lethal notify 0.4 s later is the same death.
    events = match.events() + [lethal_damage(match, 1, 10.4, match.pawn(1, 5), 99998)]
    out = run(tmp_path, match, events=events)
    assert out.rounds[1]["world_deaths"] == []


def test_two_world_hits_on_one_player_refuse_nothing(tmp_path):
    match = SyntheticMatch()
    events = match.events() + [lethal_damage(match, 3, 26.0, match.pawn(3, 0), 99998),
                               lethal_damage(match, 3, 27.0, match.pawn(3, 0), 99998)]
    out = run(tmp_path, match, events=events)
    assert out.rounds[3]["world_deaths"] == [[26.0, 0]]
```

(Check in `SyntheticMatch` that slot 0 is alive at 26 s in round 3 and that round 1's first kill is at 10.0 s; the existing tests above rely on both.)

- [ ] **Step 2: Run** `PY -m pytest tests/replays/test_replay_lifecycle.py tests/replays/test_replay_condense.py -k "world" -q -p no:cacheprovider` → FAIL.

- [ ] **Step 3: Implement**

`read_kills`: where the killer resolves to no player, instead of counting `damage_without_killer` and continuing, append `(row.time_ms, victim)` to `world`. After the loop, drop world entries within `DAMAGE_KILL_MATCH_MS` of any kill of the same victim, sort, and return `kills, world, counts`, with `counts["world_deaths"] = len(world)`. Its one caller in `condense()` unpacks three values.

`alive_intervals` gains `world: Iterable[int] = ()`; `world_set = set(world)`; the line that closes an open life on a death becomes `current[1], current[2] = t, "world" if t in world_set else "kill"`.

`admit_world_deaths`:

```python
def admit_world_deaths(start: int, end: int, kill_deaths: list[int], world_times: list[int],
                       **lifecycle) -> tuple[list[int], int]:
    """The world deaths (E1) that end a life, in time order: one is admitted only inside a life that is open and
    that no kill ends (a later kill means it wasn't fatal), and each admitted one ends that life before the next is
    tried. The rest are dropped, never a refusal. `lifecycle` is alive_intervals' other arguments."""
    accepted: list[int] = []
    dropped = 0
    for t in sorted(world_times):
        lives = alive_intervals(start, end, sorted(kill_deaths + accepted), world=accepted, **lifecycle)
        life = next((iv for iv in lives if iv[0] <= t < (iv[1] if iv[1] is not None else end + 1)), None)
        if life is None or life[2] in ("kill", "world"):
            dropped += 1
            continue
        accepted.append(t)
    return accepted, dropped
```

`alive_intervals` keeps its keyword names (`revives`, `pawn_changes`, `gone_ms`, `self_kills`, `unobserved`, `away`), and `admit_world_deaths` passes them through. The tests above use those names.

The alive loop in `condense()`: per slot, `world_t = [t for t, s in world if s == slot and start <= t <= end]`; `accepted, dropped = admit_world_deaths(start, end, deaths, world_t, revives=revived, pawn_changes=pawns, gone_ms=..., self_kills=self_kills, unobserved=spans, away=...)`, with the same values the existing `alive_intervals` call uses; then call `alive_intervals(start, end, sorted(deaths + accepted), ..., world=accepted)`. Collect `round_world[n]` from the accepted times. Add `"world_deaths": [[_seconds(t, start), slot] for t, slot in sorted(round_world)]` to the round blob, and count the dropped ones in the report (`"world_deaths_dropped"`).

Check `format.py` and the viewer (`replay.js`) for anything that switches on `alive` causes (`"kill"`, `"left"`, `"round_end"`). Treat `"world"` like `"kill"` wherever a death is drawn.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k "condense or lifecycle or format or contract or link or store or fixture or replay_view" -p no:cacheprovider` → PASS. The linker tests pass unchanged (E3).

- [ ] **Step 5: Commit** (`"condense: deaths no player caused close lives, admitted in time order"`)

---

### Task 4: The condenser labels attack, after extras (condense 13)

**Files:**
- Modify: `webapp/app/replays/condense.py` (new `read_mode`; new `label_attack(replay)`; `condense_export_dir` calls it after `attach_extras`), `webapp/app/replays/format.py` (`CONDENSE_REVISION = 13`)
- Test: `webapp/tests/replays/test_replay_condense.py`

**Interfaces:**
- Consumes: `sides.attack_sides` (Task 2). The Bomb `util` entry `{"kind": "Bomb", "owner_by": "planted", "by": slot}`, present only after `attach_extras`.
- Produces: `read_mode(export) -> str | None` (from Q0's signatures: `"competitive"`, `"swiftplay"` if supported, else None); `label_attack(replay: CondensedReplay, mode: str | None) -> None`, which sets `replay.rounds[n]["attack"]` and `replay.report["attack"]`.

- [ ] **Step 1: Write the failing tests**

```python
def planted(match, n, t, slot):
    """The events attach_extras reads as a plant by `slot` in round n (copy their shape from the
    test in test_replay_extras.py that produces owner_by == "planted")."""
    ...  # build from test_replay_extras.py's plant fixture; don't invent the event shapes


def test_a_plant_labels_every_round_of_a_competitive_match(tmp_path):
    match = SyntheticMatch()           # competitive shape
    out = run(tmp_path, match, events=match.events() + planted(match, 3, 40.0, slot=0))
    group = next(p["side"] for p in out.rounds[3]["players"] if p["slot"] == 0)
    assert out.rounds[3]["attack"] == group and out.report["attack"]["resolved"] is True
    assert out.rounds[1]["attack"] == group
    if match.round_count >= 13:
        assert out.rounds[13]["attack"] != group


def test_without_extras_or_a_plant_attack_is_null(tmp_path):
    match = SyntheticMatch()
    out = run(tmp_path, match)                                   # no plant
    assert {r["attack"] for r in out.rounds.values()} == {None}
    out = run(tmp_path / "x", match, events=match.events() + planted(match, 3, 40.0, slot=0), with_extras=False)
    assert {r["attack"] for r in out.rounds.values()} == {None}


def test_an_unrecognised_mode_is_null(tmp_path, monkeypatch):
    monkeypatch.setattr(condense, "read_mode", lambda export: None)
    match = SyntheticMatch()
    out = run(tmp_path, match, events=match.events() + planted(match, 3, 40.0, slot=0))
    assert {r["attack"] for r in out.rounds.values()} == {None}
    assert out.report["attack"]["reason"] == "unsupported mode"
```

`planted` is a placeholder for an existing fixture's event shapes, not new logic. Find the test in `test_replay_extras.py` (or `replay_synthetic.py`) that makes a Bomb with `owner_by == "planted"` and lift its event builder. If `run` doesn't accept `with_extras`, extend it to pass the flag through to `condense_export_dir`. If `SyntheticMatch` has fewer than 13 rounds, the `round 13` assertion is skipped by its `if`; add a `SyntheticMatch(rounds=14)` case if the class allows it.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** `read_mode` checks the archetype names Q0 recorded. `label_attack`:

```python
def label_attack(replay: CondensedReplay, mode: str | None) -> None:
    """Each round's attacking side group (E2), from the planted spike's planter, after attach_extras has
    resolved it. Without extras, or with no resolved sides, every round is None."""
    side_of = {}
    for n, blob in replay.rounds.items():
        side_of.update({p["slot"]: p["side"] for p in blob["players"] if p.get("side")})
        break                                    # sides are per match: any round's players carry them
    planters = {}
    for n, blob in replay.rounds.items():
        for entry in blob.get("util") or []:
            if entry.get("kind") == "Bomb" and entry.get("owner_by") == "planted" and entry.get("by") in side_of:
                planters[n] = side_of[entry["by"]]
    labels, report = sides.attack_sides(planters, len(replay.rounds), mode)
    for n, blob in replay.rounds.items():
        blob["attack"] = labels.get(n)
    replay.report["attack"] = report
```

In `condense_export_dir`: read the mode from the export before condensing, and after the `if with_extras: attach_extras(...)` line call `label_attack(replay, mode if with_extras else None)`. When extras are off, set `attack` to None for every round with the reason `"no extras"`. If the report keeps a size accounting of the blobs (look for where `attach_extras` updates sizes), do the same after `label_attack`.

`format.py`: `CONDENSE_REVISION = 13`, extending the comment: `13: world deaths (an alive cause "world", world_deaths) and each round's attacking side group (attack)`. Update any test that pins 12 or the recipe string.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k "not pg" -p no:cacheprovider` → PASS except the pre-existing worker failure.

- [ ] **Step 5: Commit** (`"condense 13: rounds say which side group attacked, from the planter and the mode's schedule"`)

---

### Task 5: The engine uses the blob when the link is missing (control 6)

**Files:**
- Modify: `webapp/app/control/engine.py` (`RoundInputs.__init__` around `:284-296`; `RoundInputs._lives` around `:340`; `RoundControl` gains `side_disagreement`, passed where it's built around `:2411`), `webapp/app/control/encode.py` (the summary carries it), `webapp/app/replays/control_format.py` (`CONTROL_REVISION = 6`; `SUMMARY_VERSION` if its rules require it), `webapp/app/services/replay_control.py` (`MIN_UNLINKED_CONDENSE_REVISION = 13`, `needs_link`, `plan`'s new reason)
- Test: `webapp/tests/replays/test_control_engine.py`, `webapp/tests/replays/test_control_store.py`

**Interfaces:**
- Produces: `RoundInputs.side_disagreement: bool`; `RoundControl.side_disagreement: bool`; summary key `side_disagreement`; `replay_control.needs_link(replay) -> bool`; `PlannedRound.reason == "needs_link"` (not computable).

- [ ] **Step 1: Write the failing tests** (with `control_toys.blob` and `toy_geometry`, as `test_control_engine.py` already uses them):

```python
from control_toys import blob, open_hall, toy_geometry
import app.control.engine as ce


def two_players():
    return {0: ("A", [(0.0, 10, 10, 0)]), 5: ("B", [(0.0, 50, 50, 0)])}


def test_without_a_link_the_blob_says_who_attacked(tmp_path):
    geo = toy_geometry("Toy", open_hall(), cache_dir=tmp_path)
    rnd = ce.RoundInputs({**blob(two_players()), "attack": "B"}, geo)
    assert rnd.group_side == {"B": "attack", "A": "defense"} and rnd.side_disagreement is False


def test_the_link_wins_and_a_disagreement_is_recorded(tmp_path):
    geo = toy_geometry("Toy", open_hall(), cache_dir=tmp_path)
    link = ce.ControlLink(sides={0: "attack", 5: "defense"})
    rnd = ce.RoundInputs({**blob(two_players()), "attack": "B"}, geo, link)
    assert rnd.group_side["A"] == "attack" and rnd.side_disagreement is True


def test_a_world_death_and_the_links_db_death_close_one_life_once(tmp_path):
    geo = toy_geometry("Toy", open_hall(), cache_dir=tmp_path)
    b = {**blob(two_players()), "world_deaths": [[10.0, 0]]}
    lives = ce.RoundInputs(b, geo, ce.ControlLink(db_deaths=((0, 10.2),)))._lives()
    assert lives[0] == [(0.0, 10.0)]


def test_side_disagreement_reaches_the_summary(tmp_path):
    ...  # run the round through the same path test_control_engine.py's summary tests use (compute → encode_summary)
    # and assert summary["side_disagreement"] is True for the disagreeing blob/link above
```

The last test follows whatever existing test already checks a summary key (search `test_control_engine.py` and `test_control_stats.py` for `encode_summary` or `summary[`). `toy_geometry`'s exact arguments come from its definition in `control_toys.py:55`.

In `test_control_store.py`:

```python
def test_an_unlinked_replay_below_condense_13_needs_its_link(db, linked):
    linked.recipe = linked.recipe.replace(f".c{rc.condense_revision(linked.recipe)}.", ".c12.")
    linked.link_status, linked.match_id = "unlinked", None
    db.commit()
    planned = rc.plan(db)
    assert planned and {p.reason for p in planned} == {"needs_link"} and not any(p.computable for p in planned)


def test_needs_link_reads_an_unknown_revision_as_needing_it(db, linked):
    linked.recipe, linked.link_status = "garbage", "unlinked"
    assert rc.needs_link(linked) is True
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**

`RoundInputs.__init__`, replacing the `group_side` lines:

```python
        self.group_side = {}
        for slot, side in link.sides.items():
            if slot in self.team:
                self.group_side.setdefault(self.team[slot], side)
        attack = blob.get("attack")
        from_blob = {attack: "attack", ("B" if attack == "A" else "A"): "defense"} if attack in ("A", "B") else {}
        # E4: the link wins; the blob fills in without one, and a disagreement is recorded, not raised
        self.side_disagreement = bool(self.group_side and from_blob and self.group_side != from_blob)
        if not self.group_side:
            self.group_side = from_blob
```

`_lives`: iterate `list(self.link.db_deaths) + [(int(s), float(t)) for t, s in self.blob.get("world_deaths") or []]` (the blob stores `[t, slot]`, the link `(slot, t)`). A death that falls inside a life closed by an earlier death matches no open life and is skipped. The existing `SAME_DEATH_S` rule covers a link death and a world death a moment apart, and the third test pins that.

`RoundControl` gets a `side_disagreement: bool = False` field (after its existing fields), set from `rnd.side_disagreement` where it is constructed. `encode_summary` adds `"side_disagreement": rc.side_disagreement`. Follow `SUMMARY_VERSION`'s rule in `control_format.py`: bump it only if adding a key requires that.

`CONTROL_REVISION = 6`, with the comment `6: without a link, a round reads its attack side and world deaths from the blob (condense 13)`.

`replay_control.py`:

```python
MIN_UNLINKED_CONDENSE_REVISION = 13


def needs_link(replay: Replay) -> bool:
    """An unlinked replay whose blob predates condense 13 has no attack side or world deaths of its own: its
    control would be wrong, so it waits for the link or a re-condense. An unreadable revision counts as old."""
    if replay_db.is_linked(replay):
        return False
    revision = condense_revision(replay.recipe)
    return revision is None or revision < MIN_UNLINKED_CONDENSE_REVISION
```

In `plan`, after the `no_map`/`old_blob` branch, emit `reason="needs_link"` for every wanted round when `needs_link(replay)`. Add `"needs_link"` to `PlannedRound.computable`'s exclusions.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k "control" -p no:cacheprovider` → PASS (update tests that pin `CONTROL_REVISION == 5`).

- [ ] **Step 5: Commit** (`"control 6: without a link, a round reads who attacked and its world deaths from the blob"`)

---

### Task 6: The replay page shows control for an unlinked replay

**Files:**
- Modify: `webapp/app/routers/replays.py` (`replay_page`'s `"control"`; `players.json` and `heatmap.json` stay linked-only at `:277`), and `app/services/replay_service.page_context` if it skips `match.control` for unlinked replays
- Test: `webapp/tests/replays/test_replay_routes.py` (whichever file renders `replay_page`)

- [ ] **Step 1: Failing tests:** an unlinked replay at c13 renders with `match.control` in the template context, and one at c12 renders without it.
- [ ] **Step 2: Implement:** `"control": context["match"]["control"] if (linked or not control_service.needs_link(replay)) else None`. If `page_context` builds no `control` entry for an unlinked replay, build it under the same condition. The side tables and heatmap keep their `unlinked` 404, because they name players and teams from the link.
- [ ] **Step 3: Browser check** on a local unlinked replay after Task 7's local step: the map draws control, and the tables say they need the link.
- [ ] **Step 4: Commit** (`"replay page: map control for unlinked replays condensed at 13"`)

---

### Task 7: Reprocess, small sample first (the user's steps)

- [ ] **Step 1:** Locally, re-condense one linked and one unlinked replay (`scripts\reingest_replays.py` on the local DB, after `export_replay.ps1`), and compute control for one replay each (`scripts\compute_control.py`, `--match` if it has it). Compare the linked replay's page before and after (it should match except where a world death now ends a life the link already ended), and check that the unlinked one now shows attack and defense. Paste the per-round world deaths beside the link's `db_deaths` into the probe findings.
- [ ] **Step 2:** The user merges. Every control row goes stale (the recipe and `CONTROL_REVISION` are both in the fingerprint). They are still served, with the companion plan's "Out of date" note.
- [ ] **Step 3:** The user re-condenses: local replays with `reingest_replays.py` through `with_friends_db.py`, uploads with the worker's archive reparse if the archive is on. Uploads with no archived `.vrf` stay at c12. Their linked rounds still recompute (the link covers them); their unlinked rounds are `needs_link` until re-uploaded.
- [ ] **Step 4:** The idle queue recomputes everything over the next few hours. Spot-check the worker's `/health` and one page per map.

## Review log (2026-10-05)

From the external review, this revision takes plan 2's findings:
- **4:** world deaths are admitted in time order against the evolving lives, and a later kill beats a world hit (Task 3, `admit_world_deaths`).
- **5:** attack is labelled after `attach_extras`, from the Bomb entry's real keys, and is null without extras (Task 4).
- **6:** a schedule per mode lives in its own module, Swiftplay only if the probe verifies it, unknown modes are null, and the tests are labelled independently of `attacking_team` (Tasks 1-2).
- **7:** team kills are out of scope and counted, not fixed (E6, Q2).
- **8:** Q1 partitions DB deaths by class and uses the same dedupe as Task 3; Q4 reads `link_inputs`; every gate needs nonzero evidence; the spawn fallback is a stop-and-ask, not a branch of this plan.
- **11:** the tests use the real names (`RoundInputs`, `control_toys.blob`, `lethal_damage`, `run`), `side_disagreement` goes through `RoundControl`, and the existing no-killer test changes on purpose (E1).

Still open, because only the code can answer them: the plant event shapes in `test_replay_extras.py` (Task 4's `planted` helper), whether `run` takes `with_extras`, and whether `"world"` needs a `FORMAT_VERSION` bump.
