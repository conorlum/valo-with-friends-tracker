"""Stores a condensed replay and links it, in one locked transaction (docs/replay-viewer-plan.md,
"Storage: migration 0012_replays", Writes).

1. `pg_advisory_xact_lock(hashtext(match_uuid))`: stores and links of one replay serialise.
2. The dedupe rule, for an existing row with this `match_uuid`:
   - the same `source_sha256` and recipe: a no-op;
   - the same `source_sha256`, another recipe (a re-ingest or a re-parse after a condenser change):
     replaced, keeping the per-kill Impact split when it links to the same match;
   - another `source_sha256` (another recording): a linked existing replay is kept, and the new
     one reported; an unlinked or refused one is replaced only if the new one links in this same
     transaction. `replace=True` (local `--replace`) overrides both.
3. One multi-row insert per table, then the link, then commit. Any failure rolls it all back and
   the old rows survive.

Refuses outright in demo mode and on the ValoMaths demo database, whatever called it. It never
runs the scorer: the per-kill Impact split is a separate, later step (app/services/replay_impact.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import insert, text

from app.config import settings
from app.models.replay import Replay, ReplayDeletion, ReplayPlayer, ReplayRound, ReplayRoundControl
from app.replays import db as replay_db
from app.replays import format as fmt
from app.replays.condense import CondensedReplay

DEMO_DATABASE = "valomaths_demo"
SOURCES = ("local", "upload")


class StoreRefused(Exception):
    pass


@dataclass
class StoreResult:
    action: str               # 'stored' | 'replaced' | 'unchanged' | 'kept_existing'
    replay_id: int | None
    link_status: str | None
    report: dict


def refuse_demo(session) -> None:
    if settings.demo_mode:
        raise StoreRefused("demo mode: the ValoMaths demo has no replays")
    if session.get_bind().dialect.name == "postgresql":
        if session.execute(text("SELECT current_database()")).scalar() == DEMO_DATABASE:
            raise StoreRefused("connected to the demo database")


def _insert(session, condensed: CondensedReplay, source: str) -> Replay:
    link_inputs = {**condensed.link_inputs, "dropped_final_round": bool(condensed.report.get("dropped_final_round"))}
    row = Replay(match_uuid=condensed.match_uuid.lower(), map_name=condensed.map_name,
                 round_count=condensed.round_count, format_version=fmt.FORMAT_VERSION, recipe=condensed.recipe,
                 game_branch=condensed.game_branch, source_sha256=condensed.source_sha256, source=source,
                 link_status="unlinked", link_inputs=link_inputs, link_report=None)
    session.add(row)
    session.flush()
    encoded = condensed.encoded_rounds()
    session.execute(insert(ReplayRound), [{"replay_id": row.id, "round_number": n, "data": data}
                                          for n, data in sorted(encoded.items())])
    session.execute(insert(ReplayPlayer), [
        {"replay_id": row.id, "slot": p["slot"], "subject": p.get("subject") and str(p["subject"]).lower(),
         "agent": p["agent"], "side_group": p.get("side_group"), "match_player_id": None}
        for p in condensed.players])
    return row


def _delete(session, row: Replay) -> None:
    # Map control first: its rows reference the rounds (migration 0014 cascades too; SQLite tests do not).
    session.query(ReplayRoundControl).filter(ReplayRoundControl.replay_id == row.id).delete(synchronize_session=False)
    session.query(ReplayRound).filter(ReplayRound.replay_id == row.id).delete(synchronize_session=False)
    session.query(ReplayPlayer).filter(ReplayPlayer.replay_id == row.id).delete(synchronize_session=False)
    session.delete(row)
    session.flush()


@dataclass(frozen=True)
class ExpectedAuto:
    """What an automatic re-parse attempt was reserved for (`replay_uploads.auto_context`): the stored replay
    as it was when selected, and the recipe the attempt aims for. The store replaces only that replay."""
    selected_replay_id: int
    source_recipe: str
    source_sha256: str
    target_recipe: str


def store_replay(session, condensed: CondensedReplay, *, source: str, replace: bool = False, commit: bool = True,
                 expected_auto: ExpectedAuto | None = None) -> StoreResult:
    """Store + link in one transaction, committed here; rolled back on any failure.

    `commit=False` hands the transaction to the caller: nothing here commits or rolls it back, whatever the
    branch (a failed link attempt is undone by its own savepoint), and an exception leaves the rollback to the
    caller. Automatic collection uses it to settle its attempt row in the same commit as the replacement.
    `expected_auto` makes the store conditional (see `_guard_auto`)."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    refuse_demo(session)
    if not commit:
        return _store(session, condensed, source, replace, expected_auto)[0]
    try:
        result, changed = _store(session, condensed, source, replace, expected_auto)
        if changed:
            session.commit()
        else:
            session.rollback()
        return result
    except Exception:
        session.rollback()
        raise


