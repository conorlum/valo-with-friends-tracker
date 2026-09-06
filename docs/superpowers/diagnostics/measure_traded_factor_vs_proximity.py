"""Is the time spec's centring gate inert, and does kill-side centring leave a
death-side residual?

Two questions, both about `_traded_factor` (impact.py:185):

(1) The spec's Testing section (:463-466) gates on the NET time contribution,
    mean(K*s) - mean(K*T*s), whose per-kill integrand is K*s*(1-T). Because
    _traded_factor returns exactly 1 for any kill whose killer was NOT traded
    back within 10s, (1-T) == 0 there and those kills carry NO weight in the
    gate. How much of the population, and of the weight mass, is that?

(2) The proposed replacement centres on the kill side only -- solve one constant
    so mean(K*s) is preserved -- and reports the death-side residual rather than
    pretending one constant can preserve both. That residual is
        resid = [E[K*T*s]/E[K*T]] / [E[K*s]/E[K]] - 1
    which is exactly 0 iff s and T are uncorrelated under K-weighting. Since s
    is a function of (proximity, advantage), this reduces to: does the traded
    factor vary with proximity to the plant?

Part of the Impact measurement record. Supports: M19, M20 (both proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_traded_factor_vs_proximity.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))  # run from webapp/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db import SessionLocal
from app.models.match import Team
# The real scorer functions -- NOT reimplementations. If these change, this
# measurement changes with them, which is the point.
from app.scoring.impact import _kill_order_bonus, _traded_factor, _check_for_resurrection

N_BOOT, SEED = 2000, 17

# --- the scalar family --------------------------------------------------------
# The spec's Part 3 form: clamp(1 + amplitude(adv) * shape(dt), 0.2, 1.7).
# Neither amplitude nor shape is fitted yet, so both are taken from the MEASURED
# tables rather than invented here:
#   shape  <- M1's 5v5 row, min-max normalised over its own range
#   amp    <- M3's fitted line, lift = 8.9pp + 13.9pp per man, as a fraction
# k is the spec's policy knob (":k is a policy parameter, not an estimate"),
# so the residual is reported across a predeclared grid rather than at one value.
K_GRID = (1.0, 2.0, 3.0, 5.0, 8.0)
_SHAPE_KNOTS = ((-30.0, 0.0), (-25.0, 0.734), (-15.0, 0.888), (-7.5, 1.0), (-2.5, 0.909))


def shape(dt):
    """dt is seconds_to_plant, negative pre-plant. 0 far, ~1 near, plateau <10s."""
    if dt <= _SHAPE_KNOTS[0][0]:
        return 0.0
    if dt >= _SHAPE_KNOTS[-1][0]:
        return _SHAPE_KNOTS[-1][1]
    for (x0, y0), (x1, y1) in zip(_SHAPE_KNOTS, _SHAPE_KNOTS[1:]):
        if x0 <= dt <= x1:
            return y0 + (y1 - y0) * (dt - x0) / (x1 - x0)
    return 0.0


def scalar(dt, adv, k):
    amp = k * (0.089 + 0.139 * max(-3, min(2, adv)))  # M3, clamped to its support
    return max(0.2, min(1.7, 1.0 + amp * shape(dt)))


BUCKETS = [("<-30", -1e9, -30.0), ("-30..-20", -30.0, -20.0), ("-20..-10", -20.0, -10.0),
           ("-10..-5", -10.0, -5.0), ("-5..0", -5.0, 0.0)]


def bucket_of(dt):
    for lab, lo, hi in BUCKETS:
        if lo <= dt < hi:
            return lab
    return None


# --- load ---------------------------------------------------------------------
db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
n_rounds = db.execute(text("SELECT count(*) FROM rounds")).scalar()
n_kills = db.execute(text("SELECT count(*) FROM kill_events")).scalar()
print(f"DATASET: {n_matches:,} matches, {n_rounds:,} rounds, {n_kills:,} kill events")
if n_matches < 3000:
    print("\n*** REFUSING TO RUN: expected the full ~3,124-match set. ***")
    print("*** An older subset produces plausible, different, WRONG numbers. ***")
    sys.exit(1)

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, team FROM match_players")).mappings()}
kills_by_round = collections.defaultdict(list)
for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id")).mappings():
    kills_by_round[k["round_id"]].append(dict(k))
db.close()

# --- replay -------------------------------------------------------------------
# Mirrors impact.py's kill-order loop exactly, including the note at :488-491
# that team1_kill_index tracks TEAM_2's alive count and vice versa.
recs = []
for rid, ks in kills_by_round.items():
    r = rounds.get(rid)
    if r is None:
        continue
    out = r["outcome"] or ""
    if "Surrendered" in out:
        continue
    if not r["planted"] or r["plant_time"] is None:
        continue
    if "Time Win" in out:          # phantom plant (M16)
        continue
    pt = r["plant_time"]
    team1_kill_index = team2_kill_index = 5
    for idx, kill in enumerate(ks):
        kid, vid = kill["killer_match_player_id"], kill["death_match_player_id"]
        self_kill = kid == vid
        if kid in mp and vid in mp and not self_kill:
            kt = Team.TEAM_1 if mp[kid]["team"] in ("TEAM_1", Team.TEAM_1) else Team.TEAM_2
            vt = Team.TEAM_1 if mp[vid]["team"] in ("TEAM_1", Team.TEAM_1) else Team.TEAM_2
            if kt != vt:
                K = _kill_order_bonus(team1_kill_index, team2_kill_index, kt, False)
                T = _traded_factor(ks, kill, False)
                own = team2_kill_index if kt == Team.TEAM_1 else team1_kill_index
                opp = team1_kill_index if kt == Team.TEAM_1 else team2_kill_index
                dt = kill["event_time_seconds"] - pt
                if dt < 0:  # pre-plant only
                    recs.append((r["match_id"], dt, own - opp, K, T))
        if not _check_for_resurrection(idx, ks):
            if self_kill:
                if kid in mp and (mp[kid]["team"] in ("TEAM_1", Team.TEAM_1)):
                    team2_kill_index -= 1
                else:
                    team1_kill_index -= 1
            elif kid in mp and vid in mp:
                if mp[kid]["team"] in ("TEAM_1", Team.TEAM_1):
                    team1_kill_index -= 1
                else:
                    team2_kill_index -= 1

print(f"POPULATION: {len(recs):,} pre-plant non-self kills in non-phantom, "
      f"non-surrendered planted rounds")

# --- bootstrap helper ---------------------------------------------------------
def boot_ratio(per_match, num_key, den_key, n_boot=N_BOOT, seed=SEED):
    """per_match: {mid: dict of partial sums}. Returns (point, lo, hi)."""
    keys = list(per_match)
    num = sum(per_match[m][num_key] for m in keys)
    den = sum(per_match[m][den_key] for m in keys)
    if den == 0:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(n_boot):
        a = b = 0.0
        for _ in range(len(keys)):
            d = per_match[keys[rng.randrange(len(keys))]]
            a += d[num_key]; b += d[den_key]
        if b:
            out.append(a / b)
    out.sort()
    return (num / den, out[int(.025 * len(out))], out[int(.975 * len(out))])


# --- (1) how much of the population does the NET gate actually see? ------------
print("\n" + "=" * 92)
print("(1) THE NET GATE'S SUPPORT -- what fraction of kills carry non-zero weight in")
print("    mean(K*s) - mean(K*T*s), whose integrand is K*s*(1-T)?")
print("=" * 92)
n_traded = sum(1 for _, _, _, _, T in recs if T < 1.0)
mass_all = sum(K * (1 - T) for _, _, _, K, T in recs)
mass_kill = sum(K for _, _, _, K, _ in recs)
print(f"  kills with T < 1 (killer traded back within 10s) : {n_traded:,} / {len(recs):,} "
      f"= {100*n_traded/len(recs):.1f}%")
print(f"  sum K*(1-T)  [the net gate's total weight]        : {mass_all:,.0f}")
print(f"  sum K        [the kill-side gate's total weight]  : {mass_kill:,.0f}")
print(f"  ratio -- net gate sees this share of kill-side mass: {100*mass_all/mass_kill:.1f}%")

# --- (2) K-weighted mean traded factor by proximity x advantage ----------------
print("\n" + "=" * 92)
print("(2) K-WEIGHTED MEAN TRADED FACTOR by proximity and man-advantage.")
print("    Flat in proximity => s and T uncorrelated => kill-side centring is free.")
print("=" * 92)
print(f"  {'adv':>4} | " + " | ".join(f"{b[0]:>22}" for b in BUCKETS))
for adv in (-2, -1, 0, 1, 2):
    cells = []
    for lab, lo, hi in BUCKETS:
        pm = collections.defaultdict(lambda: {"kt": 0.0, "k": 0.0})
        n = 0
        for mid, dt, a, K, T in recs:
            if a == adv and lo <= dt < hi:
                pm[mid]["kt"] += K * T; pm[mid]["k"] += K; n += 1
        r = boot_ratio(pm, "kt", "k") if n >= 200 else None
        cells.append(f"{r[0]:.3f} [{r[1]:.3f},{r[2]:.3f}] n={n:,}".rjust(22) if r else f"{'--':>22}")
    print(f"  {adv:>+4} | " + " | ".join(cells))

print("\n  Marginal over advantage:")
for lab, lo, hi in BUCKETS:
    pm = collections.defaultdict(lambda: {"kt": 0.0, "k": 0.0})
    n = 0
    for mid, dt, a, K, T in recs:
        if lo <= dt < hi:
            pm[mid]["kt"] += K * T; pm[mid]["k"] += K; n += 1
    r = boot_ratio(pm, "kt", "k")
    print(f"    {lab:>9}: {r[0]:.4f} [{r[1]:.4f}, {r[2]:.4f}]  n={n:,}")

# --- (3) the death-side residual after kill-side centring ----------------------
print("\n" + "=" * 92)
print("(3) DEATH-SIDE RESIDUAL after centring the scalar on the kill side.")
print("    resid = [E[K*T*s]/E[K*T]] / [E[K*s]/E[K]] - 1,  match-level bootstrap.")
print("    k is the spec's policy knob; grid is predeclared, not chosen after seeing results.")
print("=" * 92)
print(f"  {'k':>4} | {'mean scalar':>12} | {'% clamped':>10} | {'death-side residual':>30}")
for k in K_GRID:
    pm = collections.defaultdict(lambda: {"ks": 0.0, "k": 0.0, "kts": 0.0, "kt": 0.0})
    s_sum = 0.0; clamped = 0
    for mid, dt, a, K, T in recs:
        s = scalar(dt, a, k)
        raw = 1.0 + k * (0.089 + 0.139 * max(-3, min(2, a))) * shape(dt)
        if raw < 0.2 or raw > 1.7:
            clamped += 1
        s_sum += s
        d = pm[mid]
        d["ks"] += K * s; d["k"] += K; d["kts"] += K * T * s; d["kt"] += K * T
    keys = list(pm)
    rng = random.Random(SEED)
    pts = []
    for _ in range(N_BOOT):
        ks = kk = kts = kt = 0.0
        for _ in range(len(keys)):
            d = pm[keys[rng.randrange(len(keys))]]
            ks += d["ks"]; kk += d["k"]; kts += d["kts"]; kt += d["kt"]
        if kk and kt and ks:
            pts.append((kts / kt) / (ks / kk) - 1.0)
    pts.sort()
    tot = {q: sum(pm[m][q] for m in keys) for q in ("ks", "k", "kts", "kt")}
    point = (tot["kts"] / tot["kt"]) / (tot["ks"] / tot["k"]) - 1.0
    lo, hi = pts[int(.025 * len(pts))], pts[int(.975 * len(pts))]
    print(f"  {k:>4.1f} | {s_sum/len(recs):>12.4f} | {100*clamped/len(recs):>9.1f}% | "
          f"{100*point:>+8.3f}% [{100*lo:+.3f}, {100*hi:+.3f}]")

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
print("This script reports conditional quantities only.")
