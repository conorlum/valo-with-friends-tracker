"""Builds the local replay preview bundle for one linked match. Dev-only; nothing is written to a DB.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\build_replay_bundle.py --export-dir %TEMP%\\valo-replay\\<uuid>

It condenses the export strictly (the `.vrf`'s hash and the parser's BUILD.json must
match, as `ingest_replay.py --dry-run` does), links it read-only to its `matches` row, and
writes `bundle.json` beside the export (under `%TEMP%`, never the repo), holding:

- every round's blob (format v1), with each kill's DB row (`kill_events.weapon`) and its
  Impact: the killer's gain and the victim's loss, `leverage x kill_order_bonus_x_time`
  and `leverage x death_order_bonus_x_time` from the live scorer's `kill_observer`;
- the linked players (names, agents, teams, sides), each round's DB outcome, plant and
  defuse times on the replay clock, and each player's round stats and stored Impact row;
- ability objects, the planted spike and shots (`app/replays/extras.py`).

The per-kill Impact is only *read* from the scorer: `build_impact_rows_for_match` is
called with the active (v4) configuration and a kill observer, and returns rows without
writing them. The script checks those rows against the stored `impact_scores` and each
player's per-kill sums against their stored row (death_impact exactly; kill_impact's
leverage part within 1 for rounding), and refuses to write the bundle if either differs.

`app/config.py`'s `replay_preview_dir` (off by default) points the local site's dev route
at the folder holding `<uuid>/bundle.json`.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import pickle
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.replays import format as fmt  # noqa: E402
from app.replays import link as lk  # noqa: E402
from app.replays.condense import (  # noqa: E402
    build_players,
    condense,
    load_agents,
    load_maps,
    read_game_state,
)
from app.replays.contract import (  # noqa: E402
    ContractError,
    Export,
    MovementStream,
    check_manifest,
    classify_diagnostics,
    load_export_streaming,
    load_manifest,
    load_pin,
    map_codes_in_file,
    sha256_file,
)
from app.replays.extras import build_extras  # noqa: E402
from app.replays.header import header_match_uuid  # noqa: E402

BUNDLE_VERSION = 1


def default_archive() -> Path:
    configured = os.environ.get("VALO_REPLAY_ARCHIVE")
    return Path(configured) if configured else Path.home() / "ValorantReplayArchive"


def default_parser_dir() -> Path:
    configured = os.environ.get("REPLAY_PARSER_DIR")
    return Path(configured) if configured else Path.home() / "rp" / "parser"


# ---------------------------------------------------------------- condense, keeping the export


def condense_strict(export_dir: Path, vrf: Path, build: dict):
    """condense_export_dir's strict path, returning the export's player table and round windows too."""
    pin = load_pin()
    export = load_export_streaming(export_dir)
    check_manifest(export.manifest, pin, sha256_file(vrf), build)
    blocking = classify_diagnostics(export.manifest).blocking
    if blocking:
        raise ContractError("blocking_diagnostics", "; ".join(blocking))
    agents = load_agents()
    maps = load_maps()
    replay = condense(export, maps=maps, agents_by_code=agents,
                      recipe=fmt.recipe(pin.commit, fmt.assets_revision()),
                      vrf_map_codes=map_codes_in_file(vrf), header=header_match_uuid(vrf),
                      file_names=[vrf.name, str(export.manifest.get("source_file") or "")])
    players = build_players(export, agents)
    windows = read_game_state(export).windows
    return replay, players, windows, maps[replay.map_name], agents, WorldPositions(export, players)


class WorldPositions:
    """Each player's world position over the whole recording, buy phases included, thinned to
    one sample per STEP_MS; `at(t_ms)` gives the samples within 0.5 s of t. The same pass keeps
    every other pawn's movement (drones, Trailblazer), for `path(guid)`."""

    STEP_MS = 100

    def __init__(self, export, players):
        self.rows: dict[int, tuple[list[int], list[tuple[float, float]]]] = {}
        by_slot: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        self.others: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        for row in export.movement:
            data = row.data
            position = data.get("position") or {}
            if data.get("error_sentinel") or position.get("x") is None or position.get("y") is None:
                continue
            pawn = int(data.get("shooter_character_net_guid") or data.get("actor_net_guid") or 0)
            slot = players.pawn_slot.get(pawn)
            target = by_slot[slot] if slot is not None else self.others[pawn]
            if not target or row.time_ms - target[-1][0] >= self.STEP_MS:
                target.append((row.time_ms, float(position["x"]), float(position["y"])))
        for slot, samples in by_slot.items():
            samples.sort()
            self.rows[slot] = ([t for t, _, _ in samples], [(x, y) for _, x, y in samples])
        for samples in self.others.values():
            samples.sort()

    def path(self, guid: int) -> list[tuple[int, float, float]]:
        return self.others.get(guid, [])

    def at(self, t_ms: int) -> dict[int, tuple[float, float]]:
        out = {}
        for slot, (times, points) in self.rows.items():
            i = bisect.bisect_left(times, t_ms)
            best = min((j for j in (i - 1, i) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t_ms),
                       default=None)
            if best is not None and abs(times[best] - t_ms) <= 500:
                out[slot] = points[best]
        return out


