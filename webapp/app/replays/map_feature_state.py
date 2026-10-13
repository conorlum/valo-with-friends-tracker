"""The map-feature state reducer (docs/superpowers/plans/2026-10-04-map-interaction-tagger.md, "Behavior
contract: guarded transitions"; the frozen contract is docs/superpowers/specs/2026-10-04-map-features-contract.md).

Standard library only: this file is copied into the upload worker with the rest of app/replays, and its
twin in scripts/control_tagger_core.js (`TaggerCore.Features.run`) must give the same trace for every case
in tests/fixtures/control/map_features/reducer_cases.json.

A feature's behaviour is a transition table. Each row:

    {"id", "from": [state, ...] | "*", "event": kind, "name": (scheduled events only), "guard": {...} | None,
     "to": state, "motion": {"state", "duration"} | None, "follow_up": {"after", "name"} | None,
     "mid_motion": "ignore" | "restart" | "queue" | "reverse" | "unresolved"}

`duration` and `after` are value objects: {"status": "known", "value": seconds} or {"status": "unresolved"}.
A state is {"name", "terminal": bool, ...}; a terminal state (broken) rejects every later event except a
round reset.

Events: switch, shoot, activate (a cause not yet classified, e.g. a Summit drop), destroy, proximity_enter, proximity_leave (optional "occupant" id), reset, observed
({"state"}: a decoder's verified state), and the two the reducer schedules itself, motion_complete and
scheduled ({"name"}), which carry the (generation, epoch) they were scheduled under. A scheduled event whose
tokens no longer match is `stale`: an accepted transition, a destruction or a reset supersedes it.

Equal-time order (`PRIORITY`): reset, destroy, motion_complete, switch/shoot/activate/observed, proximity_enter,
proximity_leave, scheduled, then input order. So a destruction beats the obsolete completion of the motion it
cancels, and an occupant entering at the instant another leaves keeps a door open.

An event is answered by the first row whose `from`, event (and name) and guard match, in table order. While
the feature is moving, a row from the moving state answers first; with none, a proximity event only changes
occupancy, and any other event follows the moving transition's `mid_motion` policy: ignore it, queue it until
the motion completes, restart the motion, reverse it (back to its source, taking the time already spent), or
reject it as unresolved. Destruction is an ordinary row (usually `"from": "*"`) into a terminal state.

Occupancy: an occupant id counts once (duplicate evidence is ignored); events without an id count as
anonymous. The guard sees the occupancy after the event's own change. Simulated occupants are sandbox input;
the reducer never infers occupancy from a button press.
"""

from __future__ import annotations

import copy

GUARD_VOCABULARY = 1
EVENTS = ("switch", "shoot", "activate", "destroy", "proximity_enter", "proximity_leave", "reset", "observed",
          "motion_complete", "scheduled")
PRIORITY = {"reset": 0, "destroy": 1, "motion_complete": 2, "switch": 3, "shoot": 3, "activate": 3, "observed": 3,
            "proximity_enter": 4, "proximity_leave": 5, "scheduled": 6}
MID_MOTION = ("ignore", "restart", "queue", "reverse", "unresolved")
MAX_STEPS = 10000
GUARD_KEYS = ("all", "any", "not", "state_in", "occupants_eq", "occupants_gt", "motion_idle", "unresolved")


class FeatureStateError(ValueError):
    pass


def known(value) -> float | None:
    """A value object's seconds, or None when it is unresolved (or missing)."""
    if isinstance(value, dict) and value.get("status") == "known" and isinstance(value.get("value"), (int, float)) \
            and not isinstance(value.get("value"), bool):
        return float(value["value"])
    return None


def _states(feature: dict) -> dict:
    return {s["name"]: s for s in feature.get("states") or []}


def initial(feature: dict) -> dict:
    """The round-start state of a feature (epoch 0)."""
    return _fresh(feature, 0, 0)


def _fresh(feature: dict, epoch: int, generation: int) -> dict:
    return {"state": feature.get("initial_state"), "epoch": epoch, "generation": generation, "occupants": [],
            "anonymous": 0, "motion": None, "pending": [], "queue": []}


def occupancy(st: dict) -> int:
    return len(st["occupants"]) + st["anonymous"]


