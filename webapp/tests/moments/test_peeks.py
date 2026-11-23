"""Peek primitives against 125 Hz toy replays and the real sight engine."""

import ast
import sys
from pathlib import Path

import numpy as np
import pytest

from app.control import engine, stance
from app.moments import peek_constants as pc, peeks
from tests.replays import control_toys as toys


def round_of(players, *, geo=None, hz=125, t_end=4.0, util=(), deaths=None):
    data = toys.blob(players, t_end=t_end, util=util, deaths=deaths)
    for slot, (_, points) in players.items():
        data["tracks"][str(slot)] = toys.track(points, 0.0, t_end, hz=hz)
    data["hz"] = hz
    return engine.RoundInputs(data, geo or toys.open_hall(), engine.ControlLink(db_deaths=()))


def standing(x=230, y=200, side="A"):
    return side, [(0.0, x, y, 0)]


def step_in(*, back=True):
    points = [(0.0, 230, 200, 0), (1.992, 230, 200, 0), (2.0, 230, 280, 0)]
    if back:
        points += [(2.292, 230, 280, 0), (2.3, 230, 200, 0)]
    return "A", points


def corner(player=None, enemy=None, **kwargs):
    return round_of({0: player or step_in(), 5: enemy or standing(330, 280, "B")},
                    geo=toys.midwall_hall(), **kwargs)


def test_windowed_speed_still_and_noise():
    assert peeks.windowed_speed(round_of({0: standing()}), 0, 1.0) == 0.0
    px_per_uv = engine.PX / 10000
    points = [(i / 125, 230 + (-1) ** i * px_per_uv, 200, 0) for i in range(126)]
    rnd = round_of({0: ("A", points)}, t_end=1.0)
    assert all(peeks.windowed_speed(rnd, 0, t) < 0.5 for t in np.arange(0.2, 1.0, 0.008))


@pytest.mark.parametrize("hz,tolerance", [(125, 0.1), (16, 0.2)])
def test_windowed_velocity_and_speed_at_both_track_rates(hz, tolerance):
    geo = toys.open_hall()
    travel_px = 5.4 / geo.m_per_px
    rnd = round_of({0: ("A", [(0.0, 150, 200, 0), (2.0, 150 + 2 * travel_px, 200, 0)])}, hz=hz, geo=geo)
    assert peeks.windowed_speed(rnd, 0, 1.0) == pytest.approx(5.4, abs=tolerance)
    vx, vy = peeks.windowed_velocity(rnd, 0, 1.0)
    assert vx == pytest.approx(travel_px, abs=tolerance / geo.m_per_px)
    assert vy == 0.0


@pytest.mark.parametrize("t", [-0.1, 0.0, 0.004, 0.1])
def test_windowed_speed_requires_both_window_ends(t):
    assert peeks.windowed_speed(round_of({0: standing()}), 0, t) is None


def test_windowed_speed_does_not_cross_track_segments():
    data = toys.blob({0: standing()}, t_end=2.0)
    data["hz"] = 125
    data["tracks"]["0"] = toys.track(standing()[1], 0.0, 0.992, hz=125) + toys.track(
        standing()[1], 1.0, 2.0, hz=125)
    rnd = engine.RoundInputs(data, toys.open_hall(), engine.ControlLink(db_deaths=()))
    assert peeks.windowed_speed(rnd, 0, 1.064) is None
    assert peeks.windowed_speed(rnd, 0, 1.5) == 0.0


def test_windowed_speed_rejects_a_long_sample_gap_and_missing_track():
    rnd = round_of({0: standing()})
    rnd.tracks[0] = tuple(values[[0, 125]] for values in rnd.tracks[0])
    rnd._segment[0] = np.zeros(2, dtype=int)
    assert peeks.windowed_speed(rnd, 0, 1.0) is None
    assert peeks.windowed_velocity(rnd, 9, 1.0) is None


def test_max_speed_samples_the_closed_interval_and_ignores_missing(monkeypatch):
    calls = []

    def speed(rnd, slot, t):
        calls.append(t)
        return None if t == 1.0 else t

    monkeypatch.setattr(peeks, "windowed_speed", speed)
    assert peeks.max_speed(None, 0, 1.0, 1.0 + 2 * pc.ONSET_STEP_S) == 1.0 + 2 * pc.ONSET_STEP_S
    assert calls == [1.0, 1.0 + pc.ONSET_STEP_S, 1.0 + 2 * pc.ONSET_STEP_S]
    assert peeks.max_speed(None, 0, 1.0, 1.0) is None


def test_still_requires_measurement_and_the_entire_lookback():
    rnd = round_of({0: ("A", [(0.0, 150, 200, 0), (1.0, 200, 200, 0)])})
    assert not peeks.still(rnd, 0, 0.0)
    assert not peeks.still(rnd, 0, 1.5)
    assert peeks.still(rnd, 0, 2.5)


