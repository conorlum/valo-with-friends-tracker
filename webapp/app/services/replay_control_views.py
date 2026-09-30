"""Map control's per-replay views, read from the stored summaries (docs/replay-map-control-plan.md,
"Per-player control", "Where it shows"; docs/map-control-stages-4-7-impl.md, S4.1).

- `load_round_summaries`: one query for a replay's ok rows; each `summary` unpacked
  (app/replays/control_format.py). Never the ticks.
- `player_tables`: per round and per match, each player's control, coverage, active ratio and lost
  control at death, and each team's redundant control, for the viewer's Control tab.
- `match_heatmap_for`: the match heatmap (Stage 5): per time section, the share of time each cell
  was held by each side (attack/defense) or team, or contested, summed over the rounds.

Standard library and the DB only: the engine (app/control) is never imported here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import zlib
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field

from sqlalchemy.orm import load_only

from app.models.replay import Replay, ReplayRoundControl
from app.replays import control_format as cf
from app.services import replay_control

# Bumped when these views' output changes for the same rows, so ETags change with it.
VIEWS_VERSION = 1


@dataclass
class RoundSummaries:
    summaries: dict[int, dict] = field(default_factory=dict)   # round -> unpacked summary
    fingerprints: dict[int, str] = field(default_factory=dict)
    versions: dict[int, str] = field(default_factory=dict)      # round -> fingerprint and computed time
    stale: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)
    etag: str = '""'


def load_round_summaries(db, replay: Replay) -> RoundSummaries:
    rows = (db.query(ReplayRoundControl)
            .options(load_only(ReplayRoundControl.round_number, ReplayRoundControl.status,
                               ReplayRoundControl.fingerprint, ReplayRoundControl.summary,
                               ReplayRoundControl.computed_at))
            .filter(ReplayRoundControl.replay_id == replay.id, ReplayRoundControl.status == "ok")
            .order_by(ReplayRoundControl.round_number).all())
    groups = replay_control.side_groups(db, replay)
    out = RoundSummaries()
    tag = hashlib.sha256(f"views{VIEWS_VERSION};".encode("utf-8"))
    for row in rows:
        n = row.round_number
        stale = row.fingerprint != replay_control.round_fingerprint(replay, groups, n)
        # The stale flag is in the tag too: a geometry or link change makes rows stale without touching them.
        tag.update(f"{n}:{row.fingerprint}:{row.computed_at}:{int(stale)};".encode("utf-8"))
        try:
            out.summaries[n] = cf.unpack_summary(row.summary)
        except (cf.ControlFormatError, ValueError, OSError):
            out.unreadable.append(n)
            continue
        out.fingerprints[n] = row.fingerprint
        out.versions[n] = f"{row.fingerprint}:{row.computed_at}"
        if stale:
            out.stale.append(n)
    out.etag = '"' + tag.hexdigest()[:16] + '"'
    return out


# ---------------------------------------------------------------- the player table


def _per_second(m2s: float, seconds: float) -> float | None:
    return round(m2s / seconds, 1) if seconds > 0 else None


def _ratio(active: float, passive: float) -> float | None:
    both = active + passive
    return round(active / both, 3) if both > 0 else None


def _live_seconds(summary: dict) -> float:
    """The live round's length: its heatmap sections' seconds, which the engine accumulates over the
    same window as the stats (`t_decided` itself can be null in a summary)."""
    return float(sum(float(s.get("seconds") or 0.0) for s in summary.get("sections") or []))


def _deaths(player: dict) -> list[dict]:
    """The deaths inside the live round: lost control counts only those."""
    return [d for d in player.get("deaths") or [] if d.get("live")]


def round_table(summary: dict) -> dict:
    """One round: each slot's numbers, and each side group's redundant control as an average m²
    over the live round (signed: the team's own area minus its players' control)."""
    live = _live_seconds(summary)
    players = {}
    for slot, p in (summary.get("players") or {}).items():
        deaths = _deaths(p)
        players[slot] = {
            "team": p.get("team"), "side": p.get("side"), "alive_s": p.get("alive_s"),
            "control_m2": p.get("control_m2"), "active_m2": p.get("active_m2"), "passive_m2": p.get("passive_m2"),
            "active_ratio": p.get("active_ratio"),
            "lost": [{"t": d.get("t"), "control_m2": d.get("control_m2"), "share_of_team": d.get("share_of_team"),
                      "by_level_m2": d.get("by_level_m2"), "went_to_m2": d.get("went_to_m2")} for d in deaths],
        }
    redundant = {group: _per_second(float(v), live) for group, v in (summary.get("redundant_m2s") or {}).items()}
    return {"live_s": round(live, 3), "group_side": summary.get("group_side") or {}, "players": players,
            "redundant_m2": redundant}


def match_table(summaries: dict[int, dict]) -> dict:
    """The whole match: m²·s summed over rounds, divided by the summed seconds alive (or live, for
    redundant control); lost control as the live deaths, their mean m², and their summed m² over the
    summed area the team held at those deaths. Side groups keep their players all match, so
    redundant control is per group."""
    acc: dict[str, dict] = {}
    redundant: dict[str, float] = {}
    live_total = 0.0
    for summary in summaries.values():
        live_total += _live_seconds(summary)
        for group, v in (summary.get("redundant_m2s") or {}).items():
            redundant[group] = redundant.get(group, 0.0) + float(v)
        for slot, p in (summary.get("players") or {}).items():
            a = acc.setdefault(slot, {"team": p.get("team"), "alive_s": 0.0, "active_m2s": 0.0, "passive_m2s": 0.0,
                                      "control_m2s": 0.0, "rounds": 0, "deaths": 0, "lost_m2": 0.0,
                                      "shared_lost_m2": 0.0, "held_m2": 0.0})
            a["rounds"] += 1
            for key in ("alive_s", "active_m2s", "passive_m2s", "control_m2s"):
                a[key] += float(p.get(key) or 0.0)
            for d in _deaths(p):
                lost, share = float(d.get("control_m2") or 0.0), d.get("share_of_team")
                a["deaths"] += 1
                a["lost_m2"] += lost
                if share:  # the team's own area at the death is lost / share
                    a["shared_lost_m2"] += lost
                    a["held_m2"] += lost / float(share)
    players = {}
    for slot, a in sorted(acc.items(), key=lambda kv: int(kv[0])):
        players[slot] = {
            "team": a["team"], "rounds": a["rounds"], "alive_s": round(a["alive_s"], 3),
            "control_m2": _per_second(a["control_m2s"], a["alive_s"]),
            "active_m2": _per_second(a["active_m2s"], a["alive_s"]),
            "passive_m2": _per_second(a["passive_m2s"], a["alive_s"]),
            "active_ratio": _ratio(a["active_m2s"], a["passive_m2s"]),
            "deaths": a["deaths"], "lost_m2": round(a["lost_m2"], 1),
            "lost_mean_m2": round(a["lost_m2"] / a["deaths"], 1) if a["deaths"] else None,
            # PROVISIONAL(D2): a ratio of sums; a mean of per-death shares is dominated by deaths when the
            # team held little (stored shares pass 100% then).
            "lost_share": round(a["shared_lost_m2"] / a["held_m2"], 4) if a["held_m2"] > 0 else None,
        }
    return {"rounds": len(summaries), "live_s": round(live_total, 3), "players": players,
            "redundant_m2": {group: _per_second(v, live_total) for group, v in sorted(redundant.items())}}


def player_tables(replay: Replay, loaded: RoundSummaries) -> dict:
    rounds: dict[str, dict] = {}
    for n in range(1, replay.round_count + 1):
        if n in loaded.summaries:
            rounds[str(n)] = {"status": "ok", "stale": n in loaded.stale, **round_table(loaded.summaries[n])}
        else:
            rounds[str(n)] = {"status": "unreadable" if n in loaded.unreadable else "missing"}
    return {"rounds": rounds, "match": match_table(loaded.summaries), "stale_rounds": sorted(loaded.stale)}


# ---------------------------------------------------------------- the match heatmap (Stage 5)

HEATMAP_VIEWS = ("side", "team")
# State codes (cf.STATE_NAMES) by what they add up to: held by side group A, by B, or contested.
_HELD_A, _HELD_B, _CONTESTED = (1, 2, 3), (4, 5, 6), (7, 8)
_CHANNEL = [None] * len(cf.STATE_NAMES)
for _code in _HELD_A:
    _CHANNEL[_code] = 0
for _code in _HELD_B:
    _CHANNEL[_code] = 1
for _code in _CONTESTED:
    _CHANNEL[_code] = 2
_SECTION_KEY = re.compile(r"^([rp])(\d+)$")
_HEATMAPS: OrderedDict = OrderedDict()   # (replay id, versions) -> _MatchTotals
_HEATMAP_CACHE_SIZE = 8


def read_walk(data: bytes) -> str:
    """The `walk` bitmap from a stored `data` blob's header, decompressing only the header."""
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = b""
    pos = 0
    while len(raw) < 9 and pos < len(data):
        raw += inflate.decompress(data[pos:pos + 4096], 0)
        pos += 4096
    if raw[:4] != cf.MAGIC:
        raise cf.ControlFormatError("not a control blob")
    (length,) = struct.unpack("<I", raw[5:9])
    while len(raw) < 9 + length and pos < len(data):
        raw += inflate.decompress(data[pos:pos + 65536], 0)
        pos += 65536
    return json.loads(raw[9:9 + length].decode("utf-8"))["walk"]


