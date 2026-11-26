"""The /plants page: service (rows, cache, friends) and routes (demo, tabs, spots, links).

Routes are called directly with a hand-built Request (no TestClient), like
tests/test_account_groups_routes.py. Spots are patched in, so the committed
(empty) spots file does not matter.
"""

import re
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import settings
from app.db import Base
from app.models import (
    AccountGroup, AccountGroupMember, Friendship, Match, MatchPlayer, Player, Round, RoundPlant,
)
from app.models.match import MatchSource, Team
from app.models.replay import Replay
from app.models.round_plant import PlantStatsCache
from app.replays import layouts
from app.routers import plants as plants_router
from app.services import plant_page, plant_spots
from app.services.auth import SESSION_KEY, get_current_player
from app.templates import templates

TABLES = [Player, Match, MatchPlayer, Round, RoundPlant, PlantStatsCache, Replay, Friendship,
          AccountGroup, AccountGroupMember]

ONE_SPOT = {"Ascent": {"A": [{"id": "s1", "name": "Heaven", "u": 500, "v": 500, "r": 300}]}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///" + str(tmp_path / "plants.sqlite"))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in TABLES])
    with sessionmaker(bind=eng)() as session:
        def _current(request):
            return {"current_player": get_current_player(request, session)}

        monkeypatch.setattr(templates, "context_processors", [_current])
        monkeypatch.setattr(plant_spots, "load", lambda: ONE_SPOT)
        monkeypatch.setattr(plant_spots, "digest", lambda: "digest-1")
        yield session
    eng.dispose()


def _request(player=None, path="/plants", query=b""):
    session = {SESSION_KEY: player.id} if player is not None else {}
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": query, "headers": [],
        "scheme": "http", "server": ("testserver", 80), "session": session,
    })


def _body(response):
    return response.body.decode()


def _demo(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setitem(templates.env.globals, "demo_mode", True)


def _plants(db, n, *, start=0, map_name="Ascent", site="A", u=500, v=500, source="tracker",
            asset_key="Ascent", branch=None, uuid=None, team=None, player=None):
    """n plants, each in its own match (so each is its own cluster). Returns the matches."""
    made = []
    for i in range(start, start + n):
        match = Match(external_id=f"{map_name}-{i}", source=MatchSource.SCRAPED, map_name=map_name,
                      played_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        db.add(match)
        db.flush()
        if player is not None:
            db.add(MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett", team=team))
        rnd = Round(match_id=match.id, round_number=1, planted=True, plant_time=40.0)
        db.add(rnd)
        db.flush()
        db.add(RoundPlant(round_id=rnd.id, match_id=match.id, asset_key=asset_key, site=site, u=u, v=v,
                          source=source, round_number=1, plant_time=40.0, attackers_alive=4,
                          defenders_alive=3, attackers_won=(i % 2 == 0), defused=False))
        if branch is not None:
            db.add(Replay(match_uuid=uuid or f"00000000-0000-0000-0000-{i:012d}", match_id=match.id,
                          map_name=map_name, round_count=1, format_version=1, recipe="r",
                          game_branch=branch, source_sha256="x", source="upload",
                          link_status="linked", link_inputs={}, clock_offset=2.0))
        made.append(match)
    db.commit()
    return made


def _spot(db, spot_id="s1", asset_key="Ascent", site="A"):
    return plant_page.page_data(db, asset_key, None)["sites"][site]["spots"][0]


def _two_layouts(monkeypatch):
    def row(key, start):
        return {"id": key, "key": key, "start": start, "drawn_from": "13.08", "regions": None}

    registry = {"maps": {"Ascent": [row("Ascent", None), row("Ascent_14_0", "14.0")]}}
    monkeypatch.setattr(layouts, "load_registry", lambda path=None: registry)


# --- demo, nav ------------------------------------------------------------------


def test_demo_mode_is_404_on_both_routes(db, monkeypatch):
    _plants(db, 3)
    _demo(monkeypatch)
    with pytest.raises(HTTPException) as err:
        plants_router.plants_index(_request(), db=db)
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db)
    assert err.value.status_code == 404


def test_unknown_map_is_404(db):
    with pytest.raises(HTTPException) as err:
        plants_router.plants_map(_request(path="/plants/Nowhere"), "Nowhere", db=db)
    assert err.value.status_code == 404


def test_logged_out_nav_has_plants_and_demo_nav_does_not(db, monkeypatch):
    _plants(db, 3)
    page = _body(plants_router.plants_index(_request(), db=db))
    assert 'href="/plants"' in page
    _demo(monkeypatch)
    demo_nav = templates.get_template("base.html").render(current_player=None)
    assert 'href="/plants"' not in demo_nav


# --- what renders ---------------------------------------------------------------


def test_only_named_spots_render_and_the_outside_count_shows(db):
    _plants(db, 3)                      # inside Heaven
    _plants(db, 2, start=10, u=9000, v=9000)   # outside every named spot
    page = _body(plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db))
    assert 'data-spot="s1"' in page and "Heaven" in page
    assert len(re.findall(r'data-spot="', page)) == 2   # the circle and its table row, no others
    assert 'data-outside="2"' in page


def test_a_rotated_rectangle_spot_renders_and_counts_its_plants(db, monkeypatch):
    monkeypatch.setattr(plant_spots, "load", lambda: {"Ascent": {"A": [
        {"id": "s1", "name": "Heaven", "u": 500, "v": 500, "w": 400, "h": 200, "angle": 30}]}})
    _plants(db, 3)                             # inside the rectangle
    _plants(db, 2, start=10, u=500, v=690)     # past its short side
    page = _body(plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db))
    assert "width: 4.0%; height: 2.0%; border-radius: 0; transform: translate(-50%, -50%) rotate(30deg);" in page
    assert 'data-outside="2"' in page and "n=3" in page


