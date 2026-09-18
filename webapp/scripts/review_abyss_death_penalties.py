"""Read the frozen V2 artifact and compare the owner's 30%/80% own debit."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'docs/superpowers/diagnostics'))
import econ_buy_disruption_reference as ref

OUT = ROOT / 'docs/superpowers/abyss-buy-disruption-review'


def main():
    original = (OUT / 'calculations.json').read_bytes()
    source = json.loads(original)
    players, rows, events = {}, [], []
    checks = 0

    def close(a, b):
        nonlocal checks
        assert abs(a-b) < 1e-8, (a, b)
        checks += 1

    for r in source['rounds']:
        for team, info in r['teams'].items():
            budget = ref.Budget(**info['budget'])
            rate = 0.8 if budget.severity_pool > 0 else 0.3
            team_debit = 0
            for p in info['players']:
                parts = p['raw_parts']
                credit = (parts['background_credit'] + parts['disruption_credit']) * ref.REVIEW_SCALE
                debit = sum(ref.buy_linked_debit(p['lost'], budget)) * ref.REVIEW_SCALE
                net = round(credit-debit)
                total = players.setdefault(p['id'], dict(name=p['name'], team=team,
                    credit=0., debit=0., old_debit=0., net=0, old_net=0))
                for key, value in dict(credit=credit, debit=debit,
                    old_debit=(parts['background_debit']+parts['scarcity_debit'])*ref.REVIEW_SCALE,
                    net=net, old_net=p['econ_points']).items():
                    total[key] += value
                team_debit += debit
                rows.append(dict(round=r['round'], player=p['name'], team=team, rate=rate,
                    loss=p['lost'], credit=credit, debit=debit, net=net, old_net=p['econ_points']))
            close(team_debit, sum(ref.buy_linked_debit(budget.lost, budget))*ref.REVIEW_SCALE)
        for event in r['events']:
            budget = ref.Budget(**r['teams'][event['victim_team']]['budget'])
            rate = 0.8 if budget.severity_pool > 0 else 0.3
            debit = sum(ref.buy_linked_debit(event['exposure'], budget))*ref.REVIEW_SCALE
            if event['enemy']:
                close(debit, rate*event['econ_credit_points'])
            events.append(dict(round=r['round'], time=event['time'], killer=event['killer_name'],
                victim=event['victim_name'], credit=event['econ_credit_points'], rate=rate,
                old_debit=event['victim_debit_points'], debit=debit))
    teams = defaultdict(lambda: dict(credit=0., debit=0., net=0, old_net=0))
    for pid, p in players.items():
        assert p['old_net'] == source['totals'][str(pid)]['econ_points']
        p['change'] = p['net']-p['old_net']
        p['per_round'] = p['net']/len(source['rounds'])
        for key in teams[p['team']]:
            teams[p['team']][key] += p[key]
    for p in players.values():
        close(p['debit'], sum(e['debit'] for e in events if e['victim']==p['name']))
    result = dict(model='buy-disruption-v2-with-30-80-debit', match=source['match'],
        basis_sha256=hashlib.sha256(original).hexdigest(), source_sha256=source['source_sha256'],
        reference_sha256=hashlib.sha256(Path(ref.__file__).read_bytes()).hexdigest(),
        constants=dict(absorbed=.3, disrupted=.8, scale=ref.REVIEW_SCALE, C=1),
        checks=checks, players=players, teams=dict(teams), player_rounds=rows, events=events)
    (OUT/'death-penalty-30-80.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    lines = ['# Abyss 3104: 30% absorbed / 80% disrupted death penalties', '',
        'Same V2 killer credits and frozen source. Only the death penalty changes. ECON points only, C=1, scale=1007.9209.', '',
        'Absorbed loss: debit = 30% of the small equipment damage value. Constrained next buy: debit = 80% of (small value + allocated disruption value). Neither rate multiplies the previous scarcity debit.', '',
        'A positive existing V2 severity pool selects 80%; otherwise 30%. Existing eligibility, carryover targets and first-kit loss deduplication stay fixed. The funding test estimates disruption; it does not establish counterfactual causation.', '',
        '| Player | Team | Gross credit | New debit | Old net | New net | Change | New net / played round |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for p in sorted(players.values(), key=lambda p: -p['net']):
        lines.append(f"| {p['name']} | {p['team']} | {p['credit']:.2f} | {p['debit']:.2f} | {p['old_net']:+d} | {p['net']:+d} | {p['change']:+d} | {p['per_round']:+.2f} |")
    lines += ['', 'Net totals sum individually rounded player-round nets. Gross credit/debit columns retain full precision until display, so subtracting displayed match totals can differ slightly. The per-round denominator is all 24 played rounds.', '',
        '| Team | Gross credit | New debit | Old net | New net |', '|---|---:|---:|---:|---:|']
    for team, p in teams.items():
        lines.append(f"| {team} | {p['credit']:.2f} | {p['debit']:.2f} | {p['old_net']:+d} | {p['net']:+d} |")
    lines += ['', '## Selected deaths', '', '| Round | Victim | Killer credit | Old death debit | New death debit | Rate |', '|---|---|---:|---:|---:|---:|']
    for e in events:
        if (e['round'], e['victim']) in [(6,'1xgoofy#56719'), (3,'Osmin#NA1'), (22,'DoubleBl1nd#BEEF'), (2,'1xgoofy#56719'), (16,'ternstyle#GIGI'), (17,'ternstyle#GIGI')]:
            lines.append(f"| {e['round']} | {e['victim']} | {e['credit']:.2f} | {e['old_debit']:.2f} | {e['debit']:.2f} | {e['rate']:.0%} |")
    lines += ['', '## Interpretation', '',
        'Absorbed losses now have a small cost even if reserves are low. The large penalty requires the same constrained-buy condition used by the killer credit. This removes the separate reserve-depletion charge that dominated V2.', '',
        'Crediting 100% and debiting 30%/80% creates a positive match-wide balance from enemy kills by construction. That is the requested non-zero-sum policy, not independent evidence of model quality. Individual players and teams can still finish negative. Self/team/environmental losses add debit without enemy credit.', '',
        'The binary rate changes from 30% to 80% when a positive severity pool appears, including a tiny funding gap. The disruption amount is smooth, but the rate on the small background term has a step. This is preserved as requested, not silently smoothed.', '',
        f'{checks} artifact reconciliation checks pass. The comparison uses no database connection and changes no production scores.', '',
        'Full player-round and death ledger: [JSON artifact](death-penalty-30-80.json). Previous calculation: [V2 walkthrough](walkthrough.md).', '']
    (OUT/'death-penalty-30-80.md').write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines[:35]))


if __name__ == '__main__':
    main()
