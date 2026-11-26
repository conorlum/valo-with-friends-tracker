"""T10: actual browser event wiring, fragments and replay canvas; all network stays local."""
import socket
import threading
import time
from pathlib import Path
import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app import main
from tests.support.site import site  # noqa: F401
from tests.support.matches import history
from tests.support.replays import stored_replay

pytestmark = pytest.mark.browser
ASSETS = Path(__file__).parents[1]/'fixtures/browser'


@pytest.fixture
def browser_page(site, request):
    _, factory = site
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=port, lifespan='off', log_level='critical'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            threading.Event().wait(.01)
        assert server.started, 'local test app did not start'
        with sync_playwright() as p:
            try: browser = p.chromium.launch()
            except Exception as error:
                if request.config.getoption('require_browser'): pytest.fail(f'Required Chromium cannot start: {error}')
                pytest.skip('Install Playwright Chromium before running the browser lane')
            try:
                page = browser.new_page()
                errors, failed = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # A reload cancels a 202 control.bin whose body the page never reads: not a failure.
                def on_failed(req):
                    if req.url.endswith('/control.bin') and req.failure == 'net::ERR_ABORTED': return
                    failed.append(req.url)
                page.on('requestfailed', on_failed)
                # Any accidental new external resource is refused and reported.
                def local_only(route):
                    if route.request.url.startswith(f'http://127.0.0.1:{port}/'): route.continue_()
                    else: failed.append(route.request.url); route.abort()
                page.route('**/*', local_only)
                # Last-registered routes win; these pinned scripts are served locally too.
                page.route('https://unpkg.com/htmx.org@1.9.12', lambda route: route.fulfill(
                    path=str(ASSETS/'htmx-1.9.12.min.js'), content_type='text/javascript'))
                page.route('https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js', lambda route: route.fulfill(
                    path=str(ASSETS/'chart-4.4.4.umd.min.js'), content_type='text/javascript'))
                yield page, f'http://127.0.0.1:{port}', factory
                assert errors == [], errors
                assert failed == [], failed
            finally: browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), 'local test server did not stop'


@pytest.mark.parametrize('width', [1280, 390])
def test_profile_filter_career_fragment_and_keyboard_navigation(browser_page, width):
    page, base, factory = browser_page
    history(factory)
    page.set_viewport_size({'width':width,'height':900})
    page.goto(base+'/players/Main%23TEST')
    expect(page.get_by_label('Filter', exact=True)).to_be_visible()
    with page.expect_navigation(): page.get_by_label('Filter', exact=True).select_option('agent:Jett')
    expect(page.get_by_label('Filter', exact=True)).to_have_value('agent:Jett')
    expect(page.get_by_text('Showing 2 matches as Jett', exact=True)).to_be_visible()
    with page.expect_response(lambda response: '/career?' in response.url and response.status == 200):
        page.locator('#career-tab-btn').click()
    expect(page.locator('#chart-data-career')).to_be_attached()
    assert 'filter=agent%3AJett' in page.locator('#career-sections').get_attribute('hx-get')
    page.get_by_label('Filter', exact=True).focus()
    page.keyboard.press('Tab')
    assert page.evaluate('document.activeElement.tagName') != 'BODY'
    ids = page.locator('[id]').evaluate_all('(els) => els.map(e => e.id)')
    assert len(ids) == len(set(ids)), 'fragment insertion created duplicate IDs'


def test_site_health_card_shows_group_heading_and_event_row(browser_page, monkeypatch):
    from app.config import settings
    from app.routers import replays as routes
    from app.services import replay_errors
    page, base, _ = browser_page
    monkeypatch.setattr(settings, 'replay_upload_code', 'test-code')
    monkeypatch.setattr(settings, 'replay_worker_url', 'http://127.0.0.1:9')
    monkeypatch.setattr(routes.uploads, 'batch_limit', lambda worker: 5)
    event = {'key': 'test_event', 'label': 'Test events', 'group': 'Pages', 'shape': 'event', 'count': 3, 'note': None,
             'rows': [{'kind': 'test_event', 'key': 'GET /browser-probe', 'count': 3,
                       'first_seen': '2026-11-01T10:00:00+00:00', 'last_seen': '2026-11-02T10:00:00+00:00',
                       'error': 'probe failure', 'context': {}, 'link': None, 'reproduce': None}]}
    empty = [{'key': key, 'label': label, 'group': 'Replays', 'shape': 'replay', 'count': 0, 'note': None, 'rows': []}
             for key, label in replay_errors.KINDS]
    monkeypatch.setattr(replay_errors, 'cached', lambda db: {'checked_at': '2026-11-02T12:00:00+00:00',
                                                            'kinds': [event] + empty})
    page.goto(base+'/replays/upload')
    page.locator('input[name=code]').fill('test-code')
    with page.expect_navigation(): page.locator('input[name=code]').press('Enter')
    page.locator('[data-errors] summary').click()
    expect(page.locator('[data-errors-body]').get_by_role('heading', name='Pages')).to_be_visible()
    expect(page.locator('[data-errors-body]').get_by_text('GET /browser-probe')).to_be_visible()


