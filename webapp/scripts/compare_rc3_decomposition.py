"""Declared review check 4 for rc3: every term is its weight times its raw value.

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
        [--matches 3104,3120 | --all] [--report-dir DIR]
"""

from __future__ import annotations

import argparse
import dataclasses
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


def _score(db, match_id, config):
    kills_by_round, raw_econ = defaultdict(list), {}

    def on_kill(**kw):
        kills_by_round[kw["round_number"]].append(kw["kill"])

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

        expect(f"{key} D*assists", row.assists_component,
               round(weights.assists * assists[(row.round_id, row.match_player_id)]))
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--matches", help="comma-separated match ids (default: the 13 declared)")
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--report-dir")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    manifest = load_manifest(args.manifest)
    rc3 = config_from_manifest(manifest, RC3)
    rc2 = config_from_manifest(manifest, V2_30_80_BONUS)
    if rc2.econ_model != rc3.econ_model:
        raise SystemExit(f"rc2 ({rc2.econ_model}) and rc3 ({rc3.econ_model}) must share an econ model")

    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        if args.all:
            match_ids = [m for (m,) in db.execute(sa.text("SELECT id FROM matches ORDER BY id")).all()]
        elif args.matches:
            match_ids = [int(m) for m in args.matches.split(",")]
        else:
            match_ids = list(DECLARED_MATCHES)
        total_checks, all_mismatches = 0, []
        for index, match_id in enumerate(match_ids, 1):
            checks, mismatches = check_match(db, match_id, rc3, rc2)
            total_checks += checks
            all_mismatches += mismatches
            if index % 100 == 0:
                print(f"  {index}/{len(match_ids)} matches, {len(all_mismatches)} mismatches", flush=True)
        db.rollback()
    finally:
        db.close()

    lines = [f"# rc3 decomposition check: {len(match_ids)} matches", "",
             f"- checks: {total_checks:,}", f"- mismatches: {len(all_mismatches):,}", ""]
    lines += [f"- {m}" for m in all_mismatches[:200]]
    report = "\n".join(lines) + "\n"
    if args.report_dir:
        os.makedirs(args.report_dir, exist_ok=True)
        with open(os.path.join(args.report_dir, "rc3-decomposition.md"), "w", encoding="utf-8") as handle:
            handle.write(report)
    print(report)
    return 1 if all_mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
