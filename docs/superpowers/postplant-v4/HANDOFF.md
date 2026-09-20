# Post-plant time factor — handoff

Session of 2026-09-19/20. Everything here is measured, out-of-fold and reproducible; nothing is implemented,
nothing is activated. `IMPACT_CALCULATION_VERSION` stays **3** and `git diff webapp/app/` is empty.

Read this before re-deriving anything. The ledger entries in `../2026-09-07-predeclared-values.md` (declarations
1–7 and their RESULT entries, 2026-09-19 and 2026-09-20) are authoritative; this file is the map.

---

## 1. The state of the question

**The shipped post-plant time factor overpays, and `K(s)` does not.**

| | |
|---|---|
| mean `K` post-plant ÷ pre-plant | **1.018** |
| mean win-probability swing post ÷ pre | **1.023** |
| mean `T` post-plant | **1.264** (rising to 1.75) |
| what the scorer pays, post ÷ pre | **1.299** |

`K(s)` already prices a kill almost exactly right without knowing the spike exists. The time factor multiplies by a
further ~1.26× that **no measurement justifies**.

**But how far it should come down depends on the question**, and this is the session's central finding:

| method | machinery | implied scalar |
|---|---|---|
| grid search | loss contrasts, target T2 (3-round forward window) | ~0.3 |
| **A** coefficient ratio | `b_post/b_pre` fitted, target T2 | **0.431** (sd 0.017) |
| **B** direct swing | mean \|ΔV\| ratio, **no target at all** | **1.023** |
| **C** round's own outcome | flat arms vs shipped | **shipped ramp wins** |

Forward-looking targets want ~0.3–0.43. Within-round measures want ~1.0 or higher. Both are true about different
quantities: post-plant play decides the round it happens in and predicts later rounds poorly.

**`T = 1.00` is the only value no measurement contradicts.** B measures it directly; A and the grid say "well below
1.26", which 1.00 satisfies; C prefers the shipped ramp, **but `F-1.00` was never run on C's target.**

### The single open gap

**Run `F-1.00` on the round-outcome target (method C).** One replay, ~10 minutes:

```bash
cd webapp
DATABASE_URL="postgresql+psycopg2://postgres@localhost:5434/valo_v4" \
  ./.venv313/Scripts/python.exe scripts/postplant_v4_alt_metrics.py --out DIR --mode C
```
(add `F-1.0` to the `arms` dict in `mode_c`). If `F-1.00` is not HARM on the round target, `T = 1.00` is
uncontradicted by all four methods and version 4 can be specified around it.

---

## 2. The math, so it is not re-derived

### What the time factor is, and where it sits

```
impact   = A·damage + B·leverage + C·econ + D·assists      A=1 B=2.5 C=2.5 D=100 (rc3)
leverage = Σ_kills K(s)·T(t) + trade − Σ_deaths K(s)·T(t)
```

`K(s)` is the kill-order bonus — the man-advantage transition's worth, verified **symmetric under team relabeling**
(0 asymmetric edges) and **spike-blind** (`1v1→0v1` and `1v1→1v0` both weight 250). `T(t)` is meant to carry only
*when* it happened.

### Shipped `T`

```
pre-plant           T = 1
t < 38s             T = 1 + t/53        → 1.72 at 38s
38 ≤ t ≤ 45s        T = 1.75 kill / 0.50 death
after resolution    T = 0.5
```

### Part 4's replacement (built, never activated)

```
D(a,d,t) = V(a,d,t) − V(a−1,d,t)                  attacker victim
T(t)     = clamp(D / mean_t D, 0.05, 2.0) × c     c = 1.286862
```

The `mean_t D` division removes each state's own level, leaving only time shape. **That is why it measured
nothing** — see §3.

### Why every shape model failed

72% of post-plant kills land in the first 20 seconds where all candidate curves sit between 1.0 and 1.4; only
**1.02%** occur at t≥40 where they diverge wildly (2v1 measured falls to 0.18 while the shipped ramp pays 1.75).
A model can be badly wrong about 1% of events and be undetectable.

### The reusable tables

`derived_tables.json` in this directory holds, so none of it needs recomputing:

- `V_whole_round` — V(a, d, planted) over whole-round second occupancy, 71 supported states with counts. **This is
  the only pre-plant V that exists**; Part 4 built post-plant only.
- `V_postplant_by_band` — V(a,d,t) collapsed to 5 time bands for all 25 states, with counts.
- `swing_vs_K` — `corr(D,K) = 0.823`; mean D 21.92pp for attacker victims vs 13.76pp for defender victims.
- `kill_mass_by_second` — post-plant kill counts per second, 0–44.

Key derived numbers already in the ledger: attacker win % by band and by man-advantage group; time is worth
**+1.58pp/sec when down two** vs **+0.14pp/sec when up two**, ~11× more to the side that is behind.

---

## 3. What is settled, and what was withdrawn

**Settled:**
- The level is wrong and the direction is clear: post-plant is overpaid.
- `K(s)` is already right — the swing is priced without the spike.
- Part 4's shape genuinely fails **against the shipped model** (`P6 vs P0`, 0.220% residual variance, twice the
  detection floor — it had room to be seen and was not).
- The Tier A correctness fixes do no harm and are still recommended, **using `P3a` (cap at plant+45) not `P3b`**:
  the defect is 566 Elimination-Win rounds with clock skew of at most 1.88s, not phantom plants.
- `D1` (pay the measured swing directly) beats the shipped model but is **indistinguishable from a flat constant** —
  `corr(D,K)=0.823` means paying the swing ≈ paying `K` times a constant. The swing information was already in `K`.

**Withdrawn during the session, do not resurrect:**
- *"Two-thirds of planted rounds have missing resolution flags."* Wrong — attacker-elimination is a legitimate third
  ending (28,695 rounds). Only **2 of 41,515** planted rounds are physically impossible.
- *"The side asymmetry does not help."* Wrong — `A1` was `0.997 × F-0.4`, R² 0.9995, **untestable**, not negative.
- *"The differential regrouping does not beat Part 4."* Untestable (R² 0.9997+). It does genuinely fail against the
  *shipped* model, which is a different claim.
- *"Four structural models lost to a constant."* One did.
- *"Same event worth 3.5× more to one side."* Wrong — those are two *different* events; a kill is zero-sum in win
  probability.

---

## 4. The protocol gate — read this before adding any arm

Every arm is scored as a fixed composite with **one free coefficient**, which makes weighted log loss **invariant to
an affine rescale**. An arm that is a near-rescale of its comparator returns a zero contrast *because the estimator
cannot see it*, and that is indistinguishable from "does not help" unless you check.

**Before any bootstrap**, compute R² between the arm's `impact_diff` and its comparator's:

| | residual variance | status |
|---|---|---|
| `F-0.4` vs `P0` | 0.734% | testable, registered |
| `P6` vs `P0` | 0.220% | testable, genuine null |
| `F-1.0` vs `P0` | **0.107%** | **the demonstrated detection floor** |
| `A1` vs `F-0.4` | 0.0498% | UNTESTABLE |
| `D1` vs `F-0.4` | 0.0625% | UNTESTABLE |
| `P5` / `P5b` vs `P6` | 0.0132% / 0.0336% | UNTESTABLE |

Below **0.107%**, report **UNTESTABLE**, not INCONCLUSIVE. Untestable is not evidence against and not a licence to
ship — it means the question was never asked.

Other traps that cost time tonight:
- A fitted constant selected at a **grid edge** is not fitted. Two grids pinned against their floor before a wide
  enough one bracketed it. Declare a boundary rule.
- `build_target` **drops rows** (53,730 of 67,251 survive T2's forward window). Never staple a column onto its
  output — route it through `_feature_value` so it filters identically.
- `_kill_order_bonus`'s index arguments are **inverted** relative to the teams they count. Validate any
  reconstruction against the scorer's own stored values (`TEAM_1 if victim_is_attacker` matches all 153,481 kills;
  the mirror matches 24%).
- `predict_proba` computes `beta[0] + X @ beta[1:]`, so **`beta[0]` is the intercept**, not the first feature.

---

## 5. Tooling and how to re-run

