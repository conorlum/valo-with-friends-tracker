# Critical Util: the no-util baseline rework (plan)

Date: 2026-10-23. Branch `afk/2026-10-23-util-baseline`, on `worktree-critical-util` (5928faac). Spec:
`docs/superpowers/specs/2026-10-21-critical-util-design.md` ("Built", "Baseline"). Every rule below was
decided by the owner (2026-10-22/23); this plan only says where each one lives. `Rn` = the run's register.

## Goal

Make a cast's comparison with "the same moment without that util" honest:

1. each cast is compared over **its own window** (the ability's real duration plus a grace, or the rule its
   type has), not a flat 10 s;
2. comparisons are grouped by **nine types**, not the current kinds;
3. the comparison is **validated** before any call is shown (leave-one-match-out, per window length and mode);
4. a **personal** comparison ("Yours", the player's own util-free moments, cross-fitted) beside "All players",
   with a toggle on the page and the tab;
5. the extractor's casts are right first (grouping of raw-name pieces, kinds, flash spans, durations from the
   wiki instead of a 40 s cap, Killjoy `off` / Viper `on` spans);
6. two extras (R10): Reyna Leer / Gekko Dizzy shot down, mined from the raw export, and a per-device
   comparison against rounds where that device was never placed.

## Out of scope

No new pages. No change to the ledger, the job weights' fitting, the estimator, Critical Moments, control or
the coach's flash detector. No prod writes, no migration (prod has no `replay_round_util` yet; migration 0029
ships with this branch). No change to how the gap run is scheduled.

## Architecture

```
wiki (api.php) --scripts/fetch_util_durations.py--> app/critutil/abilities.json (committed)
                                                         |
blob --app/critutil/extract.py (VERSION 2)--> casts with `type`, `window` [[t0, t1], ...], tags
                                                         |  (worker, gap run; stored in replay_round_util)
app/services/util_values.py (VERSION 2)  -- swing grid per round, moment table by type x level x length,
                                            personal and per-device tables, gate, cast lifts (both modes)
app/services/replay_util.py (PAGE_VERSION 2) -- build_corpus (pure core `build_from_rounds`), page_for(mode),
                                            match_payload(mode lifts from the corpus build)
page (util/page.html, ?mode=yours|all)  /  tab (critutil.js toggle)  /  scripts/critical_util.py measure
```

### 1. The ability table: `app/critutil/abilities.json` (R5, R9; the extractor's kinds and names)

