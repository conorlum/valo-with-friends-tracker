"""The Stage 1b gate, executable: every corruption in the plan's gate table, each with its
stated expected outcome (docs/replay-viewer-plan.md, "Stage 1b", Gate).

    .\\.venv313\\Scripts\\python.exe scripts\\replay_gate.py
    .\\.venv313\\Scripts\\python.exe scripts\\replay_gate.py --export-dir %TEMP%\\valo-replay\\<uuid> --vrf <archive .vrf>

- Link-level rows run on a synthetic condensed replay and synthetic DB objects (a complete
  15-round, 13-2 competitive match), with and without decoded round winners. No DB of any kind.
- Condense-level rows run on the same synthetic shape and, with `--export-dir`, on a real export
  (loaded once with the streaming loader; each corruption edits a copy of the kept rows).
- Rows that need the competitive replay print `WAITS (1b)`.

Each line is `row | variant | expected | actual | OK/MISMATCH/WAITS`. Exit 1 on any MISMATCH.
The gate is this output, not a prose checklist.
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass, replace
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))
sys.path.insert(0, str(WEBAPP_ROOT / "tests" / "replays"))

from replay_synthetic import (  # noqa: E402
    AGENT_NAMES,
    MATCH_UUID,
    SyntheticMatch,
    team_of,
)

from app.replays import condense as cd  # noqa: E402
from app.replays import contract  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays import link as lk  # noqa: E402
from app.replays.contract import GAME_STATE_PATH, ContractError  # noqa: E402

# The synthetic linkable match: 15 rounds, 13-2 to team-1 (Red), a clock offset of 0.4 s.
BASE_KILLS = {
    1: [(10.0, 0, 5), (12.0, 1, 6), (15.0, 2, 7), (20.0, 3, 8), (25.0, 4, 9)],
    2: [(8.0, 5, 0), (9.0, 6, 1), (18.0, 0, 5), (22.0, 7, 2), (30.0, 8, 3), (31.0, 9, 4)],
    3: [(5.0, 2, 9), (6.0, 9, 2), (6.05, 8, 3), (20.0, 4, 8), (21.0, 0, 7), (22.0, 1, 6), (23.0, 3, 5)],
    4: [(4.0, 5, 0), (7.0, 6, 1), (9.0, 7, 2), (11.0, 8, 3), (13.0, 9, 4), (14.0, 4, 9)],
}
ROUNDS = 15
KILLS = {n: list(BASE_KILLS[(n - 1) % 4 + 1]) for n in range(1, ROUNDS + 1)}
WINNERS = {n: "Blue" if n in (2, 4) else "Red" for n in range(1, ROUNDS + 1)}
OFFSET = 0.4
FALLBACK_DIAGNOSTIC = {"code": "raw_payload_fallback", "export_group_path": GAME_STATE_PATH,
                       "field_name": "RoundResults"}


def mp(slot: int) -> int:
    return 500 + slot


def db_match(kills=None, agents=None, rounds=None, score=None, shift=0.0, external_id=MATCH_UUID) -> lk.DbMatch:
    agents = agents or AGENT_NAMES
    players = [lk.DbPlayer(mp(s), 900 + s, agents[s], "team-1" if team_of(s) == 0 else "team-2") for s in range(10)]
    rows, next_id = [], 1
    for n, script in (kills or KILLS).items():
        for t, killer, victim in script:
            rows.append(lk.DbKill(next_id, n, round(t + OFFSET + shift, 3), mp(killer), mp(victim)))
            next_id += 1
    rounds = rounds if rounds is not None else [
        lk.DbRound(n, f"Team {'A' if WINNERS[n] == 'Red' else 'B'} Elimination Win") for n in sorted(WINNERS)]
    wins = [sum(1 for r in rounds if lk.outcome_winner(r.outcome) == team) for team in ("team-1", "team-2")]
    t1, t2 = score or wins
    return lk.DbMatch(42, external_id.upper(), t1, t2, rounds, players, rows)


# ---------------------------------------------------------------- outcomes


@dataclass
class Outcome:
    status: str                 # linked | refused | unlinked | refused_at_condense | condensed
    check: str | None = None    # the linker's check, or the condense refusal's reason
    detail: str = ""
    mapping: dict | None = None
    offset: float | None = None
    candidates: int | None = None
    unobserved: int = 0
    eligible: bool | None = None

    def text(self) -> str:
        parts = [self.status]
        if self.check:
            parts.append(f"({self.check}{': ' + self.detail if self.detail else ''})")
        if self.offset is not None:
            parts.append(f"offset {self.offset:+.3f}")
        if self.candidates is not None:
            parts.append(f"candidates {self.candidates}")
        if self.unobserved:
            parts.append(f"unobserved lives {self.unobserved}")
        return " ".join(parts)


def link_outcome(replay: cd.CondensedReplay, match: lk.DbMatch, *, winners: bool,
                 edit=None) -> Outcome:
    view = lk.ReplayLinkView(replay.match_uuid, replay.round_count, replay.report["dropped_final_round"],
                             copy.deepcopy(replay.players), copy.deepcopy(replay.link_inputs))
    if not winners:
        view.link_inputs["round_results"] = {}
    if edit:
        edit(view)
    result = lk.link(view, [match], {})
    return Outcome(result.status, result.report.get("check"), result.report.get("reason", ""),
                   result.slot_to_match_player or None, result.clock_offset, result.report.get("candidates"))


def condense_outcome(run) -> Outcome:
    try:
        replay = run()
    except ContractError as refused:
        return Outcome("refused_at_condense", refused.reason, refused.detail[:90])
    unobserved = sum(1 for blob in replay.rounds.values() for ivs in blob["alive"].values() for iv in ivs
                     if len(iv) > 3 and "unobserved" in iv[3])
    return Outcome("condensed", unobserved=unobserved, eligible=replay.link_inputs["eligibility"]["eligible"])


# ---------------------------------------------------------------- expectations


def expect_refused(*checks):
    def ok(o: Outcome, base: Outcome) -> bool:
        return o.status == "refused" and (not checks or o.check in checks)
    return ok


def expect_linked_same(offset_shift: float = 0.0):
    def ok(o: Outcome, base: Outcome) -> bool:
        return (o.status == "linked" and o.mapping == base.mapping
                and abs((o.offset or 0) - (base.offset or 0) - offset_shift) <= 0.01)
    return ok


def expect_condense_refusal(*reasons, detail: str = ""):
    def ok(o: Outcome, base: Outcome) -> bool:
        return o.status == "refused_at_condense" and o.check in reasons and detail in o.detail
    return ok


# ---------------------------------------------------------------- the synthetic rows


def synthetic_replays(tmp: Path) -> dict[str, cd.CondensedReplay]:
    legacy = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(KILLS), winners=dict(WINNERS))
    directory = legacy.write(tmp / "legacy")
    return {"base": cd.condense_export_dir(directory, source_sha256=legacy.source_sha256), "match": legacy}


def link_rows(replay: cd.CondensedReplay) -> list[tuple]:
    """(row, expected text, expectation, db match, view edit)."""

    def swap_identity(kills):
        k = copy.deepcopy(kills)
        # Round 1's 10 s and 20 s kills trade identities: 10 s apart, far outside any pairing window.
        (t1, a1, b1), (t2, a2, b2) = k[1][0], k[1][3]
        k[1][0], k[1][3] = (t1, a2, b2), (t2, a1, b1)
        return k

    def swap_in_window(kills):
        k = copy.deepcopy(kills)
        k[3][1], k[3][2] = (6.05, 9, 2), (6.0, 8, 3)  # 0.05 s apart, reversed in the DB
        return k

    def kill_free(view):
        agents = list(AGENT_NAMES)
        agents[7] = agents[2]
        for p in view.players:
            p["agent"] = agents[p["slot"]]
        view.link_inputs["kills"] = {n: [k for k in ks if 2 not in k[1:] and 7 not in k[1:]]
                                     for n, ks in view.link_inputs["kills"].items()}
        spawns = view.link_inputs["spawn_points"]
        spawns["2"], spawns["7"] = spawns["7"], spawns["2"]
        for positions in view.link_inputs["start_positions"].values():
            positions["2"], positions["7"] = positions["7"], positions["2"]

    kill_free_agents = list(AGENT_NAMES)
    kill_free_agents[7] = kill_free_agents[2]
    kill_free_kills = {n: [k for k in s if 2 not in k[1:] and 7 not in k[1:]] for n, s in KILLS.items()}

    def moved_spawn(view):
        view.link_inputs["spawn_points"]["0"] = list(view.link_inputs["spawn_points"]["5"])

    agent_changed = list(AGENT_NAMES)
    agent_changed[2] = "Viper"
    wrong_kills = {n: [(t, (a + 1) % 5 + 5 * (a // 5), b) for t, a, b in s] for n, s in KILLS.items()}
    shifted_rounds = [lk.DbRound(r.number + 1, r.outcome) for r in db_match().rounds]
    return [
        ("a shifted round number", "refused", expect_refused(), db_match(rounds=shifted_rounds), None),
        ("two kills swapped by identity", "refused", expect_refused(), db_match(kills=swap_identity(KILLS)), None),
        ("two kills within the pairing window swapped in order", "linked, same mapping", expect_linked_same(),
         db_match(kills=swap_in_window(KILLS)), None),
        ("an agent changed", "refused", expect_refused(), db_match(agents=agent_changed), None),
        ("a 3 s clock shift", "refused", expect_refused("clock"), db_match(shift=3.0), None),
        ("a 0.5 s clock shift", "linked, same mapping, offset +0.5 s", expect_linked_same(0.5),
         db_match(shift=0.5), None),
        ("the wrong match", "refused", expect_refused(), db_match(kills=wrong_kills), None),
        ("two opposing same-agent slots made kill-free, spawn evidence crossed", "refused (two candidates)",
         lambda o, b: o.status == "refused" and o.check == "assignment" and o.candidates == 2,
         db_match(kills=kill_free_kills, agents=kill_free_agents), kill_free),
        ("a slot's spawn evidence moved to the other team", "refused (proximity disagrees)",
         expect_refused("proximity"), db_match(), moved_spawn),
        ("the DB's last k rounds deleted, score unchanged", "refused (P-e)",
         expect_refused("rounds", "completeness", "score"), db_match(rounds=db_match().rounds[:-3], score=(13, 2)),
         None),
    ]


def coverage_row(tmp: Path, match: SyntheticMatch) -> tuple[str, cd.CondensedReplay]:
    start = match.round_start(1)
    movement = [r for r in match.movement()
                if not (r["shooter_character_net_guid"] == match.pawn(1, 0) and start + 26_000 < r["time_ms"])]
    directory = match.write(tmp / "coverage", movement=movement)
    return "coverage cut to 52% for one player", cd.condense_export_dir(directory, source_sha256=match.source_sha256)


def fallback_row(tmp: Path) -> cd.CondensedReplay:
    """The same match, condensed with a manifest-only RoundResults fallback diagnostic."""
    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(KILLS), winners=dict(WINNERS),
                           manifest_extra={"diagnostics": [FALLBACK_DIAGNOSTIC]})
    directory = match.write(tmp / "fallback")
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256)


def dormant_link_row(tmp: Path, match: SyntheticMatch) -> cd.CondensedReplay:
    """A mid-match dormant close and reopen of slot 0's round-5 pawn, 20-24 s into round 5."""
    start = match.round_start(5)
    pawn = match.pawn(5, 0)
    extra = [{"type": "actor_closed", "time_ms": start + 20_000, "actor_net_guid": pawn, "channel": 10,
              "reason": "dormancy"},
             {"type": "actor_spawned", "time_ms": start + 24_000, "actor_net_guid": pawn, "channel": 10,
              "archetype_path": f"/Game/Characters/{match.agent_codes[0]}/X.Default__{match.agent_codes[0]}_PC_C"}]
    events = sorted(match.events() + extra, key=lambda row: row["time_ms"])
    movement = [r for r in match.movement()
                if not (r["shooter_character_net_guid"] == pawn and start + 20_000 < r["time_ms"] < start + 24_000)]
    directory = match.write(tmp / "dormant", events, movement)
    return cd.condense_export_dir(directory, source_sha256=match.source_sha256)


