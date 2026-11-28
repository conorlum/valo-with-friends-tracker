"""Replay search (R1, R2, R6, R7, D4, D5): the library behind the CLI, the route and the coach.

`search(db, args)` parses the filters with `app.search.filters.parse`, resolves Riot IDs into a
`match.Context`, reads the stored index rows (SQL is only a prefilter; `match.select` decides every hit) and
returns one page of hits. Only rows whose replay's state row is at `index.INDEX_REVISION` are read.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.models import MatchPlayer, Player
from app.models.replay import Replay, ReplayPlayer
from app.models.replay_search import ReplayMomentIndex, ReplayMomentIndexState
from app.search import index, match
from app.search.filters import FilterError, parse
from app.search.match import ROW_KEYS
from app.services import circle

PAGE_SIZE = 50
LEAD_SECONDS = 3.0  # a link opens this long before the moment


def _player_id(db, key: str, riot_id: str) -> int:
    pid = db.execute(select(Player.id).where(func.lower(Player.display_name) == riot_id.lower())
                     .order_by(Player.id).limit(1)).scalar_one_or_none()
    if pid is None:
        raise FilterError(f"{key}: unknown Riot ID {riot_id!r}")
    return pid


def _memberships(db, player_ids) -> dict[int, tuple[int, str]]:
    """{replay id: (slot, team)} over the linked replays any of `player_ids` is in."""
    out: dict[int, tuple[int, str]] = {}
    query = (select(ReplayPlayer.replay_id, ReplayPlayer.slot, MatchPlayer.team)
             .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
             .join(Replay, Replay.id == ReplayPlayer.replay_id)
             .where(MatchPlayer.player_id.in_(list(player_ids)), Replay.link_status == "linked")
             .order_by(ReplayPlayer.replay_id, ReplayPlayer.slot))
    for rid, slot, team in db.execute(query):
        out.setdefault(rid, (slot, getattr(team, "value", team)))
    return out


def _context(db, f) -> tuple[match.Context, int | None]:
    """The matcher's context for `f` and the `player` filter's player id (None when not given)."""
    ours: dict[int, str] = {}
    slots: dict[int, int] = {}
    allowed: set[int] | None = None
    actor = None
    if f.as_ is not None:
        ours = {rid: team for rid, (_, team) in _memberships(db, [_player_id(db, "as", f.as_)]).items()}
    if f.player is not None:
        actor = _player_id(db, "player", f.player)
        slots = {rid: slot for rid, (slot, _) in _memberships(db, [actor]).items()}
    if f.with_ is not None:
        allowed = set(_memberships(db, [_player_id(db, "with", f.with_)]))
    if f.circle is not None:
        members = circle.circle_ids(db, _player_id(db, "circle", f.circle))
        reach = set(_memberships(db, members))
        allowed = reach if allowed is None else allowed & reach
    return match.Context(ours, slots, None if allowed is None else frozenset(allowed)), actor


def _prefiltered_rows(db, f, ctx: match.Context, actor) -> list[dict]:
    wanted: set[int] | None = None
    for ids in (ctx.allowed, set(ctx.ours) if f.as_ is not None else None,
                set(ctx.player_slots) if f.player is not None else None):
        if ids is not None:
            wanted = set(ids) if wanted is None else wanted & set(ids)
    if wanted is not None and not wanted:
        return []
    t = ReplayMomentIndex
    query = (select(t).join(ReplayMomentIndexState, ReplayMomentIndexState.replay_id == t.replay_id)
             .where(ReplayMomentIndexState.revision == index.INDEX_REVISION))
    if f.kind is not None:
        query = query.where(t.kind == f.kind)
    if f.map is not None:
        query = query.where(func.lower(t.map) == f.map.lower())
    if f.agent is not None:
        query = query.where(func.lower(t.agent) == f.agent.lower())
    if f.since is not None:
        query = query.where(t.played_at >= f.since.isoformat())
    if f.until is not None:
        query = query.where(t.played_at < (f.until + timedelta(days=1)).isoformat())
    if actor is not None:
        query = query.where(or_(t.kind == "state", t.player_id == actor))
    if wanted is not None:
        query = query.where(t.replay_id.in_(sorted(wanted)))
    return [{k: getattr(r, k) for k in ROW_KEYS} for r in db.execute(query).scalars()]


def _names(db, replay_ids) -> dict[int, dict[int, tuple[str, str]]]:
    """{replay id: {slot: (team, display name)}}."""
    out: dict[int, dict[int, tuple[str, str]]] = {rid: {} for rid in replay_ids}
    if not out:
        return out
    query = (select(ReplayPlayer.replay_id, ReplayPlayer.slot, MatchPlayer.team, Player.display_name)
             .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
             .join(Player, Player.id == MatchPlayer.player_id)
             .where(ReplayPlayer.replay_id.in_(sorted(out))))
    for rid, slot, team, name in db.execute(query):
        out[rid][slot] = (getattr(team, "value", team), name)
    return out


def _link(uuid: str, rnd: int, t: float) -> str:
    start = round(max(0.0, t - LEAD_SECONDS), 1)
    return f"/replays/{uuid}?round={rnd}&t={start:g}"


def search(db, args: list[str], *, rows=None, page=None) -> dict:
    f = parse(list(args))
    ctx, actor = _context(db, f)
    if rows is None:
        rows = _prefiltered_rows(db, f, ctx, actor)
    selected = match.select(f, ctx, rows)
    total = len(selected)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    number = page if page is not None else f.page
    chunk = selected[(number - 1) * PAGE_SIZE:number * PAGE_SIZE]
    names = _names(db, {r["replay_id"] for r in chunk})
    hits = []
    for row in chunk:
        our = match.our_team(f, ctx, row)
        by_slot = names.get(row["replay_id"], {})

        def name_of(slot, _by=by_slot, _our=our):
            entry = _by.get(slot)
            return entry[1] if entry is not None and _our is not None and entry[0] == _our else None

        if our == "team-2":
            score = f"{row['score2']}-{row['score1']}"
        else:
            score = f"{row['score1']}-{row['score2']}"
        hits.append({"uuid": row["uuid"], "map": row["map"], "played_at": row["played_at"], "round": row["round"],
                     "score": score, "kind": row["kind"], "t": row["t"],
                     "summary": match.summary(row, name_of, our), "link": _link(row["uuid"], row["round"], row["t"])})
    return {"total": total, "page": number, "pages": pages, "hits": hits}


def coach_lines(result: dict) -> list[str]:
    return [f"{h['uuid']} round {h['round']} t {h['t']} {h['kind']} {h['summary']}" for h in result["hits"]]
