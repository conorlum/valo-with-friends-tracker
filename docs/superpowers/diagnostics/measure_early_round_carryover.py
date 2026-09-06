"""Early rounds: does a CLEAN win carry equipment forward that a DIRTY win does not?

Every M12* measurement uses "enemies killed / credits destroyed" as the exposure.
This one uses the OTHER side of the same table: a team's OWN deaths in round N,
conditioned on that team having WON round N. My deaths are your kills, so one
measurement serves both the hypothesis and the econ component's killer-side view.

WHY THIS CONDITIONING. M13 shows the enemy's round-4 economy carries a ~3
full-buy spread fixed before round 4 begins, by the rounds 2-3 record and the
loss-bonus ladder. That is why a raw round-N+1 readout fails early -- it mostly
reads the ladder. Conditioning on the round-N OUTCOME pins the ladder position
and varies only the equipment actually carried forward, so the ladder cannot
confound a comparison in which both arms won the round.

THE HYPOTHESIS UNDER TEST (predeclared, stated before running):
  (a) round 2, pistol WINNERS who won round 2 -- each own death costs round 3
  (b) round 2, pistol winners who LOST round 2 -- reported for contrast
  (c) round 3, the FULL-BUY team that won round 3 -- 1-2 deaths tolerable,
      3-4 hurts round 4 a lot, 5 severe
  (d) round 3, pistol LOSERS who won round 3 -- same clean/dirty logic

PREDECLARED ACCEPTANCE RULE. The mechanism is supported only if the MEDIATOR
(own equipment in N+1) degrades monotonically in own deaths AND the bootstrapped
difference excludes zero, in at least cells (a) and (c). The component is built
on the mediator, never on the win rate -- keeping the outcome out of the formula
is what preserves "no fitted parameter anywhere" in the econ spec. If the
mediator moves but the outcome does not, that is M12d's situation (mechanism
evidenced, payoff not) and is reported as such.

PREDECLARED SHAPE RULE. The register carries "destruction shows a threshold in
the deciles" as WITHDRAWN (M10, confounded by enemy wealth), and M12c found "a
kink rather than a cliff". So the FULL 0-5 curve is reported with intervals and
CONSECUTIVE differences are bootstrapped individually. A nonlinearity is claimed
only where a consecutive difference excludes zero. The shape cannot be chosen
after seeing the numbers.

The argument for why M10's confound should be weak here -- econ is low for both
teams in the early rounds -- is itself testable and is tested: the last block
reports the enemy-wealth spread early against late.

Part of the Impact measurement record. Supports: M27 (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_early_round_carryover.py

Reads only; never writes to the database.

LIMITATION: own deaths, round outcome and both teams' buys are jointly
determined within a round. Conditioning on the pistol outcome, the round-N
outcome and the enemy's round-N loadout closes three paths, not all -- site
taken, plant status, man-advantage trajectory and the team's own spending
decision remain uncontrolled. Conditional associations, not effects. A death
here is any death recorded against the player, self-kills included: a lost gun
is lost either way.
"""
import os, sys, collections, random, statistics

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal

ARMED = 2900        # rifle-tier loadout
FULL_BUY = 4200     # matches FULL_BUY_THRESHOLD in app/scoring/impact.py
N_BOOT, SEED = 800, 4242
MIN_CELL = 120

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches")
if n_matches < 3000:
    print("*** REFUSING TO RUN: expected the full ~3,124-match set. ***")
    sys.exit(1)

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
    if not o:
        return None
    return "TEAM_1" if o.startswith("Team A") else ("TEAM_2" if o.startswith("Team B") else None)


def usable(r):
    return r["outcome"] and "Surrendered" not in r["outcome"] and winner(r["outcome"])


def side(rid, team):
    d = st.get(rid, {})
    return [v for m, v in d.items() if m in mp and mp[m]["team"] == team]


by_match = collections.defaultdict(dict)
for rid, r in rounds.items():
    by_match[r["match_id"]][r["round_number"]] = r

