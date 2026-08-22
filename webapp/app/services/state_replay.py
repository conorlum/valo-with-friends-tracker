"""Canonical round-replay engine shared by Fight-EV and (eventually) the
existing round-win/kill-order diamonds in player_graphs.py.

Data-contract audit (fight_ev_diamond.txt implementation order, step 1)
------------------------------------------------------------------------
Findings against the current schema (app/models/*.py) and the tracker.gg
adapter (app/adapters/trackergg_browserstate_source.py), which is the only
ingestion path for scraped data:

- Source event ordering: KillEvent has no explicit source-sequence column,
  only `event_time_seconds` and the DB-assigned `id`. The adapter inserts
  `player-round-kills` segments in whatever order tracker.gg's payload lists
  them (not guaranteed pre-sorted by time). Existing code
  (app/scoring/impact.py, app/services/player_graphs.py) already relies on
  sorting by `(event_time_seconds, id)` as a source-order tiebreaker -- this
  replay engine keeps that same convention rather than inventing a new one.
- Round side: not persisted anywhere. Reuses the documented convention from
  app/scoring/impact.py's `_attacking_team`: rounds 1-12 team-1 attacks,
  13-24 team-2 attacks, anything past 24 (overtime) has no recoverable side
  and must be excluded.
- Terminal time: `Round.defuse_time` and `Round.plant_time` (+45s) cover the
  defuse/detonation cases. Elimination and time-expiry wins have no stored
  timestamp -- elimination truncation is derived during replay (the casualty
  that empties the losing side's alive set), and a time-expiry round is not
  truncated at all, since tracker.gg's kill feed does not log any event past
  when the round timer/objective resolves.
- Resurrection/down events: none exist in this schema or adapter -- only
  plain kill/death pairs. There is no way to distinguish a genuine revive
  from a duplicate/ambiguous kill-feed row, so the MVP fallback from section
  3.6 applies: any round where a death or kill references a match-player
  already recorded dead is excluded from both `W` and `p` in full. The
  engine still accepts an explicit `revived_match_player_id` on
  `KillEventInput` so a future source that does supply real revive events
  can be replayed correctly without a redesign; today nothing produces one.
- Surrender marker: `app/services/surrender_rounds.py` documents the
  explicit marker (`Round.outcome` ending in "Surrendered Win") tracker.gg
  gives for its placeholder padding rounds. This module reuses that same
  predicate (`is_surrender_round`) rather than inferring "no kills" as a
  surrender signal, per section 3.1.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass, field

from app.models.match import Team


class TerminalCause(str, enum.Enum):
    DEFUSE = "defuse"
    DETONATION = "detonation"
    TIME = "time"
    ELIMINATION = "elimination"


@dataclass(frozen=True)
class StateEntryOccurrence:
    match_id: int
    round_id: int
    sequence: int
    event_time_seconds: float
    team1_alive_ids: frozenset[int]
    team2_alive_ids: frozenset[int]
    post_plant: bool
    winner: Team
    terminal_cause: TerminalCause | None = None


@dataclass(frozen=True)
class DuelOccurrence:
    match_id: int
    round_id: int
    sequence: int
    event_time_seconds: float
    team1_alive_before: frozenset[int]
    team2_alive_before: frozenset[int]
    killer_match_player_id: int
    victim_match_player_id: int


@dataclass
class ReplayDiagnostics:
    accepted_rounds: int = 0
    excluded_rounds_by_reason: Counter = field(default_factory=Counter)
    post_decision_events: int = 0
    ambiguous_lifecycle_rounds: int = 0
    equal_time_ambiguities: int = 0


@dataclass(frozen=True)
class KillEventInput:
    id: int
    killer_match_player_id: int | None
    death_match_player_id: int | None
    event_time_seconds: float
    # Not produced by any current source -- see the module docstring's
    # resurrection/down-event audit finding. Kept so a future source can
    # supply real revives without changing the replay algorithm below.
    revived_match_player_id: int | None = None


@dataclass(frozen=True)
class RoundInput:
    round_id: int
    round_number: int
    outcome: str | None
    planted: bool
    plant_time: float | None
    exploded: bool
    defused: bool
    defuse_time: float | None
    kill_events: tuple[KillEventInput, ...]


@dataclass(frozen=True)
class MatchInput:
    match_id: int
    team1_player_ids: frozenset[int]
    team2_player_ids: frozenset[int]
    rounds: tuple[RoundInput, ...]


@dataclass
class RoundReplayResult:
    entries: list[StateEntryOccurrence]
    duels: list[DuelOccurrence]
    exclusion_reason: str | None = None


def attacking_team_for_round(round_number: int) -> Team | None:
    """See the module docstring's "Round side" audit finding."""
    if round_number <= 12:
        return Team.TEAM_1
    if round_number <= 24:
        return Team.TEAM_2
    return None