@dataclass
class _SectionTotals:
    seconds_a_attacks: float = 0.0     # section seconds from rounds where side group A attacks
    seconds_a_defends: float = 0.0
    rounds: int = 0
    # seconds per cell: [A held while A attacks, A held while A defends, B held while A attacks,
    # B held while A defends, contested]
    held: list = None


@dataclass
class _MatchTotals:
    walk: str
    cells: int
    sections: dict                     # key -> _SectionTotals
    used: list
    skipped: list


def _add_totals(text: str, cells: int, target: list, a_attacks: bool) -> None:
    """Adds one section's sparse totals (cf.encode_totals) into the per-cell channels."""
    buf = base64.b64decode(text)
    pos, end = 0, len(buf)
    a_slot, b_slot = (0, 2) if a_attacks else (1, 3)
    for state in range(1, len(cf.STATE_NAMES)):
        count, pos = cf.read_varint(buf, pos)
        channel = _CHANNEL[state]
        into = target[a_slot] if channel == 0 else target[b_slot] if channel == 1 else target[4]
        cell = -1
        for _ in range(count):
            # two varints: the index gap, then the time in 1/GRID_HZ s
            b = buf[pos]
            pos += 1
            gap = b & 0x7F
            shift = 7
            while b & 0x80:
                b = buf[pos]
                pos += 1
                gap |= (b & 0x7F) << shift
                shift += 7
            b = buf[pos]
            pos += 1
            units = b & 0x7F
            shift = 7
            while b & 0x80:
                b = buf[pos]
                pos += 1
                units |= (b & 0x7F) << shift
                shift += 7
            cell += gap + 1
            into[cell] += units
    if pos > end:
        raise cf.ControlFormatError("totals run past their end")


