"""Back-shots (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 6), found after the round's
last tick from the detector's complete locating history.

A back-shot is a gun damage run (ability damage never) whose attacker is within the victim's rear 120 degrees
at the run's start, judged by the victim's facing and both real positions then; no clear line is needed, so a
wall-bang counts and is tagged. It is recorded only when the victim's team had not located the shooter for
MIN_UNSEEN_S before the run (or at all this round), ignoring the shooter's own gunfire in the SHOT_LOOKBACK_S
before it (the shot that did the damage). Missing positions at the run's start: nothing is recorded, and the
case is counted. Follow-up damage is no new row: the run's own damage locates the shooter (section 4).

The path is the shooter's real track from where the team last located them (round start if never) to the run,
kept in unbroken pieces (a track-segment break is never joined); its choke sequence is known only for a path in
one piece, else null and counted. The peak speed is the highest `RoundInputs.speed` along every piece (R16).
`distance_m` is the spot (the shooter's node centre) to the victim; the candidate distance is the shooter's real
position to that spot (R16).

Linking: to an open predicted gap on the victim whose candidates include the shooter by then; the one with the
same choke sequence first, else the nearest spot. Levels: a predicted gap's `shot` is its first back-shot by a
candidate while it was open or within RESULT_WINDOW_S of that enemy standing in it; a back-shot itself fills
only `killed_*` (its shooter killed the victim within RESULT_WINDOW_S). The gaps' `killed_*` and
`victim_won_at` are set once, by `GapDetector._levels` (after `add`), on the same window as `shot`."""

from __future__ import annotations

import numpy as np

from app.control import chokes
from app.control.engine import AUDIBLE_WINDOW_S, PX
from app.gaps.detect import (BEHIND_DEG, RESULT_WINDOW_S, ROUTE_THIN_S, SHOT_LOOKBACK_S, Gap, is_open, off_facing,
                             round_context)
from app.replays import choke_assets

NO_POSITION = "back-shot check without a position (nothing recorded)"
BROKEN_PATH = "back-shot path with a break (choke sequence unknown)"


def choke_path(nodes, node_choke: np.ndarray) -> tuple:
    """The ordered chokes a node path crosses, a choke counted once per crossing."""
    out: list[int] = []
    for node in nodes:
        c = int(node_choke[int(node)])
        if c >= 0 and (not out or out[-1] != c):
            out.append(c)
    return tuple(out)


def _path(rnd, slot: int, ta: float, tb: float) -> tuple[list, list, float | None]:
    """The real track of `slot` over [ta, tb] as (pieces of [t, x px, y px] thinned to ROUTE_THIN_S, pieces of
    the node at every sample, the peak speed along them in m/s or None). A piece is one track segment."""
    tr = rnd.tracks.get(slot)
    if tr is None:
        return [], [], None
    ts, seg = tr[0], rnd._segment[slot]
    idx = np.flatnonzero((ts >= ta - 1e-9) & (ts <= tb + 1e-9))
    pieces, node_pieces, peak = [], [], None
    for s in dict.fromkeys(seg[idx].tolist()):
        pts, nodes, last = [], [], None
        piece = idx[seg[idx] == s]
        for k, i in enumerate(piece.tolist()):
            t = float(ts[i])
            x, y = float(tr[1][i]) * PX / 10000, float(tr[2][i]) * PX / 10000
            nodes.append(rnd.node(slot, t, x, y))
            if last is None or t - last >= ROUTE_THIN_S or k == len(piece) - 1:
                pts.append([round(t, 3), round(x, 1), round(y, 1)])
                last = t
            if t - AUDIBLE_WINDOW_S >= ta - 0.5 / rnd.hz:      # the speed window lies on the path
                v = rnd.speed(slot, t)
                if v is not None and (peak is None or v > peak):
                    peak = v
        pieces.append(pts)
        node_pieces.append(nodes)
    return pieces, node_pieces, peak


def _open_with(g: Gap, victim: int, life: int, shooter: int, t: float) -> bool:
    """g is a predicted gap on this victim life, open at t, with the shooter a candidate by then."""
    joined = g.joined.get(shooter)
    return (g.kind == "predicted" and g.victim == victim and g.life == life and joined is not None
            and joined <= t and is_open(g, t))


