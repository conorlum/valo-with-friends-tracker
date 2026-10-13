"""The AI coach's stored reports: write one, read them back, and the coaching page's data
(docs/superpowers/specs/2026-10-12-ai-coach-design.md). Off in demo mode, like the replay pages."""

from __future__ import annotations

from sqlalchemy import case, func

from app.config import settings
from app.models import Player
from app.models.coach import CoachFeedback, CoachPoint, CoachReport

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
