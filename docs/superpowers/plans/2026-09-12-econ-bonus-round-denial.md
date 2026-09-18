# Round 2/14 Bonus-Round Denial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `buy_disruption_v2_30_80_bonus_denial`, a separate econ model that values a pistol winner's lost round-2/14 kit per the spec, freeze it as a new candidate, and report how it differs from the current 30/80 candidate.

**Architecture:** Two new pure modules (`round_rewards.py`, `weapon_prices.py`) feed a new branch in the pure calculator `econ_buy_disruption.score_round`, active only for the new model, only in half-round 2, only for victims on the pistol-winning team. `impact.py` supplies the new optional inputs. The manifest gains the comparator, the constants, the new hashed sources and the kill weapon in the source fingerprint. A separate additive table stores tracker's `spentCredits`, which the scorer does not read. A new comparison script scores the corpus under both models with a victim-side parity reconciler.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, Postgres 18 (local, port 5433), pytest with sqlite fixtures.

**Spec:** `docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md` (commit 81916c1). Executors read both.

## Global Constraints

- The frozen `MODEL_V2_30_80` and `MODEL_V2_WEALTH` behaviour must not change. The Abyss parity tests (`tests/test_econ_buy_disruption_abyss_parity.py`) and all existing calculator tests stay green.
- `ACTIVE_MANIFEST` stays `None`; `IMPACT_CALCULATION_VERSION` stays 2. Nothing is rescored, backfilled or deployed.
- The rc2 folder `docs/superpowers/econ-buy-disruption-candidate/` is never modified.
- Values: threshold `> 1500` on `max(0, paid - utility_cost)`; `SWING_VALUE_PER_CREDIT = 1.10`; factor 0.8 (pistol winner won round N) / 1.0 (lost); victim debit 0.80 of the killer credit; non-qualifying first deaths use background 0.10 with a 0.30 debit; `R = 19500`.
- Recovery: per survivor `max(credit_recovery, feed_recovery)`, never summed; `net_denied_i = denied_i * max(0, 1 - team_recovered / sum(denied))`.
- New-model results, including abstentions, report `audit_version = 2`. Existing models keep 1.
- Missing new inputs never become zero: they abstain `missing_bonus_inputs`.
- Read-only on the main DB: every DB script runs `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` and rolls back. Migrations are exercised only on a throwaway database.
- TDD: each test must fail on a wrong VALUE before the code exists or is corrected. An ImportError alone proves nothing: stub the name first if needed.
- Run everything from `webapp/` with `.\.venv\Scripts\python.exe`; scripts that print names use `-X utf8`.
- Repo is public: no credentials; committed reports identify players by match_player id, not handle.
- Known red on clean checkout, not to be "fixed": `test_impact_exante_swing::test_builder_matches_stored_values`, `test_site_stats_cache::test_happy_path_blob_validates`.
- Commit messages end with the session attribution lines.

---

## File map

| file | action | responsibility |
|---|---|---|
| `webapp/app/scoring/round_rewards.py` | create | actual Valorant round rewards (win / loss streak within the half), kill reward, plant bonus, credit cap |
| `webapp/app/scoring/weapon_prices.py` | create | declared price table, non-purchasable and unidentified name sets, `classify` |
| `webapp/app/scoring/agent_economy.py` | modify | add `known_utility_cost` (no fallback) |
| `webapp/app/scoring/econ_buy_disruption.py` | modify | new model id, constants, optional inputs, bonus guards, bonus ledger, audit records |
| `webapp/app/scoring/impact.py` | modify | keep kill weapon; supply new inputs |
| `webapp/app/scoring/impact_manifest.py` | modify | comparator, constants, hashed sources, weapon in fingerprint |
| `webapp/app/models/round_player_spend.py` | create | `RoundPlayerSpend` model |
| `webapp/app/models/__init__.py` | modify | register the model |
| `webapp/alembic/versions/0009_round_player_spend.py` | create | table migration |
| `webapp/app/adapters/trackergg_browserstate_source.py` | modify | write `spentCredits` rows |
| `webapp/scripts/compare_econ_models.py` | create | corpus comparison 30/80 vs bonus, with parity reconciler |
| `webapp/tests/test_round_rewards.py`, `test_weapon_prices.py`, `test_econ_bonus_denial.py`, `test_impact_bonus_denial_integration.py`, `test_round_player_spend_ingest.py`, `test_compare_econ_models.py` | create | tests |
| `webapp/tests/buy_disruption_fixtures.py` | modify | opt-in weapons / planted / counted stats |
| `webapp/tests/test_impact_manifest.py` | modify | new manifest tests |
| `docs/superpowers/econ-bonus-denial-candidate/` | create | frozen manifest, reports, SUMMARY |
| `docs/superpowers/2026-09-07-predeclared-values.md` | append | declaration, then RESULT |

---

### Task 1: Pure reward and weapon-price modules

**Files:**
- Create: `webapp/app/scoring/round_rewards.py`, `webapp/app/scoring/weapon_prices.py`
- Modify: `webapp/app/scoring/agent_economy.py` (append `known_utility_cost`)
- Test: `webapp/tests/test_round_rewards.py`, `webapp/tests/test_weapon_prices.py`

**Interfaces:**
- Produces: `round_rewards.WIN_REWARD=3000`, `LOSS_REWARDS=(1900, 2400, 2900)`, `KILL_REWARD=200`, `PLANT_BONUS=300`, `CREDIT_CAP=9000`, `round_reward(winners: Mapping[int, Hashable | None], round_number: int, team: Hashable) -> int | None`.
- Produces: `weapon_prices.WEAPON_PRICES: dict[str, int]`, `NON_PURCHASABLE: frozenset[str]`, `UNIDENTIFIED: frozenset[str]`, `classify(name: str) -> str` returning `"priced" | "non_purchasable" | "unidentified" | "unrecognised"`.
- Produces: `agent_economy.known_utility_cost(agent: str | None) -> int | None`.

- [ ] **Step 1: Write the failing tests**

`webapp/tests/test_round_rewards.py`:
```python
"""Actual round rewards (spec section 4.1): 3000 after a win, 1900/2400/2900
by the consecutive-loss streak WITHIN the regulation half."""
from app.scoring import round_rewards as rr

A, B = "A", "B"


def winners(*seq, start=1):
    return {start + i: w for i, w in enumerate(seq)}


def test_win_pays_3000():
    assert rr.round_reward(winners(A, A), 3, A) == 3000


def test_loss_streak_tiers():
    w = winners(A, B, B, B, B)
    assert rr.round_reward(w, 3, A) == 1900
    assert rr.round_reward(w, 4, A) == 2400
    assert rr.round_reward(w, 5, A) == 2900
    assert rr.round_reward(w, 6, A) == 2900


def test_streak_does_not_cross_halftime():
    w = {12: B, 13: B, 14: B}
    assert rr.round_reward(w, 14, A) == 1900
    assert rr.round_reward(w, 15, A) == 2400


def test_unknown_outcome_or_pistol_reset_is_none():
    assert rr.round_reward(winners(A, None), 3, A) is None
    assert rr.round_reward(winners(A, B, None), 4, A) is None
    assert rr.round_reward({12: A}, 13, A) is None
    assert rr.round_reward({24: A}, 25, A) is None


def test_constants():
    assert (rr.WIN_REWARD, rr.LOSS_REWARDS, rr.KILL_REWARD, rr.PLANT_BONUS, rr.CREDIT_CAP) == (
        3000, (1900, 2400, 2900), 200, 300, 9000)
```

`webapp/tests/test_weapon_prices.py`:
```python
"""The declared price table and name lists (spec section 6)."""
from app.scoring import agent_economy
from app.scoring import weapon_prices as wp

SPEC_PRICES = {
    "Classic": 0, "Shorty": 300, "Frenzy": 450, "Ghost": 500, "Bandit": 600, "Sheriff": 800,
    "Stinger": 1100, "Spectre": 1600, "Bucky": 850, "Judge": 1850, "Bulldog": 2050, "Ares": 1600,
    "Odin": 3200, "Guardian": 2250, "Phantom": 2900, "Vandal": 2900, "Marshal": 950,
    "Outlaw": 2400, "Operator": 4700,
}
CORPUS_NON_WEAPONS = {
    "Headhunter", "Tour De Force", "Blade Storm", "Showstopper", "Overdrive", "Not Dead Yet",
    "Hunter's Fury", "Paint Shells", "Shock Bolt", "Hot Hands", "Annihilation", "Orbital Strike",
    "Aftershock", "Guided Salvo", "Nanoswarm", "Incendiary", "Boom Bot", "Mosh Pit", "Armageddon",
    "FRAG/ment", "TURRET", "Snake Bite", "Blaze", "Razorvine", "Trailblazer", "Trapwire",
    "Special Delivery", "Curveball", "Crush", "Blast Pack", "Dizzy", "Bomb", "Fall", "Melee",
}


def test_prices_are_the_declared_table():
    assert wp.WEAPON_PRICES == SPEC_PRICES


def test_name_sets_are_the_declared_lists_and_disjoint():
    assert wp.NON_PURCHASABLE == CORPUS_NON_WEAPONS
    assert wp.UNIDENTIFIED == {"Weapon", "Unknown", "Primary"}
    assert not (set(wp.WEAPON_PRICES) & wp.NON_PURCHASABLE)
    assert not (set(wp.WEAPON_PRICES) & wp.UNIDENTIFIED)
    assert not (wp.NON_PURCHASABLE & wp.UNIDENTIFIED)


def test_classify():
    assert wp.classify("Spectre") == "priced"
    assert wp.classify("Classic") == "priced"
    assert wp.classify("Headhunter") == "non_purchasable"
    assert wp.classify("Weapon") == "unidentified"
    assert wp.classify("Vandal2") == "unrecognised"
    assert wp.classify("") == "unrecognised"


def test_known_utility_cost_has_no_fallback():
    assert agent_economy.known_utility_cost("Jett") == 550
    assert agent_economy.known_utility_cost("KAY/O") == 700
    assert agent_economy.known_utility_cost("NotAnAgent") is None
    assert agent_economy.known_utility_cost(None) is None
```

- [ ] **Step 2: Stub the names so failures are value failures**

Create `round_rewards.py` with the constants set to `0` / `()` and `def round_reward(winners, round_number, team): return -1`; `weapon_prices.py` with `WEAPON_PRICES = {}`, `NON_PURCHASABLE = frozenset()`, `UNIDENTIFIED = frozenset()`, `def classify(name): return ""`; append `def known_utility_cost(agent): return -1` to `agent_economy.py`.

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_round_rewards.py tests/test_weapon_prices.py -q`
Expected: every test FAILS on an assertion (no ImportError).

- [ ] **Step 3: Implement**

`webapp/app/scoring/round_rewards.py`:
```python
"""Actual Valorant round rewards, read retrospectively from real outcomes.

Spec: docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 4.1. PURE and import-free with respect to the scorer:
app.scoring.credit_events.round_bonus imports app.scoring.impact, which
imports the econ calculator, so the calculator cannot reuse that helper
without a cycle. Unlike round_bonus, the loss streak here stops at the
half's pistol round (credits and streaks reset at halftime).
"""
from typing import Hashable, Mapping

