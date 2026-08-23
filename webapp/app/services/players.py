from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.models import ImpactScore, Match, MatchPlayer, Player, PlayerViewCache, Round, RoundPlayerStat
from app.services.player_profile_types import PlayerProfile, build_profile_from_precomputed
from app.services.player_view_cache import CachedPlayerViews, decode_cache_row


@dataclass
class PlayerListEntry:
    display_name: str
    matches_played: int


def list_players(db: Session) -> list[PlayerListEntry]:
    rows = (
        db.query(Player.display_name, func.count(MatchPlayer.id))
        .join(MatchPlayer, MatchPlayer.player_id == Player.id)
        .group_by(Player.display_name)
        .order_by(func.count(MatchPlayer.id).desc())
        .all()
    )
    return [PlayerListEntry(display_name=name, matches_played=cnt) for name, cnt in rows]


def list_player_display_names(db: Session) -> list[str]:
    """Cheap alternative to list_players() for UI pickers that only need names
    (the login page, the friends 'add' dropdown) -- skips the MatchPlayer/
    ImpactScore join those don't render, just to build a matches/impact stat
    those pages never use.
    """
    names = [name for (name,) in db.query(Player.display_name).all()]
    names.sort(key=str.lower)
    return names


def get_player_or_404(db: Session, display_name: str) -> Player:
    player = db.query(Player).filter_by(display_name=display_name).one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"No player '{display_name}'")
    return player


def get_player_and_cached_views(
    db: Session, display_name: str, scope: str
) -> tuple[Player, CachedPlayerViews | None]:
    """Merges the player-page router's Q1 (player lookup) and Q2
    (player_view_cache lookup) into one round trip -- an OUTER join so a
    cache miss (or no row at all) still returns the player row. Raises the
    same 404 as get_player_or_404. See docs/player_page_render_speed.txt
    Step 1(a)."""
    row = (
        db.query(Player, PlayerViewCache)
        .outerjoin(
            PlayerViewCache,
            and_(PlayerViewCache.player_id == Player.id, PlayerViewCache.scope == scope),
        )
        .filter(Player.display_name == display_name)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No player '{display_name}'")
    player, cache_row = row
    return player, decode_cache_row(cache_row, player)


def find_player_by_search_query(db: Session, query: str) -> Player | None:
    """Looks up a player from a "Name#Tag" search box.

    The scraped demo data has no real Riot ID tag, so any trailing "#..."
    is stripped before matching -- typing it out of habit still works.
    """
    name = query.split("#", 1)[0].strip()
    if not name:
        return None
    return db.query(Player).filter(Player.display_name.ilike(name)).one_or_none()


def get_player_profile(db: Session, player: Player, match_limit: int | None = None) -> PlayerProfile:
    """Live per-request path: issues its own 4 queries (scores, KDA,
    teammates, round outcomes) beyond the initial match_players load, then
    delegates the actual breakdown-building to build_profile_from_precomputed
    (app.services.player_profile_types) -- shared with the player_view_cache
    write path (Step 2), which instead derives these same four inputs from
    load_player_match_data's already-hydrated relationships rather than
    querying for them again (see app.services.player_profile_types.
    build_player_profile_from_match_data, called from app.services.
    player_views). No longer called on the player page's own request path
    post-Step-2 (profile now comes from the cache, or from that from-data
    path on a miss) -- kept as a standalone, independently-correct function
    since nothing else needed it removed, and it's what tests/
    test_player_view_cache.py's ORDER BY contract test exercises directly."""
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(joinedload(MatchPlayer.match))
    )
    if match_limit is not None:
        match_players = list(
            reversed(
                query.order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
                .limit(match_limit)
                .all()
            )
        )
    else:
        match_players = query.order_by(Match.played_at.nullslast(), Match.id).all()

    match_player_ids = [mp.id for mp in match_players]
    match_ids = [mp.match_id for mp in match_players]

    scores_by_match_player: dict[int, list[ImpactScore]] = {}
    if match_player_ids:
        for score in db.query(ImpactScore).filter(ImpactScore.match_player_id.in_(match_player_ids)).all():
            scores_by_match_player.setdefault(score.match_player_id, []).append(score)

    kda_by_match_player: dict[int, tuple[int, int, int]] = {}
    if match_player_ids:
        for mp_id, k, d, a in (
            db.query(
                RoundPlayerStat.match_player_id,
                func.sum(RoundPlayerStat.kills),
                func.sum(RoundPlayerStat.deaths),
                func.sum(RoundPlayerStat.assists),
            )
            .filter(RoundPlayerStat.match_player_id.in_(match_player_ids))
            .group_by(RoundPlayerStat.match_player_id)
            .all()
        ):
            kda_by_match_player[mp_id] = (k or 0, d or 0, a or 0)

    teammates_by_match: dict[int, list[MatchPlayer]] = {}
    if match_ids:
        all_teammates = (
            db.query(MatchPlayer)
            .filter(MatchPlayer.match_id.in_(match_ids))
            .options(joinedload(MatchPlayer.player))
            .all()
        )
        for mp in all_teammates:
            teammates_by_match.setdefault(mp.match_id, []).append(mp)

    round_ids_needed = {
        score.round_id for scores in scores_by_match_player.values() for score in scores
    }
    round_outcome_by_id: dict[int, str | None] = {}
    if round_ids_needed:
        round_outcome_by_id = dict(
            db.query(Round.id, Round.outcome).filter(Round.id.in_(round_ids_needed)).all()
        )

    return build_profile_from_precomputed(
        player, match_players, scores_by_match_player, kda_by_match_player,
        teammates_by_match, round_outcome_by_id,
    )
