"""Where does the mass actually sit? If few kills reach the extremes, a wide
bound is cosmetic; if many do, it re-orders the whole score.

Part of the Impact measurement record. Supports: M5
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_scalar_mass_distribution.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()
rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT id, round_number, outcome, planted, plant_time FROM rounds""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text("SELECT id, team FROM match_players")).mappings()}
kills=collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events ORDER BY round_id, event_time_seconds, id""")).mappings():
    kills[k["round_id"]].append(dict(k))
def atk(rn):
    if rn<=12: return "TEAM_1"
    if rn<=24: return "TEAM_2"
    return "TEAM_1" if (rn-25)%2==0 else "TEAM_2"
from app.scoring.impact import _KILL_ORDER_GRAPH as G
KOB={}
for a,b,d in G.edges(data=True): KOB[(a,b)]=d["weight"]

rows=[]; total_kills=0
for rid,ks in kills.items():
    r=rounds.get(rid)
    if not r or (r["outcome"] and "Surrendered" in r["outcome"]): continue
    planted = r["planted"] and r["plant_time"] is not None and not (r["outcome"] and "Time Win" in r["outcome"])
    a=atk(r["round_number"]); alive={"TEAM_1":5,"TEAM_2":5}
    for k in ks:
        kid,vid=k["killer_match_player_id"],k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp: continue
        kt,vt=mp[kid]["team"],mp[vid]["team"]
        if kt==vt: continue
        total_kills+=1
        dt = (k["event_time_seconds"]-r["plant_time"]) if planted else None
        rows.append({"adv":alive[kt]-alive[vt], "dt":dt, "is_atk":kt==a,
                     "preplant": dt is not None and dt<0})
        if alive[vt]>0: alive[vt]-=1

pre=[r for r in rows if r["preplant"]]
print("="*78)
print(f"MASS DISTRIBUTION   total non-self kills {total_kills:,}")
print(f"  pre-plant kills in planted rounds (the only ones a proximity scalar touches):")
print(f"    {len(pre):,}  = {100*len(pre)/total_kills:.1f}% of all kills")
print(f"  everything else keeps a scalar of exactly 1.0 by construction")
print("="*78)

print("\n  of those pre-plant kills, by man-advantage:")
c=collections.Counter(r["adv"] for r in pre)
for adv in sorted(c):
    if c[adv]>=200:
        print(f"    adv {adv:>+2}: {c[adv]:>7,}  {100*c[adv]/len(pre):>5.1f}% of pre-plant"
              f"  {100*c[adv]/total_kills:>5.1f}% of ALL kills")

print("\n  the cells a WIDE lower bound would actually hit (behind AND near the plant):")
near_behind=[r for r in pre if r["adv"]<=-2 and -10<=r["dt"]<-5]
near_behind_any=[r for r in pre if r["adv"]<=-2 and r["dt"]>=-10]
print(f"    adv<=-2 and -10..-5s : {len(near_behind):,}  = {100*len(near_behind)/total_kills:.2f}% of all kills")
print(f"    adv<=-2 and >=-10s   : {len(near_behind_any):,}  = {100*len(near_behind_any)/total_kills:.2f}% of all kills")
print("\n  the cells a WIDE upper bound would hit (ahead AND near the plant):")
for lab,f in (("adv>=+1 and -10..-5s", lambda r: r["adv"]>=1 and -10<=r["dt"]<-5),
              ("adv>=+1 and >=-10s",   lambda r: r["adv"]>=1 and r["dt"]>=-10)):
    g=[r for r in pre if f(r)]
    print(f"    {lab:<22} {len(g):>7,}  = {100*len(g)/total_kills:.2f}% of all kills")

print("\n" + "="*78)
print("SCALE COMPARISON")
print("="*78)
print(f"  kill-order graph range           40 - 250    ratio  6.2x")
print(f"  proposed scalar 0.6 - 1.5                    ratio  2.5x   -> graph stays primary")
print(f"  proposed scalar 0.2 - 1.7                    ratio  8.5x   -> SCALAR becomes primary")
print(f"  combined at 0.6-1.5:   24 - 375              ratio 15.6x")
print(f"  combined at 0.2-1.7:    8 - 425              ratio 53.1x")
db.close()