WIN_REWARD = 3000
LOSS_REWARDS = (1900, 2400, 2900)
KILL_REWARD = 200
PLANT_BONUS = 300
CREDIT_CAP = 9000


def half_start(round_number: int) -> int | None:
    """First round of the regulation half containing round_number; None in overtime."""
    if 1 <= round_number <= 12:
        return 1
    if 13 <= round_number <= 24:
        return 13
    return None


def round_reward(winners: Mapping[int, Hashable | None], round_number: int, team: Hashable) -> int | None:
    """The reward `team` receives going INTO `round_number`, from real outcomes.

    None when round_number starts a half (the pistol reset), is overtime, or
    any outcome the streak needs is unknown -- never an assumed value."""
    start = half_start(round_number)
    if start is None or round_number <= start:
        return None
    previous = round_number - 1
    if winners.get(previous) is None:
        return None
    if winners[previous] == team:
        return WIN_REWARD
    streak = 0
    r = previous
    while r >= start:
        winner = winners.get(r)
        if winner is None:
            return None
        if winner == team:
            break
        streak += 1
        r -= 1
    return LOSS_REWARDS[min(streak, len(LOSS_REWARDS)) - 1]
```

`webapp/app/scoring/weapon_prices.py`:
```python
"""Declared weapon prices and kill-feed name lists.

Spec: docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 6. Current prices checked 2026-09-12; one table for every date is an
accepted limitation. A name in none of the three sets is UNRECOGNISED and
makes the bonus-denial calculator abstain rather than guess.
"""

WEAPON_PRICES = {
    "Classic": 0, "Shorty": 300, "Frenzy": 450, "Ghost": 500, "Bandit": 600, "Sheriff": 800,
    "Stinger": 1100, "Spectre": 1600, "Bucky": 850, "Judge": 1850,
    "Bulldog": 2050, "Guardian": 2250, "Phantom": 2900, "Vandal": 2900,
    "Marshal": 950, "Outlaw": 2400, "Operator": 4700, "Ares": 1600, "Odin": 3200,
}

NON_PURCHASABLE = frozenset({
    "Headhunter", "Tour De Force", "Blade Storm", "Showstopper", "Overdrive", "Not Dead Yet",
    "Hunter's Fury", "Paint Shells", "Shock Bolt", "Hot Hands", "Annihilation", "Orbital Strike",
    "Aftershock", "Guided Salvo", "Nanoswarm", "Incendiary", "Boom Bot", "Mosh Pit", "Armageddon",
    "FRAG/ment", "TURRET", "Snake Bite", "Blaze", "Razorvine", "Trailblazer", "Trapwire",
    "Special Delivery", "Curveball", "Crush", "Blast Pack", "Dizzy", "Bomb", "Fall", "Melee",
})

UNIDENTIFIED = frozenset({"Weapon", "Unknown", "Primary"})


def classify(name: str) -> str:
    if name in WEAPON_PRICES:
        return "priced"
    if name in NON_PURCHASABLE:
        return "non_purchasable"
    if name in UNIDENTIFIED:
        return "unidentified"
    return "unrecognised"
```

Replace the stub in `agent_economy.py` with:
```python
def known_utility_cost(agent: str | None) -> int | None:
    """The table value, or None for an agent the table does not cover. Unlike
    max_utility_cost there is no fallback: the bonus-denial model abstains."""
    return AGENT_UTILITY_COST.get(_normalize_agent(agent))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_round_rewards.py tests/test_weapon_prices.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit** (`git add` the five files; message "Add round-reward and weapon-price modules for the bonus denial").

---

### Task 2: Calculator — new model, optional inputs, guards, parity outside the branch

**Files:**
- Modify: `webapp/app/scoring/econ_buy_disruption.py`
- Test: `webapp/tests/test_econ_bonus_denial.py`

**Interfaces:**
- Consumes: Task 1 modules; `agent_economy.known_utility_cost`.
- Produces: `MODEL_V2_30_80_BONUS_DENIAL = "buy_disruption_v2_30_80_bonus_denial"`; `THIRTY_EIGHTY_MODELS`; constants `BONUS_AUDIT_VERSION = 2`, `BONUS_DENIAL_THRESHOLD = 1500.0`, `SWING_VALUE_PER_CREDIT = 1.10`, `BONUS_WON_FACTOR = 0.8`, `BONUS_LOST_FACTOR = 1.0`; `audit_version_for(model) -> int`.
- Produces new optional fields: `PlayerEconomy.agent/remaining/kills/deaths/next_deaths`, `EconEvent.weapon`, `RoundEconInputs.next_events/planted/attacking_team/next_round_reward`.
- Produces outputs: `SurvivorRecovery`, `BonusDenialAudit`, `TeamAudit.bonus` (default None), `EventLedger.bonus_qualifying` (default False).

- [ ] **Step 1: Write the failing tests (guards + parity outside the branch)**

`webapp/tests/test_econ_bonus_denial.py` (Tasks 3-4 append to this file):
```python
"""The round 2/14 bonus-round denial model (spec 2026-09-12).

Hand-derived values (R = 19500):
    qualifying credit = V * 1.10 * net_denied / R, victim debit = 0.8 * that
    non-qualifying: credit 0.10 * paid / R, debit 0.30 * 0.10 * paid / R
Team A (ids 1-5, Jett, utility 550) wins the pistol round in every fixture.
"""
import pytest

from app.scoring import econ_buy_disruption as bd

A, B = "TEAM_1", "TEAM_2"
A_IDS, B_IDS = (1, 2, 3, 4, 5), (6, 7, 8, 9, 10)
ALL = A_IDS + B_IDS
R = 19500.0
NEW = bd.MODEL_V2_30_80_BONUS_DENIAL
WIN_A, WIN_B = "Team A Elimination Win", "Team B Elimination Win"


def players(*, loadout=None, next_loadout=None, next_bank=None, remaining=None, kills=None,
            deaths=None, next_deaths=None, agent=None, omit=()):
    loadout = {**{i: 3900 for i in ALL}, **(loadout or {})}
    next_loadout = {**{i: 3900 for i in ALL}, **(next_loadout or {})}
    next_bank = {**{i: 1000 for i in ALL}, **(next_bank or {})}
    remaining = {**{i: 1000 for i in ALL}, **(remaining or {})}
    kills, deaths, next_deaths = kills or {}, deaths or {}, next_deaths or {}
    agent = {**{i: "Jett" if i in A_IDS else "Sova" for i in ALL}, **(agent or {})}
    out = []
    for i in ALL:
        fields = dict(agent=agent[i], remaining=remaining[i], kills=kills.get(i, 0),
                      deaths=deaths.get(i, 0), next_deaths=next_deaths.get(i, 0))
        for name in omit:
            if name[1] == i:
                fields[name[0]] = None
        out.append(bd.PlayerEconomy(
            match_player_id=i, team=A if i in A_IDS else B, free_ability_credits=0,
            loadout=loadout[i], next_loadout=next_loadout[i], next_remaining=next_bank[i], **fields))
    return tuple(out)


def ev(event_id, t, killer, victim, weapon="Vandal"):
    return bd.EconEvent(event_id=event_id, time_seconds=t, killer_id=killer, victim_id=victim, weapon=weapon)


def inputs(*, round_number=2, events=(), next_events=(), outcome=WIN_A, planted=False,
           attacking_team=A, reward=None, player_kw=None, pistol=WIN_A, **over):
    kw = dict(
        round_number=round_number, last_round_number=24, team_a=A, team_b=B,
        players=players(**(player_kw or {})), events=tuple(events), outcome=outcome,
        next_outcome=WIN_A, pistol_outcome=pistol, has_next_round=True,
        next_events=tuple(next_events) if next_events is not None else None,
        planted=planted, attacking_team=attacking_team,
        next_round_reward=reward if reward is not None else {A: 3000 if outcome == WIN_A else 1900, B: 1900},
    )
    kw.update(over)
    return bd.RoundEconInputs(**kw)


def score(model=NEW, **kw):
    return bd.score_round(inputs(**kw), model)


# ---- identity and parity outside the branch ------------------------------------------------

def test_new_model_is_a_buy_disruption_model_with_audit_version_2():
    assert NEW in bd.BUY_DISRUPTION_MODELS
    assert bd.audit_version_for(NEW) == 2
    assert bd.audit_version_for(bd.MODEL_V2_30_80) == 1
    assert (bd.BONUS_DENIAL_THRESHOLD, bd.SWING_VALUE_PER_CREDIT, bd.BONUS_WON_FACTOR,
            bd.BONUS_LOST_FACTOR) == (1500.0, 1.10, 0.8, 1.0)


@pytest.mark.parametrize("rn", [3, 7, 11, 15, 23])
def test_rounds_outside_2_and_14_score_exactly_as_30_80(rn):
    events = [ev(1, 10.0, 6, 1), ev(2, 20.0, 1, 7), ev(3, 30.0, None, 2)]
    kw = dict(round_number=rn, events=events, player_kw=dict(next_loadout={1: 0, 2: 0}, next_bank={1: 0, 2: 0}))
    old, new = score(bd.MODEL_V2_30_80, **kw), score(NEW, **kw)
    assert new.audit_version == 2 and old.audit_version == 1
    assert new.raw_net_by_player() == old.raw_net_by_player()
    assert [(e.credit, e.victim_debit) for e in new.events] == [(e.credit, e.victim_debit) for e in old.events]


def test_rounds_outside_the_branch_do_not_require_the_new_inputs():
    result = bd.score_round(inputs(round_number=7, events=[ev(1, 10.0, 6, 1)], next_events=None,
                                   planted=None, attacking_team=None, reward={}), NEW)
    assert result.abstention is None


# ---- abstentions (spec section 7) ----------------------------------------------------------

def test_unknown_agent_abstains_with_audit_version_2():
    result = score(player_kw=dict(agent={3: "NotAnAgent"}))
    assert (result.abstention, result.audit_version) == ("unknown_agent_utility", 2)


@pytest.mark.parametrize("over", [
    dict(next_events=None), dict(planted=None), dict(attacking_team=None), dict(reward={B: 1900}),
    dict(reward={A: None, B: 1900}), dict(player_kw=dict(omit=[("remaining", 2)])),
    dict(player_kw=dict(omit=[("kills", 2)])), dict(player_kw=dict(omit=[("deaths", 8)])),
    dict(player_kw=dict(omit=[("next_deaths", 8)])), dict(events=[ev(1, 10.0, 6, 1, weapon=None)]),
])
def test_missing_bonus_inputs_abstain(over):
    result = score(**over)
    assert (result.abstention, result.audit_version) == ("missing_bonus_inputs", 2)


def test_unrecognised_weapon_by_a_pistol_winner_abstains_but_unidentified_does_not():
    assert score(events=[ev(1, 10.0, 1, 6, weapon="Vandal2")],
                 player_kw=dict(deaths={6: 1})).abstention == "unrecognised_weapon"
    assert score(next_events=[ev(9, 5.0, 2, 7, weapon="Vandal2")],
                 player_kw=dict(next_deaths={7: 1})).abstention == "unrecognised_weapon"
    ok = score(events=[ev(1, 10.0, 1, 6, weapon="Weapon")], player_kw=dict(deaths={6: 1}))
    assert ok.abstention is None
    assert any("unidentified_weapon" in flag for flag in ok.data_quality)


def test_kill_feed_incomplete_is_distinguished_from_a_round_without_kills():
    assert score(events=[], player_kw=dict(deaths={6: 1})).abstention == "kill_feed_incomplete"
    assert score(next_events=[], player_kw=dict(next_deaths={6: 1})).abstention == "kill_feed_incomplete"
    assert score(events=[], next_events=[]).abstention is None


def test_existing_guards_still_come_first():
    assert score(pistol="Draw").abstention == "unknown_pistol_winner"
```

