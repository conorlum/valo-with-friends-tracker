"""The match page's Play of the Game card: the htmx hook on the match page, the /potg fragment's JSON block, and
potg.js's clock and player (node, with injected raf, clock, loader and document)."""
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.models import Match
from app.templates import templates
from tests.support.highlights import highlight_replay
from tests.support.site import site  # noqa: F401

WEBAPP = Path(__file__).resolve().parents[1]
POTG_JS = WEBAPP / "app" / "static" / "js" / "potg.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def linked(site, tmp_path):
    return highlight_replay(site[1], tmp_path)


def test_match_page_loads_the_card_and_only_potg_js(site, linked):
    client, _ = site
    uuid, _, match_url, _, _ = linked
    page = client.get(match_url)
    assert page.status_code == 200
    assert f'hx-get="/replays/{uuid}/potg"' in page.text
    assert 'hx-trigger="load"' in page.text and 'hx-swap="outerHTML"' in page.text
    assert re.search(r'<script[^>]+src="/static/js/potg\.js\?v=\d+"', page.text)
    assert 'src="/static/js/replay.js' not in page.text
    if '<h2>Shoutouts</h2>' in page.text:
        assert page.text.index('/potg"') < page.text.index('<h2>Shoutouts</h2>')


def test_match_without_a_replay_has_neither(site, linked):
    client, factory = site
    with factory() as db:
        other = Match(external_id='no-replay-match', source=db.query(Match).first().source, map_name='Haven',
                      team1_rounds_won=13, team2_rounds_won=5)
        db.add(other)
        db.commit()
    page = client.get('/matches/no-replay-match')
    assert page.status_code == 200
    assert '/potg' not in page.text and 'potg.js' not in page.text


def test_demo_mode_has_neither(site, linked, monkeypatch):
    client, _ = site
    _, _, match_url, _, _ = linked
    monkeypatch.setattr(settings, 'demo_mode', True)
    monkeypatch.setitem(templates.env.globals, 'demo_mode', True)
    page = client.get(match_url)
    assert page.status_code == 200
    assert '/potg' not in page.text and 'potg.js' not in page.text


def test_fragment_json_block_and_privacy(site, linked):
    client, _ = site
    uuid, _, _, friends, enemies = linked
    fragment = client.get(f'/replays/{uuid}/potg')
    assert fragment.status_code == 200
    text = fragment.text
    blocks = re.findall(r'<script type="application/json" data-potg>(.*?)</script>', text, re.S)
    assert len(blocks) == 1
    data = json.loads(html.unescape(blocks[0]))
    item = client.get(f'/replays/{uuid}/highlights.json').json()['potg']
    assert data['item']['id'] == item['id']
    assert data['uuid'] == uuid and data['asset'] and 'rotation' in data and 'uv_per_unit' in data
    assert data['round'] == item['round'] and data['site'] and 'db' in data['site']
    assert set(data['clip_names']) == {str(s) for s in range(10)}
    assert data['players']['5']['name'] == friends[0].split('#')[0]
    assert data['players']['0']['name'].startswith('the enemy ')
    assert all(p['name'] == data['clip_names'][s] for s, p in data['players'].items())
    for name in enemies + friends:
        assert name not in text and '#TEST' not in text
    for name in enemies:
        assert name.split('#')[0] not in text
    for needle in ('Play of the Game', 'Open in viewer', 'Export clip', 'Play here', 'data-potg-play',
                   '<canvas', 'width="512"', 'height="512"', '/static/img/agents/'):
        assert needle in text
    assert re.search(r'Round \d+ · \d+:\d\d', html.unescape(text))


PRELUDE = 'const P = require(%s);\n' % json.dumps(str(POTG_JS))

