"""Critical Util on the site: the replay viewer's tab (`match_payload`) and the cross-match page (`corpus`,
`page_for`). Design: docs/superpowers/specs/2026-10-21-critical-util-design.md.

The worker stores each round's casts and evidence (replay_round_util); this module values them
(app/services/util_values.py) with the stored estimator and weights (util_model "win" and "values") and shapes the
results. Only current rows are read (`current_rows`: the gap run's fingerprint, like Critical Moments). The corpus
products are rebuilt when what they are made from changes (`corpus_key`, `ensure_values`; the estimator refits
when a match it trained on gets a replay, util_model.ensure). Two numbers per cast: its `value`, the share of the
round's swings and jobs credited to it, and its `lift`, its outcome against the no-util baseline (the comparison
the page's scores and calls use; util_values "the no-util baseline").
Friends only: the page shows the viewer's circle (app/services/circle.py), never tracked_players.json. Opponents
are "the enemy <agent>" (app/services/enemy_names.py).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from app.config import settings
from app.coach import areas
from app.critutil import abilities, constants
from app.critutil.corpus import (  # re-export the portable build and its helpers
    COMBO_S, COMBO_UV, EXAMPLES_KEPT, FULL_BUY_THRESHOLD, LABELS, MIN_CASTS, NO_LIFT,
    PAGE_VERSION, PISTOL_ROUNDS, SHRINK_K,
    _ability_styles, _cast_rows, _gate_list, _item_of, _mark_combos, _round_data,
    ability_label, area_of, build_from_rounds, buy_type, corpus_from_inputs, kind_label,
    label_of, lineup_score, values_key,
)
from app.services import util_model, util_store, util_values
from app.services.replay_gaps import Freshness  # re-export the shared lightweight rule


def winner_side(outcome: str | None, round_number: int) -> str | None:
    from app.scoring.plant_window import attacking_team
    from app.services.state_replay import _winner_team

    winner, attack = _winner_team(outcome), attacking_team(round_number)
    if winner is None or attack is None:
        return None
    return "attack" if winner == attack else "defense"


# --- loading
# The values build is a zlib blob, decoded and cached by util_store.


def _values_row(db) -> tuple[str, dict] | None:
    """(key, data) of the stored corpus build, or None."""
    return util_store.read_values(db)


def current_rows(db, replay_ids=None, *, with_data: bool = True) -> list[tuple]:
    """(replay id, round, fingerprint, computed_at[, data]) of every ok util row that is current (`Freshness`).
    What every reader values: the page's corpus, the tab and the coach (through the tab's payload)."""
    from app.models.replay import Replay, ReplayRoundUtil
    from app.services import replay_gaps

    cols = [ReplayRoundUtil.replay_id, ReplayRoundUtil.round_number, ReplayRoundUtil.fingerprint,
            ReplayRoundUtil.computed_at] + ([ReplayRoundUtil.data] if with_data else [])
    q = db.query(*cols).filter(ReplayRoundUtil.status == "ok", ReplayRoundUtil.gaps_revision == replay_gaps.GAPS_REVISION)
    if replay_ids is not None:
        q = q.filter(ReplayRoundUtil.replay_id.in_(list(replay_ids)))
    rows = q.all()
    ids = {r[0] for r in rows}
    replays = {r.id: r for r in db.query(Replay).filter(Replay.id.in_(list(ids)))} if ids else {}
    fresh = Freshness(db)
    fresh.preload(replays)
    return [tuple(r) for r in rows if r[0] in replays
            and fresh.current(replays[r[0]], r[1], replay_gaps.GAPS_REVISION, r[2])]


def _stored_rounds(db, replay_ids=None, rows=None):
    """{replay_id: {round: data}} of every current ok util row (`current_rows`)."""
    out: dict[int, dict] = defaultdict(dict)
    for r in rows if rows is not None else current_rows(db, replay_ids):
        if r[4]:
            out[r[0]][r[1]] = r[4]
    return out


ON_SPANS_FROM = 22          # CONDENSE revision from which Viper's Poison Cloud `on` spans exist


def on_known(recipe: str | None) -> bool:
    """Whether a replay's blob has the `on` spans (condense revision ON_SPANS_FROM or later)."""
    from app.services import replay_control

    return (replay_control.condense_revision(recipe or "") or 0) >= ON_SPANS_FROM


