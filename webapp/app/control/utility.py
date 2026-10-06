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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_FIGURES_FILE = json.loads((Path(__file__).with_name("utility.json")).read_text(encoding="utf-8"))
# Only the numbers (utility.json's `sources` and notes are not consumed): the semantic view W22 fingerprints.
FIGURES = {key: ({k: float(v) for k, v in value.items()} if isinstance(value, dict) else float(value))
           for key, value in _FIGURES_FILE.items() if key not in ("sources", "PROVISIONAL")}

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


# ---------------------------------------------------------------- Veto's Evolution (W10)

# What enemy utility can still do to an ulting Veto (the 2026-10-05 review, item 19). True: it works on him.
CAPABILITIES = {
    "sight": True,        # a player's own view sees him
    "drone_sight": True,  # a drone's or dog's cone sees him (its tag doesn't)
    "trigger": True,      # a Cypher trip still goes off on him (no concuss)
    "blind": False,       # flashes, nearsight
    "suppress": False,    # KAY/O's knife and NULL/cmd, any suppression
    "reveal": False,      # recon, Haunt, darts' and drones' tags, Neural Theft, Fade's and Gekko's utility
    "status": False,      # concuss, slow, tether, decay: what utility puts on a player
}
# PROVISIONAL(D6): no local replay has a Veto, so Evolution's own archetype has never been seen. Any of Veto's
# (`Pine`) ult-slot objects is read as Evolution being active for its life. The plan's evidence task: confirm the
# name and its life on a replay with an ulting Veto.
EVOLUTION = re.compile(r"^Pine_X_")
VETO = "Veto"


def evolution_spans(blob: dict) -> dict[int, list[tuple[float, float]]]:
    """slot -> [(from, to)] round seconds while that player was in Evolution: an ult-slot ability row of a Veto
    (EVOLUTION), owned by them, from its start to its end. Nothing for anyone else."""
    vetos = {p["slot"] for p in blob.get("players") or [] if p.get("agent") == VETO}
    out: dict[int, list[tuple[float, float]]] = {}
    for e in blob.get("util") or []:
        if e.get("k") != "ability" or e.get("by") not in vetos:
            continue
        if not EVOLUTION.match(f"{e.get('code')}_{e.get('name')}"):
            continue
        end = e.get("t1") if e.get("t1") is not None else blob.get("t_end")
        if end is not None and end > e["t"]:
            out.setdefault(int(e["by"]), []).append((float(e["t"]), float(end)))
    return out


def affects(capability: str, spans: dict, slot: int, t: float) -> bool:
    """Whether utility with this capability works on `slot` at `t` (False only while they're immune to it)."""
    if CAPABILITIES[capability]:
        return True
    return not any(a <= t < b for a, b in spans.get(slot, ()))


# ---------------------------------------------------------------- reveals and their sources' sight (W11)

# A reveal row's source -> what the reasons list calls it, and what it needs of the revealed enemy. A trip going
# off on someone is its trigger (it still works on Veto); everything else is a reveal.
REVEAL_REASONS = {"X_InterrogateHat": "neural_theft", "4_TripWire": "trip", "Q_SonarPing": "recon",
                  "E_LoSReveal_Source_Reactivate": "haunt", "E_Drone_RevealDart": "drone_dart",
                  "RemovableObject_GumshoeTrackingDart": "camera_dart", "4_SonarPing": "tejo_drone"}
# An object that looks round itself for a moment: (code_name) -> its range figure. It clears what it sees.
PULSE_SOURCES = {"BountyHunter_E_LoSReveal_Source_Reactivate": "haunt", "Hunter_Q_SonarPing": "recon"}
CONTINUOUS_S = 0.25        # a reveal longer than this also locates at its end (where they were last shown)


def read_reveals(rnd) -> list[Info]:
    """A revealed enemy is located where they stand, at the reveal's start and, for one that lasted, its end: each
    Neural Theft ping, a recon pulse, a Haunt, a dart's tag, a trip going off. A reveal on a teammate, or one with no
    position for the enemy then, locates nothing (counted)."""
    out = []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "reveal" or e.get("by") is None or e.get("target") is None:
            continue
        by, target = int(e["by"]), int(e["target"])
        side = rnd.team.get(by)
        if side is None or rnd.team.get(target) in (None, side):
            continue
        reason = REVEAL_REASONS.get(str(e.get("name")), "reveal")
        needs = "trigger" if reason == "trip" else "reveal"
        times = [float(e["t"])]
        if e.get("t1") is not None and float(e["t1"]) - float(e["t"]) > CONTINUOUS_S:
            times.append(float(e["t1"]))
        for t in times:
            if not rnd.alive(target, t):
                continue
            p = rnd.pos(target, t)
            if p is None:
                rnd.miss("reveal without a position (locates nothing)", target)
                continue
            out.append(Info(t, side, target, "locate", reason, x=p[0], y=p[1], z=rnd.height(target, t),
                            source=f"{e.get('code')}_{e.get('name')}@{e['t']}", detail={"needs": needs}))
    return out


def read_pulses(rnd) -> list[Info]:
    """A Haunt or recon pulse clears what it sees: for each living enemy of its owner, the ground in its sight and
    range is excluded at the pulse (the ones it revealed are located by `read_reveals`). Its sight is cast from the
    object's own place and height, against the map and the smokes up then. Needs: reveal (Veto keeps his region)."""
    from app.control import engine as ce          # the engine imports this module first

    geo, out = rnd.geo, []
    for e in rnd.blob.get("util") or []:
        figure = PULSE_SOURCES.get(f"{e.get('code')}_{e.get('name')}")
        if e.get("k") != "ability" or figure is None or e.get("by") is None:
            continue
        side, t = rnd.team.get(int(e["by"])), float(e["t"])
        if side is None:
            continue
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        z = rnd._device_z(e.get("z"))
        own = eye = None
        if geo.heights is not None:
            own = geo.node_at(geo.cell_of_px(x, y), None if z is None else z + ce.hc.STAND_M)
            eye = None if z is None or np.isnan(geo.node_z[own]) else z + ce.hc.DEVICE_EYE_M
        seen = ce.cast(geo, x, y, np.arange(0, 360, ce.RAY_STEP_DEG), rnd.smokes_at(ce.snap(t)), eye_z=eye, own=own)
        r = FIGURES["reveal_range_m"][figure] / geo.m_per_px
        seen &= ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) <= r * r
        if not seen.any():
            continue
        revealed = {int(r_["target"]) for r_ in rnd.blob.get("util") or [] if r_.get("k") == "reveal"
                    and r_.get("by") == e["by"] and abs(float(r_["t"]) - t) <= 1.0}
        for slot, team in sorted(rnd.team.items()):
            if team == side or slot in revealed or not rnd.alive(slot, t):
                continue
            out.append(Info(t, side, slot, "exclude", figure, mask=seen, source=f"{e.get('code')}_{e.get('name')}@{t}",
                            detail={"needs": "reveal"}))
    return out


READERS: list = [read_reveals, read_pulses]


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
