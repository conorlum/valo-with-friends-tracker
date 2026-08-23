from dataclasses import dataclass

from sqlalchemy.orm import Session, object_session, selectinload

from app.models import Match, MatchPlayer, Player, Round
from app.models.match import Team
from app.scoring.impact import econ_tier_name
from app.services.player_graphs import NO_DATA_FILL, win_color

TIER_LABELS = {"PISTOL": "Pistol", "ECO": "Eco", "FULL_BUY": "Full Buy"}
# Buy-quality tiers only -- PISTOL is a forced-reset round, not a quality
# choice on the same scale, so it's excluded from the ranked gradient below.
# econ_tier_name's SAVE, ECO, and FORCE are all folded together here: there
# wasn't enough round volume in a single match/session to fill a 3-tier grid
# (let alone 4), and the finer distinctions didn't track a meaningfully
# different decision anyway -- just eco vs. full buy.
_BUY_TIERS = ["ECO", "FULL_BUY"]
# Reuses the same red->green scale as round-win-rate coloring, applied across
# the buy-tier order instead of a win percentage -- ECO is "worst" (red),
# FULL_BUY "best" (green), so a buy-type badge reads with the same visual
# language as every win-rate graph elsewhere on the site.
TIER_COLOR = {tier: win_color(i / (len(_BUY_TIERS) - 1)) for i, tier in enumerate(_BUY_TIERS)}
TIER_COLOR["PISTOL"] = "#5b6bd6"

# Same pistol-round convention as compute_round_credit_events in
# app/scoring/credit_events.py: rounds 1 and 13 are the economy-reset rounds,
# regardless of what was actually spent.
PISTOL_ROUNDS = {1, 13}


def _tier_for(loadout: int, round_number: int) -> str:
    if round_number in PISTOL_ROUNDS:
        return "PISTOL"
    return "FULL_BUY" if econ_tier_name(loadout) == "FULL_BUY" else "ECO"


