# Independent verification of the pre-plant `dt≈2-5s` attacker dip

**Audience:** an agent picking up Part 3 of
`docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md`
with no prior context on this thread.

**Date:** 2026-09-08. **Branch:** `impact-scoring-impl`. **DB:** local
Postgres :5433, 3,124 matches (matches the authoritative Render snapshot).

**Read first:** `docs/superpowers/2026-09-07-preplant-dip-investigation.md`
(the original investigation). This document verifies it. It does not
replace it.

---

## 0. TL;DR

| Claim | Verdict |
|---|---|
| The 74.3% (`dt≈4`) vs 81.9% (`dt≈16`) attacker numbers | **Confirmed exactly**, via an independent code path |
| The dip is robust (splits, regularization, edge cases, per-state) | **Confirmed**, and more strongly than originally reported |
| The dip means "a kill 2-5s before the plant is worth less" | **Not confirmed.** ~75-88% of it is composition the state adjustment cannot see |
| "This supports encouraging faster spike plants" | **Rejected.** Read causally, the data points the *opposite* way |
| The shape×lift runtime mismatch "has already been fixed" | **False.** Still live. See §7 Bug B |
| The regression's reported attacker log-odds curve | **Mislabelled by +1.7686 logits.** See §7 Bug A |

**One-sentence summary of the mechanism:** `dt` is the gap between a kill
and the plant, so it mechanically controls *how much more round happens
before the plant*; conditional on a plant having occurred, a longer gap
means more subsequent kills, and those kills skew attacker-favourable. The
pre-kill state adjustment cannot see any of that, because all of it happens
after the kill.

**Do not act on the dip by relaxing the spec's monotonicity requirement or
by adopting the empirical curve directly.** See §6 and §8.

---

## 1. What was being verified

Fitting the spec's 2-knot logistic model against the real DB produced a
`shape()` curve that dipped and reversed sign, violating the spec's
monotonicity requirement. The original investigation (commits `350b0c9`,
`1a45848`, `04fbe43`) concluded:

1. A large fitting/runtime reconstruction mismatch explained the
   originally-reported dip at `dt≈20`.
2. A smaller, real, attacker-only dip remained at `dt ≈ 2-5s`, found by two
   independent methods, surviving a match-based 50/50 split.

Headline: trough at `dt=4` is 74.3% attacker-team round-win rate
[72.0%, 76.6%] (n=2,389); plateau at `dt=16` is 81.9% [80.3%, 83.2%]
(n=3,104), non-overlapping match-clustered bootstrap CIs.

`dt` throughout = `seconds_to_plant` = `plant_time - kill_time`, **positive
before the plant**, so *small* `dt` means *close to* the plant.

---

## 2. How to reproduce, self-contained

The original scripts are
`docs/superpowers/diagnostics/investigate_preplant_dip_1s_buckets.py` and
`..._match_split_check.py`, which go through
`app.scoring.preplant_time_model.extract_preplant_observations` (a Python
per-round replay loop).

The verification deliberately used a **different code path**: one raw-SQL
query with window functions, then `numpy.bincount` over dense
`(side, bucket, state)` codes instead of dict-of-lists. This query is the
reproducibility anchor — it is byte-equivalent to the module's extraction
(see §3).

```sql
WITH r AS (
    SELECT id, match_id, round_number, outcome, plant_time
    FROM rounds
    WHERE planted IS TRUE AND plant_time IS NOT NULL AND outcome IS NOT NULL
      AND outcome NOT LIKE '%Surrendered%'
      AND outcome NOT LIKE '%Time Win%'        -- phantom plant
),
k AS (
    SELECT ke.id AS kid, ke.round_id, ke.event_time_seconds,
           mpk.team::text AS killer_team, mpd.team::text AS victim_team
    FROM kill_events ke
    JOIN match_players mpk ON mpk.id = ke.killer_match_player_id
    JOIN match_players mpd ON mpd.id = ke.death_match_player_id
),
j AS (
    SELECT k.*, r.match_id, r.round_number, r.outcome, r.plant_time,
           (k.killer_team = k.victim_team) AS self_kill
    FROM k JOIN r ON r.id = k.round_id
),
plant_state AS (               -- alive counts AT THE PLANT INSTANT
    SELECT round_id,
      SUM(CASE WHEN NOT self_kill AND victim_team='TEAM_1'
                AND event_time_seconds < plant_time THEN 1 ELSE 0 END) AS pd1,
      SUM(CASE WHEN NOT self_kill AND victim_team='TEAM_2'
                AND event_time_seconds < plant_time THEN 1 ELSE 0 END) AS pd2,
      SUM(CASE WHEN NOT self_kill
                AND event_time_seconds < plant_time THEN 1 ELSE 0 END) AS n_preplant_kills
    FROM j GROUP BY round_id
),
c AS (                          -- alive counts JUST BEFORE each kill
    SELECT j.*,
      COALESCE(SUM(CASE WHEN NOT self_kill AND victim_team='TEAM_1' THEN 1 ELSE 0 END)
        OVER (PARTITION BY round_id ORDER BY event_time_seconds, kid
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS d1,
      COALESCE(SUM(CASE WHEN NOT self_kill AND victim_team='TEAM_2' THEN 1 ELSE 0 END)
        OVER (PARTITION BY round_id ORDER BY event_time_seconds, kid
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS d2,
      COALESCE(SUM(CASE WHEN NOT self_kill
                AND event_time_seconds < plant_time THEN 1 ELSE 0 END)
        OVER (PARTITION BY round_id ORDER BY event_time_seconds, kid
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS kills_before
    FROM j
)
SELECT c.match_id, c.round_id, c.round_number, c.plant_time, c.event_time_seconds,
       c.plant_time - c.event_time_seconds AS dt,
       GREATEST(5 - (CASE WHEN c.killer_team='TEAM_1' THEN c.d1 ELSE c.d2 END),0) AS killer_alive,
       GREATEST(5 - (CASE WHEN c.victim_team='TEAM_1' THEN c.d1 ELSE c.d2 END),0) AS victim_alive,
       (c.killer_team = (CASE WHEN c.round_number<=12 THEN 'TEAM_1'
                              WHEN c.round_number<=24 THEN 'TEAM_2'
                              WHEN (c.round_number-25)%2=0 THEN 'TEAM_1'
                              ELSE 'TEAM_2' END)) AS is_attacker,
       (c.killer_team = (CASE WHEN c.outcome LIKE 'Team A%' THEN 'TEAM_1'
                              WHEN c.outcome LIKE 'Team B%' THEN 'TEAM_2' END)) AS won,
       GREATEST(5 - (CASE WHEN c.killer_team='TEAM_1' THEN ps.pd1 ELSE ps.pd2 END),0) AS killer_alive_at_plant,
       GREATEST(5 - (CASE WHEN c.victim_team='TEAM_1' THEN ps.pd1 ELSE ps.pd2 END),0) AS victim_alive_at_plant,
       ps.n_preplant_kills, c.kills_before
FROM c JOIN plant_state ps ON ps.round_id = c.round_id
WHERE NOT c.self_kill AND c.plant_time - c.event_time_seconds > 0
```

