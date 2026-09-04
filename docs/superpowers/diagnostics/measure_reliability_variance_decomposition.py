"""WHY is site-participation reliability weak? Three candidate explanations,
separated: (A) sampling noise swamps true variance, (B) the trait drifts over
time, (C) there is no true between-player variance at all.

Part of the Impact measurement record. Supports: M17
See ../2026-09-04-impact-measurements.md

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\measure_reliability_variance_decomposition.py

Reads only; never writes to the database. Honours DATABASE_URL if set,
otherwise uses webapp/.env (see app/config.py).
"""
import os, sys, collections, random, statistics
sys.path.insert(0, os.path.abspath("."))  # run from webapp/
from sqlalchemy import text
from app.db import SessionLocal
db = SessionLocal(); PRE, POST = 30.0, 15.0

rounds = {r["id"]: dict(r) for r in db.execute(text("""
    SELECT r.id, r.match_id, r.round_number, r.outcome, r.planted, r.plant_time, m.played_at
    FROM rounds r JOIN matches m ON m.id=r.match_id""")).mappings()}
mp = {r["id"]: dict(r) for r in db.execute(text(
    "SELECT id, player_id, team FROM match_players")).mappings()}
def atk(rn):
    if rn<=12: return "TEAM_1"
    if rn<=24: return "TEAM_2"
    return "TEAM_1" if (rn-25)%2==0 else "TEAM_2"

ev=collections.defaultdict(list)
for k in db.execute(text("SELECT round_id, killer_match_player_id, event_time_seconds FROM kill_events")).mappings():
    r=rounds.get(k["round_id"])
    if not r or not r["planted"] or r["plant_time"] is None or not r["played_at"]: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    kid=k["killer_match_player_id"]
    if not kid or kid not in mp or mp[kid]["team"]==atk(r["round_number"]): continue
    ev[mp[kid]["player_id"]].append((r["played_at"], -PRE<=(k["event_time_seconds"]-r["plant_time"])<=POST))

MIN=100
players={p:e for p,e in ev.items() if len(e)>=MIN}
print("="*82)
print(f"(A) VARIANCE DECOMPOSITION -- {len(players)} players with >={MIN} qualifying kills")
print("="*82)
shares=[]; samp_var=[]
for p,e in players.items():
    n=len(e); s=sum(x[1] for x in e)/n
    shares.append(s); samp_var.append(s*(1-s)/n)
obs_var=statistics.pvariance(shares)
exp_samp=statistics.mean(samp_var)
true_var=max(0.0, obs_var-exp_samp)
print(f"  observed between-player SD : {100*obs_var**.5:6.2f} pp")
print(f"  expected sampling SD       : {100*exp_samp**.5:6.2f} pp   (binomial, at each player's own n)")
print(f"  implied TRUE SD            : {100*true_var**.5:6.2f} pp")
print(f"  -> reliability CEILING (true/observed variance) = {true_var/obs_var:.3f}")
print(f"  mean share {100*statistics.mean(shares):.1f}%, median kills/player {int(statistics.median(len(e) for e in players.values()))}")
print(f"\n  Interpretation: with a mean share near {100*statistics.mean(shares):.0f}% and a median of")
print(f"  ~{int(statistics.median(len(e) for e in players.values()))} kills, binomial noise alone produces roughly the spread we see.")

print("\n" + "="*82)
print("(B) IS IT DRIFT OR NOISE?  chronological split vs RANDOM split, same players")
print("="*82)
def pear(x,y):
    n=len(x); mx,my=sum(x)/n,sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    dx=sum((a-mx)**2 for a in x)**.5; dy=sum((b-my)**2 for b in y)**.5
    return cov/(dx*dy) if dx and dy else float('nan')
rng=random.Random(5)
for MINH in (30,50):
    chron=[]; rand=[]
    for p,e in ev.items():
        es=sorted(e,key=lambda t:t[0]); ts=sorted({t for t,_ in es})
        if len(ts)<4: continue
        cut=ts[len(ts)//2]
        a=[x for x in es if x[0]<cut]; b=[x for x in es if x[0]>=cut]
        if len(a)<MINH or len(b)<MINH: continue
        chron.append((sum(x[1] for x in a)/len(a), sum(x[1] for x in b)/len(b)))
        sh=es[:]; rng.shuffle(sh); h=len(sh)//2
        ra,rb=sh[:h],sh[h:]
        rand.append((sum(x[1] for x in ra)/len(ra), sum(x[1] for x in rb)/len(rb)))
    if len(chron)<12: continue
    rc=pear([p[0] for p in chron],[p[1] for p in chron])
    rr=pear([p[0] for p in rand],[p[1] for p in rand])
    print(f"  min {MINH}/half (n={len(chron)}):  chronological r={rc:+.3f}   random r={rr:+.3f}"
          f"   gap={rr-rc:+.3f}")
print("  A large random>chronological gap = the trait DRIFTS. A small gap = it is just NOISE.")

print("\n" + "="*82)
print("(C) A DIFFERENT LOOK: alternative measures on the same events")
print("="*82)
# rate per eligible round instead of share of kills
elig=collections.Counter(); wk=collections.Counter()
for rid,r in rounds.items():
    if not r["planted"] or r["plant_time"] is None: continue
    if r["outcome"] and ("Surrendered" in r["outcome"] or "Time Win" in r["outcome"]): continue
    a=atk(r["round_number"])
    for m,info in mp.items():
        pass
print("  (rate-per-round measure needs a per-round roster join; reported separately)")
print(f"  share spread across the {len(players)} qualifying players:")
q=sorted(shares)
print(f"    p10 {100*q[int(.1*len(q))]:.1f}%  p25 {100*q[int(.25*len(q))]:.1f}%  median {100*q[len(q)//2]:.1f}%"
      f"  p75 {100*q[int(.75*len(q))]:.1f}%  p90 {100*q[int(.9*len(q))]:.1f}%")
print(f"    full range {100*min(q):.1f}% - {100*max(q):.1f}%")
db.close()