One committed JSON the worker and the site both read (`app/critutil` ships in the worker image; the file is
data, so `constants.py`'s "standard library only" rule holds: `json` + `pathlib`). One entry per ability:

```
{"match": "<regex on code_name>", "agent": "Phoenix", "ability": "Blaze", "slot": "C|Q|E|X",
 "type": "wall", "kinds": ["smoke", "wall"], "rule": "duration", "duration_s": 8.0,
 "wiki": {"page": "Blaze", "field": "duration", "value": "8", "fetched": "2026-10-23"},
 "replay_s": 10.7}
```

- `duration_s` comes only from the wiki (R5). `scripts/fetch_util_durations.py` reads each ability page through
  `api.php?action=parse&page=<Ability>&prop=wikitext&format=json`, parses the infobox's duration fields, and
  writes the table; `--from <dir>` reads saved JSONs (the run saves them under its run folder, and tests use a
  fixture). An ability the wiki gives no duration for has `duration_s: null` and `"provisional": true`, a
  grouped tier 2 entry; never a stand-in (PROMPT hard rule).
- `replay_s`: the replay-measured median span (the scoping probe `ability_spans.txt`, re-measured after the
  extractor fixes). The fetch script prints every ability where `|wiki - replay| > 1 s`; that list is one
  grouped tier 2 card with both values (R5). The wiki value is used.
- `type` is one of the nine (below); `kinds` replaces the pattern guesses where an entry exists (fixes
  Wingman = Gekko Q, Thrash and Steel Garden = ults; `ability` gives "Showstopper" for `X_Rocket`).
- `rule` names the window rule (section 3). `slot` X marks an ult.
- The site's `ability_label` uses the table's `ability` first, then the viewer's ABILITY_STYLES as today.

### 2. Extractor fixes: `app/critutil/extract.py`, `constants.VERSION` 1 -> 2

- **Grouping of raw-name pieces.** Each piece INPUT lists (Raze `4_Projectile_*`, Phoenix
  `4_Molotov_Production`, Killjoy `4_RemoteBees_MultiDetonate`, Clove `DecayNade`, Cypher
  `E_CameraTrackingDart`, Gekko `E_OrbSpawner` / `E_DiscTurret_PowerWave` / `Zamboni_Rocket`, Viper
  `4_SmokeGrenade` / `Q_AcidGrenade`, Sage `4_SlowFIeld_Production`, Sova `4_ExplosiveBolt` / `Q_RevealBolt`,
  Waylay `E_RewindTime_ForObservers`, Neon `4_GroundStrike`, KAY/O `Q_SemtexBasic`, Chamber `4_Trap_Dart`,
  Fade `E_Divebomb`, Raze `Q_Satchel_Arming`) is checked on real rows first ("check before assuming a bug"),
  then joined to its cast by a table of piece -> parent ability (`abilities.json` `"pieces": [...]`), matched
  by owner and time (the parent's life + MERGE_S, or `thrown.t0`). A piece that really is its own use (a
  Raze ult's rocket, a sub-projectile of a second charge) stays its own cast, with the evidence in the item's
  log. Pieces whose archetype letter differs from the parent's (`4_` vs `Q_`) are why today's `c.key` match
  misses them.
- **Flash spans.** Flash casts' median span is 23.8 s and 290 of 632 sit at the 40 s cap: a long-lived row
  (not the flash's own projectile) is merged in through `FLASH_ROW_MATCH_S`. Fix: a flash cast takes only
  its projectile / source rows that end by `pop` + 1 s; any other row starts its own cast or joins its own
  ability. Flash rows carry `pop` (checked in P2); the window rule uses it.
- **Never-seen ends.** `LIFE_CAP_S` goes: a row with no `t1` / `gone` ends at `t + duration_s` from the table
  (placeables: at `t_decided`, R4); with no table entry, at `t_decided` and the cast is flagged
  `end_unknown` (counted in `measure`).
- **Killjoy `off` and Viper `on`** (R6): each cast gets `window` segments (section 3); a device with an `off`
  span is on only outside it; a Viper wall / cloud / pit is on only inside its `on` spans. Off its whole life:
  `window: []`, so no lift (credit still shown).
- **Kinds and type** from the table (section 1); `kinds` keeps today's set for evidence code that reads it.

### 3. Windows: computed in the extractor, per cast (R4, R6, R7, R8)

Each cast gets `type` and `window: [[t0, t1], ...]` (cut at `t_decided`), plus `window_rule` (the rule's
name) and, where a rule could not find its signal, `window_fallback: true`. The worker has every row the rules
need (pops, effects, on/off spans, deaths, the spike timeline, ult points); the site only reads the segments.
A heal row gets the same two fields.

| Type / rule | Window (owner, INPUT "Windows") | Read from |
| --- | --- | --- |
| smoke, wall, molly, other util with a duration | throw -> landed + `duration_s` + 1 s (seen end + 1 s when the row ends first) | table, row `t1`/`gone` |
| Sage Barrier Orb, Deadlock Barrier Mesh | 40 s | table rule `fixed:40` |
| flash (incl. Skye, KAY/O) | throw -> `pop` + the longest blind it caused, at most 4 s (no hit: `pop` + 1 s) | flash row `pop`, `hits` |
| on-hit status | until the last affected enemy's status ends + 2 s; no hit: `duration_s` + 1 s | `effects` t1 |
| recon (darts, drones, Fade eye) | 10 s from the throw | rule `fixed:10` |
| placeable (trips, cams, turrets, alarmbots, sensors, clones, anything armed) | armed (buy phase: 0 s, the barrier drop) -> gone / broken / picked up, else `t_decided`; KJ: minus `off` spans | row `t`, `gone`, `t1`, `off` |
| Viper wall / cloud / pit | `on` spans only | row `on` |
| heal, Sage Resurrection, Clove Not Dead Yet | heal / revive -> that player's death, else `t_decided` | heal row, kills |
| ults | INPUT's ult table, row by row (each a named rule in `abilities.json`) | see below |

Ult rules needing a signal the agent must find (R8 and the register's "agent decides"; one
`afk-investigator` item maps each to blob rows on real data before they are built, and cites them): Raze
Showstopper starts at the ult-points drop to 0 (probe: 103 of 103 rockets, 1.41-9.96 s before); Omen From the
Shadows' arrival; Gekko Thrash's start of movement and detain; Iso Kill Contract's duel outcome; Deadlock
Annihilation's capture end; Chamber / Jett / Neon "until the ult ends or death"; Skye Seekers' destroyed or
expired. A rule whose signal is missing in a round falls back to `duration_s` + 1 s with `window_fallback`.
Veto's ult (not in the sample): `duration_s` + 1 s from the wiki, provisional (needs-owner, grouped card).
The ult table's kind notes go into `abilities.json` too: Fade Nightfall is status only (lines, not a location
reveal: no `info` kind, no recon credit), Harbor Reckoning is smoke plus nearsight / slow (not a concuss), Gekko
Thrash and Vyse Steel Garden are ults with status (Thrash today mis-kinded status, Steel Garden wall).

- **Omen "got the spike"** (R8): the spike goes dropped -> carried within 15 s of arrival and Omen is the
  nearest player to it at the pickup (the spike timeline `player_state.spike`; the picker is not named):
  tag `got_spike`, window runs to pickup + 10 s, `got_spike_planted` if the spike is planted later that round.
  A new job feature `spike_got` (1 per tag, +1 if planted) with a starting weight in `DEFAULT_WEIGHTS`
  (tier 2, grouped card).
- **Heal tags** (R7): `kept_alive` when the damage the healed player took after the heal exceeds their
  pre-heal HP, and `hits_after`: gun hits taken after that point, up to their death (damage rows: `n` hits per
  run; precision is one damage run, and the tag says so). The credit's `saved_share` stays as built.

### 4. Nine types (R9)

`flash, smoke, wall, molly, status, recon, placeable, heal, ult` (`util_values.TYPES`), replacing
`KIND_ORDER` for grouping comparisons. A cast's type is its table entry's `type`; an ult is always `ult`
(a placeable ult too). A cast that fits none (mobility, decoys, buffs) is type `other` and compared within
`other`, as today (plan reading, grouped card). User-facing labels keep the seven kinds of `judgment.md`
(damaging, info, smokes, teleports, heals, status, blinds): `kind_label` maps a cast's kinds to one of them, or
none (walls, decoys, shields, buffs), and the page and tab show that.

### 5. The baseline: `app/services/util_values.py`, VERSION 1 -> 2 (R1)

- **Swing grid.** Per round and side, the side's round-win chance on a whole-second grid from 0 to
  `t_decided` (one `p_side` call per second per side, not per moment per length). A moment at second `t`
  has swings `P[t+L] - P[t]` for `L = 1..45` (cut at `t_decided`), a 45-vector. Moments stay every 2 s.
- **A cast's swing** is the sum over its window segments of `P[end] - P[start]` (interpolated on the grid);
  its length `L` is the segments' total, rounded to the second, clamped to 1..45.
- **Up.** A moment counts as util-free for type X when no cast of type X by that side has a window segment
  covering it (today: `t <= x < t_end`).
- **Table.** `{type: {level key: matrix [n, sum_1..45, sumsq_1..45]}}`, built with numpy from the moments'
  swing matrix, grouped by key (no per-length Python loop). Levels, the 30-moment minimum and the fallback
  are kept as built. The cast's baseline is the cell's mean at its `L`, with its own match taken out.
- **Prior, cross-fitted.** An ability's prior for a lineup is the mean lift of that ability's casts outside
  the lineup's own matches (sums per ability per match are stored; the page subtracts the lineup's matches).
  K = 5, minimum 5 casts, as built.
- **Personal (Yours).** The same table per player (the alive players of that side at each moment), only for
  players in at least one friendship (the page and tab show friends only); a cast's personal lift uses the
  thrower's own table, own match out, through the same levels and 30 minimum. Replaces the
  `player|kind|side` table.
- **Per-device comparison** (R10): for each placeable ability, a table of moments from that map and side in
  rounds where that ability was never placed by that side, same levels and minimum, at the device's `L`;
  gives `lift_device` beside the main lift. It is shown for placeables; calls stay on the main lift (plan
  reading, grouped card).
- **Storage.** The tables are build-time only: the stored build keeps each cast's `lift` for both modes
  (and `lift_device`), the level, `n`, `L`, and per replay the round fingerprints it used; the tab reads a
  cast's lifts from it when the round's fingerprint matches, else shows "no comparison yet" for that round. The
  stored row stays small (no 45-length tables in the DB).

### 6. The validation gate (R2)

Inside the build, leave-one-match-out over util-free moments: for each mode (All players, Yours) and each
length `L = 1..45`, the squared error of predicting a held-out moment's swing by its baseline cell mean (the
level a cast would use, own match out) against by the man-advantage-only mean (own match out). Computed from
per-match per-cell `[n, sum, sumsq]` (no refit per match). Pass = strictly lower error. Also for the
per-device comparison. Stored with the build: `gate[mode][L] = {n, se_cell, se_ma, pass}`.