rows = []
for mid, rs in by_match.items():
    for pistol_rn in (1, 13):
        p = rs.get(pistol_rn)
        if not p or not usable(p):
            continue
        pistol_winner = winner(p["outcome"])
        for rn in (pistol_rn + 1, pistol_rn + 2):          # 2,3 and 14,15
            cur, nxt = rs.get(rn), rs.get(rn + 1)
            if not cur or not nxt or not usable(cur) or not usable(nxt):
                continue
            w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
            for team in ("TEAM_1", "TEAM_2"):
                other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
                u_cur, u_nxt = side(cur["id"], team), side(nxt["id"], team)
                e_cur = side(cur["id"], other)
                if not (u_cur and u_nxt and e_cur):
                    continue
                own_dead = sum(1 for v in deaths.get(cur["id"], [])
                               if v and v in mp and mp[v]["team"] == team)
                rows.append({
                    "mid": mid, "rn": rn, "phase": "R2" if rn in (2, 14) else "R3",
                    "own_deaths": own_dead,
                    "own_loadout_cur": sum(l for l, _ in u_cur) / len(u_cur),
                    "enemy_loadout_cur": sum(l for l, _ in e_cur) / len(e_cur),
                    "own_loadout_next": sum(l for l, _ in u_nxt) / len(u_nxt),
                    "own_armed_next": sum(1 for l, _ in u_nxt if l >= ARMED),
                    "own_fb_next": sum(1 for l, _ in u_nxt if l >= FULL_BUY),
                    "won_nxt": wn == team,
                    "won_cur": w == team,
                    "is_pistol_winner": team == pistol_winner,
                })
                rows[-1]["is_full_buy_team"] = rows[-1]["own_loadout_cur"] >= FULL_BUY

print(f"sample: {len(rows):,} team-rounds over {len(set(r['mid'] for r in rows)):,} matches")


def boot_mean(subset, key, seed=SEED):
    per = collections.defaultdict(lambda: [0.0, 0])
    for r in subset:
        per[r["mid"]][0] += r[key]
        per[r["mid"]][1] += 1
    keys = list(per)
    if not keys:
        return None
    ts = sum(per[k][0] for k in keys)
    tn = sum(per[k][1] for k in keys)
    rng = random.Random(seed)
    out = []
    for _ in range(N_BOOT):
        s = n = 0.0
        for _ in range(len(keys)):
            a, b = per[keys[rng.randrange(len(keys))]]
            s += a
            n += b
        if n:
            out.append(s / n)
    out.sort()
    return (ts / tn, out[int(.025 * len(out))], out[int(.975 * len(out))], int(tn))


def boot_diff(lo_rows, hi_rows, key, seed=SEED):
    """Bootstrap the DIFFERENCE of means, resampling matches once for both arms."""
    def per_match(rs):
        d = collections.defaultdict(lambda: [0.0, 0])
        for r in rs:
            d[r["mid"]][0] += r[key]
            d[r["mid"]][1] += 1
        return d
    A, B = per_match(lo_rows), per_match(hi_rows)
    keys = sorted(set(A) | set(B))
    if not keys:
        return None

    def val(d, sel):
        s = n = 0.0
        for k in sel:
            if k in d:
                s += d[k][0]
                n += d[k][1]
        return s / n if n else None
    pa, pb = val(A, keys), val(B, keys)
    if pa is None or pb is None:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(N_BOOT):
        sel = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
        a, b = val(A, sel), val(B, sel)
        if a is not None and b is not None:
            out.append(b - a)
    if not out:
        return None
    out.sort()
    return (pb - pa, out[int(.025 * len(out))], out[int(.975 * len(out))])


READOUTS = [("own_loadout_next", "own loadout N+1 (cr)", 1, ""),
            ("own_armed_next", "own armed N+1", 1, ""),
            ("own_fb_next", "own full-buy N+1", 1, ""),
            ("won_nxt", "win N+1", 100, "pp")]

CELLS = [
    ("(a) R2  pistol WINNERS that WON round 2      -> round 3",
     lambda r: r["phase"] == "R2" and r["is_pistol_winner"] and r["won_cur"]),
    ("(b) R2  pistol winners that LOST round 2     -> round 3",
     lambda r: r["phase"] == "R2" and r["is_pistol_winner"] and not r["won_cur"]),
    ("(c) R3  the FULL-BUY team that WON round 3   -> round 4",
     lambda r: r["phase"] == "R3" and r["is_full_buy_team"] and r["won_cur"]),
    ("(d) R3  pistol LOSERS that WON round 3       -> round 4",
     lambda r: r["phase"] == "R3" and not r["is_pistol_winner"] and r["won_cur"]),
]

