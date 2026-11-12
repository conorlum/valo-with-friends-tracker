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
                page.on('requestfailed', lambda req: failed.append(req.url))
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


def marker_pixels(page, seconds):
    # SyntheticMatch's slot 0 starts at (-1700, 500) and walks +40 x/s.
    # Ascent maps world y to u and world x to v. These expectations come from
    # the input geometry, not the viewer's decoded tracks or hit-test metadata.
    uv = [500 * 0.00007 + 0.813895, (-1700 + 40 * seconds) * -0.00007 + 0.573242]
    return page.evaluate("""uv => {
        const viewer = window.viewer, c = viewer.canvas, size = c.width, view = viewer.view;
        const x = (uv[0]*size - view.ox)*view.k, y = (uv[1]*size - view.oy)*view.k;
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
    }""", uv)


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


# The replay page's layout (style.css "Replay viewer, linked mode", replay.js fitMapToWindow).

BOXES = """() => {
    const box = s => { const r = document.querySelector(s).getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; };
    return {map: box('[data-replay-canvas]'), scrub: box('.replay-scrub-wrap'), board: box('[data-replay-board]'),
            side: box('.replay-side'), inline: document.querySelector('[data-replay]').style.getPropertyValue('--replay-map-h')};
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
        assert abs(b[column]['bottom'] - b['map']['bottom']) <= 2, f'{column} does not end with the map: {b}'
    assert b['board']['top'] > b['map']['top'], 'the title and banner sit above the players, beside the map'
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
