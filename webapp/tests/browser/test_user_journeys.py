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