def test_line_clear_corner_and_passed_earlier_position():
    rnd = corner()
    assert peeks.line_clear(rnd, 0, 5, 1.0) is False
    assert peeks.line_clear(rnd, 0, 5, 2.1) is True
    assert peeks.line_clear(rnd, 0, 5, 2.1, pa=rnd.pos(0, 1.0), ta=1.0) is False
    assert peeks.line_clear(rnd, 0, 5, 1.0, pa=rnd.pos(0, 2.1), ta=2.1) is True
    assert peeks.line_clear(rnd, 0, 9, 1.0) is None


def test_line_clear_uses_each_passed_position_time_for_stance(monkeypatch):
    _, on = toys.on_box_track(260, 196, t_end=1.0)
    _, off = toys.standing(320, 196, 0.0)
    points = [*on, (2.0, *off[0][1:])]
    rnd = round_of({0: ("A", points), 5: standing(380, 196, "B")}, geo=toys.box_hall())
    rnd.track_z[0] = np.interp(rnd.tracks[0][0], [p[0] for p in points], [p[4] for p in points])
    calls = []
    original = stance.boxed_stance

    def record(rnd, slot, t):
        calls.append((slot, t))
        return original(rnd, slot, t)

    monkeypatch.setattr(stance, "boxed_stance", record)
    peeks.line_clear(rnd, 0, 5, 3.0, pa=rnd.pos(0, 0.5), ta=0.5,
                     pb=rnd.pos(5, 1.0), tb=1.0)
    assert calls == [(0, 0.5), (5, 1.0)]


def test_line_clear_passes_stance_eyes_and_see_over_to_los(monkeypatch):
    rnd = round_of({0: standing(), 5: standing(330, 200, "B")})
    st = stance.Stance(kind="on_box", px=(230, 200), eye_px=(232, 202), see_over=(1, 2), anchor=None)
    monkeypatch.setattr(stance, "boxed_stance", lambda rnd, slot, t: st if slot == 0 else None)
    calls = []

    def los(geo, a, b, smokes, *, see_over):
        calls.append((a, b, smokes, see_over))
        return True

    monkeypatch.setattr(engine, "los", los)
    assert peeks.line_clear(rnd, 0, 5, 2.0, pb=(350, 280, 0), tb=1.0)
    assert calls == [((232, 202, None), (350, 280, None), rnd.smokes_at(2.0), ((1, 2), ()))]


def test_exposures_step_in_and_back():
    rnd = corner()
    assert peeks.line_clear(rnd, 0, 5, 1.9) is False
    assert peeks.line_clear(rnd, 0, 5, 2.1) is True
    assert peeks.line_clear(rnd, 0, 5, 2.4) is False
    [exp] = peeks.exposures(rnd, 0, 5, 1.0, 3.0)
    assert exp["on"] == pytest.approx(2.0, abs=pc.ONSET_STEP_S)
    assert exp["off"] == pytest.approx(2.3, abs=pc.ONSET_STEP_S)
    assert exp["at_start"] is False


def test_exposures_clear_from_start_or_never_clear():
    rnd = corner(player=standing(230, 280))
    assert peeks.line_clear(rnd, 0, 5, 1.0) is True
    assert peeks.exposures(rnd, 0, 5, 1.0, 2.0) == [{"on": 1.0, "off": None, "at_start": True}]
    rnd = corner(player=standing())
    assert peeks.line_clear(rnd, 0, 5, 1.0) is False
    assert peeks.exposures(rnd, 0, 5, 1.0, 2.0) == []


def test_exposures_missing_samples_break_runs(monkeypatch):
    answers = iter([True, None, True, False, True])
    monkeypatch.setattr(peeks, "line_clear", lambda *args: next(answers))
    step = pc.ONSET_STEP_S
    assert peeks.exposures(None, 0, 5, 0.0, 4 * step) == [
        {"on": 0.0, "off": step, "at_start": True},
        {"on": 2 * step, "off": 3 * step, "at_start": False},
        {"on": 4 * step, "off": None, "at_start": False},
    ]


@pytest.mark.parametrize("who,label", [("player", "peeker"), ("enemy", "holder"), ("both", "both")])
def test_opener_attributes_the_move_that_opened_the_corner(who, label):
    player = step_in(back=False) if who != "enemy" else standing(230, 280)
    enemy = ("B", [(0.0, 330, 120, 0), (1.992, 330, 120, 0), (2.0, 330, 280, 0)]) \
        if who != "player" else standing(330, 280, "B")
    rnd = corner(player=player, enemy=enemy)
    assert peeks.line_clear(rnd, 0, 5, 1.75) is False
    assert peeks.line_clear(rnd, 0, 5, 2.0) is True
    assert peeks.opener(rnd, 0, 5, 2.0) == label


