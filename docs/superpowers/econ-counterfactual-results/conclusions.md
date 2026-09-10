# Econ review: independent debit and the late floor

Completed 2026-09-10 against 3,124 local matches and 659,290 scored player-rounds.
The measurement declaration was committed first as `27eeef3`. Database access
was one repeatable-read, read-only transaction. No production imports of the
experimental module, changes to defaults/versions, rescore, migration or deploy.

## Conclusion

The independent debit does fix the specific allocation behavior requested:
own equipment loss is charged according to own remaining resources, without
being canceled by the opponent's credit gate. It is a testable alternative to
the incumbent, not a documentation correction.

However, adopting that debit while retaining the late 0.5 credit floor is
not a convincing complete design. Both teams receive positive net econ in
77.4% of late rounds. Combining the independent debit with removal of the
floor reduces that to 12.8% and permits both-negative late rounds. Of these
four candidates, **independent_no_floor is the one to review further**. This
is a recommendation based on the requested behavior and the examples below,
not a fitted optimum or evidence of more accurate causal/player attribution.

Do not try to repair these allocation issues merely by reducing C. A global
multiplier cannot remove an inappropriate award or create a missing debit.

## Reproduced findings

The reconstructed incumbent agreed exactly with the scorer's econ observer
and every persisted-form player-round value returned by its read-only builder.
The maximum absolute unrounded ten-player round sum was 4.44e-16.
Rounded incumbent match sums reproduce the prompt: mean -0.2692, SD 3.1217,
range -12 to +11; 99.904% within +/-10. These are rounding residues.

For the named player, **ternstyle#GIGI**, the following are ECON contributions
at the unchanged anchor 1007.9209 and C=1, not whole-round Impact:

| Match 3104 round | Incumbent | Independent debit | Floor removed only | Both changes |
|---|---:|---:|---:|---:|
| 14 | +11 | -11 | +11 | -11 |
| 16 | 0 | 0 | 0 | 0 |
| 17 | -101 | 0 | 0 | 0 |

Round 14 verifies the commitment-gate case. Round 16's zero debit remains
appropriate under the proposed policy: the team's next wealth is 8,480 per
player, above the zero-scarcity threshold. Round 17 also has zero scarcity
(6,500 per player); its old -101 is inherited from the opponent's 0.5 pool.
Either changing the debit or removing the floor removes that charge.

The newly completed late walkthrough has a useful counterexample in **round 5**:

| Team | Equipment lost | Next wealth/player | Next below-full-buy count | Incumbent net | Independent net | Both changes net |
|---|---:|---:|---:|---:|---:|---:|
| TEAM_1 | 18,200 | 5,430 | 0 | 0 | +309 | -195 |
| TEAM_2 | 23,600 | 5,840 | 0 | -1 | +371 | -132 |

Both teams re-arm fully, but their remaining total resources still have
positive scarcity under the chosen 6,300 threshold. Keeping the floor gives
both teams about 504 points of gross credit. Removing it leaves their own
scarcity-weighted loss debits, so both are negative. This demonstrates the
requested behavior without forcing the round to sum to zero.

## Corpus result

Percentages use unrounded **team net** values and the scored early/late
population. Zero boundary/abstention rounds are reported separately in the
walkthrough; they are not silently included in these denominators.

| Candidate | Early both negative | Early both positive | Late both negative | Late both positive |
|---|---:|---:|---:|---:|
| Incumbent | 0 / 18,118 | 0 / 18,118 | 0 / 34,142 | 0 / 34,142 |
| Independent debit | 1,132 (6.25%) | 277 (1.53%) | 0 (0%) | 26,414 (77.36%) |
| Floor removed only | 0 | 0 | 0 | 0 |
| Both changes | 1,132 (6.25%) | 277 (1.53%) | 130 (0.38%) | 4,376 (12.82%) |

Removing the floor alone preserves zero-sum: it still uses transfer pools.
There are 22,288 actual late credit allocations at exactly the floor, out of
65,739 late team-rounds with positive removed equipment: **33.90%**. The
prompt's 19.7% used a smaller denominator/population; it is not the result for
the complete current corpus used here.

There are **5,081 early team-rounds** with positive lost equipment, positive
own scarcity, and zero incumbent debit. These are the precise situations the
independent debit restores. There are no missing own-next-roster cases in this
snapshot, so imputation/abstention does not explain the difference.