Gotcha: `match_players.team` is a Postgres **enum**, so `team::text` is
required before comparing against a `CASE` returning text — otherwise
`operator does not exist: team = text`.

**The direct-standardization estimator** (Mantel-Haenszel), stated so it can
be reimplemented without reading code:

- Reference weights `w_s` = the share of state `s` among **all** usable
  observations *for that side* (attacker and defender have separate
  reference distributions).
- For a bucket: within each state `s`, compute the raw win rate `r_s` on
  that bucket's cell. Drop cells with `n < MIN_CELL` (=20).
- Standardized rate = `Σ w_s·r_s / Σ w_s`, summed over included states only
  (renormalization). `Σ w_s` over included states is the reported
  **coverage**.

**Bucketing:** `b_k` covers `dt ∈ (k-1, k]` for k = 1..30; `REF` is
`dt > 30`. `b0_exact` (dt == 0) exists in the original script and is empty.

---

## 3. The population, and every exclusion, quantified

```
rounds total                    65,929
  planted                       42,480
  planted & Surrendered              0   <-- exclusion is INERT on this snapshot
  planted & Time Win (phantom)      76
  planted, kept                 42,404
  outcome NULL                       0   <-- so winner is ALWAYS determinable

kill_events total              487,844
  self-kills (same team)           3,234   (0.66%)
  null killer or victim                0
```

Resulting observation set:

```
usable pre-plant observations   168,432
  undeterminable winner               0
  attacker rows                 102,570
  defender rows                  65,862
  distinct matches                3,123   (of 3,124 in DB)
  distinct rounds                40,642
  dt range                 0.0120 .. 97.53
  dt <= 30                      130,506
  dt >  30 (REF)                 37,926
```

**Extraction equivalence check.** Comparing the SQL output against
`extract_preplant_observations(db)` as multisets of
`(round_id, dt, killer_alive, victim_alive, is_attacker, won)`:

```
mine: 168432   theirs: 168432
rows only in mine:   0
rows only in theirs: 0
```

So every exclusion (surrendered, phantom plant, unplanted, self-kill,
null player ids, `dt > 0`) is applied identically. §4's numbers are not
downstream of a shared extraction bug.

**Boundary checks — measured, not asserted:**

- `dt == 0.0`: **0 rows**. Minimum `dt` is 0.0120. `b0_exact` is genuinely
  unreachable given the `dt > 0` filter.
- `dt == 30.0` exactly: **0 rows**. The `b30`/`REF` boundary never fires.
  4 rows sit in `(30, 30.001]` and are assigned `REF` — negligible.
- `bucket_label` uses `int(np.ceil(dt))`, so `dt ∈ (0,1] → b1`. Correct.
- Latent trap only: a negative `dt` would mint a `b0`/`b-1` key absent from
  `bucket_order`. Unreachable today; worth a guard if the filter ever moves.

**Known data-quality wrinkle:** self-kills are skipped as observations *and*
do not decrement the alive count
(`app/scoring/preplant_time_model.py:119`). After a teamkill, alive counts
run one too high for that team for the rest of the round. 3,234 self-kills
touch 5.2% of observations. Dropping every affected round moves the headline
contrast from −0.0814 to −0.0873, i.e. it does not matter here.

---

## 4. The finding, independently reproduced

