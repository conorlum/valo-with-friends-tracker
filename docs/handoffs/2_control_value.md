HANDOFF 2 — What matters to control: value per area of the map

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Write a design spec in docs/superpowers/specs/ and get my approval before any code. Never on the demo. Every figure shows its sample size.

GOAL
For each map, side and phase of the round, show which parts of the map are worth controlling: how much holding an area raises your side's round-win chance. Not individual off-angles. Think "on Ascent attack, holding mid top by 0:30 is worth +X%; A main control barely matters".

DATA WE HAVE
- Map control per round, per tick: cell states (held / contested / enemy / unknown) from app/control/engine.py, stored in replay_round_control (app/services/replay_control_store.py, replay_control_views.py).
- Named areas: app/static/data/callouts.json; reviewed paint app/static/data/control/<asset key>.areas.json (app/services/replay_areas.py). Use areas, not raw cells. Cells are too sparse.
- The round-win estimator: app/services/util_model.py (P(attackers win | alive, spike, time)). Fitted on matches without replays, so no leakage into replay rounds. Fight swing (docs/superpowers/specs/2026-10-23-fight-swing-design.md) already measures ground lost in fights; reuse its "held ground" reading.
- Layouts: compare only the same layout (app/replays/layouts.py).

METHOD (declare it in the spec before measuring)
1. Snapshot times: e.g. 15 / 30 / 45 / 60 s into the round, plus 10 / 25 s after the plant.
2. At each snapshot, per area: the share of the area held by each side (from the control ticks, averaged over 2 s like fight swing).
3. Outcome: the round result, and the change in win chance over the next 10-20 s from util_model (less noisy).
4. Value per area = the effect of holding it, controlling for the state the estimator already knows (alive counts, spike, time). Start with a residual model: outcome minus util_model's prediction, regressed on per-area control shares with strong shrinkage (ridge, or a hierarchical prior per area). Matching (similar state, area held vs not) as a cross-check.
5. Cross-fit by match (no round valued by a model that saw its own match). Bootstrap CIs by match. Mark "not enough data" plainly. We have few replays, so expect wide CIs and say so.
6. Separate correlation from cause in the text: holding an area may just mean you were already winning. Conditioning on alive counts helps; it won't fix everything.

OUTPUT
- A heatmap per map/side/phase: areas coloured by value (diverging scale, CI shown on hover), in the replay viewer's minimap style.
- "Your team vs the average": per map, where we usually hold vs where it matters. The useful finding is "we fight for X, which is worth little, and give up Y, which is worth a lot".
- Optional later: a per-round note in the viewer ("you gave up Mid at 0:31, worth -6%").

WHERE IT GOES
- A batch script first (scripts/control_value.py measure via with_friends_db.py --read-only). If it holds up, a stored build like Critical Util's (util_model row keyed by a corpus_key, rebuilt when inputs change, never built in a request).
- Page: a new /maps/<map>/control page or a section on /util. Any scheduler checks deploy_drain.paused. Add suites to tests/suites.toml.

OPEN QUESTIONS FOR ME
- Snapshot times and phases.
- Whether strangers' rounds in our replays count (both teams are in every replay, so yes by default: doubles the data).

FIRST STEP
A read-only measurement on the map with the most replays: per-area control shares at 30 s and their raw correlation with the round result. Print a table with counts before building any model.
