import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import Player
from app.services.auth import SESSION_KEY
from app.services.economy_graphs import (
    build_loadout_win_scatter_from_aggregates,
    build_pistol_match_stats_from_aggregates,
    build_pistol_stats_from_aggregates,
    build_tier_matrix_from_aggregates,
)
from app.services.fight_ev import PAGE_BOOTSTRAP_DRAWS, serialize_fight_ev_views
from app.services.friends import get_current_player_and_friendship
from app.services.map_streaks import compute_map_streaks
from app.services.player_data import RECENT_MATCH_LIMIT
from app.services.player_graphs import build_state_diagrams_from_aggregates, top_kill_order_state_deltas
from app.services.player_view_cache import CachedPlayerViews, store_player_views
from app.services.player_views import PlayerViews, compute_player_views
from app.services.players import get_player_and_cached_views, list_players
from app.services.request_trace import get_current_trace, log_trace, span, start_trace, submit_traced
from app.templates import match_label, templates

router = APIRouter(prefix="/players", tags=["players"])

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _run_in_own_session(fn, *args, _trace_phase: str | None = None, **kwargs):
    """Runs `fn(session, *args, **kwargs)` on a fresh session/connection of its
    own. What this backs today -- map streaks, the write-through after a
    cache miss -- doesn't share any mutable state with the request session,
    so giving each its own connection lets it go out over the wire
    independently instead of paying round-trip latency to the DB serially --
    on a latency-heavy connection (e.g. Neon from a dev machine) that adds up
    fast. (Profile and econ used to run through here too, each as their own
    independent query thread -- Step 2, docs/player_page_render_speed.txt,
    retired both: they're now either read straight off the cache or produced
    by compute_player_views's single shared load, on the request thread.)

    `_trace_phase`, if given, wraps the call in a Step 0 instrumentation span
    tagged with that phase (see app.services.request_trace) -- purely
    diagnostic, changes no behavior."""

    def _call():
        session = SessionLocal()
        try:
            return fn(session, *args, **kwargs)
        finally:
            session.close()

    if _trace_phase is None:
        return _call()
    with span(f"executor:{fn.__name__}", phase=_trace_phase):
        return _call()


def _build_profile_context(
    db: Session, player: Player, match_limit: int | None, scope: str, *,
    cached: CachedPlayerViews | None, include_map_streaks: bool = False,
    extra_work: Callable[[], _T] | None = None,
) -> tuple[dict, _T | None]:
    """Builds the profile-page context. `cached` is the ALREADY-FETCHED cache
    result (Step 1 merges its lookup with the player lookup one level up, in
    the router -- see get_player_and_cached_views) rather than queried here.

    Since Step 2, `cached` (or, on a miss, `compute_player_views`) supplies
    the profile and econ aggregates too -- threads A (get_player_profile) and
    B (player_econ_samples) are GONE from this function entirely, on both the
    hit and the miss path. The ONLY worker this executor ever fans out to now
    is map_streaks (Step 4 territory); Step 2's own invariant is to narrow
    this fan-out, not add to it.

    `extra_work`, if given, runs on the REQUEST thread but INSIDE the
    ThreadPoolExecutor `with` block below -- i.e. concurrently with the
    submitted future(s), the same way the live-compute branch already
    overlaps them on a cache miss. This is how Step 1(b)'s auth/friendship
    lookup gets run "off the critical path" without widening the executor's
    own fan-out or handing the request Session to another thread."""
    trace = get_current_trace()
    if trace is not None:
        trace.tags["scope"] = scope
        trace.tags["cache_hit"] = cached is not None
    computed: PlayerViews | None = None

    with ThreadPoolExecutor(max_workers=1) as executor:
        map_streaks_future = (
            submit_traced(
                executor, _run_in_own_session, compute_map_streaks, player.id,
                _trace_phase="executor:map_streaks",
            )
            if include_map_streaks else None
        )

        if cached is not None:
            round_win_graph = cached.round_win_graph
            kill_order_graph = cached.kill_order_graph
            fight_ev_data = cached.fight_ev_data
            profile = cached.profile
            econ_tier_matrix = cached.econ_tier_matrix
            econ_pistol_stats = cached.econ_pistol_stats
            econ_loadout_scatter = cached.econ_loadout_scatter
            pistol_match_stats = cached.pistol_match_stats
        else:
            # Cache miss: full live compute on this thread, overlapped with
            # the future(s) above. Tagged as an "executor:" phase (even
            # though it runs on the request thread, not a submitted task)
            # because it genuinely overlaps the fan-out above and must be
            # counted in the same max() as the other concurrent chains, not
            # added serially.
            with span("live_compute", phase="executor:main_thread_live_compute"):
                computed = compute_player_views(db, player, match_limit, PAGE_BOOTSTRAP_DRAWS)
                round_win_graph, kill_order_graph = build_state_diagrams_from_aggregates(
                    computed.win_stats, computed.kill_order_weights
                )
                fight_ev_data = serialize_fight_ev_views(computed.fight_ev)
                profile = computed.profile
                econ_tier_matrix = build_tier_matrix_from_aggregates(computed.econ_aggregates["tier_pairs"])
                econ_pistol_stats = build_pistol_stats_from_aggregates(computed.econ_aggregates["pistol"])
                econ_loadout_scatter = build_loadout_win_scatter_from_aggregates(
                    computed.econ_aggregates["loadout_buckets"]
                )
                pistol_match_stats = build_pistol_match_stats_from_aggregates(computed.pistol_match_stats)

        extra_result: _T | None = None
        if extra_work is not None:
            with span("auth_and_friends", phase="executor:auth_friends"):
                extra_result = extra_work()

        map_streaks = map_streaks_future.result() if map_streaks_future is not None else None

    if computed is not None:
        # Opportunistic write-through on its OWN session: committing the
        # request session here would expire `player` and every other ORM
        # object the caller still uses. Failure is non-fatal.
        try:
            _run_in_own_session(
                store_player_views, player.id, {scope: computed},
                _trace_phase="post_executor_writethrough",
            )
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
        "pistol_match_stats": pistol_match_stats,
        "fight_ev_data": fight_ev_data,
        "scope": scope,
    }
    if include_map_streaks:
        context["map_streaks"] = map_streaks
    return context, extra_result