## Corrections and limits in the prompt

1. **Round 14's saving team lost 2,650, not 4,550.** TEAM_2 has mean commitment
   530 and next wealth 4,750/player. The 4,550 loss belongs to TEAM_1. The named
   player's 1,150 loss and +11 -> -11 example do reproduce.
2. **4,750/player is not below the 4,200 full-buy threshold.** It is below the
   model's 6,300 zero-scarcity threshold. Describe it as positive modeled
   scarcity, not proof the team cannot afford a full buy. Team averages also
   hide individual loadout and bank distributions.
3. **Full re-buy establishes no observed below-buy deficit, not zero economic
   harm.** Re-buying can consume reserves. Round 5 illustrates that distinction.
4. The formula reads remaining-resource LEVELS. It does not establish how much
   poorer a team became because of these deaths, or whether choosing to save
   was a mistake. The proposed harm is a weighted resource-loss proxy.
5. The old section 7 really did describe a non-zero-sum intention while its
   section 6 equations implemented transfers. That history establishes an
   untested design alternative, not that non-zero-sum is automatically better.

## Scale recalculation

All four raw distributions include exactly the same 659,290 player-rounds,
including zeros. Reference SD(legacy time_impact) = 179.07228845. Both timing
retunes are disabled, consistently across variants. Primary comparisons above
keep the old scale to separate allocation changes from normalization changes.

| Candidate | Raw mean | Raw SD | Proposed SD-matching anchor |
|---|---:|---:|---:|
| Incumbent | approximately 0 | 0.198992 | 899.8970 |
| Independent debit | 0.061595 | 0.178120 | 1005.3451 |
| Floor removed only | approximately 0 | 0.115542 | 1549.8498 |
| Both changes | 0.011739 | 0.107061 | 1672.6149 |

**The existing 1007.9209 anchor itself no longer reproduces its recorded SD
match in this snapshot.** The measured current raw SD is 0.198992, versus the
historically recorded 0.177665. Current time SD still reproduces 179.0723.
Do not silently overwrite that old record or claim these are just rounding
differences. No recalculated constant has been installed.

The combined candidate's new anchor would amplify its raw values by about
1.66 relative to the existing anchor. Review that normalization separately:
matching SD does not validate the formula, and should not hide which penalties
or awards actually changed. The JSON provides both point scales. C remains 1
throughout this experiment, not the previously discussed policy choice 0.5.

## Remaining decisions before adoption

- **Saving-team policy:** the un-gated candidate charges actual recorded
  equipment loss even when mean commitment <=1,000. Match 3104 R2 changes
  TEAM_1 from 0 to -55, while TEAM_2 stays at 0. There are 5,259 early and 68
  late low-commitment team-rounds with equipment loss. The data do not record
  intent; calling this a penalty for a bad save would be unjustified. No owner
  choice on this question was received during the experiment.
- **The credit side still has no absolute removal magnitude.** Even without
  the floor, one small removal can allocate the entire `0.2 * below_buy_count`
  pool. Removing the floor fixes the zero-count case, not the possibility of
  attributing an already-poor opponent's whole state to a small kill.
- **Late harm reuses an early scarcity curve.** Wealth versus loadout-count
  asymmetry and the early/late credit boundary remain policy assumptions.
- **Repeated deaths:** 4,952 eligible player-rounds record lost value greater
  than starting committed value. The experiment deliberately preserves the
  scorer's per-death eligibility and value, including possible resurrection
  cases. It does not observe replacement equipment at each death; restoring a
  magnitude term makes that approximation relevant. Self/environmental deaths
  remain excluded; an independent harm design could revisit them separately.
- The two changes produce more convincing behavior in the selected examples;
  sign frequencies alone cannot prove overall score quality. Review the fixed
  ten player totals and the largest changes before choosing a production rule.

## Verification and reproduction

19 new behavioral cases plus 25 incumbent econ cases pass (44 total). With
the incumbent allocator reintroduced behind the candidate's unchanged API,
15 cases fail on values and four shared behaviors still pass. No ImportError
or TypeError is used as evidence. Source production files are unchanged.

From `webapp/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_econ_counterfactual.py tests/test_econ_component.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe scripts/compare_econ_counterfactual.py
```

Artifacts: [full walkthrough](walkthrough.md), [machine-readable measurements](measurements.json).
