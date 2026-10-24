# Critical Util baseline rework: implementation plan

Plan: `docs/superpowers/plans/2026-10-23-util-baseline-plan.md` ("plan §n"). Revised after the P4 review
(run folder `IMPL-REVIEW.md`, G1-G14). Items in order; each is one commit `W<n>: ...` and one implementer
unless marked investigator / controller. Paths are under `webapp/`. Tests run from `webapp/` with the run's
Python, `-m pytest -n 0 -q -p no:cacheprovider`, `timeout: 600000`.

Rules for every item:
- Test-first (red, then green; the log says "red first"). Controller mutant specs where listed.
- "Pins" = the run's pin tests covering the item's paths; a red pin the item's change explains is
  re-recorded in the same item, with a test showing the old behaviour is what changed.
- **Readers keep working at every commit** (G1, G2): `util_values.KIND_ORDER` / `main_kind` and the row
  `kind` stay as they are (the tab's groups, the coach's `util_facts`, `replay_util`'s heal checks read
  them). New code goes beside old entry points; W18 switches `replay_util` over and deletes what nothing
  calls any more.

Common pin sets (plus each item's own):
- **P-extract**: `tests/critutil/test_extract.py`, `tests/critutil/test_unknown.py`, `tests/critutil/test_values.py`,
  `tests/critutil/test_util_web.py`, `tests/replays/test_replay_util.py`, `tests/replays/test_gaps_task.py`,
  `tests/coach/test_runner.py`, `tests/coach/test_brief.py`.
- **P-values**: `tests/critutil/test_values.py`, `tests/critutil/test_page.py`, `tests/critutil/test_util_web.py`,
  `tests/replays/test_replay_util.py`, `tests/coach/test_runner.py`, `tests/coach/test_brief.py`,
  `tests/coach/test_snapshot.py`.
- **P-site**: P-values plus `tests/replays/test_utility_fixtures.py`.

## W5 (investigator): pieces and code names (bounded: one blob in memory at a time, 30 replays, 45 min)

Read-only, 30 linked replays via `with_friends_db.py --read-only`; start from `tmp_fact_fields.json`. Output
`SIGNALS-A.md`: (1) for each raw-name piece of plan §2 (and `Zamboni_Rocket`, `Reclaim_Orb_*`): its parent
ability, its timing against the parent's rows (gap to throw / end), occurrences per parent cast, and whether it
is ever its own use, with a proposed piece -> parent table; (2) for every agent ability and ult: the
`code_name` keys seen. Done when: both parts have counts per piece / ability.

## W6 (investigator): window signals (bounded as W5)

Output `SIGNALS-B.md`: the row(s) and field(s) for a placeable's `armed` time (buy phase too); Omen From the
Shadows' arrival; the spike's dropped -> carried transitions and nearby players' positions; Gekko Thrash's
movement start and detain; Iso Kill Contract's duel end; Deadlock Annihilation's capture end; Chamber / Jett /
Neon ult end; Skye Seekers destroyed or expired; Raze's 9.5 s lead (one hold or a wrong drop). Counts found /
casts each; "not in the data" where so. Done when: every signal has an answer with counts.

## W7 (investigator): Leer, Dizzy, Viper cloud and pit in the raw export (plan §8)

Raw exports `%TEMP%\valo-replay\<uuid>\events.ndjson`, streamed line by line, one on disk and in memory at a
time; at most 3 per ability; exports it made itself (`scripts/export_replay.ps1` from the archive) are
deleted after reading; 60 min cap. Looks for a lethal hit on a Leer / Dizzy object, a destroyed or dormancy
close before its natural end (against `app/replays/extras.py` `raw.destroyed` ~244, ~496 and `Raw.effects`), and
Viper Poison Cloud / Viper's Pit on / off states (`wall_on`, extras.py ~1784, as the pattern). Output
`LEER.md`: per ability found / not derivable, the event and field, counts, a proposed condenser change
(file:line), and for any "found" a check on a locally re-condensed export (the archive's parser pin). Done
when: all four answered, each "found" with its re-condensed check.

## W8: ability table: loader, wiki parser, fetch script (plan §1)

- `app/critutil/abilities.py` (standard library): `load()` (cached), `lookup(code, name) -> entry | None`,
  `table_hash()` (all fields) and `extractor_hash()` (the fields the extractor reads: `match`, `pieces`,
  `kinds`, `type`).
- `scripts/fetch_util_durations.py`: `--fetch <dir>` saves `api.php` JSONs (one page at a time, 1 s apart),
  `--from <dir>` parses each ability's infobox duration, merges into `app/critutil/abilities.json` from the
  seed `app/critutil/abilities_seed.json`, prints the `|wiki - replay_s| > 1 s` list and the abilities with no
  wiki duration (`duration_s: null`, `provisional: true`).
- A pin test (G3): `extractor_hash()` equals a recorded value; its message says that a change to the
  extractor-facing fields needs the extractor `VERSION` and, once this branch has shipped, `GAPS_REVISION`
  bumped (stored rows are recomputed only by the gap run).
- Tests `tests/critutil/test_abilities.py`: fixture wikitext per infobox shape -> number; no duration -> null +
  provisional; lookup by `code_name`; hashes change with the file. Check: that file passes.

## W9: the seed and the table

- `abilities_seed.json` from `SIGNALS-A.md`, `constants.AGENT_KEYS` and plan §3's ult table: every
  `AGENT_KEYS` ability and every ult; types per plan §4 (Dizzy flash; Thrash / Steel Garden ult; Wingman Q;
  Fade Nightfall status, no info; Harbor Reckoning smoke + status); rules per plan §3; `replay_s` from
  `probes/ability_spans.txt`. The controller fetches the wiki pages into `<run folder>/wiki/` (curl, per
  PROMPT.md); the implementer runs `--from` on them.
- Test: every `AGENT_KEYS` ability and every ult has an entry; every type is one of the nine or `other`.
- Check: the test passes; the mismatch / missing lists are saved to `<run folder>/durations-cross-check.txt`.

## W10: extractor 1: table lookup, types, no 40 s cap (plan §2, §4)

- `extract.py`: `_kinds_of` (42) uses the table's `kinds` when an entry matches, else today's patterns; each
  cast gets `type`, `ability_id`; `_end` (96) drops `LIFE_CAP_S`: `t + duration_s`, placeables `t_decided`,
  else `t_decided` with `end_unknown: true`. `constants.VERSION` 2; `LIFE_CAP_S` removed.
- Tests (`tests/critutil/test_extract.py` toy-round pattern): Wingman not an ult; Thrash and Steel Garden ults;
  a row with no end uses the table's duration; a placeable runs to `t_decided`; unknown -> `end_unknown`;
  `kind_label` unchanged for one cast per old kind (G1).
- Pins: P-extract. Check: they pass. Mutant: an unknown ability gets the 40 s cap back.

## W11: extractor 2: grouping of pieces, flash and nearsight spans (plan §2)

- `build_casts` (extract.py 137-188): a piece in an entry's `pieces` joins its owner's latest cast of the
  parent by the gaps `SIGNALS-A.md` measured, across archetype letters; flash / nearsight casts take only rows
  ending by `pop.t` + 1 s (nearsight: its own row's end). Pieces found to be their own use stay casts.
- Tests: each piece case as a toy round (joins; a second charge stays separate); a flash and a nearsight with
  a long-lived row nearby keep a short span.
- Pins: P-extract. Check: they pass, and the controller re-runs `probes/probe_ability_spans.py` (read-only):
  no raw piece names left among cast names except own-use ones, flash medians under 5 s. Mutant: piece joins
  ignore the owner.

## W12: extractor 3: window signals (plan §2 "Signals")

- Each cast gets `signals` {landed, seen_end, pop, hits, effect_ends [[t1, known]], on, off, armed, ult_drop},
  plus the ult fields `SIGNALS-B.md` found. `seen_end` and `on` are read generically from row `gone` / `t1` /
  `on`, so W14's condenser fields need no extractor change (G11).
- Tests: one toy round per signal. Pins: P-extract. Check: they pass.

## W13: extractor 4: heal and Omen tags (R7, R8)

- Heal rows: `death`, `kept_alive` (damage taken after the heal > pre-heal HP), `hits_after` (gun hits after
  that point to death, from damage rows' `n`). Omen casts: `got_spike` (dropped -> carried within 15 s of
  arrival, Omen nearest at pickup), `got_spike_planted`.
- Tests: toy rounds for each tag, including the edges. Pins: P-extract. Check: they pass. Mutants: the 15 s
  limit dropped; nearest player not checked; `kept_alive` `>` -> `>=`.

## W14 (only if W7 found a signal): condenser `gone` / `on` (plan §8, R11)

- `app/replays/extras.py` per `LEER.md`; CONDENSE_REVISION stays 22 after
  `git show origin/main:webapp/app/replays/format.py` (if main reached 22: the next unused, tier 2 card).
- Tests in the `tests/replays/test_replay_extras.py` pattern.
- Pins: `test_replay_condense.py`, `test_replay_format.py`, `test_control_format.py`, `test_control_figures.py`,
  `test_control_pinned_inputs.py`, `test_utility_fixtures.py`, `test_replay_reparse_auto.py` (its 19 base
  failures stay deselected), `tests/critutil/test_extract.py`.
- Check: those pass (re-recording allowed only where the item's change explains it, logged).

## W15: `app/critutil/windows.py` (plan §3)

- `window(item, entry, t_decided) -> {"segments", "rule", "fallback"}` for every rule of plan §3; pure.
- Tests `tests/critutil/test_windows.py`: one per rule (KJ `off` subtracted, Toxic Screen `on`, cloud / pit
  fallback, flash with / without `pop`, Dizzy from status, Leer `known: false`, heal to death, Raze from
  `ult_drop`, Omen to pickup + 10, missing signal -> fallback, null duration -> no segments, cut at
  `t_decided`). Check: it passes. Mutants: +2 on status dropped; `off` not subtracted; 4 s flash cap dropped;
  no `t_decided` cut.

## W16: baseline 1: swing grid, types, windows, table at L (plan §5), beside the old code

- `util_values`: new `type_of(cast)` (the cast's `type`, else `other`; G1); `TYPES`; `swing_grid`;
  `moments_v2` yielding 45-vectors with "up" from `windows.window` for casts and heals; table `[n, sum_1..45]`
  (numpy); `cast_lift_v2` at the cast's L (first 45 s of on-time, levels at the first segment's start), own
  match out. Old `moments` / `add_moments` / `cast_lift` untouched. VERSION 2.
- Tests (`tests/critutil/test_values.py` 141-168 pattern): own L; on-time only; 45 s cap; heal window makes
  heal up; no segments -> no lift; own match left out; numpy table equals a plain-Python reference.
- Pins: P-values. Check: they pass. Mutant: own match kept in the cast baseline.

## W17: baseline 2: personal, per-device, gate (plan §5, §6)

- `util_values` (pure, new functions): per-player per-match sparse tables for a player set; per-device tables
  (map, side, rounds without that ability); `lift_v2(..., leave_out=matches)`; `gate(...)` ->
  `{all|yours|device: {L: {n, se_cell, se_ma, pass, diff_mean, diff_sd}}}` exact from per-match `[n, sum]`,
  per type too; same-type overlap share (F4).
- Tests: LOMO gate equals a brute-force recompute on a toy corpus (both levels); Yours leaves out every match
  given; device table excludes rounds with the ability; equal error fails.
- Pins: P-values. Check: they pass. Mutants: own match kept in the gate; `<` -> `<=`; lineup matches kept in Yours.

## W18: corpus build switched over (plan §5 "Storage", §6; `replay_util.py` 328-452)

- Pure `build_from_rounds(rounds, ctx, model, friend_players)`; `build_corpus` wraps it. Per cast `lift_all`,
  per-lineup `lift_yours`, `lift_device`, `L`, levels, gate flags, `type`; prior sums per ability per match per
  mode (cross-fitted); spread per mode from gate-passing cross-fitted scores; per replay round fingerprints.
  `corpus_key` adds `abilities.table_hash()`, extractor VERSION, friendship-player hash. `PAGE_VERSION` 2.
  Old util_values baseline functions deleted once nothing calls them.
- Tests: both modes on a toy corpus; prior excludes the lineup's matches; a gate-failing cast enters no score
  or spread; key changes with the table hash, the extractor VERSION and a new friendship.
- Pins: P-site. Check: they pass. Mutants: prior not cross-fitted; spread from all casts; table hash left out.

## W19: labels, names, `spike_got`

- Rows gain `label` (judgment's seven kinds or None; `kind` unchanged); `ability_label` uses the table's
  `ability` first; `spike_got` in `DEFAULT_WEIGHTS` and `features` (1 per tag, +1 if planted).
- Tests: `features()` gives 1 / 2; `label` per kind (walls None); Showstopper for `X_Rocket`.
- Pins: P-site. Check: they pass.

## W20: the tab (plan §5 "The tab", §7)

- `match_payload(db, replay, mode=None)` (622): lifts from the stored build by fingerprint; `ensure_values`
  when a current round is missing; payload carries `lift_all`, `lift_yours`, `label`, gate flags; `critutil.js`:
  Yours / All toggle (default Yours), fallback note, catch-all group, label shown; heal text "kept them alive" /
  "survived N more hits (to within one damage run)"; Omen "got the spike" (G5).
- Tests: `test_util_web.py` (payload both modes; a new replay triggers one rebuild; coach `util_facts` keeps
  `kind`); node tests in the `test_page.py::test_viewer_text_is_measured_and_signed` pattern for the toggle,
  heal and spike text.
- Pins: P-site. Check: they pass. Mutant: the payload built on the stored build itself, not a copy.

## W21: the page and `measure` (plan §6, §7)

- `page_for(corpus, circle, mode)`; `util_page` reads `?mode=`; toggle; per-mode calls and spread; "no
  personal comparison yet"; "baseline not validated for this window"; failing lengths listed; window copy
  replacing `base_window`. `scripts/critical_util.py measure` prints plan §6's tables.
- Tests: `test_page.py` (mode switch, hidden calls); `test_util_web.py` (`?mode=all`);
  `tests/critutil/test_measure_script.py` on a toy corpus.
- Pins: P-site. Check: they pass.

## W22 (controller + investigator): real-data measurement (plan §9)

Run-folder script (`probes/probe_r3.py` pattern): fits the estimator in memory (`util_model.fit`), extracts
the last 60 linked replays one blob at a time (rows kept, blob dropped; abort over 2 GB RSS), runs
`build_from_rounds` with the friendship players, saves the build as `<run folder>/build.json`, and writes
`MEASURE.md` (casts per type, windows per rule and fallbacks, gate per mode / L / type with diffs, Yours and
device coverage, F4 overlap, build seconds, peak RSS; teammate statuses, `gone` and cloud / pit `on` marked
"not in this data": CONDENSE 21). Sample check: at least 50% of those replays' rounds read. Re-measures
`replay_s`; a commit `W22: replay_s refreshed` updates `abilities_seed.json` / `abilities.json` (G12).
Done when: `MEASURE.md` written, the sample check passes.

## W23 (controller): previews and screenshots

Page and tab previews from `build.json` (the app's Jinja env; `critutil.js` with an embedded payload), shot
with `shot_page.py` in both modes; run folder only (names). The tab preview selects a replay with a cast whose
`lift_yours` is not None and differs from `lift_all` (G10). Done when: 4 PNGs exist and the printed text shows
at least one lift that differs between modes; if `build.json` has no such cast, the item says "the toggle
shows no difference on this data" with the counts (not counted as a pass on notice text).

## Cards (controller, G13)

- after W9: wiki vs replay mismatches + abilities with no wiki duration + Veto's ult (one grouped tier 2);
- after W19: the plan readings (plan "Key decisions") + `spike_got` weight + cloud / pit fallback (one grouped
  tier 2);
- after W22: anything the measurement shows the owner should judge (gate failures, F4 overlap, build time).

## Checkpoints (G14)

Full suite in the baseline's parts (serial, about 31 min; `--deselect` the 24 known), after W13, W17 and W21
and at wrap-up; the failure set compared with `baseline.xml`'s, not counts.
