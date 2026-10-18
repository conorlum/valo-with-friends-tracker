# Critical Moments: implementation plan

Date: 2026-10-18. Design: `docs/superpowers/specs/2026-10-17-critical-moments-design.md`, including its "Build
amendments" section, which wins where it differs. Paths below are under `webapp/` unless they start with `/`.
Tests run from `webapp/` with the 3.13 venv (`.venv313`). "Pins" means: `tests/replays/test_gaps_store.py`,
`test_gaps_task.py`, `test_gaps_web.py`, `test_control_remote.py`, `test_replay_worker.py`,
`test_replay_worker_control.py`, `test_control_figures.py`, `test_control_pinned_inputs.py`,
`test_control_isolation.py`, `test_replay_reparse_auto.py`, `test_replay_match_states.py`,
`test_player_state_freshness.py` (all under `tests/replays/`).

## Shape

- `app/moments/__init__.py` (empty), `app/moments/constants.py` (standard library only: every threshold, the
  gun table, the ability groups, the trade constants), `app/moments/fights.py` (pure: imports only
  `app.control.{engine, utility, mollies}` and `app.moments.constants`).
- Entry point: `fights.round_moments(rnd, blob, *, sight: bool = True) -> dict` returning
  `{"v": 1, "sight": bool, "fights": [fight, ...]}` for all ten players. `rnd` is `engine.RoundInputs(blob, geo,
  link)` (pixels, `rnd.geo.m_per_px`, yaw from `rnd.pos`).
- One fight (JSON; slots only, never names):
  ```
  {"slot", "i" (index within the slot's fights), "t0", "t1", "opponents": [slots], "first": "player"|"opponent",
   "man": [own_alive, enemy_alive]  (just before t0),
   "kills": [{"victim", "t"}], "died": {"t", "killer"} | null,
   "trade": {"died": "traded"|"not_traded"|null, "by": slot|null, "after_s": float|null,
             "avenged": [{"victim", "teammate"}]},
   "moving": {"measured", "above", "first_mps", "tagged": bool, "gun_class"},
   "mates": {"state": "alone"|"with"|"none", "nearest_m", "nearest_slot", "with_slot"},
   "util_on": [{"by", "kind", "name", "on", "t", "dur"}], "util_near": [{"by", "name", "on", "m"}],
   "smoke_near": [...same...], "util_on_you": [{"by", "kind", "name", "t", "dur"}],
   "mollied": [{"by", "name", "t_in", "t_out"}],
   "duels": [{"opponent", "you_saw_t", "they_saw_t", "you_angle", "they_angle"}],
   "kill_detail": [kd], "death_detail": kd | null,
   "tags": [tag, ...]}
  kd = {"killer", "victim", "t", "measurable": bool, "killer_saw": bool, "victim_saw": bool, "wall": bool,
        "through_smoke": bool, "spot": t|null, "first_shot": t|null, "ttk_shot": s|null, "ttk_spot": s|null,
        "clean": bool, "reason": str|null, "angle": deg|null, "band": "on"|"near"|"off"|null}
  ```
  Tags: `moving`, `alone`, `with_teammate`, `no_teammate`, `died_traded`, `died_not_traded`, `traded_teammate`,
  `util_on_them`, `util_near_them`, `smoke_near`, `util_on_you`, `mollied`, `saw_first`, `seen_first`,
  `caught_off_angle`, `died_unseen`, `killed_unseen`, `through_smoke`. "Heard" and "Found by utility" are not
  fight tags: the site builds them from control's `knowledge_events` (W14).
- Times are rounded to 0.01 s, metres to 0.1, angles to whole degrees in the JSON.

## Ability groups (first values, `PROVISIONAL(D5)`; an unlisted name counts in no group)

