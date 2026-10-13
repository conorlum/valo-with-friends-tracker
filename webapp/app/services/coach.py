"""The AI coach's stored reports: write one, read them back, and the coaching page's data
(docs/superpowers/specs/2026-10-12-ai-coach-design.md). Off in demo mode, like the replay pages."""

from __future__ import annotations

from sqlalchemy import case, func

from app.config import settings
from app.models import Match, Player
from app.models.coach import CoachFeedback, CoachNote, CoachPoint, CoachReport
from app.services.replays import watchable_external_ids

KIND_MATCH = "match"
POLARITIES = ("good", "improve")
VERDICTS = ("right", "wrong", "badly_put")


class ReportError(ValueError):
    pass


def coach_enabled() -> bool:
    return not settings.demo_mode


def _text(value, name: str, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f"{name} must be a non-empty string")
    if limit and len(value) > limit:
        raise ReportError(f"{name} is longer than {limit} characters")
    return value.strip()


def validate_payload(payload: dict) -> None:
    _text(payload.get("playbook_version"), "playbook_version", 32)
    _text(payload.get("model"), "model", 64)
    if not isinstance(payload.get("points"), list):
        raise ReportError("points must be a list")
    for i, point in enumerate(payload["points"]):
        where = f"points[{i}]"
        if not isinstance(point, dict) or point.get("polarity") not in POLARITIES:
            raise ReportError(f"{where}.polarity must be one of {POLARITIES}")
        _text(point.get("theme"), f"{where}.theme", 32)
        _text(point.get("title"), f"{where}.title", 160)
        _text(point.get("body"), f"{where}.body")
        if not isinstance(point.get("instances"), list):
            raise ReportError(f"{where}.instances must be a list")
        for j, instance in enumerate(point["instances"]):
            if not isinstance(instance, dict) or not isinstance(instance.get("round"), int):
                raise ReportError(f"{where}.instances[{j}].round must be a whole number")
            for key in ("t", "t1"):
                if instance.get(key) is not None and not isinstance(instance[key], (int, float)):
                    raise ReportError(f"{where}.instances[{j}].{key} must be a number")


def store_report(db, match_id: int, player_id: int, payload: dict) -> CoachReport:
    validate_payload(payload)
    latest = (db.query(func.max(CoachReport.version))
              .filter(CoachReport.match_id == match_id, CoachReport.player_id == player_id,
                      CoachReport.kind == KIND_MATCH).scalar()) or 0
    report = CoachReport(match_id=match_id, player_id=player_id, kind=KIND_MATCH, version=latest + 1,
                         playbook_version=payload["playbook_version"].strip(), model=payload["model"].strip(),
                         data_gaps=payload.get("data_gaps") or {}, dropped_points=int(payload.get("dropped_points") or 0))
    db.add(report)
    db.flush()
    ordinal = {polarity: 0 for polarity in POLARITIES}
    for point in payload["points"]:
        ordinal[point["polarity"]] += 1
        db.add(CoachPoint(report_id=report.id, polarity=point["polarity"], ordinal=ordinal[point["polarity"]],
                          theme=point["theme"].strip(), title=point["title"].strip(), body=point["body"].strip(),
                          instances=point["instances"]))
    db.commit()
    return report


def _reports(db, match_id: int, player_id: int):
    return db.query(CoachReport).filter(CoachReport.match_id == match_id, CoachReport.player_id == player_id,
                                        CoachReport.kind == KIND_MATCH)


def versions(db, match_id: int, player_id: int) -> list[int]:
    return [v for (v,) in _reports(db, match_id, player_id).with_entities(CoachReport.version)
            .order_by(CoachReport.version)]


def get_report(db, match_id: int, player_id: int, version: int | None = None) -> CoachReport | None:
    query = _reports(db, match_id, player_id)
    if version is not None:
        exact = query.filter(CoachReport.version == version).one_or_none()
        if exact is not None:
            return exact
    return query.order_by(CoachReport.version.desc()).first()


def coached_players(db, match_id: int) -> list[tuple[int, str]]:
    rows = (db.query(Player.id, Player.display_name)
            .join(CoachReport, CoachReport.player_id == Player.id)
            .filter(CoachReport.match_id == match_id, CoachReport.kind == KIND_MATCH)
            .distinct().order_by(Player.display_name).all())
    return [(pid, name) for pid, name in rows]


def points(db, report_id: int) -> list[CoachPoint]:
    order = case((CoachPoint.polarity == "good", 0), else_=1)
    return db.query(CoachPoint).filter(CoachPoint.report_id == report_id).order_by(order, CoachPoint.ordinal).all()


