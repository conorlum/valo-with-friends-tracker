"""Does M12's destruction effect survive kill-count adjustment EARLY in a half?

M12a showed the LOW-vs-HIGH destroyed-value effect is largely enemies-killed and
round-N outcome, and flips sign in two of three buy-state bands once those are
held fixed. But M12a does not break out by round number at all -- its sample is
M11's, every round 1-23 except 12.

The hypothesis tested here has a mechanism. M12a's negative cells are explained
by WEALTH PERSISTENCE: wipe a rich team and they collect a large loss bonus and
rebuy. That only works once the loss-bonus ladder has something to give. In the
first rounds after an economy reset nobody has a cushion, so destruction may
genuinely deny there and only stop mattering later.

Definitions, tercile rule, the FULL_BUY threshold and the match bootstrap follow
measure_destruction_adjusted_for_kills.py so the numbers are comparable to M12a.
Round groups are M12's own. ALL GROUPS ARE REPORTED -- the exercise is defeated
if only the surviving one is quoted.

Adjustment is stratified: terciles of destroyed are formed inside each
(stratum x round-N outcome x enemies killed) cell, cut points fixed from the
full sample, and the HIGH-LOW difference is pooled across cells weighted by cell
size. The bootstrap resamples MATCHES with replacement and recomputes the pooled
difference; tercile cut points are held fixed as nuisance parameters.

Part of the Impact measurement record. Supports: M12b (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_destruction_adjusted_by_round_group.py

Reads only; never writes to the database.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal

FULL_BUY = 4200
N_BOOT, SEED = 600, 909
MIN_ARM = 60

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches")
if n_matches < 3000:
    print("*** REFUSING TO RUN: expected the full ~3,124-match set. ***"); sys.exit(1)

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
db.close()


def winner(o):
    if not o: return None
    return "TEAM_1" if o.startswith("Team A") else ("TEAM_2" if o.startswith("Team B") else None)


def usable(r):
    return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])


by_match = collections.defaultdict(dict)
for rid, r in rounds.items():
    by_match[r["match_id"]][r["round_number"]] = r


def fb(rid, team):
    d = st.get(rid, {})
    v = [lo for m, lo in d.items() if m in mp and mp[m]["team"] == team]
    return sum(1 for x in v if x >= FULL_BUY) if v else None


GROUPS = [("2-4 / 14-16", {2, 3, 4, 14, 15, 16}),
          ("5-7 / 17-19", {5, 6, 7, 17, 18, 19}),
          ("8-11 / 20-23", {8, 9, 10, 11, 20, 21, 22, 23})]
GROUP_OF = {rn: lab for lab, s in GROUPS for rn in s}

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn in (12, 24) or rn > 24 or rn not in GROUP_OF: continue   # 1 and 13 pistol, as M12
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
            rows.append({"mid": mid, "rn": rn, "g": GROUP_OF[rn], "enemy_fb_N": f,
                         "destroyed": sum(d.get(v, 0) for v in victims),
                         "n_killed": len(victims),
                         "won_cur": w == team, "won_nxt": wn == team})

MATCH_IDS = sorted({r["mid"] for r in rows})
MIDX = {m: i for i, m in enumerate(MATCH_IDS)}
print(f"sample: {len(rows):,} team-rounds over {len(MATCH_IDS):,} matches\n")


def build_arms(subset, adjust):
    """Terciles of destroyed inside each stratum; returns [(weight, per-match 4-tuples)].
    adjust=True strata on (won_cur, n_killed); adjust=False is one stratum."""
    cells = collections.defaultdict(list)
    for r in subset:
        cells[(r["won_cur"], r["n_killed"]) if adjust else 0].append(r)
    arms = []
    for cell in cells.values():
        v = sorted(x["destroyed"] for x in cell)
        if len(v) < 3 * MIN_ARM: continue
        c_lo, c_hi = v[int(len(v) * .33)], v[int(len(v) * .67)]
        lo = [x for x in cell if x["destroyed"] < c_lo]
        hi = [x for x in cell if x["destroyed"] >= c_hi]
        if len(lo) < MIN_ARM or len(hi) < MIN_ARM: continue
        per = [[0, 0, 0, 0] for _ in MATCH_IDS]
        for x in lo:
            per[MIDX[x["mid"]]][0] += bool(x["won_nxt"]); per[MIDX[x["mid"]]][1] += 1
        for x in hi:
            per[MIDX[x["mid"]]][2] += bool(x["won_nxt"]); per[MIDX[x["mid"]]][3] += 1
        arms.append((len(lo) + len(hi), per))
    return arms


def pooled_delta(arms, n_boot=N_BOOT, seed=SEED):
    if not arms: return None
    tot_w = sum(w for w, _ in arms)

    def compute(mult):
        acc = 0.0
        for w, per in arms:
            lh = ln = hh = hn = 0
            for i, m in enumerate(mult):
                if not m: continue
                a, b, c, d = per[i]
                if b or d:
                    lh += a * m; ln += b * m; hh += c * m; hn += d * m
            if ln and hn:
                acc += w * ((hh / hn) - (lh / ln))
        return acc / tot_w

    ones = [1] * len(MATCH_IDS)
    point = compute(ones)
    rng = random.Random(seed)
    out = []
    n = len(MATCH_IDS)
    for _ in range(n_boot):
        mult = [0] * n
        for _ in range(n):
            mult[rng.randrange(n)] += 1
        out.append(compute(mult))
    out.sort()
    return (point, out[int(.025 * len(out))], out[int(.975 * len(out))], tot_w)


def fmt(d):
    return (f"{100*d[0]:+6.2f}pp [{100*d[1]:+5.2f},{100*d[2]:+5.2f}] n={d[3]:,}"
            if d else "--")


print("=" * 116)
print("(1) UNADJUSTED (M12-style) vs KILL-AND-OUTCOME-ADJUSTED (M12a-style), BY ROUND GROUP")
print("=" * 116)
print(f"  {'round group':>14} | {'unadjusted HIGH-LOW':>32} | {'adjusted HIGH-LOW':>32}")
for lab, _ in GROUPS:
    sub = [r for r in rows if r["g"] == lab]
    print(f"  {lab:>14} | {fmt(pooled_delta(build_arms(sub, False))):>32}"
          f" | {fmt(pooled_delta(build_arms(sub, True))):>32}")

print("\n" + "=" * 116)
print("(2) ADJUSTED, SPLIT BY THE ENEMY'S BUY STATE ENTERING ROUND N")
print("    These are exactly the cells w(state) would have been fitted from.")
print("=" * 116)
BANDS = [("broke 0-1", {0, 1}), ("partial 2-3", {2, 3}), ("full 4-5", {4, 5})]
print(f"  {'round group':>14} | " + " | ".join(f"{b[0]:>30}" for b in BANDS))
for lab, _ in GROUPS:
    cells = []
    for _, bs in BANDS:
        sub = [r for r in rows if r["g"] == lab and r["enemy_fb_N"] in bs]
        d = pooled_delta(build_arms(sub, True))
        cells.append((f"{100*d[0]:+6.2f}pp [{100*d[1]:+5.2f},{100*d[2]:+5.2f}]".rjust(30))
                     if d else f"{'--':>30}")
    print(f"  {lab:>14} | " + " | ".join(cells))

print("\n" + "=" * 116)
print("(3) ADJUSTED, ROUND BY ROUND -- is anything special about the first rounds of a half?")
print("=" * 116)
for pair in [{2, 14}, {3, 15}, {4, 16}, {5, 17}, {6, 18}, {7, 19}]:
    sub = [r for r in rows if r["rn"] in pair]
    lab = " / ".join(str(x) for x in sorted(pair))
    print(f"  round {lab:>7} | adjusted {fmt(pooled_delta(build_arms(sub, True))):>34}")

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