Direct standardization + match-clustered bootstrap (500 draws, seed
20260908 — a different seed and a different implementation from the
original's seed 0).

```
 bucket   n_atk   adj_atk [95% CI]            adj_def [95% CI]
     b1    2587   0.7664 [0.7467, 0.7874]   0.5453 [0.5195, 0.5732]
     b2    2466   0.7464 [0.7240, 0.7679]   0.5429 [0.5171, 0.5676]
     b3    2555   0.7487 [0.7270, 0.7708]   0.5314 [0.5075, 0.5588]
     b4    2389   0.7426 [0.7241, 0.7678]   0.5184 [0.4910, 0.5472]
     b5    3417   0.7493 [0.7296, 0.7667]   0.5171 [0.4932, 0.5450]
     b6    4227   0.7543 [0.7357, 0.7680]   0.4803 [0.4542, 0.5070]
     b7    4771   0.7784 [0.7651, 0.7949]   0.4556 [0.4347, 0.4894]
     b8    4431   0.7709 [0.7570, 0.7846]   0.4514 [0.4304, 0.4805]
     b9    4316   0.7889 [0.7740, 0.8021]   0.4307 [0.4075, 0.4515]
    b10    4233   0.7893 [0.7755, 0.8038]   0.3900 [0.3711, 0.4150]
    b11    4180   0.7955 [0.7827, 0.8080]   0.3803 [0.3573, 0.4072]
    b12    3828   0.8002 [0.7880, 0.8128]   0.3829 [0.3615, 0.4063]
    b13    3662   0.8075 [0.7920, 0.8188]   0.3607 [0.3329, 0.3790]
    b14    3359   0.8097 [0.7935, 0.8221]   0.3938 [0.3680, 0.4151]
    b15    3248   0.8104 [0.7949, 0.8226]   0.3693 [0.3463, 0.3939]
    b16    3104   0.8194 [0.8029, 0.8316]   0.3655 [0.3448, 0.3908]
    b17    2817   0.8055 [0.7911, 0.8214]   0.3554 [0.3321, 0.3843]
    b18    2765   0.8152 [0.8019, 0.8301]   0.3535 [0.3274, 0.3777]
    b19    2430   0.8213 [0.8041, 0.8364]   0.3397 [0.3215, 0.3762]
    b20    2378   0.8111 [0.7948, 0.8268]   0.3593 [0.3346, 0.3919]
    b21    2164   0.8176 [0.7992, 0.8322]   0.3152 [0.2942, 0.3468]
    b22    2075   0.8243 [0.8077, 0.8426]   0.3500 [0.3204, 0.3785]
    b23    1956   0.8148 [0.7965, 0.8319]   0.3425 [0.3094, 0.3747]
    b24    1842   0.8232 [0.8034, 0.8436]   0.3264 [0.2964, 0.3644]
    b25    1655   0.7989 [0.7760, 0.8192]   0.3349 [0.3062, 0.3655]
    b26    1555   0.8218 [0.7991, 0.8406]   0.3583 [0.3318, 0.4014]
    b27    1518   0.7989 [0.7798, 0.8213]   0.3354 [0.3084, 0.3696]
    b28    1360   0.8182 [0.7940, 0.8358]   0.3273 [0.2970, 0.3607]
    b29    1241   0.8003 [0.7784, 0.8231]   0.3473 [0.3147, 0.3827]
    b30    1208   0.7901 [0.7624, 0.8129]   0.3153 [0.2829, 0.3582]
    REF   18833   0.7496 [0.7377, 0.7610]   0.3787 [0.3656, 0.3897]
```

Against the original `preplant_dip_investigation_buckets.csv`: `b4`
adjusted 0.7426 (theirs 0.7426), `b16` 0.8194 (theirs 0.8194), coverage
0.967 / 0.954 (identical). **Point estimates agree to 4 decimal places.**
CIs differ only in the third decimal, as expected from a different bootstrap
seed.

**A stronger statistic the original did not compute.** The original compared
two *marginal* CIs and noted non-overlap — a conservative test. The right
statistic is a **paired** bootstrap of the difference:

```
attacker b4 - b16                    = -0.0767  95% CI [-0.0979, -0.0474]  P(diff>=0) = 0/500
attacker mean(b2-b5) - mean(b15-b25) = -0.0680  95% CI [-0.0806, -0.0559]  P(>=0)     = 0/500
defender b4 - b16                    = +0.1529  95% CI [+0.1166, +0.1885]
```

The original conclusion is, if anything, understated.

**Important framing note that the original writeup buries.** Calling this "a
dip at 2-5s" understates it. The attacker adjusted curve rises
near-monotonically from `b2` (0.746) all the way to `b16-b22` (~0.82) and
then flattens; `b1` bumps back up to 0.766. Relative to `REF` (0.7496),
`b1-b5` are *at* the reference while `b10-b25` are ~6 points above it. So
the fitted "shape" implied by this data peaks around `dt ≈ 13-24` and
returns to zero near the plant — i.e. it is an **inverted U in `dt`**, and
the spec's *imposed flat plateau at `dt ≤ 10`* (asserting shape == its
maximum there) is exactly backwards for attackers over that whole region.

---

## 5. Robustness — the association is real

### 5a. Split stability (16 independent subsamples)

Standardized `dt(1,5]` vs `dt(14,25]` attacker contrast, and the joint
regression's logit contrast, across 6 seeds and 4 split ratios:

```
  seed  ratio  half   n_matches   std_diff   reg_diff
       1 0.50    A      1,561    -0.0763   -0.3946
       1 0.50    B      1,562    -0.0865   -0.4593
       2 0.50    A      1,561    -0.0770   -0.4119
       2 0.50    B      1,562    -0.0847   -0.4409
       3 0.50    A      1,561    -0.0820   -0.4357
       3 0.50    B      1,562    -0.0790   -0.4164
       7 0.50    A      1,561    -0.0808   -0.4446
       7 0.50    B      1,562    -0.0790   -0.4101
      42 0.50    A      1,561    -0.0820   -0.4123
      42 0.50    B      1,562    -0.0813   -0.4415
20260908 0.50    A      1,561    -0.0817   -0.4217
20260908 0.50    B      1,562    -0.0813   -0.4328
      11 0.25  A(0.25)     780    -0.0785   -0.3720
      11 0.25  B(0.75)   2,343    -0.0812   -0.4450
      12 0.70  A(0.70)   2,186    -0.0804   -0.4429
      12 0.70  B(0.30)     937    -0.0800   -0.3949
      13 0.33  A(0.33)   1,030    -0.0922   -0.4728
      13 0.33  B(0.67)   2,093    -0.0769   -0.4063
```

Range: **−0.076 to −0.092.** The original's seed=1 result was not lucky.

### 5b. `MIN_CELL` is not distorting anything

Attacker adjusted rate at several `MIN_CELL` thresholds:

```
  bucket   mc=1     mc=5     mc=10    mc=20    mc=30    mc=50    mc=100
      b1   0.7540  0.7554  0.7611  0.7664  0.7664  0.7723  0.7946
      b2   0.7395  0.7387  0.7464  0.7464  0.7464  0.7531  0.7793
      b3   0.7400  0.7456  0.7487  0.7487  0.7487  0.7713  0.7756
      b4   0.7390  0.7379  0.7426  0.7426  0.7511  0.7737  0.7638
      b5   0.7395  0.7397  0.7450  0.7493  0.7522  0.7612  0.7877
      b7   0.7769  0.7765  0.7785  0.7784  0.7846  0.7857  0.8093
     b10   0.7865  0.7863  0.7855  0.7893  0.7919  0.7919  0.8147
     b16   0.8125  0.8119  0.8119  0.8194  0.8186  0.8150  0.8353
     b20   0.8153  0.8100  0.8113  0.8111  0.8142  0.8168  0.8263
     b30   0.7984  0.7996  0.7948  0.7901  0.7876  0.7854  0.8214
     REF   0.7534  0.7520  0.7482  0.7496  0.7498  0.7451  0.7421
```

The `b4 − b16` contrast moves only between **−0.0715 and −0.0768** across
`mc ∈ {1..100}`. Attacker coverage stays ≥ 0.83 at every bucket 1..30.

### 5c. `REF` is not driving it

The direct-standardization contrast never touches `REF`. In the joint
regression `REF` is the omitted category; dropping `REF` rows entirely:

```
  regression attacker contrast, with REF rows    = -0.4266
  regression attacker contrast, REF rows dropped = -0.4787
  per-bucket shifts are near-constant (b1..b6 ≈ -0.32, b15..b20 ≈ -0.28)
```

Same sign, same magnitude. The writeup's flag that `REF` is heterogeneous
is correct but irrelevant to the trough-vs-plateau contrast.

### 5d. Edge-case contamination

```
  all rounds                         dip-plateau = -0.0814  cov 0.97
  self-kill rounds dropped           dip-plateau = -0.0873  cov 0.97
  regulation rounds only (rn<=24)    dip-plateau = -0.0819  cov 0.97
```

### 5e. It holds *within* individual states — not a standardization artifact

Attacker, `dt(1,5]` vs `dt(14,25]`, per exact pre-kill state. `refwt` is the
state's share of the attacker reference population. Also shown: alive counts
at the plant, and how many further pre-plant kills follow this one.

```
  state  refwt | n_dip  win_dip  plant@dip aft | n_plt  win_plt  plant@plt aft |  delta
   5v5   0.236 |   868   0.753   4.70v3.70 0.60|  8347   0.828   3.97v2.34 2.69| -0.074
   5v4   0.111 |  1271   0.895   4.73v2.76 0.50|  3150   0.918   3.95v1.57 2.49| -0.022
   4v4   0.105 |  1105   0.758   3.76v2.76 0.48|  2882   0.847   3.08v1.59 2.34| -0.089
   4v5   0.102 |   519   0.549   3.65v3.69 0.66|  2714   0.696   3.05v2.29 2.67| -0.147
   4v3   0.062 |  1062   0.918   3.81v1.81 0.38|  1344   0.937   3.18v1.04 1.79| -0.019
   3v4   0.060 |   494   0.555   2.78v2.79 0.43|  1579   0.702   2.30v1.63 2.07| -0.147
   3v3   0.053 |   651   0.754   2.83v1.85 0.32|  1331   0.836   2.33v1.12 1.56| -0.082
   5v3   0.043 |   929   0.980   4.78v1.78 0.44|   829   0.971   4.01v1.02 1.99| +0.008
   3v5   0.033 |   211   0.232   2.67v3.73 0.60|   776   0.541   2.30v2.34 2.37| -0.309
   2v3   0.029 |   244   0.541   1.85v1.90 0.25|   775   0.699   1.66v1.15 1.19| -0.158
   3v2   0.027 |   533   0.957   2.86v0.90 0.24|   414   0.935   2.47v0.66 0.90| +0.022
   4v2   0.026 |   718   0.994   3.86v0.87 0.26|   310   0.968   3.38v0.63 1.03| +0.027
   2v2   0.022 |   263   0.829   1.91v0.94 0.16|   534   0.843   1.67v0.70 0.65| -0.014
   2v4   0.020 |   128   0.328   1.83v2.80 0.37|   547   0.537   1.61v1.74 1.65| -0.209
   5v2   0.013 |   449   0.998   4.86v0.85 0.29|   110   0.991   4.33v0.52 1.20| +0.007
   1v2   0.009 |    44   0.591   0.95v0.91 0.14|   256   0.617   0.98v0.75 0.27| -0.026
   1v3   0.007 |    22   0.227   0.95v1.91 0.14|   223   0.457   0.95v1.29 0.77| -0.230
   2v1   0.006 |   235   0.996   2.00v0.00 0.02|    24   0.875   1.71v0.00 0.50| +0.121
   2v5   0.006 |    33   0.121   1.73v3.88 0.39|   130   0.369   1.61v2.50 1.89| -0.248
  standardized difference = -0.0814   covered weight 0.974
```

