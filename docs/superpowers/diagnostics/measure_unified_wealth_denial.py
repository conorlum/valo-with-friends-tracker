"""Does ONE readout -- total wealth -- carry the denial at EVERY round?

M27 found the econ denial lands in two different accounts depending on whether
the victim team had cash to convert:

  round 2 (they have cash) -- they rebuy, so their BANK falls (-842 credits)
                              and their loadout barely moves (-260)
  round 3+ (they do not)   -- they cannot re-arm, so their LOADOUT falls
                              (-652) and full-buys collapse

That is one construct (M12d's "resources removed that they could not replace")
showing up in two accounts. A readout keyed on full-buy COUNT reads only one of
them, which is why it works late and inverts early -- and why the econ spec
currently needs a regime split at round 5 whose boundary was itself picked from
outcome-fitted evidence (M12b/M12c).

THE HYPOTHESIS: total wealth in N+1 = loadout + remaining unifies them. Kill
them and they rebuy -> loadout holds, bank drops. Kill them and they cannot ->
loadout drops, bank stays low. Either way total wealth falls.

PREDECLARED ACCEPTANCE RULE, stated before running. The unified readout replaces
the regime split only if the (3-4 deaths) - (0-1 deaths) difference on TOTAL
WEALTH is negative and excludes zero at EVERY round position with a sufficient
cell. A single round position where it is positive or spans zero means the
split survives and the two-regime spec is written instead. The decomposition
into loadout and bank is reported alongside so the account-shift can be seen
directly rather than assumed.

IDENTIFICATION, inherited from M27 deliberately so results stay comparable.
M13 shows the loss-bonus ladder fixes a ~3 full-buy spread before round 4
begins, so a raw round-N+1 readout mostly reads the ladder. Conditioning on the
team having WON round N pins the ladder position and varies only what was
carried forward. The exposure is that team's OWN deaths in round N, which is
the same table as "kills inflicted on them" read from the other side.

Rounds 12 and 24 are excluded as round N: the economy resets into the next
half's pistol, so there is no next-round link. Rounds 1 and 13 are excluded as
pistols with no prior economy to damage.

Part of the Impact measurement record. Supports: M28 (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_unified_wealth_denial.py

Reads only; never writes to the database.

LIMITATION: own deaths, round outcome and both teams' buys are jointly
determined within a round. Conditioning on the round-N outcome closes the
ladder path, not all of them -- site taken, plant status, man-advantage
trajectory and the team's own spending decision remain uncontrolled.
Conditional associations, not effects.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal

FULL_BUY = 4200     # matches FULL_BUY_THRESHOLD in app/scoring/impact.py
N_BOOT, SEED = 800, 8181
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

VALID_N = set(range(2, 12)) | set(range(14, 24))     # 2-11 and 14-23; 12/24 reset out

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn not in VALID_N:
            continue
        cur, nxt = rs.get(rn), rs.get(rn + 1)
        if not cur or not nxt or not usable(cur) or not usable(nxt):
            continue
        w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
        team = w                                     # the team that WON round N
        u_cur, u_nxt = side(cur["id"], team), side(nxt["id"], team)
        if not (u_cur and u_nxt):
            continue
        own_dead = sum(1 for v in deaths.get(cur["id"], [])
                       if v and v in mp and mp[v]["team"] == team)
        load = sum(l for l, _ in u_nxt) / len(u_nxt)
        bank = sum(b for _, b in u_nxt) / len(u_nxt)
        rows.append({
            "mid": mid,
            "pos": rn if rn <= 11 else rn - 12,       # pool the two halves by position
            "own_deaths": own_dead,
            "wealth_next": load + bank,
            "loadout_next": load,
            "bank_next": bank,
            "fb_next": sum(1 for l, _ in u_nxt if l >= FULL_BUY),
            "won_nxt": wn == team,
        })

print(f"sample: {len(rows):,} team-rounds over {len(set(r['mid'] for r in rows)):,} matches")
print("(one row per round the team WON, rounds 2-11 / 14-23, halves pooled by position)")


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


def cell(sub, lo_pred, hi_pred):
    return ([r for r in sub if lo_pred(r)], [r for r in sub if hi_pred(r)])


LO = lambda r: r["own_deaths"] <= 1
HI = lambda r: 3 <= r["own_deaths"] <= 4

print("\n" + "=" * 126)
print("THE DECISIVE TABLE -- (3-4 own deaths) - (0-1 own deaths), by round position")
print("The unified readout replaces the regime split only if TOTAL WEALTH is negative")
print("and excludes zero at EVERY position. * marks an interval excluding zero.")
print("=" * 126)
print(f"  {'round':>6} | {'n':>7} | {'TOTAL WEALTH':>28} | {'loadout':>22} | "
      f"{'bank':>22} | {'win N+1':>18}")
positions = sorted({r["pos"] for r in rows})
verdict = []
for pos in positions:
    sub = [r for r in rows if r["pos"] == pos]
    lo, hi = cell(sub, LO, HI)
    if len(lo) < MIN_CELL or len(hi) < MIN_CELL:
        print(f"  {pos:>6} | {len(sub):>7,} | insufficient (n_lo={len(lo):,} n_hi={len(hi):,})")
        continue
    dW = boot_diff(lo, hi, "wealth_next")
    dL = boot_diff(lo, hi, "loadout_next")
    dB = boot_diff(lo, hi, "bank_next")
    dV = boot_diff(lo, hi, "won_nxt")
    sW = "*" if (dW[1] > 0) == (dW[2] > 0) else " "
    sL = "*" if (dL[1] > 0) == (dL[2] > 0) else " "
    sB = "*" if (dB[1] > 0) == (dB[2] > 0) else " "
    sV = "*" if (dV[1] > 0) == (dV[2] > 0) else " "
    verdict.append((pos, dW, sW))
    print(f"  {pos:>6} | {len(sub):>7,} | "
          f"{dW[0]:+8.0f} [{dW[1]:+7.0f},{dW[2]:+7.0f}]{sW}".rjust(29) + " | "
          + f"{dL[0]:+7.0f} [{dL[1]:+6.0f},{dL[2]:+6.0f}]{sL}".rjust(22) + " | "
          + f"{dB[0]:+7.0f} [{dB[1]:+6.0f},{dB[2]:+6.0f}]{sB}".rjust(22) + " | "
          + f"{100*dV[0]:+6.2f}pp [{100*dV[1]:+6.2f},{100*dV[2]:+6.2f}]{sV}".rjust(19))

print("\n  VERDICT against the predeclared rule:")
bad = [(p, d) for p, d, s in verdict if not (d[0] < 0 and s == "*")]
if not verdict:
    print("    no position had a sufficient cell -- inconclusive")
elif not bad:
    print(f"    TOTAL WEALTH is negative and excludes zero at all {len(verdict)} positions")
    print("    -> the unified readout PASSES; the regime split is not needed for this reason")
else:
    print(f"    {len(bad)} of {len(verdict)} positions fail (positive or spanning zero): "
          + ", ".join(f"round {p} ({d[0]:+.0f} [{d[1]:+.0f},{d[2]:+.0f}])" for p, d in bad))
    print("    -> the unified readout FAILS the predeclared rule; the split survives")

print("\n" + "=" * 126)
print("THE ACCOUNT SHIFT -- where the denial lands, early vs late")
print("Bank-dominated early (they convert cash to guns) and loadout-dominated late")
print("(they cannot) is the pattern M27 predicts. Shares of the total-wealth difference.")
print("=" * 126)
BANDS = [("2-4", lambda p: 2 <= p <= 4), ("5-7", lambda p: 5 <= p <= 7),
         ("8-11", lambda p: 8 <= p <= 11)]
print(f"  {'rounds':>8} | {'n':>8} | {'TOTAL WEALTH':>26} | {'loadout share':>15} | {'bank share':>12}")
for blab, bfn in BANDS:
    sub = [r for r in rows if bfn(r["pos"])]
    lo, hi = cell(sub, LO, HI)
    if len(lo) < MIN_CELL or len(hi) < MIN_CELL:
        print(f"  {blab:>8} | insufficient")
        continue
    dW = boot_diff(lo, hi, "wealth_next")
    dL = boot_diff(lo, hi, "loadout_next")
    dB = boot_diff(lo, hi, "bank_next")
    tot = dL[0] + dB[0]
    sh_l = 100 * dL[0] / tot if tot else float("nan")
    sh_b = 100 * dB[0] / tot if tot else float("nan")
    print(f"  {blab:>8} | {len(sub):>8,} | {dW[0]:+8.0f} [{dW[1]:+7.0f},{dW[2]:+7.0f}]".rjust(30)
          + f" | {sh_l:>13.0f}% | {sh_b:>10.0f}%")

print("\n" + "=" * 126)
print("MONOTONICITY -- the full 0-4 curve on total wealth, pooled by band")
print("=" * 126)
for blab, bfn in BANDS:
    sub = [r for r in rows if bfn(r["pos"])]
    if len(sub) < MIN_CELL:
        continue
    parts = []
    for nd in range(0, 5):
        c = [r for r in sub if r["own_deaths"] == nd]
        if len(c) < MIN_CELL:
            parts.append(f"{nd}: --")
            continue
        m = boot_mean(c, "wealth_next")
        parts.append(f"{nd}: {m[0]:.0f}")
    print(f"  rounds {blab:<5}  " + "   ".join(parts))
