"""The utility critique gallery in a real browser: the page is rendered from a tiny samples dict and opened as file://."""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser

WEBAPP = Path(__file__).parents[2]
sys.path.insert(0, str(WEBAPP / "scripts"))
import render_util_gallery  # noqa: E402

DARK = "ability:^Wraith_4_Smoke$"
CAGE = "ability:^Gumshoe_Q_Cage$"
TRAP = "ability:^Gumshoe_Q_CageTrap$"
ORIGINAL_DARK = "#5c46a8"
ORIGINAL_CAGE = "#d9dee6"
CANVASES = "[...document.querySelectorAll('[data-entry-id=\"%s\"] canvas')].map(c => c.toDataURL())"


def sample(map_name, side, agent, kind, name, u, v, **extra):
    row = {"agent": agent, "by": 3, "code": name.split("_", 1)[0], "full": 10.0, "id": 1, "k": "ability",
           "kind": kind, "name": name.split("_", 1)[1], "owner_by": "instigator",
           "t": 10.0, "t1": 25.0, "u": u, "v": v, "z": 10}
    row.update(extra)
    return {"map": map_name, "t_end": 60.0, "side": side, "row": row, "companions": []}


SAMPLES = {"made": "test", "replays": 1, "keys": {
    "Zone|Wraith_4_Smoke": {"count": 2, "replays": 2, "samples": [
        sample("Haven", "A", "Omen", "Zone", "Wraith_4_Smoke", 3139, 1359),
        sample("Lotus", "B", "Omen", "Zone", "Wraith_4_Smoke", 5200, 4300)]},
    "Zone|Gumshoe_Q_Cage": {"count": 1, "replays": 1, "samples": [
        sample("Lotus", "B", "Cypher", "Zone", "Gumshoe_Q_Cage", 1886, 4588, yaw=180)]},
    "GameObject|Gumshoe_Q_CageTrap": {"count": 1, "replays": 1, "samples": [
        sample("Lotus", "B", "Cypher", "GameObject", "Gumshoe_Q_CageTrap", 1886, 4588, yaw=180)]},
    "Projectile|Grenadier_Q_SemtexBasic": {"count": 1, "replays": 1, "samples": [
        sample("Haven", "A", "KAY/O", "Projectile", "Grenadier_Q_SemtexBasic", 3576, 5896, yaw=318,
               flight=[[13.57, 3576, 5896, 48], [13.768, 3818, 5673, 45], [14.182, 4322, 5209, 30]])]},
}}


@pytest.fixture(scope="module")
def chromium(request):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as error:
            if request.config.getoption("require_browser"):
                pytest.fail(f"Required Chromium cannot start: {error}")
            pytest.skip("Install Playwright Chromium before running the browser lane")
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def gallery_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("gallery") / "util-gallery.html"
    path.write_text(render_util_gallery.render(SAMPLES), encoding="utf-8")
    return path


@pytest.fixture
def open_gallery(chromium, gallery_file):
    """open(storage_throws=False) -> page. Page errors and non-local requests are asserted empty at the end."""
    errors, outside, contexts = [], [], []

    def open_page(storage_throws=False):
        context = chromium.new_context(accept_downloads=True)
        contexts.append(context)
        context.grant_permissions(["clipboard-read", "clipboard-write"])

        def only_local(route):
            if route.request.url.split(":", 1)[0] in ("file", "data", "blob"):
                route.continue_()
            else:
                outside.append(route.request.url)
                route.abort()
        context.route("**/*", only_local)
        if storage_throws:
            context.add_init_script("""
                Storage.prototype.getItem = function () { throw new Error('storage off'); };
                Storage.prototype.setItem = function () { throw new Error('storage off'); };
            """)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(gallery_file.as_uri())
        settle(page)
        return page

    yield open_page
    for context in contexts:
        context.close()
    assert errors == [], errors
    assert outside == [], outside


