from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.economy_graphs import build_pistol_match_stats_from_aggregates
from app.services.eco_followup import MIN_SAMPLES_FOR_BEST, build_eco_followup_stats_from_aggregates
from app.services.round_combo_stats import build_round_combo_stats
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
    }
    return templates.TemplateResponse(request, "stats/detail.html", context)
