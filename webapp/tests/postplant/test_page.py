"""The post-plant map page: loader, cache, route and template, on SQLite with seeded rows.

Routes are called directly with a hand-built Request, like tests/test_plants_page.py. The gap run's
"current" test is patched to a per-round fingerprint the test controls (the real one needs a control
geometry); the figures are the real ones, with area names and kill swings patched in by u.
"""

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import settings
from app.coach import areas
from app.db import Base
from app.models import (
    AccountGroup, AccountGroupMember, Friendship, Match, MatchPlayer, Player, Round, RoundPlant,
)
from app.models.match import MatchSource, Team
from app.models.replay import Replay, ReplayPlayer, UtilModel
from app.models.replay_postplant import PostplantStatsCache, ReplayRoundPostplant
from app.models.round_plant import PlantStatsCache
from app.replays import layouts
from app.routers import plants as plants_router
from app.services import plant_page, plant_spots, postplant_page, postplant_stats, replay_gaps, util_model
from app.services.auth import SESSION_KEY, get_current_player
from app.templates import templates

TABLES = [Player, Match, MatchPlayer, Round, RoundPlant, PlantStatsCache, Replay, ReplayPlayer,
          ReplayRoundPostplant, PostplantStatsCache, UtilModel, Friendship, AccountGroup,
          AccountGroupMember]

SPOTS = {"Ascent": {"A": [{"id": "s1", "name": "Default plant", "u": 3500, "v": 1400, "r": 300}],
                    "B": [{"id": "s2", "name": "Back plant", "u": 2800, "v": 7400, "r": 300}]}}
A_PLANT = (3500, 1400)       # nearest "A Site" callout, inside s1
A_FAR = (3900, 1600)         # still site A, outside s1
B_PLANT = (2800, 7400)
NAMES = {100: "Heaven", 200: "Rafters"}
CALLOUTS = {"Ascent": [{"name": "Heaven", "u": 3000, "v": 2000, "super": "A"},
                       {"name": "Rafters", "u": 4000, "v": 2500, "super": "A"}]}
FP = {}                      # (replay id, round) -> the gap fingerprint the round is "now" at


@pytest.fixture
def db(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///" + str(tmp_path / "pp.sqlite"))
    Base.metadata.create_all(eng, tables=[t.__table__ for t in TABLES])
    FP.clear()
    with sessionmaker(bind=eng)() as session:
        monkeypatch.setattr(templates, "context_processors",
                            [lambda request: {"current_player": get_current_player(request, session)}])
        monkeypatch.setattr(plant_spots, "load", lambda: SPOTS)
        monkeypatch.setattr(plant_spots, "digest", lambda: "digest-1")
        monkeypatch.setattr(areas, "load_painted", lambda key, *a, **k: None)
        monkeypatch.setattr(areas, "_table", lambda: CALLOUTS)
        monkeypatch.setattr(areas, "area_at", lambda key, u, v, z, *, painted_table:
                            {"name": NAMES[u]} if u in NAMES else None)
        monkeypatch.setattr(postplant_stats.util_values, "swings", lambda data, model:
                            {(round(k["t"], 2), k["killer"], k["victim"]): 0.10 for k in data["kills"]})
        monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP.get((replay.id, n)))
        yield session
    eng.dispose()


def _request(player=None, path="/plants/Ascent/postplant", query=b""):
    session = {SESSION_KEY: player.id} if player is not None else {}
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query, "headers": [],
                    "scheme": "http", "server": ("testserver", 80), "session": session})


def _page(db, query=b"", player=None, map_name="Ascent"):
    response = plants_router.postplant_map(_request(player, query=query), map_name, db=db)
    return response.body.decode()


def _body(page):
    return re.sub(r"<nav>.*?</nav>", "", page, flags=re.S)


def _model(db):
    db.merge(UtilModel(name="win", version=util_model.VERSION, key="model-1", data={"key": "model-1"}))
    db.commit()


def event(t=10.2, killer=0, victim=5, ku=100, vu=200):
    return {"t": t, "killer": killer, "victim": victim, "k_side": "attack", "v_side": "defense",
            "before": [5, 5], "ku": ku, "kv": 100, "kz": 20, "vu": vu, "vv": 100, "vz": 30, "k_line": 1}


