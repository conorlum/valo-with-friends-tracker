"""Rows for the Form card's recent-match list: result, map, score, agent,
K/D/A, average Impact, and badges for what stood out in each match.

Three kinds of badge:
  - Party size: Duo / Trio / 4-stack / 5-stack, from how many of this
    player's friends (their Friends page list) were on their team. Not the
    tracked_players.json roster -- that only decides which matches get
    crawled. tracker.gg doesn't say who queued together, so this is
    inferred from teammates.
  - Events, shown whenever they happen: multi-kill rounds (3K/4K/Ace/6K) and
    1vX clutches.
  - Stand-outs, relative to THIS player: a match earns one when its per-round
    rate for a stat is in the top STANDOUT_QUANTILE of the player's own last
    BASELINE_MATCHES matches (and clears a small absolute minimum), so a
    badge means "unusually high for you", not "high for anyone".

Computed live from one batch of queries (the page's own cache doesn't hold
per-round data); every query is scoped to the baseline window's matches.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, selectinload

from app.models import ImpactScore, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.services.match_streaks import RECENT_FORM_COUNT, FormEntry, form_entry
from app.services.friends import list_friend_ids
from app.services.player_profile_types import match_win

BASELINE_MATCHES = 30
# Need this many baseline matches before stand-out badges mean anything.
MIN_BASELINE_MATCHES = 10
STANDOUT_QUANTILE = 0.8
MULTI_KILL_LABELS = {3: "3K", 4: "4K", 5: "Ace", 6: "6K"}
PARTY_LABELS = {2: "Duo", 3: "Trio", 4: "4-stack", 5: "5-stack"}


@dataclass(frozen=True)
class StandoutStat:
    key: str
    label: str
    kind: str  # "good" or "bad"
    # Absolute floor on the match total, so a quiet match can't earn a badge
    # just because the player's baseline is also quiet.
    min_total: float
    description: str


STANDOUT_STATS = (
    StandoutStat("first_bloods", "First bloods", "good", 3, "opening kills"),
    StandoutStat("first_deaths", "First deaths", "bad", 3, "times dying first in the round"),
    StandoutStat("econ_kill", "Econ impact", "good", 300, "kill impact from econ swings"),
    StandoutStat("clutch_kill", "Clutch impact", "good", 300, "kill impact in clutch / high-impact moments"),
    StandoutStat("post_plant_kill", "Post-plant impact", "good", 300, "kill impact after the plant"),
)


@dataclass
class MatchBadge:
    label: str
    kind: str  # "party", "event", "good", or "bad"
    title: str


@dataclass
class RecentMatchRow:
    entry: FormEntry
    agent: str | None = None
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    average_impact: float | None = None
    badges: list[MatchBadge] = field(default_factory=list)


@dataclass
class _MatchStats:
    rounds: int = 0
    impact_sum: float = 0.0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    multi_kills: dict[int, int] = field(default_factory=dict)  # kills in a round -> rounds
    clutches: dict[int, int] = field(default_factory=dict)  # X in 1vX -> rounds won
    totals: dict[str, float] = field(default_factory=dict)  # StandoutStat.key -> match total


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


def _team(mp: MatchPlayer) -> str:
    return mp.team.value if hasattr(mp.team, "value") else mp.team


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _round_first_kill_and_clutch(round_row: Round, player_mp_id: int, own_ids: set[int], opp_ids: set[int]):
    """(first_blood, first_death, clutch_vs) for the player in one round.
    clutch_vs is X when the player was left alone against X enemies and
    their team won the round, else None."""
    events = sorted(round_row.kill_events, key=lambda e: (e.event_time_seconds, e.id))
    first = events[0] if events else None
    first_blood = first is not None and first.killer_match_player_id == player_mp_id
    first_death = first is not None and first.death_match_player_id == player_mp_id

    alive_own, alive_opp = set(own_ids), set(opp_ids)
    alone_vs: int | None = None
    for event in events:
        alive_own.discard(event.death_match_player_id)
        alive_opp.discard(event.death_match_player_id)
        if alone_vs is None and alive_own == {player_mp_id} and alive_opp:
            alone_vs = len(alive_opp)
        if not alive_own or not alive_opp:
            break
    return first_blood, first_death, alone_vs


def recent_form_entries(db: Session, player_id: int, limit: int = RECENT_FORM_COUNT) -> list[FormEntry]:
    """The player's `limit` most recent matches as FormEntry rows, newest first
    -- the same matches the Form card's streak strip covers."""
    mps = (
        db.query(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(MatchPlayer.player_id == player_id)
        .order_by(Match.played_at.desc().nullslast(), Match.id.desc())
        .limit(limit)
        .all()
    )
    return [form_entry(mp.match, _team(mp), match_win(mp.match, _team(mp))) for mp in mps]


def build_recent_match_rows(db: Session, player_id: int, entries: list[FormEntry]) -> list[RecentMatchRow]:
    """One row per entry (same order), with stats and badges for `player_id`."""
    if not entries:
        return []

    shown_ids = {e.external_id for e in entries}
    baseline_mps = (
        db.query(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(MatchPlayer.player_id == player_id)
        .order_by(Match.played_at.desc().nullslast(), Match.id.desc())
        .limit(BASELINE_MATCHES)
        .all()
    )
    shown_mps = (
        db.query(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(MatchPlayer.player_id == player_id, Match.external_id.in_(shown_ids))
        .all()
    )
    player_mps = {mp.id: mp for mp in baseline_mps + shown_mps}
    match_ids = {mp.match_id for mp in player_mps.values()}
    baseline_mp_ids = {mp.id for mp in baseline_mps}

    external_id_by_match = dict(db.query(Match.id, Match.external_id).filter(Match.id.in_(match_ids)).all())
    lineup_by_match: dict[int, list[MatchPlayer]] = {}
    for mp in db.query(MatchPlayer).filter(MatchPlayer.match_id.in_(match_ids)).all():
        lineup_by_match.setdefault(mp.match_id, []).append(mp)

    stats: dict[int, _MatchStats] = {mp_id: _MatchStats() for mp_id in player_mps}

    for mp_id, kills, deaths, assists in (
        db.query(RoundPlayerStat.match_player_id, RoundPlayerStat.kills, RoundPlayerStat.deaths, RoundPlayerStat.assists)
        .filter(RoundPlayerStat.match_player_id.in_(player_mps))
        .all()
    ):
        s = stats[mp_id]
        s.kills += kills or 0
        s.deaths += deaths or 0
        s.assists += assists or 0
        if (kills or 0) >= 3:
            s.multi_kills[kills] = s.multi_kills.get(kills, 0) + 1

    for mp_id, impact, econ_kill, clutch_kill, post_plant_kill in (
        db.query(
            ImpactScore.match_player_id,
            ImpactScore.impact,
            ImpactScore.econ_kill,
            ImpactScore.clutch_kill,
            ImpactScore.post_plant_kill,
        )
        .filter(ImpactScore.match_player_id.in_(player_mps))
        .all()
    ):
        s = stats[mp_id]
        s.rounds += 1
        s.impact_sum += impact
        s.totals["econ_kill"] = s.totals.get("econ_kill", 0.0) + econ_kill
        s.totals["clutch_kill"] = s.totals.get("clutch_kill", 0.0) + clutch_kill
        s.totals["post_plant_kill"] = s.totals.get("post_plant_kill", 0.0) + post_plant_kill

    mp_by_match = {mp.match_id: mp for mp in player_mps.values()}
    rounds = (
        db.query(Round)
        .filter(Round.match_id.in_(match_ids))
        .options(selectinload(Round.kill_events))
        .all()
    )
    for round_row in rounds:
        player_mp = mp_by_match[round_row.match_id]
        team = _team(player_mp)
        lineup = lineup_by_match.get(round_row.match_id, [])
        own_ids = {mp.id for mp in lineup if _team(mp) == team}
        opp_ids = {mp.id for mp in lineup if _team(mp) != team}
        first_blood, first_death, alone_vs = _round_first_kill_and_clutch(round_row, player_mp.id, own_ids, opp_ids)
        s = stats[player_mp.id]
        s.totals["first_bloods"] = s.totals.get("first_bloods", 0) + int(first_blood)
        s.totals["first_deaths"] = s.totals.get("first_deaths", 0) + int(first_death)
        if alone_vs is not None and _winner_side(round_row.outcome) == team:
            s.clutches[alone_vs] = s.clutches.get(alone_vs, 0) + 1

    # Stand-out thresholds: the player's own top-20% per-round rate for each stat.
    thresholds: dict[str, float] = {}
    baseline = [stats[mp_id] for mp_id in baseline_mp_ids if stats[mp_id].rounds]
    if len(baseline) >= MIN_BASELINE_MATCHES:
        for stat in STANDOUT_STATS:
            thresholds[stat.key] = _quantile([s.totals.get(stat.key, 0) / s.rounds for s in baseline], STANDOUT_QUANTILE)

    friend_ids = list_friend_ids(db, player_id) - {player_id}
    friend_names = (
        dict(db.query(Player.id, Player.display_name).filter(Player.id.in_(friend_ids)).all()) if friend_ids else {}
    )

    mp_by_external_id = {external_id_by_match[mp.match_id]: mp for mp in player_mps.values()}
    rows = []
    for entry in entries:
        mp = mp_by_external_id.get(entry.external_id)
        if mp is None:
            rows.append(RecentMatchRow(entry=entry))
            continue
        s = stats[mp.id]
        badges: list[MatchBadge] = []
        friends = sorted(
            friend_names[other.player_id].split("#")[0]
            for other in lineup_by_match.get(mp.match_id, [])
            if other.player_id in friend_names and _team(other) == _team(mp)
        )
        party = PARTY_LABELS.get(len(friends) + 1)
        if party:
            badges.append(MatchBadge(party, "party", "Queued with " + ", ".join(friends)))
        for kills in sorted(s.multi_kills, reverse=True):
            count = s.multi_kills[kills]
            label = MULTI_KILL_LABELS.get(kills, f"{kills}K")
            badges.append(
                MatchBadge(
                    f"{count}× {label}" if count > 1 else label,
                    "event",
                    f"{count} round{'s' if count != 1 else ''} with {kills} kills",
                )
            )
        for vs in sorted(s.clutches, reverse=True):
            count = s.clutches[vs]
            badges.append(
                MatchBadge(
                    f"{count}× 1v{vs}" if count > 1 else f"1v{vs} clutch",
                    "event",
                    f"Won {count} round{'s' if count != 1 else ''} as the last one alive against {vs}",
                )
            )
        if s.rounds:
            for stat in STANDOUT_STATS:
                threshold = thresholds.get(stat.key)
                total = s.totals.get(stat.key, 0)
                if threshold is None or total < stat.min_total or total / s.rounds < threshold:
                    continue
                shown = int(total) if stat.key in ("first_bloods", "first_deaths") else f"{round(total):+d}"
                badges.append(
                    MatchBadge(
                        f"{stat.label} {shown}",
                        stat.kind,
                        f"{shown} {stat.description} -- in the top {round((1 - STANDOUT_QUANTILE) * 100)}% "
                        f"of this player's last {BASELINE_MATCHES} matches",
                    )
                )
        rows.append(
            RecentMatchRow(
                entry=entry,
                agent=mp.agent,
                kills=s.kills,
                deaths=s.deaths,
                assists=s.assists,
                average_impact=(s.impact_sum / s.rounds) if s.rounds else None,
                badges=badges,
            )
        )
    return rows
