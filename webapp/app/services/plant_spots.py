"""Reviewed plant spots (circles or rotated rectangles) and local proposals, without database access.

Centres, radii and rectangle sizes are in the minimap's 0..10000 UV coordinates. Clustering
thresholds are in metres, converted with the map's affine scale. Proposal
names are empty: the owner must name them before saving a reviewed file.
"""
from collections import deque
import hashlib
import json
import math
from pathlib import Path

import numpy as np


_DATA = Path(__file__).resolve().parents[1] / "static" / "data"
_PATH = _DATA / "plant_spots.json"
_FIELDS = ("id", "name", "u", "v", "r")
_RECT_FIELDS = ("id", "name", "u", "v", "w", "h", "angle")  # angle in degrees, clockwise on the minimap


def _number(value, lower, upper, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and lower <= value <= upper and (not positive or value > 0))


def load(path=None):
    """Validate version 1 and return {asset_key: {site: [spot]}}.

    IDs are unique within a site, matching plant_stats. Centres include both
    UV endpoints; radii must be finite and in (0, 10000]. This permits owner
    resizing beyond the proposal clamps while bounding circles to map units.
    Errors include the layout/site so a bad export can be corrected locally.
    """
    source = _PATH if path is None else Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != 1):
        raise ValueError("plant spots: unsupported version (expected 1)")
    layouts = data.get("layouts")
    if not isinstance(layouts, dict):
        raise ValueError("plant spots: layouts must be an object")
    result = {}
    for asset_key, sites in layouts.items():
        if not isinstance(sites, dict):
            raise ValueError(f"{asset_key}: sites must be an object")
        result[asset_key] = {}
        for site, spots in sites.items():
            location = f"{asset_key}/{site}"
            if not isinstance(spots, list):
                raise ValueError(f"{location}: spots must be a list")
            if len(spots) > 10:
                raise ValueError(f"{location}: at most 10 spots per site")
            ids = set()
            validated = []
            for spot in spots:
                required = _RECT_FIELDS if isinstance(spot, dict) and "w" in spot else _FIELDS
                if not isinstance(spot, dict) or any(key not in spot for key in required):
                    raise ValueError(f"{location}: spot requires id, name, u, v, and r or w, h, angle")
                spot_id = spot["id"]
                if not isinstance(spot_id, str) or not spot_id.strip():
                    raise ValueError(f"{location}: spot id must be nonempty text")
                if spot_id in ids:
                    raise ValueError(f"{location}: duplicate spot id {spot_id!r}")
                ids.add(spot_id)
                if not isinstance(spot["name"], str) or not spot["name"].strip():
                    raise ValueError(f"{location}/{spot_id}: name must be nonempty text")
                if not all(_number(spot[key], 0, 10000) for key in ("u", "v")):
                    raise ValueError(f"{location}/{spot_id}: centre must be within 0..10000")
                if "w" in spot:
                    if not all(_number(spot.get(key), 0, 10000, positive=True) for key in ("w", "h")):
                        raise ValueError(f"{location}/{spot_id}: width and height must be within (0, 10000]")
                    if not _number(spot.get("angle"), -180, 180):
                        raise ValueError(f"{location}/{spot_id}: angle must be within -180..180")
                    validated.append({key: spot[key] for key in _RECT_FIELDS})
                    continue
                if not _number(spot["r"], 0, 10000, positive=True):
                    raise ValueError(f"{location}/{spot_id}: radius must be within (0, 10000]")
                validated.append({key: spot[key] for key in _FIELDS})
            result[asset_key][site] = validated
    return result


def digest():
    """SHA-256 of the committed file's bytes, read afresh for cache invalidation."""
    return hashlib.sha256(_PATH.read_bytes()).hexdigest()


def m_per_uv(map_name):
    """Convert minimap UV distances to metres using maps.json's xMultiplier."""
    maps = json.loads((_DATA / "maps.json").read_text(encoding="utf-8"))
    return 1 / (abs(maps[map_name]["xMultiplier"]) * 10000 * 100)


def propose(points, m_per_uv):
    """Propose up to ten circles from one site's N x 2 UV point sequence.

    NumPy DBSCAN uses epsilon 1.5 m, includes each point itself among its
    neighbours, and requires ceil(max(5, .03*N)) samples. Noise is excluded.
    Circles use member means and the 90th-percentile member distance, clamped
    to 1.5..6 metres. Largest clusters come first, ties by centre coordinates.
    IDs are local to this proposal list and names await owner review.

    Neighbours are computed on demand, avoiding an N x N distance matrix.
    """
    if not math.isfinite(m_per_uv) or m_per_uv <= 0:
        raise ValueError("m_per_uv must be finite and positive")
    points = np.asarray(points, dtype=float)
    if points.shape == (0,):
        return []
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("points must be a finite N x 2 UV array")
    n = len(points)
    if n == 0:
        return []
    min_samples = max(5, math.ceil(0.03 * n))
    eps_squared = (1.5 / m_per_uv) ** 2

    def neighbours(index):
        delta = points - points[index]
        return np.flatnonzero(np.einsum("ij,ij->i", delta, delta) <= eps_squared)

    visited = np.zeros(n, dtype=bool)
    labels = np.full(n, -1, dtype=int)
    cluster_count = 0
    for index in range(n):
        if visited[index]:
            continue
        visited[index] = True
        nearby = neighbours(index)
        if len(nearby) < min_samples:
            continue
        labels[index] = cluster_count
        queue = deque(nearby.tolist())
        queued = np.zeros(n, dtype=bool)
        queued[nearby] = True
        while queue:
            member = queue.popleft()
            if not visited[member]:
                visited[member] = True
                nearby = neighbours(member)
                if len(nearby) >= min_samples:
                    queue.extend(nearby[~queued[nearby]].tolist())
                    queued[nearby] = True
            # A point visited earlier as noise may now belong to this cluster.
            if labels[member] == -1:
                labels[member] = cluster_count
        cluster_count += 1

    clusters = []
    for label in range(cluster_count):
        members = points[labels == label]
        centre = members.mean(axis=0)
        clusters.append((len(members), centre, members))
    clusters.sort(key=lambda cluster: (-cluster[0], *cluster[1]))
    spots = []
    for _, centre, members in clusters[:10]:
        distances = np.linalg.norm(members - centre, axis=1)
        radius_m = np.clip(np.percentile(distances, 90) * m_per_uv, 1.5, 6)
        spots.append({"id": f"spot-{len(spots) + 1}", "name": "",
                      "u": float(centre[0]), "v": float(centre[1]),
                      "r": float(radius_m / m_per_uv)})
    return spots
