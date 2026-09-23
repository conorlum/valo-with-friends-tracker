r"""Arm P5's value table: Part 4's V(a, d, t) with ONE thing changed -- the
pooling ladder key. Declared in docs/superpowers/2026-09-07-predeclared-
values.md, entry "2026-09-19 -- DECLARATION: the post-plant time factor",
section 6.

Part 4 pools a thin (a, d, t) cell by collapsing defenders upward (d, then
3+ together), then by deadline band. The owner's redesign says the state that
matters is the man-advantage DIFFERENTIAL, with a second-order correction for
how many players remain: 1v1 / 2v2 / 3v3 / 4v4 / 5v5 sit within 4.8pp of each
other, while 1v1 and 1v2 differ by 39.4pp.

So the ladder becomes, in order, each rung needing MIN_OBSERVATIONS at the
target second's own counts before smoothing:

    1. exact       (a, d, t)          -- UNCHANGED from Part 4
    2. diff_size   (g, nb, t)
    3. diff        (g, t)
    4. diff_band   (g, band(t))
    5. unsupported

with g = a - d and nb = LOW if a + d <= 5 else HIGH.

Everything else is Part 4's: the 60-observation floor applied to the target
second's own counts, the W=2 moving average whose neighbours contribute at the
TARGET's resolved rung, the analytic V(a, 0, t) = 1.0 endpoint, and the
smoother's inability to withdraw support once the ladder has granted it.

Because rung 1 is untouched, P5 can differ from Part 4 only on cells that fall
BELOW it. That is the point -- it is a claim about thin data -- and it is also
why the runner reports what share of scored kills ever reaches rungs 2 to 4.
"""

from collections import defaultdict

from app.scoring.postplant_value_table import (
    MIN_OBSERVATIONS,
    RUNG_ANALYTIC,
    RUNG_EXACT,
    RUNG_UNSUPPORTED,
    SPIKE_SECONDS,
    ValueLookup,
    band_of,
)

RUNG_DIFF_SIZE = "diff_size"
RUNG_DIFF = "diff"
RUNG_DIFF_BAND = "diff_band"

SIZE_SPLIT = 5  # a + d <= 5 is LOW


def differential(a: int, d: int) -> int:
    return a - d


def size_bucket(a: int, d: int) -> str:
    return "LOW" if a + d <= SIZE_SPLIT else "HIGH"


class DifferentialValueTable:
    """Duck-compatible with ValueTable: `difference()` only ever calls
    `.value(a, d, t)` and reads `.value`/`.rung` off the result."""

    def __init__(self, exact, diff_size, diff, diff_band, w, use_exact_rung=True):
        # use_exact_rung False is arm P5b (the amendment of 2026-09-19): the
        # differential rung becomes rung 1 and the exact (a, d, t) cell is
        # never consulted, so the regrouping applies to the whole scored
        # population instead of only to the cells Part 4 could not support.
        self._use_exact_rung = use_exact_rung
        self._exact = exact          # (a, d, t)   -> (wins, n)
        self._diff_size = diff_size  # (g, nb, t)  -> (wins, n)
        self._diff = diff            # (g, t)      -> (wins, n)
        self._diff_band = diff_band  # (g, bd)     -> (wins, n)
        self._w = w
        self.rung_counts = defaultdict(int)

    def _rung_for(self, a: int, d: int, t: int) -> str:
        g, nb = differential(a, d), size_bucket(a, d)
        if self._use_exact_rung and self._exact.get((a, d, t), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_EXACT
        if self._diff_size.get((g, nb, t), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_DIFF_SIZE
        if self._diff.get((g, t), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_DIFF
        if self._diff_band.get((g, band_of(t)), (0, 0))[1] >= MIN_OBSERVATIONS:
            return RUNG_DIFF_BAND
        return RUNG_UNSUPPORTED

    def _counts_at(self, a: int, d: int, second: int, rung: str):
        g, nb = differential(a, d), size_bucket(a, d)
        if rung == RUNG_EXACT:
            return self._exact.get((a, d, second), (0, 0))
        if rung == RUNG_DIFF_SIZE:
            return self._diff_size.get((g, nb, second), (0, 0))
        return self._diff.get((g, second), (0, 0))

    def _estimate_at(self, a: int, d: int, second: int, rung: str):
        """(rate, weight) for `second`, evaluated at the TARGET's resolved rung
        -- Part 4's rule, so a pooled count is never repeated across the seconds
        it covers and the moving average always averages one estimand."""
        if rung != RUNG_DIFF_BAND:
            wins, n = self._counts_at(a, d, second, rung)
            return (wins / n, n) if n else None
        # Mirror of Part 4's band rung: the estimate is constant within the
        # neighbour's band, weighted by that neighbour's per-second count at
        # the rung below, so smoothing is inert except near a band boundary.
        _, n = self._diff.get((differential(a, d), second), (0, 0))
        if not n:
            return None
        wins_band, n_band = self._diff_band.get(
            (differential(a, d), band_of(second)), (0, 0))
        if not n_band:
            return None
        return (wins_band / n_band, n)

    def _unsmoothed(self, a: int, d: int, t: int, rung: str) -> float:
        if rung == RUNG_DIFF_BAND:
            wins, n = self._diff_band[(differential(a, d), band_of(t))]
        else:
            wins, n = self._counts_at(a, d, t, rung)
        return wins / n

    def value(self, a: int, d: int, t: int) -> ValueLookup:
        if d == 0:
            return ValueLookup(1.0, RUNG_ANALYTIC)

        rung = self._rung_for(a, d, t)
        self.rung_counts[rung] += 1
        if rung == RUNG_UNSUPPORTED:
            return ValueLookup(None, RUNG_UNSUPPORTED)

        total = 0.0
        total_weight = 0.0
        for second in range(t - self._w, t + self._w + 1):
            if second < 0 or second >= int(SPIKE_SECONDS):
                continue  # one-sided at the ends; never widened to compensate
            estimate = self._estimate_at(a, d, second, rung)
            if estimate is None:
                continue
            rate, weight = estimate
            total += rate * weight
            total_weight += weight

        if not total_weight:
            return ValueLookup(self._unsmoothed(a, d, t, rung), rung)
        return ValueLookup(total / total_weight, rung)


def build_differential_value_table(
    observations, w: int, use_exact_rung: bool = True,
) -> DifferentialValueTable:
    exact = defaultdict(lambda: [0, 0])
    diff_size = defaultdict(lambda: [0, 0])
    diff = defaultdict(lambda: [0, 0])
    diff_band = defaultdict(lambda: [0, 0])

    for row in observations:
        won = 1 if row.atk_won else 0
        a, d, t = row.attackers_alive, row.defenders_alive, row.t
        g, nb = differential(a, d), size_bucket(a, d)
        for store, key in (
            (exact, (a, d, t)),
            (diff_size, (g, nb, t)),
            (diff, (g, t)),
            (diff_band, (g, band_of(t))),
        ):
            store[key][0] += won
            store[key][1] += 1

    return DifferentialValueTable(
        {k: tuple(v) for k, v in exact.items()},
        {k: tuple(v) for k, v in diff_size.items()},
        {k: tuple(v) for k, v in diff.items()},
        {k: tuple(v) for k, v in diff_band.items()},
        w,
        use_exact_rung=use_exact_rung,
    )
