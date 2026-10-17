"""The AI coach's stored reports: write one, read them back, and the coaching page's data
(docs/superpowers/specs/2026-10-12-ai-coach-design.md). Off in demo mode, like the replay pages."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func

from app.config import settings
from app.models import Match, Player
from app.models.replay import Replay
from app.models.coach import CoachFeedback, CoachNote, CoachPoint, CoachReport
from app.services.replays import watchable_external_ids

KIND_MATCH = "match"
POLARITIES = ("good", "improve")
VERDICTS = ("right", "wrong", "correction", "badly_put")      # badly_put: point-level rows from before 0025
MOMENT_VERDICTS = ("right", "wrong", "correction")
FEEDBACK_SHOWN = 30         # rows per point on the page and in "Prompt out": anyone can pick any name to post under


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


def _usable(raw) -> bool:
    return isinstance(raw, dict) and "round" in raw


def _instance(index: int, raw: dict, feedback: list[dict]) -> dict:
    """`index` is the instance's position in the point's stored `instances`: what moment feedback is keyed on."""
    return {"index": index, "round": raw["round"], "t": raw.get("t"), "t1": raw.get("t1"),
            "caption": str(raw.get("caption") or ""), "feedback": [f for f in feedback if f["instance"] == index]}


def _point(point: CoachPoint, feedback: list[dict], viewer_id: int | None = None) -> dict:
    mine = {f["instance"]: f for f in feedback if viewer_id is not None and f["author_id"] == viewer_id
            and not f["handled"]}
    return {"id": point.id, "theme": point.theme, "title": point.title, "body": point.body,
            "disputed": any(f["verdict"] == "wrong" for f in feedback),
            "instances": [_instance(j, raw, feedback) for j, raw in enumerate(point.instances or []) if _usable(raw)],
            "feedback": [f for f in feedback if f["instance"] is None],
            "mine": mine}       # the viewer's own open rows by moment (None: overall), to fill the form with


def _feedback_by_point(db, point_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {pid: [] for pid in point_ids}
    if not point_ids:
        return out
    rows = (db.query(CoachFeedback, Player.display_name)
            .join(Player, Player.id == CoachFeedback.author_player_id)
            .filter(CoachFeedback.point_id.in_(point_ids)).order_by(CoachFeedback.id).all())
    for row, author in rows:
        out[row.point_id].append({"author": author, "author_id": row.author_player_id, "verdict": row.verdict,
                                  "note": row.note or "", "instance": row.instance_index,
                                  "handled": row.handled_at is not None})
    return {pid: found[-FEEDBACK_SHOWN:] for pid, found in out.items()}      # the newest, oldest first


def page_data(db, replay, player_id: int | None, version: int | None, viewer_id: int | None = None) -> dict:
    coached = coached_players(db, replay.match_id)
    data = {"match_uuid": str(replay.match_uuid).lower(), "team_coming_soon": True, "players": [], "report": None}
    if not coached:
        return data
    chosen = player_id if player_id in {pid for pid, _ in coached} else coached[0][0]
    data["players"] = [{"player_id": pid, "name": name, "selected": pid == chosen} for pid, name in coached]
    report = get_report(db, replay.match_id, chosen, version)
    rows = points(db, report.id)
    feedback = _feedback_by_point(db, [p.id for p in rows])
    shown = [(p.polarity, _point(p, feedback[p.id], viewer_id)) for p in rows]
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


def _clean_note(note: str | None) -> str | None:
    note = (note or "").strip() or None
    if note and len(note) > NOTE_LIMIT:
        raise ReportError(f"a note is at most {NOTE_LIMIT} characters")
    return note


def _point_of_match(db, match_id: int, point_id: int) -> CoachPoint:
    point = (db.query(CoachPoint).join(CoachReport, CoachPoint.report_id == CoachReport.id)
             .filter(CoachPoint.id == point_id, CoachReport.match_id == match_id).one_or_none())
    if point is None:
        raise LookupError(f"point {point_id} is not in a report of match {match_id}")
    return point


def _check_moment(point: CoachPoint, instance: int | None) -> None:
    raw = point.instances or []
    if instance is not None and not (0 <= instance < len(raw) and _usable(raw[instance])):
        raise ReportError(f"point {point.id} has no moment {instance}")


def _open_row(db, point_id: int, author_player_id: int, instance: int | None) -> CoachFeedback | None:
    at = CoachFeedback.instance_index.is_(None) if instance is None else CoachFeedback.instance_index == instance
    return (db.query(CoachFeedback)
            .filter(CoachFeedback.point_id == point_id, CoachFeedback.author_player_id == author_player_id, at,
                    CoachFeedback.handled_at.is_(None)).order_by(CoachFeedback.id.desc()).first())


def _put(db, point_id: int, author_player_id: int, instance: int | None, verdict: str | None, note: str | None,
         keep_verdict: bool = False) -> CoachFeedback:
    # One open row per author per point and moment: login has no password, so a second post replaces the first
    # instead of piling up on the page and in "Prompt out" (PR 132 review, finding 7). Handled rows are history.
    row = _open_row(db, point_id, author_player_id, instance)
    if row is None:
        row = CoachFeedback(point_id=point_id, author_player_id=author_player_id, instance_index=instance,
                            verdict=verdict, note=note)
        db.add(row)
    else:
        row.note = note
        if not keep_verdict:
            row.verdict = verdict
    return row


def add_feedback(db, match_id: int, point_id: int, author_player_id: int, verdict: str | None, note: str | None,
                 instance: int | None = None) -> CoachFeedback:
    """One row: about the moment `instance` of the point, or (None) the point as a whole. A verdict, a note, or
    both; a moment's verdict is one of MOMENT_VERDICTS."""
    if verdict is not None and verdict not in (VERDICTS if instance is None else MOMENT_VERDICTS):
        raise ReportError(f"verdict must be one of {VERDICTS if instance is None else MOMENT_VERDICTS}")
    note = _clean_note(note)
    if verdict is None and note is None:
        raise ReportError("feedback needs a verdict or a note")
    point = _point_of_match(db, match_id, point_id)
    _check_moment(point, instance)
    row = _put(db, point_id, author_player_id, instance, verdict, note)
    db.commit()
    return row