- **deny** (utility near them): `4_TripWire`, `4_TripWire_SecondWire`, `MolotovFire`, `Q_Molotov_Production`,
  `AcidMolotov_NewMolotov`, `C_ExplodeyPatch`, `Reclaim_Orb_ExplodeyPatch`, `4_SlowField_Production`,
  `E_Slow_Large`, `4_ExplosiveBolt_Explosion`, `ConcussPulse`, `Concuss`, `C_TimeSlowGrenade_Explosion`,
  `E_Turret`, `E_Trap`, `Q_CageTrap`, `Q_Cage`, `Q_Explosion`, `Q_DecayExplosion`, `E_Explosion`,
  `4_BeeSwarm_Damage`, `Q_ElectricSphere`, `E_SweetSpotFissure`, `Q_SeizeTrap`, `E_AirStrikeMortar`,
  `X_DelayedBeam_Beam`, `4_FusionBlast`, `X_Shockwave`, `StealthingTrap_Flash_2`, `X_Bomb`,
  `X_OrbitalStrike_Production`, `Q_ShellShockGrenade`, `4_NetToss`, `4_RemovableNet`, `E_CableJam_Root`,
  `4_BlackHole`, `X_TidalWave`, `X_TidalWave_Chunk`, `X_FishingHook_Cage`, `E_SuppressionPulse`,
  `Q_FlameWallManager_Production`, `E_Wall_Segment_Fortifying`, `E_Wall_Fortifying`, `Wall`, `Q_WallManager`,
  `X_Segment`, `X_SegmentManager`, `X_GlobalWall`, `BarbedWire`, `WallTrap`, `Q_SoundSensor`,
  `Q_SoundSensor_Fissure`, `Q_Tether_SphereExpansion`, `4_SplashGrenade`.
- **smoke** (smoke near): `4_Smoke`, `NewSmoke`, `NewSmoke_PDS`, `E_Smoke`, `4_Smoke_ProductionNEW`,
  `4_SmokeZone`, `4_SmokeManager_Production`, `E_SmokeZone`, `E_SmokeZone_Fake`, `E_SmokeScreenManager`,
  `E_WorldSmoke`.
- **never**: everything else in the corpus (recon pawns and darts, cameras, heals, teleports, stims, decoys,
  shields, revives, markers). The full corpus name list with counts is in the run folder (`ability_names.txt`).
- Placed = `k == "ability"`, `kind in (Zone, Patch, GameObject, Pawn)`, with a position, alive at `t0`
  (`t <= t0 <= t1` or no `t1`), within `UTIL_NEAR_M` of an opponent at `t0`, from the player's side.

## Steps

Each step is one commit `W<n>: <goal>` and one implementer, unless marked controller. Each step's check is its
own new tests plus the tests named, all passing (yes/no). Full-suite checkpoints (baseline parts, known base
failures deselected): after W6, after W9, after W12, and at wrap-up.

### W4 — constants leaf
- Files: `app/moments/__init__.py`, `app/moments/constants.py`, `app/coach/detectors.py:9-12`,
  `tests/moments/test_constants.py` (new folder `tests/moments/` with `__init__.py` if the repo's test folders
  have one).