- [ ] **Step 2: Stub and run to see value failures**

Add to `econ_buy_disruption.py`: `MODEL_V2_30_80_BONUS_DENIAL = "buy_disruption_v2_30_80_bonus_denial"` (NOT yet in `BUY_DISRUPTION_MODELS`), the five constants set to 0, `def audit_version_for(model): return AUDIT_VERSION`, and the new optional dataclass fields defaulting to `None` (Step 3 lists them).
Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py -q`
Expected: assertion failures (e.g. `NEW in BUY_DISRUPTION_MODELS` False, `ValueError Unknown buy-disruption model`, abstention None != "missing_bonus_inputs").

- [ ] **Step 3: Implement**

In `econ_buy_disruption.py`:

1. Imports: `from app.scoring import agent_economy, round_rewards, weapon_prices`.
2. Model identities:
```python
MODEL_V2_30_80_BONUS_DENIAL = "buy_disruption_v2_30_80_bonus_denial"  # spec 2026-09-12
BUY_DISRUPTION_MODELS = frozenset({MODEL_V2_WEALTH, MODEL_V2_30_80, MODEL_V2_30_80_BONUS_DENIAL})
THIRTY_EIGHTY_MODELS = frozenset({MODEL_V2_30_80, MODEL_V2_30_80_BONUS_DENIAL})
```
3. Constants after `CONTEXT_FULL_BUY_RAW`:
```python
# ---- Round 2/14 bonus-round denial (spec 2026-09-12). Declared, not fitted. ----------
BONUS_AUDIT_VERSION = 2
BONUS_DENIAL_THRESHOLD = 1500.0   # kit net of agent utility, strictly greater
SWING_VALUE_PER_CREDIT = 1.10     # BACKGROUND + DISRUPTION: a disrupted swing-round loss
BONUS_WON_FACTOR = 0.8            # pistol winner still won round N
BONUS_LOST_FACTOR = 1.0           # pistol winner lost round N


def audit_version_for(model: str) -> int:
    return BONUS_AUDIT_VERSION if model == MODEL_V2_30_80_BONUS_DENIAL else AUDIT_VERSION
```
4. Optional input fields (appended after the existing defaults, so positional use is unchanged):
```python
# PlayerEconomy
    agent: str | None = None
    remaining: float | None = None
    kills: int | None = None
    deaths: int | None = None
    next_deaths: int | None = None
# EconEvent
    weapon: str | None = None
# RoundEconInputs (after use_realized)
    next_events: tuple | None = None
    planted: bool | None = None
    attacking_team: Hashable | None = None
    next_round_reward: Mapping | None = None
```
5. Output records (before `TeamAudit`):
```python
@dataclass(frozen=True)
class SurvivorRecovery:
    match_player_id: int
    utility_cost: float
    cash: float
    surplus: float
    credit_recovery: float
    feed_recovery: float
    feed_inference: str | None   # "in_round" | "carried" | None -- INFERRED, not observed acquisition
    feed_weapon: str | None
    own_weapon: str | None
    recovery: float


@dataclass(frozen=True)
class BonusDenialAudit:
    won: bool
    factor: float
    denied: dict             # qualifying victim match_player_id -> denied credits
    survivors: tuple         # SurvivorRecovery, one per surviving pistol winner
    team_recovered: float
    net_denied: dict         # qualifying victim match_player_id -> net denied credits
```
Append `bonus: "BonusDenialAudit | None" = None` as the last `TeamAudit` field and `bonus_qualifying: bool = False` as the last `EventLedger` field.
6. `_abstain`: `audit_version=audit_version_for(model)`; the final `RoundEconResult(...)` likewise.
7. Replace `model == MODEL_V2_30_80` with `model in THIRTY_EIGHTY_MODELS` in `death_debit` and in the `rates` comprehension.
8. Guards, called right after the existing `invalid_economy_data` guard:
```python
def _bonus_guard(inputs: RoundEconInputs, pistol_winner) -> tuple[str, str] | None:
    """Spec section 7, in its fixed order. Only for the new model in half-round 2."""
    team = [p for p in inputs.players if p.team == pistol_winner]
    # A NAMED agent the table lacks is unknown; an absent agent is a missing input.
    unknown = [p.match_player_id for p in team
               if p.agent is not None and agent_economy.known_utility_cost(p.agent) is None]
    if unknown:
        return "unknown_agent_utility", f"match_player_ids {unknown}"
    missing = [f"{p.match_player_id}.agent" for p in team if p.agent is None]
    missing += [f"{p.match_player_id}.{name}" for p in team for name in ("remaining", "kills")
                if not _valid_number(getattr(p, name))]
    missing += [f"{p.match_player_id}.{name}" for p in inputs.players for name in ("deaths", "next_deaths")
                if not _valid_number(getattr(p, name))]
    reward = (inputs.next_round_reward or {}).get(pistol_winner)
    if not isinstance(inputs.planted, bool):
        missing.append("planted")
    if inputs.attacking_team not in (inputs.team_a, inputs.team_b):
        missing.append("attacking_team")
    if not _valid_number(reward):
        missing.append("next_round_reward")
    if inputs.next_events is None:
        missing.append("next_events")
    else:
        missing += [f"event {e.event_id}.weapon" for e in (*inputs.events, *inputs.next_events)
                    if not isinstance(e.weapon, str)]
    if missing:
        return "missing_bonus_inputs", ", ".join(missing)
    team_ids = {p.match_player_id for p in team}
    bad = [e.event_id for e in (*inputs.events, *inputs.next_events)
           if e.killer_id in team_ids and weapon_prices.classify(e.weapon) == "unrecognised"]
    if bad:
        return "unrecognised_weapon", f"event_ids {bad}"
    if (sum(p.deaths for p in inputs.players) > len(inputs.events)
            or sum(p.next_deaths for p in inputs.players) > len(inputs.next_events)):
        return "kill_feed_incomplete", "the deaths stat exceeds the kill events"
    return None
```
In `score_round`, after the `invalid_economy_data` return:
```python
    half_round = half_round_index(rn)
    bonus_team = pistol_winner if (model == MODEL_V2_30_80_BONUS_DENIAL and half_round == 2) else None
    if bonus_team is not None:
        refused = _bonus_guard(inputs, bonus_team)
        if refused:
            return _abstain(inputs, model, *refused)
        team_ids = {p.match_player_id for p in inputs.players if p.team == bonus_team}
        flags_bonus = [f"unidentified_weapon: event {e.event_id} {e.weapon!r}"
                       for e in (*inputs.events, *inputs.next_events)
                       if e.killer_id in team_ids and weapon_prices.classify(e.weapon) == "unidentified"]
    else:
        flags_bonus = []
```
(delete the later duplicate `half_round = half_round_index(rn)` line; prepend `flags_bonus` into `flags` where `flags = []` is created: `flags = list(flags_bonus)`).
9. Register: the model is already in `BUY_DISRUPTION_MODELS` from item 2. With `bonus_team` computed but no ledger branch yet, the round scores as 30/80.

- [ ] **Step 4: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py tests/test_econ_buy_disruption.py tests/test_econ_buy_disruption_abyss_parity.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit** ("Add the bonus-denial model identity, inputs and abstentions").

---

### Task 3: Calculator — denial ledger for the pistol winner's deaths (no recovery yet)

**Files:**
- Modify: `webapp/app/scoring/econ_buy_disruption.py`
- Test: `webapp/tests/test_econ_bonus_denial.py` (append)

**Interfaces:**
- Consumes: Task 2 names.
- Produces: `_bonus_denial(inputs, by_id, paid, next_paid, exposures, team, round_winner) -> BonusDenialAudit` (recovery fields zero until Task 4), event/ledger overrides.

- [ ] **Step 1: Append failing tests**

```python
# ---- denial values (spec sections 3 and 5) --------------------------------------------------
# Victim 1: Jett, paid 3900 -> kit ex-utility 3350 > 1500. Survivors 2-5 have surplus
# (1000+3900) - (1000+reward+3900) = -reward < 550, so no credit recovery.

def _one_kill(outcome, **kw):
    return score(outcome=outcome, events=[ev(1, 10.0, 6, 1)], player_kw=dict(deaths={1: 1}, **kw))


@pytest.mark.parametrize("outcome,factor", [(WIN_A, 0.8), (WIN_B, 1.0)])
def test_qualifying_death_pays_factor_times_swing_value(outcome, factor):
    result = _one_kill(outcome)
    value = factor * 1.10 * 3350 / R
    assert result.players[6].credit == pytest.approx(value)
    assert result.players[6].background_credit == 0
    assert result.players[1].debit == pytest.approx(0.80 * value)
    assert result.players[1].raw_net == pytest.approx(-0.80 * value)
    assert result.events[0].bonus_qualifying is True
    assert result.events[0].penalty_rate == 0.80
    audit = result.teams[A].bonus
    assert (audit.won, audit.factor) == (outcome == WIN_A, factor)
    assert audit.denied == {1: 3350} and audit.net_denied == {1: pytest.approx(3350)}


def test_threshold_is_strictly_greater_than_1500_after_utility():
    at = _one_kill(WIN_A, loadout={1: 2050})       # 2050 - 550 = 1500 -> not qualifying
    above = _one_kill(WIN_A, loadout={1: 2051})    # 1501 -> qualifying
    assert at.teams[A].bonus.denied == {}
    assert at.players[6].credit == pytest.approx(0.10 * 2050 / R)
    assert at.players[1].debit == pytest.approx(0.30 * 0.10 * 2050 / R)
    assert at.events[0].penalty_rate == 0.30
    assert above.teams[A].bonus.denied == {1: 1501}
    assert above.players[6].credit == pytest.approx(0.8 * 1.10 * 1501 / R)