- A cast at a failing `(mode, L)` keeps its lift, marked `gate: false`; it does not enter a lineup score or a
  call. A lineup whose passing compared casts are under 5 shows "baseline not validated for this window" in
  place of a call (plan reading of "failing cells hide calls with the reason shown", grouped card).
- `scripts/critical_util.py measure` prints the gate table, per mode and `L`.

### 7. Page and tab (R3)

- **Page** `/util?mode=yours|all` (default yours): lineup scores, calls and spread per mode. With no personal
  comparison for a lineup: "no personal comparison yet". The gate's failing lengths are listed under the table.
- **Tab** (`critutil.js`, `/replays/<uuid>/util.json`): each cast carries `lift_all`, `lift_yours`, levels and
  gate flags; a Yours / All players toggle, default Yours; a cast with no personal comparison shows All
  players and says so.
- Labels: section 4's seven kinds; copy is tier 1.

### 8. Leer and Dizzy shot down (R10, R11)

An `afk-investigator` mines one raw export at a time (`%TEMP%\valo-replay\<uuid>\events.ndjson`) for Reyna
Leer and Gekko Dizzy actors: a lethal hit on the object, a destroyed state, or a close well before its natural
end, matched against what `raw.destroyed` (`app/replays/extras.py` ~244/496) already reads. If a signal is
found: the condenser writes `gone` on those rows (CONDENSE_REVISION stays 22, unshipped; R11 -- checked
against `origin/main` at the time), the pinned tests are re-recorded, and Leer / Dizzy windows end at `gone`.
If none is found: it is reported as not derivable (with what was looked at), and their windows use the
status rule with `duration_s` + 1 s.