def test_upload_drop_box_takes_dropped_files_and_input_stays_plain(browser_page, monkeypatch):
    from app.config import settings
    from app.routers import replays as routes
    page, base, _ = browser_page
    monkeypatch.setattr(settings, 'replay_upload_code', 'test-code')
    monkeypatch.setattr(settings, 'replay_worker_url', 'http://127.0.0.1:9')
    monkeypatch.setattr(routes.uploads, 'batch_limit', lambda worker: 5)
    page.goto(base+'/replays/upload')
    page.locator('input[name=code]').fill('test-code')
    with page.expect_navigation(): page.locator('input[name=code]').press('Enter')
    # showOpenFilePicker's blocklist refuses AppData, where replays live: the input must open the plain picker.
    assert 'showOpenFilePicker(' not in page.content()
    count = page.evaluate("""() => {
        const zone = document.querySelector('[data-drop-zone]'), dt = new DataTransfer();
        dt.items.add(new File(['x'], 'a.vrf')); dt.items.add(new File(['y'], 'b.vrf'));
        zone.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
        return document.querySelector('[data-upload-form] input[type=file]').files.length;
    }""")
    assert count == 2
    expect(page.locator('[data-drop-chosen]')).to_contain_text('2 files ready: a.vrf, b.vrf')


ASCENT_TURNS = 1


def marker_pixels(page, seconds):
    # SyntheticMatch's slot 0 starts at (-1700, 500) and walks +40 x/s.
    # Ascent maps world y to u and world x to v. These expectations come from
    # the input geometry, not the viewer's decoded tracks or hit-test metadata.
    uv = [500 * 0.00007 + 0.813895, (-1700 + 40 * seconds) * -0.00007 + 0.573242]
    # Ascent turns the map one clockwise quarter (hard-coded, not read from the viewer):
    # k=1 maps (x, y) to (S - y, x), before the view offset and zoom.
    return page.evaluate("""([uv, turns]) => {
        const viewer = window.viewer, c = viewer.canvas, size = c.width, view = viewer.view;
        const mx = uv[0]*size, my = uv[1]*size;
        const [rx, ry] = turns === 1 ? [size - my, mx] : turns === 2 ? [size - mx, size - my]
            : turns === 3 ? [my, size - mx] : [mx, my];
        const x = (rx - view.ox)*view.k, y = (ry - view.oy)*view.k;
        const radius = size/48*Math.sqrt(view.k);
        const hex = getComputedStyle(viewer.root).getPropertyValue('--replay-team-1').trim();
        const rgb = hex.startsWith('#')
            ? [1,3,5].map(i => parseInt(hex.slice(i,i+2),16))
            : hex.match(/[\\d.]+/g).slice(0,3).map(Number);
        const rgba = c.getContext('2d').getImageData(0,0,size,size).data;
        let opaque = 0, marker = 0;
        for (let py=0; py<size; py++) for (let px=0; px<size; px++) {
            const i = (py*size+px)*4;
            if (rgba[i+3] > 200) opaque++;
            if (Math.abs(Math.hypot(px-x,py-y)-radius) <= 1.5 && rgba[i+3] > 200 &&
                rgb.every((v,k) => Math.abs(rgba[i+k]-v) <= 10)) marker++;
        }
        return {marker, opaque, size, x, y};
    }""", [uv, ASCENT_TURNS])


