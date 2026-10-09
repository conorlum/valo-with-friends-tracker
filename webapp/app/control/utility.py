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
- `retract`: the broaden with the same `source` is taken back (its destination was seen after all): the ground
  only that broaden added leaves the region. Nothing else changes, and nothing is held shut after.
- `pause` / `resume`: the enemy can't move in between (Omen's channel): their region doesn't spread, and doesn't
  catch up after. Vision still clears it.

A footprint is a disk of `radius_m` round (x, y), or `mask` (flat nodes) when the reader built one (a line of
sight, a placement disk on several floors). A disk is geometric: through walls, on walkable nodes only.

Readers turn a round's util rows into Infos: `READERS` names them, each `reader(rnd) -> [Info]`.
Every reader runs on the same `RoundInputs`, so an Info's `t` is in the blob's round seconds, unsnapped.

Equal-time order (`ORDER`): what ends first (resume), then what starts (pause), then information, then cleanup;
within a phase, the order the readers gave."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_FIGURES_FILE = json.loads((Path(__file__).with_name("utility.json")).read_text(encoding="utf-8"))


def _numeric(value):
    """Numbers as floats and (nested) tables of numbers; text dropped. The same view as control_format._numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        out = {str(k): _numeric(v) for k, v in value.items() if k not in ("sources", "PROVISIONAL")}
        return {k: v for k, v in out.items() if v is not None} or None
    return None


# Only the numbers (utility.json's `sources` and notes are not consumed): the semantic view W22 fingerprints.
FIGURES = _numeric(_FIGURES_FILE)

KINDS = ("resume", "pause", "locate", "restrict", "exclude", "hypothesis", "broaden", "retract")
ORDER = {"resume": 0, "pause": 1, "locate": 2, "restrict": 2, "exclude": 2, "hypothesis": 2, "broaden": 2,
         "retract": 2}
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
# No local replay has a Veto, so Evolution's own archetype has never been seen. Any of Veto's
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
    object's own place and height, against the map and the smokes and walls up at its own exact time (as the engine
    evaluates that instant; never an earlier frame's). Needs: reveal (Veto keeps his region)."""
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
        seen = ce.cast(geo, x, y, np.arange(0, 360, ce.RAY_STEP_DEG), rnd.smokes_at(t), eye_z=eye, own=own)
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


# ---------------------------------------------------------------- knife and Skye (W12)

KNIFE = "Grenadier_E_SuppressionPulse"
SKYE_FLASH = "skye_guiding_light"


def _living_enemies(rnd, side: str, t: float) -> list[int]:
    return sorted(s for s, team in rnd.team.items() if team != side and rnd.alive(s, t))


def read_knives(rnd) -> list[Info]:
    """KAY/O's ZERO/point (item 10), once it is proven to have pulsed with a complete hit list (condenser
    `activation`): no enemy hit -> every living enemy is outside its radius (through walls); every living enemy hit
    -> each is inside it; anything else -> nothing, not even for the ones it hit. An ulting Veto can't be hit, so
    while he's alive the knife can't have hit everyone, and his region is kept on an empty one (needs: suppress)."""
    from app.control import engine as ce

    out = []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "ability" or f"{e.get('code')}_{e.get('name')}" != KNIFE or e.get("by") is None:
            continue
        act, pulse = e.get("activation") or {}, e.get("pulse")
        if act.get("state") != "completed" or not act.get("targets_complete") or not pulse or pulse.get("r") is None:
            continue
        side, t = rnd.team.get(int(e["by"])), float(pulse["t"])
        if side is None:
            continue
        living = _living_enemies(rnd, side, t)
        if not living:
            continue
        hit = set(pulse.get("hits") or []) & set(living)
        immune = [s for s in living if not affects("suppress", rnd.evolution, s, t)]
        if not hit:
            kind, reason = "exclude", "knife_zero"
        elif hit == set(living) and not immune:
            kind, reason = "restrict", "knife_all"
        else:
            continue
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        radius_m = float(pulse["r"]) * ce.PX / 10000 * rnd.geo.m_per_px
        for slot in living:
            out.append(Info(t, side, slot, kind, reason, x=x, y=y, radius_m=radius_m, source=f"{KNIFE}@{e['t']}",
                            detail={"needs": "suppress", "hits": sorted(hit), "living": living}))
    return out