def link(geo, gaps: list, victim: int, life: int, shooter: int, t: float, seq: tuple | None,
         x: float, y: float) -> Gap | None:
    """The open predicted gap a back-shot at t from (x, y) px links to: same choke sequence first, else the
    nearest spot (ties: the earlier gap); None when no open gap on the victim has the shooter."""
    open_now = [g for g in gaps if _open_with(g, victim, life, shooter, t)]
    if not open_now:
        return None
    same = [g for g in open_now if seq is not None and g.choke_seq == seq]
    if same:
        return same[0]
    return min(open_now, key=lambda g: (float(np.hypot(*(geo.centres[g.spot] - (x, y)))), g.t_open))


def mark_shots(gaps: list, shots: list) -> None:
    """Each predicted gap's first back-shot by a candidate (on the list by then) while the gap was open, or
    within RESULT_WINDOW_S after that enemy stood in it, in the same victim life."""
    for g in gaps:
        if g.kind != "predicted":
            continue
        for b in sorted(shots, key=lambda b: b.t_open):
            shooter = next(iter(b.candidates))
            joined = g.joined.get(shooter)
            if b.victim != g.victim or b.life != g.life or joined is None or joined > b.t_open:
                continue
            open_then = is_open(g, b.t_open)
            near_stand = any(s <= b.t_open <= s + RESULT_WINDOW_S for s in g.stood_times.get(shooter, ()))
            if open_then or near_stand:
                g.shot_at, g.shot_by = float(b.t_open), int(shooter)
                break


def add(det) -> None:
    rnd, geo = det.rnd, det.geo
    node_choke = chokes.node_chokes(geo, choke_assets.load(geo.name))
    predicted = [g for g in det.gaps if g.kind == "predicted"]
    shots: list[Gap] = []
    for t0, _, by, target, wall in sorted(rnd.gun_runs):
        if t0 >= rnd.t_decided:                            # nothing is detected once the round is decided
            continue
        pv, pe = rnd.pos(target, t0), rnd.pos(by, t0)
        if pv is None or pe is None:
            det._note(NO_POSITION, target if pv is None else by)
            continue
        angle = off_facing(pv[2], pv[0], pv[1], pe[0], pe[1])
        if angle <= BEHIND_DEG:
            continue
        side = rnd.team[target]
        lookback = t0 - SHOT_LOOKBACK_S
        if not det.wait_over(side, by, t0, inclusive=False, ignore_gunfire_from=lookback):
            continue
        # two runs by one shooter on one victim starting at the same time: the first's damage locating is not
        # before the second (inclusive=False), so only this keeps them one row. Later follow-ups fail the wait.
        if any(b.victim == target and by in b.candidates and b.t_open == t0 for b in shots):
            continue
        since = det.last_located(side, by, t0, inclusive=False, ignore_gunfire_from=lookback)
        pieces, node_pieces, peak = _path(rnd, by, rnd.t_start if since is None else since, t0)
        seq = choke_path(node_pieces[0], node_choke) if len(node_pieces) == 1 else None
        if len(node_pieces) > 1:
            det._note(BROKEN_PATH, by)
        spot = rnd.node(by, t0, pe[0], pe[1])
        sx, sy = (float(c) for c in geo.centres[spot])
        life = det._life(target, t0)
        b = Gap("backshot", int(target), side, -1, seq, float(t0), int(spot), int(rnd.node(target, t0, pv[0], pv[1])),
                round(det._metres(pv[0], pv[1], sx, sy), 2), round(angle, 1), pieces, life=life,
                candidates={int(by): round(det._metres(pe[0], pe[1], sx, sy), 2)})
        b.context = {**round_context(rnd, t0, None), "wall": bool(wall),
                     "peak_speed_mps": None if peak is None else round(float(peak), 2)}
        for kt, killer, victim in sorted(rnd.kills):
            # a kill after t_decided never counts (final review M4)
            if killer == by and victim == target and t0 <= kt <= min(t0 + RESULT_WINDOW_S, rnd.t_decided):
                b.killed_at, b.killed_by = float(kt), int(by)
                break
        b.linked = link(geo, predicted, target, life, by, t0, seq, pe[0], pe[1])
        shots.append(b)
    mark_shots(predicted, shots)
    det.gaps.extend(shots)
