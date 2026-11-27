"""Each ult's own lift window: its active time, plus a per-ult tail from abilities.json `ult_window`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.critutil import abilities, ult_values, windows

PINNED_EXTRACTOR_HASH = "a397dd02ece492660b985e58d1469c6c1295c7a8e1b513e82262c5aaaa5206f5"
WEBAPP_ROOT = Path(__file__).resolve().parents[2]


def _entry(agent, ability):
    return {(e["agent"], e["ability"]): e for e in abilities.load()}[(agent, ability)]


def _fixed(n, window):
    return {"agent": "X", "ability": "Y", "type": "ult", "rule": f"fixed:{n}", "ult_window": window}


def _cast(t, **kw):
    item = {"type": "ult", "t": t, "signals": {}, "effects": []}
    item.update(kw)
    return item


def _active(monkeypatch, segments):
    monkeypatch.setattr(ult_values.util_values, "item_segments", lambda rd, item, typ=None: [list(s) for s in segments])


def test_a_tail_extends_the_last_segment(monkeypatch):
    _active(monkeypatch, [[10.0, 20.0]])
    segs, mode = ult_values.ult_segments({"t_decided": 100.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 25.0]] and mode == "window"


def test_the_tail_is_cut_at_t_decided(monkeypatch):
    _active(monkeypatch, [[10.0, 20.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 22.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 22.0]]


def test_only_the_last_of_two_segments_grows(monkeypatch):
    _active(monkeypatch, [[10.0, 12.0], [20.0, 25.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, _cast(10.0), _fixed(10, 5))
    assert segs == [[10.0, 12.0], [20.0, 30.0]]


def test_brimstone_is_ten_seconds_from_the_start():
    entry = _entry("Brimstone", "Orbital Strike")
    item = _cast(10.0, ability_id="Brimstone/Orbital Strike")
    segs, mode = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[10.0, 20.0]] and mode == "window"
    segs, _ = ult_values.ult_segments({"t_decided": 15.0}, item, entry)
    assert segs == [[10.0, 15.0]]


def test_thrash_ends_at_its_latest_effect_plus_two_then_the_tail():
    entry = _entry("Gekko", "Thrash")
    item = _cast(5.0, ability_id="Gekko/Thrash", signals={"moved": 6.0, "seen_end": 12.0},
                 effects=[{"target": 1, "how": "cocooned", "t0": 8.0, "t1": 15.0, "known": True},
                          {"target": 2, "how": "cocooned", "t0": 9.0, "t1": 18.0, "known": True}])
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[6.0, 25.0]]
    segs, _ = ult_values.ult_segments({"t_decided": 22.0}, item, entry)
    assert segs == [[6.0, 22.0]]


def test_thrash_with_no_effect_runs_to_seen_end_plus_eight():
    entry = _entry("Gekko", "Thrash")
    item = _cast(5.0, ability_id="Gekko/Thrash", signals={"moved": 6.0, "seen_end": 12.0})
    segs, _ = ult_values.ult_segments({"t_decided": 100.0}, item, entry)
    assert segs == [[6.0, 25.0]]
    segs, _ = ult_values.ult_segments({"t_decided": 19.0}, item, entry)
    assert segs == [[6.0, 19.0]]


def test_a_round_ult_runs_to_the_round_end(monkeypatch):
    _active(monkeypatch, [[30.0, 40.0]])
    segs, mode = ult_values.ult_segments({"t_decided": 70.0}, _cast(30.0), _fixed(5, "round"))
    assert segs == [[30.0, 70.0]] and mode == "round"


def test_an_ult_window_is_never_capped_at_45_seconds(monkeypatch):
    _active(monkeypatch, [[10.0, 80.0]])
    segs, _ = ult_values.ult_segments({"t_decided": 200.0}, _cast(10.0), _fixed(5, 5))
    assert segs == [[10.0, 85.0]]


def test_the_window_table():
    by = {(e["agent"], e["ability"]): e for e in abilities.load() if e["type"] == "ult"}
    for key, e in by.items():
        w = e.get("ult_window")
        assert w == "round" or (isinstance(w, str) and re.fullmatch(r"total:\d+(\.\d+)?", w)) or (
            isinstance(w, (int, float)) and not isinstance(w, bool) and w >= 0), (key, w)
    assert not [e["ability"] for e in abilities.load() if e["type"] != "ult" and "ult_window" in e]
    expect = {("Brimstone", "Orbital Strike"): "total:10", ("Killjoy", "Lockdown"): 10, ("Cypher", "Neural Theft"): 10,
              ("Breach", "Rolling Thunder"): 6, ("Omen", "From the Shadows"): 0, ("Deadlock", "Annihilation"): 5,
              ("Sova", "Hunter's Fury"): 5, ("Yoru", "Dimensional Drift"): 5}
    for key in ("Jett", "Blade Storm"), ("Reyna", "Empress"), ("Veto", "Evolution"), ("Neon", "Overdrive"), \
            ("Chamber", "Tour De Force"), ("Sage", "Resurrection"), ("Clove", "Not Dead Yet"):
        expect[key] = "round"
    for key, w in expect.items():
        assert by[key]["ult_window"] == w, key
    others = [k for k in by if k not in expect]
    assert all(by[k]["ult_window"] == 5 for k in others)


def test_the_seed_carries_the_same_windows():
    d = WEBAPP_ROOT / "app" / "critutil"
    table = {(e["agent"], e["ability"]): e.get("ult_window") for e in json.loads((d / "abilities.json").read_text(encoding="utf-8"))["abilities"]}
    seed = {(e["agent"], e["ability"]): e.get("ult_window") for e in json.loads((d / "abilities_seed.json").read_text(encoding="utf-8"))["abilities"]}
    assert table == seed


def test_the_extractor_hash_ignores_ult_window():
    assert abilities.extractor_hash() == PINNED_EXTRACTOR_HASH


def test_a_non_ult_window_is_unchanged():
    entry = {"rule": "fixed:4", "type": "wall"}
    got = windows.window({"type": "wall", "t": 10.0, "signals": {}}, entry, 100.0)
    assert got == {"segments": [[10.0, 14.0]], "rule": "fixed:4", "fallback": False}
