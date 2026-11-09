"""Shared door evidence qualification; edits need a Sunset consumer bump and a CONDENSE_REVISION bump."""

DOOR_CLASS = '/Game/Interactable/WindowShield.WindowShield_C'


def _door_parameter_evidence(row, properties):
    """Qualify an omitted open-completion argument from explicit same-clock replication.

    Observed in two Sunset captures: Old State=opening, isMoveTo=true, omitted New State,
    and an explicitly serialized three-bit DoorState=0 on the same actor and timestamp.
    Do not assume a protocol default, use nearby/stale snapshots, or turn a snapshot into
    a switch. Keep the original RPC params untouched for audit and archived compatibility.
    """
    params = row['params']
    if row['class_path'] != DOOR_CLASS or row['rpc'].strip() != 'PlayDoorSounds' \
            or 'New State' in params or set(params) != {'Old State', 'isMoveTo'} \
            or tuple(params.get('Old State', ())) != (1, 3) \
            or tuple(params.get('isMoveTo', ())) != (1, 1):
        return params, []
    witnesses = properties.get((row['actor'], row['class_path'], row['time_ms']), [])
    if not witnesses or any(tuple(e['params'].get('DoorState', ())) != (0, 3) for e in witnesses):
        return params, []
    return {**params, 'New State': (0, 3)}, witnesses