def _guard_auto(existing: Replay | None, condensed: CondensedReplay, expected: ExpectedAuto) -> StoreResult | None:
    """An automatic re-parse replaces only the uploaded replay it was selected for, still as it was then.
    None: go on and replace it. A result: the replay has moved on, and nothing is written. Raises StoreRefused
    when there is nothing this attempt may ever write."""
    if condensed.recipe != expected.target_recipe or condensed.source_sha256 != expected.source_sha256:
        raise StoreRefused("the result is not what this attempt asked for")
    if existing is None:
        raise StoreRefused("the selected replay is gone; an automatic re-parse never recreates one")
    if existing.source != "upload":
        raise StoreRefused("the replay is now a local ingest; those stay with reingest_replays.py")
    selected = (existing.id == expected.selected_replay_id and existing.recipe == expected.source_recipe
                and existing.source_sha256 == expected.source_sha256)
    if selected:
        return None
    if existing.source_sha256 == condensed.source_sha256 and existing.recipe == condensed.recipe:
        return StoreResult("unchanged", existing.id, existing.link_status, {"reason": "same file and recipe"})
    return StoreResult("kept_existing", existing.id, existing.link_status,
                       {"reason": "superseded: the replay changed after it was selected for this re-parse"})


def _store(session, condensed: CondensedReplay, source: str, replace: bool,
           expected_auto: ExpectedAuto | None) -> tuple[StoreResult, bool]:
    """(the result, whether anything was written). Never commits or rolls back the transaction."""
    uuid = condensed.match_uuid.lower()
    replay_db.advisory_lock(session, uuid)
    # A match deleted on request never comes back, whoever stores it (upload, reparse or local).
    if session.get(ReplayDeletion, uuid) is not None:
        raise StoreRefused("this match was deleted on request")
    existing = session.query(Replay).filter(Replay.match_uuid == uuid).populate_existing().one_or_none()
    if expected_auto is not None:
        moved_on = _guard_auto(existing, condensed, expected_auto)
        if moved_on is not None:
            return moved_on, False
    same_source = existing is not None and existing.source_sha256 == condensed.source_sha256
    if existing is not None and not replace:
        if same_source and existing.recipe == condensed.recipe:
            return StoreResult("unchanged", existing.id, existing.link_status, {"reason": "same file and recipe"}), False
        if not same_source and replay_db.is_linked(existing):
            return StoreResult("kept_existing", existing.id, existing.link_status,
                               {"reason": "a linked replay of this match exists; the new recording was not stored"}), False
        if not same_source:
            # An unlinked or refused replay can't squat the UUID: the new one replaces it only
            # if it links, and a savepoint undoes the attempt otherwise.
            kept = (existing.id, existing.link_status)
            savepoint = session.begin_nested()
            _delete(session, existing)
            row = _insert(session, condensed, source)
            status = replay_db.link_replay(session, row)
            if status != "linked":
                savepoint.rollback()
                return StoreResult("kept_existing", kept[0], kept[1],
                                   {"reason": "the new recording did not link, so it doesn't replace the "
                                              "existing unlinked one", "new_link_status": status}), False
            savepoint.commit()
            return StoreResult("replaced", row.id, status, row.link_report or {}), True
    action = "stored"
    carried = None
    if existing is not None:
        # The same recording under a new recipe (a re-parse): the per-kill Impact split describes the
        # match's impact_scores rows, not the blobs, so it is kept when the new row links to the same
        # match. The page still shows it only while its fingerprint is current (db.kill_impact_for_page).
        if same_source and replay_db.is_linked(existing) and existing.kill_impact:
            carried = (existing.match_id, existing.kill_impact)
        _delete(session, existing)
        action = "replaced"
    row = _insert(session, condensed, source)
    status = replay_db.link_replay(session, row)
    if carried is not None and status == "linked" and row.match_id == carried[0]:
        row.kill_impact = carried[1]
    return StoreResult(action, row.id, status, row.link_report or {}), True
