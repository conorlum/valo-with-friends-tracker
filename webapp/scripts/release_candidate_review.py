r"""Read-only review of a release candidate against today's scoring.

    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --match 3129
    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --ten
    .\.venv\Scripts\python.exe scripts\release_candidate_review.py --trace 3129
    ... --weights 1.25,1.0,0.5      # A,B,C -- default is the declared candidate

Three views, in the order they are worth asking:

  --match   ONE match, every player's Impact before and after
  --ten     the FIXED ten matches below, player by player
  --trace   ONE match, event -> round -> match, including economy attribution

NEVER writes. No flag is flipped, no version bumped, no row touched. The
candidate rows are computed in memory through build_impact_rows_for_match --
the same entry point the evaluation harness uses.

THE CANDIDATE. `enable_econ_component` + `enable_postplant_leverage` at
`use_realized_swing=True` (what production passes), which is arm 3, the econ
spec's own "what actually ships". `--preplant` adds Part 3.

WEIGHTS. impact = A*damage + B*leverage + C*econ_component. The default
(1.25, 1.0, 1.0) is the DECLARED candidate and reproduces today's terms
exactly; C multiplies on top of ECON_SCALE, so C=1.0 means the declared
anchor. Nothing here is fitted -- this tool exists so a weighting can be
LOOKED AT before anyone decides whether to fit one.

RECONCILIATION. Every displayed total is checked, not asserted:
  * per player-round   impact == A*damage + B*leverage + C*econ
  * per player-match   the match row equals the sum of its round rows
  * economy            credits within a team sum to that team's econ_round
  * cross-view         the trace's match totals equal the --match view's
Any failure prints as a RECONCILIATION ERROR rather than being smoothed over.

TWO CAVEATS ON THE NUMBERS.
  * The post-plant table is fitted on the FULL corpus, deliberately:
    out-of-fold is a rule for EVALUATING a candidate, not for shipping one.
    Do not quote anything here as out-of-fold evidence.
  * With --preplant, Part 3's centring constant c is applied by this script,
    because impact.py still multiplies by the raw curve. Wiring it in is
    outstanding work.
"""
import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match
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

PREPLANT_C = 0.900537

# PINNED, so two runs at different weights compare the same games. These were
# the ten most recent matches on 2026-09-09; do not swap them for "most
# recent" or the comparison drifts as matches are ingested.
FIXED_TEN = [3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117]


def build_candidate_table(db):
    """The post-plant factor table as it would ship: fitted on everything."""
    seconds = extract_postplant_round_seconds(db)
    kills = extract_postplant_kills(db)
    table = build_factor_table(build_value_table(seconds, w=DEFAULT_W), kills)
    return table, solve_and_apply_centering(table, kills), len(kills)


class _CentredPreplant:
    """Applies Part 3's solved centring constant on top of the shipped curve.

    impact.py multiplies by the RAW curve today -- c is solved and recorded
    but not wired in. Patching it here, loudly and reversibly, is the only
    way this review can show what would SHIP. Delete once c is in impact.py.
    """

    def __init__(self, wrapped, c):
        self._wrapped, self._c = wrapped, c

    def __call__(self, dt, is_attacker, **kwargs):
        factor = self._wrapped(dt, is_attacker, **kwargs)
        # c multiplies ONLY the scored population. A fallback kill (dt>30, or
        # no plant) keeps exactly 1.0 -- the decided boundary policy. And
        # use_realized=False is the leakage gate: scaling it would carry a
        # retrospective constant into an ex-ante replay.
        in_scored_population = (
            kwargs.get("use_realized", True)
            and dt is not None
            and 0 < dt <= EMPIRICAL_CUTOFF
        )
        return self._c * factor if in_scored_population else factor


def apply_preplant_centering():
    import app.scoring.impact as impact_module

    original = impact_module.empirical_preplant_factor
    impact_module.empirical_preplant_factor = _CentredPreplant(original, PREPLANT_C)
    return lambda: setattr(impact_module, "empirical_preplant_factor", original)


