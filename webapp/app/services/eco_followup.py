""""After winning a pistol round, what should the team buy next round?" --
buckets the WINNING team's total loadout (sum of all 5 players' loadout
value) in the round right after a won pistol round (round 2 after round 1,
round 14 after round 13) against three outcomes:

  - did the team also win that immediate follow-up round
  - average kills per round over the following 4 rounds (2-5 / 14-17)
  - average round-win rate over that same 4-round window

A match contributes 0, 1, or 2 samples (one per pistol round that both had a
decisive winner AND has a recorded follow-up round). "friends" scope only
counts a sample when the pistol-winning team included a tracked roster
player; "all" scope counts every decisive pistol round in the DB. Both are
computed together in one pass over the same loaded matches -- see
app.services.site_stats.compute_pistol_win_followup_eco's caller.
"""

from dataclasses import dataclass

from app.models import Match
from app.models.match import Team
from app.services.player_graphs import win_color

# Chosen from the observed distribution (2026-08-23 snapshot of this DB): team
# total loadout the round right after a won pistol round ranged 5700-20400,
# median ~14600, with the bulk between 10000 and 19000. 1000-credit-wide
# buckets from 5000-22000 comfortably cover that with room to spare, and are
# coarse enough to keep most buckets statistically meaningful (a handful of
# samples each) rather than shattering into one-round buckets.
ECO_BUCKET_FLOOR = 5000
ECO_BUCKET_WIDTH = 1000
ECO_NUM_BUCKETS = 17  # 5000-22000

PISTOL_FOLLOWUP_ROUNDS = ((1, 2), (13, 14))
FOLLOWUP_WINDOW = 4

# A bucket needs at least this many samples before it's eligible to be
# highlighted as the "optimal" buy -- otherwise a handful of rounds in a
# sparse tail bucket could claim a 100% rate off pure noise.
MIN_SAMPLES_FOR_BEST = 20


