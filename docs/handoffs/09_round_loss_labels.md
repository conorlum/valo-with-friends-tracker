HANDOFF 9 — Round loss labels: why did we lose this round?

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named (app/services/enemy_names.py). Every figure shows n.

GOAL
Give every lost round (and, the same way, every won round) one main cause from a fixed list, so the team sees "38% of our lost rounds on Bind start with an untraded opening death" and knows what to practise. Rule-based and explainable, no AI: each label lists the facts that chose it.

TWO TIERS
- Tier 1, tracker.gg data only (every match): kill_events with times, rounds (planted, plant_time, outcome, end type), economy (round_player_spend, round type), round_player_stats. Covers the whole history.
- Tier 2, replays: refines a Tier 1 label with positions and sight (Critical Moments fights, app/moments; gaps/back-shots, app/gaps; util, replay_round_util). Shown as "refined" where present.

DRAFT LABEL LIST (agree it with me before measuring; order = priority, first match wins)
Losses:
1. Eco / save loss: our round type is eco or save and theirs is a buy (expected loss; separate from the rest).
2. Opening death, untraded: our first death not traded within the trade window (reuse the repo's trade rule: squad.py / fight swing CHAIN_S 6 s) and we never got even.
3. Opening won, thrown: we had the first kill or a man advantage and lost the round (where it flipped).
4. Failed retake (defence, spike planted, we had >= the attackers alive at the plant).
5. Lost post-plant (attack, planted, we had >= the defenders alive at the plant).
6. Time ran out (attack, no plant, round ended on time).
7. Lurk caught / picked alone (Tier 2: the death was far from every teammate; until then, Tier 1 cannot tell: leave it out).
8. Lost a clutch (we ended 1vN with N >= 1 after being level or ahead).
9. Outgunned 5v5 trade-off (none of the above: deaths traded evenly, we just lost the fights).
Wins get mirror labels (opening won and converted, retake won, post-plant held, eco won).

METHOD
- A pure function: round facts in, (label, reasons, key moment time) out. State tracking from app/services/state_replay.py (the same round replay fight-EV uses: man-advantage states with times); don't write a second kill-order engine.
- Win chance at each step from app/services/util_model.py where it helps (e.g. "thrown" = our win chance peaked above 65% and we lost).
- Validate by hand: label 40 rounds I can check in the viewer (round+time list), fix the rules, then freeze them in the spec.

OUTPUT
- Team view (/squad or a new /rounds page): per map and side, the share of losses per label, with n, over a chosen period; the change over time.
- Player view: which labels a player's deaths are part of most ("you were the untraded opening death in 14 of our lost rounds").
- Match page / replay viewer: a label chip on each round, linking to the key moment.
- Coach brief: the labels as facts with ids the checker can trust (later; ask me).

WHERE IT GOES
- Computed at ingest after scoring, or lazily and cached with the stats caches (site_stats.py patterns). New table round_labels (migration) keyed by round + a LABELS_REVISION; any ingest clears the cached aggregates like the stats caches do.
- Add suites to tests/suites.toml. Any background job checks deploy_drain.paused.

FIRST STEP
Read-only (scripts/with_friends_db.py --read-only): run the draft rules over all matches, print the label counts per map/side and 40 sample rounds (match, round, time) for me to check.
