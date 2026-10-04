"""What the control engine shows an observer each tick (docs/superpowers/specs/2026-10-02-timing-gaps-design.md,
section 3): plain values, taken after the tick's unknown is applied. The arrays are the engine's own: an
observer must use or copy them before returning, and must not change them."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PlayerView:
    slot: int
    team: str
    node: int
    x: float
    y: float
    yaw: float
    live: np.ndarray        # Tick.live: view (active, passive, presence), utility and own node; no memory
    utility: np.ndarray     # Holder.watch
    view: np.ndarray | None = None   # Tick.view: active | passive (presence included), before Memory (R12)
    eye_z: float | None = None       # Tick._eye at their node: None on a flat map or an unresolved cell (R13)


@dataclass
class TickRecord:
    t: float
    players: dict           # slot -> PlayerView (live players with a position: Tick.holders)
    unknown: dict           # side -> enemy slot -> entry id per node (-1 outside); Unknown.entry
    events: dict            # side -> [(enemy, time, kind)]; Unknown.events of this tick
    smokes: list


def record(tick, unknown) -> TickRecord:
    players = {}
    for s, h in tick.holders.items():
        p = tick.rnd.pos(s, tick.t)
        players[s] = PlayerView(s, h.team, int(h.cell), float(h.x), float(h.y), float(p[2]) if p else 0.0,
                                tick.live[s], h.watch, tick.view.get(s), tick._eye(s, tick.t, h.cell))
    return TickRecord(float(tick.t), players, unknown.entry, {k: list(v) for k, v in unknown.events.items()},
                      list(tick.smokes))
