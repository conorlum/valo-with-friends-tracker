"""`scripts/critical_util.py ults`: the ult report from the stored build (pure formatting, no database)."""

import re

import pytest

from scripts import critical_util
from tests.critutil import test_ult_values as tuv


@pytest.fixture(scope="module")
def block():
    stored, ctx = tuv.ult_fixture()
    from app.critutil import ult_values

    return ult_values.build(tuv._prepared(stored, ctx), ctx, tuv.MODEL, 4200)


def test_ult_lines_has_each_section(block):
    text = "\n".join(critical_util.ult_lines(block))
    for head in ("Ranking", "Orb chart", "Held ults", "Orb risk"):
        assert head in text
    assert "Jett" in text and "Sage" in text and "Ascent" in text
    assert "not cause and effect" in text


def test_every_signed_figure_has_a_sign_and_pp(block):
    lines = critical_util.ult_lines(block)
    figures = [m for line in lines for m in re.findall(r"[-+−]?\d+\.\d+ pp", line)]
    assert figures
    assert all(f[0] in "+-−" for f in figures)
    jett = next(line for line in lines if line.lstrip().startswith("Jett") and "pp" in line)
    assert re.search(r"[+-]\d+\.\d pp", jett)
    assert "[" in jett                      # the CI


def test_an_agent_under_five_uses_has_dashes_not_figures(block):
    lines = critical_util.ult_lines(block)
    sage = next(line for line in lines if line.lstrip().startswith("Sage"))
    assert "—" in sage and "pp" not in sage


def test_no_player_ids_are_printed(block):
    text = "\n".join(critical_util.ult_lines(block))
    for pid in ("100", "101", "200", "201"):
        assert not re.search(rf"\b{pid}\b", text)


def test_lifts_print_as_percentage_points():
    block = {"ranking": {"rows": [{"agent": "Jett", "n_uses": 9, "n_charges": 9, "n_uncompared": 0, "wasted": 1,
                                   "value": 0.031, "lo": 0.01, "hi": 0.05, "diff": -0.004}], "cast_mean": 0.035},
             "charges": [], "orbs": {"max_cost": 0, "rows": []},
             "held": {"rounds": 0, "matches": 0, "any": {"pp": None, "lo": None, "hi": None},
                      "used": {"pp": None, "lo": None, "hi": None}, "unused": {"pp": None, "lo": None, "hi": None}},
             "risk": {"overall": {"tries": 0, "deaths": 0, "cost_pp": None, "lo": None, "hi": None}, "maps": []}}
    text = "\n".join(critical_util.ult_lines(block))
    assert "+3.1 pp" in text and "-0.4 pp" in text and "+1.0 pp" in text and "+5.0 pp" in text


def test_demo_mode_refuses_and_touches_no_database(monkeypatch, capsys):
    import app.db

    def boom(*a, **k):
        raise AssertionError("the database was touched")

    monkeypatch.setattr(critical_util.settings, "demo_mode", True)
    monkeypatch.setattr(app.db, "SessionLocal", boom)
    assert critical_util.main(["ults"]) == 2
    assert "demo" in capsys.readouterr().err.lower()


class _Db:
    def close(self):
        pass


@pytest.mark.parametrize("data", [{}, {"rows": []}])
def test_a_build_without_ults_exits_2_with_the_message(monkeypatch, capsys, data):
    import app.db
    from app.services import replay_util

    monkeypatch.setattr(critical_util.settings, "demo_mode", False)
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(replay_util, "_values_row", lambda db: ("k", data))
    assert critical_util.main(["ults"]) == 2
    assert "hasn't run yet" in capsys.readouterr().err


def test_no_stored_build_exits_2(monkeypatch, capsys):
    import app.db
    from app.services import replay_util

    monkeypatch.setattr(critical_util.settings, "demo_mode", False)
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(replay_util, "_values_row", lambda db: None)
    assert critical_util.main(["ults"]) == 2
    assert "hasn't run yet" in capsys.readouterr().err


def test_a_stored_block_prints_and_exits_0(monkeypatch, capsys, block):
    import app.db
    from app.services import replay_util

    monkeypatch.setattr(critical_util.settings, "demo_mode", False)
    monkeypatch.setattr(app.db, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(replay_util, "_values_row", lambda db: ("k", {"ults": block}))
    assert critical_util.main(["ults"]) == 0
    assert "Ranking" in capsys.readouterr().out
