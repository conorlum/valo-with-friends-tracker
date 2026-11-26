"""scripts/crosshair_report.py predict: the maths on synthetic rows, and the refusal on too few."""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "crosshair_report.py"
_spec = importlib.util.spec_from_file_location("crosshair_report", _PATH)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def _rows(n, matches, slope, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        err = float(rng.uniform(0, 20))
        d = float(rng.uniform(3, 40))
        hp = int(rng.integers(20, 101)) if rng.random() > 0.1 else None
        p = 1 / (1 + math.exp(-(0.3 + slope * err / 10)))
        window = 2.0
        tto = float(rng.uniform(0.1, 1.5)) if rng.random() > 0.2 else None
        out.append({"match_id": i % matches, "err": err, "d_m": d, "hold": bool(rng.random() < 0.5),
                    "opener": str(rng.choice(["peeker", "holder", "both", "unclear"])),
                    "gun_class": str(rng.choice(["rifle", "smg", "sidearm"])), "hp": hp,
                    "enemy_hp": 100, "won": int(rng.random() < p), "tto": tto, "window": window})
    return out


def test_recovers_a_falling_effect():
    res = cr.fit_effect(_rows(3000, 60, -0.4), draws=200)
    assert res["n"] == 3000 and res["n_matches"] == 60
    assert abs(res["err"]["log_or"] - (-0.4)) < 0.15
    lo, hi = res["err"]["ci"]
    assert hi < 0
    assert res["err"]["or"] == pytest.approx(math.exp(res["err"]["log_or"]))
    assert "log_or" in res["tto"] and len(res["tto"]["ci"]) == 2


def test_no_effect_interval_includes_zero():
    res = cr.fit_effect(_rows(3000, 60, 0.0, seed=1), draws=200)
    lo, hi = res["err"]["ci"]
    assert lo < 0 < hi


def test_under_fifty_rows_refuses():
    with pytest.raises(cr.TooFew):
        cr.fit_effect(_rows(49, 10, -0.4), draws=50)


def test_main_exits_2_on_too_few(monkeypatch, capsys):
    class _Db:
        def close(self):
            pass

    monkeypatch.setattr(cr, "_open_session", lambda: _Db())
    monkeypatch.setattr(cr, "load_rows", lambda db: _rows(20, 5, -0.4))
    assert cr.main(["predict", "--draws", "50"]) == 2
    assert "fewer than 50" in capsys.readouterr().err


def test_main_prints_n_and_ratio(monkeypatch, capsys):
    class _Db:
        def close(self):
            pass

    monkeypatch.setattr(cr, "_open_session", lambda: _Db())
    monkeypatch.setattr(cr, "load_rows", lambda db: _rows(600, 20, -0.4))
    assert cr.main(["predict", "--draws", "30"]) == 0
    out = capsys.readouterr().out
    assert "600 events" in out and "20 matches" in out and "per 10 deg" in out


def test_rows_from_events_keeps_ok_won_lost_and_uses_window():
    class E:
        def __init__(self, **k):
            self.__dict__.update(dict(cls="ok", err=4.0, d_m=10.0, hold=True, opener="peeker",
                                      gun_class="rifle", hp=80, enemy_hp=100, outcome="won", match_id=1,
                                      t=10.0, t_end=12.0, on_times=None) | k)

    from app.services import aim_stats
    i = aim_stats._ladder_index(aim_stats.on_threshold(10.0))
    times = [None] * (i + 1)
    times[i] = 0.4
    rows = cr.rows_from_events([E(), E(outcome="none"), E(cls="late"), E(on_times=times)])
    assert len(rows) == 2
    assert rows[0]["tto"] is None and rows[0]["window"] == pytest.approx(2.0)
    assert rows[1]["tto"] == pytest.approx(0.4) and rows[0]["won"] == 1
