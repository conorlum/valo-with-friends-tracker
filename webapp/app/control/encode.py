"""One round's control as the stored bytes (Stage 3): `RoundControl` in, `replay_round_control`'s
`data` and `summary` out. The formats are app/replays/control_format.py's, which also decodes them;
this is the numpy side, run only by scripts/compute_control.py.
"""

from __future__ import annotations

import base64

import numpy as np

from app.control.engine import GRID_HZ, KNEW_FADE_S, N_STATES, RoundControl
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


def encode_knew(knew: np.ndarray, states: np.ndarray, checkpoints: list[int]) -> tuple[bytes, list[int]]:
    """A team's picture, each tick as the cells where it differs from the true state then: a varint
    count, then per cell a varint index gap and its code byte. Ticks stand alone; the offsets are
    those of the checkpoint ticks, for seeking."""
    out, offsets, full = bytearray(), [], set(checkpoints)
    for i in range(len(states)):
        if i in full:
            offsets.append(len(out))
        changed = np.flatnonzero(knew[i] != states[i])
        _gaps(changed, out, knew[i][changed])
    return bytes(out), offsets


def _grid_units(t: float) -> int:
    return int(round(float(t) * GRID_HZ))


# Why an enemy's unknown changed, for the Control panel (W21). A reason that repeats for the same enemy within
# REASON_REPEAT_S of its last occurrence is one run, listed once at its start (a sighting refreshes every tick).
# This is presentation only: the engine's locating history, which the gap detector reads, keeps every tick.
REASON_REPEAT_S = 1.0
REASON_SKIP = frozenset({"resume"})        # a channel's end is not news; its start (`pause`) is listed


def knowledge_events(reasons: dict | None) -> dict:
    """{side group: [[t, enemy slot, reason], ...]} in time order, t in round seconds at the event's own time (three
    decimals: not the frame grid), runs of one reason collapsed to their first."""
    out = {}
    for side in ("A", "B"):
        rows, last = [], {}
        for t, slot, kind, reason, _ in sorted((reasons or {}).get(side) or [], key=lambda r: (r[0], r[1], r[3])):
            if kind in REASON_SKIP:
                continue
            key = (int(slot), str(reason))
            seen_before = last.get(key)
            last[key] = float(t)
            if seen_before is not None and float(t) - seen_before <= REASON_REPEAT_S:
                continue
            rows.append([round(float(t), 3), int(slot), str(reason)])
        out[side] = rows
    return out


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
    streams = {"states": states, "coverage": coverage, "control": control}
    if rc.knew_states:
        # What each side group knew (R3.3): optional streams, so rows without them read as before.
        offsets = {}
        for group in ("A", "B"):
            name = f"knew_{group.lower()}"
            streams[name], offsets[name] = encode_knew(rc.knew_states[group], rc.states, checkpoints)
        header["knew_checkpoints"] = [[t, a, b] for t, a, b in zip(checkpoints, offsets["knew_a"], offsets["knew_b"])]
        header["knew"] = {group: {str(s): runs for s, runs in sorted(rc.knew_sightings[group].items())}
                          for group in ("A", "B")}
        header["knew_fade_s"] = KNEW_FADE_S
    if rc.unknown:
        # Each side group's unknown (docs/map-control-unknown-plan.md): optional streams, one mask a tick
        # in encode_masks' format with a single slot, so rows without them read as before.
        offsets = {}
        for group in ("A", "B"):
            name = f"unknown_{group.lower()}"
            streams[name], offsets[name] = encode_masks(rc.unknown[group][:, None, :], checkpoints)
        header["unknown_checkpoints"] = [[t, a, b] for t, a, b in
                                         zip(checkpoints, offsets["unknown_a"], offsets["unknown_b"])]
    if getattr(rc, "reasons", None) is not None:
        # Optional (W21): a row stored before this has no list, and the panel says so instead of guessing.
        header["knowledge_events"] = knowledge_events(rc.reasons)
    return cf.pack_data(header, streams)


def encode_summary(rc: RoundControl, blob: dict, *, provenance=None) -> bytes:
    sections = []
    for sec in rc.sections:
        units = np.rint(sec.totals * GRID_HZ).astype(np.int64)
        by_state = []
        for state in range(1, N_STATES):
            cells = np.flatnonzero(units[state] > 0)
            by_state.append(list(zip(cells.tolist(), units[state, cells].tolist())))
        sections.append({"key": sec.key, "t0": round(sec.t0, 4), "t1": round(sec.t1, 4),
                         "seconds": round(sec.seconds, 4), "totals": cf.encode_totals(by_state)})
    summary = {
        "revision": cf.CONTROL_REVISION, "map": rc.map, "round": rc.round, "cells": int(len(rc.walk_cells)),
        "cell_m2": round(float(rc.cell_m2), 5), "t_start": float(blob.get("t_start") or 0.0),
        "t_decided": blob.get("t_decided"), "group_side": rc.group_side, "sections": sections,
        "players": {str(slot): p.as_dict() for slot, p in sorted(rc.players.items())},
        "redundant_m2s": {k: round(float(v), 1) for k, v in rc.redundant_m2s.items()},
        "missing_inputs": dict(rc.missing_inputs)}
    if provenance is not None:
        summary['provenance'] = provenance
    return cf.pack_summary(summary)
