"""Per-kill Impact for a linked replay's kill feed: display-only (docs/replay-viewer-plan.md,
decision 5 as amended 2026-09-27).

Each kill's share of the stored round Impact is taken from the **existing** scorer, read-only:
`build_impact_rows_for_match` recomputes a match's rows with a `kill_observer` and writes
nothing. For each kill the killer's share is `leverage x kill_order_bonus_x_time` and the
victim's `leverage x death_order_bonus_x_time`, as `scripts/build_replay_bundle.py` has shown
locally. The split is stored (`replays.kill_impact`) only when:

- every recomputed row's persisted fields equal the stored `impact_scores` row, and
- each (round, player)'s shares reconcile with it: the victim side equals `death_impact`
  exactly (after the stored row's integer rounding), the killer side's leverage part
  (`kill_impact - damage - assists_component - trade_credit`) within 1 of the gain sum.

It stores full-precision values and a fingerprint of the stored rows (`db.impact_fingerprint`);
the page shows the split only while that fingerprint is unchanged, so a rescore hides it until
this runs again.

It runs only from user-run scripts (ingest's write path, `link_replays.py`, the crawl hook),
after the link commits, in its own session: a scorer error there can't undo a store or a link,
and the web service never runs the scorer (AFK run decision D3, approved).

This is the one replay module that may import scoring code, and only these read-only names;
tests/replays/test_replay_isolation.py enforces the list, and that nothing here persists.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.models import ImpactScore, Round
from app.models.replay import Replay
from app.replays import db as replay_db

log = logging.getLogger(__name__)

SPLIT_VERSION = 1


class SplitRefused(Exception):
    pass


def reconcile(observed: list[dict], rows: list, stored: dict, number_of: dict[int, int], persisted_fields,
              leverage: float) -> dict[int, list[float]]:
    """Pure: {kill_events.id: [gain, loss]} from the observer's events, or SplitRefused.

    `observed`: the kill_observer's keyword dicts ({"kill", "context", "round_number", ...});
    `rows`: the recomputed ImpactScore rows (unsaved); `stored`: {(round_id, match_player_id):
    the stored row}; `number_of`: round id -> round number."""
    per_kill: dict[int, list[float]] = {}
    gain_sum: dict[tuple[int, int], float] = defaultdict(float)
    loss_sum: dict[tuple[int, int], float] = defaultdict(float)
    for event in observed:
        kill = event["kill"]
        gain = leverage * kill["kill_order_bonus_x_time"]
        loss = leverage * kill["death_order_bonus_x_time"]
        per_kill[int(kill["id"])] = [gain, loss]
        if kill.get("killer_match_player_id") is not None:
            gain_sum[(event["round_number"], kill["killer_match_player_id"])] += gain
        if kill.get("death_match_player_id") is not None:
            loss_sum[(event["round_number"], kill["death_match_player_id"])] += loss
    if len(rows) != len(stored):
        raise SplitRefused(f"{len(rows)} recomputed rows for {len(stored)} stored")
    for row in rows:
        have = stored.get((row.round_id, row.match_player_id))
        if have is None or any(getattr(have, f) != getattr(row, f) for f in persisted_fields):
            raise SplitRefused(f"round {number_of.get(row.round_id)} player {row.match_player_id}: the recomputed "
                               f"row differs from the stored one")
        key = (number_of[row.round_id], row.match_player_id)
        if have.death_impact != round(loss_sum.get(key, 0.0)):
            raise SplitRefused(f"round {key[0]} player {key[1]}: death shares don't add up to death_impact")
        leverage_part = have.kill_impact - have.damage - row.assists_component - have.trade_credit
        if abs(leverage_part - gain_sum.get(key, 0.0)) > 1.0:
            raise SplitRefused(f"round {key[0]} player {key[1]}: kill shares don't add up to kill_impact")
    return per_kill


def compute_split(session, match_id: int) -> dict:
    """The `replays.kill_impact` value for a linked match, or SplitRefused. Reads only."""
    from app.scoring.impact import PERSISTED_FIELDS, FormulaWeights, build_impact_rows_for_match
    from app.scoring.impact_runtime import active_manifest, active_scoring_config

    config = active_scoring_config()
    if config is None:
        raise SplitRefused("no active scoring manifest")
    kwargs = config.build_kwargs()
    if not kwargs.get("enable_econ_component"):
        raise SplitRefused("the active configuration is not the econ-component structure this reads")
    leverage = (kwargs.get("weights") or FormulaWeights()).leverage
    observed: list[dict] = []
    rows = build_impact_rows_for_match(session, match_id, kill_observer=lambda **kw: observed.append(kw), **kwargs)
    number_of = {r.id: r.round_number for r in session.query(Round).filter(Round.match_id == match_id)}
    stored = {(s.round_id, s.match_player_id): s for s in session.query(ImpactScore)
              .filter(ImpactScore.round_id.in_(list(number_of)))}
    per_kill = reconcile(observed, rows, stored, number_of, PERSISTED_FIELDS, leverage)
    # The feed also shows the players alive on each side before the kill, from the same event.
    for event in observed:
        context = event.get("context") or {}
        if "killer_team_alive" in context and int(event["kill"]["id"]) in per_kill:
            per_kill[int(event["kill"]["id"])] += [context["killer_team_alive"], context["victim_team_alive"]]
    manifest = active_manifest() or {}
    # kills: {kill_events.id: [gain, loss, killer side alive, victim side alive]}
    return {"v": SPLIT_VERSION, "config": config.config_id,
            "impact_version": manifest.get("activation_impact_calculation_version"),
            "fingerprint": replay_db.impact_fingerprint(session, match_id),
            "kills": {str(k): v for k, v in sorted(per_kill.items())}}


def refresh_replay_impact(session_factory, replay_id: int) -> str:
    """Computes and stores one linked replay's split in its own session and transaction.
    Returns 'stored', 'not linked' or 'failed: <reason>'; never raises."""
    session = session_factory()
    try:
        replay = session.get(Replay, replay_id)
        if replay is None or not replay_db.is_linked(replay):
            return "not linked"
        match_id = replay.match_id
        try:
            split = compute_split(session, match_id)
        except SplitRefused as refused:
            session.rollback()
            log.warning("replay %s: per-kill Impact not stored: %s", replay_id, refused)
            return f"failed: {refused}"
        session.rollback()  # end the read transaction the scorer ran in before writing
        replay = session.get(Replay, replay_id)
        replay_db.advisory_lock(session, str(replay.match_uuid))
        if not replay_db.is_linked(replay) or replay.match_id != match_id:
            session.rollback()
            return "not linked"
        replay.kill_impact = split
        session.commit()
        return "stored"
    except Exception as error:  # best-effort: the link stands whatever happens here
        session.rollback()
        log.warning("replay %s: per-kill Impact failed: %s", replay_id, error)
        return f"failed: {type(error).__name__}: {error}"
    finally:
        session.close()



def claim_write_identity(session_factory) -> None:
    """The scoring ingest preflight, as the crawl runs it, for the user-run replay scripts: the link's
    `players.riot_subject` backfill is behind the release write gate. The identity goes on the
    engine, so every later session carries it. Raises IngestRefused with the reason."""
    from app.scoring.ingest_preflight import verify_ingest_preflight

    session = session_factory()
    try:
        verify_ingest_preflight(session)
        session.commit()
    finally:
        session.close()


def link_pending_replays(session_factory, uuids: list[str] | None = None, refresh_impact: bool = False) -> dict:
    """The link-later pass (`scripts/link_replays.py`, and best-effort after every tracker.gg
    crawl): links each stored replay with `match_id IS NULL` whose match row now exists (or the
    given UUIDs, whatever their state), each in its own locked transaction, then stores the
    per-kill split of every linked replay that lacks one (or, with `refresh_impact`, every one).
    Never raises: a failure is counted and logged, and never fails a crawl."""
    from sqlalchemy import func

    from app.models import Match

    counts: dict[str, int] = defaultdict(int)
    session = session_factory()
    try:
        query = session.query(Replay.id, Replay.match_uuid)
        if uuids:
            query = query.filter(Replay.match_uuid.in_([u.lower() for u in uuids]))
        else:
            query = query.filter(Replay.match_id.is_(None))
        targets = query.all()
        for replay_id, match_uuid in targets:
            if not uuids and session.query(Match.id).filter(
                    func.lower(Match.external_id) == str(match_uuid).lower()).first() is None:
                counts["still no match"] += 1
                continue
            try:
                replay_db.advisory_lock(session, str(match_uuid))
                replay = session.get(Replay, replay_id)
                status = replay_db.link_replay(session, replay)
                session.commit()
                counts[status] += 1
            except Exception as error:  # noqa: BLE001
                session.rollback()
                counts["failed"] += 1
                log.warning("replay %s: link failed: %s", replay_id, error)
        query = session.query(Replay.id).filter(Replay.link_status == "linked", Replay.match_id.isnot(None))
        if not refresh_impact:
            query = query.filter(Replay.kill_impact.is_(None))
        pending = [replay_id for (replay_id,) in query.all()]
        session.rollback()
    except Exception as error:  # noqa: BLE001  (e.g. no replays table before migration 0012)
        session.rollback()
        log.warning("replay link pass skipped: %s", error)
        return {"skipped": str(error)}
    finally:
        session.close()
    for replay_id in pending:
        status = refresh_replay_impact(session_factory, replay_id)
        counts["per-kill " + status.split(":")[0]] += 1
    return dict(counts)

