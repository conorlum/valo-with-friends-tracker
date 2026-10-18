"""Account groups: one person's several Riot accounts, shown on the player
page combined ("All accounts") or one at a time.

A group is a main account and its sub accounts. An account is in at most one
group, so once linked it can't be added by anyone else. Zero-auth, like
friendships: whoever is logged in as the main adds and removes subs, labels
them and can hand the main role to a sub; a sub can only leave. The first
account to add a sub becomes the main. A group of one isn't kept -- removing
the last sub deletes it.

The combined page is cached per group and scope in account_group_view_cache.
Rather than hooking every ingest path, each row stores a fingerprint of what
it was built from (group_fingerprint) and a row whose fingerprint no longer
matches is a miss. invalidate_player_cache and invalidate_all_player_caches
also clear group rows, for changes the fingerprint can't see.
"""

import hashlib
import logging

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.models import AccountGroup, AccountGroupMember, AccountGroupViewCache, Friendship, ImpactScore, MatchPlayer, Player
from app.services.player_view_cache import CachedPlayerViews, cache_version, decode_player_views_blob, encode_player_views
from app.services.player_views import PlayerViews, compute_player_views_by_scope

logger = logging.getLogger(__name__)

LABEL_MAX_LENGTH = 32


def list_group_members(db: Session, player_id: int) -> list[AccountGroupMember]:
    """Every account in `player_id`'s group (including it): the main first,
    then the subs, oldest link first, each with its Player and group loaded.
    Empty if it isn't in a group. One round trip, since every player page asks."""
    group_id = select(AccountGroupMember.group_id).where(AccountGroupMember.player_id == player_id)
    return (
        db.query(AccountGroupMember)
        .join(AccountGroup, AccountGroup.id == AccountGroupMember.group_id)
        .options(joinedload(AccountGroupMember.player), contains_eager(AccountGroupMember.group))
        .filter(AccountGroupMember.group_id == group_id.scalar_subquery())
        .order_by(
            case((AccountGroupMember.player_id == AccountGroup.main_player_id, 0), else_=1),
            AccountGroupMember.created_at,
            AccountGroupMember.player_id,
        )
        .all()
    )


def group_id_for(db: Session, player_id: int) -> int | None:
    return db.query(AccountGroupMember.group_id).filter_by(player_id=player_id).scalar()


def linked_display_names(db: Session) -> set[str]:
    """Every account in some group -- none of them can be added to another."""
    return {
        name for (name,) in db.query(Player.display_name).join(AccountGroupMember, AccountGroupMember.player_id == Player.id)
    }


def _group_of(db: Session, player_id: int) -> AccountGroup | None:
    member = db.get(AccountGroupMember, player_id)
    return member.group if member is not None else None


def _locked_group(db: Session, player_id: int) -> AccountGroup | None:
    """`player_id`'s group with its row locked until the transaction ends (a
    no-op on sqlite), so two writes to one group queue instead of interleaving.
    Membership is re-checked after the lock: a write that waited may find the
    account gone from the group, or the group itself deleted. None then."""
    group_id = group_id_for(db, player_id)
    if group_id is None:
        return None
    group = (
        db.query(AccountGroup).filter_by(id=group_id).with_for_update().populate_existing().one_or_none()
    )
    if group is None or group_id_for(db, player_id) != group_id:
        return None
    return group


def _clear_group_cache(db: Session, group_id: int) -> None:
    db.query(AccountGroupViewCache).filter_by(group_id=group_id).delete(synchronize_session=False)


def link_account(db: Session, owner_player_id: int, other_player_id: int) -> str | None:
    """Adds `other_player_id` as a sub of the owner, starting a group with the
    owner as its main if the owner has none. Returns an error message instead
    when the owner is a sub (only the main adds accounts) or the other account
    is already linked to anyone. Does NOT commit."""
    if owner_player_id == other_player_id:
        return None
    group = _locked_group(db, owner_player_id)
    if group is not None and group.main_player_id != owner_player_id:
        main = db.get(Player, group.main_player_id)
        return f"Only the main account adds accounts. Log in as {main.display_name} to add one."
    other_group_id = group_id_for(db, other_player_id)
    if other_group_id is not None:
        if group is not None and other_group_id == group.id:
            return None
        other = db.get(Player, other_player_id)
        return f"{other.display_name} is already linked to other accounts. It has to leave that group first."

    if group is None:
        group = AccountGroup(main_player_id=owner_player_id)
        db.add(group)
        db.flush()
        # Ascending player_id, so two opposite links at once collide on the
        # same first row: the loser gets a unique violation, not a deadlock.
        for pid in sorted((owner_player_id, other_player_id)):
            db.add(AccountGroupMember(player_id=pid, group_id=group.id))
        db.flush()
    else:
        db.add(AccountGroupMember(player_id=other_player_id, group_id=group.id))
    _clear_group_cache(db, group.id)
    return None


