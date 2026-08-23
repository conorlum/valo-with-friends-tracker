from dataclasses import dataclass

from sqlalchemy import and_, func
from sqlalchemy.orm import Session, aliased

from app.models import Friendship, MatchPlayer, Player


def get_current_player_and_friendship(
    db: Session, session_player_id: int | None, target_player_id: int
) -> tuple[Player | None, bool]:
    """Merges the player-page router's Q3 (get_current_player) and Q4
    (list_friend_ids, narrowed to just the one relationship the page needs)
    into one round trip -- an OUTER join so "not a friend" still returns the
    session player row. Returns (None, False) with NO query at all when
    there's no session, matching app.services.auth.get_current_player's own
    short-circuit for a logged-out visitor. See docs/
    player_page_render_speed.txt Step 1(b)."""
    if session_player_id is None:
        return None, False
    row = (
        db.query(Player, Friendship)
        .outerjoin(
            Friendship,
            and_(Friendship.owner_player_id == Player.id, Friendship.friend_player_id == target_player_id),
        )
        .filter(Player.id == session_player_id)
        .one_or_none()
    )
    if row is None:
        return None, False
    current_player, friendship = row
    is_friend = friendship is not None and current_player.id != target_player_id
    return current_player, is_friend


def list_friend_ids(db: Session, owner_player_id: int) -> set[int]:
    return {
        row[0]
        for row in db.query(Friendship.friend_player_id).filter_by(owner_player_id=owner_player_id).all()
    }


def list_friends(db: Session, owner_player_id: int) -> list[Player]:
    return (
        db.query(Player)
        .join(Friendship, Friendship.friend_player_id == Player.id)
        .filter(Friendship.owner_player_id == owner_player_id)
        .order_by(Player.display_name)
        .all()
    )


def add_friend(db: Session, owner_player_id: int, friend_player_id: int) -> bool:
    """Returns whether anything changed. Does NOT commit -- the caller
    commits, so the mutation and the cache invalidation that must
    accompany it land in one transaction."""
    if owner_player_id == friend_player_id:
        return False
    exists = (
        db.query(Friendship)
        .filter_by(owner_player_id=owner_player_id, friend_player_id=friend_player_id)
        .one_or_none()
    )
    if exists is None:
        db.add(Friendship(owner_player_id=owner_player_id, friend_player_id=friend_player_id))
        return True
    return False


def remove_friend(db: Session, owner_player_id: int, friend_player_id: int) -> bool:
    """Returns whether anything changed. Does NOT commit -- see add_friend."""
    deleted = (
        db.query(Friendship)
        .filter_by(owner_player_id=owner_player_id, friend_player_id=friend_player_id)
        .delete()
    )
    return bool(deleted)


@dataclass
class Acquaintance:
    player: Player
    shared_matches: int


def list_acquaintances(db: Session, player_id: int, min_shared_matches: int = 2) -> list[Acquaintance]:
    """Players who've shared at least `min_shared_matches` matches with
    `player_id` -- a low-friction "people you might want to add as a friend"
    suggestion list, derived on the fly rather than stored.
    """
    Mine = aliased(MatchPlayer)
    Theirs = aliased(MatchPlayer)

    rows = (
        db.query(Theirs.player_id, func.count(func.distinct(Mine.match_id)))
        .join(Mine, Mine.match_id == Theirs.match_id)
        .filter(Mine.player_id == player_id, Theirs.player_id != player_id)
        .group_by(Theirs.player_id)
        .having(func.count(func.distinct(Mine.match_id)) >= min_shared_matches)
        .all()
    )
    if not rows:
        return []

    players_by_id = {p.id: p for p in db.query(Player).filter(Player.id.in_([pid for pid, _ in rows])).all()}
    acquaintances = [
        Acquaintance(player=players_by_id[pid], shared_matches=count)
        for pid, count in rows
        if pid in players_by_id
    ]
    acquaintances.sort(key=lambda a: (-a.shared_matches, a.player.display_name.lower()))
    return acquaintances
