"""The timing-gaps pattern page's data (docs/superpowers/plans/2026-10-04-timing-gaps-pattern-page.md, P1 and its
review amendments; docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 9 and decision 16).

- `eligible_rounds`: which replay rounds of a map count (gap run `ok` and its fingerprint the one the round's
  stored control fingerprint gives now), and how many were left out as stale, failed or not yet computed.
- `load_rows`: the gap rows of counted rounds, filtered (`Filters`), as plain dicts; `route` only when asked.
- `friend_slots` / `apply_population`: Everyone, or Friends (the viewer and the friendships they own) resolved
  at query time through `replay_players.match_player_id -> match_players.player_id`, linked replays only.
- `group_patterns`: by full choke sequence, with each figure's `n`; predicted gaps and back-shots never summed.
- `shape_groups`: the empty sequence's rows grouped by route shape, per side.
- `page_data` (everything the page needs) and `pattern_routes` (the selected pattern's routes).

Standard library and the DB only: no app.control, app.gaps or numpy (tests/replays/test_control_isolation.py).
Nothing here reads scripts/tracked_players.json."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only

from app.models import Match, MatchPlayer
from app.models.replay import Replay, ReplayGap, ReplayPlayer, ReplayRoundControl, ReplayRoundGapRun
from app.replays import choke_assets
from app.replays import db as replay_db
from app.services import replay_control, replay_gaps
from app.services.friends import list_friend_ids
from app.services.replay_gaps_view import choke_points

SHAPE_THRESHOLD_M = 4.0
SHAPE_POINTS = 16
SHAPE_CAP = 2000          # rows per map and side, the most recent
PX = 1024                 # the minimap's pixels, as app.control.geometry's PX (not imported)

KINDS = ("both", "predicted", "backshot")
SIDES = ("both", "attack", "defense")
CAUSES = ("any", "route_released", "victim_turned", "victim_moved", "open_timing")
USES = ("any", "stood", "shot", "killed", "unused")
POPS = ("everyone", "friends")
DIRS = ("left", "opponents")
_TRUE = ("1", "true", "on", "yes")

# Every column the page reads, `route` aside (loaded only when asked: review amendment 8).
_COLUMNS = ("replay_id", "round_number", "seq", "kind", "victim_slot", "victim_side", "t_open", "flicker", "cause",
            "choke_seq", "candidate_slots", "stood_at", "shot_at", "killed_at", "context", "linked_seq")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- filters


@dataclass(frozen=True)
class Filters:
    kind: str = "both"
    side: str = "both"
    cause: str = "any"
    use: str = "any"
    t_max: float | None = None
    flickers: bool = False
    pop: str = "everyone"
    dir: str = "left"

    @classmethod
    def parse(cls, params: Mapping) -> "Filters":
        """From query parameters; a missing or bad value is the default."""
        def pick(name, allowed):
            value = params.get(name)
            return value if value in allowed else allowed[0]

        t_max = None
        try:
            raw = params.get("t_max")
            if raw not in (None, ""):
                value = float(raw)
                if math.isfinite(value) and value >= 0:
                    t_max = value
        except (TypeError, ValueError):
            t_max = None
        return cls(kind=pick("kind", KINDS), side=pick("side", SIDES), cause=pick("cause", CAUSES),
                   use=pick("use", USES), t_max=t_max,
                   flickers=str(params.get("flickers", "")).lower() in _TRUE,
                   pop=pick("pop", POPS), dir=pick("dir", DIRS))

    def as_params(self) -> dict:
        """The non-default values, for a link to this filtered view."""
        default = Filters()
        out = {}
        for key, value in asdict(self).items():
            if value != getattr(default, key):
                out[key] = ("1" if value else "0") if isinstance(value, bool) else value
        return out


def parse(params: Mapping) -> Filters:
    return Filters.parse(params)


# ---------------------------------------------------------------- which rounds count


@dataclass
class ReplayInfo:
    match_uuid: str
    linked: bool
    sort_date: datetime          # matches.played_at, else replays.created_at (review amendment n4)


@dataclass
class Eligible:
    rounds: set = field(default_factory=set)                 # {(replay_id, round_number)}
    left_out: dict = field(default_factory=lambda: {"stale": 0, "failed": 0, "not_computed": 0})
    old_blob: int = 0                                         # rounds of replays too old to compute: not counted
    replays: dict = field(default_factory=dict)               # replay_id -> ReplayInfo, every valid replay


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _EPOCH
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def eligible_rounds(db, map_name: str) -> Eligible:
    """The counted rounds of valid replays of this map: the gap run is `ok` and its fingerprint equals
    `gap_fingerprint(<the round's stored ok control row's fingerprint>, map)` (review amendment 6); the choke and
    hearing hashes are computed once. A round of an `old_blob` replay can never be computed, so it is counted
    apart, not as "not computed"."""
    out = Eligible()
    replays = (db.query(Replay)
               .options(load_only(Replay.id, Replay.match_uuid, Replay.match_id, Replay.map_name, Replay.round_count,
                                  Replay.format_version, Replay.recipe, Replay.link_status, Replay.created_at))
               .filter(Replay.map_name == map_name).all())
    if not replays:
        return out
    valid = replay_control._valid_ids(db, replays)
    replays = [r for r in replays if r.id in valid]
    match_ids = [r.match_id for r in replays if r.match_id is not None]
    played = dict(db.query(Match.id, Match.played_at).filter(Match.id.in_(match_ids))) if match_ids else {}
    ids = [r.id for r in replays]
    control = {(rid, n): (status, fp) for rid, n, status, fp in db.query(
        ReplayRoundControl.replay_id, ReplayRoundControl.round_number, ReplayRoundControl.status,
        ReplayRoundControl.fingerprint).filter(ReplayRoundControl.replay_id.in_(ids))} if ids else {}
    runs = {(rid, n): (status, fp) for rid, n, status, fp in db.query(
        ReplayRoundGapRun.replay_id, ReplayRoundGapRun.round_number, ReplayRoundGapRun.status,
        ReplayRoundGapRun.fingerprint).filter(ReplayRoundGapRun.replay_id.in_(ids))} if ids else {}
    chokes, hearing = choke_assets.asset_hash(map_name), replay_gaps.hearing_hash()
    wanted: dict[str, str] = {}
    for replay in replays:
        out.replays[replay.id] = ReplayInfo(
            str(replay.match_uuid), replay_db.is_linked(replay),
            _aware(played.get(replay.match_id)) if played.get(replay.match_id) else _aware(replay.created_at))
        if replay_control.blob_too_old(replay):
            out.old_blob += replay.round_count
            continue
        for n in range(1, replay.round_count + 1):
            run = runs.get((replay.id, n))
            if run is None:
                out.left_out["not_computed"] += 1
                continue
            if run[0] != "ok":
                out.left_out["failed"] += 1
                continue
            row = control.get((replay.id, n))
            if row is not None and row[0] == "ok":
                if row[1] not in wanted:
                    wanted[row[1]] = replay_gaps.gap_fingerprint(row[1], map_name, chokes=chokes, hearing=hearing)
                if run[1] == wanted[row[1]]:
                    out.rounds.add((replay.id, n))
                    continue
            out.left_out["stale"] += 1
    return out


# ---------------------------------------------------------------- rows


def _t_round(row: dict) -> float | None:
    value = (row.get("context") or {}).get("t_round")
    return None if value is None else float(value)


def _keep(row: dict, f: Filters) -> bool:
    predicted = row["kind"] == "predicted"
    if not f.flickers and row["flicker"]:
        return False
    if f.kind != "both" and row["kind"] != f.kind:
        return False
    if f.side != "both" and row["victim_side"] != f.side:
        return False
    if f.cause != "any" and predicted and row["cause"] != f.cause:
        return False
    if f.use == "killed" and row["killed_at"] is None:          # both kinds (review amendment n5)
        return False
    if predicted and f.use == "stood" and row["stood_at"] is None:
        return False
    if predicted and f.use == "shot" and row["shot_at"] is None:
        return False
    if predicted and f.use == "unused" and any(row[k] is not None for k in ("stood_at", "shot_at", "killed_at")):
        return False
    if f.t_max is not None:
        t = _t_round(row)
        if t is None or t > f.t_max:
            return False
    return True


def load_rows(db, map_name: str, eligible: Eligible, filters: Filters, with_routes: bool = False) -> list[dict]:
    """The rows of counted rounds that pass the filters (population aside), as dicts with the replay's
    `match_uuid` and `sort_date` added; `route` is selected only when `with_routes`."""
    ids = sorted({rid for rid, _ in eligible.rounds})
    if not ids:
        return []
    names = _COLUMNS + (("route",) if with_routes else ())
    query = (db.query(*(getattr(ReplayGap, c) for c in names))
             .filter(ReplayGap.map == map_name, ReplayGap.replay_id.in_(ids))
             .order_by(ReplayGap.replay_id, ReplayGap.round_number, ReplayGap.seq))
    out = []
    for values in query:
        row = dict(zip(names, values))
        if (row["replay_id"], row["round_number"]) not in eligible.rounds or not _keep(row, filters):
            continue
        info = eligible.replays[row["replay_id"]]
        row["match_uuid"], row["sort_date"] = info.match_uuid, info.sort_date
        out.append(row)
    return out


def load_routes(db, rows: list[dict], chunk: int = 200) -> list[dict]:
    """`rows` with their `route` filled in, read for those rows' keys only."""
    want: dict[tuple, list] = defaultdict(list)
    for row in rows:
        want[row["replay_id"], row["round_number"]].append(row["seq"])
    found = {}
    keys = sorted(want)
    for i in range(0, len(keys), chunk):
        clause = or_(*(and_(ReplayGap.replay_id == rid, ReplayGap.round_number == n, ReplayGap.seq.in_(want[rid, n]))
                       for rid, n in keys[i:i + chunk]))
        for rid, n, seq, route in db.query(ReplayGap.replay_id, ReplayGap.round_number, ReplayGap.seq,
                                           ReplayGap.route).filter(clause):
            found[rid, n, seq] = route
    return [{**row, "route": found.get((row["replay_id"], row["round_number"], row["seq"])) or []} for row in rows]


