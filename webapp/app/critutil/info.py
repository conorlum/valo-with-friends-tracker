"""Per-cast information measures shared by the site and replay worker. Standard library only.

Missing info is unknown (an old row or a failed probe); a measured zero still counts as a use.
Darts contribute to their camera or drone, never to a separate information use.
"""

from collections import defaultdict
from math import fsum

FRIEND_MIN_SAMPLE = (10, 3)  # D5: casts, matches; shared with the page's defensive filter.

CLEAR_ABILITIES = frozenset({
    "Sova/Recon Bolt", "Sova/Owl Drone", "Sova/Hunter's Fury",
    "Fade/Haunt", "Fade/Prowler", "Fade/Seize", "Fade/Nightfall",
    "Skye/Trailblazer", "Skye/Seekers", "Skye/Guiding Light",
    "Gekko/Wingman", "Gekko/Dizzy", "Gekko/Thrash", "Tejo/Stealth Drone",
    "Cypher/Spycam", "Cypher/Neural Theft", "Killjoy/Turret", "Raze/Boom Bot",
    "Harbor/Storm Surge", "Yoru/Fakeout", "KAY/O/ZERO/point",
})

HOLD_ABILITIES = frozenset({
    "Cypher/Trapwire", "Cypher/Spycam", "Killjoy/Alarmbot", "Killjoy/Turret", "Chamber/Trademark",
    "Sova/Owl Drone", "Skye/Trailblazer", "Gekko/Thrash", "Tejo/Stealth Drone",
    # The objects on app.control.mollies.MOLLY_KEYS, explicitly named so this module stays portable.
    "Breach/Aftershock", "Brimstone/Incendiary", "Brimstone/Orbital Strike", "Gekko/Mosh Pit",
    "KAY/O/FRAG/ment", "Killjoy/Nanoswarm", "Phoenix/Hot Hands", "Tejo/Guided Salvo",
    "Tejo/Armageddon", "Viper/Snake Bite",
})


def empty() -> dict:
    """A fresh measured zero for one eligible cast."""
    return {"cleared_m2": 0.0, "located": 0, "hold_s": 0.0}


def figure(cast: dict) -> dict | None:
    """Read the stored measures, preserving unknown on rows from before this contract."""
    return cast.get("info")


def _mean(groups) -> float:
    return fsum(fsum(values) for values in groups) / sum(len(values) for values in groups)


def friend_row_eligible(n: int, matches: int) -> bool:
    return n >= FRIEND_MIN_SAMPLE[0] and matches >= FRIEND_MIN_SAMPLE[1]


def summarise(casts, *, ci) -> dict:
    """Compact ability tables from scored cast rows, with unknown measurements left out.

    `ci` is stats_math.cluster_bootstrap_ci, supplied by the builder so this module stays standard library only.
    Its default seed is fixed at zero. Sorted matches and values make resampling independent of input order.
    Player keys are strings for storage; only the overall tables carry percentile points (linear interpolation).
    Below-D5 player rows never get a CI or a stored row: only their count per player is retained in below_bar.
    """
    overall = defaultdict(lambda: defaultdict(list))
    players = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for cast in casts:
        measured = figure(cast)
        if measured is None:
            continue
        ability = cast.get("ability_id") or f"{cast.get('agent')}/{cast.get('ability')}"
        for section, metric, abilities in (("clears", "cleared_m2", CLEAR_ABILITIES),
                                           ("holds", "hold_s", HOLD_ABILITIES)):
            if ability not in abilities:
                continue
            key = (section, metric, ability)
            overall[key][cast["match"]].append(float(measured[metric]))
            if cast.get("player_id") is not None:
                players[str(cast["player_id"])][key][cast["match"]].append(float(measured[metric]))

    def tables(grouped, percentiles=None):
        out = {"clears": [], "holds": []}
        for (section, metric, ability), matches in sorted(grouped.items()):
            groups = {m: sorted(matches[m]) for m in sorted(matches, key=str)}
            values = sorted(v for group in groups.values() for v in group)
            agent, name = ("KAY/O", ability[len("KAY/O/"):]) if ability.startswith("KAY/O/") else ability.split("/", 1)
            out[section].append({"ability_id": ability, "agent": agent, "ability": name, "metric": metric,
                                 "mean": fsum(values) / len(values), "min": values[0], "max": values[-1],
                                 "n": len(values), "matches": len(groups), "ci": list(ci(_mean, groups))})
            if percentiles is not None and len(values) >= 10:
                points = []
                for p in range(101):
                    at = (len(values) - 1) * p / 100
                    lo = int(at)
                    hi = min(lo + 1, len(values) - 1)
                    points.append(values[lo] + (values[hi] - values[lo]) * (at - lo))
                percentiles.setdefault(ability, {})[metric] = points
        return out

    percentiles = {}
    overall_tables = tables(overall, percentiles)
    player_tables, below_bar = {}, {}
    for pid in sorted(players):
        eligible, omitted = {}, 0
        for key, matches in players[pid].items():
            if friend_row_eligible(sum(map(len, matches.values())), len(matches)):
                eligible[key] = matches
            else:
                omitted += 1
        if eligible:
            player_tables[pid] = tables(eligible)
        if omitted:
            below_bar[pid] = omitted
    return {"overall": overall_tables, "players": player_tables, "percentiles": percentiles, "below_bar": below_bar}