# ---------------------------------------------------------------- DB reads (read-only session)


def open_read_only_session():
    from sqlalchemy import text

    from app.config import settings
    from app.db import SessionLocal

    if settings.demo_mode:
        raise SystemExit("REFUSED: demo mode; the ValoMaths demo has no replays")
    session = SessionLocal()
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))
        if session.execute(text("SELECT current_database()")).scalar() == "valomaths_demo":
            raise SystemExit("REFUSED: connected to the demo database")
    return session


def per_kill_impact(session, match_id: int) -> tuple[dict[int, dict], dict, dict]:
    """{kill_events.id: {gain, loss, ...}} from the live scorer's kill_observer, each (round,
    match player)'s unpersisted components, and the checks.

    Reads only: build_impact_rows_for_match returns rows and writes nothing."""
    from app.models import ImpactScore, Round
    from app.scoring.impact import PERSISTED_FIELDS, FormulaWeights, build_impact_rows_for_match
    from app.scoring.impact_runtime import active_manifest, active_scoring_config

    config = active_scoring_config()
    if config is None:
        raise SystemExit("REFUSED: no active scoring manifest; per-kill Impact needs the live v4 configuration")
    kwargs = config.build_kwargs()
    if not kwargs.get("enable_econ_component"):
        raise SystemExit("REFUSED: the active configuration is not the econ-component structure this reads")
    leverage = (kwargs.get("weights") or FormulaWeights()).leverage
    seen: list[dict] = []
    rows = build_impact_rows_for_match(session, match_id, kill_observer=lambda **kw: seen.append(kw), **kwargs)

    per_kill: dict[int, dict] = {}
    gain_sum: dict[tuple[int, int], float] = defaultdict(float)
    loss_sum: dict[tuple[int, int], float] = defaultdict(float)
    for event in seen:
        kill, context = event["kill"], event["context"]
        gain = leverage * kill["kill_order_bonus_x_time"]
        loss = leverage * kill["death_order_bonus_x_time"]
        per_kill[kill["id"]] = {
            "gain": round(gain, 1), "loss": round(loss, 1),
            "state": [context["killer_team_alive"], context["victim_team_alive"]],
            "post_plant": bool(kill.get("is_post_plant")),
            "self_kill": bool(context["self_kill"]), "environmental": bool(context["environmental"]),
        }
        if kill["killer_match_player_id"] is not None:
            gain_sum[(event["round_number"], kill["killer_match_player_id"])] += gain
        if kill["death_match_player_id"] is not None:
            loss_sum[(event["round_number"], kill["death_match_player_id"])] += loss

    number_of = {r.id: r.round_number for r in session.query(Round).filter(Round.match_id == match_id)}
    stored = {(s.round_id, s.match_player_id): s for s in session.query(ImpactScore)
              .filter(ImpactScore.round_id.in_(list(number_of)))}
    row_mismatches, sum_mismatches = [], []
    # leverage_component and assists_component aren't persisted: they come from the recomputed
    # row, which is only used once every persisted field of it equals the stored row.
    components: dict[tuple[int, int], dict] = {}
    for row in rows:
        have = stored.get((row.round_id, row.match_player_id))
        if have is None or any(getattr(have, f) != getattr(row, f) for f in PERSISTED_FIELDS):
            row_mismatches.append([number_of[row.round_id], row.match_player_id])
            continue
        key = (number_of[row.round_id], row.match_player_id)
        components[key] = {"leverage": row.leverage_component, "assists_c": row.assists_component}
        if have.death_impact != round(loss_sum.get(key, 0.0)):
            sum_mismatches.append([*key, "death_impact", have.death_impact, round(loss_sum.get(key, 0.0), 2)])
        leverage_part = have.kill_impact - have.damage - row.assists_component
        if abs(leverage_part - (gain_sum.get(key, 0.0) + have.trade_credit)) > 1.0:
            sum_mismatches.append([*key, "kill_impact", leverage_part, round(gain_sum.get(key, 0.0), 2)])
    manifest = active_manifest() or {}
    checks = {
        "config": config.config_id, "manifest_version": manifest.get("activation_impact_calculation_version"),
        "leverage_weight": leverage, "kills_observed": len(seen), "rows_recomputed": len(rows),
        "rows_stored": len(stored), "row_mismatches": row_mismatches, "sum_mismatches": sum_mismatches,
        "ok": not row_mismatches and not sum_mismatches and len(rows) == len(stored),
    }
    return per_kill, components, checks


