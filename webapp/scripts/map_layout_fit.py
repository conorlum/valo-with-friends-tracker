"""Fit a picture's floor mask onto our minimap's floor mask, and diff the two.

Ported from the Corrode fit (align.py, diff_check.py, occupancy.py). Offline tooling: numpy, Pillow and scipy,
never imported by app/replays (the worker keeps that standard-library only).

Frame: our minimap is size x size (1024); a floor mask is a bool array, True where there is floor.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

BACKGROUND = (9, 15, 20)


def picture_floor_mask(rgb: np.ndarray, background=BACKGROUND, tol: int = 18) -> np.ndarray:
    """Any pixel not the background colour is floor; one closing pass (enclosed walls stay background)."""
    arr = np.asarray(rgb).astype(int)[..., :3]
    mask = np.abs(arr - np.array(background)).sum(axis=2) > tol
    return ndimage.binary_closing(mask, iterations=1)


def ours_floor_mask(rgba: np.ndarray) -> np.ndarray:
    return np.asarray(rgba)[..., 3] > 0


def warp(img: Image.Image, rot: int, scale: float, tx: float, ty: float, size: int = 1024,
         resample=Image.NEAREST) -> Image.Image:
    """The picture rotated by rot*90 CCW, scaled, its top-left placed at (tx, ty) on a size x size canvas."""
    img = img.rotate(90 * rot, expand=True)
    w, h = img.size
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), resample)
    canvas = Image.new(img.mode, (size, size))
    canvas.paste(img, (round(tx), round(ty)))
    return canvas


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max(1, (a | b).sum()))


def warp_mask(mask: np.ndarray, rot: int, scale: float, tx: float, ty: float, size: int = 1024) -> np.ndarray:
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    return np.array(warp(img, rot, scale, tx, ty, size)) > 0


def fit_mask(pic: np.ndarray, ours: np.ndarray, size: int = 1024, scales=None,
             coarse_shift: int = 12, coarse_step: int = 4) -> dict:
    """Rotation (x90 CCW), scale and shift of pic onto ours, best IoU. Returns rot, scale, tx, ty, iou."""
    if scales is None:
        scales = np.arange(1.0, 1.8, 0.01)
    fm = Image.fromarray(pic.astype(np.uint8) * 255)
    oy, ox = np.nonzero(ours)
    best = None
    for rot in range(4):
        ys, xs = np.nonzero(np.array(fm.rotate(90 * rot, expand=True)))
        for scale in scales:
            # bounding boxes' centres coincide, then a coarse shift search
            cx = (ox.min() + ox.max()) / 2 - (xs.min() + xs.max()) / 2 * scale
            cy = (oy.min() + oy.max()) / 2 - (ys.min() + ys.max()) / 2 * scale
            for dx in range(-coarse_shift, coarse_shift + 1, coarse_step):
                for dy in range(-coarse_shift, coarse_shift + 1, coarse_step):
                    s = iou(warp_mask(pic, rot, scale, cx + dx, cy + dy, size), ours)
                    if best is None or s > best[0]:
                        best = (s, rot, float(scale), cx + dx, cy + dy)
    _, rot, scale, tx, ty = best
    for dx in np.arange(-4, 4.5, 0.5):        # fine shift and scale
        for dy in np.arange(-4, 4.5, 0.5):
            for ds in (-0.005, -0.0025, 0, 0.0025, 0.005):
                v = iou(warp_mask(pic, rot, scale + ds, tx + dx, ty + dy, size), ours)
                if v > best[0]:
                    best = (v, rot, scale + ds, tx + dx, ty + dy)
    s, rot, scale, tx, ty = best
    return {"rot": int(rot), "scale": float(scale), "tx": float(tx), "ty": float(ty), "iou": float(s)}


def diff_regions(ours: np.ndarray, new: np.ndarray, min_px: int = 60, open_iterations: int = 2) -> list[dict]:
    """Changed regions between two floor masks as connected components.

    'removed' = floor on ours, wall in the picture; 'added' = wall on ours, floor in the picture. Edge slivers
    (1-2 px from a resample) are dropped by an opening, components under min_px are skipped. Each region has
    kind, area, bbox (x0, y0, x1, y1 inclusive) and mask (bool array, not for JSON). Largest first.
    """
    out = []
    for kind, mask in (("removed", ours & ~new), ("added", ~ours & new)):
        core = ndimage.binary_opening(mask, iterations=open_iterations) if open_iterations else mask
        lab, n = ndimage.label(core)
        for i in range(1, n + 1):
            comp = lab == i
            area = int(comp.sum())
            if area < min_px:
                continue
            ys, xs = np.nonzero(comp)
            out.append({"kind": kind, "area": area,
                        "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), "mask": comp})
    out.sort(key=lambda r: -r["area"])
    return out


def count_positions(regions: list[dict], positions, size: int | None = None) -> list[int]:
    """For each region, how many (x, y) positions (minimap pixels) fall inside it."""
    counts = []
    for r in regions:
        m = r["mask"]
        h, w = m.shape
        counts.append(sum(1 for x, y in positions if 0 <= int(y) < h and 0 <= int(x) < w and m[int(y), int(x)]))
    return counts


def diff_image(ours: np.ndarray, new: np.ndarray) -> np.ndarray:
    """RGB: grey both, red floor on ours only (removed), green floor in the picture only (added)."""
    img = np.zeros(ours.shape + (3,), np.uint8)
    img[ours & new] = (110, 110, 110)
    img[ours & ~new] = (230, 60, 60)
    img[~ours & new] = (60, 200, 90)
    return img


FLOOR_RGB = (118, 118, 118)
OUTER_PX = 2


def layout_minimap(old_rgba: np.ndarray, picture_floor: np.ndarray, regions: np.ndarray) -> np.ndarray:
    """The layout's minimap (RGBA) in the vendored style: inside `regions`, floor of the aligned picture is
    (118,118,118) with its outer 2 px white and everything else transparent wall; outside them the old
    minimap's pixels are kept."""
    out = np.array(old_rgba, dtype=np.uint8, copy=True)
    floor = np.asarray(picture_floor, bool)
    inner = ndimage.binary_erosion(floor, iterations=OUTER_PX)
    inside = np.asarray(regions, bool)
    out[inside] = (0, 0, 0, 0)
    out[inside & floor] = (*FLOOR_RGB, 255)
    out[inside & floor & ~inner] = (255, 255, 255, 255)
    return out


def callouts_in(callouts: list[dict], regions: np.ndarray, uv_scale: int = 10000) -> list[dict]:
    """The callouts (u, v in 0..uv_scale) whose minimap pixel lies inside the region mask."""
    h, w = regions.shape
    found = []
    for c in callouts:
        x, y = int(c["u"] / uv_scale * w), int(c["v"] / uv_scale * h)
        if 0 <= y < h and 0 <= x < w and regions[y, x]:
            found.append(c)
    return found