def test_replay_fetch_canvas_round_seek_and_unavailable_control(browser_page, tmp_path):
    page, base, factory = browser_page
    uuid, _, _, condensed = stored_replay(factory, tmp_path)
    with page.expect_response(lambda response: f'/replays/{uuid}/1.json' in response.url and response.status == 200):
        page.goto(base+f'/replays/{uuid}?round=1&t=5')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.t === 5')
    page.wait_for_function('window.viewer.map.complete && window.viewer.map.naturalWidth > 0')
    expect(page.locator('[data-replay-canvas]')).to_be_visible()
    assert page.locator('[data-replay-canvas]').get_attribute('aria-label')
    assert page.evaluate('window.viewer.current.blob.round') == 1
    # Leave the minimap, icons and player rings visible; hide annotations that
    # can cover a ring. A visible canvas alone also passes with drawing disabled.
    page.locator('[data-replay-layer="names"]').uncheck()
    page.locator('[data-replay-layer="cones"]').uncheck()
    first = marker_pixels(page, 5)
    assert first['opaque'] > first['size']**2/4, f'minimap was not painted: {first}'
    assert first['marker'] > 20, f'player ring missing at its known 5 s position: {first}'
    before = page.locator('[data-replay-clock]').inner_text()
    page.get_by_role('button',name='Next kill',exact=True).click()
    expect(page.locator('[data-replay-clock]')).not_to_have_text(before)
    # Use the actual scrub input event, then verify the player moved and the
    # previous ring was erased. This also catches an unchanged/stale painting.
    value = round(35/condensed.rounds[1]['t_end']*1000)
    page.get_by_role('slider',name='Time in round').evaluate("""(el, value) => {
        el.value=String(value); el.dispatchEvent(new Event('input',{bubbles:true}));
    }""", value)
    assert page.evaluate('window.viewer.t') == pytest.approx(35,abs=0.03)
    assert page.evaluate('window.viewer.turns') == ASCENT_TURNS
    later = marker_pixels(page,35)
    assert later['marker'] > 20, f'player ring did not move to its known 35 s position: {later}'
    assert marker_pixels(page,5)['marker'] < 5, 'the old player ring was not cleared after seeking'
    # Round 2 may already have been prefetched by the viewer.
    page.get_by_role('button',name='Next round',exact=True).click()
    page.wait_for_function('window.viewer.number === 2 && window.viewer.current.blob.round === 2')
    page.locator('[data-replay-layer="names"]').uncheck()
    assert not page.locator('[data-replay-layer="names"]').is_checked()
    control = page.locator('[data-replay-layer="control"]')
    expect(control).to_be_visible()
    with page.expect_response(lambda response: f'/replays/{uuid}/2/control.bin' in response.url and response.status == 202):
        control.check()
    expect(page.locator('[data-replay-control-status]')).to_have_text("Map control isn't ready for this round yet.")
    before = page.locator('[data-replay-clock]').inner_text()
    start = page.evaluate('window.viewer.t')
    page.get_by_role('button',name='Play',exact=True).click()
    expect(page.get_by_role('button',name='Pause',exact=True)).to_be_visible()
    page.wait_for_function('start => window.viewer.t >= start + 0.3',arg=start,timeout=5000)
    expect(page.locator('[data-replay-clock]')).not_to_have_text(before)
    page.get_by_role('button',name='Pause',exact=True).click()
    stopped = page.evaluate('window.viewer.t')
    after_frames = page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(window.viewer.t))))')
    assert after_frames == stopped, 'playback advanced after pausing'