def db_details(session, result: lk.LinkResult) -> dict:
    from app.models import ImpactScore, KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat

    match = session.get(Match, result.match_id)
    mp_ids = list(result.slot_to_match_player.values())
    mps = {mp.id: (mp, player) for mp, player in session.query(MatchPlayer, Player)
           .join(Player, MatchPlayer.player_id == Player.id).filter(MatchPlayer.id.in_(mp_ids))}
    rounds = session.query(Round).filter(Round.match_id == match.id).order_by(Round.round_number).all()
    by_id = {r.id: r for r in rounds}
    kills = {k.id: k for k in session.query(KillEvent).filter(KillEvent.round_id.in_(list(by_id)))}
    stats = session.query(RoundPlayerStat).filter(RoundPlayerStat.round_id.in_(list(by_id))).all()
    impacts = session.query(ImpactScore).filter(ImpactScore.round_id.in_(list(by_id))).all()
    return {"match": match, "players": mps, "rounds": rounds, "by_id": by_id, "kills": kills,
            "stats": stats, "impacts": impacts}


# ---------------------------------------------------------------- the bundle


def _team(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def build_bundle(replay, result: lk.LinkResult, db: dict, per_kill: dict, components: dict, impact_checks: dict,
                 extras, game_map) -> dict:
    offset = result.clock_offset or 0.0
    slot_of_mp = {mp: slot for slot, mp in result.slot_to_match_player.items()}
    side_of = {p["slot"]: p["side_group"] for p in replay.players}
    team_side = {team: side for side, team in result.side_to_team.items()}
    players = []
    for slot, mp_id in sorted(result.slot_to_match_player.items()):
        mp, player = db["players"][mp_id]
        players.append({"slot": slot, "name": player.display_name, "player_id": player.id,
                        "match_player_id": mp_id, "agent": mp.agent, "team": _team(mp.team),
                        "side": side_of.get(slot) or team_side.get(_team(mp.team))})

    stats_by_round: dict[int, dict] = defaultdict(dict)
    number_of = {r.id: r.round_number for r in db["rounds"]}
    for s in db["stats"]:
        slot = slot_of_mp.get(s.match_player_id)
        if slot is not None:
            stats_by_round[number_of[s.round_id]][str(slot)] = {
                "score": s.score, "kills": s.kills, "deaths": s.deaths, "assists": s.assists,
                "loadout": s.loadout, "remaining": s.remaining}
    for i in db["impacts"]:
        slot = slot_of_mp.get(i.match_player_id)
        if slot is not None:
            stats_by_round[number_of[i.round_id]].setdefault(str(slot), {}).update({
                "impact": i.impact, "kill_impact": i.kill_impact, "death_impact": i.death_impact,
                "damage": i.damage, "econ": i.econ_component, "trade_credit": i.trade_credit,
                **components.get((number_of[i.round_id], i.match_player_id), {})})

    db_round = {r.round_number: r for r in db["rounds"]}
    rounds = {}
    for n, blob in sorted(replay.rounds.items()):
        blob = json.loads(json.dumps(blob))
        ids = result.kill_map.get(str(n), [])
        for kill in blob["kills"]:
            kid = ids[kill["i"]] if kill["i"] < len(ids) else None
            row = db["kills"].get(kid) if kid is not None else None
            kill["db_id"] = kid
            kill["weapon"] = row.weapon if row is not None else None
            if kid in per_kill:
                kill.update(per_kill[kid])
        r = db_round.get(n)
        blob["db"] = None if r is None else {
            "outcome": r.outcome, "winner": lk.outcome_winner(r.outcome),
            "plant": None if r.plant_time is None else round(r.plant_time - offset, 3),
            "defuse": None if r.defuse_time is None else round(r.defuse_time - offset, 3),
            "exploded": r.exploded, "defused": r.defused}
        blob["stats"] = stats_by_round.get(n, {})
        blob["extras"] = extras.rounds.get(n, {"abilities": [], "shots": []})
        rounds[str(n)] = blob

    match = db["match"]
    return {
        "bundle_version": BUNDLE_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "match": {"uuid": replay.match_uuid, "match_id": match.id, "map": replay.map_name,
                  "played_at": match.played_at.isoformat() if match.played_at else None,
                  "team1_rounds_won": match.team1_rounds_won, "team2_rounds_won": match.team2_rounds_won,
                  "side_to_team": result.side_to_team, "clock_offset": offset, "recipe": replay.recipe,
                  # minimap units per world unit, for drawing ability radii
                  "uv_per_unit": round(abs(game_map.x_mult) * fmt.UV_SCALE, 5)},
        "players": players,
        "rounds": rounds,
        "link": result.report,
        "impact_check": impact_checks,
        "extras_report": extras.report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--vrf", type=Path, help="the exported .vrf (default: the archive's copy)")
    parser.add_argument("--parser-dir", type=Path, help="the parser build (default $REPLAY_PARSER_DIR)")
    parser.add_argument("--out", type=Path, help="default: <export-dir>/bundle.json")
    parser.add_argument("--reuse-condensed", action="store_true",
                        help="reuse the condensed replay from the last run (bundle-condensed.pickle); extras are rebuilt")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.export_dir)
    vrf = args.vrf or default_archive() / str(manifest.get("source_file") or "")
    if not vrf.is_file():
        raise SystemExit(f"REFUSED: the exported .vrf is not at {vrf}; pass --vrf")
    build_path = (args.parser_dir or default_parser_dir()) / "bin" / "BUILD.json"
    if not build_path.is_file():
        raise SystemExit(f"REFUSED: no {build_path}")
    started = time.time()
    cache = args.export_dir / "bundle-condensed.pickle"
    if args.reuse_condensed and cache.is_file():
        # A local scratch file this script wrote itself, beside the export.
        replay, players, windows, game_map, agents = pickle.loads(cache.read_bytes())
        positions = WorldPositions(Export({}, [], MovementStream(args.export_dir / "movement.ndjson"), 0), players)
        print(f"reused {cache.name}")
    else:
        try:
            replay, players, windows, game_map, agents, positions = condense_strict(
                args.export_dir, vrf, json.loads(build_path.read_text(encoding="utf-8-sig")))
        except ContractError as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 3
        cache.write_bytes(pickle.dumps((replay, players, windows, game_map, agents)))
        print(f"condensed {replay.map_name}, {replay.round_count} rounds ({time.time() - started:.0f} s)")
    extras = build_extras(args.export_dir / "events.ndjson", players, windows, game_map, agents, positions.at,
                          positions.path)
    print(f"extras: {extras.report} ({time.time() - started:.0f} s)")
    if not replay.link_inputs["eligibility"]["eligible"]:
        print(f"REFUSED: link eligibility: {replay.link_inputs['eligibility']['reasons']}", file=sys.stderr)
        return 1


    session = open_read_only_session()
    try:
        from ingest_replay import load_link_candidates  # the dry-run's own reader

        candidates, owners = load_link_candidates(session, replay.match_uuid)
        view = lk.ReplayLinkView(replay.match_uuid, replay.round_count, replay.report["dropped_final_round"],
                                 replay.players, replay.link_inputs)
        result = lk.link(view, candidates, owners)
        if result.status != "linked":
            print(json.dumps({"status": result.status, "report": result.report}, indent=2, default=str))
            return 1
        print(f"linked to match {result.match_id}: {result.report['anchors']} anchors, "
              f"offset {result.clock_offset} s")
        per_kill, components, impact_checks = per_kill_impact(session, result.match_id)
        print(f"impact check: {json.dumps(impact_checks)}")
        if not impact_checks["ok"]:
            print("REFUSED: per-kill Impact does not reconcile with the stored rows", file=sys.stderr)
            return 1
        details = db_details(session, result)
        bundle = build_bundle(replay, result, details, per_kill, components, impact_checks, extras, game_map)
    finally:
        session.rollback()
        session.close()

    out = args.out or args.export_dir / "bundle.json"
    temp_root = Path(os.environ.get("TEMP") or tempfile.gettempdir()).resolve()
    if WEBAPP_ROOT.parent.resolve() in out.resolve().parents:
        raise SystemExit("REFUSED: the bundle holds player names; write it outside the repo")
    out.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {time.time() - started:.0f} s)"
          + ("" if temp_root in out.resolve().parents else "  (outside %TEMP%)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
