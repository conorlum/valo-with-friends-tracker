"""Optional per-frame feet-only sightings, without changing the sight bool or eager LOS work."""

import numpy as np
import pytest

from app.control import engine as ce
from app.control import heights as hc
from app.control import utility as ut
from app.control.encode import encode_data
from app.replays import control_format as cf
from tests.replays.control_toys import box_hall_tops, height_blob, open_hall, standing, toy_heights
from tests.replays.test_control_sight import Y, low_overhang


def overhang_blob():
    return height_blob({0: standing(196, Y, 0.0), 5: standing(268, Y, 1.5, "B")}, t_end=1.0)


def count_los(monkeypatch):
    calls = []
    original = ce.los

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(ce, "los", counted)
    return calls


def test_feet_only_fan_sightings_are_stored_at_frames_and_sees_is_unchanged(monkeypatch):
    b, geo = overhang_blob(), low_overhang()
    tick = ce.Tick(ce.RoundInputs(b, geo), 0.0)
    assert tick.sees == {0: {5}, 5: set()}
    assert (0, 5) not in tick.direct, "this sighting comes from the fan"
    calls = count_los(monkeypatch)
    assert tick.feet_only() == {(0, 5)}
    assert len(calls) == 2, "blocked head, then clear feet"
    assert all(kwargs.get("record") is None for _, kwargs in calls), "metadata never counts as unresolved sight"
    assert tick.feet_only() == {(0, 5)} and len(calls) == 2, "computed once, lazily"
    assert tick.sees == {0: {5}, 5: set()}

    checked = []
    original = ce.Tick.feet_only

    def at_frame(self):
        checked.append(self.t)
        return original(self)

    monkeypatch.setattr(ce.Tick, "feet_only", at_frame)
    # An extra analytical instant must not request or store feet-only sightings.
    infos = [ut.Info(0.21, "A", 5, "broaden", "omen_unheard", x=268, y=Y, radius_m=5.0)]
    rc = ce.compute_round(b, geo, knowledge=False, infos=infos)
    header, _ = cf.unpack_data(encode_data(rc, b))
    expected = [[n, 0, 5] for n in range(len(rc.ticks))]
    assert rc.feet_only == header["feet_only"] == expected
    assert checked == list(rc.ticks)
    assert len(rc.analytic) > len(rc.ticks)


def test_head_visible_on_open_floor_has_no_optional_header(monkeypatch):
    geo = toy_heights("FeetOnlyOpen", [(192, 192, 288, 216)])
    b = height_blob({0: standing(196, Y, 0.0), 5: standing(268, Y, 0.0, "B")}, t_end=1.0)
    tick = ce.Tick(ce.RoundInputs(b, geo), 0.0)
    calls = count_los(monkeypatch)
    assert tick.sees[0] == {5}
    assert tick.feet_only() == set()
    assert len(calls) == 1, "a clear head short-circuits the feet line"
    rc = ce.compute_round(b, geo, knowledge=False)
    header, _ = cf.unpack_data(encode_data(rc, b))
    assert rc.feet_only is None and "feet_only" not in header
    del rc.feet_only
    assert "feet_only" not in cf.unpack_data(encode_data(rc, b))[0]


def test_flat_map_does_not_check_los_or_write_a_header(monkeypatch):
    b, geo = overhang_blob(), open_hall()
    tick = ce.Tick(ce.RoundInputs(b, geo), 0.0)
    calls = count_los(monkeypatch)
    assert tick.sees[0] == {5}
    assert tick.feet_only() == set() and calls == []
    rc = ce.compute_round(b, geo, knowledge=False)
    header, _ = cf.unpack_data(encode_data(rc, b))
    assert rc.feet_only is None and "feet_only" not in header
    assert calls == []


@pytest.mark.parametrize("path, expected_calls, expected_pairs", [
    ("direct", 2, {(0, 5)}), ("boxed", 1, set()),
])
@pytest.mark.parametrize("jump_sight", [False, True])
def test_exact_sight_los_count_with_legacy_witness(monkeypatch, path, expected_calls, expected_pairs, jump_sight):
    monkeypatch.setattr(ce, "JUMP_SIGHT", jump_sight)
    if path == "direct":
        # Same plate, but the target is lifted above a 0 m floor. The floor fan misses;
        # R4: one blocked eye-to-eye line with jump sight; switched off, the original
        # blocked head and clear feet still need exactly two calls (the legacy witness).
        expected_calls = 1 if jump_sight else 2
        geo = toy_heights("FeetOnlyLifted", [(192, 192, 288, 216)], upper=[((216, 192, 288, 216), 2.6)])
        b = overhang_blob()
    else:
        # One east-facing viewer, one enemy on the box facing away. The target's
        # head line is clear over its own box: exactly one LOS call, no feet call.
        geo = box_hall_tops()
        b = height_blob({0: standing(228, 196, 0.0), 5: standing(260, 196, 1.0, "B")}, t_end=1.0)
    monkeypatch.setattr(ce, "cast", lambda geo, *args, **kwargs: np.zeros(geo.n, bool))
    calls = count_los(monkeypatch)
    tick = ce.Tick(ce.RoundInputs(b, geo), 0.5)  # allow the box stance to settle
    assert len(calls) == expected_calls
    assert ce._on_box(tick.holders[5]) == (path == "boxed")
    seen = not (jump_sight and path == "direct")
    assert ((0, 5) in tick.direct) is seen
    assert tick.sees[0] == ({5} if seen else set())
    assert tick.feet_only() == (expected_pairs if seen else set())
    assert len(calls) == expected_calls, "reuse the exact sight's head result"


@pytest.mark.parametrize("missing", ["eye", "target", "target_nan"])
def test_unresolved_fan_sight_is_not_feet_only(monkeypatch, missing):
    tick = ce.Tick(ce.RoundInputs(overhang_blob(), low_overhang()), 0.0)
    if missing == "eye":
        monkeypatch.setattr(tick, "_eye", lambda *args: None)
    else:
        monkeypatch.setattr(tick.rnd, "height", lambda *args: None if missing == "target" else float("nan"))
        # Only this tick's geometry copy is altered; the shared toy stays intact.
        import copy

        tick.geo = copy.copy(tick.geo)
        tick.geo.node_z = tick.geo.node_z.copy()
        tick.geo.node_z[tick.holders[5].cell] = np.nan
    calls = count_los(monkeypatch)
    assert tick.sees[0] == {5}
    assert tick.feet_only() == set() and calls == []


def test_fan_pair_requires_a_clear_feet_line_and_uses_target_height(monkeypatch):
    tick = ce.Tick(ce.RoundInputs(overhang_blob(), low_overhang()), 0.0)
    calls = []

    def blocked(geo, eye, target, smokes, **kwargs):
        calls.append((eye, target, smokes, kwargs))
        return False

    monkeypatch.setattr(ce, "los", blocked)
    assert tick.feet_only() == set()
    assert [target[2] for _, target, _, _ in calls] == [1.5 + hc.HEAD_M, 1.5 + hc.FEET_M]
    assert all(smokes is tick.smokes and kwargs.get("record") is None
               for _, _, smokes, kwargs in calls)
    assert tick.sees[0] == {5}, "recording does not revise the fan's bool"
