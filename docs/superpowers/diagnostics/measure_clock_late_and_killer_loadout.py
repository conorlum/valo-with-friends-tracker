"""Re-run every measurement that lacked cell counts, intervals, or an explicit
sample definition (Sol #9). Full 3,124-match dataset. Every cell reports
n_observations / n_matches and a match-level bootstrap 95% CI.

Part of the Impact measurement record. Supports: M2, M7, M9
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_clock_late_and_killer_loadout.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal

db = SessionLocal()
FULL_BUY = 4200
rng = random.Random(101)

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, team FROM match_players")).mappings()}
st = collections.defaultdict(dict)
for r in db.execute(text("SELECT round_id, match_player_id, loadout FROM round_player_stats")).mappings():
    st[r["round_id"]][r["match_player_id"]] = r["loadout"]
kills = collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events ORDER BY round_id, event_time_seconds, id""")).mappings():
    kills[k["round_id"]].append(dict(k))


def winner(o):
    if not o: return None
    return "TEAM_1" if o.startswith("Team A") else ("TEAM_2" if o.startswith("Team B") else None)

def usable(r):
    return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])

def real_plant(r):
    return r["planted"] and r["plant_time"] is not None and not (r["outcome"] and "Time Win" in r["outcome"])

def boot(obs, n=2000):
    """obs: [(match_id, hit)] -> (rate, lo, hi, n_obs, n_matches)"""
    if not obs: return None
    d = collections.defaultdict(lambda: [0, 0])
    for mid, hit in obs:
        d[mid][0] += bool(hit); d[mid][1] += 1
    ks = list(d); th = sum(d[k][0] for k in ks); tn = sum(d[k][1] for k in ks)
    if not tn: return None
    out = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(ks)):
            a, b = d[ks[rng.randrange(len(ks))]]; h += a; t += b
        if t: out.append(h / t)
    out.sort()
    return (th / tn, out[int(.025 * len(out))], out[int(.975 * len(out))], tn, len(ks))

def boot_delta(obs_a, obs_b, n=2000):
    """CI on rate(b) - rate(a), resampling matches independently in each arm."""
    def by_m(o):
        d = collections.defaultdict(lambda: [0, 0])
        for mid, hit in o:
            d[mid][0] += bool(hit); d[mid][1] += 1
        return list(d.values())
    A, B = by_m(obs_a), by_m(obs_b)
    if not A or not B: return None
    ds = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(A)):
            a, b = A[rng.randrange(len(A))]; h += a; t += b
        p1 = h / t if t else 0
        h = t = 0
        for _ in range(len(B)):
            a, b = B[rng.randrange(len(B))]; h += a; t += b
        ds.append((h / t if t else 0) - p1)
    ds.sort()
    ra = sum(x[1] for x in obs_a) / len(obs_a); rb = sum(x[1] for x in obs_b) / len(obs_b)
    return (rb - ra, ds[int(.025 * len(ds))], ds[int(.975 * len(ds))])

def cell(b):
    return f"{100*b[0]:5.1f}% [{100*b[1]:5.1f},{100*b[2]:5.1f}] n={b[3]:,}/{b[4]}m" if b else "--"

# ---- build the kill-level record once ----
recs = []
for rid, ks in kills.items():
    r = rounds.get(rid)
    if not r or not usable(r): continue
    w = winner(r["outcome"]); d = st.get(rid, {})
    planted = real_plant(r)
    fb = collections.Counter()
    for m, lo in d.items():
        if m in mp and lo >= FULL_BUY: fb[mp[m]["team"]] += 1
    alive = {"TEAM_1": 5, "TEAM_2": 5}
    for k in ks:
        kid, vid = k["killer_match_player_id"], k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp: continue
        kt, vt = mp[kid]["team"], mp[vid]["team"]
        if kt == vt: continue
        recs.append({
            "mid": r["match_id"], "rn": r["round_number"], "t": k["event_time_seconds"],
            "planted": planted, "dt": (k["event_time_seconds"] - r["plant_time"]) if planted else None,
            "state": (alive[kt], alive[vt]), "ctx": fb[kt] - fb[vt],
            "kl": d.get(kid, 0), "won": kt == w,
        })
        if alive[vt] > 0: alive[vt] -= 1