def _loadout_ratio(own_loadout: int, enemy_loadout: int) -> float | None:
    total = own_loadout + enemy_loadout
    return own_loadout / total if total else None


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
    """round_number -> (team1_loadout, team2_loadout, winner), averaged
    across each round's per-player loadout -- econ_tier_name's thresholds
    are calibrated against a single player's buy, so a team-total sum would
    push almost every round into FULL_BUY. Rounds with no recorded loadout
    stats (e.g. not yet ingested) are omitted."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rows: dict[int, tuple[int, int, Team | None]] = {}
    for round_row in match.rounds:
        loadout_sum = {Team.TEAM_1: 0, Team.TEAM_2: 0}
        player_count = {Team.TEAM_1: 0, Team.TEAM_2: 0}
        for stat in round_row.player_stats:
            team = team_of.get(stat.match_player_id)
            if team is not None:
                loadout_sum[team] += stat.loadout
                player_count[team] += 1
        if not player_count[Team.TEAM_1] and not player_count[Team.TEAM_2]:
            continue
        avg_loadout = {
            team: (loadout_sum[team] // player_count[team] if player_count[team] else 0)
            for team in (Team.TEAM_1, Team.TEAM_2)
        }
        rows[round_row.round_number] = (
            avg_loadout[Team.TEAM_1],
            avg_loadout[Team.TEAM_2],
            _winner_team(round_row.outcome),
        )
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
    team1_tier = _tier_for(team1_loadout, round_number)
    team2_tier = _tier_for(team2_loadout, round_number)
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
    """Per-round average-per-player team loadouts/buy-tiers for one match,
    keyed by round_number -- what each team bought that round, for a raw
    round-by-round table."""
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
    own_loadout: int
    enemy_loadout: int


def _samples_from_raw(raw: dict[int, tuple[int, int, Team | None]], own_team: Team) -> list[EconSample]:
    samples = []
    for round_number, (team1_loadout, team2_loadout, winner) in raw.items():
        if winner is None:
            continue
        own_loadout, enemy_loadout = (
            (team1_loadout, team2_loadout) if own_team == Team.TEAM_1 else (team2_loadout, team1_loadout)
        )
        samples.append(
            EconSample(
                own_tier=_tier_for(own_loadout, round_number),
                enemy_tier=_tier_for(enemy_loadout, round_number),
                own_won=winner == own_team,
                own_loadout=own_loadout,
                enemy_loadout=enemy_loadout,
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


def econ_samples_from_data(match_players: list[MatchPlayer]) -> list[EconSample]:
    """Same output as player_econ_samples, but sourced from
    app.services.player_data.load_player_match_data's shared hydration
    (Match.rounds.player_stats + Match.match_players, both already
    eager-loaded there since Step 2) instead of issuing its own query. Used
    by the player_view_cache write path so econ-sample building shares ONE
    load with profile + state-diagram + fight-EV instead of a second,
    independent replay."""
    samples: list[EconSample] = []
    for mp in match_players:
        raw = _match_raw_econ_rounds(mp.match)
        samples.extend(_samples_from_raw(raw, mp.team))
    return samples


def player_econ_samples(db: Session, player: Player, match_limit: int | None = None) -> list[EconSample]:
    """Live per-request path: issues its own query (with its own
    selectinloads) then delegates to econ_samples_from_data. No longer
    called on the player page's own request path post-Step-2 (econ now
    comes from the cache, or from econ_samples_from_data on a miss) -- kept
    as a standalone, independently-correct function; see get_player_profile's
    docstring for why."""
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

    return econ_samples_from_data(query.all())


@dataclass
class TierMatrixCell:
    own_tier: str
    enemy_tier: str
    wins: int
    total: int
    win_pct: float | None
    fill: str
    avg_loadout_ratio: float | None


@dataclass
class TierMatrix:
    tiers: list[str]
    tier_labels: dict[str, str]
    cells: dict[tuple[str, str], TierMatrixCell]
    total_rounds: int


def _tier_pair_buckets(samples: list[EconSample]) -> dict[tuple[str, str], dict[str, float]]:
    """(own_tier, enemy_tier) -> {win, total, ratio_sum, ratio_count}, pistol
    rounds excluded from either side. The canonical aggregate cached for
    Step 2 -- see app.services.economy_graphs.compute_econ_aggregates and
    docs/player_page_render_speed.txt 2a."""
    buckets: dict[tuple[str, str], dict[str, float]] = {}
    for s in samples:
        if s.own_tier == "PISTOL" or s.enemy_tier == "PISTOL":
            continue
        bucket = buckets.setdefault(
            (s.own_tier, s.enemy_tier), {"win": 0, "total": 0, "ratio_sum": 0.0, "ratio_count": 0}
        )
        bucket["total"] += 1
        if s.own_won:
            bucket["win"] += 1
        ratio = _loadout_ratio(s.own_loadout, s.enemy_loadout)
        if ratio is not None:
            bucket["ratio_sum"] += ratio
            bucket["ratio_count"] += 1
    return buckets


def build_tier_matrix_from_aggregates(tier_pairs: dict[tuple[str, str], dict[str, float]]) -> TierMatrix:
    """Presentation half of build_tier_matrix, taking the canonical
    (own_tier, enemy_tier) -> {win,total,ratio_sum,ratio_count} aggregate
    instead of raw samples -- what player_view_cache's decode calls, so a
    fill-color or layout change never needs a cache version bump (2a)."""
    cells: dict[tuple[str, str], TierMatrixCell] = {}
    total_rounds = 0
    for own_tier in _BUY_TIERS:
        for enemy_tier in _BUY_TIERS:
            bucket = tier_pairs.get((own_tier, enemy_tier))
            total = bucket["total"] if bucket else 0
            total_rounds += total
            win_pct = bucket["win"] / total if total else None
            avg_ratio = bucket["ratio_sum"] / bucket["ratio_count"] if bucket and bucket["ratio_count"] else None
            cells[(own_tier, enemy_tier)] = TierMatrixCell(
                own_tier=own_tier,
                enemy_tier=enemy_tier,
                wins=bucket["win"] if bucket else 0,
                total=total,
                win_pct=win_pct,
                fill=win_color(win_pct) if win_pct is not None else NO_DATA_FILL,
                avg_loadout_ratio=avg_ratio,
            )
    return TierMatrix(tiers=_BUY_TIERS, tier_labels=TIER_LABELS, cells=cells, total_rounds=total_rounds)


def build_tier_matrix(samples: list[EconSample]) -> TierMatrix:
    """Own-tier x enemy-tier win-rate grid over the ranked buy tiers (Eco/
    Force/Full Buy) -- needs a lot of rounds to fill in all 9 cells, so this
    is only used where the sample is large (a player's whole match history,
    or a multi-match session), not a single match.

    Pistol rounds are excluded: a pistol round is always PISTOL vs PISTOL
    (both teams reset on the same round number), so folding it into this
    grid would only ever populate one row/column and leave the rest of that
    row/column empty. build_pistol_stats covers pistol rounds separately.
    """
    return build_tier_matrix_from_aggregates(_tier_pair_buckets(samples))


@dataclass
class PistolStats:
    wins: int
    losses: int
    total: int
    win_pct: float | None
    fill: str
    avg_loadout_ratio: float | None


def _pistol_bucket(samples: list[EconSample]) -> dict[str, float]:
    """{win, total, ratio_sum, ratio_count} over just the PISTOL-tier
    samples. The canonical aggregate cached for Step 2 (2a)."""
    pistol_samples = [s for s in samples if s.own_tier == "PISTOL"]
    total = len(pistol_samples)
    wins = sum(1 for s in pistol_samples if s.own_won)
    ratios = [
        r for r in (_loadout_ratio(s.own_loadout, s.enemy_loadout) for s in pistol_samples) if r is not None
    ]
    return {"win": wins, "total": total, "ratio_sum": sum(ratios), "ratio_count": len(ratios)}


def build_pistol_stats_from_aggregates(pistol: dict[str, float]) -> PistolStats:
    """Presentation half of build_pistol_stats, taking the canonical
    {win,total,ratio_sum,ratio_count} aggregate instead of raw samples."""
    total = pistol["total"]
    wins = pistol["win"]
    win_pct = wins / total if total else None
    avg_ratio = pistol["ratio_sum"] / pistol["ratio_count"] if pistol["ratio_count"] else None
    return PistolStats(
        wins=wins,
        losses=total - wins,
        total=total,
        win_pct=win_pct,
        fill=win_color(win_pct) if win_pct is not None else NO_DATA_FILL,
        avg_loadout_ratio=avg_ratio,
    )


def build_pistol_stats(samples: list[EconSample]) -> PistolStats:
    """Pistol-round win rate/loadout share, pulled out of the tier matrix
    grid above since pistol rounds don't vary by buy tier."""
    return build_pistol_stats_from_aggregates(_pistol_bucket(samples))


