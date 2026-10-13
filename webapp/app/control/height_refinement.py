"""Deterministic local height-refinement evidence, before mixed-footprint runtime activation.

This analysis consumes the exact accepted stand/walk footprints. It neither changes
the coarse asset nor certifies a map for release. History is an explicit per-map input.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json

import numpy as np

from app.control import height_build as hb, height_motion as hm, heights as hc
from app.control.geometry import CELL, GRID


def history(map_name: str, parents=()) -> dict:
    """Canonical persisted selection; no worker-local previous-asset lookup."""
    parents = list(parents)
    if any(type(c) is not int or not 0 <= c < GRID * GRID for c in parents):
        raise ValueError('invalid refinement parent')
    return {'v': 1, 'map': map_name, 'child_px': CELL // 2, 'parents': sorted(set(parents))}


def identity(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
                          .encode('utf-8')).hexdigest()


def _child_ids(parent):
    y, x = divmod(parent, GRID)
    return [(y * 2 + dy) * (GRID * 2) + x * 2 + dx for dy in (0, 1) for dx in (0, 1)]


def observations(stands, walks, round_match):
    """One observation per accepted run/child, including runs split around platforms.

    A stand keeps its accepted run median; a walk keeps its lowest accepted child
    position-z. No per-frame votes, whole-run mean-XY reconstruction or new flight filter.
    """
    grouped = {}
    for stand, found in ((True, stands), (False, walks)):
        for run in found:
            if len(run.fine_cells) == 0:
                continue
            values = [run.z] * len(run.fine_cells) if stand else run.fine_low
            if len(values) != len(run.fine_cells):
                raise ValueError('fine footprint/height evidence mismatch')
            accepted = set(run.cells)
            for cell, z in zip(run.fine_cells, values):
                if type(cell) is not int or not 0 <= cell < (GRID * 2) ** 2 \
                        or hm.coarse_of_fine(cell) not in accepted:
                    raise ValueError('fine evidence lies outside accepted coarse footprint')
                key = (run.round, run.slot, run.t0, run.t1, stand, cell)
                grouped[key] = min(int(z), grouped.get(key, int(z)))
    by_child = defaultdict(list)
    for (ri, slot, t0, t1, stand, cell), z in sorted(grouped.items()):
        by_child[cell].append((z, ri, round_match[ri], stand))
    return by_child


def analyze(geo, stands, walks, round_match, parent_floors, parent_reasons, previous):
    """Propose 4-pixel children on any map; retained selections never silently shrink.

    The caller must supply a history, including an explicit empty one on the first
    build. Unknown children stay unknown. Spatially identical stacked-floor evidence
    keeps a reliable parent coarse.
    """
    if not isinstance(previous, dict) or previous.get('v') != 1 or previous.get('map') != geo.name \
            or previous.get('child_px') != CELL // 2 or not isinstance(previous.get('parents'), list):
        raise ValueError('explicit compatible per-map refinement history required')
    prior = history(geo.name, previous['parents'])
    if previous != prior:
        raise ValueError('refinement history must be canonical')
    by_child = observations(stands, walks, round_match)
    direct = {hm.coarse_of_fine(c) for c in by_child}
    conflicts = {c for c, reason in parent_reasons.items()
                 if reason in (hb.TOO_CLOSE, hb.SPREAD, hb.TOO_MANY, hb.NEIGHBOURS_DISAGREE)}
    multi = {c for c, floors in parent_floors.items() if len(floors) > 1}
    candidates = sorted(((conflicts | multi) & direct) | set(prior['parents']))
    chosen, checked, records = set(prior['parents']), [], {}
    for parent in candidates:
        if not geo.walk.ravel()[parent]:
            # Retain history across mask revisions, but do not make a removed region walkable.
            continue
        children = []
        for child in _child_ids(parent):
            rows = sorted(by_child.get(child, []))
            if rows:
                z, rounds, matches, stand = (np.asarray(col) for col in zip(*rows))
                floors, why, kind = hb.group_cell(z.astype(float), rounds, matches, stand.astype(bool))
            else:
                floors, why, kind = [], hb.NO_SAMPLES, hc.KIND_NONE
            if not floors and why is None:
                why = 'insufficient support'
            y, x = divmod(child, GRID * 2)
            children.append({'cell': child, 'bbox_px': [x * 4, y * 4, x * 4 + 4, y * 4 + 4],
                'floors_world_dm': [[int(z), int(spread)] for z, spread in floors],
                'kind': int(kind), 'reason': why, 'observations': len(rows)})
        signatures = {tuple(f[0] for f in c['floors_world_dm']) for c in children if c['floors_world_dm']}
        # Distinct supported child surfaces are evidence of an internal spatial boundary.
        # Minor quantization/tolerance changes on one flat surface do not justify splitting.
        spatial = any(len(a) != len(b) or any(abs(za - zb) > hc.FLOOR_TOL_M * 10 for za, zb in zip(a, b))
                      for a in signatures for b in signatures)
        # A known ambiguous footprint should retain finer sampling even before enough
        # children resolve. No-sample parents are excluded above; reliable stacked
        # floors are still kept coarse unless child evidence proves a spatial boundary.
        ambiguous = parent in conflicts
        select = parent in chosen or spatial or ambiguous
        checked.append({'parent': parent, 'selected': select, 'spatial_distinction': spatial,
                        'ambiguous_parent': ambiguous})
        if select:
            chosen.add(parent)
            records[parent] = children
    result_history = history(geo.name, chosen)
    walkable = [p for p in sorted(chosen) if geo.walk.ravel()[p]]
    children = [c for p in walkable for c in records[p]]
    support = sum(bool(c['floors_world_dm']) for c in children)
    input_rows = [[int(cell), sorted([list(row) for row in rows])] for cell, rows in sorted(by_child.items())]
    return {'v': 1, 'map': geo.name, 'runtime_enabled': False,
        'qualification': 'accepted-ground refinement analysis; mixed-footprint runtime not qualified',
        'previous_history': prior, 'previous_history_sha256': identity(prior),
        'history': result_history, 'history_sha256': identity(result_history),
        'evidence_sha256': identity({'children': input_rows}),
        'checked': checked, 'children': children,
        'area': {'selected_parent_equivalents': len(walkable), 'selected_px2': len(walkable) * CELL ** 2,
                 'supported_child_px2': support * (CELL // 2) ** 2,
                 'unknown_child_px2': (len(children) - support) * (CELL // 2) ** 2}}
