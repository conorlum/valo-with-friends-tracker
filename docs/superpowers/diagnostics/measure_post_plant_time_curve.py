"""Does post-plant kill value actually rise with time since the plant, and does
the shipped ramp match it?

`_time_factor` (impact.py:174-181) pays a side-blind ramp post-plant:
    1 + (kill_time - plant_time) / 53      -> 1.00 at the plant, 1.85 at +45
with a flat 1.75 override in [plant+38, plant+45] and 0.5 after resolution.

That ramp is inherited from matchDataPipeline.py and has NEVER been validated
against outcomes. M2 measured the ABSOLUTE CLOCK and found it flat, but only
among PRE-plant kills; there is no post-plant analogue in Layer 1.

Structural asymmetry this tests: a full defuse takes 7s, so a defender must
START defusing by plant+38 to finish before detonation at plant+45. A defender
kill after that cannot convert into a defuse win; an attacker kill then is
close to decisive. The ramp is blind to side, so it pays both identically.

Part of the Impact measurement record. Supports: M21, M22 (both proposed)
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_post_plant_time_curve.py

Reads only; never writes to the database.

STANDING LIMITATION, stated up front: a round only REACHES plant+40 if it was
not resolved earlier, so late buckets condition on a round having stayed
contested. This is post-treatment selection of exactly the kind the measurement
record's method section warns about, and it is NOT removed by conditioning on
man-advantage. These are conditional associations, not effects of time.
"""
import os, sys, collections

sys.path.insert(0, os.path.abspath("."))  # run from webapp/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.db import SessionLocal
from app.models.match import Team
from app.services.map_side_stats import attacking_team_for_round  # OT-aware reference impl
from app.scoring.impact import _time_factor, _check_for_resurrection
from _bootstrap import boot_rate

SPIKE_TIMER = 45.0
DEFUSE_FULL = 7.0

BUCKETS = [("0..5", 0.0, 5.0), ("5..10", 5.0, 10.0), ("10..15", 10.0, 15.0),
           ("15..25", 15.0, 25.0), ("25..35", 25.0, 35.0), ("35..45", 35.0, 45.0)]

db = SessionLocal()
n_matches = db.execute(text("SELECT count(*) FROM matches")).scalar()
print(f"DATASET: {n_matches:,} matches, "
      f"{db.execute(text('SELECT count(*) FROM rounds')).scalar():,} rounds, "
      f"{db.execute(text('SELECT count(*) FROM kill_events')).scalar():,} kill events")
if n_matches < 3000:
    print("\n*** REFUSING TO RUN: expected the full ~3,124-match set. ***")
    sys.exit(1)

rounds = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, match_id, round_number, outcome, planted, plant_time, exploded, defused, defuse_time "
    "FROM rounds")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, team FROM match_players")).mappings()}
kills_by_round = collections.defaultdict(list)
for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id")).mappings():
    kills_by_round[k["round_id"]].append(dict(k))
db.close()


def team_of(mpid):
    t = mp[mpid]["team"]
    return Team.TEAM_1 if t in ("TEAM_1", Team.TEAM_1) else Team.TEAM_2


def winner_of(outcome):
    if not outcome:
        return None
    return Team.TEAM_1 if outcome.startswith("Team A") else (Team.TEAM_2 if outcome.startswith("Team B") else None)


recs = []
for rid, ks in kills_by_round.items():
    r = rounds.get(rid)
    if r is None:
        continue
    out = r["outcome"] or ""
    if "Surrendered" in out or not r["planted"] or r["plant_time"] is None:
        continue
    if "Time Win" in out:                       # phantom plant (M16)
        continue
    w = winner_of(out)
    if w is None:
        continue
    pt = r["plant_time"]
    atk = attacking_team_for_round(r["round_number"])
    # When the round actually stopped being playable.
    resolution = pt + SPIKE_TIMER
    if r["defused"] and r["defuse_time"] is not None:
        resolution = min(resolution, r["defuse_time"])

    t1 = t2 = 5   # impact.py's convention: t1 tracks TEAM_2 alive, t2 tracks TEAM_1 alive
    for idx, kill in enumerate(ks):
        kid, vid = kill["killer_match_player_id"], kill["death_match_player_id"]
        self_kill = kid == vid
        if kid in mp and vid in mp and not self_kill:
            kt, vt = team_of(kid), team_of(vid)
            if kt != vt:
                dt = kill["event_time_seconds"] - pt
                own = t2 if kt == Team.TEAM_1 else t1
                opp = t1 if kt == Team.TEAM_1 else t2
                if 0 <= dt < SPIKE_TIMER and kill["event_time_seconds"] < resolution:
                    recs.append({
                        "mid": r["match_id"], "dt": dt, "adv": own - opp,
                        "state": (own, opp),
                        "atk": kt == atk,
                        "won": kt == w,
                        "tf": _time_factor(type("R", (), r)(), kill["event_time_seconds"]),
                    })
        if not _check_for_resurrection(idx, ks):
            if self_kill:
                if kid in mp and team_of(kid) == Team.TEAM_1:
                    t2 -= 1
                else:
                    t1 -= 1
            elif kid in mp and vid in mp:
                if team_of(kid) == Team.TEAM_1:
                    t1 -= 1
                else:
                    t2 -= 1

