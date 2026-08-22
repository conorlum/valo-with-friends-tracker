import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Player
from app.services.auth import get_current_player
from app.services.economy_graphs import (
    build_loadout_win_scatter,
    build_pistol_stats,
    build_tier_matrix,
    player_econ_samples,
)
from app.services.fight_ev import PAGE_BOOTSTRAP_DRAWS, serialize_fight_ev_views
from app.services.friends import list_friend_ids
from app.services.map_streaks import compute_map_streaks
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.player_graphs import build_state_diagrams_from_aggregates, top_kill_order_state_deltas
from app.services.player_view_cache import get_cached_views, store_player_views
from app.services.player_views import PlayerViews, compute_player_views
from app.services.players import get_player_or_404, get_player_profile, list_players
from app.templates import match_label, templates

router = APIRouter(prefix="/players", tags=["players"])

logger = logging.getLogger(__name__)


def _run_in_own_session(fn, *args, **kwargs):
    """Runs `fn(session, *args, **kwargs)` on a fresh session/connection of its
    own. The independent queries this backs (player profile, state diagrams,
    econ samples, fight-EV, map streaks) don't share any mutable state, so
    giving each its own connection lets them go out over the wire concurrently
    instead of paying round-trip latency to the DB one at a time -- on a
    latency-heavy connection (e.g. Neon from a dev machine) that serial chain
    of ~20 queries dominates page load far more than any of the CPU work."""
    session = SessionLocal()
    try:
        return fn(session, *args, **kwargs)
    finally:
        session.close()


def _build_profile_context(
    db: Session, player: Player, match_limit: int | None, scope: str, *, include_map_streaks: bool = False
) -> dict:
    cached = get_cached_views(db, player.id, scope)
    computed: PlayerViews | None = None

    with ThreadPoolExecutor(max_workers=3) as executor:
        profile_future = executor.submit(
            _run_in_own_session, get_player_profile, player, match_limit=match_limit
        )
        econ_future = executor.submit(
            _run_in_own_session, player_econ_samples, player, match_limit=match_limit
        )
        map_streaks_future = (
            executor.submit(_run_in_own_session, compute_map_streaks, player.id) if include_map_streaks else None
        )

        if cached is not None:
            round_win_graph = cached.round_win_graph
            kill_order_graph = cached.kill_order_graph
            fight_ev_data = cached.fight_ev_data
        else:
            # Cache miss: full live compute on this thread, overlapped with
            # the futures above.
            computed = compute_player_views(db, player, match_limit, PAGE_BOOTSTRAP_DRAWS)
            round_win_graph, kill_order_graph = build_state_diagrams_from_aggregates(
                computed.win_stats, computed.kill_order_weights
            )
            fight_ev_data = serialize_fight_ev_views(computed.fight_ev)

        profile = profile_future.result()
        econ_samples = econ_future.result()
        map_streaks = map_streaks_future.result() if map_streaks_future is not None else None

    if computed is not None:
        # Opportunistic write-through on its OWN session: committing the
        # request session here would expire `player` and every other ORM
        # object the caller still uses. Failure is non-fatal.
        try:
            _run_in_own_session(store_player_views, player.id, {scope: computed})
        except Exception:
            logger.exception("Cache write-through failed for player %s (%s)", player.id, scope)

    recent_first_matches = list(reversed(profile.matches))
    chart_data = {
        "labels": [match_label(m.match) for m in recent_first_matches],
        "kill_impact": [m.average_kill_impact for m in recent_first_matches],
        "death_impact": [m.average_death_impact for m in recent_first_matches],
        "avg_impact": profile.overall_average_impact,
    }
    highlights_chart_data = {
        "labels": ["Econ", "Clutch / High-Impact", "Post-Plant"],
        "kill": [profile.avg_econ_kill, profile.avg_clutch_kill, profile.avg_post_plant_kill],
        "death": [profile.avg_econ_death, profile.avg_clutch_death, profile.avg_post_plant_death],
    }
    map_chart_data = {
        "labels": [s.key for s in profile.map_stats],
        "kill_impact": [s.average_kill_impact for s in profile.map_stats],
        "death_impact": [s.average_death_impact for s in profile.map_stats],
    }
    top_kill_differentials, top_death_differentials = top_kill_order_state_deltas(kill_order_graph)
    econ_tier_matrix = build_tier_matrix(econ_samples)
    econ_pistol_stats = build_pistol_stats(econ_samples)
    econ_loadout_scatter = build_loadout_win_scatter(econ_samples)

    context = {
        "profile": profile,
        "chart_data": chart_data,
        "highlights_chart_data": highlights_chart_data,
        "map_chart_data": map_chart_data,
        "round_win_graph": round_win_graph,
        "kill_order_graph": kill_order_graph,
        "top_kill_differentials": top_kill_differentials,
        "top_death_differentials": top_death_differentials,
        "econ_tier_matrix": econ_tier_matrix,
        "econ_pistol_stats": econ_pistol_stats,
        "econ_loadout_scatter": econ_loadout_scatter,
        "fight_ev_data": fight_ev_data,
        "scope": scope,
    }
    if include_map_streaks:
        context["map_streaks"] = map_streaks
    return context


@router.get("")
def player_list(request: Request, db: Session = Depends(get_db)):
    players = list_players(db)
    return templates.TemplateResponse(request, "players/list.html", {"players": players})


@router.get("/{display_name}")
def player_detail(request: Request, display_name: str, db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    # Map streaks aren't scoped by match_limit (their own windowing logic already
    # bounds itself to the current pool era) and aren't part of the recent/career
    # toggle -- computed here (rather than always in the shared context builder)
    # so the career fragment endpoint below doesn't redundantly recompute it.
    context = _build_profile_context(db, player, match_limit=RECENT_MATCH_LIMIT, scope="recent", include_map_streaks=True)

    current_player = get_current_player(request, db)
    context["is_own_profile"] = current_player is not None and current_player.id == player.id
    context["is_friend"] = (
        current_player is not None
        and current_player.id != player.id
        and player.id in list_friend_ids(db, current_player.id)
    )
    return templates.TemplateResponse(request, "players/detail.html", context)


@router.get("/{display_name}/career")
def player_career_fragment(request: Request, display_name: str, db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    context = _build_profile_context(db, player, match_limit=None, scope="career")
    return templates.TemplateResponse(request, "players/_profile_sections.html", context)
