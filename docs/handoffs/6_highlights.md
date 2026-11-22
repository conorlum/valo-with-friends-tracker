# Handoff 06: Clip export from the replay viewer and auto highlights

Read `docs/handoffs/README.md` (ground rules) and `CLAUDE.md` first. A short design spec for the owner's
approval, then build. Never on the demo (no replays there; make sure every new route 404s in demo mode).
Opponents are never named, including any text drawn into a clip: "the enemy Jett".

## Goal

1. **Export (first):** in the replay viewer, choose a section of a round and download it as an animated GIF and as
   MP4 or WebM, ready for Discord.
2. **Auto highlights:** find the match's best moments automatically and list them with one-click clips.

## What exists

- The viewer is `app/static/js/replay.js` (one large file, about 300 KB). A viewer object holds the canvas
  (`[data-replay-canvas]`), fits the view (`fitView`), and redraws with `this.draw()`; playback advances in a
  `requestAnimationFrame(this.tick)` loop. It draws players, yaw cones, util, the control layer and heatmaps
  (`paintHeatmap`), health bars and the spike over the minimap.
- The page takes `?round=n` and `?t=` (seconds into the round; `app/routers/replays.py`).
- Data for highlights: Critical Moments fights (`/replays/<uuid>/moments`, `app/static/js/moments.js`), fight
  swing tags, Critical Util values (`/replays/<uuid>/util.json`, `critutil.js`), the kill feed, clutch logic
  in `app/services/squad.py`, round-win chance (`app/services/util_model.py`).

## Part 1: export (all in the browser, no server work)

### UI

- An "Export clip" button opens a small panel: in and out handles on the timeline (default: current time −3 s to
  +7 s), speed (1x, 2x), output size (512 or 720 px wide), format (GIF, MP4/WebM), toggles for the control layer,
  names and health bars, and an optional caption (round, score, "1v3 clutch").
- Progress bar and Cancel. The result downloads; nothing is uploaded.
- Share link: "Copy link to this moment" produces `/replays/<uuid>?round=n&t=<in>` (and `&until=<out>` if the
  viewer learns to stop there; small change).

### Rendering

- Render frame by frame at a fixed step (20 fps for GIF, 30 fps for video), not in real time, so output is
  deterministic and smooth regardless of machine speed.
- That needs a function that draws the state at time t into a given canvas at a given size. Find how `draw()`
  reads the current time and the canvas; pull out `drawAt(t, ctx, size, options)` with as small a change as
  possible. Do not restructure the viewer beyond that.
- Draw the caption into the frame (top bar), with enemy names replaced by agent labels.

### Encoding

- **Video:** WebCodecs `VideoEncoder` where available (Chrome, Edge, recent Safari), muxed with a small muxer
  library (`mp4-muxer` or `webm-muxer`, an exact pinned version from jsdelivr or cdnjs). Fallback:
  `canvas.captureStream()` + `MediaRecorder` to WebM (real time; show that it will take the clip's length).
- **GIF:** a worker-based encoder (`gifenc` or `gif.js`, pinned version). Palette per clip, not per frame, to
  keep size down.
- **Size:** estimate size before export; Discord's free upload limit is small (check the current limit), so if
  the estimate is over it, offer lower fps or size automatically.
- Load encoder libraries lazily, only when the panel opens.

## Part 2: auto highlights

### Candidate finder (pure function, server side)

Input: one match's stored data (kill feed, rounds, moments, util rows, win-chance estimator). Output: a list of
`{round, t_start, t_end, kind, slot, score, summary}`.

Kinds:

- clutch won (1vN, N ≥ 2), with N in the score;
- ace; 4k; 3k within 10 s;
- an entry 2k (first two kills of the round within 5 s by one player);
- defuse with enemies alive (a "ninja" defuse when no enemy saw the defuser: use sight from moments);
- the match's top 3 fight swings (from the fight swing tag);
- the match's top 3 util plays by Critical Util value;
- the match's biggest single change in round-win chance (estimator before vs after an event).

Score per kind so the list can be sorted; window = a few seconds before the first event to 2 s after the last.

### UI

- A "Highlights" tab in the viewer's side column: each item jumps to its moment and has "Export clip" prefilled.
- A highlights strip on the match page.
- Later, with the owner's go-ahead: a weekly digest of the group's top highlights (a page first; Discord
  posting is a separate decision).

## Tests

- Pure tests for the candidate finder (clutch detection, multikill windows, scoring order).
- A browser journey (`tests/browser`, `--require-browser`) that opens a fixture replay, exports a 2 s clip in each
  format, and checks the file type and that it is non-empty.
- Add to `tests/suites.toml`; register any new route in `site_health.PAGE_ROUTES`.

## Phases and done criteria

1. Find the draw path in `replay.js` and report whether `drawAt(t, ctx, size, options)` can be pulled out cleanly.
2. GIF export end to end. Done when a 10 s clip exports and plays in Discord.
3. Video export with fallback.
4. Candidate finder and Highlights tab.

## Open questions for the owner

- Default clip length and size.
- Which highlight kinds matter most.
- Whether a digest page is wanted, and whether it should ever post to Discord.

## Pitfalls

- Fonts and images drawn into the canvas must be loaded before rendering frames, or frames come out blank.
- A tainted canvas (cross-origin images) cannot be read back: minimap images must come from the same origin.
- Keep the export code out of the main viewer bundle path so normal page loads do not get heavier.
