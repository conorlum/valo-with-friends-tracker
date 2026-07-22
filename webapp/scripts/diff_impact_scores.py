import argparse
import csv as csv_module
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from scripts.snapshot_impact_scores import build_snapshot

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_DATA_REL_PATH = "webapp/seed_data/demo_matches.sql"

_INSERT_RE = re.compile(
    r"^INSERT INTO public\.impact_scores \(id, round_id, match_player_id, kill_impact, death_impact, impact, breakdown\) VALUES \("
    r"\d+, (\d+), (\d+), (-?[\d.]+), (-?[\d.]+), (-?[\d.]+), '(.*)'\);$",
    re.MULTILINE,
)


def _load_baseline_from_git_ref(git_ref: str) -> dict[tuple[int, int], dict]:
    result = subprocess.run(
        ["git", "show", f"{git_ref}:{_SEED_DATA_REL_PATH}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        check=True,
    )
    baseline = {}
    for match in _INSERT_RE.finditer(result.stdout):
        round_id, match_player_id, kill_impact, death_impact, impact, breakdown_raw = match.groups()
        breakdown = json.loads(breakdown_raw.replace("''", "'"))
        baseline[(int(round_id), int(match_player_id))] = {
            "kill_impact": float(kill_impact),
            "death_impact": float(death_impact),
            "impact": float(impact),
            "breakdown": breakdown,
        }
    return baseline


def _load_baseline_from_file(path: Path) -> dict[tuple[int, int], dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    return {
        (record["round_id"], record["match_player_id"]): {
            "kill_impact": record["kill_impact"],
            "death_impact": record["death_impact"],
            "impact": record["impact"],
            "breakdown": record["breakdown"],
        }
        for record in records
    }


def _rank(values: list[float]) -> list[float]:
    # Average rank for ties.
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(old: list[float], new: list[float]) -> float | None:
    n = len(old)
    if n < 2:
        return None
    old_ranks = _rank(old)
    new_ranks = _rank(new)
    d_squared_sum = sum((o - n_) ** 2 for o, n_ in zip(old_ranks, new_ranks))
    return 1 - (6 * d_squared_sum) / (n * (n**2 - 1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Diff current impact_scores against a baseline.")
    parser.add_argument("--baseline-file", type=Path, help="Path to a JSON snapshot from snapshot_impact_scores.py")
    parser.add_argument(
        "--baseline-git-ref",
        default=None,
        help="Git ref to read webapp/seed_data/demo_matches.sql from (default: HEAD, if --baseline-file isn't given)",
    )
    parser.add_argument("--csv", type=Path, help="Optional path to write the full per-row diff table")
    args = parser.parse_args()

    if args.baseline_file:
        baseline_desc = f"snapshot file {args.baseline_file}"
        baseline = _load_baseline_from_file(args.baseline_file)
    else:
        git_ref = args.baseline_git_ref or "HEAD"
        baseline_desc = f"git ref {git_ref}:{_SEED_DATA_REL_PATH}"
        baseline = _load_baseline_from_git_ref(git_ref)

    db = SessionLocal()
    try:
        current_records = build_snapshot(db)
    finally:
        db.close()

    current_by_key = {(r["round_id"], r["match_player_id"]): r for r in current_records}

    common_keys = sorted(set(baseline) & set(current_by_key))
    if not common_keys:
        print(f"No overlapping (round_id, match_player_id) rows between current DB and {baseline_desc}")
        sys.exit(1)

    diff_rows = []
    for key in common_keys:
        old = baseline[key]
        new = current_by_key[key]
        diff_rows.append(
            {
                "match": new["match_external_id"],
                "round": new["round_number"],
                "player": new["player_name"],
                "team": new["team"],
                "old_impact": old["impact"],
                "new_impact": new["impact"],
                "delta": new["impact"] - old["impact"],
                "old_breakdown": old["breakdown"] or {},
                "new_breakdown": new["breakdown"] or {},
            }
        )

    print(f"Comparing current DB against {baseline_desc}")
    print(f"Rows compared: {len(diff_rows)}\n")

    deltas = [row["delta"] for row in diff_rows]
    abs_deltas = [abs(d) for d in deltas]
    max_row = max(diff_rows, key=lambda r: abs(r["delta"]))
    print("=== Summary ===")
    print(f"mean delta:      {statistics.mean(deltas):8.2f}")
    print(f"mean abs delta:  {statistics.mean(abs_deltas):8.2f}")
    print(f"median abs delta:{statistics.median(abs_deltas):8.2f}")
    print(
        f"max abs delta:   {max_row['delta']:8.2f}  "
        f"({max_row['match']} R{max_row['round']} {max_row['player']}, "
        f"{max_row['old_impact']:.0f} -> {max_row['new_impact']:.0f})"
    )

    by_match: dict[str, list[dict]] = {}
    for row in diff_rows:
        by_match.setdefault(row["match"], []).append(row)
    correlations = []
    for match_name, rows in by_match.items():
        rho = _spearman([r["old_impact"] for r in rows], [r["new_impact"] for r in rows])
        if rho is not None:
            correlations.append(rho)
    if correlations:
        print(f"mean per-match Spearman rank correlation (old vs new impact): {statistics.mean(correlations):.3f}")

    print("\n=== Biggest movers (top 15 by |delta|) ===")
    for row in sorted(diff_rows, key=lambda r: abs(r["delta"]), reverse=True)[:15]:
        print(
            f"{row['match']:<20} R{row['round']:<3} {row['player']:<15} {row['team']:<8} "
            f"{row['old_impact']:7.0f} -> {row['new_impact']:7.0f}  (delta {row['delta']:+7.0f})"
        )

    print("\n=== Per-player average delta (sorted by magnitude) ===")
    by_player: dict[str, list[float]] = {}
    for row in diff_rows:
        by_player.setdefault(row["player"], []).append(row["delta"])
    player_avgs = sorted(
        ((player, statistics.mean(ds)) for player, ds in by_player.items()),
        key=lambda t: abs(t[1]),
        reverse=True,
    )
    for player, avg in player_avgs:
        print(f"{player:<20} avg delta {avg:+7.1f}  (n={len(by_player[player])})")

    print("\n=== Breakdown-component average deltas ===")
    old_keys = set()
    new_keys = set()
    for row in diff_rows:
        old_keys.update(row["old_breakdown"].keys())
        new_keys.update(row["new_breakdown"].keys())
    common_breakdown_keys = sorted(old_keys & new_keys)
    old_only = sorted(old_keys - new_keys)
    new_only = sorted(new_keys - old_keys)
    for bkey in common_breakdown_keys:
        vals = [
            row["new_breakdown"].get(bkey, 0) - row["old_breakdown"].get(bkey, 0)
            for row in diff_rows
            if isinstance(row["new_breakdown"].get(bkey), (int, float))
            and isinstance(row["old_breakdown"].get(bkey), (int, float))
        ]
        if vals:
            print(f"{bkey:<25} avg delta {statistics.mean(vals):+7.2f}")
    if old_only:
        print(f"old-only breakdown keys (retired): {', '.join(old_only)}")
    if new_only:
        print(f"new-only breakdown keys (added):   {', '.join(new_only)}")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv_module.DictWriter(
                f, fieldnames=["match", "round", "player", "team", "old_impact", "new_impact", "delta"]
            )
            writer.writeheader()
            for row in diff_rows:
                writer.writerow({k: row[k] for k in writer.fieldnames})
        print(f"\nFull per-row diff written to {args.csv}")


if __name__ == "__main__":
    main()
