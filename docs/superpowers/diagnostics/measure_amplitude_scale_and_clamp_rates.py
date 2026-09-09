"""Amplitude scale for the pre-plant time factor: what k does on the log-odds basis.

Converts M3's percentage-point lifts to log-odds using the actual far/near
baselines, fits proxy amplitude lines by side, and reports clamp rates, the
K-weighted mean scalar and the implied centring constant c across a k grid.

NOT the spec's fitted regression -- proxy lines from bucket-level differences,
used to place a predeclared k grid in the right units. See
../2026-09-07-three-grids-declaration-draft.md section 0.

Part of the Impact measurement record. Supports: the k / FLOOR / CEIL / W
declaration. Reproduces M20's table on the old pp basis as a replay check.

Run from webapp/:
    .\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\measure_amplitude_scale_and_clamp_rates.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, math

sys.path.insert(0, os.path.abspath("."))
if os.path.exists(".env.remote"):
    for line in open(".env.remote"):
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()

from sqlalchemy import text
from app.db import SessionLocal
from app.scoring.impact import _KILL_ORDER_GRAPH as _G

KOB = {(u, v): d["weight"] for u, v, d in _G.edges(data=True)}

db = SessionLocal()
print("matches:", db.execute(text("select count(*) from matches")).scalar())

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, team FROM match_players")).mappings()}
kills = collections.defaultdict(list)
for k in db.execute(text("""SELECT round_id, killer_match_player_id, death_match_player_id,
    event_time_seconds FROM kill_events ORDER BY round_id, event_time_seconds, id""")).mappings():
    kills[k["round_id"]].append(dict(k))


def winner(o):
    return "TEAM_1" if o and o.startswith("Team A") else ("TEAM_2" if o and o.startswith("Team B") else None)


def atk(rn):
    if rn <= 12:
        return "TEAM_1"
    if rn <= 24:
        return "TEAM_2"
    return "TEAM_1" if (rn - 25) % 2 == 0 else "TEAM_2"


recs = []          # pre-plant kills in planted rounds (the affected population)
total_kills = 0    # every non-self kill, the M5 denominator
for rid, ks in kills.items():
    r = rounds.get(rid)
    if not r or (r["outcome"] and "Surrendered" in r["outcome"]):
        continue
    planted = r["planted"] and r["plant_time"] is not None and not (r["outcome"] and "Time Win" in r["outcome"])
    w = winner(r["outcome"])
    a = atk(r["round_number"])
    alive = {"TEAM_1": 5, "TEAM_2": 5}
    for k in ks:
        kid, vid = k["killer_match_player_id"], k["death_match_player_id"]
        if not kid or not vid or kid not in mp or vid not in mp:
            continue
        kt, vt = mp[kid]["team"], mp[vid]["team"]
        if kt == vt:
            continue
        total_kills += 1
        dt = (k["event_time_seconds"] - r["plant_time"]) if planted else None
        if dt is not None and dt < 0 and w is not None:
            before = f"{alive['TEAM_2']}v{alive['TEAM_1']}"
            nxt = dict(alive)
            if nxt[vt] > 0:
                nxt[vt] -= 1
            after = f"{nxt['TEAM_2']}v{nxt['TEAM_1']}"
            try:
                kob = KOB[(before, after)]
            except KeyError:
                kob = 100
            recs.append({"adv": alive[kt] - alive[vt], "dt": dt, "atk": kt == a,
                         "won": kt == w, "K": kob})
        if alive[vt] > 0:
            alive[vt] -= 1

print(f"all non-self kills {total_kills:,}   pre-plant-in-planted {len(recs):,} "
      f"({100 * len(recs) / total_kills:.1f}%)")


def logit(p):
    return math.log(p / (1 - p))


print("\n=== far (<-30s) vs near (-10..-5s) win rate, by advantage ===")
print(f"{'adv':>4} {'n_far':>7} {'far%':>7} {'n_near':>7} {'near%':>7} {'pp lift':>8} {'logit':>8} {'ratio':>6}")
pts = []
for adv in range(-3, 3):
    far = [r for r in recs if r["adv"] == adv and r["dt"] < -30]
    near = [r for r in recs if r["adv"] == adv and -10 <= r["dt"] < -5]
    if len(far) < 80 or len(near) < 80:
        continue
    pf = sum(r["won"] for r in far) / len(far)
    pn = sum(r["won"] for r in near) / len(near)
    ll = logit(pn) - logit(pf)
    pts.append((adv, ll, min(len(far), len(near))))
    print(f"{adv:>4} {len(far):>7,} {100 * pf:>6.1f}% {len(near):>7,} {100 * pn:>6.1f}% "
          f"{100 * (pn - pf):>+7.1f} {ll:>+8.3f} {ll / (pn - pf):>6.2f}")


def wls(points):
    sw = sum(w for _, _, w in points)
    mx = sum(x * w for x, _, w in points) / sw
    my = sum(y * w for _, y, w in points) / sw
    b = sum(w * (x - mx) * (y - my) for x, y, w in points) / sum(w * (x - mx) ** 2 for x, _, w in points)
    return my - b * mx, b


a0, b0 = wls(pts)
print(f"\npooled logit amplitude line:  L(adv) = {a0:+.3f} {b0:+.3f}*adv")
SIDE_LINE = {}
for side, lab in ((True, "attacker"), (False, "defender")):
    sp = []
    for adv in range(-3, 3):
        far = [r for r in recs if r["adv"] == adv and r["atk"] == side and r["dt"] < -30]
        near = [r for r in recs if r["adv"] == adv and r["atk"] == side and -10 <= r["dt"] < -5]
        if len(far) < 80 or len(near) < 80:
            continue
        pf = sum(r["won"] for r in far) / len(far)
        pn = sum(r["won"] for r in near) / len(near)
        if pf in (0, 1) or pn in (0, 1):
            continue
        sp.append((adv, logit(pn) - logit(pf), min(len(far), len(near))))
    if len(sp) >= 3:
        a, b = wls(sp)
        SIDE_LINE[side] = (a, b)
        print(f"  {lab:<9} L(adv) = {a:+.3f} {b:+.3f}*adv   (levels: "
              + ", ".join(f"{adv}:{y:+.2f}" for adv, y, _ in sp) + ")")

_K = ((-30.0, 0.0), (-25.0, 0.734), (-15.0, 0.888), (-7.5, 1.0), (-2.5, 0.909))


def shape(dt):
    if dt <= _K[0][0]:
        return 0.0
    if dt >= _K[-1][0]:
        return _K[-1][1]
    for (x0, y0), (x1, y1) in zip(_K, _K[1:]):
        if x0 <= dt <= x1:
            return y0 + (y1 - y0) * (dt - x0) / (x1 - x0)
    return 0.0


# The declared grid, marked *, plus a finer sweep around it so the window's
# edges are locatable: {0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8}
KS = (0.1, 0.2, 0.25, 0.27, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8,
      0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 2.0, 2.2, 2.5, 2.8, 3.0, 4.0, 8.0)
DECLARED = {0.2, 0.27, 0.35, 0.45, 0.6, 0.8, 1.0, 1.3, 1.7, 2.2, 2.8}


def clamp_table(label, amp_of):
    print(f"\n=== clamp rates, {label} ===")
    print(f"{'k':>6} | {'floor%all':>9} {'ceil%all':>9} | {'floor%aff':>9} {'ceil%aff':>9} | "
          f"{'mean s':>7} {'K-wtd s':>8} {'c':>6} {'|c-1|':>6} | {'eff bounds':>14} | target?")
    for k in KS:
        fl = ce = 0
        ssum = 0.0
        ks_sum = k_sum = 0.0
        for r in recs:
            s = 1 + k * amp_of(r) * shape(r["dt"])
            if s < 0.2:
                fl += 1
            if s > 1.7:
                ce += 1
            s = max(0.2, min(1.7, s))
            ssum += s
            ks_sum += r["K"] * s
            k_sum += r["K"]
        fa, ca = 100 * fl / total_kills, 100 * ce / total_kills
        fp, cp = 100 * fl / len(recs), 100 * ce / len(recs)
        kw = ks_sum / k_sum          # K-weighted mean clamped scalar
        c = k_sum / ks_sum           # today's pre-plant factor is a flat 1.0
        ok = "YES-all" if (fa < 1.5 and 2.0 <= ca <= 4.0) else ""
        ok += " YES-aff" if (fp < 1.5 and 2.0 <= cp <= 4.0) else ""
        mark = "*" if k in DECLARED else " "
        print(f"{mark}{k:>5.2f} | {fa:>8.2f}% {ca:>8.2f}% | {fp:>8.2f}% {cp:>8.2f}% | "
              f"{ssum / len(recs):>7.4f} {kw:>8.4f} {c:>6.3f} {abs(c - 1):>6.3f} | "
              f"[{0.2 * c:>5.3f},{1.7 * c:>6.3f}] | {ok}")


clamp_table("SIDE-SPECIFIC L(adv) -- what the spec's fit actually produces",
            lambda r: SIDE_LINE[r["atk"]][0] + SIDE_LINE[r["atk"]][1] * max(-3, min(2, r["adv"])))
clamp_table("POOLED L(adv) -- M4 says this is a Simpson's artifact, shown for contrast",
            lambda r: a0 + b0 * max(-3, min(2, r["adv"])))

print("\n=== same, on the OLD pp basis amp = k*(0.089+0.139*adv), to reproduce M20 ===")
for k in (1.0, 2.0, 3.0, 5.0, 8.0):
    cl = 0
    ssum = 0.0
    for r in recs:
        amp = k * (0.089 + 0.139 * max(-3, min(2, r["adv"])))
        s = 1 + amp * shape(r["dt"])
        if s < 0.2 or s > 1.7:
            cl += 1
        ssum += max(0.2, min(1.7, s))
    print(f"  k={k:>4.1f}  mean scalar {ssum / len(recs):.4f}   % clamped (affected denom) {100 * cl / len(recs):.1f}%")
