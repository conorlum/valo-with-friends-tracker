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
EXPENSIVE_FIGHT_M2 = 500.0  # a team fight whose swing is this far below zero is an expensive fight (2026-10-23)
EXPENSIVE_FIGHTS_SHOWN = 3  # the summary's most expensive fights

GUN_CLASS = {
    "Classic": "pistol", "Shorty": "shotgun", "Frenzy": "pistol", "Ghost": "pistol", "Sheriff": "sheriff",
    "Stinger": "smg", "Spectre": "smg", "Bucky": "shotgun", "Judge": "shotgun", "Bulldog": "rifle",
    "Guardian": "rifle", "Phantom": "rifle", "Vandal": "rifle", "Marshal": "sniper", "Outlaw": "sniper",
    "Operator": "sniper", "Ares": "lmg", "Odin": "lmg",
}
UNTAGGED_CLASSES = frozenset({"shotgun", "other"})  # classes given no speed tags; an unknown gun is "other"

# The owner's seven kinds of utility (D5). Most abilities are one of them; walls, decoys, shields, buffs, ult
# markers and pickup orbs are none, and so is a name not listed here.
UTILITY_KINDS = ("damaging", "info", "smoke", "teleport", "heal", "status", "blind")
NEAR_THEM_KINDS = frozenset({"damaging", "info", "status", "blind"})  # "utility near them"; smokes are their own tag
# A hit on a player, by its event kind. Nearsight is a status, not a blind (judgment: vision capped, not zero).
HIT_KIND = {"damage": "damaging", "reveal": "info", "status": "status", "nearsight": "status", "flash": "blind"}

ABILITY_KIND = {  # placed ability name -> kind; agents from the corpus (ability_names, W10)
    # damaging
    "MolotovFire": "damaging", "Q_FlameWallManager_Production": "damaging",                  # Phoenix
    "Q_Molotov_Production": "damaging", "X_OrbitalStrike_Production": "damaging",            # Brimstone
    "AcidMolotov_NewMolotov": "damaging",                                                    # Viper
    "4_ExplosiveBolt_Explosion": "damaging",                                                 # Sova
    "E_Turret": "damaging", "4_BeeSwarm_Damage": "damaging",                                 # Killjoy
    "Q_Explosion": "damaging", "E_Boomba": "damaging",                                       # Raze
    "Q_DecayExplosion": "damaging",                                                          # Clove (decay)
    "E_Explosion": "damaging", "E_AirStrikeMortar": "damaging", "E_MapMissileMarker": "damaging",
    "E_MapMissileMarker_SecondRocket": "damaging", "X_Segment": "damaging", "X_SegmentManager": "damaging",  # Tejo
    "4_FusionBlast": "damaging",                                                             # Breach
    "BarbedWire": "damaging",                                                                # Vyse
    "C_ExplodeyPatch": "damaging", "Reclaim_Orb_ExplodeyPatch": "damaging",                  # Gekko
    # info
    "Q_SonarPing": "info", "Q_SonarBolt": "info", "E_Drone": "info", "E_Drone_RevealDart": "info",  # Sova
    "E_PossessableCamera": "info", "RemovableObject_GumshoeTrackingDart": "info", "X_InterrogateHat": "info",  # Cypher
    "E_LoSReveal_Source_Reactivate": "info",                                                 # Fade
    "4_SonarPing": "info", "4_Spider_LockOn": "info",                                        # Tejo
    # smoke (anything that blocks sight like a smoke: smokes, smoke walls, cages)
    "4_Smoke": "smoke", "NewSmoke": "smoke", "NewSmoke_PDS": "smoke", "E_Smoke": "smoke",
    "4_Smoke_ProductionNEW": "smoke", "4_SmokeManager_Production": "smoke", "4_SmokeZone": "smoke",
    "E_SmokeZone": "smoke", "E_SmokeZone_Fake": "smoke", "X_GlobalWall": "smoke",            # Astra
    "E_SmokeScreenManager": "smoke", "X_Circular": "smoke",                                  # Viper
    "E_WorldSmoke": "smoke", "Q_WallManager": "smoke",                                       # Harbor
    "Q_CageTrap": "smoke", "Q_Cage": "smoke",                                                # Cypher
    "4_Tunnel": "smoke",                                                                     # Neon
    # teleport
    "E_Teleporter_Tether": "teleport", "E_TeleporterMoving_FakeTP": "teleport",
    "E_TeleporterStationary_FakeTP": "teleport", "X_GlobalTeleport_Intention": "teleport",
    "E_RewindTime_RewindTarget": "teleport", "4_UsableTeleport": "teleport",
    # heal
    "Q_Heal_HealPool_High": "heal", "Q_Heal_HealPool_AutoActivate": "heal", "Heal": "heal", "4_Heal_AOE": "heal",
    # status (non-damaging: slows, concusses, tethers, suppress, vulnerable, nearsight, detain)
    "4_TripWire": "status", "4_TripWire_SecondWire": "status",                               # Cypher
    "4_SlowField_Production": "status",                                                      # Sage
    "E_Trap": "status", "E_Slow_Large": "status",                                            # Chamber
    "ConcussPulse": "status", "Concuss": "status", "X_SonicWave": "status",                  # Miks
    "C_TimeSlowGrenade_Explosion": "status", "X_DelayedBeam_Beam": "status",                 # Waylay
    "Q_Tether_SphereExpansion": "status", "Q_SeizeTrap": "status",                           # Fade, Veto
    "4_WolfHound": "status", "X_WaveForm": "status",                                         # Fade
    "4_NearsightAOE_Source": "status",                                                       # Reyna
    "Q_NearsightMissile_TrajectoryWarning": "status",                                        # Omen
    "Q_PossessableScout": "status", "X_Pack": "status",                                      # Skye
    "Q_StealthAlarmbot": "status", "X_Bomb": "status",                                       # Killjoy
    "X_Shockwave": "status", "E_SweetSpotFissure": "status",                                 # Breach
    "Q_ElectricSphere": "status",                                                            # Neon
    "E_SuppressionPulse": "status", "X_UltPulse": "status", "X_UltPulseManager": "status",   # KAY/O
    "Q_FragileMissile_TrajectoryWarning": "status",                                          # Iso
    "Q_ShellShockGrenade": "status",                                                         # Tejo
    "4_NetToss": "status", "4_RemovableNet": "status", "Q_SoundSensor": "status",
    "Q_SoundSensor_Fissure": "status", "X_FishingHook": "status", "X_FishingHook_Cage": "status",  # Deadlock
    "4_BlackHole": "status", "Q_FlashBurst": "status",                                       # Astra
    "X_TidalWave": "status", "X_TidalWave_Chunk": "status", "4_SplashGrenade": "status",     # Harbor
    "SeekerNade": "status", "RollyPolly": "status",                                          # Gekko
    "DisarmPulse": "status",                                                                 # Vyse
    # blind
    "E_HawkFlash_FlashSource": "blind",                                                      # Skye
    "StealthingTrap_Flash_2": "blind",                                                       # Vyse
}