def unlink_account(db: Session, actor_player_id: int, other_player_id: int) -> bool:
    """The main removes a sub, or a sub (`other_player_id == actor_player_id`)
    leaves. The main itself can't leave while it has subs: it hands the main
    role over first. Deletes the group if only the main would be left. False
    if the actor may not remove that account. Does NOT commit."""
    group = _locked_group(db, actor_player_id)
    if group is None or group_id_for(db, other_player_id) != group.id or other_player_id == group.main_player_id:
        return False
    if actor_player_id not in (group.main_player_id, other_player_id):
        return False
    group_id = group.id
    db.query(AccountGroupMember).filter_by(player_id=other_player_id).delete(synchronize_session=False)
    _clear_group_cache(db, group_id)
    remaining = db.query(func.count(AccountGroupMember.player_id)).filter_by(group_id=group_id).scalar()
    if remaining <= 1:
        db.query(AccountGroupMember).filter_by(group_id=group_id).delete(synchronize_session=False)
        db.query(AccountGroup).filter_by(id=group_id).delete(synchronize_session=False)
    db.expire_all()
    return True


def make_main_account(db: Session, actor_player_id: int, other_player_id: int) -> bool:
    """The main hands the main role to one of its subs. False if the actor
    isn't the main or `other_player_id` isn't in its group. Does NOT commit."""
    group = _locked_group(db, actor_player_id)
    if group is None or group.main_player_id != actor_player_id or group_id_for(db, other_player_id) != group.id:
        return False
    group.main_player_id = other_player_id
    return True


def set_account_label(db: Session, actor_player_id: int, other_player_id: int, label: str) -> bool:
    """The main sets (or with a blank `label`, clears) what an account in its
    group is called. False if the actor isn't the main or the account isn't in
    its group. Does NOT commit."""
    group = _locked_group(db, actor_player_id)
    member = db.query(AccountGroupMember).filter_by(player_id=other_player_id).populate_existing().one_or_none()
    if group is None or group.main_player_id != actor_player_id or member is None or member.group_id != group.id:
        return False
    member.label = label.strip()[:LABEL_MAX_LENGTH] or None
    return True


def group_fingerprint(db: Session, player_ids: frozenset[int]) -> str:
    """What a group's combined page is built from: its accounts, their
    matches, their Impact rows and their friendships (the fight-EV teammate
    pool). A new match, a newly scored one or a friend added or removed
    changes it. Impact rows rescored in place don't, which is why
    invalidate_player_cache clears group rows too."""
    ids = sorted(player_ids)
    match_count, match_max = (
        db.query(func.count(MatchPlayer.id), func.max(MatchPlayer.id))
        .filter(MatchPlayer.player_id.in_(ids))
        .one()
    )
    score_stats = (
        db.query(func.count(), func.max(ImpactScore.round_id), func.sum(ImpactScore.impact))
        .select_from(ImpactScore)
        .join(MatchPlayer, MatchPlayer.id == ImpactScore.match_player_id)
        .filter(MatchPlayer.player_id.in_(ids))
        .one()
    )
    friends = sorted(
        {fid for (fid,) in db.query(Friendship.friend_player_id).filter(Friendship.owner_player_id.in_(ids)).all()}
    )
    parts = [ids, [match_count, match_max], list(score_stats), friends]
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def get_cached_group_views(
    db: Session, group_id: int, scope: str, fingerprint: str, player: Player
) -> CachedPlayerViews | None:
    """The group's cached combined page for `scope`, profiled under `player`
    (the page being viewed). None on a miss, a stale fingerprint or version,
    or a bad blob -- never raises."""
    try:
        row = db.get(AccountGroupViewCache, (group_id, scope))
    except Exception:
        logger.exception("account_group_view_cache: read failed for group %s", group_id)
        return None
    if row is None or row.fingerprint != fingerprint:
        return None
    return decode_player_views_blob(row.data, row.version, player)


def store_group_views(db: Session, group_id: int, scope: str, fingerprint: str, views: PlayerViews) -> None:
    """Upserts one scope's row and commits."""
    _put_group_views(db, group_id, scope, fingerprint, views)
    db.commit()


def _put_group_views(db: Session, group_id: int, scope: str, fingerprint: str, views: PlayerViews) -> None:
    data = encode_player_views(views)
    row = db.get(AccountGroupViewCache, (group_id, scope))
    if row is None:
        db.add(AccountGroupViewCache(
            group_id=group_id, scope=scope, fingerprint=fingerprint, data=data, version=cache_version(),
        ))
    else:
        row.fingerprint = fingerprint
        row.data = data
        row.version = cache_version()
        row.updated_at = func.now()


def prewarm_group_caches(db: Session, player_ids: set[int]) -> None:
    """Recomputes both scopes of every group any of `player_ids` is in, so the
    first visit after an ingest doesn't pay for a combined career replay
    (seconds per account). Best-effort per group, like prewarm_player_cache."""
    if not player_ids:
        return
    try:
        group_ids = {
            gid
            for (gid,) in db.query(AccountGroupMember.group_id).filter(AccountGroupMember.player_id.in_(player_ids))
        }
    except Exception:
        db.rollback()
        logger.exception("Group cache pre-warm: couldn't look up groups; next page visits will recompute")
        return
    for group_id in sorted(group_ids):
        try:
            members = (
                db.query(AccountGroupMember)
                .filter_by(group_id=group_id)
                .order_by(AccountGroupMember.created_at, AccountGroupMember.player_id)
                .all()
            )
            member_ids = frozenset(m.player_id for m in members)
            fingerprint = group_fingerprint(db, member_ids)
            anchor = db.get(Player, members[0].player_id)
            for scope, views in compute_player_views_by_scope(db, anchor, player_ids=member_ids).items():
                _put_group_views(db, group_id, scope, fingerprint, views)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Group cache pre-warm failed for group %s; next page visit will recompute", group_id)