for label, sel in CELLS:
    sub = [r for r in rows if sel(r)]
    print("\n" + "=" * 122)
    print(f"{label}   (n={len(sub):,} team-rounds, {len(set(r['mid'] for r in sub)):,} matches)")
    print("=" * 122)
    if len(sub) < MIN_CELL:
        print("  insufficient data")
        continue
    print(f"  {'own deaths':>10} | {'own loadout N+1 (cr)':>28} | {'own armed N+1':>22} | "
          f"{'own full-buy N+1':>22} | {'win N+1':>16}")
    curve = {}
    for nd in range(0, 6):
        cell = [r for r in sub if r["own_deaths"] == nd]
        if len(cell) < MIN_CELL:
            print(f"  {nd:>10} | {'--':>28} | {'--':>22} | {'--':>22} | {'--':>16}   n={len(cell):,}")
            continue
        curve[nd] = cell
        L = boot_mean(cell, "own_loadout_next")
        A = boot_mean(cell, "own_armed_next")
        F = boot_mean(cell, "own_fb_next")
        W = boot_mean(cell, "won_nxt")
        print(f"  {nd:>10} | {L[0]:7.0f} [{L[1]:6.0f},{L[2]:6.0f}] n={L[3]:,}".ljust(46)
              + f" | {A[0]:4.2f} [{A[1]:4.2f},{A[2]:4.2f}]".rjust(25)
              + f" | {F[0]:4.2f} [{F[1]:4.2f},{F[2]:4.2f}]".rjust(25)
              + f" | {100 * W[0]:5.1f}%".rjust(19))

    print("\n  CONSECUTIVE differences (predeclared shape test -- a nonlinearity is claimed")
    print("  only where one of these excludes zero, marked *):")
    for nd in range(0, 5):
        if nd not in curve or nd + 1 not in curve:
            continue
        parts = []
        for key, lab, mult, unit in READOUTS:
            d = boot_diff(curve[nd], curve[nd + 1], key)
            if d is None:
                continue
            star = "*" if (d[1] > 0) == (d[2] > 0) else " "
            parts.append(f"{lab}: {mult * d[0]:+7.2f}{unit} [{mult * d[1]:+7.2f},{mult * d[2]:+7.2f}]{star}")
        print(f"    {nd} -> {nd + 1} deaths:  " + "   ".join(parts))

    lo = [r for r in sub if r["own_deaths"] <= 1]
    hi = [r for r in sub if 3 <= r["own_deaths"] <= 4]
    if len(lo) >= MIN_CELL and len(hi) >= MIN_CELL:
        print(f"\n  HEADLINE  (3-4 deaths) - (0-1 deaths)   n_lo={len(lo):,} n_hi={len(hi):,}")
        for key, lab, mult, unit in READOUTS:
            d = boot_diff(lo, hi, key)
            if d is None:
                continue
            star = " *excludes 0" if (d[1] > 0) == (d[2] > 0) else "  spans 0"
            print(f"    {lab:<24}: {mult * d[0]:+8.2f}{unit} [{mult * d[1]:+8.2f}, {mult * d[2]:+8.2f}]{star}")

    # Does a dirty win just mean the enemy bought well? Stratify on enemy round-N loadout.
    BANDS = [("enemy poor  <2100", lambda v: v < 2100),
             ("enemy mid   2100-4249", lambda v: 2100 <= v < 4250),
             ("enemy rich  >=4250", lambda v: v >= 4250)]
    print("\n  Same headline, stratified by the ENEMY'S round-N loadout")
    print("  (guards against 'a dirty win just means they bought well'):")
    for blab, bfn in BANDS:
        s2 = [r for r in sub if bfn(r["enemy_loadout_cur"])]
        l2 = [r for r in s2 if r["own_deaths"] <= 1]
        h2 = [r for r in s2 if 3 <= r["own_deaths"] <= 4]
        if len(l2) < MIN_CELL or len(h2) < MIN_CELL:
            print(f"    {blab:<24}: insufficient (n_lo={len(l2):,} n_hi={len(h2):,})")
            continue
        parts = []
        for key, lab, mult, unit in (READOUTS[0], READOUTS[3]):
            d = boot_diff(l2, h2, key)
            if d is None:
                continue
            star = "*" if (d[1] > 0) == (d[2] > 0) else " "
            parts.append(f"{lab}: {mult * d[0]:+8.2f}{unit} [{mult * d[1]:+8.2f},{mult * d[2]:+8.2f}]{star}")
        print(f"    {blab:<24}: n={len(s2):,}   " + "   ".join(parts))

