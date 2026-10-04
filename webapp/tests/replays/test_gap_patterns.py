"""The timing-gaps pattern page's service (docs/superpowers/plans/2026-10-04-timing-gaps-pattern-page.md, P1 and
its review amendments). SQLite, no engine and no detector: replays, control rows, gap runs and gap rows are
written by hand in their stored shapes."""

import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Friendship, Match, MatchPlayer, Player
from app.models.match import MatchSource, Team
from app.models.replay import (Replay, ReplayGap, ReplayPlayer, ReplayRound, ReplayRoundControl,
                               ReplayRoundGapRun)
from app.replays import choke_assets
from app.services import gap_patterns as gp
from app.services import replay_gaps

MAP = "Ascent"
TABLES = [Player.__table__, Match.__table__, MatchPlayer.__table__, Friendship.__table__, Replay.__table__,
          ReplayRound.__table__, ReplayRoundControl.__table__, ReplayPlayer.__table__,
          ReplayRoundGapRun.__table__, ReplayGap.__table__]
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def players(db, count=10):
    out = []
    for i in range(count):
        p = Player(display_name=f"p{i}-{uuid.uuid4().hex[:6]}")
        db.add(p)
        db.flush()
        out.append(p.id)
    return out


def make_replay(db, rounds=3, map_name=MAP, linked=True, played=T0, recipe="p1.c12.f1.a0", complete=True,
                player_ids=None):
    """A valid replay with `rounds` rounds; linked to a match whose slot i is player_ids[i] when `linked`."""
    match = None
    if linked or player_ids:
        match = Match(external_id=uuid.uuid4().hex, source=MatchSource.SCRAPED, map_name=map_name, played_at=played)
        db.add(match)
        db.flush()
    replay = Replay(match_uuid=str(uuid.uuid4()), match_id=match.id if (match and linked) else None,
                    map_name=map_name, round_count=rounds, format_version=1, recipe=recipe, game_branch="b",
                    source_sha256="0" * 64, source="local", link_status="linked" if linked else "unlinked",
                    link_inputs={})
    db.add(replay)
    db.flush()
    for n in range(1, rounds + 1 - (0 if complete else 1)):
        db.add(ReplayRound(replay_id=replay.id, round_number=n, data=b"x"))
    for slot in range(10):
        mp_id = None
        if match is not None and player_ids:
            mp = MatchPlayer(match_id=match.id, player_id=player_ids[slot], agent="Jett",
                             team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
            db.add(mp)
            db.flush()
            mp_id = mp.id
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent="Jett", side_group="A" if slot < 5 else "B",
                            match_player_id=mp_id))
    db.commit()
    return replay


def control_fp(replay, n):
    return f"c{replay.id:03d}r{n:02d}".ljust(16, "0")[:16]


def put_control(db, replay, n, status="ok", fingerprint=None):
    db.merge(ReplayRoundControl(replay_id=replay.id, round_number=n, status=status,
                                fingerprint=fingerprint or control_fp(replay, n),
                                data_version=1 if status == "ok" else None, data=b"x" if status == "ok" else None,
                                summary=b"x" if status == "ok" else None))
    db.commit()


def put_run(db, replay, n, status="ok", fingerprint=None, control=True):
    if control:
        put_control(db, replay, n)
    db.merge(ReplayRoundGapRun(replay_id=replay.id, round_number=n, status=status,
                               fingerprint=fingerprint or replay_gaps.gap_fingerprint(control_fp(replay, n),
                                                                                      replay.map_name),
                               gaps_revision=replay_gaps.GAPS_REVISION, gap_count=0))
    db.commit()


def gap(db, replay, n, seq, **over):
    row = {"seq": seq, "kind": "predicted", "map": replay.map_name, "victim_slot": 0, "victim_side": "attack",
           "t_open": 10.0 + seq, "t_last_exposed": 12.0, "t_close": 13.0, "spot_cell": 130, "victim_cell": 0,
           "distance_m": 9.5, "angle_deg": 160.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing",
           "cause_detail": {}, "choke_seq": [1, 2], "route": [[[8.0, 400, 200], [10.0, 500, 300]]],
           "candidate_slots": [5], "candidate_distances": None, "checked_at": None, "stood_at": None,
           "stood_by": None, "shot_at": None, "shot_by": None, "killed_at": None, "killed_by": None,
           "victim_won_at": None, "context": {"t_round": 20.0}, "linked_seq": None}
    if over.get("kind") == "backshot":
        row.update(cause=None, flicker=False, t_last_exposed=None, t_close=None, qualified_s=None)
    row.update(over)
    db.merge(ReplayGap(replay_id=replay.id, round_number=n, **row))
    db.commit()