def candidate_kwargs(table, preplant, weights):
    kwargs = {
        "use_realized_swing": True,
        "enable_econ_component": True,
        "enable_postplant_leverage": True,
        "postplant_factor_table": table,
        "weights": weights,
    }
    if preplant:
        kwargs["enable_preplant_empirical"] = True
    return kwargs


def _names(db, match_id):
    return {
        r["id"]: (r["display_name"], r["team"])
        for r in db.execute(text(
            "SELECT mp.id, p.display_name, mp.team FROM match_players mp "
            "JOIN players p ON p.id = mp.player_id WHERE mp.match_id = :m"
        ), {"m": match_id}).mappings()
    }


def _header(db, match_id):
    return db.execute(text(
        "SELECT external_id, map_name, played_at, team1_rounds_won, team2_rounds_won "
        "FROM matches WHERE id = :m"
    ), {"m": match_id}).mappings().first()


def _round_numbers(db, match_id):
    return {
        r["id"]: r["round_number"]
        for r in db.execute(text(
            "SELECT id, round_number FROM rounds WHERE match_id = :m"
        ), {"m": match_id}).mappings()
    }


def _stored_totals(db, match_id):
    return {
        r["match_player_id"]: r["impact"]
        for r in db.execute(text(
            "SELECT s.match_player_id, SUM(s.impact) AS impact "
            "FROM impact_scores s JOIN rounds r ON r.id = s.round_id "
            "WHERE r.match_id = :m GROUP BY s.match_player_id"
        ), {"m": match_id}).mappings()
    }


class Reconciler:
    """Collects every arithmetic claim the report makes, so a mismatch is
    printed rather than silently rendered."""

    def __init__(self):
        self.checks = 0
        self.failures = []
        self.rounding_gaps = 0

    def identity(self, label, total, parts):
        self.checks += 1
        if total != sum(parts):
            self.failures.append(f"{label}: shown {total} != sum of parts {sum(parts)}")

    def equal(self, label, left, right):
        self.checks += 1
        if left != right:
            self.failures.append(f"{label}: {left} != {right}")

    def close(self, label, left, right, tolerance=1e-6):
        self.checks += 1
        if abs(left - right) > tolerance:
            self.failures.append(f"{label}: {left} != {right}")

    def report(self):
        if self.failures:
            print(f"\n  *** {len(self.failures)} RECONCILIATION ERRORS "
                  f"out of {self.checks:,} checks ***")
            for failure in self.failures[:20]:
                print(f"      {failure}")
        else:
            print(f"\n  reconciliation: all {self.checks:,} checks pass")
        if self.rounding_gaps:
            print(f"  note: (kill_impact - death_impact) differs from "
                  f"(A*damage + B*leverage) by 1 on {self.rounding_gaps:,} rows, "
                  f"because each rounds independently. `impact` is built from "
                  f"the three terms, never from that subtraction.")


def _totals(rows, rec=None, label=""):
    """Per-player match totals, with the per-row identity checked."""
    out = defaultdict(lambda: dict(impact=0, kill=0, death=0, damage=0,
                                   leverage=0, econ=0, rounds=0))
    for row in rows:
        if rec is not None:
            rec.identity(f"{label} round {row.round_id} player {row.match_player_id}",
                         row.impact,
                         [row.damage, row.leverage_component, row.econ_component])
            if row.kill_impact - row.death_impact != row.leverage_component + row.damage:
                rec.rounding_gaps += 1
        t = out[row.match_player_id]
        t["impact"] += row.impact
        t["kill"] += row.kill_impact
        t["death"] += row.death_impact
        t["damage"] += row.damage
        t["leverage"] += row.leverage_component
        t["econ"] += row.econ_component
        t["rounds"] += 1
    return out


