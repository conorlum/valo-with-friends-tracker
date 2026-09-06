"""M11, M12, M13 with explicit sample definitions, cell counts, and delta CIs.

Part of the Impact measurement record. Supports: M11, M12, M13
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_denial_destruction_and_path.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal

db = SessionLocal(); FULL_BUY = 4200; rng = random.Random(202)
rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, team FROM match_players")).mappings()}
st = collections.defaultdict(dict)
for r in db.execute(text("SELECT round_id, match_player_id, loadout FROM round_player_stats")).mappings():
    st[r["round_id"]][r["match_player_id"]] = r["loadout"]
deaths = collections.defaultdict(list)
for k in db.execute(text("SELECT round_id, death_match_player_id FROM kill_events")).mappings():
    deaths[k["round_id"]].append(k["death_match_player_id"])

def winner(o):
    if not o: return None
    return "TEAM_1" if o.startswith("Team A") else ("TEAM_2" if o.startswith("Team B") else None)
def usable(r): return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])
by_match = collections.defaultdict(dict)
for rid, r in rounds.items(): by_match[r["match_id"]][r["round_number"]] = r
def fb(rid, team):
    d = st.get(rid, {}); v = [lo for m, lo in d.items() if m in mp and mp[m]["team"] == team]
    return sum(1 for x in v if x >= FULL_BUY) if v else None

def boot(obs, n=2000):
    if not obs: return None
    d = collections.defaultdict(lambda: [0, 0])
    for mid, hit in obs: d[mid][0] += bool(hit); d[mid][1] += 1
    ks = list(d); th = sum(d[k][0] for k in ks); tn = sum(d[k][1] for k in ks)
    out = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(ks)):
            a, b = d[ks[rng.randrange(len(ks))]]; h += a; t += b
        if t: out.append(h / t)
    out.sort()
    return (th / tn, out[int(.025*len(out))], out[int(.975*len(out))], tn, len(ks))
def boot_delta(a, b, n=2000):
    def by_m(o):
        d = collections.defaultdict(lambda: [0, 0])
        for mid, hit in o: d[mid][0] += bool(hit); d[mid][1] += 1
        return list(d.values())
    A, B = by_m(a), by_m(b); ds = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(A)):
            x, y = A[rng.randrange(len(A))]; h += x; t += y
        p1 = h/t if t else 0
        h = t = 0
        for _ in range(len(B)):
            x, y = B[rng.randrange(len(B))]; h += x; t += y
        ds.append((h/t if t else 0) - p1)
    ds.sort()
    return (sum(x[1] for x in b)/len(b) - sum(x[1] for x in a)/len(a),
            ds[int(.025*len(ds))], ds[int(.975*len(ds))])

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn in (12, 24) or rn > 24: continue
        cur, nxt = rs.get(rn), rs.get(rn+1)
        if not cur or not nxt or not usable(cur) or not usable(nxt): continue
        d = st.get(cur["id"], {})
        if not d: continue
        w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
        for team in ("TEAM_1", "TEAM_2"):
            other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
            fbn, fbx = fb(cur["id"], other), fb(nxt["id"], other)
            if fbn is None or fbx is None: continue
            rows.append({"mid": mid, "rn": rn,
                "destroyed": sum(d.get(v, 0) for v in deaths.get(cur["id"], [])
                                 if v and v in mp and mp[v]["team"] == other),
                "enemy_fb_N": fbn, "enemy_fb_N1": fbx,
                "won_cur": w == team, "won_nxt": wn == team})

print("=" * 96)
print("M11 / M12 / M13 -- provenance re-run")
print(f"SAMPLE (shared): one row per (team, round N) where rounds N and N+1 both exist, both")
print(f"  usable (non-surrendered, decided), N not in (12,24), N<=24, and both teams have")
print(f"  round_player_stats. n = {len(rows):,} team-rounds, {len(set(r['mid'] for r in rows)):,} matches")
print("=" * 96)

print("\nM11 -- enemy full-buy count in round N+1 vs winning round N+1")
print("   NOTE: no restriction on round N's outcome; all rounds 2-11 and 14-23 included")
for k in range(6):
    g = [r for r in rows if r["enemy_fb_N1"] == k]
    b = boot([(r["mid"], r["won_nxt"]) for r in g])
    if b: print(f"   enemy full-buys N+1 = {k}:  win N+1 {100*b[0]:5.1f}% "
                f"[{100*b[1]:5.1f},{100*b[2]:5.1f}]  n={b[3]:,}/{b[4]}m")

print("\nM12 -- destruction tercile vs win N+1, conditioned on enemy buy state ENTERING ROUND N")
print("   terciles of `destroyed` (credits) are formed WITHIN each (round group x buy state) cell")
groups = [("2-4 / 14-16", {2,3,4,14,15,16}), ("5-7 / 17-19", {5,6,7,17,18,19}),
          ("8-11 / 20-23", {8,9,10,11,20,21,22,23})]
bands = [("broke 0-1", 0, 1), ("partial 2-3", 2, 3), ("full 4-5", 4, 5)]
for glab, gs in groups:
    for blab, lo, hi in bands:
        band = [r for r in rows if r["rn"] in gs and lo <= r["enemy_fb_N"] <= hi]
        if len(band) < 400: continue
        v = sorted(r["destroyed"] for r in band); c = [v[int(len(v)*f)] for f in (.33, .67)]
        low = [(r["mid"], r["won_nxt"]) for r in band if r["destroyed"] < c[0]]
        high = [(r["mid"], r["won_nxt"]) for r in band if r["destroyed"] >= c[1]]
        bl, bh = boot(low), boot(high)
        d = boot_delta(low, high)
        flag = "EXCLUDES 0" if (d[1] > 0 or d[2] < 0) else "spans 0"
        print(f"   {glab:<13} {blab:<12} cuts={c[0]:,}/{c[1]:,}")
        print(f"       LOW  {100*bl[0]:5.1f}% [{100*bl[1]:5.1f},{100*bl[2]:5.1f}] n={bl[3]:,}/{bl[4]}m"
              f"   HIGH {100*bh[0]:5.1f}% [{100*bh[1]:5.1f},{100*bh[2]:5.1f}] n={bh[3]:,}/{bh[4]}m")
        print(f"       delta {100*d[0]:+5.1f}pp [{100*d[1]:+5.1f},{100*d[2]:+5.1f}]  {flag}")

print("\nM13 -- enemy full-buys entering round 4/16, by their EXACT rounds 2-3 record")
print("   categories are mutually exclusive by construction (the pair of outcomes)")
m13 = []
for mid, rs in by_match.items():
    for base in (1, 13):
        r2, r3, r4 = rs.get(base+1), rs.get(base+2), rs.get(base+3)
        if not all((r2, r3, r4)) or not all(usable(x) for x in (r2, r3, r4)): continue
        for team in ("TEAM_1", "TEAM_2"):
            other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
            f4 = fb(r4["id"], other)
            if f4 is None: continue
            w2 = winner(r2["outcome"]) == other; w3 = winner(r3["outcome"]) == other
            m13.append({"mid": mid, "rec": (w2, w3), "fb4": f4})
labels = {(False,False): "lost BOTH rounds 2 and 3", (True,False): "won 2, lost 3",
          (False,True): "lost 2, won 3", (True,True): "won BOTH rounds 2 and 3"}
for rec, lab in labels.items():
    g = [r for r in m13 if r["rec"] == rec]
    if len(g) < 100: continue
    vals = [r["fb4"] for r in g]
    bs = []
    for _ in range(2000):
        s = [vals[rng.randrange(len(vals))] for _ in range(len(vals))]
        bs.append(sum(s)/len(s))
    bs.sort()
    print(f"   {lab:<26} mean enemy full-buys in R4 = {sum(vals)/len(vals):.2f} "
          f"[{bs[50]:.2f},{bs[1949]:.2f}]  n={len(g):,}")
db.close()
