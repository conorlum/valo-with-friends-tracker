"""Is the time x man-advantage interaction a SIMPLE function, or 10 free curves?
If the proximity lift is (say) linear in advantage, this is 2-3 parameters.

Part of the Impact measurement record. Supports: M3, M4
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_interaction_functional_form.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()
rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text("SELECT id, team FROM match_players")).mappings()}
kills=collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events ORDER BY round_id, event_time_seconds, id""")).mappings():
    kills[k["round_id"]].append(dict(k))
def winner(o):
    return "TEAM_1" if o and o.startswith("Team A") else ("TEAM_2" if o and o.startswith("Team B") else None)
def atk(rn):
    if rn<=12: return "TEAM_1"
    if rn<=24: return "TEAM_2"
    return "TEAM_1" if (rn-25)%2==0 else "TEAM_2"

recs=[]
for rid,ks in kills.items():
    r=rounds.get(rid); w=winner(r["outcome"]) if r else None
    if w is None or not r["planted"] or r["plant_time"] is None: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    a=atk(r["round_number"]); alive={"TEAM_1":5,"TEAM_2":5}
    for k in ks:
        kid,vid=k["killer_match_player_id"],k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp: continue
        kt,vt=mp[kid]["team"],mp[vid]["team"]
        if kt==vt: continue
        dt=k["event_time_seconds"]-r["plant_time"]
        if dt<0:
            recs.append({"mid":r["match_id"],"adv":alive[kt]-alive[vt],"dt":dt,
                         "is_atk":kt==a,"won":kt==w})
        if alive[vt]>0: alive[vt]-=1

rng=random.Random(41)
def lift(rows):
    far=[r for r in rows if r["dt"]<-30]; near=[r for r in rows if -10<=r["dt"]<-5]
    if len(far)<60 or len(near)<60: return None
    def bym(rr):
        d=collections.defaultdict(lambda:[0,0])
        for x in rr: d[x["mid"]][0]+=x["won"]; d[x["mid"]][1]+=1
        return list(d.values())
    F,N=bym(far),bym(near); ds=[]
    for _ in range(1200):
        h=n=0
        for _ in range(len(F)):
            a,b=F[rng.randrange(len(F))]; h+=a; n+=b
        p1=h/n if n else 0
        h=n=0
        for _ in range(len(N)):
            a,b=N[rng.randrange(len(N))]; h+=a; n+=b
        ds.append((h/n if n else 0)-p1)
    ds.sort()
    return (sum(x["won"] for x in near)/len(near)-sum(x["won"] for x in far)/len(far),
            ds[30], ds[1169], len(far)+len(near))

print("="*88)
print("PROXIMITY LIFT BY EXACT MAN-ADVANTAGE  (is it a simple function?)")
print("="*88)
print(f"  {'advantage':>10} {'lift':>10} {'95% CI':>20} {'n':>10}")
pts=[]
for adv in range(-4,5):
    r=lift([x for x in recs if x["adv"]==adv])
    if r:
        print(f"  {adv:>+10} {100*r[0]:>+9.1f}pp [{100*r[1]:+5.1f},{100*r[2]:+5.1f}]".ljust(44)+f"{r[3]:>10,}")
        pts.append((adv, r[0]))
if len(pts)>=4:
    n=len(pts); sx=sum(p[0] for p in pts); sy=sum(p[1] for p in pts)
    sxx=sum(p[0]**2 for p in pts); sxy=sum(p[0]*p[1] for p in pts)
    b=(n*sxy-sx*sy)/(n*sxx-sx*sx); a=(sy-b*sx)/n
    ss_t=sum((p[1]-sy/n)**2 for p in pts); ss_r=sum((p[1]-(a+b*p[0]))**2 for p in pts)
    print(f"\n  LINEAR FIT of lift on advantage:  lift = {100*a:+.1f}pp {100*b:+.1f}pp per man"
          f"   R^2 = {1-ss_r/ss_t:.3f}")
    print(f"  residuals: " + ", ".join(f"{p[0]:+d}:{100*(p[1]-(a+b*p[0])):+.1f}" for p in pts))

print("\n" + "="*88)
print("SAME, SPLIT BY SIDE -- does side need its own slope, or just its own intercept?")
print("="*88)
for slab, sf in (("ATTACKER", lambda x: x["is_atk"]), ("DEFENDER", lambda x: not x["is_atk"])):
    print(f"  --- {slab} ---")
    sp=[]
    for adv in range(-3,4):
        r=lift([x for x in recs if x["adv"]==adv and sf(x)])
        if r:
            print(f"    adv {adv:>+2}: lift {100*r[0]:>+6.1f}pp [{100*r[1]:+5.1f},{100*r[2]:+5.1f}]  n={r[3]:,}")
            sp.append((adv,r[0]))
    if len(sp)>=3:
        n=len(sp); sx=sum(p[0] for p in sp); sy=sum(p[1] for p in sp)
        sxx=sum(p[0]**2 for p in sp); sxy=sum(p[0]*p[1] for p in sp)
        b=(n*sxy-sx*sy)/(n*sxx-sx*sx); a=(sy-b*sx)/n
        print(f"    -> slope {100*b:+.1f}pp/man, intercept {100*a:+.1f}pp")
db.close()