def test_49_is_grey_without_a_percentage_and_50_has_a_signed_adjusted_value(db):
    assert plant_page.MIN_N == 50          # the owner's gate (2026-11-25), was 100
    _plants(db, 49)
    small = _spot(db)
    assert small["n"] == 49 and small["adjusted_display"] is None
    page = _body(plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db))
    assert "spot-grey" in page and 'data-adjusted=""' in page and "n=49" in page

    _plants(db, 1, start=49)
    big = _spot(db)
    assert big["n"] == 50
    assert re.fullmatch(r"[+-]\d+\.\d%", big["adjusted_display"])
    page = _body(plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db))
    assert re.search(r'data-adjusted="[+-]\d+\.\d%"', page)


# --- friends --------------------------------------------------------------------


def test_friends_keeps_the_circles_attacking_rounds_and_logged_out_gets_everyone(db):
    viewer, other = Player(display_name="Viewer#1"), Player(display_name="Stranger#2")
    db.add_all([viewer, other])
    db.flush()
    _plants(db, 1, start=0, player=viewer, team=Team.TEAM_1)    # round 1: team 1 attacks -> counts
    _plants(db, 1, start=1, player=viewer, team=Team.TEAM_2)    # defending -> out
    _plants(db, 1, start=2, player=other, team=Team.TEAM_1)     # not in the circle -> out
    everyone = plant_page.page_data(db, "Ascent", None)
    friends = plant_page.page_data(db, "Ascent", viewer.id)
    assert everyone["sites"]["A"]["n"] == 3
    assert friends["sites"]["A"]["n"] == 1

    out = _body(plants_router.plants_map(_request(None, "/plants/Ascent", b"view=friends"), "Ascent", db=db))
    assert 'data-n-site="3"' in out and "log in" in out.lower()
    inn = _body(plants_router.plants_map(_request(viewer, "/plants/Ascent", b"view=friends"), "Ascent", db=db))
    assert 'data-n-site="1"' in inn


def test_no_player_name_in_the_page(db):
    viewer = Player(display_name="Viewer#1")
    db.add(viewer)
    db.flush()
    _plants(db, 2, player=viewer, team=Team.TEAM_1)
    page = _body(plants_router.plants_map(_request(viewer, "/plants/Ascent", b"view=friends"), "Ascent", db=db))
    body = re.sub(r"<nav>.*?</nav>", "", page, flags=re.S)
    assert "Viewer" not in body


# --- cache ----------------------------------------------------------------------