def is_surrender_round(outcome: str | None) -> bool:
    return bool(outcome) and outcome.endswith("Surrendered Win")


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _outcome_cause(outcome: str | None) -> TerminalCause | None:
    # tracker.gg's roundResult values observed in this DB: "Elimination",
    # "Defuse", "Detonate", "Time", "Surrendered" (handled separately by
    # is_surrender_round). Matched as substrings of the "Team {A,B} {result}
    # Win" outcome string built by the adapter's _outcome_string.
    if not outcome:
        return None
    if "Elimination" in outcome:
        return TerminalCause.ELIMINATION
    if "Detonate" in outcome:
        return TerminalCause.DETONATION
    if "Defuse" in outcome:
        return TerminalCause.DEFUSE
    if "Time" in outcome:
        return TerminalCause.TIME
    return None


def is_valid_state_transition(event: KillEventInput) -> bool:
    """True for any event that changes a team's alive count: an ordinary
    kill, a self-kill, or an environmental casualty -- anything with a
    victim. Not true for a bare revive marker, which is a +1 transition
    handled separately in replay_round."""
    return event.death_match_player_id is not None


def is_valid_resolved_duel(
    event: KillEventInput, team_by_match_player: dict[int, Team]
) -> bool:
    """True only for a genuine opponent duel: known killer and victim, on
    different match-player IDs and different teams. Self-kills and
    environmental casualties change state (see is_valid_state_transition)
    but are never resolved duels."""
    killer = event.killer_match_player_id
    victim = event.death_match_player_id
    if killer is None or victim is None or killer == victim:
        return False
    killer_team = team_by_match_player.get(killer)
    victim_team = team_by_match_player.get(victim)
    if killer_team is None or victim_team is None:
        return False
    return killer_team != victim_team


def _terminal_time(round_input: RoundInput, cause: TerminalCause | None) -> float | None:
    if cause == TerminalCause.DEFUSE:
        return round_input.defuse_time
    if cause == TerminalCause.DETONATION:
        if round_input.planted and round_input.plant_time is not None:
            return round_input.plant_time + 45
        return None
    return None  # elimination is discovered during replay; time expiry has no stored cutoff


