"""What utility tells a team about one enemy, as knowledge operations on that enemy's unknown
(docs/superpowers/plans/2026-10-05-replay-control-review-impl.md, W09). Local tooling only, like the engine.

An `Info` says, at its own exact time `t`, something `side` learns about enemy `slot`:

- `locate`: the enemy is exactly at (x, y): their region starts again from there (a sighting). It is locating
  evidence: it joins `Unknown.events`, the stream the gap detector reads, and resets the last-located time.
- `restrict`: the enemy is somewhere in the footprint: everything outside it is dropped. Disconnected parts that
  survive stay apart; nothing is re-centred. Not locating evidence.
- `exclude`: the enemy is not in the footprint: it is dropped. Not locating evidence.
- `one_of`: an anonymous `count` of live enemies is in the footprint (one by default). Uses ONE_OF_SLOT;
  derived per-enemy restrict/hypothesis operations are selected from their regions at this exact time.
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

import collections
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

ONE_OF_SLOT = -1
SIDE_KINDS = ("one_of",)
KINDS = ("resume", "pause", "locate", "restrict", "exclude", "hypothesis", "broaden", "retract", "one_of")
ORDER = {"resume": 0, "pause": 1, "locate": 2, "restrict": 2, "exclude": 2, "hypothesis": 2, "broaden": 2,
         "retract": 2, "one_of": 2}
LOCATING = frozenset({"locate"})


@dataclass
class Info:
    t: float
    side: str                     # the team that learns it
    slot: int                     # the enemy it is about; ONE_OF_SLOT for SIDE_KINDS
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
    cast_id: int | None = None    # the util row's id; reveals have no id
    row_key: tuple | None = None  # (by, code, name, t) of the original util row
    count: int = 1                # anonymous enemies in a side-wide footprint; zero uses zero_hits

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown knowledge operation {self.kind!r}")
        if (self.kind in SIDE_KINDS) != (self.slot == ONE_OF_SLOT):
            raise ValueError("side-wide knowledge requires ONE_OF_SLOT; per-slot knowledge forbids it")


def _identity(row: dict) -> dict:
    """Keep cast identity separate from the source used by hypothesis protection/retraction."""
    return {"cast_id": row.get("id"), "row_key": tuple(row.get(k) for k in ("by", "code", "name", "t"))}


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
# Evolution has no object: it is a `buff` row (`Pine` / `X_Evolution`, CONDENSE 23) on Veto himself, from the end
# of its windup to his death or the round's end (trap-jobs D10: 4 uses in 6e52839b and 47a5f123). Blobs before 23
# have no such row, so they show no Evolution, as before.
EVOLUTION = re.compile(r"^Pine_X_")
VETO = "Veto"


def evolution_spans(blob: dict) -> dict[int, list[tuple[float, float]]]:
    """slot -> [(from, to)] round seconds while that player was in Evolution: an ult-slot buff row of a Veto
    (EVOLUTION) on himself, from its start to its end. Nothing for anyone else."""
    vetos = {p["slot"] for p in blob.get("players") or [] if p.get("agent") == VETO}
    out: dict[int, list[tuple[float, float]]] = {}
    for e in blob.get("util") or []:
        if e.get("k") != "buff" or e.get("by") not in vetos or e.get("target", e.get("by")) != e.get("by"):
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
                            source=f"{e.get('code')}_{e.get('name')}@{e['t']}", detail={"needs": needs}, **_identity(e)))
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
                            detail={"needs": "reveal"}, **_identity(e)))
    return out


# ---------------------------------------------------------------- knife and Skye (W12)

KNIFE = "Grenadier_E_SuppressionPulse"
SKYE_FLASH = "skye_guiding_light"


def _living_enemies(rnd, side: str, t: float) -> list[int]:
    return sorted(s for s, team in rnd.team.items() if team != side and rnd.alive(s, t))


def zero_hits(rnd, t: float, side: str, reason: str, *, needs: str, **kwargs) -> list[Info]:
    """An anonymous zero excludes the footprint for every live enemy.

    The caller supplies the footprint and cast identity as Info keywords, and must name the capability
    (status or reveal) so RoundInputs drops exclusions for enemies immune to that utility.
    """
    detail = {**kwargs.pop("detail", {}), "needs": needs}
    return [Info(t, side, slot, "exclude", reason, detail=detail.copy(), **kwargs)
            for slot in _living_enemies(rnd, side, t)]


KNIFE_REASONS = ("knife_zero", "knife_all", "knife_hit", "knife_missed")


def read_knives(rnd) -> list[Info]:
    """KAY/O's ZERO/point (item 10; partial hits 2026-10-16), once it is proven to have pulsed with a complete hit
    list (condenser `activation`): no enemy hit -> every living enemy is outside its radius (through walls); every
    living enemy hit and none immune -> each is inside it; otherwise (some hit) -> each hit enemy is inside it
    (restrict knife_hit) and each other living enemy outside it (exclude knife_missed). Every op needs: suppress,
    so an ulting Veto, who can't be hit, keeps his region (the engine drops his op). Restricts use r + knife_rim_m,
    excludes max(0, r - knife_rim_m)."""
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
        x, y = e["u"] * ce.PX / 10000, e["v"] * ce.PX / 10000
        radius_m = float(pulse["r"]) * ce.PX / 10000 * rnd.geo.m_per_px
        # The rim margin (utility.json knife_rim_m, AFK run decision): a hit enemy may have moved
        # out by the edge of the pulse's timing, a missed one in; restricts widen, excludes shrink, never below 0.
        rim = FIGURES["knife_rim_m"]
        if not hit:
            plan = {s: ("exclude", "knife_zero") for s in living}
        elif hit == set(living) and not immune:
            plan = {s: ("restrict", "knife_all") for s in living}
        else:
            plan = {s: ("restrict", "knife_hit") if s in hit else ("exclude", "knife_missed") for s in living}
        for slot in living:
            kind, reason = plan[slot]
            r_op = radius_m + rim if kind == "restrict" else max(0.0, radius_m - rim)
            out.append(Info(t, side, slot, kind, reason, x=x, y=y, radius_m=r_op, source=f"{KNIFE}@{e['t']}",
                            detail={"needs": "suppress", "hits": sorted(hit), "living": living, "r_m": radius_m}, **_identity(e)))
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
                            detail={"needs": "blind"}, **_identity(e)))
    return out


# ---------------------------------------------------------------- Reyna's Leer (W13)

LEER_EYE = "Vampire_4_NearsightAOE_Source"
LEER_LOOK_STEP_S = 0.25      # how often, while the eye is up, each enemy's view of it is tested


def _sees_point(rnd, viewer: int, t: float, x: float, y: float, target: int | None = None) -> bool:
    """Whether `viewer`, alive at t, has (x, y) px in their field of view with a clear 2D line to it (walls and the
    smokes up then; a flash or nearsight on them blinds it). A viewer on a box sees from their stance's eye over
    that piece; `target` (a player slot whose position is the point), when boxed, is seen at its eye over its piece.
    Without a boxed target, a point on a top node's leaf sees over that piece too. This remains a 2D query, heights
    being the engine's, except for a `target` when either end is airborne: then the engine's own two pair lines."""
    from app.control import engine as ce
    from app.control import stance as st_mod

    if not rnd.alive(viewer, t) or any(a <= t < b for a, b in rnd.flashed.get(viewer, [])):
        return False
    p = rnd.pos(viewer, t)
    if p is None:
        return False
    vs = st_mod.boxed_stance(rnd, viewer, t)
    ex, ey = (p[0], p[1]) if vs is None else vs.eye_px
    sv = () if vs is None else vs.see_over
    st = ()
    ts = None
    if target is not None:
        ts = st_mod.boxed_stance(rnd, target, t)
        if ts is not None:
            x, y = ts.eye_px
            st = ts.see_over
    if ts is None and len(rnd.geo.top_nodes):
        tops = np.flatnonzero(rnd.geo.top_leaf == rnd.geo.space_at_px(x, y))
        if len(tops):
            st = (int(rnd.geo.top_piece[tops[0]]),)
    off = abs((math.degrees(math.atan2(y - ey, x - ex)) - p[2] + 180) % 360 - 180)
    if off > ce.FOV_HALF:
        return False
    if any(a <= t < b for a, b in rnd.nearsight.get(viewer, [])) and \
            math.hypot(x - ex, y - ey) * rnd.geo.m_per_px >= ce.NEARSIGHT_RADIUS_M:
        return False
    if target is not None:
        seen = ce._air_pair_sight(rnd, viewer, target, t, rnd.smokes_at(t))
        if seen is not None:
            return seen
    return ce.los(rnd.geo, (ex, ey, None), (x, y, None), rnd.smokes_at(t), see_over=(sv, st))


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
                        source=f"{LEER_EYE}@{t0}", detail={"needs": "sight"}, **_identity(e)))
    return out


# ---------------------------------------------------------------- teleports and temporary bodies (W16-W18)

OMEN_ULT = "Wraith_X_GlobalTeleport_Intention"
WAYLAY_ANCHOR = "Terra_E_RewindTime_RewindTarget"


def listens(rnd, s: int, t: float) -> bool:
    """Whether player `s` can hear at t: alive and not nearsighted (a nearsight deafens; D11, changed 2026-10-09:
    "deafened players cant hear for like omen ult or other things")."""
    return rnd.alive(s, t) and not any(a <= t < b for a, b in rnd.nearsight.get(s, ()))


def heard(rnd, side: str, t: float, x: float, y: float, range_m: float) -> bool:
    """Whether a listening player of `side` is within range_m of (x, y) px at t (walls ignored, as for footsteps)."""
    r = range_m / rnd.geo.m_per_px
    for s, team in rnd.team.items():
        if team != side or not listens(rnd, s, t):
            continue
        p = rnd.pos(s, t)
        if p is not None and (p[0] - x) ** 2 + (p[1] - y) ** 2 <= r * r:
            return True
    return False


def outside_hearing(rnd, side: str, t: float, range_m: float) -> np.ndarray:
    """The walkable nodes no listening player of `side` hears from at t: everywhere they could have gone unheard."""
    geo = rnd.geo
    out = geo.walk_n & ~geo.is_top
    r = range_m / geo.m_per_px
    for s, team in rnd.team.items():
        if team != side or not listens(rnd, s, t):
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
        out.append(Info(t0, side, omen, "pause", "omen_channel", source=source, **_identity(e)))
        out.append(Info(t1, side, omen, "resume", "omen_channel", source=source, **_identity(e)))
        enemies = [s for s, team in rnd.team.items() if team == side]
        range_m = FIGURES["hearing_m"]["omen_ult"]
        # decided on what they know when it appears; a look later in the channel is its own, later, Info
        if heard(rnd, side, t0, x, y, range_m) or any(_sees_point(rnd, s, t0, x, y) for s in enemies):
            continue
        out.append(Info(t0, side, omen, "broaden", "omen_unheard", mask=outside_hearing(rnd, side, t0, range_m),
                        source=source, detail={"outcome": e.get("outcome")}, **_identity(e)))
        t = t0 + LEER_LOOK_STEP_S
        while t <= t1:
            if any(_sees_point(rnd, s, t, x, y) for s in enemies):
                out.append(Info(t, side, omen, "retract", "omen_seen", source=source, **_identity(e)))
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
                        source=f"{WAYLAY_ANCHOR}@{e['t']}", **_identity(e)))
    return out


def read_hush_suspensions(rnd) -> list[Info]:
    """Suspension's hit feedback says at least one enemy is in this zone (R11/D9).

    The sound is treated as heard by all of Hush's team, with no range check; unverified (D9).
    Status targets identify enemy hits only, never which enemy the team's knowledge should name.
    """
    from app.control import engine as ce

    rows = rnd.blob.get("util") or []
    hits = {}
    for e in rows:
        if (e.get("k") != "status" or e.get("status") != "levitated" or e.get("friendly")
                or e.get("code") != "Paprika" or e.get("name") != "X_LevitateZone" or not e.get("src")):
            continue
        side, target = rnd.team.get(e.get("by")), rnd.team.get(e.get("target"))
        if side is not None and target is not None and side != target:
            key = (e["src"], e.get("by"))
            hits[key] = min(hits.get(key, math.inf), float(e["t"]))
    out = []
    for e in rows:
        if (e.get("k") != "ability" or e.get("code") != "Paprika" or e.get("name") != "X_LevitateZone"
                or (e.get("id"), e.get("by")) not in hits):
            continue
        t = hits[e["id"], e["by"]]
        out.append(Info(t, rnd.team[e["by"]], ONE_OF_SLOT, "one_of", "hush_suspension",
                        x=e["u"] * ce.PX / 10000, y=e["v"] * ce.PX / 10000,
                        radius_m=FIGURES["hush_suspension_radius_m"], source=f"Paprika_X_LevitateZone@{t}",
                        detail={"needs": "status"}, **_identity(e)))
    return out


# An unjoined hit makes nearby casts of its owner uncertain, even when their own rows prove they fired.
UNMATCHED_HIT_WINDOW_S = 3.0


# W5.1 (R14, extended to these sources by the owner, 2026-11-23): whether a source's "it hit nobody" rules its area
# out. After the fix to when and where the check is made, the probe re-measured the zero-hit footprints that held a
# real live enemy (W5.1-notes.md); a source still over 5% is switched off here, and its hits and counts stay.
# Dizzy has no entry: it only fires at an enemy it sees, so a miss says nothing about the ground (the owner).
ZERO_HIT_EXCLUDES = {"nightfall": True, "storm_surge": True, "thrash": True}
DIZZY_SHOT_WINDOW_S = 0.1     # hits this close to a Dizzy's first are the same shot
# MEASURED (W5.1), not from the wiki: how long after a Dizzy's cast row it fires its first shot (the wind-up and the
# first sight). The first `concussed` hit came 0.608-0.616 s after the cast in 12 of 15 hit casts on 4 real replays
# (the others 1.31-1.52 s, a later sight). It is the firing moment of a fired Dizzy that hit nobody, whose row only
# says that it fired; never the end of its life (about 1.9 s), when the enemy it saw has moved on.
DIZZY_FIRE_S = 0.61


def _hit_counts(hits):
    """Anonymous counts per analytical tick, never a cast's historical total."""
    from app.control import engine as ce

    ticks = collections.defaultdict(set)
    for t, target in hits:
        at = round(math.ceil(t / ce.TICK_STEP_S - 1e-9) * ce.TICK_STEP_S, 6)
        ticks[at].add(target)
    return [(t, len(targets)) for t, targets in sorted(ticks.items())]