def tile(page, entry_id):
    return page.locator(f'[data-entry-id="{entry_id}"]')


def canvases(page, entry_id):
    return page.evaluate(CANVASES % entry_id)


def every_canvas(page):
    return page.evaluate("""Object.fromEntries(window.utilGallery.tiles.map(t =>
        [t.entry.id, t.canvases.map(c => c.canvas.toDataURL()).join('|')]))""")


def settle(page):
    """Wait for the maps and icons to load and the drawing to stop changing."""
    page.wait_for_function("window.utilGallery && window.utilGallery.tiles.every(t => !t.viewer.mapUrl || "
                           "(t.viewer.map && t.viewer.map.complete && t.viewer.map.naturalWidth))")
    last = None
    for _ in range(40):
        now = every_canvas(page)
        if now == last:
            return
        last = now
        page.wait_for_timeout(50)


def wait_changed(page, entry_id, before):
    page.wait_for_function("([id, before]) => JSON.stringify(" + (CANVASES % "' + id + '") + ") !== before",
                           arg=[entry_id, json.dumps(before)])
    return canvases(page, entry_id)


def set_colour(page, entry_id, value):
    tile(page, entry_id).locator('[data-control="color"]').fill(value)
    page.wait_for_timeout(100)


def visible_ids(page):
    return page.evaluate("window.utilGallery.tiles.filter(t => !t.root.hidden).map(t => t.entry.id)")


def test_opens_clean_with_one_tile_per_entry(open_gallery):
    page = open_gallery()
    count = page.evaluate("UtilGallery.galleryEntries(Replay, JSON.parse(document.getElementById('gallery-payload')"
                          ".textContent).samples, JSON.parse(document.getElementById('gallery-payload').textContent)"
                          ".abilityIcons).length")
    assert count > 10
    assert page.evaluate("window.utilGallery.tiles.length") == count
    assert page.locator("[data-entry-id]").count() == count


def test_the_tile_draws_the_utility_and_made_up_and_missing_samples_say_so(open_gallery):
    page = open_gallery()
    full = canvases(page, DARK)
    assert full and all(full)
    sizes = tile(page, DARK).locator('canvas').first.evaluate('c => [c.width, c.clientWidth]')
    assert sizes == [480, 240]
    page.evaluate("""(id) => { const t = window.utilGallery.tiles.find(t => t.entry.id === id);
        t.viewer.layers.abilities = false; t.viewer.draw(); }""", DARK)
    map_only = canvases(page, DARK)
    page.evaluate("""(id) => { const t = window.utilGallery.tiles.find(t => t.entry.id === id);
        t.viewer.layers.abilities = true; t.viewer.draw(); }""", DARK)
    assert map_only != full
    assert canvases(page, DARK) == full
    badges = page.evaluate("window.utilGallery.tiles.map(t => [t.sampleBadge.textContent, t.sampleBadge.hidden])")
    assert ["made up", False] in badges
    assert ["no sample", False] in badges


def test_colour_edits_only_the_one_ability(open_gallery):
    page = open_gallery()
    before = every_canvas(page)
    set_colour(page, DARK, "#00ff00")
    after = every_canvas(page)
    abilities = page.evaluate("Object.fromEntries(window.utilGallery.tiles.map(t => [t.entry.id, t.entry.ability]))")
    changed = [key for key in before if before[key] != after[key]]
    assert DARK in changed
    assert {abilities[key] for key in changed} == {"Dark Cover"}