# ---------------------------------------------------------------------------
# PART 2 -- the DEFERRED round-2 effect, measured at N+2 instead of N+1.
#
# Cell (a) above reverses: killing pistol winners in round 2 leaves them with
# MORE full buys in round 3, because a survivor keeps a cheap carried gun while
# a casualty rebuys with two rounds of winnings behind them. But that full buy
# DRAINS THE BANK. So the denial should not be looked for in round 3 at all --
# it should appear in round 4, and only for the teams that then lost round 3
# and so lost the equipment they had just spent everything on.
#
# The channel is therefore the BANK (round_player_stats.remaining, verified as
# a bank in M12d), and the split is the round-3 outcome. Predeclared: the
# hypothesis is supported only if, among pistol winners who won round 2 and
# then LOST round 3, more round-2 deaths leaves them measurably poorer in
# round 4, with the bootstrapped difference excluding zero.
# ---------------------------------------------------------------------------
rows2 = []
for mid, rs in by_match.items():
    for pistol_rn in (1, 13):
        p = rs.get(pistol_rn)
        if not p or not usable(p):
            continue
        pw = winner(p["outcome"])
        r2, r3, r4 = rs.get(pistol_rn + 1), rs.get(pistol_rn + 2), rs.get(pistol_rn + 3)
        if not all(x is not None and usable(x) for x in (r2, r3, r4)):
            continue
        if winner(r2["outcome"]) != pw:          # restrict to pistol winners who WON round 2
            continue
        u2, u3, u4 = side(r2["id"], pw), side(r3["id"], pw), side(r4["id"], pw)
        if not (u2 and u3 and u4):
            continue
        own_dead2 = sum(1 for v in deaths.get(r2["id"], [])
                        if v and v in mp and mp[v]["team"] == pw)
        rows2.append({
            "mid": mid,
            "own_deaths": own_dead2,
            "bank_r3": sum(b for _, b in u3) / len(u3),
            "loadout_r3": sum(l for l, _ in u3) / len(u3),
            "fb_r3": sum(1 for l, _ in u3 if l >= FULL_BUY),
            "bank_r4": sum(b for _, b in u4) / len(u4),
            "loadout_r4": sum(l for l, _ in u4) / len(u4),
            "fb_r4": sum(1 for l, _ in u4 if l >= FULL_BUY),
            "armed_r4": sum(1 for l, _ in u4 if l >= ARMED),
            "won_r3": winner(r3["outcome"]) == pw,
            "won_r4": winner(r4["outcome"]) == pw,
        })

DEFERRED = [("bank_r3", "bank R3 (cr)", 1, ""),
            ("loadout_r4", "loadout R4 (cr)", 1, ""),
            ("fb_r4", "full-buys R4", 1, ""),
            ("armed_r4", "armed R4", 1, ""),
            ("won_r4", "win R4", 100, "pp")]

print("\n" + "=" * 122)
print("PART 2 -- the DEFERRED effect: pistol WINNERS that WON round 2, followed to ROUND 4")
print("Does a round-2 death force a round-3 full buy that leaves them broke in round 4?")
print("=" * 122)

