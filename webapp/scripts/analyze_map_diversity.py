"""
Tests candidate mechanisms for Riot's map-diversity system against real
match history, rather than assuming one up front. Uses each crawled
player's own chronological Competitive match sequence (complete and
gap-free for anyone in scripts/map_crawl_state.json's "crawled" list, since
the crawl discovers matches contiguously backward from most recent) to
build a hazard/propensity table: for every (match, candidate map that was
actually in the active pool at the time) pair, was that candidate the one
chosen? P(chosen | recency-gap) is then directly comparable across gap
values and candidate models can be scored by log-likelihood.

The active pool per match is read from scripts/season_acts.json (real
tracker.gg Episode/Act boundaries) rather than approximated -- refresh it
with:
    .venv\\Scripts\\python.exe -c "from playwright.sync_api import sync_playwright; \\
        from app.adapters.trackergg_browserstate_source import discover_all_season_ids; \\
        import json; p = sync_playwright().start(); b = p.chromium.connect_over_cdp('http://localhost:9222'); \\
        page = b.contexts[0].new_page(); acts = discover_all_season_ids(page, 'NPrightdolphin#NA1'); \\
        json.dump(acts, open('scripts/season_acts.json', 'w')); page.close(); p.stop()"

Run after scripts/crawl_map_diversity_data.py has ingested enough data:
    .venv\\Scripts\\python.exe scripts\\analyze_map_diversity.py
"""

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app.db import SessionLocal
from app.models import Match, MatchPlayer, Player

STATE_PATH = Path(__file__).resolve().parent / "map_crawl_state.json"
SEASON_ACTS_PATH = Path(__file__).resolve().parent / "season_acts.json"


def _crawled_player_ids(db) -> dict[int, str]:
    riot_ids = json.loads(STATE_PATH.read_text(encoding="utf-8"))["crawled"]
    rows = db.query(Player.id, Player.display_name).filter(Player.display_name.in_(riot_ids)).all()
    return {player_id: display_name for player_id, display_name in rows}


