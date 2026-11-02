"""numpy versions of app/replays/map_feature_sources.py's per-pixel mask work, byte for byte the same.

app/replays is copied into the replay worker's stdlib-only image, so it never imports numpy
(tests/replays/test_control_isolation.py). The web app, which already has numpy, registers this module there
(`register`, from app/main.py's lifespan and app/services/util_rebuild.py): a map's mask decode drops from about
2 s to about 0.03 s. Without it map_feature_sources runs its own pure-Python loops.
tests/replays/test_map_feature_sources_real_assets.py checks both give the same descriptors on every real map."""

from __future__ import annotations

import base64

import numpy as np


def unfilter_up(line: bytes, previous: bytes) -> bytearray:
    """PNG filter 2 ("up"): each byte plus the one above it, mod 256."""
    return bytearray((np.frombuffer(line, np.uint8) + np.frombuffer(previous, np.uint8)).tobytes())


def expand(lines: bytes, depth: int) -> bytes:
    """Unfiltered scanlines to one byte a pixel, 0 or 1: 1-bit rows high bit first, 8-bit rows by > 127."""
    values = np.frombuffer(lines, np.uint8)
    return (np.unpackbits(values) if depth == 1 else (values > 127).astype(np.uint8)).tobytes()


def pack(raw: bytes, shape) -> dict | None:
    """map_feature_artifacts.pack_mask's descriptor, or None for input it would refuse (it then says why)."""
    count = 1
    for n in shape:
        count *= n
    values = np.frombuffer(raw, np.uint8)
    if len(raw) != count or (count and values.max() > 1):
        return None
    packed = np.packbits(values, bitorder="little").tobytes()
    return {"shape": list(shape), "bitorder": "little", "count": count, "bytes": len(packed),
            "data": base64.b64encode(packed).decode("ascii")}


def barrier_cells(pixels: bytes) -> bytes:
    """1 per 8x8 block of a 1024-square paint holding any paint, rows then columns (128x128)."""
    return np.frombuffer(pixels, np.uint8).reshape(128, 8, 128, 8).any(axis=(1, 3)).astype(np.uint8).tobytes()


def legacy_large(bits: bytes) -> bytes:
    """A 256-square legacy paint (8 cells a byte, low bit first) scaled 4x to 1024-square, a byte a cell."""
    small = np.unpackbits(np.frombuffer(bits, np.uint8), bitorder="little").reshape(256, 256)
    return small.repeat(4, axis=0).repeat(4, axis=1).tobytes()


def register() -> None:
    import sys

    from app.replays import map_feature_sources

    map_feature_sources.use_accelerator(sys.modules[__name__])
