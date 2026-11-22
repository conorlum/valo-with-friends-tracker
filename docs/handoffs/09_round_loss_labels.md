# Handoff 09: Round loss labels: why did we lose this round?

Read `docs/handoffs/README.md` (ground rules, shared building blocks) and `CLAUDE.md` first. Design spec for
the owner's approval before code.

## Goal

Give every lost round (and every won round, with mirror labels) one main cause from a fixed list, so the team sees
"38% of our lost rounds on Bind start with an untraded opening death" and knows what to work on. Rule-based and
explainable, no AI: each label lists the facts that chose it and the key moment.

## Two tiers

- **Tier 1, tracker.gg data only** (every match): kill feed with times (`kill_events`), round outcome and end type
  (`rounds.outcome`, `planted`, `plant_time`, `defused`, `exploded`), loadouts and credits
  (`round_player_stats.loadout`, `remaining`, `round_player_spend`). Covers the whole history.
- **Tier 2, replays:** refines a label with positions and sight (Critical Moments fights, gaps/back-shots, util
  rows, and handoff 03's tradeable-death classes if built). Shown as "refined" where present.

## Reuse, do not rebuild

- Man-advantage states through a round: `app/services/state_replay.py` (the round replay fight-EV and the player
  pages use). Do not write a second kill-order engine.
- Round types (pistol, eco, force, full buy): reuse the existing classification (`force_buy_stats.py`,
  `eco_followup.py`, `economy_graphs.py`: find the shared one).
- Trades: the repo's trade rule (`TRADE_WINDOW_S` 6 s, the same as Impact's).
- Win chance: `app/services/util_model.py` (`p_attack`), for "thrown" and for the key moment.
- Surrender rounds: `state_replay.is_surrender_round`.

## Draft label list (agree with the owner before measuring; first matching rule wins)

Losses:

1. **Economy loss:** our round type is eco or save and theirs is a buy. Expected; shown separately from the rest.
2. **Untraded opening:** our first death was not traded within the trade window and we never got back to even.
3. **Thrown advantage:** we got the opening kill or a man advantage, our win chance peaked above a threshold
   (propose 65%), and we lost.
4. **Failed retake:** defence, spike planted, we had at least as many alive as the attackers at the plant.
5. **Lost post-plant:** attack, planted, we had at least as many alive as the defenders at the plant.
6. **Out of time:** attack, no plant, the round ended on time.
7. **Lost the clutch:** we ended in a 1vN (N ≥ 1) after being level or ahead.
8. **Picked off alone** (Tier 2 only): our death was far from every teammate (handoff 03's isolated class).
9. **Lost the trades:** none of the above; kills traded about evenly and we lost the fights.

Wins (mirrors): economy win (our eco beat their buy), opening converted, retake won, post-plant held, clutch won,
won the trades.

Also store **secondary labels** (all rules that matched), so a round that was both "untraded opening" and
"failed retake" can be counted under either for exploration, while the headline uses the first.

## Method

- A pure function: a round's facts in; `(label, secondary labels, reasons, key moment time)` out.
- Key moment: the event where the win chance moved most against us (or the event the rule names).
- Validate by hand: label 40 rounds the owner can check in the viewer (match, round, time list), fix the rules,
  then freeze them in the spec with a `LABELS_REVISION`.

## Output

- **Team view** (on `/squad`, or a new page): per map and side, share of losses per label, with n, over a chosen
  period; the change over time (last 20 matches vs before).
- **Player view:** which labels the player's deaths are part of ("you were the untraded opening death in 14 of our
  lost rounds"), and which they turned around (clutch wins, retakes won).
- **Match page / viewer:** a label chip per round linking to the key moment (`?round=n&t=`).
- **Coach:** the labels as facts with ids the checker trusts, later and only with the owner's go-ahead.
- Respect the friends circle (`app/services/circle.py`): "we" is the viewer's circle.

## Storage and pipeline

- A `round_labels` table (migration): round id, team, label, secondary labels, reasons, key moment time,
  `LABELS_REVISION`, tier. Filled at ingest after scoring (both tracker.gg ingest paths and the demo-match scripts
  call the same function), refined when a replay links. A backfill script for history.
- Aggregates through the stats cache patterns (`app/services/site_stats.py`), cleared by any ingest as those caches
  are.

## Tests

- One synthetic round per label, plus ordering tests (a round matching two rules gets the first). Revision bump
  recomputes. Add to `tests/suites.toml`; a Postgres-lane test for the table.

## Phases and done criteria

1. Read-only: draft rules over all matches, label counts per map and side, 40 sample rounds. Done when the owner has
   checked them.
2. Frozen rules, table, backfill. Done when every round has a label.
3. Pages. Done when the team and player views render, off on the demo.

## Open questions for the owner

- The label list and its order.
- The "thrown" threshold.
- Whether won rounds get labels in the first version.
