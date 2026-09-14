"""Part 4's post-plant factor: difference two V cells, divide by that cell's
own kill-weighted time-average, clamp.

    D(a, d, t, victim=defender) = V(a, d-1, t) - V(a, d, t)
    D(a, d, t, victim=attacker) = V(a, d, t)   - V(a-1, d, t)

    post_plant_factor = clamp(D / mean_over_t D, FLOOR, CEIL)

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4. Clamp values: docs/superpowers/2026-09-07-three-grids-declaration-draft.md
section 2.

A ratio, deliberately: D is a state-transition value and so is
kill_order_bonus, so scoring D directly would multiply two measures of the same
thing and reintroduce the collinearity this redesign exists to escape.
Dividing by the state's own time-average leaves only the TIME SHAPE and leaves
the state level where it already lives -- which is also why the (a, d) and
victim-side LEVELS divide out and are modelled by nothing (spec, "Known
limitation", recorded rather than fixed).

Normalisation is within (state, victim_side), never across sides: M24's shapes
differ by state with reversing sign (2v1 falls 1.33 -> 0.10 while 1v2 rises
0.89 -> 1.09), so one shared shape cannot represent them.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text

from app.models.match import Team
from app.scoring.impact import (
    _alive_before_each_kill, _kill_order_bonus, _present_players, _round_stats_for_presence,
    _scoreable_kills, _traded_factor,
)
from app.scoring.plant_window import attacking_team
from app.scoring.postplant_value_table import SPIKE_SECONDS, ValueTable, _winner_team

# Policy parameters, not estimates -- on the same footing as Part 3's k. The
# standing constraint that no kill is ever worth negative Impact requires
# FLOOR > 0. Sensitivity grid (declaration 2.1): FLOOR in {0.02, 0.05, 0.1} x
# CEIL in {1.5, 2.0, 2.5}.
FLOOR_DEFAULT = 0.05
CEIL_DEFAULT = 2.0

# The whole (a, d, victim_side) cell is unsupported below this many eligible
# seconds (estimator contract, item 5).
MIN_ELIGIBLE_SECONDS = 5


@dataclass(frozen=True)
class PostPlantKill:
    """A scored post-plant, pre-resolution kill, with the state BEFORE it."""

    match_id: int
    round_id: int
    t: int                      # whole seconds since the plant, floor(t)
    attackers_alive: int
    defenders_alive: int
    victim_is_attacker: bool
    # impact.py's own graph weight for this transition, carried so the centring
    # gate can be solved on CONTRIBUTION (K * factor) rather than on the factor
    # alone -- the factor and kill_order_bonus are demonstrably not independent,
    # since both key on the same alive counts.
    kill_order_bonus: float = 1.0
    # impact.py's _traded_factor for this kill, carried so the death-side
    # residual is computed on the weight deaths actually score with (K*T)
    # rather than at a placeholder T=1.
    traded_factor: float = 1.0
    # EXACT seconds since the plant. `t` above is the TABLE INDEX -- V,
    # support and the denominator buckets are defined on whole seconds and
    # stay that way, which is declared policy. But the LEGACY baseline the
    # centring equation preserves against is `1 + (kill_time - plant_time)/53`,
    # which runtime evaluates at the REAL timestamp. Solving that baseline at
    # floor(t) understates it: 99.9% of scored kills carry a fractional offset
    # (mean 0.496s), the summed baseline comes out 0.725% low, and c is biased
    # down by about the same. Defaulted to None so existing constructions keep
    # working; `exact_seconds` below falls back to `t` when it is absent.
    t_exact: float | None = None

    @property
    def exact_seconds(self) -> float:
        """Seconds since the plant for LEGACY-factor evaluation. Never use
        this as a table index: `t` is the index, and the split is the whole
        point of the field."""
        return float(self.t) if self.t_exact is None else self.t_exact


def extract_postplant_kills(db) -> list[PostPlantKill]:
    """Non-self post-plant kills in non-phantom, non-surrendered planted
    rounds, before the round resolves. These are the events the factor
    scores, and their per-second counts are the mean_over_t weights.
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
    # Built once for _traded_factor's team check, not per kill.
    team_of = {mp_id: Team[mp["team"]] for mp_id, mp in match_players.items()}
    stats_by_round = _round_stats_for_presence(db)
    kills_by_round: dict[int, list[dict]] = defaultdict(list)
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds, weapon "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round[k["round_id"]].append(dict(k))

    out: list[PostPlantKill] = []
    for round_id, round_row in rounds.items():
        outcome = round_row["outcome"] or ""
        if "Surrendered" in outcome or "Time Win" in outcome:
            continue
        if not round_row["planted"] or round_row["plant_time"] is None:
            continue
        if _winner_team(outcome) is None:
            continue
        attackers = attacking_team(round_row["round_number"])
        if attackers is None:
            continue

        plant_time = round_row["plant_time"]
        resolution = plant_time + SPIKE_SECONDS
        if round_row["defused"] and round_row["defuse_time"] is not None:
            resolution = min(resolution, round_row["defuse_time"])

        # The scorer's own alive counts (impact._alive_before_each_kill), so
        # the factor is fitted on exactly the states scoring uses.
        kills = _scoreable_kills(kills_by_round.get(round_id, []), team_of)
        alive_before = _alive_before_each_kill(
            kills, team_of, _present_players(stats_by_round.get(round_id, {}), kills, team_of))
        defenders = Team.TEAM_2 if attackers == Team.TEAM_1 else Team.TEAM_1
        for index, kill in enumerate(kills):
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if killer_id is None:
                continue  # environmental: in the alive counts, not a kill
            killer_team = team_of[killer_id]
            victim_team = team_of[victim_id]
            self_kill = killer_id == victim_id
            kill_time = kill["event_time_seconds"]
            a_before = alive_before[index][attackers]
            d_before = alive_before[index][defenders]

            if self_kill or killer_team == victim_team:
                continue
            if not (plant_time <= kill_time < resolution):
                continue
            # impact.py's index names are inverted relative to the teams they
            # count: team1_kill_index holds TEAM_2's alive count and vice versa.
            if attackers == Team.TEAM_1:
                team1_index, team2_index = d_before, a_before
            else:
                team1_index, team2_index = a_before, d_before
            out.append(PostPlantKill(
                match_id=round_row["match_id"], round_id=round_id,
                t=int(kill_time - plant_time),
                # The table index is floored above; the legacy baseline needs
                # the real offset (Astra review, claim 3).
                t_exact=float(kill_time - plant_time),
                attackers_alive=a_before, defenders_alive=d_before,
                victim_is_attacker=(victim_team == attackers),
                kill_order_bonus=_kill_order_bonus(
                    team1_index, team2_index, killer_team, False
                ),
                traded_factor=_traded_factor(
                    kills, kill, False,
                    team_of=team_of,
                ),
            ))

    return out


