"""Derive the attacking side from ROUND OUTCOMES instead of assuming it.
  Time Win  -> that team was DEFENDING (timer expired, no plant stuck)
  Bomb win  -> exploded => winner attacked; defused => winner defended
Then (a) validate the 1-12 / 13-24 convention, (b) settle OT.

Part of the Impact measurement record. Supports: M15, M16
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_attacking_side_and_phantoms.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()

rows = db.execute(text("""
    SELECT id, match_id, round_number, outcome, planted, plant_time, exploded, defused
    FROM rounds""")).mappings().all()
print(f"total rounds {len(rows)} | OT rounds (>24): {sum(1 for r in rows if r['round_number']>24)}")
print("\ndistinct outcome strings:")
for o, n in collections.Counter(r["outcome"] for r in rows).most_common():
    print(f"   {str(o):<34} {n}")

def winner(o):
    return "TEAM_1" if o and o.startswith("Team A") else ("TEAM_2" if o and o.startswith("Team B") else None)

def derived_attacker(r):
    """Which team was ATTACKING, derived from the outcome. None if undeterminable."""
    w = winner(r["outcome"])
    if w is None or not r["outcome"]: return None
    other = "TEAM_2" if w == "TEAM_1" else "TEAM_1"
    o = r["outcome"]
    if "Time Win" in o:  return other      # winner defended
    if "Defuse" in o:    return other      # winner defended
    if "Detonate" in o or "Explo" in o: return w   # winner attacked
    return None                             # Elimination: ambiguous

def convention(rn):
    if 1 <= rn <= 12: return "TEAM_1"
    if 13 <= rn <= 24: return "TEAM_2"
    return None

agree = disagree = 0
for r in rows:
    if r["round_number"] > 24: continue
    d, c = derived_attacker(r), convention(r["round_number"])
    if d is None or c is None: continue
    if d == c: agree += 1
    else: disagree += 1
print(f"\n(a) CONVENTION CHECK on rounds 1-24 (only outcome-determinable rounds)")
print(f"    agrees {agree}   disagrees {disagree}   -> {100*agree/(agree+disagree):.2f}% agreement")

print(f"\n(b) OT ROUNDS: derived attacker by round number")
ot = collections.defaultdict(collections.Counter)
for r in rows:
    if r["round_number"] <= 24: continue
    d = derived_attacker(r)
    if d: ot[r["round_number"]][d] += 1
for rn in sorted(ot):
    c = ot[rn]; tot = sum(c.values())
    t1 = c.get("TEAM_1", 0)
    print(f"   round {rn:>2}: TEAM_1 attacked {t1:>4} / {tot:<4} ({100*t1/tot:5.1f}%)   TEAM_2 {tot-t1}")
db.close()
