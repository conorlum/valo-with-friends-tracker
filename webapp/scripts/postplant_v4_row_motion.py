r"""Run from webapp/, read-only:

    DATABASE_URL="$PROD" .\.venv313\Scripts\python.exe scripts\postplant_v4_row_motion.py \
        --out DIR --arms PC,P1,P2b,P3b

How much stored Impact each declared arm would actually move. Section 8 of the
declaration (docs/superpowers/2026-09-07-predeclared-values.md, entry
"2026-09-19 -- DECLARATION: the post-plant time factor") makes this load-bearing:
where every Tier B arm fails, a version 4 is recommended on the Tier A fixes
alone only if their combination moves ">= 1% of impact_scores rows, or >= 5% of
matches reordered by within-match player rank". Those two numbers come from here.

This is NOT an arm contrast and carries no verdict. An out-of-fold contrast says
whether a change predicts better; this says whether it changes anything worth
rescoring for. The whole point of the Part 4 episode is that the second question
is not evidence for the first -- 81.5% of matches reordered under a shape worth
nothing out of fold.

One pass over the corpus, scoring each match once per arm while it is loaded.
Read-only; nothing is written to the database.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.scoring.impact import build_impact_rows_for_match

import postplant_v4_variants as variants


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def rank_order(rows):
    """Players of a match ordered by mean Impact, which is what the site shows
    and therefore what "reordered" has to mean here."""
    totals, counts = defaultdict(float), defaultdict(int)
    for row in rows:
        totals[row.match_player_id] += row.impact
        counts[row.match_player_id] += 1
    means = {p: totals[p] / counts[p] for p in totals if counts[p]}
    return [p for p, _ in sorted(means.items(), key=lambda kv: (-kv[1], kv[0]))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--arms", default="PC,P1,P2b,P3a,P3b")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    arms = [a for a in args.arms.split(",") if a]

    db = SessionLocal()
    db.rollback()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()

    match_ids = [r[0] for r in db.execute(text("SELECT id FROM matches ORDER BY id"))]
    if args.limit:
        match_ids = match_ids[-args.limit:]
    log(f"{len(match_ids):,} matches; arms {arms}")

    variants.install()
    stats = {arm: {"rows": 0, "rows_changed": 0, "matches_changed": 0,
                   "matches_reordered": 0, "abs_delta_sum": 0.0,
                   "max_abs_delta": 0.0} for arm in arms}
    total_rows = 0
    t0 = time.time()

    for index, match_id in enumerate(match_ids, 1):
        variants.activate(None)
        base = build_impact_rows_for_match(db, match_id)
        if not base:
            continue
        base_by_key = {(r.match_player_id, r.round_id): r.impact for r in base}
        base_order = rank_order(base)
        total_rows += len(base)

        for arm in arms:
            variants.activate(variants.variant_for(arm))
            rows = build_impact_rows_for_match(db, match_id)
            changed = 0
            for row in rows:
                before = base_by_key.get((row.match_player_id, row.round_id))
                if before is None or before != row.impact:
                    changed += 1
                    if before is not None:
                        delta = abs(row.impact - before)
                        stats[arm]["abs_delta_sum"] += delta
                        stats[arm]["max_abs_delta"] = max(stats[arm]["max_abs_delta"], delta)
            stats[arm]["rows"] += len(rows)
            stats[arm]["rows_changed"] += changed
            if changed:
                stats[arm]["matches_changed"] += 1
                if rank_order(rows) != base_order:
                    stats[arm]["matches_reordered"] += 1

        db.rollback()  # no snapshot held open across the corpus
        if index % 250 == 0:
            rate = (time.time() - t0) / index
            log(f"  {index:,}/{len(match_ids):,} matches, {rate:.2f}s each, "
                f"~{rate * (len(match_ids) - index) / 60:.0f} min left")

    variants.activate(None)
    variants.uninstall()
    db.rollback()
    db.close()

    report = {"matches": len(match_ids), "rows_per_arm_baseline": total_rows, "arms": {}}
    print()
    print("=" * 78)
    print("ROW MOTION -- how much stored Impact each arm would move")
    print("NOT a contrast, and no verdict. Motion is not improvement.")
    print("=" * 78)
    print(f"  {len(match_ids):,} matches, {total_rows:,} rows under P0")
    print()
    print(f"  {'arm':<8} {'rows changed':>16} {'matches changed':>17} "
          f"{'reordered':>12} {'mean |delta|':>13}")
    for arm in arms:
        s = stats[arm]
        mean_delta = s["abs_delta_sum"] / s["rows_changed"] if s["rows_changed"] else 0.0
        report["arms"][arm] = {
            "rows_changed": s["rows_changed"],
            "rows_changed_pct": 100 * s["rows_changed"] / total_rows if total_rows else 0,
            "matches_changed": s["matches_changed"],
            "matches_changed_pct": 100 * s["matches_changed"] / len(match_ids),
            "matches_reordered": s["matches_reordered"],
            "matches_reordered_pct": 100 * s["matches_reordered"] / len(match_ids),
            "mean_abs_delta_on_changed_rows": mean_delta,
            "max_abs_delta": s["max_abs_delta"],
        }
        r = report["arms"][arm]
        print(f"  {arm:<8} {s['rows_changed']:>9,} ({r['rows_changed_pct']:>4.2f}%) "
              f"{s['matches_changed']:>10,} ({r['matches_changed_pct']:>4.1f}%) "
              f"{s['matches_reordered']:>6,} ({r['matches_reordered_pct']:>4.1f}%) "
              f"{mean_delta:>12.1f}")

    (out_dir / "row_motion.json").write_text(json.dumps(report, indent=2))
    print()
    print(f"  written to {out_dir / 'row_motion.json'}")


if __name__ == "__main__":
    main()