def read_skye_flashes(rnd) -> list[Info]:
    """Skye's Guiding Light (item 14): a recorded enemy hit means her cue played (whatever the blind's length, zero
    included): nothing changes. A flash proven to have popped with a complete hit list and no enemy hit means no
    enemy was in its reach: the ground within its range and in its sight from where it popped is excluded for each
    living enemy (needs: blind, so an ulting Veto keeps his region). Missing, unresolved or unplaced: nothing."""
    from app.control import engine as ce

    geo, out = rnd.geo, []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "flash" or e.get("ability") != SKYE_FLASH or e.get("by") is None:
            continue
        act, pop = e.get("activation") or {}, e.get("pop") or {}
        if act.get("state") != "completed" or not act.get("targets_complete") or pop.get("u") is None:
            continue
        side = rnd.team.get(int(e["by"]))
        if side is None:
            continue
        t = float(pop["t"])
        if any(rnd.team.get(slot) not in (None, side) for slot, *_ in e.get("hits") or []):
            continue                                       # an enemy was hit: the cue played, nothing is cleared
        living = _living_enemies(rnd, side, t)
        if not living:
            continue
        x, y = pop["u"] * ce.PX / 10000, pop["v"] * ce.PX / 10000
        z = rnd._device_z(pop.get("z"))
        own = eye = None
        if geo.heights is not None:
            own = geo.node_at(geo.cell_of_px(x, y), None if z is None else z + ce.hc.STAND_M)
            eye = None if z is None or np.isnan(geo.node_z[own]) else z
        seen = ce.cast(geo, x, y, np.arange(0, 360, ce.RAY_STEP_DEG), rnd.smokes_at(t), eye_z=eye, own=own)
        r = FIGURES["skye_flash_range_m"] / geo.m_per_px
        seen &= ((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2) <= r * r
        if not seen.any():
            continue
        for slot in living:
            out.append(Info(t, side, slot, "exclude", "skye_no_cue", mask=seen, source=f"flash@{e.get('id', e['t'])}",
                            detail={"needs": "blind"}))
    return out


# ---------------------------------------------------------------- Reyna's Leer (W13)

LEER_EYE = "Vampire_4_NearsightAOE_Source"
LEER_LOOK_STEP_S = 0.25      # how often, while the eye is up, each enemy's view of it is tested


def _sees_point(rnd, viewer: int, t: float, x: float, y: float) -> bool:
    """Whether `viewer`, alive at t, has (x, y) px in their field of view with a clear 2D line to it (walls and the
    smokes up then; a flash or nearsight on them blinds it)."""
    from app.control import engine as ce

    if not rnd.alive(viewer, t) or any(a <= t < b for a, b in rnd.flashed.get(viewer, [])):
        return False
    p = rnd.pos(viewer, t)
    if p is None:
        return False
    off = abs((math.degrees(math.atan2(y - p[1], x - p[0])) - p[2] + 180) % 360 - 180)
    if off > ce.FOV_HALF:
        return False
    if any(a <= t < b for a, b in rnd.nearsight.get(viewer, [])) and \
            math.hypot(x - p[0], y - p[1]) * rnd.geo.m_per_px >= ce.NEARSIGHT_RADIUS_M:
        return False
    return ce.los(rnd.geo, (p[0], p[1], None), (x, y, None), rnd.smokes_at(t))


def read_leers(rnd) -> list[Info]:
    """Reyna's Leer (item 29): once one of her enemies sees the eye (it is in a living enemy's view, or it
    nearsighted one of them), the team knows she cast it from within the eye's placement distance, through walls:
    her region is restricted to that disk, widened by how far she could have walked between the cast and the moment
    it is seen (UNKNOWN_MPS, straight-line: no smaller than the truth), and moves on from there as usual. An eye no
    enemy saw tells them nothing. Only Reyna's own region, never another enemy's."""
    from app.control import engine as ce

    out = []
    util = rnd.blob.get("util") or []
    for e in util:
        if e.get("k") != "ability" or f"{e.get('code')}_{e.get('name')}" != LEER_EYE or e.get("by") is None:
            continue
        reyna, t0 = int(e["by"]), float(e["t"])
        side_of_reyna = rnd.team.get(reyna)
        if side_of_reyna is None:
            continue
        t1 = float(e["t1"]) if e.get("t1") is not None else rnd.t_end
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        enemies = sorted(s for s, team in rnd.team.items() if team != side_of_reyna)
        seen_at = None
        for cast_row in util:                         # a hit on an enemy is seeing it
            if cast_row.get("k") == "nearsight" and cast_row.get("by") == reyna and cast_row.get("ability") == "reyna_leer":
                for slot, t_hit, *_ in cast_row.get("hits") or []:
                    if slot in enemies and t0 - 0.05 <= float(t_hit) <= t1:
                        seen_at = float(t_hit) if seen_at is None else min(seen_at, float(t_hit))
        t = t0
        while t <= t1 and (seen_at is None or t < seen_at):
            if any(_sees_point(rnd, s, t, x, y) for s in enemies):
                seen_at = t
                break
            t += LEER_LOOK_STEP_S
        if seen_at is None or not rnd.alive(reyna, seen_at):
            continue
        side = next(rnd.team[s] for s in enemies)
        out.append(Info(seen_at, side, reyna, "restrict", "leer_seen", x=x, y=y,
                        radius_m=FIGURES["leer_cast_range_m"] + ce.UNKNOWN_MPS * max(0.0, seen_at - t0),
                        source=f"{LEER_EYE}@{t0}", detail={"needs": "sight"}))
    return out


# ---------------------------------------------------------------- teleports and temporary bodies (W16-W18)

OMEN_ULT = "Wraith_X_GlobalTeleport_Intention"
WAYLAY_ANCHOR = "Terra_E_RewindTime_RewindTarget"


def heard(rnd, side: str, t: float, x: float, y: float, range_m: float) -> bool:
    """Whether a living player of `side` is within range_m of (x, y) px at t (walls ignored, as for footsteps)."""
    r = range_m / rnd.geo.m_per_px
    for s, team in rnd.team.items():
        if team != side or not rnd.alive(s, t):
            continue
        p = rnd.pos(s, t)
        if p is not None and (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r * r:
            return True
    return False


def outside_hearing(rnd, side: str, t: float, range_m: float) -> np.ndarray:
    """The walkable nodes no living player of `side` hears from at t: everywhere they could have gone unheard."""
    geo = rnd.geo
    out = geo.walk_n.copy()
    r = range_m / geo.m_per_px
    for s, team in rnd.team.items():
        if team != side or not rnd.alive(s, t):
            continue
        p = rnd.pos(s, t)
        if p is not None:
            out &= (geo.centres[:, 0] - p[0]) ** 2 + (geo.centres[:, 1] - p[1]) ** 2 > r * r
    return out


def read_omen_ults(rnd) -> list[Info]:
    """Omen's From the Shadows (item 12), for each enemy team: his region doesn't move while he channels (pause at the
    marker's start, resume at its end). If none of them hears or sees the destination when it appears, he may be
    anywhere they don't hear: that ground joins his region then, whatever the outcome, and stays after a cancel. If
    one of them first sees the destination later in the channel, the guess is taken back at that moment (`retract`),
    never earlier: what was known before the look is not rewritten. Seen or heard at the start: nothing more (a
    completion is then an ordinary sighting or spread from where he lands)."""
    from app.control import engine as ce

    out = []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "ability" or f"{e.get('code')}_{e.get('name')}" != OMEN_ULT or e.get("by") is None:
            continue
        omen = int(e["by"])
        own = rnd.team.get(omen)
        if own is None:
            continue
        t0 = float(e["t"])
        t1 = float(e["t1"]) if e.get("t1") is not None else rnd.t_end
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        side = next((team for team in rnd.team.values() if team != own), None)
        if side is None:
            continue
        source = f"{OMEN_ULT}@{t0}"
        out.append(Info(t0, side, omen, "pause", "omen_channel", source=source))
        out.append(Info(t1, side, omen, "resume", "omen_channel", source=source))
        enemies = [s for s, team in rnd.team.items() if team == side]
        range_m = FIGURES["hearing_m"]["omen_ult"]
        # decided on what they know when it appears; a look later in the channel is its own, later, Info
        if heard(rnd, side, t0, x, y, range_m) or any(_sees_point(rnd, s, t0, x, y) for s in enemies):
            continue
        out.append(Info(t0, side, omen, "broaden", "omen_unheard", mask=outside_hearing(rnd, side, t0, range_m),
                        source=source, detail={"outcome": e.get("outcome")}))
        t = t0 + LEER_LOOK_STEP_S
        while t <= t1:
            if any(_sees_point(rnd, s, t, x, y) for s in enemies):
                out.append(Info(t, side, omen, "retract", "omen_seen", source=source))
                break
            t += LEER_LOOK_STEP_S
    return out


def yoru_beacon(rnd, yoru: int, t: float, x: float, y: float, source: str) -> list[Info]:
    """Yoru's Gatecrash (item 16) from evidence the export doesn't carry yet (no beacon actor is exported, W2): an
    activated or faked beacon an enemy hears adds a second origin there, beside his real body, until vision clears
    it. Not in READERS: called by a reader once a beacon signal exists."""
    own = rnd.team.get(yoru)
    side = next((team for team in rnd.team.values() if team != own), None)
    if side is None or not heard(rnd, side, t, x, y, FIGURES["hearing_m"]["yoru_beacon"]):
        return []
    return [Info(t, side, yoru, "hypothesis", "yoru_beacon", x=x, y=y, source=source)]


def yoru_drift_exit(rnd, yoru: int, t: float, x: float, y: float, source: str) -> list[Info]:
    """Yoru's Dimensional Drift exit (item 17), from evidence the export doesn't carry yet: heard, nothing; unheard,
    he may be anywhere no enemy hears. Not in READERS (see `yoru_beacon`)."""
    own = rnd.team.get(yoru)
    side = next((team for team in rnd.team.values() if team != own), None)
    range_m = FIGURES["hearing_m"]["yoru_drift_exit"]
    if side is None or heard(rnd, side, t, x, y, range_m):
        return []
    return [Info(t, side, yoru, "broaden", "yoru_drift_unheard", mask=outside_hearing(rnd, side, t, range_m),
                 source=source)]


def read_waylay_recalls(rnd) -> list[Info]:
    """Waylay's Refract (item 21): a completed recall (the condenser's `recall` on her return point) that an enemy
    hears, at its start or its end, puts her at the return point when she arrives: her old region is gone and she
    spreads from there. Unheard: nothing. A recall can't be faked, so a heard one is located."""
    from app.control import engine as ce

    out = []
    for e in rnd.blob.get("util") or []:
        if e.get("k") != "ability" or f"{e.get('code')}_{e.get('name')}" != WAYLAY_ANCHOR or not e.get("recall"):
            continue
        waylay = e.get("by")
        own = rnd.team.get(waylay)
        if own is None:
            continue
        side = next((team for team in rnd.team.values() if team != own), None)
        rc_ = e["recall"]
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        fx, fy = rc_["u"] * ce.PX / 10000, rc_["v"] * ce.PX / 10000
        t_arrive = float(rc_["t1"])
        range_m = FIGURES["hearing_m"]["waylay_recall"]
        if side is None or not rnd.alive(int(waylay), t_arrive):
            continue
        if not (heard(rnd, side, float(rc_["t"]), fx, fy, range_m) or heard(rnd, side, t_arrive, x, y, range_m)):
            continue
        out.append(Info(t_arrive, side, int(waylay), "locate", "waylay_recall", x=x, y=y, z=rnd._device_z(e.get("z")),
                        source=f"{WAYLAY_ANCHOR}@{e['t']}"))
    return out


# The readers that run, by name and in order (names, so the list is one of the constants CONTROL_REVISION pins:
# tests/replays/test_control_format.py).
READERS = ("read_reveals", "read_pulses", "read_knives", "read_skye_flashes", "read_leers", "read_omen_ults",
           "read_waylay_recalls")


def read_all(rnd) -> list[Info]:
    """Every reader's Infos for the round, in engine order (`ORDER`)."""
    out: list[Info] = []
    for name in READERS:
        out.extend(globals()[name](rnd))
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
