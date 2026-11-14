"""Everything one coach run needs about a match, read from the database once and kept on disk
(`app/coach/snapfile.py`). The brief, the detectors and the query commands read the files; the model never
reaches the database."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.coach import control_areas
from app.coach.snapfile import FILE, ROUNDS_DIR, VERSION, Snapshot, SnapshotError, _groups, load, save  # noqa: F401
from app.replays import format as fmt
from app.services import enemy_names, replay_control, replays

log = logging.getLogger(__name__)


def assemble(context: dict, blobs: dict[int, dict], controls: dict[int, tuple], links: dict | None = None,
             util: dict | None = None) -> dict:
    """`util`: the replay's Critical Util per round (app/services/replay_util.py match_payload's `rounds`), or None
    when the caller has none; a round missing from it names the gap."""
    rounds = {}
    for n, blob in sorted(blobs.items()):
        status, stale, shares = controls.get(n, ("not_ready", False, None))
        link = (links or {}).get(n) or {}
        link = {"sides": dict(link.get("sides") or {}), "db_deaths": [list(d) for d in link.get("db_deaths") or []]}
        gaps = []
        if status != "ok":
            gaps.append(f"control_{status}")
        elif stale:
            gaps.append("control_stale")
        if not (shares and shares.get("knew")):
            gaps.append("team_knowledge")
        if "player_state" not in blob:
            gaps.append("player_state")
        if link["db_deaths"]:
            gaps.append("db_only_deaths")
        round_util = (util or {}).get(str(n))
        if util is not None and round_util is None:
            gaps.append("critical_util")
        rounds[str(n)] = {"blob": blob, "control": shares, "link": link, "gaps": gaps, "util": round_util}
    return {"v": VERSION, "context": context, "rounds": rounds}


def _replay_identity(replay):
    # store._store deletes and inserts on re-parse. The recipe/source also distinguish
    # replacements when a database reuses an integer id.
    return None if replay is None else (replay.id, replay.recipe, replay.source_sha256)


def _wait_util(db, match_uuid, *, session_factory, deadline, poll_s, ask):
    """Read a current stored build before loading rounds, releasing every transaction while waiting."""
    from app.critutil import build_digest
    from app.db import SessionLocal
    from app.models.replay import UtilModel
    from app.services import replay_util, util_model, util_values

    session_factory = SessionLocal if session_factory is None else session_factory
    asked = False
    first = True
    while first or time.monotonic() < deadline:
        first = False
        db.rollback()
        try:
            with session_factory() as poll:
                stored = poll.query(UtilModel.key, UtilModel.version).filter(UtilModel.name == "values").one_or_none()
                stored_key = stored.key if stored is not None and stored.version == util_values.VERSION else None
                # Coverage and the corpus key need only this projection of the estimator, never its fitted cells.
                win = poll.query(UtilModel.key, UtilModel.version, UtilModel.data["excluded"].label("excluded")) \
                    .filter(UtilModel.name == "win").one_or_none()
                model = {"key": win.key, "excluded": win.excluded} \
                    if win is not None and win.version == util_model.VERSION else None
                rows = replay_util.current_rows(poll, with_data=False)
                key = replay_util.corpus_key(poll, rows, model)
            replay = replays.find_replay(db, match_uuid)
            if replay is None:
                raise SnapshotError(f"no replay stored for {match_uuid}")
            identity = _replay_identity(replay)
            current = stored_key == key and util_model.covers(model, {replay.match_id})
            reason = f"the stored build is not current (stored key {stored_key}, current key {key})"
            if current:
                payload = replay_util.match_payload(db, replay, read_only=True)
                if not payload["updating"]:
                    return identity, payload["rounds"]
                reason += "; read-only payload is updating"
        except Exception as error:
            db.rollback()
            reason = f"{type(error).__name__}: {error}"
            log.warning("Critical Util read failed: %s", reason, exc_info=True)
        db.rollback()
        if not asked:
            asked = True
            if ask is None:
                log.warning("no REPLAY_ADMIN_TOKEN: waiting for the site's own build")
            else:
                answer = None
                try:
                    answer = ask()
                except Exception:
                    log.warning("Critical Util build request failed", exc_info=True)
                if answer and answer.get("digest"):
                    local_digest = build_digest.digest(Path(__file__).resolve().parents[2])
                    site_digest = answer["digest"]
                    if local_digest != site_digest:
                        raise SnapshotError("Critical Util: this checkout's code differs from the site's "
                                            f"(local digest {local_digest}, site digest {site_digest})")
                if answer and "areas_hash" in answer:       # a site from before painted areas sends none
                    from app.coach import areas

                    local_areas = areas.table_hash(painted_table=areas.load_table(published_only=True))
                    if local_areas != answer["areas_hash"]:
                        raise SnapshotError(
                            "Critical Util: this checkout's published map paint (origin/main) differs from the "
                            f"site's (local {local_areas}, site {answer['areas_hash']}): the build keys can never "
                            "match. Fetch origin/main, or wait for the site to deploy the paint, then run again")
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(max(poll_s, 0.001), remaining))
    raise SnapshotError(f"Critical Util: {reason}")


def _linked_context(db, replay, match_uuid):
    context = replays.page_context(db, replay)
    if not context["match"].get("linked"):
        raise SnapshotError(f"replay {match_uuid} is {context['match'].get('reason') or 'not linked'}: nothing to coach")
    selection = replay_control.layout_of(replay)
    asset_key = selection.layout.asset_key if selection.state == "ok" else None
    context = {**context, "match": {**context["match"], "asset_key": asset_key}}
    return context


def read(db, match_uuid: str, *, session_factory=None, util_wait_s=600, poll_s=15, ask=None) -> dict:
    deadline = time.monotonic() + max(0, util_wait_s)
    replay = replays.find_replay(db, match_uuid)
    if replay is None:
        raise SnapshotError(f"no replay stored for {match_uuid}")
    # Preserve the early unlinked refusal (including callers without a database).
    # All context used in the snapshot is loaded again after the util wait.
    _linked_context(db, replay, match_uuid)
    asked = False

    def ask_once():
        nonlocal asked
        if not asked:
            asked = True
            return ask()
        return None

    while True:
        identity, util = _wait_util(db, match_uuid, session_factory=session_factory,
                                   deadline=deadline, poll_s=poll_s, ask=ask_once if ask else None)
        db.rollback()  # expire the ORM identity map before checking the confirmed replay
        replay = replays.find_replay(db, match_uuid)
        current = _replay_identity(replay)
        if current == identity:
            try:
                context = _linked_context(db, replay, match_uuid)
                data = _read_rounds(db, replay, context, util)
            except Exception:
                # A replacement may remove blobs while they are being read. Retry only
                # when it actually changed; ordinary read errors keep their traceback.
                db.rollback()
                current = _replay_identity(replays.find_replay(db, match_uuid))
                if current == identity:
                    raise
            else:
                db.rollback()
                current = _replay_identity(replays.find_replay(db, match_uuid))
                if current == identity:
                    return data
        db.rollback()
        remaining = deadline - time.monotonic()
        reason = (f"Critical Util: replay identity changed from {identity} to {current} "
                  "before the snapshot could be read")
        if remaining <= 0:
            raise SnapshotError(reason)
        time.sleep(min(max(poll_s, 0.001), remaining))
        if time.monotonic() >= deadline:
            raise SnapshotError(reason)


def _read_rounds(db, replay, context, util):
    asset_key = context["match"]["asset_key"]
    groups = _groups(context)
    blobs, controls, links = {}, {}, {}
    for n in context["match"]["rounds"]:
        blobs[n] = fmt.decode_blob(replays.round_blob(db, replay, n))
        links[n] = replay_control.round_link(replay, groups, n)
        roles = links[n].get("sides") or {}
        for p in blobs[n]["players"]:
            slot = int(p["slot"])
            if p.get("side") is None and slot not in groups and str(slot) not in roles and slot not in roles:
                raise SnapshotError(f"round {n}: slot {p['slot']} has no side in the replay or the link: "
                                    "nothing can be said about teammates or enemies")
        answer = replay_control.round_control(db, replay, n)
        shares = (control_areas.area_shares(answer.row.data, asset_key)
                  if answer.status == "ok" and asset_key is not None else None)
        controls[n] = (answer.status, bool(answer.stale), shares)
    return assemble(context, blobs, controls, links, util)


def hide_enemy_names(data: dict, player_id: int) -> dict:
    """The snapshot as one player's run sees it: every opponent named by their agent ("the enemy Neon"), so no
    opponent's name reaches the model or its report."""
    players = data["context"]["players"]
    team = next((p.get("team") for p in players.values() if p.get("player_id") == player_id), None)
    if team is None:
        raise SnapshotError(f"player {player_id} is not in this replay")
    return {**data, "context": {**data["context"], "players": enemy_names.hide_in(players, team)}}


