"""(1) WITHIN-PLAYER role comparison: same person, different agent. Controls for
       player identity, which the pooled between-player comparison does not.
   (2) DEATH share by role -- an anchor may hold site and die there without the kill.

Part of the Impact measurement record. Supports: M18
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_agent_role_within_player.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()
PRE, POST = 30.0, 15.0

ROLE = {}
for r, names in {
 "Duelist":   "Jett Reyna Phoenix Raze Yoru Neon Iso Waylay",
 "Initiator": "Sova Breach Skye KAY/O Fade Gekko Tejo",
 "Controller":"Brimstone Omen Viper Astra Harbor Clove Miks",
 "Sentinel":  "Sage Cypher Killjoy Chamber Deadlock Vyse Veto",
}.items():
    for n in names.split(): ROLE[n] = r

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, round_number, outcome, planted, plant_time FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, player_id, team, agent FROM match_players")).mappings()}
pname = {r["id"]: r["display_name"] for r in db.execute(text(
    "SELECT id, display_name FROM players")).mappings()}
def atk_team(rn):
    if 1 <= rn <= 12: return "TEAM_1"
    if 13 <= rn <= 24: return "TEAM_2"
    return "TEAM_1" if rn % 2 == 1 else "TEAM_2"

# (player, role) -> [kills_in_window, kills_tot, deaths_in_window, deaths_tot]
cell = collections.defaultdict(lambda: [0, 0, 0, 0])
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events""")).mappings():
    r = rounds.get(k["round_id"])
    if not r or not r["planted"] or r["plant_time"] is None: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    kid, vid = k["killer_match_player_id"], k["death_match_player_id"]
    if not kid or not vid or kid not in mp or vid not in mp: continue
    if mp[kid]["team"] == mp[vid]["team"]: continue
    atk = atk_team(r["round_number"])
    inw = (-PRE <= k["event_time_seconds"] - r["plant_time"] <= POST)
    for who, is_kill in ((kid, True), (vid, False)):
        if mp[who]["team"] == atk: continue                    # defenders only
        role = ROLE.get(mp[who]["agent"])
        if role is None: continue
        c = cell[(mp[who]["player_id"], role)]
        if is_kill: c[0] += inw; c[1] += 1
        else:       c[2] += inw; c[3] += 1

MIN = 40
print("="*78)
print(f"(1) WITHIN-PLAYER paired role differences in site-participation (kill share)")
print(f"    each player contributes only where they have >={MIN} defender kills on BOTH roles")
print("="*78)
share = {(p, r): c[0]/c[1] for (p, r), c in cell.items() if c[1] >= MIN}
players = collections.defaultdict(dict)
for (p, r), s in share.items(): players[p][r] = s
roles = ["Sentinel", "Controller", "Initiator", "Duelist"]
print(f"{'pair':<26} {'n players':>9} {'mean diff':>10} {'median':>8}  {'>0':>5}")
for i in range(len(roles)):
    for j in range(i+1, len(roles)):
        a, b = roles[i], roles[j]
        d = [players[p][a]-players[p][b] for p in players if a in players[p] and b in players[p]]
        if len(d) < 5: 
            print(f"{a+' - '+b:<26} {len(d):>9}   (too few)")
            continue
        print(f"{a+' - '+b:<26} {len(d):>9} {100*statistics.mean(d):>+9.1f}pp "
              f"{100*statistics.median(d):>+7.1f}pp {sum(1 for x in d if x>0):>3}/{len(d)}")

print("\n" + "="*78)
print("(2) POOLED role shares: KILLS vs DEATHS in the window (does the anchor show in deaths?)")
print("="*78)
agg = collections.defaultdict(lambda: [0,0,0,0])
for (p, r), c in cell.items():
    for i in range(4): agg[r][i] += c[i]
print(f"{'role':<12} {'kill share':>11} {'n':>8} | {'death share':>12} {'n':>8} | {'death-kill':>11}")
for r in roles:
    kw, kt, dw, dt = agg[r]
    ks, ds = kw/kt, dw/dt
    print(f"{r:<12} {100*ks:>10.1f}% {kt:>8} | {100*ds:>11.1f}% {dt:>8} | {100*(ds-ks):>+10.1f}pp")

print("\n" + "="*78)
print("(3) WITHIN-PLAYER paired role differences in DEATH share")
print("="*78)
dshare = {(p, r): c[2]/c[3] for (p, r), c in cell.items() if c[3] >= MIN}
dplayers = collections.defaultdict(dict)
for (p, r), s in dshare.items(): dplayers[p][r] = s
for i in range(len(roles)):
    for j in range(i+1, len(roles)):
        a, b = roles[i], roles[j]
        d = [dplayers[p][a]-dplayers[p][b] for p in dplayers if a in dplayers[p] and b in dplayers[p]]
        if len(d) < 5: continue
        print(f"{a+' - '+b:<26} {len(d):>9} {100*statistics.mean(d):>+9.1f}pp "
              f"{100*statistics.median(d):>+7.1f}pp {sum(1 for x in d if x>0):>3}/{len(d)}")
db.close()
