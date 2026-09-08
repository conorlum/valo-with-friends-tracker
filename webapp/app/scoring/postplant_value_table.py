"""Part 4's V(a, d, t) table -- the attacking team's win rate given `a`
attackers and `d` defenders alive at whole second `t` after the plant, round
unresolved.

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("Estimating V without overfitting", "The estimator contract -- fixed so
two implementations agree", "The no-attackers-left state").

This is a pure estimation leaf: DB-read-only extraction plus in-memory table
construction, with no dependency on impact.py's kill loop (see
postplant_factor.py for the differencing and the runtime factor). The
observation rule deliberately mirrors M23
(docs/superpowers/diagnostics/measure_post_plant_marginal_value.py) second for
second, since the spec pins the population to "M23's observation rule".
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text

from app.models.match import Team
from app.scoring.impact import _check_for_resurrection
from app.scoring.plant_window import attacking_team

SPIKE_SECONDS = 45.0

# The two defuse deadlines, from the game's own timings: a defuse takes 7.0s
# with a resume checkpoint at 3.5s, so a fresh defuse cannot start past
# plant+38.0 and a resumed one cannot start past plant+41.5. Bands are
# HALF-OPEN -- [0, 38.0), [38.0, 41.5), [41.5, 45) -- which is what puts whole
# second 41 in the middle band and second 42 in the last (estimator contract,
# item 4; the deadline falls inside a second and this is the convention that
# resolves it rather than leaving it to a rounding accident).
BAND_EDGES: tuple[float, ...] = (0.0, 38.0, 41.5, 45.0)

MIN_OBSERVATIONS = 60  # the floor M23 used
DEFAULT_W = 2          # smoother half-window; predeclared, never selected from results

# Pooling ladder rungs, in the order the spec fixes them. The ladder never
# reorders and never skips a rung, so the level a cell lands on is a
# deterministic function of the counts.
RUNG_EXACT = "exact"
RUNG_D_POOLED = "d_pooled"
RUNG_BAND = "band"
RUNG_UNSUPPORTED = "unsupported"
RUNG_ANALYTIC = "analytic"  # V(a, 0, t) -- pinned, not estimated

# Rung (ii) pools d upward with 3+ together.
_D_POOL_FLOOR = 3


@dataclass(frozen=True)
class PostPlantRoundSecond:
    """One (round, whole second) occupied post-plant and pre-resolution."""

    match_id: int
    round_id: int
    t: int
    attackers_alive: int
    defenders_alive: int
    atk_won: bool


@dataclass(frozen=True)
class ValueLookup:
    """A V lookup and the rung it resolved on.

    `value` is None when unsupported -- deliberately NOT 1.0. The neutral
    FACTOR is 1.0, but 1.0 as a win PROBABILITY is certainty, and feeding
    certainty into a difference produces a large spurious D rather than a
    neutral factor (spec, "The no-attackers-left state", final bullet).
    """

    value: float | None
    rung: str

    @property
    def supported(self) -> bool:
        return self.value is not None


def band_of(t: int) -> int:
    """Index of the half-open deadline band containing whole second `t`."""
    for i in range(len(BAND_EDGES) - 1):
        if BAND_EDGES[i] <= t < BAND_EDGES[i + 1]:
            return i
    return len(BAND_EDGES) - 2  # t at or past the spike timer clamps to the last band


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def extract_postplant_round_seconds(db) -> list[PostPlantRoundSecond]:
    """One row per (round, whole second) occupied post-plant and pre-resolution.

    Resolution is `plant_time + 45`, lowered to `defuse_time` when a defuse
    landed -- M23's timer-and-defuse horizon. It deliberately does NOT stop
    when a side is eliminated: the (0, d, t) cells are real, live states the
    differencing needs, and (a, 0, t) rows are excluded later by the
    calibration population rather than here.
    """
    rounds = {
        r["id"]: dict(r)
        for r in db.execute(text(
            "SELECT id, match_id, round_number, outcome, planted, plant_time, "
            "exploded, defused, defuse_time FROM rounds"
        )).mappings()
    }
    match_players = {
        mp["id"]: dict(mp)
        for mp in db.execute(text("SELECT id, team FROM match_players")).mappings()
    }
    kills_by_round: dict[int, list[dict]] = defaultdict(list)
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round[k["round_id"]].append(dict(k))

    rows: list[PostPlantRoundSecond] = []
    for round_id, round_row in rounds.items():
        outcome = round_row["outcome"] or ""
        if "Surrendered" in outcome or "Time Win" in outcome:
            continue  # surrendered, or a phantom plant (M16)
        if not round_row["planted"] or round_row["plant_time"] is None:
            continue
        winner = _winner_team(outcome)
        if winner is None:
            continue
        attackers = attacking_team(round_row["round_number"])
        if attackers is None:
            continue

        plant_time = round_row["plant_time"]
        resolution = plant_time + SPIKE_SECONDS
        if round_row["defused"] and round_row["defuse_time"] is not None:
            resolution = min(resolution, round_row["defuse_time"])
        atk_won = winner == attackers

        kills = kills_by_round.get(round_id, [])
        a_alive = d_alive = 5
        events: list[tuple[float, int, int]] = []
        for index, kill in enumerate(kills):
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if killer_id not in match_players or victim_id not in match_players:
                continue
            self_kill = killer_id == victim_id
            if not _check_for_resurrection(index, kills):
                # SQLAlchemy's Enum column stores the member NAME ("TEAM_1"),
                # which raw SQL reads back as a plain string -- Team[...] (by
                # name), never Team(...) (by value). Same trap documented in
                # preplant_time_model.extract_preplant_observations.
                killer_team = Team[match_players[killer_id]["team"]]
                victim_team = Team[match_players[victim_id]["team"]]
                victim_is_attacker = (
                    (killer_team == attackers) if self_kill else (victim_team == attackers)
                )
                if victim_is_attacker:
                    a_alive = max(0, a_alive - 1)
                else:
                    d_alive = max(0, d_alive - 1)
            events.append((kill["event_time_seconds"], a_alive, d_alive))

        horizon = int(min(SPIKE_SECONDS, resolution - plant_time))
        a = d = 5
        event_index = 0
        for t in range(0, horizon):
            absolute_t = plant_time + t
            while event_index < len(events) and events[event_index][0] <= absolute_t:
                a, d = events[event_index][1], events[event_index][2]
                event_index += 1
            rows.append(PostPlantRoundSecond(
                match_id=round_row["match_id"], round_id=round_id, t=t,
                attackers_alive=a, defenders_alive=d, atk_won=atk_won,
            ))

    return rows


def _d_key(d: int) -> int:
    return min(d, _D_POOL_FLOOR)


class ValueTable:
    """V(a, d, t) with the spec's pooling ladder and moving-average smoother.

    Support is resolved by the ladder on the target second's OWN counts,
    BEFORE any smoothing -- the 60-observation floor is never applied to a
    summed moving window (three-grids declaration, section 3.2; applying it to
    the window would create a second route to eligibility and make varying W
    change smoothing and support selection at once).
    """

    def __init__(self, exact, d_pooled, band, w):
        self._exact = exact          # (a, d, t)      -> [wins, n]
        self._d_pooled = d_pooled    # (a, d_key, t)  -> [wins, n]
        self._band = band            # (a, d_key, bd) -> [wins, n]
        self._w = w

    def _rung_for(self, a: int, d: int, t: int) -> str:
        if self._exact.get((a, d, t), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_EXACT
        if self._d_pooled.get((a, _d_key(d), t), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_D_POOLED
        if self._band.get((a, _d_key(d), band_of(t)), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_BAND
        return RUNG_UNSUPPORTED

    def _estimate_at(self, a: int, d: int, second: int, rung: str) -> tuple[float, float] | None:
        """(rate, weight) for `second`, evaluated at the TARGET's resolved rung.

        A neighbour contributes its estimate at the target second's pooling
        level, not its own, weighted by its own per-second observation count at
        that level -- so a pooled count is never repeated across the seconds it
        covers, and a moving average always averages one estimand (declaration
        3.2). Seconds with no observations at that level contribute nothing.
        """
        if rung == RUNG_EXACT:
            wins, n = self._exact.get((a, d, second), (0, 0))
            return (wins / n, n) if n else None
        if rung == RUNG_D_POOLED:
            wins, n = self._d_pooled.get((a, _d_key(d), second), (0, 0))
            return (wins / n, n) if n else None
        # RUNG_BAND: the estimate is constant within the neighbour's own band,
        # weighted by that neighbour's per-second count at the pooled level, so
        # smoothing is a no-op except within W seconds of a band boundary.
        _, n = self._d_pooled.get((a, _d_key(d), second), (0, 0))
        if not n:
            return None
        wins_band, n_band = self._band.get((a, _d_key(d), band_of(second)), (0, 0))
        if not n_band:
            return None
        return (wins_band / n_band, n)

    def value(self, a: int, d: int, t: int) -> ValueLookup:
        # Terminal endpoint, analytic and exact: with every defender dead the
        # attacking team has won, whether the round is recorded as a detonation
        # or as an elimination. No estimate, no floor, no smoothing.
        if d == 0:
            return ValueLookup(1.0, RUNG_ANALYTIC)

        rung = self._rung_for(a, d, t)
        if rung == RUNG_UNSUPPORTED:
            return ValueLookup(None, RUNG_UNSUPPORTED)

        total_weight = 0.0
        total = 0.0
        for second in range(t - self._w, t + self._w + 1):
            if second < 0 or second >= int(SPIKE_SECONDS):
                continue  # one-sided at the ends; never widened to compensate
            estimate = self._estimate_at(a, d, second, rung)
            if estimate is None:
                continue  # weight zero
            rate, weight = estimate
            total += rate * weight
            total_weight += weight

        if not total_weight:
            # The ladder runs FIRST and fixes support; the smoother may only
            # adjust the value, never withdraw support (declaration 3.2). A
            # band-rung cell reaches here exactly when the target second has no
            # observations of its own -- which is why it fell to that rung --
            # so fall back to its unsmoothed estimate at the resolved rung.
            return ValueLookup(self._unsmoothed(a, d, t, rung), rung)
        return ValueLookup(total / total_weight, rung)

    def _unsmoothed(self, a: int, d: int, t: int, rung: str) -> float:
        if rung == RUNG_EXACT:
            wins, n = self._exact[(a, d, t)]
        elif rung == RUNG_D_POOLED:
            wins, n = self._d_pooled[(a, _d_key(d), t)]
        else:
            wins, n = self._band[(a, _d_key(d), band_of(t))]
        return wins / n


def build_value_table(
    observations: list[PostPlantRoundSecond], w: int = DEFAULT_W,
) -> ValueTable:
    exact: dict[tuple[int, int, int], list] = defaultdict(lambda: [0, 0])
    d_pooled: dict[tuple[int, int, int], list] = defaultdict(lambda: [0, 0])
    band: dict[tuple[int, int, int], list] = defaultdict(lambda: [0, 0])

    for row in observations:
        won = 1 if row.atk_won else 0
        for store, key in (
            (exact, (row.attackers_alive, row.defenders_alive, row.t)),
            (d_pooled, (row.attackers_alive, _d_key(row.defenders_alive), row.t)),
            (band, (row.attackers_alive, _d_key(row.defenders_alive), band_of(row.t))),
        ):
            store[key][0] += won
            store[key][1] += 1

    return ValueTable(
        {k: tuple(v) for k, v in exact.items()},
        {k: tuple(v) for k, v in d_pooled.items()},
        {k: tuple(v) for k, v in band.items()},
        w,
    )
