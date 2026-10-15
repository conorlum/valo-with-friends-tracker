from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer, Player
from app.services import map_pool
from app.services.map_pool import load_pool_history

# See scripts/analyze_map_diversity.py + scripts/analyze_session_map_patterns.py
# + scripts/inspect_session_repeats.py for how these were derived: there is no
# hard "can't repeat a map" rule and no multi-game exclusion window. There are
# two distinct, differently-sized soft cooldowns rather than one compounding
# curve (an earlier version of this fit a steep per-slot exponent from just
# two aggregate numbers -- checking the *exact* shared-player count behind
# those numbers showed it doesn't keep compounding from 3 to 5 shared
# players, so that was an overcorrection):
#   - one KNOWN player's own last map: ~0.47x (rate_gap1=6.3% vs
#     rate_gap2plus=13.3%, ~1144 observations -- large, solid sample)
#   - 2+ known players sharing that last map (they very likely just played
#     it together as a group): ~0.2x (2.47% within-session repeat rate vs
#     ~13% baseline; only 243 samples pooled across 3/4/5-shared, and the
#     3-vs-5-shared rates were statistically indistinguishable from each
#     other -- so this is one flat tier, not scaled further by exact count)
INDIVIDUAL_COOLDOWN_WEIGHT = 0.47
GROUP_COOLDOWN_WEIGHT = 0.2
# Right after a pool change the game mostly gives a map that just entered (the owner's experience, D7;
# not fitted). Each such map starts at this many matches, every other pool map at 1, and real matches
# since the change take over as they come in.
NEW_MAP_PRIOR = 5


@dataclass
class MapPrediction:
    map_name: str
    probability: float


def get_current_map_pool(db: Session) -> dict[str, float]:
    """The current map pool (app.services.map_pool) with popularity weights: each pool map's share of
    our matches since the current pool began (the latest official pool change), each map starting at 1
    match and each map that entered at that change at NEW_MAP_PRIOR."""
    history = load_pool_history(db)
    pool = history.current_pool()
    if not pool:
        return {}
    query = db.query(Match.map_name, func.count(Match.id)).filter(Match.played_at.isnot(None))
    start = history.latest_change_at(official_only=True)
    if start is not None:
        query = query.filter(Match.played_at >= start)
    counts = {m: c for m, c in query.group_by(Match.map_name).all() if m in pool}
    new_maps = {
        c.map_name
        for c in history.changes
        if c.in_pool and c.effective_at == start and c.source != map_pool.SAFETY_NET_SOURCE
    }
    smoothed = {m: counts.get(m, 0) + (NEW_MAP_PRIOR if m in new_maps else 1) for m in sorted(pool)}
    total = sum(smoothed.values())
    return {m: c / total for m, c in smoothed.items()}


def get_player_last_map(db: Session, player_id: int) -> str | None:
    row = (
        db.query(Match.map_name)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .filter(MatchPlayer.player_id == player_id, Match.played_at.isnot(None))
        .order_by(Match.played_at.desc())
        .first()
    )
    return row[0] if row else None


def _population_last_map_rates(db: Session) -> dict[str, float]:
    """Each map's share of ALL players' own most recent match -- stands in
    for "what did this unknown opponent probably just play" when
    marginalizing the lobby slots we don't have a known player for."""
    ranked = (
        db.query(
            MatchPlayer.player_id,
            Match.map_name,
            func.row_number()
            .over(partition_by=MatchPlayer.player_id, order_by=Match.played_at.desc())
            .label("rn"),
        )
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(Match.played_at.isnot(None))
        .subquery()
    )
    rows = db.query(ranked.c.map_name, func.count()).filter(ranked.c.rn == 1).group_by(ranked.c.map_name).all()
    total = sum(c for _, c in rows)
    return {m: c / total for m, c in rows} if total else {}


def predict_map_probabilities(
    db: Session, known_player_ids: list[int], lobby_size: int = 10
) -> list[MapPrediction]:
    """Combines each map's current-pool popularity with the two cooldown
    tiers found during fitting (see the constants above): a map that 2+
    known players share as their last game gets the stronger GROUP
    discount; a map only one known player just played gets the milder
    INDIVIDUAL discount. Unknown lobby slots are marginalized with
    population-wide "last map played" rates, treated individually since we
    have no way to know whether the unknown opponents were a group too."""
    pool = get_current_map_pool(db)
    if not pool:
        return []

    known_last_maps = Counter(m for m in (get_player_last_map(db, pid) for pid in known_player_ids) if m)
    population_last_map = _population_last_map_rates(db)
    unknown_count = max(lobby_size - len(known_player_ids), 0)

    weights: dict[str, float] = {}
    for map_name, popularity in pool.items():
        known_count = known_last_maps.get(map_name, 0)
        if known_count >= 2:
            multiplier = GROUP_COOLDOWN_WEIGHT
        elif known_count == 1:
            multiplier = INDIVIDUAL_COOLDOWN_WEIGHT
        else:
            multiplier = 1.0
        if unknown_count:
            p_last = population_last_map.get(map_name, 0.0)
            # Expected value of the per-slot discount-if-flagged factor for
            # one unknown slot, raised to unknown_count independent slots.
            multiplier *= (1 - p_last * (1 - INDIVIDUAL_COOLDOWN_WEIGHT)) ** unknown_count
        weights[map_name] = popularity * multiplier

    total = sum(weights.values())
    if total == 0:
        return []
    predictions = [MapPrediction(map_name=m, probability=w / total) for m, w in weights.items()]
    predictions.sort(key=lambda p: p.probability, reverse=True)
    return predictions
