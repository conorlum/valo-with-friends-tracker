"""One round's control as the stored bytes (Stage 3): `RoundControl` in, `replay_round_control`'s
`data` and `summary` out. The formats are app/replays/control_format.py's, which also decodes them;
this is the numpy side, run only by scripts/compute_control.py.
"""

from __future__ import annotations

import base64

import numpy as np

from app.control.engine import GRID_HZ, N_STATES, RoundControl
from app.control.geometry import GRID
from app.replays import control_format as cf


def checkpoint_ticks(ticks: np.ndarray) -> list[int]:
    """The first tick, then the first tick at or after every CHECKPOINT_S from it."""
    if not len(ticks):
        return []
    out, due = [0], float(ticks[0]) + cf.CHECKPOINT_S
    for i in range(1, len(ticks)):
        if ticks[i] >= due - 1e-9:
            out.append(i)
            while due <= ticks[i] + 1e-9:
                due += cf.CHECKPOINT_S
    return out


def _gaps(indices: np.ndarray, out: bytearray, codes: np.ndarray | None = None) -> None:
    cf.put_varint(len(indices), out)
    last = -1
    for k, c in enumerate(indices.tolist()):
        cf.put_varint(c - last - 1, out)
        if codes is not None:
            out.append(int(codes[k]))
        last = c


def encode_states(states: np.ndarray, checkpoints: list[int]) -> tuple[bytes, list[int]]:
    out, offsets, full, prev = bytearray(), [], set(checkpoints), None
    for i, frame in enumerate(states):
        if i in full:
            offsets.append(len(out))
            out.append(0)
            padded = np.append(frame, 0) if len(frame) % 2 else frame
            out += (padded[0::2] << 4 | padded[1::2]).astype(np.uint8).tobytes()
        else:
            out.append(1)
            changed = np.flatnonzero(frame != prev)
            _gaps(changed, out, frame[changed])
        prev = frame
    return bytes(out), offsets


def encode_masks(masks: np.ndarray, checkpoints: list[int]) -> tuple[bytes, list[int]]:
    """masks: ticks x SLOTS x cells (bool)."""
    out, offsets, full, prev = bytearray(), [], set(checkpoints), None
    for i, frame in enumerate(masks):
        if i in full:
            offsets.append(len(out))
        for slot, mask in enumerate(frame):
            if i in full:
                out += np.packbits(mask).tobytes()
            else:
                _gaps(np.flatnonzero(mask != prev[slot]), out)
        prev = frame
    return bytes(out), offsets


def _grid_units(t: float) -> int:
    return int(round(float(t) * GRID_HZ))


def encode_data(rc: RoundControl, blob: dict) -> bytes:
    if rc.states.shape[1] != len(rc.walk_cells) or rc.control_masks.shape[1] != cf.SLOTS:
        raise cf.ControlFormatError("RoundControl arrays don't match its walkable cells and slots")
    checkpoints = checkpoint_ticks(rc.ticks)
    states, s_off = encode_states(rc.states, checkpoints)
    coverage, v_off = encode_masks(rc.coverage_masks, checkpoints)
    control, c_off = encode_masks(rc.control_masks, checkpoints)
    walk = np.zeros(GRID * GRID, bool)
    walk[rc.walk_cells] = True
    control_m2 = [[None if np.isnan(v) else round(float(v), 1) for v in rc.control[:, s]] for s in range(cf.SLOTS)]
    header = {"revision": cf.CONTROL_REVISION, "map": rc.map, "round": rc.round, "grid": GRID, "hz": GRID_HZ,
              "cell_m2": round(float(rc.cell_m2), 5), "cells": int(len(rc.walk_cells)),
              "walk": base64.b64encode(np.packbits(walk).tobytes()).decode("ascii"),
              "ticks": [_grid_units(t) for t in rc.ticks], "t_start": float(blob.get("t_start") or 0.0),
              "t_decided": blob.get("t_decided"), "t_end": float(blob["t_end"]),
              "checkpoint_s": cf.CHECKPOINT_S,
              "checkpoints": [[t, a, b, c] for t, a, b, c in zip(checkpoints, s_off, v_off, c_off)],
              "states": list(cf.STATE_NAMES), "group_side": rc.group_side, "control_m2": control_m2}
    return cf.pack_data(header, {"states": states, "coverage": coverage, "control": control})


def encode_summary(rc: RoundControl, blob: dict) -> bytes:
    sections = []
    for sec in rc.sections:
        units = np.rint(sec.totals * GRID_HZ).astype(np.int64)
        by_state = []
        for state in range(1, N_STATES):
            cells = np.flatnonzero(units[state] > 0)
            by_state.append(list(zip(cells.tolist(), units[state, cells].tolist())))
        sections.append({"key": sec.key, "t0": round(sec.t0, 4), "t1": round(sec.t1, 4),
                         "seconds": round(sec.seconds, 4), "totals": cf.encode_totals(by_state)})
    return cf.pack_summary({
        "revision": cf.CONTROL_REVISION, "map": rc.map, "round": rc.round, "cells": int(len(rc.walk_cells)),
        "cell_m2": round(float(rc.cell_m2), 5), "t_start": float(blob.get("t_start") or 0.0),
        "t_decided": blob.get("t_decided"), "group_side": rc.group_side, "sections": sections,
        "players": {str(slot): p.as_dict() for slot, p in sorted(rc.players.items())},
        "redundant_m2s": {k: round(float(v), 1) for k, v in rc.redundant_m2s.items()},
        "missing_inputs": dict(rc.missing_inputs)})
