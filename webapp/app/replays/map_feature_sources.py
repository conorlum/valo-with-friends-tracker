"""Parent-safe snapshots of the existing monochrome control mask assets."""
import base64
import collections
import copy
import hashlib
import json
import struct
import threading
import zlib

from app.replays.map_feature_artifacts import pack_mask
from app.replays.map_feature_inputs import FeatureInputsError


def png_mask(raw):
    try:
        return _png_mask(raw)
    except (struct.error, zlib.error, IndexError, TypeError) as exc:
        raise FeatureInputsError('invalid mask PNG') from exc


def _png_mask(raw):
    if len(raw) > 4 * 1024 * 1024:
        raise FeatureInputsError('mask PNG size limit')
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise FeatureInputsError('invalid mask PNG')
    pos, compressed, header = 8, bytearray(), None
    while pos < len(raw):
        size = struct.unpack('>I', raw[pos:pos + 4])[0]
        kind, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + size]
        if pos + size + 12 > len(raw) or zlib.crc32(kind + data) != struct.unpack('>I', raw[pos + 8 + size:pos + 12 + size])[0]:
            raise FeatureInputsError('invalid mask PNG chunk')
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            break
        pos += size + 12
    if header is None or header[:2] != (1024, 1024) or header[2] not in (1, 8) or header[3:] != (0, 0, 0, 0):
        raise FeatureInputsError('mask PNG must be noninterlaced 1024-square monochrome')
    depth = header[2]
    width = 1024 * depth // 8
    inflater = zlib.decompressobj()
    pixels = inflater.decompress(compressed, (width + 1) * 1024 + 1)
    if len(pixels) != (width + 1) * 1024 or not inflater.eof or inflater.unused_data:
        raise FeatureInputsError('invalid mask PNG expansion')
    previous, expanded = bytearray(width), bytearray()
    for row in range(1024):
        start = row * (width + 1)
        method, line = pixels[start], bytearray(pixels[start + 1:start + 1 + width])
        if method > 4:
            raise FeatureInputsError('invalid PNG filter')
        for x in range(width) if method else ():
            a, b, c = line[x - 1] if x else 0, previous[x], previous[x - 1] if x else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
            line[x] = (line[x] + (0, a, b, (a + b) // 2, predictor)[method]) & 255
        if depth == 1:
            expanded.extend((value >> bit) & 1 for value in line for bit in range(7, -1, -1))
        else:
            expanded.extend(int(value > 127) for value in line)
        previous = line
    return bytes(expanded)


def descriptor_digest(descriptor):
    """Match the engine's big-endian packed-mask identity from a little-endian archive."""
    import hashlib
    reverse = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
    packed = base64.b64decode(descriptor['data'], validate=True)
    return hashlib.sha256(packed.translate(reverse)).hexdigest()[:12]


def _read_raws(map_name, folder):
    sight = (folder / f'{map_name}.sight.png').read_bytes()
    walk = (folder / f'{map_name}.walk.png').read_bytes()
    barrier_path = folder / f'{map_name}.barrier.png'
    return sight, walk, (barrier_path.read_bytes() if barrier_path.is_file() else None)


def _base_snapshot(map_name, folder, entry, scale):
    return _decode(map_name, _read_raws(map_name, folder), entry, scale)


def _decode(map_name, raws, entry, scale):
    masks = {'sight': png_mask(raws[0]), 'walk': png_mask(raws[1])}
    barrier, barrier_sha = None, None
    if raws[2] is not None:
        pixels = png_mask(raws[2])
        barrier_sha = descriptor_digest(pack_mask(pixels, [1024, 1024]))
        barrier = bytes(int(any(pixels[(r * 8 + y) * 1024 + c * 8 + x] for y in range(8) for x in range(8)))
                        for r in range(128) for c in range(128))
    legacy = {}
    for kind in ('cover_paint', 'cant_walk_paint'):
        if entry.get(kind):
            bits = base64.b64decode(entry[kind], validate=True)
            if len(bits) != 256 * 256 // 8:
                raise FeatureInputsError('invalid legacy paint size')
            small = bytes((value >> bit) & 1 for value in bits for bit in range(8))
            large = bytes(small[(r // 4) * 256 + c // 4] for r in range(1024) for c in range(1024))
            legacy[kind] = pack_mask(large, [1024, 1024])
    return {'sight': pack_mask(masks['sight'], [1024, 1024]), 'walk': pack_mask(masks['walk'], [1024, 1024]),
            'barrier': None if barrier is None else pack_mask(barrier, [128, 128]),
            'barrier_sha': barrier_sha,
            'scale': scale, 'specials': entry.get('specials') or [], 'legacy': legacy}


# The decode cache: keyed on the asset files' bytes (an edited file is decoded afresh), never on paths or times.
_CACHE_MAX = 32
_cache = collections.OrderedDict()
_counts = {'hits': 0, 'misses': 0}
_cache_lock = threading.Lock()


def _locked():
    return _cache_lock


def _copy(value):
    return copy.deepcopy(value)


def _raw_digests(raws):
    return tuple(None if raw is None else hashlib.sha256(raw).hexdigest() for raw in raws)


def _entry_digest(entry):
    part = {k: entry.get(k) for k in ('cover_paint', 'cant_walk_paint', 'specials')}
    return hashlib.sha256(json.dumps(part, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _key(map_name, raws, entry, scale):
    return (map_name, _raw_digests(raws), _entry_digest(entry), repr(scale))


def _evict():
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def base_snapshot(map_name, folder, entry, scale):
    raws = _read_raws(map_name, folder)
    key = _key(map_name, raws, entry, scale)
    with _locked():
        if key in _cache:
            _cache.move_to_end(key)
            _counts['hits'] += 1
            return _copy(_cache[key])
        value = _decode(map_name, raws, entry, scale)
        _cache[key] = value
        _counts['misses'] += 1
        _evict()
        return _copy(value)


def _cache_clear():
    with _cache_lock:
        _cache.clear()
        _counts.update(hits=0, misses=0)


def _cache_info():
    with _cache_lock:
        return {'hits': _counts['hits'], 'misses': _counts['misses'], 'size': len(_cache)}


base_snapshot.cache_clear = _cache_clear
base_snapshot.cache_info = _cache_info