def review_match(db, match_id, table, preplant, weights, rec):
    header, names = _header(db, match_id), _names(db, match_id)
    base = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
    cand = build_impact_rows_for_match(
        db, match_id, **candidate_kwargs(table, preplant, weights))

    print(f"\nMATCH {match_id}  {header['map_name']}  {str(header['played_at'])[:16]}  "
          f"{header['team1_rounds_won']}-{header['team2_rounds_won']}  "
          f"({header['external_id']})")

    stored = _stored_totals(db, match_id)
    replayed = _totals(base)
    stale = [mp for mp in stored if mp in replayed and stored[mp] != replayed[mp]["impact"]]
    print("  baseline replay vs stored impact_scores: "
          + (f"{len(stale)} of {len(stored)} players DIFFER (stored rows are stale)"
             if stale else f"identical for all {len(stored)} players"))

    before = _totals(base)
    after = _totals(cand, rec, f"match {match_id}")

    print(f"\n  {'player':<20} {'team':<7} {'BEFORE':>8} {'AFTER':>8} {'delta':>8} "
          f"{'%':>8}  |  {'A*dmg':>7} {'B*lev':>7} {'C*econ':>7} {'=impact':>8}")
    print("  " + "-" * 103)
    ranked_before = sorted(before, key=lambda m: -before[m]["impact"])
    ranked_after = sorted(after, key=lambda m: -after[m]["impact"])
    for mp in ranked_before:
        name, team = names.get(mp, ("?", "?"))
        b, a = before[mp]["impact"], after[mp]["impact"]
        d, l, e = after[mp]["damage"], after[mp]["leverage"], after[mp]["econ"]
        rec.identity(f"match {match_id} player {mp} match total", a, [d, l, e])
        pct = (a - b) / abs(b) * 100 if b else float("nan")
        print(f"  {name[:20]:<20} {str(team)[5:]:<7} {b:>8,} {a:>8,} {a - b:>+8,} "
              f"{pct:>+7.1f}%  |  {d:>7,} {l:>7,} {e:>7,} {d + l + e:>8,}")

    moved = [f"{names[mp][0][:14]} {ranked_before.index(mp) + 1}->{ranked_after.index(mp) + 1}"
             for mp in ranked_before if ranked_before.index(mp) != ranked_after.index(mp)]
    print(f"\n  rank movement: {', '.join(moved) if moved else 'none'}")
    return before, after


def review_ten(db, match_ids, table, preplant, weights, rec):
    print(f"\nFIXED TEN MATCHES (pinned: {', '.join(str(m) for m in match_ids)})")
    all_deltas, all_pcts, rank_moves, per_match = [], [], 0, []

    for match_id in match_ids:
        header, name_map = _header(db, match_id), _names(db, match_id)
        base = build_impact_rows_for_match(db, match_id, use_realized_swing=True)
        cand = build_impact_rows_for_match(
            db, match_id, **candidate_kwargs(table, preplant, weights))
        before, after = _totals(base), _totals(cand, rec, f"match {match_id}")

        rb = sorted(before, key=lambda m: -before[m]["impact"])
        ra = sorted(after, key=lambda m: -after[m]["impact"])
        moved = sum(1 for mp in rb if rb.index(mp) != ra.index(mp))
        rank_moves += moved

        print(f"\n  match {match_id}  {header['map_name']:<9} "
              f"{header['team1_rounds_won']}-{header['team2_rounds_won']}   "
              f"{moved}/{len(rb)} players change rank")
        print(f"    {'player':<20} {'BEFORE':>7} {'AFTER':>7} {'delta':>7}  "
              f"{'A*dmg':>7} {'B*lev':>7} {'C*econ':>7}")
        for mp in rb:
            b, a = before[mp]["impact"], after[mp]["impact"]
            d, l, e = after[mp]["damage"], after[mp]["leverage"], after[mp]["econ"]
            rec.identity(f"match {match_id} player {mp} match total", a, [d, l, e])
            all_deltas.append(a - b)
            if b:
                all_pcts.append((a - b) / abs(b) * 100)
            print(f"    {name_map.get(mp, ('?',))[0][:20]:<20} {b:>7,} {a:>7,} "
                  f"{a - b:>+7,}  {d:>7,} {l:>7,} {e:>7,}")
        per_match.append((match_id, statistics.mean(
            [after[m]["impact"] - before[m]["impact"] for m in before])))

    all_deltas.sort()
    all_pcts.sort()

    def at(values, q):
        return values[min(len(values) - 1, int(len(values) * q))]

    print(f"\n  ACROSS ALL TEN ({len(all_deltas)} player-matches)")
    print(f"    mean delta {statistics.mean(all_deltas):>+9.1f}   "
          f"median {statistics.median(all_deltas):>+9.1f}   "
          f"SD {statistics.pstdev(all_deltas):>9.1f}")
    print(f"    p1 {at(all_deltas, 0.01):>+8.0f}  p5 {at(all_deltas, 0.05):>+8.0f}  "
          f"p95 {at(all_deltas, 0.95):>+8.0f}  p99 {at(all_deltas, 0.99):>+8.0f}")
    print(f"    largest drop {all_deltas[0]:>+8.0f}   largest gain {all_deltas[-1]:>+8.0f}")
    print(f"    median % change {statistics.median(all_pcts):>+7.1f}%   "
          f"p5 {at(all_pcts, 0.05):>+8.1f}%   p95 {at(all_pcts, 0.95):>+7.1f}%")
    print(f"    rank changes: {rank_moves} of {len(all_deltas)} "
          f"({100 * rank_moves / len(all_deltas):.1f}%)")
    print("    per-match mean delta: "
          + "  ".join(f"{m}:{d:+.0f}" for m, d in per_match))


