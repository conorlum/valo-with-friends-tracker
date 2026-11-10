"""Bounded Ascent map-message decoder. Raw capture is evidence, never a state by itself.

Bindings name map actors, not network GUIDs. Reviewed opening/closing/closed and explicit break/reset
RPCs are supported. Omitted open completion needs explicit same-clock DoorState evidence;
other absent/default state parameters remain unknown.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path

from app.replays import layouts
from app.replays.map_feature_door_evidence import _door_parameter_evidence

VERSION = 1
DOOR_CLASS = '/Game/Interactable/WindowShield.WindowShield_C'
GLASS_CLASS = '/Game/Interactable/RespawningDestructible.RespawningDestructible_C'
BINDINGS = {
    'WindowShieldA1': ('ascent_garden', 'Garden Door', DOOR_CLASS, [3940, 2340]),
    'WindowShieldB1': ('ascent_market', 'Market Door', DOOR_CLASS, [2880, 5980]),
    'RespawningDestructible_2': ('ascent_heaven_glass', 'Heaven Glass', GLASS_CLASS, [2680, 2830]),
}
KEYS = frozenset(b[0] for b in BINDINGS.values())
MAX_BITS = 65536
MAX_ROWS = 200000


class DecodeError(ValueError):
    pass


class Bits:
    def __init__(self, data, length):
        if type(length) is not int or not 0 <= length <= MAX_BITS or len(data) != (length + 7) // 8:
            raise DecodeError('invalid bit boundary')
        self.data, self.length, self.pos = data, length, 0

    @property
    def remaining(self):
        return self.length - self.pos

    def take(self, n):
        if n < 0 or n > self.remaining:
            raise DecodeError('field exceeds payload boundary')
        value = sum(((self.data[(self.pos+i)//8] >> ((self.pos+i)%8)) & 1) << i for i in range(n))
        self.pos += n
        return value

    def packed(self):
        value = 0
        for i in range(5):
            byte = self.take(8)
            value |= (byte >> 1) << (7*i)
            if not byte & 1:
                if value > 0xffffffff:
                    raise DecodeError('packed uint overflow')
                return value
        raise DecodeError('packed uint continuation overflow')

    def serialized(self, maximum):
        width = maximum.bit_length() - 1
        value = self.take(width)
        mask = 1 << width
        if value + mask < maximum and self.take(1):
            value |= mask
        return value

    def sub(self, length):
        return Bits(self.take(length).to_bytes((length+7)//8, 'little'), length)


def fields(bits, descriptor, *, preserve_unknown=False):
    names = {f['handle']: f['name'] for f in descriptor.get('fields', [])}
    result = {}
    if bits.remaining:
        if bits.take(1):
            raise DecodeError('property checksums are unsupported')
    while bits.remaining > 1:
        handle = bits.packed()
        if handle == 0:
            break
        count = bits.packed()
        value = bits.take(count)
        name = names.get(handle - 1)
        if name is None and preserve_unknown:
            name = '#' + str(handle - 1)
        if name in result:
            raise DecodeError('duplicate parameter')
        if name:
            result[name] = (value, count)
    if bits.remaining and bits.take(bits.remaining):
        raise DecodeError('nonzero property tail')
    return result


def invocations(row, groups, class_path, *, strict=False, opaque_rpcs=(), keep_unknown_rpcs=()):
    encoded = row.get('transformed_base64')
    if row.get('diagnostic_version') != VERSION or row.get('truncated') or not isinstance(encoded, str) \
            or len(encoded) > ((MAX_BITS+7)//8+2)//3*4:
        raise DecodeError('unsupported or truncated capture')
    try:
        bits = Bits(base64.b64decode(encoded, validate=True), row['payload_bits'])
    except (ValueError, KeyError) as exc:
        raise DecodeError('invalid captured payload') from exc
    if row.get('has_rep_layout'):
        fields(bits, groups[class_path])  # validate framing, but do not promote property snapshots to transitions
        return []
    descriptor = groups[class_path + '_ClassNetCache']
    handles = {f['handle']: f['name'] for f in descriptor['fields']}
    if not handles or any(type(h) is not int or h < 0 for h in handles):
        raise DecodeError('invalid class-net-cache descriptor')
    maximum = max(max(handles)+1, 2)
    calls = []
    while bits.remaining:
        handle = bits.serialized(maximum)
        if bits.remaining < 8:
            if bits.take(bits.remaining):
                raise DecodeError('nonzero RPC tail')
            break
        body = bits.sub(bits.packed())
        name = handles.get(handle)
        if name is None:
            raise DecodeError('unknown RPC handle')
        layout = class_path + ':' + name
        opaque = name.strip() in opaque_rpcs and layout not in groups
        if strict and body.remaining and layout not in groups and not opaque:
            raise DecodeError('RPC parameter descriptor missing')
        # Strict map ledgers must retain unknown parameter handles: discarding one
        # could make an incomplete argument set look like a qualified omission.
        params = fields(body, groups.get(layout, {}),
                        preserve_unknown=opaque or strict or name.strip() in keep_unknown_rpcs) \
            if body.remaining and (layout in groups or opaque) else {}
        calls.append((name, params))
    return calls


def read_capture(export_dir: Path, map_name: str, manifest: dict):
    path = export_dir / 'map-audit.ndjson'
    # No "opening" flag: openings are qualified per event (the witness rule), and the signals carry them.
    report = {'v': VERSION, 'status': 'unavailable', 'rows': 0, 'errors': [],
              'source_sha256': manifest.get('source_sha256')}
    if layouts.map_of(map_name) != 'Ascent' or not path.is_file():
        return [], report
    groups = {g['path']: g for g in manifest.get('net_field_export_groups', [])}
    signals, digest = [], hashlib.sha256()
    calls, properties = [], {}
    identities = {}  # Reject one authored actor name appearing on multiple live network identities.
    ambiguous = set()
    with path.open('rb') as stream:
        for line_number in range(1, MAX_ROWS + 2):
            line = stream.readline(40001)
            if not line:
                break
            if line_number > MAX_ROWS or len(line) > 40000:
                raise DecodeError('map capture size/row limit')
            digest.update(line)
            report['rows'] += 1
            try:
                row = json.loads(line)
                actor = row.get('actor_path')
                if actor not in BINDINGS or not row.get('is_actor'):
                    continue
                key, _, class_path, _ = BINDINGS[actor]
                identity = (row.get('actor_net_guid'), row.get('channel'))
                if any(type(v) is not int or v < 0 for v in identity):
                    raise DecodeError('missing actor identity')
                if actor in identities and identities[actor] != identity:
                    ambiguous.add(key)
                identities[actor] = identity
                time = row.get('time_ms')
                if type(time) is not int or time < 0:
                    raise DecodeError('invalid replay time')
                decoded = invocations(row, groups, class_path, keep_unknown_rpcs={'PlayDoorSounds'})
                if class_path == DOOR_CLASS and row.get('has_rep_layout'):
                    params = fields(Bits(base64.b64decode(row['transformed_base64'], validate=True),
                                         row['payload_bits']), groups[class_path])
                    if 'DoorState' in params:
                        properties.setdefault((actor, class_path, time), []).append(
                            {'params': {'DoorState': params['DoorState']}, 'line': line_number})
                for name, params in decoded:
                    calls.append({'actor': actor, 'class_path': class_path, 'key': key, 'time_ms': time,
                                  'rpc': name, 'params': params, 'line': line_number})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                if len(report['errors']) < 20:
                    report['errors'].append({'line': line_number, 'reason': str(exc)[:160]})
                report['error_count'] = report.get('error_count', 0) + 1
    # Resolve after collecting properties: the same-clock witness can follow its RPC in file order.
    for row in calls:
        name, params = row['rpc'], row['params']
        state, witnesses = None, []
        if row['class_path'] == DOOR_CLASS:
            if name == 'MulticastRoundBegin':
                state = 'open'
            elif name == 'HandleDoorDestroyed':
                state = 'broken'
            elif name == 'PlayDoorSounds':
                new = params.get('New State')
                state = {1: 'opening', 2: 'closing', 3: 'closed'}.get(new[0]) if new and new[1] <= 32 else None
                if state is None:
                    _, witnesses = _door_parameter_evidence(row, properties)
                    state = 'open' if witnesses else 'unknown'
        elif name == 'RoundBeginBroadcast':
            state = 'intact'
        elif name == 'OnDie':
            state = 'broken'
        if state:
            signal = {'key': row['key'], 'time_ms': row['time_ms'], 'state': state,
                      'rpc': name, 'line': row['line']}
            if witnesses:
                signal['evidence'] = [e['line'] for e in witnesses]
            signals.append(signal)
    # No partial rule activation when any captured frame failed or an actor binding is ambiguous.
    report.update(sha256=digest.hexdigest(), status='partial' if report['errors'] or ambiguous else 'decoded',
                  ambiguous_keys=sorted(ambiguous), signals=len(signals))
    return sorted(signals, key=lambda s: (s['time_ms'], s['state'] == 'broken', s['line'])), report


def attach(replay, export_dir, manifest, windows):
    try:
        signals, report = read_capture(export_dir, replay.map_name, manifest)
    except (DecodeError, OSError) as exc:
        signals, report = [], {'v': VERSION, 'status': 'partial', 'errors': [{'reason': str(exc)[:160]}]}
    replay.report['map_features'] = report
    if layouts.map_of(replay.map_name) != 'Ascent':
        return
    for number, (start, decided, end) in enumerate(windows, 1):
        if number not in replay.rounds:
            continue
        features = []
        for actor, (key, label, _, uv) in BINDINGS.items():
            before = [s for s in signals if s['key'] == key and s['time_ms'] <= start
                      and s['rpc'] in ('MulticastRoundBegin', 'RoundBeginBroadcast')]
            # Each round requires its own explicit reset in the preceding buy phase.
            previous_end = windows[number-2][2] if number > 1 else 0
            initial = before[-1]['state'] if before and before[-1]['time_ms'] >= previous_end else 'unknown'
            reset_time = before[-1]['time_ms'] if initial != 'unknown' else start
            events = [{'t': round((s['time_ms']-start)/1000, 6), 'state': s['state'],
                       'rpc': s['rpc'], 'line': s['line'],
                       **({'evidence': s['evidence']} if 'evidence' in s else {})} for s in signals
                      if s['key'] == key and reset_time < s['time_ms'] < end]
            features.append({'key': key, 'name': label, 'uv': uv, 'initial': initial,
                             'events': events, 'closing_s': 5 if key != 'ascent_heaven_glass' else None})
        replay.rounds[number]['map_features'] = {'v': VERSION, 'status': report['status'],
            'sha256': report.get('sha256'), 'features': features, 'calculation': 'pending geometry'}


def _usable_duration(duration):
    return isinstance(duration, (int, float)) and not isinstance(duration, bool) \
        and duration > 0 and math.isfinite(duration)


def _motion_done(began, at, duration):
    """A motion begun at `began` has run its full duration by `at`, within the 0.25 s grace.

    Compared on the source's 6-decimal clock: 8.008 - 3.008 is 4.999999999999999 in floats."""
    return began is not None and _usable_duration(duration) and duration <= round(at - began, 6) <= duration + .25


