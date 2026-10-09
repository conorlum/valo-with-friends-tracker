"""Stored map control: its byte formats and its freshness (docs/replay-map-control-plan.md, "Storage";
Stage 3). Standard library only, so the web app can judge freshness and read summaries without the
engine's numpy and scipy (tests/replays/test_control_isolation.py).

One `replay_round_control` row per round (migration 0014):

- **`data`**, served as is by `GET /replays/{uuid}/{n}/control.bin` (`Content-Encoding: gzip`). Inside
  the gzip: `MAGIC`, a version byte, a little-endian u32 header length, the header (UTF-8 JSON), then
  three streams back to back:
  - **states**, per tick: a checkpoint is `0` and every walkable cell's code in 4 bits (high nibble
    first); any other tick is `1`, a varint count of changed cells, then per cell a varint index gap
    and its code byte.
  - **coverage** and **control**, per tick and per slot 0-9: at a checkpoint the slot's bitmask over
    the walkable cells (MSB first), else a varint count of flipped cells and their varint index gaps.
  Checkpoints are the first tick and the first tick at or after every CHECKPOINT_S, so the viewer can
  seek: `header["checkpoints"]` gives each one's tick and byte offset in each stream.
  - Optional, **knew_a** and **knew_b** (R3.3, docs/map-control-team-knew-plan.md): each side group's
    picture of the round, per tick the cells where it differs from that tick's true state (a varint
    count, then per cell a varint index gap and its code byte). `header["knew_checkpoints"]` gives
    each checkpoint's tick and offsets in the two; `header["knew"]` each group's sightings of each
    enemy slot, `[[t0, t1, u, v], ...]` (u, v at the last sighting). A row without them reads as before.
- **`summary`**, gzip JSON, never served whole: the per-section heatmap totals and the per-player
  stats (Stages 4-5 read these instead of replaying ticks). A section's totals are, for each state
  but `none` (the section's seconds minus the rest), a varint count of cells, then per cell a varint
  index gap and a varint time in 1/GRID_HZ s (rounded).

A cell index is the position among the round's walkable cells (row-major over the GRID x GRID map),
whose bitmap is the header's `walk`.

**Freshness.** `fingerprint` hashes everything a round's control is computed from: CONTROL_REVISION,
the formats, the blob (its recipe and source file), the link data control reads (sides, DB-only
deaths on the replay clock) and the map's geometry (index.json's mask hashes and specials). A row
whose fingerprint differs is stale: still served, flagged, and recomputed by the next
`scripts/compute_control.py` run. Any change to the engine's or geometry's rules or constants
bumps CONTROL_REVISION (pinned by tests/replays/test_control_store.py).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
from pathlib import Path

CONTROL_REVISION = 9     # 9: player state (the molly figures; blinded players and active mollies, unreleased)
DATA_VERSION = 1
SUMMARY_VERSION = 1

# Map heights (app/control/heights.py re-exports the first, third and fourth). Here, in a stdlib-only module,
# so the web app can judge a stored asset and a build's report without importing numpy.
HEIGHT_VERSION = 2            # the asset's format: an asset of another version can't be loaded
# The rules a build follows. Bump it for ANY change to how rounds become heights (a constant in
# app/control/heights.py, the code of height_build.py or height_motion.py): every map's rebuild is then due.
# tests/replays/test_height_inputs.py pins it to the constants' hash, as CONTROL_REVISION is pinned.
HEIGHT_RULES_REVISION = 2      # 2: recheck emitted floor separation after taking each band's low percentile
HEIGHT_SUPPORTED_MIN = 0.60   # the bar: share of walkable cells with a supported floor
KILL_LINE_BAR = 0.02          # the kill-line check: at most this share of real kill lines blocked

MAGIC = b"VCTL"
GRID = 128
GRID_HZ = 16
SLOTS = 10
CHECKPOINT_S = 2.5
# The engine's cell state codes, in side-group terms (A/B, the blob's `side`).
STATE_NAMES = ("none", "a_passive", "a_safe", "a_active", "b_passive", "b_safe", "b_active",
               "contested_active", "contested")


class ControlFormatError(ValueError):
    pass


# ---------------------------------------------------------------- freshness


_CONTROL_DIR = Path(__file__).resolve().parents[1] / "control"
HEARING_FILE = _CONTROL_DIR / "hearing.json"
UTILITY_FILE = _CONTROL_DIR / "utility.json"


def _numbers(value):
    """The numeric view of a figures file: numbers as floats, tables of numbers, nothing else (no `sources`, notes
    or `PROVISIONAL` text)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        out = {str(k): _numbers(v) for k, v in value.items() if k not in ("sources", "PROVISIONAL")}
        return {k: v for k, v in out.items() if v is not None} or None
    return None


