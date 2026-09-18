# Economy impact: round 2/14 bonus-round denial for the pistol-winning team

Date: 2026-09-12. Status: design agreed with the owner in session, external
review incorporated; **not implemented, not measured under a frozen manifest,
no activation.** Extends `2026-09-10-econ-buy-disruption-implementation.md`
(section 12, the 30%/80% candidate) as a separate model; that model is not
edited.

## 1. Owner direction

The owner's words, in order:

- "the econ for round 2 should matter more. its impactful to take a half buy gun
  away from the pistol win team as then they don't get the half buy gun into
  their bonus (round 3). we should be able to track if the team loses big
  purchases (over 1500 credits) and that should be detrimental similar to
  losing a full buy gun on a swing round (maybe 80% of that value). and it
  should be beneficial to the team that took the gun away. this is pretty much
  a only round 2 and 14 thing"
- If the pistol winner **wins** round 2: measure how much of the bought kit was
  actually lost going into round 3, accounting for teammates picking weapons up.
  "if the pistol win team loses then that should count similar to losing a full
  buy swing round as they have a high likely hood of losing the next round."
- A round-3 rebuy is "a gamble" -- if it fails, round 3's normal econ already
  charges it. Round 2 must not charge it again. Most players rebuy all utility
  every round, so used utility is not a denial.
- Pickups: "do credit reconstruction first with then confirming with the kill
  feed model as not every picked up gun is going to be used in a kill"; a
  survivor whose surplus exceeds their agent's utility cost has picked something
  up; the value netted is the surplus above that utility cost; the kill feed is
  used as well.
- Value: "80% of the full swing value" -- today a disrupted swing-round loss pays
  1.10 per credit (0.10 background + 1.00 disruption).

Why the current candidate under-reads it (measured 2026-09-12, 52,111 scored
rounds): round 2's swing term is 158 pts/round, the lowest of any round but 11,
because `severity_pool = min(lost, gap * activation)` is capped by cheap
bonus-round kits (~4,400 credits lost per team vs 12,000-15,000 later).

## 2. Scope

- Rounds with `half_round_index == 2`: rounds 2 and 14. Overtime is already
  ineligible.
- **Victims on the pistol-winning team** (winner of round 1 / 13, from
  `pistol_outcome`) are scored by this design.
- **Victims on the pistol-losing team** keep the 30%/80% calculation unchanged.
  That team's *players' totals still change*: their kills of pistol winners earn
  the new credit (section 5).
- Every other round is scored exactly as `MODEL_V2_30_80`.
- Out of scope: the pistol loser's own forced buys over 1,500 (owner framed the
  rule around the pistol-winning team); reading stored spend in scoring; any
  re-crawl; weight changes; including econ in Round Win Impact.

## 3. Terms

All credit quantities are per player, from the stored round rows.

| term | definition |
|---|---|
| `paid_i` | `max(0, loadout_i - free_ability_credits(agent_i))` (existing) |
| `utility_cost_i` | `AGENT_UTILITY_COST[agent_i]` -- the table, **not** `max_utility_cost`'s 650 fallback (section 7) |
| `kit_ex_util_i` | `max(0, paid_i - utility_cost_i)` |
| first death | a player's first chronological death in the round (existing `first_loss_exposures`); a repeated death exposes nothing |
| survivor | a pistol-winning-team player with no death event in round N |
| `won` | the pistol-winning team also won round N |
| `R` | 19,500 (`TEAM_REFERENCE`) |

**Qualifying death:** victim on the pistol-winning team, first death, and
`kit_ex_util_i > 1500` (strictly greater). Its `denied_i = kit_ex_util_i`.

**Non-qualifying first death** of a pistol-winning-team player: existing
background only -- killer credit `0.10 * paid_i / R` (enemy kills), victim debit
`0.30 * 0.10 * paid_i / R`. No disruption term.

Development evidence (not validation): 91% of pistol winners carry more than
1,500 paid in round 2/14; 70-74% of their deaths qualify under the ex-utility
rule; mean `denied` per qualifying death 2,584-2,600.

## 4. Recovery: pickups netted against the denial

Recovery is computed per **survivor** S, then pooled for the team. All pickup
evidence is **inferred**: credits show value that was not bought; a kill-feed
weapon shows use, not how or when the weapon was acquired.

### 4.1 Credit evidence (primary)

```
cash_S    = min(9000, remaining_N + 200 * kills_N + plant_bonus_S + reward_S)
surplus_S = (next_remaining_S + next_paid_S) - (cash_S + paid_S)
credit_recovery_S = surplus_S - utility_cost_S   if surplus_S > utility_cost_S
                  = 0                            otherwise
```

