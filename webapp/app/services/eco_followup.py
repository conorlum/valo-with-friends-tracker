""""After winning a pistol round, what should the team buy next round?" --
buckets the WINNING team's total loadout (sum of all 5 players' loadout
value) in the round right after a won pistol round (round 2 after round 1,
round 14 after round 13) against four outcomes:

  - did the team also win that immediate follow-up round
  - average round-win rate over the next 2 rounds (2-3 / 14-15)
  - average round-win rate over the next 4 rounds (2-5 / 14-17)
  - did the team go on to win the whole match

The match-win outcome is undefined (excluded from that column only, not the
sample as a whole) for a tied/undecided match -- see match_win's docstring.

(Kills-per-round over the follow-up window used to be tracked here too, but
it came out ~4 kills/round in every buy-amount bucket -- not discriminating
enough to be worth showing -- so it was dropped.)

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
from app.services.player_profile_types import match_win

# Chosen from the observed distribution (2026-08-23 snapshot of this DB): team
# total loadout the round right after a won pistol round ranged 5700-20400,
# median ~14600, with the bulk between 10000 and 19000. The tails (below 9000,
# at/above 19000) are thin on their own -- one 1000-wide bucket per 1000
# credits leaves them with only a handful of samples each -- so they're each
# folded into a single "under"/"above" catch-all bucket instead, keeping the
# tails statistically meaningful while the well-populated middle keeps its
# 1000-credit-wide granularity. (The under-bucket was originally under-8000;
# widened to under-9000 after the 8000-9000 bucket also turned out sparse.)
ECO_TAIL_LOW = 9000
ECO_TAIL_HIGH = 19000
ECO_BUCKET_WIDTH = 1000
_ECO_NUM_MIDDLE_BUCKETS = (ECO_TAIL_HIGH - ECO_TAIL_LOW) // ECO_BUCKET_WIDTH
ECO_NUM_BUCKETS = _ECO_NUM_MIDDLE_BUCKETS + 2  # + 1 "under" bucket, + 1 "above" bucket

PISTOL_FOLLOWUP_ROUNDS = ((1, 2), (13, 14))
FOLLOWUP_WINDOW = 4
FOLLOWUP_WINDOW_SHORT = 2

# A bucket needs at least this many samples before it's eligible to be
# highlighted as the "optimal" buy -- otherwise a handful of rounds in a
# sparse tail bucket could claim a 100% rate off pure noise.
MIN_SAMPLES_FOR_BEST = 20


def eco_bucket_index(total_loadout: int) -> int:
    if total_loadout < ECO_TAIL_LOW:
        return 0
    if total_loadout >= ECO_TAIL_HIGH:
        return ECO_NUM_BUCKETS - 1
    return int(1 + (total_loadout - ECO_TAIL_LOW) // ECO_BUCKET_WIDTH)


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def _pistol_win_followup_samples(match: Match) -> list[tuple[Team, int, bool, float, float, bool | None]]:
    """(winning_team, followup_total_loadout, won_followup_round, win_rate_next2,
    win_rate_next4, won_match) for each pistol round (1, 13) in `match` that had
    a decisive winner and a recorded follow-up round. The win rates are
    averaged over whatever of the next FOLLOWUP_WINDOW rounds actually exist --
    most matches have all 4, but one ending early (e.g. 13-3) may have fewer,
    so a raw sum would understate a short match's rate rather than reflect it.
    The follow-up round itself (round_number == followup_start) is already
    confirmed to exist above, so win_rate_next2's denominator is never zero.
    won_match is None for a tied/undecided match (see match_win)."""
    team_of = {mp.id: mp.team for mp in match.match_players}
    rounds_by_number = {r.round_number: r for r in match.rounds}

    samples: list[tuple[Team, int, bool, float, float, bool | None]] = []
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

        wins_total = 0
        rounds_available = 0
        wins_total_2 = 0
        rounds_available_2 = 0
        for i, rn in enumerate(range(followup_start, followup_start + FOLLOWUP_WINDOW)):
            r = rounds_by_number.get(rn)
            if r is None:
                continue
            rounds_available += 1
            won = _winner_team(r.outcome) == winner
            if won:
                wins_total += 1
            if i < FOLLOWUP_WINDOW_SHORT:
                rounds_available_2 += 1
                if won:
                    wins_total_2 += 1
        if rounds_available == 0:
            continue

        samples.append(
            (
                winner,
                total_loadout,
                won_followup,
                wins_total_2 / rounds_available_2,
                wins_total / rounds_available,
                match_win(match, winner),
            )
        )
    return samples


def _empty_bucket_accumulator() -> dict[str, float]:
    return {"total": 0, "win": 0, "wins_ratio_sum_2": 0.0, "wins_ratio_sum_4": 0.0, "match_total": 0, "match_win": 0}


def _encode_buckets(buckets: dict[int, dict[str, float]]) -> list[list]:
    return [
        [
            idx, int(b["total"]), int(b["win"]), b["wins_ratio_sum_2"], b["wins_ratio_sum_4"],
            int(b["match_total"]), int(b["match_win"]),
        ]
        for idx, b in sorted(buckets.items())
    ]


def compute_pistol_win_followup_eco(matches: list[Match], roster_player_ids: set[int]) -> dict:
    """Pure aggregation over already-loaded Match rows (match_players + rounds
    + round.player_stats must be eager-loaded by the caller). Returns
    {"friends": {"buckets": [...]}, "all": {"buckets": [...]}}, each bucket
    row JSON-safe as [idx, total, win, wins_ratio_sum_2, wins_ratio_sum_4,
    match_total, match_win] -- match_total/match_win only count samples whose
    match had a decisive outcome, so match_total <= total."""
    friends_buckets: dict[int, dict[str, float]] = {}
    all_buckets: dict[int, dict[str, float]] = {}

    for match in matches:
        for winner, total_loadout, won_followup, win_rate_2, win_rate_4, won_match in _pistol_win_followup_samples(
            match
        ):
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
                bucket["wins_ratio_sum_2"] += win_rate_2
                bucket["wins_ratio_sum_4"] += win_rate_4
                if won_match is not None:
                    bucket["match_total"] += 1
                    if won_match:
                        bucket["match_win"] += 1

    return {"friends": {"buckets": _encode_buckets(friends_buckets)}, "all": {"buckets": _encode_buckets(all_buckets)}}


@dataclass
class EcoFollowupBucket:
    label: str
    sort_key: int
    total: int
    immediate_win_pct: float
    immediate_fill: str
    avg_win_rate_next2: float
    next2_fill: str
    avg_win_rate_next4: float
    next4_fill: str
    match_total: int
    match_win_pct: float | None
    match_fill: str | None


@dataclass
class EcoFollowupStats:
    buckets: list[EcoFollowupBucket]
    total_samples: int
    best_immediate_win_bucket: EcoFollowupBucket | None
    best_win_rate_next2_bucket: EcoFollowupBucket | None
    best_win_rate_next4_bucket: EcoFollowupBucket | None
    best_match_win_bucket: EcoFollowupBucket | None
    overall_immediate_win_pct: float | None
    overall_immediate_loss_pct: float | None


def _bucket_low(idx: int) -> int:
    """The credit value this bucket sorts by -- 0 for the "under" catch-all
    (sorts before every real bucket), ECO_TAIL_HIGH for the "above" catch-all
    (sorts after every real bucket), else the bucket's own lower edge."""
    if idx == 0:
        return 0
    if idx == ECO_NUM_BUCKETS - 1:
        return ECO_TAIL_HIGH
    return ECO_TAIL_LOW + (idx - 1) * ECO_BUCKET_WIDTH