def truncation_margin(replay: cd.CondensedReplay, winners: bool) -> str:
    """The smallest per-round kill prefix that still links uniquely: with the anchor minimum, and
    without it (how much the kills alone pin the assignment)."""
    most = max(len(s) for s in KILLS.values())
    found = {}
    for label, minimum in (("with limits", lk.MIN_ANCHORS), ("assignment only", 1)):
        saved = lk.MIN_ANCHORS
        lk.MIN_ANCHORS = minimum
        try:
            for j in range(1, most + 1):
                kills = {n: s[:j] for n, s in KILLS.items()}

                def trim(view, j=j):
                    view.link_inputs["kills"] = {n: ks[:j] for n, ks in view.link_inputs["kills"].items()}

                if link_outcome(replay, db_match(kills=kills), winners=winners, edit=trim).status == "linked":
                    found[label] = f"{j} per round ({sum(len(s) for s in kills.values())} kills)"
                    break
            else:
                found[label] = "none"
        finally:
            lk.MIN_ANCHORS = saved
    return "; ".join(f"{k}: {v}" for k, v in found.items())


# ---------------------------------------------------------------- condense-level rows


def edit_rows(export: contract.Export, mutate) -> contract.Export:
    events = [contract.Row(r.index, r.time_ms, copy.deepcopy(r.data)) for r in export.events]
    events = mutate(events)
    return replace(export, events=sorted(events, key=lambda row: row.time_ms))


