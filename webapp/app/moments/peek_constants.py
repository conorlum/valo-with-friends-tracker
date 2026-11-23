"""Thresholds from the peek-types build's PLAN; no runtime dependencies."""

SPEED_WINDOW_S = 0.125  # PLAN R1: trailing window for mean velocity.
SPEED_MAX_PAIR_S = 0.25  # PLAN R1: longest measurable sample pair.
ONSET_STEP_S = 1 / 32  # PLAN R2: sight and onset sampling interval.
ONSET_LOOKBACK_S = 10.0  # PLAN R2: maximum onset search lookback.
OPENER_DELTA_S = 0.25  # PLAN R3: earlier counterfactual position offset.
HELD_MAX_MPS = 1.0  # PLAN R3: maximum speed for a still player.
HELD_LOOK_S = 1.0  # PLAN R3: stillness lookback duration.
JIGGLE_MAX_S = 0.4  # PLAN R3a/R4: maximum jiggle duration.
REPEEK_WINDOW_S = 5.0  # PLAN R3b/R4: earlier exposure window.
EVENT_SLACK_S = 0.1  # PLAN R2: event allowance after a line closes.
REPEEK_SPOT_M = 2.0  # PLAN R4: maximum distance from the earlier peek.
SWING_MPS = 4.5  # PLAN R4: minimum swing speed.
WIDE_M = 1.5  # PLAN R4: minimum sideways travel for a wide swing.
WIDE_LOOK_S = 0.5  # PLAN R4: sideways travel measurement window.
JUMP_LOOK_S = 0.25  # PLAN R4/D14: flight overlap window on either side of onset.
DIST_BANDS_M = (10.0, 25.0)  # PLAN D10: close/mid/long distance boundaries.
SHRINK_K = 10  # PLAN D9: pooled-rate pseudo-openings for shrinkage.
TYPES = ("held", "wide_swing", "slow_peek", "jump_peek", "ability_peek", "jiggle", "re_peek", "other")  # PLAN R4/D14.
OPENERS = ("peeker", "holder", "both", "unclear")  # PLAN R3: line-opening attribution.
