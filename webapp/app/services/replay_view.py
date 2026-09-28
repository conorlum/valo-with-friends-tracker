"""Stage 4: the site's analysis beside a linked replay (docs/replay-viewer-plan.md, "Stage 4").
Read-only; it computes no Impact (decision 5, amended: the per-kill split comes from
app/services/replay_impact.py at ingest, and the round's `impact_scores` rows are shown as stored).

- **Playback state** (`alive_steps`): the players alive per team at every moment of a round, from
  the blob's `alive` intervals (a revive opens a new one) plus the link's DB-only deaths (spike,
  fall, self and team kills the replay may not show), on the replay clock. It drives the live
  5v5 -> 4v5 badge.
- **Analytical annotations** (`round_annotations`): the man-advantage states of
  `state_replay.replay_round`, the same engine the player pages use, for the rounds it includes;
  an excluded round (equal-time kills, an ambiguous lifecycle, ...) shows its reason instead.
  Each state is placed at its DB time converted with the clock offset; a defuse or detonation
  ending sits at its own DB time, and replay kills after the round was decided are marked
  post-decision.
"""

from __future__ import annotations

from app.models import KillEvent, MatchPlayer, Round
from app.models.match import Team
from app.services.state_replay import (
    KillEventInput,
    ReplayDiagnostics,
    RoundInput,
    TerminalCause,
    replay_round,
)

# A DB-only death within this long before a replay death of the same slot is that death, not another.
SAME_DEATH_S = 1.0


def _team(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def alive_steps(blob: dict, slot_team: dict[int, str], db_deaths: list[dict], clock_offset: float) -> list[list]:
    """[[t, team-1 alive, team-2 alive], ...]: one entry at the round's start and one at every
    change, on the replay clock. Deaths at the same moment land in one step."""
    t_start = float(blob.get("t_start") or 0.0)
    lives: dict[int, list[list[float | None]]] = {}
    for slot, intervals in (blob.get("alive") or {}).items():
        lives[int(slot)] = [[iv[0], iv[1]] for iv in intervals]
    for death in db_deaths or []:
        slot = death.get("slot")
        if slot is None or slot not in lives:
            continue
        t = round(float(death["t_db"]) - clock_offset, 3)
        for life in lives[slot]:
            start, end = life
            if start <= t and (end is None or t < end - SAME_DEATH_S):
                life[1] = t
                break
    changes = {t_start}
    for intervals in lives.values():
        for start, end in intervals:
            changes.add(max(start, t_start))
            if end is not None:
                changes.add(end)
    steps: list[list] = []
    for t in sorted(changes):
        counts = {"team-1": 0, "team-2": 0}
        for slot, intervals in lives.items():
            team = slot_team.get(slot)
            if team in counts and any(start <= t and (end is None or t < end) for start, end in intervals):
                counts[team] += 1
        row = [round(t, 3), counts["team-1"], counts["team-2"]]
        if not steps or steps[-1][1:] != row[1:]:
            steps.append(row)
    return steps


def _round_input(round_row: Round, kills: list[KillEvent]) -> RoundInput:
    return RoundInput(
        round_id=round_row.id, round_number=round_row.round_number, outcome=round_row.outcome,
        planted=round_row.planted, plant_time=round_row.plant_time, exploded=round_row.exploded,
        defused=round_row.defused, defuse_time=round_row.defuse_time,
        kill_events=tuple(KillEventInput(id=k.id, killer_match_player_id=k.killer_match_player_id,
                                         death_match_player_id=k.death_match_player_id,
                                         event_time_seconds=k.event_time_seconds) for k in kills))


def round_annotations(db, match_id: int, clock_offset: float) -> dict[int, dict]:
    """{round number: {"states": [...], "ending": {...} | None, "excluded": reason | None}}."""
    players = db.query(MatchPlayer).filter(MatchPlayer.match_id == match_id).all()
    team1 = frozenset(mp.id for mp in players if mp.team == Team.TEAM_1)
    team2 = frozenset(mp.id for mp in players if mp.team == Team.TEAM_2)
    rounds = db.query(Round).filter(Round.match_id == match_id).order_by(Round.round_number).all()
    kills: dict[int, list[KillEvent]] = {r.id: [] for r in rounds}
    for kill in db.query(KillEvent).filter(KillEvent.round_id.in_(list(kills))):
        kills[kill.round_id].append(kill)
    out = {}
    diagnostics = ReplayDiagnostics()
    for r in rounds:
        result = replay_round(match_id, _round_input(r, kills[r.id]), team1, team2, diagnostics)
        if result.exclusion_reason:
            out[r.round_number] = {"states": [], "ending": None, "excluded": result.exclusion_reason}
            continue
        states = [{"t": round(e.event_time_seconds - clock_offset, 3) if e.sequence else 0.0,
                   "alive": [len(e.team1_alive_ids), len(e.team2_alive_ids)],
                   "post_plant": e.post_plant, "decided_by": e.terminal_cause.value if e.terminal_cause else None}
                  for e in result.entries]
        ending = None
        if r.defused and r.defuse_time is not None and "Defuse" in (r.outcome or ""):
            ending = {"cause": TerminalCause.DEFUSE.value, "t": round(r.defuse_time - clock_offset, 3)}
        elif "Detonate" in (r.outcome or "") and r.plant_time is not None:
            ending = {"cause": TerminalCause.DETONATION.value, "t": round(r.plant_time + 45 - clock_offset, 3)}
        elif "Time" in (r.outcome or ""):
            ending = {"cause": TerminalCause.TIME.value, "t": None}
        out[r.round_number] = {"states": states, "ending": ending, "excluded": None,
                               "winner": _team(result.entries[0].winner) if result.entries else None}
    return out