**14 of the 19 states with usable cells are negative.** The 5 positives
(`5v3`, `3v2`, `4v2`, `5v2`, `2v1`) are all overwhelming attacker-advantage
states already winning 93-100% — ceiling effects, not counter-evidence.

**So: the association is real, reproducible, within-state, and not a
standardization artifact. Everything from here is about what it means.**

---

## 6. Why the causal reading fails

### 6a. The fact that reframes everything

**The raw attacker win rate at `dt≈4` is HIGHER than at `dt≈16`:**
0.8234 vs 0.8160. With no adjustment at all, the "dip" group *outperforms*
by **+0.0215**. The entire −8pp effect is manufactured by the state
adjustment. That is not illegitimate — adjusting is the point — but it means
the whole result rests on the adjustment capturing the right thing.

### 6b. What `dt` actually is

`dt = plant_time − kill_time`. It is **not** a property of the kill. It is
the gap between the kill and a *future* event. Two consequences:

1. `dt` mechanically determines **how much more round happens before the
   plant**. The pre-kill state adjustment cannot see any of it, because all
   of it happens after the kill.
2. Because the population is conditioned on *the plant having happened*,
   a longer gap systematically means the attack ground the defence down
   before planting.

Composition by bucket (attacker rows), showing exactly this:

```
 bkt      n   win   plant_time  kill_time  state@kill  state@plant  kills_after
  b1   2587  0.840     35.0        34.5    3.86v3.06   3.83v2.04       0.06
  b2   2466  0.820     35.1        33.5    3.90v3.16   3.79v2.07       0.21
  b3   2555  0.829     35.7        33.2    3.94v3.16   3.76v2.02       0.32
  b4   2389  0.823     34.7        31.3    4.03v3.30   3.80v2.09       0.45
  b5   3417  0.838     38.9        34.3    3.74v3.05   3.51v1.82       0.47
  b8   4431  0.807     36.5        29.0    3.91v3.60   3.56v2.06       0.90
 b10   4233  0.813     37.0        27.5    3.98v3.76   3.54v2.00       1.20
 b13   3662  0.810     38.5        26.0    4.03v3.95   3.43v1.95       1.60
 b16   3104  0.816     39.6        24.1    4.10v4.07   3.36v1.85       1.97
 b20   2378  0.802     42.7        23.2    4.07v4.19   3.20v1.80       2.27
 b25   1655  0.784     47.6        23.1    4.05v4.26   2.97v1.65       2.71
 b30   1208  0.773     52.7        23.2    3.94v4.25   2.75v1.57       2.87
 REF  18833  0.711     65.4        20.8    3.95v4.44   2.33v1.50       3.56
```

Note two mechanical correlations `dt` carries with it: `kills_after` rises
monotonically 0.06 → 3.56, and `plant_time` rises 35s → 65s (a kill 20s
before a plant *requires* `plant_time ≥ 20`).

### 6c. The unified mechanism, in the attacker frame

Re-expressing every observation as attacker-minus-defender, regardless of
which side made the kill. `gain` = man-advantage the attackers acquire
between the kill and the plant.

```
  bkt       n   adv@kill  adv@plant   gain   atk_team_win
   b1    4816    +0.78     +0.85     +0.08     0.688
   b4    4271    +0.78     +0.94     +0.16     0.703
   b7    6553    +0.58     +1.27     +0.69     0.775
  b10    6238    +0.38     +1.18     +0.80     0.759
  b16    4835    +0.21     +1.12     +0.91     0.755
  b20    3768    +0.06     +1.03     +0.97     0.740
  b25    2745    +0.00     +0.96     +0.95     0.733
  b30    2024    -0.08     +0.88     +0.96     0.731
  REF   37926    -0.22     +0.50     +0.72     0.653
```

This is why attacker and defender curves point *opposite* ways in the §4
table but say the *same* thing: more time between a kill and the plant means
more subsequent kills, and — because the plant happened — those went the
attackers' way. One fact, seen from two sides.

### 6d. The smoking gun: one cell, pre-kill state held exactly constant

5v5 pre-kill (i.e. first blood of the round), split by who got it:

