"""Critical Util on the site: the replay viewer's tab (`match_payload`) and the cross-match page (`corpus`,
`page_for`). Design: docs/superpowers/specs/2026-10-21-critical-util-design.md.

The worker stores each round's casts and evidence (replay_round_util); this module values them
(app/services/util_values.py) with the stored estimator and weights (util_model "win" and "values") and shapes the
results. The corpus products are rebuilt when the stored evidence or the estimator changes (`ensure_values`).
Friends only: the page shows the viewer's circle (app/services/circle.py), never tracked_players.json. Opponents
are "the enemy <agent>" (app/services/enemy_names.py).
"""

from __future__ import annotations

import functools
import hashlib
import re
from collections import defaultdict
from pathlib import Path

from app.services import util_model, util_values

PAGE_VERSION = 1
EXAMPLES_KEPT = 5
COMBO_S = 3.0               # measure (spec, "Measurements" 4)
COMBO_UV = 700              # minimap units (0..10000): about 8 m on a mid-size map; measure
SHRINK_K = 5.0
MIN_CASTS = 5


# --- names


@functools.lru_cache(maxsize=1)
def _ability_styles() -> list[tuple[re.Pattern, str]]:
    """The replay viewer's own code -> ability name table (app/static/js/replay.js ABILITY_STYLES), so the tab and
    the viewer name an ability the same way."""
    path = Path(__file__).resolve().parents[1] / "static" / "js" / "replay.js"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for pattern, label in re.findall(r'\[/(\^[^/]+)/, \{ ability: "([^"]+)"', text):
        try:
            out.append((re.compile(pattern), label))
        except re.error:
            continue
    return out


def ability_label(cast: dict) -> str:
    agent = cast.get("agent") or ""
    codes = [c for c in cast.get("codes") or () if c]
    names = [n for n in cast.get("names") or () if n]
    for name in names:
        for code in codes or [agent]:
            key = f"{code}_{name}"
            for pattern, label in _ability_styles():
                if pattern.match(key):
                    return label
    for name in names:
        for pattern, label in _ability_styles():
            if pattern.search("_" + name) or pattern.pattern.endswith(name + "$"):
                return label
    for name in names:          # a flash row's own name: "phoenix_curveball_left" -> "Curveball"
        if name.islower() and "_" in name:
            words = [w for w in name.split("_") if w not in ("left", "right")]
            if agent and words and words[0] == agent.lower().replace("/", ""):
                words = words[1:]
            if words:
                return " ".join(w.capitalize() for w in words)
    return names[0] if names else "utility"


def kind_label(kinds) -> str:
    order = ("ult", "blind", "smoke", "wall", "damaging", "status", "info", "device", "heal", "other")
    for k in order:
        if k in (kinds or ()):
            return k
    return "other"


# --- areas and economy


def area_of(map_name: str, u, v, side: str | None) -> str:
    from app.coach import areas

    if u is None or v is None:
        return "unknown"
    found = areas.area_at(map_name, u, v)
    sup = (found or {}).get("super")
    if sup in ("Attacker Side", "Defender Side"):
        own = (sup == "Attacker Side") == (side == "attack")
        return "own half" if own else "enemy half"
    return sup or "unknown"


def buy_type(round_number: int, avg_loadout: float | None) -> str:
    from app.scoring.impact import econ_tier_name
    from app.services.economy_graphs import PISTOL_ROUNDS

    if round_number in PISTOL_ROUNDS:
        return "pistol"
    if avg_loadout is not None and econ_tier_name(int(avg_loadout)) == "FULL_BUY":
        return "full buy"
    return "other"


def winner_side(outcome: str | None, round_number: int) -> str | None:
    from app.scoring.plant_window import attacking_team
    from app.services.state_replay import _winner_team

    winner, attack = _winner_team(outcome), attacking_team(round_number)
    if winner is None or attack is None:
        return None
    return "attack" if winner == attack else "defense"


# --- loading


def _weights(db) -> dict:
    from app.models.replay import UtilModel

    row = db.get(UtilModel, "values")
    if row is None or row.version != util_values.VERSION:
        return dict(util_values.DEFAULT_WEIGHTS)
    return (row.data or {}).get("fit", {}).get("weights") or dict(util_values.DEFAULT_WEIGHTS)


