"""Thresholds and tables for Critical Util (design: docs/superpowers/specs/2026-10-21-critical-util-design.md).
Standard library only: nothing here may import the app. Values marked "measure" are starting points the spec's
"Measurements to make before code" replace once measured on the corpus (scripts/critical_util.py measure)."""

from __future__ import annotations

VERSION = 2                 # the extractor's output (app/critutil/extract.py); part of the stored row

# --- one cast (measured on real exports, 2026-10-21: a recon is a projectile, a bolt object and two pings over
# about 3 s; a smoke's zone lands about 2 s after its projectile; a flash's projectile starts with its flash row)
MERGE_S = 0.6               # a piece starting this soon after a cast's last piece ended still belongs to it
THROW_MATCH_S = 0.05        # a piece whose `thrown.t0` is this close to a cast's throw is that cast's
REPEAT_S = 1.0              # the same landed piece again this much later is another use (a second smoke) ...
REPEAT_PARTS = ("SonarPing", "RevealDart", "Pulse", "Segment", "Chunk", "Manager", "Wall", "Second")  # ... but not these
BUY_PLACED_S = 0.5          # a placement starting by this round time was placed in the buy phase (clamped to 0 s) ...
SAME_PLACE_M = 1.5          # ... and the same piece placed farther than this from its namesake is another charge
#                             (2026-10-22, 10 replays: 105 Cypher trip pairs at 0 s; mid-round same-name pieces at
#                             two places are one use: Skye's seekers, Neon's bolt, Reyna's pools, a Killjoy swarm)
FLASH_TAIL_S = 1.0         # a flash cast's rows end by its pop plus this
FLASH_ROW_MATCH_S = 1.0    # a projectile or source thrown this close to a flash or nearsight row is that cast

# --- windows (measure)
GRACE_S = 1.0               # a kill this long after an effect ends still counts as on an affected enemy
ZONE_DAMAGE_LOOKBACK_S = 3.0  # a victim damaged by a zone this recently before dying
TRIGGER_WINDOW_S = 4.0      # a device's trigger: kills this long after it
NEAR_TRIGGER_M = 15.0       # ... on enemies within this of the device
FOLLOW_S = 1.5              # a teammate's damage or aimed shot this soon after an effect starts follows it up

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

HOLD_MATCH_S = 2.0          # an unknown-probe item belongs to its owner's cast of its kind that landed this close

# --- heals
HEAL_FOLLOW_S = 10.0        # a fight or damage this soon after a heal is what the heal was for

# --- unused util
ENGAGE_LOOKBACK_S = 5.0     # the death's incoming damage started within this before it

# --- a cast's kinds, from the names its rows carry (app/moments/constants.py ABILITY_KIND covers most)
DEVICE_PATTERNS = ("TripWire", "PossessableCamera", "E_Turret", "StealthAlarmbot", "SoundSensor", "E_Trap",
                   "SeizeTrap", "StealthingTrap", "BarbedWire", "Interceptor", "Decoy", "Clone",
                   "X_InterrogateHat", "E_Drone", "Q_PossessableScout", "WolfHound", "4_Spider")
WALL_PATTERNS = ("Q_WallManager", "E_SmokeScreenManager", "X_GlobalWall", "4_Tunnel", "Q_FlameWallManager",
                 "Shear", "Wall", "Mesh")
MOBILITY_KEYS = frozenset({"Clay_Q_Explosion"})   # Raze's Blast Pack: mostly a jump, never called wasted
STALL_KINDS = frozenset({"smoke", "damaging", "status", "device"})

