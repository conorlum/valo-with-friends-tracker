"""Map control's stored bytes (Stage 3; app/control/encode.py, app/replays/control_format.py): a real
RoundControl from a toy map round-trips through `data` and `summary`, and the engine's constants
are pinned to CONTROL_REVISION."""

import base64
import dataclasses
import gzip
import hashlib
import json
import re

import numpy as np
import pytest

from app.control import engine as ce
from app.control import geometry as cg
from app.control.encode import checkpoint_ticks, encode_data, encode_summary
from app.replays import control_format as cf
from tests.replays.control_toys import blob, door_hall

# CONTROL_REVISION -> the digest of the engine's and geometry's constants it was released with.
# Changed a constant? Bump CONTROL_REVISION in app/replays/control_format.py (every stored round
# is then stale and recomputed) and add the new revision's digest here.
# 2: space taken, what each team knew (KNEW_*) and remembered ground (D6: DECAY_MPS). Unreleased,
# so re-pinned in place. 3: barriers, backfill and unknown (UNKNOWN_MPS; DECAY_MPS and BARRIER_GRACE_S
# removed), presence (PRESENCE_M), rifle-walk UNKNOWN_MPS, GAP_SEAL_M and DROP_PIECE_CELLS. Unreleased, so
# re-pinned in place.
PINNED = {1: "956a0fb740a1cee8", 2: "8e37c7fd96bbfc68", 3: "2499e07080176dae"}


