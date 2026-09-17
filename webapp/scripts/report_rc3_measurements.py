"""The rc3 declared measurements, computed from keyed artifacts.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md
("rc3: the owner's lock after measurement", measurements 1-7, and the RESULT
entry that re-declared the chain from commit b65fd4f).

Inputs are the comparison-projection CSVs written by export_impact_artifact.py:
one credit-ON and one credit-OFF artifact from the same commit and interpreter.
They are read in lockstep and ALIGNED BY KEY -- a row whose (round_id,
match_player_id) differs between the two stops the report. The earlier scratch
reports zipped unkeyed arrays by position and could not have noticed.

Source mappings (which match and player a row belongs to, kill/death counts,
the stored v1 impact) are read from the database in one read-only snapshot.

    DATABASE_URL=... python scripts/report_rc3_measurements.py \
        --on  .../K2p-on.csv --off .../KAp-off.csv \
        --json .../last-look.json

Definitions, fixed by the declaration:
- corpus share: each term's summed magnitude over player-matches;
- median share: over player-matches with >= 12 rounds;
- within-match impact order: descending impact, ties broken by player id;
- impact rank against K-D rank: K-D from kill_events, ties kept in player-match
  (match_player_id) order;
- credit (a) leverage difference: ON minus OFF leverage_component per
  player-round; its positive-leverage basis is credit OFF;
- credit (b) persisted credit: the sum of the trade_credit field.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from scripts.export_impact_artifact import COMPARISON_HEADER

TERMS = ("damage", "leverage_component", "econ_component", "assists_component")
SMALLINT_COLUMNS = ("damage", "econ_impact", "time_impact", "swing_impact", "econ_kill", "econ_death",
                    "clutch_kill", "clutch_death", "post_plant_kill", "post_plant_death",
                    "traded_teammate", "traded_by_teammate", "kill_order_bonus", "econ_component",
                    "econ_pickup", "trade_credit")
INT_COLUMNS = tuple(c for c in COMPARISON_HEADER if c != "trade_detail")


class ArtifactMismatch(RuntimeError):
    pass


def _rows(path):
    handle = open(path, "r", encoding="utf-8", newline="")
    reader = csv.reader(handle)
    header = tuple(next(reader))
    if header != COMPARISON_HEADER:
        raise ArtifactMismatch(f"{path}: header is not the recorded comparison header")
    index = {name: i for i, name in enumerate(header)}
    for raw in reader:
        yield {name: int(raw[index[name]]) for name in INT_COLUMNS}
    handle.close()


def _rank_average(values):
    """Ranks with ties averaged, 1 = largest."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        for k in range(position, end + 1):
            ranks[order[k]] = (position + end) / 2 + 1
        position = end + 1
    return ranks


def spearman(a, b):
    ra, rb = _rank_average(a), _rank_average(b)
    mean_a, mean_b = statistics.fmean(ra), statistics.fmean(rb)
    num = math.fsum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = math.sqrt(math.fsum((x - mean_a) ** 2 for x in ra) * math.fsum((y - mean_b) ** 2 for y in rb))
    return num / den if den else None


def order_by_impact(players, key):
    """Descending impact, ties broken by player id."""
    return [p["player_id"] for p in sorted(players, key=lambda p: (-p[key], p["player_id"]))]


def load_sources(db):
    match_player = {mp_id: (match_id, player_id) for mp_id, match_id, player_id in db.execute(
        sa.text("SELECT id, match_id, player_id FROM match_players")).all()}
    names = dict(db.execute(sa.text("SELECT id, display_name FROM players")).all())
    kills = dict(db.execute(sa.text(
        "SELECT killer_match_player_id, count(*) FROM kill_events "
        "WHERE killer_match_player_id IS NOT NULL GROUP BY 1")).all())
    deaths = dict(db.execute(sa.text(
        "SELECT death_match_player_id, count(*) FROM kill_events "
        "WHERE death_match_player_id IS NOT NULL GROUP BY 1")).all())
    stored = {mp_id: (int(total), int(rounds)) for mp_id, total, rounds in db.execute(sa.text(
        "SELECT match_player_id, sum(impact), count(*) FROM impact_scores GROUP BY 1")).all()}
    round_counts = dict(db.execute(sa.text("SELECT match_id, count(*) FROM rounds GROUP BY 1")).all())
    return match_player, names, kills, deaths, stored, round_counts