def _regime(context, kill):
    if kill["is_post_plant"]:
        return "post-plant"
    dt = context["seconds_to_plant"]
    if dt is None:
        return "pre-plant, round never planted (flat 1.0 by policy)"
    if 0 < dt <= EMPIRICAL_CUTOFF:
        return f"pre-plant, dt={dt:.1f}s (inside the Part 3 curve)"
    return f"pre-plant, dt={dt:.1f}s (beyond the curve, flat 1.0)"


def _r(product, bonus):
    return f"{product / bonus:>5.3f}" if bonus else "  n/a"


def trace_match(db, match_id, table, preplant, weights, rec, out_path):
    names, header = _names(db, match_id), _header(db, match_id)
    rounds = _round_numbers(db, match_id)

    kills_seen, econ_seen, base_kills = [], [], []
    rows = build_impact_rows_for_match(
        db, match_id,
        kill_observer=lambda **kw: kills_seen.append(kw),
        econ_observer=lambda **kw: econ_seen.append(kw),
        **candidate_kwargs(table, preplant, weights),
    )
    build_impact_rows_for_match(
        db, match_id, use_realized_swing=True,
        kill_observer=lambda **kw: base_kills.append(kw),
    )

    base_by_key = {(c["round_number"], c["kill_index"]): c for c in base_kills}
    cand_by_key = {(c["round_number"], c["kill_index"]): c for c in kills_seen}
    econ_by_round = {d["round_number"]: d for d in econ_seen}
    rows_by_round = defaultdict(dict)
    for row in rows:
        rows_by_round[rounds[row.round_id]][row.match_player_id] = row

    A, B, C = weights.damage, weights.leverage, weights.econ
    L = ["EVENT -> ROUND -> MATCH TRACE",
         f"match {match_id}  {header['map_name']}  "
         f"{header['team1_rounds_won']}-{header['team2_rounds_won']}  "
         f"({header['external_id']})",
         f"weights: A(damage)={A}  B(leverage)={B}  C(econ)={C}"
         + ("   + Part 3 pre-plant curve" if preplant else ""),
         "",
         "impact = A*damage + B*leverage + C*econ_component",
         "",
         "Every number below is what the SCORER computed, reported through its",
         "own per-kill and per-econ observers -- not re-derived.",
         "",
         "KILL CREDIT.  Each kill earns kill_order_bonus (how much man-advantage",
         "  it swung) times a time factor. BEFORE averaged three factors (econ,",
         "  time, swing); the candidate uses time alone and moves economy to a",
         "  separate round-level term.",
         "DEATH CREDIT.  The same event debits the victim, discounted by the",
         "  traded factor -- a death traded back within 10s costs less.",
         "ECONOMY.  A team-level magnitude econ_round(T) is DIVIDED among that",
         "  team's players by how much enemy loadout value each removed:",
         "      credit(p) = econ_round(T)   * removed_by(p) / removed(T)",
         "      debit(p)  = econ_round(opp) * lost_by(p)    / removed(opp)",
         "  so credits within a team always sum to that team's own econ_round.",
         "  It is a SHARE of the allocation, never a per-player sum.",
         ""]

    match_totals = defaultdict(lambda: dict(damage=0, leverage=0, econ=0, impact=0))

    for round_number in sorted(rounds.values()):
        keys = sorted(k for k in cand_by_key if k[0] == round_number)
        round_rows = rows_by_round.get(round_number, {})
        if not round_rows:
            continue
        ctx0 = cand_by_key[keys[0]]["context"] if keys else None
        L.append("=" * 104)
        L.append(f"ROUND {round_number}"
                 + (f"   {ctx0['round_outcome']}   planted={ctx0['planted']}"
                    + (f" at {ctx0['plant_time']:.1f}s" if ctx0["plant_time"] else "")
                    if ctx0 else "   (no kills)"))
        L.append("=" * 104)

        for k in keys:
            b, c = base_by_key[k], cand_by_key[k]
            bk, ck, ctx = b["kill"], c["kill"], c["context"]
            killer = names.get(bk["killer_match_player_id"], ("?",))[0]
            victim = names.get(bk["death_match_player_id"], ("?",))[0]
            bonus = ctx["kill_order_bonus_raw"]
            L.append("")
            L.append(f"  [{ctx['killer_team_alive']}v{ctx['victim_team_alive']}] "
                     f"t={bk['event_time_seconds']:>6.1f}s  "
                     f"{'ATK' if ctx['killer_is_attacker'] else 'DEF'}  "
                     f"{killer[:16]} -> {victim[:16]}"
                     + ("   (SELF-KILL: no kill credit, death still debits)"
                        if ctx["self_kill"] else ""))
            L.append(f"      {_regime(ctx, ck)}")
            L.append(f"      kill_order_bonus  {bonus:>8.0f}   "
                     f"(the man-advantage this kill swung)")
            base_mean = (bk["kill_order_bonus_x_econ"] + bk["kill_order_bonus_x_time"]
                         + bk["kill_order_bonus_x_swing"]) / 3
            L.append(f"      BEFORE  econ x{_r(bk['kill_order_bonus_x_econ'], bonus)}"
                     f"  time x{_r(bk['kill_order_bonus_x_time'], bonus)}"
                     f"  swing x{_r(bk['kill_order_bonus_x_swing'], bonus)}"
                     f"   -> mean {base_mean:>8.1f}")
            L.append(f"      AFTER   time x{_r(ck['kill_order_bonus_x_time'], bonus)}"
                     f"  x B={B}                     "
                     f"-> {B * ck['kill_order_bonus_x_time']:>8.1f}  to {killer[:16]}")
            traded = ck["death_order_bonus"] / bonus if bonus else 1.0
            L.append(f"      death   traded x{traded:>5.3f}  x B={B}                     "
                     f"    -> {B * ck['death_order_bonus_x_time']:>8.1f}  "
                     f"from {victim[:16]}")

        econ = econ_by_round.get(round_number)
        L.append("")
        if econ is None:
            L.append("  ECONOMY: scores 0 this round -- pistol, halftime, overtime, the")
            L.append("           final round, or outside the scored 2-4/14-16 and")
            L.append("           5-11/17-23 regimes.")
        else:
            L.append("  ECONOMY")
            for team, magnitude in sorted(econ["econ_round_by_team"].items(),
                                          key=lambda kv: str(kv[0])):
                denominator = econ["removed_by_team"].get(team, 0.0)
                L.append(f"      econ_round({str(team)[5:]}) = {magnitude:>7.4f}    "
                         f"removed({str(team)[5:]}) = {denominator:>9.0f} credits "
                         f"of enemy loadout")
                if denominator > 0:
                    credited = sum(p["credit"] for p in econ["players"].values()
                                   if p["team"] == team)
                    rec.close(f"round {round_number} credits sum for {team}",
                              credited, magnitude)
                else:
                    L.append("          removed = 0, so the allocation ABSTAINS: every "
                             "share is 0 and the magnitude is not redistributed")
            L.append(f"      {'player':<18} {'removed':>9} {'lost':>9} "
                     f"{'credit':>9} {'debit':>9} {'net':>9} {'xSCALE':>9} {'xC':>9}")
            for mp, p in sorted(econ["players"].items(), key=lambda kv: -kv[1]["value"]):
                L.append(f"      {names.get(mp, ('?',))[0][:18]:<18} "
                         f"{p['removed']:>9.0f} {p['lost']:>9.0f} "
                         f"{p['credit']:>9.4f} {p['debit']:>9.4f} {p['value']:>9.4f} "
                         f"{p['scaled']:>9.1f} {C * p['scaled']:>9.1f}")

        L.append("")
        L.append(f"  ROUND {round_number} TOTALS")
        L.append(f"      {'player':<18} {'A*damage':>9} {'B*leverage':>11} "
                 f"{'C*econ':>9} {'=impact':>9}   {'kill':>7} {'death':>7}")
        for mp, row in sorted(round_rows.items(), key=lambda kv: -kv[1].impact):
            rec.identity(f"round {round_number} player {mp}", row.impact,
                         [row.damage, row.leverage_component, row.econ_component])
            L.append(f"      {names.get(mp, ('?',))[0][:18]:<18} {row.damage:>9,} "
                     f"{row.leverage_component:>11,} {row.econ_component:>9,} "
                     f"{row.impact:>9,}   {row.kill_impact:>7,} {row.death_impact:>7,}")
            t = match_totals[mp]
            t["damage"] += row.damage
            t["leverage"] += row.leverage_component
            t["econ"] += row.econ_component
            t["impact"] += row.impact
        L.append("")

    L.append("=" * 104)
    L.append("MATCH TOTALS  (each is the sum of that player's round rows above)")
    L.append("=" * 104)
    L.append(f"  {'player':<20} {'A*damage':>10} {'B*leverage':>12} {'C*econ':>10} "
             f"{'=impact':>10}")
    view_totals = _totals(rows)
    for mp, t in sorted(match_totals.items(), key=lambda kv: -kv[1]["impact"]):
        rec.identity(f"match total player {mp}", t["impact"],
                     [t["damage"], t["leverage"], t["econ"]])
        rec.equal(f"trace vs match view player {mp}",
                  t["impact"], view_totals[mp]["impact"])
        L.append(f"  {names.get(mp, ('?',))[0][:20]:<20} {t['damage']:>10,} "
                 f"{t['leverage']:>12,} {t['econ']:>10,} {t['impact']:>10,}")

    Path(out_path).write_text("\n".join(L), encoding="utf-8")
    print(f"\n  trace written to {out_path}  "
          f"({len(L):,} lines, {len(cand_by_key):,} kills, "
          f"{len(econ_by_round):,} rounds with economy)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", type=int)
    parser.add_argument("--ten", action="store_true")
    parser.add_argument("--trace", type=int)
    parser.add_argument("--preplant", action="store_true")
    parser.add_argument("--weights", default=None,
                        help="A,B,C -- default 1.25,1.0,1.0 (the declared candidate)")
    parser.add_argument("--out", default="release_candidate_trace.txt")
    args = parser.parse_args()

    weights = FormulaWeights()
    if args.weights:
        a, b, c = (float(x) for x in args.weights.split(","))
        weights = FormulaWeights(damage=a, leverage=b, econ=c)

    db = SessionLocal()
    print("building the candidate's post-plant table on the full corpus ...")
    table, centering, n = build_candidate_table(db)
    print(f"  {n:,} post-plant kills   c = {centering.c:.6f}   "
          f"death-side residual = {centering.death_side_residual:+.4%}")
    is_declared = (weights.damage, weights.leverage, weights.econ) == (1.25, 1.0, 1.0)
    print(f"  weights: A(damage)={weights.damage}  B(leverage)={weights.leverage}  "
          f"C(econ)={weights.econ}"
          f"   [{'DECLARED CANDIDATE' if is_declared else 'NON-DEFAULT'}]")

    rec = Reconciler()
    undo = apply_preplant_centering() if args.preplant else (lambda: None)
    try:
        if args.match:
            review_match(db, args.match, table, args.preplant, weights, rec)
        if args.ten:
            review_ten(db, FIXED_TEN, table, args.preplant, weights, rec)
        if args.trace:
            trace_match(db, args.trace, table, args.preplant, weights, rec, args.out)
    finally:
        undo()
    rec.report()


if __name__ == "__main__":
    main()
