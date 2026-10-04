# P4 review of IMPL-PLAN.md (fresh subagent), and what was done with each finding

1. **Blocker: the KJ rule as scripted misclassified real devices** (Lotus 53774 and 49190 attacks read as
   off; Lotus 8394's off at +43.9 s and 49190's off at +8.4 s missed). Fixed: verified the reviewer's signal
   on the dumps (tmp_review_offcont.py: every turret off on both maps starts with the spawn-container effect
   stopping in the same ms as a new continuous effect; attacks never stop it; reactivations are the same
   moment with the spawn/boot containers replaying). The plan now uses that for turrets, keeps the boot rule
   (0 ms, one-shot test) for alarmbots, tracks the latest play in the container as "watching", and W3's
   real check prints the full 80-device table, checked by eye against the dumps. -> D1.
2. **Should-fix: GUID reuse.** Fixed: device events are kept only for KJ device archetypes and filtered to
   the actor's own life (spawn to close).
3. **Should-fix: `device_off_spans` needs start/end to clip.** Fixed: the pure rule returns ms spans; the
   row conversion clips with the round window like `wall_on`.
4. **Should-fix: W3's real check can't get round time from read_raw.** Fixed: it asserts replay ms
   (188976) and Lotus 32224's two spans in ms; round time is checked in W5 from the condensed blob.
5. **Should-fix: W5 can't print watcher cells from compute_task.** Fixed: W5 builds `RoundInputs` and
   calls `Tick._watch` for KJ's slot at 21.0 and 22.0 s, printing KJ's alive state too; check
   `check_manifest` passes for the local CliReader before relying on `condense_export_dir`.
6. **Should-fix: W3's pytest misses test_control_store.py:416 and test_replay_condense.py.** Fixed: added
   to W3's check.
7. Nit: only `_watch` sees turrets/alarmbots; off edges clipped to [t0, t1) before joining events. Applied.
8. Nit: extras.py docstring row keys gain `["off"]`; the viewer still draws an off turret as active, noted
   in CHECKLIST (out of scope). Applied.
9. Nit: W9 `playRound` must set playing in the promise's `.then`; the test fires the strip button's
   listener via a stubbed `document`, so the old JS fails on the assertion, not a TypeError. Applied.
10. Nit: no conflicts with hard rules; budgets fine; util passthrough fine. No action.

No open blockers.
