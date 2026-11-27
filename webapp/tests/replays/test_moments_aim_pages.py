"""The player page's Aim section (GET /players/{name}/aim): the figures with n and IQR, the all-players figure beside
them, the agent filter, linked accounts, the demo, and that no opponent is named. SQLite, rows seeded directly."""

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))

from tests.support.site import site  # noqa: E402,F401  (fixture)

from app.config import settings  # noqa: E402
from app.models import AccountGroup, AccountGroupMember, Match, MatchPlayer, Player  # noqa: E402
from app.models.match import MatchSource, Team  # noqa: E402
from app.models.replay import Replay, ReplayAimEvent, ReplayPlayer, ReplayRound  # noqa: E402
from app.services import aim_stats, replay_gaps  # noqa: E402

FP = "f" * 16
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
STRANGER = "Rivalname"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP)
    aim_stats._EVERYONE.clear()
    yield
    aim_stats._EVERYONE.clear()


def _player(db, name):
    p = Player(display_name=name)
    db.add(p)
    db.flush()
    return p


def _match(db, day, agents_by_player, *, stranger, events):
    """One match with one replay. `agents_by_player`: [(player, agent, n_events, err)]; the stranger is on the
    other team with `events` events at err 4.5."""
    match = Match(external_id=str(uuid4()), source=MatchSource.SCRAPED, map_name="Ascent",
                  played_at=T0 + timedelta(days=day))
    db.add(match)
    db.flush()
    replay = Replay(match_uuid=str(uuid4()), match_id=match.id, map_name="Ascent", round_count=1, format_version=1,
                    recipe="r", game_branch="b", source_sha256=uuid4().hex, source="upload", link_status="linked",
                    link_inputs={}, linked_at=T0)
    db.add(replay)
    db.flush()
    db.add(ReplayRound(replay_id=replay.id, round_number=1, data=b""))
    db.flush()
    rows = [(p, agent, n, err, Team.TEAM_1) for p, agent, n, err in agents_by_player]
    rows.append((stranger, "Sage", events, 4.5, Team.TEAM_2))
    idx = 0
    for slot, (p, agent, n, err, team) in enumerate(rows):
        mp = MatchPlayer(match_id=match.id, player_id=p.id, agent=agent, team=team)
        db.add(mp)
        db.flush()
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent=agent, match_player_id=mp.id))
        for _ in range(n):
            db.add(ReplayAimEvent(
                replay_id=replay.id, round_number=1, idx=idx, fingerprint=FP, gaps_revision=replay_gaps.GAPS_REVISION,
                slot=slot, fight_i=0, opponent=0, t=10.0, cls="ok", err=err, err_125=None, err_250=None, d_m=5.0,
                fine=True, hold=None, opener=None, gun_class="rifle", hp=100, enemy_hp=100, outcome="won", t_end=11.0,
                on_times=[0.2] * 14, shot=0.3, dmg=0.5))
            idx += 1
    db.flush()


def _fragment(client, name="Aimer", query=""):
    return client.get(f"/players/{name}/aim{query}")


def _you(text):
    """The player's own placement row (the everyone row and the trend hold other figures)."""
    return re.search(r"<tr data-aim-you>(.*?)</tr>", text, re.S).group(1)


def test_player_with_events_shows_median_n_and_everyone(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 12, 1.5)], stranger=other, events=12)
        db.commit()
    r = _fragment(client)
    assert r.status_code == 200
    assert "Horizontal only: the replays have no pitch, so head height isn't judged." in r.text
    assert "1.50" in r.text and "n=12" in r.text
    assert "Everyone" in r.text and "3.00" in r.text and "n=24" in r.text
    assert "IQR" in r.text
    assert "0.4 m" in r.text and "0.5" in r.text           # the rule in words


def test_player_without_events_says_none_measured(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [], stranger=other, events=12)
        db.add(MatchPlayer(match_id=db.query(Match).one().id, player_id=me.id, agent="Jett", team=Team.TEAM_1))
        db.commit()
    r = _fragment(client)
    assert r.status_code == 200
    assert "no replays measured yet" in r.text.lower()


def test_a_cell_under_ten_events_says_too_few(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 4, 1.5)], stranger=other, events=12)
        db.commit()
    r = _fragment(client)
    assert "too few (n=4)" in _you(r.text)
    assert "1.50" not in _you(r.text)


def test_agent_filter_keeps_only_that_agents_matches(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 12, 1.5)], stranger=other, events=0)
        _match(db, 1, [(me, "Sage", 12, 6.0)], stranger=other, events=0)
        db.commit()
    assert "n=24" in _you(_fragment(client).text)
    only = _you(_fragment(client, query="?filter=agent:Jett").text)
    assert "1.50" in only and "6.00" not in only and "n=12" in only


def test_linked_accounts_pool_with_one_row_per_shared_match(site):
    client, factory = site
    with factory() as db:
        a1, a2, other = _player(db, "Aimer"), _player(db, "Aimer2"), _player(db, STRANGER)
        group = AccountGroup(main_player_id=a1.id)
        db.add(group)
        db.flush()
        for p in (a1, a2):
            db.add(AccountGroupMember(player_id=p.id, group_id=group.id))
        _match(db, 0, [(a1, "Jett", 12, 1.5), (a2, "Jett", 12, 2.5)], stranger=other, events=0)
        _match(db, 1, [(a2, "Jett", 12, 2.5)], stranger=other, events=0)
        db.commit()
    assert "n=12" in _you(_fragment(client).text)
    pooled = _you(_fragment(client, query="?accounts=all").text)
    assert "n=24" in pooled and "n=36" not in pooled


