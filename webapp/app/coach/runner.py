"""One coach run: brief, model, checker, one repair, payload (docs/superpowers/specs/2026-10-12-ai-coach-design.md,
"Coach run"). The model call is passed in, so the whole flow is tested without a model."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.coach import brief as brief_module
from app.coach import checker, prompt, query
from app.services import coach as coach_service

PER_SIDE = 3
_STORED = ("polarity", "theme", "title", "body", "instances")


class RunError(Exception):
    pass


def _need_session(session) -> None:
    if not session:
        raise RunError("the model gave no session id, so a follow-up would start a fresh conversation")


def extract_points(text: str) -> list | None:
    """The `points` list of the first JSON object in the text that has one (entries as sent, objects or not)."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("points"), list):
            return list(value["points"])      # every entry keeps its place: repair indices must not shift
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
        _need_session(session)
        text, session = call_model(prompt.json_only_prompt(), session)
        points = extract_points(text)
    if points is None:
        raise RunError("the model returned no report")

    def check_one(p, log) -> list[str]:
        if not isinstance(p, dict):
            return ["the point is not an object"]
        try:
            return checker.check_point(p, log, snap, book.themes, slot)
        except Exception as error:      # noqa: BLE001 - a point the checker cannot read fails; the run goes on
            return [f"the checker could not read this point: {type(error).__name__}"]

    def checked(candidates: list) -> list[list[str]]:
        """Each candidate's problems, against the log as it can be reproduced now."""
        log = checker.trusted_log(query.read_log(run_dir), snap, flags)
        return [check_one(p, log) for p in candidates]

    def storable(p) -> bool:
        try:
            return not checker.shape_problems(p, snap, book.themes)
        except Exception:               # noqa: BLE001 - a point that cannot be read cannot be shown
            return False

    failed = [(i, p if isinstance(p, dict) else {}, problems)
              for i, (p, problems) in enumerate(zip(points, checked(points))) if problems]
    # The owner wants three and three even when some are weak: a side that came back short is asked for more.
    missing = {side: max(0, PER_SIDE - sum(1 for p in points if isinstance(p, dict) and p.get("polarity") == side))
               for side in checker.POLARITIES}
    unchecked: dict[int, list[str]] = {}        # points kept although their evidence did not check out
    dropped = 0
    if failed or (any(missing.values()) and session):
        _need_session(session)
        first_count = len(points)
        text, session = call_model(prompt.repair_prompt(failed, missing, first_count), session)
        fixes = {int(p["index"]): p for p in (extract_points(text) or [])
                 if isinstance(p, dict) and re.fullmatch(r"[0-9]+", str(p.get("index")))}
        offered = [fixes.get(i) for i, _, _ in failed]
        added = [fixes[i] for i in sorted(fixes) if i >= first_count]
        verdicts = iter(checked([f for f in offered if f is not None] + added))
        for (i, _, problems), fix in zip(failed, offered):
            fix_problems = next(verdicts) if fix is not None else None
            if fix is not None and not fix_problems:
                points[i] = fix
            elif fix is not None and storable(fix):
                points[i], unchecked[i] = fix, fix_problems
            elif storable(points[i]):
                unchecked[i] = problems
            else:
                points[i] = None
                dropped += 1
        for fix in added:
            fix_problems = next(verdicts)
            if not fix_problems or storable(fix):
                points.append(fix)
                if fix_problems:
                    unchecked[len(points) - 1] = fix_problems
    # Up to three a side, points whose evidence checked out first, shown in the order the model gave them.
    chosen, count = set(), {side: 0 for side in checker.POLARITIES}
    order = [i for i in range(len(points)) if i not in unchecked] + sorted(unchecked)
    for i in order:
        p = points[i]
        if p is None or count[p["polarity"]] >= PER_SIDE:
            continue
        count[p["polarity"]] += 1
        chosen.add(i)
    kept = []
    for i in sorted(chosen):
        p = points[i]
        kept.append({**{k: p.get(k) for k in _STORED},
                     "instances": [{k: v for k, v in inst.items() if k in ("round", "t", "t1", "caption", "evidence")}
                                   for inst in p["instances"]]})
    if not kept:
        raise RunError("no point could be stored; nothing to publish")
    payload = {"playbook_version": book.version, "model": model_name, "data_gaps": built["data_gaps"],
               "dropped_points": dropped, "points": kept,
               "unchecked": [{"title": points[i].get("title"), "problems": unchecked[i]}
                             for i in sorted(chosen) if i in unchecked]}
    try:
        coach_service.validate_payload(payload)     # the store's own rules, here, so a finished run is never lost to them
    except coach_service.ReportError as error:
        raise RunError(f"the checked report cannot be stored: {error}") from None
    return payload
