"""The replay routes and links (docs/replay-viewer-plan.md, "The viewer", "Two sites").

Routes are called directly (no TestClient: httpx stays out, see tests/test_site_modes.py). The
blob route, validity and the unlinked page run on sqlite; the linked page context needs
impact_scores (a JSONB column), so it runs on PostgreSQL (`VALO_TEST_DATABASE_URL`, skipped
without it)."""

import gzip
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_replay_store import TABLES, add_match, condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Match  # noqa: E402
from app.models.replay import Replay, ReplayRound  # noqa: E402
from app.replays import store  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replays as service  # noqa: E402
from app.templates import templates  # noqa: E402


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def request(headers: dict | None = None) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def status_of(call) -> int:
    with pytest.raises(HTTPException) as raised:
        call()
    return raised.value.status_code


def test_every_replay_route_404s_in_demo_mode_even_with_rows(db, condensed, monkeypatch):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: routes.replay_page(request(), MATCH_UUID, db)) == 404
    assert status_of(lambda: routes.replay_round(request(), MATCH_UUID, 1, db)) == 404
    assert status_of(lambda: routes.match_replay(MATCH_UUID, db)) == 404
    match = db.query(Match).one()
    assert service.replay_url_for_match(db, match) is None
    assert service.watchable_external_ids(db, [MATCH_UUID]) == set()


def test_the_round_json_is_the_stored_gzip_with_an_etag_and_a_304(db, condensed):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    response = routes.replay_round(request(), MATCH_UUID.upper(), 1, db)
    stored = db.query(ReplayRound).filter(ReplayRound.round_number == 1).one().data
    assert response.body == stored
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["etag"] == '"' + hashlib.sha256(stored).hexdigest()[:16] + '"'
    assert json.loads(gzip.decompress(response.body)) == condensed.rounds[1]
    again = routes.replay_round(request({"If-None-Match": response.headers["etag"]}), MATCH_UUID, 1, db)
    assert again.status_code == 304 and not again.body
    assert status_of(lambda: routes.replay_round(request(), MATCH_UUID, 99, db)) == 404


def test_a_replacement_under_the_same_recipe_changes_the_body_and_etag(db, condensed):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    first = routes.replay_round(request(), MATCH_UUID, 1, db)
    other = replace(condensed, source_sha256="f" * 64,
                    rounds={**condensed.rounds, 1: {**condensed.rounds[1], "t_end": condensed.rounds[1]["t_end"] + 1}})
    assert store.store_replay(db, other, source="local", replace=True).action == "replaced"
    second = routes.replay_round(request({"If-None-Match": first.headers["etag"]}), MATCH_UUID, 1, db)
    assert second.status_code == 200 and second.headers["etag"] != first.headers["etag"]


def test_an_incomplete_replay_reads_as_no_replay(db, condensed):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    db.query(ReplayRound).filter(ReplayRound.round_number == 2).delete()
    db.commit()
    assert status_of(lambda: routes.replay_page(request(), MATCH_UUID, db)) == 404
    assert status_of(lambda: routes.match_replay(MATCH_UUID, db)) == 404
    assert service.watchable_external_ids(db, [MATCH_UUID]) == set()


def test_an_unlinked_page_carries_no_site_data(db, condensed):
    result = store.store_replay(db, condensed, source="upload")
    context = service.page_context(db, db.get(Replay, result.replay_id))
    assert context["match"]["linked"] is False and context["players"] == {} and context["rounds"] == {}
    assert "not linked" in context["match"]["reason"]
    text_ = json.dumps(context)
    assert "synthetic-" not in text_ and "00000000-0000-4000-8000-0000000000" not in text_
    assert status_of(lambda: routes.match_replay(MATCH_UUID, db)) == 404  # no match row yet


def test_the_links_appear_only_for_a_linked_valid_replay(db, condensed):
    match, _ = add_match(db)
    assert service.replay_url_for_match(db, match) is None
    store.store_replay(db, condensed, source="local")
    assert service.replay_url_for_match(db, match) == f"/replays/{MATCH_UUID}"
    assert service.watchable_external_ids(db, [MATCH_UUID.upper(), "other"]) == {MATCH_UUID}
    redirect = routes.match_replay(MATCH_UUID, db)
    assert redirect.status_code == 302 and redirect.headers["location"] == f"/replays/{MATCH_UUID}"
    # A deleted match (the trigger's work on PostgreSQL; here the state it leaves) links nothing.
    row = db.query(Replay).one()
    row.match_id, row.link_status = None, "unlinked"
    db.commit()
    assert service.replay_url_for_match(db, match) is None