- `constants.py`: `TRADE_WINDOW_S = 6.0`, `TRADE_RANGE_M = 20.0`; `FIGHT_GAP_S = 3.0`; `MOVING_MPS = 2.5`,
  `SHOT_LEAD_S = 1.0` (shots from 1 s before t0), `DASH_EXCLUDE_S = 0.75`, `SPEED_MAX_SPAN_S = 0.2`;
  `UTIL_LOOKBACK_S = 3.0`, `UTIL_NEAR_M = 8.0`; `SIGHT_LOOK_S = 3.0`, `SIGHT_STEP_S = 1/16`;
  `SPOT_LOOKBACK_S = 10.0`, `SPOT_GAP_S = 0.25`, `RUN_GAP_S = 0.6`, `SHOT_AFTER_KILL_S = 0.05`,
  `SPOT_TOLERANCE_S = 0.05`, `CLEAN_SPOT_TO_SHOT_S = 1.0`, `CLEAN_MAX_MOVING = 1`, `GUN_DAMAGE_LOOKBACK_S = 10.0`;
  `CROSSHAIR_ON_DEG = 5`, `CROSSHAIR_NEAR_DEG = 20`; `MEDIAN_MIN_CLEAN = 5`; `INFO_MERGE_S = 2.0`;
  `GUN_CLASS` (copy of the survey's table: `/…/run folder/survey/survey.py:18-21`, pasted in the brief);
  `UNTAGGED_CLASSES = {"shotgun", "other"}`; `DENY_ABILITIES`, `SMOKE_ABILITIES` (frozensets, the lists above).
  One comment line per threshold with its corpus number from the spec. Each threshold and both ability sets carry
  `# PROVISIONAL(D4)` / `# PROVISIONAL(D5)`.
- `detectors.py` takes `TRADE_WINDOW_S` and `TRADE_RANGE_M` from the leaf, keeping its exported names
  unchanged (`detectors.TRADE_WINDOW_S`, `detectors.TRADE_RANGE_M` still resolve).
- Tests: the leaf imports only the standard library (AST check, pattern `tests/replays/test_control_isolation.py:37`);
  `constants.TRADE_WINDOW_S == float(app.scoring.impact.TRADE_WINDOW_SECONDS)`; detectors' names equal the leaf's.
- Check: `pytest tests/moments tests/coach/test_detectors.py` and every file `git grep -l "TRADE_WINDOW_S\|TRADE_RANGE_M" -- tests` names.

### W5 — fights: split, outcome, trades, teammates
- Files: `app/moments/fights.py`, `tests/moments/test_fights_core.py`, `tests/moments/helpers.py` (hand-built
  blob + fake geometry builder, following how `tests/replays/test_gaps_detect.py` builds `RoundInputs`).
- `round_moments` with: event list per slot (damage rows with `by`/`target` across sides, kill rows), `t` only,
  dropping `t > t_decided`; split at `> FIGHT_GAP_S`; opponents; `first`; `man` (alive counts at `t0 - 0.01`
  from `rnd.alive`); kills/died from kill rows only; trade (`died_traded` when the killer died to the player's
  teammate within `TRADE_WINDOW_S` of the death; `traded_teammate` when a victim of this fight had killed a
  teammate of the player within `TRADE_WINDOW_S` before), same rule as `app/coach/detectors.py` (read it, cite
  its line); mates (`none` when no teammate alive at `t0 - 0.01`; else nearest by `rnd.pos` distance x
  `m_per_px`; `with` when a teammate within `TRADE_RANGE_M` damaged an opponent of this fight in `[t0, t1]`; else
  `alone` when nearest > `TRADE_RANGE_M`; a teammate within range who did not fire is neither tag). Other fields
  present with empty/null values, filled by W6-W8.
- Tests (spec "Testing"): gaps of 2.9 s and 3.1 s; damage after `t_decided` dropped; a DB-only death is not a
  fight; a trade at the window's edge (6.0 inside, 6.01 outside); teammate at 19.9 and 20.1 m; no teammate alive.
- Check: `pytest tests/moments`.

### W6 — moving, utility on/near/smoke, utility on you, mollied
- Files: `app/moments/fights.py`, `tests/moments/test_fights_util.py`.
- Moving (spec 1 + amendments): shots `k == "shot"` by the slot in `[t0 - SHOT_LEAD_S, t1]`; speed from
  `rnd`'s 16 Hz track over the samples either side (span ≤ `SPEED_MAX_SPAN_S`), in m/s; a shot within
  `DASH_EXCLUDE_S` of the slot's `movement_casts` entry has no speed (no key: none excluded); `gun_class` the
  fight's most common class; tagged when `above >= 2`, `above * 2 >= measured`, and class not in
  `UNTAGGED_CLASSES`. Read the blob format in `app/replays/format.py:1-35` and `app/replays/extras.py:20-22`.
- Utility on them / on you (spec 4, 9): flash `hits` (`[slot, t, dur]` — check the format), `status`, `reveal`
  (overlapping `[t0 - 3, t1]`), `damage` with `src == "ability"`; from the player's side onto opponents, or
  from opponents onto the player.
- Near / smoke near: placed abilities as above, `DENY_ABILITIES` → `util_near`, `SMOKE_ABILITIES` →
  `smoke_near`.
- Mollied: zones from `mollies.molly_zones(util, figures)` (`app/control/mollies.py:155`; figures as
  `app/control/task.py` or the engine loads them — find it), enemy-owned; the player inside at the zone's start,
  alive, and outside before it ends, overlapping `[t0 - 3, t1]`.
- Tests: exactly 2.5 m/s (not above), a dash shot, a shotgun fight (not tagged, speeds shown), a flash hit
  before the window, a smoke near an enemy (smoke_near, not util_near), a molly on the player and one beside.
- Check: `pytest tests/moments`. Then the controller runs full-suite checkpoint 1.

### W7 — sight tags
- Files: `app/moments/fights.py`, `tests/moments/test_fights_sight.py`.
- For each kill (gun damage killer→victim within `GUN_DAMAGE_LOOKBACK_S`, else `measurable=false`, no sight):
  sample `utility._sees_point` at `SIGHT_STEP_S` from the first such gun damage minus `SIGHT_LOOK_S` to the kill,
  both ways (`sight_corpus.py` in the run folder's `survey/` is the reference; pasted lines in the brief).
  `wall` = any of those gun damage rows has `wall`. Neither saw: test the killer→victim line again with no
  smokes (`engine.los(geo, a, b, [])`) at the same samples; clear at some sample → `through_smoke` (and both
  `killer_saw`/`victim_saw` stay false so it also counts unseen); never clear and no wall → `measurable=false`,
  no sight tags. Tags: `died_unseen` on the victim's fight, `killed_unseen` on the killer's,
  `through_smoke` on both. With `sight=False`, none of this runs and every kd has `measurable=false`.
- Tests: killer never seen, through smoke, walls both ways (not measurable), wall-bang, sight off.
- Check: `pytest tests/moments`.

### W8 — spot, time to kill, clean, angles, saw first
- Files: `app/moments/fights.py`, `tests/moments/test_fights_ttk.py`.
- Spot: the start of the killer's last unbroken sighting (gaps ≤ `SPOT_GAP_S`) within `SPOT_LOOKBACK_S` before
  the kill. First shot: walk the killer's shots backward from the last at or before `kill + SHOT_AFTER_KILL_S`,
  breaking at a gap `> RUN_GAP_S`. `ttk_shot = kill - first_shot`, `ttk_spot = kill - spot`. Clean and its
  reason in the spec's order: never saw / no shots / fired before the spot (beyond `SPOT_TOLERANCE_S`) / first
  shot more than `CLEAN_SPOT_TO_SHOT_S` after the spot / moving (more than `CLEAN_MAX_MOVING` shots above
  `MOVING_MPS`). References: `survey/ttk.py:66-106`, `survey/crosshair.py:91-107` (pasted in the brief).
- Angle at the spot (spec 7a): horizontal angle between the viewer's yaw and the bearing to the other at the
  viewer's own spot; band `on` ≤ 5, `near` ≤ 20, else `off`.
- Duels per opponent over `[t0 - SIGHT_LOOK_S, t1]`: each side's first sight time and angle there. Tags
  `saw_first` / `seen_first` by the earlier first sight (none when neither saw); `caught_off_angle` when the
  opponent saw first and the player's angle at their own first sight was over `CROSSHAIR_NEAR_DEG`.
- Tests: first shot 0.99 s and 1.01 s after the spot; fired before the spot; a 0.25 s and 0.3 s sight gap; the
  angle bands at 5 and 20 degrees; caught off-angle.
- Check: `pytest tests/moments`.

### W9 — golden test on match ac9fd52a (Summit)
- Files: `tests/moments/fixtures/ac9fd52a_rounds.json` (rounds 9, 15, 33 only: blob with player names and
  ids removed or replaced by slot numbers, plus link `sides`/`db_deaths`), `tests/moments/test_golden.py`, a
  run-folder script that builds the fixture from `corpus/ac9fd52a-…/snapshot.json`.
- The controller first runs the module over those rounds and confirms by hand the spec's facts (round 33: the
  picked player's fight is `with_teammate`, teammate 2.0 m, both damaging the same opponent; round 15: `man`
  is `[3, 3]`; round 9 at 29.77 s: the player's kill is `killed_unseen`). The slots are found in the snapshot's
  `context.players` (names stay in the run folder).
- Test: builds `RoundInputs` with Summit's real geometry (as the control sight tests load it; skip if absent)
  and asserts those facts. A grep check that the fixture holds no player name from the snapshot.
- Check: `pytest tests/moments`; then full-suite checkpoint 2.

### W10 — corpus check (afk-investigator, no commit)
- A run-folder script runs `round_moments` over every round of every `corpus/*/snapshot.json`, one match per
  line appended to `corpus_check.jsonl` (resumable, a batch size argument, each batch under ~9 min, foreground).
  Geometry per map via `geometry.load_geometry` + `visibility` as `survey/sight_corpus.py` does.
- Compare against the spec: fights 20,622; moving share of fights (~11%); nearest-teammate percentiles
  (14.2/24.2/36.1); utility-on rows 4,611; sight table by map; clean 5,138 of 8,673 and the reason rows; the
  crosshair table. Write `CORPUS-CHECK.md` in the run folder: each number, spec vs build, and the cause of any
  difference beyond rounding (a definition change from the amendments is a cause; an unexplained one is a bug
  for the controller).
- Check: every corpus match has a line; `CORPUS-CHECK.md` written with every row.

### W11 — the worker computes moments
- Files: `app/control/task.py:271-338` (`_gaps`), `app/gaps/detect.py:46` and `app/services/replay_gaps.py:24`
  (`GAPS_REVISION` 3 → 4), `/replay_worker/server.py:1162` and `app/services/replay_control_remote.py:109`
  (`GAPS_PROTOCOL` 1 → 2), `replay_control_remote.py:339` (`_trusted_gaps`: an ok result needs a `moments`
  dict with `status` in ok/failed and, when ok, a `fights` list), `/replay_worker/Dockerfile:36-40, :47`
  (COPY `webapp/app/moments`, smoke import `app.moments.fights`), whatever `/replay_tasks/` builds its image
  from (grep; same change if separate).
- `_gaps`: after the rows, in its own try, `moments = {"status": "ok", "fights": ..., "error": None}` from
  `fights.round_moments(rnd, blob)` (`rnd` the same `RoundInputs`; sight on), else `{"status": "failed",
  "fights": [], "error": ...}`. A moments failure never fails the gap run. Failed gap runs also carry a
  `moments` failed dict.
- Pins: the gap fingerprint/revision pins and the protocol pins turn red; re-record them in this step, with the
  tests at `test_control_remote.py:541-543`, `test_replay_worker_control.py:464-472`, `test_control_isolation.py:57`
  updated to 2. Add a Dockerfile text assertion (pattern `test_replay_worker_control.py:341-361`). Add: a worker
  advertising protocol 1 gets no gaps work (existing behaviour, assert it still holds); a gaps result without
  `moments` is not trusted.
- Check: `pytest tests/moments` + every pin.

### W12 — storage
- Files: `alembic/versions/0026_replay_round_moments.py` (pattern `0016_replay_gaps.py`; down_revision `0025`),
  `app/models/replay.py` (`ReplayRoundMoments` beside `ReplayRoundGapRun` :153), `app/services/replay_gaps_store.py:62`
  (`store_gaps` takes the moments dict and upserts the row in the same transaction, only on the branch that
  writes the gap run), callers: `replay_control_remote.py` (`_keep_gaps` :353 passes `gaps["moments"]`),
  `scripts/compute_control.py:268-274`.
- Table `replay_round_moments`: `replay_id` + `round_number` primary key, FK to the replay round with
  `ondelete="CASCADE"` exactly as `replay_round_gap_runs` does, `status` text, `fingerprint` text,
  `gaps_revision` int, `fights` JSON (nullable), `error` text, `computed_at` timestamptz.
- Tests: a store writes the row; read back through a fresh session; a store returning "already current" writes
  nothing; deleting the round deletes the row (on the repo's Postgres test path if it has one, else SQLite as
  the existing gaps store tests do); mutants for "commit removed" and "row not written" (the controller's
  mutant run).
- Check: `pytest tests/moments` + pins; then full-suite checkpoint 3.

### W13 — migration up, down, up on local PostgreSQL 18 (controller)
- `pg_local.sh start` a throwaway cluster (initdb into the run folder, port 55432), `alembic upgrade head`,
  insert a replay round's gap run + moments row, `downgrade 0025`, `upgrade head`, count tables and rows each time.
  `DATABASE_URL` points at localhost only. Record the counts in `LOG.md`.
- Check: the same counts after both upgrades; the table gone after the downgrade.

### W14 — endpoint
- Files: `app/services/replay_moments.py` (new: load rows, join, per-player summary), `app/routers/replays.py`
  (`GET /replays/{match_uuid}/moments.json` beside gaps.json :326, `_control_replay_or_404` :290,
  `_json_with_etag` :303), `tests/replays/test_moments_web.py` (pattern `test_gaps_web.py`).
- Payload: `{"players": {slot: {"name", "side"}}, "rounds": {n: {"status", "fights"}}, "summary": {slot: {
  "expensive_deaths": [top 3 by control_m2: {round, t, control_m2, share, fight_i}], "medians": {band or
  "all": {"ttk_shot", "ttk_spot", "spot_to_shot", "n"}}  (clean kills only; `null` below MEDIAN_MIN_CLEAN),
  "info": {"heard": [{round, t}], "gunfire_outside": [...], "found_by_utility": [{round, t, reason}],
  "seen": count}}}, "stale": count of rounds whose moments are missing or not current}`.
  Deaths from `replay_control_views.load_round_summaries` (:47) / `_deaths` (:94); locating events from each
  round's control data header `knowledge_events` (`{A|B: [[t, enemy slot, reason]]}`, `app/control/encode.py:95`):
  the events in the enemy side's list naming the player's slot. Reasons: `footsteps` → heard; `gunfire` with
  `t` outside every one of the player's fights → gunfire_outside; anything not in {seen, damage, footsteps,
  kill, gunfire} → found_by_utility; `seen` counted. Same reason under `INFO_MERGE_S` apart → one moment.
  ETag over the moments rows (fingerprint, computed_at) and the summaries' etag.
- Tests: 404s like gaps.json; demo 404; payload shape on a seeded replay; medians null under 5; info merge at
  1.9 and 2.1 s; ETag changes when a row changes.
- Check: `pytest tests/replays/test_moments_web.py tests/replays/test_gaps_web.py tests/moments`.

### W15 — the tab
- Files: `app/templates/replays/_player.html:126-149` (tab after Analysis, gated `linked and control`),
  `app/static/js/moments.js` (new, loaded like the other viewer scripts in `replay.html:45-53`),
  `app/routers/replays.py:360` (`viewer_context` gets the viewer's slot from `get_current_player`
  (`services/auth.py:9`) and `page_context` players), CSS beside the gaps panel's.
- Behaviour: fetch `moments.json` when the tab is first opened; picker (default: viewer's slot, else lowest slot
  of the round-1 attackers); top: most expensive deaths, then one summary line (medians per band with counts,
  info counts); groups in the spec's order with counts, each row: round, clock time, opponents' names,
  outcome, the group's numbers; Heard and Found by utility from `summary.info`; a row click calls
  `window.viewer.showRound(round, max(0, t0 - 5))`; one line saying the tags are measurements, not verdicts.
  Stale rounds: a note "N rounds not computed yet".
- Tests: a Node test of the grouping/row text and the jump call (pattern `tests/replays/test_gaps_viewer.py`),
  a template test that the tab shows for a linked replay with control and not otherwise.
- Check: `pytest tests/replays/test_moments_web.py tests/replays/test_gaps_viewer.py` + the new tests.

### W16 — preview with real fights (controller + implementer for the script change)
- `with_friends_db.py --read-only` run of `scripts/export_replay_preview.py ac9fd52a-f661-4eac-aef0-04a8052d3b2d`
  into the run folder; a run-folder script computes every round's moments from the exported blobs with Summit's
  geometry and builds the W14 payload through `replay_moments`' pure functions (deaths from
  `control_players.json`, events from the exported control data); `scripts/render_replay_standalone.py`
  embeds `moments.json` as it does gaps (:73-74, :91-110, :160-163).
