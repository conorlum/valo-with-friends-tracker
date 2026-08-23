"""Shared match-data load for the player page's heavy builders (state
diagrams, fight-EV, and -- since docs/player_page_render_speed.txt Step 2 --
the profile summary and econ aggregates too). NEUTRAL LEAF: imports
app.models and nothing else from app.services, so player_graphs, fight_ev,
player_views, players, economy_graphs and the player_view_cache module can
all import from here without creating an import cycle.
"""

from sqlalchemy.orm import Session, selectinload

from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round

RECENT_MATCH_LIMIT = 30


def load_player_match_data(
    db: Session, player: Player, match_limit: int | None = None
) -> list[MatchPlayer]:
    """Every MatchPlayer row for `player`, most recent match first, with
    the whole Match -> match_players (+ each teammate's Player row) / rounds
    -> kill_events, round -> player_stats graph eagerly loaded. Shared by
    the state-diagram, fight-EV, profile-summary and econ-aggregate builders
    (Step 2) so a page load or a recompute hydrates this graph once instead
    of once per product.

    Round.player_stats and MatchPlayer.player (Step 2b) are extra load
    relative to what state-diagrams/fight-EV alone need -- roughly three
    more statements on the write path (this function's own selectin batches,
    plus the caller's bulk ImpactScore query below), in exchange for
    deleting the ten-plus warm-read statements those two extra products used
    to cost on every cache MISS and prewarm run. Worth it, but not free: say
    so in any commit/PR touching this.

    The ORDER BY is applied even when match_limit is None: callers rely on
    list[:RECENT_MATCH_LIMIT] being exactly the "recent" scope of the same
    career load (see app.services.player_views.compute_player_views_by_scope).

    Historically (pre Step 2) this ORDER BY was also a shared contract with
    app.services.players.get_player_profile and
    app.services.economy_graphs.player_econ_samples's OWN independent
    queries, enforced only by a comment across three call sites. Since Step
    2, the player page's live write path no longer calls either of those
    functions -- profile and econ are both built from THIS load (see
    app.services.players.build_player_profile_from_match_data and
    app.services.economy_graphs.econ_samples_from_data) -- so the contract
    is now enforced by construction on that path, not by convention. Both
    functions remain independently correct and still must honor the same
    ordering if ever called directly (as the ORDER BY parity test in
    tests/test_player_view_cache.py still checks) -- this just documents
    that a live page view can no longer drift between the three, because
    there's only one load left to drift from.

    An unknown `played_at` counts as the NEWEST match (nullsfirst
    descending) -- a deliberate, pinned convention (see docs/
    player_page_precompute.txt section 3.3), not an arbitrary choice. If
    this ordering is ever changed, change every caller of this shared load
    (and get_player_profile/player_econ_samples's own queries) together and
    bump PLAYER_VIEW_CACHE_SCHEMA_VERSION.
    """
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(
            selectinload(MatchPlayer.match)
            .selectinload(Match.match_players)
            .selectinload(MatchPlayer.player),
            selectinload(MatchPlayer.match)
            .selectinload(Match.rounds)
            .selectinload(Round.kill_events)
            .defer(KillEvent.source_meta),
            selectinload(MatchPlayer.match).selectinload(Match.rounds).selectinload(Round.player_stats),
        )
        .order_by(Match.played_at.desc().nullsfirst(), Match.id.desc())
    )
    if match_limit is not None:
        query = query.limit(match_limit)
    return query.all()


def load_impact_scores_for_match_players(
    db: Session, match_player_ids: list[int]
) -> dict[int, list[ImpactScore]]:
    """ONE bulk query for every ImpactScore row belonging to any of
    `match_player_ids`, grouped by match_player_id -- the write path's
    replacement for get_player_profile's equivalent inline query (Step 2b
    item 4). Round outcomes don't need a query of their own here the way
    get_player_profile's does: they come from the rounds already hydrated by
    load_player_match_data."""
    scores_by_match_player: dict[int, list[ImpactScore]] = {}
    if not match_player_ids:
        return scores_by_match_player
    for score in db.query(ImpactScore).filter(ImpactScore.match_player_id.in_(match_player_ids)).all():
        scores_by_match_player.setdefault(score.match_player_id, []).append(score)
    return scores_by_match_player