def _replay_context(db, replay_ids):
    """Per replay: map, uuid, match id, slot -> (player_id, agent, team), round -> (winner side, number), and each
    slot's team's average start loadout per round."""
    from app.models.match import MatchPlayer
    from app.models.replay import Replay, ReplayPlayer
    from app.models.round import Round, RoundPlayerStat
    from app.services import replay_control

    out = {}
    replays = db.query(Replay.id, Replay.map_name, Replay.match_uuid, Replay.match_id, Replay.clock_offset,
                       Replay.recipe, Replay.game_branch).filter(
        Replay.id.in_(list(replay_ids)), Replay.match_id.is_not(None)).all()
    match_of = {r.id: r.match_id for r in replays}
    players: dict[int, dict] = defaultdict(dict)
    mp_slot: dict[int, tuple[int, int]] = {}
    for rp, mp in (db.query(ReplayPlayer, MatchPlayer).join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
                   .filter(ReplayPlayer.replay_id.in_(list(match_of)))):
        team = mp.team.value if hasattr(mp.team, "value") else str(mp.team)
        players[rp.replay_id][rp.slot] = {"player_id": mp.player_id, "agent": mp.agent, "team": team}
        mp_slot[mp.id] = (rp.replay_id, rp.slot)
    rounds: dict[int, dict] = defaultdict(dict)
    round_ids = {}
    by_match = defaultdict(list)
    for rid, mid in match_of.items():
        by_match[mid].append(rid)
    for r in db.query(Round.id, Round.match_id, Round.round_number, Round.outcome).filter(
            Round.match_id.in_(list(by_match))):
        for rid in by_match[r.match_id]:
            rounds[rid][r.round_number] = {"winner": winner_side(r.outcome, r.round_number), "id": r.id}
            round_ids[r.id] = (rid, r.round_number)
    loadouts: dict[tuple, list] = defaultdict(list)
    for s in db.query(RoundPlayerStat.round_id, RoundPlayerStat.match_player_id, RoundPlayerStat.loadout).filter(
            RoundPlayerStat.round_id.in_(list(round_ids))):
        rid, n = round_ids[s.round_id]
        slot = mp_slot.get(s.match_player_id)
        if slot is None:
            continue
        team = players[slot[0]].get(slot[1], {}).get("team")
        loadouts[(rid, n, team)].append(s.loadout or 0)
    for r in replays:
        selection = replay_control.layout_of(r)
        asset_key = selection.layout.asset_key if selection.state == "ok" else None
        out[r.id] = {"map": r.map_name, "asset_key": asset_key, "uuid": str(r.match_uuid), "match_id": r.match_id,
                     "offset": r.clock_offset or 0.0, "on_known": on_known(r.recipe),
                     "players": players.get(r.id, {}), "rounds": rounds.get(r.id, {}),
                     "loadout": {(n, team): sum(v) / len(v) for (rid, n, team), v in loadouts.items()
                                 if rid == r.id and v}}
    return out


# --- per cast rows


# --- the corpus


REBUILD_TRIES = 3


def corpus_key(db, rows: list[tuple], model: dict | None, *, painted_table: dict[str, areas.Painted] | None = None) -> str:
    """What a corpus build is made from, as one key: the versions, every current util row it reads (round,
    fingerprint, when computed), each of their replays' match and clock offset, the estimator's key, and the
    reviewed map areas. Computed from the very rows a build reads, so the key stored with a build always describes
    that build's inputs."""
    return values_key(corpus_key_parts(db, rows, painted_table=painted_table), (model or {}).get("key"))


def corpus_key_parts(db, rows: list[tuple], *, painted_table: dict[str, areas.Painted] | None = None) -> dict:
    """Freeze the metadata hashed by corpus_key, apart from the estimator's key. `painted_table` is the published
    paint (read here when not given); only its hash is kept.

    Timestamp text retains the original key's timezone/naive spelling; the wire rows separately carry UTC
    microseconds. Neither requires loading the util blobs.
    """
    from app.models.replay import Replay

    ids = sorted({r[0] for r in rows})
    replays = sorted(db.query(Replay.id, Replay.match_id, Replay.clock_offset).filter(Replay.id.in_(ids))) if ids else []
    friends = hashlib.sha256(",".join(str(p) for p in sorted(friend_players(db))).encode()).hexdigest()
    return {"values_version": util_values.VERSION, "page_version": PAGE_VERSION,
            "abilities_hash": abilities.table_hash(), "constants_version": constants.VERSION,
            "friends_hash": friends, "rows": [[rid, n, fp, str(at)] for rid, n, fp, at, *_ in rows],
            "replays": [list(r) for r in replays],
            "areas_hash": areas.table_hash(painted_table=areas.load_table(published_only=True)
                                           if painted_table is None else painted_table)}