@dataclass
class PistolMatchStats:
    """Presentation for "pistol round win -> match win" and "won both
    pistols -> match win", built from app.services.player_profile_types.
    compute_pistol_match_stats's canonical aggregate."""

    single_wins: int
    single_total: int
    single_win_pct: float | None
    single_fill: str
    double_wins: int
    double_total: int
    double_win_pct: float | None
    double_fill: str


def build_pistol_match_stats_from_aggregates(agg: dict[str, int]) -> PistolMatchStats:
    single_total, single_wins = agg["single_total"], agg["single_wins"]
    double_total, double_wins = agg["double_total"], agg["double_wins"]
    single_pct = single_wins / single_total if single_total else None
    double_pct = double_wins / double_total if double_total else None
    return PistolMatchStats(
        single_wins=single_wins, single_total=single_total, single_win_pct=single_pct,
        single_fill=win_color(single_pct) if single_pct is not None else NO_DATA_FILL,
        double_wins=double_wins, double_total=double_total, double_win_pct=double_pct,
        double_fill=win_color(double_pct) if double_pct is not None else NO_DATA_FILL,
    )


# Fixed pixel layout for the chart below -- unlike the diamond state diagrams
# elsewhere, this has a real fixed x/y axis pair, so the geometry is
# hardcoded once here rather than computed per dataset.
_SCATTER_WIDTH = 960
_SCATTER_HEIGHT = 320
_SCATTER_PLOT_LEFT = 56
_SCATTER_PLOT_RIGHT = _SCATTER_WIDTH - 24
_SCATTER_PLOT_TOP = 20
_SCATTER_PLOT_BOTTOM = _SCATTER_HEIGHT - 44

# Own buy share is a near-continuous ratio of two credit totals, so grouping
# by its exact value would put ~1 round in almost every group -- not enough
# to average. 5-point-wide buckets (20 across the 0-100% range) are coarse
# enough that a full player history (30 matches, ~13-20+ rounds each, so
# 400-600+ rounds) puts a meaningful sample behind most buckets. A session's
# handful of matches doesn't clear that bar, so callers with too little data
# (sessions) skip this chart entirely rather than rendering a noisy one.
_BUCKET_WIDTH_PCT = 5
_NUM_BUCKETS = 100 // _BUCKET_WIDTH_PCT
_MIN_POINT_RADIUS = 2.0
_MAX_POINT_RADIUS = 16.0


@dataclass
class BuyBucketPoint:
    cx: float
    cy: float
    r: float
    fill: str
    title: str


@dataclass
class AxisTick:
    pos: float
    label: str


@dataclass
class LoadoutWinScatter:
    points: list[BuyBucketPoint]
    line_path: str
    x_ticks: list[AxisTick]
    y_ticks: list[AxisTick]
    reference_line_y: float
    view_box: str
    plot_left: float
    plot_right: float
    plot_top: float
    plot_bottom: float
    total_rounds: int


