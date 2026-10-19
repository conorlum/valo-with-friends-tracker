"""A person's circle: the accounts shown as "friends" on their pages.

A person is a group. Your circle is all your accounts, plus everyone friended
from any of your accounts, plus each of those friends' own grouped accounts.
One level only: a friend's friends are not in it. Friendships stay per
account; the circle reads them through the group.

Each function is one SQL statement (group expansion in subqueries), so a page
can ask for a circle without adding round trips.
"""

from sqlalchemy import literal, select, union
from sqlalchemy.orm import Session, aliased

from app.models import AccountGroupMember, Friendship


def _with_group_members(ids):
    """`ids` (a one-column select) plus every account in the group of any of them."""
    mine = aliased(AccountGroupMember)
    sibling = aliased(AccountGroupMember)
    siblings = (
        select(sibling.player_id)
        .select_from(mine)
        .join(sibling, sibling.group_id == mine.group_id)
        .where(mine.player_id.in_(ids))
    )
    return union(ids, siblings)


def _own_select(player_id: int):
    return _with_group_members(select(literal(player_id).label("player_id"))).subquery()


def _circle_rows(db: Session, player_id: int) -> list[tuple[int, int]]:
    """(account id, 1 if it is one of `player_id`'s own accounts else 0), one statement."""
    own = _own_select(player_id)
    own_ids = select(own.c.player_id)
    friends = select(Friendship.friend_player_id.label("player_id")).where(Friendship.owner_player_id.in_(own_ids))
    friends_and_alts = _with_group_members(friends).subquery()
    stmt = union(
        select(own.c.player_id, literal(1).label("is_own")),
        select(friends_and_alts.c.player_id, literal(0).label("is_own")),
    )
    return [(row[0], row[1]) for row in db.execute(stmt)]


def own_account_ids(db: Session, player_id: int) -> set[int]:
    """`player_id`'s group members, or just itself when it is in no group."""
    own = _own_select(player_id)
    return {row[0] for row in db.execute(select(own.c.player_id))}


def circle_ids(db: Session, player_id: int) -> set[int]:
    """Own accounts, everyone any of them friended, and those friends' group members."""
    return {pid for pid, _ in _circle_rows(db, player_id)}


def circle_friend_ids(db: Session, player_id: int) -> set[int]:
    """The circle without `player_id` itself (its own other accounts stay).
    A superset of friends.list_friend_ids."""
    return {pid for pid, _ in _circle_rows(db, player_id)} - {player_id}


def circle_teammate_ids(db: Session, player_id: int) -> set[int]:
    """The circle without any of `player_id`'s own accounts: the fight-EV pool."""
    rows = _circle_rows(db, player_id)
    own = {pid for pid, is_own in rows if is_own}
    return {pid for pid, _ in rows} - own


def circle_dependents(db: Session, account_ids: set[int]) -> set[int]:
    """Every account whose circle can change when the groups or friendships of
    `account_ids` change: those accounts and their groups, every account that
    friends any of them, and those friending accounts' groups."""
    if not account_ids:
        return set()
    seed_rows = union(*(select(literal(i).label("player_id")) for i in sorted(account_ids))).subquery()
    grouped = _with_group_members(select(seed_rows.c.player_id)).subquery()
    frienders = select(Friendship.owner_player_id.label("player_id")).where(
        Friendship.friend_player_id.in_(select(grouped.c.player_id))
    )
    friender_accounts = _with_group_members(frienders).subquery()
    everyone = union(select(grouped.c.player_id), select(friender_accounts.c.player_id))
    return {row[0] for row in db.execute(everyone)}