def _status_counts(rnd, code: str, name: str, status: str, *, proof=("hits_read", "fired"), first_shot=False):
    """Distinct enemy hits per source and tick, with no target identity leaving this helper.

    Positive evidence survives old/missing firing proof; only a proven cast (all of `proof` true on its row) can say
    zero hits. Positive counts retain their observation ticks; a proven empty cast keeps its end time.
    Dizzy alone uses its existing first-shot window and exact first-hit time (W5.1).
    A hit with no matching source makes nearby casts of that owner/source unusable.
    """
    rows = rnd.blob.get("util") or []
    casts = [row for row in rows if row.get("k") == "ability" and row.get("code") == code
             and row.get("name") == name]
    sources = {(row.get("id"), row.get("by")) for row in casts}
    hits, unmatched = {}, []
    for hit in rows:
        if (hit.get("k") != "status" or hit.get("code") != code or hit.get("name") != name
                or hit.get("status") != status or hit.get("friendly")):
            continue
        side, target_side = rnd.team.get(hit.get("by")), rnd.team.get(hit.get("target"))
        if side is None or target_side in (None, side):
            continue
        key = (hit.get("src"), hit.get("by"))
        if hit.get("src") is None or key not in sources:
            unmatched.append((hit.get("by"), float(hit["t"])))
            continue
        hits.setdefault(key, []).append((float(hit["t"]), hit["target"]))
    for row in casts:
        side = rnd.team.get(row.get("by"))
        if side is None:
            continue
        # A flown source can hit long after spawning. Protect its whole life, not just its start.
        end = row.get("t1") if row.get("t1") is not None else rnd.blob.get("t_end", math.inf)
        if any(by == row.get("by") and float(row["t"]) - UNMATCHED_HIT_WINDOW_S <= t
               <= float(end) + UNMATCHED_HIT_WINDOW_S for by, t in unmatched):
            continue
        own = hits.get((row.get("id"), row.get("by")), ())
        if own:
            if first_shot:
                t = min(t for t, _ in own)
                yield row, side, len({target for at, target in own if at <= t + DIZZY_SHOT_WINDOW_S}), t
            else:
                for t, count in _hit_counts(own):
                    yield row, side, count, t
        elif all(row.get(field) is True for field in proof):
            t = row.get("t1") if row.get("t1") is not None else row["t"]
            yield row, side, 0, float(t)


