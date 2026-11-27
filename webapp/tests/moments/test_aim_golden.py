"""Aim events on the three real Summit rounds of test_golden.py, through the real engine and geometry."""

import pytest

from app.control import engine, geometry as G
from app.moments import aim, fights
from tests.moments.test_golden import _load


@pytest.fixture(scope="module")
def rounds():
    raw = _load()
    try:
        geo = G.load_geometry("Summit")
    except (FileNotFoundError, KeyError):
        pytest.skip("Summit geometry asset is absent")
    G.visibility(geo)
    out = {}
    for rn, r in raw.items():
        link = r["link"]
        rnd = engine.RoundInputs(r["blob"], geo, engine.ControlLink(
            sides={int(k): v for k, v in link["sides"].items()},
            db_deaths=tuple(tuple(x) for x in link["db_deaths"] or ())))
        fl = fights.round_moments(rnd, r["blob"])["fights"]
        out[int(rn)] = (fl, aim.round_aim(rnd, r["blob"], fl))
    return out


def test_every_error_is_an_angle(rounds):
    for fl, events in rounds.values():
        assert events
        for e in events:
            for key in ("err", "err_125", "err_250"):
                assert e[key] is None or 0 <= e[key] <= 180


def test_every_event_names_a_real_fight_opponent(rounds):
    for fl, events in rounds.values():
        opponents = {(f["slot"], f["i"]): set(f["opponents"]) for f in fl}
        for e in events:
            assert e["opponent"] in opponents[(e["slot"], e["i"])]


def test_every_round_has_an_ok_event(rounds):
    for rn, (fl, events) in rounds.items():
        assert any(e["cls"] == "ok" for e in events), rn
