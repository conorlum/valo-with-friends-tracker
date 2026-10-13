"""Round geometry's bounded sight memo and conservative authored source-area hints.

Only compiler-owned, read-only panels are memoized. Pixel sampling in blocked_lines is
batch-dependent: its longest target defines the linspace. That step count is part of
every key, so reuse cannot move a thin panel's intersection or a height-band crossing.
Complete rows keep that sampling context too. Smokes and target skips stay out of
geometry rows; exact unions use their complete immutable input values.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
import zipfile

import numpy as np

MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024 * 1024
MAX_UNION_BYTES = 16 * 1024 * 1024
ROW_VERSION = 1  # Bump when row sampling/height semantics change without a geometry-key change.


@dataclass
class SourceArea:
    drawn: np.ndarray
    sweep_bounds: tuple
    checked: np.ndarray

    def affects(self, geo, source):
        """An omitted source is skipped only after its static row proves it safe.

        The rectangle encloses the entire sweep, including both resting poses. Ignore
        heights/smokes here: these can only remove visibility and must not weaken proof.
        """
        from app.control.features import _lines_meet_bounds
        if self.drawn[source]:
            return True
        if self.checked[source] < 0:
            row = np.unpackbits(geo.rows[geo.row_of[source]])[:geo.n]
            targets = np.flatnonzero(row)
            b = np.column_stack((geo.centres[targets], np.zeros(len(targets))))
            self.checked[source] = bool(_lines_meet_bounds(geo.centres[source], b, self.sweep_bounds).any())
        return bool(self.checked[source])


def source_area(geo, feature):
    """Project paint onto complete node footprints, with a proof for omitted nodes."""
    shape = feature.get('sight_source_area')
    if shape is None:
        return None
    from app.control import features as cf
    paint = cf.raster(shape)
    if geo.adaptive:
        touched = np.unique(geo.space_grid[paint])
        drawn = np.isin(geo.node_space, touched)
    else:
        cells = paint.reshape(128, 2, 128, 2).any((1, 3)).ravel()
        drawn = cells[geo.node_cell] if geo.node_cell is not None else cells
    pivot = np.asarray(feature['rotation']['pivot']['uv'], float) * 1024 / 10000
    panel = np.asarray(feature['rotation']['panel']['uv'], float) * 1024 / 10000
    # Raster geometry selects 4-pixel cells at their centres, then expands each
    # selected cell. Enclose those corners too, not only the mathematical panel.
    radius = float(np.linalg.norm(panel - pivot, axis=1).max()) + 4
    # A custom authored phase can go outside the continuous panel's sweep.
    for phase in feature['rotation'].get('phases') or []:
        geometry = phase.get('panel') or phase.get('geometry')
        if geometry is not None:
            ys, xs = np.nonzero(cf.to_px(cf.raster(geometry)))
            if len(xs):
                radius = max(radius, float(np.hypot(xs - pivot[0], ys - pivot[1]).max()) + 1)
    bounds = (float(pivot[0]-radius), float(pivot[1]-radius),
              float(pivot[0]+radius), float(pivot[1]+radius))
    return SourceArea(drawn, bounds, np.full(geo.n, -1, np.int8))


class SightCache:
    """LRU of sparse checked/blocked pairs with a capped allocation budget.

    Content digests reuse identical resting poses without retaining previous rounds'
    large rasters or relying on Python object IDs. A rebuilt geometry has its own cache.
    """
    def __init__(self, geo, limit=MAX_CACHE_BYTES):
        self.limit, self.bytes = limit, 0
        self.entries = OrderedDict()
        self.source_proofs = OrderedDict()
        self.signature = (id(geo.rows), id(geo.centres), id(geo.node_z), id(geo.heights), geo.n,
                          geo.features_sha, geo.height_sha)
        self.hits = self.misses = 0
        self.rows, self.row_requests, self.unions = OrderedDict(), OrderedDict(), OrderedDict()
        self.row_bytes = self.union_bytes = 0
        self.row_hits = self.row_builds = self.union_hits = self.union_misses = 0
        self.row_path = None
        self.rows_dirty = False
        from app.control import heights as hc
        body = np.full(geo.n, np.nan) if geo.heights is None else geo.node_z + hc.BODY_M
        self.targets = np.column_stack((geo.centres.astype(float), body))
        self.proof_coordinates_valid = bool(((geo.centres >= 0) & (geo.centres <= 1023)).all())
        if geo.features_sha and geo.visibility_source in ('built', 'cache'):
            from app.control import geometry as cg
            from pathlib import Path
            identity = hashlib.sha256(f'{ROW_VERSION}:{cg.cache_key(geo)}:{geo.features_sha}'.encode()).hexdigest()
            directory = Path(os.environ['CONTROL_FEATURE_ROW_CACHE_DIR']) if os.environ.get('CONTROL_FEATURE_ROW_CACHE_DIR') else cg.cache_dir() / 'feature-rows'
            self.row_path = directory / f'{geo.name}.{identity}.npz'
            self._load_rows(geo)

    @staticmethod
    def _key(occ, source, steps):
        return (occ.mask_digest, occ.bottom, occ.top, occ.all_height, int(source), steps)

    def clear_row(self, geo, source, targets, occluders):
        """A complete static visibility row for this exact door sampling context.

        Promote repeated source/pose contexts, keeping first-use moving poses cheap.
        Each door is cached independently; combine their blocking rows on lookup.
        Smoke was filtered before determining the original longest-target spacing.
        """
        if any(o.mask.flags.writeable or o.bounds_px is None or o.mask_digest is None for o in occluders):
            return None
        b = self.targets[targets]
        steps = int(np.ceil(np.hypot(b[:, 0] - geo.centres[source, 0],
                                    b[:, 1] - geo.centres[source, 1]).max())) + 1
        occluders = [o for o in occluders if o.source_area is None or o.source_area.affects(geo, source)]
        keys = [self._key(o, source, steps) for o in occluders]
        ready = True
        for key in keys:
            count = self.row_requests.pop(key, 0) + 1
            self.row_requests[key] = count
            ready &= key in self.rows or count >= 3
        while len(self.row_requests) > 8192:
            self.row_requests.popitem(last=False)
        if not ready:
            return None
        out = geo.rows[geo.row_of[source]].copy()
        full_targets = None
        for key, occ in zip(keys, occluders):
            entry = self.rows.pop(key, None)
            if entry is None:
                from app.control import features as cf, heights as hc
                if full_targets is None:
                    full_targets = np.flatnonzero(np.unpackbits(geo.rows[geo.row_of[source]])[:geo.n])
                a = (*geo.centres[source], cf._eye_body(geo, source, hc.EYE_M))
                # Use the *base* row for each door, never the previous door's answer.
                blocked = np.zeros(geo.n, bool)
                blocked[full_targets] = cf.blocked_lines([occ], a, self.targets[full_targets], sampling_steps=steps)
                packed = np.packbits(blocked)
                packed.setflags(write=False)
                entry = (packed, packed.nbytes + 1024, bool(getattr(occ, 'cache_resting', True)))
                self.row_bytes += entry[1]
                self.row_builds += 1
                self.rows_dirty |= entry[2]
                sparse = self.entries.pop(key, None)
                if sparse is not None:
                    self.bytes -= sparse[2]
            else:
                self.row_hits += 1
            self.rows[key] = entry
            out &= ~entry[0]
        while self.row_bytes > MAX_ROW_BYTES and self.rows:
            _, removed = self.rows.popitem(last=False)
            self.row_bytes -= removed[1]
            self.rows_dirty |= removed[2]
        return out

    def union_key(self, sources, smokes, occluders, skip):
        """Only value-stable blockers enter the complete-request memo."""
        if any(not isinstance(s, tuple) or not all(isinstance(v, (int, float, bool, np.number)) for v in s)
               for s in smokes):
            return None  # Wall abilities retain their ordinary path.
        poses = []
        for o in occluders:
            if o.mask.flags.writeable or o.bounds_px is None or o.mask_digest is None:
                return None
            area = o.source_area
            hint = None if area is None else (area.sweep_bounds, np.packbits(area.drawn).tobytes())
            poses.append((o.mask_digest, o.bottom, o.top, o.all_height, o.bounds_px, hint))
        return (np.asarray(sources, np.int64).tobytes(), None if skip is None else np.packbits(skip).tobytes(),
                tuple(smokes), tuple(poses))

    def union_get(self, key):
        if key is None:
            return None
        entry = self.unions.pop(key, None)
        if entry is None:
            self.union_misses += 1
            return None
        self.unions[key] = entry
        self.union_hits += 1
        return np.unpackbits(entry[0])[:len(self.targets)].astype(bool)

    def union_put(self, key, answer):
        if key is None:
            return
        packed = np.packbits(answer)
        hint_bytes = sum(len(p[-1][1]) for p in key[3] if p[-1] is not None)
        size = (packed.nbytes + len(key[0]) + (len(key[1]) if key[1] else 0) + hint_bytes
                + 1024 + 256 * len(key[3]) + 128 * sum(len(s) for s in key[2]))
        old = self.unions.pop(key, None)
        self.union_bytes -= old[1] if old else 0
        if size > MAX_UNION_BYTES:
            return
        self.unions[key] = (packed, size)
        self.union_bytes += size
        while self.union_bytes > MAX_UNION_BYTES:
            _, removed = self.unions.popitem(last=False)
            self.union_bytes -= removed[1]

    def _load_rows(self, geo):
        try:
            with zipfile.ZipFile(self.row_path) as archive:
                if sum(i.file_size for i in archive.infolist()) > MAX_ROW_BYTES + 8 * 1024 * 1024:
                    return
            with np.load(self.row_path, allow_pickle=False) as data:
                keys = json.loads(data['keys'].tobytes())
                rows = data['rows']
                if (type(keys) is not list or data['keys'].dtype != np.uint8 or rows.dtype != np.uint8
                        or rows.shape != (len(keys), (geo.n + 7) // 8)):
                    return
                for encoded, row in zip(keys, rows):
                    digest, lo, hi, all_height, source, steps = encoded
                    if not (len(digest) == 64 and type(source) is int and 0 <= source < geo.n
                            and type(steps) is int and 1 <= steps <= 1450 and type(all_height) is bool):
                        raise ValueError('invalid row key')
                    key = (bytes.fromhex(digest), float.fromhex(lo), float.fromhex(hi), all_height, source, steps)
                    if len(key[0]) != 32 or np.isnan(key[1]) or np.isnan(key[2]) or key in self.rows:
                        raise ValueError('invalid row identity')
                    row = row.copy()
                    row.setflags(write=False)
                    size = row.nbytes + 1024
                    if self.row_bytes + size > MAX_ROW_BYTES:
                        break
                    self.rows[key] = (row, size, True)
                    self.row_bytes += size
        except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile):
            self.rows.clear()
            self.row_bytes = 0

    def persist(self):
        """Best-effort atomic resting-row snapshot. Corrupt/missing caches rebuild."""
        if self.row_path is None or not self.rows_dirty:
            return
        selected = [(key, entry[0]) for key, entry in self.rows.items() if entry[2]]
        keys = [(key[0].hex(), float(key[1]).hex(), float(key[2]).hex(), key[3], key[4], key[5])
                for key, _ in selected]
        temporary = self.row_path.with_suffix(f'.{os.getpid()}.tmp')
        try:
            self.row_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open('wb') as stream:
                np.savez_compressed(stream, keys=np.frombuffer(json.dumps(keys).encode(), np.uint8),
                                    rows=np.stack([row for _, row in selected]) if selected else
                                         np.empty((0, (len(self.targets) + 7) // 8), np.uint8))
            os.replace(temporary, self.row_path)
            self.rows_dirty = False
        except OSError:
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def unaffected(self, geo, sources, occluders):
        """Sources whose entire static row misses every trusted blocker bound.

        These sources can be unioned in packed batches. An authored omission is
        never sufficient: SourceArea proves it against the whole rotation sweep.
        Fixed panels use their enclosing rectangle, ignoring height and smoke.
        Mutable/untrusted masks retain the individual intersection path.
        """
        clear = np.ones(len(sources), bool)
        for occ in occluders:
            if occ.mask.flags.writeable or occ.bounds_px is None or occ.mask_digest is None:
                return np.zeros(len(sources), bool)
            proof = occ.source_area
            if proof is None:
                # Bounded proofs are only valid for the unclipped map coordinates.
                if not self.proof_coordinates_valid:
                    return np.zeros(len(sources), bool)
                proof = self.source_proofs.pop(occ.bounds_px, None)
                if proof is None:
                    proof = SourceArea(np.zeros(geo.n, bool), occ.bounds_px,
                                       np.full(geo.n, -1, np.int8))
                self.source_proofs[occ.bounds_px] = proof
                while len(self.source_proofs) > 64:
                    self.source_proofs.popitem(last=False)
            candidates = sources[clear]
            unchecked = candidates[~proof.drawn[candidates] & (proof.checked[candidates] < 0)]
            for source in unchecked.tolist():
                proof.affects(geo, source)
            clear &= ~proof.drawn[sources] & (proof.checked[sources] == 0)
            if not clear.any():
                break
        return clear

    def blocked(self, geo, source, targets, occluders, record):
        from app.control import features as cf, heights as hc
        if not len(targets):
            return np.zeros(0, bool)
        a = (*geo.centres[source], cf._eye_body(geo, source, hc.EYE_M))
        b = self.targets[targets]
        # Preserve the diagnostic for every logical test, including memo hits.
        if record is not None:
            flat = np.isnan(a[2]) | np.isnan(b[:, 2])
            if flat.any():
                record['flat_occluder_tests'] = record.get('flat_occluder_tests', 0) + int(flat.sum())
        result = np.zeros(len(targets), bool)
        occluders = [occ for occ in occluders if occ.source_area is None or occ.source_area.affects(geo, source)]
        if not occluders:
            return result
        steps = int(np.ceil(np.hypot(b[:, 0]-a[0], b[:, 1]-a[1]).max())) + 1
        for occ in occluders:
            # Direct/caller-owned mutable masks must always use the uncached path.
            if occ.mask.flags.writeable or occ.bounds_px is None or occ.mask_digest is None:
                result |= cf.blocked_lines([occ], a, b, sampling_steps=steps)
                continue
            key = (occ.mask_digest, occ.bottom, occ.top, occ.all_height, int(source), steps)
            entry = self.entries.pop(key, None)
            if entry is None:
                size = (geo.n + 7) // 8
                entry = (np.zeros(size, np.uint8), np.zeros(size, np.uint8), 2*size + 1024)
                self.bytes += entry[2]
            known, blocked = entry[:2]
            byte, bit = targets // 8, (1 << (targets % 8)).astype(np.uint8)
            missing = (known[byte] & bit) == 0
            self.hits += int((~missing).sum())
            self.misses += int(missing.sum())
            if missing.any():
                hits = cf.blocked_lines([occ], a, b[missing], sampling_steps=steps)
                np.bitwise_or.at(known, byte[missing], bit[missing])
                np.bitwise_or.at(blocked, byte[missing][hits], bit[missing][hits])
            result |= (blocked[byte] & bit) != 0
            self.entries[key] = entry
            while self.bytes > self.limit and self.entries:
                _, removed = self.entries.popitem(last=False)
                self.bytes -= removed[2]
        return result


def cache_for(geo):
    signature = (id(geo.rows), id(geo.centres), id(geo.node_z), id(geo.heights), geo.n,
                 geo.features_sha, geo.height_sha)
    cache = getattr(geo, '_feature_sight_cache', None)
    if cache is None or cache.signature != signature:
        cache = geo._feature_sight_cache = SightCache(geo)
    return cache
