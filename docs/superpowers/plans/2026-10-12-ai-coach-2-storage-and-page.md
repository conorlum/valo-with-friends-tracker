# AI Coach 2: Storage and the Coaching Page - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store coaching reports and show them on a coaching page that is the replay viewer with a panel of points, each instance of which moves the replay to its round and second.

**Architecture:** Four new tables behind one migration and one service module. A new route renders the existing `replays/replay.html` with an extra `coach` context, so the page is the viewer's own code plus an included partial and a small script. A script stores a report from a JSON file in the exact shape plan 3's coach run will produce, so the page is reviewed with a hand-written report first.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Jinja2, plain JavaScript, pytest (SQLite in memory).

**Spec:** `docs/superpowers/specs/2026-10-12-ai-coach-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/ai-coach`, branch `worktree-ai-coach`. Paths are relative to `webapp/` unless they start with `docs/`.
- Run Python as `$PY`: `C:\Users\User\Documents\GitHub\valo-with-friends-tracker\webapp\.venv313\Scripts\python.exe`, from the worktree's `webapp/`.
- The repository is public: no credential, player name, match id or real report text in any committed file.
- Every coach route returns 404 when `settings.demo_mode` is on. The demo database never holds a report.
- Scoring models (`app/models/{match,round,kill_event,impact_score,player}.py`) and `app/scoring/` are never edited. New models go in `app/models/coach.py` with no relationship back-references into them.
- The coaching page is `replays/replay.html` with a panel. Do not copy the viewer's template or script.
- Anyone can read any report. Report text is rendered escaped; it is never marked safe.
- No heredocs in shell commands. Commit messages go in a scratch file, `git commit -F`, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Only the owner merges into `main`. Migration `0021` reaches the live database when the owner merges and Render deploys; nothing in this plan runs it against the friends database.
- Writing a report to the friends database publishes it. Do that only on the owner's word, in the step that says so.
- Local Postgres: `docker compose -p valomaths-private up -d` from `webapp/` (port 5433). Never plain `docker compose up`.

## Review Focus

1. Report text containing HTML (`<script>`, `&`) must appear as text. Test in Task 4.
2. A match whose report exists but whose replay is gone or unlinked must not get a Coach link that leads to a 404. Test in Task 3.
3. `?version=` or `?player=` that is not a number, or names a version or player with no report, falls back to the latest report and the first coached player; it never errors. Test in Task 3.
4. Storing a second report for the same match and player makes version 2 and leaves version 1 readable. Test in Task 2.
5. A stored point whose instances list is empty or whose instance lacks `t` still renders (as a point without links, or a link to the round start). Test in Task 4.

## File Structure

| File | Responsibility |
| --- | --- |
| `alembic/versions/0021_coach.py` | The four tables. |
| `app/models/coach.py` | `CoachReport`, `CoachPoint`, `CoachFeedback`, `CoachNote`. |
| `app/models/__init__.py` | Export them. |
| `app/services/coach.py` | Store, read, list, and the page's data. |
| `app/routers/coach.py` | `GET /replays/{match_uuid}/coach`. |
| `app/routers/replays.py` | Extract `viewer_context` from `replay_page`. |
| `app/main.py` | Include the router. |
| `app/templates/replays/_coach.html` | The panel. |
| `app/templates/replays/replay.html` | Include the panel and script when `coach` is set. |
| `app/static/js/coach.js` | Instance click to `viewer.showRound(n, t)`. |
| `app/static/css/style.css` | Panel styles. |
| `app/templates/matches/list.html`, `app/templates/sessions/detail.html`, their routers | The Coach column. |
| `scripts/coach_store_report.py` | Store a report from a JSON file. |
| `tests/coach/test_coach_store.py`, `test_coach_page.py`, `test_coach_column.py` | Tests. |

---

### Task 1: Tables and models

**Files:**
- Create: `alembic/versions/0021_coach.py`, `app/models/coach.py`
- Modify: `app/models/__init__.py`
- Test: `tests/coach/test_coach_store.py` (first test only)

**Interfaces:**
- Consumes: `app.db.Base`; `app.models.replay.JSONType`.
- Produces: `CoachReport(id, match_id, player_id, kind, version, playbook_version, model, data_gaps, dropped_points, created_at)`, `CoachPoint(id, report_id, polarity, ordinal, theme, title, body, instances)`, `CoachFeedback(id, point_id, author_player_id, verdict, note, created_at, handled_at)`, `CoachNote(id, kind, body, active, created_at)`. `tests/coach/conftest.py` fixture `db` and helper `coach_world(db) -> (replay, player_ids)`.

- [ ] **Step 1: Write the shared fixtures and the failing test**

`tests/coach/conftest.py`:

```python
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replays"))

from test_gap_patterns import TABLES, make_replay, players  # noqa: E402  (the replay tests' builders)

from app.db import Base  # noqa: E402
from app.models.coach import CoachFeedback, CoachNote, CoachPoint, CoachReport  # noqa: E402

COACH_TABLES = [CoachReport.__table__, CoachPoint.__table__, CoachFeedback.__table__, CoachNote.__table__]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES + COACH_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def world(db):
    """A linked, valid replay of a match whose slot i is player ids[i]."""
    ids = players(db)
    return make_replay(db, rounds=3, player_ids=ids), ids
```

`tests/coach/test_coach_store.py`:

```python
from app.models.coach import CoachFeedback, CoachPoint, CoachReport


def test_a_report_its_points_and_feedback_round_trip(db, world):
    replay, ids = world
    report = CoachReport(match_id=replay.match_id, player_id=ids[0], kind="match", version=1,
                         playbook_version="1", model="m", data_gaps={"2": ["control_failed"]}, dropped_points=0)
    db.add(report)
    db.flush()
    point = CoachPoint(report_id=report.id, polarity="improve", ordinal=1, theme="trading", title="T", body="B",
                       instances=[{"round": 2, "t": 41.5, "caption": "c", "evidence": ["q1"]}])
    db.add(point)
    db.flush()
    db.add(CoachFeedback(point_id=point.id, author_player_id=ids[1], verdict="wrong", note="n"))
    db.commit()
    stored = db.query(CoachPoint).one()
    assert stored.instances[0]["t"] == 41.5 and db.query(CoachFeedback).one().handled_at is None
    assert db.query(CoachReport).one().data_gaps == {"2": ["control_failed"]}
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/coach/test_coach_store.py -q` — Expected: FAIL, `No module named 'app.models.coach'`.

- [ ] **Step 3: Implement the models**

`app/models/coach.py`:

```python
"""The AI coach's tables (migration 0021; docs/superpowers/specs/2026-10-12-ai-coach-design.md, "Storage").

No relationship back-references into the scoring models, for the reason app/models/replay.py gives: those files
are scoring HASHED_SOURCES and are never edited. A report is immutable once stored: regenerating makes the next
`version` for the same match, player and kind, and feedback stays attached to the points it was written about."""

from datetime import datetime

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.replay import JSONType


class CoachReport(Base):
    __tablename__ = "coach_reports"
    __table_args__ = (
        CheckConstraint("kind IN ('match', 'cross_match', 'round')", name="ck_coach_reports_kind"),
        UniqueConstraint("match_id", "player_id", "kind", "version", name="uq_coach_reports_version"),
        Index("ix_coach_reports_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="match")
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    playbook_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    data_gaps: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    dropped_points: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CoachPoint(Base):
    __tablename__ = "coach_points"
    __table_args__ = (CheckConstraint("polarity IN ('good', 'improve')", name="ck_coach_points_polarity"),
                      Index("ix_coach_points_report", "report_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("coach_reports.id", ondelete="CASCADE"), nullable=False)
    polarity: Mapped[str] = mapped_column(String(8), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    theme: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"round": 7, "t": 41.5, "t1": 48.0 (optional), "caption": "...", "evidence": ["q3", "q4"]}, ...]
    instances: Mapped[list] = mapped_column(JSONType, nullable=False)


class CoachFeedback(Base):
    __tablename__ = "coach_feedback"
    __table_args__ = (CheckConstraint("verdict IN ('right', 'wrong', 'badly_put')", name="ck_coach_feedback_verdict"),
                      Index("ix_coach_feedback_point", "point_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("coach_points.id", ondelete="CASCADE"), nullable=False)
    author_player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CoachNote(Base):
    """Owner-written material the coach reads that must not be in the public repository (kind 'voice')."""

    __tablename__ = "coach_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

In `app/models/__init__.py`, add after the `app.models.replay` import block:

```python
from app.models.coach import CoachFeedback, CoachNote, CoachPoint, CoachReport
```

and add `"CoachReport", "CoachPoint", "CoachFeedback", "CoachNote",` to `__all__`.

- [ ] **Step 4: Write the migration**

`alembic/versions/0021_coach.py`:

```python
"""The AI coach's reports, points, feedback and notes. Additive; no existing table changes.

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), 'postgresql')


def upgrade():
    op.create_table('coach_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('match_id', sa.Integer(), sa.ForeignKey('matches.id', ondelete='CASCADE'), nullable=True),
        sa.Column('player_id', sa.Integer(), sa.ForeignKey('players.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('version', sa.SmallInteger(), nullable=False),
        sa.Column('playbook_version', sa.String(32), nullable=False),
        sa.Column('model', sa.String(64), nullable=False),
        sa.Column('data_gaps', JSON, nullable=True),
        sa.Column('dropped_points', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('match', 'cross_match', 'round')", name='ck_coach_reports_kind'),
        sa.UniqueConstraint('match_id', 'player_id', 'kind', 'version', name='uq_coach_reports_version'))
    op.create_index('ix_coach_reports_player', 'coach_reports', ['player_id'])
    op.create_table('coach_points',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('coach_reports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('polarity', sa.String(8), nullable=False),
        sa.Column('ordinal', sa.SmallInteger(), nullable=False),
        sa.Column('theme', sa.String(32), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('instances', JSON, nullable=False),
        sa.CheckConstraint("polarity IN ('good', 'improve')", name='ck_coach_points_polarity'))
    op.create_index('ix_coach_points_report', 'coach_points', ['report_id'])
    op.create_table('coach_feedback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('point_id', sa.Integer(), sa.ForeignKey('coach_points.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_player_id', sa.Integer(), sa.ForeignKey('players.id', ondelete='CASCADE'), nullable=False),
        sa.Column('verdict', sa.String(12), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("verdict IN ('right', 'wrong', 'badly_put')", name='ck_coach_feedback_verdict'))
    op.create_index('ix_coach_feedback_point', 'coach_feedback', ['point_id'])
    op.create_table('coach_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade():
    op.drop_table('coach_notes')
    op.drop_table('coach_feedback')
    op.drop_table('coach_points')
    op.drop_table('coach_reports')
```

- [ ] **Step 5: Run the test, then the migration on local Postgres**

Run: `$PY -m pytest tests/coach/test_coach_store.py -q` — Expected: 1 passed.

Run (local database only; confirm `.env` names port 5433 first): `docker compose -p valomaths-private up -d`, then `$PY -m alembic upgrade head`, then `$PY -m alembic downgrade 0020`, then `$PY -m alembic upgrade head`.
Expected: each command exits 0; `$PY -m alembic current` prints `0021 (head)`. The local database may be behind `origin/main`'s schema (it has been before): if `upgrade head` fails on an earlier migration, report that and stop; do not repair the local database in this task.

- [ ] **Step 6: Commit**

```bash
git add webapp/alembic/versions/0021_coach.py webapp/app/models/coach.py webapp/app/models/__init__.py webapp/tests/coach/conftest.py webapp/tests/coach/test_coach_store.py
git commit -F <scratch file: "Add the coach's tables">
```

---

### Task 2: Storing and reading reports

**Files:**
- Create: `app/services/coach.py`
- Test: `tests/coach/test_coach_store.py` (extend)

**Interfaces:**
- Consumes: the models of Task 1; `app.config.settings.demo_mode`; `Match`, `MatchPlayer`, `Player`.
- Produces:
  - `coach.coach_enabled() -> bool`
  - `coach.ReportError(ValueError)`
  - `coach.validate_payload(payload: dict) -> None` (raises `ReportError` naming the first problem)
  - `coach.store_report(db, match_id: int, player_id: int, payload: dict) -> CoachReport` (commits; next version)
  - `coach.get_report(db, match_id, player_id, version: int | None = None) -> CoachReport | None` (latest when None or unknown)
  - `coach.versions(db, match_id, player_id) -> list[int]` ascending
  - `coach.coached_players(db, match_id) -> list[tuple[int, str]]` `(player_id, display_name)` by name
  - `coach.points(db, report_id) -> list[CoachPoint]` good first, then improve, each by ordinal

The payload, which is also what plan 3's run produces:

```json
{"playbook_version": "1", "model": "name", "data_gaps": {"2": ["control_failed"]}, "dropped_points": 0,
 "points": [{"polarity": "good", "theme": "trading", "title": "...", "body": "...",
             "instances": [{"round": 7, "t": 41.5, "t1": 48.0, "caption": "...", "evidence": ["q3"]}]}]}
```

- [ ] **Step 1: Write the failing tests** (append to `tests/coach/test_coach_store.py`)

```python
import pytest

from app.services import coach


def payload(**over):
    point = {"polarity": "improve", "theme": "trading", "title": "Died alone", "body": "Body.",
             "instances": [{"round": 2, "t": 41.5, "caption": "c", "evidence": ["q1"]}]}
    base = {"playbook_version": "1", "model": "m", "data_gaps": {}, "dropped_points": 0,
            "points": [{**point, "polarity": "good", "title": "Good one"}, point]}
    return {**base, **over}


def test_storing_twice_makes_two_versions_and_keeps_the_first(db, world):
    replay, ids = world
    first = coach.store_report(db, replay.match_id, ids[0], payload())
    second = coach.store_report(db, replay.match_id, ids[0], payload(playbook_version="2"))
    assert (first.version, second.version) == (1, 2)
    assert coach.versions(db, replay.match_id, ids[0]) == [1, 2]
    assert coach.get_report(db, replay.match_id, ids[0]).playbook_version == "2"
    assert coach.get_report(db, replay.match_id, ids[0], 1).id == first.id
    assert coach.get_report(db, replay.match_id, ids[0], 99).id == second.id


def test_points_come_back_good_first_in_order(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    assert [(p.polarity, p.ordinal, p.title) for p in coach.points(db, report.id)] == [
        ("good", 1, "Good one"), ("improve", 1, "Died alone")]


def test_coached_players_lists_each_player_once(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[3], payload())
    assert sorted(pid for pid, _ in coach.coached_players(db, replay.match_id)) == sorted([ids[0], ids[3]])
    assert coach.get_report(db, replay.match_id, ids[5]) is None


@pytest.mark.parametrize("change, message", [
    ({"points": [{"polarity": "bad", "theme": "t", "title": "x", "body": "b", "instances": []}]}, "polarity"),
    ({"points": [{"polarity": "good", "theme": "t", "title": "", "body": "b", "instances": []}]}, "title"),
    ({"points": [{"polarity": "good", "theme": "t", "title": "x", "body": "b", "instances": [{"t": 1}]}]}, "round"),
    ({"playbook_version": ""}, "playbook_version"),
    ({"points": "nope"}, "points"),
])
def test_a_malformed_payload_is_refused_and_nothing_is_stored(db, world, change, message):
    replay, ids = world
    with pytest.raises(coach.ReportError, match=message):
        coach.store_report(db, replay.match_id, ids[0], payload(**change))
    assert coach.versions(db, replay.match_id, ids[0]) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_coach_store.py -q` — Expected: FAIL, `cannot import name 'coach' from 'app.services'`.

- [ ] **Step 3: Implement**

`app/services/coach.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach/test_coach_store.py -q` — Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add webapp/app/services/coach.py webapp/tests/coach/test_coach_store.py
git commit -F <scratch file: "Store and read coach reports">
```

---

### Task 3: The page's data, the route and the Coach column

**Files:**
- Modify: `app/services/coach.py`, `app/routers/replays.py` (`replay_page`), `app/main.py`, `app/routers/matches.py:36-43`, `app/routers/sessions.py:~78`, `app/templates/matches/list.html`, `app/templates/sessions/detail.html`
- Create: `app/routers/coach.py`
- Test: `tests/coach/test_coach_page.py`, `tests/coach/test_coach_column.py`

**Interfaces:**
- Consumes: Task 2's service; `app.services.replays.find_replay`, `.watchable_external_ids(db, external_ids) -> set[str]`; `app.routers.replays.replay_page`'s body.
- Produces:
  - `coach.page_data(db, replay, player_id: int | None, version: int | None) -> dict`:
    ```
    {"match_uuid", "team_coming_soon": True,
     "players": [{"player_id", "name", "selected": bool}],
     "report": None | {"id", "version", "versions": [..], "playbook_version", "model", "created": "YYYY-MM-DD",
                       "player_id", "player_name", "data_gaps": {...}, "dropped_points": n,
                       "good": [point...], "improve": [point...]}}
    point = {"id", "theme", "title", "body", "disputed": bool,
             "instances": [{"round", "t", "t1", "caption"}],
             "feedback": [{"author", "verdict", "note", "handled": bool}]}
    ```
  - `coach.coached_external_ids(db, external_ids) -> set[str]` (lower-cased; only matches that are also watchable)
  - `replays.viewer_context(request, db, replay) -> dict` (the template context `replay_page` renders)
  - `GET /replays/{match_uuid}/coach?player=<player id>&version=<n>`

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_coach_page.py`:

```python
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.models.coach import CoachFeedback
from app.routers import coach as routes
from app.services import coach
from tests.coach.test_coach_store import payload


def request():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "session": {}, "query_string": b""})


@pytest.fixture
def captured(monkeypatch):
    seen = {}
    monkeypatch.setattr(routes.templates, "TemplateResponse",
                        lambda req, name, context, **kw: seen.update(name=name, context=context) or seen)
    monkeypatch.setattr(routes, "viewer_context", lambda req, db, replay: {"replay": {"map_name": replay.map_name}})
    return seen


def test_page_data_shows_the_latest_report_of_the_first_coached_player(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    coach.store_report(db, replay.match_id, ids[0], payload(playbook_version="2"))
    data = coach.page_data(db, replay, None, None)
    assert data["report"]["version"] == 2 and data["report"]["versions"] == [1, 2]
    assert [p["title"] for p in data["report"]["good"]] == ["Good one"]
    assert data["report"]["improve"][0]["instances"] == [{"round": 2, "t": 41.5, "t1": None, "caption": "c"}]
    assert data["players"][0]["selected"] is True and data["team_coming_soon"] is True


def test_a_bad_or_unknown_player_or_version_falls_back(db, world):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    for player, version in ((ids[7], None), (None, 99), (-1, -1)):
        assert coach.page_data(db, replay, player, version)["report"]["version"] == 1


def test_a_match_with_no_report_has_an_empty_page(db, world):
    replay, _ = world
    assert coach.page_data(db, replay, None, None) == {"match_uuid": str(replay.match_uuid).lower(),
                                                       "team_coming_soon": True, "players": [], "report": None}


def test_a_point_with_a_wrong_verdict_is_disputed_and_keeps_its_feedback(db, world):
    replay, ids = world
    report = coach.store_report(db, replay.match_id, ids[0], payload())
    point = coach.points(db, report.id)[1]
    db.add(CoachFeedback(point_id=point.id, author_player_id=ids[1], verdict="wrong", note="not what happened"))
    db.commit()
    shown = coach.page_data(db, replay, None, None)["report"]["improve"][0]
    assert shown["disputed"] is True
    assert shown["feedback"][0]["verdict"] == "wrong" and shown["feedback"][0]["note"] == "not what happened"
    assert coach.page_data(db, replay, None, None)["report"]["good"][0]["disputed"] is False


def test_an_instance_without_a_time_or_caption_still_reads(db, world):
    replay, ids = world
    body = payload()
    body["points"][0]["instances"] = [{"round": 3}]
    body["points"][1]["instances"] = []
    coach.store_report(db, replay.match_id, ids[0], body)
    report = coach.page_data(db, replay, None, None)["report"]
    assert report["good"][0]["instances"] == [{"round": 3, "t": None, "t1": None, "caption": ""}]
    assert report["improve"][0]["instances"] == []


def test_the_route_renders_the_viewer_template_with_the_coach_data(db, world, captured):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    routes.coach_page(request(), str(replay.match_uuid), player="x", version="y", db=db)
    assert captured["name"] == "replays/replay.html"
    assert captured["context"]["coach"]["report"]["version"] == 1


def test_the_route_404s_in_demo_mode_and_for_an_unknown_or_unlinked_replay(db, world, captured, monkeypatch):
    replay, _ = world
    with pytest.raises(HTTPException) as missing:
        routes.coach_page(request(), "00000000-0000-4000-8000-000000000000", player=None, version=None, db=db)
    assert missing.value.status_code == 404
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(HTTPException) as demo:
        routes.coach_page(request(), str(replay.match_uuid), player=None, version=None, db=db)
    assert demo.value.status_code == 404
```

`tests/coach/test_coach_column.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replays"))

from test_gap_patterns import make_replay, players  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import Match  # noqa: E402
from app.services import coach  # noqa: E402
from tests.coach.test_coach_store import payload  # noqa: E402


def external_id(db, replay):
    return db.get(Match, replay.match_id).external_id


def test_only_matches_with_a_report_and_a_watchable_replay_are_coached(db, world, monkeypatch):
    replay, ids = world
    other = make_replay(db, rounds=2, player_ids=players(db))
    coach.store_report(db, replay.match_id, ids[0], payload())
    both = [external_id(db, replay), external_id(db, other)]
    monkeypatch.setattr(coach, "watchable_external_ids", lambda db, ids_: {e.lower() for e in ids_})
    assert coach.coached_external_ids(db, both) == {both[0].lower()}
    # the report exists but the replay cannot be watched: no link
    monkeypatch.setattr(coach, "watchable_external_ids", lambda db, ids_: set())
    assert coach.coached_external_ids(db, both) == set()


def test_nothing_is_coached_in_demo_mode(db, world, monkeypatch):
    replay, ids = world
    coach.store_report(db, replay.match_id, ids[0], payload())
    monkeypatch.setattr(settings, "demo_mode", True)
    assert coach.coached_external_ids(db, [external_id(db, replay)]) == set()
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_coach_page.py tests/coach/test_coach_column.py -q` — Expected: FAIL, `cannot import name 'coach' from 'app.routers'`.

- [ ] **Step 3: Extend the service**

Append to `app/services/coach.py` (and add `from app.models import Match` and `from app.services.replays import watchable_external_ids` to its imports):

```python
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
```

- [ ] **Step 4: Extract the viewer's context and add the route**

In `app/routers/replays.py`, split `replay_page` so its context can be reused. Replace the function with:

```python
def viewer_context(request: Request, db: Session, replay) -> dict:
    """The context replays/replay.html renders for this replay (also the coaching page's, app/routers/coach.py)."""
    context = replay_service.page_context(db, replay)
    start_round = request.query_params.get("round")
    start = int(start_round) if start_round and start_round.isdigit() else 1
    if start not in context["match"]["rounds"]:
        start = 1
    linked = context["match"]["linked"]
    return {
        "replay": {"map_name": replay.map_name, "round_numbers": context["match"]["rounds"],
                   "title_suffix": None, "linked": context["match"]["linked"],
                   "match_url": (f"/matches/{context['match']['external_id']}" if context["match"]["linked"] else None),
                   "reason": context["match"].get("reason")},
        "linked": linked, "replay_data": context, "start_round": start,
        "start_t": start_time(request.query_params.get("t")),
        "control": context["match"]["control"] if linked else None,
        "gaps": bool(linked and context["match"].get("gaps"))}


@router.get("/replays/{match_uuid}")
def replay_page(request: Request, match_uuid: str, db: Session = Depends(get_db)):
    replay = _replay_or_404(db, match_uuid)
    return templates.TemplateResponse(request, "replays/replay.html", viewer_context(request, db, replay))
```

Run the viewer's existing tests to prove nothing moved: `$PY -m pytest tests/replays/test_replay_viewer.py tests/replays/test_viewer_annotations.py -q` — Expected: same result as before the edit (run them once before editing to know).

`app/routers/coach.py`:

```python
"""The coaching page: the replay viewer with a coach panel (docs/superpowers/specs/2026-10-12-ai-coach-design.md).

    GET /replays/{match_uuid}/coach?player=<player id>&version=<n>

A linked replay only; 404 in demo mode. A match with no report renders the viewer with an empty panel."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.replays import UUID_SHAPE, viewer_context
from app.services import coach as coach_service
from app.services import replays as replay_service
from app.templates import templates

router = APIRouter(tags=["coach"])


def _whole(value) -> int | None:
    return int(value) if isinstance(value, str) and value.isdigit() else None


def linked_replay_or_404(db: Session, match_uuid: str):
    if not coach_service.coach_enabled() or not UUID_SHAPE.match(match_uuid):
        raise HTTPException(status_code=404)
    replay = replay_service.find_replay(db, match_uuid)
    if replay is None or replay.match_id is None or replay.link_status != "linked":
        raise HTTPException(status_code=404)
    return replay


@router.get("/replays/{match_uuid}/coach")
def coach_page(request: Request, match_uuid: str, player: str | None = None, version: str | None = None,
               db: Session = Depends(get_db)):
    replay = linked_replay_or_404(db, match_uuid)
    context = viewer_context(request, db, replay)
    context["coach"] = coach_service.page_data(db, replay, _whole(player), _whole(version))
    return templates.TemplateResponse(request, "replays/replay.html", context)
```

In `app/main.py`, add `coach` to the `from app.routers import (...)` list and add `app.include_router(coach.router)` on the line **before** `app.include_router(replays.router)`.

- [ ] **Step 5: Add the Coach column**

`app/routers/matches.py`: import `from app.services.coach import coached_external_ids` and add to the context dict `"coached": coached_external_ids(db, [m.external_id for m in matches]),`.

`app/routers/sessions.py`: same import, and add `"coached": coached_external_ids(db, [m.external_id for m in session.matches]),` beside `"watchable"`.

`app/templates/matches/list.html`: after the `<th>Watch</th>{% endif %}` header cell add

```html
{% if coached is defined and coached %}
      <th>Coach</th>{% endif %}
```

after the watch `<td>...</td>{% endif %}` body cell add

```html
{% if coached is defined and coached %}
      <td class="watch-cell">{% if match.external_id and match.external_id.lower() in coached %}<a class="watch-link" href="/replays/{{ match.external_id.lower() }}/coach" onclick="event.stopPropagation()">Coach</a>{% else %}<span class="muted" title="No coaching report for this match">&mdash;</span>{% endif %}</td>{% endif %}
```

and change the empty row's colspan to `{{ 2 + (1 if show_watch else 0) + (1 if (coached is defined and coached) else 0) }}`.

`app/templates/sessions/detail.html`: the same header cell after its `<th>Watch</th>{% endif %}` (line ~338) and the same body cell after its watch cell (line ~354). Read the surrounding rows first and keep any colspan in that table consistent.

- [ ] **Step 6: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.
Run: `$PY -m pytest tests -q -k "match_list or session or replay_viewer"` — Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add webapp/app/services/coach.py webapp/app/routers/coach.py webapp/app/routers/replays.py webapp/app/main.py webapp/app/routers/matches.py webapp/app/routers/sessions.py webapp/app/templates/matches/list.html webapp/app/templates/sessions/detail.html webapp/tests/coach
git commit -F <scratch file: "Add the coaching page route and the Coach column">
```

---

### Task 4: The coach panel

**Files:**
- Create: `app/templates/replays/_coach.html`, `app/static/js/coach.js`
- Modify: `app/templates/replays/replay.html`, `app/static/css/style.css`
- Test: `tests/coach/test_coach_panel.py`

**Interfaces:**
- Consumes: `coach` context from Task 3 (`page_data`'s shape); the viewer's globals set in `replay.html`: `window.viewerReady` (a promise) and `window.viewer.showRound(n, t)` (`app/static/js/replay.js:1227`).
- Produces: markup hooks plan 3 extends: each point is `<article class="coach-point" data-coach-point="<id>">` containing `<div class="coach-feedback" data-coach-feedback>`; the panel root is `<section class="coach" data-coach data-coach-uuid data-coach-report>`.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_coach_panel.py`:

```python
from app.templates import templates


def point(**over):
    base = {"id": 7, "theme": "trading", "title": "Died alone on B", "body": "You were 30 m from help.",
            "disputed": False, "feedback": [],
            "instances": [{"round": 4, "t": 41.5, "t1": None, "caption": "first death"},
                          {"round": 9, "t": 12.0, "t1": 20.0, "caption": "again"}]}
    return {**base, **over}


def coach(**over):
    report = {"id": 3, "version": 2, "versions": [1, 2], "playbook_version": "5", "model": "m", "created": "2026-10-12",
              "player_id": 11, "player_name": "Owner#1", "data_gaps": {"6": ["control_failed"]}, "dropped_points": 1,
              "good": [point(id=1, title="Good trade")], "improve": [point()]}
    base = {"match_uuid": "abc", "team_coming_soon": True,
            "players": [{"player_id": 11, "name": "Owner#1", "selected": True},
                        {"player_id": 12, "name": "Friend#2", "selected": False}], "report": report}
    return {**base, **over}


def html(data):
    return templates.env.get_template("replays/_coach.html").render(coach=data)


def test_every_instance_is_a_link_carrying_its_round_and_time():
    out = html(coach())
    assert out.count('data-coach-round="4" data-coach-t="41.5"') == 2      # the good point and the improve point
    assert 'data-coach-round="9" data-coach-t="12.0"' in out
    assert "R4 0:41" in out and "R9 0:12" in out and "first death" in out


def test_the_report_states_its_version_playbook_and_gaps():
    out = html(coach())
    assert "playbook 5" in out and "Round 6" in out and "control_failed" in out
    assert "1 point was dropped" in out
    assert 'href="/replays/abc/coach?player=11&amp;version=1"' in out


def test_the_team_toggle_is_disabled_and_says_coming_soon():
    out = html(coach())
    assert "Coming soon" in out and "data-coach-team disabled" in out


def test_a_disputed_point_stays_and_is_marked():
    data = coach()
    data["report"]["improve"] = [point(disputed=True, feedback=[{"author": "Friend#2", "verdict": "wrong",
                                                                 "note": "not so", "handled": False}])]
    out = html(data)
    assert "Disputed" in out and "Died alone on B" in out and "not so" in out


def test_report_text_is_escaped():
    data = coach()
    data["report"]["improve"] = [point(title="<script>alert(1)</script>", body="a & b <b>x</b>")]
    out = html(data)
    assert "<script>alert(1)</script>" not in out and "&lt;script&gt;" in out and "a &amp; b" in out


def test_a_point_without_instances_and_an_instance_without_a_time_still_render():
    data = coach()
    data["report"]["good"] = [point(instances=[])]
    data["report"]["improve"] = [point(instances=[{"round": 3, "t": None, "t1": None, "caption": ""}])]
    out = html(data)
    assert 'data-coach-round="3" data-coach-t=""' in out and "R3" in out


def test_no_report_shows_the_empty_state():
    out = html(coach(report=None, players=[]))
    assert "No coaching report for this match yet" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_coach_panel.py -q` — Expected: FAIL, `TemplateNotFound: replays/_coach.html`.

- [ ] **Step 3: Write the panel**

`app/templates/replays/_coach.html`:

```html
{# The coach panel on the coaching page (app/routers/coach.py). Context: `coach` (app/services/coach.py
   page_data). Each instance is a button carrying its round and second; coach.js moves the viewer there.
   Report text is model-written: rendered escaped, never |safe. #}
{% macro clock(i) %}R{{ i.round }}{% if i.t is not none %} {{ "%d:%02d" | format((i.t | int) // 60, (i.t | int) % 60) }}{% endif %}{% endmacro %}
{% macro point_card(p) %}
<article class="coach-point{% if p.disputed %} is-disputed{% endif %}" data-coach-point="{{ p.id }}">
  <h4>{{ p.title }} <span class="coach-theme">{{ p.theme }}</span>{% if p.disputed %} <span class="coach-disputed">Disputed</span>{% endif %}</h4>
  <p>{{ p.body }}</p>
  {% if p.instances %}<ul class="coach-instances">
    {% for i in p.instances %}<li><button type="button" class="coach-instance" data-coach-round="{{ i.round }}" data-coach-t="{{ i.t if i.t is not none else '' }}">{{ clock(i) }}</button>{% if i.caption %} <span>{{ i.caption }}</span>{% endif %}</li>
    {% endfor %}</ul>{% endif %}
  <div class="coach-feedback" data-coach-feedback>
    {% for f in p.feedback %}<p class="coach-feedback-row"><strong>{{ f.author }}</strong>: {{ f.verdict | replace("_", " ") }}{% if f.note %} &ndash; {{ f.note }}{% endif %}{% if f.handled %} <span class="muted">(handled)</span>{% endif %}</p>
    {% endfor %}
  </div>
</article>
{% endmacro %}
<section class="coach" data-coach data-coach-uuid="{{ coach.match_uuid }}"{% if coach.report %} data-coach-report="{{ coach.report.id }}"{% endif %}>
  <header class="coach-header">
    <h2>Coach</h2>
    <div class="coach-mode" role="group" aria-label="Report type">
      <button type="button" class="replay-button" aria-pressed="true">Player</button>
      <button type="button" class="replay-button" data-coach-team disabled title="Coming soon">Team</button>
      {% if coach.team_coming_soon %}<span class="coach-soon">Coming soon</span>{% endif %}
    </div>
    {% if coach.players %}<nav class="coach-players" aria-label="Coached players">
      {% for p in coach.players %}<a href="/replays/{{ coach.match_uuid }}/coach?player={{ p.player_id }}"{% if p.selected %} aria-current="true"{% endif %}>{{ p.name }}</a>
      {% endfor %}</nav>{% endif %}
  </header>
  {% if not coach.report %}
  <p class="coach-empty">No coaching report for this match yet.</p>
  {% else %}{% set r = coach.report %}
  <p class="coach-meta">{{ r.player_name }} &middot; version {{ r.version }} &middot; playbook {{ r.playbook_version }} &middot; {{ r.created }}
    {% for v in r.versions if v != r.version %} &middot; <a href="/replays/{{ coach.match_uuid }}/coach?player={{ r.player_id }}&amp;version={{ v }}">version {{ v }}</a>{% endfor %}</p>
  {% if r.data_gaps or r.dropped_points %}<p class="coach-gaps">
    {% if r.dropped_points %}{{ r.dropped_points }} point{{ " was" if r.dropped_points == 1 else "s were" }} dropped because the evidence did not check out.{% endif %}
    {% if r.data_gaps %}Data missing when this was written: {% for key, names in r.data_gaps | dictsort %}{{ "Match" if key == "match" else "Round " ~ key }} ({{ names | join(", ") }}){{ "; " if not loop.last }}{% endfor %}.{% endif %}
  </p>{% endif %}
  <div class="coach-columns">
    <div><h3>Did well</h3>{% for p in r.good %}{{ point_card(p) }}{% else %}<p class="muted">None in this version.</p>{% endfor %}</div>
    <div><h3>To improve</h3>{% for p in r.improve %}{{ point_card(p) }}{% else %}<p class="muted">None in this version.</p>{% endfor %}</div>
  </div>
  {% endif %}
</section>
```

- [ ] **Step 4: Wire it into the viewer page**

In `app/templates/replays/replay.html`:

- change the title block to `{% block title %}{{ replay.map_name }} {{ "coaching" if coach is defined and coach else "replay" }} &ndash; {{ site_name }}{% endblock %}`
- directly before `{% include "replays/_player.html" %}` add `{% if coach is defined and coach %}{% include "replays/_coach.html" %}{% endif %}`
- directly before `{% endblock %}` at the end add `{% if coach is defined and coach %}<script src="/static/js/coach.js?v={{ static_version() }}"></script>{% endif %}`

`app/static/js/coach.js`:

```javascript
// The coaching page (app/templates/replays/_coach.html): an instance moves the replay to its round and second.
// The viewer is the replay page's own (window.viewer, ready when window.viewerReady resolves).
(function () {
  "use strict";
  var root = document.querySelector("[data-coach]");
  if (!root) return;
  root.addEventListener("click", function (event) {
    var button = event.target.closest("[data-coach-round]");
    if (!button) return;
    var round = parseInt(button.getAttribute("data-coach-round"), 10);
    var t = parseFloat(button.getAttribute("data-coach-t"));
    if (!isFinite(round) || !window.viewerReady) return;
    window.viewerReady.then(function () {
      return isFinite(t) ? window.viewer.showRound(round, t) : window.viewer.showRound(round);
    }).then(function () {
      var stage = document.querySelector("[data-replay]");
      if (stage && stage.scrollIntoView) stage.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }).catch(function () { /* a round that fails to load shows the viewer's own error */ });
  });
})();
```

Read `ReplayViewer.prototype.showRound` (`app/static/js/replay.js:1227`) before relying on its second argument: confirm `t` is a seek time in round seconds and that it returns a promise. If it does not seek, use `showRound(round).then(function () { window.viewer.seek(t); })`, which is what `replay.html` already does for `?t=`.

Append to `app/static/css/style.css` (use the file's existing custom properties; read the `.replay-*` rules near line 1687 for the names in use):

```css
/* The coach panel (replays/_coach.html) */
.coach { margin: 0 0 1rem; }
.coach-header { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; }
.coach-header h2 { margin: 0; }
.coach-soon, .coach-theme { color: var(--ink-muted); font-size: 0.85em; font-weight: 400; }
.coach-players a { margin-right: 0.75rem; }
.coach-players a[aria-current] { font-weight: 700; }
.coach-meta, .coach-gaps { color: var(--ink-muted); margin: 0.25rem 0; }
.coach-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: 1rem; }
.coach-point { border-top: 1px solid var(--ink-muted); padding: 0.5rem 0; }
.coach-point h4 { margin: 0 0 0.25rem; }
.coach-instances { list-style: none; margin: 0.25rem 0; padding: 0; }
.coach-instance { font: inherit; cursor: pointer; }
.coach-disputed { font-size: 0.85em; font-weight: 700; }
```

- [ ] **Step 5: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.

- [ ] **Step 6: Commit**

```bash
git add webapp/app/templates/replays/_coach.html webapp/app/templates/replays/replay.html webapp/app/static/js/coach.js webapp/app/static/css/style.css webapp/tests/coach/test_coach_panel.py
git commit -F <scratch file: "Add the coach panel to the replay viewer">
```

---

### Task 5: Store a report from a file, and look at the page

**Files:**
- Create: `scripts/coach_store_report.py`
- Test: `tests/coach/test_coach_store_script.py`

**Interfaces:**
- Consumes: `coach.store_report`, `coach.ReportError`; `Replay`, `MatchPlayer`, `Player`.
- Produces: `coach_store_report.resolve(db, match_uuid: str, player: str) -> tuple[int, int]` (`match_id`, `player_id`; raises `ValueError` listing the match's players); CLI `scripts/coach_store_report.py <match uuid> "<player>" <report.json>` printing the page URL path. Plan 3's run calls `resolve` and `coach.store_report` directly.

- [ ] **Step 1: Write the failing tests**

`tests/coach/test_coach_store_script.py`:

```python
import pytest

from app.models import Player
from scripts import coach_store_report as script


def test_a_player_is_resolved_by_display_name_within_the_match(db, world):
    replay, ids = world
    name = db.get(Player, ids[2]).display_name
    assert script.resolve(db, str(replay.match_uuid), name.upper()) == (replay.match_id, ids[2])


def test_a_player_not_in_the_match_is_refused_with_the_names(db, world):
    replay, ids = world
    with pytest.raises(ValueError, match=db.get(Player, ids[0]).display_name):
        script.resolve(db, str(replay.match_uuid), "someone else")


def test_an_unknown_or_unlinked_match_is_refused(db, world):
    with pytest.raises(ValueError, match="no linked replay"):
        script.resolve(db, "00000000-0000-4000-8000-000000000000", "x")
```


- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/coach/test_coach_store_script.py -q` — Expected: FAIL, no module `scripts.coach_store_report`.

- [ ] **Step 3: Implement**

`scripts/coach_store_report.py`:

```python
"""Stores one coaching report from a JSON file (the payload app/services/coach.py validate_payload accepts).

Local database (reads .env):
    $PY scripts/coach_store_report.py <match uuid> "<player>" <report.json>
Friends database (PUBLISHES the report on the live site):
    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_store_report.py <match uuid> "<player>" <report.json>

Each run adds the next version for that match and player; nothing is overwritten. Exits 2 when refused."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def resolve(db, match_uuid: str, player: str) -> tuple[int, int]:
    from app.models import MatchPlayer, Player
    from app.models.replay import Replay

    replay = (db.query(Replay).filter(Replay.match_uuid == match_uuid.lower(), Replay.link_status == "linked",
                                      Replay.match_id.isnot(None)).one_or_none())
    if replay is None:
        raise ValueError(f"no linked replay for {match_uuid}")
    rows = (db.query(Player.id, Player.display_name).join(MatchPlayer, MatchPlayer.player_id == Player.id)
            .filter(MatchPlayer.match_id == replay.match_id).all())
    wanted = player.strip().lower()
    found = [pid for pid, name in rows if name.lower() == wanted]
    if len(found) != 1:
        raise ValueError(f"{player!r} is not a player of this match; players: " + ", ".join(sorted(n for _, n in rows)))
    return replay.match_id, found[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("player")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services import coach

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        try:
            match_id, player_id = resolve(db, args.match_uuid, args.player)
            report = coach.store_report(db, match_id, player_id, payload)
        except (ValueError, coach.ReportError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
        print(f"stored version {report.version}: /replays/{args.match_uuid.lower()}/coach?player={player_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/coach -q` — Expected: every coach test passes.

- [ ] **Step 5: Commit**

```bash
git add webapp/scripts/coach_store_report.py webapp/tests/coach/test_coach_store_script.py
git commit -F <scratch file: "Add a script that stores a coach report from a file">
```

- [ ] **Step 6: See the page working (local)**

Write a six-point report JSON by hand to a scratch file outside the repository, for a match that has a linked replay in the local database (ask the owner which; if the local database has none, say so and do this step against the friends database in Step 7 instead). Give at least one point two instances and one point an instance with no `t`. Then:

1. `$PY scripts/coach_store_report.py <uuid> "<player>" <scratch report.json>`
2. `$PY -m uvicorn app.main:app --port 8010`
3. Open `http://127.0.0.1:8010/matches?all=1` and confirm the Coach column links the match.
4. Open the coaching page. Confirm: the panel sits above the viewer; clicking each instance loads that round and seeks to that second; the instance without a time opens the round at its start; the Team button is disabled; an earlier version link appears after storing a second time.
5. Open `http://127.0.0.1:8010/replays/<uuid>` and confirm the plain viewer is unchanged and has no panel.

Report what was seen, including anything that looked wrong. A layout problem found here is fixed in this task under the panel tests.

- [ ] **Step 7: Publish for the owner to look at (only when the owner says so, and only after they have merged)**

The live site needs migration `0021`, which arrives when the owner merges this branch and Render deploys. After that, and on the owner's explicit go-ahead, store the hand-written report on the friends database with the second command in the script's docstring, and hand back the page URL. Do not do this step on your own initiative.