def _match_totals(summaries: dict[int, dict], walks: dict[int, str], newest: int | None) -> _MatchTotals | None:
    """Sums every usable round's section totals. Cell indices count a round's walkable cells, so only
    rounds with the same `walk` bitmap add up: the group of the newest computed round is used."""
    if newest is None or newest not in walks:
        return None
    walk = walks[newest]
    cells = summaries[newest]["cells"]
    used, skipped, sections = [], [], {}
    for n in sorted(summaries):
        s = summaries[n]
        if walks.get(n) != walk or s.get("cells") != cells:
            skipped.append(n)
            continue
        a_attacks = (s.get("group_side") or {}).get("A") == "attack"
        used.append(n)
        for sec in s.get("sections") or []:
            tot = sections.get(sec["key"])
            if tot is None:
                tot = sections[sec["key"]] = _SectionTotals(held=[array("d", bytes(8 * cells)) for _ in range(5)])
            if a_attacks:
                tot.seconds_a_attacks += float(sec["seconds"])
            else:
                tot.seconds_a_defends += float(sec["seconds"])
            tot.rounds += 1
            _add_totals(sec["totals"], cells, tot.held, a_attacks)
    if sections:  # the whole live round: every section summed
        whole = _SectionTotals(held=[array("d", bytes(8 * cells)) for _ in range(5)],
                               rounds=sum(1 for n in used if summaries[n].get("sections")))
        for tot in sections.values():
            whole.seconds_a_attacks += tot.seconds_a_attacks
            whole.seconds_a_defends += tot.seconds_a_defends
            for acc, add in zip(whole.held, tot.held):
                for c, v in enumerate(add):
                    if v:
                        acc[c] += v
        sections["all"] = whole
    return _MatchTotals(walk, cells, sections, used, skipped)