def test_view_presets_set_the_show_bar_and_a_change_shows_custom(browser_page, tmp_path):
    page, base, factory = browser_page
    uuid, _, _, _ = stored_replay(factory, tmp_path)
    page.goto(base+f'/replays/{uuid}?round=1&t=5')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.map.complete')
    preset = page.locator('[data-replay-preset]')
    expect(preset).to_have_value('custom')
    box = lambda name: page.locator(f'[data-replay-layer="{name}"]')  # noqa: E731
    preset.select_option('team-1')
    assert not box('names').is_checked() and box('control').is_checked() and box('bar').is_checked()
    assert not box('areas').is_checked() and box('abilities').is_checked() and box('aim').is_checked()
    assert page.locator('[data-replay-control-view]').input_value() == 'true'
    enemy = page.evaluate("Object.entries(ReplayControl.groupTeams(window.viewer.linked.players))"
                          ".filter(([g, t]) => t === 'team-2').map(([g]) => g)[0] || 'B'")
    assert page.locator('[data-replay-control-unknown]').input_value() == enemy
    assert page.evaluate('window.viewer.layers.names') is False     # the boxes' own handlers ran
    preset.select_option('all')
    assert box('names').is_checked() and page.locator('[data-replay-control-unknown]').input_value() == 'both'
    box('cones').uncheck()
    expect(preset).to_have_value('custom')
    # No control data for this round: the bar stays hidden however its box is set.
    expect(page.locator('[data-replay-ctlbar]')).to_be_hidden()
    page.reload()
    page.wait_for_function('window.viewer && window.viewer.current')
    expect(page.locator('[data-replay-preset]')).to_have_value('custom')
    assert not page.locator('[data-replay-layer="cones"]').is_checked()
    # A saved team preset is reapplied on the next page, and stays the picker's value (not Custom).
    preset = page.locator('[data-replay-preset]')
    preset.select_option('team-2')
    expect(preset).to_have_value('team-2')
    page.reload()
    page.wait_for_function('window.viewer && window.viewer.current')
    expect(page.locator('[data-replay-preset]')).to_have_value('team-2')
    assert not page.locator('[data-replay-layer="names"]').is_checked()
    assert page.locator('[data-replay-layer="cones"]').is_checked()
    friend = page.evaluate("Object.entries(ReplayControl.groupTeams(window.viewer.linked.players))"
                           ".filter(([g, t]) => t === 'team-1').map(([g]) => g)[0] || 'A'")
    assert page.locator('[data-replay-control-unknown]').input_value() == friend
    # The two control pickers sit in hidden labels, so the change is fired from the page.
    for picker, value in (('data-replay-control-view', 'false'), ('data-replay-control-unknown', 'both')):
        page.locator('[data-replay-preset]').select_option('team-2')
        expect(page.locator('[data-replay-preset]')).to_have_value('team-2')
        page.evaluate("([sel, v]) => { const el = document.querySelector('[' + sel + ']');"
                      " el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); }", [picker, value])
        expect(page.locator('[data-replay-preset]')).to_have_value('custom')


# The replay page's layout (style.css "Replay viewer, linked mode", replay.js fitMapToWindow).

BOXES = """() => {
    const box = s => { const r = document.querySelector(s).getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; };
    return {map: box('[data-replay-canvas]'), scrub: box('.replay-scrub-wrap'), board: box('[data-replay-board]'),
            side: box('.replay-side'), head: box('.replay-body > .replay-header'), banner: box('[data-replay-banner]'),
            inline: document.querySelector('[data-replay]').style.getPropertyValue('--replay-map-h')};
}"""
SETTLED = '() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 50))))'


def open_replay(page, base, factory, tmp_path, width, height):
    uuid, _, ids, _ = stored_replay(factory, tmp_path)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(base+f'/replays/{uuid}?round=1&t=5')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.map.complete')
    page.evaluate(SETTLED)
    return uuid, ids


def test_wide_replay_fits_the_map_and_its_controls_in_the_window(browser_page, tmp_path):
    page, base, factory = browser_page
    open_replay(page, base, factory, tmp_path, 1920, 911)
    b = page.evaluate(BOXES)
    assert b['inline'], 'the wide layout sets the measured map height'
    assert b['scrub']['bottom'] <= 911, f'the timeline runs off the window: {b}'
    assert b['map']['bottom'] > 911 - 150, f'the map leaves the window unused: {b}'
    assert abs(b['side']['top'] - b['map']['top']) <= 2, f'the tabs do not start with the map: {b}'
    for column in ('board', 'side'):
        assert abs(b[column]['top'] - b['map']['top']) <= 2, f'{column} does not start with the map: {b}'
        assert abs(b[column]['bottom'] - b['map']['bottom']) <= 2, f'{column} does not end with the map: {b}'
    for above in ('head', 'banner'):
        assert b[above]['bottom'] <= b['map']['top'] + 1, f'the {above} is not above the map: {b}'
    # The players panel's rows grow with it, and all ten still fit without scrolling.
    fit = page.evaluate("""() => { const board = document.querySelector('[data-replay-board]');
        return {font: parseFloat(getComputedStyle(board.querySelector('.replay-pl-team')).fontSize),
                scroll: board.scrollHeight, client: board.clientHeight}; }""")
    assert fit['font'] > 0.85 * 16, f'the players panel did not scale up: {fit}'
    assert fit['scroll'] <= fit['client'] + 1, f'the players panel overflows: {fit}'
    # Resizing into the medium layout hands sizing back to the stylesheet; resizing back measures again.
    page.set_viewport_size({'width': 1300, 'height': 911})
    page.evaluate(SETTLED)
    assert page.evaluate(BOXES)['inline'] == ''
    page.set_viewport_size({'width': 1600, 'height': 800})
    page.evaluate(SETTLED)
    b = page.evaluate(BOXES)
    assert b['inline'] and b['scrub']['bottom'] <= 800, b


