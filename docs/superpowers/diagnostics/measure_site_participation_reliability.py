"""Site-participation split-half reliability on the FULL dataset, bootstrapped by
PLAYER (the unit of analysis here), with the split procedure made explicit.

Part of the Impact measurement record. Supports: M17
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_site_participation_reliability.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal(); PRE, POST = 30.0, 15.0

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT r.id, r.round_number, r.outcome, r.planted, r.plant_time, m.played_at
    FROM rounds r JOIN matches m ON m.id=r.match_id""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, player_id, team FROM match_players")).mappings()}
def atk(rn):
    if rn<=12: return "TEAM_1"
    if rn<=24: return "TEAM_2"
    return "TEAM_1" if (rn-25)%2==0 else "TEAM_2"

ev=collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, event_time_seconds
    FROM kill_events""")).mappings():
    r=rounds.get(k["round_id"])
    if not r or not r["planted"] or r["plant_time"] is None or not r["played_at"]: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    kid=k["killer_match_player_id"]
    if not kid or kid not in mp: continue
    if mp[kid]["team"]==atk(r["round_number"]): continue     # defender kills only
    d=k["event_time_seconds"]-r["plant_time"]
    ev[mp[kid]["player_id"]].append((r["played_at"], -PRE<=d<=POST))

def pear(x,y):
    n=len(x); mx,my=sum(x)/n,sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    dx=sum((a-mx)**2 for a in x)**.5; dy=sum((b-my)**2 for b in y)**.5
    return cov/(dx*dy) if dx and dy else float('nan')

print("="*84)
print("SITE-PARTICIPATION SPLIT-HALF RELIABILITY -- full 3,124 matches")
print("  split = CHRONOLOGICAL (each player's own events ordered by match date, halved at their")
print("  median match); bootstrap resamples PLAYERS, since the player is the unit of analysis.")
print("="*84)
rng=random.Random(11)
for MIN in (30,50,80):
    pairs=[]
    for pid,evs in ev.items():
        evs=sorted(evs,key=lambda t:t[0])
        ts=sorted({t for t,_ in evs})
        if len(ts)<4: continue
        cut=ts[len(ts)//2]
        a=[e for e in evs if e[0]<cut]; b=[e for e in evs if e[0]>=cut]
        if len(a)<MIN or len(b)<MIN: continue
        pairs.append((sum(e[1] for e in a)/len(a), sum(e[1] for e in b)/len(b)))
    if len(pairs)<12: continue
    r=pear([p[0] for p in pairs],[p[1] for p in pairs])
    boots=[]
    for _ in range(2000):
        s=[pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        try: boots.append(pear([p[0] for p in s],[p[1] for p in s]))
        except Exception: pass
    boots=[b for b in boots if b==b]; boots.sort()
    lo,hi=boots[int(.025*len(boots))],boots[int(.975*len(boots))]
    sb=2*r/(1+r) if r>-1 else float('nan')
    print(f"  min {MIN:>2} kills/half: n={len(pairs):>3} players   r={r:+.3f}  95% CI [{lo:+.3f},{hi:+.3f}]"
          f"   Spearman-Brown={sb:+.3f}")
    print(f"       (1,151-match subset previously reported: "
          f"{'+0.440 at min 30' if MIN==30 else '+0.517 at min 50' if MIN==50 else 'not measured'})")
db.close()