def friend_players(db) -> set[int]:
    """Every player id in any friendship: the players whose casts get a Yours baseline."""
    from app.models.friendship import Friendship

    return {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id) for pid in pair}


def build_corpus(db, *, rows: list[tuple] | None = None, model: dict | None = None,
                 painted_table: dict[str, areas.Painted] | None = None) -> dict:
    """The stored build's data (`build_from_rounds`) from `rows` (`current_rows`; read here when not given), the
    estimator `model` (the stored one when not given) and the friendships, with each row's fingerprint added.
    Reads only: `ensure_values` stores it. Ends the caller's transaction (commit) before computing, so no
    connection is held through the build."""
    rows = current_rows(db) if rows is None else rows
    model = util_model.load(db) if model is None else model
    if painted_table is None:
        painted_table = areas.load_table(published_only=True)
    inputs = corpus_inputs(db, rows)
    db.commit()
    return corpus_from_inputs(inputs, rows, model, painted_table=painted_table)


def corpus_inputs(db, rows: list[tuple]) -> tuple[dict, dict, set]:
    """Everything build_corpus reads from the database for `rows`: (stored rounds, replay context, friend
    players). Reading them first lets a caller end its transaction before the minutes of computing."""
    stored = _stored_rounds(db, rows=rows)
    return stored, _replay_context(db, set(stored)), friend_players(db)


def _store_values(db, key: str, data: dict) -> None:
    from sqlalchemy.exc import IntegrityError

    try:
        util_store.store_values(db, key, data)
        db.commit()
    except IntegrityError:
        db.rollback()       # another process stored the first row meanwhile (no lock on this database): keep theirs


def ensure_values(db, *, force: bool = False) -> dict:
    """The stored corpus build, rebuilt when what it is made from changed (`corpus_key`) or with `force`.

    No connection or transaction is held while it computes (R3); the result is stored under a short lock, under
    the key of the inputs it was built from, even if superseded (D6). The key stored is computed from the rows the build read, and when the rows changed while it was building it
    builds again (up to REBUILD_TRIES), so an older build is never stored under a newer key, and a request never
    goes on with a build it knows is stale."""
    data = None
    for _ in range(REBUILD_TRIES):
        model = util_model.ensure(db)
        key = corpus_key(db, current_rows(db, with_data=False), model)
        stored = _values_row(db)
        if not force and stored is not None and stored[0] == key:
            return stored[1]
        rows = current_rows(db)
        painted_table = areas.load_table(published_only=True)
        used = corpus_key(db, rows, model, painted_table=painted_table)
        inputs = corpus_inputs(db, rows)
        db.commit()                         # nothing is held through the compute (R3)
        data = corpus_from_inputs(inputs, rows, model, painted_table=painted_table)
        with util_model.rebuild_lock(db, "values"):     # a short transaction: the lock, the row, the commit
            _store_values(db, used, data)   # stored even if superseded (D6)
        force = False
        if corpus_key(db, current_rows(db, with_data=False), util_model.load(db)) == used:
            return data
    return data


def values_current(db, stored: tuple[str, dict] | None = None, rows: list[tuple] | None = None) -> bool:
    """Whether the stored build is what `ensure_values` would return now, without building anything: the stored
    estimator covers every match with a replay, and the build's key matches its inputs. `rows`: the caller's
    `current_rows(db, with_data=False)`, when it has them already."""
    model = util_model.load(db)
    if not util_model.covers(model, util_model.replay_match_ids(db)):
        return False
    stored = _values_row(db) if stored is None else stored
    rows = current_rows(db, with_data=False) if rows is None else rows
    return stored is not None and stored[0] == corpus_key(db, rows, model)


def page_values(db) -> tuple[dict | None, bool]:
    """(the stored build or None, whether it is current), for a request: never builds. When it is stale or missing
    the caller shows what is stored. Local mode starts a background child (app/services/util_rebuild.py).
    Remote mode leaves dispatch to the util-build loop."""
    from app.services import util_build_remote, util_rebuild

    stored = _values_row(db)
    if values_current(db, stored):
        return stored[1], True
    if not util_build_remote.enabled():
        util_rebuild.request()
    return (stored[1] if stored else None), False