def round_data(samples, kills=(), plant=A_PLANT, sides_known=True):
    return {"v": 1, "plant": {"t": 10, "u": plant[0], "v": plant[1], "by": 0}, "end": 12, "step": 0.5,
            "sides": {str(s): "attack" if s < 5 else "defense" for s in range(10)},
            "sides_known": sides_known, "samples": samples, "kills": list(kills)}


def seed(db, i, *, samples=None, kills=(), plant=A_PLANT, branch="release-13.08", players=None,
         revision=None, sides_known=True, offset=2.0, map_name="Ascent", fingerprint=None):
    """One replay of one round with a stored post-plant row on a current fingerprint. `players` maps slot ->
    Player (linked through a MatchPlayer); other slots stay unlinked."""
    match = Match(external_id=f"{map_name}-{i}", source=MatchSource.SCRAPED, map_name=map_name,
                  played_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    db.add(match)
    db.flush()
    replay = Replay(match_uuid=f"00000000-0000-0000-0000-{i:012d}", match_id=match.id, map_name=map_name,
                    round_count=1, format_version=1, recipe="r", game_branch=branch, source_sha256="x",
                    source="upload", link_status="linked", link_inputs={}, clock_offset=offset,
                    linked_at=datetime(2026, 10, 2, tzinfo=timezone.utc))
    db.add(replay)
    db.flush()
    for slot in range(10):
        mp_id = None
        if players and slot in players:
            mp = MatchPlayer(match_id=match.id, player_id=players[slot].id, agent="Jett",
                             team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
            db.add(mp)
            db.flush()
            mp_id = mp.id
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent="Jett", match_player_id=mp_id))
    FP[(replay.id, 1)] = f"fp-{replay.id}"
    data = round_data([[10, 0, 100, 100, 20, 1]] if samples is None else samples, kills, plant, sides_known)
    db.add(ReplayRoundPostplant(
        replay_id=replay.id, round_number=1, status="ok", fingerprint=fingerprint or f"fp-{replay.id}",
        gaps_revision=replay_gaps.GAPS_REVISION if revision is None else revision, data=data,
        computed_at=datetime(2026, 10, 3, tzinfo=timezone.utc)))
    db.commit()
    return replay


def _player(db, name="Zorblax#1"):
    player = Player(display_name=name)
    db.add(player)
    db.flush()
    return player


def _row(page, area="Heaven"):
    found = re.search(rf'<tr data-area="{area}".*?</tr>', page, flags=re.S)
    return found.group(0) if found else None


def _n(page, area="Heaven"):
    found = re.search(rf'<tr data-area="{area}" data-n="(\d+)"', page)
    return int(found.group(1)) if found else None


def _two_layouts(monkeypatch):
    def row(key, start):
        return {"id": key, "key": key, "start": start, "drawn_from": "13.08", "regions": None}

    registry = {"maps": {"Ascent": [row("Ascent", None), row("Ascent_14_0", "14.0")]}}
    monkeypatch.setattr(layouts, "load_registry", lambda path=None: registry)


# --- routes ---------------------------------------------------------------------


def test_demo_mode_and_unknown_map_are_404(db, monkeypatch):
    seed(db, 1)
    with pytest.raises(HTTPException) as err:
        plants_router.postplant_map(_request(path="/plants/Nowhere/postplant"), "Nowhere", db=db)
    assert err.value.status_code == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setitem(templates.env.globals, "demo_mode", True)
    with pytest.raises(HTTPException) as err:
        plants_router.postplant_map(_request(), "Ascent", db=db)
    assert err.value.status_code == 404


def test_map_page_has_a_post_plant_tab_and_the_tab_row(db):
    seed(db, 1)
    page = plants_router.plants_map(_request(path="/plants/Ascent"), "Ascent", db=db).body.decode()
    assert 'href="/plants/Ascent/postplant"' in page and "Post-plant" in page
    own = _page(db)
    assert 'href="/plants/Ascent"' in own and "Plant spots" in own
    assert re.search(r'class="tab-btn active"[^>]*>Post-plant<', own)


def test_the_route_is_listed_in_site_health():
    from app.services import site_health

    assert site_health.PAGE_ROUTES["/plants/{map_name}/postplant"] == "page"


# --- what counts (R8, D17) -------------------------------------------------------


