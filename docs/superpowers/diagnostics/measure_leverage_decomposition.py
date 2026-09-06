"""Can the leverage component be split into STATE and TIMING for evaluation?

The scoring formula fuses them: leverage = kill_order_bonus * time_factor, stored
as `time_impact` (impact.py:674). The evaluation harness does NOT have to use the
same shape, and a split would let it ask a question the fused column cannot:
does the time modulation add anything on top of the raw kill-order bonus?

  kill_order_bonus  -- what the kill was worth for the STATE it happened in
  time_delta        -- what it was worth extra (or less) for WHEN it happened
  the two sum back to time_impact exactly, so nothing is invented

THE DECISION THIS INFORMS. If the two columns are near-collinear the split
answers nothing and the harness list should stay fused (Option A). If they carry
separable variance, the split is worth one new scorer field (Option B).

HOW kill_order_bonus IS OBTAINED WITHOUT REIMPLEMENTING ANYTHING. The scorer is
replayed twice per match: once normally, and once with `_time_factor` pinned to
1.0. With every time factor equal to 1, `time_impact` reduces to
kill_order_bonus * 1 summed the same way -- so run 2 IS the raw kill-order
bonus, exactly, computed by the real scorer rather than a copy of it that could
drift. time_delta is then run1 - run2 per (round, player).

Run in EX-ANTE mode (use_realized_swing=False), because that is the mode the
harness actually fits in, and it is the mode where the question matters: with
the pre-plant factor returning exactly 1.0 there, time_delta is non-zero only
for POST-PLANT kills -- which is precisely the column the plant-window spec's
Part 4 non-inferiority gate needs to read.

Part of the Impact measurement record. Supports: M29 (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_leverage_decomposition.py

Reads only; never writes to the database.

COMPUTE BOUND, NOT A SELECTION. This replays through the ORM twice per match, so
it runs on a capped sample of matches rather than all 3,124 -- the same
restriction M14 carries and for the same reason. The quantity measured is a
property of the FORMULA's shape rather than of the data distribution, so the cap
is unlikely to matter, but it has not been verified on the full set.
"""
import os, sys, random, math, collections

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal
from app.scoring import impact as impact_mod
from app.scoring.impact import build_impact_rows_for_match

MATCH_CAP = 400
N_BOOT, SEED = 800, 31337

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches")
if n_matches < 3000:
    print("*** REFUSING TO RUN: expected the full ~3,124-match set. ***")
    sys.exit(1)

match_ids = [r[0] for r in db.execute(text(
    "SELECT DISTINCT m.id FROM matches m JOIN rounds r ON r.match_id = m.id "
    "ORDER BY m.id")).all()]
rng = random.Random(SEED)
sample = sorted(rng.sample(match_ids, min(MATCH_CAP, len(match_ids))))
print(f"replaying {len(sample):,} matches twice each (compute bound; see docstring)")

_real_time_factor = impact_mod._time_factor
rows = []
for i, mid in enumerate(sample):
    if i % 100 == 0 and i:
        print(f"  ... {i}/{len(sample)}")
    try:
        normal = build_impact_rows_for_match(db, mid, use_realized_swing=False)
        impact_mod._time_factor = lambda *a, **k: 1.0
        try:
            pinned = build_impact_rows_for_match(db, mid, use_realized_swing=False)
        finally:
            impact_mod._time_factor = _real_time_factor
    except Exception as exc:
        print(f"  skipped match {mid}: {type(exc).__name__}")
        impact_mod._time_factor = _real_time_factor
        continue

    base = {(r.round_id, r.match_player_id): r for r in pinned}
    for r in normal:
        b = base.get((r.round_id, r.match_player_id))
        if b is None:
            continue
        rows.append({
            "mid": mid,
            "leverage": float(r.time_impact),          # kill_order_bonus * time_factor
            "kob": float(b.time_impact),               # kill_order_bonus, exactly
            "delta": float(r.time_impact) - float(b.time_impact),
            "damage": float(r.damage),
        })
db.close()

print(f"\nsample: {len(rows):,} player-rounds over "
      f"{len(set(r['mid'] for r in rows)):,} matches")


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def boot_corr(subset, ka, kb, seed=SEED):
    """Match-level bootstrap of a correlation: resample MATCHES, not rows."""
    by_match = collections.defaultdict(list)
    for r in subset:
        by_match[r["mid"]].append(r)
    keys = list(by_match)
    if len(keys) < 2:
        return None
    point = pearson([r[ka] for r in subset], [r[kb] for r in subset])
    rg = random.Random(seed)
    out = []
    for _ in range(N_BOOT):
        xs, ys = [], []
        for _ in range(len(keys)):
            for r in by_match[keys[rg.randrange(len(keys))]]:
                xs.append(r[ka])
                ys.append(r[kb])
        c = pearson(xs, ys)
        if not math.isnan(c):
            out.append(c)
    if not out:
        return None
    out.sort()
    return (point, out[int(.025 * len(out))], out[int(.975 * len(out))])


print("\n" + "=" * 96)
print("THE DECISION NUMBER -- corr(kill_order_bonus, time_delta) across player-rounds")
print("=" * 96)
c = boot_corr(rows, "kob", "delta")
print(f"  corr(kob, delta)      = {c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}]")
c2 = boot_corr(rows, "kob", "leverage")
print(f"  corr(kob, leverage)   = {c2[0]:+.4f} [{c2[1]:+.4f}, {c2[2]:+.4f}]   (the fused column)")
c3 = boot_corr(rows, "damage", "delta")
print(f"  corr(damage, delta)   = {c3[0]:+.4f} [{c3[1]:+.4f}, {c3[2]:+.4f}]")
c4 = boot_corr(rows, "damage", "kob")
print(f"  corr(damage, kob)     = {c4[0]:+.4f} [{c4[1]:+.4f}, {c4[2]:+.4f}]")

zero = sum(1 for r in rows if r["delta"] == 0)
print(f"\n  player-rounds with delta exactly 0: {zero:,} / {len(rows):,} "
      f"= {100*zero/len(rows):.1f}%")
print("  (ex-ante mode: the pre-plant factor is exactly 1.0, so delta is non-zero")
print("   ONLY where the player had post-plant involvement)")

nz = [r for r in rows if r["delta"] != 0]
if len(nz) > 100:
    cn = boot_corr(nz, "kob", "delta")
    print(f"\n  restricted to the {len(nz):,} rows where delta != 0:")
    print(f"    corr(kob, delta)    = {cn[0]:+.4f} [{cn[1]:+.4f}, {cn[2]:+.4f}]")

vals = sorted(r["delta"] for r in rows)
print(f"\n  delta spread: p05={vals[int(.05*len(vals))]:+.0f}  "
      f"p50={vals[len(vals)//2]:+.0f}  p95={vals[int(.95*len(vals))]:+.0f}  "
      f"min={vals[0]:+.0f}  max={vals[-1]:+.0f}")

print("\n" + "=" * 96)
print("READING THIS. The existing components correlate 0.73-0.90, which is the")
print("collinearity the econ spec exists to escape. If corr(kob, delta) sits well")
print("below that band the split carries separable variance and Option B is worth")
print("one new scorer field. If it approaches it, the split answers nothing and the")
print("harness list should stay fused (Option A).")