# --- the cross-match page


def _summ(values):
    n = len(values)
    mean = sum(values) / n if n else 0.0
    return n, mean


def page_for(corpus: dict, circle: set[int], mode: str = "yours") -> dict:
    """The page's tables for one viewer's circle: friends' casts only. `mode` ("yours" or "all") picks the baseline
    each cast is compared with: the caster's own moments or every player's (a lift counts only where the gate
    passed at the cast's length, R2)."""
    mode = mode if mode in ("yours", "all") else "yours"
    lk, gk = f"lift_{mode}", f"gate_{mode}"
    rows = [r for r in corpus.get("rows") or () if r.get("player_id") in circle and r.get("scored", True)]
    spread = (corpus.get("spread_by_mode") or {}).get(mode, corpus.get("spread")) or 0.0
    sums = (corpus.get("prior_sums") or {}).get(mode) or {}
    gate = (corpus.get("gate") or {}).get(mode)
    out: dict = {"n": len(rows), "fit": corpus.get("fit"), "estimator": corpus.get("estimator"),
                 "overlap": corpus.get("overlap") or {}, "mode": mode,
                 "failing": [L for L in range(1, util_values.MAX_L + 1) if not gate[L - 1]["pass"]] if gate else []}

    def compared(r):
        return r[lk] if r.get(lk) is not None and r.get(gk) else None

    def mean_lift(v):
        lifts = [x for x in map(compared, v) if x is not None]
        return (round(100 * sum(lifts) / len(lifts), 2) if lifts else None), len(lifts)

    wins: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            wins[(r["ability"], r["agent"], r["map"], r["side"])].append(r)
    out["wins_rounds"] = sorted(
        ({"ability": k[0], "agent": k[1], "map": k[2], "side": k[3], "n": len(v),
          "matches": len({r["match"] for r in v}), "value": round(100 * sum(r["value"] for r in v) / len(v), 2),
          "lift": mean_lift(v)[0], "compared": mean_lift(v)[1],
          "round_win": round(100 * sum(1 for r in v if r["won"]) / len(v), 1)}
         for k, v in wins.items() if len(v) >= MIN_CASTS), key=lambda x: -x["value"])

    lineups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            lineups[(r["player_id"], r["agent"], r["ability"], r["map"], r["side"], r["area"])].append(r)
    calls = []
    for k, v in lineups.items():
        if len(v) < MIN_CASTS:
            continue
        lifts = [x for x in map(compared, v) if x is not None]
        left = {r["match"] for r in v}
        cells = sums.get(k[2]) or {}
        pn = sum(c[0] for mm, c in cells.items() if int(mm) not in left)
        ps = sum(c[1] for mm, c in cells.items() if int(mm) not in left)
        ability_prior = ps / pn if pn else 0.0
        if "prior_sums" not in corpus:      # an old build: its all-players prior
            ability_prior = (corpus.get("ability_prior") or {}).get(k[2], 0.0)
        score = lineup_score(lifts, ability_prior)
        if score is not None:
            call = util_values.call(score, spread)
        elif mode == "yours" and all(r.get("lift_yours") is None for r in v):
            call = "no personal comparison yet"
        elif any(r.get(lk) is not None for r in v):
            call = "baseline not validated for this window"
        else:
            call = "no comparison yet"
        calls.append({"player_id": k[0], "agent": k[1], "ability": k[2], "map": k[3], "side": k[4], "area": k[5],
                      "n": len(v), "compared": len(lifts), "matches": len({r["match"] for r in v}),
                      "value": round(100 * sum(r["value"] for r in v) / len(v), 2),
                      "score": None if score is None else round(100 * score, 2),
                      "prior": round(100 * ability_prior, 2), "call": call})
    out["lineups"] = sorted(calls, key=lambda c: -(c["score"] if c["score"] is not None else -1e9))

    by_player: dict[int, list] = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(r)
    out["players"] = {pid: _player_block(v) for pid, v in by_player.items()}
    out["combos"] = _combo_table(rows)
    out["ults"] = _ult_table(rows)
    return out