def eco_bucket_index(total_loadout: int) -> int:
    idx = (total_loadout - ECO_BUCKET_FLOOR) // ECO_BUCKET_WIDTH
    return min(max(int(idx), 0), ECO_NUM_BUCKETS - 1)


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _pistol_win_followup_samples(match: Match) -> list[tuple[Team, int, bool, float, float]]:
    """(winning_team, followup_total_loadout, won_followup_round, avg_kills_per_round,
    avg_win_rate) for each pistol round (1, 13) in `match` that had a decisive
    winner and a recorded follow-up round. avg_kills_per_round/avg_win_rate are
    averaged over whatever of the next FOLLOWUP_WINDOW rounds actually exist --
    most matches have all 4, but one ending early (e.g. 13-3) may have fewer,
    so a raw sum would understate a short match's rate rather than reflect it."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rounds_by_number = {r.round_number: r for r in match.rounds}

    samples: list[tuple[Team, int, bool, float, float]] = []
    for pistol_rn, followup_start in PISTOL_FOLLOWUP_ROUNDS:
        pistol_round = rounds_by_number.get(pistol_rn)
        if pistol_round is None:
            continue
        winner = _winner_team(pistol_round.outcome)
        if winner is None:
            continue

        followup = rounds_by_number.get(followup_start)
        if followup is None:
            continue
        total_loadout = sum(
            stat.loadout for stat in followup.player_stats if team_of.get(stat.match_player_id) == winner
        )
        if total_loadout <= 0:
            continue  # no recorded loadout stats for this round

        followup_winner = _winner_team(followup.outcome)
        if followup_winner is None:
            continue
        won_followup = followup_winner == winner

        kills_total = 0
        wins_total = 0
        rounds_available = 0
        for rn in range(followup_start, followup_start + FOLLOWUP_WINDOW):
            r = rounds_by_number.get(rn)
            if r is None:
                continue
            rounds_available += 1
            kills_total += sum(
                stat.kills for stat in r.player_stats if team_of.get(stat.match_player_id) == winner
            )
            if _winner_team(r.outcome) == winner:
                wins_total += 1
        if rounds_available == 0:
            continue

        samples.append((winner, total_loadout, won_followup, kills_total / rounds_available, wins_total / rounds_available))
    return samples


def _empty_bucket_accumulator() -> dict[str, float]:
    return {"total": 0, "win": 0, "kills_ratio_sum": 0.0, "wins_ratio_sum": 0.0}


def _encode_buckets(buckets: dict[int, dict[str, float]]) -> list[list]:
    return [
        [idx, int(b["total"]), int(b["win"]), b["kills_ratio_sum"], b["wins_ratio_sum"]]
        for idx, b in sorted(buckets.items())
    ]


def compute_pistol_win_followup_eco(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """Pure aggregation over already-loaded Match rows (match_players + rounds
    + round.player_stats must be eager-loaded by the caller). Returns
    {"friends": {"buckets": [...]}, "all": {"buckets": [...]}}, each bucket
    row JSON-safe as [idx, total, win, kills_ratio_sum, wins_ratio_sum]."""
    friends_buckets: dict[int, dict[str, float]] = {}
    all_buckets: dict[int, dict[str, float]] = {}

    for match in matches:
        for winner, total_loadout, won_followup, kills_rate, wins_rate in _pistol_win_followup_samples(match):
            idx = eco_bucket_index(total_loadout)
            winner_player_ids = {mp.player_id for mp in match.match_players if mp.team == winner}

            targets = [all_buckets]
            if winner_player_ids & roster_player_ids:
                targets.append(friends_buckets)

            for buckets in targets:
                bucket = buckets.setdefault(idx, _empty_bucket_accumulator())
                bucket["total"] += 1
                if won_followup:
                    bucket["win"] += 1
                bucket["kills_ratio_sum"] += kills_rate
                bucket["wins_ratio_sum"] += wins_rate

    return {"friends": {"buckets": _encode_buckets(friends_buckets)}, "all": {"buckets": _encode_buckets(all_buckets)}}


@dataclass
class EcoFollowupBucket:
    label: str
    total: int
    immediate_win_pct: float
    immediate_fill: str
    avg_kills_next4: float
    avg_win_rate_next4: float
    next4_fill: str


@dataclass
class EcoFollowupStats:
    buckets: list[EcoFollowupBucket]
    total_samples: int
    best_immediate_win_bucket: EcoFollowupBucket | None
    best_kills_bucket: EcoFollowupBucket | None
    best_win_rate_next4_bucket: EcoFollowupBucket | None
    overall_immediate_win_pct: float | None
    overall_immediate_loss_pct: float | None


def _bucket_label(idx: int) -> str:
    low = ECO_BUCKET_FLOOR + idx * ECO_BUCKET_WIDTH
    high = low + ECO_BUCKET_WIDTH
    return f"{low:,}-{high:,}"


def build_eco_followup_stats_from_aggregates(variant: dict) -> EcoFollowupStats:
    buckets: list[EcoFollowupBucket] = []
    total_samples = 0
    overall_wins = 0
    for idx, total, win, kills_ratio_sum, wins_ratio_sum in variant["buckets"]:
        if total == 0:
            continue
        total_samples += total
        overall_wins += win
        immediate_win_pct = win / total
        avg_win_rate_next4 = wins_ratio_sum / total
        buckets.append(
            EcoFollowupBucket(
                label=_bucket_label(idx),
                total=total,
                immediate_win_pct=immediate_win_pct,
                immediate_fill=win_color(immediate_win_pct),
                avg_kills_next4=kills_ratio_sum / total,
                avg_win_rate_next4=avg_win_rate_next4,
                next4_fill=win_color(avg_win_rate_next4),
            )
        )

    eligible = [b for b in buckets if b.total >= MIN_SAMPLES_FOR_BEST]
    overall_win_pct = overall_wins / total_samples if total_samples else None
    return EcoFollowupStats(
        buckets=buckets,
        total_samples=total_samples,
        best_immediate_win_bucket=max(eligible, key=lambda b: b.immediate_win_pct, default=None),
        best_kills_bucket=max(eligible, key=lambda b: b.avg_kills_next4, default=None),
        best_win_rate_next4_bucket=max(eligible, key=lambda b: b.avg_win_rate_next4, default=None),
        overall_immediate_win_pct=overall_win_pct,
        overall_immediate_loss_pct=(1 - overall_win_pct) if overall_win_pct is not None else None,
    )