def _count_disk(row, *, at_end=False):
    """A source's own place; a flown detain uses its final path point, never its victim's position."""
    from app.control import engine as ce

    if at_end and not row.get("path"):
        return None
    if at_end:
        _, u, v, *_ = row["path"][-1]
    else:
        u, v = row.get("u"), row.get("v")
    if u is None or v is None:
        return None
    return {"x": u * ce.PX / 10000, "y": v * ce.PX / 10000,
            "source": f"{row.get('code')}_{row.get('name')}@{row.get('id', row['t'])}", **_identity(row)}


def read_nightfall_counts(rnd) -> list[Info]:
    """Nightfall's anonymous hit count over its forward wave rectangle, through walls.

    Each positive count covers only the slice swept in its observation tick. A proven wave that hit nobody
    rules the rectangle out slice by slice, each at the time the wave passes it (W5.1): the wave sweeps forward
    at `nightfall.speed_mps` after `delay_s`, so ground behind it is excluded at the tick it went by, never at the
    end of the wave's life, when an enemy may have walked into ground it had already crossed.
    """
    from app.control import engine as ce

    out = []
    figures = FIGURES["nightfall"]
    for row, side, count, t in _status_counts(rnd, "BountyHunter", "X_WaveForm", "decayed"):
        place = _count_disk(row)
        if place is None or row.get("yaw") is None:
            continue
        d = (rnd.geo.centres - (place["x"], place["y"])) * rnd.geo.m_per_px
        a = math.radians(row["yaw"])
        along = d[:, 0] * math.cos(a) + d[:, 1] * math.sin(a)
        across = -d[:, 0] * math.sin(a) + d[:, 1] * math.cos(a)
        mask = (rnd.geo.walk_n & (along >= 0) & (along <= figures["length_m"])
                & (np.abs(across) <= figures["width_m"] / 2))
        if not mask.any():
            continue
        if count:
            start = float(row["t"]) + figures["delay_s"]
            behind = max(-1., figures["speed_mps"] * (t - ce.TICK_STEP_S - start))
            front = min(figures["length_m"], figures["speed_mps"] * (t - start))
            part = mask & (along > behind) & (along <= front)
            if part.any():
                out.append(Info(t, side, ONE_OF_SLOT, "one_of", "nightfall_count", count=count, mask=part,
                                detail={"needs": "status"}, **place))
        elif ZERO_HIT_EXCLUDES["nightfall"]:
            start = float(row["t"]) + figures["delay_s"]
            behind = -1.0
            for k in range(math.ceil(start / ce.TICK_STEP_S - 1e-9),
                           math.ceil((start + figures["length_m"] / figures["speed_mps"]) / ce.TICK_STEP_S - 1e-9) + 1):
                t_k = round(k * ce.TICK_STEP_S, 6)
                if t_k > rnd.t_end:
                    break
                front = min(figures["length_m"], figures["speed_mps"] * (t_k - start))
                part = mask & (along > behind) & (along <= front)
                behind = front
                if part.any():
                    out.extend(zero_hits(rnd, t_k, side, "nightfall_zero", needs="status", mask=part, **place))
    return out


