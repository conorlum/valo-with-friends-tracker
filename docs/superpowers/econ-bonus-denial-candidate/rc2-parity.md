# rc2 parity demonstration (ledger check 3)

Editing hashed scoring files (`econ_buy_disruption.py`, `impact.py`, `impact_manifest.py`) makes the rc2
manifest fail verification against this checkout. That is correct, and verification was not weakened.
Instead, the unchanged 30/80 model was re-audited at the new revision.

- A scratch manifest `rc2-parity-scratch-30-80-at-head` was built at scorer revision
  `c7cbfd7f137692e7c453c6367af843109ecda7b0` with `release_comparator = buy_disruption_v2_30_80`
  (LF-SHA-256 `ed59a935416fc61fbb0902f11a4940ad67c8857cb03709ef8f9ac42250c24a0d`). It is never activated
  and is not committed.
- `scripts/release_candidate_review.py --manifest <scratch> --corpus` ran over all 3,124 local matches.
  Reconciliation: all 3 checks pass.
- The resulting `corpus-audit.json` was compared with rc2's committed
  `docs/superpowers/econ-buy-disruption-candidate/corpus-audit.json` on every key except `snapshot`.

**Result: IDENTICAL on all 18 compared keys.**

So the new code's input wiring, weapon loading, abstention plumbing and model registry changed nothing about
how the frozen 30/80 candidate scores the corpus: every distribution, count, identity check, largest
increase and decrease, rank change and leaderboard figure matches rc2 byte-for-value.