- Screenshots (`shot_page.py`, run folder): the tab for the owner's slot showing the groups, one fight row, and
  the viewer after clicking it (round and time changed). Pixel check: the after-click shot differs from the
  before shot.
- Check: three PNGs exist, the after-click screenshot's round label differs.

## Review amendments (P4 review, 2026-10-18; these win over the steps above)

- **Splits.** W6 becomes W6a (moving, dash, gun class) and W6b (utility on/near/smoke, on you, mollied). W14
  becomes W14a (pure functions in `app/services/replay_moments.py` + unit tests) and W14b (route, ETag, web
  tests). Checkpoint 1 moves to after W6b.
- **W5/W6 helpers:** `tests/moments/helpers.py` wraps `tests/replays/control_toys.py` (`toy_geometry`,
  `open_hall`, `door_hall`, `track`, `blob`, `toy_ability`, `uv`; :28-110) and only adds damage, shot, kill,
  cast, status, reveal and flash row builders.
- **W6a dash rule:** casts are `util` rows `{"k": "cast", "t", "by", ...}`; `movement_casts` is a flag (`1`,
  `app/replays/format.py:30-33`, `extras.py:51-55`). A shot within `DASH_EXCLUDE_S` of a cast row by the slot
  has no speed; a blob without the `movement_casts` key excludes none.