# ---------------------------------------------------------------- populations


def friend_slots(db, replay_ids, player_ids) -> dict[int, set[int]]:
    """{replay_id: {slot}} of these players in these replays: the `replay_players.match_player_id ->
    match_players.player_id` join of replay_control_views.friends_replay_ids. The caller passes linked replays
    only."""
    replay_ids, player_ids = sorted(set(replay_ids)), sorted(set(player_ids))
    if not replay_ids or not player_ids:
        return {}
    out: dict[int, set[int]] = defaultdict(set)
    for rid, slot in (db.query(ReplayPlayer.replay_id, ReplayPlayer.slot)
                      .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
                      .filter(ReplayPlayer.replay_id.in_(replay_ids), MatchPlayer.player_id.in_(player_ids))):
        out[rid].add(int(slot))
    return dict(out)


def group_player_ids(db, viewer_player_id: int | None) -> set[int]:
    """The viewer and the friends they own (as /stats); empty when logged out."""
    if viewer_player_id is None:
        return set()
    return {viewer_player_id} | set(list_friend_ids(db, viewer_player_id))


def apply_population(db, rows: list[dict], eligible: Eligible, filters: Filters,
                     viewer_player_id: int | None) -> tuple[list[dict], str]:
    """(rows, population used). Everyone: every row. Friends (logged in only; else Everyone): rows of linked
    replays whose victim (`dir=left`) or any candidate slot, a back-shot's shooter included (`dir=opponents`), is
    one of the group's slots."""
    if filters.pop != "friends" or viewer_player_id is None:
        return rows, "everyone"
    linked = {row["replay_id"] for row in rows if eligible.replays[row["replay_id"]].linked}
    slots = friend_slots(db, linked, group_player_ids(db, viewer_player_id))
    out = []
    for row in rows:
        mine = slots.get(row["replay_id"])
        if not mine:
            continue
        if filters.dir == "left":
            hit = int(row["victim_slot"]) in mine
        else:
            hit = any(int(s) in mine for s in row["candidate_slots"] or [])
        if hit:
            out.append(row)
    return out, "friends"


