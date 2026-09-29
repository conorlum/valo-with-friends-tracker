# Replay viewer: ability pops and player status effects

2026-09-28. Branch `replay-status` (built on #90's `replay-owners`). Written after measuring the local
exports; every timing below comes from the replays unless marked **game value**.

## The problem

Abilities that go off in an instant and leave a status on players are drawn on the map for as long as
their object lives in the replay, which is far longer than the ability is actually active:

| Ability (agent, code) | Object | Object lives | What actually happens (measured) |
|---|---|---|---|
| Saturate (Waylay, Terra) | `GameObject_Terra_C_TimeSlowGrenade_Explosion` | 10.0 s | pops at spawn; hit players get a ~0.6 s effect naming the object |
| M-pulse (Miks, Iris; objects `Thumper`) | `GameObject_Thumper_Concuss` | 5.0 s | pulses at +0, +2, +4 s (self effects); concuss ~1.0-1.5 s on the hit |
| Special Delivery (Tejo, Cashew) | `GameObject_Cashew_Q_ShellShockGrenade` | 0.9 s | pops at close (damage at +0.9 s); ~0.9 s effect on the hit |
| Fault Line (Breach) | `GameObject_Breach_E_SweetSpotFissure` | 6.1 s | fires at +1.1 s (self one-shot) |
| Aftershock (Breach) | `GameObject_Breach_4_FusionBlast` | 4.3 s | blasts hit at +2.2 and +2.8 s |
| Rolling Thunder (Breach) | `GameObject_Breach_X_Shockwave` | 6.9 s | no self effect recorded |
| Nova Pulse (Astra, Rift) | `GameObject_Rift_Q_FlashBurst` | 2.0 s | no self effect recorded |
| ZERO/point (KAY/O, Grenadier) | `Gameobject_Grenadier_E_SuppressionPulse` | 15.0 s | pulses at +1.0 s; suppressed players get a one-shot naming the pulse. **Not ingested at all today**: the archetype is spelled `Gameobject` |
| Undercut (Iso, Sequoia) | `GameObject_Sequoia_Q_FragileMissile_TrajectoryWarning` | 1.6 s | Fragile on the hit for exactly 4.0 s (effect naming Iso) |
| Wingman's stun (Gekko, Aggrobot) | `Projectile_E_Aggrobot_DiscTurret_PowerWave` | 1.9 s | concuss for exactly 2.0 s (effect naming Gekko) |
| Seize / Chokehold (Fade, Veto) | `*_Tether_SphereExpansion` | 4.5 s | tethered ~4.3 s (effect naming the object) - its area already lasts about right |

Neon (Sprinter), Deadlock (Cable) and Harbor (Mage) appear in no archived replay, so none of their
objects can be named or timed yet.

## Where a status is in the replay

Two shapes, both effects played on the hit player's character:

1. **Naming the ability object** (Saturate, M-pulse, Tejo, ZERO/point, tethers, Wingman's wave). Direct:
   the object is the source, its owner (already exact from #89/#90) is who applied it.
2. **Naming the caster** (Iso's Fragile, Gekko's concuss, M-pulse's concuss on Haven). The container ID
   varies by replay, so it is found the way reveals are: a container whose plays on enemies cluster
   within 3 s after that caster's status source pops (>= 80 % of its plays, at least 2).

A continuous effect gives the duration (start to stop). A one-shot marks only the moment (ZERO/point):
shown as a ping, except where the **game value** is known and important: suppression lasts 8 s.

## Plan

### Ingest (`app/replays/extras.py`, CONDENSE_REVISION 8 -> 9)
1. Read `Gameobject_*` archetypes as `GameObject` (case-insensitive kind) so ZERO/point is ingested.
2. Record every effect an ability object plays on itself (continuous and one-shot), and store the
   distinct times (0.2 s apart, round seconds) as the ability's `fx` for the pop abilities in
   `POP_ARCHETYPES` only, so blobs don't grow for everything.
3. New util kind `status`: `{k, t, by (the applier), t1, target, code, name, status, from}`:
   - `from: "object"`: an effect on a character naming a live ability object listed in
     `STATUS_OBJECTS` (archetype -> status label: hindered, concussed, suppressed, fragile, tethered,
     decayed), or any object of an agent in `STATUS_AGENTS` (Neon, Deadlock, Harbor, Astra: label
     "hit"). The applier is the object's owner.
   - `from: "caster"`: the calibrated caster-named containers after a source in `STATUS_SOURCES`.
   - Only the applier's enemies count (teams from the condenser; as reveals do), never the applier.
   - One per (source, target): overlapping effects merge; one-shots last `STATUS_PING_S` (1 s) or the
     label's known duration (`suppressed`: 8 s, **game value**).
4. Tests: casing, `fx`, both status shapes, teams, merging, one-shot durations.

### Viewer (`app/static/js/replay.js`)
1. `abilityStyle` gains `pop` (seconds shown after the last pop). A pop ability shows from its spawn
   until `last pop + pop`, where the pops are its `fx` or its spawn: Saturate (spawn + 1 s),
   M-pulse (a ring per pulse at +0/+2/+4 s), Fault Line and Rolling Thunder (the aim until the pop,
   then 0.8 s), Aftershock (spawn -> +3.0 s: the blasts), Nova Pulse (its 2 s life, charging then a
   burst), ZERO/point (a pulse at +1 s, then gone; the knife's badge until then), Tejo (as now).
   `abilitiesAt` uses it, so the map stops showing them for the object's whole life.
2. Pops draw as an expanding ring burst in the owner's colour at each pop time.
3. Statuses draw on the affected player while active: a coloured ring (one colour per status,
   labelled, never colour alone), a small chip under the player's name ("CONCUSSED" ...), and the
   ability's badge; a one-shot pings once. The tooltip names who applied it with what, and when.
4. The Utility tab notes who each ability hit ("concussed A, B"), as reveals do.
5. Tests: pop windows, `statusesAt`, every new archetype has a style naming a real ability.

### Not in this change
- Neon/Deadlock/Harbor object styles and pop timings (no replay has them): generic statuses only.
- Blind/nearsight already come from the W-e flash/nearsight casts; not duplicated here.

## After merge
The user re-ingests: all 10 stored replays are local.

## Review (2026-09-28, before implementing)

Checked the plan against the data and the existing code:

- **Risk: object-named effects that are not statuses.** Smokes and heals also play effects naming their
  object on players inside them. Fixed in the plan: only an allow-list (`STATUS_OBJECTS`, plus the four
  unseen agents' objects), and enemies only (heals are ally-only).
- **Risk: the caster-named calibration over-matching.** The same ">= 80 % inside the window" rule that
  holds for reveals; plus enemies only. Checked on Ascent: Iso's Fragile container is 11/11 inside,
  4.0 s every time.
- **Risk: the 8 s suppression is a game value**, not measured. Kept, because a one-second ping would
  hide the thing that matters; labelled in code and tooltip as the game's duration.
- **Gap: Rolling Thunder and Nova Pulse have no measured pop.** They keep short windows (the wave 1.5 s;
  Nova its own 2 s life) instead of inventing a pop time.
- **Size:** `fx` only for pop abilities, statuses one row per (source, target): a few hundred rows a
  match, small beside shots.
- **Changed after review:** statuses from an unknown owner are kept (drawn neutral) rather than dropped,
  since the target is still exact.

## Results (implemented 2026-09-28)

Full condense of four exports with this branch (statuses per ability, median duration; none on a
teammate of the applier in any match):

| Match | Statuses found |
|---|---|
| Haven d07225f3 | ZERO/point suppressed 24 (8.0 s, game value), M-pulse concussed 10 (1.3 s), Saturate hindered 15 (0.6 s) |
| Ascent a5549826 | Undercut fragile (4.0 s, caster-named), Saturate hindered 10 (0.6 s) |
| Abyss 81e38a00 | Wingman concussed 13 (2.0-2.5 s), Chamber slow 7, Sage slow 5, Saturate 4 |
| Summit 6e52839b | Chokehold tethered 5 (4.5 s), Sage slow 28 (1.5 s) |

Pops (`fx`) as measured: ZERO/point [+1.0 s], Fault Line [+0, +1.1 s], M-pulse [+0, +2, +4 s].

Changed during implementation: Undercut's object-named effects turned out to be the missile's
**path warning** (0.2-1 s on anyone in its path), so Undercut counts only the caster-named Fragile
(`CASTER_ONLY`).

Still not found: Breach's concuss (Fault Line, Rolling Thunder) and Astra's Nova Pulse concuss don't
show on the hit players in these recordings (Nova has only 4 casts), so they get their pops but no
status. Neon, Deadlock and Harbor still have no replay.