def page(db, viewer=None, **params):
    return gp.page_data(db, MAP, gp.Filters.parse(params), viewer)


def seqs(data):
    return [r["seq"] for p in data["patterns"] for r in p["rounds"]]


# ---------------------------------------------------------------- filters


def test_filters_parse_ignores_bad_values():
    f = gp.Filters.parse({"kind": "nope", "side": "attack", "cause": "victim_moved", "use": "shot", "t_max": "25",
                          "flickers": "1", "pop": "friends", "dir": "opponents"})
    assert f == gp.Filters(kind="both", side="attack", cause="victim_moved", use="shot", t_max=25.0, flickers=True,
                           pop="friends", dir="opponents")
    assert gp.parse({"t_max": "abc", "use": "", "dir": "x", "flickers": "0"}) == gp.Filters()
    assert gp.Filters.parse({"t_max": "-3"}).t_max is None and gp.Filters.parse({"t_max": "nan"}).t_max is None
    assert gp.Filters(side="attack", flickers=True).as_params() == {"side": "attack", "flickers": "1"}


# ---------------------------------------------------------------- eligibility


def test_eligibility_counts_ok_rounds_and_says_what_was_left_out(db, monkeypatch):
    replay = make_replay(db, rounds=8)
    put_run(db, replay, 1)                                                       # counted
    monkeypatch.setattr(replay_gaps, "GAPS_REVISION", replay_gaps.GAPS_REVISION - 1)
    put_run(db, replay, 2)                                                       # older gap rules
    monkeypatch.undo()
    fp = control_fp(replay, 3)
    put_run(db, replay, 3, fingerprint=replay_gaps.gap_fingerprint(fp, MAP, chokes="0" * 16))   # older chokes
    fp = control_fp(replay, 4)
    put_run(db, replay, 4, fingerprint=replay_gaps.gap_fingerprint(fp, MAP, hearing="1" * 16))  # older hearing
    put_run(db, replay, 5, status="failed")
    put_run(db, replay, 6, control=False)                                        # no control row: stale
    put_run(db, replay, 7)
    put_control(db, replay, 7, fingerprint="f" * 16)                             # control recomputed since
    # round 8: no run at all
    eligible = gp.eligible_rounds(db, MAP)
    assert eligible.rounds == {(replay.id, 1)}
    assert eligible.left_out == {"stale": 5, "failed": 1, "not_computed": 1}
    assert eligible.old_blob == 0


def test_a_changed_choke_asset_stales_every_round_and_is_hashed_once(db, monkeypatch):
    replay = make_replay(db, rounds=3)
    for n in (1, 2, 3):
        put_run(db, replay, n)
    assert len(gp.eligible_rounds(db, MAP).rounds) == 3
    calls = []
    monkeypatch.setattr(choke_assets, "asset_hash", lambda name, *a: calls.append(name) or "e" * 16)
    hearing = []
    real = replay_gaps.hearing_hash
    monkeypatch.setattr(replay_gaps, "hearing_hash", lambda *a: hearing.append(1) or real(*a))
    eligible = gp.eligible_rounds(db, MAP)
    assert eligible.rounds == set() and eligible.left_out["stale"] == 3
    assert calls == [MAP] and len(hearing) == 1


def test_old_blobs_invalid_replays_and_other_maps_are_not_counted_as_not_computed(db):
    make_replay(db, rounds=2, recipe="p1.c9.f1.a0")
    make_replay(db, rounds=2, complete=False)                 # invalid: a round missing
    other = make_replay(db, rounds=2, map_name="Bind")
    put_run(db, other, 1)
    eligible = gp.eligible_rounds(db, MAP)
    assert eligible.rounds == set() and eligible.old_blob == 2
    assert eligible.left_out == {"stale": 0, "failed": 0, "not_computed": 0}
    data = page(db)
    assert data["rounds"] == {"counted": 0, "left_out": {"stale": 0, "failed": 0, "not_computed": 0},
                              "old_blob": 2, "replays": 0}


