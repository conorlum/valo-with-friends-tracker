"""Post-plant: what does a death cost, separately for attackers and defenders?

Duel leverage decomposes EXACTLY into the two sides' death costs:

    leverage(a,d,t) = V(a,d-1,t) - V(a-1,d,t)
                    = [V(a,d,t)   - V(a-1,d,t)]      <- what ATTACKERS lose to a death
                    + [V(a,d-1,t) - V(a,d,t)]        <- what DEFENDERS lose to a death

so "how much rode on the duel" and "what did each side risk" are the same
measurement viewed two ways. This splits it, because the design question is
asymmetric: an attacker who dies late may cost their team almost nothing (the
clock keeps working for them), while a defender who dies late was probably
caught defusing, which is a real loss.

Also tests the TWO defuse deadlines, which the shipped denial window
([plant+38, plant+45], impact.py:174) only half captures:
    t = 38.0   a FULL defuse (7.0s) can no longer complete
    t = 41.5   a HALF defuse (3.5s) can no longer complete, even on a
               spike already taken to half and held there

Part of the Impact measurement record. Supports: M25, M26 (both proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_post_plant_death_cost.py

Reads only; never writes to the database.

LIMITATION: V is estimated from rounds that REACHED each (state, t); holding
state and second fixed does not remove that selection. Conditional
associations, not effects. Cells below the observation floor are blank rather
than reported thin.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db import SessionLocal
from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.scoring.impact import _check_for_resurrection

SPIKE, DEFUSE_FULL, DEFUSE_HALF = 45.0, 7.0, 3.5
DEADLINE_FULL, DEADLINE_HALF = SPIKE - DEFUSE_FULL, SPIKE - DEFUSE_HALF   # 38.0, 41.5
MIN_N, N_BOOT, SEED = 60, 400, 17

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches")
if n_matches < 3000:
    print("*** REFUSING TO RUN: expected the full ~3,124-match set. ***"); sys.exit(1)

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome, planted, plant_time, exploded, defused, defuse_time "
    "FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text("SELECT id, team FROM match_players")).mappings()}
kills_by_round = collections.defaultdict(list)
for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id")).mappings():
    kills_by_round[k["round_id"]].append(dict(k))
db.close()

team_of = lambda i: Team.TEAM_1 if mp[i]["team"] in ("TEAM_1", Team.TEAM_1) else Team.TEAM_2
def winner_of(o):
    if not o: return None
    return Team.TEAM_1 if o.startswith("Team A") else (Team.TEAM_2 if o.startswith("Team B") else None)

cell_by_match = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
deaths = []   # (mid, dt, victim_is_attacker, a_before, d_before)

for rid, ks in kills_by_round.items():
    r = rounds.get(rid)
    if r is None: continue
    out = r["outcome"] or ""
    if "Surrendered" in out or not r["planted"] or r["plant_time"] is None: continue
    if "Time Win" in out: continue
    w = winner_of(out)
    if w is None: continue
    pt = r["plant_time"]; atk = attacking_team_for_round(r["round_number"])
    resolution = pt + SPIKE
    if r["defused"] and r["defuse_time"] is not None:
        resolution = min(resolution, r["defuse_time"])
    atk_won = 1 if (w == atk) else 0
    mid = r["match_id"]

    a = d = 5; events = []
    for idx, kill in enumerate(ks):
        kid, vid = kill["killer_match_player_id"], kill["death_match_player_id"]
        self_kill = kid == vid
        t = kill["event_time_seconds"]
        if kid in mp and vid in mp:
            kt, vt = team_of(kid), team_of(vid)
            if (not self_kill) and kt != vt and pt <= t < resolution:
                deaths.append((mid, t - pt, vt == atk, a, d))
            if not _check_for_resurrection(idx, ks):
                victim_is_atk = (vt == atk) if not self_kill else (kt == atk)
                if victim_is_atk: a = max(0, a - 1)
                else: d = max(0, d - 1)
            events.append((t, a, d))

    a = d = 5; ei = 0
    for t in range(0, int(min(SPIKE, resolution - pt))):
        abs_t = pt + t
        while ei < len(events) and events[ei][0] <= abs_t:
            a, d = events[ei][1], events[ei][2]; ei += 1
        c = cell_by_match[(a, d, t)][mid]; c[0] += atk_won; c[1] += 1

V = {}
for cell, per_m in cell_by_match.items():
    n = sum(v[1] for v in per_m.values())
    if n >= MIN_N:
        V[cell] = sum(v[0] for v in per_m.values()) / n
print(f"V cells (>= {MIN_N} obs): {len(V):,}   post-plant deaths: {len(deaths):,}")


def att_death_cost(a, d, t):
    hi, lo = V.get((a, d, t)), V.get((a - 1, d, t))
    return None if hi is None or lo is None else hi - lo


def def_death_cost(a, d, t):
    hi, lo = V.get((a, d - 1, t)), V.get((a, d, t))
    return None if hi is None or lo is None else hi - lo


STATES = [(1, 1), (2, 2), (2, 1), (1, 2), (3, 2), (2, 3)]

print("\n" + "=" * 132)
print("(1) COST OF A DEATH, by state and second, split by which side lost the player.")
print("    Units: drop in that team's own win probability. leverage = ATT cost + DEF cost.")
print("=" * 132)
for label, fn in (("ATTACKER dies (what the attacking team loses)", att_death_cost),
                  ("DEFENDER dies (what the defending team loses)", def_death_cost)):
    print(f"\n  --- {label} ---")
    print(f"  {'t':>4} | " + " | ".join(f"{f'{a}v{d}':>10}" for a, d in STATES))
    for t in list(range(0, 30, 5)) + list(range(30, 44)):
        cells = []
        for a, d in STATES:
            v = fn(a, d, t)
            cells.append(f"{v:>+10.3f}" if v is not None else f"{'--':>10}")
        print(f"  {t:>4} | " + " | ".join(cells))

# ---- (2) weighted by where deaths actually happen -----------------------------
print("\n" + "=" * 132)
print("(2) SAME, WEIGHTED BY WHERE DEATHS ACTUALLY OCCUR. Match-bootstrapped.")
print("    Deadlines: 38.0s (full defuse impossible), 41.5s (half defuse impossible).")
print("=" * 132)
BANDS = [("0-15", 0, 15), ("15-25", 15, 25), ("25-32", 25, 32), ("32-38", 32, DEADLINE_FULL),
         ("38-41.5", DEADLINE_FULL, DEADLINE_HALF), ("41.5-45", DEADLINE_HALF, 45)]


def boot(pm):
    keys = list(pm)
    if not keys: return None
    ts = sum(pm[k][0] for k in keys); tn = sum(pm[k][1] for k in keys)
    if tn == 0: return None
    rng = random.Random(SEED); out = []
    for _ in range(N_BOOT):
        s = n = 0.0
        for _ in range(len(keys)):
            x, y = pm[keys[rng.randrange(len(keys))]]
            s += x; n += y
        if n: out.append(s / n)
    out.sort()
    return (ts / tn, out[int(.025*len(out))], out[int(.975*len(out))], int(tn))


print(f"  {'band':>9} | {'ATTACKER death cost':>32} | {'DEFENDER death cost':>32}")
for lab, lo, hi in BANDS:
    cells = []
    for want_atk_victim in (True, False):
        pm = collections.defaultdict(lambda: [0.0, 0])
        for mid, dt, victim_is_atk, a, d in deaths:
            if victim_is_atk != want_atk_victim or not (lo <= dt < hi): continue
            c = att_death_cost(a, d, int(dt)) if victim_is_atk else def_death_cost(a, d, int(dt))
            if c is None: continue
            pm[mid][0] += c; pm[mid][1] += 1
        r = boot({k: tuple(v) for k, v in pm.items()})
        cells.append(f"{r[0]:+.4f} [{r[1]:+.4f},{r[2]:+.4f}] n={r[3]:,}".rjust(32)
                     if r and r[3] >= 80 else f"{'--':>32}")
    print(f"  {lab:>9} | " + " | ".join(cells))

# ---- (3) does the 41.5s deadline show up as its own break? --------------------
print("\n" + "=" * 132)
print("(3) THE TWO DEADLINES as breaks in 1v1 duel leverage (the cleanest state).")
print("=" * 132)
for t in range(34, 45):
    a_c, d_c = att_death_cost(1, 1, t), def_death_cost(1, 1, t)
    if a_c is None or d_c is None:
        print(f"  t={t:>2}s | --"); continue
    mark = ""
    if t == 38: mark = "   <-- full defuse (7.0s) no longer completes"
    if t == 41: mark = "   <-- half defuse (3.5s) no longer completes past 41.5"
    print(f"  t={t:>2}s | leverage {a_c + d_c:+.3f} = ATT risk {a_c:+.3f} + DEF risk {d_c:+.3f}{mark}")

# ---- (4) how thin is late post-plant, and why -------------------------------
print("\n" + "=" * 132)
print("(4) SURVIVORS LATE: why 3v3 runs out of data. Distribution of total alive at each second.")
print("=" * 132)
alive_dist = collections.defaultdict(collections.Counter)
for (a, d, t), per_m in cell_by_match.items():
    alive_dist[t][a + d] += sum(v[1] for v in per_m.values())
print(f"  {'t':>4} | " + " | ".join(f"{f'{k} alive':>9}" for k in (10, 8, 6, 4, 3, 2)))
for t in (0, 10, 20, 30, 35, 40, 43):
    tot = sum(alive_dist[t].values()) or 1
    print(f"  {t:>4} | " + " | ".join(f"{100*alive_dist[t][k]/tot:>8.1f}%" for k in (10, 8, 6, 4, 3, 2)))

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
