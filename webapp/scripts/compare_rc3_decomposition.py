"""Declared review check 4: every term is its weight times its raw value.

Parameterised on the manifest's RELEASE comparator (Impact v4 plan, section
3.4): it once hard-selected rc3 and checked D x raw assists, so running it
unchanged on a v4 manifest would have reviewed rc3. With the v4 flags set it
checks D x ELIGIBLE assists -- assists on kills made before the round was
decided -- and the decided-only time factor, each rebuilt independently.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md ("Raw
econ equals the rc2 configuration's raw econ, and every term equals its weight
times its raw value after the scorer's rounding"). It replaces
compare_econ_models.py for rc3, which demanded identical non-econ fields between
comparators and so could not compare configurations whose weights differ.

The point is independence. Each term is rebuilt from something other than the
row that reports it:
- D*assists from round_player_stats;
- raw econ from the rc2 configuration's own run, which must match rc3's exactly;
- leverage from the per-kill values the scorer's kill observer reports, summed
  here in the scorer's order;
- the trade credit from a SEPARATE implementation of the declared rule (the
  2026-09-14 entry: first death of the killer inside the window not by his own
  side, timed share from the schedule, split max/sum when one trade kill avenges
  several players, nothing when the trade kill was a self or environmental
  death). A slip in the scorer's version -- a reversed schedule, a missing
  split, credit paid to the trader -- disagrees with this one.
Damage must also be identical with the credit on and off, so nothing leaks into
it from leverage.

Every comparison is exact. Exits 1 on any mismatch.

    DATABASE_URL=... python scripts/compare_rc3_decomposition.py --manifest M.json \
        [--comparator NAME] [--matches 3104,3120 | --all] [--report-dir DIR]

--matches or --all is required unless the release comparator is rc3, whose
13 declared matches stay its default.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from app.scoring import econ_buy_disruption as bd
from app.scoring import econ_component
from app.scoring import impact
from app.scoring.impact import build_impact_rows_for_match
from app.scoring.impact_manifest import RC3, V2_30_80_BONUS, config_from_manifest, load_manifest

ROUND_SECONDS = 100.0
SPIKE_SECONDS = 45.0

DECLARED_MATCHES = (3104, 3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117, 3120, 3133)


def independent_trade_credits(kills: list[dict], team_of: dict) -> dict[int, float]:
    """The declared trade-credit rule, written again from its declaration rather
    than imported, so that it can disagree with the scorer.

    `kills` are the scorer's per-kill dicts in its own order, carrying
    `kill_order_bonus_x_time`. Returns {victim match_player_id: unweighted credit}.
    """
    by_trade: dict[int, list[tuple[int, float]]] = {}
    for kill in kills:
        killer, victim = kill["killer_match_player_id"], kill["death_match_player_id"]
        if killer is None or killer == victim or team_of[killer] == team_of[victim]:
            continue  # only a death to an enemy earns credit
        trade_index, gap = None, None
        for index, later in enumerate(kills):
            if later["death_match_player_id"] != killer:
                continue
            delay = later["event_time_seconds"] - kill["event_time_seconds"]
            if not 0 <= delay < impact.TRADE_WINDOW_SECONDS:
                continue
            avenger = later["killer_match_player_id"]
            if avenger is not None and avenger != killer and team_of.get(avenger) == team_of.get(killer):
                continue  # the killer's own side killed him: not a trade, keep looking
            trade_index, gap = index, delay
            break
        if trade_index is None:
            continue
        trade = kills[trade_index]
        if trade["killer_match_player_id"] is None or trade["killer_match_player_id"] == killer:
            continue  # a self or environmental trade discounts the death but has no kill to share
        share = next(s for upper, s in impact.TRADE_CREDIT_SCHEDULE if gap < upper)
        by_trade.setdefault(trade_index, []).append((victim, share))

    credits: dict[int, float] = defaultdict(float)
    for trade_index, avenged in by_trade.items():
        shares = [share for _, share in avenged]
        scale = max(shares) / math.fsum(shares)
        for victim, share in avenged:
            credits[victim] += share * scale * kills[trade_index]["kill_order_bonus_x_time"]
    return credits


def independent_round_decided(round_row: dict, t: float) -> bool:
    """Declaration 12's "decided", written again from the declaration rather
    than imported from plant_window, so that it can disagree with the scorer:
    defused and at/after the defuse; a REAL plant (a planted round that is not
    a Time Win) and at/after plant+45; or no real plant, a Time Win, after
    100s."""
    outcome = round_row["outcome"] or ""
    if round_row["defused"] and round_row["defuse_time"] is not None and t >= round_row["defuse_time"]:
        return True
    # A REAL plant: planted, not a Time Win, and with a plant time. plant_time
    # is nullable, and the scorer's own predicate routes a planted round without
    # one through effective_plant_time() is None to the Time-Win branch below;
    # reading None as a number here would raise instead of reporting.
    if round_row["planted"] and "Time Win" not in outcome and round_row["plant_time"] is not None:
        return t >= round_row["plant_time"] + SPIKE_SECONDS
    return t > ROUND_SECONDS and "Time Win" in outcome


def independent_eligible_assist_cuts(db, match_id) -> dict[tuple[int, int], int]:
    """{(round_id, match_player_id): assists to remove}, from kill_events and
    players directly -- not from the scorer's kill dicts, which never carry the
    assistants. The declared rule (declaration 13): an assistant named on a kill
    made after the round was decided is removed, case-insensitively by display
    name; a name matching no player, or more than one, is left in; and no
    player loses more than their scoreboard count (the clamp)."""
    rounds = {r["id"]: r for r in db.execute(sa.text(
        "SELECT id, planted, plant_time, defused, defuse_time, outcome FROM rounds "
        "WHERE match_id = :m"), {"m": match_id}).mappings().all()}
    names = defaultdict(list)
    for mp, name in db.execute(sa.text(
            "SELECT mp.id, p.display_name FROM match_players mp JOIN players p ON p.id = mp.player_id "
            "WHERE mp.match_id = :m"), {"m": match_id}).all():
        names[(name or "").lower()].append(mp)
    scoreboard = {(r, mp): a for r, mp, a in db.execute(sa.text(
        "SELECT s.round_id, s.match_player_id, s.assists FROM round_player_stats s "
        "JOIN rounds r ON r.id = s.round_id WHERE r.match_id = :m"), {"m": match_id}).all()}
    wanted = defaultdict(int)
    for round_id, t, meta in db.execute(sa.text(
            "SELECT k.round_id, k.event_time_seconds, k.source_meta FROM kill_events k "
            "JOIN rounds r ON r.id = k.round_id WHERE r.match_id = :m"), {"m": match_id}).all():
        if isinstance(meta, str):
            meta = json.loads(meta)
        assistants = (meta or {}).get("assistants") or []
        if not assistants or not independent_round_decided(rounds[round_id], t):
            continue
        for name in assistants:
            matched = names.get(str(name).lower(), [])
            if len(matched) == 1:
                wanted[(round_id, matched[0])] += 1
    return {key: min(n, scoreboard.get(key, 0)) for key, n in wanted.items()}


def _score(db, match_id, config):
    kills_by_round, raw_econ = defaultdict(list), {}

    def on_kill(**kw):
        # The context rides along on a copy, for the decided-only time check.
        kills_by_round[kw["round_number"]].append({**kw["kill"], "_context": kw["context"]})

    def on_econ(**kw):
        result = kw.get("result")
        raw_econ[kw["round_number"]] = result.raw_net_by_player() if result is not None else None

    rows = build_impact_rows_for_match(db, match_id, kill_observer=on_kill, econ_observer=on_econ,
                                       **config.build_kwargs())
    return rows, kills_by_round, raw_econ


def check_match(db, match_id, rc3, rc2) -> tuple[int, list[str]]:
    """(checks made, mismatches) for one match."""
    credit_off = dataclasses.replace(rc3, config_id=f"{rc3.config_id}-credit-off", enable_trade_credit=False)
    on_rows, kills_by_round, raw_on = _score(db, match_id, rc3)
    off_rows, _, _ = _score(db, match_id, credit_off)
    _, _, raw_rc2 = _score(db, match_id, rc2)

    weights = rc3.weights
    round_number = dict(db.execute(sa.text("SELECT id, round_number FROM rounds WHERE match_id = :m"),
                                   {"m": match_id}).all())
    round_by_number = {r["round_number"]: r for r in db.execute(sa.text(
        "SELECT round_number, planted, plant_time, defused, defuse_time, outcome FROM rounds "
        "WHERE match_id = :m"), {"m": match_id}).mappings().all()}
    cuts = (independent_eligible_assist_cuts(db, match_id)
            if rc3.remove_post_decided_assists else {})
    team_of = dict(db.execute(sa.text("SELECT id, team FROM match_players WHERE match_id = :m"),
                              {"m": match_id}).all())
    assists = {(r, mp): a for r, mp, a in db.execute(sa.text(
        "SELECT s.round_id, s.match_player_id, s.assists FROM round_player_stats s "
        "JOIN rounds r ON r.id = s.round_id WHERE r.match_id = :m"), {"m": match_id}).all()}

    checks, mismatches = 0, []

    def expect(label, shown, rebuilt):
        nonlocal checks
        checks += 1
        if shown != rebuilt:
            mismatches.append(f"match {match_id} {label}: row says {shown!r}, rebuilt {rebuilt!r}")

    for rn in sorted(set(raw_on) | set(raw_rc2)):
        expect(f"round {rn} raw econ vs rc2", raw_on.get(rn), raw_rc2.get(rn))

    if rc3.enable_decided_only_time:
        # T is 1, or 0 once decided, for every kill and death; a self-kill's
        # kill side stays 0 and its death costs K * 1 * T.
        for rn, kills in kills_by_round.items():
            for index, kill in enumerate(kills):
                context = kill["_context"]
                t = 0.0 if independent_round_decided(round_by_number[rn], kill["event_time_seconds"]) else 1.0
                k = 0 if context["self_kill"] else context["kill_order_bonus_raw"]
                expect(f"round {rn} kill {index} kill time factor", kill["kill_order_bonus_x_time"], k * t)
                expect(f"round {rn} kill {index} death time factor",
                       kill["death_order_bonus_x_time"], kill["death_order_bonus"] * t)

    credits_by_round = {rn: independent_trade_credits(kills, team_of) for rn, kills in kills_by_round.items()}
    off_by_key = {(r.round_id, r.match_player_id): r for r in off_rows}
    for row in on_rows:
        rn = round_number[row.round_id]
        key = f"round {rn} player {row.match_player_id}"
        kills = kills_by_round.get(rn, [])
        kill_sum = 0.0
        for kill in kills:
            if kill["killer_match_player_id"] == row.match_player_id:
                kill_sum += kill["kill_order_bonus_x_time"]
        death_sum = 0.0
        for kill in kills:
            if kill["death_match_player_id"] == row.match_player_id:
                death_sum += kill["death_order_bonus_x_time"]
        credit = credits_by_round.get(rn, {}).get(row.match_player_id, 0.0) * weights.trade_credit_scale
        raw = (raw_on.get(rn) or {}).get(row.match_player_id, 0.0)

        cut = cuts.get((row.round_id, row.match_player_id), 0)
        eligible = assists[(row.round_id, row.match_player_id)] - cut
        label = "D*eligible assists" if rc3.remove_post_decided_assists else "D*assists"
        expect(f"{key} {label}", row.assists_component, round(weights.assists * eligible))
        expect(f"{key} C*econ", row.econ_component, round(weights.econ * (econ_component.ECON_SCALE * raw)))
        expect(f"{key} B*leverage", row.leverage_component,
               round(weights.leverage * (kill_sum + credit - death_sum)))
        expect(f"{key} trade credit", row.trade_credit, round(weights.leverage * credit))
        expect(f"{key} four-term identity", row.impact,
               row.damage + row.leverage_component + row.econ_component + row.assists_component)
        off = off_by_key[(row.round_id, row.match_player_id)]
        expect(f"{key} damage independent of the credit", row.damage, off.damage)
        expect(f"{key} credit off pays no credit", off.trade_credit, 0)
    return checks, mismatches


def configs_from_manifest(manifest: dict, comparator: str | None = None):
    """(configuration under review, the rc2 econ reference). The configuration
    is the manifest's RELEASE comparator unless one is named -- never rc3 by
    default, which would review rc3 whatever the manifest releases."""
    release = config_from_manifest(manifest, comparator)
    rc2 = config_from_manifest(manifest, V2_30_80_BONUS)
    if rc2.econ_model != release.econ_model:
        raise SystemExit(f"rc2 ({rc2.econ_model}) and {release.config_id} ({release.econ_model}) "
                         "must share an econ model")
    return release, rc2


def default_matches(release) -> list[int]:
    """rc3's 13 declared matches, for rc3 only. Another release's review
    cohort is fixed at its own freeze and must be passed explicitly."""
    if release.config_id != RC3:
        raise SystemExit(f"{release.config_id}: pass --matches (its frozen review cohort) or --all; "
                         "rc3's declared matches are not its default")
    return list(DECLARED_MATCHES)


def report_name(release) -> str:
    return "rc3-decomposition.md" if release.config_id == RC3 else f"{release.config_id}-decomposition.md"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--comparator", help="a declared comparator in the manifest other than "
                                             "its release comparator (diagnostics only)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--matches", help="comma-separated match ids (default: the 13 declared)")
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    manifest = load_manifest(args.manifest)
    release, rc2 = configs_from_manifest(manifest, args.comparator)

    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        if args.all:
            match_ids = [m for (m,) in db.execute(sa.text("SELECT id FROM matches ORDER BY id")).all()]
        elif args.matches:
            match_ids = [int(m) for m in args.matches.split(",")]
        else:
            match_ids = default_matches(release)
        total_checks, all_mismatches = 0, []
        for index, match_id in enumerate(match_ids, 1):
            checks, mismatches = check_match(db, match_id, release, rc2)
            total_checks += checks
            all_mismatches += mismatches
            if index % 100 == 0:
                print(f"  {index}/{len(match_ids)} matches, {len(all_mismatches)} mismatches", flush=True)
        db.rollback()
    finally:
        db.close()

    lines = [f"# {release.config_id} decomposition check: {len(match_ids)} matches", "",
             f"- checks: {total_checks:,}", f"- mismatches: {len(all_mismatches):,}", ""]
    lines += [f"- {m}" for m in all_mismatches[:200]]
    report = "\n".join(lines) + "\n"
    if args.report_dir:
        os.makedirs(args.report_dir, exist_ok=True)
        with open(os.path.join(args.report_dir, report_name(release)), "w", encoding="utf-8") as handle:
            handle.write(report)
    print(report)
    return 1 if all_mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
