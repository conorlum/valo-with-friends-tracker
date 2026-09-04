"""The SHARE has a random denominator (the player's own kill count), which adds
noise. A RATE over eligible rounds has a fixed denominator. Does that measure
better? Also: window DEATH rate, since deaths are more frequent than kills.

Part of the Impact measurement record. Supports: M17
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_rate_vs_share_denominator.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal(); PRE, POST = 30.0, 15.0

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT r.id, r.match_id, r.round_number, r.outcome, r.planted, r.plant_time, m.played_at
    FROM rounds r JOIN matches m ON m.id=r.match_id""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, player_id, team FROM match_players")).mappings()}
mp_by_match=collections.defaultdict(list)
for i,v in mp.items(): mp_by_match[v["match_id"]].append(i)
def atk(rn):
    if rn<=12: return "TEAM_1"
    if rn<=24: return "TEAM_2"
    return "TEAM_1" if (rn-25)%2==0 else "TEAM_2"

# eligible defensive planted rounds per player, and window kills / deaths in them
elig=collections.defaultdict(list)   # player -> [(played_at, win_kills, win_deaths, all_kills)]
per_round=collections.defaultdict(lambda: collections.defaultdict(lambda:[0,0,0]))
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events""")).mappings():
    r=rounds.get(k["round_id"])
    if not r or not r["planted"] or r["plant_time"] is None: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    a=atk(r["round_number"]); inw = -PRE<=(k["event_time_seconds"]-r["plant_time"])<=POST
    for who,idx in ((k["killer_match_player_id"],0),(k["death_match_player_id"],1)):
        if who and who in mp and mp[who]["team"]!=a:
            if inw: per_round[k["round_id"]][who][idx]+=1
            if idx==0: per_round[k["round_id"]][who][2]+=1

for rid,r in rounds.items():
    if not r["planted"] or r["plant_time"] is None: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    a=atk(r["round_number"])
    for m in mp_by_match[r["match_id"]]:
        if mp[m]["team"]==a: continue
        wk,wd,ak = per_round[rid].get(m,[0,0,0])
        elig[mp[m]["player_id"]].append((r["played_at"], wk, wd, ak))

def pear(x,y):
    n=len(x); mx,my=sum(x)/n,sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    dx=sum((a-mx)**2 for a in x)**.5; dy=sum((b-my)**2 for b in y)**.5
    return cov/(dx*dy) if dx and dy else float('nan')

MEASURES=[
 ("window-kill SHARE  (wk / all kills)", lambda h: (sum(e[1] for e in h), sum(e[3] for e in h))),
 ("window-kill RATE   (wk / round)",     lambda h: (sum(e[1] for e in h), len(h))),
 ("window-DEATH RATE  (wd / round)",     lambda h: (sum(e[2] for e in h), len(h))),
]
print("="*92)
print("ALTERNATIVE MEASURES -- split-half reliability, chronological AND random")
print("  denominator matters: SHARE divides by the player's own kills (itself random);")
print("  RATE divides by eligible rounds played (fixed, not random).")
print("="*92)
rng=random.Random(7)
for lab,fn in MEASURES:
    for MINR in (150, 300):
        chron=[]; rand=[]
        for p,e in elig.items():
            es=sorted(e,key=lambda t:t[0])
            if len(es)<2*MINR: continue
            half=len(es)//2
            a,b=es[:half],es[half:]
            na,da=fn(a); nb,dbb=fn(b)
            if da<20 or dbb<20: continue
            chron.append((na/da, nb/dbb))
            sh=es[:]; rng.shuffle(sh)
            ra,rb=sh[:half],sh[half:]
            na2,da2=fn(ra); nb2,db2=fn(rb)
            if da2<20 or db2<20: continue
            rand.append((na2/da2, nb2/db2))
        if len(chron)<15: continue
        rc=pear([x[0] for x in chron],[x[1] for x in chron])
        rr=pear([x[0] for x in rand],[x[1] for x in rand])
        print(f"  {lab:<38} >={MINR:>3} rounds/half  n={len(chron):>3}   "
              f"chrono r={rc:+.3f}   random r={rr:+.3f}")
db.close()