def _flown_place(row, t: float):
    """Where a thrown device was at `t`: its flight (held at its last sample after it ends), else its row's place."""
    from app.control import engine as ce

    place, z = _count_disk(row), row.get("z")
    flight = row.get("flight") or row.get("path")
    if place is None or not flight:
        return place, z
    held = min(max(t, flight[0][0]), flight[-1][0])
    at = ce.path_at(flight, held)
    if at is not None:
        place["x"], place["y"] = at[0], at[1]
        flown_z = ce.path_z(flight, held)
        z = z if flown_z is None else flown_z
    return place, z


def read_dizzy_counts(rnd) -> list[Info]:
    """A fired Dizzy counts at least one enemy over the ground visible from where it fired, within range.

    It only fires at an enemy it sees, hit or not, so firing alone proves one in its sight; its hits (the
    `concussed` rows map control already reads) are the status effect and make the count larger. It never rules
    ground out (the owner, 2026-11-23): one that was shot down (`gone`, no `fired`) or never wound up says nothing.
    """
    from app.control import engine as ce

    geo, out = rnd.geo, []
    for row, side, count, t in _status_counts(rnd, "Aggrobot", "E_DiscTurret_PowerWave", "concussed",
                                              proof=("fired",), first_shot=True):
        # The firing moment: its first shot's hits, else when it winds up and fires (`dizzy_fire_s` after the
        # cast), never the end of its life, when the enemy it saw has long moved. Later shots are other moments.
        if not count:
            t = min(float(row["t"]) + DIZZY_FIRE_S, float(row["t1"]) if row.get("t1") is not None else math.inf)
        place, row_z = _flown_place(row, t)
        if place is None:
            continue
        x, y = place["x"], place["y"]
        z = rnd._device_z(row_z)
        own = eye = None
        if geo.heights is not None:
            own = geo.node_at(geo.cell_of_px(x, y), None if z is None else z + ce.hc.STAND_M)
            eye = None if z is None or np.isnan(geo.node_z[own]) else z
        mask = ce.cast(geo, x, y, np.arange(0, 360, ce.RAY_STEP_DEG), rnd.smokes_at(t), eye_z=eye, own=own)
        mask &= geo.walk_n & (((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2)
                             <= (FIGURES["dizzy_range_m"] / geo.m_per_px) ** 2)
        if mask.any():
            out.append(Info(t, side, ONE_OF_SLOT, "one_of", "dizzy_count", count=max(1, count), mask=mask,
                            detail={"needs": "status"}, **place))
    return out


def read_thrash_detains(rnd) -> list[Info]:
    """Thrash's detain counts around its flight position at each observation tick."""
    out = []
    for row, side, count, t in _status_counts(rnd, "Aggrobot", "RollyPolly", "detained"):
        if not row.get("path"):
            continue
        place, _ = _flown_place(row, t)
        if place is None:
            continue
        place["radius_m"] = FIGURES["thrash_detain_radius_m"]
        if count:
            out.append(Info(t, side, ONE_OF_SLOT, "one_of", "thrash_count", count=count,
                            detail={"needs": "status"}, **place))
        elif ZERO_HIT_EXCLUDES["thrash"]:
            out.extend(zero_hits(rnd, t, side, "thrash_zero", needs="status", **place))
    return out


def read_storm_surges(rnd) -> list[Info]:
    """A heard whirlpool counts the parser's nearsight hits, joined through the thrown projectile (W5.1).

    The parser's `nearsight` rows carry the time and place of the thrown Projectile row, not the whirlpool's: each
    joins strictly (same owner, same ms, same place) to its Projectile, then to that throw's whirlpool, the owner's
    next whirlpool spawn at the projectile's end (one per throw, within `UNMATCHED_HIT_WINDOW_S`). The projectile
    is the same cast's flight, never a second count. Hits that join to no single whirlpool invalidate nearby casts
    of the same owner: neither a partial count nor a zero is proven. Empty hit lists say nothing here.
    The hits fall at the whirlpool's burst (its end), so a zero is made there, around the whirlpool.
    """
    rows = rnd.blob.get("util") or []

    def whirlpool(kind):
        return [row for row in rows if row.get("k") == "ability" and row.get("code") == "Mage"
                and row.get("name") == "4_SplashGrenade" and row.get("kind") == kind
                and row.get("u") is not None and row.get("v") is not None and row.get("by") in rnd.team]

    casts, projectiles = whirlpool("GameObject"), whirlpool("Projectile")
    hits, ambiguous = {}, set()
    for row in rows:
        if (row.get("k") != "nearsight" or row.get("ability") != "harbor_storm_surge"
                or not row.get("hits")):
            continue
        thrown = [p for p in projectiles if row.get("by") == p["by"]
                  and abs(float(row["t"]) - float(p["t"])) <= .001
                  and row.get("u") is not None and row.get("v") is not None
                  and abs(row["u"] - p["u"]) <= 1 and abs(row["v"] - p["v"]) <= 1]
        matches = []
        if len(thrown) == 1:
            landed = float(thrown[0]["t1"] if thrown[0].get("t1") is not None else thrown[0]["t"])
            later = [(float(cast["t"]), i) for i, cast in enumerate(casts) if cast["by"] == thrown[0]["by"]
                     and landed - .001 <= float(cast["t"]) <= landed + UNMATCHED_HIT_WINDOW_S]
            if later:
                first = min(t for t, _ in later)
                matches = [i for t, i in later if t - first <= .001]
        if len(matches) != 1:
            ambiguous.update(i for i, cast in enumerate(casts) if row.get("by") == cast["by"]
                             and abs(float(row["t"]) - float(cast["t"])) <= UNMATCHED_HIT_WINDOW_S)
            continue
        index = matches[0]
        side = rnd.team[casts[index]["by"]]
        own = hits.setdefault(index, [])
        for target, hit_t, *_ in row.get("hits") or []:
            if rnd.team.get(target) not in (None, side):
                own.append((float(hit_t), target))
    out = []
    for i, row in enumerate(casts):
        if i in ambiguous:
            continue
        burst = float(row["t1"]) if row.get("t1") is not None else float(row["t"])
        own = hits.get(i, ())
        if not own and not (row.get("hits_read") is True and row.get("fired") is True
                                and ZERO_HIT_EXCLUDES["storm_surge"]):
            continue
        side, place = rnd.team[row["by"]], _count_disk(row)
        place["radius_m"] = FIGURES["storm_surge_radius_m"]
        if own:
            for t, count in _hit_counts(own):
                if heard(rnd, side, t, place["x"], place["y"], FIGURES["storm_surge_hearing_m"]):
                    out.append(Info(t, side, ONE_OF_SLOT, "one_of", "storm_surge_count", count=count,
                                    detail={"needs": "status"}, **place))
        elif heard(rnd, side, burst, place["x"], place["y"], FIGURES["storm_surge_hearing_m"]):
            out.extend(zero_hits(rnd, burst, side, "storm_surge_zero", needs="status", **place))
    return out


# R14: each source's misses can be disabled independently after the false-clear probe; hits stay enabled.
# W9's probe (4 real replays): exclude footprints that held a real live enemy at that moment were Prowler 32 of 129
# (24.8%), Wingman 11 of 129 (8.5%), Seekers 41 of 235 (17.4%), all above the declared 5%, so their misses are off.
# Boom Bot has no stamped cast (its hits can't be joined to a damage row), so it excludes nothing and stays on.
CREATURE_MISSES = {"prowler": False, "wingman": False, "boom_bot": True, "seekers": False}


def read_creatures(rnd) -> list[Info]:
    """Creature hits locate; before the first hit, proven misses clear only their visible cone.

    Sample on the round's existing half-second clock, never on path or yaw timestamps. A later
    victim's identity does not select who gets an exclusion. Unjoined statuses make nearby casts
    of the same owner and source uncertain, while retaining their positive hit evidence.
    """
    from app.control import engine as ce

    sources = {"BountyHunter_4_WolfHound": ("prowler", "nearsighted"),
               "Aggrobot_SeekerNade": ("wingman", "concussed"),
               "Clay_E_Boomba": ("boom_bot", None), "Guide_X_Pack": ("seekers", "nearsighted")}
    hit_reasons = {"prowler": "prowler_hit", "wingman": "wingman_hit",
                   "boom_bot": "boom_bot_hit", "seekers": "seekers_hit"}
    miss_reasons = {"prowler": "prowler_miss", "wingman": "wingman_miss",
                    "boom_bot": "boom_bot_miss", "seekers": "seekers_miss"}
    rows, geo, out = rnd.blob.get("util") or [], rnd.geo, []
    for key, (figure, status) in sources.items():
        casts = [r for r in rows if r.get("k") == "ability"
                 and f"{r.get('code')}_{r.get('name')}" == key]
        by_id = {(r["id"], r.get("by")): r for r in casts if r.get("id") is not None}
        first_hits, unmatched = {}, []
        for hit in rows:
            if (f"{hit.get('code')}_{hit.get('name')}" != key or hit.get("friendly")
                    or (status is not None and (hit.get("k") != "status" or hit.get("status") != status))
                    or (status is None and hit.get("k") != "damage")):
                continue
            side, target = rnd.team.get(hit.get("by")), hit.get("target")
            if side is None or rnd.team.get(target) in (None, side):
                continue
            source_id = hit.get("src") if status is not None else hit.get("id")
            cast_key = (source_id, hit.get("by"))
            cast = by_id.get(cast_key)
            t = float(hit["t"])
            if cast is None:
                if status is None:  # Boom Bot damage must name its exact source object.
                    continue
                unmatched.append((hit.get("by"), t))
            else:
                first_hits[cast_key] = min(t, first_hits.get(cast_key, math.inf))
            if not rnd.alive(target, t):
                continue
            p = rnd.pos(target, t)
            if p is None:
                rnd.miss("creature hit without a position (locates nothing)", target)
                continue
            out.append(Info(t, side, target, "locate", hit_reasons[figure], x=p[0], y=p[1],
                            z=rnd.height(target, t), source=f"{key}@{source_id}",
                            detail={"needs": "status"}, **_identity(cast if cast is not None else hit)))
        for row in casts:
            side, path, yaws = rnd.team.get(row.get("by")), row.get("path"), row.get("yaws")
            if side is None or row.get("hits_read") is not True or not CREATURE_MISSES[figure] or not path or not yaws:
                continue
            start = max(float(row["t"]), float(path[0][0]), float(yaws[0][0]), 0.0)
            run_end = float(row["t1"]) if row.get("t1") is not None else rnd.t_end
            end = min(run_end, float(path[-1][0]), rnd.t_end)
            if any(by == row.get("by") and float(row["t"]) - UNMATCHED_HIT_WINDOW_S <= t
                   <= run_end + UNMATCHED_HIT_WINDOW_S for by, t in unmatched):
                continue
            first = first_hits.get((row.get("id"), row.get("by")), math.inf)
            params = FIGURES["creature"][figure]
            for step in range(math.ceil(start / ce.TICK_STEP_S), math.floor(end / ce.TICK_STEP_S) + 1):
                t = round(step * ce.TICK_STEP_S, 6)
                if t >= first or t >= run_end or t >= rnd.t_end:
                    break
                at = ce.path_at(path, t)
                if at is None:
                    continue
                x, y, _ = at
                yaw = float(ce._step_at(yaws, t))
                own = eye = None
                z = rnd._device_z(ce.path_z(path, t))
                if geo.heights is not None:
                    own = geo.node_at(geo.cell_of_px(x, y), None if z is None else z + ce.hc.STAND_M)
                    eye = None if z is None or np.isnan(geo.node_z[own]) else z + ce.hc.DEVICE_EYE_M
                mask = ce.cast(geo, x, y, yaw + np.arange(-params["half_deg"], params["half_deg"] + 1e-9,
                                                        ce.RAY_STEP_DEG), rnd.smokes_at(t), eye_z=eye, own=own)
                mask &= geo.walk_n & (((geo.centres[:, 0] - x) ** 2 + (geo.centres[:, 1] - y) ** 2)
                                     <= (params["range_m"] / geo.m_per_px) ** 2)
                if mask.any():
                    out.extend(zero_hits(rnd, t, side, miss_reasons[figure], needs="status", mask=mask,
                                         source=f"{key}@{row.get('id', row['t'])}", **_identity(row)))
    return out


def read_hunters_fury(rnd) -> list[Info]:
    """Each fired, fully read blast clears its forward strip through walls for enemies it did not hit."""
    out = []
    for row in rnd.blob.get("util") or []:
        if row.get("k") != "blast" or row.get("code") != "Hunter" or row.get("name") != "X_LaserMulti":
            continue
        side, t = rnd.team.get(row.get("by")), float(row["t"])
        if side is None:
            continue
        place = {"source": f"Hunter_X_LaserMulti@{t}", **_identity(row)}
        hits = set(row.get("hits") or [])
        for target in sorted(hits & set(_living_enemies(rnd, side, t))):
            p = rnd.pos(target, t)
            if p is None:
                rnd.miss("Fury hit without a position (locates nothing)", target)
                continue
            out.append(Info(t, side, target, "locate", "hunters_fury_hit", x=p[0], y=p[1],
                            z=rnd.height(target, t), detail={"needs": "reveal"}, **place))
        if row.get("hits_read") is not True or row.get("fired") is not True or row.get("yaw") is None:
            continue
        origin = _count_disk(row)
        if origin is None:
            continue
        d = (rnd.geo.centres - (origin["x"], origin["y"])) * rnd.geo.m_per_px
        a = math.radians(row["yaw"])
        along = d[:, 0] * math.cos(a) + d[:, 1] * math.sin(a)
        across = -d[:, 0] * math.sin(a) + d[:, 1] * math.cos(a)
        params = FIGURES["hunters_fury"]
        mask = (rnd.geo.walk_n & (along >= 0) & (along <= params["range_m"])
                & (np.abs(across) <= params["width_m"] / 2))
        if mask.any():
            out.extend(i for i in zero_hits(rnd, t, side, "hunters_fury_miss", needs="reveal", mask=mask, **place)
                       if i.slot not in hits)
    return out


def read_seize(rnd) -> list[Info]:
    """Visible Seize tethers locate caught enemies at each hit, conditional on the tick's sight."""
    rows, out = rnd.blob.get("util") or [], []
    casts = {(r["id"], r.get("by")): r for r in rows
             if r.get("k") == "ability" and r.get("code") == "BountyHunter"
             and r.get("name") == "Q_Tether_SphereExpansion" and r.get("id") is not None}
    seen = set()
    for hit in rows:
        if (hit.get("k") != "status" or hit.get("status") != "tethered" or hit.get("friendly")
                or hit.get("code") != "BountyHunter" or hit.get("name") != "Q_Tether_SphereExpansion"):
            continue
        row = casts.get((hit.get("src"), hit.get("by")))
        side, target, t = rnd.team.get(hit.get("by")), hit.get("target"), float(hit["t"])
        key = (hit.get("src"), hit.get("by"), target, t)
        if (row is None or side is None or rnd.team.get(target) in (None, side)
                or not rnd.alive(target, t) or key in seen):
            continue
        seen.add(key)
        centre, p = _count_disk(row), rnd.pos(target, t)
        if centre is None or p is None:
            continue
        node = rnd.geo.node_at_px(centre["x"], centre["y"], rnd._device_z(row.get("z")))
        out.append(Info(t, side, target, "locate", "seize_hit", x=p[0], y=p[1], z=rnd.height(target, t),
                        source=centre["source"], detail={"needs": "status", "needs_view": node}, **_identity(row)))
    return out


def read_fakeout(rnd) -> list[Info]:
    """A seen decoy flash gives one anonymous shooter in its forward wedge, regardless of blinds."""
    from app.control import engine as ce

    geo, out = rnd.geo, []
    for row in rnd.blob.get("util") or []:
        if (row.get("k") != "ability" or row.get("code") != "Stealth" or row.get("name") != "4_Decoy_V2"
                or row.get("fired") is not True):
            continue
        flash, side = row.get("flash") or {}, rnd.team.get(row.get("by"))
        if side is None or flash.get("t") is None or flash.get("yaw") is None:
            continue
        t = float(flash["t"])
        place = _count_disk(row)
        z = rnd._device_z(row.get("z"))
        if row.get("path"):
            at = ce.path_at(row["path"], t)
            if at is None:
                continue
            place = {"x": at[0], "y": at[1], "source": f"Stealth_4_Decoy_V2@{row.get('id', row['t'])}"}
            z = rnd._device_z(ce.path_z(row["path"], t))
        if place is None:
            continue
        x, y = place["x"], place["y"]
        delta = (geo.centres - (x, y)) * geo.m_per_px
        heading = np.degrees(np.arctan2(delta[:, 1], delta[:, 0]))
        offset = np.abs((heading - float(flash["yaw"]) + 180) % 360 - 180)
        params = FIGURES["fakeout"]
        mask = geo.walk_n & (offset <= params["half_deg"]) & (np.linalg.norm(delta, axis=1) <= params["range_m"])
        if mask.any():
            out.append(Info(t, side, ONE_OF_SLOT, "one_of", "fakeout_direction", mask=mask, count=1,
                            source=place["source"], detail={"needs": "status", "needs_view": geo.node_at_px(x, y, z)},
                            **_identity(row)))
    return out


# The readers that run, by name and in order (names, so the list is one of the constants CONTROL_REVISION pins:
# tests/replays/test_control_format.py).
READERS = ("read_reveals", "read_pulses", "read_knives", "read_skye_flashes", "read_leers", "read_omen_ults",
           "read_waylay_recalls", "read_hush_suspensions", "read_nightfall_counts", "read_dizzy_counts",
           "read_thrash_detains", "read_storm_surges", "read_creatures", "read_hunters_fury",
           "read_seize", "read_fakeout")


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
        return info.mask.astype(bool) & geo.walk_n & ~geo.is_top
    if info.x is None or info.y is None or info.radius_m is None:
        return np.zeros(geo.n, bool)
    r = info.radius_m / geo.m_per_px
    near = (geo.centres[:, 0] - info.x) ** 2 + (geo.centres[:, 1] - info.y) ** 2 <= r * r
    return near & geo.walk_n & ~geo.is_top