for split_label, split_fn in (("ALL", lambda r: True),
                              ("then LOST round 3", lambda r: not r["won_r3"]),
                              ("then WON round 3", lambda r: r["won_r3"])):
    sub = [r for r in rows2 if split_fn(r)]
    print(f"\n  --- {split_label}  (n={len(sub):,} team-halves, "
          f"{len(set(r['mid'] for r in sub)):,} matches) ---")
    if len(sub) < MIN_CELL:
        print("    insufficient data")
        continue
    print(f"    {'R2 deaths':>10} | {'bank R3 (cr)':>26} | {'loadout R4 (cr)':>26} | "
          f"{'full-buys R4':>22} | {'win R4':>10}")
    curve = {}
    for nd in range(0, 6):
        cell = [r for r in sub if r["own_deaths"] == nd]
        if len(cell) < MIN_CELL:
            print(f"    {nd:>10} | {'--':>26} | {'--':>26} | {'--':>22} | {'--':>10}   n={len(cell):,}")
            continue
        curve[nd] = cell
        B = boot_mean(cell, "bank_r3")
        L = boot_mean(cell, "loadout_r4")
        F = boot_mean(cell, "fb_r4")
        W = boot_mean(cell, "won_r4")
        print(f"    {nd:>10} | {B[0]:6.0f} [{B[1]:5.0f},{B[2]:5.0f}] n={B[3]:,}".ljust(44)
              + f" | {L[0]:6.0f} [{L[1]:5.0f},{L[2]:5.0f}]".rjust(29)
              + f" | {F[0]:4.2f} [{F[1]:4.2f},{F[2]:4.2f}]".rjust(25)
              + f" | {100 * W[0]:5.1f}%".rjust(13))
    lo = [r for r in sub if r["own_deaths"] <= 1]
    hi = [r for r in sub if 3 <= r["own_deaths"] <= 4]
    if len(lo) >= MIN_CELL and len(hi) >= MIN_CELL:
        print(f"\n    HEADLINE  (3-4 R2 deaths) - (0-1 R2 deaths)   n_lo={len(lo):,} n_hi={len(hi):,}")
        for key, lab, mult, unit in DEFERRED:
            d = boot_diff(lo, hi, key)
            if d is None:
                continue
            star = " *excludes 0" if (d[1] > 0) == (d[2] > 0) else "  spans 0"
            print(f"      {lab:<18}: {mult * d[0]:+9.2f}{unit} "
                  f"[{mult * d[1]:+9.2f}, {mult * d[2]:+9.2f}]{star}")

# ---------------------------------------------------------------------------
# PART 3 -- round 2 conditioned on BUY-IN.
#
# M28 found the round-2 OUTCOME still runs the wrong way (+11.74pp) even after
# the unified total-wealth readout fixed the mediator's sign. The candidate
# missing condition: destroying equipment only denies something if they BOUGHT
# it. A team that saved round 2 has nothing to take, so their deaths should cost
# them nothing -- and if the anomaly is concentrated in the savers, buy-in is
# the condition the component needs.
#
# Predeclared: buy-in is the missing condition only if the win-N+1 difference is
# materially less positive (or negative) in the bought-in stratum than in the
# saving stratum. Terciles of the victim team's own round-2 loadout, cut points
# computed from the exposure's distribution alone and printed below -- never
# from any outcome.
# ---------------------------------------------------------------------------
rows3 = []
for mid, rs in by_match.items():
    for pistol_rn in (1, 13):
        p = rs.get(pistol_rn)
        if not p or not usable(p):
            continue
        pw = winner(p["outcome"])
        r2, r3, r4 = rs.get(pistol_rn + 1), rs.get(pistol_rn + 2), rs.get(pistol_rn + 3)
        if not all(x is not None and usable(x) for x in (r2, r3, r4)):
            continue
        team = winner(r2["outcome"])          # the team that WON round 2 (ladder pinned)
        u2, u3, u4 = side(r2["id"], team), side(r3["id"], team), side(r4["id"], team)
        if not (u2 and u3 and u4):
            continue
        own_dead = sum(1 for v in deaths.get(r2["id"], [])
                       if v and v in mp and mp[v]["team"] == team)
        l3 = sum(l for l, _ in u3) / len(u3)
        b3 = sum(b for _, b in u3) / len(u3)
        l4 = sum(l for l, _ in u4) / len(u4)
        b4 = sum(b for _, b in u4) / len(u4)
        rows3.append({
            "mid": mid,
            "own_deaths": own_dead,
            "buyin": sum(l for l, _ in u2) / len(u2),
            "is_pistol_winner": team == pw,
            "wealth_r3": l3 + b3,
            "loadout_r3": l3,
            "bank_r3": b3,
            "wealth_r4": l4 + b4,
            "fb_r4": sum(1 for l, _ in u4 if l >= FULL_BUY),
            "won_r3": winner(r3["outcome"]) == team,
            "won_r4": winner(r4["outcome"]) == team,
        })