def _player_match_sequences(db, player_ids: list[int]) -> dict[int, list[tuple]]:
    """For each player id, their Competitive matches sorted ascending by
    played_at: list of (played_at, map_name, match_id)."""
    rows = (
        db.query(MatchPlayer.player_id, Match.played_at, Match.map_name, Match.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .filter(MatchPlayer.player_id.in_(player_ids))
        .filter(Match.played_at.isnot(None))
        .order_by(MatchPlayer.player_id, Match.played_at.asc())
        .all()
    )
    sequences: dict[int, list[tuple]] = defaultdict(list)
    for player_id, played_at, map_name, match_id in rows:
        sequences[player_id].append((played_at, map_name, match_id))
    return sequences


def _repeat_gaps(sequence: list[tuple]) -> list[int]:
    """Games-since-last-play-of-that-map, for every match where the map had
    already appeared earlier in this player's recorded history."""
    last_seen_at_index: dict[str, int] = {}
    gaps = []
    for i, (_, map_name, _match_id) in enumerate(sequence):
        if map_name in last_seen_at_index:
            gaps.append(i - last_seen_at_index[map_name])
        last_seen_at_index[map_name] = i
    return gaps


GAP_CAP = 20  # bucket every gap >= this together; support gets thin past here anyway


def _act_boundaries() -> list[tuple[datetime, datetime | None]]:
    """Real Episode/Act boundaries from scripts/season_acts.json, most
    recent first; the most recent act's end is None (still ongoing)."""
    acts = json.loads(SEASON_ACTS_PATH.read_text(encoding="utf-8"))
    acts_sorted = sorted(acts, key=lambda a: a["start_time"], reverse=True)
    boundaries = []
    for i, act in enumerate(acts_sorted):
        start = datetime.fromisoformat(act["start_time"])
        end = datetime.fromisoformat(acts_sorted[i - 1]["start_time"]) if i > 0 else None
        boundaries.append((start, end))
    return boundaries


def _active_pool_by_act(db, boundaries: list[tuple[datetime, datetime | None]]) -> list[set[str]]:
    """The set of maps that actually appeared in ANY match (any player)
    within each real act's date range -- precise, since it's anchored to
    Riot's actual rotation boundaries instead of a calendar approximation."""
    rows = db.query(Match.played_at, Match.map_name).filter(Match.played_at.isnot(None)).all()
    pools = [set() for _ in boundaries]
    for played_at, map_name in rows:
        for idx, (start, end) in enumerate(boundaries):
            if played_at >= start and (end is None or played_at < end):
                pools[idx].add(map_name)
                break
    return pools


def _make_pool_lookup(
    boundaries: list[tuple[datetime, datetime | None]], pools: list[set[str]]
) -> Callable[[datetime], set[str] | None]:
    def lookup(played_at: datetime) -> set[str] | None:
        for (start, end), pool in zip(boundaries, pools):
            if played_at >= start and (end is None or played_at < end):
                return pool
        return None

    return lookup


def _hazard_table(
    sequences: dict[int, list[tuple]], pool_lookup: Callable[[datetime], set[str] | None]
) -> tuple[Counter, Counter]:
    """For every (match, candidate-map-in-the-active-pool) pair, buckets by
    the candidate's recency gap (games since the player last played it,
    capped at GAP_CAP; skipped entirely if never seen in recorded history --
    no baseline to compare against) and records whether that candidate was
    the one actually chosen. This is a proper hazard/propensity table:
    P(chosen | gap=g) = gap_chosen[g] / gap_available[g] -- comparable across
    gap values regardless of how big the active pool was at the time."""
    gap_available: Counter = Counter()
    gap_chosen: Counter = Counter()

    for seq in sequences.values():
        last_seen_idx: dict[str, int] = {}
        for i, (played_at, map_name, _match_id) in enumerate(seq):
            pool = pool_lookup(played_at)
            if pool:
                for candidate in pool:
                    if candidate not in last_seen_idx:
                        continue
                    gap = min(i - last_seen_idx[candidate], GAP_CAP)
                    gap_available[gap] += 1
                    if candidate == map_name:
                        gap_chosen[gap] += 1
            last_seen_idx[map_name] = i

    return gap_available, gap_chosen


def _map_keyed_hazard_table(
    sequences: dict[int, list[tuple]], pool_lookup: Callable[[datetime], set[str] | None]
) -> tuple[Counter, Counter]:
    """Same (match, candidate-in-active-pool) enumeration as _hazard_table,
    but keyed by the candidate map's identity instead of its recency gap --
    used to test whether per-map popularity alone (no recency effect at
    all) already explains a meaningful share of the variation."""
    map_available: Counter = Counter()
    map_chosen: Counter = Counter()
    for seq in sequences.values():
        for played_at, map_name, _match_id in seq:
            pool = pool_lookup(played_at)
            if not pool:
                continue
            for candidate in pool:
                map_available[candidate] += 1
                if candidate == map_name:
                    map_chosen[candidate] += 1
    return map_available, map_chosen


def _map_popularity(db) -> dict[str, float]:
    """Each map's overall share of all matches (any player), used as the
    'popularity-weighted null' baseline for the tests below."""
    rows = (
        db.query(Match.map_name, func.count(Match.id))
        .filter(Match.played_at.isnot(None))
        .group_by(Match.map_name)
        .all()
    )
    total = sum(c for _, c in rows)
    return {m: c / total for m, c in rows} if total else {}


def _log_likelihood(gap_available: Counter, gap_chosen: Counter, p_hat_fn) -> float:
    ll = 0.0
    for g, n in gap_available.items():
        if n == 0:
            continue
        k = gap_chosen.get(g, 0)
        p = min(max(p_hat_fn(g), 1e-9), 1 - 1e-9)
        ll += k * math.log(p) + (n - k) * math.log(1 - p)
    return ll


def _compare_candidate_models(gap_available: Counter, gap_chosen: Counter) -> None:
    """Only the candidates that showed a real chance in the first pass:
      - a hard exclusion window (sanity re-check -- already falsified)
      - gap==1 special-cased (soft "just played" suppression)
      - a smooth exponential-recovery curve (alternative shape for the same effect)
      - gap==1 + long-tail both suppressed (checks whether the tail effect
        survives now that the pool is the real act boundary, not a guess)
    Linear-in-gap, a generic two-piece breakpoint search, and a separate
    gap==2 effect were all tested previously and came back with ~zero
    signal -- dropped here rather than re-run for the sake of it.
    """
    total_n = sum(gap_available.values())
    total_k = sum(gap_chosen.values())
    if total_n == 0:
        print("\nNo hazard-table observations -- need more data.")
        return
    null_rate = total_k / total_n
    ll_null = _log_likelihood(gap_available, gap_chosen, lambda g: null_rate)

    print(f"\nOverall base rate (any map, any gap, chosen this game): {null_rate:.4f}")
    print(f"Null model (no recency effect) log-likelihood: {ll_null:.1f}")

    gaps_sorted = sorted(gap_available)
    print(f"\n{'gap':>4} {'available':>10} {'chosen':>8} {'P(chosen|gap)':>15}")
    for g in gaps_sorted:
        n, k = gap_available[g], gap_chosen.get(g, 0)
        rate = k / n if n else float("nan")
        print(f"{g:>4} {n:>10} {k:>8} {rate:>15.4f}")

    below_gap2 = sum(gap_chosen.get(g, 0) for g in gaps_sorted if g < 2)
    print(
        "\nHard-window re-check: "
        + ("falsified again (a repeat occurred at gap<2)" if below_gap2 > 0 else "no violation observed")
    )

    print("\n'Just-played is suppressed, everything else flat' candidate (gap==1 special-cased):")
    n1, k1 = gap_available.get(1, 0), gap_chosen.get(1, 0)
    n_rest = sum(gap_available[g] for g in gaps_sorted if g != 1)
    k_rest = sum(gap_chosen.get(g, 0) for g in gaps_sorted if g != 1)
    rate1 = rate_rest = None
    ll_gap1 = None
    if n1 and n_rest:
        rate1 = k1 / n1
        rate_rest = k_rest / n_rest
        ll_gap1 = _log_likelihood(gap_available, gap_chosen, lambda g: (rate1 if g == 1 else rate_rest))
        print(
            f"  rate_gap1={rate1:.4f}, rate_gap2plus={rate_rest:.4f}, LL={ll_gap1:.1f} "
            f"(vs null LL={ll_null:.1f}, 2*delta={2 * (ll_gap1 - ll_null):.1f})"
        )

    print("\nExponential-recovery candidate (rate recovers from a floor toward a plateau):")
    plateau_n = sum(gap_available[g] for g in gaps_sorted if 3 <= g <= 12)
    plateau_k = sum(gap_chosen.get(g, 0) for g in gaps_sorted if 3 <= g <= 12)
    plateau = plateau_k / plateau_n if plateau_n else null_rate
    best_exp = None
    for floor_milli in range(0, 151, 5):
        floor = floor_milli / 1000
        for decay_pct in range(10, 96, 5):
            decay = decay_pct / 100

            def rate_fn(g, f=floor, p=plateau, d=decay):
                return min(max(p - (p - f) * (d ** max(g - 1, 0)), 1e-6), 1 - 1e-6)

            ll = _log_likelihood(gap_available, gap_chosen, rate_fn)
            if best_exp is None or ll > best_exp[0]:
                best_exp = (ll, floor, decay)
    if best_exp:
        ll_exp, floor, decay = best_exp
        print(
            f"  best fit: floor={floor:.3f}, plateau={plateau:.3f}, decay={decay:.2f} per game, "
            f"LL={ll_exp:.1f} (vs null LL={ll_null:.1f}, 2*delta={2 * (ll_exp - ll_null):.1f})"
        )

    if rate1 is not None:
        print("\nCombined candidate: gap==1 special-cased AND a long-tail breakpoint both suppressed:")
        best_combo = None
        for bp in range(10, GAP_CAP + 1):
            mid_n = sum(gap_available[g] for g in gaps_sorted if 2 <= g < bp)
            mid_k = sum(gap_chosen.get(g, 0) for g in gaps_sorted if 2 <= g < bp)
            tail_n = sum(gap_available[g] for g in gaps_sorted if g >= bp)
            tail_k = sum(gap_chosen.get(g, 0) for g in gaps_sorted if g >= bp)
            if mid_n == 0 or tail_n == 0:
                continue
            rate_mid = mid_k / mid_n
            rate_tail = tail_k / tail_n
            ll_combo = _log_likelihood(
                gap_available,
                gap_chosen,
                lambda g, bp=bp, r1=rate1, rm=rate_mid, rt=rate_tail: (
                    r1 if g == 1 else (rm if g < bp else rt)
                ),
            )
            if best_combo is None or ll_combo > best_combo[0]:
                best_combo = (ll_combo, bp, rate_mid, rate_tail)
        if best_combo:
            ll_combo, bp, rate_mid, rate_tail = best_combo
            print(
                f"  best tail breakpoint K={bp - 1}: rate_gap1={rate1:.4f}, rate_mid={rate_mid:.4f}, "
                f"rate_tail={rate_tail:.4f}, LL={ll_combo:.1f} (vs null LL={ll_null:.1f}, "
                f"2*delta={2 * (ll_combo - ll_null):.1f}, vs gap1-only 2*delta="
                f"{2 * (ll_combo - ll_gap1):.1f})"
            )
            print(
                "  (last term: with the real act-boundary pool, is the tail still adding real signal "
                "over gap==1 alone, or was it mostly a calendar-approximation artifact?)"
            )


def _team_wide_test(sequences: dict[int, list[tuple]], map_popularity: dict[str, float]):
    """Candidate: the suppression isn't purely about the querying player's
    OWN last map -- it also avoids maps that OTHER crawled players in the
    same lobby (teammates or opponents) played last game. Groups per-player
    sequences by match_id, builds each match's union of participating
    crawled players' own immediately-previous map, and compares how often
    the actual chosen map falls in that union against the popularity-
    weighted expectation if there were no such group effect at all."""
    match_participants: dict[int, list[tuple[int, int]]] = defaultdict(list)
    match_info: dict[int, str] = {}
    for player_id, seq in sequences.items():
        for idx, (_played_at, map_name, match_id) in enumerate(seq):
            match_participants[match_id].append((player_id, idx))
            match_info[match_id] = map_name

    observed_hits = 0
    observed_total = 0
    per_match_p: list[float] = []
    for match_id, participants in match_participants.items():
        actual_map = match_info[match_id]
        excluded: set[str] = set()
        for player_id, idx in participants:
            if idx == 0:
                continue
            excluded.add(sequences[player_id][idx - 1][1])
        if not excluded:
            continue
        observed_total += 1
        p_i = sum(map_popularity.get(m, 0.0) for m in excluded)
        per_match_p.append(p_i)
        if actual_map in excluded:
            observed_hits += 1

    if observed_total == 0:
        return None
    expected_hits = sum(per_match_p)
    variance = sum(p * (1 - p) for p in per_match_p)
    z = (observed_hits - expected_hits) / math.sqrt(variance) if variance > 0 else float("nan")
    return observed_total, observed_hits, expected_hits, z


def main() -> None:
    db = SessionLocal()
    try:
        player_names = _crawled_player_ids(db)
        if not player_names:
            print("No crawled players found in map_crawl_state.json -- run the crawl first.")
            return

        sequences = _player_match_sequences(db, list(player_names))

        all_gaps: list[tuple[int, str]] = []
        per_player_summary = []
        for player_id, name in player_names.items():
            seq = sequences.get(player_id, [])
            gaps = _repeat_gaps(seq)
            per_player_summary.append((name, len(seq), len(gaps), min(gaps) if gaps else None))
            all_gaps.extend((g, name) for g in gaps)

        print(f"{'riot_id':30} {'matches':>8} {'repeats':>8} {'min_gap':>8}")
        for name, n_matches, n_repeats, min_gap in sorted(
            per_player_summary, key=lambda r: (r[3] is None, r[3])
        ):
            print(f"{name:30} {n_matches:>8} {n_repeats:>8} {str(min_gap):>8}")

        if not all_gaps:
            print("\nNo repeats observed yet -- need more data before a window (K) can be estimated.")
            return

        print("\n" + "=" * 70)
        print("Active map pool by real Episode/Act boundary (scripts/season_acts.json)")
        print("=" * 70)
        boundaries = _act_boundaries()
        pools = _active_pool_by_act(db, boundaries)
        for (start, end), pool in zip(boundaries, pools):
            if not pool:
                continue
            end_str = end.date().isoformat() if end else "ongoing"
            print(f"  {start.date()} -> {end_str}: {sorted(pool)}")
        pool_lookup = _make_pool_lookup(boundaries, pools)

        print("\n" + "=" * 70)
        print("Hazard-rate analysis: P(a given available map is chosen | its recency gap)")
        print("=" * 70)
        gap_available, gap_chosen = _hazard_table(sequences, pool_lookup)
        _compare_candidate_models(gap_available, gap_chosen)

        print("\n" + "=" * 70)
        print("Does per-map popularity alone (zero recency effect) explain the data?")
        print("=" * 70)
        map_available, map_chosen = _map_keyed_hazard_table(sequences, pool_lookup)
        total_n = sum(map_available.values())
        total_k = sum(map_chosen.values())
        null_rate = total_k / total_n if total_n else 0.0
        ll_flat = _log_likelihood(Counter({0: total_n}), Counter({0: total_k}), lambda g: null_rate)
        ll_per_map = sum(
            _log_likelihood(
                Counter({0: map_available[m]}),
                Counter({0: map_chosen.get(m, 0)}),
                lambda g, m=m: (map_chosen.get(m, 0) / map_available[m]) if map_available[m] else 1e-9,
            )
            for m in map_available
        )
        print(f"  flat-rate null LL:    {ll_flat:.1f}")
        print(f"  per-map-popularity LL: {ll_per_map:.1f}  (2*delta={2 * (ll_per_map - ll_flat):.1f})")

        print("\n" + "=" * 70)
        print("Team-wide candidate: does the pick also avoid OTHER crawled players' last map?")
        print("=" * 70)
        map_popularity = _map_popularity(db)
        team_result = _team_wide_test(sequences, map_popularity)
        if team_result:
            considered, hits, expected, z = team_result
            print(f"  matches considered (>=1 participant with known last map): {considered}")
            print(f"  observed hits (actual map was in the union of others' last maps): {hits}")
            print(f"  expected hits under popularity-only null: {expected:.1f}")
            print(f"  z-score: {z:.2f}  (|z|>~2 suggests a real group-wide effect beyond the individual one)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
