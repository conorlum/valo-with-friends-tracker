# Critical Util - design specification

Date: October 21, 2026. Status: design only; no application changes. Branch `worktree-critical-util`, cut from
`origin/main` at `26c2578d`. Revised the same day after an independent review (findings folded in below; the
owner questions it raised are under "Open questions").

## Built (2026-10-21)

Built on branch `worktree-critical-util` on top of `CONDENSE_REVISION` 21 (PR #167), which it takes to 22. Where the
build differs from the text below, or leaves something for later:

- **Hold space** (owner, 2026-10-21: wired in this round) uses the map-control unknown: the gap run steps an
  unknown probe (`app/critutil/unknown.py`) beside the gap detector, and for each trip, area watcher, camera, turret
  and molly measures the seconds the unknown stopped at its edge (`front_s`), the ground only it held (`held_m2s`)
  and the seconds the unknown got past it (`crossed_s`). Observed enemy presence (held back, waiting) is kept
  beside it.
- **Teammate statuses** (owner, 2026-10-21: every adverse status counts) are kept by the parser from
  `CONDENSE_REVISION` 22, flagged `friendly`; control skips them, Critical Util counts them as harm. Teammate damage
  counts half of a flash or a status.
- **Unused util names the ability** ("died holding Flashpoint"), never its keybind; the charge timelines' slot stays
  internal (`constants.AGENT_KEYS`).
- **The coach's flash detector** (`app/coach/detectors.py` `_flashes`) is kept beside the new util facts:
  replacing it changes existing coach output, which is the owner's call.
- **Weights** are fitted only when the corpus beats the starting weights on held-out matches; on the first 8
  replays measured it did not, so the starting weights (`util_values.DEFAULT_WEIGHTS`) are in use.
- **Ult cost** is shown as value per ult point, not subtracted from the value.
- **Measured on real data** (8 replays, read-only): about 0.3 s per round on the worker, no failures; the
  estimator, fitted on 3,868 non-replay matches, is calibrated within 1 point in every decile on held-out matches
  and beats man advantage alone (log loss 0.436 vs 0.440).

Fixed after the branch review (2026-10-22):

- **Friendly rows never reach control or Critical Moments**: `RoundInputs` reads the round through
  `engine.control_view` (revision 22's `friendly` rows taken out), so every reader, including a suppressed
  Cypher's devices (`_suppress_devices`), sees what revision 21 gave; the gap run hands Critical Moments the same
  view. Critical Util alone reads the whole blob.
- **Buy-phase placements**: the condenser starts every buy-phase placement at 0 s, so the same piece placed
  at another place by then (over `SAME_PLACE_M`, `BUY_PLACED_S`) is another charge; a tripwire's second anchor
  joins the wire whose `end` it sits on. Measured on 10 replays: 105 Cypher trip pairs at 0 s were one cast each;
  mid-round, same-name pieces at two places are one use (Skye's seekers, Neon's bolt), so the rule is the buy
  phase's only. The unknown probe's items carry their row's `id` (`Watcher.src`) and are matched by it.
- **The unknown probe** counts a device only while the engine has it watching (not switched off, owner alive,
  Q71), a molly while it burns, and nothing after `t_decided`; ground two of a team's items hold at once is split
  between them.
- **Shared outcomes once**: sight lines two smokes cut at the same instant are split, each covered teammate's
  save goes to one cast, an enemy waiting at two edges is one enemy-second, and an enemy held back belongs to the
  edge they came closest to. A defuse attempt is a cast's to stop only when the defuser is the caster's enemy and
  the cast reached them during it (an effect on the defuser, or a burning zone on the spike), and each stopped
  attempt goes to one cast.
- **Heals**: the lowest health in the heal's own life within `HEAL_FOLLOW_S` already holds every other heal, so a
  heal saved the life only when that low point minus its amount is at or below zero; heals each needed share the
  save by amount (`saved_share`); without vitals it is unavailable (None).
- **Teammate harm** is charged with the death it was on (`died_t`, inside that life), never a later life's.
- **Unused full ults**: the worker reports each slot's ult per round (`ults`); the site proves the cost from the
  match (the most points held before any use) and lists a round where a player ended full and never used it
  (`replay_util.unused_ults`). No claim for a slot that never used its ult in the match.
- **Freshness**: the tab, the coach and the corpus read only rows whose gap fingerprint is current
  (`replay_util.current_rows`: link, clock correction, geometry, chokes, hearing), Critical Moments' rule. The
  corpus key is computed from the rows a build read; rebuilds run one at a time (a Postgres advisory lock) and
  build again when the rows changed meanwhile. The estimator stores the replay matches it left out and is refitted
  before a match that later got a replay is valued (`util_model.ensure`).
- **Weights out of sample**: when fitted weights are adopted, each match is valued with its own fold's weights.
- **The no-util baseline** (section "Baseline") is built: every 2 s of every stored round, per side, the change in
  that side's round-win chance over the next 10 s; a cast's `lift` is its own change minus the mean of moments in
  other matches in the same state, phase and time bucket with no util of its kind up (falling back to state and
  phase, then man advantage, under 30 moments; the level is shown). Lineup scores and calls use the lift, shrunk
  toward the ability's mean lift (the ability prior, shown separately); the player's own moments without that util
  are shown beside it ("Yours"). Per-ability overlap is in `scripts/critical_util.py measure`. On 8 replays most
  casts compare at the full level; a cast's lift is noisy (sd about 20 points), which the shrinkage and the
  5-cast minimum are for. The `value` (the ledger and jobs credit) is still shown, labelled as credit.
- **After the decision**: post-`t_decided` casts are in the tab, marked not scored, and in no total or ranking.

## Outcome

Three things, all built from the stored replays (the corpus is the linked replays we have, nothing else):

1. A **Critical Util** tab in the replay viewer, on every linked replay, computed with no AI. A viewer picks a
   player and sees every piece of util they used, tagged with what it did, plus their best and worst util of the
   match. Each row moves the viewer to just before the cast.
2. A **cross-match util page**: which util wins rounds, which util each friend should keep throwing, and the
   patterns in how they use it.
3. **Coach facts**: every coached match gets the util tags and values as citable facts, and the coach adds the
   judgements the rules cannot make.

The point is to help people work out which util to keep throwing. Hitting an enemy helps a piece of util's
value but is not the value.

**What the numbers mean.** Every value here is a measured association between a cast and what followed it,
against a baseline. With about 60 replays it is evidence, not proof that the cast caused the outcome. The tab
and page state measurements ("blinded 2 for 1.8 s; teammate peeked 0.6 s later; 1 kill"); the blended score
ranks, it does not judge.

## Decisions (owner, 2026-10-21)

| # | Decision |
| --- | --- |
| Unit | Each cast on its own, and combos of casts. |
| Outcome | A blend. Util holds space, takes space, forces good fights, gives info; no single outcome covers it. |
| Factors | Core: enabled a kill, prevented a death, info, time/stall, space taken. Also: spike effect, wasted, timing, unused util, harm to teammates (flash/status matters more than damage), ult points spent. Minor: area denial (pathing), credits. |
| Baseline | Similar situations without that util, plus the player's own non-util fights where possible. Test that the baseline predicts fight results before relying on it. |
| Jobs | A cast is labelled with the jobs it did from context, scored on each, and the jobs combine in one currency without double counting (not the max). |
| Kill window | While the effect is active, plus a grace. Placed util (trips, turrets, cameras, clones, Vetos' interceptor) is judged from when it fires or is activated, not when placed. |
| Placed util | Holding the unknown back in a lane is value, not waste. Firing or being broken gives active info and is worth more. |
| Teammate harm | Weighted by what happened to the teammate. |
| Combos | Two or more teammates' casts within about 3 s, on the same area or the same fight; window tuned on the corpus. |
| Healing | Judged as util. A heal that kept a player alive through a fight they would otherwise have died in is worth far more than leftover HP. Healing lets players make different plays; that is measured too. |
| Unused util | Only deaths in a fight the player took while holding util that fits the fight, plus unused full ults. |
| Ranking | Friends only (the viewer's friendships, never `tracked_players.json`). Small samples shrunk toward the ability's mean, minimum 5 casts. Best/worst single examples need no minimum. |
| Breakdown | Which util wins rounds: ability x map x side. Lineups grouped by site (A/B/C), mid, and each side's own half. |
| Scoring method | Fixed rules for the tags, with the numbers stored. A small learned weighting only for the blended score, checked on held-out matches. |
| Coach | Util tags and values become citable facts. In coached matches the coach adds: was this the right util, what unused util would have done, did it line up with the team's play. |
| Economy | Full buy / pistol / every other round, using the existing thresholds. Loadout includes carried-over gear; accepted. |
| Process | This spec first; thresholds measured from the corpus before code. Ship, the owner checks it on the live site, then fix. |

## Depends on: replay parser change (separate branch)

In progress in worktree `replay-heals-charges` (`CONDENSE_REVISION` 21; old replays re-parsed from archived
uploads by `replay_reparse_auto`). It adds:

- **Damage causers**: ability damage runs name the actor that did the damage, joined to its util row where the
  join is proven. Unjoined ability damage stays unattributed; nothing here assumes every run resolves.
- **Heals**: `MulticastNotifyHeal` runs (amount, causer, healed player, life after), fed into
  `player_state.vitals`. Sources measured: Sage Healing Orb, Skye Regrowth, Reyna Devour/ult,
  Phoenix Hot Hands and ult, Miks, Clove Pick-Me-Up, KAY/O ult revive. Regen Shield sends no heal rows.
- **Charges**: per player and slot (C/Q/E), from `EquipmentChargeComponent`, ownership proven through the
  inventory slot. No ult charge component exists; ults come from `player_state.ult` (since revision 19).

- **Statuses on teammates** (owner, 2026-10-21: added to that branch). `find_statuses` (`app/replays/extras.py`,
  the `enemies()` filter) keeps statuses only on enemies, except debuff-only objects. The branch keeps
  same-team statuses too, flagged `friendly`, without changing what existing readers (control, Critical
  Moments) see. Flashes and nearsights already include teammates in `hits`. Replays that don't have the flag
  report teammate status harm as unavailable, never as zero.

**Older replays.** The two replays at revision 15 with no archived upload have no heals, causers, charges or
ult timelines. Any metric they cannot supply is marked unavailable for them and never enters a denominator as a
zero.

## The cast

A **cast** is one use of one ability charge (or one ult). Replays store actors, not casts: a projectile, the zone
it lands as, a second anchor, a wall's segments and an ult's pieces can all be one cast. The builder defines:

- A **canonical cast id** per round, and a per-ability grouping table (actor codes/names to one cast), starting
  from `ABILITY_KIND` (`app/moments/constants.py`) and the agent tables in `app/control/utility.json`.
- A cast's **kinds** are a set, not one label: a Viper wall is smoke and stall; a Harbor wall is smoke and (with
  High Tide) slow; a placed ult is a device and an ult.
- Placed devices: **set** = when it is placed and armed; **trigger** = the time the replay shows it firing,
  activating or being broken (`fx`, `gone`, `off`, a Vyse wall's `raised`; note `trigger` on a Vyse wall names
  the crossing player, not a time).
- Edge cases, decided:
  - Owner unknown (`by` null): kept for round-level totals, never ranked or credited to a player.
  - Thrown in the buy phase: set-up util counts from the barrier drop. Buy-phase-only actors (gone before the
    round) are already dropped by the extractor.
  - After the round is decided (`t > t_decided`): shown, never scored.
  - Clove's smokes after death: valid casts by Clove.
  - Yoru clones: scored on what the replay shows (trigger, flash hits, kills nearby), no invented watch.
  - Enemy reveals on our players: exposure, shown as context or harm, never positive info for us.
  - Ults: scored as casts, plus the ult points they cost, charged once per use.

## The currency: a round-win estimator

Neither Impact's graph weights (`app/scoring/impact.py`) nor fight-EV's state win rates (`fight_ev.py`) are the
contextual win probability this needs, and Impact's weights are not probabilities. Critical Util defines its own
**versioned round-win estimator**:

- **Inputs**: man advantage, side, time left, spike state (planted, time to detonation), each team's start-round
  economy tier. Optional where support allows: alive players' HP.
- **Fallback**: when a state cell has too little data, drop inputs in a fixed order (HP, then economy, then time
  bucket) down to man advantage x side.
- **Fitting**: cross-fitted by match, as `app/services/win_probability.py` already requires for its own model:
  a cast's value is always computed from an estimator that did not see that cast's match.
- **Validation before use**: on held-out matches, log loss and Brier score against simple baselines (man
  advantage only), plus calibration by bucket. Separately check **fight conversion**: does the estimator's swing
  across a fight match how fights actually resolve? At 60 matches this catches gross failure, not fine
  differences; the tab says which fallback level each value used.

## Baseline

For a cast, the comparison set is moments in the corpus with the same state (estimator cell) and **phase**
(before contact / after contact / post-plant) where that kind of util was not active. Before scoring:

- Check **overlap**: for each ability, the share of its casts whose state has a usable comparison set. Casts
  without one are scored at the next fallback level and say so.
- Where a player has enough of their own no-util fights in the same state, their **personal** baseline is shown
  beside it.
- Thresholds, priors and weights are set inside training folds only (split by match), never on the matches
  being reported.

## Jobs and how they are measured

A cast's **eligible jobs** are decided from its kind and context **before** looking at what happened after it.
It is then scored on every eligible job, including the ones that came to nothing (zero or negative). Eligibility
never depends on success.

Each job is measured with what the data can actually show:

| Job | Eligible when | Measured as |
| --- | --- | --- |
| Take space | Attack before plant, util landing in an area the team does not control | Control gain for the caster's team in that area over the following seconds (map control engine), against the baseline |
| Hold space | Defence, or any side holding an area | Devices and mollies: the modelled unknown kept out of the area while active (mollies block it, `control/mollies.py`). Smokes and walls: sight lines blocked (`control/geometry.py` `cast()`), since smokes do not stop the unknown in the engine |
| Force a fight | Util that affects players, or smoke/wall at a contact point | Overlap of the effect with a teammate's peek and the fight's result against the baseline |
| Info | Reveals, recon, trips, cameras, flashes that give position | Knowledge gained (`control/utility.py` information events) and what followed: a kill on a revealed enemy, team movement within the window |
| Stall | Post-plant, retake, or a push under way | Observed waiting: enemies stopped at the util's edge, or a plant/defuse attempt delayed, valued by time left |
| Spike | On or near the spike | Plant/defuse attempts that overlap the util, and their interruptions |
| Save | A teammate in a hostile sight line | A hostile sight line blocked, followed by the teammate surviving the window |
| Heal | A heal | See Healing |

These are associations. "Prevented a death", "avoided a stack" and "deterred a push" are not claimed; the tab
says what was observed ("sight line from the enemy Chamber to your Sage blocked for 3.1 s; Sage survived").

### Kill window

- Util that affects players: while the effect is active plus a grace, measured from the corpus (kill times on
  affected enemies after the effect ends).
- Smokes and walls: their whole lifetime, with fights inside it eligible (no fixed 4 s cut). A wall that also
  applies a status gets both windows.
- Placed devices: from the trigger. The **watch** (while armed, before the trigger) and the **trigger** are
  separate, and the watch ends when the trigger happens.

### Waste and harm

- **Waste** is decided last and only when nothing else applies: a device that held no lane any enemy could reach
  and never triggered; a flash that hit no enemy and was not followed up; util gone at once with no information.
  A device broken at once still gave information (where the breaker was) and is scored for that first.
- **Teammate harm**: flashes, nearsights and (once kept) statuses on teammates, weighted by what happened: a
  teammate in a fight, or who died while affected, counts heavily; one hit while safe counts little. Teammate
  damage counts less. Unknown effect durations are shown as unknown, never as harmless.
- **Ult points**: an ult's value against the points it cost, for value per ult point. **Credits**: nominal ability
  prices, labelled as nominal, shown and barely weighted.
- **Timing**: thrown before contact, at contact, or after; and whether a teammate peeked within the effect.

## No double counting: one outcome ledger per round

1. Each round has a **ledger** of outcome items, each with one budget in win-odds units: every kill, every
   plant/defuse/detonation, and each control change cut into disjoint area-and-time slices.
2. A cast is eligible for an item when the item falls inside one of its jobs' windows and evidence links them
   (the victim was affected; the slice is in the util's area; the sight line was blocked).
3. Each item's budget is split among its eligible casts by normalised evidence weights (the effect on the victim
   outranks presence in the area; the weights are part of the blended weighting). Gunplay gets a share too, so
   util never takes 100% of a kill. Within a cast, its share is split across its jobs.
4. A combo's value is the sum of its members' shares. It is shown as a combo **instead of**, not on top of, the
   members' own entries for those items.
5. A placed device: the watch earns only from slices before the trigger; the trigger earns only from items after
   it (new information, the fight it starts).
6. Negative items (a teammate dying while flashed by us, control lost) go through the same ledger with a
   negative sign.

A flash + dart + smoke onto one kill: the kill's budget is split between the flash (victim blinded), the dart
(victim revealed), the smoke (only if a sight line onto the killer was blocked) and the shooter, once.

## Healing

From heal runs and vitals, within one life of the healed player, counting only complete observations:

- **Life saved**: the healed player then took damage greater than their effective health before the heal (HP
  plus known shield, net of other heals in between) and lived. Worth far more than anything else here.
  Overlapping heals split the credit by amount.
- **Play opened**: compared with players on the same low HP who were not healed, did the healed player take a
  fight sooner, win it, or hold instead of falling back? A matched comparison, shown as such.
- **HP that mattered**: HP restored before a fight the player then took. HP topped up with no fight before the
  round ended is shown and worth little.

## Unused util

A death counts only when:

- the player engaged first: they dealt damage or had a clear sight line on the enemy before taking damage
  (Critical Moments' fights start at the first damage either way, so being ambushed is not enough);
- the charge timeline shows a charge held at the time;
- the held ability fits the fight (a flash before a peek, a smoke to back off under, a molly or stall on a plant
  or defuse), from a per-ability rule table;
- otherwise no claim is made.

Unused full ults count once per player per round (at death or round end, not both). Shown as its own line, never
as a cast.

## Combos

Two or more teammates' casts within a window (about 3 s, measured) on the same area or the same fight. Valued
through the ledger (above).

## Areas and lineups

An area is the callout's **super region** (`area_at` in `app/coach/areas.py` gives the nearest callout and its
`super`): A, B, C, Mid, Attacker Side, Defender Side. The pages show "own half" / "enemy half" by side. Grouped by
where the util takes effect (landing point; a wall's midpoint; a device's position). These are area patterns: the
same area does not prove the same lineup, and "enemies adapting" is shown as value by round number within a
match, not claimed as adaptation.

## Rankings and recommendations

- Per friend and ability, and per friend and lineup (ability x map x side x area).
- Shrinkage: each player's mean value per cast shrunk toward the ability's corpus mean, with the prior strength
  published on the page; the pooling order is player-lineup, then player-ability, then ability.
- Shown at 5 or more casts, always with the cast count **and the number of matches** they came from, plus an
  uncertainty interval.
- **A util score and a plain call** (owner, 2026-10-21): every friend x lineup with 5+ casts shows its score
  (shrunk value per cast against baseline) so people can judge it themselves, plus a call graded by the score:
  clearly positive = "keep throwing it", strongly positive = a stronger "this wins rounds, keep throwing it",
  around zero = "no clear value", negative = "this is costing you". Cast and match counts sit beside every
  call; shrinkage keeps a lucky handful of throws from earning a strong call. The grade cut-points are set from
  the corpus's spread of lineup scores.
- The blended weighting stays small: a few global weights; any area-specific weight needs shown support.

## 1. The Critical Util tab

In the replay viewer's side column, next to the existing Utility and Critical Moments tabs, for the picked
player:

- Their best and worst util of the match, each with its tags.
- Every cast, grouped by kind, with tags and value. Combos show their partners.
- Unused util and teammate harm, each its own section.
- Each row moves the viewer to about 2 s before the cast (or the device's trigger).
- When geometry or control is missing for the map, sight-based and control-based jobs say "unavailable".

Computed on the worker beside Critical Moments (the gaps block of the control task, with its own isolated
failure status like moments). Stored per round in a new table with a `UTIL_REVISION`. Its freshness covers the
code revision, the estimator version, the area mapping version and the control inputs it read. Backfilled for
every linked replay. Served as JSON at `/replays/{uuid}/util.json`.

## 2. The cross-match util page

For the viewer's friend circle (the friendship-derived circle helpers, grouped accounts included; never the crawl
roster):

- Which util wins rounds: ability x map x side, value against baseline, sample sizes and matches. Confounders
  shown, not hidden: economy tier, the state at the throw, and team strength (the opposing team's average Impact
  as a covariate where it matters).
- The util score and graded call per friend and lineup (see Rankings).
- Patterns, per friend and group:
  - Throw time within the round, before or after first contact.
  - Support rate: how often their util is followed by a teammate's play.
  - Waste and teammate harm rates.
  - Pre-plant vs post-plant util.
  - Full buy vs pistol vs every other round: pistol = rounds 1 and 13 (`PISTOL_ROUNDS` in
    `app/services/economy_graphs.py`); full buy = the team's start-round loadout at `FULL_BUY` in
    `econ_tier_name` (`app/scoring/impact.py`); everything else is "other".
  - Area patterns: how often the same ability is thrown into the same area, and its value by round within a
    match.
  - Combos: which pairs and triples on a team work.
  - Ults: when they are used, value per ult point, and which ults are worth charging.

## 3. Coach

- Cast facts are logged through the coach's trusted fact ids (as economy and score are today, `facts_id` in the
  brief), versioned with `UTIL_REVISION`, and the checker (`app/coach/checker.py`) gains typed checks for them.
- The coach's own flash detector (`app/coach/detectors.py` `_flashes`) is replaced by the shared util facts, so
  there is one source of truth.
- Facts say "measured follow-up kills"; whether the util "enabled" them is the coach's judgement, not the fact.
- In coached matches the coach adds: was this the right util for the moment, what unused util would have done,
  did it line up with the team's play.

## Scope and privacy

- Demo mode: the tab, the JSON, the page and the computation are all off (replay access already refuses demo
  mode, `replays_enabled()` in `app/services/replays.py`); the page checks it too.
- Opponents are "the enemy <agent>", never Riot names, scrubbed before serialisation.

## Checking it

It ships to the live site, the owner checks it there (best and worst casts, saved-life heals, keep/drop calls),
and rules and weights are fixed from what the owner finds. Fixes come from cases found wrong, never from tuning
toward expected rankings.

## Measurements to make before code

1. Estimator: held-out log loss, Brier and calibration against the man-advantage-only baseline; fight conversion.
2. Baseline overlap per ability.
3. Grace after each effect ends (kill-time distribution).
4. Combo window and area size.
5. Casts per friend x ability and per lineup, and how many matches they span.
6. Heals that save a life; deaths holding fitting util in fights the player engaged.
7. Casts per super region, to confirm the area split.

## Open questions (owner)

None.

Decided (owner, 2026-10-21):

- **Active info vs passive watch**: a placed device holding a lane is good; a device that gets broken or
  triggered earns more on top. Both are valued through the ledger with no fixed multiplier: the watch earns
  while armed, the trigger earns for what followed it.
- **Blended weighting**: trained on round win in held-out matches, weights constrained non-negative.
