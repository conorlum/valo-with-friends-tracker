# Design review - replay starting barriers and player state

Date: October 6, 2026. Method: self-review of the draft against the current checkout and local pinned parser source. No separate reviewer agent was used. This is document/code inspection, not replay reproduction or a test run.

Reviewed: [design specification](2026-10-06-replay-player-state-design.md).

| Finding | Evidence and required correction | Disposition |
| --- | --- | --- |
| D1 - last living player can still receive control | `compute_round` uses Q63: for the last holder it assigns every team-owned cell to their control, without relying on counterfactual difference. Zeroing body vision or even fixing Memory cannot satisfy zero personal control. Distinguish living bodies, eligible personal contributors and independent utility; explicitly test the sole-survivor branch and preserve alive duration. | Incorporated into control invariants and the implementation gate. |
| D2 - boundary timing can disagree | `RoundInputs` snaps intervals, whereas `statusesAt` currently includes the end instant. A status could end in the viewer while cached control stays disabled, or control might apply a blind before its hit. Require one normalization policy and end-exclusive intervals; exact analytical boundaries and causal serialization must preserve cadence and duration accounting. | Incorporated; reconcile the October 5 timing work rather than duplicating it. |
| D3 - sparse state can invent health/spike | Descriptors declare fields but do not prove real, populated export updates. Missing ownership and omitted properties cannot be numeric zero, full HP or indefinite possession. A health drop can be decay; current equipped item is not inventory membership. Explicit validity and cause evidence are necessary. | Incorporated into the data contract/evidence gate. |
| D4 - labels collide after adding health | The existing renderer moves only names to avoid collisions. Drawing a bar independently at a fixed name offset can separate it from its owner or collide with the moved name. Arrange the health/name block as one layout unit and reserve status/spike bounds. | Incorporated into layout acceptance. |
| D5 - planted spike toggle inconsistency | Existing planted drawing is inside the abilities pass. New carrier/drop markers independent of that pass would make a spike vanish precisely on plant when abilities are hidden, or duplicate it if both paths render. Introduce one lifecycle drawing owner and keep the existing post-plant HUD logic. | Incorporated into spike rendering contract. |
| D6 - optional health answer should not look settled | The user requested total health percentage but has not settled HP versus HP/shields. A proposed HP-only default is reasonable for planning, but fixtures and release notes must identify it as the assumption rather than an agreed answer. | Kept explicit; revise if the pending chat answer arrives. |

Review outcome: the revised design covers all five requests and the identified architectural paths. No unresolved document defect prevents writing the implementation plan. Evidence gates remain: identify the screenshot match/player; verify real vitals, spike lifecycle and damaging-molly activity/footprint records; validate the proposed independent-device attribution; settle or explicitly retain the proposed HP-only default. None is represented as already proven.

## Added damaging-molly rule - review addendum

Reviewed the added fifth request against `RoundInputs.damage_zones`, `DAMAGE_ZONES`, `Unknown.apply` and `Tick.unknown_without/_flood`.

| Finding | Required correction | Disposition |
| --- | --- | --- |
| D7 - observer and mover teams are opposite | `unknown[A]` tracks B players: A-owned damaging mollies are hostile to that movement. Comparing ownership to the observer as if it were the mover reverses the rule. | Section 5 supplies explicit mirrored examples and round-side/owner checks. |
| D8 - damage zones include non-mollies | `DAMAGE_ZONES` includes explosions and strikes as well as sustained ground mollies. Object lifetime also need not equal active damage time. | Use a verified sustained-molly allow-list and damaging intervals; projectiles, dormant phases and non-damaging effects do not block. |
| D9 - activation is not evidence that a space is empty | Clearing all reached cells under a molly erases possible enemies and can remove uncertainty already beyond it. Reopening from an old reached time can credit movement during the blockade. | Separate occupancy from traversal; preserve prior uncertainty and reset newly traversable route timing at actual expiry. |
| D10 - alternate consumers can bypass a blocker | Current counterfactual flood is separate from chronological propagation. A global sight/sealed mask loses team ownership, and cached consumers can lack dynamic state. | Share team-specific traversal semantics across movement consumers, counterfactuals and cached/live gap replay; leave sight and information-only geometric inference unchanged. |

Addendum outcome: corrections are incorporated into section 5 and the implementation-plan acceptance gates. This was a document/code review only; no new rule has been implemented or tested at runtime.
