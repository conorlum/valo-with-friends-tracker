# Every arm, and what happened to it

A catalogue of the post-plant time-factor investigation, 2026-09-19/20. Companion to `HANDOFF.md` (the map) and
`../2026-09-07-predeclared-values.md` (the authoritative ledger, declarations 1–8).

## The one-sentence version

**Almost every arm beat the shipped model. Almost nothing beat a flat constant.**

That sentence is the whole investigation. The shipped ramp is a low bar, so "improves on `P0`" was cheap and nearly
universal. The question that mattered was always *"does this beat a flat constant at the same level?"* — and that
comparison is where every structural idea died, or failed to be asked at all.

## How to read the numbers

- Sign convention throughout: `loss(arm) − loss(reference)`. **Positive is worse.**
- Every arm is scored as the fixed composite `impact_diff` with **one free coefficient**, fitted out-of-fold on
  3,198 matches, 5 match-clustered folds, 2,000-draw paired bootstrap.
- An interval spanning zero is **INCONCLUSIVE**, never "no harm found".
- Unless stated otherwise the target is **T2** (a 3-round forward window). Method **C** (the round's own outcome)
  is marked explicitly; it is a different question and its numbers are not comparable to T2's without normalising
  by headroom — see `HANDOFF.md` §1.

### An arm can fail in three different ways, and they are not the same

| kind | what it means | is it evidence against the idea? |
|---|---|---|
| **genuine null** | the contrast had power to see an effect and saw none | **yes** |
| **UNTESTABLE** | the arm is a near-rescale of its comparator; the estimator is blind to it | **no** — the question was never asked |
| **harm** | it measurably made things worse | yes |

The distinction was added late (declaration 6's correction) and it retroactively rewrote several conclusions. The
gate: compute R² between the arm's `impact_diff` and its comparator's *before* bootstrapping. Below the demonstrated
detection floor for that target, report UNTESTABLE.

---

## 1. The arms that beat the shipped model

All against `P0`, target T2. This section is long because it is nearly everything.

### 1a. Flat constants — replace the post-plant regime with one number

`F-k`: no ramp, no plant+38..45 override. Pre-plant stays 1.0, post-resolution stays 0.5.

| arm | vs `P0` | verdict |
|---|---:|---|
| `F-0.0` | −8.266e−05 | INCONCLUSIVE |
| `F-0.05` | −8.493e−05 | INCONCLUSIVE |
| `F-0.1` | −8.778e−05 | INCONCLUSIVE |
| `F-0.15` | −8.975e−05 | IMPROVEMENT |
| `F-0.2` | −9.071e−05 | IMPROVEMENT |
| `F-0.3` | −9.242e−05 | IMPROVEMENT |
| **`F-0.4`** | **−9.259e−05** | **IMPROVEMENT** — best flat arm on T2 |
| `F-0.6` | −8.633e−05 | IMPROVEMENT |
| `F-0.8` | −7.285e−05 | IMPROVEMENT |
| `F-1.0` | −5.270e−05 | IMPROVEMENT |
| `F-1.2` | −2.494e−05 | IMPROVEMENT |
| `F-1.26` | −1.516e−05 | INCONCLUSIVE |
| `F-1.4` | +9.469e−06 | INCONCLUSIVE |

**Every constant from 0.15 to 1.2 beats the shipped ramp.** The curve is a clean parabola (quadratic fit: max
residual 1.6e−07 on a curve spanning 1.0e−04) with vertex **k\* = 0.3221** and a zero crossing at **k = 1.349**.

### 1b. Level scaling — keep the ramp's shape, scale it

`P4-k` and `L-k` are **the same function** (`_v_p4`), scaling the whole post-plant regime by `k`; the two prefixes
are just two grids declared at different times.

| arm | vs `P0` | verdict |
|---|---:|---|
| `L-0.1` | −8.785e−05 | IMPROVEMENT |
| `L-0.2` | −9.035e−05 | IMPROVEMENT |
| `L-0.3` | −8.990e−05 | IMPROVEMENT |
| `L-0.4` | −8.622e−05 | IMPROVEMENT |
| `L-0.5` | −7.979e−05 | IMPROVEMENT |
| `L-0.6` | −6.991e−05 | IMPROVEMENT |
| `L-0.8` | −4.107e−05 | IMPROVEMENT |
| `P4-0.7` | −5.748e−05 | IMPROVEMENT |
| `P4-0.7826` | −4.402e−05 | IMPROVEMENT |
| `P4-0.9` | −2.179e−05 | IMPROVEMENT |

### 1c. Fitted-per-fold selections, and the combination

| arm | what it is | vs `P0` | verdict |
|---|---|---:|---|
| `Lf` | level, fitted per fold | −8.990e−05 | IMPROVEMENT — **selected 0.3 on all 5 folds, grid PINNED at its floor** |
| `Ff` | flat, fitted per fold | −9.259e−05 | IMPROVEMENT — **selected 0.4 on all 5, grid PINNED** |
| `Lf2` | level, widened grid | −8.830e−05 | IMPROVEMENT — selected 0.2–0.3, bracketed |
| `Ff2` | flat, widened grid | −9.073e−05 | IMPROVEMENT — selected 0.3–0.4, bracketed |
| `P4f` | Part 4's scale, fitted | −5.748e−05 | IMPROVEMENT — selected 0.70 on all 5 folds |
| `PC+` | `PC` + `P4-0.70` combined | −6.050e−05 | IMPROVEMENT |
| `A1` | side-asymmetric level | −8.163e−05 | IMPROVEMENT |
| `A2` | side-asymmetric, time-banded | −7.516e−05 | IMPROVEMENT |
| `D1` | pay the measured swing directly | −8.716e−05 | IMPROVEMENT |

**`Lf` and `Ff` do not count as fitted constants.** Both pinned against their grid floor on every fold. A selection
at a grid edge is not a selection; it took two more grids to bracket the value. That trap is in `HANDOFF.md` §4.

---

## 2. The comparison that mattered — and where everything died

Beating `P0` is cheap. Here is every arm tested against **a flat constant at a comparable level**, which is the
only comparison that could justify shipping structure.

| contrast | point | verdict | resid. var. | what it really says |
|---|---:|---|---:|---|
| `Ff` vs `Lf` | −2.697e−06 | INCONCLUSIVE | — | **the ramp's shape cannot be told from flat** |
| `Ff2` vs `Lf2` | −2.431e−06 | INCONCLUSIVE | — | same, on the bracketed grids |
| `A1` vs `F-0.4` | +1.096e−05 | INCONCLUSIVE | **0.0498%** | **UNTESTABLE** |
| `A2` vs `F-0.4` | +1.743e−05 | **HARM** | — | measurably worse than flat |
| `A2` vs `A1` | +6.473e−06 | **HARM** | — | the time-banding hurt |
| `D1` vs `F-0.4` | +5.430e−06 | INCONCLUSIVE | **0.0625%** | **UNTESTABLE** |
| `P5` vs `P6` | −7.405e−06 | INCONCLUSIVE | **0.0132%** | **UNTESTABLE** |
| `P5b` vs `P6` | −1.031e−05 | INCONCLUSIVE | **0.0336%** | **UNTESTABLE** |
| `F-0.0` vs `Ff2` | +8.073e−06 | INCONCLUSIVE | — | zero is indistinguishable from the fitted constant |
| `F-0.3` vs `F-0.4` | +1.705e−07 | INCONCLUSIVE | — | the level is flat across 0.3–0.4 |

**Not one structural arm beat a flat constant.** Four of the ten contrasts could not have, because the arm was a
near-rescale of its comparator and the estimator is blind to a rescale by construction.

---

## 3. The failures, by kind

### 3a. Genuine nulls — had the power, found nothing

| arm | what it is | vs `P0` | resid. var. | why it failed |
|---|---|---:|---:|---|
| **`P6`** | **Part 4 exactly as built** — a `(a, d, t, victim_side)` state table, `D / mean_t D` | +1.824e−06 INCONCLUSIVE | **0.220%** | **twice the demonstrated floor — it had room to be seen and was not.** The `mean_t D` division removes each state's own level, leaving only time *shape*, and the shape is worth nothing |
| `P5` | differential rungs below Part 4's exact rung | −5.581e−06 INCONCLUSIVE | ~0.22% | null **against the shipped model** (that claim stands); untestable against `P6`, which is the comparator the hypothesis was about |
| `P5b` | differential rung IS rung 1 | −8.491e−06 INCONCLUSIVE | ~0.22% | same |

`P6` is the strongest negative in the investigation, and it is the only structural null that is genuinely evidence.

### 3b. UNTESTABLE — the question was never asked

These returned INCONCLUSIVE and were originally written up as "does not help". That reading is **withdrawn**.

| contrast | resid. var. | floor | why the test was blind |
|---|---:|---:|---|
| `A1` vs `F-0.4` | 0.0498% | 0.107% | `A1 = 0.99672 × F-0.40 − 2.53`, R² 0.9995. A 0.997 rescale of its own comparator |
| `D1` vs `F-0.4` | 0.0625% | 0.107% | `corr(D, K) = 0.823` — paying the measured swing ≈ paying `K` times a constant. **The swing information was already inside `K`** |
| `P5` vs `P6` | 0.0132% | 0.107% | 8x below the floor; differs from `P6` only in the pooling ladder |
| `P5b` vs `P6` | 0.0336% | 0.107% | 3x below the floor |

**The deeper reason `A1` collapsed.** The measured asymmetry is real and large — in a 1v1 at 38–45s an attacker's
death costs 50.0pp against a defender's 4.0pp. But `_time_factor` **returns one number per event**, applied to the
killer's credit and the victim's debit alike, so within a round the re-weightings largely cancel in the
differential. **No arm reachable through this wrapper could have carried that hypothesis.** Testing it needs a
scorer that can charge the two sides differently for the same event — an interface change, not a new constant.

`A1`'s nested/incremental test agrees: the extra column's fitted coefficient **flips sign across folds**
(+0.044, +0.106, −0.072, −0.513, −0.311), so only the coefficient *sum* is identified.

### 3c. Measurable harm

| contrast | point | interval | why |
|---|---:|---|---|
| `P2L` vs `P0` | +1.814e−06 | [+9.932e−07, +2.691e−06] | spreading `P2b`'s death-side leverage uniformly over every post-plant death is worse than leaving it concentrated |
| `A2` vs `F-0.4` | +1.743e−05 | [+9.068e−07, +3.368e−05] | the time-banded side split is worse than a flat constant |
| `A2` vs `A1` | +6.473e−06 | [+5.802e−07, +1.275e−05] | adding the `t >= 30` band to the side split actively hurt |
| **`F-1.00` on C** | **+9.824e−04** | **[+6.548e−04, +1.304e−03]** | **on the round's own outcome, parity underpays post-plant kills.** This refutes `T = 1.00` |
| `F-0.7` / `F-0.4` / `F-0.3` on C | +3.417e−03 / +6.953e−03 / +8.428e−03 | all exclude zero | the whole 0.3–0.7 region is badly wrong for the within-round question |

### 3d. The Tier A correctness fixes — a different bar

These were argued *a priori* as bug fixes and measured **only for harm**. INCONCLUSIVE is a pass, not a failure.

| arm | what it fixes | vs `P0` | verdict |
|---|---|---:|---|
| `P1` | a decided round pays 0, not 0.5 | −4.736e−06 | INCONCLUSIVE — no harm |
| `P2b` | the death cliff at plant+38 becomes a linear decay | +3.619e−06 | INCONCLUSIVE — no harm |
| **`P3a`** | **cap the factor at plant+45** | +2.897e−07 | INCONCLUSIVE — no harm. **Recommended** |
| `P3b` | a phantom plant gets no post-plant regime | +5.033e−08 | INCONCLUSIVE — no harm |
| `PC` | `P3b` + `P1` + `P2b` combined | −9.850e−07 | INCONCLUSIVE — no harm |

**Take `P3a` over `P3b`.** The defect is 566 Elimination-Win rounds with clock skew of at most 1.88s, not phantom
plants — only **2 of 41,515** planted rounds are physically impossible. `P3a` addresses 634 rows against `P3b`'s 45.

---

## 4. Why no shape model could ever have won

Three independent reasons, each sufficient on its own.

**1. The events that would separate the models barely exist.** 72% of post-plant kills land in the first 20 seconds,
where every candidate curve sits between 1.0 and 1.4. Only **1.02%** occur at t ≥ 40, where they diverge wildly —
the measured 2v1 value falls to 0.18 while the shipped ramp pays 1.75. **A model can be badly wrong about 1% of
events and be undetectable.**

**2. The information was already in `K(s)`.** Mean `K` is 1.018x larger post-plant; the measured win-probability
swing is 1.023x larger. The kill-order bonus already prices a post-plant kill almost exactly right *while being
verifiably spike-blind* (`1v1→0v1` and `1v1→1v0` both weight 250). There was no shape left for `T` to add.

**3. One free coefficient absorbs any rescale.** Anything that amounts to multiplying `impact_diff` by a constant
returns a contrast of zero by construction. Four of the ten structural contrasts fell into this hole.

---

## 5. Scoreboard

| outcome | count | arms |
|---|---:|---|
| beat the shipped `P0` | 20+ | every flat 0.15–1.2, every level 0.1–0.9, `PC+`, `A1`, `A2`, `D1`, `P4f`, `Lf`/`Ff`/`Lf2`/`Ff2` |
| beat a flat constant | **0** | — |
| genuine null vs flat/`P6` | 3 | `P6`, `Ff` vs `Lf`, `Ff2` vs `Lf2` |
| UNTESTABLE | 4 | `A1`, `D1`, `P5`, `P5b` |
| measurable harm | 5 | `P2L`, `A2` (twice), and on target C `F-1.00` and the 0.3–0.7 region |
| no-harm correctness fixes | 5 | `P1`, `P2b`, `P3a`, `P3b`, `PC` |

**What survives to ship:** a flat constant (level per `HANDOFF.md` §1, currently 1.18–1.35), the Tier A fixes taking
`P3a`, and **no structure whatsoever** — no state table, no differential grouping, no side asymmetry, no time shape.
Not zero: `F-0.00` is statistically indistinguishable from the fitted constant, and was pre-committed as never
shippable because a decisive duel scoring nothing contradicts a standing constraint.