def phase_rows(events, begin_value):
    return [r for r in events if r.data.get("function_name") == contract.RPC_PHASE_BEGIN
            and (r.data.get("payload") or {}).get("NewPhase") == begin_value]


def condense_rows(export: contract.Export, run) -> list[tuple]:
    """(row, expected text, expectation, the export to condense). `run(export)` condenses it."""
    players = cd.build_players(export, cd.load_agents())
    by_slot_pawn = {slot: pawn for pawn, slot in players.pawn_slot.items()}
    game = cd.read_game_state(export)
    middle = len(game.windows) // 2

    def remove_a_4(events):
        target = phase_rows(events, 4)[middle]
        return [r for r in events if r is not target]

    def move_a_5(events):
        fives = phase_rows(events, 5)
        threes = phase_rows(events, 3)
        five = fives[middle - 1]
        later_three = next(r for r in threes if r.time_ms > five.time_ms)
        return [contract.Row(r.index, later_three.time_ms + 1, r.data) if r is five else r for r in events]

    def second_state(events):
        state = next(r.data["payload"][contract.CHARACTER_PLAYER_STATE] for r in events
                     if r.data.get("type") == "export_group_received"
                     and int(r.data.get("actor_net_guid") or 0) == by_slot_pawn[1]
                     and (r.data.get("payload") or {}).get(contract.CHARACTER_PLAYER_STATE))
        t = events[0].time_ms + 100
        return events + [contract.Row(-1, t, {"type": "export_group_received", "time_ms": t, "actor_net_guid": state,
                                              "export_group_path": contract.PLAYER_STATE_PATH,
                                              "payload": {"SpawnedCharacter": by_slot_pawn[3]}})]

    rows = [
        ("one phase 4 removed", "refused at condense (P-a)",
         expect_condense_refusal("phase_cycle", "phase_ended"), edit_rows(export, remove_a_4)),
        ("one phase 5 moved after the next 3", "refused at condense (P-a)",
         expect_condense_refusal("phase_cycle", "phase_ended"), edit_rows(export, move_a_5)),
        ("a pawn claimed by a second player state", "refused at condense (P-b)",
         expect_condense_refusal("ownership"), edit_rows(export, second_state)),
    ]
    lifecycle = cd.read_lifecycle(export, players, game.windows[-1][1], max(export.end_ms, game.windows[-1][2]))
    left = lifecycle.left
    if left:
        slot, gone = sorted(left.items())[0]
        start = next(s for s, _, _ in game.windows if s > gone)
        victim = next(s for s in range(10) if s != slot and s not in left)

        def kill_after_left(events):
            t = start + 5000
            return events + [contract.Row(-1, t, {"type": "rpc_received", "time_ms": t, "actor_net_guid": 1,
                                                  "function_name": contract.RPC_KILLED_ENEMY,
                                                  "payload": {"KillerCharacter": by_slot_pawn[slot],
                                                              "KilledCharacter": by_slot_pawn[victim]}})]

        rows.append(("a kill by a player after their \"left\" close", "refused at condense (P-c)",
                     expect_condense_refusal("lifecycle", detail="after that player left"),
                     edit_rows(export, kill_after_left)))
    return [(name, text, check, (lambda e=edited: run(e))) for name, text, check, edited in rows]


