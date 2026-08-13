from dataclasses import dataclass

from sqlalchemy.orm import Session, object_session, selectinload

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import Team
from app.scoring.impact import econ_tier_name
from app.services.player_graphs import NO_DATA_FILL, win_color

TIERS = ["SAVE", "ECO", "FORCE", "FULL_BUY"]
TIER_LABELS = {"SAVE": "Save", "ECO": "Eco", "FORCE": "Force", "FULL_BUY": "Full Buy"}
# Reuses the same red->green scale as round-win-rate coloring, applied across
# the tier order instead of a win percentage -- SAVE is "worst" (red), FULL_BUY
# "best" (green), so a buy-type badge reads with the same visual language as
# every win-rate graph elsewhere on the site.
TIER_COLOR = {tier: win_color(i / (len(TIERS) - 1)) for i, tier in enumerate(TIERS)}
_TIER_RANK = {tier: i for i, tier in enumerate(TIERS)}


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _preload_match(match: Match) -> None:
    db = object_session(match)
    if db is not None:
        db.query(Match).filter_by(id=match.id).options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.player_stats),
        ).one()


def _preload_matches(matches: list[Match]) -> None:
    if not matches:
        return
    db = object_session(matches[0])
    if db is not None:
        db.query(Match).filter(Match.id.in_([m.id for m in matches])).options(
            selectinload(Match.match_players),
            selectinload(Match.rounds).selectinload(Round.player_stats),
        ).all()