- `plant_bonus_S` = 300 if round N was planted and S's team attacked round N.
- `reward_S` = S's team's actual round-N+1 reward: 3,000 after a win, else
  1,900 / 2,400 / 2,900 by the real consecutive-loss streak within the half.
- **Amendment 2026-09-12 (owner rule, after code review):** a survivor of a round
  their team LOST banks the survive-loss **1,000**, never the loss bonus.
  - Checked at team level on the 8 raw captures, where teammate drops cancel:
    winners 126 of 127 exactly 15,000, and dead losers exact on the streak bonus.
  - Every true survivor of a lost round banked 1,000. The four apparent exceptions
    were defenders killed by the spike detonation: the kill feed records them as
    "Bomb" victims, but tracker's deaths stat omits them. Survival is therefore
    read from kill events, never from the deaths stat.
- Both are supplied as calculator inputs (section 8).

Development evidence, pistol-winner survivors of rounds 2/14 labelled by the kill
feed: the rule flags 12.4% of kill-feed-inferred pickups, 2.6% of survivors who
kept their own gun, 4.8% of all 16,666 survivors. The gap between those two
groups' median surplus (-200 vs -600, i.e. +400 for pickups) tracks the upgrade
(`price(new) - price(own)`), not the gun's price (medians +400 for 1-700
upgrades, +750 for 701-1,300). Survivors' carried kit moves by more than their
full utility cost 41% of the time even with exact spend (8 captures), so this
rule is conservative, not exact.

### 4.2 Kill-feed evidence (supplement)

A priced weapon is a name in the price table (section 6). S's **own weapon** is
the priced weapon of S's most recent kill in round N before the event being
tested. For (a) that is the latest earlier kill in round N; for (b), whose event
is in round N+1, it is S's last priced kill weapon in round N. If S has no such
kill, the own weapon is unknown and no kill-feed pickup is inferred for S.

A kill-feed pickup by S of weapon G, valued `max(0, price(G) - price(own))`, is
inferred when **all** hold:

1. a pistol-winning teammate V, who died in round N, killed with G in round N
   before dying;
2. S's own weapon is known, and differs from G; and one of:
   - **(a) in round:** S kills with G in round N after V's death, having not
     killed with G earlier in round N (a weapon cannot be bought mid-round), **and**
     S's paid kit is below `price(G)` plus the prices of every distinct priced weapon
     S had already killed with in round N (amendment 2026-09-12, owner rule: a kit
     that covers them all, e.g. Vandal + Sheriff = 3,700, shows S owned the gun); or
   - **(b) carried:** S's first kill in round N+1 uses G, and S's round-N+1
     spend `cash_S - next_remaining_S` is **less than** `price(G)` (S could not
     have bought it).

`feed_recovery_S` is the largest value over S's inferred pickups.

Development evidence: 120 in-round pickups (a) in rounds 2/14; of 178 N+1 carries
matching (b) without the spend test, 30 (17%) could have been purchases and 148
(83%) could not.

### 4.3 Combining evidence and allocating

```
recovery_S     = max(credit_recovery_S, feed_recovery_S)   # per survivor only
team_recovered = sum over survivors of recovery_S
net_denied_i   = denied_i * max(0, 1 - team_recovered / sum(denied))   # sum(denied) > 0
```

- **The "take the larger" rule applies only between one survivor's two
  estimates.** It is never summed, and never compared across survivors, so one
  recovery is never counted twice.
- If `sum(denied) = 0` (no qualifying deaths), there is nothing to net; recovery
  is still reported in the audit.
- Recovery reduces the team's qualifying deaths pro rata and floors each at zero.
  A qualifying death with `net_denied_i = 0` pays nothing (no background fallback).
- Credits cannot tell a teammate's gun from an enemy's; an enemy weapon picked up
  also counts as recovery.
- Player econ nets stay **signed** (owner clarification 2026-09-10). Only
  `net_denied_i` is floored.

## 5. Values for a qualifying death

```
V = 0.8 if won else 1.0
SWING_VALUE_PER_CREDIT = 1.10                     # BACKGROUND + DISRUPTION
killer credit (raw) = V * 1.10 * net_denied_i / R # enemy kills only
victim debit  (raw) = 0.80 * V * 1.10 * net_denied_i / R
```

This **replaces** both background and disruption for the event. The outcome
factor and the victim's 80% each apply exactly once. Self, team and
environmental deaths take the victim debit and award no credit (existing
convention).

Points use the existing path: the scorer multiplies each player-round's raw net
by `ECON_SCALE (1007.9209) * C` and rounds once.

