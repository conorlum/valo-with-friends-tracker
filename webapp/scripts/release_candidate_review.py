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

FROZEN MODE (--manifest PATH). Everything below "THE CANDIDATE" describes the
UNFROZEN legacy review. With a manifest, the configurations are the manifest's
NAMED comparators -- live_legacy, separate_econ_legacy (diagnostic),
buy_disruption_v2_wealth and buy_disruption_v2_30_80 -- verified against this
checkout, with no implicit post-plant table or pre-plant patch:

    ... --manifest M --match 3104 --compare site      # total release change
    ... --manifest M --match 3104 --compare penalty   # wealth vs 30/80 debit only
    ... --manifest M --trace 3104 --report-dir DIR    # per-kill economy trace
    ... --manifest M --ten --report-dir DIR
    ... --manifest M --corpus --report-dir DIR        # predeclared read-only audit
    ... --manifest M --ten --extra 3104 --results R   # backfill acceptance values

TWO CAVEATS ON THE NUMBERS.
  * The post-plant table is fitted on the FULL corpus, deliberately:
    out-of-fold is a rule for EVALUATING a candidate, not for shipping one.
    Do not quote anything here as out-of-fold evidence.
  * With --preplant, Part 3's centring constant c is applied by this script,
    because impact.py still multiplies by the raw curve. Wiring it in is
    outstanding work.