def save_point_feedback(db, match_id: int, point_id: int, author_player_id: int,
                        moments: dict[int, tuple[str | None, str | None]], overall: str | None) -> None:
    """The coach panel's form for one point, in one go: a verdict and note per moment, and an overall note. The
    form shows the author's open rows filled in, so a moment (or the overall note) sent blank removes their open
    row for it. An overall row from before migration 0025 keeps its verdict."""
    point = _point_of_match(db, match_id, point_id)
    cleaned = {}
    for instance, (verdict, note) in moments.items():
        _check_moment(point, instance)
        if verdict is not None and verdict not in MOMENT_VERDICTS:
            raise ReportError(f"verdict must be one of {MOMENT_VERDICTS}")
        cleaned[instance] = (verdict, _clean_note(note))
    entries = [*cleaned.items(), (None, (None, _clean_note(overall)))]
    for instance, (verdict, note) in entries:
        if verdict is None and note is None:
            row = _open_row(db, point_id, author_player_id, instance)
            if row is not None and instance is None and row.verdict is not None:
                row.note = None                         # an old overall row: its verdict is not on the form
            elif row is not None:
                db.delete(row)
        else:
            _put(db, point_id, author_player_id, instance, verdict, note, keep_verdict=instance is None)
    db.commit()


def report_of_point(db, point_id: int) -> CoachReport | None:
    return (db.query(CoachReport).join(CoachPoint, CoachPoint.report_id == CoachReport.id)
            .filter(CoachPoint.id == point_id).one_or_none())


def report_page_data(db, report_id: int) -> tuple[str, dict] | None:
    report = db.get(CoachReport, report_id)
    if report is None or report.match_id is None:
        return None
    replay = db.query(Replay).filter(Replay.match_id == report.match_id).one_or_none()
    if replay is None:
        return None
    data = page_data(db, replay, report.player_id, report.version)
    if data["report"] is None or data["report"]["id"] != report.id:
        return None
    return data["match_uuid"], data["report"]


def match_prompt_data(db, replay) -> tuple[list[dict], list[str]]:
    """For a whole match's "Prompt out": every report with open feedback, whatever its version, by player then
    version; and the names of the coached players who have none."""
    rows = (db.query(CoachReport.player_id, CoachReport.version)
            .join(CoachPoint, CoachPoint.report_id == CoachReport.id)
            .join(CoachFeedback, CoachFeedback.point_id == CoachPoint.id)
            .filter(CoachReport.match_id == replay.match_id, CoachReport.kind == KIND_MATCH,
                    CoachFeedback.handled_at.is_(None)).distinct().all())
    coached = coached_players(db, replay.match_id)
    order = {pid: i for i, (pid, _) in enumerate(coached)}
    reports = [page_data(db, replay, pid, version)["report"]
               for pid, version in sorted(rows, key=lambda row: (order[row[0]], row[1]))]
    return reports, [name for pid, name in coached if pid not in {p for p, _ in rows}]


def mark_handled(db, report_id: int) -> int:
    ids = [pid for (pid,) in db.query(CoachPoint.id).filter(CoachPoint.report_id == report_id)]
    if not ids:
        return 0
    count = (db.query(CoachFeedback).filter(CoachFeedback.point_id.in_(ids), CoachFeedback.handled_at.is_(None))
             .update({CoachFeedback.handled_at: datetime.now(timezone.utc)}, synchronize_session=False))
    db.commit()
    return count


def open_feedback(db) -> list[dict]:
    rows = (db.query(CoachReport.id, Replay.match_uuid, Player.display_name, func.count(CoachFeedback.id))
            .join(CoachPoint, CoachPoint.report_id == CoachReport.id)
            .join(CoachFeedback, CoachFeedback.point_id == CoachPoint.id)
            .join(Player, Player.id == CoachReport.player_id)
            .outerjoin(Replay, Replay.match_id == CoachReport.match_id)
            .filter(CoachFeedback.handled_at.is_(None))
            .group_by(CoachReport.id, Replay.match_uuid, Player.display_name).order_by(CoachReport.id).all())
    return [{"report_id": rid, "match_uuid": str(uuid).lower() if uuid else None, "player": name, "open": n}
            for rid, uuid, name, n in rows]
