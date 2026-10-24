# Critical Util baseline rework: implementation plan

Plan: `docs/superpowers/plans/2026-10-23-util-baseline-plan.md` (sections cited as "plan §n"). Items in order;
each is one commit `W<n>: ...` and one implementer unless marked investigator / controller. Paths are under
`webapp/`. Test commands run from `webapp/` with the run's Python, `-m pytest -n 0 -q -p no:cacheprovider`.
"Pins" = the run's pin tests that cover the item's paths (memory "revision bump pinned tests"): a red pin that
the item's change explains is re-recorded in the same item, with a test that the old behaviour is what changed.

Every implementation item is test-first (red, then green; its log says "red first"). Mutant specs are written
by the controller where listed.

## W5 (investigator): piece grouping and window signals, on real data

Read-only, 30 linked replays (friends DB, `with_friends_db.py --read-only`), output `SIGNALS.md` in the run
folder. Uses `tmp_fact_fields.json` (every row type, agent and name with its fields) as the start.
1. For each raw-name piece of plan §2 (and `Zamboni_Rocket`, `Reclaim_Orb_*`): which ability it belongs to,
   its timing against the parent's rows (gap to the parent's throw / end), how often it occurs per parent
   cast, and whether it is ever its own use. Propose the piece -> parent table.
