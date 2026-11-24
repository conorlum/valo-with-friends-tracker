"""Shared classes for stored control reasons; safe to import without the engine."""

CLASSES = {
    "seen": "found",
    "kill": "found",
    "plant": "found",
    "damage": "found",
    "gunfire": "found",
    "footsteps": "found",
    "tap": "found",
    "orb": "found",
    "revived": "found",
    "neural_theft": "found",
    "trip": "found",
    "reveal": "found",
    "drone_dart": "found",
    "camera_dart": "found",
    "tejo_drone": "found",
    "waylay_recall": "found",
    "trip_destroyed": "found",
    "camera_destroyed": "found",
    "knife_hit": "roughly_placed",
    "knife_all": "roughly_placed",
    "leer_seen": "roughly_placed",
    "knife_zero": "ruled_out",
    "knife_missed": "ruled_out",
    "skye_no_cue": "ruled_out",
    "hypothesis_cleared": "ruled_out",
    "omen_seen": "ruled_out",
    "omen_unheard": "lost_track",
    "yoru_drift_unheard": "lost_track",
    "yoru_beacon": "lost_track",
    "died": "other",
    "omen_channel": "other",
    "hush_suspension": "found",  # heard hit feedback, like a trip or other heard information cue
    "nightfall_count": "roughly_placed",
    "dizzy_count": "roughly_placed",
    "thrash_count": "roughly_placed",
    "storm_surge_count": "roughly_placed",
    "nightfall_zero": "ruled_out",
    "dizzy_zero": "ruled_out",
    "thrash_zero": "ruled_out",
    "storm_surge_zero": "ruled_out",
    "prowler_hit": "found",
    "wingman_hit": "found",
    "boom_bot_hit": "found",
    "seekers_hit": "found",
    "hunters_fury_hit": "found",
    "seize_hit": "found",
    "fakeout_direction": "roughly_placed",
    "prowler_miss": "ruled_out",
    "wingman_miss": "ruled_out",
    "boom_bot_miss": "ruled_out",
    "seekers_miss": "ruled_out",
    "hunters_fury_miss": "ruled_out",
}

TWO_KIND = {"haunt", "recon"}
SIGHT_SOUND = {"seen", "footsteps", "gunfire", "damage", "kill", "tap", "orb"}


def classify(reason: str, kind: str | None = None) -> str:
    """Classify a reason, treating old Haunt/Recon rows without a kind as exclusions.

    Only an explicit locate operation establishes that Haunt or Recon found someone.
    Unknown reasons retain the neutral class for compatibility with future rows.
    """
    if reason in TWO_KIND:
        return "found" if kind == "locate" else "ruled_out"
    return CLASSES.get(reason, "other")