def test_demo_mode_404s_and_the_page_has_no_aim_div(site, monkeypatch):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 12, 1.5)], stranger=other, events=12)
        db.commit()
    page = client.get("/players/Aimer")
    assert page.status_code == 200 and 'id="aim-section"' in page.text
    assert "/players/Aimer/aim" in page.text
    from app.templates import templates
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setitem(templates.env.globals, "demo_mode", True)   # the template global is read at import
    assert _fragment(client).status_code == 404
    demo_page = client.get("/players/Aimer")
    assert 'id="aim-section"' not in demo_page.text


def test_no_opponent_name_in_the_fragment(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 12, 1.5)], stranger=other, events=12)
        db.commit()
    assert STRANGER not in _fragment(client).text


def _stats_aim(client, group="all"):
    return client.get(f"/stats/aim?group={group}")


def _log_in(client, name):
    assert client.post("/login", data={"display_name": name}).status_code in (200, 303)


def _circle_world(db):
    """Aimer (viewer), Pal and Rare (friends, 12 and 4 events), the stranger (12 events, errors 4.5)."""
    from app.models import Friendship
    me, pal, rare, other = (_player(db, n) for n in ("Aimer", "Palname", "Rareplayer", STRANGER))
    db.add(Friendship(owner_player_id=me.id, friend_player_id=pal.id))
    db.add(Friendship(owner_player_id=me.id, friend_player_id=rare.id))
    _match(db, 0, [(me, "Jett", 12, 1.5), (pal, "Sova", 12, 3.0), (rare, "Sage", 4, 2.0)], stranger=other, events=12)
    db.commit()


def test_stats_aim_all_is_one_pooled_row_with_n_and_no_names(site):
    client, factory = site
    with factory() as db:
        _circle_world(db)
    r = _stats_aim(client, "all")
    assert r.status_code == 200
    assert r.text.count("data-aim-pooled") == 1 and "data-aim-member" not in r.text
    assert "n=40" in r.text and "IQR" in r.text
    assert "Horizontal only" in r.text
    for name in ("Aimer", "Palname", "Rareplayer", STRANGER):
        assert name not in r.text


def test_stats_aim_friends_has_a_row_per_member_over_the_threshold_and_a_pooled_row(site):
    client, factory = site
    with factory() as db:
        _circle_world(db)
    _log_in(client, "Aimer")
    r = _stats_aim(client, "friends")
    assert r.status_code == 200
    assert r.text.count("data-aim-member") == 2
    assert "Aimer" in r.text and "Palname" in r.text
    assert "Rareplayer" not in r.text and STRANGER not in r.text
    pooled = re.search(r"<tr data-aim-pooled>(.*?)</tr>", r.text, re.S).group(1)
    assert "n=28" in pooled


def test_stats_aim_friends_logged_out_is_the_login_note(site):
    client, factory = site
    with factory() as db:
        _circle_world(db)
    r = _stats_aim(client, "friends")
    assert r.status_code == 200
    assert "needs a login" in r.text and "data-aim-pooled" not in r.text


def test_stats_aim_demo_404s_and_the_stats_page_has_no_aim_card(site, monkeypatch):
    client, factory = site
    with factory() as db:
        _circle_world(db)
    page = client.get("/stats/all")
    assert page.status_code == 200 and "/stats/aim?group=all" in page.text
    from app.templates import templates
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setitem(templates.env.globals, "demo_mode", True)
    assert _stats_aim(client).status_code == 404
    assert "/stats/aim" not in client.get("/stats/all").text


def test_trend_table_shows_n_and_date_range_and_pools_small_matches(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        for day in range(4):
            _match(db, day, [(me, "Jett", 5, 1.5)], stranger=other, events=0)
        db.commit()
    html = _fragment(client).text
    table = re.search(r"data-trend-table.*?</table>", html, re.S).group(0)
    assert table.count("<tr>") == 3        # header and two windows of 10
    assert "2026-11-01" in table and "2026-11-02" in table and "2026-11-03" in table and "2026-11-04" in table
    assert "n=10" in table and "IQR" in table
    assert "pools matches to at least 10 events" in html


def test_trend_with_under_ten_events_says_too_few_and_shows_no_figure(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 4, 1.77)], stranger=other, events=0)
        _match(db, 1, [(me, "Jett", 4, 1.77)], stranger=other, events=0)
        db.commit()
    html = _fragment(client).text
    assert "data-trend-too-few" in html and "too few (n=8)" in html
    assert "1.77" not in html and "<canvas" not in html and "data-trend-table" not in html


def test_trend_is_a_table_under_three_matches_and_a_chart_from_three(site):
    client, factory = site
    with factory() as db:
        me, other = _player(db, "Aimer"), _player(db, STRANGER)
        _match(db, 0, [(me, "Jett", 12, 1.5)], stranger=other, events=0)
        _match(db, 1, [(me, "Jett", 12, 2.0)], stranger=other, events=0)
        db.commit()
    two = _fragment(client).text
    assert "<canvas" not in two and "data-trend-table" in two
    with factory() as db:
        me = db.query(Player).filter_by(display_name="Aimer").one()
        other = db.query(Player).filter_by(display_name=STRANGER).one()
        _match(db, 2, [(me, "Jett", 12, 2.5)], stranger=other, events=0)
        db.commit()
    three = _fragment(client).text
    assert "<canvas" in three and re.search(r"aim-trend", three)
