"""Thresholds and tables for the per-fight Critical Moments tags (design:
docs/superpowers/specs/2026-10-17-critical-moments-design.md). Values approved by the owner
(D4-D6). Standard library only: nothing here may import the app."""

from __future__ import annotations

TRADE_WINDOW_S = 6.0        # equals app.scoring.impact.TRADE_WINDOW_SECONDS, pinned by a test
WITH_AIM_DEG = 10  # a teammate's shot aimed within this of an opponent counts as fighting; -1 = damage only
TRADE_RANGE_M = 20.0       # a teammate farther than this could not have traded

FIGHT_GAP_S = 3.0           # a gap longer than this splits fights (corpus: 20,622 fights at 3 s)

MOVING_MPS = 2.5            # a shot above this speed is a moving shot (corpus rifle shots: 90th percentile)
SHOT_LEAD_S = 1.0           # shots from this long before t0 count
DASH_EXCLUDE_S = 0.75       # shots this close to a movement cast get no speed
SPEED_MAX_SPAN_S = 0.2      # max span of the two track samples a speed is taken from

UTIL_LOOKBACK_S = 3.0       # utility this recent before the fight counts
UTIL_NEAR_M = 8.0           # utility placed within this of a player counts as near

SIGHT_LOOK_S = 3.0          # how far back a sight check looks
SIGHT_STEP_S = 1 / 16       # one step of the 16 Hz track

SPOT_LOOKBACK_S = 10.0      # how far back a spot is searched for
SPOT_GAP_S = 0.25           # gap that ends a spotting run
RUN_GAP_S = 0.6             # gap that ends a run of shots
SHOT_AFTER_KILL_S = 0.05    # a shot this soon after the kill still belongs to it
SPOT_TOLERANCE_S = 0.05     # slack when matching a spot to a time
CLEAN_SPOT_TO_SHOT_S = 1.0  # a clean kill fires within this of the spot
CLEAN_MAX_MOVING = 1        # a clean kill has at most this many moving shots
GUN_DAMAGE_LOOKBACK_S = 10.0  # how far back gun damage is looked for

CROSSHAIR_ON_DEG = 5        # crosshair within this angle of the enemy is on target
CROSSHAIR_NEAR_DEG = 20     # crosshair within this angle is near
MEDIAN_MIN_CLEAN = 5        # fewest clean kills before a median is shown
INFO_MERGE_S = 2.0          # info events closer than this merge
DEATH_MATCH_S = 0.1         # a fight's death matches control's death within this

GUN_CLASS = {
    "Classic": "pistol", "Shorty": "shotgun", "Frenzy": "pistol", "Ghost": "pistol", "Sheriff": "sheriff",
    "Stinger": "smg", "Spectre": "smg", "Bucky": "shotgun", "Judge": "shotgun", "Bulldog": "rifle",
    "Guardian": "rifle", "Phantom": "rifle", "Vandal": "rifle", "Marshal": "sniper", "Outlaw": "sniper",
    "Operator": "sniper", "Ares": "lmg", "Odin": "lmg",
}
UNTAGGED_CLASSES = frozenset({"shotgun", "other"})  # classes given no speed tags; an unknown gun is "other"

DENY_ABILITIES = frozenset({  # utility near them; an unlisted name counts in no group  # PROVISIONAL(D5)
    "4_TripWire", "4_TripWire_SecondWire", "MolotovFire", "Q_Molotov_Production", "AcidMolotov_NewMolotov",
    "C_ExplodeyPatch", "Reclaim_Orb_ExplodeyPatch", "4_SlowField_Production", "E_Slow_Large",
    "4_ExplosiveBolt_Explosion", "ConcussPulse", "Concuss", "C_TimeSlowGrenade_Explosion", "E_Turret",
    "E_Trap", "Q_CageTrap", "Q_Cage", "Q_Explosion", "Q_DecayExplosion", "E_Explosion", "4_BeeSwarm_Damage",
    "Q_ElectricSphere", "E_SweetSpotFissure", "Q_SeizeTrap", "E_AirStrikeMortar", "X_DelayedBeam_Beam",
    "4_FusionBlast", "X_Shockwave", "StealthingTrap_Flash_2", "X_Bomb", "X_OrbitalStrike_Production",
    "Q_ShellShockGrenade", "4_NetToss", "4_RemovableNet", "E_CableJam_Root", "4_BlackHole", "X_TidalWave",
    "X_TidalWave_Chunk", "X_FishingHook_Cage", "E_SuppressionPulse", "Q_FlameWallManager_Production",
    "E_Wall_Segment_Fortifying", "E_Wall_Fortifying", "Wall", "Q_WallManager", "X_Segment",
    "X_SegmentManager", "X_GlobalWall", "BarbedWire", "WallTrap", "Q_SoundSensor", "Q_SoundSensor_Fissure",
    "Q_Tether_SphereExpansion", "4_SplashGrenade",
})

SMOKE_ABILITIES = frozenset({  # smoke placements  # PROVISIONAL(D5)
    "4_Smoke", "NewSmoke", "NewSmoke_PDS", "E_Smoke", "4_Smoke_ProductionNEW", "4_SmokeZone",
    "4_SmokeManager_Production", "E_SmokeZone", "E_SmokeZone_Fake", "E_SmokeScreenManager", "E_WorldSmoke",
})
