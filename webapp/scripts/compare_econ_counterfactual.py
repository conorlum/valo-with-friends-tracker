"""Read-only four-arm econ review; no production scoring flags are changed.

Run from webapp: .venv/Scripts/python.exe scripts/compare_econ_counterfactual.py
Writes JSON measurements and a Markdown walkthrough under --out-dir.
The database transaction is REPEATABLE READ, READ ONLY; localhost is required.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.scoring.econ_component import ECON_SCALE, PlayerRemoval, commitment, denial_early
from app.scoring.econ_counterfactual import attribute_candidate
from app.scoring.impact import build_impact_rows_for_match


ARMS = {
    "incumbent": {},
    "independent": {"independent_debit": True},
    "no_floor": {"remove_late_floor": True},
    "independent_no_floor": {"independent_debit": True, "remove_late_floor": True},
}
FIXED_TEN = [3129, 3130, 3131, 3113, 3118, 3121, 3114, 3115, 3116, 3117]
EPSILON = 1e-10


def team_name(team):
    # SQLAlchemy persists Enum member NAMES, while Team.value is "team-1".
    return team.name if hasattr(team, "name") else str(team)


def signs(values):
    signs_ = [0 if abs(x) <= EPSILON else (1 if x > 0 else -1) for x in values]
    if len(signs_) != 2:
        raise ValueError("The declared comparison requires two teams")
    if signs_ == [-1, -1]:
        return "both_negative"
    if signs_ == [1, 1]:
        return "both_positive"
    if signs_ == [0, 0]:
        return "both_zero"
    if set(signs_) == {-1, 1}:
        return "opposite"
    return "one_zero"


def distribution(values):
    values = np.asarray(values, dtype=float)
    return dict(n=len(values), mean=float(values.mean()), sd=float(values.std()),
                **{f"p{p}": float(np.percentile(values, p)) for p in (1, 5, 50, 95, 99)},
                minimum=float(values.min()), maximum=float(values.max()))


def read_snapshot(db, limit):
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    match_ids = list(db.execute(text("SELECT id FROM matches ORDER BY id")).scalars())
    if limit:
        match_ids = match_ids[:limit]
    rounds = {r.id: dict(r._mapping) for r in db.execute(text(
        "SELECT id, match_id, round_number, outcome FROM rounds ORDER BY id"))}
    roster = defaultdict(dict)
    names = {}
    for r in db.execute(text(
        "SELECT mp.id, mp.match_id, mp.team, p.display_name FROM match_players mp "
        "JOIN players p ON p.id=mp.player_id ORDER BY mp.id"
    )).mappings():
        roster[r["match_id"]][r["id"]] = r["team"]
        names[r["id"]] = r["display_name"]
    states = {}
    for r in db.execute(text(
        "SELECT r.match_id, r.round_number, mp.team, count(*) AS n, "
        "count(s.loadout) AS loadout_n, count(s.remaining) AS remaining_n, "
        "sum(s.loadout+s.remaining) AS wealth, sum(s.loadout) AS loadout, "
        "sum(s.remaining) AS bank, "
        "sum(CASE WHEN s.loadout < 4200 THEN 1 ELSE 0 END) AS below "
        "FROM round_player_stats s JOIN rounds r ON r.id=s.round_id "
        "JOIN match_players mp ON mp.id=s.match_player_id "
        "GROUP BY r.match_id,r.round_number,mp.team"
    )).mappings():
        states[(r["match_id"], r["round_number"], r["team"])] = dict(r)
    identity = {
        "database_is_local": True, "snapshot": db.execute(text("SELECT txid_current_snapshot()::text")).scalar(),
        "transaction_read_only": db.execute(text("SHOW transaction_read_only")).scalar(),
        "matches": len(match_ids), "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip(),
    }
    return match_ids, rounds, roster, names, states, identity


def collect(db, limit, focus):
    match_ids, rounds, rosters, names, states, identity = read_snapshot(db, limit)
    raw, metadata, time_values, other_impact = [], [], [], []
    sign_counts = {a: defaultdict(Counter) for a in ARMS}
    exposure = Counter()
    focus_rounds = []
    max_observer_error = max_incumbent_net = 0.0
    data_hash = hashlib.sha256()
    start = time.monotonic()
    for ordinal, mid in enumerate(match_ids, 1):
        calls = {}
        rows = build_impact_rows_for_match(
            db, mid, use_realized_swing=True, enable_econ_component=True,
            econ_observer=lambda **kw: calls.__setitem__(kw["round_number"], kw),
        )
        values_by_round = {}
        details_by_round = {}
        sizes = Counter(rosters[mid].values())
        if len(sizes) != 2:
            raise ValueError(f"Match {mid}: roster does not contain exactly two teams")
        for rn, call in calls.items():
            removals = [PlayerRemoval(mp, p["team"], p["removed"], p["lost"])
                        for mp, p in call["players"].items()]
            wealth = {}
            team_details = {}
            for team in call["econ_round_by_team"]:
                t = team_name(team)
                nxt = states.get((mid, rn + 1, t))
                complete = nxt is not None and all(nxt[k] == sizes[t] for k in ("n", "loadout_n", "remaining_n"))
                wealth[team] = float(nxt["wealth"]) if complete else None
                members = [p for p in call["players"].values() if p["team"] == team]
                mean_commit = sum(p["committed"] for p in members) / sizes[t]
                loss = sum(p["lost"] for p in members)
                scarcity = denial_early(wealth[team], sizes[t]) if complete else 0.0
                opponent = next(x for x in call["econ_round_by_team"] if x != team)
                regime = "early" if rn in (2, 3, 4, 14, 15, 16) else "late"
                exposure[f"{regime}_team_rounds"] += 1
                exposure[f"{regime}_missing_own_next"] += int(not complete)
                exposure["repeated_loss_player_rounds"] += sum(p["lost"] > p["committed"] + EPSILON for p in members)
                if loss > 0:
                    exposure[f"{regime}_team_rounds_lost_positive"] += 1
                    if scarcity > 0:
                        exposure[f"{regime}_scarce_team_rounds_with_loss"] += 1
                        if call["econ_round_by_team"][opponent] == 0:
                            exposure[f"{regime}_scarce_loss_zero_incumbent_debit"] += 1
                    if commitment(mean_commit) == 0:
                        exposure[f"{regime}_saving_proxy_team_rounds_with_loss"] += 1
                        exposure[f"{regime}_saving_proxy_independent_debit_raw"] += scarcity * loss / 19500
                if regime == "late" and complete and nxt["below"] == 0 and loss > 0:
                    exposure["late_full_rebuy_with_loss"] += 1
                    exposure["late_full_rebuy_zero_scarcity_with_loss"] += int(scarcity == 0)
                if regime == "late" and call["removed_by_team"][team] > 0:
                    exposure["late_positive_removal_team_rounds"] += 1
                    if abs(call["econ_round_by_team"][team] - 0.5) < EPSILON:
                        exposure["late_positive_removal_at_floor"] += 1
                team_details[t] = dict(
                    roster_size=sizes[t], next_wealth=wealth[team], next_below=nxt["below"] if complete else None,
                    next_loadout=nxt["loadout"] if complete else None, next_bank=nxt["bank"] if complete else None,
                    mean_committed=mean_commit, commitment=commitment(mean_commit), scarcity=scarcity,
                    removed=call["removed_by_team"][team], lost=loss,
                    incumbent_gain=call["econ_round_by_team"][team],
                )
            results = {a: attribute_candidate(
                removals, call["econ_round_by_team"], wealth,
                {t: sizes[team_name(t)] for t in call["econ_round_by_team"]}, rn, **kwargs,
            ) for a, kwargs in ARMS.items()}
            for mp, p in call["players"].items():
                base = results["incumbent"][mp]
                error = max(abs(base.credit - p["credit"]), abs(base.debit - p["debit"]), abs(base.value - p["value"]))
                max_observer_error = max(max_observer_error, error)
                if error > EPSILON:
                    raise AssertionError(f"Observer mismatch in match {mid}, round {rn}, player {mp}")
            values_by_round[rn] = results
            details_by_round[rn] = team_details
            if mid == focus:
                focus_rounds.append(dict(match_id=mid, round=rn, teams=team_details, players=[dict(
                    id=mp, name=names[mp], team=team_name(p["team"]), committed=p["committed"],
                    removed=p["removed"], lost=p["lost"],
                    arms={a: dict(credit=r[mp].credit, debit=r[mp].debit, raw=r[mp].value)
                          for a, r in results.items()},
                ) for mp, p in call["players"].items()]))
        round_nets = defaultdict(lambda: {a: defaultdict(float) for a in ARMS})
        round_rn = {}
        for row in rows:
            rn = rounds[row.round_id]["round_number"]
            round_rn[row.round_id] = rn
            values = [values_by_round[rn][a][row.match_player_id].value if rn in values_by_round else 0.0 for a in ARMS]
            if round(ECON_SCALE * values[0]) != row.econ_component:
                raise AssertionError(f"Scorer mismatch in match {mid}, round {rn}, player {row.match_player_id}")
            raw.append(values)
            metadata.append((mid, rn, row.match_player_id))
            time_values.append(row.time_impact)
            other_impact.append(row.damage + row.leverage_component)
            for a, value in zip(ARMS, values):
                round_nets[row.round_id][a][rosters[mid][row.match_player_id]] += value
            data_hash.update(repr((mid, rn, row.match_player_id, values, row.time_impact)).encode())
        for rid, nets in round_nets.items():
            rn = round_rn[rid]
            regime = ("early" if rn in (2, 3, 4, 14, 15, 16) else "late") if rn in calls else "abstained"
            for a, teams in nets.items():
                vals = [teams[t] for t in sizes]
                sign_counts[a][regime][signs(vals)] += 1
                if a == "incumbent":
                    max_incumbent_net = max(max_incumbent_net, abs(math.fsum(vals)))
        if ordinal % 100 == 0 or ordinal == len(match_ids):
            print(f"Replayed {ordinal}/{len(match_ids)} matches; {len(raw):,} player-rounds; {time.monotonic()-start:.1f}s", flush=True)
        db.expunge_all()
    identity["replayed_values_sha256"] = data_hash.hexdigest()
    identity["elapsed_seconds"] = time.monotonic() - start
    return (np.asarray(raw), np.asarray(metadata), np.asarray(time_values), np.asarray(other_impact),
            dict(identity=identity, exposure=dict(exposure), focus_rounds=focus_rounds,
                 signs={a: {regime: dict(c) for regime, c in regimes.items()} for a, regimes in sign_counts.items()},
                 max_observer_error=max_observer_error, max_incumbent_round_net=max_incumbent_net), names)


def summarize(raw, metadata, time_values, other, report, names):
    time_sd = float(time_values.std())
    scales = time_sd / raw.std(axis=0)
    report["time_sd"] = time_sd
    report["arms"] = {}
    old_points = np.rint(raw[:, 0] * ECON_SCALE)
    match_ids = metadata[:, 0]
    unique_matches, match_index = np.unique(match_ids, return_inverse=True)
    for col, arm in enumerate(ARMS):
        points = np.rint(raw[:, col] * ECON_SCALE)
        reanchored = np.rint(raw[:, col] * scales[col])
        match_sum = np.bincount(match_index, weights=points).tolist()
        report["arms"][arm] = dict(
            raw=distribution(raw[:, col]), same_scale=distribution(points),
            proposed_anchor=float(scales[col]), reanchored=distribution(reanchored),
            change_same_scale=distribution(points-old_points),
            change_reanchored=distribution(reanchored-old_points), match_sum=distribution(match_sum),
            global_unrounded_sum=float(raw[:, col].sum()),
            global_rounding_residual=float(points.sum() - ECON_SCALE * raw[:, col].sum()),
            match_sum_within_ten_percent=float(np.mean(np.abs(match_sum) <= 10) * 100),
        )
        indices = np.argsort(np.abs(points-old_points))[-10:][::-1]
        report["arms"][arm]["largest_changes"] = [dict(
            match_id=int(metadata[i, 0]), round=int(metadata[i, 1]), player=names[int(metadata[i, 2])],
            before=int(old_points[i]), after=int(points[i]), reanchored=int(reanchored[i]),
        ) for i in indices]
    report["fixed_ten"] = []
    for mid in FIXED_TEN:
        for mp in np.unique(metadata[match_ids == mid, 2]):
            mask = (match_ids == mid) & (metadata[:, 2] == mp)
            report["fixed_ten"].append(dict(match_id=mid, player=names[int(mp)], player_id=int(mp), rounds=int(mask.sum()),
                arms={a: dict(econ=int(np.rint(raw[mask, j]*ECON_SCALE).sum()),
                              impact=int(other[mask].sum()+np.rint(raw[mask, j]*ECON_SCALE).sum()),
                              reanchored_econ=int(np.rint(raw[mask, j]*scales[j]).sum()))
                      for j, a in enumerate(ARMS)}))
    for r in report["focus_rounds"]:
        for p in r["players"]:
            for j, a in enumerate(ARMS):
                p["arms"][a]["points"] = round(p["arms"][a]["raw"]*ECON_SCALE)
                p["arms"][a]["reanchored_points"] = round(p["arms"][a]["raw"]*scales[j])
        for team, d in r["teams"].items():
            d["arms"] = {a: dict(
                raw=math.fsum(p["arms"][a]["raw"] for p in r["players"] if p["team"] == team),
                points=sum(p["arms"][a]["points"] for p in r["players"] if p["team"] == team),
                credit=math.fsum(p["arms"][a]["credit"] for p in r["players"] if p["team"] == team),
                debit=math.fsum(p["arms"][a]["debit"] for p in r["players"] if p["team"] == team),
            ) for a in ARMS}
    return report


def markdown(report):
    lines = ["# Independent econ debit and late-floor experiment", "",
             "Read-only descriptive comparison. No production defaults, versions, weights or stored scores changed.",
             "All point comparisons use C=1 and the same ECON_SCALE=1007.9209 unless explicitly labeled reanchored.",
             "Time is the unchanged legacy reference; these are econ-only comparisons, not a full release-candidate validation.", "",
             f"Matches: {report['identity']['matches']:,}; player-rounds: {report['arms']['incumbent']['raw']['n']:,}; read-only transaction: {report['identity']['transaction_read_only']}.",
             f"Observer discrepancy: {report['max_observer_error']:.3g}; maximum unrounded incumbent round net: {report['max_incumbent_round_net']:.3g}.", "",
             "## Formula and scale", "",
             "| Arm | Raw mean | Raw SD | Proposed anchor | Mean points at old scale | SD points at old scale |",
             "|---|---:|---:|---:|---:|---:|"]
    for a, v in report["arms"].items():
        lines.append(f"| {a} | {v['raw']['mean']:.6f} | {v['raw']['sd']:.6f} | {v['proposed_anchor']:.4f} | {v['same_scale']['mean']:.2f} | {v['same_scale']['sd']:.2f} |")
    lines += ["", "## Unrounded round signs", "", "| Arm | Regime | Rounds | Both negative | Both positive | Both zero | Opposite | One zero |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for a, regimes in report["signs"].items():
        for regime, c in regimes.items():
            n = sum(c.values())
            cells = [f"{c.get(k, 0):,} ({100*c.get(k, 0)/n:.1f}%)" for k in ("both_negative", "both_positive", "both_zero", "opposite", "one_zero")]
            lines.append(f"| {a} | {regime} | {n:,} | " + " | ".join(cells) + " |")
    lines += ["", "## Exposure counts", "", "```json", json.dumps(report["exposure"], indent=2), "```", "",
              "## Match 3104: team-by-team walkthrough", "",
              "NEXT wealth includes loadout plus remaining bank; below-buy count uses loadout only. These are observed levels, not a counterfactual loss estimate.", "",
              "| Round | Team | Lost | Mean commitment | Next wealth/player | Next below-buy | Own scarcity | Old gain | Incumbent | Independent | No floor | Both changes |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in report["focus_rounds"]:
        for team, d in r["teams"].items():
            avg = d['next_wealth']/d['roster_size'] if d['next_wealth'] is not None else float('nan')
            lines.append(f"| {r['round']} | {team} | {d['lost']:,.0f} | {d['mean_committed']:.0f} | {avg:,.0f} | {d['next_below']} | {d['scarcity']:.4f} | {d['incumbent_gain']:.4f} | " + " | ".join(f"{d['arms'][a]['points']:+,}" for a in ARMS) + " |")
    lines += ["", "## Match 3104: each player's economy contribution", "",
              "Each variant is CREDIT minus DEBIT before the common point scale and final rounding. Independent debit can be zero while the same team's opponent still gets positive credit.", ""]
    for r in report["focus_rounds"]:
        lines += [f"### Round {r['round']}", "", "| Player | Team | Removed | Lost | Old credit/debit | New debit | Incumbent | Independent | No floor | Both changes |", "|---|---|---:|---:|---|---:|---:|---:|---:|---:|"]
        for p in r["players"]:
            old = p["arms"]["incumbent"]
            lines.append(f"| {p['name']} | {p['team']} | {p['removed']:,.0f} | {p['lost']:,.0f} | {old['credit']:.4f} / {old['debit']:.4f} | {p['arms']['independent']['debit']:.4f} | " + " | ".join(f"{p['arms'][a]['points']:+,}" for a in ARMS) + " |")
    lines += ["", "## Fixed ten matches: player economy totals", "", "| Match | Player | Rounds | Incumbent | Independent | No floor | Both changes |", "|---|---|---:|---:|---:|---:|---:|"]
    for p in report["fixed_ten"]:
        lines.append(f"| {p['match_id']} | {p['player']} | {p['rounds']} | " + " | ".join(f"{p['arms'][a]['econ']:+,}" for a in ARMS) + " |")
    lines += ["", "The JSON companion includes reanchored values, component quantiles, largest changes and illustrative total Impact with all other terms fixed.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Development-only subset; 0 means full corpus")
    parser.add_argument("--focus", type=int, default=3104)
    parser.add_argument("--out-dir", default="../docs/superpowers/econ-counterfactual-results")
    args = parser.parse_args()
    if engine.url.host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit("Refusing a non-local database for this declared local experiment")
    with SessionLocal() as db:
        raw, meta, times, other, report, names = collect(db, args.limit, args.focus)
        report = summarize(raw, meta, times, other, report, names)
        db.rollback()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "measurements.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (out / "walkthrough.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"identity": report["identity"], "anchors": {a: v["proposed_anchor"] for a, v in report["arms"].items()}}, indent=2))
    print(f"Report: {(out / 'walkthrough.md').resolve()}")


if __name__ == "__main__":
    main()