def test_read_rounds_can_be_limited_to_some_replays(db):
    first, second = seed(db, 1), seed(db, 2)
    both = postplant_page.read_rounds(db, "Ascent")
    assert {r.replay_uuid for r in both} == {first.match_uuid, second.match_uuid}
    only = postplant_page.read_rounds(db, "Ascent", replay_ids={first.id})
    assert [r.replay_uuid for r in only] == [first.match_uuid]
    assert postplant_page.read_rounds(db, "Ascent", replay_ids=set()) == []


def test_a_current_row_shows_its_area_with_n(db):
    seed(db, 1)
    page = _page(db)
    assert _n(page) == 1
    assert "n=1" in _row(page)


def test_old_revision_and_changed_control_inputs_are_not_counted(db):
    seed(db, 1)
    seed(db, 2, revision=replay_gaps.GAPS_REVISION - 1)
    assert _n(_page(db)) == 1
    stale = seed(db, 3)
    FP[(stale.id, 1)] = "the-control-inputs-changed"          # the post-plant row is untouched
    assert _n(_page(db)) == 1


def test_pending_and_unknown_layouts_are_not_counted_anywhere(db, monkeypatch):
    _two_layouts(monkeypatch)
    seed(db, 1, branch="release-13.08")
    seed(db, 2, branch="release-14.00")                       # Ascent_14_0 has no control assets: pending
    seed(db, 3, branch="a-dev-branch")                        # no patch: unknown
    assert _n(_page(db, b"layout=Ascent")) == 1
    assert _page(db, b"layout=Ascent_14_0").count("data-area=") == 0


def test_a_round_whose_sides_are_unknown_is_not_counted(db):
    seed(db, 1)
    seed(db, 2, sides_known=False)
    assert _n(_page(db)) == 1


# --- the table and the map -------------------------------------------------------


def test_unnamed_is_in_the_table_with_no_marker(db):
    seed(db, 1, samples=[[10, 0, 100, 100, 20, 1], [10.5, 0, 999, 100, 20, 1]])
    page = _page(db)
    assert _row(page, "Unnamed") is not None
    assert 'data-marker="Unnamed"' not in page and 'data-marker="Heaven"' in page


@pytest.mark.parametrize("measure", ["kills", "net", "gained", "survival", "line", "crossfire"])
def test_n_19_is_grey_and_n_20_is_coloured(db, measure):
    _model(db)
    for i in range(19):
        seed(db, i)
    marker = re.search(r'<div[^>]*data-marker="Heaven"[^>]*>', _page(db, f"measure={measure}".encode()))[0]
    assert "spot-grey" in marker and 'data-coloured="0"' in marker
    seed(db, 19)
    marker = re.search(r'<div[^>]*data-marker="Heaven"[^>]*>', _page(db, f"measure={measure}".encode()))[0]
    assert "spot-grey" not in marker and 'data-coloured="1"' in marker


def test_every_figure_cell_shows_its_n_with_signed_win_figures(db):
    _model(db)
    for i in range(2):
        seed(db, i, kills=[event()])
    row = _row(_page(db), "Heaven")
    cells = re.findall(r'<td data-fig="[^"]+"[^>]*>(.*?)</td>', row, flags=re.S)
    assert len(cells) >= 9 and all("n=" in cell for cell in cells), cells
    assert re.search(r'data-fig="net"[^>]*>\s*[+-]\d+\.\d pts', row)
    assert re.search(r'data-fig="gained"[^>]*>\s*[+-]\d+\.\d pts', row)


def test_intervals_need_two_matches(db):
    _model(db)
    seed(db, 1, kills=[event()])
    assert _row(_page(db), "Heaven").count("needs 2 matches") == 3
    seed(db, 2, kills=[event(), event(t=11.0, victim=6)])
    row = _row(_page(db), "Heaven")
    assert "needs 2 matches" not in row
    assert all(f'data-ci="{field}"' in row for field in ("kills", "gained", "net"))


def test_no_player_name_in_the_page(db):
    viewer = _player(db, "Zorblax#1")
    seed(db, 1, players={0: viewer})
    page = _page(db, b"view=friends", player=viewer)
    assert "Zorblax" not in _body(page) and _n(page) == 1


# --- populations (D16) -----------------------------------------------------------


def test_friends_logged_out_is_everyone_with_the_note_and_logged_in_counts_the_circle(db):
    viewer, stranger = _player(db, "Viewer#1"), _player(db, "Stranger#2")
    seed(db, 1, samples=[[10, 0, 100, 100, 20, 1], [10, 1, 100, 100, 20, 1]],
         players={0: viewer, 1: stranger})
    out = _page(db, b"view=friends")
    assert _n(out) == 2 and plants_router.LOGIN_NOTE in out
    inn = _page(db, b"view=friends", player=viewer)
    assert _n(inn) == 1 and plants_router.LOGIN_NOTE not in inn
    assert _n(_page(db, b"view=friends", player=_player(db, "Nobody#3"))) is None


