# The econ component is zero-sum, and the design that wasn't

## Objective

`econ_component` in the Valorant Impact score is **exactly zero-sum** across the
ten players in a round. It should not be. A non-zero-sum design was specified,
was never implemented, and was retired as a documentation error rather than
evaluated. Bring it back: build it, measure it, and decide it on evidence.

Repo `valo-with-friends-tracker`, branch `impact-scoring-impl`. Everything below
was verified in-session against the code and the local DB (3,124 matches).

## How the component works today

`webapp/app/scoring/econ_component.py` and `_econ_components_for_round` in
`webapp/app/scoring/impact.py:532`. Per round:

```
1.  C(v)        = victim's committed value = loadout - free ability credits
2.  removed(T)  = sum of C(v) over enemies team T killed this round
    lost(p)     = your own C(v), when you die
3.  econ_round(T):
      late  (rounds 5-11, 17-23):  0.5 + 0.2 * (enemies below 4200 credits NEXT round)
      early (rounds 2-4, 14-16):   commitment(what the enemy had) * denial_early(their
                                   total wealth NEXT round)
      pistols, halftime, OT, final round:  0
4.  allocation:
      credit(p) = econ_round(T)   * removed_by(p) / removed(T)
      debit(p)  = econ_round(opp) * lost_by(p)    / removed(opp)
      econ_component(p) = ECON_SCALE * (credit - debit)      ECON_SCALE = 1007.9209
```

`econ_round(T)` reads the **enemy's** next-round state, so it is the right
quantity: "how poor did T make the other side."

## The defect

**Step 4 uses the same number twice.** `econ_round(opp)` is simultaneously team
opp's credit pool and team T's debit pool. Sum the credits over a team and the
`removed(p)` cancel the denominator, leaving `econ_round(T)`. Sum the debits over
team A and `sum(lost_by(p))` is by definition `removed(B)`, leaving
`econ_round(B)`. Credits and debits therefore sum to the same thing and the
component nets to exactly zero.

Verified, not inferred — 60 matches, straight out of the allocator before rounding:

```
sum of econ_component, ROUNDED ints  :          -10
same quantity UNROUNDED              :     0.000000
```

Across all 3,124 matches the per-match sum has mean -0.269, sd 3.12, range
-12..+11; 99.9% within +/-10. Predicted sd of 240 independent `round()` calls is
sqrt(240/12) = 4.47. It is rounding noise and nothing else.

**The consequence that matters: your debit is the enemy's gain, so when the enemy
earns nothing you pay nothing, however broke you are.** Two independent routes
zero the enemy's gain:

- the `commitment` gate — you were saving, so there was nothing to deny
- `scarcity = 0` — you are rich next round

The first route is the bug. It zeroes your debit for a reason that has nothing to
do with your own economy.

### Concrete case: match 3104, round 14

Team B saves (mean committed 530, below `SAVE_FLOOR` 1000), so `commitment = 0`
and `gain(A) = 0`. Team B loses 4,550 credits of equipment and enters round 15 at
**4,750 per player — genuinely poor**. Every team B player is debited **exactly
zero**, because their debit is team A's gain and team A's gain was gated to nil.

Player 226 removed 800 and lost 1,150 that round, and scores **+11**.

### Sharper case: match 3104, the same player in rounds 16 and 17

player 226 is killed first blood at 5v5 by the same player in both rounds,
with the same `kill_order_bonus` of 150 and the same time factor of 1.000.
Leverage is **-150 in both**. The totals differ because of econ:

```
                A*damage   B*leverage   C*econ   = impact
ROUND 16    p226         0        -150         0      -150
ROUND 17    p226        39        -150      -101      -212

R16 (EARLY regime)  econ_round(TEAM_1) = 0.0000   removed(TEAM_1) = 12,850
    p226       lost 4,000   debit 0.0000  ->     0
R17 (LATE regime)   econ_round(TEAM_1) = 0.5000   removed(TEAM_1) = 22,400
    p226       lost 4,500   debit 0.1004  ->  -101
```

**R16** — the debit is team A's gain, and team A's gain is 0 because player 226's
own team is rich next round (42,400 total, 8,480/player, above `ZERO_AT`). They
lose 4,000 credits of equipment and pay nothing.

**R17** — the regime flips to late and team A's gain is **exactly the 0.5 floor**:
`denial_late = 0.5 + 0.2 * (enemies below full buy next round)` with that count
at **zero**. Team A removed 22,400 credits of equipment and team B re-bought in
full. The denial provably did not happen; team A collects 0.5 anyway and
player 226 is charged their share of it.

Same player, same death, consecutive rounds, 0 then 101 — the difference is the
round number, not anything either team did. Both defects in one example: the
inherited debit in R16, the standalone floor in R17.

## The history — this was specified, then deleted

Commit `d475e4d`, `docs/superpowers/specs/2026-09-04-econ-impact-separate-component-design.md`,
section 7, before the rewrite:

> `econ_component` is **not** zero-sum across teams. Team A's credit derives from
> `magnitude(A) * w * denial` and team B's debit from the same removal but scaled
> by **B's own state and denial**, which differ. This is deliberate — **a round
> can be economically bad for both sides** — and is stated so no test asserts
> symmetry.

The current section 7 is titled *"Zero-sum — it IS, and an earlier draft said
otherwise"* and concludes:

> The prose was describing a design the equations do not implement. [...]
> Zero-sum is accepted rather than worked around.

**Section 6's equations are byte-identical between `d475e4d` and today.** The
prose was always aspirational; the equations were always zero-sum. The
resolution changed the prose to match the code. The non-zero-sum design was never
built, never measured, and never compared against anything.

### A related deletion nobody re-checked

