"""Score every match under the current 30/80 candidate and the round 2/14
bonus-denial model from ONE frozen manifest, and report what changes.

Spec: docs/superpowers/specs/2026-09-12-econ-bonus-round-denial-design.md.

Reconciliation (any count is a failure, exit 1):
  * every player-round's non-econ fields are identical between the two models;
  * outside half-round 2, every player-round econ value is identical;
  * in half-round 2, when the bonus model scores, every event whose victim is on
    the pistol-LOSING team has identical credit and victim debit.

Read-only: one repeatable-read snapshot, rolled back. Players are identified by
match_player / player id only, because the repo is public.

    .\\.venv\\Scripts\\python.exe -X utf8 scripts\\compare_econ_models.py --manifest PATH --report-dir DIR
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.scoring import econ_buy_disruption as bd  # noqa: E402
from app.scoring import econ_component  # noqa: E402
from app.scoring.impact import ImpactInputError  # noqa: E402
from app.scoring.impact_manifest import (  # noqa: E402
    config_from_manifest, lf_sha256, load_manifest, verify_manifest,
)
from scripts.release_candidate_review import NON_ECON_FIELDS, _quantiles, _spearman, score_with  # noqa: E402

OLD = bd.MODEL_V2_30_80


def _gross_points(result, scale_c):
    """Credit earned plus debit charged, both teams, in points; 0 when abstaining."""
    if result.abstention:
        return 0.0
    return scale_c * sum(a.credit + a.debit for a in result.teams.values())


def compare(db, manifest, match_ids=None, min_matches=20, progress=None):
    old_cfg = config_from_manifest(manifest, OLD)
    new_cfg = config_from_manifest(manifest)
    scale_c = econ_component.ECON_SCALE * new_cfg.weights.econ
    if match_ids is None:
        match_ids = [m for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()]
    player_of = {mp: pl for mp, pl in db.execute(text("SELECT id, player_id FROM match_players")).all()}

    parity = defaultdict(int)
    failures = {}
    by_rn = defaultdict(lambda: dict(rounds=0, old=0.0, new=0.0))
    abstain = defaultdict(int)
    bonus = defaultdict(float)
    econ_delta_rows, impact_delta_matches, movers = [], [], []
    leader = defaultdict(lambda: dict(old=0, new=0, rounds=0, matches=0))
    rank_changes = players_seen = 0
    for index, match_id in enumerate(match_ids):
        if progress and index % 250 == 0:
            progress(index, len(match_ids))
        try:
            old = score_with(db, match_id, old_cfg)
            new = score_with(db, match_id, new_cfg)
        except ImpactInputError as exc:
            failures[str(match_id)] = str(exc)
            continue
        rn_of = {rid: rn for rid, rn in db.execute(
            text("SELECT id, round_number FROM rounds WHERE match_id = :m"), {"m": match_id}).all()}
        per_player = defaultdict(lambda: dict(old=0, new=0, rounds=0))
        for o_row, n_row in zip(old["rows"], new["rows"]):
            if (o_row.round_id, o_row.match_player_id) != (n_row.round_id, n_row.match_player_id):
                parity["row_order"] += 1
                continue
            rn = rn_of[n_row.round_id]
            for field in NON_ECON_FIELDS:
                if getattr(o_row, field) != getattr(n_row, field):
                    parity[f"non_econ_{field}"] += 1
            if bd.half_round_index(rn) != 2 and o_row.econ_component != n_row.econ_component:
                parity["econ_outside_half_round_2"] += 1
            econ_delta_rows.append(n_row.econ_component - o_row.econ_component)
            p = per_player[n_row.match_player_id]
            p["old"] += o_row.impact
            p["new"] += n_row.impact
            p["rounds"] += 1
        for rn, kw in new["econ"].items():
            n_res, o_res = kw["result"], old["econ"][rn]["result"]
            by_rn[rn]["rounds"] += 1
            by_rn[rn]["old"] += _gross_points(o_res, scale_c)
            by_rn[rn]["new"] += _gross_points(n_res, scale_c)
            if bd.half_round_index(rn) != 2:
                continue
            if n_res.abstention:
                abstain[n_res.abstention] += 1
                if not o_res.abstention:
                    abstain["scored_by_30_80_but_not_bonus"] += 1
                continue
            for o_ev, n_ev in zip(o_res.events, n_res.events):
                if (n_res.teams[n_ev.victim_team].bonus is None
                        and (o_ev.credit, o_ev.victim_debit) != (n_ev.credit, n_ev.victim_debit)):
                    parity["pistol_loser_victim_event"] += 1
            for audit in n_res.teams.values():
                b = audit.bonus
                if b is None:
                    continue
                bonus["team_rounds"] += 1
                bonus["team_rounds_pistol_winner_won"] += b.won
                bonus["qualifying_deaths"] += len(b.denied)
                bonus["denied_credits"] += sum(b.denied.values())
                bonus["net_denied_credits"] += sum(b.net_denied.values())
                for s in b.survivors:
                    bonus["survivors"] += 1
                    bonus["survivors_with_credit_evidence"] += s.credit_recovery > 0
                    bonus["survivors_with_feed_in_round"] += s.feed_inference == "in_round"
                    bonus["survivors_with_feed_carried"] += s.feed_inference == "carried"
                    bonus["survivors_with_both"] += s.credit_recovery > 0 and s.feed_recovery > 0
                    bonus["recovery_credits_offered"] += s.recovery
            bonus["unidentified_weapon_flags"] += sum("unidentified_weapon" in f for f in n_res.data_quality)
        before = sorted(per_player, key=lambda mp: -per_player[mp]["old"])
        after = sorted(per_player, key=lambda mp: -per_player[mp]["new"])
        for mp, p in per_player.items():
            players_seen += 1
            rank_changes += before.index(mp) != after.index(mp)
            impact_delta_matches.append(p["new"] - p["old"])
            movers.append((p["new"] - p["old"], match_id, mp, p["old"], p["new"]))
            lb = leader[player_of[mp]]
            lb["old"] += p["old"]
            lb["new"] += p["new"]
            lb["rounds"] += p["rounds"]
            lb["matches"] += 1
    movers.sort()
    eligible = {pl: v for pl, v in leader.items() if v["matches"] >= min_matches}
    ids = sorted(eligible)
    old_avg = [eligible[pl]["old"] / eligible[pl]["rounds"] for pl in ids]
    new_avg = [eligible[pl]["new"] / eligible[pl]["rounds"] for pl in ids]
    old_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -old_avg[i])]
    new_order = [ids[i] for i in sorted(range(len(ids)), key=lambda i: -new_avg[i])]
    moves = sorted(((new_order.index(pl) - old_order.index(pl), pl) for pl in ids), key=lambda m: -abs(m[0]))
    return {
        "old": OLD, "new": manifest["release_comparator"],
        "matches_requested": len(match_ids), "matches_scored": len(match_ids) - len(failures),
        "input_validation_failures": failures,
        "parity_mismatches": dict(parity),
        "half_round_2_abstentions_bonus_model": dict(sorted(abstain.items())),
        "bonus": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(bonus.items())},
        "by_round_number": {
            str(rn): {"scored_rounds": v["rounds"],
                      "old_gross_points_per_round": round(v["old"] / v["rounds"], 1),
                      "new_gross_points_per_round": round(v["new"] / v["rounds"], 1)}
            for rn, v in sorted(by_rn.items()) if v["rounds"]},
        "econ_change_player_round": _quantiles(econ_delta_rows),
        "impact_change_player_match": _quantiles(impact_delta_matches),
        "largest_player_match_decreases_delta_match_mp_old_new": [list(m) for m in movers[:10]],
        "largest_player_match_increases_delta_match_mp_old_new": [list(m) for m in movers[-10:][::-1]],
        "within_match_rank_changes": [rank_changes, players_seen],
        "leaderboard": {
            "min_matches": min_matches, "players": len(ids),
            "spearman_avg_impact_per_round": _spearman(old_avg, new_avg) if len(ids) > 2 else None,
            "top20_overlap": len(set(old_order[:20]) & set(new_order[:20])),
            "largest_rank_moves_player_id_old_new": [[pl, old_order.index(pl) + 1, new_order.index(pl) + 1]
                                                     for _, pl in moves[:15]],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--match", type=int, action="append")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    verify_manifest(manifest)
    db = SessionLocal()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    report = compare(db, manifest, args.match, progress=lambda i, n: print(f"  {i}/{n}", flush=True))
    report["snapshot"] = db.execute(text("SELECT txid_current_snapshot()::text")).scalar()
    report["manifest_lf_sha256"] = lf_sha256(args.manifest)
    db.rollback()
    out = Path(args.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "model-comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    bad = report["parity_mismatches"] or report["input_validation_failures"]
    print(f"parity mismatches {report['parity_mismatches']}  "
          f"input failures {len(report['input_validation_failures'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
