# Review diagnostics

Small scripts written while producing
`../2026-09-02-impact-stages-abc-findings.md` and while working through Sol's
peer review of it. They exist so the mechanisms the report claims can be
re-derived rather than taken on trust. None is part of the site or of the
evaluation tooling, and none writes to the database.

Run them from `webapp/`:

```
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\<script>.py
```

## Standalone (need a live Postgres unless noted)

| script | supports | needs DB |
|---|---|---|
| `why_d_negative.py` | finding 1 — `d` flips negative when leverage columns enter | yes |
| `within_player_ci_bias.py` | finding 3 — the duplicate-match eligibility defect | **no**, pure synthetic |
| `paired_impact_vs_acs.py` | Part 1 — paired Impact−ACS bootstrap, frozen eligibility | yes |

- **`why_d_negative.py`** fits nested designs on the same frozen T2 target and
  the same rows, changing only the columns, and prints the damage coefficient
  `d` for each. Shows `d` going from `+0.0000817` to `-0.0001573` when a single
  kill-order leverage column is added, against a `damage`/leverage correlation
  of 0.869.
- **`within_player_ci_bias.py`** builds synthetic player-matches with a known
  within-player effect and the real cohort shape (95% single-match players).
  Shows the top-level bootstrap CI excluding its own point estimate, and counts
  the zero-variance rows that duplicate match draws inject.
- **`paired_impact_vs_acs.py`** bootstraps the *difference* between the Impact
  and ACS statistics on identical player-matches, clustered by match, with
  cohort eligibility frozen by distinct match id.

## The econ-specification set (finding 11)

These four answer "does econ_impact's negative sign depend on the control set?".
Run `cache_t2_design.py` **first** — it does the expensive replay once and caches
the T2 design matrix to the scratchpad, so the other three run in seconds.

```
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\cache_t2_design.py       # ~4 min, needs DB
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\econ_sign_specification.py
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\econ_dropone_by_specification.py
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\econ_which_control_flips.py
```

- **`econ_sign_specification.py`** — the headline: `econ_impact` is negative in
  5/5 folds without controls and positive in 0/5 with them, stable across L2.
- **`econ_dropone_by_specification.py`** — every component's coefficient sign and
  out-of-fold drop-one cost under both specifications. Shows the negative sign
  migrating to `time_impact`, and `damage` becoming worth dropping.
- **`econ_which_control_flips.py`** — adds controls one at a time.
  `full_buy_count_diff` is the one that flips econ; `econ_impact` is the only
  component that correlates negatively with the economy controls.

The three cached-design scripts read the `.npz` from this session's scratchpad
path, which is hardcoded. If it has been cleaned up, re-run `cache_t2_design.py`
or edit the path at the top of each file.

---

# Impact measurement scripts (2026-09-04)

The scripts named `measure_*.py` back
[`../2026-09-04-impact-measurements.md`](../2026-09-04-impact-measurements.md).
Every number in that document comes from one of them. They exist so the
measurements can be **re-derived rather than trusted** -- which matters here,
because several earlier claims in this project did not survive being re-run on
the full dataset.

All read-only. All run from `webapp/`:

```
.\.venv\Scripts\python.exe ..\docs\superpowers\diagnostics\<script>.py
```

They honour `DATABASE_URL` if set, otherwise `webapp/.env`. Note the local DB
must be the **synced full dataset** (3,124 matches) -- see
`webapp/RENDER_DEPLOY.md`. Against the older 1,151-match local subset they will
run fine and produce different, wrong numbers.