def guard_ok(guard, st: dict) -> bool | None:
    """True / False, or None when the guard is unresolved (the transition can't run)."""
    if guard is None:
        return True
    if not isinstance(guard, dict) or len(guard) != 1:
        raise FeatureStateError(f"a guard is one condition: {guard!r}")
    (key, arg), = guard.items()
    if key == "all":
        results = [guard_ok(g, st) for g in arg]
        return False if False in results else (None if None in results else True)
    if key == "any":
        results = [guard_ok(g, st) for g in arg]
        return True if True in results else (None if None in results else False)
    if key == "not":
        r = guard_ok(arg, st)
        return None if r is None else not r
    if key == "state_in":
        return st["state"] in arg
    if key == "occupants_eq":
        return occupancy(st) == arg
    if key == "occupants_gt":
        return occupancy(st) > arg
    if key == "motion_idle":
        return (st["motion"] is None) == bool(arg)
    if key == "unresolved":
        return None
    raise FeatureStateError(f"unknown guard {key!r} (vocabulary {GUARD_VOCABULARY})")


def _match(feature: dict, st: dict, event: dict):
    """The first row of the table for this event from this state whose guard holds: (row, None), or
    (None, reason)."""
    unresolved = False
    for row in feature.get("transitions") or []:
        if row.get("event") != event["kind"]:
            continue
        if event["kind"] == "scheduled" and row.get("name") != event.get("name"):
            continue
        source = row.get("from")
        if source != "*" and st["state"] not in (source or []):
            continue
        ok = guard_ok(row.get("guard"), st)
        if ok is None:
            unresolved = True
            continue
        if ok:
            return row, None
    return None, "unresolved_guard" if unresolved else "no_transition"


def _schedule(st: dict, t: float, kind: str, name: str | None = None) -> None:
    st["pending"].append({"t": t, "kind": kind, "name": name, "generation": st["generation"], "epoch": st["epoch"]})


def _enter(feature: dict, st: dict, row: dict, t: float) -> list[str]:
    """Runs an accepted transition from time t; returns notes (unresolved timings)."""
    notes = []
    states = _states(feature)
    st["generation"] += 1
    st["pending"] = []
    motion = row.get("motion")
    if motion:
        st["motion"] = {"row": row.get("id"), "from": st["state"], "to": row.get("to"), "start": t,
                        "duration": known(motion.get("duration"))}
        st["state"] = motion.get("state")
        if st["motion"]["duration"] is None:
            notes.append("unresolved_duration")
        else:
            _schedule(st, t + st["motion"]["duration"], "motion_complete")
    else:
        st["state"] = row.get("to")
        st["motion"] = None
        notes += _follow_up(st, row, t)
    if states.get(st["state"], {}).get("terminal"):
        st["pending"], st["queue"], st["motion"] = [], [], None
    return notes


def _follow_up(st: dict, row: dict, t: float) -> list[str]:
    follow = row.get("follow_up")
    if not follow:
        return []
    after = known(follow.get("after"))
    if after is None:
        return ["unresolved_follow_up"]
    _schedule(st, t + after, "scheduled", follow.get("name"))
    return []


def _row(feature: dict, row_id):
    for row in feature.get("transitions") or []:
        if row.get("id") == row_id:
            return row
    return None