def measure(on_path, off_path, sources, tracked_names, limit_matches=None):
    match_player, names, kills, deaths, stored, round_counts = sources

    per_mp = defaultdict(lambda: {"rounds": 0, "damage": 0, "leverage_component": 0, "econ_component": 0,
                                  "assists_component": 0, "impact": 0, "impact_off": 0})
    column_sums = Counter()
    column_max_abs = Counter()
    rows = 0
    impact_sum = 0
    impact_sq_sum = 0
    negative_damage = 0
    lev_diff_total = 0
    lev_diff_positive_rows = 0
    lev_diff_max = 0
    positive_leverage_off = 0
    credit_total = 0
    credit_nonzero_rows = 0
    credit_max = 0

    for on, off in zip(_rows(on_path), _rows(off_path)):
        key_on = (on["round_id"], on["match_player_id"])
        key_off = (off["round_id"], off["match_player_id"])
        if key_on != key_off:
            raise ArtifactMismatch(f"artifacts are not key-aligned: ON {key_on} vs OFF {key_off}")
        match_id, _player = match_player[on["match_player_id"]]
        if limit_matches is not None and match_id not in limit_matches:
            continue
        rows += 1
        for name in INT_COLUMNS[2:]:
            column_sums[name] += on[name]
        for name in SMALLINT_COLUMNS:
            column_max_abs[name] = max(column_max_abs[name], abs(on[name]))
        impact_sum += on["impact"]
        impact_sq_sum += on["impact"] * on["impact"]
        negative_damage += on["damage"] < 0

        difference = on["leverage_component"] - off["leverage_component"]
        lev_diff_total += difference
        lev_diff_positive_rows += difference > 0
        lev_diff_max = max(lev_diff_max, difference)
        positive_leverage_off += max(off["leverage_component"], 0)
        credit_total += on["trade_credit"]
        credit_nonzero_rows += on["trade_credit"] != 0
        credit_max = max(credit_max, on["trade_credit"])

        totals = per_mp[on["match_player_id"]]
        totals["rounds"] += 1
        for term in TERMS:
            totals[term] += on[term]
        totals["impact"] += on["impact"]
        totals["impact_off"] += off["impact"]

    # --- player-match tables
    matches = defaultdict(list)
    for mp_id, totals in per_mp.items():
        match_id, player_id = match_player[mp_id]
        matches[match_id].append({"match_player_id": mp_id, "player_id": player_id, **totals,
                                  "kd": kills.get(mp_id, 0) - deaths.get(mp_id, 0),
                                  "stored": stored.get(mp_id)})

    corpus_magnitude = {t: math.fsum(abs(p[t]) for ps in matches.values() for p in ps) for t in TERMS}
    grand = math.fsum(corpus_magnitude.values())
    corpus_share = {t: round(100 * corpus_magnitude[t] / grand, 1) for t in TERMS}
    shares = {t: [] for t in TERMS}
    for ps in matches.values():
        for p in ps:
            if p["rounds"] < 12:
                continue
            magnitude = math.fsum(abs(p[t]) for t in TERMS)
            if magnitude:
                for t in TERMS:
                    shares[t].append(100 * abs(p[t]) / magnitude)
    median_share = {t: round(statistics.median(v), 1) for t, v in shares.items()}

    players_per_match = Counter(len(ps) for ps in matches.values())
    round_profile = Counter(round_counts[m] for m in matches)

    order_changes = top_changes = rank_moves = rises = unchanged = falls = 0
    shift = Counter()
    kd_identical = kd_within_one = kd_total = 0
    for ps in matches.values():
        before = order_by_impact(ps, "impact_off")
        after = order_by_impact(ps, "impact")
        order_changes += before != after
        top_changes += before[0] != after[0]
        for position, player_id in enumerate(before):
            moved = after.index(player_id) - position
            shift[abs(moved)] += 1
            rank_moves += moved != 0
        for p in ps:
            rises += p["impact"] > p["impact_off"]
            unchanged += p["impact"] == p["impact_off"]
            falls += p["impact"] < p["impact_off"]
        if len(ps) == 10:
            by_mp = sorted(ps, key=lambda p: p["match_player_id"])
            impact_rank = {id(p): i for i, p in enumerate(sorted(by_mp, key=lambda p: -p["impact"]))}
            kd_rank = {id(p): i for i, p in enumerate(sorted(by_mp, key=lambda p: -p["kd"]))}
            for p in by_mp:
                gap = abs(impact_rank[id(p)] - kd_rank[id(p)])
                kd_total += 1
                kd_identical += gap == 0
                kd_within_one += gap <= 1

    # --- measurement 7: stored v1 -> rc3, over matches with stored rows
    v1_moves = v1_top_changes = v1_matches = v1_player_matches = 0
    v1_shift = Counter()
    correlations = []
    unscored = []
    for match_id, ps in matches.items():
        if any(p["stored"] is None for p in ps):
            unscored.append(match_id)
            continue
        v1_matches += 1
        for p in ps:
            p["v1"] = p["stored"][0]
        before = order_by_impact(ps, "v1")
        after = order_by_impact(ps, "impact")
        v1_top_changes += before[0] != after[0]
        for position, player_id in enumerate(before):
            moved = after.index(player_id) - position
            v1_shift[abs(moved)] += 1
            v1_moves += moved != 0
            v1_player_matches += 1
        rho = spearman([p["v1"] for p in ps], [p["impact"] for p in ps])
        if rho is not None:
            correlations.append(rho)

    tracked = []
    ids_by_name = {name: player_id for player_id, name in names.items()}
    for name in tracked_names:
        player_id = ids_by_name.get(name)
        if player_id is None:
            continue
        v1_total = v1_rounds = rc3_total = rc3_rounds = 0
        for match_id, ps in matches.items():
            if match_id in unscored:
                continue
            for p in ps:
                if p["player_id"] == player_id:
                    v1_total += p["stored"][0]
                    v1_rounds += p["stored"][1]
                    rc3_total += p["impact"]
                    rc3_rounds += p["rounds"]
        if v1_rounds and rc3_rounds:
            tracked.append({"player": name, "matches_rounds": rc3_rounds,
                            "v1_per_round": round(v1_total / v1_rounds, 1),
                            "rc3_per_round": round(rc3_total / rc3_rounds, 1)})
    for rank_key in ("v1_per_round", "rc3_per_round"):
        for rank, entry in enumerate(sorted(tracked, key=lambda e: -e[rank_key]), start=1):
            entry[rank_key.replace("per_round", "rank")] = rank

    unscored_detail = {}
    for match_id in unscored:
        ps = matches[match_id]
        unscored_detail[str(match_id)] = sorted(
            ({"player": names[p["player_id"]], "rc3_impact": p["impact"], "rounds": p["rounds"]}
             for p in ps), key=lambda e: -e["rc3_impact"])

    mean = impact_sum / rows
    sd = math.sqrt(max(impact_sq_sum / rows - mean * mean, 0.0))
    return {
        "rows": rows,
        "player_matches": sum(len(ps) for ps in matches.values()),
        "matches": len(matches),
        "1_shares": {"corpus": corpus_share, "median_ge_12_rounds": median_share,
                     "median_n": len(shares["damage"])},
        "2_credit": {
            "a_leverage_difference": {
                "total": lev_diff_total,
                "share_of_positive_leverage_credit_off_pct": round(100 * lev_diff_total / positive_leverage_off, 1),
                "positive_leverage_credit_off": positive_leverage_off,
                "player_rounds_positive": lev_diff_positive_rows,
                "largest_per_round": lev_diff_max},
            "b_persisted_credit": {"total": credit_total, "player_rounds_nonzero": credit_nonzero_rows,
                                   "largest_per_round": credit_max},
            "a_minus_b": lev_diff_total - credit_total,
        },
        "3_impact_per_round": {"mean": round(mean, 1), "sd": round(sd, 1),
                               "negative_damage_rows": negative_damage},
        "4_sanity_and_ranks": {
            "players_per_match": dict(players_per_match),
            "round_count_profile": dict(sorted(round_profile.items())),
            "impact_vs_kd": {"identical_pct": round(100 * kd_identical / kd_total, 1),
                             "within_one_pct": round(100 * kd_within_one / kd_total, 1), "n": kd_total},
            "off_to_on": {"matches_order_changes": order_changes, "matches_top_changes": top_changes,
                          "player_match_rank_changes": rank_moves, "shift": dict(sorted(shift.items())),
                          "impact_rises": rises, "impact_unchanged": unchanged, "impact_falls": falls},
        },
        "5_checksums": {"rows": rows, "column_sums": dict(column_sums)},
        "6_smallint_max_abs": dict(column_max_abs),
        "7_stored_v1_to_rc3": {
            "matches_compared": v1_matches, "player_matches": v1_player_matches,
            "player_match_rank_changes": v1_moves, "shift": dict(sorted(v1_shift.items())),
            "matches_top_player_changes": v1_top_changes,
            "spearman_per_match": {"median": round(statistics.median(correlations), 3),
                                   "p10": round(sorted(correlations)[len(correlations) // 10], 3),
                                   "p90": round(sorted(correlations)[9 * len(correlations) // 10], 3)},
            "tracked_players": sorted(tracked, key=lambda e: e["rc3_rank"]),
            "unscored_matches_reported_separately": unscored_detail,
        },
    }


def rows_that_differ(path_a, path_b):
    differing, columns = 0, Counter()
    for a, b in zip(_rows(path_a), _rows(path_b)):
        if (a["round_id"], a["match_player_id"]) != (b["round_id"], b["match_player_id"]):
            raise ArtifactMismatch("artifacts are not key-aligned")
        if a != b:
            differing += 1
            for name in INT_COLUMNS:
                if a[name] != b[name]:
                    columns[name] += 1
    return {"rows": differing, "columns": dict(columns)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--on", required=True)
    parser.add_argument("--off", required=True)
    parser.add_argument("--json", required=True, help="where to write every figure")
    parser.add_argument("--compare-on", action="append", default=[],
                        help="LABEL=path: report rows differing from --on (no pass condition)")
    parser.add_argument("--limit-matches", help="comma-separated match ids: mechanics check only")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        sources = load_sources(db)
        db.rollback()
    finally:
        db.close()

    tracked_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracked_players.json")
    tracked_names = json.load(open(tracked_path, encoding="utf-8"))
    limit = {int(x) for x in args.limit_matches.split(",")} if args.limit_matches else None

    result = measure(args.on, args.off, sources, tracked_names, limit_matches=limit)
    for spec in args.compare_on:
        label, path = spec.split("=", 1)
        result[f"differs_from_{label}"] = rows_that_differ(args.on, path)

    with open(args.json, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=False)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