def _example(r) -> dict:
    return {k: r.get(k) for k in ("uuid", "round", "t", "agent", "ability", "map", "side", "area", "value", "kind",
                                  "features", "harm_died", "wasted")} | {"value": round(100 * r["value"], 2)}


def _player_block(rows: list[dict]) -> dict:
    casts = [r for r in rows if r["kind"] != "heal"]
    heals = [r for r in rows if r["kind"] == "heal"]
    ranked = sorted(casts, key=lambda r: r["value"])

    def share(pred, among):
        return round(100 * sum(1 for r in among if pred(r)) / len(among), 1) if among else None

    def mean(among):
        return round(100 * sum(r["value"] for r in among) / len(among), 2) if among else None

    affecting = [r for r in casts if r["followed"] is not None]
    patterns = {
        "phase": {ph: {"n": len(g), "value": mean(g)} for ph in ("pre_contact", "contact", "post_plant")
                  for g in [[r for r in casts if r["phase"] == ph]]},
        "buy": {b: {"n": len(g), "value": mean(g)} for b in ("pistol", "full buy", "other")
                for g in [[r for r in casts if r["buy"] == b]]},
        "support_rate": share(lambda r: r["followed"], affecting),
        "waste_rate": share(lambda r: r["wasted"],
                            [r for r in casts if set(r.get("kinds") or ()) & util_values.JUDGED_KINDS]),
        "harm_rate": share(lambda r: r["harm"] > 0, casts),
        "harm_deaths": sum(r["harm_died"] for r in casts),
        "timing": _timing(casts),
    }
    return {"casts": len(casts), "value": mean(casts), "best": [_example(r) for r in ranked[::-1][:EXAMPLES_KEPT]],
            "worst": [_example(r) for r in ranked[:EXAMPLES_KEPT]], "patterns": patterns,
            "heals": {"n": len(heals), "saved": sum(1 for r in heals if (r.get("heal") or {}).get("saved")),
                      "value": mean(heals)},
            "areas": _area_repeats(casts)}


def _timing(casts):
    buckets = {"0-20s": (0, 20), "20-45s": (20, 45), "45-70s": (45, 70), "70s+": (70, 999)}
    return {k: {"n": len(g), "value": round(100 * sum(r["value"] for r in g) / len(g), 2) if g else None}
            for k, (a, b) in buckets.items() for g in [[r for r in casts if a <= (r["t"] or 0) < b]]}


