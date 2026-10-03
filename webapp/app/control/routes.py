"""The unknown's route history (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 4): an
append-only log of arrivals per team. An entry is (node, arrival time, parent entry, choke sequence id); a
parent is always an earlier entry, so a route never loops and is never rewritten. Choke sequences are
interned: id 0 is the empty sequence."""

from __future__ import annotations

import numpy as np


class RouteLog:
    def __init__(self, capacity: int = 4096):
        self._node = np.zeros(capacity, np.int64)
        self._t = np.zeros(capacity, np.float64)
        self._parent = np.zeros(capacity, np.int64)
        self._seq = np.zeros(capacity, np.int32)
        self._n = 0
        self.seqs: list[tuple[int, ...]] = [()]
        self._seq_id: dict[tuple[int, ...], int] = {(): 0}

    def __len__(self) -> int:
        return self._n

    def _grow(self) -> None:
        for name in ("_node", "_t", "_parent", "_seq"):
            old = getattr(self, name)
            new = np.zeros(max(len(old) * 2, 1), old.dtype)
            new[: len(old)] = old
            setattr(self, name, new)

    def add(self, node: int, t: float, parent: int, choke: int) -> int:
        if self._n == len(self._node):
            self._grow()
        sid = int(self._seq[parent]) if parent >= 0 else 0
        if choke >= 0:
            base = self.seqs[sid]
            if not base or base[-1] != choke:
                seq = base + (choke,)
                sid = self._seq_id.get(seq)
                if sid is None:
                    sid = self._seq_id[seq] = len(self.seqs)
                    self.seqs.append(seq)
        i = self._n
        self._node[i], self._t[i], self._parent[i], self._seq[i] = node, t, parent, sid
        self._n += 1
        return i

    @property
    def node(self) -> np.ndarray:
        return self._node[: self._n]

    @property
    def t(self) -> np.ndarray:
        return self._t[: self._n]

    @property
    def parent(self) -> np.ndarray:
        return self._parent[: self._n]

    @property
    def seq(self) -> np.ndarray:
        return self._seq[: self._n]

    def trace(self, entry: int) -> list[int]:
        """Entry ids from the route's source to `entry`."""
        out = []
        while entry >= 0:
            out.append(int(entry))
            entry = int(self._parent[entry])
        return out[::-1]
