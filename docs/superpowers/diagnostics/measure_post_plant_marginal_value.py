"""What is a post-plant kill actually WORTH at each second, on the margin?

The raw win-rate-by-second table (measure_post_plant_time_curve.py) cannot
distinguish "this kill decided the round" from "this round was already decided
and a kill happened during it". Late post-plant that distinction is the whole
question: at plant+35 in a 1v1 the defender is FORCED to expose themselves by
the spike timer, the attacker wins on threat alone, and the kill may be
recording an outcome it did not cause.

So this measures a state-value function and takes differences:

    V(a, d, t) = P(attacking team wins | a attackers and d defenders alive,
                   t seconds after the plant, round not yet resolved)

    marginal value of a kill at time t = V(state_after, t) - V(state_before, t)

evaluated from the KILLER's perspective. A kill whose marginal value is ~0 did
not move the round, however strongly its raw win rate correlates with winning.

Bucketing per the design discussion: 5s buckets to +30, then PER SECOND to +45,
because the defuse deadline (spike 45s, full defuse 7s, half defuse 3.5s) makes
the last 15 seconds structurally different second by second.

Part of the Impact measurement record. Supports: M21, M22, M23 (all proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_post_plant_marginal_value.py

Reads only; never writes to the database.

LIMITATION: V is estimated from rounds that REACHED each (state, t), which is
post-treatment selection. V differences hold state and time fixed, which is
stronger than the raw table, but does not license causal language.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db import SessionLocal
from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.scoring.impact import _time_factor, _check_for_resurrection

SPIKE, DEFUSE_FULL, DEFUSE_HALF = 45.0, 7.0, 3.5
N_BOOT, SEED = 1000, 17
MIN_N = 60

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


# ---- pass 1: build per-round post-plant timelines -----------------------------
# timeline[t] = (attackers_alive, defenders_alive) for integer t in [0, resolution)
timelines = []      # (match_id, atk_won, resolution, [(t, a, d)], [kill records])
for rid, ks in kills_by_round.items():
    r = rounds.get(rid)
    if r is None: continue
    out = r["outcome"] or ""
    if "Surrendered" in out or not r["planted"] or r["plant_time"] is None: continue
    if "Time Win" in out: continue                       # phantom (M16)
    w = winner_of(out)
    if w is None: continue
    pt = r["plant_time"]
    atk = attacking_team_for_round(r["round_number"])
    resolution = pt + SPIKE
    if r["defused"] and r["defuse_time"] is not None:
        resolution = min(resolution, r["defuse_time"])
    atk_won = (w == atk)

    # alive counts through the whole round, mirroring impact.py's resurrection rule
    a_alive = d_alive = 5
    events = []          # (time, a_after, d_after)
    kill_recs = []       # (dt, killer_is_attacker, a_before, d_before, a_after, d_after)
    for idx, kill in enumerate(ks):
        kid, vid = kill["killer_match_player_id"], kill["death_match_player_id"]
        self_kill = kid == vid
        t = kill["event_time_seconds"]
        if kid in mp and vid in mp:
            kt, vt = team_of(kid), team_of(vid)
            a_b, d_b = a_alive, d_alive
            resurrect = _check_for_resurrection(idx, ks)
            if not resurrect:
                victim_is_atk = (vt == atk) if not self_kill else (kt == atk)
                if victim_is_atk: a_alive = max(0, a_alive - 1)
                else: d_alive = max(0, d_alive - 1)
            if (not self_kill) and kt != vt and pt <= t < resolution:
                kill_recs.append((t - pt, kt == atk, a_b, d_b, a_alive, d_alive))
            events.append((t, a_alive, d_alive))
    timelines.append((r["match_id"], atk_won, pt, resolution, events, kill_recs))

print(f"planted rounds usable: {len(timelines):,}")

# ---- pass 2: estimate V(a, d, t) ---------------------------------------------
# One observation per (round, integer second alive post-plant).
V_obs = collections.defaultdict(list)     # (a,d,t) -> [(match_id, atk_won)]
for mid, atk_won, pt, resolution, events, _ in timelines:
    horizon = int(min(SPIKE, resolution - pt))
    a = d = 5
    ei = 0
    for t in range(0, horizon):
        abs_t = pt + t
        while ei < len(events) and events[ei][0] <= abs_t:
            a, d = events[ei][1], events[ei][2]; ei += 1
        V_obs[(a, d, t)].append((mid, atk_won))

V = {k: sum(x[1] for x in v) / len(v) for k, v in V_obs.items() if len(v) >= MIN_N}
print(f"V cells estimated (>= {MIN_N} obs): {len(V):,}")

# ---- bucketing ---------------------------------------------------------------
def bucket(dt):
    if dt < 30: return f"{int(dt)//5*5}-{int(dt)//5*5+5}"
    return f"{int(dt)}s"

ORDER = [f"{i}-{i+5}" for i in range(0, 30, 5)] + [f"{s}s" for s in range(30, 45)]


def boot_mean(per_match, n_boot=N_BOOT, seed=SEED):
    keys = list(per_match)
    if not keys: return None
    tot_s = sum(per_match[k][0] for k in keys); tot_n = sum(per_match[k][1] for k in keys)
    if tot_n == 0: return None
    rng = random.Random(seed); out = []
    for _ in range(n_boot):
        s = n = 0.0
        for _ in range(len(keys)):
            a, b = per_match[keys[rng.randrange(len(keys))]]
            s += a; n += b
        if n: out.append(s / n)
    out.sort()
    return (tot_s / tot_n, out[int(.025*len(out))], out[int(.975*len(out))], int(tot_n))


# ---- (1) marginal value of a kill, by side and time ---------------------------
print("\n" + "=" * 132)
print("(1) MARGINAL VALUE OF A KILL = V(state_after, t) - V(state_before, t), from the KILLER's side.")
print("    Units: change in the killer's team's win probability. ~0 means the kill did not move the round.")
print("=" * 132)
print(f"  {'bucket':>7} | {'ATTACKER kill':>30} | {'DEFENDER kill':>30} | {'ramp pays':>9}")
agg = {True: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0])),
       False: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))}
ramp_acc = collections.defaultdict(lambda: [0.0, 0])
for mid, atk_won, pt, resolution, events, kill_recs in timelines:
    for dt, killer_is_atk, a_b, d_b, a_a, d_a in kill_recs:
        t = int(dt)
        vb, va = V.get((a_b, d_b, t)), V.get((a_a, d_a, t))
        if vb is None or va is None: continue
        delta = (va - vb) if killer_is_atk else (vb - va)   # killer's perspective
        b = bucket(dt)
        agg[killer_is_atk][b][mid][0] += delta
        agg[killer_is_atk][b][mid][1] += 1
        rr = rounds  # ramp value actually paid
for mid, atk_won, pt, resolution, events, kill_recs in timelines:
    for dt, killer_is_atk, *_ in kill_recs:
        b = bucket(dt)
        ramp_acc[b][0] += 1 + dt / 53 if not (38 <= dt <= 45) else 1.75
        ramp_acc[b][1] += 1

for b in ORDER:
    cells = []
    for side in (True, False):
        pm = {m: tuple(v) for m, v in agg[side][b].items()}
        r = boot_mean(pm)
        cells.append(f"{100*r[0]:+6.2f}pp [{100*r[1]:+5.2f},{100*r[2]:+5.2f}] n={r[3]:,}".rjust(30)
                     if r and r[3] >= 100 else f"{'--':>30}")
    rp = ramp_acc[b][0] / ramp_acc[b][1] if ramp_acc[b][1] else float("nan")
    print(f"  {b:>7} | " + " | ".join(cells) + f" | {rp:>9.3f}")

# ---- (2) the value function itself, for the states that matter late -----------
print("\n" + "=" * 132)
print("(2) V(a, d, t) = attacker win probability, by second. Shows how much room the DEFENDER has left.")
print("    If V is already ~1.0 before the kill, no kill at that moment can be worth much.")
print("=" * 132)
states = [(1, 1), (2, 2), (1, 2), (2, 1), (3, 3)]
print(f"  {'t':>4} | " + " | ".join(f"{f'{a}v{d}':>12}" for a, d in states))
for t in list(range(0, 30, 5)) + list(range(30, 45)):
    cells = []
    for a, d in states:
        v = V.get((a, d, t))
        n = len(V_obs.get((a, d, t), []))
        cells.append(f"{v:.3f} ({n//100}h)".rjust(12) if v is not None else f"{'--':>12}")
    print(f"  {t:>4} | " + " | ".join(cells))

# ---- (3) the defender's death: is it forced? ----------------------------------
print("\n" + "=" * 132)
print("(3) DEFENDER DEATHS LATE: what was the defending team's win probability BEFORE the death?")
print("    If it was already near zero, the death cannot have cost them anything.")
print("=" * 132)
print(f"  {'bucket':>7} | {'DEF win prob BEFORE death':>28} | {'AFTER':>12} | {'cost of the death':>26}")
d_before = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))
d_after = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))
d_cost = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))
for mid, atk_won, pt, resolution, events, kill_recs in timelines:
    for dt, killer_is_atk, a_b, d_b, a_a, d_a in kill_recs:
        if not killer_is_atk: continue          # attacker killed a defender
        t = int(dt)
        vb, va = V.get((a_b, d_b, t)), V.get((a_a, d_a, t))
        if vb is None or va is None: continue
        b = bucket(dt)
        d_before[b][mid][0] += (1 - vb); d_before[b][mid][1] += 1
        d_after[b][mid][0] += (1 - va); d_after[b][mid][1] += 1
        d_cost[b][mid][0] += (vb - va); d_cost[b][mid][1] += 1
for b in ORDER:
    rb = boot_mean({m: tuple(v) for m, v in d_before[b].items()})
    ra = boot_mean({m: tuple(v) for m, v in d_after[b].items()})
    rc = boot_mean({m: tuple(v) for m, v in d_cost[b].items()})
    if not rb or rb[3] < 100:
        print(f"  {b:>7} | {'--':>28} | {'--':>12} | {'--':>26}"); continue
    print(f"  {b:>7} | {100*rb[0]:6.2f}% [{100*rb[1]:5.2f},{100*rb[2]:5.2f}] n={rb[3]:,}".ljust(42)
          + f" | {100*ra[0]:10.2f}% | {100*rc[0]:+8.2f}pp [{100*rc[1]:+5.2f},{100*rc[2]:+5.2f}]")

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