def _constants_digest() -> str:
    def plain(value):
        if isinstance(value, re.Pattern):
            return value.pattern
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (set, frozenset)):
            return sorted(plain(v) for v in value)
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value

    constants = {}
    for module in (ce, cg):
        for name, value in sorted(vars(module).items()):
            if name.isupper() and not name.startswith("_") and name != "CONTROL_REVISION":
                if isinstance(value, (int, float, str, bool, tuple, list, dict, set, frozenset, np.ndarray)):
                    constants[f"{module.__name__}.{name}"] = plain(value)
    text = json.dumps(constants, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_the_engine_constants_are_pinned_to_the_control_revision():
    digest = _constants_digest()
    assert PINNED.get(cf.CONTROL_REVISION) == digest, (
        f"the engine's or geometry's constants changed (digest {digest}): bump CONTROL_REVISION in "
        f"app/replays/control_format.py and pin {{{cf.CONTROL_REVISION + 1}: '{digest}'}} here")


def test_the_formats_agree_with_the_engine():
    assert ce.CONTROL_REVISION == cf.CONTROL_REVISION
    assert ce.GRID_HZ == cf.GRID_HZ and cg.GRID == cf.GRID
    assert ce.N_STATES == len(cf.STATE_NAMES)
    codes = (ce.NONE, ce.A_PASSIVE, ce.A_SAFE, ce.A_ACTIVE, ce.B_PASSIVE, ce.B_SAFE, ce.B_ACTIVE,
             ce.CONTESTED_ACTIVE, ce.CONTESTED)
    assert list(codes) == list(range(len(cf.STATE_NAMES)))


def still(side, x, y, yaw):
    return side, [(0.0, x, y, yaw)]


@pytest.fixture(scope="module")
def round_control():
    geo = door_hall()
    players = {0: still("A", 150, 200, 0), 1: ("A", [(0.0, 300, 150, 90), (6.0, 300, 280, 90)]),
               5: still("B", 400, 110, 180), 6: still("B", 380, 250, 200)}
    bomb = {"k": "ability", "t": 3.3, "t1": None, "by": 0, "kind": "Bomb", "code": "", "name": "Spike",
            "u": 3000, "v": 3000}
    data = blob(players, t_end=8.0, t_decided=7.0, util=[bomb], deaths={6: 4.1})
    return ce.compute_round(data, geo, ce.ControlLink(sides={0: "attack", 1: "attack", 5: "defense", 6: "defense"})), data


def test_checkpoints_are_the_first_tick_then_every_interval():
    ticks = np.array([0.0, 0.5, 1.0, 2.4375, 2.5, 3.0, 5.0625, 7.5, 12.0])
    assert checkpoint_ticks(ticks) == [0, 4, 6, 7, 8]
    assert checkpoint_ticks(np.array([])) == []


def test_data_round_trips_every_tick(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(rc, data))
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    assert header["v"] == cf.DATA_VERSION and header["revision"] == cf.CONTROL_REVISION
    assert header["cells"] == cells and header["ticks"] == [round(t * cf.GRID_HZ) for t in rc.ticks]
    assert cf.walk_bitmap(header) == rc.walk_cells.tolist()
    checkpoints = [c[0] for c in header["checkpoints"]]
    assert checkpoints == checkpoint_ticks(rc.ticks) and len(checkpoints) >= 3
    assert np.array_equal(np.array(cf.decode_states(streams["states"], n_ticks, cells)), rc.states)
    for name, masks in (("coverage", rc.coverage_masks), ("control", rc.control_masks)):
        got = np.array(cf.decode_masks(streams[name], n_ticks, cells, checkpoints), bool)
        assert np.array_equal(got, masks), name
        assert masks.any(), f"the toy round should exercise {name}"
    for slot in range(cf.SLOTS):
        want = [None if np.isnan(v) else round(float(v), 1) for v in rc.control[:, slot]]
        assert header["control_m2"][slot] == want
    assert header["group_side"] == {"A": "attack", "B": "defense"}


def test_each_teams_unknown_round_trips_as_optional_streams(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(rc, data))
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    checkpoints = [c[0] for c in header["checkpoints"]]
    assert [c[0] for c in header["unknown_checkpoints"]] == checkpoints
    for group in ("A", "B"):
        got = cf.decode_unknown(streams[f"unknown_{group.lower()}"], n_ticks, cells, checkpoints)
        assert np.array_equal(np.array(got, bool), rc.unknown[group]), group
        assert rc.unknown[group].any(), f"the toy round should exercise {group}'s unknown"


def test_a_row_without_unknown_reads_as_before(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(dataclasses.replace(rc, unknown=None), data))
    assert "unknown_a" not in streams and "unknown_checkpoints" not in header
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    assert np.array_equal(np.array(cf.decode_states(streams["states"], n_ticks, cells)), rc.states)


def test_what_each_team_knew_round_trips_as_optional_streams(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(rc, data))
    n_ticks, cells = len(rc.ticks), len(rc.walk_cells)
    true = cf.decode_states(streams["states"], n_ticks, cells)
    for group in ("A", "B"):
        got = cf.decode_knew(streams[f"knew_{group.lower()}"], true)
        assert np.array_equal(np.array(got), rc.knew_states[group]), group
    assert [c[0] for c in header["knew_checkpoints"]] == [c[0] for c in header["checkpoints"]]
    assert set(header["knew"]) == {"A", "B"} and header["knew_fade_s"] > 0
    # a row computed without them has the three streams only, and reads the same
    plain = ce.compute_round(data, door_hall(), ce.ControlLink(sides={0: "attack", 1: "attack", 5: "defense",
                                                                         6: "defense"}), knowledge=False)
    h2, s2 = cf.unpack_data(encode_data(dataclasses.replace(plain, unknown=None), data))
    assert set(s2) == {"states", "coverage", "control"} and "knew" not in h2
    assert cf.decode_states(s2["states"], n_ticks, cells) == true


def test_a_checkpoint_offset_lets_a_reader_start_there(round_control):
    rc, data = round_control
    header, streams = cf.unpack_data(encode_data(rc, data))
    tick, states_at, coverage_at, _ = header["checkpoints"][2]
    cells = header["cells"]
    rest = cf.decode_states(streams["states"][states_at:], len(rc.ticks) - tick, cells)
    assert np.array_equal(np.array(rest), rc.states[tick:])
    later = [c[0] - tick for c in header["checkpoints"][2:]]
    got = np.array(cf.decode_masks(streams["coverage"][coverage_at:], len(rc.ticks) - tick, cells, later), bool)
    assert np.array_equal(got, rc.coverage_masks[tick:])


def test_the_summary_keeps_the_section_totals_and_player_stats(round_control):
    rc, data = round_control
    summary = cf.unpack_summary(encode_summary(rc, data))
    cells = len(rc.walk_cells)
    assert [s["key"] for s in summary["sections"]] == [s.key for s in rc.sections]
    for stored, sec in zip(summary["sections"], rc.sections):
        totals = np.array(cf.decode_totals(stored["totals"], cells, stored["seconds"]))
        # Rounded to 1/GRID_HZ s per state; `none` takes the rest.
        assert np.abs(totals[1:] - sec.totals[1:]).max() <= 0.5 / cf.GRID_HZ + 1e-6
        rounding = (len(cf.STATE_NAMES) - 1) * 0.5 / cf.GRID_HZ
        assert totals.sum(0) == pytest.approx(np.full(cells, sec.seconds), abs=rounding + 1e-6)
    assert summary["players"]["0"] == rc.players[0].as_dict()
    assert summary["players"]["6"]["deaths"] and summary["group_side"] == {"A": "attack", "B": "defense"}


def test_the_data_is_one_gzip_member_with_the_magic_inside():
    raw = cf.pack_data({"x": 1}, {"states": b"s", "coverage": b"cc", "control": b"ddd"})
    inner = gzip.decompress(raw)
    assert inner[:4] == cf.MAGIC and inner[4] == cf.DATA_VERSION
    header, streams = cf.unpack_data(raw)
    assert header["x"] == 1 and streams == {"states": b"s", "coverage": b"cc", "control": b"ddd"}
    with pytest.raises(cf.ControlFormatError):
        cf.unpack_data(gzip.compress(b"NOPE" + inner[4:]))


def test_sparse_totals_round_trip():
    by_state = [[(0, 3), (7, 160)], [], [(2, 1)]] + [[] for _ in range(5)]
    text = cf.encode_totals(by_state)
    totals = cf.decode_totals(text, 8, 10.0)
    assert totals[1][0] == 3 / 16 and totals[1][7] == 10.0 and totals[3][2] == 1 / 16
    assert totals[0][7] == 0.0 and totals[0][1] == 10.0
    assert base64.b64decode(text)
