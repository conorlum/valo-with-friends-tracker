"""Replay search (R1, R2, R6, R7, D4, D5): the library behind the CLI, the route and the coach.

`search(db, args)` parses the filters with `app.search.filters.parse`, resolves Riot IDs into a
`match.Context`, reads the stored index rows (SQL is only a prefilter; `match.select` decides every hit) and
returns one page of hits. Only rows whose replay's state row is at `index.INDEX_REVISION` are read.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

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
    query = (select(*[getattr(t, k) for k in ROW_KEYS])
             .join(ReplayMomentIndexState, ReplayMomentIndexState.replay_id == t.replay_id)
             .where(ReplayMomentIndexState.revision == index.INDEX_REVISION))
    for condition in _conditions(f, actor):
        query = query.where(condition)
    if wanted is not None:
        query = query.where(t.replay_id.in_(sorted(wanted)))
    return [{k: row[k] for k in ROW_KEYS} for row in db.execute(query).mappings()]


def _either(column_a, column_b, test):
    """`test` on either of two columns: the filter's team order is only known per row."""
    return or_(test(column_a), test(column_b))


def _conditions(f, actor) -> list:
    """SQL conditions that keep a superset of what `match.matches` keeps (never fewer rows). `tag` is left to the
    matcher (a JSON column), and so are `player`, `with`, `circle` and `as` beyond the replay-id set."""
    t = ReplayMomentIndex
    out = []
    if f.kind is not None:
        out.append(t.kind == f.kind)
    if f.map is not None:
        out.append(func.lower(t.map) == f.map.lower())
    if f.agent is not None:
        out.append(func.lower(t.agent) == f.agent.lower())
    if f.since is not None:
        out.append(t.played_at >= f.since.isoformat())
    if f.until is not None:
        out.append(t.played_at < (f.until + timedelta(days=1)).isoformat())
    if actor is not None:
        out.append(or_(t.kind == "state", t.player_id == actor))
    if f.ult is not None:
        out.append(t.ult == f.ult)
    if f.first is not None:
        out.append(t.first == f.first)
    if f.planted is not None:
        out.append(t.planted == f.planted)
    if f.post_plant is not None:
        out.append(t.post_plant == f.post_plant)
    if f.site is not None:
        out.append(t.site == f.site)
    if f.round is not None:
        out.append(t.round.between(*f.round))
    if f.after is not None:
        out.append(t.t >= f.after - 1e-6)
    if f.before is not None:
        out.append(t.t <= f.before + 1e-6)
    if f.left is not None:
        out.append(and_(t.post_plant.is_(True), t.plant_t.is_not(None),
                        t.plant_t + match.SPIKE_SECONDS - t.t >= f.left - 1e-6))  # slack for float rounding
    if f.won is not None:
        out.append(t.won_team.is_not(None))
    if f.team is not None:
        out.append(t.team.is_not(None))
    if f.state is not None:
        x, y = f.state
        out.append(or_(and_(t.alive1 == x, t.alive2 == y), and_(t.alive1 == y, t.alive2 == x)))
    for rng in (f.our_score, f.their_score):
        if rng is not None:
            out.append(_either(t.score1, t.score2, lambda column, rng=rng: column.between(*rng)))
    for buy in (f.our_buy, f.their_buy):
        if buy is not None:
            out.append(_either(t.buy1, t.buy2, lambda column, buy=buy: column == buy))
    if f.ability is not None:
        wanted_ability = f.ability.casefold()
        if wanted_ability.isascii():
            if len(wanted_ability) == 1:
                out.append(func.lower(t.ability_key) == wanted_ability)
            else:
                out.append(func.lower(t.ability).contains(wanted_ability, autoescape=True))
    return out


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
