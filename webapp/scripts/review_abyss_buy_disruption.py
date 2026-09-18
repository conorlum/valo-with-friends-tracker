"""Export and work through one local match using the frozen spec arithmetic.

No production scorer is called or changed. Source inputs and exact calculations
are saved so every example can be checked independently from the formulas.
"""
from collections import defaultdict
import argparse
from dataclasses import asdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT / "docs/superpowers/diagnostics"))

from sqlalchemy import text
from app.db import SessionLocal, engine
from app.scoring.agent_economy import free_ability_credits
import econ_buy_disruption_reference as ref

MATCH_ID = 3104
OUT = ROOT / "docs/superpowers/abyss-buy-disruption-review"


def serial(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def read_source():
    if engine.url.host not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("This walkthrough is restricted to the local database")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        def query(sql):
            return [dict(r) for r in db.execute(text(sql), {"mid": MATCH_ID}).mappings()]
        source = {
            "match": query("SELECT id,external_id,map_name,played_at,team1_rounds_won,team2_rounds_won FROM matches WHERE id=:mid")[0],
            "players": query("SELECT mp.id,mp.team,mp.agent,p.display_name FROM match_players mp JOIN players p ON p.id=mp.player_id WHERE mp.match_id=:mid ORDER BY mp.id"),
            "rounds": query("SELECT id,round_number,outcome,planted,plant_time FROM rounds WHERE match_id=:mid ORDER BY round_number"),
            "stats": query("SELECT r.round_number,s.match_player_id,s.loadout,s.remaining,s.kills,s.deaths FROM round_player_stats s JOIN rounds r ON r.id=s.round_id WHERE r.match_id=:mid ORDER BY r.round_number,s.match_player_id"),
            "events": query("SELECT k.id,r.round_number,k.killer_match_player_id AS killer,k.death_match_player_id AS victim,k.event_time_seconds AS time,k.weapon FROM kill_events k JOIN rounds r ON r.id=k.round_id WHERE r.match_id=:mid ORDER BY r.round_number,k.event_time_seconds,k.id"),
            "read_only": db.execute(text("SHOW transaction_read_only")).scalar(),
            "snapshot": db.execute(text("SELECT txid_current_snapshot()::text")).scalar(),
        }
        for p in source["players"]:
            p["free_ability_value"] = free_ability_credits(p["agent"])
        db.rollback()
    if source["match"]["map_name"] != "Abyss":
        raise AssertionError("The declared match is not Abyss")
    return source


def winner(outcome):
    if (outcome or "").startswith("Team A"):
        return "TEAM_1"
    if (outcome or "").startswith("Team B"):
        return "TEAM_2"
    return None


def calculate(source):
    players = {p["id"]: p for p in source["players"]}
    roster = {t: [p["id"] for p in source["players"] if p["team"] == t] for t in ("TEAM_1", "TEAM_2")}
    if any(len(ids) != 5 for ids in roster.values()):
        raise ValueError("Expected a complete 5v5 roster")
    rounds = {r["round_number"]: r for r in source["rounds"]}
    stats, events = defaultdict(dict), defaultdict(list)
    for s in source["stats"]:
        stats[s["round_number"]][s["match_player_id"]] = s
    for e in source["events"]:
        events[e["round_number"]].append(e)
    result, checks = [], 0
    def close(a, b):
        nonlocal checks
        checks += 1
        if abs(a-b) > 1e-8:
            raise AssertionError(f"Reconciliation failed: {a} versus {b}")
    for rn, r in rounds.items():
        reason = None
        if rn >= max(rounds):
            reason = "final_round"
        elif rn not in set(range(2, 12)) | set(range(14, 24)):
            reason = "pistol_half_or_ot_boundary"
        elif rn+1 not in rounds:
            reason = "missing_next_round"
        elif "Surrendered" in (r["outcome"] or "") or "Surrendered" in (rounds[rn+1]["outcome"] or ""):
            reason = "surrender"
        elif set(stats[rn]) != set(players) or set(stats[rn+1]) != set(players):
            reason = "incomplete_stats"
        elif any(e["victim"] not in players for e in events[rn]):
            reason = "unknown_victim"
        pistol_round = 1 if rn <= 12 else 13
        pistol_winner = winner(rounds.get(pistol_round, {}).get("outcome"))
        if reason is None and pistol_winner is None:
            reason = "unknown_pistol_winner"
        if reason:
            result.append(dict(round=rn, outcome=r["outcome"], abstention=reason, teams={}, events=[]))
            continue
        for n in (rn, rn+1):
            for s in stats[n].values():
                ref._valid([s["loadout"], s["remaining"]])
        paid = {mp: max(0, stats[rn][mp]["loadout"]-p["free_ability_value"]) for mp, p in players.items()}
        next_paid = {mp: max(0, stats[rn+1][mp]["loadout"]-p["free_ability_value"]) for mp, p in players.items()}
        exposure = ref.first_loss_exposures(paid, events[rn])
        loss_by_player = defaultdict(float)
        for e in events[rn]:
            loss_by_player[e["victim"]] += exposure[e["id"]]
        teams, budgets, ledger = {}, {}, {}
        for t, members in roster.items():
            targets = ref.buy_targets([paid[mp] for mp in members], rn if rn <= 12 else rn-12, t == pistol_winner)
            bank = [stats[rn+1][mp]["remaining"] for mp in members]
            budgets[t] = ref.team_budget(targets, [next_paid[mp] for mp in members], bank,
                                         [loss_by_player[mp] for mp in members])
            b = budgets[t]
            close(b.restorable, b.shortfall-max(0, b.target-(b.funding+b.lost)))
            if b.funding >= b.target:
                close(b.restorable, 0)
            teams[t] = dict(budget=asdict(b), pistol_winner=t == pistol_winner,
                round_winner=t == winner(r["outcome"]),
                deaths=len([e for e in events[rn] if players[e["victim"]]["team"] == t]),
                first_loss_events=len([mp for mp in members if loss_by_player[mp] > 0]),
                before_paid=[paid[mp] for mp in members],
                next_raw=[stats[rn+1][mp]["loadout"] for mp in members],
                next_paid=[next_paid[mp] for mp in members], next_bank=bank, targets=targets,
                next_below_raw4200=sum(stats[rn+1][mp]["loadout"] < 4200 for mp in members),
                players=[dict(id=mp, name=players[mp]["display_name"], current_paid=paid[mp],
                              next_raw=stats[rn+1][mp]["loadout"], next_paid=next_paid[mp],
                              next_bank=stats[rn+1][mp]["remaining"], target=target, lost=loss_by_player[mp])
                         for mp, target in zip(members, targets)])
            for mp in members:
                debit = ref.own_debit(loss_by_player[mp], b)
                ledger[mp] = dict(background_credit=0., disruption_credit=0.,
                                  background_debit=debit[0], scarcity_debit=debit[1])
        scored_events = []
        for e in events[rn]:
            v = exposure[e["id"]]
            killer, victim = players.get(e["killer"]), players[e["victim"]]
            is_enemy = killer is not None and killer["team"] != victim["team"]
            bg, bonus = ref.event_credit(v, budgets[victim["team"]], enemy=is_enemy)
            if is_enemy:
                ledger[e["killer"]]["background_credit"] += bg
                ledger[e["killer"]]["disruption_credit"] += bonus
            if v == 0:
                close(bg+bonus, 0)
            if bg+bonus > 1.1*v/ref.R+1e-10:
                raise AssertionError("Event reward exceeds its equipment-restoration cap")
            scored_events.append(dict(**e, killer_name=killer["display_name"] if killer else "unknown/environment",
                victim_name=victim["display_name"], victim_team=victim["team"], enemy=is_enemy,
                exposure=v, background_points=bg*ref.REVIEW_SCALE, disruption_points=bonus*ref.REVIEW_SCALE,
                econ_credit_points=(bg+bonus)*ref.REVIEW_SCALE,
                victim_debit_points=sum(ref.own_debit(v, budgets[victim["team"]]))*ref.REVIEW_SCALE))
        for t, info in teams.items():
            ids = roster[t]
            for p in info["players"]:
                parts = ledger[p["id"]]
                raw = parts["background_credit"]+parts["disruption_credit"]-parts["background_debit"]-parts["scarcity_debit"]
                p.update(raw_parts=parts, raw_net=raw, econ_points=round(raw*ref.REVIEW_SCALE))
            info["credit_points"] = sum((ledger[i]["background_credit"]+ledger[i]["disruption_credit"])*ref.REVIEW_SCALE for i in ids)
            info["debit_points"] = sum((ledger[i]["background_debit"]+ledger[i]["scarcity_debit"])*ref.REVIEW_SCALE for i in ids)
            info["net_points"] = sum(p["econ_points"] for p in info["players"])
            credit_event_sum = sum(e["econ_credit_points"] for e in scored_events if players.get(e["killer"], {}).get("team") == t)
            close(info["credit_points"], credit_event_sum)
            close(info["debit_points"], sum(ref.own_debit(budgets[t].lost, budgets[t]))*ref.REVIEW_SCALE)
        result.append(dict(round=rn, outcome=r["outcome"], planted=r["planted"], plant_time=r["plant_time"],
                           pistol_winner=pistol_winner, abstention=None, teams=teams, events=scored_events))
    valid = [r for r in result if not r["abstention"]]
    positive = [e for r in valid for e in r["events"] if e["econ_credit_points"] > 0]
    positive.sort(key=lambda e: (-e["econ_credit_points"], e["round_number"], e["time"], e["id"]))
    totals = {}
    for mp, p in players.items():
        player_rounds = [item for r in valid for info in r["teams"].values() for item in info["players"] if item["id"] == mp]
        totals[mp] = dict(name=p["display_name"], team=p["team"], econ_points=sum(x["econ_points"] for x in player_rounds))
    return dict(model_version="buy-disruption-v2", match=source["match"], rounds=result, highest=positive[:8],
                lowest=sorted(positive, key=lambda e: (e["econ_credit_points"],e["round_number"],e["time"],e["id"]))[:8],
                totals=totals, reconciliation_checks=checks,
                eligible_rounds=len(valid), disrupted_team_rounds=sum(info["budget"]["restorable"] > 0 for r in valid for info in r["teams"].values()),
                total_team_rounds=2*len(valid))


def markdown(report):
    lines = ["# Abyss 3104: worked buy-disruption calculation", "",
             "TEAM_1 lost 11-13 to TEAM_2. The match began 2026-08-25 00:48 UTC (August 24 in Pacific time).",
             "These are experimental ECON points only. Combat credit, damage/assist credit and total Impact are not being redefined or rescored here.",
             "V2 fixed values: background=0.10, disruption=1.00, R=19500, activation gap=3900, scale=1007.9209, C=1. Paid-loadout targets follow the spec.", "",
             f"{report['eligible_rounds']} eligible rounds; {report['disrupted_team_rounds']}/{report['total_team_rounds']} team-rounds have a positive estimated restorable funding gap. {report['reconciliation_checks']} arithmetic checks pass.", "",
             "## Every eligible round and team", "",
             "L = first-loss exposure; H = target; U = capped next equipment plus bank; D=max(0,H-U). V2 pool=min(L, observed equipment gap * min(1,D/3900)). It is a severity index, not literal missing cash or causal denial.", "",
             "| Round | Team | Won round? | Deaths / first-loss players | L | H | U | Funding gap D | Severity pool | Next raw below 4200 | Killer credit | Own debit | Net econ |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in report["rounds"]:
        for t, info in r["teams"].items():
            b = info["budget"]
            lines.append(f"| {r['round']} | {t} | {'yes' if info['round_winner'] else 'no'} | {info['deaths']}/{info['first_loss_events']} | {b['lost']:,.0f} | {b['target']:,.0f} | {b['funding']:,.0f} | {b['shortfall']:,.0f} | {b['severity_pool']:,.1f} | {info['next_below_raw4200']} | {info['credit_points']:.1f} | {info['debit_points']:.1f} | {info['net_points']:+d} |")
    for label in ("highest", "lowest"):
        lines += ["", f"## {label.title()} positive enemy-kill econ credits", "", "| Round | Time | Killer | Victim | Exposure | Small credit | Disruption credit | Total econ credit |", "|---|---:|---|---|---:|---:|---:|---:|"]
        for e in report[label]:
            lines.append(f"| {e['round_number']} | {e['time']:.3f}s | {e['killer_name']} | {e['victim_name']} | {e['exposure']:,.0f} | {e['background_points']:.2f} | {e['disruption_points']:.2f} | {e['econ_credit_points']:.2f} |")
    lines += ["", "## Detailed inputs and event calculations", ""]
    for r in report["rounds"]:
        if r["abstention"]:
            lines += [f"### Round {r['round']}: zero ({r['abstention']})", ""]
            continue
        lines += [f"### Round {r['round']}: {r['outcome']}", "", f"Pistol winner: {r['pistol_winner']}. Plant time: {r['plant_time'] if r['planted'] else 'none'}.", ""]
        for t, info in r["teams"].items():
            b = info["budget"]
            lines += [f"**{t}:** L={b['lost']:,.0f}; H={b['target']:,.0f}; U={b['funding']:,.0f}; D={b['shortfall']:,.0f}; restorable Q={b['restorable']:,.0f}. Observed gap={b['observed_gap']:,.0f}; activation={b['activation']:.6f}; severity pool={b['severity_pool']:,.3f}. Uncapped next wealth={b['wealth']:,.0f}; f={b['scarcity']:.6f}.", "",
                      "| Player | Current paid | Lost once | Next raw loadout | Next paid | Next bank | Target | Credit raw | Debit raw | Net econ points |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for p in info["players"]:
                x=p["raw_parts"]
                lines.append(f"| {p['name']} | {p['current_paid']:,.0f} | {p['lost']:,.0f} | {p['next_raw']:,.0f} | {p['next_paid']:,.0f} | {p['next_bank']:,.0f} | {p['target']:,.0f} | {x['background_credit']+x['disruption_credit']:.6f} | {x['background_debit']+x['scarcity_debit']:.6f} | {p['econ_points']:+d} |")
            lines.append("")
        lines += ["| Event ID | Time | Killer | Victim | Exposure | Small credit | Buy-disruption credit | Killer econ credit | Victim econ debit |", "|---|---:|---|---|---:|---:|---:|---:|---:|"]
        for e in r["events"]:
            lines.append(f"| {e['id']} | {e['time']:.3f}s | {e['killer_name']} | {e['victim_name']} | {e['exposure']:,.0f} | {e['background_points']:.2f} | {e['disruption_points']:.2f} | {e['econ_credit_points']:.2f} | {e['victim_debit_points']:.2f} |")
        lines.append("")
    lines += ["## Match economy totals", "", "Sum of rounded player-round economy nets; these are not full Impact scores.", "", "| Player | Team | Econ points |", "|---|---|---:|"]
    for p in sorted(report["totals"].values(), key=lambda p: -p["econ_points"]):
        lines.append(f"| {p['name']} | {p['team']} | {p['econ_points']:+d} |")
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Recalculate the exact saved snapshot without a database query")
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8")) if args.source else read_source()
    report = calculate(source)
    source_json = json.dumps(source, indent=2, default=serial, allow_nan=False)
    report["source_sha256"] = hashlib.sha256(source_json.encode()).hexdigest()
    report["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["reference_sha256"] = hashlib.sha256(Path(ref.__file__).read_bytes()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "source.json").write_text(source_json, encoding="utf-8")
    (OUT / "calculations.json").write_text(json.dumps(report, indent=2, default=serial, allow_nan=False), encoding="utf-8")
    (OUT / "walkthrough.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("eligible_rounds", "disrupted_team_rounds", "total_team_rounds", "reconciliation_checks", "highest", "lowest")}, indent=2))
    print(f"Walkthrough: {OUT / 'walkthrough.md'}")


if __name__ == "__main__":
    main()
