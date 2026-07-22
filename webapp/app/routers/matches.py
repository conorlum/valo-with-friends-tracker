from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.matches import (
    get_match_or_404,
    get_match_summary,
    get_round_detail,
    list_matches,
    list_matches_for_player,
)
from app.services.player_graphs import build_match_round_win_diagrams
from app.templates import templates

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("")
def match_list(
    request: Request,
    show_all: bool = Query(False, alias="all"),
    db: Session = Depends(get_db),
):
    current_player = get_current_player(request, db)
    showing_own_matches = current_player is not None and not show_all
    matches = (
        list_matches_for_player(db, current_player.id) if showing_own_matches else list_matches(db)
    )
    return templates.TemplateResponse(
        request,
        "matches/list.html",
        {"matches": matches, "showing_own_matches": showing_own_matches},
    )


@router.get("/{external_id}")
def match_detail(request: Request, external_id: str, db: Session = Depends(get_db)):
    match = get_match_or_404(db, external_id)
    summary = get_match_summary(db, match)
    chart_data = {
        "labels": summary.round_numbers,
        "series": [
            {
                "label": f"{p.display_name} ({p.agent})",
                "team": p.team,
                "data": [p.impact_by_round.get(r) for r in summary.round_numbers],
            }
            for p in sorted(summary.players, key=lambda p: p.team)
        ],
    }
    highlights_chart_data = {
        "labels": ["Econ", "Clutch / High-Impact", "Post-Plant"],
        "default_player_id": str(summary.players[0].match_player_id) if summary.players else None,
        "players": {
            str(p.match_player_id): {
                "kill": [p.econ_kill, p.clutch_kill, p.post_plant_kill],
                "death": [p.econ_death, p.clutch_death, p.post_plant_death],
                "traded_teammate": p.traded_teammate,
                "traded_by_teammate": p.traded_by_teammate,
                "traded_teammate_breakdown": p.traded_teammate_names,
                "traded_by_teammate_breakdown": p.traded_by_teammate_names,
            }
            for p in summary.players
        },
    }
    team_chart_data = {
        "labels": summary.round_numbers,
        "series": [
            {
                "label": "Team 1" if ts.team == "team-1" else "Team 2",
                "team": ts.team,
                "data": [ts.impact_by_round.get(r, 0.0) for r in summary.round_numbers],
            }
            for ts in summary.team_summaries
        ],
    }
    team1_win_graph, team2_win_graph = build_match_round_win_diagrams(match)
    return templates.TemplateResponse(
        request,
        "matches/detail.html",
        {
            "match": match,
            "summary": summary,
            "chart_data": chart_data,
            "highlights_chart_data": highlights_chart_data,
            "team_chart_data": team_chart_data,
            "team1_win_graph": team1_win_graph,
            "team2_win_graph": team2_win_graph,
        },
    )


@router.get("/{external_id}/rounds/{round_number}")
def round_detail(request: Request, external_id: str, round_number: int, db: Session = Depends(get_db)):
    match = get_match_or_404(db, external_id)
    detail = get_round_detail(db, match, round_number)
    return templates.TemplateResponse(
        request, "matches/_round_detail.html", {"match": match, "detail": detail}
    )