"""
import argparse
import copy
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring import econ_buy_disruption as bd
from app.scoring import econ_component
from app.scoring.impact import FormulaWeights, ImpactInputError, build_impact_rows_for_match
from app.scoring.impact_manifest import (
    config_from_manifest,
    lf_sha256,
    load_manifest,
    match_source_fingerprint,
    verify_manifest,
    verify_source_snapshots,
)
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


# ============================================================================
# FROZEN-MANIFEST MODE (--manifest). Implementation plan sections 3 and 5,
# plan-review findings P2 (named comparators; freeze before review).
#
# Nothing here builds a post-plant table, enables a timing flag or patches a
# module: every configuration comes from the frozen manifest, verified against
# this checkout, and every reviewed match's source rows are checked against
# their frozen fingerprint first. Scores come from build_impact_rows_for_match
# and the econ audit from the production calculator via econ_observer -- the
# same code the runtime and backfill call.
# ============================================================================

NON_ECON_FIELDS = (
    "damage", "leverage_component", "time_impact", "kill_impact", "death_impact",
    "kill_order_bonus", "econ_kill", "econ_death", "clutch_kill", "clutch_death",
    "post_plant_kill", "post_plant_death", "traded_teammate", "traded_by_teammate", "trade_detail",
)


def frozen_pair(manifest, compare):
    """The two NAMED comparators a view contrasts, in (before, after) order."""
    if compare == "site":
        names = manifest["site_comparison"]
    elif compare == "penalty":
        names = manifest["penalty_comparison"]
    elif compare == "separate":
        names = [bd.MODEL_SEPARATE_ECON_LEGACY, manifest["release_comparator"]]
    else:
        raise ValueError(f"unknown comparison {compare!r}")
    return [(name, config_from_manifest(manifest, name)) for name in names]


def score_with(db, match_id, config):
    econ, kills = {}, []
    rows = build_impact_rows_for_match(
        db, match_id,
        kill_observer=lambda **kw: kills.append(kw),
        econ_observer=lambda **kw: econ.__setitem__(kw["round_number"], kw),
        **config.build_kwargs())
    return {"config": config, "rows": rows, "econ": econ, "kills": kills}


def econ_points(scored, round_number, match_player_id):
    """(gross credit, gross debit) in C-weighted econ points, unrounded."""
    config = scored["config"]
    scale = config.weights.econ * econ_component.ECON_SCALE
    audit = scored["econ"].get(round_number)
    if audit is None:
        return 0.0, 0.0
    if "result" in audit:
        ledger = audit["result"].players.get(match_player_id)
        return (scale * ledger.credit, scale * ledger.debit) if ledger else (0.0, 0.0)
    player = audit["players"].get(match_player_id)
    return (scale * player["credit"], scale * player["debit"]) if player else (0.0, 0.0)


def reconcile_scores(rec, label, scored, round_numbers):
    config = scored["config"]
    for row in scored["rows"]:
        tag = f"{label} round {round_numbers[row.round_id]} player {row.match_player_id}"
        if config.enable_econ_component:
            # Four terms: D*assists is its own term. The trade credit is NOT a
            # fifth one -- it is inside leverage_component, and is shown there.
            rec.identity(tag, row.impact, [row.damage, row.leverage_component, row.econ_component,
                                           row.assists_component])
    if config.econ_model not in bd.BUY_DISRUPTION_MODELS:
        return
    for row in scored["rows"]:
        rn = round_numbers[row.round_id]
        raw = scored["econ"][rn]["result"].raw_net_by_player().get(row.match_player_id, 0.0)
        rec.equal(f"{label} round {rn} player {row.match_player_id} econ == round(C*S*raw)",
                  row.econ_component,
                  round(config.weights.econ * (econ_component.ECON_SCALE * raw)))
    for rn, kw in scored["econ"].items():
        result = kw["result"]
        if result.abstention:
            continue
        for team, audit in result.teams.items():
            rec.close(f"{label} round {rn} {team} credit == its enemy events", audit.credit,
                      sum(e.credit for e in result.events if e.kind == "enemy" and e.killer_team == team),
                      tolerance=1e-9)
            rec.close(f"{label} round {rn} {team} debit == its victims' event debits", audit.debit,
                      sum(e.victim_debit for e in result.events if e.victim_team == team), tolerance=1e-9)


def reconcile_penalty_pair(rec, left, right):
    """The penalty-only comparison: identical gross credits and non-econ terms."""
    rec.equal("penalty pair row count", len(left["rows"]), len(right["rows"]))
    for l_row, r_row in zip(left["rows"], right["rows"]):
        key = (l_row.round_id, l_row.match_player_id)
        rec.equal(f"penalty pair row order {key}", key, (r_row.round_id, r_row.match_player_id))
        for field in NON_ECON_FIELDS:
            rec.equal(f"penalty pair {field} {key}", getattr(l_row, field), getattr(r_row, field))
    for rn, l_kw in left["econ"].items():
        l_result, r_result = l_kw["result"], right["econ"][rn]["result"]
        rec.equal(f"penalty pair round {rn} abstention", l_result.abstention, r_result.abstention)
        for pid, ledger in l_result.players.items():
            rec.equal(f"penalty pair round {rn} player {pid} gross credit",
                      ledger.credit, r_result.players[pid].credit)


def _blank_side():
    # `credit`/`debit` are gross ECONOMY points; `trade` is the trade credit,
    # which already sits inside `leverage` and is broken out only for display.
    return dict(impact=0, damage=0, leverage=0, econ=0, assists=0, trade=0, credit=0.0, debit=0.0, rounds=0)


def _stored_rows(db, match_id):
    """{(round_id, match_player_id): stored impact} -- what the site shows today."""
    return {
        (r["round_id"], r["match_player_id"]): r["impact"]
        for r in db.execute(text(
            "SELECT s.round_id, s.match_player_id, s.impact "
            "FROM impact_scores s JOIN rounds r ON r.id = s.round_id WHERE r.match_id = :m"
        ), {"m": match_id}).mappings()
    }


def player_match_rows(db, match_id, left, right, stored=None):
    """Per-player totals for both replays, plus -- when `stored` is given -- the
    STORED total and its rank, which is the only honest "before" for a
    deployment: the legacy replay is what the current code would compute now,
    not what production actually holds."""
    names, rounds = _names(db, match_id), _round_numbers(db, match_id)
    players = {}
    for side, scored in (("left", left), ("right", right)):
        for row in scored["rows"]:
            name, team = names.get(row.match_player_id, ("?", "?"))
            entry = players.setdefault(row.match_player_id, dict(
                name=name, team=str(team), left=_blank_side(), right=_blank_side(), by_round={}))
            t = entry[side]
            rn = rounds[row.round_id]
            credit, debit = econ_points(scored, rn, row.match_player_id)
            t["impact"] += row.impact
            t["damage"] += row.damage
            t["leverage"] += row.leverage_component
            t["econ"] += row.econ_component
            t["assists"] += row.assists_component
            t["trade"] += row.trade_credit
            t["credit"] += credit
            t["debit"] += debit
            t["rounds"] += 1
            entry["by_round"].setdefault(rn, {})[side] = (row.impact, row.econ_component)
            entry["by_round"][rn]["round_id"] = row.round_id
    for side in ("left", "right"):
        ranked = sorted(players, key=lambda mp: -players[mp][side]["impact"])
        for position, mp in enumerate(ranked, 1):
            players[mp][side]["rank"] = position
    if stored is not None:
        for mp, entry in players.items():
            entry["stored"] = {"impact": stored.get(mp)}
        with_stored = [mp for mp in players if players[mp]["stored"]["impact"] is not None]
        for position, mp in enumerate(sorted(with_stored, key=lambda m: -players[m]["stored"]["impact"]), 1):
            players[mp]["stored"]["rank"] = position
    return players


def _identity_block(manifest, manifest_sha, pair):
    lines = [f"Candidate `{manifest['candidate_id']}`, manifest LF-SHA-256 `{manifest_sha}`.", ""]
    for label, (name, config) in zip(("Left", "Right"), pair):
        kwargs = config.build_kwargs()
        weights = config.weights
        lines.append(f"- **{label}: `{name}`** -- enable_econ_component={kwargs['enable_econ_component']}, "
                     f"econ_model={kwargs['econ_model']}, weights A/B/C/D={weights.damage}/"
                     f"{weights.leverage}/{weights.econ}/{weights.assists}, trade credit "
                     f"{'ON' if config.enable_trade_credit else 'OFF'} (scale {weights.trade_credit_scale}), "
                     f"use_realized_swing={kwargs['use_realized_swing']}, "
                     f"post-plant table OFF, pre-plant curve OFF")
    return lines


def render_match_review(db, match_id, manifest, compare, rec, manifest_sha="(unrecorded)"):
    pair = frozen_pair(manifest, compare)
    (left_name, left_cfg), (right_name, right_cfg) = pair
    left, right = score_with(db, match_id, left_cfg), score_with(db, match_id, right_cfg)
    rounds = _round_numbers(db, match_id)
    reconcile_scores(rec, left_name, left, rounds)
    reconcile_scores(rec, right_name, right, rounds)
    if compare == "penalty":
        reconcile_penalty_pair(rec, left, right)
    site = compare == "site"
    players = player_match_rows(db, match_id, left, right,
                                stored=_stored_totals(db, match_id) if site else None)
    header = _header(db, match_id)
    played = len({row.round_id for row in right["rows"]})

    lines = [f"# Match {match_id}: {header['map_name']} {header['team1_rounds_won']}-"
             f"{header['team2_rounds_won']} -- {left_name} vs {right_name}", ""]
    lines += _identity_block(manifest, manifest_sha, pair)
    if site:
        lines += ["", "This is the TOTAL release change. Formula changes versus live legacy:", ""]
        lines += [f"- {change}" for change in manifest["formula_changes_vs_live_legacy"]]
        unscored = sum(1 for p in players.values() if p["stored"]["impact"] is None)
        stale = sum(1 for p in players.values()
                    if p["stored"]["impact"] is not None and p["stored"]["impact"] != p["left"]["impact"])
        lines += ["", "**Before is what production stores today**, so Change and Rank describe what a visitor "
                      f"will actually see move. The `{left_name}` REPLAY is shown separately as Legacy replay: it "
                      "is what today's legacy code would compute now, which is not what production holds -- it "
                      f"differs from the stored value for {stale} of {len(players)} players"
                      + (f", and {unscored} have no stored rows at all" if unscored else "") + "."]
    elif compare == "penalty":
        lines += ["", "Penalty-only comparison: gross kill credits and every non-econ term are "
                      "reconciled as identical; only the death debit differs."]
    lines += ["", f"Econ points per played round divide by all {played} played rounds, including "
                  "zero-econ boundary rounds. `of which trade credit` is already inside B*leverage; "
                  "impact = A*damage + B*leverage + C*econ + D*assists.", ""]
    if site:
        lines += ["| Player | Team | Before (stored) | After | Change | Rank | A*damage | B*leverage | "
                  "of which trade credit | C*econ | D*assists | Gross credit | Gross debit | Econ / round | "
                  "Legacy replay |",
                  "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    else:
        lines += ["| Player | Team | Before | After | Change | Rank | A*damage | B*leverage | "
                  "of which trade credit | C*econ after | D*assists | Gross credit | Gross debit | "
                  "C*econ before | Econ / round |",
                  "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    team_totals = defaultdict(lambda: dict(before=0, before_missing=0, after=0, econ=0, credit=0.0, debit=0.0))
    for mp, p in sorted(players.items(), key=lambda kv: kv[1]["right"]["rank"]):
        l, r = p["left"], p["right"]
        if right_cfg.enable_econ_component:
            rec.identity(f"{right_name} match {match_id} player {mp} total", r["impact"],
                         [r["damage"], r["leverage"], r["econ"], r["assists"]])
        t = team_totals[p["team"][5:]]
        if site:
            before = p["stored"]["impact"]
            before_cell = f"{before:+,}" if before is not None else "n/a (unscored)"
            change_cell = f"{r['impact'] - before:+,}" if before is not None else "n/a"
            rank_cell = f"{p['stored'].get('rank', 'n/a')}->{r['rank']}"
            tail = f"{r['econ'] / played:+.2f} | {l['impact']:+,}"
            if before is None:
                t["before_missing"] += 1
            else:
                t["before"] += before
        else:
            before_cell, change_cell = f"{l['impact']:+,}", f"{r['impact'] - l['impact']:+,}"
            rank_cell = f"{l['rank']}->{r['rank']}"
            tail = f"{l['econ']:+,} | {r['econ'] / played:+.2f}"
            t["before"] += l["impact"]
        lines.append(f"| {p['name']} | {p['team'][5:]} | {before_cell} | {r['impact']:+,} | {change_cell} | "
                     f"{rank_cell} | {r['damage']:,} | {r['leverage']:+,} | {r['trade']:+,} | {r['econ']:+,} | "
                     f"{r['assists']:+,} | {r['credit']:.2f} | {r['debit']:.2f} | {tail} |")
        t["after"] += r["impact"]
        t["econ"] += r["econ"]
        t["credit"] += r["credit"]
        t["debit"] += r["debit"]
    lines += ["", "| Team | Before | After | C*econ after | Gross credit | Gross debit |",
              "|---|---:|---:|---:|---:|---:|"]
    for team, t in sorted(team_totals.items()):
        before = (f"{t['before']:+,}" + (f" ({t['before_missing']} unscored)" if t["before_missing"] else ""))
        lines.append(f"| {team} | {before} | {t['after']:+,} | {t['econ']:+,} | "
                     f"{t['credit']:.2f} | {t['debit']:.2f} |")

    stored_rounds = _stored_rows(db, match_id) if site else {}
    lines += ["", "## Per-round changes", "",
              ("Each cell: STORED impact -> after impact (C*econ after)." if site
               else "Each cell: impact before -> after (C*econ before -> after)."), ""]
    round_list = sorted({rn for p in players.values() for rn in p["by_round"]})
    lines.append("| Round | " + " | ".join(p["name"][:14] for p in players.values()) + " |")
    lines.append("|---|" + "---|" * len(players))
    for rn in round_list:
        cells = []
        for mp, p in players.items():
            pair_round = p["by_round"].get(rn, {})
            if "left" in pair_round and "right" in pair_round:
                (li, le), (ri, re) = pair_round["left"], pair_round["right"]
                if site:
                    was = stored_rounds.get((pair_round.get("round_id"), mp))
                    cells.append(f"{was:+}->{ri:+} ({re:+})" if was is not None else f"n/a->{ri:+} ({re:+})")
                else:
                    cells.append(f"{li:+}->{ri:+} ({le:+}->{re:+})")
            else:
                cells.append("n/a")
        lines.append(f"| {rn} | " + " | ".join(cells) + " |")

    if right_cfg.econ_model in bd.BUY_DISRUPTION_MODELS:
        lines += ["", "## Economy by round (after)", "",
                  "| Round | Abstention | Team | L lost | H target | U funding | D gap | G observed gap | "
                  "Severity pool | Rate | Next raw < 4200 | Credit pts | Debit pts |",
                  "|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|"]
        scale = right_cfg.weights.econ * econ_component.ECON_SCALE
        for rn in sorted(right["econ"]):
            result = right["econ"][rn]["result"]
            if result.abstention:
                lines.append(f"| {rn} | {result.abstention} | | | | | | | | | | | |")
                continue
            for team, audit in sorted(result.teams.items(), key=lambda kv: str(kv[0])):
                b = audit.budget
                rate = f"{audit.penalty_rate:.0%}" if audit.penalty_rate is not None else "wealth"
                lines.append(f"| {rn} | | {str(team)[5:]} | {b.lost:,.0f} | {b.target:,.0f} | "
                             f"{b.funding:,.0f} | {b.shortfall:,.0f} | {b.observed_gap:,.0f} | "
                             f"{b.severity_pool:,.1f} | {rate} | {audit.next_below_raw_4200} | "
                             f"{scale * audit.credit:.2f} | {scale * audit.debit:.2f} |")
    return "\n".join(lines) + "\n", {"players": players, "left": left, "right": right}


def render_frozen_trace(db, match_id, manifest, rec, compare="site", manifest_sha="(unrecorded)"):
    """Event -> round -> match, for the AFTER comparator, with BEFORE impact
    shown per player-round. Every economy line comes from the calculator's
    audit through econ_observer; every combat line from kill_observer."""
    pair = frozen_pair(manifest, compare)
    (left_name, left_cfg), (right_name, right_cfg) = pair
    left, right = score_with(db, match_id, left_cfg), score_with(db, match_id, right_cfg)
    names, rounds, header = _names(db, match_id), _round_numbers(db, match_id), _header(db, match_id)
    reconcile_scores(rec, right_name, right, rounds)
    scale = right_cfg.weights.econ * econ_component.ECON_SCALE
    A, B, C = right_cfg.weights.damage, right_cfg.weights.leverage, right_cfg.weights.econ
    D = right_cfg.weights.assists
    site = compare == "site"
    stored_rounds = _stored_rows(db, match_id) if site else {}

    def who(mp):
        return names.get(mp, ("unknown/environment",))[0] if mp is not None else "environment"

    left_rows = {(r.round_id, r.match_player_id): r for r in left["rows"]}
    rows_by_round = defaultdict(dict)
    for row in right["rows"]:
        rows_by_round[rounds[row.round_id]][row.match_player_id] = row
    kills_by_round = defaultdict(list)
    for kw in right["kills"]:
        kills_by_round[kw["round_number"]].append(kw)

    L = [f"# Per-kill trace: match {match_id} {header['map_name']} {header['team1_rounds_won']}-"
         f"{header['team2_rounds_won']}", ""]
    L += _identity_block(manifest, manifest_sha, pair)
    L += ["", f"impact = A*damage + B*leverage + C*econ + D*assists with A={A}, B={B}, C={C}, D={D}; "
              f"econ points = C * {econ_component.ECON_SCALE} * raw, rounded ONCE per player-round. "
              + (f"Trade credit is ON (scale {right_cfg.weights.trade_credit_scale}): a traded player's share "
                 "of the trade kill's leverage is inside B*leverage and shown separately as `trade credit`."
                 if right_cfg.enable_trade_credit else "Trade credit is OFF."),
          "Kill credit = small equipment value + allocated buy-disruption value. Death debit = 30% of the "
          "victim's damage value when the team's funding absorbed the loss, 80% when its severity pool "
          "is positive (constrained next buy). Repeated deaths expose no new kit."
          + (" Under the round 2/14 bonus-round denial, a pistol winner's qualifying death instead pays "
             "factor x 1.10 x net denied kit to the killer and 80% of that as the victim's debit; "
             "that team's 30/80 budget does not apply." if right_cfg.econ_model == bd.MODEL_V2_30_80_BONUS_DENIAL
             else ""), ""]
    match_totals = defaultdict(lambda: dict(before=0, before_missing=0, replay=0, damage=0, leverage=0,
                                            trade=0, econ=0, assists=0, impact=0, credit=0.0, debit=0.0,
                                            rounds=0))
    for rn in sorted(rows_by_round):
        audit_kw = right["econ"].get(rn)
        result = audit_kw["result"] if audit_kw and "result" in audit_kw else None
        events = {e.event_id: e for e in result.events} if result is not None else {}
        L += [f"## Round {rn}", ""]
        if result is not None and result.abstention:
            L += [f"Economy abstains: **{result.abstention}**"
                  + (f" ({result.abstention_detail})" if result.abstention_detail else "")
                  + " -- econ is exactly 0 this round.", ""]
        elif result is not None:
            first = next(iter(result.teams.values()))
            L += [f"Pistol winner {'/'.join(str(t)[5:] for t, a in result.teams.items() if a.pistol_winner)}; "
                  f"round winner {'/'.join(str(t)[5:] for t, a in result.teams.items() if a.round_winner) or 'none'}; "
                  f"half round {first.half_round}.", ""]
            for team, audit in sorted(result.teams.items(), key=lambda kv: str(kv[0])):
                bonus_audit = getattr(audit, "bonus", None)
                if bonus_audit is not None:
                    # The bonus-denial ledger, not the 30/80 budget, scores this team's deaths.
                    L.append(f"**{str(team)[5:]}** BONUS-ROUND DENIAL (pistol winner "
                             f"{'won' if bonus_audit.won else 'lost'} this round, factor {bonus_audit.factor}): "
                             f"denied {sum(bonus_audit.denied.values()):,.0f}; recovered "
                             f"{bonus_audit.team_recovered:,.0f}; net {sum(bonus_audit.net_denied.values()):,.0f}.")
                    for survivor in bonus_audit.survivors:
                        if survivor.recovery:
                            feed = (f" {survivor.feed_inference} {survivor.feed_weapon} over {survivor.own_weapon}"
                                    if survivor.feed_inference else "")
                            L.append(f"- {who(survivor.match_player_id)} recovered {survivor.recovery:,.0f} "
                                     f"(credit {survivor.credit_recovery:,.0f}; kill feed "
                                     f"{survivor.feed_recovery:,.0f}{feed}; inferred)")
                else:
                    b = audit.budget
                    verdict = ("funding ABSORBED its losses (30% death debits)" if audit.penalty_rate == 0.30
                               else "CONSTRAINED next buy (80% death debits)" if audit.penalty_rate == 0.80
                               else "wealth-debit comparator")
                    L.append(f"**{str(team)[5:]}** lost L={b.lost:,.0f}; target H={b.target:,.0f}"
                             f"{' (carryover targets)' if audit.carryover_targets else ''}; funding U={b.funding:,.0f}; "
                             f"gap D={b.shortfall:,.0f}; observed next-equipment gap G={b.observed_gap:,.0f}; "
                             f"activation {b.activation:.4f}; severity pool {b.severity_pool:,.2f} -> {verdict}.")
                L += ["", "| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target |",
                      "|---|---:|---:|---:|---:|---:|---:|"]
                for pid, ledger in sorted(result.players.items()):
                    if ledger.team == team:
                        L.append(f"| {who(pid)} | {ledger.current_paid:,.0f} | {ledger.lost:,.0f} | "
                                 f"{ledger.next_loadout:,.0f} | {ledger.next_paid:,.0f} | "
                                 f"{ledger.next_bank:,.0f} | {ledger.target:,.0f} |")
                L.append("")
        L += ["| Event | Time | Killer -> victim | Kind | State | Kill-order bonus | Time x | "
              "B*leverage to killer | B*death to victim | Exposure | Small credit | Disruption credit | "
              "Killer econ credit | Victim econ debit (rate) |",
              "|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for kw in kills_by_round.get(rn, []):
            kill, ctx = kw["kill"], kw["context"]
            bonus = ctx["kill_order_bonus_raw"]
            econ_event = events.get(kill["id"])
            kind = econ_event.kind if econ_event else ("environment" if ctx.get("environmental") else
                                                      "self" if ctx["self_kill"] else "combat")
            time_x = kill["kill_order_bonus_x_time"] / bonus if bonus and not ctx["self_kill"] else 1.0
            if econ_event is not None:
                rate = f"{econ_event.penalty_rate:.0%}" if econ_event.penalty_rate is not None else "wealth"
                state = ("denial" if getattr(econ_event, "bonus_qualifying", False)
                         else "absorbed" if econ_event.absorbed else "constrained")
                econ_cells = (f"{econ_event.exposure:,.0f} | {scale * econ_event.background_credit:.2f} | "
                              f"{scale * econ_event.disruption_credit:.2f} | {scale * econ_event.credit:.2f} | "
                              f"{scale * econ_event.victim_debit:.2f} ({rate}, {state})")
            else:
                econ_cells = "| | | | no economy this round"
            L.append(f"| event {kill['id']} | {kill['event_time_seconds']:.3f}s | "
                     f"{who(kill['killer_match_player_id'])} -> {who(kill['death_match_player_id'])} | {kind} | "
                     f"{ctx['killer_team_alive']}v{ctx['victim_team_alive']} | {bonus:,.0f} | {time_x:.3f} | "
                     f"{B * kill['kill_order_bonus_x_time']:.1f} | {B * kill['death_order_bonus_x_time']:.1f} | "
                     f"{econ_cells} |")
        before_label = "Stored impact" if site else "Before impact"
        L += ["", f"| Player | {before_label} | A*damage | B*leverage | of which trade credit | "
                  "Econ credit pts | Econ debit pts | C*econ | D*assists | = After impact |"
              + (" Legacy replay |" if site else ""),
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|" + ("---:|" if site else "")]
        for mp, row in sorted(rows_by_round[rn].items(), key=lambda kv: -kv[1].impact):
            credit, debit = econ_points(right, rn, mp)
            replay_row = left_rows.get((row.round_id, mp))
            replay_impact = replay_row.impact if replay_row is not None else 0
            t = match_totals[mp]
            if site:
                stored_impact = stored_rounds.get((row.round_id, mp))
                before_cell = f"{stored_impact:+,}" if stored_impact is not None else "n/a"
                if stored_impact is None:
                    t["before_missing"] += 1
                else:
                    t["before"] += stored_impact
            else:
                before_cell = f"{replay_impact:+,}"
                t["before"] += replay_impact
            L.append(f"| {who(mp)} | {before_cell} | {row.damage:,} | {row.leverage_component:+,} | "
                     f"{row.trade_credit:+,} | {credit:.2f} | {debit:.2f} | {row.econ_component:+,} | "
                     f"{row.assists_component:+,} | {row.impact:+,} |"
                     + (f" {replay_impact:+,} |" if site else ""))
            t["replay"] += replay_impact
            t["damage"] += row.damage
            t["leverage"] += row.leverage_component
            t["trade"] += row.trade_credit
            t["econ"] += row.econ_component
            t["assists"] += row.assists_component
            t["impact"] += row.impact
            t["credit"] += credit
            t["debit"] += debit
            t["rounds"] += 1
        L.append("")

    L += ["## Match totals", "",
          f"| Player | {'Stored' if site else 'Before'} | A*damage | B*leverage | of which trade credit | "
          "Gross econ credit | Gross econ debit | C*econ | D*assists | Econ / played round | After |"
          + (" Legacy replay |" if site else ""),
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|" + ("---:|" if site else "")]
    view = _totals(right["rows"])
    for mp, t in sorted(match_totals.items(), key=lambda kv: -kv[1]["impact"]):
        rec.identity(f"trace match total player {mp}", t["impact"],
                     [t["damage"], t["leverage"], t["econ"], t["assists"]])
        rec.equal(f"trace vs match view player {mp}", t["impact"], view[mp]["impact"])
        before = (f"{t['before']:+,}" + (f" ({t['before_missing']} rounds unscored)"
                                          if t["before_missing"] else ""))
        L.append(f"| {who(mp)} | {before} | {t['damage']:,} | {t['leverage']:+,} | {t['trade']:+,} | "
                 f"{t['credit']:.2f} | {t['debit']:.2f} | {t['econ']:+,} | {t['assists']:+,} | "
                 f"{t['econ'] / t['rounds']:+.2f} | {t['impact']:+,} |"
                 + (f" {t['replay']:+,} |" if site else ""))
    return "\n".join(L) + "\n"


def pistol_winner_round_two_losses(db, exclude=()):
    """Matches where a pistol winner LOST round 2 or 14, selected from round
    OUTCOMES only (never from scores), most recent first."""
    rows = db.execute(text(
        "SELECT r.match_id, r.round_number, r.outcome, m.played_at FROM rounds r "
        "JOIN matches m ON m.id = r.match_id WHERE r.round_number IN (1, 2, 3, 13, 14, 15)"
    )).all()
    by_match = defaultdict(dict)
    played = {}
    for match_id, number, outcome, played_at in rows:
        by_match[match_id][number] = outcome
        played[match_id] = played_at

    def winner(outcome):
        if not outcome or "Surrendered" in outcome:
            return None
        return "A" if outcome.startswith("Team A") else "B" if outcome.startswith("Team B") else None

    found = []
    for match_id, outcomes in by_match.items():
        if match_id in exclude:
            continue
        for pistol, second in ((1, 2), (13, 14)):
            p, s = winner(outcomes.get(pistol)), winner(outcomes.get(second))
            if p and s and p != s and winner(outcomes.get(second + 1)):
                found.append(dict(match_id=match_id, round=second, played_at=str(played[match_id])))
    found.sort(key=lambda f: (f["played_at"], f["match_id"]), reverse=True)
    return found


def build_review_results(db, manifest_path, manifest, match_ids):
    from scripts.backfill_impact_candidate import RESULT_FIELDS, result_rows

    config = config_from_manifest(manifest)
    return {
        "manifest_lf_sha256": lf_sha256(manifest_path),
        "candidate_id": manifest["candidate_id"],
        "release_comparator": manifest["release_comparator"],
        # Derived from the list the rows are actually written in. It used to be
        # a hand-typed six-field list while every row carried all the persisted
        # fields in a different order, so a reader taking the metadata at its
        # word read kill_impact as impact.
        "fields": list(RESULT_FIELDS),
        "matches": {
            str(match_id): {
                "source_fingerprint": match_source_fingerprint(db, match_id),
                "rows": result_rows(build_impact_rows_for_match(db, match_id, **config.build_kwargs())),
            }
            for match_id in match_ids
        },
    }


def render_ten_review(db, match_ids, manifest, compare, rec, manifest_sha="(unrecorded)"):
    pair = frozen_pair(manifest, compare)
    (left_name, _), (right_name, right_cfg) = pair
    lines = [f"# Fixed ten-match review: {left_name} vs {right_name}", ""]
    lines += _identity_block(manifest, manifest_sha, pair)
    lines += ["", f"Matches (pinned): {', '.join(str(m) for m in match_ids)}", ""]
    deltas, econ_values, rank_moves, total_players = [], [], 0, 0
    reasons, rates = defaultdict(int), defaultdict(int)
    histories = pistol_winner_round_two_losses(db)
    history_by_match = defaultdict(list)
    for h in histories:
        history_by_match[h["match_id"]].append(h["round"])
    site = compare == "site"
    unscored_players = 0
    for match_id in match_ids:
        text_block, data = render_match_review(db, match_id, manifest, compare, rec, manifest_sha)
        players = data["players"]
        header = _header(db, match_id)
        if site:
            # Against what production STORES, not against a replay of the legacy code.
            compared = [p for p in players.values() if p["stored"]["impact"] is not None]
            unscored_players += len(players) - len(compared)
            moved = sum(1 for p in compared if p["stored"]["rank"] != p["right"]["rank"])
            total_players += len(compared)
        else:
            moved = sum(1 for p in players.values() if p["left"]["rank"] != p["right"]["rank"])
            total_players += len(players)
        rank_moves += moved
        right = data["right"]
        for kw in right["econ"].values():
            if "result" in kw:
                result = kw["result"]
                reasons[result.abstention or "scored"] += 1
                for audit in result.teams.values():
                    if audit.budget.lost > 0 and audit.penalty_rate is not None:
                        rates[f"{audit.penalty_rate:.0%}"] += 1
        history = history_by_match.get(match_id)
        lines += [f"## Match {match_id}: {header['map_name']} {header['team1_rounds_won']}-"
                  f"{header['team2_rounds_won']} -- {moved}/{len(players)} players change rank"
                  + (f"; pistol winner lost round(s) {history}" if history else ""), "",
                  f"| Player | Team | {'Before (stored)' if site else 'Before'} | After | Change | Rank | "
                  "C*econ | D*assists | of which trade credit | Gross credit | Gross debit |",
                  "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|"]
        for mp, p in sorted(players.items(), key=lambda kv: kv[1]["right"]["rank"]):
            l, r = p["left"], p["right"]
            econ_values.append(r["econ"])
            if site:
                before = p["stored"]["impact"]
                if before is None:
                    before_cell, change_cell, rank_cell = "n/a (unscored)", "n/a", f"n/a->{r['rank']}"
                else:
                    deltas.append(r["impact"] - before)
                    before_cell, change_cell = f"{before:+,}", f"{r['impact'] - before:+,}"
                    rank_cell = f"{p['stored']['rank']}->{r['rank']}"
            else:
                deltas.append(r["impact"] - l["impact"])
                before_cell, change_cell = f"{l['impact']:+,}", f"{r['impact'] - l['impact']:+,}"
                rank_cell = f"{l['rank']}->{r['rank']}"
            lines.append(f"| {p['name']} | {p['team'][5:]} | {before_cell} | {r['impact']:+,} | "
                         f"{change_cell} | {rank_cell} | {r['econ']:+,} | {r['assists']:+,} | "
                         f"{r['trade']:+,} | {r['credit']:.2f} | {r['debit']:.2f} |")
        lines.append("")
    deltas.sort()
    econ_values.sort()

    def at(values, q):
        return values[min(len(values) - 1, int(len(values) * q))]

    lines += ["## Across all ten", "",
              (f"- compared against STORED production values; {unscored_players} player-matches have no "
               "stored rows and are excluded from Change and Rank" if site else
               "- compared against the named Before comparator's replay"),
              f"- player-matches: {len(deltas)}; rank changes {rank_moves} ({100 * rank_moves / total_players:.1f}%)",
              f"- full-Impact change: mean {statistics.mean(deltas):+.1f}, median {statistics.median(deltas):+.1f}, "
              f"SD {statistics.pstdev(deltas):.1f}, p5 {at(deltas, .05):+}, p95 {at(deltas, .95):+}, "
              f"min {deltas[0]:+}, max {deltas[-1]:+}",
              f"- match C*econ per player: mean {statistics.mean(econ_values):+.1f}, median "
              f"{statistics.median(econ_values):+.1f}, min {econ_values[0]:+}, max {econ_values[-1]:+}, "
              f"negative {sum(v < 0 for v in econ_values)}, zero {sum(v == 0 for v in econ_values)}",
              f"- economy rounds by outcome: {dict(sorted(reasons.items()))}",
              f"- team-rounds with lost equipment by death-penalty rate: {dict(sorted(rates.items()))}",
              f"- pistol-winner-loses-round-2/14 histories present: "
              f"{ {m: history_by_match[m] for m in match_ids if m in history_by_match} }"]
    return "\n".join(lines) + "\n"


def _quantiles(values):
    values = sorted(values)
    if not values:
        return {}

    def at(q):
        return values[min(len(values) - 1, int(len(values) * q))]

    return dict(n=len(values), mean=statistics.fmean(values), sd=statistics.pstdev(values),
                min=values[0], p1=at(.01), p5=at(.05), p50=at(.5), p95=at(.95), p99=at(.99), max=values[-1],
                negative=sum(v < 0 for v in values), zero=sum(v == 0 for v in values),
                positive=sum(v > 0 for v in values))


def _spearman(xs, ys):
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return cov / ((sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5)


def corpus_audit(db, manifest, min_matches=20, match_ids=None, progress=None):
    """The predeclared read-only corpus checks. Descriptive: nothing is fitted
    or tuned from these numbers."""
    live_cfg = config_from_manifest(manifest, "live_legacy")
    wealth_cfg = config_from_manifest(manifest, bd.MODEL_V2_WEALTH)
    release_cfg = config_from_manifest(manifest)
    if match_ids is None:
        match_ids = [m for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()]
    player_of = {mp: pl for mp, pl in db.execute(text("SELECT id, player_id FROM match_players")).all()}
    player_name = {pl: name for pl, name in db.execute(text("SELECT id, display_name FROM players")).all()}

    econ_rows, delta_rows, delta_matches, match_econ = [], [], [], []
    reasons, kinds, rates, signs = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    mismatch = defaultdict(int)
    failures = {}
    rank_changes = players_seen = 0
    leader = defaultdict(lambda: dict(live=0, release=0, rounds=0, matches=0))
    stale_player_matches = 0
    for index, match_id in enumerate(match_ids):
        if progress and index % 250 == 0:
            progress(index, len(match_ids))
        try:
            live = build_impact_rows_for_match(db, match_id, **live_cfg.build_kwargs())
            wealth = score_with(db, match_id, wealth_cfg)
            release = score_with(db, match_id, release_cfg)
        except ImpactInputError as exc:
            failures[str(match_id)] = str(exc)
            continue
        rounds = {r.round_id for r in release["rows"]}
        round_numbers = {rid: rn for rid, rn in db.execute(
            text("SELECT id, round_number FROM rounds WHERE match_id = :m"), {"m": match_id}).all()}
        live_by_key = {(r.round_id, r.match_player_id): r for r in live}
        per_player = defaultdict(lambda: dict(live=0, release=0, econ=0, rounds=0))
        for row, w_row in zip(release["rows"], wealth["rows"]):
            key = (row.round_id, row.match_player_id)
            rn = round_numbers[row.round_id]
            econ_rows.append(row.econ_component)
            live_row = live_by_key[key]
            delta_rows.append(row.impact - live_row.impact)
            raw = release["econ"][rn]["result"].raw_net_by_player().get(row.match_player_id, 0.0)
            if row.econ_component != round(release_cfg.weights.econ * (econ_component.ECON_SCALE * raw)):
                mismatch["econ_row_vs_calculator"] += 1
            if row.impact != (row.damage + row.leverage_component + row.econ_component
                              + row.assists_component):
                mismatch["impact_identity"] += 1
            for field in NON_ECON_FIELDS:
                if getattr(row, field) != getattr(w_row, field):
                    mismatch[f"non_econ_{field}_wealth_vs_release"] += 1
            w_ledger = wealth["econ"][rn]["result"].players.get(row.match_player_id)
            r_ledger = release["econ"][rn]["result"].players.get(row.match_player_id)
            if (w_ledger.credit if w_ledger else 0.0) != (r_ledger.credit if r_ledger else 0.0):
                mismatch["gross_credit_wealth_vs_release"] += 1
            p = per_player[row.match_player_id]
            p["live"] += live_row.impact
            p["release"] += row.impact
            p["econ"] += row.econ_component
            p["rounds"] += 1
        for rn, kw in release["econ"].items():
            result = kw["result"]
            reasons[result.abstention or "scored"] += 1
            if result.abstention:
                continue
            for event in result.events:
                kinds[event.kind] += 1
            nets = []
            for team, audit in result.teams.items():
                if audit.budget.lost > 0:
                    rates[f"{audit.penalty_rate:.0%}"] += 1
                nets.append(audit.credit - audit.debit)
            if all(n > 1e-10 for n in nets):
                signs["both_positive"] += 1
            elif all(n < -1e-10 for n in nets):
                signs["both_negative"] += 1
            elif all(abs(n) <= 1e-10 for n in nets):
                signs["both_zero"] += 1
            else:
                signs["mixed"] += 1
        stored = _stored_totals(db, match_id)
        before_rank = sorted(per_player, key=lambda mp: -per_player[mp]["live"])
        after_rank = sorted(per_player, key=lambda mp: -per_player[mp]["release"])
        for mp, p in per_player.items():
            players_seen += 1
            if before_rank.index(mp) != after_rank.index(mp):
                rank_changes += 1
            if stored.get(mp) is not None and stored[mp] != p["live"]:
                stale_player_matches += 1
            delta_matches.append((p["release"] - p["live"], match_id, player_name.get(player_of.get(mp), "?"),
                                  p["live"], p["release"], p["econ"]))
            match_econ.append(p["econ"])
            lb = leader[player_of[mp]]
            lb["live"] += p["live"]
            lb["release"] += p["release"]
            lb["rounds"] += p["rounds"]
            lb["matches"] += 1
    delta_matches.sort()
    eligible = {pl: v for pl, v in leader.items() if v["matches"] >= min_matches}
    ids = sorted(eligible)
    live_avg = [eligible[pl]["live"] / eligible[pl]["rounds"] for pl in ids]
    release_avg = [eligible[pl]["release"] / eligible[pl]["rounds"] for pl in ids]
    live_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -live_avg[i])]
    release_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -release_avg[i])]
    moves = sorted(((release_order.index(pl) - live_order.index(pl), pl) for pl in ids),
                   key=lambda m: -abs(m[0]))
    return {
        "matches_requested": len(match_ids),
        "matches_scored": len(match_ids) - len(failures),
        "input_validation_failures": failures,
        "player_rounds": len(econ_rows),
        "econ_rounds_by_outcome": dict(sorted(reasons.items())),
        "event_kinds_in_scored_rounds": dict(sorted(kinds.items())),
        "team_rounds_with_losses_by_rate": dict(sorted(rates.items())),
        "scored_round_team_net_signs": dict(sorted(signs.items())),
        "identity_mismatches": dict(mismatch),
        "econ_component_player_round": _quantiles(econ_rows),
        "econ_component_player_match": _quantiles(match_econ),
        "full_impact_change_player_round": _quantiles(delta_rows),
        "full_impact_change_player_match": _quantiles([d[0] for d in delta_matches]),
        "largest_player_match_decreases": [list(d) for d in delta_matches[:10]],
        "largest_player_match_increases": [list(d) for d in delta_matches[-10:][::-1]],
        "within_match_rank_changes": [rank_changes, players_seen],
        "persisted_vs_live_replay_differing_player_matches": [stale_player_matches, players_seen],
        "leaderboard": {
            "min_matches": min_matches, "players": len(ids),
            "spearman_avg_impact_per_round": _spearman(live_avg, release_avg) if len(ids) > 2 else None,
            "top20_overlap": len(set(live_order[:20]) & set(release_order[:20])),
            "largest_rank_moves": [[player_name.get(pl, "?"), live_order.index(pl) + 1,
                                    release_order.index(pl) + 1] for _, pl in moves[:15]],
        },
    }


def render_corpus_audit(audit, manifest, manifest_sha):
    q = audit["econ_component_player_round"]
    lines = [f"# Corpus audit: live_legacy vs {manifest['release_comparator']}", "",
             f"Candidate `{manifest['candidate_id']}`, manifest LF-SHA-256 `{manifest_sha}`. Read-only, "
             "one repeatable-read snapshot. Descriptive checks declared before this run; nothing is fitted.", "",
             "```json", json.dumps(audit, indent=2, default=str), "```", "",
             f"Player-round C*econ: mean {q.get('mean', 0):+.2f}, SD {q.get('sd', 0):.2f}, "
             f"p1 {q.get('p1')}, p50 {q.get('p50')}, p99 {q.get('p99')}."]
    return "\n".join(lines) + "\n"


def _emit(out_dir, filename, content):
    if out_dir is None:
        print(content)
        return
    path = Path(out_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  wrote {path}")


def register_corpus_failures(rec, audit):
    """The corpus audit's own checks must reach the exit status, not sit in
    the JSON while the command reports success."""
    rec.equal("corpus identity mismatches", dict(audit.get("identity_mismatches") or {}), {})
    rec.equal("corpus input validation failures", dict(audit.get("input_validation_failures") or {}), {})
    rec.equal("corpus matches scored", audit.get("matches_scored"), audit.get("matches_requested"))


def apply_weight_override(manifest, manifest_sha, spec):
    """--weights makes the run something other than the frozen candidate, so
    the reported identity must say so rather than carrying the file's hash
    alone into artifacts."""
    a, b, c = (float(x) for x in spec.split(","))
    # --weights only knows A, B and C. A comparator the review actually uses that
    # carries D or the trade credit would be reviewed with an A/B/C that nobody
    # declared alongside a D and credit that --weights cannot express, so refuse.
    # Comparators the review never uses are left alone, and every comparator
    # keeps its own D and credit scale rather than having them dropped to
    # defaults by a weights dict that simply omits them.
    used = (set(manifest.get("site_comparison") or []) | set(manifest.get("penalty_comparison") or [])
            | set(manifest.get("diagnostic_comparators") or []) | {manifest.get("release_comparator")})
    for name in sorted(n for n in used if n in manifest["comparators"]):
        data = manifest["comparators"][name]
        if (data.get("weights") or {}).get("assists", 0.0) or data.get("enable_trade_credit"):
            raise SystemExit(
                f"--weights cannot override comparator {name!r}: it carries D (assists) or the trade "
                "credit, which --weights cannot express. Freeze a new candidate instead.")
    overridden = copy.deepcopy(manifest)
    for data in overridden["comparators"].values():
        data["weights"] = {**(data.get("weights") or {}), "damage": a, "leverage": b, "econ": c}
    overridden["candidate_id"] = f"{manifest['candidate_id']} WEIGHTS-OVERRIDE {a},{b},{c}"
    overridden["formula_changes_vs_live_legacy"] = [
        change for change in manifest["formula_changes_vs_live_legacy"]
        if not change.startswith("weights:")
    ] + [f"weights: OVERRIDDEN at review time -- A(damage)={a}, B(leverage)={b}, C(econ)={c}"]
    return overridden, (f"{manifest_sha} with --weights {a},{b},{c}: "
                        "NOT THE FROZEN CANDIDATE")


def frozen_main(args):
    manifest = load_manifest(args.manifest)
    verify_manifest(manifest)
    manifest_sha = lf_sha256(args.manifest)
    identity = manifest_sha
    if args.weights:
        if args.results:
            raise SystemExit(
                "--results writes the backfill's acceptance values and cannot be combined with "
                "--weights; freeze a new candidate instead")
        manifest, identity = apply_weight_override(manifest, manifest_sha, args.weights)
        print(f"  *** {identity} ***")
    print(f"frozen candidate {manifest['candidate_id']}  manifest LF-SHA-256 {identity}")
    db = SessionLocal()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    rec = Reconciler()
    reviewed = sorted({m for m in (args.match, args.trace) if m} | (set(FIXED_TEN) if args.ten else set())
                      | set(args.extra))
    if reviewed:
        verify_source_snapshots(db, manifest, reviewed)
        print(f"  source fingerprints match the freeze for {len(reviewed)} matches")
    suffix = args.compare
    if args.match:
        content, _ = render_match_review(db, args.match, manifest, args.compare, rec, identity)
        _emit(args.report_dir, f"match-{args.match}-{suffix}.md", content)
    if args.trace:
        _emit(args.report_dir, f"trace-{args.trace}-{suffix}.md",
              render_frozen_trace(db, args.trace, manifest, rec, args.compare, identity))
    if args.ten:
        _emit(args.report_dir, f"fixed-ten-{suffix}.md",
              render_ten_review(db, FIXED_TEN, manifest, args.compare, rec, identity))
    if args.pistol_examples:
        for found in pistol_winner_round_two_losses(db)[:20]:
            print(f"  match {found['match_id']} round {found['round']} played {found['played_at']}")
    if args.corpus:
        audit = corpus_audit(db, manifest, progress=lambda i, n: print(f"  corpus {i}/{n}", flush=True))
        audit["snapshot"] = db.execute(text("SELECT txid_current_snapshot()::text")).scalar()
        register_corpus_failures(rec, audit)
        _emit(args.report_dir, "corpus-audit.json", json.dumps(audit, indent=2, default=str))
        _emit(args.report_dir, "corpus-audit.md", render_corpus_audit(audit, manifest, identity))
    if args.results:
        results = build_review_results(db, args.manifest, manifest, reviewed)
        Path(args.results).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  wrote review results for {len(reviewed)} matches to {args.results}")
    db.rollback()
    rec.report()
    return 1 if rec.failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", type=int)
    parser.add_argument("--ten", action="store_true")
    parser.add_argument("--trace", type=int)
    parser.add_argument("--preplant", action="store_true")
    parser.add_argument("--weights", default=None,
                        help="A,B,C -- default 1.25,1.0,1.0 (the declared candidate)")
    parser.add_argument("--out", default="release_candidate_trace.txt")
    parser.add_argument("--manifest", help="frozen candidate manifest: explicit comparators, no implicit timing")
    parser.add_argument("--compare", choices=["site", "penalty", "separate"], default="site")
    parser.add_argument("--report-dir", help="write frozen-mode reports here instead of printing")
    parser.add_argument("--extra", type=int, action="append", default=[],
                        help="additional frozen match ids to fingerprint-check and include in --results")
    parser.add_argument("--corpus", action="store_true", help="frozen mode: predeclared corpus audit")
    parser.add_argument("--pistol-examples", action="store_true")
    parser.add_argument("--results", help="frozen mode: write persisted-field results for backfill acceptance")
    args = parser.parse_args()

    if args.manifest:
        if args.preplant:
            raise SystemExit("--preplant patches a module and cannot be combined with a frozen manifest")
        sys.exit(frozen_main(args))
    print("  NOTE: no --manifest, so this is the UNFROZEN legacy review: it enables post-plant "
          "leverage and rebuilds its table from the current corpus.")

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
