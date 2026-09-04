"""Does the plant-proximity curve hold OUTSIDE the four even states it was fitted
on, and does it differ for attackers vs defenders? Statistic = the LIFT from the
far bucket (<-30s) to the near bucket (-10..-5s), bootstrapped by match.

Part of the Impact measurement record. Supports: M3, M4
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_state_and_side_interaction.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, team FROM match_players")).mappings()}
kills = collections.defaultdict(list)
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
for rid, ks in kills.items():
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
        if dt>=0: 
            if alive[vt]>0: alive[vt]-=1
            continue
        recs.append({"mid":r["match_id"], "adv":alive[kt]-alive[vt], "dt":dt,
                     "killer_is_atk": kt==a, "won": kt==w})
        if alive[vt]>0: alive[vt]-=1

rng=random.Random(31)
def lift(rows):
    """win-rate difference between the -10..-5 bucket and the <-30 bucket, boot by match."""
    far =[r for r in rows if r["dt"]< -30]
    near=[r for r in rows if -10<=r["dt"]< -5]
    if len(far)<80 or len(near)<80: return None
    def bym(rr):
        d=collections.defaultdict(lambda:[0,0])
        for x in rr: d[x["mid"]][0]+=x["won"]; d[x["mid"]][1]+=1
        return list(d.values())
    F,N=bym(far),bym(near); ds=[]
    for _ in range(1500):
        h=n=0
        for _ in range(len(F)):
            a,b=F[rng.randrange(len(F))]; h+=a; n+=b
        p1=h/n if n else 0
        h=n=0
        for _ in range(len(N)):
            a,b=N[rng.randrange(len(N))]; h+=a; n+=b
        ds.append((h/n if n else 0)-p1)
    ds.sort()
    fr=sum(x["won"] for x in far)/len(far); nr=sum(x["won"] for x in near)/len(near)
    return (fr, nr, nr-fr, ds[37], ds[1462], len(far), len(near))

def show(lab, rows):
    r=lift(rows)
    if r is None: print(f"  {lab:<30} (insufficient n)"); return
    fr,nr,d,lo,hi,nf,nn = r
    flag = "" if lo>0 else "   <-- CI SPANS 0"
    print(f"  {lab:<30} far {100*fr:5.1f}% -> near {100*nr:5.1f}%   "
          f"lift {100*d:+5.1f}pp [{100*lo:+5.1f},{100*hi:+5.1f}]  n={nf:,}/{nn:,}{flag}")

print("="*104)
print("(1) DOES THE CURVE HOLD OUTSIDE EVEN STATES?  lift from <-30s to -10..-5s, by man-advantage")
print("="*104)
for lab,f in (("EVEN (fitted on these)", lambda r: r["adv"]==0),
              ("killer AHEAD by 1",      lambda r: r["adv"]==1),
              ("killer AHEAD by 2+",     lambda r: r["adv"]>=2),
              ("killer BEHIND by 1",     lambda r: r["adv"]==-1),
              ("killer BEHIND by 2+",    lambda r: r["adv"]<=-2)):
    show(lab, [r for r in recs if f(r)])

print("\n" + "="*104)
print("(2) ATTACKER vs DEFENDER -- do they need separate curves?")
print("="*104)
for lab,f in (("ATTACKER kills, even",   lambda r: r["killer_is_atk"] and r["adv"]==0),
              ("DEFENDER kills, even",   lambda r: not r["killer_is_atk"] and r["adv"]==0),
              ("ATTACKER kills, all",    lambda r: r["killer_is_atk"]),
              ("DEFENDER kills, all",    lambda r: not r["killer_is_atk"])):
    show(lab, [r for r in recs if f(r)])
db.close()