def _area_repeats(casts):
    """How often the same ability went into the same area, and its value by round number within the match (first
    half of uses vs second): area patterns, not proof of a lineup or of enemies adapting."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in casts:
        groups[(r["ability"], r["map"], r["side"], r["area"])].append(r)
    out = []
    for k, v in groups.items():
        if len(v) < 3:
            continue
        by_match: dict[int, list] = defaultdict(list)
        for r in v:
            by_match[r["match"]].append(r)
        early, late = [], []
        for ms in by_match.values():
            ms.sort(key=lambda r: r["round"])
            half = len(ms) // 2
            early += ms[:half] if half else []
            late += ms[half:] if half else []
        out.append({"ability": k[0], "map": k[1], "side": k[2], "area": k[3], "n": len(v),
                    "per_match": round(len(v) / len(by_match), 1),
                    "early": round(100 * sum(r["value"] for r in early) / len(early), 2) if early else None,
                    "late": round(100 * sum(r["value"] for r in late) / len(late), 2) if late else None})
    return sorted(out, key=lambda x: -x["n"])[:10]


def _combo_table(rows):
    by_key = {(r["replay"], r["round"], r["i"]): r for r in rows if r["i"] is not None}
    pairs: dict[tuple, list] = defaultdict(list)
    for r in by_key.values():
        for j in r.get("combo") or ():
            other = by_key.get((r["replay"], r["round"], j))
            if other is None or (r["i"], r["agent"]) > (other["i"], other["agent"]):
                continue
            a, b = sorted([f"{r['agent']} {r['ability']}", f"{other['agent']} {other['ability']}"])
            pairs[(a, b)].append(r["value"] + other["value"])
    return sorted(({"pair": list(k), "n": len(v), "value": round(100 * sum(v) / len(v), 2)}
                   for k, v in pairs.items() if len(v) >= 3), key=lambda x: -x["value"])[:20]


def _ult_table(rows):
    ults: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if "ult" in (r.get("kinds") or ()):
            ults[(r["agent"], r["ability"])].append(r)
    out = []
    for (agent, ability), v in ults.items():
        pts = [r["ult_points"] for r in v if r.get("ult_points")]
        value = sum(r["value"] for r in v) / len(v)
        out.append({"agent": agent, "ability": ability, "n": len(v), "value": round(100 * value, 2),
                    "per_point": round(100 * value / (sum(pts) / len(pts)), 3) if pts else None,
                    "phase": max(("pre_contact", "contact", "post_plant"),
                                 key=lambda ph: sum(1 for r in v if r["phase"] == ph))})
    return sorted(out, key=lambda x: -(x["per_point"] or -1e9))


# --- the replay viewer's tab


def match_payload(db, replay, *, wait: bool = False, read_only: bool = False) -> dict:
    """The tab's body for one linked replay: each player's casts with values, best and worst, unused util, harm.
    Opponents named by agent.

    When the stored build (or the estimator) is out of date, `wait` rebuilds it first. With `wait=False`,
    the rebuild starts in the background (util_rebuild) and the body says `updating`,
    valued meanwhile with the stored estimator and weights. Remote mode always reads the stored products
    and leaves rebuilding to the util-build loop, including when `wait` is requested. `read_only` uses stored
    products and reports `updating` when stale, with every build and request disabled."""
    from sqlalchemy import or_

    from app.models.friendship import Friendship
    from app.models.replay import ReplayRoundUtil
    from app.services import replays as replay_service
    from app.services import util_build_remote
    from app.services.replay_moments import tab_players

    rows = db.query(ReplayRoundUtil).filter(ReplayRoundUtil.replay_id == replay.id).all()
    stored = {r.round_number: r for r in rows}
    page = replay_service.page_context(db, replay)["players"]
    ids = [p["player_id"] for p in page.values() if p.get("player_id") is not None]
    friends = {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id)
               .filter(or_(Friendship.owner_player_id.in_(ids), Friendship.friend_player_id.in_(ids)))
               for pid in pair} if ids else set()
    players = tab_players(page, friends)
    ctx = _replay_context(db, {replay.id}).get(replay.id)
    model = util_model.load(db)
    build_local = not util_build_remote.enabled() and not read_only
    updating = read_only and model is None
    if ctx is not None and not util_model.covers(model, {ctx["match_id"]}):
        # this match was in the stored estimator's training: refit it first, or (not waiting) in the background
        if build_local and (wait or model is None):
            model = util_model.ensure(db)
        else:
            updating = True
    key, corpus = _values_row(db) or (None, {})
    weights = util_values.weights_for(corpus.get("fit"), (ctx or {}).get("match_id"))
    rounds_out, stale, failed = {}, 0, []
    current = {}
    fresh = Freshness(db)
    for n in range(1, replay.round_count + 1):
        row = stored.get(n)
        if row is None or not fresh.current(replay, n, row.gaps_revision, row.fingerprint):
            stale += 1
            continue
        if row.status != "ok" or not row.data:
            failed.append(n)
            continue
        current[n] = row.data
    covered = corpus.get("fingerprints", {}).get(str(replay.id), {})
    # rebuilt when the stored build does not cover this replay's rounds or anything else it is made from changed
    # (another replay's rows, the estimator, the table, the friendships: `corpus_key`): first when waiting, else in
    # the background, once per process at a time
    if updating or any(stored[n].fingerprint != covered.get(str(n)) for n in current) \
            or key != corpus_key(db, current_rows(db, with_data=False), model):
        if wait and build_local:
            ensure_values(db)
            corpus = (_values_row(db) or (None, {}))[1]
            model = util_model.load(db)     # the rebuild may have refit it: score with the build's (branch review B14)
            if ctx is not None and not util_model.covers(model, {ctx["match_id"]}):
                model = util_model.ensure(db)
            weights = util_values.weights_for(corpus.get("fit"), (ctx or {}).get("match_id"))
            updating = False
        else:
            from app.services import util_rebuild

            if build_local:
                util_rebuild.request()
            updating = True
    built = {(r["replay"], r["round"], r["i"], r["slot"] if r["i"] is None else None, r["t"] if r["i"] is None else None): r
             for r in corpus.get("rows") or () if r.get("replay") == replay.id}
    flat = _cast_rows(replay.id, ctx, current, model, weights, include_unscored=True,
                      painted_table=areas.load_table(published_only=True)) if ctx else []
    for r in flat:          # the lift comes from the stored build (the same cast there), when it has one
        b = built.get((r["replay"], r["round"], r["i"], r["slot"] if r["i"] is None else None,
                       r["t"] if r["i"] is None else None))
        if b is not None:
            r.update(area=b.get("area"), lift=b.get("lift"), base_n=b.get("base_n", 0), base_level=b.get("base_level"))
            r.update({k: b.get(k) for k in ("type", "L", "lift_all", "lift_yours", "yours_level", "yours_n", "gate_all",
                                            "gate_yours", "lift_device", "device_level", "device_n", "gate_device")})
    by_round: dict[int, list] = defaultdict(list)
    for r in flat:
        by_round[r["round"]].append(r)
    ults = unused_ults(current)
    for n, data in current.items():
        casts = {c["i"]: c for c in data.get("casts") or ()}
        out = []
        for r in by_round.get(n, []):
            c = casts.get(r["i"]) or {}
            out.append({**{k: r[k] for k in ("i", "slot", "agent", "ability", "kind", "kinds", "side", "area", "phase",
                                             "t", "wasted", "wasted_why", "found_nobody", "followed", "enemies", "harm_died",
                                             "combo", "scored", "base_n", "base_level")
                           if k in r},
                        **{k: r.get(k) for k in ("label", "type", "L", "gate_all", "gate_yours", "gate_device",
                                                 "yours_level", "yours_n", "device_level", "device_n")},
                        **{k: None if r.get(k) is None else round(100 * r[k], 2)
                           for k in ("lift_all", "lift_yours", "lift_device")},
                        "signals": c.get("signals"),
                        "value": round(100 * r["value"], 2) if r["scored"] else None,
                        "ledger": round(100 * r["ledger"], 2), "jobs": round(100 * r["jobs"], 2),
                        "lift": None if r.get("lift") is None else round(100 * r["lift"], 2),
                        "features": r["features"],
                        "kills": c.get("kills") or [], "harm": c.get("harm") or [], "effects": c.get("effects") or [],
                        "cover": c.get("cover"), "presence": c.get("presence"), "trigger": c.get("trigger"),
                        "hold": c.get("hold"),
                        "heal": r.get("heal")})
        rounds_out[str(n)] = {"casts": out, "unused": (data.get("unused") or []) + ults.get(n, []),
                              "avail": data.get("avail") or {}}
    summary = {}
    for slot in players:
        mine = [r for r in flat if str(r["slot"]) == str(slot) and r["kind"] != "heal" and r["scored"]]
        ranked = sorted(mine, key=lambda r: r["value"])
        pick = lambda r: {"round": r["round"], "t": r["t"], "i": r["i"], "ability": r["ability"],  # noqa: E731
                          "value": round(100 * r["value"], 2)}
        summary[str(slot)] = {"casts": len(mine), "best": [pick(r) for r in ranked[::-1][:3] if r["value"] > 0],
                              "worst": [pick(r) for r in ranked[:3] if r["value"] < 0],
                              "value": round(100 * sum(r["value"] for r in mine), 2)}
    return {"players": players, "rounds": rounds_out, "summary": summary, "stale": stale,
            "failed": sorted(failed), "estimator": bool(model and model.get("n")),
            "weights_fitted": weights != util_values.DEFAULT_WEIGHTS,
            "baseline": bool(corpus.get("rows")), "updating": updating}


def unused_ults(rounds: dict[int, dict]) -> dict[int, list]:
    """Full ults never used, once per player-round, from one match's rounds (spec, "Unused util"). A slot's ult
    cost is proven by the match: the most points it held just before any use in any round (extract `_ults`). A
    round where they ended (died, or the round was decided) holding that many and never used it is one line. No
    claim for a slot that never used its ult in the match: its cost is unproven."""
    caps: dict[str, int] = {}
    for data in rounds.values():
        for slot, u in (data.get("ults") or {}).items():
            if u.get("cap_seen"):
                caps[slot] = max(caps.get(slot, 0), int(u["cap_seen"]))
    out: dict[int, list] = defaultdict(list)
    for n, data in rounds.items():
        for slot, u in (data.get("ults") or {}).items():
            cap = caps.get(slot)
            if cap and u.get("end") is not None and u["end"] >= cap and not u.get("used"):
                out[n].append({"slot": int(slot), "t": u.get("t"), "kind": "ult_unused", "points": u["end"]})
    return out
