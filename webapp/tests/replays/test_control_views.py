"""Map control's per-replay views from the stored summaries (docs/map-control-stages-4-7-impl.md,
S4.1 and S5.1): the player tables and their endpoint. SQLite, no engine: summaries are made up in
the shape app/control/encode.py stores."""

import base64
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.models.replay import ReplayRoundControl  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_views as views  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_heatmap_cache():
    # Rows in separate in-memory databases can share a replay id, fingerprint and (whole-second) time.
    views._HEATMAPS.clear()
    yield
    views._HEATMAPS.clear()


def player(slot, team, side, alive, active, passive, control, deaths=()):
    return {"slot": slot, "team": team, "side": side, "alive_s": alive, "active_m2s": active, "passive_m2s": passive,
            "control_m2s": control, "active_m2": active / alive if alive else None,
            "passive_m2": passive / alive if alive else None, "control_m2": control / alive if alive else None,
            "active_ratio": active / (active + passive) if active + passive else None, "deaths": list(deaths)}


def death(t, m2, share, live=True):
    return {"t": t, "live": live, "control_m2": m2, "share_of_team": share,
            "by_level_m2": {"active": m2, "safe": 0.0, "passive": 0.0, "not_held": 0.0},
            "went_to_m2": {"enemy": m2, "contested": 0.0, "nobody": 0.0}}


