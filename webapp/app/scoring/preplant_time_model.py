"""Part 3 of docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md:
the pre-plant proximity-to-plant time factor. Pure extraction/fitting leaf --
no coupling to the live impact.py kill loop (see app/scoring/preplant_scalar.py
for the dormant runtime hook).

`dt` throughout this module is `seconds_to_plant` (app.scoring.plant_window):
POSITIVE before the plant, decreasing to 0 at the plant instant. This matches
the spec's own knot values, written as positive numbers (30, 20, 10, 5, 0)."""

from dataclasses import dataclass

from sqlalchemy import text

from app.models.match import Team
from app.scoring.plant_window import attacking_team, effective_plant_time, is_phantom_plant, seconds_to_plant


@dataclass(frozen=True)
class PreplantKillObservation:
    match_id: int
    round_id: int
    dt: float                          # seconds_to_plant; always > 0 (strictly pre-plant)
    adv: int                           # killer_alive - victim_alive at kill time, pre-decrement
    is_attacker: bool                  # was the killer on the attacking side this round
    exact_state: str                   # "{killer_alive}v{victim_alive}"
    round_won_by_killer_team: bool | None  # None if the round's winner is not determinable


class _RoundRow:
    """Minimal shim so plant_window's helpers (which only read .planted,
    .plant_time, .outcome) can run against a plain dict row without an ORM
    round-trip."""

    def __init__(self, planted, plant_time, outcome):
        self.planted = planted
        self.plant_time = plant_time
        self.outcome = outcome


def _winner_team(outcome: str | None) -> Team | None:
    if not outcome:
        return None
    if outcome.startswith("Team A"):
        return Team.TEAM_1
    if outcome.startswith("Team B"):
        return Team.TEAM_2
    return None


def extract_preplant_observations(db) -> list[PreplantKillObservation]:
    """One row per non-self pre-plant kill in a non-phantom, non-surrendered
    planted round. Deaths are NOT extracted separately -- they reuse the same
    fitted scalar at scoring time (spec, "Deaths": ship symmetric)."""
    rounds = {
        r["id"]: dict(r)
        for r in db.execute(text(
            "SELECT id, match_id, round_number, outcome, planted, plant_time FROM rounds"
        )).mappings()
    }
    match_players = {
        mp["id"]: dict(mp)
        for mp in db.execute(text("SELECT id, match_id, team FROM match_players")).mappings()
    }

    kills_by_round: dict[int, list[dict]] = {}
    for k in db.execute(text(
        "SELECT round_id, killer_match_player_id, death_match_player_id, event_time_seconds "
        "FROM kill_events ORDER BY round_id, event_time_seconds, id"
    )).mappings():
        kills_by_round.setdefault(k["round_id"], []).append(dict(k))

    observations: list[PreplantKillObservation] = []
    for round_id, kills in kills_by_round.items():
        r = rounds.get(round_id)
        if r is None:
            continue
        if r["outcome"] and "Surrendered" in r["outcome"]:
            continue

        round_row = _RoundRow(r["planted"], r["plant_time"], r["outcome"])
        if is_phantom_plant(round_row):
            continue
        plant_time = effective_plant_time(round_row)
        if plant_time is None:
            continue  # never-planted (or phantom, already excluded above)

        winner = _winner_team(r["outcome"])
        atk = attacking_team(r["round_number"])
        alive = {Team.TEAM_1: 5, Team.TEAM_2: 5}

        for kill in kills:
            killer_id = kill["killer_match_player_id"]
            victim_id = kill["death_match_player_id"]
            if not killer_id or not victim_id or killer_id not in match_players or victim_id not in match_players:
                continue
            # SQLAlchemy's Enum column stores the member NAME ("TEAM_1"), not
            # its value ("team-1"), and raw SQL reads that name back as a
            # plain string on both sqlite and Postgres -- Team[...] (by name)
            # is the correct reconstruction, not Team(...) (by value).
            killer_team = Team[match_players[killer_id]["team"]]
            victim_team = Team[match_players[victim_id]["team"]]
            self_kill = killer_team == victim_team
            kill_time = kill["event_time_seconds"]
            dt = seconds_to_plant(round_row, kill_time)

            if not self_kill and dt is not None and dt > 0:
                killer_alive = alive[killer_team]
                victim_alive = alive[victim_team]
                observations.append(PreplantKillObservation(
                    match_id=r["match_id"],
                    round_id=round_id,
                    dt=dt,
                    adv=killer_alive - victim_alive,
                    is_attacker=(atk == killer_team),
                    exact_state=f"{killer_alive}v{victim_alive}",
                    round_won_by_killer_team=(winner == killer_team) if winner is not None else None,
                ))

            if not self_kill and alive[victim_team] > 0:
                alive[victim_team] -= 1

    return observations


# -- The fixed-knot proximity shape (spec, "Shape knots") -------------------
#
# Knots at dt = 30, 20, 10, 5, 0 (dt is seconds_to_plant: POSITIVE before the
# plant). shape(30) is pinned to 0 (the far-end anchor, no free parameter).
# shape(20) = theta1 and shape(10) = theta2 are free, fitted parameters
# (Task 3). The plateau below 10s is IMPOSED, not fitted: shape(5) and
# shape(0) both equal theta2 exactly, rather than carrying their own
# parameters -- M1 shows the near-plant buckets dip below the -10..-5 bucket
# in every even state, and letting the shape fit that dip would misrepresent
# sampling noise as a real late-arriving decline.
SHAPE_KNOTS = (30.0, 20.0, 10.0, 5.0, 0.0)


def shape_basis(dt: float) -> tuple[float, float]:
    """Weights (w1, w2) on the two free shape parameters theta1 = shape(20),
    theta2 = shape(10) == shape(5) == shape(0) (the imposed plateau), such
    that shape(dt) = w1*theta1 + w2*theta2 for any fitted (theta1, theta2)."""
    if dt >= 30.0:
        return (0.0, 0.0)
    if dt >= 20.0:
        frac = (30.0 - dt) / 10.0
        return (frac, 0.0)
    if dt >= 10.0:
        frac = (20.0 - dt) / 10.0
        return (1.0 - frac, frac)
    return (0.0, 1.0)  # plateau: 0 <= dt < 10 (and anything nearer the plant)