def difference(
    value_table: ValueTable, a: int, d: int, t: int, victim_is_attacker: bool,
) -> float | None:
    """D -- what the victim's team lost. None when either endpoint of the
    difference is unsupported: support is required at BOTH V cells, not just
    the kill's own, and the states this spec cares most about (the last kill
    of a round) are exactly the ones whose second endpoint is thin.
    """
    before = value_table.value(a, d, t)
    after = (
        value_table.value(a - 1, d, t) if victim_is_attacker
        else value_table.value(a, d - 1, t)
    )
    if not (before.supported and after.supported):
        return None
    if victim_is_attacker:
        return before.value - after.value
    return after.value - before.value


class PostPlantFactorTable:
    def __init__(self, denominators, value_table, floor, ceil, diagnostics,
                 centering: float = 1.0):
        self._denominators = denominators  # (a, d, victim_is_attacker) -> mean D
        self._value_table = value_table
        self._floor = floor
        self._ceil = ceil
        self.diagnostics = diagnostics
        # Part 4's centring constant c (review finding 3). 1.0 until
        # solve_and_apply_centering runs, so a table built and used without
        # centring is exactly the uncentred table and nothing changes silently.
        self._centering = centering

    @property
    def centering(self) -> float:
        return self._centering

    def set_centering(self, c: float) -> None:
        self._centering = c

    def raw_ratio(self, a: int, d: int, t: int, victim_is_attacker: bool) -> float | None:
        """D / mean_over_t D, BEFORE clamping. Crossing rates are counted on
        this, never on the clamped factor (declaration 2.4's 'bound inert'
        statement is defined on the raw ratio)."""
        denominator = self._denominators.get((a, d, victim_is_attacker))
        if denominator is None:
            return None
        numerator = difference(self._value_table, a, d, t, victim_is_attacker)
        if numerator is None:
            return None
        return numerator / denominator

    def clamped_factor(self, a: int, d: int, t: int, victim_is_attacker: bool) -> float:
        """The raw ratio put through the DECLARED clamp, with c NOT applied.

        This is the `s` the centring equation is solved on -- c is defined as
        the constant that rescales these -- so it has to be reachable
        separately from the scored factor. It is also the only place the clamp
        diagnostics are counted, so scoring a kill increments them exactly
        once whichever entry point is used.
        """
        ratio = self.raw_ratio(a, d, t, victim_is_attacker)
        if ratio is None:
            return 1.0
        if ratio < 0:
            # Still scores at FLOOR; only the counting is new (declaration 2.4).
            self.diagnostics["negative_numerators"] += 1
        if ratio < self._floor:
            self.diagnostics["floor_bindings"] += 1
            return self._floor
        if ratio > self._ceil:
            self.diagnostics["ceiling_bindings"] += 1
            return self._ceil
        return ratio

    def factor(self, a: int, d: int, t: int, victim_is_attacker: bool) -> float:
        """What scoring receives: the clamped ratio scaled by c.

        Three properties the spec fixes, all visible here:
          * a FALLBACK cell returns EXACTLY 1.0 and is never scaled by c --
            centring it would multiply the neutral fallback, contradicting
            both the fallback rule and the centring equation, which holds the
            fallback population out of its denominator;
          * c is applied AFTER the declared clamp, so the clamp bounds the
            RATIO, which is what declaration 2.4's crossing rates are defined
            on;
          * and the product is NOT clamped again -- a second clamp would make
            the centring it just applied partially inert, and the whole point
            of c is that total contribution is preserved exactly.
        """
        if not self.is_supported(a, d, t, victim_is_attacker):
            return 1.0
        return self.clamped_factor(a, d, t, victim_is_attacker) * self._centering

    def is_supported(self, a: int, d: int, t: int, victim_is_attacker: bool) -> bool:
        return self.raw_ratio(a, d, t, victim_is_attacker) is not None