# ---------------------------------------------------------------- patterns and figures


def _share(k: int, n: int) -> dict:
    return {"k": k, "n": n}


def figures(rows: list[dict]) -> dict:
    """A group's figures, each with its `n`: predicted gaps and back-shots apart, never summed."""
    pred = [r for r in rows if r["kind"] == "predicted"]
    back = [r for r in rows if r["kind"] == "backshot"]
    causes = Counter(r["cause"] for r in pred if r["cause"])
    cause = None
    if causes:
        name, k = sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        cause = {"name": name, "k": k, "n": len(pred)}
    times = [t for t in (_t_round(r) for r in pred) if t is not None]
    return {
        "predicted": {
            "n": len(pred),
            "stood": _share(sum(r["stood_at"] is not None for r in pred), len(pred)),
            "shot": _share(sum(r["shot_at"] is not None for r in pred), len(pred)),
            "killed": _share(sum(r["killed_at"] is not None for r in pred), len(pred)),
            "cause": cause,
            "median_t_round": round(statistics.median(times), 1) if times else None,
            "t_round_n": len(times),
        },
        "backshots": {
            "n": len(back),
            "linked": sum(r["linked_seq"] is not None for r in back),
            "standalone": sum(r["linked_seq"] is None for r in back),
            "killed": _share(sum(r["killed_at"] is not None for r in back), len(back)),
        },
    }


