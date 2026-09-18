r"""What A / D / B / C make each Impact term average a declared PERCENTAGE?

    .\.venv\Scripts\python.exe scripts\solve_impact_weights.py --parts 3:2:2 --combat-split 4:1
    ... --parts 4:2:2 --combat-split 3:1        # any other allocation
    ... --target damage=0.35,assists=0.10,leverage=0.30,econ=0.25
    ... --matches ten                           # the pinned ten instead of everything

The four terms are

    impact = A*damage + D*assists + B*leverage + C*econ

where the damage column today holds damage AND assists, so this tool splits
them: assists = ASSIST_POINTS * assists in that round, damage = the remainder.

A term's SHARE for one player-match is |term| / sum of the four |terms| --
absolute, because leverage and econ are frequently negative. A match's share
is the mean over its ten players, and the corpus share is the mean over
matches, so every match counts once regardless of length.

Two answers are printed:

  RATIO PASS   weight = target / share-at-weight-1, averaged over matches.
               This is one division per term. It OVERSHOOTS, because raising
               one weight also inflates the denominator of every other term.
  SOLVED       the same ratio applied repeatedly until the achieved shares sit
               on the targets. This is what to use.

Weights are reported relative to A=1. Nothing here is fitted to match outcomes
or win probability: it is a scale convention, like ECON_SCALE, and the target
percentages are the owner's product choice. Read-only; writes only its cache.
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring import econ_buy_disruption as bd
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match

TERMS = ("damage", "assists", "leverage", "econ")
ASSIST_POINTS = 25
FIXED_TEN = [3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117]


# ---- the arithmetic (pure) ---------------------------------------------------------

def targets_from_parts(parts: str, combat_split: str) -> dict:
    """"3:2:2" + "4:1" -> combat 3/7 of the score, split 4:1 into damage:assists."""
    combat, leverage, econ = (float(p) for p in parts.split(":"))
    damage_part, assist_part = (float(p) for p in combat_split.split(":"))
    total = combat + leverage + econ
    combat_share = combat / total
    return {
        "damage": combat_share * damage_part / (damage_part + assist_part),
        "assists": combat_share * assist_part / (damage_part + assist_part),
        "leverage": leverage / total,
        "econ": econ / total,
    }


def match_shares(players, weights) -> dict:
    """Mean over a match's players of each term's absolute share."""
    totals = {term: 0.0 for term in TERMS}
    counted = 0
    for player in players:
        weighted = {term: weights[term] * player[term] for term in TERMS}
        magnitude = sum(abs(value) for value in weighted.values())
        if magnitude == 0:
            continue
        counted += 1
        for term in TERMS:
            totals[term] += abs(weighted[term]) / magnitude
    if not counted:
        return {term: 0.0 for term in TERMS}
    return {term: totals[term] / counted for term in TERMS}


def achieved_shares(corpus, weights) -> dict:
    per_match = [match_shares(match, weights) for match in corpus]
    return {term: statistics.fmean(share[term] for share in per_match) for term in TERMS}


def derive_weights(shares, targets) -> dict:
    """One ratio pass: target / observed share. None when a term contributes
    nothing at all, which cannot be scaled into a share."""
    return {term: (targets[term] / shares[term] if shares[term] else None) for term in TERMS}


def solve_weights(corpus, targets, iterations: int = 200, normalize_to: str = "damage") -> dict:
    """Apply the ratio pass repeatedly until the achieved shares stop moving."""
    weights = {term: 1.0 for term in TERMS}
    for _ in range(iterations):
        shares = achieved_shares(corpus, weights)
        if all(shares[term] for term in TERMS):
            step = derive_weights(shares, targets)
            weights = {term: weights[term] * step[term] for term in TERMS}
        else:  # a term with no contribution anywhere cannot be solved for
            break
        scale = weights[normalize_to]
        if scale:
            weights = {term: value / scale for term, value in weights.items()}
    return weights


# ---- the corpus decomposition ------------------------------------------------------

def load_corpus(match_ids=None, assist_points: int = ASSIST_POINTS, progress=None):
    """Per match, per player: the four term values at weight 1."""
    corpus, names = [], {}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        if match_ids is None:
            match_ids = [row[0] for row in db.execute(text("SELECT id FROM matches ORDER BY id"))]
        for index, match_id in enumerate(match_ids):
            assists = {(r[0], r[1]): r[2] for r in db.execute(text(
                "SELECT s.round_id, s.match_player_id, s.assists FROM round_player_stats s "
                "JOIN rounds r ON r.id = s.round_id WHERE r.match_id = :m"), {"m": match_id}).all()}
            per_player = defaultdict(lambda: {term: 0 for term in TERMS})
            for row in build_impact_rows_for_match(
                    db, match_id, use_realized_swing=True, enable_econ_component=True,
                    econ_model=bd.MODEL_V2_30_80,
                    weights=FormulaWeights(damage=1.0, leverage=1.0, econ=1.0)):
                assist_value = assist_points * assists[(row.round_id, row.match_player_id)]
                player = per_player[row.match_player_id]
                player["damage"] += row.damage - assist_value
                player["assists"] += assist_value
                player["leverage"] += row.leverage_component
                player["econ"] += row.econ_component
            if per_player:
                corpus.append(list(per_player.values()))
            if progress and index % 250 == 0:
                progress(index, len(match_ids))
        db.rollback()
    return corpus, names


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parts", default="3:2:2", help="combat:leverage:econ, default 3:2:2")
    parser.add_argument("--combat-split", default="4:1", help="damage:assists inside combat, default 4:1")
    parser.add_argument("--target", help="explicit e.g. damage=0.35,assists=0.10,leverage=0.30,econ=0.25")
    parser.add_argument("--assist-points", type=int, default=ASSIST_POINTS)
    parser.add_argument("--matches", default="all", help="all | ten | comma-separated ids")
    parser.add_argument("--cache", default="impact_weight_corpus.json",
                        help="decomposition cache, so re-solving other percentages is instant")
    parser.add_argument("--refresh", action="store_true", help="rebuild the cache from the database")
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    if args.target:
        targets = {k: float(v) for k, v in (pair.split("=") for pair in args.target.split(","))}
        total = sum(targets.values())
        targets = {term: targets[term] / total for term in TERMS}
    else:
        targets = targets_from_parts(args.parts, args.combat_split)

    cache = Path(args.cache)
    key = f"{args.matches}:{args.assist_points}"
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() and not args.refresh else {}
    if cached.get("key") == key:
        corpus = cached["corpus"]
        print(f"corpus from cache {cache} ({len(corpus)} matches; --refresh to rebuild)")
    else:
        match_ids = (None if args.matches == "all" else
                     FIXED_TEN if args.matches == "ten" else
                     [int(m) for m in args.matches.split(",")])
        corpus, _ = load_corpus(match_ids, args.assist_points,
                                progress=lambda i, n: print(f"  {i}/{n}", flush=True))
        cache.write_text(json.dumps({"key": key, "corpus": corpus}), encoding="utf-8")
        print(f"corpus built from the database and cached in {cache} ({len(corpus)} matches)")

    unit = achieved_shares(corpus, {term: 1.0 for term in TERMS})
    ratio = derive_weights(unit, targets)
    solved = solve_weights(corpus, targets, args.iterations)
    final = achieved_shares(corpus, solved)
    ratio_shares = achieved_shares(corpus, {t: (ratio[t] or 0.0) for t in TERMS})

    print(f"\nmatches {len(corpus)}   player-matches {sum(len(m) for m in corpus):,}   "
          f"assist points {args.assist_points}")
    print(f"targets: " + "  ".join(f"{t} {targets[t]:.2%}" for t in TERMS))
    print(f"\n{'term':<10}{'share @1':>10}{'ratio pass':>12}{'its share':>11}"
          f"{'SOLVED':>10}{'its share':>11}")
    print("-" * 64)
    label = {"damage": "A damage", "assists": "D assists", "leverage": "B leverage", "econ": "C econ"}
    for term in TERMS:
        weight = "n/a" if ratio[term] is None else f"{ratio[term]:.3f}"
        print(f"{label[term]:<10}{unit[term]:>9.2%}{weight:>12}{ratio_shares[term]:>10.2%}"
              f"{solved[term]:>10.3f}{final[term]:>10.2%}")
    print(f"\nuse: A={solved['damage']:.3f}  D={solved['assists']:.3f}  "
          f"B={solved['leverage']:.3f}  C={solved['econ']:.3f}   (relative to A=1)")


if __name__ == "__main__":
    main()
