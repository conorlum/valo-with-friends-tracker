"""seen_from checks only the statically visible pairs, smoke by smoke, instead of an S x N matrix per smoke
(2026-10-30: the matrix was a third of a real round's compute). The answers must be the matrix's, bit for bit."""

import numpy as np

from app.control import engine as ce
from app.control.geometry import Wall
from tests.replays.control_toys import open_hall


def matrix_seen_from(geo, src, smokes, skip=None):
    """seen_from as it was: every source x every candidate target, through every smoke."""
    src = src[geo.row_of[src] >= 0]
    packed = geo.rows[geo.row_of[src]]
    static = np.unpackbits(np.bitwise_or.reduce(packed, axis=0))[: geo.n].astype(bool)
    cand = static & ~skip if skip is not None else static.copy()
    targets = np.flatnonzero(cand)
    out = static & ~cand
    q = geo.centres[targets]
    seen_t = np.zeros(len(targets), bool)
    for lo in range(0, len(src), 256):
        rows = np.unpackbits(packed[lo:lo + 256], axis=1)[:, : geo.n][:, targets].astype(bool)
        p = geo.centres[src[lo:lo + 256]]
        for smoke in smokes:
            rows &= ~ce.smoke_blocks(p, q, smoke)
        seen_t |= rows.any(0)
    out[targets] = seen_t
    return out


def test_pairs_match_the_matrix_through_hollow_and_solid_smokes_and_a_wall():
    geo = open_hall()
    walk = np.flatnonzero(geo.walk.ravel())
    rng = np.random.default_rng(7)
    smokes = [(200.0, 200.0, 24.0, False), (330.0, 150.0, 18.0, True),
              Wall(segs=np.array([[260.0, 120.0, 260.0, 280.0], [260.0, 280.0, 300.0, 290.0]]))]
    for n_src in (1, 5, 300):                          # 300: two blocks of sources, the second pruned
        src = rng.choice(walk, n_src, replace=False)
        skip = np.zeros(geo.n, bool)
        skip[rng.choice(walk, len(walk) // 4, replace=False)] = True
        for these in ([smokes[0]], [smokes[1]], [smokes[2]], smokes):
            for sk in (None, skip):
                got = ce.seen_from(geo, src, these, sk)
                assert (got == matrix_seen_from(geo, src, these, sk)).all(), (n_src, len(these), sk is None)