def test_rows_of_left_out_rounds_never_show(db):
    replay = make_replay(db, rounds=3)
    put_run(db, replay, 1)
    put_run(db, replay, 2, fingerprint="0" * 16)
    put_run(db, replay, 3, status="failed")
    for n in (1, 2, 3):
        gap(db, replay, n, 0)
    data = page(db)
    assert data["rows"]["total"] == 1 and data["patterns"][0]["rounds"][0]["round"] == 1
    assert data["rounds"]["counted"] == 1 and data["rounds"]["left_out"] == {"stale": 1, "failed": 1,
                                                                            "not_computed": 0}


# ---------------------------------------------------------------- populations


@pytest.fixture
def friends_world(db):
    ids = players(db)
    viewer, friend = ids[0], ids[3]
    db.add(Friendship(owner_player_id=viewer, friend_player_id=friend))
    db.commit()
    a = make_replay(db, rounds=1, player_ids=ids)
    put_run(db, a, 1)
    gap(db, a, 1, 0, victim_slot=0, candidate_slots=[5])          # the viewer left it open
    gap(db, a, 1, 1, victim_slot=7, candidate_slots=[3, 6])       # a friend is a candidate enemy
    gap(db, a, 1, 2, victim_slot=8, candidate_slots=[6])          # nobody of the group
    gap(db, a, 1, 3, kind="backshot", victim_slot=6, candidate_slots=[0])   # the viewer is the shooter
    gap(db, a, 1, 4, victim_slot=3, candidate_slots=[9])          # the friend left it open
    other = players(db)
    b = make_replay(db, rounds=1, linked=False, player_ids=[viewer] + other[1:])   # slot 0 joins the viewer
    put_run(db, b, 1)
    gap(db, b, 1, 0, victim_slot=0, candidate_slots=[5])
    return {"db": db, "viewer": viewer, "friend": friend, "a": a, "b": b}


def test_friends_left_open_and_opponents_left_open(friends_world):
    w = friends_world
    db, viewer = w["db"], w["viewer"]
    everyone = page(db, viewer)
    assert everyone["population"] == "everyone" and everyone["rows"]["total"] == 6
    left = page(db, viewer, pop="friends")
    assert left["population"] == "friends" and left["direction"] == "left"
    assert sorted(seqs(left)) == [0, 4]                         # unlinked replay b contributes nothing
    opp = page(db, viewer, pop="friends", dir="opponents")
    assert sorted(seqs(opp)) == [1, 3]
    assert gp.group_player_ids(db, viewer) == {viewer, w["friend"]}


def test_a_logged_out_visitor_gets_everyone(friends_world):
    data = page(friends_world["db"], None, pop="friends")
    assert data["population"] == "everyone" and data["friends_available"] is False and data["rows"]["total"] == 6
    assert data["direction"] is None


def test_a_relinked_replay_picks_up_its_new_players(friends_world):
    w = friends_world
    db, b = w["db"], w["b"]
    # Linking b: its match row gets the link and its slots their match players (slot 0 is the viewer).
    b.match_id = db.query(MatchPlayer.match_id).filter(MatchPlayer.player_id == w["viewer"])\
        .order_by(MatchPlayer.id.desc()).first()[0]
    b.link_status = "linked"
    db.commit()
    assert sorted((r["match_uuid"], r["seq"]) for p in page(db, w["viewer"], pop="friends")["patterns"]
                  for r in p["rounds"]) == sorted([(w["a"].match_uuid, 0), (w["a"].match_uuid, 4),
                                                   (b.match_uuid, 0)])
    # Re-linked to other players: slot 0 is no longer the viewer.
    stranger = players(db, 1)[0]
    mp = db.get(MatchPlayer, db.query(ReplayPlayer).filter_by(replay_id=b.id, slot=0).one().match_player_id)
    mp.player_id = stranger
    db.commit()
    assert sorted(seqs(page(db, w["viewer"], pop="friends"))) == [0, 4]


