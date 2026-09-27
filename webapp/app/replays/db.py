"""The linker's database layer (docs/replay-viewer-plan.md, "Linking", Stage 2).

`link.py` stays pure; this module loads what it compares (`load_link_candidates`), and writes
what it decided (`write_link`): `replays.match_id`, `clock_offset`, `kill_map`, `db_deaths`,
`link_status`, `link_report`, `replay_players.match_player_id`, and the `players.riot_subject`
backfill. It never rewrites a blob or `link_inputs`, the only copy for an upload.

`players.riot_subject` is read and written with Core SQL: `app/models/player.py` is a scoring
HASHED_SOURCE, so it has no ORM attribute (migration 0012 adds the column). On a database without
the column (sqlite tests, a DB before 0012) there are no anchors and no backfill.

Nothing here imports scoring code.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, inspect, text

from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round
from app.models.replay import Replay, ReplayPlayer, ReplayRound
from app.replays import format as fmt
from app.replays import link as lk

# Decision 1 (closed 2026-09-27 by Stage 1b findings 15 and 21): Subjects are real and stable,
# so a link backfills `players.riot_subject` under Linking step 6's rules (AFK run decision D6).
BACKFILL_SUBJECTS = True


def has_riot_subject(session) -> bool:
    # The session's own connection: inspecting the engine would check a pooled connection out
    # and back in, and a check-in resets (rolls back) whatever transaction it carried.
    return any(c["name"] == "riot_subject" for c in inspect(session.connection()).get_columns("players"))


def _canonical(subject) -> str | None:
    return None if subject is None else str(subject).lower()


def load_link_candidates(session, match_uuid: str) -> tuple[list[lk.DbMatch], dict[str, int]]:
    """Reads, never writes: the `matches` rows for this UUID and everything the linker compares,
    and every `players.riot_subject` already set (canonical lower-case string -> player id)."""
    with_subjects = has_riot_subject(session)
    candidates = []
    for match in session.query(Match).filter(func.lower(Match.external_id) == match_uuid.lower()).all():
        rows = (session.query(MatchPlayer, Player).join(Player, MatchPlayer.player_id == Player.id)
                .filter(MatchPlayer.match_id == match.id).all())
        subjects = {}
        if with_subjects and rows:
            subjects = {pid: _canonical(s) for pid, s in session.execute(
                text("SELECT id, riot_subject FROM players WHERE id IN :ids").bindparams(
                    _expanding("ids")), {"ids": [player.id for _, player in rows]}).all()}
        players = []
        for mp, player in rows:
            team = mp.team.value if hasattr(mp.team, "value") else str(mp.team)
            players.append(lk.DbPlayer(mp.id, player.id, mp.agent, team, subjects.get(player.id)))
        rounds = session.query(Round).filter(Round.match_id == match.id).order_by(Round.round_number).all()
        number_of = {r.id: r.round_number for r in rounds}
        kills = []
        if rounds:
            for kill in session.query(KillEvent).filter(KillEvent.round_id.in_(list(number_of))).all():
                kills.append(lk.DbKill(kill.id, number_of[kill.round_id], kill.event_time_seconds,
                                       kill.killer_match_player_id, kill.death_match_player_id))
        candidates.append(lk.DbMatch(
            match.id, match.external_id, match.team1_rounds_won, match.team2_rounds_won,
            [lk.DbRound(r.round_number, r.outcome, r.plant_time, r.defuse_time) for r in rounds], players, kills))
    owners: dict[str, int] = {}
    if with_subjects:
        owners = {_canonical(s): pid for pid, s in session.execute(
            text("SELECT id, riot_subject FROM players WHERE riot_subject IS NOT NULL")).all()}
    return candidates, owners


def _expanding(name: str):
    from sqlalchemy import bindparam

    return bindparam(name, expanding=True)


def link_view(session, replay: Replay) -> lk.ReplayLinkView:
    players = [{"slot": p.slot, "subject": _canonical(p.subject), "agent": p.agent, "side_group": p.side_group}
               for p in session.query(ReplayPlayer).filter(ReplayPlayer.replay_id == replay.id)
               .order_by(ReplayPlayer.slot)]
    report = replay.link_inputs or {}
    return lk.ReplayLinkView(str(replay.match_uuid).lower(), replay.round_count,
                             bool(report.get("dropped_final_round")), players, replay.link_inputs)


def clear_link(session, replay: Replay, status: str, report: dict) -> None:
    replay.link_status = status
    replay.link_report = report
    replay.match_id = None
    replay.clock_offset = None
    replay.kill_map = None
    replay.db_deaths = None
    replay.kill_impact = None
    replay.linked_at = None
    session.query(ReplayPlayer).filter(ReplayPlayer.replay_id == replay.id).update(
        {ReplayPlayer.match_player_id: None}, synchronize_session=False)


def write_link(session, replay: Replay, result: lk.LinkResult) -> str:
    """Writes the linker's decision in the caller's transaction; returns the final status. The
    backfill is the last statement, rechecked under `FOR UPDATE` (Linking step 6): any conflict
    turns the link into `refused` and nothing of it is written."""
    if result.status != "linked":
        clear_link(session, replay, result.status, result.report)
        return result.status
    if BACKFILL_SUBJECTS and result.backfills and has_riot_subject(session):
        conflict = _backfill(session, result.backfills)
        if conflict is not None:
            clear_link(session, replay, "refused", {"check": "backfill", "reason": conflict,
                                                    "match_id": result.match_id})
            return "refused"
    # Another replay linked to this match (a different recording) keeps it: match_id is unique.
    other = session.query(Replay.id).filter(Replay.match_id == result.match_id, Replay.id != replay.id).first()
    if other is not None:
        clear_link(session, replay, "refused", {"check": "match", "reason": "another replay is linked to this match",
                                                "match_id": result.match_id})
        return "refused"
    replay.link_status = "linked"
    replay.match_id = result.match_id
    replay.clock_offset = result.clock_offset
    replay.kill_map = result.kill_map
    replay.db_deaths = result.db_deaths
    replay.link_report = result.report
    replay.linked_at = datetime.now(timezone.utc)
    replay.kill_impact = None  # recomputed after commit (app/services/replay_impact.py)
    for slot, mp in result.slot_to_match_player.items():
        session.query(ReplayPlayer).filter(ReplayPlayer.replay_id == replay.id, ReplayPlayer.slot == slot).update(
            {ReplayPlayer.match_player_id: mp}, synchronize_session=False)
    return "linked"


def _backfill(session, backfills: list[tuple[int, str]]) -> str | None:
    """Sets `players.riot_subject` for each (player id, Subject), or returns why it can't: a
    player that already has a different Subject, or a Subject another player already has."""
    locking = session.get_bind().dialect.name == "postgresql"
    for player_id, subject in backfills:
        subject = _canonical(subject)
        current = session.execute(text("SELECT riot_subject FROM players WHERE id = :id"
                                       + (" FOR UPDATE" if locking else "")), {"id": player_id}).scalar()
        if current is not None and _canonical(current) != subject:
            return "a linked player already has a different Subject"
        owner = session.execute(text("SELECT id FROM players WHERE riot_subject = :s AND id <> :id"),
                                {"s": subject, "id": player_id}).scalar()
        if owner is not None:
            return "the Subject already belongs to another player"
    for player_id, subject in backfills:
        session.execute(text("UPDATE players SET riot_subject = :s WHERE id = :id AND riot_subject IS NULL"),
                        {"s": _canonical(subject), "id": player_id})
    return None


def link_replay(session, replay: Replay) -> str:
    """Links one stored replay in the caller's transaction (the caller holds the advisory lock)."""
    candidates, owners = load_link_candidates(session, str(replay.match_uuid))
    result = lk.link(link_view(session, replay), candidates, owners)
    return write_link(session, replay, result)


