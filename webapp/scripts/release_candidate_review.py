r"""Read-only review of the release candidate against today's scoring.

    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --match 3129
    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --sample 10
    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --trace 3129

Answers three questions, in the order they are worth asking:

  --match   for ONE match, what every player's Impact becomes and by how much
  --sample  across N matches, how the whole distribution moves
  --trace   for ONE match, how each individual kill earned its score, in the
            context of its round and the match

NEVER writes. It does not touch impact_scores, does not flip a flag, and does
not bump a version. It computes the candidate rows in memory by passing the
existing flags to build_impact_rows_for_match, which is the same entry point
the evaluation harness uses.

THE CANDIDATE. `enable_econ_component` + `enable_postplant_leverage`, at
`use_realized_swing=True` (what production scoring passes). That pair is arm
3, the econ spec's own "what actually ships". `--preplant` additionally
enables Part 3.

  Part 3 CAVEAT, stated because the number is wrong without it: its centring
  constant c = 0.900537 is solved and recorded but NOT yet applied at
  runtime. With --preplant this script applies it itself, so the review shows
  what would ship rather than what the code currently computes. Wiring c into
  impact.py is outstanding work.

THE POST-PLANT TABLE is fitted on the FULL corpus here, deliberately. Fitting
it out-of-fold is a rule for EVALUATING the candidate, not for shipping it --
the table that would ship is fitted on everything. Do not quote any number
from this script as out-of-fold evidence; the five-arm report is that.
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.models.match import Team
from app.scoring.impact import build_impact_rows_for_match
from app.scoring.postplant_factor import (
    build_factor_table,
    extract_postplant_kills,
    solve_and_apply_centering,
)
from app.scoring.postplant_value_table import (
    DEFAULT_W,
    build_value_table,
    extract_postplant_round_seconds,
)
from app.scoring.preplant_centering import EMPIRICAL_CUTOFF

# Solved 2026-09-09 over the SCORED population; see the time spec's
# "2026-09-09: the centring gate, run against the shipped curve".
PREPLANT_C = 0.900537


def build_candidate_table(db):
    """The post-plant factor table as it would ship: fitted on everything."""
    seconds = extract_postplant_round_seconds(db)
    kills = extract_postplant_kills(db)
    table = build_factor_table(build_value_table(seconds, w=DEFAULT_W), kills)
    centering = solve_and_apply_centering(table, kills)
    return table, centering, len(kills)


class _CentredPreplant:
    """Applies Part 3's solved centring constant on top of the shipped curve.

    impact.py multiplies by the RAW curve today -- c is solved and recorded
    but not yet wired in. Patching it here, script-locally and loudly, is the
    only way this review can show what would SHIP rather than what the code
    currently computes. Delete this once c is applied in impact.py.
    """

    def __init__(self, wrapped, c):
        self._wrapped, self._c = wrapped, c

    def __call__(self, dt, is_attacker, **kwargs):
        factor = self._wrapped(dt, is_attacker, **kwargs)
        # c multiplies ONLY the scored population. A fallback kill (dt>30, or
        # no plant) keeps exactly 1.0 -- that is the decided boundary policy.
        # use_realized=False is the leakage gate and must stay exactly
        # neutral: scaling it would carry a retrospective constant into an
        # ex-ante replay.
        in_scored_population = (
            kwargs.get("use_realized", True)
            and dt is not None
            and 0 < dt <= EMPIRICAL_CUTOFF
        )
        return self._c * factor if in_scored_population else factor


def apply_preplant_centering():
    """Returns an undo callable, so the patch cannot leak past its caller."""
    import app.scoring.impact as impact_module

    original = impact_module.empirical_preplant_factor
    impact_module.empirical_preplant_factor = _CentredPreplant(original, PREPLANT_C)
    return lambda: setattr(impact_module, "empirical_preplant_factor", original)


def candidate_kwargs(table, preplant: bool) -> dict:
    kwargs = {
        "use_realized_swing": True,
        "enable_econ_component": True,
        "enable_postplant_leverage": True,
        "postplant_factor_table": table,
    }
    if preplant:
        kwargs["enable_preplant_empirical"] = True
    return kwargs


def _names(db, match_id):
    rows = db.execute(text(
        "SELECT mp.id, p.display_name, mp.team FROM match_players mp "
        "JOIN players p ON p.id = mp.player_id WHERE mp.match_id = :m"
    ), {"m": match_id}).mappings()
    return {r["id"]: (r["display_name"], r["team"]) for r in rows}


def _match_header(db, match_id):
    row = db.execute(text(
        "SELECT external_id, map_name, played_at, team1_rounds_won, team2_rounds_won "
        "FROM matches WHERE id = :m"
    ), {"m": match_id}).mappings().first()
    return row


def _round_numbers(db, match_id):
    return {
        r["id"]: r["round_number"]
        for r in db.execute(text(
            "SELECT id, round_number FROM rounds WHERE match_id = :m"
        ), {"m": match_id}).mappings()
    }


def _totals_by_player(rows):
    totals = defaultdict(lambda: {"impact": 0, "kill": 0, "death": 0, "damage": 0,
                                  "econ": 0, "time": 0, "swing": 0, "econ_component": 0})
    for r in rows:
        t = totals[r.match_player_id]
        t["impact"] += r.impact
        t["kill"] += r.kill_impact
        t["death"] += r.death_impact
        t["damage"] += r.damage
        t["econ"] += r.econ_impact
        t["time"] += r.time_impact
        t["swing"] += r.swing_impact
        t["econ_component"] += r.econ_component
    return totals


def _stored_totals(db, match_id):
    rows = db.execute(text(
        "SELECT s.match_player_id, SUM(s.impact) AS impact "
        "FROM impact_scores s JOIN rounds r ON r.id = s.round_id "
        "WHERE r.match_id = :m GROUP BY s.match_player_id"
    ), {"m": match_id}).mappings()
    return {r["match_player_id"]: r["impact"] for r in rows}


def review_match(db, match_id, table, preplant):
    header = _match_header(db, match_id)
    names = _names(db, match_id)
    base = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
    cand = build_impact_rows_for_match(db, match_id, **candidate_kwargs(table, preplant))

    print(f"\nMATCH {match_id}  {header['map_name']}  {str(header['played_at'])[:16]}  "
          f"{header['team1_rounds_won']}-{header['team2_rounds_won']}  "
          f"({header['external_id']})")

    # Sanity: today's replay must reproduce what is stored, or the whole
    # comparison is against the wrong reference.
    stored = _stored_totals(db, match_id)
    replay = _totals_by_player(base)
    mismatched = [
        mp for mp in stored
        if mp in replay and stored[mp] != replay[mp]["impact"]
    ]
    if mismatched:
        print(f"  WARNING: replayed baseline differs from stored impact_scores "
              f"for {len(mismatched)} of {len(stored)} players -- the stored "
              f"rows are stale relative to today's code.")
    else:
        print(f"  baseline replay reproduces stored impact_scores exactly "
              f"({len(stored)} players)")

    before, after = _totals_by_player(base), _totals_by_player(cand)
    print(f"\n  {'player':<20} {'team':<7} {'BEFORE':>8} {'AFTER':>8} {'delta':>8} "
          f"{'%':>8}   {'damage':>7} {'leverage':>9} {'econ_c':>7}")
    print("  " + "-" * 94)
    ranked_before = sorted(before, key=lambda m: -before[m]["impact"])
    ranked_after = sorted(after, key=lambda m: -after[m]["impact"])
    for mp in ranked_before:
        name, team = names.get(mp, ("?", "?"))
        b, a = before[mp]["impact"], after[mp]["impact"]
        pct = (a - b) / abs(b) * 100 if b else float("nan")
        print(f"  {name[:20]:<20} {str(team)[5:]:<7} {b:>8,} {a:>8,} {a - b:>+8,} "
              f"{pct:>+7.1f}%   {after[mp]['damage']:>7,} "
              f"{after[mp]['time']:>9,} {after[mp]['econ_component']:>7,}")

    moved = [
        f"{names[mp][0][:14]} {ranked_before.index(mp) + 1}->{ranked_after.index(mp) + 1}"
        for mp in ranked_before if ranked_before.index(mp) != ranked_after.index(mp)
    ]
    print(f"\n  rank movement within the match: "
          f"{', '.join(moved) if moved else 'none -- identical ordering'}")
    return before, after


def review_sample(db, match_ids, table, preplant):
    print(f"\nDISTRIBUTIONAL REVIEW over {len(match_ids)} matches")
    deltas, pcts, rank_moves, rows_seen = [], [], 0, 0
    per_match = []
    for match_id in match_ids:
        base = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
        cand = build_impact_rows_for_match(db, match_id, **candidate_kwargs(table, preplant))
        before, after = _totals_by_player(base), _totals_by_player(cand)
        rows_seen += len(base)
        rb = sorted(before, key=lambda m: -before[m]["impact"])
        ra = sorted(after, key=lambda m: -after[m]["impact"])
        rank_moves += sum(1 for mp in rb if rb.index(mp) != ra.index(mp))
        match_deltas = []
        for mp in before:
            d = after[mp]["impact"] - before[mp]["impact"]
            deltas.append(d)
            match_deltas.append(d)
            if before[mp]["impact"]:
                pcts.append(d / abs(before[mp]["impact"]) * 100)
        per_match.append((match_id, statistics.mean(match_deltas)))

    deltas.sort()
    pcts.sort()

    def pct_at(values, q):
        return values[min(len(values) - 1, int(len(values) * q))]

    print(f"  player-match rows compared: {len(deltas):,}  "
          f"(from {rows_seen:,} player-rounds)")
    print(f"  mean delta          {statistics.mean(deltas):>+10.1f}")
    print(f"  median delta        {statistics.median(deltas):>+10.1f}")
    print(f"  SD of delta         {statistics.pstdev(deltas):>10.1f}")
    print(f"  p1 / p5             {pct_at(deltas, 0.01):>+10.0f} / {pct_at(deltas, 0.05):>+10.0f}")
    print(f"  p95 / p99           {pct_at(deltas, 0.95):>+10.0f} / {pct_at(deltas, 0.99):>+10.0f}")
    print(f"  largest drop / gain {deltas[0]:>+10.0f} / {deltas[-1]:>+10.0f}")
    print(f"  median % change     {statistics.median(pcts):>+10.1f}%")
    print(f"  p5 / p95 % change   {pct_at(pcts, 0.05):>+10.1f}% / {pct_at(pcts, 0.95):>+10.1f}%")
    print(f"  players whose within-match rank moved: {rank_moves:,} of {len(deltas):,} "
          f"({100 * rank_moves / len(deltas):.1f}%)")
    print("\n  per-match mean delta:")
    for match_id, mean_delta in per_match:
        print(f"    match {match_id:<6} {mean_delta:>+9.1f}")


def _regime(context, kill):
    if kill["is_post_plant"]:
        return "post-plant"
    if context["seconds_to_plant"] is None:
        return "pre-plant (never planted)"
    if 0 < context["seconds_to_plant"] <= EMPIRICAL_CUTOFF:
        return f"pre-plant (dt={context['seconds_to_plant']:.1f}s, curve active)"
    return f"pre-plant (dt={context['seconds_to_plant']:.1f}s, beyond curve)"


def trace_match(db, match_id, table, preplant, out_path):
    names = _names(db, match_id)
    header = _match_header(db, match_id)
    rounds = _round_numbers(db, match_id)

    base_events, cand_events = [], []
    build_impact_rows_for_match(
        db, match_id, use_realized_swing=True,
        kill_observer=lambda **kw: base_events.append(kw),
    )
    cand_rows = build_impact_rows_for_match(
        db, match_id, kill_observer=lambda **kw: cand_events.append(kw),
        **candidate_kwargs(table, preplant),
    )

    def key(call):
        return (call["round_number"], call["kill_index"])

    base_by_key = {key(c): c for c in base_events}
    cand_by_key = {key(c): c for c in cand_events}

    econ_by_round = defaultdict(dict)
    for row in cand_rows:
        econ_by_round[rounds[row.round_id]][row.match_player_id] = row

    lines = []
    lines.append(f"PER-KILL TRACE -- match {match_id} {header['map_name']} "
                 f"{header['team1_rounds_won']}-{header['team2_rounds_won']} "
                 f"({header['external_id']})")
    lines.append(f"candidate: econ_component + post-plant leverage"
                 + (" + pre-plant curve" if preplant else "")
                 + f"; post-plant table centred at c={table.centering:.6f}"
                 if hasattr(table, "centering") else "")
    lines.append("")
    lines.append("Each kill shows what the SCORER computed (reported through its own")
    lines.append("per-kill observer, not re-derived). BEFORE averages three factors;")
    lines.append("AFTER uses kill_order_bonus x time_factor alone, with econ moving to")
    lines.append("a separate ROUND-level term shown under each round.")
    lines.append("")

    for round_number in sorted(rounds.values()):
        keys = sorted(k for k in cand_by_key if k[0] == round_number)
        if not keys:
            continue
        context0 = cand_by_key[keys[0]]["context"]
        lines.append("=" * 100)
        lines.append(f"ROUND {round_number}   {context0['round_outcome']}   "
                     f"planted={context0['planted']}"
                     + (f" at {context0['plant_time']:.1f}s" if context0["plant_time"] else ""))
        lines.append("=" * 100)
        for k in keys:
            b, c = base_by_key[k], cand_by_key[k]
            bk, ck, ctx = b["kill"], c["kill"], c["context"]
            killer = names.get(bk["killer_match_player_id"], ("?", ""))[0]
            victim = names.get(bk["death_match_player_id"], ("?", ""))[0]
            side = "ATK" if ctx["killer_is_attacker"] else "DEF"
            lines.append("")
            lines.append(f"  [{ctx['killer_team_alive']}v{ctx['victim_team_alive']}] "
                         f"t={bk['event_time_seconds']:>6.1f}s  {side}  "
                         f"{killer[:16]} killed {victim[:16]}"
                         + ("   (SELF-KILL)" if ctx["self_kill"] else ""))
            lines.append(f"      regime: {_regime(ctx, ck)}")
            lines.append(f"      kill_order_bonus = {ctx['kill_order_bonus_raw']:.0f}  "
                         f"(the man-advantage swing this kill caused)")
            base_kill = (bk["kill_order_bonus_x_econ"] + bk["kill_order_bonus_x_time"]
                         + bk["kill_order_bonus_x_swing"]) / 3
            lines.append(f"      BEFORE  econ x{_ratio(bk['kill_order_bonus_x_econ'], ctx)}"
                         f"  time x{_ratio(bk['kill_order_bonus_x_time'], ctx)}"
                         f"  swing x{_ratio(bk['kill_order_bonus_x_swing'], ctx)}"
                         f"  -> mean {base_kill:>8.1f}")
            lines.append(f"      AFTER   time x{_ratio(ck['kill_order_bonus_x_time'], ctx)}"
                         f"                              "
                         f"  -> {ck['kill_order_bonus_x_time']:>13.1f}")
            lines.append(f"      death side: BEFORE {(bk['death_order_bonus_x_econ'] + bk['death_order_bonus_x_time'] + bk['death_order_bonus_x_swing']) / 3:>8.1f}"
                         f"   AFTER {ck['death_order_bonus_x_time']:>8.1f}"
                         f"   (traded factor {_traded(bk, ctx):.2f})")

        lines.append("")
        lines.append(f"  ROUND {round_number} TOTALS (candidate)")
        lines.append(f"      {'player':<18} {'damage':>7} {'leverage':>9} {'econ_c':>7} "
                     f"{'kill':>7} {'death':>7} {'impact':>7}")
        for mp, row in sorted(econ_by_round[round_number].items(),
                              key=lambda kv: -kv[1].impact):
            lines.append(f"      {names.get(mp, ('?',''))[0][:18]:<18} {row.damage:>7} "
                         f"{row.time_impact:>9} {row.econ_component:>7} "
                         f"{row.kill_impact:>7} {row.death_impact:>7} {row.impact:>7}")
        lines.append("")

    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nper-kill trace written to {out_path} ({len(lines):,} lines, "
          f"{len(cand_by_key):,} kills)")
    return lines


def _ratio(product, context):
    bonus = context["kill_order_bonus_raw"]
    return f"{product / bonus:>5.3f}" if bonus else "  n/a"


def _traded(kill, context):
    bonus = context["kill_order_bonus_raw"]
    return kill["death_order_bonus"] / bonus if bonus else 1.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", type=int)
    parser.add_argument("--sample", type=int)
    parser.add_argument("--trace", type=int)
    parser.add_argument("--preplant", action="store_true")
    parser.add_argument("--out", default="release_candidate_trace.txt")
    args = parser.parse_args()

    db = SessionLocal()
    print("building the release candidate's post-plant table on the full corpus ...")
    table, centering, n_kills = build_candidate_table(db)
    print(f"  {n_kills:,} post-plant kills   c = {centering.c:.6f}   "
          f"death-side residual = {centering.death_side_residual:+.4%}")
    print(f"  candidate = econ_component + post-plant leverage"
          + (f" + pre-plant curve (c={PREPLANT_C}, applied by this script only)"
             if args.preplant else ""))

    undo = apply_preplant_centering() if args.preplant else (lambda: None)
    try:
        _run(db, args, table)
    finally:
        undo()


def _run(db, args, table):
    if args.match:
        review_match(db, args.match, table, args.preplant)
    if args.sample:
        ids = [r[0] for r in db.execute(text(
            "SELECT m.id FROM matches m JOIN impact_scores s "
            "ON s.round_id IN (SELECT id FROM rounds WHERE match_id = m.id) "
            "GROUP BY m.id ORDER BY m.played_at DESC LIMIT :n"
        ), {"n": args.sample}).fetchall()]
        review_sample(db, ids, table, args.preplant)
    if args.trace:
        trace_match(db, args.trace, table, args.preplant, args.out)


if __name__ == "__main__":
    main()
