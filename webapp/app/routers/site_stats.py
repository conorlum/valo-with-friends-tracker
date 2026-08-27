from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.economy_graphs import build_pistol_match_stats_from_aggregates
from app.services.eco_followup import MIN_SAMPLES_FOR_BEST, build_eco_followup_stats_from_aggregates
from app.services.enemy_at_11_response import (
    MIN_SAMPLES_FOR_BEST as ENEMY_AT_11_MIN_SAMPLES_FOR_BEST,
    build_enemy_at_11_response_stats,
)
from app.services.force_buy_stats import build_force_buy_stats
from app.services.halftime_conversion_stats import build_halftime_conversion_stats
from app.services.map_side_stats import build_map_side_stats_from_aggregates
from app.services.round_combo_stats import build_round_combo_stats
from app.services.round_streak_stats import build_round_streak_stats
from app.services.score_reached_stats import build_score_reached_stats
from app.services.site_stats import compute_roster_pistol_match_stats, get_site_stats
from app.templates import templates

router = APIRouter(prefix="/stats", tags=["stats"])


def _round_combo_context(variant: dict) -> dict:
    return {
        "first_half_combo_stats": build_round_combo_stats(variant["first_half"], "first_half"),
        "full_combo_stats": build_round_combo_stats(variant["full"], "full"),
    }


def _friends_context(db: Session, scope: str) -> dict:
    raw = compute_roster_pistol_match_stats(db, scope)
    site_stats = get_site_stats(db)
    return {
        "scope": scope,
        "subject_label": "the friend group",
        "pistol_match_stats": build_pistol_match_stats_from_aggregates(raw),
        "eco_followup_stats": build_eco_followup_stats_from_aggregates(
            site_stats["pistol_win_followup_eco"]["friends"]
        ),
        "eco_followup_min_samples": MIN_SAMPLES_FOR_BEST,
        **_round_combo_context(site_stats["pistol_round_combos"]["friends"]),
        "map_side_stats": build_map_side_stats_from_aggregates(site_stats["map_side_stats"]["friends"]),
        "halftime_conversion_stats": build_halftime_conversion_stats(site_stats["halftime_conversion"]["friends"]),
        "score_reached_stats": build_score_reached_stats(site_stats["score_reached"]["friends"]),
        "round_streak_stats": build_round_streak_stats(site_stats["round_streaks"]["friends"]),
        "force_buy_stats": build_force_buy_stats(site_stats["force_buy_stats"]["friends"]),
        "enemy_at_11_response_stats": build_enemy_at_11_response_stats(site_stats["enemy_at_11_response"]["friends"]),
        "enemy_at_11_response_min_samples": ENEMY_AT_11_MIN_SAMPLES_FOR_BEST,
    }


@router.get("")
def stats_page(request: Request, db: Session = Depends(get_db)):
    context = _friends_context(db, "recent")
    context["group"] = "friends"
    return templates.TemplateResponse(request, "stats/detail.html", context)


@router.get("/friends/career")
def friends_career_fragment(request: Request, db: Session = Depends(get_db)):
    context = _friends_context(db, "career")
    return templates.TemplateResponse(request, "stats/_stats_sections.html", context)


@router.get("/all")
def all_players_page(request: Request, db: Session = Depends(get_db)):
    site_stats = get_site_stats(db)
    context = {
        "group": "all",
        "subject_label": "every player in the database",
        "pistol_match_stats": build_pistol_match_stats_from_aggregates(site_stats["pistol_match_stats"]),
        "eco_followup_stats": build_eco_followup_stats_from_aggregates(
            site_stats["pistol_win_followup_eco"]["all"]
        ),
        "eco_followup_min_samples": MIN_SAMPLES_FOR_BEST,
        **_round_combo_context(site_stats["pistol_round_combos"]["all"]),
        "map_side_stats": build_map_side_stats_from_aggregates(site_stats["map_side_stats"]["all"]),
        "halftime_conversion_stats": build_halftime_conversion_stats(site_stats["halftime_conversion"]["all"]),
        "score_reached_stats": build_score_reached_stats(site_stats["score_reached"]["all"]),
        "round_streak_stats": build_round_streak_stats(site_stats["round_streaks"]["all"]),
        "force_buy_stats": build_force_buy_stats(site_stats["force_buy_stats"]["all"]),
        "enemy_at_11_response_stats": build_enemy_at_11_response_stats(site_stats["enemy_at_11_response"]["all"]),
        "enemy_at_11_response_min_samples": ENEMY_AT_11_MIN_SAMPLES_FOR_BEST,
    }
    return templates.TemplateResponse(request, "stats/detail.html", context)