def build_factor_table(
    value_table: ValueTable, kills: list[PostPlantKill],
    floor: float = FLOOR_DEFAULT, ceil: float = CEIL_DEFAULT,
) -> PostPlantFactorTable:
    """Precompute mean_over_t per (a, d, victim_side).

    The average runs over whole seconds in that cell, WEIGHTED BY THE NUMBER
    OF SCORED KILLS observed at each second -- not uniformly over seconds and
    not by live-round occupancy. Three defensible weightings give three
    different denominators, and the spec fixes this one: the denominator's job
    is to leave the factor averaging ~1 over the population it scores, so the
    weighting is that population.
    """
    kill_counts: dict[tuple[int, int, bool], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for kill in kills:
        key = (kill.attackers_alive, kill.defenders_alive, kill.victim_is_attacker)
        kill_counts[key][kill.t] += 1

    diagnostics: dict[str, int] = defaultdict(int)
    denominators: dict[tuple[int, int, bool], float] = {}

    for key, per_second in kill_counts.items():
        a, d, victim_is_attacker = key
        eligible_weight = 0.0
        eligible_total = 0.0
        eligible_seconds = 0
        for second in range(int(SPIKE_SECONDS)):
            numerator = difference(value_table, a, d, second, victim_is_attacker)
            if numerator is None:
                continue  # falls back to 1.0, so it is not scored by the ratio
            eligible_seconds += 1
            weight = per_second.get(second, 0)
            eligible_weight += weight
            eligible_total += weight * numerator

        if eligible_seconds < MIN_ELIGIBLE_SECONDS:
            diagnostics["cells_with_too_few_eligible_seconds"] += 1
            continue
        if eligible_weight <= 0:
            diagnostics["cells_with_no_scored_weight"] += 1
            continue

        mean_d = eligible_total / eligible_weight
        if mean_d <= 0:
            # Never divide: dividing by a non-positive mean flips the factor's
            # sign, which the FLOOR > 0 clamp then hides rather than catches.
            diagnostics["non_positive_denominators"] += 1
            continue
        denominators[key] = mean_d

    diagnostics["supported_cells"] = len(denominators)
    return PostPlantFactorTable(denominators, value_table, floor, ceil, diagnostics)


class _PostPlantRoundShim:
    """What `_time_factor` reads, so today's SHIPPED post-plant factor can be
    evaluated for a kill at t seconds past the plant.

    plant_time is 0.0 and the kill time is t itself: the legacy post-plant
    branches depend on `kill_time - plant_time` only (the plant+38..45 window
    and the 1 + (t - plant)/53 ramp), so the absolute timestamp divides out.
    `exploded`/`defused` are False because extract_postplant_kills already
    restricted its rows to kills strictly before the round resolves, and
    `outcome` is None because it excluded phantom plants -- this shim stands in
    only for rounds that passed both filters.
    """

    plant_time = 0.0
    planted = True
    exploded = False
    defused = False
    defuse_time = None
    outcome = None


_SHIM = _PostPlantRoundShim()


def legacy_postplant_factor(t: float, for_death: bool = False) -> float:
    """Today's shipped `_time_factor` at t seconds past the plant.

    `t` IS A FLOAT AND MUST BE THE EXACT OFFSET, not the table index. The
    legacy ramp is `1 + (kill_time - plant_time) / 53`, evaluated by runtime
    at the real timestamp; passing floor(t) understates it on 99.9% of scored
    kills and biases the centring constant low (Astra review, claim 3). Use
    `PostPlantKill.exact_seconds`, never `.t`.

    `for_death` is not cosmetic and is the whole of review finding 4: in
    plant+38..45 the legacy scorer pays 1.75 on the kill side and 0.5 on the
    death side. A death-side residual measured against the KILL-side ramp is
    wrong by 3.5x exactly where the two diverge.
    """
    from app.scoring.impact import _time_factor  # local: impact imports nothing here

    return _time_factor(_SHIM, float(t), for_death=for_death)


def solve_and_apply_centering(table: PostPlantFactorTable, kills: list[PostPlantKill]):
    """Solve Part 4's centring constant on `kills` and attach it to `table`.

    Returns the full PostPlantCenteringResult so the caller can report c, its
    supported/fallback split and the death-side residual. `table` is mutated:
    every subsequent `factor()` call on it is centred.

    LEAKAGE. `kills` must be the population the caller is entitled to fit on.
    In the five-arm report that is the outer fold's TRAINING matches only --
    solving c once on the whole corpus and then scoring held-out folds with it
    would put a whole-corpus quantity back into every arm, which is exactly
    the leak the per-fold tables were built to remove.
    """
    from app.scoring.postplant_centering import solve_postplant_centering

    bonuses, kill_ramp, death_ramp, new, supported, traded = [], [], [], [], [], []
    for kill in kills:
        key = (kill.attackers_alive, kill.defenders_alive, kill.t, kill.victim_is_attacker)
        bonuses.append(kill.kill_order_bonus)
        # EXACT seconds for the legacy baselines (what runtime actually pays),
        # the floored `t` inside `key` above for the table lookups (declared
        # policy). Mixing them is Astra review claim 3.
        kill_ramp.append(legacy_postplant_factor(kill.exact_seconds, for_death=False))
        death_ramp.append(legacy_postplant_factor(kill.exact_seconds, for_death=True))
        supported.append(table.is_supported(*key))
        # The UNCENTRED clamped factor: c is by definition the constant that
        # rescales these, so solving against already-centred values would be
        # circular.
        new.append(table.clamped_factor(*key))
        traded.append(kill.traded_factor)

    result = solve_postplant_centering(
        kill_order_bonuses=bonuses,
        ramp_factors=kill_ramp,
        new_factors=new,
        supported=supported,
        traded_factors=traded,
        death_ramp_factors=death_ramp,
    )
    table.set_centering(result.c)
    return result