HARNESS = """
function rig(opts) {
  opts = opts || {};
  const frames = [], draws = [], images = [], cancelled = [];
  let clock = 0, next = 1, queue = [], listeners = {};
  const doc = {hidden: false, addEventListener: (type, fn) => { listeners[type] = fn; }};
  const off = {getContext: () => ({tag: "off"})};
  const viewer = {drawAt: (t, ctx, o) => { draws.push([t, ctx.tag, Object.keys(o || {})]); }};
  let resolveLoad;
  const loaded = new Promise(r => { resolveLoad = r; });
  const ctx = {drawImage: (...a) => images.push(a)};
  const player = P.player({
    raf: fn => { const id = next++; queue.push([id, fn]); return id; },
    caf: id => { cancelled.push(id); queue = queue.filter(q => q[0] !== id); },
    now: () => clock, document: doc, ctx, off,
    item: {t_start: 10, t_end: 14}, nameOf: s => "n" + s,
    load: () => opts.immediate ? Promise.resolve(viewer) : loaded.then(() => viewer),
    onError: e => { throw e; }
  });
  function step(ms) { clock += ms; const q = queue; queue = []; q.forEach(([id, fn]) => fn(clock)); }
  return {player, step, draws, images, cancelled, doc, listeners, resolveLoad, pending: () => queue.length,
          setHidden: v => { doc.hidden = v; if (listeners.visibilitychange) listeners.visibilitychange(); }};
}
const tick = () => new Promise(r => setTimeout(r, 0));
"""


def run_node(body):
    completed = subprocess.run([NODE, '-e', PRELUDE + HARNESS + 'async function run() {' + body + '}\n'
                                'run().then(out => process.stdout.write(JSON.stringify(out)), '
                                'e => { console.error(e); process.exit(1); });'],
                               capture_output=True, text=True, encoding='utf-8', timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@needs_node
def test_loop_time():
    out = run_node('return [P.loopTime(10, 14, 0), P.loopTime(10, 14, 5.5), P.loopTime(10, 14, 4), P.loopTime(10, 10, 3)];')
    assert out == [10, 11.5, 10, 10]


@needs_node
def test_frames_advance_wrap_and_scale():
    out = run_node("""
      const r = rig({immediate: true});
      r.player.play(); await tick();
      for (let i = 0; i < 5; i++) r.step(1000);
      return {ts: r.draws.map(d => d[0]), ctx: r.draws.map(d => d[1]), opts: r.draws[0][2], images: r.images};
    """)
    assert out['ts'] == [10, 11, 12, 13, 10, 11]
    assert set(out['ctx']) == {'off'} and 'nameOf' in out['opts']
    assert len(out['images']) == 6
    assert all(a[1:] == [0, 0, 512, 512] for a in out['images'])


@needs_node
def test_stop_cancels_the_next_frame():
    out = run_node("""
      const r = rig({immediate: true});
      r.player.play(); await tick(); r.step(1000);
      const before = r.draws.length;
      r.player.stop(); r.step(1000); r.step(1000);
      return {before, after: r.draws.length, cancelled: r.cancelled.length, pending: r.pending()};
    """)
    assert out['after'] == out['before'] and out['cancelled'] >= 1 and out['pending'] == 0


@needs_node
def test_hidden_during_loading_never_draws():
    out = run_node("""
      const r = rig();
      r.player.play(); await tick();
      r.setHidden(true);
      r.resolveLoad(); await tick();
      r.step(1000); r.step(1000);
      return {draws: r.draws.length, images: r.images.length, pending: r.pending()};
    """)
    assert out == {'draws': 0, 'images': 0, 'pending': 0}


@needs_node
def test_stop_during_loading_never_draws():
    out = run_node("""
      const r = rig();
      r.player.play(); await tick();
      r.player.stop();
      r.resolveLoad(); await tick();
      r.step(1000);
      return {draws: r.draws.length, pending: r.pending()};
    """)
    assert out == {'draws': 0, 'pending': 0}


@needs_node
def test_hidden_during_playback_stops_frames():
    out = run_node("""
      const r = rig({immediate: true});
      r.player.play(); await tick(); r.step(1000);
      const before = r.draws.length;
      r.setHidden(true);
      r.step(1000); r.step(1000);
      return {before, after: r.draws.length, pending: r.pending()};
    """)
    assert out['after'] == out['before'] and out['pending'] == 0