```
  attacker got first blood
    bkt      n   adv@plant   atk_team_win
      b1    149    +0.97        0.785
      b4    244    +0.96        0.742
     b10    766    +1.34        0.809
     b16    880    +1.60        0.832
     b25    569    +1.69        0.837
     REF   6355    +1.27        0.779

  defender got first blood
    bkt      n   adv@plant   atk_team_win
      b1    113    -0.98        0.363
      b4    189    -0.85        0.418
     b10    335    -0.18        0.522
     b16    416    +0.01        0.560
     b25    283    +0.31        0.622
     REF   7447    +0.25        0.609
```

Pre-kill state is *identical by construction* across every row. The b4 group
planted having converted exactly the one kill — into four living defenders.
The b16 group spent those 16 seconds killing ~0.6 more defenders net, then
planted. **The win-rate gap tracks the man-count at the plant, not the
timing of the kill.**

### 6e. Closing the channel — two independent ways

```
stratification                                    diff      95% CI              cov
[A] none (raw)                                  +0.0215                        1.00
[B] pre-kill exact state   <-- ORIGINAL METHOD  -0.0814  [-0.0955, -0.0704]    0.97
[C] plant-instant alive counts ALONE            -0.0098  [-0.0207, -0.0006]    0.99
[D] pre-kill state x plant-instant state        -0.0209  [-0.0333, -0.0084]    0.53
[E] plant_time decade                           +0.0333                        1.00
[F] pre-kill state x plant_time decade          -0.0812                        0.75
[G] pre-kill x plant state x plant_time decade  -0.0229                        0.25

design-based, no over-control:
    last pre-plant kill only, pre-kill state    -0.0206  [-0.0346, -0.0048]    0.88
    NOT the last pre-plant kill (comparison)    -0.1112                        0.98
    last-kill + plant_time decade               -0.0258  [-0.0476, -0.0072]    0.53
```

Two facts to take from this table:

- **[F] shows `plant_time` is not the explanation.** Controlling for it
  leaves −0.0812, essentially unchanged from −0.0814.
- **[C] and the last-kill restriction, which share no logic, both collapse
  the effect to ~2pp.** Restricting to kills after which nothing further
  happens before the plant is a *subpopulation*, not a control — it closes
  the channel by construction, with no over-control. It lands at −0.0206,
  matching [C]'s −0.0098 to within the noise, while non-last kills sit at
  −0.1112.

**~75-88% of the reported effect is the man-count at the plant.**

### 6f. Why "plant faster" is the wrong reading, specifically

The brief's gloss was "a dragged-out, contested entry costing the attack
something a clean early kill doesn't." That has the direction backwards:

- **Large `dt`** = fight ends, 16-25 seconds pass, calm plant. *That* is the
  dragged-out round.
- **Small `dt`** = kill, then plant 4 seconds later. That is the **fast,
  forced** plant — into a site that hasn't been cleared.

Read causally, the association therefore argues *against* planting faster.

And the counterfactual isn't in the data at all: every observation is
conditioned on a plant having occurred, so "planted 4s after a kill" vs
"planted 16s after" compares a team that planted into a live site against a
team that planted *because* it had cleared. Nothing here identifies what
happens if a team chooses to plant sooner.

**Plain-English alternative explanation:** *a plant that lands right on the
heels of a kill is a plant into a site that hasn't been cleared. `dt` is a
proxy for how cleared the site was at plant time — a property of the plant,
not of the kill.*

### 6g. Why this matters for Impact specifically

The reason a `dt=16` kill "predicts" a better round is that **other kills
followed it** — kills that get their own `ImpactScore` rows. Crediting the
`dt=16` kill for them double-counts across the per-kill decomposition. This
is the leakage-horizon problem: the shared round-outcome label lets a
per-kill component peek at events past its own information horizon.

---

## 7. Bugs found

### Bug A — real, changes reported numbers (low severity, diagnostics only)

`docs/superpowers/diagnostics/investigate_preplant_dip_1s_buckets.py:280`
and `investigate_preplant_dip_match_split_check.py:63`:

```python
bl: def_bucket_coef[bl] + atk_main + float(beta[idx_bucket_atk + i])
```

Algebra: attacker in `b_k` = `intercept + state + def_k + atk_main + int_k`;
attacker in `REF` = `intercept + state + atk_main`. Their difference is
`def_k + int_k`. Adding `atk_main` makes the quantity **attacker-in-`b_k`
minus DEFENDER-in-REF**.

Every attacker value is inflated by exactly `atk_main = +1.7686`:

```
  bkt   as reported   correct (atk vs attacker-REF)   defender
  b1      +1.6797            -0.0889                  +0.8976
  b2      +1.5530            -0.2156                  +0.8266
  b3      +1.5887            -0.1800                  +0.8284
  b4      +1.5746            -0.1940                  +0.7856
  b5      +1.6905            -0.0781                  +0.7943
 b10      +1.8593            +0.0907                  +0.2329
 b16      +2.0242            +0.2555                  +0.1049
 b20      +2.0323            +0.2637                  +0.0472
 b30      +1.9830            +0.2143                  -0.2098
```

