"""Is there a KILL-COUNT THRESHOLD at which destruction denies the enemy's next buy?

The hypothesis, which no existing measurement addresses: 1 kill changes nothing,
but 4 kills means the enemy cannot full buy and has to force. Destruction is
worth looking at only when it adds up to cross a buy threshold.

WHY THIS IS NOT M12a. M12a held enemies-killed FIXED and asked whether the
remaining variation in destroyed value predicted anything. That remaining
variation is how RICH the enemies you killed were (M10), and the answer was no.
But if the economic path runs THROUGH kill count -- kills -> they cannot rebuy ->
you win N+1 -- then holding kill count fixed is controlling for a MEDIATOR and
blocks the path being asked about. Kill count is the exposure here, not a
confound, and is deliberately NOT adjusted away.

The confound that does need handling is the round-N outcome: killing more
correlates with winning round N, which predicts winning N+1 on its own. So the
tables are split by round-N outcome and read primarily off the LOST rows, where
the won-the-round path is closed and what remains is the damage done on the way
out.

Reported at each kill count:
  (a) the MEDIATOR   -- enemy full-buy count and mean credits entering N+1
  (b) the OUTCOME    -- your win rate in N+1
so a threshold in (a) can be checked against a step in (b) rather than assumed.

Part of the Impact measurement record. Supports: M12c (proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_kill_count_econ_threshold.py

Reads only; never writes to the database.

LIMITATION: kills, round outcome and next-round economy are jointly determined
within a round. Splitting on the round-N outcome closes one path, not all of
them -- man-advantage trajectory, plant status and who held map control are
uncontrolled. Conditional associations, not effects.
"""
import os, sys, collections, random

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import text
from app.db import SessionLocal
from _bootstrap import boot_rate

FULL_BUY = 4200
SEED = 909

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


def loadouts(rid, team):
    d = st.get(rid, {})
    return [lo for m, lo in d.items() if m in mp and mp[m]["team"] == team]


GROUPS = [("2-4 / 14-16", {2, 3, 4, 14, 15, 16}),
          ("5-7 / 17-19", {5, 6, 7, 17, 18, 19}),
          ("8-11 / 20-23", {8, 9, 10, 11, 20, 21, 22, 23})]
GROUP_OF = {rn: lab for lab, s in GROUPS for rn in s}

rows = []
for mid, rs in by_match.items():
    for rn in sorted(rs):
        if rn not in GROUP_OF: continue
        cur, nxt = rs.get(rn), rs.get(rn + 1)
        if not cur or not nxt or not usable(cur) or not usable(nxt): continue
        if not st.get(cur["id"]) or not st.get(nxt["id"]): continue
        w, wn = winner(cur["outcome"]), winner(nxt["outcome"])
        for team in ("TEAM_1", "TEAM_2"):
            other = "TEAM_2" if team == "TEAM_1" else "TEAM_1"
            enemy_next = loadouts(nxt["id"], other)
            if not enemy_next: continue
            victims = [v for v in deaths.get(cur["id"], []) if v and v in mp and mp[v]["team"] == other]
            rows.append({
                "mid": mid, "g": GROUP_OF[rn], "rn": rn,
                "n_killed": len(victims),
                "destroyed": sum(st[cur["id"]].get(v, 0) for v in victims),
                "won_cur": w == team, "won_nxt": wn == team,
                "enemy_fb_next": sum(1 for x in enemy_next if x >= FULL_BUY),
                "enemy_cred_next": sum(enemy_next) / len(enemy_next),
            })

print(f"sample: {len(rows):,} team-rounds over {len(set(r['mid'] for r in rows)):,} matches")


def mean_ci(subset, key, n_boot=800, seed=SEED):
    """Match-level bootstrap of a mean."""
    per = collections.defaultdict(lambda: [0.0, 0])
    for r in subset:
        per[r["mid"]][0] += r[key]; per[r["mid"]][1] += 1
    keys = list(per)
    if not keys: return None
    tot_s = sum(per[k][0] for k in keys); tot_n = sum(per[k][1] for k in keys)
    rng = random.Random(seed); out = []
    for _ in range(n_boot):
        s = n = 0.0
        for _ in range(len(keys)):
            a, b = per[keys[rng.randrange(len(keys))]]
            s += a; n += b
        if n: out.append(s / n)
    out.sort()
    return (tot_s / tot_n, out[int(.025 * len(out))], out[int(.975 * len(out))], int(tot_n))


for won_cur in (False, True):
    tag = "LOST round N  (the 'took some with them' case -- read this one first)" if not won_cur \
        else "WON round N   (won-the-round path is open; read with care)"
    print("\n" + "=" * 122)
    print(f"({'1' if not won_cur else '2'}) TEAMS THAT {tag}")
    print("    mediator = enemy economy entering N+1;  outcome = your win rate in N+1")
    print("=" * 122)
    for glab, _ in GROUPS:
        print(f"\n  --- rounds {glab} ---")
        print(f"  {'enemies killed':>14} | {'enemy full-buys N+1':>26} | {'enemy mean creds N+1':>24} | {'you win N+1':>24}")
        for nk in range(0, 6):
            cell = [r for r in rows if r["won_cur"] == won_cur and r["g"] == glab and r["n_killed"] == nk]
            if len(cell) < 150:
                print(f"  {nk:>14} | {'--':>26} | {'--':>24} | {'--':>24}")
                continue
            fbm = mean_ci(cell, "enemy_fb_next")
            crm = mean_ci(cell, "enemy_cred_next")
            wr = boot_rate([(r["mid"], r["won_nxt"]) for r in cell])
            print(f"  {nk:>14} | {fbm[0]:5.2f} [{fbm[1]:4.2f},{fbm[2]:4.2f}] n={fbm[3]:,}".ljust(45)
                  + f" | {crm[0]:7.0f} [{crm[1]:5.0f},{crm[2]:5.0f}]".rjust(25)
                  + f" | {100*wr[0]:5.1f}% [{100*wr[1]:4.1f},{100*wr[2]:4.1f}]".rjust(25))

print("\n" + "=" * 122)
print("(3) IS THERE A STEP? Marginal gain in win rate from one more kill, teams that LOST round N.")
print("    A threshold predicts a jump at a particular k, not a constant slope.")
print("=" * 122)
for glab, _ in GROUPS:
    print(f"\n  --- rounds {glab} ---")
    prev = None
    for nk in range(0, 6):
        cell = [r for r in rows if not r["won_cur"] and r["g"] == glab and r["n_killed"] == nk]
        if len(cell) < 150:
            prev = None; continue
        wr = boot_rate([(r["mid"], r["won_nxt"]) for r in cell])
        fbm = mean_ci(cell, "enemy_fb_next")
        step = f"{100*(wr[0]-prev[0]):+6.2f}pp" if prev else "     --"
        print(f"    {nk} kills -> win {100*wr[0]:5.1f}%   step {step}   "
              f"enemy full-buys next {fbm[0]:4.2f}")
        prev = wr

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