def test_a_tall_tab_does_not_move_the_map_at_medium_width(browser_page, tmp_path):
    page, base, factory = browser_page
    open_replay(page, base, factory, tmp_path, 1300, 900)
    before = page.evaluate(BOXES)['map']['top']
    page.evaluate("""() => {
        const panel = document.querySelector('.replay-side [data-replay-panel]:not([hidden])');
        const tall = document.createElement('div'); tall.style.height = '3000px'; panel.appendChild(tall);
    }""")
    page.evaluate(SETTLED)
    assert page.evaluate(BOXES)['map']['top'] == pytest.approx(before, abs=1)


def test_sending_coach_feedback_keeps_the_page_where_it_is(browser_page, tmp_path):
    from app.models import Player, Replay
    from app.models.coach import CoachFeedback
    from app.services import coach
    from tests.coach.test_coach_store import payload
    page, base, factory = browser_page
    uuid, _, ids, _ = stored_replay(factory, tmp_path)
    with factory() as db:
        match_id = db.query(Replay).filter(Replay.match_uuid == uuid).one().match_id
        coach.store_report(db, match_id, ids[0], payload())
        reader = db.get(Player, ids[1]).display_name
    assert page.request.post(base+'/login', form={'display_name': reader}).ok
    page.set_viewport_size({'width': 1920, 'height': 911})
    page.goto(base+f'/replays/{uuid}/coach?player={ids[0]}')
    page.wait_for_function('window.viewer && window.viewer.current')
    page.evaluate('() => { window.notReloaded = true; window.scrollTo(0, 120); }')
    page.evaluate(SETTLED)
    scrolled = page.evaluate('window.scrollY')
    point = page.locator('[data-coach-point]').first
    point.locator('textarea[name="note"]').fill('Stays put')
    area = point.get_by_label('Area should be')
    player = point.locator('select[name="area_player_0"]')
    expect(player).to_have_value(str(ids[0]))
    area.fill('Market')
    player.select_option(str(ids[5]))
    point.get_by_role('button', name='Send').click()
    expect(page.locator('[data-coach-send-status]').first).to_have_text('Sent')
    assert page.evaluate('window.notReloaded') is True, 'Send reloaded the page'
    assert page.evaluate('window.scrollY') == scrolled
    expect(page.locator('[data-coach-point]').first.locator('textarea[name="note"]')).to_have_value('Stays put')
    expect(area).to_have_value('Market')
    expect(player).to_have_value(str(ids[5]))
    page.reload()
    expect(area).to_have_value('Market')
    expect(player).to_have_value(str(ids[5]))
    with factory() as db:
        rows = {row.instance_index: row for row in db.query(CoachFeedback)}
        assert rows[None].note == 'Stays put'
        assert (rows[0].area_name, rows[0].area_player_id) == ('Market', ids[5])


GRAB = """() => { const c = window.viewer.canvas; return Array.from(c.getContext('2d').getImageData(0,0,c.width,c.height).data); }"""


