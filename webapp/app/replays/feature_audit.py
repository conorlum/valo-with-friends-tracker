"""Private, read-only inventory of possible map-feature signals in raw parser exports.

Candidate names are search hints, never bindings or state transitions. This scanner
deliberately bypasses the condenser's row filter. Memory and retained evidence are
bounded, and every limit is reported. It imports no database or worker services.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import html
import json
import math
from pathlib import Path
import re

from app.replays.contract import check_manifest, classify_diagnostics, load_pin, sha256_file, ContractError

FORMAT = "map-feature-replay-audit"
VERSION = 1
KEYWORD = re.compile(r"door|switch|glass|breakable|window|shutter|barrier|shootable|interact", re.I)
META = ("actor_path", "archetype_path", "replication_class_path", "export_group_path", "object_path",
        "class_path", "outer_path", "function_name", "function_export_path")


@dataclass(frozen=True)
class Limits:
    rows: int | None = None
    line_bytes: int = 8 * 1024 * 1024
    candidates: int = 1000
    actors: int = 100000
    samples: int = 12
    names: int = 2000

    def validate(self):
        for key, value in asdict(self).items():
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{key} must be a positive integer")


def _bounded(value, depth=0):
    """Bound private samples too; do not retain arbitrarily large property values."""
    if depth >= 4:
        return "<depth limit>"
    if isinstance(value, float) and not math.isfinite(value):
        return '<nonfinite value>'
    if isinstance(value, dict):
        result = {str(k)[:200]: _bounded(v, depth + 1) for k, v in list(value.items())[:64]}
        if len(value) > 64:
            result["<omitted keys>"] = len(value) - 64
        return result
    if isinstance(value, list):
        return [_bounded(v, depth + 1) for v in value[:32]] + (["<items omitted>"] if len(value) > 32 else [])
    if isinstance(value, str):
        return value[:1000] + ("<truncated>" if len(value) > 1000 else "")
    return value


def _names(row):
    # Never use player chat/names or arbitrary string property values as discovery hints.
    names = [(key, row[key]) for key in META if isinstance(row.get(key), str)]
    payload = row.get("payload")
    if isinstance(payload, dict):
        names.extend(("property", str(key)) for key in payload)
    return names


def _feature_list(tags, map_name):
    features = ((tags or {}).get("maps", {}).get(map_name, {}).get("map_features", {}).get("features", []))
    return [{"id": f["id"], "name": f.get("name", f["id"]),
             "states": [s["name"] for s in f.get("states", [])],
             "events": sorted({t["event"] for t in f.get("transitions", []) if isinstance(t.get("event"), str)})}
            for f in features]


def _reject_constant(value):
    raise ValueError(f'nonfinite JSON constant {value}')


def _check_manifest_shape(manifest):
    for field in ('diagnostics', 'net_field_export_groups', 'filtered_export_group_summary'):
        values = manifest.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            raise ValueError(f'manifest.{field} must be an array of objects')
    if not isinstance(manifest.get('stats', {}), dict):
        raise ValueError('manifest.stats must be an object')
    for field in ('malformed_packet_count',):
        value = manifest.get('stats', {}).get(field, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f'manifest.stats.{field} must be a nonnegative integer')
    value = manifest.get('suppressed_diagnostic_count', 0)
    if type(value) is not int or value < 0:
        raise ValueError('manifest.suppressed_diagnostic_count must be a nonnegative integer')
    duration = manifest.get('duration_ms')
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        raise ValueError('manifest.duration_ms must be finite and nonnegative')
    for diagnostic in manifest.get('diagnostics', []):
        channel = diagnostic.get('channel_index')
        if channel is not None and type(channel) is not int:
            raise ValueError('manifest diagnostic channel_index must be an integer')
        for field in ('code', 'export_group_path', 'field_name'):
            if diagnostic.get(field) is not None and not isinstance(diagnostic[field], str):
                raise ValueError(f'manifest diagnostic {field} must be text')
    for group in manifest.get('net_field_export_groups', []):
        if not isinstance(group.get('fields', []), list):
            raise ValueError('manifest export-group fields must be an array')


def _plain_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def inventory(export_dir: Path, *, tags=None, map_name=None, source: Path | None = None,
              limits=Limits(), pin=None):
    limits.validate()
    tool_sha = sha256_file(Path(__file__))
    manifest_path, events = export_dir / "manifest.json", export_dir / "events.ndjson"
    if manifest_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("manifest exceeds 32 MiB audit limit")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes, parse_constant=_reject_constant)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    _check_manifest_shape(manifest)
    descriptor_terms = set()
    for group in manifest.get('net_field_export_groups', []):
        fields = [str(f.get('name', '')) for f in group.get('fields', []) if isinstance(f, dict)]
        path = str(group.get('path', ''))
        if KEYWORD.search(path) or any(KEYWORD.search(f) for f in fields):
            term = path.split(':')[0].split('/')[-1].split('.')[-1]
            term = re.sub(r'_ClassNetCache$|_C$', '', term)
            if len(term) >= 8 and len(descriptor_terms) < limits.names:
                descriptor_terms.add(term)
    folded_terms = [(term, term.casefold()) for term in sorted(descriptor_terms)]
    source_hash = sha256_file(source) if source else None
    contract_error = None
    try:
        check_manifest(manifest, pin or load_pin(), source_hash)
    except ContractError as exc:
        contract_error = str(exc)
    counts, candidates, actors, generations, paths, names = Counter(), {}, {}, {}, Counter(), Counter()
    flags, errors, digest = set(), [], hashlib.sha256()
    total, byte_count, last_time = 0, 0, None
    before = events.stat()
    with events.open("rb") as handle:
        while True:
            if limits.rows is not None and total >= limits.rows:
                if handle.peek(1):
                    flags.add("row_limit")
                break
            line = handle.readline(limits.line_bytes + 1)
            if not line:
                break
            if len(line) > limits.line_bytes:
                flags.add("line_size_limit")
                errors.append({"line": total + 1, "reason": "line exceeds configured size; scan stopped"})
                break
            digest.update(line); byte_count += len(line); total += 1
            try:
                row = json.loads(line, parse_constant=_reject_constant)
                if not isinstance(row, dict) or not isinstance(row.get("type"), str):
                    raise ValueError("row must be an object with a type")
                time = row.get("time_ms")
                if type(time) not in (int, float) or not math.isfinite(time) or time < 0:
                    raise ValueError("invalid export time_ms")
            except (ValueError, UnicodeError, RecursionError) as exc:
                if len(errors) < 20:
                    errors.append({"line": total, "reason": str(exc)[:200]})
                flags.add("malformed_rows")
                continue
            if last_time is not None and time < last_time:
                flags.add("nonmonotonic_times")
            last_time = time
            kind = row["type"]
            if kind in counts or len(counts) < limits.names:
                counts[kind] += 1
            else:
                flags.add("name_limit")
            guid = row.get("actor_net_guid")
            guid = str(guid) if type(guid) is int and guid > 0 else None
            if kind == "actor_spawned" and guid:
                if guid not in generations and len(generations) >= limits.actors:
                    flags.add("actor_limit")
                else:
                    generations[guid] = generations.get(guid, 0) + 1
                    actors[guid] = {"generation": generations[guid], "spawn_line": total, "channel": row.get("channel")}
            lifetime = actors.get(guid) if guid else None
            if lifetime and row.get('channel') is not None and lifetime['channel'] is not None and row['channel'] != lifetime['channel']:
                lifetime = None
                flags.add('actor_channel_mismatch')
            if kind in ("actor_closed", "actor_close") and guid:
                # Keep the lifecycle row as evidence, with no inferred destruction.
                actors.pop(guid, None)
            row_names = _names(row)
            for key, value in row_names:
                if key == "property":
                    if value in names or len(names) < limits.names:
                        names[value] += 1
                    else:
                        flags.add("name_limit")
                elif value.startswith("/Game/Maps/"):
                    if value in paths or len(paths) < limits.names:
                        paths[value] += 1
                    else:
                        flags.add("name_limit")
            hints = [f"{key}: {value[:400]}" for key, value in row_names if KEYWORD.search(value)]
            metadata_text = [value.casefold() for key, value in row_names if key != 'property']
            for term, folded in folded_terms:
                if any(folded in value for value in metadata_text):
                    hints.append('descriptor_name_hint: ' + term)
            actor_key = f"actor:{guid}:life:{lifetime['generation']}" if lifetime else None
            if not hints and actor_key not in candidates:
                continue
            key = actor_key or f"unbound:{guid or 'none'}:channel:{row.get('channel')}:{kind}:{next(iter(hints), '')}"
            if key not in candidates:
                if len(candidates) >= limits.candidates:
                    flags.add("candidate_limit"); continue
                candidates[key] = {"key": key, "actor_net_guid": guid, "lifetime": lifetime,
                                   "binding": "candidate_only" if lifetime else "unbound",
                                   "hints": [], "rows": 0, "types": {}, "samples": [], "properties": {},
                                   "first_ms": time, "last_ms": time}
            candidate = candidates[key]; candidate["rows"] += 1; candidate["last_ms"] = time
            if kind in candidate['types'] or len(candidate['types']) < limits.names:
                candidate["types"][kind] = candidate["types"].get(kind, 0) + 1
            changed = False
            payload = row.get('payload')
            if isinstance(payload, dict):
                for prop, value in payload.items():
                    if not re.search(r'state|transition|alive|door|glass|switch|broken|health', prop, re.I):
                        continue
                    if type(value) not in (int, float, bool, str) and value is not None:
                        continue
                    if prop not in candidate['properties'] and len(candidate['properties']) >= 64:
                        flags.add('property_limit'); continue
                    entry = candidate['properties'].setdefault(prop, {'changes': [], 'changes_omitted': 0})
                    value = _bounded(value)
                    if not entry['changes'] or entry['last_value'] != value:
                        changed = True
                        if len(entry['changes']) < 32:
                            entry['changes'].append({'line': total, 'time_ms': time, 'value': value})
                        else:
                            entry['changes_omitted'] += 1
                    entry['last_value'] = value
            for hint in hints:
                if hint not in candidate["hints"] and len(candidate["hints"]) < 30:
                    candidate["hints"].append(hint)
            if len(candidate["samples"]) < limits.samples and (changed or len(candidate['samples']) < max(1, limits.samples // 2)):
                sample = _bounded(row)
                if len(json.dumps(sample)) > 16384:
                    sample = {k: _bounded(row.get(k)) for k in ('type', 'time_ms', 'actor_net_guid', 'object_net_guid', *META)}
                    sample['sample_payload_omitted'] = True
                candidate["samples"].append({"line": total, "row": sample})
    after = events.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        flags.add("source_changed_during_scan")
    complete = byte_count == before.st_size and not flags.intersection({"row_limit", "line_size_limit", "source_changed_during_scan"})
    for candidate in candidates.values():
        candidate["samples_omitted"] = candidate["rows"] - len(candidate["samples"])
    groups = []
    for group in manifest.get("net_field_export_groups", []):
        if isinstance(group, dict):
            fields = [f.get("name", "") for f in group.get("fields", []) if isinstance(f, dict)]
            if KEYWORD.search(str(group.get("path", ""))) or any(KEYWORD.search(str(f)) for f in fields):
                groups.append(_bounded(group))
    report = {"format": FORMAT, "version": VERSION,
              "identity": {"manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                           "tool_sha256": tool_sha,
                           "events_sha256": digest.hexdigest() if complete else None,
                           "scanned_prefix_sha256": digest.hexdigest(), "source_sha256": manifest.get("source_sha256"),
                           "tags_sha256": hashlib.sha256(_plain_json(tags).encode()).hexdigest() if tags else None},
              "parser": {key: manifest.get(key) for key in ("schema_version", "parser_version", "replay_build", "parse_profile", "parse_status", "duration_ms")},
              "contract": {"compatible": contract_error is None, "error": contract_error,
                           "source_verified": source is not None and source_hash == manifest.get("source_sha256")},
              "provenance": {"synthetic_markers": [str(manifest[key])[:200] for key in ('fixture', 'synthetic', 'notes', 'parse_profile')
                              if key in manifest and re.search('fixture|synthetic|synthesized', str(manifest[key]), re.I)],
                             "authenticity": 'not_independently_verified',
                             "note": 'Checksum verification binds files; it does not verify observed game behavior.'},
              "coverage": {"diagnostics": classify_diagnostics(manifest).as_dict(),
                           "limitations": manifest.get("limitations", []),
                           "movement_available": (export_dir / "movement.ndjson").is_file(),
                           "descriptor_discovery_terms": sorted(descriptor_terms),
                           "keyword_export_groups": groups[:limits.names],
                           "filtered_export_groups": [_bounded(g) for g in manifest.get("filtered_export_group_summary", [])[:limits.names]],
                           "filtered_groups_omitted": max(0, len(manifest.get("filtered_export_group_summary", [])) - limits.names),
                           "keyword_groups_omitted": max(0, len(groups) - limits.names)},
              "scan": {"complete": complete, "rows": total, "bytes": byte_count, "file_bytes": before.st_size,
                       "limits": asdict(limits), "flags": sorted(flags), "errors": errors,
                       "types": dict(counts), "property_names": dict(names), "map_paths": dict(paths)},
              "map": {"name": map_name, "basis": "owner_assertion" if map_name else "unresolved"},
              "discovery": {'keyword_pattern': KEYWORD.pattern, 'case_insensitive': True,
                            'sources': ['named_metadata', 'top_level_property_names', 'manifest_descriptor_name_hints'],
                            'property_change_pattern': 'state|transition|alive|door|glass|switch|broken|health',
                            'note': 'Names and scalar property changes are search aids; no game-state values are decoded.'},
              "features": _feature_list(tags, map_name), "candidates": list(candidates.values()),
              "conclusion": "Candidate inventory only. No binding or state is verified; this export cannot prove that a signal is absent from the replay."}
    report["identity"]["audit_sha256"] = hashlib.sha256(_plain_json(report).encode()).hexdigest()
    return report


def worksheet(report):
    return {"format": "map-feature-observations", "version": 1, "audit_sha256": report["identity"]["audit_sha256"],
            "clock": "export_time_ms", "observations": [
                {"feature_id": f["id"], "action": "", "state": "", "time_ms": None,
                 "uncertainty_ms": None, "review": "unverified", "candidate_key": None,
                 "evidence_lines": [], "notes": "Watch one clear change; record time and before/after state.",
                 "observation_id": f"scene-{i + 1}"}
                for i, f in enumerate(report["features"])]}


def validate_observations(value, report):
    """Reject stale identities and ambiguous clocks before comparing any scene."""
    errors = []
    if not isinstance(report, dict) or report.get('format') != FORMAT or report.get('version') != VERSION or not isinstance(report.get('identity'), dict):
        return ['audit integrity mismatch or unsupported version']
    try:
        expected = report['identity'].get('audit_sha256')
        check = json.loads(json.dumps(report, allow_nan=False))
        check['identity'].pop('audit_sha256', None)
        if expected != hashlib.sha256(json.dumps(check, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest():
            return ['audit integrity mismatch or unsupported version']
        features = {f['id']: f for f in report['features']}
        candidates = {c['key']: c for c in report['candidates']}
        if not isinstance(report['scan']['rows'], int) or report['scan']['rows'] < 0:
            raise ValueError('invalid rows')
    except (KeyError, TypeError, ValueError):
        return ['malformed audit report']
    if not isinstance(value, dict):
        return ["worksheet must be an object"]
    if value.get("format") != "map-feature-observations" or value.get("version") != 1:
        errors.append("unsupported observation format/version")
    if value.get("audit_sha256") != report["identity"]["audit_sha256"]:
        errors.append("worksheet belongs to a different audit")
    if value.get("clock") != "export_time_ms":
        errors.append("clock must be export_time_ms; prove round-clock conversion separately")
    observations = value.get("observations")
    if not isinstance(observations, list):
        return errors + ["observations must be an array"]
    scene_ids = set()
    for i, observation in enumerate(observations):
        prefix = f"observations[{i}]"
        if not isinstance(observation, dict):
            errors.append(f"{prefix}: must be an object"); continue
        scene_id = observation.get('observation_id')
        if not isinstance(scene_id, str) or not scene_id or len(scene_id) > 64 or scene_id in scene_ids:
            errors.append(f'{prefix}: observation_id must be a unique nonempty string (up to 64 characters)')
        else:
            scene_ids.add(scene_id)
        feature_id = observation.get("feature_id")
        feature = features.get(feature_id) if isinstance(feature_id, str) else None
        if not feature:
            errors.append(f"{prefix}: unknown feature_id"); continue
        if observation.get("review") not in ("unverified", "uncertain", "verified"):
            errors.append(f"{prefix}: invalid review")
        for field in ("time_ms", "uncertainty_ms"):
            number = observation.get(field)
            if number is not None and (type(number) not in (int, float) or not math.isfinite(number) or number < 0):
                errors.append(f"{prefix}: {field} must be a finite nonnegative number")
        duration = report.get('parser', {}).get('duration_ms')
        time = observation.get('time_ms')
        if type(duration) in (int, float) and type(time) in (int, float) and time > duration:
            errors.append(f'{prefix}: time_ms is outside the replay duration')
        if not isinstance(observation.get('state'), str) or (observation.get("state") and observation["state"] not in feature["states"]):
            errors.append(f"{prefix}: unknown state")
        if not isinstance(observation.get('action'), str) or (observation.get("action") and observation["action"] not in feature["events"]):
            errors.append(f"{prefix}: unknown action")
        if not isinstance(observation.get('notes'), str):
            errors.append(f'{prefix}: notes must be text')
        key = observation.get("candidate_key")
        if key is not None and (not isinstance(key, str) or key not in candidates):
            errors.append(f"{prefix}: unknown candidate_key")
        lines = observation.get("evidence_lines")
        if not isinstance(lines, list) or any(type(line) is not int or line <= 0 or line > report['scan']['rows'] for line in lines):
            errors.append(f"{prefix}: evidence_lines must refer to scanned lines")
        if observation.get("review") == "verified" and (observation.get("time_ms") is None or
                observation.get("uncertainty_ms") is None or not observation.get("state") or not lines):
            errors.append(f"{prefix}: verified scenes need time, uncertainty, state and source evidence")
    return errors


def review_observations(value, report):
    errors = validate_observations(value, report)
    if errors:
        return {"valid": False, "errors": errors, "scenes": []}
    scenes = []
    for observation in value['observations']:
        time, uncertainty = observation.get('time_ms'), observation.get('uncertainty_ms')
        candidates = [c for c in report['candidates'] if not observation.get('candidate_key') or c['key'] == observation['candidate_key']]
        nearby, changes = [], []
        if time is not None and uncertainty is not None:
            for candidate in candidates:
                for sample in candidate['samples']:
                    if abs(sample['row']['time_ms'] - time) <= uncertainty:
                        nearby.append({'candidate_key': candidate['key'], **sample})
                for prop, summary in candidate.get('properties', {}).items():
                    for change in summary['changes']:
                        if abs(change['time_ms'] - time) <= uncertainty:
                            changes.append({'candidate_key': candidate['key'], 'property': prop, **change})
        scenes.append({'observation': observation, 'nearby_retained_samples': nearby[:100],
                       'nearby_samples_omitted': max(0, len(nearby) - 100),
                       'nearby_retained_property_changes': changes[:100],
                       'nearby_property_changes_omitted': max(0, len(changes) - 100),
                       'note': 'Time overlap is a review aid, not a feature binding. Retained samples may omit changes.'})
    return {'valid': True, 'errors': [], 'scenes': scenes,
            'note': 'Owner observations do not activate or verify a runtime decoder.'}


def private_output(path: Path, export_dir: Path):
    target, source = path.resolve(), export_dir.resolve()
    if target == source or (source.is_dir() and source in target.parents):
        raise ValueError("output must be outside the source export")
    for parent in (target, *target.parents):
        if (parent / ".git").exists():
            raise ValueError("private replay evidence cannot be written inside a Git repository")
    if target.exists():
        raise ValueError("output already exists; choose a new directory to preserve prior evidence")
    return target


def render_report(report):
    def escaped(value):
        return html.escape(json.dumps(value, indent=2, ensure_ascii=False))
    cards = ''.join('<details><summary>' + html.escape(c['key']) + ' — ' + str(c['rows']) + ' rows</summary><pre>' + escaped(c) + '</pre></details>' for c in report['candidates'])
    script = (Path(__file__).resolve().parents[2] / 'scripts' / 'feature_audit_review.js').read_text(encoding='utf-8')
    data = json.dumps({'report': report, 'worksheet': worksheet(report)}, ensure_ascii=False).replace('<', '\\u003c')
    return '<!doctype html><meta charset="utf-8"><title>Private map-feature audit</title><style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eee;padding:1rem}summary{cursor:pointer}details,fieldset{margin:1rem 0}label{display:block;margin:.5rem 0}input,select,textarea,button{font:inherit;max-width:100%}textarea{width:95%}button{margin:.5rem}</style><h1>Private map-feature audit</h1><p>' + html.escape(report['conclusion']) + '</p><p>Watch a scene, record absolute export milliseconds and uncertainty, then download your worksheet and run the review command. Keyword matches include unrelated abilities, audio and weapons. Changes here are saved only when you download.</p><h2>Observation worksheet</h2><div id="observations"></div><div id="observation-actions"></div><p id="review-message" role="status"></p><h2>Identity and coverage</h2><pre>' + escaped({k: v for k, v in report.items() if k != 'candidates'}) + '</pre><h2>Candidate evidence</h2>' + cards + '<script type="application/json" id="audit-data">' + data + '</script><script>' + script + '</script>'