# --- spots (D7) ------------------------------------------------------------------


def test_spot_tabs_list_only_the_sites_named_spots_and_filter_n(db, monkeypatch):
    seed(db, 1, plant=A_PLANT)
    seed(db, 2, plant=A_PLANT)
    seed(db, 3, plant=A_FAR)                                  # site A, outside every named spot
    seed(db, 4, plant=B_PLANT)
    page = _page(db, b"site=A")
    assert "spot=s1" in page and "spot=s2" not in page and "Back plant" not in page
    assert _n(page) == 3
    assert _n(_page(db, b"site=A&spot=s1")) == 2
    assert _n(_page(db, b"site=B&spot=s2")) == 1
    assert 'data-spot-tabs' not in _page(db)                  # no site chosen: site tabs only
    monkeypatch.setattr(plant_spots, "load", lambda: {})
    assert 'data-spot-tabs' not in _page(db, b"site=A") and _n(_page(db, b"site=A")) == 3


# --- the win model (D14) ---------------------------------------------------------


def test_without_a_win_model_the_win_columns_say_so_and_net_falls_back_to_kills(db):
    seed(db, 1, kills=[event()])
    page = _page(db, b"measure=net")
    assert 'data-active-measure="kills"' in page and "No win model" in page
    assert re.search(r'data-fig="net"[^>]*>\s*no win model', _row(page))
    _model(db)
    assert 'data-active-measure="net"' in _page(db, b"measure=net")


# --- the cache (D18, R15) ---------------------------------------------------------


def _counting(monkeypatch):
    calls = []
    real = postplant_page._compute
    monkeypatch.setattr(postplant_page, "_compute", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_a_second_request_reads_the_stored_row(db, monkeypatch):
    seed(db, 1)
    calls = _counting(monkeypatch)
    first = _page(db)
    assert _page(db) == first and calls == [1]
    row = db.get(PostplantStatsCache, "Ascent|everyone|all|attack|all")
    assert row is not None and row.fingerprint == postplant_page.fingerprint(db, "Ascent", None)
    seed(db, 2)                                               # a new current row: recompute
    assert _n(_page(db)) == 2 and calls == [1, 1]


def test_the_fingerprint_follows_paint_links_and_clock_offsets(db, monkeypatch):
    replay = seed(db, 1, players={0: _player(db)})
    base = postplant_page.fingerprint(db, "Ascent", None)
    assert postplant_page.fingerprint(db, "Ascent", None) == base
    monkeypatch.setattr(areas, "load_painted", lambda key, *a, **k: SimpleNamespace(raw=b"paint"))
    painted = postplant_page.fingerprint(db, "Ascent", None)
    monkeypatch.setattr(areas, "load_painted", lambda key, *a, **k: SimpleNamespace(raw=b"paint2"))
    assert len({base, painted, postplant_page.fingerprint(db, "Ascent", None)}) == 3
    monkeypatch.setattr(areas, "load_painted", lambda key, *a, **k: None)
    assert postplant_page.fingerprint(db, "Ascent", None) == base

    other = _player(db, "Other#2")
    mp = MatchPlayer(match_id=replay.match_id, player_id=other.id, agent="Jett", team=Team.TEAM_1)
    db.add(mp)
    db.flush()
    db.query(ReplayPlayer).filter_by(replay_id=replay.id, slot=0).update({"match_player_id": mp.id})
    db.commit()
    relinked = postplant_page.fingerprint(db, "Ascent", None)
    assert relinked != base
    replay.clock_offset = 5.0                                 # the gap rows are unchanged
    db.commit()
    assert postplant_page.fingerprint(db, "Ascent", None) not in (base, relinked)
    assert postplant_page.fingerprint(db, "Ascent", {1}) != postplant_page.fingerprint(db, "Ascent", None)


def test_the_win_model_and_a_stale_row_change_what_is_cached(db):
    seed(db, 1)
    before = postplant_page.fingerprint(db, "Ascent", None)
    _model(db)
    assert postplant_page.fingerprint(db, "Ascent", None) != before
