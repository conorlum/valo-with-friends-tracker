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


def test_replay_fetch_canvas_round_seek_and_unavailable_control(browser_page, tmp_path):
    page, base, factory = browser_page
    uuid, _, _, condensed = stored_replay(factory, tmp_path)
    with page.expect_response(lambda response: f'/replays/{uuid}/1.json' in response.url and response.status == 200):
        page.goto(base+f'/replays/{uuid}?round=1&t=5')
    page.wait_for_function('window.viewer && window.viewer.current && window.viewer.t === 5')
    expect(page.locator('[data-replay-canvas]')).to_be_visible()
    assert page.locator('[data-replay-canvas]').get_attribute('aria-label')
    assert page.evaluate('window.viewer.current.blob.round') == 1
    before = page.locator('[data-replay-clock]').inner_text()
    page.get_by_role('button',name='Next kill',exact=True).click()
    expect(page.locator('[data-replay-clock]')).not_to_have_text(before)
    # Round 2 may already have been prefetched by the viewer.
    page.get_by_role('button',name='Next round',exact=True).click()
    page.wait_for_function('window.viewer.number === 2 && window.viewer.current.blob.round === 2')
    page.locator('[data-replay-layer="names"]').uncheck()
    assert not page.locator('[data-replay-layer="names"]').is_checked()
    control = page.locator('[data-replay-layer="control"]')
    if control.count():
        control.check()
        expect(page.locator('[data-replay-control-status]')).not_to_be_empty()
    page.get_by_role('button',name='Play',exact=True).click()
    expect(page.get_by_role('button',name='Pause',exact=True)).to_be_visible()
    page.get_by_role('button',name='Pause',exact=True).click()
