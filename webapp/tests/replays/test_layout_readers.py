"""Replay readers use the selected layout while keeping the displayed map name."""

import gzip
from types import SimpleNamespace

import pytest

from test_control_store import db, factory, linked, put_row
from test_layout_selection import layout_assets, layout_chokes, write_json
from test_replay_store import condensed, pg

from app.coach import areas, brief, snapshot
from app.models.replay import ReplayRoundMoments
from app.models.round import RoundPlayerStat
from app.replays import control_format as cf
from app.services import control_feature_verification, replay_control as rc
from app.services import replay_control_store, replay_control_views, replay_gaps, replay_moments, replay_util
from tests.coach import toys
from tests.coach.test_control_areas import control_bytes
from tests.critutil.test_values import MODEL, v2_cast, v2_round


@pytest.fixture
def util_db(db):
    RoundPlayerStat.__table__.create(db.get_bind())
    return db


@pytest.fixture
def callout_table(monkeypatch):
    table = {
        "Toy": [{"name": "Base Site", "super": "A", "u": 2000, "v": 2000}],
        "Toy_13_10": [{"name": "Later Site", "super": "B", "u": 2000, "v": 2000}],
    }
    monkeypatch.setattr(areas, "_table", lambda: table)
    return table


@pytest.mark.parametrize("branch,key,super_area", [
    ("release-13.08", "Toy", "A"),
    ("release-13.10", "Toy_13_10", "B"),
])
def test_util_cast_areas_follow_replay_layout(util_db, linked, layout_assets, callout_table,
                                             branch, key, super_area):
    linked.map_name, linked.game_branch = "Toy", branch
    util_db.commit()
    ctx = replay_util._replay_context(util_db, [linked.id])[linked.id]
    cast = v2_cast(0, by=0, side="attack", u=2000, v=2000)
    rows = replay_util._cast_rows(linked.id, ctx, {1: v2_round([cast])}, MODEL, None,
                                  include_unscored=True)
    assert rows[0]["area"] == super_area
    assert rows[0]["map"] == ctx["map"] == "Toy"
    assert ctx["asset_key"] == key


@pytest.mark.parametrize("branch,key,name", [
    ("release-13.08", "Toy", "Base Site"),
    ("release-13.10", "Toy_13_10", "Later Site"),
])
def test_snapshot_shares_roundview_and_brief_follow_layout(monkeypatch, layout_assets, callout_table,
                                                         branch, key, name):
    replay = SimpleNamespace(map_name="Toy", game_branch=branch)
    context = {"match": {"uuid": "synthetic", "map": "Toy", "rounds": [1], "linked": True,
                          "uv_per_unit": toys.UV_PER_UNIT},
               "players": {"0": {"slot": 0, "name": "Player", "agent": "Jett", "team": "team1",
                                  "side": "A", "player_id": 1}}, "rounds": {}}
    blob = toys.round_blob({0: (2000, 2000)})
    monkeypatch.setattr(snapshot.replays, "find_replay", lambda *args: replay)
    monkeypatch.setattr(snapshot.replays, "page_context", lambda *args: context)
    monkeypatch.setattr(snapshot.replays, "round_blob", lambda *args: b"synthetic")
    monkeypatch.setattr(snapshot.fmt, "decode_blob", lambda data: blob)
    monkeypatch.setattr(rc, "round_link", lambda *args: {})
    monkeypatch.setattr(rc, "round_control", lambda *args: rc.RoundControlAnswer(
        "ok", SimpleNamespace(data=control_bytes([(1, 4)], [0]))))
    monkeypatch.setattr(replay_util, "match_payload", lambda *args, **kwargs: {"rounds": {}})
    snap = snapshot.Snapshot(snapshot.read(None, "synthetic"))
    assert snap.control(1)["cells"] == {name: 2}
    assert snap.view(1).area(2000, 2000)["name"] == name
    assert snap.view(1).area_names() == [name]
    assert snap.context["match"]["asset_key"] == key
    report = brief.build(snap, 0)
    assert report["areas"] == [name]
    assert report["match"]["map"] == snap.map == "Toy"