| net denied 1,000 credits | killer credit basis | victim debit basis | pts at C=3.871 |
|---|---:|---:|---:|
| pistol winner **wins** round N | 880 | -704 | +176 / -141 |
| pistol winner **loses** round N | 1,100 | -880 | +220 / -176 |

## 6. Declared constants and tables

New in `econ_buy_disruption.py` (added to `CALCULATOR_CONSTANT_NAMES`):
`BONUS_DENIAL_THRESHOLD = 1500`, `SWING_VALUE_PER_CREDIT = 1.10`,
`BONUS_WON_FACTOR = 0.8`, `BONUS_LOST_FACTOR = 1.0`, `CREDIT_CAP = 9000`.

New module `app/scoring/weapon_prices.py` (hashed):

| weapon | price | weapon | price | weapon | price |
|---|---:|---|---:|---|---:|
| Classic | 0 | Stinger | 1,100 | Guardian | 2,250 |
| Shorty | 300 | Spectre | 1,600 | Phantom | 2,900 |
| Frenzy | 450 | Bucky | 850 | Vandal | 2,900 |
| Ghost | 500 | Judge | 1,850 | Marshal | 950 |
| Bandit | 600 | Bulldog | 2,050 | Outlaw | 2,400 |
| Sheriff | 800 | Ares | 1,600 | Operator | 4,700 |
| | | Odin | 3,200 | | |

Current prices, checked 2026-09-12 against Hotspawn, Mobalytics, Gfinity,
Sportskeeda (Bandit) and Liquipedia (Odin). One table for all dates; patch-era
price differences are an accepted limitation.

**Declared non-purchasable names** (never pickup evidence), every other name
observed in the corpus: Headhunter, Tour De Force, Blade Storm, Showstopper,
Overdrive, Not Dead Yet, Hunter's Fury, Paint Shells, Shock Bolt, Hot Hands,
Annihilation, Orbital Strike, Aftershock, Guided Salvo, Nanoswarm, Incendiary,
Boom Bot, Mosh Pit, Armageddon, FRAG/ment, TURRET, Snake Bite, Blaze, Razorvine,
Trailblazer, Trapwire, Special Delivery, Curveball, Crush, Blast Pack, Dizzy,
Bomb, Fall, Melee.

**Declared unidentified names:** `Weapon` (958 corpus kills), `Unknown` (7),
`Primary` (2). They contribute no kill-feed evidence and are listed in the round
audit's `data_quality`. This is a declared fallback: the feed only supplements
credit evidence (section 4.3), so abstaining a whole round over them would be
disproportionate.

The pure round-reward helper lives in a new hashed module
`app/scoring/round_rewards.py` with no imports from `impact.py`.
`credit_events.round_bonus` imports `impact.py`, which imports the calculator,
so the calculator cannot reuse it. `credit_events.py` is not changed here.

## 7. Abstentions (new model only)

These run after the existing guards, in this fixed order. A round-2/14 result
that abstains scores zero for the whole round, as today.

1. `unknown_agent_utility` -- a pistol-winning-team player's agent is not in
   `AGENT_UTILITY_COST`.
2. `missing_bonus_inputs` -- any required input for section 4 is absent:
   current remaining or kills, next-round stats, the plant flag, attacking side,
   next-round reward, or round N+1's event list.
3. `unrecognised_weapon` -- a kill by a pistol-winning-team player in round N or
   N+1 carries a name that is in neither the price table, the non-purchasable
   list, nor the unidentified list.
4. `kill_feed_incomplete` -- for round N or N+1, the sum of the `deaths` stat
   exceeds the number of kill events. This separates a complete round without
   kills from missing kill data. Measured 2026-09-12: 0 of 65,929 rounds; more
   events than deaths (repeated deaths) in 2.3%, which is valid.

Every new-model result, including abstentions, reports `audit_version = 2`. The
existing models keep 1.

## 8. Inputs and integration

- New optional fields default to `None` so existing callers and models are
  untouched.
  - `PlayerEconomy`: `agent`, `remaining`, `kills`, `deaths`, `next_deaths`.
    The last two feed `kill_feed_incomplete` (plan-time amendment). An absent
    agent is a missing input; `unknown_agent_utility` applies only to a named
    agent the utility table lacks.
  - `EconEvent`: `weapon`.
  - `RoundEconInputs`: `next_events`, `planted`, `attacking_team`,
    `next_round_reward` (per team).
- For the new model, `None` in any of them triggers `missing_bonus_inputs`, never
  an assumed zero.
- `impact.py`: the kill loader keeps `weapon`, and
  `_buy_disruption_econ_for_round` fills the new fields.
- The team audit gains: `denied`, `team_recovered`, per-survivor
  `credit_recovery` / `feed_recovery` with the inference type, `net_denied` per
  victim, and `won`.