def test_friend_slots_maps_players_to_slots(friends_world):
    w = friends_world
    assert gp.friend_slots(w["db"], [w["a"].id], {w["viewer"], w["friend"]}) == {w["a"].id: {0, 3}}
    assert gp.friend_slots(w["db"], [], {w["viewer"]}) == {} and gp.friend_slots(w["db"], [w["a"].id], set()) == {}


def test_a_stale_match_player_from_an_earlier_link_does_not_count(friends_world):
    w = friends_world
    db, a = w["db"], w["a"]
    # Re-linked to another match, its replay_players still point at the old match's players.
    other = Match(external_id=uuid.uuid4().hex, source=MatchSource.SCRAPED, map_name=MAP, played_at=T0)
    db.add(other)
    db.flush()
    a.match_id = other.id
    db.commit()
    assert gp.friend_slots(db, [a.id], {w["viewer"], w["friend"]}) == {}
    assert seqs(page(db, w["viewer"], pop="friends")) == []


# ---------------------------------------------------------------- each filter


@pytest.fixture
def filter_world(db):
    r = make_replay(db, rounds=1)
    put_run(db, r, 1)
    gap(db, r, 1, 0)                                                         # plain, unused, attack, t 20
    gap(db, r, 1, 1, flicker=True)
    gap(db, r, 1, 2, victim_side="defense", cause="victim_turned", stood_at=11.0, context={"t_round": 50.0})
    gap(db, r, 1, 3, victim_side=None, cause="route_released", stood_at=11.0, shot_at=12.0, killed_at=12.5,
        context={"t_round": 5.0})
    gap(db, r, 1, 4, kind="backshot", killed_at=11.0, victim_side="attack", context={"t_round": 30.0})
    gap(db, r, 1, 5, kind="backshot", victim_side="defense", context={})
    gap(db, r, 1, 6, shot_at=12.0, context=None)
    return db


@pytest.mark.parametrize("params, expected", [
    ({}, [0, 2, 3, 4, 5, 6]),
    ({"flickers": "1"}, [0, 1, 2, 3, 4, 5, 6]),
    ({"kind": "predicted"}, [0, 2, 3, 6]),
    ({"kind": "backshot"}, [4, 5]),
    ({"side": "attack"}, [0, 4, 6]),                    # null side excluded
    ({"side": "defense"}, [2, 5]),
    ({"cause": "victim_turned"}, [2, 4, 5]),            # cause filters predicted only
    ({"use": "stood"}, [2, 3, 4, 5]),                   # back-shots unaffected
    ({"use": "shot"}, [3, 4, 5, 6]),
    ({"use": "killed"}, [3, 4]),                        # both kinds
    ({"use": "unused"}, [0, 4, 5]),
    ({"t_max": "25"}, [0, 3]),                          # rows without t_round left out
    ({"t_max": "30", "kind": "backshot"}, [4]),
])
def test_each_filter(filter_world, params, expected):
    data = page(filter_world, **params)
    assert sorted(seqs(data)) == expected


# ---------------------------------------------------------------- figures and order


