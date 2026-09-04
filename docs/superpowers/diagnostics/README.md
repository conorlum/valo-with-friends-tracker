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

`measure_swing_guard_suppression.py` is the slow one -- it replays through the
ORM rather than in bulk SQL, which is why M14 is restricted to 900 matches.

## Known defect in the older scripts above

`cache_t2_design.py`, `econ_dropone_by_specification.py`,
`econ_sign_specification.py` and `econ_which_control_flips.py` contain an
**absolute path into a since-deleted session scratchpad**
(`C:/Users/User/AppData/Local/Temp/claude/.../0b4fe174-.../t2_design.npz`).
They cannot run as committed. The `measure_*.py` scripts deliberately avoid
this: they resolve `_bootstrap` relative to `__file__` and take their data from
the database.