Consequences:
- The comment at `:283-286` ("both curves are therefore already on the
  'relative to dt>30' scale") is **false for the attacker curve**.
- The CSV column `regression_logit_atk_vs_ref` (`:290`) is mislabelled.
- The original writeup's "trough ~1.55-1.59 at dt=2-4 against a dt=15-28
  plateau of ~1.9-2.1" should read **−0.19 to −0.22 against +0.21 to
  +0.26**. The sign flips: near-plant attacker kills sit *below* the far
  reference, not 1.5 logits above it.
- **The dip itself is unaffected** — `atk_main` cancels in the contrast
  (`b4 − b16 = −0.4496` either way). Finding 2 and both split verdicts
  stand.

### Bug B — the shape×lift mismatch is NOT fixed (higher severity, live code)

`350b0c9` fixed two *fitting* bugs (standardize-before-ridge, un-interact
`w1`). Un-interacting `w1` is precisely what **creates** the structural
mismatch. It is still live.

Fit against the full DB, **as measured on 2026-09-08**:

```
theta1 (w1 column)  = +0.31218
theta2 (w2 column)  = -0.27809
shape_mid_ratio     = -1.12258
intercept_atk=+1.22417  slope_atk=+0.67290
intercept_def=-0.27809  slope_def=+0.59371
```

**Recomputed 2026-09-09 under the corrected replay** (`extract_preplant_
observations` diverged from `impact.py` on self-kills and resurrections; see
`M1` in the measurement record). The block above is kept as the record of what
this verification actually measured; the corrected values are:

```
theta1 (w1 column)  = +0.31376
theta2 (w2 column)  = -0.26023
shape_mid_ratio     = -1.20568
```

Nothing in this section's argument turns on the third decimal: `theta2` is
still negative, the ratio is still negative, and the mismatch this
verification identified is unchanged. Both the mismatch and the negative
reference cell were subsequently fixed by profiling `shape_mid_ratio`
directly, which is what produced the `+1.63` non-monotonic fit that sent
Part 3 to an empirical curve.

Reconstructing `eta` from the design matrix vs. what the runtime computes,
row by row on all 168,432 observations:

```
max |delta| = 3.1949 logits   mean 0.2686   37.6% of rows have |delta| > 0.01
  dt < 10  (w1=0):  max 0.0000       10 <= dt < 20:  max 3.1949
  20 <= dt < 30:    max 3.1937       dt >= 30:       max 0.0000
```

The model's true linear predictor at `dt=20` is the flat constant
`theta1 = +0.3122` for every side and advantage. `shape(20)*logit_lift`:

```
  adv=-3 atk=True   eta_true=+0.3122   shape*lift=+0.8919   delta=+0.5798
  adv=-3 atk=False  eta_true=+0.3122   shape*lift=+2.3117   delta=+1.9995
  adv=+0 atk=True   eta_true=+0.3122   shape*lift=-1.3742   delta=-1.6864
  adv=+0 atk=False  eta_true=+0.3122   shape*lift=+0.3122   delta=+0.0000  <-- only exact cell
  adv=+2 atk=True   eta_true=+0.3122   shape*lift=-2.8850   delta=-3.1972
  adv=+2 atk=False  eta_true=+0.3122   shape*lift=-1.0208   delta=-1.3330
```

Agreement holds **only** where `logit_lift(adv, side) == theta2 = −0.2781`,
i.e. `adv=0`, defender. Live consumers:
`app/scoring/preplant_k_selection.py:21`
(`1.0 + k * fit.logit_lift(...) * fit.shape(...)`) and
`app/scoring/preplant_scalar.preplant_proximity_scalar`.

Answering the specific question this verification was asked: the design
matrix (`state FE + w1, w2, w2·adv, w2·atk, w2·adv·atk`) does **not** match
what `PreplantFit.shape()` / `.logit_lift()` consume. This is the *same*
mismatch, not a second undiscovered one.

**Related and worth flagging:** `theta2 = intercept_def` is **negative**, so
`shape_mid_ratio = theta1/theta2` is negative too. The "normalize shape to 1
at the plateau" convention is pinned on a negative reference cell, and that
is the mechanical origin of the sign reversal in `shape()`. Ridge sensitivity
is not the issue (`shape_mid_ratio` moves only −1.205 to −1.227 across
`l2 ∈ {0.1, 1, 10, 100}` under the corrected replay; −1.122 to −1.142 as
originally measured -- the insensitivity is the point either way).

### Verified clean

- **`standardized_rate` (`:203-218`)** — Mantel-Haenszel math and
  renormalization are correct. An independent reimplementation reproduces
  every published `adjusted_rate` and `coverage` to 4 dp.
- **The bootstrap (`:310-355`)** — genuinely match-clustered. `picks` indexes
  `match_ids` and whole `by_match[...]` blocks are concatenated; individual
  kills are never resampled. Recomputing `draw_ref` inside each draw is the
  correct plug-in bootstrap of the full statistic — no leakage, no
  double-counting. An independent bootstrap with a different seed and
  implementation lands within ~0.003 on every CI.
  - Minor: cell inclusion (`< MIN_CELL`) is re-evaluated per draw, so cells
    near the threshold flip in and out and the bootstrapped statistic is not
    exactly the same functional across draws. This inflates intervals
    slightly — conservative, so harmless.
- **Design-matrix indices (`:272-282`)** — correct, no off-by-one.
  `fit_logistic` returns `beta[0]` = intercept with `beta[1:]` in column
  order, and `back_transform` preserves that layout, so `idx_bucket_def =
  1 + n_state` is right. Verified on synthetic data (248,000 rows) with a
  planted −0.9 dip at `b4` and +0.7 spike at `b16`: both recovered in the
  correct buckets, max coefficient error 0.15 logits (all ridge shrinkage),
  and a deliberately shifted index visibly disagrees.
- **`bucket_label` boundaries** — see §3. Empirically verified, not asserted.

### Cosmetic

- `investigate_preplant_dip_1s_buckets.py:87-88` — dead loop
  `for o in usable: pass`.
- Compared buckets are standardized over *different* included-cell sets
  (`b4` coverage 0.967, `b16` 0.954), so differencing two published rows is
  not quite like-for-like. A common-cell filter gives −0.0846 instead of
  −0.0768. Does not change any conclusion.

---

## 8. What this means for Part 3, and what is still open

**Recommendation:** keep the spec's monotonicity requirement. Treat the
`dt` gradient as a predeclared, loudly reported caveat (the original
investigation's option (a)) rather than a reason to adopt the empirical
curve directly. Fix Bug A and Bug B before anything else reads these
curves.

**Do not** use the empirical per-bucket numbers as a scoring weight. They
carry the subsequent-kill signal described in §6, which is already credited
elsewhere in the per-kill decomposition.

**The estimand question, stated cleanly** — three different questions are
tangled here, and the original investigation answers the first while the
brief tried to use it for the second:

1. **Prediction** — "given pre-kill state and `dt`, what is P(win)?" The
   original method answers this *correctly*; −8.1pp is right. But it is a
   forecast, and a forecast cannot support a "plant faster" recommendation.
2. **Credit for the kill** (what Impact needs) — the effect of the kill
   holding fixed everything knowable *at kill time*. `dt` is not knowable at
   kill time (the plant hasn't happened yet), so it cannot modify the kill's
   value without importing later events.
3. **Accounting** — "how much of the `dt` signal is just the man-count
   moving between the kill and the plant?" This is what the plant-instant
   state is *for*. Over-control is harmless here because nothing is being
   estimated; the question is only whether a channel exists. Answer: 75-88%.

**Why plant-instant alive counts are a diagnostic and not a better
adjustment set** (this question was asked explicitly):

- It is **downstream of the kill**. The kill causes part of it — the victim
  is one of the players missing at the plant. Conditioning on a
  post-treatment variable blocks the path you are trying to measure, and
  since plant-state is a common effect of the kill *and* of unmeasured
  factors (team skill, site control, utility), conditioning can open
  collider paths. So −0.0098 is not "the effect with confounding removed";
  it is a number with its own bias of unknown sign.
- **The over-control is visible.** Comparing `b4` and `b16` rounds that both
  ended 5v4 at the plant means comparing "got a kill and planted
  immediately" against "got a kill, then sixteen seconds of nothing, then
  planted". The reweighting is severe — within the 5v5 attacker-first-blood
  cell, 5v4-at-plant is 55.7% of `b4` (the modal case) but only 11.8% of
  `b16` (an unusual, unproductive one):

```
  b4  (plant 4s later, n=244)         b16 (plant 16s later, n=880)
    5v4   55.7%   win 0.743             5v3   14.5%   win 0.930
    4v4   16.0%   win 0.615             4v3   12.0%   win 0.811
    5v3   13.9%   win 0.941             5v4   11.8%   win 0.817
    4v3    7.4%   win 0.778             5v2   11.1%   win 0.980
    3v3    2.5%   win 0.333             4v2   10.2%   win 0.956
    kills after: 0.63                   3v2    5.7%   win 0.700
                                        kills after: 2.34
```

  Conditioning forces the `b16` group onto its worst rounds — its 5v4
  stratum wins 0.817 against a cell average of 0.832 — so **−0.0098 likely
  understates the residual**. This is why the design-based −0.0206 is the
  better anchor.
- **The symmetry argument, which is the real point.** If plant-state is
  rejected for being post-kill information, the same objection lands on
  `dt`, which is *also* partly determined after the kill. Controlling for
  everything before the kill does nothing about a variable that is partly
  determined after it. That is the entire finding restated.

**What would distinguish the two readings:** a residual `dt` gradient after
closing the subsequent-kill channel that is large and stable. It isn't —
−0.0206 [−0.0346, −0.0048], about a quarter of the headline, and adding
`plant_time` leaves −0.0258 [−0.0476, −0.0072] at only 0.53 coverage. If
that residual is worth chasing, the informative variable is **spatial**
(defender proximity to the site at plant time), not temporal — and this
schema carries no positional data.

**Caveats on the verification itself, stated plainly:**

- The last-pre-plant-kill restriction selects on a post-kill fact (that no
  more kills followed), so it is not bias-free either. Its value is that it
  is wrong in a *different* way from conditioning on plant-state, and the
  two agree at ~2pp. Neither number alone carries the argument.
- Coverage in the [D] and [G] rows is low (0.53, 0.25); those are directional
  support, not precise estimates.
- Everything here is association. No causal claim is established in either
  direction; the argument is that the *specific* causal reading proposed
  ("faster plants") is not supported and is contradicted by the composition.

**Scope of this verification:** read-only throughout. Nothing in
`app/scoring/impact.py` was touched, nothing was rescored,
`IMPACT_CALCULATION_VERSION` was not changed, and no monotonicity
requirement in the spec or the implementation was relaxed.