def _reverses(state, next_state, began, at, duration):
    return (state, next_state) in (('opening', 'closing'), ('closing', 'opening')) \
        and not _motion_done(began, at, duration)


def reverses(feature):
    """Whether the door reverses mid-motion anywhere in the round, even with an endpoint on the same clock.

    `state_at` is pending only between a reversal and its endpoint; when both share a timestamp no sample falls
    between them, so the runtime asks this once (a reversal fails the round, design section 2)."""
    state, began = feature.get('initial', 'unknown'), None
    for event in feature.get('events', []):
        if state == 'broken':
            return False
        if _reverses(state, event['state'], began, event['t'], feature.get('closing_s')):
            return True
        state = event['state']
        began = event['t'] if state in ('closing', 'opening') else None
    return False


def state_at(feature, t):
    """Source state plus a separately labeled five-second motion estimate. Missing endpoints stay unknown."""
    state, began = feature.get('initial', 'unknown'), None
    terminal = state == 'broken'
    reversed_motion = False
    duration = feature.get('closing_s')
    usable_duration = _usable_duration(duration)

    def motion_closure(at):
        if began is None or not usable_duration or at - began > duration + .25:
            return None
        progress = min(max((at - began) / duration, 0.0), 1.0)
        return 1.0 - progress if state == 'opening' else progress

    for event in feature.get('events', []):
        if event['t'] > t:
            break
        # A terminal object cannot become intact/open without an explicit round reset.
        if terminal:
            continue
        next_state = event['state']
        if _reverses(state, next_state, began, event['t'], duration):
            reversed_motion = True
        if next_state in ('open', 'closed', 'broken'):
            reversed_motion = False
        state = event['state']
        if state == 'broken':
            terminal = True
        began = event['t'] if state in ('closing', 'opening') else None
    closure = {'open': 0.0, 'closed': 1.0}.get(state)
    modeled = state in ('closing', 'opening')
    if modeled and not reversed_motion:
        closure = motion_closure(t)
    return {'state': state, 'closure': closure, 'modeled': modeled,
            'pending': reversed_motion or state == 'unknown' or (modeled and closure is None)}
