# 30/80 parity at the rc2 revision (declared check 3)

The rc2 code-review fixes edited hashed scoring sources: `econ_buy_disruption.py`, `impact_manifest.py` and
`release_candidate_review.py`, plus the adapter. The unchanged 30/80 model was therefore re-audited at this
revision, rather than weakening verification of the older manifests.

- A scratch manifest `rc2-parity-scratch-30-80-at-head` was built at scorer revision
  `90d9cd12dd9b31fce154a7f6d601ee78b389d3e3`, with `release_comparator = buy_disruption_v2_30_80`
  (LF-SHA-256 `6ed0596400e23a92cf1d5960fcc8a7ea75a1778be683c2bc817bd3a96fcfcdae`). It is never activated and
  is not committed.
- `scripts/release_candidate_review.py --manifest <scratch> --corpus` ran over all 3,124 local matches.
  Reconciliation: all 3 checks pass.
- The resulting `corpus-audit.json` was compared with the 30/80 candidate's committed
  `docs/superpowers/econ-buy-disruption-candidate/corpus-audit.json` on every key except `snapshot`.

**Result: IDENTICAL on all 18 compared keys.**

The survive-loss reward, the loadout-coverage pickup rule, the trace rendering, the comparison counting, the
manifest description and the spend guard changed nothing about how the frozen 30/80 candidate scores the corpus.
