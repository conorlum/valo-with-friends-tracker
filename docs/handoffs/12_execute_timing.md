# Handoff 12: Execute timing: does our util land when we swing?

Read `docs/handoffs/README.md` (ground rules, shared building blocks), `CLAUDE.md` (especially "Critical
Util") and `docs/superpowers/specs/2026-10-21-critical-util-design.md` first. Research and a design spec for the
owner's approval before code.

## Goal

For attacking site takes (and, mirrored, defensive retakes): how well the team's utility and entries line up. The
gap between flashes, smokes and stuns landing and the first teammate entering, the spread of the entries, and
whether that timing goes with winning the take.

Target finding: "Our flashes pop 1.4 s before anyone swings on Haven A, and takes where the flash and the swing
are within 0.5 s succeed much more often."

The hard part is deciding what an execute is. Start there and check it by eye with the owner before measuring
anything else.

## What exists

- Casts with kind, position, time and who they affected (blinded, concussed, revealed; kills on affected
  enemies): `app/critutil/extract.py`, stored in `replay_round_util.data`. Flash status intervals are exact in the
  engine (`[t, t + dur)`).
- Positions per tick: the condensed blob.
- Areas: `callouts.json` (each callout with a `super` region: A, B, Mid...), reviewed paint, and the map's chokes
  (`app/static/data/control/<Map>.chokes.json`), which may already mark entrances.
- Win chance: `app/services/util_model.py`.
- Fights and their start times: Critical Moments.

## Step 1: detect an execute (declare rules; validate on 30 by hand)

Candidate definition, to refine with the owner:

- **Site regions and entrances** per map, as data: a site's region from callouts with that `super`; its
  entrances from chokes or a small hand-made file (ask whether the owner wants to draw them in the tagger).
- **Execute:** within a window W (propose 8 s), at least K (propose 2) attacking util casts land on the site or its
  entrances (smoke, flash, stun, molly, recon), and at least 2 attackers cross an entrance into the site.
- **Times:** execute start = the first such util landing; entry = the first attacker crossing into the site; the
  take ends at the plant or when the attackers are all dead.
- **Other kinds:** a dry hit (3 or more enter with no util), a split (entries from two entrances more than 3 s
  apart), a fake (util on one site, then the team goes to the other: the util shows up without an entry).
- **Retakes:** the same rules for defenders entering a planted site.

Print every detected execute and every attacking round with none, so missed ones are visible.

## Step 2: measures per execute

- **Flash lead:** entry time minus each flash's pop time (negative = swung before the flash). Use the flash's effect
  window and whether it blinded anyone.
- **Smoke timing:** smokes up before entry and how long before (a smoke too early fades during the take; use its
  duration from the abilities table).
- **Entry spread:** time from the first to the third attacker entering.
- **Util used on the take:** count and kinds; util held by attackers who died in the take.
- **Payoff:** kills on blinded or concussed enemies during the take (Critical Util already links them).
- **Outcome:** plant or not, round won, kills and deaths in the take, the win-chance change over the take.

## Step 3: analysis

- Distributions per map and site for our team, and the same measures for strangers' executes in the same replays
  (unnamed) as a reference.
- Which timing measures go with a successful take, controlling for the man advantage at the start. Cluster
  bootstrap by match; report nulls.

## Output

- **Viewer:** an "Execute" marker on the round timeline with its numbers ("flash lead −0.3 s, entries over 2.1 s,
  planted").
- **Team view** (on `/squad` or `/util`): per map and site, median flash lead, entry spread and take success, with
  n; a list of the best and worst executes linking into the viewer.
- **Coach:** later, as facts with logged ids, with the owner's go-ahead.

## Storage and pipeline

- Phase 1 is a read-only script over stored blobs and util rows.
- If kept: compute on the worker in the gap run next to Critical Util (it needs positions and util rows), with its
  own result key and failure status; bump `GAPS_REVISION`; the worker image ships the module.

## Tests

Synthetic rounds: an execute with util then entry, a dry hit, a fake, a split; flash lead sign; region crossing
with boundary jitter. Add to `tests/suites.toml`; run the slow lane before merging gap-run changes.

## Phases and done criteria

1. Detection on 5 replays, every execute and every miss printed. Done when the owner has checked them in the viewer.
2. Measures and analysis. Done when the report with intervals exists.
3. UI.

## Open questions for the owner

- W and K.
- How site entrances are defined per map.
- Whether retakes come in the first version.
