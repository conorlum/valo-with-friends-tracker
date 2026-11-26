"""The viewer's Highlights tab: highlights.js render(), the player partial's tab and panel, replay.html's wiring,
and replay.js's showTab / playFrom / exportClip handlers. The JS checks need Node; they skip without it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WEBAPP = HERE.parents[1]

JS_DIR = WEBAPP / "app" / "static" / "js"
HIGHLIGHTS_JS = JS_DIR / "highlights.js"
REPLAY_JS = JS_DIR / "replay.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def item(id_, round_=2, t0=30.0, t1=42.0, player="Alpha", summary="won a 1v3 clutch", **kw):
    base = {"id": id_, "round": round_, "slot": 0, "kind": "clutch", "rung": 1, "t_start": t0, "t_end": t1,
            "events": [], "segments": [[t0, t1]], "metric": 3, "speed_s": None, "summary": summary, "tags": [],
            "player": player, "agent": "Omen", "score": [4, 5]}
    base.update(kw)
    return base


def payload(**kw):
    base = {"potg": item("potg", summary="the play of the game"),
            "players": {
                "0": {"name": "Alpha", "agent": "Omen", "best": item("a1", player="Alpha", summary="alpha best"),
                      "fallback": "kill", "more": [item("a2", summary="alpha more one"),
                                                   item("a3", summary="alpha more two"),
                                                   item("a4", summary="alpha more three")]},
                "1": {"name": "Bravo", "agent": "Sova", "best": None, "fallback": None, "more": []}},
            "highlights": [], "missing": [], "clip_names": {}, "home_team": "Red", "uuid": "u"}
    base.update(kw)
    return base


FAKE_DOM = """
  global.document = {createElement: tag => {
    const e = {tag, children: [], listeners: {}, attrs: {}, className: "", textContent: "",
      setAttribute(k, v) {this.attrs[k] = v;}, appendChild(c) {this.children.push(c); return c;},
      addEventListener(type, fn) {this.listeners[type] = fn;}};
    return e;
  }};
  function texts(node) {
    return [node.textContent || "", ...node.children.flatMap(texts)].filter(t => t !== "");
  }
  function find(node, pred) {
    if (pred(node)) return node;
    for (const c of node.children) { const f = find(c, pred); if (f) return f; }
    return null;
  }
"""

READ_STDIN = """
  let input = ""; process.stdin.on("data", d => input += d).on("end", () => {
    const payload = JSON.parse(input);
    Promise.resolve(run(payload)).then(out => process.stdout.write(JSON.stringify(out)),
      e => { console.error(e); process.exit(1); });
  });