def _instance(raw: dict) -> dict:
    return {"round": raw["round"], "t": raw.get("t"), "t1": raw.get("t1"), "caption": str(raw.get("caption") or "")}


def _point(point: CoachPoint, feedback: list[dict]) -> dict:
    return {"id": point.id, "theme": point.theme, "title": point.title, "body": point.body,
            "disputed": any(f["verdict"] == "wrong" for f in feedback),
            "instances": [_instance(raw) for raw in point.instances or [] if isinstance(raw, dict) and "round" in raw],
            "feedback": feedback}


def _feedback_by_point(db, point_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {pid: [] for pid in point_ids}
    if not point_ids:
        return out
    rows = (db.query(CoachFeedback, Player.display_name)
            .join(Player, Player.id == CoachFeedback.author_player_id)
            .filter(CoachFeedback.point_id.in_(point_ids)).order_by(CoachFeedback.id).all())
    for row, author in rows:
        out[row.point_id].append({"author": author, "verdict": row.verdict, "note": row.note or "",
                                  "handled": row.handled_at is not None})
    return out


def page_data(db, replay, player_id: int | None, version: int | None) -> dict:
    coached = coached_players(db, replay.match_id)
    data = {"match_uuid": str(replay.match_uuid).lower(), "team_coming_soon": True, "players": [], "report": None}
    if not coached:
        return data
    chosen = player_id if player_id in {pid for pid, _ in coached} else coached[0][0]
    data["players"] = [{"player_id": pid, "name": name, "selected": pid == chosen} for pid, name in coached]
    report = get_report(db, replay.match_id, chosen, version)
    rows = points(db, report.id)
    feedback = _feedback_by_point(db, [p.id for p in rows])
    shown = [(p.polarity, _point(p, feedback[p.id])) for p in rows]
    data["report"] = {
        "id": report.id, "version": report.version, "versions": versions(db, replay.match_id, chosen),
        "playbook_version": report.playbook_version, "model": report.model,
        "created": report.created_at.strftime("%Y-%m-%d") if report.created_at else "",
        "player_id": chosen, "player_name": dict(coached)[chosen],
        "data_gaps": report.data_gaps or {}, "dropped_points": report.dropped_points,
        "good": [p for polarity, p in shown if polarity == "good"],
        "improve": [p for polarity, p in shown if polarity == "improve"]}
    return data


def coached_external_ids(db, external_ids) -> set[str]:
    """The lower-cased external ids among these that have a report and a replay the viewer can open."""
    if not coach_enabled():
        return set()
    wanted = [e for e in external_ids if e]
    if not wanted:
        return set()
    with_report = {str(e).lower() for (e,) in
                   db.query(Match.external_id).join(CoachReport, CoachReport.match_id == Match.id)
                   .filter(Match.external_id.in_(wanted), CoachReport.kind == KIND_MATCH).distinct()}
    return with_report & watchable_external_ids(db, sorted(with_report)) if with_report else set()


def add_note(db, kind: str, body: str) -> CoachNote:
    note = CoachNote(kind=_text(kind, "kind", 16), body=_text(body, "body"), active=True)
    db.add(note)
    db.commit()
    return note


def active_notes(db, kind: str) -> list[str]:
    return [body for (body,) in db.query(CoachNote.body).filter(CoachNote.kind == kind, CoachNote.active.is_(True))
            .order_by(CoachNote.id)]


def set_note_active(db, note_id: int, active: bool) -> bool:
    note = db.get(CoachNote, note_id)
    if note is None:
        return False
    note.active = active
    db.commit()
    return True


NOTE_LIMIT = 2000


def add_feedback(db, match_id: int, point_id: int, author_player_id: int, verdict: str, note: str | None) -> CoachFeedback:
    if verdict not in VERDICTS:
        raise ReportError(f"verdict must be one of {VERDICTS}")
    note = (note or "").strip() or None
    if note and len(note) > NOTE_LIMIT:
        raise ReportError(f"a note is at most {NOTE_LIMIT} characters")
    owner = (db.query(CoachReport).join(CoachPoint, CoachPoint.report_id == CoachReport.id)
             .filter(CoachPoint.id == point_id, CoachReport.match_id == match_id).one_or_none())
    if owner is None:
        raise LookupError(f"point {point_id} is not in a report of match {match_id}")
    row = CoachFeedback(point_id=point_id, author_player_id=author_player_id, verdict=verdict, note=note)
    db.add(row)
    db.commit()
    return row


def report_of_point(db, point_id: int) -> CoachReport | None:
    return (db.query(CoachReport).join(CoachPoint, CoachPoint.report_id == CoachReport.id)
            .filter(CoachPoint.id == point_id).one_or_none())
