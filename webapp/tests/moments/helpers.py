"""Builders for Critical Moments tests. Geometry and tracks come from tests/replays/control_toys.py; this adds
the util and kill rows a fight is made of."""

from app.control import engine as ce
from tests.replays.control_toys import blob, door_hall, open_hall, toy_ability, track, uv  # noqa: F401


def damage(t, by, target, *, t1=None, src="gun", wall=False, n=1) -> dict:
    return {"k": "damage", "t": t, "t1": t if t1 is None else t1, "by": by, "target": target, "src": src,
            "wall": wall, "n": n}


def shot(t, by, gun="Vandal", u=0, v=0, u1=None, v1=None) -> dict:
    return {"k": "shot", "t": t, "by": by, "u": u, "v": v, "u1": u if u1 is None else u1,
            "v1": v if v1 is None else v1, "gun": gun}


def kill(t, killer, victim, i=0) -> dict:
    return {"i": i, "t": t, "killer": killer, "victim": victim, "u": 0, "v": 0}


def cast(t, by, code="Dash", name="Dash") -> dict:
    return {"k": "cast", "t": t, "by": by, "code": code, "name": name}


def status(t, t1, by, target, name="Concuss", status="concussed") -> dict:
    """app/replays/extras.py:21: by is a slot or None."""
    return {"k": "status", "t": t, "by": by, "t1": t1, "target": target, "code": "Breach", "name": name,
            "status": status, "from": "ability"}


def reveal(t, t1, by, target, name="Dart") -> dict:
    return {"k": "reveal", "t": t, "by": by, "t1": t1, "target": target, "code": "Sova", "name": name}


def flash(t, by, hits, x=0, y=0, k="flash", name="Flash") -> dict:
    """app/replays/format.py:16: hits = [[target slot, hit t, dur s or None], ...]."""
    u, v = uv(x, y)
    return {"k": k, "t": t, "by": by, "u": u, "v": v, "name": name, "hits": [list(h) for h in hits]}


def round_of(players, *, kills=(), util=(), t_end=30.0, t_decided=None, db_deaths=(), geo=None):
    """`players` = {slot: (side, [(t, x px, y px, yaw), ...])}. Kills end their victim's life.
    Returns (RoundInputs, blob)."""
    geo = geo or open_hall()
    deaths = {k["victim"]: k["t"] for k in kills}
    data = blob(players, t_end=t_end, t_decided=t_decided, util=list(util), deaths=deaths)
    data["kills"] = list(kills)
    link = ce.ControlLink(db_deaths=tuple(db_deaths))
    return ce.RoundInputs(data, geo, link), data
