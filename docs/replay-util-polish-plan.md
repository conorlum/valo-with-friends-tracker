# Replay preview: utility polish plan

Branch `replay-local-preview` (local only). Written 2026-09-26. Scope: how the preview draws agent
utility (`app/static/js/replay.js`, `app/replays/extras.py`). Nothing here touches format v1, the
condenser recipe, or scoring.

## Where things stand

- `ABILITY_STYLES` in `replay.js` maps 30 archetype patterns, all from the Ascent match (a5549826):
  Omen, Clove, Cypher, Sova, Waylay, Iso, Phoenix, Skye. Anything else falls back to a plain badge
  labelled with the raw archetype name and no icon.
- `static/data/abilities.json` already has icons for all 29 agents, so a mapping only needs the
  archetype → ability name.
- Two more exports are on disk (`%TEMP%\valo-replay\<uuid>\events.ndjson`) with **no bundle**:
  - Abyss `81e38a00` (refused at P-f eligibility): Astra, Gekko, Raze, Chamber, Sage, Reyna, plus
    Cypher/Skye/Waylay/Omen.
  - `d45b2844` (a third `.vrf` in the archive, not in `index.json`): Fade, Veto, Viper, Jett, KAY/O,
    Chamber, Reyna, Clove.
- Slot letters in archetype names don't line up with the icon slots: `Hunter_Q_SonarBolt` is
  `ability2`, `Phoenix_Q_...` is `grenade`, `Wraith_4_...` is `ability2`. A generic
  letter → icon rule is out, so every ability needs an explicit mapping.

## Found while surveying (bugs, not polish)

1. **Archetypes that don't start with the agent code get no agent.** `extras.ABILITY_ARCHETYPE` takes the
   first token as the code, so these come through with `agent = None`, which means no owner guess,
   no icon and a neutral colour:
   `Projectile_E_Aggrobot_DiscTurret_PowerWave`, `Projectile_E_Aggrobot_OrbSpawner`,
   `Projectile_C_Grenadier_Flash_Underhand`, `GameObject_Q_BountyHunter_Tether_SphereExpansion`,
   `Projectile_Q_BountyHunter_TetherGrenade_SphereExpansion`, `Projectile_E_BountyHunter_Divebomb`.
   (`RemovableObject_GumshoeTrackingDart` is already special-cased in JS.)
   Fix: when the first token is a lone slot letter (`C|Q|E|X|4`) or `RemovableObject`, look for a known
   agent code among the following tokens and rewrite the name to the usual `<Code>_<Letter>_...` form.
   Add unit tests for each shape.
2. **`GameObject_Infinity_Rain_Instance`** (21 on Abyss, 9 on d45b): the code `Infinity` isn't in
   `agents.json`. Both matches had Chamber and Reyna. Before styling it, find out what it is from
   where and when it spawns. Until then it stays hidden, not drawn as a mystery badge.

## Step 1: make the other matches viewable (prerequisite)

Checking the new styles by eye needs a bundle for Abyss (and ideally d45b). **Decision needed:**
- **(A, recommended)** Add a dev-only `--allow-ineligible` flag to `build_replay_bundle.py` that builds
  the bundle anyway, stamps the refusal reason into `bundle.json`, and shows a banner on
  `/dev/replays/<uuid>`. This is quick and keeps the Abyss P-f investigation (open item 3) separate.
- (B) Fix the Abyss P-f refusal first. That's slower, but it's open anyway and matters for D19.

d45b2844 also needs to be checked: what mode it is, whether it's in the DB, and whether it can link.
If it can't link, it's only used to find archetype names (still worth it), and the new styles are
checked by eye with a small synthetic round in the node tests instead.

## Step 2: map the observed archetypes

Ability names below come from game knowledge, so each one gets checked against when and where it
spawns and against the icon before it's committed. The "?" rows need an actual look.

