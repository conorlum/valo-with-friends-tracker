"""The util.json endpoint, the /util page and the coach's util facts, on SQLite with stored util rows."""

import json
import sys
from pathlib import Path

import pytest

REPLAYS = Path(__file__).resolve().parents[1] / "replays"
sys.path.insert(0, str(REPLAYS))
sys.path.insert(0, str(REPLAYS.parents[0]))

from replay_synthetic import MATCH_UUID  # noqa: E402
from test_control_store import feature_free_source, put_row  # noqa: E402
from test_gaps_web import page_db, page_linked  # noqa: E402,F401  (fixtures)
from test_replay_routes import request, status_of  # noqa: E402
from test_replay_store import condensed  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.models import AccountGroupMember  # noqa: E402
from app.models.friendship import Friendship  # noqa: E402
from app.models.replay import ReplayRoundUtil, UtilModel  # noqa: E402
from app.routers import replays as routes  # noqa: E402
from app.services import replay_gaps as gaps  # noqa: E402
from app.services import replay_util  # noqa: E402

KILLS = [{"t": 9.2, "killer": 0, "victim": 5, "k_side": "attack", "v_side": "defense", "before": [5, 5]}]


def cast(i=0, slot=0, value_kills=True):
    return {"i": i, "by": slot, "agent": "Breach", "t": 8.0, "t_end": 8.5, "u": 5000, "v": 5000,
            "names": ["4_Flashpoint"], "codes": ["Breach"], "kinds": ["blind"], "side": "attack", "phase": "contact",
            "after_decided": False, "alive": [5, 5], "trigger": None, "enemies": [5],
            "effects": [{"target": 5, "how": "blind", "t0": 8.4, "t1": 10.4, "known": True}],
            "kills": [{"t": 9.2, "killer": 0, "victim": 5, "ours": True, "ev": "affected"}] if value_kills else [],
            "followed": True, "harm": [], "cover": None, "presence": None, "spike": None, "ult_points": None,
            "activation": None}


def data(casts=None):
    return {"v": 1, "sides": {str(s): ("attack" if s < 5 else "defense") for s in range(10)}, "contact": 9.0,
            "plant": None, "defuse": None, "t_decided": 90.0, "kills": KILLS, "casts": casts or [cast()],
            "heals": [], "unused": [{"slot": 0, "t": 30.0, "kind": "held_at_death", "abilities": ["Flashpoint"], "kinds": ["blind"]}],
            "avail": {"heals": True, "causes": True, "charges": True, "ult": True, "friendly_status": False}}


@pytest.fixture
def db(page_db, monkeypatch):
    feature_free_source(monkeypatch)
    bind = page_db.get_bind()
    for table in (Friendship.__table__, AccountGroupMember.__table__, UtilModel.__table__):
        table.create(bind, checkfirst=True)
    return page_db


@pytest.fixture
def linked(page_linked):
    return page_linked


def put_util(db, replay, n, payload=None, status="ok", revision=None, fingerprint=None):
    """A stored util row; by default with the round's current gap fingerprint, as a gap run writes it."""
    fingerprint = fingerprint or replay_util.Freshness(db).expected(replay, n)
    db.merge(ReplayRoundUtil(replay_id=replay.id, round_number=n, status=status, fingerprint=fingerprint,
                             gaps_revision=gaps.GAPS_REVISION if revision is None else revision,
                             data=(payload or data()) if status == "ok" else None))
    db.commit()


def put_model(db, data, excluded):
    from app.services import util_model

    db.merge(UtilModel(name="win", version=util_model.VERSION, key="k",
                       data={**data, "excluded": sorted(excluded), "key": "k"}))
    db.commit()