print("=" * 100)
print("PROVENANCE RE-RUN -- full dataset, all cells with n_obs/n_matches and 95% CI")
print(f"non-self kills in usable rounds: {len(recs):,}")
print("=" * 100)

# ---------- M2 ----------
print("\nM2 -- ABSOLUTE ROUND CLOCK, pre-plant kills in PLANTED rounds, even states")
print("     sample: non-self pre-plant kills, non-phantom planted rounds, killer alive == victim alive")
B = [("<20s", 0, 20), ("20-35", 20, 35), ("35-50", 35, 50), ("50-65", 50, 65), (">=65", 65, 1e9)]
print(f"  {'state':>6} | " + " | ".join(f"{b[0]:>26}" for b in B))
for stv in [(5,5),(4,4),(3,3),(2,2)]:
    row = []
    for lab, lo, hi in B:
        g = [x for x in recs if x["planted"] and x["dt"] is not None and x["dt"] < 0
             and x["state"] == stv and lo <= x["t"] < hi]
        b = boot([(x["mid"], x["won"]) for x in g]) if len(g) >= 60 else None
        row.append(cell(b).rjust(26))
    print(f"  {stv[0]}v{stv[1]:<4} | " + " | ".join(row))

# ---------- M7 ----------
print("\nM7 -- LATE KILLS IN NEVER-PLANTED ROUNDS (t >= 70s), by state")
late = collections.defaultdict(list)
for x in recs:
    if not x["planted"] and x["t"] >= 70:
        late[f"{x['state'][0]}v{x['state'][1]}"].append((x["mid"], x["won"]))
print("     sample: non-self kills at t>=70s in rounds that were never planted")
tot = sum(len(v) for v in late.values())
print(f"     total such kills: {tot:,}")
for s_, obs in sorted(late.items(), key=lambda kv: -len(kv[1]))[:8]:
    b = boot(obs)
    if b and b[3] >= 60: print(f"     {s_:>5}  killer's team won {cell(b)}")
print("     baseline for comparison, even states, t<50s in never-planted rounds:")
for stv in ("2v2", "3v3"):
    base = [(x["mid"], x["won"]) for x in recs if not x["planted"] and x["t"] < 50
            and f"{x['state'][0]}v{x['state'][1]}" == stv]
    lateo = late.get(stv, [])
    b1, b2 = boot(base), boot(lateo)
    if b1 and b2 and b2[3] >= 60:
        d = boot_delta(base, lateo)
        print(f"     {stv}: early {cell(b1)}  late {cell(b2)}")
        print(f"          delta {100*d[0]:+.1f}pp [{100*d[1]:+.1f},{100*d[2]:+.1f}]"
              f"{'  EXCLUDES 0' if d[1]>0 or d[2]<0 else '  spans 0'}")

# ---------- M9 ----------
print("\nM9 -- KILLER'S OWN LOADOUT within a fixed TEAM full-buy differential, even states")
print("     sample: non-self kills, killer alive == victim alive; ctx = killer's team full-buys")
print("             minus victim's team full-buys, both in the round the kill occurs")
def band(v): return "poor" if v < 2100 else ("mid" if v < 4250 else "rich")
even = [x for x in recs if x["state"][0] == x["state"][1]]
print(f"  {'team ctx':>9} | " + " | ".join(f"killer={c:>4}".rjust(26) for c in ("poor","mid","rich")))
for lab, lo, hi in (("-5..-3",-5,-3),("-2..-1",-2,-1),("0",0,0),("+1..+2",1,2),("+3..+5",3,5)):
    row = []
    for kb in ("poor","mid","rich"):
        g = [x for x in even if lo <= x["ctx"] <= hi and band(x["kl"]) == kb]
        row.append(cell(boot([(x["mid"], x["won"]) for x in g]) if len(g) >= 60 else None).rjust(26))
    print(f"  {lab:>9} | " + " | ".join(row))
db.close()