def apply(feature: dict, st: dict, event: dict) -> tuple[dict, dict]:
    """One event: (new state, trace entry). Never changes its inputs."""
    st = copy.deepcopy(st)
    kind, t = event["kind"], float(event["t"])
    if kind not in EVENTS:
        raise FeatureStateError(f"unknown event {kind!r}")
    entry = {"t": t, "kind": kind}
    if event.get("name") is not None:
        entry["name"] = event["name"]
    states = _states(feature)

    def done(result, reason=None, row=None, notes=None):
        entry["result"] = result
        if reason:
            entry["reason"] = reason
        if row is not None:
            entry["transition"] = row.get("id")
        if notes:
            entry["notes"] = notes
        entry.update(snapshot(st))
        return st, entry

    if kind == "reset":
        fresh = _fresh(feature, st["epoch"] + 1, st["generation"] + 1)
        st.clear()
        st.update(fresh)
        return done("applied")
    if kind in ("motion_complete", "scheduled"):
        if "generation" not in event or "epoch" not in event:
            return done("rejected", "no_token")
        if event["epoch"] != st["epoch"] or event["generation"] != st["generation"]:
            return done("stale")
        st["pending"] = [p for p in st["pending"] if not _same(p, event)]
        if kind == "motion_complete":
            motion = st["motion"]
            if motion is None:
                return done("stale")
            st["state"], st["motion"] = motion["to"], None
            notes = _follow_up(st, _row(feature, motion["row"]) or {}, t)
            queued, st["queue"] = st["queue"], []
            entry["result"] = "applied"
            for q in queued:          # deferred presses run now, in order, at the completion time
                st, sub = apply(feature, st, {**q, "t": t})
                entry.setdefault("released", []).append(sub["result"])
            return done("applied", notes=notes)
    if states.get(st["state"], {}).get("terminal"):
        return done("rejected", "terminal")
    if kind == "observed":
        if event.get("state") not in states:
            return done("rejected", "unknown_state")
        st["generation"] += 1
        st["state"], st["motion"], st["pending"] = event["state"], None, []
        return done("applied")
    if kind == "proximity_enter":
        who = event.get("occupant")
        if who is None:
            st["anonymous"] += 1
        elif who in st["occupants"]:
            return done("ignored", "duplicate_occupant")
        else:
            st["occupants"] = sorted(st["occupants"] + [who])
    elif kind == "proximity_leave":
        who = event.get("occupant")
        if who is None:
            if not st["anonymous"]:
                return done("rejected", "unknown_occupant")
            st["anonymous"] -= 1
        elif who not in st["occupants"]:
            return done("rejected", "unknown_occupant")
        else:
            st["occupants"] = [o for o in st["occupants"] if o != who]
    row, reason = _match(feature, st, event)
    if row is not None:
        return done("applied", row=row, notes=_enter(feature, st, row, t))
    if kind in ("proximity_enter", "proximity_leave"):
        return done("applied", "occupancy_only")
    if st["motion"] is not None:
        # no row of the table answers this event in mid-motion: the moving transition's own policy does
        current = _row(feature, st["motion"]["row"]) or {}
        policy = current.get("mid_motion", "unresolved")
        if policy == "ignore":
            return done("ignored", "in_motion")
        if policy == "queue":
            st["queue"].append({k: v for k, v in event.items() if k != "t"})
            return done("queued")
        if policy == "restart":
            motion = st["motion"]
            st["generation"] += 1
            st["pending"] = []
            motion["start"] = t
            if motion["duration"] is not None:
                _schedule(st, t + motion["duration"], "motion_complete")
            return done("applied", "restart", row=current)
        if policy == "reverse":
            motion = st["motion"]
            if motion["duration"] is None:
                return done("rejected", "unresolved_duration")
            elapsed = min(max(t - motion["start"], 0.0), motion["duration"])
            st["generation"] += 1
            st["pending"] = []
            motion["from"], motion["to"] = motion["to"], motion["from"]
            motion["start"], motion["duration"] = t - (motion["duration"] - elapsed), motion["duration"]
            _schedule(st, t + elapsed, "motion_complete")
            return done("applied", "reverse", row=current)
        return done("rejected", "unresolved_policy")
    return done("rejected", reason)


def _same(p: dict, event: dict) -> bool:
    return p["kind"] == event["kind"] and p.get("name") == event.get("name") and p["t"] == float(event["t"])


def snapshot(st: dict) -> dict:
    """What a trace entry records of the state after an event."""
    return {"state": st["state"], "epoch": st["epoch"], "generation": st["generation"],
            "occupancy": occupancy(st), "moving": st["motion"] is not None,
            "pending": [[p["t"], p["kind"], p["name"]] for p in sorted(st["pending"], key=_pending_key)]}


def _pending_key(p: dict):
    return (p["t"], PRIORITY[p["kind"]], p["name"] or "")


def run(feature: dict, events: list[dict], until: float | None = None) -> list[dict]:
    """Every event in order (input events and the reducer's own scheduled ones, merged by time, then
    PRIORITY, then input order; scheduled ones after input ones at a tie of both), up to `until` (inclusive;
    default: through the last pending event). Returns the trace."""
    return evaluate(feature, events, until)["trace"]


def evaluate(feature: dict, events: list[dict], until: float | None = None) -> dict:
    """Return the trace and full reducer state, including motion's start/end, at a query time.

    No extra clock events are inserted; callers use the retained motion to sample geometry between events.
    `run` retains its original trace contract.
    """
    st = initial(feature)
    for event in events:
        if event.get("kind") not in EVENTS:
            raise FeatureStateError(f"unknown event {event.get('kind')!r}")
    inputs = sorted(enumerate(events), key=lambda ie: (float(ie[1]["t"]), PRIORITY[ie[1]["kind"]], ie[0]))
    trace = []
    i = 0
    while True:
        if len(trace) >= MAX_STEPS:
            raise FeatureStateError(f"more than {MAX_STEPS} events: a follow-up loop?")
        nxt_in = inputs[i][1] if i < len(inputs) else None
        nxt_p = min(st["pending"], key=_pending_key) if st["pending"] else None
        if nxt_in is None and nxt_p is None:
            break
        use_input = nxt_p is None or (nxt_in is not None and
                                      (float(nxt_in["t"]), PRIORITY[nxt_in["kind"]]) <= (nxt_p["t"], PRIORITY[nxt_p["kind"]]))
        event = nxt_in if use_input else nxt_p
        if until is not None and float(event["t"]) > until:
            break
        if use_input:
            i += 1
        st, entry = apply(feature, st, event)
        if not use_input:
            entry["scheduled"] = True
        trace.append(entry)
    return {"state": st, "trace": trace}
