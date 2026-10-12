import base64

from app.coach import control_areas
from app.replays import control_format as cf

# Two walkable cells: cell 0 (top-left, nearest "A Site") and the last cell (bottom-right, nearest "B Site").
TABLE = {"Toy": [{"name": "A Site", "super": "A", "u": 0, "v": 0},
                 {"name": "B Site", "super": "B", "u": 10000, "v": 10000}]}
CELLS = [0, cf.GRID * cf.GRID - 1]


def control_bytes(frames, ticks, knew=None):
    """`frames`: per tick, the two cells' state codes. Every tick stored as a checkpoint (kind 0, 4 bits a cell)."""
    bits = bytearray(cf.GRID * cf.GRID // 8)
    for c in CELLS:
        bits[c >> 3] |= 1 << (7 - (c & 7))
    stream = bytearray()
    for a, b in frames:
        stream += bytes([0, (a << 4) | b])
    header = {"hz": 16, "ticks": ticks, "walk": base64.b64encode(bytes(bits)).decode("ascii"), "cells": 2,
              "group_side": {"A": "attack", "B": "defense"}}       # the link's words (replay_control.round_link)
    if knew is not None:
        header["knew"] = knew
        header["knowledge_events"] = {"A": [[1.0, 5, "seen"]], "B": []}
    return cf.pack_data(header, {"states": bytes(stream), "coverage": b"", "control": b""})


def test_each_area_counts_its_cells_by_owner_per_tick():
    shares = control_areas.area_shares(control_bytes([(1, 4), (7, 0)], [0, 160]), "Toy", TABLE)
    assert shares["t"] == [0.0, 10.0]
    assert shares["cells"] == {"A Site": 1, "B Site": 1}
    assert shares["areas"]["A Site"] == [[1, 0, 0], [0, 0, 1]]
    assert shares["areas"]["B Site"] == [[0, 1, 0], [0, 0, 0]]
    assert shares["group_side"] == {"A": "attack", "B": "defense"} and shares["knew"] is None
    assert shares["signals"] is None


def test_control_over_a_span_is_time_weighted():
    shares = control_areas.area_shares(control_bytes([(1, 4), (4, 4), (4, 4)], [0, 160, 320]), "Toy", TABLE)
    # A Site: side A for 0-10 s, side B for 10-20 s
    assert control_areas.control_over(shares, "A Site", 0.0, 20.0) == {"a": 0.5, "b": 0.5, "contested": 0.0, "none": 0.0}
    assert control_areas.control_over(shares, "A Site", 0.0, 5.0)["a"] == 1.0
    # one moment is the tick in force then: side A at 4 s, side B from the tick at 10 s
    assert control_areas.control_over(shares, "A Site", 4.0, 4.0)["a"] == 1.0
    assert control_areas.control_over(shares, "A Site", 10.0, 10.0)["b"] == 1.0


def test_an_unknown_area_or_empty_span_has_no_answer():
    shares = control_areas.area_shares(control_bytes([(1, 4)], [80]), "Toy", TABLE)   # the only tick is at 5 s
    assert control_areas.control_over(shares, "C Site", 0.0, 10.0) is None
    assert control_areas.control_over(shares, "A Site", 0.0, 2.0) is None
    assert control_areas.control_over(shares, "A Site", 2.0, 2.0) is None      # a moment before the first tick
    # the last tick holds to the end of the span asked about
    assert control_areas.control_over(shares, "A Site", 50.0, 60.0)["a"] == 1.0


def test_stored_sightings_are_carried_through():
    knew = {"A": {"5": [[1.0, 2.0, 100, 100]]}, "B": {}}
    shares = control_areas.area_shares(control_bytes([(0, 0)], [0], knew), "Toy", TABLE)
    assert shares["knew"] == knew and shares["signals"] == {"A": [[1.0, 5, "seen"]], "B": []}
