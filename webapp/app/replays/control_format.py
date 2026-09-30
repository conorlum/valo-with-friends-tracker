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

CONTROL_REVISION = 2
DATA_VERSION = 1
SUMMARY_VERSION = 1

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


def fingerprint(recipe: str, source_sha256: str, link: dict, geometry: dict) -> str:
    """16 hex characters over every input of one round's control."""
    body = {"control": CONTROL_REVISION, "data": DATA_VERSION, "summary": SUMMARY_VERSION, "recipe": recipe,
            "source": source_sha256, "link": link, "geometry": geometry}
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
    order = ("states", "coverage", "control")
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


def decode_masks(stream: bytes, ticks: int, cells: int, checkpoints: list[int]) -> list[list[list[int]]]:
    """Every tick's per-slot masks, as lists of 0/1 (reference decoder)."""
    full = set(checkpoints)
    width = (cells + 7) >> 3
    frames, cur, pos = [], [[0] * cells for _ in range(SLOTS)], 0
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
