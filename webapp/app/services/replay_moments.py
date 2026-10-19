"""Pure functions that turn stored per-round moments, control deaths and
locating events into the JSON payload of the replay moments route.

The functions above `load_moments_payload` are pure (no DB, no FastAPI); the loader alone reads the DB.
"""
from __future__ import annotations

import statistics

from app.moments.constants import DEATH_MATCH_S, INFO_MERGE_S, MEDIAN_MIN_CLEAN
from app.services import enemy_names


def _slot_list(by_slot, slot) -> list:
    if not by_slot:
        return []
    if slot in by_slot:
        return by_slot[slot]
    return by_slot.get(str(slot), by_slot.get(_as_int(slot), []))


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _fights_of(slot, rnd_moments) -> list:
    fights = (rnd_moments or {}).get("fights") or []
    return [f for f in fights if str(f.get("slot")) == str(slot)]


def attach_deaths(fights, round_deaths) -> list[dict]:
    out = []
    for f in fights:
        c = dict(f)
        if f.get("died") is not None:
            died = dict(f["died"])
            best = None
            for d in _slot_list(round_deaths, f.get("slot")):
                gap = abs(d["t"] - died["t"])
                if gap <= DEATH_MATCH_S and (best is None or gap < best[0]):
                    best = (gap, d)
            died["control_m2"] = best[1].get("control_m2") if best else None
            died["share"] = best[1].get("share_of_team") if best else None
            c["died"] = died
        out.append(c)
    return out


def _merge(times: list[float]) -> list[float]:
    kept: list[float] = []
    prev = None
    for t in sorted(times):
        if prev is None or not (t - prev < INFO_MERGE_S):
            kept.append(t)
        prev = t
    return kept


def player_info(slot, events, moments) -> dict:
    slot = _as_int(slot)
    heard, gunfire, seen, util = [], [], [], []
    for n, groups in (events or {}).items():
        rnd = int(n)
        fights = _fights_of(slot, (moments or {}).get(n, (moments or {}).get(rnd)))
        by_reason: dict[str, list[float]] = {}
        for side in (groups or {}).values():
            for t, s, reason in side:
                if _as_int(s) != slot:
                    continue
                if reason in ("damage", "kill"):
                    continue
                if reason == "gunfire" and any(f["t0"] <= t <= f["t1"] for f in fights):
                    continue
                by_reason.setdefault(reason, []).append(t)
        for reason, ts in by_reason.items():
            for t in _merge(ts):
                if reason == "footsteps":
                    heard.append({"round": rnd, "t": t})
                elif reason == "gunfire":
                    gunfire.append({"round": rnd, "t": t})
                elif reason == "seen":
                    seen.append(t)
                else:
                    util.append({"round": rnd, "t": t, "reason": reason})
    key = lambda m: (m["round"], m["t"])
    return {
        "heard": sorted(heard, key=key),
        "gunfire_outside": sorted(gunfire, key=key),
        "found_by_utility": sorted(util, key=lambda m: (m["round"], m["t"], m["reason"])),
        "seen": len(seen),
    }


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _bucket(kds):
    n = len(kds)
    if n < MEDIAN_MIN_CLEAN:
        return {"ttk_shot": None, "ttk_spot": None, "spot_to_shot": None, "n": n}
    s2s = [k["first_shot"] - k["spot"] for k in kds
           if k.get("first_shot") is not None and k.get("spot") is not None]
    return {
        "ttk_shot": _median([k.get("ttk_shot") for k in kds]),
        "ttk_spot": _median([k.get("ttk_spot") for k in kds]),
        "spot_to_shot": _median(s2s),
        "n": n,
    }


def player_summary(slot, moments, deaths) -> dict:
    slot = _as_int(slot)
    rows = []
    for n, by_slot in (deaths or {}).items():
        rnd = int(n)
        fights = _fights_of(slot, (moments or {}).get(n, (moments or {}).get(rnd)))
        for d in _slot_list(by_slot, slot):
            fi = None
            for f in fights:
                if f.get("died") and abs(f["died"]["t"] - d["t"]) <= DEATH_MATCH_S:
                    fi = f.get("i")
                    break
            rows.append({"round": rnd, "t": d["t"], "control_m2": d.get("control_m2"),
                         "share": d.get("share_of_team"), "fight_i": fi})
    rows.sort(key=lambda r: (-(r["control_m2"] or 0), r["round"], r["t"]))

    clean = []
    for n, rm in (moments or {}).items():
        for f in _fights_of(slot, rm):
            clean.extend(k for k in f.get("kill_detail") or [] if k.get("clean") is True)
    buckets = {"all": clean}
    for b in ("on", "near", "off"):
        buckets[b] = [k for k in clean if k.get("band") == b]
    return {"expensive_deaths": rows[:3],
            "medians": {k: _bucket(v) for k, v in buckets.items()}}


