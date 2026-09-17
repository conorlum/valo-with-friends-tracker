r"""Maintenance-window Impact backfill under ONE frozen, ACTIVE candidate manifest.

    .\.venv\Scripts\python.exe scripts\backfill_impact_candidate.py ^
        --manifest ..\docs\superpowers\econ-buy-disruption-candidate\candidate-manifest.json ^
        --state backfill-state.json --confirm-maintenance-window ^
        [--approved-results ..\docs\superpowers\econ-buy-disruption-candidate\review-results.json]

Plan section 6 and plan-review finding P1. scripts/recompute_impact.py clears
caches once and commits match by match, so on its own a running site can
compute aggregates from a half-converted table and cache them under the new
version. This script is for the local site's MAINTENANCE WINDOW:

  1. stop the web process, ingestion and prewarming; back up the database
  2. set app.scoring.impact_runtime.ACTIVE_MANIFEST and bump
     IMPACT_CALCULATION_VERSION to the manifest's activation version
  3. run this script. It refuses unless the window is confirmed, no other
     client session is connected, and the manifest is the verified ACTIVE
     runtime configuration (so ingestion afterwards uses the same one)
  4. the declared match set and every attempted/succeeded/failed ID are
     written to --state after each match. An interruption or failure leaves
     status "incomplete"/"interrupted": keep everything stopped and rerun the
     same command (idempotent upserts resume it), or restore the backup
  5. acceptance: every declared match succeeded AND its persisted rows equal
     a fresh replay under the frozen configuration (so no missing and no
     stale rows), plus the approved review results when given
  6. caches are cleared at the start and on EVERY exit, so nothing a stray
     process cached from mixed rows survives the run
  7. on "scored": run scripts/recompute_player_views.py, check the pages with
     ingestion still paused, then reopen

Exit codes: 0 scored, 2 incomplete/interrupted/verification failed, 3 refused.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.models import ImpactScore, Match, Round
from app.scoring import impact, impact_runtime
from app.scoring.impact_manifest import config_from_manifest, lf_sha256, load_manifest, verify_manifest
from app.services.player_view_cache import invalidate_all_player_caches
from app.services.site_stats_cache import invalidate_site_stats_cache

STATE_VERSION = 1
SESSION_CHECK_EVERY = 25
# EVERY persisted field. A subset would let a stale econ_impact, swing_impact,
# kill_order_bonus, econ_kill/econ_death, trade_detail or trade counter pass
# acceptance while dependent views display the old values.
RESULT_FIELDS = impact.PERSISTED_FIELDS


class BackfillRefused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def other_database_sessions(db) -> list[str]:
    """Every OTHER client connected to this database. A web worker, ingest or
    prewarm process shows up here; so does an open psql/pgAdmin session."""
    if db.bind.dialect.name != "postgresql":
        return []
    rows = db.execute(text(
        "SELECT pid, application_name, client_addr, state FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
        "AND backend_type = 'client backend'"
    )).all()
    return [f"pid {r.pid} app={r.application_name!r} client={r.client_addr} state={r.state}" for r in rows]


def result_rows(rows) -> dict:
    """{"round_id:match_player_id": [RESULT_FIELDS...]} for CalculatedImpact or ImpactScore rows."""
    return {f"{r.round_id}:{r.match_player_id}": [getattr(r, f) for f in RESULT_FIELDS] for r in rows}


def persisted_rows(db, match_id: int) -> dict:
    return result_rows(
        db.query(ImpactScore).join(Round, Round.id == ImpactScore.round_id)
        .filter(Round.match_id == match_id).all())


def persisted_result_diffs(db, approved: dict, manifest_sha: str) -> list[str]:
    diffs = []
    if approved.get("manifest_lf_sha256") != manifest_sha:
        diffs.append("the approved results were produced under a different manifest")
    # The rows are positional, so the field list that labels them must be the one
    # they were written in. A file whose metadata disagrees is refused rather than
    # compared, because a positional comparison would still "pass" on it.
    if approved.get("fields") != list(RESULT_FIELDS):
        diffs.append(f"the approved results label their values {approved.get('fields')}, "
                     f"not the persisted fields {list(RESULT_FIELDS)}")
    for match_id, match in approved.get("matches", {}).items():
        stored = persisted_rows(db, int(match_id))
        expected = match["rows"]
        differing = sorted(k for k in set(stored) | set(expected) if stored.get(k) != expected.get(k))
        if differing:
            diffs.append(f"match {match_id}: {len(differing)} persisted player-rounds differ from the "
                         f"approved report (first: {differing[0]})")
    return diffs


def replay_diffs(db, match_ids, config) -> dict[str, str]:
    """Persisted rows that do not equal a fresh replay under the frozen config."""
    out = {}
    for match_id in match_ids:
        expected = result_rows(impact.build_impact_rows_for_match(db, match_id, **config.build_kwargs()))
        stored = persisted_rows(db, match_id)
        missing = sorted(set(expected) - set(stored))
        stale = sorted(k for k in expected if k in stored and stored[k] != expected[k])
        extra = sorted(set(stored) - set(expected))
        if missing or stale or extra:
            out[str(match_id)] = f"{len(missing)} missing, {len(stale)} stale, {len(extra)} unexpected rows"
    return out


def _clear_caches(db) -> None:
    invalidate_all_player_caches(db)
    invalidate_site_stats_cache(db)
    db.commit()


def _write_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_state(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run_backfill(db_factory, manifest_path, state_path, *, confirm_maintenance_window: bool,
                 compute=None, session_checker=other_database_sessions,
                 approved_results_path=None, log=print) -> dict:
    compute = compute or impact.compute_impact_for_match
    manifest_path, state_path = Path(manifest_path), Path(state_path)
    if not confirm_maintenance_window:
        raise BackfillRefused(
            "confirm the maintenance window (--confirm-maintenance-window) after stopping the web "
            "process, ingestion and prewarming and backing up the database")

    manifest = load_manifest(manifest_path)
    verify_manifest(manifest)
    manifest_sha = lf_sha256(manifest_path)
    if impact_runtime.active_manifest() != manifest:
        raise BackfillRefused(
            "this manifest is not the active runtime configuration: set impact_runtime.ACTIVE_MANIFEST "
            "to it (with its IMPACT_CALCULATION_VERSION) first, so ingestion cannot score differently")
    config = config_from_manifest(manifest)
    approved = (json.loads(Path(approved_results_path).read_text(encoding="utf-8"))
                if approved_results_path else None)

    db = db_factory()
    cleared_caches = False
    try:
        others = session_checker(db)
        if others:
            raise BackfillRefused("other database sessions are connected: " + "; ".join(others))

        all_ids = [m for (m,) in db.query(Match.id).order_by(Match.id).all()]
        state = _load_state(state_path)
        if state is None:
            state = {"state_version": STATE_VERSION, "candidate_id": manifest["candidate_id"],
                     "manifest_lf_sha256": manifest_sha, "match_ids": all_ids, "attempted": [],
                     "succeeded": [], "failed": {}, "status": "running", "history": [["started", _now()]]}
        else:
            if state.get("manifest_lf_sha256") != manifest_sha:
                raise BackfillRefused(
                    "the state file was recorded under a different manifest; resume with that manifest, "
                    "or restore the backup before starting a new backfill")
            if sorted(state["match_ids"]) != all_ids:
                added = sorted(set(all_ids) - set(state["match_ids"]))
                removed = sorted(set(state["match_ids"]) - set(all_ids))
                raise BackfillRefused(
                    f"the match set changed since this backfill was declared (added {added[:10]}, removed "
                    f"{removed[:10]}): something ingested during the window. Restore the backup.")
            state["status"] = "running"
            state["history"].append(["resumed", _now()])
        _write_state(state_path, state)
        _clear_caches(db)
        cleared_caches = True

        done = set(state["succeeded"])
        pending = [m for m in state["match_ids"] if m not in done]
        log(f"backfill {manifest['candidate_id']}: {len(pending)} of {len(state['match_ids'])} matches pending")
        for index, match_id in enumerate(pending):
            if index and index % SESSION_CHECK_EVERY == 0:
                others = session_checker(db)
                if others:
                    state["status"] = "interrupted"
                    state["history"].append(["interrupted: session connected", _now()])
                    _write_state(state_path, state)
                    raise BackfillRefused("a database session connected mid-run: " + "; ".join(others))
            if match_id not in state["attempted"]:
                state["attempted"].append(match_id)
            try:
                compute(db, match_id, config=config)
            except Exception as exc:  # recorded, never swallowed silently
                db.rollback()
                state["failed"][str(match_id)] = f"{type(exc).__name__}: {exc}"[:500]
            else:
                state["succeeded"].append(match_id)
                state["failed"].pop(str(match_id), None)
            _write_state(state_path, state)

        if state["failed"]:
            state["status"] = "incomplete"
            state["history"].append(["incomplete", _now()])
            _write_state(state_path, state)
            log(f"BACKFILL INCOMPLETE: {len(state['failed'])} matches failed. Keep the web process, "
                "ingestion and prewarming STOPPED. Rerun this command with the same manifest and state "
                "file to resume, or restore the pre-backfill backup and revert ACTIVE_MANIFEST and "
                "IMPACT_CALCULATION_VERSION. Never reopen a partly converted database.")
            return state

        replay = replay_diffs(db, state["match_ids"], config)
        if replay:
            # Requeue them. A match that returned cleanly but does not equal its
            # replay must be recomputed by the documented rerun, not skipped
            # forever because it is already listed in `succeeded`.
            state["succeeded"] = [m for m in state["succeeded"] if str(m) not in replay]
        current_ids = [m for (m,) in db.query(Match.id).order_by(Match.id).all()]
        added = sorted(set(current_ids) - set(state["match_ids"]))
        removed = sorted(set(state["match_ids"]) - set(current_ids))
        verification = {"replay_differences": replay,
                        # Session polling cannot see a connection that has
                        # already disconnected, so the declared match set is
                        # rechecked against the database here.
                        "match_set_changed": ({"added": added, "removed": removed}
                                              if added or removed else {}),
                        "approved_result_differences": (persisted_result_diffs(db, approved, manifest_sha)
                                                        if approved is not None else []),
                        "sessions_at_end": session_checker(db)}
        state["verification"] = verification
        if any(verification.values()):
            state["status"] = "verification_failed"
            state["history"].append(["verification_failed", _now()])
            _write_state(state_path, state)
            log("BACKFILL VERIFICATION FAILED -- keep the site stopped: " + json.dumps(verification)[:2000])
            return state

        state["status"] = "scored"
        state["history"].append(["scored", _now()])
        _write_state(state_path, state)
        log(f"BACKFILL SCORED: all {len(state['match_ids'])} matches equal their frozen replay. Next: "
            "scripts/recompute_player_views.py, then page checks with ingestion still paused, then reopen.")
        return state
    finally:
        try:
            db.rollback()
            # Only when this run actually started. A REFUSED run must leave the
            # database exactly as it found it -- clearing caches is a write, and
            # the site may still be serving from them.
            if cleared_caches:
                _clear_caches(db)
        finally:
            db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--confirm-maintenance-window", action="store_true")
    parser.add_argument("--approved-results")
    args = parser.parse_args(argv)
    try:
        state = run_backfill(SessionLocal, args.manifest, args.state,
                             confirm_maintenance_window=args.confirm_maintenance_window,
                             approved_results_path=args.approved_results)
    except BackfillRefused as exc:
        print(f"REFUSED: {exc}")
        return 3
    return 0 if state["status"] == "scored" else 2


if __name__ == "__main__":
    sys.exit(main())
