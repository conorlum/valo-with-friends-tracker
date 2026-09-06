"""Bootstrap the time spec's load-bearing tables, on the FULL 3,124-match data.
(1) plant-proximity curve  (2) the OT 'six for six' claim  (3) site-participation reliability.

Part of the Impact measurement record. Supports: M1, M6
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_proximity_curve_and_overtime.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import boot_rate
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal(); PRE, POST = 30.0, 15.0

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, player_id, team, agent FROM match_players")).mappings()}
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
    if w is None or (r["outcome"] and "Surrendered" in r["outcome"]): continue
    if r["planted"] and r["outcome"] and "Time Win" in r["outcome"]: continue
    pt = r["plant_time"] if r["planted"] else None
    a=atk(r["round_number"]); alive={"TEAM_1":5,"TEAM_2":5}
    for k in ks:
        kid,vid=k["killer_match_player_id"],k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp: continue
        kt,vt=mp[kid]["team"],mp[vid]["team"]
        if kt==vt: continue
        recs.append({"mid":r["match_id"],"ot":r["round_number"]>24,"state":(alive[kt],alive[vt]),
            "dt":(k["event_time_seconds"]-pt) if pt is not None else None,
            "def_kill": kt!=a, "won": kt==w})
        if alive[vt]>0: alive[vt]-=1

print("="*104)
print("(1) PLANT-PROXIMITY CURVE, full 3,124 matches, match-level bootstrap 95% CI")
print("="*104)
B=[("<-30",-1e9,-30),("-30..-20",-30,-20),("-20..-10",-20,-10),("-10..-5",-10,-5),("-5..0",-5,0)]
print(f"  {'state':>6} | " + " | ".join(f"{b[0]:>20}" for b in B))
for stv in [(5,5),(4,4),(3,3),(2,2)]:
    cells=[]
    for lab,lo,hi in B:
        g=[x for x in recs if x["state"]==stv and x["dt"] is not None and lo<=x["dt"]<hi]
        r=boot_rate([(x["mid"],x["won"]) for x in g])
        cells.append(f"{100*r[0]:5.1f} [{100*r[1]:4.1f},{100*r[2]:4.1f}]".rjust(20) if r and r[3]>=100 else f"{'--':>20}")
    print(f"  {stv[0]}v{stv[1]:<4} | " + " | ".join(cells))

print("\n" + "="*104)
print("(2) THE 'SIX FOR SIX' OT CLAIM: defender share of window kills, regulation vs OT, WITH CI on the difference")
print("="*104)
S=[("-30..-20",-30,-20),("-20..-10",-20,-10),("-10..-5",-10,-5),("-5..0",-5,0),("0..+5",0,5),("+5..+15",5,15)]
rng=random.Random(23)
print(f"  {'bucket':>10} | {'regulation':>20} | {'overtime':>20} | {'difference (OT - reg)':>26}")
sig=0
for lab,lo,hi in S:
    reg=[x for x in recs if not x["ot"] and x["dt"] is not None and lo<=x["dt"]<hi]
    ot =[x for x in recs if     x["ot"] and x["dt"] is not None and lo<=x["dt"]<hi]
    if len(ot)<40: print(f"  {lab:>10} |  (insufficient OT)"); continue
    br=boot_rate([(x["mid"],x["def_kill"]) for x in reg]); bo=boot_rate([(x["mid"],x["def_kill"]) for x in ot])
    # bootstrap the difference, resampling matches within each stratum
    def by_m(rows):
        d=collections.defaultdict(lambda:[0,0])
        for x in rows: d[x["mid"]][0]+=x["def_kill"]; d[x["mid"]][1]+=1
        return list(d.values())
    R,O=by_m(reg),by_m(ot); diffs=[]
    for _ in range(2000):
        h=n=0
        for _ in range(len(R)):
            a,b=R[rng.randrange(len(R))]; h+=a; n+=b
        p1=h/n if n else 0
        h=n=0
        for _ in range(len(O)):
            a,b=O[rng.randrange(len(O))]; h+=a; n+=b
        p2=h/n if n else 0
        diffs.append(p2-p1)
    diffs.sort(); dlo,dhi=diffs[50],diffs[1949]
    star="  EXCLUDES 0" if (dlo>0 or dhi<0) else "  spans 0"
    if dlo>0 or dhi<0: sig+=1
    print(f"  {lab:>10} | {100*br[0]:5.1f}% n={br[3]:<7,}| {100*bo[0]:5.1f}% n={bo[3]:<6,}| "
          f"{100*(bo[0]-br[0]):+5.1f}pp [{100*dlo:+5.1f},{100*dhi:+5.1f}]{star}")
print(f"\n  -> {sig} of 6 buckets have a difference whose 95% CI excludes zero")
db.close()
