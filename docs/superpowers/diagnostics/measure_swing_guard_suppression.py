"""How often does _combine_swing_factors discard the realized signal?
Uses the real functions from app.scoring.impact.

Part of the Impact measurement record. Supports: M14
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_swing_guard_suppression.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
from app.models import MatchPlayer, Round
from app.models.match import Team
from app.scoring.impact import (
    _econ_swing_risk_factor, _realized_econ_swing_factor, _combine_swing_factors)
db = SessionLocal()

MATCH_LIMIT = 900   # keep the ORM replay bounded; sampled, not exhaustive
match_ids = [m for (m,) in db.execute(text(
    "SELECT id FROM matches ORDER BY played_at DESC LIMIT :n"), {"n": MATCH_LIMIT})]

tally = collections.Counter()
pairs = []
for mid in match_ids:
    rounds = db.query(Round).filter_by(match_id=mid).order_by(Round.round_number).all()
    if not rounds: continue
    mps = {mp.id: mp for mp in db.query(MatchPlayer).filter_by(match_id=mid).all()}
    rn_by_id = {r.id: r.round_number for r in rounds}
    outcomes = {r.round_number: r.outcome for r in rounds}
    rps = collections.defaultdict(dict)
    for s in db.execute(text("""SELECT rps.round_id, rps.match_player_id, rps.score, rps.kills,
              rps.deaths, rps.assists, rps.loadout, rps.remaining
        FROM round_player_stats rps JOIN rounds r ON r.id=rps.round_id
        WHERE r.match_id=:m"""), {"m": mid}).mappings():
        rps[rn_by_id[s["round_id"]]][s["match_player_id"]] = dict(s)
    for r in rounds:
        if r.outcome is None or "Surrendered" in (r.outcome or ""): continue
        for team in (Team.TEAM_1, Team.TEAM_2):
            try:
                ex = _econ_swing_risk_factor(outcomes, rps, mps, r.round_number, team, r)
                rz = _realized_econ_swing_factor(rps, mps, r.round_number, team)
            except Exception:
                continue
            comb = _combine_swing_factors(ex, rz)
            if ex == 1: tally["ex-ante exactly 1.0"] += 1
            elif rz == 1: tally["realized exactly 1.0"] += 1
            elif (ex > 1) != (rz > 1): tally["DISAGREE -> discarded"] += 1
            else: tally["agree -> kept"] += 1
            pairs.append((ex, rz, comb))

tot = sum(tally.values())
print("="*74)
print(f"_combine_swing_factors outcomes over {tot:,} team-rounds ({len(match_ids)} recent matches)")
print("="*74)
for k, v in tally.most_common():
    print(f"  {k:<28} {v:>8,}   {100*v/tot:>5.1f}%")
neutral = tot - tally["agree -> kept"]
print(f"\n  -> returns a NEUTRAL 1.0 in {neutral:,} of {tot:,} = {100*neutral/tot:.1f}% of team-rounds")
print(f"  -> the realized signal survives in only {100*tally['agree -> kept']/tot:.1f}%")

kept=[p for p in pairs if p[2]!=1.0]
print(f"\n  when kept, combined factor: min {min(p[2] for p in kept):.2f} "
      f"max {max(p[2] for p in kept):.2f} mean {statistics.mean(p[2] for p in kept):.3f}")
rz_all=[p[1] for p in pairs]
print(f"  realized factor alone      : min {min(rz_all):.2f} max {max(rz_all):.2f} "
      f"mean {statistics.mean(rz_all):.3f}")
ex_all=[p[0] for p in pairs]
print(f"  ex-ante factor alone       : min {min(ex_all):.2f} max {max(ex_all):.2f} "
      f"mean {statistics.mean(ex_all):.3f}")

# do they even agree?
both=[(e,r) for e,r in zip(ex_all,rz_all) if e!=1 and r!=1]
agree=sum(1 for e,r in both if (e>1)==(r>1))
print(f"\n  where both are informative (n={len(both):,}): they agree {100*agree/len(both):.1f}% "
      f"of the time -- {100-100*agree/len(both):.1f}% is thrown away")
db.close()