def test_size_icon_look_and_settings_redraw_and_reset_restores(open_gallery):
    page = open_gallery()
    original = canvases(page, DARK)
    dark = tile(page, DARK)
    dark.locator('[data-control="r"]').fill("800")
    dark.locator('[data-control="r"]').dispatch_event("change")
    sized = wait_changed(page, DARK, original)
    assert sized != original

    centre = "(id) => window.utilGallery.tiles.find(t => t.entry.id === id).canvases.map(c => " \
             "Array.from(c.canvas.getContext('2d').getImageData(240, 240, 1, 1).data).join())"
    before_icon, pixels = canvases(page, DARK), page.evaluate(centre, DARK)
    dark.locator('[data-control="badge"]').set_checked(True)
    with_icon = wait_changed(page, DARK, before_icon)
    assert with_icon != before_icon
    assert page.evaluate(centre, DARK) != pixels

    dark.locator('[data-control="look"]').select_option("grain")
    grain = wait_changed(page, DARK, with_icon)
    assert grain != with_icon

    dark.locator('[data-control="settings"]').fill("{not json")
    dark.locator('[data-control="settings"]').dispatch_event("change")
    page.wait_for_timeout(100)
    assert dark.locator("[data-settings-error]").inner_text().strip() != ""
    assert canvases(page, DARK) == grain

    dark.locator('[data-control="reset"]').click()
    assert wait_changed(page, DARK, grain) == original
    assert dark.locator("[data-settings-error]").inner_text().strip() == ""


def test_sample_prev_and_next_switch_the_drawing(open_gallery):
    page = open_gallery()
    dark = tile(page, DARK)
    first = canvases(page, DARK)
    assert "1 of 2" in dark.inner_text()
    dark.locator('[data-sample="next"]').click()
    second = wait_changed(page, DARK, first)
    assert second != first and "2 of 2" in dark.inner_text()
    dark.locator('[data-sample="prev"]').click()
    assert wait_changed(page, DARK, second) == first


def test_palette_and_side_change_the_drawing(open_gallery):
    page = open_gallery()
    start = every_canvas(page)
    page.evaluate("""() => { const s = document.querySelector('[data-header="side"]'); s.value = 'B';
        s.dispatchEvent(new Event('change')); }""")
    page.wait_for_timeout(100)
    settle(page)
    by_side = every_canvas(page)
    page.evaluate("""() => { const s = document.querySelector('[data-header="palette"]');
        s.value = s.options[s.options.length - 1].value; s.dispatchEvent(new Event('change')); }""")
    page.wait_for_timeout(100)
    settle(page)
    by_palette = every_canvas(page)
    assert page.evaluate("document.querySelector('[data-header=\"palette\"]').options.length") > 1
    assert by_side[DARK] != start[DARK], "side B must change the Dark Cover tile (sample side is A)"
    assert by_palette[DARK] != by_side[DARK], "the palette must change the Dark Cover tile"


def test_export_and_copy_prompt(open_gallery, tmp_path):
    page = open_gallery()
    set_colour(page, DARK, "#00ff00")
    other = page.evaluate("window.utilGallery.tiles.find(t => !['Dark Cover', 'Cyber Cage'].includes("
                          "t.entry.ability) && t.entry.list === 'ability').entry.id")
    tile(page, other).locator('[data-control="notes"]').fill("make this brighter")
    with page.expect_download() as download:
        page.locator('[data-header="export"]').click()
    info = download.value
    assert re.fullmatch(r"util-critique-\d{4}-\d{2}-\d{2}\.json", info.suggested_filename)
    saved = tmp_path / "export.json"
    info.save_as(saved)
    data = json.loads(saved.read_text(encoding="utf-8"))
    replay = (WEBAPP / "app/static/js/replay.js").read_text(encoding="utf-8")
    assert data["replay_js_sha256"] == hashlib.sha256(replay.encode("utf-8")).hexdigest()
    dark = [e for e in data["entries"] if e["list"] == "ability" and e["pattern"] == "^Wraith_4_Smoke$"]
    assert len(dark) == 1 and dark[0]["after"]["color"] == "#00ff00"
    assert dark[0]["before"]["color"] == ORIGINAL_DARK
    coloured = [e for e in data["entries"] if "color" in e["after"]]
    assert coloured and {e["ability"] for e in coloured} == {"Dark Cover"}
    noted = [e for e in data["entries"] if e["notes"] == "make this brighter"]
    assert len(noted) == 1 and noted[0]["after"] == {}

    page.locator('[data-header="copy"]').click()
    page.wait_for_function("document.querySelector('[data-header=\"status\"]').textContent !== ''")
    assert "Copied" in page.locator('[data-header="status"]').inner_text()
    text = page.evaluate("navigator.clipboard.readText()")
    assert text.startswith("Apply each after to the entry with that pattern")
    assert '"entries"' in text


