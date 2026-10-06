"""What utility tells a team about one enemy, as knowledge operations on that enemy's unknown
(docs/superpowers/plans/2026-10-05-replay-control-review-impl.md, W09). Local tooling only, like the engine.

An `Info` says, at its own exact time `t`, something `side` learns about enemy `slot`:

- `locate`: the enemy is exactly at (x, y): their region starts again from there (a sighting). It is locating
  evidence: it joins `Unknown.events`, the stream the gap detector reads, and resets the last-located time.
- `restrict`: the enemy is somewhere in the footprint: everything outside it is dropped. Disconnected parts that
  survive stay apart; nothing is re-centred. Not locating evidence.
- `exclude`: the enemy is not in the footprint: it is dropped. Not locating evidence.
- `hypothesis`: the enemy may also be at (x, y) (a beacon heard, a recall): a second origin, spreading like the
  rest, joined to the region without replacing it. Its source node is protected from the size-only sliver
  cleanup until the team's vision clears it, and never protected again after. Not locating evidence.
- `broaden`: the enemy may be anywhere in the footprint (an unheard teleport): it joins the region at `t`, with
  no protection. Not locating evidence.
- `pause` / `resume`: the enemy can't move in between (Omen's channel): their region doesn't spread, and doesn't
  catch up after. Vision still clears it.

A footprint is a disk of `radius_m` round (x, y), or `mask` (flat nodes) when the reader built one (a line of
sight, a placement disk on several floors). A disk is geometric: through walls, on walkable nodes only.

Readers turn a round's util rows into Infos: `READERS` (filled by the later tasks), each `reader(rnd) -> [Info]`.
Every reader runs on the same `RoundInputs`, so an Info's `t` is in the blob's round seconds, unsnapped.

Equal-time order (`ORDER`): what ends first (resume), then what starts (pause), then information, then cleanup;
within a phase, the order the readers gave."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

KINDS = ("resume", "pause", "locate", "restrict", "exclude", "hypothesis", "broaden")
ORDER = {"resume": 0, "pause": 1, "locate": 2, "restrict": 2, "exclude": 2, "hypothesis": 2, "broaden": 2}
LOCATING = frozenset({"locate"})


@dataclass
class Info:
    t: float
    side: str                     # the team that learns it
    slot: int                     # the enemy it is about
    kind: str                     # one of KINDS
    reason: str                   # what the reasons list shows: "haunt", "neural_theft", "knife_zero", ...
    x: float | None = None        # px
    y: float | None = None
    radius_m: float | None = None
    mask: np.ndarray | None = None
    source: str = ""              # an id for the utility that caused it (a cast's actor), for provenance
    seq: int = 0                  # the reader's order, for equal times
    z: float | None = None        # the place's height (m above the map's origin), for the node of a point
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown knowledge operation {self.kind!r}")


READERS: list = []


def read_all(rnd) -> list[Info]:
    """Every reader's Infos for the round, in engine order (`ORDER`)."""
    out: list[Info] = []
    for reader in READERS:
        out.extend(reader(rnd))
    return ordered(out)


def ordered(infos) -> list[Info]:
    infos = list(infos)
    for i, info in enumerate(infos):
        info.seq = i
    return sorted(infos, key=lambda i: (i.t, ORDER[i.kind], i.seq))


def footprint(info: Info, geo) -> np.ndarray:
    """The Info's nodes: its own mask, else the walkable nodes within radius_m of (x, y) px, through walls."""
    if info.mask is not None:
        return info.mask.astype(bool) & geo.walk_n
    if info.x is None or info.y is None or info.radius_m is None:
        return np.zeros(geo.n, bool)
    r = info.radius_m / geo.m_per_px
    near = (geo.centres[:, 0] - info.x) ** 2 + (geo.centres[:, 1] - info.y) ** 2 <= r * r
    return near & geo.walk_n