def _bucket_index(ratio: float) -> int:
    return min(int(ratio * 100 // _BUCKET_WIDTH_PCT), _NUM_BUCKETS - 1)


def _loadout_win_buckets(samples: list[EconSample]) -> dict[int, dict[str, int]]:
    """bucket_index -> {win, total}, pistol rounds excluded. The canonical
    aggregate cached for Step 2 (2a)."""
    buckets: dict[int, dict[str, int]] = {}
    for s in samples:
        if s.own_tier == "PISTOL":
            continue
        ratio = _loadout_ratio(s.own_loadout, s.enemy_loadout)
        if ratio is None:
            continue
        bucket = buckets.setdefault(_bucket_index(ratio), {"win": 0, "total": 0})
        bucket["total"] += 1
        if s.own_won:
            bucket["win"] += 1
    return buckets


def build_loadout_win_scatter_from_aggregates(loadout_buckets: dict[int, dict[str, int]]) -> LoadoutWinScatter:
    """Presentation half of build_loadout_win_scatter, taking the canonical
    bucket_index -> {win,total} aggregate instead of raw samples -- what
    player_view_cache's decode calls, so the fixed pixel layout/colors/ticks
    never need a cache version bump (2a)."""

    def x_for(pct: float) -> float:
        return _SCATTER_PLOT_LEFT + pct / 100 * (_SCATTER_PLOT_RIGHT - _SCATTER_PLOT_LEFT)

    def y_for(win_pct: float) -> float:
        return _SCATTER_PLOT_BOTTOM - win_pct * (_SCATTER_PLOT_BOTTOM - _SCATTER_PLOT_TOP)

    buckets = loadout_buckets
    max_total = max((b["total"] for b in buckets.values()), default=0)
    points: list[BuyBucketPoint] = []
    line_coords: list[str] = []
    total_rounds = 0
    for idx in range(_NUM_BUCKETS):
        bucket = buckets.get(idx)
        if not bucket:
            continue
        total = bucket["total"]
        total_rounds += total
        win_pct = bucket["win"] / total
        cx = x_for(idx * _BUCKET_WIDTH_PCT + _BUCKET_WIDTH_PCT / 2)
        cy = y_for(win_pct)
        radius = _MIN_POINT_RADIUS + (_MAX_POINT_RADIUS - _MIN_POINT_RADIUS) * (total / max_total)
        low, high = idx * _BUCKET_WIDTH_PCT, (idx + 1) * _BUCKET_WIDTH_PCT
        points.append(
            BuyBucketPoint(
                cx=cx,
                cy=cy,
                r=radius,
                fill=win_color(win_pct),
                title=f"{low}-{high}% of buy -- {bucket['win']}/{total} rounds won ({round(win_pct * 100)}%)",
            )
        )
        line_coords.append(f"{cx},{cy}")

    x_ticks = [AxisTick(pos=x_for(pct), label=f"{pct}%") for pct in (0, 20, 40, 60, 80, 100)]
    y_ticks = [AxisTick(pos=y_for(pct / 100), label=f"{pct}%") for pct in (0, 25, 50, 75, 100)]

    return LoadoutWinScatter(
        points=points,
        line_path=("M " + " L ".join(line_coords)) if len(line_coords) > 1 else "",
        x_ticks=x_ticks,
        y_ticks=y_ticks,
        reference_line_y=y_for(0.5),
        view_box=f"0 0 {_SCATTER_WIDTH} {_SCATTER_HEIGHT}",
        plot_left=_SCATTER_PLOT_LEFT,
        plot_right=_SCATTER_PLOT_RIGHT,
        plot_top=_SCATTER_PLOT_TOP,
        plot_bottom=_SCATTER_PLOT_BOTTOM,
        total_rounds=total_rounds,
    )


def build_loadout_win_scatter(samples: list[EconSample]) -> LoadoutWinScatter:
    """Win rate by own buy share, bucketed into 5-point-wide bins (0-5%,
    5-10%, ..., 95-100%) instead of plotted per round -- own buy share is a
    near-continuous ratio, so an exact-value grouping would mostly be groups
    of one, and the resulting "average" would just restate each round's own
    win/loss. Point radius scales with each bucket's round count so a
    bucket's position reads with the confidence its sample size deserves.
    Pistol rounds are excluded, same rationale as the tier matrix: buy tier
    doesn't meaningfully apply to a forced-reset round.
    """
    return build_loadout_win_scatter_from_aggregates(_loadout_win_buckets(samples))


def compute_econ_aggregates(samples: list[EconSample]) -> dict:
    """The three canonical econ aggregates Step 2 caches, computed together
    from one samples list -- tier_pairs/pistol/loadout_buckets, each build_*
    _from_aggregates function's counterpart above. Presentation (colors,
    radii, SVG paths, ticks, view box) is never part of this -- see 2a."""
    return {
        "tier_pairs": _tier_pair_buckets(samples),
        "pistol": _pistol_bucket(samples),
        "loadout_buckets": _loadout_win_buckets(samples),
    }
