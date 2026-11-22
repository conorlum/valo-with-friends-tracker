HANDOFF 14 — Post-plant play: where do players get the most impact and kills after the plant?

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Design spec in docs/superpowers/specs/ for my approval before code. Never on the demo. Opponents never named. Every figure with n and a CI. Companion to handoff 13 (plant heatmap); share its plant data and map payload code.

GOAL
After the spike is planted, where do attackers stand (and where do defenders retake from) when they get kills and win-chance swings? Per map and site: a heatmap of post-plant positions coloured by how much value players got from there. "On Haven C, players holding from long get 0.4 kills per post-plant and +9% win chance; players on site get 0.1."

DATA
- Replays: positions per tick (condensed blobs), deaths with killer and victim positions, sight (app/control/engine.py), Critical Moments fights (app/moments/fights.py), plant time and position.
- Win chance: app/services/util_model.py (P(attackers win | alive, spike, time)); per-kill swings the same way Critical Util's ledger does (app/services/util_values.py).
- Both teams in every replay count (strangers unnamed). Layouts via app/replays/layouts.py.

METHOD (declare before measuring)
1. Post-plant window: from the plant to the round end.
2. Position per player: where they were for each kill/death (killer's position at the kill), and where they spent the post-plant (time-weighted positions on a ~2 m grid, or named areas from callouts.json).
3. Value per position (attackers, and separately defenders retaking):
   - kills per post-plant from there, deaths per post-plant there;
   - win-chance change credited to kills from there (the util_model swing of each kill);
   - "time to die": how long a player survived holding there.
4. Relative to the plant spot: positions are best shown in the plant's frame (distance and direction from the spike) as well as on the map, since a long-range hold of one plant spot is a different spot for another plant.
5. Shrink each cell/area toward the site's mean (beta/normal prior); adjust for state (alive counts at the plant) like handoff 13.
6. Also measure: did the holder have sight on the spike (can they stop a defuse?), crossfires (two attackers seeing the spike from angles > 60° apart), playing off-site vs on-site, and the spread of attackers.

OUTPUT
- Per map/site page (shared with handoff 13's page as a second tab, "Post-plant"): heatmap of value per position, a toggle kills / win chance / survival, attack vs retake.
- Per player: their post-plant positions vs the high-value ones ("you hold on-site 70% of the time; long gets 3x the kills").
- Examples: each cell links to post-plant moments in the replay viewer.

WHERE IT GOES
Pure functions over stored rows, cached per map+layout; if it needs sight checks, compute on the worker next to Critical Moments with a revision bump. Add suites to tests/suites.toml; register new pages in site_health.PAGE_ROUTES.

FIRST STEP
Read-only: on the map with the most replays, list every post-plant kill with killer position, distance to the spike and sight on the spike, plus counts per site. Show a quick PNG of kill positions over the minimap.
