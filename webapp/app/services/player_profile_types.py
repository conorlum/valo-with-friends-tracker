"""Shared PlayerProfile/MatchBreakdown/GroupedStat output shapes, plus two
pure builders: build_profile_from_precomputed (the per-match-breakdown loop,
given four precomputed lookup dicts) and build_player_profile_from_match_data
(derives those four dicts from load_player_match_data's already-hydrated
relationships instead of querying for them). NEUTRAL LEAF: imports
app.models and nothing else from app.services, so app.services.players
(live per-request queries), app.services.player_views (the cache write
path, per docs/player_page_render_speed.txt Step 2) and
app.services.player_view_cache (decode) can all import from here without an
import cycle -- the same role app.services.player_data plays for the
state-diagram/fight-EV leaf load. build_player_profile_from_match_data
specifically lives here rather than in players.py because players.py
imports player_view_cache.py (Step 1), which imports player_views.py --
a call from player_views.py back into players.py would cycle.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from app.models import ImpactScore, MatchPlayer, Player


@dataclass
class CachedMatchRef:
    """Lightweight stand-in for a live Match ORM object, decoded from a
    cached match_summary row. Exposes exactly what match_label()
    (app/templates.py) and the profile templates read off `m.match`: nothing
    else, since anything else would need its own cache-invalidation input
    (see docs/player_page_render_speed.txt 4.2)."""

    external_id: str
    map_name: str | None
    played_at: datetime | None
    team1_rounds_won: int
    team2_rounds_won: int


@dataclass
class MatchBreakdown:
    match: object  # a live app.models.Match OR a CachedMatchRef -- duck-typed on purpose
    agent: str
    team: str
    average_impact: float
    average_kill_impact: float
    average_death_impact: float
    win: bool | None
    kills: int
    deaths: int
    assists: int


@dataclass
class GroupedStat:
    """Match-level win/loss + impact breakdown grouped by some key (an agent
    name or a map name)."""

    key: str
    matches_played: int
    wins: int
    losses: int
    win_rate: float | None
    average_impact: float
    average_kill_impact: float
    average_death_impact: float


@dataclass
class PlayerProfile:
    player: Player
    overall_average_impact: float
    overall_average_round_win_impact: float
    overall_average_death_impact: float
    matches: list[MatchBreakdown]
    agent_counts: Counter = field(default_factory=Counter)
    agent_stats: list[GroupedStat] = field(default_factory=list)
    map_stats: list[GroupedStat] = field(default_factory=list)
    avg_econ_kill: float = 0.0
    avg_econ_death: float = 0.0
    avg_clutch_kill: float = 0.0
    avg_clutch_death: float = 0.0
    avg_post_plant_kill: float = 0.0
    avg_post_plant_death: float = 0.0
    avg_traded_teammate: float = 0.0
    avg_traded_by_teammate: float = 0.0
    top_traded_teammate: list[tuple[str, int]] = field(default_factory=list)
    top_traded_by_teammate: list[tuple[str, int]] = field(default_factory=list)


def match_win(match, team: str) -> bool | None:
    """None for a tie (or missing round data) -- excluded from win-rate math
    rather than counted as a loss. `match` is duck-typed: a live Match or a
    CachedMatchRef, both expose team1_rounds_won/team2_rounds_won."""
    if match.team1_rounds_won == match.team2_rounds_won:
        return None
    team1_won = match.team1_rounds_won > match.team2_rounds_won
    return team1_won if team == "team-1" else not team1_won


def _winner_side(outcome: str | None) -> str | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return "team-1"
    if outcome.startswith("Team B"):
        return "team-2"
    return None


# Same pistol-round convention as app.services.economy_graphs.PISTOL_ROUNDS and
# app.scoring.credit_events.compute_round_credit_events: rounds 1 and 13 are
# the economy-reset rounds, regardless of match format.
PISTOL_ROUND_NUMBERS = (1, 13)


def compute_pistol_match_stats(match_players: list[MatchPlayer]) -> dict[str, int]:
    """Canonical aggregate behind the "pistols won -> match win" stat --
    three MUTUALLY EXCLUSIVE buckets by how many of the two pistol rounds
    (round 1 and round 13) this player's team won in a match: lost_both (0),
    won_one (1), won_both (2). Each bucket is {*_total, *_wins}, tallying
    whether the match itself was won.

    A match only lands in a bucket when BOTH pistol rounds have a resolved
    outcome -- unlike counting each pistol round independently, a single
    missing/unparseable outcome makes the bucket AMBIGUOUS (not just
    incomplete) since there are only two rounds to place, so the whole match
    is excluded rather than guessed at. Ties (match_win returns None) are
    excluded from every denominator too, same as match_win's own contract
    elsewhere in this module."""
    buckets = {n: {"total": 0, "wins": 0} for n in range(len(PISTOL_ROUND_NUMBERS) + 1)}
    for mp in match_players:
        match = mp.match
        team = mp.team.value if hasattr(mp.team, "value") else mp.team
        won_match = match_win(match, team)
        if won_match is None:
            continue

        rounds_by_number = {r.round_number: r for r in match.rounds}
        pistol_results = []
        for round_number in PISTOL_ROUND_NUMBERS:
            r = rounds_by_number.get(round_number)
            winner = _winner_side(r.outcome) if r is not None else None
            if winner is None:
                pistol_results = None
                break
            pistol_results.append(winner == team)
        if pistol_results is None:
            continue

        bucket = buckets[sum(pistol_results)]
        bucket["total"] += 1
        if won_match:
            bucket["wins"] += 1

    return {
        "lost_both_total": buckets[0]["total"], "lost_both_wins": buckets[0]["wins"],
        "won_one_total": buckets[1]["total"], "won_one_wins": buckets[1]["wins"],
        "won_both_total": buckets[2]["total"], "won_both_wins": buckets[2]["wins"],
    }


def grouped_stats(matches: list[MatchBreakdown], key_fn) -> list[GroupedStat]:
    groups: dict[str, dict] = {}
    for m in matches:
        key = key_fn(m)
        if key is None:
            continue
        g = groups.setdefault(
            key, {"count": 0, "wins": 0, "losses": 0, "impact": 0.0, "kill": 0.0, "death": 0.0}
        )
        g["count"] += 1
        if m.win is True:
            g["wins"] += 1
        elif m.win is False:
            g["losses"] += 1
        g["impact"] += m.average_impact
        g["kill"] += m.average_kill_impact
        g["death"] += m.average_death_impact

    stats = []
    for key, g in groups.items():
        decided = g["wins"] + g["losses"]
        stats.append(
            GroupedStat(
                key=key,
                matches_played=g["count"],
                wins=g["wins"],
                losses=g["losses"],
                win_rate=(g["wins"] / decided) if decided else None,
                average_impact=g["impact"] / g["count"],
                average_kill_impact=g["kill"] / g["count"],
                average_death_impact=g["death"] / g["count"],
            )
        )
    stats.sort(key=lambda s: s.matches_played, reverse=True)
    return stats


def build_profile_from_precomputed(
    player: Player,
    match_players: list[MatchPlayer],
    scores_by_match_player: dict[int, list],
    kda_by_match_player: dict[int, tuple[int, int, int]],
    teammates_by_match: dict[int, list[MatchPlayer]],
    round_outcome_by_id: dict[int, str | None],
) -> PlayerProfile:
    """The per-match-breakdown loop shared by app.services.players.
    get_player_profile (which builds the four lookup dicts via its own live
    queries) and the player_view_cache write path (which derives them from
    load_player_match_data's already-hydrated relationships instead, see
    Step 2b) -- identical output either way, just a different source for the
    four inputs. `match_players` order determines `PlayerProfile.matches`
    order; callers must pass oldest-first to match get_player_profile's
    existing convention (recent_first_matches = reversed(profile.matches) in
    the router)."""
    matches: list[MatchBreakdown] = []
    all_impacts: list[float] = []
    all_round_win_impacts: list[float] = []
    all_death_impacts: list[float] = []
    agent_counts: Counter = Counter()

    total_econ_kill = 0.0
    total_econ_death = 0.0
    total_clutch_kill = 0.0
    total_clutch_death = 0.0
    total_post_plant_kill = 0.0
    total_post_plant_death = 0.0
    total_traded_teammate = 0
    total_traded_by_teammate = 0
    traded_teammate_totals: dict[str, int] = {}
    traded_by_teammate_totals: dict[str, int] = {}

    for match_player in match_players:
        scores = scores_by_match_player.get(match_player.id, [])
        if not scores:
            continue

        impacts = [score.impact for score in scores]
        kill_impacts = [score.kill_impact for score in scores]
        death_impacts = [score.death_impact for score in scores]

        match = match_player.match
        team = match_player.team.value if hasattr(match_player.team, "value") else match_player.team
        # Only counts a round's kill_impact if this player's team actually won that round --
        # death_impact still counts regardless. See app.services.matches's
        # average_round_win_impact for the match-page counterpart.
        round_win_impacts = [
            (score.kill_impact if _winner_side(round_outcome_by_id.get(score.round_id)) == team else 0.0)
            - score.death_impact
            for score in scores
        ]
        kills, deaths, assists = kda_by_match_player.get(match_player.id, (0, 0, 0))
        matches.append(
            MatchBreakdown(
                match=match,
                agent=match_player.agent,
                team=team,
                average_impact=sum(impacts) / len(impacts),
                average_kill_impact=sum(kill_impacts) / len(kill_impacts),
                average_death_impact=sum(death_impacts) / len(death_impacts),
                win=match_win(match, team),
                kills=kills,
                deaths=deaths,
                assists=assists,
            )
        )
        all_impacts.extend(impacts)
        all_round_win_impacts.extend(round_win_impacts)
        all_death_impacts.extend(death_impacts)
        agent_counts[match_player.agent] += 1

        teammate_names = {
            mp.id: mp.player.display_name
            for mp in teammates_by_match.get(match_player.match_id, [])
        }

        for score in scores:
            breakdown = score.breakdown or {}
            total_econ_kill += breakdown.get("econ_kill", 0)
            total_econ_death += breakdown.get("econ_death", 0)
            total_clutch_kill += breakdown.get("clutch_kill", 0)
            total_clutch_death += breakdown.get("clutch_death", 0)
            total_post_plant_kill += breakdown.get("post_plant_kill", 0)
            total_post_plant_death += breakdown.get("post_plant_death", 0)
            total_traded_teammate += breakdown.get("traded_teammate", 0)
            total_traded_by_teammate += breakdown.get("traded_by_teammate", 0)
            for teammate_id, count in breakdown.get("traded_teammate_targets", {}).items():
                name = teammate_names.get(int(teammate_id))
                if name:
                    traded_teammate_totals[name] = traded_teammate_totals.get(name, 0) + count
            for teammate_id, count in breakdown.get("traded_by_teammate_sources", {}).items():
                name = teammate_names.get(int(teammate_id))
                if name:
                    traded_by_teammate_totals[name] = traded_by_teammate_totals.get(name, 0) + count

    overall_average = sum(all_impacts) / len(all_impacts) if all_impacts else 0.0
    overall_average_round_win_impact = (
        sum(all_round_win_impacts) / len(all_round_win_impacts) if all_round_win_impacts else 0.0
    )
    overall_average_death_impact = (
        sum(all_death_impacts) / len(all_death_impacts) if all_death_impacts else 0.0
    )
    matches_played = len(matches)

    def _avg(total: float) -> float:
        return total / matches_played if matches_played else 0.0

    def _top4(totals: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)[:4]

    agent_stats = grouped_stats(matches, lambda m: m.agent)
    map_stats = grouped_stats(matches, lambda m: m.match.map_name)
    # Win rate first (undecided-only maps sink to the bottom via the -1 sentinel,
    # since win_rate is always in [0, 1]), matches played as the tiebreak among
    # maps with identical win rates. grouped_stats' own default sort
    # (matches-played-only) stays as-is for agent_stats -- this override is
    # map_stats-specific.
    map_stats.sort(key=lambda s: (s.win_rate if s.win_rate is not None else -1, s.matches_played), reverse=True)

    return PlayerProfile(
        player=player,
        overall_average_impact=overall_average,
        overall_average_round_win_impact=overall_average_round_win_impact,
        overall_average_death_impact=overall_average_death_impact,
        matches=matches,
        agent_counts=agent_counts,
        agent_stats=agent_stats,
        map_stats=map_stats,
        avg_econ_kill=_avg(total_econ_kill),
        avg_econ_death=_avg(total_econ_death),
        avg_clutch_kill=_avg(total_clutch_kill),
        avg_clutch_death=_avg(total_clutch_death),
        avg_post_plant_kill=_avg(total_post_plant_kill),
        avg_post_plant_death=_avg(total_post_plant_death),
        avg_traded_teammate=_avg(total_traded_teammate),
        avg_traded_by_teammate=_avg(total_traded_by_teammate),
        top_traded_teammate=_top4(traded_teammate_totals),
        top_traded_by_teammate=_top4(traded_by_teammate_totals),
    )


def build_player_profile_from_match_data(
    player: Player, match_players: list[MatchPlayer], scores_by_match_player: dict[int, list[ImpactScore]]
) -> PlayerProfile:
    """Same output as app.services.players.get_player_profile, but sourced
    from app.services.player_data.load_player_match_data's shared hydration
    (Round.player_stats and Match.match_players.player both eager-loaded,
    see Step 2b) instead of issuing 3 more independent queries for
    KDA/teammates/round-outcomes. Called by app.services.player_views'
    write-path orchestration (not app.services.players -- that module
    imports app.services.player_view_cache, which imports player_views, so
    a call from there back into players.py would cycle) so profile-building
    shares ONE load with econ + state-diagram + fight-EV instead of a
    second, independent replay.

    `match_players` must be oldest-first (get_player_profile's own
    convention -- load_player_match_data itself returns newest-first, so
    callers must reverse the slice they pass in)."""
    kda_by_match_player: dict[int, tuple[int, int, int]] = {}
    teammates_by_match: dict[int, list[MatchPlayer]] = {}
    round_outcome_by_id: dict[int, str | None] = {}

    matches_seen: set[int] = set()
    for mp in match_players:
        match = mp.match
        if match.id not in matches_seen:
            matches_seen.add(match.id)
            teammates_by_match[match.id] = match.match_players
            for r in match.rounds:
                round_outcome_by_id[r.id] = r.outcome

        kills = deaths = assists = 0
        for r in match.rounds:
            for stat in r.player_stats:
                if stat.match_player_id == mp.id:
                    kills += stat.kills
                    deaths += stat.deaths
                    assists += stat.assists
        kda_by_match_player[mp.id] = (kills, deaths, assists)

    return build_profile_from_precomputed(
        player, match_players, scores_by_match_player, kda_by_match_player,
        teammates_by_match, round_outcome_by_id,
    )
