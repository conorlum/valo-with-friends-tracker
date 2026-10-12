"""Hand-built rounds for the coach tests. Slots 0-4 are side A, 5-9 side B. UV_PER_UNIT 0.7 is Ascent's, so
70 uv = 1 m. Every player stands still unless a test passes its own track."""

from app.replays import format as fmt

HZ = 4
UV_PER_UNIT = 0.7
NAMES = {i: f"P{i}" for i in range(10)}
CALLOUTS = {"Toy": [{"name": "A Site", "super": "A", "u": 2000, "v": 2000},
                    {"name": "B Site", "super": "B", "u": 8000, "v": 8000},
                    {"name": "Mid Courtyard", "super": "Mid", "u": 5000, "v": 5000}]}


def stand(u, v, t0=0.0, t1=60.0, yaw=0):
    n = int((t1 - t0) * HZ) + 1
    return [fmt.encode_segment(t0, [u] * n, [v] * n, [yaw] * n)]


def walk(u0, v0, u1, v1, t0, t1):
    n = int((t1 - t0) * HZ) + 1
    us = [round(u0 + (u1 - u0) * i / (n - 1)) for i in range(n)]
    vs = [round(v0 + (v1 - v0) * i / (n - 1)) for i in range(n)]
    return [fmt.encode_segment(t0, us, vs, [0] * n)]


def round_blob(positions, kills=(), util=(), t_end=60.0, t_decided=None, n=1, tracks=None, player_state=True):
    """`positions`: {slot: (u, v)}. A killed player's track and alive interval end at their death; a survivor's
    interval ends in None, as the condenser stores it."""
    died = {k["victim"]: k["t"] for k in kills}
    blob = {"v": 1, "round": n, "map": "Toy", "hz": HZ, "t_start": 0.0, "t_end": t_end,
            "t_decided": t_end if t_decided is None else t_decided,
            "players": [{"slot": s, "agent": "Jett", "side": "A" if s < 5 else "B"} for s in sorted(positions)],
            "tracks": {}, "alive": {}, "plant": None, "defuse": None, "util": list(util),
            "kills": [{"i": i, "u": positions[k["victim"]][0], "v": positions[k["victim"]][1], **k}
                      for i, k in enumerate(sorted(kills, key=lambda k: k["t"]))]}
    for s, (u, v) in positions.items():
        end = died.get(s, t_end)
        blob["tracks"][str(s)] = (tracks or {}).get(s) or stand(u, v, 0.0, end)
        blob["alive"][str(s)] = [[0.0, died[s], "kill"]] if s in died else [[0.0, None, "round_end"]]
    if player_state:
        blob["player_state"] = {"version": 1}      # presence is all the coach checks
    return blob


def ten(a=(2000, 2000), b=(8000, 8000)):
    """Five on A Site, five on B Site."""
    return {**{s: a for s in range(5)}, **{s: b for s in range(5, 10)}}