"""


def run_node(body: str, data=None, *modules):
    prelude = "const H = require(%s);\n" % json.dumps(str(HIGHLIGHTS_JS))
    completed = subprocess.run([NODE, "-e", prelude + FAKE_DOM + body + READ_STDIN], input=json.dumps(data),
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_render_orders_potg_then_players_and_labels_fallbacks():
    got = run_node("""function run(p) {
      const panel = document.createElement("div");
      H.render(panel, p, {jump() {}, exportClip() {}});
      return texts(panel);
    }""", payload())
    joined = "\n".join(got)
    assert joined.index("Play of the Game") < joined.index("the play of the game") < joined.index("Alpha (Omen)") \
        < joined.index("alpha best") < joined.index("Bravo (Sova)")
    assert "best kill" in joined and "best util" not in joined
    assert "No highlight this match" in joined
    # up to two "more" items
    assert "alpha more one" in joined and "alpha more two" in joined and "alpha more three" not in joined
    assert "Fight swings not available yet" not in joined


@needs_node
def test_render_util_fallback_note_and_missing_note():
    p = payload(missing=["fight"])
    p["players"]["0"]["fallback"] = "util"
    got = run_node("""function run(p) {
      const panel = document.createElement("div");
      H.render(panel, p, {jump() {}, exportClip() {}});
      return texts(panel);
    }""", p)
    joined = "\n".join(got)
    assert "best util" in joined and "best kill" not in joined
    assert "Team-fight swings not available yet" in joined


@needs_node
@pytest.mark.parametrize("missing, notes", [
    (["util"], ["Util plays not available yet"]),
    (["winchance"], ["Win-chance swings not available yet"]),
    (["fight", "util"], ["Team-fight swings not available yet", "Util plays not available yet"]),
    ([], []),
    (["unknown"], []),
    (["fight", "fight"], ["Team-fight swings not available yet"]),
])
def test_missing_notes_describe_each_missing_kind(missing, notes):
    got = run_node("""function run(p) {
      const panel = document.createElement("div");
      H.render(panel, p, {});
      return texts(panel).filter(t => t.includes("not available yet"));
    }""", payload(missing=missing))
    assert got == notes


@needs_node
def test_render_item_shows_round_player_summary_and_duration():
    got = run_node("""function run(p) {
      const panel = document.createElement("div");
      H.render(panel, p, {jump() {}, exportClip() {}});
      return texts(panel);
    }""", payload())
    assert "R2" in got and "Alpha the play of the game" in got and "12 s" in got
    assert "Play" in got and "Export clip" in got


@needs_node
def test_render_without_potg_has_no_potg_block():
    got = run_node("""function run(p) {
      const panel = document.createElement("div");
      H.render(panel, p, {jump() {}, exportClip() {}});
      return texts(panel);
    }""", payload(potg=None))
    assert "Play of the Game" not in "\n".join(got)


@needs_node
def test_buttons_call_the_handlers_with_the_item():
    got = run_node("""function run(p) {
      const panel = document.createElement("div"), calls = [];
      H.render(panel, p, {jump: i => calls.push(["jump", i.id]), exportClip: i => calls.push(["export", i.id])});
      const jump = find(panel, n => n.attrs["data-highlight-jump"] === "a1");
      const exp = find(panel, n => n.attrs["data-highlight-export"] === "a1");
      jump.listeners.click(); exp.listeners.click();
      const potgExport = find(panel, n => n.attrs["data-highlight-export"] === "potg");
      potgExport.listeners.click();
      return calls;
    }""", payload())
    assert got == [["jump", "a1"], ["export", "a1"], ["export", "potg"]]


def render_player(**context):
    env = Environment(loader=FileSystemLoader(str(WEBAPP / "app" / "templates")))
    return env.get_template("replays/_player.html").render(replay={"map_name": "Ascent", "round_numbers": [1, 2]},
                                                           **context)


def test_the_tab_and_panel_render_only_for_linked_with_clip_export():
    html = render_player(linked=True, clip_export=True)
    assert 'data-replay-tab="highlights"' in html and 'data-replay-panel="highlights"' in html
    assert html.index('data-replay-tab="feed"') < html.index('data-replay-tab="highlights"')
    assert html.index('data-replay-tab="highlights"') < html.index('data-replay-tab="util"')
    for context in ({"linked": False, "clip_export": True}, {"linked": True}, {"linked": False}):
        html = render_player(**context)
        assert 'data-replay-tab="highlights"' not in html and 'data-replay-panel="highlights"' not in html


def render_replay(**context):
    from app.templates import templates
    from test_replay_routes import request

    base = {"replay": {"map_name": "Ascent", "round_numbers": [1], "match_url": None}, "replay_data": {"match": {}},
            "start_round": 1, "start_t": None, "linked": True, "gaps": False, "viewer_slot": 3}
    base.update(context)
    return templates.env.get_template("replays/replay.html").render(**base, request=request())


def test_replay_html_loads_highlights_only_when_linked():
    html = render_replay(linked=True)
    assert "/static/js/highlights.js" in html and "loadHighlights" in html and "/highlights.json" in html
    html = render_replay(linked=False)
    assert "/static/js/highlights.js" not in html and "loadHighlights" not in html


def test_nothing_highlights_renders_in_demo_mode():
    html = render_player(linked=True, clip_export=True, demo_mode=True)
    assert 'data-replay-tab="highlights"' not in html and 'data-replay-panel="highlights"' not in html
    html = render_player(linked=True, clip_export=True, demo_mode=False)
    assert 'data-replay-tab="highlights"' in html and 'data-replay-panel="highlights"' in html
    html = render_replay(linked=True, demo_mode=True)
    assert "/static/js/highlights.js" not in html and "loadHighlights" not in html
    html = render_replay(linked=True, demo_mode=False)
    assert "/static/js/highlights.js" in html and "loadHighlights" in html


VIEWER_PRELUDE = """
  const {environment, makeViewer, simpleBlob} = require(%s);
  const R = environment(%s);
  function viewer(options) {
    const {v} = makeViewer(R);
    Object.assign(v.options, options);
    v.cache = {}; v.rounds = [1, 2, 3]; v.playing = false; v.loadSeq = 0;
    for (const m of ["renderStrip", "renderTicks", "renderBanner", "renderFeed", "renderMapFeatureEvents",
      "renderAnalysis", "renderUtilList", "refreshControl", "renderControlTable", "refreshGaps",
      "updateControls", "draw"]) v[m] = () => {};
    return v;
  }
  function deferred() {
    const d = {}; d.promise = new Promise((res, rej) => { d.resolve = res; d.reject = rej; }); return d;
  }
  const tick = () => new Promise(r => setTimeout(r, 0));