print(f"POPULATION: {len(recs):,} post-plant pre-resolution non-self kills in "
      f"non-phantom, non-surrendered planted rounds")
print(f"  attacker kills: {sum(1 for x in recs if x['atk']):,}   "
      f"defender kills: {sum(1 for x in recs if not x['atk']):,}")


def show(rows, label):
    r = boot_rate([(x["mid"], x["won"]) for x in rows])
    if r is None or r[3] < 150:
        return f"{'--':>21}"
    return f"{100*r[0]:5.1f} [{100*r[1]:4.1f},{100*r[2]:4.1f}] {r[3]//1000}k".rjust(21) \
        if r[3] >= 1000 else f"{100*r[0]:5.1f} [{100*r[1]:4.1f},{100*r[2]:4.1f}] {r[3]}".rjust(21)


print("\n" + "=" * 150)
print("(1) KILLER'S-TEAM ROUND-WIN %, by seconds since plant, SPLIT BY SIDE, within man-advantage.")
print("    The shipped ramp is side-blind: it pays both sides the same at the same dt.")
print("=" * 150)
for side_label, want_atk in (("ATTACKER kills", True), ("DEFENDER kills", False)):
    print(f"\n  --- {side_label} ---")
    print(f"  {'adv':>4} | " + " | ".join(f"{b[0]:>21}" for b in BUCKETS))
    for adv in (-2, -1, 0, 1, 2):
        cells = []
        for lab, lo, hi in BUCKETS:
            g = [x for x in recs if x["atk"] == want_atk and x["adv"] == adv and lo <= x["dt"] < hi]
            cells.append(show(g, lab))
        print(f"  {adv:>+4} | " + " | ".join(cells))
    print(f"  {'ALL':>4} | " + " | ".join(
        show([x for x in recs if x["atk"] == want_atk and lo <= x["dt"] < hi], lab)
        for lab, lo, hi in BUCKETS))

print("\n" + "=" * 150)
print("(2) THE DEFUSE DEADLINE. A full defuse needs 7s, so a defender kill after plant+38")
print("    cannot convert into a defuse win. Does defender kill value collapse there?")
print("=" * 150)
DEADLINE = SPIKE_TIMER - DEFUSE_FULL   # 38.0
for side_label, want_atk in (("ATTACKER", True), ("DEFENDER", False)):
    before = [x for x in recs if x["atk"] == want_atk and x["dt"] < DEADLINE]
    after = [x for x in recs if x["atk"] == want_atk and x["dt"] >= DEADLINE]
    rb, ra = boot_rate([(x["mid"], x["won"]) for x in before]), boot_rate([(x["mid"], x["won"]) for x in after])
    print(f"  {side_label:>8}  before +38s: {100*rb[0]:5.1f} [{100*rb[1]:4.1f},{100*rb[2]:4.1f}] n={rb[3]:,}"
          f"   |   at/after +38s: {100*ra[0]:5.1f} [{100*ra[1]:4.1f},{100*ra[2]:4.1f}] n={ra[3]:,}")

print("\n" + "=" * 150)
print("(3) WHAT THE SHIPPED RAMP PAYS vs THE MEASURED LIFT, pooled over advantage.")
print("    Ramp column is the mean _time_factor actually returned for kills in that bucket.")
print("=" * 150)
print(f"  {'bucket':>8} | {'ramp pays':>10} | {'ATT win%':>22} | {'DEF win%':>22} | {'n att':>8} | {'n def':>8}")
for lab, lo, hi in BUCKETS:
    a = [x for x in recs if x["atk"] and lo <= x["dt"] < hi]
    d = [x for x in recs if not x["atk"] and lo <= x["dt"] < hi]
    ramp = sum(x["tf"] for x in a + d) / max(1, len(a + d))
    ra, rd = boot_rate([(x["mid"], x["won"]) for x in a]), boot_rate([(x["mid"], x["won"]) for x in d])
    fa = f"{100*ra[0]:5.1f} [{100*ra[1]:4.1f},{100*ra[2]:4.1f}]" if ra else "--"
    fd = f"{100*rd[0]:5.1f} [{100*rd[1]:4.1f},{100*rd[2]:4.1f}]" if rd else "--"
    print(f"  {lab:>8} | {ramp:>10.3f} | {fa:>22} | {fd:>22} | {len(a):>8,} | {len(d):>8,}")

print("\n" + "=" * 150)
print("(4) MASS: where post-plant kills actually are (the population any retune would touch).")
print("=" * 150)
tot = len(recs)
for lab, lo, hi in BUCKETS:
    n = sum(1 for x in recs if lo <= x["dt"] < hi)
    print(f"  {lab:>8}: {n:>7,}  ({100*n/tot:4.1f}% of post-plant)")

print("\nInterpretation is a POLICY question and belongs in the spec, not here.")