- **W6b formats:** flash `hits` durations can be null (format.py:15: `dur` null); `status.by` can be null
  (extras.py:23): such a row counts for no side. Nearsight rows with `hits` count as utility on (kind
  `nearsight`). Mollies: read `rnd.mollies` (`[(owner side group, Molly, nodes)]`, built at `engine.py:932-948`),
  never a second `molly_zones` call; "inside" uses the containment test `mollies.py` itself uses.
- **W7 no-smoke line:** `engine.los(geo, a, b, [])` also drops ability walls and map features (as
  `survey/smoke_split.py:39-40`), so "through smoke" means through smoke, an ability wall or a door; the
  constant's comment says so.
- **Geometry path.** W9 (golden) and W10 (corpus) use plain `geometry.load_geometry` + `visibility`, as the
  survey did. The worker uses `prepare_geometry` with features and heights (`task.py:278-280`;
  `smokes_at` includes map features, `engine.py:1008-1013`). W10 adds one row: a sample of 5 matches through
  the `prepare_geometry` path where the local assets allow it, with the difference in sight counts.
- **W11 third protocol:** `app/services/replay_upload.py:97` (`GAPS_PROTOCOL = 1`, checked in
  `worker_off_reason` :167-168) also goes to 2, or the site refuses every re-parse. Pins add
  `tests/replays/test_replay_upload.py`; a protocol-2 worker gets no off-reason (test). `/replay_tasks/` builds
  from the same `replay_worker/Dockerfile` (`render.yaml:84`; `COPY replay_tasks` at Dockerfile:44); the smoke
  import line is ~:46. The fake ok-result helper in `tests/replays/test_control_remote.py:96` (and any in
  `test_gaps_task.py`) returns an ok `moments` dict by default.
