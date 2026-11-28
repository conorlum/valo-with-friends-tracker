"""Replay search library and CLI (W5): `search` hits equal the one matcher over every stored row (R1), Riot IDs
resolve to a context, newest first, 50 a page, links, names (R6) and the revision gate. SQLite in memory, two toy
linked replays built through the real index rebuild."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (AccountGroup, AccountGroupMember, Friendship, KillEvent, Match, MatchPlayer, Player, Round,
                        RoundPlant, RoundPlayerStat)
from app.models.match import MatchSource, Team
from app.models.replay import (Replay, ReplayGap, ReplayPlayer, ReplayRound, ReplayRoundMoments,
                               ReplayRoundUtil)
from app.models.replay_search import ReplayMomentIndex
from app.search import filters, index, match
from app.search.match import ROW_KEYS
from app.services import replay_search as lib
from app.services import replay_search_index as store

OFFSET = 100.0
UUID1 = "11111111-1111-4111-8111-111111111111"
UUID2 = "22222222-2222-4222-8222-222222222222"
AGENTS = ["Sova", "Jett", "Omen", "Sage", "Reyna", "Viper", "Cypher", "Raze", "Breach", "Skye"]
NAMES1 = ["Alpha#1", "Bravo#2", "Charlie#3", "Delta#4", "Echo#5", "Foxtrot#6", "Golf#7", "Hotel#8", "India#9",
          "Juliet#10"]
NAMES2 = NAMES1[:4] + ["Kilo#11", "Lima#12", "Mike#13", "November#14", "Oscar#15", "Papa#16"]
TABLES = [Player.__table__, Match.__table__, MatchPlayer.__table__, Round.__table__, RoundPlayerStat.__table__,
          KillEvent.__table__, RoundPlant.__table__, Replay.__table__, ReplayRound.__table__,
          ReplayPlayer.__table__, ReplayRoundMoments.__table__, ReplayRoundUtil.__table__, ReplayGap.__table__,
          ReplayMomentIndex.__table__, store.ReplayMomentIndexState.__table__, Friendship.__table__,
          AccountGroup.__table__, AccountGroupMember.__table__]


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def pragma(dbapi, _record):
        dbapi.execute("PRAGMA foreign_keys=ON")

    for table in TABLES:
        table.create(engine)
    maker = sessionmaker(bind=engine)
    with maker() as db:
        seed(db, UUID1, "toy-1", "Ascent", datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc), NAMES1)
        seed(db, UUID2, "toy-2", "Sunset", datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc), NAMES2)
    for rid in replay_ids(maker):
        assert store.rebuild(maker, rid).startswith("rebuilt")
    yield maker
    engine.dispose()


def seed(db, uuid, external_id, map_name, played, names):
    m = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name=map_name, team1_rounds_won=1,
              team2_rounds_won=1, played_at=played)
    db.add(m)
    db.flush()
    mps = []
    for slot in range(10):
        player = db.execute(select(Player).where(Player.display_name == names[slot])).scalar_one_or_none()
        if player is None:
            player = Player(display_name=names[slot])
            db.add(player)
            db.flush()
        mp = MatchPlayer(match_id=m.id, player_id=player.id, agent=AGENTS[slot],
                         team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
        db.add(mp)
        db.flush()
        mps.append(mp.id)
    replay = Replay(match_uuid=uuid, match_id=m.id, map_name=map_name, round_count=2, format_version=1,
                    recipe="r", game_branch="release-13.08", source_sha256="0" * 64, source="upload",
                    link_status="linked", link_inputs={}, clock_offset=OFFSET)
    db.add(replay)
    db.flush()
    for slot in range(10):
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent=AGENTS[slot], match_player_id=mps[slot]))
    rounds = {}
    for n, outcome, plant in ((1, "Team A Time Win", None), (2, "Team B Time Win", 140.0)):
        rnd = Round(match_id=m.id, round_number=n, outcome=outcome, planted=plant is not None, plant_time=plant)
        db.add(rnd)
        db.flush()
        rounds[n] = rnd
        db.add(ReplayRound(replay_id=replay.id, round_number=n, data=b""))
        for slot in range(10):
            db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mps[slot], loadout=4500 if slot < 5 else 2000))
    for rnd, killer, victim, when in ((1, 0, 5, OFFSET + 20.1234), (1, 1, 6, OFFSET + 30.0),
                                      (2, 5, 0, OFFSET + 25.0)):
        db.add(KillEvent(round_id=rounds[rnd].id, killer_match_player_id=mps[killer],
                         death_match_player_id=mps[victim], weapon="Vandal", event_time_seconds=when))
    db.add(ReplayRoundMoments(replay_id=replay.id, round_number=1, status="ok", fingerprint="fp1", gaps_revision=5,
                              fights={"v": 1, "sight": None, "fights": [
                                  {"t0": 19.0, "t1": 21.0, "slot": 0, "first": "player", "tags": ["won"]}]}))
    db.add(ReplayRoundUtil(replay_id=replay.id, round_number=1, status="ok", fingerprint="fp1", gaps_revision=5,
                           data={"casts": [{"t": 10.0, "t_end": 12.0, "by": 1, "agent": "Jett",
                                            "ability_id": "Jett/Smoke", "names": ["C_Smoke"],
                                            "type": "basic", "kinds": ["smoke"]}]}))
    db.add(ReplayGap(replay_id=replay.id, round_number=1, seq=0, kind="backshot", map=map_name, victim_slot=6,
                     t_open=25.0, spot_cell=1, victim_cell=2, distance_m=5.0, angle_deg=10.0, cause="flank",
                     route=[], candidate_slots=[], flicker=False))
    db.commit()


def replay_ids(maker):
    with maker() as db:
        return list(db.execute(select(Replay.id).order_by(Replay.id)).scalars())


def pid(db, name):
    return db.execute(select(Player.id).where(Player.display_name.ilike(name))).scalar_one()


def all_rows(db):
    return [{k: getattr(r, k) for k in ROW_KEYS} for r in db.execute(select(ReplayMomentIndex)).scalars()]


def memberships(db, player_id):
    """{replay id: (slot, team)} of the replays a player is in, read straight from the tables."""
    out = {}
    for rid, slot, team in db.execute(
            select(ReplayPlayer.replay_id, ReplayPlayer.slot, MatchPlayer.team)
            .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
            .where(MatchPlayer.player_id == player_id)):
        out[rid] = (slot, getattr(team, "value", team))
    return out


def expected_ctx(db, f):
    ours, slots, allowed = {}, {}, None
    if f.as_ is not None:
        ours = {rid: team for rid, (_, team) in memberships(db, pid(db, f.as_)).items()}
    if f.player is not None:
        slots = {rid: slot for rid, (slot, _) in memberships(db, pid(db, f.player)).items()}
    if f.with_ is not None:
        allowed = set(memberships(db, pid(db, f.with_)))
    return match.Context(ours, slots, None if allowed is None else frozenset(allowed))


R1_TABLE = [
    [], ["kind=kill"], ["kind=death"], ["kind=state"], ["kind=cast"], ["kind=fight"], ["kind=backshot"],
    ["kind=plant"], ["map=ascent"], ["map=sunset", "kind=kill"], ["agent=sova"], ["agent=sova", "kind=kill"],
    ["since=2026-11-05"], ["until=2026-11-05"], ["since=2026-11-01", "until=2026-11-30"],
    ["since=2026-11-05", "until=2026-11-30", "kind=kill"],
    ["as=Alpha#1"], ["as=alpha#1", "kind=death", "first=1"], ["as=Kilo#11", "kind=kill"],
    ["as=Echo#5", "won=1"], ["side=attack", "kind=kill"], ["side=defense", "kind=death"],
    ["player=Alpha#1"], ["player=Alpha#1", "kind=state"], ["player=Bravo#2", "kind=cast"],
    ["with=Echo#5"], ["with=Kilo#11", "kind=kill"], ["round=1..2", "after=10"], ["round=2", "kind=kill"],
    ["kind=kill", "first=1", "side=attack"], ["as=Alpha#1", "state=5v4"],
    # the scalar filters pushed into SQL (R1.2)
    ["ult=1"], ["ult=0", "kind=cast"], ["first=1"], ["first=0", "kind=kill"], ["planted=1"], ["planted=0", "kind=kill"],
    ["post_plant=1"], ["post_plant=0", "kind=state"], ["site=a"], ["site=b", "kind=plant"], ["left=5"],
    ["round=2"], ["round=1..1", "kind=kill"], ["after=22"], ["before=22"], ["after=10", "before=26", "kind=kill"],
    ["as=Alpha#1", "state=4v5"], ["as=Foxtrot#6", "state=5v4"], ["side=attack", "state=5v5"],
    ["side=defense", "state=4v5", "kind=kill"], ["as=Alpha#1", "our_score=0"], ["as=Foxtrot#6", "their_score=1"],
    ["side=attack", "our_score=0..1", "their_score=0..1"], ["as=Alpha#1", "our_buy=full"],
    ["as=Foxtrot#6", "their_buy=full"], ["side=attack", "their_buy=eco"], ["as=Alpha#1", "won=1"],
    ["as=Alpha#1", "won=0"], ["ability=smoke"], ["ability=sm", "kind=cast"],
    ["ability=zzz"], ["tag=won"], ["tag=won", "kind=fight"], ["tag=nope"], ["as=Alpha#1", "team=ours"],
    ["as=Alpha#1", "team=theirs", "kind=kill"],
    # streamed prefilter: tag in SQL, won / team with side= against the attack team (R2.1)
    ["tag=smoke"], ["tag=smoke", "tag=nope"], ["tag=won", "tag=smoke"], ["tag=mo"], ["tag=%"], ["tag=_"],
    ["tag=wo\\n"], ['tag=w"on'],
    ["side=attack", "won=1"], ["side=attack", "won=0"], ["side=defense", "won=1"], ["side=defense", "won=0"],
    ["side=attack", "won=1", "kind=kill"], ["side=attack", "team=ours"], ["side=attack", "team=theirs"],
    ["side=defense", "team=ours"], ["side=defense", "team=theirs", "kind=kill"],
    ["side=attack", "team=ours", "won=1"], ["side=attack", "team=ours", "tag=smoke"],
    ["player=Alpha#1", "side=attack", "team=ours"],
]


@pytest.mark.parametrize("args", R1_TABLE, ids=lambda a: " ".join(a) or "none")
def test_library_hits_equal_the_matcher_over_every_row(factory, args):
    with factory() as db:
        f = filters.parse(args)
        rows = all_rows(db)
        want = match.select(f, expected_ctx(db, f), rows)
        got = lib.search(db, args)
    assert got["total"] == len(want)
    assert [(h["uuid"], h["round"], h["t"], h["kind"]) for h in got["hits"]] == [
        (r["uuid"], r["round"], r["t"], r["kind"]) for r in want[:lib.PAGE_SIZE]]


def prefiltered(db, args):
    f = filters.parse(args)
    ctx, actor = lib._context(db, f)
    return list(lib._prefiltered_rows(db, f, ctx, actor))


def row_key(r):
    return (r["replay_id"], r["round"], r["seq"])


@pytest.mark.parametrize("args", R1_TABLE, ids=lambda a: " ".join(a) or "none")
def test_the_sql_prefilter_is_a_superset_of_the_matcher(factory, args):
    with factory() as db:
        f = filters.parse(args)
        want = {row_key(r) for r in match.select(f, expected_ctx(db, f), all_rows(db))}
        got = {row_key(r) for r in prefiltered(db, args)}
    assert want <= got


def test_ult_without_a_kind_reads_only_ult_rows(factory):
    with factory() as db:
        db.execute(text("UPDATE replay_moment_index SET ult=1 WHERE kind='cast'"))
        db.commit()
        total = len(all_rows(db))
        rows = prefiltered(db, ["ult=1"])
        assert 0 < len(rows) < total and all(r["ult"] for r in rows)
        assert lib.search(db, ["ult=1"])["total"] == len(rows)


@pytest.mark.parametrize("args,column", [
    (["first=1"], lambda r: r["first"]), (["planted=1"], lambda r: r["planted"]),
    (["round=2"], lambda r: r["round"] == 2), (["after=25"], lambda r: r["t"] >= 25),
    (["before=11"], lambda r: r["t"] <= 11), (["ability=smoke"], lambda r: r["kind"] == "cast"),
    (["as=Alpha#1", "state=4v5"], lambda r: {r["alive1"], r["alive2"]} == {4, 5}),
    (["tag=smoke"], lambda r: "smoke" in r["tags"]),
    (["side=attack", "won=1"], lambda r: r["won_team"] == r["attack_team"]),
    (["side=attack", "won=0"], lambda r: r["won_team"] != r["attack_team"]),
    (["side=defense", "won=1"], lambda r: r["won_team"] != r["attack_team"]),
    (["side=attack", "team=ours"], lambda r: r["team"] == r["attack_team"]),
    (["side=attack", "team=theirs"], lambda r: r["team"] != r["attack_team"]),
])
def test_scalar_filters_narrow_what_sql_reads(factory, args, column):
    with factory() as db:
        total = len(all_rows(db))
        rows = prefiltered(db, args)
        assert 0 < len(rows) < total and all(column(r) for r in rows)


def test_prefiltered_rows_are_plain_dicts_with_the_row_keys(factory):
    with factory() as db:
        rows = prefiltered(db, ["kind=kill"])
    assert rows and all(type(r) is dict and tuple(r) == ROW_KEYS for r in rows)


def test_the_prefilter_is_streamed_not_listed(factory):
    with factory() as db:
        f = filters.parse([])
        ctx, actor = lib._context(db, f)
        rows = lib._prefiltered_rows(db, f, ctx, actor)
        assert not isinstance(rows, (list, tuple)) and iter(rows) is rows


def test_search_hands_the_matcher_an_iterator_and_never_a_list(factory, monkeypatch):
    seen = []
    real = match.select

    def spy(f, ctx, rows):
        seen.append(rows)
        return real(f, ctx, rows)

    monkeypatch.setattr(lib.match, "select", spy)
    with factory() as db:
        lib.search(db, ["tag=smoke"])
    assert len(seen) == 1 and not isinstance(seen[0], (list, tuple))


def test_the_query_is_executed_with_yield_per(factory, monkeypatch):
    options = []
    with factory() as db:
        real = db.execute

        def spy(statement, *a, **kw):
            options.append(getattr(statement, "get_execution_options", lambda: {})())
            return real(statement, *a, **kw)

        monkeypatch.setattr(db, "execute", spy)
        lib.search(db, ["kind=kill"])
    assert any(o.get("yield_per") == 2000 for o in options)


@pytest.mark.parametrize("tag", ["smoke", "SMOKE", "sm", "mo", "%", "_", "a b", 'w"on', "wo\\n", "é", "x\ny"])
def test_tag_condition_is_a_superset_for_odd_values(factory, tag):
    with factory() as db:
        f = filters.parse([f"tag={tag}"])
        want = {row_key(r) for r in match.select(f, expected_ctx(db, f), all_rows(db))}
        got = {row_key(r) for r in prefiltered(db, [f"tag={tag}"])}
    assert want <= got


def test_the_tag_needle_is_the_quoted_json_element(factory):
    # the text form of a stored tag list holds `"smoke"` with its quotes on both dialects
    with factory() as db:
        text_form = db.execute(text("SELECT tags FROM replay_moment_index WHERE kind='cast'")).scalars().first()
    assert '"smoke"' in text_form


def test_the_r1_table_is_not_vacuous(factory):
    with factory() as db:
        for args in (["map=ascent"], ["agent=sova", "kind=kill"], ["since=2026-11-05"], ["until=2026-11-05"],
                     ["kind=death", "first=1", "side=attack"]):
            assert lib.search(db, args)["total"] > 0, args
        both = lib.search(db, [])["total"]
        assert lib.search(db, ["map=ascent"])["total"] < both
        assert 0 < lib.search(db, ["since=2026-11-05"])["total"] < both
        assert {h["uuid"] for h in lib.search(db, ["since=2026-11-05"])["hits"]} == {UUID2}
        assert {h["uuid"] for h in lib.search(db, ["until=2026-11-05"])["hits"]} == {UUID1}
        assert {h["map"] for h in lib.search(db, ["map=ascent"])["hits"]} == {"Ascent"}


def test_as_unknown_riot_id_is_a_filter_error_naming_it(factory):
    with factory() as db:
        with pytest.raises(filters.FilterError, match="Nobody#0"):
            lib.search(db, ["as=Nobody#0"])
        with pytest.raises(filters.FilterError, match="Ghost#1"):
            lib.search(db, ["player=Ghost#1"])


def test_filter_errors_propagate(factory):
    with factory() as db:
        with pytest.raises(filters.FilterError):
            lib.search(db, ["bogus=1"])


def test_as_a_player_only_in_replay_one(factory):
    with factory() as db:
        got = lib.search(db, ["as=Echo#5"])
        assert got["total"] > 0 and {h["uuid"] for h in got["hits"]} == {UUID1}
        both = lib.search(db, ["as=Alpha#1"])
        assert {h["uuid"] for h in both["hits"]} == {UUID1, UUID2}


def test_with_keeps_only_replays_that_player_is_in(factory):
    with factory() as db:
        assert {h["uuid"] for h in lib.search(db, ["with=Echo#5"])["hits"]} == {UUID1}
        assert {h["uuid"] for h in lib.search(db, ["with=Kilo#11"])["hits"]} == {UUID2}
        assert {h["uuid"] for h in lib.search(db, ["with=Alpha#1"])["hits"]} == {UUID1, UUID2}
        with pytest.raises(filters.FilterError, match="Nobody#0"):
            lib.search(db, ["with=Nobody#0"])


def test_circle_keeps_replays_with_any_member_and_intersects_with_with(factory):
    with factory() as db:
        echo, foxtrot, kilo = pid(db, "Echo#5"), pid(db, "Foxtrot#6"), pid(db, "Kilo#11")
        db.add(Friendship(owner_player_id=echo, friend_player_id=foxtrot))
        db.commit()
        # Echo's circle: Echo and Foxtrot, both only in replay 1
        assert {h["uuid"] for h in lib.search(db, ["circle=Echo#5"])["hits"]} == {UUID1}
        # a circle that reaches into replay 2 through a friend
        db.add(Friendship(owner_player_id=foxtrot, friend_player_id=kilo))
        db.commit()
        db.add(Friendship(owner_player_id=echo, friend_player_id=kilo))
        db.commit()
        assert {h["uuid"] for h in lib.search(db, ["circle=Echo#5"])["hits"]} == {UUID1, UUID2}
        # intersection with `with`
        assert {h["uuid"] for h in lib.search(db, ["circle=Echo#5", "with=Kilo#11"])["hits"]} == {UUID2}
        assert {h["uuid"] for h in lib.search(db, ["circle=Echo#5", "with=Echo#5"])["hits"]} == {UUID1}
        with pytest.raises(filters.FilterError, match="Nobody#0"):
            lib.search(db, ["circle=Nobody#0"])
        with pytest.raises(filters.FilterError, match="Nobody#0"):
            lib.search(db, ["circle=Echo#5", "with=Nobody#0"])


def test_circle_and_with_with_no_overlap_is_empty(factory):
    with factory() as db:
        db.add(Friendship(owner_player_id=pid(db, "Echo#5"), friend_player_id=pid(db, "Foxtrot#6")))
        db.commit()
        assert lib.search(db, ["circle=Echo#5"])["total"] > 0
        assert lib.search(db, ["with=Kilo#11"])["total"] > 0
        got = lib.search(db, ["circle=Echo#5", "with=Kilo#11"])
        assert got["total"] == 0 and got["hits"] == []


def test_newest_first(factory):
    with factory() as db:
        hits = lib.search(db, [])["hits"]
    assert hits[0]["uuid"] == UUID2 and hits[-1]["uuid"] == UUID1
    keys = [(h["uuid"] != UUID2, h["round"], h["t"]) for h in hits]
    assert keys == sorted(keys)


def clones(db, count):
    base = next(r for r in all_rows(db) if r["kind"] == "kill" and r["uuid"] == UUID2)
    return [dict(base, seq=1000 + i, t=float(i)) for i in range(count)]


def test_paging(factory):
    with factory() as db:
        rows = clones(db, 51)
        one = lib.search(db, ["kind=kill"], rows=rows)
        assert (one["total"], one["page"], one["pages"], len(one["hits"])) == (51, 1, 2, 50)
        two = lib.search(db, ["kind=kill"], rows=rows, page=2)
        assert (two["total"], two["page"], two["pages"], len(two["hits"])) == (51, 2, 2, 1)
        assert two["hits"][0]["t"] == 50.0
        via_arg = lib.search(db, ["kind=kill", "page=2"], rows=rows)
        assert via_arg["page"] == 2 and len(via_arg["hits"]) == 1
        three = lib.search(db, ["kind=kill"], rows=rows, page=3)
        assert three["hits"] == [] and three["total"] == 51
        assert lib.PAGE_SIZE == 50


def test_link_offsets_three_seconds_and_never_below_zero(factory):
    with factory() as db:
        rows = clones(db, 2)
        rows[0]["t"], rows[1]["t"] = 2.0, 41.47
        hits = lib.search(db, ["kind=kill"], rows=rows)["hits"]
    by_t = {h["t"]: h for h in hits}
    assert by_t[2.0]["link"].endswith("&t=0")
    assert by_t[2.0]["link"].startswith(f"/replays/{UUID2}?round=")
    assert by_t[41.47]["link"].endswith("&t=38.5")


def test_hit_shape_and_score(factory):
    with factory() as db:
        hit = lib.search(db, ["kind=kill", "map=ascent", "round=2"])["hits"][0]
        assert set(hit) == {"uuid", "map", "played_at", "round", "score", "kind", "t", "summary", "link"}
        assert hit["score"] == "1-0"  # team-1 - team-2, before round 2, no perspective
        mine = lib.search(db, ["kind=kill", "as=Foxtrot#6", "map=ascent", "round=2"])["hits"][0]
        assert mine["score"] == "0-1"  # ours (team-2) - theirs


def test_names_enemy_by_agent_and_ours_by_name(factory):
    with factory() as db:
        got = lib.search(db, ["kind=kill", "as=Alpha#1", "map=ascent", "round=1"])["hits"]
        first = got[0]["summary"]
        assert first.startswith("Alpha#1 killed the enemy Viper")
        death = lib.search(db, ["kind=death", "as=Alpha#1", "map=ascent", "round=2"])["hits"][0]["summary"]
        assert death.startswith("Alpha#1 was killed by the enemy Viper")
        assert "Foxtrot#6" not in death
        theirs = lib.search(db, ["kind=kill", "as=Foxtrot#6", "map=ascent", "round=1"])["hits"][0]["summary"]
        assert theirs.startswith("the enemy Sova killed Foxtrot#6")


def test_no_perspective_uses_agents_only(factory):
    with factory() as db:
        for hit in lib.search(db, ["kind=kill"])["hits"]:
            assert "#" not in hit["summary"] and "the enemy" not in hit["summary"]
        got = lib.search(db, ["kind=kill", "map=ascent", "round=1"])["hits"][0]["summary"]
        assert got.startswith("Sova killed Viper")


def test_rows_of_another_revision_are_not_read(factory):
    with factory() as db:
        full = lib.search(db, [])["total"]
        r2 = replay_ids(factory)[1]
        db.execute(text("UPDATE replay_moment_index_state SET revision=revision-1 WHERE replay_id=:r"), {"r": r2})
        db.commit()
        got = lib.search(db, [])
        assert 0 < got["total"] < full
        assert {h["uuid"] for h in got["hits"]} == {UUID1}


def test_rows_given_are_used_without_the_database_rows(factory):
    with factory() as db:
        rows = [r for r in all_rows(db) if r["uuid"] == UUID1]
        got = lib.search(db, [], rows=rows)
        assert {h["uuid"] for h in got["hits"]} == {UUID1} and got["total"] == len(rows)


def test_coach_lines(factory):
    with factory() as db:
        result = lib.search(db, ["kind=kill", "as=Alpha#1", "map=ascent", "round=1"])
    lines = lib.coach_lines(result)
    hit = result["hits"][0]
    assert lines[0] == f"{hit['uuid']} round {hit['round']} t {hit['t']} {hit['kind']} {hit['summary']}"
    assert len(lines) == len(result["hits"])


# ---- the CLI

def test_cli_coach_format(factory, monkeypatch, capsys):
    from scripts import replay_search as cli

    monkeypatch.setattr("app.db.SessionLocal", factory)
    assert cli.main(["kind=death", "first=1", "side=attack", "--format", "coach"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out and all(" death " in line for line in out)
    with factory() as db:
        want = lib.coach_lines(lib.search(db, ["kind=death", "first=1", "side=attack"]))
    assert out == want


def test_cli_json_lines_end_with_the_totals(factory, monkeypatch, capsys):
    from scripts import replay_search as cli

    monkeypatch.setattr("app.db.SessionLocal", factory)
    assert cli.main(["kind=kill"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert lines[-1] == {"total": 6, "page": 1, "pages": 1}
    assert len(lines) == 7 and all("link" in h for h in lines[:-1])


def test_cli_bad_filter_exits_2(factory, monkeypatch, capsys):
    from scripts import replay_search as cli

    monkeypatch.setattr("app.db.SessionLocal", factory)
    assert cli.main(["nonsense=1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error: ") and "nonsense" in captured.err


def test_cli_status(factory, monkeypatch, capsys):
    from scripts import replay_search as cli

    monkeypatch.setattr("app.db.SessionLocal", factory)
    assert cli.main(["--status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"linked": 2, "indexed": 2, "missing": 0, "stale": 0,
                                                    "failed": 0}


def test_since_until_are_documented_as_utc_days_of_the_match_start():
    from scripts import replay_search as cli

    for doc in (cli.__doc__, filters.__doc__):
        assert "UTC" in doc and "since" in doc and "until" in doc


def test_index_revision_is_the_gate_constant():
    assert isinstance(index.INDEX_REVISION, int)