@pytest.mark.parametrize("moving,label", [(False, "holder"), (True, "unclear")])
def test_opener_when_smoke_ends_and_neither_move_opened_the_line(moving, label):
    player = ("A", [(0.0, 120, 200, 0), (4.0, 200, 200, 0)]) if moving else standing(120, 200)
    smoke = toys.toy_ability("Wraith", "4_Smoke", 260, 200, 5, t=0.0, t1=2.0)
    rnd = round_of({0: player, 5: standing(400, 200, "B")}, util=[smoke])
    assert peeks.line_clear(rnd, 0, 5, 1.75) is False
    assert peeks.line_clear(rnd, 0, 5, 2.0) is True
    assert peeks.line_clear(rnd, 0, 5, 2.0, pa=rnd.pos(0, 1.75), ta=1.75) is True
    assert peeks.opener(rnd, 0, 5, 2.0) == label


def test_opener_missing_earlier_position_is_not_blocked():
    rnd = round_of({0: standing(), 5: standing(330, 200, "B")})
    assert peeks.opener(rnd, 0, 5, 0.0) == "unclear"


def test_jiggle_exposure_player_steps_back_and_staying_is_not_a_jiggle():
    rnd = corner()
    [exp] = peeks.exposures(rnd, 0, 5, 1.0, 3.0)
    assert peeks.closed_by_player(rnd, 0, 5, exp["off"])
    assert peeks.is_jiggle_exposure(rnd, 0, 5, exp)
    rnd = corner(player=step_in(back=False))
    [exp] = peeks.exposures(rnd, 0, 5, 1.0, 3.0)
    assert exp["off"] is None
    assert not peeks.is_jiggle_exposure(rnd, 0, 5, exp)


def test_jiggle_exposure_enemy_closing_the_line_does_not_count():
    enemy = ("B", [(0.0, 330, 280, 0), (2.292, 330, 280, 0), (2.3, 330, 120, 0)])
    rnd = corner(player=step_in(back=False), enemy=enemy)
    assert peeks.line_clear(rnd, 0, 5, 1.9) is False
    assert peeks.line_clear(rnd, 0, 5, 2.1) is True
    assert peeks.line_clear(rnd, 0, 5, 2.4) is False
    [exp] = peeks.exposures(rnd, 0, 5, 1.0, 3.0)
    assert not peeks.closed_by_player(rnd, 0, 5, exp["off"])
    assert not peeks.is_jiggle_exposure(rnd, 0, 5, exp)


@pytest.mark.parametrize("slot", [0, 5])
def test_jiggle_exposure_requires_both_alive_at_close(slot):
    rnd = corner(deaths={slot: 2.25})
    [exp] = peeks.exposures(rnd, 0, 5, 1.0, 3.0)
    assert not peeks.is_jiggle_exposure(rnd, 0, 5, exp)


def test_jiggle_exposure_rejects_a_long_exposure():
    rnd = corner()
    assert not peeks.is_jiggle_exposure(rnd, 0, 5, {"on": 2.0, "off": 2.5, "at_start": False})


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", ["peek_constants", "peeks"])
def test_import_boundaries(module):
    path = Path(__file__).resolve().parents[2] / "app" / "moments" / f"{module}.py"
    names = _imports(path)
    allowed = lambda n: n.split(".")[0] in sys.stdlib_module_names or (  # noqa: E731
        module == "peeks" and (n == "numpy" or n in {"app.control", "app.moments"}
                               or n.startswith(("app.control.", "app.moments."))))
    assert not {n for n in names if not allowed(n)}


def test_plan_constants_are_pinned():
    expected = {
        "SPEED_WINDOW_S": 0.125, "SPEED_MAX_PAIR_S": 0.25, "ONSET_STEP_S": 1 / 32,
        "ONSET_LOOKBACK_S": 10.0, "OPENER_DELTA_S": 0.25, "HELD_MAX_MPS": 1.0, "HELD_LOOK_S": 1.0,
        "JIGGLE_MAX_S": 0.4, "REPEEK_WINDOW_S": 5.0, "EVENT_SLACK_S": 0.1, "REPEEK_SPOT_M": 2.0,
        "SWING_MPS": 4.5, "WIDE_M": 1.5, "WIDE_LOOK_S": 0.5, "AIRBORNE_M": 0.8, "AIRBORNE_LOOK_S": 0.5,
        "DIST_BANDS_M": (10.0, 25.0), "SHRINK_K": 10,
        "TYPES": ("held", "wide_swing", "slow_peek", "jiggle", "re_peek", "other"),
        "OPENERS": ("peeker", "holder", "both", "unclear"),
    }
    assert {name: getattr(pc, name) for name in expected} == expected
