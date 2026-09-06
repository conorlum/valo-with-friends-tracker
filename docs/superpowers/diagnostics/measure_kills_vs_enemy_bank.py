"""Does a kill deny a gun only when the enemy CANNOT REPLACE IT?

The mechanism: if the enemy has spent nearly everything (bank under ~500 each),
a kill destroys a gun they cannot rebuy, and k kills leaves them k guns short
next round. If they are sitting on cash, the same kill destroys a gun they
simply buy again. So the economic value of a kill should depend on the VICTIM
TEAM'S BANK, not on the credits nominally destroyed.

This is a THIRD axis, distinct from both prior econ measurements:
  M12/M12a  -- at fixed kill count, how RICH were the enemies you killed (dead)
  M12c      -- how MANY did you kill, kill count as exposure (holds late)
  here      -- could they REPLACE what you took, conditioned on their bank

`round_player_stats.remaining` is the bank. Verified: 95.8% of players are under
500 on round 1 and 97.6% on round 13, the two pistol resets, capped at 9,000.
Already load-bearing in impact.py:293-309 and credit_events.py:111.

Read the LOST rows first: losing round N closes the won-the-round path, leaving
the damage done on the way out. Differences ARE bootstrapped, not inferred from
overlapping intervals.

Part of the Impact measurement record. Supports: M12d (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_kills_vs_enemy_bank.py

Reads only; never writes to the database.

LIMITATION: bank, kills and round outcome are jointly determined within a round.
Conditioning on the round-N outcome and on the enemy's bank closes two paths,
not all -- man-advantage trajectory, plant status and map control remain
uncontrolled, as does the acting team's own spending decision except where
reported. Conditional associations, not effects.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal

ARMED = 2900        # rifle-tier loadout
FULL_BUY = 4200     # matches FULL_BUY_THRESHOLD usage elsewhere
N_BOOT, SEED = 800, 909

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches")
if n_matches < 3000:
    print("*** REFUSING TO RUN: expected the full ~3,124-match set. ***"); sys.exit(1)

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, team FROM match_players")).mappings()}
st = collections.defaultdict(dict)
for r in db.execute(text(
        "SELECT round_id, match_player_id, loadout, remaining FROM round_player_stats")).mappings():
    st[r["round_id"]][r["match_player_id"]] = (r["loadout"], r["remaining"])
deaths = collections.defaultdict(list)
for k in db.execute(text("SELECT round_id, death_match_player_id FROM kill_events")).mappings():
    deaths[k["round_id"]].append(k["death_match_player_id"])
db.close()


def winner(o):
    if not o: return None
    return "TEAM_1" if o.startswith("Team A") else ("TEAM_2" if o.startswith("Team B") else None)


def usable(r):
    return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])


by_match = collections.defaultdict(dict)
for rid, r in rounds.items():
    by_match[r["match_id"]][r["round_number"]] = r


def side(rid, team):
    d = st.get(rid, {})
    return [v for m, v in d.items() if m in mp and mp[m]["team"] == team]


GROUPS = [("2-4 / 14-16", {2, 3, 4, 14, 15, 16}),
          ("5-11 / 17-23", {5, 6, 7, 8, 9, 10, 11, 17, 18, 19, 20, 21, 22, 23})]
GROUP_OF = {rn: lab for lab, s in GROUPS for rn in s}

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn not in GROUP_OF: continue
        cur, nxt = rs.get(rn), rs.get(rn + 1)
        if not cur or not nxt or not usable(cur) or not usable(nxt): continue
        w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
        for team in ("TEAM_1", "TEAM_2"):
            other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
            e_cur, e_nxt = side(cur["id"], other), side(nxt["id"], other)
            u_cur, u_nxt = side(cur["id"], team), side(nxt["id"], team)
            if not (e_cur and e_nxt and u_cur and u_nxt): continue
            victims = [v for v in deaths.get(cur["id"], []) if v and v in mp and mp[v]["team"] == other]
            rows.append({
                "mid": mid, "g": GROUP_OF[rn],
                "n_killed": len(victims),
                "enemy_bank": sum(b for _, b in e_cur) / len(e_cur),
                "own_loadout": sum(l for l, _ in u_cur) / len(u_cur),
                "enemy_armed_next": sum(1 for l, _ in e_nxt if l >= ARMED),
                "enemy_fb_next": sum(1 for l, _ in e_nxt if l >= FULL_BUY),
                "own_armed_next": sum(1 for l, _ in u_nxt if l >= ARMED),
                "won_cur": w == team, "won_nxt": wn == team,
            })
            rows[-1]["gun_diff_next"] = rows[-1]["own_armed_next"] - rows[-1]["enemy_armed_next"]

print(f"sample: {len(rows):,} team-rounds over {len(set(r['mid'] for r in rows)):,} matches")

BANKS = [("committed <500", lambda b: b < 500),
         ("partial 500-1500", lambda b: 500 <= b < 1500),
         ("cash >=1500", lambda b: b >= 1500)]


def boot_mean(subset, key, seed=SEED):
    per = collections.defaultdict(lambda: [0.0, 0])
    for r in subset:
        per[r["mid"]][0] += r[key]; per[r["mid"]][1] += 1
    keys = list(per)
    if not keys: return None
    ts = sum(per[k][0] for k in keys); tn = sum(per[k][1] for k in keys)
    rng = random.Random(seed); out = []
    for _ in range(N_BOOT):
        s = n = 0.0
        for _ in range(len(keys)):
            a, b = per[keys[rng.randrange(len(keys))]]
            s += a; n += b
        if n: out.append(s / n)
    out.sort()
    return (ts / tn, out[int(.025*len(out))], out[int(.975*len(out))], int(tn))


def boot_diff(lo_rows, hi_rows, key, seed=SEED):
    """Bootstrap the DIFFERENCE of means, resampling matches once for both arms."""
    def per_match(rs):
        d = collections.defaultdict(lambda: [0.0, 0])
        for r in rs:
            d[r["mid"]][0] += r[key]; d[r["mid"]][1] += 1
        return d
    A, B = per_match(lo_rows), per_match(hi_rows)
    keys = sorted(set(A) | set(B))
    if not keys: return None
    def val(d, sel):
        s = n = 0.0
        for k in sel:
            if k in d:
                s += d[k][0]; n += d[k][1]
        return s / n if n else None
    point = (val(B, keys) or 0) - (val(A, keys) or 0)
    rng = random.Random(seed); out = []
    for _ in range(N_BOOT):
        sel = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
        a, b = val(A, sel), val(B, sel)
        if a is not None and b is not None:
            out.append(b - a)
    if not out: return None
    out.sort()
    return (point, out[int(.025*len(out))], out[int(.975*len(out))])


for glab, _ in GROUPS:
    print("\n" + "=" * 126)
    print(f"ROUNDS {glab} -- teams that LOST round N, by the ENEMY'S BANK in round N")
    print("=" * 126)
    for blab, bfn in BANKS:
        sub = [r for r in rows if r["g"] == glab and not r["won_cur"] and bfn(r["enemy_bank"])]
        if len(sub) < 400:
            print(f"\n  --- enemy bank {blab}: insufficient data ---"); continue
        print(f"\n  --- enemy bank {blab}  (n={len(sub):,}) ---")
        print(f"  {'kills':>6} | {'enemy ARMED next':>22} | {'enemy full-buy next':>22} | "
              f"{'gun diff next (you-them)':>26} | {'you win N+1':>14}")
        for nk in range(0, 6):
            cell = [r for r in sub if r["n_killed"] == nk]
            if len(cell) < 120:
                print(f"  {nk:>6} | {'--':>22} | {'--':>22} | {'--':>26} | {'--':>14}")
                continue
            a = boot_mean(cell, "enemy_armed_next"); f = boot_mean(cell, "enemy_fb_next")
            g = boot_mean(cell, "gun_diff_next"); w = boot_mean(cell, "won_nxt")
            print(f"  {nk:>6} | {a[0]:5.2f} [{a[1]:4.2f},{a[2]:4.2f}] n={a[3]:,}".ljust(41)
                  + f" | {f[0]:5.2f} [{f[1]:4.2f},{f[2]:4.2f}]".rjust(23)
                  + f" | {g[0]:+5.2f} [{g[1]:+5.2f},{g[2]:+5.2f}]".rjust(27)
                  + f" | {100*w[0]:5.1f}%".rjust(15))
        lo = [r for r in sub if r["n_killed"] <= 1]
        hi = [r for r in sub if r["n_killed"] >= 4]
        if len(lo) >= 120 and len(hi) >= 120:
            for key, lab in (("enemy_armed_next", "enemy armed next"),
                             ("gun_diff_next", "gun differential next"),
                             ("won_nxt", "win N+1")):
                d = boot_diff(lo, hi, key)
                mult = 100 if key == "won_nxt" else 1
                unit = "pp" if key == "won_nxt" else ""
                print(f"    DIFF (>=4 kills) - (<=1 kill), {lab:<22}: "
                      f"{mult*d[0]:+7.2f}{unit} [{mult*d[1]:+7.2f}, {mult*d[2]:+7.2f}]")

print("\n" + "=" * 126)
print("THE CASE DESCRIBED: enemy committed (<500), YOU saved round N (own loadout < 2000),")
print("rounds 5-11 / 17-23, teams that lost round N.")
print("=" * 126)
sub = [r for r in rows if r["g"] == "5-11 / 17-23" and not r["won_cur"]
       and r["enemy_bank"] < 500 and r["own_loadout"] < 2000]
print(f"  n = {len(sub):,} team-rounds")
print(f"  {'kills':>6} | {'enemy armed next':>22} | {'your armed next':>22} | {'gun diff':>20} | {'win N+1':>14}")
for nk in range(0, 6):
    cell = [r for r in sub if r["n_killed"] == nk]
    if len(cell) < 60:
        print(f"  {nk:>6} | {'--':>22} | {'--':>22} | {'--':>20} | {'--':>14}"); continue
    a = boot_mean(cell, "enemy_armed_next"); o = boot_mean(cell, "own_armed_next")
    g = boot_mean(cell, "gun_diff_next"); w = boot_mean(cell, "won_nxt")
    print(f"  {nk:>6} | {a[0]:5.2f} [{a[1]:4.2f},{a[2]:4.2f}] n={a[3]:,}".ljust(41)
          + f" | {o[0]:5.2f} [{o[1]:4.2f},{o[2]:4.2f}]".rjust(23)
          + f" | {g[0]:+5.2f} [{g[1]:+5.2f},{g[2]:+5.2f}]".rjust(21)
          + f" | {100*w[0]:5.1f}%".rjust(15))

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
