"""One coach run: brief, model, checker, one repair, payload (docs/superpowers/specs/2026-10-12-ai-coach-design.md,
"Coach run"). The model call is passed in, so the whole flow is tested without a model."""

from __future__ import annotations

import json
from pathlib import Path

from app.coach import brief as brief_module
from app.coach import checker, prompt, query
from app.services import coach as coach_service

PER_SIDE = 3
_STORED = ("polarity", "theme", "title", "body", "instances")


class RunError(Exception):
    pass


def extract_points(text: str) -> list[dict] | None:
    """The `points` list of the first JSON object in the text that has one."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("points"), list):
            return [p for p in value["points"] if isinstance(p, dict)]
        start = text.find("{", start + 1)
    return None


def _brief_with_flag_ids(snap, slot: int, run_dir: Path) -> tuple[dict, dict]:
    """The brief with each flag logged and given its id, and those flags by id as this process logged them:
    the checker trusts a flag entry only if it matches (checker.trusted_log)."""
    built = brief_module.build(snap, slot)
    logged = {}
    for r in built["rounds"]:
        for flag in r["flags"]:
            result = {"round": flag["round"], "t": flag["t"], "kind": flag["kind"], **flag["values"]}
            args = [flag["round"], flag["t"], flag["kind"]]
            flag["id"] = query.append(run_dir, "flag", args, result)
            logged[flag["id"]] = {"cmd": "flag", "args": args, "result": result}
    return built, logged


def generate(snap, slot: int, run_dir: Path, book, voice: list[str], call_model, model_name: str) -> dict:
    built, flags = _brief_with_flag_ids(snap, slot, run_dir)
    text, session = call_model(prompt.run_prompt(book, voice, built), None)
    points = extract_points(text)
    if points is None:
        text, session = call_model(prompt.json_only_prompt(), session)
        points = extract_points(text)
    if points is None:
        raise RunError("the model returned no report")

    def checked(candidates: list[dict]) -> list[list[str]]:
        """Each candidate's problems, against the log as it can be reproduced now."""
        log = checker.trusted_log(query.read_log(run_dir), snap, flags)
        return [checker.check_point(p, log, snap, book.themes, slot) for p in candidates]

    failed = [(i, p, problems) for i, (p, problems) in enumerate(zip(points, checked(points))) if problems]
    dropped = 0
    if failed:
        text, session = call_model(prompt.repair_prompt(failed), session)
        fixes = {int(p["index"]): p for p in (extract_points(text) or []) if str(p.get("index")).isdigit()}
        offered = [fixes.get(i) for i, _, _ in failed]
        verdicts = checked([f for f in offered if f is not None])
        passed = iter(not problems for problems in verdicts)
        for (i, _, _), fix in zip(failed, offered):
            if fix is not None and next(passed):
                points[i] = fix
            else:
                points[i] = None
                dropped += 1
    kept, count = [], {"good": 0, "improve": 0}
    for p in points:
        if p is None or count[p["polarity"]] >= PER_SIDE:
            continue
        count[p["polarity"]] += 1
        kept.append({**{k: p.get(k) for k in _STORED},
                     "instances": [{k: v for k, v in inst.items() if k in ("round", "t", "t1", "caption", "evidence")}
                                   for inst in p["instances"]]})
    if not kept:
        raise RunError("no point passed the evidence check; nothing to store")
    payload = {"playbook_version": book.version, "model": model_name, "data_gaps": built["data_gaps"],
               "dropped_points": dropped, "points": kept}
    try:
        coach_service.validate_payload(payload)     # the store's own rules, here, so a finished run is never lost to them
    except coach_service.ReportError as error:
        raise RunError(f"the checked report cannot be stored: {error}") from None
    return payload
