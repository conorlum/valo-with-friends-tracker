# The replay's own sides and deaths: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map control no longer needs the tracker.gg link: the condenser works out which side attacked each round and records the deaths no player caused (spike, fall, suicide), so an unlinked replay's control is as correct as a linked one's and its page can show it.

**Architecture:** A probe first, against real exports and the linked replays' known answers, decides whether the evidence holds (Task 1 is a gate: nothing after it starts unless it passes). Then the condenser (`app/replays/condense.py`) gains two outputs at condense revision 13: per-round `attack` (which side group, "A" or "B", attacked) from the spike's planter and the half-swap rule, and per-round `world_deaths` from lethal damage that names no player. The engine (`app/control/engine.py`) uses them when the link is missing. Every stored replay is then re-condensed; the recipe change makes every control row stale, and the idle queue (companion plan) recomputes them.

**Tech Stack:** Python 3.13, the condenser and engine as they are, SQLAlchemy, pytest with the synthetic replay (`tests/replays/replay_synthetic.py`).

**Spec:** Agreed in conversation on 2026-10-05; no separate spec file. Depends on `docs/superpowers/plans/2026-10-05-control-idle-queue.md` (the queue that recomputes stale rows) being merged first.

| # | Decision | Source |
|---|---|---|
| E1 | The replay already holds the deaths the link supplies: lethal damage notifies (`DamageKilledTarget`) whose attacker resolves to no player are dropped today (`read_kills`, `condense.py:752`, counted as `damage_without_killer`). Keep them as deaths. | user: "how could the replay not know these deaths?" |
| E2 | Attack side from the replay, not the link. The user proposed spawn areas. This plan uses the **spike's planter** first (the spike actor's owner is already resolved as "planted", `extras.py:554`), with the half-swap rule (`app/scoring/plant_window.attacking_team`, the same rule `round_link` uses) to cover rounds with no plant. It needs no per-map data. The probe also measures spawn areas as the fallback for a match with no plant at all. | user (spawns); this plan (planter first) |
| E3 | World deaths are **not** kills. They never enter `kills` (the linker pairs replay kills one-to-one with tracker.gg kills, and `side_groups` reads kills as cross-side evidence); they only close lives. | this plan |
| E4 | The link still wins where it exists. When the link names sides, the engine uses them; a disagreement with the blob's `attack` is counted in the round's summary, not raised. | this plan |
| E5 | Reprocessing is the user's step: local replays through `scripts/reingest_replays.py`, uploads through the worker's archive reparse (`/admin/replays/*`) if the archive is on, else they stay at c12 until re-uploaded. | standing rule |

## Global Constraints

- Run tests from `webapp/` with `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe -m pytest ...` (`PY -m pytest` below).
- Reading the friends DB goes through `scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only`; auto mode can't read prod, so a probe against Render is run by the user. The local DB (last pulled 2026-10-02) is fine for the probe.
- Exports come from `scripts\export_replay.ps1 <uuid>` into `%TEMP%\valo-replay\<uuid>`; the `.vrf` archive is `%USERPROFILE%\ValorantReplayArchive` (or `VALO_REPLAY_ARCHIVE`).
- `CONDENSE_REVISION` goes 12 → 13 exactly once, in Task 3; `FORMAT_VERSION` doesn't change (new keys are additive; `format.py`'s docstring says when `v` must bump).
- `CONTROL_REVISION` goes 5 → 6 in Task 4 (the engine reads new inputs).
- Never change what the linker matches (`app/replays/link.py`): `link_kills` and the blob's `kills` rows stay as they are.
- Small sample before a full recompute (memory: `feedback_small_sample_before_full_recompute`): preview one or two rounds before the full re-condense.

## Review Focus

1. **A death the replay has twice:** a lethal hit with no player attacker at the same moment as a kill RPC for that victim (a molly whose owner is gone, a Clove self-revive). Expected: one death. Pinned in Task 3 (dedupe within `DAMAGE_KILL_MATCH_MS`).
2. **A world death of a player already dead** (damage on a corpse, or after a revive closes). Expected: ignored, never a contract error ("a second death with no revive"). Pinned in Task 3.
3. **A match with no plant in one half, or none at all.** Expected: the other half's plants decide both halves; with none at all, `attack` is null for every round and the engine behaves as today (link or nothing). Pinned in Task 2.
4. **Plants that disagree** (a planter from group A in round 3 and from group A in round 15, which the swap rule says is impossible). Expected: `attack` null for the whole match, reported, never guessed. Pinned in Task 2.
5. **Swiftplay and other short modes** (`replay_synthetic` has `shape="swiftplay"`), where halves aren't 12 rounds. Expected: the same answer the link gives, since both use `attacking_team`; the probe reports any mode where the planter evidence disagrees with that rule, and those matches get null. Pinned in Task 1 (measured) and Task 2 (contradiction → null).