def test_utility_is_subtracted_by_agent():
    sova = _one_kill(WIN_A, agent={1: "Sova"}, loadout={1: 2150})   # 2150 - 700 = 1450
    jett = _one_kill(WIN_A, loadout={1: 2150})                      # 2150 - 550 = 1600
    assert sova.teams[A].bonus.denied == {}
    assert jett.teams[A].bonus.denied == {1: 1600}


def test_environmental_self_and_team_deaths_take_the_debit_without_credit():
    for killer in (None, 1, 2):
        result = score(events=[ev(1, 10.0, killer, 1)], player_kw=dict(deaths={1: 1}))
        assert result.players[1].debit == pytest.approx(0.80 * 0.8 * 1.10 * 3350 / R)
        assert sum(p.credit for p in result.players.values()) == 0


def test_repeated_death_exposes_nothing():
    result = score(events=[ev(1, 10.0, 6, 1), ev(2, 50.0, 7, 1)], player_kw=dict(deaths={1: 2}))
    assert result.players[7].credit == 0
    assert result.players[1].debit == pytest.approx(0.80 * 0.8 * 1.10 * 3350 / R)


def test_pistol_losers_equipment_loss_is_unchanged_but_their_killers_earn_the_denial():
    events = [ev(1, 10.0, 6, 1), ev(2, 20.0, 2, 7)]
    kw = dict(events=events, player_kw=dict(deaths={1: 1, 7: 1}))
    old, new = score(bd.MODEL_V2_30_80, **kw), score(NEW, **kw)
    assert new.events[1].credit == old.events[1].credit          # victim 7 on the pistol loser
    assert new.events[1].victim_debit == old.events[1].victim_debit
    assert new.players[6].credit != old.players[6].credit          # killer of a pistol winner


def test_round_14_uses_pistol_round_13():
    result = score(round_number=14, events=[ev(1, 10.0, 6, 1)], player_kw=dict(deaths={1: 1}))
    assert result.teams[A].bonus is not None
    assert result.teams[B].bonus is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py -q -k "qualifying or threshold or utility_is or environmental or repeated or pistol_losers or round_14"`
Expected: FAIL (`.bonus` is None / 30-80 values).

- [ ] **Step 3: Implement the ledger branch**

Add after `death_debit`:
```python
def _utility(player: PlayerEconomy) -> float:
    return float(agent_economy.known_utility_cost(player.agent))


def _bonus_denial(inputs, by_id, paid, next_paid, exposures, team, round_winner) -> BonusDenialAudit:
    """Spec sections 3-5 for the pistol-winning team in half-round 2."""
    won = round_winner == team
    denied: dict[int, float] = {}
    dead: set[int] = set()
    for event, exposure in exposures:
        victim = by_id[event.victim_id]
        if victim.team != team:
            continue
        dead.add(victim.match_player_id)
        if exposure == 0:
            continue
        kit = max(0.0, exposure - _utility(victim))
        if kit > BONUS_DENIAL_THRESHOLD:
            denied[victim.match_player_id] = kit
    survivors = tuple(_survivor_recovery(inputs, by_id, paid, next_paid, team, dead, p)
                      for p in inputs.players if p.team == team and p.match_player_id not in dead)
    team_recovered = sum(s.recovery for s in survivors)
    total = sum(denied.values())
    keep = max(0.0, 1.0 - team_recovered / total) if total > 0 else 0.0
    return BonusDenialAudit(
        won=won, factor=BONUS_WON_FACTOR if won else BONUS_LOST_FACTOR, denied=denied,
        survivors=survivors, team_recovered=team_recovered,
        net_denied={pid: d * keep for pid, d in denied.items()},
    )


def _survivor_recovery(inputs, by_id, paid, next_paid, team, dead, player) -> SurvivorRecovery:
    # Task 3: recovery is not computed yet.
    return SurvivorRecovery(player.match_player_id, _utility(player), 0.0, 0.0, 0.0, 0.0,
                            None, None, None, 0.0)