`econ_round` originally had three factors:

```python
econ_round(T) = magnitude(T) * w(state) * denial(T)
magnitude(T)  = removed(T) / R          # R = 19500, absolute credits removed
```

`magnitude` and `w(state)` were both deleted 2026-09-05, deliberately, justified
by measurement `M12d` ("credits removed is the *price*; what matters is whether
they can *replace* it, which depends on their bank"). Defensible on its own
terms — but it left the late regime's `0.5` floor standing alone. Previously the
floor sat inside a product: one 800-credit pistol kill gave
`800/19500 * 0.5 = 0.021`. Today it gives a flat **0.5**, about 24x more.
Measured: **2,631 of 13,388 team-rounds (19.7%) sit at exactly 0.5** — enemy
fully re-bought, killer credited anyway, victim debited anyway.

## What is NOT the problem

Do not chase these; they were measured and are separate concerns.

- **Winner-entanglement is not caused by zero-sum.** At match level the winning
  team has the higher econ total in 80.9% of 3,086 decided matches — but only
  **58.9% in 1-2 round nail-biters**, versus damage 75.6% and leverage 83.5%.
  Econ is the *least* winner-entangled of the three terms at every margin band.
  At round level it correlates +0.5469 with round N's own result, against damage
  +0.8473 and leverage +0.9420.
- **The debit already scales with your own poverty** in the normal case, because
  `econ_round(opp)` reads your team's next-round state. The break is the gating,
  not the direction.

## The proposed fix

Any construction where both sides are shares of one pool is zero-sum by
arithmetic. Breaking it requires the debit to become its own quantity:

```
gain(T) = how much T denied the enemy      [UNCHANGED — reads enemy next-round state]
harm(T) = denial_early(T's OWN wealth NEXT round) * lost(T) / R

credit(p) = gain(T) * removed_by(p) / removed(T)      [unchanged]
debit(p)  = scarcity(T) * lost_by(p) / R              [NEW — not a share of enemy gain]
```

This restores `magnitude` on the debit side, where its removal was never examined,
and gives the required behaviour directly: lose equipment while rich, `scarcity`
is ~0, `harm` is ~0.

### Prototype evidence, match 3104 early regime (read-only, nothing wired)

The `CURRENT` column was checked against the scorer's own `econ_observer` in
every round and matched exactly.

```
round      CUR A   CUR B    sum      NEW A   NEW B    sum
R2             0       0      0        -55       0    -55
R3          -261    +261      0       -147    +261   +114
R4          -202    +202      0       -212    +202    -10
R14          -62     +62      0        -39     +11    -28
R15         -365    +365      0       -381    +365    -16
R16         -483    +482     -1       -508    +482    -26
```

- **R14** is the target case: both `harm` values non-zero (0.0383 and 0.0502),
  so both sides carry economic damage — "a round can be economically bad for
  both sides", which the current design cannot express. player 226 goes
  **+11 -> -11**.
- **R3** is the rule working in both directions: team B loses 9,700 of kit and is
  debited **zero** (7,750/player next round, above `ZERO_AT`), while team A pays
  147 instead of 261.
- **R4, R15, R16** go the other way — the new debit is *larger* when your own
  poverty exceeds what the enemy earned. The asymmetry is not a uniform softening.

## Work to do

1. **Late regime (5-11, 17-23) walkthrough** on match 3104, same shape as the
   early one above. This is where the weight is: 14 rounds instead of 6, no
   `commitment` gate, and the standalone 0.5 floor. Not yet done.
2. **Corpus-wide measurement**: over all 3,124 matches, how often does the new
   version produce both-teams-negative and both-teams-positive rounds? That is
   the claim the original prose made and one match cannot settle it.
3. **Re-examine the 0.5 late floor** now that `magnitude` is gone. Arguably a bug
   from the 2026-09-05 deletion rather than a design change, and cheap to test.
4. **`ECON_SCALE` recalibration.** It is currently 1007.9209, set so
   `SD(econ_component)` equals `SD(time_impact)` over scored player-rounds. A
   non-zero-sum component has a different SD, so the anchor must be re-derived,
   not carried over.
5. **Decide the saving-team question.** The new `harm` has no `commitment` gate,
   so a team that deliberately saves and dies is debited (match 3104 R2: -55).
   The harm is real — they *are* poorer next round — but they chose it. This is a
   product judgement for the project owner, not a technical question.

## Constraints

- `IMPACT_CALCULATION_VERSION` stays 1. No flag defaults change. No rescore, no
  migration, no deploy. The repo is **public and deployed** — never commit a
  credential.
- **Predeclaration discipline.** Values, grids, targets and decision rules are
  committed BEFORE the measurement that judges them, in
  `docs/superpowers/2026-09-07-predeclared-values.md`. Amendments are appended.
  **Never edit a recorded value in place** — strike through and restate.
- **TDD.** Every behavioural change needs a test that FAILS before the fix and
  PASSES after, verified by reverting. If a revert only produces `ImportError` or
  `TypeError`, that proves nothing: reintroduce the defect BEHIND the new API and
  confirm a VALUE failure.
- The spec carries a **SCOPE LOCK** on the late denial regime (section "The
  design"). Changing it is a decision to reopen it, taken explicitly and recorded
  — not a drift.
- Verify claims numerically rather than by argument. This project has had
  conclusions reversed four times by pooled-vs-conditioned comparisons and
  several times by a fifteen-line demo beating a plausible chain of reasoning.

## The question to answer

Is the non-zero-sum construction better than the incumbent, measured rather than
argued? It was retired without ever being tested. It may still be wrong — but
"the prose didn't match the equations" is not a finding about the design, and
that is the only reason on record for dropping it.