- **W12:** `store_gaps(..., *, moments)` keyword-only and required; both callers change in the same commit
  (`replay_control_remote.py:361`, `scripts/compute_control.py:274`). The stored `fights` JSON keeps
  `round_moments`' `v` and `sight`. The column is `gaps_revision` (the amendment), not the spec's
  `moments_revision`. The cascade test uses the opt-in Postgres fixture `pg` (`tests/replays/test_replay_store.py:387`,
  `tests/_postgres.py`, `writes=True`): SQLite doesn't cascade (`app/replays/store.py:74`).
- **W13** also deletes the replay round and shows the moments row count go to 0.
- **W14a inputs:** pure functions over `deaths = {n: {slot: [{"t", "control_m2", "share_of_team"}]}}` (the
  `player_tables` / `control_players.json` shape, `replay_control_views.py:106-111, :169-176`) and
  `events = {n: knowledge_events}`, so the endpoint and the W16 script feed the same signature. Each fight's
  `died` gets `control_m2` and `share` from the death of that slot with `|t - died.t| <= DEATH_MATCH_S` (0.1 s);
  `expensive_deaths` uses the same match for `fight_i`.
- **W14b reading `knowledge_events`:** they are in the header of `ReplayRoundControl.data` (gzip), not the
  summary. Load `data` with `load_only` in one query and read the header with `control_format.unpack_data`
  (`app/replays/control_format.py:165`); the test notes the time on a 24-round replay.
