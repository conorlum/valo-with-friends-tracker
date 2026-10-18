from sqlalchemy import event

from app.models import Friendship
from app.services.account_groups import link_account
from app.services.circle import (
    circle_dependents,
    circle_friend_ids,
    circle_ids,
    circle_teammate_ids,
    own_account_ids,
)
from tests.test_account_groups import _players, db  # noqa: F401


def _group(db, main, *subs):
    for sub in subs:
        assert link_account(db, main.id, sub.id) is None
    db.commit()


def _friend(db, owner, friend):
    db.add(Friendship(owner_player_id=owner.id, friend_player_id=friend.id))
    db.commit()


def _ids(*players):
    return {p.id for p in players}


def test_an_ungrouped_player_with_no_friends_is_a_circle_of_one(db):
    (x,) = _players(db, "x#1")
    db.commit()
    assert own_account_ids(db, x.id) == {x.id}
    assert circle_ids(db, x.id) == {x.id}
    assert circle_friend_ids(db, x.id) == set()
    assert circle_teammate_ids(db, x.id) == set()


def test_own_alts_are_in_the_circle_from_the_main_and_from_a_sub(db):
    x, alt, other = _players(db, "x#1", "alt#2", "other#3")
    _group(db, x, alt)
    for who in (x, alt):
        assert own_account_ids(db, who.id) == _ids(x, alt)
        assert circle_ids(db, who.id) == _ids(x, alt)
    assert other.id not in circle_ids(db, x.id)


def test_a_friends_alts_are_in_the_circle(db):
    x, f, f_alt = _players(db, "x#1", "f#2", "f_alt#3")
    _group(db, f, f_alt)
    _friend(db, x, f)
    assert circle_ids(db, x.id) == _ids(x, f, f_alt)


def test_a_friend_of_a_friend_is_not_in_the_circle(db):
    x, f, ff, ff_alt = _players(db, "x#1", "f#2", "ff#3", "ff_alt#4")
    _group(db, ff, ff_alt)
    _friend(db, x, f)
    _friend(db, f, ff)
    assert circle_ids(db, x.id) == _ids(x, f)


def test_a_friendship_owned_by_an_alt_counts_for_the_main_and_the_other_alt(db):
    x, alt, f = _players(db, "x#1", "alt#2", "f#3")
    _group(db, x, alt)
    _friend(db, alt, f)
    assert circle_ids(db, x.id) == _ids(x, alt, f)
    assert circle_ids(db, alt.id) == _ids(x, alt, f)


def test_a_friendship_owned_by_the_main_counts_for_a_sub(db):
    x, alt, f = _players(db, "x#1", "alt#2", "f#3")
    _group(db, x, alt)
    _friend(db, x, f)
    assert circle_ids(db, alt.id) == _ids(x, alt, f)


def test_nobody_who_only_friended_x_is_in_x_circle(db):
    x, f = _players(db, "x#1", "f#2")
    _friend(db, f, x)
    assert circle_ids(db, x.id) == {x.id}


def test_circle_friend_ids_drops_only_x_and_keeps_its_alts(db):
    x, alt, f, f_alt = _players(db, "x#1", "alt#2", "f#3", "f_alt#4")
    _group(db, x, alt)
    _group(db, f, f_alt)
    _friend(db, x, f)
    assert circle_friend_ids(db, x.id) == _ids(alt, f, f_alt)
    assert circle_friend_ids(db, alt.id) == _ids(x, f, f_alt)


def test_circle_teammate_ids_drops_every_own_account(db):
    x, alt, f, f_alt = _players(db, "x#1", "alt#2", "f#3", "f_alt#4")
    _group(db, x, alt)
    _group(db, f, f_alt)
    _friend(db, alt, f)
    assert circle_teammate_ids(db, x.id) == _ids(f, f_alt)


def test_a_friend_who_is_also_an_own_account_stays_own(db):
    x, alt = _players(db, "x#1", "alt#2")
    _group(db, x, alt)
    _friend(db, x, alt)
    assert circle_teammate_ids(db, x.id) == set()
    assert circle_friend_ids(db, x.id) == {alt.id}


def test_dependents_of_a_friended_account_are_its_friender_and_the_friender_group(db):
    a, a_alt, f, other = _players(db, "a#1", "a_alt#2", "f#3", "other#4")
    _group(db, a, a_alt)
    _friend(db, a, f)
    assert circle_dependents(db, {f.id}) == _ids(f, a, a_alt)
    assert other.id not in circle_dependents(db, {f.id})


def test_dependents_include_the_group_of_the_account_itself(db):
    x, alt, other = _players(db, "x#1", "alt#2", "other#3")
    _group(db, x, alt)
    assert circle_dependents(db, {alt.id}) == _ids(x, alt)


def test_dependents_include_the_frienders_of_every_group_member(db):
    x, alt, a, b = _players(db, "x#1", "alt#2", "a#3", "b#4")
    _group(db, x, alt)
    _friend(db, a, alt)
    _friend(db, b, x)
    assert circle_dependents(db, {x.id}) == _ids(x, alt, a, b)


def test_dependents_of_an_ungrouped_unfriended_account_is_itself(db):
    (x,) = _players(db, "x#1")
    db.commit()
    assert circle_dependents(db, {x.id}) == {x.id}


def test_dependents_of_nothing_is_nothing_and_runs_no_query(db):
    statements = _count_statements(db)
    assert circle_dependents(db, set()) == set()
    assert statements == []


def _count_statements(db):
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *args: statements.append(args[2]))
    return statements


def test_each_function_issues_exactly_one_statement(db):
    x, alt, f, f_alt = _players(db, "x#1", "alt#2", "f#3", "f_alt#4")
    _group(db, x, alt)
    _group(db, f, f_alt)
    _friend(db, x, f)
    db.commit()
    x_id, f_id = x.id, f.id   # read before counting: a committed row's attributes reload
    statements = _count_statements(db)
    for fn in (own_account_ids, circle_ids, circle_friend_ids, circle_teammate_ids):
        statements.clear()
        fn(db, x_id)
        assert len(statements) == 1, fn.__name__
    statements.clear()
    circle_dependents(db, {x_id, f_id})
    assert len(statements) == 1