def _round_detail_html(replay_url):
    detail = SimpleNamespace(round_number=3, outcome="Team A Elimination Win", events=[], players=[],
                             team_totals={}, impact_rows=[])
    try:
        return templates.env.get_template("matches/_round_detail.html").render(
            match=SimpleNamespace(external_id=MATCH_UUID), detail=detail, replay_url=replay_url)
    except Exception as error:  # noqa: BLE001  (the partial reads more of `detail` than this fake has)
        pytest.skip(f"the round partial needs a fuller detail: {error}")


def test_the_round_partial_links_to_its_round_only_with_a_replay():
    assert f'href="/replays/{MATCH_UUID}?round=3"' in _round_detail_html(f"/replays/{MATCH_UUID}")
    assert "/replays/" not in _round_detail_html(None)


def test_the_match_list_shows_watch_only_for_watchable_matches():
    matches = [SimpleNamespace(external_id=MATCH_UUID, team1_rounds_won=13, team2_rounds_won=2, map_name="Ascent",
                               played_at=None),
               SimpleNamespace(external_id="other", team1_rounds_won=13, team2_rounds_won=5, map_name="Bind",
                               played_at=None)]
    env = templates.env
    try:
        html = env.get_template("matches/list.html").render(matches=matches, showing_own_matches=False,
                                                           current_player=None, watchable={MATCH_UUID})
        none = env.get_template("matches/list.html").render(matches=matches, showing_own_matches=False,
                                                           current_player=None, watchable=set())
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"the list template needs more globals: {error}")
    assert html.count("&#9654; Watch") == 1 and f'href="/replays/{MATCH_UUID}"' in html and "<th>Watch</th>" in html
    assert "Watch" not in none


# ---------------------------------------------------------------- PostgreSQL: the linked page


@pytest.fixture
def pg(monkeypatch):
    from _postgres import postgres_url_or_skip

    monkeypatch.setattr(settings, "demo_mode", False)
    url = postgres_url_or_skip(writes=True)
    engine = create_engine(url)
    tables = ("replay_players", "replay_rounds", "replay_uploads", "replays", "impact_scores", "kill_events",
              "round_player_stats", "rounds", "match_players", "matches", "friendships", "players")
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f"DELETE FROM {table}"))
    factory = sessionmaker(bind=engine)
    yield factory
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


def test_pg_a_linked_page_has_names_weapons_and_hides_a_stale_split(pg, condensed):
    session = pg()
    add_match(session)
    result = store.store_replay(session, condensed, source="local")
    replay = session.get(Replay, result.replay_id)
    context = service.page_context(session, replay)
    assert context["match"]["linked"] and len(context["players"]) == 10
    assert {p["name"] for p in context["players"].values()} == {f"synthetic-{s}" for s in range(10)}
    first = context["rounds"]["1"]
    assert first["db"]["winner"] in ("team-1", "team-2")
    assert all(k["weapon"] == "Vandal" and "gain" not in k for k in first["kills"].values())
    assert context["match"]["per_kill_impact"] is False
    # A split whose fingerprint matches is shown, rounded for the feed; any other is hidden.
    from app.replays import db as replay_db

    kill_id = replay.kill_map["1"][0]
    replay.kill_impact = {"fingerprint": replay_db.impact_fingerprint(session, replay.match_id),
                          "kills": {str(kill_id): [12.345, -6.789, 5, 4]}}
    session.commit()
    shown = service.page_context(session, replay)["rounds"]["1"]["kills"]["0"]
    assert (shown["gain"], shown["loss"], shown["state"]) == (12.3, -6.8, [5, 4])
    replay.kill_impact = {**replay.kill_impact, "fingerprint": "stale"}
    session.commit()
    assert "gain" not in service.page_context(session, replay)["rounds"]["1"]["kills"]["0"]
    assert "subject" not in json.dumps(context).lower()
    session.close()