def test_rotated_replay_hover_round_buttons_and_aim_line(browser_page, tmp_path):
    page, base, factory = browser_page
    uuid, _, _, _ = stored_replay(factory, tmp_path)
    page.set_viewport_size({'width': 1920, 'height': 911})
    page.goto(base+f'/replays/{uuid}?round=1&t=5')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.t === 5')
    page.wait_for_function('window.viewer.map.complete && window.viewer.map.naturalWidth > 0')
    page.evaluate(SETTLED)
    page.locator('[data-replay-layer="names"]').uncheck()
    page.locator('[data-replay-layer="cones"]').uncheck()
    # Hover: the pointer over slot 0's projected (rotated) position shows its tooltip.
    spot = marker_pixels(page, 5)
    canvas = page.locator('[data-replay-canvas]')
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    page.mouse.move(box['x'] + spot['x']*box['width']/spot['size'], box['y'] + spot['y']*box['height']/spot['size'])
    tip = page.locator('[data-replay-tip]')
    expect(tip).to_be_visible()
    assert page.evaluate('window.viewer.nameOf(0)') in tip.inner_text()
    page.mouse.move(box['x'] + 2, box['y'] + 2)
    expect(tip).to_be_hidden()
    # Round buttons: text buttons at the ends of the controls row, no arrows beside the strip.
    assert page.locator('.replay-controls button').first.get_attribute('data-replay-prev') is not None
    assert page.locator('.replay-controls button').last.get_attribute('data-replay-next') is not None
    arrows = page.locator('.replay-rounds button').evaluate_all(
        "els => els.filter(e => /^[\\u2190\\u2192\\u2039\\u203a<>]$/.test(e.textContent.trim())).length")
    assert arrows == 0
    expect(page.locator('[data-replay-prev]')).to_be_disabled()
    expect(page.locator('[data-replay-next]')).to_be_enabled()
    page.locator('[data-replay-next]').click()
    page.wait_for_function('window.viewer.number === 2 && window.viewer.current.blob.round === 2')
    expect(page.locator('[data-replay-prev]')).to_be_enabled()
    page.locator('[data-replay-prev]').click()
    page.wait_for_function('window.viewer.number === 1 && window.viewer.current.blob.round === 1')
    # Aim line. Slot 0 faces a wall a few pixels away on this fixture, so find a living player whose
    # line is long (the same sight-mask rule the viewer uses), and look at that moment.
    aim = page.locator('[data-replay-layer="aim"]')
    expect(aim).to_be_checked()
    target = page.evaluate("""() => {
        const v = window.viewer, T = window.Replay, s = v.sight; let best = null;
        for (let t = 0; t < 60; t += 1) for (const p of v.current.blob.players) {
            const at = T.trackAt(v.current.tracks[String(p.slot)], t);
            if (!at || !at.live) continue;
            const rad = at.yaw*Math.PI/180;
            const end = T.tracerEnd(s.mask, s.size, at.u, at.v, at.u+Math.cos(rad)*20000, at.v+Math.sin(rad)*20000);
            const len = end ? end.steps*v.canvas.width/s.size : 0;
            if (!best || len > best.len) best = {t, slot: p.slot, u: at.u, v: at.v, len};
        }
        return best;
    }""")
    assert target and target['len'] > 80, f'no living player with a long line of sight in the fixture: {target}'
    page.evaluate('t => window.viewer.seek(t)', target['t'])
    page.wait_for_function('t => window.viewer.t === t', arg=target['t'])
    page.evaluate(SETTLED)
    spot = page.evaluate("""([u, v, turns]) => {
        const c = window.viewer.canvas, size = c.width, view = window.viewer.view;
        const mx = u*size/10000, my = v*size/10000;
        const [rx, ry] = turns === 1 ? [size - my, mx] : [mx, my];
        return {x: (rx - view.ox)*view.k, y: (ry - view.oy)*view.k, size};
    }""", [target['u'], target['v'], ASCENT_TURNS])
    with_aim = page.evaluate(GRAB)
    aim.uncheck(); page.evaluate(SETTLED)
    without = page.evaluate(GRAB)
    size, radius = spot['size'], spot['size']/48
    changed = [i//4 for i in range(0, len(with_aim), 4) if with_aim[i:i+4] != without[i:i+4]]
    dist2 = lambda p: ((p % size)-spot['x'])**2 + ((p//size)-spot['y'])**2
    assert len(changed) > 10, 'unchecking Aim lines changed nothing'
    assert max(map(dist2, changed)) > (radius*2)**2, 'the aim line does not reach past the player circle'
    aim.check(); page.evaluate(SETTLED)
    assert page.evaluate(GRAB) == with_aim, 'rechecking Aim lines did not restore the line'


# ---- Clip export, the Highlights tab and the match page's Play here (W8) ----

LAZY = ('clip_export.js', 'mp4-muxer')


def record_requests(page):
    seen = []
    page.on('request', lambda r: seen.append((r.method, r.url, r.post_data_buffer)))
    return seen


def read_download(download):
    data = Path(download.path()).read_bytes()
    assert download.failure() is None
    return data


def size_of(text):
    import re
    found = re.search(r'([\d.]+) MB', text)
    return float(found.group(1)) if found else 0


def open_clip_page(page, base, uuid, query='?round=1&t=1'):
    page.goto(base+f'/replays/{uuid}{query}')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.map.complete')


def scored_match(factory, tmp_path):
    """highlight_replay plus one Impact row per player, so the match page's scoreboard (which lists scored players)
    has someone to show, as a real match does."""
    from app.models import ImpactScore, MatchPlayer, Round
    from app.models.replay import Replay
    from tests.support.highlights import highlight_replay
    result = highlight_replay(factory, tmp_path)
    with factory() as db:
        match_id = db.get(Replay, result[1]).match_id
        first = db.query(Round).filter(Round.match_id == match_id).order_by(Round.round_number).first()
        for mp in db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id):
            db.add(ImpactScore(round_id=first.id, match_player_id=mp.id, kill_impact=10, death_impact=0, impact=10))
        db.commit()
    return result


def set_field(page, field, value):
    page.locator(f'#clip-{field}').fill(value) if field in ('in', 'out') else page.locator(f'#clip-{field}').select_option(value)
    page.locator(f'#clip-{field}').dispatch_event('change')


def wait_progress(page):
    try:
        page.wait_for_function("() => document.querySelector('#clip-progress').value >= 1", timeout=30000)
    except Exception:
        state = page.evaluate("""() => { const q = id => document.querySelector('#clip-' + id);
            return {message: q('message').textContent, progress: [q('progress').value, q('progress').max],
                    exportDisabled: q('export').disabled, cancelDisabled: q('cancel').disabled, size: q('size').textContent,
                    estimate: q('estimate').textContent, hidden: document.querySelector('[data-clip-panel]').hidden}; }""")
        raise AssertionError(f'export never showed a frame: {state}') from None


def export_ready(page):
    page.wait_for_function("() => !document.querySelector('#clip-export').disabled || "
                           "/not supported/.test(document.querySelector('#clip-message').textContent)")
    message = page.locator('#clip-message').inner_text()
    assert 'not supported' not in message, f'fixture Chromium has no H.264 encoder (pickCodec null): {message}'


def test_export_clip_downloads_an_mp4_and_sends_nothing(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, _, _, _ = highlight_replay(factory, tmp_path)
    open_clip_page(page, base, uuid)
    seen = record_requests(page)
    page.locator('[data-replay-export]').click()
    expect(page.locator('[data-clip-panel]')).to_be_visible()
    set_field(page, 'in', '1.0'); set_field(page, 'out', '3.0')
    export_ready(page)
    marks = {}
    with page.expect_download(timeout=60000) as info:
        page.locator('#clip-export').click()
        wait_progress(page)
        marks['n'] = len(seen)
    data = read_download(info.value)
    assert len(data) > 0 and data[4:8] == b'ftyp', data[:16]
    assert info.value.suggested_filename == 'round1-1s.mp4'
    assert size_of(page.locator('#clip-size').inner_text()) > 0
    assert page.locator('#clip-message').inner_text() == 'Clip downloaded.'
    for method, url, body in seen:
        assert method == 'GET', (method, url)
        assert body is None, url
    assert [u for _, u, _ in seen[marks['n']:]] == [], 'a request was made after the clip was prepared'
    assert any('clip_export.js' in u for _, u, _ in seen) and any('mp4-muxer' in u for _, u, _ in seen)


def test_the_potg_export_link_opens_the_panel_with_the_play_filled_in(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, match_url, _, _ = scored_match(factory, tmp_path)
    page.goto(base+match_url)
    card = page.locator('[data-potg-card]')
    expect(card).to_be_visible()
    summary = card.locator('.potg-summary').inner_text()
    potg = page.request.get(base+f'/replays/{uuid}/highlights.json').json()['potg']
    with page.expect_navigation():
        card.get_by_role('link', name='Export clip').click()
    page.wait_for_function('window.viewer && window.viewer.current')
    expect(page.locator('[data-clip-panel]')).to_be_visible(timeout=15000)
    page.wait_for_function("() => document.querySelector('#clip-in').value !== ''")
    assert float(page.locator('#clip-in').input_value()) == pytest.approx(potg['t_start'], abs=0.01)
    assert float(page.locator('#clip-out').input_value()) == pytest.approx(potg['t_end'], abs=0.01)
    assert summary in page.locator('#clip-caption').input_value()
    if len(potg.get('segments') or []) >= 2:
        expect(page.locator('#clip-cut')).to_be_enabled()
    else:
        expect(page.locator('#clip-cut')).to_be_disabled()


def test_page_load_does_not_request_the_export_scripts(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, match_url, _, _ = scored_match(factory, tmp_path)
    seen = record_requests(page)
    open_clip_page(page, base, uuid)
    page.wait_for_load_state('networkidle')
    assert not [u for _, u, _ in seen if any(k in u for k in LAZY)]
    page.locator('[data-replay-export]').click()
    expect(page.locator('[data-clip-panel]')).to_be_visible()
    page.wait_for_function("() => !!window.ClipExport")
    assert [u for _, u, _ in seen if 'clip_export.js' in u] and [u for _, u, _ in seen if 'mp4-muxer' in u]
    # The match page loads neither the export code nor replay.js until Play here.
    seen.clear()
    page.goto(base+match_url)
    expect(page.locator('[data-potg-card]')).to_be_visible()
    page.wait_for_load_state('networkidle')
    assert not [u for _, u, _ in seen if any(k in u for k in LAZY + ('replay.js',))]


def test_highlights_tab_items_play_and_export_their_round(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, _, _, _ = highlight_replay(factory, tmp_path)
    open_clip_page(page, base, uuid)
    body = page.request.get(base+f'/replays/{uuid}/highlights.json').json()
    items = ([body['potg']] if body['potg'] else []) + [p['best'] for p in body['players'].values() if p.get('best')]
    page.locator('[data-replay-tab="highlights"]').click()
    first = items[0]
    expect(page.locator(f'[data-highlight-export="{first["id"]}"]').first).to_be_visible()
    page.locator(f'[data-highlight-export="{first["id"]}"]').first.click()
    expect(page.locator('[data-clip-panel]')).to_be_visible()
    page.wait_for_function("() => document.querySelector('#clip-in').value !== ''")
    assert float(page.locator('#clip-in').input_value()) == pytest.approx(first['t_start'], abs=0.01)
    assert float(page.locator('#clip-out').input_value()) == pytest.approx(first['t_end'], abs=0.01)
    # An item from another round than the one shown exports that round.
    other = next(i for i in items if i['round'] != 1)
    page.locator(f'[data-highlight-export="{other["id"]}"]').first.click()
    page.wait_for_function('t => Math.abs(Number(document.querySelector("#clip-in").value) - t) < 0.02', arg=other['t_start'])
    set_field(page, 'width', '512')
    export_ready(page)
    with page.expect_download(timeout=120000) as info:
        page.locator('#clip-export').click()
    data = read_download(info.value)
    assert len(data) > 0 and data[4:8] == b'ftyp'
    assert info.value.suggested_filename.startswith(f'round{other["round"]}-')
    # Play seeks to the item's round and start, and plays.
    page.locator(f'[data-highlight-jump="{other["id"]}"]').first.click()
    page.wait_for_function('([n, t]) => window.viewer.number === n && window.viewer.t >= t', arg=[other['round'], other['t_start']])


def test_play_here_loops_the_potg_on_a_canvas_and_stop_ends_it(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, match_url, _, _ = scored_match(factory, tmp_path)
    seen = record_requests(page)
    page.goto(base+match_url)
    card = page.locator('[data-potg-card]')
    expect(card).to_be_visible()
    assert not [u for _, u, _ in seen if 'replay.js' in u]
    page.evaluate("""() => { window.__draws = 0; const d = CanvasRenderingContext2D.prototype.drawImage;
        CanvasRenderingContext2D.prototype.drawImage = function () { window.__draws++; return d.apply(this, arguments); }; }""")
    card.locator('[data-potg-play]').click()
    expect(card.locator('[data-potg-play]')).to_have_text('Stop', timeout=15000)
    assert [u for _, u, _ in seen if 'replay.js' in u]
    page.wait_for_timeout(1000)
    total = page.evaluate("""() => { const c = document.querySelector('[data-potg-canvas]');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let s = 0;
        for (let i = 0; i < d.length; i++) s += d[i]; return s; }""")
    assert total > 0
    card.locator('[data-potg-play]').click()
    expect(card.locator('[data-potg-play]')).to_have_text('▶ Play here')
    page.wait_for_timeout(100)
    frozen = page.evaluate('window.__draws')
    page.wait_for_timeout(400)
    assert page.evaluate('window.__draws') == frozen, 'frames kept coming after Stop'


def test_cancel_stops_an_export_and_a_second_export_works(browser_page, tmp_path):
    from tests.support.highlights import highlight_replay
    page, base, factory = browser_page
    uuid, _, _, _, _ = highlight_replay(factory, tmp_path)
    open_clip_page(page, base, uuid)
    downloads = []
    page.on('download', lambda d: downloads.append(d))
    page.locator('[data-replay-export]').click()
    expect(page.locator('[data-clip-panel]')).to_be_visible()
    set_field(page, 'width', '1024'); set_field(page, 'in', '0'); set_field(page, 'out', '10')
    export_ready(page)
    page.locator('#clip-export').click()
    wait_progress(page)
    page.locator('#clip-cancel').click()
    expect(page.locator('#clip-message')).to_have_text('Cancelled.', timeout=5000)
    page.wait_for_timeout(2000)
    assert downloads == []
    set_field(page, 'width', '512'); set_field(page, 'out', '2')
    export_ready(page)
    with page.expect_download(timeout=60000) as info:
        page.locator('#clip-export').click()
    data = read_download(info.value)
    assert len(data) > 0 and data[4:8] == b'ftyp'