def _match_raw_econ_rounds(match: Match) -> dict[int, tuple[int, int, Team | None]]:
    """round_number -> (team1_loadout, team2_loadout, winner), summed from
    each round's per-player loadout. Rounds with no recorded loadout stats
    (e.g. not yet ingested) are omitted."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rows: dict[int, tuple[int, int, Team | None]] = {}
    for round_row in match.rounds:
        loadout = {Team.TEAM_1: 0, Team.TEAM_2: 0}
        has_stats = False
        for stat in round_row.player_stats:
            team = team_of.get(stat.match_player_id)
            if team is not None:
                loadout[team] += stat.loadout
                has_stats = True
        if not has_stats:
            continue
        rows[round_row.round_number] = (loadout[Team.TEAM_1], loadout[Team.TEAM_2], _winner_team(round_row.outcome))
    return rows


@dataclass
class EconRoundRow:
    round_number: int
    team1_loadout: int
    team2_loadout: int
    team1_tier_label: str
    team2_tier_label: str
    team1_tier_color: str
    team2_tier_color: str
    winner: Team | None


def _econ_round_row(round_number: int, team1_loadout: int, team2_loadout: int, winner: Team | None) -> EconRoundRow:
    team1_tier = econ_tier_name(team1_loadout)
    team2_tier = econ_tier_name(team2_loadout)
    return EconRoundRow(
        round_number=round_number,
        team1_loadout=team1_loadout,
        team2_loadout=team2_loadout,
        team1_tier_label=TIER_LABELS[team1_tier],
        team2_tier_label=TIER_LABELS[team2_tier],
        team1_tier_color=TIER_COLOR[team1_tier],
        team2_tier_color=TIER_COLOR[team2_tier],
        winner=winner,
    )


def match_econ_rounds(match: Match) -> dict[int, EconRoundRow]:
    """Per-round team loadouts/buy-tiers for one match, keyed by round_number
    -- what each team bought that round, for a raw round-by-round table."""
    _preload_match(match)
    return {
        round_number: _econ_round_row(round_number, team1_loadout, team2_loadout, winner)
        for round_number, (team1_loadout, team2_loadout, winner) in _match_raw_econ_rounds(match).items()
    }


@dataclass
class EconSample:
    own_tier: str
    enemy_tier: str
    own_won: bool


def _samples_from_raw(raw: dict[int, tuple[int, int, Team | None]], own_team: Team) -> list[EconSample]:
    samples = []
    for team1_loadout, team2_loadout, winner in raw.values():
        if winner is None:
            continue
        own_loadout, enemy_loadout = (
            (team1_loadout, team2_loadout) if own_team == Team.TEAM_1 else (team2_loadout, team1_loadout)
        )
        samples.append(
            EconSample(
                own_tier=econ_tier_name(own_loadout),
                enemy_tier=econ_tier_name(enemy_loadout),
                own_won=winner == own_team,
            )
        )
    return samples


def match_econ_samples(match: Match) -> tuple[list[EconSample], list[EconSample]]:
    """Team1-perspective and team2-perspective econ samples for one match."""
    _preload_match(match)
    raw = _match_raw_econ_rounds(match)
    return _samples_from_raw(raw, Team.TEAM_1), _samples_from_raw(raw, Team.TEAM_2)


def session_econ_samples(matches: list[Match], team_by_match: dict[int, str]) -> list[EconSample]:
    """Combined econ samples for a session's tracked team, across every match
    in the session -- matches with no resolvable side are skipped, same
    convention as build_session_round_win_diagram."""
    _preload_matches(matches)
    samples: list[EconSample] = []
    for match in matches:
        own_team_str = team_by_match.get(match.id)
        if own_team_str is None:
            continue
        raw = _match_raw_econ_rounds(match)
        samples.extend(_samples_from_raw(raw, Team(own_team_str)))
    return samples


def player_econ_samples(db: Session, player: Player, match_limit: int | None = None) -> list[EconSample]:
    """Econ samples across every round this player has played, from their own
    team's perspective each time -- aggregated the same way build_state_diagrams
    aggregates the kill-order/round-win diagrams."""
    query = (
        db.query(MatchPlayer)
        .filter_by(player_id=player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .options(
            selectinload(MatchPlayer.match).selectinload(Match.match_players),
            selectinload(MatchPlayer.match).selectinload(Match.rounds).selectinload(Round.player_stats),
        )
    )
    if match_limit is not None:
        query = query.order_by(Match.played_at.desc().nullsfirst(), Match.id.desc()).limit(match_limit)

    samples: list[EconSample] = []
    for mp in query.all():
        raw = _match_raw_econ_rounds(mp.match)
        samples.extend(_samples_from_raw(raw, mp.team))
    return samples


@dataclass
class TierMatrixCell:
    own_tier: str
    enemy_tier: str
    wins: int
    total: int
    win_pct: float | None
    fill: str


@dataclass
class TierMatrix:
    tiers: list[str]
    tier_labels: dict[str, str]
    cells: dict[tuple[str, str], TierMatrixCell]
    total_rounds: int


def build_tier_matrix(samples: list[EconSample]) -> TierMatrix:
    """Full own-tier x enemy-tier win-rate grid -- needs a lot of rounds to
    fill in all 16 cells, so this is only used where the sample is large
    (a player's whole match history), not a single match or session."""
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for s in samples:
        bucket = buckets.setdefault((s.own_tier, s.enemy_tier), {"win": 0, "total": 0})
        bucket["total"] += 1
        if s.own_won:
            bucket["win"] += 1

    cells: dict[tuple[str, str], TierMatrixCell] = {}
    for own_tier in TIERS:
        for enemy_tier in TIERS:
            bucket = buckets.get((own_tier, enemy_tier))
            total = bucket["total"] if bucket else 0
            win_pct = bucket["win"] / total if total else None
            cells[(own_tier, enemy_tier)] = TierMatrixCell(
                own_tier=own_tier,
                enemy_tier=enemy_tier,
                wins=bucket["win"] if bucket else 0,
                total=total,
                win_pct=win_pct,
                fill=win_color(win_pct) if win_pct is not None else NO_DATA_FILL,
            )
    return TierMatrix(tiers=TIERS, tier_labels=TIER_LABELS, cells=cells, total_rounds=len(samples))


# Coarser than the tier matrix: whether a team's buy out-ranked, matched, or
# under-ranked the enemy's, crossed with round outcome. Fits a single
# match's or session's round count without leaving cells empty the way the
# full 4x4 grid would.
FAVOR_LABELS = {
    "favored": "Favored (bought more)",
    "even": "Even",
    "unfavored": "Unfavored (bought less)",
}


def _favor_key(own_tier: str, enemy_tier: str) -> str:
    own_rank, enemy_rank = _TIER_RANK[own_tier], _TIER_RANK[enemy_tier]
    if own_rank > enemy_rank:
        return "favored"
    if own_rank < enemy_rank:
        return "unfavored"
    return "even"


@dataclass
class FavorOutcomeRow:
    key: str
    label: str
    wins: int
    losses: int
    total: int
    win_pct: float | None
    fill: str


@dataclass
class FavorOutcomeMatrix:
    rows: list[FavorOutcomeRow]
    total_rounds: int


def build_favor_outcome_matrix(samples: list[EconSample]) -> FavorOutcomeMatrix:
    buckets = {key: {"win": 0, "total": 0} for key in FAVOR_LABELS}
    for s in samples:
        bucket = buckets[_favor_key(s.own_tier, s.enemy_tier)]
        bucket["total"] += 1
        if s.own_won:
            bucket["win"] += 1

    rows = []
    for key, label in FAVOR_LABELS.items():
        bucket = buckets[key]
        total = bucket["total"]
        win_pct = bucket["win"] / total if total else None
        rows.append(
            FavorOutcomeRow(
                key=key,
                label=label,
                wins=bucket["win"],
                losses=total - bucket["win"],
                total=total,
                win_pct=win_pct,
                fill=win_color(win_pct) if win_pct is not None else NO_DATA_FILL,
            )
        )
    return FavorOutcomeMatrix(rows=rows, total_rounds=len(samples))