def _counting(monkeypatch):
    calls = []
    real = plant_page._compute
    monkeypatch.setattr(plant_page, "_compute", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_cache_hit_recomputes_nothing_and_changes_recompute(db, monkeypatch):
    _plants(db, 3)
    calls = _counting(monkeypatch)
    first = plant_page.page_data(db, "Ascent", None)
    assert plant_page.page_data(db, "Ascent", None) == first
    assert len(calls) == 1

    _plants(db, 1, start=50)                          # a new row on this map
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 2

    _plants(db, 1, start=60, map_name="Bind", asset_key="Bind")   # a new row on another map
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 3

    monkeypatch.setattr(plant_spots, "digest", lambda: "digest-2")   # a changed spots file
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 4
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 4


def test_a_corrupt_blob_recomputes(db, monkeypatch):
    _plants(db, 3)
    plant_page.page_data(db, "Ascent", None)
    row = db.get(PlantStatsCache, ("Ascent", "all"))
    row.data = b"not zlib"
    db.commit()
    calls = _counting(monkeypatch)
    assert plant_page.page_data(db, "Ascent", None)["sites"]["A"]["n"] == 3
    assert len(calls) == 1


# --- R9 at read time --------------------------------------------------------------


def test_a_tracker_row_without_a_replay_is_not_counted_on_a_two_layout_map(db, monkeypatch):
    _two_layouts(monkeypatch)
    monkeypatch.setattr(plant_spots, "load", lambda: {
        "Ascent": ONE_SPOT["Ascent"], "Ascent_14_0": ONE_SPOT["Ascent"]})
    _plants(db, 2)                                         # tracker, no replay: layout unknown
    _plants(db, 1, start=10, branch="release-13.08")       # base layout
    _plants(db, 1, start=20, branch="release-14.00", asset_key="Ascent_14_0")
    assert plant_page.page_data(db, "Ascent", None)["sites"]["A"]["n"] == 1
    assert plant_page.page_data(db, "Ascent_14_0", None)["sites"]["A"]["n"] == 1


def test_a_changed_registry_or_a_newly_linked_replay_recomputes(db, monkeypatch):
    matches = _plants(db, 3)
    calls = _counting(monkeypatch)
    plant_page.page_data(db, "Ascent", None)
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 1

    monkeypatch.setattr(plant_page, "_registry_digest", lambda: "registry-2")
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 2

    db.add(Replay(match_uuid="00000000-0000-0000-0000-0000000000aa", match_id=matches[0].id,
                  map_name="Ascent", round_count=1, format_version=1, recipe="r",
                  game_branch="release-13.08", source_sha256="x", source="upload",
                  link_status="linked", link_inputs={}, clock_offset=2.0))
    db.commit()
    plant_page.page_data(db, "Ascent", None)
    assert len(calls) == 3


def _replay(db, match, uuid_tail, *, branch="release-13.08", offset=2.0):
    row = Replay(match_uuid=f"00000000-0000-0000-0000-{uuid_tail:012d}", match_id=match.id,
                 map_name="Ascent", round_count=1, format_version=1, recipe="r",
                 game_branch=branch, source_sha256="x", source="upload",
                 link_status="linked", link_inputs={}, clock_offset=offset)
    db.add(row)
    db.commit()
    return row


def _recomputes_after(db, monkeypatch, change):
    plant_page.page_data(db, "Ascent", None)
    calls = _counting(monkeypatch)
    plant_page.page_data(db, "Ascent", None)
    assert calls == []                                    # nothing changed: a cache hit
    change()
    db.commit()
    plant_page.page_data(db, "Ascent", None)
    return len(calls)


def test_a_changed_clock_offset_recomputes(db, monkeypatch):
    matches = _plants(db, 3, branch="release-13.08")
    row = db.query(Replay).filter_by(match_id=matches[0].id).one()
    assert _recomputes_after(db, monkeypatch, lambda: setattr(row, "clock_offset", 5.0)) == 1


def test_a_changed_game_branch_recomputes(db, monkeypatch):
    matches = _plants(db, 3, branch="release-13.08")
    row = db.query(Replay).filter_by(match_id=matches[0].id).one()
    assert _recomputes_after(db, monkeypatch, lambda: setattr(row, "game_branch", "release-13.09")) == 1


def test_an_unlink_plus_a_different_link_at_the_same_count_recomputes(db, monkeypatch):
    matches = _plants(db, 3)
    first = _replay(db, matches[0], 1)

    def swap():
        first.link_status = "unlinked"
        first.match_id = None
        _replay(db, matches[1], 2)

    assert _recomputes_after(db, monkeypatch, swap) == 1


def test_an_unrelated_replay_change_is_still_a_cache_hit(db, monkeypatch):
    matches = _plants(db, 3)
    other = _replay(db, matches[0], 1)
    other.link_status = "unlinked"
    other.match_id = None
    db.commit()
    assert _recomputes_after(db, monkeypatch, lambda: setattr(other, "source_sha256", "y")) == 0


def test_a_refresh_sets_computed_at_again(db):
    _plants(db, 3)
    plant_page.page_data(db, "Ascent", None)
    row = db.get(PlantStatsCache, ("Ascent", "all"))
    old = datetime(2020, 1, 1)
    row.computed_at = old
    row.fingerprint = "stale"
    db.commit()
    plant_page.page_data(db, "Ascent", None)
    db.expire_all()
    assert db.get(PlantStatsCache, ("Ascent", "all")).computed_at.replace(tzinfo=None) > old


# --- index cost, image, layout echo ----------------------------------------------


def test_counts_by_asset_is_one_aggregate_equal_to_read_rows(db, monkeypatch):
    _two_layouts(monkeypatch)
    _plants(db, 2)
    _plants(db, 3, start=10, branch="release-13.08")
    _plants(db, 4, start=20, branch="release-14.00", asset_key="Ascent_14_0")
    _plants(db, 1, start=30, map_name="Bind", asset_key="Bind")
    from collections import Counter
    expected = Counter(row["asset_key"] for row in plant_page.read_rows(db))
    assert expected == {"Ascent": 3, "Ascent_14_0": 4, "Bind": 1}
    assert plant_page.counts_by_asset(db) == expected


def test_the_index_never_loads_every_row(db, monkeypatch):
    _plants(db, 3)
    monkeypatch.setattr(plant_page, "read_rows", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rows")))
    page = _body(plants_router.plants_index(_request(), db=db))
    assert "n=3" in page


def test_a_layout_without_its_own_image_uses_the_base_map_image(db, monkeypatch):
    _two_layouts(monkeypatch)
    monkeypatch.setattr(plant_spots, "load", lambda: {"Ascent_14_0": ONE_SPOT["Ascent"]})
    page = _body(plants_router.plants_map(
        _request(path="/plants/Ascent", query=b"layout=Ascent_14_0"), "Ascent", db=db))
    assert "/static/img/maps/Ascent.png" in page
    assert "Ascent_14_0.png" not in page
    assert "layout=Ascent_14_0" in page          # a real layout key is carried in links


def test_an_arbitrary_layout_value_is_never_echoed(db):
    _plants(db, 3)
    page = _body(plants_router.plants_map(
        _request(path="/plants/Ascent", query=b"layout=%3Cscript%3E&site=A"), "Ascent", db=db))
    assert "%3Cscript" not in page and "&lt;script" not in page
    assert "layout=" not in page.split("<main>")[1]


# --- links ----------------------------------------------------------------------


def test_example_links_are_replay_deep_links(db):
    _plants(db, 4, branch="release-13.08")
    page = _body(plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db))
    links = re.findall(r'href="(/replays/[^"]+)"', page)
    assert 1 <= len(links) <= 3
    for link in links:
        assert re.fullmatch(r"/replays/[0-9a-f-]{36}\?round=1&amp;t=38\.0|/replays/[0-9a-f-]{36}\?round=1&t=38\.0", link)


def _moved_site(monkeypatch):
    from app.services import plant_site_changes
    monkeypatch.setattr(plant_site_changes, "cutoffs", lambda: {
        ("Bind", "B"): datetime(2025, 10, 14, tzinfo=timezone.utc)})
    monkeypatch.setattr(plant_site_changes, "digest", lambda: "moves-1")


def _played(db, matches, when):
    for match in matches:
        match.played_at = when
    db.commit()


def test_a_moved_site_counts_only_plants_from_its_move_on(db, monkeypatch):
    _moved_site(monkeypatch)
    _played(db, _plants(db, 2, map_name="Bind", site="B", asset_key="Bind"),
            datetime(2025, 10, 13, 23, tzinfo=timezone.utc))          # before: left out
    _plants(db, 3, start=10, map_name="Bind", site="B", asset_key="Bind")   # 2026-10-01: counted
    _played(db, _plants(db, 1, start=20, map_name="Bind", site="B", asset_key="Bind"), None)  # no date: kept
    _played(db, _plants(db, 4, start=30, map_name="Bind", site="A", asset_key="Bind"),
            datetime(2025, 1, 1, tzinfo=timezone.utc))                # the other site never moved
    from collections import Counter
    rows = plant_page.read_rows(db)
    assert Counter(row["site"] for row in rows) == {"B": 4, "A": 4}
    assert plant_page.counts_by_asset(db) == {"Bind": 8}


def test_a_new_move_recomputes(db, monkeypatch):
    from app.services import plant_site_changes
    _plants(db, 2)
    before = plant_page.fingerprint(db)
    monkeypatch.setattr(plant_site_changes, "digest", lambda: "moves-2")
    assert plant_page.fingerprint(db) != before


def test_the_committed_moves_file_reads(monkeypatch):
    from app.services import plant_site_changes
    plant_site_changes.cutoffs.cache_clear()
    moves = plant_site_changes.cutoffs()
    assert moves[("Abyss", "B")] == datetime(2025, 10, 14, tzinfo=timezone.utc)
    assert plant_site_changes.counts("Abyss", "B", datetime(2025, 10, 14)) is True       # naive is UTC
    assert plant_site_changes.counts("Abyss", "B", datetime(2025, 10, 13, 23, 59)) is False
    assert plant_site_changes.counts("Abyss", "A", datetime(2020, 1, 1)) is True
