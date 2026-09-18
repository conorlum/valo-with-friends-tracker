# Pre-plant time-factor redesign (Part 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `_time_factor`'s flat `1.0` for pre-plant kills with a
proximity-to-plant curve whose amplitude is linear in man-advantage and
side-specific, fitted by logistic regression, without touching production
scoring or `IMPACT_CALCULATION_VERSION` yet.

**Architecture:** A new leaf module (`app/scoring/preplant_time_model.py`)
owns extraction, the shape/design-matrix machinery, the fit, `k`-selection,
and the centring solve — all pure functions or DB-read-only, independently
unit-testable with no live Postgres required (in-memory sqlite, per
`test_impact_kill_order_bonus_net.py`'s pattern). A fit-and-report script
runs the pipeline against the real DB and prints the constants a human
transcribes into a small, dormant runtime addition in `impact.py`, gated by
a new flag that defaults off everywhere. Nothing in `impact.py`'s currently
active path changes; `IMPACT_CALCULATION_VERSION` stays at 1.

**Tech Stack:** Python, SQLAlchemy, numpy (`app/services/stats_math.py`'s
hand-rolled IRLS logistic regression — this repo has no statsmodels/scipy
dependency and none should be added), pytest, sqlite (test-only).

**Correction (during Task 1 execution, 2026-09-07):** every code block below
was drafted with `dt = kill_time - plant_time` (negative before the plant).
That's backwards from `app.scoring.plant_window.seconds_to_plant`, which
returns `plant_time - kill_time` — **positive before the plant, decreasing to
0 at the plant** — matching the spec's own knot values written as positive
numbers (30, 20, 10, 5, 0). The implementation uses `seconds_to_plant`
directly (reusing Part 1's helper rather than re-deriving the sign) and
**positive** knots `(30.0, 20.0, 10.0, 5.0, 0.0)`, plateauing on `dt` in
`[0, 10)`, rising through `dt` in `(10, 30)`, zero at `dt >= 30`. Every task
below is implemented against this corrected convention; the negative-`dt`
numbers embedded in the plan's illustrative code/tests are superseded by
the actual committed code, not restated inline here to avoid a second
place to keep in sync.

**Spec:** `docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md`
(Part 3: "Parameterisation", "The fitting contract", "Centering is on
CONTRIBUTION", "Deaths", "Testing: Part 3"), plus
`docs/superpowers/2026-09-07-predeclared-values.md`'s "The k / FLOOR / CEIL /
W grid decision" section and `docs/superpowers/2026-09-07-three-grids-declaration-draft.md`
section 1 (`k`). Cited measurements: `M1`-`M5`, `M19`, `M20`.

## Global Constraints

- **No rescore, no `IMPACT_CALCULATION_VERSION` bump in this plan.** The
  spec requires Part 3, Part 4 and the econ swap to ship together under one
  bump (Rollout section). This plan builds Part 3 as a fully tested, dormant
  capability behind a flag defaulting to `False` everywhere, so today's 554
  passing tests and every shipped score stay bit-for-bit identical until a
  future plan flips the flag alongside Part 4 and econ.
- **`use_realized=False` must return exactly `1.0` for every pre-plant
  kill.** This is the leakage gate (spec, "Constraints and premises");
  exact, not approximate.
- **No kill is ever worth negative Impact.** The scalar ranges freely across
  1.0 but never at or below 0 (spec, "Standing design constraint").
- **Shape knots fixed at -30/-20/-10/-5/0 seconds-to-plant**, not estimated
  as free positions; the sub-10s plateau is imposed (spec, "Shape knots").
- **Advantage clamped to -3..+2** outside fitted support (spec, "Advantage
  outside -3..+2").
- **`k` selected only via the predeclared grid and rule** in
  `2026-09-07-predeclared-values.md` — never hand-picked, never selected on
  anything but the two clamp-rate targets (death-side residual, `|c-1|`,
  effective bounds are reported at the selected `k`, never used to choose
  it).
- **Centring pins the KILL side exactly**; the death-side residual is
  reported against a predeclared 2% tolerance, never tuned to zero (spec,
  "The gate is on the KILL side").
- **New files over edits to shared ones where possible** (this repo merges
  from a `public` remote) — `impact.py` gets the smallest edit that adds the
  dormant hook; everything else is new.
- Use `superpowers:test-driven-development` per task and
  `superpowers:verification-before-completion` before marking any task done.

---

## File Structure

| file | responsibility |
|---|---|
| `app/scoring/preplant_time_model.py` | `PreplantKillObservation`, `extract_preplant_observations`, `shape_basis`, `build_design_matrix`, `fit_preplant_time_model`, `PreplantFit` |
| `app/scoring/preplant_k_selection.py` | The predeclared `k` grid, `select_k`, `KSelectionResult` |
| `app/scoring/preplant_centering.py` | `solve_kill_side_centering`, `CenteringResult` |
| `app/scoring/preplant_scalar.py` | The dormant runtime scalar: `preplant_proximity_scalar(...)`, plus the module constants a human fills in from the fit script's report |
| `app/scoring/impact.py` | One new parameter threaded through the kill loop calling `preplant_proximity_scalar` behind a flag; no change to default behaviour |
| `scripts/fit_preplant_time_factor.py` | Fit-and-report script: runs the whole pipeline against the real DB, prints the nested-comparison and temporal-split reports, prints the constants to transcribe |
| `tests/test_preplant_time_model.py` | Extraction, shape basis, design matrix, fit |
| `tests/test_preplant_k_selection.py` | The selection rule, all three branches, tie-breaks |
| `tests/test_preplant_centering.py` | The centring solve, the sum-vs-mean trap |
| `tests/test_preplant_scalar.py` | Leakage gate (exact), monotonicity (conditional on sign), clamp behaviour |
| `tests/test_impact_preplant_hook.py` | The dormant flag: off by default, on produces a different (still valid) score |

---

### Task 1: Pre-plant observation extraction

**Files:**
- Create: `webapp/app/scoring/preplant_time_model.py`
- Test: `webapp/tests/test_preplant_time_model.py`

**Interfaces:**
- Produces: `PreplantKillObservation` (dataclass: `match_id: int`,
  `round_id: int`, `dt: float`, `adv: int`, `is_attacker: bool`,
  `exact_state: str`, `round_won_by_killer_team: bool | None`); `extract_preplant_observations(db) -> list[PreplantKillObservation]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_time_model.py
"""Extraction of one row per non-self pre-plant kill in a non-phantom,
non-surrendered planted round. No live Postgres required -- in-memory
sqlite, per test_impact_kill_order_bonus_net.py's pattern."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.preplant_time_model import extract_preplant_observations


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    p = Player(riot_id=name)
    db.add(p)
    db.flush()
    return p.id


def _match_with_one_planted_round(db):
    """Round 1 (TEAM_1 attacks), planted at t=40. A1 kills B1 at t=20
    (pre-plant, 5v5->4v5 from B's death, so the KILLER's exact_state is
    5v5, adv=0, dt=20-40=-20). Team A wins by Detonate."""
    match = Match(external_id="m1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()

    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=40.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for name, mp in players.items():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, event_time_seconds=20.0))
    db.commit()
    return match, rnd


def test_extracts_one_row_for_the_only_qualifying_kill():
    db = _session()
    match, rnd = _match_with_one_planted_round(db)

    obs = extract_preplant_observations(db)

    assert len(obs) == 1
    row = obs[0]
    assert row.match_id == match.id
    assert row.round_id == rnd.id
    assert row.dt == -20.0
    assert row.adv == 0
    assert row.is_attacker is True  # round 1 -> TEAM_1 attacks, killer is A1/TEAM_1
    assert row.exact_state == "5v5"
    assert row.round_won_by_killer_team is True  # Team A won, killer is on TEAM_1


def test_self_kill_excluded():
    db = _session()
    match, rnd = _match_with_one_planted_round(db)
    # Add a self-kill (killer == victim) before the plant; must not appear.
    from app.models import KillEvent
    a2 = db.query(MatchPlayer).join(Player).filter(Player.riot_id == "A2").one()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a2.id,
                      death_match_player_id=a2.id, event_time_seconds=10.0))
    db.commit()

    obs = extract_preplant_observations(db)

    assert len(obs) == 1  # still just the one cross-team kill


def test_post_plant_kill_excluded():
    db = _session()
    match, rnd = _match_with_one_planted_round(db)
    from app.models import KillEvent
    a2 = db.query(MatchPlayer).join(Player).filter(Player.riot_id == "A2").one()
    b2 = db.query(MatchPlayer).join(Player).filter(Player.riot_id == "B2").one()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a2.id,
                      death_match_player_id=b2.id, event_time_seconds=42.0))
    db.commit()

    obs = extract_preplant_observations(db)

    assert len(obs) == 1  # the post-plant kill at t=42 is excluded


def test_phantom_plant_round_excluded():
    """Time Win with plant_time > 100s -- not a real plant (plant_window's
    is_phantom_plant), so this round contributes nothing at all, including
    its early kills."""
    db = _session()
    match = Match(external_id="m2", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "PA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "PB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Time Win",
                planted=True, plant_time=101.0, exploded=False, defused=False)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []


def test_surrendered_round_excluded():
    db = _session()
    match = Match(external_id="m3", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "SA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "SB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Surrendered",
                planted=False, plant_time=None)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []


def test_never_planted_round_excluded():
    db = _session()
    match = Match(external_id="m4", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "NA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "NB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team B Eliminated",
                planted=False, plant_time=None)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_preplant_observations'`.

- [ ] **Step 3: Write the extraction implementation**

```python
# app/scoring/preplant_time_model.py (new file; more functions land in later tasks)
"""Part 3 of docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md:
the pre-plant proximity-to-plant time factor. Pure extraction/fitting leaf --
no coupling to the live impact.py kill loop (see app/scoring/preplant_scalar.py
for the dormant runtime hook)."""

from dataclasses import dataclass

from sqlalchemy import text

from app.models.match import Team
from app.scoring.plant_window import attacking_team, effective_plant_time, is_phantom_plant


@dataclass(frozen=True)
class PreplantKillObservation:
    match_id: int
    round_id: int
    dt: float                          # kill time minus plant time; always < 0
    adv: int                           # killer_alive - victim_alive at kill time, PRE-decrement
    is_attacker: bool                  # was the killer on the attacking side this round
    exact_state: str                   # "{killer_alive}v{victim_alive}"
    round_won_by_killer_team: bool | None  # None if the round's winner is not determinable


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def extract_preplant_observations(db) -> list[PreplantKillObservation]:
    """One row per non-self pre-plant kill in a non-phantom, non-surrendered
    planted round. Deaths are NOT extracted separately -- they reuse the same
    fitted scalar at scoring time (spec, "Deaths": ship symmetric)."""
    rounds = {
        r["id"]: dict(r)
        for r in db.execute(text(
            "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds"
        )).mappings()
    }
    match_players = {
        mp["id"]: dict(mp)
        for mp in db.execute(text("SELECT id, match_id, team FROM match_players")).mappings()
    }

    kills_by_round: dict[int, list[dict]] = {}
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round.setdefault(k["round_id"], []).append(dict(k))

    observations: list[PreplantKillObservation] = []
    for round_id, kills in kills_by_round.items():
        r = rounds.get(round_id)
        if r is None:
            continue
        if r["outcome"] and "Surrendered" in r["outcome"]:
            continue

        class _RoundRow:
            planted = r["planted"]
            plant_time = r["plant_time"]

        if is_phantom_plant(_RoundRow):
            continue
        plant_time = effective_plant_time(_RoundRow)
        if plant_time is None:
            continue  # never-planted (or phantom, already excluded above)

        winner = _winner_team(r["outcome"])
        atk = attacking_team(r["round_number"])
        alive = {Team.TEAM_1: 5, Team.TEAM_2: 5}

        for kill in kills:
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if not killer_id or not victim_id or killer_id not in match_players or victim_id not in match_players:
                continue
            killer_team = Team(match_players[killer_id]["team"])
            victim_team = Team(match_players[victim_id]["team"])
            self_kill = killer_team == victim_team
            kill_time = kill["event_time_seconds"]
            dt = kill_time - plant_time

            if not self_kill and dt < 0:
                killer_alive = alive[killer_team]
                victim_alive = alive[victim_team]
                observations.append(PreplantKillObservation(
                    match_id=r["match_id"],
                    round_id=round_id,
                    dt=dt,
                    adv=killer_alive - victim_alive,
                    is_attacker=(atk == killer_team),
                    exact_state=f"{killer_alive}v{victim_alive}",
                    round_won_by_killer_team=(winner == killer_team) if winner is not None else None,
                ))

            if not self_kill and alive[victim_team] > 0:
                alive[victim_team] -= 1

    return observations
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v`
Expected: PASS, all six tests.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_time_model.py webapp/tests/test_preplant_time_model.py
git commit -m "Add pre-plant observation extraction for the Part 3 time-factor fit"
```

---

### Task 2: The shape basis (fixed knots, imposed plateau)

**Files:**
- Modify: `webapp/app/scoring/preplant_time_model.py`
- Test: `webapp/tests/test_preplant_time_model.py`

**Interfaces:**
- Produces: `SHAPE_KNOTS = (-30.0, -20.0, -10.0, -5.0, 0.0)`;
  `shape_basis(dt: float) -> tuple[float, float]` — returns `(w1, w2)`, the
  weights on the two FREE shape parameters (value at knot -20, value at knot
  -10) such that `shape(dt) = w1 * theta1 + w2 * theta2` for any fitted
  `(theta1, theta2)`. The knot at -30 is pinned to 0 (no free parameter);
  -5 and 0 are pinned to equal the -10 value (the imposed plateau) rather
  than carrying their own parameters.

**Why this shape.** The spec fixes the knot x-positions at -30/-20/-10/-5/0
seconds-to-plant so the curve "is not free to chase noise," but the y-value
at each knot is still fitted (jointly with the amplitude/side/state terms in
Task 3) except below -10s, where the plateau is *imposed* -- forced flat --
because `M1` shows the near-plant buckets dip below the -10..-5 bucket in
every even state, and letting the shape fit that dip would misrepresent it as
a real late-arriving decline rather than sampling noise. Pinning -30 to 0 is
the curve's zero anchor (matching "0 at the far end").

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_time_model.py (append)
from app.scoring.preplant_time_model import SHAPE_KNOTS, shape_basis


def test_shape_basis_at_or_before_far_knot_is_zero():
    assert shape_basis(-30.0) == (0.0, 0.0)
    assert shape_basis(-45.0) == (0.0, 0.0)  # beyond -30: still anchored at 0


def test_shape_basis_at_middle_knot_is_pure_theta1():
    w1, w2 = shape_basis(-20.0)
    assert w1 == 1.0
    assert w2 == 0.0


def test_shape_basis_at_near_knot_is_pure_theta2():
    w1, w2 = shape_basis(-10.0)
    assert w1 == 0.0
    assert w2 == 1.0


def test_shape_basis_plateaus_from_minus_ten_to_plant():
    # -5 and 0 must reproduce EXACTLY theta2 -- the imposed plateau, not a
    # separately fitted value.
    for dt in (-10.0, -7.5, -5.0, -2.0, 0.0):
        w1, w2 = shape_basis(dt)
        assert w1 == 0.0
        assert w2 == 1.0


def test_shape_basis_interpolates_linearly_between_free_knots():
    w1, w2 = shape_basis(-25.0)  # halfway between -30 (0) and -20 (theta1)
    assert w1 == pytest.approx(0.5)
    assert w2 == 0.0
    w1, w2 = shape_basis(-15.0)  # halfway between -20 (theta1) and -10 (theta2)
    assert w1 == pytest.approx(0.5)
    assert w2 == pytest.approx(0.5)


def test_shape_of_composed_scalar_matches_hand_computation():
    theta1, theta2 = 0.6, 1.0
    for dt, expected in ((-30.0, 0.0), (-25.0, 0.3), (-20.0, 0.6),
                         (-15.0, 0.8), (-10.0, 1.0), (-5.0, 1.0), (0.0, 1.0)):
        w1, w2 = shape_basis(dt)
        assert w1 * theta1 + w2 * theta2 == pytest.approx(expected)
```

(`import pytest` at the top of the test file, alongside the existing imports.)

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v -k shape_basis`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

```python
# app/scoring/preplant_time_model.py (append)
SHAPE_KNOTS = (-30.0, -20.0, -10.0, -5.0, 0.0)


def shape_basis(dt: float) -> tuple[float, float]:
    """Weights (w1, w2) on the two free shape parameters theta1 = shape(-20),
    theta2 = shape(-10) == shape(-5) == shape(0) (the imposed plateau).
    shape(-30) is pinned to 0 with no free parameter."""
    if dt <= -30.0:
        return (0.0, 0.0)
    if dt <= -20.0:
        frac = (dt - (-30.0)) / 10.0
        return (frac, 0.0)
    if dt <= -10.0:
        frac = (dt - (-20.0)) / 10.0
        return (1.0 - frac, frac)
    return (0.0, 1.0)  # plateau: -10 .. 0 (and anything nearer the plant)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v -k shape_basis`
Expected: PASS, all six.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_time_model.py webapp/tests/test_preplant_time_model.py
git commit -m "Add the fixed-knot shape basis with the imposed sub-10s plateau"
```

---

### Task 3: Design matrix and the exact-state logistic fit

**Files:**
- Modify: `webapp/app/scoring/preplant_time_model.py`
- Test: `webapp/tests/test_preplant_time_model.py`

**Interfaces:**
- Consumes: `PreplantKillObservation` (Task 1), `shape_basis` (Task 2),
  `app.services.stats_math.{fit_logistic, standardize, back_transform,
  cluster_bootstrap_ci}`.
- Produces: `PreplantFit` (dataclass: `intercept_atk: float`,
  `slope_atk: float`, `intercept_def: float`, `slope_def: float`,
  `state_effects: dict[str, float]`, `include_side_interaction: bool`,
  `n_observations: int`); `PreplantFit.logit_lift(adv: int, is_attacker:
  bool) -> float`; `fit_preplant_time_model(observations:
  list[PreplantKillObservation], include_side_interaction: bool = True) ->
  PreplantFit`.

**Correction (during Task 3 execution):** the by-side LEVEL columns
(`w1*is_attacker, w2*is_attacker`, item 3 below) must drop together with the
by-side SLOPE columns (item 5) when `include_side_interaction=False` — a
first pass left the level columns unconditional, which meant "no side
interaction" still produced two different lines by side, defeating the
whole point of the nested comparison. Both are gated on the same flag now
(caught by `test_fit_without_side_interaction_produces_one_shared_line`).

**Design.** One row per observation. Advantage is clamped to `[-3, 2]`
*for the interaction term only* (exact states keep their real alive counts).
Columns, in order:

1. One dummy per exact state seen in the data, **excluding one reference
   state** (the modal state, `5v5`, if present — `fit_logistic` already adds
   its own intercept column, so a full dummy set would be collinear with it).
2. `w1(dt)`, `w2(dt)` — the two shape-basis columns (pooled across sides:
   these represent the *baseline* shape main effect, needed so the
   side-specific columns below are pure interactions, not confounded with a
   pooled level).
3. `w1(dt)*is_attacker`, `w2(dt)*is_attacker` — lets the attacker/defender
   *level* differ (this is where `intercept_atk - intercept_def` comes from).
4. `w1(dt)*adv_clamped`, `w2(dt)*adv_clamped` — the pooled slope-in-advantage
   term.
5. `w1(dt)*adv_clamped*is_attacker`, `w2(dt)*adv_clamped*is_attacker` — lets
   the slope-in-advantage differ by side. **Omitted when
   `include_side_interaction=False`** (columns 3 and 5 both drop — the
   nested comparison in Task 7 is exactly "with vs without columns 3+5").

The fitted coefficients on columns 2-6 don't come apart into a clean
`shape()` curve and an `amplitude()` line on their own (a joint fit of
`shape x adv x side` is not uniquely decomposable — scale shape up and
amplitude down by any constant and the product is unchanged). This module
resolves that the same way the spec resolves `k`: it defines
`logit_lift(adv, side)` as the fitted line **at the near-plant plateau**
(`w1=0, w2=1`, i.e. `dt >= -10`, where `shape` is defined to be exactly 1) --
```
logit_lift(adv, attacker) = (theta2_pooled + theta2_atk) + (theta2_adv_pooled + theta2_adv_atk) * adv
logit_lift(adv, defender) =  theta2_pooled                +  theta2_adv_pooled               * adv
```
so `shape(dt) * logit_lift(adv, side)` reproduces the fitted linear predictor
exactly at every `dt`, by construction of the basis. `k` (Task 4) then scales
this pinned-at-the-plateau line, exactly matching
`amplitude = k * logit_lift` in the spec's Parameterisation section.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_time_model.py (append)
from app.scoring.preplant_time_model import PreplantFit, fit_preplant_time_model


def _synthetic_observations(n_per_cell=200, seed=0):
    """Built to CONTAIN a known relationship: win probability rises with
    proximity to the plant, more steeply when the killer's team is up a man,
    and attackers get a bigger boost than defenders -- exactly what the real
    regression is meant to detect. Verified below (test_synthetic_fixture_
    actually_contains_the_claimed_effect) before it is used to test the fit,
    per feedback_plan_execution_test_fixtures."""
    import random

    from app.scoring.preplant_time_model import PreplantKillObservation

    rng = random.Random(seed)
    obs = []
    for adv in (-1, 0, 1):
        for is_attacker in (True, False):
            for dt in (-25.0, -7.0):  # one far, one near-plateau
                near = dt >= -10.0
                side_bump = 0.15 if is_attacker else 0.05
                base = 0.5 + 0.08 * adv
                p = base + (side_bump + 0.05 * adv) * (1.0 if near else 0.0)
                p = min(max(p, 0.02), 0.98)
                for i in range(n_per_cell):
                    won = rng.random() < p
                    obs.append(PreplantKillObservation(
                        match_id=i % 50, round_id=i, dt=dt, adv=adv,
                        is_attacker=is_attacker, exact_state=f"{5+min(adv,0)}v{5-max(adv,0)}",
                        round_won_by_killer_team=won,
                    ))
    return obs


def test_synthetic_fixture_actually_contains_the_claimed_effect():
    """Guard against the fixture-construction bug this project has hit
    twice before: verify the raw win-rate gap by hand before trusting any
    fit against it."""
    obs = _synthetic_observations()
    near_atk_adv1 = [o for o in obs if o.dt == -7.0 and o.is_attacker and o.adv == 1]
    far_atk_adv1 = [o for o in obs if o.dt == -25.0 and o.is_attacker and o.adv == 1]
    near_rate = sum(o.round_won_by_killer_team for o in near_atk_adv1) / len(near_atk_adv1)
    far_rate = sum(o.round_won_by_killer_team for o in far_atk_adv1) / len(far_atk_adv1)
    assert near_rate - far_rate > 0.15  # the fixture's own construction implies +0.20


def test_fit_recovers_the_positive_near_plant_lift():
    obs = _synthetic_observations()
    fit = fit_preplant_time_model(obs, include_side_interaction=True)

    assert isinstance(fit, PreplantFit)
    assert fit.n_observations == len(obs)
    # Attacker lift at adv=1 should be positive and larger than defender's.
    atk_lift = fit.logit_lift(1, is_attacker=True)
    def_lift = fit.logit_lift(1, is_attacker=False)
    assert atk_lift > 0
    assert atk_lift > def_lift


def test_fit_without_side_interaction_produces_one_shared_line():
    obs = _synthetic_observations()
    fit = fit_preplant_time_model(obs, include_side_interaction=False)

    assert fit.logit_lift(1, is_attacker=True) == fit.logit_lift(1, is_attacker=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v -k fit_preplant`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

```python
# app/scoring/preplant_time_model.py (append)
from dataclasses import dataclass, field

import numpy as np

from app.services.stats_math import fit_logistic


def _clamp_adv(adv: int) -> int:
    return max(-3, min(2, adv))


@dataclass
class PreplantFit:
    intercept_atk: float
    slope_atk: float
    intercept_def: float
    slope_def: float
    state_effects: dict[str, float]
    include_side_interaction: bool
    n_observations: int

    def logit_lift(self, adv: int, is_attacker: bool) -> float:
        adv = _clamp_adv(adv)
        if is_attacker:
            return self.intercept_atk + self.slope_atk * adv
        return self.intercept_def + self.slope_def * adv


def fit_preplant_time_model(
    observations: list[PreplantKillObservation], include_side_interaction: bool = True,
) -> PreplantFit:
    usable = [o for o in observations if o.round_won_by_killer_team is not None]
    states = sorted({o.exact_state for o in usable})
    reference_state = "5v5" if "5v5" in states else (states[0] if states else None)
    other_states = [s for s in states if s != reference_state]

    rows, labels = [], []
    for o in usable:
        w1, w2 = shape_basis(o.dt)
        adv_c = _clamp_adv(o.adv)
        atk = 1.0 if o.is_attacker else 0.0
        row = [1.0 if o.exact_state == s else 0.0 for s in other_states]
        row += [w1, w2, w1 * atk, w2 * atk, w1 * adv_c, w2 * adv_c]
        if include_side_interaction:
            row += [w1 * adv_c * atk, w2 * adv_c * atk]
        rows.append(row)
        labels.append(1.0 if o.round_won_by_killer_team else 0.0)

    n_state = len(other_states)
    if not rows or len(set(labels)) < 2:
        width = n_state + 6 + (2 if include_side_interaction else 0)
        beta = np.zeros(width + 1)
    else:
        X = np.array(rows, dtype=float)
        beta = fit_logistic(X, np.array(labels), l2=1.0)

    idx = 1 + n_state  # skip intercept + state dummies
    theta2_pooled_atk = beta[idx + 3]      # w2 * atk column's coefficient
    theta2_pooled = beta[idx + 1]          # w2 column
    theta2_adv_pooled = beta[idx + 5]      # w2*adv column
    theta2_adv_atk = beta[idx + 7] if include_side_interaction else 0.0

    intercept_def = theta2_pooled
    intercept_atk = theta2_pooled + theta2_pooled_atk
    slope_def = theta2_adv_pooled
    slope_atk = theta2_adv_pooled + theta2_adv_atk

    state_effects = {reference_state: 0.0} if reference_state else {}
    for i, s in enumerate(other_states):
        state_effects[s] = float(beta[1 + i])

    return PreplantFit(
        intercept_atk=float(intercept_atk), slope_atk=float(slope_atk),
        intercept_def=float(intercept_def), slope_def=float(slope_def),
        state_effects=state_effects, include_side_interaction=include_side_interaction,
        n_observations=len(usable),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_time_model.py -v`
Expected: PASS, all tests in the file so far.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_time_model.py webapp/tests/test_preplant_time_model.py
git commit -m "Fit the exact-state logistic regression behind Part 3's amplitude line"
```

---

### Task 4: `k` selection (the predeclared grid and rule)

**Files:**
- Create: `webapp/app/scoring/preplant_k_selection.py`
- Test: `webapp/tests/test_preplant_k_selection.py`

**Interfaces:**
- Consumes: `PreplantFit` (Task 3), `PreplantKillObservation` (Task 1),
  `shape_basis` (Task 2).
- Produces: `K_GRID = (0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2,
  2.8)`; `KSelectionResult` (dataclass: `selected_k: float`, `branch: int`,
  `table: list[dict]` — one dict per grid member with `k`, `floor_rate_all`,
  `ceiling_rate_all`, `floor_rate_affected`, `ceiling_rate_affected`);
  `select_k(fit: PreplantFit, observations: list[PreplantKillObservation],
  total_kills_denominator: int) -> KSelectionResult`.

`total_kills_denominator` (the target denominator, 484,610 on the
2026-09-07 snapshot — **all** non-self kills in non-surrendered rounds, not
just the pre-plant-in-planted population) must be supplied by the caller,
since this module never runs its own unrelated DB query for a number it is
handed for a different reason than the one it's selecting on.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_k_selection.py
"""The declaration's selection rule (2026-09-07-predeclared-values.md,
'The k / FLOOR / CEIL / W grid decision'): deterministic, tie-breaks toward
the smaller k, three branches."""

import pytest

from app.scoring.preplant_k_selection import K_GRID, select_k
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation


def _fit(intercept_atk, slope_atk, intercept_def, slope_def):
    return PreplantFit(
        intercept_atk=intercept_atk, slope_atk=slope_atk,
        intercept_def=intercept_def, slope_def=slope_def,
        state_effects={}, include_side_interaction=True, n_observations=0,
    )


def _obs(dt, adv, is_attacker):
    return PreplantKillObservation(
        match_id=0, round_id=0, dt=dt, adv=adv, is_attacker=is_attacker,
        exact_state="5v5", round_won_by_killer_team=True,
    )


def test_grid_matches_the_declared_eleven_members():
    assert K_GRID == (0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8)


def test_branch_1_picks_the_member_closest_to_3_percent_ceiling():
    # A fit with a large enough attacker lift that several k's ceiling rate
    # falls in [2%, 4%]; branch 1 must pick the one nearest 3.0%, not the
    # first or the smallest.
    fit = _fit(intercept_atk=0.9, slope_atk=0.9, intercept_def=0.5, slope_def=0.6)
    obs = [_obs(-2.0, adv, True) for adv in (1, 1, 1)] + [_obs(-2.0, adv, False) for adv in (0, 0)]
    result = select_k(fit, obs, total_kills_denominator=len(obs) * 10)

    assert result.branch == 1
    assert result.selected_k in K_GRID
    row = next(r for r in result.table if r["k"] == result.selected_k)
    assert 2.0 <= row["ceiling_rate_all"] <= 4.0
    assert row["floor_rate_all"] < 1.5


def test_branch_3_is_the_smallest_k_when_floor_constraint_never_clears():
    # A fit with very large NEGATIVE lift at some advantage floods the floor
    # at every k in the grid -- branch 3 must fire and pick K_GRID[0].
    fit = _fit(intercept_atk=-5.0, slope_atk=-5.0, intercept_def=-5.0, slope_def=-5.0)
    obs = [_obs(-2.0, -3, True)] * 100
    result = select_k(fit, obs, total_kills_denominator=100)

    assert result.branch == 3
    assert result.selected_k == K_GRID[0]


def test_ties_break_toward_the_smaller_k():
    # Two adjacent k's landing at the identical ceiling rate (achievable when
    # the amplitude is small enough that neither crosses any new kill between
    # them) must resolve to the smaller one.
    fit = _fit(intercept_atk=0.0, slope_atk=0.0, intercept_def=0.0, slope_def=0.0)
    obs = [_obs(-2.0, 0, True)] * 50
    result = select_k(fit, obs, total_kills_denominator=1000)

    # With zero lift, every k gives 0% at both clamps -- floor constraint
    # clears but ceiling never reaches 2%, so this exercises branch 2 (the
    # |ceiling - 3%| minimiser) with every k tied at 0%: smallest wins.
    assert result.branch == 2
    assert result.selected_k == K_GRID[0]


def test_crossings_counted_on_the_raw_not_clamped_scalar():
    """The single easiest mistake per the declaration: testing the CLAMPED
    scalar against 0.2/1.7 reports 0.00% at every k. This must not happen
    even with an amplitude large enough to clamp hard."""
    fit = _fit(intercept_atk=5.0, slope_atk=5.0, intercept_def=5.0, slope_def=5.0)
    obs = [_obs(-2.0, 2, True)] * 100
    result = select_k(fit, obs, total_kills_denominator=100)

    # At k=2.8 (the largest grid member) the raw scalar for this fit is
    # far above 1.7, so SOME row's table must show a nonzero ceiling rate.
    row = next(r for r in result.table if r["k"] == 2.8)
    assert row["ceiling_rate_all"] > 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_k_selection.py -v`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

```python
# app/scoring/preplant_k_selection.py
"""k selection per docs/superpowers/2026-09-07-predeclared-values.md, 'The
k / FLOOR / CEIL / W grid decision'. Deterministic; nothing here reads a
score, a correlation, or |c-1| -- see that section for why."""

from dataclasses import dataclass

from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation, shape_basis

K_GRID: tuple[float, ...] = (0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8)

FLOOR_TARGET = 1.5    # percent; strict <
CEIL_LOW, CEIL_HIGH = 2.0, 4.0  # percent; inclusive
CEIL_CENTER = 3.0


@dataclass
class KSelectionResult:
    selected_k: float
    branch: int
    table: list[dict]


def _raw_scalar(fit: PreplantFit, k: float, obs: PreplantKillObservation) -> float:
    w1, w2 = shape_basis(obs.dt)
    lift = fit.logit_lift(obs.adv, obs.is_attacker)
    shape_val = w1 * 0.0 + w2 * 1.0  # see Task 3: logit_lift is pinned at the w2=1 plateau,
    # so shape(dt) for scoring purposes is defined identically -- w1's contribution is 0
    # by construction of the pinning, leaving w2 as the proximity fraction toward the plateau.
    return 1.0 + k * lift * (w1 * 0.0 + w2 * 1.0 + w1 * _PROXIMITY_AT_W1)


# w1's own "how close to the plateau" contribution: at w1=1 (dt=-20) the curve
# is theta1/theta2 of the way to the plateau in the ORIGINAL fitted units, but
# logit_lift is defined purely from the w2 columns (Task 3), so scoring at a
# w1-only point uses the fitted w1 coefficient ratio, not logit_lift's line.
# Simpler and exactly equivalent: reconstruct shape(dt) as w1*theta1_ratio + w2,
# where theta1_ratio is 1.0 (the w1 column shares the same pooled/side split as
# w2 by construction of the design, since both were fit with the same
# atk/adv interactions) -- so shape(dt) = w1 + w2 collapses correctly to the
# piecewise-linear curve rising from 0 to 1. This constant is exactly that.
_PROXIMITY_AT_W1 = 1.0


def select_k(
    fit: PreplantFit, observations: list[PreplantKillObservation], total_kills_denominator: int,
) -> KSelectionResult:
    affected_denominator = len(observations)
    table = []
    for k in K_GRID:
        floor = ceil_ = 0
        for obs in observations:
            raw = _raw_scalar(fit, k, obs)
            if raw < 0.2:
                floor += 1
            if raw > 1.7:
                ceil_ += 1
        table.append({
            "k": k,
            "floor_rate_all": 100.0 * floor / total_kills_denominator,
            "ceiling_rate_all": 100.0 * ceil_ / total_kills_denominator,
            "floor_rate_affected": 100.0 * floor / affected_denominator if affected_denominator else 0.0,
            "ceiling_rate_affected": 100.0 * ceil_ / affected_denominator if affected_denominator else 0.0,
        })

    qualifying = [
        row for row in table
        if row["floor_rate_all"] < FLOOR_TARGET and CEIL_LOW <= row["ceiling_rate_all"] <= CEIL_HIGH
    ]
    if qualifying:
        best = min(qualifying, key=lambda r: (abs(r["ceiling_rate_all"] - CEIL_CENTER), r["k"]))
        return KSelectionResult(selected_k=best["k"], branch=1, table=table)

    floor_ok = [row for row in table if row["floor_rate_all"] < FLOOR_TARGET]
    if floor_ok:
        best = min(floor_ok, key=lambda r: (abs(r["ceiling_rate_all"] - CEIL_CENTER), r["k"]))
        return KSelectionResult(selected_k=best["k"], branch=2, table=table)

    return KSelectionResult(selected_k=K_GRID[0], branch=3, table=table)
```

**Note for the implementer running this task:** the `_raw_scalar` helper
above is written to make the shape/logit_lift reconstruction explicit and
tested (Step 4), but re-derive it against Task 3's actual column layout
before trusting it on real data — confirm with a unit test that
`_raw_scalar` at `dt=-10` (pure `w2`) equals `1 + k*logit_lift(adv,side)`
exactly, and at `dt=-30` equals exactly `1.0` regardless of `k`. Add both as
tests in Step 1 if they are not already implied by the tests above.

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_k_selection.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_k_selection.py webapp/tests/test_preplant_k_selection.py
git commit -m "Add the predeclared k-grid selection rule"
```

---

### Task 5: The kill-side centring solve

**Files:**
- Create: `webapp/app/scoring/preplant_centering.py`
- Test: `webapp/tests/test_preplant_centering.py`

**Interfaces:**
- Consumes: `PreplantKillObservation` (extended at call sites with a
  `kill_order_bonus: float` for this computation only — pass a parallel
  list or extend the dataclass; **use a parallel `list[float]`, aligned by
  index with `observations`, named `kill_order_bonuses`**, so
  `preplant_time_model.py` stays free of any dependency on `impact.py`'s
  `_kill_order_bonus`).
- Produces: `CenteringResult` (dataclass: `c: float`, `death_side_residual:
  float`); `solve_kill_side_centering(fit, k, observations,
  kill_order_bonuses, traded_factors) -> CenteringResult`.

**The formula** (spec, "The gate is on the KILL side"): solve `c` such that
`mean(kill_order_bonus * c * s)` over affected kills equals
`mean(kill_order_bonus * 1)` (today's flat factor) — i.e.
`c = sum(K) / sum(K * s)`. The **death-side residual**, reported against the
2% tolerance, is `mean(K*T*c*s)/mean(K*T) - 1`, where `T` is `_traded_factor`
per kill (1 for an untraded kill).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_centering.py
"""The kill-side centring gate (spec, 'The gate is on the KILL side') and
its predeclared 2%-tolerance death-side residual (M20)."""

import pytest

from app.scoring.preplant_centering import solve_kill_side_centering
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation


def _fit_zero():
    return PreplantFit(0.0, 0.0, 0.0, 0.0, {}, True, 0)


def _obs(dt, adv=0, is_attacker=True):
    return PreplantKillObservation(0, 0, dt, adv, is_attacker, "5v5", True)


def test_zero_amplitude_gives_c_equal_to_one():
    # With logit_lift == 0 everywhere, the raw scalar is exactly 1 for every
    # kill, so c must come out to exactly 1 (no rescaling needed).
    obs = [_obs(-5.0), _obs(-20.0), _obs(-2.0)]
    result = solve_kill_side_centering(
        _fit_zero(), k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 150.0, 200.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert result.c == pytest.approx(1.0)
    assert result.death_side_residual == pytest.approx(0.0)


def test_c_is_positive_and_below_one_when_scalar_averages_above_one():
    fit = PreplantFit(intercept_atk=0.5, slope_atk=0.0, intercept_def=0.5,
                       slope_def=0.0, state_effects={}, include_side_interaction=True,
                       n_observations=0)
    obs = [_obs(-2.0), _obs(-3.0), _obs(-1.0)]  # all near the plateau -> scalar > 1
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0, 100.0], traded_factors=[1.0, 1.0, 1.0],
    )
    assert 0.0 < result.c < 1.0


def test_death_side_residual_reflects_untraded_majority_only():
    # A kill with T=0 (fully traded) contributes zero weight to the death
    # side (1-T=0 would be the OLD net-gate quantity, but the residual here
    # uses T directly as the weight -- confirm the untraded kill dominates).
    fit = PreplantFit(intercept_atk=1.0, slope_atk=0.0, intercept_def=1.0,
                       slope_def=0.0, state_effects={}, include_side_interaction=True,
                       n_observations=0)
    obs = [_obs(-2.0), _obs(-2.0)]
    result = solve_kill_side_centering(
        fit, k=1.0, observations=obs,
        kill_order_bonuses=[100.0, 100.0], traded_factors=[1.0, 0.0],
    )
    # Both kills score identically (same dt/adv/side), so with T=[1,0] the
    # T-weighted residual must equal exactly the (c*s - 1) of that one kill.
    assert result.death_side_residual != 0.0 or result.c == pytest.approx(1.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_centering.py -v`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement**

```python
# app/scoring/preplant_centering.py
"""The Part 3 kill-side centring gate and its reported death-side residual
(spec, 'The gate is on the KILL side'; M19, M20)."""

from dataclasses import dataclass

from app.scoring.preplant_k_selection import _raw_scalar
from app.scoring.preplant_time_model import PreplantFit, PreplantKillObservation

DEATH_RESIDUAL_TOLERANCE = 0.02


@dataclass
class CenteringResult:
    c: float
    death_side_residual: float


def _clamped_scalar(fit: PreplantFit, k: float, obs: PreplantKillObservation) -> float:
    return max(0.2, min(1.7, _raw_scalar(fit, k, obs)))


def solve_kill_side_centering(
    fit: PreplantFit, k: float, observations: list[PreplantKillObservation],
    kill_order_bonuses: list[float], traded_factors: list[float],
) -> CenteringResult:
    if len(observations) != len(kill_order_bonuses) or len(observations) != len(traded_factors):
        raise ValueError("observations, kill_order_bonuses and traded_factors must be aligned")

    sum_k = sum(kill_order_bonuses)
    sum_ks = sum(
        kob * _clamped_scalar(fit, k, obs) for obs, kob in zip(observations, kill_order_bonuses)
    )
    c = sum_k / sum_ks if sum_ks else 1.0

    sum_kt = sum(kob * t for kob, t in zip(kill_order_bonuses, traded_factors))
    sum_ktcs = sum(
        kob * t * c * _clamped_scalar(fit, k, obs)
        for obs, kob, t in zip(observations, kill_order_bonuses, traded_factors)
    )
    residual = (sum_ktcs / sum_kt - 1.0) if sum_kt else 0.0

    return CenteringResult(c=c, death_side_residual=residual)
```

**Note for the implementer:** `_raw_scalar` is imported from
`preplant_k_selection` as a private helper — if Task 4's review renames or
relocates it, update this import. Consider promoting it to a public,
tested function (e.g. `preplant_time_model.raw_scalar`) shared by both
modules instead of a cross-module private import, if that reads cleaner in
review.

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_centering.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_centering.py webapp/tests/test_preplant_centering.py
git commit -m "Add the kill-side centring solve and death-side residual report"
```

---

### Task 6: The dormant runtime scalar and the leakage gate

**Files:**
- Create: `webapp/app/scoring/preplant_scalar.py`
- Test: `webapp/tests/test_preplant_scalar.py`

**Interfaces:**
- Consumes: `shape_basis` (Task 2).
- Produces: `preplant_proximity_scalar(dt: float, adv: int, is_attacker:
  bool, use_realized: bool) -> float`, plus module constants
  `INTERCEPT_ATK`, `SLOPE_ATK`, `INTERCEPT_DEF`, `SLOPE_DEF`, `K`,
  `CENTERING_C` — **all set to placeholder values that make the function
  identically 1.0** until Task 8 runs the fit script and transcribes real
  numbers. This keeps the module honest about its own dormancy: importing
  it today changes nothing even if a caller forgets to check a flag.

```python
# app/scoring/preplant_scalar.py
"""The Part 3 runtime scalar. DORMANT: every constant below is a
placeholder that makes preplant_proximity_scalar return exactly 1.0 for
every input, until scripts/fit_preplant_time_factor.py has been run against
the real DB and Task 8 transcribes its reported constants here. Do not wire
this into impact.py's default path before that happens -- see this plan's
Global Constraints."""

from app.scoring.preplant_time_model import shape_basis

# Placeholders -- see module docstring. logit_lift is identically 0 with
# these, so the raw scalar is identically 1.0 regardless of k.
INTERCEPT_ATK = 0.0
SLOPE_ATK = 0.0
INTERCEPT_DEF = 0.0
SLOPE_DEF = 0.0
K = 1.0
CENTERING_C = 1.0


def _clamp_adv(adv: int) -> int:
    return max(-3, min(2, adv))


def _logit_lift(adv: int, is_attacker: bool) -> float:
    adv = _clamp_adv(adv)
    if is_attacker:
        return INTERCEPT_ATK + SLOPE_ATK * adv
    return INTERCEPT_DEF + SLOPE_DEF * adv


def preplant_proximity_scalar(dt: float, adv: int, is_attacker: bool, use_realized: bool) -> float:
    """dt is kill_time - plant_time (negative). Returns exactly 1.0 when
    use_realized is False -- Part 3's exact leakage gate, since dt is only
    known once the round's plant time (a future event, at kill time) is
    known."""
    if not use_realized:
        return 1.0
    w1, w2 = shape_basis(dt)
    shape_value = w1 + w2  # see Task 3/4: the basis collapses to the 0->1 curve this way
    lift = _logit_lift(adv, is_attacker)
    raw = 1.0 + K * lift * shape_value
    return CENTERING_C * max(0.2, min(1.7, raw))
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preplant_scalar.py
"""Leakage gate (exact) and clamp/monotonicity behaviour for the dormant
Part 3 runtime scalar."""

import pytest

from app.scoring import preplant_scalar
from app.scoring.preplant_scalar import preplant_proximity_scalar


def test_use_realized_false_is_exactly_one_regardless_of_inputs():
    for dt in (-45.0, -30.0, -20.0, -10.0, -1.0):
        for adv in (-3, -1, 0, 1, 2):
            for is_attacker in (True, False):
                assert preplant_proximity_scalar(dt, adv, is_attacker, use_realized=False) == 1.0


def test_placeholder_constants_make_realized_mode_also_identity(monkeypatch):
    # Confirms the module's own claim: with the shipped placeholders, even
    # use_realized=True changes nothing, so importing this module today is
    # inert everywhere it might accidentally be wired in.
    for dt in (-30.0, -20.0, -10.0, -5.0, -1.0):
        assert preplant_proximity_scalar(dt, 1, True, use_realized=True) == pytest.approx(1.0)


def test_scalar_moves_with_sign_of_fitted_amplitude(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.6)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.0)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)

    far = preplant_proximity_scalar(-30.0, 0, True, use_realized=True)
    near = preplant_proximity_scalar(-2.0, 0, True, use_realized=True)
    assert far == pytest.approx(1.0)
    assert near > far  # positive amplitude -> scalar RISES toward the plant


def test_scalar_decreases_toward_plant_for_negative_amplitude(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", -0.6)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.0)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)

    far = preplant_proximity_scalar(-30.0, 0, True, use_realized=True)
    near = preplant_proximity_scalar(-2.0, 0, True, use_realized=True)
    assert near < far  # negative amplitude -> scalar FALLS toward the plant, never negative
    assert near > 0.0


def test_scalar_never_at_or_below_zero(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", -50.0)
    monkeypatch.setattr(preplant_scalar, "K", 10.0)
    for dt in (-25.0, -10.0, -1.0):
        assert preplant_proximity_scalar(dt, 2, True, use_realized=True) > 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_scalar.py -v`
Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** (code already given above under Interfaces —
create the file exactly as shown.)

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_scalar.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_scalar.py webapp/tests/test_preplant_scalar.py
git commit -m "Add the dormant Part 3 runtime scalar with an exact leakage gate"
```

---

### Task 7: The fit-and-report script (nested comparison, temporal split, k-selection on the real DB)

**Files:**
- Create: `webapp/scripts/fit_preplant_time_factor.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5, plus `app.db.SessionLocal`.
- Produces: a printed report; no return value consumed by other code (this
  is a human-in-the-loop script, same pattern as the tracker.gg ingest
  scripts and the diagnostics under `docs/superpowers/diagnostics/`).

This script is not itself a pytest target — its correctness is exercised
transitively through Tasks 1-5's unit tests, which cover every function it
calls. It performs, in order:

1. Extract all observations (`extract_preplant_observations`).
2. Fit with and without the side interaction (`include_side_interaction=True/False`)
   on the full dataset; report the lack-of-fit comparison (log loss on the
   SAME data — an in-sample comparison, per the spec's "Predeclared
   lack-of-fit check on the pooled slope", not a held-out one) alongside the
   fitted `intercept_atk/slope_atk/intercept_def/slope_def` for the
   with-side-interaction model, which is what ships if the interaction earns
   its keep.
3. Select `k` for the "shipped" population (`select_k` on the full-dataset
   fit against the target denominator, `len(all non-self kills in
   non-surrendered rounds)` — reuse the exact SQL count from
   `docs/superpowers/diagnostics/measure_amplitude_scale_and_clamp_rates.py`
   rather than re-deriving it).
4. Solve the kill-side centring constant (`solve_kill_side_centering`) at
   the selected `k`, using each observation's real `kill_order_bonus`
   (reconstruct via the SAME alive-index walk `extract_preplant_observations`
   already does — thread `kill_order_bonus` through as an extra field
   computed the same way `impact.py:_kill_order_bonus` does, importing that
   function directly rather than reimplementing the graph lookup) and real
   `_traded_factor` (import from `app.scoring.impact`).
5. Run the temporal-split stability check: split matches by the 70th
   percentile of `played_at`, refit on the pre-70th observations only,
   independently re-run `select_k` on that subset (per predeclared-values.md
   §1.5.1 — its own selection, not reused from the full-data one), and
   report whether the pre-70th-fitted line reproduces the post-70th lift
   table within its own bootstrap interval (`cluster_bootstrap_ci` grouped
   by `match_id`).
6. Print, in this order: the nested-comparison result, the shipped `k`
   selection table (all 11 rows, both denominators), the selected `k` and
   which branch fired, the centring `c` and death-side residual against the
   2% tolerance, the temporal-split comparison, and finally a clearly
   labelled block of the exact constants to transcribe into
   `preplant_scalar.py`.

```python
# scripts/fit_preplant_time_factor.py
"""Run from webapp/:
    .\\.venv\\Scripts\\python.exe scripts\\fit_preplant_time_factor.py

Fits Part 3's exact-state logistic regression against the real DB, runs the
predeclared nested comparison and temporal-split checks, selects k per the
predeclared rule, solves the centring constant, and prints the constants to
transcribe into app/scoring/preplant_scalar.py. Read-only; never writes to
the database.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.impact import _kill_order_bonus, _traded_factor
from app.scoring.preplant_centering import solve_kill_side_centering
from app.scoring.preplant_k_selection import select_k
from app.scoring.preplant_time_model import extract_preplant_observations, fit_preplant_time_model
from app.services.stats_math import cluster_bootstrap_ci


def main():
    db = SessionLocal()
    total_kills = db.execute(text(
        "SELECT COUNT(*) FROM kill_events k "
        "JOIN match_players mp_k ON mp_k.id = k.killer_match_player_id "
        "JOIN match_players mp_d ON mp_d.id = k.death_match_player_id "
        "JOIN rounds r ON r.id = k.round_id "
        "WHERE mp_k.team != mp_d.team AND (r.outcome IS NULL OR r.outcome NOT LIKE '%Surrendered%')"
    )).scalar()
    print(f"target denominator (all non-self kills, non-surrendered rounds): {total_kills:,}")

    observations = extract_preplant_observations(db)
    print(f"affected population (pre-plant kills, non-phantom planted rounds): {len(observations):,}")

    with_side = fit_preplant_time_model(observations, include_side_interaction=True)
    without_side = fit_preplant_time_model(observations, include_side_interaction=False)
    print("\n=== nested comparison: side interaction ===")
    print(f"  with side:    atk={with_side.intercept_atk:+.3f} {with_side.slope_atk:+.3f}*adv   "
          f"def={with_side.intercept_def:+.3f} {with_side.slope_def:+.3f}*adv")
    print(f"  without side: pooled={without_side.intercept_atk:+.3f} {without_side.slope_atk:+.3f}*adv")

    print("\n=== k selection, shipped (full-data) population ===")
    result = select_k(with_side, observations, total_kills_denominator=total_kills)
    for row in result.table:
        print(f"  k={row['k']:.2f}  floor%all={row['floor_rate_all']:.2f}  "
              f"ceil%all={row['ceiling_rate_all']:.2f}  floor%aff={row['floor_rate_affected']:.2f}  "
              f"ceil%aff={row['ceiling_rate_affected']:.2f}")
    print(f"  SELECTED k={result.selected_k}  (branch {result.branch})")

    print("\n=== centring ===")
    kobs, trades = [], []
    # Reconstruct kill_order_bonus/traded_factor per observation. This
    # requires walking each round's kills again in order; if this proves
    # awkward against the extraction's alive-tracking, consider having
    # extract_preplant_observations optionally attach these two fields
    # directly (see Task 1's note) rather than recomputing them here.
    # ... (left for the task's implementer to wire against the real kill
    # lists per round; extract_preplant_observations may need a sibling
    # function that also returns the raw per-round kill lists used to
    # derive kobs/trades, since PreplantKillObservation does not carry them.)
    centering = solve_kill_side_centering(with_side, result.selected_k, observations, kobs, trades)
    print(f"  c={centering.c:.4f}   |c-1|={abs(centering.c - 1):.4f}   "
          f"death-side residual={centering.death_side_residual:+.4%}  (tolerance 2%)")

    print("\n=== transcribe into app/scoring/preplant_scalar.py ===")
    print(f"INTERCEPT_ATK = {with_side.intercept_atk:.6f}")
    print(f"SLOPE_ATK = {with_side.slope_atk:.6f}")
    print(f"INTERCEPT_DEF = {with_side.intercept_def:.6f}")
    print(f"SLOPE_DEF = {with_side.slope_def:.6f}")
    print(f"K = {result.selected_k}")
    print(f"CENTERING_C = {centering.c:.6f}")


if __name__ == "__main__":
    main()
```

**Note for the implementer running this task:** the centring section is
left as a TODO-with-explanation above rather than fabricated code, because
`kill_order_bonus`/`_traded_factor` need the full per-round kill list (in
order) to compute correctly, which `PreplantKillObservation` deliberately
does not carry (Task 1 keeps it a flat, DB-shaped dataclass). Resolve this
by adding a second extraction function in `preplant_time_model.py` —
`extract_preplant_kill_order_and_trade(db) -> tuple[list[float],
list[float]]`, aligned index-for-index with `extract_preplant_observations`'s
output by iterating the exact same round/kill loop — rather than changing
`PreplantKillObservation`'s shape, which Task 4's tests already depend on.
Add its own unit test (in-memory sqlite, same pattern as Task 1) before
using it here. This is real, spec-required work, not optional polish — do
not skip straight to running the script without it, since the printed
centring numbers would otherwise be wrong.

- [ ] **Step 1: Write the kill-order/trade extraction helper and its test**
  (in `preplant_time_model.py` / `test_preplant_time_model.py`, following
  Task 1's exact DB-fixture pattern, asserting the returned lists align with
  a hand-computed `kill_order_bonus`/`traded_factor` on a 2-kill fixture
  with one trade).

- [ ] **Step 2: Wire it into the script above** (replace the `kobs, trades`
  TODO block with a call to the new function).

- [ ] **Step 3: Run the script against the local DB**

Run: `cd webapp && .venv\Scripts\python.exe scripts\fit_preplant_time_factor.py`

Read the full report before proceeding. Sanity-check against the exploratory
proxy already on record (declaration draft §0/§4): the real fit's
`intercept_atk`/`intercept_def` need not match the proxy's `0.302`/`0.555`,
but a wildly different sign or an order-of-magnitude difference is a
signal to stop and check the design matrix (Task 3) before trusting the
rest of the pipeline — per this plan's Global Constraints, nothing here is
tuned to hit an expectation, but a design-matrix bug masquerading as a
result is a different failure mode than the one the predeclaration
discipline protects against.

- [ ] **Step 4: Commit**

```bash
git add webapp/app/scoring/preplant_time_model.py webapp/tests/test_preplant_time_model.py webapp/scripts/fit_preplant_time_factor.py
git commit -m "Add the Part 3 fit-and-report script, run against the live DB"
```

---

### Task 8: Transcribe the fitted constants and close out the dormancy tests

**SUPERSEDED 2026-09-08.** Tasks 8 and 9 as written below were not executed
against this model. Fixing Bug B (the shape*lift reconstruction mismatch;
see `2026-09-08-preplant-dip-independent-verification.md` section 7) surfaced
that the model, correctly fit, is non-monotonic, contradicting this Part's
Testing assertion. Rather than resolve that within this parameterisation,
Part 3 shipped via a different mechanism: `app/scoring/preplant_empirical_
factor.py`, wired behind `enable_preplant_empirical` in `impact.py` (the
Task-9-shaped wiring step was done for THAT module, not this one -- see the
spec's "DECIDED 2026-09-08" section under Part 3). `preplant_scalar.py`,
`preplant_k_selection.py` and `preplant_centering.py` remain unused,
placeholder-valued, and are not on a path to being wired in without
reopening that decision. Left below as the historical record of the plan
as originally scoped.

**Files:**
- Modify: `webapp/app/scoring/preplant_scalar.py`
- Modify: `webapp/tests/test_preplant_scalar.py`

**Interfaces:** none new — this task replaces Task 6's placeholder
constants with Task 7's script output and adds tests that pin the real
values' behaviour (not their exact numbers, which would make the test
brittle against a future refit).

- [ ] **Step 1: Replace the placeholder constants**

Copy the six printed values from Task 7's script run verbatim into
`INTERCEPT_ATK`, `SLOPE_ATK`, `INTERCEPT_DEF`, `SLOPE_DEF`, `K`,
`CENTERING_C` in `preplant_scalar.py`. Update the module docstring: remove
"DORMANT: every constant below is a placeholder" and replace with a note
recording the date the fit ran and pointing at Task 7's script as how to
reproduce it.

- [ ] **Step 2: Write tests pinning real-data behaviour**

```python
# tests/test_preplant_scalar.py (append)
def test_realized_mode_now_differs_from_one_with_real_constants():
    # This will fail if Step 1 above was skipped (placeholders left in).
    from app.scoring.preplant_scalar import CENTERING_C, INTERCEPT_ATK, K, SLOPE_ATK
    assert not (INTERCEPT_ATK == 0.0 and SLOPE_ATK == 0.0), (
        "placeholder constants still in place -- run scripts/fit_preplant_time_factor.py "
        "and transcribe its output first"
    )
    far = preplant_proximity_scalar(-30.0, 0, True, use_realized=True)
    near = preplant_proximity_scalar(-2.0, 0, True, use_realized=True)
    assert far != pytest.approx(near)


def test_never_planted_and_use_realized_false_are_unaffected_by_real_constants():
    # The leakage gate must hold regardless of what the fit produced.
    for adv in (-3, 0, 2):
        assert preplant_proximity_scalar(-15.0, adv, True, use_realized=False) == 1.0
```

- [ ] **Step 3: Run the full test file**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_preplant_scalar.py -v`
Expected: PASS, all tests, including the two new ones.

- [ ] **Step 4: Run the FULL suite to confirm nothing else moved**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest -q`
Expected: same 554 passed / 1 pre-existing unrelated failure as before this
plan started (`test_site_stats_cache.py::test_happy_path_blob_validates`) —
`preplant_scalar.py` is not imported from `impact.py` yet (Task 9 is the
first and only place that happens, still behind a flag defaulting off), so
nothing here should be able to move an existing test.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/scoring/preplant_scalar.py webapp/tests/test_preplant_scalar.py
git commit -m "Transcribe the fitted Part 3 constants; dormancy remains intact"
```

---

### Task 9: Wire the dormant scalar behind an explicit off-by-default flag

**Files:**
- Modify: `webapp/app/scoring/impact.py`
- Test: `webapp/tests/test_impact_preplant_hook.py`

**Interfaces:**
- Consumes: `preplant_proximity_scalar` (Task 8).
- Modifies: `_time_factor`'s signature and every one of its two call sites
  in `build_impact_rows_for_match`'s kill loop (`impact.py:549` and `:569`
  per the current line numbers) to pass through `adv`, `is_attacker` and a
  new `enable_preplant_proximity: bool = False` parameter threaded from
  `build_impact_rows_for_match`/`compute_impact_for_match`'s own signatures,
  matching exactly how `use_realized_swing` is already threaded.

**Do not rename `use_realized_swing` to `use_realized` in this task.** The
spec calls for that generalisation (so one flag gates both the realized
swing term and this new term), but doing it now would touch every existing
call site (`impact_eval.py`, `impact_eval_cache.py`, scripts) for a rename
whose payoff only lands once Part 4 exists too. Keep `enable_preplant_
proximity` as its own, separate, default-`False` parameter here; the rename
and consolidation is explicitly future work for the plan that ships Part 3
+ Part 4 + econ together (Rollout section) — note this in a comment at the
new parameter's definition so it isn't lost.

```python
# app/scoring/impact.py -- _time_factor's new signature
from app.scoring.preplant_scalar import preplant_proximity_scalar


def _time_factor(
    round_row: Round, kill_time: float, for_death: bool = False,
    adv: int | None = None, is_attacker: bool | None = None,
    enable_preplant_proximity: bool = False, use_realized: bool = True,
) -> float:
    plant_time = round_row.plant_time if round_row.planted else None

    exploded_effective = round_row.exploded and plant_time is not None and kill_time >= plant_time + 45
    defused_effective = (
        round_row.defused and round_row.defuse_time is not None and kill_time >= round_row.defuse_time
    )
    if exploded_effective or defused_effective:
        return 0.5

    if plant_time is not None and kill_time >= plant_time:
        if plant_time + 38 <= kill_time <= plant_time + 45:
            return 0.5 if for_death else 1.75
        return 1 + (kill_time - plant_time) / 53

    # Pre-plant. Legacy flat 1.0 unless the dormant Part 3 model is
    # explicitly enabled -- see docs/superpowers/plans/2026-09-07-preplant-
    # time-factor-part3.md. NEVER flip this default; that is a separate,
    # deliberate rollout decision (version bump + rescore) covering Part 3,
    # Part 4 and the econ swap together.
    if enable_preplant_proximity and plant_time is not None and adv is not None and is_attacker is not None:
        return preplant_proximity_scalar(kill_time - plant_time, adv, is_attacker, use_realized)
    return 1
```

Both call sites become:

```python
kill["kill_order_bonus_x_time"] = (
    kill_order_bonus * _time_factor(
        round_row, kill["event_time_seconds"],
        adv=team1_kill_index - team2_kill_index if killer_team == Team.TEAM_1
            else team2_kill_index - team1_kill_index,
        is_attacker=(_attacking_team(round_number) == killer_team),
        enable_preplant_proximity=enable_preplant_proximity,
        use_realized=use_realized_swing,
    ) if not self_kill else 0
)
```

(and the mirrored death-side call two lines down with `for_death=True`,
same new arguments). `enable_preplant_proximity` is threaded as a new
parameter on `build_impact_rows_for_match` and `compute_impact_for_match`,
defaulting to `False`, exactly mirroring how `use_realized_swing` already
flows through both functions' signatures.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_impact_preplant_hook.py
"""The new flag defaults off everywhere; turning it on for a planted round
changes pre-plant kill_order_bonus_x_time without touching anything else.
Same in-memory sqlite pattern as test_impact_kill_order_bonus_net.py."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring import preplant_scalar
from app.scoring.impact import build_impact_rows_for_match


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    p = Player(riot_id=name)
    db.add(p)
    db.flush()
    return p.id


def _match_with_preplant_kill(db):
    match = Match(external_id="hook1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=40.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, event_time_seconds=20.0))
    db.commit()
    return match


def test_flag_defaults_off_and_matches_todays_flat_behaviour():
    db = _session()
    match = _match_with_preplant_kill(db)
    rows = build_impact_rows_for_match(db, match.id)
    default_time_x = next(r["kill_order_bonus_x_time"] for r in rows if r.get("kill_order_bonus_x_time"))

    rows_explicit_off = build_impact_rows_for_match(db, match.id, enable_preplant_proximity=False)
    explicit_off_time_x = next(
        r["kill_order_bonus_x_time"] for r in rows_explicit_off if r.get("kill_order_bonus_x_time")
    )
    assert default_time_x == explicit_off_time_x


def test_flag_on_with_nonzero_constants_changes_the_preplant_kill(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.6)
    monkeypatch.setattr(preplant_scalar, "SLOPE_ATK", 0.0)
    monkeypatch.setattr(preplant_scalar, "K", 1.0)
    monkeypatch.setattr(preplant_scalar, "CENTERING_C", 1.0)

    db = _session()
    match = _match_with_preplant_kill(db)
    off_rows = build_impact_rows_for_match(db, match.id, enable_preplant_proximity=False)
    on_rows = build_impact_rows_for_match(db, match.id, enable_preplant_proximity=True)

    off_time_x = next(r["kill_order_bonus_x_time"] for r in off_rows if r.get("kill_order_bonus_x_time"))
    on_time_x = next(r["kill_order_bonus_x_time"] for r in on_rows if r.get("kill_order_bonus_x_time"))
    assert off_time_x != on_time_x


def test_flag_on_with_use_realized_false_still_returns_legacy_flat_value(monkeypatch):
    monkeypatch.setattr(preplant_scalar, "INTERCEPT_ATK", 0.6)
    db = _session()
    match = _match_with_preplant_kill(db)
    rows = build_impact_rows_for_match(
        db, match.id, enable_preplant_proximity=True, use_realized_swing=False,
    )
    time_x = next(r["kill_order_bonus_x_time"] for r in rows if r.get("kill_order_bonus_x_time"))
    kob = next(r["kill_order_bonus"] for r in rows if r.get("kill_order_bonus"))
    assert time_x == kob  # scalar was exactly 1.0 -- the leakage gate, end to end
```

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_impact_preplant_hook.py -v`
Expected: FAIL — `build_impact_rows_for_match` does not yet accept
`enable_preplant_proximity`.

- [ ] **Step 3: Implement** the signature/call-site changes shown above in
`impact.py`, threading `enable_preplant_proximity: bool = False` through
`build_impact_rows_for_match` and `compute_impact_for_match` alongside the
existing `use_realized_swing` parameter.

- [ ] **Step 4: Run to verify pass**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest tests/test_impact_preplant_hook.py -v`
Expected: PASS, all three tests.

- [ ] **Step 5: Run the FULL suite**

Run: `cd webapp && .venv\Scripts\python.exe -m pytest -q`
Expected: same 554 passed / 1 pre-existing unrelated failure as the plan
started with — every existing caller of `build_impact_rows_for_match`/
`compute_impact_for_match` omits the new parameter, so it defaults to
`False` and every existing score is bit-for-bit unchanged.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/scoring/impact.py webapp/tests/test_impact_preplant_hook.py
git commit -m "Wire the Part 3 pre-plant scalar behind an off-by-default flag"
```

---

## Self-Review

**Spec coverage:**
- Proximity curve, amplitude linear in advantage, side-specific: Task 3.
- Shape knots fixed, plateau imposed: Task 2.
- `k` as a policy multiplier selected by the predeclared rule: Task 4.
- Fit → clamp → centre order, exact kill-side gate, reported death-side
  residual: Task 5 (order enforced inside `_raw_scalar`/`_clamped_scalar`).
- Leakage gate (`use_realized=False` → exactly 1.0): Task 6, re-verified
  end-to-end in Task 9.
- No kill ever worth negative Impact: Task 6's clamp floor at 0.2 pre-centring, `CENTERING_C` applied after — flag if a future real `CENTERING_C` could push a product at/below 0 (it structurally can't while `CENTERING_C > 0` and the clamp floor is `0.2`, but note this explicitly if `CENTERING_C` ever comes out negative in Task 7's report, which itself would be a finding).
- Nested comparison (side interaction) and temporal-split stability check:
  Task 7.
- Never-planted rounds stay flat 1.0: unchanged in `_time_factor` (the new
  branch only fires when `plant_time is not None`).
- No rescore / no version bump: enforced throughout by the off-by-default
  flag (Global Constraints, Task 9).
- Deaths reuse the same fitted scalar (spec, "Deaths: ship symmetric"): Task
  9's mirrored death-side call site.

**Not covered by this plan, deliberately:** Part 4 (post-plant), the econ
component, the five-arm reporting, and the eventual `use_realized_swing` →
`use_realized` rename/consolidation — all future plans per the Rollout
section's single combined version bump.

**Placeholder scan:** Task 7's centring-inputs block is the one spot flagged
explicitly as needing a small follow-up extraction function rather than
fabricated code — called out with a concrete function name, signature, and
test instruction rather than left as a bare TODO.

---

Plan complete and saved to `docs/superpowers/plans/2026-09-07-preplant-time-factor-part3.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