def _ref(row: dict) -> dict:
    """A row's viewer link: /replays/{match_uuid}?round={round}&t={t_open}."""
    return {"match_uuid": row["match_uuid"], "round": row["round_number"], "seq": row["seq"],
            "t_open": round(float(row["t_open"]), 3), "kind": row["kind"]}


def _order(row: dict) -> tuple:
    return (row["sort_date"], row["replay_id"], row["round_number"], row["seq"])


def group_patterns(rows: list[dict]) -> dict:
    """{"patterns": [...], "unknown_seq": n}: rows grouped by full choke sequence (a null sequence is left out and
    counted), sorted by predicted count, then back-shot count, then sequence (review amendment n3)."""
    groups: dict[tuple, list] = defaultdict(list)
    unknown = 0
    for row in rows:
        if row["choke_seq"] is None:
            unknown += 1
            continue
        groups[tuple(int(c) for c in row["choke_seq"])].append(row)
    patterns = []
    for seq, members in groups.items():
        fig = figures(members)
        patterns.append({"choke_seq": list(seq), "key": "-".join(str(c) for c in seq), **fig,
                         "rounds": [_ref(r) for r in sorted(members, key=_order)]})
    patterns.sort(key=lambda p: (-p["predicted"]["n"], -p["backshots"]["n"], p["choke_seq"]))
    return {"patterns": patterns, "unknown_seq": unknown}


# ---------------------------------------------------------------- route shape


def _side_key(side: str | None) -> str:
    return side if side in ("attack", "defense") else "none"


def resample(route: list, points: int = SHAPE_POINTS) -> list[tuple[float, float]] | None:
    """A stored route (pieces of [t, x, y] points) joined in order, as `points` equally spaced (x, y) along its
    length. None when it has no points."""
    pts = [(float(p[1]), float(p[2])) for piece in route or [] for p in piece or []]
    if not pts:
        return None
    if len(pts) == 1:
        return [pts[0]] * points
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    total = cum[-1]
    if total == 0:
        return [pts[0]] * points
    out, j = [], 0
    for i in range(points):
        d = total * i / (points - 1)
        while j < len(pts) - 2 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        u = 0.0 if seg == 0 else min(1.0, max(0.0, (d - cum[j]) / seg))
        (x0, y0), (x1, y1) = pts[j], pts[j + 1]
        out.append((x0 + (x1 - x0) * u, y0 + (y1 - y0) * u))
    return out


def route_distance_m(a, b, m_per_px: float) -> float:
    return sum(math.dist(p, q) for p, q in zip(a, b)) / len(a) * m_per_px


def shape_selection(rows: list[dict]) -> tuple[dict[str, list[dict]], dict[str, bool]]:
    """The empty-sequence rows per side ("attack", "defense", "none"), in the fixed order (match date, replay id,
    round, seq), cut to the SHAPE_CAP most recent; and which sides were cut."""
    by_side: dict[str, list] = defaultdict(list)
    for row in rows:
        if row["choke_seq"] is not None and len(row["choke_seq"]) == 0:
            by_side[_side_key(row["victim_side"])].append(row)
    chosen, cut = {}, {}
    for side, members in by_side.items():
        members.sort(key=_order)
        cut[side] = len(members) > SHAPE_CAP
        chosen[side] = members[-SHAPE_CAP:] if cut[side] else members
    return chosen, cut


