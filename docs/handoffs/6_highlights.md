HANDOFF 6 — Highlight clips: GIF/MP4 export from the replay viewer + auto-found highlights

Repo: conorlum/valo-with-friends-tracker (public). Read CLAUDE.md first. Short design spec in docs/superpowers/specs/ for my approval, then build. Never on the demo (demo replays don't exist there anyway; make sure the route 404s). Opponents never named: clips label enemies by agent (app/services/enemy_names.py), including any text drawn on the clip.

GOAL
Part 1 (first): in the replay viewer, select a section of a round (start/end, or "the 10 s around this moment") and download it as an animated GIF and MP4 / WebM, ready to drop into Discord.
Part 2: find highlights automatically (clutches, aces, 3k+, fastest multikills, ninja defuses, the biggest fight swings, the biggest util plays) and list them with one-click clips.

WHAT EXISTS
- The viewer: app/static/js/replay.js (one ~300 KB file) draws a round on a <canvas> over the minimap: players, yaw cones, util, control layer, health bars, spike. Templates app/templates/replays/replay.html, _player.html.
- Critical Moments (app/static/js/moments.js, /replays/<uuid>/moments): fights with times. Fight swing tags. Critical Util (critutil.js): util values. Round data: kills, clutches in round_player_stats / squad.py's clutch logic.

PART 1 — EXPORT (client-side, no server load)
- Add an "Export clip" control: in/out handles on the timeline (default: the current time -3 s / +7 s), speed (1x, 2x), size (e.g. 512 or 720 px), toggles for the control layer / names / health bars, and a small caption (round, score, "the enemy Jett").
- Render frame by frame off-screen: step the viewer's draw function at fixed dt (e.g. 20 fps for GIF, 30 for video) instead of real time, so the output is smooth and deterministic. Refactor replay.js only as needed to call one "draw at time t into this canvas" function.
- MP4/WebM: canvas.captureStream + MediaRecorder is real-time; better use WebCodecs (VideoEncoder) where available with a small muxer (webm-muxer / mp4-muxer from cdnjs/jsdelivr, pinned version), and fall back to MediaRecorder.
- GIF: gif.js or gifenc (pinned version, worker-based). Keep GIFs under Discord's free limit: show the estimated size, and lower fps/size automatically if needed.
- Show progress; allow cancel. No upload; the file downloads.
- Shareable link too: /replays/<uuid>?round=N&t=12.3&until=22.0 opens the viewer at that section (check what URL params already exist).

PART 2 — AUTO HIGHLIGHTS
- A pure function over a match's stored data: candidates with (round, t_start, t_end, kind, player, score). Kinds: clutch won (1vN, N>=2), ace, 3k/4k within 10 s, entry 2k, defuse with enemies alive, fight swing top 3, Critical Util top 3, the biggest win-chance swing in the match (util_model).
- A "Highlights" tab in the viewer and a per-match strip on the match page; each item jumps the viewer there and offers "Export clip".
- Later (ask me): a weekly digest of the group's top highlights.

TESTS
Pure-function tests for the candidate finder (add to tests/suites.toml); a browser journey for the export (tests/browser, --require-browser) that exports a 2 s clip and checks the file's size/type.

FIRST STEP
Find where replay.js draws a frame and how the playback clock works; report whether a "draw at t into canvas" function can be pulled out cleanly, then build Part 1 behind a small UI on one branch.