def _bucket_label(idx: int) -> str:
    if idx == 0:
        return f"Under {ECO_TAIL_LOW:,}"
    if idx == ECO_NUM_BUCKETS - 1:
        return f"{ECO_TAIL_HIGH:,}+"
    low = _bucket_low(idx)
    high = low + ECO_BUCKET_WIDTH
    return f"{low:,}-{high:,}"


def build_eco_followup_stats_from_aggregates(variant: dict) -> EcoFollowupStats:
    buckets: list[EcoFollowupBucket] = []
    total_samples = 0
    overall_wins = 0
    for idx, total, win, wins_ratio_sum_2, wins_ratio_sum_4, match_total, match_win_count in variant["buckets"]:
        if total == 0:
            continue
        total_samples += total
        overall_wins += win
        immediate_win_pct = win / total
        avg_win_rate_next2 = wins_ratio_sum_2 / total
        avg_win_rate_next4 = wins_ratio_sum_4 / total
        match_win_pct = match_win_count / match_total if match_total else None
        buckets.append(
            EcoFollowupBucket(
                label=_bucket_label(idx),
                sort_key=_bucket_low(idx),
                total=total,
                immediate_win_pct=immediate_win_pct,
                immediate_fill=win_color(immediate_win_pct),
                avg_win_rate_next2=avg_win_rate_next2,
                next2_fill=win_color(avg_win_rate_next2),
                avg_win_rate_next4=avg_win_rate_next4,
                next4_fill=win_color(avg_win_rate_next4),
                match_total=match_total,
                match_win_pct=match_win_pct,
                match_fill=win_color(match_win_pct) if match_win_pct is not None else None,
            )
        )

    eligible = [b for b in buckets if b.total >= MIN_SAMPLES_FOR_BEST]
    eligible_for_match = [b for b in eligible if b.match_win_pct is not None and b.match_total >= MIN_SAMPLES_FOR_BEST]
    overall_win_pct = overall_wins / total_samples if total_samples else None
    return EcoFollowupStats(
        buckets=buckets,
        total_samples=total_samples,
        best_immediate_win_bucket=max(eligible, key=lambda b: b.immediate_win_pct, default=None),
        best_win_rate_next2_bucket=max(eligible, key=lambda b: b.avg_win_rate_next2, default=None),
        best_win_rate_next4_bucket=max(eligible, key=lambda b: b.avg_win_rate_next4, default=None),
        best_match_win_bucket=max(eligible_for_match, key=lambda b: b.match_win_pct, default=None),
        overall_immediate_win_pct=overall_win_pct,
        overall_immediate_loss_pct=(1 - overall_win_pct) if overall_win_pct is not None else None,
    )