def shape_groups(rows: list[dict], m_per_px: float, cut: dict[str, bool] | None = None) -> dict:
    """{side: {"groups": [...], "cut", "rows", "no_route"}} for the empty choke sequence: greedy, in the fixed
    order, a route joins the first group whose founding route is within SHAPE_THRESHOLD_M (mean distance of the
    16 matching points, in metres), else founds a new group. Rows need `route`. Each group has its figures, its
    rows' links and their resampled points (minimap pixels) for drawing."""
    chosen, was_cut = shape_selection(rows)
    out = {}
    for side, members in chosen.items():
        groups: list[dict] = []
        no_route = 0
        for row in members:
            pts = resample(row.get("route"))
            if pts is None:
                no_route += 1
                continue
            for g in groups:
                if route_distance_m(g["founder"], pts, m_per_px) <= SHAPE_THRESHOLD_M:
                    g["members"].append((row, pts))
                    break
            else:
                groups.append({"founder": pts, "members": [(row, pts)]})
        listed = []
        for k, g in enumerate(groups, start=1):
            members_rows = [r for r, _ in g["members"]]
            listed.append({"index": k, "n": len(members_rows), **figures(members_rows),
                           "rounds": [{**_ref(r), "points": [[round(x, 1), round(y, 1)] for x, y in p]}
                                      for r, p in g["members"]]})
        out[side] = {"groups": listed, "cut": bool(was_cut.get(side) or (cut or {}).get(side)),
                     "rows": len(members), "no_route": no_route}
    return out


def m_per_px(map_name: str) -> float | None:
    """Metres per minimap pixel, as app.control.geometry derives it from maps.json's xMultiplier (not imported)."""
    inputs = replay_control.geometry_inputs(map_name)
    scale = (inputs or {}).get("scale")
    return 1.0 / (float(scale) * PX * 100) if scale else None


# ---------------------------------------------------------------- the page


def _population_rows(db, map_name, filters, viewer_player_id, with_routes=False):
    eligible = eligible_rounds(db, map_name)
    rows = load_rows(db, map_name, eligible, filters, with_routes=with_routes)
    rows, population = apply_population(db, rows, eligible, filters, viewer_player_id)
    return eligible, rows, population


def page_data(db, map_name: str, filters: Filters, viewer_player_id: int | None) -> dict:
    """Everything the pattern page shows. Routes are read only for the empty sequence's rows (route shape); a
    selected pattern's routes come from `pattern_routes`."""
    eligible, rows, population = _population_rows(db, map_name, filters, viewer_player_id)
    grouped = group_patterns(rows)
    scale = m_per_px(map_name)
    shapes = None
    if scale is not None:
        chosen, cut = shape_selection(rows)
        shapes = shape_groups(load_routes(db, [r for side in chosen.values() for r in side]), scale, cut)
    return {
        "map": map_name,
        "filters": asdict(filters),
        "params": filters.as_params(),
        "population": population,
        "direction": filters.dir if population == "friends" else None,
        "friends_available": viewer_player_id is not None,
        "rounds": {"counted": len(eligible.rounds), "left_out": dict(eligible.left_out),
                   "old_blob": eligible.old_blob, "replays": len({rid for rid, _ in eligible.rounds})},
        "rows": {"total": len(rows), "predicted": sum(r["kind"] == "predicted" for r in rows),
                 "backshot": sum(r["kind"] == "backshot" for r in rows), "unknown_seq": grouped["unknown_seq"]},
        "patterns": grouped["patterns"],
        "shapes": shapes,
        "shape_threshold_m": SHAPE_THRESHOLD_M,
        "shape_cap": SHAPE_CAP,
        "chokes": choke_points(map_name),
    }


def pattern_routes(db, map_name: str, choke_seq, filters: Filters, viewer_player_id: int | None) -> list[dict]:
    """The routes of one pattern (its full choke sequence) under the same filters and population: each row's
    viewer link plus its stored `route` (pieces of [t, x, y], minimap pixels)."""
    want = [int(c) for c in choke_seq]
    _, rows, _ = _population_rows(db, map_name, filters, viewer_player_id)
    rows = [r for r in rows if r["choke_seq"] is not None and [int(c) for c in r["choke_seq"]] == want]
    rows.sort(key=_order)
    return [{**_ref(r), "route": r["route"]} for r in load_routes(db, rows)]
