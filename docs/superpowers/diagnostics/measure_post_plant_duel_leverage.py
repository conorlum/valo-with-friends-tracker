"""Post-plant DUEL LEVERAGE by state and second: how much was riding on the fight?

measure_post_plant_marginal_value.py computed V(after) - V(before). That estimand
is biased toward zero for expected outcomes, because V(before) already prices in
the duel:

    V(before) = p*V(atk wins) + (1-p)*V(def wins)
    =>  V(after|atk wins) - V(before)  =  (1-p) * [V(atk wins) - V(def wins)]

So a kill the killer was 95% likely to get scores at 5% of what the moment was
actually worth. That understates "the late kill secured the round".

This measures the STAKES instead, which is symmetric in the two outcomes:

    leverage(a, d, t) = V(a, d-1, t) - V(a-1, d, t)

i.e. the swing in attacker win probability between the two ways the duel can go,
holding the state and the second fixed. This is the same quantity
kill_order_bonus encodes for man-advantage, extended to time and side.

Also reports leverage as a RATIO to that state's own time-average, which is the
design object: it isolates the time/side adjustment and leaves the state itself
to kill_order_bonus, rather than multiplying two measures of the same thing.

Part of the Impact measurement record. Supports: M23, M24 (both proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_post_plant_duel_leverage.py

Reads only; never writes to the database.

PRECISION NOTE: per-second cells are POINT ESTIMATES with counts shown. Match
bootstrapped intervals are reported for the 5-second buckets and for the two
headline contrasts only -- per-second CIs on every cell would be 600+ bootstraps
and the thin cells could not support them anyway.

LIMITATION: V is estimated from rounds that REACHED each (state, t). Holding
state and second fixed is stronger than the raw curve but is still conditioning
on survival to that point. Conditional associations, not effects of time.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db import SessionLocal
from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round
from app.scoring.impact import _check_for_resurrection

SPIKE = 45.0
MIN_N = 60
N_BOOT, SEED = 400, 17

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

# cell -> per-match [wins, n]; cell = (a, d, t)
cell_by_match = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
duel_count = collections.Counter()      # (a, d, t) -> observed duels starting from that state

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

    a = d = 5
    events = []
    for idx, kill in enumerate(ks):
        kid, vid = kill["killer_match_player_id"], kill["death_match_player_id"]
        self_kill = kid == vid
        t = kill["event_time_seconds"]
        if kid in mp and vid in mp:
            kt, vt = team_of(kid), team_of(vid)
            if (not self_kill) and kt != vt and pt <= t < resolution:
                duel_count[(a, d, int(t - pt))] += 1
            if not _check_for_resurrection(idx, ks):
                victim_is_atk = (vt == atk) if not self_kill else (kt == atk)
                if victim_is_atk: a = max(0, a - 1)
                else: d = max(0, d - 1)
            events.append((t, a, d))

    a = d = 5; ei = 0
    horizon = int(min(SPIKE, resolution - pt))
    for t in range(0, horizon):
        abs_t = pt + t
        while ei < len(events) and events[ei][0] <= abs_t:
            a, d = events[ei][1], events[ei][2]; ei += 1
        c = cell_by_match[(a, d, t)][mid]
        c[0] += atk_won; c[1] += 1

V = {}
for cell, per_m in cell_by_match.items():
    n = sum(v[1] for v in per_m.values())
    if n >= MIN_N:
        V[cell] = sum(v[0] for v in per_m.values()) / n
print(f"V cells (>= {MIN_N} obs): {len(V):,}")


def leverage(a, d, t):
    """Swing in attacker win prob between the two duel outcomes, at fixed (state, t)."""
    va, vd = V.get((a, d - 1, t)), V.get((a - 1, d, t))
    if va is None or vd is None: return None
    return va - vd


STATES = [(1, 1), (2, 2), (3, 3), (2, 1), (1, 2), (3, 2), (2, 3)]

print("\n" + "=" * 128)
print("(1) DUEL LEVERAGE = V(atk wins duel) - V(def wins duel), at fixed state and second.")
print("    'How much was riding on this fight' -- symmetric, NOT shrunk by who was favoured.")
print("    Point estimates; (n) = duels observed starting from that state at that second.")
print("=" * 128)
print(f"  {'t':>4} | " + " | ".join(f"{f'{a}v{d}':>15}" for a, d in STATES))
for t in list(range(0, 30, 5)) + list(range(30, 44)):
    cells = []
    for a, d in STATES:
        lv = leverage(a, d, t)
        n = duel_count.get((a, d, t), 0)
        cells.append(f"{lv:+.3f} ({n:,})".rjust(15) if lv is not None else f"{'--':>15}")
    print(f"  {t:>4} | " + " | ".join(cells))

print("\n" + "=" * 128)
print("(2) THE RATIO -- leverage(state, t) / that state's own time-averaged leverage.")
print("    This is the design object: the TIME adjustment with the STATE divided out,")
print("    so it can multiply kill_order_bonus without re-encoding what kill_order_bonus already is.")
print("=" * 128)
print(f"  {'t':>4} | " + " | ".join(f"{f'{a}v{d}':>10}" for a, d in STATES))
base = {}
for a, d in STATES:
    vals = [leverage(a, d, t) for t in range(0, 44)]
    vals = [v for v in vals if v is not None]
    base[(a, d)] = sum(vals) / len(vals) if vals else None
for t in list(range(0, 30, 5)) + list(range(30, 44)):
    cells = []
    for a, d in STATES:
        lv, b = leverage(a, d, t), base[(a, d)]
        cells.append(f"{lv/b:>10.2f}" if lv is not None and b else f"{'--':>10}")
    print(f"  {t:>4} | " + " | ".join(cells))
print("\n  time-averaged leverage per state (the part kill_order_bonus should carry):")
for a, d in STATES:
    b = base[(a, d)]
    print(f"    {a}v{d}: {b:+.3f}" if b else f"    {a}v{d}: --")

# ---- (3) bootstrapped headline contrasts -------------------------------------
print("\n" + "=" * 128)
print("(3) BOOTSTRAPPED CONTRASTS (match-level, 400 draws). Does leverage really move with time?")
print("=" * 128)
match_ids = sorted({m for per_m in cell_by_match.values() for m in per_m})
idx = {m: i for i, m in enumerate(match_ids)}


def boot_leverage_diff(a, d, t_lo, t_hi):
    need = [(a, d - 1, t_lo), (a - 1, d, t_lo), (a, d - 1, t_hi), (a - 1, d, t_hi)]
    arrs = []
    for cell in need:
        w = [0] * len(match_ids); n = [0] * len(match_ids)
        for m, (cw, cn) in cell_by_match.get(cell, {}).items():
            w[idx[m]] = cw; n[idx[m]] = cn
        arrs.append((w, n))
    rng = random.Random(SEED); out = []
    for _ in range(N_BOOT):
        draw = [rng.randrange(len(match_ids)) for _ in range(len(match_ids))]
        vs = []
        for w, n in arrs:
            sw = sn = 0
            for i in draw:
                sw += w[i]; sn += n[i]
            vs.append(sw / sn if sn else None)
        if all(v is not None for v in vs):
            out.append((vs[0] - vs[1]) - (vs[2] - vs[3]))
    if not out: return None
    out.sort()
    return (out[len(out)//2], out[int(.025*len(out))], out[int(.975*len(out))])


for a, d, t_lo, t_hi in [(1, 1, 10, 35), (2, 2, 10, 35), (2, 1, 10, 35), (1, 2, 10, 35)]:
    r = boot_leverage_diff(a, d, t_lo, t_hi)
    l_lo, l_hi = leverage(a, d, t_lo), leverage(a, d, t_hi)
    if r is None or l_lo is None or l_hi is None:
        print(f"  {a}v{d}: insufficient data"); continue
    print(f"  {a}v{d}  leverage t={t_lo}: {l_lo:+.3f}   t={t_hi}: {l_hi:+.3f}   "
          f"change: {r[0]:+.3f} [{r[1]:+.3f}, {r[2]:+.3f}]")

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
