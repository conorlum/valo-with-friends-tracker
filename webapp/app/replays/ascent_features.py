"""Bounded Ascent map-message decoder. Raw capture is evidence, never a state by itself.

Bindings name map actors, not network GUIDs. Only the reviewed closing/closed and explicit break/reset
RPCs are supported. Intact reopening and absent/default state parameters remain unknown.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path

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


def invocations(row, groups, class_path, *, strict=False, opaque_rpcs=()):
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
        params = fields(body, groups.get(layout, {}), preserve_unknown=opaque) \
            if body.remaining and (layout in groups or opaque) else {}
        calls.append((name, params))
    return calls


def read_capture(export_dir: Path, map_name: str, manifest: dict):
    path = export_dir / 'map-audit.ndjson'
    report = {'v': VERSION, 'status': 'unavailable', 'rows': 0, 'errors': [],
              'opening': 'unverified', 'source_sha256': manifest.get('source_sha256')}
    if map_name != 'Ascent' or not path.is_file():
        return [], report
    groups = {g['path']: g for g in manifest.get('net_field_export_groups', [])}
    signals, digest = [], hashlib.sha256()
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
                for name, params in invocations(row, groups, class_path):
                    state = None
                    if class_path == DOOR_CLASS:
                        if name == 'MulticastRoundBegin':
                            state = 'open'
                        elif name == 'HandleDoorDestroyed':
                            state = 'broken'
                        elif name == 'PlayDoorSounds':
                            new = params.get('New State')
                            state = {2: 'closing', 3: 'closed'}.get(new[0]) if new and new[1] <= 32 else None
                            if state is None:
                                state = 'unknown'  # includes opening and absent/default parameters
                    elif name == 'RoundBeginBroadcast':
                        state = 'intact'
                    elif name == 'OnDie':
                        state = 'broken'
                    if state:
                        signals.append({'key': key, 'time_ms': time, 'state': state,
                                        'rpc': name, 'line': line_number})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                if len(report['errors']) < 20:
                    report['errors'].append({'line': line_number, 'reason': str(exc)[:160]})
                report['error_count'] = report.get('error_count', 0) + 1
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
    if replay.map_name != 'Ascent':
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
                       'rpc': s['rpc'], 'line': s['line']} for s in signals
                      if s['key'] == key and reset_time < s['time_ms'] < end]
            features.append({'key': key, 'name': label, 'uv': uv, 'initial': initial,
                             'events': events, 'closing_s': 5 if key != 'ascent_heaven_glass' else None})
        replay.rounds[number]['map_features'] = {'v': VERSION, 'status': report['status'],
            'sha256': report.get('sha256'), 'features': features, 'calculation': 'pending geometry'}


def state_at(feature, t):
    """Source state plus a separately labeled five-second motion estimate. Missing endpoints stay unknown."""
    state, began = feature.get('initial', 'unknown'), None
    terminal = state == 'broken'
    for event in feature.get('events', []):
        if event['t'] > t:
            break
        # A terminal object cannot become intact/open without an explicit round reset.
        if terminal:
            continue
        state = event['state']
        if state == 'broken':
            terminal = True
        began = event['t'] if state == 'closing' else None
    closure = {'open': 0.0, 'closed': 1.0}.get(state)
    if state == 'closing' and began is not None:
        duration = feature.get('closing_s')
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0 \
                and math.isfinite(duration) and t - began <= duration + .25:
            closure = min(max((t-began)/duration, 0.0), 1.0)
    return {'state': state, 'closure': closure, 'modeled': state == 'closing',
            'pending': state == 'unknown' or (state == 'closing' and closure is None)}