---

### Task 1: The probe (a gate)

**Files:**
- Create: `webapp/scripts/probe_replay_sides_deaths.py`
- Create: `docs/superpowers/plans/2026-10-05-replay-own-sides-and-deaths-probe.md` (its findings)

The probe answers four questions on every **linked** replay whose export is on disk, using the link as the answer key:

| Q | Measured | Pass |
|---|---|---|
| Q1 | Each lethal damage notify with no player attacker (today's `damage_without_killer`), matched to the link's `db_deaths` for that round on the replay clock (`t_db - clock_offset`) within 1.0 s, same slot. | ≥ 95% of `db_deaths` found, and ≤ 5% of the notifies unmatched |
| Q2 | Team kills: does a team kill arrive as `MulticastNotifyKilledEnemy`? Count rounds where a link `db_death` whose DB killer is a teammate matches a replay kill RPC. | Reported; decides Task 3's step 3 |
| Q3 | Planter group per planted round (the Bomb actor's `caused_by` owner → the blob's player `side`) against the link's attacking group for that round (`round_link(...)["sides"]`). | 100% agreement on rounds with a planter; any miss is listed with its round and mode |
| Q4 | Spawn areas: per map, the centroid of the attacking group's start positions (`start_positions` in the condense report) over linked rounds; then, held out by replay, how often "the group nearer the attack centroid" gives the link's answer. | Reported only (the fallback for no-plant matches; built only if Q3 leaves > 5% of matches with no plant) |

- [ ] **Step 1: Write the probe**

It reuses the condenser's own readers rather than re-deriving them:

```python
"""Measures whether a replay can supply what map control reads from the link: deaths no player caused, and
which side attacked (docs/superpowers/plans/2026-10-05-replay-own-sides-and-deaths.md, Task 1). Read-only.

    .\\.venv313\\Scripts\\python.exe scripts\\probe_replay_sides_deaths.py --export-root %TEMP%\\valo-replay
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models.replay import Replay  # noqa: E402
from app.replays import condense as c  # noqa: E402
from app.replays import extras as x  # noqa: E402
from app.services import replay_control as rc  # noqa: E402


def lethal_without_player(export, players) -> list[tuple[int, int]]:
    """(t_ms, victim slot) for every lethal damage notify whose attacker is no player: what read_kills drops."""
    out = []
    for row in export.events:
        data = row.data
        if data.get("type") != "rpc_received" or data.get("function_name") not in c.RPC_DAMAGE:
            continue
        payload = data.get("payload") or {}
        if payload.get("DamageKilledTarget") is not True:
            continue
        victim = players.resolve(int(payload.get("Character") or 0), row.time_ms)
        killer = players.resolve(int(payload.get("EventInstigatorPawn") or 0), row.time_ms)
        if victim is not None and killer is None:
            out.append((row.time_ms, victim))
    return out
```

The rest of the script, per linked replay with an export under `--export-root/<uuid>`:
1. Open the export the way `condense_export_dir` does (`c.Export`/`c.read_players` — follow `condense_export_dir`'s first lines exactly; don't re-implement them) and condense it once (`c.condense_export_dir(..., with_extras=True)`) to get the blobs, round windows and report.
2. **Q1:** for each round, `db = rc.round_link(replay, rc.side_groups(db, replay), n)["db_deaths"]` (already on the replay clock), and the probe's `lethal_without_player` rows inside the round's window converted with `c._seconds(t, start)`. Greedy match on (slot, |dt| ≤ 1.0 s). Count found/unmatched both ways.
3. **Q2:** for `db_deaths` whose tracker.gg kill has a teammate killer (`link.split_db_kills` gives the excluded set with killers), look for a replay kill row `[t, killer, victim]` at that time.
4. **Q3:** for each round blob, the `util` entry whose kind is the Bomb with `owner_by == "planted"` (check the field names in a real blob first: print one round's Bomb entry and adjust), its owner slot → the blob's `players[slot]["side"]`; the link's attacking group is the group of any slot whose `sides[slot] == "attack"`.
5. **Q4:** collect `report["start_positions"][n]` (u, v per slot) for linked rounds, label each slot attack/defense from the link, average per map, then score held-out replays.

Print a table per question and write the totals as JSON to stdout's last line.

- [ ] **Step 2: Run it**

```
scripts\export_replay.ps1 <uuid>     # for each linked replay with a .vrf in the archive, if not already exported
.\.venv313\Scripts\python.exe scripts\probe_replay_sides_deaths.py --export-root %TEMP%\valo-replay
```

(Local DB; the probe is read-only. Exports are the user's machine's; if fewer than 5 linked replays can be exported, stop and ask.)

- [ ] **Step 3: Write the findings and decide**

Write `...-probe.md` with the four tables and the decision:
- Q1 passes → Task 3 as written. Q1 fails → stop; report which kinds of deaths are missing (by DB death type if tracker.gg says) and ask.
- Q2 → if team kills arrive as kill RPCs, they already close lives today (nothing to do, note it). If not, and they show up as lethal notifies with a **player** attacker on the same team, Task 3 step 3 keeps them as world deaths too.
- Q3 at 100% → Task 2 as written. Any miss → stop and ask (it means the planter's owner resolution or the half rule is wrong somewhere, and guessing would mislabel every round of that match).
- Q4 → build the spawn fallback only if Q3 leaves more than 5% of matches with no plant at all; otherwise record the number and skip it (YAGNI).

- [ ] **Step 4: Commit** the probe and the findings (`"probe: can a replay supply its own sides and deaths?"`).

**Gate:** Tasks 2-6 start only after the user has read the findings.

---

### Task 2: The condenser works out which side attacked

**Files:**
- Modify: `webapp/app/replays/condense.py` (new `attack_sides`; the round blob's new `attack` key; the report's `attack` entry)
- Test: `webapp/tests/replays/test_replay_condense.py`

**Interfaces:**
- Produces: `attack_sides(planters: dict[int, str], rounds: int) -> tuple[dict[int, str | None], dict]` — per round number, "A"/"B"/None, and a report `{"resolved": bool, "evidence": int, "reason"?: str}`; each round blob gains `"attack": "A" | "B" | None`.

- [ ] **Step 1: Write the failing tests**

```python
from app.replays.condense import attack_sides


def test_one_plant_labels_every_round_by_the_half_rule():
    sides, report = attack_sides({3: "B"}, rounds=26)
    assert sides[1] == "B" and sides[12] == "B" and sides[13] == "A" and sides[24] == "A"
    assert sides[25] == "B" and sides[26] == "A"           # overtime alternates, starting as round 1
    assert report == {"resolved": True, "evidence": 1}


def test_plants_in_both_halves_must_agree():
    assert attack_sides({3: "B", 15: "A"}, rounds=24)[1]["resolved"] is True
    sides, report = attack_sides({3: "B", 15: "B"}, rounds=24)
    assert set(sides.values()) == {None} and report["resolved"] is False and "disagree" in report["reason"]


def test_no_plant_leaves_every_round_unknown():
    sides, report = attack_sides({}, rounds=20)
    assert set(sides.values()) == {None} and report == {"resolved": False, "evidence": 0, "reason": "no plant"}


def test_the_synthetic_replays_blobs_carry_attack(condensed):
    blob = condensed.rounds[1]          # adjust to how the fixture exposes round blobs
    assert "attack" in blob
```

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_replay_condense.py -k attack -q`
Expected: FAIL, `ImportError: cannot import name 'attack_sides'`.

- [ ] **Step 3: Implement**

```python
def attack_sides(planters: dict[int, str], rounds: int) -> tuple[dict[int, str | None], dict]:
    """Which side group attacked each round, from the spike's planters (round -> the planter's group) and the
    half rule the link uses (app/scoring/plant_window.attacking_team). Every planter must agree with one
    assignment; otherwise, or with no plant at all, every round is None: never guessed."""
    from app.scoring.plant_window import attacking_team
    from app.models.match import Team

    if not planters:
        return {n: None for n in range(1, rounds + 1)}, {"resolved": False, "evidence": 0, "reason": "no plant"}
    # the group that attacks when TEAM_1 does
    votes = {group if attacking_team(n) == Team.TEAM_1 else ("B" if group == "A" else "A")
             for n, group in planters.items()}
    if len(votes) != 1:
        return ({n: None for n in range(1, rounds + 1)},
                {"resolved": False, "evidence": len(planters), "reason": "plants disagree with the half rule"})
    first = votes.pop()
    other = "B" if first == "A" else "A"
    sides = {n: first if attacking_team(n) == Team.TEAM_1 else other for n in range(1, rounds + 1)}
    return sides, {"resolved": True, "evidence": len(planters)}
```

In the per-round loop, after `util` is built, find the round's planter from the Bomb entry the probe identified (same field names), and collect `planters[n] = sides[slot]` when the slot's side group is known. Because `util` is attached per round inside the loop, compute `attack_sides` after the loop and write `rounds[n]["attack"] = attack[n]` in a second pass over `rounds`. Add `"attack": attack_report` to the report beside `"sides"`. When `side_groups` failed (`sides_report["resolved"] is False`), every `attack` is None.

- [ ] **Step 4: Run** `PY -m pytest tests/replays/test_replay_condense.py tests/replays/test_replay_format.py tests/replays/test_replay_contract.py -q` → PASS.

- [ ] **Step 5: Commit** (`"condense: each round says which side group attacked, from the planter and the half rule"`).

---

### Task 3: The condenser records deaths no player caused

**Files:**
- Modify: `webapp/app/replays/condense.py` (`read_kills` returns world deaths too; the alive-interval loop uses them; the blob's `world_deaths`; `CONDENSE_REVISION` in `format.py` to 13 with its comment)
- Test: `webapp/tests/replays/test_replay_condense.py`, `webapp/tests/replays/test_replay_lifecycle.py`

**Interfaces:**
- Produces: `read_kills(export, players) -> tuple[list[Kill], list[tuple[int, int]], dict]` — kills, then world deaths `(t_ms, victim slot)`, then counts (`damage_without_killer` is renamed `world_deaths`); each round blob gains `"world_deaths": [[t_seconds, slot], ...]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_lethal_hit_with_no_player_attacker_is_a_world_death_not_a_kill():
    export, players = synthetic_export_with(
        lethal_hits=[{"t": 5000, "victim": 3, "instigator": 0}])   # 0: resolves to no player
    kills, world, counts = read_kills(export, players)
    assert world == [(5000, 3)] and not [k for k in kills if k.victim == 3]
    assert counts["world_deaths"] == 1


def test_a_world_death_next_to_a_kill_rpc_is_the_same_death():
    export, players = synthetic_export_with(
        kill_rpcs=[{"t": 5000, "killer": 6, "victim": 3}],
        lethal_hits=[{"t": 5400, "victim": 3, "instigator": 0}])
    _, world, _ = read_kills(export, players)
    assert world == []


def test_a_world_death_closes_the_life_and_one_on_a_dead_player_is_ignored():
    intervals = alive_intervals_with_world(start=0, end=100_000, deaths=[], world=[40_000, 60_000])
    assert intervals[0][1] == 40_000 and len(intervals) == 1
```

`synthetic_export_with` and `alive_intervals_with_world`: build them from `replay_synthetic.SyntheticMatch` (it already writes lethal damage notifies for kills; add a parameter for a lethal notify with no instigator) and from `alive_intervals`' existing test helpers in `test_replay_lifecycle.py`. Look at how those tests construct exports before writing new helpers; extend `SyntheticMatch` with an optional `world_deaths=[(t_ms, slot)]` rather than hand-building rows.

- [ ] **Step 2: Run them to see them fail**

Run: `PY -m pytest tests/replays/test_replay_condense.py tests/replays/test_replay_lifecycle.py -k world -q` → FAIL.

- [ ] **Step 3: Implement**

In `read_kills`, where a lethal notify's killer resolves to no player:

```python
        killer = players.resolve(int(payload.get("EventInstigatorPawn") or 0), row.time_ms)
        if killer is None:
            # E1: the spike, a fall, or the victim's own damage: a death no player caused. Not a kill (E3).
            world.append((row.time_ms, victim))
            continue
```

After the loop, drop world deaths within `DAMAGE_KILL_MATCH_MS` of a kill of the same victim (kill RPCs can arrive after the notify), sort, and return `kills, world, counts`. If the probe's Q2 found team kills arriving as lethal notifies with a teammate attacker and no kill RPC, add them to `world` here too (same dedupe).

In the alive-interval loop, the round's world deaths for the slot go through the same path as kills, but only while the player is alive: filter to times inside an open life before calling `alive_intervals`, so a hit on a corpse can't raise "a second death with no revive". Concretely, compute intervals once with kill deaths only, drop world deaths that fall outside every interval or within `DAMAGE_KILL_MATCH_MS` of an interval's end, then compute again with the survivors added to `deaths`. (Two calls is simpler and safer than threading a second kind through `alive_intervals`' state machine.)

Round blob: `"world_deaths": [[_seconds(t, start), slot] for t, slot in world if start <= t <= end]`.

`format.py`: `CONDENSE_REVISION = 13` with the comment extended: `13: each round's attacking side group (attack) and the deaths no player caused (world_deaths)`.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k "condense or lifecycle or format or contract or link or store or fixture"` → PASS. The linker tests must pass unchanged (E3: `link_kills` didn't change). Update any test that pins `CONDENSE_REVISION == 12` or the recipe string.

- [ ] **Step 5: Small sample (the user's eyes):** condense one exported replay with and without the change and print, per round, the world deaths and each one's nearest `db_deaths` entry from the link. Paste it in the probe findings doc.

- [ ] **Step 6: Commit** (`"condense 13: deaths no player caused close lives; rounds say who attacked"`).

---

### Task 4: The engine uses the blob when the link is missing

**Files:**
- Modify: `webapp/app/control/engine.py` (the round's team/side setup at `:284-296`; `_lives` at `:340`), `webapp/app/replays/control_format.py` (`CONTROL_REVISION = 6` with its comment), `webapp/app/services/replay_control.py` (`MIN_CONDENSE_REVISION` stays 10; new `MIN_UNLINKED_CONDENSE_REVISION = 13`; `plan`'s reason for an unlinked replay below it)
- Test: `webapp/tests/replays/test_control_engine.py`, `webapp/tests/replays/test_control_store.py`

**Interfaces:**
- Consumes: blob keys `attack`, `world_deaths` (Tasks 2-3).
- Produces: `PlannedRound.reason` may be `needs_link` (unlinked and blob below c13; not computable).

- [ ] **Step 1: Write the failing tests**

```python
def test_without_a_link_the_blob_says_who_attacked(round_blob, geometry):
    blob = {**round_blob, "attack": "A"}
    rnd = engine.Round(blob, geometry, engine.ControlLink())
    assert rnd.group_side == {"A": "attack", "B": "defense"}


def test_the_link_wins_and_a_disagreement_is_counted(round_blob, geometry):
    blob = {**round_blob, "attack": "B"}
    link = engine.ControlLink(sides={s: ("attack" if p["side"] == "A" else "defense")
                                     for s, p in ((p["slot"], p) for p in blob["players"])})
    rnd = engine.Round(blob, geometry, link)
    assert rnd.group_side["A"] == "attack" and rnd.side_disagreement is True


def test_world_deaths_close_lives_like_the_links_db_deaths(round_blob, geometry):
    slot = round_blob["players"][0]["slot"]
    blob = {**round_blob, "world_deaths": [[10.0, slot]]}
    lives = engine.Round(blob, geometry, engine.ControlLink())._lives()
    assert all(end <= 10.0 for _, end in lives[slot])


def test_an_unlinked_replay_below_condense_13_needs_its_link(db, linked):   # in test_control_store.py
    linked.recipe = linked.recipe.replace(f".c{rc.condense_revision(linked.recipe)}.", ".c12.")
    linked.link_status = "unlinked"
    db.commit()
    planned = rc.plan(db)
    assert planned and {p.reason for p in planned} == {"needs_link"}
    assert not any(p.computable for p in planned)
```

Use the fixtures `test_control_engine.py` already has for a round blob and geometry (`control_toys.py`), renaming `round_blob`/`geometry` above to theirs.

- [ ] **Step 2: Run them to see them fail** → FAIL.

- [ ] **Step 3: Implement**

Engine setup (replacing `:293-296`):

```python
        self.group_side = {}
        for slot, side in link.sides.items():
            if slot in self.team:
                self.group_side.setdefault(self.team[slot], side)
        attack = blob.get("attack")
        from_blob = {attack: "attack", ("B" if attack == "A" else "A"): "defense"} if attack in ("A", "B") else {}
        # E4: the link wins; the blob fills in for an unlinked replay, and a disagreement is counted
        self.side_disagreement = bool(self.group_side and from_blob and self.group_side != from_blob)
        if not self.group_side:
            self.group_side = from_blob
```

Players with no `side` and no link side still raise as today.

`_lives`: iterate `list(self.link.db_deaths) + [(int(s), float(t)) for t, s in self.blob.get("world_deaths") or []]` (note the blob's order is `[t, slot]`, the link's `(slot, t)`). The existing "closes the first life that contains it, unless within SAME_DEATH_S of its end" rule dedupes the two sources.

Add `side_disagreement` to the round summary (`encode.py` builds it from `rc`; add one boolean key, which `SUMMARY_VERSION`'s docstring says is additive or not; bump it if it says so).

`CONTROL_REVISION = 6` (comment: `6: an unlinked round reads its attack side and world deaths from the blob (condense 13)`).

`replay_control.plan`: for a replay that is not linked and whose `condense_revision(recipe) < MIN_UNLINKED_CONDENSE_REVISION`, emit `reason="needs_link"` and make `computable` false for it (add it to the tuple in `PlannedRound.computable`). This keeps the idle queue from spending time on rounds that would come out wrong.

- [ ] **Step 4: Run** `PY -m pytest tests/replays -q -k control` → PASS (update tests that pin `CONTROL_REVISION == 5`).

- [ ] **Step 5: Commit** (`"control 6: an unlinked round reads who attacked and its world deaths from the blob"`).

---

### Task 5: The replay page shows control for an unlinked replay

**Files:**
- Modify: `webapp/app/routers/replays.py` (`replay_page`'s `"control"`; the per-round `control.bin` route already serves unlinked replays; `players.json`/`heatmap.json` stay linked-only, `_control_replay_or_404` at `:277`)
- Test: `webapp/tests/replays/test_control_store.py` or `test_replay_routes.py` (whichever already renders `replay_page`)

- [ ] **Step 1: Failing test:** an unlinked replay at c13 renders the page with the control layer's data (`match.control` present in the template context); an unlinked replay at c12 doesn't.

- [ ] **Step 2: Implement:** `"control": context["match"]["control"] if (linked or not control_service.needs_link(replay)) else None`, with `needs_link(replay) -> bool` in `replay_control.py` (unlinked and below `MIN_UNLINKED_CONDENSE_REVISION`). Check `page_context` actually builds `match.control` for an unlinked replay; if it skips it, make it build it under the same condition. The tables and heatmap keep their `unlinked` 404: they name players and teams from the link.

- [ ] **Step 3: Check in a browser** on a local unlinked replay after re-condensing it (Task 6's local step): the map shows control; the side tables say they need the link.

- [ ] **Step 4: Commit** (`"replay page: map control for unlinked replays condensed at 13"`).

---

### Task 6: Reprocess, in a small sample first (the user's steps, written down)

**Files:**
- Modify: this plan (append the run log), `CLAUDE.md` only if a command changed

- [ ] **Step 1:** Locally, re-condense one linked and one unlinked replay (`scripts\reingest_replays.py` against the local DB; export first with `export_replay.ps1`). Compute control for one round of each (`scripts\compute_control.py` with a single-round filter if it has one, else one replay). Look at the page: the linked round should look the same as before except deaths the link already had; the unlinked round should now show attack/defense.
- [ ] **Step 2:** The user merges. Render rebuilds; every control row is stale at once (the recipe and `CONTROL_REVISION` are both in the fingerprint), and keeps being served, flagged, with the companion plan's "Out of date" note.
- [ ] **Step 3:** The user re-condenses: local replays with `reingest_replays.py` through `with_friends_db.py` (writes); uploads with the worker's archive reparse if the archive is on. Uploads with no archived `.vrf` stay at c12: their linked rounds still recompute (the link covers them), their unlinked rounds stay `needs_link` until re-uploaded.
- [ ] **Step 4:** The idle queue recomputes everything over the next few hours. Spot-check `GET /health` on the worker and a page per map.