def test_figures_have_their_n_and_kinds_are_never_summed(db):
    r = make_replay(db, rounds=2)
    put_run(db, r, 1)
    put_run(db, r, 2)
    gap(db, r, 1, 0, cause="victim_moved", stood_at=1.0, context={"t_round": 10.0})
    gap(db, r, 1, 1, cause="victim_moved", stood_at=1.0, shot_at=2.0, context={"t_round": 30.0})
    gap(db, r, 1, 2, cause="open_timing", stood_at=1.0, shot_at=2.0, killed_at=3.0, context={"t_round": 20.0})
    gap(db, r, 1, 3, kind="backshot", linked_seq=2, killed_at=3.0)
    gap(db, r, 1, 4, kind="backshot")
    gap(db, r, 2, 0, choke_seq=[4], kind="backshot")
    gap(db, r, 2, 1, choke_seq=[4], kind="backshot")
    gap(db, r, 2, 2, choke_seq=[4], kind="backshot")
    gap(db, r, 2, 3, choke_seq=[3])
    gap(db, r, 2, 4, choke_seq=[2])
    gap(db, r, 2, 5, choke_seq=None, kind="backshot")               # a broken path: left out, counted
    data = page(db)
    assert [p["choke_seq"] for p in data["patterns"]] == [[1, 2], [2], [3], [4]]
    top = data["patterns"][0]
    assert top["key"] == "1-2"
    assert top["predicted"] == {"n": 3, "stood": {"k": 3, "n": 3}, "shot": {"k": 2, "n": 3},
                                "killed": {"k": 1, "n": 3}, "cause": {"name": "victim_moved", "k": 2, "n": 3},
                                "median_t_round": 20.0, "t_round_n": 3}
    assert top["backshots"] == {"n": 2, "linked": 1, "standalone": 1, "killed": {"k": 1, "n": 2}}
    assert top["rounds"][0] == {"match_uuid": r.match_uuid, "round": 1, "seq": 0, "t_open": 10.0,
                                "kind": "predicted"}
    assert data["patterns"][3]["predicted"]["n"] == 0 and data["patterns"][3]["backshots"]["n"] == 3
    assert data["rows"] == {"total": 11, "predicted": 5, "backshot": 6, "unknown_seq": 1}


def test_predicted_count_sorts_before_backshot_count():
    rows = [{"choke_seq": s, "kind": k, "cause": None, "stood_at": None, "shot_at": None, "killed_at": None,
             "linked_seq": None, "context": None, "match_uuid": "u", "round_number": 1, "seq": i, "t_open": 0.0,
             "sort_date": T0, "replay_id": 1}
            for i, (s, k) in enumerate([([9], "backshot"), ([9], "backshot"), ([8], "predicted"),
                                        ([7], "predicted"), ([7], "backshot"), (None, "predicted")])]
    out = gp.group_patterns(rows)
    assert [p["choke_seq"] for p in out["patterns"]] == [[7], [8], [9]] and out["unknown_seq"] == 1


# ---------------------------------------------------------------- route shape


def shape_row(seq, route, side="attack", days=0, replay_id=1, kind="predicted"):
    return {"choke_seq": [], "victim_side": side, "kind": kind, "cause": None, "stood_at": None,
            "shot_at": None, "killed_at": None, "linked_seq": None, "context": None, "match_uuid": "u",
            "round_number": 1, "seq": seq, "t_open": 1.0, "replay_id": replay_id,
            "sort_date": T0 + timedelta(days=days), "route": route}


def line(x0, y0, x1, y1):
    return [[[0.0, x0, y0], [1.0, x1, y1]]]


def test_resample_joins_pieces_and_spaces_points_evenly():
    pts = gp.resample([[[0, 0, 0], [1, 100, 0]], [[2, 100, 0], [3, 100, 50]]], points=4)
    assert pts == [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (100.0, 50.0)]
    assert gp.resample([]) is None and gp.resample([[[0, 5, 5]]]) == [(5.0, 5.0)] * 16


def test_similar_routes_group_and_a_distant_one_does_not():
    rows = [shape_row(0, line(100, 100, 300, 100)),
            shape_row(1, [[[0, 100, 110], [1, 200, 110]], [[2, 200, 110], [3, 300, 110]]]),   # 1 m off, 2 pieces
            shape_row(2, line(100, 500, 300, 500)),                                           # 40 m off
            shape_row(3, line(100, 100, 300, 100), side="defense"),                           # other side
            shape_row(4, line(100, 100, 300, 100), side=None),                                # its own groups
            shape_row(5, [])]                                                                 # no route
    out = gp.shape_groups(rows, m_per_px=0.1)
    attack = out["attack"]
    assert [g["n"] for g in attack["groups"]] == [2, 1] and attack["cut"] is False
    assert attack["no_route"] == {"predicted": 1, "backshot": 0}             # by kind, never summed
    assert [r["seq"] for r in attack["groups"][0]["rounds"]] == [0, 1]
    assert len(attack["groups"][0]["rounds"][0]["points"]) == 16
    assert attack["groups"][0]["predicted"]["n"] == 2
    assert [g["n"] for g in out["defense"]["groups"]] == [1] and [g["n"] for g in out["none"]["groups"]] == [1]
    assert all(r["choke_seq"] == [] for r in rows)
    assert gp.shape_groups([dict(rows[0], choke_seq=[1])], 0.1) == {}


