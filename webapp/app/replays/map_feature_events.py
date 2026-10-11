"""Bounded per-map RPC ledger and explicit annotation-to-source event bindings.

This module has no geometry/database imports. A framed RPC is evidence, not an
activation guessed from damage, proximity, player names or a network GUID.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from app.replays.ascent_features import DecodeError, invocations, MAX_ROWS

VERSION = 1
CLASSES = {
    'WindowShield': '/Game/Interactable/WindowShield.WindowShield_C',
    'RespawningWallPlate': '/Game/Interactable/WallPlates/RespawningWallPlate.RespawningWallPlate_C',
    'Switch_HiddenTemple': '/Game/Maps/Jam/Switch_HiddenTemple.Switch_HiddenTemple_C',
    'RespawningPlummetShootable': '/Game/Environment/Plummet/Blueprints/RespawningPlummetShootable.RespawningPlummetShootable_C',
}
MAP_PREFIXES = {'Sunset': ('WindowShield',), 'Abyss': ('RespawningWallPlate',),
                'Lotus': ('Switch_HiddenTemple', 'RespawningWallPlate'),
                'Summit': ('RespawningPlummetShootable',)}
RESET_RPCS = frozenset({'MulticastRoundBegin', 'RoundBeginBroadcast', 'MulticastResetAnimation'})
CAUSES = frozenset({'switch', 'shoot', 'activate', 'destroy', 'observed'})
SOURCE_RULES = {
    CLASSES['WindowShield']: ({'MulticastRoundBegin'}, {'PlayDoorSounds': {'switch', 'observed'},
                                                        'HandleDoorDestroyed': {'destroy'}}),
    CLASSES['RespawningWallPlate']: ({'RoundBeginBroadcast'}, {'OnDie': {'destroy'}}),
    CLASSES['Switch_HiddenTemple']: ({'MulticastResetAnimation'}, {'MulticastPlayAnimation': {'switch'}}),
    CLASSES['RespawningPlummetShootable']: ({'MulticastRoundBegin'}, {'OnDie': {'shoot'}}),
}


def read_ledger(export_dir: Path, map_name: str, manifest: dict):
    path = export_dir / 'map-audit.ndjson'
    report = {'v': VERSION, 'status': 'unavailable', 'rows': 0, 'errors': [],
              'source_sha256': manifest.get('source_sha256'), 'qualification': 'framing_only'}
    if map_name not in MAP_PREFIXES or not path.is_file():
        return [], report
    groups = {g['path']: g for g in manifest.get('net_field_export_groups', [])}
    ledger, digest, identities, ambiguous = [], hashlib.sha256(), {}, set()
    with path.open('rb') as stream:
        for line_number in range(1, MAX_ROWS + 2):
            line = stream.readline(40001)
            if not line:
                break
            if line_number > MAX_ROWS or len(line) > 40000:
                raise DecodeError('map capture size/row limit')
            digest.update(line); report['rows'] += 1
            try:
                row = json.loads(line)
                actor = row.get('actor_path')
                if not isinstance(actor, str) or not row.get('is_actor'):
                    continue
                prefix = next((p for p in MAP_PREFIXES[map_name] if actor.startswith(p)), None)
                if prefix is None:
                    continue
                cls = CLASSES[prefix]
                identity = row.get('actor_net_guid'), row.get('channel')
                if any(type(v) is not int or v < 0 for v in identity):
                    raise DecodeError('missing actor identity')
                if actor in identities and identities[actor] != identity:
                    ambiguous.add(actor)
                identities[actor] = identity
                when = row.get('time_ms')
                if type(when) is not int or when < 0:
                    raise DecodeError('invalid replay time')
                declared = row.get('class_path')
                # Some captures expose the static class in export_group_path only.
                if declared and declared not in (cls, actor):
                    raise DecodeError('capture class conflicts with map actor family')
                # OnDie's identity is exported by the class-net-cache. Its arguments
                # do not decide whether destruction occurred; validate their field
                # framing and retain unnamed handles without inventing inherited names.
                for rpc, params in invocations(row, groups, cls, strict=True, opaque_rpcs={'OnDie'}):
                    ledger.append({'actor': actor, 'class_path': cls, 'time_ms': when,
                                   'rpc': rpc, 'params': params, 'line': line_number})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                report['error_count'] = report.get('error_count', 0) + 1
                if len(report['errors']) < 20:
                    report['errors'].append({'line': line_number, 'reason': str(exc)[:160]})
    # Keep duplicate source evidence. Timeline binding deduplicates equivalent transitions.
    report.update(status='partial' if report['errors'] or ambiguous else 'framed',
                  sha256=digest.hexdigest(), ambiguous_actors=sorted(ambiguous), calls=len(ledger))
    return sorted(ledger, key=lambda e: (e['time_ms'], e['line'])), report


def binding_problems(source):
    if not isinstance(source, dict):
        return ['explicit replay source binding missing']
    actors = source.get('actors')
    if not isinstance(actors, list) or not actors or not all(isinstance(a, str) and a for a in actors) \
            or len(set(actors)) != len(actors):
        return ['source needs distinct exact actor names']
    if source.get('class_path') not in CLASSES.values():
        return ['unsupported source class']
    resets = source.get('reset_rpcs')
    allowed_resets, allowed_rules = SOURCE_RULES[source['class_path']]
    if not isinstance(resets, list) or not resets or not all(isinstance(r, str) for r in resets) \
            or not set(resets) <= allowed_resets:
        return ['source needs reviewed explicit reset RPCs']
    rules = source.get('rules')
    if not isinstance(rules, list) or not rules:
        return ['source event rules missing']
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('rpc'), str) \
                or not isinstance(rule.get('kind'), str) or rule['kind'] not in CAUSES:
            return ['invalid source event rule']
        if rule['kind'] not in allowed_rules.get(rule['rpc'], set()):
            return ['source RPC/cause pair is not qualified for this family']
        if rule['kind'] == 'observed' and not isinstance(rule.get('state'), str):
            return ['observed rule needs an explicit state']
        equals = rule.get('equals', {})
        if not isinstance(equals, dict) or any(not isinstance(k, str) or k.startswith('#') or type(v) is not int
                                             for k, v in equals.items()):
            return ['parameter conditions must name integer values']
    return []


def bind_round(feature, ledger, report, start_ms, end_ms, previous_end_ms=0):
    """Bind only exact actor/class/RPC/parameter matches, requiring a fresh reset.

    Multiple buttons may target one door. Their resets are one feature reset;
    same-clock equivalent events are deduplicated while retaining source lines.
    Unmatched RPCs are retained in the ledger, never silently assigned a state.
    """
    source = feature.get('replay_source')
    problems = binding_problems(source)
    result = {'v': VERSION, 'status': 'pending', 'initial': 'unknown', 'events': [], 'reasons': problems,
              'sha256': report.get('sha256')}
    if problems:
        return result
    if report.get('status') != 'framed':
        result['reasons'] = ['complete framed capture required']
        return result
    rows = [e for e in ledger if e['actor'] in source['actors'] and e['class_path'] == source['class_path']]
    resets = [e for e in rows if e['rpc'].strip() in source['reset_rpcs']
              and previous_end_ms <= e['time_ms'] <= start_ms]
    if not resets:
        result['reasons'] = ['round reset unobserved']
        return result
    reset = max(e['time_ms'] for e in resets)
    events = {}
    for row in rows:
        if not reset < row['time_ms'] < end_ms:
            continue
        if row['rpc'].strip() in source['reset_rpcs']:
            result['reasons'] = ['unexpected reset during round']
            return result
        matched = [r for r in source['rules'] if row['rpc'].strip() == r['rpc'] and
                   all(k in row['params'] and row['params'][k][0] == v
                       for k, v in r.get('equals', {}).items())]
        if len(matched) > 1:
            result['reasons'] = ['ambiguous source event rules']
            return result
        if not matched:
            # A known transition RPC with absent/unknown parameters is a coverage hole.
            if any(row['rpc'].strip() == r['rpc'] for r in source['rules']):
                result['reasons'] = ['transition parameters unqualified']
                return result
            continue
        rule = matched[0]
        event = {'t': (row['time_ms'] - start_ms) / 1000, 'kind': rule['kind']}
        if rule['kind'] == 'observed':
            event['state'] = rule['state']
        key = (event['t'], event['kind'], event.get('state'))
        if key not in events:
            events[key] = {**event, 'evidence': []}
        events[key]['evidence'].append({'actor': row['actor'], 'rpc': row['rpc'], 'line': row['line']})
    result.update(status='bound', initial=feature.get('initial_state'), events=list(events.values()),
                  reset_time_ms=reset, reasons=[])
    if result['initial'] is None:
        result.update(status='pending', reasons=['annotation initial state unresolved'])
    return result


def attach(replay, export_dir, manifest, windows):
    if replay.map_name not in MAP_PREFIXES:
        return
    try:
        ledger, report = read_ledger(export_dir, replay.map_name, manifest)
    except (DecodeError, OSError) as exc:
        ledger, report = [], {'v': VERSION, 'status': 'partial', 'errors': [{'reason': str(exc)[:160]}]}
    replay.report['map_messages'] = report
    for number, (start, _, end) in enumerate(windows, 1):
        if number not in replay.rounds:
            continue
        previous = windows[number - 2][2] if number > 1 else 0
        # Buy-phase resets are necessary for binding and remain on the absolute replay clock.
        replay.rounds[number]['map_messages'] = {'v': VERSION, 'map': replay.map_name, 'report': report,
            'start_ms': start, 'end_ms': end, 'previous_end_ms': previous,
            'ledger': [e for e in ledger if previous <= e['time_ms'] < end]}