def replay_round(
    match_id: int,
    round_input: RoundInput,
    team1_player_ids: frozenset[int],
    team2_player_ids: frozenset[int],
    diagnostics: ReplayDiagnostics,
) -> RoundReplayResult:
    if is_surrender_round(round_input.outcome):
        diagnostics.excluded_rounds_by_reason["surrendered"] += 1
        return RoundReplayResult([], [], "surrendered")

    winner = _winner_team(round_input.outcome)
    if winner is None:
        diagnostics.excluded_rounds_by_reason["unknown_winner"] += 1
        return RoundReplayResult([], [], "unknown_winner")

    if not team1_player_ids or not team2_player_ids:
        diagnostics.excluded_rounds_by_reason["unknown_team_membership"] += 1
        return RoundReplayResult([], [], "unknown_team_membership")

    if round_input.round_number > 24:
        diagnostics.excluded_rounds_by_reason["overtime_unknown_side"] += 1
        return RoundReplayResult([], [], "overtime_unknown_side")

    cause = _outcome_cause(round_input.outcome)
    if cause is None:
        diagnostics.excluded_rounds_by_reason["unrecognized_round_result"] += 1
        return RoundReplayResult([], [], "unrecognized_round_result")

    terminal_time = _terminal_time(round_input, cause)
    if cause in (TerminalCause.DEFUSE, TerminalCause.DETONATION) and terminal_time is None:
        diagnostics.excluded_rounds_by_reason["missing_terminal_time"] += 1
        return RoundReplayResult([], [], "missing_terminal_time")

    team_by_match_player: dict[int, Team] = {}
    for pid in team1_player_ids:
        team_by_match_player[pid] = Team.TEAM_1
    for pid in team2_player_ids:
        team_by_match_player[pid] = Team.TEAM_2

    events = sorted(round_input.kill_events, key=lambda e: (e.event_time_seconds, e.id))

    transition_times = [e.event_time_seconds for e in events if is_valid_state_transition(e)]
    if len(transition_times) != len(set(transition_times)):
        diagnostics.equal_time_ambiguities += 1
        diagnostics.excluded_rounds_by_reason["equal_time_ambiguity"] += 1
        return RoundReplayResult([], [], "equal_time_ambiguity")

    alive = {Team.TEAM_1: set(team1_player_ids), Team.TEAM_2: set(team2_player_ids)}
    entries: list[StateEntryOccurrence] = []
    duels: list[DuelOccurrence] = []
    post_plant = False
    sequence = 0

    def snapshot(event_time: float, entry_cause: TerminalCause | None) -> None:
        nonlocal sequence
        entries.append(
            StateEntryOccurrence(
                match_id=match_id,
                round_id=round_input.round_id,
                sequence=sequence,
                event_time_seconds=event_time,
                team1_alive_ids=frozenset(alive[Team.TEAM_1]),
                team2_alive_ids=frozenset(alive[Team.TEAM_2]),
                post_plant=post_plant,
                winner=winner,
                terminal_cause=entry_cause,
            )
        )
        sequence += 1

    snapshot(0.0, None)

    decided = False
    for event in events:
        if decided:
            diagnostics.post_decision_events += 1
            continue

        if (
            round_input.planted
            and round_input.plant_time is not None
            and event.event_time_seconds >= round_input.plant_time
        ):
            post_plant = True

        if terminal_time is not None and event.event_time_seconds >= terminal_time:
            diagnostics.post_decision_events += 1
            continue

        if event.revived_match_player_id is not None:
            revived_id = event.revived_match_player_id
            revived_team = team_by_match_player.get(revived_id)
            if revived_team is not None:
                alive[revived_team].add(revived_id)
                snapshot(event.event_time_seconds, None)
            continue

        if not is_valid_state_transition(event):
            continue

        killer = event.killer_match_player_id
        victim = event.death_match_player_id

        victim_alive = victim in alive[Team.TEAM_1] or victim in alive[Team.TEAM_2]
        if not victim_alive:
            diagnostics.ambiguous_lifecycle_rounds += 1
            diagnostics.excluded_rounds_by_reason["ambiguous_lifecycle"] += 1
            return RoundReplayResult([], [], "ambiguous_lifecycle")

        if killer is not None and killer != victim:
            killer_alive = killer in alive[Team.TEAM_1] or killer in alive[Team.TEAM_2]
            if not killer_alive:
                diagnostics.ambiguous_lifecycle_rounds += 1
                diagnostics.excluded_rounds_by_reason["ambiguous_lifecycle"] += 1
                return RoundReplayResult([], [], "ambiguous_lifecycle")

        if is_valid_resolved_duel(event, team_by_match_player):
            duels.append(
                DuelOccurrence(
                    match_id=match_id,
                    round_id=round_input.round_id,
                    sequence=sequence,
                    event_time_seconds=event.event_time_seconds,
                    team1_alive_before=frozenset(alive[Team.TEAM_1]),
                    team2_alive_before=frozenset(alive[Team.TEAM_2]),
                    killer_match_player_id=killer,
                    victim_match_player_id=victim,
                )
            )

        victim_team = team_by_match_player.get(victim)
        if victim_team is not None:
            alive[victim_team].discard(victim)

        entry_cause = None
        if cause == TerminalCause.ELIMINATION and (not alive[Team.TEAM_1] or not alive[Team.TEAM_2]):
            entry_cause = TerminalCause.ELIMINATION
            decided = True
        snapshot(event.event_time_seconds, entry_cause)

    if not decided and entries:
        # Defuse/detonation/time endings have no dedicated triggering event of
        # their own (only kills create entries) -- attach the terminal cause to
        # the last existing entry instead of fabricating a new one.
        last = entries[-1]
        entries[-1] = StateEntryOccurrence(
            match_id=last.match_id,
            round_id=last.round_id,
            sequence=last.sequence,
            event_time_seconds=last.event_time_seconds,
            team1_alive_ids=last.team1_alive_ids,
            team2_alive_ids=last.team2_alive_ids,
            post_plant=last.post_plant,
            winner=last.winner,
            terminal_cause=cause,
        )

    diagnostics.accepted_rounds += 1
    return RoundReplayResult(entries, duels, None)


def replay_match(
    match_input: MatchInput, diagnostics: ReplayDiagnostics | None = None
) -> tuple[list[StateEntryOccurrence], list[DuelOccurrence], ReplayDiagnostics]:
    diagnostics = diagnostics if diagnostics is not None else ReplayDiagnostics()
    entries: list[StateEntryOccurrence] = []
    duels: list[DuelOccurrence] = []
    for round_input in match_input.rounds:
        result = replay_round(
            match_input.match_id,
            round_input,
            match_input.team1_player_ids,
            match_input.team2_player_ids,
            diagnostics,
        )
        entries.extend(result.entries)
        duels.extend(result.duels)
    return entries, duels, diagnostics