| script | measurements |
|---|---|
| `_bootstrap.py` | shared match-level bootstrap helper; not runnable alone |
| `measure_proximity_curve_and_overtime.py` | **M1**, **M6** |
| `measure_clock_late_and_killer_loadout.py` | **M2**, **M7**, **M9** |
| `measure_state_and_side_interaction.py` | **M3**, **M4** |
| `measure_interaction_functional_form.py` | **M3**, **M4** (the linear fit and per-side slopes) |
| `measure_scalar_mass_distribution.py` | **M5** |
| `measure_window_composition.py` | **M8** |
| `measure_value_destroyed_is_enemy_wealth.py` | **M10** |
| `measure_denial_destruction_and_path.py` | **M11**, **M12**, **M13** |
| `measure_swing_guard_suppression.py` | **M14** |
| `measure_attacking_side_and_phantoms.py` | **M15**, **M16** |
| `measure_site_participation_reliability.py` | **M17** (split-half) |
| `measure_reliability_variance_decomposition.py` | **M17** (variance decomposition, drift) |
| `measure_rate_vs_share_denominator.py` | **M17** (rate vs share) |
| `measure_agent_role_within_player.py` | **M18** |
| `measure_traded_factor_vs_proximity.py` | **M19**, **M20** |
| `measure_post_plant_time_curve.py` | **M21**, **M22** |
| `measure_post_plant_marginal_value.py` | **M23** |
| `measure_post_plant_duel_leverage.py` | **M24** |
| `measure_post_plant_death_cost.py` | **M25**, **M26** |
| `measure_destruction_adjusted_for_kills.py` | **M12a** |
| `measure_destruction_adjusted_by_round_group.py` | **M12b** |
| `measure_kill_count_econ_threshold.py` | **M12c** |
| `measure_kills_vs_enemy_bank.py` | **M12d** |
| `measure_early_round_carryover.py` | **M27**, **M27e**, **M27f** |
| `measure_unified_wealth_denial.py` | **M28** |
| `measure_leverage_decomposition.py` | **M29** |

The three `M12*` scripts above measure **different axes of the same raw
quantity** and must not be read as replications of each other:
`measure_destruction_adjusted_for_kills.py` (M12a) and
`..._by_round_group.py` (M12b) hold kill count fixed and vary enemy wealth;
`..._econ_threshold.py` (M12c) makes kill count the exposure;
`..._vs_enemy_bank.py` (M12d) conditions on whether the enemy could replace
what was taken. The axes point in opposite directions by round group, which is
why the pooled M12 produced a middling positive that survived neither
adjustment.

The two `M27`/`M28` scripts invert the exposure the `M12*` set uses. `M12*`
varies **enemies killed / credits destroyed**; these vary a team's **own
deaths**, conditioned on that team having **won** the round. They are the same
table read from opposite sides, and the outcome conditioning is what pins the
loss-bonus ladder (`M13`) that makes a raw next-round readout unusable early.
`measure_early_round_carryover.py` prints three parts in one run -- immediate
(`M27`), deferred to N+2 (`M27e`) and conditioned on buy-in (`M27f`) -- so the
round-2 sign reversal and its resolution appear in a single output.

The four `measure_post_plant_*` scripts build the same post-plant state-value
function `V(a, d, t)` and differ only in what they difference out of it, so
their `V` cell counts should agree (1,219 cells at the 60-observation floor).
If they ever disagree, one of them has drifted. Each refuses to run against a
database with fewer than 3,000 matches, because an older 1,151-match subset
produces plausible, different, wrong numbers silently -- which is exactly how
`M15`'s OT counts were wrong for as long as they were.

`measure_swing_guard_suppression.py` is the slow one -- it replays through the
ORM rather than in bulk SQL, which is why M14 is restricted to 900 matches.
`measure_leverage_decomposition.py` is slower still per match, because it
replays each one **twice**, which is why M29 is capped at 400. Both caps are
compute bounds, not selections.

`measure_leverage_decomposition.py` gets the raw `kill_order_bonus` without
reimplementing any scoring logic: it replays a second time with
`impact._time_factor` pinned to `1.0`, so that run's `time_impact` reduces to
the kill-order bonus exactly, computed by the real scorer. Prefer this trick to
copying scorer internals into a diagnostic -- a copy drifts, and this project
has already lost work to numbers computed over a quietly wrong population.

## Known defect in the older scripts above

`cache_t2_design.py`, `econ_dropone_by_specification.py`,
`econ_sign_specification.py` and `econ_which_control_flips.py` contain an
**absolute path into a since-deleted session scratchpad**
(`C:/Users/User/AppData/Local/Temp/claude/.../0b4fe174-.../t2_design.npz`).
They cannot run as committed. The `measure_*.py` scripts deliberately avoid
this: they resolve `_bootstrap` relative to `__file__` and take their data from
the database.