print("\n" + "=" * 122)
print("PART 3 -- ROUND 2 CONDITIONED ON BUY-IN: you can only deny what they bought")
print("=" * 122)
buyins = sorted(r["buyin"] for r in rows3)
if len(buyins) >= 300:
    qs = [buyins[int(q * len(buyins))] for q in (.10, .3333, .50, .6667, .90)]
    print(f"  victim team's own round-2 loadout: p10={qs[0]:.0f}  t1={qs[1]:.0f}  "
          f"p50={qs[2]:.0f}  t2={qs[3]:.0f}  p90={qs[4]:.0f}   (n={len(buyins):,})")
    C1, C2 = qs[1], qs[3]
    STRATA = [(f"SAVED      <{C1:.0f}", lambda r: r["buyin"] < C1),
              (f"LIGHT   {C1:.0f}-{C2:.0f}", lambda r: C1 <= r["buyin"] < C2),
              (f"BOUGHT IN >={C2:.0f}", lambda r: r["buyin"] >= C2),
              ("pistol WINNERS only", lambda r: r["is_pistol_winner"]),
              ("pistol LOSERS only", lambda r: not r["is_pistol_winner"])]
    R3KEYS = [("wealth_r3", "wealth R3 (cr)", 1, ""), ("bank_r3", "bank R3 (cr)", 1, ""),
              ("won_r3", "win R3", 100, "pp"), ("wealth_r4", "wealth R4 (cr)", 1, ""),
              ("fb_r4", "full-buys R4", 1, ""), ("won_r4", "win R4", 100, "pp")]
    for slab, sfn in STRATA:
        sub = [r for r in rows3 if sfn(r)]
        lo = [r for r in sub if r["own_deaths"] <= 1]
        hi = [r for r in sub if 3 <= r["own_deaths"] <= 4]
        print(f"\n  --- {slab}   n={len(sub):,}  (n_lo={len(lo):,} n_hi={len(hi):,}) ---")
        if len(lo) < MIN_CELL or len(hi) < MIN_CELL:
            print("      insufficient")
            continue
        mean_buy = sum(r["buyin"] for r in sub) / len(sub)
        print(f"      mean round-2 loadout {mean_buy:.0f} cr")
        for key, lab, mult, unit in R3KEYS:
            d = boot_diff(lo, hi, key)
            if d is None:
                continue
            star = " *excludes 0" if (d[1] > 0) == (d[2] > 0) else "  spans 0"
            print(f"      {lab:<16}: {mult * d[0]:+9.2f}{unit} "
                  f"[{mult * d[1]:+9.2f}, {mult * d[2]:+9.2f}]{star}")
else:
    print("  insufficient data")

# ---------------------------------------------------------------------------
# Is the M10 confound (enemy wealth) actually weaker early? The claim is that
# econ is low for BOTH teams in the early rounds, so there is little wealth
# variance for it to work through. Testable directly.
# ---------------------------------------------------------------------------
print("\n" + "=" * 122)
print("ENEMY-WEALTH SPREAD, EARLY vs LATE -- is there less wealth variance for M10's confound to use?")
print("=" * 122)
SPREAD = [("2-4 / 14-16", {2, 3, 4, 14, 15, 16}),
          ("5-11 / 17-23", {5, 6, 7, 8, 9, 10, 11, 17, 18, 19, 20, 21, 22, 23})]
print(f"  {'rounds':>14} | {'n team-rounds':>14} | {'mean':>8} | {'sd':>8} | "
      f"{'p10':>7} | {'p50':>7} | {'p90':>7} | {'p90-p10':>8}")
for lab, rset in SPREAD:
    vals = []
    for mid, rs in by_match.items():
        for rn in rset:
            r = rs.get(rn)
            if not r or not usable(r):
                continue
            for team in ("TEAM_1", "TEAM_2"):
                s = side(r["id"], team)
                if s:
                    vals.append(sum(l for l, _ in s) / len(s))
    if len(vals) < 100:
        continue
    vals.sort()
    p10, p50, p90 = (vals[int(q * len(vals))] for q in (.10, .50, .90))
    print(f"  {lab:>14} | {len(vals):>14,} | {statistics.mean(vals):>8.0f} | "
          f"{statistics.pstdev(vals):>8.0f} | {p10:>7.0f} | {p50:>7.0f} | {p90:>7.0f} | {p90 - p10:>8.0f}")
print("\n  A materially smaller spread early supports treating M10's wealth confound as weak")
print("  there; a comparable spread means the confound is live and the stratified rows above")
print("  are the ones to read.")