def _shares(values, seconds: float) -> str:
    """Seconds per cell (in 1/GRID_HZ units) as shares of `seconds`, one byte each (0-255), base64."""
    if seconds <= 0:
        return base64.b64encode(bytes(len(values))).decode("ascii")
    scale = 255.0 / (seconds * cf.GRID_HZ)
    return base64.b64encode(bytes(min(255, int(v * scale + 0.5)) for v in values)).decode("ascii")


def _section_label(key: str) -> str:
    if key == "all":
        return "Whole live round"
    m = _SECTION_KEY.match(key)
    if not m:
        return key
    lo = 10 * int(m.group(2))
    return f"{lo}–{lo + 10} s" + (" after the plant" if m.group(1) == "p" else "")


def _section_order(key: str) -> tuple:
    m = _SECTION_KEY.match(key)
    return (0, 0) if key == "all" else ((1 if m.group(1) == "r" else 2), int(m.group(2))) if m else (3, 0)


def match_heatmap(totals: _MatchTotals, view: str, group_team: dict[str, str]) -> dict:
    """The heatmap for one view: per section, each cell's share of the section's time held by side
    (or team) x, held by y, and contested; nobody holds the rest."""
    if view == "side":
        labels = {"x": "attack", "y": "defense"}
    else:
        a_team = group_team.get("A") or "team-1"
        labels = {"x": "team-1", "y": "team-2"}
        a_is_x = a_team == "team-1"
    sections = []
    for key in sorted(totals.sections, key=_section_order):
        tot = totals.sections[key]
        seconds = tot.seconds_a_attacks + tot.seconds_a_defends
        a_att, a_def, b_att, b_def, contested = tot.held
        if view == "side":   # attack holds: A where A attacks, B where A defends
            x = [a_att[c] + b_def[c] for c in range(totals.cells)]
            y = [b_att[c] + a_def[c] for c in range(totals.cells)]
        else:
            a = [a_att[c] + a_def[c] for c in range(totals.cells)]
            b = [b_att[c] + b_def[c] for c in range(totals.cells)]
            x, y = (a, b) if a_is_x else (b, a)
        sections.append({"key": key, "label": _section_label(key), "seconds": round(seconds, 2), "rounds": tot.rounds,
                         "x": _shares(x, seconds), "y": _shares(y, seconds), "contested": _shares(contested, seconds)})
    return {"status": "ok", "view": view, "labels": labels, "walk": totals.walk, "cells": totals.cells,
            "grid": cf.GRID, "rounds_used": totals.used, "rounds_skipped": totals.skipped, "sections": sections}


def match_heatmap_for(db, replay: Replay, view: str, loaded: RoundSummaries | None = None) -> dict:
    """The heatmap endpoint's body. Each replay's decoded totals are cached in-process by its rows'
    fingerprints and computed times, so the side and team views share one decode."""
    loaded = loaded or load_round_summaries(db, replay)
    key = (replay.id, tuple(sorted(loaded.versions.items())))
    totals = _HEATMAPS.get(key)
    if totals is None:
        rows = (db.query(ReplayRoundControl)
                .options(load_only(ReplayRoundControl.round_number, ReplayRoundControl.data,
                                   ReplayRoundControl.computed_at))
                .filter(ReplayRoundControl.replay_id == replay.id, ReplayRoundControl.status == "ok").all())
        walks, newest, newest_at = {}, None, None
        for row in rows:
            if row.round_number not in loaded.summaries:
                continue
            try:
                walks[row.round_number] = read_walk(row.data)
            except (cf.ControlFormatError, ValueError, OSError, zlib.error):
                continue
            if newest_at is None or row.computed_at >= newest_at:
                newest, newest_at = row.round_number, row.computed_at
        totals = _match_totals(loaded.summaries, walks, newest)
        if totals is None:
            return {"status": "missing", "view": view, "sections": [], "rounds_used": [], "rounds_skipped": []}
        _HEATMAPS[key] = totals
        while len(_HEATMAPS) > _HEATMAP_CACHE_SIZE:
            _HEATMAPS.popitem(last=False)
    else:
        _HEATMAPS.move_to_end(key)
    side_to_team = (replay.link_report or {}).get("side_to_team") or {}
    return match_heatmap(totals, view, {g: str(t) for g, t in side_to_team.items()})
