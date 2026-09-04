"""Read-only exploration: the SITE FIGHT window around the plant,
[plant - 30s, plant + 15s]. Kills and deaths INSIDE the window only.
Phantom plants (round ends in a Time Win => bomb never armed) excluded.

Part of the Impact measurement record. Supports: M8
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_window_composition.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal()

PRE, POST = 30.0, 15.0

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, round_number, outcome, planted, plant_time, exploded, defused FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, player_id, team FROM match_players")).mappings()}
pname = {r["id"]: r["display_name"] for r in db.execute(text(
    "SELECT id, display_name FROM players")).mappings()}
kills = collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events ORDER BY round_id, event_time_seconds, id""")).mappings():
    kills[k["round_id"]].append(dict(k))

def atk_team(rn):
    if 1 <= rn <= 12: return "TEAM_1"
    if 13 <= rn <= 24: return "TEAM_2"
    return None
def winner(o):
    return "TEAM_1" if o and o.startswith("Team A") else ("TEAM_2" if o and o.startswith("Team B") else None)

# ---- hygiene ----
planted_all = [r for r in rounds.values() if r["planted"] and r["plant_time"] is not None]
phantom = [r for r in planted_all if r["outcome"] and "Time Win" in r["outcome"]]
usable  = [r for r in planted_all if r not in phantom and atk_team(r["round_number"]) is not None]
print("="*72)
print(f"planted rounds total       : {len(planted_all)}")
print(f"  phantom (Time Win outcome): {len(phantom)}   <- excluded")
print(f"  overtime (rd>24)          : {sum(1 for r in planted_all if r['round_number']>24)}   <- excluded (no side convention)")
print(f"  usable                    : {len(usable)}")
print("="*72)

usable_ids = {r["id"] for r in usable}

# ---- window events ----
sub_labels = ["-30..-20", "-20..-10", "-10..-5", "-5..0", "0..+5", "+5..+15"]
def sub(d):
    if d < -20: return "-30..-20"
    if d < -10: return "-20..-10"
    if d < -5:  return "-10..-5"
    if d < 0:   return "-5..0"
    if d < 5:   return "0..+5"
    return "+5..+15"

by_sub = collections.defaultdict(lambda: {"atk_kills": 0, "def_kills": 0})
# per (player, round) window tallies, split by side
tally = collections.defaultdict(lambda: {"k": 0, "d": 0})
round_side_won = {}

for rid in usable_ids:
    r = rounds[rid]; pt = r["plant_time"]; atk = atk_team(r["round_number"]); w = winner(r["outcome"])
    if w is None: continue
    round_side_won[rid] = (atk, w)
    for k in kills.get(rid, []):
        kid, vid = k["killer_match_player_id"], k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp: continue
        if mp[kid]["team"] == mp[vid]["team"]: continue
        d = k["event_time_seconds"] - pt
        if not (-PRE <= d <= POST): continue
        s = sub(d)
        if mp[kid]["team"] == atk: by_sub[s]["atk_kills"] += 1
        else: by_sub[s]["def_kills"] += 1
        tally[(rid, kid)]["k"] += 1
        tally[(rid, vid)]["d"] += 1

print("\n(1) WHERE THE FIGHT HAPPENS — kills inside the window, by sub-bucket and side")
print(f"{'bucket':>10} | {'attacker kills':>15} | {'defender kills':>15} | {'def share':>9}")
tot_a = tot_d = 0
for s in sub_labels:
    a, dk = by_sub[s]["atk_kills"], by_sub[s]["def_kills"]
    tot_a += a; tot_d += dk
    print(f"{s:>10} | {a:>15} | {dk:>15} | {100*dk/(a+dk) if a+dk else 0:>8.1f}%")
print(f"{'TOTAL':>10} | {tot_a:>15} | {tot_d:>15} | {100*tot_d/(tot_a+tot_d):>8.1f}%")

# ---- per-side exchange, and whether it tracks winning ----
print("\n(2) DEFENDER exchange inside the window, split by whether defenders held the round")
for label, want_def_win in (("defenders WON the round", True), ("defenders LOST the round", False)):
    ks = ds = 0
    for (rid, mpid), t in tally.items():
        atk, w = round_side_won[rid]
        if mp[mpid]["team"] == atk: continue
        def_won = (w != atk)
        if def_won != want_def_win: continue
        ks += t["k"]; ds += t["d"]
    print(f"  {label:>26}: kills {ks:>6}  deaths {ds:>6}  K/D {ks/ds if ds else 0:.3f}")

# ---- per-player defender window exchange ----
per_player = collections.defaultdict(lambda: {"k": 0, "d": 0, "rounds": 0})
for (rid, mpid), t in tally.items():
    atk, w = round_side_won[rid]
    if mp[mpid]["team"] == atk: continue
    p = per_player[mp[mpid]["player_id"]]
    p["k"] += t["k"]; p["d"] += t["d"]; p["rounds"] += 1
qual = [(v["k"]/v["d"], v["k"], v["d"], v["rounds"], pid)
        for pid, v in per_player.items() if v["d"] >= 40]
qual.sort(reverse=True)
print(f"\n(3) PER-PLAYER defender window K/D  ({len(qual)} players with >=40 window deaths)")
for kd, k, d, n, pid in qual[:6]:
    print(f"   {pname.get(pid,'?'):<22} K/D {kd:5.2f}   ({k:>4}K / {d:>4}D over {n} rounds)")
print("   ...")
for kd, k, d, n, pid in qual[-6:]:
    print(f"   {pname.get(pid,'?'):<22} K/D {kd:5.2f}   ({k:>4}K / {d:>4}D over {n} rounds)")
vals = [q[0] for q in qual]
print(f"   spread {min(vals):.2f} - {max(vals):.2f}, median {statistics.median(vals):.2f}")
db.close()