### 9. Measurement on real data (read-only)

A run-folder script (like the scoping probe `probe_r3.py`) extracts every linked replay's rounds in memory
from the friends DB (`with_friends_db.py --read-only`), fits the estimator in memory, runs the pure build
(`replay_util.build_from_rounds`) and reports: casts per type, overlap per level, the gate per mode and `L`,
personal coverage, per-device coverage, the flash span and `end_unknown` counts, build time. Page and tab
screenshots come from standalone previews rendered from that build (the friends DB has no util rows), kept in
the run folder (they hold player names: never committed).

## Rule -> home

| INPUT rule | Home |
| --- | --- |
| Comparison set, fallback, 30 minimum, level shown, "no comparison yet" (R1) | util_values `baseline` (kept), section 5 |
| Compare by type; rankings per ability | section 4, replay_util lineups keyed by ability (kept) |
| Calls on lift shrunk to prior, K=5, min 5 | replay_util `lineup_score` (kept) |
| Prior with the lineup's matches left out | section 5 "Prior, cross-fitted" |
| Gate, LOMO, per L and mode, strict, hidden calls with reason, `measure` (R2) | section 6 |
| Yours / All toggle, page and tab, fallback, thresholds per mode (R3) | sections 5, 7 |
| Wiki durations, replay cross-check card (R5) | section 1 |
| Windows per type, ults table (R4) | section 3 |
| KJ off / Viper on (R6) | sections 2, 3 |
| Heal tags, survived N hits (R7) | section 3 |
| Omen got the spike, Raze voice line (R8) | section 3 |
| Whole-second baseline lengths to 45 s | section 5 |
| Nine types, placeable ult -> ult | section 4 |
| Extractor: flash spans, LIFE_CAP_S, KJ/Viper, grouping, kinds, VERSION 2 | section 2 |
| Leer / Dizzy mining (R10, R11) | section 8 |
| Per-device comparison under the gate (R10) | sections 5, 6 |
| Veto ult needs-owner | section 3, grouped card |

## Key decisions (agent's, logged)

- **Windows in the extractor, not the site**: every signal the rules need is in the blob, which only the worker
  reads; the site gets segments. Cost: a later window change needs the gap run again (GAPS_REVISION 5 is
  unshipped, so this pass needs no bump). Tier 1.
- **Tables not stored**: per-cast lifts in the build instead of 45-length tables (size: ~10 types x ~1000 keys
  x 91 numbers, plus per player). Tier 1.
- **Personal tables for friendship players only**: what the pages show; bounds the build. Tier 1.
- Plan readings a reasonable person could read otherwise go on one grouped tier 2 card: the type `other`;
  walls before smokes in type order; a flash's "+ at most 4 s" as the longest blind capped at 4 (no hit: 1 s);
  gate-failing casts kept but out of scores; the per-device lift shown, not driving calls; the Omen weight;
  the cap at 45 s for long heal / placeable windows.

## Risks

- **Signals missing for some ult rules** (Thrash movement, Iso's duel): fall back per cast, counted, never
  invented. The investigator item finds out before the code.
- **Leer / Dizzy may not be derivable**: a valid outcome (section 8).
- **Build time**: 45 lengths x per-player tables. Mitigated by the swing grid and numpy grouping; measured in
  section 9 (budget: under 60 s for the linked corpus).
- **Pinned tests**: extractor VERSION, util_values VERSION and (if R11 applies) CONDENSE pins move; each item
  re-records the pins it moves (memory "revision bump pinned tests").
- **Thin data**: ~60 replays; many `(mode, L)` cells may fail the gate. That is the gate doing its job; the page
  says so.
- **Personal data**: previews and measurements hold player names; they stay in the run folder.