@router.get("")
def player_list(request: Request, db: Session = Depends(get_db)):
    players = list_players(db)
    return templates.TemplateResponse(request, "players/list.html", {"players": players})


@router.get("/{display_name}")
def player_detail(request: Request, display_name: str, db: Session = Depends(get_db)):
    trace = start_trace(f"players.detail:{display_name}", endpoint="detail")
    try:
        with span("player_lookup", phase="pre_executor"):
            player, cached = get_player_and_cached_views(db, display_name, "recent")

        session_player_id = request.session.get(SESSION_KEY)

        def _auth_and_friends():
            return get_current_player_and_friendship(db, session_player_id, player.id)

        # Map streaks aren't scoped by match_limit (their own windowing logic already
        # bounds itself to the current pool era) and aren't part of the recent/career
        # toggle -- computed here (rather than always in the shared context builder)
        # so the career fragment endpoint below doesn't redundantly recompute it.
        # _auth_and_friends is passed in (rather than run after context = ... returns)
        # so it executes ON the request thread but INSIDE _build_profile_context's
        # ThreadPoolExecutor block -- concurrently with the submitted futures,
        # off the critical path, per Step 1(b).
        context, (current_player, is_friend) = _build_profile_context(
            db, player, match_limit=RECENT_MATCH_LIMIT, scope="recent",
            cached=cached, include_map_streaks=True, extra_work=_auth_and_friends,
        )
        context["is_own_profile"] = current_player is not None and current_player.id == player.id
        context["is_friend"] = is_friend

        with span("template_render", phase="render"):
            response = templates.TemplateResponse(request, "players/detail.html", context)
        return response
    finally:
        log_trace(trace)


@router.get("/{display_name}/career")
def player_career_fragment(request: Request, display_name: str, db: Session = Depends(get_db)):
    trace = start_trace(f"players.career:{display_name}", endpoint="career")
    try:
        with span("player_lookup", phase="pre_executor"):
            player, cached = get_player_and_cached_views(db, display_name, "career")
        context, _ = _build_profile_context(db, player, match_limit=None, scope="career", cached=cached)
        with span("template_render", phase="render"):
            response = templates.TemplateResponse(request, "players/_profile_sections.html", context)
        return response
    finally:
        log_trace(trace)