2. The row(s) and field(s) that give: placeable `armed` time (and a buy-phase placement's); Omen From the
   Shadows' arrival; the spike's dropped -> carried transitions and positions near it (`player_state.spike`,
   player positions); Gekko Thrash's movement start and detain; Iso Kill Contract's duel end; Deadlock
   Annihilation's capture end; Chamber / Jett / Neon ult end; Skye Seekers destroyed or expired; Raze's 9.5 s
   lead (one hold or a wrong drop?). Each with counts found / casts, and "not in the data" where so.
3. For every agent ability: the `code_name` keys seen (to build `abilities.json` matchers).
Done when: `SIGNALS.md` answers 1-3 with counts per item.

## W6 (investigator): Leer, Dizzy, Viper cloud and pit in the raw export (plan §8, R10, R11)

Read-only on raw exports (`%TEMP%\valo-replay\<uuid>\events.ndjson`, one at a time, streamed; more with
`scripts/export_replay.ps1` from the archive if fewer than 3 have a Reyna / Gekko / Viper). Looks for: a lethal
hit on a Leer / Dizzy object, a destroyed or dormancy close before its natural end, matched against
`app/replays/extras.py` `raw.destroyed` (~244, ~496) and `Raw.effects`; Viper Poison Cloud / Viper's Pit
on/off states (the Toxic Screen path `wall_on`, extras.py ~1784, as the pattern). Output `LEER.md`: per
ability, found / not found, the event and field, counts, and a proposed condenser change (file:line) if found.
Done when: `LEER.md` says found or not derivable for each of the four, with what was looked at.

## W7: the ability table and its wiki fetch (plan §1, R5)

- New `scripts/fetch_util_durations.py`: `--from <dir>` reads saved `api.php` JSONs, `--fetch <dir>` saves
  them first (urllib, one page at a time, 1 s apart); parses each ability's infobox duration field(s);
  writes `app/critutil/abilities.json` merged with a hand-kept seed (`app/critutil/abilities_seed.json`:
  matcher, agent, ability, slot, type, kinds, rule, pieces, wiki page name, `replay_s`). Prints the
  `|wiki - replay_s| > 1 s` list and every ability with no wiki duration.
- The seed comes from W5's `SIGNALS.md` §1 and §3, `constants.AGENT_KEYS` and the plan §3 ult table. Every
  agent in `AGENT_KEYS` plus every ult; types per plan §4 (Dizzy flash; Thrash / Steel Garden ult; Wingman Q).
- `app/critutil/abilities.py` (standard library): `load()` (cached), `lookup(code, name) -> entry | None`,
  `table_hash()`.
- The run saves the wiki JSONs under `<run folder>/wiki/` (curl per PROMPT.md) and runs `--from` on them.
- Tests: `tests/critutil/test_abilities.py`: a fixture wikitext per infobox shape parses to its number; a page
  with no duration gives `null` + provisional; lookup by `code_name`; every `AGENT_KEYS` ability has an entry;
  the hash changes with the file.
- Check: `pytest tests/critutil/test_abilities.py` passes; `abilities.json` has an entry for every
  `AGENT_KEYS` ability and every ult; the script's mismatch / missing lists are saved to the run folder.

## W8: extractor, part 1: table lookup, types, kinds, no 40 s cap (plan §2, §4)

- `app/critutil/extract.py`: `_kinds_of` (42) uses the table's `kinds` when an entry matches, else today's
  patterns; each cast gets `type`, `ability_id`; `_end` (96) drops `LIFE_CAP_S`: `t + duration_s`, placeables
  `t_decided`, else `t_decided` with `end_unknown: true`. `constants.VERSION` 2; `LIFE_CAP_S` removed.
- Tests (`tests/critutil/test_extract.py`, pattern at its existing toy-round tests): Wingman not an ult;
  Thrash and Steel Garden ults; `X_Rocket` labelled Showstopper; a row with no end uses the table's duration;
  a placeable runs to `t_decided`; unknown ability -> `end_unknown`.
- Pins: `tests/critutil/test_extract.py`, `tests/critutil/test_util_web.py`, `tests/replays/test_replay_util.py`,
  `tests/coach/test_runner.py`.
- Check: those four files pass.

## W9: extractor, part 2: grouping of pieces, flash and nearsight spans (plan §2)

- `build_casts` (extract.py 137-188): a piece in an entry's `pieces` joins its parent's latest cast by owner
  and time (W5's measured gaps), across archetype letters; flash / nearsight casts take only rows ending by
  `pop.t` + 1 s (nearsight: its own row's end). Pieces W5 found to be their own use stay casts.
- Tests: each W5 piece case as a toy round (joins; a second charge stays separate); a flash with a long-lived
  row nearby keeps a short span; a nearsight likewise.
- Pins: as W8. Check: those files pass, and the controller's real-data re-run of
  `probes/probe_ability_spans.py` shows no raw piece names among cast names except W5's own-use ones and
  flash medians under 5 s.

## W10: extractor, part 3: window signals and tags (plan §2 "Signals", §3)

- Each cast gets `signals` {landed, seen_end, pop, hits, effect_ends[[t1, known]], on, off, armed, ult_drop,
  and W5's ult fields}; heal rows get `death` (the target's death time or None), `kept_alive`, `hits_after`
  (R7); Omen casts get `got_spike`, `got_spike_planted` (R8: within 15 s of arrival, nearest player at pickup).
- Tests: one toy round per signal and per tag, from W5's row shapes.
- Pins: as W8. Check: those files pass.

## W11 (only if W6 found a signal): condenser `gone` / `on` (plan §8, R11)

- `app/replays/extras.py` per `LEER.md`; CONDENSE_REVISION stays 22 after checking
  `git show origin/main:webapp/app/replays/format.py` (if main reached 22: the next unused, tier 2 card).
- Tests in `tests/replays/test_replay_extras.py` / `test_replay_condense.py` pattern.
- Pins: `test_replay_condense.py`, `test_replay_format.py`, `test_control_format.py`,
  `test_control_figures.py`, `test_control_pinned_inputs.py`, `test_utility_fixtures.py`,
  `test_replay_reparse_auto.py` (its 19 base failures stay deselected), `tests/critutil/test_extract.py`.
- Check: those pass; control-side readers unchanged (`test_control_pinned_inputs.py` green without re-record,
  since only util rows change).

## W12: `app/critutil/windows.py` (plan §3)

- `window(item, entry, t_decided) -> {"segments", "rule", "fallback"}` for every rule in plan §3's table and
  each ult rule; pure, standard library.
- Tests `tests/critutil/test_windows.py`: one per rule (incl. KJ `off` subtracted, Toxic Screen `on`, cloud /
  pit fallback, flash with / without `pop`, Dizzy from status, Leer `known: false`, heal to death, Raze from
  `ult_drop`, Omen to pickup + 10, missing signal -> fallback, null duration -> no segments, cut at
  `t_decided`).
- Check: `pytest tests/critutil/test_windows.py`; controller mutants (`mutants-W12.json`): drop the +2 on
  status, drop the `off` subtraction, drop the 4 s flash cap, drop the `t_decided` cut.

## W13: baseline, part 1: swing grid, types, windows, table at L (plan §5)

- `app/services/util_values.py` 301-447: `TYPES` replaces `KIND_ORDER` / `main_kind` (keep `main_kind` as the
  type lookup for callers); `swing_grid(round, model)`; `moments` yields 45-vectors and reads segments from
  `windows.window` for casts and heals (up check, F11); table `[n, sum_1..45]` with numpy; `cast_lift` at the
  cast's L (first 45 s of on-time, levels at the first segment's start, F3); heal rows get lifts. VERSION 2.
- Tests (`tests/critutil/test_values.py`, pattern 141-168): a cast compares at its own L; a segment window
  sums only on-time; the first 45 s cap; a heal window makes "heal" up; a cast with no segments has no lift;
  numpy table equals a plain-Python reference on a toy corpus.
- Pins: `tests/critutil/test_values.py`, `test_page.py`, `test_util_web.py`, `tests/coach/test_runner.py`.
- Check: those pass.

## W14: baseline, part 2: personal, per-device, gate (plan §5, §6)

- In `util_values` (pure): per-player per-match sparse tables for a given player set; per-device tables (map,
  side, ability absent from the round); `lift_for(...)` with a set of matches left out; `gate(tables) ->
  {all|yours|device: {L: {n, se_cell, se_ma, pass, diff_mean, diff_sd}}}` exact from per-match `[n, sum]`;
  per-type gate too; same-type overlap share (F4).
- Tests: LOMO gate equals a brute-force refit on a toy corpus (both levels); Yours leaves out every match given;
  device table excludes rounds with the ability; a strictly-equal error fails the gate.
- Pins: as W13. Check: those pass; mutants `mutants-W14.json`: own match not removed in the gate; `<` -> `<=`;
  lineup matches not removed in Yours.

## W15: corpus build (plan §5 "Storage", §6, §7; `replay_util.py` 328-452)

- Pure `build_from_rounds(rounds, ctx, model, friend_players)`; `build_corpus` wraps it. Per cast: `lift_all`,
  per-lineup `lift_yours`, `lift_device`, `L`, levels, gate flags, `type`, `label`; cross-fitted prior sums per
  ability per match per mode; spread per mode from gate-passing cross-fitted scores; per replay round
  fingerprints. `corpus_key` adds `abilities.table_hash()`, extractor VERSION and the friendship-player hash.
  `PAGE_VERSION` 2. `ability_label` uses the table first. `spike_got` in `DEFAULT_WEIGHTS` / `features`.
- Tests (`tests/critutil/test_page.py` pattern + `test_util_web.py` fixtures): build on a toy corpus gives both
  modes; prior excludes the lineup's matches; a gate-failing cast enters no score; key changes with the table
  hash and with a new friendship.
- Pins: `test_page.py`, `test_util_web.py`, `test_replay_util.py`, `tests/coach/test_runner.py`,
  `tests/coach/test_brief.py`.
- Check: those pass; mutants `mutants-W15.json`: prior not cross-fitted; spread from all casts; table hash
  left out of the key.

## W16: the tab (plan §5 "The tab", §7)

- `match_payload(db, replay, mode=None)` (622): lifts from the stored build by fingerprint; `ensure_values` when
  a current round is missing; payload carries `lift_all`, `lift_yours`, `label`, gate flags, `personal`
  availability; `critutil.js`: Yours / All toggle (default Yours), fallback note, catch-all kind group, label.
- Tests: `test_util_web.py` (route payload has both modes; a new replay triggers one rebuild; the coach's
  `util_facts` still gets `kind`); `test_page.py`'s node test (`test_viewer_text_is_measured_and_signed`
  pattern) for the toggle text.
- Pins: `test_util_web.py`, `test_replay_util.py`, `tests/coach/test_runner.py`, `tests/coach/test_brief.py`,
  `tests/coach/test_snapshot.py`. Check: those pass. Mutant: payload built from a copy of the stored build (a
  caller's change must not change the stored data): "input not copied".

## W17: the page and `measure` (plan §6, §7)

- `page_for(corpus, circle, mode)`; `util_page` reads `?mode=`; template toggle, per-mode calls and spread,
  "no personal comparison yet", "baseline not validated for this window", failing lengths listed, window
  copy replacing `base_window`. `scripts/critical_util.py measure` prints plan §6's tables (gate per mode, L
  and type with paired diff and sd; same-type overlap; coverage; end_unknown; fallback counts).
- Tests: `test_page.py` (mode switch, hidden calls), `test_util_web.py` (page route with `?mode=all`), a
  `measure` test on a toy corpus (`tests/critutil/test_measure_script.py`).
- Pins: as W16. Check: those pass.

## W18 (controller + investigator): real-data measurement (plan §9)

A run-folder script (`probes/probe_r3.py` pattern) extracts the last 60 linked replays in memory, runs
`build_from_rounds`, and writes `MEASURE.md`: casts per type, windows per rule (fallback counts), gate per
mode / L / type, Yours coverage, device coverage, F4 overlap, build seconds and peak RSS. Sample check: the
build reads at least 50% of the linked replays' rounds (CONDENSE 21 blobs; plan §9's caveats). Also re-runs
the span probe for `replay_s` and the wiki-vs-replay list for the D-card.
Done when: `MEASURE.md` written with those figures and the sample check passes.

## W19 (controller): previews and screenshots

Standalone page and tab previews rendered from W18's build (Jinja env of the app; `critutil.js` with an
embedded payload), shot with `shot_page.py` in both modes; kept in the run folder only (they hold names).
Done when: 4 PNGs exist (page Yours / All, tab Yours / All) and the toggle changes at least one shown value
(counted from the printed text).

## Checkpoints

Full suite (serial, baseline parts, `--deselect` the 24 known) after W10, W14 and W17, and at wrap-up.