@pytest.mark.parametrize("branch,key", [("release-13.08", "Toy"), ("release-13.10", "Toy_13_10")])
def test_moments_and_summary_freshness_use_layout(db, linked, layout_chokes, monkeypatch, branch, key):
    linked.map_name, linked.game_branch = "Toy", branch
    put_row(db, linked, 1)
    control = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    expected = replay_gaps.gap_fingerprint(control, key)
    db.add(ReplayRoundMoments(replay_id=linked.id, round_number=1, status="ok",
                             fingerprint=expected, gaps_revision=replay_gaps.GAPS_REVISION,
                             fights={"fights": []}))
    db.commit()
    monkeypatch.setattr(snapshot.replays, "page_context", lambda *args: {"players": {}})
    payload = replay_moments.load_moments_payload(db, linked)
    assert payload["stale"] == linked.round_count - 1
    assert replay_gaps.Freshness(db).expected(linked, 1) == expected
    assert replay_control_views.load_round_summaries(db, linked).stale == []
    if key != "Toy":
        assert expected != replay_gaps.gap_fingerprint(control, "Toy")


def test_later_layout_without_callouts_has_no_names(layout_assets, callout_table):
    callout_table.pop("Toy_13_10")
    context = {"match": {"uuid": "synthetic", "map": "Toy", "asset_key": "Toy_13_10",
                          "uv_per_unit": toys.UV_PER_UNIT}, "players": {}, "rounds": {}}
    snap = snapshot.Snapshot(snapshot.assemble(context, {1: toys.round_blob({})}, {}))
    assert areas.area_at("Toy_13_10", 2000, 2000) is None
    assert areas.callouts("Toy_13_10") == []
    assert replay_util.area_of("Toy_13_10", 2000, 2000, "attack") == "unknown"
    assert snap.view(1).area(2000, 2000) is None
    assert snap.view(1).area_names() == []


@pytest.mark.parametrize("branch", ["release-13.10", "unparsed"])
def test_unavailable_layout_readers_never_resolve_base_geometry(util_db, linked, layout_assets, monkeypatch, branch):
    db = util_db
    folder, index, _ = layout_assets
    linked.map_name, linked.game_branch = "Toy", "release-13.08"
    put_row(db, linked, 1)
    linked.game_branch = branch
    index["maps"].pop("Toy_13_10")
    write_json(folder / "index.json", index)
    db.commit()

    def unavailable(*args):
        pytest.fail("an unavailable layout must not resolve base geometry")

    monkeypatch.setattr(rc, "resolve_current_geometry", unavailable)
    assert replay_control_views.load_round_summaries(db, linked).stale == [1]
    assert replay_util._replay_context(db, [linked.id])[linked.id]["asset_key"] is None
    result, _ = control_feature_verification._freshness(db, SimpleNamespace(replay_id=linked.id))
    assert result == "unavailable"


def test_feature_freshness_uses_selected_layout(db, linked, layout_assets):
    linked.map_name, linked.game_branch = "Toy", "release-13.10"
    current = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    row = SimpleNamespace(replay_id=linked.id, round_number=1, fingerprint=current)
    assert control_feature_verification._freshness(db, row) == ("current", None)


def test_guarded_control_store_uses_selected_layout(factory, db, linked, layout_assets):
    linked.map_name, linked.game_branch = "Toy", "release-13.10"
    db.commit()
    fingerprint = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    result = {"status": "ok", "data": gzip.compress(b"synthetic"), "summary": cf.pack_summary({})}
    assert replay_control_store.store_round(factory, linked.id, 1, fingerprint, result,
                                            require_current=True) == "stored"


def test_legacy_snapshot_keeps_base_callouts(callout_table):
    context = {"match": {"uuid": "synthetic", "map": "Toy", "uv_per_unit": toys.UV_PER_UNIT},
               "players": {}, "rounds": {}}
    snap = snapshot.Snapshot(snapshot.assemble(context, {1: toys.round_blob({})}, {}))
    assert snap.view(1).area_names() == ["Base Site"]
