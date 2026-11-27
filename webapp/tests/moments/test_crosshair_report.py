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
    zero = cr.rows_from_events([E(d_m=0.0, on_times=[None] * 13 + [0.6])])
    assert zero[0]["tto"] == pytest.approx(0.6)
    t = cr.times_to_on_target({"d_m": 0.0, "on": [None] * 13 + [0.6]})
    assert t == {"body": pytest.approx(0.6), "five": pytest.approx(0.6), "fixed5": None}
    rows = cr.rows_from_events([E(), E(outcome="none"), E(cls="late"), E(on_times=times)])
    assert len(rows) == 2
    assert rows[0]["tto"] is None and rows[0]["window"] == pytest.approx(2.0)
    assert rows[1]["tto"] == pytest.approx(0.4) and rows[0]["won"] == 1


# ---- events subcommand: selection and formatting on synthetic events (offline)

def _ev(uuid, rnd, cls, i=0, d_m=8.0, turn=0.0, t=10.0, err=3.0, on=None):
    return {"uuid": uuid, "round": rnd, "slot": 1, "opponent": 6, "t": t, "cls": cls, "err": err, "d_m": d_m,
            "opener": "peeker", "on": on if on is not None else [None] * 14, "shot": 0.3, "dmg": 0.4,
            "turn": turn, "i": i}


def test_select_spreads_across_classes_and_replays():
    evs = []
    for u in ("aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3"):
        for r in range(1, 5):
            for k in range(6):
                evs.append(_ev(u, r, "ok", i=k, t=float(k)))
            evs.append(_ev(u, r, "behind"))
            evs.append(_ev(u, r, "turning"))
    sel = cr.select_events(evs, 20)
    assert len(sel) == 20
    classes = {e["cls"] for e in sel}
    assert classes == {"ok", "behind", "turning"}
    assert {e["uuid"] for e in sel} == {"aaaaaaaa-1", "bbbbbbbb-2", "cccccccc-3"}
    assert len({(e["uuid"], e["round"], e["t"], e["cls"], e["i"]) for e in sel}) == 20


def test_select_fewer_than_n_returns_all_and_is_deterministic():
    evs = [_ev("aaaaaaaa-1", 1, "ok", i=k) for k in range(5)]
    assert cr.select_events(evs, 20) == cr.select_events(list(evs), 20)
    assert len(cr.select_events(evs, 20)) == 5


def test_times_to_on_target_by_rule():
    from app.services import aim_stats
    on = [None] * 14
    on[aim_stats._ladder_index(aim_stats.on_threshold(10.0, "body"))] = 0.4
    on[aim_stats._ladder_index(aim_stats.on_threshold(10.0, "five"))] = 0.2
    t = cr.times_to_on_target(_ev("u", 1, "ok", d_m=10.0, on=on))
    assert t == {"body": 0.4, "five": 0.2, "fixed5": 0.2}


def test_deciles_of_values():
    out = cr.deciles(list(range(101)))
    assert out == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    assert cr.deciles([]) == []


def test_band_counts_and_distance_deciles():
    evs = [_ev("u", 1, "ok", d_m=d) for d in (3, 5, 12, 15, 30, 45)]
    c = cr.band_counts(evs)
    assert sum(c.values()) == 6 and set(c) == {"close", "mid", "long"}


def test_format_row_has_link_and_no_names():
    ev = _ev("0a9ab548-718b-41ee-94ce-db25fd430bf8", 3, "ok", t=12.5)
    line = cr.format_row(ev)
    assert "0a9ab548" in line and "/replays/0a9ab548-718b-41ee-94ce-db25fd430bf8?round=3&t=12" in line
    assert " ok " in f" {line} "


def test_report_header_says_fine_false_and_sections():
    evs = [_ev("aaaaaaaa-1", 1, "ok", turn=float(k), d_m=5.0 + k) for k in range(30)]
    text = cr.build_report(evs, 20, fine=False)
    assert "fine: false" in text
    assert text.count("\n") > 20
    assert "turn" in text and "distance" in text.lower() and "close" in text


def test_report_summary_counts_ok_under_10m():
    from app.services import aim_stats
    on = [None] * 14
    on[aim_stats._ladder_index(aim_stats.on_threshold(5.0, "body"))] = 0.5
    evs = [_ev("u", 1, "ok", d_m=5.0, on=on), _ev("u", 1, "ok", d_m=5.0), _ev("u", 1, "behind", d_m=5.0)]
    s = cr.summary_lines(evs)
    assert "ok 2" in s and "behind 1" in s
    assert "1 of 2" in s