| Agent (code) | Archetype | Ability | Shape |
|---|---|---|---|
| Astra (Rift) | `E_SmokeZone` | Nebula | smoke |
| | `E_SmokeZone_Fake` | Nebula (dissipate) | smoke, fainter |
| | `Q_FlashBurst` | Nova Pulse | area (concuss) |
| | `4_BlackHole` | Gravity Well | area |
| | `X_GlobalWall` | Astral Form / Cosmic Divide | **line** (new shape, see step 3) |
| | `X_Markers` | Astral Form (stars) | small badge, unlisted |
| Gekko (Aggrobot) | `C_ExplodeyPatch` (Patch) | Mosh Pit | area |
| | `SeekerNade` (Pawn) | Wingman | badge + path |
| | `RollyPolly` (Pawn) | Thrash | badge + path |
| | `Reclaim_Orb_*` | globules | small badge, unlisted |
| | `Zamboni_Rocket`, `DiscTurret_PowerWave` | ? (Dizzy / Wingman) | hidden (projectiles) |
| Raze (Clay) | `Q_Explosion` | Blast Pack | area |
| | `E_Boomba` (Pawn) | Boom Bot | badge + path |
| Chamber (Deadeye) | `E_Trap` | Trademark | badge |
| | `E_Slow_Large` (Patch) | Trademark slow | area |
| | `E_Teleporter_Tether` | Rendezvous | badge |
| Sage (Thorne) | `E_Wall_Fortifying` (+ `_Segment_`) | Barrier Orb | **line** (segments hidden) |
| | `4_SlowField_Production` (Patch) | Slow Orb | area |
| Reyna (Vampire) | `4_NearsightAOE_Source` | Leer | badge |
| | `Q_Heal_HealPool_*` | Soul orbs | small badge, unlisted |
| Fade (BountyHunter) | `E_LoSReveal_Source_Reactivate` | Haunt | reveal |
| | `Q_Tether_SphereExpansion` | Seize | area |
| | `X_WaveForm` | Nightfall | badge (direction: a later step) |
| | `E_Divebomb` | ? (Prowler) | check |
| Veto (Pine) | `4_UsableTeleport` | Crosscut | badge |
| | `Q_SeizeTrap`, `Q_Tether_SphereExpansion` | Chokehold | badge / area |
| | `E_RadEater` (Pawn) | Interceptor | badge |
| Viper (Pandemic) | `E_SmokeScreenManager` | Toxic Screen | **line** |
| | `AcidMolotov_NewMolotov` (Patch) | Snake Bite | area |
| Jett (Wushu) | `4_SmokeZone` | Cloudburst | smoke (small r) |
| Clove (Smonk) | `NewSmoke_PDS` | Ruse (after death) | smoke |
| KAY/O (Grenadier) | flash / knife / frag | all projectiles only | nothing drawn today, see step 4 |

Radii: use in-game values (converted to world units, 100 per metre, as the existing `r:` values are)
and adjust by eye on the map.

## Step 3: new shapes

- **line**: a wall drawn from the spawn point along its yaw with a fixed length (Sage wall, Viper
  screen, Astra divide). This needs the rotation we already read (`_Actor.yaw`) passed into the extras
  row. Check it against the Sage segment actors, whose positions should sit on the drawn line.
- **fake smoke**: the `smoke` shape at lower opacity with a dashed edge.

## Step 4: fallback and unmapped utility

- Unknown archetypes on a known agent get a team-coloured badge with the **agent's** icon and a readable
  name ("Breach utility"), instead of the raw archetype name.
- `build_replay_bundle.py` lists unmapped archetypes in `extras_report`, so each new match shows
  what's missing. This needs the style table readable from Python. Either move `ABILITY_STYLES` to
  `static/data/ability_styles.json`, loaded by both sides, or have the report just list every archetype
  and let a node test flag the unmapped ones. **Recommend the JSON move**: it's one source of truth and
  the node tests get simpler.
- KAY/O and other projectile-only utility: drawing where it lands needs the projectile's end
  position. Check whether `actor_closed` or a later export group carries one. If not, leave it out.

## Step 5: agents with no fixture yet

Breach, Brimstone, Deadlock, Harbor, Killjoy, Miks, Neon, Tejo, Vyse and Yoru have no export, so there
are no real archetype names to map. **Don't guess them.** They get the step 4 fallback until a replay with
them is archived. The only way to get more is for you to download replays of your own matches with
those agents.

## Tests

- Python: the step 1 normalisation, one case per irregular shape above. The flag's refusal stamp.
- Node: every `ability` in the style table exists in `abilities.json` under its agent (catches typos);
  every archetype seen in the fixtures resolves to a style or is explicitly hidden; the line shape's
  endpoints.
- Visual: `/dev/replays/<abyss>` and `<d45b>` via headless Playwright (or the Chrome extension if it
  connects), a few rounds each, screenshots for you to review.

## Order and size

1. Step 1 bug fix (small), then the Abyss/d45b bundles via decision A.
2. Move styles to JSON and add the node consistency tests.
3. Step 2 mappings, verified by eye one agent at a time.
4. Step 3 line and fake-smoke shapes.
5. Step 4 fallback and unmapped report. Look into KAY/O landings, time-boxed.

Out of scope: Cypher owner decoding, spike planter, tracer length, the 390 px nav overflow, a Watch link on
the match page.
