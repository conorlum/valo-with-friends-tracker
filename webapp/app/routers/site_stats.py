from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
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
from app.services.site_stats import get_site_stats, get_viewer_site_stats
from app.templates import templates

router = APIRouter(prefix="/stats", tags=["stats"])

FRIENDS_LOGIN_NOTE = "Friends shows you and the players on your Friends page, so it needs a login."


def _sections_context(stats: dict, pistol_match_stats: dict[str, int], subject_label: str) -> dict:
    """Template context for the stat cards, from one population's variants
    (every key of the site-wide or viewer blob except pistol_match_stats,
    whose right scope the caller picks)."""
    combos = stats["pistol_round_combos"]
    return {
        "subject_label": subject_label,
        "pistol_match_stats": build_pistol_match_stats_from_aggregates(pistol_match_stats),
        "eco_followup_stats": build_eco_followup_stats_from_aggregates(stats["pistol_win_followup_eco"]),
        "eco_followup_min_samples": MIN_SAMPLES_FOR_BEST,
        "first_half_combo_stats": build_round_combo_stats(combos["first_half"], "first_half"),
        "full_combo_stats": build_round_combo_stats(combos["full"], "full"),
        "map_side_stats": build_map_side_stats_from_aggregates(stats["map_side_stats"]),
        "halftime_conversion_stats": build_halftime_conversion_stats(stats["halftime_conversion"]),
        "score_reached_stats": build_score_reached_stats(stats["score_reached"]),
        "round_streak_stats": build_round_streak_stats(stats["round_streaks"]),
        "force_buy_stats": build_force_buy_stats(stats["force_buy_stats"]),
        "enemy_at_11_response_stats": build_enemy_at_11_response_stats(stats["enemy_at_11_response"]),
        "enemy_at_11_response_min_samples": ENEMY_AT_11_MIN_SAMPLES_FOR_BEST,
    }


def _friends_context(db: Session, viewer_player_id: int, scope: str) -> dict:
    stats = get_viewer_site_stats(db, viewer_player_id)
    context = _sections_context(stats, stats["pistol_match_stats"][scope], "you and your friends")
    context["scope"] = scope
    return context


@router.get("")
def stats_page(request: Request, db: Session = Depends(get_db)):
    viewer = get_current_player(request, db)
    if viewer is None:
        return RedirectResponse(url="/stats/all", status_code=303)
    context = _friends_context(db, viewer.id, "recent")
    context["group"] = "friends"
    return templates.TemplateResponse(request, "stats/detail.html", context)


@router.get("/friends/career")
def friends_career_fragment(request: Request, db: Session = Depends(get_db)):
    viewer = get_current_player(request, db)
    if viewer is None:
        # An htmx fragment: a redirect would swap the whole All Players page into it.
        return HTMLResponse(f'<p class="page-meta">{FRIENDS_LOGIN_NOTE}</p>')
    context = _friends_context(db, viewer.id, "career")
    return templates.TemplateResponse(request, "stats/_stats_sections.html", context)


@router.get("/map-control")
def map_control_fragment(request: Request, group: str = "all", db: Session = Depends(get_db)):
    """The map control card's table (docs/replay-map-control-plan.md, "Heatmaps"; R3.4), loaded by htmx.
    Friends: replays with the viewer or their friendships in them (never tracked_players.json). 404 on
    the demo site, where there are no replays."""
    from app.config import settings
    from app.services import replay_control_views as control_views
    from app.services.site_stats import viewer_group_player_ids

    if settings.demo_mode:
        raise HTTPException(status_code=404)
    viewer = get_current_player(request, db) if group == "friends" else None
    if group == "friends" and viewer is not None:
        ids = control_views.friends_replay_ids(db, viewer_group_player_ids(db, viewer.id))
        rows, label = control_views.map_aggregate(db, ids), "replays with you or your friends in them"
    else:
        rows, label = control_views.map_aggregate(db), "every uploaded replay"
    return templates.TemplateResponse(request, "stats/_map_control_table.html", {
        "rows": rows, "label": label, "min_rounds": control_views.MIN_ROUNDS_PER_MAP})


def _player_names(db: Session, ids) -> dict[int, str]:
    from app.models import Player

    ids = list(ids)
    if not ids:
        return {}
    return {pid: name for pid, name in db.query(Player.id, Player.display_name).filter(Player.id.in_(ids))}


@router.get("/ults")
def ults_fragment(request: Request, group: str = "all", db: Session = Depends(get_db)):
    """The Ults card's tables (the stored Critical Util build's `ults` block), loaded by htmx. Never builds:
    `page_values` returns what is stored and whether it is current. All Players is pooled by agent and names no
    one; Friends adds the viewer's circle's rows (app/services/circle.py), and needs a login. 404 on the demo."""
    from app.config import settings
    from app.critutil import ult_values
    from app.services import circle, replay_util

    if settings.demo_mode:
        raise HTTPException(status_code=404)
    stored, current = replay_util.page_values(db)
    ults = (stored or {}).get("ults") or None
    viewer = get_current_player(request, db) if group == "friends" else None
    friend_rows: list[dict] = []
    if viewer is not None and ults and ults.get("charges"):
        ids = circle.circle_ids(db, viewer.id) | {viewer.id}
        rows = ult_values.friend_rows(ults["charges"], ids)
        names = _player_names(db, {r["player_id"] for r in rows})
        friend_rows = [{**r, "name": names[r["player_id"]]} for r in rows if r["player_id"] in names]
    label = "the viewer's circle" if viewer is not None else "every uploaded replay"
    return templates.TemplateResponse(request, "stats/_ults_table.html", {
        "ults": ults, "current": current, "friend_rows": friend_rows, "label": label,
        "min_uses": ult_values.MIN_USES})


@router.get("/all")
def all_players_page(request: Request, db: Session = Depends(get_db)):
    stats = get_site_stats(db)
    context = _sections_context(stats, stats["pistol_match_stats"], "every player in the database")
    context["group"] = "all"
    context["friends_login_note"] = FRIENDS_LOGIN_NOTE
    return templates.TemplateResponse(request, "stats/detail.html", context)
