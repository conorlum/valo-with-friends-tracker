"""Per-cast information measures shared by the site and replay worker. Standard library only.

Missing info is unknown (an old row or a failed probe); a measured zero still counts as a use.
Darts contribute to their camera or drone, never to a separate information use.
"""

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