def figures_hash(hearing=None, utility=None) -> str:
    """16 hex of the game figures the engine consumes (the 2026-10-05 plan, W22): app/control/hearing.json's and
    app/control/utility.json's numbers, as sorted JSON. One view for control's fingerprint and the gap keys, so a
    changed range makes both stale and an edited citation makes neither. Read as files, never imported (the web
    app has no engine); a missing file reads as empty."""
    view = {}
    for name, path in (("hearing", hearing or HEARING_FILE), ("utility", utility or UTILITY_FILE)):
        try:
            with open(path, encoding="utf-8") as handle:
                view[name] = _numbers(json.load(handle)) or {}
        except FileNotFoundError:
            view[name] = {}
    return hashlib.sha256(json.dumps(view, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def fingerprint(recipe: str, source_sha256: str, link: dict, geometry: dict) -> str:
    """16 hex characters over every input of one round's control (since revision 7, the consumed game figures
    too: `figures_hash`)."""
    body = {"control": CONTROL_REVISION, "data": DATA_VERSION, "summary": SUMMARY_VERSION, "recipe": recipe,
            "source": source_sha256, "link": link, "geometry": geometry, "figures": figures_hash()}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- varints


def put_varint(n: int, out: bytearray) -> None:
    if n < 0:
        raise ControlFormatError(f"varint of a negative number: {n}")
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, pos
        shift += 7


# ---------------------------------------------------------------- data (the served bytes)


def pack_data(header: dict, streams: dict[str, bytes]) -> bytes:
    """gzip(MAGIC, version, header length, header, states, coverage, control)."""
    # the three always there, then optional ones (what each team knew: knew_a, knew_b) by name
    order = ("states", "coverage", "control") + tuple(sorted(set(streams) - {"states", "coverage", "control"}))
    offset, where = 0, {}
    for name in order:
        where[name] = [offset, len(streams[name])]
        offset += len(streams[name])
    head = json.dumps({**header, "v": DATA_VERSION, "streams": where}, separators=(",", ":")).encode("utf-8")
    raw = MAGIC + bytes([DATA_VERSION]) + struct.pack("<I", len(head)) + head + b"".join(streams[n] for n in order)
    return gzip.compress(raw, compresslevel=9, mtime=0)


def unpack_data(data: bytes) -> tuple[dict, dict[str, bytes]]:
    raw = gzip.decompress(data)
    if raw[:4] != MAGIC:
        raise ControlFormatError("not a control blob")
    if raw[4] != DATA_VERSION:
        raise ControlFormatError(f"control data version {raw[4]} is not {DATA_VERSION}")
    (length,) = struct.unpack("<I", raw[5:9])
    header = json.loads(raw[9:9 + length].decode("utf-8"))
    body = raw[9 + length:]
    return header, {name: body[a:a + n] for name, (a, n) in header["streams"].items()}


def walk_bitmap(header: dict) -> list[int]:
    """The flat map cells (row-major over GRID x GRID) of the round's walkable cells, in order."""
    bits = base64.b64decode(header["walk"])
    return [i for i in range(GRID * GRID) if bits[i >> 3] >> (7 - (i & 7)) & 1]


def decode_states(stream: bytes, ticks: int, cells: int) -> list[list[int]]:
    """Every tick's state codes (reference decoder; the viewer's is replay.js)."""
    frames, cur, pos = [], [0] * cells, 0
    for _ in range(ticks):
        kind = stream[pos]
        pos += 1
        if kind == 0:
            for c in range(0, cells, 2):
                b = stream[pos]
                pos += 1
                cur[c] = b >> 4
                if c + 1 < cells:
                    cur[c + 1] = b & 15
        else:
            count, pos = read_varint(stream, pos)
            c = -1
            for _ in range(count):
                gap, pos = read_varint(stream, pos)
                c += gap + 1
                cur[c] = stream[pos]
                pos += 1
        frames.append(list(cur))
    return frames


def decode_knew(stream: bytes, true_frames: list[list[int]]) -> list[list[int]]:
    """A team's picture per tick (the optional `knew_a` / `knew_b` streams, R3.3): each tick is the true
    state with the stored cells replaced (reference decoder)."""
    frames, pos = [], 0
    for base in true_frames:
        cur = list(base)
        count, pos = read_varint(stream, pos)
        c = -1
        for _ in range(count):
            gap, pos = read_varint(stream, pos)
            c += gap + 1
            cur[c] = stream[pos]
            pos += 1
        frames.append(cur)
    return frames


def decode_masks(stream: bytes, ticks: int, cells: int, checkpoints: list[int],
                 slots: int = SLOTS) -> list[list[list[int]]]:
    """Every tick's per-slot masks, as lists of 0/1 (reference decoder)."""
    full = set(checkpoints)
    width = (cells + 7) >> 3
    frames, cur, pos = [], [[0] * cells for _ in range(slots)], 0
    for i in range(ticks):
        for mask in cur:
            if i in full:
                for c in range(cells):
                    mask[c] = stream[pos + (c >> 3)] >> (7 - (c & 7)) & 1
                pos += width
            else:
                count, pos = read_varint(stream, pos)
                c = -1
                for _ in range(count):
                    gap, pos = read_varint(stream, pos)
                    c += gap + 1
                    mask[c] ^= 1
        frames.append([list(m) for m in cur])
    return frames


def decode_unknown(stream: bytes, ticks: int, cells: int, checkpoints: list[int]) -> list[list[int]]:
    """A side group's unknown per tick (the optional `unknown_a` / `unknown_b` streams; 1 where an enemy of
    that group could be): decode_masks with one slot (reference decoder; the viewer's is replay_control.js)."""
    return [frame[0] for frame in decode_masks(stream, ticks, cells, checkpoints, slots=1)]


# ---------------------------------------------------------------- summary (heatmaps and per-player stats)


def pack_summary(summary: dict) -> bytes:
    return gzip.compress(json.dumps({**summary, "v": SUMMARY_VERSION}, separators=(",", ":")).encode("utf-8"),
                         compresslevel=9, mtime=0)


def unpack_summary(data: bytes) -> dict:
    summary = json.loads(gzip.decompress(data).decode("utf-8"))
    if summary.get("v") != SUMMARY_VERSION:
        raise ControlFormatError(f"control summary version {summary.get('v')} is not {SUMMARY_VERSION}")
    return summary


def encode_totals(by_state: list[list[tuple[int, int]]]) -> str:
    """base64 of the sparse totals: for states 1.. (not `none`), [(cell, ticks of 1/GRID_HZ s), ...]."""
    out = bytearray()
    for cells in by_state:
        put_varint(len(cells), out)
        last = -1
        for cell, units in cells:
            put_varint(cell - last - 1, out)
            put_varint(units, out)
            last = cell
    return base64.b64encode(bytes(out)).decode("ascii")


def decode_totals(text: str, cells: int, seconds: float) -> list[list[float]]:
    """Seconds per state (all STATE_NAMES) per walkable cell; `none` is the section's rest."""
    buf, pos = base64.b64decode(text), 0
    totals = [[0.0] * cells for _ in STATE_NAMES]
    for state in range(1, len(STATE_NAMES)):
        count, pos = read_varint(buf, pos)
        c = -1
        for _ in range(count):
            gap, pos = read_varint(buf, pos)
            units, pos = read_varint(buf, pos)
            c += gap + 1
            totals[state][c] = units / GRID_HZ
    for c in range(cells):
        totals[0][c] = max(0.0, seconds - sum(totals[s][c] for s in range(1, len(STATE_NAMES))))
    return totals
