"""Map control's per-replay views from the stored summaries (docs/map-control-stages-4-7-impl.md,
S4.1 and S5.1): the player tables and their endpoint. SQLite, no engine: summaries are made up in
the shape app/control/encode.py stores."""

import gzip
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.models.replay import ReplayRoundControl  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_views as views  # noqa: E402


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
    assert p0["lost_mean_m2"] == 20.0 and p0["lost_mean_share"] == 0.2
    p5 = table["players"]["5"]
    assert p5["control_m2"] == 8.4 and p5["deaths"] == 0 and p5["lost_mean_m2"] is None
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


@pytest.mark.parametrize("path", ["/replays/{match_uuid}/control/players.json"])
def test_the_route_is_registered_before_nothing_shadows_it(path):
    from app.main import app

    paths = [r.path for r in app.routes]
    assert path in paths
