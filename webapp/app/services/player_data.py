"""Shared match-data load for the player page's heavy builders (state
diagrams, fight-EV). NEUTRAL LEAF: imports app.models and nothing else from
app.services, so player_graphs, fight_ev, player_views and the player_view_cache
module can all import from here without creating an import cycle.
"""

from sqlalchemy.orm import Session, selectinload

from app.models import KillEvent, Match, MatchPlayer, Player, Round

RECENT_MATCH_LIMIT = 30


def load_player_match_data(
    db: Session, player: Player, match_limit: int | None = None
) -> list[MatchPlayer]:
    """Every MatchPlayer row for `player`, most recent match first, with
    the whole Match -> match_players / rounds -> kill_events graph eagerly
    loaded. Shared by the state-diagram and fight-EV builders so a page
    load or a recompute hydrates the ~15k KillEvent objects once instead
    of once per product.

    The ORDER BY is applied even when match_limit is None: callers rely on
    list[:RECENT_MATCH_LIMIT] being exactly the "recent" scope of the same
    career load (see app.services.player_views.compute_player_views_by_scope).

    This clause is a shared contract with app.services.players.get_player_profile
    and app.services.economy_graphs.player_econ_samples -- all three must select
    the SAME "recent" matches, or a cached view describes a different match set
    than the profile/econ charts next to it on the same page. An unknown
    `played_at` counts as the NEWEST match (nullsfirst descending) -- a
    deliberate, pinned convention (see docs/player_page_precompute.txt section
    3.3), not an arbitrary choice. If this ordering is ever changed, change all
    three call sites together and bump PLAYER_VIEW_CACHE_SCHEMA_VERSION.
    """
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(
            selectinload(MatchPlayer.match).selectinload(Match.match_players),
            selectinload(MatchPlayer.match)
            .selectinload(Match.rounds)
            .selectinload(Round.kill_events)
            .defer(KillEvent.source_meta),
        )
        .order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
    )
    if match_limit is not None:
        query = query.limit(match_limit)
    return query.all()