def _stored_rounds(db, replay_ids=None):
    """{replay_id: {round: data}} of every current ok util row."""
    from app.models.replay import ReplayRoundUtil
    from app.services import replay_gaps

    q = db.query(ReplayRoundUtil.replay_id, ReplayRoundUtil.round_number, ReplayRoundUtil.data).filter(
        ReplayRoundUtil.status == "ok", ReplayRoundUtil.gaps_revision == replay_gaps.GAPS_REVISION)
    if replay_ids is not None:
        q = q.filter(ReplayRoundUtil.replay_id.in_(list(replay_ids)))
    out: dict[int, dict] = defaultdict(dict)
    for rid, n, data in q:
        if data:
            out[rid][n] = data
    return out


def _replay_context(db, replay_ids):
    """Per replay: map, uuid, match id, slot -> (player_id, agent, team), round -> (winner side, number), and each
    slot's team's average start loadout per round."""
    from app.models.match import MatchPlayer
    from app.models.replay import Replay, ReplayPlayer
    from app.models.round import Round, RoundPlayerStat

    out = {}
    replays = db.query(Replay.id, Replay.map_name, Replay.match_uuid, Replay.match_id, Replay.clock_offset).filter(
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
        out[r.id] = {"map": r.map_name, "uuid": str(r.match_uuid), "match_id": r.match_id,
                     "offset": r.clock_offset or 0.0,
                     "players": players.get(r.id, {}), "rounds": rounds.get(r.id, {}),
                     "loadout": {(n, team): sum(v) / len(v) for (rid, n, team), v in loadouts.items()
                                 if rid == r.id and v}}
    return out


# --- per cast rows


def _cast_rows(replay_id: int, ctx: dict, rounds: dict, model, weights) -> list[dict]:
    """Every valued cast of one replay, flat, with who threw it and where, and its combo partners."""
    rows = []
    for n, data in sorted(rounds.items()):
        data = {**data, "clock_offset": ctx.get("offset", 0.0)}
        valued = util_values.value_round(data, model, weights)
        sides = data.get("sides") or {}
        casts = data.get("casts") or []
        for c in casts:
            v = valued["casts"].get(c["i"]) or {}
            if not v.get("scored"):
                continue
            p = ctx["players"].get(c["by"]) or {}
            team = p.get("team")
            rows.append({
                "replay": replay_id, "uuid": ctx["uuid"], "map": ctx["map"], "match": ctx["match_id"], "round": n,
                "i": c["i"], "slot": c["by"], "player_id": p.get("player_id"), "agent": c.get("agent") or p.get("agent"),
                "ability": ability_label(c), "kind": kind_label(c.get("kinds")), "kinds": c.get("kinds"),
                "side": c.get("side"), "area": area_of(ctx["map"], c.get("u"), c.get("v"), c.get("side")),
                "phase": c.get("phase"), "t": c["t"],
                "buy": buy_type(n, ctx["loadout"].get((n, team))),
                "won": (ctx["rounds"].get(n) or {}).get("winner") == c.get("side") if c.get("side") else None,
                "value": v["total"], "ledger": v["ledger"], "jobs": v["jobs"], "wasted": v.get("wasted", False),
                "found_nobody": v.get("found_nobody", False),
                "harm": len(c.get("harm") or ()), "harm_died": sum(1 for h in c.get("harm") or () if h.get("died")),
                "followed": c.get("followed"), "ult_points": c.get("ult_points"),
                "enemies": len(c.get("enemies") or ()), "features": v["features"],
            })
        _mark_combos([r for r in rows if r["round"] == n], casts)
        for h in valued["heals"]:
            p = ctx["players"].get(h.get("by")) or {}
            rows.append({"replay": replay_id, "uuid": ctx["uuid"], "map": ctx["map"], "match": ctx["match_id"],
                         "round": n, "i": None, "slot": h.get("by"), "player_id": p.get("player_id"),
                         "agent": p.get("agent"), "ability": ability_label({"agent": p.get("agent"),
                                                                            "codes": [h.get("code")],
                                                                            "names": [h.get("name")]}),
                         "kind": "heal", "kinds": ["heal"], "side": sides.get(str(h.get("by"))), "area": "unknown",
                         "phase": None, "t": h["t"], "buy": buy_type(n, None), "won": None,
                         "value": h["value"], "ledger": 0.0, "jobs": h["value"], "wasted": False, "harm": 0,
                         "harm_died": 0, "followed": None, "ult_points": None, "enemies": 0,
                         "features": {"saved": bool(h.get("saved")), "amount": h.get("amount")},
                         "heal": {"target": h.get("target"), "saved": h.get("saved"), "fought": h.get("fought")}})
    return rows


def _mark_combos(rows: list[dict], casts: list[dict]) -> None:
    """Casts by teammates within COMBO_S of each other, on the same enemy or within COMBO_M: each row gets the
    (round-local) indices of its partners."""
    by_i = {c["i"]: c for c in casts}
    for r in rows:
        r["combo"] = []
    for a in rows:
        ca = by_i.get(a["i"])
        for b in rows:
            if b is a or b["slot"] == a["slot"] or b["side"] != a["side"]:
                continue
            cb = by_i.get(b["i"])
            if ca is None or cb is None or abs(ca["t"] - cb["t"]) > COMBO_S:
                continue
            same_enemy = bool(set(ca.get("enemies") or ()) & set(cb.get("enemies") or ()))
            close = (ca.get("u") is not None and cb.get("u") is not None
                     and ((ca["u"] - cb["u"]) ** 2 + (ca["v"] - cb["v"]) ** 2) ** 0.5 <= COMBO_UV)
            if same_enemy or close:
                a["combo"].append(b["i"])


# --- the corpus


def corpus_key(db) -> str:
    from sqlalchemy import func

    from app.models.replay import ReplayRoundUtil, UtilModel

    n, latest = db.query(func.count(ReplayRoundUtil.replay_id), func.max(ReplayRoundUtil.computed_at)).filter(
        ReplayRoundUtil.status == "ok").one()
    win = db.get(UtilModel, "win")
    raw = f"{util_values.VERSION}:{PAGE_VERSION}:{n}:{latest}:{win.key if win else 'none'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_corpus(db, *, fit_missing: bool = False) -> dict:
    """Every friend's valued casts, the fitted weights and what the page needs to grade a lineup. With
    `fit_missing`, a site with no estimator stored fits one first (once: it is stored)."""
    model = util_model.load(db)
    if model is None and fit_missing:
        model = util_model.refresh(db)
    stored = _stored_rounds(db)
    ctx = _replay_context(db, set(stored))
    training = [{"match": ctx[rid]["match_id"], "data": {**data, "clock_offset": ctx[rid]["offset"]},
                 "winner": (ctx[rid]["rounds"].get(n) or {}).get("winner")}
                for rid, rounds in stored.items() if rid in ctx for n, data in rounds.items()]
    fit = util_values.fit_weights(util_values.team_rounds(training, model))
    rows = []
    for rid, rounds in stored.items():
        if rid in ctx:
            rows.extend(_cast_rows(rid, ctx[rid], rounds, model, fit["weights"]))
    prior: dict[str, list] = defaultdict(list)
    for r in rows:
        prior[r["ability"]].append(r["value"])
    prior_mean = {k: sum(v) / len(v) for k, v in prior.items()}
    lineup: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            lineup[(r["player_id"], r["ability"], r["map"], r["side"], r["area"])].append(r["value"])
    scores = [util_values.shrunk(v, prior_mean.get(k[1], 0.0), SHRINK_K) - prior_mean.get(k[1], 0.0)
              for k, v in lineup.items() if len(v) >= MIN_CASTS]
    spread = (sum(s * s for s in scores) / len(scores)) ** 0.5 if scores else 0.0
    return {"rows": rows, "fit": fit, "prior_mean": prior_mean, "spread": spread,
            "estimator": {k: (model or {}).get(k) for k in ("n", "matches", "check", "calibration")}}


def ensure_values(db, *, force: bool = False) -> dict:
    """The stored corpus build, rebuilt when its key changed (or with `force`)."""
    from datetime import datetime, timezone

    from app.models.replay import UtilModel

    key = corpus_key(db)
    row = db.get(UtilModel, "values")
    if not force and row is not None and row.version == util_values.VERSION and row.key == key:
        return row.data
    data = build_corpus(db, fit_missing=True)
    key = corpus_key(db)            # the estimator may have just been stored
    db.merge(UtilModel(name="values", version=util_values.VERSION, key=key, data=data,
                       computed_at=datetime.now(timezone.utc)))
    db.commit()
    return data


# --- the cross-match page


def _summ(values):
    n = len(values)
    mean = sum(values) / n if n else 0.0
    return n, mean


def page_for(corpus: dict, circle: set[int]) -> dict:
    """The page's tables for one viewer's circle: friends' casts only."""
    rows = [r for r in corpus.get("rows") or () if r.get("player_id") in circle]
    prior = corpus.get("prior_mean") or {}
    spread = corpus.get("spread") or 0.0
    out: dict = {"n": len(rows), "fit": corpus.get("fit"), "estimator": corpus.get("estimator")}

    wins: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["kind"] != "heal":
            wins[(r["ability"], r["agent"], r["map"], r["side"])].append(r)
    out["wins_rounds"] = sorted(
        ({"ability": k[0], "agent": k[1], "map": k[2], "side": k[3], "n": len(v),
          "matches": len({r["match"] for r in v}), "value": round(100 * sum(r["value"] for r in v) / len(v), 2),
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
        mean_prior = prior.get(k[2], 0.0)
        score = util_values.shrunk([r["value"] for r in v], mean_prior, SHRINK_K) - mean_prior
        calls.append({"player_id": k[0], "agent": k[1], "ability": k[2], "map": k[3], "side": k[4], "area": k[5],
                      "n": len(v), "matches": len({r["match"] for r in v}), "score": round(100 * score, 2),
                      "call": util_values.call(score, spread)})
    out["lineups"] = sorted(calls, key=lambda c: -c["score"])

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


def match_payload(db, replay) -> dict:
    """The tab's body for one linked replay: each player's casts with values, best and worst, unused util, harm.
    Opponents named by agent."""
    from sqlalchemy import or_

    from app.models.friendship import Friendship
    from app.models.replay import ReplayRoundUtil
    from app.services import replay_gaps
    from app.services import replays as replay_service
    from app.services.replay_moments import tab_players

    model = util_model.load(db)
    weights = _weights(db)
    rows = db.query(ReplayRoundUtil).filter(ReplayRoundUtil.replay_id == replay.id).all()
    stored = {r.round_number: r for r in rows}
    page = replay_service.page_context(db, replay)["players"]
    ids = [p["player_id"] for p in page.values() if p.get("player_id") is not None]
    friends = {pid for pair in db.query(Friendship.owner_player_id, Friendship.friend_player_id)
               .filter(or_(Friendship.owner_player_id.in_(ids), Friendship.friend_player_id.in_(ids)))
               for pid in pair} if ids else set()
    players = tab_players(page, friends)
    ctx = _replay_context(db, {replay.id}).get(replay.id)
    rounds_out, stale, failed = {}, 0, []
    current = {}
    for n in range(1, replay.round_count + 1):
        row = stored.get(n)
        if row is None or row.gaps_revision != replay_gaps.GAPS_REVISION:
            stale += 1
            continue
        if row.status != "ok" or not row.data:
            failed.append(n)
            continue
        current[n] = row.data
    flat = _cast_rows(replay.id, ctx, current, model, weights) if ctx else []
    by_round: dict[int, list] = defaultdict(list)
    for r in flat:
        by_round[r["round"]].append(r)
    for n, data in current.items():
        casts = {c["i"]: c for c in data.get("casts") or ()}
        out = []
        for r in by_round.get(n, []):
            c = casts.get(r["i"]) or {}
            out.append({**{k: r[k] for k in ("i", "slot", "agent", "ability", "kind", "kinds", "side", "area", "phase",
                                             "t", "wasted", "found_nobody", "followed", "enemies", "harm_died",
                                             "combo")
                           if k in r},
                        "value": round(100 * r["value"], 2), "ledger": round(100 * r["ledger"], 2),
                        "jobs": round(100 * r["jobs"], 2), "features": r["features"],
                        "kills": c.get("kills") or [], "harm": c.get("harm") or [], "effects": c.get("effects") or [],
                        "cover": c.get("cover"), "presence": c.get("presence"), "trigger": c.get("trigger"),
                        "heal": r.get("heal")})
        rounds_out[str(n)] = {"casts": out, "unused": data.get("unused") or [], "avail": data.get("avail") or {}}
    summary = {}
    for slot in players:
        mine = [r for r in flat if str(r["slot"]) == str(slot) and r["kind"] != "heal"]
        ranked = sorted(mine, key=lambda r: r["value"])
        pick = lambda r: {"round": r["round"], "t": r["t"], "i": r["i"], "ability": r["ability"],  # noqa: E731
                          "value": round(100 * r["value"], 2)}
        summary[str(slot)] = {"casts": len(mine), "best": [pick(r) for r in ranked[::-1][:3] if r["value"] > 0],
                              "worst": [pick(r) for r in ranked[:3] if r["value"] < 0],
                              "value": round(100 * sum(r["value"] for r in mine), 2)}
    return {"players": players, "rounds": rounds_out, "summary": summary, "stale": stale,
            "failed": sorted(failed), "estimator": bool(model), "weights_fitted": weights != util_values.DEFAULT_WEIGHTS}
