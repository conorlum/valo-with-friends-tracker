from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.player_graphs import build_state_diagrams
from app.services.players import get_player_or_404, get_player_profile, list_players
from app.templates import match_label, templates

router = APIRouter(prefix="/players", tags=["players"])


@router.get("")
def player_list(request: Request, db: Session = Depends(get_db)):
    players = list_players(db)
    return templates.TemplateResponse(request, "players/list.html", {"players": players})


@router.get("/{display_name}")
def player_detail(request: Request, display_name: str, db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    profile = get_player_profile(db, player)
    chart_data = {
        "labels": [match_label(m.match) for m in profile.matches],
        "kill_impact": [m.average_kill_impact for m in profile.matches],
        "death_impact": [m.average_death_impact for m in profile.matches],
    }
    highlights_chart_data = {
        "labels": ["Econ", "Clutch / High-Impact", "Post-Plant"],
        "kill": [profile.avg_econ_kill, profile.avg_clutch_kill, profile.avg_post_plant_kill],
        "death": [profile.avg_econ_death, profile.avg_clutch_death, profile.avg_post_plant_death],
    }
    round_win_graph, kill_order_graph = build_state_diagrams(db, player)
    return templates.TemplateResponse(
        request,
        "players/detail.html",
        {
            "profile": profile,
            "chart_data": chart_data,
            "highlights_chart_data": highlights_chart_data,
            "round_win_graph": round_win_graph,
            "kill_order_graph": kill_order_graph,
        },
    )