## 9. Manifest, candidate and parity

- New model id `MODEL_V2_30_80_BONUS_DENIAL`, added to `BUY_DISRUPTION_MODELS`,
  plus a comparator in `impact_manifest.py`.
- `HASHED_SOURCES` += `app/scoring/weapon_prices.py`, `app/scoring/round_rewards.py`.
- `_FINGERPRINT_QUERIES["events"]` += `k.weapon`, so a weapon-only change
  invalidates a review. Every other newly consumed field (remaining, kills,
  agent, planted, outcome) is already fingerprinted.
- **rc2 is preserved, not re-verified.** Editing hashed scoring files makes the
  rc2 manifest fail verification against the new checkout; that is correct and
  verification is not weakened. Instead:
  - rc2's artifacts stay untouched.
  - The new manifest carries the unchanged `buy_disruption_v2_30_80` comparator.
  - Its corpus audit must reproduce rc2's `corpus-audit.json` exactly, apart
    from identity lines. That is the parity demonstration.
- Candidate folder `docs/superpowers/econ-bonus-denial-candidate/`, frozen with
  **rc2's weights** (A=1.25, B=1, C=1) so the comparison isolates the formula.
  Reviews use the existing README commands.
- `ACTIVE_MANIFEST` stays `None`; `IMPACT_CALCULATION_VERSION` is not bumped by
  this work.

## 10. Stored spend (separate storage work)

- Migration `0009` creates `round_player_spend` (`id`,
  `round_player_stat_id` FK unique, `spent` integer NOT NULL), with model file
  `app/models/round_player_spend.py`. It is additive, and `round.py` is untouched.
- The adapter writes a row when tracker's `spentCredits` is present.
- **No row means unknown**; a missing value is never written as 0. Existing
  matches have no rows and no re-crawl is done.
- The scorer does not read this table, so it is not hashed or fingerprinted.
  Reading it later is a new design.

## 11. Ledger declaration (before any scored run)

- **What:** an entry in `docs/superpowers/2026-09-07-predeclared-values.md`
  declaring:
  - sections 3-7 verbatim, with the constants, price table and name lists
  - samples: Abyss 3104, the fixed ten, match 3120, the corpus
  - comparators: rc2's 30/80 via the new manifest
- **Reports:**
  - the round-number econ profile
  - pickup counts by evidence type
  - abstention counts by reason
  - the parity demonstration
- **Problem flags:**
  - any validation failure or parity mismatch
  - mean round-2/14 gross above twice the mean of rounds 3/4 and 15/16
  - an abstention reason firing on more than 1% of rounds 2/14
- **Disclosure:** the sizing (~1,190 pts per round 2/14 from this term, vs ~305
  today), the pickup calibration, the round-number profile and the 8 raw
  captures were all seen during design. They are **development evidence, not an
  untouched validation set.**

## 12. Tests (value failures first)

- **Calculator:**
  - V 0.8 vs 1.0
  - threshold at 1,500 and 1,501
  - utility subtracted before the threshold
  - non-qualifying deaths use 30% background
  - credit recovery nets `surplus - utility_cost` only above the utility cost
  - feed recovery nets the upgrade value, in-round (a) and carried (b)
  - (b) rejected when spend covers the price
  - **duplicate evidence:** the same pickup seen by both credits and feed nets
    the larger, not the sum
  - **full recovery** floors `net_denied` at 0 with signed player totals
  - **repeated deaths** expose nothing
  - environmental/self/team deaths: debit, no credit
  - **round 14** uses pistol round 13
- **Victim-side parity (replaces any whole-team assertion):** every event whose
  victim is on the pistol-losing team, and every event in rounds other than
  2/14, is identical to `MODEL_V2_30_80`. Checked on the Abyss fixture and a
  corpus sample.
- **Missing inputs:** each of the section-7 abstentions, including
  `kill_feed_incomplete` vs a genuine no-kill round; abstentions report
  `audit_version = 2`.
- **Manifest:** a weapon-only source change fails verification; the new
  constants and modules are hashed.
- **Storage:** the adapter stores `spentCredits`; absent input writes no row;
  ingestion into an already populated database; the migration's upgrade and
  downgrade.
- **Defect reinstatement:** one reinstated defect per branch of sections 4, 5 and
  7, each caught by a test.

## 13. Known limitations

- A pickup nobody kills with, and whose value is hidden by utility use or shield
  damage, goes unseen or is under-netted. This leans toward keeping the denial.
- Kill-feed evidence exists only when both players got kills with the weapon.
  A victim's own weapon is known from the feed for 38% of pistol-winner deaths.
- One price table for all patches.
- Enemy weapons recovered by survivors count as recovery.