""" % (json.dumps(str(HERE / "viewer_recorder.js")), json.dumps(str(REPLAY_JS)))


def run_viewer(body: str):
    completed = subprocess.run([NODE, "-e", VIEWER_PRELUDE + body + READ_STDIN], input="null",
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_show_tab_loads_highlights_once_across_two_opens():
    got = run_viewer("""async function run() {
      let loads = 0, renders = [];
      const panel = {textContent: ""};
      const v = viewer({loadHighlights: () => { loads++; return Promise.resolve({potg: null, players: {}}); }});
      v.root = {querySelectorAll: () => [], querySelector: sel => sel === '[data-replay-panel="highlights"]' ? panel : null};
      global.Highlights = {render: (p, payload, handlers) => renders.push([p === panel, Object.keys(handlers).sort()])};
      v.showTab("highlights"); await tick();
      v.showTab("feed"); v.showTab("highlights"); await tick();
      return {loads, renders};
    }""")
    assert got == {"loads": 1, "renders": [[True, ["exportClip", "jump"]]]}


@needs_node
def test_play_from_shows_only_the_latest_request():
    got = run_viewer("""async function run() {
      const loads = {}, v = viewer({loadRound: n => { loads[n] = deferred(); return loads[n].promise; }});
      const a = v.playFrom(2, 30), b = v.playFrom(3, 12);
      const blob = n => Object.assign(simpleBlob(), {round: n});
      loads[3].resolve(blob(3)); await b;
      const after3 = [v.number, v.t, v.playing];
      loads[2].resolve(blob(2)); await a; await tick();
      return {after3, final: [v.number, v.t, v.playing]};
    }""")
    assert got["after3"] == [3, 12, True]
    assert got["final"] == [3, 12, True]


@needs_node
def test_play_from_stays_paused_when_paused_while_loading():
    got = run_viewer("""async function run() {
      const loads = {}, v = viewer({loadRound: n => { loads[n] = deferred(); return loads[n].promise; }});
      const b = v.playFrom(3, 12);
      v.toggle();
      loads[3].resolve(Object.assign(simpleBlob(), {round: 3})); await b;
      return [v.number, v.t, v.playing];
    }""")
    assert got == [3, 12, False]


@needs_node
@pytest.mark.parametrize('names', [None, {}, {'5': 'Home'}], ids=['no-names', 'missing-slot', 'clip-name'])
def test_the_tabs_export_clip_passes_the_item_and_its_round_score(names):
    got = run_viewer("""async function run() {
      let handlers = null, opened = [];
      const panel = {textContent: ""};
      const v = viewer({loadHighlights: () => Promise.resolve({potg: null, players: {}}),
        replayData: {round_scores: {"2": [5, 7]}, clip_names: %s}});
      v.root = {querySelectorAll: () => [], querySelector: sel => sel === '[data-replay-panel="highlights"]' ? panel : null};
      v.openExport = prefill => { opened.push(prefill); return Promise.resolve(null); };
      v.playRound = () => Promise.resolve(null);
      global.Highlights = {render: (p, payload, h) => { handlers = h; }};
      v.showTab("highlights"); await tick();
      handlers.exportClip({id: "x", round: 2, t_start: 30, t_end: 42, segments: [[30, 36], [38, 42]],
        slot: 5, summary: "won a clutch", player: "Alpha"});
      return opened;
    }""" % json.dumps(names))
    assert len(got) == 1
    prefill = got[0]
    assert prefill["round"] == 2 and prefill["t_in"] == 30 and prefill["t_out"] == 42
    assert prefill["segments"] == [[30, 36], [38, 42]]
    assert prefill["caption"] == ("Home" if names else "Alpha") + " · won a clutch"
    assert prefill["score"] == [5, 7]
