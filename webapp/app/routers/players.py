from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Player
from app.services.auth import get_current_player
from app.services.economy_graphs import build_pistol_stats, build_tier_matrix, player_econ_samples
from app.services.friends import list_friend_ids
from app.services.map_streaks import compute_map_streaks
from app.services.player_graphs import build_state_diagrams, top_kill_order_state_deltas
from app.services.players import get_player_or_404, get_player_profile, list_players
from app.templates import match_label, templates

router = APIRouter(prefix="/players", tags=["players"])

RECENT_MATCH_LIMIT = 30


def _build_profile_context(db: Session, player: Player, match_limit: int | None, scope: str) -> dict:
    profile = get_player_profile(db, player, match_limit=match_limit)
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
    round_win_graph, kill_order_graph = build_state_diagrams(db, player, match_limit=match_limit)
    top_kill_differentials, top_death_differentials = top_kill_order_state_deltas(kill_order_graph)
    econ_samples = player_econ_samples(db, player, match_limit=match_limit)
    econ_tier_matrix = build_tier_matrix(econ_samples)
    econ_pistol_stats = build_pistol_stats(econ_samples)
    return {
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
        "scope": scope,
    }


@router.get("")
def player_list(request: Request, db: Session = Depends(get_db)):
    players = list_players(db)
    return templates.TemplateResponse(request, "players/list.html", {"players": players})


@router.get("/{display_name}")
def player_detail(request: Request, display_name: str, db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    context = _build_profile_context(db, player, match_limit=RECENT_MATCH_LIMIT, scope="recent")
    # Map streaks aren't scoped by match_limit (their own windowing logic already
    # bounds itself to the current pool era) and aren't part of the recent/career
    # toggle -- computed once here rather than in the shared context builder so the
    # career fragment endpoint below doesn't redundantly recompute it.
    context["map_streaks"] = compute_map_streaks(db, player.id)

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