def synthetic_condense_export(tmp: Path) -> tuple[contract.Export, SyntheticMatch]:
    match = SyntheticMatch(shape="swiftplay", leaver=(9, SyntheticMatch().round_start(3) - 5000),
                           kills={1: [(10.0, 0, 5), (12.0, 1, 6)], 2: [(8.0, 5, 0), (9.0, 6, 1)], 3: [(5.0, 2, 8)]})
    directory = match.write(tmp / "condense")
    return contract.load_export_streaming(directory), match


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-dir", type=Path, help="a real export for the condense-level rows")
    parser.add_argument("--vrf", type=Path, help="that export's .vrf (map and match UUID)")
    parser.add_argument("--work", type=Path, help="scratch folder (default: a temp folder)")
    args = parser.parse_args(argv)

    import tempfile
    work = args.work or Path(tempfile.mkdtemp(prefix="replay-gate-"))
    lines: list[tuple[str, str, str, str, str]] = []

    def record(row, variant, expected, outcome: Outcome | str, ok: bool | None, verdict: str | None = None):
        verdict = verdict or ("WAITS" if ok is None else ("OK" if ok else "MISMATCH"))
        lines.append((row, variant, expected, outcome if isinstance(outcome, str) else outcome.text(), verdict))

    # Link level, synthetic, with and without decoded winners.
    synth = synthetic_replays(work)
    base_replay, match = synth["base"], synth["match"]
    for winners in (True, False):
        variant = "synthetic, winners" if winners else "synthetic, no winners"
        base = link_outcome(base_replay, db_match(), winners=winners)
        record("the base synthetic match links", variant, "linked", base, base.status == "linked")
        for name, expected, check, match_row, edit in link_rows(base_replay):
            outcome = link_outcome(base_replay, match_row, winners=winners, edit=edit)
            record(name, variant, expected, outcome, check(outcome, base))
        name, low = coverage_row(work / ("w" if winners else "nw"), SyntheticMatch(
            rounds=ROUNDS, kills=copy.deepcopy(KILLS), winners=dict(WINNERS)))
        outcome = link_outcome(low, db_match(), winners=winners)
        record(name, variant, "refused (P-f)", outcome, outcome.status == "refused" and outcome.check == "eligibility")
        outcome = link_outcome(fallback_row(work / ("fw" if winners else "fnw")), db_match(), winners=winners)
        record("a manifest-only RoundResults fallback diagnostic", variant, "refused (P-f)", outcome,
               outcome.status == "refused" and outcome.check == "eligibility")
        dormant = dormant_link_row(work / ("dw" if winners else "dnw"), SyntheticMatch(
            rounds=ROUNDS, kills=copy.deepcopy(KILLS), winners=dict(WINNERS)))
        outcome = link_outcome(dormant, db_match(), winners=winners)
        unobserved = sum(1 for blob in dormant.rounds.values() for ivs in blob["alive"].values() for iv in ivs
                         if len(iv) > 3 and "unobserved" in iv[3])
        outcome.unobserved = unobserved
        record("a mid-match dormant close and reopen", variant, "linked, the life unobserved", outcome,
               outcome.status == "linked" and outcome.mapping == base.mapping and unobserved == 1)
        record("kills truncated: the smallest prefix that still links uniquely", variant, "reported margin",
               truncation_margin(base_replay, winners), True)

    # Condense level, synthetic Swiftplay shape (with a leaver).
    export, smatch = synthetic_condense_export(work)
    vrf = smatch.write_vrf(work / f"{MATCH_UUID}.vrf")
    header = cd.header_match_uuid(vrf)
    maps, agents = cd.load_maps(), cd.load_agents()
    recipe = fmt.recipe(contract.load_pin().commit, fmt.assets_revision())

    def synthetic_run(e):
        return cd.condense(e, maps=maps, agents_by_code=agents, recipe=recipe,
                           vrf_map_codes=contract.map_codes_in_file(vrf), header=header)

    base = condense_outcome(lambda: synthetic_run(export))
    record("the base synthetic export condenses", "synthetic", "condensed", base, base.status == "condensed")
    for name, expected, check, run in condense_rows(export, synthetic_run):
        outcome = condense_outcome(run)
        record(name, "synthetic", expected, outcome, check(outcome, base))

    # Condense level, the real export.
    if args.export_dir:
        real = contract.load_export_streaming(args.export_dir)
        real_vrf = args.vrf
        real_header = cd.header_match_uuid(real_vrf) if real_vrf else None
        real_codes = contract.map_codes_in_file(real_vrf) if real_vrf else None

        def real_run(e):
            return cd.condense(e, maps=maps, agents_by_code=agents, recipe=recipe, vrf_map_codes=real_codes,
                               header=real_header)

        variant = f"real export {args.export_dir.name[:8]}"
        base = condense_outcome(lambda: real_run(real))
        # Not a gate-table row: whether the export condenses at all. A refusal here is a recorded
        # finding (docs/replay-viewer-plan.md, "Pass-6 findings"), not a corruption's outcome; the
        # rows below still discriminate by the check that refuses.
        record("(baseline) the real export as it is", variant, "condensed", base, None,
               "OK" if base.status == "condensed" else "FINDING")
        for name, expected, check, run in condense_rows(real, real_run):
            outcome = condense_outcome(run)
            record(name, variant, expected, outcome, check(outcome, base))
        record("a mid-match dormant close and reopen", variant, "linked, the life unobserved",
               "not run: needs a condensable base and a DB match", None)

    # What waits for the competitive replay.
    for name in ("the scratch linker accepts the competitive match (one candidate, every slot pinned, eligible)",
                 "every link-level row above, on the competitive replay, with and without winners",
                 "every condense-level row above, on the competitive export",
                 "the limits frozen from two or more competitive matches"):
        record(name, "competitive", "as stated", "WAITS (1b)", None)

    width = [max(len(line[i]) for line in lines) for i in range(4)]
    for line in lines:
        print(" | ".join(part.ljust(width[i]) if i < 4 else part for i, part in enumerate(line)))
    mismatches = sum(1 for line in lines if line[4] == "MISMATCH")
    print(f"\n{sum(1 for l in lines if l[4] == 'OK')} OK, {mismatches} MISMATCH, "
          f"{sum(1 for l in lines if l[4] == 'WAITS')} WAITS, {sum(1 for l in lines if l[4] == 'FINDING')} FINDING")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
