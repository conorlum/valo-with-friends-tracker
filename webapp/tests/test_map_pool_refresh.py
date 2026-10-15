"""The map pool's daily refresh: fake fetcher only, no network."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.map_pool import MapPoolChange, MapPoolPatchNotes
from app.models.match import Match
from app.services import map_pool, map_pool_refresh as refresh
from app.services.patch_notes import parse_listing, parse_patch_notes

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_cache():
    map_pool.clear_pool_cache()
    refresh._last_check = None
    yield
    map_pool.clear_pool_cache()
    refresh._last_check = None


@pytest.fixture
def engine(tmp_path):
    eng = create_engine("sqlite:///" + str(tmp_path / "pool.sqlite"))
    Base.metadata.create_all(eng, tables=[MapPoolChange.__table__, MapPoolPatchNotes.__table__, Match.__table__])
    yield eng
    eng.dispose()


@pytest.fixture
def sf(engine):
    return sessionmaker(bind=engine)


def url_for(patch):
    return "https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-" + patch.replace(".", "-")


def listing(items):
    """items: (patch, datetime) pairs."""
    data = [
        {"title": f"VALORANT Patch Notes {p}", "publishedAt": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
         "action": {"payload": {"url": url_for(p).replace("https://playvalorant.com", "")}}}
        for p, t in items
    ]
    blob = json.dumps({"props": {"pageProps": {"page": {"blades": [{"items": data}]}}}})
    return f'<html><script id="__NEXT_DATA__" type="application/json">{blob}</script></html>'


def page(lines, when=None):
    ld = ""
    if when is not None:
        blob = json.dumps({"@type": "TechArticle", "datePublished": when.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        ld = f'<script type="application/ld+json">{blob}</script>'
    return ld + "".join(f"<p>{x}</p>" for x in lines)


CHANGE_PAGE = page(["Ascent is IN the Competitive queue.", "Fracture is OUT of the Competitive queue."])
NO_CHANGE_PAGE = page(["Nothing about maps here."])
MISS_PAGE = "<ul><li>Map Pool Updates</li><li>Ascent joins the Competitive rotation.</li></ul>"
# The OUT line sits in a bare <div>, which the parser doesn't read: only the IN line comes back.
PARTIAL_PAGE = ("<ul><li>Map Pool Updates</li><li>Ascent is IN the Competitive queue.</li></ul>"
                "<div>Fracture is OUT of the Competitive queue.</div>")


class Fake:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise OSError("unknown " + url)
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return v


def t(day, month=11):
    return datetime(2026, month, day, 13, 0, tzinfo=UTC)


def make(items, pages):
    return Fake({refresh.LISTING_URL: listing(items), **{url_for(p): h for p, h in pages.items()}})


def test_sanity_parsers_accept_the_synthetic_html():
    assert len(parse_listing(listing([("13.09", t(1))]))) == 1
    assert parse_patch_notes(CHANGE_PAGE).changes
    assert parse_patch_notes(MISS_PAGE).is_miss
    assert not parse_patch_notes(NO_CHANGE_PAGE).changes


def test_newest_first_and_cap_of_five(sf):
    items = [(f"13.{10 + i}", t(1 + i)) for i in range(7)]
    fake = make(items, {p: NO_CHANGE_PAGE for p, _ in items})
    counts = refresh.check(sf, fake, now=NOW)
    pages_fetched = fake.calls[1:]
    assert pages_fetched == [url_for(f"13.{10 + i}") for i in (6, 5, 4, 3, 2)]
    assert counts["listed"] == 7 and counts["read"] == 5


def test_pre_seed_item_is_ignored(sf):
    fake = make([("13.06", datetime(2026, 9, 22, 13, tzinfo=UTC)), ("13.08", datetime(2026, 10, 13, 13, tzinfo=UTC))],
                {"13.06": CHANGE_PAGE, "13.08": CHANGE_PAGE})
    refresh.check(sf, fake, now=NOW)
    assert fake.calls == [refresh.LISTING_URL]


def test_miss_is_stored_retried_and_given_up_after_seven_days(sf):
    fake = make([("13.10", t(1))], {"13.10": MISS_PAGE})
    now1 = t(2)
    c = refresh.check(sf, fake, now=now1)
    assert c["misses"] == 1
    with sf() as s:
        row = s.get(MapPoolPatchNotes, url_for("13.10"))
        assert row.read_at is None and row.miss_count == 1 and row.last_error == "parser miss"
    refresh.check(sf, fake, now=now1 + timedelta(days=1))
    assert fake.calls.count(url_for("13.10")) == 2
    with sf() as s:
        assert s.get(MapPoolPatchNotes, url_for("13.10")).miss_count == 2
    c = refresh.check(sf, fake, now=t(1) + timedelta(days=8))
    assert c["given_up"] == 1
    with sf() as s:
        assert s.get(MapPoolPatchNotes, url_for("13.10")).last_error == "given up: parser miss"
    n = fake.calls.count(url_for("13.10"))
    refresh.check(sf, fake, now=t(1) + timedelta(days=9))
    assert fake.calls.count(url_for("13.10")) == n


def test_given_up_rows_dropped_before_the_cap(sf):
    items = [(f"13.{10 + i}", t(1 + i)) for i in range(6)]
    with sf() as s:
        s.add(MapPoolPatchNotes(url=url_for("13.15"), patch="13.15", published_at=t(6),
                                last_error="given up: parser miss"))
        s.commit()
    fake = make(items, {p: NO_CHANGE_PAGE for p, _ in items})
    refresh.check(sf, fake, now=NOW)
    assert url_for("13.15") not in fake.calls
    assert len(fake.calls) == 6  # listing + the other five


def test_no_change_page_is_read_and_not_fetched_again(sf):
    fake = make([("13.10", t(1))], {"13.10": NO_CHANGE_PAGE})
    refresh.check(sf, fake, now=NOW)
    with sf() as s:
        row = s.get(MapPoolPatchNotes, url_for("13.10"))
        assert row.read_at is not None and row.change_count == 0
    refresh.check(sf, fake, now=NOW + timedelta(days=1))
    assert fake.calls.count(url_for("13.10")) == 1


def test_stored_change_shows_in_history_and_clears_memo(sf):
    when = t(1)
    with sf() as db:
        before = map_pool.load_pool_history(db)  # populates the memo
        was_in = "Ascent" in before.pool_at(NOW)
        swap = (["Ascent is OUT of the Competitive queue.", "Fracture is IN the Competitive queue."] if was_in
                else ["Ascent is IN the Competitive queue.", "Fracture is OUT of the Competitive queue."])
        fake = make([("13.10", when)], {"13.10": page(swap)})
        refresh.check(sf, fake, now=NOW)
        after = map_pool.load_pool_history(db)
        assert ("Ascent" in after.pool_at(NOW)) != was_in
    with sf() as s:
        rows = s.execute(select(MapPoolChange)).scalars().all()
        assert {r.map_name for r in rows} == {"Ascent", "Fracture"}
        assert all(r.source == "patch_notes" and r.patch == "13.10" and r.source_url == url_for("13.10")
                   for r in rows)


def test_published_at_from_page_wins_over_listing(sf):
    page_time = t(3)
    fake = make([("13.10", t(1))], {"13.10": page(["Ascent is IN the Competitive queue.",
                                                   "Fracture is OUT of the Competitive queue."], when=page_time)})
    refresh.check(sf, fake, now=NOW)
    with sf() as s:
        effs = set(s.execute(select(MapPoolChange.effective_at)).scalars())
        assert {e.replace(tzinfo=UTC) for e in effs} == {page_time}


def test_unbalanced_page_is_a_miss_and_stores_nothing(sf):
    assert parse_patch_notes(PARTIAL_PAGE).changes  # the parser does find the IN line
    fake = make([("13.10", t(1))], {"13.10": PARTIAL_PAGE})
    c = refresh.check(sf, fake, now=t(2))
    assert c["misses"] == 1 and c["changes"] == 0
    with sf() as s:
        assert s.execute(select(MapPoolChange)).all() == []
        row = s.get(MapPoolPatchNotes, url_for("13.10"))
        assert row.read_at is None and row.change_count is None
    refresh.check(sf, fake, now=t(3))
    assert fake.calls.count(url_for("13.10")) == 2  # retried, like any miss


def test_stop_during_a_fetch_writes_nothing(sf):
    stop = threading.Event()
    fake = make([("13.10", t(1))], {"13.10": CHANGE_PAGE})

    def fetch(url):
        out = fake(url)
        if url != refresh.LISTING_URL:
            stop.set()  # shutdown arrives while the page is being fetched
        return out

    refresh.check(sf, fetch, now=NOW, stop=stop)
    with sf() as s:
        assert s.execute(select(MapPoolChange)).all() == []
        assert s.execute(select(MapPoolPatchNotes)).all() == []


def test_stop_before_a_page_skips_its_fetch(sf):
    stop = threading.Event()
    fake = make([("13.10", t(1)), ("13.11", t(2))], {"13.10": CHANGE_PAGE, "13.11": NO_CHANGE_PAGE})

    def fetch(url):
        out = fake(url)
        stop.set()  # set during the listing fetch
        return out

    refresh.check(sf, fetch, now=NOW, stop=stop)
    assert fake.calls == [refresh.LISTING_URL]


def test_duplicate_inserts_do_not_raise(sf):
    fake = make([("13.10", t(1))], {"13.10": CHANGE_PAGE})
    refresh.check(sf, fake, now=NOW)
    with sf() as s:
        s.execute(text("UPDATE map_pool_patch_notes SET read_at = NULL"))
        s.commit()
    c = refresh.check(sf, fake, now=NOW)
    assert c["errors"] == 0 and c["read"] == 1
    with sf() as s:
        assert len(s.execute(select(MapPoolChange)).all()) == 2


def test_start_none_when_off_or_demo(monkeypatch, sf):
    monkeypatch.setattr(refresh.settings, "map_pool_auto", False)
    monkeypatch.setattr(refresh.settings, "demo_mode", False)
    assert refresh.start(sf) is None
    monkeypatch.setattr(refresh.settings, "map_pool_auto", True)
    monkeypatch.setattr(refresh.settings, "demo_mode", True)
    assert refresh.start(sf) is None
    assert not refresh.enabled()


def test_listing_fetch_error_changes_nothing(sf):
    c = refresh.check(sf, Fake({}), now=NOW)
    assert c["errors"] == 1
    with sf() as s:
        assert s.execute(select(MapPoolPatchNotes)).all() == []
        assert s.execute(select(MapPoolChange)).all() == []


def test_page_fetch_error_leaves_page_unread(sf):
    fake = make([("13.10", t(1)), ("13.11", t(2))], {"13.11": OSError("boom"), "13.10": NO_CHANGE_PAGE})
    c = refresh.check(sf, fake, now=NOW)
    assert c["errors"] == 1 and c["read"] == 1
    with sf() as s:
        bad = s.get(MapPoolPatchNotes, url_for("13.11"))
        assert bad is None or bad.read_at is None
        assert s.get(MapPoolPatchNotes, url_for("13.10")).read_at is not None


def test_run_forever_skips_a_check_under_24h_after_the_last(sf):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        raise OSError("x")

    stop = threading.Event()
    th = threading.Thread(target=refresh.run_forever, args=(sf, fake_fetch, stop, 0.01, 0), daemon=True)
    th.start()
    deadline = threading.Event()
    deadline.wait(0.3)
    stop.set()
    th.join(2)
    assert calls == [refresh.LISTING_URL]  # later iterations (every 0.01 s) were skipped by the 24 h gate
