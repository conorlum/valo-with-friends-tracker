import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Match, MatchPlayer, Player

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

SEASON_ACTS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "season_acts.json"

# A single match logged right at an act boundary (the previous act's last
# map bleeding a few minutes into the new act) shouldn't count as a real
# pool member -- see the Pearl example from the V26:A4 pool check.
MIN_POOL_MATCHES = 3


@dataclass
class MapPrediction:
    map_name: str
    probability: float


def _current_act_start(db: Session) -> datetime | None:
    if not SEASON_ACTS_PATH.exists():
        return None
    acts = json.loads(SEASON_ACTS_PATH.read_text(encoding="utf-8"))
    if not acts:
        return None
    latest = max(acts, key=lambda a: a["start_time"])
    return datetime.fromisoformat(latest["start_time"])


def get_current_map_pool(db: Session) -> dict[str, float]:
    """Current active map pool with popularity weights (share of matches
    played on that map, within the current Episode/Act only -- older acts'
    maps are a different pool and shouldn't dilute this)."""
    query = db.query(Match.map_name, func.count(Match.id)).filter(Match.played_at.isnot(None))
    start = _current_act_start(db)
    if start is not None:
        query = query.filter(Match.played_at >= start)
    rows = [(m, c) for m, c in query.group_by(Match.map_name).all() if c >= MIN_POOL_MATCHES]
    total = sum(c for _, c in rows)
    return {m: c / total for m, c in rows} if total else {}


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
    """Combines each map's current-act popularity with the two cooldown
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