- **Fight JSON additions:** `mates.with_m` (the fighting teammate's distance); payload `players: {slot:
  {"name", "team"}}` plus `attack_round1: [slots]` for the picker default.
- **W15:** also `app/static/js/replay.js` (`showTab` :1520-1532 lazy-loads `moments` through a new
  `options.loadMoments`, supplied by the page as `loadGaps` is at `replay.html:45-53` and by the standalone
  script as at `render_replay_standalone.py:160-163`); the `<script>` tag beside `replay.html:11-13`, gated
  `control`; the panel div in `_player.html`. Rows carry `data-moment-round` / `data-moment-t`.
- **W16:** `scripts/export_replay_preview.py` also writes `N.link.json` (`replay_control.round_link(...)`,
  `app/services/replay_control.py:316`); events come from the exported `control.bin` via `unpack_data`. The
  standalone page takes `&tab=moments&slot=N` in its hash. The click is on a row in a round other than the one
  shown; the check reads the round label and clock with `--text` (round differs, clock ≈ t0 - 5). Pixels that
  move: the round label, the scrubber and the map's player dots. `shot_page.py` is
  `~/.claude/skills/afk/tools/shot_page.py`.

## Cards at wrap-up

D1 (departures), D4+D5 (thresholds and ability groups, with the corpus numbers and CORPUS-CHECK.md), the
migration (W13 counts), the self-starting recompute (55 stored linked replays locally; the planner line that
starts it, `replay_gaps.py:91-119`, plus the stale window from the amendments), the screenshots (W16).