def test_filters(open_gallery):
    page = open_gallery()
    everything = visible_ids(page)
    page.locator('[data-header="agent"]').select_option("Omen")
    shown = visible_ids(page)
    assert shown and len(shown) < len(everything)
    assert page.evaluate("window.utilGallery.tiles.filter(t => !t.root.hidden).every(t => t.entry.agent === 'Omen')")
    page.locator('[data-header="agent"]').select_option("")
    assert visible_ids(page) == everything

    page.locator('[data-header="filter"]').fill("dark cover")
    shown = visible_ids(page)
    assert DARK in shown and len(shown) < len(everything)
    assert page.evaluate("window.utilGallery.tiles.filter(t => !t.root.hidden).every(t => "
                         "[t.entry.ability, t.entry.agent, t.entry.shape, t.entry.pattern, t.entry.style && "
                         "t.entry.style.label].join(' ').toLowerCase().includes('dark cover'))")
    page.locator('[data-header="filter"]').fill("")

    tile(page, DARK).locator('[data-control="r"]').fill("700")
    tile(page, DARK).locator('[data-control="r"]').dispatch_event("change")
    tile(page, CAGE).locator('[data-control="notes"]').fill("a note")
    page.locator('[data-header="edited"]').check()
    assert sorted(visible_ids(page)) == sorted([DARK, CAGE])
    page.locator('[data-header="edited"]').uncheck()
    assert visible_ids(page) == everything


def test_a_shared_colour_updates_both_cyber_cage_entries_and_survives_reload_and_reset(open_gallery):
    page = open_gallery()
    cage_before, trap_before = canvases(page, CAGE), canvases(page, TRAP)
    # The trap is a glyph badge, which ignores the ability colour, so the colour is edited on the trap and
    # watched on the smoke: the other entry's input and canvas both follow.
    set_colour(page, TRAP, "#ff0000")
    assert tile(page, CAGE).locator('[data-control="color"]').input_value() == "#ff0000"
    assert canvases(page, CAGE) != cage_before
    page.reload()
    settle(page)
    assert tile(page, CAGE).locator('[data-control="color"]').input_value() == "#ff0000"
    assert tile(page, TRAP).locator('[data-control="color"]').input_value() == "#ff0000"
    tile(page, CAGE).locator('[data-control="reset"]').click()
    page.wait_for_timeout(100)
    assert tile(page, CAGE).locator('[data-control="color"]').input_value() == ORIGINAL_CAGE
    assert tile(page, TRAP).locator('[data-control="color"]').input_value() == ORIGINAL_CAGE
    assert canvases(page, TRAP) == trap_before


def test_edits_persist_across_reload_and_clear_all_forgets_them(open_gallery):
    page = open_gallery()
    set_colour(page, DARK, "#00ff00")
    page.reload()
    settle(page)
    assert tile(page, DARK).locator('[data-control="color"]').input_value() == "#00ff00"
    page.locator('[data-header="clear"]').click()
    page.reload()
    settle(page)
    assert tile(page, DARK).locator('[data-control="color"]').input_value() == ORIGINAL_DARK
    assert page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('util-gallery:v1:'))") == []


def test_the_page_works_when_storage_throws(open_gallery):
    page = open_gallery(storage_throws=True)
    before = canvases(page, DARK)
    set_colour(page, DARK, "#00ff00")
    assert wait_changed(page, DARK, before) != before