```
In `score_round`, after `rates = {...}`:
```python
    bonus = (_bonus_denial(inputs, by_id, paid, next_paid, exposures, bonus_team, round_winner)
             if bonus_team is not None else None)
    bonus_debits: dict[int, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
```
Inside the event loop, replace the credit/debit computation with:
```python
        budget = budgets[victim.team]
        qualifying = False
        event_rate = rates[victim.team]
        if bonus is not None and victim.team == bonus_team:
            vid = victim.match_player_id
            if exposure > 0 and vid in bonus.denied:
                qualifying = True
                value = bonus.factor * SWING_VALUE_PER_CREDIT * bonus.net_denied[vid] / TEAM_REFERENCE
                bg, dis = (0.0, value) if kind == "enemy" else (0.0, 0.0)
                debit_bg, debit_dis, debit_scarcity = 0.0, DISRUPTED_RATE * value, 0.0
                event_rate = DISRUPTED_RATE
            else:
                background = BACKGROUND * exposure / TEAM_REFERENCE
                bg, dis = (background, 0.0) if kind == "enemy" else (0.0, 0.0)
                debit_bg, debit_dis, debit_scarcity = ABSORBED_RATE * background, 0.0, 0.0
                event_rate = ABSORBED_RATE
            prev = bonus_debits[vid]
            bonus_debits[vid] = (prev[0] + debit_bg, prev[1] + debit_dis)
        else:
            bg, dis = event_credit(exposure, budget) if kind == "enemy" else (0.0, 0.0)
            debit_bg, debit_dis, debit_scarcity = death_debit(exposure, budget, model)
        if kind == "enemy":
            background_credit[killer.match_player_id] += bg
            disruption_credit[killer.match_player_id] += dis
```
and in the `EventLedger(...)` call use `penalty_rate=event_rate`, `absorbed=(not qualifying) if (bonus is not None and victim.team == bonus_team) else budget.severity_pool == 0`, `bonus_qualifying=qualifying`.

In the player-ledger loop:
```python
        if bonus is not None and player.team == bonus_team:
            debit_bg, debit_dis = bonus_debits[pid]
            debit_scarcity = 0.0
            player_rate = DISRUPTED_RATE if pid in bonus.denied else ABSORBED_RATE
        else:
            debit_bg, debit_dis, debit_scarcity = death_debit(lost[pid], budget, model)
            player_rate = rates[player.team]
```
using `penalty_rate=player_rate`. In the team loop: `penalty_rate=(DISRUPTED_RATE if bonus.denied else ABSORBED_RATE) if (bonus is not None and team == bonus_team) else rates[team]`, `bonus=bonus if team == bonus_team else None`.

- [ ] **Step 4: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py tests/test_econ_buy_disruption.py tests/test_econ_buy_disruption_abyss_parity.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit** ("Score the pistol winner's round 2/14 kit loss as a bonus-round denial").

---

### Task 4: Calculator — recovery from credits and the kill feed

**Files:**
- Modify: `webapp/app/scoring/econ_buy_disruption.py` (`_survivor_recovery`)
- Test: `webapp/tests/test_econ_bonus_denial.py` (append)

**Interfaces:**
- Consumes: `round_rewards.KILL_REWARD/PLANT_BONUS/CREDIT_CAP`, `weapon_prices.WEAPON_PRICES/classify`.
- Produces: the final `_survivor_recovery`, `_feed_recovery(pid, team, by_id, events, next_events, cash, next_remaining) -> tuple[float, str | None, str | None, str | None]`.

- [ ] **Step 1: Append failing tests**

```python
# ---- recovery (spec section 4) --------------------------------------------------------------
# Victim 1 (qualifying, denied 3350). Round won -> reward 3000.
# Survivor 2's cash = remaining 1000 + 200*kills + plant + 3000.

def _recovery(**kw):
    base = dict(events=[ev(1, 10.0, 6, 1)], player_kw=dict(deaths={1: 1}))
    for key, value in kw.items():
        if key == "player_kw":
            base["player_kw"] = {**base["player_kw"], **value}
        else:
            base[key] = value
    return score(**base)


def test_credit_recovery_nets_only_the_surplus_above_utility():
    # survivor 2: next wealth 1000 + 6000 = 7000; cash 4000 + paid 3900 = 7900 -> surplus -900: none
    none = _recovery(player_kw=dict(next_loadout={2: 6000}))
    assert none.teams[A].bonus.team_recovered == 0
    # next loadout 7550 -> surplus 650 > 550 -> recovery 100
    small = _recovery(player_kw=dict(next_loadout={2: 7550}))
    s2 = next(s for s in small.teams[A].bonus.survivors if s.match_player_id == 2)
    assert (s2.cash, s2.surplus, s2.credit_recovery, s2.recovery) == (4000, 650, 100, 100)
    assert small.teams[A].bonus.net_denied[1] == pytest.approx(3250)
    assert small.players[6].credit == pytest.approx(0.8 * 1.10 * 3250 / R)


def test_kills_plant_and_the_credit_cap_enter_cash():
    result = _recovery(planted=True, attacking_team=A, reward={A: 3000, B: 1900},
                       player_kw=dict(kills={2: 2}, remaining={2: 8000}))
    s2 = next(s for s in result.teams[A].bonus.survivors if s.match_player_id == 2)
    assert s2.cash == 9000   # min(9000, 8000 + 400 + 300 + 3000)


def test_in_round_feed_pickup_nets_the_upgrade():
    # 1 kills with Spectre, then dies; 2 killed with Stinger, then kills with Spectre after 1's death
    events = [ev(1, 5.0, 1, 7, "Spectre"), ev(2, 6.0, 2, 8, "Stinger"), ev(3, 10.0, 6, 1, "Vandal"),
              ev(4, 20.0, 2, 9, "Spectre")]
    result = score(events=events, player_kw=dict(deaths={1: 1, 7: 1, 8: 1, 9: 1}))
    s2 = next(s for s in result.teams[A].bonus.survivors if s.match_player_id == 2)
    assert (s2.feed_inference, s2.feed_weapon, s2.own_weapon, s2.feed_recovery) == (
        "in_round", "Spectre", "Stinger", 500)
    assert result.teams[A].bonus.net_denied[1] == pytest.approx(2850)


def test_feed_use_before_the_teammate_died_is_not_a_pickup():
    events = [ev(1, 5.0, 1, 7, "Spectre"), ev(2, 6.0, 2, 8, "Stinger"), ev(4, 8.0, 2, 9, "Spectre"),
              ev(3, 10.0, 6, 1, "Vandal")]
    result = score(events=events, player_kw=dict(deaths={1: 1, 7: 1, 8: 1, 9: 1}))
    assert result.teams[A].bonus.team_recovered == 0


def test_carried_feed_pickup_requires_spend_below_the_price():
    events = [ev(1, 5.0, 1, 7, "Vandal"), ev(2, 6.0, 2, 8, "Spectre"), ev(3, 10.0, 6, 1, "Vandal")]
    nxt = [ev(9, 4.0, 2, 6, "Vandal")]
    # survivor 2: cash 1000 + 200 + 3000 = 4200. next bank 3000 -> spend 1200 < 2900: carried
    carried = score(events=events, next_events=nxt,
                    player_kw=dict(deaths={1: 1, 7: 1, 8: 1}, next_deaths={6: 1}, kills={2: 1},
                                   next_bank={2: 3000}))
    s2 = next(s for s in carried.teams[A].bonus.survivors if s.match_player_id == 2)
    assert (s2.feed_inference, s2.feed_recovery) == ("carried", 1300)
    # next bank 1000 -> spend 3200 >= 2900: could have bought it, no inference
    bought = score(events=events, next_events=nxt,
                   player_kw=dict(deaths={1: 1, 7: 1, 8: 1}, next_deaths={6: 1}, kills={2: 1},
                                  next_bank={2: 1000}))
    s2b = next(s for s in bought.teams[A].bonus.survivors if s.match_player_id == 2)
    assert (s2b.feed_inference, s2b.feed_recovery) == (None, 0)


def test_duplicate_evidence_takes_the_larger_never_the_sum():
    events = [ev(1, 5.0, 1, 7, "Spectre"), ev(2, 6.0, 2, 8, "Stinger"), ev(3, 10.0, 6, 1, "Vandal"),
              ev(4, 20.0, 2, 9, "Spectre")]
    # credit evidence 100 (as above, with kills counted in cash), feed evidence 500
    result = score(events=events, player_kw=dict(
        deaths={1: 1, 7: 1, 8: 1, 9: 1}, kills={2: 2}, next_loadout={2: 7950}))
    s2 = next(s for s in result.teams[A].bonus.survivors if s.match_player_id == 2)
    assert s2.credit_recovery == 100 and s2.feed_recovery == 500
    assert s2.recovery == 500
    assert result.teams[A].bonus.team_recovered == 500


def test_full_recovery_floors_net_denied_at_zero_and_keeps_totals_signed():
    result = _recovery(player_kw=dict(next_loadout={2: 20000}))
    assert result.teams[A].bonus.net_denied == {1: 0.0}
    assert result.players[6].credit == 0
    assert result.players[1].raw_net == 0


def test_recovery_is_pro_rata_across_qualifying_deaths():
    events = [ev(1, 10.0, 6, 1), ev(2, 11.0, 7, 3)]
    result = score(events=events, player_kw=dict(deaths={1: 1, 3: 1}, loadout={3: 2050 + 1000},
                                                  next_loadout={2: 7550 + 1000}))
    audit = result.teams[A].bonus
    assert audit.denied == {1: 3350, 3: 2500}
    assert audit.team_recovered == 1100
    keep = 1 - 1100 / 5850
    assert audit.net_denied[1] == pytest.approx(3350 * keep)
    assert audit.net_denied[3] == pytest.approx(2500 * keep)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py -q -k "recovery or feed or carried or duplicate or cash"`
Expected: FAIL (recovery 0 / cash 0).

- [ ] **Step 3: Implement**

Replace `_survivor_recovery` with:
```python
def _event_key(event: EconEvent) -> tuple:
    return (event.time_seconds, event.event_id)


def _survivor_recovery(inputs, by_id, paid, next_paid, team, dead, player) -> SurvivorRecovery:
    """Spec section 4 for one surviving pistol winner."""
    pid = player.match_player_id
    utility = _utility(player)
    plant = round_rewards.PLANT_BONUS if (inputs.planted and inputs.attacking_team == team) else 0.0
    cash = min(float(round_rewards.CREDIT_CAP),
               player.remaining + round_rewards.KILL_REWARD * player.kills + plant
               + inputs.next_round_reward[team])
    surplus = (player.next_remaining + next_paid[pid]) - (cash + paid[pid])
    credit_recovery = surplus - utility if surplus > utility else 0.0
    feed_value, inference, weapon, own = _feed_recovery(
        pid, team, by_id, inputs.events, inputs.next_events, cash, player.next_remaining)
    return SurvivorRecovery(
        match_player_id=pid, utility_cost=utility, cash=cash, surplus=surplus,
        credit_recovery=credit_recovery, feed_recovery=feed_value, feed_inference=inference,
        feed_weapon=weapon, own_weapon=own, recovery=max(credit_recovery, feed_value),
    )


def _feed_recovery(pid, team, by_id, events, next_events, cash, next_remaining):
    """Spec section 4.2. Returns (value, inference, weapon, own_weapon); value 0
    and inference None when nothing is inferred. Unidentified and
    non-purchasable names are never evidence and never change the own weapon."""
    prices = weapon_prices.WEAPON_PRICES
    ordered = ordered_events(events)
    death_key: dict[int, tuple] = {}
    for e in ordered:
        if by_id[e.victim_id].team == team and e.victim_id not in death_key:
            death_key[e.victim_id] = _event_key(e)
    # weapon -> earliest death of a teammate who killed with it before dying
    teammate_gun_death: dict[str, tuple] = {}
    for e in ordered:
        k = e.killer_id
        if (k is None or k == pid or k not in death_key
                or weapon_prices.classify(e.weapon) != "priced" or _event_key(e) >= death_key[k]):
            continue
        teammate_gun_death[e.weapon] = min(teammate_gun_death.get(e.weapon, death_key[k]), death_key[k])

    best = (0.0, None, None, None)
    used: list[str] = []
    for e in ordered:
        if e.killer_id != pid or weapon_prices.classify(e.weapon) != "priced":
            continue
        gun, own = e.weapon, (used[-1] if used else None)
        if (own is not None and gun != own and gun not in used and gun in teammate_gun_death
                and _event_key(e) > teammate_gun_death[gun]):
            value = max(0.0, float(prices[gun] - prices[own]))
            if value > best[0]:
                best = (value, "in_round", gun, own)
        used.append(gun)

    own_last = used[-1] if used else None
    first_next = next((e for e in ordered_events(next_events) if e.killer_id == pid), None)
    if (first_next is not None and own_last is not None
            and weapon_prices.classify(first_next.weapon) == "priced"):
        gun = first_next.weapon
        if gun != own_last and gun in teammate_gun_death and cash - next_remaining < prices[gun]:
            value = max(0.0, float(prices[gun] - prices[own_last]))
            if value > best[0]:
                best = (value, "carried", gun, own_last)
    return best
```

- [ ] **Step 4: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py tests/test_econ_buy_disruption.py tests/test_econ_buy_disruption_abyss_parity.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit** ("Net inferred pickups against the bonus-round denial").

---

### Task 5: Victim-side and out-of-branch parity on the Abyss fixture and random rounds

**Files:**
- Test: `webapp/tests/test_econ_bonus_denial.py` (append)

**Interfaces:**
- Consumes: `tests.test_econ_buy_disruption_abyss_parity.results(model) -> dict[int, RoundEconResult]`.

- [ ] **Step 1: Append tests**

```python
# ---- parity (spec section 12) ---------------------------------------------------------------
import random

from tests.test_econ_buy_disruption_abyss_parity import results as abyss_results


def test_abyss_outside_rounds_2_and_14_is_identical_to_30_80():
    old, new = abyss_results(bd.MODEL_V2_30_80), abyss_results(NEW)
    compared = 0
    for rn, o in old.items():
        n = new[rn]
        if bd.half_round_index(rn) == 2:
            # The frozen Abyss snapshot carries no weapons or next-round kills: it abstains, visibly.
            assert o.abstention is not None or n.abstention == "missing_bonus_inputs"
            continue
        assert n.abstention == o.abstention
        assert n.raw_net_by_player() == o.raw_net_by_player()
        compared += 1
    assert compared > 0


@pytest.mark.parametrize("seed", range(25))
def test_random_round_2_victims_on_the_pistol_loser_are_identical_to_30_80(seed):
    rng = random.Random(seed)
    order = rng.sample(ALL, 10)
    events, deaths, t = [], {}, 0.0
    for victim in order[: rng.randint(1, 8)]:
        killers = [k for k in ALL if k not in deaths and (k in A_IDS) != (victim in A_IDS)] or [None]
        t += rng.uniform(1, 9)
        events.append(ev(len(events) + 1, t, rng.choice(killers), victim, rng.choice(["Vandal", "Spectre", "Ghost"])))
        deaths[victim] = 1
    kw = dict(events=events, outcome=rng.choice([WIN_A, WIN_B]), player_kw=dict(
        deaths=deaths, loadout={i: rng.choice([800, 2000, 3900]) for i in ALL},
        next_loadout={i: rng.choice([0, 1500, 3900]) for i in ALL},
        next_bank={i: rng.choice([0, 800, 3000]) for i in ALL}))
    old, new = score(bd.MODEL_V2_30_80, **kw), score(NEW, **kw)
    assert new.abstention is None
    for o, n in zip(old.events, new.events):
        assert o.event_id == n.event_id
        if o.victim_team == B:
            assert (n.credit, n.victim_debit) == (o.credit, o.victim_debit)
    for pid in B_IDS:
        assert new.players[pid].debit == old.players[pid].debit
```

- [ ] **Step 2: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_econ_bonus_denial.py -q`
Expected: PASS. If the Abyss test finds a round-2/14 30/80 result that scored while new abstains for a reason other than `missing_bonus_inputs`, stop and report: that is an implementation finding.

- [ ] **Step 3: Commit** ("Pin bonus-denial parity with 30/80 outside its branch").

---

### Task 6: Wire the new inputs through `impact.py`

**Files:**
- Modify: `webapp/app/scoring/impact.py` (kill loader ~line 939; `_buy_disruption_econ_for_round` ~line 823; its call site ~line 1201)
- Modify: `webapp/tests/buy_disruption_fixtures.py` (`build_match` opt-in parameters)
- Test: `webapp/tests/test_impact_bonus_denial_integration.py`

**Interfaces:**
- Consumes: `round_rewards.round_reward`, `bd.outcome_winner`, `_attacking_team` (existing in impact.py).
- Produces: `build_match(..., weapons=None, planted=None, count_stats=False)`; `_buy_disruption_econ_for_round(..., next_kills: list[dict] | None)`.

- [ ] **Step 1: Extend the fixture (opt-in, default behaviour unchanged)**

In `build_match` signature add `weapons=None, planted=None, count_stats=False`; set `weapons, planted = weapons or {}, planted or {}`. When creating `Round`, use `planted=planted.get(number, False)`. Before adding stats, if `count_stats`:
```python
        killed = defaultdict(int); died = defaultdict(int)
        for killer, victim, _t in kills.get(number, ()):
            if killer: killed[killer] += 1
            if victim: died[victim] += 1
```
and pass `kills=killed[name] if count_stats else 0, deaths=died[name] if count_stats else 0`. In the kill loop:
```python
        round_weapons = weapons.get(number, [])
        for index, (killer, victim, t) in enumerate(kills.get(number, ())):
            weapon = round_weapons[index] if index < len(round_weapons) else "Vandal"
```
and pass `weapon=weapon` to `KillEvent`. Add `from collections import defaultdict`.

- [ ] **Step 2: Write the failing integration test**

`webapp/tests/test_impact_bonus_denial_integration.py`:
```python
"""The bonus-denial model through build_impact_rows_for_match (spec section 8).

Round 1: Team A wins the pistol. Round 2: B1 kills A1 (Jett, loadout 2600 -> kit
2050) and Team B wins, so A lost round 2 -> factor 1.0, reward into round 3 is 1900.
Survivors A2-A5: cash 1000 + 1900 = 2900; surplus (1000+3900) - (2900+3900) = -1900: no recovery.
    B1 credit 1.0 * 1.10 * 2050 / 19500 * 1007.9209 = 116.56 -> +117
    A1 debit  0.8 * that                            =  93.25 ->  -93
Under 30/80 the same round gives A1's loss the carryover-target treatment instead.
"""
from app.scoring import econ_buy_disruption as bd
from app.scoring.impact import build_impact_rows_for_match
from tests.buy_disruption_fixtures import build_match, round_id, rows_for_round, session

NEW = dict(use_realized_swing=True, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL)
OLD = dict(use_realized_swing=True, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80)


def _match(db, **kw):
    return build_match(db, kw.pop("external_id", "bonus"), kills={2: [("B1", "A1", 10.0)]},
                       weapons={2: ["Spectre"]}, loadouts={2: {"A1": 2600}},
                       outcomes={2: "Team B Elimination Win"}, count_stats=True, **kw)


def test_bonus_denial_values_through_the_build_path():
    db = session()
    match, players, _ = _match(db)
    econ = []
    rows = build_impact_rows_for_match(db, match.id, econ_observer=lambda **kw: econ.append(kw), **NEW)
    r2 = rows_for_round(rows, round_id(db, match, 2))
    assert r2[players["B1"].id].econ_component == 117
    assert r2[players["A1"].id].econ_component == -93
    result = next(kw["result"] for kw in econ if kw["round_number"] == 2)
    assert result.audit_version == 2 and result.abstention is None
    assert result.teams[players["A1"].team].bonus.denied == {players["A1"].id: 2050}


def test_other_rounds_match_30_80_through_the_build_path():
    db = session()
    match, players, _ = _match(db, external_id="parity")
    new = {(r.round_id, r.match_player_id): r.econ_component for r in build_impact_rows_for_match(db, match.id, **NEW)}
    old = {(r.round_id, r.match_player_id): r.econ_component for r in build_impact_rows_for_match(db, match.id, **OLD)}
    r2 = round_id(db, match, 2)
    assert {k: v for k, v in new.items() if k[0] != r2} == {k: v for k, v in old.items() if k[0] != r2}


def test_missing_kill_rows_abstain_rather_than_score_as_no_kills():
    db = session()
    match, players, _ = _match(db, external_id="nokills")
    from app.models import KillEvent
    db.query(KillEvent).delete()
    db.commit()
    econ = []
    build_impact_rows_for_match(db, match.id, econ_observer=lambda **kw: econ.append(kw), **NEW)
    result = next(kw["result"] for kw in econ if kw["round_number"] == 2)
    assert result.abstention == "kill_feed_incomplete"
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_bonus_denial_integration.py -q`
Expected: FAIL (`missing_bonus_inputs` abstention, econ 0 != 117).

- [ ] **Step 3: Implement in `impact.py`**

1. Import `from app.scoring import round_rewards` next to the other `app.scoring` imports.
2. Kill loader dict: add `"weapon": kill.weapon,`.
3. `_buy_disruption_econ_for_round` signature: add `next_kills: list[dict] | None = None` after `round_player_stats`. Build:
```python
    winners = {n: econ_buy_disruption.outcome_winner(row.outcome, Team.TEAM_1, Team.TEAM_2)
               for n, row in rounds_by_number.items()}
    next_round_reward = {team: round_rewards.round_reward(winners, round_number + 1, team)
                         for team in (Team.TEAM_1, Team.TEAM_2)}
```
In `PlayerEconomy(...)` add:
```python
                agent=mp.agent,
                remaining=this_round.get(match_player_id, {}).get("remaining"),
                kills=this_round.get(match_player_id, {}).get("kills"),
                deaths=this_round.get(match_player_id, {}).get("deaths"),
                next_deaths=next_round.get(match_player_id, {}).get("deaths"),
```
In `EconEvent(...)` add `weapon=kill.get("weapon"),`. In `RoundEconInputs(...)` add:
```python
        next_events=(tuple(
            econ_buy_disruption.EconEvent(
                event_id=kill["id"], time_seconds=kill["event_time_seconds"],
                killer_id=kill["killer_match_player_id"], victim_id=kill["death_match_player_id"],
                weapon=kill.get("weapon"),
            ) for kill in next_kills) if (next_row is not None and next_kills is not None) else None),
        planted=rounds_by_number[round_number].planted,
        attacking_team=_attacking_team(round_number),
        next_round_reward=next_round_reward,
```
4. Call site: pass `next_kills=round_kills.get(round_number + 1, [])`.

- [ ] **Step 4: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_bonus_denial_integration.py tests/test_impact_buy_disruption_integration.py tests/test_econ_buy_disruption_abyss_parity.py tests/test_release_candidate_review_frozen.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit** ("Supply weapons, next-round kills and rewards to the econ calculator").

---

### Task 7: Manifest — comparator, constants, hashed sources, weapon fingerprint

**Files:**
- Modify: `webapp/app/scoring/impact_manifest.py`
- Test: `webapp/tests/test_impact_manifest.py` (append)

**Interfaces:**
- Produces: `impact_manifest.V2_30_80_BONUS = bd.MODEL_V2_30_80_BONUS_DENIAL`; comparator entry; `build_manifest(..., release_comparator=V2_30_80_BONUS)` works.

- [ ] **Step 1: Append failing tests**

```python
def test_bonus_comparator_differs_from_30_80_only_in_the_model():
    manifest = _manifest(release_comparator=impact_manifest.V2_30_80_BONUS)
    verify_manifest(manifest)
    old = dict(config_from_manifest(manifest, bd.MODEL_V2_30_80).build_kwargs())
    new = dict(config_from_manifest(manifest).build_kwargs())
    assert {k for k in old if old[k] != new[k]} == {"econ_model"}
    assert new["econ_model"] == bd.MODEL_V2_30_80_BONUS_DENIAL


def test_bonus_constants_and_modules_are_frozen():
    manifest = _manifest()
    for name in ("BONUS_AUDIT_VERSION", "BONUS_DENIAL_THRESHOLD", "SWING_VALUE_PER_CREDIT",
                 "BONUS_WON_FACTOR", "BONUS_LOST_FACTOR"):
        assert name in manifest["calculator_constants"]
    assert {"app/scoring/weapon_prices.py", "app/scoring/round_rewards.py"} <= set(manifest["source_digests"])


def test_a_weapon_only_change_moves_the_source_fingerprint():
    db = session()
    match, _, _ = build_match(db, "fpw", kills={7: [("A1", "B1", 10.0)]})
    before = match_source_fingerprint(db, match.id)
    from app.models import KillEvent
    db.query(KillEvent).one().weapon = "Phantom"
    db.commit()
    assert match_source_fingerprint(db, match.id) != before
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_manifest.py -q -k "bonus or weapon_only"`
Expected: FAIL (AttributeError on `V2_30_80_BONUS` is not a value failure -- first add `V2_30_80_BONUS = None` to the module, re-run, and see the assertion failures).

- [ ] **Step 2: Implement**

```python
V2_30_80_BONUS = bd.MODEL_V2_30_80_BONUS_DENIAL
# COMPARATORS:
    # The round 2/14 bonus-round denial candidate (spec 2026-09-12).
    V2_30_80_BONUS: ImpactScoringConfig(V2_30_80_BONUS, enable_econ_component=True,
                                        econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL),
```
`HASHED_SOURCES`: add `"app/scoring/weapon_prices.py"` and `"app/scoring/round_rewards.py"` after `econ_component.py`.
`CALCULATOR_CONSTANT_NAMES`: append the five bonus names.
`_FINGERPRINT_QUERIES["events"]`: `"k.event_time_seconds, k.weapon FROM kill_events k ..."`.

- [ ] **Step 3: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_impact_manifest.py tests/test_release_candidate_review_frozen.py tests/test_backfill_impact_candidate.py -q`
Expected: all PASS.

- [ ] **Step 4: Commit** ("Freeze the bonus-denial comparator, constants and kill weapons in the manifest").

---

### Task 8: Stored spend (separate table, adapter, migration)

**Files:**
- Create: `webapp/app/models/round_player_spend.py`, `webapp/alembic/versions/0009_round_player_spend.py`
- Modify: `webapp/app/models/__init__.py`, `webapp/app/adapters/trackergg_browserstate_source.py`
- Test: `webapp/tests/test_round_player_spend_ingest.py`

**Interfaces:**
- Produces: `RoundPlayerSpend(round_player_stat_id: int, spent: int)`.

- [ ] **Step 1: Write the failing test**

```python
"""tracker.gg spentCredits is stored when present, never invented (spec section 10)."""
import pytest

from app.adapters.trackergg_browserstate_source import load_match
from app.models import RoundPlayerStat
from app.models.round_player_spend import RoundPlayerSpend
from tests.buy_disruption_fixtures import build_match, session

IDS = [f"P{i}#T" for i in range(10)]


def _stat(value):
    return {"value": value}


def _payload(match_id="m-spend", spent_for=lambda r, i: 100 * r + i):
    segments = [
        {"type": "team-summary", "attributes": {"teamId": "Red"}, "stats": {"roundsWon": _stat(2)}},
        {"type": "team-summary", "attributes": {"teamId": "Blue"}, "stats": {"roundsWon": _stat(0)}},
    ]
    for i, pid in enumerate(IDS):
        segments.append({"type": "player-summary", "attributes": {"platformUserIdentifier": pid},
                         "metadata": {"agentName": "Jett", "teamId": "Red" if i < 5 else "Blue"}})
    for r in (1, 2):
        segments.append({"type": "round-summary", "attributes": {"round": r}, "metadata": {},
                         "stats": {"winningTeam": _stat("Red"), "roundResult": _stat("Elimination")}})
        for i, pid in enumerate(IDS):
            stats = {k: _stat(0) for k in ("score", "kills", "deaths", "assists")}
            stats["loadoutValue"], stats["remainingCredits"] = _stat(800), _stat(0)
            spent = spent_for(r, i)
            if spent is not None:
                stats["spentCredits"] = _stat(spent)
            segments.append({"type": "player-round", "attributes": {"round": r, "platformUserIdentifier": pid},
                             "stats": stats})
    return {"attributes": {"id": match_id}, "metadata": {"mapName": "Bind"}, "segments": segments}


def test_spent_credits_are_stored_per_player_round():
    db = session(all_tables=True)
    load_match(db, _payload())
    rows = {(s.round_player_stat_id): s.spent for s in db.query(RoundPlayerSpend)}
    assert len(rows) == 20
    assert sorted(rows.values())[:3] == [100, 101, 102]


def test_absent_spent_credits_write_no_row_not_zero():
    db = session(all_tables=True)
    load_match(db, _payload(spent_for=lambda r, i: None if i == 3 else 0))
    assert db.query(RoundPlayerSpend).count() == 18
    assert {s.spent for s in db.query(RoundPlayerSpend)} == {0}


def test_ingesting_into_a_populated_database_leaves_old_matches_unknown():
    db = session(all_tables=True)
    old, _, _ = build_match(db, "old")
    old_stats = db.query(RoundPlayerStat).count()
    load_match(db, _payload("m-new"))
    assert db.query(RoundPlayerSpend).count() == 20
    linked = {s.round_player_stat_id for s in db.query(RoundPlayerSpend)}
    old_ids = {s.id for s in db.query(RoundPlayerStat).join(RoundPlayerStat.round).filter_by(match_id=old.id)}
    assert len(old_ids) == old_stats and not (linked & old_ids)
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_round_player_spend_ingest.py -q`
Expected: first create an empty `round_player_spend.py` defining the model (Step 2, model only), then FAIL on counts (0 != 20).

- [ ] **Step 2: Implement**

`webapp/app/models/round_player_spend.py`:
```python
"""tracker.gg spentCredits per player-round (spec 2026-09-12 section 10).

A separate additive table so app/models/round.py -- shared with the public repo
and hashed by the scoring manifest -- is untouched. No row means UNKNOWN: the
value is never written as 0 when tracker.gg did not supply it. The scorer does
not read this table.
"""
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RoundPlayerSpend(Base):
    __tablename__ = "round_player_spend"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_player_stat_id: Mapped[int] = mapped_column(
        ForeignKey("round_player_stats.id", ondelete="CASCADE"), nullable=False, unique=True)
    spent: Mapped[int] = mapped_column(Integer, nullable=False)
```
`app/models/__init__.py`: `from app.models.round_player_spend import RoundPlayerSpend` and add `"RoundPlayerSpend"` to `__all__`.

Adapter: import `RoundPlayerSpend`; in the `player_rounds` loop build `stat_row = RoundPlayerStat(...)`, `db.add(stat_row)`, and `if "spentCredits" in stats: pending_spend.append((stat_row, stats["spentCredits"]["value"]))` (declare `pending_spend = []` before the loop). After the loop:
```python
    if pending_spend:
        db.flush()
        for stat_row, spent in pending_spend:
            db.add(RoundPlayerSpend(round_player_stat_id=stat_row.id, spent=spent))
```

`webapp/alembic/versions/0009_round_player_spend.py`:
```python
"""add round_player_spend

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12

tracker.gg's spentCredits per player-round, stored going forward only
(docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md,
section 10). Additive: no existing table changes and no backfill -- a
missing row means unknown spend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "round_player_spend",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("round_player_stat_id", sa.Integer(),
                  sa.ForeignKey("round_player_stats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spent", sa.Integer(), nullable=False),
        sa.UniqueConstraint("round_player_stat_id", name="uq_round_player_spend_stat"),
    )


def downgrade() -> None:
    op.drop_table("round_player_spend")
```

- [ ] **Step 3: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_round_player_spend_ingest.py -q`
Expected: PASS.

- [ ] **Step 4: Exercise the migration on a throwaway database (never the synced one)**

```bash
docker compose -p valomaths-private exec -T postgres createdb -U valorant spend_migration_check
DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/spend_migration_check ./.venv/Scripts/python.exe -m alembic upgrade head
DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/spend_migration_check ./.venv/Scripts/python.exe -m alembic downgrade 0008
DATABASE_URL=postgresql+psycopg2://valorant:valorant@localhost:5433/spend_migration_check ./.venv/Scripts/python.exe -m alembic upgrade head
docker compose -p valomaths-private exec -T postgres dropdb -U valorant spend_migration_check
```
Expected: each alembic command exits 0. First confirm `app/config.py` reads `DATABASE_URL` from the environment, ahead of `.env`. If it does not, stop and report rather than point at the main DB.

- [ ] **Step 5: Commit** ("Store tracker.gg spentCredits in an additive table").

---

### Task 9: Comparison script (30/80 vs bonus denial)

**Files:**
- Create: `webapp/scripts/compare_econ_models.py`
- Test: `webapp/tests/test_compare_econ_models.py`

**Interfaces:**
- Consumes: `scripts.release_candidate_review.score_with(db, match_id, config) -> {"rows", "econ", "kills"}`, `_quantiles`, `_spearman`; `impact_manifest.load_manifest/verify_manifest/config_from_manifest/lf_sha256`.
- Produces: `compare(db, manifest, match_ids=None, min_matches=20, progress=None) -> dict`; CLI `--manifest PATH --report-dir DIR [--match ID ...]`.

- [ ] **Step 1: Write the failing test**

```python
"""compare_econ_models: parity holds where declared, differences are counted."""
import scripts.compare_econ_models as cmp
from app.scoring import impact
from app.scoring import impact_manifest as im
from tests.buy_disruption_fixtures import build_match, session


def test_small_corpus_reconciles_and_reports_the_round_two_change():
    db = session()
    build_match(db, "c1", kills={2: [("B1", "A1", 10.0)], 5: [("A2", "B2", 5.0)]}, weapons={2: ["Spectre"]},
                loadouts={2: {"A1": 2600}}, outcomes={2: "Team B Elimination Win"}, count_stats=True)
    build_match(db, "c2", kills={7: [("A1", "B1", 10.0)]}, count_stats=True)
    manifest = im.build_manifest(candidate_id="t", created="2026-09-12", scorer_revision="t",
                                 activation_impact_calculation_version=impact.IMPACT_CALCULATION_VERSION + 1,
                                 source_snapshots={"matches": {}}, release_comparator=im.V2_30_80_BONUS)
    report = cmp.compare(db, manifest, min_matches=1)
    assert report["parity_mismatches"] == {}
    assert report["matches_scored"] == 2
    assert report["bonus"]["qualifying_deaths"] == 1
    assert report["by_round_number"]["2"]["new_gross_points_per_round"] > report["by_round_number"]["2"]["old_gross_points_per_round"]
    assert report["by_round_number"]["5"]["new_gross_points_per_round"] == report["by_round_number"]["5"]["old_gross_points_per_round"]
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compare_econ_models.py -q`
Expected: FAIL (module missing -> create a stub `def compare(*a, **k): return {}` first, then KeyError/assert failures).

- [ ] **Step 2: Implement `webapp/scripts/compare_econ_models.py`**

```python
"""Score every match under the current 30/80 candidate and the round 2/14
bonus-denial model from ONE frozen manifest, and report what changes.

Reconciliation (any count is a failure, exit 1):
  * every player-round's non-econ fields are identical between the two models;
  * outside half-round 2, every player-round econ value is identical;
  * in half-round 2, when both models score, every event whose victim is on the
    pistol-LOSING team has identical credit and victim debit.
Read-only: one repeatable-read snapshot, rolled back. Players are identified by
match_player id only (the repo is public).
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.scoring import econ_buy_disruption as bd  # noqa: E402
from app.scoring import econ_component  # noqa: E402
from app.scoring.impact import ImpactInputError  # noqa: E402
from app.scoring.impact_manifest import (  # noqa: E402
    config_from_manifest, lf_sha256, load_manifest, verify_manifest,
)
from scripts.release_candidate_review import NON_ECON_FIELDS, _quantiles, _spearman, score_with  # noqa: E402

OLD = bd.MODEL_V2_30_80


def _gross_points(result, scale_c):
    if result.abstention:
        return 0.0
    return scale_c * sum(a.credit + a.debit for a in result.teams.values())


def compare(db, manifest, match_ids=None, min_matches=20, progress=None):
    old_cfg = config_from_manifest(manifest, OLD)
    new_cfg = config_from_manifest(manifest)
    scale_c = econ_component.ECON_SCALE * new_cfg.weights.econ
    if match_ids is None:
        match_ids = [m for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()]
    player_of = {mp: pl for mp, pl in db.execute(text("SELECT id, player_id FROM match_players")).all()}

    parity = defaultdict(int)
    failures = {}
    by_rn = defaultdict(lambda: dict(rounds=0, old=0.0, new=0.0))
    abstain = defaultdict(int)
    bonus = defaultdict(float)
    econ_delta_rows, impact_delta_matches, movers = [], [], []
    leader = defaultdict(lambda: dict(old=0, new=0, rounds=0, matches=0))
    rank_changes = players_seen = 0
    for index, match_id in enumerate(match_ids):
        if progress and index % 250 == 0:
            progress(index, len(match_ids))
        try:
            old = score_with(db, match_id, old_cfg)
            new = score_with(db, match_id, new_cfg)
        except ImpactInputError as exc:
            failures[str(match_id)] = str(exc)
            continue
        rn_of = {rid: rn for rid, rn in db.execute(
            text("SELECT id, round_number FROM rounds WHERE match_id = :m"), {"m": match_id}).all()}
        per_player = defaultdict(lambda: dict(old=0, new=0, rounds=0))
        for o_row, n_row in zip(old["rows"], new["rows"]):
            rn = rn_of[n_row.round_id]
            for field in NON_ECON_FIELDS:
                if getattr(o_row, field) != getattr(n_row, field):
                    parity[f"non_econ_{field}"] += 1
            if bd.half_round_index(rn) != 2 and o_row.econ_component != n_row.econ_component:
                parity["econ_outside_half_round_2"] += 1
            econ_delta_rows.append(n_row.econ_component - o_row.econ_component)
            p = per_player[n_row.match_player_id]
            p["old"] += o_row.impact
            p["new"] += n_row.impact
            p["rounds"] += 1
        for rn, kw in new["econ"].items():
            n_res, o_res = kw["result"], old["econ"][rn]["result"]
            by_rn[rn]["rounds"] += 1
            by_rn[rn]["old"] += _gross_points(o_res, scale_c)
            by_rn[rn]["new"] += _gross_points(n_res, scale_c)
            if bd.half_round_index(rn) != 2:
                continue
            if n_res.abstention:
                abstain[n_res.abstention] += 1
                if not o_res.abstention:
                    abstain["scored_by_30_80_only"] += 1
                continue
            for o_ev, n_ev in zip(o_res.events, n_res.events):
                team_bonus = n_res.teams[n_ev.victim_team].bonus
                if team_bonus is None and (o_ev.credit, o_ev.victim_debit) != (n_ev.credit, n_ev.victim_debit):
                    parity["pistol_loser_victim_event"] += 1
            for audit in n_res.teams.values():
                if audit.bonus is None:
                    continue
                b = audit.bonus
                bonus["team_rounds"] += 1
                bonus["team_rounds_won"] += b.won
                bonus["qualifying_deaths"] += len(b.denied)
                bonus["denied_credits"] += sum(b.denied.values())
                bonus["net_denied_credits"] += sum(b.net_denied.values())
                bonus["recovered_credits_applied"] += sum(b.denied.values()) - sum(b.net_denied.values())
                for s in b.survivors:
                    bonus["survivors"] += 1
                    bonus["survivors_credit_evidence"] += s.credit_recovery > 0
                    bonus["survivors_feed_in_round"] += s.feed_inference == "in_round"
                    bonus["survivors_feed_carried"] += s.feed_inference == "carried"
                    bonus["survivors_both_evidence"] += s.credit_recovery > 0 and s.feed_recovery > 0
            bonus["unidentified_weapon_flags"] += sum("unidentified_weapon" in f for f in n_res.data_quality)
        before = sorted(per_player, key=lambda mp: -per_player[mp]["old"])
        after = sorted(per_player, key=lambda mp: -per_player[mp]["new"])
        for mp, p in per_player.items():
            players_seen += 1
            rank_changes += before.index(mp) != after.index(mp)
            impact_delta_matches.append(p["new"] - p["old"])
            movers.append((p["new"] - p["old"], match_id, mp, p["old"], p["new"]))
            lb = leader[player_of[mp]]
            lb["old"] += p["old"]; lb["new"] += p["new"]; lb["rounds"] += p["rounds"]; lb["matches"] += 1
    movers.sort()
    eligible = {pl: v for pl, v in leader.items() if v["matches"] >= min_matches}
    ids = sorted(eligible)
    old_avg = [eligible[pl]["old"] / eligible[pl]["rounds"] for pl in ids]
    new_avg = [eligible[pl]["new"] / eligible[pl]["rounds"] for pl in ids]
    old_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -old_avg[i])]
    new_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -new_avg[i])]
    moves = sorted(((new_order.index(pl) - old_order.index(pl), pl) for pl in ids), key=lambda m: -abs(m[0]))
    return {
        "old": OLD, "new": manifest["release_comparator"],
        "matches_requested": len(match_ids), "matches_scored": len(match_ids) - len(failures),
        "input_validation_failures": failures,
        "parity_mismatches": dict(parity),
        "half_round_2_abstentions_new": dict(sorted(abstain.items())),
        "bonus": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(bonus.items())},
        "by_round_number": {
            str(rn): {"scored_rounds": v["rounds"],
                      "old_gross_points_per_round": round(v["old"] / v["rounds"], 1),
                      "new_gross_points_per_round": round(v["new"] / v["rounds"], 1)}
            for rn, v in sorted(by_rn.items()) if v["rounds"]},
        "econ_change_player_round": _quantiles(econ_delta_rows),
        "impact_change_player_match": _quantiles(impact_delta_matches),
        "largest_player_match_decreases": [list(m) for m in movers[:10]],
        "largest_player_match_increases": [list(m) for m in movers[-10:][::-1]],
        "within_match_rank_changes": [rank_changes, players_seen],
        "leaderboard": {
            "min_matches": min_matches, "players": len(ids),
            "spearman_avg_impact_per_round": _spearman(old_avg, new_avg) if len(ids) > 2 else None,
            "top20_overlap": len(set(old_order[:20]) & set(new_order[:20])),
            "largest_rank_moves_player_id_old_new": [[pl, old_order.index(pl) + 1, new_order.index(pl) + 1]
                                                     for _, pl in moves[:15]],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--match", type=int, action="append")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    verify_manifest(manifest)
    db = SessionLocal()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    report = compare(db, manifest, args.match, progress=lambda i, n: print(f"  {i}/{n}", flush=True))
    report["snapshot"] = db.execute(text("SELECT txid_current_snapshot()::text")).scalar()
    report["manifest_lf_sha256"] = lf_sha256(args.manifest)
    db.rollback()
    out = Path(args.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model-comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    bad = report["parity_mismatches"] or report["input_validation_failures"]
    print(f"parity mismatches {report['parity_mismatches']}  failures {len(report['input_validation_failures'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_compare_econ_models.py -q`
Expected: PASS.

- [ ] **Step 4: Commit** ("Add the 30/80 vs bonus-denial corpus comparison").

---

### Task 10: Full suite, freeze the candidate, declare in the ledger (before any scored run)

**Files:**
- Create: `docs/superpowers/econ-bonus-denial-candidate/candidate-manifest.json` (via a scratchpad freeze script)
- Append: `docs/superpowers/2026-09-07-predeclared-values.md`

- [ ] **Step 1: Full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`
Expected: everything passes except the two known-red tests. Quote the counts.

- [ ] **Step 2: Freeze**

Copy the previous session's freeze script to this session's scratchpad as `freeze_bonus_candidate.py` with: `OUT = REPO / "docs/superpowers/econ-bonus-denial-candidate/candidate-manifest.json"`, `candidate_id="impact-bonus-denial-rc1"`, `created="2026-09-12"`, `release_comparator=V2_30_80_BONUS` passed to `build_manifest`, the same `ABYSS`, `FIXED_TEN`, `PISTOL_ROUND_TWO` roles, and notes:
- "Frozen before any scored run of this candidate. Release comparator buy_disruption_v2_30_80_bonus_denial; weights A=1.25, B=1, C=1 (rc2's), so the comparison isolates the formula."
- "Sizing, pickup calibration, the round-number profile and 8 raw captures were seen during design (spec section 11): development evidence, not an untouched validation set."
- "The unchanged buy_disruption_v2_30_80 comparator is carried for the rc2 parity demonstration."

The script refuses uncommitted scoring sources; run it after Task 9's commit. Record its printed LF-SHA and revision.

- [ ] **Step 3: Ledger declaration**

Append an entry `### 2026-09-12 -- round 2/14 bonus-round denial: frozen candidate, samples and checks, declared before scoring` stating:
- "**Nothing below has been scored.**"
- the manifest path, candidate id, LF-SHA and scorer revision
- a value table (threshold, swing value, factors, debit share, background, recovery rule, prices and name lists by reference to spec section 6)
- the comparators: 30/80 (current) vs bonus (new); live_legacy for site reviews
- the samples: Abyss 3104 (development only), the fixed ten, match 3120, the corpus
- the checks:
  1. source fingerprints
  2. `compare_econ_models.py` parity mismatches == 0 and input failures == 0
  3. the rc2 parity demonstration: a scratch manifest at this revision with `release_comparator=buy_disruption_v2_30_80`, whose `--corpus` audit must equal rc2's `corpus-audit.json` on every key except `snapshot`
  4. site reviews of 3104, the ten and 3120 reconcile with zero failures
  5. defect reinstatement catches every declared defect
- the reports: round-number gross profile both models, bonus evidence counts, abstentions, player-match impact change quantiles, rank changes, leaderboard Spearman
- the problem flags from spec section 11: half-round-2 gross mean > 2x the mean of rounds 3/4/15/16; any abstention reason > 1% of rounds 2/14; any parity/validation failure
- the deviation: the release-review `--corpus` audit is not run under the bonus manifest, because its wealth-vs-release gross-credit identity holds only for 30/80 by construction; the comparison script's victim-side parity replaces it
- interpretation: nothing is tuned from these results; this activates nothing

- [ ] **Step 4: Verify and commit**

`git diff --cached` must show only the manifest and the ledger append (verify the append landed with `grep -n "bonus-round denial: frozen candidate" ../docs/superpowers/2026-09-07-predeclared-values.md`). Commit: "Freeze the bonus-denial candidate and declare its review before scoring".

---

### Task 11: Run the declared reviews and report the difference

- [ ] **Step 1: rc2 parity demonstration** (scratch, background, ~15 min)

Build a scratch manifest at HEAD with `release_comparator=V2_30_80` in the scratchpad; run
`.\.venv\Scripts\python.exe scripts\release_candidate_review.py --manifest <scratch> --corpus --report-dir <scratch>\parity`;
then compare `parity/corpus-audit.json` with `docs/superpowers/econ-buy-disruption-candidate/corpus-audit.json` on every key but `snapshot` (a short Python diff). Expected: identical.

- [ ] **Step 2: Comparison corpus run** (background, ~10 min)

`.\.venv\Scripts\python.exe -X utf8 scripts\compare_econ_models.py --manifest ..\docs\superpowers\econ-bonus-denial-candidate\candidate-manifest.json --report-dir ..\docs\superpowers\econ-bonus-denial-candidate`
Expected: exit 0, parity mismatches {}.

- [ ] **Step 3: Site reviews under the bonus manifest**

```
$M = "..\docs\superpowers\econ-bonus-denial-candidate\candidate-manifest.json"; $D = "..\docs\superpowers\econ-bonus-denial-candidate"
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3104 --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --ten --compare site --report-dir $D
.\.venv\Scripts\python.exe -X utf8 scripts\release_candidate_review.py --manifest $M --match 3120 --trace 3120 --report-dir $D
```
Expected: reconciler reports zero failures. A failure is an implementation finding: report it, do not patch the check.

- [ ] **Step 4: Defect reinstatement** (scratch harness copied from the previous session's `reinstate_defects.py`, tests limited to the new test files plus manifest/integration tests)

Mutations, each target text exactly once:
- B1: the factor applied twice (`bonus.factor * bonus.factor *`)
- B2: the threshold `>` becomes `>=`
- B3: utility not subtracted (`exposure - _utility(victim)` -> `exposure`)
- B4: recovery summed (`max(credit_recovery, feed_value)` -> `credit_recovery + feed_value`)
- B5: credit recovery nets the full surplus (`surplus - utility if` -> `surplus if`)
- B6: the carried inference without the spend test (`and cash - next_remaining < prices[gun]` removed)
- B7: `net_denied` not floored (`max(0.0, 1.0 - team_recovered / total)` -> `1.0 - team_recovered / total`)
- B8: environmental deaths credited (`if kind == "enemy" else (0.0, 0.0)` in the qualifying branch -> `(0.0, value)`)
- B9: the pistol loser also scored by the branch (`victim.team == bonus_team` -> `True` in the event loop)
- B10: a missing weapon not refused (drop the `not isinstance(e.weapon, str)` list)
- B11: `kill_feed_incomplete` disabled
- B12: abstentions at audit version 1 (`audit_version=audit_version_for(model)` in `_abstain` -> `AUDIT_VERSION`)
- B13: the fingerprint without the weapon
- B14: absent spend written as 0 (adapter `if "spentCredits" in stats` -> always, `.get("spentCredits", {"value": 0})`)

Expected: every mutation is applied and detected. Write `defect-reinstatement.md` in the candidate folder.

- [ ] **Step 5: RESULT, SUMMARY, commit, report**

- Write `docs/superpowers/econ-bonus-denial-candidate/SUMMARY.md`: what changed, every declared check with its count, the round-number table (both models), bonus evidence counts, player-match change quantiles, rank changes, the problem flags evaluated, and nothing tuned.
- Append a ledger RESULT entry quoting the same numbers.
- Commit the reports.
- Report the difference to the owner.
