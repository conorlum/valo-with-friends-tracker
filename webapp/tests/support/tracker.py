"""Small API-shaped match with known identities, a trade, assists and unknown spend."""
from collections import Counter

IDS = [f'Synthetic{i}#TEST' for i in range(10)]


def payload(external_id='tracker-contract', rounds=2):
    def stat(value): return {'value': value}
    segments = [
        {'type': 'team-summary', 'attributes': {'teamId': side}, 'stats': {'roundsWon': stat(score)}}
        for side, score in [('Red', rounds), ('Blue', 0)]
    ]
    for i, name in enumerate(IDS):
        segments.append({'type': 'player-summary', 'attributes': {'platformUserIdentifier': name},
                         'metadata': {'agentName': 'Jett' if i < 5 else 'Sova', 'teamId': 'Red' if i < 5 else 'Blue'}})
    for rn in range(1, rounds+1):
        events = [(0, 5, 18000, [1]), (6, 0, 18500, [])]
        segments.append({'type': 'round-summary', 'attributes': {'round': rn}, 'metadata': {},
                         'stats': {'winningTeam': stat('Red'), 'roundResult': stat('Elimination')}})
        for i, name in enumerate(IDS):
            values = {'score': 200 if i in (0, 6) else 0, 'kills': int(i in (0, 6)),
                      'deaths': int(i in (0, 5)), 'assists': int(i == 1), 'loadoutValue': 3900, 'remainingCredits': 1000}
            if i > 0: values['spentCredits'] = 0 if i == 1 else 3900
            segments.append({'type': 'player-round', 'attributes': {'round': rn, 'platformUserIdentifier': name},
                             'stats': {k: stat(v) for k,v in values.items()}})
        for killer, victim, milliseconds, assists in events:
            segments.append({'type': 'player-round-kills', 'attributes': {'round': rn,
                'platformUserIdentifier': IDS[killer], 'opponentPlatformUserIdentifier': IDS[victim]},
                'metadata': {'roundTime': milliseconds, 'weaponName': 'Vandal',
                             'assistants': [{'platformUserIdentifier': IDS[i]} for i in assists]}})
    return {'attributes': {'id': external_id}, 'metadata': {'mapName': 'Bind', 'dateStarted': '2026-10-01T20:00:00+00:00'},
            'segments': segments}
