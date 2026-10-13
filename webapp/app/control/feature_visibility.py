"""Round geometry's bounded sight memo and conservative authored source-area hints.

Only compiler-owned, read-only panels are memoized. Pixel sampling in blocked_lines is
batch-dependent: its longest target defines the linspace. That step count is part of
every key, so reuse cannot move a thin panel's intersection or a height-band crossing.
Smokes, target skips and source unions stay outside the memo.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

MAX_CACHE_BYTES = 32 * 1024 * 1024


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
        self.signature = (id(geo.rows), id(geo.centres), id(geo.node_z), id(geo.heights), geo.n)
        self.hits = self.misses = 0
        from app.control import heights as hc
        body = np.full(geo.n, np.nan) if geo.heights is None else geo.node_z + hc.BODY_M
        self.targets = np.column_stack((geo.centres.astype(float), body))
        self.proof_coordinates_valid = bool(((geo.centres >= 0) & (geo.centres <= 1023)).all())

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
    signature = (id(geo.rows), id(geo.centres), id(geo.node_z), id(geo.heights), geo.n)
    cache = getattr(geo, '_feature_sight_cache', None)
    if cache is None or cache.signature != signature:
        cache = geo._feature_sight_cache = SightCache(geo)
    return cache