# Each agent's abilities, by the inventory slot the charge timelines are keyed on (player_state `charges`: C, Q, E):
# the ability's name (what the pages show; never the key) and its kind. From game knowledge; the owner checks it.
# An agent or slot not here makes no "unused" claim.
AGENT_KEYS = {
    "Astra": {"C": ("Gravity Well", "status"), "Q": ("Nova Pulse", "status"), "E": ("Nebula", "smoke")},
    "Breach": {"C": ("Aftershock", "damaging"), "Q": ("Flashpoint", "blind"), "E": ("Fault Line", "status")},
    "Brimstone": {"C": ("Stim Beacon", "other"), "Q": ("Incendiary", "damaging"), "E": ("Sky Smoke", "smoke")},
    "Chamber": {"C": ("Trademark", "device"), "Q": ("Headhunter", "other"), "E": ("Rendezvous", "mobility")},
    "Clove": {"C": ("Pick-Me-Up", "heal"), "Q": ("Meddle", "status"), "E": ("Ruse", "smoke")},
    "Cypher": {"C": ("Trapwire", "device"), "Q": ("Cyber Cage", "smoke"), "E": ("Spycam", "info")},
    "Deadlock": {"C": ("GravNet", "status"), "Q": ("Sonic Sensor", "device"), "E": ("Barrier Mesh", "other")},
    "Fade": {"C": ("Prowler", "status"), "Q": ("Seize", "status"), "E": ("Haunt", "info")},
    "Gekko": {"C": ("Mosh Pit", "damaging"), "Q": ("Wingman", "status"), "E": ("Dizzy", "blind")},
    "Harbor": {"C": ("Storm Surge", "status"), "Q": ("High Tide", "smoke"), "E": ("Cove", "smoke")},
    "Iso": {"C": ("Contingency", "other"), "Q": ("Undercut", "status"), "E": ("Double Tap", "other")},
    "Jett": {"C": ("Cloudburst", "smoke"), "Q": ("Updraft", "mobility"), "E": ("Tailwind", "mobility")},
    "KAY/O": {"C": ("FRAG/ment", "damaging"), "Q": ("FLASH/drive", "blind"), "E": ("ZERO/point", "status")},
    "Killjoy": {"C": ("Nanoswarm", "damaging"), "Q": ("Alarmbot", "device"), "E": ("Turret", "device")},
    "Neon": {"C": ("Fast Lane", "smoke"), "Q": ("Relay Bolt", "status"), "E": ("High Gear", "mobility")},
    "Omen": {"C": ("Shrouded Step", "mobility"), "Q": ("Paranoia", "status"), "E": ("Dark Cover", "smoke")},
    "Phoenix": {"C": ("Blaze", "smoke"), "Q": ("Curveball", "blind"), "E": ("Hot Hands", "damaging")},
    "Raze": {"C": ("Boom Bot", "damaging"), "Q": ("Blast Pack", "mobility"), "E": ("Paint Shells", "damaging")},
    "Reyna": {"C": ("Leer", "status"), "Q": ("Devour", "heal"), "E": ("Dismiss", "mobility")},
    "Sage": {"C": ("Barrier Orb", "other"), "Q": ("Slow Orb", "status"), "E": ("Healing Orb", "heal")},
    "Skye": {"C": ("Regrowth", "heal"), "Q": ("Trailblazer", "status"), "E": ("Guiding Light", "blind")},
    "Sova": {"C": ("Owl Drone", "info"), "Q": ("Shock Bolt", "damaging"), "E": ("Recon Bolt", "info")},
    "Tejo": {"C": ("Stealth Drone", "info"), "Q": ("Special Delivery", "status"), "E": ("Guided Salvo", "damaging")},
    "Viper": {"C": ("Snake Bite", "damaging"), "Q": ("Poison Cloud", "smoke"), "E": ("Toxic Screen", "smoke")},
    "Vyse": {"C": ("Razorvine", "damaging"), "Q": ("Shear", "other"), "E": ("Arc Rose", "blind")},
    "Waylay": {"C": ("Saturate", "status"), "Q": ("Lightspeed", "mobility"), "E": ("Refract", "mobility")},
    "Yoru": {"C": ("Fakeout", "other"), "Q": ("Blindside", "blind"), "E": ("Gatecrash", "mobility")},
}
# What util "fits" a fight the player engaged in, so dying with it held counts (owner decision: unused util).
FITS_ANY_FIGHT = frozenset({"blind", "smoke", "status"})
FITS_SPIKE = frozenset({"damaging", "smoke", "status", "device"})   # a planted spike or a defuse attempt

# Evidence weights for splitting one ledger item between its casts and the gunplay (app/services/util_values.py).
# Gunplay always takes a share, so util never takes a whole kill.
EVIDENCE_WEIGHT = {"affected": 1.0, "revealed": 0.7, "zone_damage": 0.8, "trigger": 0.6, "cover": 0.5,
                   "harmed_teammate": 1.0, "failed_follow": 0.5}
GUN_WEIGHT = 1.0