def advisory_lock(session, match_uuid: str) -> None:
    """Serialises every store and link of one replay (plan: `pg_advisory_xact_lock(hashtext(uuid))`)."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:u))"), {"u": match_uuid.lower()})


# ---------------------------------------------------------------- validity and the Impact fingerprint


def is_valid(session, replay: Replay) -> bool:
    """Complete (one row per round, 1..round_count) and every blob of a supported `v`. The recipe
    is one per replay by construction. Invalid reads as "no replay"."""
    numbers = [n for (n,) in session.query(ReplayRound.round_number).filter(ReplayRound.replay_id == replay.id)
               .order_by(ReplayRound.round_number)]
    if numbers != list(range(1, replay.round_count + 1)):
        return False
    return replay.format_version in fmt.SUPPORTED_VERSIONS


def is_linked(replay: Replay) -> bool:
    """Linked means both the status and a match row: stale link data never renders."""
    return replay.link_status == "linked" and replay.match_id is not None


IMPACT_FINGERPRINT_EXCLUDE = frozenset({"id", "created_at", "updated_at"})


def impact_fingerprint(session, match_id: int) -> str:
    """sha256 over every stored `impact_scores` column (but ids and timestamps) of the match's
    rows, in a fixed order. The per-kill split is shown only while this is unchanged."""
    columns = [c for c in ImpactScore.__table__.columns if c.name not in IMPACT_FINGERPRINT_EXCLUDE]
    rows = (session.query(*columns).join(Round, Round.id == ImpactScore.round_id)
            .filter(Round.match_id == match_id)
            .order_by(ImpactScore.round_id, ImpactScore.match_player_id).all())
    payload = json.dumps([[c.name for c in columns], [list(r) for r in rows]], default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def kill_impact_for_page(session, replay: Replay) -> dict[int, list[float]] | None:
    """The stored per-kill split ({kill_events.id: [gain, loss]}) while it still describes the
    stored `impact_scores` rows, else None (the feed hides it until it is recomputed)."""
    split = replay.kill_impact
    if not split or not is_linked(replay):
        return None
    if split.get("fingerprint") != impact_fingerprint(session, replay.match_id):
        return None
    return {int(k): v for k, v in (split.get("kills") or {}).items()}