def summary(n, players, redundant, t_start=0.0, t_decided=40.0, group_side=None, sections=None):
    if sections is None:  # 10 s sections over the live round, like the engine's
        live = t_decided - t_start
        sections = [{"key": f"r{i}", "t0": t_start + 10 * i, "t1": min(t_start + 10 * (i + 1), t_decided),
                     "seconds": min(10.0, live - 10 * i), "totals": ""} for i in range(int(-(-live // 10)))]
    return {"revision": cf.CONTROL_REVISION, "map": "Ascent", "round": n, "cells": 4, "cell_m2": 1.0,
            "t_start": t_start, "t_decided": t_decided, "group_side": group_side or {"A": "attack", "B": "defense"},
            "sections": list(sections), "players": {str(p["slot"]): p for p in players}, "redundant_m2s": redundant,
            "missing_inputs": {}}


ROUND_1 = summary(1, [player(0, "A", "attack", 20.0, 400.0, 1200.0, 2000.0, [death(20.0, 30.0, 0.1)]),
                      player(5, "B", "defense", 40.0, 0.0, 0.0, -80.0)],
                  {"A": 800.0, "B": -400.0})
ROUND_2 = summary(2, [player(0, "A", "defense", 30.0, 600.0, 600.0, 1000.0, [death(35.0, 10.0, 0.3),
                                                                           death(50.0, 99.0, 0.9, live=False)]),
                      player(5, "B", "attack", 10.0, 100.0, 300.0, 500.0)],
                  {"A": 200.0, "B": 0.0}, t_start=5.0, t_decided=65.0, group_side={"A": "defense", "B": "attack"})


def test_a_round_is_averaged_over_its_own_times():
    table = views.round_table(ROUND_1)
    assert table["live_s"] == 40.0
    p0 = table["players"]["0"]
    assert p0["control_m2"] == 100.0 and p0["active_m2"] == 20.0 and p0["passive_m2"] == 60.0
    assert p0["active_ratio"] == 0.25
    assert p0["lost"] == [{"t": 20.0, "control_m2": 30.0, "share_of_team": 0.1,
                           "by_level_m2": ROUND_1["players"]["0"]["deaths"][0]["by_level_m2"],
                           "went_to_m2": ROUND_1["players"]["0"]["deaths"][0]["went_to_m2"]}]
    assert table["players"]["5"]["control_m2"] == -2.0
    assert table["players"]["5"]["active_ratio"] is None     # nothing covered: shown as a dash
    assert table["redundant_m2"] == {"A": 20.0, "B": -10.0}


def test_space_taken_when_the_summary_has_it():
    with_taken = summary(1, [dict(player(0, "A", "attack", 20.0, 1.0, 1.0, 1.0), taken_m2=30.0),
                             player(5, "B", "defense", 20.0, 1.0, 1.0, 1.0)], {})
    other = summary(2, [dict(player(0, "A", "defense", 20.0, 1.0, 1.0, 1.0), taken_m2=10.0)], {})
    assert views.round_table(with_taken)["players"]["0"]["taken_m2"] == 30.0
    assert views.round_table(with_taken)["players"]["5"]["taken_m2"] is None     # a revision-1 row
    match = views.match_table({1: with_taken, 2: other})["players"]
    assert (match["0"]["taken_m2"], match["0"]["taken_per_round_m2"]) == (40.0, 20.0)
    assert match["5"]["taken_m2"] is None and match["5"]["taken_per_round_m2"] is None


def test_the_live_time_comes_from_the_sections_not_t_decided():
    body = dict(ROUND_1, t_decided=None)
    assert views.round_table(body)["live_s"] == 40.0
    assert views.round_table(body)["redundant_m2"] == {"A": 20.0, "B": -10.0}
    assert views.round_table(dict(ROUND_1, sections=[]))["redundant_m2"] == {"A": None, "B": None}


def test_the_match_sums_integrals_before_dividing():
    table = views.match_table({1: ROUND_1, 2: ROUND_2})
    p0 = table["players"]["0"]
    assert p0["rounds"] == 2 and p0["alive_s"] == 50.0
    assert p0["control_m2"] == 60.0                           # (2000 + 1000) / 50, not the mean of 100 and 33.3
    assert p0["active_m2"] == 20.0 and p0["passive_m2"] == 36.0
    assert p0["active_ratio"] == 0.357                         # 1000 / 2800
    assert p0["deaths"] == 2 and p0["lost_m2"] == 40.0         # the death after the round was decided is left out
    assert p0["lost_mean_m2"] == 20.0 and p0["lost_share"] == 0.12        # 40 m² of 300 + 33.3 held
    p5 = table["players"]["5"]
    assert p5["control_m2"] == 8.4 and p5["deaths"] == 0 and p5["lost_mean_m2"] is None and p5["lost_share"] is None
    assert table["live_s"] == 100.0
    assert table["redundant_m2"] == {"A": 10.0, "B": -4.0}     # (800 + 200) / 100, (-400 + 0) / 100


def put_summary(db, replay, n, body, fingerprint=None):
    groups = rc.side_groups(db, replay)
    db.merge(ReplayRoundControl(replay_id=replay.id, round_number=n, status="ok",
                                fingerprint=fingerprint or rc.round_fingerprint(replay, groups, n),
                                data_version=cf.DATA_VERSION, data=gzip.compress(b"x"), summary=cf.pack_summary(body)))
    db.commit()


def call_players(db, headers=None, uuid=MATCH_UUID):
    return routes.replay_control_players(request(headers), uuid, db)


def test_the_endpoint_lists_every_round_and_flags_missing_and_stale_ones(db, linked):
    put_summary(db, linked, 1, ROUND_1)
    put_summary(db, linked, 2, ROUND_2, fingerprint="0" * 16)
    response = call_players(db)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-cache"
    body = json.loads(response.body)
    assert body["rounds"]["1"]["status"] == "ok" and body["rounds"]["1"]["stale"] is False
    assert body["rounds"]["2"]["stale"] is True and body["stale_rounds"] == [2]
    assert all(body["rounds"][str(n)] == {"status": "missing"} for n in range(3, linked.round_count + 1))
    assert body["match"]["rounds"] == 2 and body["match"]["players"]["0"]["control_m2"] == 60.0
    again = call_players(db, {"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304
    put_summary(db, linked, 3, ROUND_1)
    assert call_players(db, {"If-None-Match": response.headers["etag"]}).status_code == 200


def test_rows_going_stale_change_the_etag(db, linked, monkeypatch):
    put_summary(db, linked, 1, ROUND_1)
    fresh = call_players(db)
    assert json.loads(fresh.body)["stale_rounds"] == []
    # The map's geometry changes: the row is untouched but no longer current.
    monkeypatch.setattr(rc, "geometry_inputs", lambda name: {"sight": "changed", "walk": "", "specials": [], "scale": 1})
    again = call_players(db, {"If-None-Match": fresh.headers["etag"]})
    assert again.status_code == 200 and json.loads(again.body)["stale_rounds"] == [1]


def test_an_unlinked_replay_has_no_tables(db, linked):
    put_summary(db, linked, 1, ROUND_1)
    linked.link_status = "unlinked"
    db.commit()
    response = call_players(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "unlinked"}


def test_an_unreadable_summary_is_listed_not_fatal(db, linked):
    put_summary(db, linked, 1, ROUND_1)
    db.get(ReplayRoundControl, (linked.id, 1)).summary = b"not gzip"
    db.commit()
    body = json.loads(call_players(db).body)
    assert body["rounds"]["1"] == {"status": "unreadable"} and body["match"]["rounds"] == 0


def test_the_endpoint_404s_like_control_bin(db, linked, monkeypatch):
    put_summary(db, linked, 1, ROUND_1)
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    response = call_players(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "no_map"}
    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", False)
    linked.recipe = linked.recipe.replace(f".c{rc.condense_revision(linked.recipe)}.", ".c9.")
    db.commit()
    response = call_players(db)
    assert response.status_code == 404 and json.loads(response.body) == {"status": "old_blob"}


def test_demo_mode_and_unknown_replays_are_plain_404s(db, linked, monkeypatch):
    put_summary(db, linked, 1, ROUND_1)
    assert status_of(lambda: call_players(db, uuid="00000000-0000-4000-8000-000000000000")) == 404
    assert status_of(lambda: call_players(db, uuid="not-a-uuid")) == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call_players(db)) == 404


# ---------------------------------------------------------------- the match heatmap (S5.1)

WALK = base64.b64encode(bytes([0b11110000]) + bytes(cf.GRID * cf.GRID // 8 - 1)).decode()      # cells 0-3
OTHER_WALK = base64.b64encode(bytes([0b11101000]) + bytes(cf.GRID * cf.GRID // 8 - 1)).decode()  # 4 other cells


def section(key, seconds, by_state):
    """by_state: {state name: {cell: seconds}} -> a stored section."""
    states = [sorted((c, round(s * cf.GRID_HZ)) for c, s in by_state.get(name, {}).items())
              for name in cf.STATE_NAMES[1:]]
    return {"key": key, "t0": 0.0, "t1": seconds, "seconds": seconds, "totals": cf.encode_totals(states)}


HEAT_1 = summary(1, [], {}, group_side={"A": "attack", "B": "defense"}, sections=[
    section("r0", 10.0, {"a_active": {0: 10.0}, "b_safe": {1: 5.0}, "contested": {2: 10.0}}),
    section("r1", 5.0, {"contested_active": {2: 5.0}})])
HEAT_2 = summary(2, [], {}, group_side={"A": "defense", "B": "attack"}, sections=[
    section("r0", 10.0, {"a_passive": {0: 10.0}, "b_active": {3: 10.0}})])


def put_heat(db, replay, n, body, walk=WALK):
    data = cf.pack_data({"walk": walk, "cells": body["cells"]}, {"states": b"", "coverage": b"", "control": b""})
    groups = rc.side_groups(db, replay)
    db.merge(ReplayRoundControl(replay_id=replay.id, round_number=n, status="ok",
                                fingerprint=rc.round_fingerprint(replay, groups, n), data_version=cf.DATA_VERSION,
                                data=data, summary=cf.pack_summary(body)))
    db.commit()


def shares(text):
    return list(base64.b64decode(text))


def call_heatmap(db, view="side", headers=None):
    return routes.replay_control_heatmap(request(headers), MATCH_UUID, view, db)


def test_the_heatmap_sums_rounds_by_side(db, linked):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    put_heat(db, linked, 2, dict(HEAT_2, cells=4))
    body = json.loads(call_heatmap(db).body)
    assert body["labels"] == {"x": "attack", "y": "defense"} and body["walk"] == WALK
    assert body["rounds_used"] == [1, 2] and body["rounds_skipped"] == []
    by_key = {s["key"]: s for s in body["sections"]}
    assert [s["key"] for s in body["sections"]] == ["all", "r0", "r1"]
    r0 = by_key["r0"]
    assert r0["seconds"] == 20.0 and r0["rounds"] == 2 and r0["label"] == "0–10 s"
    assert shares(r0["x"]) == [128, 0, 0, 128]        # attack: A's cell 0 in round 1, B's cell 3 in round 2
    assert shares(r0["y"]) == [128, 64, 0, 0]         # defense: B's cell 1 (5 s) in round 1, A's cell 0 in round 2
    assert shares(r0["contested"]) == [0, 0, 128, 0]
    whole = by_key["all"]
    assert whole["seconds"] == 25.0 and whole["rounds"] == 2
    assert shares(whole["contested"]) == [0, 0, 153, 0]   # 15 s of 25


def test_the_team_view_maps_side_groups_by_the_link(db, linked):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    put_heat(db, linked, 2, dict(HEAT_2, cells=4))
    a_team = linked.link_report["side_to_team"]["A"]
    body = json.loads(call_heatmap(db, "team").body)
    assert body["labels"] == {"x": "team-1", "y": "team-2"}
    r0 = {s["key"]: s for s in body["sections"]}["r0"]
    a_shares, b_shares = [255, 0, 0, 0], [0, 64, 0, 128]
    assert (shares(r0["x"]), shares(r0["y"])) == ((a_shares, b_shares) if a_team == "team-1" else (b_shares, a_shares))


def test_a_round_with_other_walkable_cells_is_skipped_not_misplaced(db, linked):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    put_heat(db, linked, 2, dict(HEAT_2, cells=4), walk=OTHER_WALK)   # same count, different cells
    db.get(ReplayRoundControl, (linked.id, 1)).computed_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    db.commit()
    body = json.loads(call_heatmap(db).body)
    assert body["rounds_used"] == [1] and body["rounds_skipped"] == [2] and body["walk"] == WALK


def test_the_heatmap_view_is_checked_and_etagged(db, linked):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    bad = call_heatmap(db, "diagonal")
    assert bad.status_code == 400 and json.loads(bad.body)["status"] == "bad_view"
    first = call_heatmap(db, "side")
    assert first.status_code == 200
    assert call_heatmap(db, "side", {"If-None-Match": first.headers["etag"]}).status_code == 304
    assert call_heatmap(db, "team").headers["etag"] != first.headers["etag"]
    zipped = call_heatmap(db, "side", {"Accept-Encoding": "gzip, br"})
    assert zipped.headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(zipped.body)) == json.loads(first.body)


def test_the_heatmap_404s_like_the_other_control_routes(db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    assert json.loads(call_heatmap(db).body) == {"status": "no_map"}
    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call_heatmap(db)) == 404


def test_no_rows_is_a_missing_heatmap(db, linked):
    body = json.loads(call_heatmap(db).body)
    assert body["status"] == "missing" and body["sections"] == []


# ---------------------------------------------------------------- the /stats per-map aggregate (R3.4)


@pytest.fixture
def fresh_sums():
    views._ROUND_SUMS.clear()
    yield
    views._ROUND_SUMS.clear()


def test_the_map_aggregate_sums_live_cell_seconds_by_side(db, linked, fresh_sums, monkeypatch):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    put_heat(db, linked, 2, dict(HEAT_2, cells=4))
    [row] = views.map_aggregate(db)
    assert (row["map"], row["replays"], row["rounds"]) == (linked.map_name, 1, 2)
    assert row["enough"] is False                          # 2 rounds, under MIN_ROUNDS_PER_MAP
    # cell-seconds: (10 + 5 + 10) s x 4 cells = 100. Attack: A's 10 (r1) + B's 10 (r2); defense: B's 5 (r1)
    # + A's 10 (r2); contested: 10 + 5 (r1).
    assert row["shares"] == {"attack": 0.2, "defense": 0.15, "contested": 0.15, "nobody": 0.5}
    monkeypatch.setattr(views, "MIN_ROUNDS_PER_MAP", 2)
    assert views.map_aggregate(db)[0]["enough"] is True    # the count is rounds, not replays (D8)
    monkeypatch.setattr(views, "MIN_ROUNDS_PER_MAP", 3)
    assert views.map_aggregate(db)[0]["enough"] is False
    assert views.map_aggregate(db, set()) == [] and views.map_aggregate(db, {linked.id + 1}) == []


def test_unlinked_replays_are_left_out(db, linked, fresh_sums):
    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    linked.link_status = "unlinked"
    db.commit()
    assert views.map_aggregate(db) == []


def test_friends_are_the_players_in_the_replay_not_a_roster(db, linked):
    from app.models import MatchPlayer
    from app.models.replay import ReplayPlayer

    ids = {pid for (pid,) in db.query(MatchPlayer.player_id).join(
        ReplayPlayer, ReplayPlayer.match_player_id == MatchPlayer.id).filter(ReplayPlayer.replay_id == linked.id)}
    assert ids
    assert views.friends_replay_ids(db, {next(iter(ids))}) == {linked.id}
    assert views.friends_replay_ids(db, {max(ids) + 1000}) == set() and views.friends_replay_ids(db, set()) == set()


def test_the_stats_fragment_404s_in_demo_mode(db, linked, monkeypatch):
    from app.routers import site_stats

    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: site_stats.map_control_fragment(request(), "all", db)) == 404


def test_the_stats_fragment_lists_the_maps(db, linked, fresh_sums, monkeypatch):
    from app.routers import site_stats
    from app.templates import templates

    put_heat(db, linked, 1, dict(HEAT_1, cells=4))
    seen = {}
    monkeypatch.setattr(site_stats.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx, name=name))
    site_stats.map_control_fragment(request(), "all", db)
    assert seen["name"] == "stats/_map_control_table.html" and seen["rows"][0]["map"] == linked.map_name
    html = templates.env.get_template(seen["name"]).render(**seen)
    assert linked.map_name in html and "needs 40+ rounds" in html


def test_read_walk_reads_only_the_header():
    data = cf.pack_data({"walk": WALK, "cells": 4}, {"states": b"s" * 100000, "coverage": b"", "control": b""})
    assert views.read_walk(data) == WALK


def test_pg_a_linked_page_offers_the_layer_only_on_a_map_that_has_it(pg, condensed, monkeypatch):
    from test_replay_store import add_match

    from app.models.replay import Replay
    from app.replays import store

    monkeypatch.setattr(settings, "demo_mode", False)
    from app.templates import templates

    session = pg()
    add_match(session)
    replay = session.get(Replay, store.store_replay(session, condensed, source="local").replay_id)

    def page() -> str:
        # The route's context, rendered without the site's context processor (it opens the app's DB).
        seen = {}
        monkeypatch.setattr(routes.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx))
        routes.replay_page(request(), MATCH_UUID, session)
        return templates.env.get_template("replays/replay.html").render(**seen, request=request())

    shown = page()
    assert 'data-replay-layer="control"' in shown and "/static/js/replay_control.js" in shown
    assert 'data-replay-tab="control"' in shown and "loadControlPlayers" in shown
    assert "data-replay-heatmap" in shown and "loadHeatmap" in shown
    assert "data-replay-control-view" in shown          # the "as ... knew it" picker (shown once a row has it)
    assert ("cover not reviewed" in shown) == (not rc.map_layer(replay.map_name)["cover_reviewed"])
    monkeypatch.setattr(rc, "map_layer", lambda name: None)
    shown = page()
    assert "data-replay-layer" in shown and 'data-replay-layer="control"' not in shown
    assert "replay_control.js" not in shown and 'data-replay-tab="control"' not in shown
    assert "data-replay-heatmap" not in shown
    session.close()


@pytest.mark.parametrize("path", ["/replays/{match_uuid}/control/players.json"])
def test_the_route_is_registered_before_nothing_shadows_it(path):
    from app.main import app

    paths = [r.path for r in app.routes]
    assert path in paths
