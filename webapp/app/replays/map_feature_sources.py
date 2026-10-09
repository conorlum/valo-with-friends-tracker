"""Parent-safe snapshots of the existing monochrome control mask assets."""
import base64
import struct
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


def base_snapshot(map_name, folder, entry, scale):
    masks = {kind: png_mask((folder / f'{map_name}.{kind}.png').read_bytes()) for kind in ('sight', 'walk')}
    barrier_path = folder / f'{map_name}.barrier.png'
    barrier, barrier_sha = None, None
    if barrier_path.is_file():
        pixels = png_mask(barrier_path.read_bytes())
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
