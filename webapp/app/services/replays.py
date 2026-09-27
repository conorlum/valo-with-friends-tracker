"""The replay pages' reads (docs/replay-viewer-plan.md, "The viewer"). Read-only; no scoring code.

- `find_replay`: a stored replay by match UUID, only when valid (a complete round set, a
  supported `v`); anything else reads as "no replay".
- `replay_url_for_match` / `watchable_external_ids`: links in, only for a linked, valid replay.
- `page_context`: what the page renders beside the blobs. For a linked replay: names, teams and
  sides per slot, and per round the DB outcome, plant and defuse on the replay clock
  (`t_replay = t_db - clock_offset`), each player's stats and stored Impact row, and each kill's
  weapon and per-kill Impact split (shown only while it still describes the stored rows). For an
  unlinked one: the reason only. Subjects never leave the server.

Everything is off in demo mode: the ValoMaths demo has no replays (decision 4).
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from sqlalchemy import func

from app.config import settings
from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.replay import Replay, ReplayPlayer, ReplayRound
from app.replays import db as replay_db
from app.replays import format as fmt
from app.replays import link as lk


def replays_enabled() -> bool:
    return not settings.demo_mode


def find_replay(db, match_uuid: str) -> Replay | None:
    if not replays_enabled():
        return None
    replay = db.query(Replay).filter(Replay.match_uuid == match_uuid.lower()).one_or_none()
    if replay is None or not replay_db.is_valid(db, replay):
        return None
    return replay


def round_blob(db, replay: Replay, round_number: int) -> bytes | None:
    row = db.get(ReplayRound, (replay.id, round_number))
    return None if row is None else row.data


def replay_url_for_match(db, match: Match) -> str | None:
    """`/replays/<uuid>` when a linked, valid replay of this match exists, else None."""
    if not replays_enabled():
        return None
    replay = db.query(Replay).filter(Replay.match_id == match.id, Replay.link_status == "linked").one_or_none()
    if replay is None or not replay_db.is_valid(db, replay):
        return None
    return f"/replays/{str(replay.match_uuid).lower()}"


def watchable_external_ids(db, external_ids) -> set[str]:
    """The lower-cased external IDs (= match UUIDs) among these that have a linked, valid replay:
    one query for a whole list page."""
    if not replays_enabled():
        return set()
    wanted = {str(e).lower() for e in external_ids if e}
    if not wanted:
        return set()
    counts = (db.query(Replay.match_uuid, Replay.round_count, func.count(ReplayRound.round_number))
              .join(ReplayRound, ReplayRound.replay_id == Replay.id)
              .filter(Replay.link_status == "linked", Replay.match_id.isnot(None),
                      Replay.format_version.in_(sorted(fmt.SUPPORTED_VERSIONS)))
              .group_by(Replay.id, Replay.match_uuid, Replay.round_count).all())
    return {str(uuid).lower() for uuid, rounds, stored in counts if rounds == stored} & wanted


@lru_cache(maxsize=1)
def _uv_per_unit() -> dict[str, float]:
    from app.replays.condense import load_maps

    return {name: round(abs(m.x_mult) * fmt.UV_SCALE, 5) for name, m in load_maps().items()}


def _team(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def page_context(db, replay: Replay) -> dict:
    """The page's data beside the blobs: JSON-ready, no Subjects."""
    uuid = str(replay.match_uuid).lower()
    base = {"uuid": uuid, "map": replay.map_name, "rounds": list(range(1, replay.round_count + 1)),
            "source_sha256": replay.source_sha256, "uv_per_unit": _uv_per_unit().get(replay.map_name),
            "linked": replay_db.is_linked(replay), "status": replay.link_status}
    if not base["linked"]:
        report = replay.link_report or {}
        base["reason"] = ("not linked to a match on this site" if replay.link_status == "unlinked"
                          else f"not linked: {report.get('reason') or 'refused'}")
        return {"match": base, "players": {}, "rounds": {}}

    match = db.get(Match, replay.match_id)
    offset = replay.clock_offset or 0.0
    rows = (db.query(ReplayPlayer, MatchPlayer, Player)
            .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
            .join(Player, Player.id == MatchPlayer.player_id)
            .filter(ReplayPlayer.replay_id == replay.id).all())
    side_to_team = (replay.link_report or {}).get("side_to_team") or {}
    team_side = {team: side for side, team in side_to_team.items()}
    players, slot_of_mp = {}, {}
    for rp, mp, player in rows:
        team = _team(mp.team)
        players[str(rp.slot)] = {"slot": rp.slot, "name": player.display_name, "player_id": player.id,
                                 "match_player_id": mp.id, "agent": mp.agent, "team": team,
                                 "side": rp.side_group or team_side.get(team)}
        slot_of_mp[mp.id] = rp.slot

    rounds = db.query(Round).filter(Round.match_id == match.id).order_by(Round.round_number).all()
    number_of = {r.id: r.round_number for r in rounds}
    kills = {k.id: k for k in db.query(KillEvent).filter(KillEvent.round_id.in_(list(number_of)))} if rounds else {}
    split = replay_db.kill_impact_for_page(db, replay) or {}
    stats: dict[int, dict] = defaultdict(dict)
    for s in db.query(RoundPlayerStat).filter(RoundPlayerStat.round_id.in_(list(number_of))):
        slot = slot_of_mp.get(s.match_player_id)
        if slot is not None:
            stats[number_of[s.round_id]][str(slot)] = {"score": s.score, "kills": s.kills, "deaths": s.deaths,
                                                       "assists": s.assists, "loadout": s.loadout,
                                                       "remaining": s.remaining}
    for i in db.query(ImpactScore).filter(ImpactScore.round_id.in_(list(number_of))):
        slot = slot_of_mp.get(i.match_player_id)
        if slot is not None:
            stats[number_of[i.round_id]].setdefault(str(slot), {}).update({
                "impact": i.impact, "kill_impact": i.kill_impact, "death_impact": i.death_impact,
                "damage": i.damage, "econ": i.econ_component, "trade_credit": i.trade_credit})

    out_rounds = {}
    kill_map = replay.kill_map or {}
    for r in rounds:
        n = r.round_number
        feed = {}
        for index, kill_id in enumerate(kill_map.get(str(n), [])):
            row = kills.get(kill_id) if kill_id is not None else None
            entry = {"db_id": kill_id, "weapon": row.weapon if row is not None else None}
            share = split.get(kill_id) if kill_id is not None else None
            if share:
                entry["gain"], entry["loss"] = round(share[0], 1), round(share[1], 1)
                if len(share) >= 4:
                    entry["state"] = [share[2], share[3]]
            feed[str(index)] = entry
        out_rounds[str(n)] = {
            "db": {"outcome": r.outcome, "winner": lk.outcome_winner(r.outcome),
                   "plant": None if r.plant_time is None else round(r.plant_time - offset, 3),
                   "defuse": None if r.defuse_time is None else round(r.defuse_time - offset, 3),
                   "exploded": r.exploded, "defused": r.defused},
            "stats": stats.get(n, {}),
            "kills": feed,
            "db_deaths": (replay.db_deaths or {}).get(str(n), []),
        }
    base.update({"match_id": match.id, "external_id": match.external_id, "clock_offset": offset,
                 "score": [match.team1_rounds_won, match.team2_rounds_won], "side_to_team": side_to_team,
                 "per_kill_impact": bool(split)})
    return {"match": base, "players": players, "rounds": out_rounds}
