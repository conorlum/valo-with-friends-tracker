HANDOFF 4 — Crosshair placement proxy and time-to-target from yaw

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Feasibility measurement first, then a design spec in docs/superpowers/specs/ for my approval, then code. Never on the demo. Opponents never named. Every figure shows its sample size.

GOAL
Two aim metrics per player from replays:
1. Crosshair placement proxy: when an enemy first becomes visible to the player, how far (in degrees) the player's facing was from the enemy.
2. Time to target: from that first sight to (a) facing within X degrees of the enemy and (b) the first shot or damage landed.
Track both over time and across matches.

DATA WE HAVE / CHECK FIRST
- Condensed replay frames have yaw per player (app/replays/condense.py: `yaw`, converted with yaw_to_map). Check: the sample rate (GRID_HZ), whether pitch exists anywhere in the parser export (movement.ndjson; app/replays/contract.py). If yaw only: we measure horizontal placement only. Say so on the page. Pitch (head height) can't be judged without it.
- Sight: the control engine raycasts each player's 103° view on the sight mask with smokes (app/control/engine.py). We need "enemy E entered player P's possible view (line of sight, ignoring facing)" vs "inside P's actual cone". Find or add a per-pair first-visibility event. Critical Moments already has "saw first" and "did you see your killer" (app/moments/fights.py). Reuse that.
- Shots and damage: shots are event ticks in the engine (SHOT_WINDOW_S); damage RPCs (contract.py RPC_DAMAGE).
- Fights: app/moments/fights.py defines fights. Measure per fight opening.

METHOD (declare before measuring)
- The event: the first moment of a fight (or of each new enemy appearing) where the line of sight between P and E is clear, P is alive and not flashed/stunned (status intervals in player_state).
- Placement error = |angle(P facing) - angle(P to E)| at that moment, 0-180°. Also at +100 ms and +250 ms.
- Time to on-target = first time the error < threshold (pick with me: ~5° at range, wider up close: use an angular width from the enemy's body width at that distance).
- Time to first shot, and to first damage on E.
- Split by distance band (gun_ranges.json), by who peeked whom (moving vs holding: speed over the last 0.5 s), by weapon class (guns.json), by whether P was pre-aiming a common angle.
- Exclude: P was turning fast before (flick vs held angle as its own category), E appeared behind P (> 90°: that's a gap / back-shot, app/gaps), smokes/spam.
- Validate: pick 20 events, check them by eye in the replay viewer (does facing match what the minimap shows?). Yaw lag or interpolation could ruin timings below ~100 ms. Measure the data's real time resolution first and don't report finer than it.

OUTPUT
- Player page: median placement error and median time to target (with IQR and n), by distance band, and a trend over matches.
- A friend comparison table, and "vs everyone in our replays" (strangers too, both teams are recorded, unnamed).
- Critical Moments: add the numbers to each fight ("facing 34° off when E appeared; on target at 0.21 s").
- Link: does a lower error predict winning the duel? (logistic, CI by match.) If not, say so; the metric would then be decoration.

WHERE IT GOES
Computed on the worker next to Critical Moments (same gap run, new fields, revision bump) or as a read-only script first. Add suites to tests/suites.toml; run the slow lane before merging gaps/control changes.

FIRST STEP
Read-only: report yaw's sample rate and smoothness, whether pitch exists, and placement error for every fight opening in 3 replays, with 10 events I can check in the viewer at given round+time.
