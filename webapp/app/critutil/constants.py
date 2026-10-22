"""Thresholds and tables for Critical Util (design: docs/superpowers/specs/2026-10-21-critical-util-design.md).
Standard library only: nothing here may import the app. Values marked "measure" are starting points the spec's
"Measurements to make before code" replace once measured on the corpus (scripts/critical_util.py measure)."""

from __future__ import annotations

VERSION = 1                 # the extractor's output (app/critutil/extract.py); part of the stored row

# --- one cast (measured on real exports, 2026-10-21: a recon is a projectile, a bolt object and two pings over
# about 3 s; a smoke's zone lands about 2 s after its projectile; a flash's projectile starts with its flash row)
MERGE_S = 0.6               # a piece starting this soon after a cast's last piece ended still belongs to it
THROW_MATCH_S = 0.05        # a piece whose `thrown.t0` is this close to a cast's throw is that cast's
REPEAT_S = 1.0              # the same landed piece again this much later is another use (a second smoke) ...
REPEAT_PARTS = ("SonarPing", "RevealDart", "Pulse", "Segment", "Chunk", "Manager", "Wall", "Second")  # ... but not these
FLASH_ROW_MATCH_S = 1.0     # a projectile or source thrown this close to a flash or nearsight row is that cast

# --- windows (measure)
GRACE_S = 1.0               # a kill this long after an effect ends still counts as on an affected enemy
ZONE_DAMAGE_LOOKBACK_S = 3.0  # a victim damaged by a zone this recently before dying
TRIGGER_WINDOW_S = 4.0      # a device's trigger: kills this long after it
NEAR_TRIGGER_M = 15.0       # ... on enemies within this of the device
FOLLOW_S = 1.5              # a teammate's damage or aimed shot this soon after an effect starts follows it up
LIFE_CAP_S = 40.0           # an ability row with no close counts at most this long

# --- sight blocking (smokes and walls)
SIGHT_STEP_S = 0.5
SIGHT_RANGE_M = 45.0        # pairs farther apart than this aren't counted as a sight line the smoke cut
SAVE_WINDOW_S = 3.0         # a covered teammate alive this long after the cover ends was "covered and lived"

# --- space (observed enemy presence; the unknown-based measure is a follow-up, see the spec)
ZONE_RADIUS_M = {"smoke": 4.5, "damaging": 4.0, "device": 12.0, "status": 6.0, "default": 6.0}
WAIT_RING_M = 8.0           # an enemy stopped within this outside the zone is waiting on it
TURN_AWAY_M = 6.0           # an enemy at the edge who then got this much farther away, never entering, held back
PRESENCE_KINDS = frozenset({"smoke", "damaging", "wall", "device"})   # area util: the casts presence is measured for
STOPPED_MPS = 1.0
PRESENCE_STEP_S = 0.5
SPIKE_NEAR_M = 15.0

# --- heals
HEAL_FOLLOW_S = 10.0        # a fight or damage this soon after a heal is what the heal was for

# --- unused util
ENGAGE_LOOKBACK_S = 5.0     # the death's incoming damage started within this before it

# --- a cast's kinds, from the names its rows carry (app/moments/constants.py ABILITY_KIND covers most)
DEVICE_PATTERNS = ("TripWire", "PossessableCamera", "E_Turret", "StealthAlarmbot", "SoundSensor", "E_Trap",
                   "SeizeTrap", "StealthingTrap", "BarbedWire", "Q_CageTrap", "Interceptor", "Decoy", "Clone",
                   "X_InterrogateHat", "E_Drone", "Q_PossessableScout", "WolfHound", "4_Spider")
WALL_PATTERNS = ("Q_WallManager", "E_SmokeScreenManager", "X_GlobalWall", "4_Tunnel", "Q_FlameWallManager",
                 "Shear", "Wall", "Mesh")
MOBILITY_KEYS = frozenset({"Clay_Q_Explosion"})   # Raze's Blast Pack: mostly a jump, never called wasted
STALL_KINDS = frozenset({"smoke", "damaging", "status", "device"})

# Ability by keybind per agent, for unused util (the charge timelines name the inventory slot, not the ability).
# From game knowledge; the owner checks it. An agent or key not here makes no "unused" claim.
AGENT_KEYS = {
    "Astra": {"C": "status", "Q": "status", "E": "smoke"},
    "Breach": {"C": "damaging", "Q": "blind", "E": "status"},
    "Brimstone": {"C": "other", "Q": "damaging", "E": "smoke"},
    "Chamber": {"C": "device", "Q": "other", "E": "mobility"},
    "Clove": {"C": "heal", "Q": "status", "E": "smoke"},
    "Cypher": {"C": "device", "Q": "smoke", "E": "info"},
    "Deadlock": {"C": "status", "Q": "device", "E": "other"},
    "Fade": {"C": "status", "Q": "status", "E": "info"},
    "Gekko": {"C": "damaging", "Q": "status", "E": "blind"},
    "Harbor": {"C": "status", "Q": "smoke", "E": "smoke"},
    "Iso": {"C": "other", "Q": "status", "E": "other"},
    "Jett": {"C": "smoke", "Q": "mobility", "E": "mobility"},
    "KAY/O": {"C": "damaging", "Q": "blind", "E": "status"},
    "Killjoy": {"C": "damaging", "Q": "device", "E": "device"},
    "Neon": {"C": "smoke", "Q": "status", "E": "mobility"},
    "Omen": {"C": "mobility", "Q": "status", "E": "smoke"},
    "Phoenix": {"C": "smoke", "Q": "blind", "E": "damaging"},
    "Raze": {"C": "damaging", "Q": "mobility", "E": "damaging"},
    "Reyna": {"C": "status", "Q": "heal", "E": "mobility"},
    "Sage": {"C": "other", "Q": "status", "E": "heal"},
    "Skye": {"C": "heal", "Q": "status", "E": "blind"},
    "Sova": {"C": "info", "Q": "damaging", "E": "info"},
    "Tejo": {"C": "info", "Q": "status", "E": "damaging"},
    "Viper": {"C": "damaging", "Q": "smoke", "E": "smoke"},
    "Vyse": {"C": "damaging", "Q": "other", "E": "blind"},
    "Waylay": {"C": "status", "Q": "mobility", "E": "mobility"},
    "Yoru": {"C": "other", "Q": "blind", "E": "mobility"},
}
# What util "fits" a fight the player engaged in, so dying with it held counts (owner decision: unused util).
FITS_ANY_FIGHT = frozenset({"blind", "smoke", "status"})
FITS_SPIKE = frozenset({"damaging", "smoke", "status", "device"})   # a planted spike or a defuse attempt

# Evidence weights for splitting one ledger item between its casts and the gunplay (app/services/util_values.py).
# Gunplay always takes a share, so util never takes a whole kill.
EVIDENCE_WEIGHT = {"affected": 1.0, "revealed": 0.7, "zone_damage": 0.8, "trigger": 0.6, "cover": 0.5,
                   "harmed_teammate": 1.0, "failed_follow": 0.5}
GUN_WEIGHT = 1.0
