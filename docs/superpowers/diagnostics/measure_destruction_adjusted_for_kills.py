"""Is M12's destruction effect just kill count and round-N outcome in disguise?

M12 compares LOW vs HIGH total destroyed value. High destruction co-occurs with
killing more enemies, winning round N, and reaching favourable man-advantage --
all of which predict winning N+1 on their own. If the effect vanishes once
those are held fixed, the econ component's fitted weights would be
reintroducing the kill-count correlation the whole redesign exists to escape.

Part of the Impact measurement record. Supports: M12 (adjusted)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_destruction_adjusted_for_kills.py

Reads only; never writes to the database.
"""
import os, sys, collections, random
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal

db = SessionLocal(); FULL_BUY = 4200; rng = random.Random(909)

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

def usable(r):
    return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])

by_match = collections.defaultdict(dict)
for rid, r in rounds.items():
    by_match[r["match_id"]][r["round_number"]] = r

def fb(rid, team):
    d = st.get(rid, {}); v = [lo for m, lo in d.items() if m in mp and mp[m]["team"] == team]
    return sum(1 for x in v if x >= FULL_BUY) if v else None

def boot(obs, n=1500):
    if not obs: return None
    d = collections.defaultdict(lambda: [0, 0])
    for mid, hit in obs:
        d[mid][0] += bool(hit); d[mid][1] += 1
    ks = list(d); th = sum(d[k][0] for k in ks); tn = sum(d[k][1] for k in ks)
    out = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(ks)):
            a, b = d[ks[rng.randrange(len(ks))]]; h += a; t += b
        if t: out.append(h / t)
    out.sort()
    return (th / tn, out[int(.025 * len(out))], out[int(.975 * len(out))], tn)

def boot_delta(a, b, n=1500):
    def by_m(o):
        d = collections.defaultdict(lambda: [0, 0])
        for mid, hit in o:
            d[mid][0] += bool(hit); d[mid][1] += 1
        return list(d.values())
    A, B = by_m(a), by_m(b)
    if not A or not B: return None
    ds = []
    for _ in range(n):
        h = t = 0
        for _ in range(len(A)):
            x, y = A[rng.randrange(len(A))]; h += x; t += y
        p1 = h / t if t else 0
        h = t = 0
        for _ in range(len(B)):
            x, y = B[rng.randrange(len(B))]; h += x; t += y
        ds.append((h / t if t else 0) - p1)
    ds.sort()
    return (sum(x[1] for x in b) / len(b) - sum(x[1] for x in a) / len(a),
            ds[int(.025 * len(ds))], ds[int(.975 * len(ds))])

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn in (12, 24) or rn > 24: continue
        cur, nxt = rs.get(rn), rs.get(rn + 1)
        if not cur or not nxt or not usable(cur) or not usable(nxt): continue
        d = st.get(cur["id"], {})
        if not d: continue
        w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
        for team in ("TEAM_1", "TEAM_2"):
            other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
            f = fb(cur["id"], other)
            if f is None: continue
            victims = [v for v in deaths.get(cur["id"], []) if v and v in mp and mp[v]["team"] == other]
            rows.append({"mid": mid, "rn": rn, "enemy_fb_N": f,
                         "destroyed": sum(d.get(v, 0) for v in victims),
                         "n_killed": len(victims),
                         "won_cur": w == team, "won_nxt": wn == team})

print("=" * 100)
print("M12 ADJUSTED -- does destruction still predict once ENEMIES KILLED and ROUND-N OUTCOME are held fixed?")
print(f"sample as M11/M12: n={len(rows):,} team-rounds, {len(set(r['mid'] for r in rows)):,} matches")
print("=" * 100)

print("\nSTEP 1 -- how confounded is it? correlation of destroyed with the suspects")
import statistics as S
dv = [r["destroyed"] for r in rows]
def corr(a, b):
    ma, mb = S.mean(a), S.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** .5; dbb = sum((y - mb) ** 2 for y in b) ** .5
    return num / (da * dbb) if da and dbb else float("nan")
print(f"   corr(destroyed, enemies killed) = {corr(dv, [r['n_killed'] for r in rows]):+.3f}")
print(f"   corr(destroyed, won round N)    = {corr(dv, [1.0 if r['won_cur'] else 0.0 for r in rows]):+.3f}")

print("\nSTEP 2 -- LOW vs HIGH destroyed WITHIN a fixed number of enemies killed and round-N outcome")
print("   (terciles of destroyed formed inside each cell; delta bootstrapped by match)")
for won in (True, False):
    print(f"\n  ---- teams that {'WON' if won else 'LOST'} round N ----")
    print(f"  {'enemies killed':>15} {'LOW destroyed':>22} {'HIGH destroyed':>22} {'delta':>26}")
    for nk in range(1, 6):
        cell = [r for r in rows if r["won_cur"] == won and r["n_killed"] == nk]
        if len(cell) < 400: continue
        v = sorted(r["destroyed"] for r in cell)
        c = [v[int(len(v) * f)] for f in (.33, .67)]
        lo = [(r["mid"], r["won_nxt"]) for r in cell if r["destroyed"] < c[0]]
        hi = [(r["mid"], r["won_nxt"]) for r in cell if r["destroyed"] >= c[1]]
        if len(lo) < 100 or len(hi) < 100: continue
        bl, bh = boot(lo), boot(hi); dd = boot_delta(lo, hi)
        flag = "EXCLUDES 0" if (dd[1] > 0 or dd[2] < 0) else "spans 0"
        print(f"  {nk:>15} "
              f"{100*bl[0]:5.1f}% n={bl[3]:<6,}".rjust(23)
              + f"{100*bh[0]:5.1f}% n={bh[3]:<6,}".rjust(23)
              + f"{100*dd[0]:+5.1f}pp [{100*dd[1]:+5.1f},{100*dd[2]:+5.1f}] {flag}".rjust(27))

print("\nSTEP 3 -- the same, additionally within enemy buy state (the weights M12 would set)")
bands = [("broke 0-1", 0, 1), ("partial 2-3", 2, 3), ("full 4-5", 4, 5)]
print(f"  {'buy state':>12} {'enemies killed':>15} {'delta (HIGH-LOW), won round N':>34}")
for blab, blo, bhi in bands:
    for nk in (4, 5):
        cell = [r for r in rows if r["won_cur"] and blo <= r["enemy_fb_N"] <= bhi and r["n_killed"] == nk]
        if len(cell) < 400: continue
        v = sorted(r["destroyed"] for r in cell)
        c = [v[int(len(v) * f)] for f in (.33, .67)]
        lo = [(r["mid"], r["won_nxt"]) for r in cell if r["destroyed"] < c[0]]
        hi = [(r["mid"], r["won_nxt"]) for r in cell if r["destroyed"] >= c[1]]
        if len(lo) < 80 or len(hi) < 80: continue
        dd = boot_delta(lo, hi)
        flag = "EXCLUDES 0" if (dd[1] > 0 or dd[2] < 0) else "spans 0"
        print(f"  {blab:>12} {nk:>15}   {100*dd[0]:+5.1f}pp [{100*dd[1]:+5.1f},{100*dd[2]:+5.1f}]  {flag}"
              f"   n={len(lo):,}/{len(hi):,}")
db.close()
