"""Fight swing (docs/superpowers/specs/2026-10-23-fight-swing-design.md): the map control each side lost in a team
fight, over the ground the fight was about. Pure: numpy only, no geometry, so the engine passes plain inputs.

A team fight is the round's live deaths chained while each is within CHAIN_S of the previous one. Its ground is
every walkable cell within NEAR_M of a death, plus a dead setup sentinel's removal mask (their trips and camera
going down) where their team held or contested the cell. Each side's lost ground is what it held there before
minus after; the swing for a side is the enemy's lost minus its own (app/services/replay_moments.py).
"""

from __future__ import annotations

import numpy as np

CHAIN_S = 6.0        # the repo's trade window (app/moments/constants.py TRADE_WINDOW_S)
PRE_S = 1.0          # "before" ends this long before the first death
POST_S = 5.0         # "after" ends this long after the last death, at most at t_decided
AVG_S = 2.0          # each end is the mean over this long of stored frames
NEAR_M = 20.0        # a death's ground: walkable cells within this, flat distance
HELD_LEAD_S = 0.5    # a dead sentinel's removal mask is read this long before the death
SETUP_AGENTS = frozenset({"Chamber", "Cypher", "Deadlock", "Killjoy", "Veto", "Vyse"})   # sentinels but Sage

# the engine's state codes (engine.NONE ... CONTESTED), repeated so this module needs no engine import
_HELD = {"A": (1, 3), "B": (4, 6)}
_CONTESTED = (7, 8)


def chain(deaths: list[tuple[int, float]]) -> list[list[tuple[int, float]]]:
    """Deaths sorted by time -> team fights, each a list of (slot, t)."""
    out: list[list[tuple[int, float]]] = []
    for slot, t in sorted(deaths, key=lambda d: (d[1], d[0])):
        if out and t - out[-1][-1][1] <= CHAIN_S:
            out[-1].append((slot, t))
        else:
            out.append([(slot, t)])
    return out


def left_ends(alive: dict) -> set[tuple[int, float]]:
    """(slot, t) of every life the round's `alive` rows end with cause "left" (a disconnect, not a death). A life the
    link's DB death cut short ends at another time, so it stays a death."""
    return {(int(s), round(float(iv[1]), 3)) for s, ivs in (alive or {}).items() for iv in ivs
            if len(iv) > 2 and iv[2] == "left" and iv[1] is not None}


def _held(states: np.ndarray, group: str) -> np.ndarray:
    lo, hi = _HELD[group]
    return (states >= lo) & (states <= hi)


def round_fights(*, deaths, team, agents, alive, pos_m, frames, states, control_masks, cell_xy_m, cell_m2,
                 t_start, t_decided, left=frozenset()) -> list[dict]:
    """Every live team fight of the round.

    deaths: [(slot, t)], every life's end; left: the disconnects among them (`left_ends`), which are dropped; team: slot -> "A"/"B"; agents: slot -> agent name; alive(slot, t) -> bool; pos_m(slot, t)
    -> (x, y) in metres or None; frames: stored frame times (s); states: frames x walkable cells, the engine's
    codes; control_masks: frames x 10 x walkable cells; cell_xy_m: walkable cells x 2, metres."""
    frames = np.asarray(frames, float)
    if len(frames) == 0:
        return []
    live = [(s, t) for s, t in deaths
            if t_start <= t <= t_decided and s in team and (s, round(float(t), 3)) not in left]
    out = []
    for fight in chain(live):
        first, last = fight[0][1], fight[-1][1]
        t_before = max(first - PRE_S, frames[0])
        t_after = max(min(last + POST_S, t_decided, frames[-1]), t_before)
        # frames in [end - AVG_S, end]: never one past an end that falls between frames
        before = np.arange(np.searchsorted(frames, t_before - AVG_S), np.searchsorted(frames, t_before, side="right"))
        after = np.arange(np.searchsorted(frames, t_after - AVG_S), np.searchsorted(frames, t_after, side="right"))
        before, after = before[before < len(frames)], after[after < len(frames)]
        if len(before) == 0:
            before = np.array([0])
        if len(after) == 0:
            after = np.array([len(frames) - 1])

        area = np.zeros(states.shape[1], bool)
        for slot, t in fight:
            p = pos_m(slot, t - 0.01)
            if p is not None:
                area |= np.hypot(cell_xy_m[:, 0] - p[0], cell_xy_m[:, 1] - p[1]) <= NEAR_M
            if agents.get(slot) in SETUP_AGENTS:
                k = max(0, int(np.searchsorted(frames, t - HELD_LEAD_S, side="right")) - 1)   # last frame at or before
                theirs = _held(states[k], team[slot]) | np.isin(states[k], _CONTESTED)
                area |= control_masks[k, slot] & theirs

        lost = {}
        for group in ("A", "B"):
            mean = lambda ix: float(np.mean([(_held(states[i], group) & area).sum() for i in ix]))  # noqa: E731
            lost[group] = round((mean(before) - mean(after)) * cell_m2, 1)
        wipe = any(not any(alive(s, last + 0.01) for s, g in team.items() if g == group) for group in ("A", "B"))
        out.append({"t0": round(first, 3), "t1": round(last, 3),
                    "deaths": [[int(s), round(float(t), 3), team[s]] for s, t in fight],
                    "wipe": bool(wipe), "measured": [round(float(t_before), 3), round(float(t_after), 3)],
                    "area_m2": round(float(area.sum()) * cell_m2, 1), "lost_m2": lost})
    return out