def test_the_early_exit_gives_the_same_groups_as_the_full_distance():
    rng = random.Random(7)
    rows = []
    for i in range(300):
        x, y = rng.choice([(100, 100), (500, 500), (800, 200)])
        pts = [[float(t), x + 12 * t + rng.uniform(-30, 30), y + rng.uniform(-30, 30)] for t in range(rng.randint(1, 6))]
        rows.append(shape_row(i, [pts], side=rng.choice(["attack", "defense"]),
                              kind=rng.choice(["predicted", "backshot"])))
    for scale in (0.05, 0.1, 0.13):
        groups = {}
        for row in sorted(rows, key=gp._order):          # the reference: greedy on route_distance_m alone
            pts = gp.resample(row["route"])
            side = groups.setdefault(row["victim_side"], [])
            for g in side:
                if gp.route_distance_m(g[0], pts, scale) <= gp.SHAPE_THRESHOLD_M:
                    g.append(pts)
                    break
            else:
                side.append([pts])
        out = gp.shape_groups(rows, scale)
        for side, expected in groups.items():
            got = out[side]["groups"]
            assert [g["n"] for g in got] == [len(g) for g in expected]
            assert len(got) > 1 and any(g["n"] > 1 for g in got)


def test_shape_grouping_cuts_to_the_most_recent_rows(monkeypatch):
    monkeypatch.setattr(gp, "SHAPE_CAP", 3)
    rows = [shape_row(i, line(100, 100 + 100 * i, 300, 100 + 100 * i), days=-i) for i in range(5)]
    out = gp.shape_groups(rows, 0.1)["attack"]
    assert out["cut"] is True and out["rows"] == 3
    assert [g["rounds"][0]["seq"] for g in out["groups"]] == [2, 1, 0]       # oldest kept first, fixed order
    assert gp.shape_groups(rows[:3], 0.1)["attack"]["cut"] is False


def test_the_page_groups_empty_sequence_rows_by_shape(db):
    r = make_replay(db, rounds=1)
    put_run(db, r, 1)
    gap(db, r, 1, 0, choke_seq=[], route=line(100, 100, 300, 100))
    gap(db, r, 1, 1, choke_seq=[], route=line(100, 104, 300, 104))
    gap(db, r, 1, 2, choke_seq=[], route=line(100, 900, 900, 900))
    gap(db, r, 1, 3, choke_seq=[1], route=line(100, 100, 300, 100))
    data = page(db)
    scale = gp.m_per_px(MAP)
    assert scale is not None and 0.01 < scale < 1
    assert [g["n"] for g in data["shapes"]["attack"]["groups"]] == [2, 1]
    assert {p["key"] for p in data["patterns"]} == {"", "1"}


# ---------------------------------------------------------------- routes are read only when needed


@pytest.fixture
def statements(db):
    seen = []
    engine = db.get_bind()

    def log(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", log)
    yield seen
    event.remove(engine, "before_cursor_execute", log)


def test_no_route_is_loaded_unless_asked(db, statements):
    r = make_replay(db, rounds=1)
    put_run(db, r, 1)
    gap(db, r, 1, 0)
    gap(db, r, 1, 1, kind="backshot", choke_seq=[1, 2], route=[[[1.0, 10, 10]], [[2.0, 20, 20]]])
    statements.clear()
    eligible = gp.eligible_rounds(db, MAP)
    rows = gp.load_rows(db, MAP, eligible, gp.Filters())
    assert rows and all("route" not in row for row in rows)
    page(db)
    assert not [s for s in statements if "replay_gaps.route" in s]
    assert gp.load_rows(db, MAP, eligible, gp.Filters(), with_routes=True)[0]["route"] == \
        [[[8.0, 400, 200], [10.0, 500, 300]]]
    routes = gp.pattern_routes(db, MAP, [1, 2], gp.Filters(kind="backshot"), None)
    assert [(x["seq"], x["route"]) for x in routes] == [(1, [[[1.0, 10, 10]], [[2.0, 20, 20]]])]
    assert gp.pattern_routes(db, MAP, [9], gp.Filters(), None) == []