All read-only. All in `webapp/scripts/`:

| script | what it does |
|---|---|
| `postplant_v4_variants.py` | every arm, as a wrapper **around** `_time_factor` — calls the shipped function and overrides its result. `app/` is never edited |
| `run_postplant_v4_report.py` | the arm runner: identity gate, per-fold fitting, per-arm caching of out-of-fold predictions |
| `postplant_v4_contrast_subset.py` | named subsets of contrasts, parallelisable. Use this — the full runner recomputes all ~35 bootstraps every invocation (an hour) |
| `postplant_v4_incremental.py` | nested/encompassing test: does arm X carry information beyond arm Y? |
| `postplant_v4_alt_metrics.py` | methods A, B, C |
| `postplant_v4_row_motion.py` | how much stored Impact an arm actually moves (not a contrast, no verdict) |

### The local corpus — this is the big time-saver

A replay against Render is **28 minutes**; against a local copy it is **80 seconds** (21×). The data dumps in 19
seconds — the cost was never volume, it was 12,792 network round trips per pass.

```bash
# start (the cluster already exists at ~/pg18/data, restored and verified)
powershell -Command "Start-Process -FilePath 'C:\Users\Conor Lum\pg18\pgsql\bin\postgres.exe' \
  -ArgumentList '-D','\"C:\Users\Conor Lum\pg18\data\"','-p','5434' -WindowStyle Hidden"
DATABASE_URL="postgresql+psycopg2://postgres@localhost:5434/valo_v4"
```

**Start it detached** (`Start-Process`, not `pg_ctl` from a shell) — a shell-owned postmaster dies with the shell,
corrupts its shared memory segment, and needs the orphan killed by PID plus `postmaster.pid` removed.

Verified identical to production: 3,198 matches / 67,453 rounds / 499,093 kill_events / 674,530 round_player_stats,
alembic 0010, match-id md5 `1d639f01ece40d3cf43b7b94352edccc`.

To rebuild from scratch: server binaries are a no-admin ZIP from
`https://get.enterprisedb.com/postgresql/postgresql-18.6-1-windows-x64-binaries.zip`; extract **only**
`pgsql/{bin,lib,share}` with Windows `tar.exe` to a **short path** (the full archive blows Windows' 260-char limit,
and MSYS `tar` reads `C:` as a hostname and silently does nothing). Dump with `--exclude-table-data` for
`impact_scores`, `impact_scores_v1`, `player_view_cache`, `site_stats_cache` — 157MB of the 346MB.

### Artifacts in this directory

`contrasts*.json` (all arm contrasts), `alt_metric_{A,B,C}.json`, `incremental_A1_over_F-0.4.json`,
`row_motion.json`, `identity_gate.json`, `d1_scale.json`, `{lf,ff,lf2,ff2}_selected.json`, `{a1,a2}_weights.json`,
`p{5,5b,6}_centring.json`, `p{5,5b}_rungs.json`, `derived_tables.json`, `meta.json`.

Corpus fingerprint `3198:f9a31bb2df2586ec`, fold mapping `cebae50f85e94736` — every arm shares them.

---

## 6. If version 4 proceeds

Blocked on the owner's answer to **what Impact is for**, and on the `F-1.00`-on-C gap above.

1. **Post-plant regime → a flat constant.** `1.00` if Impact rates contribution to the match played; `0.3–0.43` if
   it forecasts future performance. Remove the ramp's growth and the plant+38..45 override either way.
2. **Tier A fixes**, taking `P3a` over `P3b`.
3. **No structure**: no state table, no differential grouping, no side asymmetry, no time shape. None beat a
   constant, and the ones that "lost" mostly were not measurable.
4. **Not zero** — `F-0.00` is indistinguishable from the fitted constant, and was pre-committed as never shippable
   because a decisive duel scoring nothing contradicts a standing constraint.

Row motion for the Tier A combination: 6,891 rows (1.02%), 242 matches reordered (7.6%) — clears both declared
version-4 thresholds. Scale check: the best contrast is ~1/215th of what the whole scoring system buys, so the
version bump is justified by the correctness fixes and the sign error, not by the size of the gain.
