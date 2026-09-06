"""measure_value_destroyed_is_enemy_wealth.py

Part of the Impact measurement record. Supports: M10
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_value_destroyed_is_enemy_wealth.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()
FULL_BUY=4200
rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, team FROM match_players")).mappings()}
st = collections.defaultdict(dict)
for r in db.execute(text("SELECT round_id, match_player_id, loadout FROM round_player_stats")).mappings():
    st[r["round_id"]][r["match_player_id"]] = r["loadout"]
deaths = collections.defaultdict(list)
for k in db.execute(text("SELECT round_id, death_match_player_id FROM kill_events")).mappings():
    deaths[k["round_id"]].append(k["death_match_player_id"])
def winner(o):
    return "TEAM_1" if o and o.startswith("Team A") else ("TEAM_2" if o and o.startswith("Team B") else None)
def usable(r): return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])
by_match=collections.defaultdict(dict)
for rid,r in rounds.items(): by_match[r["match_id"]][r["round_number"]]=r

for RN in (2, 14):
    rows=[]
    for mid, rs in by_match.items():
        cur,nxt = rs.get(RN), rs.get(RN+1)
        if not cur or not nxt or not usable(cur) or not usable(nxt): continue
        d=st.get(cur["id"],{})
        if not d: continue
        w,wn = winner(cur["outcome"]), winner(nxt["outcome"])
        for team in ("TEAM_1","TEAM_2"):
            other="TEAM_2" if team=="TEAM_1" else "TEAM_1"
            victims=[v for v in deaths.get(cur["id"],[]) if v and v in mp and mp[v]["team"]==other]
            their=[lo for m,lo in d.items() if m in mp and mp[m]["team"]==other]
            if not their: continue
            rows.append({"destroyed": sum(d.get(v,0) for v in victims),
                         "n_killed": len(victims),
                         "their_total": sum(their),
                         "won_cur": w==team, "won_nxt": wn==team})
    sub=[r for r in rows if r["won_cur"]]
    v=sorted(r["destroyed"] for r in sub); cuts=[v[int(len(v)*f)] for f in (.25,.5,.75)]
    print("="*86)
    print(f"ROUND {RN}, teams that WON it  (n={len(sub):,})   quartile cuts in credits: {cuts}")
    print("="*86)
    print(f"  {'bucket':<7} {'credits destroyed':<22} {'n':>6} {'mean enemies':>13} {'mean cr':>9} "
          f"{'their total':>12} {'win N+1':>9}")
    prev=-1
    for i,lab in enumerate(("Q1","Q2","Q3","Q4")):
        hi=cuts[i] if i<3 else 10**9
        g=[r for r in sub if prev<=r["destroyed"]<hi] if hi!=10**9 else [r for r in sub if r["destroyed"]>=prev]
        rng=f"{max(prev,0):,} - {'max' if hi==10**9 else format(hi,',')}"
        print(f"  {lab:<7} {rng:<22} {len(g):>6,} {statistics.mean(r['n_killed'] for r in g):>13.2f} "
              f"{statistics.mean(r['destroyed'] for r in g):>9,.0f} "
              f"{statistics.mean(r['their_total'] for r in g):>12,.0f} "
              f"{100*sum(r['won_nxt'] for r in g)/len(g):>8.1f}%")
        prev=hi
    print(f"  (their total = the enemy team's whole loadout value that round, for scale)")
db.close()