ESTIMATOR_ROWS = [(a, d, False, 10.0, None, i < 20 * a // (a + d)) for a in range(1, 6) for d in range(1, 6)
                  for i in range(20)]


def call(db, uuid=MATCH_UUID):
    return routes.replay_util_json(request(None), uuid, db)


def body_of(response):
    assert response.status_code == 200, response.body
    return json.loads(response.body)


def test_the_route_is_registered_before_the_round_route():
    from starlette.routing import Match

    from app.main import app

    scope = {"type": "http", "method": "GET", "path": f"/replays/{MATCH_UUID}/util.json", "root_path": ""}
    first = next(r for r in app.routes if r.matches(scope)[0] == Match.FULL)
    assert first.path == "/replays/{match_uuid}/util.json"
    assert "/util" in [r.path for r in app.routes]


def test_unknown_replays_and_demo_mode_are_plain_404s(db, linked, monkeypatch):
    assert status_of(lambda: call(db, uuid="00000000-0000-4000-8000-000000000000")) == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    assert status_of(lambda: call(db)) == 404


def test_payload_values_casts_and_counts_stale_and_failed(db, linked):
    put_row(db, linked, 1)
    put_util(db, linked, 1)
    put_util(db, linked, 2, status="failed")
    put_util(db, linked, 3, revision=gaps.GAPS_REVISION - 1)
    body = body_of(call(db))
    (c,) = body["rounds"]["1"]["casts"]
    assert c["slot"] == 0 and c["kind"] == "blind" and c["kills"][0]["ev"] == "affected"
    assert body["rounds"]["1"]["unused"][0]["kind"] == "held_at_death"
    assert body["failed"] == [2] and body["stale"] == linked.round_count - 2
    assert body["estimator"] is False and c["value"] == 0.0, "no estimator could be fitted: kills are worth nothing"
    assert all(set(p) == {"name", "agent", "team"} for p in body["players"].values())


def test_with_an_estimator_an_enabling_flash_is_worth_something(db, linked):
    from app.services import util_model

    put_model(db, util_model.fit(ESTIMATOR_ROWS), {linked.match_id})
    put_util(db, linked, 1)
    body = body_of(call(db))
    assert body["rounds"]["1"]["casts"][0]["value"] > 0
    assert body["summary"]["0"]["best"][0]["round"] == 1


def test_a_row_from_an_older_link_clock_or_geometry_is_stale(db, linked):
    put_util(db, linked, 1)
    put_util(db, linked, 2, fingerprint="0" * 16)        # computed before a relink or a clock correction
    body = body_of(call(db))
    assert "2" not in body["rounds"] and body["stale"] == linked.round_count - 1
    assert [r[1] for r in replay_util.current_rows(db)] == [1]
    linked.clock_offset = (linked.clock_offset or 0.0) + 1.5
    linked.db_deaths = {"1": [{"slot": 0, "t_db": 20.0}]}
    db.commit()
    assert replay_util.current_rows(db) == [], "the round's link changed: its stored util is stale"


def test_an_estimator_trained_on_a_match_that_now_has_a_replay_is_refitted_first(db, linked, monkeypatch):
    from app.services import util_model

    put_model(db, util_model.fit(ESTIMATOR_ROWS), set())       # fitted before this match had a replay
    put_util(db, linked, 1)
    refits = []

    def refresh(session):
        refits.append(1)
        model = {**util_model.fit(ESTIMATOR_ROWS), "excluded": [linked.match_id], "key": "k2"}
        session.merge(UtilModel(name="win", version=util_model.VERSION, key="k2", data=model))
        session.commit()
        return model

    monkeypatch.setattr(util_model, "refresh", refresh)
    body_of(call(db))
    assert refits == [1]
    body_of(call(db))
    assert refits == [1], "refitted once: the stored one now leaves this match out"


def test_a_build_that_raced_new_evidence_is_stored_under_its_own_inputs_and_built_again(db, linked, monkeypatch):
    from app.services import util_model

    put_model(db, util_model.fit(ESTIMATOR_ROWS), {linked.match_id})
    put_util(db, linked, 1)
    real = replay_util.build_corpus
    builds = []

    def racing(session, **kw):
        out = real(session, **kw)
        builds.append(len(out["rows"]))
        if len(builds) == 1:
            put_util(db, linked, 2)          # the worker stores another round while this build runs
        return out

    monkeypatch.setattr(replay_util, "build_corpus", racing)
    data = replay_util.ensure_values(db)
    assert builds == [1, 2] and len(data["rows"]) == 2, "it saw the rows change and built again"
    key, stored = replay_util._values_row(db)
    rows = replay_util.current_rows(db, with_data=False)
    assert key == replay_util.corpus_key(db, rows, util_model.load(db)) and len(stored["rows"]) == 2
    replay_util.ensure_values(db)
    assert builds == [1, 2], "the stored build is current: no third"


def test_the_corpus_key_follows_the_ability_table_the_extractor_version_and_the_friendships(db, linked, monkeypatch):
    from app.critutil import abilities, constants

    put_util(db, linked, 1)
    rows = replay_util.current_rows(db, with_data=False)
    base = replay_util.corpus_key(db, rows, None)
    assert replay_util.corpus_key(db, rows, None) == base
    with monkeypatch.context() as mp:
        mp.setattr(abilities, "table_hash", lambda entries=None: "another table")
        assert replay_util.corpus_key(db, rows, None) != base
    with monkeypatch.context() as mp:
        mp.setattr(constants, "VERSION", constants.VERSION + 1)
        assert replay_util.corpus_key(db, rows, None) != base
    assert replay_util.corpus_key(db, rows, None) == base
    db.add(Friendship(owner_player_id=1, friend_player_id=2))
    db.commit()
    assert replay_util.corpus_key(db, rows, None) != base


def test_the_replay_context_says_whether_the_blob_has_on_spans(db, linked):
    linked.recipe = "p1.c22.f1.a1"
    db.commit()
    assert replay_util._replay_context(db, {linked.id})[linked.id]["on_known"] is True
    linked.recipe = "p1.c21.f1.a1"
    db.commit()
    assert replay_util._replay_context(db, {linked.id})[linked.id]["on_known"] is False


def test_the_tab_reads_a_casts_lift_from_the_stored_build(db, linked):
    from app.services import util_model

    put_model(db, util_model.fit(ESTIMATOR_ROWS), {linked.match_id})
    put_util(db, linked, 1)
    assert body_of(call(db))["rounds"]["1"]["casts"][0]["lift"] is None
    key = replay_util.corpus_key(db, replay_util.current_rows(db, with_data=False), util_model.load(db))
    built = {"rows": [{"replay": linked.id, "round": 1, "i": 0, "lift": 0.0312, "base_n": 55, "base_level": "x"}],
             "fingerprints": {str(linked.id): {"1": _fingerprint(db, linked, 1)}}}
    replay_util._store_values(db, key, built)
    c = body_of(call(db))["rounds"]["1"]["casts"][0]
    assert c["lift"] == 3.12 and c["base_n"] == 55 and c["base_level"] == "x"


def _fingerprint(db, replay, n):
    return db.get(ReplayRoundUtil, (replay.id, n)).fingerprint


def _stored_build(db, linked, row, fingerprints=True):
    from app.services import util_model

    put_model(db, util_model.fit(ESTIMATOR_ROWS), {linked.match_id})
    put_util(db, linked, 1)
    key = replay_util.corpus_key(db, replay_util.current_rows(db, with_data=False), util_model.load(db))
    built = {"rows": [{"replay": linked.id, "round": 1, "i": 0, "slot": 0, "t": 8.0, **row}]}
    if fingerprints:
        built["fingerprints"] = {str(linked.id): {"1": _fingerprint(db, linked, 1)}}
    replay_util._store_values(db, key, built)
    return built


def test_the_payload_carries_both_baselines_gates_label_and_signals(db, linked):
    row = {"lift": 0.02, "type": "blind", "L": 0.1, "lift_all": 0.02, "base_level": "s", "base_n": 9,
           "lift_yours": 0.0345, "yours_level": "y", "yours_n": 7, "gate_all": True, "gate_yours": False,
           "lift_device": None, "device_level": None, "device_n": 0, "gate_device": None, "label": "Dart"}
    _stored_build(db, linked, row)
    c = body_of(call(db))["rounds"]["1"]["casts"][0]
    assert c["lift_all"] == 2.0 and c["lift_yours"] == 3.45 and c["lift_device"] is None
    assert c["gate_all"] is True and c["gate_yours"] is False and c["yours_level"] == "y" and c["yours_n"] == 7
    assert c["type"] == "blind" and c["L"] == 0.1 and c["kind"] == "blind"
    assert "label" in c and "signals" in c and "device_n" in c


def test_a_build_missing_this_replays_rounds_is_rebuilt_once(db, linked, monkeypatch):
    _stored_build(db, linked, {"lift": 0.0}, fingerprints=False)
    calls = []
    monkeypatch.setattr(replay_util, "ensure_values", lambda d, *a, **k: calls.append(1))
    call(db)
    assert calls == [1]


def test_a_build_with_matching_fingerprints_is_not_rebuilt(db, linked, monkeypatch):
    _stored_build(db, linked, {"lift": 0.0})
    monkeypatch.setattr(replay_util, "ensure_values", lambda d, *a, **k: pytest.fail("rebuilt"))
    call(db)


def test_the_payload_never_mutates_the_build_or_shares_with_it(db, linked):
    import copy

    _stored_build(db, linked, {"lift": 0.02, "lift_all": 0.02, "gate_all": True, "base_level": "s"})
    before = copy.deepcopy(replay_util._values_row(db)[1])
    first = body_of(call(db))
    assert replay_util._values_row(db)[1] == before
    first["rounds"]["1"]["casts"][0]["lift_all"] = 99
    from app.routers import replays as r

    payload = replay_util.match_payload(db, linked)
    payload["rounds"]["1"]["casts"][0]["lift_all"] = 77
    assert replay_util.match_payload(db, linked)["rounds"]["1"]["casts"][0]["lift_all"] == 2.0
    assert replay_util._values_row(db)[1] == before


def test_a_cast_after_the_round_was_decided_is_shown_unscored(db, linked):
    late = {**cast(i=1, value_kills=False), "t": 95.0, "after_decided": True}
    put_util(db, linked, 1, payload=data([cast(), late]))
    rows = {c["i"]: c for c in body_of(call(db))["rounds"]["1"]["casts"]}
    assert rows[1]["scored"] is False and rows[1]["value"] is None
    assert rows[0]["scored"] is True
    assert body_of(call(db))["summary"]["0"]["casts"] == 1, "never in the totals"


def test_a_full_ult_held_all_round_is_unused_when_the_match_proves_its_cost(db, linked):
    used = {**data(), "ults": {"0": {"cap_seen": 7, "end": 0, "t": 40.0, "used": True}}}
    held = {**data(), "ults": {"0": {"cap_seen": None, "end": 7, "t": 90.0, "used": False}}}
    never = {**data(), "ults": {"1": {"cap_seen": None, "end": 8, "t": 90.0, "used": False}}}
    put_util(db, linked, 1, payload=used)
    put_util(db, linked, 2, payload=held)
    put_util(db, linked, 3, payload=never)
    body = body_of(call(db))
    ult = lambda n: [u for u in body["rounds"][n]["unused"] if u["kind"] == "ult_unused"]  # noqa: E731
    assert ult("2") == [{"slot": 0, "t": 90.0, "kind": "ult_unused", "points": 7}]
    assert ult("1") == [] and ult("3") == [], "slot 1 never used its ult in the match: its cost is unproven"


def render(db, monkeypatch, viewer=None):
    from app.routers import util_page

    seen = {}
    monkeypatch.setattr(util_page.templates, "TemplateResponse", lambda req, name, ctx, **kw: seen.update(ctx) or ctx)
    monkeypatch.setattr(util_page, "get_current_player", lambda request, db: viewer)
    util_page.util_page(request(None), db)
    return seen


def test_page_needs_a_login_and_404s_in_demo_mode(db, linked, monkeypatch):
    assert render(db, monkeypatch)["login_note"]
    monkeypatch.setattr(settings, "demo_mode", True)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        render(db, monkeypatch)


def test_page_for_a_viewer_shows_only_their_circle(db, linked, monkeypatch):
    from app.models.player import Player

    put_util(db, linked, 1)
    ctx = replay_util._replay_context(db, {linked.id})[linked.id]
    me = db.get(Player, ctx["players"][0]["player_id"])
    seen = render(db, monkeypatch, viewer=me)
    assert seen["login_note"] is None
    assert [p["name"] for p in seen["players"]] == [me.display_name]
    other = db.get(Player, ctx["players"][5]["player_id"])
    assert render(db, monkeypatch, viewer=other)["players"] == []


def test_coach_util_facts_are_the_players_casts_with_ids():
    from app.coach import brief

    round_util = {"casts": [{"slot": 0, "t": 8.0, "ability": "Flashpoint", "kind": "blind", "value": 2.5, "enemies": 1,
                             "kills": [{"ours": True}], "harm": [], "harm_died": 0, "followed": True, "wasted": False,
                             "cover": {"blocked_s": 3.5}},
                            {"slot": 1, "t": 9.0, "ability": "x", "kind": "smoke", "value": 0.0}],
                  "unused": [{"slot": 0, "t": 30.0, "kind": "held_at_death", "kinds": ["blind"]}]}
    facts = brief.util_facts(round_util, 0)
    assert [f["t"] for f in facts] == [8.0, 30.0]
    assert facts[0]["kills"] == 1 and facts[0]["value"] == 2.5 and facts[0]["blocked_s"] == 3.5
    assert brief.util_facts(None, 0) == []