def build_payload(*, players, attack_round1, moments, deaths, events, stale, failed=()) -> dict:
    """`failed`: rounds whose current moments row failed (the tab says so, rather than showing no fights)."""
    rounds = {}
    for n, m in (moments or {}).items():
        ok = m.get("status") == "ok"
        rd = (deaths or {}).get(n, (deaths or {}).get(_as_int(n), {}))
        rounds[str(n)] = {
            "status": m.get("status"),
            "fights": attach_deaths(m.get("fights") or [], rd) if ok else [],
        }
    summary = {}
    for slot in players:
        summary[str(slot)] = {**player_summary(slot, moments, deaths),
                              "info": player_info(slot, events, moments)}
    return {"players": players, "attack_round1": attack_round1, "rounds": rounds,
            "summary": summary, "stale": stale, "failed": sorted(int(n) for n in failed)}


def home_team(page_players: dict, friends: set[int]):
    """The team with more of the site's friends (players in any friendship) on it; None on a tie."""
    counts: dict = {}
    for p in page_players.values():
        counts.setdefault(p.get("team"), 0)
        counts[p.get("team")] += p.get("player_id") in friends
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def tab_players(page_players: dict, friends: set[int]) -> dict:
    """The tab's players: slot -> name, agent and team, the other team's named by agent (app/services/enemy_names.py).
    With no home team, everyone is named by agent: no opponent's name is ever shown."""
    players = {str(slot): {"name": p.get("name"), "agent": p.get("agent"), "team": p.get("team")}
               for slot, p in page_players.items()}
    return enemy_names.hide_in(players, home_team(page_players, friends))


def load_moments_payload(db, replay) -> dict:
    """The route's body for a linked replay with the control layer: reads the stored moments, control deaths and
    knowledge events, then hands them to build_payload."""
    from sqlalchemy import or_
    from sqlalchemy.orm import load_only

    from app.models.friendship import Friendship
    from app.models.replay import ReplayRoundControl, ReplayRoundMoments
    from app.replays import control_format
    from app.services import replay_control, replay_control_views, replay_gaps
    from app.services import replays as replay_service

    moments = {}
    stored = {}
    for row in db.query(ReplayRoundMoments).filter(ReplayRoundMoments.replay_id == replay.id):
        moments[row.round_number] = {"status": row.status, "fights": (row.fights or {}).get("fights")}
        stored[row.round_number] = row

    groups = replay_control.side_groups(db, replay)
    context = replay_control.resolve_current_geometry(db, replay.map_name)
    stale = 0
    failed = []
    for n in range(1, replay.round_count + 1):
        row = stored.get(n)
        if row is None or row.gaps_revision != replay_gaps.GAPS_REVISION:
            stale += 1
            continue
        control = replay_control.round_fingerprint(replay, groups, n, context=context)
        current = None if control is None else replay_gaps.gap_fingerprint(control, replay.map_name)
        if current is None or row.fingerprint != current:
            stale += 1
        elif row.status != "ok":
            failed.append(n)     # current but failed: the tab says so, not "no fights"

    loaded = replay_control_views.load_round_summaries(db, replay)
    tables = replay_control_views.player_tables(replay, loaded)
    deaths = {}
    for n, entry in tables["rounds"].items():
        if entry.get("status") == "ok":
            deaths[int(n)] = {slot: p["lost"] for slot, p in entry["players"].items()}

    events = {}
    rows = (db.query(ReplayRoundControl.round_number, ReplayRoundControl.data)
            .filter(ReplayRoundControl.replay_id == replay.id, ReplayRoundControl.status == "ok"))
    for n, data in rows:
        try:
            header, _ = control_format.unpack_data(data)
        except (control_format.ControlFormatError, ValueError, OSError):
            continue
        found = header.get("knowledge_events") or {}
        if found:
            events[n] = found

    page = replay_service.page_context(db, replay)["players"]
    ids = [p["player_id"] for p in page.values() if p.get("player_id") is not None]
    friends = {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id)
               .filter(or_(Friendship.owner_player_id.in_(ids), Friendship.friend_player_id.in_(ids)))
               for pid in pair} if ids else set()
    players = tab_players(page, friends)
    link = replay_control.round_link(replay, groups, 1)
    attack_round1 = sorted(int(s) for s, side in link["sides"].items() if side == "attack")
    return build_payload(players=players, attack_round1=attack_round1, moments=moments, deaths=deaths,
                         events=events, stale=stale, failed=failed)
